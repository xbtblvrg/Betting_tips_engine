"""Large retained warnings must not erase the actual failed-test summary."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from betting_engine import runtime_errors as errors
from betting_engine.development import _enrich_runtime_diagnostic


def failure(tmp_path, raw, stderr=b''):
    root=tmp_path/'project';folder=root/'data/development';rid='large-log-D25'
    run=folder/'runs'/rid;run.mkdir(parents=True)
    (run/'python-tests.stdout.log').write_bytes(raw)
    (run/'python-tests.stderr.log').write_bytes(stderr)
    result={'task_id':'D25','failure_phase':'python-tests','error':'python-tests exited 1'}
    return folder,rid,result


def test_large_warning_log_retains_exact_failures_full_hash_and_export(tmp_path):
    body=b'warning line\n'*400000+b'FAILED tests/test_shared.py::test_fixture - ValueError: rejected fixture\n'
    assert len(body)>4*1024*1024
    folder,rid,result=failure(tmp_path,body)
    d=errors.failure_diagnostic(folder,rid,result)
    assert d['failed_tests']==['tests/test_shared.py::test_fixture']
    assert d['failed_test_count']==1 and d['truncated'] is False
    assert d['logs'][0]['bytes']==len(body)
    assert d['logs'][0]['sha256']==hashlib.sha256(body).hexdigest()
    assert errors.diagnostic_archive_valid(folder,d)
    workspace=tmp_path/'worker';workspace.mkdir()
    manifest=errors.export_failure_diagnostics(folder,workspace,{'source_run_id':rid,'diagnostic':d})
    assert manifest['state']=='verified'
    assert (workspace/'data/runtime-failure-evidence/python-tests.stdout.log').read_bytes()==body
    (folder/f'runs/{rid}/python-tests.stdout.log').write_bytes(body+b'changed')
    assert not errors.diagnostic_archive_valid(folder,d)
    with pytest.raises(ValueError):
        errors.export_failure_diagnostics(folder,workspace,{'source_run_id':rid,'diagnostic':d})


def test_over_bound_log_is_explicit_incomplete_not_empty_stderr_success(tmp_path,monkeypatch):
    monkeypatch.setattr(errors,'MAX_DIAGNOSTIC_LOG_BYTES',128)
    folder,rid,result=failure(tmp_path,b'x'*129)
    d=errors.failure_diagnostic(folder,rid,result)
    assert d['truncated'] is True
    assert len(d['unavailable_logs'])==1
    assert d['unavailable_logs'][0]['path'].endswith('stdout.log')
    assert d['unavailable_logs'][0]['error_type']=='ValueError'
    assert d['failed_tests']==[]
    workspace=tmp_path/'export';workspace.mkdir()
    m=errors.export_failure_diagnostics(folder,workspace,{'source_run_id':rid,'diagnostic':d})
    assert m['state']=='verified' and m['completeness']=='incomplete'
    assert m['truncated'] is True and m['unavailable_logs']==d['unavailable_logs']


def test_symlink_log_never_read_or_exported(tmp_path):
    folder,rid,result=failure(tmp_path,b'')
    p=folder/f'runs/{rid}/python-tests.stdout.log';p.unlink()
    other=tmp_path/'outside';other.write_text('FAILED tests/private.py::secret')
    p.symlink_to(other)
    d=errors.failure_diagnostic(folder,rid,result)
    assert d['truncated'] and d['failed_tests']==[]
    assert 'Symlink' in d['unavailable_logs'][0]['reason']
    assert all(not row['path'].endswith('stdout.log')for row in d['logs'])


def test_parser_migration_can_add_newly_readable_log_without_replacing_old_evidence(tmp_path):
    folder,rid,result=failure(tmp_path,b'FAILED tests/test_shared.py::test_fixture - ValueError\n')
    d=errors.failure_diagnostic(folder,rid,result)
    old={'parser_version':'pytest-failure-diagnostic-v2','logs':[d['logs'][1]]}
    previous={'diagnostic':old,'evidence':[{'path':'original-proof','sha256':'original'}]}
    before=deepcopy(previous)
    new=_enrich_runtime_diagnostic(folder,previous,d)
    assert new and new['diagnostic']==d
    assert previous==before
    assert new['evidence'][0]==before['evidence'][0]
    archive=folder/new['prior_diagnostic_record']['path']
    assert archive.is_file()
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==new['prior_diagnostic_record']['sha256']
    changed=deepcopy(d);changed['logs'][1]['sha256']='changed'
    assert _enrich_runtime_diagnostic(folder,previous,changed) is None
    assert _enrich_runtime_diagnostic(folder,{'diagnostic':d},d) is None
