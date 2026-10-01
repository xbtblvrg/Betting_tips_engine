"""Controller-owned, no-tool Claude coding transport and workspace patch boundary.

The model receives bounded source text and proposes complete file replacements.
It cannot read the project or execute code. Only the controller applies validated
edits; normal sandboxed tests, independent review and release gates still follow.
Provider selection, cooldowns, subprocess lifetime and acceptance are external.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from . import ai_delegation

VERSION = 'claude-structured-development-v1'
MODEL = 'claude-opus-5-5'
MAX_FILE_BYTES = 262144
MAX_SOURCE_BYTES = 393216
MAX_FILES = 96
MAX_INVENTORY = 4096
MAX_REPLY_BYTES = 2 * 1024 * 1024
MAX_EDIT_BYTES = 524288
MAX_EDITS = 24
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_PART = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,159}\Z')
_CONTEXT = ('AGENTS.md', 'config/project_contract.json',
            'data/development_inputs/manifest.json',
            'data/validation_sources/export.json',
            'data/task_diagnostics/manifest.json',
            'data/runtime-failure-evidence/manifest.json',
            'data/release-failure-evidence-manifest.json')


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _strict_json(body):
    def pairs(rows):
        value = {}
        for name, item in rows:
            if name in value:
                raise ValueError('Duplicate JSON key')
            value[name] = item
        return value
    try:
        value = json.loads(body, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 48:
                raise ValueError('JSON nesting bound exceeded')
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError('Nonfinite JSON number')
            if isinstance(item, dict):
                pending.extend((v, depth + 1) for v in item.values())
            elif isinstance(item, list):
                pending.extend((v, depth + 1) for v in item)
        return value
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Malformed bounded JSON') from exc


def _name(name):
    if (not isinstance(name, str) or not name or '\\' in name
            or str(Path(name)) != name or Path(name).is_absolute()
            or any(not _PART.fullmatch(part) or part in ('.', '..') for part in Path(name).parts)):
        raise ValueError('Noncanonical relative path')
    return name


def _directory(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('Directory must be absolute and canonical')
    for item in (path, *path.parents):
        info = item.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Directory ancestry is not ordinary')
    info = path.stat()
    if info.st_uid != os.getuid():
        raise ValueError('Directory is not owned by this controller')
    return path


def _identity(info):
    return [info.st_dev, info.st_ino]


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_nlink, info.st_uid)


def _read(path, maximum=MAX_FILE_BYTES):
    """Ordinary single-link immutable read; do not follow an input symlink."""
    path = Path(path)
    for parent in path.parents:
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError('Unsafe input ancestry')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.getuid() or before.st_size > maximum):
            raise ValueError('Input is not a bounded owned single-link regular file')
        body = bytearray()
        while len(body) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > maximum or _signature(before) != _signature(os.fstat(fd)):
            raise ValueError('Input changed during bounded read')
        if _signature(path.lstat()) != _signature(before):
            raise ValueError('Input path changed during read')
        return bytes(body), before
    finally:
        os.close(fd)


def _workspace(root, workspace):
    root, workspace = _directory(root), _directory(workspace)
    if workspace == root or root.is_relative_to(workspace):
        raise ValueError('Workspace cannot contain the production root')
    if workspace.is_relative_to(root):
        parts = workspace.relative_to(root).parts
        if not (len(parts) == 4 and parts[:3] in (
                ('data', 'development', 'workspaces'), ('data', 'releases', 'staging'))):
            raise ValueError('Workspace is outside isolated controller namespace')
    return root, workspace


def _readable(name):
    return (name in _CONTEXT or name.startswith(('src/betting_engine/', 'tests/', 'docs/', 'knowledge/cards/')))


def _editable(name, before, protected):
    _name(name)
    if (any(name == path or name.startswith(path + '/') for path in protected)
            or Path(name).name in ('__init__.py', 'conftest.py', 'sitecustomize.py', 'usercustomize.py')):
        raise ValueError('Protected file edit')
    if re.fullmatch(r'src/betting_engine/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py', name):
        return
    if re.fullmatch(r'tests/test_[A-Za-z0-9_]+\.(?:py|mjs)', name):
        if before is not None:
            raise ValueError('Existing regression test edit')
        return
    if re.fullmatch(r'docs/(?:[A-Za-z0-9_-]+/){0,7}[A-Za-z0-9_-]+\.(?:md|json|txt|log)', name):
        return
    raise ValueError('Edit outside source/new-test/documentation scope')


def source_bundle(workspace, task, prompt, *, preferred_paths=(), max_files=MAX_FILES,
                  max_source_bytes=MAX_SOURCE_BYTES):
    """Deterministic relevance order; omitted inputs are explicit, never absent claims."""
    workspace = _directory(workspace)
    if type(max_files) is not int or not 1 <= max_files <= MAX_FILES:
        raise ValueError('Invalid source file bound')
    if type(max_source_bytes) is not int or not 1 <= max_source_bytes <= MAX_SOURCE_BYTES:
        raise ValueError('Invalid source byte bound')
    preferred = {_name(name) for name in preferred_paths}
    if any(not _readable(name) for name in preferred):
        raise ValueError('Preferred input outside curated namespaces')
    names = set(_CONTEXT) | preferred
    for folder in ('src/betting_engine', 'tests', 'docs', 'knowledge/cards'):
        start = workspace / folder
        if start.is_symlink():
            raise ValueError('Symlink source directory')
        if not start.exists():
            continue
        for base, dirs, files in os.walk(start, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__'
                             and not (Path(base) / d).is_symlink())
            for name in sorted(files):
                if name.startswith('.'):
                    continue
                names.add((Path(base) / name).relative_to(workspace).as_posix())
                if len(names) > MAX_INVENTORY:
                    raise ValueError('Source inventory bound exceeded')
    task_text = _json(task).lower()
    terms = {word for word in re.findall(r'[a-z][a-z0-9_]{2,}', task_text)
             if word not in {'the', 'and', 'with', 'from', 'task', 'source', 'tests', 'data'}}
    task_id = str(task.get('id', '')).lower()
    def priority(name):
        lower = name.lower()
        if name in preferred:
            return (0, name)
        if name == 'AGENTS.md':
            return (1, name)
        if name in prompt or (task_id and task_id in Path(name).name.lower()):
            return (2, name)
        hits = sum(word in Path(lower).stem for word in terms)
        return (3 if hits else 5, -hits, name)
    files, omitted, inventory = [], [], {}
    used = 0
    for name in sorted(names, key=priority):
        try:
            _name(name)
        except ValueError:
            omitted.append({'path': name, 'reason': 'unsupported_path'})
            continue
        path = workspace / name
        if not path.exists() and not path.is_symlink():
            omitted.append({'path': name, 'reason': 'not_present_in_snapshot'})
            continue
        try:
            body, info = _read(path)
            row = {'path': name, 'sha256': _sha(body), 'bytes': len(body)}
            inventory[name] = {**row, 'mode': stat.S_IMODE(info.st_mode)}
            content = body.decode('utf-8', errors='strict')
            if '\x00' in content:
                raise UnicodeError('binary')
        except (OSError, ValueError, UnicodeError) as exc:
            omitted.append({'path': name, 'reason': 'unavailable_or_binary_or_oversized',
                            'error_type': type(exc).__name__})
            continue
        if len(files) >= max_files or used + len(body) > max_source_bytes:
            omitted.append({**row, 'reason': 'bounded_context_omission'})
        else:
            files.append({**row, 'content': content})
            used += len(body)
    return {'files': files, 'omitted': sorted(omitted, key=lambda r: r['path']),
            'inventory': inventory, 'selected_bytes': used,
            'scope': 'Only supplied text was inspected. Omitted, binary or unavailable inputs require explicit follow-up; do not invent their contents.'}


def _validate_schema(value, schema):
    """Validate the controller's deliberately small result-schema vocabulary."""
    allowed = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum',
               'maxItems', 'minItems', 'maxLength', 'pattern'}
    if not isinstance(schema, dict) or set(schema) - allowed:
        raise ValueError('Unsupported controller result schema')
    kinds = schema.get('type')
    kinds = [kinds] if isinstance(kinds, str) else kinds
    types = {'object': dict, 'array': list, 'string': str, 'boolean': bool,
             'integer': int, 'number': (int, float), 'null': type(None)}
    if not isinstance(kinds, list) or not kinds or any(k not in types for k in kinds):
        raise ValueError('Unsupported schema type')
    if not any(isinstance(value, types[k]) and not (isinstance(value, bool) and k in ('integer', 'number')) for k in kinds):
        raise ValueError('Structured output type mismatch')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Structured output enum mismatch')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if not set(schema.get('required', ())) <= set(value):
            raise ValueError('Missing structured output field')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError('Unexpected structured output field')
        for name, item in value.items():
            if name in properties:
                _validate_schema(item, properties[name])
    elif isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10000):
            raise ValueError('Structured output list bound')
        for item in value:
            _validate_schema(item, schema['items'])
    elif isinstance(value, str):
        if len(value) > schema.get('maxLength', MAX_REPLY_BYTES):
            raise ValueError('Structured output string bound')
        if 'pattern' in schema and re.search(schema['pattern'], value) is None:
            raise ValueError('Structured output pattern mismatch')


