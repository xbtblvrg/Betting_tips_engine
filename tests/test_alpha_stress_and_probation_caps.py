"""Anti-Tautological Tests for Sizing & Slate Exposure Fixes (2026-11-28 Audit).

Validates against independent mathematical and behavioral invariants:
1. Anti-Loss Quarantine Invariant: Historical `is_graduated=True` diplomas NEVER
   override live demotion or Tier 1/2 (`stake_cap = 0.0`) quarantine ($0.00 stake).
2. Stress-Value Conviction Gate: Marginal nominal-EV `model_pick` candidates with
   `stress_roi < 0` NEVER scale into $2.50-$8.00 Solid/Super Alpha stakes, while
   preserving >= $0.50 funding for daily volume and scaling genuine `stress_value`
   (`stress_roi >= +1.0%`) candidates into $5.00-$8.00 Super Alpha.
3. Sealed Historical Replay Compatibility: Receipts sealed prior to
   `ALPHA_STRESS_AND_TIER_FIX_TS` (`2026-11-28T08:10:00+00:00`), including real
   ledger Bet #1394, reproduce their exact recorded `stake_sizing` byte-for-byte.
4. Concurrent Open Slate Exposure Caps: Capital Defense Tourniquet segments hard-cap
   at 5 simultaneous open positions ($2.50 slate risk, never bypassed by volume
   deficit), and unproven Tier 3 Probation Nursery segments (`N < 15`) cap at 8
   simultaneous open positions ($4.00 slate risk) once daily volume target is met.
"""
from __future__ import annotations

from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

import betting_engine.autonomous_roi_governor as gov_mod
import betting_engine.decisions as dec_mod
from betting_engine.decisions import (
    CONTINUOUS_PROBABILITY_SELECTION_V4,
    DEFAULT_SETTINGS,
    MAX_PROBATION_CONCURRENT_OPEN,
    MAX_TOURNIQUET_CONCURRENT_OPEN,
    rank_and_size,
)
from betting_engine.funding_policy import funding_basis, size_recommendation
from betting_engine.outcome_sizing import (
    ALPHA_STRESS_AND_TIER_FIX_TS,
    POLICY_V3,
    apply_adjustment,
    build_snapshot,
    summary_for,
    verify_summary,
)
import betting_engine.unified_market_universe as umu_mod


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
AS_OF = "2026-11-28T09:35:00+00:00"


def _make_settled_row(
    idx: int,
    *,
    status: str = "won",
    stake: float = 2.0,
    odds: float = 1.95,
    league: str = "NBA",
    market: str = "spreads",
    scope: str = "full_game",
) -> dict:
    tip_id = f"hist_{league.lower()}_{idx}"
    pnl = stake * (odds - 1.0) if status == "won" else (-stake if status == "lost" else 0.0)
    payload = {
        "id": tip_id,
        "game_id": f"{league}:hist_game_{idx}",
        "league": league,
        "market": market,
        "model_scope": scope,
        "start": "2026-11-20T18:00:00+00:00",
        "ownership": "engine",
        "stake": stake,
        "reference_odds": odds,
    }
    return {
        "family": f"contract:{tip_id}",
        "tip_id": tip_id,
        "issued_at": "2026-11-20T16:00:00+00:00",
        "payload": payload,
        "status": status,
        "pnl": pnl,
        "outcome_evidence": {
            "settled_at": "2026-11-20T22:00:00+00:00",
            "fetched_at": "2026-11-20T21:55:00+00:00",
            "source": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
            "home_score": 112,
            "away_score": 104,
        },
    }


def _build_super_alpha_summary(league: str = "NBA", market: str = "spreads", scope: str = "full_game") -> dict:
    """Builds a cryptographically valid 66-game Super Alpha summary (~+28% ROI, +$64+ PnL)."""
    # 44 wins @ 1.95 (+$1.90 each = +$83.60) and 22 losses @ $0.85 (-$18.70) => 66 games, +$64.90 PnL, >+28% ROI
    rows = [
        _make_settled_row(i, status="won", stake=2.0, odds=1.95, league=league, market=market, scope=scope)
        for i in range(44)
    ] + [
        _make_settled_row(44 + j, status="lost", stake=0.85, odds=1.95, league=league, market=market, scope=scope)
        for j in range(22)
    ]
    snap = build_snapshot(rows, AS_OF)
    probe = {"league": league, "market": market, "model_scope": scope}
    summary = summary_for(snap, probe)
    assert verify_summary(summary)
    assert summary["independent_games"] == 66
    assert summary["game_roi"] >= 0.28
    assert summary["pnl"] >= 64.0
    return summary


