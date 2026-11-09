import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - engine may inject into scope
    pass


class MLSHalf1AwayTotalModel(BaseCandidateModel):
    """
    MLS | half_1 | away_total  --  cold-start, prior-anchored ZIP count model.

    Model
    -----
    Away first-half goals X ~ ZIP(pi, lam), parameterised to preserve the mean:
        E[X] = mu,        lam = mu / (1 - pi)
        P(X = 0) = pi + (1 - pi) * exp(-lam)

    Mean structure (log-linear; all covariates are scale-free and zero-centred):
        log mu = log(m0) + a0
                 + b_att  * log(away_expected / m0)
                 + b_def  * log(home_defense_exposure)
                 + b_pace * log(pace)
                 + d_rest * rest_log_ratio
                 + d_tz   * |timezone_shift|
                 + d_alt  * altitude_km

    Extra-zero structure ("conservative away posture"):
        logit pi = g0 + g_tz * |timezone_shift| + g_alt * altitude_km - g_rest * rest_log_ratio

    Cold start
    ----------
    With no settled wagers, every parameter sits at its prior mean:
        b_att = 1, all other slopes = 0, a0 = 0, pi ~ 0.
    The model is then exactly Poisson(away_expected), i.e. the empirical
    anchor 0.60 scaled by the situational input (dispersion index 1.0).
    Structure enters only via `fit()` (MAP with Gaussian shrinkage), so travel,
    altitude and zero-inflation effects are learned rather than asserted.

    Exposure terms (b_def, b_pace) shrink to 0 rather than 1 because
    `away_expected` is assumed to already encode opponent strength; a prior
    of 1 would double-count it.

    Scale assumption
    ----------------
    `away_expected` / `home_expected` are read on the first-half scale
    (baseline 0.60 / 0.80). If the context provides explicit
    `away_expected_half_1` / `home_expected_half_1`, those take precedence.
    """

    # Empirical anchors for this segment (first-half means).
    BASE_HOME = 0.80
    BASE_AWAY = 0.60

    # Parameter layout: name -> (prior mean, prior sd).
    # Prior sds encode how far the data must move before structure is accepted.
    _PARAM_SPEC: Tuple[Tuple[str, float, float], ...] = (
        ("a0", 0.0, 0.15),      # global calibration offset on log mean
        ("b_att", 1.0, 0.20),   # elasticity to supplied expectation
        ("b_def", 0.0, 0.20),   # residual home-defense exposure
        ("b_pace", 0.0, 0.20),  # residual tempo effect
        ("d_rest", 0.0, 0.10),  # rest advantage on mean
        ("d_tz", 0.0, 0.05),    # per-hour timezone shift on mean
        ("d_alt", 0.0, 0.10),   # per-km venue altitude on mean
        ("g0", -6.0, 1.50),     # zero-inflation intercept (pi ~ 0.25%)
        ("g_tz", 0.0, 0.30),
        ("g_alt", 0.0, 0.50),
        ("g_rest", 0.0, 0.50),
    )

    _HIGH_ALTITUDE_VENUES = {
        "colorado": 1.61,       # Dick's Sporting Goods Park, Commerce City (km)
        "rapids": 1.61,
        "real salt lake": 1.39, # America First Field, Sandy (km)
        "rsl": 1.39,
        "salt lake": 1.39,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1107_115322",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._names = [p[0] for p in self._PARAM_SPEC]
        self._prior_mean = np.array([p[1] for p in self._PARAM_SPEC], dtype=float)
        self._prior_sd = np.array([p[2] for p in self._PARAM_SPEC], dtype=float)
        self.theta = self._prior_mean.copy()
        self.n_fitted = 0

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._num(ctx.get(k))
                if v is not None:
                    return v
        return None

    def _side_rating(self, ctx: Dict[str, Any], side: str, field: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(field))
            if v is not None:
                return v
        return self._get(ctx, f"{side}_{field}")

    @staticmethod
    def _log_ratio_positive(x: Optional[float]) -> float:
        """log of a multiplicative rating centred at 1; 0 if unavailable."""
        if x is None or x <= 0:
            return 0.0
        return math.log(x)

    def _altitude_km(self, ctx: Dict[str, Any]) -> float:
        alt_m = self._get(ctx, "altitude_m", "venue_altitude_m", "elevation_m")
        if alt_m is not None:
            return max(alt_m, 0.0) / 1000.0
        venue = ctx.get("venue")
        name = ""
        if isinstance(venue, dict):
            alt_m = self._num(venue.get("altitude_m") or venue.get("elevation_m"))
            if alt_m is not None:
                return max(alt_m, 0.0) / 1000.0
            name = str(venue.get("name", "")) + " " + str(venue.get("city", ""))
        elif venue is not None:
            name = str(venue)
        name = (name + " " + str(ctx.get("home_team", ""))).lower()
        for key, km in self._HIGH_ALTITUDE_VENUES.items():
            if key in name:
                return km
        return 0.0

    def _features(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        away_exp = self._get(ctx, "away_expected_half_1", "away_expected")
        home_exp = self._get(ctx, "home_expected_half_1", "home_expected")
        if away_exp is None or away_exp <= 0:
            away_exp = self.BASE_AWAY
        if home_exp is None or home_exp <= 0:
            home_exp = self.BASE_HOME

        # Home defensive exposure: >1 means the home side concedes more than average.
        home_def = self._side_rating(ctx, "home", "defense")

        pace = self._get(ctx, "pace", "tempo", "expected_pace")

        # Rest: scale-free log ratio, positive when the away side is fresher.
        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        rest = 0.0
        if hr is not None and ar is not None and hr >= 0 and ar >= 0:
            rest = math.log((1.0 + ar) / (1.0 + hr))

        tz = self._get(ctx, "timezone_shift", "tz_shift", "away_timezone_diff", "timezone_diff")
        tz = abs(tz) if tz is not None else 0.0

        return {
            "away_exp": away_exp,
            "home_exp": home_exp,
            "x_att": math.log(away_exp / self.BASE_AWAY),
            "x_def": self._log_ratio_positive(home_def),
            "x_pace": self._log_ratio_positive(pace),
            "x_rest": rest,
            "x_tz": tz,
            "x_alt": self._altitude_km(ctx),
        }

    # ------------------------------------------------------------------ #
    # Core parameter map
    # ------------------------------------------------------------------ #
    def _mu_pi(self, f: Dict[str, float], theta: np.ndarray) -> Tuple[float, float]:
        p = dict(zip(self._names, theta))
        log_mu = (
            math.log(self.BASE_AWAY)
            + p["a0"]
            + p["b_att"] * f["x_att"]
            + p["b_def"] * f["x_def"]
            + p["b_pace"] * f["x_pace"]
            + p["d_rest"] * f["x_rest"]
            + p["d_tz"] * f["x_tz"]
            + p["d_alt"] * f["x_alt"]
        )
        eta_pi = (
            p["g0"]
            + p["g_tz"] * f["x_tz"]
            + p["g_alt"] * f["x_alt"]
            - p["g_rest"] * f["x_rest"]
        )
        return math.exp(log_mu), float(special.expit(eta_pi))

    @staticmethod
    def _zip_pmf(mu: float, pi: float, k_max: int) -> np.ndarray:
        lam = mu / (1.0 - pi)
        k = np.arange(k_max + 1)
        pmf = (1.0 - pi) * stats.poisson.pmf(k, lam)
        pmf[0] += pi
        return pmf

    @staticmethod
    def _support(mean: float) -> int:
        """Truncation chosen so omitted tail mass is negligible (< 1e-12)."""
        return int(max(stats.poisson.isf(1e-12, max(mean, 1e-9)), 1)) + 1

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def away_goal_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        f = self._features(game_context)
        mu, pi = self._mu_pi(f, self.theta)
        k_max = self._support(mu / (1.0 - pi))
        pmf = self._zip_pmf(mu, pi, k_max)
        return pmf / pmf.sum()

    def prob_over(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Over/under/push probabilities for any half or whole line."""
        pmf = self.away_goal_pmf(game_context)
        k = np.arange(pmf.size)
        over = float(pmf[k > line].sum())
        push = float(pmf[k == line].sum()) if float(line).is_integer() else 0.0
        return {"over": over, "under": 1.0 - over - push, "push": push}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        f = self._features(game_context)
        mu_a, pi = self._mu_pi(f, self.theta)

        # Home first-half goals: Poisson on the same anchored, scale-free basis.
        mu_h = f["home_exp"]

        ka = self._support(mu_a / (1.0 - pi))
        kh = self._support(mu_h)
        p_a = self._zip_pmf(mu_a, pi, ka)
        p_h = stats.poisson.pmf(np.arange(kh + 1), mu_h)
        p_a /= p_a.sum()
        p_h /= p_h.sum()

        grid = np.outer(p_h, p_a)
        scores = {
            (int(h), int(a)): float(grid[h, a])
            for h in range(grid.shape[0])
            for a in range(grid.shape[1])
            if grid[h, a] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)

    # ------------------------------------------------------------------ #
    # Learning: MAP with Gaussian shrinkage toward the cold-start prior
    # ------------------------------------------------------------------ #
    def _neg_log_post(self, theta: np.ndarray, feats: List[Dict[str, float]], y: np.ndarray) -> float:
        nll = 0.0
        for f, yi in zip(feats, y):
            mu, pi = self._mu_pi(f, theta)
            pi = min(max(pi, 1e-12), 1.0 - 1e-9)
            lam = mu / (1.0 - pi)
            if yi == 0:
                nll -= math.log(pi + (1.0 - pi) * math.exp(-lam))
            else:
                nll -= math.log(1.0 - pi) + stats.poisson.logpmf(int(yi), lam)
        z = (theta - self._prior_mean) / self._prior_sd
        return nll + 0.5 * float(z @ z)

    def fit(self, records: Sequence[Tuple[Dict[str, Any], int]]) -> "MLSHalf1AwayTotalModel":
        """
        records: iterable of (game_context, settled away first-half goals).
        With few records the posterior stays near the Poisson anchor; structure
        is admitted only in proportion to evidence.
        """
        clean = [(ctx, int(g)) for ctx, g in records if g is not None and int(g) >= 0]
        if not clean:
            return self
        feats = [self._features(ctx) for ctx, _ in clean]
        y = np.array([g for _, g in clean], dtype=int)
        res = optimize.minimize(
            self._neg_log_post,
            x0=self.theta.copy(),
            args=(feats, y),
            method="L-BFGS-B",
        )
        if res.success and np.all(np.isfinite(res.x)):
            self.theta = res.x
            self.n_fitted = len(clean)
        return self

    def parameters(self) -> Dict[str, float]:
        return {n: float(v) for n, v in zip(self._names, self.theta)}

    # ------------------------------------------------------------------ #
    # Contract valuation (no market information enters the forecast)
    # ------------------------------------------------------------------ #
    def expected_value(self, game_context: Dict[str, Any], line: float, side: str, decimal_odds: float) -> float:
        """EV per unit stake; pushes refund the stake."""
        pr = self.prob_over(game_context, line)
        p_win = pr["over"] if side.lower() == "over" else pr["under"]
        p_lose = 1.0 - p_win - pr["push"]
        return p_win * (decimal_odds - 1.0) - p_lose

    def is_eligible(self, game_context: Dict[str, Any], line: float, side: str, decimal_odds: float) -> bool:
        return decimal_odds >= 1.40 and self.expected_value(game_context, line, side, decimal_odds) > 0.0