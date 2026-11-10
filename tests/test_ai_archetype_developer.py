"""Unit tests for AI Archetype Developer Agent & Smart Algorithmic Engine."""
import json
from pathlib import Path
import sqlite3
import pytest

from betting_engine.ai_archetype_developer import (
    AIArchetypeDeveloperAgent,
    DistilledArchetypeRecord,
)
from betting_engine.dynamic_model_registry import DynamicArchetypeRegistry
from betting_engine.codex_model_synthesizer import CodexModelSynthesizer


@pytest.fixture
def isolated_env(tmp_path):
    """Creates an isolated database and directory environment for archetype developer tests."""
    db_file = tmp_path / "shadow_incubator.sqlite3"
    arch_dir = tmp_path / "dynamically_distilled"
    gen_dir = tmp_path / "generated"
    arch_dir.mkdir(parents=True, exist_ok=True)
    gen_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_synthesis_feedback_loop (
                id TEXT PRIMARY KEY,
                segment_key TEXT,
                league TEXT,
                scope TEXT,
                family TEXT,
                model_id TEXT,
                class_name TEXT,
                generator_provider TEXT,
                source_file_path TEXT,
                claude_hypothesis TEXT,
                codex_rationale TEXT,
                grok_critique TEXT,
                oos_sample_size INTEGER,
                oos_win_rate REAL,
                oos_simulated_roi REAL,
                oos_delta_brier REAL,
                oos_max_drawdown REAL,
                crucible_passed INTEGER DEFAULT 0,
                current_tier INTEGER DEFAULT 1,
                graduation_status TEXT DEFAULT 'quarantine',
                learned_insight TEXT,
                validation_errors TEXT,
                failure_reason TEXT,
                live_games_settled INTEGER DEFAULT 0,
                live_wins INTEGER DEFAULT 0,
                live_losses INTEGER DEFAULT 0,
                live_pushes INTEGER DEFAULT 0,
                live_win_rate REAL DEFAULT 0.0,
                live_realized_roi REAL DEFAULT 0.0,
                live_realized_pnl REAL DEFAULT 0.0,
                live_brier_delta REAL DEFAULT 0.0,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()

    agent = AIArchetypeDeveloperAgent(db_path=db_file, archetypes_dir=arch_dir, generated_dir=gen_dir)
    return {
        "agent": agent,
        "db_path": db_file,
        "arch_dir": arch_dir,
        "gen_dir": gen_dir,
        "tmp_path": tmp_path,
    }


def test_categorize_model():
    # Basketball possession lattice
    cat, leagues, fams = AIArchetypeDeveloperAgent.categorize_model(
        "NBAPossessionLatticeModel", "possession count lattice", "NBA", "spreads"
    )
    assert cat == "possession_lattice"
    assert "NBA" in leagues
    assert "WNBA" in leagues
    assert "spreads" in fams

    # Soccer Dixon-Coles
    cat2, leagues2, fams2 = AIArchetypeDeveloperAgent.categorize_model(
        "DixonColesSoccerModel", "bivariate poisson soccer dependence", "EPL", "totals"
    )
    assert cat2 == "bivariate_tail"
    assert "EPL" in leagues2
    assert "MLS" in leagues2
    assert "totals" in fams2


def test_distill_valid_candidate_model(isolated_env):
    agent = isolated_env["agent"]
    sample_code = """
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class SamplePossessionModel(BaseCandidateModel):
    def __init__(self, model_id='sample_01', version='1.0', league='NBA', scope='quarter_1', family='totals'):
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)

    def predict_distribution(self, game_context):
        return DiscreteDistribution.from_scores({(28, 26): 0.6, (26, 28): 0.4})
"""
    rec = agent.distill_from_candidate(
        model_id="sample_01",
        source_code=sample_code,
        class_name="SamplePossessionModel",
        league="NBA",
        scope="quarter_1",
        family="totals",
        oos_roi=0.185,
        delta_brier=0.015,
        provider="codex"
    )

    assert rec is not None
    assert rec.base_class_name == "SamplePossessionModel"
    assert rec.historical_oos_roi == 0.185
    assert (isolated_env["arch_dir"] / f"{rec.archetype_id}.py").exists()

    # Query catalog
    matches = agent.get_distilled_archetypes_for_market("NBA", "quarter_1", "totals")
    assert len(matches) == 1
    assert matches[0].archetype_id == rec.archetype_id


def test_distill_rejects_unsafe_code(isolated_env):
    agent = isolated_env["agent"]
    unsafe_code = """
import os
from betting_engine.sandbox_models.base import BaseCandidateModel

class MaliciousModel(BaseCandidateModel):
    def predict_distribution(self, game_context):
        os.system('echo bad')
        return None
"""
    rec = agent.distill_from_candidate(
        model_id="bad_01",
        source_code=unsafe_code,
        class_name="MaliciousModel",
        league="NBA",
        scope="full_game",
        family="totals",
        oos_roi=0.20,
    )
    assert rec is None


def test_bootstrap_from_existing_champions(isolated_env):
    agent = isolated_env["agent"]
    db_path = isolated_env["db_path"]
    gen_dir = isolated_env["gen_dir"]

    # Write a champion model to gen_dir
    champ_code = """
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class NBAChampLatticeModel(BaseCandidateModel):
    def __init__(self, model_id='champ_01', version='1.0', league='NBA', scope='half_1', family='totals'):
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)

    def predict_distribution(self, game_context):
        return DiscreteDistribution.from_scores({(55, 52): 0.7, (52, 55): 0.3})
"""
    champ_file = gen_dir / "champ_01.py"
    champ_file.write_text(champ_code, encoding="utf-8")

    # Insert champion record into feedback loop
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            INSERT INTO ai_synthesis_feedback_loop (
                id, segment_key, league, scope, family, model_id, class_name,
                source_file_path, oos_simulated_roi, oos_delta_brier, crucible_passed
            ) VALUES (
                'fb_01', 'NBA|half_1|totals', 'NBA', 'half_1', 'totals', 'champ_01',
                'NBAChampLatticeModel', ?, 0.22, 0.02, 1
            )
        """, (str(champ_file),))
        conn.commit()

    count = agent.bootstrap_from_existing_champions(min_roi=0.035)
    assert count == 1

    matches = agent.get_distilled_archetypes_for_market("NBA", "half_1", "totals")
    assert len(matches) == 1
    assert matches[0].base_class_name == "NBAChampLatticeModel"


def test_smart_algorithmic_synthesis_picks_distilled_archetype(isolated_env):
    agent = isolated_env["agent"]
    tmp_path = isolated_env["tmp_path"]

    # Seed catalog with a high-ROI basketball archetype
    sample_code = """
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution

class SmartDistilledBasketballModel(BaseCandidateModel):
    def __init__(self, model_id='distill_bb_01', version='1.0', league='NBA', scope='quarter_2', family='totals'):
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)

    def predict_distribution(self, game_context):
        return DiscreteDistribution.from_scores({(28, 25): 0.65, (25, 28): 0.35})
"""
    agent.distill_from_candidate(
        model_id="distill_bb_01",
        source_code=sample_code,
        class_name="SmartDistilledBasketballModel",
        league="NBA",
        scope="quarter_2",
        family="totals",
        oos_roi=0.25,
        delta_brier=0.03,
    )

    # Initialize synthesizer and generate algorithmic archetype
    synth = CodexModelSynthesizer(data_dir=tmp_path)
    code, cls_name, rationale = synth._generate_algorithmic_archetype(
        league="NBA", scope="quarter_2", family="totals", model_id="cand_smart_01"
    )

    assert "SmartDistilledBasketballModel" in cls_name
    assert "cand_smart_01" in code
    assert "Smart Algorithmic Distillation" in rationale
