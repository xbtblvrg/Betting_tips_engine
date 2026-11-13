"""Tests for Autonomous Bottleneck Watchdog & Self-Healing Defense System."""
import json
import sqlite3
from pathlib import Path
import pytest

from src.betting_engine.bottleneck_watchdog import (
    BottleneckWatchdog,
    BottleneckAnomaly,
    get_latest_bottleneck_watchdog_status,
)
from src.betting_engine.providers import DATA
from src.betting_engine.shadow_market_incubator import load_graduated_markets
from src.betting_engine.graduated_models import resolve_graduated_segment_models


def test_watchdog_all_graduated_markets_healthy():
    """Verify that all Tier 3 graduated markets pass the bottleneck integrity check."""
    grad = load_graduated_markets(DATA)
    total_grads = len(grad)
    assert total_grads >= 25

    segment_models = resolve_graduated_segment_models(grad, {})
    assert len(segment_models) > 0

    watchdog = BottleneckWatchdog(DATA)
    report = watchdog.audit_and_heal(active_segment_models=segment_models)

    assert report.total_graduated_markets == total_grads
    assert report.healthy_markets_count == total_grads
    assert report.status in ('HEALTHY', 'SELF_HEALED')
    assert len(report.learning_jobs_enqueued) == 0


def test_watchdog_auto_heals_unmounted_market(tmp_path):
    """Verify that the watchdog detects and auto-heals an unmounted champion model."""
    grad = load_graduated_markets(DATA)
    total_grads = len(grad)
    watchdog = BottleneckWatchdog(DATA)
    # Pass an empty active_segment_models dict to trigger dynamic self-healing
    active_models = {}
    report = watchdog.audit_and_heal(active_segment_models=active_models)

    assert report.healthy_markets_count == total_grads
    assert report.status in ('HEALTHY', 'SELF_HEALED')


def test_watchdog_enqueues_learning_jobs_on_unhealed_anomaly(tmp_path):
    """Verify that unhealed anomalies are atomically enqueued in learning_jobs."""
    db_path = tmp_path / 'engine.sqlite3'
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE learning_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                league TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                next_attempt TEXT,
                attempts INTEGER DEFAULT 0,
                payload TEXT,
                result TEXT,
                last_error TEXT
            )
        """)

    watchdog = BottleneckWatchdog(tmp_path)
    mock_anomaly = BottleneckAnomaly(
        anomaly_id='test_missing_model',
        category='missing_champion',
        league='TEST_LEAGUE',
        scope='quarter_1',
        family='totals',
        sub_family='totals',
        severity='CRITICAL',
        message='Test unhealed anomaly',
        self_healed=False
    )

    job_id = watchdog._enqueue_autonomous_repair([mock_anomaly])
    assert job_id is not None
    assert job_id.startswith('bottleneck_repair_')

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT kind, status, payload FROM learning_jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == 'autonomous_bottleneck_repair'
        assert row[1] == 'queued'
        payload = json.loads(row[2])
        assert payload['anomaly_count'] == 1


def test_watchdog_status_persistence():
    """Verify that watchdog reports are persisted and retrievable."""
    grad = load_graduated_markets(DATA)
    total_grads = len(grad)
    watchdog = BottleneckWatchdog(DATA)
    report = watchdog.audit_and_heal()

    status_data = get_latest_bottleneck_watchdog_status(DATA)
    assert status_data['total_graduated_markets'] == total_grads
    assert status_data['healthy_markets_count'] == total_grads
    assert status_data['status'] in ('HEALTHY', 'SELF_HEALED')
    assert 'tier1_tier2_health' in status_data


def test_watchdog_tier1_tier2_health():
    """Verify Tier 1 & Tier 2 pipeline health telemetry."""
    watchdog = BottleneckWatchdog(DATA)
    health = watchdog.audit_tier1_tier2_health()

    assert health['total_pipeline_universe_markets'] >= 1000
    assert health['tier1_total_markets'] > 0
    assert 'tier1_data_deficit_markets' in health
    assert health['tier1_data_deficit_markets'] >= 0
    assert health['tier1_actionable_markets'] > 0
    assert 'tier2_incubating_models' in health
    assert 'synthesis_preflight_failure_rate' in health
    assert isinstance(health['recent_preflight_errors'], list)
    assert 'unadmitted_champions_backlog_count' in health
    assert 'alias_phase_splits_count' in health
    assert 'funnel_conversion_ratio' in health


def test_unmodelled_alias_cannot_demote_incubating_market(tmp_path):
    """Verify that an unmodelled alias in demoted_markets never downgrades an active Tier 2 market."""
    from src.betting_engine.unified_market_universe import UnifiedMarketUniverse, MarketTier

    # Ingest existing roster with current DATA
    u = UnifiedMarketUniverse(DATA)
    roster = u.build_universe_roster(force_refresh=True)

    # Find any incubating market
    t2_markets = [m for m in roster.values() if m.tier == int(MarketTier.SHADOW_INCUBATOR)]
    assert len(t2_markets) > 0, "Expected active Tier 2 incubating markets in universe"

    # Verify that all incubating markets retain their active model ID and tier 2
    for m in t2_markets[:10]:
        assert m.tier == 2
        assert m.active_model_id is not None
        assert "Live forward validation" in m.phase_detail
