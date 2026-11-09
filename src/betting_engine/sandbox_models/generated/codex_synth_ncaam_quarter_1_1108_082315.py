import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from numpy.polynomial.hermite_e import hermegauss
from scipy import integrate, optimize, special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - resolved from execution scope
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NCAAMQuarter1AwayTotalCompoundModel(BaseCandidateModel):
    """
    NCAAM first 10-minute segment ("quarter_1") away-total model.

    Structure
    ---------
    S_away = sum_{i=1..N} X_i with a shared possession count N for both teams.

    * N | pace: renewal-process count over 10 game minutes. Var(N | pace) ~ mu_N * CV_L^2,
      where CV_L is the coefficient of variation of possession length (counts in a fixed
      clock window are under-dispersed). Pace uncertainty (game-level tempo deviation
      and the first-segment pace ratio rho_1) is a lognormal mixture integrated by
      Gauss-Hermite quadrature.
    * X_i in {0,1,2,3,4}: built from turnover, shooting-foul, bonus-FT and field-goal
      branches. Offensive rebounds are treated as geometric second chances. And-ones are
      included.
    * Bonus FTs: NCAAM gives the 1-and-1 at the 7th team foul and the double bonus at
      the 10th, and fouls reset each half. The fraction of time the defense is in each
      bonus state is the integral of the Gamma(k, r) foul-arrival CDF. The segment
      [0, 10] min is integrated separately from the full-half average [0, 20], so FT
      damping comes from the structure rather than a constant.
    * Anchoring: the efficiency logit-shift theta is solved so that the full-game
      model mean equals the full-game away expectation. The same theta is then applied
      to the segment model with segment-specific bonus exposure, which removes the
      pro-rating FT bias.
    * Per-team game-level shooting form is a logit-normal random effect, which
      generates overdispersion beyond possession-level lumpiness.
    * Cold start: a conjugate normal log-bias term starts at zero and is shrunk toward
      the prior as settled outcomes arrive (see `update_with_results`).
    """

    # ---- Domain priors (NCAA D-I structural averages; overridable via game_context) ----
    SEGMENT_MINUTES = 10.0
    GAME_MINUTES = 40.0
    HALF_MINUTES = 20.0
    BONUS_FOUL = 7            # 1-and-1 threshold (team fouls per half)
    DOUBLE_BONUS_FOUL = 10    # two-shot threshold

    PRIOR = {
        "possessions_per_game": 68.0,
        "turnover_rate": 0.175,
        "three_attempt_share": 0.39,
        "three_pct": 0.340,
        "two_pct": 0.510,
        "ft_pct": 0.715,
        "orb_rate": 0.29,
        "fouls_per_game": 17.5,
        "shooting_foul_share": 0.42,
        "and_one_rate_two": 0.060,
        "and_one_rate_three": 0.010,
        "possession_length_cv": 0.55,
        "pace_log_sd": 0.050,          # game-level tempo deviation from projection
        "rho1_mean": 1.0,              # first-segment pace ratio (weak prior at 1)
        "rho1_log_sd": 0.040,
        "efficiency_logit_sd": 0.11,   # per-team game-level shooting form
        "rest_log_coef": 0.015,        # log-linear rest effect on efficiency target
        "bias_prior_sd": 0.05,         # cold-start shrinkage prior on log bias
        "home_anchor_segment": 20.00,
        "away_anchor_segment": 18.75,
    }

    QUAD_NODES_PACE = 7
    QUAD_NODES_EFF = 7
    TAIL_MASS = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_1_1108_082315",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_1",
        family: str = "away_total",
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.segment_share = self.SEGMENT_MINUTES / self.GAME_MINUTES
        # Cold-start conjugate state for the log bias of the segment away mean.
        self._bias_mean = 0.0
        self._bias_var = self.PRIOR["bias_prior_sd"] ** 2
        self._n_settled = 0
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _team_get(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict) and key in team:
            return self._num(team.get(key))
        return self._num(ctx.get(f"{side}_{key}"))

    def _param(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        v = self._team_get(ctx, side, key)
        return v if v is not None else self.PRIOR[key]

    def _scale_to_full_game(self, value: float, anchor_segment: float) -> float:
        """
        Inputs may be quoted on segment scale (pro-rated) or full-game scale.
        The decision boundary is the geometric midpoint between the two scales
        implied by the anchor, which is derived and not an arbitrary cut.
        """
        full_anchor = anchor_segment / self.segment_share
        boundary = math.sqrt(anchor_segment * full_anchor)
        return value if value >= boundary else value / self.segment_share

    def _exposure_multiplier(self, ctx: Dict[str, Any], off: str, deff: str) -> float:
        def as_mult(v: Optional[float]) -> Optional[float]:
            if v is None or v <= 0:
                return None
            # Ratings quoted per-100-possession are normalized by their own scale.
            return v / 100.0 if v > 10.0 else v

        a = as_mult(self._team_get(ctx, off, "attack"))
        d = as_mult(self._team_get(ctx, deff, "defense"))
        m = 1.0
        if a is not None:
            m *= a
        if d is not None:
            m *= d
        return m

    def _rest_multiplier(self, ctx: Dict[str, Any], side: str, other: str) -> float:
        r_s = self._num(ctx.get(f"{side}_rest_days"))
        r_o = self._num(ctx.get(f"{other}_rest_days"))
        if r_s is None or r_o is None:
            return 1.0
        diff = math.log1p(max(r_s, 0.0)) - math.log1p(max(r_o, 0.0))
        return math.exp(self.PRIOR["rest_log_coef"] * diff)

    def _full_game_target(self, ctx: Dict[str, Any], side: str, other: str) -> float:
        anchor = self.PRIOR[f"{side}_anchor_segment"]
        raw = self._num(ctx.get(f"{side}_expected"))
        if raw is not None and raw > 0:
            full = self._scale_to_full_game(raw, anchor)
        else:
            full = (anchor / self.segment_share) * self._exposure_multiplier(ctx, side, other)
        return full * self._rest_multiplier(ctx, side, other)

    def _possessions_per_game(self, ctx: Dict[str, Any]) -> float:
        lg = self._num(ctx.get("league_tempo")) or self.PRIOR["possessions_per_game"]
        th = self._team_get(ctx, "home", "tempo")
        ta = self._team_get(ctx, "away", "tempo")
        if th is not None and ta is not None and th > 0 and ta > 0:
            return th * ta / lg
        pace = self._num(ctx.get("pace")) or self._num(ctx.get("tempo"))
        return pace if pace is not None and pace > 0 else lg

    # ------------------------------------------------------------------ #
    # Foul / bonus mechanics
    # ------------------------------------------------------------------ #
    def _bonus_time_fractions(self, fouls_per_game: float, horizon: float) -> Tuple[float, float]:
        """Average fraction of [0, horizon] (from half start) spent in 1-and-1 / double bonus."""
        r = fouls_per_game / self.GAME_MINUTES

        def time_at_least(k: int) -> float:
            val, _ = integrate.quad(lambda t: special.gammainc(k, r * t), 0.0, horizon)
            return val

        t7 = time_at_least(self.BONUS_FOUL)
        t10 = time_at_least(self.DOUBLE_BONUS_FOUL)
        return (t7 - t10) / horizon, t10 / horizon

    # ------------------------------------------------------------------ #
    # Per-possession scoring distribution
    # ------------------------------------------------------------------ #
    def _possession_rates(
        self, ctx: Dict[str, Any], off: str, deff: str, poss_game: float, horizon: float
    ) -> Dict[str, float]:
        fouls = self._param(ctx, deff, "fouls_per_game")
        s_share = self._param(ctx, deff, "shooting_foul_share")
        f1, f2 = self._bonus_time_fractions(fouls, horizon)
        per_poss_fouls = fouls / poss_game
        p_sf = s_share * per_poss_fouls
        p_bonus_total = (1.0 - s_share) * per_poss_fouls * (f1 + f2)
        w_double = f2 / (f1 + f2) if (f1 + f2) > 0 else 0.0
        return {
            "p_to": self._param(ctx, off, "turnover_rate"),
            "p_sf": p_sf,
            "p_b": p_bonus_total,
            "w_double": w_double,
            "t3": self._param(ctx, off, "three_attempt_share"),
            "p3": self._param(ctx, off, "three_pct"),
            "p2": self._param(ctx, off, "two_pct"),
            "ft": self._param(ctx, off, "ft_pct"),
            "orb": self._param(ctx, off, "orb_rate"),
            "a2": self.PRIOR["and_one_rate_two"],
            "a3": self.PRIOR["and_one_rate_three"],
        }

    @staticmethod
    def _possession_pmf(theta: float, r: Dict[str, float]) -> np.ndarray:
        p3 = special.expit(special.logit(r["p3"]) + theta)
        p2 = special.expit(special.logit(r["p2"]) + theta)
        ft = r["ft"]
        make3 = r["t3"] * p3
        make2 = (1.0 - r["t3"]) * p2
        miss = 1.0 - make3 - make2
        denom = 1.0 - miss * r["orb"]  # geometric offensive-rebound second chances

        shot = np.zeros(5)
        shot[0] = miss * (1.0 - r["orb"]) / denom
        shot[2] = make2 * (1.0 - r["a2"] * ft) / denom
        shot[3] = (make3 * (1.0 - r["a3"] * ft) + make2 * r["a2"] * ft) / denom
        shot[4] = make3 * r["a3"] * ft / denom

        two_ft = np.array([(1 - ft) ** 2, 2 * ft * (1 - ft), ft ** 2, 0.0, 0.0])
        one_and_one = np.array([1 - ft, ft * (1 - ft), ft ** 2, 0.0, 0.0])
        bonus = r["w_double"] * two_ft + (1.0 - r["w_double"]) * one_and_one
        turnover = np.array([1.0, 0.0, 0.0, 0.0, 0.0])

        p_to, p_sf, p_b = r["p_to"], r["p_sf"], r["p_b"]
        non_fga = p_to + p_sf + p_b
        if non_fga >= 1.0:  # numerical guard for degenerate inputs: renormalize branches
            p_to, p_sf, p_b = (p_to / non_fga, p_sf / non_fga, p_b / non_fga)
            non_fga = 1.0
        pmf = p_to * turnover + p_sf * two_ft + p_b * bonus + (1.0 - non_fga) * shot
        return pmf / pmf.sum()

    def _eff_nodes(self) -> Tuple[np.ndarray, np.ndarray]:
        z, w = hermegauss(self.QUAD_NODES_EFF)
        return z * self.PRIOR["efficiency_logit_sd"], w / w.sum()

    def _mixture_ppp(self, theta: float, rates: Dict[str, float]) -> float:
        dz, w = self._eff_nodes()
        support = np.arange(5)
        return float(sum(wk * self._possession_pmf(theta + d, rates) @ support for d, wk in zip(dz, w)))

    def _solve_theta(self, target_mean: float, n_poss: float, rates: Dict[str, float]) -> float:
        f = lambda th: n_poss * self._mixture_ppp(th, rates) - target_mean
        lo, hi = -1.0, 1.0
        while f(lo) > 0 and lo > -100.0:
            lo *= 2.0
        while f(hi) < 0 and hi < 100.0:
            hi *= 2.0
        if f(lo) > 0:
            return lo
        if f(hi) < 0:
            return hi
        return optimize.brentq(f, lo, hi, xtol=1e-10)

    # ------------------------------------------------------------------ #
    # Possession-count distribution for the segment
    # ------------------------------------------------------------------ #
    def _possession_count_pmf(self, mu_seg: float) -> np.ndarray:
        cv_l = self.PRIOR["possession_length_cv"]
        log_sd = math.hypot(self.PRIOR["pace_log_sd"], self.PRIOR["rho1_log_sd"])
        z, w = hermegauss(self.QUAD_NODES_PACE)
        w = w / w.sum()
        mus = mu_seg * self.PRIOR["rho1_mean"] * np.exp(z * log_sd - 0.5 * log_sd ** 2)
        sig_max = math.sqrt(mus.max() * cv_l ** 2)
        n_max = int(math.ceil(mus.max() + 10.0 * sig_max)) + 1
        n = np.arange(n_max + 1)
        pmf = np.zeros(n_max + 1)
        for m, wk in zip(mus, w):
            s = math.sqrt(m * cv_l ** 2)
            cdf_hi = special.ndtr((n + 0.5 - m) / s)
            cdf_lo = special.ndtr((n - 0.5 - m) / s)
            comp = cdf_hi - cdf_lo
            pmf += wk * comp / comp.sum()
        return pmf / pmf.sum()

    # ------------------------------------------------------------------ #
    # Compound convolution
    # ------------------------------------------------------------------ #
    def _compound_powers(self, theta: float, rates: Dict[str, float], n_max: int) -> np.ndarray:
        """Row n = efficiency-mixed distribution of points scored over n possessions."""
        length = 4 * n_max + 1
        out = np.zeros((n_max + 1, length))
        dz, w = self._eff_nodes()
        for d, wk in zip(dz, w):
            pmf = self._possession_pmf(theta + d, rates)
            cur = np.array([1.0])
            out[0, 0] += wk
            for k in range(1, n_max + 1):
                cur = np.convolve(cur, pmf)
                out[k, : cur.size] += wk * cur
        return out

    # ------------------------------------------------------------------ #
    # Cold-start shrinkage
    # ------------------------------------------------------------------ #
    def update_with_results(self, results: List[Dict[str, float]]) -> None:
        """
        Conjugate normal update of the log bias using settled segment outcomes.
        Each item needs 'predicted_mean', 'predicted_var' (optional) and 'actual'.
        """
        for r in results:
            mu = self._num(r.get("predicted_mean"))
            y = self._num(r.get("actual"))
            if mu is None or y is None or mu <= 0:
                continue
            var = self._num(r.get("predicted_var")) or mu
            obs = math.log((y + 0.5) / (mu + 0.5))
            obs_var = var / (mu + 0.5) ** 2
            post_prec = 1.0 / self._bias_var + 1.0 / obs_var
            self._bias_mean = (self._bias_mean / self._bias_var + obs / obs_var) / post_prec
            self._bias_var = 1.0 / post_prec
            self._n_settled += 1

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def _team_segment_spec(
        self, ctx: Dict[str, Any], off: str, deff: str, poss_game: float, mu_seg: float, bias: float
    ) -> Tuple[float, Dict[str, float], float]:
        target_full = self._full_game_target(ctx, off, deff)
        rates_full = self._possession_rates(ctx, off, deff, poss_game, self.HALF_MINUTES)
        theta = self._solve_theta(target_full, poss_game, rates_full)
        rates_seg = self._possession_rates(ctx, off, deff, poss_game, self.SEGMENT_MINUTES)
        if bias != 0.0:
            seg_mean = mu_seg * self._mixture_ppp(theta, rates_seg) * math.exp(bias)
            theta = self._solve_theta(seg_mean, mu_seg, rates_seg)
        return theta, rates_seg, target_full

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        poss_game = self._possessions_per_game(ctx)
        mu_seg = poss_game * self.segment_share

        theta_a, rates_a, full_a = self._team_segment_spec(
            ctx, "away", "home", poss_game, mu_seg, self._bias_mean
        )
        theta_h, rates_h, full_h = self._team_segment_spec(ctx, "home", "away", poss_game, mu_seg, 0.0)

        n_pmf = self._possession_count_pmf(mu_seg)
        n_max = n_pmf.size - 1
        pow_a = self._compound_powers(theta_a, rates_a, n_max)
        pow_h = self._compound_powers(theta_h, rates_h, n_max)

        # Shared possession count induces positive home/away correlation.
        joint = (pow_h * n_pmf[:, None]).T @ pow_a
        joint /= joint.sum()

        marg_h, marg_a = joint.sum(axis=1), joint.sum(axis=0)
        cut_h = int(np.searchsorted(np.cumsum(marg_h), 1.0 - self.TAIL_MASS)) + 1
        cut_a = int(np.searchsorted(np.cumsum(marg_a), 1.0 - self.TAIL_MASS)) + 1
        joint = joint[:cut_h, :cut_a]
        joint /= joint.sum()

        pts_a = np.arange(joint.shape[1])
        pa = joint.sum(axis=0)
        mean_a = float(pa @ pts_a)
        var_a = float(pa @ (pts_a - mean_a) ** 2)
        self.last_diagnostics = {
            "possessions_full_game": poss_game,
            "possessions_segment_mean": float(n_pmf @ np.arange(n_pmf.size)),
            "away_full_game_target": full_a,
            "home_full_game_target": full_h,
            "away_segment_mean": mean_a,
            "away_segment_prorated": full_a * self.segment_share,
            "away_segment_var": var_a,
            "away_dispersion_index": var_a / mean_a if mean_a > 0 else float("nan"),
            "theta_away": theta_a,
            "theta_home": theta_h,
            "bias_log_posterior_mean": self._bias_mean,
            "bias_log_posterior_sd": math.sqrt(self._bias_var),
            "n_settled": self._n_settled,
        }
        return self._to_distribution(joint)

    @staticmethod
    def _to_distribution(joint: np.ndarray) -> "DiscreteDistribution":
        scores = {
            (int(h), int(a)): float(joint[h, a])
            for h in range(joint.shape[0])
            for a in range(joint.shape[1])
            if joint[h, a] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(joint)