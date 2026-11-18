import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # DiscreteDistribution may be injected into execution scope instead
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NCAABaseballQuarter3SpreadsModel(BaseCandidateModel):
    """
    NCAA_BASEBALL | quarter_3 | spreads

    Segment mapping: 'quarter_3' is interpreted as the third quarter of a nine-inning
    game, i.e. the inning window where NCAA starters typically exit (innings ~5-6).

    Per-team segment runs follow a hurdle-compound process:
        P(X = 0)      = 1 - pi
        P(X = k | k>0) = ZTNB(k; nu, D)
    with
        pi = 1 - exp(-mu * kappa)
        kappa = kappa_D * exp(-phi_extra)
        kappa_D = ln(D) / (D - 1)          (the scoring-window fraction implied by the NB
                                             itself; reduces to 1 in the Poisson limit)
        phi_extra = bullpen clustering driven by the starter-exit hazard
    and nu solved so that pi * E[X | X > 0] = mu exactly (mean preserving).
    Because x / (1 - e^{-x}) > 1 and (D - 1) / ln D is the zero-mean limit of the ZTNB
    conditional mean, a root always exists; no clamps are needed.

    The margin pmf comes from exact convolution of the two team pmfs. The structural
    home edge embedded in the empirical baseline is shrunk toward a domain prior by
    credibility n / (n + k), so home-field confidence grows only with settled evidence.
    """

    # ---- Empirical anchors (segment autopsy) ----
    BASE_HOME_MEAN = 0.40
    BASE_AWAY_MEAN = 0.30
    OBSERVED_DISPERSION_INDEX = 1.0
    SETTLED_SAMPLE = 0

    # ---- Domain priors (configurable; each enters through a continuous function) ----
    PRIOR_DISPERSION_INDEX = 2.0          # NCAA two-inning run var/mean (aluminum bats, walks, errors)
    DISPERSION_PSEUDOCOUNT = 200.0        # credibility k for the dispersion index
    PRIOR_HFA_LOG_RATIO = 0.08            # college-baseball structural home scoring edge per segment
    HFA_PSEUDOCOUNT = 400.0               # credibility k for the home edge
    RATING_POOLING_EXPONENT = 0.6         # partial pooling of noisy attack/defense ratings
    PITCHES_PER_INNING = 17.0             # NCAA starter workload prior
    INNINGS_BEFORE_SEGMENT = 4.0
    EXIT_PITCH_MIDPOINT = 85.0            # logistic starter-exit hazard midpoint
    EXIT_PITCH_SCALE = 12.0
    BULLPEN_RUN_PREMIUM = 0.15            # log-run uplift when the bullpen is fully exposed
    BULLPEN_CLUSTER_INTENSITY = 0.35      # extra hurdle clustering when the bullpen is fully exposed
    REST_FATIGUE_AMPLITUDE = 0.08         # log-run uplift from a fully depleted bullpen
    REST_RECOVERY_TAU = 2.0               # days
    REST_REFERENCE_DAYS = 3.0
    TEMP_LOG_SLOPE = 0.002                # per degree F
    TEMP_REFERENCE_F = 70.0
    WIND_OUT_LOG_SLOPE = 0.006            # per mph of wind blowing out (when direction is supplied)
    TAIL_MASS = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_quarter_3_1108_105557",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "quarter_3",
        family: str = "spreads",
    ) -> None:
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for key in keys:
            if key in ctx:
                v = self._num(ctx[key])
                if v is not None:
                    return v
        return None

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        """Multiplicative rating (1.0 = average). Accepts nested dicts or flat keys."""
        nested = ctx.get(side)
        value = None
        if isinstance(nested, dict):
            for key in (kind, f"{kind}_rating", f"{kind}_strength"):
                value = self._num(nested.get(key))
                if value is not None:
                    break
        if value is None:
            value = self._get(ctx, f"{side}_{kind}", f"{side}_{kind}_rating", f"{side}_{kind}_strength")
        if value is None or value <= 0.0:
            return 1.0
        return value ** self.RATING_POOLING_EXPONENT

    def _sample_size(self, ctx: Dict[str, Any]) -> float:
        n = self._get(ctx, "settled_sample", "segment_sample_size")
        return max(n, 0.0) if n is not None else float(self.SETTLED_SAMPLE)

    def _dispersion_index(self, n: float) -> float:
        w = n / (n + self.DISPERSION_PSEUDOCOUNT)
        return 1.0 + (1.0 - w) * (self.PRIOR_DISPERSION_INDEX - 1.0) + w * (self.OBSERVED_DISPERSION_INDEX - 1.0)

    def _exit_hazard(self, ctx: Dict[str, Any], pitching_side: str) -> float:
        pc = self._get(ctx, f"{pitching_side}_starter_pitch_count", f"{pitching_side}_pitch_count")
        if pc is None:
            ppi = self._get(ctx, f"{pitching_side}_starter_pitches_per_inning") or self.PITCHES_PER_INNING
            pc = ppi * self.INNINGS_BEFORE_SEGMENT
        z = (pc - self.EXIT_PITCH_MIDPOINT) / self.EXIT_PITCH_SCALE
        return 1.0 / (1.0 + math.exp(-z))

    def _rest_log_effect(self, ctx: Dict[str, Any], pitching_side: str) -> float:
        rest = self._get(ctx, f"{pitching_side}_bullpen_rest_days", f"{pitching_side}_rest_days")
        if rest is None or rest < 0.0:
            return 0.0
        tau = self.REST_RECOVERY_TAU
        return self.REST_FATIGUE_AMPLITUDE * (math.exp(-rest / tau) - math.exp(-self.REST_REFERENCE_DAYS / tau))

    def _environment_log_effect(self, ctx: Dict[str, Any]) -> float:
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        dome = weather.get("is_dome", ctx.get("is_dome", False))
        if bool(dome):
            return 0.0
        effect = 0.0
        temp = self._num(weather.get("temp", weather.get("temperature")))
        if temp is not None:
            effect += self.TEMP_LOG_SLOPE * (temp - self.TEMP_REFERENCE_F)
        wind = self._num(weather.get("wind_speed"))
        wind_out = self._num(weather.get("wind_out_component"))
        if wind_out is not None:
            effect += self.WIND_OUT_LOG_SLOPE * wind_out
        elif wind is not None:
            direction = str(weather.get("wind_direction", "")).lower()
            if direction in ("out", "blowing_out", "out_to_cf"):
                effect += self.WIND_OUT_LOG_SLOPE * wind
            elif direction in ("in", "blowing_in", "in_from_cf"):
                effect -= self.WIND_OUT_LOG_SLOPE * wind
        return effect

    # ------------------------------------------------------------------ means
    def _segment_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float, float]:
        he = self._get(ctx, "home_expected")
        ae = self._get(ctx, "away_expected")
        he = he if he is not None and he > 0.0 else self.BASE_HOME_MEAN
        ae = ae if ae is not None and ae > 0.0 else self.BASE_AWAY_MEAN

        log_h = math.log(he) + math.log(self._rating(ctx, "home", "attack")) - math.log(self._rating(ctx, "away", "defense"))
        log_a = math.log(ae) + math.log(self._rating(ctx, "away", "attack")) - math.log(self._rating(ctx, "home", "defense"))

        # Credibility-shrink the structural home edge embedded in the empirical baseline.
        n = self._sample_size(ctx)
        w = n / (n + self.HFA_PSEUDOCOUNT)
        base_edge = math.log(self.BASE_HOME_MEAN / self.BASE_AWAY_MEAN)
        eff_edge = w * base_edge + (1.0 - w) * self.PRIOR_HFA_LOG_RATIO
        correction = 0.5 * (base_edge - eff_edge)
        log_h -= correction
        log_a += correction

        # Home batters face the away staff and vice versa.
        hz_away_staff = self._exit_hazard(ctx, "away")
        hz_home_staff = self._exit_hazard(ctx, "home")
        env = self._environment_log_effect(ctx)
        log_h += self.BULLPEN_RUN_PREMIUM * hz_away_staff + self._rest_log_effect(ctx, "away") + env
        log_a += self.BULLPEN_RUN_PREMIUM * hz_home_staff + self._rest_log_effect(ctx, "home") + env

        return math.exp(log_h), math.exp(log_a), hz_away_staff, hz_home_staff

    # ------------------------------------------------------------------ team pmf
    def _team_pmf(self, mu: float, hazard: float, D: float) -> np.ndarray:
        poisson_limit = (D - 1.0) < 1e-9
        kappa_d = 1.0 if poisson_limit else math.log(D) / (D - 1.0)
        kappa = kappa_d * math.exp(-self.BULLPEN_CLUSTER_INTENSITY * hazard)
        pi = -math.expm1(-mu * kappa)
        m_c = mu / pi  # required conditional mean given scoring

        if poisson_limit:
            def p0(nu: float) -> float:
                return math.exp(-nu)
        else:
            def p0(nu: float) -> float:
                return D ** (-nu / (D - 1.0))

        def gap(nu: float) -> float:
            return nu / (1.0 - p0(nu)) - m_c

        lo = 1e-10
        hi = m_c  # g(nu) >= nu, so g(m_c) >= m_c
        nu = optimize.brentq(gap, lo, hi, xtol=1e-14, rtol=1e-12) if gap(lo) < 0.0 else lo

        if poisson_limit:
            parent = stats.poisson(nu)
        else:
            parent = stats.nbinom(nu / (D - 1.0), 1.0 / D)
        kmax = int(parent.isf(self.TAIL_MASS)) + 2
        k = np.arange(kmax + 1)
        parent_pmf = parent.pmf(k)
        trunc = parent_pmf.copy()
        trunc[0] = 0.0
        trunc /= (1.0 - p0(nu))
        pmf = pi * trunc
        pmf[0] = 1.0 - pi
        return pmf / pmf.sum()

    # ------------------------------------------------------------------ public API
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        lam_h, lam_a, hz_h_bat, hz_a_bat = self._segment_means(game_context)
        D = self._dispersion_index(self._sample_size(game_context))
        pmf_h = self._team_pmf(lam_h, hz_h_bat, D)
        pmf_a = self._team_pmf(lam_a, hz_a_bat, D)
        return np.outer(pmf_h, pmf_a)

    def margin_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        mat = self.score_matrix(game_context)
        out: Dict[int, float] = {}
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                out[i - j] = out.get(i - j, 0.0) + float(mat[i, j])
        return out

    def price_spread(self, game_context: Dict[str, Any], home_line: float) -> Dict[str, float]:
        """Home covers if margin + home_line > 0; explicit push mass on integer lines."""
        cover = push = 0.0
        for m, p in self.margin_pmf(game_context).items():
            adj = m + home_line
            if abs(adj) < 1e-12:
                push += p
            elif adj > 0.0:
                cover += p
        return {"home_cover": cover, "push": push, "away_cover": 1.0 - cover - push}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mat = self.score_matrix(game_context)
        p_home = float(np.tril(mat, -1).sum())
        p_draw = float(np.trace(mat))
        p_away = float(np.triu(mat, 1).sum())
        total = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total

        scores = {
            (i, j): float(mat[i, j])
            for i in range(mat.shape[0])
            for j in range(mat.shape[1])
            if mat[i, j] > self.TAIL_MASS
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            try:
                return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
            except Exception:
                return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})