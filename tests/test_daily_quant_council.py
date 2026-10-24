"""Unit tests for Proactive Daily Multi-AI Quant Council Briefing."""
import json
from pathlib import Path
import pytest

from betting_engine.daily_quant_council import (
    DailyCouncilDirective,
    convene_daily_quant_council,
    load_daily_quant_strategy
)


def test_convene_daily_quant_council_basic(tmp_path):
    directive = convene_daily_quant_council(
        data_dir=tmp_path,
        as_of="2026-10-24T08:00:00Z",
        policy_override={"enabled": False}
    )

    assert isinstance(directive, DailyCouncilDirective)
    assert directive.briefing_date == "2026-10-24"
    assert "Gemini Antigravity" in directive.metadata["council_chair"]
    assert len(directive.high_conviction_alpha_targets) > 0
    assert "claude-opus-5-5" in directive.metadata["participating_brains"]
    assert "gpt-6-astra" in directive.metadata["participating_brains"]
    assert len(directive.claude_domain_insights) > 0
    assert len(directive.codex_math_guidance) > 0
    assert len(directive.grok_redteam_critique) > 0

    # Verify persistent file creation
    strat_file = tmp_path / "research" / "daily_quant_strategy.json"
    assert strat_file.exists()

    # Verify load_daily_quant_strategy
    loaded = load_daily_quant_strategy(tmp_path)
    assert loaded is not None
    assert loaded.briefing_date == "2026-10-24"
    assert loaded.chair_synthesis == directive.chair_synthesis


def test_daily_quant_council_caching_and_refresh(tmp_path):
    p_off = {"enabled": False}
    d1 = convene_daily_quant_council(data_dir=tmp_path, as_of="2026-10-24T08:00:00Z", policy_override=p_off)
    
    # Second call without force_refresh uses cache
    d2 = convene_daily_quant_council(data_dir=tmp_path, as_of="2026-10-24T09:00:00Z", force_refresh=False, policy_override=p_off)
    assert d1.created_at == d2.created_at

    # Force refresh generates fresh directive
    d3 = convene_daily_quant_council(data_dir=tmp_path, as_of="2026-10-24T10:00:00Z", force_refresh=True, policy_override=p_off)
    assert d3.created_at >= d1.created_at
