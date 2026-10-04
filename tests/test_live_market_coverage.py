from datetime import datetime, timezone
from betting_engine.live_market_coverage import build_live_coverage, export_markdown, feedback_ids

NOW=datetime(2026,10,4,10,tzinfo=timezone.utc)
TITLE='1st Quarter - Draw No Bet'

def inputs():
    tip={'id':'a','league':'NFL','stake':2,'quote_at':NOW.isoformat(),'start':'2026-10-04T12:00:00Z','valid_until':'2026-10-04T10:05:00Z','stake_market':{'name':TITLE},'execution_review':{'actionable':True}}
    market={'league':'NFL','market':TITLE,'parsed_outcomes':2,'priced_outcomes':2,'guard_passed_outcomes':2,'observed_outcomes':2}
    return {'version':8,'generated_at':NOW.isoformat(),'market_types':[market],'recommendations':[tip]}, {'types':[market]}

def test_exact_evidence_distinguishes_guards_funding_and_feedback():
    pub,cat=inputs()
    decision={'family':'f','tip_id':'a','issued_at':NOW.isoformat(),'status':'won','payload':pub['recommendations'][0]}
    report=build_live_coverage(pub,cat,[decision,decision],['a'],NOW)
    row=report['rows'][0]
    assert row['market']==TITLE and row['scope']=='quarter_1'
    assert row['funding_eligible_now'] and row['settled_and_feedback']
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

def test_funding_support_is_separate_from_marked_or_exposure_blocked(monkeypatch):
    pub,cat=inputs()
    candidate={**pub['recommendations'][0], 'stake':0, 'decision_guard':{'passed':True}, 'input_readiness':{'passed':True}}
    pub['recommendations']=[];pub['evaluated_candidates']=[candidate];pub['settings']={'max_bet':5}
    import betting_engine.funding_policy as policy
    monkeypatch.setattr(policy,'funding_basis',lambda *args:{'eligible':True})
    monkeypatch.setattr(policy,'size_recommendation',lambda *args:{'amount':2})
    row=build_live_coverage(pub,cat,now=NOW)['rows'][0]
    assert row['funding_supported'] and row['qualifying_before_exposure']==1
    assert not row['funding_eligible_now']
    monkeypatch.setattr(policy,'size_recommendation',lambda *args:{'amount':0})
    assert not build_live_coverage(pub,cat,now=NOW)['rows'][0]['funding_supported']


def test_placed_and_started_not_ready(monkeypatch):
    pub,cat=inputs();pub['placement_marks']={'a':NOW.isoformat()}
    assert not build_live_coverage(pub,cat,now=NOW)['rows'][0]['funding_eligible_now']
    pub['placement_marks']={};pub['recommendations'][0]['start']=NOW.isoformat()
    assert not build_live_coverage(pub,cat,now=NOW)['rows'][0]['funding_eligible_now']

def test_held_original_stake_uses_shared_readiness_predicate():
    pub,cat=inputs();tip=pub['recommendations'][0]
    original={**tip,'tracking_source':'auto_assumed_placed'}
    tip['stake']=0;tip['tracked_bet']=True
    pub['evaluated_candidates']=[tip]
    record={'tip_id':'a','stake':2,'status':'open','created_at':NOW.isoformat(),'payload':original,'instruction_review':{'actionable':True}}
    pub['tracked_bets']=[record]
    assert build_live_coverage(pub,cat,now=NOW)['rows'][0]['funding_eligible_now']
    pub['placement_marks']={'a':'yes'}
    assert not build_live_coverage(pub,cat,now=NOW)['rows'][0]['funding_eligible_now']
    pub['placement_marks']={};pub['tracked_bets'].append(record)
    assert not build_live_coverage(pub,cat,now=NOW)['rows'][0]['funding_eligible_now']

