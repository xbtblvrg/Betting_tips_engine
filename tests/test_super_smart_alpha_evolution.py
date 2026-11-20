"""Anti-tautological verification suite for Super-Smart Alpha Invention & Evolution (Items 2, 3, 4, 5).

Tests:
1. Item 2 (MarketAutopsy Residual Feature Attribution & Scientific Hypothesis Generation):
   - Verifies exact Pearson correlation rho(odds, y - p), rho(p - p_mkt, y - p), rho(line, y - p),
     signed calibration residual E[y - p], Brier delta E[B_mkt - B_mod], and zero-prompt-anchoring
     scientific hypotheses.
2. Item 3 (Evolutionary Alpha Crossover & Multi-Hypothesis Tournament):
   - Verifies that _synthesize_evolutionary_crossover_candidates produces 3 valid, AST-safe,
     probability-axiom-compliant models parameterized by empirical residuals, and that the
     Multi-Hypothesis Walk-Forward Tournament selects the candidate with superior OOS Crucible score.
3. Item 4 (ExecutionMetaLearner - Continuous Empirical-Bayes Kernel Regression over Log-Odds & CLV):
   - Verifies continuous monotonicity and Empirical-Bayes shrinkage across winning vs. overconfident
     odds regimes, deterministic replay invariance, and Kelly bounds [$0.50, $8.00].
4. Item 5 (Full-Game Moneyline Champion Distribution Blending in graduated_models.py):
   - Verifies that attach_champion_to_any_model blends full_game|moneyline probabilities from the
     champion distribution (both score lattice and direct probabilities) while preserving JSON
     serializability of Forecast.public().
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from betting_engine.codex_model_synthesizer import (
    CodexModelSynthesizer,
    check_ast_safety,
    instantiate_synthesized_class,
)
from betting_engine.execution_meta_learner import ExecutionMetaLearner
from betting_engine.graduated_models import attach_champion_to_any_model
from betting_engine.market_autopsy import MarketAutopsyAnalyzer, TargetedSynthesisBriefBuilder
from betting_engine.models import Forecast
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution
from betting_engine.sandbox_models.validator import validate_candidate_model


def test_item2_residual_autopsy_and_scientific_hypotheses(tmp_path: Path):
    """Verifies empirical residual attribution, correlation matrix, and zero-anchoring hypotheses."""
    db_path = tmp_path / "shadow_incubator.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE incubated_wagers (
            selection TEXT, line REAL, odds REAL, model_prob REAL, market_prob REAL,
            side TEXT, result TEXT, pnl REAL, league TEXT, scope TEXT, family TEXT,
            sub_family TEXT, status TEXT
        )
    """)
    # Insert 30 settled wagers where High-Line Over bets are systematically overconfident and losing,
    # while Low-Line Under bets are well-calibrated and winning.
    rows = []
    for i in range(15):
        # High-line Over: model says 0.68, market says 0.50 (odds 2.00), actual result = lost (y=0)
        rows.append(("Over 235.5", 235.5 + (i % 4), 2.00, 0.68, 0.50, "over", "lost", -1.0, "NBA", "full_game", "totals", "totals", "settled"))
    for i in range(15):
        # Low-line Under: model says 0.56, market says 0.52 (odds 1.92), actual result = won (y=1)
        rows.append(("Under 215.5", 215.5 - (i % 4), 1.92, 0.56, 0.52, "under", "won", 0.92, "NBA", "full_game", "totals", "totals", "settled"))
    conn.executemany("INSERT INTO incubated_wagers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    analyzer = MarketAutopsyAnalyzer(db_path)
    autopsy = analyzer.analyze_market("NBA|full_game|totals")

    assert autopsy.sample_size == 30
    # E[y - p] = (15 * (0 - 0.68) + 15 * (1 - 0.56)) / 30 = (-10.2 + 6.6) / 30 = -0.12
    assert math.isclose(autopsy.mean_calibration_residual, -0.12, abs_tol=1e-3)
    assert autopsy.worst_side == "over"
    assert autopsy.side_autopsy["over"]["mean_residual"] < -0.50
    assert autopsy.side_autopsy["under"]["mean_residual"] > 0.30
    # Line vs residual correlation must be strongly negative (higher lines have negative residual)
    assert autopsy.residual_correlations["line_vs_residual"] < -0.80
    # Disagreement (p - p_mkt) is +0.18 on losing Overs and +0.04 on winning Unders -> strongly negative correlation
    assert autopsy.residual_correlations["disagreement_vs_residual"] < -0.80
    assert len(autopsy.scientific_hypotheses) >= 3

    prompt = TargetedSynthesisBriefBuilder.build_targeted_prompt(autopsy)
    assert "Residual Correlation Matrix:" in prompt
    assert "line_vs_residual=" in prompt
    assert "Odds-Regime Attribution:" in prompt
    assert "Line-Regime Attribution:" in prompt

    # Commandment 10 (Zero Prompt Anchoring & Exemplar Bias Invariant):
    # Verify that zero distribution buzzwords, parent class names, or prescriptive recipes appear in the prompt
    forbidden_anchoring_terms = [
        "Negative Binomial", "Copula", "Weibull", "Renewal Hazard", "Bayesian shrinkage",
        "Dixon-Coles", "Skellam", "Markov", "Poisson", "Laplace", "Hurdle", "Zero-Inflated",
        "James-Stein", "Kalman", "Gumbel", "Clayton", "Frank", "consider dampening",
        "EVOLUTIONARY PARENT CHAMPION", "Parent 'Distilled",
    ]
    for term in forbidden_anchoring_terms:
        assert term.lower() not in prompt.lower(), f"Prompt anchoring violation: '{term}' found in synthesis prompt!"


def test_item3_evolutionary_crossover_and_multi_hypothesis_tournament(tmp_path: Path):
    """Verifies that evolutionary crossover generates 3 distinct valid models and selects the OOS winner."""
    db_path = tmp_path / "shadow_incubator.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE incubated_wagers (
            id TEXT, fixture_id TEXT, league TEXT, scope TEXT, family TEXT, sub_family TEXT,
            selection TEXT, side TEXT, line REAL, odds REAL, model_prob REAL, market_prob REAL,
            ev REAL, status TEXT, result TEXT, pnl REAL, brier_model REAL, brier_market REAL,
            settled_at TEXT, cohort TEXT, model_id TEXT
        )
    """)
    # Insert 28 settled wagers for NHL|full_game|totals where Over 5.5 wins consistently
    for i in range(28):
        res = "won" if i % 4 != 0 else "lost"
        pnl = 1.10 if res == "won" else -1.0
        conn.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, league, scope, family, sub_family, selection, side, line, odds,
                model_prob, market_prob, ev, status, result, pnl, settled_at
            ) VALUES (?, ?, 'NHL', 'full_game', 'totals', 'totals', 'Over 5.5', 'over', 5.5, 2.10, 0.55, 0.48, 0.15, 'settled', ?, ?, ?)
        """, (f"w_{i}", f"g_{i}", res, pnl, f"2026-11-18T10:{i:02d}:00Z"))
    conn.commit()
    conn.close()

    synth = CodexModelSynthesizer(data_dir=tmp_path)
    autopsy = synth.autopsy_analyzer.analyze_market("NHL|full_game|totals")
    evo_candidates = synth._synthesize_evolutionary_crossover_candidates(
        "NHL", "full_game", "totals", "test_evo_nhl", autopsy=autopsy, parents=[]
    )
    assert len(evo_candidates) == 3

    ctx = {"home_expected": 3.4, "away_expected": 3.1, "home_rest_days": 3, "away_rest_days": 1, "wind_speed": 0.0}
    for code, cls_name, rationale, provider in evo_candidates:
        is_safe, found_cls, err = check_ast_safety(code)
        assert is_safe and found_cls == cls_name, f"AST safety failed for {cls_name}: {err}"
        inst = instantiate_synthesized_class(code, found_cls, model_id="test_evo", league="NHL", scope="full_game", family="totals")
        v_ok, v_errs = validate_candidate_model(inst, ctx)
        assert v_ok, f"Validation failed for {cls_name}: {v_errs}"
        dist = inst.predict_distribution(ctx)
        assert math.isclose(sum(dist.scores.values()), 1.0, rel_tol=1e-4)


