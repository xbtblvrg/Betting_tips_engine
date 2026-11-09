from betting_engine.storage import Store
from betting_engine.domain import utcnow
from datetime import timedelta

def test_refresh_is_not_new_bet_or_duplicate_notification(tmp_path):
    s=Store(tmp_path/'test.db')
    tip={'id':'a','selection':'Home','model_version':'v1','label':'value','stake':1,'reference_odds':2}
    s.publish({'recommendations':[tip]});s.publish({'recommendations':[tip]})
    with s.db() as c:
        assert c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM bets').fetchone()[0]==0
    s.add_bet('a',1,2,'book');assert s.open_exposure()==1
    s.settle(1,'won',1);assert s.open_exposure()==0

def test_request_retry_is_idempotent_and_completed_bets_still_consume_daily_limit(tmp_path):
    s=Store(tmp_path/'test.db');s.publish({'recommendations':[{'id':'a','reference_odds':2}]})
    first=s.add_bet('a',3,2,'book','unique-key')
    assert s.add_bet('a',3,2,'book','unique-key')==first
    s.settle(first,'lost',-3)
    assert s.open_exposure()==0 and s.risk_committed()==3

def test_withdrawal_and_return_each_create_one_event(tmp_path):
    s=Store(tmp_path/'test.db');t={'id':'a','reference_odds':2}
    for tips in ([t],[t],[],[],[t],[t]):s.publish({'recommendations':tips})
    with s.db() as c:assert [r[0] for r in c.execute('SELECT event FROM outbox')]==['new','withdrawn','updated']

def test_unknown_system_cost_does_not_claim_net_profit(tmp_path):
    s=Store(tmp_path/'test.db')
    assert s.performance()['net_after_system_cost'] is None
    s.save_settings({'monthly_system_cost':30})
    assert s.performance()['net_after_system_cost']==0

def test_settlement_cannot_overwrite_an_already_recorded_result(tmp_path):
    s=Store(tmp_path/'test.db');s.publish({'recommendations':[{'id':'a','reference_odds':2}]});id=s.add_bet('a',1,2,'book')
    assert s.settle(id,'won',1)
    assert not s.settle(id,'lost',-1)
    assert s.bets()[0]['pnl']==1

def test_archived_pre_stake_candidates_are_preserved_as_non_actionable(tmp_path):
    s=Store(tmp_path/'test.db')
    future=(utcnow()+timedelta(hours=4)).isoformat()
    s.publish({'recommendations':[{'id':'old','game_id':'g','league':'NFL','home':'Home','away':'Away','start':future,'valid_until':future,'market':'totals','side':'under','line':45.5,'selection':'Összpontszám alatt 45.5','reference_platform':'Polymarket','reference_odds':1.9,'stake':2}], 'status':{'market_source':'Polymarket'}})
    archived=s.archived_candidates()
    assert archived[0]['id']=='old' and archived[0]['stake']==0
    assert archived[0]['stake_verified'] is False
    assert archived[0]['stake_status']=='Needs Stake Verification'

def test_alternatives_are_durable_and_only_withdrawn_when_absent_from_both_lists(tmp_path):
    s=Store(tmp_path/'test.db');t={'id':'alt','reference_odds':1.9,'stake':0}
    s.publish({'recommendations':[],'alternatives':[t]})
    assert s.get_tip('alt')['reference_odds']==1.9
    s.publish({'recommendations':[t],'alternatives':[]})
    assert 'withdrawn_at' not in s.get_tip('alt')
    s.publish({'recommendations':[],'alternatives':[]})
    assert s.get_tip('alt')['withdrawn_at']


def test_settle_forecasts_handles_alternative_probability_formats(tmp_path):
    import json
    from betting_engine.domain import Game
    s = Store(tmp_path / 'test.db')
    g = Game(
        id='UCL:12345', league='UCL', start='2026-10-13T12:00:00Z',
        home='Arsenal', away='PSG', home_id='1', away_id='2',
        home_abbr='ARS', away_abbr='PSG', state='final', season=2026,
        home_score=2, away_score=1, game_type='regular',
        source='http://test', fetched_at='2026-10-13T12:00:00Z',
        context={'segment_scores': {'half_1': {'home': 1, 'away': 0, 'complete': True, 'source': 'http://test', 'fetched_at': '2026-10-13T12:00:00Z', 'source_sha256': 'abc'}}}
    )
    with s.db() as c:
        c.execute(
            'INSERT INTO segment_prospective(game_id,scope,created_at,start,model_version,payload) VALUES (?,?,?,?,?,?)',
            (g.id, 'half_1', '2026-10-13T10:00:00Z', g.start, 'v1', json.dumps({'model_probabilities': {'three_way_home': 0.45}}))
        )
    # settle_forecasts must not raise KeyError: 'home'
    s.settle_forecasts([g])
    with s.db() as c:
        row = c.execute('SELECT outcome, brier FROM segment_prospective WHERE game_id=?', (g.id,)).fetchone()
        assert row is not None
        assert row[0] is not None


def test_performance_tier3_stratification(tmp_path):
    import json
    import sqlite3
    s = Store(tmp_path / 'test.db')
    # Create shadow_incubator DB with graduated market
    incubator_path = tmp_path / 'shadow_incubator.sqlite3'
    con_inc = sqlite3.connect(incubator_path)
    con_inc.execute('''
        CREATE TABLE graduated_markets (
            segment_key TEXT PRIMARY KEY,
            league TEXT,
            scope TEXT,
            family TEXT,
            sub_family TEXT,
            stats TEXT
        )
    ''')
    con_inc.execute(
        "INSERT INTO graduated_markets VALUES ('EPL|full_game|totals', 'EPL', 'full_game', 'totals', 'totals', '{}')"
    )
    con_inc.commit()
    con_inc.close()

    # Add 1 Tier 3 bet (EPL totals) and 1 Demoted bet (ATP moneyline)
    tip1 = {'id': 't1', 'league': 'EPL', 'model_scope': 'full_game', 'market': 'totals', 'reference_odds': 2.0}
    tip2 = {'id': 't2', 'league': 'ATP', 'model_scope': 'full_game', 'market': 'moneyline', 'reference_odds': 2.0}
    s.publish({'recommendations': [tip1, tip2]})
    b1 = s.add_bet('t1', 10.0, 2.0, 'book')
    b2 = s.add_bet('t2', 10.0, 2.0, 'book')
    s.settle(b1, 'won', 10.0)
    s.settle(b2, 'lost', -10.0)

    perf = s.performance()
    t3 = perf['tier3_performance']
    dem = perf['demoted_performance']
    assert t3['settled'] == 1
    assert t3['won'] == 1
    assert t3['lost'] == 0
    assert t3['pnl'] == 10.0
    assert t3['turnover'] == 10.0
    assert t3['roi'] == 1.0
    assert t3['hit_rate'] == 1.0

    assert dem['settled'] == 1
    assert dem['won'] == 0
    assert dem['lost'] == 1
    assert dem['pnl'] == -10.0
    assert dem['turnover'] == 10.0
    assert dem['roi'] == -1.0
    assert dem['hit_rate'] == 0.0


