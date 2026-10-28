"""Unit tests for the Auto-Run Telemetry & AI Diagnostic Logging Kernel."""
import tempfile
from pathlib import Path
from src.betting_engine.autorun_logger import (
    log_autorun_event,
    get_autorun_logs,
    get_autorun_diagnostics,
    AutoRunErrorCode,
    init_autorun_db,
)


def test_autorun_logger_event_creation_and_query():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test_engine.sqlite3"
        init_autorun_db(test_db)

        # Log a few structured events
        e1 = log_autorun_event(
            cycle_id="test_cycle_1",
            stage="CYCLE_START",
            severity="INFO",
            message="Cycle started with 10 tips",
            payload={"tips_count": 10},
            run_type="15m_cron",
            store=type("MockStore", (), {"path": str(test_db)})(),
        )
        assert e1["cycle_id"] == "test_cycle_1"
        assert e1["stage"] == "CYCLE_START"

        e2 = log_autorun_event(
            cycle_id="test_cycle_1",
            stage="LINE_EVAL",
            severity="WARN",
            tip_id="tip_xyz",
            selection="Under 224.5",
            line=224.5,
            odds=1.91,
            stake=1.50,
            market="totals",
            market_name="Total (Incl. Overtime)",
            league="NBA",
            status="skipped",
            message="Line drifted, no +EV alternative",
            error_code=AutoRunErrorCode.LINE_DRIFT_NO_EV,
            payload={"available": ["Under 220.5", "Under 221.5"]},
            run_type="15m_cron",
            store=type("MockStore", (), {"path": str(test_db)})(),
        )
        assert e2["error_code"] == AutoRunErrorCode.LINE_DRIFT_NO_EV
        assert "mathematical expectation" in e2["ai_remediation_hint"]

        e3 = log_autorun_event(
            cycle_id="test_cycle_1",
            stage="PLACE",
            severity="INFO",
            tip_id="tip_abc",
            selection="Over 237.5",
            line=237.5,
            odds=2.21,
            stake=1.00,
            status="placed",
            message="Placed successfully",
            run_type="15m_cron",
            store=type("MockStore", (), {"path": str(test_db)})(),
        )
        assert e3["status"] == "placed"

        # Query logs back
        mock_store = type("MockStore", (), {"path": str(test_db)})()
        logs = get_autorun_logs(cycle_id="test_cycle_1", store=mock_store)
        assert len(logs) == 3

        warn_logs = get_autorun_logs(severity="WARN", store=mock_store)
        assert len(warn_logs) == 1
        assert warn_logs[0]["error_code"] == AutoRunErrorCode.LINE_DRIFT_NO_EV

        # Query diagnostics
        diag = get_autorun_diagnostics(hours=24.0, store=mock_store)
        assert diag["total_events"] == 3
        assert diag["total_placed"] == 1
        assert diag["total_skipped"] == 1
        assert diag["error_counts"].get(AutoRunErrorCode.LINE_DRIFT_NO_EV) == 1
        assert len(diag["actionable_recommendations"]) >= 1


def test_autorun_api_endpoints():
    from fastapi.testclient import TestClient
    from src.betting_engine.server import app

    client = TestClient(app)

    # 1. Log an event via API
    resp = client.post(
        "/api/autorun/log",
        json={
            "cycle_id": "test_api_cycle_42",
            "stage": "STAGE_START",
            "severity": "INFO",
            "tip_id": "tip_api_123",
            "selection": "Los Angeles Lakers ML",
            "line": 0.0,
            "odds": 1.95,
            "stake": 2.50,
            "market": "moneyline",
            "market_name": "Winner (Incl. Overtime)",
            "league": "NBA",
            "status": "in_progress",
            "message": "Initiating stage for Lakers ML",
            "run_type": "api_test",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["event"]["cycle_id"] == "test_api_cycle_42"

    # 2. Query logs via API
    resp_logs = client.get("/api/autorun/logs?cycle_id=test_api_cycle_42")
    assert resp_logs.status_code == 200
    logs_data = resp_logs.json()
    assert logs_data["status"] == "ok"
    events = logs_data["events"]
    assert len(events) >= 1
    assert any(e["cycle_id"] == "test_api_cycle_42" for e in events)

    # 3. Query diagnostics via API
    resp_diag = client.get("/api/autorun/diagnostics?hours=1")
    assert resp_diag.status_code == 200
    diag_data = resp_diag.json()
    assert diag_data["status"] == "ok"
    assert "total_events" in diag_data
    assert "remediation_recommendations" in diag_data

