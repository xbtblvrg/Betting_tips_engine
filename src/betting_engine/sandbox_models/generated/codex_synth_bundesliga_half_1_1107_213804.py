import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in execution scope.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover - import fallback for standalone use
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class BundesligaHalf1BothScoreModel(BaseCandidateModel):
    """
    First-half BTTS model for the Bundesliga.

    Structure
    ---------
    First-half goals (X, Y) follow a bivariate Poisson with a shared component:

        X = Z1 + Z3,  Y = Z2 + Z3,  Zi ~ Poisson(lambda_i) independent

    so E[X] = mu_h, E[Y] = mu_a, Cov(X, Y) = lambda_3 >= 0.

    The shared component represents state dependence in first halves: an
    early goal raises the trailing side's attacking intensity, so both
    sides' scoring is positively correlated in open games. Holding the
    marginal means fixed, a larger lambda_3 raises P(0, 0), which raises
    P(BTTS). An independent-Poisson model therefore underprices BTTS-yes
    in open, high-tempo matchups.

    Marginal means (log-linear around the segment baseline)
    -------------------------------------------------------
        mu_h = b_h * (home_expected / ref_h)^alpha * exp(beta . x_h)
        mu_a = b_a * (away_expected / ref_a)^alpha * exp(beta . x_a)

    - b_h, b_a: segment baseline first-half means (0.80 / 0.60).
    - ref_h, ref_a: reference means for the scale of the incoming
      *_expected inputs (full-match by default).
    - The ratio b / ref is the implied first-half goal share s, which
      absorbs the slow opening phase and the later-skewed timing curve.
    - alpha is an elasticity, so s is learned rather than fixed.
    - x holds attack-vs-defense exposure, the rest differential and tempo.

    Correlation
    -----------
        lambda_3 = rho * sqrt(mu_h * mu_a)
        rho      = rho_max * sigmoid(theta + gamma * tempo)
        rho_max  = min(mu_h, mu_a) / sqrt(mu_h * mu_a)

    rho_max is the structural feasibility bound (lambda_1, lambda_2 >= 0),
    not a tuning clamp. Tempo opens the game and lets gamma scale rho
    continuously.

    Cold start
    ----------
    Every parameter has a Gaussian prior. The prior centres come from
    Bundesliga full-match structure: about 44% of goals arrive before
    half-time, and full-match goal correlation is mildly positive.
    `fit()` performs MAP estimation on settled first-half scores, so the
    likelihood overrides the priors as evidence accumulates. With zero
    settled results the model is the prior model.

    Dispersion
    ----------
    The observed dispersion index is 1.000, so the marginals stay Poisson.
    Extra-Poisson variance in the total enters only through lambda_3:

        Var(X + Y) = mu_h + mu_a + 2 * lambda_3
    """

    # Ordered names of the parameter vector.
    _PARAM_NAMES = (
        "log_bh", "log_ba", "alpha",
        "beta_exposure", "beta_rest", "beta_tempo",
        "theta", "gamma",
    )

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_half_1_1107_213804",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "half_1",
        family: str = "both_score",
        baseline_home: float = 0.80,
        baseline_away: float = 0.60,
        reference_home: float = 1.75,
        reference_away: float = 1.40,
        prior_rho: float = 0.10,
        tail_mass: float = 1e-12,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            **kwargs,
        )

        # Empirical anchors for the segment.
        self.baseline_home = float(baseline_home)
        self.baseline_away = float(baseline_away)

        # Scale of the incoming *_expected inputs.
        # Defaults are Bundesliga full-match home/away means.
        self.reference_home = float(reference_home)
        self.reference_away = float(reference_away)

        # Truncation tolerance for the score lattice; total mass is
        # renormalised afterwards.
        self.tail_mass = float(tail_mass)

        # Convert the prior on rho into a prior on theta, evaluated at the
        # baseline means.
        rho_max0 = self._rho_max(self.baseline_home, self.baseline_away)
        frac = min(max(prior_rho / rho_max0, 1e-6), 1 - 1e-6)
        theta0 = float(special.logit(frac))

        # Gaussian prior (mean, sd) for each parameter.
        self.prior_mean = np.array(
            [
                math.log(self.baseline_home),
                math.log(self.baseline_away),
                1.0,      # alpha: proportional transfer of match strength
                0.0,      # beta_exposure: exposure mostly already in *_expected
                0.0,      # beta_rest
                0.0,      # beta_tempo
                theta0,   # theta
                0.0,      # gamma
            ]
        )
        self.prior_sd = np.array(
            [
                0.10,  # +-10% on the baseline first-half means
                0.10,
                0.25,
                0.15,
                0.10,
                0.10,
                0.75,  # correlation is weakly identified; keep it soft
                0.50,
            ]
        )

        self.params = self.prior_mean.copy()
        self.n_fitted = 0

    # ------------------------------------------------------------------ #
    # Feature engineering
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    @classmethod
    def _log_strength(cls, value: Any) -> float:
        """Ratings > 0 are treated as multiplicative strengths centred at 1.

        Any other value is assumed to be on an additive, already-centred
        scale.
        """
        v = cls._num(value)
        if v is None:
            return 0.0
        return math.log(v) if v > 0 else v

    @classmethod
    def _side(cls, ctx: Dict[str, Any], side: str, key: str) -> Any:
        block = ctx.get(side)
        if isinstance(block, dict) and key in block:
            return block[key]
        return ctx.get(f"{side}_{key}")

    def _features(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, float]:
        # Attack-vs-defence exposure: own attack against opponent defence.
        # A higher defence rating means a stronger defence.
        exp_h = (
            self._log_strength(self._side(ctx, "home", "attack"))
            - self._log_strength(self._side(ctx, "away", "defense"))
        )
        exp_a = (
            self._log_strength(self._side(ctx, "away", "attack"))
            - self._log_strength(self._side(ctx, "home", "defense"))
        )

        # Rest differential on a log scale (diminishing returns per day).
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        rest = (
            math.log((1.0 + rh) / (1.0 + ra))
            if rh is not None and ra is not None and rh >= 0 and ra >= 0
            else 0.0
        )

        # Tempo relative to the league reference.
        # If no reference is supplied, the feature is neutral (0).
        tempo = 0.0
        t = self._num(ctx.get("tempo", ctx.get("pace")))
        if t is None:
            th = self._num(self._side(ctx, "home", "pace"))
            ta = self._num(self._side(ctx, "away", "pace"))
            if th is not None and ta is not None and th > 0 and ta > 0:
                t = math.sqrt(th * ta)
        ref_t = self._num(ctx.get("league_tempo", ctx.get("league_pace")))
        if t is not None and ref_t is not None and t > 0 and ref_t > 0:
            tempo = math.log(t / ref_t)

        x_h = np.array([exp_h, rest, tempo])
        x_a = np.array([exp_a, -rest, tempo])
        return x_h, x_a, tempo

    def _expected_ratio(self, ctx: Dict[str, Any], key: str, ref: float, base: float) -> float:
        v = self._num(ctx.get(key))
        if v is None or v <= 0:
            return 1.0
        scale = str(ctx.get("expected_scope", "full_match")).lower()
        denom = base if scale in ("half_1", "first_half", "h1") else ref
        return v / denom

    # ------------------------------------------------------------------ #
    # Core parameter map
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rho_max(mu_h: float, mu_a: float) -> float:
        return min(mu_h, mu_a) / math.sqrt(mu_h * mu_a)

    def _rates(self, ctx: Dict[str, Any], p: Optional[np.ndarray] = None) -> Tuple[float, float, float]:
        p = self.params if p is None else p
        log_bh, log_ba, alpha, b_exp, b_rest, b_tempo, theta, gamma = p
        beta = np.array([b_exp, b_rest, b_tempo])
        x_h, x_a, tempo = self._features(ctx)

        r_h = self._expected_ratio(ctx, "home_expected", self.reference_home, self.baseline_home)
        r_a = self._expected_ratio(ctx, "away_expected", self.reference_away, self.baseline_away)

        mu_h = math.exp(log_bh + alpha * math.log(r_h) + float(beta @ x_h))
        mu_a = math.exp(log_ba + alpha * math.log(r_a) + float(beta @ x_a))

        rho = self._rho_max(mu_h, mu_a) * float(special.expit(theta + gamma * tempo))
        lam3 = rho * math.sqrt(mu_h * mu_a)
        # By construction lam3 <= min(mu_h, mu_a), so lam1, lam2 >= 0.
        return mu_h - lam3, mu_a - lam3, lam3

    # ------------------------------------------------------------------ #
    # Bivariate Poisson primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _bp_logpmf(x: int, y: int, l1: float, l2: float, l3: float) -> float:
        k = np.arange(0, min(x, y) + 1)
        terms = (
            stats.poisson.logpmf(x - k, l1)
            + stats.poisson.logpmf(y - k, l2)
            + stats.poisson.logpmf(k, l3)
        )
        return float(special.logsumexp(terms))

    def _score_matrix(self, l1: float, l2: float, l3: float) -> Dict[Tuple[int, int], float]:
        n_h = int(stats.poisson.ppf(1.0 - self.tail_mass, l1 + l3)) + 1
        n_a = int(stats.poisson.ppf(1.0 - self.tail_mass, l2 + l3)) + 1
        p1 = stats.poisson.pmf(np.arange(n_h + 1), l1)
        p2 = stats.poisson.pmf(np.arange(n_a + 1), l2)
        p3 = stats.poisson.pmf(np.arange(min(n_h, n_a) + 1), l3)

        grid = np.zeros((n_h + 1, n_a + 1))
        for k, w in enumerate(p3):
            grid[k:, k:] += w * np.outer(p1[: n_h + 1 - k], p2[: n_a + 1 - k])
        grid /= grid.sum()

        return {
            (i, j): float(grid[i, j])
            for i in range(n_h + 1)
            for j in range(n_a + 1)
            if grid[i, j] > 0
        }

    @staticmethod
    def btts_probability(l1: float, l2: float, l3: float) -> float:
        """Closed form for P(X > 0 and Y > 0)."""
        return float(
            1.0
            - math.exp(-(l1 + l3))
            - math.exp(-(l2 + l3))
            + math.exp(-(l1 + l2 + l3))
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        l1, l2, l3 = self._rates(ctx)
        scores = self._score_matrix(l1, l2, l3)
        try:
            return DiscreteDistribution.from_scores(scores)
        except (AttributeError, TypeError):
            p_yes = self.btts_probability(l1, l2, l3)
            return DiscreteDistribution(probabilities={"yes": p_yes, "no": 1.0 - p_yes})

    def explain(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        """Return the components behind a single prediction."""
        l1, l2, l3 = self._rates(game_context or {})
        mu_h, mu_a = l1 + l3, l2 + l3
        p_indep = (1.0 - math.exp(-mu_h)) * (1.0 - math.exp(-mu_a))
        return {
            "mu_home_h1": mu_h,
            "mu_away_h1": mu_a,
            "lambda_shared": l3,
            "rho": l3 / math.sqrt(mu_h * mu_a),
            "p_btts": self.btts_probability(l1, l2, l3),
            "p_btts_independent": p_indep,
        }

    def fit(self, records: Iterable[Dict[str, Any]]) -> "BundesligaHalf1BothScoreModel":
        """
        MAP update from settled first-half results.

        Each record holds:
          - 'game_context' (or the context fields at the top level)
          - 'home_h1' and 'away_h1': integer first-half goals

        The Gaussian priors act as hierarchical shrinkage toward
        full-match-derived values. Their influence decays as about 1/n.
        """
        data: List[Tuple[Dict[str, Any], int, int]] = []
        for r in records:
            ctx = r.get("game_context", r)
            h = self._num(r.get("home_h1"))
            a = self._num(r.get("away_h1"))
            if h is None or a is None or h < 0 or a < 0:
                continue
            data.append((ctx, int(round(h)), int(round(a))))

        if not data:
            return self

        def neg_log_post(p: np.ndarray) -> float:
            ll = 0.0
            for ctx, h, a in data:
                l1, l2, l3 = self._rates(ctx, p)
                ll += self._bp_logpmf(h, a, max(l1, 1e-300), max(l2, 1e-300), max(l3, 1e-300))
            lp = -0.5 * float(np.sum(((p - self.prior_mean) / self.prior_sd) ** 2))
            return -(ll + lp)

        res = optimize.minimize(neg_log_post, self.params, method="L-BFGS-B")
        if res.success and np.all(np.isfinite(res.x)):
            self.params = res.x
            self.n_fitted = len(data)
        return self

    def get_params(self) -> Dict[str, float]:
        return {name: float(v) for name, v in zip(self._PARAM_NAMES, self.params)}