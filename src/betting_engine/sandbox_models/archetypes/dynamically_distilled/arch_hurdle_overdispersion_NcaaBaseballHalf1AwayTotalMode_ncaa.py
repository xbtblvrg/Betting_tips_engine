import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats
from scipy import special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live beside the base class or be pre-loaded in scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - fall back to the execution-scope primitive
    pass


class NcaaBaseballHalf1AwayTotalModel(BaseCandidateModel):
    """
    NCAA_BASEBALL | half_1 (innings 1-5) | away_total

    Generative structure
    --------------------
    The away team bats the top of innings 1..5. That is five guaranteed plate
    appearance windows with no walk-off truncation. Away first-half runs are
    modeled as

        R_away = sum_{i=1..5} X_i,   X_i | G ~ Hurdle-ZTNB(lambda_i * G, D_inn, k)

    * Hurdle-ZTNB per inning: P(X_i = 0) = 1 - pi_i, and X_i | X_i > 0 follows a
      zero-truncated negative binomial (mean mu, size k). Given the target inning
      mean lambda and inning dispersion index D, (pi, mu) are solved in closed form:
          E[X^2] / E[X] = D + lambda   and   m2/m1 = 1 + mu (1 + 1/k)
          => mu = (D + lambda - 1) / (1 + 1/k),   pi = lambda / E[ZTNB(mu,k)]
      This captures "crooked number" clustering, which BBCOR bats, high BB/HBP
      rates, and starter blow-ups produce.
    * Inning-rate shape lambda_i: lineup-slot quality cycles through the order
      (batters per inning = 3 / (1 - OBP)). The opposing pitching rate blends
      starter and bullpen through the starter-survival curve S_i, with a
      times-through-the-order penalty on the starter. Starter depth and quality
      depend on series slot (Fri/Sat/Sun/midweek) and on the defending team's rest.
    * Game-level frailty G (mean 1, lognormal via Gauss-Hermite) represents
      uncertainty in the team-strength expectation. It induces positive
      cross-inning correlation. The total half-game dispersion is decomposed as
          D_half = D_inn + sigma^2 * Lambda
      so the inning-level dispersion is whatever is left after frailty.
    * Unit resolution: the supplied anchors (home 0.80 / away 0.60) are implausible
      as half-game means. The units of the supplied expectations (per-inning,
      half-game, or full-game) are resolved by maximum plausibility against an NCAA
      D-I half-game run prior. They are never assumed blindly.

    The home team is modeled with the same machinery, so a full (home, away) score
    lattice is emitted. The away marginal is the priced object.
    """

    # ------------------------------------------------------------------ #
    # Domain priors (NCAA D-I). These are structural constants describing #
    # the sport, not clamps on outputs.                                   #
    # ------------------------------------------------------------------ #
    N_INNINGS = 5
    FULL_GAME_INNINGS = 9
    TEAM_RUNS_PER_GAME = 6.1             # D-I BBCOR era, runs/team/game
    FIRST_HALF_SHARE = 0.555             # share of runs in innings 1-5 (top of order, starters' blowups)
    HALF_MEAN_LOG_SD = 0.45              # cross-game spread of team half-game means (log scale)
    EMPIRICAL_HOME_ANCHOR = 0.80         # supplied anchor (per-inning after resolution)
    EMPIRICAL_AWAY_ANCHOR = 0.60

    HALF_DISPERSION_PRIOR = 1.90         # Var/Mean of half-game team runs (domain: 1.6-2.2)
    DISPERSION_PRIOR_WEIGHT = 150.0      # pseudo-games behind the dispersion prior
    ZTNB_SIZE = 1.5                      # tail shape of runs-given-scoring (near geometric)
    EXPECTATION_CV = 0.18                # default uncertainty of a supplied team expectation
    MIN_INNING_EXCESS_DISPERSION = 0.05  # numeric floor keeping D_inn strictly > 1

    LEAGUE_OBP = 0.385
    LINEUP_SLOT_SPREAD = 0.24            # slot 1 vs slot 9 run-value gap (relative)
    TTO_PENALTY = 0.09                   # starter run-rate increase per pass through the order

    # Series slot -> (expected starter innings, starter run rate relative to bullpen)
    SLOT_PROFILE = {
        "friday": (5.8, 0.84),
        "saturday": (5.2, 0.94),
        "sunday": (4.6, 1.03),
        "midweek": (3.3, 1.10),
        "unknown": (4.8, 0.98),
    }
    SLOT_DISPERSION_SCALE = {"friday": 0.95, "saturday": 1.0, "sunday": 1.04, "midweek": 1.10, "unknown": 1.0}
    STARTER_EXIT_SD = 1.4                # innings, sd of starter exit point
    REST_DEPTH_GAIN = 0.35               # innings of extra starter depth at saturated rest
    REST_SCALE_DAYS = 3.0
    SHORT_REST_BULLPEN_PENALTY = 0.06    # bullpen run-rate inflation on <=1 rest day

    TEMP_REF_F = 70.0
    TEMP_LOG_COEF = 0.004                # log-run change per degree F
    WIND_OUT_LOG_COEF = 0.012            # log-run change per mph blowing out (signed)
    WIND_DISPERSION_COEF = 0.01          # unsigned wind adds variance only

    RESIDUAL_FEATURE_WEIGHT = 0.25       # rating features are mostly already in *_expected
    FRAILTY_NODES = 15
    MAX_RUNS_PER_INNING = 18
    MAX_RUNS_HALF = 32
    MIN_ELIGIBLE_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_half_1_1108_060747",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "half_1",
        family: str = "away_total",
    ):
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        except TypeError:
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family
        gh_x, gh_w = special.roots_hermitenorm(self.FRAILTY_NODES)
        self._gh_z = np.asarray(gh_x, dtype=float)
        self._gh_w = np.asarray(gh_w, dtype=float) / float(np.sum(gh_w))

    # ================================================================== #
    # Public API                                                          #
    # ================================================================== #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        home_pmf, away_pmf = self._team_marginals(ctx)
        scores: Dict[Tuple[int, int], float] = {}
        joint = np.outer(home_pmf, away_pmf)
        joint /= joint.sum()
        for h in range(joint.shape[0]):
            for a in range(joint.shape[1]):
                p = float(joint[h, a])
                if p > 1e-13:
                    scores[(h, a)] = p
        total = sum(scores.values())
        scores = {key: val / total for key, val in scores.items()}
        return DiscreteDistribution.from_scores(scores)  # noqa: F821

    def away_total_probabilities(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Exact over/under/push probabilities at a given away first-half total line."""
        _, away_pmf = self._team_marginals(game_context or {})
        runs = np.arange(away_pmf.size)
        over = float(away_pmf[runs > line].sum())
        under = float(away_pmf[runs < line].sum())
        push = float(away_pmf[np.isclose(runs, line)].sum())
        return {"over": over, "under": under, "push": push}

    def expected_value(self, p_win: float, decimal_odds: float, p_push: float = 0.0) -> Optional[float]:
        """EV per unit stake. Continuous valuation; eligibility only requires odds >= 1.40."""
        if decimal_odds is None or decimal_odds < self.MIN_ELIGIBLE_ODDS:
            return None
        p_loss = max(0.0, 1.0 - p_win - p_push)
        return p_win * (decimal_odds - 1.0) - p_loss

    # ================================================================== #
    # Core construction                                                   #
    # ================================================================== #
    def _team_marginals(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        home_raw, home_supplied = self._expectation(ctx, "home")
        away_raw, away_supplied = self._expectation(ctx, "away")
        scale = self._resolve_unit_scale(ctx, home_raw, away_raw)

        env = self._environment_multiplier(ctx)
        away_mean = away_raw * scale * env * self._matchup_multiplier(ctx, "away", "home", away_supplied)
        home_mean = home_raw * scale * env * self._matchup_multiplier(ctx, "home", "away", home_supplied)

        away_pmf = self._half_pmf(ctx, batting="away", pitching="home", half_mean=away_mean)
        home_pmf = self._half_pmf(ctx, batting="home", pitching="away", half_mean=home_mean)
        return home_pmf, away_pmf

    def _half_pmf(self, ctx: Dict[str, Any], batting: str, pitching: str, half_mean: float) -> np.ndarray:
        half_mean = max(half_mean, 1e-6)
        slot = self._series_slot(ctx, pitching)
        shape = self._inning_shape(ctx, batting, pitching, slot)
        lambdas = half_mean * shape / shape.sum()

        sigma = self._expectation_sd(ctx, batting)
        d_half = self._half_dispersion(ctx, slot)
        # Var_half = Lambda*D_inn + sigma^2*Lambda^2  ->  D_inn = D_half - sigma^2*Lambda
        frailty_var = math.expm1(sigma * sigma)  # exact variance of mean-1 lognormal
        d_inn = max(1.0 + self.MIN_INNING_EXCESS_DISPERSION, d_half - frailty_var * half_mean)

        out = np.zeros(self.MAX_RUNS_HALF + 1)
        for z, w in zip(self._gh_z, self._gh_w):
            g = math.exp(sigma * z - 0.5 * sigma * sigma)
            pmf = np.array([1.0])
            for lam in lambdas:
                pmf = np.convolve(pmf, self._inning_pmf(lam * g, d_inn))[: self.MAX_RUNS_HALF + 1]
            out[: pmf.size] += w * pmf
        out = np.clip(out, 0.0, None)
        return out / out.sum()

    def _inning_pmf(self, lam: float, d: float) -> np.ndarray:
        """Hurdle zero-truncated NB with mean lam and Var/Mean d (closed-form moment match)."""
        n = self.MAX_RUNS_PER_INNING
        k = self.ZTNB_SIZE
        lam = max(lam, 1e-9)
        mu = (d + lam - 1.0) / (1.0 + 1.0 / k)
        support = np.arange(n + 1)
        if mu > 0:
            p_nb = k / (k + mu)
            p0 = p_nb ** k
            m1 = mu / (1.0 - p0)
            pi = lam / m1
            if 0.0 < pi < 1.0:
                body = stats.nbinom.pmf(support, k, p_nb)
                body[0] = 0.0
                body /= body.sum()
                pmf = pi * body
                pmf[0] = 1.0 - pi
                return pmf
        # Moment system infeasible (very high inning rate): use an untruncated NB with matching moments.
        size = lam / max(d - 1.0, self.MIN_INNING_EXCESS_DISPERSION)
        pmf = stats.nbinom.pmf(support, size, size / (size + lam))
        return pmf / pmf.sum()

    # ================================================================== #
    # Inning-rate shape: lineup cycling x starter/bullpen blend           #
    # ================================================================== #
    def _inning_shape(self, ctx: Dict[str, Any], batting: str, pitching: str, slot: str) -> np.ndarray:
        obp = self._num(ctx.get(f"{batting}_obp"), self.LEAGUE_OBP)
        obp = min(max(obp, 0.2), 0.6)
        bpi = 3.0 / (1.0 - obp)  # expected batters per inning

        exp_ip, sp_ratio = self.SLOT_PROFILE[slot]
        rest = self._num(ctx.get(f"{pitching}_rest_days"), None)
        if rest is not None:
            exp_ip += self.REST_DEPTH_GAIN * math.tanh((rest - self.REST_SCALE_DAYS) / self.REST_SCALE_DAYS)
        sp_quality = self._num(ctx.get(f"{pitching}_starter_rating"), None)  # >1 = worse (allows more)
        if sp_quality is not None and sp_quality > 0:
            sp_ratio *= sp_quality
        bp_ratio = 1.0
        bp_quality = self._num(ctx.get(f"{pitching}_bullpen_rating"), None)
        if bp_quality is not None and bp_quality > 0:
            bp_ratio *= bp_quality
        if rest is not None and rest <= 1.0:
            bp_ratio *= 1.0 + self.SHORT_REST_BULLPEN_PENALTY

        shape = np.zeros(self.N_INNINGS)
        for i in range(1, self.N_INNINGS + 1):
            start, end = (i - 1) * bpi, i * bpi
            lineup_q = self._lineup_quality(start, end)
            survival = 1.0 - stats.norm.cdf((i - 0.5 - exp_ip) / self.STARTER_EXIT_SD)
            pass_num = math.floor(0.5 * (start + end) / 9.0)
            sp_rate = sp_ratio * (1.0 + self.TTO_PENALTY * pass_num)
            shape[i - 1] = lineup_q * (survival * sp_rate + (1.0 - survival) * bp_ratio)
        return shape

    def _lineup_quality(self, start: float, end: float) -> float:
        """Average run value of batting-order slots covered in [start, end) batters."""
        grid = np.linspace(start, end, 64, endpoint=False)
        slots = np.floor(grid).astype(int) % 9
        q = 1.0 + self.LINEUP_SLOT_SPREAD * (0.5 - slots / 8.0)
        return float(q.mean())

    # ================================================================== #
    # Expectation resolution                                              #
    # ================================================================== #
    def _expectation(self, ctx: Dict[str, Any], side: str) -> Tuple[float, bool]:
        val = self._num(ctx.get(f"{side}_expected"), None)
        if val is not None and val > 0:
            return val, True
        anchor = self.EMPIRICAL_HOME_ANCHOR if side == "home" else self.EMPIRICAL_AWAY_ANCHOR
        return anchor, False

    def _resolve_unit_scale(self, ctx: Dict[str, Any], home_raw: float, away_raw: float) -> float:
        candidates = {
            "per_inning": float(self.N_INNINGS),
            "half": 1.0,
            "full_game": self.FIRST_HALF_SHARE,
        }
        declared = str(ctx.get("expected_unit") or ctx.get("expectation_unit") or "").lower()
        for key in candidates:
            if declared and (declared == key or declared.replace("-", "_") == key):
                return candidates[key]
        if declared in ("game", "full", "match"):
            return candidates["full_game"]

        prior_half = self.TEAM_RUNS_PER_GAME * self.FIRST_HALF_SHARE
        anchor_sum = self.EMPIRICAL_HOME_ANCHOR + self.EMPIRICAL_AWAY_ANCHOR
        mu_home = math.log(prior_half * 2.0 * self.EMPIRICAL_HOME_ANCHOR / anchor_sum)
        mu_away = math.log(prior_half * 2.0 * self.EMPIRICAL_AWAY_ANCHOR / anchor_sum)
        best, best_ll = 1.0, -math.inf
        for factor in candidates.values():
            ll = stats.norm.logpdf(math.log(home_raw * factor), mu_home, self.HALF_MEAN_LOG_SD)
            ll += stats.norm.logpdf(math.log(away_raw * factor), mu_away, self.HALF_MEAN_LOG_SD)
            if ll > best_ll:
                best, best_ll = factor, ll
        return best

    def _matchup_multiplier(self, ctx: Dict[str, Any], batting: str, pitching: str, supplied: bool) -> float:
        omega = self.RESIDUAL_FEATURE_WEIGHT if supplied else 1.0
        attack = self._rating(ctx, batting, "attack")
        defense = self._rating(ctx, pitching, "defense")
        pace = self._ratio(self._num(ctx.get("pace", ctx.get("tempo")), None))
        park = self._ratio(self._num(ctx.get("park_factor"), None))
        return float((attack * defense * pace * park) ** omega)

    def _environment_multiplier(self, ctx: Dict[str, Any]) -> float:
        if bool(ctx.get("is_dome")):
            return 1.0
        log_m = 0.0
        temp = self._num(ctx.get("temp", ctx.get("temperature")), None)
        if temp is not None:
            if str(ctx.get("temp_unit", "F")).upper().startswith("C"):
                temp = temp * 9.0 / 5.0 + 32.0
            log_m += self.TEMP_LOG_COEF * (temp - self.TEMP_REF_F)
        wind_out = self._num(ctx.get("wind_out_mph", ctx.get("wind_out")), None)
        if wind_out is not None:
            log_m += self.WIND_OUT_LOG_COEF * wind_out
        return math.exp(log_m)

    def _half_dispersion(self, ctx: Dict[str, Any], slot: str) -> float:
        prior = self.HALF_DISPERSION_PRIOR * self.SLOT_DISPERSION_SCALE[slot]
        obs = self._num(ctx.get("dispersion_index"), None)
        n_obs = self._num(ctx.get("dispersion_sample_size", ctx.get("sample_size")), 0.0) or 0.0
        if obs is not None and obs > 0 and n_obs > 0:
            prior = (n_obs * obs + self.DISPERSION_PRIOR_WEIGHT * prior) / (n_obs + self.DISPERSION_PRIOR_WEIGHT)
        if not bool(ctx.get("is_dome")) and ctx.get("wind_out_mph") is None and ctx.get("wind_out") is None:
            wind = self._num(ctx.get("wind_speed"), None)
            if wind is not None and wind > 0:
                prior += self.WIND_DISPERSION_COEF * wind  # direction unknown: variance, not mean
        return prior

    def _expectation_sd(self, ctx: Dict[str, Any], side: str) -> float:
        sd = self._num(ctx.get(f"{side}_expected_sd"), None)
        mean = self._num(ctx.get(f"{side}_expected"), None)
        if sd is not None and mean is not None and mean > 0 and sd >= 0:
            cv = sd / mean
        else:
            cv = self.EXPECTATION_CV
        return math.sqrt(math.log1p(cv * cv))

    def _series_slot(self, ctx: Dict[str, Any], pitching: str) -> str:
        raw = ctx.get(f"{pitching}_starter_slot", ctx.get("series_slot", ctx.get("day_of_week")))
        game_no = self._num(ctx.get("series_game"), None)
        if raw is not None:
            s = str(raw).strip().lower()
            for key in ("friday", "saturday", "sunday"):
                if s.startswith(key[:3]):
                    return key
            if s.startswith(("mon", "tue", "wed", "thu", "mid")):
                return "midweek"
        if game_no is not None:
            return {1: "friday", 2: "saturday", 3: "sunday"}.get(int(round(game_no)), "midweek")
        return "unknown"

    # ================================================================== #
    # Parsing helpers                                                     #
    # ================================================================== #
    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        block = ctx.get(side)
        val = None
        if isinstance(block, dict):
            val = self._num(block.get(key), None)
        if val is None:
            val = self._num(ctx.get(f"{side}_{key}"), None)
        return self._ratio(val)

    @staticmethod
    def _ratio(val: Optional[float]) -> float:
        """Positive values are multiplicative (1 = average); non-positive are log-additive."""
        if val is None:
            return 1.0
        return float(val) if val > 0 else math.exp(float(val))

    @staticmethod
    def _num(val: Any, default: Optional[float]) -> Optional[float]:
        try:
            out = float(val)
        except (TypeError, ValueError):
            return default
        return out if math.isfinite(out) else default