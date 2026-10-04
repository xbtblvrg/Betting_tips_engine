from datetime import datetime, timezone, timedelta
import sqlite3
import json
from betting_engine.flow_checker import assess_flow, read_lifecycle, check_live_flow, LEAGUES

NOW=datetime(2026,10,4,10,tzinfo=timezone.utc)

def snapshot():
    return {'generated_at':NOW.isoformat(),'version':1,'status':{'daily_progress':{'date':'2026-10-04','by_league':{'NFL':40,'MLB':60}},'outcome_sizing':{'accepted_decisions':10}},
      'opportunities':{'windows':{'48':{'leagues':[{'league':l,'source_state':'observed','games':1,'matched_games':1,'odds_loaded_games':1,'source_errors':[]} for l in LEAGUES]}}},
      'market_types':[{'league':'NFL','market':'Winner (Incl. Overtime)','scope':'full_game','observed_outcomes':2,'evaluated_outcomes':2,'guard_checked_outcomes':2,'guard_passed_outcomes':2}]}

def rows(result):return {r['id']:r for r in result['checks']}

def test_source_zero_is_not_missing_source():
    p=snapshot();r=rows(assess_flow(p,{}, {},NOW));assert r['1.1']['passed']
    p['opportunities']['windows']['48']['leagues'].pop()
    assert not rows(assess_flow(p,{}, {},NOW))['1.1']['passed']

def test_unsupported_props_and_scope_are_explicit_failures():
    p=snapshot();p['market_types'].append({'league':'NFL','market':'Passing Yards','scope':'player','observed_outcomes':2,'evaluated_outcomes':0})
    r=rows(assess_flow(p,{}, {},NOW));assert not r['1.2']['passed']
    assert r['1.2']['evidence']['examples'][0]['market']=='Passing Yards'

def test_daily_only_main_leagues_and_no_padding():
    p=snapshot();p['status']['daily_progress']['by_league']={'NFL':9,'NCAAF':1000}
    r=rows(assess_flow(p,{}, {},NOW))['1.4'];assert r['evidence']['issued']==9 and not r['passed'] and r['evidence']['behind_pace']

def test_new_publications_not_polls_count_consecutive():
    p=snapshot();first=assess_flow(p,{}, {},NOW);second=assess_flow(p,{},first,NOW)
    assert rows(second)['1.1']['consecutive_publications']==1
    p['generated_at']=(NOW+timedelta(seconds=60)).isoformat()
    third=assess_flow(p,{},second,NOW+timedelta(seconds=60));assert rows(third)['1.1']['consecutive_publications']==2

def test_stale_publication_never_fulfills_any_check():
    p=snapshot();r=assess_flow(p,{}, {},NOW+timedelta(minutes=6));assert all(not x['passed'] for x in r['checks'])

def test_only_actual_active_task_is_under_repair():
    r=assess_flow({}, {'development':{'progress':{'active_tasks':['D36']}},'flow_repair_assignments':{'1.2':'D36'}},{},NOW)
    assert rows(r)['1.2']['status']=='under_repair' and rows(r)['1.5']['status']=='not_fulfilled'

def test_catalogue_requires_feedback_and_same_publication():
    p=snapshot();p['automation']={'market_catalog':{'live_coverage':{'publication_at':p['generated_at'],'checked_at':p['generated_at'],'publication_fresh':True,'rows':[{'league':'NFL','section':'standard','market':'Winner (Incl. Overtime)','scope':'full_game','connected':True,'evaluated':True,'funding_eligible':True,'settled_and_feedback':False}]}}}
    assert not rows(assess_flow(p,{}, {},NOW))['2']['passed']
    p['automation']['market_catalog']['live_coverage']['rows'][0]['settled_and_feedback']=True
    assert rows(assess_flow(p,{}, {},NOW))['2']['passed']

def test_missing_probe_is_not_success(tmp_path):
    r=check_live_flow(tmp_path,{}, {},NOW,lambda: (_ for _ in ()).throw(OSError('down')))
    assert r['failure_count']==10 and 'OSError' in r['probe_error']

def dbfixture(tmp_path):
    path=tmp_path/'engine.sqlite3';db=sqlite3.connect(path)
    db.executescript('CREATE TABLE system_decisions(family TEXT,tip_id TEXT,payload TEXT,status TEXT,pnl REAL,outcome_evidence TEXT); CREATE TABLE bets(id INTEGER,tip_id TEXT,status TEXT,stake REAL); CREATE TABLE bet_outcomes(bet_id INTEGER,result TEXT,brier REAL,log_loss REAL);')
    return path,db