def prepare_step(root, workspace, directory, task, prompt, result_schema, *, readonly=False,
                 binary=None, protected_paths=(), preferred_paths=(), max_files=MAX_FILES,
                 max_source_bytes=MAX_SOURCE_BYTES):
    root, workspace = _workspace(root, workspace)
    directory = _directory(directory)
    if directory.is_relative_to(root) or directory.is_relative_to(workspace) or list(directory.iterdir()):
        raise ValueError('Claude transport needs a fresh private directory outside project/workspace')
    if stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise ValueError('Transport directory must be private')
    if type(readonly) is not bool or not isinstance(task, dict) or not isinstance(prompt, str):
        raise ValueError('Invalid controller step')
    from .development import PROTECTED
    protected = sorted({_name(name) for name in (*PROTECTED, *protected_paths,
                         'src/betting_engine/claude_development.py', 'docs/candidate_fixture_repairs')})
    bundle = source_bundle(workspace, task, prompt, preferred_paths=preferred_paths,
                           max_files=max_files, max_source_bytes=max_source_bytes)
    edit_schema = {'type': 'object', 'properties': {
        'path': {'type': 'string'}, 'before_sha256': {'type': ['string', 'null']},
        'content': {'type': 'string'}}, 'required': ['path', 'before_sha256', 'content'],
        'additionalProperties': False}
    schema = {'type': 'object', 'properties': {'report': result_schema,
        'edits': {'type': 'array', 'items': edit_schema, 'maxItems': 0 if readonly else MAX_EDITS}},
        'required': ['report', 'edits'], 'additionalProperties': False}
    context = {'version': VERSION, 'provider': 'claude', 'model': MODEL, 'root': str(root),
               'workspace': str(workspace), 'workspace_identity': _identity(workspace.stat()),
               'readonly': readonly, 'protected_paths': protected, 'result_schema': result_schema,
               'output_schema': schema, 'source_bundle': bundle}
    instruction = ('You are the temporarily authorized Claude Opus developer. Produce real complete source-file edits, '
        'not advisory suggestions. You have NO tools or project access. Use only the source bundle below. '
        'Return the schema envelope with report and edits; content is the complete UTF-8 replacement file. '
        'Existing-file before_sha256 must match a supplied selected source. New files use null. '
        'Never edit existing regression tests, protected source/config, ledgers or model artifacts. '
        'Do not claim tests were run. The controller applies exact edits then runs full isolated validation and '
        'independent review; final production release remains separately authorized. '
        'If omitted inputs prevent a safe change, report the exact follow-up in remaining; do not invent missing data. '
        + ('This is an independent read-only review: edits MUST be empty. ' if readonly else '')
        + '\nCONTROLLER TASK AND INSTRUCTIONS:\n' + prompt + '\nTASK:\n' + _json(task)
        + '\nPROTECTED PATHS:\n' + _json(protected) + '\nBOUNDED SOURCE BUNDLE:\n' + _json(bundle))
    if len(instruction.encode()) > 1536 * 1024:
        raise ValueError('Bounded prompt exceeds transport limit')
    command, env, stdin = ai_delegation.command('claude', {
        'binary': str(binary or Path.home() / '.local/bin/claude'), 'model': MODEL,
        '_protected_root': str(root)}, directory, instruction)
    # The full CLI may use its own authentication/API. It has no tools and
    # cannot read OR write any project/workspace path at the OS boundary.
    profile = directory / 'project-boundary.sb'
    profile.write_text('(version 1)\n(allow default)\n' + ''.join(
        '(deny file-read* file-write* (subpath ' + json.dumps(str(path)) + '))\n'
        for path in dict.fromkeys((root, workspace))))
    command += ['--effort', 'high', '--json-schema', _json(schema)]
    cwd = directory / 'empty'
    cwd.mkdir(mode=0o700)
    context['boundary_sha256'] = _sha(profile.read_bytes())
    context['prompt_sha256'] = _sha(instruction.encode())
    return {'command': command, 'env': env, 'stdin': stdin, 'cwd': str(cwd), 'context': context}