def test_graduated_flag_never_overrides_demotion_or_tier2_zero_quarantine(monkeypatch):
    """Fix 1: Real candidate with `is_graduated=True` in a demoted / Tier 2 (`stake_cap=0.0`)
    segment MUST get strictly $0.00 stake across both `rank_and_size` and `outcome_sizing`."""
    tier2_prof = SimpleNamespace(
        tier=2,
        stake_cap=0.0,
        rolling_pnl=-14.5,
        rolling_roi_pct=-18.2,
        sample_size=19,
        phase_detail="Tier 2 Shadow Incubator - Paper-trading candidate model ($0.00)",
    )
    monkeypatch.setattr(dec_mod, "_get_governor_status", lambda: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": [],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(gov_mod, "get_latest_governor_report", lambda data_dir=None: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": [],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(
        umu_mod.UnifiedMarketUniverse,
        "build_universe_roster",
        lambda self, force_refresh=False: {"NBA|half_1|spreads": tier2_prof},
    )
    monkeypatch.setattr(dec_mod, "_get_universe_roster", lambda: {"NBA|half_1|spreads": tier2_prof})

    real_cand = {
        "id": "9f8e7d6c5b4a32109876",
        "game_id": "NBA:401910999",
        "league": "NBA",
        "model_scope": "half_1",
        "market": "half_1:spreads",
        "side": "home",
        "line": -2.5,
        "selection": "Boston Celtics -2.5",
        "probability": 0.59,
        "stress_probability": 0.55,
        "push_probability": 0.0,
        "reference_odds": 1.98,
        "odds": 1.98,
        "minimum_odds": 1.75,
        "expected_roi": 0.59 * 1.98 - 1.0,
        "stress_roi": 0.55 * 1.98 - 1.0,
        "created_at": "2026-11-28T09:20:00+00:00",
        "is_graduated": True,  # Stale/historical graduation flag present!
        "is_demoted": False,
    }

    # 1A. V4 rank_and_size when _get_demoted_segments reflects Tier 2 demotion
    settings_v4 = {
        **DEFAULT_SETTINGS,
        "recommendation_policy": "continuous_model_v1",
        "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
        "model_pick_sizing_policy": "all_tiers_weighted_v3",
        "outcome_sizing_policy": POLICY_V3,
        "capital_defense": True,
        "alpha_acceleration": True,
        "is_test": False,
    }
    chosen_dem, alts_dem = rank_and_size([dict(real_cand)], settings_v4)
    assert len(chosen_dem) == 0, "Demoted Tier 2 market with is_graduated=True must NEVER enter funded chosen!"
    assert len(alts_dem) == 1
    assert alts_dem[0]["stake"] == 0.0
    assert alts_dem[0]["allocation_code"] in ("capital_defense_quarantined", "universe_quarantined")

    # 1B. Even if _get_demoted_segments were empty/stale, UnifiedMarketUniverse Tier 2 (stake_cap=0.0)
    # must NEVER be overwritten up to $0.50 by (c.get('is_graduated') or is_grad)!
    monkeypatch.setattr(dec_mod, "_get_demoted_segments", lambda: set())
    monkeypatch.setattr(dec_mod, "_get_graduated_segments", lambda: {"NBA|half_1|spreads"})
    chosen_u, alts_u = rank_and_size([dict(real_cand)], settings_v4)
    assert all(x["stake"] == 0.0 for x in chosen_u + alts_u)
    quarantined_item = (alts_u or chosen_u)[0]
    assert quarantined_item["stake"] == 0.0
    assert quarantined_item["allocation_code"] == "universe_quarantined"

    # 1C. outcome_sizing.apply_adjustment also enforces $0.00 and never pops universe_tier_stake_cap,
    # and rank_and_size never stamps `is_super_alpha=True` onto a Tier 2 market even if gov report is stale!
    alpha_summary = _build_super_alpha_summary(league="NBA", market="half_1:spreads", scope="half_1")
    cand_with_hist = {**real_cand, "outcome_sizing_evidence": alpha_summary}
    sized = apply_adjustment(
        {"amount": 2.82, "maximum_stake": 8.0, "event_discount": 1.0, "note": "base"},
        cand_with_hist,
        settings_v4,
        8.0,
    )
    assert sized["amount"] == 0.0
    caps = sized["outcome_adjustment"]["caps"]
    assert caps.get("universe_tier_stake_cap") == 0.0
    assert caps.get("demoted_market_defense_clamp") == 0.0
    assert caps.get("capital_defense_quarantine") == 0.0

    monkeypatch.setattr(dec_mod, "_get_governor_status", lambda: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": ["NBA|half_1|spreads"],  # Stale governor report!
        "solid_alpha_list": [],
    })
    chosen_stale_gov, alts_stale_gov = rank_and_size([dict(real_cand)], settings_v4)
    assert all(x["stake"] == 0.0 for x in chosen_stale_gov + alts_stale_gov)
    assert not any(x.get("is_super_alpha") for x in chosen_stale_gov + alts_stale_gov)


def test_stress_conviction_gate_blocks_unstressed_model_picks_from_alpha_scaling(monkeypatch):
    """Fix 2: In a proven 66-game Super Alpha segment (+28% ROI, +$64+ PnL), a marginal
    `model_pick` failing stressed value (`stress_roi = -0.0011 < 0`) is capped <= $1.25
    (never $5-$8), while a genuine `stress_value` (`stress_roi = +0.045`) scales to $5-$8."""
    tier4_prof = SimpleNamespace(
        tier=4,
        stake_cap=8.0,
        rolling_pnl=42.0,
        rolling_roi_pct=24.5,
        sample_size=66,
        phase_detail="Tier 4 Live Proven Alpha ($8.00 cap)",
    )
    monkeypatch.setattr(gov_mod, "get_latest_governor_report", lambda data_dir=None: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": ["NBA|full_game|spreads"],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(
        umu_mod.UnifiedMarketUniverse,
        "build_universe_roster",
        lambda self, force_refresh=False: {"NBA|full_game|spreads": tier4_prof},
    )
    monkeypatch.setattr(dec_mod, "_get_demoted_segments", lambda: set())
    monkeypatch.setattr(dec_mod, "_get_graduated_segments", lambda: {"NBA|full_game|spreads"})

    summary_66 = _build_super_alpha_summary(league="NBA", market="spreads", scope="full_game")
    settings = {
        **DEFAULT_SETTINGS,
        "minimum_roi": 0.02,
        "max_bet": 8.0,
        "unit_stake": 1.0,
        "recommendation_policy": "continuous_model_v1",
        "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
        "model_pick_sizing_policy": "all_tiers_weighted_v3",
        "model_pick_allocation_policy": "distinct_family_v1",
        "outcome_sizing_policy": POLICY_V3,
        "capital_defense": True,
        "alpha_acceleration": True,
        "is_test": False,
    }

    # Candidate A: Marginal unstressed model_pick (stress_roi = -0.0011 < 0, nominal EV = +7.89% > 0)
    odds_a = 2.00
    prob_a = 0.53945  # model_roi = 0.53945 * 2.0 - 1 = +0.0789
    stress_prob_a = 0.49945  # stress_roi = 0.49945 * 2.0 - 1 = -0.0011
    tip_unstressed = {
        "id": "1a2b3c4d5e6f7890abcd",
        "game_id": "NBA:401910501",
        "league": "NBA",
        "model_scope": "full_game",
        "market": "spreads",
        "side": "away",
        "line": 1.5,
        "selection": "Indiana Pacers +1.5",
        "probability": prob_a,
        "stress_probability": stress_prob_a,
        "push_probability": 0.0,
        "reference_odds": odds_a,
        "odds": odds_a,
        "expected_roi": prob_a * odds_a - 1.0,
        "stress_roi": stress_prob_a * odds_a - 1.0,
        "created_at": "2026-11-28T09:25:00+00:00",
        "is_graduated": True,
        "is_super_alpha": True,  # Even when stamped by priority ranking!
        "outcome_sizing_evidence": summary_66,
        "market_anchor": {
            "status": "usable",
            "model_probability": prob_a,
            "market_probability": 0.50,
            "disagreement_threshold": 0.30,
        },
        "input_readiness": {
            "passed": True,
            "state": "ready",
            "checks": {"model_features": {"required": 10, "missing": [], "invalid": []}},
        },
    }
    tip_unstressed["funding_basis"] = funding_basis(tip_unstressed, settings)
    assert tip_unstressed["funding_basis"]["tier"] == "model_pick"
    assert math.isclose(tip_unstressed["funding_basis"]["stress_roi"], -0.0011, abs_tol=1e-6)

    res_unstressed = size_recommendation(tip_unstressed, settings, 8.0, 0)
    # Invariant: Not dropped (>= $0.50), but strictly blocked from $2.50-$8.00 Alpha scaling (<= $1.25)
    assert 0.50 <= res_unstressed["amount"] <= 1.25, (
        f"Unstressed model_pick (stress_roi=-0.0011) leaked Alpha stake: ${res_unstressed['amount']:.2f}"
    )

    # Candidate B: Exact same 66-game segment, positive stressed conviction (stress_roi = +0.045, tier = stress_value)
    odds_b = 2.00
    prob_b = 0.5625
    stress_prob_b = 0.5225  # stress_roi = 0.5225 * 2.0 - 1 = +0.045
    tip_stressed = {
        **tip_unstressed,
        "id": "2b3c4d5e6f7890abcdef",
        "probability": prob_b,
        "stress_probability": stress_prob_b,
        "expected_roi": prob_b * odds_b - 1.0,
        "stress_roi": stress_prob_b * odds_b - 1.0,
        "market_anchor": {
            "status": "usable",
            "model_probability": prob_b,
            "market_probability": 0.50,
            "disagreement_threshold": 0.30,
        },
    }
    tip_stressed["funding_basis"] = funding_basis(tip_stressed, settings)
    assert tip_stressed["funding_basis"]["tier"] == "stress_value"
    assert math.isclose(tip_stressed["funding_basis"]["stress_roi"], 0.045, abs_tol=1e-6)

    res_stressed = size_recommendation(tip_stressed, settings, 8.0, 0)
    assert 5.00 <= res_stressed["amount"] <= 8.00, (
        f"Genuine stress_value (+4.5% stress ROI) in Super Alpha segment must scale to $5-$8, got ${res_stressed['amount']:.2f}"
    )
    assert res_stressed["amount"] > res_unstressed["amount"] * 4.0

    # Candidate C: Mild stressed value (0 <= stress_roi = +0.005 < +0.01) steps down to Solid Alpha ($2.50-$4.00)
    low_hurdle_settings = {**settings, "minimum_roi": 0.002}
    prob_c = 0.5425
    stress_prob_c = 0.5025  # stress_roi = +0.005 (< +0.01 Super Alpha threshold, >= 0.0 Solid Alpha threshold)
    tip_mild_stress = {
        **tip_unstressed,
        "id": "3c4d5e6f7890abcdef12",
        "probability": prob_c,
        "stress_probability": stress_prob_c,
        "expected_roi": prob_c * odds_b - 1.0,
        "stress_roi": stress_prob_c * odds_b - 1.0,
    }
    tip_mild_stress["funding_basis"] = funding_basis(tip_mild_stress, low_hurdle_settings)
    assert tip_mild_stress["funding_basis"]["tier"] == "stress_value"
    res_mild = size_recommendation(tip_mild_stress, low_hurdle_settings, 8.0, 0)
    assert 2.50 <= res_mild["amount"] <= 4.00
    assert res_unstressed["amount"] < res_mild["amount"] < res_stressed["amount"]


