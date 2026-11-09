"""
Candidate model for UEL | half_2 | three_way.

Second-half-only goals modelled as a bivariate Poisson with a shared
component, plus a Dixon-Coles low-score correction, aggregated into
P(home), P(draw), P(away).

Design notes:
  * Cold start: there are 0 settled bets. The covariance term (lambda3) and the
    Dixon-Coles rho therefore default to 0, so the model is exactly
    independent Poisson at (0.80, 0.60) until `fit()` receives real
    second-half scorelines. Both are shrunk toward 0 by sample size.
  * The supplied hypothesis wrote the rest effect as a multiplier
    (1 - exp(-delta_rest)). That would zero out home goals whenever rest days
    are equal, so it is replaced with a bounded, smooth multiplier that equals
    1 at delta_rest = 0.
  * `home_expected` / `away_expected` are treated as SECOND-HALF goal
    expectations, which is consistent with the 0.80 / 0.60 baselines.
    Multiplying them again by a second-half share would double-count. If only
    full-match expectations are available, pass `full_match_expected=True` in
    the context and the empirical second-half share is applied.
  * Attack/defense ratings are applied only when expectations are absent, so
    information already priced into the expectations is not counted twice.
  * Fixed numbers in this file are documented PRIORS exposed as constructor
    arguments, not outcome clamps. The only bounds are mathematical validity
    constraints: rho must keep tau >= 0, and lambda3 must stay below each
    marginal rate.
  * No edge is claimed. Validate out-of-sample (log loss / Brier against
    closing prices) before deploying stakes.
"""

