"""
Candidate model for NCAAW | quarter_3 | away_total.

This is a cold-start segment with zero settled wagers. No realized edge or ROI
can be claimed, and the benchmark figures in the brief cannot be verified for
this segment. Every structural parameter below is a documented domain prior,
not a fitted value. Validate out-of-sample (log-loss / Brier / CLV) before
staking.

Mechanics:
  Away Q3 points = sum over N possessions of per-possession points X.
  * N   : possession count in the quarter. It is shared by both teams, which
          induces natural home/away score correlation through pace. It is a
          discretised normal around the tempo-implied mean.
  * X   : points per possession in {0,1,2,3}. The base outcome shares come
          from NCAAW shot/turnover mechanics. They are exponentially tilted
          (the minimum-KL change) so that E[X] matches the situational
          points-per-possession.
  * Cold-start parameter uncertainty on each team's efficiency is integrated
    out with mean-preserving lognormal Gauss-Hermite quadrature.
  Dispersion is therefore implied by the mechanics:
      Var = E[N] Var(X) + Var(N) E[X]^2 + efficiency uncertainty
  It is not imposed as a static Var/Mean = 1 Poisson assumption.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import roots_hermitenorm
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pre-loaded into execution scope per sandbox contract
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


class NCAAWQuarter3AwayTotalModel(BaseCandidateModel):
    # Empirical segment anchors (Q3 points).
    ANCHOR_HOME = 20.00
    ANCHOR_AWAY = 18.75

    # Domain priors (documented, not fitted).
    POSS_PER_QUARTER_PRIOR = 17.5    # ~70 possessions per NCAAW game / 4
    Q3_SHARE_OF_GAME = 0.25          # used only if full-game inputs are passed
    POSS_CV = 0.07                   # quarter-level pace variability
    EFF_SD = 0.08                    # cold-start log-efficiency uncertainty
    EFF_NODES = 7                    # Gauss-Hermite nodes
    REST_BETA = 0.012                # log-rest efficiency elasticity
    # Partial pass-through of ratings when *_expected is supplied.
    # This avoids double counting.
    RATING_PASS_THROUGH_WITH_EXPECTED = 0.5

    OUTCOME_POINTS = np.array([0.0, 1.0, 2.0, 3.0])
    # Approximate shares: empty possession, 1-pt (FT), 2-pt, 3-pt. Mean ~0.97.
    BASE_SHARES = np.array([0.53, 0.06, 0.32, 0.09])

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_3_1108_101910",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_3",
        family: str = "away_total",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family
        z, w = roots_hermitenorm(self.EFF_NODES)
        self._eff_mult = np.exp(self.EFF_SD * z - 0.5 * self.EFF_SD ** 2)
        self._eff_w = w / w.sum()
        self._last_away_pmf: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ inputs
    def _quarter_points(self, raw: Optional[float], anchor: float) -> Optional[float]:
        if raw is None or raw <= 0:
            return None
        # A value far above any plausible quarter total is a full-game figure.
        return raw * self.Q3_SHARE_OF_GAME if raw > 3.0 * anchor else raw

    def _possessions(self, ctx: Dict[str, Any]) -> float:
        cands: List[float] = []
        for key in ("pace", "tempo", "expected_possessions"):
            v = _num(ctx.get(key))
            if v is not None and v > 0:
                cands.append(v)
        for side in ("home", "away"):
            d = ctx.get(side)
            if isinstance(d, dict):
                for key in ("pace", "tempo"):
                    v = _num(d.get(key))
                    if v is not None and v > 0:
                        cands.append(v)
        if not cands:
            return self.POSS_PER_QUARTER_PRIOR
        p = float(np.mean(cands))
        return p / 4.0 if p > 40.0 else p

    @staticmethod
    def _rating(ctx: Dict[str, Any], side: str, key: str) -> float:
        d = ctx.get(side)
        v = _num(d.get(key)) if isinstance(d, dict) else None
        if v is None:
            v = _num(ctx.get(f"{side}_{key}"))
        # Ratings are interpreted as multiplicative indices centred on 1.
        return v if (v is not None and 0.0 < v < 5.0) else 1.0

    def _team_means(self, ctx: Dict[str, Any], poss: float) -> Tuple[float, float]:
        pace_ratio = poss / self.POSS_PER_QUARTER_PRIOR
        out = []
        for side, opp, anchor in (
            ("home", "away", self.ANCHOR_HOME),
            ("away", "home", self.ANCHOR_AWAY),
        ):
            given = self._quarter_points(
                _num(ctx.get(f"{side}_q3_expected"), _num(ctx.get(f"{side}_expected"))),
                anchor,
            )
            lam = self.RATING_PASS_THROUGH_WITH_EXPECTED if given is not None else 1.0
            base = given if given is not None else anchor * pace_ratio
            rating = self._rating(ctx, side, "attack") * self._rating(ctx, opp, "defense")
            r_own = max(_num(ctx.get(f"{side}_rest_days"), 0.0) or 0.0, 0.0)
            r_opp = max(_num(ctx.get(f"{opp}_rest_days"), 0.0) or 0.0, 0.0)
            rest = math.exp(self.REST_BETA * (math.log1p(r_own) - math.log1p(r_opp)))
            out.append(base * (rating ** lam) * rest)
        return out[0], out[1]

    # --------------------------------------------------------------- mechanics
    def _tilted_pmf(self, ppp: float) -> np.ndarray:
        x, b = self.OUTCOME_POINTS, self.BASE_SHARES
        # Feasibility bound of the support [0, 3]; this is not a modelling clamp.
        eps = 1e-3
        target = min(max(ppp, eps), x[-1] - eps)

        def gap(t: float) -> float:
            w = b * np.exp(t * (x - x.mean()))
            return float((w * x).sum() / w.sum() - target)

        t = brentq(gap, -60.0, 60.0)
        w = b * np.exp(t * (x - x.mean()))
        return w / w.sum()

    def _poss_dist(self, mu: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = max(self.POSS_CV * mu, 0.5)
        lo, hi = max(1, int(math.floor(mu - 5 * sd))), int(math.ceil(mu + 5 * sd))
        n = np.arange(lo, hi + 1)
        p = norm.cdf(n + 0.5, mu, sd) - norm.cdf(n - 0.5, mu, sd)
        return n, p / p.sum()

    def _team_conditional(
        self, mean_pts: float, poss_mu: float, n_vals: np.ndarray, length: int
    ) -> Dict[int, np.ndarray]:
        n_max = int(n_vals.max())
        mix = {int(n): np.zeros(length) for n in n_vals}
        for m, wk in zip(self._eff_mult, self._eff_w):
            px = self._tilted_pmf(mean_pts * m / poss_mu)
            cur = np.array([1.0])
            for n in range(1, n_max + 1):
                cur = np.convolve(cur, px)
                if n in mix:
                    mix[n][: cur.size] += wk * cur
        return mix

    # -------------------------------------------------------------- interface
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        poss_mu = self._possessions(ctx)
        mu_h, mu_a = self._team_means(ctx, poss_mu)
        n_vals, n_p = self._poss_dist(poss_mu)
        length = 3 * int(n_vals.max()) + 1

        H = self._team_conditional(mu_h, poss_mu, n_vals, length)
        A = self._team_conditional(mu_a, poss_mu, n_vals, length)

        joint = np.zeros((length, length))
        for n, pn in zip(n_vals, n_p):
            joint += pn * np.outer(H[int(n)], A[int(n)])
        joint /= joint.sum()
        self._last_away_pmf = joint.sum(axis=0)

        scores = {
            (int(h), int(a)): float(joint[h, a])
            for h, a in zip(*np.nonzero(joint > 1e-10))
        }
        tot = sum(scores.values())
        scores = {k: v / tot for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, AttributeError, ValueError):
            return DiscreteDistribution.from_scores(
                [(h, a, p) for (h, a), p in scores.items()]
            )

    # ---------------------------------------------------------------- helpers
    def away_total_probabilities(
        self, game_context: Dict[str, Any], line: float
    ) -> Dict[str, float]:
        """P(over / under / push) for the away Q3 total at a given line."""
        self.predict_distribution(game_context)
        pmf = self._last_away_pmf
        k = np.arange(pmf.size)
        over = float(pmf[k > line].sum())
        push = float(pmf[k == line].sum()) if float(line).is_integer() else 0.0
        return {"over": over, "under": max(0.0, 1.0 - over - push), "push": push}

    @staticmethod
    def expected_value(p_win: float, decimal_odds: float, p_push: float = 0.0) -> float:
        """EV per unit stake. Eligible if decimal_odds >= 1.40 and EV > 0."""
        return p_win * (decimal_odds - 1.0) - (1.0 - p_win - p_push)