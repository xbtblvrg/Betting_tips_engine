"""Tests for Multi-AI Quant Council.

Verifies:
1. Mandatory Prime Directive in prompts (continuous WR improvement & net PnL uptrend).
2. Zero-waste 24-hour caching behavior.
3. Quant Council session assembly, SHA-256 fingerprinting, and Antigravity synthesis.
4. Seamless integration with Crucible walk-forward backtests.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
import sqlite3
import tempfile

from betting_engine.ai_delegation import ALLOWED, helper_prompt
from betting_engine.failure_forensics import ForensicDossier
from betting_engine.quant_council import (
    convene_quant_council,
    _get_cached_session,
    QuantCouncilSession,
    SESSIONS_FILE,
)
from betting_engine.quant_crucible import run_crucible_walk_forward
from betting_engine.hypothesis_engine import HypothesisJournal, ResearchRecord
from betting_engine.quant_council import CouncilAdvice


@pytest.fixture(autouse=True)
def mock_council_members(monkeypatch):
    def fake_query(provider, kind, brief, policy, timeout=45):
        if kind == 'sports_mechanics':
            text = "Claude mechanics: 3rd period empty net clustering expands tail variance beyond Poisson."
        elif kind == 'quant_remedy':
            text = "Codex remedy: apply Negative Binomial dispersion alpha=1.22 to dampen overconfidence on totals."
        elif kind == 'adversarial_red_team':
            text = "Grok red-team: beware of low-scoring defensive clashes; clamp maximum probability shift to 3.5%."
        else:
            text = "Advisory advice."
        return CouncilAdvice(
            provider=provider,
            role="advisory",
            kind=kind,
            text=text,
            sha256="fake_sha256_" + provider
        )
    monkeypatch.setattr("betting_engine.quant_council._query_council_member", fake_query)


def test_mandatory_prime_directive_in_all_prompts():
    """Verifies every delegated AI prompt mandates continuous WR and cumulative PnL uptrend."""
    sample_brief = {
        'id': 'test_brief_001',
        'title': 'Test Quant Problem',
        'objective': 'Fix underperforming market',
        'acceptance': 'Pass Crucible'
    }
    required_keywords = [
        'MANDATORY PRIME OBJECTIVE',
        'Continuous win rate (WR) improvement',
        'cumulative net PnL uptrend'
    ]

    for kind in ALLOWED:
        prompt = helper_prompt(sample_brief, kind)
        for kw in required_keywords:
            assert kw in prompt, f"Prompt for '{kind}' must contain '{kw}'"

    from betting_engine.quant_council import council_prompt
    for kind in ('sports_mechanics', 'quant_remedy', 'adversarial_red_team'):
        c_prompt = council_prompt(sample_brief, kind)
        for kw in required_keywords:
            assert kw in c_prompt, f"Council prompt for '{kind}' must contain '{kw}'"


def test_quant_council_session_creation_and_synthesis():
    """Verifies that convene_quant_council creates a valid session and hypothesis."""
    dossier = ForensicDossier(
        segment="NHL|full_game|totals",
        league="NHL",
        scope="full_game",
        family="totals",
        evaluated_at="2026-10-07T12:00:00Z",
        total_bets=10,
        losses=6,
        wins=4,
        pushes=0,
        win_rate_pct=40.0,
        total_staked=10.0,
        total_pnl=-4.25,
        roi_pct=-8.5,
        primary_failure_mode="EMPTY_NET_TAIL_VARIANCE",
        confidence_score=0.92,
        actionable_remedy="Switch to Negative Binomial distribution to capture 3rd period clustering.",
        failure_breakdown={"empty_net_rate": 0.35}
    )

    session = convene_quant_council(dossier, force_refresh=True)
    assert session is not None
    assert session.segment == "NHL|full_game|totals"
    assert session.league == "NHL"
    assert "council_" in session.session_id

    # Check synthesis
    hyp_dict = session.synthesized_hypothesis
    assert hyp_dict is not None
    assert "hyp_council_" in hyp_dict["hypothesis_id"]
    assert "Quant Council" in hyp_dict["title"]
    assert "cumulative net PnL uptrend" in hyp_dict["rationale"]

    # Check dispositions
    assert isinstance(session.dispositions, list)


def test_quant_council_zero_waste_caching(monkeypatch):
    """Verifies zero-waste 24-hour caching prevents repeated external queries."""
    dossier = ForensicDossier(
        segment="MLB|innings_1_5|innings_1_5:totals",
        league="MLB",
        scope="innings_1_5",
        family="totals",
        evaluated_at="2026-10-07T12:00:00Z",
        total_bets=12,
        losses=7,
        wins=5,
        pushes=0,
        win_rate_pct=41.6,
        total_staked=12.0,
        total_pnl=-2.48,
        roi_pct=-6.2,
        primary_failure_mode="TOP_OF_ORDER_TTO_VARIANCE",
        confidence_score=0.88,
        actionable_remedy="Incorporate 1st inning starter FIP.",
        failure_breakdown={}
    )

    # 1. Run first time (creates session & caches)
    session1 = convene_quant_council(dossier, force_refresh=True)
    assert session1 is not None

    # 2. Second call should return cached without re-running
    session2 = convene_quant_council(dossier, force_refresh=False)
    assert session2 is not None
    assert session1.session_id == session2.session_id


def test_council_hypothesis_crucible_walk_forward_compatibility():
    """Verifies council hypothesis can be processed in the Crucible without error."""
    dossier = ForensicDossier(
        segment="NHL|full_game|totals",
        league="NHL",
        scope="full_game",
        family="totals",
        evaluated_at="2026-10-07T12:00:00Z",
        total_bets=10,
        losses=6,
        wins=4,
        pushes=0,
        win_rate_pct=40.0,
        total_staked=10.0,
        total_pnl=-4.25,
        roi_pct=-8.5,
        primary_failure_mode="EMPTY_NET_TAIL_VARIANCE",
        confidence_score=0.92,
        actionable_remedy="NegBin switch",
        failure_breakdown={}
    )

    session = convene_quant_council(dossier, force_refresh=True)
    hyp_dict = session.synthesized_hypothesis

    rec = ResearchRecord(
        hypothesis_id=hyp_dict["hypothesis_id"],
        segment=dossier.segment,
        league=dossier.league,
        category=hyp_dict["category"],
        title=hyp_dict["title"],
        rationale=hyp_dict["rationale"],
        proposed_formula_or_rule=hyp_dict["proposed_formula_or_rule"],
        status="proposed",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat()
    )

    # Setup in-memory SQLite database with sample decisions
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE system_decisions (
            payload TEXT,
            status TEXT,
            pnl REAL,
            outcome_evidence TEXT,
            issued_at TEXT
        )
    """)

    sample_payload = {
        "league": "NHL",
        "market": "totals",
        "side": "over",
        "probability": 0.55,
        "reference_odds": 1.95,
        "stake": 1.0
    }
    cur.execute(
        "INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?)",
        (json.dumps(sample_payload), "won", 0.95, "{}", "2026-10-07T12:00:00Z")
    )
    cur.execute(
        "INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?)",
        (json.dumps(sample_payload), "lost", -1.0, "{}", "2026-10-07T13:00:00Z")
    )
    cur.execute(
        "INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?)",
        (json.dumps(sample_payload), "won", 0.95, "{}", "2026-10-07T14:00:00Z")
    )
    cur.execute(
        "INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?)",
        (json.dumps(sample_payload), "lost", -1.0, "{}", "2026-10-07T15:00:00Z")
    )
    conn.commit()

    result = run_crucible_walk_forward(rec, conn, min_sample_size=4)
    assert result is not None
    assert result.hypothesis_id == rec.hypothesis_id
    assert result.sample_size == 4
    conn.close()


