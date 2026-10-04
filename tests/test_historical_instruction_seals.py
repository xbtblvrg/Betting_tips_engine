"""Frozen original receipts from the 2026-10-02 issuance remain verifiable."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from betting_engine.domain import parse_time
from betting_engine.instructions import _original_budget, current_execution_review

RECORDS = json.loads((Path(__file__).parent/'fixtures/pre_anchor_issued_seals.json').read_text())

@pytest.mark.parametrize('record', RECORDS)
def test_real_pre_anchor_immutable_receipt_and_sizing_reproduce(record):
    before=deepcopy(record)
    assert _original_budget(record['payload'],parse_time(record['created_at']))=='pre_anchor_seal'
    assert record==before
    # Historical compatibility cannot turn an old receipt into fresh publication authority.
    assert not current_execution_review(record['payload'],parse_time(record['created_at']),.03)['actionable']

@pytest.mark.parametrize('field,value', [('probability',.999),('reference_odds',8.),('stake',5.),('line',999.)])
def test_historical_replay_rejects_mutation(field,value):
    record=deepcopy(RECORDS[0]);record['payload'][field]=value
    with pytest.raises(ValueError):_original_budget(record['payload'],parse_time(record['created_at']))

@pytest.mark.parametrize('field', ['market_anchor','probability_basis','calibration_uncertainty','model_trained_at','probability_support_policy','outcome_sizing_policy','composite_contract_policy'])
def test_new_fields_cannot_be_stripped_from_historical_schema(field):
    record=deepcopy(RECORDS[0]);record['payload'][field]=None
    with pytest.raises(ValueError,match='seal'):_original_budget(record['payload'],parse_time(record['created_at']))

def test_historical_schema_not_accepted_after_its_cutoff():
    with pytest.raises(ValueError,match='seal'):
        _original_budget(RECORDS[0]['payload'],parse_time('2026-10-04T09:00:00+00:00'))

def test_exact_intermediate_pre_calibration_schema_replays():
    import hashlib
    from betting_engine.governance import SEALED_FIELDS
    record=deepcopy(RECORDS[0]);tip=record['payload']
    # Synthetic intermediate schema: explicit null anchor fields were sealed
    # before calibration metadata existed; numeric decision remains unchanged.
    tip.update(market_anchor=None,probability_basis=None)
    fields={k:tip.get(k) for k in SEALED_FIELDS if k not in ('calibration_uncertainty','model_trained_at')}
    fields['funding_basis']=tip['funding_basis']
    tip['decision_guard']['candidate_sha256']=hashlib.sha256(json.dumps(fields,sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert _original_budget(tip,parse_time(record['created_at']))=='pre_calibration_seal'
    tip['market_anchor']={'modified':True}
    with pytest.raises(ValueError,match='seal'):_original_budget(tip,parse_time(record['created_at']))
