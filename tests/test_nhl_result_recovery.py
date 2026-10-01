"""Official result recovery preserves original records and exact score scopes."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import gzip
import hashlib
import json

import pytest
from betting_engine.domain import Game, parse_time
from betting_engine.nhl_results import parse_landing, recover_pending_nhl
from betting_engine.contracts import settlement_scores, contract_value

FIXTURES = Path(__file__).parent/'fixtures/nhl_results'
NOW = parse_time('2026-09-30T18:00:00+00:00')


def observed(n=4):
    meta=next(m for m in json.loads((FIXTURES/'manifest.json').read_text()) if m['url'].endswith(f'202602000{n}/landing'))
    raw=gzip.decompress((FIXTURES/meta['path']).read_bytes());body=json.loads(raw)
    from betting_engine.providers import NHL_NAMES
    tip={'id':'tip'+str(n),'league':'NHL','game_id':'NHL:'+str(body['id']),'home':NHL_NAMES[body['homeTeam']['abbrev']],
         'away':NHL_NAMES[body['awayTeam']['abbrev']],'start':body['startTimeUTC'],
         'market':'moneyline','side':'away','line':None,'stake':1.44,'reference_odds':3.2,'ledger_contract':'sportsbook',
         'probability':.45,'push_probability':0.,'model_version':'original-model'}
    return raw,meta,tip


def changed(fn,n=4):
    raw,meta,tip=observed(n);body=json.loads(raw);fn(body);raw=json.dumps(body).encode()
    return raw,{**meta,'sha256':hashlib.sha256(raw).hexdigest()},tip


@pytest.mark.parametrize('n,final,regulation',[(1,(0,1),(0,0)),(2,(2,3),(2,3)),(3,(3,0),(3,0)),(4,(5,6),(5,5)),(5,(5,2),(5,2))])
def test_actual_hash_archived_final_and_regulation_scores(n,final,regulation):
    raw,meta,tip=observed(n);before=deepcopy(tip)
    game=parse_landing(raw,meta,tip,NOW)
    assert game.state=='final' and (game.home_score,game.away_score)==final
    assert settlement_scores({'market':'regulation:three_way'},game)[:2]==regulation
    assert settlement_scores(tip,game)[:2]==final
    assert game.context['official_result']['sha256']==meta['sha256']
    assert all(v['source_sha256']==meta['sha256'] for v in game.context['segment_scores'].values())
    assert tip==before


def test_overtime_win_is_regulation_draw_and_zero_period_is_observed():
    game=parse_landing(*observed(1),NOW)
    assert contract_value('moneyline','away',None,*settlement_scores({'market':'moneyline'},game)[:2])>0
    reg=settlement_scores({'market':'regulation:three_way'},game)[:2]
    assert contract_value('three_way','away',None,*reg)<0
    assert contract_value('three_way','draw',None,*reg)>0
    assert settlement_scores({'market':'period_3:totals'},game)[:2]==(0,0)


def test_shootout_goal_counts_only_in_full_game_not_regulation_or_third_period():
    def shootout(body):
        body['periodDescriptor'].update(number=5,periodType='SO')
        body['summary']['scoring'][3]['goals']=[]
        body['summary']['scoring'].append({'periodDescriptor':{'number':5,'periodType':'SO'},'goals':[]})
    game=parse_landing(*changed(shootout,1),NOW)
    assert (game.home_score,game.away_score)==(0,1)
    assert settlement_scores({'market':'regulation:totals'},game)[:2]==(0,0)
    assert settlement_scores({'market':'period_3:totals'},game)[:2]==(0,0)


@pytest.mark.parametrize('mutate,reason',[
    (lambda b:b.update(id=2026020999),'identity'),
    (lambda b:b.update(startTimeUTC='2026-09-29T02:00:00Z'),'start'),
    (lambda b:b['homeTeam'].update(abbrev='TOR'),'team'),
    (lambda b:b.update(gameState='LIVE'),'not_final'),
    (lambda b:b['homeTeam'].update(score=None),'integer'),
    (lambda b:b['homeTeam'].update(score=True),'integer'),
    (lambda b:b['homeTeam'].update(score=6),'winner'),
    (lambda b:b['homeTeam'].update(score=2),'margin'),
])
def test_wrong_identity_unfinished_and_invalid_final_are_not_recovered(mutate,reason):
    with pytest.raises(ValueError,match=reason):parse_landing(*changed(mutate),NOW)


def test_source_hash_url_and_time_are_mandatory():
    raw,meta,tip=observed()
    for corrupt in ({**meta,'sha256':'0'*64},{**meta,'url':'https://other.invalid/landing'},
                    {**meta,'fetched_at':'2026-10-01T18:00:00Z'}):
        with pytest.raises(ValueError):parse_landing(raw,corrupt,tip,NOW)


@pytest.mark.parametrize('mutate',[
    lambda b:b['summary']['scoring'].pop(1),
    lambda b:b['summary']['scoring'].append(deepcopy(b['summary']['scoring'][0])),
    lambda b:b['summary']['scoring'][0]['goals'].pop(),
    lambda b:b['summary']['scoring'][0]['goals'][0].update(homeScore=50),
    lambda b:b['summary']['scoring'][0]['goals'][0]['teamAbbrev'].update(default='TOR'),
])
def test_partial_or_conflicting_goal_log_never_substitutes_final_for_period(mutate):
    game=parse_landing(*changed(mutate),NOW)
    assert game.context['official_result']['scoped_scores_state']=='incomplete'
    assert (game.home_score,game.away_score)==(5,6)
    with pytest.raises(ValueError,match='segment'):settlement_scores({'market':'regulation:three_way'},game)


class Feed:
    def __init__(self,fail=()):self.calls=[];self.fail=set(fail)
    def get(self,url,ttl):
        assert ttl==300;self.calls.append(url);n=int(url.split('/')[-2][-1])
        if n in self.fail:raise OSError('test source failure')
        raw,meta,_=observed(n);return raw,meta


def row(tip,id=1):return {'id':id,'status':'open','platform':'Stake.com','payload':tip}


def test_deduplicated_pending_recovery_includes_decisions_without_ticket_and_skips_future(tmp_path):
    t1=observed(1)[2];t4=observed(4)[2];future={**observed(5)[2],'start':'2026-10-01T02:00:00Z'};feed=Feed()
    tickets=[row(t1),row(t1,2),row(future,3)]
    original=deepcopy(tickets)
    games,status=recover_pending_nhl([],tickets,[row(t1),row(t4)],data_dir=tmp_path,fetcher=feed,now=NOW)
    assert len(feed.calls)==2 and {g.id for g in games}=={t1['game_id'],t4['game_id']}
    assert status['recovered_games']==2 and tickets==original
    assert len(list((tmp_path/'nhl_settlement/attempts').glob('*.json')))==2
    again,audit=recover_pending_nhl(games,tickets,[row(t1),row(t4)],data_dir=tmp_path,fetcher=feed,now=NOW)
    assert len(feed.calls)==2 and audit['requested_games']==0


def test_conflicting_original_identity_never_guessed(tmp_path):
    tip=observed()[2];conflict={**tip,'away':'Montreal Canadiens'};feed=Feed()
    games,status=recover_pending_nhl([],[row(tip),row(conflict)],[],data_dir=tmp_path,fetcher=feed,now=NOW)
    assert games==[] and not feed.calls
    assert status['rows'][0]['state']=='conflicting_original_identities'


def test_failed_sources_retain_history_backoff_and_do_not_starve_other_pending_game(tmp_path):
    a=observed(1)[2];b=observed(4)[2];feed=Feed(fail={1})
    _,first=recover_pending_nhl([],[row(a),row(b)],[],data_dir=tmp_path,fetcher=feed,now=NOW,max_games=1)
    assert first['requested_games']==1
    games,second=recover_pending_nhl([],[row(a),row(b)],[],data_dir=tmp_path,fetcher=feed,now=NOW+timedelta(seconds=60),max_games=1)
    assert [g.id for g in games]==[b['game_id']] and len(feed.calls)==2
    assert any(r['state']=='retry_backoff' for r in second['rows'])
    feed.fail.clear()
    games,third=recover_pending_nhl(games,[row(a),row(b)],[],data_dir=tmp_path,fetcher=feed,now=NOW+timedelta(seconds=301),max_games=1)
    assert len(games)==2 and third['attempts'][a['game_id']]['count']==2
    history=[json.loads(p.read_text()) for p in (tmp_path/'nhl_settlement/attempts').glob('*.json')]
    assert any(r.get('error')=='OSError' for r in history)


def test_original_bets_decisions_and_outcomes_settle_idempotently_in_sync(tmp_path):
    from betting_engine.storage import Store
    from betting_engine.engine import Engine
    store=Store(tmp_path/'copy.sqlite3');tip=observed(4)[2]
    tips=[tip,{**tip,'id':'regulation-draw-loses','market':'regulation:three_way'},
          {**tip,'id':'period-push','market':'period_2:moneyline'},
          {**observed(5)[2],'id':'future','start':'2026-10-01T02:00:00Z'}]
    with store.db() as db:
        for i,t in enumerate(tips,1):
            raw=json.dumps(t)
            db.execute('INSERT INTO bets(id,tip_id,created_at,stake,odds,platform,payload) VALUES(?,?,?,?,?,?,?)',(i,t['id'],t['start'],t['stake'],t['reference_odds'],'Stake.com',raw))
            db.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload) VALUES(?,?,?,?)',('contract:'+t['id'],t['id'],t['start'],raw))
        original=[tuple(r) for r in db.execute('SELECT id,tip_id,created_at,stake,odds,payload FROM bets ORDER BY id')]
        decisions=[tuple(r) for r in db.execute('SELECT family,tip_id,issued_at,payload FROM system_decisions ORDER BY family')]
    games,_=recover_pending_nhl([],store.open_bets(),store.system_decisions(),data_dir=tmp_path,fetcher=Feed(),now=NOW)
    saved=tmp_path/'games.json';saved.write_text(json.dumps([g.dict() for g in games]))
    games=[Game(**g) for g in json.loads(saved.read_text())]
    engine=Engine.__new__(Engine);engine.games=games;engine.store=store
    engine.settle_bets();store.settle_system_decisions(games)
    with store.db() as db:
        assert [tuple(r) for r in db.execute('SELECT id,tip_id,created_at,stake,odds,payload FROM bets ORDER BY id')]==original
        assert [tuple(r) for r in db.execute('SELECT family,tip_id,issued_at,payload FROM system_decisions ORDER BY family')]==decisions
        bets=[tuple(r) for r in db.execute('SELECT tip_id,status,pnl FROM bets ORDER BY tip_id')]
        assert bets==[tuple(r) for r in db.execute('SELECT tip_id,status,pnl FROM system_decisions ORDER BY tip_id')]
        assert dict((r[0],r[1]) for r in bets)=={'future':'open','period-push':'push','regulation-draw-loses':'lost','tip4':'won'}
        outcomes=[tuple(r) for r in db.execute('SELECT * FROM bet_outcomes ORDER BY bet_id')]
        assert len(outcomes)==3
        for record in db.execute('SELECT evidence FROM bet_outcomes'):
            score=json.loads(record['evidence'])['score_scope']
            source=score.get('official_result') or score.get('final_summary_source')
            assert hashlib.sha256((tmp_path/source['archive']).read_bytes()).hexdigest()==source['sha256']
        for record in db.execute("SELECT outcome_evidence FROM system_decisions WHERE status!='open'"):
            score=json.loads(record['outcome_evidence'])['score_scope']
            source=score.get('official_result') or score.get('final_summary_source')
            assert hashlib.sha256((tmp_path/source['archive']).read_bytes()).hexdigest()==source['sha256']
    engine.settle_bets();store.settle_system_decisions(games)
    with store.db() as db:assert [tuple(r) for r in db.execute('SELECT * FROM bet_outcomes ORDER BY bet_id')]==outcomes


def test_provider_rollover_retains_last_week_final_and_loads_current_nhl_season(tmp_path,monkeypatch):
    from betting_engine import providers
    from betting_engine.domain import Game
    calls=[]
    official=parse_landing(*observed(4),NOW)
    scheduled=deepcopy(official);scheduled.state='scheduled';scheduled.home_score=scheduled.away_score=None
    scheduled.source='https://github.com/sportsdataverse/season.csv';scheduled.fetched_at=(NOW+timedelta(minutes=5)).isoformat()
    def rows_meta(rows,url):return rows,{'url':url,'fetched_at':NOW.isoformat(),'sha256':'a'*64}
    def season(self,league,year):
        calls.append(('season',league,year))
        return rows_meta([scheduled] if year==2027 else [],scheduled.source)
    def recent(self,date=None):
        calls.append(('current',date))
        return rows_meta([official] if date else [],f'https://api-web.nhle.com/v1/schedule/{date or "2026-09-30"}')
    monkeypatch.setattr(providers,'DATA',tmp_path)
    monkeypatch.setattr(providers,'utcnow',lambda:NOW)
    monkeypatch.setattr(providers.SportsData,'sd_season',season)
    monkeypatch.setattr(providers.SportsData,'nhl_current',recent)
    monkeypatch.setattr(providers.SportsData,'nhl_official_season',lambda self,y:rows_meta([],'https://api-web.nhle.com/history'))
    games,status=providers.SportsData(fetcher=object()).load(('NHL',))
    assert ('season','NHL',2027) in calls
    assert ('current','2026-09-23') in calls and ('current',None) in calls
    assert len(games)==1 and games[0].state=='final' and games[0].away_score==6
    assert all(r['ok'] for r in status)


def test_scope_incomplete_recovery_remains_pending_and_does_not_refetch_each_poll(tmp_path):
    raw,meta,tip=changed(lambda b:b['summary']['scoring'].pop(1));tip['market']='period_2:moneyline'
    class Partial:
        calls=0
        def get(self,*a,**kw):self.calls+=1;return raw,meta
    feed=Partial()
    games,status=recover_pending_nhl([],[row(tip)],[],data_dir=tmp_path,fetcher=feed,now=NOW)
    assert status['rows'][0]['state']=='partial_result_recovered'
    assert status['rows'][0]['missing_scopes']==['period_2']
    with pytest.raises(ValueError):settlement_scores(tip,games[0])
    _,again=recover_pending_nhl(games,[row(tip)],[],data_dir=tmp_path,fetcher=feed,now=NOW+timedelta(seconds=1))
    assert feed.calls==1 and again['rows'][0]['state']=='retry_backoff'


def test_recovered_result_body_is_retained_by_hash_independent_of_url_cache(tmp_path):
    raw,meta,tip=observed(4)
    games,status=recover_pending_nhl([],[row(tip)],[],data_dir=tmp_path,fetcher=Feed(),now=NOW)
    source=games[0].context['official_result']
    path=tmp_path/source['archive']
    assert path.read_bytes()==raw and hashlib.sha256(path.read_bytes()).hexdigest()==meta['sha256']
    history=json.loads((tmp_path/status['attempts'][tip['game_id']]['attempt_evidence']).read_text())
    assert history['source']['archive']==source['archive']
    assert history['source']['fetched_at']==meta['fetched_at']
    assert status['state']=='healthy' and status['unresolved_count']==0


def test_corrupt_retained_result_evidence_is_not_overwritten_or_used(tmp_path):
    _,meta,tip=observed(4);folder=tmp_path/'nhl_settlement/sources';folder.mkdir(parents=True)
    archive=folder/(meta['sha256']+'.json');archive.write_bytes(b'corrupt')
    games,status=recover_pending_nhl([],[row(tip)],[],data_dir=tmp_path,fetcher=Feed(),now=NOW)
    assert not games and status['state']=='degraded' and status['unresolved_count']==1
    assert archive.read_bytes()==b'corrupt'


def test_restart_reuses_verified_archived_final_before_network_backoff(tmp_path):
    tip=observed(4)[2];feed=Feed()
    first,audit=recover_pending_nhl([],[row(tip)],[],data_dir=tmp_path,fetcher=feed,now=NOW)
    assert len(feed.calls)==1
    # Simulate interruption before games.json and the wager were committed.
    recovered,again=recover_pending_nhl([],[row(tip)],[],data_dir=tmp_path,fetcher=feed,now=NOW+timedelta(seconds=1))
    assert len(feed.calls)==1 and recovered[0].dict()==first[0].dict()
    assert again['requested_games']==0 and again['recovered_games']==again['cached_recovered_games']==1
    assert again['unresolved_count']==0 and again['rows'][0]['reused_archived_evidence'] is True


def test_settlement_evidence_retains_exact_source_hash_archive_for_full_and_scoped(tmp_path):
    raw,meta,tip=observed(4)
    games,_=recover_pending_nhl([],[row(tip)],[],data_dir=tmp_path,fetcher=Feed(),now=NOW)
    game=games[0]
    full=settlement_scores(tip,game)[2]
    scoped=settlement_scores({'market':'regulation:three_way'},game)[2]
    source=full['official_result']
    assert source==scoped['final_summary_source']
    assert source['sha256']==scoped['source_sha256']==meta['sha256']
    assert source['game_id']==tip['game_id']
    assert (tmp_path/source['archive']).read_bytes()==raw
    # Mutating returned top-level source metadata cannot edit the Game record.
    source['claim']='external display change'
    assert game.context['official_result']['claim']!='external display change'


def test_plain_schedule_final_still_gets_hash_bound_landing_before_settlement(tmp_path):
    raw,meta,tip=observed(1)
    schedule_final=parse_landing(raw,meta,tip,NOW)
    schedule_final.context={}  # A normal SportsData recent-week final.
    feed=Feed()
    games,status=recover_pending_nhl([schedule_final],[row(tip)],[],data_dir=tmp_path,fetcher=feed,now=NOW)
    assert len(feed.calls)==1 and status['recovered_games']==1
    evidence=settlement_scores(tip,games[0])[2]['official_result']
    assert evidence['sha256']==meta['sha256'] and (tmp_path/evidence['archive']).read_bytes()==raw


def test_failed_exact_result_fetch_blocks_schedule_final_for_both_settlement_scopes(tmp_path):
    raw,meta,tip=observed(4);old=parse_landing(raw,meta,tip,NOW);original=deepcopy(old.dict())
    old.context={}  # Schedule final exists; exact retained result does not.
    games,status=recover_pending_nhl([old],[row(tip)],[],data_dir=tmp_path,fetcher=Feed(fail={4}),now=NOW)
    assert games[0].state=='final' and (games[0].home_score,games[0].away_score)==(5,6)
    assert games[0].context['nhl_settlement_pending'] is True
    assert old.context=={}  # Input sports records are not mutated in place.
    for market in ('moneyline','regulation:three_way'):
        with pytest.raises(ValueError,match='pending'):settlement_scores({'market':market},games[0])
    assert status['unresolved_count']==1
    retried,next_status=recover_pending_nhl(games,[row(tip)],[],data_dir=tmp_path,fetcher=Feed(),now=NOW+timedelta(seconds=301))
    assert 'nhl_settlement_pending' not in retried[0].context
    assert settlement_scores(tip,retried[0])[:2]==(5,6)
    assert next_status['unresolved_count']==0


def test_persisted_recovered_games_preserve_exact_outcome_source_after_restart(tmp_path):
    from betting_engine.domain import Game
    tip=observed(4)[2]
    games,_=recover_pending_nhl([],[row(tip)],[],data_dir=tmp_path,fetcher=Feed(),now=NOW)
    path=tmp_path/'games.json';path.write_text(json.dumps([g.dict() for g in games]))
    loaded=[Game(**g) for g in json.loads(path.read_text())]
    feed=Feed(fail={4})
    restored,status=recover_pending_nhl(loaded,[row(tip)],[],data_dir=tmp_path,fetcher=feed,now=NOW+timedelta(seconds=1))
    assert not feed.calls and status['unresolved_count']==0
    assert settlement_scores(tip,restored[0])[2]==settlement_scores(tip,games[0])[2]
