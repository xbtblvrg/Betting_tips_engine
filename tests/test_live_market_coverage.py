from datetime import datetime, timezone
from betting_engine.live_market_coverage import build_live_coverage, export_markdown, feedback_ids

NOW=datetime(2026,10,4,10,tzinfo=timezone.utc)
TITLE='1st Quarter - Draw No Bet'

def inputs():
    tip={'id':'a','league':'NFL','stake':2,'quote_at':NOW.isoformat(),'stake_market':{'name':TITLE},'execution_review':{'actionable':True}}
    market={'league':'NFL','market':TITLE,'parsed_outcomes':2,'priced_outcomes':2,'guard_passed_outcomes':2,'observed_outcomes':2}
    return {'version':8,'generated_at':NOW.isoformat(),'market_types':[market],'recommendations':[tip]}, {'types':[market]}

def test_exact_evidence_distinguishes_guards_funding_and_feedback():
    pub,cat=inputs()
    decision={'family':'f','tip_id':'a','issued_at':NOW.isoformat(),'status':'won','payload':pub['recommendations'][0]}
    report=build_live_coverage(pub,cat,[decision,decision],['a'],NOW)
    row=report['rows'][0]
    assert row['market']==TITLE and row['scope']=='quarter_1'
    assert row['funding_eligible'] and row['settled_and_feedback']
    assert row['issued_today']==1
    pub['recommendations'][0]['execution_review']['actionable']=False
    row=build_live_coverage(pub,cat,[decision],[],NOW)['rows'][0]
    assert row['guard_passed_outcomes']==2 and not row['funding_eligible']
    assert row['settled_decisions']==1 and not row['settled_and_feedback']

def test_stale_and_catalogue_only_never_become_live_capability(tmp_path):
    pub,cat=inputs();pub['generated_at']='2026-10-04T09:00:00Z'
    cat['types'].append({'league':'NHL','market':'1st Goal'})
    report=build_live_coverage(pub,cat,now=NOW)
    assert all(not r['connected'] and not r['evaluated'] and not r['funding_eligible'] for r in report['rows'])
    assert report['leagues']['NHL']['observed_types']==1
    export_markdown(report,tmp_path/'coverage.md')
    assert TITLE in (tmp_path/'coverage.md').read_text()

def test_closed_push_and_open_decisions_are_distinct():
    pub,cat=inputs()
    rows=[{'family':str(i),'tip_id':str(i),'issued_at':'2026-10-03T22:30:00Z','status':status,'payload':pub['recommendations'][0]} for i,status in enumerate(['push','open'])]
    row=build_live_coverage(pub,cat,rows,['0'],NOW)['rows'][0]
    assert row['issued_today']==2 and row['settled_decisions']==1 and row['settled_and_feedback']

def test_feedback_hash_failure_fails_closed(tmp_path):
    import json
    p=tmp_path/'outcome_feedback';p.mkdir()
    (p/'latest.json').write_text(json.dumps({'latest_review':{'path':'../bad','sha256':'a'*64}}))
    ids,error=feedback_ids(tmp_path)
    assert ids==[] and 'Invalid' in error
