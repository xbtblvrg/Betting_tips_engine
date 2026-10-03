"""Result recovery runs before settlement and never rewrites original wagers."""
from copy import deepcopy
import json
import pytest
from betting_engine import engine, espn_results
from betting_engine.contracts import settlement_scores
from betting_engine.domain import Game, parse_time
from betting_engine.engine import Engine
from betting_engine.storage import Store

NOW=parse_time('2026-10-03T08:00:00Z')


def original(league='WNBA',identifier='401918294'):
    return {'id':league+identifier,'league':league,'game_id':league+':'+identifier,
            'home':'Golden State Valkyries','away':'Dallas Wings','start':'2026-10-03T01:00:00Z',
            'market':'moneyline','side':'home','line':None,'stake':1.44,'reference_odds':1.9,
            'ledger_contract':'sportsbook','probability':.6,'push_probability':0.,'model_version':'original'}


def final(tip):
    return Game(id=tip['game_id'],league=tip['league'],start=tip['start'],home=tip['home'],away=tip['away'],
                home_id='129689',away_id='3',home_abbr='GS',away_abbr='DAL',state='final',season=2026,
                home_score=77,away_score=73,source='https://site.api.espn.com/result',fetched_at=NOW.isoformat())


def seed(store,tips):
    with store.db() as db:
        for t in tips:
            body=json.dumps(t)
            db.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload) VALUES(?,?,?,?,?,?)',
                       (t['id'],t['start'],t['stake'],t['reference_odds'],'Stake.com',body))
            db.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload) VALUES(?,?,?,?)',
                       ('contract:'+t['id'],t['id'],t['start'],body))


def originals(store):
    with store.db() as db:
        return ([tuple(r) for r in db.execute('SELECT id,payload,stake,odds FROM bets ORDER BY id')],
                [tuple(r) for r in db.execute('SELECT family,payload FROM system_decisions ORDER BY family')])


@pytest.mark.parametrize('failure',['helper','persistence'])
def test_failure_preserves_pending_bets_and_other_leagues(tmp_path,monkeypatch,failure):
    store=Store(tmp_path/'engine.sqlite3');tip=original();other=original('MLB','1')
    future={**original('WNBA','2'),'start':'2026-10-04T01:00:00Z'}
    seed(store,[tip,other,future]);before=originals(store)
    old,mlb,scheduled=map(final,[tip,other,future]);scheduled.state='scheduled'
    worker=Engine.__new__(Engine);worker.store=store;worker.games=[old,mlb,scheduled]
    def recover(games,*args,**kw):
        if failure=='helper':raise OSError('injected recovery failure')
        return [deepcopy(g) for g in games],{'recovered_games':1,'unresolved_count':0}
    def persist(*a,**kw):raise OSError('injected games persistence failure')
    monkeypatch.setattr(espn_results,'recover_pending_espn',recover)
    monkeypatch.setattr(engine,'atomic_json',persist);monkeypatch.setattr(engine,'DATA',tmp_path)
    monkeypatch.setattr(engine,'utcnow',lambda:NOW)
    worker.recover_espn_results();worker.settle_bets();store.settle_system_decisions(worker.games)
    assert worker.espn_settlement_status['state']=='failed'
    assert worker.espn_settlement_status['unresolved_count']==1
    assert old.context['espn_settlement_pending'] is True
    assert not mlb.context.get('espn_settlement_pending') and not scheduled.context.get('espn_settlement_pending')
    assert originals(store)==before
    assert {b['tip_id']:b['status'] for b in store.bets()}=={tip['id']:'open',other['id']:'won',future['id']:'open'}
    assert {b['tip_id']:b['status'] for b in store.system_decisions()}=={tip['id']:'open',other['id']:'won',future['id']:'open'}


