"""Experimental, pure Asian payout arithmetic; not a production contract adapter.

One quoted decimal price applies to both equal virtual legs. Scores must be
integer counts for the caller's independently validated settlement scope. No
source, participation, scope, quote, or model approval is implied by this API.
Money and probabilities use exact Fractions, with no currency rounding or fees.
"""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Iterable, Literal


Number = int | float | str | Decimal | Fraction
LegState = Literal["win", "push", "loss"]
State = Literal["full_win", "half_win", "push", "half_loss", "full_loss"]


def _number(value: Number, name: str) -> Fraction:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not bool")
    try:
        # Decimal spelling avoids inheriting binary floating-point artifacts.
        return value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc


def _score(value: Number) -> Fraction:
    score = _number(value, "score")
    if score < 0 or score.denominator != 1:
        raise ValueError("scores must be nonnegative integer counts")
    return score


def _price_stake(odds: Number, stake: Number) -> tuple[Fraction, Fraction]:
    price, amount = _number(odds, "odds"), _number(stake, "stake")
    if price <= 1 or amount <= 0:
        raise ValueError("decimal odds must exceed 1 and stake must be positive")
    return price, amount


def decompose_line(line: Number) -> tuple[Fraction, ...]:
    """Ascending adjacent integer/half lines; integer/half inputs pass through.

    No rounding to the nearest permitted line. Negative quarters use floor,
    e.g. -0.25 -> (-0.5, 0), not (0, 0.5).
    """
    value = _number(line, "line")
    if (value * 4).denominator != 1:
        raise ValueError("line must be an exact multiple of 0.25")
    if (value * 2).denominator == 1:
        return (value,)
    lower = Fraction((value * 2).__floor__(), 2)
    return lower, lower + Fraction(1, 2)


@dataclass(frozen=True)
class AsianContract:
    """Signed handicap is always for the selected team (home or away).

    Uses repository market names: spreads/home|away, totals/over|under.
    This mathematical specification is not an exact Stake event identity.
    """

    market: str
    side: str
    line: Number

    def __post_init__(self) -> None:
        sides = {"spreads": ("home", "away"), "totals": ("over", "under")}
        if self.market not in sides or self.side not in sides[self.market]:
            raise ValueError("unsupported Asian market or side")
        line = _number(self.line, "line")
        decompose_line(line)
        if self.market == "totals" and line < 0:
            raise ValueError("total line must be nonnegative")
        object.__setattr__(self, "line", line)


def _leg_states(contract: AsianContract, home: Number, away: Number) -> tuple[LegState, ...]:
    home, away = _score(home), _score(away)
    states = []
    for line in decompose_line(contract.line):
        if contract.market == "spreads":
            value = (home - away if contract.side == "home" else away - home) + line
        else:
            value = (home + away - line) * (1 if contract.side == "over" else -1)
        states.append("win" if value > 0 else "loss" if value < 0 else "push")
    return tuple(states)


def _state(legs: tuple[LegState, ...]) -> State:
    outcomes = frozenset(legs)
    return {
        frozenset(("win",)): "full_win",
        frozenset(("win", "push")): "half_win",
        frozenset(("push",)): "push",
        frozenset(("loss", "push")): "half_loss",
        frozenset(("loss",)): "full_loss",
    }[outcomes]


@dataclass(frozen=True)
class LegPayoff:
    line: Fraction
    stake: Fraction
    state: LegState
    gross_return: Fraction
    profit: Fraction


@dataclass(frozen=True)
class Payoff:
    state: State
    stake: Fraction
    odds: Fraction
    gross_return: Fraction
    profit: Fraction
    legs: tuple[LegPayoff, ...]


def settle_asian(
    contract: AsianContract, home_score: Number, away_score: Number,
    *, odds: Number, stake: Number = 1,
) -> Payoff:
    """Return hypothetical cashflows, never update a ticket or round cents.

    The stake is an arithmetic scale, not a funding recommendation. Production
    $1-$5 sizing and guards are outside this inactive utility.
    """
    price, amount = _price_stake(odds, stake)
    states = _leg_states(contract, home_score, away_score)
    leg_stake = amount / len(states)
    legs = []
    for line, state in zip(decompose_line(contract.line), states):
        returned = leg_stake * (price if state == "win" else 1 if state == "push" else 0)
        legs.append(LegPayoff(line, leg_stake, state, returned, returned - leg_stake))
    gross = sum((leg.gross_return for leg in legs), Fraction())
    return Payoff(_state(states), amount, price, gross, gross - amount, tuple(legs))


@dataclass(frozen=True)
class StateProbabilities:
    full_win: Fraction
    half_win: Fraction
    push: Fraction
    half_loss: Fraction
    full_loss: Fraction

    @property
    def winning_fraction(self) -> Fraction:
        return self.full_win + self.half_win / 2

    @property
    def losing_fraction(self) -> Fraction:
        return self.full_loss + self.half_loss / 2

    @property
    def refunded_fraction(self) -> Fraction:
        return self.push + (self.half_win + self.half_loss) / 2

    def expected_profit(self, odds: Number, stake: Number = 1) -> Fraction:
        price, amount = _price_stake(odds, stake)
        return amount * (self.winning_fraction * (price - 1) - self.losing_fraction)

    @property
    def break_even_odds(self) -> Fraction | None:
        """None means no unique finite price (including an all-push PMF)."""
        if not self.winning_fraction:
            return None
        return 1 + self.losing_fraction / self.winning_fraction


@dataclass(frozen=True)
class Aggregation:
    probabilities: StateProbabilities
    # Each key has one state per virtual leg in ascending line order.
    joint_legs: tuple[tuple[tuple[LegState, ...], Fraction], ...]
    input_mass: Fraction


def aggregate_asian(
    contract: AsianContract,
    score_distribution: Iterable[tuple[Number, Number, Number]],
) -> Aggregation:
    """Aggregate (home_score, away_score, probability) from a supplied joint PMF.

    Both legs use each SAME score realization; no multiplication of marginal
    leg probabilities. Repeated score rows are additive. A mass discrepancy
    <=1e-12 is normalized proportionally and input_mass discloses it; larger
    errors (including missing tails) fail. Zero-weight rows are still validated.

    This odds-free function cannot authenticate model provenance. Inputs must
    come from independently validated, scope-matched sports forecasts before
    any future production integration; passing arithmetic is not approval.
    """
    joint = {}
    mass = Fraction()
    for home, away, probability in score_distribution:
        weight = _number(probability, "probability")
        if not 0 <= weight <= 1:
            raise ValueError("probability must be between 0 and 1")
        legs = _leg_states(contract, home, away)
        mass += weight
        if weight:
            joint[legs] = joint.get(legs, Fraction()) + weight
    if not mass or abs(mass - 1) > Fraction(1, 10**12):
        raise ValueError("score distribution must have total probability 1")
    states = dict.fromkeys(("full_win", "half_win", "push", "half_loss", "full_loss"), Fraction())
    normalized = tuple((legs, weight / mass) for legs, weight in sorted(joint.items()))
    for legs, weight in normalized:
        states[_state(legs)] += weight
    return Aggregation(StateProbabilities(**states), normalized, mass)
