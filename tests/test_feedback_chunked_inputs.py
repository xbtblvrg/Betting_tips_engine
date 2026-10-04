"""Large feedback retains complete evidence without duplicating payload cohorts."""
import copy
import json
from datetime import timedelta
import pytest
from betting_engine import outcome_feedback as feedback
from tests.test_outcome_feedback import cohort, NOW


def test_chunked_feedback_preserves_full_inputs_and_replays_idempotently(tmp_path,monkeypatch):
    monkeypatch.setattr(feedback,'MAX_BYTES',64000)
    monkeypatch.setattr(feedback,'INPUT_CHUNK_BYTES',4000)
    data=cohort();data['decisions'][0]['payload']['retained_detail']='x'*12000
    before=copy.deepcopy(data)
    result=feedback.record_outcome_feedback(tmp_path/'data',**data,as_of=NOW)
    assert result['source_verified'] and data==before
    review=json.loads((tmp_path/result['review_path']).read_text())
    assert 'inputs' not in review and review['input_archive']['counts']=={k:len(v) for k,v in data.items()}
    rows={k:[] for k in data}
    for ref in review['input_archive']['chunks']:
        value=json.loads((tmp_path/'data'/ref['path']).read_text());rows[value['kind']]+=value['rows']
    assert rows==feedback.settled_inputs(**data)
    loss=next(p for p in result['proposals'] if p['kind']=='negative_original_pnl')
    assert 'original_decisions' not in loss['evidence']
    assert loss['evidence']['original_decisions_reference']['input_sha256']==review['input_sha256']
    again=feedback.record_outcome_feedback(tmp_path/'data',**data,as_of=NOW+timedelta(minutes=1))
    assert again['review_sha256']==result['review_sha256']
    assert len(list((tmp_path/'data/outcome_feedback/reviews').glob('*.json')))==1
    assert again['source_verified']


def test_missing_or_modified_chunk_fails_closed(tmp_path,monkeypatch):
    monkeypatch.setattr(feedback,'MAX_BYTES',64000)
    data=cohort();data['decisions'][0]['payload']['retained_detail']='x'*12000
    result=feedback.record_outcome_feedback(tmp_path/'data',**data,as_of=NOW)
    review=json.loads((tmp_path/result['review_path']).read_text())
    path=tmp_path/'data'/next(ref['path'] for ref in review['input_archive']['chunks'] if ref['kind']=='decisions')
    path.write_text(path.read_text().replace('decision1','decision2'))
    reread=feedback.read_outcome_feedback(tmp_path/'data',now=NOW)
    assert not reread['source_verified'] and reread['state']=='evidence_invalid'
    with pytest.raises(ValueError):feedback.record_outcome_feedback(tmp_path/'data',**data,as_of=NOW)


def test_chunk_archive_limit_cannot_publish_completed_evidence(tmp_path,monkeypatch):
    monkeypatch.setattr(feedback,'MAX_BYTES',64000)
    monkeypatch.setattr(feedback,'MAX_INPUT_ARCHIVE_BYTES',100)
    data=cohort();data['decisions'][0]['payload']['retained_detail']='x'*12000
    with pytest.raises(ValueError,match='feedback_input_archive_limit'):
        feedback.record_outcome_feedback(tmp_path/'data',**data,as_of=NOW)
    assert not (tmp_path/'data/outcome_feedback/latest.json').exists()

def test_invalid_feedback_return_cannot_complete_learning_job(tmp_path,monkeypatch):
    from tests.test_learning import worker
    w=worker(tmp_path);w.run_once()
    job_id=w.enqueue('outcome_review','invalid-return')
    monkeypatch.setattr(feedback,'record_outcome_feedback',lambda *a,**kw:{'source_verified':False,'state':'evidence_invalid','error':'chunk_missing','proposals':[]})
    w.run_once()
    job=next(j for j in w.snapshot()['jobs'] if j['id']==job_id)
    assert job['status']=='retry' and job['result'] is None
    assert 'outcome_feedback_verification_failed' in job['last_error']
    with w.store.db() as db:
        assert db.execute('SELECT status FROM learning_attempts WHERE job_id=?',(job_id,)).fetchone()[0]=='failed'