def test_sealed_historical_receipts_before_cutoff_reproduce_exact_stake(monkeypatch):
    """Sealed candidates created before `ALPHA_STRESS_AND_TIER_FIX_TS` reproduce $8.00 without breaking replay."""
    tier4_prof = SimpleNamespace(
        tier=4,
        stake_cap=8.0,
        rolling_pnl=42.0,
        rolling_roi_pct=24.5,
        sample_size=66,
        phase_detail="Tier 4 Live Proven Alpha ($8.00 cap)",
    )
    monkeypatch.setattr(gov_mod, "get_latest_governor_report", lambda data_dir=None: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": ["NBA|full_game|spreads"],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(
        umu_mod.UnifiedMarketUniverse,
        "build_universe_roster",
        lambda self, force_refresh=False: {"NBA|full_game|spreads": tier4_prof},
    )
    monkeypatch.setattr(dec_mod, "_get_demoted_segments", lambda: set())
    monkeypatch.setattr(dec_mod, "_get_graduated_segments", lambda: {"NBA|full_game|spreads"})

    summary_66 = _build_super_alpha_summary(league="NBA", market="spreads", scope="full_game")
    settings = {
        **DEFAULT_SETTINGS,
        "minimum_roi": 0.03,
        "max_bet": 8.0,
        "unit_stake": 1.0,
        "recommendation_policy": "continuous_model_v1",
        "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
        "model_pick_sizing_policy": "all_tiers_weighted_v3",
        "model_pick_allocation_policy": "distinct_family_v1",
        "outcome_sizing_policy": POLICY_V3,
        "capital_defense": True,
        "alpha_acceleration": True,
    }

    sealed_tip = {
        "id": "8b75d90f94151a8055d6",
        "game_id": "NBA:401910042",
        "league": "NBA",
        "model_scope": "full_game",
        "market": "spreads",
        "side": "away",
        "line": 1.5,
        "selection": "Indiana Pacers +1.5",
        "probability": 0.56,
        "stress_probability": 0.4381255,
        "push_probability": 0.0,
        "reference_odds": 2.06,
        "odds": 2.06,
        "expected_roi": 0.56 * 2.06 - 1.0,
        "stress_roi": 0.4381255 * 2.06 - 1.0,  # negative stress_roi (-0.0975)
        "created_at": "2026-11-27T12:30:29+00:00",  # < ALPHA_STRESS_AND_TIER_FIX_TS
        "decision_guard": {"passed": True, "candidate_sha256": "a" * 64},
        "is_graduated": True,
        "is_super_alpha": True,
        "outcome_sizing_evidence": summary_66,
        "market_anchor": {
            "status": "usable",
            "model_probability": 0.56,
            "market_probability": 0.49,
            "disagreement_threshold": 0.30,
        },
        "input_readiness": {
            "passed": True,
            "state": "ready",
            "checks": {"model_features": {"required": 10, "missing": [], "invalid": []}},
        },
    }
    assert sealed_tip["created_at"] < ALPHA_STRESS_AND_TIER_FIX_TS
    sealed_tip["funding_basis"] = funding_basis(sealed_tip, settings)
    assert sealed_tip["funding_basis"]["tier"] == "model_pick"

    replayed = size_recommendation(sealed_tip, settings, 8.0, 0)
    assert replayed["amount"] == 8.0

    # Also verify replay against real production Bet #1394 if data/engine.sqlite3 is present
    db_path = DATA_DIR / "engine.sqlite3"
    if db_path.exists():
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT stake, payload FROM bets WHERE id = 1394").fetchone()
        if row is not None:
            import betting_engine.execution_meta_learner as eml_mod
            monkeypatch.setattr(
                eml_mod,
                "get_execution_meta_learner",
                lambda: SimpleNamespace(evaluate_candidate_regime=lambda _t: {"meta_kelly_multiplier": 1.18, "meta_ev_hurdle": 0.02}),
            )
            recorded_stake, raw_payload = float(row[0]), json.loads(row[1])
            guard = raw_payload["decision_guard"]
            sizing = raw_payload["stake_sizing"]
            replay_settings = {
                **DEFAULT_SETTINGS,
                "bankroll": sizing["bankroll"],
                "unit_stake": sizing["base_unit"],
                "max_bet": max(sizing["maximum_stake"], guard["sizing_settings"]["max_bet"]),
                "kelly_fraction": sizing["kelly_fraction"],
                "max_bankroll_fraction": sizing["max_bankroll_fraction"],
                "minimum_roi": guard["minimum_roi"],
                "recommendation_policy": guard["recommendation_policy"],
                "model_pick_sizing_policy": guard["model_pick_sizing_policy"],
                "model_pick_allocation_policy": guard["model_pick_allocation_policy"],
                "family_selection_policy": guard["family_selection_policy"],
                "outcome_sizing_policy": guard["sizing_settings"]["outcome_sizing_policy"],
                "capital_defense": guard["sizing_settings"]["capital_defense"],
                "alpha_acceleration": guard["sizing_settings"]["alpha_acceleration"],
            }
            rem_cap = max(sizing["maximum_stake"], sizing["outcome_adjustment"]["caps"]["remaining"])
            actual_replayed = size_recommendation(raw_payload, replay_settings, rem_cap, sizing["related_contracts"])
            assert actual_replayed == sizing
            assert actual_replayed["amount"] == recorded_stake == 8.0

            # Counterfactual invariant on the EXACT SAME real Bet #1394 payload post-fix:
            # Because stress_roi = -0.0975 < 0 and tier = 'model_pick', a fresh post-cutoff
            # candidate with identical numbers is blocked from $8.00 Alpha and capped <= $1.25!
            fresh_1394 = {
                k: v for k, v in raw_payload.items() if k != "stake_sizing"
            }
            fresh_1394["created_at"] = "2026-11-28T09:30:00+00:00"
            post_fix_sized = size_recommendation(fresh_1394, replay_settings, rem_cap, sizing["related_contracts"])
            assert 0.50 <= post_fix_sized["amount"] <= 1.25


