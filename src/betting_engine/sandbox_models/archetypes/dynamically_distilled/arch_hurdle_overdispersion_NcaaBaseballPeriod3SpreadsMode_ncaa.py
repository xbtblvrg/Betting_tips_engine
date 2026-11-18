"""
Candidate model for the segment NCAA_BASEBALL | period_3 | spreads.

Market: the third-inning run line, i.e. the home margin H - A within inning 3.

Structural derivation
---------------------
1. Inning runs per side follow a hurdle-geometric law: the inning scores with
   probability p; given that it scores, runs are geometric on {1, 2, ...} with
   mean m. Then
       mu = p * m
       D  = Var/Mean = (2m - 1) - mu
   This gives the zero inflation (many scoreless innings) and the big-inning
   clustering that unit-dispersion Poisson/Skellam cannot reproduce.

2. The scoring probability links to the mean through p(mu) = 1 - exp(-alpha * mu).
   alpha is solved so that, at the pooled mean mu_bar, the dispersion equals the
   calibrated index D. As a consequence m(mu) = mu / p(mu) rises smoothly with
   the scoring environment, with no clamps.

3. Home field is set as log mu_{h,a} = log mu_bar +/- kappa * h / 2 + x' beta,
   where kappa = tau^2 / (tau^2 + sigma^2 / n) is the empirical-Bayes
   reliability of the raw 0.53 / 0.40 gap. At cold start (n -> 0), kappa -> 0,
   so the model refuses to inherit a noisy home edge.

4. A common environmental shock Z ~ Gamma(k, 1/k), with E[Z] = 1, multiplies
   both sides (park, weather, umpire). It is integrated with generalized
   Gauss-Laguerre quadrature. The result is a joint score lattice whose
   push mass (0-0, 1-1, ...) and tail mass are priced explicitly.

5. Lineup turnover sets the expected batting slot that opens inning 3. This
   comes from the projected plate appearances in innings 1-2, and the slot
   quality profile then modulates each side's mean.

Priors are named, documented domain quantities and can be overridden through
game_context. They are not ad-hoc clamps.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may be exported by base or pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NcaaBaseballPeriod3SpreadsModel(BaseCandidateModel):
    """Hurdle-geometric inning model with common shock and shrunk home field."""

    # ------------------------------------------------------------------ #
    # Empirical anchors (segment baseline moments)                       #
    # ------------------------------------------------------------------ #
    BASE_HOME_MEAN = 0.53
    BASE_AWAY_MEAN = 0.40

    # ------------------------------------------------------------------ #
    # Domain priors (overridable through game_context)                   #
    # ------------------------------------------------------------------ #
    # Mean runs in a scoring NCAA inning (BBCOR, high BB/E rates).
    PRIOR_SCORING_INNING_SIZE = 1.9
    # Strength of the dispersion prior, in settled-inning equivalents.
    PRIOR_DISPERSION_STRENGTH = 200.0
    # Baserunners beyond outs per run scored (runs + LOB per run).
    PRIOR_BASERUNNERS_PER_RUN = 2.1
    # Relative run-value gradient between the leadoff slot and the 9-hole.
    PRIOR_SLOT_GRADIENT = 0.12
    # Variance of the inning-level environmental multiplier (open air).
    PRIOR_ENV_VARIANCE_OPEN = 0.05
    # Fraction of open-air environmental variance that remains under a dome.
    PRIOR_DOME_VARIANCE_RATIO = 0.6
    # Wind speed (mph) that doubles the environmental variance.
    PRIOR_WIND_VARIANCE_SCALE = 15.0
    # Temperature run elasticity per degree F around the reference temperature.
    PRIOR_TEMP_ELASTICITY = 0.004
    PRIOR_TEMP_REFERENCE = 70.0
    # Log-run elasticity to pitching rest-day differential.
    PRIOR_REST_ELASTICITY = 0.03

    # ------------------------------------------------------------------ #
    # Numerical settings                                                 #
    # ------------------------------------------------------------------ #
    QUAD_NODES = 24
    TAIL_TOL = 1e-12
    MIN_ODDS = 1.40  # eligibility floor specified by the segment mandate

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_period_3_1108_081602",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "period_3",
        family: str = "spreads",
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
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.mu_bar = 0.5 * (self.BASE_HOME_MEAN + self.BASE_AWAY_MEAN)
        self.home_log_gap = math.log(self.BASE_HOME_MEAN / self.BASE_AWAY_MEAN)
        # Per-inning vs full-game detection boundary: the geometric midpoint
        # between the baseline inning total and the baseline 9-inning total.
        inning_total = self.BASE_HOME_MEAN + self.BASE_AWAY_MEAN
        self.scale_boundary = math.sqrt(inning_total * 9.0 * inning_total)

    # ================================================================== #
    # Utilities                                                          #
    # ================================================================== #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._num(ctx[k])
                if v is not None:
                    return v
        return None

    def _prior(self, ctx: Dict[str, Any], name: str) -> float:
        v = self._num(ctx.get(name.lower()))
        return v if v is not None else float(getattr(self, name))

    @staticmethod
    def _log_rating(v: Optional[float]) -> float:
        """Map a rating to log scale.

        Positive values above 0.2 are read as multiplicative (centered on 1).
        Anything else is read as already additive on the log scale.
        """
        if v is None:
            return 0.0
        return math.log(v) if v > 0.2 else v

    def _side_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            for key in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = self._num(block.get(key))
                if v is not None:
                    return v
        return self._get(ctx, f"{side}_{kind}", f"{side}_{kind}_rating")

    # ================================================================== #
    # Shrinkage of the home-field gap                                    #
    # ================================================================== #
    def _settled_n(self, ctx: Dict[str, Any]) -> float:
        n = self._get(ctx, "segment_settled_n", "settled_n", "baseline_sample_n")
        return max(n, 0.0) if n is not None else 0.0

    def _home_reliability(self, ctx: Dict[str, Any], dispersion: float) -> float:
        """kappa = tau^2 / (tau^2 + sigma^2 / n).

        tau is set from the observed gap itself: the true edge is plausibly
        anywhere in [0, h], so tau = |h| / 2. sigma^2 is the sampling variance
        of a single inning-pair log-ratio under the hurdle-geometric law:
        sigma^2 = D / mu_h + D / mu_a.
        """
        n = self._settled_n(ctx)
        if n <= 0.0:
            return 0.0
        tau2 = (0.5 * self.home_log_gap) ** 2
        sigma2 = dispersion / self.BASE_HOME_MEAN + dispersion / self.BASE_AWAY_MEAN
        return tau2 / (tau2 + sigma2 / n)

    # ================================================================== #
    # Dispersion calibration                                             #
    # ================================================================== #
    def _dispersion(self, ctx: Dict[str, Any]) -> float:
        m0 = self._prior(ctx, "PRIOR_SCORING_INNING_SIZE")
        d_prior = 2.0 * m0 - 1.0 - self.mu_bar
        d_obs = self._get(ctx, "dispersion_index", "observed_dispersion")
        n = self._settled_n(ctx)
        if d_obs is None or n <= 0.0:
            return d_prior
        nu = self._prior(ctx, "PRIOR_DISPERSION_STRENGTH")
        w = n / (n + nu)
        return (1.0 - w) * d_prior + w * d_obs

    def _alpha(self, dispersion: float) -> Optional[float]:
        """Solve p(mu_bar) = mu_bar / m0 with m0 implied by D.

        Returns None when D implies under-dispersion that a geometric hurdle
        cannot represent; the caller then uses the Poisson limit.
        """
        m0 = 0.5 * (dispersion + 1.0 + self.mu_bar)
        p0 = self.mu_bar / m0
        if not (0.0 < p0 < 1.0):
            return None
        alpha = -math.log1p(-p0) / self.mu_bar
        # m(mu) = mu / (1 - e^{-alpha mu}) >= 1/alpha, so m > 1 needs alpha < 1.
        return alpha if alpha < 1.0 else None

    # ================================================================== #
    # Lineup turnover                                                    #
    # ================================================================== #
    def _slot_factor(self, mu_inning: float, ctx: Dict[str, Any]) -> float:
        """Average slot quality of the batters expected to bat in inning 3,
        relative to the lineup mean (= 1)."""
        rho = self._prior(ctx, "PRIOR_BASERUNNERS_PER_RUN")
        g = self._prior(ctx, "PRIOR_SLOT_GRADIENT")
        pa = 3.0 + rho * mu_inning
        start = (2.0 * pa) % 9.0
        grid = np.linspace(start, start + pa, 400, endpoint=False)
        slots = np.floor(grid).astype(int) % 9
        quality = 1.0 + g * (8.0 - 2.0 * slots) / 8.0  # +g at leadoff, -g at 9-hole
        return float(np.mean(quality))

    # ================================================================== #
    # Expected inning-3 runs                                             #
    # ================================================================== #
    def _expected_runs(self, ctx: Dict[str, Any], dispersion: float) -> Tuple[float, float]:
        he = self._get(ctx, "home_expected", "home_xg", "home_expected_runs")
        ae = self._get(ctx, "away_expected", "away_xg", "away_expected_runs")
        have_ctx = he is not None and ae is not None and he > 0 and ae > 0

        if have_ctx:
            full_game = (he + ae) > self.scale_boundary
            if full_game:
                mu_h = he / 9.0
                mu_a = ae / 9.0
                mu_h *= self._slot_factor(mu_h, ctx)
                mu_a *= self._slot_factor(mu_a, ctx)
            else:
                mu_h, mu_a = he, ae
            # Upstream expectations already encode matchup and home edge.
            # Optional reliability shrinks their log-ratio toward the
            # shrunk structural home gap.
            omega = self._get(ctx, "expected_reliability")
            omega = 1.0 if omega is None else omega
            if omega != 1.0:
                kappa = self._home_reliability(ctx, dispersion)
                level = 0.5 * (math.log(mu_h) + math.log(mu_a))
                r = math.log(mu_h / mu_a)
                r_eff = omega * r + (1.0 - omega) * kappa * self.home_log_gap
                mu_h = math.exp(level + 0.5 * r_eff)
                mu_a = math.exp(level - 0.5 * r_eff)
        else:
            kappa = self._home_reliability(ctx, dispersion)
            log_h = math.log(self.mu_bar) + 0.5 * kappa * self.home_log_gap
            log_a = math.log(self.mu_bar) - 0.5 * kappa * self.home_log_gap
            # Attack of one side against the defence of the other.
            log_h += self._log_rating(self._side_rating(ctx, "home", "attack"))
            log_h += self._log_rating(self._side_rating(ctx, "away", "defense"))
            log_a += self._log_rating(self._side_rating(ctx, "away", "attack"))
            log_a += self._log_rating(self._side_rating(ctx, "home", "defense"))
            # Common environment mean effects (structural fallback only).
            env = 0.0
            is_dome = bool(ctx.get("is_dome", False))
            temp = self._get(ctx, "temp", "temperature")
            if temp is not None and not is_dome:
                env += self._prior(ctx, "PRIOR_TEMP_ELASTICITY") * (
                    temp - self._prior(ctx, "PRIOR_TEMP_REFERENCE")
                )
            pace = self._get(ctx, "pace", "tempo")
            if pace is not None and pace > 0.2:
                env += math.log(pace)
            log_h += env
            log_a += env
            mu_h, mu_a = math.exp(log_h), math.exp(log_a)
            mu_h *= self._slot_factor(mu_h, ctx)
            mu_a *= self._slot_factor(mu_a, ctx)

        # Pitching rest: more rest for a staff suppresses the opponent's runs.
        rh = self._get(ctx, "home_rest_days")
        ra = self._get(ctx, "away_rest_days")
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            beta_r = self._prior(ctx, "PRIOR_REST_ELASTICITY")
            diff = math.log1p(rh) - math.log1p(ra)
            mu_a *= math.exp(-beta_r * diff)
            mu_h *= math.exp(beta_r * diff)
        return mu_h, mu_a

    # ================================================================== #
    # Common shock                                                       #
    # ================================================================== #
    def _shock_shape(self, ctx: Dict[str, Any]) -> float:
        v = self._prior(ctx, "PRIOR_ENV_VARIANCE_OPEN")
        if bool(ctx.get("is_dome", False)):
            v *= self._prior(ctx, "PRIOR_DOME_VARIANCE_RATIO")
        else:
            wind = self._get(ctx, "wind_speed", "wind")
            if wind is not None and wind > 0:
                v *= 1.0 + wind / self._prior(ctx, "PRIOR_WIND_VARIANCE_SCALE")
        return 1.0 / v  # Gamma shape k with mean 1 and variance 1/k

    def _quadrature(self, k: float) -> Tuple[np.ndarray, np.ndarray]:
        x, w = special.roots_genlaguerre(self.QUAD_NODES, k - 1.0)
        w = w / np.sum(w)
        return x / k, w

    # ================================================================== #
    # Marginals and the joint lattice                                    #
    # ================================================================== #
    def _marginal(self, mu: float, alpha: Optional[float], n_max: int) -> np.ndarray:
        ks = np.arange(n_max + 1)
        if alpha is None:  # Poisson limit for D <= structural threshold
            logp = ks * math.log(mu) - mu - special.gammaln(ks + 1)
            pmf = np.exp(logp)
        else:
            p = -math.expm1(-alpha * mu)
            m = mu / p
            q = 1.0 - 1.0 / m
            pmf = np.empty(n_max + 1)
            pmf[0] = 1.0 - p
            pmf[1:] = p * (1.0 - q) * np.power(q, ks[1:] - 1)
        return pmf / pmf.sum()

    def _lattice_size(self, mu_max: float, alpha: Optional[float]) -> int:
        if alpha is None:
            # Poisson tail: mean plus enough standard deviations for the tolerance.
            z = math.sqrt(-2.0 * math.log(self.TAIL_TOL))
            return int(math.ceil(mu_max + z * math.sqrt(mu_max) + z * z)) + 1
        p = -math.expm1(-alpha * mu_max)
        q = 1.0 - p / mu_max
        return int(math.ceil(math.log(self.TAIL_TOL) / math.log(q))) + 1

    def joint_score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        dispersion = self._dispersion(ctx)
        alpha = self._alpha(dispersion)
        mu_h, mu_a = self._expected_runs(ctx, dispersion)
        z, w = self._quadrature(self._shock_shape(ctx))
        n_max = self._lattice_size(max(mu_h, mu_a) * float(z.max()), alpha)
        joint = np.zeros((n_max + 1, n_max + 1))
        for zi, wi in zip(z, w):
            ph = self._marginal(mu_h * zi, alpha, n_max)
            pa = self._marginal(mu_a * zi, alpha, n_max)
            joint += wi * np.outer(ph, pa)
        return joint / joint.sum()

    @staticmethod
    def margin_distribution(joint: np.ndarray) -> Dict[int, float]:
        n = joint.shape[0]
        out: Dict[int, float] = {}
        for k in range(-(n - 1), n):
            s = float(np.trace(joint, offset=-k))  # sum over cells where h - a = k
            if s > 0.0:
                out[k] = s
        return out

    # ================================================================== #
    # Public interface                                                   #
    # ================================================================== #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.joint_score_matrix(game_context)
        scores = {
            (int(i), int(j)): float(joint[i, j])
            for i, j in zip(*np.nonzero(joint > self.TAIL_TOL))
        }
        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items()}

        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
        try:
            return DiscreteDistribution.from_scores(joint)
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
        margin = self.margin_distribution(joint)
        p_home = sum(p for k, p in margin.items() if k > 0)
        p_draw = margin.get(0, 0.0)
        p_away = sum(p for k, p in margin.items() if k < 0)
        try:
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution(
                probabilities={"home": p_home, "draw": p_draw, "away": p_away}
            )

    def price_spread(self, game_context: Dict[str, Any], home_line: float) -> Dict[str, float]:
        """Cover, push and lose probabilities for the home side at home_line.

        Home covers when (H - A) + home_line > 0. The away side is the mirror image.
        """
        margin = self.margin_distribution(self.joint_score_matrix(game_context))
        win = sum(p for k, p in margin.items() if k + home_line > 0)
        push = sum(p for k, p in margin.items() if k + home_line == 0)
        return {"home_cover": win, "push": push, "away_cover": 1.0 - win - push}

    def evaluate_offer(
        self, game_context: Dict[str, Any], home_line: float, side: str, decimal_odds: float
    ) -> Dict[str, Any]:
        """Continuous EV per unit staked; pushes refund the stake."""
        pr = self.price_spread(game_context, home_line)
        if side == "home":
            p_win, p_lose = pr["home_cover"], pr["away_cover"]
        else:
            p_win, p_lose = pr["away_cover"], pr["home_cover"]
        ev = p_win * (decimal_odds - 1.0) - p_lose
        return {
            "p_win": p_win,
            "p_push": pr["push"],
            "p_lose": p_lose,
            "ev": ev,
            "eligible": bool(decimal_odds >= self.MIN_ODDS and ev > 0.0),
        }