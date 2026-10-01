"""Venue-independent arithmetic; probabilities must come from independent models.

No model, price feed, scheduler, or production approval is implied by this module.
All returns are net profit per dollar of total capital at risk.
"""
from dataclasses import dataclass
from datetime import datetime
from math import floor, isfinite


def finite(value: float) -> float:
    if not isfinite(value):
        raise ValueError("Non-finite numeric input")
    return value


@dataclass(frozen=True)
class Probabilities:
    win: float
    push: float
    lose: float

    def __post_init__(self):
        values = (self.win, self.push, self.lose)
        if any(not 0 <= finite(x) <= 1 for x in values):
            raise ValueError("Probabilities must be in [0, 1]")
        if abs(sum(values) - 1) > 1e-9:
            raise ValueError("Probabilities must sum to 1")

    def conservative(self, haircut: float) -> "Probabilities":
        """Sensitivity scenario, NOT a statistical confidence interval."""
        if not 0 <= finite(haircut) <= 1:
            raise ValueError("Invalid probability haircut")
        shift = min(self.win, haircut)
        return Probabilities(self.win - shift, self.push, self.lose + shift)


@dataclass(frozen=True)
class Payoffs:
    win: float
    push: float = 0.0
    lose: float = -1.0

    def __post_init__(self):
        for value in (self.win, self.push, self.lose):
            if finite(value) < -1:
                raise ValueError("Cannot lose more than defined capital at risk")
        if self.win <= 0 or self.lose >= 0 or self.push > 0:
            raise ValueError("Invalid long-only payoff")


def decimal_payoffs(odds: float, fee_per_stake: float = 0) -> Payoffs:
    """An upfront fee is paid even on a push; denominator includes that fee."""
    if finite(odds) <= 1 or finite(fee_per_stake) < 0:
        raise ValueError("Invalid odds or fee")
    capital = 1 + fee_per_stake
    return Payoffs((odds - 1 - fee_per_stake) / capital,
                   -fee_per_stake / capital, -1)


def token_payoffs(all_in_cost_per_share: float) -> Payoffs:
    """Binary token paying $1 on a win; caller supplies executable cost + fees.

    Cancelled/ambiguous markets need their own settlement model. This function
    does not claim that a Polymarket cancellation refunds the purchase price.
    """
    if not 0 < finite(all_in_cost_per_share) < 1:
        raise ValueError("Invalid binary token cost")
    return Payoffs(1 / all_in_cost_per_share - 1)


def expected_return(p: Probabilities, r: Payoffs) -> float:
    return p.win * r.win + p.push * r.push + p.lose * r.lose


def kelly_fraction(p: Probabilities, r: Payoffs) -> float:
    """Numerical log-growth optimum, including pushes and upfront fees."""
    if expected_return(p, r) <= 0:
        return 0.0
    pairs = ((p.win, r.win), (p.push, r.push), (p.lose, r.lose))
    def derivative(f):
        return sum(prob * ret / (1 + f * ret) for prob, ret in pairs if prob)
    low, high = 0.0, 1 - 1e-12
    if derivative(high) >= 0:
        return high
    for _ in range(80):
        mid = (low + high) / 2
        if derivative(mid) > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def paper_stake(p: Probabilities, r: Payoffs, *, bankroll: float,
                daily_remaining: float, event_remaining: float,
                fraction: float = 0.25, max_bankroll_fraction: float = 0.01,
                min_stake: float = 1, max_stake: float = 5) -> float:
    """Research-only size. Never rounds a sub-minimum allocation up to $1."""
    for value in (bankroll, daily_remaining, event_remaining, min_stake, max_stake):
        if finite(value) < 0:
            raise ValueError("Negative budget")
    if not 0 < finite(fraction) <= 1 or not 0 < finite(max_bankroll_fraction) <= 1:
        raise ValueError("Invalid risk fraction")
    if not 1 <= min_stake <= max_stake <= 5:
        raise ValueError("Stake range must stay inside $1-$5")
    size = min(bankroll * fraction * kelly_fraction(p, r),
               bankroll * max_bankroll_fraction, daily_remaining,
               event_remaining, max_stake)
    size = floor(size * 100) / 100
    return size if size >= min_stake else 0.0


def validate_asof(*, decision: datetime, starts_at: datetime,
                  information_at: datetime, quote_at: datetime,
                  max_quote_age_seconds: float = 120) -> None:
    """Validate pregame timing; information_at is max known-at of inputs."""
    for stamp in (decision, starts_at, information_at, quote_at):
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Timezone-aware timestamps required")
    if finite(max_quote_age_seconds) <= 0:
        raise ValueError("Invalid quote age limit")
    if information_at > decision or quote_at > decision:
        raise ValueError("Future information / look-ahead leakage")
    if decision >= starts_at:
        raise ValueError("Pregame market already started")
    if (decision - quote_at).total_seconds() > max_quote_age_seconds:
        raise ValueError("Stale quote")


def brier_score(predictions: list[float], outcomes: list[int]) -> float:
    """Binary resolved outcomes only; exclude pushes, voids and open events."""
    if not predictions or len(predictions) != len(outcomes):
        raise ValueError("Non-empty matching inputs required")
    if any(not 0 <= finite(p) <= 1 for p in predictions):
        raise ValueError("Invalid probability")
    if any(y not in (0, 1) for y in outcomes):
        raise ValueError("Only binary settled outcomes supported")
    return sum((p - y) ** 2 for p, y in zip(predictions, outcomes)) / len(outcomes)
