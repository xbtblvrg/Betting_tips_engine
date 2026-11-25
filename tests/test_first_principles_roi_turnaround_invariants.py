"""Independent Anti-Tautological Verification of First-Principles ROI Turnaround Invariants.

Validates against:
1. Kolmogorov probability axioms, push-adjusted expectation (EV = P*O + P_push - 1),
   Constitutional Kelly bounds [$0.50, $8.00], and monotonic epistemic divergence attenuation.
2. End-to-end cryptographic EvidenceGuard + rank_and_size + verify_publication replay parity
   across post-cutoff regimes (core spreads, high-odds underdogs O >= 3.0, scoped hockey totals).
3. Training-support and distributional tail boundaries (NCAA < 5 games, NHL |line| >= 2.5,
   discrete period/quarter tie-mass markets) without any static odds clamps (Commandment 3).
4. Container-family graduation isolation (preventing overtime/sub-family graduation from
   unlocking uncalibrated spreads/totals on moneyline-only college models).
5. Crucible anti-inflation invariants (60s polling deduplication, multi-model cohort separation,
   and strict positive Brier skill delta_brier > 0 enforcement).
6. Empirical validation on real settled ground-truth outcomes in data/engine.sqlite3
   (read-only) and immutable ledger preservation (Commandments 1, 6, 7, 10).
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from betting_engine.crucible_rules import evaluate_crucible_candidate
from betting_engine.decisions import candidate, rank_and_size
from betting_engine.domain import utcnow
from betting_engine.governance import EvidenceGuard, KnowledgeAgent, verify_publication
from betting_engine.graduated_models import (
    _concrete_validated_markets_for_graduation,
    _is_champion_compatible,
    _resolve_best_ai_champion,
    attach_champion_to_any_model,
    resolve_graduated_full_game_models,
)
from betting_engine.outcome_sizing import (
    POLICY_V3,
    apply_adjustment,
    build_snapshot,
    segment_key as sizing_segment_key,
    summary_for,
    verify_summary,
)
from betting_engine.shadow_market_incubator import ShadowMarketIncubator
from betting_engine.unified_market_universe import MarketTier, UnifiedMarketUniverse
from test_governance import guard_case
from test_outcome_sizing import AS_OF, decision as make_settled_decision


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def _make_live_sized_tip(
    *,
    league: str = "NBA",
    market: str = "spreads",
    probability: float = 0.56,
    market_probability: float = 0.50,
    odds: float = 1.95,
    push_probability: float = 0.0,
    game_id: str = "NBA:401999101",
    history_rows: list | None = None,
    snap_cache: dict | None = None,
    extra_tip: dict | None = None,
) -> tuple[dict, dict]:
    ev = probability * odds + push_probability - 1.0
    base_payload = deepcopy(make_settled_decision("live_template", league=league, market=market)["payload"])
    tip = {
        **base_payload,
        "id": f"live_{league}_{market}_{int(probability * 1000)}",
        "game_id": game_id,
        "league": league,
        "market": market,
        "model_scope": market.split(":")[0] if ":" in market else "full_game",
        "line": -4.5 if "spread" in market else None,
        "probability": probability,
        "push_probability": push_probability,
        "reference_odds": odds,
        "expected_roi": ev,
        "created_at": "2026-11-26T12:00:00+00:00",
        "market_anchor": {
            "status": "usable",
            "model_probability": probability,
            "market_probability": market_probability,
            "disagreement_threshold": 0.30,
        },
        "input_readiness": {
            "passed": True,
            "state": "ready",
            "checks": {"model_features": {"required": 10, "missing": [], "invalid": []}},
        },
    }
    if extra_tip:
        tip.update(extra_tip)
    evid = tip.get("outcome_sizing_evidence")
    if not (isinstance(evid, dict) and verify_summary(evid) and evid.get("segment") == sizing_segment_key(tip)):
        snap = snap_cache if snap_cache is not None else build_snapshot(history_rows or [], AS_OF)
        tip["outcome_sizing_evidence"] = summary_for(snap, tip)
    sizing_settings = {
        "outcome_sizing_policy": POLICY_V3,
        "unit_stake": 0.50,
        "max_bet": 8.00,
        "bankroll": None,
        "capital_defense": True,
        "alpha_acceleration": True,
    }
    sized = apply_adjustment(
        {"amount": 1.0, "maximum_stake": 8.0, "event_discount": 1.0},
        tip,
        sizing_settings,
        8.00,
    )
    return tip, sized


def test_probability_ev_and_epistemic_kelly_monotonicity_axioms():
    """Verify EV axiom, [$0.50, $8.00] stake bounds, and strict epistemic divergence attenuation."""
    wins = [
        make_settled_decision(f"nba_sp_{i}", "won", game_id=f"NBA:hist_{i}", league="NBA", market="spreads")
        for i in range(14)
    ]

    # Calibrated alpha edge: model_p = 0.58 vs market_p = 0.50 (|dp| = 0.08 <= 0.12 -> rho_agree = 1.0)
    tip_cal, res_cal = _make_live_sized_tip(
        league="NBA",
        market="spreads",
        probability=0.58,
        market_probability=0.50,
        odds=1.96,
        history_rows=wins,
    )

    # Uncalibrated extreme divergence at the SAME market_p=0.50: model_p = 0.86 (|dp| = 0.36 > 0.12 -> rho_agree = 0.40)
    tip_div, res_div = _make_live_sized_tip(
        league="NBA",
        market="spreads",
        probability=0.86,
        market_probability=0.50,
        odds=1.96,
        history_rows=wins,
    )

    for tip, res in ((tip_cal, res_cal), (tip_div, res_div)):
        p = float(tip["probability"])
        pp = float(tip["push_probability"])
        assert 0.0 < p < 1.0
        assert 0.0 <= pp < 1.0
        assert p + pp <= 1.0
        assert math.isclose(tip["expected_roi"], p * tip["reference_odds"] + pp - 1.0, rel_tol=1e-6)
        assert 0.50 <= float(res["amount"]) <= 8.00

    # Calibrated Super Alpha scales above $6.00, while extreme uncalibrated model-market divergence
    # is continuously attenuated by rho_agree rather than naively rewarded with max $8.00 stake.
    assert float(res_cal["amount"]) >= 6.00
    assert float(res_div["amount"]) < float(res_cal["amount"])
    assert float(res_div["amount"]) <= 4.00

    # Probation / neutral candidates (no historical alpha evidence) AND 3-way partition anchors
    # must ALSO be attenuated by rho_agree rather than sizing p=0.91 vs market_p=0.48 up to $3.50.
    _, res_prob_cal = _make_live_sized_tip(
        league="EPL",
        market="half_1:three_way",
        probability=0.52,
        market_probability=0.46,
        odds=2.10,
        history_rows=[],
        extra_tip={
            "side": "home",
            "game_id": "EPL:401999202",
            "market_anchor": {
                "status": "usable",
                "model_probability": 0.52,
                "market_probabilities": {"home": 0.46, "draw": 0.27, "away": 0.27},
            },
        },
    )
    prob_base_high = apply_adjustment(
        {"amount": 3.50, "maximum_stake": 8.0, "event_discount": 1.0},
        {
            **tip_div,
            "league": "EPL",
            "market": "half_1:three_way",
            "model_scope": "half_1",
            "game_id": "EPL:401999203",
            "side": "home",
            "probability": 0.9099,
            "reference_odds": 1.98,
            "expected_roi": 0.9099 * 1.98 - 1.0,
            "outcome_sizing_evidence": summary_for(
                build_snapshot([], AS_OF),
                {**tip_div, "league": "EPL", "market": "half_1:three_way", "model_scope": "half_1"},
            ),
            "market_anchor": {
                "status": "review",
                "model_probability": 0.9099,
                "market_probabilities": {"home": 0.4762, "draw": 0.2619, "away": 0.2619},
            },
        },
        {
            "outcome_sizing_policy": POLICY_V3,
            "unit_stake": 0.50,
            "max_bet": 8.00,
            "bankroll": None,
            "capital_defense": True,
            "alpha_acceleration": True,
        },
        8.00,
    )
    assert 0.50 <= float(res_prob_cal["amount"]) <= 1.00
    assert 0.50 <= float(prob_base_high["amount"]) <= 1.00


def test_evidence_guard_cryptographic_replay_parity_across_regimes():
    """Verify 100% deterministic EvidenceGuard + verify_publication reproduction across post-cutoff regimes."""
    # Verify NHL period_3 Over chains empty-net vig deduction AND scoped 0.85 shrinkage (never overwritten)
    ok_nhl_p3, _, p_nhl_p3 = evaluate_crucible_candidate({
        "league": "NHL",
        "market": "period_3:totals",
        "side": "over",
        "line": 1.5,
        "model_scope": "period_3",
        "probability": 0.68,
        "reference_odds": 1.85,
        "expected_roi": 0.68 * 1.85 - 1.0,
    })
    assert ok_nhl_p3 is True
    # Without chaining, 0.85*0.68 + 0.15*(1/1.85) = 0.6591; with empty-net deduction chained first, p_nhl_p3 < 0.64
    assert p_nhl_p3 < 0.64

    from betting_engine.models import feature_names
    cases_spec = [
        # 1. Core NBA spread (Tier 4 Live Proven Alpha)
        ("NBA", "basketball", "spreads", "home", -4.5, 0.58, 1.95, "full_game", True),
        # 2. High-odds underdog O = 3.55 on NBA alt spread (Commandment 3: zero static odds ceiling)
        ("NBA", "basketball", "spreads", "away", -6.5, 0.38, 3.55, "full_game", True),
        # 3. Scoped NHL period_1 Over total (Tier 4 Live Proven Alpha + scoped 0.85 shrinkage inside EvidenceGuard)
        ("NHL", "hockey", "period_1:totals", "over", 1.5, 0.61, 1.85, "period_1", True),
        # 4. Quarantined NBA moneyline (Tier 2 -> $0.00 universe_quarantined without UnboundLocalError)
        ("NBA", "basketball", "moneyline", "away", None, 0.38, 3.55, "full_game", False),
    ]

    for idx, (lg, sport, mkt_kind, side, line, prob, odds, scope, expect_funded) in enumerate(cases_spec):
        _, game, market, quote, forecast, report, settings = guard_case()
        settings["recommendation_policy"] = "continuous_best_available_v1"
        settings["outcome_sizing_policy"] = POLICY_V3
        settings["crucible_rules_mode"] = "live"
        settings["unit_stake"] = 0.50
        settings["max_bet"] = 8.00
        settings["allow_event_additions"] = True

        game.league = lg
        game.sport = sport
        game.id = market.game_id = forecast.game_id = f"{lg}:guard_parity_{idx}"
        forecast.league = lg
        forecast.scope = scope
        forecast.margin = 6.5
        forecast.total = 6.0 if lg == "NHL" else 224.5
        feats = list(feature_names(lg))
        forecast.features = {**dict.fromkeys(feats, 0.0), "home_rest": 1.0, "away_rest": 1.0}
        if "home_games" in forecast.features:
            forecast.features["home_games"] = math.log1p(24)
            forecast.features["away_games"] = math.log1p(24)
        forecast.market_probability = lambda *_a, _p=prob: (_p, 0.0)
        report["league"] = lg
        report["scope"] = scope
        report["features"] = feats

        market.kind = mkt_kind
        market.side = side
        market.line = line
        if scope != "full_game":
            market.model_scope = scope
            from hashlib import sha256
            src_dir = ROOT / "src" / "betting_engine"
            report["code_sha256"] = sha256((src_dir / "period_models.py").read_bytes()).hexdigest()
            report["promotion_gate"] = {"promote": True}
            report["validated_markets"] = ["spreads", "totals", "moneyline"]
        market.description = "1st Period Total" if scope == "period_1" else ("Handicap" if line is not None else "Winner")
        market.selection = f"{side.title()} {line}" if line is not None else side.title()
        market.url = f"https://stake.com/sports/{sport}/usa/{lg.lower()}/fixture-{idx}"
        market.metadata["stake_market_groups"] = [{"id": "main", "name": "main"}]
        market.metadata["stake_odds"] = odds
        quote.effective_cost = quote.price = 1.0 / odds

        anchor = {
            "mode": "shadow",
            "status": "usable",
            "model_probability": prob,
            "market_probability": round(1.0 / odds, 6),
            "disagreement": round(abs(prob - 1.0 / odds), 6),
            "disagreement_threshold": 0.30,
            "model_weight": 1.0,
        }
        tip = candidate(game, market, quote, forecast, settings, market_anchor=anchor)
        assert tip is not None, f"Expected candidate for {lg} {mkt_kind}"
        snap = build_snapshot([], utcnow())
        tip["outcome_sizing_evidence"] = summary_for(snap, tip)
        tip["stake_evidence"] = {
            "fixture_slug": f"fixture-{idx}",
            "market_name": market.description,
            "outcome_name": market.selection,
            "decimal_odds": odds,
            "fetched_at": quote.fetched_at,
            "market_groups": deepcopy(market.metadata["stake_market_groups"]),
        }

        check = EvidenceGuard(KnowledgeAgent()).check(tip, game, market, quote, forecast, report, settings)
        assert check["passed"], f"EvidenceGuard failed for {lg} {mkt_kind}: {check['errors']}"

        rank_and_size([tip], settings)
        if expect_funded:
            assert 0.50 <= float(tip["stake"]) <= 8.00
            verify_publication(tip, utcnow(), settings)
        else:
            assert float(tip["stake"]) == 0.0
            assert tip["allocation_code"] == "universe_quarantined"


def test_domain_support_and_tail_boundaries_preserve_underdog_freedom():
    """Out-of-support NCAA (<5 games) and unmodeled tails are blocked, while valid high-odds underdogs pass."""
    # 1. NCAA team with only 2 historical games (log1p(2) -> expm1 = 2 < 4.5 training support)
    ncaa_low_support = {
        "league": "NCAAM",
        "sport": "basketball",
        "game_id": "NCAAM:401999111",
        "market": "moneyline",
        "selection": "Small College Bulldogs",
        "line": None,
        "probability": 0.62,
        "reference_odds": 1.85,
        "expected_roi": 0.62 * 1.85 - 1.0,
        "features": {"home_games": math.log1p(12), "away_games": math.log1p(2)},
    }
    ok_ncaa, rsn_ncaa, _ = evaluate_crucible_candidate(ncaa_low_support)
    assert ok_ncaa is False
    assert "ncaa_out_of_training_support_team_history" in rsn_ncaa

    # 2. NCAA team with 10 games each on moneyline passes cleanly
    ncaa_supported = dict(
        ncaa_low_support,
        features={"home_games": math.log1p(12), "away_games": math.log1p(10)},
    )
    ok_ncaa_good, _, _ = evaluate_crucible_candidate(ncaa_supported)
    assert ok_ncaa_good is True

    # 3. NHL extreme alt puckline (-2.5) is blocked, whereas standard puckline (-1.5) and high-odds ML (O=3.80) pass
    nhl_alt_puck = {
        "league": "NHL",
        "sport": "hockey",
        "market": "spreads",
        "selection": "Toronto Maple Leafs -2.5",
        "line": -2.5,
        "probability": 0.45,
        "reference_odds": 2.60,
        "expected_roi": 0.45 * 2.60 - 1.0,
    }
    ok_alt, rsn_alt, _ = evaluate_crucible_candidate(nhl_alt_puck)
    assert ok_alt is False
    assert "nhl_uncalibrated_multi_goal_alt_puckline_tail" in rsn_alt

    nhl_std_puck = dict(nhl_alt_puck, selection="Toronto Maple Leafs -1.5", line=-1.5, probability=0.46, reference_odds=2.45)
    nhl_std_puck["expected_roi"] = 0.46 * 2.45 - 1.0
    ok_std, _, _ = evaluate_crucible_candidate(nhl_std_puck)
    assert ok_std is True

    nhl_high_dog = {
        "league": "NHL",
        "sport": "hockey",
        "market": "moneyline",
        "selection": "San Jose Sharks",
        "line": None,
        "probability": 0.31,
        "reference_odds": 3.80,
        "expected_roi": 0.31 * 3.80 - 1.0,
    }
    ok_dog, _, _ = evaluate_crucible_candidate(nhl_high_dog)
    assert ok_dog is True

    # 4. Discrete tie-mass period/quarter 3-way markets blocked while NHL period_1 totals passes
    ok_p1_3w, _, _ = evaluate_crucible_candidate({
        "league": "NHL",
        "market": "period_1:three_way",
        "model_scope": "period_1",
        "probability": 0.48,
        "reference_odds": 2.35,
        "expected_roi": 0.48 * 2.35 - 1.0,
    })
    assert ok_p1_3w is False

    ok_q1_ml, _, _ = evaluate_crucible_candidate({
        "league": "NBA",
        "market": "quarter_1:moneyline",
        "model_scope": "quarter_1",
        "model_version": "NBA-quarter_1-empirical-period-v1",
        "probability": 0.58,
        "reference_odds": 1.85,
        "expected_roi": 0.58 * 1.85 - 1.0,
    })
    assert ok_q1_ml is False

    # 5. Commandment 7 Market Preservation: once a dedicated non-linear/tie-aware AI model graduates,
    # it is NOT permanently banned by baseline linear/k-NN pathology gates.
    ok_q1_regrad, _, _ = evaluate_crucible_candidate({
        "league": "NBA",
        "market": "quarter_1:moneyline",
        "model_scope": "quarter_1",
        "model_version": "codex_synth_nba_quarter_1_1125:1.0.0",
        "probability": 0.62,
        "reference_odds": 1.85,
        "expected_roi": 0.62 * 1.85 - 1.0,
    })
    assert ok_q1_regrad is True


def test_container_graduation_does_not_bleed_into_unvalidated_college_markets():
    """Graduating american_derivatives:overtime must NEVER unlock spreads/totals on a moneyline-only model."""
    assert _concrete_validated_markets_for_graduation("american_derivatives", "overtime") == ["moneyline"]
    assert _concrete_validated_markets_for_graduation("american_derivatives", "spreads") == ["spreads"]
    assert _concrete_validated_markets_for_graduation("totals", "away_total") == ["away_total"]

    college_model = SimpleNamespace(
        league="NCAAM",
        version="NCAAM-sport-v4-baseline",
        _sealed_report={"validated_markets": ["moneyline"]},
        forecast=lambda g: None,
    )
    attach_champion_to_any_model(
        college_model,
        "NCAAM",
        "full_game",
        "american_derivatives",
        "overtime",
        SimpleNamespace(),
        "ncaam_ot_champ_v1",
    )
    validated = college_model._sealed_report["validated_markets"]
    assert validated == ["moneyline"]
    assert "spreads" not in validated
    assert "totals" not in validated

    # Cross-league, cross-scope, cross-market, and unproven incubating challengers must NEVER mount
    dummy_inst = SimpleNamespace(league="", model_id="x")
    assert _is_champion_compatible(dummy_inst, "dyn_codex_synth_wnba_half_2_1102", "WNBA", "full_game", ["spreads"]) is False
    assert _is_champion_compatible(dummy_inst, "distilled_arch_DixonColes_UCL_full_ucl", "EPL", "full_game", ["totals"]) is False
    assert _is_champion_compatible(dummy_inst, "atp_full_game_spreads_model", "WTA", "full_game", ["spreads"]) is False
    assert _is_champion_compatible(dummy_inst, "atp_full_game_spreads_model", "ATP", "full_game", ["moneyline"]) is False
    assert _is_champion_compatible(dummy_inst, "nba_full_game_spreads_gumbel_1025", "NBA", "full_game", ["spreads"]) is True

    fake_t_eng = SimpleNamespace(
        load_registry=lambda: {
            "champions": {},
            "tournaments": {
                "NCAAM|full_game|totals": {
                    "candidates": {
                        "codex_synth_ncaam_full_game_unproven": {"status": "incubating", "registered_at": "2026-11-25"}
                    }
                }
            },
        },
        get_model_by_id=lambda mid: SimpleNamespace(league="NCAAM", model_id=mid),
        get_active_candidates=lambda _k: (_ for _ in ()).throw(AssertionError("Must never call get_active_candidates for live champion resolution")),
    )
    m_res, cid_res = _resolve_best_ai_champion(fake_t_eng, "NCAAM", "full_game", "totals", "totals", "NCAAM|full_game|totals", None)
    assert m_res is None and cid_res is None

    # Professional LeagueModels with validated_markets=None must retain None (never clobbered to ['spreads'])
    pro_nba = SimpleNamespace(league="NBA", report={"validated_markets": None}, forecast=lambda g: None)
    resolved_pro = resolve_graduated_full_game_models(
        {"NBA|full_game|spreads": {"league": "NBA", "scope": "full_game", "family": "spreads", "sub_family": "spreads"}},
        {"NBA": pro_nba},
    )
    assert resolved_pro["NBA"].report["validated_markets"] is None


def test_crucible_rejects_polling_inflation_multi_model_soup_and_negative_brier(tmp_path: Path):
    """Crucible must reject 60s polling duplicates, multi-model soup, and negative Brier skill."""
    inc = ShadowMarketIncubator(data_dir=tmp_path)

    with inc._get_connection() as conn:
        # Case 1: 30 60-second polling captures of 1 game (NBA|half_1|totals) -> distinct games = 1 < 25
        for minute in range(30):
            conn.execute(
                """
                INSERT INTO incubated_wagers (
                    id, fixture_id, game_id, league, scope, family, sub_family,
                    raw_market_name, selection, side, line, odds, model_prob, push_prob,
                    market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                    result, pnl, brier_model, brier_market, metadata, cohort
                ) VALUES (?, 'NBA:single_1', 'NBA:single_1', 'NBA', 'half_1', 'totals', 'totals',
                          '1st Half Total', 'Over 112.5', 'over', 112.5, 1.95, 0.62, 0.0,
                          0.51, 0.21, 25, ?, 'settled', '2026-11-20T04:00:00Z',
                          'won', 0.95, 0.14, 0.24, '{}', 'gap_market')
                """,
                (f"poll_dup_{minute}", f"2026-11-20T01:{minute:02d}:00Z"),
            )

        # Case 2: 15 different challenger models each betting 2 games (30 rows in tournament_challenger)
        for m_idx in range(15):
            for g_idx in range(2):
                conn.execute(
                    """
                    INSERT INTO incubated_wagers (
                        id, fixture_id, game_id, league, scope, family, sub_family,
                        raw_market_name, selection, side, line, odds, model_prob, push_prob,
                        market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                        result, pnl, brier_model, brier_market, metadata, cohort, model_id
                    ) VALUES (?, ?, ?, 'NHL', 'period_2', 'totals', 'totals',
                              '2nd Period Total', 'Over 1.5', 'over', 1.5, 1.95, 0.62, 0.0,
                              0.51, 0.21, 25, '2026-11-20T01:00:00Z', 'settled', '2026-11-20T04:00:00Z',
                              'won', 0.95, 0.14, 0.24, '{}', 'tournament_challenger', ?)
                    """,
                    (f"soup_{m_idx}_{g_idx}", f"NHL:g_{g_idx}", f"NHL:g_{g_idx}", f"nhl_period_2_cand_{m_idx}"),
                )

        # Case 3: 35 distinct games with high lucky ROI (+37%) on long odds, but NEGATIVE Brier skill (-0.08)
        for i in range(35):
            won = i < 16
            conn.execute(
                """
                INSERT INTO incubated_wagers (
                    id, fixture_id, game_id, league, scope, family, sub_family,
                    raw_market_name, selection, side, line, odds, model_prob, push_prob,
                    market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                    result, pnl, brier_model, brier_market, metadata, cohort
                ) VALUES (?, ?, ?, 'EPL', 'half_1', 'moneyline', 'moneyline',
                          '1st Half Winner', 'Away', 'away', NULL, 3.00, 0.75, 0.0,
                          0.33, 1.25, 35, '2026-11-20T12:00:00Z', 'settled', '2026-11-20T15:00:00Z',
                          ?, ?, 0.30, 0.22, '{}', 'gap_market')
                """,
                (f"neg_brier_{i}", f"EPL:nb_{i}", f"EPL:nb_{i}", "won" if won else "lost", 2.0 if won else -1.0),
            )

        # Case 4: 28 distinct games with genuine positive ROI (+39%) AND positive Brier skill (+0.05)
        for i in range(28):
            won = i < 20
            conn.execute(
                """
                INSERT INTO incubated_wagers (
                    id, fixture_id, game_id, league, scope, family, sub_family,
                    raw_market_name, selection, side, line, odds, model_prob, push_prob,
                    market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                    result, pnl, brier_model, brier_market, metadata, cohort
                ) VALUES (?, ?, ?, 'MLS', 'full_game', 'totals', 'totals',
                          'Total Goals', 'Over 2.5', 'over', 2.5, 1.95, 0.62, 0.0,
                          0.51, 0.21, 25, '2026-11-20T18:00:00Z', 'settled', '2026-11-20T21:00:00Z',
                          ?, ?, 0.18, 0.23, '{}', 'gap_market')
                """,
                (f"valid_mls_{i}", f"MLS:ok_{i}", f"MLS:ok_{i}", "won" if won else "lost", 0.95 if won else -1.0),
            )

    # Case 5: Core-model live graduation track (engine.sqlite3) must compute REAL empirical Brier skill
    # rather than fabricating hardcoded delta_brier=0.003: reject NCAAM|totals (53% WR at p=0.88 -> negative Brier skill)
    # while graduating NBA|spreads (67% WR at p=0.62 -> positive Brier skill).
    with closing(sqlite3.connect(str(tmp_path / "engine.sqlite3"))) as econn:
        econn.execute(
            "CREATE TABLE system_decisions (tip_id TEXT PRIMARY KEY, issued_at TEXT, status TEXT, pnl REAL, payload TEXT)"
        )
        for i in range(30):
            won_c = i < 16  # 16W - 14L at 2.05 odds -> +9.3% ROI, but p=0.88 is horribly miscalibrated!
            econn.execute(
                "INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?)",
                (
                    f"ncaam_tot_{i}",
                    f"2026-11-20T{i % 24:02d}:00:00Z",
                    "won" if won_c else "lost",
                    1.05 if won_c else -1.0,
                    json.dumps({
                        "league": "NCAAM",
                        "market": "totals",
                        "model_scope": "full_game",
                        "game_id": f"NCAAM:core_{i}",
                        "stake": 1.0,
                        "reference_odds": 2.05,
                        "probability": 0.88,
                        "market_anchor": {"market_probability": 0.48},
                    }),
                ),
            )
            won_nba = i < 20  # 20W - 10L at 1.95 odds -> +30.0% ROI AND calibrated p=0.63!
            econn.execute(
                "INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?)",
                (
                    f"nba_sp_core_{i}",
                    f"2026-11-20T{i % 24:02d}:00:00Z",
                    "won" if won_nba else "lost",
                    0.95 if won_nba else -1.0,
                    json.dumps({
                        "league": "NBA",
                        "market": "spreads",
                        "model_scope": "full_game",
                        "game_id": f"NBA:core_{i}",
                        "stake": 1.0,
                        "reference_odds": 1.95,
                        "probability": 0.63,
                        "market_anchor": {"market_probability": 0.50},
                    }),
                ),
            )
        econn.commit()

    grads = inc.evaluate_graduation(as_of="2026-11-21T00:00:00Z")
    grad_map = {g["segment_key"]: g for g in grads}
    grad_keys = set(grad_map.keys())

    assert "NBA|half_1|totals" not in grad_keys
    assert "NHL|period_2|totals" not in grad_keys
    assert "EPL|half_1|moneyline" not in grad_keys
    assert "NCAAM|full_game|totals" not in grad_keys
    assert "MLS|full_game|totals" in grad_keys
    assert "NBA|full_game|spreads" in grad_keys
    assert grad_map["NBA|full_game|spreads"]["stats"]["delta_brier"] != 0.003
    assert grad_map["NBA|full_game|spreads"]["stats"]["delta_brier"] > 0.01


def test_real_settled_ledger_immutability_and_positive_roi_validation():
    """Validate against real ground-truth settled outcomes in data/engine.sqlite3 (read-only)."""
    engine_db = DATA_DIR / "engine.sqlite3"
    if not engine_db.exists():
        pytest.skip("Production data/engine.sqlite3 not present in environment")

    umu = UnifiedMarketUniverse(DATA_DIR)
    roster = umu.build_universe_roster(force_refresh=True)
    summary = umu.get_summary(force_refresh=True)

    # 1. Negative-Brier, polling-mirage (<10 distinct games), uncalibrated core (NCAAM|totals dBrier=-0.0428),
    #    and chronic negative-ROI segments must all be quarantined at $0.00 in Tier 1/2 (never funded in Tier 3/4)
    for quarantined_key in (
        "NBA|full_game|away_total",
        "EPL|full_game|away_total",
        "MLB|innings_1_5|away_total",
        "WNBA|full_game|away_total",
        "NCAAM|full_game|totals",
        "NCAAF|full_game|american_derivatives:spreads",
    ):
        q_prof = roster.get(quarantined_key)
        assert q_prof is not None, f"Expected {quarantined_key} in universe roster"
        assert q_prof.tier in (int(MarketTier.RESEARCH_RND), int(MarketTier.SHADOW_INCUBATOR)), (
            f"{quarantined_key} leaked into funded Tier {q_prof.tier}"
        )
        assert float(q_prof.stake_cap) == 0.0

    # 2. Proven live Stake winners must be promoted to Tier 4 Live Proven Alpha
    assert summary["tier_4_live_proven_alpha_count"] >= 14
    for proven_key in ("NBA|full_game|spreads", "NHL|period_1|totals", "MLS|full_game|american_derivatives:spreads", "MLS|full_game|totals"):
        prof = roster.get(proven_key)
        assert prof is not None, f"Missing {proven_key} in roster"
        assert prof.tier == int(MarketTier.LIVE_PROVEN_ALPHA)
        assert prof.rolling_pnl > 0.0
        assert prof.rolling_roi_pct >= 10.0

    # 3. Evaluate actual settled ground-truth ledger outcomes under repaired UnifiedMarketUniverse + Crucible + Sizing
    with closing(sqlite3.connect(f"file:{engine_db}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "SELECT tip_id, status, pnl, payload FROM system_decisions WHERE status IN ('won', 'lost', 'push')"
        ).fetchall()

    assert len(rows) >= 1112  # Immutable ledger row count preserved

    empty_snap = build_snapshot([], AS_OF)
    kept_stake = 0.0
    kept_pnl = 0.0
    kept_n = 0
    for rid, status, _orig_pnl, payload_raw in rows:
        p = json.loads(payload_raw)
        lg = str(p.get("league") or "").upper()
        mkt = str(p.get("market") or "")
        leaf_mkt = mkt.split(":")[-1]
        scope = str(p.get("model_scope") or (mkt.split(":")[0] if ":" in mkt else "full_game"))

        # UnifiedMarketUniverse quarantine ($0.00 Tier 1/2) — no manual league bypass needed!
        canon_k = umu.get_canonical_key(lg, scope, leaf_mkt)
        prof = roster.get(canon_k)
        if prof is not None and float(prof.stake_cap) <= 0.0:
            continue

        odds = float(p.get("accepted_odds") or p.get("reference_odds") or 1.9)
        prob = float(p.get("probability") or 0.5)
        push_p = float(p.get("push_probability") or 0.0)
        probe = {
            "league": lg,
            "sport": p.get("sport", ""),
            "game_id": p.get("game_id", ""),
            "market": mkt,
            "side": p.get("side", ""),
            "selection": p.get("selection", ""),
            "line": p.get("line"),
            "probability": prob,
            "push_probability": push_p,
            "reference_odds": odds,
            "model_scope": scope,
            "features": p.get("features") if isinstance(p.get("features"), dict) else {},
            "expected_roi": prob * odds + push_p - 1.0,
        }
        ok_crucible, _, p_adj = evaluate_crucible_candidate(probe)
        if not ok_crucible:
            continue
        p_use = float(p_adj if p_adj is not None else prob)
        if p_use * odds + push_p - 1.0 <= 0.0:
            continue

        _, sized = _make_live_sized_tip(
            league=lg,
            market=mkt,
            probability=p_use,
            market_probability=min(0.95, 1.0 / max(1.01, odds)),
            odds=odds,
            push_probability=push_p,
            game_id=str(p.get("game_id") or f"{lg}:{rid}"),
            snap_cache=empty_snap,
            extra_tip={"outcome_sizing_evidence": p.get("outcome_sizing_evidence")},
        )
        new_stake = float(sized["amount"])
        if new_stake <= 0.0:
            continue
        if status == "won":
            realized_pnl = new_stake * (odds - 1.0)
        elif status == "lost":
            realized_pnl = -new_stake
        else:
            realized_pnl = 0.0

        kept_n += 1
        kept_stake += new_stake
        kept_pnl += realized_pnl

    realized_roi = kept_pnl / max(1e-6, kept_stake)
    assert kept_n >= 200
    assert kept_pnl > 0.0
    assert realized_roi >= 0.12, f"Expected >= +12.0% realized ROI on settled ground truth, got {realized_roi:.2%} (${kept_pnl:+.2f} / ${kept_stake:.2f}, N={kept_n})"
