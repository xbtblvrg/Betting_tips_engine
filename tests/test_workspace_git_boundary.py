"""Fresh isolated instruction discovery without granting access to parent Git."""
from pathlib import Path
import json
import pytest

from betting_engine.workspace_boundary import ensure_workspace_git_boundary, verify_workspace_git_boundary
from betting_engine.development import DevelopmentWorker, tree_manifest, validate_changes
from betting_engine.release_coordinator import ReleaseSandbox
from betting_engine.io_utils import atomic_json


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve()


def workspace(root, relative='data/development/workspaces/20261001T111322852503-D24'):
    path = root / relative
    path.mkdir(parents=True)
    (path / 'AGENTS.md').write_text('Only this workspace is authorized.\n')
    return path


@pytest.mark.parametrize('relative', ['data/development/workspace',
    'data/development/workspaces/20261001T111322852503-D24', 'data/releases/staging/release-20261001-a1b2'])
def test_three_existing_controller_namespaces_receive_local_boundary(root, relative):
    path = workspace(root, relative)
    before = tree_manifest(path)
    result = ensure_workspace_git_boundary(root, path)
    assert result['parent_access_granted'] is False
    assert (path / '.git/HEAD').read_text().startswith('ref: refs/heads/')
    assert (path / '.git/objects').is_dir() and (path / '.git/refs/heads').is_dir()
    assert 'remote' not in (path / '.git/config').read_text()
    assert tree_manifest(path) == before
    assert not (root / '.git').exists()


