"""Keep developer fixture routing separate from protected source authority."""
import ast
from pathlib import Path

import pytest

from betting_engine import frozen_validation_workspace as frozen
from betting_engine import native_test_protocol as native
from test_frozen_validation_workspace import fixture, put, saved_proof
from test_controller_dependency_recovery import pair


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
def test_private_test_database_is_post_run_output_only(fixture, suffix):
    name = 'data/engine.sqlite3' + suffix
    with frozen.frozen_validation_workspace(fixture.worker, fixture.prefix):
        put(fixture.worker.workspace, name, b'isolated test output')
    assert not (fixture.workspace / name).exists()
    assert saved_proof(fixture)['state'] == 'validated'


@pytest.mark.parametrize('suffix', ['', '-wal', '-shm', '-journal'])
def test_database_cannot_enter_from_candidate_inputs(fixture, suffix):
    put(fixture.workspace, 'data/engine.sqlite3' + suffix, b'not allowed as input')
    with pytest.raises(ValueError, match='Undeclared'):
        with frozen.frozen_validation_workspace(fixture.worker, fixture.prefix):
            pytest.fail('Unexpected input copied')


@pytest.mark.parametrize('fault', ['link', 'unknown', 'directory'])
def test_output_exception_does_not_authorize_other_paths(fixture, fault):
    with pytest.raises((ValueError, OSError)):
        with frozen.frozen_validation_workspace(fixture.worker, fixture.prefix):
            clone = fixture.worker.workspace
            if fault == 'link':
                (clone / 'data/engine.sqlite3').symlink_to(fixture.workspace / 'src/example.py')
            elif fault == 'directory':
                put(clone, 'data/engine.sqlite3/subfile', b'not a database')
            else:
                put(clone, 'data/unrelated.db', b'not allowed')
    assert saved_proof(fixture)['state'] == 'failed'


def current_source():
    return Path(native.__file__).with_name('development.py').read_text()


def test_only_exact_appended_developer_guidance_normalizes():
    source = current_source()
    old = source.replace('        from .developer_regression import developer_regression_instructions\n', '')
    old = old.replace("''' + developer_regression_instructions(self.workspace)", "'''")
    assert native._development_ast(source) == native._development_ast(old)
    assert native._developer_guidance_count(source) == 1
    assert native._developer_guidance_count(old) == 0


@pytest.mark.parametrize('fault', ['relocate', 'duplicate', 'argument', 'replace', 'prepend'])
def test_malformed_guidance_cannot_inherit_native_proof(fault):
    source = current_source()
    hook = '        from .developer_regression import developer_regression_instructions\n'
    if fault == 'relocate':
        source = source.replace(hook, '').replace('        release_policy = read_json', '        release_policy = read_json', 1)
        source = source.replace("        return f'''You are implementing", hook + "        return f'''You are implementing", 1)
    elif fault == 'duplicate':
        source = source.replace(hook, hook + hook, 1)
    elif fault == 'argument':
        source = source.replace('developer_regression_instructions(self.workspace)', 'developer_regression_instructions(self.root)')
    elif fault == 'replace':
        source = source.replace("''' + developer_regression_instructions(self.workspace)", "''' + 'replaced guidance'")
    else:
        source = source.replace("        return f'''You are implementing", "        return developer_regression_instructions(self.workspace) + f'''You are implementing", 1)
        source = source.replace("''' + developer_regression_instructions(self.workspace)", "'''")
    with pytest.raises(ValueError, match='guidance'):
        native._development_ast(source)


def test_existing_prompt_change_is_not_normalized_away():
    source = current_source()
    changed = source.replace('Manual wagering only.', 'Changed original protected instruction.')
    assert native._development_ast(source) != native._development_ast(changed)


def test_installed_guidance_cannot_be_removed_from_candidate(pair):
    root, work, _ = pair
    path = work / 'src/betting_engine/development.py'
    body = path.read_text()
    body = body.replace('        from .developer_regression import developer_regression_instructions\n', '')
    body = body.replace("''' + developer_regression_instructions(self.workspace)", "'''")
    path.write_text(body)
    with pytest.raises(ValueError, match='Unreviewed controller delta'):
        native._bindings(root, work)