def test_tourniquet_and_probation_concurrent_open_slate_exposure_caps(monkeypatch):
    """Fix 3: 20-game slate in Capital Defense Tourniquet stops at 5 open picks ($2.50 risk,
    even under volume_deficit), and unproven Probation Nursery stops at 8 open picks ($4.00 risk)."""
    tourniquet_prof = SimpleNamespace(
        tier=3,
        stake_cap=0.50,
        rolling_pnl=-4.80,
        rolling_roi_pct=-6.2,
        sample_size=20,
        phase_detail="Capital Defense Tourniquet (-6.2% ROI over 20 games) - $0.50 clamp",
    )
    probation_prof = SimpleNamespace(
        tier=3,
        stake_cap=0.50,
        rolling_pnl=0.95,
        rolling_roi_pct=4.1,
        sample_size=6,  # < 15 sample maturity threshold
        phase_detail="Tier 3 Live Probation Nursery ($0.50 floor)",
    )
    roster_mock = {
        "NCAAM|full_game|spreads": tourniquet_prof,
        "EPL|half_2|spreads": probation_prof,
        "EPL|half_2|half_2:spreads": probation_prof,
    }
    monkeypatch.setattr(dec_mod, "_get_universe_roster", lambda: roster_mock)
    monkeypatch.setattr(
        umu_mod.UnifiedMarketUniverse,
        "build_universe_roster",
        lambda self, force_refresh=False: roster_mock,
    )
    monkeypatch.setattr(dec_mod, "_get_demoted_segments", lambda: set())
    monkeypatch.setattr(
        dec_mod,
        "_get_graduated_segments",
        lambda: {"NCAAM|full_game|spreads", "EPL|half_2|spreads", "EPL|half_2|half_2:spreads"},
    )
    monkeypatch.setattr(dec_mod, "_get_governor_status", lambda: {
        "quarantine_list": [],
        "capital_defense_list": ["NCAAM|full_game|spreads"],
        "super_alpha_list": [],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(gov_mod, "get_latest_governor_report", lambda data_dir=None: {
        "quarantine_list": [],
        "capital_defense_list": ["NCAAM|full_game|spreads"],
        "super_alpha_list": [],
        "solid_alpha_list": [],
    })

    settings = {
        **DEFAULT_SETTINGS,
        "recommendation_policy": "continuous_model_v1",
        "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
        "model_pick_sizing_policy": "all_tiers_weighted_v3",
        "model_pick_allocation_policy": "distinct_family_v1",
        "publication_limits_version": None,
        "outcome_sizing_policy": POLICY_V3,
        "capital_defense": True,
        "alpha_acceleration": True,
        "unit_stake": 0.50,
        "max_bet": 8.0,
        "is_test": False,
    }
    empty_snap = build_snapshot([], AS_OF)

    def _make_slate(league: str, scope: str, market: str, count: int) -> list[dict]:
        evid = summary_for(empty_snap, {"league": league, "market": market, "model_scope": scope})
        return [
            {
                "id": f"real_{league.lower()}_{idx:03d}abcdef1234",
                "game_id": f"{league}:40199{idx:04d}",
                "league": league,
                "model_scope": scope,
                "market": market,
                "side": "home",
                "line": -3.5,
                "selection": f"Team {idx} -3.5",
                "probability": 0.58,
                "stress_probability": 0.55,
                "push_probability": 0.0,
                "reference_odds": 1.95,
                "odds": 1.95,
                "minimum_odds": 1.78,
                "expected_roi": 0.58 * 1.95 - 1.0,  # +13.1% EV (> 3.5% Capital Defense hurdle)
                "stress_roi": 0.55 * 1.95 - 1.0,
                "reference_minimum_notional": 0.50,
                "created_at": "2026-11-28T09:15:00+00:00",
                "outcome_sizing_evidence": evid,
                "is_graduated": True,
            }
            for idx in range(count)
        ]

    # Part A: 20-game slate in Capital Defense Tourniquet segment (even with daily_funded_count=0 volume deficit!)
    tourniquet_slate = _make_slate("NCAAM", "full_game", "spreads", 20)
    chosen_t, alts_t = rank_and_size(tourniquet_slate, settings, daily_funded_count=0)
    funded_t = [c for c in chosen_t if c["stake"] > 0]
    assert len(funded_t) == MAX_TOURNIQUET_CONCURRENT_OPEN == 5
    assert math.isclose(sum(c["stake"] for c in funded_t), 2.50, abs_tol=1e-6)
    capped_t = [a for a in alts_t if a.get("allocation_code") == "tourniquet_slate_exposure_limit"]
    assert len(capped_t) == 15
    assert all(a["stake"] == 0.0 and a["alternative"] is True for a in capped_t)

    # Also verify pre-existing held open positions count against MAX_TOURNIQUET_CONCURRENT_OPEN
    held_3 = [
        {"id": f"held_ncaam_{i}", "game_id": f"NCAAM:40188{i:04d}", "league": "NCAAM", "model_scope": "full_game", "market": "spreads", "stake": 0.50}
        for i in range(3)
    ]
    chosen_t2, alts_t2 = rank_and_size(
        _make_slate("NCAAM", "full_game", "spreads", 20),
        settings,
        daily_funded_count=0,
        held_event_roster=held_3,
    )
    funded_t2 = [c for c in chosen_t2 if c["stake"] > 0]
    assert len(funded_t2) == 2  # 3 held + 2 new == 5 max concurrent open
    assert sum(1 for a in alts_t2 if a.get("allocation_code") == "tourniquet_slate_exposure_limit") == 18

    # Part B: 20-game slate in unproven Tier 3 Probation Nursery segment stops at 8 open picks
    probation_slate = _make_slate("EPL", "half_2", "half_2:spreads", 20)
    chosen_p, alts_p = rank_and_size(probation_slate, settings, daily_funded_count=105)
    funded_p = [c for c in chosen_p if c["stake"] > 0]
    assert len(funded_p) == MAX_PROBATION_CONCURRENT_OPEN == 8
    assert math.isclose(sum(c["stake"] for c in funded_p), 4.00, abs_tol=1e-6)
    capped_p = [a for a in alts_p if a.get("allocation_code") == "probation_slate_exposure_limit"]
    assert len(capped_p) == 12
    assert all(a["stake"] == 0.0 and a["alternative"] is True for a in capped_p)

    # Part C: Edge cases — empty slate, volume-deficit probation expansion, and mature sample_size >= 15
    assert rank_and_size([], settings, daily_funded_count=105) == ([], [])

    chosen_vd, alts_vd = rank_and_size(_make_slate("EPL", "half_2", "half_2:spreads", 12), settings, daily_funded_count=10)
    assert sum(1 for c in chosen_vd if c["stake"] > 0) == 12
    assert not any(a.get("allocation_code") == "probation_slate_exposure_limit" for a in alts_vd)

    mature_prof = SimpleNamespace(
        tier=3,
        stake_cap=0.50,
        rolling_pnl=2.50,
        rolling_roi_pct=5.0,
        sample_size=20,  # >= 15 mature sample!
        phase_detail="Tier 3 Live Graduated ($0.50 cap)",
    )
    roster_mock["EPL|half_2|spreads"] = mature_prof
    roster_mock["EPL|half_2|half_2:spreads"] = mature_prof
    chosen_m, alts_m = rank_and_size(_make_slate("EPL", "half_2", "half_2:spreads", 12), settings, daily_funded_count=105)
    assert sum(1 for c in chosen_m if c["stake"] > 0) == 12
    assert not any(a.get("allocation_code") == "probation_slate_exposure_limit" for a in alts_m)


def test_regraduated_cross_taxonomy_aliases_never_falsely_quarantined(monkeypatch):
    """Cross-taxonomy Re-Graduation Invariant (Commandments 2, 6, & 7):
    When a market re-graduates through the Crucible under an international/container sub-key
    (e.g., `NFL|half_1|asian_lines:asian_handicap` Tier 3 $0.50) while an older pre-regraduation
    alias (`NFL|half_1|spreads` Tier 1 $0.00) still exists in `roster`, `half_1:spreads` MUST
    resolve as graduated Tier 3 ($0.50), NEVER falsely quarantined to $0.00 — while an unrelated
    genuinely demoted market (`NFL|half_1|totals` Tier 1 $0.00) remains strictly quarantined at $0.00."""
    regrad_spread_prof = SimpleNamespace(
        tier=3,
        stake_cap=0.50,
        rolling_pnl=0.0,
        rolling_roi_pct=8.5,
        sample_size=1,
        phase_detail="Newly Graduated Crucible Model - Live Probation ($0.50 floor)",
    )
    stale_spread_alias_prof = SimpleNamespace(
        tier=1,
        stake_cap=0.0,
        rolling_pnl=-2.56,
        rolling_roi_pct=-25.7,
        sample_size=20,
        phase_detail="Live Drawdown Demotion (-25.7% ROI) - Quarantined ($0.00)",
    )
    demoted_totals_prof = SimpleNamespace(
        tier=1,
        stake_cap=0.0,
        rolling_pnl=-6.40,
        rolling_roi_pct=-32.0,
        sample_size=18,
        phase_detail="Live Drawdown Demotion (-32.0% ROI) - Quarantined ($0.00)",
    )
    roster_mock = {
        "NFL|half_1|asian_lines:asian_handicap": regrad_spread_prof,
        "NFL|half_1|spreads": stale_spread_alias_prof,
        "NFL|half_1|totals": demoted_totals_prof,
        "NFL|half_1|asian_lines:asian_total": demoted_totals_prof,
    }
    monkeypatch.setattr(dec_mod, "_get_universe_roster", lambda: roster_mock)
    monkeypatch.setattr(
        umu_mod.UnifiedMarketUniverse,
        "build_universe_roster",
        lambda self, force_refresh=False: roster_mock,
    )
    monkeypatch.setattr(dec_mod, "_get_governor_status", lambda: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": [],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(gov_mod, "get_latest_governor_report", lambda data_dir=None: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": [],
        "solid_alpha_list": [],
    })

    empty_snap = build_snapshot([], AS_OF)
    evid_spread = summary_for(empty_snap, {"league": "NFL", "market": "half_1:spreads", "model_scope": "half_1"})
    evid_total = summary_for(empty_snap, {"league": "NFL", "market": "half_1:totals", "model_scope": "half_1"})

    settings = {
        **DEFAULT_SETTINGS,
        "recommendation_policy": "continuous_model_v1",
        "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
        "model_pick_sizing_policy": "all_tiers_weighted_v3",
        "outcome_sizing_policy": POLICY_V3,
        "capital_defense": True,
        "alpha_acceleration": True,
        "unit_stake": 1.0,
        "max_bet": 8.0,
        "is_test": False,
    }

    regrad_cand = {
        "id": "aa11bb22cc33dd44ee55",
        "game_id": "NFL:401995001",
        "league": "NFL",
        "model_scope": "half_1",
        "market": "half_1:spreads",
        "side": "home",
        "line": -3.5,
        "selection": "Kansas City Chiefs -3.5",
        "probability": 0.58,
        "stress_probability": 0.54,
        "push_probability": 0.0,
        "reference_odds": 1.95,
        "odds": 1.95,
        "minimum_odds": 1.80,
        "expected_roi": 0.58 * 1.95 - 1.0,
        "stress_roi": 0.54 * 1.95 - 1.0,
        "reference_minimum_notional": 0.50,
        "created_at": "2026-11-28T09:25:00+00:00",
        "outcome_sizing_evidence": evid_spread,
        "is_graduated": False,
    }
    demoted_cand = {
        **regrad_cand,
        "id": "ff99ee88dd77cc66bb55",
        "market": "half_1:totals",
        "side": "over",
        "line": 23.5,
        "selection": "Over 23.5",
        "outcome_sizing_evidence": evid_total,
        "is_graduated": True,  # Even with stale graduation flag!
    }

    chosen, alts = rank_and_size([regrad_cand, demoted_cand], settings)
    funded = [c for c in chosen if c["stake"] > 0]
    assert len(funded) == 1
    assert funded[0]["id"] == "aa11bb22cc33dd44ee55"
    assert funded[0]["stake"] == 0.50
    assert funded[0]["allocation_code"] == "funded"

    quarantined = [a for a in alts if a["id"] == "ff99ee88dd77cc66bb55"]
    assert len(quarantined) == 1
    assert quarantined[0]["stake"] == 0.0
    assert quarantined[0]["allocation_code"] in ("capital_defense_quarantined", "universe_quarantined")


def test_stress_probability_precedence_and_cross_alias_slate_caps(monkeypatch):
    """Verifies:
    1. `apply_adjustment` computes `cand_stress_roi` from `tip['stress_probability']` + `reference_odds`
       even when `tip['stress_roi']` is omitted and `base_sizing['stress_roi']` holds positive nominal EV.
    2. Cross-taxonomy held positions (`half_1:asian_handicap` + `half_1:spreads`) and `held_families`
       share the exact same canonical `(league, scope, 'spreads')` slate exposure counter.
    3. Live pre-restart open bets in `data/engine.sqlite3` (including Bet #1501) suffer zero newly
       introduced `_original_budget` verification regressions."""
    tier4_prof = SimpleNamespace(
        tier=4,
        stake_cap=8.0,
        rolling_pnl=30.0,
        rolling_roi_pct=22.0,
        sample_size=50,
        phase_detail="Tier 4 Live Proven Alpha ($8.00 cap)",
    )
    tourn_prof = SimpleNamespace(
        tier=3,
        stake_cap=0.50,
        rolling_pnl=-3.20,
        rolling_roi_pct=-9.5,
        sample_size=12,
        phase_detail="Capital Defense Tourniquet (-9.5% ROI) - $0.50 clamp",
    )
    roster_mock = {
        "NBA|full_game|spreads": tier4_prof,
        "NFL|half_1|spreads": tourn_prof,
        "NFL|half_1|asian_lines:asian_handicap": tourn_prof,
    }
    monkeypatch.setattr(dec_mod, "_get_universe_roster", lambda: roster_mock)
    monkeypatch.setattr(
        umu_mod.UnifiedMarketUniverse,
        "build_universe_roster",
        lambda self, force_refresh=False: roster_mock,
    )
    monkeypatch.setattr(dec_mod, "_get_governor_status", lambda: {
        "quarantine_list": [],
        "capital_defense_list": ["NFL|half_1|spreads"],
        "super_alpha_list": ["NBA|full_game|spreads"],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(gov_mod, "get_latest_governor_report", lambda data_dir=None: {
        "quarantine_list": [],
        "capital_defense_list": ["NFL|half_1|spreads"],
        "super_alpha_list": ["NBA|full_game|spreads"],
        "solid_alpha_list": [],
    })

    # 1. Negative stress_probability must NOT be masked by nominal base_sizing['stress_roi']
    summary_66 = _build_super_alpha_summary(league="NBA", market="spreads", scope="full_game")
    tip_no_sroi_field = {
        "id": "ab12cd34ef5678901234",
        "game_id": "NBA:401996001",
        "league": "NBA",
        "model_scope": "full_game",
        "market": "spreads",
        "probability": 0.55,
        "stress_probability": 0.48,  # 0.48 * 2.00 - 1.0 = -0.04 < 0!
        "push_probability": 0.0,
        "reference_odds": 2.00,
        "expected_roi": 0.10,
        "created_at": "2026-11-28T09:30:00+00:00",
        "outcome_sizing_evidence": summary_66,
    }
    nominal_base = {
        "amount": 1.50,
        "maximum_stake": 8.0,
        "event_discount": 1.0,
        "stress_roi": 0.10,  # Nominal EV from raw stake_assessment(nominal_prob)
        "probability_basis": "model",
        "note": "base",
    }
    res_adj = apply_adjustment(
        nominal_base,
        tip_no_sroi_field,
        {"outcome_sizing_policy": POLICY_V3, "capital_defense": True, "alpha_acceleration": True, "unit_stake": 1.0, "max_bet": 8.0, "is_test": False},
        8.0,
    )
    assert 0.50 <= res_adj["amount"] <= 1.25

    # 2. Cross-taxonomy held_event_roster (`half_1:asian_handicap`) + `half_1:spreads` slate cap
    empty_snap = build_snapshot([], AS_OF)
    evid_nfl = summary_for(empty_snap, {"league": "NFL", "market": "half_1:spreads", "model_scope": "half_1"})
    settings_slate = {
        **DEFAULT_SETTINGS,
        "recommendation_policy": "continuous_model_v1",
        "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
        "model_pick_sizing_policy": "all_tiers_weighted_v3",
        "model_pick_allocation_policy": "distinct_family_v1",
        "publication_limits_version": None,
        "outcome_sizing_policy": POLICY_V3,
        "capital_defense": True,
        "alpha_acceleration": True,
        "unit_stake": 0.50,
        "max_bet": 8.0,
        "is_test": False,
    }
    held_cross_alias = [
        {"id": f"held_ah_{i}", "game_id": f"NFL:40177{i:04d}", "league": "NFL", "model_scope": "half_1", "market": "asian_handicap", "stake": 0.50}
        for i in range(2)
    ] + [
        {"id": f"held_sp_{i}", "game_id": f"NFL:40178{i:04d}", "league": "NFL", "model_scope": "full_game", "market": "half_1:spreads", "stake": 0.50}
        for i in range(2)
    ]
    nfl_cands = [
        {
            "id": f"real_nfl_h1_{i:02d}abcdef12",
            "game_id": f"NFL:40199{i:04d}",
            "league": "NFL",
            "model_scope": "half_1",
            "market": "half_1:spreads",
            "side": "home",
            "line": -2.5,
            "selection": f"Team {i} -2.5",
            "probability": 0.58,
            "stress_probability": 0.55,
            "push_probability": 0.0,
            "reference_odds": 1.95,
            "odds": 1.95,
            "minimum_odds": 1.78,
            "expected_roi": 0.58 * 1.95 - 1.0,
            "stress_roi": 0.55 * 1.95 - 1.0,
            "reference_minimum_notional": 0.50,
            "created_at": "2026-11-28T09:30:00+00:00",
            "outcome_sizing_evidence": evid_nfl,
            "is_graduated": True,
        }
        for i in range(6)
    ]
    chosen_ca, alts_ca = rank_and_size(nfl_cands, settings_slate, daily_funded_count=0, held_event_roster=held_cross_alias)
    assert sum(1 for c in chosen_ca if c["stake"] > 0) == 1  # 4 held asian_handicap + 1 new spreads == 5 max!
    assert sum(1 for a in alts_ca if a.get("allocation_code") == "tourniquet_slate_exposure_limit") == 5

    # Also verify held_families fallback when held_event_roster is omitted
    held_fams = [(f"NFL:40166{i:04d}", "half_1:spreads") for i in range(3)]
    nfl_cands_fam = [dict(x, stake=0.0) for x in nfl_cands]
    chosen_hf, alts_hf = rank_and_size(nfl_cands_fam, settings_slate, daily_funded_count=0, held_families=held_fams)
    assert sum(1 for c in chosen_hf if c["stake"] > 0) == 2  # 3 held_families + 2 new == 5 max!
    assert sum(1 for a in alts_hf if a.get("allocation_code") == "tourniquet_slate_exposure_limit") == 4


def test_live_bet_1501_sealed_replay_parity():
    """Real ledger Bet #1501 (`2026-11-28T08:30:55+00:00`, `stress_roi = -0.0957`, `model_pick`)
    must pass `_original_budget` without any `marginal_unstressed_model_pick_cap` replay mismatch."""
    from betting_engine.domain import parse_time
    from betting_engine.instructions import _original_budget

    db_path = DATA_DIR / "engine.sqlite3"
    if not db_path.exists():
        pytest.skip("live engine.sqlite3 not present")
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT created_at, payload FROM bets WHERE id = 1501").fetchone()
    if row is None:
        pytest.skip("Bet #1501 not present")
    created_at, payload = row[0], json.loads(row[1])
    fmt = _original_budget(payload, parse_time(created_at))
    assert fmt == "current_seal"


def test_post_cutoff_verify_publication_and_instruction_review_under_dynamic_event_limits(monkeypatch):
    """Attacks the prior report's untested edge case:
    Verifies end-to-end `rank_and_size` (with `dynamic_event_limits` second-pass batch resizing
    and `seal_batch`) -> `verify_publication` -> `instruction_review` replay parity on newly
    issued post-cutoff `model_pick` (`stress_roi < 0`) and mild `stress_value` (`0 <= stress_roi < 0.01`)
    contracts."""
    from betting_engine.decision_readiness import MISSING_STARTER_LEGACY, READINESS_VERSION
    from betting_engine.decisions import minimum_odds, publication_limit_receipt, sportsbook_ev
    from betting_engine.domain import parse_time
    from betting_engine.dynamic_event_limits import VERSION_V2 as DYN_V2
    from betting_engine.governance import evidence_digest, verify_publication
    from betting_engine.instructions import instruction_review
    import betting_engine.professional_quality as pq_mod
    import betting_engine.stake_website_availability as swa_mod

    tier4_prof = SimpleNamespace(
        tier=4,
        stake_cap=8.0,
        rolling_pnl=48.0,
        rolling_roi_pct=26.0,
        sample_size=66,
        phase_detail="Tier 4 Live Proven Alpha ($8.00 cap)",
    )
    roster_mock = {
        "NBA|full_game|spreads": tier4_prof,
        "NBA|full_game|moneyline": tier4_prof,
    }
    monkeypatch.setattr(dec_mod, "_get_universe_roster", lambda: roster_mock)
    monkeypatch.setattr(
        umu_mod.UnifiedMarketUniverse,
        "build_universe_roster",
        lambda self, force_refresh=False: roster_mock,
    )
    monkeypatch.setattr(dec_mod, "_get_governor_status", lambda: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": ["NBA|full_game|spreads", "NBA|full_game|moneyline"],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(gov_mod, "get_latest_governor_report", lambda data_dir=None: {
        "quarantine_list": [],
        "capital_defense_list": [],
        "super_alpha_list": ["NBA|full_game|spreads", "NBA|full_game|moneyline"],
        "solid_alpha_list": [],
    })
    monkeypatch.setattr(swa_mod, "website_availability_issue", lambda _t: None)
    monkeypatch.setattr(pq_mod, "verify_quality_receipt", lambda *_a, **_kw: {"passed": True})
    import betting_engine.execution_meta_learner as eml_mod
    monkeypatch.setattr(
        eml_mod,
        "get_execution_meta_learner",
        lambda: SimpleNamespace(evaluate_candidate_regime=lambda _t: {"meta_kelly_multiplier": 1.0, "meta_ev_hurdle": 0.02, "meta_priority_boost": 0.0}),
    )

    summary_spread = _build_super_alpha_summary(league="NBA", market="spreads", scope="full_game")
    summary_ml = _build_super_alpha_summary(league="NBA", market="moneyline", scope="full_game")

    settings = {
        **DEFAULT_SETTINGS,
        "minimum_roi": 0.002,
        "bankroll": None,
        "unit_stake": 1.0,
        "max_bet": 8.0,
        "kelly_fraction": 0.25,
        "max_bankroll_fraction": 0.05,
        "daily_risk_limit": None,
        "recommendation_policy": "continuous_model_v1",
        "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
        "model_pick_sizing_policy": "all_tiers_weighted_v3",
        "model_pick_allocation_policy": "distinct_family_v1",
        "publication_limits_version": "v3",
        "outcome_sizing_policy": POLICY_V3,
        "capital_defense": True,
        "alpha_acceleration": True,
        "dynamic_event_limits": {
            "version": DYN_V2,
            "max_multiplier": 2.0,
            "max_per_event": 8,
            "max_period_per_event": 4,
            "max_stake_per_event": 25.0,
            "minimum_score": 0.5,
            "minimum_history_signal": 0.05,
        },
        "is_test": False,
    }

    now_str = "2026-11-28T09:35:00+00:00"
    start_str = "2026-11-28T23:00:00+00:00"
    readiness = {
        "version": READINESS_VERSION,
        "passed": True,
        "state": "ready",
        "missing_starter_policy": MISSING_STARTER_LEGACY,
        "checks": {"model_features": {"required": 10, "missing": [], "invalid": []}},
        "model_quality": {"passed": True},
    }

    def _make_sealed_cand(cid: str, market: str, side: str, line, selection: str, p: float, sp: float, odds: float, evid: dict) -> dict:
        tip = {
            "id": cid,
            "game_id": "NBA:401997777",
            "league": "NBA",
            "home": "Los Angeles Lakers",
            "away": "Boston Celtics",
            "start": start_str,
            "market": market,
            "side": side,
            "line": line,
            "selection": selection,
            "model_scope": "full_game",
            "model_version": "nba-v4",
            "probability": p,
            "push_probability": 0.0,
            "stress_probability": sp,
            "reference_odds": odds,
            "odds": odds,
            "expected_roi": sportsbook_ev(p, 0.0, odds),
            "stress_roi": sportsbook_ev(sp, 0.0, odds),
            "minimum_odds": minimum_odds(sp, 0.0, settings["minimum_roi"]),
            "quote_at": now_str,
            "valid_until": "2026-11-28T10:05:00+00:00",
            "created_at": now_str,
            "reference_platform": "Stake.com",
            "reference_market_id": f"mkt_{cid}",
            "reference_contract_description": market,
            "reference_url": "https://stake.com/sports/basketball/usa/nba/lakers-celtics",
            "reference_minimum_notional": 0.50,
            "stake_market": {"availability_verified": True, "tab": "main"},
            "stake_evidence": {
                "fixture_slug": "lakers-celtics",
                "league_path": "basketball/usa/nba",
                "market_name": "Winner (Incl. Overtime)" if market == "moneyline" else "Handicap (Incl. Overtime)",
                "market_groups": [{"name": "Main"}],
                "outcome_name": selection,
                "decimal_odds": odds,
                "fetched_at": now_str,
            },
            "input_readiness": readiness,
            "game_context": {},
            "market_anchor": {"status": "usable", "mode": "shadow", "model_probability": p, "market_probability": 0.50},
            "probability_basis": "independent model (market anchor shadow)",
            "calibration_uncertainty": {"state": "measured", "independent_games": 66},
            "model_trained_at": "2026-11-28T00:00:00+00:00",
            "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
            "outcome_sizing_policy": POLICY_V3,
            "outcome_sizing_evidence": evid,
            "tracking_source": "auto_assumed_placed",
            "is_graduated": True,
        }
        tip["funding_basis"] = funding_basis(tip, settings)
        guard = {
            "passed": True,
            "input_readiness": readiness,
            "publication_limits": publication_limit_receipt(settings),
            "family_selection_policy": CONTINUOUS_PROBABILITY_SELECTION_V4,
            "outcome_sizing_policy": POLICY_V3,
            "recommendation_policy": settings["recommendation_policy"],
            "model_pick_sizing_policy": settings["model_pick_sizing_policy"],
            "model_pick_allocation_policy": settings["model_pick_allocation_policy"],
            "minimum_roi": settings["minimum_roi"],
            "model": {"version": "nba-v4"},
            "sizing_settings": {
                "bankroll": None,
                "unit_stake": 1.0,
                "max_bet": 8.0,
                "kelly_fraction": 0.25,
                "max_bankroll_fraction": 0.05,
                "outcome_sizing_policy": POLICY_V3,
                "capital_defense": True,
                "alpha_acceleration": True,
            },
        }
        guard["candidate_sha256"] = evidence_digest(tip)
        tip["decision_guard"] = guard
        return tip

    # Same-game batch: 1 mild stress_value (stress_roi = +0.005 -> Solid Alpha $2.50-$4.00)
    # + 1 unstressed model_pick (stress_roi = -0.004 -> <= $1.25)
    c_mild = _make_sealed_cand(
        "c1112223334445556667", "spreads", "home", -3.5, "Los Angeles Lakers -3.5",
        0.5425, 0.5025, 2.00, summary_spread,
    )
    c_unstressed = _make_sealed_cand(
        "c888999000aaabbbcccd", "moneyline", "home", None, "Los Angeles Lakers",
        0.54, 0.498, 2.00, summary_ml,
    )
    assert c_mild["funding_basis"]["tier"] == "stress_value"
    assert c_unstressed["funding_basis"]["tier"] == "model_pick"

    chosen, alts = rank_and_size([c_mild, c_unstressed], settings, daily_funded_count=50, held_event_roster=[])
    funded = {c["id"]: c for c in chosen if c["stake"] > 0}
    assert set(funded) == {"c1112223334445556667", "c888999000aaabbbcccd"}
    assert 2.50 <= funded["c1112223334445556667"]["stake"] <= 4.00
    assert 0.50 <= funded["c888999000aaabbbcccd"]["stake"] <= 1.25

    now_dt = parse_time(now_str)
    for cid, issued_tip in funded.items():
        # Both verify_publication and instruction_review must pass 100% cleanly!
        verify_publication(issued_tip, now_dt, settings)
        record = {
            "id": 9999,
            "tip_id": cid,
            "status": "open",
            "stake": issued_tip["stake"],
            "odds": issued_tip["reference_odds"],
            "created_at": now_str,
            "payload": json.loads(json.dumps(issued_tip)),
        }
        rev = instruction_review(record, issued_tip, now_dt, settings["minimum_roi"], 1, settings)
        assert rev["actionable"] is True, f"instruction_review failed for {cid}: {rev}"
        assert rev["stake"] == issued_tip["stake"]



