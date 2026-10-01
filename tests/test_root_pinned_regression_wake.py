"""Feed growth cannot supply an explicitly root-pinned regression fixture."""
from copy import deepcopy
import json

import pytest

from betting_engine.development import schedule_recoveries, choose_task
from betting_engine.io_utils import atomic_json
from betting_engine.runtime_errors import meaningful_source_changes
from test_development_recovery import failed
from test_parallel_development import project

ACTUAL_REQUIREMENT = ('Root-pin the companion-test fixture correction: its current-export '
                      'assertion fails because no volleyball offers are present.')


@pytest.mark.parametrize('requirement', [ACTUAL_REQUIREMENT,
    'Root-pin the retained regression fixture before retrying current offers.',
    'Supply the root-pinned historical regression fixture required by the unchanged test.',
    'Provide a root-pinned test fixture repair for old odds.'])
@pytest.mark.parametrize('task_id', ['E7E4DE07846', 'UNRELATED_TASK'])
def test_explicit_outstanding_root_fixture_holds_only_feed_acceleration(tmp_path, requirement, task_id):
    result = {'task_id': task_id, 'remaining': [requirement],
              'input_generation': {'available': {'stake_sources': True}}}
    changed = {'stake_sources': 'new-feed', 'validation_sources': 'new-fixture', 'scoped_models': 'new-model'}
    original = deepcopy((result, changed))
    kept, notes = meaningful_source_changes(tmp_path, 'run', result, {}, changed)
    assert kept == {'validation_sources': 'new-fixture', 'scoped_models': 'new-model'}
    assert notes[0]['state'] == 'awaiting_root_pinned_regression_fixture'
    assert (result, changed) == original


@pytest.mark.parametrize('requirement', [
    'No volleyball offers are present; discover new current contracts.',
    'Validate available current offers and preserve the regression fixture.',
    'Supply root-pinned model evidence for current offers.',
    'The root-pinned regression fixture correction is complete; discover current offers.',
    'No root-pinned regression fixture correction is required; inspect current offers.',
    'Root-pin the model artifact for current odds.',
])
def test_current_contract_discovery_and_non_fixture_work_keep_source_wake(tmp_path, requirement):
    result = {'remaining': [requirement], 'input_generation': {'available': {'stake_sources': True}}}
    assert meaningful_source_changes(tmp_path, 'run', result, {}, {'stake_sources': 'new'}) == ({'stake_sources': 'new'}, [])


def scenario(tmp_path, requirement):
    root, plan = project(tmp_path, 1)
    state, now = failed(root, plan, 'evidence_required')
    row = state['tasks']['D0']; folder = root / 'data/development'
    path = folder / 'runs' / row['run_id'] / 'run.json'
    result = json.loads(path.read_text())
    result.update(remaining=[requirement], input_generation={
        'components': {'stake_sources': 'old'}, 'available': {'stake_sources': True}})
    atomic_json(path, result)
    generation = {'components': {'stake_sources': 'new'}, 'available': {'stake_sources': True}, 'sha256': 'new-generation'}
    return plan, state, now, folder, generation


def test_controller_preserves_original_backoff_deadline_attempts_and_failed_run(tmp_path):
    plan, state, now, folder, generation = scenario(tmp_path, ACTUAL_REQUIREMENT)
    original = deepcopy((plan, state))
    schedule_recoveries(plan, state, folder, now, generation)
    row = state['tasks']['D0']
    assert 'recovery_work' not in row
    assert choose_task(plan, state, now) is None
    assert row['source_reassessment']['state'] == 'unchanged_required_evidence'
    assert row['source_reassessment']['findings'][0]['state'] == 'awaiting_root_pinned_regression_fixture'
    for name in ('next_attempt_at', 'attempts', 'run_id', 'last_error'):
        assert row[name] == original[1]['tasks']['D0'][name]
    assert state['runs'] == original[1]['runs']
    assert plan == original[0]
    assert not state['recovery_events']


def test_controller_keeps_genuine_contract_discovery_without_fixture_prerequisite(tmp_path):
    plan, state, now, folder, generation = scenario(tmp_path, 'No volleyball offers are present; discover current contracts.')
    schedule_recoveries(plan, state, folder, now, generation)
    row = state['tasks']['D0']
    assert row['recovery_work']['kind'] == 'source_inputs_changed'
    assert row['recovery_work']['input_update']['components'] == {'stake_sources': 'new'}
    assert choose_task(plan, state, now)['id'] == 'D0'
