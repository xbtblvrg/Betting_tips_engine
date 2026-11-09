import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from numpy.polynomial.hermite_e import hermegauss
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally exported next to the base class
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pre-loaded into execution scope by the sandbox
    DiscreteDistribution = globals()["DiscreteDistribution"]  # type: ignore  # noqa: F821


class NCAABaseballDoubleChanceModel(BaseCandidateModel):
    """
    NCAA_BASEBALL | full_game | double_chance

    Structural model
    ----------------
    1. Regulation runs for each side ~ Negative Binomial(mu, r).
       The dispersion index D = Var/Mean gives r = mu / (D - 1). This captures
       aluminium-bat big innings and thin bullpens. If D <= 1 the model falls
       back to Poisson.
    2. Means: mu = level x attack x opponent-defence exposure x opponent
       pitching-fatigue (rest) x shared run environment (temperature, wind,
       dome).
    3. Margin M = H - A by exact convolution of the two PMFs.
    4. Ties after 9 innings are resolved by extra innings. NB additivity means
       one half-inning ~ NB(mu/9, r/9) with the same success probability. A
       home walk-off ("last at-bat") lift applies to scoring the single run
       needed after a scoreless top half.
       p_xi = P(home wins inning) / P(inning is decided).
    5. A residual "draw" only comes from games tied after regulation that are
       never resolved (curfew, travel, or weather-called ties).
    6. Strength uncertainty in the home/away log-ratio is integrated out with
       Gauss-Hermite quadrature. This shrinks win probabilities toward
       coin-flip in proportion to genuine parameter uncertainty, which limits
       home-field over-confidence without any clamp.

    Scale guard
    -----------
    The supplied empirical anchors (1.60 / 1.20) are on a soccer scale. NCAA
    run environments are roughly 6-7 runs per team. When the incoming
    expectations sum far below the league run environment, they are read as
    relative strength indices. Their ratio is kept and their level is rescaled
    to the league environment. Only the ratio carries information about the
    winner. The level drives tie mass, so it must be baseball-scale.

    Every prior below is a documented, overridable domain parameter, not a
    clamp. Any of them can be overridden per game via game_context.
    """

    # ---------------- domain priors (override via ctor kwargs or game_context) ----
    DEFAULT_PRIORS: Dict[str, float] = {
        # League run environment, D1 average (runs per team per game)
        "league_runs_per_team": 6.4,
        # Inputs whose total falls below this fraction of the league total are
        # read as relative indices
        "scale_detection_fraction": 0.5,
        # Empirical anchors supplied by the forensic report (used when the
        # context is missing)
        "anchor_home_expected": 1.60,
        "anchor_away_expected": 1.20,
        # Var/Mean of team runs per game in NCAA D1 (aluminium bats, bullpen
        # depth)
        "dispersion_index": 2.4,
        # Weight of attack/defence ratings when *_expected already present
        # (information overlap)
        "rating_weight_with_expected": 0.5,
        # Pitching-fatigue: log-run effect on runs ALLOWED per unit of rest
        # deficit, smooth saturation
        "rest_beta": 0.06,
        "rest_reference_days": 3.0,
        "rest_scale_days": 2.0,
        # Shared run environment
        "temp_beta_per_10F": 0.025,
        "temp_reference_F": 70.0,
        "wind_beta_per_10mph": 0.04,  # multiplied by wind direction sign (+1 out, -1 in)
        # Extra innings
        "regulation_innings": 9.0,
        "extra_inning_rate_mult": 1.0,  # relative scoring rate of extra-inning pitching
        "walkoff_small_ball_lift": 0.03,  # relative lift to P(home scores >=1 | needs exactly 1)
        "unresolved_tie_rate": 0.02,  # share of regulation ties never completed
        # Uncertainty in the home/away log-strength ratio (integrated out)
        "strength_log_sigma": 0.12,
        "quadrature_nodes": 15,
        # Numerical tail tolerance for PMF truncation
        "tail_eps": 1e-12,
        # Wagering eligibility (spec: odds >= 1.40 and positive expectation)
        "min_decimal_odds": 1.40,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_full_game_1108_053330",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "full_game",
        family: str = "double_chance",
        **prior_overrides: float,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.priors: Dict[str, float] = dict(self.DEFAULT_PRIORS)
        for k, v in prior_overrides.items():
            if k in self.priors and self._finite(v):
                self.priors[k] = float(v)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _finite(x: Any) -> bool:
        try:
            return x is not None and math.isfinite(float(x))
        except (TypeError, ValueError):
            return False

    def _num(self, ctx: Dict[str, Any], key: str, default: Optional[float]) -> Optional[float]:
        v = ctx.get(key) if isinstance(ctx, dict) else None
        return float(v) if self._finite(v) else default

    def _prior(self, ctx: Dict[str, Any], key: str) -> float:
        """Per-game override of any prior via game_context, else model prior."""
        v = self._num(ctx, key, None)
        return v if v is not None else self.priors[key]

    @staticmethod
    def _side_rating(ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
        """Accepts ctx['home']={'attack':..}, or flat ctx['home_attack']."""
        block = ctx.get(side)
        cand = None
        if isinstance(block, dict):
            cand = block.get(name)
        if cand is None:
            cand = ctx.get(f"{side}_{name}")
        try:
            val = float(cand)
            return val if math.isfinite(val) and val > 0 else None
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------ mean construction
    def _base_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, bool, bool]:
        h = self._num(ctx, "home_expected", None)
        a = self._num(ctx, "away_expected", None)
        supplied = h is not None and a is not None and h > 0 and a > 0
        if not supplied:
            h = self._prior(ctx, "anchor_home_expected")
            a = self._prior(ctx, "anchor_away_expected")

        league_total = 2.0 * self._prior(ctx, "league_runs_per_team")
        total = h + a
        rescaled = total < self._prior(ctx, "scale_detection_fraction") * league_total
        if rescaled:
            # Keep the information-bearing ratio. Restore the baseball run level.
            share_h = h / total
            h, a = league_total * share_h, league_total * (1.0 - share_h)
        return h, a, supplied, rescaled

    def _rating_multipliers(self, ctx: Dict[str, Any], expected_supplied: bool) -> Tuple[float, float]:
        w = self._prior(ctx, "rating_weight_with_expected") if expected_supplied else 1.0
        log_h = log_a = 0.0
        h_att = self._side_rating(ctx, "home", "attack")
        a_def = self._side_rating(ctx, "away", "defense")
        a_att = self._side_rating(ctx, "away", "attack")
        h_def = self._side_rating(ctx, "home", "defense")
        if h_att:
            log_h += math.log(h_att)
        if a_def:
            log_h += math.log(a_def)  # defence > 1 = leaky exposure
        if a_att:
            log_a += math.log(a_att)
        if h_def:
            log_a += math.log(h_def)
        return math.exp(w * log_h), math.exp(w * log_a)

    def _fatigue_multiplier(self, ctx: Dict[str, Any], pitching_side: str) -> float:
        """Runs ALLOWED by `pitching_side` rise as its staff's rest shrinks (smooth saturation)."""
        rest = self._num(ctx, f"{pitching_side}_rest_days", None)
        if rest is None:
            return 1.0
        ref = self._prior(ctx, "rest_reference_days")
        scale = self._prior(ctx, "rest_scale_days")
        beta = self._prior(ctx, "rest_beta")
        deficit = math.tanh((ref - rest) / scale) if scale > 0 else 0.0
        return math.exp(beta * deficit)

    def _environment_multiplier(self, ctx: Dict[str, Any]) -> float:
        dome = ctx.get("is_dome")
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        merged = {**weather, **{k: v for k, v in ctx.items() if k in ("temp", "wind_speed", "wind_direction")}}
        if dome is None:
            dome = weather.get("is_dome", False)
        if bool(dome):
            return 1.0
        log_env = 0.0
        temp = self._num(merged, "temp", None)
        if temp is not None:
            log_env += self._prior(ctx, "temp_beta_per_10F") * (temp - self._prior(ctx, "temp_reference_F")) / 10.0
        wind = self._num(merged, "wind_speed", None)
        direction = self._num(merged, "wind_direction", 0.0)  # +1 blowing out, -1 blowing in
        if wind is not None and direction:
            log_env += self._prior(ctx, "wind_beta_per_10mph") * direction * wind / 10.0
        return math.exp(log_env)

    def _team_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, Any]]:
        h, a, supplied, rescaled = self._base_means(ctx)
        rh, ra = self._rating_multipliers(ctx, supplied)
        env = self._environment_multiplier(ctx)
        mu_h = h * rh * self._fatigue_multiplier(ctx, "away") * env
        mu_a = a * ra * self._fatigue_multiplier(ctx, "home") * env
        info = {"expected_supplied": supplied, "rescaled_to_baseball_level": rescaled, "env_mult": env}
        return mu_h, mu_a, info

    # ------------------------------------------------------ count distributions
    @staticmethod
    def _nb_params(mu: float, dispersion_index: float) -> Optional[float]:
        """Return NB size r for Var/Mean = D; None => Poisson limit."""
        excess = dispersion_index - 1.0
        return mu / excess if excess > 0 else None

    def _pmf(self, mu: float, r: Optional[float], upper: int) -> np.ndarray:
        k = np.arange(upper + 1)
        if r is None:
            p = stats.poisson.pmf(k, mu)
        else:
            p = stats.nbinom.pmf(k, r, r / (r + mu))
        s = p.sum()
        return p / s if s > 0 else p

    def _upper(self, mu: float, r: Optional[float], eps: float) -> int:
        q = stats.poisson.ppf(1 - eps, mu) if r is None else stats.nbinom.ppf(1 - eps, r, r / (r + mu))
        return int(max(q, 1)) + 1

    @staticmethod
    def _compare(ph: np.ndarray, pa: np.ndarray) -> Tuple[float, float, float]:
        n = max(len(ph), len(pa))
        ph = np.pad(ph, (0, n - len(ph)))
        pa = np.pad(pa, (0, n - len(pa)))
        cdf_a = np.cumsum(pa)
        cdf_h = np.cumsum(ph)
        p_home = float(np.dot(ph[1:], cdf_a[:-1]))
        p_away = float(np.dot(pa[1:], cdf_h[:-1]))
        p_tie = float(np.dot(ph, pa))
        return p_home, p_tie, p_away

    # ------------------------------------------------------ extra innings
    def _extra_inning_home_share(self, ctx: Dict[str, Any], mu_h: float, mu_a: float, D: float) -> float:
        innings = self._prior(ctx, "regulation_innings")
        mult = self._prior(ctx, "extra_inning_rate_mult")
        eps = self._prior(ctx, "tail_eps")
        lam_h, lam_a = mult * mu_h / innings, mult * mu_a / innings
        # NB additivity: sum of `innings` iid NB(r_i, p) = NB(innings*r_i, p) -> r_i = r/innings
        r_h = self._nb_params(mu_h, D)
        r_a = self._nb_params(mu_a, D)
        r_h = None if r_h is None else r_h / innings
        r_a = None if r_a is None else r_a / innings
        upper = max(self._upper(lam_h, r_h, eps), self._upper(lam_a, r_a, eps))
        ph = self._pmf(lam_h, r_h, upper)
        pa = self._pmf(lam_a, r_a, upper)

        # Last at-bat: after a scoreless top half, home plays for exactly one run.
        lift = self._prior(ctx, "walkoff_small_ball_lift")
        p_h_scores = 1.0 - ph[0]
        boosted = p_h_scores * (1.0 + lift)
        boosted = boosted / (1.0 + lift * p_h_scores)  # odds-preserving lift keeps it a probability
        win_h, tie, win_a = self._compare(ph, pa)
        delta = pa[0] * (boosted - p_h_scores)  # extra mass moved from (0,0) tie to home win
        win_h += delta
        tie -= delta
        decided = win_h + win_a
        return win_h / decided if decided > 0 else 0.5

    # ------------------------------------------------------ core probability engine
    def _outcome_probs(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        mu_h0, mu_a0, info = self._team_means(ctx)
        D = self._num(ctx, "dispersion_index", None)
        if D is None or D <= 0:
            D = self.priors["dispersion_index"]
        sigma = self._prior(ctx, "strength_log_sigma")
        n_nodes = int(self._prior(ctx, "quadrature_nodes"))
        eps = self._prior(ctx, "tail_eps")
        unresolved = self._prior(ctx, "unresolved_tie_rate")

        nodes, weights = hermegauss(max(n_nodes, 1))
        weights = weights / weights.sum()

        P_h = P_d = P_a = 0.0
        tie_reg_total = 0.0
        for z, w in zip(nodes, weights):
            shift = 0.5 * sigma * z
            mu_h, mu_a = mu_h0 * math.exp(shift), mu_a0 * math.exp(-shift)
            r_h, r_a = self._nb_params(mu_h, D), self._nb_params(mu_a, D)
            upper = max(self._upper(mu_h, r_h, eps), self._upper(mu_a, r_a, eps))
            reg_h, reg_tie, reg_a = self._compare(self._pmf(mu_h, r_h, upper), self._pmf(mu_a, r_a, upper))
            p_xi = self._extra_inning_home_share(ctx, mu_h, mu_a, D)
            resolved = reg_tie * (1.0 - unresolved)
            P_h += w * (reg_h + resolved * p_xi)
            P_a += w * (reg_a + resolved * (1.0 - p_xi))
            P_d += w * (reg_tie * unresolved)
            tie_reg_total += w * reg_tie

        s = P_h + P_d + P_a
        P_h, P_d, P_a = P_h / s, P_d / s, P_a / s
        self.last_diagnostics = {
            **info,
            "mu_home": mu_h0,
            "mu_away": mu_a0,
            "dispersion_index": D,
            "p_regulation_tie": tie_reg_total,
            "p_home": P_h,
            "p_draw": P_d,
            "p_away": P_a,
        }
        return P_h, P_d, P_a

    # ------------------------------------------------------ public API
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, dict) else {}
        p_h, p_d, p_a = self._outcome_probs(ctx)
        return DiscreteDistribution(probabilities={"home": p_h, "draw": p_d, "away": p_a})

    def double_chance_probabilities(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        p_h, p_d, p_a = self._outcome_probs(game_context if isinstance(game_context, dict) else {})
        return {"1X": p_h + p_d, "X2": p_a + p_d, "12": p_h + p_a}

    def evaluate_double_chance(
        self, game_context: Dict[str, Any], odds: Dict[str, float]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Continuous valuation. No vig blending; the forecast is purely
        model-derived. A leg is eligible iff decimal odds >= min_decimal_odds
        and EV > 0.

        Note: '12' settles ~1 - P(unresolved tie). Its fair price sits near
        1.0, so it is almost never eligible at the >= 1.40 threshold. This is
        the intended behaviour, not a filter artifact.
        """
        probs = self.double_chance_probabilities(game_context)
        min_odds = self._prior(game_context if isinstance(game_context, dict) else {}, "min_decimal_odds")
        out: Dict[str, Dict[str, Any]] = {}
        for leg, p in probs.items():
            o = odds.get(leg) if isinstance(odds, dict) else None
            if not self._finite(o) or float(o) <= 1.0:
                continue
            o = float(o)
            ev = p * o - 1.0
            kelly = ev / (o - 1.0)
            out[leg] = {
                "prob": p,
                "fair_odds": (1.0 / p) if p > 0 else float("inf"),
                "decimal_odds": o,
                "ev": ev,
                "kelly_fraction": kelly,
                "eligible": bool(o >= min_odds and ev > 0.0),
            }
        return out