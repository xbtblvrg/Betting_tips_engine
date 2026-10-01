"""No provider calls: native disposable boundary and transactional patch faults."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import claude_development as cd


REPORT = {'type': 'object', 'properties': {'summary': {'type': 'string'},
          'task_complete': {'type': 'boolean'}, 'remaining': {'type': 'array', 'items': {'type': 'string'}}},
          'required': ['summary', 'task_complete', 'remaining'], 'additionalProperties': False}


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / 'production'; root.mkdir()
    workspace = root / 'data/development/workspaces/test-D25'; workspace.mkdir(parents=True)
    (workspace / 'src/betting_engine').mkdir(parents=True)
    (workspace / 'tests').mkdir()
    (workspace / 'docs').mkdir()
    (workspace / 'src/betting_engine/target.py').write_text('VALUE = 1\n')
    (workspace / 'tests/test_old.py').write_text('def test_old(): assert True\n')
    (workspace / 'AGENTS.md').write_text('Keep all original tests.\n')
    transport = tmp_path / 'transport'; transport.mkdir(mode=0o700)
    step = cd.prepare_step(root, workspace, transport, {'id': 'D25', 'title': 'Target recovery'},
                           'Fix target.py. Do not claim tests ran.', REPORT)
    return root, workspace, transport, step


def edit(step, path='src/betting_engine/target.py', content='VALUE = 2\n'):
    inventory = step['context']['source_bundle']['inventory']
    return {'path': path, 'before_sha256': inventory.get(path, {}).get('sha256'), 'content': content}


def response(edits, report=None):
    return {'type': 'result', 'is_error': False, 'subtype': 'success', 'terminal_reason': 'completed',
            'permission_denials': [], 'modelUsage': {cd.MODEL: {'canonicalModel': cd.MODEL}},
            'structured_output': {'report': report or {'summary': 'Proposed code', 'task_complete': True,
                                                     'remaining': []}, 'edits': edits}}


def finish(step, edits):
    return cd.finish_step(json.dumps(response(edits)), step['context'])


def test_actual_full_file_code_proposal_applies_only_in_workspace(prepared):
    root, work, _, step = prepared
    original = work / 'src/betting_engine/target.py'
    original.chmod(0o640)
    result = finish(step, [edit(step), edit(step, 'tests/test_new.py', 'def test_new(): assert 2 == 2\n')])
    receipts = cd.apply_edits(work, result['edits'], step['context'])
    assert original.read_text() == 'VALUE = 2\n' and original.stat().st_mode & 0o777 == 0o640
    assert (work / 'tests/test_new.py').exists() and len(receipts) == 2
    assert not (root / 'src').exists()
    assert (work / 'tests/test_old.py').read_text() == 'def test_old(): assert True\n'


@pytest.mark.parametrize('path', ['../root.py', '/tmp/evil.py', 'src//betting_engine/new.py',
                                'src/betting_engine/../bad.py', 'src\\betting_engine\\x.py',
                                'config/project_contract.json', 'data/engine.sqlite3',
                                'src/betting_engine/development.py', 'src/betting_engine/claude_development.py',
                                'tests/conftest.py', 'tests/test_old.py', '.claude/settings.json'])
def test_protected_and_escaping_edits_cannot_enter_application(prepared, path):
    _, work, _, step = prepared
    with pytest.raises(ValueError):
        finish(step, [edit(step), edit(step, path, 'bad')])
    assert (work / 'src/betting_engine/target.py').read_text() == 'VALUE = 1\n'


@pytest.mark.parametrize('damage', ['wrong_model', 'extra_model', 'assistant_claim_only', 'error',
                                  'not_complete', 'permission', 'subagent', 'report_schema', 'missing_structured'])
def test_cli_envelope_and_report_are_authoritative(prepared, damage):
    _, _, _, step = prepared; body = response([edit(step)])
    if damage == 'wrong_model': body['modelUsage'] = {'claude-other': {'canonicalModel': 'claude-other'}}
    elif damage == 'extra_model': body['modelUsage']['claude-other'] = {}
    elif damage == 'assistant_claim_only': body.pop('modelUsage'); body['result'] = cd.MODEL
    elif damage == 'error': body['is_error'] = True
    elif damage == 'not_complete': body['terminal_reason'] = 'max_tokens'
    elif damage == 'permission': body['permission_denials'] = ['denied']
    elif damage == 'subagent': body['subagent_stats'] = {'spawned': 1}
    elif damage == 'report_schema': body['structured_output']['report']['task_complete'] = 'true'
    else: body['result'] = json.dumps(body.pop('structured_output'))
    with pytest.raises(ValueError): cd.finish_step(json.dumps(body), step['context'])


@pytest.mark.parametrize('raw', ['{"type":"result","type":"result"}', '{"x": NaN}', '{"x":1e999}', '[' * 1200 + '0' + ']' * 1200])
def test_malformed_json_never_becomes_a_submission(prepared, raw):
    with pytest.raises(ValueError): cd.finish_step(raw, prepared[3]['context'])


def test_missing_stale_and_omitted_old_hashes_fail_before_any_write(prepared):
    _, work, _, step = prepared
    target = work / 'src/betting_engine/target.py'
    changes = finish(step, [edit(step)])['edits']
    target.write_text('CONCURRENT = True\n')
    with pytest.raises(ValueError): cd.apply_edits(work, changes, step['context'])
    assert target.read_text() == 'CONCURRENT = True\n'
    bad = edit(step); bad['before_sha256'] = '0' * 64
    with pytest.raises(ValueError): finish(step, [bad])
    excluded = deepcopy(step); excluded['context']['source_bundle']['files'] = []
    with pytest.raises(ValueError): finish(excluded, [edit(step)])


@pytest.mark.parametrize('damage', ['duplicate', 'oversized', 'nul', 'noop', 'new_existing'])
def test_invalid_edit_batches_fail_before_mutation(prepared, damage):
    _, work, _, step = prepared; changes = [edit(step)]
    if damage == 'duplicate': changes *= 2
    elif damage == 'oversized': changes[0]['content'] = 'x' * (cd.MAX_FILE_BYTES + 1)
    elif damage == 'nul': changes[0]['content'] = '\x00'
    elif damage == 'noop': changes[0]['content'] = 'VALUE = 1\n'
    else: changes[0]['before_sha256'] = None
    with pytest.raises(ValueError): finish(step, changes)
    assert (work / 'src/betting_engine/target.py').read_text() == 'VALUE = 1\n'


@pytest.mark.parametrize('damage', ['symlink_file', 'symlink_parent', 'hardlink', 'directory'])
def test_file_and_parent_link_escapes_rejected_preflight(prepared, tmp_path, damage):
    _, work, _, step = prepared; changes = finish(step, [edit(step)])['edits']
    p = work / 'src/betting_engine/target.py'; outside = tmp_path / 'outside'; outside.write_text('KEEP\n')
    p.unlink()
    if damage == 'symlink_file': p.symlink_to(outside)
    elif damage == 'hardlink': os.link(outside, p)
    elif damage == 'directory': p.mkdir()
    else:
        p.parent.rmdir(); p.parent.symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / 'target.py').write_text('VALUE = 1\n')
    with pytest.raises((ValueError, OSError)): cd.apply_edits(work, changes, step['context'])
    assert outside.read_text() == 'KEEP\n'


def test_later_invalid_target_prevents_first_valid_edit(prepared, tmp_path):
    _, work, _, step = prepared
    changes = finish(step, [edit(step), edit(step, 'tests/test_new.py', 'new')])['edits']
    (work / 'tests/test_new.py').symlink_to(tmp_path / 'missing')
    with pytest.raises((ValueError, OSError)): cd.apply_edits(work, changes, step['context'])
    assert (work / 'src/betting_engine/target.py').read_text() == 'VALUE = 1\n'


def test_second_swap_failure_restores_first_and_leaves_no_partial_files(prepared, monkeypatch):
    _, work, _, step = prepared
    changes = finish(step, [edit(step), edit(step, 'tests/test_new.py', 'new')])['edits']
    def fail_new(source, dest, **kwargs):
        raise OSError('injected second publication failure')
    monkeypatch.setattr(cd.os, 'link', fail_new)
    with pytest.raises(cd.PatchApplicationError) as error:
        cd.apply_edits(work, changes, step['context'])
    assert error.value.rollback_conflicts == []
    assert (work / 'src/betting_engine/target.py').read_text() == 'VALUE = 1\n'
    assert not (work / 'tests/test_new.py').exists()
    assert not list(work.rglob('.claude-patch-*'))


def test_rollback_preserves_later_external_writer_and_reports_conflict(prepared, monkeypatch):
    _, work, _, step = prepared; target = work / 'src/betting_engine/target.py'
    changes = finish(step, [edit(step), edit(step, 'tests/test_new.py', 'new')])['edits']
    def race(source, dest, **kwargs):
        target.write_text('EXTERNAL\n'); raise OSError('injected race')
    monkeypatch.setattr(cd.os, 'link', race)
    with pytest.raises(cd.PatchApplicationError) as error:
        cd.apply_edits(work, changes, step['context'])
    assert target.read_text() == 'EXTERNAL\n' and error.value.rollback_conflicts


def test_source_changes_after_staging_are_not_overwritten(prepared, monkeypatch):
    _, work, _, step = prepared; target = work / 'src/betting_engine/target.py'
    changes = finish(step, [edit(step)])['edits']; fsync = cd.os.fsync; called = []
    def change(fd):
        if not called:
            called.append(True); target.write_text('AFTER STAGING\n')
        return fsync(fd)
    monkeypatch.setattr(cd.os, 'fsync', change)
    with pytest.raises(cd.PatchApplicationError): cd.apply_edits(work, changes, step['context'])
    assert target.read_text() == 'AFTER STAGING\n'


def test_parent_replaced_after_staging_is_not_followed(prepared, monkeypatch):
    _, work, _, step = prepared
    changes = finish(step, [edit(step)])['edits']
    parent = work / 'src/betting_engine'; held = work / 'src/retained'; fsync = cd.os.fsync; once = []
    def replace_parent(fd):
        if not once:
            once.append(True); parent.rename(held); parent.mkdir()
            (parent / 'target.py').write_text('OTHER WRITER\n')
        return fsync(fd)
    monkeypatch.setattr(cd.os, 'fsync', replace_parent)
    with pytest.raises(cd.PatchApplicationError): cd.apply_edits(work, changes, step['context'])
    assert (parent / 'target.py').read_text() == 'OTHER WRITER\n'
    assert (held / 'target.py').read_text() == 'VALUE = 1\n'
    assert not list(held.glob('.claude-patch-*'))


def test_new_nested_source_is_created_without_touching_existing_neighbors(prepared):
    _, work, _, step = prepared
    proposed = [edit(step, 'src/betting_engine/experiment/new.py', 'VALUE = 3\n')]
    receipt = cd.apply_edits(work, finish(step, proposed)['edits'], step['context'])
    assert (work / 'src/betting_engine/experiment/new.py').read_text() == 'VALUE = 3\n'
    assert receipt[0]['before_sha256'] is None
    assert (work / 'src/betting_engine/target.py').read_text() == 'VALUE = 1\n'


def test_readonly_review_cannot_propose_or_apply_edits(prepared):
    _, work, _, step = prepared; context = deepcopy(step['context']); context['readonly'] = True
    context['output_schema']['properties']['edits']['maxItems'] = 0
    with pytest.raises(ValueError): cd.finish_step(json.dumps(response([edit(step)])), context)
    with pytest.raises(ValueError): cd.apply_edits(work, [edit(step)], context)
    result = cd.finish_step(json.dumps(response([])), context)
    assert result['edits'] == []


def test_deterministic_bounded_bundle_marks_omissions_and_pins_selected_body(prepared):
    _, work, _, _ = prepared
    args = {'preferred_paths': ['src/betting_engine/target.py'], 'max_files': 1, 'max_source_bytes': 20}
    first = cd.source_bundle(work, {'id': 'D25'}, '', **args)
    second = cd.source_bundle(work, {'id': 'D25'}, '', **args)
    assert first == second and len(first['files']) == 1
    assert first['files'][0]['path'] == 'src/betting_engine/target.py'
    assert first['omitted'] and first['selected_bytes'] <= 20
    assert any(r['path'] == 'tests/test_old.py' and r['reason'] == 'bounded_context_omission' for r in first['omitted'])


def test_command_has_no_tools_or_permission_bypass_and_secrets_not_in_context(prepared, monkeypatch, tmp_path):
    root, work, _, _ = prepared; private = tmp_path / 'next'; private.mkdir(mode=0o700)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-secret')
    step = cd.prepare_step(root, work, private, {'id': 'D25'}, 'code task', REPORT)
    argv = step['command']
    assert argv[:2] == ['/usr/bin/sandbox-exec', '-f']
    assert argv[argv.index('--tools') + 1] == ''
    assert argv[argv.index('--model') + 1] == cd.MODEL
    assert argv[argv.index('--effort') + 1] == 'high'
    assert '--json-schema' in argv and '--restricted' in argv and '--safe-mode' in argv
    assert not any('bypass' in arg or 'dangerously' in arg for arg in argv)
    assert 'synthetic-secret' not in json.dumps(step['context']) + step['stdin'] + json.dumps(argv)
    assert step['env']['ANTHROPIC_API_KEY'] == 'synthetic-secret'


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS native boundary')
def test_native_no_tool_process_cannot_read_or_write_root_or_external_workspace(tmp_path):
    root = tmp_path / 'root'; work = tmp_path / 'work'; temp = tmp_path / 'transport'
    for path in (root, work): path.mkdir()
    temp.mkdir(mode=0o700)
    for path in (root, work): (path / 'marker').write_text('UNCHANGED')
    fake = tmp_path / 'fake-claude'
    fake.write_text('#!' + sys.executable + '\nimport json,sys\nfrom pathlib import Path\nsys.stdin.read()\n'
        + 'results=[]\nfor name in ' + repr([str(root / 'marker'), str(work / 'marker')]) + ':\n'
        + ' p=Path(name)\n for op in (lambda:p.read_bytes(),lambda:p.write_text("BAD")):\n'
        + '  try:op();results.append(False)\n  except PermissionError:results.append(True)\n'
        + 'print(json.dumps(results))\n')
    fake.chmod(0o700)
    step = cd.prepare_step(root, work, temp, {'id': 'D25'}, 'code task', REPORT, binary=fake)
    positive = subprocess.run(step['command'][3:], input=step['stdin'], text=True, capture_output=True,
                              cwd=step['cwd'], env=step['env'], timeout=10)
    assert positive.returncode == 0 and json.loads(positive.stdout) == [False, False, False, False]
    assert all((path / 'marker').read_text() == 'BAD' for path in (root, work))
    for path in (root, work): (path / 'marker').write_text('UNCHANGED')
    done = subprocess.run(step['command'], input=step['stdin'], text=True, capture_output=True,
                          cwd=step['cwd'], env=step['env'], timeout=10)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [True, True, True, True]
    assert all((path / 'marker').read_text() == 'UNCHANGED' for path in (root, work))


def test_post_commit_drift_is_not_reported_as_success(prepared, monkeypatch):
    _, work, _, step = prepared; target = work / 'src/betting_engine/target.py'
    fsync = cd.os.fsync; injected = []
    def drift(fd):
        if not injected and target.read_text() == 'VALUE = 2\n':
            target.write_text('FOREIGN AFTER COMMIT\n'); injected.append(True)
        return fsync(fd)
    monkeypatch.setattr(cd.os, 'fsync', drift)
    with pytest.raises(cd.PatchApplicationError) as failure:
        cd.apply_edits(work, [edit(step)], step['context'])
    assert target.read_text() == 'FOREIGN AFTER COMMIT\n'
    assert failure.value.rollback_conflicts


def test_later_commit_cannot_hide_earlier_source_drift(prepared, monkeypatch):
    _, work, _, step = prepared; target = work / 'src/betting_engine/target.py'
    added = work / 'tests/test_new.py'; fsync = cd.os.fsync; injected = []
    def drift(fd):
        if not injected and added.exists():
            target.write_text('FOREIGN DURING LATER COMMIT\n'); injected.append(True)
        return fsync(fd)
    monkeypatch.setattr(cd.os, 'fsync', drift)
    with pytest.raises(cd.PatchApplicationError) as failure:
        cd.apply_edits(work, [edit(step), edit(step, 'tests/test_new.py', 'new')], step['context'])
    assert target.read_text() == 'FOREIGN DURING LATER COMMIT\n'
    assert failure.value.rollback_conflicts and not added.exists()


def test_rollback_rechecks_cas_after_staging_fsync(prepared, monkeypatch):
    _, work, _, step = prepared; target = work / 'src/betting_engine/target.py'
    fsync = cd.os.fsync; rolling = []; injected = []
    def fail_new(*args, **kwargs):
        rolling.append(True); raise OSError('second publication failure')
    def drift(fd):
        if rolling and not injected:
            target.write_text('FOREIGN DURING ROLLBACK\n'); injected.append(True)
        return fsync(fd)
    monkeypatch.setattr(cd.os, 'link', fail_new); monkeypatch.setattr(cd.os, 'fsync', drift)
    with pytest.raises(cd.PatchApplicationError) as failure:
        cd.apply_edits(work, [edit(step), edit(step, 'tests/test_new.py', 'new')], step['context'])
    assert target.read_text() == 'FOREIGN DURING ROLLBACK\n'
    assert failure.value.rollback_conflicts and not list(work.rglob('.claude-patch-*'))


def test_exclusive_new_file_commit_preserves_racing_writer(prepared, monkeypatch):
    _, work, _, step = prepared; target = work / 'tests/test_new.py'; link = cd.os.link
    def race(source, dest, **kwargs):
        target.write_text('FOREIGN NEW FILE\n'); return link(source, dest, **kwargs)
    monkeypatch.setattr(cd.os, 'link', race)
    with pytest.raises(cd.PatchApplicationError):
        cd.apply_edits(work, [edit(step, 'tests/test_new.py', 'new')], step['context'])
    assert target.read_text() == 'FOREIGN NEW FILE\n'
    assert not list(work.rglob('.claude-patch-*'))


@pytest.mark.parametrize('fault', ['staging_unlink', 'directory_fsync'])
def test_new_publication_failure_has_tracked_rollback(prepared, monkeypatch, fault):
    import stat
    _, work, _, step = prepared; target = work / 'tests/test_new.py'; injected = []
    unlink, fsync = cd.os.unlink, cd.os.fsync
    def fail_unlink(name, **kwargs):
        if not injected and target.exists() and str(name).startswith('.claude-patch-'):
            injected.append(True); raise OSError('unlink fault')
        return unlink(name, **kwargs)
    def fail_fsync(fd):
        if not injected and target.exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
            injected.append(True); raise OSError('directory fsync fault')
        return fsync(fd)
    monkeypatch.setattr(cd.os, 'unlink' if fault == 'staging_unlink' else 'fsync',
                        fail_unlink if fault == 'staging_unlink' else fail_fsync)
    with pytest.raises(cd.PatchApplicationError) as failure:
        cd.apply_edits(work, [edit(step), edit(step, 'tests/test_new.py', 'new')], step['context'])
    assert injected and failure.value.rollback_conflicts == []
    assert not target.exists() and not list(work.rglob('.claude-patch-*'))
    assert (work / 'src/betting_engine/target.py').read_text() == 'VALUE = 1\n'


@pytest.mark.parametrize('name', ['docs/immutable/new.json', 'docs/immutable/nested/new.md',
                                  'docs/validation-document.json', 'docs/candidate_fixture_repairs/new.json'])
def test_exact_and_directory_controller_inputs_are_never_editable(prepared, tmp_path, name):
    root, work, _, _ = prepared; directory = tmp_path / 'protected-step'; directory.mkdir(mode=0o700)
    step = cd.prepare_step(root, work, directory, {'id': 'D25'}, 'Propose changes', REPORT,
                           protected_paths=('docs/immutable', 'docs/validation-document.json'))
    with pytest.raises(ValueError, match='Protected file'):
        finish(step, [edit(step, name, 'new')])
    assert not (work / name).exists()
    # A sibling sharing only a text prefix is not the protected directory.
    assert finish(step, [edit(step, 'docs/immutable-neighbor/new.md', 'new')])['edits']
