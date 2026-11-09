import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - DiscreteDistribution may be pre-loaded in scope
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class SerieAHalf1WinnerTotalModel(BaseCandidateModel):
    """
    SERIEA | half_1 | winner_total

    Structure
    ---------
    First-half goals (X, Y) follow a bivariate Poisson:
        X = U + W, Y = V + W, with U~Pois(l1), V~Pois(l2), W~Pois(l3)
    and E[X] = lam_h, E[Y] = lam_a, l3 = shared tempo component.

    A Dixon-Coles low-score correction (rho) is then applied to the
    {0-0, 1-0, 0-1, 1-1} cells. It captures the excess of cagey 0-0 / 1-1
    half-time states typical of Serie A first halves.

    Marginal means
    --------------
    lam = anchor * attack * opp_defense * tau, where
      * anchor is the situational expected value, shrunk toward an online
        league first-half mean. The weight comes from a pseudo-count, so the
        shrinkage is data driven rather than a static clamp.
      * tau is a smooth log-linear multiplier for tempo, rest and weather.

    Dependence calibration
    ----------------------
    l3 and rho start at their prior values and are re-estimated online, as
    settled half-time scores accrue, by moment matching:
      * l3 from the sample covariance of (X, Y), which is the bivariate
        Poisson covariance.
      * rho by solving for the value at which the model draw probability
        matches the observed draw frequency.
    Both estimates are shrunk toward the prior with weight n / (n + k).

    Output
    ------
    The full joint score lattice is returned, so every winner_total cell
    {sign(i - j)} x {i + j vs line} is priced as an exact sum over the joint
    mass. Result and total stay coherently coupled.
    """

    # ---- Domain priors (cold start; replaced progressively by online moments) ----
    PRIOR_HOME_HT = 0.80          # empirical baseline home first-half mean
    PRIOR_AWAY_HT = 0.60          # empirical baseline away first-half mean
    PRIOR_DISPERSION = 1.0        # observed Var/Mean prior (pure Poisson)
    PRIOR_HT_DRAW_RATE = 0.41     # Serie A half-time draw frequency (domain prior)

    # Pseudo-count strengths: how many settled halves the prior is "worth".
    K_LEAGUE_MEAN = 120.0
    K_DEPENDENCE = 250.0
    K_SIGNAL = 1.0                # reliability ratio of situational input vs league mean

    # Elasticities of the log-linear situational multiplier.
    ELAST_TEMPO = 1.0             # goals scale ~proportionally with pace
    ELAST_REST = 0.035            # per log-day rest advantage
    ELAST_WIND = 0.004            # per km/h above calm reference
    ELAST_TEMP = 0.0025           # per deg C away from thermal optimum
    TEMP_OPTIMUM_C = 16.0
    WIND_REFERENCE = 10.0

    # Probability-mass tolerance used to size the score lattice (numerical only).
    TAIL_EPS = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_1_1107_223744",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_1",
        family: str = "winner_total",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
                **kwargs,
            )
        except TypeError:
            super().__init__()  # type: ignore[call-arg]
            self.model_id = model_id
            self.version = version
            self.league = league
            self.scope = scope
            self.family = family

        # Online sufficient statistics for settled first halves.
        self._n = 0
        self._sum_h = 0.0
        self._sum_a = 0.0
        self._sum_hh = 0.0
        self._sum_aa = 0.0
        self._sum_ha = 0.0
        self._draws = 0
        self._sum_pred_h = 0.0
        self._sum_pred_a = 0.0

        self._lambda3 = 0.0
        self._rho = self._prior_rho()

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _side_feature(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        """Read attack/defense ratings (multiplicative, 1.0 = league average)."""
        nested = ctx.get(side)
        val = None
        if isinstance(nested, dict):
            val = self._num(nested.get(key))
        if val is None:
            val = self._num(ctx.get(f"{side}_{key}"))
        if val is None or val <= 0.0:
            return 1.0
        # Ratings given on an additive scale centered on 0 are mapped via exp.
        return val

    def _league_means(self) -> Tuple[float, float]:
        """Online league first-half means, shrunk toward the empirical prior."""
        w = self._n / (self._n + self.K_LEAGUE_MEAN)
        if self._n == 0:
            return self.PRIOR_HOME_HT, self.PRIOR_AWAY_HT
        mh = self._sum_h / self._n
        ma = self._sum_a / self._n
        return (
            w * mh + (1.0 - w) * self.PRIOR_HOME_HT,
            w * ma + (1.0 - w) * self.PRIOR_AWAY_HT,
        )

    def _calibration_ratio(self) -> Tuple[float, float]:
        """Ratio of realized to predicted means; corrects systemic input bias."""
        if self._n == 0 or self._sum_pred_h <= 0 or self._sum_pred_a <= 0:
            return 1.0, 1.0
        w = self._n / (self._n + self.K_LEAGUE_MEAN)
        rh = (self._sum_h + 1e-9) / self._sum_pred_h
        ra = (self._sum_a + 1e-9) / self._sum_pred_a
        return math.exp(w * math.log(rh)), math.exp(w * math.log(ra))

    def _situational_log_multiplier(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Shared and side-specific log-multipliers from tempo, rest and weather."""
        shared = 0.0

        pace = self._num(ctx.get("pace", ctx.get("tempo")))
        if pace is not None and pace > 0:
            # Pace is either a ratio (~1.0) or a raw count; normalize raw counts
            # by their own context mean when provided.
            ref = self._num(ctx.get("league_pace", ctx.get("pace_reference")), None)
            ratio = pace / ref if (ref and ref > 0) else pace
            if ratio > 0:
                shared += self.ELAST_TEMPO * math.log(ratio)

        is_dome = bool(ctx.get("is_dome", False))
        if not is_dome:
            wind = self._num(ctx.get("wind_speed"))
            if wind is not None and wind > self.WIND_REFERENCE:
                shared -= self.ELAST_WIND * (wind - self.WIND_REFERENCE)
            temp = self._num(ctx.get("temp"))
            if temp is not None:
                shared -= self.ELAST_TEMP * abs(temp - self.TEMP_OPTIMUM_C)

        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        rest_h = rest_a = 0.0
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            # Diminishing returns to rest: log(1 + days) differential.
            diff = math.log1p(rh) - math.log1p(ra)
            rest_h = self.ELAST_REST * diff
            rest_a = -self.ELAST_REST * diff

        return shared + rest_h, shared + rest_a

    def _marginal_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        league_h, league_a = self._league_means()

        in_h = self._num(ctx.get("home_expected"))
        in_a = self._num(ctx.get("away_expected"))
        in_h = league_h if (in_h is None or in_h <= 0) else in_h
        in_a = league_a if (in_a is None or in_a <= 0) else in_a

        # Precision-weighted shrinkage in log space: the situational input
        # carries K_SIGNAL units of reliability versus one unit for the league mean.
        ws = self.K_SIGNAL / (self.K_SIGNAL + 1.0)
        anchor_h = math.exp(ws * math.log(in_h) + (1.0 - ws) * math.log(league_h))
        anchor_a = math.exp(ws * math.log(in_a) + (1.0 - ws) * math.log(league_a))

        att_h = self._side_feature(ctx, "home", "attack")
        def_h = self._side_feature(ctx, "home", "defense")
        att_a = self._side_feature(ctx, "away", "attack")
        def_a = self._side_feature(ctx, "away", "defense")

        lm_h, lm_a = self._situational_log_multiplier(ctx)
        cal_h, cal_a = self._calibration_ratio()

        lam_h = anchor_h * att_h * def_a * math.exp(lm_h) * cal_h
        lam_a = anchor_a * att_a * def_h * math.exp(lm_a) * cal_a
        return lam_h, lam_a

    # ------------------------------------------------------------------ #
    # Joint distribution
    # ------------------------------------------------------------------ #
    def _lattice_size(self, lam_h: float, lam_a: float, l3: float) -> int:
        lam_max = max(lam_h, lam_a) + l3
        n = int(math.ceil(lam_max + 1.0))
        # Grow until the Poisson tail beyond n is below TAIL_EPS.
        while special.pdtrc(n, lam_max) > self.TAIL_EPS:
            n += 1
        return n + 1

    @staticmethod
    def _bivariate_poisson(lam_h: float, lam_a: float, l3: float, n: int) -> np.ndarray:
        """P(X=i, Y=j) for X=U+W, Y=V+W."""
        l1 = lam_h - l3
        l2 = lam_a - l3
        k = np.arange(n)
        log_fact = special.gammaln(k + 1.0)
        if l3 <= 0.0:
            ph = np.exp(k * math.log(lam_h) - lam_h - log_fact)
            pa = np.exp(k * math.log(lam_a) - lam_a - log_fact)
            return np.outer(ph, pa)

        log_l1, log_l2, log_l3 = math.log(l1), math.log(l2), math.log(l3)
        base = -(l1 + l2 + l3)
        mat = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                m = min(i, j)
                w = np.arange(m + 1)
                terms = (
                    (i - w) * log_l1 - log_fact[i - w]
                    + (j - w) * log_l2 - log_fact[j - w]
                    + w * log_l3 - log_fact[w]
                )
                mat[i, j] = math.exp(base + special.logsumexp(terms))
        return mat

    @staticmethod
    def _rho_bounds(lam_h: float, lam_a: float) -> Tuple[float, float]:
        """Feasible interval keeping every Dixon-Coles cell weight non-negative."""
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        return lo, hi

    @staticmethod
    def _apply_dixon_coles(mat: np.ndarray, lam_h: float, lam_a: float, rho: float) -> np.ndarray:
        out = mat.copy()
        out[0, 0] *= 1.0 - lam_h * lam_a * rho
        out[0, 1] *= 1.0 + lam_h * rho
        out[1, 0] *= 1.0 + lam_a * rho
        out[1, 1] *= 1.0 - rho
        out = np.clip(out, 0.0, None)  # numerical floor only; bounds already enforce >= 0
        s = out.sum()
        return out / s if s > 0 else mat

    def _joint(self, lam_h: float, lam_a: float, l3: float, rho: float) -> np.ndarray:
        l3 = min(l3, 0.999 * min(lam_h, lam_a))
        n = self._lattice_size(lam_h, lam_a, l3)
        mat = self._bivariate_poisson(lam_h, lam_a, max(l3, 0.0), n)
        lo, hi = self._rho_bounds(lam_h, lam_a)
        rho = min(max(rho, lo), hi)
        return self._apply_dixon_coles(mat, lam_h, lam_a, rho)

    # ------------------------------------------------------------------ #
    # Dependence calibration
    # ------------------------------------------------------------------ #
    def _solve_rho_for_draw(self, lam_h: float, lam_a: float, l3: float, target: float) -> float:
        lo, hi = self._rho_bounds(lam_h, lam_a)
        eps = 1e-9

        def gap(r: float) -> float:
            return float(np.trace(self._joint(lam_h, lam_a, l3, r))) - target

        a, b = lo + eps, hi - eps
        ga, gb = gap(a), gap(b)
        if ga * gb > 0:
            return a if abs(ga) < abs(gb) else b
        return float(optimize.brentq(gap, a, b, xtol=1e-8))

    def _prior_rho(self) -> float:
        """rho implied by the domain HT draw prior at the baseline means."""
        try:
            return self._solve_rho_for_draw(
                self.PRIOR_HOME_HT, self.PRIOR_AWAY_HT, 0.0, self.PRIOR_HT_DRAW_RATE
            )
        except Exception:
            return 0.0

    def _refit_dependence(self) -> None:
        if self._n < 2:
            return
        n = self._n
        mh, ma = self._sum_h / n, self._sum_a / n
        cov = self._sum_ha / n - mh * ma
        w = n / (n + self.K_DEPENDENCE)

        l3_hat = max(cov, 0.0)
        self._lambda3 = w * l3_hat  # prior l3 = 0 (independence)

        draw_freq = self._draws / n
        target = w * draw_freq + (1.0 - w) * self.PRIOR_HT_DRAW_RATE
        lh, la = self._league_means()
        try:
            self._rho = self._solve_rho_for_draw(lh, la, self._lambda3, target)
        except Exception:
            pass

    def update(self, game_context: Dict[str, Any], home_goals: int, away_goals: int) -> None:
        """Ingest a settled first-half score and refresh online calibration."""
        x, y = int(home_goals), int(away_goals)
        pred_h, pred_a = self._marginal_means(game_context)
        self._n += 1
        self._sum_h += x
        self._sum_a += y
        self._sum_hh += x * x
        self._sum_aa += y * y
        self._sum_ha += x * y
        self._draws += int(x == y)
        self._sum_pred_h += pred_h
        self._sum_pred_a += pred_a
        self._refit_dependence()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        lam_h, lam_a = self._marginal_means(game_context)
        return self._joint(lam_h, lam_a, self._lambda3, self._rho)

    def winner_total_cells(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Exact joint pricing of {home, draw, away} x {over, under} at a total line."""
        mat = self.score_matrix(game_context)
        n = mat.shape[0]
        i, j = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        tot = i + j
        out: Dict[str, float] = {}
        for res, mask in (("home", i > j), ("draw", i == j), ("away", i < j)):
            out[f"{res}_over"] = float(mat[mask & (tot > line)].sum())
            out[f"{res}_under"] = float(mat[mask & (tot < line)].sum())
            out[f"{res}_push"] = float(mat[mask & (tot == line)].sum())
        return out

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mat = self.score_matrix(game_context)
        n = mat.shape[0]
        scores = {(i, j): float(mat[i, j]) for i in range(n) for j in range(n) if mat[i, j] > 0.0}

        if hasattr(DiscreteDistribution, "from_scores"):
            try:
                return DiscreteDistribution.from_scores(scores)
            except Exception:
                pass

        p_home = float(np.tril(mat, -1).sum())
        p_draw = float(np.trace(mat))
        p_away = float(np.triu(mat, 1).sum())
        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

        if hasattr(DiscreteDistribution, "from_probabilities"):
            try:
                return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
            except Exception:
                pass
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})