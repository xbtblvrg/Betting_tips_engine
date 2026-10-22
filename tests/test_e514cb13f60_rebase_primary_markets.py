"""Source-rebase regression: period faults cannot remove core full-game offers.

Bodies are hash-bound controller exports. Injected faults are synthetic; replay
does not refresh these historical quotes or grant model/execution approval.
"""
from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from betting_engine.contracts import split_market
from betting_engine.domain import Game
from betting_engine.market_catalog import raw_inventory
from betting_engine.markets import StakeSportsbook
from betting_engine.stake_api import market_records
from test_e514cb13f60_frozen_integration import exported


@pytest.mark.parametrize('league', ['NHL', 'NFL', 'WNBA'])
@pytest.mark.parametrize('invalid', [{}, [], False, 'periodnr=99|total=1.5'])
def test_period_fault_preserves_observed_primary_contracts(exported, league, invalid):
    publication, models, bound = exported
    original = next(t for t in publication['evaluated_candidates']
                    if t['league'] == league and split_market(t['market'])[0] != 'full_game')
    source = original['stake_evidence']['api_source']
    raw = gzip.decompress(bound('data/' + source['archive']))
    assert hashlib.sha256(raw).hexdigest() == source['sha256']
    body = json.loads(raw)
    before = deepcopy(body)
    fixture = body['fixture']
    game = Game(**deepcopy(next(g for g in publication['games'] if g['id'] == original['game_id'])))
    book = StakeSportsbook(fetcher=object(), segment_markets={
        key: model.report['validated_markets'] for key, model in models.items()})
    expected = book._markets_for_fixture(body, fixture, game, league, source['fetched_at'])
    primary = [m for m in expected if m.kind in ('moneyline', 'spreads', 'totals')]
    assert {m.kind for m in primary} == {'moneyline', 'spreads', 'totals'}

    # Reuse an actually recognized scoped score contract, then prepend a malformed
    # neighboring observation so iteration order cannot hide the fault.
    scoped = next(m for m in expected if ':' in m.kind and not m.kind.endswith(':spreads'))
    observed = next(r for _, r in market_records(body) if str(r.get('id')) == scoped.condition_id)
    broken = deepcopy(observed)
    broken.update(id='synthetic-rebase-period-fault', specifiers=invalid)
    changed = deepcopy(body)
    changed['groups'].insert(0, {'id': 'synthetic-fault', 'name': 'synthetic-fault',
                                 'markets': [[broken]]})
    actual = book._markets_for_fixture(changed, fixture, game, league, source['fetched_at'])
    assert actual == expected  # Includes exact IDs, signed lines, prices and source times.
    assert book.quotes(actual) == book.quotes(expected)
    assert body == before

    # Catalogue consumes schedule `date`; the fixture endpoint names the same
    # millisecond timestamp `startTime`. Never replace it with export time.
    rows = raw_inventory(changed, {**fixture, 'date': fixture['startTime']}, league, source)
    rejected = next(r for r in rows if r['market_id'] == broken['id'])
    assert rejected['raw_contract'] == broken
    assert rejected['source']['sha256'] == source['sha256']
    if not isinstance(invalid, str):
        assert rejected['rejection_reason'] == 'invalid_specifiers_type'
