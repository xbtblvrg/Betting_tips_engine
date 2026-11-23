import json
import pytest


def test_finish_agy_step_decodes_markdown_fences_and_structured_output():
    from betting_engine.agy_development import finish_agy_step
    from betting_engine.claude_development import VERSION
    report_schema = {
        'type': 'object',
        'properties': {
            'summary': {'type': 'string'},
            'task_complete': {'type': 'boolean'},
            'tests_added': {'type': 'array', 'items': {'type': 'string'}},
            'remaining': {'type': 'array', 'items': {'type': 'string'}},
            'evidence': {'type': 'array', 'items': {'type': 'string'}},
            'helper_reviews': {'type': 'array', 'items': {'type': 'object'}}
        },
        'required': ['summary', 'task_complete', 'tests_added', 'remaining', 'evidence', 'helper_reviews'],
        'additionalProperties': False
    }
    edit_schema = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string'},
            'before_sha256': {'type': ['string', 'null']},
            'content': {'type': 'string'}
        },
        'required': ['path', 'before_sha256', 'content'],
        'additionalProperties': False
    }
    envelope_schema = {
        'type': 'object',
        'properties': {
            'report': report_schema,
            'edits': {'type': 'array', 'items': edit_schema, 'maxItems': 10}
        },
        'required': ['report', 'edits'],
        'additionalProperties': False
    }
    context = {
        'version': VERSION,
        'output_schema': envelope_schema,
        'result_schema': report_schema,
        'readonly': False,
        'protected_paths': [],
        'source_bundle': {'files': [], 'inventory': set()}
    }
    payload = {
        'report': {
            'summary': 'Test summary',
            'task_complete': True,
            'tests_added': ['tests/test_foo.py'],
            'remaining': [],
            'evidence': ['docs/EVIDENCE.md'],
            'helper_reviews': []
        },
        'edits': [
            {
                'path': 'tests/test_foo.py',
                'before_sha256': None,
                'content': 'def test_foo(): pass\n'
            }
        ]
    }
    raw_response = {
        'conversation_id': 'test-conv-123',
        'status': 'SUCCESS',
        'response': '```json\n' + json.dumps(payload) + '\n```'
    }
    raw_str = json.dumps(raw_response)

    res = finish_agy_step(raw_str, context, 'gemini-3.8-flash-high')
    assert res['provider'] == 'agy'
    assert res['model'] == 'gemini-3.8-flash-high'
    assert res['report']['summary'] == 'Test summary'
    assert len(res['edits']) == 1
    assert res['edits'][0]['path'] == 'tests/test_foo.py'


def test_finish_agy_step_handles_empty_or_malformed_as_provider_unavailable():
    from betting_engine.agy_development import finish_agy_step
    from betting_engine.provider_runtime import ProviderUnavailable
    from betting_engine.claude_development import VERSION
    context = {'version': VERSION, 'output_schema': {}, 'result_schema': {}}

    # Empty response
    empty_raw = json.dumps({'conversation_id': '123', 'status': 'SUCCESS', 'response': ''})
    with pytest.raises(ProviderUnavailable, match='empty response'):
        finish_agy_step(empty_raw, context, 'gemini-3.8-flash-high')

    # Non-JSON response
    malformed_raw = json.dumps({'conversation_id': '123', 'status': 'SUCCESS', 'response': 'Not json text'})
    with pytest.raises(ProviderUnavailable, match='non-JSON output'):
        finish_agy_step(malformed_raw, context, 'gemini-3.8-flash-high')


def test_parallel_agy_slots_allow_concurrent_threads_without_collision(tmp_path):
    import threading
    from betting_engine.agy_development import acquire_agy_slot

    locks_dir = tmp_path / 'ai-locks'
    barrier = threading.Barrier(4)
    release = threading.Event()
    acquired_slots = []
    lock = threading.Lock()

    def worker():
        with acquire_agy_slot(locks_dir, max_slots=4, timeout=5.0) as (ok, slot_id):
            assert ok is True
            with lock:
                acquired_slots.append(slot_id)
            barrier.wait(timeout=3.0)
            release.wait(timeout=3.0)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()

    # Wait until all 4 threads simultaneously hold 4 distinct slots
    deadline_check = __import__('time').monotonic() + 3.0
    while len(acquired_slots) < 4 and __import__('time').monotonic() < deadline_check:
        __import__('time').sleep(0.02)
    assert sorted(acquired_slots) == [0, 1, 2, 3]

    # A 5th concurrent caller while all 4 slots stay held must cleanly fail-fast on short timeout
    with acquire_agy_slot(locks_dir, max_slots=4, timeout=0.1) as (ok5, slot5):
        assert ok5 is False and slot5 == -1

    release.set()
    for t in threads:
        t.join(timeout=3.0)


