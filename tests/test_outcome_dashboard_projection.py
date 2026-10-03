"""Projection happens after review and cannot mutate original sizing evidence."""
from copy import deepcopy
import json

import pytest

from betting_engine.dashboard_payload import _compact_dashboard_tip, build_dashboard
from betting_engine.governance import verify_publication
from betting_engine.domain import utcnow
from betting_engine.outcome_sizing import verify_summary
from test_outcome_sizing_integration import make_tip, history, insert
import test_continuous_delivery as cases


def test_eligible_zero_stake_research_no_longer_bypasses_compaction():
    row={'id':'research','stake':0,'outcome_sizing_policy':'historical_game_weighted_v1',
         'family_selection_policy':'qualified_probability_v1',
         'execution_review':{'actionable':True,'state':'current','minimum_odds':1.5,
              'outcome_sizing_policy':'historical_game_weighted_v1'},
         'decision_guard':{'passed':True,'knowledge':[{'raw':'x'*100000}],
              'input_readiness':{'raw':'x'*100000},'outcome_sizing_policy':'historical_game_weighted_v1'},
         'stake_sizing':{'outcome_adjustment':{'source':{'raw':'x'*100000}}},
         'game_context':{'raw':'x'*100000},'input_readiness':{'raw':'x'*100000}}
    before=deepcopy(row)
    compact=_compact_dashboard_tip(row)
    assert len(json.dumps(compact))<2000
    assert 'stake_sizing' not in compact
    assert compact['execution_review']['actionable']
    assert compact['execution_review']['minimum_odds']==1.5
    assert compact['decision_guard']['outcome_sizing_policy']==row['outcome_sizing_policy']
    assert compact['outcome_sizing_policy']==row['outcome_sizing_policy']
    assert row==before


def test_funded_trace_retains_aggregates_policies_and_browser_contract(make_tip):
    tip,settings,check=make_tip(history(),probability=.65,odds=1.4)
    assert check['passed']
    before=deepcopy(tip)
    projected=_compact_dashboard_tip(tip)
    assert projected['stake']==tip['stake'] and projected['funding_basis']==tip['funding_basis']
    assert projected['stake_evidence']==tip['stake_evidence']
    assert projected['decision_guard']['passed'] is True
    assert projected['decision_guard']['outcome_sizing_policy']==settings['outcome_sizing_policy']
    evidence=projected['outcome_sizing_evidence']
    assert 'games' not in evidence and evidence['evidence_projection']=='dashboard_aggregate_v1'
    trace=projected['stake_sizing']['outcome_adjustment']
    for key in ('base_amount','factor','adjusted_amount','caps','segment','summary_sha256'):
        assert trace[key]==tip['stake_sizing']['outcome_adjustment'][key]
    for key in ('independent_games','game_wr','game_roi','break_even_wr','as_of','summary_sha256'):
        assert trace['source'][key]==tip['stake_sizing']['outcome_adjustment']['source'][key]
    assert 'games' not in trace['source']
    assert not verify_summary(evidence)  # Display summary is never a replay receipt.
    assert tip==before and verify_summary(tip['outcome_sizing_evidence'])
    verify_publication(tip,utcnow(),settings)


def test_dashboard_reviews_full_evidence_then_projects_without_ledger_writes(make_tip,tmp_path):
    rows=history()
    tip,settings,check=make_tip(rows,probability=.65,odds=1.4)
    assert check['passed']
    store=cases.database(tmp_path,settings)
    insert(store,rows)
    store.publish({'recommendations':[tip],'evaluated_candidates':[tip],
                   'settings':settings,'games':[],'models':{},'status':{},
                   'issued_contracts':[{'id':tip['id'],'stake':tip['stake']}]},require_guard=True)
    originals=deepcopy(store.system_decisions())
    full_publication=deepcopy(store.latest())
    dashboard=build_dashboard(store,{},tmp_path)
    current=dashboard['evaluated_candidates'][0]
    assert current['execution_review']['actionable']
    assert len(dashboard['tracked_bets'])==1
    assert dashboard['tracked_bets'][0]['instruction_review']['actionable']
    assert dashboard['status']['execution_counts']['current_executable']==1
    assert 'games' not in current['outcome_sizing_evidence']
    assert 'games' not in dashboard['tracked_bets'][0]['payload']['outcome_sizing_evidence']
    assert dashboard['stake_reassessments'][0]['eligible']
    assert 'games' not in dashboard['stake_reassessments'][0]['sizing']['outcome_adjustment']['source']
    assert store.system_decisions()==originals and store.latest()==full_publication
    # The source, including per-game lineage, remains available to later reviews.
    stored=store.latest()['evaluated_candidates'][0]
    verify_publication(stored,utcnow(),settings)
    assert stored['outcome_sizing_evidence']['games']


def test_research_projection_preserves_all_funding_gate_fields(make_tip):
    tip,settings,_=make_tip(history(),probability=.65,odds=1.4)
    tip['stake']=0
    tip['execution_review']={'actionable':True,'state':'current',
        'outcome_sizing_policy':settings['outcome_sizing_policy']}
    projected=_compact_dashboard_tip(tip)
    for key,value in tip['funding_basis'].items():
        if key not in ('note','claim'):assert projected['funding_basis'][key]==value
    assert 'stake_sizing' not in projected
    assert projected['outcome_sizing_evidence']['summary_sha256']==tip['outcome_sizing_evidence']['summary_sha256']
