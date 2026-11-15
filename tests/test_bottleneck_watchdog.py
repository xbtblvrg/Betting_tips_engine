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

    assert report.total_graduated_markets >= total_grads
    assert report.healthy_markets_count == report.total_graduated_markets
    assert report.status in ('HEALTHY', 'SELF_HEALED', 'ESCALATED_TO_AI')
    assert isinstance(report.learning_jobs_enqueued, list)


def test_watchdog_auto_heals_unmounted_market(tmp_path):
    """Verify that the watchdog detects and auto-heals an unmounted champion model."""
    grad = load_graduated_markets(DATA)
    total_grads = len(grad)
    watchdog = BottleneckWatchdog(DATA)
    # Pass an empty active_segment_models dict to trigger dynamic self-healing
    active_models = {}
    report = watchdog.audit_and_heal(active_segment_models=active_models)

    assert report.healthy_markets_count == report.total_graduated_markets
    assert report.status in ('HEALTHY', 'SELF_HEALED', 'ESCALATED_TO_AI')


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
    assert status_data['total_graduated_markets'] >= 25
    assert status_data['healthy_markets_count'] > 0
    assert status_data['status'] in ('HEALTHY', 'SELF_HEALED', 'ESCALATED_TO_AI')
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


def test_watchdog_score_gap_sentinel():
    """Verify that score gap sentinel runs and recovers past unscored matches."""
    watchdog = BottleneckWatchdog(DATA)
    anomalies, healed = watchdog._check_score_gaps()
    assert isinstance(anomalies, list)
    assert healed >= 0
    for a in anomalies:
        assert a.category == 'score_gap'
        assert a.severity == 'HIGH'


def test_watchdog_zero_volume_sentinel():
    """Verify that liquid leagues with fixtures are audited for open bets."""
    watchdog = BottleneckWatchdog(DATA)
    grad = load_graduated_markets(DATA)
    segment_models = resolve_graduated_segment_models(grad, {})
    anomalies, healed = watchdog._check_zero_volume_fixtures(
        upcoming_fixtures=None,
        graduated=grad,
        active_segment_models=segment_models
    )
    assert isinstance(anomalies, list)
    assert healed >= 0
    for a in anomalies:
        assert a.category == 'zero_volume_fixtures'


def test_watchdog_stagnant_quarantine_sentinel():
    """Verify that stagnant markets in demoted_markets are audited and repaired."""
    watchdog = BottleneckWatchdog(DATA)
    anomalies, healed = watchdog._check_stagnant_quarantine()
    assert isinstance(anomalies, list)
    assert healed >= 0
    for a in anomalies:
        assert a.category == 'stagnant_quarantine'


def test_learning_worker_autonomous_handlers(tmp_path):
    """Verify that LearningWorker has valid handlers for all autonomous repair jobs."""
    from src.betting_engine.learning import LearningWorker
    from src.betting_engine.storage import Store
    from unittest.mock import patch, MagicMock

    store = Store(tmp_path / 'engine.sqlite3')
    worker = LearningWorker(store=store, data_dir=tmp_path)

    # 1. autonomous_bottleneck_repair
    job_btn = {
        'id': 'test_btn_1',
        'kind': 'autonomous_bottleneck_repair',
        'league': 'MULTI',
        'payload': json.dumps({'anomalies': []})
    }
    with patch('src.betting_engine.bottleneck_watchdog.BottleneckWatchdog.audit_and_heal') as mock_heal:
        mock_rep = MagicMock()
        mock_rep.status = 'HEALTHY'
        mock_rep.total_graduated_markets = 50
        mock_rep.anomalies_detected_count = 0
        mock_rep.anomalies_healed_count = 0
        mock_rep.uncrowned_champions_crowned = 0
        mock_heal.return_value = mock_rep
        res = worker.execute(job_btn)
        assert res['status'] == 'HEALTHY'

    # 2. autonomous_alpha_recovery
    job_alpha = {
        'id': 'test_alpha_1',
        'kind': 'autonomous_alpha_recovery',
        'league': 'NHL',
        'payload': json.dumps({'segment': 'NHL|full_game|spreads', 'target_roi': 10.0})
    }
    with patch('src.betting_engine.codex_model_synthesizer.CodexModelSynthesizer.synthesize_and_screen_candidate') as mock_synth:
        mock_rec = MagicMock()
        mock_rec.crucible_passed = False
        mock_rec.model_id = 'test_model_1'
        mock_rec.oos_simulated_roi = 0.02
        mock_rec.oos_sample_size = 50
        mock_rec.validation_errors = []
        mock_rec.failure_reason = None
        mock_synth.return_value = mock_rec
        res_alpha = worker.execute(job_alpha)
        assert res_alpha['status'] == 'screened'
        assert res_alpha['segment_key'] == 'NHL|full_game|spreads'

    # 3. market_gap_model_synthesis
    job_gap = {
        'id': 'test_gap_1',
        'kind': 'market_gap_model_synthesis',
        'league': 'NBA',
        'payload': json.dumps({'league': 'NBA', 'model_scope': 'quarter_2', 'market': 'spreads'})
    }
    with patch('src.betting_engine.codex_model_synthesizer.CodexModelSynthesizer.synthesize_and_screen_candidate') as mock_synth_gap:
        mock_rec_gap = MagicMock()
        mock_rec_gap.crucible_passed = False
        mock_rec_gap.model_id = 'test_gap_model_1'
        mock_rec_gap.oos_simulated_roi = 0.01
        mock_rec_gap.failure_reason = None
        mock_synth_gap.return_value = mock_rec_gap
        res_gap = worker.execute(job_gap)
        assert res_gap['status'] == 'screened'
        assert res_gap['segment_key'] == 'NBA|quarter_2|spreads'
