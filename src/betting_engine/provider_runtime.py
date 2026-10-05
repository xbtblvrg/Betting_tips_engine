"""Trusted controller integration for subscription-provider continuity.

This module never submits bets or changes a task deadline. Provider state lives
outside every coding workspace; the live sports workers do not depend on it.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import uuid
from datetime import datetime

from .delivery import read_json
from .io_utils import atomic_json


class ProviderUnavailable(RuntimeError):
    """A bounded retry with preserved task evidence, not an implementation fault."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def configuration(root):
    return read_json(Path(root) / 'config/provider_recovery.json')


def enabled(root):
    return configuration(root).get('enabled') is True


def provider_status(root):
    status = read_json(Path(root) / 'data/development/provider-recovery/status.json',
                       {'state': 'not_started', 'enabled': enabled(root)})
    checked = status.get('checked_at_epoch')
    if isinstance(checked, (int, float)) and not 0 <= time.time() - checked <= 240:
        return {**status, 'state': 'stale', 'last_observed_state': status.get('state')}
    return status


def failure_observation(prefix, exception):
    """Only terminal CLI envelope errors count; model prose cannot request resets."""
    evidence = []
    kind = 'timeout' if isinstance(exception, TimeoutError) else 'unknown'
    code = None
    for suffix in ('.stdout.log', '.stderr.log'):
        path = Path(str(prefix) + suffix)
        try:
            with path.open('rb') as handle:
                handle.seek(0, 2); size = handle.tell()
                handle.seek(max(0, size - 131072)); raw = handle.read(131072)
            evidence.append(hashlib.sha256(raw).hexdigest())
        except OSError:
            continue
        for line in raw.splitlines():
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(row, dict) or row.get('type') not in ('error', 'turn.failed'):
                continue
            error = row.get('error') if isinstance(row.get('error'), dict) else row
            observed = error.get('code') or error.get('error_code') or error.get('codexErrorInfo')
            if isinstance(observed, dict):
                observed = observed.get('type')
            if observed in ('usageLimitExceeded', 'rateLimitExceeded'):
                kind, code = 'quota', observed
            elif str(error.get('message', '')).startswith(("You've hit your usage limit", 'Usage limit exceeded', 'Your weekly limit has been reached')):
                # Terminal CLI error envelope, never assistant/task prose.
                kind, code = 'quota', 'usageLimitExceeded'
            elif observed in ('unauthorized', 'authenticationError'):
                kind, code = 'auth_error', observed
    return {'kind': kind, 'code': code, 'source': 'codex_structured' if code else 'transport',
            'evidence_sha256': digest({'log_hashes': evidence, 'exception': type(exception).__name__})}


