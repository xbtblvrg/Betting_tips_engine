"""
Candidate model for segment NBA | half_1 | home_total.

Structure:
    Half-time points for each team = sum of per-possession scoring outcomes
    over a random number of possessions N. The possession count is shared by
    both teams (one pace per game).

        E[P | N] = N * ppp
        Var[P | N] = N * s2_ppp
        Var[P] = E[N] * s2_ppp + ppp^2 * Var(N) + tau^2

    tau^2 is the uncertainty in the team-strength estimate, so it is not
    double-counted as scoring noise.

    N is log-normal. That gives a mild right skew, which is how blowout and
    overtime-pace halves behave. The shared N produces positive home/away
    correlation in the joint score lattice.

Note on the reported dispersion index (0.187):
    Var/Mean < 1 means the data is under-dispersed relative to Poisson, not
    over-dispersed, so it conflicts with the "heavy right tail" label. Taken
    literally on 56 points, it implies SD ≈ 3.2, which is far below observed
    NBA half-score variability (SD ≈ 8–9). It most likely measures something
    other than raw score variance, such as a residual or bet-level quantity.

    The model therefore does not plug 0.187 in as a variance. Variance comes
    from possession mechanics. The `fit()` method then re-estimates the
    variance scale, mean bias and expectation shrinkage from settled
    historical games by method of moments with ridge-style shrinkage.

    The under-heavy PnL ($71.55 under vs $17.95 over) is consistent with
    upstream expectations running high. `fit()` estimates that bias rather
    than assuming it.

All structural quantities are constructor parameters with domain-derived
defaults. None of them are clamps on the output.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


def _f(x: Any) -> Optional[float]:
    """Convert to a finite float, or return None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class NBAHalf1HomeTotalModel(BaseCandidateModel):
    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_182128",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        # Empirical anchors from the segment autopsy
        anchor_home: float = 56.0,
        anchor_away: float = 54.0,
        # Possession mechanics (NBA first half)
        half_possessions: float = 49.5,   # ~99 possessions per game / 2
        ppp_variance: float = 1.35,       # Var of points on one possession
                                          # (0/1/2/3 + and-ones + FT trips)
        pace_cv: float = 0.055,           # game-to-game possession-count CV
        strength_sd: float = 3.0,         # SD of error in pre-game expectation
        # Situational elasticities
        rest_elasticity: float = 0.008,   # efficiency change per rest day
        rest_cap_days: float = 3.0,       # rest benefit saturates (physiology)
        half_share: float = 0.505,        # 1st-half share if full-game input given
        # Expectation calibration (updated by fit())
        expectation_weight: float = 1.0,  # shrink input expectation toward anchor
        mean_bias: float = 0.0,           # additive bias correction (points)
        variance_scale: float = 1.0,      # multiplicative variance correction
        # Numerics
        n_pace_nodes: int = 11,
        tail_sd: float = 7.0,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family

        self.anchor_home = float(anchor_home)
        self.anchor_away = float(anchor_away)
        self.half_possessions = float(half_possessions)
        self.ppp_variance = float(ppp_variance)
        self.pace_cv = float(pace_cv)
        self.strength_sd = float(strength_sd)
        self.rest_elasticity = float(rest_elasticity)
        self.rest_cap_days = float(rest_cap_days)
        self.half_share = float(half_share)
        self.expectation_weight = float(expectation_weight)
        self.mean_bias = float(mean_bias)
        self.variance_scale = float(variance_scale)
        self.n_pace_nodes = int(n_pace_nodes)
        self.tail_sd = float(tail_sd)

        # Gauss–Hermite nodes for the log-normal pace mixture
        # (probabilists' Hermite polynomials; weights normalised to 1)
        x, w = np.polynomial.hermite_e.hermegauss(self.n_pace_nodes)
        self._gh_x = x
        self._gh_w = w / w.sum()

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    def _side_value(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Read e.g. ctx['home']['attack'] or ctx['home_attack']."""
        block = ctx.get(side)
        if isinstance(block, dict) and key in block:
            return _f(block.get(key))
        return _f(ctx.get(f"{side}_{key}"))

    def _to_half(self, value: Optional[float], anchor: float) -> Optional[float]:
        if value is None or value <= 0:
            return None
        # A value near twice the half anchor is a full-game expectation
        if value > 1.5 * anchor:
            return value * self.half_share
        return value

    def _rating_multiplier(self, attack: Optional[float], opp_defense: Optional[float]) -> float:
        """
        Ratings can be multiplicative (centred on 1) or additive per-100
        offsets (centred on 0). Both map to an efficiency multiplier.
        """
        def as_mult(r: Optional[float], sign: float) -> float:
            if r is None:
                return 1.0
            if 0.3 < r < 3.0:   # multiplicative convention
                return r if sign > 0 else 1.0 / r if r > 0 else 1.0
            # additive points-per-100 convention, relative to ~ league ORtg
            return math.exp(sign * r / 112.0)

        # A higher defense rating means a better defense, so fewer points
        return as_mult(attack, +1.0) * as_mult(opp_defense, -1.0)

    def _rest_multiplier(self, own_rest: Optional[float], opp_rest: Optional[float]) -> float:
        if own_rest is None and opp_rest is None:
            return 1.0
        own = min(max(own_rest if own_rest is not None else 1.0, 0.0), self.rest_cap_days)
        opp = min(max(opp_rest if opp_rest is not None else 1.0, 0.0), self.rest_cap_days)
        return math.exp(self.rest_elasticity * (own - opp))

    def _pace_multiplier(self, ctx: Dict[str, Any]) -> float:
        pace = _f(ctx.get("pace"))
        if pace is None:
            hp, ap = self._side_value(ctx, "home", "pace"), self._side_value(ctx, "away", "pace")
            vals = [p for p in (hp, ap) if p is not None and p > 0]
            pace = float(np.mean(vals)) if vals else None
        if pace is None or pace <= 0:
            return 1.0
        # Accept game pace (~99) or half pace (~49.5)
        ref = 2.0 * self.half_possessions if pace > 1.5 * self.half_possessions else self.half_possessions
        return pace / ref

    def _means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        """Return (mu_home, mu_away, expected half possessions)."""
        pace_mult = self._pace_multiplier(ctx)
        poss = self.half_possessions * pace_mult

        hr = self._side_value(ctx, "home", "rest_days")
        ar = self._side_value(ctx, "away", "rest_days")
        if hr is None:
            hr = _f(ctx.get("home_rest_days"))
        if ar is None:
            ar = _f(ctx.get("away_rest_days"))

        out = []
        for side, opp, anchor, own_rest, opp_rest in (
            ("home", "away", self.anchor_home, hr, ar),
            ("away", "home", self.anchor_away, ar, hr),
        ):
            # Structural prior: anchor × pace × matchup × rest
            prior = (
                anchor
                * pace_mult
                * self._rating_multiplier(
                    self._side_value(ctx, side, "attack"),
                    self._side_value(ctx, opp, "defense"),
                )
                * self._rest_multiplier(own_rest, opp_rest)
            )
            given = self._to_half(_f(ctx.get(f"{side}_expected")), anchor)
            if given is None:
                mu = prior
            else:
                # Upstream expectation already embeds matchup/pace.
                # Shrink it toward the structural prior by the fitted weight.
                w = self.expectation_weight
                mu = w * given + (1.0 - w) * prior
            out.append(mu)

        mu_h = out[0] + self.mean_bias
        mu_a = out[1]
        eps = 1e-6
        return max(mu_h, eps), max(mu_a, eps), max(poss, eps)

    # ------------------------------------------------------------------ #
    # Joint score lattice
    # ------------------------------------------------------------------ #
    def _score_matrix(self, mu_h: float, mu_a: float, poss: float) -> np.ndarray:
        s_pace = math.sqrt(math.log1p(self.pace_cv ** 2))
        ppp_h, ppp_a = mu_h / poss, mu_a / poss

        # Conditional variances at mean pace; used to size the lattice
        def cond_var(p: float, ppp: float) -> float:
            v = p * self.ppp_variance + self.strength_sd ** 2
            return self.variance_scale * v

        sd_h_tot = math.sqrt(cond_var(poss, ppp_h) + (ppp_h * poss * self.pace_cv) ** 2)
        sd_a_tot = math.sqrt(cond_var(poss, ppp_a) + (ppp_a * poss * self.pace_cv) ** 2)
        hi_h = int(math.ceil(mu_h + self.tail_sd * sd_h_tot)) + 1
        hi_a = int(math.ceil(mu_a + self.tail_sd * sd_a_tot)) + 1

        grid_h = np.arange(hi_h + 1, dtype=float)
        grid_a = np.arange(hi_a + 1, dtype=float)
        mat = np.zeros((hi_h + 1, hi_a + 1))

        for z, w in zip(self._gh_x, self._gh_w):
            # Log-normal pace with mean preserved: E[N] = poss
            n = poss * math.exp(s_pace * z - 0.5 * s_pace ** 2)
            pm = []
            for ppp, grid in ((ppp_h, grid_h), (ppp_a, grid_a)):
                m = n * ppp
                sd = math.sqrt(cond_var(n, ppp))
                cdf = stats.norm.cdf((np.append(grid, grid[-1] + 1.0) - 0.5 - m) / sd)
                p = np.diff(cdf)
                p[0] += cdf[0]  # fold the negative half-line into zero
                s = p.sum()
                pm.append(p / s if s > 0 else p)
            mat += w * np.outer(pm[0], pm[1])

        mat = np.clip(mat, 0.0, None)
        total = mat.sum()
        if not np.isfinite(total) or total <= 0:
            raise ValueError("Degenerate score lattice")
        return mat / total

    def _to_distribution(self, mat: np.ndarray) -> "DiscreteDistribution":
        nz = np.argwhere(mat > 1e-12)
        scores = {(int(i), int(j)): float(mat[i, j]) for i, j in nz}
        s = sum(scores.values())
        scores = {k: v / s for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(mat)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mu_h, mu_a, poss = self._means(ctx)
        mat = self._score_matrix(mu_h, mu_a, poss)
        return self._to_distribution(mat)

    def home_total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        """Marginal PMF of home first-half points (index = points)."""
        mu_h, mu_a, poss = self._means(game_context or {})
        return self._score_matrix(mu_h, mu_a, poss).sum(axis=1)

    def prob_over(self, game_context: Dict[str, Any], line: float) -> Tuple[float, float, float]:
        """(P(over), P(push), P(under)) for a home first-half total line."""
        pmf = self.home_total_pmf(game_context)
        pts = np.arange(pmf.size)
        p_over = float(pmf[pts > line].sum())
        p_push = float(pmf[pts == line].sum())
        return p_over, p_push, max(0.0, 1.0 - p_over - p_push)

    # ------------------------------------------------------------------ #
    # Calibration on settled history (method of moments + ridge shrinkage)
    # ------------------------------------------------------------------ #
    def fit(
        self,
        games: Iterable[Dict[str, Any]],
        home_key: str = "home_score_half_1",
        ridge: Optional[float] = None,
    ) -> "NBAHalf1HomeTotalModel":
        """
        Each record holds a game context plus the realised first-half home points.

        1. Expectation weight: ridge regression of the realised score on the
           (given - prior) gap, shrunk toward 1.
        2. Mean bias: the shrunk mean residual. Its shrinkage strength is the
           ratio of residual variance to sample size.
        3. Variance scale: the ratio of empirical residual variance to model
           variance, shrunk toward 1 with roughly n_eff / (n_eff + n) weighting.
        """
        rows: List[Tuple[float, float, float, float]] = []
        saved = (self.expectation_weight, self.mean_bias, self.variance_scale)
        self.expectation_weight, self.mean_bias, self.variance_scale = 0.0, 0.0, 1.0

        for g in games:
            ctx = g.get("game_context", g)
            y = _f(g.get(home_key, g.get("home_score")))
            given = self._to_half(_f(ctx.get("home_expected")), self.anchor_home)
            if y is None or given is None:
                continue
            prior, _, poss = self._means(ctx)
            rows.append((y, given, prior, poss))

        if len(rows) < 30:
            self.expectation_weight, self.mean_bias, self.variance_scale = saved
            return self

        y, g_, p_, n_ = (np.array(c) for c in zip(*rows))
        n = len(y)
        gap = g_ - p_
        r0 = y - p_

        # 1) Shrunk slope on the gap, centred on a weight of 1
        lam = ridge if ridge is not None else float(np.var(r0)) / max(np.var(gap), 1e-9)
        sxx = float(np.dot(gap, gap))
        w = (float(np.dot(gap, r0)) + lam * 1.0) / (sxx + lam) if sxx > 0 else 1.0
        self.expectation_weight = float(np.clip(w, 0.0, 1.0))

        # 2) Bias, shrunk by precision
        mu = p_ + self.expectation_weight * gap
        resid = y - mu
        rv = float(np.var(resid, ddof=1))
        shrink = n / (n + rv / max(self.strength_sd ** 2, 1e-9))
        self.mean_bias = float(shrink * resid.mean())

        # 3) Variance scale
        ppp = mu / n_
        model_var = n_ * self.ppp_variance + self.strength_sd ** 2 + (ppp * n_ * self.pace_cv) ** 2
        ratio = float(np.mean((resid - self.mean_bias) ** 2) / np.mean(model_var))
        k = 2.0 / (2.0 + n / 50.0)  # weight on the prior of 1; roughly 1/(1 + n/100)
        self.variance_scale = float(k * 1.0 + (1.0 - k) * ratio)
        return self


CandidateModel = NBAHalf1HomeTotalModel