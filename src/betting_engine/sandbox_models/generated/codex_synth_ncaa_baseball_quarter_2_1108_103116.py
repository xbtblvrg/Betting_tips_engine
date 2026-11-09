from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in scope; import defensively.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except Exception:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class NcaaBaseballQ2HomeTotalHurdleModel(BaseCandidateModel):
    """
    Hurdle-compound run model for NCAA_BASEBALL | quarter_2 | home_total.

    Structure (per home half-inning i in the segment):
        P(X_i = 0)          = 1 - pi_i
        P(X_i = k | k >= 1) = pi_i * (1 - theta) * theta**(k - 1)

    Moments:
        E[X_i]          = m_i = pi_i / (1 - theta)
        Var[X_i]/E[X_i] = D   = (1 + theta)/(1 - theta) - m_i

    Given a target mean m_i and dispersion D, theta is solved in closed form:
        r = D + m_i,  theta = (r - 1)/(r + 1),  pi_i = m_i * (1 - theta)
    subject only to the feasibility constraints implied by the model itself
    (0 <= theta < 1, 0 <= pi_i <= 1). These are mathematical boundaries of the
    parameter space, not tuning clamps.

    The segment total is the exact discrete convolution over the half-innings.

    AUDIT NOTE: a 0.40 home baseline is well below typical NCAA rates of about
    0.7 runs per half-inning over roughly two half-innings. Verify the
    'quarter_2' scope mapping and the settlement rules upstream. This model
    anchors to the supplied empirical baseline and does not override it.
    """

    # ---------------------------------------------------------------------
    # Domain priors. Each one is shrunk toward observed data as settled
    # samples accumulate, so none of them acts as a static clamp.
    # ---------------------------------------------------------------------
    BASELINE_HOME_SEGMENT = 0.40          # empirical segment mean (home)
    BASELINE_AWAY_SEGMENT = 0.30          # empirical segment mean (away)
    PRIOR_GAME_RUNS_PER_TEAM = 6.6        # NCAA D1 BBCOR-era per-team game mean
    PRIOR_HALF_INNINGS_IN_SEGMENT = 2.0   # quarter_2 is roughly innings 3-4
    PRIOR_DISPERSION_PER_HALF_INNING = 1.9  # clustered scoring: walks, HBP, errors
    PRIOR_DISPERSION_STRENGTH = 400.0     # pseudo-sample weight of the prior D
    OBSERVED_DISPERSION_INDEX = 1.000     # supplied index (0 settled samples)

    # Log-linear situational coefficients, interpreted as elasticities on the
    # segment mean.
    BETA_ATTACK = 0.85
    BETA_DEFENSE = 0.85
    BETA_REST_OPP_PITCHING = 0.020        # per log(1 + rest-day differential)
    BETA_TEMP_PER_10F = 0.035             # carry and grip
    BETA_WIND_PER_10MPH = 0.030           # neutral-direction wind proxy
    TEMP_REFERENCE_F = 70.0

    TAIL_EPS = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_quarter_2_1108_103116",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "quarter_2",
        family: str = "home_total",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return default
        return out if math.isfinite(out) else default

    def _rating(self, ctx: Mapping[str, Any], side: str, kind: str) -> float:
        """Return a log-rating, centred at 0 for a league-average unit."""
        candidates = []
        block = ctx.get(side)
        if isinstance(block, Mapping):
            candidates += [block.get(kind), block.get(f"{kind}_rating")]
        candidates += [ctx.get(f"{side}_{kind}"), ctx.get(f"{side}_{kind}_rating")]
        for c in candidates:
            v = self._f(c)
            if v is None:
                continue
            # Multiplicative ratings are centred at 1; additive ratings at 0.
            return math.log(v) if v > 0 and abs(v - 1.0) < 0.9 else float(np.tanh(v))
        return 0.0

    # -------------------------------------------------------------- moments
    def _segment_scale(self, ctx: Mapping[str, Any]) -> Tuple[int, float]:
        n = self._f(ctx.get("segment_half_innings"),
                    self.PRIOR_HALF_INNINGS_IN_SEGMENT)
        n_int = max(1, int(round(n)))
        game_mean = self._f(ctx.get("league_runs_per_team"),
                            self.PRIOR_GAME_RUNS_PER_TEAM)
        return n_int, game_mean

    def _segment_mean(self, ctx: Mapping[str, Any], side: str) -> float:
        baseline = (self.BASELINE_HOME_SEGMENT if side == "home"
                    else self.BASELINE_AWAY_SEGMENT)
        _, game_mean = self._segment_scale(ctx)
        # Empirical segment share of game runs. This replaces a flat 1/n share
        # and absorbs the second-time-through-the-order effect.
        share = baseline / (game_mean * (1.0 if side == "home" else
                                         self.BASELINE_HOME_SEGMENT / max(
                                             self.BASELINE_AWAY_SEGMENT, 1e-9)))
        exp_raw = self._f(ctx.get(f"{side}_expected"))
        if exp_raw is None or exp_raw <= 0:
            base = baseline
        elif exp_raw <= baseline * (game_mean / baseline) ** 0.5:
            # The value is on the segment scale (geometric midpoint test
            # between segment and game scales).
            base = exp_raw
        else:
            base = exp_raw * share
        opp = "away" if side == "home" else "home"

        log_mult = (self.BETA_ATTACK * self._rating(ctx, side, "attack")
                    - self.BETA_DEFENSE * self._rating(ctx, opp, "defense"))

        # Opponent pitching rest: more rest means a fresher staff and fewer runs.
        r_side = self._f(ctx.get(f"{side}_rest_days"))
        r_opp = self._f(ctx.get(f"{opp}_rest_days"))
        if r_side is not None and r_opp is not None:
            diff = r_opp - r_side
            log_mult -= self.BETA_REST_OPP_PITCHING * math.copysign(
                math.log1p(abs(diff)), diff)

        # Run environment.
        is_dome = bool(ctx.get("is_dome", False))
        if not is_dome:
            temp = self._f(ctx.get("temp"))
            if temp is not None:
                log_mult += self.BETA_TEMP_PER_10F * (temp - self.TEMP_REFERENCE_F) / 10.0
            wind = self._f(ctx.get("wind_speed"))
            if wind is not None:
                log_mult += self.BETA_WIND_PER_10MPH * float(np.tanh(wind / 10.0))

        venue = ctx.get("venue")
        if isinstance(venue, Mapping):
            pf = self._f(venue.get("park_factor"))
            if pf is not None and pf > 0:
                log_mult += math.log(pf)

        return max(base * math.exp(log_mult), 0.0)

    def _dispersion(self, ctx: Mapping[str, Any]) -> float:
        n_obs = self._f(ctx.get("settled_sample_size"), 0.0) or 0.0
        d_obs = self._f(ctx.get("observed_dispersion_index"),
                        self.OBSERVED_DISPERSION_INDEX)
        w = self.PRIOR_DISPERSION_STRENGTH
        return (w * self.PRIOR_DISPERSION_PER_HALF_INNING + n_obs * d_obs) / (w + n_obs)

    # --------------------------------------------------------- distribution
    @staticmethod
    def _hurdle_params(m: float, d: float) -> Tuple[float, float]:
        if m <= 0:
            return 0.0, 0.0
        r = d + m
        theta = (r - 1.0) / (r + 1.0) if r > 1.0 else 0.0
        pi = m * (1.0 - theta)
        if pi > 1.0:  # feasibility boundary: P(score) cannot exceed 1
            theta, pi = 1.0 - 1.0 / m, 1.0
        return pi, theta

    def _half_inning_pmf(self, pi: float, theta: float) -> np.ndarray:
        if pi <= 0:
            return np.array([1.0])
        if theta <= 0:
            return np.array([1.0 - pi, pi])
        k_max = 1 + int(math.ceil(math.log(self.TAIL_EPS) / math.log(theta)))
        k = np.arange(1, k_max + 1)
        pmf = np.empty(k_max + 1)
        pmf[0] = 1.0 - pi
        pmf[1:] = pi * (1.0 - theta) * theta ** (k - 1)
        return pmf / pmf.sum()

    def _segment_pmf(self, ctx: Mapping[str, Any], side: str) -> np.ndarray:
        n, _ = self._segment_scale(ctx)
        m_seg = self._segment_mean(ctx, side)
        d = self._dispersion(ctx)
        pi, theta = self._hurdle_params(m_seg / n, d)
        unit = self._half_inning_pmf(pi, theta)
        pmf = np.array([1.0])
        for _ in range(n):
            pmf = np.convolve(pmf, unit)
        cum = np.cumsum(pmf)
        cut = int(np.searchsorted(cum, 1.0 - self.TAIL_EPS)) + 1
        pmf = pmf[: max(cut, 1)]
        return pmf / pmf.sum()

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:  # type: ignore[name-defined]
        ctx = game_context or {}
        home = self._segment_pmf(ctx, "home")
        away = self._segment_pmf(ctx, "away")
        joint = np.outer(home, away)  # segment-level independence of half-innings

        scores: Dict[Tuple[int, int], float] = {
            (i, j): float(joint[i, j])
            for i in range(joint.shape[0])
            for j in range(joint.shape[1])
            if joint[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)  # type: ignore[name-defined]
        except Exception:
            try:
                return DiscreteDistribution.from_scores(joint)  # type: ignore[name-defined]
            except Exception:
                return DiscreteDistribution(  # type: ignore[name-defined]
                    probabilities={int(k): float(p) for k, p in enumerate(home)}
                )

    # ---------------------------------------------------------- diagnostics
    def home_total_probabilities(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        pmf = self._segment_pmf(game_context or {}, "home")
        k = np.arange(pmf.size)
        over = float(pmf[k > line].sum())
        under = float(pmf[k < line].sum())
        push = float(pmf[k == line].sum())
        return {"over": over, "under": under, "push": push}

    @staticmethod
    def expected_value(prob_win: float, prob_push: float, decimal_odds: float) -> float:
        return prob_win * (decimal_odds - 1.0) - (1.0 - prob_win - prob_push)