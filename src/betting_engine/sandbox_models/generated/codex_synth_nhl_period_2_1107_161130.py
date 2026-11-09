import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - resolved from execution scope
    try:
        from betting_engine.sandbox_models.distribution import DiscreteDistribution
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")


class NHLPeriod2WinnerTotalBivariatePoisson(BaseCandidateModel):
    """
    NHL | period_2 | winner_total  —  bivariate Poisson joint (side, total) pricer.

    Structure
    ---------
        H = X1 + X3,  A = X2 + X3,  Xk ~ Poisson(lambda_k) independent
        E[H] = lambda1 + lambda3 = mu_h,  E[A] = lambda2 + lambda3 = mu_a
        Cov(H, A) = lambda3    (shared long-change / tempo shock)

    * Margin H - A = X1 - X2 cancels X3, so the shared tempo component moves
      totals and the correlated joint cells without inflating home edge.
    * Draw mass: P(H = A) = exp(-(l1 + l2)) * I0(2 sqrt(l1 l2)), evaluated
      stably as exp(-(sqrt l1 - sqrt l2)^2) * ive(0, 2 sqrt(l1 l2)).

    Learning (all state starts at the cold-start prior and is refit online)
    ----------------------------------------------------------------------
    * Side intercepts: Gamma-Poisson conjugate scale per side, prior mean 1,
      prior strength = `prior_games` P2 periods of baseline scoring.
    * Situational elasticities theta (fatigue differential, joint-fatigue
      long-change amplification, residual attack/defense weight, pace weight):
      online Laplace / Fisher-scoring Poisson GLM with Gaussian domain priors.
    * lambda3: method-of-moments Cov(H, A) on prediction residuals with a
      signal-to-noise (James-Stein style) shrinkage weight cov^2/(cov^2 + Var/n).
      Zero at cold start => dispersion index 1.000 as observed.

    Input scaling
    -------------
    `home_expected` / `away_expected` are anchored to the P2 empirical means
    (1.02 / 0.92). If they are on a full-game scale (detected by the geometric
    midpoint between period-scale and regulation-scale totals), they are mapped
    to the P2 window by B_p2 / reference_full_game_total, where the reference is
    the running mean of observed inputs (the current input at cold start), so
    the matchup split is preserved and the window total stays anchored.
    """

    BASE_HOME = 1.02
    BASE_AWAY = 0.92
    REGULATION_PERIODS = 3
    MIN_ODDS = 1.40  # eligibility rule specified for this segment

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_2_1107_161130",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_2",
        family: str = "winner_total",
        prior_games: float = 82.0,
        reference_rest_days: float = 1.0,
        theta_prior_mean: Optional[Tuple[float, float, float, float]] = None,
        theta_prior_sd: Optional[Tuple[float, float, float, float]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.base_total = self.BASE_HOME + self.BASE_AWAY
        self.prior_games = float(prior_games)
        self.f_ref = self._fatigue(reference_rest_days)

        # theta = [fatigue differential, joint-fatigue amplification,
        #          residual attack/defense weight, pace weight]
        # Domain priors (weakly informative), updated by Bayesian online GLM.
        m0 = theta_prior_mean or (0.04, 0.03, 0.5, 0.5)
        s0 = theta_prior_sd or (0.05, 0.05, 0.35, 0.35)
        self.theta = np.asarray(m0, dtype=float)
        self.precision = np.diag(1.0 / np.square(np.asarray(s0, dtype=float)))

        # Gamma-Poisson side calibration accumulators
        self._sum_y = {"home": 0.0, "away": 0.0}
        self._sum_mu = {"home": 0.0, "away": 0.0}

        # Covariance (shared tempo) accumulators
        self._n = 0
        self._sum_cross = 0.0
        self._sum_mumu = 0.0
        self._sum_total_mu = 0.0

        # Full-game input reference (running mean of observed full-scale totals)
        self._n_full = 0
        self._sum_full_input = 0.0

        self._tail_tol = np.finfo(float).eps ** 0.75
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
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
                v = self._num(ctx[k])
                if v is not None:
                    return v
        return None

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        team = ctx.get(side)
        aliases = {
            "attack": ("attack", "attack_rating", "offense", "off"),
            "defense": ("defense", "defense_rating", "def"),
            "pace": ("pace", "tempo"),
        }[kind]
        if isinstance(team, dict):
            for a in aliases:
                v = self._num(team.get(a))
                if v is not None and v > 0:
                    return v
        for a in aliases:
            v = self._num(ctx.get(f"{side}_{a}"))
            if v is not None and v > 0:
                return v
        return None

    @staticmethod
    def _fatigue(rest_days: Optional[float]) -> Optional[float]:
        if rest_days is None:
            return None
        return 1.0 / (1.0 + max(rest_days, 0.0))

    # ------------------------------------------------------- feature building
    def _base_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, str]:
        h_in = self._get(ctx, "period_2_home_expected", "p2_home_expected", "home_expected")
        a_in = self._get(ctx, "period_2_away_expected", "p2_away_expected", "away_expected")
        if h_in is None or h_in <= 0:
            h_in = self.BASE_HOME
        if a_in is None or a_in <= 0:
            a_in = self.BASE_AWAY
        total_in = h_in + a_in

        # Geometric midpoint between a single-period scale and regulation scale.
        threshold = self.base_total * math.sqrt(self.REGULATION_PERIODS)
        if total_in <= threshold:
            return h_in, a_in, "period"
        ref = (self._sum_full_input / self._n_full) if self._n_full > 0 else total_in
        scale = self.base_total / ref
        return h_in * scale, a_in * scale, "full_game"

    def _features(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        fh = self._fatigue(self._get(ctx, "home_rest_days"))
        fa = self._fatigue(self._get(ctx, "away_rest_days"))
        fh = self.f_ref if fh is None else fh
        fa = self.f_ref if fa is None else fa

        att_h = self._team_rating(ctx, "home", "attack")
        att_a = self._team_rating(ctx, "away", "attack")
        def_h = self._team_rating(ctx, "home", "defense")
        def_a = self._team_rating(ctx, "away", "defense")

        def rel(x: Optional[float], y: Optional[float], league_key: str) -> Tuple[float, float]:
            league_avg = self._get(ctx, league_key)
            if x is None or y is None:
                return 0.0, 0.0
            ref = league_avg if (league_avg and league_avg > 0) else math.sqrt(x * y)
            return math.log(x / ref), math.log(y / ref)

        la_h, la_a = rel(att_h, att_a, "league_avg_attack")
        # 'defense' interpreted as goals-against multiplier (>1 = leakier)
        ld_h, ld_a = rel(def_h, def_a, "league_avg_defense")

        pace = self._get(ctx, "pace", "tempo")
        ph, pa = self._team_rating(ctx, "home", "pace"), self._team_rating(ctx, "away", "pace")
        if pace is None and ph is not None and pa is not None:
            league_pace = self._get(ctx, "league_avg_pace")
            pace = math.sqrt(ph * pa) / league_pace if (league_pace and league_pace > 0) else None
        log_pace = math.log(pace) if (pace is not None and pace > 0) else 0.0

        joint_fatigue = fh + fa - 2.0 * self.f_ref
        x_h = np.array([fa - fh, joint_fatigue, la_h + ld_a, log_pace])
        x_a = np.array([fh - fa, joint_fatigue, la_a + ld_h, log_pace])
        return x_h, x_a

    def _calibration(self, side: str) -> float:
        base = self.BASE_HOME if side == "home" else self.BASE_AWAY
        a0 = self.prior_games * base
        return (a0 + self._sum_y[side]) / (a0 + self._sum_mu[side])

    def _lambda3(self, mu_h: float, mu_a: float) -> float:
        if self._n == 0:
            return 0.0
        cov = self._sum_cross / self._n
        if cov <= 0.0:
            return 0.0
        noise_var = (self._sum_mumu / self._n) / self._n
        w = cov * cov / (cov * cov + noise_var)
        mean_total = self._sum_total_mu / self._n
        lam3 = w * cov * (mu_h + mu_a) / mean_total
        return min(lam3, min(mu_h, mu_a) * (1.0 - np.finfo(float).eps ** 0.5))

    def _means(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        b_h, b_a, scale_mode = self._base_means(ctx)
        x_h, x_a = self._features(ctx)
        raw_h = b_h * math.exp(float(self.theta @ x_h))
        raw_a = b_a * math.exp(float(self.theta @ x_a))
        mu_h = raw_h * self._calibration("home")
        mu_a = raw_a * self._calibration("away")
        return {
            "mu_h": mu_h, "mu_a": mu_a, "raw_h": raw_h, "raw_a": raw_a,
            "x_h": x_h, "x_a": x_a, "scale_mode": scale_mode,
        }

    # ------------------------------------------------------------ joint pmf
    def _score_matrix(self, mu_h: float, mu_a: float, lam3: float) -> np.ndarray:
        l1, l2 = mu_h - lam3, mu_a - lam3
        n = int(stats.poisson.ppf(1.0 - self._tail_tol, mu_h + mu_a)) + 2
        k = np.arange(n)
        p1, p2 = stats.poisson.pmf(k, l1), stats.poisson.pmf(k, l2)
        base = np.outer(p1, p2)
        if lam3 <= 0.0:
            m = base
        else:
            p3 = stats.poisson.pmf(k, lam3)
            m = np.zeros((n, n))
            for s in range(n):
                if p3[s] < self._tail_tol * self._tail_tol:
                    break
                m[s:, s:] += p3[s] * base[: n - s, : n - s]
        return m / m.sum()

    def _analytic_draw(self, l1: float, l2: float) -> float:
        z = 2.0 * math.sqrt(l1 * l2)
        return math.exp(-(math.sqrt(l1) - math.sqrt(l2)) ** 2) * float(special.ive(0, z))

    # ---------------------------------------------------------- public API
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        m = self._means(ctx)
        mu_h, mu_a = m["mu_h"], m["mu_a"]
        lam3 = self._lambda3(mu_h, mu_a)
        mat = self._score_matrix(mu_h, mu_a, lam3)

        p_home = float(np.tril(mat, -1).sum())
        p_draw = float(np.trace(mat))
        p_away = float(np.triu(mat, 1).sum())

        self.last_diagnostics = {
            "mu_home": mu_h, "mu_away": mu_a, "lambda3": lam3,
            "dispersion_index_total": 1.0 + 2.0 * lam3 / (mu_h + mu_a),
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
            "p_draw_analytic": self._analytic_draw(mu_h - lam3, mu_a - lam3),
            "scale_mode": m["scale_mode"], "theta": self.theta.tolist(),
            "calibration": (self._calibration("home"), self._calibration("away")),
        }
        self._last_matrix = mat

        scores = {
            (i, j): float(mat[i, j])
            for i in range(mat.shape[0]) for j in range(mat.shape[1])
            if mat[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            try:
                return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
            except Exception:
                return DiscreteDistribution(
                    probabilities={"home": p_home, "draw": p_draw, "away": p_away}
                )

    def price_winner_total(
        self, game_context: Dict[str, Any], side: str, total_line: float, over: bool
    ) -> Dict[str, float]:
        """Joint probability of (side in {home, draw, away}) AND (total over/under line)."""
        self.predict_distribution(game_context)
        mat = self._last_matrix
        i, j = np.indices(mat.shape)
        side_mask = {"home": i > j, "draw": i == j, "away": i < j}[side.lower()]
        tot = i + j
        win_mask = side_mask & ((tot > total_line) if over else (tot < total_line))
        push_mask = side_mask & (tot == total_line)
        p_win = float(mat[win_mask].sum())
        p_push = float(mat[push_mask].sum())
        p_loss = 1.0 - p_win - p_push
        decided = p_win + p_loss
        fair = decided / p_win if p_win > 0 else math.inf
        return {"p_win": p_win, "p_push": p_push, "p_loss": p_loss, "fair_odds": fair}

    def expected_value(self, priced: Dict[str, float], decimal_odds: float) -> float:
        return priced["p_win"] * (decimal_odds - 1.0) - priced["p_loss"]

    def is_eligible(self, priced: Dict[str, float], decimal_odds: float) -> bool:
        return decimal_odds >= self.MIN_ODDS and self.expected_value(priced, decimal_odds) > 0.0

    def update(self, game_context: Dict[str, Any], home_goals: int, away_goals: int) -> None:
        """Online refit from a settled period-2 score."""
        ctx = game_context or {}
        m = self._means(ctx)
        y_h, y_a = float(home_goals), float(away_goals)
        mu_h, mu_a = m["mu_h"], m["mu_a"]

        # Fisher-scoring / Laplace step for situational elasticities
        grad = (y_h - mu_h) * m["x_h"] + (y_a - mu_a) * m["x_a"]
        self.precision = (
            self.precision
            + mu_h * np.outer(m["x_h"], m["x_h"])
            + mu_a * np.outer(m["x_a"], m["x_a"])
        )
        self.theta = self.theta + np.linalg.solve(self.precision, grad)

        # Conjugate side calibration (raw means exclude the intercept itself)
        self._sum_y["home"] += y_h
        self._sum_y["away"] += y_a
        self._sum_mu["home"] += m["raw_h"]
        self._sum_mu["away"] += m["raw_a"]

        # Shared-tempo covariance moments
        self._n += 1
        self._sum_cross += (y_h - mu_h) * (y_a - mu_a)
        self._sum_mumu += mu_h * mu_a
        self._sum_total_mu += mu_h + mu_a

        # Full-game input reference
        if m["scale_mode"] == "full_game":
            h_in = self._get(ctx, "home_expected") or 0.0
            a_in = self._get(ctx, "away_expected") or 0.0
            self._n_full += 1
            self._sum_full_input += h_in + a_in