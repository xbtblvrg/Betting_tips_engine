"""Verification of the high-fidelity verbose debug telemetry and AI cognitive feeding system.

Validates that:
1. Verbose logging records deep DEBUG events to rotating text and structured JSONL.
2. Mathematical decisions (EV, Kelly fraction, probabilities, clamps) are traced with precision.
3. Walk-Forward Crucible rules are traced with sample size, thresholds, and metrics.
4. Runtime anomalies and exceptions are captured with full stack tracebacks.
5. AI prompts receive rich, bounded, non-anchored runtime debug telemetry.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import tempfile
import time

import pytest

from betting_engine.verbose_logger import (
    QuantVerboseLogger,
    VERBOSE_LOG_NAME,
    VERBOSE_JSONL_NAME,
)


@pytest.fixture
def temp_quant_logger(tmp_path):
    """Creates an isolated QuantVerboseLogger with temporary data directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    logger = QuantVerboseLogger(data_dir=data_dir)
    yield logger, data_dir
    logger.close()


def test_verbose_logger_initialization_and_files(temp_quant_logger):
    logger, data_dir = temp_quant_logger
    logs_dir = data_dir / "logs"

    # Verify log directory and file creation
    assert logs_dir.exists()
    assert logger.log_file == logs_dir / VERBOSE_LOG_NAME
    assert logger.jsonl_file == logs_dir / VERBOSE_JSONL_NAME


def test_verbose_logger_event_emission_and_jsonl_schema(temp_quant_logger):
    logger, data_dir = temp_quant_logger

    test_data = {"alpha": 42, "model_id": "nhl_totals_v2", "brier_score": 0.185}
    logger.log_event(
        component="test_suite",
        event_type="unit_test_probe",
        message="Validating structured emission",
        data=test_data,
        level=logging.DEBUG,
    )

    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()

    assert logger.jsonl_file.exists()
    content = logger.jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) >= 1

    entry = json.loads(content[-1])
    assert entry["level"] == "DEBUG"
    assert entry["component"] == "test_suite"
    assert entry["event_type"] == "unit_test_probe"
    assert entry["quant_data"] == test_data
    assert "epoch_ns" in entry
    assert "timestamp" in entry


def test_trace_decision_mathematical_precision(temp_quant_logger):
    logger, data_dir = temp_quant_logger

    # Trace a real mathematical decision
    logger.trace_decision(
        candidate_id="cand_nfl_spread_001",
        fixture="Chiefs vs Bills",
        market="spreads:home:-2.5",
        prob=0.58432,
        odds=1.952,
        ev=0.14059,
        kelly_fraction=0.14768,
        final_stake=3.50,
        accepted=True,
        rejection_reason=None,
        details={"league": "NFL", "model_archetype": "HierarchicalWeibull"},
    )

    for h in logging.getLogger().handlers:
        h.flush()

    events = logger.get_recent_telemetry(component="decisions", limit=10)
    assert len(events) >= 1
    target = events[-1]

    assert target["component"] == "decisions"
    assert target["event_type"] == "candidate_evaluation"
    qdata = target["quant_data"]
    assert qdata["candidate_id"] == "cand_nfl_spread_001"
    assert qdata["model_probability"] == 0.58432
    assert qdata["market_odds"] == 1.952
    assert qdata["ev"] == 0.14059
    assert qdata["kelly_fraction"] == 0.14768
    assert qdata["final_stake_usd"] == 3.50
    assert qdata["accepted"] is True
    assert qdata["model_archetype"] == "HierarchicalWeibull"


def test_trace_crucible_rule_evaluation(temp_quant_logger):
    logger, data_dir = temp_quant_logger

    logger.trace_crucible(
        segment="NBA|full_game|spreads",
        model_name="NbaSpreadElasticNetV3",
        phase="Tier 1 -> Tier 2 Admission",
        metric="oos_roi",
        value=0.0485,
        threshold=0.0350,
        passed=True,
        sample_size=42,
        details={"brier_skill": 0.021, "max_drawdown": 0.12},
    )

    for h in logging.getLogger().handlers:
        h.flush()

    events = logger.get_recent_telemetry(component="crucible", limit=10)
    assert len(events) >= 1
    rec = events[-1]["quant_data"]
    assert rec["segment"] == "NBA|full_game|spreads"
    assert rec["metric"] == "oos_roi"
    assert rec["measured_value"] == 0.0485
    assert rec["passed"] is True
    assert rec["sample_size"] == 42
    assert rec["brier_skill"] == 0.021


def test_trace_runtime_anomaly_with_exception(temp_quant_logger):
    logger, data_dir = temp_quant_logger

    try:
        raise KeyError("missing_player_stat_field")
    except KeyError as exc:
        logger.trace_runtime_anomaly(
            component="feature_store",
            anomaly_type="missing_feature",
            details={"player_id": "lebron_james", "stat": "points_per_min"},
            exc=exc,
        )

    for h in logging.getLogger().handlers:
        h.flush()

    events = logger.get_recent_telemetry(component="feature_store", limit=10)
    assert len(events) >= 1
    err = events[-1]
    assert err["level"] == "ERROR"
    assert "exception" in err
    assert err["exception"]["type"] == "KeyError"
    assert "missing_player_stat_field" in err["exception"]["message"]
    assert "Traceback" in err["exception"]["traceback"]


def test_format_telemetry_for_ai_bounding_and_content(temp_quant_logger):
    logger, data_dir = temp_quant_logger

    # Simulate presence of service-error.log
    err_file = data_dir / "logs" / "service-error.log"
    err_file.write_text("Database locked during WAL checkpoint\nKeyError: 'target_scope'\n")

    # Add structured telemetry
    logger.trace_decision(
        candidate_id="cand_test_ai",
        fixture="Arsenal vs Chelsea",
        market="moneyline:home",
        prob=0.55,
        odds=2.10,
        ev=0.155,
        kelly_fraction=0.14,
        final_stake=2.50,
        accepted=True,
    )

    formatted = logger.format_telemetry_for_ai(
        task={"id": "D99", "title": "Model calibration fix"},
        prompt="Audit supervisor decision engine",
        max_chars=5000,
    )

    # Verify unvarnished facts are present
    assert "RECENT SERVICE & PROCESS ERRORS" in formatted
    assert "Database locked" in formatted
    assert "STRUCTURED QUANT DEBUG TELEMETRY" in formatted
    assert "cand_test_ai" in formatted
    assert len(formatted) <= 5000
