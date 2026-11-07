"""Candidate model for NBA|quarter_2|home_total: a hierarchical compound possession sum.

Status: UNFITTED and UNVALIDATED. This code has not been run against betting_engine.
With 0 settled wagers, no leak has been observed, and nothing here shows the model
can reach the supplied ROI or Brier benchmarks. Treat ROI figures of that size as
possible leakage or small-sample artifacts until they are reproduced out of sample.

"Zero hardcoded constants" is not achievable without data. The structural priors in
`Q2Priors` are rough domain estimates that should be replaced with play-by-play fits.
They are kept in one injectable object and do not act as probability or odds clamps.

Structure, conditional on the quarter's latent state:
    S_H = sum_{i=1..N} X_i, with X_i in {0, 1, 2, 3}
    N   ~ discretized Normal(E[N], Var[N]), underdispersed because the clock bounds it
    P(X = k | eta) proportional to base_k * exp(eta * k)
    eta = eta_0 + sd * (rho * z_N + sqrt(1 - rho^2) * z), with z ~ Normal(0, 1)

eta_0 is solved numerically so that E[S_H] equals the situational target exactly.
Both teams share N, and their efficiencies are independent given N. The +/-1
possession difference between teams is not modeled.

Implied variance:
    Var(S) = E[N] E[Var(X|eta)] + E[X]^2 Var(N) + E[N]^2 Var(E[X|eta])
             + 2 E[N] E[X] Cov(N, E[X|eta])
At the default priors this is roughly 43, or a dispersion index near 1.5 and a
standard deviation near 6.6 points. The index is derived, not fixed at 1.000.

Not modeled explicitly: bench stagger, foul-bonus accumulation and two-for-one
possessions. They enter only through Var(N), the latent efficiency variance and the
base categorical, so those parameters should be fitted to Q2 data specifically.

Assumptions to verify against the engine:
  * `DiscreteDistribution.from_scores` accepts a 2D array (rows = home points,
    columns = away points). If it raises, a {(home, away): probability} dict is used.
    A format that is accepted but read differently would not be detected.
  * Attack and defense ratings are multiplicative indices centered on 1.0, where a
    higher defense value means conceding more. Other scales are ignored as neutral.
  * Rest days count full off days, so 0 means a back-to-back.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import softmax

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # DiscreteDistribution is pre-loaded in the execution scope.


@dataclass(frozen=True)
class Q2Priors:
    """Unfitted structural priors. Replace them with play-by-play estimates."""

    home_mean: float = 28.0
    away_mean: float = 27.0
    possessions_per_quarter: float = 25.0
    quarter_share: float = 0.25

    # Per-possession points categorical for k = 0, 1, 2, 3 before tilting.
    base_points_probs: Tuple[float, float, float, float] = (0.47, 0.04, 0.35, 0.14)

    # Var(N) / E[N]; below 1 because a timed quarter limits possession variation.
    possession_var_to_mean: float = 0.16
    # Standard deviation of the latent quarter-level efficiency tilt.
    efficiency_tilt_sd: float = 0.06
    # Correlation between possession count and latent efficiency.
    pace_efficiency_corr: float = 0.10
    # Share of a total-score deviation attributed to pace when pace is not supplied.
    implied_pace_elasticity: float = 0.5

    # Weight on ratings and rest when an expected score is supplied, because that
    # expectation may already include them. Set to 0 if it definitely does.
    anchored_feature_weight: float = 0.25
    rating_index_bound: float = 4.0

    # Log effects per unit of the saturating rest-recovery curve.
    rest_time_constant_days: float = 1.0
    reference_rest_days: float = 1.0
    rest_offense_effect: float = 0.02
    rest_defense_effect: float = 0.01
    rest_pace_effect: float = 0.004

    # Numerical settings.
    quadrature_nodes: int = 15
    possession_grid_sds: float = 6.0
    tilt_search_bound: float = 25.0
    tail_tolerance: float = 1e-12


def _finite(value: Any) -> Optional[float]:
    """Return value as a finite float, or None if it is unusable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> Optional[float]:
    number = _finite(value)
    return number if number is not None and number > 0.0 else None