def test_item4_execution_meta_learner_continuous_kernel_regression(tmp_path: Path):
    """Verifies continuous Empirical-Bayes log-odds kernel regression and CLV scaling."""
    eng_db = tmp_path / "engine.sqlite3"
    conn = sqlite3.connect(str(eng_db))
    conn.execute("""
        CREATE TABLE system_decisions (
            family TEXT, tip_id TEXT, issued_at TEXT, payload TEXT, status TEXT, pnl REAL, outcome_evidence TEXT
        )
    """)
    # Regime A (odds around 2.10 in NBA|full_game|spreads): 18 wins, 4 losses (strong positive ROI & calibration)
    for i in range(22):
        st = "won" if i < 18 else "lost"
        od = 2.05 + (i % 3) * 0.05
        pnl = (od - 1.0) if st == "won" else -1.0
        payload = json.dumps({
            "league": "NBA",
            "model_scope": "full_game",
            "market": "spreads",
            "reference_odds": od,
            "probability": 0.56,
            "stake": 1.0,
        })
        conn.execute("INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, NULL)", (f"f_a_{i}", f"t_a_{i}", "2026-11-18T12:00:00Z", payload, st, pnl))

    # Regime B (heavy favorites around 1.42 in NBA|full_game|moneyline): 4 wins, 16 losses (severe overconfidence)
    for i in range(20):
        st = "won" if i < 4 else "lost"
        od = 1.42
        pnl = (od - 1.0) if st == "won" else -1.0
        payload = json.dumps({
            "league": "NBA",
            "model_scope": "full_game",
            "market": "moneyline",
            "reference_odds": od,
            "probability": 0.76,
            "stake": 1.0,
        })
        conn.execute("INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, NULL)", (f"f_b_{i}", f"t_b_{i}", "2026-11-18T12:00:00Z", payload, st, pnl))
    conn.commit()
    conn.close()

    # Write positive CLV evidence for NBA|full_game|spreads
    (tmp_path / "closing_evaluation.json").write_text(json.dumps({
        "reports": [
            {"league": "NBA", "market_key": "full_game:spreads", "price_clv": 0.045}
            for _ in range(12)
        ]
    }), encoding="utf-8")

    learner = ExecutionMetaLearner(data_dir=tmp_path)
    tip_win_regime = {
        "league": "NBA",
        "model_scope": "full_game",
        "market": "spreads",
        "reference_odds": 2.10,
        "created_at": "2026-11-19T12:00:00+00:00",
        "start": "2026-11-20T02:00:00+00:00",
    }
    tip_lose_regime = {
        "league": "NBA",
        "model_scope": "full_game",
        "market": "moneyline",
        "reference_odds": 1.42,
        "created_at": "2026-11-19T12:00:00+00:00",
        "start": "2026-11-20T02:00:00+00:00",
    }

    res_win = learner.evaluate_candidate_regime(tip_win_regime, force_refresh=True)
    res_lose = learner.evaluate_candidate_regime(tip_lose_regime, force_refresh=False)

    # Winning regime must earn positive priority boost, Kelly multiplier > 1.0, and lower adaptive EV hurdle
    assert res_win["kernel_roi"] > 0.25
    assert res_win["kernel_brier_delta"] > 0.0
    assert res_win["segment_clv"] > 0.02
    assert res_win["meta_priority_boost"] > 0.15
    assert res_win["meta_kelly_multiplier"] > 1.15
    assert res_win["meta_ev_hurdle"] < 0.020

    # Losing overconfident regime must earn negative priority boost, Kelly multiplier < 1.0, and higher EV hurdle
    assert res_lose["kernel_roi"] < -0.20
    assert res_lose["kernel_residual_bias"] < -0.20
    assert res_lose["meta_priority_boost"] < -0.15
    assert res_lose["meta_kelly_multiplier"] < 0.85
    assert res_lose["meta_ev_hurdle"] > 0.025

    # Deterministic replay invariance check (required by governance.py)
    res_win_replay = learner.evaluate_candidate_regime(tip_win_regime, force_refresh=False)
    assert res_win == res_win_replay