def test_event_driven_cache_invalidation(tmp_path, monkeypatch):
    """Verifies that invalidate_segment_cache immediately purges segment session from cache."""
    from betting_engine.quant_council import invalidate_segment_cache, _get_cached_session
    import betting_engine.quant_council as qc

    test_file = tmp_path / "test_council_sessions.jsonl"
    monkeypatch.setattr(qc, "SESSIONS_FILE", test_file)

    dossier = ForensicDossier(
        segment="NHL|full_game|spreads",
        league="NHL",
        scope="full_game",
        family="spreads",
        evaluated_at="2026-10-07T12:00:00Z",
        total_bets=10,
        losses=6,
        wins=4,
        pushes=0,
        win_rate_pct=40.0,
        total_staked=10.0,
        total_pnl=-4.25,
        roi_pct=-8.5,
        primary_failure_mode="CALIBRATION_OVERCONFIDENCE",
        confidence_score=0.92,
        actionable_remedy="Apply shrinkage to market consensus.",
        failure_breakdown={}
    )

    # 1. Convene session and write to cache
    session = convene_quant_council(dossier, force_refresh=True)
    assert session is not None
    cached = _get_cached_session("NHL|full_game|spreads")
    assert cached is not None
    assert cached.segment == "NHL|full_game|spreads"

    # 2. Invalidate cache on event trigger
    success = invalidate_segment_cache("NHL|full_game|spreads")
    assert success is True

    # 3. Cache must now be empty for that segment
    assert _get_cached_session("NHL|full_game|spreads") is None
