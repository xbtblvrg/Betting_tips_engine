"""
Candidate model for BUNDESLIGA | full_game | winner_total.

Architecture (derived from the mechanics of association football scoring):

1. Goal arrival is a near-Poisson counting process (dispersion index 1.0),
   so each side's goals are modeled as Poisson with a rate built on a
   log-linear decomposition:
       log(lambda) = log(base expectation) + attack/defense exposure
                     + rest asymmetry + tempo/environment.
   The decomposition is anchored on the empirical league means
   (home 1.60, away 1.20) when no expectation is supplied.

2. Low-score dependence (0-0, 1-0, 0-1, 1-1) is corrected with a
   Dixon-Coles tau kernel. This matters most for draw mass. If the context
   supplies a league draw-rate prior, rho is solved for numerically so the
   kernel reproduces it. Otherwise a weak negative prior is used.

3. Epistemic uncertainty is integrated out rather than plugged in. This is
   the structural guard against home-field over-confidence.
     - A common "tempo" factor scales both rates. It drives total-goals
       variance and is calibrated from the dispersion index plus a
       sample-size-dependent model-uncertainty term.
     - A "supremacy" factor moves the rates in opposite directions. It
       drives winner uncertainty and shrinks with observed sample size,
       so a cold start yields wider margins.
   Both factors are mean-preserving lognormals, integrated with
   Gauss-Hermite quadrature.

4. The output is the full score lattice, so the winner (1X2) and total
   markets are mutually consistent.

All tunable quantities are named, documented class-level priors. They can
be overridden through game_context. There are no static probability or
odds clamps.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded; import defensively.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class BundesligaFullGameWinnerTotalModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Empirical anchors and structural priors (overridable via context).
    # ------------------------------------------------------------------ #
    PRIOR_HOME_MEAN: float = 1.60          # empirical league home goals/game
    PRIOR_AWAY_MEAN: float = 1.20          # empirical league away goals/game
    PRIOR_DISPERSION_INDEX: float = 1.0    # Var/Mean of goal counts
    PRIOR_RHO: float = -0.04               # weak Dixon-Coles dependence prior

    # Exposure weights. When an expectation is supplied it already embeds
    # team strength, so the ratings are partially pooled (half-weight)
    # to avoid double counting.
    EXPOSURE_WEIGHT_WITH_EXPECTATION: float = 0.5
    EXPOSURE_WEIGHT_WITHOUT_EXPECTATION: float = 1.0

    # Rest-asymmetry effect on log-rate (saturating via tanh of log-ratio).
    REST_LOG_EFFECT: float = 0.035

    # Environment: wind above the reference speed suppresses tempo.
    WIND_REFERENCE_KMH: float = 30.0
    WIND_LOG_EFFECT: float = 0.03
    TEMP_COMFORT_C: float = 15.0
    TEMP_SCALE_C: float = 20.0
    TEMP_LOG_EFFECT: float = 0.02

    # Epistemic uncertainty (log-scale std devs) at zero observed sample.
    # It shrinks as sigma0 / sqrt(1 + n / n_half).
    SUPREMACY_SIGMA0: float = 0.16
    TEMPO_SIGMA0: float = 0.07
    SIGMA_HALF_SAMPLE: float = 250.0

    # Numerics.
    QUAD_NODES: int = 9
    TAIL_TOL: float = 1e-7

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_full_game_1108_020740",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "full_game",
        family: str = "winner_total",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:  # pragma: no cover - alternative base signature
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        z, w = np.polynomial.hermite_e.hermegauss(self.QUAD_NODES)
        self._qz = z
        self._qw = w / w.sum()

    # ------------------------------------------------------------------ #
    # Feature extraction helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            if value is None:
                return default
            v = float(value)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _positive(self, value: Any, default: float) -> float:
        v = self._num(value)
        return v if (v is not None and v > 0.0) else default

    def _log_rating(self, team: Any, keys: Tuple[str, ...]) -> float:
        """Return a rating on log scale.

        Positive values are treated as multiplicative ratings (1.0 means
        neutral). Non-positive values are assumed to already be log-scale.
        """
        if not isinstance(team, dict):
            return 0.0
        for k in keys:
            v = self._num(team.get(k))
            if v is not None:
                return math.log(v) if v > 0.0 else v
        return 0.0

    # ------------------------------------------------------------------ #
    # Rate construction
    # ------------------------------------------------------------------ #
    def _base_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        h_exp = self._num(ctx.get("home_expected"))
        a_exp = self._num(ctx.get("away_expected"))
        supplied = (h_exp is not None and h_exp > 0) and (a_exp is not None and a_exp > 0)
        lh = h_exp if supplied else self._positive(ctx.get("league_home_mean"), self.PRIOR_HOME_MEAN)
        la = a_exp if supplied else self._positive(ctx.get("league_away_mean"), self.PRIOR_AWAY_MEAN)
        w = (
            self.EXPOSURE_WEIGHT_WITH_EXPECTATION
            if supplied
            else self.EXPOSURE_WEIGHT_WITHOUT_EXPECTATION
        )
        return lh, la, w

    def _log_adjustments(self, ctx: Dict[str, Any], w: float) -> Tuple[float, float]:
        home = ctx.get("home") or {}
        away = ctx.get("away") or {}
        att_keys = ("attack", "attack_rating", "off", "offense")
        def_keys = ("defense", "defense_rating", "def")

        # Defense uses the conceded-multiplier convention: higher = leakier.
        adj_h = w * (self._log_rating(home, att_keys) + self._log_rating(away, def_keys))
        adj_a = w * (self._log_rating(away, att_keys) + self._log_rating(home, def_keys))

        # Rest asymmetry, saturating so extreme rest gaps cannot dominate.
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            r = math.tanh(math.log((1.0 + rh) / (1.0 + ra)))
            adj_h += self.REST_LOG_EFFECT * r
            adj_a -= self.REST_LOG_EFFECT * r

        # Common tempo: explicit pace plus the environment.
        tempo = 0.0
        pace = self._num(ctx.get("pace", ctx.get("tempo")))
        if pace is not None and pace > 0:
            tempo += math.log(pace)

        is_dome = bool(ctx.get("is_dome", False))
        if not is_dome:
            wind = self._num(ctx.get("wind_speed"))
            if wind is not None and wind > 0:
                excess = math.log1p(math.exp((wind - self.WIND_REFERENCE_KMH) / self.WIND_REFERENCE_KMH * 4.0)) / 4.0
                tempo -= self.WIND_LOG_EFFECT * excess
            temp = self._num(ctx.get("temp"))
            if temp is not None:
                dev = (temp - self.TEMP_COMFORT_C) / self.TEMP_SCALE_C
                tempo -= self.TEMP_LOG_EFFECT * (dev * dev) / (1.0 + dev * dev)

        return adj_h + tempo, adj_a + tempo

    # ------------------------------------------------------------------ #
    # Uncertainty calibration
    # ------------------------------------------------------------------ #
    def _sigmas(self, ctx: Dict[str, Any], mean_goals: float) -> Tuple[float, float]:
        n = self._num(ctx.get("sample_size", ctx.get("settled_sample")), 0.0) or 0.0
        shrink = 1.0 / math.sqrt(1.0 + max(n, 0.0) / self.SIGMA_HALF_SAMPLE)

        d = self._positive(ctx.get("dispersion_index"), self.PRIOR_DISPERSION_INDEX)
        # A lognormal mixture of a Poisson gives Var/Mean = 1 + mu*(exp(s^2) - 1).
        # Inverting that maps observed over-dispersion to a tempo sigma.
        s2_disp = math.log1p((d - 1.0) / mean_goals) if d > 1.0 else 0.0
        s_tempo = math.sqrt(s2_disp + (self.TEMPO_SIGMA0 * shrink) ** 2)
        s_sup = self.SUPREMACY_SIGMA0 * shrink
        return s_tempo, s_sup

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rho_bounds(lh: float, la: float) -> Tuple[float, float]:
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)
        return lo, hi

    def _dc_matrix(self, lh: float, la: float, rho: float, g: int) -> np.ndarray:
        k = np.arange(g + 1)
        ph = stats.poisson.pmf(k, lh)
        pa = stats.poisson.pmf(k, la)
        m = np.outer(ph, pa)
        lo, hi = self._rho_bounds(lh, la)
        r = min(max(rho, lo), hi)  # validity domain of tau, not a probability clamp
        m[0, 0] *= 1.0 - lh * la * r
        m[0, 1] *= 1.0 + lh * r
        m[1, 0] *= 1.0 + la * r
        m[1, 1] *= 1.0 - r
        m = np.clip(m, 0.0, None)
        return m

    def _lattice(self, lh: float, la: float, rho: float,
                 s_tempo: float, s_sup: float) -> np.ndarray:
        spread = 3.5 * math.hypot(s_tempo, s_sup)
        g = int(stats.poisson.ppf(1.0 - self.TAIL_TOL, max(lh, la) * math.exp(spread))) + 2
        acc = np.zeros((g + 1, g + 1))
        for zt, wt in zip(self._qz, self._qw):
            ft = math.exp(s_tempo * zt - 0.5 * s_tempo ** 2)
            for zs, ws in zip(self._qz, self._qw):
                fs = math.exp(s_sup * zs - 0.5 * s_sup ** 2)
                acc += wt * ws * self._dc_matrix(lh * ft * fs, la * ft / fs, rho, g)
        total = acc.sum()
        return acc / total if total > 0 else acc

    def _calibrate_rho(self, ctx: Dict[str, Any], lh: float, la: float,
                       s_tempo: float, s_sup: float) -> float:
        rho_ctx = self._num(ctx.get("rho"))
        if rho_ctx is not None:
            return rho_ctx
        target = self._num(ctx.get("draw_rate_prior", ctx.get("league_draw_rate")))
        if target is None or not (0.0 < target < 1.0):
            return self.PRIOR_RHO
        lo, hi = self._rho_bounds(lh, la)
        lo, hi = 0.95 * lo, 0.95 * hi

        def gap(r: float) -> float:
            return float(np.trace(self._lattice(lh, la, r, s_tempo, s_sup))) - target

        try:
            if gap(lo) * gap(hi) < 0:
                return float(optimize.brentq(gap, lo, hi, xtol=1e-5))
        except (ValueError, RuntimeError):
            pass
        return self.PRIOR_RHO

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        base_h, base_a, w = self._base_rates(ctx)
        adj_h, adj_a = self._log_adjustments(ctx, w)
        lh = base_h * math.exp(adj_h)
        la = base_a * math.exp(adj_a)

        s_tempo, s_sup = self._sigmas(ctx, lh + la)
        rho = self._calibrate_rho(ctx, lh, la, s_tempo, s_sup)
        mat = self._lattice(lh, la, rho, s_tempo, s_sup)

        scores = {
            (int(i), int(j)): float(mat[i, j])
            for i in range(mat.shape[0])
            for j in range(mat.shape[1])
            if mat[i, j] > 0.0
        }

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:  # fall back to the 3-way projection of the lattice
            p_home = float(np.tril(mat, -1).sum())
            p_draw = float(np.trace(mat))
            p_away = float(np.triu(mat, 1).sum())
            s = p_home + p_draw + p_away
            return DiscreteDistribution.from_probabilities(p_home / s, p_draw / s, p_away / s)