def test_ten_stages_require_distinct_evidence_and_full_current_outcome_coverage(monkeypatch):
    pub,cat=inputs();tip=pub['recommendations'][0]
    tip.update(model_scope='quarter_1',model_version='v1',probability=.6,fair_odds=1.67,minimum_odds=1.8,
               expected_roi=.1,stress_roi=.04,market_anchor={'status':'usable'},
               input_readiness={'passed':True},decision_guard={'passed':True},
               family_selection_policy='qualified_probability_v1',allocation_code='selected')
    pub['evaluated_candidates']=[tip];pub['settings']={'max_bet':5}
    pub['market_types'][0].update(active_standard_outcomes=1,parsed_outcomes=1,guard_passed_outcomes=1)
    import betting_engine.funding_policy as policy
    monkeypatch.setattr(policy,'funding_basis',lambda *args:{'eligible':True})
    monkeypatch.setattr(policy,'size_recommendation',lambda *args:{'amount':2,'outcome_adjustment':{'source':{'independent_games':3}}})
    decision={'family':'f','tip_id':'a','issued_at':NOW.isoformat(),'status':'won','payload':tip,
              'assumed_placement_recorded':True,'pnl':1.,'outcome_evidence':{'source':'official','settled_at':NOW.isoformat()}}
    report=build_live_coverage(pub,cat,[decision],['a'],NOW);row=report['rows'][0]
    assert not row['complete_10_of_10'] and len(row['stages'])==10
    assert row['stages']['learning']['state']=='awaiting_measurement'
    assert report['leagues']['NFL']['complete_10_of_10_types']==0
    assert row['learning_detail']['model_promotion_verified'] is False
    pub['market_types'][0]['active_standard_outcomes']=2
    row=build_live_coverage(pub,cat,[decision],['a'],NOW)['rows'][0]
    assert not row['complete_10_of_10'] and row['stages']['data']['state']=='partial'
    # A processed historical result does not prove its use in current decisions.
    monkeypatch.setattr(policy,'size_recommendation',lambda *args:{'amount':2})
    assert build_live_coverage(pub,cat,[decision],['a'],NOW)['rows'][0]['stages']['learning']['state']=='awaiting_measurement'


def test_history_and_catalogue_only_are_not_current_offered_or_settlement_support():
    pub,cat=inputs();tip=pub['recommendations'][0]
    decision={'family':'f','tip_id':'a','issued_at':NOW.isoformat(),'status':'won','payload':tip}
    pub['market_types']=[];pub['evaluated_candidates']=[];pub['recommendations']=[]
    report=build_live_coverage(pub,cat,[decision],['a'],NOW);row=report['rows'][0]
    assert row['inventory_state']=='historical_only' and not row['offered_now']
    assert row['stages']['model']['state']=='not_offered'
    assert row['stages']['settlement']['state']=='not_observed'
    assert report['leagues']['NFL']['offered_types']==0
    assert report['leagues']['NFL']['complete_10_of_10_types']==0


def test_swish_observed_offers_remain_explicit_unverified_and_incomplete():
    pub,cat=inputs();pub['market_types']=[{'league':'NFL','market':'rushing yards','section':'swishMarkets.playerProps','observed_outcomes':8,'active_standard_outcomes':0}]
    row=next(r for r in build_live_coverage(pub,cat,now=NOW)['rows'] if r['section'].startswith('swish'))
    assert not row['offered_now'] and not row['offer_activity_verified']
    assert row['inventory_state']=='observed_activity_unverified'
    assert row['stages']['parsing']['expected']==8 and row['stages']['parsing']['observed']==0
    assert not row['complete_10_of_10']

def test_priced_guard_rejections_do_not_become_guard_or_settlement_support():
    pub,cat=inputs();pub['recommendations']=[]
    pub['market_types'][0].update(active_standard_outcomes=2,guard_checked_outcomes=2,guard_passed_outcomes=0,guard_rejected_outcomes=2)
    row=build_live_coverage(pub,cat,now=NOW)['rows'][0]
    assert row['stages']['pricing']['state']=='verified'
    assert row['stages']['model']['state']=='verified'
    assert row['stages']['guard']['state']=='not_observed'
    assert row['stages']['settlement']['state']=='not_observed'
    pub['market_types'][0]['guard_checked_outcomes']=0
    pub['market_types'][0]['reasons']={'invalid_model_market_probability':2}
    row=build_live_coverage(pub,cat,now=NOW)['rows'][0]
    assert row['stages']['pricing']['state']=='not_observed'


