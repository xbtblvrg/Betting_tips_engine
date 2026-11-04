"""Tests for MarketAutopsyAnalyzer, TargetedSynthesisBriefBuilder, and DynamicArchetypeRegistry.

Anti-tautological test suite ensuring:
1. Real moment extraction (non-negative variance, valid probabilities in [0, 1]).
2. Targeted quantitative prompt construction matching exact market pathologies.
3. Safe dynamic archetype indexing, discovery, and instantiation.
"""
from pathlib import Path
import pytest

from betting_engine.historical_trainer import get_default_segment_context
from betting_engine.market_autopsy import (
    MarketAutopsy,
    MarketAutopsyAnalyzer,
    TargetedSynthesisBriefBuilder,
)
from betting_engine.dynamic_model_registry import (
    DynamicArchetypeRegistry,
    RegisteredArchetype,
)
from betting_engine.codex_model_synthesizer import (
    CodexModelSynthesizer,
    SynthesizedModelRecord,
    check_ast_safety,
)
from betting_engine.sandbox_models.base import BaseCandidateModel


def test_market_autopsy_extracts_real_moments_and_pathology(tmp_path):
    analyzer = MarketAutopsyAnalyzer()
    autopsy = analyzer.analyze_market("NBA|half_1|three_way")

    assert autopsy.segment_key == "NBA|half_1|three_way"
    assert autopsy.league == "NBA"
    assert autopsy.scope == "half_1"
    assert autopsy.family == "three_way"
    assert autopsy.home_expected > 0.0
    assert autopsy.away_expected > 0.0
    assert autopsy.dispersion_index > 0.0
    assert 0.0 <= autopsy.win_rate <= 1.0
    assert len(autopsy.recommended_distributions) > 0
    assert isinstance(autopsy.pathology_summary, str)
    assert len(autopsy.pathology_summary) > 0


def test_brief_builder_generates_dense_quant_prompt():
    autopsy = MarketAutopsy(
        segment_key="NBA|full_game|home_total",
        league="NBA",
        scope="full_game",
        family="home_total",
        sample_size=32540,
        win_rate=0.345,
        total_pnl=-12072.57,
        realized_roi=-0.371,
        pnl_by_side={'over': -8500.0, 'under': -3572.57},
        worst_side='over',
        home_expected=112.4,
        away_expected=108.2,
        dispersion_index=1.345,
        zero_frequency=0.0,
        recommended_distributions=['NegativeBinomialHurdle', 'PaceAdjustedGammaPoisson'],
        pathology_summary="Severe overdispersion on over lines",
    )

    prompt = TargetedSynthesisBriefBuilder.build_targeted_prompt(autopsy)
    assert "NBA|full_game|home_total" in prompt
    assert "112.40" in prompt
    assert "1.345" in prompt
    assert "NegativeBinomialHurdle" not in prompt
    assert "PaceAdjustedGammaPoisson" not in prompt
    assert "FIRST-PRINCIPLES MATHEMATICAL DERIVATION" in prompt
    assert "CRITICAL CASH LEAK: Side 'over'" in prompt


def test_brief_builder_handles_cold_start_truthfully():
    cold_autopsy = MarketAutopsy(
        segment_key="MLS|full_game|away_total",
        league="MLS",
        scope="full_game",
        family="away_total",
        sample_size=0,
        win_rate=0.0,
        total_pnl=0.0,
        realized_roi=0.0,
        home_expected=1.6,
        away_expected=1.2,
        dispersion_index=1.0,
        recommended_distributions=['DixonColesSoccerModel', 'ZeroInflatedNegativeBinomial']
    )
    prompt = TargetedSynthesisBriefBuilder.build_targeted_prompt(cold_autopsy)
    assert "Cold Start market" in prompt
    assert "Realized Win Rate: N/A" in prompt
    assert "0.0% win rate" not in prompt.lower()
    assert "DixonColesSoccerModel" not in prompt
    assert "ZeroInflatedNegativeBinomial" not in prompt
    assert "FIRST-PRINCIPLES MATHEMATICAL DERIVATION" in prompt


def test_dynamic_archetype_registry_discovery_and_instantiation():
    registry = DynamicArchetypeRegistry.get_instance()
    count = registry.refresh_registry()
    assert count >= 0

    # Test candidate query for EPL totals
    candidates = registry.get_dynamic_candidates_for_segment("EPL", "full_game", "totals")
    assert isinstance(candidates, list)
    for name, instance, params in candidates:
        assert isinstance(instance, BaseCandidateModel)
        assert hasattr(instance, "predict_distribution")
        assert hasattr(instance, "evaluate_market")


def test_synthesizer_uses_autopsy_and_calibrates_algorithmic_archetype(tmp_path):
    synthesizer = CodexModelSynthesizer(data_dir=tmp_path)
    
    # Force algorithmic synthesis on NFL spreads
    rec = synthesizer.synthesize_and_screen_candidate(
        segment_key="NFL|full_game|spreads",
        force_archetype="asymmetric_laplace"
    )

    assert isinstance(rec, SynthesizedModelRecord)
    assert rec.syntax_valid is True
    assert rec.safety_valid is True
    assert rec.generator_provider == "algorithmic_codex_engine"
    # Anti-constant check: ensure it used NFL baseline (~24 pts), NOT the old buggy 1.4 constant!
    assert "def_h = 1.4" not in rec.source_code
    assert "def_h = 24.5" in rec.source_code or "def_h = 28.0" in rec.source_code
