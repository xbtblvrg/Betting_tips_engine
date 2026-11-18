"""
WNBA | quarter_4 | totals: compound shared-possession model.

Structure (derived, not assumed):
    T = H + A,  H = sum_{i<=N} X_i^H,  A = sum_{j<=N} X_j^A
    N        : possessions per team in Q4. Both teams share N, which creates
               positive H/A correlation through common pace.
    N | M3   : clock-bounded, so it is near-Gaussian *within* a game state.
               Pace multiplier g(M3) = 1 + kappa*close(M3) - lambda*blowout(M3).
               g is mean-normalised, so E[T] stays anchored on
               home_expected + away_expected.
    M3       : margin entering Q4, ~ Normal(3 * quarter edge, sigma_M3). It
               collapses to a point when a live 'q3_margin' is supplied.
    X        : per-possession point value on {0,1,2,3,4}. It is an
               exponential tilt of a WNBA shape prior, solved so that
               E[X] = expected / E[N].

Overdispersion emerges from the mechanics:
    Var(T) = E[N] Var(X) terms + E[X]^2 Var(N) terms + 2 Cov(H, A)
Here Var(N) includes the game-state mixture. With priors, Var/Mean is about
1.4 to 1.6.

Note on parameters: every numeric value below is a *documented domain prior*.
Each one can be overridden through `config` or `game_context`, and the state
parameters (kappa, lambda, m_star, b_star, sd) can be re-estimated with
`fit_state_parameters` as settlements arrive. A cold-start market cannot be
fitted from zero data. Treat the referenced +89% to +118% OOS ROI "benchmarks"
as implausible for a totals market. Validate this model on held-out Brier and
log-loss before staking.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live in base or be pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


_DEFAULT_PRIORS: Dict[str, Any] = {
    # Empirical anchors (quarter-level means)
    "baseline_home_expected": 20.00,
    "baseline_away_expected": 18.75,
    # Pace: possessions per team per 40 minutes (WNBA ~ 78-82)
    "pace_per40": 80.0,
    "quarter_fraction": 10.0 / 40.0,
    # Within-state possession SD as a fraction of the mean
    # (clock-bounded => sub-Poisson)
    "possession_sd_frac": 0.08,
    # Margin entering Q4: SD of the three-quarter margin around its expectation
    "q3_margin_sd": 12.0,
    # Game-state mechanics
    "kappa": 0.10,        # close-game possession stretch (fouling, stopped clock)
    "lambda_": 0.08,      # blowout compression (bench units, clock running)
    "m_star": 5.0,        # close-game scale (points)
    "b_star": 15.0,       # blowout onset (points)
    "blowout_width": 3.0, # smoothness of blowout onset
    # Possession value shape prior on {0,1,2,3,4} (4 = and-one three / 2+2 FT)
    "value_shape": [0.50, 0.07, 0.31, 0.11, 0.01],
    # Situational effects
    "rest_rho": 0.015,     # efficiency loss scale from short rest
    "rating_weight": 0.5,  # shrinkage on attack/defense exposure (avoids double count)
    "quadrature_nodes": 21,
    "tail_sd": 8.0,        # truncation width (SDs) for the score lattice
}


class WNBAQuarter4TotalsModel(BaseCandidateModel):
    """Shared-possession compound model for WNBA fourth-quarter totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_4_1107_182611",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_4",
        family: str = "totals",
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:
            super().__init__()  # type: ignore[misc]
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family
        self.params: Dict[str, Any] = dict(_DEFAULT_PRIORS)
        if config:
            self.params.update(config)

    # ------------------------------------------------------------------ utils
    def _p(self, ctx: Dict[str, Any], key: str) -> Any:
        """Parameter lookup: game_context override > config > prior."""
        val = ctx.get(key, None) if isinstance(ctx, dict) else None
        return self.params[key] if val is None else val

    @staticmethod
    def _num(x: Any, default: float) -> float:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _rating(ctx: Dict[str, Any], side: str, kind: str) -> float:
        """Multiplicative strength index (1.0 = league average).

        For defense, an index above 1 means the side concedes more.
        """
        flat = ctx.get(f"{side}_{kind}")
        nested = ctx.get(side) if isinstance(ctx.get(side), dict) else {}
        raw = flat if flat is not None else nested.get(kind)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return 1.0
        return v if (math.isfinite(v) and v > 0) else 1.0

    # ------------------------------------------------- possession value model
    def _value_pmf(self, target_ppp: float, shape: np.ndarray) -> np.ndarray:
        """Exponential tilt of the shape prior so that E[X] = target_ppp.

        This is the maximum-entropy (minimum-KL) adjustment.
        """
        k = np.arange(len(shape), dtype=float)
        hi = float(k[-1])
        target = min(max(target_ppp, 1e-6), hi - 1e-6)

        def mean_gap(theta: float) -> float:
            w = shape * np.exp(theta * k - np.max(theta * k))
            return float((w * k).sum() / w.sum()) - target

        theta = optimize.brentq(mean_gap, -40.0, 40.0, xtol=1e-12)
        w = shape * np.exp(theta * k - np.max(theta * k))
        return w / w.sum()

    # ------------------------------------------------ possession count model
    def _possession_pmf(
        self, mu_n: float, ctx: Dict[str, Any], margin_mu: float, margin_sd: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        kappa = self._num(self._p(ctx, "kappa"), 0.0)
        lam = self._num(self._p(ctx, "lambda_"), 0.0)
        m_star = max(self._num(self._p(ctx, "m_star"), 1.0), 1e-6)
        b_star = self._num(self._p(ctx, "b_star"), 15.0)
        b_w = max(self._num(self._p(ctx, "blowout_width"), 1.0), 1e-6)
        sd_frac = max(self._num(self._p(ctx, "possession_sd_frac"), 0.05), 1e-4)

        if margin_sd <= 1e-9:
            nodes, weights = np.array([margin_mu]), np.array([1.0])
        else:
            z, w = np.polynomial.hermite_e.hermegauss(int(self.params["quadrature_nodes"]))
            nodes, weights = margin_mu + margin_sd * z, w / w.sum()

        a = np.abs(nodes)
        close = np.exp(-0.5 * (a / m_star) ** 2)
        blow = 1.0 / (1.0 + np.exp(-(a - b_star) / b_w))
        g = np.maximum(1.0 + kappa * close - lam * blow, 1e-3)
        g = g / float((weights * g).sum())  # mean-preserving: E[N] = mu_n

        tail = float(self.params["tail_sd"])
        sd_max = sd_frac * mu_n * g.max()
        n_lo = max(1, int(math.floor(mu_n * g.min() - tail * sd_max)))
        n_hi = int(math.ceil(mu_n * g.max() + tail * sd_max)) + 1
        n = np.arange(n_lo, n_hi + 1)

        pmf = np.zeros(len(n))
        for gk, wk in zip(g, weights):
            m, s = mu_n * gk, sd_frac * mu_n * gk
            pmf += wk * (stats.norm.cdf(n + 0.5, m, s) - stats.norm.cdf(n - 0.5, m, s))
        return n, pmf / pmf.sum()

    # ------------------------------------------------------- core quantities
    def _inputs(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        base_h = float(self.params["baseline_home_expected"])
        base_a = float(self.params["baseline_away_expected"])
        h_exp = self._num(ctx.get("home_expected"), base_h)
        a_exp = self._num(ctx.get("away_expected"), base_a)
        if h_exp <= 0:
            h_exp = base_h
        if a_exp <= 0:
            a_exp = base_a

        # Attack/defense exposure (shrunk; expectations may already embed ratings)
        rw = self._num(self._p(ctx, "rating_weight"), 0.0)
        h_exp *= (self._rating(ctx, "home", "attack") * self._rating(ctx, "away", "defense")) ** rw
        a_exp *= (self._rating(ctx, "away", "attack") * self._rating(ctx, "home", "defense")) ** rw

        # Rest: smooth fatigue penalty, exp(-rest) -> largest on back-to-backs
        rho = self._num(self._p(ctx, "rest_rho"), 0.0)
        ref = math.exp(-2.0)  # league-typical ~2 days of rest
        for side in ("home", "away"):
            r = max(self._num(ctx.get(f"{side}_rest_days"), 2.0), 0.0)
            mult = math.exp(-rho * (math.exp(-r) - ref))
            if side == "home":
                h_exp *= mult
            else:
                a_exp *= mult

        # Shared pace -> possessions per team in Q4
        pace = self._num(ctx.get("pace", ctx.get("expected_pace")), float(self.params["pace_per40"]))
        mu_n = max(pace * float(self._p(ctx, "quarter_fraction")), 1.0)
        max_ppp = 0.9 * (len(self.params["value_shape"]) - 1)
        mu_n = max(mu_n, max(h_exp, a_exp) / max_ppp)  # feasibility guard

        # Margin entering Q4
        if ctx.get("q3_margin") is not None:  # live state: margin is known
            m_mu, m_sd = self._num(ctx.get("q3_margin"), 0.0), 0.0
        else:
            spread = ctx.get("full_game_home_margin")
            q_edge = self._num(ctx.get("home_expected"), base_h) - self._num(ctx.get("away_expected"), base_a)
            m_mu = 0.75 * self._num(spread, 4.0 * q_edge) if spread is not None else 3.0 * q_edge
            m_sd = max(self._num(self._p(ctx, "q3_margin_sd"), 12.0), 0.0)

        return {"h_exp": h_exp, "a_exp": a_exp, "mu_n": mu_n, "m_mu": m_mu, "m_sd": m_sd}

    def _score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        inp = self._inputs(ctx)
        shape = np.asarray(self._p(ctx, "value_shape"), dtype=float)
        shape = shape / shape.sum()
        vh = self._value_pmf(inp["h_exp"] / inp["mu_n"], shape)
        va = self._value_pmf(inp["a_exp"] / inp["mu_n"], shape)
        n_grid, n_pmf = self._possession_pmf(inp["mu_n"], ctx, inp["m_mu"], inp["m_sd"])

        tail = float(self.params["tail_sd"])

        def cap(exp: float) -> int:
            return int(math.ceil(exp + tail * math.sqrt(2.0 * max(exp, 1.0)))) + 4

        cap_h, cap_a = cap(inp["h_exp"]), cap(inp["a_exp"])

        mat = np.zeros((cap_h + 1, cap_a + 1))
        ph, pa = np.array([1.0]), np.array([1.0])
        n_max = int(n_grid[-1])
        p_by_n = dict(zip(n_grid.tolist(), n_pmf.tolist()))
        for n in range(1, n_max + 1):
            ph = np.convolve(ph, vh)[: cap_h + 1]
            pa = np.convolve(pa, va)[: cap_a + 1]
            w = p_by_n.get(n, 0.0)
            if w > 0.0:
                hp = np.pad(ph, (0, cap_h + 1 - len(ph)))
                ap = np.pad(pa, (0, cap_a + 1 - len(pa)))
                mat += w * np.outer(hp, ap)
        return mat / mat.sum()

    # --------------------------------------------------------------- public
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mat = self._score_matrix(ctx)
        try:
            return DiscreteDistribution.from_scores(mat)
        except Exception:
            scores = {
                (int(h), int(a)): float(mat[h, a])
                for h, a in zip(*np.nonzero(mat > 1e-12))
            }
            return DiscreteDistribution.from_scores(scores)

    def total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        """P(T = t) for t = 0..len-1."""
        mat = self._score_matrix(game_context or {})
        h, a = np.indices(mat.shape)
        return np.bincount((h + a).ravel(), weights=mat.ravel())

    def prob_over(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Over/under/push probabilities for a total line."""
        pmf = self.total_pmf(game_context)
        t = np.arange(len(pmf))
        return {
            "over": float(pmf[t > line].sum()),
            "under": float(pmf[t < line].sum()),
            "push": float(pmf[t == line].sum()),
        }

    def moments(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        pmf = self.total_pmf(game_context)
        t = np.arange(len(pmf))
        mean = float((t * pmf).sum())
        var = float(((t - mean) ** 2 * pmf).sum())
        return {"mean": mean, "var": var, "dispersion_index": var / mean if mean > 0 else float("nan")}

    @staticmethod
    def expected_value(prob_win: float, decimal_odds: float, prob_push: float = 0.0) -> float:
        """Continuous EV per unit stake (no caps). Eligible if odds >= 1.40 and EV > 0."""
        return prob_win * (decimal_odds - 1.0) - (1.0 - prob_win - prob_push)

    # ------------------------------------------------- online re-estimation
    def fit_state_parameters(
        self,
        observations: Iterable[Dict[str, Any]],
        keys: Tuple[str, ...] = ("kappa", "lambda_", "m_star", "b_star", "possession_sd_frac"),
        prior_strength: float = 1.0,
    ) -> Dict[str, float]:
        """Penalised maximum likelihood fit of the game-state mechanics.

        Each observation is a game_context dict plus 'q4_total' (int). Supply
        'q3_margin' to condition on the realised entering margin. The penalty
        is a Gaussian shrinkage toward the current priors on the log scale for
        positive parameters, which prevents small-sample overfitting.
        """
        obs: List[Dict[str, Any]] = [o for o in observations if o.get("q4_total") is not None]
        if not obs:
            return {k: float(self.params[k]) for k in keys}

        prior = {k: float(self.params[k]) for k in keys}
        positive = {"m_star", "b_star", "possession_sd_frac"}

        def unpack(x: np.ndarray) -> Dict[str, float]:
            return {k: (math.exp(v) if k in positive else v) for k, v in zip(keys, x)}

        x0 = np.array([math.log(prior[k]) if k in positive else prior[k] for k in keys])

        def nll(x: np.ndarray) -> float:
            saved = dict(self.params)
            self.params.update(unpack(x))
            try:
                ll = 0.0
                for o in obs:
                    pmf = self.total_pmf(o)
                    t = int(o["q4_total"])
                    ll += math.log(max(pmf[t] if t < len(pmf) else 0.0, 1e-12))
            except Exception:
                ll = -1e12
            finally:
                self.params = saved
            return -ll + 0.5 * prior_strength * float(((x - x0) ** 2).sum())

        res = optimize.minimize(nll, x0, method="Nelder-Mead",
                                options={"maxiter": 400, "xatol": 1e-4, "fatol": 1e-6})
        fitted = unpack(res.x)
        self.params.update(fitted)
        return fitted