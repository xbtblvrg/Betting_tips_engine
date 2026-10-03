"""Regression for the 2026-10-02 17:00–17:10 UTC 158-tip flood."""
from collections import Counter

from betting_engine.decisions import DEFAULT_SETTINGS, rank_and_size
from betting_engine.contracts import allocation_family
from betting_engine.storage import Store
from test_core_market_sizing import row


def test_158_tip_publication_replay_is_bounded_by_live_execution_limits():
    # The production incident emitted 158 funded rows in one ten-minute
    # window. Replaying the same shape (many markets, same game clusters)
    # under the live policy must produce at most ten new funded rows.
    markets=['moneyline','spreads','totals','home_total','away_total',
             'quarter_1:moneyline','quarter_2:moneyline','half_1:totals']
    rows=[]
    for i in range(158):
        market=markets[i % len(markets)]
        game=f'game-{i % 20}'
        rows.append(row(f'tip-{i}',market,p=.65,odds=2.0,game=game))
    settings={**DEFAULT_SETTINGS,'publication_limits_version':'v1',
              'max_new_funded_per_publication':10,'max_per_event':3,
              'max_period_per_event':1,'max_stake_per_event':5.,
              'max_daily_family_share':.25}
    chosen, alternatives=rank_and_size(rows,settings)
    funded=[tip for tip in chosen if tip.get('stake',0)>0]
    assert len(funded)<=10
    assert max(Counter(t['game_id'] for t in funded).values(),default=0)<=3
    assert max(Counter(t['game_id'] for t in funded if t.get('model_scope')!='full_game').values(),default=0)<=1
    assert max((sum(t['stake'] for t in funded if t['game_id']==game) for game in {t['game_id'] for t in funded}),default=0)<=5
    family_counts=Counter((t['league'],t['market']) for t in funded)
    assert max(family_counts.values(),default=0)<=3
    assert len(funded)+sum(t.get('stake',0)>0 for t in alternatives)<=10


def test_small_batch_respects_actual_family_share_at_publication(tmp_path):
    """A ten-slot estimate must not authorize three same-family tickets."""
    settings={**DEFAULT_SETTINGS,'publication_limits_version':'v1'}
    rows=[row(str(i),'moneyline',game=str(i)) for i in range(3)]
    selected,_=rank_and_size(rows,settings)
    funded=[tip for tip in selected if tip['stake']>0]
    assert len(funded)==1
    assert sum(t.get('allocation_code')=='family_share_limit' for t in selected)==2
    with Store(tmp_path/'limits.sqlite').db() as db:
        Store._validate_publication_limits(db,{'recommendations':selected},settings)


def test_family_share_is_recomputed_after_another_family_is_removed(tmp_path):
    settings={**DEFAULT_SETTINGS,'publication_limits_version':'v1'}
    rows=[row(str(i),'moneyline',game=str(i)) for i in range(3)]
    rows += [row('spread','spreads',game='spread'),row('total','totals',game='total')]
    selected,_=rank_and_size(rows,settings)
    funded=[tip for tip in selected if tip['stake']>0]
    assert len(funded)==3
    assert Counter(allocation_family(t) for t in funded)=={'moneyline':1,'spreads':1,'totals':1}
    with Store(tmp_path/'limits.sqlite').db() as db:
        Store._validate_publication_limits(db,{'recommendations':selected},settings)


def test_existing_daily_issuances_and_checkpoint_count_do_not_deadlock(tmp_path):
    from test_autotracking import tip
    from betting_engine.domain import utcnow
    settings={**DEFAULT_SETTINGS,'publication_limits_version':'v1'}
    store=Store(tmp_path/'limits.sqlite')
    # Nine first issues survived before the incident; daily checkpoints and
    # UI counters must not supply a different denominator from the ledger.
    markets=['moneyline']*3+['totals']*3+['spreads']*2+['regulation:three_way']
    with store.db() as db:
        for i,market in enumerate(markets):
            Store.record_issuance(db,tip(id=f'held-{i}',game_id=f'held-{i}',league='NCAAF',market=market),utcnow().isoformat())
    counts=store.daily_family_counts()
    rows=[row('new','moneyline',game='new',league='NCAAF')]
    selected,_=rank_and_size(rows,settings,daily_family_counts=counts,daily_funded_count=100)
    assert not any(t['stake'] for t in selected)
    with store.db() as db:
        Store._validate_publication_limits(db,{'recommendations':selected},settings)
    assert store.daily_family_counts()==counts


def test_family_trimming_resizes_survivors_against_final_related_exposure(tmp_path):
    from betting_engine.model_allocation import verify_family_publication
    settings={**DEFAULT_SETTINGS,'publication_limits_version':'v1',
              'model_pick_allocation_policy':'distinct_family_v1'}
    rows=[row('m3','moneyline',game='g3'),row('m2','moneyline',game='g2'),
          row('m1','moneyline',game='g1'),row('spread','spreads',game='g4'),
          row('total','totals',game='g1')]
    selected,_=rank_and_size(rows,settings)
    funded=[tip for tip in selected if tip['stake']>0]
    assert {t['id'] for t in funded}=={'m3','spread','total'}
    total=next(t for t in funded if t['id']=='total')
    assert total['stake']==3.87
    assert total['stake_sizing']['related_contracts']==0
    with Store(tmp_path/'limits.sqlite').db() as db:
        Store._validate_publication_limits(db,{'recommendations':selected},settings)
        verify_family_publication(db,selected,settings)


def test_same_game_new_allocations_are_counted_once_up_to_existing_cap():
    """A real three-ticket cap must not become two through double counting."""
    import copy
    for held in range(4):
        settings={**DEFAULT_SETTINGS,'publication_limits_version':'v1'}
        candidates=[row('winner','moneyline',p=.52),row('spread','spreads',p=.52),
                    row('total','totals',p=.52,side='over',line=8.5)]
        selected,alternatives=rank_and_size(copy.deepcopy(candidates),settings,
            held_event_counts={'game':held},held_event_stakes={'game':float(held)})
        funded=[t for t in selected if t['stake']>0]
        assert len(funded)==max(0,3-held)
        assert held+sum(t['stake'] for t in funded)<=5
        assert len(funded)+len([t for t in alternatives if t.get('allocation_code')=='event_limit'])==3
