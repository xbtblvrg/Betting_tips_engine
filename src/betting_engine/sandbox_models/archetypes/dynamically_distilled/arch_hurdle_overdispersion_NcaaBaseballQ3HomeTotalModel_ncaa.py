"""
Candidate model for NCAA_BASEBALL | quarter_3 | home_total.

Structure
---------
Home runs in the segment are modelled as the sum over the segment's home
half-innings of independent hurdle-geometric variables:

    X_i = 0                         with prob exp(-lambda_i)
    X_i = k >= 1                    with prob (1 - exp(-lambda_i)) * (1 - rho) * rho**(k-1)

so that
    E[X_i]   = (1 - e^{-lambda_i}) / (1 - rho)
    Var[X_i] = q_i (1 + rho) / (1 - rho)^2 - q_i^2 / (1 - rho)^2,   q_i = 1 - e^{-lambda_i}

Mean anchoring: the base intensity lambda_0 is solved numerically so that the
segment mean equals the situational target (home_expected, defaulting to the
empirical baseline 0.40). Situational effects (third time through the order,
bullpen handoff, matchup, weather, rest) reshape intensity *across* innings and
set the rally continuation probability rho, but do not move the anchored mean
except through the explicit target.

Dispersion: rho has a domain-derived prior (opponent run-prevention quality,
BBCOR/offensive environment, bullpen depth) and is updated online by
moment-matching the observed Var/Mean index with Bayesian shrinkage toward the
prior; with zero observations the prior is used unchanged.

The away side is modelled symmetrically so the score lattice is valid; the
home marginal is what prices this market.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # normally pre-loaded in the execution scope
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NcaaBaseballQ3HomeTotalModel(BaseCandidateModel):
    # ----- empirical anchors supplied by the segment autopsy -----
    BASELINE_HOME_MEAN = 0.40
    BASELINE_AWAY_MEAN = 0.30

    # ----- segment geometry (overridable via game_context) -----
    # Third quarter of a 9-inning game ~ innings 5.25-7.5; use three half-innings
    # with fractional exposure on the boundary innings.
    DEFAULT_SEGMENT_INNINGS: Tuple[Tuple[int, float], ...] = ((5, 0.75), (6, 1.0), (7, 0.5))

    # ----- structural priors (log-intensity effects, shrinkable) -----
    PRIOR_BETA_TTO = 0.18      # starter third time through the order
    PRIOR_BETA_PEN = 0.12      # inning containing a likely bullpen handoff
    PRIOR_BETA_DEF = 0.35      # elasticity to opponent run-prevention quality
    PRIOR_BETA_ATT = 0.35      # elasticity to own offensive quality
    MATCHUP_SHRINK = 0.5       # matchup partly already priced into *_expected

    # ----- dispersion prior -----
    # Baseline rally continuation probability for college offense with BBCOR bats.
    # Var/Mean of a pure ZT-geometric cluster = (1+rho)/(1-rho); rho=0.38 -> ~2.2.
    PRIOR_RHO = 0.38
    RHO_PRIOR_STRENGTH = 60.0  # pseudo-observations behind the dispersion prior
    RHO_BOUNDS = (1e-4, 0.85)  # numeric domain of a valid geometric continuation

    TAIL_EPS = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_quarter_3_1108_104048",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "quarter_3",
        family: str = "home_total",
    ) -> None:
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        # Online moment accumulators (home segment runs).
        self._n = 0
        self._sum = 0.0
        self._sumsq = 0.0
        self._sum_pred_mean = 0.0

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _team_rating(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        """Return a multiplicative rating (1.0 = average)."""
        block = ctx.get(side)
        val = None
        if isinstance(block, dict):
            val = self._num(block.get(key))
        if val is None:
            val = self._num(ctx.get(f"{side}_{key}"))
        if val is None or val <= 0:
            return 1.0
        return val

    def _segment_innings(self, ctx: Dict[str, Any]) -> List[Tuple[int, float]]:
        seg = ctx.get("segment_innings")
        if isinstance(seg, (list, tuple)) and seg:
            out = []
            for item in seg:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    inn, w = int(item[0]), self._num(item[1], 1.0)
                else:
                    inn, w = int(item), 1.0
                if w and w > 0:
                    out.append((inn, w))
            if out:
                return out
        return list(self.DEFAULT_SEGMENT_INNINGS)

    def _environment_log_factor(self, ctx: Dict[str, Any]) -> float:
        """Run environment tilt from weather; zero in domes or when missing."""
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        if bool(weather.get("is_dome", False)):
            return 0.0
        temp = self._num(weather.get("temp"))
        wind = self._num(weather.get("wind_speed"))
        f = 0.0
        if temp is not None:
            # ~+1% run scoring per 1C above ~20C (ball carry), symmetric below.
            temp_c = (temp - 32.0) * 5.0 / 9.0 if temp > 45 else temp
            f += 0.01 * (temp_c - 20.0)
        if wind is not None:
            # Direction-agnostic wind adds variance more than mean; small mean effect.
            f += 0.002 * wind
        return f

    # ------------------------------------------------------------------ #
    # Intensity shape across the segment
    # ------------------------------------------------------------------ #
    def _inning_log_shape(self, ctx: Dict[str, Any], batting: str, innings: List[Tuple[int, float]]) -> np.ndarray:
        pitching = "away" if batting == "home" else "home"
        pitch_rest = self._num(ctx.get(f"{pitching}_rest_days"))

        # Starter depth: short rest / midweek games push the handoff earlier.
        tto_inning = 5.0
        pen_inning = 6.0
        if pitch_rest is not None:
            shift = -0.5 * max(0.0, 3.0 - pitch_rest) / 3.0
            tto_inning += shift
            pen_inning += 2.0 * shift
        if bool(ctx.get("is_midweek", False)):
            tto_inning -= 1.0
            pen_inning -= 1.0

        shape = []
        for inn, _ in innings:
            t = 1.0 / (1.0 + math.exp(-2.0 * (inn - tto_inning)))   # smooth TTO onset
            b = math.exp(-0.5 * ((inn - pen_inning) / 0.8) ** 2)     # handoff turbulence
            shape.append(self.PRIOR_BETA_TTO * t + self.PRIOR_BETA_PEN * b)
        return np.asarray(shape, dtype=float)

    # ------------------------------------------------------------------ #
    # Dispersion
    # ------------------------------------------------------------------ #
    def _prior_rho(self, ctx: Dict[str, Any], batting: str) -> float:
        pitching = "away" if batting == "home" else "home"
        opp_def = self._team_rating(ctx, pitching, "defense")
        own_att = self._team_rating(ctx, batting, "attack")
        pitch_rest = self._num(ctx.get(f"{pitching}_rest_days"))
        # Work on the logit scale so adjustments stay inside (0,1) naturally.
        z = math.log(self.PRIOR_RHO / (1.0 - self.PRIOR_RHO))
        z += 0.5 * math.log(own_att) - 0.5 * math.log(opp_def)
        if pitch_rest is not None:
            z += 0.08 * max(0.0, 3.0 - pitch_rest)  # tired staff -> longer rallies
        z += 0.5 * self._environment_log_factor(ctx)
        return 1.0 / (1.0 + math.exp(-z))

    def _posterior_rho(self, prior_rho: float) -> float:
        """Shrink toward moment-matched rho implied by observed Var/Mean."""
        if self._n < 3:
            return prior_rho
        mean = self._sum / self._n
        var = max(self._sumsq / self._n - mean * mean, 0.0) * self._n / (self._n - 1)
        if mean <= 0:
            return prior_rho
        d = var / mean
        # For segment totals with low scoring prob, Var/Mean ~ (1+rho)/(1-rho) - mean.
        d_cluster = max(d + mean, 1.0 + 1e-6)
        rho_obs = (d_cluster - 1.0) / (d_cluster + 1.0)
        w = self._n / (self._n + self.RHO_PRIOR_STRENGTH)
        lo, hi = self.RHO_BOUNDS
        return float(min(max((1 - w) * prior_rho + w * rho_obs, lo), hi))

    # ------------------------------------------------------------------ #
    # Distribution construction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _segment_mean(lam0: float, shape: np.ndarray, weights: np.ndarray, rho: float) -> float:
        lam = lam0 * weights * np.exp(shape)
        return float(np.sum(1.0 - np.exp(-lam)) / (1.0 - rho))

    def _segment_pmf(self, target_mean: float, shape: np.ndarray, weights: np.ndarray, rho: float) -> np.ndarray:
        target_mean = max(target_mean, 1e-9)
        # Max achievable mean as lam0 -> inf is sum(1)/(1-rho); raise rho if needed.
        cap = len(weights) / (1.0 - rho)
        if target_mean >= cap:
            rho = 1.0 - len(weights) / (target_mean * 1.05)
            rho = min(max(rho, self.RHO_BOUNDS[0]), self.RHO_BOUNDS[1])
        f = lambda l0: self._segment_mean(l0, shape, weights, rho) - target_mean
        hi = 1.0
        while f(hi) < 0 and hi < 1e6:
            hi *= 2.0
        lam0 = brentq(f, 1e-12, hi, xtol=1e-12)
        lam = lam0 * weights * np.exp(shape)

        # Support large enough that the geometric tail is negligible.
        kmax = int(math.ceil(math.log(self.TAIL_EPS) / math.log(rho))) + 1 if rho > 0 else 1
        kmax = max(kmax * max(1, len(lam)) // 2 + 5, 15)

        total = np.zeros(1)
        total[0] = 1.0
        ks = np.arange(1, kmax + 1)
        for li in lam:
            q = 1.0 - math.exp(-li)
            p = np.zeros(kmax + 1)
            p[0] = 1.0 - q
            p[1:] = q * (1.0 - rho) * rho ** (ks - 1)
            total = np.convolve(total, p)[: kmax + 1]
        total = np.clip(total, 0.0, None)
        total /= total.sum()
        nz = np.nonzero(total > self.TAIL_EPS)[0]
        return total[: (nz[-1] + 1 if len(nz) else 1)]

    def _side_pmf(self, ctx: Dict[str, Any], batting: str, baseline: float) -> np.ndarray:
        pitching = "away" if batting == "home" else "home"
        target = self._num(ctx.get(f"{batting}_expected"), baseline)
        if target is None or target <= 0:
            target = baseline

        # Matchup tilt, partially shrunk because *_expected already encodes it.
        att = self._team_rating(ctx, batting, "attack")
        dfn = self._team_rating(ctx, pitching, "defense")
        tilt = self.MATCHUP_SHRINK * (self.PRIOR_BETA_ATT * math.log(att) - self.PRIOR_BETA_DEF * math.log(dfn))
        tilt += self.MATCHUP_SHRINK * self._environment_log_factor(ctx)

        # Online bias correction: ratio of realised to predicted mean, shrunk.
        if batting == "home" and self._n > 0 and self._sum_pred_mean > 0:
            w = self._n / (self._n + self.RHO_PRIOR_STRENGTH)
            tilt += w * math.log(max(self._sum, 1e-6) / self._sum_pred_mean)

        target *= math.exp(tilt)

        innings = self._segment_innings(ctx)
        weights = np.asarray([w for _, w in innings], dtype=float)
        shape = self._inning_log_shape(ctx, batting, innings)
        rho = self._posterior_rho(self._prior_rho(ctx, batting)) if batting == "home" else self._prior_rho(ctx, batting)
        return self._segment_pmf(target, shape, weights, rho)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        home_pmf = self._side_pmf(ctx, "home", self.BASELINE_HOME_MEAN)
        away_pmf = self._side_pmf(ctx, "away", self.BASELINE_AWAY_MEAN)

        scores: Dict[Tuple[int, int], float] = {}
        for h, ph in enumerate(home_pmf):
            if ph <= 0:
                continue
            for a, pa in enumerate(away_pmf):
                p = float(ph * pa)
                if p > self.TAIL_EPS:
                    scores[(h, a)] = p
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:  # fallback: dense matrix form
            matrix = np.outer(home_pmf, away_pmf)
            matrix /= matrix.sum()
            return DiscreteDistribution.from_scores(matrix)

    # ------------------------------------------------------------------ #
    # Online learning hook
    # ------------------------------------------------------------------ #
    def update(self, game_context: Dict[str, Any], observed_home_runs: float) -> None:
        """Record a settled segment outcome for moment-matched recalibration."""
        y = self._num(observed_home_runs)
        if y is None or y < 0:
            return
        ctx = game_context or {}
        pred = self._num(ctx.get("home_expected"), self.BASELINE_HOME_MEAN) or self.BASELINE_HOME_MEAN
        self._n += 1
        self._sum += y
        self._sumsq += y * y
        self._sum_pred_mean += pred