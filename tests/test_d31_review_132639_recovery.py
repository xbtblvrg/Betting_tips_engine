"""D31 review regressions: actual Store/Engine integration, isolated SQLite only."""
import gzip
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from betting_engine.domain import Game
from betting_engine.engine import Engine
from betting_engine.experimental_lifecycle import (
    SportsOutcomeStore, import_receipts, manual_settlement, manual_snapshot,
)
from betting_engine.experimental_publication_retention import (
    ARCHIVES, EVENTS, archive_before_prune, restore_publication, retention_plan, _seal,
)
from betting_engine.snapshots import PREFIX
from betting_engine.storage import Store
from test_d31_baseline_defects import recorded_tip

NOW = '2026-10-01T12:00:00Z'
OLD = '2026-09-01T00:00:00Z'
EVIDENCE = {'kind': 'synthetic-user-report', 'account_verified': False}


@pytest.fixture
def manual_entry(tmp_path):
    store = Store(tmp_path / 'manual.sqlite')
    tip = recorded_tip()
    with store.db() as c:
        c.execute('INSERT INTO tips VALUES (?,?,?,?,?)', (tip['id'], OLD, OLD, 'fixture', json.dumps(tip)))
        c.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload) VALUES (?,?,?,?)',
                  ('contract:' + tip['id'], tip['id'], OLD, json.dumps(tip)))
    bid = store.add_bet(tip['id'], 1, 1.9, 'Stake.com', settlement_mode='sportsbook')
    return store, bid


def receipt(bid, rid='ticket'):
    return {'receipt_id': rid, 'tip_id': 'd31-fixture', 'stake': '2.25',
            'odds': '1.9876543210987654321', 'currency': 'USD', 'reconcile_bet_id': bid}


def ingest(c, source, values):
    return import_receipts(c, source, values, evidence=EVIDENCE, recorded_at=NOW)


def override(c, bid, request='override', total='0.75'):
    return manual_settlement(c, bid, total, request_id=request, actor='fixture-user',
                             reason='Reported cash correction', evidence=EVIDENCE,
                             expected_sha256=manual_snapshot(c, bid)['sha256'], recorded_at=NOW)


def snapshot(c, table):
    return [tuple(r) for r in c.execute('SELECT * FROM ' + table)]


def game(state='final', **changes):
    return Game(id='NBA:fixture', league='NBA', start=OLD, home='A', away='B',
                home_id='a', away_id='b', home_abbr='A', away_abbr='B', state=state,
                season=2026, home_score=100, away_score=90, source='synthetic:score',
                fetched_at=NOW, **changes)


def run_engine(store, games):
    # Only the settlement method runs: no Engine loop, sources or services.
    engine = Engine(store=SportsOutcomeStore(store))
    engine.games = games
    engine.settle_bets()


def test_manual_entry_receipt_audits_original_and_preserves_separate_ticket(manual_entry):
    store, bid = manual_entry
    with store.db() as c:
        original = manual_snapshot(c, bid)['bet']
        decisions = snapshot(c, 'system_decisions')
        assert 'tracking_source' not in json.loads(original['payload'])
        assert ingest(c, 'upload', [receipt(bid)]) == [bid]
        assert ingest(c, 'renamed-upload', [receipt(None)]) == [bid]
        duplicate = ingest(c, 'separate-upload', [receipt(None, 'separate-ticket')])[0]
        assert duplicate != bid
        assert len(snapshot(c, 'bets')) == 2
        assert snapshot(c, 'system_decisions') == decisions
        audit = c.execute('SELECT before_record FROM bet_reconciliation WHERE bet_id=?', (bid,)).fetchone()
        assert json.loads(audit[0]) == original
        accepted = json.loads(c.execute('SELECT accepted FROM d31_receipts WHERE bet_id=?', (bid,)).fetchone()[0])
        assert (accepted['stake'], accepted['odds']) == ('2.25', '1.9876543210987654321')
        current = manual_snapshot(c, bid)['bet']
        for key in ('stake', 'reference_odds', 'probability', 'model_version'):
            assert json.loads(current['payload'])[key] == json.loads(original['payload'])[key]


