"""Unit tests for Codex Autonomous Code & Formula Synthesizer."""
import pytest
from pathlib import Path

from betting_engine.codex_model_synthesizer import (
    CodexModelSynthesizer,
    check_ast_safety,
    extract_clean_code,
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


def test_instantiate_synthesized_class_with_quant_primitives():
    code = """
from scipy.stats import poisson

class AdvancedCopulaModel(BaseCandidateModel):
    def __init__(self, model_id='copula_test', version='1.0', league='MLS', scope='full_game', family='three_way'):
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        self.copula = ClaytonCopula(theta=1.4)
        self.kalman = KalmanRatingTracker(initial_rating=100.0)

    def predict_distribution(self, game_context):
        # Marginal count distributions
        pmf_h = [poisson.pmf(i, 1.5) for i in range(6)]
        pmf_a = [poisson.pmf(i, 1.2) for i in range(6)]
        # Couple via Clayton copula into score lattice
        lattice = build_copula_score_lattice(pmf_h, pmf_a, self.copula)
        return DiscreteDistribution.from_scores(lattice)
"""
    is_safe, cls_name, err = check_ast_safety(code)
    assert is_safe is True
    assert cls_name == "AdvancedCopulaModel"

    model = instantiate_synthesized_class(
        code, cls_name, model_id="cop_001", league="MLS", scope="full_game", family="three_way"
    )
    dist = model.predict_distribution({})
    # Axiom: probabilities must sum to 1.0
    total_prob = dist.p_home + dist.p_draw + dist.p_away
    assert abs(total_prob - 1.0) < 1e-5
    assert dist.p_home > 0.0
    assert dist.p_draw > 0.0
    assert dist.p_away > 0.0


def test_instantiate_synthesized_class_with_dataclasses():
    code = """
from dataclasses import dataclass
from typing import Optional
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

@dataclass
class ModelParams:
    mu_home: float = 2.5
    mu_away: float = 1.8

class DataclassTestModel(BaseCandidateModel):
    def __init__(self, model_id='dc_test', version='1.0', league='NHL', scope='full_game', family='totals', params: Optional[ModelParams] = None):
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        self.params = params or ModelParams()

    def predict_distribution(self, game_context):
        return DiscreteDistribution.from_scores({(3, 2): 0.6, (2, 3): 0.4})
"""
    is_safe, cls_name, err = check_ast_safety(code)
    assert is_safe is True
    assert cls_name == "DataclassTestModel"

    model = instantiate_synthesized_class(
        code, cls_name, model_id="dc_001", league="NHL", scope="full_game", family="totals"
    )
    assert isinstance(model, BaseCandidateModel)
    dist = model.predict_distribution({})
    assert dist.p_home == 0.6
    assert dist.p_away == 0.4
    assert model.params.mu_home == 2.5


def test_codex_model_synthesizer_end_to_end(tmp_path):
    import sqlite3
    db_file = tmp_path / "shadow_incubator.sqlite3"
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incubated_wagers (
                id TEXT PRIMARY KEY,
                game_id TEXT,
                league TEXT,
                scope TEXT,
                family TEXT,
                sub_family TEXT,
                selection TEXT,
                line REAL,
                odds REAL,
                model_prob REAL,
                market_prob REAL,
                side TEXT,
                result TEXT,
                pnl REAL,
                captured_at TEXT,
                status TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS demoted_markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_key TEXT UNIQUE,
                league TEXT,
                scope TEXT,
                family TEXT,
                incubation_phase TEXT DEFAULT 'research_rnd',
                incubated_model_id TEXT,
                model_archetype TEXT,
                model_params TEXT,
                historical_roi REAL,
                historical_sample_size INTEGER
            )
        """)
        pattern = ['won', 'won', 'won', 'won', 'lost'] * 6
        for i, res_str in enumerate(pattern):
            odds_val = 2.00
            pnl_val = 1.0 if res_str == 'won' else -1.0
            day = (i % 28) + 1
            conn.execute(f"""
                INSERT INTO incubated_wagers (id, game_id, league, scope, family, sub_family, selection, line, odds, model_prob, market_prob, side, result, pnl, captured_at, status)
                VALUES ('w_{i}', 'g_{i}', 'NHL', 'period_3', 'totals', 'totals', 'Over 1.5', 1.5, {odds_val}, 0.65, 0.48, 'over', '{res_str}', {pnl_val}, '2026-10-{day:02d}T12:00:00Z', 'settled')
            """)
        conn.commit()

    synthesizer = CodexModelSynthesizer(data_dir=tmp_path)
    res = synthesizer.synthesize_and_screen_candidate(
        segment_key="NHL|period_3|totals",
        domain_problem="NHL 3rd period high-scoring right tail from empty net pulls",
        sample_context={'home_expected': 1.1, 'away_expected': 0.9},
        force_archetype="hurdle"
    )

    try:
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
    finally:
        from betting_engine.codex_model_synthesizer import GENERATED_MODELS_DIR
        gen_file = GENERATED_MODELS_DIR / f"{res.model_id}.py"
        if gen_file.exists():
            gen_file.unlink()


def test_extract_clean_code_with_invoke_tags_and_preamble():
    raw_claude_response = """I'll check whether `betting_engine` is available so I can match the real `DiscreteDistribution` API.
<invoke name="Bash">
<parameter name="command">python -c 'import betting_engine'</parameter>
</invoke>

Here is the candidate model class:
```python
import numpy as np
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class WNBAPossessionModel(BaseCandidateModel):
    def predict_distribution(self, game_context):
        return DiscreteDistribution({'home': 0.52, 'away': 0.48})
```
I hope this captures the 4Q fatigue dynamics!"""

    code = extract_clean_code(raw_claude_response)
    assert "<invoke" not in code
    assert "I'll check whether" not in code
    assert "class WNBAPossessionModel(BaseCandidateModel):" in code
    is_safe, cls_name, err = check_ast_safety(code)
    assert is_safe is True
    assert cls_name == "WNBAPossessionModel"
    assert err is None


def test_extract_clean_code_pure_english_fails_cleanly():
    raw_non_code = "I'm sorry, but I cannot fulfill this request without knowing the database schema."
    code = extract_clean_code(raw_non_code)
    assert code == ""
    is_safe, cls_name, err = check_ast_safety(code)
    assert is_safe is False
    assert cls_name is None
    assert "No candidate class definition found" in err
