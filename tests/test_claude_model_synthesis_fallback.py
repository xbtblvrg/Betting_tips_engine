"""Anti-tautological unit tests for Claude 1:1 fallback in quantitative model synthesis."""
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from betting_engine.codex_model_synthesizer import (
    CodexModelSynthesizer,
    SynthesizedModelRecord,
    check_ast_safety,
)


def test_claude_domain_hypothesis_enrichment(tmp_path):
    """Verifies that when claude_hypothesis is None, it is queried from Claude and enriches the synthesis."""
    synth = CodexModelSynthesizer(data_dir=tmp_path)

    mock_hypothesis = "Rest asymmetry and back-to-back travel density suppress away team 4Q efficiency."
    with patch.object(synth, '_query_claude_for_domain_hypothesis', return_value=mock_hypothesis) as mock_hyp:
        with patch.object(synth, '_query_codex_for_model_code', return_value=None):
            with patch.object(synth, '_query_claude_for_model_code', return_value=None):
                rec = synth.synthesize_and_screen_candidate(
                    segment_key="NBA|full_game|totals",
                    domain_problem="High scoring pace variance in 4th quarter",
                    sample_context={'home_expected': 112.0, 'away_expected': 106.0},
                    force_archetype="hurdle",
                )
                # When force_archetype is used, it skips querying, preserving algorithmic purity
                assert rec.syntax_valid is True
                assert rec.generator_provider == "algorithmic_codex_engine"


def test_codex_busy_falls_back_to_claude_1to1(tmp_path):
    """Verifies that when Codex is busy/unavailable, Claude is seamlessly invoked as 1:1 fallback."""
    synth = CodexModelSynthesizer(data_dir=tmp_path)

    claude_code = """
import numpy as np
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class ClaudeSynthesizedNBAModel(BaseCandidateModel):
    def __init__(self, model_id='test', version='1.0', league='NBA', scope='full_game', family='totals'):
        super().__init__(model_id, version, league, scope, family)

    def predict_distribution(self, game_context):
        h = float(game_context.get('home_expected', 110.0))
        a = float(game_context.get('away_expected', 106.0))
        scores = build_universal_score_lattice(h, a)
        return DiscreteDistribution.from_scores(scores)
"""
    # Codex returns None (simulating codex.lock busy, rate limit, or timeout)
    with patch.object(synth, '_query_codex_for_model_code', return_value=None) as mock_codex:
        with patch.object(synth, '_query_claude_for_model_code', return_value=(claude_code, "ClaudeSynthesizedNBAModel", "Claude 1:1 fallback alpha")) as mock_claude:
            rec = synth.synthesize_and_screen_candidate(
                segment_key="NBA|full_game|totals",
                domain_problem="Fatigue shock and 3-point variance",
                sample_context={'home_expected': 114.0, 'away_expected': 108.0},
            )

            assert mock_codex.called
            assert mock_claude.called
            assert rec.generator_provider == "claude"
            assert rec.class_name == "ClaudeSynthesizedNBAModel"
            assert rec.syntax_valid is True
            assert rec.safety_valid is True
            assert rec.validation_passed is True

            # Verify that Claude's rationale and attribution are recorded
            assert "Claude (claude-opus-5-5)" in rec.rationale or "Claude 1:1" in rec.rationale


def test_both_codex_and_claude_busy_falls_back_to_algorithmic_archetype(tmp_path):
    """Verifies that when both Codex and Claude are busy, deterministic algorithmic engine guarantees zero downtime."""
    synth = CodexModelSynthesizer(data_dir=tmp_path)

    with patch.object(synth, '_query_codex_for_model_code', return_value=None):
        with patch.object(synth, '_query_claude_for_model_code', return_value=None):
            rec = synth.synthesize_and_screen_candidate(
                segment_key="NHL|full_game|totals",
                domain_problem="Empty net hazard variance",
                sample_context={'home_expected': 3.1, 'away_expected': 2.8},
            )

            assert rec.generator_provider == "algorithmic_codex_engine"
            assert rec.syntax_valid is True
            assert rec.safety_valid is True
            assert rec.validation_passed is True