def test_lifecycle_readonly_missing_assumed_and_old_open(tmp_path):
    path,db=dbfixture(tmp_path);db.execute('INSERT INTO system_decisions VALUES (?,?,?,?,?,?)',('f','t',json.dumps({'stake':2,'start':'2026-10-01T00:00:00Z'}),'open',None,None));db.commit();db.close()
    before=path.read_bytes();r=read_lifecycle(tmp_path,NOW)
    assert r['verified'] and r['missing_tickets']==1 and r['unsettled_after_12h']==1
    assert before==path.read_bytes()

def test_lifecycle_missing_numeric_or_closing_not_complete(tmp_path):
    path,db=dbfixture(tmp_path);db.execute('INSERT INTO system_decisions VALUES (?,?,?,?,?,?)',('f','t',json.dumps({'stake':2,'start':'2026-10-01T00:00:00Z'}),'won',2,'{}'));db.execute('INSERT INTO bets VALUES (1,"t","won",2)');db.execute('INSERT INTO bet_outcomes VALUES (1,"won",NULL,NULL)');db.commit();db.close()
    r=read_lifecycle(tmp_path,NOW);assert r['missing_numeric_metrics']==1 and not r['metrics_complete'] and not r['closing_complete']

def test_unrelated_running_task_does_not_claim_repair():
    r=assess_flow({}, {'development':{'progress':{'active_tasks':['D36']}}},{},NOW)
    assert all(x['status']!='under_repair' for x in r['checks'])

def test_catalogue_identity_omission_is_not_complete():
    p=snapshot();p['automation']={'market_catalog':{'live_coverage':{'publication_at':p['generated_at'],'checked_at':p['generated_at'],'publication_fresh':True,'rows':[{'league':'NFL','market':'Wrong market','scope':'full_game','connected':True,'evaluated':True,'funding_eligible':True,'settled_and_feedback':True}]}}}
    assert not rows(assess_flow(p,{}, {},NOW))['2']['passed']

def test_required_service_missing_fails():
    p=snapshot();r=rows(assess_flow(p,{'services':{'engine':{'state':'healthy'}}},{},NOW))
    assert not r['1.6']['passed']

def test_single_trial_is_not_paired_improvement():
    from betting_engine.flow_checker import prospective_comparison
    result=prospective_comparison({'automation':{'shadow_evidence':{'prospective_trials':[{'state':'promoted'}]}}})
    assert result['state']=='paired_live_comparison_source_missing'


def test_yesterdays_daily_count_does_not_meet_target():
    p=snapshot();p['status']['daily_progress']['date']='2026-10-03'
    assert not rows(assess_flow(p,{}, {},NOW))['1.4']['passed']


def test_guard_pass_does_not_prove_learned_anchor_or_quantified_inputs():
    p=snapshot();r=rows(assess_flow(p,{}, {},NOW))['1.3']
    assert not r['passed']
    assert set(r['evidence']['diagnostic_gaps'])=={'per_segment_learned_anchor_not_active','required_input_effects_not_measured'}


def test_catalogue_additional_history_is_not_missing_current_coverage():
    p=snapshot(); row={'league':'NFL','section':'standard','market':'Winner (Incl. Overtime)','scope':'full_game','connected':True,'evaluated':True,'funding_eligible':True,'settled_and_feedback':True}
    p['automation']={'market_catalog':{'live_coverage':{'publication_at':p['generated_at'],'checked_at':p['generated_at'],'publication_fresh':True,'rows':[row,{**row,'market':'Total (Incl. Overtime)'}]}}}
    assert rows(assess_flow(p,{}, {},NOW))['2']['passed']
    p['automation']['market_catalog']['live_coverage']['rows'].append(row)
    assert not rows(assess_flow(p,{}, {},NOW))['2']['passed']


def test_priced_but_guard_rejected_outcome_is_not_missing_model():
    p=snapshot(); p['market_types'][0].update(evaluated_outcomes=1,priced_outcomes=2,guard_checked_outcomes=2,guard_passed_outcomes=1)
    r=rows(assess_flow(p,{}, {},NOW))
    assert r['1.2']['passed'] and not r['1.3']['passed']