def _edits(edits, context):
    if not isinstance(edits, list) or len(edits) > MAX_EDITS or (context['readonly'] and edits):
        raise ValueError('Invalid edit count or read-only mutation')
    included = {r['path']: r for r in context['source_bundle']['files']}
    inventory = context['source_bundle']['inventory']
    seen, total, result = set(), 0, []
    for row in edits:
        if not isinstance(row, dict) or set(row) != {'path', 'before_sha256', 'content'}:
            raise ValueError('Invalid edit descriptor')
        name, before, content = row['path'], row['before_sha256'], row['content']
        _name(name)
        if name in seen or not isinstance(content, str) or '\x00' in content:
            raise ValueError('Duplicate or invalid file content')
        seen.add(name)
        body = content.encode('utf-8', errors='strict')
        total += len(body)
        if len(body) > MAX_FILE_BYTES or total > MAX_EDIT_BYTES:
            raise ValueError('Edit byte bound exceeded')
        _editable(name, before, context['protected_paths'])
        if before is None:
            if name in inventory:
                raise ValueError('New edit targets an existing source')
        elif (not isinstance(before, str) or not _SHA.fullmatch(before)
              or name not in included or included[name]['sha256'] != before):
            raise ValueError('Existing edit was not bound to supplied source')
        if before == _sha(body):
            raise ValueError('No-op source replacement')
        result.append(dict(row))
    return result