@pytest.mark.parametrize('patch', [
    {'ledger_contract': 'manual'}, {'tracking_source': 'unknown'},
    {'tracking_source': None}, {'id': 'different'}, {'reference_platform': 'elsewhere'},
])
def test_manual_reconciliation_does_not_accept_unrelated_or_malformed_ticket(manual_entry, patch):
    store, bid = manual_entry
    with store.db() as c:
        tip = json.loads(manual_snapshot(c, bid)['bet']['payload'])
        c.execute('UPDATE bets SET payload=? WHERE id=?', (json.dumps({**tip, **patch}), bid))
        c.commit()
        before = snapshot(c, 'bets')
        with pytest.raises(ValueError):
            ingest(c, 'bad', [receipt(bid)])
        assert snapshot(c, 'bets') == before
        assert snapshot(c, 'bet_reconciliation') == []


def test_manual_cash_before_official_result_preserves_both_lifecycles_after_restart(manual_entry):
    store, bid = manual_entry
    with store.db() as c:
        ingest(c, 'receipt', [receipt(bid)])
        override(c, bid)
        cash = snapshot(c, 'bets')
        revisions = snapshot(c, 'd31_manual_revisions')
        decisions = snapshot(c, 'system_decisions')
    # No outcome is manufactured when final exact-scope evidence is unavailable.
    run_engine(store, [game('scheduled')])
    reopened = Store(store.path)
    assert SportsOutcomeStore(reopened).open_bets()[0]['cash_status'] == 'manual'
    run_engine(reopened, [game()])
    with reopened.db() as c:
        outcome = dict(c.execute('SELECT * FROM bet_outcomes WHERE bet_id=?', (bid,)).fetchone())
        assert outcome['result'] == 'won'
        assert outcome['brier'] == pytest.approx((.65 - 1) ** 2)
        assert outcome['log_loss'] > 0
        assert json.loads(outcome['evidence'])['source_fetched_at'] == NOW
        assert snapshot(c, 'bets') == cash
        assert snapshot(c, 'd31_manual_revisions') == revisions
        assert snapshot(c, 'system_decisions') == decisions
        before = snapshot(c, 'bet_outcomes')
        override(c, bid, 'second', '0')
        assert snapshot(c, 'bet_outcomes') == before
    assert SportsOutcomeStore(reopened).open_bets() == []
    run_engine(reopened, [game()])
    with reopened.db() as c:
        assert snapshot(c, 'bet_outcomes') == before


def test_manual_segment_waits_for_exact_scope_scores(manual_entry):
    store, bid = manual_entry
    with store.db() as c:
        tip = json.loads(manual_snapshot(c, bid)['bet']['payload'])
        tip['market'] = 'half_1:moneyline'
        c.execute('UPDATE bets SET payload=? WHERE id=?', (json.dumps(tip), bid)); c.commit()
        override(c, bid)
    run_engine(store, [game()])
    with store.db() as c:
        assert snapshot(c, 'bet_outcomes') == []
    assert len(SportsOutcomeStore(store).open_bets()) == 1
    run_engine(store, [game(context={'segment_scores': {'half_1': {
        'home': 40, 'away': 50, 'complete': True, 'source': 'synthetic:half',
        'fetched_at': NOW, 'source_sha256': 'ab' * 32}}})])
    with store.db() as c:
        assert manual_snapshot(c, bid)['outcome']['result'] == 'lost'
        assert manual_snapshot(c, bid)['bet']['pnl'] == -.25


