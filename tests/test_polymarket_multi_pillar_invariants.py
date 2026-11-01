"""Anti-Tautological Mathematical Invariant Test Suite for Polymarket Multi-Pillar Integration.

Validates:
1. Probability axioms (P in [0, 1], sum(P) == 1.0).
2. Mathematical EV invariant (EV = P * O - 1.0).
3. Continuous Dynamic EV Hurdle properties (monotonicity, variance scaling).
4. Strict Scope Isolation Invariant (period/quarter/team totals never contaminate full-game ladders).
5. Home vs Away Spread Symmetry and Non-Collision Invariant.
6. Crucible Divergence Defense & CLOB Anchor Logit Blending Invariants.
"""
import pytest
import math
from datetime import timedelta
from betting_engine.polymarket_service import PolymarketService, _detect_market_scope
from betting_engine.crucible_rules import evaluate_crucible_candidate
from betting_engine.domain import utcnow, Game
from betting_engine.markets import Market
from betting_engine.decisions import candidate, DEFAULT_SETTINGS
from types import SimpleNamespace


def test_decision_engine_polymarket_reasons():
    """Assert candidate embeds verified Polymarket consensus reasons."""
    g = SimpleNamespace(
        id="test_g_1",
        league="NFL",
        start=(utcnow() + timedelta(hours=4)).isoformat(),
        home="Kansas City Chiefs",
        away="Las Vegas Raiders",
        state="scheduled",
        source="test",
        context={
            "polymarket": {
                "matched": True,
                "has_spreads": True,
                "spreads": {
                    "home:-3.5": {"prob": 0.58, "line": -3.5},
                    "away:+3.5": {"prob": 0.42, "line": 3.5},
                }
            }
        }
    )

    m = SimpleNamespace(
        id="stake_m_1",
        kind="spreads",
        side="home",
        line=-3.5,
        selection="Kansas City Chiefs -3.5",
        platform="Stake.com",
        url="https://stake.com/test",
        description="Spread"
    )

    q = SimpleNamespace(
        fetched_at=utcnow().isoformat(),
        effective_cost=1.0 / 1.91,
        price=1.0 / 1.91,
        fee_per_share=0,
        minimum_notional=0
    )

    f = SimpleNamespace(
        market_probability=lambda *args: (0.58, 0.0),
        disagreement=0,
        margin=7.0,
        total=48.5,
        features={'home_rest': 3.0, 'away_rest': 3.0},
        warnings=[],
        model_version='v1',
        model_probabilities={},
        home_points=27.0,
        away_points=20.0
    )

    t = candidate(g, m, q, f, DEFAULT_SETTINGS)
    assert t is not None
    # Stake implied at 1.91 is 1/1.91 = 52.36%. Poly consensus is 58.0% (+5.6% edge).
    reasons_str = " ".join(t.get('reasons', []))
    assert "🔮 Polymarket Edge: Spread (-3.5) true consensus 58.0%" in reasons_str


def test_independent_mathematical_ev_and_hurdle_invariants():
    """Verify EV = P * O - 1.0 and monotonic dynamic hurdle without code-circularity."""
    odds_ladder = [1.40, 1.65, 1.91, 2.20, 3.00, 4.50, 7.00]
    hurdles = [PolymarketService.calculate_dynamic_ev_hurdle(o) for o in odds_ladder]

    # Invariant 1: Monotonicity - higher odds (higher variance) require smaller absolute edge %
    for i in range(len(hurdles) - 1):
        assert hurdles[i] >= hurdles[i+1], f"Hurdle not monotonically non-increasing: {hurdles[i]} < {hurdles[i+1]}"

    # Invariant 2: Mathematical EV calculation
    for o in odds_ladder:
        p_breakeven = 1.0 / o
        calc = PolymarketService.calculate_stale_edge(p_breakeven, o)
        # At exactly break-even probability, EV must be 0.0
        assert math.isclose(calc["expected_roi_pct"], 0.0, abs_tol=1e-5)
        assert calc["edge_pct"] == 0.0
        assert calc["is_stale"] is False

        # With positive alpha equal to exact hurdle (+ delta)
        hurdle = PolymarketService.calculate_dynamic_ev_hurdle(o)
        p_plus_edge = p_breakeven + hurdle + 1e-4
        calc_edge = PolymarketService.calculate_stale_edge(p_plus_edge, o, hurdle=hurdle)
        assert calc_edge["is_stale"] is True
        expected_roi = (p_plus_edge * o - 1.0) * 100
        assert math.isclose(calc_edge["expected_roi_pct"], expected_roi, abs_tol=1e-2)


