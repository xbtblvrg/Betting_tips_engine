from betting_engine.engineering_loop import engineering_input_generation


def test_new_failure_wakes_intake_without_waiting_periodic_timer(tmp_path):
    state = {'tasks': {'D14': {'state': 'running', 'run_id': 'attempt-1'}}}
    before = engineering_input_generation(tmp_path, state)
    state['tasks']['D14'].update(state='retry', failure_class='controller_failure',
                                last_error='Retained evidence unavailable')
    assert engineering_input_generation(tmp_path, state) != before
    first = engineering_input_generation(tmp_path, state)
    state.update(checked_at='later', resource_admission={'allowed_slots': 6})
    state['tasks']['D14']['next_attempt_at'] = 'later'
    assert engineering_input_generation(tmp_path, state) == first
    assert state['tasks']['D14']['state'] == 'retry'


def test_atomic_evidence_replacement_and_removal_wake_once(tmp_path):
    path = tmp_path / 'data/supervisor.json'
    path.parent.mkdir()
    before = engineering_input_generation(tmp_path, {})
    path.write_text('{"new_error":true}')
    changed = engineering_input_generation(tmp_path, {})
    assert changed != before
    assert engineering_input_generation(tmp_path, {}) == changed
    other = path.with_suffix('.tmp')
    other.write_text('{"new_error":true}')
    other.replace(path)
    assert engineering_input_generation(tmp_path, {}) != changed
    path.unlink()
    assert engineering_input_generation(tmp_path, {}) == before


def test_failed_task_resolution_is_a_new_generation(tmp_path):
    state = {'tasks': {'D14': {'state': 'retry', 'last_error': 'bad evidence'}}}
    before = engineering_input_generation(tmp_path, state)
    state['tasks']['D14'] = {'state': 'production_verified'}
    assert engineering_input_generation(tmp_path, state) != before