class RuntimeTransport:
    def __init__(self, root, config, control):
        self.root, self.config, self.control = Path(root), config, control

    def call(self, method, params, timeout_seconds):
        if method == 'account/rateLimits/read':
            return self.control.read_limits(timeout_seconds=timeout_seconds)
        if method == 'account/rateLimitResetCredit/consume':
            policy = read_json(self.root / 'data/development/provider-recovery/verified-policy.json')
            return self.control.consume_reset(params['idempotencyKey'],
                credit_id=params.get('creditId'), timeout_seconds=timeout_seconds,
                expected_account_scope_sha256=policy.get('account_scope_sha256'))
        raise ValueError('Unsupported provider control operation')

    def read_limits(self, timeout_seconds):
        return self.control.read_limits(timeout_seconds=timeout_seconds)

    def consume_reset(self, idempotency_key, credit_id=None, *, timeout_seconds,
                      expected_account_scope_sha256):
        return self.control.consume_reset(idempotency_key, credit_id,
            timeout_seconds=timeout_seconds,
            expected_account_scope_sha256=expected_account_scope_sha256)

    def probe_model(self, model, *, provider, reasoning_effort=None,
                    timeout_seconds, operation_id):
        return self.probe(provider, model, timeout_seconds, operation_id,
                          reasoning_effort=reasoning_effort)

    def probe(self, provider, model, timeout_seconds, operation_id, reasoning_effort=None):
        if provider == 'codex':
            return self.control.probe_model(model, timeout_seconds=timeout_seconds,
                reasoning_effort=reasoning_effort or 'low', operation_id=operation_id)
        if provider == 'agy':
            from .ai_delegation import invoke, file_lock, decode
            with file_lock(self.root / 'data/development/ai-locks/agy.lock', blocking=False) as acquired:
                if not acquired:
                    raise ProviderUnavailable('Antigravity capacity is in use; retry later')
                agy_binary = self.config.get('agy_binary', '/Users/belavarga/.local/bin/agy')
                result = invoke('agy', {'binary': agy_binary,
                    'model': model, '_protected_root': str(self.root)},
                    'Reply exactly OK. Do not call any tools.', timeout=timeout_seconds)
            status_code, answer = decode('agy', result, expected_model=model)
            ok = (status_code == 'received_unreviewed' and answer is not None and answer.strip().startswith('OK'))
            if str(uuid.UUID(operation_id)) != operation_id:
                raise ValueError('Invalid provider probe operation ID')
            record = {'operation_id': operation_id, 'provider': provider, 'requested_model': model,
                      'reported_model': model if ok else None, 'available': ok,
                      'checked_at_epoch': time.time(), 'returncode': result.get('returncode'),
                      'stdout_sha256': hashlib.sha256(str(result.get('stdout', '')).encode()).hexdigest(),
                      'stderr_sha256': hashlib.sha256(str(result.get('stderr', '')).encode()).hexdigest()}
            proof = digest(record)
            archive = self.root / 'data/development/provider-recovery/agy-probes'
            archive.mkdir(parents=True, exist_ok=True)
            with (archive / (operation_id + '.json')).open('x') as handle:
                handle.write(json.dumps(record, sort_keys=True))
            return {'status': 'available' if ok else 'unavailable',
                    'reported_model': model if ok else None, 'evidence_sha256': proof}
        if provider != 'claude' or model != 'claude-opus-5-5':
            raise ValueError('Unapproved fallback provider')
        from .ai_delegation import invoke, file_lock
        with file_lock(self.root / 'data/development/ai-locks/claude.lock', blocking=False) as acquired:
            if not acquired:
                raise ProviderUnavailable('Claude capacity is in use; retry later')
            result = invoke('claude', {'binary': self.config['claude_binary'],
                'model': model, '_protected_root': str(self.root)},
                'Reply exactly OK. Do not call any tools.', timeout=timeout_seconds)
        try:
            output = json.loads(result.get('stdout', ''))
            models = output.get('modelUsage', {})
            ok = (result.get('returncode') == 0 and result.get('state') is None
                  and not result.get('cleanup_errors') and output.get('is_error') is False
                  and output.get('type') == 'result' and output.get('subtype') == 'success'
                  and output.get('terminal_reason') == 'completed'
                  and output.get('permission_denials') == []
                  and output.get('subagent_stats', {}).get('spawned', 0) == 0
                  and output.get('result', '').strip() == 'OK'
                  and list(models) == [model] and models[model].get('canonicalModel') == model)
        except (ValueError, TypeError, AttributeError):
            ok = False
        if str(uuid.UUID(operation_id)) != operation_id:
            raise ValueError('Invalid provider probe operation ID')
        record = {'operation_id': operation_id, 'provider': provider, 'requested_model': model,
                  'reported_model': model if ok else None, 'available': ok,
                  'checked_at_epoch': time.time(), 'returncode': result.get('returncode'),
                  'stdout_sha256': hashlib.sha256(str(result.get('stdout', '')).encode()).hexdigest(),
                  'stderr_sha256': hashlib.sha256(str(result.get('stderr', '')).encode()).hexdigest()}
        proof = digest(record)
        archive = self.root / 'data/development/provider-recovery/claude-probes'
        archive.mkdir(parents=True, exist_ok=True)
        with (archive / (operation_id + '.json')).open('x') as handle:
            handle.write(json.dumps(record, sort_keys=True))
        return {'status': 'available' if ok else 'unavailable',
                'reported_model': model if ok else None, 'evidence_sha256': proof}


