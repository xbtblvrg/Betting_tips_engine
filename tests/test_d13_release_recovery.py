"""Process interruption drills against utility-owned temporary stores only."""
from contextlib import closing
import json
import os
import signal
import sqlite3
import time

import pytest

import betting_engine.release_rehearsal as module
from test_d13_release_rehearsal import BASE, NEW, evidence, seed


def wait_for_child(pid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            assert os.waitstatus_to_exitcode(status) == 73
            return
        time.sleep(.01)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    pytest.fail('interrupted rehearsal child did not exit')


@pytest.mark.parametrize('action', ['promote', 'rollback'])
@pytest.mark.parametrize('phase', ['staged', 'before_replace', 'after_replace', 'after_sync'])
def test_process_exit_at_commit_boundaries_recovers_without_losing_later_bets(action, phase):
    with module.ReleaseRehearsal(BASE) as run:
        seed(run)
        manifest = run.manifest(NEW, evidence())
        if action == 'rollback':
            assert run.promote(manifest, NEW, evidence())['state'] == 'rehearsal_promoted'
            seed(run, 'newer-than-release', stake=5, odds=1.8)
        before, ledger = run.state(), run.ledger_snapshot()
        database_bytes = run._database.read_bytes()
        pid = os.fork()
        if pid == 0:
            # Child changes only this owned tree; abrupt exit bypasses cleanup.
            original_replace, original_sync = module.os.replace, module._sync_directory
            def replace(*args):
                if phase == 'before_replace':
                    os._exit(73)
                original_replace(*args)
                if phase == 'after_replace':
                    os._exit(73)
            def sync(path):
                original_sync(path)
                if phase == 'after_sync' and path == run.root:
                    os._exit(73)
            def staged():
                if phase == 'staged':
                    os._exit(73)
            module.os.replace, module._sync_directory = replace, sync
            if action == 'promote':
                run.promote(manifest, NEW, evidence(), before_commit=staged)
            else:
                run.rollback(before_commit=staged)
            os._exit(99)
        wait_for_child(pid)
        # Parent re-reads child-committed state; there is no cached release state.
        result = run.recover()
        assert result['state'] == 'rehearsal_recovered'
        committed = phase in ('after_replace', 'after_sync')
        expected = NEW if (action == 'promote') == committed else BASE
        assert run.active_files() == expected
        assert run.state()['generation'] == before['generation'] + int(committed)
        assert run.ledger_snapshot() == ledger
        assert run._database.read_bytes() == database_bytes
        assert run.recover() == result  # recovery is read-only and repeatable
        if action == 'promote' and committed:
            seed(run, 'after-restart', stake=3, odds=2.5)
            assert run.rollback()['state'] == 'rehearsal_rolled_back'
            assert len(run.ledger_snapshot()['bets']) == 2
        elif not committed:
            # Inactive staging is retained, never adopted; a fresh retry works.
            result = (run.promote(manifest, NEW, evidence()) if action == 'promote'
                      else run.rollback())
            assert result['state'] == ('rehearsal_promoted' if action == 'promote'
                                       else 'rehearsal_rolled_back')


@pytest.mark.parametrize('action', ['promote', 'rollback'])
def test_post_replace_directory_sync_failure_is_not_reported_as_unchanged(monkeypatch, action):
    with module.ReleaseRehearsal(BASE) as run:
        seed(run)
        manifest = run.manifest(NEW, evidence())
        if action == 'rollback':
            run.promote(manifest, NEW, evidence())
            seed(run, 'newer', stake=5)
        ledger = run.ledger_snapshot()
        original_sync = module._sync_directory
        def fail_root(path):
            if path == run.root:
                raise OSError('injected directory sync failure')
            original_sync(path)
        monkeypatch.setattr(module, '_sync_directory', fail_root)
        result = (run.promote(manifest, NEW, evidence()) if action == 'promote'
                  else run.rollback())
        assert result['state'] == 'recovery_required'
        assert 'pointer replaced' in result['reason']
        assert run.active_files() == (NEW if action == 'promote' else BASE)
        assert run.recover()['state'] == 'rehearsal_recovered'
        assert run.ledger_snapshot() == ledger


@pytest.mark.parametrize('target', ['active', 'rollback', 'model_report', 'manifest',
                                   'extra_evidence', 'ledger', 'pointer'])
def test_recovery_rejects_committed_artifact_corruption_without_repair(target):
    with module.ReleaseRehearsal(BASE) as run:
        seed(run)
        before = run.state()
        result = run.promote(run.manifest(NEW, evidence()), NEW, evidence())
        archive = run.root / result['evidence_directory']
        if target == 'active':
            (run._tree(run.state()) / 'src/engine.py').write_bytes(b'drift')
        elif target == 'rollback':
            (run._tree(before) / 'src/engine.py').write_bytes(b'drift')
        elif target == 'model_report':
            (archive / 'model.json').write_bytes(b'{}')
        elif target == 'manifest':
            (archive / 'manifest.json').write_bytes(b'{}')
        elif target == 'extra_evidence':
            (archive / 'unreviewed.json').write_bytes(b'{}')
        elif target == 'ledger':
            with closing(sqlite3.connect(run._database)) as db, db:
                db.execute('UPDATE bets SET odds=1.1')
        else:
            (run.root / 'active.json').write_bytes(b'{incomplete')
        pointer, database = (run.root / 'active.json').read_bytes(), run._database.read_bytes()
        assert run.recover()['state'] == 'blocked'
        assert (run.root / 'active.json').read_bytes() == pointer
        assert run._database.read_bytes() == database


def test_recovery_preserves_newer_tickets_and_settlement_after_rollback():
    with module.ReleaseRehearsal(BASE) as run:
        seed(run)
        run.promote(run.manifest(NEW, evidence()), NEW, evidence())
        seed(run, 'newer', stake=5)
        run.rollback()
        with closing(sqlite3.connect(run._database)) as db, db:
            db.execute("UPDATE bets SET status='lost',pnl=-stake")
        ledger = run.ledger_snapshot()
        assert run.recover()['state'] == 'rehearsal_recovered'
        assert run.ledger_snapshot() == ledger
        with closing(sqlite3.connect(run._database)) as db, db:
            db.execute("DELETE FROM bets WHERE tip_id='newer'")
        assert run.recover()['state'] == 'blocked'


def test_recovery_after_multiple_promotions_and_a_source_rebase():
    with module.ReleaseRehearsal(BASE) as run:
        run.promote(run.manifest(NEW, evidence()), NEW, evidence())
        rebased = {**NEW, 'src/other.py': b'independently accepted work'}
        run.change_source(rebased)
        candidate = {**rebased, 'src/engine.py': b'next candidate'}
        reports = evidence(candidate, rebased)
        assert run.promote(run.manifest(candidate, reports), candidate, reports)['state'] == 'rehearsal_promoted'
        assert run.recover()['state'] == 'rehearsal_recovered'
        assert run.rollback()['state'] == 'rehearsal_rolled_back'
        assert run.recover()['state'] == 'rehearsal_recovered'
        assert run.active_files() == rebased


def test_source_drift_after_restart_still_requires_new_baseline_evidence():
    with module.ReleaseRehearsal(BASE) as run:
        manifest = run.manifest(NEW, evidence())
        changed = {**BASE, 'src/other.py': b'accepted upstream'}
        run.change_source(changed)
        assert run.recover()['state'] == 'rehearsal_recovered'
        assert run.promote(manifest, NEW, evidence())['state'] == 'blocked'
        assert run.active_files() == changed
