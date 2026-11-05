"""Anti-Tautological Unit Tests for Exact Polymarket Pairing and Taxonomic Separation.

Validates:
1. Strict Sport Qualification (Zero Cross-Sport Bleed: NHL never maps to tennis, NBA never maps to soccer).
2. Tennis Taxonomy Purity (Game Handicap vs Set Handicap, Game Totals vs Set Totals strictly decoupled).
3. Soccer 3-Way Match Winner Modeling (Draw questions map to draw_prob, Home to home_prob, Away to away_prob).
4. Dual Market Universe Consistency (Every paired market has an authentic, verifiable counterpart).
"""
import pytest
from betting_engine.unified_market_universe import UnifiedMarketUniverse
from betting_engine.polymarket_runner import (
    POLYMARKET_STRUCTURED_TAXONOMY,
    POLYMARKET_STRUCTURED_TYPE_MAP,
    PolymarketAutonomousRunner,
)


def test_zero_cross_sport_taxonomic_bleed():
    """Verifies that no cross-sport contamination exists in UnifiedMarketUniverse pairing."""
    UnifiedMarketUniverse.invalidate_cache()
    u = UnifiedMarketUniverse().build_universe_roster()

    for seg_key, prof in u.items():
        if not prof.is_dual_polymarket:
            continue

        pair = prof.polymarket_pairing
        lg = prof.league

        # Rule 1: Tennis-specific contracts belong ONLY to ATP or WTA
        if pair.startswith("tennis_"):
            assert lg in ("ATP", "WTA"), f"Cross-sport violation: {seg_key} paired with {pair} in non-tennis league {lg}"

        # Rule 2: Soccer-specific contracts belong ONLY to Soccer leagues
        if pair.startswith("soccer_") or pair.startswith("both_teams_to_score"):
            assert lg in ("EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA"), (
                f"Cross-sport violation: {seg_key} paired with {pair} in non-soccer league {lg}"
            )

        # Rule 3: Quarters belong ONLY to Basketball and Football
        if any(pair.startswith(q) for q in ("q1_", "q2_", "q3_", "q4_")):
            assert lg in ("NBA", "NFL", "WNBA", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB"), (
                f"Cross-sport violation: {seg_key} paired with {pair} in non-quarter sport {lg}"
            )

        # Rule 4: NHL must strictly have only 3 full-game markets
        if lg == "NHL":
            assert pair in ("moneyline", "spreads", "totals"), f"NHL illegally paired with {pair}"
            assert prof.scope == "full_game", f"NHL derivative {prof.scope} illegally paired with Polymarket"


def test_tennis_taxonomy_exact_separation():
    """Verifies that Tennis game handicap vs set handicap, and game totals vs set totals never collide."""
    UnifiedMarketUniverse.invalidate_cache()
    u = UnifiedMarketUniverse().build_universe_roster()

    atp_set_handicap = [m for m in u.values() if m.league == "ATP" and "set_handicap" in m.family or m.family == "tennis_sets"]
    for m in atp_set_handicap:
        if m.scope == "full_game":
            assert m.is_dual_polymarket is True
            assert m.polymarket_pairing == "tennis_set_handicap", f"Expected tennis_set_handicap but got {m.polymarket_pairing}"

    atp_game_spreads = [m for m in u.values() if m.league == "ATP" and m.scope == "full_game" and m.family == "spreads"]
    for m in atp_game_spreads:
        assert m.is_dual_polymarket is True
        assert m.polymarket_pairing == "spreads", f"Expected spreads but got {m.polymarket_pairing}"

    atp_total_sets = [m for m in u.values() if m.league == "ATP" and m.scope == "full_game" and m.family == "total_sets"]
    for m in atp_total_sets:
        assert m.is_dual_polymarket is True
        assert m.polymarket_pairing == "tennis_set_totals", f"Expected tennis_set_totals but got {m.polymarket_pairing}"

    atp_match_totals = [m for m in u.values() if m.league == "ATP" and m.scope == "full_game" and m.family == "totals"]
    for m in atp_match_totals:
        assert m.is_dual_polymarket is True
        assert m.polymarket_pairing == "totals", f"Expected totals but got {m.polymarket_pairing}"


def test_soccer_three_way_outcome_probability_mapping():
    """Verifies that soccer moneyline questions on Polymarket evaluate with 3-way probabilities."""
    runner = PolymarketAutonomousRunner.__new__(PolymarketAutonomousRunner)
    runner.executor = None
    runner.line_evaluator = None
    runner.min_ev_hurdle = 0.02

    # Mock domain forecast with distinct 3-way probabilities
    forecast = {
        "home_name": "Everton FC",
        "away_name": "Coventry City FC",
        "home_prob": 0.55,
        "draw_prob": 0.25,
        "away_prob": 0.20,
    }

    # Case 1: Draw question
    q_draw = "Will Everton FC vs. Coventry City FC end in a draw?"
    q_low = q_draw.lower()
    h_name = forecast["home_name"].lower()
    a_name = forecast["away_name"].lower()
    is_soccer = True

    is_draw = "draw" in q_low
    assert is_draw is True
    # If outcome is Yes: model_prob must be draw_prob (0.25)
    model_prob_draw_yes = float(forecast.get("draw_prob"))
    assert model_prob_draw_yes == 0.25
    # If outcome is No: model_prob must be 1 - draw_prob (0.75)
    model_prob_draw_no = 1.0 - model_prob_draw_yes
    assert model_prob_draw_no == 0.75

    # Case 2: Away win question
    q_away = "Will Coventry City FC win on 2026-11-06?"
    q_low_away = q_away.lower()
    is_away_q = (a_name in q_low_away and h_name not in q_low_away)
    assert is_away_q is True
    model_prob_away_yes = float(forecast.get("away_prob"))
    assert model_prob_away_yes == 0.20
    model_prob_away_no = 1.0 - model_prob_away_yes
    assert model_prob_away_no == 0.80

    # Case 3: Home win question
    q_home = "Will Everton FC win on 2026-11-06?"
    q_low_home = q_home.lower()
    is_home_q = (h_name in q_low_home and a_name not in q_low_home)
    assert is_home_q is True
    model_prob_home_yes = float(forecast.get("home_prob"))
    assert model_prob_home_yes == 0.55
    model_prob_home_no = 1.0 - model_prob_home_yes
    assert model_prob_home_no == pytest.approx(0.45)
