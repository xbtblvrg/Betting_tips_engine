from datetime import timedelta
from betting_engine.domain import utcnow
from betting_engine.component_health import audit_components
from betting_engine.operations import operating_audit
from test_operations import publication

def test_each_unrecommended_fixture_has_cause_and_durable_research_request():
    p=publication();f=p['opportunity_inventory']['fixtures'][0]
    f.update(fixture_id='f',game_id='NHL:g',name='Away - Home',counts=[{'supported':2}])
    now=utcnow();p['evaluated_candidates']=[{'league':'NHL','game_id':'NHL:g','quote_at':now.isoformat(),
        'valid_until':(now+timedelta(minutes=3)).isoformat(),'start':f['start'],'stress_roi':-.1}]
    a=operating_audit(p,{'shortfall':3},now)
    assert a['fixtures'][0]['state']=='no_qualifying_edge'
    assert any(g['code']=='fixtures_without_recommendation' for g in a['gaps'])
    assert not p['recommendations']
    p['issued_contracts']=[{'game_id':'NHL:g'}]
    a=operating_audit(p,{'shortfall':3},now)
    assert a['fixtures'][0]['issued_contracts']==1
    assert not any(g['code']=='fixtures_without_recommendation' for g in a['gaps'])

def test_unknown_ncaa_sport_and_registry_failure_stay_visible_without_fabricated_model():
    p=publication();f=p['opportunity_inventory']['fixtures'][0];f.update(league='NCAA_VOLLEYBALL',fixture_id='v',matched=False)
    p['opportunity_inventory']['schedule_counts']['NCAA_VOLLEYBALL']=1
    p['opportunity_inventory']['issues']=[{'league':'NCAA_DISCOVERY','reason':'stake_sports_registry_error'}]
    a=operating_audit(p,{'shortfall':10})
    assert any(g['league']=='NCAA_VOLLEYBALL' and g['code']=='league_not_modeled' for g in a['gaps'])
    assert any(g['league']=='NCAA_DISCOVERY' for g in a['gaps'])
    assert a['fixtures'][0]['state']=='unmatched_fixture'

def test_fresh_heartbeat_does_not_hide_stale_publication_or_missing_archive():
    now=utcnow();p=publication();p['generated_at']=(now-timedelta(minutes=10)).isoformat()
    p['api_audit']={'fixtures':[{'responses':{'odds':{'sha256':'hash'}}}]}
    p['status']['governance']={'guard':{'checked':2,'passed':2,'rejected':0}}
    result=audit_components(p,{'checked_at':now.isoformat(),'counts':{}},{},{},now)
    by={r['component']:r for r in result['checks']}
    assert by['Learning worker']['state']=='healthy'
    assert by['Recommendation publication']['state']=='stale'
    assert by['Stake fixture endpoints']['state']=='degraded'
    assert result['state']=='degraded' and not result['code_self_modification']

def test_partial_fixture_endpoint_and_missing_sports_input_trigger_different_repairs():
    from betting_engine.feed_health import market_feed_health
    assert market_feed_health({'quote':1},[{'reason':'stake_fixture_endpoint_error'}])['state']=='partial'
    p=publication();p['status']['data_sources']=[{'source':'NCAAF/current','ok':False},
        {'source':'NCAAF/injuries','ok':False,'state':'not_integrated'}]
    gaps=operating_audit(p,{'shortfall':1})['gaps']
    assert any(g['code']=='independent_source_failure' and g['action']=='source_refresh' for g in gaps)
    assert any(g['code']=='independent_input_missing' and g['action']=='market_research' for g in gaps)
