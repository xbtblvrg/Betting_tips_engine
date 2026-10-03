from copy import deepcopy
import pytest
from betting_engine.decisions import DEFAULT_SETTINGS, publication_limits, publication_limit_receipt, rank_and_size
from betting_engine.storage import Store
from betting_engine.contracts import allocation_family
from betting_engine import server
from fastapi.testclient import TestClient
from test_core_market_sizing import row
from test_autotracking import store

SETTINGS={**DEFAULT_SETTINGS,'publication_limits_version':'v3','sparse_league_overrides':['NFL','NHL','MLB'],
          'max_per_event':4,'max_period_per_event':2,'max_stake_per_event':10.,
          'max_new_funded_per_publication':15,'max_daily_family_share':.35,'allow_event_additions':True}

def test_explicit_overrides_only_and_legacy_receipt_unchanged():
    for league in ['NFL','NHL','MLB']:
        limits=publication_limits(SETTINGS,league)
        assert (limits['max_per_event'],limits['max_period_per_event'],limits['max_stake_per_event'])==(6,3,20.)
    for league in ['NBA','WNBA','NCAAF',None]:
        assert publication_limits(SETTINGS,league)['max_per_event']==4
    assert 'sparse_league_overrides' not in publication_limit_receipt({**SETTINGS,'publication_limits_version':'v2'})
    assert publication_limit_receipt(SETTINGS)['sparse_league_overrides']==['NFL','NHL','MLB']
    with pytest.raises(ValueError):publication_limits({**SETTINGS,'sparse_league_overrides':['NCAAF']})

def rows(league='NFL'):
    return [row(str(i),market,p=.8,odds=2.,game='g',league=league) for i,market in enumerate(
        ['moneyline','totals','spreads','home_total','away_total','half_1:totals','quarter_1:totals'])]

def test_six_diverse_professional_allocations_and_atomic_limit(tmp_path):
    selected,_=rank_and_size(rows(),SETTINGS)
    funded=[r for r in selected if r['stake']]
    assert len(funded)==6
    assert len({allocation_family(r) for r in funded})==6
    assert sum(r['stake'] for r in funded)<=20
    with Store(tmp_path/'x.sqlite').db() as db:
        Store._validate_publication_limits(db,{'recommendations':funded},SETTINGS)
        bad=deepcopy(funded);bad.append({**bad[0],'id':'duplicate-new'})
        with pytest.raises(ValueError,match='distinct scope/market families'):
            Store._validate_publication_limits(db,{'recommendations':bad},SETTINGS)
    selected,_=rank_and_size(rows('NCAAF'),SETTINGS)
    assert sum(bool(r['stake']) for r in selected)<=4

def test_held_family_cannot_rotate_to_another_line():
    selected,_=rank_and_size(rows(),SETTINGS,held_event_counts={'g':4},held_allocation_families={('g','totals')})
    assert all(not r['stake'] for r in selected if allocation_family(r)=='totals')
    assert sum(bool(r['stake']) for r in selected)<=2

def test_api_accepts_explicit_bounded_policy_and_blocks_downgrade(tmp_path,monkeypatch):
    saved=store(tmp_path,**SETTINGS)
    monkeypatch.setattr(server.engine,'store',saved)
    monkeypatch.setattr(server.engine,'refresh',lambda:True)
    client=TestClient(server.app)
    assert client.put('/api/settings',json={'publication_limits_version':'v3','sparse_league_overrides':['NFL']}).status_code==200
    assert client.put('/api/settings',json={'publication_limits_version':'v2'}).status_code==422
    assert client.put('/api/settings',json={'sparse_league_overrides':['NCAA']}).status_code==422


def test_original_overcap_exposure_blocks_new_bets_without_rewriting():
    selected,_=rank_and_size(rows(),SETTINGS,held_event_counts={'g':14},held_event_stakes={'g':35})
    assert not any(r['stake'] for r in selected)


def test_mutated_override_invalidates_sealed_publication_receipt():
    from test_continuous_delivery import reviewed
    from betting_engine.governance import verify_publication
    from betting_engine.domain import utcnow
    tip,settings,guard=reviewed('strict',probability=.72,odds=2.)
    active={**settings,**SETTINGS}
    tip['decision_guard']['publication_limits']=publication_limit_receipt(active)
    verify_publication(tip,utcnow(),active)
    with pytest.raises(ValueError,match='exposure limits changed'):
        verify_publication(tip,utcnow(),{**active,'sparse_league_overrides':[]})

@pytest.mark.parametrize('kind,expected',[('count','maximum 6'),('stake','maximum stake'),('period','maximum 3')])
def test_transaction_rejects_v3_overcap_bundle(tmp_path,kind,expected):
    bundle=rows()
    if kind=='stake':bundle=bundle[:5]
    if kind=='period':
        bundle=[row(str(i),f'quarter_{i+1}:totals',p=.8,odds=2.,game='g',league='NFL') for i in range(4)]
    for tip in bundle:tip['stake']=5 if kind=='stake' else 1
    with Store(tmp_path/'x.sqlite').db() as db:
        with pytest.raises(ValueError,match=expected):
            Store._validate_publication_limits(db,{'recommendations':bundle},SETTINGS)