def test_sports_outcome_rollback_and_concurrent_retries(manual_entry):
    store, bid = manual_entry
    with store.db() as c:
        override(c, bid)
        cash = snapshot(c, 'bets')
        c.execute('''CREATE TRIGGER reject_outcome BEFORE INSERT ON bet_outcomes
            BEGIN SELECT RAISE(ABORT,'fixture outcome failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='outcome failure'):
        run_engine(store, [game()])
    with store.db() as c:
        assert snapshot(c, 'bets') == cash
        assert snapshot(c, 'bet_outcomes') == []
        c.execute('DROP TRIGGER reject_outcome')
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run_engine(store, [game()]), range(2)))
    with store.db() as c:
        assert len(snapshot(c, 'bet_outcomes')) == 1
        assert snapshot(c, 'bets') == cash


def test_ordinary_engine_settlement_still_matches_store_diagnostics(manual_entry, tmp_path):
    store, bid = manual_entry
    baseline = Store(tmp_path / 'baseline.sqlite')
    with store.db() as c, baseline.db() as b:
        bet = manual_snapshot(c, bid)['bet']
        b.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload) VALUES (?,?,?,?,?,?)',
                  tuple(bet[k] for k in ('tip_id', 'created_at', 'stake', 'odds', 'platform', 'payload')))
    run_engine(store, [game()])
    engine = Engine(store=baseline); engine.games = [game()]; engine.settle_bets()
    with store.db() as c, baseline.db() as b:
        actual, expected = manual_snapshot(c, bid), manual_snapshot(b, 1)
        for key in ('status', 'pnl'):
            assert actual['bet'][key] == expected['bet'][key]
        for key in ('result', 'probability', 'push_probability', 'brier', 'log_loss'):
            assert actual['outcome'][key] == expected['outcome'][key]


def test_unaudited_legacy_manual_cash_is_not_silently_reopened(manual_entry):
    store, bid = manual_entry
    store.settle(bid, 'manual', -.25)
    assert SportsOutcomeStore(store).open_bets() == []


@pytest.mark.parametrize('storage', ['text', 'blob', 'compressed'])
@pytest.mark.parametrize('location', ['hot', 'archived'])
def test_duplicate_publication_keys_block_prune_restore_and_rollback(tmp_path, storage, location):
    store = Store(tmp_path / 'retention.sqlite')
    bad = '{"publication_version":1,"publication_version":2}'
    value = bad if storage == 'text' else bad.encode()
    if storage == 'compressed':
        value = PREFIX + gzip.compress(value)
    with store.db() as c:
        c.executemany('INSERT INTO publications VALUES (?,?,?)', [(1, OLD, '{}'), (2, OLD, '{}'), (3, NOW, '{}')])
        c.commit()
        if location == 'hot':
            c.execute('UPDATE publications SET payload=? WHERE id=3', (value,)); c.commit()
        else:
            # A byte-authenticated older archive can still contain ambiguous JSON.
            archive_before_prune(c, cutoff=NOW, recorded_at=NOW)
            raw = value.encode() if isinstance(value, str) else value
            kind = 'text' if isinstance(value, str) else 'blob'
            c.execute(f'''UPDATE {ARCHIVES} SET storage_type=?,payload_gzip=?,payload_bytes=?,
                       payload_sha256=?,record_sha256=? WHERE publication_id=1''',
                      (kind, gzip.compress(raw), len(raw), hashlib.sha256(raw).hexdigest(), _seal(1, OLD, kind, raw)))
            c.execute('INSERT INTO publications VALUES (?,?,?)', (4, OLD, '{}'))
            c.execute('INSERT INTO publications VALUES (?,?,?)', (5, NOW, '{}')); c.commit()
        before = snapshot(c, 'publications')
        schema = snapshot(c, 'sqlite_master')
        archives = snapshot(c, ARCHIVES) if location == 'archived' else None
        events = snapshot(c, EVENTS) if location == 'archived' else None
        for operation in [lambda: retention_plan(c, cutoff=NOW),
                          lambda: archive_before_prune(c, cutoff=NOW, recorded_at=NOW)]:
            with pytest.raises(ValueError, match='Duplicate reference JSON key'):
                operation()
            assert snapshot(c, 'publications') == before
            assert snapshot(c, 'sqlite_master') == schema
        if location == 'archived':
            with pytest.raises(ValueError, match='Duplicate reference JSON key'):
                restore_publication(c, 1, recorded_at=NOW)
            assert snapshot(c, 'publications') == before
            assert snapshot(c, ARCHIVES) == archives
            assert snapshot(c, EVENTS) == events
