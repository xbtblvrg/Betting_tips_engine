"""Controller-owned local Git discovery boundary for isolated Codex workspaces.

No parent repository, configuration, hooks, remotes, commits or executable is
used. The small valid repository exists only to stop instruction discovery at
the already-authorized workspace. Native permissions remain authoritative.
"""
from pathlib import Path
import re

VERSION = 'isolated-workspace-git-boundary-v1'
_FILES = {
    'HEAD': b'ref: refs/heads/workspace\n',
    'config': b'[core]\n\trepositoryformatversion = 0\n\tbare = false\n',
}
_DIRECTORIES = {'objects', 'refs', 'refs/heads'}
_SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}\Z')


def _workspace(root, workspace):
    root, workspace = Path(root), Path(workspace)
    if not root.is_absolute() or not workspace.is_absolute():
        raise ValueError('Workspace Git boundary requires absolute canonical paths')
    if root.resolve() != root or workspace.resolve() != workspace:
        raise ValueError('Workspace Git boundary rejects symlinks and noncanonical paths')
    try:
        relative = workspace.relative_to(root)
    except ValueError as exc:
        raise ValueError('Workspace Git boundary must remain in the isolated root namespace') from exc
    parts = relative.parts
    legacy = parts == ('data', 'development', 'workspace')
    child = (len(parts) == 4 and parts[:3] in (
        ('data', 'development', 'workspaces'), ('data', 'releases', 'staging'))
        and bool(_SAFE_ID.fullmatch(parts[3])))
    if not (legacy or child) or not workspace.is_dir():
        raise ValueError('Workspace Git boundary requires an existing isolated workspace')
    for path in (workspace, *workspace.parents):
        if path.is_symlink():
            raise ValueError('Workspace Git boundary rejects symlink ancestors')
        if path == root:
            break
    return workspace


def verify_workspace_git_boundary(root, workspace):
    """Reject foreign metadata, indirection, content changes and extra files."""
    marker = _workspace(root, workspace) / '.git'
    if marker.is_symlink() or not marker.is_dir():
        raise ValueError('Workspace Git boundary must be an ordinary local directory')
    found = set()
    for path in marker.rglob('*'):
        relative = path.relative_to(marker).as_posix()
        if path.is_symlink():
            raise ValueError('Workspace Git boundary rejects metadata symlinks')
        if relative in _DIRECTORIES and path.is_dir():
            found.add(relative)
        elif relative in _FILES and path.is_file():
            # Fixed tiny records; do not read an unbounded foreign file.
            with path.open('rb') as stream:
                body = stream.read(len(_FILES[relative]) + 1)
            if body != _FILES[relative]:
                raise ValueError('Workspace Git boundary metadata changed')
            found.add(relative)
        else:
            raise ValueError('Workspace Git boundary contains unexpected metadata')
    if found != set(_FILES) | _DIRECTORIES:
        raise ValueError('Workspace Git boundary metadata incomplete')
    return {'version': VERSION, 'path': '.git', 'parent_access_granted': False}


def ensure_workspace_git_boundary(root, workspace):
    """Create only missing controller metadata; never replace foreign metadata."""
    marker = _workspace(root, workspace) / '.git'
    if marker.is_symlink() or marker.exists():
        return verify_workspace_git_boundary(root, workspace)
    marker.mkdir(mode=0o700)
    for name in sorted(_DIRECTORIES, key=lambda value: (value.count('/'), value)):
        (marker / name).mkdir(mode=0o700)
    for name, body in _FILES.items():
        with (marker / name).open('xb') as stream:
            stream.write(body)
    return verify_workspace_git_boundary(root, workspace)