class ProviderRuntime:
    def __init__(self, root):
        from .codex_control import CodexControl
        self.root = Path(root)
        self.folder = self.root / 'data/development/provider-recovery'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.config = configuration(self.root)
        runner = read_json(self.root / 'config/development_runner.json')
        self.control = CodexControl(runner['codex_binary'], evidence_dir=self.folder / 'control')
        self.transport = RuntimeTransport(self.root, self.config, self.control)

    def router(self, refresh=False):
        from .provider_recovery import ProviderRouter, validate_policy
        now = time.time()
        policy_path = self.folder / 'verified-policy.json'
        policy = read_json(policy_path)
        if refresh or not policy or policy.get('expires_at', 0) <= now:
            try:
                cap = self.control.capabilities(timeout_seconds=self.config.get('control_timeout_seconds', 15),
                    ttl_seconds=self.config.get('capability_ttl_seconds', 3600))
            except Exception as exc:
                if not policy or self.config.get('allow_temporary_claude') is not True:
                    raise
                # Preserve old timestamps. Expired authority permits only the
                # separately configured Claude continuity route; no reset/GPT.
                configured = {(r['id'], r['provider'], r['model']) for r in self.config['routes']}
                if any((r['id'], r['provider'], r['model']) not in configured for r in policy.get('routes', [])):
                    raise
                error = {'at_epoch': now, 'exception_type': type(exc).__name__,
                    'state': 'catalog_refresh_failed', 'previous_expires_at': policy.get('expires_at')}
                atomic_json(self.folder / 'errors' / (str(uuid.uuid4()) + '.json'), error)
                atomic_json(self.folder / 'catalog-error.json', error)
            else:
                models = {r['model'] for r in cap['models']}
                routes = [{**r, 'capability_sha256': cap['evidence_sha256']}
                    for r in self.config['routes'] if
                    (r['provider'] == 'claude' and self.config.get('allow_temporary_claude') is True)
                    or (r['provider'] == 'agy' and self.config.get('allow_temporary_agy', True) is True)
                    or (r['provider'] == 'codex' and r['model'] in models)]
                policy = {'version': 'provider-recovery-policy-v1',
                    'account_scope_sha256': cap['account_scope_sha256'],
                    'capability_evidence_sha256': cap['evidence_sha256'],
                    'verified_at': datetime.fromisoformat(cap['verified_at']).timestamp(),
                    'expires_at': datetime.fromisoformat(cap['expires_at']).timestamp(),
                    'reset_supported': cap['reset_supported'] and self.config.get('consume_earned_reset_on_weekly_limit') is True,
                    'allow_temporary_claude': self.config.get('allow_temporary_claude') is True,
                    'allow_temporary_agy': self.config.get('allow_temporary_agy', True) is True,
                    'probe_interval_seconds': self.config.get('preferred_probe_seconds', 300),
                    'transport_timeout_seconds': self.config.get('probe_timeout_seconds', 60),
                    'routes': routes}
                atomic_json(policy_path, policy)
        # Explicit authorization revocation applies immediately, even while
        # the last successful catalog is still within its freshness window.
        configured = {(r['id'], r['provider'], r['model']) for r in self.config['routes']}
        policy = {**policy, 'routes': [r for r in policy['routes']
            if (r['id'], r['provider'], r['model']) in configured
            and (r['provider'] != 'claude' or self.config.get('allow_temporary_claude') is True)
            and (r['provider'] != 'agy' or self.config.get('allow_temporary_agy', True) is True)],
            'allow_temporary_claude': self.config.get('allow_temporary_claude') is True,
            'allow_temporary_agy': self.config.get('allow_temporary_agy', True) is True,
            'reset_supported': policy['reset_supported'] and self.config.get('consume_earned_reset_on_weekly_limit') is True}
        policy = validate_policy(policy)
        return ProviderRouter(self.folder / policy['account_scope_sha256'], policy, self.transport)

    def tick(self):
        policy = read_json(self.folder / 'verified-policy.json')
        refresh = time.time() >= policy.get('verified_at', 0) + 900
        router = self.router(refresh=refresh)
        router.periodic_tick(max_actions=4)
        status = {**router.status(), 'enabled': True, 'checked_at_epoch': time.time(),
                  'release_review_requires_codex': True}
        selected = status.get('selection')
        status['state'] = ('unavailable' if not selected else
            'claude_fallback' if selected['provider'] == 'claude' else
            'agy_fallback' if selected['provider'] == 'agy' else
            'primary' if selected['id'] == router.policy['routes'][0]['id'] else 'gpt_fallback')
        atomic_json(self.folder / 'status.json', status)
        return status


