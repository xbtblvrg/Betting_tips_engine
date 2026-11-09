import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - primitive is pre-loaded in execution scope
    pass


class NcaaBaseballQ1HomeTotalRallyModel(BaseCandidateModel):
    """
    Compound Poisson-geometric ("rally") model for early-inning NCAA home runs.

    Generative structure
    --------------------
    Rallies N ~ Poisson(lambda). Each rally yields 1 + Geometric(p) runs, where
    p is the probability that a rally extends by one more run. Then:

        P(0)      = exp(-lambda)
        E[R]      = lambda / (1 - p)
        Var/Mean  = (1 + p) / (1 - p)

    Calibration
    -----------
    * The mean is anchored to the empirical domain means (home 0.40, away 0.30)
      and moved log-linearly by situational exposure.
    * p is solved from the scoreless frequency implied by domain fundamentals
      (or a supplied observed scoreless rate), not fixed at Poisson (p=0).
    * Situational power and run-environment terms move p through a logit link,
      so zero mass and tail shift coherently without clamps.
    * Coefficients are partial elasticities shrunk toward zero when the
      provided expected value already embeds the same information, to avoid
      double counting.
    """

    # ---- Domain anchors (empirical segment moments) -------------------------
    HOME_ANCHOR_MEAN = 0.40
    AWAY_ANCHOR_MEAN = 0.30

    # Domain fundamental: share of early NCAA half-innings that are scoreless.
    # Used only when no observed scoreless rate is supplied in game_context.
    PRIOR_SCORELESS_RATE_HOME = 0.73
    PRIOR_SCORELESS_RATE_AWAY = 0.78

    # Partial log-elasticities for situational exposure.
    BETA_OFFENSE = 0.55
    BETA_DEFENSE = 0.45
    BETA_PARK = 0.35
    BETA_REST = 0.04
    BETA_SERIES = 0.06
    BETA_TEMP = 0.004
    BETA_WIND = 0.008

    # Shrinkage applied to team-strength terms when a context-specific
    # expected value is supplied (it already reflects those strengths).
    SUPPLIED_EXPECTATION_SHRINK = 0.35

    # Logit-scale sensitivity of rally extension to power/run environment.
    GAMMA_POWER = 0.60
    GAMMA_ENV = 0.50

    # Numerical truncation
    TAIL_EPS = 1e-12
    MAX_RUNS_HARD = 40

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_quarter_1_1108_095354",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "quarter_1",
        family: str = "home_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

    # ------------------------------------------------------------------------
    # Feature extraction helpers
    # ------------------------------------------------------------------------
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            v = float(value)
            if math.isnan(v) or math.isinf(v):
                return None
            return v
        except (TypeError, ValueError):
            return None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for key in keys:
            if key in ctx:
                v = self._num(ctx.get(key))
                if v is not None:
                    return v
        return None

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """
        Accepts either nested dicts (ctx['home']['attack']) or flat keys
        (ctx['home_attack']). Ratings are interpreted as multiplicative indices
        centred at 1.0 (1.10 = 10% above average).
        """
        nested = ctx.get(side)
        if isinstance(nested, dict):
            v = self._num(nested.get(kind))
            if v is not None:
                return v
        return self._get(ctx, f"{side}_{kind}")

    @staticmethod
    def _log_index(x: Optional[float]) -> float:
        if x is None or x <= 0.0:
            return 0.0
        return math.log(x)

    @staticmethod
    def _logit(x: float) -> float:
        return math.log(x / (1.0 - x))

    @staticmethod
    def _expit(z: float) -> float:
        if z >= 0:
            e = math.exp(-z)
            return 1.0 / (1.0 + e)
        e = math.exp(z)
        return e / (1.0 + e)

    # ------------------------------------------------------------------------
    # Structural calibration
    # ------------------------------------------------------------------------
    def _base_extension_prob(self, mean: float, scoreless_rate: float) -> float:
        """
        Solve p from P(0) = exp(-mu (1 - p)):  p = 1 + ln(P0) / mu.
        If the implied process would be underdispersed (p <= 0), the rally
        structure degenerates naturally to Poisson; we map through a smooth
        softplus so p stays strictly in (0, 1) without hard clamps.
        """
        if mean <= 0.0 or not (0.0 < scoreless_rate < 1.0):
            return 0.0
        lam = -math.log(scoreless_rate)
        ratio = lam / mean  # = 1 - p in the exact solution
        # Smooth positive mapping of (1-p): softplus keeps it > 0; then
        # p = 1 - ratio_s is pushed through a logistic squash for (0,1) support.
        ratio_s = math.log1p(math.exp(ratio * 8.0)) / 8.0
        raw_p = 1.0 - ratio_s
        # Squash to (0,1) preserving values already interior
        return self._expit(self._logit(0.5) + 4.0 * (raw_p - 0.5)) if raw_p <= 0 else raw_p

    def _situational_mean(
        self, ctx: Dict[str, Any], side: str
    ) -> Tuple[float, float, float]:
        """
        Returns (mu, power_signal, env_signal) for the given batting side.
        """
        opp = "away" if side == "home" else "home"
        anchor = self.HOME_ANCHOR_MEAN if side == "home" else self.AWAY_ANCHOR_MEAN

        supplied = self._get(ctx, f"{side}_expected")
        base = supplied if (supplied is not None and supplied > 0.0) else anchor
        shrink = self.SUPPLIED_EXPECTATION_SHRINK if supplied is not None else 1.0

        # Offense exposure: lineup attack index, optional OBP/OPS relative indices
        attack = self._log_index(self._team_rating(ctx, side, "attack"))
        obp_rel = self._log_index(self._get(ctx, f"{side}_obp_index", f"{side}_ops_index"))
        offense = attack + 0.5 * obp_rel

        # Opposing run prevention: defense index > 1 means stronger prevention
        defense = self._log_index(self._team_rating(ctx, opp, "defense"))
        sp_quality = self._log_index(self._get(ctx, f"{opp}_starter_index", f"{opp}_sp_index"))
        prevention = defense + sp_quality

        # Starter freshness: rest days of the pitching side relative to a
        # standard weekly college rotation interval.
        opp_rest = self._get(ctx, f"{opp}_rest_days")
        rest_term = 0.0
        if opp_rest is not None:
            rest_term = math.log1p(max(opp_rest, 0.0)) - math.log1p(6.0)

        # Game-in-series: Friday ace (1) vs Sunday arm (3). Later games => weaker SP.
        series_game = self._get(ctx, "series_game", "game_in_series")
        series_term = 0.0 if series_game is None else (series_game - 2.0)

        # Park / weather run environment
        park = self._log_index(self._get(ctx, "park_factor", "venue_run_factor"))
        is_dome = bool(ctx.get("is_dome", False))
        temp = self._get(ctx, "temp", "temperature")
        wind = self._get(ctx, "wind_speed")
        wind_out = self._get(ctx, "wind_out_component")
        weather = 0.0
        if not is_dome:
            if temp is not None:
                weather += self.BETA_TEMP * (temp - 70.0)
            if wind_out is not None:
                weather += self.BETA_WIND * wind_out
            elif wind is not None:
                # Unknown direction: wind adds variance more than mean
                weather += 0.0

        log_mu = (
            math.log(base)
            + shrink * self.BETA_OFFENSE * offense
            - shrink * self.BETA_DEFENSE * prevention
            - self.BETA_REST * rest_term
            + self.BETA_SERIES * series_term
            + self.BETA_PARK * park
            + weather
        )
        mu = math.exp(log_mu)

        power_signal = offense + self._log_index(self._get(ctx, f"{side}_iso_index"))
        env_signal = park + weather
        if not is_dome and wind is not None:
            env_signal += 0.5 * self.BETA_WIND * wind  # gusty air widens tails
        return mu, power_signal, env_signal

    def _side_params(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        """Return (lambda, p) for a batting side."""
        mu, power, env = self._situational_mean(ctx, side)

        prior_s = self.PRIOR_SCORELESS_RATE_HOME if side == "home" else self.PRIOR_SCORELESS_RATE_AWAY
        obs_s = self._get(ctx, f"{side}_scoreless_rate", "scoreless_rate")
        anchor = self.HOME_ANCHOR_MEAN if side == "home" else self.AWAY_ANCHOR_MEAN
        scoreless = obs_s if (obs_s is not None and 0.0 < obs_s < 1.0) else prior_s

        # Base extension probability fixed at the anchor moment pair
        p0 = self._base_extension_prob(anchor, scoreless)
        p0 = min(max(p0, 1e-6), 1.0 - 1e-6) if p0 > 0 else 1e-6

        # Situational shift on logit scale: power and run environment
        # lengthen rallies (crooked numbers), the remainder moves rally rate.
        z = self._logit(p0) + self.GAMMA_POWER * power + self.GAMMA_ENV * env
        p = self._expit(z)

        lam = mu * (1.0 - p)
        return lam, p

    # ------------------------------------------------------------------------
    # Compound Poisson-geometric pmf
    # ------------------------------------------------------------------------
    def _rally_pmf(self, lam: float, p: float) -> np.ndarray:
        """
        P(0) = e^{-lam}
        P(n) = e^{-lam} * sum_{k=1..n} lam^k/k! * C(n-1,k-1) (1-p)^k p^{n-k},  n>=1
        Evaluated in log space; truncated once residual mass < TAIL_EPS.
        """
        if lam <= 0.0:
            out = np.zeros(1)
            out[0] = 1.0
            return out

        q = 1.0 - p
        log_p = math.log(p) if p > 0 else -math.inf
        log_q = math.log(q)
        log_lam = math.log(lam)

        probs = [math.exp(-lam)]
        cum = probs[0]
        n = 1
        while n <= self.MAX_RUNS_HARD and (1.0 - cum) > self.TAIL_EPS:
            terms = []
            for k in range(1, n + 1):
                if n - k > 0 and log_p == -math.inf:
                    continue
                lt = (
                    -lam
                    + k * log_lam
                    - math.lgamma(k + 1)
                    + math.lgamma(n)
                    - math.lgamma(k)
                    - math.lgamma(n - k + 1)
                    + k * log_q
                    + ((n - k) * log_p if n - k > 0 else 0.0)
                )
                terms.append(lt)
            if terms:
                m = max(terms)
                pn = math.exp(m) * sum(math.exp(t - m) for t in terms)
            else:
                pn = 0.0
            probs.append(pn)
            cum += pn
            n += 1

        arr = np.asarray(probs, dtype=float)
        arr /= arr.sum()
        return arr

    # ------------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------------
    def describe_parameters(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        lam_h, p_h = self._side_params(game_context, "home")
        lam_a, p_a = self._side_params(game_context, "away")
        return {
            "home_lambda": lam_h,
            "home_p": p_h,
            "home_mean": lam_h / (1.0 - p_h),
            "home_dispersion": (1.0 + p_h) / (1.0 - p_h),
            "home_p_zero": math.exp(-lam_h),
            "away_lambda": lam_a,
            "away_p": p_a,
            "away_mean": lam_a / (1.0 - p_a),
            "away_dispersion": (1.0 + p_a) / (1.0 - p_a),
            "away_p_zero": math.exp(-lam_a),
        }

    def home_total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        lam_h, p_h = self._side_params(game_context, "home")
        return self._rally_pmf(lam_h, p_h)

    def prob_home_over(self, game_context: Dict[str, Any], line: float) -> float:
        pmf = self.home_total_pmf(game_context)
        threshold = int(math.floor(line)) + 1
        return float(pmf[threshold:].sum()) if threshold < len(pmf) else 0.0

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        lam_h, p_h = self._side_params(ctx, "home")
        lam_a, p_a = self._side_params(ctx, "away")

        pmf_h = self._rally_pmf(lam_h, p_h)
        pmf_a = self._rally_pmf(lam_a, p_a)

        # Half-innings are disjoint events with separate pitchers; treat the
        # two sides as conditionally independent given situational inputs.
        joint = np.outer(pmf_h, pmf_a)
        joint /= joint.sum()

        scores: Dict[Tuple[int, int], float] = {}
        for h in range(joint.shape[0]):
            for a in range(joint.shape[1]):
                v = float(joint[h, a])
                if v > 0.0:
                    scores[(h, a)] = v

        return DiscreteDistribution.from_scores(scores)