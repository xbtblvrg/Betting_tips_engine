import copy,json,math
from datetime import timedelta
from types import SimpleNamespace
import joblib,pytest
from betting_engine.domain import utcnow,LEAGUES
from betting_engine.operations import market_types,operating_audit,restore_model,research_dossier
from betting_engine.decisions import DEFAULT_SETTINGS,stake_assessment,rank_and_size
from test_learning import worker

def publication():
    now=utcnow()
    return {'generated_at':now.isoformat(),'settings':DEFAULT_SETTINGS,'models':{l:{'version':'v'} for l in LEAGUES},
        'status':{},'games':[],'recommendations':[],'evaluated_candidates':[],
        'market_types':[{'league':'NHL','section':'standard','market':'1st Period - Total','observed_outcomes':2,'supported_outcomes':0}],
        'opportunity_inventory':{'fixtures':[{'league':'NHL','start':(now+timedelta(hours=1)).isoformat(),'active':True,'matched':True,'odds_loaded':True}],
            'schedule_counts':{l:1 for l in (*LEAGUES,'MLS')},'issues':[]}}

def test_all_leagues_are_checked_without_inventing_games_or_supported_market_models():
    p=publication();a=operating_audit(p,{'shortfall':9})
    assert {r['league'] for r in a['leagues']}==set(LEAGUES)|{'MLS'}
    nba=next(r for r in a['leagues'] if r['league']=='NBA')
    assert nba['state']=='no_games_in_horizon' and nba['modeled_outcomes']==0
    assert a['gaps'][0]['code']=='market_coverage' and a['gaps'][0]['action']=='market_research'
    assert not a['full_market_coverage'] and not a['code_self_modification']

def test_source_failure_is_not_an_empty_calendar_and_repair_is_durable_and_deduplicated(tmp_path):
    w=worker(tmp_path);p=publication();p['opportunity_inventory']['schedule_counts'].pop('NBA');w.store.publish(p)
    w.plan_operations();w.plan_operations()
    jobs=[j for j in w.snapshot()['jobs'] if j['kind']=='source_refresh'];assert len(jobs)==1
    w.execute(jobs[0]);request=w.store.pending_source_refresh();assert request
    # Completing the request is not successful source recovery.
    w.plan_operations()
    with w.store.db() as c:assert c.execute("SELECT status FROM operating_gaps WHERE id='NBA:source_failure'").fetchone()['status']=='repair_pending'
    w.store.acknowledge_source_refresh({**request,'reason':'different request'});assert w.store.pending_source_refresh()
    w.store.acknowledge_source_refresh(request);assert w.store.pending_source_refresh() is None
    p['opportunity_inventory']['schedule_counts']['NBA']=0;w.store.publish(p);w.plan_operations()
    with w.store.db() as c:assert c.execute("SELECT status FROM operating_gaps WHERE id='NBA:source_failure'").fetchone()['status']=='resolved'

def test_disappearing_market_gap_is_not_claimed_implemented_or_fixed(tmp_path):
    w=worker(tmp_path);p=publication();w.store.publish(p);w.plan_operations()
    p['market_types']=[];w.store.publish(p);w.plan_operations()
    with w.store.db() as c:
        r=c.execute("SELECT * FROM operating_gaps WHERE id='NHL:market_coverage'").fetchone()
        assert r['status']=='not_currently_observed' and r['resolved_at'] is None

def test_new_market_schema_reopens_research_without_repeating_unchanged_work(tmp_path):
    w=worker(tmp_path);p=publication();w.store.publish(p);w.plan_operations()
    with w.store.db() as c:c.execute("UPDATE learning_jobs SET status='completed' WHERE kind='market_research'")
    w.plan_operations();assert len([j for j in w.snapshot()['jobs'] if j['kind']=='market_research'])==1
    p['market_types'].append({**p['market_types'][0],'market':'Player shots'})
    w.store.publish(p);w.plan_operations();w.plan_operations()
    assert len([j for j in w.snapshot()['jobs'] if j['kind']=='market_research'])==2