def start_monitor(root, stop):
    """One bounded control thread per scheduler; task heartbeats never await RPC."""
    if not enabled(root):
        return None
    def loop():
        folder = Path(root) / 'data/development/provider-recovery'
        while not stop.is_set():
            try:
                if not enabled(root):
                    return
                runtime = ProviderRuntime(root)
                runtime.tick()
            except Exception as exc:
                error = {
                    'at_epoch': time.time(), 'exception_type': type(exc).__name__,
                    'state': 'retry_pending', 'next_probe_seconds': 60}
                atomic_json(folder / 'errors' / (str(uuid.uuid4()) + '.json'), error)
                atomic_json(folder / 'monitor-error.json', error)
                atomic_json(folder / 'status.json', {**read_json(folder / 'status.json'),
                    'state': 'control_retry_pending', 'last_monitor_error': error})
            stop.wait(60)
    thread = threading.Thread(target=loop, name='provider-recovery', daemon=True)
    thread.start()
    return thread


def run_ai_step(worker, schema, output, prefix, prompt, timeout=1200,
                sandbox='workspace-write', require_codex=False):
    """Resume a quota-interrupted step in-place, with separate provider evidence."""
    if not enabled(worker.root):
        return worker.run_process(worker.command(schema, output, sandbox), prefix, prompt, timeout)
    deadline = time.monotonic() + timeout
    runtime = ProviderRuntime(worker.root)
    startup_remaining = deadline - time.monotonic()
    if startup_remaining <= 0:
        raise TimeoutError('Development step exceeded its execution time limit')
    runtime.config['control_timeout_seconds'] = min(
        runtime.config.get('control_timeout_seconds', 15), startup_remaining)
    router = runtime.router()
    tried = {}
    records = []
    prefix, output = Path(prefix), Path(output)
    try:
        while time.monotonic() < deadline:
            route = router.get_selection()
            progress = None
            if not route or tried.get(route['id'], 0) >= 2 or (require_codex and route['provider'] != 'codex'):
                # One bounded action at a time. Retain the original step budget
                # across startup, reset, fallback and subprocess execution.
                reserve = getattr(router, 'policy', {}).get('transport_timeout_seconds', 35) + 15
                if deadline - time.monotonic() <= reserve:
                    raise ProviderUnavailable('Provider unavailable within remaining step budget; retry retains all prior evidence')
                progress = router.periodic_tick(max_actions=1)
                route = router.get_selection()
            if not route or tried.get(route['id'], 0) >= 2 or (require_codex and route['provider'] != 'codex'):
                if isinstance(progress, dict) and progress.get('actions', 0) > 0:
                    continue
                raise ProviderUnavailable('Provider unavailable; retained step will retry after the next bounded availability check')
            tried[route['id']] = tried.get(route['id'], 0) + 1
            step = prefix.with_name(prefix.name + '.provider-' + str(len(records) + 1))
            record = {'provider': route['provider'], 'model': route['model'],
                      'started_at_epoch': time.time(), 'log_prefix': step.name,
                      'phase': prefix.name, 'state': 'running'}
            records.append(record)
            atomic_json(Path(str(prefix) + '.providers.json'), records)
            if output.exists():
                if output.is_symlink() or not output.is_file():
                    raise ValueError('Unsafe previous AI output')
                prior = Path(str(step) + '.prior-output.json')
                if prior.exists():
                    raise ValueError('Prior output archive already exists')
                output.rename(prior)
                record['prior_output_sha256'] = hashlib.sha256(prior.read_bytes()).hexdigest()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Development step exceeded its execution time limit')
            try:
                if route['provider'] == 'codex':
                    command = worker.command(schema, output, sandbox)
                    command[command.index('-m') + 1] = route['model']
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Development step exceeded its execution time limit')
                    worker.run_process(command, step, prompt, remaining)
                elif route['provider'] == 'agy':
                    from .claude_development import prepare_step, finish_step, apply_edits
                    from .ai_delegation import file_lock
                    with file_lock(worker.root / 'data/development/ai-locks/agy.lock', blocking=False) as acquired, tempfile.TemporaryDirectory(prefix='quant-agy-candidate-') as temporary:
                        if not acquired:
                            raise ProviderUnavailable('Antigravity capacity is in use; candidate retained for retry')
                        agy_binary = runtime.config.get('agy_binary', '/Users/belavarga/.local/bin/agy')
                        prepared = prepare_step(worker.root, worker.workspace, Path(temporary).resolve(),
                            getattr(worker, 'current_task', {}), prompt, read_json(schema),
                            readonly=sandbox == 'read-only', binary=agy_binary,
                            protected_paths=(*__import__('betting_engine.development', fromlist=['PROTECTED']).PROTECTED,
                                *(name for name in worker.readonly_input_paths() if name != '.git')))
                        agy_cmd = [agy_binary, '-p', prepared['stdin'],
                            '--model', route['model'], '--output-format', 'json',
                            '--sandbox', '--disable-slash-commands']
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('Development step exceeded its execution time limit')
                        worker.run_process(agy_cmd, step, None, remaining,
                            process_cwd=prepared['cwd'], process_env=prepared['env'])
                        with Path(str(step) + '.stdout.log').open('rb') as handle:
                            raw = handle.read(2 * 1024 * 1024 + 1)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ValueError('Antigravity output exceeds bounded controller limit')
                        raw_str = raw.decode('utf-8')
                        try:
                            agy_json = json.loads(raw_str)
                            response_text = agy_json.get('response', '')
                        except (ValueError, TypeError):
                            response_text = raw_str
                        result = finish_step(response_text, prepared['context'])
                        applied = apply_edits(worker.workspace, result['edits'], prepared['context'])
                        record['applied_edits'] = applied
                        atomic_json(output, result['report'])
                else:
                    from .claude_development import prepare_step, finish_step, apply_edits
                    from .ai_delegation import file_lock
                    with file_lock(worker.root / 'data/development/ai-locks/claude.lock', blocking=False) as acquired, tempfile.TemporaryDirectory(prefix='quant-claude-candidate-') as temporary:
                        if not acquired:
                            raise ProviderUnavailable('Claude capacity is in use; candidate retained for retry')
                        prepared = prepare_step(worker.root, worker.workspace, Path(temporary).resolve(),
                            getattr(worker, 'current_task', {}), prompt, read_json(schema),
                            readonly=sandbox == 'read-only', binary=runtime.config['claude_binary'],
                            protected_paths=(*__import__('betting_engine.development', fromlist=['PROTECTED']).PROTECTED,
                                *(name for name in worker.readonly_input_paths() if name != '.git')))
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('Development step exceeded its execution time limit')
                        worker.run_process(prepared['command'], step, prepared['stdin'], remaining,
                            process_cwd=prepared['cwd'], process_env=prepared['env'])
                        with Path(str(step) + '.stdout.log').open('rb') as handle:
                            raw = handle.read(2 * 1024 * 1024 + 1)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ValueError('Claude output exceeds bounded controller limit')
                        result = finish_step(raw.decode('utf-8'), prepared['context'])
                        applied = apply_edits(worker.workspace, result['edits'], prepared['context'])
                        record['applied_edits'] = applied
                        atomic_json(output, result['report'])
                report = read_json(output)
                if not report:
                    raise ValueError('AI step returned no structured result')
                record.update(state='completed', finished_at_epoch=time.time(),
                              output_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
                router.report_success(route['id'], record['output_sha256'], route['model'])
                worker._last_ai_provider = {'provider': route['provider'], 'model': route['model']}
                return record
            except ProviderUnavailable:
                record.update(state='capacity_wait', finished_at_epoch=time.time())
                raise
            except (RuntimeError, TimeoutError) as exc:
                observation = failure_observation(step, exc)
                record.update(state='failed', finished_at_epoch=time.time(), observation=observation)
                router.report_failure(route['id'], observation)
                # A provider failure cannot turn a code/test defect into a retry.
                # Only verified quota errors merit immediate in-step failover.
                if observation['kind'] not in ('quota', 'weekly_quota'):
                    raise
            except Exception as exc:
                record.update(state='failed', finished_at_epoch=time.time(),
                              exception_type=type(exc).__name__)
                raise
    finally:
        atomic_json(Path(str(prefix) + '.providers.json'), records)
    raise TimeoutError('Development step exceeded its execution time limit')
