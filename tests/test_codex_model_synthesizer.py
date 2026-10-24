"""Unit tests for Codex Autonomous Code & Formula Synthesizer."""
import pytest
from pathlib import Path

from betting_engine.codex_model_synthesizer import (
    CodexModelSynthesizer,
    check_ast_safety,
    instantiate_synthesized_class
)
from betting_engine.sandbox_models.base import BaseCandidateModel


def test_ast_safety_checker_allows_safe_code():
    safe_code = """
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class SafeSampleModel(BaseCandidateModel):
    def predict_distribution(self, game_context):
        return DiscreteDistribution.from_scores({(1, 0): 0.6, (0, 1): 0.4})
"""
    is_safe, cls_name, err = check_ast_safety(safe_code)
    assert is_safe is True
    assert cls_name == "SafeSampleModel"
    assert err is None


def test_ast_safety_checker_blocks_unsafe_code():
    unsafe_code = """
import os
from betting_engine.sandbox_models.base import BaseCandidateModel

class MaliciousModel(BaseCandidateModel):
    def predict_distribution(self, game_context):
        os.system('echo dangerous')
        return None
"""
    is_safe, cls_name, err = check_ast_safety(unsafe_code)
    assert is_safe is False
    assert "disallowed import 'os'" in err


def test_instantiate_synthesized_class():
    code = """
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class DynamicTestModel(BaseCandidateModel):
    def __init__(self, model_id='test', version='1.0', league='NHL', scope='full_game', family='totals'):
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)

    def predict_distribution(self, game_context):
        return DiscreteDistribution.from_scores({(3, 2): 0.55, (2, 3): 0.45})
"""
    model = instantiate_synthesized_class(
        code, "DynamicTestModel", model_id="dyn_001", league="NHL", scope="full_game", family="totals"
    )
    assert isinstance(model, BaseCandidateModel)
    assert model.model_id == "dyn_001"
    dist = model.predict_distribution({})
    assert dist.p_home == 0.55
    assert dist.p_away == 0.45


def test_codex_model_synthesizer_end_to_end(tmp_path):
    synthesizer = CodexModelSynthesizer(data_dir=tmp_path)
    res = synthesizer.synthesize_and_screen_candidate(
        segment_key="NHL|period_3|totals",
        domain_problem="NHL 3rd period high-scoring right tail from empty net pulls",
        sample_context={'home_expected': 1.1, 'away_expected': 0.9}
    )

    assert res.syntax_valid is True
    assert res.safety_valid is True
    assert res.validation_passed is True
    assert len(res.validation_errors) == 0
    assert res.crucible_passed is True
    assert res.is_admitted_to_incubator is True
    assert "NegativeBinomialHurdleModel" in res.class_name

    # Check tournament registration
    active_cands = synthesizer.tournament.get_active_candidates("NHL|period_3|totals")
    assert any(c.model_id == res.model_id for c in active_cands)