def test_expired_snapshot_cannot_clear_an_existing_gap(tmp_path):
    w=worker(tmp_path);p=publication();w.store.publish(p);w.plan_operations()
    p['market_types']=[];p['generated_at']=(utcnow()-timedelta(minutes=6)).isoformat();w.store.publish(p);w.plan_operations()
    with w.store.db() as c:assert c.execute("SELECT status FROM operating_gaps WHERE id='NHL:market_coverage'").fetchone()['status']=='needs_implementation'
    assert json.loads((w.data/'operations.json').read_text())['state']=='awaiting_fresh_publication'

def test_recovery_restores_exact_deployed_archive_not_a_newer_rejected_model(tmp_path):
    folder=tmp_path/'models';folder.mkdir();target=folder/'NBA.joblib';target.write_bytes(b'damaged')
    report={'version':'approved','league':'NBA'};(folder/'NBA.json').write_text(json.dumps(report))
    for name,r in [('old',report),('new',{'version':'rejected','league':'NBA'})]:
        archive=folder/'versions'/name;archive.mkdir(parents=True);joblib.dump(SimpleNamespace(report=r),archive/'model.joblib')
    result=restore_model('NBA',tmp_path)
    assert result['restored'] and joblib.load(target).report==report
    assert list((folder/'quarantine').glob('*.joblib'))[0].read_bytes()==b'damaged'
    assert not restore_model('NBA',tmp_path)['restored']

def test_one_broken_model_does_not_stop_other_leagues(tmp_path,monkeypatch):
    from betting_engine import engine
    monkeypatch.setattr(engine,'DATA',tmp_path);folder=tmp_path/'models';folder.mkdir()
    (folder/'NBA.joblib').write_bytes(b'damaged');joblib.dump(SimpleNamespace(report={'version':'NHL-ok'}),folder/'NHL.joblib')
    w=worker(tmp_path/'worker');e=engine.Engine(w.store);e.load_models()
    assert e.models['NHL'].report['version']=='NHL-ok' and 'NBA' in e.model_load_errors

def test_market_catalog_counts_exact_names_and_never_counts_swish_as_modeled():
    inventory=[{'league':'NHL','market':'Total','outcome_count':2,'supported_outcomes':2,'reason':'supported'},
        {'league':'NHL','market':'Total','outcome_count':3,'supported_outcomes':0,'provider_section':'swishMarkets.playerProps','reason':'unverified'}]
    rows=market_types(inventory,[{'league':'NHL','stake_evidence':{'market_name':'Total'}}])
    assert rows[0]['evaluated_outcomes']==1 and rows[1]['evaluated_outcomes']==0


def test_market_catalog_never_hides_an_observed_unpriced_family_without_reason():
    rows=market_types([{'league':'NFL','market':'2nd Quarter - Draw No Bet','outcome_count':2,
                        'supported_outcomes':2,'reason':'supported'}],[])
    assert rows[0]['reasons']=={'no_candidate_evaluation':2}
    assert 'fresh guarded candidate evaluation' in rows[0]['next_step']