def test_learning_requires_all_four_source_bound_consumers():
    from betting_engine.live_market_coverage import _learning_consumers
    from betting_engine.outcome_sizing import build_snapshot, summary_for
    from betting_engine.calibration_uncertainty import segment_key
    from betting_engine.anchor_learning import effective_weight, segment, VERSION
    tip={'id':'new','game_id':'new','league':'MLB','market':'moneyline','model_scope':'full_game',
         'model_version':'v1','reference_odds':2.,'decision_guard':{'passed':True}}
    historic={**tip,'id':'old','game_id':'old','ownership':'engine','stake':1.,'start':'2026-10-01T10:00:00Z'}
    decision={'family':'old','tip_id':'old','issued_at':'2026-10-01T09:00:00Z','payload':historic,
              'status':'won','pnl':1.,'outcome_evidence':{'settled_at':'2026-10-01T12:00:00Z','source':'https://statsapi.mlb.com/api/v1/schedule','fetched_at':'2026-10-01T11:59:00Z','home_score':1,'away_score':0}}
    summary=summary_for(build_snapshot([decision], '2026-10-03T10:00:00Z'),tip)
    size={'outcome_adjustment':{'source':summary,'summary_sha256':summary['summary_sha256'],
          'snapshot_sha256':summary['snapshot_sha256'],'factor':1.,'adjusted_amount':1.}}
    assert _learning_consumers(tip,size,{})=={'segment':False,'sizing':False,'calibration':False,'weights':False}
    key=segment('MLB','moneyline')
    settings={'_market_weight_snapshot':{'version':VERSION,'snapshot_sha256':'a'*64,
              'weights':{key+'|v1':{'segment':key,'model_weight':.2,'proof_sha256':'b'*64}}}}
    weight,receipt=effective_weight(settings,'MLB','moneyline',None,'v1')
    tip['market_anchor']={'model_weight':weight,'learned_weight':receipt}
    tip['calibration_uncertainty']={'state':'measured','segment':segment_key('MLB','moneyline',None,2.),'snapshot_sha256':'c'*64}
    pub={'settings':settings,'status':{'outcome_sizing':{'snapshot_sha256':summary['snapshot_sha256']},'market_anchor':{'learned_weights':{'snapshot_sha256':'a'*64}},
         'calibration_uncertainty':{'snapshot_sha256':'c'*64}}}
    assert all(_learning_consumers(tip,size,pub).values())
    tip['calibration_uncertainty']['snapshot_sha256']='d'*64
    assert not _learning_consumers(tip,size,pub)['calibration']
    tip['market_anchor']['learned_weight']={**receipt,'proof_sha256':'e'*64}
    assert not _learning_consumers(tip,size,pub)['weights']


def test_learning_complete_requires_every_closed_decision_and_unique_guarded_candidate(monkeypatch):
    import betting_engine.live_market_coverage as coverage
    import betting_engine.funding_policy as policy
    pub,cat=inputs();tip=pub['recommendations'][0]
    tip.update(input_readiness={'passed':True},decision_guard={'passed':True})
    other={**tip,'id':'b'}
    pub.update(settings={'max_bet':5},evaluated_candidates=[tip,tip,other])
    monkeypatch.setattr(policy,'funding_basis',lambda *args:{'eligible':True})
    monkeypatch.setattr(policy,'size_recommendation',lambda *args:{'amount':2})
    monkeypatch.setattr(coverage,'_learning_consumers',lambda t,*args:dict.fromkeys(('segment','sizing','calibration','weights'),t['id']=='a'))
    decisions=[{'family':identity,'tip_id':identity,'issued_at':NOW.isoformat(),'status':'won','payload':{**tip,'id':identity}} for identity in ('a','old')]
    row=build_live_coverage(pub,cat,decisions,['a'],NOW)['rows'][0]
    assert row['learning_detail']['current_all_four_consumers']==1
    assert row['learning_detail']['current_consumers_expected']==2
    assert row['stages']['sizing']['observed']==2
    assert row['stages']['learning']['state']=='partial'
    assert row['stages']['learning']['observed']==2 and row['stages']['learning']['expected']==4
    monkeypatch.setattr(coverage,'_learning_consumers',lambda *args:dict.fromkeys(('segment','sizing','calibration','weights'),True))
    row=build_live_coverage(pub,cat,decisions,['a'],NOW)['rows'][0]
    assert row['stages']['learning']['state']=='partial'
    row=build_live_coverage(pub,cat,decisions,['a','old'],NOW)['rows'][0]
    assert row['stages']['learning']['state']=='verified'
