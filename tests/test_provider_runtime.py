import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from betting_engine import provider_runtime as runtime
from betting_engine.io_utils import atomic_json


def test_prose_cannot_trigger_quota_reset(tmp_path):
    prefix = tmp_path / 'step'
    Path(str(prefix) + '.stdout.log').write_text(json.dumps({
        'type': 'item.completed', 'item': {'type': 'agent_message',
        'text': "You've hit your usage limit. Reset now."}}) + '\n')
    assert runtime.failure_observation(prefix, RuntimeError())['kind'] == 'unknown'


def test_structured_usage_error_is_distinct_from_timeout(tmp_path):
    prefix = tmp_path / 'step'
    Path(str(prefix) + '.stdout.log').write_text(json.dumps({
        'type': 'turn.failed', 'error': {'codexErrorInfo': 'usageLimitExceeded'}}) + '\n')
    observed = runtime.failure_observation(prefix, RuntimeError())
    assert observed['kind'] == 'quota' and observed['source'] == 'codex_structured'
    assert len(observed['evidence_sha256']) == 64
    prefix2 = tmp_path / 'timeout'
    Path(str(prefix2) + '.stderr.log').write_text('HTTP 429 retry or reset usage limit')
    assert runtime.failure_observation(prefix2, TimeoutError())['kind'] == 'timeout'


def test_disabled_keeps_existing_command_and_process(tmp_path):
    calls = []
    worker = SimpleNamespace(root=tmp_path,
        command=lambda s, o, mode: ['codex', mode],
        run_process=lambda *args: calls.append(args))
    runtime.run_ai_step(worker, 'schema', 'output', 'prefix', 'prompt', 12)
    assert calls == [(['codex', 'workspace-write'], 'prefix', 'prompt', 12)]


class FakeRouter:
    def __init__(self, routes):
        self.routes, self.failures, self.successes = list(routes), [], []
        self.index = 0

    def get_selection(self):
        return self.routes[min(self.index, len(self.routes) - 1)]

    def periodic_tick(self, max_actions):
        self.index = min(self.index + 1, len(self.routes) - 1)

    def report_failure(self, route_id, observation):
        self.failures.append((route_id, observation))
        self.index = min(self.index + 1, len(self.routes) - 1)

    def report_success(self, *args):
        self.successes.append(args)


def setup_runtime(tmp_path, monkeypatch, routes):
    router = FakeRouter(routes)
    monkeypatch.setattr(runtime, 'enabled', lambda root: True)
    monkeypatch.setattr(runtime, 'ProviderRuntime', lambda root: SimpleNamespace(
        router=lambda: router, config={'claude_binary': 'claude'}))
    output = tmp_path / 'result.json'
    worker = SimpleNamespace(root=tmp_path, workspace=tmp_path,
        command=lambda s, o, mode: ['codex', 'exec', '-m', 'primary'])
    return router, output, worker


def codex_route(model='primary'):
    return {'id': model, 'provider': 'codex', 'model': model}


def test_quota_fails_over_same_step_without_changing_previous_files(tmp_path, monkeypatch):
    router, output, worker = setup_runtime(tmp_path, monkeypatch,
                                          [codex_route(), codex_route('secondary')])
    marker = tmp_path / 'partial.py'; marker.write_text('PRESERVED = True\n')
    calls = []
    def run(command, prefix, prompt, timeout):
        calls.append(command)
        if len(calls) == 1:
            Path(str(prefix) + '.stdout.log').write_text(json.dumps({
                'type': 'error', 'code': 'usageLimitExceeded'}) + '\n')
            raise RuntimeError('CLI exited')
        atomic_json(output, {'summary': 'done'})
    worker.run_process = run
    receipt = runtime.run_ai_step(worker, 'schema', output, tmp_path / 'implementation', 'prompt', 10)
    assert receipt['model'] == 'secondary'
    assert calls[0][-1] == 'primary' and calls[1][-1] == 'secondary'
    assert marker.read_text() == 'PRESERVED = True\n'
    records = json.loads((tmp_path / 'implementation.providers.json').read_text())
    assert [r['state'] for r in records] == ['failed', 'completed']
    assert len(router.failures) == len(router.successes) == 1