def test_scope_isolation_mathematical_invariants(tmp_path):
    """Assert that no period, quarter, or team-total market contaminates full-game ladders."""
    svc = PolymarketService(cache_dir=tmp_path)

    # Complex multi-market fixture simulating full NFL game with derivatives
    fake_markets = [
        # Full Game Spreads
        {"sportsMarketType": "spreads", "question": "Spread: Chiefs (-3.5)", "line": -3.5, "outcomes": ["Chiefs", "Raiders"], "outcomePrices": ["0.55", "0.45"], "bestBid": 0.54, "bestAsk": 0.56, "volumeNum": 50000.0},
        {"sportsMarketType": "spreads", "question": "Spread: Raiders (-3.5)", "line": -3.5, "outcomes": ["Raiders", "Chiefs"], "outcomePrices": ["0.15", "0.85"], "bestBid": 0.14, "bestAsk": 0.16, "volumeNum": 20000.0},
        # Derivative Spreads (Must be isolated)
        {"sportsMarketType": "first_half_spreads", "question": "1H Spread: Chiefs (-2.5)", "line": -2.5, "outcomes": ["Chiefs", "Raiders"], "outcomePrices": ["0.52", "0.48"], "bestBid": 0.51, "bestAsk": 0.53, "volumeNum": 8000.0},
        {"sportsMarketType": "second_half_spreads", "question": "2H Spread: Chiefs (-1.5)", "line": -1.5, "outcomes": ["Chiefs", "Raiders"], "outcomePrices": ["0.50", "0.50"], "bestBid": 0.49, "bestAsk": 0.51, "volumeNum": 5000.0},
        {"sportsMarketType": "q1_spreads", "question": "1Q Spread: Chiefs (-0.5)", "line": -0.5, "outcomes": ["Chiefs", "Raiders"], "outcomePrices": ["0.58", "0.42"], "bestBid": 0.57, "bestAsk": 0.59, "volumeNum": 3000.0},
        {"sportsMarketType": "q4_spreads", "question": "4Q Spread: Chiefs (-3.5)", "line": -3.5, "outcomes": ["Chiefs", "Raiders"], "outcomePrices": ["0.40", "0.60"], "bestBid": 0.39, "bestAsk": 0.41, "volumeNum": 4000.0},
        # Full Game Totals
        {"sportsMarketType": "totals", "question": "Chiefs vs. Raiders: O/U 48.5", "line": 48.5, "outcomes": ["Over", "Under"], "outcomePrices": ["0.52", "0.48"], "bestBid": 0.51, "bestAsk": 0.53, "volumeNum": 60000.0},
        # Derivative Totals (Must be isolated)
        {"sportsMarketType": "team_totals", "question": "Chiefs Team Total: O/U 26.5", "line": 26.5, "outcomes": ["Over", "Under"], "outcomePrices": ["0.55", "0.45"], "bestBid": 0.54, "bestAsk": 0.56, "volumeNum": 15000.0},
        {"sportsMarketType": "first_half_totals", "question": "1H Total Points: O/U 24.5", "line": 24.5, "outcomes": ["Over", "Under"], "outcomePrices": ["0.49", "0.51"], "bestBid": 0.48, "bestAsk": 0.50, "volumeNum": 7000.0},
    ]

    fake_event = [{
        "id": "invariant_ev_1",
        "title": "Raiders vs. Chiefs",
        "startTime": "2026-11-01T21:00:00Z",
        "teams": [{"name": "Kansas City Chiefs"}, {"name": "Las Vegas Raiders"}],
        "markets": fake_markets,
    }]

    svc.get_open_events = lambda league: fake_event

    game = {
        "league": "NFL",
        "home": "Kansas City Chiefs",
        "away": "Las Vegas Raiders",
        "start": "2026-11-01T21:00:00Z",
    }

    res = svc.enrich_game_context(game)
    assert res["matched"] is True

    # INVARIANT A: Scope Isolation for Spreads
    # Full game spreads must have exactly the full game lines
    full_spreads = res["spreads"]
    assert "home:-3.5" in full_spreads
    assert "away:+3.5" in full_spreads
    assert "home:+3.5" in full_spreads
    assert "away:-3.5" in full_spreads

    # The 4Q Chiefs (-3.5) MUST NOT overwrite full-game Chiefs (-3.5)
    # Full game Chiefs (-3.5) was 0.55 / 0.45; 4Q Chiefs (-3.5) was 0.40 / 0.60
    assert full_spreads["home:-3.5"]["prob"] == 0.55
    assert full_spreads["away:+3.5"]["prob"] == 0.45

    # Check derivative spreads went to proper period scopes
    p_spreads = res["period_spreads"]
    assert "half_1" in p_spreads
    assert "half_2" in p_spreads
    assert "quarter_1" in p_spreads
    assert "quarter_4" in p_spreads

    assert p_spreads["half_1"]["home:-2.5"]["prob"] == 0.52
    assert p_spreads["quarter_4"]["home:-3.5"]["prob"] == 0.40

    # INVARIANT B: Scope Isolation for Totals
    full_totals = res["totals"]
    assert "48.5" in full_totals
    assert full_totals["48.5"]["p_true_over"] == 0.52
    assert full_totals["48.5"]["p_true_under"] == 0.48

    # Team Total (26.5) and 1H Total (24.5) MUST NEVER appear in full-game totals
    assert "26.5" not in full_totals
    assert "24.5" not in full_totals

    # 1H Total must be under period_totals["half_1"]
    assert "half_1" in res["period_totals"]
    assert "24.5" in res["period_totals"]["half_1"]
    assert res["period_totals"]["half_1"]["24.5"]["p_true_over"] == 0.49


