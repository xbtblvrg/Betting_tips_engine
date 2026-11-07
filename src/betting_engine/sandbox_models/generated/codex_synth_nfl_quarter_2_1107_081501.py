"""Compound-Gamma-Poisson model for NFL|quarter_2|home_total.

Status: this code has not been run or fitted, and no edge is claimed. With 0
settled wagers, nothing here is evidence of skill, and the supplied ROI
benchmarks are not used as targets. Validate out of sample against closing
lines before promotion.

Assumptions to check against the real interfaces:
  * BaseCandidateModel accepts the five constructor keywords used below.
  * DiscreteDistribution.from_scores accepts {(home, away): probability}, or
    failing that, a 2-D probability matrix.
  * home_expected and away_expected are quarter-2 point means, not full-game.
  * attack, defense and pace are multiplicative indices with league average
    1.0. A defense index above 1.0 means the team concedes more than average.

Model:
    S = sum_{i=1..N} V_i,  N | theta ~ Poisson(theta * lambda),
    E[theta] = 1,  Var(theta) = phi

With Poisson arrivals, scoreless drives thin out of the process, so only the
scoring-drive rate mu = E[S] / E[V | V > 0] and the mix of V > 0 affect the
distribution. Possession count and scoring rate per drive are not separately
identified. Pace therefore acts through points per drive, which moves the
TD/FG mix, while E[S] stays equal to the expected-points input.

    Var(S) / E[S] = E[V^2] / E[V] + phi * E[S]

Situational features move the mix but not the mean, so effects already priced
into home_expected are not counted twice in E[S].

The values in DEFAULT_PRIORS are approximate structural priors, not fitted
estimates. Pass league data to calibrate() to shrink the TD share and phi
toward observed values before relying on the probabilities.
"""

import math
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # DiscreteDistribution is expected to be pre-loaded in the execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


DEFAULT_PRIORS: Dict[str, float] = {
    "baseline_home_mean": 6.12,
    "baseline_away_mean": 5.38,
    # Share of quarter-2 scoring drives ending in a touchdown.
    "td_share": 0.55,
    # Points on a touchdown drive: missed or no conversion, extra point, two-point try.
    "td_value_6": 0.06,
    "td_value_7": 0.90,
    "td_value_8": 0.04,
    # Var(theta): residual game-level variation in the scoring rate.
    "phi": 0.10,
    # TD-share log-odds sensitivities.
    "beta_efficiency": 0.80,
    "beta_exposure": 0.25,
    "beta_rest": 0.10,
    "beta_wind_per_mph": 0.005,
    # Pseudo-counts for shrinkage in calibrate().
    "phi_prior_games": 200.0,
    "td_share_prior_drives": 200.0,
}

# Numerical truncation only; remaining mass is renormalized.
_TAIL_TOLERANCE = 1e-12