def test_idempotent_verification_never_rewrites_metadata_or_project_files(root):
    path = workspace(root)
    ensure_workspace_git_boundary(root, path)
    records = {str(p): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
               for p in path.rglob('*') if p.is_file()}
    ensure_workspace_git_boundary(root, path)
    assert records == {str(p): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
                       for p in path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('relative', ['', 'src', 'data/development', 'data/development/accepted',
    'data/development/runs/R1', 'data/development/workspaces', 'data/development/workspaces/.hidden',
    'data/development/workspaces/bad.name', 'data/development/workspaces/A/nested',
    'data/releases/staging', 'data/releases/staging/A/nested', 'data/development/workspace/A'])
def test_arbitrary_destinations_rejected_before_any_metadata_write(root, relative):
    path = root / relative
    path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        ensure_workspace_git_boundary(root, path)
    assert not (path / '.git').exists()


def test_missing_workspace_is_not_created(root):
    path = root / 'data/development/workspace'
    with pytest.raises(ValueError):
        ensure_workspace_git_boundary(root, path)
    assert not path.exists()


def test_external_and_relative_paths_rejected(root):
    with pytest.raises(ValueError):
        ensure_workspace_git_boundary(root, root.parent)
    with pytest.raises(ValueError):
        ensure_workspace_git_boundary(Path('.'), Path('data/development/workspace'))


@pytest.mark.parametrize('link_at', ['workspace', 'ancestor', 'root'])
def test_symlink_namespaces_rejected_without_touching_target(root, link_at):
    other = root / 'foreign'; other.mkdir()
    if link_at == 'workspace':
        path = root / 'data/development/workspace';path.parent.mkdir(parents=True);path.symlink_to(other, target_is_directory=True)
        actual_root = root
    elif link_at == 'ancestor':
        (root / 'data').symlink_to(other, target_is_directory=True)
        path = root / 'data/development/workspace';path.mkdir(parents=True)
        actual_root = root
    else:
        alias = root / 'alias';alias.symlink_to(other, target_is_directory=True)
        actual_root = alias;path = alias / 'data/development/workspace';path.mkdir(parents=True)
    with pytest.raises(ValueError):
        ensure_workspace_git_boundary(actual_root, path)
    assert not list(other.rglob('.git'))


@pytest.mark.parametrize('fault', ['git_file', 'git_link', 'HEAD_link', 'config_link', 'objects_link',
    'HEAD_modified', 'config_remote', 'config_oversize', 'extra_hook', 'extra_index', 'commondir',
    'missing_HEAD', 'refs_file', 'unexpected_object'])
def test_foreign_or_modified_git_metadata_is_never_replaced(root, fault):
    path = workspace(root); outside = root / 'foreign';outside.mkdir()
    marker = path / '.git'
    if fault == 'git_file':
        marker.write_text('gitdir: ../../../../../.git\n')
    elif fault == 'git_link':
        marker.symlink_to(outside, target_is_directory=True)
    else:
        ensure_workspace_git_boundary(root, path)
        if fault in ('HEAD_link', 'config_link', 'objects_link'):
            target = marker / fault.removesuffix('_link')
            if target.is_dir():target.rmdir()
            else:target.unlink()
            target.symlink_to(outside, target_is_directory=True)
        elif fault == 'HEAD_modified':(marker / 'HEAD').write_text('ref: refs/heads/foreign\n')
        elif fault == 'config_remote':(marker / 'config').write_text('[remote "origin"]\nurl=/parent\n')
        elif fault == 'config_oversize':(marker / 'config').write_bytes(b'x' * 100000)
        elif fault == 'extra_hook':(marker / 'hooks').mkdir();(marker / 'hooks/post-checkout').write_text('untrusted')
        elif fault == 'extra_index':(marker / 'index').write_bytes(b'candidate index')
        elif fault == 'commondir':(marker / 'commondir').write_text('../../parent/.git')
        elif fault == 'missing_HEAD':(marker / 'HEAD').unlink()
        elif fault == 'refs_file':(marker / 'refs/heads').rmdir();(marker / 'refs').rmdir();(marker / 'refs').write_text('foreign')
        elif fault == 'unexpected_object':(marker / 'objects/payload').write_text('foreign')
    before = {(str(p.relative_to(path)), p.is_symlink(), p.readlink().as_posix() if p.is_symlink() else p.read_bytes() if p.is_file() else None)
              for p in path.rglob('*')}
    with pytest.raises(ValueError):ensure_workspace_git_boundary(root, path)
    after = {(str(p.relative_to(path)), p.is_symlink(), p.readlink().as_posix() if p.is_symlink() else p.read_bytes() if p.is_file() else None)
             for p in path.rglob('*')}
    assert before == after
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('worker_type,relative', [(DevelopmentWorker, 'data/development/workspace'),
    (DevelopmentWorker, 'data/development/workspaces/R1'), (ReleaseSandbox, 'data/releases/staging/release-R1')])
@pytest.mark.parametrize('mode', ['workspace-write', 'read-only'])
def test_all_codex_command_paths_use_boundary_without_expanding_permissions(root, worker_type, relative, mode):
    path = workspace(root, relative);worker = worker_type(root);worker.workspace = path
    worker.config = {'codex_binary': '/bin/false', 'model': 'gpt-6-astra'}
    command = worker.command(path / 'schema.json', path / 'output.json', mode)
    verify_workspace_git_boundary(root, path)
    assert '--ignore-user-config' in command and '--skip-git-repo-check' in command
    assert command[command.index('-m') + 1] == 'gpt-6-astra'
    assert 'model_reasoning_effort="high"' in command and 'approval_policy="never"' in command
    permissions = next(a for a in command if a.startswith('permissions='))
    assert json.dumps(str(path / '.git')) + '="read"' in permissions
    assert json.dumps(str(root)) + '="read"' not in permissions
    assert 'network={enabled=false}' in permissions
    assert '":workspace_roots"={"."="' + ('read' if mode == 'read-only' else 'write') + '"}' in permissions


def test_command_rejects_foreign_git_before_returning_executable_argv(root):
    path = workspace(root);(path / '.git').write_text('gitdir: /foreign\n')
    worker = DevelopmentWorker(root);worker.workspace = path;worker.config = {'codex_binary': '/bin/false', 'model': 'gpt-6-astra'}
    with pytest.raises(ValueError):worker.command(path / 'schema.json', path / 'output.json')


def test_controller_module_remains_protected_from_candidate_changes():
    with pytest.raises(ValueError, match='Protected file changed'):
        validate_changes({}, {'src/betting_engine/workspace_boundary.py': 'foreign', 'tests/test_new.py': 'new'})


def test_accepted_snapshot_git_is_ignored_and_fresh_boundary_recreated(root):
    worker = DevelopmentWorker(root);worker.config = {'codex_binary': '/bin/false', 'model': 'gpt-6-astra'}
    accepted = worker.folder / 'accepted';accepted.mkdir()
    (accepted / '.git').write_text('gitdir: /foreign\n')
    (accepted / 'AGENTS.md').write_text('Retain exact instructions.\n')
    worker.prepare()
    assert not (worker.workspace / '.git').exists()
    assert (accepted / '.git').read_text() == 'gitdir: /foreign\n'
    worker.command(worker.workspace / 'schema.json', worker.workspace / 'output.json')
    verify_workspace_git_boundary(root, worker.workspace)


@pytest.mark.parametrize('failed_file', ['HEAD', 'config'])
def test_interrupted_creation_fails_closed_until_controller_recreates_workspace(root, monkeypatch, failed_file):
    path = workspace(root, 'data/development/workspace')
    original_open = Path.open
    def fail_creation(self, *args, **kwargs):
        if self == path / '.git' / failed_file and args and args[0] == 'xb':
            raise OSError('injected controller metadata write failure')
        return original_open(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', fail_creation)
        with pytest.raises(OSError, match='injected'):
            ensure_workspace_git_boundary(root, path)
    partial = {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='incomplete'):
        ensure_workspace_git_boundary(root, path)
    assert partial == {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}
    worker = DevelopmentWorker(root);worker.config = {'codex_binary': '/bin/false', 'model': 'gpt-6-astra'}
    worker.prepare()  # Existing controller lifecycle discards the failed attempt.
    worker.command(path / 'schema.json', path / 'output.json')
    verify_workspace_git_boundary(root, path)