def test_session_binding_prevents_cross_thread_and_cross_workspace_mixing(tmp_path):
    import threading
    from betting_engine.agy_development import create_agy_session_binding, verify_agy_session_binding

    ws_a = tmp_path / 'ws_a'
    ws_b = tmp_path / 'ws_b'
    ws_a.mkdir()
    ws_b.mkdir()

    binding_a = create_agy_session_binding(0, 'D27', ws_a)
    ctx_a = {'agy_isolation': dict(binding_a)}

    # Exact thread + exact workspace passes
    verify_agy_session_binding(ctx_a, binding_a, ws_a)

    # Swapping workspace B against workspace A's binding must fail immediately
    with pytest.raises(ValueError, match='Cross-thread Antigravity session or workspace binding mismatch'):
        verify_agy_session_binding(ctx_a, binding_a, ws_b)

    # Calling verify from a different thread against Thread A's binding must fail immediately
    cross_thread_errors = []
    def other_thread():
        try:
            verify_agy_session_binding(ctx_a, binding_a, ws_a)
        except ValueError as exc:
            cross_thread_errors.append(str(exc))

    t = threading.Thread(target=other_thread)
    t.start()
    t.join(timeout=2.0)
    assert len(cross_thread_errors) == 1
    assert 'Cross-thread' in cross_thread_errors[0]