def _finite_positive(value: Any) -> Optional[float]:
    """Return value as a finite positive float, otherwise None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _lookup(context: Mapping, *keys: str) -> Any:
    """Find the first non-None key at the top level, then weather, then venue."""
    scopes = [context]
    for nested in ("weather", "venue"):
        candidate = context.get(nested)
        if isinstance(candidate, Mapping):
            scopes.append(candidate)
    for scope in scopes:
        for key in keys:
            if scope.get(key) is not None:
                return scope[key]
    return None


def _team_index(context: Mapping, side: str, name: str) -> float:
    """Read an index from context[side][name] or context[f"{side}_{name}"]."""
    block = context.get(side)
    raw = block.get(name) if isinstance(block, Mapping) else None
    if raw is None:
        raw = context.get(f"{side}_{name}")
    index = _finite_positive(raw)
    return index if index is not None else 1.0


class NFLQuarter2HomeTotalCompoundModel(BaseCandidateModel):
    """Lattice distribution of quarter-2 points from drive-level scoring."""

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_2_1107_081501",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_2",
        family: str = "home_total",
        priors: Optional[Dict[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.priors = {**DEFAULT_PRIORS, **(priors or {})}

    def calibrate(
        self,
        home_scores: Optional[Sequence[float]] = None,
        td_count: int = 0,
        fg_count: int = 0,
    ) -> None:
        """Shrink the TD share and phi toward league quarter-2 data.

        Update the mix first, because the phi estimate depends on it:
            phi_hat = (Var(S) - E[S] * E[V^2] / E[V]) / E[S]^2
        """
        p = self.priors

        prior_drives = p["td_share_prior_drives"]
        p["td_share"] = (prior_drives * p["td_share"] + td_count) / (
            prior_drives + td_count + fg_count
        )

        if home_scores is None or len(home_scores) < 2:
            return
        scores = np.asarray(home_scores, dtype=float)
        mean = float(scores.mean())
        if mean <= 0.0:
            return

        values, weights = self._scoring_mix(0.0)
        compound_index = float(np.dot(weights, values**2) / np.dot(weights, values))
        phi_hat = (float(scores.var(ddof=1)) - mean * compound_index) / mean**2

        prior_games = p["phi_prior_games"]
        games = float(scores.size)
        shrunk_phi = (prior_games * p["phi"] + games * phi_hat) / (prior_games + games)
        # A variance cannot be negative; phi = 0 is the compound Poisson limit.
        p["phi"] = max(shrunk_phi, 0.0)

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        home_pmf = self.team_pmf(game_context, "home")
        away_pmf = self.team_pmf(game_context, "away")

        # Independence is a simplification. It leaves the home_total marginal
        # unchanged, but this joint should not be used to price spreads.
        joint = np.outer(home_pmf, away_pmf)
        scores = {
            (int(home), int(away)): float(joint[home, away])
            for home, away in zip(*np.nonzero(joint))
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(joint)

    def team_pmf(self, context: Mapping, side: str) -> np.ndarray:
        """Return P(S = s) for s = 0, 1, ... for the "home" or "away" side."""
        p = self.priors
        baseline = p[f"baseline_{side}_mean"]
        expected = _finite_positive(context.get(f"{side}_expected"))
        mean = expected if expected is not None else baseline

        shift = self._mix_shift(context, side, mean, baseline)
        values, weights = self._scoring_mix(shift)
        return self._panjer_pmf(mean, values, weights, p["phi"])

    def _mix_shift(self, context: Mapping, side: str, mean: float, baseline: float) -> float:
        """TD-share log-odds shift; moves the mix and leaves the mean fixed."""
        p = self.priors
        opponent = "away" if side == "home" else "home"

        # Relative points per drive: (mean / baseline) / relative possessions.
        efficiency = math.log(mean / baseline) - math.log(self._pace_index(context))

        exposure = math.log(_team_index(context, side, "attack")) + math.log(
            _team_index(context, opponent, "defense")
        )

        rest = 0.0
        own_rest = context.get(f"{side}_rest_days")
        other_rest = context.get(f"{opponent}_rest_days")
        if isinstance(own_rest, (int, float)) and isinstance(other_rest, (int, float)):
            if own_rest >= 0 and other_rest >= 0:
                # Log ratio gives each extra rest day a diminishing effect.
                rest = math.log((1.0 + own_rest) / (1.0 + other_rest))

        wind = 0.0
        if not _lookup(context, "is_dome"):
            wind = _finite_positive(_lookup(context, "wind_speed")) or 0.0

        return (
            p["beta_efficiency"] * efficiency
            + p["beta_exposure"] * exposure
            + p["beta_rest"] * rest
            + p["beta_wind_per_mph"] * wind
        )

    @staticmethod
    def _pace_index(context: Mapping) -> float:
        """Average the team pace indices, or use a game-level index if absent."""
        team_paces: List[float] = []
        for key in ("home_pace", "away_pace", "home_tempo", "away_tempo"):
            pace = _finite_positive(context.get(key))
            if pace is not None:
                team_paces.append(pace)
        if team_paces:
            return float(np.mean(team_paces))
        game_pace = _finite_positive(_lookup(context, "pace", "tempo"))
        return game_pace if game_pace is not None else 1.0

    def _scoring_mix(self, log_odds_shift: float) -> "tuple[np.ndarray, np.ndarray]":
        """Return the values and probabilities of V given V > 0."""
        p = self.priors
        base_log_odds = math.log(p["td_share"] / (1.0 - p["td_share"]))
        td_share = 1.0 / (1.0 + math.exp(-(base_log_odds + log_odds_shift)))

        conversion = np.array([p["td_value_6"], p["td_value_7"], p["td_value_8"]], dtype=float)
        conversion /= conversion.sum()

        values = np.array([3.0, 6.0, 7.0, 8.0])
        weights = np.concatenate(([1.0 - td_share], td_share * conversion))
        return values, weights

    @staticmethod
    def _panjer_pmf(
        mean: float, values: np.ndarray, weights: np.ndarray, phi: float
    ) -> np.ndarray:
        """Compute the compound Negative Binomial pmf with Panjer recursion.

        With mu = mean / E[V], the (a, b, 0) coefficients are
            a = mu * phi / (1 + mu * phi)
            b = mu * (1 - phi) / (1 + mu * phi)
        and phi = 0 reduces exactly to the compound Poisson case.
        """
        severity_mean = float(np.dot(weights, values))
        severity_second = float(np.dot(weights, values**2))
        mu = mean / severity_mean

        denominator = 1.0 + mu * phi
        a = mu * phi / denominator
        b = mu * (1.0 - phi) / denominator
        zero_mass = math.exp(-mu) if phi == 0.0 else math.exp(-math.log1p(mu * phi) / phi)

        variance = mean * severity_second / severity_mean + phi * mean**2
        support_limit = int(math.ceil(mean + 40.0 * math.sqrt(variance))) + int(values.max())

        int_values = values.astype(int)
        pmf = [zero_mass]
        cumulative = zero_mass
        for score in range(1, support_limit + 1):
            mass = 0.0
            for value, weight in zip(int_values, weights):
                if value <= score:
                    mass += (a + b * value / score) * weight * pmf[score - value]
            pmf.append(mass)
            cumulative += mass
            if cumulative >= 1.0 - _TAIL_TOLERANCE:
                break

        result = np.asarray(pmf, dtype=float)
        return result / result.sum()