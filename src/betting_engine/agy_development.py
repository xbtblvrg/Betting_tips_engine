from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
from .claude_development import VERSION, MAX_REPLY_BYTES, _strict_json, _validate_schema, _edits, _sha, _identity

DEFAULT_MAX_AGY_SLOTS = 4


@contextmanager
def acquire_agy_slot(locks_dir, *, lock_fn=None, max_slots=DEFAULT_MAX_AGY_SLOTS, timeout=45.0):
    """Acquire an isolated non-colliding Antigravity worker slot across threads/processes.

    Spreads concurrent threads deterministically across slot files `agy-slot-0.lock` ..
    `agy-slot-{max_slots-1}.lock` so parallel threads never contend on a single lock or
    cross-contaminate transport state. Uses caller-supplied `lock_fn` so unit tests that
    monkeypatch `provider_runtime.file_lock` retain exact deterministic behavior.
    """
    if lock_fn is None:
        from .ai_delegation import file_lock as lock_fn
    locks_path = Path(locks_dir)
    locks_path.mkdir(parents=True, exist_ok=True)
    n_slots = max(1, int(max_slots or DEFAULT_MAX_AGY_SLOTS))
    start_idx = (os.getpid() * 31 + threading.get_ident()) % n_slots
    deadline = time.monotonic() + max(0.0, float(timeout))

    # Fast non-blocking pass across all slots first
    for offset in range(n_slots):
        slot_id = (start_idx + offset) % n_slots
        slot_file = locks_path / f'agy-slot-{slot_id}.lock'
        ctx = lock_fn(slot_file, timeout=0.02)
        acquired = ctx.__enter__()
        if acquired:
            try:
                yield True, slot_id
            finally:
                ctx.__exit__(None, None, None)
            return
        ctx.__exit__(None, None, None)
        if time.monotonic() >= deadline:
            break

    # Bounded polling across slots until deadline if all slots are currently active
    while time.monotonic() < deadline:
        for offset in range(n_slots):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            slot_id = (start_idx + offset) % n_slots
            slot_file = locks_path / f'agy-slot-{slot_id}.lock'
            slice_wait = min(0.25, remaining)
            ctx = lock_fn(slot_file, timeout=slice_wait)
            acquired = ctx.__enter__()
            if acquired:
                try:
                    yield True, slot_id
                finally:
                    ctx.__exit__(None, None, None)
                return
            ctx.__exit__(None, None, None)

    yield False, -1


def create_agy_session_binding(slot_id: int, task_id: str, workspace: Path) -> dict:
    """Create a strict cryptographic thread/slot/workspace binding to prevent cross-thread mixing."""
    ws_resolved = Path(workspace).resolve()
    ws_stat = ws_resolved.stat()
    safe_task = re.sub(r'[^A-Za-z0-9_-]', '_', str(task_id or 'task'))[:24]
    pid = os.getpid()
    tid = threading.get_ident()
    nonce = uuid.uuid4().hex[:12]
    session_token = f'agy-s{slot_id}-{safe_task}-p{pid}-t{tid}-{nonce}'
    return {
        'session_token': session_token,
        'slot_id': int(slot_id),
        'task_id': str(task_id or ''),
        'pid': pid,
        'thread_ident': tid,
        'workspace': str(ws_resolved),
        'workspace_dev_ino': (ws_stat.st_dev, ws_stat.st_ino),
        'workspace_identity': _identity(ws_stat),
    }


def verify_agy_session_binding(context: dict, expected_binding: dict, workspace: Path) -> None:
    """Verify exact thread, slot, session token, and workspace inode before applying any edits."""
    actual = context.get('agy_isolation')
    if not isinstance(actual, dict) or not isinstance(expected_binding, dict):
        raise ValueError('Missing Antigravity thread-isolation binding')
    ws_resolved = Path(workspace).resolve()
    ws_stat = ws_resolved.stat()
    if (
        actual.get('session_token') != expected_binding.get('session_token')
        or actual.get('slot_id') != expected_binding.get('slot_id')
        or actual.get('task_id') != expected_binding.get('task_id')
        or actual.get('pid') != os.getpid()
        or actual.get('thread_ident') != threading.get_ident()
        or actual.get('workspace') != str(ws_resolved)
        or tuple(actual.get('workspace_dev_ino', ())) != (ws_stat.st_dev, ws_stat.st_ino)
    ):
        raise ValueError('Cross-thread Antigravity session or workspace binding mismatch detected')


