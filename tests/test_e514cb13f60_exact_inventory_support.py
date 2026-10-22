"""Discovery must attribute support to exact contracts, not shared feed IDs.

Malformed neighbors are synthetic mutations of a retained observed contract.
These endpoint replays establish no current availability or model approval.
"""
from copy import deepcopy

import pytest

from betting_engine.domain import Game
from betting_engine.markets import StakeSportsbook
from test_e514cb13f60_inventory_integration import setup_feed
from test_e514cb13f60_scope_identity import parse


@pytest.mark.parametrize('invalid', [
    'total=1.5|periodnr=9',
    'total=1.5|total=1.5',
    'total=NaN',
    'total=1.25',
    'total=1.5|unknown=1',
])
def test_shared_market_id_does_not_launder_rejected_string_specifier(monkeypatch, invalid):
    case, _, feed = setup_feed(monkeypatch, invalid)
    before = deepcopy(feed.bodies)
    game = Game(**case['game'])
    book = StakeSportsbook(feed, segment_markets={
        (game.league, case['scope']): [case['family']]})
    markets, _, issues = book.discover([game])
    assert not issues
    assert [m.id for m in markets] == [m.id for m in parse(case)]
    rejected = next(row for row in book.inventory if row['specifiers'] == invalid)
    assert rejected['supported_outcomes'] == 0
    assert rejected['reason'] == 'unsupported_or_inactive_contract'
    valid = next(row for row in book.inventory if row['specifiers'] != invalid)
    assert valid['supported_outcomes'] == 2
    assert valid['reason'] == 'supported'
    assert feed.bodies == before


def test_inventory_recovery_cannot_borrow_an_unapproved_family(monkeypatch):
    case, _, feed = setup_feed(monkeypatch, {'total': 1.5})
    book = StakeSportsbook(feed, segment_markets={})
    markets, events, issues = book.discover([Game(**case['game'])])
    assert not issues and len(events) == 1
    assert markets == []
    assert len(book.inventory) == 2
    assert all(row['supported_outcomes'] == 0 for row in book.inventory)
    assert {row['reason'] for row in book.inventory} == {
        'invalid_specifiers_type', 'unsupported_or_inactive_contract'}
