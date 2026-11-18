import math
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in scope; import defensively.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class NcaaWomenHockeyP1DoubleChanceModel(BaseCandidateModel):
    """
    First-period double-chance model for NCAA women's ice hockey.

    Structure
    ---------
    1. Scoring rates:
           lambda_H = mu_H * (A_H / D_A) * tau_pace * rest_H
           lambda_A = mu_A * (A_A / D_H) * tau_pace * rest_A
       where mu_H, mu_A are the empirical P1 anchors (0.53 / 0.40). Attack and
       defence ratings are shrunk toward 1 in log space, with credibility that
       grows with games played.
    2. Strength heterogeneity: each rate is Gamma-mixed with shape k, so the
       marginals are Negative Binomial. k = inf gives pure Poisson, which matches
       the observed dispersion index of 1.000. k is only loosened by `fit`.
    3. Low-score dependence: a Dixon-Coles tau on cells (0,0), (1,0), (0,1), (1,1).
       rho starts from a weak cold-start prior that inflates 0-0 and 1-1, and is
       learned by penalised MLE in `fit`. rho is kept inside its analytic
       validity region, so every adjusted cell mass stays non-negative.
    4. Output: a coherent 3-way distribution (home / draw / away). The
       double-chance legs follow as complements:
           P(1X) = 1 - P(A), P(X2) = 1 - P(H), P(12) = 1 - P(D).
       Home-ice confidence is therefore bounded by the draw mass.

    All hyper-parameters are exposed as constructor arguments and act as
    priors, not clamps. `fit` updates rho and k from settled score data.
    """

    # Regulation structure: three 20-minute periods.
    _PERIODS_PER_GAME = 3

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_ice_hockey_women_period_1_1108_052539",
        version: str = "1.0.0",
        league: str = "NCAA_ICE_HOCKEY_WOMEN",
        scope: str = "period_1",
        family: str = "double_chance",
        base_home_rate: float = 0.53,
        base_away_rate: float = 0.40,
        observed_dispersion_index: float = 1.000,
        rho_prior_mean: float = -0.08,
        rho_prior_sd: float = 0.10,
        rating_prior_games: float = 8.0,
        default_rating_games: float = 8.0,
        rest_effect_per_log_day: float = 0.03,
        p1_share_of_game_scoring: Optional[float] = None,
        tail_eps: float = 1e-12,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:  # base class with a different signature
            super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.base_home_rate = float(base_home_rate)
        self.base_away_rate = float(base_away_rate)
        self.base_total = self.base_home_rate + self.base_away_rate

        # Gamma shape implied by the dispersion index D = 1 + mu / k.
        # D <= 1 means no overdispersion evidence, so use the Poisson limit.
        excess = float(observed_dispersion_index) - 1.0
        self.k_shape = (self.base_total / 2.0) / excess if excess > 0 else math.inf

        self.rho_prior_mean = float(rho_prior_mean)
        self.rho_prior_sd = float(rho_prior_sd)
        self.rho = self.rho_prior_mean

        self.rating_prior_games = float(rating_prior_games)
        self.default_rating_games = float(default_rating_games)
        self.rest_effect = float(rest_effect_per_log_day)

        # Without an explicit share, assume uniform scoring across periods.
        self.p1_share = (
            float(p1_share_of_game_scoring)
            if p1_share_of_game_scoring is not None
            else 1.0 / self._PERIODS_PER_GAME
        )
        self.tail_eps = float(tail_eps)

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _get(ctx: Dict[str, Any], *paths: Sequence[str]) -> Optional[float]:
        """Return the first numeric value found along any of the key paths."""
        for path in paths:
            node: Any = ctx
            ok = True
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    ok = False
                    break
            if ok and node is not None:
                try:
                    val = float(node)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(val):
                    return val
        return None

    def _shrunk_log_rating(self, raw: Optional[float], games: Optional[float]) -> float:
        """Log-space credibility shrinkage of a multiplicative rating toward 1."""
        if raw is None or raw <= 0:
            return 0.0
        n = games if (games is not None and games > 0) else self.default_rating_games
        weight = n / (n + self.rating_prior_games)
        return weight * math.log(raw)

    def _team_ratings(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        att = self._get(
            ctx,
            (side, "attack"), (side, "attack_rating"), (side, "offense"),
            (f"{side}_attack",), (f"{side}_attack_rating",), (f"{side}_offense",),
        )
        dfn = self._get(
            ctx,
            (side, "defense"), (side, "defense_rating"), (side, "defence"),
            (f"{side}_defense",), (f"{side}_defense_rating",), (f"{side}_defence",),
        )
        games = self._get(
            ctx, (side, "games_played"), (side, "games"), (f"{side}_games_played",)
        )
        return self._shrunk_log_rating(att, games), self._shrunk_log_rating(dfn, games)

    def _period_scale(self, home_exp: float, away_exp: float) -> float:
        """
        Detect whether supplied expectations are full-game or period-1 scale.

        The log-midpoint between the period-1 and full-game magnitudes is the
        decision boundary, so no arbitrary threshold is introduced.
        """
        total = home_exp + away_exp
        if total <= 0:
            return 1.0
        ratio = total / self.base_total
        boundary = math.sqrt(1.0 / self.p1_share)
        return self.p1_share if ratio > boundary else 1.0

    # ------------------------------------------------------------------ rates
    def _rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        home_exp = self._get(ctx, ("home_expected",), ("home", "expected"))
        away_exp = self._get(ctx, ("away_expected",), ("away", "expected"))

        if home_exp is not None and away_exp is not None and home_exp > 0 and away_exp > 0:
            scale = self._period_scale(home_exp, away_exp)
            mu_h, mu_a = home_exp * scale, away_exp * scale
            # The supplied expectations already encode team strength, so the
            # ratings only contribute their shrunk residual information.
            rating_weight = 0.5
        else:
            mu_h, mu_a = self.base_home_rate, self.base_away_rate
            rating_weight = 1.0

        a_h, d_h = self._team_ratings(ctx, "home")
        a_a, d_a = self._team_ratings(ctx, "away")
        # A rating > 1 means a stronger attack and a weaker (more porous) defence.
        log_h = rating_weight * (a_h + d_a)
        log_a = rating_weight * (a_a + d_h)

        # Pace: relative tempo multiplier, defaults to neutral.
        pace = self._get(
            ctx, ("pace",), ("tempo",), ("pace_factor",), ("venue", "pace_factor")
        )
        hp = self._get(ctx, ("home", "pace"), ("home_pace",))
        ap = self._get(ctx, ("away", "pace"), ("away_pace",))
        log_tau = 0.0
        if pace is not None and pace > 0:
            log_tau += math.log(pace)
        if hp is not None and ap is not None and hp > 0 and ap > 0:
            log_tau += 0.5 * (math.log(hp) + math.log(ap))

        # Rest: diminishing returns via log(1 + days). The effect is antisymmetric
        # on the rest gap, so total expectation is preserved.
        hr = self._get(ctx, ("home_rest_days",), ("home", "rest_days"))
        ar = self._get(ctx, ("away_rest_days",), ("away", "rest_days"))
        rest = 0.0
        if hr is not None and ar is not None:
            rest = self.rest_effect * (math.log1p(max(hr, 0.0)) - math.log1p(max(ar, 0.0)))

        lam_h = mu_h * math.exp(log_h + log_tau + rest)
        lam_a = mu_a * math.exp(log_a + log_tau - rest)
        return lam_h, lam_a

    # ------------------------------------------------------------ score grid
    def _marginal(self, lam: float, k: float, n_max: int) -> np.ndarray:
        goals = np.arange(n_max + 1)
        if math.isinf(k):
            return stats.poisson.pmf(goals, lam)
        p = k / (k + lam)
        return stats.nbinom.pmf(goals, k, p)

    def _grid_size(self, lam: float, k: float) -> int:
        if math.isinf(k):
            n = stats.poisson.ppf(1.0 - self.tail_eps, lam)
        else:
            n = stats.nbinom.ppf(1.0 - self.tail_eps, k, k / (k + lam))
        return int(max(n, 3))

    @staticmethod
    def _rho_bounds(lam_h: float, lam_a: float) -> Tuple[float, float]:
        """Analytic region where all Dixon-Coles tau factors stay non-negative."""
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        return lo, hi

    def score_matrix(
        self, lam_h: float, lam_a: float, rho: Optional[float] = None, k: Optional[float] = None
    ) -> np.ndarray:
        rho = self.rho if rho is None else rho
        k = self.k_shape if k is None else k
        lo, hi = self._rho_bounds(lam_h, lam_a)
        rho = min(max(rho, lo), hi)

        n = max(self._grid_size(lam_h, k), self._grid_size(lam_a, k))
        ph = self._marginal(lam_h, k, n)
        pa = self._marginal(lam_a, k, n)
        m = np.outer(ph, pa)

        m[0, 0] *= 1.0 - lam_h * lam_a * rho
        m[0, 1] *= 1.0 + lam_h * rho
        m[1, 0] *= 1.0 + lam_a * rho
        m[1, 1] *= 1.0 - rho

        total = m.sum()
        return m / total if total > 0 else m

    @staticmethod
    def _three_way(m: np.ndarray) -> Tuple[float, float, float]:
        p_d = float(np.trace(m))
        p_h = float(np.tril(m, -1).sum())  # home goals (row) > away goals (col)
        p_a = float(np.triu(m, 1).sum())
        s = p_h + p_d + p_a
        return p_h / s, p_d / s, p_a / s

    # ------------------------------------------------------------- interface
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a = self._rates(ctx)
        m = self.score_matrix(lam_h, lam_a)
        p_h, p_d, p_a = self._three_way(m)
        return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)

    def double_chance_probabilities(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        """Coherent double-chance legs derived as complements of the 3-way."""
        lam_h, lam_a = self._rates(game_context or {})
        p_h, p_d, p_a = self._three_way(self.score_matrix(lam_h, lam_a))
        return {"1X": 1.0 - p_a, "X2": 1.0 - p_h, "12": 1.0 - p_d}

    @staticmethod
    def expected_value(prob: float, decimal_odds: float) -> float:
        """Continuous EV per unit stake. Eligibility is EV > 0 and odds >= 1.40."""
        return prob * decimal_odds - 1.0

    # --------------------------------------------------------------- learning
    def fit(self, settled: Iterable[Dict[str, Any]]) -> "NcaaWomenHockeyP1DoubleChanceModel":
        """
        Penalised MLE update of (rho, 1/k) from settled first-period scores.

        Each record must contain the game context plus 'home_p1_goals' and
        'away_p1_goals'. Priors: rho ~ N(rho_prior_mean, rho_prior_sd), and an
        exponential prior on 1/k centred near zero (Poisson). Cold start returns
        the priors unchanged.
        """
        rows = []
        for rec in settled or []:
            hg = self._get(rec, ("home_p1_goals",), ("home_goals",))
            ag = self._get(rec, ("away_p1_goals",), ("away_goals",))
            if hg is None or ag is None:
                continue
            lam_h, lam_a = self._rates(rec)
            rows.append((int(hg), int(ag), lam_h, lam_a))
        if not rows:
            return self

        # The prior rate on 1/k corresponds to the overdispersion one would
        # expect from roughly one rating-prior's worth of games.
        inv_k_prior_rate = self.rating_prior_games

        def nll(theta: np.ndarray) -> float:
            rho, log_inv_k = theta
            inv_k = math.exp(log_inv_k)
            k = 1.0 / inv_k
            ll = 0.0
            for hg, ag, lh, la in rows:
                m = self.score_matrix(lh, la, rho=rho, k=k)
                if hg >= m.shape[0] or ag >= m.shape[1]:
                    ll += math.log(self.tail_eps)
                else:
                    ll += math.log(max(m[hg, ag], self.tail_eps))
            prior = (
                -0.5 * ((rho - self.rho_prior_mean) / self.rho_prior_sd) ** 2
                - inv_k_prior_rate * inv_k
                + log_inv_k  # Jacobian of the log-transform
            )
            return -(ll + prior)

        start_inv_k = 1.0 / self.k_shape if math.isfinite(self.k_shape) else 1e-4
        x0 = np.array([self.rho, math.log(start_inv_k)])
        res = optimize.minimize(nll, x0, method="Nelder-Mead",
                                options={"xatol": 1e-5, "fatol": 1e-7, "maxiter": 2000})
        if res.success or np.isfinite(res.fun):
            self.rho = float(res.x[0])
            self.k_shape = 1.0 / math.exp(float(res.x[1]))
        return self