"""Unit tests for Multi-AI Reinforcement Feedback Loop (RLQS) and League Priority Synthesis."""
import pytest
from pathlib import Path

from betting_engine.ai_feedback_loop import (
    AIFeedbackLoopManager,
    AIFeedbackRecord,
    get_league_priority_weight,
)
from betting_engine.codex_model_synthesizer import CodexModelSynthesizer


def test_league_priority_ordering():
    """Verifies that American leagues (NBA, NFL) take top priority, followed by NHL, etc."""
    assert get_league_priority_weight('NBA') == 0
    assert get_league_priority_weight('NFL') == 0
    assert get_league_priority_weight('NHL') == 1
    assert get_league_priority_weight('MLB') == 2
    assert get_league_priority_weight('NCAAF') == 2
    assert get_league_priority_weight('ATP') == 3
    assert get_league_priority_weight('WTA') == 3
    assert get_league_priority_weight('MLS') == 4
    assert get_league_priority_weight('EPL') == 4
    assert get_league_priority_weight('UNKNOWN') == 5

    # NBA and NFL strictly precede NHL
    assert get_league_priority_weight('NBA') < get_league_priority_weight('NHL')
    assert get_league_priority_weight('NFL') < get_league_priority_weight('NHL')
    assert get_league_priority_weight('NHL') < get_league_priority_weight('MLS')


def test_feedback_loop_manager_lifecycle(tmp_path):
    """Verifies feedback recording, live settlement updates, and in-context learning retrieval."""
    db_file = tmp_path / "test_incubator.sqlite3"
    mgr = AIFeedbackLoopManager(db_path=db_file)

    # 1. Record profitable candidate
    rec_pass = AIFeedbackRecord(
        id="fb_nba_001",
        segment_key="NBA|full_game|totals",
        league="NBA",
        scope="full_game",
        family="totals",
        model_id="test_nba_model_001",
        class_name="NegativeBinomialHurdleModel_NBA_full_game",
        generator_provider="algorithmic_codex_engine",
        source_file_path="/tmp/test_nba.py",
        claude_hypothesis="Pace variation in 4th quarter requires overdispersion alpha=0.18",
        codex_rationale="Two-part hurdle negative binomial",
        oos_sample_size=120,
        oos_win_rate=0.58,
        oos_simulated_roi=0.065,
        oos_delta_brier=0.008,
        oos_max_drawdown=0.12,
        crucible_passed=True,
        current_tier=2,
        graduation_status="incubating",
    )
    assert mgr.record_synthesis_attempt(rec_pass) is True

    # 2. Record failed counter-example
    rec_fail = AIFeedbackRecord(
        id="fb_nba_002",
        segment_key="NBA|period_1|spreads",
        league="NBA",
        scope="period_1",
        family="spreads",
        model_id="test_nba_model_002",
        class_name="NaivePoissonSpreadModel_NBA",
        generator_provider="algorithmic_codex_engine",
        source_file_path="",
        oos_sample_size=50,
        oos_win_rate=0.42,
        oos_simulated_roi=-0.082,
        oos_delta_brier=-0.005,
        oos_max_drawdown=0.35,
        crucible_passed=False,
        current_tier=1,
        graduation_status="rejected",
    )
    assert mgr.record_synthesis_attempt(rec_fail) is True

    # 3. Verify in-context learning prompt incorporates empirical facts without class name anchoring
    prompt = mgr.get_in_context_learning_prompt(league="NBA", family="totals")
    assert "HISTORICAL EMPIRICAL ALPHA BENCHMARKS IN NBA" in prompt
    assert "+6.5%" in prompt
    assert "HISTORICAL EMPIRICAL CALIBRATION DEFICITS IN NBA" in prompt
    assert "-8.2%" in prompt
    # Strict anti-anchoring invariant: no class names or formula exemplars in prompt
    assert "NegativeBinomialHurdleModel_NBA_full_game" not in prompt
    assert "NaivePoissonSpreadModel_NBA" not in prompt

    # 4. Record live settlements
    # 5 wins, 0 losses
    for _ in range(5):
        mgr.record_settlement(
            model_id="test_nba_model_001",
            wager_result="won",
            odds=1.95,
            pnl=0.95,
            model_prob=0.55,
            market_prob=0.51,
        )

    # Check updated live prompt
    updated_prompt = mgr.get_in_context_learning_prompt(league="NBA", family="totals")
    assert "Live ROI:" in updated_prompt
    assert "5 games" in updated_prompt


def test_codex_synthesizer_integrates_feedback_manager(tmp_path):
    """Verifies that synthesis records attempts into the feedback database."""
    synth = CodexModelSynthesizer(data_dir=tmp_path)
    res = synth.synthesize_and_screen_candidate(
        segment_key="NBA|full_game|totals",
        domain_problem="NBA high scoring tempo and 3-point variance",
        sample_context={'home_expected': 114.0, 'away_expected': 108.0},
        force_archetype="hurdle",
        claude_hypothesis="Rest asymmetry creates overdispersion in late game scoring",
        grok_critique="Clamp variance to prevent blowout tail explosion"
    )

    try:
        assert res.syntax_valid is True
        assert res.safety_valid is True
        assert res.validation_passed is True

        # Check that it recorded into feedback manager
        fb_prompt = synth.feedback_manager.get_in_context_learning_prompt("NBA", "totals")
        assert len(fb_prompt) > 0
    finally:
        from betting_engine.codex_model_synthesizer import GENERATED_MODELS_DIR
        gen_file = GENERATED_MODELS_DIR / f"{res.model_id}.py"
        if gen_file.exists():
            gen_file.unlink()
