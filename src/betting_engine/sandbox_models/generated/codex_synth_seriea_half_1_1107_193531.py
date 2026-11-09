"""
Candidate model: SERIEA | half_1 | moneyline

Architecture (derived from first-half Serie A scoring mechanics):

1. Marginal intensities
   lambda_i = mu_i * (att_i * exp_def_{-i})^w_s * pace * weather * g(rest_i - rest_{-i})
   mu = (home_expected, away_expected), with the empirical anchor (0.80, 0.60) as fallback.
   Strength ratings are partially pooled through the exponent w_s, because the
   supplied expectations usually already contain team strength.

2. First-half home-advantage shrinkage
   The home share r = log(lambda_H / lambda_A) is pooled toward the 1H anchor
   share log(0.80 / 0.60) with a precision-weighted (normal-normal) estimator.
   The total lambda_H + lambda_A is preserved, so only the split moves and the
   draw mass is not distorted.

3. Correlated low-score structure
   - Bivariate Poisson (X, Y) = (U1 + U3, U2 + U3). The shared rate lambda_3 is
     set from a prior goal correlation. It preserves the marginals exactly.
   - A Dixon-Coles tau(rho) correction on the {0,1} x {0,1} cells. rho is mapped
     smoothly (tanh) into its analytic feasibility interval, so no hard clamp is
     used.
   - Conjugate online updating. Settled 1H results update a Gamma posterior on
     the draw-rate multiplier, and that posterior rescales rho. This turns the
     cold-start prior into a data-driven estimate as results arrive.

4. The output is a pure model probability {home, draw, away}. No bookmaker
   prices are blended in.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - the sandbox pre-loads the base class
    BaseCandidateModel = object  # type: ignore

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded in scope


class SerieAHalf1MoneylineModel(BaseCandidateModel):
    # ---------------------------------------------------------------- priors
    # Empirical 1H anchors. These are the normalization point of the model,
    # not clamps.
    ANCHOR_HOME = 0.80
    ANCHOR_AWAY = 0.60

    # Home-advantage pooling.
    # Prior spread of the 1H log home share across fixtures. This is roughly
    # the between-team SD of the home edge, which is smaller in the first half.
    HA_PRIOR_SD = 0.18
    # Effective number of matches of information behind a supplied expectation.
    # Sampling variance of the log ratio is approximately 1/(n*lH) + 1/(n*lA).
    EXPECTATION_EFFECTIVE_N = 20.0

    # Strength-rating pooling exponent. It is derived from the same
    # normal-normal logic: w = tau^2 / (tau^2 + sigma^2).
    STRENGTH_SIGNAL_SD = 0.12
    STRENGTH_NOISE_SD = 0.15

    # Correlation structure.
    PRIOR_GOAL_CORR = 0.04   # shared-component correlation of 1H goals
    PRIOR_DC_RHO = -0.07     # Dixon-Coles draw-cell inflation (negative = more 0-0/1-1)

    # Rest: elasticity of log intensity per unit of tanh(rest differential / week).
    REST_ELASTICITY = 0.03
    REST_SCALE_DAYS = 7.0

    # Weather: wind above a reference speed suppresses tempo, via a softplus.
    WIND_REF = 25.0          # km/h
    WIND_ELASTICITY = 0.004  # log-intensity change per km/h above the reference
    TEMP_OPT = 16.0          # deg C
    TEMP_ELASTICITY = 0.0004 # quadratic log penalty per deg^2

    # Gamma prior on the draw-rate multiplier (shape = rate gives a mean of 1).
    DRAW_PRIOR_STRENGTH = 60.0

    TAIL_EPS = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_1_1107_193531",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_1",
        family: str = "moneyline",
    ) -> None:
        if BaseCandidateModel is not object:
            try:
                super().__init__(
                    model_id=model_id, version=version, league=league,
                    scope=scope, family=family,
                )
            except TypeError:
                super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Conjugate draw-rate sufficient statistics.
        self._obs_draws = 0.0
        self._exp_draws = 0.0

    # ----------------------------------------------------------- utilities
    @staticmethod
    def _f(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _softplus(x: float) -> float:
        return math.log1p(math.exp(-abs(x))) + max(x, 0.0)

    @staticmethod
    def _as_multiplier(v: Optional[float]) -> float:
        """Accept a positive multiplier (centered at 1) or a log-scale rating (centered at 0)."""
        if v is None:
            return 1.0
        if v > 0.0 and v < 20.0 and abs(v - 1.0) < abs(v):
            return v
        return math.exp(v)

    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        block = ctx.get(side)
        val = None
        if isinstance(block, dict):
            for k in (key, f"{key}_rating", f"{key}_strength", f"{key}_exposure"):
                if k in block:
                    val = self._f(block[k])
                    break
        if val is None:
            for k in (f"{side}_{key}", f"{side}_{key}_rating", f"{side}_{key}_exposure"):
                if k in ctx:
                    val = self._f(ctx[k])
                    break
        return self._as_multiplier(val)

    # -------------------------------------------------------- intensities
    def _marginals(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mu_h = self._f(ctx.get("home_expected"), None)
        mu_a = self._f(ctx.get("away_expected"), None)
        mu_h = mu_h if (mu_h is not None and mu_h > 0) else self.ANCHOR_HOME
        mu_a = mu_a if (mu_a is not None and mu_a > 0) else self.ANCHOR_AWAY

        # Partially pooled strength adjustment. Defense is read as an
        # "exposure": a value above 1 means the side concedes more.
        w_s = self.STRENGTH_SIGNAL_SD ** 2 / (
            self.STRENGTH_SIGNAL_SD ** 2 + self.STRENGTH_NOISE_SD ** 2
        )
        h_att, h_def = self._rating(ctx, "home", "attack"), self._rating(ctx, "home", "defense")
        a_att, a_def = self._rating(ctx, "away", "attack"), self._rating(ctx, "away", "defense")
        s_h = (h_att * a_def) ** w_s
        s_a = (a_att * h_def) ** w_s

        # Common tempo factor. It scales both sides and leaves the split unchanged.
        pace = self._f(ctx.get("pace", ctx.get("tempo")), None)
        pace_mult = self._as_multiplier(pace) ** w_s if pace is not None else 1.0

        # Weather. A dome removes the weather channel entirely.
        log_weather = 0.0
        if not bool(ctx.get("is_dome", False)):
            wind = self._f(ctx.get("wind_speed"), None)
            if wind is not None:
                log_weather -= self.WIND_ELASTICITY * self._softplus(wind - self.WIND_REF)
            temp = self._f(ctx.get("temp"), None)
            if temp is not None:
                log_weather -= self.TEMP_ELASTICITY * (temp - self.TEMP_OPT) ** 2
        common = pace_mult * math.exp(log_weather)

        # Rest differential. It is smooth and antisymmetric, so it is
        # zero-sum on the log scale.
        rh = self._f(ctx.get("home_rest_days"), None)
        ra = self._f(ctx.get("away_rest_days"), None)
        rest_term = 0.0
        if rh is not None and ra is not None:
            rest_term = self.REST_ELASTICITY * math.tanh((rh - ra) / self.REST_SCALE_DAYS)

        lam_h = mu_h * s_h * common * math.exp(rest_term)
        lam_a = mu_a * s_a * common * math.exp(-rest_term)
        return self._shrink_home_share(lam_h, lam_a)

    def _shrink_home_share(self, lam_h: float, lam_a: float) -> Tuple[float, float]:
        """Precision-weighted pooling of the log home share toward the 1H anchor,
        keeping the total intensity fixed."""
        total = lam_h + lam_a
        r_obs = math.log(lam_h / lam_a)
        r_prior = math.log(self.ANCHOR_HOME / self.ANCHOR_AWAY)
        n = self.EXPECTATION_EFFECTIVE_N
        samp_var = 1.0 / (n * lam_h) + 1.0 / (n * lam_a)
        prior_var = self.HA_PRIOR_SD ** 2
        w = prior_var / (prior_var + samp_var)
        r_post = w * r_obs + (1.0 - w) * r_prior
        share_h = 1.0 / (1.0 + math.exp(-r_post))
        return total * share_h, total * (1.0 - share_h)

    # --------------------------------------------------- score lattice
    def _rho(self, lam_h: float, lam_a: float) -> float:
        # Posterior draw multiplier. Gamma(a0 + obs, a0 + exp) has a mean near 1 at cold start.
        a0 = self.DRAW_PRIOR_STRENGTH
        draw_mult = (a0 + self._obs_draws) / (a0 + self._exp_draws)
        raw = self.PRIOR_DC_RHO * draw_mult
        # Feasibility interval that keeps every tau cell positive.
        lo = -1.0 / max(lam_h, lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
        return mid + half * math.tanh((raw - mid) / half)

    def _score_matrix(self, lam_h: float, lam_a: float) -> np.ndarray:
        lam3 = self.PRIOR_GOAL_CORR * math.sqrt(lam_h * lam_a)
        # Never let the shared rate exhaust either marginal. A smooth ratio keeps U1, U2 > 0.
        lam3 = lam3 * min(lam_h, lam_a) / (min(lam_h, lam_a) + lam3)
        l1, l2 = lam_h - lam3, lam_a - lam3

        g_max = int(stats.poisson.isf(self.TAIL_EPS, lam_h + lam_a)) + 2
        k = np.arange(g_max + 1)
        p1 = stats.poisson.pmf(k, l1)
        p2 = stats.poisson.pmf(k, l2)
        p3 = stats.poisson.pmf(k, lam3)

        m = np.zeros((g_max + 1, g_max + 1))
        for s in range(g_max + 1):
            if p3[s] < self.TAIL_EPS:
                break
            m[s:, s:] += p3[s] * np.outer(p1[: g_max + 1 - s], p2[: g_max + 1 - s])

        rho = self._rho(lam_h, lam_a)
        m[0, 0] *= 1.0 - lam_h * lam_a * rho
        m[0, 1] *= 1.0 + lam_h * rho
        m[1, 0] *= 1.0 + lam_a * rho
        m[1, 1] *= 1.0 - rho
        return m / m.sum()

    # -------------------------------------------------------- interface
    def _outcome_probs(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        lam_h, lam_a = self._marginals(ctx)
        m = self._score_matrix(lam_h, lam_a)
        p_home = float(np.tril(m, -1).sum())
        p_draw = float(np.trace(m))
        p_away = float(np.triu(m, 1).sum())
        z = p_home + p_draw + p_away
        return p_home / z, p_draw / z, p_away / z

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        p_h, p_d, p_a = self._outcome_probs(ctx)
        return DiscreteDistribution(probabilities={"home": p_h, "draw": p_d, "away": p_a})

    def observe_result(self, game_context: Dict[str, Any], home_goals_1h: int, away_goals_1h: int) -> None:
        """Conjugate update of the draw-rate multiplier from a settled first half."""
        _, p_d, _ = self._outcome_probs(game_context or {})
        self._exp_draws += p_d
        self._obs_draws += 1.0 if int(home_goals_1h) == int(away_goals_1h) else 0.0

    # Aliases for engines that call a different update hook.
    def update(self, game_context: Dict[str, Any], result: Dict[str, Any]) -> None:
        h = result.get("home_score", result.get("home_goals", result.get("home")))
        a = result.get("away_score", result.get("away_goals", result.get("away")))
        if h is not None and a is not None:
            self.observe_result(game_context, int(h), int(a))