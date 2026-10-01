"""Exact collection failures and document generations retain recovery boundaries."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from betting_engine import development_inputs as inputs
from betting_engine.development import (DevelopmentWorker, choose_task, recovery_evidence_valid,
    schedule_recoveries)
from betting_engine.io_utils import atomic_json
from betting_engine.runtime_errors import (DIAGNOSTIC_PARSER, failure_diagnostic,
    validation_document_changes)
from test_development_recovery import failed
from test_parallel_development import project

DOC = 'docs/UTILITY_EVIDENCE_REPLAY.json'
TEST = 'tests/test_passive_evidence_release.py'


def sha(body):
    return hashlib.sha256(body).hexdigest()


def scenario(tmp_path, *, historical=True):
    root, plan = project(tmp_path, 1)
    (root / TEST).write_text('def test_original_acceptance(): assert 2 + 2 == 4\n')
    exporter = root / 'src/betting_engine/development_inputs.py'
    exporter.write_text('ROOT_EXPORTER_VERSION = 1\n')
    raw = b'{"records": [{"kind": "frozen diagnostic"}]}\n'
    source = root / 'data/validation_source_archives' / (sha(raw) + '.bin')
    source.parent.mkdir(parents=True); source.write_bytes(raw)
    catalog = {'version': inputs.VALIDATION_VERSION, 'bundles': [{
        'id': 'replay-v1', 'delivery': 'workspace-documents',
        'tests': [{'path': TEST, 'sha256': sha((root / TEST).read_bytes())}],
        'files': [{'path': DOC, 'sha256': sha(raw), 'bytes': len(raw)}]}]}
    atomic_json(root / inputs.VALIDATION_CATALOG, catalog)
    state, now = failed(root, plan, 'test_failure')
    row = state['tasks']['D0']; row['due_at'] = (now - timedelta(hours=3)).isoformat()
    folder = root / 'data/development'; run = folder / 'runs' / row['run_id']
    result = json.loads((run / 'run.json').read_text())
    result.update(failure_class='test_failure', failure_phase='python-tests',
        error='python-tests exited 2; inspect its retained local log')
    row['last_error'] = result['error']
    if historical:
        generation = inputs.validation_document_generation(root, DOC)
        assert generation['available'] is True
        result['input_generation'] = {'validation_documents': {DOC: generation}}
    atomic_json(run / 'run.json', result)
    log = (f'________ ERROR collecting {TEST} ________\n'
           f'{TEST}:10: in <module>\n'
           f'E   FileNotFoundError: [Errno 2] No such file or directory: '
           f"'{folder / 'workspaces' / row['run_id'] / DOC}'\n"
           f'ERROR {TEST} - FileNotFoundError: [Errno 2] N...\n')
    (run / 'python-tests.stdout.log').write_text(log)
    (run / 'python-tests.stderr.log').write_text('')
    return root, plan, state, now, run, result, source


def changed_exporter(root):
    (root / 'src/betting_engine/development_inputs.py').write_text('ROOT_EXPORTER_VERSION = 2\n')


def test_collection_error_has_exact_test_and_declared_document_identity(tmp_path):
    root, _, _, _, run, result, _ = scenario(tmp_path)
    diagnostic = failure_diagnostic(root / 'data/development', run.name, result)
    assert diagnostic['failed_tests'] == [TEST]
    assert diagnostic['failed_test_count'] == 1
    assert diagnostic['failure_signatures'] == [f'ERROR {TEST} - FileNotFoundError: [Errno 2] N...']
    assert diagnostic['missing_controller_inputs'] == [DOC]
    assert diagnostic['validation_documents'] == result['input_generation']['validation_documents']
    assert diagnostic['input_files'] == {TEST: sha((root / TEST).read_bytes())}
    assert diagnostic['parser_version'] == DIAGNOSTIC_PARSER


@pytest.mark.parametrize('mutation', ['different_path', 'different_test', 'not_missing', 'undeclared', 'bad_catalog'])
def test_only_catalog_bound_collecting_test_and_missing_document_qualify(tmp_path, mutation):
    root, _, _, _, run, result, _ = scenario(tmp_path)
    path = run / 'python-tests.stdout.log'; text = path.read_text()
    if mutation == 'different_path': path.write_text(text.replace(DOC, 'docs/UNRELATED.json'))
    elif mutation == 'different_test': path.write_text(text.replace(TEST, 'tests/test_other.py'))
    elif mutation == 'not_missing': path.write_text(text.replace('FileNotFoundError:', 'ValueError:'))
    elif mutation == 'undeclared': (root / inputs.VALIDATION_CATALOG).unlink()
    else: (root / inputs.VALIDATION_CATALOG).write_text('{"broken": true}')
    diagnostic = failure_diagnostic(root / 'data/development', run.name, result)
    assert diagnostic['missing_controller_inputs'] == []
    assert diagnostic['validation_documents'] == {}


def test_legacy_empty_diagnostic_is_archived_and_enriched_without_reschedule(tmp_path):
    root, plan, state, now, run, result, _ = scenario(tmp_path, historical=False)
    folder = root / 'data/development'
    schedule_recoveries(plan, state, folder, now)
    row = state['tasks']['D0']; work = row['recovery_work']
    old = work['diagnostic']
    old.pop('parser_version'); old.pop('validation_documents')
    old.update(failed_tests=[], failed_test_count=0, failure_signatures=[],
               missing_controller_inputs=[], input_files={})
    work['repeated_failure_count'] = 4
    work['due_at'] = (now + timedelta(hours=2)).isoformat()
    original = deepcopy(work); original_row = deepcopy(row); events = len(state['recovery_events'])
    run_before = (run / 'run.json').read_bytes()
    schedule_recoveries(plan, state, folder, now + timedelta(minutes=1))
    repaired = row['recovery_work']
    assert repaired['diagnostic']['failed_tests'] == [TEST]
    assert repaired['diagnostic']['missing_controller_inputs'] == [DOC]
    assert repaired['diagnostic']['validation_documents'] == {}
    for key in ('state', 'due_at', 'scheduled_at', 'previous_next_attempt_at', 'repeated_failure_count'):
        assert repaired[key] == original[key]
    for key in ('due_at', 'next_attempt_at', 'attempts', 'last_error', 'run_id'):
        assert row[key] == original_row[key]
    archive = folder / repaired['prior_diagnostic_record']['path']
    assert json.loads(archive.read_bytes()) == original
    assert sha(archive.read_bytes()) == repaired['prior_diagnostic_record']['sha256']
    assert (run / 'run.json').read_bytes() == run_before
    assert len(state['recovery_events']) == events + 1
    assert choose_task(plan, state, now) is None
    once = deepcopy(state)
    changed_exporter(root)
    schedule_recoveries(plan, state, folder, now + timedelta(minutes=2))
    assert state == once  # No fabricated old document generation, even after a real source edit.


def test_known_document_generation_change_wakes_once_and_keeps_original_constraints(tmp_path):
    root, plan, state, now, run, result, _ = scenario(tmp_path)
    folder = root / 'data/development'
    schedule_recoveries(plan, state, folder, now)
    original_row = deepcopy(state['tasks']['D0']); runs = deepcopy(state['runs']); original_plan = deepcopy(plan)
    changed_exporter(root)
    schedule_recoveries(plan, state, folder, now + timedelta(seconds=1))
    row = state['tasks']['D0']; work = row['recovery_work']
    assert work['kind'] == 'validation_documents_changed'
    assert work['input_update']['validation_documents'][DOC]['previous'] == result['input_generation']['validation_documents'][DOC]
    assert work['input_update']['validation_documents'][DOC]['current'] == inputs.validation_document_generation(root, DOC)
    assert recovery_evidence_valid(folder, work)
    for key in ('due_at', 'next_attempt_at', 'attempts', 'last_error', 'run_id'):
        assert row[key] == original_row[key]
    assert state['runs'] == runs and plan == original_plan
    assert choose_task(plan, state, now + timedelta(minutes=1))['id'] == 'D0'
    once = deepcopy(state)
    schedule_recoveries(plan, state, folder, now + timedelta(minutes=2))
    assert state == once


@pytest.mark.parametrize('change', ['heartbeat', 'unrelated_source', 'unrelated_bundle', 'missing_archive', 'bad_archive', 'changed_bound_test'])
def test_unrelated_or_unavailable_generation_keeps_existing_backoff(tmp_path, change):
    root, plan, state, now, run, result, source = scenario(tmp_path)
    folder = root / 'data/development'; schedule_recoveries(plan, state, folder, now)
    state['tasks']['D0']['recovery_work']['due_at'] = (now + timedelta(hours=1)).isoformat()
    before = deepcopy(state)
    if change == 'heartbeat': atomic_json(root / 'data/live-heartbeat.json', {'at': now.isoformat()})
    elif change == 'unrelated_source': (root / 'src/betting_engine/other.py').write_text('unrelated = True')
    elif change == 'unrelated_bundle':
        catalog = json.loads((root / inputs.VALIDATION_CATALOG).read_text())
        other = deepcopy(catalog['bundles'][0]); other['id'] = 'unrelated-v1'; other['files'][0]['path'] = 'docs/OTHER.json'
        catalog['bundles'].append(other); atomic_json(root / inputs.VALIDATION_CATALOG, catalog)
    elif change == 'missing_archive': source.unlink()
    elif change == 'bad_archive': source.write_text('{"changed": true}')
    else: (root / TEST).write_text('changed bound regression')
    schedule_recoveries(plan, state, folder, now + timedelta(minutes=1),
        input_generation={'sha256': 'unrelated', 'components': {'validation_sources': 'new', 'stake_sources': 'new'},
                          'available': {'validation_sources': True, 'stake_sources': True}})
    assert state == before
    assert choose_task(plan, state, now + timedelta(minutes=1)) is None


def test_corrupt_original_log_cannot_gain_authority_from_changed_exporter(tmp_path):
    root, plan, state, now, run, _, _ = scenario(tmp_path)
    folder = root / 'data/development'; schedule_recoveries(plan, state, folder, now)
    (run / 'python-tests.stdout.log').write_text((run / 'python-tests.stdout.log').read_text() + 'altered\n')
    changed_exporter(root)
    schedule_recoveries(plan, state, folder, now + timedelta(seconds=1))
    work = state['tasks']['D0']['recovery_work']
    assert work['state'] == 'evidence_invalid' and work['invalid_reason'] == 'archive_changed'
    before = deepcopy(state)
    schedule_recoveries(plan, state, folder, now + timedelta(seconds=2))
    assert state == before and choose_task(plan, state, now) is None


def test_repair_generation_drift_invalidates_dispatch_then_rebinds_exact_proof(tmp_path):
    root, plan, state, now, _, _, _ = scenario(tmp_path)
    folder = root / 'data/development'; schedule_recoveries(plan, state, folder, now)
    changed_exporter(root); schedule_recoveries(plan, state, folder, now + timedelta(seconds=1))
    row = state['tasks']['D0']; old = deepcopy(row['recovery_work'])
    (root / 'src/betting_engine/development_inputs.py').write_text('ROOT_EXPORTER_VERSION = 3\n')
    assert not recovery_evidence_valid(folder, old)
    schedule_recoveries(plan, state, folder, now + timedelta(seconds=2))
    assert row['recovery_work']['state'] == 'evidence_invalid'
    schedule_recoveries(plan, state, folder, now + timedelta(seconds=3))
    work = row['recovery_work']
    assert work['state'] == 'scheduled' and work['evidence_sha256'] != old['evidence_sha256']
    assert work['due_at'] == old['due_at'] and work['previous_next_attempt_at'] == old['previous_next_attempt_at']
    assert recovery_evidence_valid(folder, work)


def test_document_change_cannot_bypass_original_dependencies(tmp_path):
    root, plan, state, now, _, _, _ = scenario(tmp_path)
    plan['tasks'][0]['depends_on'] = ['PREREQUISITE']
    state['tasks']['PREREQUISITE'] = {'state': 'retry'}
    folder = root / 'data/development'; schedule_recoveries(plan, state, folder, now)
    changed_exporter(root); schedule_recoveries(plan, state, folder, now + timedelta(minutes=1))
    assert state['tasks']['D0']['recovery_work']['kind'] == 'validation_documents_changed'
    assert choose_task(plan, state, now + timedelta(minutes=2)) is None


@pytest.mark.parametrize('kind', ['evidence_required', 'provider_failure'])
def test_prose_and_generation_alone_never_accelerate_other_failure_classes(tmp_path, kind):
    root, plan, _, _, _, _, _ = scenario(tmp_path)
    state, now = failed(root, plan, kind)
    row = state['tasks']['D0']; path = root / 'data/development/runs' / row['run_id'] / 'run.json'
    result = json.loads(path.read_text()); result['remaining'] = [f'Supply missing {DOC} and repair test collection.']
    result['input_generation'] = {'validation_documents': {DOC: inputs.validation_document_generation(root, DOC)}}
    atomic_json(path, result); changed_exporter(root)
    schedule_recoveries(plan, state, root / 'data/development', now,
        input_generation={'validation_documents': {DOC: inputs.validation_document_generation(root, DOC)}})
    assert 'recovery_work' not in row and choose_task(plan, state, now) is None


def test_workspace_permissions_protect_only_exact_selected_document(tmp_path):
    root, _, _, _, _, _, _ = scenario(tmp_path)
    worker = DevelopmentWorker(root)
    (worker.workspace / 'tests').mkdir(parents=True, exist_ok=True)
    (worker.workspace / TEST).write_bytes((root / TEST).read_bytes())
    args = ' '.join(worker.permission_args())
    assert json.dumps(str(worker.workspace / DOC)) + '="read"' in args
    assert json.dumps(str(worker.workspace / 'docs')) + '="read"' not in args
    assert json.dumps(str(worker.workspace / 'docs/UNRELATED.json')) + '="read"' not in args
