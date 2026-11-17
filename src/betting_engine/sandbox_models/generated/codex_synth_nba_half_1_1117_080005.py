"""
Candidate model for NBA | half_1 | home_total.

Structure:
  Home first-half points H are possessions N times points per possession X.
      E[H]   = E[N] * E[X]
      Var[H] = E[N] * Var[X] + Var[N] * E[X]^2 + Var(mu_hat)
  - The pace term Var[N] is shared by both teams, so it induces positive
    home/away correlation. It is integrated out with Gauss-Hermite quadrature.
  - Given pace, each team's score is a discretised Gamma with matched mean and
    variance. This keeps the right skew and avoids the
    variance = mean constraint of a Poisson.
  - The reported dispersion index (0.187) is a statement about the spread of
    the input expectations. It is not a statement about outcome variance:
    real NBA first-half team scoring has Var/Mean near 1. So it enters only
    as forecast-mean uncertainty, Var(mu_hat) = DI * mu.
  - The PnL is skewed toward unders, which points to an upward bias in the
    input expectations. Mean reliability (beta) and bias (delta) are
    estimated by maximum likelihood in `fit` on settled games. No bookmaker
    line enters the forecast.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import stats, optimize

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAHalf1HomeTotalModel(BaseCandidateModel):
    # ----------------------------------------------------------------- init
    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_080005",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        baseline_home: float = 56.0,
        baseline_away: float = 54.0,
        observed_dispersion_index: float = 0.187,
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

        # Empirical anchors supplied by the segment autopsy.
        self.baseline_home = float(baseline_home)
        self.baseline_away = float(baseline_away)
        self.mean_uncertainty_di = float(observed_dispersion_index)

        # Structural priors derived from possession mechanics. All of them
        # can be re-estimated with `fit`.
        #
        # Per-possession outcome mix (0, 1, 2, 3 points) gives
        # Var[X] / E[X] around 1.2 for modern NBA shot profiles.
        outcome_pts = np.array([0.0, 1.0, 2.0, 3.0])
        outcome_p = np.array([0.48, 0.04, 0.33, 0.15])
        outcome_p = outcome_p / outcome_p.sum()
        ex = float(outcome_pts @ outcome_p)
        vx = float((outcome_pts ** 2) @ outcome_p - ex ** 2)
        self.ppp_mean = ex
        self.ppp_fano = vx / ex

        # Half-game pace coefficient of variation. It comes from the
        # baseline possessions implied by the anchors and a game-to-game
        # possession sd of roughly sqrt of the possession count.
        implied_poss = 0.5 * (self.baseline_home + self.baseline_away) / ex
        self.pace_cv = math.sqrt(implied_poss) / implied_poss

        # Shrinkage of input mean toward baseline. Prior reliability is
        # signal var / (signal var + noise var), using DI as the noise ratio.
        self.beta = 1.0 / (1.0 + self.mean_uncertainty_di)
        self.delta = 0.0           # additive bias, set by fit()
        self.rest_coef = 0.0       # points per rest-day differential, set by fit()

        # Score lattice wide enough to hold the full tail mass.
        top = self.baseline_home + self.baseline_away
        self.max_pts = int(math.ceil(top * 1.25))
        self._grid = np.arange(self.max_pts + 1, dtype=float)
        self._edges = np.concatenate(([0.0], self._grid[1:] - 0.5, [np.inf]))

        gh_x, gh_w = np.polynomial.hermite_e.hermegauss(9)
        self._gh_z = gh_x
        self._gh_w = gh_w / gh_w.sum()

    # ------------------------------------------------------------- features
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            cur: Any = ctx
            ok = True
            for part in k.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if ok and cur is not None:
                try:
                    v = float(cur)
                    if math.isfinite(v):
                        return v
                except (TypeError, ValueError):
                    pass
        return None

    def _situational_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        """Return (mu_home, mu_away, pace_multiplier)."""
        h_in = self._num(ctx, "home_expected") or self.baseline_home
        a_in = self._num(ctx, "away_expected") or self.baseline_away

        # Attack/defence exposure, applied as multiplicative efficiency
        # ratios relative to the league average (ratio 1.0 when absent).
        h_att = self._num(ctx, "home.attack", "home_attack")
        h_def = self._num(ctx, "home.defense", "home_defense")
        a_att = self._num(ctx, "away.attack", "away_attack")
        a_def = self._num(ctx, "away.defense", "away_defense")
        h_eff = 1.0
        a_eff = 1.0
        if h_att is not None and a_def is not None and h_att > 0 and a_def > 0:
            h_eff = math.sqrt(h_att * a_def) / math.sqrt(
                max(h_att, 1e-9) * max(a_def, 1e-9)
            ) if False else (h_att * a_def) ** 0.5 / ((h_att + a_def) / 2.0)
        if a_att is not None and h_def is not None and a_att > 0 and h_def > 0:
            a_eff = (a_att * h_def) ** 0.5 / ((a_att + h_def) / 2.0)

        # Pace: the ratio of the game pace to league pace scales both means.
        pace = self._num(ctx, "pace", "expected_pace")
        lg_pace = self._num(ctx, "league_pace")
        pace_mult = (pace / lg_pace) if (pace and lg_pace) else 1.0

        # Rest differential, using fatigue mechanics. The effect is
        # capped by its own estimated slope, not by a static clamp.
        hr = self._num(ctx, "home_rest_days")
        ar = self._num(ctx, "away_rest_days")
        rest_diff = 0.0
        if hr is not None and ar is not None:
            rest_diff = math.tanh(hr - ar)

        def shrink(x: float, base: float) -> float:
            return base + self.beta * (x - base)

        mu_h = shrink(h_in * h_eff * pace_mult, self.baseline_home) + self.delta \
            + self.rest_coef * rest_diff
        mu_a = shrink(a_in * a_eff * pace_mult, self.baseline_away) \
            - self.rest_coef * rest_diff
        floor = 1.0 / self.ppp_mean
        return max(mu_h, floor), max(mu_a, floor), pace_mult

    # ---------------------------------------------------------- primitives
    def _gamma_pmf_rows(self, means: np.ndarray, variances: np.ndarray) -> np.ndarray:
        shape = means ** 2 / variances
        scale = variances / means
        cdf = stats.gamma.cdf(self._edges[None, :], a=shape[:, None], scale=scale[:, None])
        pmf = np.clip(np.diff(cdf, axis=1), 0.0, None)
        s = pmf.sum(axis=1, keepdims=True)
        s[s <= 0] = 1.0
        return pmf / s

    def _marginal_home_pmf(self, mu_h: float) -> np.ndarray:
        z, w = self._gh_z, self._gh_w
        m = np.maximum(1.0 + self.pace_cv * z, 1e-3)
        mh = mu_h * m
        vh = mh * self.ppp_fano + self.mean_uncertainty_di * mu_h
        return w @ self._gamma_pmf_rows(mh, vh)

    def _joint_matrix(self, mu_h: float, mu_a: float) -> np.ndarray:
        z, w = self._gh_z, self._gh_w
        m = np.maximum(1.0 + self.pace_cv * z, 1e-3)   # shared possessions
        mh, ma = mu_h * m, mu_a * m
        vh = mh * self.ppp_fano + self.mean_uncertainty_di * mu_h
        va = ma * self.ppp_fano + self.mean_uncertainty_di * mu_a
        ph = self._gamma_pmf_rows(mh, vh)
        pa = self._gamma_pmf_rows(ma, va)
        joint = np.einsum("k,ki,kj->ij", w, ph, pa)
        joint = np.clip(joint, 0.0, None)
        return joint / joint.sum()

    # ----------------------------------------------------------- interface
    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        try:
            mu_h, mu_a, _ = self._situational_means(game_context or {})
            joint = self._joint_matrix(mu_h, mu_a)
            if not np.all(np.isfinite(joint)) or joint.sum() <= 0:
                raise ValueError("degenerate joint")
        except Exception:
            joint = self._joint_matrix(self.baseline_home, self.baseline_away)

        idx = np.argwhere(joint > 1e-12)
        scores = {(int(i), int(j)): float(joint[i, j]) for i, j in idx}
        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items()}
        return DiscreteDistribution.from_scores(scores)

    def prob_home_over(self, game_context: Dict[str, Any], line: float) -> float:
        """Helper: P(home first-half points > line), from the marginal."""
        mu_h, _, _ = self._situational_means(game_context or {})
        pmf = self._marginal_home_pmf(mu_h)
        return float(pmf[self._grid > line].sum())

    # ---------------------------------------------------------- calibration
    def fit(self, games: Iterable[Tuple[Dict[str, Any], float]]) -> Dict[str, float]:
        """
        Maximum-likelihood calibration on settled games.
        `games` is an iterable of (game_context, realised_home_h1_points).
        Estimates beta (reliability), delta (bias), rest_coef, ppp_fano and pace_cv.
        """
        data: List[Tuple[Dict[str, Any], int]] = [
            (c, int(round(y))) for c, y in games
            if y is not None and math.isfinite(float(y))
        ]
        if len(data) < 30:
            return self.params()

        hs = [float(c.get("home_h1_points", y)) for c, y in data]
        emp_mean = float(np.mean(hs))
        emp_var = float(np.var(hs, ddof=1))

        # Method-of-moments starting point for overall variance:
        #   emp_var ~ mu*fano + (pace_cv*mu)^2 + DI*mu
        resid = emp_var - self.mean_uncertainty_di * emp_mean - (self.pace_cv * emp_mean) ** 2
        fano0 = max(resid / emp_mean, 1e-2) if resid > 0 else self.ppp_fano

        x0 = np.array([
            math.log(self.beta / (1 - self.beta + 1e-9)),
            emp_mean - self.baseline_home,
            0.0,
            math.log(fano0),
            math.log(self.pace_cv),
        ])
        ys = np.array([min(y, self.max_pts) for _, y in data])
        ctxs = [c for c, _ in data]

        def unpack(theta: np.ndarray) -> None:
            self.beta = 1.0 / (1.0 + math.exp(-theta[0]))
            self.delta = float(theta[1])
            self.rest_coef = float(theta[2])
            self.ppp_fano = math.exp(theta[3])
            self.pace_cv = math.exp(theta[4])

        def nll(theta: np.ndarray) -> float:
            unpack(theta)
            ll = 0.0
            for c, y in zip(ctxs, ys):
                mu_h, _, _ = self._situational_means(c)
                p = self._marginal_home_pmf(mu_h)[y]
                ll += math.log(max(p, 1e-300))
            return -ll / len(ys)

        saved = self.params()
        try:
            res = optimize.minimize(
                nll, x0, method="Nelder-Mead",
                options={"maxiter": 400, "xatol": 1e-3, "fatol": 1e-6},
            )
            unpack(res.x if res.success or np.isfinite(res.fun) else x0)
        except Exception:
            self.beta, self.delta = saved["beta"], saved["delta"]
            self.rest_coef, self.ppp_fano = saved["rest_coef"], saved["ppp_fano"]
            self.pace_cv = saved["pace_cv"]
        return self.params()

    def params(self) -> Dict[str, float]:
        return {
            "beta": self.beta, "delta": self.delta, "rest_coef": self.rest_coef,
            "ppp_fano": self.ppp_fano, "pace_cv": self.pace_cv,
            "mean_uncertainty_di": self.mean_uncertainty_di,
        }


CandidateModel = NBAHalf1HomeTotalModel