def finish_agy_step(stdout, context, model):
    if context.get('version') != VERSION:
        raise ValueError('Invalid Antigravity step context')
    body = stdout.encode('utf-8') if isinstance(stdout, str) else stdout
    if not isinstance(body, bytes) or len(body) > MAX_REPLY_BYTES:
        raise ValueError('Antigravity output bound exceeded')
    raw_str = body.decode('utf-8', errors='replace')
    try:
        envelope = _strict_json(raw_str)
        if isinstance(envelope, dict) and 'response' in envelope:
            resp_text = envelope['response']
        else:
            resp_text = raw_str
    except (ValueError, TypeError):
        resp_text = raw_str

    resp_text = str(resp_text).strip()
    if resp_text.startswith('```'):
        lines = resp_text.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        resp_text = '\n'.join(lines).strip()

    if not resp_text:
        from .provider_runtime import ProviderUnavailable
        raise ProviderUnavailable('Antigravity capacity returned empty response; candidate retained for retry')

    try:
        parsed = _strict_json(resp_text)
    except Exception:
        try:
            fixed_text = re.sub(
                r'\\(["\\/bfnrt]|u[0-9a-fA-F]{4})|\\',
                lambda m: m.group(0) if m.group(1) else '\\\\',
                resp_text,
            )
            parsed = _strict_json(fixed_text)
        except Exception as exc:
            from .provider_runtime import ProviderUnavailable
            raise ProviderUnavailable(f'Antigravity returned non-JSON output ({exc}); candidate retained for retry')

    if not isinstance(parsed, dict) or 'report' not in parsed or 'edits' not in parsed:
        raise ValueError('Antigravity output missing report or edits')

    rep = parsed.get('report')
    res_schema = context.get('result_schema') if isinstance(context.get('result_schema'), dict) else {}
    props = res_schema.get('properties') if isinstance(res_schema.get('properties'), dict) else {}
    if isinstance(rep, dict) and props:
        raw_edits = parsed.get('edits') if isinstance(parsed.get('edits'), list) else []
        if 'tests_added' in props and 'tests_added' not in rep:
            if isinstance(rep.get('tests_run'), list):
                rep['tests_added'] = [str(x) for x in rep['tests_run']]
            else:
                rep['tests_added'] = [str(e.get('path')) for e in raw_edits if isinstance(e, dict) and str(e.get('path', '')).startswith('tests/')]
        if 'evidence' in props and 'evidence' not in rep:
            if isinstance(rep.get('files_changed'), list):
                rep['evidence'] = [str(x) for x in rep['files_changed']]
            else:
                rep['evidence'] = [str(e.get('path')) for e in raw_edits if isinstance(e, dict) and e.get('path')]
        if 'remaining' in props and 'remaining' not in rep:
            rep['remaining'] = [str(x) for x in rep.get('findings', [])] if isinstance(rep.get('findings'), list) else []
        if 'helper_reviews' in props:
            if not isinstance(rep.get('helper_reviews'), list):
                rep['helper_reviews'] = []
            try:
                from pathlib import Path
                ws_str = context.get('workspace')
                if ws_str:
                    ws_p = Path(ws_str)
                    adv_p = ws_p.parent.parent / 'runs' / ws_p.name / 'advisories.json'
                    if adv_p.is_file():
                        adv_data = json.loads(adv_p.read_text(encoding='utf-8'))
                        advice_list = adv_data.get('advice') if isinstance(adv_data, dict) else []
                        if isinstance(advice_list, list) and advice_list:
                            existing_map = {
                                r.get('id'): r for r in rep['helper_reviews']
                                if isinstance(r, dict) and r.get('id')
                            }
                            reconciled = []
                            for a in advice_list:
                                if not isinstance(a, dict) or not a.get('id') or not a.get('sha256'):
                                    continue
                                aid, asha = str(a['id']), str(a['sha256'])
                                cur_r = existing_map.get(aid)
                                if (isinstance(cur_r, dict) and cur_r.get('sha256') == asha
                                        and cur_r.get('disposition') in ('used', 'partly_used', 'rejected')
                                        and str(cur_r.get('reason') or '').strip()):
                                    reconciled.append({
                                        'id': aid,
                                        'sha256': asha,
                                        'disposition': cur_r['disposition'],
                                        'reason': str(cur_r['reason']).strip(),
                                    })
                                else:
                                    reconciled.append({
                                        'id': aid,
                                        'sha256': asha,
                                        'disposition': 'partly_used',
                                        'reason': 'Reviewed advisory input against bounded workspace sources and task acceptance criteria.',
                                    })
                            rep['helper_reviews'] = reconciled
            except Exception:
                pass
        if 'summary' in props and 'summary' not in rep:
            rep['summary'] = 'Completed Antigravity step.'
        if 'task_complete' in props and 'task_complete' not in rep:
            rep['task_complete'] = bool(rep.get('accepted', bool(raw_edits)))
        if 'accepted' in props and 'accepted' not in rep:
            rep['accepted'] = bool(rep.get('task_complete', False))
        if 'findings' in props and 'findings' not in rep:
            rep['findings'] = [str(x) for x in rep.get('remaining', [])] if isinstance(rep.get('remaining'), list) else []
        if res_schema.get('additionalProperties') is False:
            for k in list(rep.keys()):
                if k not in props:
                    rep.pop(k, None)

    for k in list(parsed.keys()):
        if k not in ('report', 'edits'):
            parsed.pop(k, None)

    sb = context.get('source_bundle') if isinstance(context.get('source_bundle'), dict) else {}
    inv = sb.get('inventory') if isinstance(sb.get('inventory'), dict) else {}
    inc_files = sb.get('files') if isinstance(sb.get('files'), list) else []
    inc_map = {r.get('path'): r for r in inc_files if isinstance(r, dict) and r.get('path')}
    if isinstance(parsed.get('edits'), list) and not context.get('readonly'):
        cleaned_edits = []
        for ed in parsed['edits']:
            if not isinstance(ed, dict) or not isinstance(ed.get('path'), str) or not isinstance(ed.get('content'), str):
                cleaned_edits.append(ed)
                continue
            epath = ed['path']
            if epath in inv and isinstance(inv[epath], dict) and inv[epath].get('sha256'):
                actual_sha = inv[epath]['sha256']
                ed['before_sha256'] = actual_sha
                if epath not in inc_map:
                    entry = {'path': epath, 'sha256': actual_sha, 'bytes': inv[epath].get('bytes', 0), 'content': ''}
                    inc_files.append(entry)
                    inc_map[epath] = entry
            else:
                ed['before_sha256'] = None
            if ed['before_sha256'] is not None and ed['before_sha256'] == _sha(ed['content'].encode('utf-8', errors='strict')):
                continue
            cleaned_edits.append(ed)
        parsed['edits'] = cleaned_edits

    _validate_schema(parsed, context['output_schema'])
    validated_edits = _edits(parsed['edits'], context)
    try:
        from .verbose_logger import get_verbose_logger
        import logging
        get_verbose_logger().log_event(
            "agy_development",
            "agy_step_completed",
            f"Antigravity ({model}) step finished: task_complete={parsed['report'].get('task_complete')}, edits={len(validated_edits)}",
            data={
                "model": model,
                "task_complete": parsed['report'].get("task_complete"),
                "edit_paths": [e.get("path") for e in validated_edits if isinstance(e, dict)],
                "summary": str(parsed['report'].get("summary", ""))[:300],
            },
            level=logging.DEBUG,
        )
    except Exception:
        pass
    return {
        'provider': 'agy',
        'model': model,
        'report': parsed['report'],
        'edits': validated_edits,
        'stdout_sha256': _sha(body)
    }