def test_market_catalog_exposes_every_parsed_contract_decision_state():
    from betting_engine.markets import Market, Quote
    market = Market(id='stake:m:1', game_id='NHL:g', league='NHL', kind='period_1:three_way',
                    side='home', line=None, selection='Home', platform='Stake.com',
                    url='https://stake.com/sports/ice-hockey/usa/nhl/g', token='1',
                    condition_id='m', description='1st Period - 1x2', fee_rate=0.,
                    fee_exponent=1., minimum_notional=0., market_start=(utcnow()+timedelta(hours=1)).isoformat(),
                    fetched_at=utcnow().isoformat(), metadata={'stake_market_name':'1st Period - 1x2'})
    quote = Quote(market.id, '1', 2., 0., .5, 10., 0., utcnow().isoformat(), utcnow().isoformat())
    rows = market_types(
        [{'league':'NHL','market':'1st Period - 1x2','outcome_count':3,
          'supported_outcomes':3,'reason':'supported'}],
        [], [market], {market.id: quote},
        [{'market_id':market.id,'league':'NHL','market_name':'1st Period - 1x2',
          'state':'guard_rejected','reasons':['segment_market_not_validated']}],
    )
    row = rows[0]
    assert row['parsed_outcomes'] == row['quoted_outcomes'] == 1
    assert row['guard_checked_outcomes'] == row['guard_rejected_outcomes'] == 1
    assert row['analysis_status'] == 'guard_rejected'
    assert row['decision_chain_status'] == 'guard_rejected'
    assert row['reasons']['segment_market_not_validated'] == 1

def test_dossier_executes_research_but_does_not_claim_market_implementation(tmp_path):
    result=research_dossier('NHL',publication(),tmp_path)
    assert result['knowledge'] and result['market_types'] and not result['missing_market_models_resolved']
    assert not result['automatic_implementation'] and (tmp_path/'research/leagues/NHL/latest.json').exists()

def test_dynamic_stakes_are_price_sensitive_bounded_and_discount_related_contracts():
    s=DEFAULT_SETTINGS
    weak=stake_assessment(.55,0,2,s,math.inf)
    strong=stake_assessment(.65,0,2,s,math.inf)
    related=stake_assessment(.65,0,2,s,math.inf,8)
    assert weak['amount']==2.23 and strong['amount']==3.87
    assert 1<=related['amount']<strong['amount']<=5
    assert stake_assessment(.9,0,10,s,math.inf)['amount']==5
    assert stake_assessment(.4,0,2,s,math.inf)['amount']==0
    assert stake_assessment(.65,0,2,{**s,'bankroll':20},math.inf)['amount']==0
    assert not weak['personalized'] and weak['bankroll'] is None
    assert stake_assessment(.55,.1,2,s,math.inf)['full_kelly_fraction']==pytest.approx((.55/.9*2-1))

def test_sizing_is_reproduced_before_funded_publication():
    from test_governance import guard_case
    from betting_engine.governance import KnowledgeAgent,EvidenceGuard,verify_publication
    args=guard_case();t=args[0];EvidenceGuard(KnowledgeAgent()).check(*args)
    t['stake_sizing']=stake_assessment(t['stress_probability'],t['push_probability'],t['reference_odds'],args[-1],math.inf)
    t['stake']=t['stake_sizing']['amount'];verify_publication(t,utcnow())
    t['stake']+=.01
    with pytest.raises(ValueError,match='does not reproduce'):verify_publication(t,utcnow())

def test_held_instruction_is_not_resized_or_reissued():
    row={'id':'held','game_id':'g','market':'moneyline','stress_roi':.3,'probability':.68,'stress_probability':.65,'push_probability':0,'reference_odds':2,'minimum_odds':1.7,'stake_sizing':{'amount':1}}
    chosen,_=rank_and_size([row],DEFAULT_SETTINGS,held_contracts={'held'},held_event_counts={'g':1})
    assert chosen[0]['stake']==0 and chosen[0]['allocation_code']=='already_held' and 'stake_sizing' not in chosen[0]


def test_start_conflict_retains_specific_cause_and_repair_route():
    from betting_engine.engineering_loop import _route
    p=publication();f=p['opportunity_inventory']['fixtures'][0]
    f.update(fixture_id='f',active=False,start_conflict={'sports_start':f['start'],'stake_start':'conflicting'})
    audit=operating_audit(p,{'shortfall':0})
    assert audit['fixtures'][0]['state']=='fixture_start_mismatch'
    gap=next(g for g in audit['gaps'] if g['code']=='fixture_start_mismatch')
    assert gap['action']=='source_refresh' and gap['fixtures'][0]['fixture_id']=='f'
    assert _route(gap['code'])=='source'
