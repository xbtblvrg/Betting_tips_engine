"""Root-fixed, precisely diagnosed regression sources wake work once, not approve it."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from betting_engine import development
from betting_engine.io_utils import atomic_json
from test_development_recovery import failed
from test_parallel_development import project, stub

PYTEST = 'tests/test_periods.py'
DOM = 'tests/web_all_tier_sizing_cases.mjs'
OLD = b'CLOCK = "old fixed timestamp"\n'
NEW = b'CLOCK = "isolated relative timestamp"\n'


def digest(body):
    return hashlib.sha256(body).hexdigest()


def scenario(tmp_path, source=PYTEST, reference=None, doc='docs/CLOCK_REPAIR.md', remedy='correction'):
    root, plan = project(tmp_path, 1)
    state, now = failed(root, plan, 'evidence_required')
    row = state['tasks']['D0']; folder = root/'data/development'
    run = folder/'runs'/row['run_id']
    old = folder/'workspaces'/row['run_id']/source
    old.parent.mkdir(parents=True, exist_ok=True); old.write_bytes(OLD)
    current = root/source; current.parent.mkdir(parents=True, exist_ok=True); current.write_bytes(NEW)
    requirements = ['Validation requires controller '+remedy+' before full acceptance can run.',
                    'Keep authentic model and prospective source requirements pending.']
    error = 'Task remains incomplete: '+'; '.join(requirements)
    result = json.loads((run/'run.json').read_text())
    result.update(remaining=requirements, error=error)
    row.update(last_error=error, due_at=(now-timedelta(hours=2)).isoformat())
    atomic_json(run/'run.json', result)
    reference = reference or source
    text = 'Root must '+remedy+' the existing regression fixture `'+reference+'`; its clock is stale.'
    report = {'task_complete':False, 'remaining':requirements, 'evidence':[doc] if doc else [text]}
    atomic_json(run/'result.json', report)
    manifest = {source:digest(OLD)}
    if doc:
        body = (json.dumps({'diagnosis':text}) if doc.endswith('.json') else text).encode()
        path=run/'files'/doc;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(body)
        manifest[doc]=digest(body)
    atomic_json(run/'candidate-manifest.json',manifest)
    return root,plan,state,now,run


@pytest.mark.parametrize('source,reference,doc,remedy',[
    (PYTEST,PYTEST,'docs/CLOCK_REPAIR.md','fix'),
    (DOM,DOM,'docs/CLOCK_REPAIR.md','correction'),
    (PYTEST,'test_periods.py','docs/CLOCK_REPAIR.json','corrections'),
    (DOM,'web_all_tier_sizing_cases.mjs','docs/CLOCK_REPAIR.json','resolve'),
    (PYTEST,PYTEST,None,'repair'),
])
def test_exact_diagnosed_changed_test_wakes_once_preserving_failed_work(tmp_path, source, reference, doc, remedy):
    root,plan,state,now,run=scenario(tmp_path,source,reference,doc,remedy)
    folder=root/'data/development';before=deepcopy(state);original_run=(run/'run.json').read_bytes()
    assert development.choose_task(plan,state,now) is None
    development.schedule_recoveries(plan,state,folder,now)
    row=state['tasks']['D0'];work=row['recovery_work']
    assert work['kind']=='protected_sources_changed' and work['state']=='scheduled'
    assert work['input_update']['protected_sources']==[{'path':source,'previous_sha256':digest(OLD),'current_sha256':digest(NEW)}]
    assert development.recovery_evidence_valid(folder,work)
    assert development.choose_task(plan,state,now)['id']=='D0'
    for k,v in before['tasks']['D0'].items():assert row[k]==v
    assert state['runs']==before['runs'] and (run/'run.json').read_bytes()==original_run
    snapshot=deepcopy(state)
    for _ in range(5):development.schedule_recoveries(plan,state,folder,now+timedelta(minutes=1))
    assert state==snapshot and len(state['recovery_events'])==1
    assert 'approved' not in work and 'production_ready' not in work


@pytest.mark.parametrize('mutation',[
    'unchanged','unrelated_test','missing_baseline','baseline_changed','missing_current','manifest_mismatch',
    'document_mismatch','missing_report','report_mismatch','completed_report','no_test_name',
    'nonexact_name','foreign_path','ambiguous_basename','source_symlink','source_parent_symlink',
    'baseline_symlink','document_symlink','oversized_source','malformed_json_diagnosis','source_not_in_manifest',
    'not_regression_source','traversal_reference','no_remedy','no_controller_diagnosis',
])
def test_unrelated_ambiguous_missing_or_unbound_tests_never_wake(tmp_path,mutation):
    root,plan,state,now,run=scenario(tmp_path)
    folder=root/'data/development';rid=state['tasks']['D0']['run_id']
    baseline=folder/'workspaces'/rid/PYTEST;manifest=json.loads((run/'candidate-manifest.json').read_text())
    report=json.loads((run/'result.json').read_text());doc=run/'files/docs/CLOCK_REPAIR.md'
    if mutation in ('unchanged','unrelated_test'):
        (root/PYTEST).write_bytes(OLD)
        if mutation=='unrelated_test':(root/'tests/test_unrelated.py').write_bytes(NEW)
    elif mutation=='missing_baseline':baseline.unlink()
    elif mutation=='baseline_changed':baseline.write_bytes(b'not archived source')
    elif mutation=='missing_current':(root/PYTEST).unlink()
    elif mutation=='manifest_mismatch':manifest[PYTEST]='f'*64
    elif mutation=='document_mismatch':doc.write_text('root fix '+PYTEST)
    elif mutation=='missing_report':(run/'result.json').unlink()
    elif mutation=='report_mismatch':report['remaining']=['different diagnosis']
    elif mutation=='completed_report':report['task_complete']=True
    elif mutation in ('no_test_name','nonexact_name','foreign_path','traversal_reference','not_regression_source','ambiguous_basename'):
        reference={'no_test_name':'the suite', 'nonexact_name':'my_test_periods.py',
            'foreign_path':'elsewhere/tests/test_periods.py','traversal_reference':'../tests/test_periods.py',
            'not_regression_source':'tests/conftest.py','ambiguous_basename':'test_periods.py'}[mutation]
        doc.write_text('Root correction needed for '+reference)
        manifest['docs/CLOCK_REPAIR.md']=digest(doc.read_bytes())
        if mutation=='ambiguous_basename':manifest['tests/other/test_periods.py']=digest(OLD)
        if mutation=='not_regression_source':
            manifest['tests/conftest.py']=digest(OLD)
            (root/'tests/conftest.py').write_bytes(NEW)
            p=baseline.parent/'conftest.py';p.write_bytes(OLD)
    elif mutation=='source_symlink':
        p=root/PYTEST;p.unlink();p.symlink_to(baseline)
    elif mutation=='source_parent_symlink':
        p=root/'tests';p.rename(root/'renamed-tests');p.symlink_to(root/'renamed-tests',target_is_directory=True)
    elif mutation=='baseline_symlink':
        baseline.unlink();baseline.symlink_to(root/PYTEST)
    elif mutation=='document_symlink':
        other=tmp_path/'document';other.write_bytes(doc.read_bytes());doc.unlink();doc.symlink_to(other)
    elif mutation=='oversized_source':(root/PYTEST).write_bytes(b'x'*(1024*1024+1))
    elif mutation=='malformed_json_diagnosis':
        p=run/'files/docs/BROKEN.json';p.write_bytes(b'{not JSON}');manifest['docs/BROKEN.json']=digest(p.read_bytes());report['evidence'].append('docs/BROKEN.json')
    elif mutation=='source_not_in_manifest':manifest.pop(PYTEST)
    elif mutation in ('no_remedy','no_controller_diagnosis'):
        text='Validation evidence unavailable: '+PYTEST if mutation=='no_remedy' else 'Please fix '+PYTEST
        report['remaining']=[text];doc.write_text(text);manifest['docs/CLOCK_REPAIR.md']=digest(doc.read_bytes())
        r=json.loads((run/'run.json').read_text());r['remaining']=report['remaining'];atomic_json(run/'run.json',r)
    if mutation!='missing_report':atomic_json(run/'result.json',report)
    atomic_json(run/'candidate-manifest.json',manifest)
    original=deepcopy(state['tasks']['D0'])
    development.schedule_recoveries(plan,state,folder,now)
    assert 'recovery_work' not in state['tasks']['D0'] and development.choose_task(plan,state,now) is None
    for k,v in original.items():assert state['tasks']['D0'][k]==v


def test_exact_path_disambiguates_duplicate_basename(tmp_path):
    root,plan,state,now,run=scenario(tmp_path)
    manifest=json.loads((run/'candidate-manifest.json').read_text());manifest['tests/other/test_periods.py']=digest(OLD)
    atomic_json(run/'candidate-manifest.json',manifest)
    development.schedule_recoveries(plan,state,root/'data/development',now)
    assert [r['path'] for r in state['tasks']['D0']['recovery_work']['input_update']['protected_sources']]==[PYTEST]


@pytest.mark.parametrize('mutation',['current_changed','current_symlink','update_tampered','archive_tampered'])
def test_test_source_drift_invalidates_scheduled_dispatch(tmp_path,mutation):
    root,plan,state,now,run=scenario(tmp_path,DOM);folder=root/'data/development'
    development.schedule_recoveries(plan,state,folder,now);work=state['tasks']['D0']['recovery_work']
    if mutation=='current_changed':(root/DOM).write_bytes(b'newer unreviewed generation')
    elif mutation=='current_symlink':
        p=root/DOM;p.unlink();other=tmp_path/'same-bytes';other.write_bytes(NEW);p.symlink_to(other)
    elif mutation=='update_tampered':work['input_update']['protected_sources'][0]['path']='tests/conftest.py'
    else:
        item=next(i for i in work['evidence'] if i['path'].endswith('.blob'));(folder/item['path']).write_bytes(b'changed')
    assert not development.recovery_evidence_valid(folder,work)
    development.schedule_recoveries(plan,state,folder,now)
    assert work is not state['tasks']['D0']['recovery_work']
    assert state['tasks']['D0']['recovery_work']['state']=='evidence_invalid'
    assert development.choose_task(plan,state,now) is None


def test_changed_fixture_cannot_override_provider_backoff_or_running_claim(tmp_path):
    root,plan,state,now,run=scenario(tmp_path);folder=root/'data/development'
    result=json.loads((run/'run.json').read_text());result['error']='implementation exited 1; inspect its retained local log'
    state['tasks']['D0']['last_error']=result['error'];atomic_json(run/'run.json',result)
    development.schedule_recoveries(plan,state,folder,now)
    assert 'recovery_work' not in state['tasks']['D0']
    state['tasks']['D0']['state']='running';snapshot=deepcopy(state)
    development.schedule_recoveries(plan,state,folder,now)
    assert state==snapshot


def test_next_attempt_with_same_root_fixed_tests_does_not_loop(tmp_path):
    root,plan,state,now,run=scenario(tmp_path);folder=root/'data/development'
    baseline=folder/'workspaces'/state['tasks']['D0']['run_id']/PYTEST;baseline.write_bytes(NEW)
    manifest=json.loads((run/'candidate-manifest.json').read_text());manifest[PYTEST]=digest(NEW)
    atomic_json(run/'candidate-manifest.json',manifest)
    development.schedule_recoveries(plan,state,folder,now)
    assert 'recovery_work' not in state['tasks']['D0']


def test_diagnosis_only_in_hash_verified_json_can_explain_root_correction(tmp_path):
    root,plan,state,now,run=scenario(tmp_path,doc='docs/CLOCK_REPAIR.json')
    report=json.loads((run/'result.json').read_text());report['remaining']=['Complete existing validation and source work.']
    result=json.loads((run/'run.json').read_text());result['remaining']=report['remaining']
    atomic_json(run/'run.json',result);atomic_json(run/'result.json',report)
    development.schedule_recoveries(plan,state,root/'data/development',now)
    assert state['tasks']['D0']['recovery_work']['kind']=='protected_sources_changed'


def test_full_original_acceptance_still_runs_and_existing_tests_stay_protected(tmp_path,monkeypatch):
    root,plan,state,now,run=scenario(tmp_path);atomic_json(root/'data/development/state.json',state)
    seen=stub(monkeypatch);worker=development.DevelopmentWorker(root)
    result=worker.loop(once=True)
    assert result['state']=='candidate_ready'
    assert [phase for _,phase,_ in seen]==['implementation','python-tests','dom-tests','review']
    prompt=(worker.folder/'runs'/result['id']/'prompt.txt').read_text()
    assert plan['tasks'][0]['acceptance'] in prompt and PYTEST in prompt
    assert worker.state['tasks']['D0']['attempts']==5
    with pytest.raises(ValueError,match='Existing regression tests must remain unchanged'):
        development.validate_changes({PYTEST:digest(OLD)}, {PYTEST:digest(NEW)})


def test_candidate_added_uninstalled_test_is_not_root_repair_or_a_blocker(tmp_path):
    root,plan,state,now,run=scenario(tmp_path);folder=root/'data/development'
    name='tests/test_candidate_clock.py'
    doc=run/'files/docs/CLOCK_REPAIR.md';doc.write_text(doc.read_text()+' Candidate regression added: '+name)
    manifest=json.loads((run/'candidate-manifest.json').read_text())
    manifest['docs/CLOCK_REPAIR.md']=digest(doc.read_bytes());manifest[name]=digest(NEW)
    p=folder/'workspaces'/state['tasks']['D0']['run_id']/name;p.write_bytes(NEW)
    atomic_json(run/'candidate-manifest.json',manifest)
    development.schedule_recoveries(plan,state,folder,now)
    work=state['tasks']['D0']['recovery_work']
    assert [s['path'] for s in work['input_update']['protected_sources']]==[PYTEST]
    assert development.recovery_evidence_valid(folder,work)


@pytest.mark.parametrize('kind',['large_valid','oversized','too_deep'])
def test_json_diagnosis_keeps_explicit_bounded_read_limit(tmp_path,kind):
    root,plan,state,now,run=scenario(tmp_path,doc='docs/CLOCK_REPAIR.json')
    p=run/'files/docs/CLOCK_REPAIR.json';value=json.loads(p.read_text())
    if kind=='too_deep':body=('['*2000+'0'+']'*2000).encode()
    else:
        value['retained_evidence']='x'*(350000 if kind=='large_valid' else 1024*1024)
        body=json.dumps(value).encode()
    p.write_bytes(body);manifest=json.loads((run/'candidate-manifest.json').read_text())
    manifest['docs/CLOCK_REPAIR.json']=digest(body);atomic_json(run/'candidate-manifest.json',manifest)
    development.schedule_recoveries(plan,state,root/'data/development',now)
    assert ('recovery_work' in state['tasks']['D0'])==(kind=='large_valid')


def test_absent_candidate_test_list_cannot_exhaust_existing_source_bound(tmp_path):
    root,plan,state,now,run=scenario(tmp_path)
    doc=run/'files/docs/CLOCK_REPAIR.md';manifest=json.loads((run/'candidate-manifest.json').read_text())
    names=['tests/test_candidate_'+str(i)+'.py' for i in range(20)]
    doc.write_text(doc.read_text()+'\nCandidate regressions: '+' '.join(names))
    manifest.update({name:digest(NEW) for name in names})
    manifest['docs/CLOCK_REPAIR.md']=digest(doc.read_bytes());atomic_json(run/'candidate-manifest.json',manifest)
    development.schedule_recoveries(plan,state,root/'data/development',now)
    assert [s['path'] for s in state['tasks']['D0']['recovery_work']['input_update']['protected_sources']]==[PYTEST]