import math
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live in base or be pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class UELHalf2ThreeWayModel(BaseCandidateModel):
    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_2_1107_165852",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_2",
        family: str = "three_way",
        # --- empirical anchors (cold-start baselines, 2H goals) ---
        prior_home_2h: float = 0.80,
        prior_away_2h: float = 0.60,
        # Share of full-match goals scored after the break. Used only when the
        # context supplies full-match expectations.
        second_half_share: float = 0.55,
        # --- priors for situational effects (log-scale sensitivities) ---
        rest_sensitivity: float = 0.03,   # max log-rate shift from rest edge
        rest_scale_days: float = 3.0,     # rest-day difference that saturates tanh
        rating_elasticity: float = 1.0,   # exponent on attack/defense ratios
        # Partial pooling of the context home/away ratio toward the empirical
        # 2H ratio (0 = trust the context fully). Targets home over-confidence
        # once data justify it.
        home_ratio_pooling: float = 0.0,
        # --- dependence parameters (cold start -> independent Poisson) ---
        shared_lambda: float = 0.0,
        dc_rho: float = 0.0,
        # Pseudo-sample size that shrinks fitted dependence toward 0
        shrinkage_pseudo_n: float = 200.0,
        tail_tolerance: float = 1e-12,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.prior_home_2h = float(prior_home_2h)
        self.prior_away_2h = float(prior_away_2h)
        self.second_half_share = float(second_half_share)
        self.rest_sensitivity = float(rest_sensitivity)
        self.rest_scale_days = float(rest_scale_days)
        self.rating_elasticity = float(rating_elasticity)
        self.home_ratio_pooling = float(home_ratio_pooling)
        self.shared_lambda = float(shared_lambda)
        self.dc_rho = float(dc_rho)
        self.shrinkage_pseudo_n = float(shrinkage_pseudo_n)
        self.tail_tolerance = float(tail_tolerance)
        self.empirical_home_ratio = self.prior_home_2h / self.prior_away_2h
        self.n_fitted = 0

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        block = ctx.get(side)
        val = None
        if isinstance(block, dict):
            val = self._num(block.get(key))
        if val is None:
            val = self._num(ctx.get(f"{side}_{key}"))
        return val if (val is not None and val > 0) else 1.0

    def _base_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mh = self._num(ctx.get("home_expected"))
        ma = self._num(ctx.get("away_expected"))

        if mh is not None and mh > 0 and ma is not None and ma > 0:
            if ctx.get("full_match_expected"):
                mh *= self.second_half_share
                ma *= self.second_half_share
        else:
            # Fall back to the anchors, scaled by ratings.
            # attack > 1 means stronger attack; defense > 1 means leakier defence.
            e = self.rating_elasticity
            mh = self.prior_home_2h * (
                self._rating(ctx, "home", "attack")
                * self._rating(ctx, "away", "defense")
            ) ** e
            ma = self.prior_away_2h * (
                self._rating(ctx, "away", "attack")
                * self._rating(ctx, "home", "defense")
            ) ** e

        # Pool the home/away ratio toward the empirical 2H ratio, keeping the
        # total (geometric mean) fixed.
        w = self.home_ratio_pooling
        if w > 0:
            g = math.sqrt(mh * ma)
            log_r = (1 - w) * math.log(mh / ma) + w * math.log(self.empirical_home_ratio)
            mh, ma = g * math.exp(0.5 * log_r), g * math.exp(-0.5 * log_r)

        # Rest / congestion: smooth and bounded, neutral at equal rest.
        hr = self._num(ctx.get("home_rest_days"))
        ar = self._num(ctx.get("away_rest_days"))
        if hr is not None and ar is not None and self.rest_scale_days > 0:
            shift = self.rest_sensitivity * math.tanh((hr - ar) / self.rest_scale_days)
            mh *= math.exp(shift)
            ma *= math.exp(-shift)

        return mh, ma

    def _components(self, mh: float, ma: float) -> Tuple[float, float, float]:
        # Smooth map keeps lambda3 strictly below min(mh, ma), so l1, l2 > 0.
        m = min(mh, ma)
        l3 = self.shared_lambda * m / (m + self.shared_lambda) if self.shared_lambda > 0 else 0.0
        return mh - l3, ma - l3, l3

    def _grid_size(self, mh: float, ma: float) -> int:
        return int(stats.poisson.ppf(1.0 - self.tail_tolerance, mh + ma)) + 2

    def _score_matrix(self, mh: float, ma: float, rho: float) -> np.ndarray:
        l1, l2, l3 = self._components(mh, ma)
        n = self._grid_size(mh, ma)
        g = np.arange(n)
        p1, p2 = stats.poisson.pmf(g, l1), stats.poisson.pmf(g, l2)

        if l3 > 0:
            P = np.zeros((n, n))
            p3 = stats.poisson.pmf(g, l3)
            for k in range(n):
                if p3[k] < self.tail_tolerance * 1e-3:
                    break
                P[k:, k:] += p3[k] * np.outer(p1[: n - k], p2[: n - k])
        else:
            P = np.outer(p1, p2)

        if rho != 0.0:
            rho = self._valid_rho(mh, ma, rho)
            P[0, 0] *= 1 - mh * ma * rho
            P[0, 1] *= 1 + mh * rho
            P[1, 0] *= 1 + ma * rho
            P[1, 1] *= 1 - rho

        return P / P.sum()

    @staticmethod
    def _rho_bounds(mh: float, ma: float) -> Tuple[float, float]:
        # Validity constraint: every tau factor must stay >= 0.
        lo = max(-1.0 / mh, -1.0 / ma)
        hi = min(1.0 / (mh * ma), 1.0)
        return lo, hi

    def _valid_rho(self, mh: float, ma: float, rho: float) -> float:
        lo, hi = self._rho_bounds(mh, ma)
        return min(max(rho, lo), hi)

    # ------------------------------------------------------------------ #
    # Calibration from observed second-half scorelines
    # ------------------------------------------------------------------ #
    def fit(
        self,
        scorelines: Iterable[Sequence[float]],
        contexts: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> "UELHalf2ThreeWayModel":
        data = np.asarray([(float(h), float(a)) for h, a in scorelines], dtype=float)
        n = len(data)
        self.n_fitted = n
        if n < 2:
            return self  # stay at cold-start priors

        h, a = data[:, 0], data[:, 1]
        k = n / (n + self.shrinkage_pseudo_n)

        if h.mean() > 0 and a.mean() > 0:
            self.prior_home_2h, self.prior_away_2h = float(h.mean()), float(a.mean())
            self.empirical_home_ratio = self.prior_home_2h / self.prior_away_2h

        # Shared Poisson component = covariance (only non-negative is admissible).
        cov = float(np.cov(h, a, ddof=1)[0, 1])
        self.shared_lambda = k * max(cov, 0.0)

        # rho by penalized MLE, with a Gaussian prior centred at 0 (Poisson).
        ctxs = list(contexts) if contexts is not None else [None] * n
        means = [
            self._base_means(c) if c is not None else (self.prior_home_2h, self.prior_away_2h)
            for c in ctxs
        ]
        lo = max(self._rho_bounds(mh, ma)[0] for mh, ma in means)
        hi = min(self._rho_bounds(mh, ma)[1] for mh, ma in means)
        prior_sd = 1.0 / math.sqrt(self.shrinkage_pseudo_n)

        def nll(rho: float) -> float:
            total = 0.0
            for (mh, ma), (hi_, ai_) in zip(means, data.astype(int)):
                P = self._score_matrix(mh, ma, rho)
                p = P[hi_, ai_] if hi_ < P.shape[0] and ai_ < P.shape[1] else self.tail_tolerance
                total -= math.log(max(p, 1e-300))
            return total + 0.5 * (rho / prior_sd) ** 2

        if lo < hi:
            res = optimize.minimize_scalar(nll, bounds=(lo, hi), method="bounded")
            if res.success:
                self.dc_rho = float(res.x)
        return self

    # ------------------------------------------------------------------ #
    # Prediction
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mh, ma = self._base_means(game_context or {})
        P = self._score_matrix(mh, ma, self.dc_rho)

        p_home = float(np.tril(P, -1).sum())  # home goals > away goals
        p_draw = float(np.trace(P))
        p_away = float(np.triu(P, 1).sum())
        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

        if hasattr(DiscreteDistribution, "from_probabilities"):
            try:
                return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
            except Exception:
                pass
        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )

    def diagnostics(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        mh, ma = self._base_means(game_context or {})
        l1, l2, l3 = self._components(mh, ma)
        P = self._score_matrix(mh, ma, self.dc_rho)
        return {
            "mu_home_2h": mh, "mu_away_2h": ma,
            "lambda1": l1, "lambda2": l2, "lambda3": l3,
            "rho": self._valid_rho(mh, ma, self.dc_rho) if self.dc_rho else 0.0,
            "p_0_0": float(P[0, 0]), "p_draw": float(np.trace(P)),
            "n_fitted": float(self.n_fitted),
        }