"""Unit and integration tests for SupremeSystemAuditor & Autonomous Healing Engine."""
from datetime import timedelta
import json
from pathlib import Path
import pytest
from betting_engine.domain import utcnow
from betting_engine.supreme_system_auditor import SupremeSystemAuditor, load_supreme_system_audit


def test_supreme_system_auditor_initialization(tmp_path):
    auditor = SupremeSystemAuditor(root=tmp_path, data_dir=tmp_path / 'data')
    assert auditor.root == tmp_path
    assert auditor.data_dir == tmp_path / 'data'
    assert auditor.audit_status_path == tmp_path / 'data' / 'supreme_system_audit.json'


def test_audit_constitutional_invariants_handles_empty_db(tmp_path):
    auditor = SupremeSystemAuditor(root=tmp_path, data_dir=tmp_path / 'data')
    inv = auditor.audit_constitutional_invariants()
    assert 'checked_at' in inv
    assert any(v.get('directive') == 10 for v in inv['violations'])


def test_audit_database_integrity_runs_without_crash(tmp_path):
    auditor = SupremeSystemAuditor(root=tmp_path, data_dir=tmp_path / 'data')
    res = auditor.audit_database_integrity()
    assert isinstance(res, dict)


def test_audit_code_and_tests_runs_and_reports_pass(tmp_path):
    auditor = SupremeSystemAuditor(root=Path(__file__).resolve().parents[1])
    res = auditor.audit_code_and_tests(targets=['tests/test_f0042_cycle_chronology.py'])
    assert res['status'] == 'passed'
    assert res['returncode'] == 0
    assert res['failures_count'] == 0


def test_synthesize_findings_detects_critical_severity():
    auditor = SupremeSystemAuditor()
    code_audit = {
        'status': 'failed',
        'failures': [{'test': 'test_dummy_failure', 'error_line': 'AssertionError: 1 != 2'}]
    }
    inv_audit = {'violations': [{'directive': 4, 'error': 'Stake bounds violated'}]}
    health_audit = {'degraded_components': [{'component': 'Model forecasts', 'state': 'degraded', 'detail': 'error'}]}
    db_audit = {'engine.sqlite3': {'integrity_ok': False, 'error': 'corrupt'}}

    findings = auditor.synthesize_findings(code_audit, inv_audit, health_audit, db_audit)
    assert len(findings) == 4
    severities = [f['severity'] for f in findings]
    assert severities.count('CRITICAL') == 3
    assert severities.count('HIGH') == 1


def test_load_supreme_system_audit(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    p = data_dir / 'supreme_system_audit.json'
    p.write_text(json.dumps({'status': 'optimal', 'findings_total': 0}))
    loaded = load_supreme_system_audit(data_dir=data_dir)
    assert loaded is not None
    assert loaded['status'] == 'optimal'


def test_run_full_system_audit_produces_atomic_report(tmp_path):
    auditor = SupremeSystemAuditor(root=Path(__file__).resolve().parents[1], data_dir=tmp_path / 'data')
    report = auditor.run_full_system_audit(include_pytest=False)
    assert 'audited_at' in report
    assert 'duration_seconds' in report
    assert 'status' in report
    assert 'constitutional_invariants' in report
    assert (tmp_path / 'data' / 'supreme_system_audit.json').exists()