def finish_step(stdout, context):
    if context.get('version') != VERSION or context.get('model') != MODEL:
        raise ValueError('Invalid Claude step context')
    body = stdout.encode() if isinstance(stdout, str) else stdout
    if not isinstance(body, bytes) or len(body) > MAX_REPLY_BYTES:
        raise ValueError('Claude output bound exceeded')
    payload = _strict_json(body)
    if (not isinstance(payload, dict) or payload.get('type') != 'result'
            or payload.get('is_error') is not False or payload.get('subtype') != 'success'
            or payload.get('terminal_reason') != 'completed' or payload.get('permission_denials') != []
            or payload.get('subagent_stats', {}).get('spawned', 0) != 0):
        raise ValueError('Claude CLI did not report a clean completed result')
    usage = payload.get('modelUsage')
    if (not isinstance(usage, dict) or set(usage) != {MODEL} or not isinstance(usage[MODEL], dict)
            or usage[MODEL].get('canonicalModel') != MODEL):
        raise ValueError('Claude reported model is not the exact authorized Opus model')
    value = payload.get('structured_output')
    _validate_schema(value, context['output_schema'])
    return {'provider': 'claude', 'model': MODEL, 'report': value['report'],
            'edits': _edits(value['edits'], context), 'stdout_sha256': _sha(body)}


