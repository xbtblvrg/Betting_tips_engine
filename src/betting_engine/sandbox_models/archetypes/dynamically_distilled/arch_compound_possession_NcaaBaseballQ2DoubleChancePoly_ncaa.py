import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - fall back to execution-scope primitive
    DiscreteDistribution = globals().get("DiscreteDistribution")


class NcaaBaseballQ2DoubleChancePolyaAeppliModel(BaseCandidateModel):
    """
    NCAA_BASEBALL | quarter_2 | double_chance

    Structural model
    ----------------
    "quarter_2" is interpreted literally as the second quarter of a 9-inning
    regulation game: the inning interval [9/4, 9/2] = [2.25, 4.50]. That gives
    fractional inning exposures {inning 3: 0.75, inning 4: 1.00, inning 5: 0.50}.
    Each team's runs in a (fractional) half-inning follow a Polya-Aeppli
    (compound Poisson-geometric) law:

        rallies   ~ Poisson(lambda_i)
        runs/rally~ Geometric_{1,2,...}(1 - theta_i)
        E[R_i]    = lambda_i / (1 - theta_i)
        Var/Mean  = (1 + theta_i) / (1 - theta_i)
        P(R_i=0)  = exp(-lambda_i)

    Compound Poisson laws are infinitely divisible, so fractional exposure is
    handled exactly by scaling lambda.

    theta (rally continuation) is identified by method of moments from the
    scoreless-inning share at a reference per-inning scoring rate:
        theta = 1 + ln(p0_ref) / m_ref
    or, if an observed dispersion index D > 1 is supplied, theta = (D-1)/(D+1).

    Times-through-order (TTO) drift: within the block, lineup pass index rises.
    Segment mean is preserved by normalizing exposure*fatigue weights; fatigue
    enters rally *length* via theta_i = theta ** (1 / s_i**eta), which stays in
    (0, 1) by construction and lengthens rallies late in the block.

    A shared Gamma(1/v, v) environment shock (umpire zone, wind, park state)
    multiplies both teams' means; it is integrated with generalized
    Gauss-Laguerre quadrature and induces the positive score correlation that
    raises draw mass relative to independent margins.

    Home edge enters ONLY through the input means (no additive home bonus),
    which limits home-side overconfidence in the 1X leg.

    All structural priors live in PRIORS and may be overridden per call via
    game_context['model_priors'].
    """

    PRIORS: Dict[str, float] = {
        # Empirical anchors for the segment (cold-start baseline means).
        "base_home_mean": 0.40,
        "base_away_mean": 0.30,
        # Regulation structure.
        "regulation_innings": 9.0,
        "n_quarters": 4.0,
        # Method-of-moments identification of theta (NCAA inning-level).
        "ref_runs_per_inning": 0.72,
        "ref_scoreless_share": 0.70,
        # Times-through-order dynamics.
        "batters_per_inning": 4.3,
        "lineup_size": 9.0,
        "tto_gradient": 0.08,      # proportional intensity lift per lineup pass
        "tto_theta_elasticity": 0.5,
        # Partial pooling on situational adjustments (inputs may already
        # embed these effects, so they are shrunk toward the base mean).
        "matchup_pooling": 0.35,
        "pace_pooling": 0.50,
        "rest_effect": 0.03,
        "rest_reference_days": 1.0,
        "rest_scale_days": 2.0,
        "temp_effect_per_10f": 0.025,
        "temp_reference_f": 70.0,
        "wind_effect_per_mph": 0.006,
        # Shared environment shock variance.
        "environment_variance": 0.04,
        "quadrature_nodes": 16.0,
        # Numerical support control.
        "tail_tolerance": 1e-12,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_quarter_2_1108_062901",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "quarter_2",
        family: str = "double_chance",
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
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            x = float(value)
            return x if math.isfinite(x) else None
        except (TypeError, ValueError):
            return None

    def _priors(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        p = dict(self.PRIORS)
        override = ctx.get("model_priors")
        if isinstance(override, dict):
            for k, v in override.items():
                x = self._num(v)
                if k in p and x is not None:
                    p[k] = x
        return p

    def _log_ratio(self, value: Any) -> float:
        """Multiplicative ratio (>0) -> log; non-positive values are log offsets."""
        x = self._num(value)
        if x is None:
            return 0.0
        return math.log(x) if x > 0.0 else x

    def _team_field(self, ctx: Dict[str, Any], side: str, field: str) -> Any:
        block = ctx.get(side)
        if isinstance(block, dict) and field in block:
            return block.get(field)
        return ctx.get(f"{side}_{field}")

    # ------------------------------------------------------------------ #
    # Segment geometry
    # ------------------------------------------------------------------ #
    def _segment_exposures(self, ctx: Dict[str, Any], p: Dict[str, float]) -> List[Tuple[int, float]]:
        explicit = ctx.get("segment_innings")
        if isinstance(explicit, (list, tuple)) and explicit:
            out = []
            for item in explicit:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    i, w = int(item[0]), self._num(item[1])
                else:
                    i, w = int(item), 1.0
                if w is not None and w > 0.0:
                    out.append((i, w))
            if out:
                return out

        q_index = 2
        scope = str(ctx.get("scope", self.scope))
        if "_" in scope:
            tail = scope.rsplit("_", 1)[-1]
            if tail.isdigit():
                q_index = int(tail)
        span = p["regulation_innings"] / p["n_quarters"]
        lo, hi = (q_index - 1) * span, q_index * span
        out = []
        inning = int(math.floor(lo)) + 1
        while inning - 1 < hi:
            overlap = min(hi, float(inning)) - max(lo, float(inning - 1))
            if overlap > 0.0:
                out.append((inning, overlap))
            inning += 1
        return out

    def _segment_innings_total(self, exposures: List[Tuple[int, float]]) -> float:
        return float(sum(w for _, w in exposures))

    # ------------------------------------------------------------------ #
    # Dispersion identification
    # ------------------------------------------------------------------ #
    def _theta(self, ctx: Dict[str, Any], p: Dict[str, float]) -> float:
        d_obs = self._num(ctx.get("dispersion_index"))
        if d_obs is not None and d_obs > 1.0:
            return (d_obs - 1.0) / (d_obs + 1.0)

        m_ref = self._num(ctx.get("ref_runs_per_inning")) or p["ref_runs_per_inning"]
        p0_ref = self._num(ctx.get("scoreless_inning_rate")) or p["ref_scoreless_share"]
        # Identification requires exp(-m) < p0 < 1 (zero inflation vs Poisson).
        if m_ref > 0.0 and math.exp(-m_ref) < p0_ref < 1.0:
            return 1.0 + math.log(p0_ref) / m_ref
        return 0.0

    # ------------------------------------------------------------------ #
    # Mean construction
    # ------------------------------------------------------------------ #
    def _segment_means(
        self, ctx: Dict[str, Any], p: Dict[str, float], seg_innings: float
    ) -> Tuple[float, float, Dict[str, float]]:
        mu_h = self._num(ctx.get("home_expected"))
        mu_a = self._num(ctx.get("away_expected"))
        mu_h = mu_h if (mu_h is not None and mu_h > 0.0) else p["base_home_mean"]
        mu_a = mu_a if (mu_a is not None and mu_a > 0.0) else p["base_away_mean"]

        if str(ctx.get("expected_scope", "")).lower() in ("full_game", "game", "match"):
            frac = seg_innings / p["regulation_innings"]
            mu_h *= frac
            mu_a *= frac

        log_h, log_a = 0.0, 0.0
        w_m = p["matchup_pooling"]

        # Attack = run-creation ratio; defense = runs-allowed exposure ratio.
        log_h += w_m * (self._log_ratio(self._team_field(ctx, "home", "attack"))
                        + self._log_ratio(self._team_field(ctx, "away", "defense")))
        log_a += w_m * (self._log_ratio(self._team_field(ctx, "away", "attack"))
                        + self._log_ratio(self._team_field(ctx, "home", "defense")))

        pace = ctx.get("pace", ctx.get("tempo"))
        lp = p["pace_pooling"] * self._log_ratio(pace)
        log_h += lp
        log_a += lp

        # Rest: a team's own pitching freshness suppresses the opponent.
        def rest_term(days: Any) -> float:
            d = self._num(days)
            if d is None:
                return 0.0
            return p["rest_effect"] * math.tanh((d - p["rest_reference_days"]) / p["rest_scale_days"])

        log_a -= rest_term(ctx.get("home_rest_days"))
        log_h -= rest_term(ctx.get("away_rest_days"))

        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}

        def wx(key: str) -> Any:
            return weather.get(key, ctx.get(key))

        is_dome = bool(wx("is_dome")) if wx("is_dome") is not None else False
        if not is_dome:
            temp = self._num(wx("temp"))
            if temp is None:
                temp = self._num(wx("temperature"))
            if temp is not None:
                lt = p["temp_effect_per_10f"] * (temp - p["temp_reference_f"]) / 10.0
                log_h += lt
                log_a += lt
            wind = self._num(wx("wind_speed"))
            direction = str(wx("wind_direction") or "").lower()
            if wind is not None and direction:
                sign = 1.0 if "out" in direction else (-1.0 if "in" in direction else 0.0)
                lw = sign * p["wind_effect_per_mph"] * wind
                log_h += lw
                log_a += lw

        return mu_h * math.exp(log_h), mu_a * math.exp(log_a), {"log_adj_home": log_h, "log_adj_away": log_a}

    # ------------------------------------------------------------------ #
    # Inning-level structure
    # ------------------------------------------------------------------ #
    def _inning_profile(
        self, exposures: List[Tuple[int, float]], theta: float, p: Dict[str, float]
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Return normalized mean-share weights and inning-specific thetas."""
        bpi, lineup = p["batters_per_inning"], p["lineup_size"]
        tau, eta = p["tto_gradient"], p["tto_theta_elasticity"]
        expo = np.array([w for _, w in exposures], dtype=float)
        mids = np.array([i - 0.5 for i, _ in exposures], dtype=float)
        tto = mids * bpi / lineup                     # continuous lineup pass index
        s = np.power(1.0 + tau, tto - np.average(tto, weights=expo))
        weights = expo * s
        weights = weights / weights.sum()
        if theta > 0.0:
            thetas = np.power(theta, 1.0 / np.power(s, eta))
        else:
            thetas = np.zeros_like(s)
        return weights, thetas

    @staticmethod
    def _polya_aeppli_pmf(lam: float, theta: float, kmax: int) -> np.ndarray:
        g = np.zeros(kmax + 1)
        if lam <= 0.0:
            g[0] = 1.0
            return g
        g[0] = math.exp(-lam)
        j = np.arange(1, kmax + 1, dtype=float)
        jf = j * (1.0 - theta) * np.power(theta, j - 1.0)   # j * f(j)
        for k in range(1, kmax + 1):
            g[k] = (lam / k) * float(np.dot(jf[:k], g[k - 1::-1][:k]))
        return g

    def _team_pmf(self, mu: float, weights: np.ndarray, thetas: np.ndarray, kmax: int) -> np.ndarray:
        pmf = np.zeros(kmax + 1)
        pmf[0] = 1.0
        for w, th in zip(weights, thetas):
            m_i = mu * w
            lam_i = m_i * (1.0 - th)
            pmf = np.convolve(pmf, self._polya_aeppli_pmf(lam_i, th, kmax))[: kmax + 1]
        return pmf

    @staticmethod
    def _outcomes(ph: np.ndarray, pa: np.ndarray) -> Tuple[float, float, float]:
        cdf_a = np.cumsum(pa)
        p_draw = float(np.dot(ph, pa))
        p_home = float(np.dot(ph[1:], cdf_a[:-1]))
        cdf_h = np.cumsum(ph)
        p_away = float(np.dot(pa[1:], cdf_h[:-1]))
        return p_home, p_draw, p_away

    def _support(self, mu_h: float, mu_a: float, theta_max: float, z_max: float, tol: float) -> int:
        d = (1.0 + theta_max) / (1.0 - theta_max)
        m = max(mu_h, mu_a) * z_max
        sd = math.sqrt(max(m * d, 1e-12))
        # Polya-Aeppli tail decays like theta^k; ensure geometric tail below tol.
        geo = int(math.ceil(math.log(tol) / math.log(theta_max))) if theta_max > 0.0 else 0
        return int(max(25, math.ceil(m + 12.0 * sd) + 10, geo))

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        p = self._priors(ctx)

        exposures = self._segment_exposures(ctx, p)
        seg_innings = self._segment_innings_total(exposures)
        theta = self._theta(ctx, p)
        mu_h, mu_a, adj = self._segment_means(ctx, p, seg_innings)
        weights, thetas = self._inning_profile(exposures, theta, p)

        v = self._num(ctx.get("environment_variance"))
        v = v if (v is not None and v >= 0.0) else p["environment_variance"]
        n_nodes = int(p["quadrature_nodes"])
        if v > 0.0:
            shape = 1.0 / v
            x, w = special.roots_genlaguerre(n_nodes, shape - 1.0)
            w = np.asarray(w, dtype=float)
            w = w / w.sum()
            z_nodes = np.asarray(x, dtype=float) / shape
        else:
            z_nodes, w = np.array([1.0]), np.array([1.0])

        theta_max = float(np.max(thetas)) if thetas.size else 0.0
        kmax = self._support(mu_h, mu_a, theta_max, float(np.max(z_nodes)), p["tail_tolerance"])

        p_home = p_draw = p_away = 0.0
        p_00 = 0.0
        for z, wz in zip(z_nodes, w):
            ph = self._team_pmf(mu_h * z, weights, thetas, kmax)
            pa = self._team_pmf(mu_a * z, weights, thetas, kmax)
            h, d, a = self._outcomes(ph, pa)
            p_home += wz * h
            p_draw += wz * d
            p_away += wz * a
            p_00 += wz * ph[0] * pa[0]

        total = p_home + p_draw + p_away
        if not (math.isfinite(total) and total > 0.0):
            # Deterministic Skellam-free fallback: independent Poisson at anchors.
            ph = self._polya_aeppli_pmf(p["base_home_mean"], 0.0, 40)
            pa = self._polya_aeppli_pmf(p["base_away_mean"], 0.0, 40)
            p_home, p_draw, p_away = self._outcomes(ph, pa)
            total = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total

        self.last_diagnostics = {
            "segment_exposures": exposures,
            "theta": theta,
            "inning_thetas": thetas.tolist(),
            "inning_mean_shares": weights.tolist(),
            "dispersion_index_block": (1.0 + theta) / (1.0 - theta) if theta < 1.0 else float("inf"),
            "mu_home": mu_h,
            "mu_away": mu_a,
            "p_scoreless_draw": p_00 / total,
            "double_chance": {
                "1X": p_home + p_draw,
                "X2": p_away + p_draw,
                "12": p_home + p_away,
            },
            "support_kmax": kmax,
            **adj,
        }

        if hasattr(DiscreteDistribution, "from_probabilities"):
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})