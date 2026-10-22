"""Disposable synthetic release protocol; no production measurements or issuance."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from threading import Event, Thread

import pytest

from betting_engine.release_rehearsal import (
    ReleaseRehearsal, RehearsalBlocked, sha256, tree_hash,
)


BASE = {'src/engine.py': b'old code', 'src/deleted.py': b'remove in v2',
        'models/model.json': b'old weights', 'config/model.json': b'old recipe'}
NEW = {'src/engine.py': b'new code', 'src/new.py': b'new module',
       'models/model.json': b'new weights', 'config/model.json': b'new recipe',
       'tests/test_engine.py': b'candidate test bytes'}


def encoded(obj):
    return json.dumps(obj, sort_keys=True).encode()


def evidence(candidate=NEW, baseline=BASE):
    """Fabricated TEST reports: never model, source-availability or profit claims."""
    binding = {'synthetic_only': True, 'candidate_sha256': tree_hash(candidate),
               'baseline_sha256': tree_hash(baseline)}
    source = b'{"synthetic_only":true,"targets":[1,0]}'
    reports = {
        'tests': {'cases': [{'name': 'synthetic unit/integration case', 'status': 'passed'}]},
        'model': {'gates': {k: True for k in ('independent_sports_data', 'chronological_validation',
                                           'score_regression', 'approved_model')},
                  'train_end': '2025-01-01T00:00:00Z',
                  'selection_start': '2025-02-01T00:00:00Z',
                  'selection_end': '2025-03-01T00:00:00Z',
                  'test_start': '2025-04-01T00:00:00Z',
                  'test_end': '2025-05-01T00:00:00Z',
                  'artifacts': {k: sha256(v) for k, v in candidate.items() if k.startswith('models/')},
                  'sources': {'sports.json': sha256(source)}},
        'settlement': {'cases': [dict(id=str(i), result=result, stake=2, odds=2.1, pnl=pnl)
                                for i, (result, pnl) in enumerate(
                                    [('won', '2.2'), ('lost', '-2'), ('push', '0'), ('void', '0')])]}}
    return {**{kind + '.json': encoded({**binding, 'kind': kind, **report})
               for kind, report in reports.items()}, 'sports.json': source}


def change_report(bundle, kind, change):
    result = deepcopy(bundle)
    report = json.loads(result[kind + '.json'])
    change(report)
    result[kind + '.json'] = encoded(report)
    return result


def seed(run, identity='old', stake=2, odds=2.1):
    run.record_synthetic_bet(identity, stake=stake, odds=odds,
                            issued_at='2026-09-29T12:00:00+00:00',
                            payload={'model_version': 'frozen', 'probability': .55,
                                     'line': -1.5, 'game_id': 'synthetic-game'})


def test_promotion_and_rollback_restore_all_code_and_models_without_replacing_newer_bets():
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        # Preserve an existing loss and two individual tickets for one decision.
        with closing(sqlite3.connect(run._database)) as db, db:
            db.execute("UPDATE system_decisions SET status='lost',pnl=-2,outcome_evidence='synthetic loss'")
            db.execute("INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload) "
                       "SELECT tip_id,created_at,stake,odds,platform,payload FROM bets")
        original = run.ledger_snapshot()
        original_database = run._database.read_bytes()
        bundle = evidence()
        manifest = run.manifest(NEW, bundle)
        result = run.promote(manifest, NEW, bundle)
        assert result['state'] == 'rehearsal_promoted'
        assert run.active_files() == NEW
        assert run.ledger_snapshot() == original
        assert run._database.read_bytes() == original_database
        seed(run, 'new', stake=5, odds=1.8)
        newer = run.ledger_snapshot()
        newer_database = run._database.read_bytes()
        assert json.loads(newer['system_decisions'][0][3])['release_tree_sha256'] == tree_hash(NEW)
        assert run.rollback()['state'] == 'rehearsal_rolled_back'
        assert run.active_files() == BASE
        assert run.ledger_snapshot() == newer
        assert run._database.read_bytes() == newer_database
        assert len(newer['bets']) == 3 and len(newer['system_decisions']) == 2
        # Old and new synthetic decisions remain readable after rollback.
        assert {json.loads(row[3])['stake'] for row in newer['system_decisions']} == {2, 5}
        assert run.state()['history'][0]['manifest_sha256'] == manifest['sha256']
        archived = run.root / result['evidence_directory']
        assert json.loads((archived / 'manifest.json').read_bytes()) == manifest
        assert (archived / 'model.json').read_bytes() == bundle['model.json']
        root = run.root
    assert not root.exists()


@pytest.mark.parametrize('missing', ['tests.json', 'model.json', 'settlement.json'])
def test_each_missing_evidence_gate_blocks_without_any_changes(missing):
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        before, ledger = run.state(), run.ledger_snapshot()
        bundle = evidence()
        del bundle[missing]
        result = run.promote(run.manifest(NEW, bundle), NEW, bundle)
        assert result['state'] == 'blocked'
        assert 'missing ' + missing.split('.')[0] in result['reason']
        assert run.state() == before and run.ledger_snapshot() == ledger


@pytest.mark.parametrize('kind,mutate,reason', [
    ('tests', lambda r: r.update(candidate_sha256='0' * 64), 'stale tests'),
    ('tests', lambda r: r.update(baseline_sha256='0' * 64), 'stale tests'),
    ('tests', lambda r: r.update(cases=[], passed=True), 'missing individual'),
    ('tests', lambda r: r.update(cases=[{'name': 'raw failure', 'status': 'failed'}], passed=True), 'individual'),
    ('tests', lambda r: r.update(synthetic_only=False), 'only synthetic'),
    ('model', lambda r: r['gates'].update(score_regression=False), 'model gate'),
    ('model', lambda r: r['gates'].pop('approved_model'), 'model gate'),
    ('model', lambda r: r.update(train_end=r['test_end']), 'chronological'),
    ('model', lambda r: r.update(test_start=r['selection_end']), 'chronological'),
    ('model', lambda r: r.update(test_start='2025-04-01'), 'timezone'),
    ('model', lambda r: r.update(artifacts={}), 'artifact coverage'),
    ('model', lambda r: r.update(sources={'sports.json': '0' * 64}), 'source hash'),
    ('settlement', lambda r: r['cases'][1].update(pnl=0), 'reconciliation'),
    ('settlement', lambda r: r['cases'].pop(1), 'coverage'),
    ('settlement', lambda r: r['cases'][0].update(stake='NaN'), 'amounts'),
    ('settlement', lambda r: r['cases'][0].update(stake=9), 'amounts'),
])
def test_invalid_evidence_blocks_even_with_a_matching_manifest(kind, mutate, reason):
    with ReleaseRehearsal(BASE) as run:
        bundle = change_report(evidence(), kind, mutate)
        before = run.state()
        result = run.promote(run.manifest(NEW, bundle), NEW, bundle)
        assert result['state'] == 'blocked' and reason in result['reason']
        assert run.state() == before


@pytest.mark.parametrize('mutation', ['changed', 'added', 'deleted', 'model', 'config', 'tests', 'evidence', 'seal'])
def test_all_candidate_bytes_and_evidence_are_bound(mutation):
    with ReleaseRehearsal(BASE) as run:
        candidate, bundle = deepcopy(NEW), evidence()
        manifest = run.manifest(candidate, bundle)
        if mutation == 'added':
            candidate['src/unreviewed.py'] = b'unreviewed'
        elif mutation == 'deleted':
            del candidate['src/engine.py']
        elif mutation in ('changed', 'model', 'config', 'tests'):
            name = {'changed': 'src/engine.py', 'model': 'models/model.json',
                    'config': 'config/model.json', 'tests': 'tests/test_engine.py'}[mutation]
            candidate[name] += b'changed'
        elif mutation == 'evidence':
            bundle['sports.json'] += b' '
        else:
            manifest['body']['candidate_files']['src/engine.py'] = '0' * 64
        before = run.state()
        assert run.promote(manifest, candidate, bundle)['state'] == 'blocked'
        assert run.state() == before and run.active_files() == BASE


def test_hashes_ignore_mapping_order_and_filesystem_mtimes():
    with ReleaseRehearsal(BASE) as run:
        first = run.manifest(NEW, evidence())
        second = run.manifest(dict(reversed(list(NEW.items()))), dict(reversed(list(evidence().items()))))
        assert first == second
        import os
        os.utime(run._tree(run.state()) / 'src/engine.py', (1, 1))
        assert run.manifest(NEW, evidence()) == first


def test_concurrent_source_writer_during_staging_aborts_promotion_without_erasing_writer():
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        before = run.ledger_snapshot()
        manifest = run.manifest(NEW, evidence())
        staged, changed = Event(), Event()
        updated = {**BASE, 'src/engine.py': b'concurrent accepted work'}
        def writer():
            assert staged.wait(5)
            run.change_source(updated)
            changed.set()
        thread = Thread(target=writer)
        thread.start()
        def pause():
            staged.set()
            assert changed.wait(5)
        try:
            result = run.promote(manifest, NEW, evidence(), before_commit=pause)
        finally:
            thread.join(5)
        assert not thread.is_alive()
        assert result['state'] == 'blocked' and 'concurrent' in result['reason']
        assert run.active_files() == updated and run.ledger_snapshot() == before
        assert run.state()['history'] == []


@pytest.mark.parametrize('mutation', ['edit', 'add', 'delete', 'symlink', 'hardlink'])
def test_direct_active_tree_drift_is_rejected_at_final_check(mutation):
    with ReleaseRehearsal(BASE) as run:
        manifest = run.manifest(NEW, evidence())
        root = run._tree(run.state())
        def drift():
            if mutation == 'edit':
                (root / 'src/engine.py').write_bytes(b'concurrent source edit')
            elif mutation == 'add':
                (root / 'src/extra.py').write_bytes(b'extra')
            elif mutation == 'delete':
                (root / 'src/engine.py').unlink()
            elif mutation == 'symlink':
                (root / 'src/link').symlink_to(root / 'src/engine.py')
            else:
                import os
                os.link(root / 'src/engine.py', root / 'src/link')
        before = run.state()
        assert run.promote(manifest, NEW, evidence(), before_commit=drift)['state'] == 'blocked'
        assert run.state() == before


def test_new_bets_during_staging_and_rollback_do_not_block_or_get_replaced():
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        assert run.promote(run.manifest(NEW, evidence()), NEW, evidence(),
                           before_commit=lambda: seed(run, 'during-promotion'))['state'] == 'rehearsal_promoted'
        assert run.rollback(before_commit=lambda: seed(run, 'during-rollback'))['state'] == 'rehearsal_rolled_back'
        assert {row[0] for row in run.ledger_snapshot()['system_decisions']} == {
            'old', 'during-promotion', 'during-rollback'}


def test_corrupt_rollback_artifact_refuses_and_retains_new_code_and_ledger():
    with ReleaseRehearsal(BASE) as run:
        old_tree = run._tree(run.state())
        assert run.promote(run.manifest(NEW, evidence()), NEW, evidence())['state'] == 'rehearsal_promoted'
        seed(run, 'new')
        before, ledger = run.state(), run.ledger_snapshot()
        (old_tree / 'models/model.json').write_bytes(b'corrupt')
        result = run.rollback()
        assert result['state'] == 'blocked' and 'rollback artifact' in result['reason']
        assert run.state() == before and run.ledger_snapshot() == ledger
        assert run.active_files() == NEW


def test_concurrent_change_blocks_rollback_and_double_rollback_is_rejected():
    with ReleaseRehearsal(BASE) as run:
        assert run.rollback()['state'] == 'blocked'
        assert run.promote(run.manifest(NEW, evidence()), NEW, evidence())['state'] == 'rehearsal_promoted'
        result = run.rollback(before_commit=lambda: run.change_source(NEW))
        assert result['state'] == 'blocked' and 'concurrent' in result['reason']
    with ReleaseRehearsal(BASE) as run:
        run.promote(run.manifest(NEW, evidence()), NEW, evidence())
        assert run.rollback()['state'] == 'rehearsal_rolled_back'
        assert run.rollback()['state'] == 'blocked'


@pytest.mark.parametrize('path', ['/Library/LaunchDaemons/service.plist', '../escape.py',
    'src/../../escape.py', 'src//file.py', 'src/.hidden', 'data/bets.db', 'src/bets.sqlite',
    'config/launchd.plist', 'src/back\\slash'])
def test_only_safe_deployable_bytes_can_enter_the_disposable_tree(path):
    with pytest.raises(RehearsalBlocked):
        ReleaseRehearsal({path: b'do not write'})


def test_external_paths_and_database_objects_are_not_accepted(tmp_path):
    sentinel = tmp_path / 'untouched.db'
    sentinel.write_bytes(b'untouched')
    with pytest.raises(RehearsalBlocked):
        ReleaseRehearsal({'src/app.py': sentinel})
    with pytest.raises(TypeError):
        ReleaseRehearsal(BASE, root=tmp_path)
    assert sentinel.read_bytes() == b'untouched'


def test_precommit_pointer_failure_leaves_previous_code_and_store_intact(monkeypatch):
    import betting_engine.release_rehearsal as module
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        before, ledger = run.state(), run.ledger_snapshot()
        def fail(*args):
            raise OSError('injected atomic replace failure')
        monkeypatch.setattr(module.os, 'replace', fail)
        result = run.promote(run.manifest(NEW, evidence()), NEW, evidence())
        assert result['state'] == 'blocked' and 'replace failure' in result['reason']
        assert run.state() == before and run.active_files() == BASE
        assert run.ledger_snapshot() == ledger


def test_supplied_missing_component_evidence_is_not_a_release_attestation():
    path = Path(__file__).parent / 'fixtures/d13_component_gap.json'
    observation = json.loads(path.read_bytes())
    assert observation['gap']['evidence']['checked_at'] is None
    with ReleaseRehearsal(BASE) as run:
        bundle = {'component.json': path.read_bytes()}
        result = run.promote(run.manifest(NEW, bundle), NEW, bundle)
        assert result['state'] == 'blocked'
        assert 'missing tests evidence' in result['reason']


@pytest.mark.parametrize('mutation', ['staged-code', 'evidence', 'extra-evidence'])
def test_staged_artifact_corruption_before_commit_is_rejected(mutation):
    with ReleaseRehearsal(BASE) as run:
        baseline = run.state()
        def corrupt():
            staged = next(p for p in (run.root / 'revisions').iterdir() if p.name != baseline['revision'])
            if mutation == 'staged-code':
                (staged / 'src/engine.py').write_bytes(b'changed after staging')
            else:
                archive = run.root / 'evidence' / staged.name
                (archive / ('model.json' if mutation == 'evidence' else 'extra.json')).write_bytes(b'{}')
        result = run.promote(run.manifest(NEW, evidence()), NEW, evidence(), before_commit=corrupt)
        assert result['state'] == 'blocked'
        assert run.state() == baseline and run.active_files() == BASE


@pytest.mark.parametrize('bad', [None, [], 'passed', True])
def test_malformed_model_gates_fail_closed(bad):
    with ReleaseRehearsal(BASE) as run:
        bundle = change_report(evidence(), 'model', lambda r: r.update(gates=bad))
        assert run.promote(run.manifest(NEW, bundle), NEW, bundle)['state'] == 'blocked'


def test_duplicate_report_keys_and_reserved_manifest_attachment_rejected():
    with ReleaseRehearsal(BASE) as run:
        bundle = evidence()
        bundle['tests.json'] = bundle['tests.json'][:-1] + b', "kind":"tests"}'
        assert 'duplicate evidence JSON key' in run.promote(run.manifest(NEW, bundle), NEW, bundle)['reason']
        bundle = {**evidence(), 'manifest.json': b'fake manifest'}
        assert 'reserved evidence name' in run.promote(run.manifest(NEW, bundle), NEW, bundle)['reason']


def test_a_second_promotion_cannot_reuse_the_previous_source_binding():
    with ReleaseRehearsal(BASE) as run:
        manifest = run.manifest(NEW, evidence())
        assert run.promote(manifest, NEW, evidence())['state'] == 'rehearsal_promoted'
        before = run.state()
        assert run.promote(manifest, NEW, evidence())['state'] == 'blocked'
        assert run.state() == before
        next_files = {**NEW, 'src/engine.py': b'v3'}
        bundle = evidence(next_files, NEW)
        assert run.promote(run.manifest(next_files, bundle), next_files, bundle)['state'] == 'rehearsal_promoted'
        assert run.rollback()['state'] == 'rehearsal_rolled_back'
        assert run.active_files() == NEW


@pytest.mark.parametrize('action', ['promote', 'rollback'])
def test_unreadable_synthetic_store_blocks_before_pointer_change(action):
    with ReleaseRehearsal(BASE) as run:
        manifest = run.manifest(NEW, evidence())
        if action == 'rollback':
            assert run.promote(manifest, NEW, evidence())['state'] == 'rehearsal_promoted'
        before = run.state()
        run._database.write_bytes(b'corrupt synthetic SQLite store')
        result = run.rollback() if action == 'rollback' else run.promote(manifest, NEW, evidence())
        assert result['state'] == 'blocked'
        assert run.state() == before
        assert run._database.read_bytes() == b'corrupt synthetic SQLite store'


@pytest.mark.parametrize('table,change', [
    ('bets', "UPDATE bets SET odds=1.2"),
    ('bets', "UPDATE bets SET stake=1"),
    ('bets', "DELETE FROM bets"),
    ('system_decisions', "UPDATE system_decisions SET payload='{}'"),
    ('system_decisions', "DELETE FROM system_decisions"),
    ('recommendation_issuance', "UPDATE recommendation_issuance SET odds=1.2"),
    ('recommendation_issuance', "UPDATE recommendation_issuance SET stake=1"),
    ('recommendation_issuance', "DELETE FROM recommendation_issuance"),
])
@pytest.mark.parametrize('action', ['promote', 'rollback'])
def test_immutable_record_mutation_blocks_transition_without_repairing_the_store(table, change, action):
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        manifest = run.manifest(NEW, evidence())
        if action == 'rollback':
            assert run.promote(manifest, NEW, evidence())['state'] == 'rehearsal_promoted'
        before = run.state()
        altered_bytes = []
        def corrupt():
            with closing(sqlite3.connect(run._database)) as db, db:
                db.execute(change)
            altered_bytes.append(run._database.read_bytes())
        result = (run.promote(manifest, NEW, evidence(), before_commit=corrupt)
                  if action == 'promote' else run.rollback(before_commit=corrupt))
        assert result['state'] == 'blocked'
        assert 'immutable ' + table in result['reason']
        assert run.state() == before
        # Never restore a database to hide corruption, even on a blocked release.
        assert run._database.read_bytes() == altered_bytes[0]


def test_settlement_can_advance_without_rewriting_original_decision_or_price():
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        manifest = run.manifest(NEW, evidence())
        def settle():
            with closing(sqlite3.connect(run._database)) as db, db:
                db.execute("UPDATE bets SET status='lost',pnl=-2")
                db.execute("UPDATE system_decisions SET status='lost',pnl=-2,outcome_evidence='scope fixture'")
        assert run.promote(manifest, NEW, evidence(), before_commit=settle)['state'] == 'rehearsal_promoted'
        rows = run.ledger_snapshot()
        assert rows['bets'][0][3:5] == (2, 2.1)
        assert rows['bets'][0][6:8] == ('lost', -2)
        assert run.rollback()['state'] == 'rehearsal_rolled_back'
        assert run.ledger_snapshot() == rows


def test_newer_ticket_mutation_during_rollback_is_not_silently_accepted():
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        assert run.promote(run.manifest(NEW, evidence()), NEW, evidence())['state'] == 'rehearsal_promoted'
        seed(run, 'new', stake=5, odds=1.8)
        before = run.state()
        def corrupt():
            with closing(sqlite3.connect(run._database)) as db, db:
                db.execute("UPDATE bets SET odds=1.1 WHERE tip_id='new'")
        assert run.rollback(before_commit=corrupt)['state'] == 'blocked'
        assert run.state() == before


def test_source_change_and_change_back_still_invalidates_generation():
    with ReleaseRehearsal(BASE) as run:
        manifest = run.manifest(NEW, evidence())
        run.change_source(NEW)
        run.change_source(BASE)
        assert run.promote(manifest, NEW, evidence())['state'] == 'blocked'
        assert run.active_files() == BASE


def test_two_simultaneous_promotions_have_exactly_one_commit():
    from threading import Barrier
    with ReleaseRehearsal(BASE) as run:
        manifest = run.manifest(NEW, evidence())
        barrier = Barrier(2)
        results = []
        def promote():
            results.append(run.promote(manifest, NEW, evidence(), before_commit=lambda: barrier.wait(5)))
        threads = [Thread(target=promote) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert not any(thread.is_alive() for thread in threads)
        assert sorted(r['state'] for r in results) == ['blocked', 'rehearsal_promoted']
        assert len(run.state()['history']) == 1
        assert run.active_files() == NEW


def test_historical_sports_manifest_cannot_substitute_for_release_evidence():
    path = (Path(__file__).parent / 'fixtures/d13_history_manifest.json')
    observation = json.loads(path.read_bytes())
    assert observation['current_participation'] == 'unknown'
    assert observation['historical_point_in_time_backtest_ready'] is False
    with ReleaseRehearsal(BASE) as run:
        bundle = {'history.json': path.read_bytes()}
        result = run.promote(run.manifest(NEW, bundle), NEW, bundle)
        assert result['state'] == 'blocked'
        assert 'missing tests evidence' in result['reason']


def test_every_synthetic_database_connection_is_explicitly_closed(monkeypatch):
    import betting_engine.release_rehearsal as module
    connect = sqlite3.connect
    opened = []
    def tracked(*args, **kwargs):
        db = connect(*args, **kwargs)
        opened.append(db)
        return db
    monkeypatch.setattr(module.sqlite3, 'connect', tracked)
    with ReleaseRehearsal(BASE) as run:
        seed(run)
        manifest = run.manifest(NEW, evidence())
        for _ in range(10):
            run.ledger_snapshot()
        assert run.promote(manifest, NEW, evidence())['state'] == 'rehearsal_promoted'
        assert run.rollback()['state'] == 'rehearsal_rolled_back'
        for db in opened:
            with pytest.raises(sqlite3.ProgrammingError, match='closed database'):
                db.execute('SELECT 1')
