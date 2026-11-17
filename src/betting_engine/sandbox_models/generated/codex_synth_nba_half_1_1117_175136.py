"""
Candidate model for the NBA | half_1 | home_total segment.

Model summary
-------------
The home first-half score is treated as a compound sum
    T = sum_{i=1..N} X_i
  * N is the home possession count in the half. Its expectation comes from
    both teams' tempo; its realized deviation from expectation is shared with
    the away side.
  * X_i is the points scored on possession i, with outcomes in {0, 1, 2, 3}.
    Its mean is set by the home attack and away defense efficiency.

Moments follow from the structure:
    E[T]   = E[N] * mu_x
    Var(T) = E[N] * sigma_x^2 + Var(N) * mu_x^2 + Var(mean-forecast error)

On the reported dispersion index of 0.187
-----------------------------------------
A Var/Mean of 0.187 around a mean of about 56 implies an SD of about 3.2
points. Realized NBA first-half team scores have an SD of roughly 7-8
points, and a possession-level variance bound alone already gives a
Var/Mean above 1. Pricing realized outcomes with an SD of about 3 would put
too much mass near the mean and too little in the tails. That would produce
large overconfident edges, which matches the failure pattern seen in the
listed Crucible defects.

The 0.187 figure is therefore read as the dispersion of the game-level
expected score (forecast-mean uncertainty). It enters the model as the
between-game variance term `0.187 * mean`. It is not used as the total
outcome dispersion.

Construction
------------
* Shared pace randomness: a Gauss-Hermite mixture over the realized
  possession count. This also induces a positive home/away score correlation.
* Conditional marginals: negative binomial when the conditional
  Var/Mean > 1, and a discretized Gaussian otherwise, so either dispersion
  regime is handled.
* Output: a joint (home, away) score lattice, normalized to sum to 1.
* All priors are named class attributes. Each has its domain rationale
  documented below.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in the execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    pass


class NBAHalf1HomeTotalCompoundModel(BaseCandidateModel):
    # ---------------- empirical segment anchors ----------------
    HOME_ANCHOR: float = 56.00          # empirical home 1H expectation
    AWAY_ANCHOR: float = 54.00          # empirical away 1H expectation
    EMPIRICAL_DISPERSION: float = 0.187  # Var/Mean of forecast means (see module doc)

    # ---------------- possession-process priors ----------------
    # League pace is ~99-101 possessions per 48 min, so ~50 per half.
    LEAGUE_PACE_48: float = 100.0
    HALF_SHARE: float = 0.5
    # SD of realized 1H possessions around their expectation
    # (fouls, transition variance, end-of-quarter heaves).
    POSSESSION_SD_HALF: float = 3.0
    # Distribution of points conditional on a scoring possession
    # (free-trip 1s, 2s and and-ones, 3s). Normalized internally.
    SCORE_SHARES: Tuple[float, float, float] = (0.11, 0.58, 0.31)

    # ---------------- situational priors ----------------
    # Rest: a saturating effect on offensive efficiency.
    # Back-to-back is about -1.5%, and the effect fades after ~2 days.
    REST_EFFECT_MAX: float = 0.015
    REST_SCALE_DAYS: float = 1.5
    # A provided expectation above this multiple of the anchor is treated
    # as a full-game value and scaled down to the half.
    FULL_GAME_DETECT_RATIO: float = 1.6

    # ---------------- numerics ----------------
    GH_NODES: int = 7
    TAIL_SDS: float = 9.0
    MIN_CELL_PROB: float = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_175136",
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
            super().__init__()
            self.model_id = model_id
            self.version = version
            self.league = league
            self.scope = scope
            self.family = family

        shares = np.asarray(self.SCORE_SHARES, dtype=float)
        shares = shares / shares.sum()
        pts = np.array([1.0, 2.0, 3.0])
        # Moments of X given that the possession scored
        self._ex_given_score = float(np.dot(shares, pts))
        self._ex2_given_score = float(np.dot(shares, pts ** 2))

        gh_x, gh_w = np.polynomial.hermite_e.hermegauss(self.GH_NODES)
        self._gh_x = gh_x
        self._gh_w = gh_w / gh_w.sum()

    # ------------------------------------------------------------------
    # Feature parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(out):
            return None
        return out

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._to_float(ctx.get(k))
                if v is not None:
                    return v
        return None

    def _team_field(self, ctx: Dict[str, Any], side: str, field: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._to_float(block.get(field))
            if v is not None:
                return v
        return self._get(ctx, f"{side}_{field}")

    @staticmethod
    def _rating_to_factor(value: Optional[float]) -> float:
        """Map a rating to a multiplicative factor centered at 1.

        Accepts relative factors (~1.0) or per-100 efficiency ratings
        (~110, divided by the league scale).
        """
        if value is None or value <= 0:
            return 1.0
        if value < 5.0:          # already a relative factor
            return value
        if value < 250.0:        # points per 100 possessions
            return value / 113.0  # league-average offensive rating
        return 1.0

    def _rest_factor(self, own_rest: Optional[float], opp_rest: Optional[float]) -> float:
        if own_rest is None or opp_rest is None:
            return 1.0
        diff = own_rest - opp_rest
        return 1.0 + self.REST_EFFECT_MAX * math.tanh(diff / self.REST_SCALE_DAYS)

    def _half_scale(self, value: float, anchor: float) -> float:
        if value > self.FULL_GAME_DETECT_RATIO * anchor:
            return value * self.HALF_SHARE
        return value

    # ------------------------------------------------------------------
    # Expected scores and possessions
    # ------------------------------------------------------------------
    def _expected_possessions(self, ctx: Dict[str, Any]) -> float:
        hp = self._team_field(ctx, "home", "pace")
        ap = self._team_field(ctx, "away", "pace")
        gp = self._get(ctx, "pace", "game_pace", "expected_pace")
        if gp is None:
            paces = [p for p in (hp, ap) if p is not None and p > 0]
            gp = float(np.mean(paces)) if paces else self.LEAGUE_PACE_48
        # Accept either a per-48 pace or an already-halved value
        if gp > self.FULL_GAME_DETECT_RATIO * self.LEAGUE_PACE_48 * self.HALF_SHARE:
            gp = gp * self.HALF_SHARE
        return max(gp, 1.0)

    def _expected_scores(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h_rest = self._get(ctx, "home_rest_days")
        a_rest = self._get(ctx, "away_rest_days")

        home_exp = self._get(ctx, "home_expected")
        away_exp = self._get(ctx, "away_expected")

        if home_exp is not None and home_exp > 0:
            home_exp = self._half_scale(home_exp, self.HOME_ANCHOR)
        else:
            atk = self._rating_to_factor(self._team_field(ctx, "home", "attack"))
            dfn = self._rating_to_factor(self._team_field(ctx, "away", "defense"))
            home_exp = self.HOME_ANCHOR * atk * dfn * self._rest_factor(h_rest, a_rest)

        if away_exp is not None and away_exp > 0:
            away_exp = self._half_scale(away_exp, self.AWAY_ANCHOR)
        else:
            atk = self._rating_to_factor(self._team_field(ctx, "away", "attack"))
            dfn = self._rating_to_factor(self._team_field(ctx, "home", "defense"))
            away_exp = self.AWAY_ANCHOR * atk * dfn * self._rest_factor(a_rest, h_rest)

        return float(home_exp), float(away_exp)

    # ------------------------------------------------------------------
    # Possession-level variance
    # ------------------------------------------------------------------
    def _ppp_variance(self, mu_x: float) -> float:
        """Var(X) for points per possession.

        The scoring-outcome mix is held fixed, and the scoring probability
        is solved so that E[X] = mu_x.
        """
        p_score = min(mu_x / self._ex_given_score, 1.0)
        ex2 = p_score * self._ex2_given_score
        return max(ex2 - mu_x ** 2, 1e-6)

    # ------------------------------------------------------------------
    # Conditional pmf (NB if overdispersed, discretized normal otherwise)
    # ------------------------------------------------------------------
    @staticmethod
    def _pmf(mean: float, var: float, support: np.ndarray) -> np.ndarray:
        mean = max(mean, 1e-6)
        var = max(var, 1e-6)
        if var > mean * (1.0 + 1e-9):
            r = mean * mean / (var - mean)
            p = r / (r + mean)
            pmf = stats.nbinom.pmf(support, r, p)
        else:
            sd = math.sqrt(var)
            upper = stats.norm.cdf((support + 0.5 - mean) / sd)
            lower = stats.norm.cdf((support - 0.5 - mean) / sd)
            pmf = upper - lower
            pmf[0] = upper[0]  # fold negative mass into zero
        pmf = np.clip(pmf, 0.0, None)
        s = pmf.sum()
        return pmf / s if s > 0 else np.full_like(pmf, 1.0 / len(pmf))

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def _joint_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        home_mu, away_mu = self._expected_scores(ctx)
        n_bar = self._expected_possessions(ctx)
        pace_cv = self.POSSESSION_SD_HALF / n_bar

        mu_xh = home_mu / n_bar
        mu_xa = away_mu / n_bar
        var_xh = self._ppp_variance(mu_xh)
        var_xa = self._ppp_variance(mu_xa)

        # Between-game forecast-mean uncertainty (empirical 0.187 index)
        unc_h = self.EMPIRICAL_DISPERSION * home_mu
        unc_a = self.EMPIRICAL_DISPERSION * away_mu

        # Bound the support so the tails are fully covered
        tot_var_h = n_bar * var_xh + (self.POSSESSION_SD_HALF * mu_xh) ** 2 + unc_h
        tot_var_a = n_bar * var_xa + (self.POSSESSION_SD_HALF * mu_xa) ** 2 + unc_a
        kmax_h = int(math.ceil(home_mu + self.TAIL_SDS * math.sqrt(tot_var_h)))
        kmax_a = int(math.ceil(away_mu + self.TAIL_SDS * math.sqrt(tot_var_a)))
        sup_h = np.arange(0, kmax_h + 1)
        sup_a = np.arange(0, kmax_a + 1)

        joint = np.zeros((len(sup_h), len(sup_a)))
        for z, w in zip(self._gh_x, self._gh_w):
            # Shared pace shock; exp keeps the possession count positive
            n_z = n_bar * math.exp(pace_cv * z - 0.5 * pace_cv ** 2)
            mh, ma = n_z * mu_xh, n_z * mu_xa
            vh = n_z * var_xh + unc_h
            va = n_z * var_xa + unc_a
            joint += w * np.outer(self._pmf(mh, vh, sup_h), self._pmf(ma, va, sup_a))

        joint = np.clip(joint, 0.0, None)
        joint[joint < self.MIN_CELL_PROB] = 0.0
        total = joint.sum()
        if not np.isfinite(total) or total <= 0:
            # Degenerate fallback: point mass at the rounded anchors
            joint = np.zeros((len(sup_h), len(sup_a)))
            joint[min(int(round(home_mu)), kmax_h), min(int(round(away_mu)), kmax_a)] = 1.0
            total = 1.0
        return joint / total

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, dict) else {}
        joint = self._joint_matrix(ctx)
        hi, ai = np.nonzero(joint)
        probs = joint[hi, ai]
        probs = probs / probs.sum()
        scores = {(int(h), int(a)): float(p) for h, a, p in zip(hi, ai, probs)}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(joint)

    # ------------------------------------------------------------------
    # Convenience diagnostics
    # ------------------------------------------------------------------
    def home_total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        return self._joint_matrix(game_context).sum(axis=1)

    def prob_over(self, game_context: Dict[str, Any], line: float) -> float:
        pmf = self.home_total_pmf(game_context)
        k = np.arange(len(pmf))
        return float(pmf[k > line].sum())

    def prob_under(self, game_context: Dict[str, Any], line: float) -> float:
        pmf = self.home_total_pmf(game_context)
        k = np.arange(len(pmf))
        return float(pmf[k < line].sum())