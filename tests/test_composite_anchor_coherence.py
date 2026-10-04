from copy import deepcopy
import numpy as np
import pytest

from betting_engine.composite_contracts import receipt,anchored_partition
from betting_engine.decisions import candidate,rank_and_size
from betting_engine.domain import utcnow
from betting_engine.governance import EvidenceGuard,KnowledgeAgent,verify_publication
from betting_engine.market_anchor import contract_anchors
from betting_engine.models import feature_names
from test_complete_composite_policy import case


def model_report(g,f):
    return {'league':g.league,'version':f.model_version,'odds_used':False,'features':list(feature_names(g.league)),
        'trained_at':utcnow().isoformat(),'code_sha256':'test','training_data_fingerprint':'test'}


def attach_source(t,m):
    t['stake_evidence']={'market_groups':m.metadata['stake_market_groups'],'league_path':m.metadata['league_path'],
        'market_name':m.description,'outcome_name':m.selection,'fixture_slug':m.metadata['fixture_slug']}


@pytest.mark.parametrize('league',['WNBA','MLB'])
@pytest.mark.parametrize('endpoints',[False,True])
def test_live_complete_partition_normalized_and_guard_reproduces(league,endpoints):
    _,g,ms,qs,f,s=case(league);s['market_anchor_mode']='live'
    if endpoints:
        s['market_disagreement_threshold']=.999
        f.residuals=np.zeros((1,2));f.home_probability=1.;f.away_probability=0.
        if league=='WNBA':f.margin=20.;f.total=170.
    anchors=contract_anchors(ms,qs);final=[]
    raw=[f.market_probability(m.kind,m.side,m.line)[0] for m in ms]
    if endpoints:assert 0. in raw and 1. in raw
    guard=EvidenceGuard(KnowledgeAgent())
    for m in ms:
        t=candidate(g,m,qs[m.id],f,s,anchors[m.id]);assert t is not None
        attach_source(t,m);final.append(t['probability'])
        a=t['market_anchor'];partition=a['composite_blend']
        assert sum(partition['probabilities'].values())==pytest.approx(1,abs=1e-15)
        assert sum(partition['market_probabilities'].values())==pytest.approx(1,abs=1e-15)
        assert t['probability']==partition['probabilities'][m.side]
        assert t['push_probability']==0.
        result=guard.check(t,g,m,qs[m.id],f,model_report(g,f),s)
        if league=='WNBA':
            assert result['passed'],result['errors']
            rank_and_size([t],s);verify_publication(t,utcnow(),s)
        else:
            assert result['errors'] and all(e.startswith('input_readiness:') for e in result['errors'])
    assert sum(final)==pytest.approx(1,abs=1e-15)


@pytest.mark.parametrize('change',['selected_fair','full_fair','source_identity','quote_time','conditional'])
def test_source_anchor_forgery_rejected_by_candidate_and_guard(change):
    _,g,ms,qs,f,s=case();s['market_anchor_mode']='live';m=ms[0]
    anchor=contract_anchors(ms,qs)[m.id]
    t=candidate(g,m,qs[m.id],f,s,anchor);attach_source(t,m)
    forged=deepcopy(anchor)
    if change=='selected_fair':forged['market_probability']=.49
    elif change=='full_fair':forged['market_probabilities'][m.side]+=.01
    elif change=='source_identity':forged['market_anchor_key'][1]='other-source'
    elif change=='quote_time':forged['quote_at']='2000-01-01T00:00:00Z'
    else:forged['conditional_push_free']=True
    with pytest.raises(ValueError,match='complete source'):
        candidate(g,m,qs[m.id],f,s,forged)
    t['market_anchor'].update(forged)
    result=EvidenceGuard(KnowledgeAgent()).check(t,g,m,qs[m.id],f,model_report(g,f),s)
    assert not result['passed'] and 'invalid_numeric_contract' in result['errors']


def test_guard_rejects_forged_normalized_partition_receipt():
    _,g,ms,qs,f,s=case();s['market_anchor_mode']='live';m=ms[0]
    t=candidate(g,m,qs[m.id],f,s,contract_anchors(ms,qs)[m.id]);attach_source(t,m)
    t['market_anchor']['composite_blend']['probabilities'][m.side]+=.01
    result=EvidenceGuard(KnowledgeAgent()).check(t,g,m,qs[m.id],f,model_report(g,f),s)
    assert not result['passed'] and 'invalid_numeric_contract' in result['errors']


def test_shadow_preserves_independent_probabilities_and_common_review_status():
    _,g,ms,qs,f,s=case();s.update(market_anchor_mode='shadow',market_disagreement_threshold=.01)
    anchors=contract_anchors(ms,qs)
    rows=[candidate(g,m,qs[m.id],f,s,anchors[m.id]) for m in ms]
    assert all(t['market_anchor']['status']=='review' for t in rows)
    assert sum(t['probability'] for t in rows)==pytest.approx(1.)
    for t,m in zip(rows,ms):assert t['probability']==f.market_probability(m.kind,m.side,m.line)[0]


def test_model_only_weight_retains_exact_partition_endpoints():
    _,g,ms,qs,f,s=case();s.update(market_model_weight=1.,market_disagreement_threshold=.999)
    f.home_probability=1.;f.away_probability=0.;f.margin=20.;f.total=170.;f.residuals=np.zeros((1,2))
    m=ms[0];proof=receipt(g,m,f,s)
    result=anchored_partition(proof,m,s,contract_anchors(ms,qs)[m.id],qs[m.id].fetched_at)
    assert result['composite_blend']['probabilities']==proof['probabilities']