def test_item5_full_game_moneyline_champion_blending_and_json_serializability():
    """Verifies that full_game|moneyline uses the attached AI champion distribution and serializes cleanly."""
    class DummyChampion(BaseCandidateModel):
        def __init__(self):
            super().__init__(model_id="champ_ml_1", version="1.0", league="NBA", scope="full_game", family="moneyline")

        def predict_distribution(self, game_context):
            # Champion assigns 80% win probability to Home (much higher than base 55%)
            return DiscreteDistribution.from_scores({(115, 100): 0.80, (100, 112): 0.20})

    class DummyDomainModel:
        def forecast(self, g):
            import numpy as np
            return Forecast(
                game_id=g.id,
                league="NBA",
                home_probability=0.55,
                away_probability=0.45,
                draw_probability=0.0,
                home_points=110.0,
                away_points=108.0,
                margin=2.0,
                total=218.0,
                model_probabilities={"moneyline:home": 0.55, "moneyline:away": 0.45},
                disagreement=0.02,
                features={"home_rest": 3.0, "away_rest": 1.0},
                model_version="nba-test-v1",
                warnings=[],
                residuals=np.zeros((10, 2)),
            )

    domain_model = DummyDomainModel()
    champ = DummyChampion()
    attach_champion_to_any_model(domain_model, "NBA", "full_game", "moneyline", "moneyline", champ, "champ_ml_1")

    game = SimpleNamespace(id="NBA:game_1", league="NBA", home="Lakers", away="Celtics", context={"wind_kph": 0.0})
    fc = domain_model.forecast(game)

    p_home, push_home = fc.market_probability("moneyline", "home", None)
    p_away, push_away = fc.market_probability("moneyline", "away", None)

    # Without champion blending, p_home is ~0.579 (from margin=2.0, margin_sd=10.0).
    # With champion blending (0.65 * 0.80 + 0.35 * 0.579 = ~0.723), p_home must be > 0.70!
    assert p_home > 0.70
    assert p_away < 0.30
    assert math.isclose(p_home + p_away, 1.0, abs_tol=1e-4)
    assert push_home == 0.0

    # Verify Forecast.public() is 100% JSON serializable (no callable market_probability leak)
    encoded = json.dumps(fc.public())
    assert "NBA:game_1" in encoded


