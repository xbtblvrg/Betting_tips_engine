"""Bounded installed Codex app-server control; no routing or automatic reset policy.

Each operation owns one stdio process group. Only an explicit consume_reset call
can redeem a banked credit; the caller durably owns its UUID and account scope.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time
import uuid

MAX_BYTES = 2 * 1024**2
MAX_LINE = 1024**2
KNOWN_PROTOCOL_VERSION = '0.157.1'
_ALLOWED = {'initialize', 'initialized', 'account/rateLimits/read', 'model/list',
            'account/rateLimitResetCredit/consume', 'thread/start', 'turn/start'}
_RESET_OUTCOMES = {'reset', 'alreadyRedeemed', 'nothingToReset', 'noCredit'}
_PROBE_CONFIG = {
    'approval_policy': 'never', 'web_search': 'disabled', 'mcp_servers': {},
    'project_doc_max_bytes': 0,
    'permissions': {'quant_probe': {'filesystem': {':minimal': 'read', ':workspace_roots': {'.': 'read'}},
                                   'network': {'enabled': False}}},
    'features': {name: False for name in ('hooks', 'plugins', 'apps', 'shell_tool',
        'unified_exec', 'multi_agent', 'computer_use', 'browser_use', 'browser_use_external',
        'image_generation', 'view_image', 'sleep_tool', 'goals', 'code_mode', 'code_mode_host')},
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _decode(body):
    return json.loads(body, object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def _seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 120:
        raise ValueError('timeout_seconds must be finite and in (0,120]')
    return float(value)


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('operation/idempotency key must be a canonical UUID')
    return value


def _safe(value):
    if isinstance(value, dict):
        return {k: ({'sha256': _hash(v)} if k in ('accountId', 'creditId', 'codexHome', 'email')
            or (k == 'id' and 'resetType' in value) else _safe(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


class ControlError(RuntimeError):
    """Typed bounded diagnostic. Raw provider text/auth are never interpolated."""
    def __init__(self, kind, *, rpc_code=None, codex_error_info=None):
        super().__init__(kind)
        self.kind = kind
        self.rpc_code = rpc_code
        self.codex_error_info = codex_error_info
        self.evidence_sha256 = None


class _Session:
    def __init__(self, binary, cwd, timeout_seconds, *, probe=False):
        self.deadline = time.monotonic() + _seconds(timeout_seconds)
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.queue = deque(); self.pending = bytearray(); self.requests = []
        self.streams = {'stdout': hashlib.sha256(), 'stderr': hashlib.sha256()}
        self.counts = {'stdout': 0, 'stderr': 0}; self.next_id = 1
        self.selector = selectors.DefaultSelector(); self.process = None
        self.init = {}; self.cleanup = []
        env = {k: os.environ[k] for k in ('HOME', 'PATH', 'USER', 'LOGNAME', 'TMPDIR', 'LANG') if k in os.environ}
        argv = [str(binary), 'app-server', '--stdio']
        # Prevent project discovery; no user-controlled prompt/config enters here.
        if probe:
            argv += ['-c', 'mcp_servers={}', '-c', 'web_search="disabled"']
            argv += [arg for name in _PROBE_CONFIG['features'] for arg in ('--disable', name)]
        try:
            self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            for stream, name in ((self.process.stdout, 'stdout'), (self.process.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                self.selector.register(stream, selectors.EVENT_READ, name)
            os.set_blocking(self.process.stdin.fileno(), False)
            self.init = self.call('initialize', {'clientInfo': {'name': 'quant_provider_control', 'version': '1'},
                'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized'})
        except BaseException as exc:
            self.close()
            if isinstance(exc, ControlError):
                exc.startup_evidence = self.evidence()
            raise

    def _remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ControlError('timeout')
        return min(remaining, .1)

    def send(self, message):
        if message.get('method') not in _ALLOWED:
            raise ControlError('method_not_allowed')
        body = memoryview(_json(message) + b'\n')
        if len(body) > 64 * 1024:
            raise ControlError('request_too_large')
        self.requests.append({'method': message['method'], 'id': message.get('id')})
        self.selector.register(self.process.stdin, selectors.EVENT_WRITE, 'stdin')
        try:
            while body:
                for key, _ in self.selector.select(self._remaining()):
                    if key.data == 'stdin':
                        try:
                            count = os.write(key.fileobj.fileno(), body)
                        except BlockingIOError:
                            continue
                        except BrokenPipeError as exc:
                            raise ControlError('process_exited') from exc
                        body = body[count:]
                    else:
                        self._read(key)
        finally:
            self.selector.unregister(self.process.stdin)

    def _read(self, key):
        try:
            chunk = os.read(key.fileobj.fileno(), 8192)
        except BlockingIOError:
            return
        if not chunk:
            self.selector.unregister(key.fileobj)
            return
        self.counts[key.data] += len(chunk); self.streams[key.data].update(chunk)
        if sum(self.counts.values()) > MAX_BYTES:
            raise ControlError('output_limit')
        if key.data != 'stdout':
            return
        self.pending.extend(chunk)
        while b'\n' in self.pending:
            line, _, remaining = self.pending.partition(b'\n'); self.pending[:] = remaining
            if len(line) > MAX_LINE:
                raise ControlError('line_limit')
            try:
                message = _decode(line)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise ControlError('invalid_json') from exc
            if not isinstance(message, dict):
                raise ControlError('invalid_message')
            if 'method' in message and 'id' in message:
                # Control operations never authorize server-side approval/tool requests.
                raise ControlError('unexpected_server_request')
            self.queue.append(message)
        if len(self.pending) > MAX_LINE:
            raise ControlError('line_limit')

    def next_message(self):
        while not self.queue:
            self._remaining()
            if not self.selector.get_map():
                raise ControlError('truncated_json' if self.pending else 'process_exited')
            for key, _ in self.selector.select(self._remaining()):
                self._read(key)
        return self.queue.popleft()

    def call(self, method, params):
        ident = self.next_id; self.next_id += 1
        self.send({'id': ident, 'method': method, 'params': params})
        deferred = []
        while True:
            message = self.next_message()
            if 'id' not in message:
                deferred.append(message)
                continue
            if type(message['id']) is not int or message['id'] != ident:
                raise ControlError('unexpected_response_id')
            if ('error' in message) == ('result' in message):
                raise ControlError('invalid_response')
            if 'error' in message:
                error = message['error']
                raise ControlError('rpc_error', rpc_code=error.get('code') if isinstance(error, dict) else None)
            if not isinstance(message['result'], dict):
                raise ControlError('invalid_result')
            self.queue.extendleft(reversed(deferred))
            return message['result']

    def close(self):
        if self.process is not None:
            # Kill our process group even if its leader already exited: inherited
            # pipe-holding descendants must not outlive an operation deadline.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.process.pid, sig)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    self.cleanup.append({'signal': sig.name, 'errno': exc.errno})
                try:
                    self.process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    pass
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
        self.selector.close()

    def evidence(self):
        return {'started_at': self.started_at, 'completed_at': datetime.now(timezone.utc).isoformat(),
            'requests': self.requests, 'streams': {name: {'bytes': self.counts[name], 'sha256': value.hexdigest()}
                for name, value in self.streams.items()}, 'cleanup_errors': self.cleanup,
            'returncode': self.process.returncode if self.process else None, 'initialize': _safe(self.init)}


def _limits(session):
    value = session.call('account/rateLimits/read', {'excludeResetCreditDetails': False, 'supportsLunaReserve': False})
    if not isinstance(value.get('rateLimits'), dict) or (value.get('ordinaryUsageAllowed') is not None and type(value.get('ordinaryUsageAllowed')) is not bool):
        raise ControlError('invalid_rate_limits')
    account = value.pop('accountId', None)
    value['account_scope_sha256'] = _hash(account) if isinstance(account, str) and account else None
    return value


def _models(session):
    data = []; cursor = None; seen = set()
    for _ in range(10):
        page = session.call('model/list', {'includeHidden': True, 'limit': 100, 'cursor': cursor})
        rows = page.get('data')
        if not isinstance(rows, list) or len(rows) > 100:
            raise ControlError('invalid_models')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not isinstance(row.get('model'), str):
                raise ControlError('invalid_models')
            if row['id'] in seen:
                raise ControlError('duplicate_model')
            seen.add(row['id']); data.append(row)
        cursor = page.get('nextCursor')
        if cursor is None:
            return {'data': data, 'nextCursor': None}
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
            raise ControlError('invalid_cursor')
    raise ControlError('model_page_limit')


class CodexControl:
    def __init__(self, binary, evidence_dir=None):
        self.binary = Path(binary).absolute()
        self.evidence_dir = Path(evidence_dir).absolute() if evidence_dir is not None else None

    def _run(self, action, operation, *, timeout_seconds=30, operation_id=None, probe=False):
        _seconds(timeout_seconds); operation_id = _uuid(operation_id or str(uuid.uuid4()))
        session = None; result = None; failure = None
        with tempfile.TemporaryDirectory(prefix='quant-control-') as cwd:
            try:
                session = _Session(self.binary, cwd, timeout_seconds, probe=probe)
                result = operation(session)
            except ControlError as exc:
                failure = exc
            except (OSError, ValueError, TypeError, KeyError) as exc:
                failure = ControlError('transport_error')
            finally:
                if session is not None:
                    session.close()
            record = {'version': 'codex-control-v1', 'action': action, 'operation_id': operation_id,
                'transport': session.evidence() if session is not None else getattr(failure, 'startup_evidence', {'startup_failed': True}),
                'result': _safe(result), 'failure': {'kind': failure.kind, 'rpc_code': failure.rpc_code,
                    'codex_error_info': failure.codex_error_info} if failure else None}
            evidence_body = _json(record) + b'\n'
            digest = hashlib.sha256(evidence_body).hexdigest()
            if self.evidence_dir is not None:
                if any(p.is_symlink() for p in (self.evidence_dir, *self.evidence_dir.parents)):
                    raise ControlError('unsafe_evidence_directory')
                self.evidence_dir.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.evidence_dir / (operation_id + '-' + digest + '.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as out:
                    out.write(evidence_body); out.flush(); os.fsync(out.fileno())
            if failure:
                failure.evidence_sha256 = digest
                raise failure
            return {**result, 'evidence_sha256': digest, 'operation_id': operation_id}

    def read_limits(self, *, timeout_seconds=30):
        return self._run('read_limits', _limits, timeout_seconds=timeout_seconds)

    def list_models(self, *, timeout_seconds=30):
        return self._run('list_models', _models, timeout_seconds=timeout_seconds)

    def capabilities(self, *, timeout_seconds=30, ttl_seconds=300):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError('ttl_seconds must be between1 and3600')
        def operation(session):
            limits = _limits(session); models = _models(session); now = datetime.now(timezone.utc)
            agent = session.init.get('userAgent', '')
            protocol_known = isinstance(agent, str) and ('/' + KNOWN_PROTOCOL_VERSION + ' ') in agent
            return {'account_scope_sha256': limits['account_scope_sha256'], 'models': models['data'],
                'reset_supported': protocol_known and isinstance(limits.get('rateLimitResetCredits'), dict),
                'protocol_version': KNOWN_PROTOCOL_VERSION if protocol_known else None,
                'limits': limits, 'verified_at': now.isoformat(), 'expires_at': (now + timedelta(seconds=ttl_seconds)).isoformat()}
        value = self._run('capabilities', operation, timeout_seconds=timeout_seconds)
        value['capability_evidence_sha256'] = value['evidence_sha256']
        return value

    def consume_reset(self, idempotency_key, credit_id=None, *, expected_account_scope_sha256=None, timeout_seconds=30):
        _uuid(idempotency_key)
        if not isinstance(expected_account_scope_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_account_scope_sha256):
            raise ValueError('expected account scope is required for reset')
        if credit_id is not None and (not isinstance(credit_id, str) or not 0 < len(credit_id) <= 1024):
            raise ValueError('invalid credit_id')
        def operation(session):
            limits = _limits(session)
            if limits['account_scope_sha256'] != expected_account_scope_sha256:
                raise ControlError('account_scope_changed')
            params = {'idempotencyKey': idempotency_key}
            if credit_id is not None:
                params['creditId'] = credit_id
            result = session.call('account/rateLimitResetCredit/consume', params)
            if result.get('outcome') not in _RESET_OUTCOMES:
                raise ControlError('unknown_reset_outcome')
            return result
        return self._run('consume_reset', operation, timeout_seconds=timeout_seconds, operation_id=idempotency_key)

    def probe_model(self, exact_model, *, provider='codex', reasoning_effort='low', timeout_seconds=30, operation_id=None):
        if provider != 'codex' or not isinstance(exact_model, str) or not re.fullmatch('[A-Za-z0-9._-]{1,100}', exact_model):
            raise ValueError('only an exact Codex model is supported')
        if reasoning_effort not in ('low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('unsupported probe reasoning effort')
        def operation(session):
            start = session.call('thread/start', {'model': exact_model, 'allowProviderModelFallback': False,
                'ephemeral': True, 'approvalPolicy': 'never', 'permissions': 'quant_probe',
                'config': _PROBE_CONFIG, 'environments': [], 'dynamicTools': [],
                'baseInstructions': 'You are a bounded availability probe. Never call tools. Reply only with the requested JSON.',
                'developerInstructions': 'No tools, file access, network browsing, subagents or commands. Return {"ok":true}.'})
            reported = start.get('model'); thread_id = start.get('thread', {}).get('id')
            if reported != exact_model or not isinstance(thread_id, str) or start.get('instructionSources'):
                raise ControlError('model_or_context_mismatch')
            turn = session.call('turn/start', {'threadId': thread_id, 'model': exact_model,
                'effort': reasoning_effort, 'environments': [],
                'input': [{'type': 'text', 'text': 'Reply exactly {"ok":true}. Do not call any tool.'}],
                'outputSchema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}})
            turn_id = turn.get('turn', {}).get('id')
            if not isinstance(turn_id, str):
                raise ControlError('invalid_turn')
            text = []
            while True:
                message = session.next_message(); params = message.get('params', {}); method = message.get('method')
                if method in ('item/started', 'item/completed') and params.get('threadId') == thread_id and params.get('turnId') == turn_id:
                    item = params.get('item', {})
                    if item.get('type') not in ('userMessage', 'agentMessage', 'reasoning'):
                        raise ControlError('unexpected_probe_tool')
                    if method == 'item/completed' and item.get('type') == 'agentMessage':
                        text.append(item.get('text', ''))
                if method == 'turn/completed' and params.get('threadId') == thread_id and params.get('turn', {}).get('id') == turn_id:
                    completed = params['turn']
                    if completed.get('status') != 'completed':
                        error = completed.get('error') or {}
                        raise ControlError('model_probe_failed', codex_error_info=error.get('codexErrorInfo'))
                    if any(not isinstance(item, dict) or item.get('type') not in ('userMessage', 'agentMessage', 'reasoning') for item in completed.get('items', [])):
                        raise ControlError('unexpected_probe_tool')
                    if not text:
                        text.extend(item.get('text', '') for item in completed.get('items', []) if item.get('type') == 'agentMessage')
                    answer = _decode(text[0]) if len(text) == 1 else None
                    if not isinstance(answer, dict) or set(answer) != {'ok'} or answer['ok'] is not True:
                        raise ControlError('invalid_probe_answer')
                    return {'status': 'available', 'reported_model': reported, 'requested_model': exact_model,
                        'reasoning_effort': reasoning_effort, 'scope': 'one ephemeral response; no coding capability or future availability guarantee'}
        try:
            return self._run('probe_model', operation, timeout_seconds=timeout_seconds, operation_id=operation_id, probe=True)
        except ControlError as exc:
            status = ('quota' if exc.codex_error_info in ('usageLimitExceeded', 'rateLimitExceeded')
                else 'auth_error' if exc.codex_error_info == 'unauthorized'
                else 'timeout' if exc.kind == 'timeout'
                else 'unavailable' if exc.kind == 'model_or_context_mismatch' or exc.codex_error_info == 'serverOverloaded'
                else 'transport_error')
            return {'status': status, 'failure_kind': exc.kind, 'reported_model': None, 'requested_model': exact_model,
                'codex_error_info': exc.codex_error_info,
                'source': 'codex_structured' if exc.codex_error_info is not None else 'transport',
                'code': exc.codex_error_info, 'evidence_sha256': exc.evidence_sha256}