class NBAQuarter2HomeTotalModel(BaseCandidateModel):
    """Prices Q2 home points from a coupled compound possession model."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1107_001803",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_2",
        family: str = "home_total",
        priors: Optional[Q2Priors] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.priors = priors or Q2Priors()
        nodes, weights = np.polynomial.hermite_e.hermegauss(
            self.priors.quadrature_nodes
        )
        self._z_nodes = nodes
        self._z_weights = weights / weights.sum()
        base = np.asarray(self.priors.base_points_probs, dtype=float)
        self._log_base = np.log(base / base.sum())
        self._points = np.arange(base.size, dtype=float)

    # ------------------------------------------------------------------ inputs

    def _to_quarter_scale(self, value: float, quarter_prior: float) -> float:
        """Convert a value that is clearly full-game scale to a single quarter."""
        return value * self.priors.quarter_share if value > 2.0 * quarter_prior else value

    def _rating(self, ctx: Mapping[str, Any], side: str, name: str) -> float:
        nested = ctx.get(side)
        candidates = [ctx.get(f"{side}_{name}"), ctx.get(f"{side}_{name}_strength")]
        if isinstance(nested, Mapping):
            candidates.append(nested.get(name))
        bound = self.priors.rating_index_bound
        for candidate in candidates:
            rating = _positive(candidate)
            if rating is not None and 1.0 / bound < rating < bound:
                return rating
        return 1.0

    def _recovery(self, rest_days: Optional[float]) -> float:
        p = self.priors
        days = p.reference_rest_days if rest_days is None else max(rest_days, 0.0)
        return 1.0 - math.exp(-days / p.rest_time_constant_days)

    def _target_means(
        self, ctx: Mapping[str, Any], home_recovery: float, away_recovery: float
    ) -> Tuple[float, float]:
        p = self.priors
        reference = self._recovery(None)

        def target(side: str, opponent: str, prior: float, own: float, opp: float) -> float:
            anchor = _positive(ctx.get(f"{side}_expected"))
            log_effect = (
                math.log(self._rating(ctx, side, "attack"))
                + math.log(self._rating(ctx, opponent, "defense"))
                + p.rest_offense_effect * (own - reference)
                - p.rest_defense_effect * (opp - reference)
            )
            if anchor is None:
                return prior * math.exp(log_effect)
            scaled = self._to_quarter_scale(anchor, prior)
            return scaled * math.exp(p.anchored_feature_weight * log_effect)

        return (
            target("home", "away", p.home_mean, home_recovery, away_recovery),
            target("away", "home", p.away_mean, away_recovery, home_recovery),
        )

    def _expected_possessions(
        self,
        ctx: Mapping[str, Any],
        home_mean: float,
        away_mean: float,
        home_recovery: float,
        away_recovery: float,
    ) -> float:
        p = self.priors
        pace = None
        for key in ("pace", "tempo", "expected_pace", "possessions"):
            pace = _positive(ctx.get(key))
            if pace is not None:
                break
        if pace is None:
            side_paces = [_positive(ctx.get("home_pace")), _positive(ctx.get("away_pace"))]
            known = [value for value in side_paces if value is not None]
            pace = sum(known) / len(known) if known else None

        if pace is not None:
            possessions = self._to_quarter_scale(pace, p.possessions_per_quarter)
        else:
            total_ratio = (home_mean + away_mean) / (p.home_mean + p.away_mean)
            possessions = p.possessions_per_quarter * total_ratio ** p.implied_pace_elasticity

        mean_recovery = 0.5 * (home_recovery + away_recovery)
        return possessions * math.exp(
            p.rest_pace_effect * (mean_recovery - self._recovery(None))
        )

    # --------------------------------------------------------------- mechanics

    def _point_probs(self, eta: np.ndarray) -> np.ndarray:
        return softmax(self._log_base + eta[..., None] * self._points, axis=-1)

    def _conditional_pmfs(
        self,
        target_mean: float,
        counts: np.ndarray,
        count_probs: np.ndarray,
        count_z: np.ndarray,
        lattice_size: int,
    ) -> np.ndarray:
        """Return P(S = s | N = n), with one row per n, from the exact convolution."""
        p = self.priors
        expected_count = float(count_probs @ counts)
        if not 0.0 < target_mean < self._points[-1] * expected_count:
            raise ValueError(
                f"Target mean {target_mean:.3f} cannot be reached with "
                f"{expected_count:.3f} expected possessions."
            )

        rho = p.pace_efficiency_corr
        shift = p.efficiency_tilt_sd * (
            rho * count_z[:, None]
            + math.sqrt(1.0 - rho * rho) * self._z_nodes[None, :]
        )

        def mean_gap(eta_0: float) -> float:
            means = self._point_probs(eta_0 + shift) @ self._points
            return float(count_probs @ (counts * (means @ self._z_weights))) - target_mean

        eta_0 = brentq(
            mean_gap, -p.tilt_search_bound, p.tilt_search_bound, xtol=1e-12, rtol=1e-12
        )

        # The lattice covers the maximum possible score, so the FFT cannot alias.
        transform = np.fft.rfft(self._point_probs(eta_0 + shift), n=lattice_size, axis=-1)
        powered = transform ** counts[:, None, None]
        mixed = (powered * self._z_weights[None, :, None]).sum(axis=1)
        pmfs = np.clip(np.fft.irfft(mixed, n=lattice_size, axis=-1), 0.0, None)
        return pmfs / pmfs.sum(axis=1, keepdims=True)

    def _joint_score_matrix(self, ctx: Mapping[str, Any]) -> np.ndarray:
        """Return the joint Q2 score PMF, with rows = home and columns = away."""
        p = self.priors
        home_recovery = self._recovery(_finite(ctx.get("home_rest_days")))
        away_recovery = self._recovery(_finite(ctx.get("away_rest_days")))
        home_mean, away_mean = self._target_means(ctx, home_recovery, away_recovery)
        possessions = self._expected_possessions(
            ctx, home_mean, away_mean, home_recovery, away_recovery
        )

        count_sd = math.sqrt(p.possession_var_to_mean * possessions)
        low = max(1, math.floor(possessions - p.possession_grid_sds * count_sd))
        high = math.ceil(possessions + p.possession_grid_sds * count_sd)
        counts = np.arange(low, high + 1, dtype=float)
        count_z = (counts - possessions) / count_sd
        count_probs = np.exp(-0.5 * count_z * count_z)
        count_probs /= count_probs.sum()

        lattice_size = int(self._points[-1]) * high + 1
        home = self._conditional_pmfs(home_mean, counts, count_probs, count_z, lattice_size)
        away = self._conditional_pmfs(away_mean, counts, count_probs, count_z, lattice_size)
        joint = np.einsum("n,ni,nj->ij", count_probs, home, away, optimize=True)

        # Remove only numerically negligible tail cells to keep the output compact.
        home_end = int(np.searchsorted(np.cumsum(joint.sum(axis=1)), 1.0 - p.tail_tolerance)) + 1
        away_end = int(np.searchsorted(np.cumsum(joint.sum(axis=0)), 1.0 - p.tail_tolerance)) + 1
        trimmed = joint[:home_end, :away_end]
        return trimmed / trimmed.sum()

    # ---------------------------------------------------------------- public API

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self._joint_score_matrix(game_context)
        try:
            return DiscreteDistribution.from_scores(joint)
        except (TypeError, ValueError, AttributeError, KeyError):
            scores = {
                (int(home), int(away)): float(joint[home, away])
                for home, away in zip(*np.nonzero(joint))
            }
            return DiscreteDistribution.from_scores(scores)

    def home_total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        """Return the marginal PMF of Q2 home points, indexed by points scored."""
        return self._joint_score_matrix(game_context).sum(axis=1)

    def over_under_probabilities(
        self, game_context: Dict[str, Any], line: float
    ) -> Dict[str, float]:
        """Return over, push and under probabilities for a home-total line."""
        pmf = self.home_total_pmf(game_context)
        points = np.arange(pmf.size, dtype=float)
        return {
            "over": float(pmf[points > line].sum()),
            "push": float(pmf[points == line].sum()),
            "under": float(pmf[points < line].sum()),
        }

    def dispersion_index(self, game_context: Dict[str, Any]) -> float:
        """Return the model-implied Var/Mean for out-of-sample calibration checks."""
        pmf = self.home_total_pmf(game_context)
        points = np.arange(pmf.size, dtype=float)
        mean = float(pmf @ points)
        return float(pmf @ (points - mean) ** 2) / mean