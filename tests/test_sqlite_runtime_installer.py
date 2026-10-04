import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('sqlite_installer',Path(__file__).resolve().parents[1]/'scripts/install_sqlite_runtime.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


def test_archive_hash_rejects_untrusted_body():
    with pytest.raises(ValueError,match='SHA3'):
        installer.verify_archive(b'not the official archive')


def test_path_activation_quotes_spaces_and_apostrophes():
    target=Path("/tmp/project's folder/python")
    code=installer.activation_text(target).decode()
    compile(code,'pth','exec')
    assert repr(str(target)) in code and 'DYLD' not in code


@pytest.fixture
def setup(tmp_path,monkeypatch):
    python=tmp_path/'.venv/bin/python';python.parent.mkdir(parents=True);python.write_text('python')
    site=tmp_path/'.venv/lib/python/site-packages';site.mkdir(parents=True)
    pth=site/installer.PTH_NAME
    runtime=tmp_path/'data/runtime'/('sqlite-'+installer.VERSION);runtime.mkdir(parents=True)
    source=tmp_path/'original.so';source.write_bytes(b'original extension')
    monkeypatch.setattr(installer.sys,'platform','darwin')
    monkeypatch.setattr(installer,'paths',lambda root:(python,pth,runtime))
    commands=[]
    def run(args,**kwargs):
        args=[str(a) for a in args];commands.append(args)
        if '-S' in args:return str(source)
        if args[0]=='/usr/bin/otool':
            return installer.OLD_DYLIB if args[-1]==str(source) else str(runtime/'libsqlite3.dylib')
        return ''
    monkeypatch.setattr(installer,'run',run)
    monkeypatch.setattr(installer,'verify_runtime',lambda *a:{'sqlite_version':installer.VERSION,'integrity':'ok'})
    def build(folder):
        path=folder/'libsqlite3.dylib';path.write_bytes(b'pinned dylib');return path
    monkeypatch.setattr(installer,'build_library',build)
    return tmp_path,pth,runtime,source,commands


def test_install_idempotent_and_rollback_original_pth(setup):
    root,pth,runtime,source,commands=setup
    pth.write_bytes(b'# original\n')
    manifest=installer.install(root)
    assert manifest['state']=='active'
    assert source.read_bytes()==b'original extension'
    assert any(c[0]=='/usr/bin/install_name_tool' and str(source) not in c for c in commands)
    count=len(commands)
    assert installer.install(root)==manifest and len(commands)==count
    assert installer.rollback(root)['state']=='rolled_back'
    assert pth.read_bytes()==b'# original\n'
    assert installer.rollback(root)['state']=='rolled_back'


def test_install_failure_restores_prior_activation(setup,monkeypatch):
    root,pth,*_=setup
    pth.write_bytes(b'# original\n')
    def fail(*a):raise ValueError('runtime wrong')
    monkeypatch.setattr(installer,'verify_runtime',fail)
    with pytest.raises(ValueError,match='runtime wrong'):installer.install(root)
    assert pth.read_bytes()==b'# original\n'


def test_rollback_removes_only_own_new_pth(setup):
    root,pth,*_=setup
    installer.install(root);installer.rollback(root)
    assert not pth.exists()


def test_external_activation_change_not_overwritten(setup):
    root,pth,*_=setup
    installer.install(root);pth.write_bytes(b'# operator change\n')
    with pytest.raises(ValueError,match='externally'):installer.rollback(root)
    with pytest.raises(ValueError,match='externally'):installer.install(root)
    assert pth.read_bytes()==b'# operator change\n'


def test_manual_matching_runtime_adopted_without_rebuilding(setup,monkeypatch):
    root,pth,runtime,source,commands=setup
    extension=runtime/'python'/source.name;extension.parent.mkdir();extension.write_bytes(b'patched')
    dylib=runtime/'libsqlite3.dylib';dylib.write_bytes(b'manual dylib')
    pth.write_bytes(installer.activation_text(extension.parent))
    monkeypatch.setattr(installer,'build_library',lambda *a:pytest.fail('must not rebuild installed runtime'))
    manifest=installer.install(root)
    assert manifest['build_provenance']=='externally_built_adopted_without_rebuild'
    assert not any(c[0]=='/usr/bin/install_name_tool' for c in commands)
    assert dylib.read_bytes()==b'manual dylib' and extension.read_bytes()==b'patched'