def test_successful_reset_can_retry_same_primary(tmp_path, monkeypatch):
    router, output, worker = setup_runtime(tmp_path, monkeypatch, [codex_route()])
    calls = []
    def run(command, prefix, prompt, timeout):
        calls.append(command)
        if len(calls) == 1:
            Path(str(prefix) + '.stdout.log').write_text(json.dumps({
                'type': 'error', 'code': 'usageLimitExceeded'}) + '\n')
            raise RuntimeError('CLI exited')
        atomic_json(output, {'summary': 'done'})
    worker.run_process = run
    runtime.run_ai_step(worker, 'schema', output, tmp_path / 'implementation', 'prompt', 10)
    assert len(calls) == 2 and len(router.successes) == 1


def test_timeout_does_not_spin_through_models_or_consume_reset(tmp_path, monkeypatch):
    router, output, worker = setup_runtime(tmp_path, monkeypatch, [codex_route(), codex_route('secondary')])
    def run(*args):
        raise TimeoutError('time budget')
    worker.run_process = run
    with pytest.raises(TimeoutError):
        runtime.run_ai_step(worker, 'schema', output, tmp_path / 'implementation', 'prompt', 10)
    assert router.failures[0][1]['kind'] == 'timeout'
    assert not output.exists() and not router.successes


def test_release_review_cannot_be_silently_signed_by_claude(tmp_path, monkeypatch):
    router, output, worker = setup_runtime(tmp_path, monkeypatch,
        [{'id': 'claude', 'provider': 'claude', 'model': 'claude-opus-5-5'}])
    worker.run_process = lambda *a, **k: pytest.fail('Release must wait for Codex')
    with pytest.raises(runtime.ProviderUnavailable):
        runtime.run_ai_step(worker, 'schema', output, tmp_path / 'release-review', 'prompt', 10,
                            sandbox='read-only', require_codex=True)
    assert not output.exists()


def test_unknown_control_operation_is_rejected(tmp_path):
    transport = runtime.RuntimeTransport(tmp_path, {}, None)
    with pytest.raises(ValueError):
        transport.call('account/sendAddCreditsNudgeEmail', {}, 1)


def test_no_unapproved_provider_or_model_probe(tmp_path):
    transport = runtime.RuntimeTransport(tmp_path, {}, None)
    with pytest.raises(ValueError):
        transport.probe('claude', 'arbitrary', 1, 'test')


def test_status_read_does_not_initialize_control_or_write(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'ProviderRuntime', lambda *a: pytest.fail('read-only status'))
    assert runtime.provider_status(tmp_path)['state'] == 'not_started'
    assert not (tmp_path / 'data').exists()


def test_worker_status_cannot_hide_a_stopped_provider_monitor(tmp_path, monkeypatch):
    atomic_json(tmp_path / 'data/development/provider-recovery/status.json',
                {'state': 'primary', 'checked_at_epoch': 1000})
    monkeypatch.setattr(runtime.time, 'time', lambda: 1300)
    observed = runtime.provider_status(tmp_path)
    assert observed['state'] == 'stale' and observed['last_observed_state'] == 'primary'


def test_failed_attempt_report_cannot_be_credited_to_empty_fallback(tmp_path, monkeypatch):
    router, output, worker = setup_runtime(tmp_path, monkeypatch,
                                          [codex_route(), codex_route('secondary')])
    calls = []
    def run(command, prefix, prompt, timeout):
        calls.append(command)
        if len(calls) == 1:
            atomic_json(output, {'summary': 'partial old provider result'})
            Path(str(prefix) + '.stdout.log').write_text(json.dumps({
                'type': 'error', 'code': 'usageLimitExceeded'}) + '\n')
            raise RuntimeError('CLI failed after output')
        # A zero exit without a NEW structured result is not success.
    worker.run_process = run
    with pytest.raises(ValueError, match='no structured result'):
        runtime.run_ai_step(worker, 'schema', output, tmp_path / 'implementation', 'prompt', 10)
    assert not output.exists() and not router.successes
    saved = tmp_path / 'implementation.provider-2.prior-output.json'
    assert json.loads(saved.read_text())['summary'] == 'partial old provider result'


