"""Independent payout oracles; example prices/PMFs are synthetic, not offers."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, astuple
from decimal import Decimal, localcontext
from fractions import Fraction as F
from pathlib import Path
import ast

import pytest

from betting_engine.asian_quarter import AsianContract, aggregate_asian, decompose_line, settle_asian


@pytest.mark.parametrize("line,expected", [
    ("-2.75", (F(-3), F(-5, 2))), ("-0.75", (F(-1), F(-1, 2))),
    ("-0.25", (F(-1, 2), F(0))), ("0.25", (F(0), F(1, 2))),
    ("0.75", (F(1, 2), F(1))), ("2.25", (F(2), F(5, 2))),
    ("2.5", (F(5, 2),)), ("-1", (F(-1),)), ("-0.0", (F(0),)),
])
def test_adjacent_lines_preserve_signed_quote(line, expected):
    legs = decompose_line(line)
    assert legs == expected
    assert sum(legs) / len(legs) == F(line)
    assert all((leg * 2).denominator == 1 for leg in legs)
    assert decompose_line("-0.0") == decompose_line("+0.0")


@pytest.mark.parametrize("market,side,line,home,away,odds,stake,state,profit", [
    ("spreads", "home", "-.25", 1, 0, "1.90", 2, "full_win", "1.80"),
    ("spreads", "home", "-.25", 0, 0, "1.90", 2, "half_loss", "-1"),
    ("spreads", "home", "-.25", 0, 1, "1.90", 2, "full_loss", "-2"),
    ("spreads", "home", "-.75", 1, 0, "2", 4, "half_win", "2"),
    ("spreads", "home", "-.75", 2, 0, "2", 4, "full_win", "4"),
    ("spreads", "away", "-.75", 0, 1, "2", 4, "half_win", "2"),
    ("spreads", "home", ".25", 0, 0, "1.90", 2, "half_win", ".90"),
    ("spreads", "away", ".25", 0, 0, "1.90", 2, "half_win", ".90"),
    ("spreads", "home", ".75", 0, 1, "1.90", 2, "half_loss", "-1"),
    ("spreads", "away", ".75", 1, 0, "1.90", 2, "half_loss", "-1"),
    ("totals", "over", "2.25", 1, 1, "1.95", 2, "half_loss", "-1"),
    # Corrected advisory arithmetic: 2 * (1.95 - 1) = 1.90, not 1.95.
    ("totals", "over", "2.25", 2, 1, "1.95", 2, "full_win", "1.90"),
    ("totals", "under", "2.75", 2, 1, "1.90", 2, "half_loss", "-1"),
    ("totals", "under", "2.75", 1, 1, "1.90", 2, "full_win", "1.80"),
    ("totals", "over", "2.75", 2, 1, "2", 2, "half_win", "1"),
    ("totals", "under", "2.25", 1, 1, "2", 2, "half_win", "1"),
    ("totals", "over", "1.75", 1, 1, "2", 2, "half_win", "1"),
    ("totals", "under", "1.75", 1, 1, "2", 2, "half_loss", "-1"),
    ("spreads", "home", "-1", 2, 1, "1.90", 2, "push", "0"),
    ("totals", "over", "3", 2, 1, "1.90", 2, "push", "0"),
    ("totals", "under", "0", 0, 0, "1.90", 2, "push", "0"),
])
def test_hand_calculated_cashflows(market, side, line, home, away, odds, stake, state, profit):
    result = settle_asian(AsianContract(market, side, line), home, away, odds=odds, stake=stake)
    assert result.state == state
    assert result.profit == F(profit)
    assert result.gross_return == F(stake) + F(profit)
    assert sum(leg.stake for leg in result.legs) == stake
    assert sum(leg.profit for leg in result.legs) == result.profit


def test_integer_score_grid_against_direct_quarter_distance_oracle():
    # Independent oracle: residual +/- 1 quarter implies a half outcome;
    # distance >= 3 quarters implies a full outcome. No leg decomposition.
    for ticks in range(-27, 28, 2):
        line = F(ticks, 4)
        for home in range(7):
            for away in range(7):
                specs = [("spreads", "home", 4 * (home - away) + ticks),
                         ("spreads", "away", 4 * (away - home) + ticks)]
                if line > 0:
                    specs += [("totals", "over", 4 * (home + away) - ticks),
                              ("totals", "under", ticks - 4 * (home + away))]
                for market, side, distance in specs:
                    state = ("half_win" if distance == 1 else "full_win") if distance > 0 else (
                        "half_loss" if distance == -1 else "full_loss")
                    for odds in (F(187, 100), F(23, 10)):
                        result = settle_asian(AsianContract(market, side, line), home, away, odds=odds)
                        profit = odds - 1 if distance > 0 else F(-1)
                        if abs(distance) == 1:
                            profit /= 2
                        assert (result.state, result.profit) == (state, profit)
                        assert result.state != "push"


def test_mirrored_contracts_and_total_directions_at_even_odds():
    for ticks in range(-25, 26):
        line = F(ticks, 4)
        for home, away in ((0, 0), (1, 2), (6, 0), (0, 6)):
            h = settle_asian(AsianContract("spreads", "home", line), home, away, odds=2)
            a = settle_asian(AsianContract("spreads", "away", -line), home, away, odds=2)
            assert h.profit == -a.profit
            if line >= 0:
                over = settle_asian(AsianContract("totals", "over", line), home, away, odds=2)
                under = settle_asian(AsianContract("totals", "under", line), home, away, odds=2)
                assert over.profit == -under.profit


def test_no_per_leg_currency_rounding_or_decimal_context_dependency():
    with localcontext() as ctx:
        ctx.prec = 2
        c = AsianContract("spreads", "home", Decimal(".25"))
        result = settle_asian(c, 0, 0, odds=Decimal("1.87"), stake=Decimal("1.05"))
    assert [leg.stake for leg in result.legs] == [F(".525"), F(".525")]
    assert result.gross_return == F("1.50675")
    assert result.profit == F(".45675")
    assert settle_asian(c, 0, 0, odds="1.87").gross_return == F("1.435")
    assert settle_asian(c, 1, 0, odds="1.87").gross_return == F("1.87")


def test_joint_payout_probabilities_and_exact_break_even():
    distribution = [(1, 0, ".45"), (0, 0, ".30"), (0, 1, ".25")]
    result = aggregate_asian(AsianContract("spreads", "home", "-.25"), distribution)
    p = result.probabilities
    assert astuple(p) == (F(".45"), 0, 0, F(".30"), F(".25"))
    assert dict(result.joint_legs) == {
        ("win", "win"): F(".45"), ("loss", "push"): F(".30"), ("loss", "loss"): F(".25"),
    }
    assert sum(astuple(p)) == 1
    assert p.winning_fraction + p.losing_fraction + p.refunded_fraction == 1
    assert p.expected_profit("1.90") == F(".005")
    assert p.break_even_odds == F(17, 9)
    assert p.expected_profit(p.break_even_odds) == 0
    # Independence falsely introduces loss/win with mass .55*.45. Its EV
    # nevertheless equals the correct EV by linearity, so EV alone is no test.
    assert ("loss", "win") not in dict(result.joint_legs)
    assert dict(result.joint_legs)[("win", "win")] != F(".45") ** 2
    independent_ev = (F(".45") * F(".90") - F(".55")) / 2 + (
        F(".45") * F(".90") - F(".25")) / 2
    assert independent_ev == p.expected_profit("1.90")


@pytest.mark.parametrize("market,side,line,expected", [
    ("spreads", "home", ".25", (".2", ".3", "0", "0", ".5")),
    ("spreads", "away", "-.25", (".5", "0", "0", ".3", ".2")),
    ("totals", "over", "2.75", ("0", ".2", "0", "0", ".8")),
    ("totals", "under", "2.75", (".8", "0", "0", ".2", "0")),
    ("totals", "over", "2.25", (".2", "0", "0", ".3", ".5")),
    ("totals", "under", "2.25", (".5", ".3", "0", "0", ".2")),
])
def test_distribution_aggregation_matches_manual_state_masses(market, side, line, expected):
    scores = [(2, 1, ".2"), (1, 1, ".3"), (0, 1, ".5")]
    p = aggregate_asian(AsianContract(market, side, line), scores).probabilities
    assert astuple(p) == tuple(F(v) for v in expected)
    expected_profit = 3 * (F(expected[0]) * F(".9") + F(expected[1]) * F(".45")
                           - F(expected[3]) / 2 - F(expected[4]))
    assert p.expected_profit("1.9", 3) == expected_profit


def test_single_leg_push_and_undefined_break_even():
    p = aggregate_asian(AsianContract("totals", "over", 2), [(1, 1, 1)]).probabilities
    assert p.push == 1 and p.break_even_odds is None
    assert p.expected_profit("1.9", 5) == 0
    p = aggregate_asian(AsianContract("totals", "over", "2.25"), [(1, 1, 1)]).probabilities
    assert p.half_loss == 1 and p.break_even_odds is None


def test_price_does_not_change_forecast_and_inputs_are_immutable():
    scores = [[2, 1, ".2"], [1, 1, ".3"], [0, 1, ".5"]]
    before = deepcopy(scores)
    c = AsianContract("totals", "over", "2.25")
    result = aggregate_asian(c, scores)
    assert result.probabilities.expected_profit("1.9") != result.probabilities.expected_profit("2.5")
    assert aggregate_asian(c, iter(scores)) == result
    assert aggregate_asian(c, reversed(scores)) == result
    assert scores == before
    with pytest.raises(FrozenInstanceError):
        c.line = 3
    with pytest.raises(FrozenInstanceError):
        result.probabilities.full_win = 1


def test_duplicate_support_and_disclosed_tiny_mass_normalization():
    c = AsianContract("spreads", "home", "-.25")
    scores = [(1, 0, ".15"), (1, 0, ".30"), (0, 0, ".30"), (0, 1, ".25")]
    assert aggregate_asian(c, scores) == aggregate_asian(c, [(1, 0, ".45"), *scores[2:]])
    result = aggregate_asian(c, [(1, 0, ".45"), (0, 1, ".5499999999999")])
    assert result.input_mass == F(".9999999999999")
    assert sum(astuple(result.probabilities)) == 1


@pytest.mark.parametrize("line", ["2.3", "0.25000000000000001", float("nan"), float("inf"),
                                  float("-inf"), True, None, "", "bad"])
def test_invalid_lines_fail_without_rounding(line):
    with pytest.raises(ValueError):
        decompose_line(line)


@pytest.mark.parametrize("market,side,line", [("moneyline", "home", 0), ("totals", "home", 2),
    ("spreads", "draw", 0), ("totals", "under", "-.25"), ("quarter_1:totals", "over", 2)])
def test_no_implicit_market_or_scope_mapping(market, side, line):
    with pytest.raises(ValueError):
        AsianContract(market, side, line)


@pytest.mark.parametrize("score", [None, -1, 1.5, True, "NaN", "Infinity"])
def test_missing_or_noninteger_scores_never_become_zero(score):
    c = AsianContract("totals", "over", "2.25")
    for home, away in ((score, 0), (0, score)):
        with pytest.raises(ValueError):
            settle_asian(c, home, away, odds=2)
        with pytest.raises(ValueError):
            aggregate_asian(c, [(home, away, 0), (1, 1, 1)])


@pytest.mark.parametrize("odds,stake", [(1, 1), (0, 1), (-2, 1), ("NaN", 1), ("inf", 1),
    (True, 1), (2, 0), (2, -1), (2, True), (2, "NaN"), (2, "inf")])
def test_invalid_prices_and_stakes(odds, stake):
    c = AsianContract("totals", "over", "2.25")
    with pytest.raises(ValueError):
        settle_asian(c, 2, 1, odds=odds, stake=stake)
    p = aggregate_asian(c, [(2, 1, 1)]).probabilities
    with pytest.raises(ValueError):
        p.expected_profit(odds, stake)


@pytest.mark.parametrize("scores", [[], [(1, 0, ".9")], [(1, 0, 0)], [(1, 0, "1.01")],
    [(1, 0, "-.1"), (0, 1, "1.1")], [(1, 0, "NaN")], [(1, 0, "Infinity")],
    [(1, 0, True)], [(1, 0, ".99999999")]])
def test_invalid_pmf_fails_instead_of_filling_missing_mass(scores):
    with pytest.raises(ValueError):
        aggregate_asian(AsianContract("spreads", "home", "-.25"), scores)


def test_supplied_public_observation_schema_accepts_integer_valued_floats():
    # Observed fields copied from data/games.json (hash and scope in D08 doc).
    # Line and odds below are hypothetical arithmetic inputs, not historical bets.
    observation = {"id": "NCAAF:401238035", "state": "final", "home_score": 45.0,
                   "away_score": 35.0, "start": "2020-09-04T00:00Z",
                   "fetched_at": "2026-09-29T13:09:58.605304+00:00"}
    before = deepcopy(observation)
    result = settle_asian(AsianContract("spreads", "home", "-10.25"),
                          observation["home_score"], observation["away_score"], odds="1.9")
    assert result.state == "half_loss" and result.profit == F("-.5")
    assert observation == before


def test_experimental_adapter_is_not_imported_by_production():
    root = Path(__file__).resolve().parents[1] / "src" / "betting_engine"
    for path in root.rglob("*.py"):
        if path.name == "asian_quarter.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert "asian_quarter" not in (node.module or "").split(".")
                assert all(alias.name != "asian_quarter" for alias in node.names)
            elif isinstance(node, ast.Import):
                assert all("asian_quarter" not in alias.name.split(".") for alias in node.names)
