"""
Candidate model for NBA | half_1 | home_total.

The model treats first-half home points as a compound process: a random number
of possessions, each scoring a random number of points. It produces the exact
discrete joint score distribution through characteristic-function (FFT)
convolution. It does not use a Gaussian or plain Poisson approximation.

On the 0.187 dispersion index:
    A Var/Mean of 0.187 around a mean of 56 implies a standard deviation of
    about 3.2 points per half. That is physically too small. For N ~ 50
    possessions and points-per-possession variance around 1, the compound
    variance is at least N * Var(PPP), roughly 50 or more, so Var/Mean is
    about 1. The reported index is therefore treated as noisy evidence that
    feeds an empirical-Bayes blend. It is never allowed to push the variance
    below the structural floor implied by the possession process. Making the
    tails thinner would deepen the over-side cash leak, not fix it.

No architecture can guarantee a particular out-of-sample ROI. The parameters
below are named, documented priors that can be refit with `fit()` on settled
history before the model is run through Crucible.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # Normally pre-loaded in the execution scope; imported explicitly for safety.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    pass


# --------------------------------------------------------------------------- #
# Priors (all named, all overridable through the constructor or fit())        #
# --------------------------------------------------------------------------- #
@dataclass
class NBAHalfPriors:
    # Empirical anchors for this segment (first-half points).
    home_mean: float = 56.00
    away_mean: float = 54.00

    # Possession structure: league pace in possessions per 48 minutes, per team.
    pace_full_game: float = 99.0
    # Between-game standard deviation of first-half possessions.
    pace_half_sd: float = 2.6

    # Shape of points scored on a possession, P(0..4 points), before tilting.
    # The model exponentially tilts this shape (the minimum-KL adjustment) so
    # that it hits each game's required points per possession.
    ppp_shape: Tuple[float, ...] = (0.505, 0.045, 0.300, 0.140, 0.010)

    # Observed dispersion evidence and how much weight it receives.
    observed_dispersion_index: float = 0.187
    observed_sample_size: int = 500
    # Pseudo-sample size that expresses confidence in the structural model.
    structural_prior_strength: float = 500.0

    # Rest elasticity: proportional change in scoring efficiency per log-day
    # of rest advantage. Kept small because rest effects are second order.
    rest_efficiency_elasticity: float = 0.006

    # Numerical controls.
    fft_size: int = 512
    tail_mass: float = 1e-10
    n_sd_possessions: float = 6.0


class NBAHalf1HomeTotalCompoundModel(BaseCandidateModel):
    """Compound possession-by-efficiency model for first-half NBA home totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_105108",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        priors: Optional[NBAHalfPriors] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = priors or NBAHalfPriors()
        self._base_shape = self._normalise(np.asarray(self.priors.ppp_shape, float))
        self._points = np.arange(len(self._base_shape), dtype=float)
        self._target_dispersion = self._blend_dispersion()

    # ------------------------------------------------------------------ #
    # Calibration                                                        #
    # ------------------------------------------------------------------ #
    def fit(self, home_points: Iterable[float], away_points: Iterable[float]) -> "NBAHalf1HomeTotalCompoundModel":
        """Refit the anchors and dispersion evidence from settled first-half scores."""
        h = np.asarray(list(home_points), float)
        a = np.asarray(list(away_points), float)
        h = h[np.isfinite(h)]
        a = a[np.isfinite(a)]
        if h.size >= 2:
            self.priors.home_mean = float(h.mean())
            self.priors.observed_dispersion_index = float(h.var(ddof=1) / max(h.mean(), 1e-9))
            self.priors.observed_sample_size = int(h.size)
        if a.size >= 2:
            self.priors.away_mean = float(a.mean())
        self._target_dispersion = self._blend_dispersion()
        return self

    def _structural_dispersion(self, mean_points: float) -> float:
        n_mean = self.priors.pace_full_game / 2.0
        q = self._tilted_pmf(mean_points / n_mean)
        mu = float(q @ self._points)
        var_ppp = float(q @ self._points ** 2) - mu ** 2
        var_total = n_mean * var_ppp + mu ** 2 * self.priors.pace_half_sd ** 2
        return var_total / mean_points

    def _blend_dispersion(self) -> float:
        """Precision-weighted blend of observed and structural Var/Mean."""
        d_struct = self._structural_dispersion(self.priors.home_mean)
        n_obs = max(self.priors.observed_sample_size, 0)
        d_obs = self.priors.observed_dispersion_index
        if not (np.isfinite(d_obs) and d_obs > 0 and n_obs > 1):
            return d_struct
        w = n_obs / (n_obs + self.priors.structural_prior_strength)
        # Blend in log space because a dispersion index is a scale parameter.
        return float(math.exp(w * math.log(d_obs) + (1.0 - w) * math.log(d_struct)))

    # ------------------------------------------------------------------ #
    # Feature extraction                                                 #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _side_rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(key))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{key}"))

    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.priors
        mh = self._num(ctx.get("home_expected"))
        ma = self._num(ctx.get("away_expected"))

        # Fall back to multiplicative attack/defence exposure around the anchors.
        if mh is None:
            atk = self._side_rating(ctx, "home", "attack") or 1.0
            dfn = self._side_rating(ctx, "away", "defense") or 1.0
            mh = p.home_mean * atk * dfn
        if ma is None:
            atk = self._side_rating(ctx, "away", "attack") or 1.0
            dfn = self._side_rating(ctx, "home", "defense") or 1.0
            ma = p.away_mean * atk * dfn

        # Inputs given on a full-game scale are rescaled to the half, using the
        # anchors themselves to identify the scale.
        anchor_sum = p.home_mean + p.away_mean
        if mh + ma > 1.5 * anchor_sum:
            mh, ma = mh / 2.0, ma / 2.0

        # Rest: log-day advantage acts on scoring efficiency, not on pace.
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            adv = math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0))
            e = p.rest_efficiency_elasticity
            mh *= math.exp(e * adv)
            ma *= math.exp(-e * adv)

        return max(mh, 1e-6), max(ma, 1e-6)

    def _possession_mean(self, ctx: Dict[str, Any]) -> float:
        pace = None
        for k in ("pace", "tempo", "expected_pace"):
            pace = self._num(ctx.get(k))
            if pace is not None:
                break
        if pace is None:
            ph = self._side_rating(ctx, "home", "pace")
            pa = self._side_rating(ctx, "away", "pace")
            if ph is not None and pa is not None:
                pace = 0.5 * (ph + pa)
        if pace is None:
            pace = self.priors.pace_full_game
        # Values above a half-game scale are treated as per-48 pace.
        return pace / 2.0 if pace > 0.75 * self.priors.pace_full_game else pace

    # ------------------------------------------------------------------ #
    # Core probability mechanics                                         #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalise(v: np.ndarray) -> np.ndarray:
        v = np.clip(np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0), 0.0, None)
        s = v.sum()
        return v / s if s > 0 else np.full_like(v, 1.0 / v.size)

    def _tilted_pmf(self, target_ppp: float) -> np.ndarray:
        """Exponentially tilt the PPP shape so that its mean equals target_ppp."""
        k = self._points
        lo_feasible, hi_feasible = k[0] + 1e-6, k[-1] - 1e-6
        target = min(max(target_ppp, lo_feasible), hi_feasible)
        logb = np.log(self._base_shape)

        def mean_gap(theta: float) -> float:
            w = logb + theta * k
            w = np.exp(w - w.max())
            return float((w @ k) / w.sum()) - target

        lo, hi = -1.0, 1.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        w = np.exp(logb + theta * k - (logb + theta * k).max())
        return w / w.sum()

    def _possession_pmf(self, n_mean: float, extra_var: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = math.sqrt(self.priors.pace_half_sd ** 2 + max(extra_var, 0.0))
        span = self.priors.n_sd_possessions * sd
        n = np.arange(max(1, int(math.floor(n_mean - span))), int(math.ceil(n_mean + span)) + 1)
        # Discretised normal over integer possession counts.
        cdf = stats.norm.cdf(np.append(n - 0.5, n[-1] + 0.5), n_mean, sd)
        return n, self._normalise(np.diff(cdf))

    def _conditional_pmfs(self, ppp_pmf: np.ndarray, n_vals: np.ndarray) -> np.ndarray:
        """Exact pmf of a sum of N i.i.d. PPP draws for each N, computed via FFT."""
        size = self.priors.fft_size
        phi = np.fft.rfft(ppp_pmf, n=size)
        rows = np.fft.irfft(phi[None, :] ** n_vals[:, None], n=size)
        return np.clip(rows, 0.0, None)

    def _build_joint(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        mh, ma = self._expected_points(ctx)
        n_mean = self._possession_mean(ctx)

        q_h = self._tilted_pmf(mh / n_mean)
        q_a = self._tilted_pmf(ma / n_mean)

        # Lift the variance to the calibrated target. The structural compound
        # variance is a physical floor, so only extra pace variance is added.
        mu_h = float(q_h @ self._points)
        var_ppp_h = float(q_h @ self._points ** 2) - mu_h ** 2
        var_struct = n_mean * var_ppp_h + mu_h ** 2 * self.priors.pace_half_sd ** 2
        var_target = self._target_dispersion * mh
        extra_var = (var_target - var_struct) / max(mu_h ** 2, 1e-9)

        n_vals, w_n = self._possession_pmf(n_mean, extra_var)
        cond_h = self._conditional_pmfs(q_h, n_vals)
        cond_a = self._conditional_pmfs(q_a, n_vals)

        # Shared possession count couples the two sides: positive covariance.
        marg_h = self._normalise(w_n @ cond_h)
        marg_a = self._normalise(w_n @ cond_a)
        idx_h = self._support(marg_h)
        idx_a = self._support(marg_a)

        joint = np.einsum("n,ni,nj->ij", w_n, cond_h[:, idx_h], cond_a[:, idx_a])
        joint = np.clip(np.nan_to_num(joint, nan=0.0), 0.0, None)
        total = joint.sum()
        if not total > 0:
            raise ValueError("degenerate joint distribution")
        return idx_h, idx_a, joint / total

    def _support(self, pmf: np.ndarray) -> np.ndarray:
        cdf = np.cumsum(pmf)
        eps = self.priors.tail_mass
        lo = int(np.searchsorted(cdf, eps))
        hi = int(np.searchsorted(cdf, 1.0 - eps)) + 1
        return np.arange(lo, min(hi + 1, pmf.size))

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        idx_h, idx_a, joint = self._build_joint(ctx)

        scores: Dict[Tuple[int, int], float] = {}
        nz_i, nz_j = np.nonzero(joint > 0.0)
        for i, j in zip(nz_i.tolist(), nz_j.tolist()):
            scores[(int(idx_h[i]), int(idx_a[j]))] = float(joint[i, j])

        s = sum(scores.values())
        scores = {k: v / s for k, v in scores.items()}
        return DiscreteDistribution.from_scores(scores)

    def home_total_probabilities(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Over/under/push probabilities for a home first-half total line."""
        idx_h, _, joint = self._build_joint(game_context or {})
        marg = joint.sum(axis=1)
        over = float(marg[idx_h > line].sum())
        push = float(marg[idx_h == line].sum())
        under = max(0.0, 1.0 - over - push)
        return {"over": over, "under": under, "push": push}

    def expected_value(self, game_context: Dict[str, Any], line: float, side: str, decimal_odds: float) -> float:
        """Expected value per unit staked; pushes return the stake."""
        p = self.home_total_probabilities(game_context, line)
        win = p["over"] if side == "over" else p["under"]
        lose = 1.0 - win - p["push"]
        return win * (decimal_odds - 1.0) - lose


CandidateModel = NBAHalf1HomeTotalCompoundModel