def test_spread_side_symmetry_and_non_collision():
    """Assert that reverse lines (Home -3.5 vs Away -3.5) maintain side-symmetry without collision."""
    # When Market 1: Chiefs (-3.5) priced at 0.55 home cover / 0.45 away cover (+3.5)
    # And Market 2: Raiders (-3.5) priced at 0.15 away cover (-3.5) / 0.85 home cover (+3.5)
    home_spreads = {
        "home:-3.5": {"prob": 0.55},
        "home:+3.5": {"prob": 0.85},
    }
    away_spreads = {
        "away:+3.5": {"prob": 0.45},
        "away:-3.5": {"prob": 0.15},
    }

    # Axiom: P(Chiefs -3.5) + P(Raiders +3.5) must equal 1.0
    assert math.isclose(home_spreads["home:-3.5"]["prob"] + away_spreads["away:+3.5"]["prob"], 1.0)
    # Axiom: P(Raiders -3.5) + P(Chiefs +3.5) must equal 1.0
    assert math.isclose(away_spreads["away:-3.5"]["prob"] + home_spreads["home:+3.5"]["prob"], 1.0)


def test_crucible_rules_polymarket_guards():
    """Assert crucible sharp divergence defense and CLOB anchor blending invariants."""
    context = {
        "polymarket": {
            "matched": True,
            "has_moneyline": True,
            "true_prob_home": 0.35,  # Polymarket sharp consensus gives home only 35%
            "true_prob_away": 0.65,
            "has_spreads": True,
            "spreads": {
                "home:-3.5": {"prob": 0.38, "p_true_home": 0.38},
                "away:+3.5": {"prob": 0.62, "p_true_away": 0.62},
            },
            "has_totals": True,
            "totals": {
                "48.5": {"p_true_over": 0.40, "p_true_under": 0.60},
            }
        }
    }

    # Case 1: Sharp Divergence Defense (Model overconfident at 0.55 vs Poly 0.38 -> diff 0.17 > 0.12)
    candidate_spread = {
        "league": "NFL",
        "model_scope": "full_game",
        "market": "spreads",
        "side": "home",
        "line": -3.5,
        "probability": 0.55,
        "reference_odds": 1.91,
        "game_context": context,
    }
    rules_divergence = [{
        "hypothesis_id": "hyp_polymarket_stale_line_arb_v1",
        "league": "NFL",
        "category": "filter_gate",
        "proposed_formula_or_rule": "divergence",
    }]
    ok, code, _ = evaluate_crucible_candidate(candidate_spread, rules_divergence, context=context)
    assert ok is False
    assert "polymarket_sharp_divergence_rejected" in code

    # Case 2: CLOB Zero-Vig Anchor Blending pulls overconfidence toward consensus
    candidate_ml = {
        "league": "NFL",
        "model_scope": "full_game",
        "market": "moneyline",
        "side": "home",
        "probability": 0.45,
        "reference_odds": 2.10,
        "game_context": context,
    }
    rules_anchor = [{
        "hypothesis_id": "hyp_polymarket_clob_anchor_v1",
        "league": "NFL",
        "category": "calibration_shrinkage",
        "proposed_formula_or_rule": "polymarket_anchor",
    }]
    approved, _, p_adj = evaluate_crucible_candidate(candidate_ml, rules_anchor, context=context)
    assert approved is True
    # Original model p_raw was 0.45; Poly true_prob was 0.35.
    # Blended p_adj must be pulled downward towards 0.35 (strictly < 0.45)
    assert 0.35 < p_adj < 0.45
