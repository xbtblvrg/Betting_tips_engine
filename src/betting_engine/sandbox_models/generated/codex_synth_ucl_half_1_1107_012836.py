"""Cold-start candidate model for UCL | half_1 | away_total.

Structure
---------
Home goals follow H ~ Poisson(h). Away goals have rate a*Z before the first home
goal and a*Z*exp(kappa) after it, with Z ~ Gamma(1/phi, phi) and E[Z] = 1.

With tau = min(first home goal time, 1), and time scaled so the half has length 1:

    E[tau] = m = (1 - exp(-h)) / h
    E[N_A] = a * (m + exp(kappa) * (1 - m))

E[N_A] is linear in a, so a has a closed form and needs no numerical root solve.

Given H = k >= 1, tau has density k * (1 - t)^(k - 1) on [0, 1]. Given tau, Z
integrates out analytically, so N_A is negative binomial with mean
a * (tau + exp(kappa) * (1 - tau)). Only the tau integral needs quadrature.
Column zero of the score matrix is the integrated form of:

    P(N_A=0 | Z) = exp(-(aZ+h))
                   + h / (h + aZ(1-exp(kappa)))
                     * (exp(-aZ*exp(kappa)) - exp(-(aZ+h)))

Limitations
-----------
* This code has not been executed or backtested, and its edge is an untested
  hypothesis. Validate it on out-of-sample log-loss and closing-line value at
  the 0.5 and 1.5 lines before staking.
* The supplied ROI and Brier benchmarks were not used and no claim is made of
  matching them. The reported failure on this segment can't be diagnosed from a
  sample of 0 settled wagers.
* The values in `Priors` are domain judgements, not estimates. They should be
  replaced through `fit()`, which needs first-half results that aren't available
  here.
* Multipliers are neutral at reference inputs (rating 1, equal rest, no tie
  pressure). They are not centred on a population mean because no population
  sample is available, so the 0.60 and 0.80 anchors hold only at those inputs.

Interface assumptions (to confirm against the engine)
-----------------------------------------------------
* `home_expected` and `away_expected` are first-half means, are independent of
  bookmaker prices, already include team strength, and exclude pace, rest and
  tie state. Attack and defense ratings are used only when the matching expected
  value is missing, which avoids double counting.
* Attack and defense ratings are multiplicative with 1 = league average, and a
  defense rating above 1 means conceding more. Pace is relative to 1 unless
  `league_pace` is supplied.
* `leg` (1 or 2) and `away_aggregate_diff` (positive when the away team leads)
  carry tie state. If they are missing, tie state has no effect.
* `DiscreteDistribution` is importable from the base module or already in the
  execution scope. `from_scores` accepts {(home, away): p} or a probability
  matrix.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import numpy as np
from scipy import optimize, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


@dataclass(frozen=True)
class Priors:
    """Elicited starting values and prior widths; none are estimated from data."""

    home_anchor: float = 0.80
    away_anchor: float = 0.60
    kappa: float = 0.12  # log rate shift for the away team after the first home goal
    phi: float = 0.12  # residual between-match variance of Z
    kappa_sd: float = 0.25  # prior SD used by fit()
    log_phi_sd: float = 0.75  # prior SD used by fit()
    rest_elasticity: float = 0.03  # effect of the log rest ratio
    first_leg_effect: float = -0.05  # away log rate shift in a first leg
    aggregate_effect: float = 0.10  # away log rate shift at full aggregate pressure
    quadrature_nodes: int = 48  # numerical accuracy only
    tail_tolerance: float = 1e-10  # numerical accuracy only


def _number(context: Dict[str, Any], *keys: str) -> Optional[float]:
    """Return the first finite numeric value found under `keys`."""
    for key in keys:
        value = context.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _positive(context: Dict[str, Any], *keys: str) -> Optional[float]:
    number = _number(context, *keys)
    return number if number is not None and number > 0.0 else None


def _rating(context: Dict[str, Any], side: str, name: str) -> float:
    """Read a rating from context[side][name] or context['<side>_<name>']."""
    nested = context.get(side)
    if isinstance(nested, dict):
        value = _positive(nested, name)
        if value is not None:
            return value
    value = _positive(context, f"{side}_{name}")
    return value if value is not None else 1.0


def _count_pmf(counts: np.ndarray, means: np.ndarray, phi: float) -> np.ndarray:
    """Evaluate the Gamma-Poisson pmf, with shape (len(means), len(counts))."""
    means = np.atleast_1d(means)[:, None]
    if phi <= 0.0:
        return stats.poisson.pmf(counts[None, :], means)
    size = 1.0 / phi
    return stats.nbinom.pmf(counts[None, :], size, size / (size + means))


class UclHalf1AwayTotalStateSwitchModel(BaseCandidateModel):
    """Away first-half goals with a home-goal state switch and a Gamma frailty."""

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_half_1_1107_012836",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "half_1",
        family: str = "away_total",
        priors: Priors = Priors(),
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.priors = priors
        self.kappa = priors.kappa
        self.phi = priors.phi
        nodes, weights = np.polynomial.legendre.leggauss(priors.quadrature_nodes)
        self._nodes = 0.5 * (nodes + 1.0)
        self._weights = 0.5 * weights

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        home_mean, away_mean = self._expected_means(game_context)
        matrix = self._score_matrix(home_mean, away_mean, self.kappa, self.phi)
        scores = {
            (home, away): float(matrix[home, away])
            for home in range(matrix.shape[0])
            for away in range(matrix.shape[1])
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(matrix)

    def fit(self, matches: Iterable[Dict[str, Any]]) -> "UclHalf1AwayTotalStateSwitchModel":
        """Estimate kappa and phi by MAP, partially pooled toward the priors.

        Each match needs `home_goals` and `away_goals` for the first half, plus
        features in `context` or at the top level. An optional `weight` in (0, 1]
        tempers its likelihood, so comparable European competitions can inform
        UCL with less influence. This is a power-prior approximation, not a full
        multilevel posterior.
        """
        observations = []
        for match in matches:
            context = match.get("context", match)
            home_mean, away_mean = self._expected_means(context)
            observations.append(
                (
                    int(match["home_goals"]),
                    int(match["away_goals"]),
                    home_mean,
                    away_mean,
                    float(match.get("weight", 1.0)),
                )
            )
        if not observations:
            return self

        priors = self.priors

        def negative_log_posterior(theta: np.ndarray) -> float:
            log_phi, kappa = float(theta[0]), float(theta[1])
            phi = math.exp(log_phi)
            total = 0.5 * ((log_phi - math.log(priors.phi)) / priors.log_phi_sd) ** 2
            total += 0.5 * ((kappa - priors.kappa) / priors.kappa_sd) ** 2
            for home_goals, away_goals, home_mean, away_mean, weight in observations:
                base_rate = self._base_rate(home_mean, away_mean, kappa)
                pmf = self._away_pmf_given_home(
                    home_goals, np.array([away_goals]), base_rate, kappa, phi
                )
                # H does not depend on kappa or phi, so P(A | H) is sufficient.
                total -= weight * math.log(max(float(pmf[0]), np.finfo(float).tiny))
            return total

        start = np.array([math.log(self.phi), self.kappa])
        result = optimize.minimize(negative_log_posterior, start, method="Nelder-Mead")
        if not result.success:
            raise RuntimeError(f"Hyperparameter fit did not converge: {result.message}")
        self.phi = math.exp(float(result.x[0]))
        self.kappa = float(result.x[1])
        return self

    def _expected_means(self, context: Dict[str, Any]) -> tuple:
        """Return first-half (home, away) means after situational scaling."""
        priors = self.priors
        home_base = _positive(context, "home_expected")
        if home_base is None:
            home_base = (
                priors.home_anchor
                * _rating(context, "home", "attack")
                * _rating(context, "away", "defense")
            )
        away_base = _positive(context, "away_expected")
        if away_base is None:
            away_base = (
                priors.away_anchor
                * _rating(context, "away", "attack")
                * _rating(context, "home", "defense")
            )

        pace = _positive(context, "pace", "tempo")
        league_pace = _positive(context, "league_pace")
        if pace is None:
            pace_multiplier = 1.0
        else:
            pace_multiplier = pace / league_pace if league_pace is not None else pace

        rest_shift = self._rest_log_shift(context)
        tie_shift = self._tie_log_shift(context)
        home_mean = home_base * pace_multiplier * math.exp(-rest_shift)
        away_mean = away_base * pace_multiplier * math.exp(rest_shift + tie_shift)
        return home_mean, away_mean

    def _rest_log_shift(self, context: Dict[str, Any]) -> float:
        """Return a symmetric away rest effect with diminishing returns."""
        home_rest = _number(context, "home_rest_days")
        away_rest = _number(context, "away_rest_days")
        if home_rest is None or away_rest is None or home_rest < 0.0 or away_rest < 0.0:
            return 0.0
        return self.priors.rest_elasticity * (math.log1p(away_rest) - math.log1p(home_rest))

    def _tie_log_shift(self, context: Dict[str, Any]) -> float:
        """Lower away risk in a first leg; scale it to aggregate state in a second leg."""
        leg = _number(context, "leg")
        if leg == 1.0:
            return self.priors.first_leg_effect
        if leg == 2.0:
            lead = _number(context, "away_aggregate_diff")
            if lead is not None:
                return -self.priors.aggregate_effect * lead / (1.0 + abs(lead))
        return 0.0

    @staticmethod
    def _base_rate(home_mean: float, away_mean: float, kappa: float) -> float:
        """Solve E[N_A] = away_mean for the pre-switch rate a."""
        time_before_switch = -math.expm1(-home_mean) / home_mean
        return away_mean / (time_before_switch + math.exp(kappa) * (1.0 - time_before_switch))

    def _away_pmf_given_home(
        self,
        home_goals: int,
        counts: np.ndarray,
        base_rate: float,
        kappa: float,
        phi: float,
    ) -> np.ndarray:
        """Return P(N_A = counts | H = home_goals), integrating out tau and Z."""
        if home_goals == 0:
            return _count_pmf(counts, np.array([base_rate]), phi)[0]
        tau = self._nodes
        exposure = base_rate * (tau + math.exp(kappa) * (1.0 - tau))
        tau_density = home_goals * (1.0 - tau) ** (home_goals - 1)
        return (self._weights * tau_density) @ _count_pmf(counts, exposure, phi)

    def _score_matrix(
        self, home_mean: float, away_mean: float, kappa: float, phi: float
    ) -> np.ndarray:
        """Build the joint first-half score matrix, indexed [home, away]."""
        tolerance = self.priors.tail_tolerance
        base_rate = self._base_rate(home_mean, away_mean, kappa)
        peak_rate = base_rate * max(1.0, math.exp(kappa))

        max_home = int(stats.poisson.isf(tolerance, home_mean)) + 1
        if phi > 0.0:
            size = 1.0 / phi
            max_away = int(stats.nbinom.isf(tolerance, size, size / (size + peak_rate))) + 1
        else:
            max_away = int(stats.poisson.isf(tolerance, peak_rate)) + 1

        away_counts = np.arange(max_away + 1)
        home_pmf = stats.poisson.pmf(np.arange(max_home + 1), home_mean)
        matrix = np.vstack(
            [
                home_pmf[home_goals]
                * self._away_pmf_given_home(home_goals, away_counts, base_rate, kappa, phi)
                for home_goals in range(max_home + 1)
            ]
        )
        return matrix / matrix.sum()