class PatchApplicationError(ValueError):
    def __init__(self, message, receipts, rollback_conflicts):
        super().__init__(message)
        self.receipts, self.rollback_conflicts = receipts, rollback_conflicts


def apply_edits(workspace, edits, context):
    """All-before validation, descriptor-relative atomic CAS, reverse CAS rollback.

    The caller must hold the existing task/workspace ownership lock. A conflicting
    external write is preserved and reported, never overwritten during rollback.
    """
    workspace = _directory(workspace)
    if str(workspace) != context['workspace'] or _identity(workspace.stat()) != context['workspace_identity']:
        raise ValueError('Workspace identity changed')
    edits = _edits(edits, context)
    plans = []
    # Validate EVERY path and current body before staging even the first file.
    for row in edits:
        path = workspace / row['path']
        for parent in path.parents:
            if parent == workspace:
                break
            if parent.exists() or parent.is_symlink():
                if not stat.S_ISDIR(parent.lstat().st_mode):
                    raise ValueError('Unsafe edit parent')
        if path.exists() or path.is_symlink():
            body, info = _read(path)
            if row['before_sha256'] is None or _sha(body) != row['before_sha256']:
                raise ValueError('Source compare-and-swap failed')
        else:
            if row['before_sha256'] is not None:
                raise ValueError('Source disappeared before apply')
            body, info = None, None
        plans.append({'row': row, 'path': path, 'before': body, 'info': info,
                      'after': row['content'].encode(), 'committed': False})
    opened, created, receipts, conflicts = [], [], [], []
    root_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    if _identity(os.fstat(root_fd)) != context['workspace_identity']:
        os.close(root_fd)
        raise ValueError('Workspace changed before directory open')
    opened.append(root_fd)

    def verify_parent(plan):
        _directory(workspace)
        if _identity(workspace.stat()) != context['workspace_identity']:
            raise ValueError('Workspace directory changed during apply')
        for path, identity in plan['parents']:
            if not stat.S_ISDIR(path.lstat().st_mode) or _identity(path.lstat()) != identity:
                raise ValueError('Edit ancestry changed during apply')

    def current(plan):
        verify_parent(plan)
        try:
            fd = os.open(plan['path'].name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=plan['fd'])
        except FileNotFoundError:
            return None, None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_size > MAX_FILE_BYTES:
                raise ValueError('Changed target is not an ordinary bounded file')
            data = b''
            while len(data) <= MAX_FILE_BYTES:
                part = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - len(data)))
                if not part:
                    break
                data += part
            if len(data) > MAX_FILE_BYTES or _signature(info) != _signature(os.fstat(fd)):
                raise ValueError('Target changed while checking CAS')
            return data, info
        finally:
            os.close(fd)

    def stage(plan, body, mode):
        name = '.claude-patch-' + uuid.uuid4().hex
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=plan['fd'])
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(body); stream.flush()
                os.fchmod(fd, mode); os.fsync(fd)
            identity = _identity(os.fstat(fd))
        except BaseException:
            os.unlink(name, dir_fd=plan['fd'])
            raise
        finally:
            os.close(fd)
        return name, identity

    def verify_committed(plan):
        data, info = current(plan)
        if data != plan['after'] or info is None or _identity(info) != plan['after_identity']:
            raise ValueError('Target changed after commit; preserve external write')

    def remove_private(plan, key, identity_key):
        name = plan.get(key)
        if not name:
            return
        try:
            info = os.stat(name, dir_fd=plan['fd'], follow_symlinks=False)
        except FileNotFoundError:
            return
        if _identity(info) != plan[identity_key] or not stat.S_ISREG(info.st_mode):
            raise ValueError('Private staging file changed; preserve external write')
        os.unlink(name, dir_fd=plan['fd'])

    try:
        for plan in plans:
            fd, path, parents = root_fd, workspace, []
            for part in Path(plan['row']['path']).parts[:-1]:
                path = path / part
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    created.append((path, _identity(os.fstat(child))))
                opened.append(child); fd = child
                parents.append((path, _identity(os.fstat(fd))))
            plan.update(fd=fd, parents=parents)
            data, info = current(plan)
            if data != plan['before'] or (info is not None and _signature(info) != _signature(plan['info'])):
                raise ValueError('Source changed during staging')
            mode = stat.S_IMODE(plan['info'].st_mode) if plan['info'] else 0o600
            plan['temp'], plan['after_identity'] = stage(plan, plan['after'], mode)
        for plan in plans:
            data, info = current(plan)
            if data != plan['before'] or (info is not None and _signature(info) != _signature(plan['info'])):
                raise ValueError('Source changed immediately before commit')
            if plan['before'] is None:
                # link(2) is an atomic no-replace publication: a concurrent new
                # destination cannot be overwritten between absence check and commit.
                os.link(plan['temp'], plan['path'].name, src_dir_fd=plan['fd'],
                        dst_dir_fd=plan['fd'], follow_symlinks=False)
                plan['committed'] = True
                remove_private(plan, 'temp', 'after_identity')
            else:
                os.replace(plan['temp'], plan['path'].name, src_dir_fd=plan['fd'], dst_dir_fd=plan['fd'])
                plan['committed'] = True
            os.fsync(plan['fd'])
            verify_committed(plan)
            receipts.append({'path': plan['row']['path'], 'before_sha256': plan['row']['before_sha256'],
                             'after_sha256': _sha(plan['after']), 'bytes': len(plan['after'])})
        # A later commit/fsync must not hide drift in an earlier committed file.
        for plan in plans:
            verify_committed(plan)
        return receipts
    except BaseException as exc:
        for plan in reversed(plans):
            if not plan['committed']:
                continue
            try:
                remove_private(plan, 'temp', 'after_identity')
                verify_committed(plan)
                if plan['before'] is None:
                    os.unlink(plan['path'].name, dir_fd=plan['fd'])
                else:
                    name, identity = stage(plan, plan['before'], stat.S_IMODE(plan['info'].st_mode))
                    plan['rollback_temp'], plan['rollback_identity'] = name, identity
                    # Staging includes fsync; recheck CAS after that blocking work.
                    verify_committed(plan)
                    os.replace(name, plan['path'].name, src_dir_fd=plan['fd'], dst_dir_fd=plan['fd'])
                os.fsync(plan['fd'])
            except (OSError, ValueError) as rollback_error:
                conflicts.append({'path': plan['row']['path'], 'error': str(rollback_error)[:300]})
        raise PatchApplicationError('Claude patch application failed: ' + str(exc)[:300], receipts, conflicts) from exc
    finally:
        for plan in plans:
            for key, identity_key in (('temp', 'after_identity'), ('rollback_temp', 'rollback_identity')):
                try:
                    remove_private(plan, key, identity_key)
                except (OSError, ValueError) as cleanup_error:
                    conflicts.append({'path': plan['row']['path'], 'error': str(cleanup_error)[:300]})
        for fd in reversed(opened):
            os.close(fd)
        for path, identity in reversed(created):
            try:
                if _identity(path.lstat()) == identity:
                    path.rmdir()
            except OSError:
                pass