def test_parallel_run_ai_step_threads_never_mix_outputs_or_logs(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    from betting_engine import provider_runtime as runtime, agy_development, claude_development

    monkeypatch.setattr(runtime, 'enabled', lambda root: True)
    router = SimpleNamespace(
        get_selection=lambda: {'id': 'agy', 'provider': 'agy', 'model': 'gemini-4-argon-high'},
        policy={'routes': [{'id': 'agy', 'provider': 'agy', 'model': 'gemini-4-argon-high'}]},
        report_failure=lambda *a: None,
        report_success=lambda *a: None,
    )
    monkeypatch.setattr(runtime, 'ProviderRuntime', lambda root: SimpleNamespace(
        router=lambda: router,
        config={'agy_binary': '/usr/bin/agy', 'max_parallel_agy_slots': 4},
    ))

    def fake_prepare(root, workspace, directory, task, prompt, result_schema, **kw):
        return {
            'stdin': f'TASK={task["id"]}',
            'cwd': str(directory),
            'env': {},
            'context': {'task_id': task['id'], 'workspace': str(workspace)},
        }

    def fake_finish(raw, ctx, model):
        data = json.loads(raw)
        return {
            'report': {'summary': f"done-{data['task']}", 'task_id': data['task']},
            'edits': [{'path': f"docs/{data['task']}.md", 'content': data['task']}],
        }

    applied_by_ws = {}
    app_lock = threading.Lock()
    def fake_apply(ws, edits, ctx):
        with app_lock:
            applied_by_ws[str(ws)] = [e['path'] for e in edits]
        return [e['path'] for e in edits]

    monkeypatch.setattr(claude_development, 'prepare_step', fake_prepare)
    monkeypatch.setattr(agy_development, 'finish_agy_step', fake_finish)
    monkeypatch.setattr(claude_development, 'apply_edits', fake_apply)

    barrier = threading.Barrier(3)
    seen_log_files = []
    results = {}

    def lane_run(idx):
        tid = f'TASK_{idx}'
        ws = tmp_path / f'ws_{idx}'
        ws.mkdir()
        run_dir = tmp_path / f'run_{idx}'
        run_dir.mkdir()
        schema = run_dir / 'schema.json'
        schema.write_text('{}')
        output = run_dir / 'result.json'

        def run_proc(cmd, prefix, prompt, timeout, process_cwd=None, process_env=None):
            barrier.wait(timeout=3.0)
            log_idx = cmd.index('--log-file')
            with app_lock:
                seen_log_files.append(cmd[log_idx + 1])
            assert f'TASK={tid}' in cmd[cmd.index('-p') + 1]
            from pathlib import Path as _P
            _P(str(prefix) + '.stdout.log').write_text(json.dumps({'task': tid}))

        w = SimpleNamespace(
            root=tmp_path,
            workspace=ws,
            current_task={'id': tid},
            readonly_input_paths=lambda: [],
            run_process=run_proc,
        )
        rec = runtime.run_ai_step(w, schema, output, run_dir / 'implementation', f'prompt-{tid}', 30)
        with app_lock:
            results[tid] = (rec, json.loads(output.read_text()), str(ws))

    threads = [threading.Thread(target=lane_run, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert len(results) == 3
    assert len(set(seen_log_files)) == 3  # 3 distinct isolated CLI log files
    slots_used = {results[f'TASK_{i}'][0]['agy_slot_id'] for i in range(3)}
    assert len(slots_used) == 3  # 3 distinct slots simultaneously
    tokens_used = {results[f'TASK_{i}'][0]['agy_session_token'] for i in range(3)}
    assert len(tokens_used) == 3  # 3 distinct cryptographic session tokens
    for i in range(3):
        tid = f'TASK_{i}'
        rec, out_json, ws_str = results[tid]
        assert out_json == {'summary': f'done-{tid}', 'task_id': tid}
        assert applied_by_ws[ws_str] == [f'docs/{tid}.md']


def test_agy_bounded_source_bundle_preserves_valid_json_under_arg_max(tmp_path, monkeypatch):
    """Verifies that when prompt header + source_bundle exceeds 218 KB, provider_runtime drops
    tail bundle files cleanly rather than slicing mid-JSON at 220 KB.
    """
    from types import SimpleNamespace
    from betting_engine import provider_runtime as runtime, agy_development, claude_development as cd

    monkeypatch.setattr(runtime, 'enabled', lambda root: True)
    router = SimpleNamespace(
        get_selection=lambda: {'id': 'agy', 'provider': 'agy', 'model': 'gemini-4-argon-high'},
        policy={'routes': [{'id': 'agy', 'provider': 'agy', 'model': 'gemini-4-argon-high'}]},
        report_failure=lambda *a: None,
        report_success=lambda *a: None,
    )
    monkeypatch.setattr(runtime, 'ProviderRuntime', lambda root: SimpleNamespace(
        router=lambda: router,
        config={'agy_binary': '/usr/bin/agy', 'max_parallel_agy_slots': 4},
    ))

    big_header = 'INSTRUCTIONS:\n' + ('H' * 85000) + '\nBOUNDED SOURCE BUNDLE:\n'
    files = [{'path': f'src/mod_{i}.py', 'content': 'x = ' + ('1' * 16000) + '\n', 'sha256': f'{i:064x}'} for i in range(10)]

    def fake_prepare(root, workspace, temp_dir, task, prompt, schema, **kwargs):
        sb = {'files': files, 'selected_bytes': sum(len(f['content']) for f in files), 'omitted': ['src/extra.py'], 'scope': 'bounded'}
        return {
            'stdin': big_header + json.dumps(sb),
            'cwd': str(temp_dir),
            'env': {'HOME': str(temp_dir)},
            'context': {'source_bundle': sb, 'selected': {}},
        }

    monkeypatch.setattr(cd, 'prepare_step', fake_prepare)
    monkeypatch.setattr(cd, 'apply_edits', lambda ws, edits, ctx: [])
    monkeypatch.setattr(agy_development, 'finish_agy_step', lambda raw, ctx, model: {
        'edits': [], 'report': {'summary': 'ok'}
    })

    captured_payloads = []

    def run_proc(cmd, prefix, prompt, timeout, process_cwd=None, process_env=None):
        p_arg = cmd[cmd.index('-p') + 1]
        captured_payloads.append(p_arg)
        from pathlib import Path as _P
        _P(str(prefix) + '.stdout.log').write_text('{"response": "{}"}')

    ws = tmp_path / 'ws'
    ws.mkdir()
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    schema = run_dir / 'schema.json'
    schema.write_text('{}')
    output = run_dir / 'out.json'
    w = SimpleNamespace(
        root=tmp_path,
        workspace=ws,
        current_task={'id': 'T_JSON'},
        readonly_input_paths=lambda: [],
        run_process=run_proc,
    )
    runtime.run_ai_step(w, schema, output, run_dir / 'step', 'prompt', 30, sandbox='read-only')

    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert len(payload.encode('utf-8')) <= 218000
    assert '[TRUNCATED SOURCE CONTEXT' not in payload
    bundle_part = payload.split('\nBOUNDED SOURCE BUNDLE:\n', 1)[1]
    parsed_bundle = json.loads(bundle_part)
    assert 1 <= len(parsed_bundle['files']) < 10
    assert parsed_bundle['omitted_count'] == 1 + (10 - len(parsed_bundle['files']))