def test_recovered_missing_game_settles_both_ledgers_once(tmp_path,monkeypatch):
    store=Store(tmp_path/'engine.sqlite3');tip=original('NCAAF','401858245');seed(store,[tip]);before=originals(store)
    game=final(tip);game.context['official_result']={'sha256':'a'*64,'archive_path':'espn_settlement/sources/a.json'}
    worker=Engine.__new__(Engine);worker.store=store;worker.games=[]
    monkeypatch.setattr(engine,'DATA',tmp_path);monkeypatch.setattr(engine,'utcnow',lambda:NOW)
    monkeypatch.setattr(espn_results,'recover_pending_espn',lambda *a,**kw:([game],{'recovered_games':1,'unresolved_count':0}))
    worker.recover_espn_results()
    for _ in range(2):worker.settle_bets();store.settle_system_decisions(worker.games)
    assert (tmp_path/'games.json').exists() and originals(store)==before
    assert store.bets()[0]['status']=='won' and store.bets()[0]['pnl']==pytest.approx(1.296)
    assert store.system_decisions()[0]['status']=='won'
    with store.db() as db:
        rows=db.execute('SELECT evidence FROM bet_outcomes').fetchall();assert len(rows)==1
        assert json.loads(rows[0]['evidence'])['score_scope']['official_result']['sha256']=='a'*64
    with pytest.raises(ValueError,match='segment'):settlement_scores({'market':'quarter_1:totals'},game)


@pytest.mark.parametrize('market',['moneyline','quarter_1:totals','half_1:three_way'])
def test_pending_result_blocks_every_scope(market):
    game=final(original());game.context['espn_settlement_pending']=True
    with pytest.raises(ValueError,match='ESPN'):settlement_scores({'market':market},game)


@pytest.mark.parametrize('valid',[True,False])
def test_independent_period_scores_reattached_only_when_exact_final_matches(tmp_path,monkeypatch,valid):
    store=Store(tmp_path/'engine.sqlite3');tip={**original(),'market':'quarter_1:moneyline'}
    seed(store,[tip]);before=originals(store);game=final(tip)
    scores={game.id:{'home_id':game.home_id,'away_id':game.away_id,'home_score':77 if valid else 78,
                    'away_score':73,'start':game.start,'scores':{'quarter_1':{'home':22,'away':18,'complete':True,
                    'source':'https://independent.example/scope','source_sha256':'b'*64,'fetched_at':NOW.isoformat()}}}}
    worker=Engine.__new__(Engine);worker.store=store;worker.games=[]
    monkeypatch.setattr(engine,'DATA',tmp_path);monkeypatch.setattr(engine,'utcnow',lambda:NOW)
    monkeypatch.setattr(espn_results,'recover_pending_espn',lambda *a,**kw:([game],{'recovered_games':1,'unresolved_count':0}))
    worker.recover_espn_results(scores);worker.settle_bets();store.settle_system_decisions(worker.games)
    assert originals(store)==before
    assert store.bets()[0]['status']==('won' if valid else 'open')
    assert store.system_decisions()[0]['status']==('won' if valid else 'open')


@pytest.mark.parametrize('bad_kind',['missing_start','malformed_record'])
def test_partial_identity_scan_failure_cannot_allow_schedule_final_settlement(monkeypatch,bad_kind):
    tip=original();game=final(tip)
    bad={**tip,'start':None} if bad_kind=='missing_start' else None
    class Ledger:
        def open_bets(self):return [{'status':'open','payload':bad},{'status':'open','payload':tip}]
        def system_decisions(self):return []
    worker=Engine.__new__(Engine);worker.store=Ledger();worker.games=[game]
    monkeypatch.setattr(engine,'utcnow',lambda:NOW)
    def failed(*a,**kw):raise OSError('injected helper failure')
    monkeypatch.setattr(espn_results,'recover_pending_espn',failed)
    worker.recover_espn_results()
    assert worker.espn_settlement_status['state']=='failed'
    assert worker.espn_settlement_status['unresolved_count']==1
    with pytest.raises(ValueError,match='ESPN'):settlement_scores(tip,game)


def test_future_original_with_stale_final_stays_blocked_on_helper_failure(tmp_path,monkeypatch):
    tip={**original(),'start':'2026-10-04T01:00:00Z'};game=final(tip)
    store=Store(tmp_path/'engine.sqlite3');seed(store,[tip]);before=originals(store)
    worker=Engine.__new__(Engine);worker.store=store;worker.games=[game]
    monkeypatch.setattr(engine,'utcnow',lambda:NOW)
    def failed(*a,**kw):raise OSError('injected source failure')
    monkeypatch.setattr(espn_results,'recover_pending_espn',failed)
    worker.recover_espn_results();worker.settle_bets();store.settle_system_decisions(worker.games)
    assert worker.espn_settlement_status['unresolved_count']==1
    assert originals(store)==before
    assert store.bets()[0]['status']=='open' and store.system_decisions()[0]['status']=='open'
