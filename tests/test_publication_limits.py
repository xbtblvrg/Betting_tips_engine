"""Regression for the 2026-10-02 17:00–17:10 UTC 158-tip flood."""
from collections import Counter

from betting_engine.decisions import DEFAULT_SETTINGS, rank_and_size
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