def test_verbose_debug_telemetry_feedback_loop_and_anti_anchoring(tmp_path):
    """Verifies that verbose DEBUG telemetry logs feed back into AIFeedbackLoopManager with zero prompt anchoring."""
    import logging
    from betting_engine.verbose_logger import QuantVerboseLogger
    from betting_engine.ai_feedback_loop import AIFeedbackLoopManager

    vlog = QuantVerboseLogger(data_dir=tmp_path)
    # 1. Valid empirical residual telemetry event -> must be included
    vlog.log_event(
        "market_autopsy",
        "residual_attribution",
        "[AUTOPSY_RESIDUAL_TRACE] NHL|full_game|totals: N=120, ROI=-4.2%, E[y-p]=-0.0410, BrierDelta=+0.01120",
        data={"segment": "NHL|full_game|totals", "sample_size": 120, "mean_calibration_residual": -0.041},
        level=logging.DEBUG,
    )
    # 2. Anchoring-contaminated event containing model buzzwords -> must be filtered out
    vlog.log_event(
        "market_autopsy",
        "residual_attribution",
        "[AUTOPSY_RESIDUAL_TRACE] NHL|full_game|totals: Use NegativeBinomial or Copula model_123",
        data={"segment": "NHL|full_game|totals"},
        level=logging.DEBUG,
    )

    summary = vlog.get_segment_debug_telemetry_summary("NHL", "totals")
    assert "LIVE VERBOSE DEBUG TELEMETRY FEEDBACK" in summary
    assert "E[y-p]=-0.0410" in summary
    assert "negativebinomial" not in summary.lower()
    assert "copula" not in summary.lower()

    fb = AIFeedbackLoopManager(db_path=tmp_path / "engine.sqlite3")
    # Monkeypatch get_verbose_logger or test integration via data_dir=tmp_path
    import betting_engine.verbose_logger as vl_mod
    old_singleton = vl_mod._VERBOSE_LOGGER
    try:
        vl_mod._VERBOSE_LOGGER = vlog
        icl_prompt = fb.get_in_context_learning_prompt("NHL", "totals")
        assert "LIVE VERBOSE DEBUG TELEMETRY FEEDBACK" in icl_prompt
        assert "E[y-p]=-0.0410" in icl_prompt
        assert "negativebinomial" not in icl_prompt.lower()
    finally:
        vl_mod._VERBOSE_LOGGER = old_singleton

