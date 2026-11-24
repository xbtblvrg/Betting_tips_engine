"""
Candidate model for NBA | half_1 | home_total.

How it is built
---------------
Home first-half points are modeled as a sum over possessions.

    P_home = sum_{i=1..N} X_i

* N is the number of first-half possessions. Both teams share it, which is
  how game tempo enters.
* X_i is the points scored on one possession, with support {0, 1, 2, 3, 4}.

The model layers three sources of variance:

1. Within-game scoring noise. N is held fixed and X_i is i.i.d. The exact
   PMF is X^{*N}, computed by FFT convolution.
2. Shared pace uncertainty. N is drawn from a Gaussian prior and
   integrated by Gauss-Hermite quadrature. Both teams use the same node,
   which gives a structural, positive home/away covariance.
3. Uncertainty in the mean, i.e. how wrong the forecast `home_expected`
   is. The observed dispersion index sets its prior:
       sigma_mu^2 = D * mu,   with D = 0.187.
   D = 0.187 is far below the Var/Mean ~ 1.1-1.4 that a possession compound
   produces for actual half-time scores. So D is read as the dispersion of
   the expected-value signal, not of the outcomes. Using it directly as
   outcome variance would make the model badly overconfident. That is the
   tail-mispricing failure listed in the deficits.

Fitting the per-possession PMF to a target points-per-possession uses the
minimum-KL exponential tilt of a league-shape prior:

    p_k(theta) ∝ q_k * exp(theta * k)

This keeps the empirical possession-outcome shape (misses, free throws,
twos, threes, and-ones) while matching the required mean exactly.

Every structural prior is a named, overridable parameter. `calibrate()`
re-estimates the following from settled historical games by method of
moments, shrunk toward the priors:

* the mean-bias multiplier (the source of the under-side PnL asymmetry),
* the pace variance (from the home/away residual covariance),
* the efficiency variance.

There are no bookmaker inputs and no odds caps.

Honest note: the "benchmark" ROIs in the brief (e.g. +122%) are not a
credible target for a single totals market. The goal here is calibrated
probabilities. Any edge must be verified out-of-sample.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    pass


class NBAHalf1HomeTotalCompoundModel(BaseCandidateModel):
    """Possession-compound model for NBA first-half home team points."""

    # ---- empirical anchors for this segment (from the forensic autopsy) ----
    EMPIRICAL_HOME_MEAN = 56.00
    EMPIRICAL_AWAY_MEAN = 54.00
    EMPIRICAL_DISPERSION_INDEX = 0.187

    # ---- domain priors (named, overridable, re-estimable via calibrate) ----
    # Shape of NBA points-per-possession outcomes over {0, 1, 2, 3, 4}.
    POSSESSION_SHAPE_PRIOR = (0.47, 0.05, 0.34, 0.13, 0.01)
    # Relative SD of first-half possessions (about 2.7 of ~48 possessions).
    PACE_REL_SD_PRIOR = 0.055
    # Rest elasticity of scoring rate, per unit of log(1 + rest days).
    REST_ELASTICITY_PRIOR = 0.010
    # Weight on attack/defense ratings beyond what home_expected already encodes.
    RATING_RESIDUAL_WEIGHT = 0.25
    # Pseudo-sample size for shrinking calibrated moments toward priors.
    SHRINK_PSEUDO_N = 200.0

    # ---- numerical controls ----
    PACE_NODES = 9
    EFF_NODES = 7
    TAIL_EPS = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_full_game_1123_235145",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        **overrides: Any,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        # Apply any overrides of the named priors.
        for key, val in overrides.items():
            if hasattr(self, key.upper()):
                setattr(self, key.upper(), val)

        q = np.asarray(self.POSSESSION_SHAPE_PRIOR, dtype=float)
        self._q = q / q.sum()
        self._k = np.arange(self._q.size, dtype=float)
        self._ppp_prior = float(self._q @ self._k)

        # Calibratable state.
        self.bias_mult = 1.0
        self.pace_rel_sd = float(self.PACE_REL_SD_PRIOR)
        # None means use the dispersion-index prior D * mu.
        self.eff_var_ratio: Optional[float] = None

        self._gh_pace = np.polynomial.hermite_e.hermegauss(self.PACE_NODES)
        self._gh_eff = np.polynomial.hermite_e.hermegauss(self.EFF_NODES)

    # ------------------------------------------------------------------ #
    # feature handling
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        val = None
        if isinstance(block, dict):
            val = self._num(block.get(key))
        if val is None:
            val = self._num(ctx.get(f"{side}_{key}"))
        return val

    @staticmethod
    def _log_strength(v: Optional[float]) -> float:
        """Map a rating to log-multiplicative scale.

        Positive ratings centred near 1 are treated as multiplicative.
        Anything else is treated as already additive on the log scale.
        """
        if v is None:
            return 0.0
        if v > 0.0 and 0.2 < v < 5.0:
            return math.log(v)
        return float(np.tanh(v))  # bounded additive interpretation

    def _means_and_pace(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        mu_h = self._num(ctx.get("home_expected"))
        if mu_h is None or mu_h <= 0:
            mu_h = self.EMPIRICAL_HOME_MEAN
        mu_a = self._num(ctx.get("away_expected"))
        if mu_a is None or mu_a <= 0:
            mu_a = self.EMPIRICAL_AWAY_MEAN

        # Residual attack/defense information (shrunk to avoid double counting).
        w = float(self.RATING_RESIDUAL_WEIGHT)
        h_att = self._log_strength(self._rating(ctx, "home", "attack"))
        h_def = self._log_strength(self._rating(ctx, "home", "defense"))
        a_att = self._log_strength(self._rating(ctx, "away", "attack"))
        a_def = self._log_strength(self._rating(ctx, "away", "defense"))
        mu_h *= math.exp(w * (h_att - a_def))
        mu_a *= math.exp(w * (a_att - h_def))

        # Rest: relative fatigue on scoring rate.
        hr = self._num(ctx.get("home_rest_days"))
        ar = self._num(ctx.get("away_rest_days"))
        if hr is not None and ar is not None and hr >= 0 and ar >= 0:
            d = math.log1p(hr) - math.log1p(ar)
            mu_h *= math.exp(self.REST_ELASTICITY_PRIOR * d)
            mu_a *= math.exp(-self.REST_ELASTICITY_PRIOR * d)

        mu_h *= self.bias_mult
        mu_a *= self.bias_mult

        # Possessions per team in the first half.
        pace = None
        for key in ("pace", "expected_pace", "tempo"):
            pace = self._num(ctx.get(key))
            if pace:
                break
        if not pace:
            hp = self._num(ctx.get("home_pace"))
            ap = self._num(ctx.get("away_pace"))
            if hp and ap:
                pace = 0.5 * (hp + ap)

        if pace and pace > 0:
            # Per-48 pace -> per-half. Already-halved inputs pass through.
            n_half = pace / 2.0 if pace > 70.0 else pace
        else:
            n_half = (mu_h + mu_a) / (2.0 * self._ppp_prior)
        return mu_h, mu_a, n_half

    # ------------------------------------------------------------------ #
    # possession compound machinery
    # ------------------------------------------------------------------ #
    def _tilted_pmf(self, ppp: float) -> np.ndarray:
        """Minimum-KL tilt of the possession prior to a target mean in (0, kmax)."""
        kmax = self._k[-1]
        # Support constraint: the mean must lie strictly inside the support.
        target = float(np.clip(ppp, 1e-6, kmax - 1e-6))
        logq = np.log(self._q)

        def mean_gap(theta: float) -> float:
            z = logq + theta * self._k
            z -= z.max()
            p = np.exp(z)
            p /= p.sum()
            return float(p @ self._k) - target

        lo, hi = -5.0, 5.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = brentq(mean_gap, lo, hi, xtol=1e-12)

        z = logq + theta * self._k
        z -= z.max()
        p = np.exp(z)
        return p / p.sum()

    @staticmethod
    def _compound_pmf(fx: np.ndarray, n: float, size: int) -> np.ndarray:
        """PMF of a sum of n i.i.d. draws via FFT. Non-integer n is mixed linearly."""
        n = max(n, 0.0)
        n_lo = int(math.floor(n))
        w_hi = n - n_lo
        pmf = (1.0 - w_hi) * np.fft.irfft(fx ** n_lo, size)
        if w_hi > 0:
            pmf += w_hi * np.fft.irfft(fx ** (n_lo + 1), size)
        pmf = np.where(pmf > 0, pmf, 0.0)  # remove FFT round-off negatives
        s = pmf.sum()
        return pmf / s if s > 0 else pmf

    def _conditional_marginals(
        self, mu: float, n_mean: float, pace_nodes: np.ndarray, size: int
    ) -> np.ndarray:
        """Return matrix [pace_node, points]: efficiency-integrated, pace-conditional PMFs."""
        var_ratio = (
            self.EMPIRICAL_DISPERSION_INDEX
            if self.eff_var_ratio is None
            else self.eff_var_ratio
        )
        sd_mu = math.sqrt(max(var_ratio * mu, 0.0))
        x_e, w_e = self._gh_eff
        w_e = w_e / w_e.sum()

        out = np.zeros((pace_nodes.size, size))
        for xe, we in zip(x_e, w_e):
            # Log-normal-ish multiplicative shift keeps the mean positive.
            mu_e = mu * math.exp(sd_mu / mu * xe - 0.5 * (sd_mu / mu) ** 2)
            ppp = mu_e / n_mean
            pmf = self._tilted_pmf(ppp)
            pad = np.zeros(size)
            pad[: pmf.size] = pmf
            fx = np.fft.rfft(pad)
            for j, n in enumerate(pace_nodes):
                out[j] += we * self._compound_pmf(fx, n, size)
        return out

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mu_h, mu_a, n_mean = self._means_and_pace(game_context)

        x_p, w_p = self._gh_pace
        w_p = w_p / w_p.sum()
        sd_n = self.pace_rel_sd * n_mean
        pace_nodes = np.maximum(n_mean + sd_n * x_p, 1.0)

        kmax = int(self._k[-1])
        size = int(kmax * math.ceil(pace_nodes.max() + 1)) + 1

        mh = self._conditional_marginals(mu_h, n_mean, pace_nodes, size)
        ma = self._conditional_marginals(mu_a, n_mean, pace_nodes, size)

        # Joint distribution: mixture over the shared pace nodes.
        joint = np.einsum("j,ji,jk->ik", w_p, mh, ma)
        joint = np.where(np.isfinite(joint) & (joint > 0), joint, 0.0)

        # Trim the negligible tail for compactness, then renormalise exactly.
        ph = joint.sum(axis=1)
        pa = joint.sum(axis=0)
        h_max = int(np.searchsorted(np.cumsum(ph), 1.0 - self.TAIL_EPS)) + 1
        a_max = int(np.searchsorted(np.cumsum(pa), 1.0 - self.TAIL_EPS)) + 1
        joint = joint[: min(h_max + 1, size), : min(a_max + 1, size)]
        total = joint.sum()
        if not (total > 0 and math.isfinite(total)):
            raise ValueError("Degenerate score distribution")
        joint /= total

        return self._emit(joint)

    @staticmethod
    def _emit(joint: np.ndarray) -> "DiscreteDistribution":
        scores = {
            (int(h), int(a)): float(joint[h, a])
            for h, a in zip(*np.nonzero(joint > 0))
        }
        # Re-normalise after the float cast so the sum is exactly 1.
        s = math.fsum(scores.values())
        scores = {k: v / s for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(joint)

    # ------------------------------------------------------------------ #
    # calibration on settled historical games
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_outcome(rec: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
        for hk, ak in (
            ("home_h1", "away_h1"),
            ("home_half_1", "away_half_1"),
            ("home_score", "away_score"),
        ):
            if hk in rec and ak in rec:
                try:
                    return float(rec[hk]), float(rec[ak])
                except (TypeError, ValueError):
                    return None, None
        return None, None

    def calibrate(self, records: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        """Method-of-moments calibration, shrunk toward the priors.

        Each record holds a game context (under 'context', or flat in the
        record) and the realised first-half home/away points.
        """
        saved = (self.bias_mult, self.pace_rel_sd, self.eff_var_ratio)
        self.bias_mult = 1.0

        rows: List[Tuple[float, float, float, float, float]] = []
        for rec in records:
            ctx = rec.get("context", rec)
            yh, ya = self._extract_outcome(rec)
            if yh is None or ya is None:
                continue
            mu_h, mu_a, n = self._means_and_pace(ctx)
            rows.append((yh, ya, mu_h, mu_a, n))

        if len(rows) < 10:
            self.bias_mult, self.pace_rel_sd, self.eff_var_ratio = saved
            return {"n": float(len(rows))}

        arr = np.asarray(rows)
        yh, ya, mh, ma, n = arr.T
        m = float(len(rows))
        lam = m / (m + self.SHRINK_PSEUDO_N)

        # 1) Mean bias. This is the source of the over/under asymmetry.
        b_raw = (yh.sum() + ya.sum()) / (mh.sum() + ma.sum())
        b = 1.0 + lam * (b_raw - 1.0)
        rh, ra = yh - b * mh, ya - b * ma

        # 2) Shared pace variance from the cross-covariance of residuals.
        ppp_h, ppp_a = b * mh / n, b * ma / n
        cov = float(np.mean((rh - rh.mean()) * (ra - ra.mean())))
        var_n = max(cov / float(np.mean(ppp_h * ppp_a)), 0.0)  # variance is non-negative
        rel_raw = math.sqrt(var_n) / float(np.mean(n))
        rel = self.PACE_REL_SD_PRIOR + lam * (rel_raw - self.PACE_REL_SD_PRIOR)

        # 3) Efficiency variance = residual variance minus the possession
        #    noise and the pace contribution.
        within = np.array(
            [
                ni * float(self._tilted_pmf(p) @ (self._k - p) ** 2)
                for ni, p in zip(n, ppp_h)
            ]
        )
        pace_part = ppp_h ** 2 * (rel * n) ** 2
        eff_var = max(float(np.var(rh) - within.mean() - pace_part.mean()), 0.0)
        ratio_raw = eff_var / float(np.mean(b * mh))
        ratio = self.EMPIRICAL_DISPERSION_INDEX + lam * (
            ratio_raw - self.EMPIRICAL_DISPERSION_INDEX
        )

        self.bias_mult, self.pace_rel_sd, self.eff_var_ratio = b, rel, ratio
        return {
            "n": m,
            "bias_mult": b,
            "pace_rel_sd": rel,
            "eff_var_ratio": ratio,
        }