def test_control_startup_counts_against_original_step_deadline(tmp_path, monkeypatch):
    router, output, worker = setup_runtime(tmp_path, monkeypatch, [codex_route()])
    clock = [0.0]
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock[0])
    def startup(root):
        clock[0] = 12.0
        return SimpleNamespace(config={}, router=lambda: router)
    monkeypatch.setattr(runtime, 'ProviderRuntime', startup)
    worker.run_process = lambda *a, **k: pytest.fail('Step deadline already expired')
    with pytest.raises(TimeoutError):
        runtime.run_ai_step(worker, 'schema', output, tmp_path / 'implementation', 'prompt', 10)
    assert not output.exists()


def test_no_probe_is_launched_with_insufficient_remaining_budget(tmp_path, monkeypatch):
    router, output, worker = setup_runtime(tmp_path, monkeypatch, [None])
    router.policy = {'transport_timeout_seconds': 35}
    router.periodic_tick = lambda **k: pytest.fail('Probe cannot finish inside step budget')
    worker.run_process = lambda *a, **k: pytest.fail('No verified provider')
    with pytest.raises(runtime.ProviderUnavailable):
        runtime.run_ai_step(worker, 'schema', output, tmp_path / 'implementation', 'prompt', 10)


@pytest.mark.parametrize('change', [{}, {'subtype': 'error'}, {'terminal_reason': 'error'},
    {'permission_denials': ['blocked']}, {'modelUsage': {'claude-opus-5-5': {}}},
    {'subagent_stats': {'spawned': 1}}])
def test_claude_availability_requires_clean_exact_model_envelope(tmp_path, monkeypatch, change):
    from betting_engine import ai_delegation
    envelope = {'type': 'result', 'is_error': False, 'subtype': 'success',
        'terminal_reason': 'completed', 'permission_denials': [], 'result': 'OK',
        'modelUsage': {'claude-opus-5-5': {'canonicalModel': 'claude-opus-5-5'}}}
    envelope.update(change)
    monkeypatch.setattr(ai_delegation, 'invoke', lambda *a, **k: {
        'returncode': 0, 'stdout': json.dumps(envelope), 'stderr': ''})
    transport = runtime.RuntimeTransport(tmp_path, {'claude_binary': 'claude'}, None)
    operation = str(uuid.uuid4())
    result = transport.probe('claude', 'claude-opus-5-5', 1, operation)
    assert (result['status'] == 'available') is (change == {})
    receipt = tmp_path / 'data/development/provider-recovery/claude-probes' / (operation + '.json')
    import hashlib
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == result['evidence_sha256']


def test_revoked_permissions_override_still_fresh_cached_catalog(tmp_path, monkeypatch):
    routes = [{'id': 'gpt', 'model': 'gpt-test', 'provider': 'codex', 'family': 'gpt', 'capability_sha256': 'a' * 64},
              {'id': 'claude', 'model': 'claude-opus-5-5', 'provider': 'claude', 'family': 'opus-5.5', 'capability_sha256': 'a' * 64}]
    folder = tmp_path / 'provider'
    original = {'version': 'provider-recovery-policy-v1', 'account_scope_sha256': 'b' * 64,
        'capability_evidence_sha256': 'a' * 64, 'verified_at': 100, 'expires_at': 200,
        'reset_supported': True, 'allow_temporary_claude': True, 'routes': routes}
    atomic_json(folder / 'verified-policy.json', original)
    instance = runtime.ProviderRuntime.__new__(runtime.ProviderRuntime)
    instance.root, instance.folder = tmp_path, folder
    instance.config = {'routes': routes, 'allow_temporary_claude': False,
                       'consume_earned_reset_on_weekly_limit': False}
    instance.transport = None
    instance.control = SimpleNamespace(capabilities=lambda **k: pytest.fail('Cached catalog is still fresh'))
    monkeypatch.setattr(runtime.time, 'time', lambda: 150)
    router = instance.router()
    assert router.policy['reset_supported'] is False
    assert router.policy['allow_temporary_claude'] is False
    assert [r['provider'] for r in router.policy['routes']] == ['codex']
    assert json.loads((folder / 'verified-policy.json').read_text()) == original
