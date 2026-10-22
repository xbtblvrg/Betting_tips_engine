"""Deterministic read/reconcile/settle interleavings on isolated Store databases."""
import json
import sqlite3
from decimal import Decimal

import pytest

from betting_engine.engine import Engine
from betting_engine.experimental_lifecycle import SportsOutcomeStore, manual_snapshot
from test_d31_review_132639_recovery import (
    manual_entry, game, ingest, override, receipt, snapshot,
)


def engine_with_interleaving(store, action, final_game):
    class InterleavedStore(SportsOutcomeStore):
        def settle(self, bid, status, pnl, evidence=None):
            # Engine already read open_bets and calculated cash on old terms.
            action(bid, status, pnl)
            return super().settle(bid, status, pnl, evidence)

    engine = Engine(store=InterleavedStore(store))
    engine.games = [final_game]
    engine.settle_bets()


@pytest.mark.parametrize('result,home,away,expected', [
    ('won', 100, 90, 1.96), ('lost', 90, 100, -2), ('push', 100, 100, 0),
])
def test_receipt_between_engine_read_and_settlement_uses_accepted_terms(
        manual_entry, result, home, away, expected):
    store, bid = manual_entry
    accepted = {**receipt(bid), 'stake': '2', 'odds': '1.98'}
    with store.db() as c:
        originals = snapshot(c, 'system_decisions')
        original_payload = json.loads(manual_snapshot(c, bid)['bet']['payload'])

    def reconcile(ticket, status, stale_pnl):
        assert ticket == bid and status == result
        assert stale_pnl == pytest.approx({'won': .9, 'lost': -1, 'push': 0}[result])
        with store.db() as c:
            ingest(c, 'racing-receipt', [accepted])

    final = game()
    final.home_score, final.away_score = home, away
    engine_with_interleaving(store, reconcile, final)
    with store.db() as c:
        settled = manual_snapshot(c, bid)
        assert settled['bet']['pnl'] == expected
        assert settled['bet']['status'] == settled['outcome']['result'] == result
        assert (settled['bet']['stake'], settled['bet']['odds']) == (2, 1.98)
        assert snapshot(c, 'system_decisions') == originals
        for key in ('stake', 'reference_odds', 'probability', 'model_version'):
            assert json.loads(settled['bet']['payload'])[key] == original_payload[key]
        audit = snapshot(c, 'bet_reconciliation')
        ingest(c, 'renamed-receipt', [accepted])
        assert manual_snapshot(c, bid) == settled
        assert snapshot(c, 'bet_reconciliation') == audit
    assert not SportsOutcomeStore(store).settle(bid, 'lost', -999, {'fixture': 'retry'})
    with store.db() as c:
        assert manual_snapshot(c, bid) == settled


@pytest.mark.parametrize('manual_first', [False, True])
def test_racing_manual_override_keeps_cash_and_appends_only_sports_outcome(manual_entry, manual_first):
    store, bid = manual_entry
    kept = {}

    def report_and_override():
        with store.db() as c:
            ingest(c, 'receipt', [{**receipt(bid), 'stake': '2', 'odds': '1.98'}])
            override(c, bid, total='0.75')
            kept['bets'] = snapshot(c, 'bets')
            kept['revisions'] = snapshot(c, 'd31_manual_revisions')
            kept['originals'] = snapshot(c, 'system_decisions')

    if manual_first:
        report_and_override()
    engine_with_interleaving(store, lambda *_: None if manual_first else report_and_override(), game())
    with store.db() as c:
        assert snapshot(c, 'bets') == kept['bets']
        assert snapshot(c, 'd31_manual_revisions') == kept['revisions']
        assert snapshot(c, 'system_decisions') == kept['originals']
        state = manual_snapshot(c, bid)
        assert state['bet']['pnl'] == -1.25
        assert state['outcome']['result'] == 'won'
        assert state['outcome']['brier'] == pytest.approx((.65 - 1) ** 2)


def test_settlement_uses_exact_receipt_decimal_not_rounded_float_projection(manual_entry):
    store, bid = manual_entry
    accepted = {**receipt(bid), 'stake': '2.25'}
    def reconcile(*_):
        with store.db() as c:
            ingest(c, 'receipt', [accepted])
    engine_with_interleaving(store, reconcile, game())
    expected = float(Decimal('2.25') * (Decimal(accepted['odds']) - 1))
    with store.db() as c:
        assert manual_snapshot(c, bid)['bet']['pnl'] == expected


def test_cash_update_failure_rolls_back_outcome_and_retry_uses_current_terms(manual_entry):
    store, bid = manual_entry
    with store.db() as c:
        ingest(c, 'receipt', [{**receipt(bid), 'stake': '2', 'odds': '1.98'}])
        before = manual_snapshot(c, bid)
        c.execute("""CREATE TRIGGER reject_cash BEFORE UPDATE ON bets
            BEGIN SELECT RAISE(ABORT, 'fixture cash failure'); END""")
    adapter = SportsOutcomeStore(store)
    with pytest.raises(sqlite3.IntegrityError, match='fixture cash failure'):
        adapter.settle(bid, 'won', .9, {'fixture': 'result'})
    with store.db() as c:
        assert manual_snapshot(c, bid) == before
        c.execute('DROP TRIGGER reject_cash')
    assert adapter.settle(bid, 'won', .9, {'fixture': 'result'})
    with store.db() as c:
        assert manual_snapshot(c, bid)['bet']['pnl'] == 1.96
