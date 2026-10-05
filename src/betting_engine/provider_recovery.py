"""Durable provider selection; transports and verified ranking are injected.

No CLI commands, credentials, job mutation, purchasing or betting authority live
here. Control adapters must enforce the supplied hard timeout and exact account
and reported-model binding. Catalog membership alone is never availability.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import stat
import time
import uuid

VERSION = 'provider-recovery-v1'
POLICY_VERSION = 'provider-recovery-policy-v1'
FAILURES = {'weekly_quota', 'quota', 'timeout', 'auth_error', 'transport_error', 'unavailable', 'unknown'}
RESET_OUTCOMES = {'reset', 'alreadyRedeemed', 'nothingToReset', 'noCredit'}
SHA = re.compile(r'[0-9a-f]{64}')
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}')
MAX_STATE_BYTES = 2 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _pin(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError('Duplicate provider state key')
        out[key] = value
    return out


def _decode(body):
    return json.loads(body.decode('utf-8'), object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite provider state')))


def validate_policy(policy):
    """Root supplies verified order; neither names nor catalog order imply rank."""
    value = copy.deepcopy(policy)
    required = {'version', 'account_scope_sha256', 'capability_evidence_sha256', 'verified_at', 'expires_at',
                'reset_supported', 'allow_temporary_claude', 'routes'}
    optional = {'allow_temporary_agy', 'base_backoff_seconds', 'max_backoff_seconds', 'probe_interval_seconds',
                'verification_ttl_seconds', 'limits_interval_seconds', 'transport_timeout_seconds', 'max_reset_attempts'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError('Invalid provider recovery policy fields')
    if (value['version'] != POLICY_VERSION or not _pin(value['account_scope_sha256'])
            or not _pin(value['capability_evidence_sha256'])
            or not _number(value['verified_at']) or not _number(value['expires_at'])
            or value['verified_at'] < 0 or value['expires_at'] <= value['verified_at']
            or type(value['reset_supported']) is not bool or type(value['allow_temporary_claude']) is not bool
            or ('allow_temporary_agy' in value and type(value['allow_temporary_agy']) is not bool)):
        raise ValueError('Provider policy requires verified finite authority')
    routes = value['routes']
    if not isinstance(routes, list) or not 1 <= len(routes) <= 16:
        raise ValueError('Provider policy needs a bounded verified route list')
    seen = set(); models = set(); claude_seen = False; agy_seen = False
    for route in routes:
        if (not isinstance(route, dict) or not {'id', 'provider', 'family', 'model', 'capability_sha256'} <= set(route)
                or set(route) - {'id', 'provider', 'family', 'model', 'capability_sha256', 'reasoning_effort', 'limit_id'}):
            raise ValueError('Invalid route fields')
        if any(not isinstance(route[k], str) or not IDENTIFIER.fullmatch(route[k]) for k in ('id', 'model')):
            raise ValueError('Invalid provider route identifier')
        if not _pin(route['capability_sha256']) or route['id'] in seen or (route['provider'], route['model']) in models:
            raise ValueError('Duplicate or unverified provider route')
        seen.add(route['id']); models.add((route['provider'], route['model']))
        if route['provider'] == 'codex' and route['family'] == 'gpt' and not agy_seen and not claude_seen:
            pass
        elif (route['provider'] == 'agy' and route['family'] in ('gemini', 'flash', 'pro')
              and value.get('allow_temporary_agy', True) and not claude_seen):
            agy_seen = True
        elif (route['provider'] == 'claude' and route['family'] == 'opus-5.5' and route['model'] == 'claude-opus-5-5'
              and value['allow_temporary_claude'] and not claude_seen and len(seen) > 1):
            claude_seen = True
        else:
            raise ValueError('Only ordered verified GPT routes then temporary agy/Gemini or Opus 5.5 are authorized')
        for name in ('reasoning_effort', 'limit_id'):
            if route.get(name) is not None and (not isinstance(route[name], str) or not IDENTIFIER.fullmatch(route[name])):
                raise ValueError('Invalid verified route option')
    defaults = {'base_backoff_seconds': 30, 'max_backoff_seconds': 900, 'probe_interval_seconds': 300,
                'verification_ttl_seconds': 3600, 'limits_interval_seconds': 60,
                'transport_timeout_seconds': 30, 'max_reset_attempts': 5}
    bounds = {'base_backoff_seconds': (1, 300), 'max_backoff_seconds': (30, 3600),
              'probe_interval_seconds': (30, 3600), 'verification_ttl_seconds': (3600, 86400),
              'limits_interval_seconds': (10, 600), 'transport_timeout_seconds': (1, 90), 'max_reset_attempts': (1, 10)}
    for key, default in defaults.items():
        value.setdefault(key, default)
        if type(value[key]) is not int or not bounds[key][0] <= value[key] <= bounds[key][1]:
            raise ValueError('Provider policy timing outside bounded range')
    if value['base_backoff_seconds'] > value['max_backoff_seconds']:
        raise ValueError('Backoff bounds reversed')
    return value


def classify_failure(observation):
    """Use typed controller evidence, never match free-form task/error prose."""
    if not isinstance(observation, dict) or not _pin(observation.get('evidence_sha256')):
        raise ValueError('Provider failure requires a retained evidence hash')
    kind = observation.get('kind')
    if kind not in FAILURES or observation.get('source') not in {'codex_structured', 'control_snapshot', 'transport'}:
        raise ValueError('Provider failure must be explicitly classified')
    if kind in {'quota', 'weekly_quota'}:
        if observation['source'] == 'codex_structured' and observation.get('code') not in {'usageLimitExceeded', 'rateLimitExceeded'}:
            return 'unknown'
        if observation['source'] == 'transport':
            return 'unknown'
        # A structured generic quota failure starts diagnosis. Only a fresh
        # rate-limit snapshot can authorize spending an earned weekly reset.
        if kind == 'weekly_quota' and observation.get('window_duration_mins') != 10080:
            return 'quota'
    return kind


def _route_identity(route):
    return {k: route.get(k) for k in ('id', 'provider', 'family', 'model', 'reasoning_effort', 'limit_id')}


class ProviderRouter:
    """One global folder/account; every controller uses the same flock/state.

    get_selection is nonblocking even while another process performs a bounded
    control call. It returns a fresh proven route or None, never a catalog guess.
    """
    def __init__(self, folder, policy, transport, *, clock=time.time, jitter=random.random):
        self.folder = Path(os.path.abspath(folder))
        self.policy = validate_policy(policy)
        self.transport = transport
        self.clock = clock
        self.jitter = jitter
        self._ensure_directory(self.folder)
        self._ensure_directory(self.folder / 'attempts')
        self._ensure_directory(self.folder / 'inbox')
        self.path = self.folder / 'state.json'

    @staticmethod
    def _ensure_directory(path):
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if current.is_symlink():
                raise ValueError('Symlink in provider state directory')
            current.mkdir(mode=0o700, exist_ok=True)
            if not current.is_dir():
                raise ValueError('Provider state ancestor is not a directory')

    def _now(self, now):
        value = self.clock() if now is None else now
        if not _number(value) or value < 0:
            raise ValueError('Provider recovery requires a finite clock')
        return float(value)

    def _fresh_policy(self, now):
        return self.policy['verified_at'] <= now < self.policy['expires_at']

    @contextmanager
    def _locked(self):
        path = self.folder / 'routing.lock'
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Invalid provider lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                try:
                    yield True
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {'version': VERSION, 'account_scope_sha256': self.policy['account_scope_sha256'],
                    'revision': 0, 'policy_sha256': _digest(self.policy), 'routes': {},
                    'quota_episode': None, 'resets': [], 'limits': None, 'next_limits_at': 0, 'limits_failures': 0,
                    'processed_notifications': []}
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Provider state is not a regular file')
            body = stream.read(MAX_STATE_BYTES + 1)
        if len(body) > MAX_STATE_BYTES:
            raise ValueError('Provider state exceeds its bound')
        value = _decode(body)
        if (not isinstance(value, dict) or value.get('version') != VERSION
                or value.get('account_scope_sha256') != self.policy['account_scope_sha256']
                or type(value.get('revision')) is not int or value['revision'] < 0
                or not isinstance(value.get('routes'), dict) or not isinstance(value.get('resets'), list)
                or not _number(value.get('next_limits_at')) or type(value.get('limits_failures')) is not int
                or not isinstance(value.get('processed_notifications'), list)):
            raise ValueError('Provider state corrupt or belongs to another account')
        for claim in value['resets']:
            if (not isinstance(claim, dict) or str(uuid.UUID(claim['idempotency_key'])) != claim['idempotency_key']
                    or claim.get('status') not in RESET_OUTCOMES | {'pending', 'uncertain', 'unknown_exhausted'}
                    or type(claim.get('attempts')) is not int or not _number(claim.get('next_retry_at'))):
                raise ValueError('Provider reset history is invalid')
        return value

    def _save(self, state, event, now):
        state['revision'] += 1; state['policy_sha256'] = _digest(self.policy)
        # Fixed sanitized fields only. No raw provider body, prompt, credentials,
        # account ID, credit ID or exception message enters durable state/evidence.
        event = {'version': VERSION, 'revision': state['revision'], 'at': now,
                 'account_scope_sha256': self.policy['account_scope_sha256'], **event}
        event_name = f"{state['revision']:012d}-{uuid.uuid4()}.json"
        body = json.dumps(event, sort_keys=True, allow_nan=False).encode() + b'\n'
        event_fd = os.open(self.folder / 'attempts' / event_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(event_fd, 'wb') as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        body = json.dumps(state, sort_keys=True, allow_nan=False).encode() + b'\n'
        if len(body) > MAX_STATE_BYTES:
            raise ValueError('Provider state exceeds its bound')
        temporary = self.folder / ('.state-' + str(uuid.uuid4()))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(body); stream.flush(); os.fsync(stream.fileno())
            if self.path.is_symlink():
                raise ValueError('Provider state became a symlink')
            os.replace(temporary, self.path)
            parent = os.open(self.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(parent)
            finally: os.close(parent)
        finally:
            if temporary.exists(): temporary.unlink()

    def _route(self, route_id):
        for route in self.policy['routes']:
            if route['id'] == route_id: return route
        raise ValueError('Route is absent from verified policy')

    def _entry(self, state, route):
        entry = state['routes'].get(route['id'])
        if not isinstance(entry, dict) or entry.get('identity') != _route_identity(route):
            entry = {'identity': _route_identity(route), 'status': 'unknown', 'verified_at': None,
                     'next_probe_at': 0, 'failures': 0, 'evidence_sha256': None}
            state['routes'][route['id']] = entry
        return entry

    def _stale_claude(self, now):
        if now < self.policy['verified_at'] or not self.policy['allow_temporary_claude']:
            return None
        return next((r for r in self.policy['routes'] if r['provider'] == 'claude'
                     and r['model'] == 'claude-opus-5-5'), None)

    def _selected(self, state, now):
        eligible = self.policy['routes'] if self._fresh_policy(now) else [self._stale_claude(now)]
        for route in eligible:
            if route is None: continue
            entry = state['routes'].get(route['id'], {})
            verified = entry.get('verified_at')
            if (entry.get('identity') == _route_identity(route) and entry.get('status') == 'available'
                    and _pin(entry.get('evidence_sha256')) and _number(verified)
                    and 0 <= now - verified < self.policy['verification_ttl_seconds']):
                return copy.deepcopy(route)
        return None

    def _enqueue(self, route, kind, evidence, now, model=None):
        event = {'id': str(uuid.uuid4()), 'account_scope_sha256': self.policy['account_scope_sha256'],
                 'identity': _route_identity(route), 'kind': kind, 'evidence_sha256': evidence,
                 'observed_at': now, 'model': model}
        name = f"{now:020.6f}-{event['id']}.json"
        body = json.dumps(event, sort_keys=True, allow_nan=False).encode() + b'\n'
        temporary = self.folder / 'inbox' / ('.' + event['id'])
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        os.rename(temporary, self.folder / 'inbox' / name)
        fd = os.open(self.folder / 'inbox', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)

    def _notifications(self):
        events = []
        for path in sorted((self.folder / 'inbox').glob('*.json'))[:128]:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue  # Another lock owner may have atomically acknowledged it.
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('Invalid provider notification file')
                body = stream.read(8193)
            if len(body) > 8192: raise ValueError('Provider notification too large')
            event = _decode(body)
            if (not isinstance(event, dict) or set(event) != {'id', 'account_scope_sha256', 'identity', 'kind', 'evidence_sha256', 'observed_at', 'model'}
                    or event['account_scope_sha256'] != self.policy['account_scope_sha256']
                    or str(uuid.UUID(event['id'])) != event['id'] or not isinstance(event['identity'], dict)
                    or event['kind'] not in FAILURES | {'success'} or not _pin(event['evidence_sha256'])
                    or not _number(event['observed_at'])):
                raise ValueError('Invalid provider notification evidence')
            events.append((path, event))
        return events

    def _drain(self, state, now):
        for path, event in self._notifications():
            if event['id'] not in state['processed_notifications']:
                route = next((r for r in self.policy['routes'] if _route_identity(r) == event['identity']), None)
                if route is not None:
                    entry = self._entry(state, route)
                    if event['observed_at'] >= entry.get('last_observed_at', 0):
                        if event['kind'] == 'success':
                            self._success(state, route, event['evidence_sha256'], event['model'], event['observed_at'])
                        else:
                            self._failure(state, route, event['kind'], event['evidence_sha256'], event['observed_at'])
                state['processed_notifications'] = (state['processed_notifications'] + [event['id']])[-1024:]
                self._save(state, {'action': 'notification_applied' if route else 'notification_obsolete_route',
                                   'notification_id': event['id'], 'route_id': event['identity'].get('id'),
                                   'status': event['kind'], 'evidence_sha256': event['evidence_sha256']}, now)
            # State is fsynced before acknowledgment. On a crash, the retained ID
            # prevents applying an event twice. An unlink error stops the drain.
            path.unlink()

    def _overlay_pending_failures(self, state):
        state = copy.deepcopy(state)
        if len(list((self.folder / 'inbox').glob('*.json'))) > 128:
            for entry in state['routes'].values(): entry['status'] = 'unknown'
            return state  # Bounded reads cannot silently overlook a later failure.
        for _, event in self._notifications():
            if event['kind'] == 'success' or event['id'] in state['processed_notifications']:
                continue
            route = next((r for r in self.policy['routes'] if _route_identity(r) == event['identity']), None)
            if route:
                entry = self._entry(state, route)
                if event['observed_at'] >= entry.get('last_observed_at', 0):
                    entry['status'] = event['kind']
        return state

    def get_selection(self, now=None):
        now = self._now(now)
        with self._locked() as acquired:
            state = self._load()
            return self._selected(self._overlay_pending_failures(state), now)

    def status(self, now=None):
        now = self._now(now)
        with self._locked() as acquired:
            state = self._overlay_pending_failures(self._load())
            return {'version': VERSION, 'coordination': 'idle' if acquired else 'busy',
                    'policy_fresh': self._fresh_policy(now), 'selection': self._selected(state, now),
                    'quota_episode': copy.deepcopy(state['quota_episode']), 'resets': copy.deepcopy(state['resets']),
                    'routes': copy.deepcopy(state['routes']), 'next_limits_at': state['next_limits_at'],
                    'pending_notifications': len(list((self.folder / 'inbox').glob('*.json')))}

    def set_policy(self, policy, now=None):
        candidate = validate_policy(policy)
        if candidate['account_scope_sha256'] != self.policy['account_scope_sha256']:
            raise ValueError('Account changes require a separate routing state directory')
        now = self._now(now)
        with self._locked() as acquired:
            if not acquired: return False
            state = self._load(); self.policy = candidate
            for route in candidate['routes']: self._entry(state, route)
            self._save(state, {'action': 'policy_refresh', 'evidence_sha256': candidate['capability_evidence_sha256']}, now)
            return True

    def _backoff(self, failures, *, preferred_probe=False):
        raw = self.jitter()
        if not _number(raw) or not 0 <= raw <= 1:
            raise ValueError('Jitter source must return a finite fraction')
        base = self.policy['probe_interval_seconds'] if preferred_probe else self.policy['base_backoff_seconds']
        delay = min(self.policy['max_backoff_seconds'], base * 2 ** min(max(failures - 1, 0), 10))
        return max(1, min(self.policy['max_backoff_seconds'], delay * (0.8 + 0.4 * raw)))

    def _failure(self, state, route, kind, evidence, now):
        entry = self._entry(state, route); entry['failures'] += 1
        entry.update(status=kind, evidence_sha256=evidence, last_observed_at=now,
                     next_probe_at=now + self._backoff(entry['failures'], preferred_probe=kind in {'weekly_quota', 'quota'}))
        if route['provider'] == 'codex' and kind in {'weekly_quota', 'quota'}:
            episode = state['quota_episode']
            if not episode or episode.get('resolved_at') is not None:
                state['quota_episode'] = {'id': str(uuid.uuid4()), 'route_id': route['id'], 'opened_at': now,
                                          'evidence_sha256': evidence, 'resolved_at': None}
            state['next_limits_at'] = min(state['next_limits_at'], now)

    def report_failure(self, route_id, observation, now=None):
        now = self._now(now); route = self._route(route_id); kind = classify_failure(observation)
        self._enqueue(route, kind, observation['evidence_sha256'], now)
        with self._locked() as acquired:
            if acquired: self._drain(self._load(), now)
        return True  # Durable even when a different process owns the control lock.

    def _success(self, state, route, evidence, model, now):
        if not _pin(evidence) or model != route['model']:
            raise ValueError('Provider success must bind the exact verified model and retained evidence')
        entry = self._entry(state, route)
        entry.update(status='available', verified_at=now, last_observed_at=now, evidence_sha256=evidence, failures=0,
                     next_probe_at=now + self.policy['verification_ttl_seconds'])
        if route['id'] == self.policy['routes'][0]['id'] and state['quota_episode']:
            state['quota_episode']['resolved_at'] = now

    def report_success(self, route_id, evidence_sha256, model, now=None):
        now = self._now(now); route = self._route(route_id)
        if not _pin(evidence_sha256) or model != route['model']:
            raise ValueError('Provider success must bind the exact verified model and retained evidence')
        self._enqueue(route, 'success', evidence_sha256, now, model=model)
        with self._locked() as acquired:
            if acquired: self._drain(self._load(), now)
        return True

    def _read_limits(self, state, now):
        operation = str(uuid.uuid4())
        self._save(state, {'action': 'limits_started', 'operation_id': operation}, now)
        try:
            now = max(now, self._now(None))
            if not self._fresh_policy(now): raise ValueError('Control policy expired')
            response = self.transport.read_limits(timeout_seconds=self.policy['transport_timeout_seconds'])
            now = max(now, self._now(None))
            if not isinstance(response, dict) or not _pin(response.get('evidence_sha256')):
                raise ValueError('Unverified control response')
            if response.get('account_scope_sha256') != self.policy['account_scope_sha256']:
                raise ValueError('Control account mismatch')
            allowed = response.get('ordinaryUsageAllowed')
            if allowed is not None and type(allowed) is not bool: raise ValueError('Invalid ordinary usage status')
            preferred = self.policy['routes'][0]
            bucket = response.get('rateLimits')
            if preferred.get('limit_id'):
                bucket = (response.get('rateLimitsByLimitId') or {}).get(preferred['limit_id'])
            if not isinstance(bucket, dict): raise ValueError('Missing quota bucket')
            weekly = any(isinstance(bucket.get(part), dict)
                         and type(bucket[part].get('windowDurationMins')) is int
                         and bucket[part]['windowDurationMins'] == 10080
                         and type(bucket[part].get('usedPercent')) is int
                         and bucket[part]['usedPercent'] >= 100 for part in ('primary', 'secondary'))
            weekly = weekly and bucket.get('rateLimitReachedType') == 'rate_limit_reached' and allowed is not True
            credits = response.get('rateLimitResetCredits')
            count = credits.get('availableCount') if isinstance(credits, dict) else None
            if count is not None and (type(count) is not int or count < 0): raise ValueError('Invalid earned reset count')
            state['limits'] = {'observed_at': now, 'ordinary_usage_allowed': allowed, 'weekly_exhausted': weekly,
                               'available_reset_count': count, 'evidence_sha256': response['evidence_sha256']}
            state['limits_failures'] = 0; state['next_limits_at'] = now + self.policy['limits_interval_seconds']
            # Every documented reset result requires a read before another action
            # can call the preferred route recovered. Actual inference is still required.
            for claim in state['resets']:
                if claim.get('needs_refresh'):
                    claim['needs_refresh'] = False
                    self._entry(state, preferred)['next_probe_at'] = now
            outcome = 'verified'
        except Exception as exc:
            now = max(now, self._now(None))
            outcome = 'timeout' if isinstance(exc, TimeoutError) else 'control_unavailable'
            state['limits_failures'] += 1; state['next_limits_at'] = now + self._backoff(state['limits_failures'])
        self._save(state, {'action': 'limits_finished', 'operation_id': operation, 'status': outcome,
                           'evidence_sha256': state['limits']['evidence_sha256'] if outcome == 'verified' else None}, now)

    def _reset(self, state, claim, now):
        claim['attempts'] += 1; claim['status'] = 'pending'
        claim['next_retry_at'] = now + self.policy['transport_timeout_seconds'] + self._backoff(claim['attempts'])
        self._save(state, {'action': 'reset_started', 'idempotency_key': claim['idempotency_key'],
                           'attempt': claim['attempts'], 'episode_id': claim['episode_id']}, now)
        try:
            now = max(now, self._now(None))
            if not self._fresh_policy(now): raise ValueError('Reset policy expired')
            response = self.transport.consume_reset(claim['idempotency_key'], credit_id=None,
                timeout_seconds=self.policy['transport_timeout_seconds'],
                expected_account_scope_sha256=self.policy['account_scope_sha256'])
            now = max(now, self._now(None))
            if (not isinstance(response, dict) or response.get('outcome') not in RESET_OUTCOMES
                    or not _pin(response.get('evidence_sha256'))):
                raise ValueError('Unknown reset result')
            claim['status'] = response['outcome']; claim['evidence_sha256'] = response['evidence_sha256']
            claim['needs_refresh'] = True; state['next_limits_at'] = now
        except Exception:
            now = max(now, self._now(None))
            claim['next_retry_at'] = now + self._backoff(claim['attempts'])
            claim['status'] = 'unknown_exhausted' if claim['attempts'] >= self.policy['max_reset_attempts'] else 'uncertain'
            # No exception detail or raw response is persisted. SAME UUID only.
        self._save(state, {'action': 'reset_finished', 'idempotency_key': claim['idempotency_key'],
                           'attempt': claim['attempts'], 'status': claim['status'],
                           'evidence_sha256': claim.get('evidence_sha256')}, now)

    def _probe(self, state, route, now):
        entry = self._entry(state, route); operation = str(uuid.uuid4())
        entry['next_probe_at'] = now + self.policy['probe_interval_seconds']
        self._save(state, {'action': 'probe_started', 'operation_id': operation, 'route_id': route['id']}, now)
        try:
            now = max(now, self._now(None))
            if not self._fresh_policy(now) and route != self._stale_claude(now):
                raise ValueError('GPT catalogue policy expired')
            response = self.transport.probe_model(route['model'], provider=route['provider'],
                reasoning_effort=route.get('reasoning_effort'), timeout_seconds=self.policy['transport_timeout_seconds'],
                operation_id=operation)
            now = max(now, self._now(None))
            if not isinstance(response, dict) or not _pin(response.get('evidence_sha256')):
                raise ValueError('Unverified inference probe')
            if response.get('status') == 'available':
                self._success(state, route, response['evidence_sha256'], response.get('reported_model'), now)
            elif response.get('status') in FAILURES:
                kind = response['status']
                if kind in {'quota', 'weekly_quota'}:
                    kind = classify_failure({**response, 'kind': kind})
                self._failure(state, route, kind, response['evidence_sha256'], now)
            else:
                raise ValueError('Unknown inference probe result')
        except Exception as exc:
            now = max(now, self._now(None))
            self._failure(state, route, 'timeout' if isinstance(exc, TimeoutError) else 'unknown', None, now)
        self._save(state, {'action': 'probe_finished', 'operation_id': operation, 'route_id': route['id'],
                           'status': entry['status'], 'evidence_sha256': entry['evidence_sha256']}, now)

    def periodic_tick(self, now=None, max_actions=4):
        now = self._now(now)
        if type(max_actions) is not int or not 1 <= max_actions <= 20:
            raise ValueError('Provider tick action budget outside bounds')
        if not self._fresh_policy(now) and self._stale_claude(now) is None:
            return {'state': 'policy_refresh_required', 'actions': 0, 'selection': None}
        with self._locked() as acquired:
            if not acquired:
                return {'state': 'busy', 'actions': 0, 'selection': self._selected(self._overlay_pending_failures(self._load()), now)}
            state = self._load(); self._drain(state, now); actions = 0
            while actions < max_actions:
                now = max(now, self._now(None))
                self._drain(state, now)
                if not self._fresh_policy(now):
                    # Root-authorized exact Opus continuity does not fabricate a
                    # fresh Codex catalogue or authorize any Codex/reset call.
                    claude = self._stale_claude(now)
                    if (self._selected(state, now) or claude is None
                            or now < self._entry(state, claude)['next_probe_at']):
                        break
                    self._probe(state, claude, now); actions += 1; continue
                episode = state['quota_episode']; active_quota = episode and episode.get('resolved_at') is None
                claims = [c for c in state['resets'] if active_quota and c['episode_id'] == episode['id']]
                claim = claims[-1] if claims else None
                if active_quota and now >= state['next_limits_at']:
                    self._read_limits(state, now); actions += 1; continue
                limits = state['limits'] or {}
                fresh_limits = _number(limits.get('observed_at')) and 0 <= now - limits['observed_at'] <= self.policy['limits_interval_seconds']
                if claim and claim['status'] == 'pending' and claim['attempts'] >= self.policy['max_reset_attempts']:
                    claim['status'] = 'unknown_exhausted'
                    self._save(state, {'action': 'reset_crash_attempts_exhausted', 'idempotency_key': claim['idempotency_key']}, now)
                if active_quota and self.policy['reset_supported']:
                    if (claim and claim['status'] in {'pending', 'uncertain'} and now >= claim['next_retry_at']
                            and claim['attempts'] < self.policy['max_reset_attempts']
                            and fresh_limits and limits.get('weekly_exhausted') is True):
                        self._reset(state, claim, now); actions += 1; continue
                    if (claim is None and fresh_limits and limits.get('weekly_exhausted') is True
                            and type(limits.get('available_reset_count')) is int and limits['available_reset_count'] > 0):
                        claim = {'idempotency_key': str(uuid.uuid4()), 'episode_id': episode['id'], 'status': 'pending',
                                 'attempts': 0, 'next_retry_at': now, 'created_at': now, 'evidence_sha256': None, 'needs_refresh': False}
                        state['resets'].append(claim)
                        self._reset(state, claim, now); actions += 1; continue
                selected = self._selected(state, now)
                candidates = []
                for route in self.policy['routes']:
                    entry = self._entry(state, route)
                    if selected and route['id'] == selected['id']: break
                    if now >= entry['next_probe_at']: candidates.append(route)
                if not candidates: break
                self._probe(state, candidates[0], now); actions += 1
            now = max(now, self._now(None))
            self._drain(state, now)
            if not self._fresh_policy(now):
                return {'state': 'policy_refresh_required', 'actions': actions, 'selection': self._selected(state, now)}
            return {'state': 'selected' if self._selected(state, now) else 'unavailable',
                    'actions': actions, 'selection': self._selected(state, now)}
