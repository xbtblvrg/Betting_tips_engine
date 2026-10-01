"""Unverified NHL recovery results must not contaminate prospective scores."""
import json
import pytest
from betting_engine.storage import Store
from betting_engine.domain import Game


def test_pending_nhl_result_waits_then_scores_verified_final_once(tmp_path):
    store=Store(tmp_path/'engine.sqlite3')
    forecast={'home_probability':.6,'draw_probability':0}
    with store.db() as c:
        c.execute('INSERT INTO prospective(game_id,created_at,start,league,model_version,payload) VALUES(?,?,?,?,?,?)',
          ('NHL-test','2026-09-29T12:00:00Z','2026-09-29T19:00:00Z','NHL','frozen-v1',json.dumps(forecast)))
    game=Game('NHL-test','NHL','2026-09-29T19:00:00Z','Home','Away','h','a','H','A','final',2026,4,2,
      source='https://api-web.nhle.com/v1/gamecenter/test/boxscore',fetched_at='2026-09-29T22:00:00Z',context={'nhl_settlement_pending':True})
    store.settle_forecasts([game])
    with store.db() as c:
        row=dict(c.execute('SELECT * FROM prospective').fetchone())
    assert row['outcome'] is None and row['brier'] is None
    assert json.loads(row['payload'])==forecast
    game.context={'nhl_settlement_pending':False,'official_result':{'game_state':'OFF','source_sha256':'verified-test'}}
    store.settle_forecasts([game])
    with store.db() as c:scored=dict(c.execute('SELECT * FROM prospective').fetchone())
    assert scored['outcome']==1 and scored['brier']==pytest.approx(.16)
    game.home_score=1;game.away_score=4
    store.settle_forecasts([game])
    with store.db() as c:assert dict(c.execute('SELECT * FROM prospective').fetchone())==scored
