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


def test_scheduler_wakes_on_error_before_timer_and_coalesces(tmp_path, monkeypatch):
    from betting_engine.development import DevelopmentWorker
    import betting_engine.development as development
    clock = [100.0]
    monkeypatch.setattr(development.time, 'monotonic', lambda: clock[0])
    worker = DevelopmentWorker(tmp_path)
    assert worker.engineering_intake_due()
    assert worker._engineering_due == 160
    clock[0] = 101
    worker.state['tasks']['D14'] = {'state': 'retry', 'last_error': 'new failure'}
    assert not worker.engineering_intake_due()  # two-second coalescing
    clock[0] = 102
    assert worker.engineering_intake_due()
    clock[0] = 105
    assert not worker.engineering_intake_due()  # unchanged error not a new attempt
    clock[0] = 162
    assert worker.engineering_intake_due()  # periodic fallback still runs


def test_gate_only_state_transition_wakes_intake(tmp_path):
    state = {'tasks': {'D14': {'runtime_failure_gate': {'source_run_id': 'r1', 'state': 'awaiting_changed_evidence'}}}}
    before = engineering_input_generation(tmp_path, state)
    state['tasks']['D14']['runtime_failure_gate']['state'] = 'evidence_invalid'
    assert engineering_input_generation(tmp_path, state) != before
