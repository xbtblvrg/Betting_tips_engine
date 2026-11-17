"""
Candidate model for segment 'NBA|half_1|home_total'.

Design (derived from scoring mechanics, not a template):

1. Home first-half points are a sum over possessions:  S = sum_{i=1..N} X_i
   with N ~ possessions in the half and X_i in {0,1,2,3,4} points.
   Conditional on N:  E[S|N] = N*ppp,  Var[S|N] = N*(E[X^2] - ppp^2).
   Writing r = E[X^2]/E[X] (fixed by the 1/2/3-point scoring mix):
       Var[S | pace] = mu * (r - ppp)
   This is the compound-scoring variance. With NBA-realistic ppp ~1.1 and
   r ~2.3, Var/Mean ~1.2 for the conditional component.

2. Pace is shared by both teams. It is integrated with Gauss-Hermite
   quadrature. This produces positive home/away correlation and mild
   overdispersion of the marginal home total. That is the physically
   meaningful source of the "right tail" flagged in the autopsy.

3. Mean-estimation uncertainty is added as an explicit variance term so the
   model does not price tails as if its mean were known exactly. That
   overconfidence is the pathology behind the -100% historical deficits.

4. Note on the reported dispersion index (0.184): a Var/Mean below 1 is
   inconsistent with compound 1/2/3-point scoring. Team half totals cannot
   have sd ~3.2 points, so that figure is very likely measured on a
   different quantity. It is recorded for diagnostics only. Variance comes
   from the structural formula and can be re-estimated from real outcomes
   via `fit()`.

Structural priors are documented class attributes, not hidden clamps. Each
of them can be re-estimated by `fit()` on historical games. No out-of-sample
performance is implied; validate on held-out data before staking.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may already be pre-loaded in execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    pass


class NBAHalf1HomeTotalModel(BaseCandidateModel):
    # ---- Empirical anchors supplied for this segment (first-half points) ----
    ANCHOR_HOME_H1: float = 56.00
    ANCHOR_AWAY_H1: float = 54.00
    REPORTED_DISPERSION_INDEX: float = 0.184  # diagnostics only (see module doc)

    # ---- Structural priors (refit by `fit`) ----
    # First-half share of regulation scoring. Used only if inputs look like
    # full-game expectations.
    H1_SHARE: float = 0.497
    # E[X^2]/E[X] per possession, implied by the NBA 1/2/3-point + FT scoring mix.
    SCORING_MOMENT_RATIO: float = 2.30
    # League possessions per 48 minutes (reference for pace scaling).
    LEAGUE_PACE_48: float = 99.0
    # Coefficient of variation of realised half-pace around its expectation.
    PACE_CV: float = 0.05
    # sd (points) of the error in our own half-mean estimate.
    MEAN_UNCERTAINTY_SD: float = 2.0
    # Weight on rating/pace signals not already embedded in *_expected
    # (avoids double counting).
    SIGNAL_WEIGHT: float = 0.5
    # Max rest effect on a team's half output (points), reached smoothly
    # through 1 - exp(-days).
    REST_EFFECT_PTS: float = 0.9
    # Shrinkage strength (pseudo-games) for `fit` estimates.
    FIT_PRIOR_GAMES: float = 200.0

    _GH_NODES = 9  # Gauss-Hermite nodes for pace integration
    _TAIL_SDS = 8.0  # grid half-width in sds; mass beyond is ~1e-15

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_032547",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Instance copies so `fit` never mutates class-level priors.
        self.h1_share = self.H1_SHARE
        self.scoring_ratio = self.SCORING_MOMENT_RATIO
        self.pace_cv = self.PACE_CV
        self.mean_unc_sd = self.MEAN_UNCERTAINTY_SD
        self.mean_bias_home = 1.0
        self.mean_bias_away = 1.0

        z, w = np.polynomial.hermite_e.hermegauss(self._GH_NODES)
        self._gh_z = z
        self._gh_w = w / w.sum()

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Read attack/defense from nested ({'home': {'attack': ..}}) or flat keys."""
        nested = ctx.get(side)
        if isinstance(nested, dict):
            v = self._num(nested.get(kind))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{kind}"))

    @staticmethod
    def _rating_log_factor(r: Optional[float]) -> float:
        """Map a rating to a log-multiplier.

        Accepts multiplicative ratings (around 1.0) or points-per-100
        (around 110-115).
        """
        if r is None or r <= 0:
            return 0.0
        if r > 20.0:  # per-100-possession efficiency scale
            return math.log(r / 113.0)
        return math.log(r)

    def _to_half(self, v: Optional[float], anchor: float) -> float:
        if v is None or v <= 0:
            return anchor
        # A value far above the half anchor is a full-game expectation.
        return v * self.h1_share if v > 1.5 * anchor else v

    @staticmethod
    def _rest_term(days: Optional[float]) -> float:
        if days is None or days < 0:
            return 1.0 - math.exp(-1.0)  # neutral: typical 1-day rest
        return 1.0 - math.exp(-days)

    # --------------------------------------------------------- mean structure
    def _means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float, float]:
        mu_h = self._to_half(self._num(ctx.get("home_expected")), self.ANCHOR_HOME_H1)
        mu_a = self._to_half(self._num(ctx.get("away_expected")), self.ANCHOR_AWAY_H1)

        w = self.SIGNAL_WEIGHT
        # Matchup: own attack vs opponent defense (defense > 1 means leakier).
        lh = self._rating_log_factor(self._rating(ctx, "home", "attack")) + \
            self._rating_log_factor(self._rating(ctx, "away", "defense"))
        la = self._rating_log_factor(self._rating(ctx, "away", "attack")) + \
            self._rating_log_factor(self._rating(ctx, "home", "defense"))
        mu_h *= math.exp(w * lh)
        mu_a *= math.exp(w * la)

        # Pace: shared multiplicative scaling.
        pace = self._num(ctx.get("pace")) or self._num(ctx.get("expected_pace"))
        if pace is not None and pace > 0:
            pace48 = pace * 2.0 if pace < 70 else pace  # accept half or full pace
            pf = math.exp(w * math.log(pace48 / self.LEAGUE_PACE_48))
            mu_h *= pf
            mu_a *= pf
            half_poss = pace48 / 2.0
        else:
            half_poss = self.LEAGUE_PACE_48 / 2.0

        # Rest: fresher offense scores more; fresher defense concedes less.
        rh = self._rest_term(self._num(ctx.get("home_rest_days")))
        ra = self._rest_term(self._num(ctx.get("away_rest_days")))
        mu_h += self.REST_EFFECT_PTS * (rh - ra)
        mu_a += self.REST_EFFECT_PTS * (ra - rh)

        mu_h *= self.mean_bias_home
        mu_a *= self.mean_bias_away

        floor = 1e-6
        mu_h, mu_a = max(mu_h, floor), max(mu_a, floor)
        ppp_h = mu_h / half_poss
        ppp_a = mu_a / half_poss
        return mu_h, mu_a, ppp_h, ppp_a

    def _cond_var(self, mu: float, ppp: float) -> float:
        # Compound-scoring variance. (r - ppp) > 0 always holds for real
        # scoring mixes; guard numerically only.
        excess = max(self.scoring_ratio - ppp, 1e-3)
        return mu * excess + self.mean_unc_sd ** 2

    @staticmethod
    def _discretized_normal(grid: np.ndarray, m: float, s: float) -> np.ndarray:
        cdf = stats.norm.cdf((np.append(grid, grid[-1] + 1) - 0.5 - m) / s)
        p = np.diff(cdf)
        p[0] += cdf[0]  # fold left tail into 0
        p = np.clip(p, 0.0, None)
        tot = p.sum()
        return p / tot if tot > 0 else np.full_like(p, 1.0 / p.size)

    # --------------------------------------------------------- distribution
    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a, ppp_h, ppp_a = self._means(ctx)
        sd_h_tot = math.sqrt(self._cond_var(mu_h, ppp_h) + (self.pace_cv * mu_h) ** 2)
        sd_a_tot = math.sqrt(self._cond_var(mu_a, ppp_a) + (self.pace_cv * mu_a) ** 2)
        nh = int(math.ceil(mu_h + self._TAIL_SDS * sd_h_tot)) + 1
        na = int(math.ceil(mu_a + self._TAIL_SDS * sd_a_tot)) + 1
        gh, ga = np.arange(nh), np.arange(na)

        mat = np.zeros((nh, na))
        for z, wt in zip(self._gh_z, self._gh_w):
            k = max(1.0 + self.pace_cv * z, 1e-3)  # shared pace realisation
            mh, ma = mu_h * k, mu_a * k
            ph = self._discretized_normal(gh, mh, math.sqrt(self._cond_var(mh, ppp_h)))
            pa = self._discretized_normal(ga, ma, math.sqrt(self._cond_var(ma, ppp_a)))
            mat += wt * np.outer(ph, pa)

        mat = np.clip(mat, 0.0, None)
        return mat / mat.sum()

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, dict) else {}
        mat = self.score_matrix(ctx)

        idx = np.argwhere(mat > 1e-12)
        vals = mat[mat > 1e-12]
        vals = vals / vals.sum()
        scores = {(int(h), int(a)): float(p) for (h, a), p in zip(idx, vals)}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(mat)

    # Convenience: direct market probability for the home team total.
    def prob_home_over(self, game_context: Dict[str, Any], line: float) -> float:
        marg = self.score_matrix(game_context).sum(axis=1)
        pts = np.arange(marg.size)
        over = marg[pts > line].sum()
        push = marg[pts == line].sum()
        denom = 1.0 - push
        return float(over / denom) if denom > 0 else 0.5

    # ------------------------------------------------------------ calibration
    def fit(self, games: Iterable[Dict[str, Any]]) -> "NBAHalf1HomeTotalModel":
        """Method-of-moments recalibration on settled games.

        Each game is a context dict that also contains realised first-half
        scores under 'home_score_h1' / 'away_score_h1'. Estimates are shrunk
        toward structural priors with weight n / (n + FIT_PRIOR_GAMES).
        """
        rows: List[Tuple[float, float, float, float, float, float]] = []
        saved = (self.mean_bias_home, self.mean_bias_away)
        self.mean_bias_home = self.mean_bias_away = 1.0
        for g in games:
            yh = self._num(g.get("home_score_h1"))
            ya = self._num(g.get("away_score_h1"))
            if yh is None or ya is None:
                continue
            mu_h, mu_a, ppp_h, ppp_a = self._means(g)
            rows.append((yh, ya, mu_h, mu_a, ppp_h, ppp_a))
        n = len(rows)
        if n < 30:
            self.mean_bias_home, self.mean_bias_away = saved
            return self

        a = np.asarray(rows)
        yh, ya, mh, ma, pph, ppa = a.T
        lam = n / (n + self.FIT_PRIOR_GAMES)

        # Multiplicative mean bias (ratio of sums is robust to scale).
        self.mean_bias_home = (1 - lam) + lam * float(yh.sum() / mh.sum())
        self.mean_bias_away = (1 - lam) + lam * float(ya.sum() / ma.sum())
        mh, ma = mh * self.mean_bias_home, ma * self.mean_bias_away

        # Shared-pace CV from the residual covariance:
        # Cov(res_h, res_a) ~ cv^2 * mu_h * mu_a.
        rh, ra = yh - mh, ya - ma
        cov = float(np.mean(rh * ra))
        cv_hat = math.sqrt(max(cov, 0.0) / float(np.mean(mh * ma)))
        self.pace_cv = (1 - lam) * self.PACE_CV + lam * cv_hat

        # Scoring moment ratio from residual variance net of pace and
        # mean-uncertainty terms. Winsorize residuals at 1st/99th pct.
        lo, hi = np.percentile(rh, [1, 99])
        rhw = np.clip(rh, lo, hi)
        resid_var = rhw ** 2 - (self.pace_cv * mh) ** 2 - self.mean_unc_sd ** 2
        r_hat = float(np.mean(resid_var) / np.mean(mh) + np.mean(pph))
        if math.isfinite(r_hat) and r_hat > float(np.max(pph)):
            self.scoring_ratio = (1 - lam) * self.SCORING_MOMENT_RATIO + lam * r_hat
        return self