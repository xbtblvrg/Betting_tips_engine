"""Family quality changes current instructions, never frozen issued records."""
from copy import deepcopy
import math

import pytest

from betting_engine.decisions import candidate, rank_and_size
from betting_engine.domain import utcnow
from betting_engine.governance import EvidenceGuard, KnowledgeAgent, evidence_digest, verify_publication
from betting_engine.instructions import current_execution_review, instruction_review
from test_governance import guard_case
from test_execution_instructions import reviewed_tip, issued_record


def guarded(market='moneyline', line=None, regression=False):
    _, g, m, q, f, report, settings=guard_case()
    m.kind=market; m.line=line; f.margin=10
    if regression:
        report['elo_baseline']={'n':2,'brier':.08,'log_loss':.3}
    tip=candidate(g,m,q,f,settings)
    result=EvidenceGuard(KnowledgeAgent()).check(tip,g,m,q,f,report,settings)
    return tip,result,settings


@pytest.mark.parametrize(('market','line'), [('moneyline',None),('spreads',0),('spreads',.5),('spreads',-.5)])
def test_failed_winner_and_payoff_aliases_cannot_be_published(market,line):
    tip,result,settings=guarded(market,line,True)
    assert not result['passed']
    assert 'model_quality:winner_brier_worse_than_elo' in result['errors']
    with pytest.raises(ValueError): verify_publication(tip,utcnow())
    assert not current_execution_review(tip,utcnow(),.03)['actionable']


def test_winner_comparator_does_not_claim_total_failure_or_approval():
    tip,result,settings=guarded('totals',210.5,True)
    assert result['passed']
    quality=tip['input_readiness']['model_quality']
    assert quality['state']=='family_evidence_missing'
    assert not quality['blocks_publication'] and not quality['authorizes_publication']
    assert quality['repair_required']


@pytest.mark.parametrize('mutation', ['remove','foreign_model','failure','flag_only'])
def test_current_publication_requires_complete_bound_quality_receipt(mutation):
    tip=reviewed_tip()
    quality=tip['input_readiness']['model_quality']
    if mutation=='remove':tip['input_readiness'].pop('model_quality')
    elif mutation=='foreign_model':quality['model_version']='different'
    elif mutation=='failure':quality['blocks_publication']=True
    else:tip['input_readiness']['model_quality']={'passed':True}
    # Even a recomputed outer digest cannot replace the independent receipt checks.
    tip['decision_guard']['candidate_sha256']=evidence_digest(tip)
    assert not current_execution_review(tip,utcnow(),.03)['actionable']


def test_pre_quality_original_receipt_stays_frozen_with_current_family_review():
    original=reviewed_tip(cap=1.)
    original['input_readiness'].pop('model_quality')
    original['decision_guard']['candidate_sha256']=evidence_digest(original)
    record=issued_record(original); frozen=deepcopy(record)
    current=reviewed_tip(start=original['start'],held=True)
    review=instruction_review(record,current,utcnow(),.03)
    assert review['actionable'] and review['stake']==1
    assert record==frozen


def test_model_quality_has_no_effect_on_successful_stake_policy():
    tip,result,settings=guarded()
    rank_and_size([tip],settings)
    verify_publication(tip,utcnow())
    assert 1<=tip['stake']<=5
    assert result['passed'] and not result['llm_approval']


def test_in_place_report_change_invalidates_cached_quality_without_version_change():
    args=guard_case();guard=EvidenceGuard(KnowledgeAgent())
    assert guard.check(*args)['passed']
    args[5]['elo_baseline']['brier']=.08
    reviewed=guard.check(*args)
    assert not reviewed['passed']
    assert 'model_quality:winner_brier_worse_than_elo' in reviewed['errors']
    assert args[0]['input_readiness']['model_quality']['elo_brier']==.08


def test_regression_repair_is_durable_and_absence_does_not_resolve_it(tmp_path):
    from datetime import timedelta
    from test_engineering_loop import NOW, evidence, run, registry
    from betting_engine.professional_quality import assess_professional_quality
    report=guard_case()[5]
    report['elo_baseline']={'n':2,'brier':.08,'log_loss':.3}
    quality=assess_professional_quality(report,'moneyline')
    def values(at):
        data=evidence(at)
        data[0]['status']={'model_quality':{'NBA':{'moneyline':quality}}}
        data[0]['models']={'NBA':{'version':'v'}}
        return data
    first=run(tmp_path,values=values(NOW))
    key='model_quality:NBA:full_game:moneyline'
    deadline=registry(tmp_path)['gaps'][key]['due_at']
    assert key in first['task_focus']['D18']['gap_ids']
    run(tmp_path,NOW+timedelta(minutes=1),values=values(NOW+timedelta(minutes=1)))
    run(tmp_path,NOW+timedelta(minutes=2))
    row=registry(tmp_path)['gaps'][key]
    assert row['state']=='open' and row['due_at']==deadline
    report['elo_baseline']={'n':2,'brier':.1,'log_loss':.4}
    quality=assess_professional_quality(report,'moneyline')
    run(tmp_path,NOW+timedelta(minutes=3),values=values(NOW+timedelta(minutes=3)))
    assert registry(tmp_path)['gaps'][key]['state']=='resolved'
