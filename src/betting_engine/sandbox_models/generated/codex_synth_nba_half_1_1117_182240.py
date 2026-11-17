"""
Candidate model for NBA | half_1 | home_total.

Modeling notes (read before deploying):

1. Structure. First-half home points are modeled as a possession-compound sum
       S = sum_{i=1..N} X_i
   N is the number of home possessions in the half. It is driven by game pace,
   is shared with the away side, and fatigue lowers it. X_i is points per
   possession. The exact compound-variance identity is
       Var(S) = E[N] * Var(X) + Var(N) * mu_ppp^2
   Var(X) comes from the scoring mix. A point scored as part of a k-point event
   contributes k to E[X^2] / E[X], so
       Var(X) / E[X] = sum_k k * share_k - mu_ppp.
   The shared Var(N) term gives positive home/away correlation. The model
   therefore emits a joint (home, away) score lattice, so the pricing engine
   can read the home marginal or any derived market.

2. The reported dispersion index (0.187) is NOT used as the score variance by
   default. A Var/Mean of 0.187 at mean 56 implies SD of about 3.2 points. The
   possession mechanics above imply Var/Mean of about 1.0 to 1.3 for NBA
   first-half team points, which is SD of about 7.5 to 8.5. A model that
   prices with SD 3.2 would be severely overconfident. That pattern matches the
   negative delta-Brier and deep drawdowns in the prior Crucible failures. The
   0.187 figure most likely measures the spread of the wager-level expectation
   inputs, not realized scores. If it really is a realized-score statistic, set
   `use_reported_dispersion=True`. Better, call `fit()` on historical games and
   the dispersion is estimated from realized residuals.

3. No line or vig information enters the forecast. All structural constants
   are explicit attributes that `fit()` re-estimates. They are not clamps.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAHalf1HomeTotalPossessionModel(BaseCandidateModel):
    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_182240",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        use_reported_dispersion: bool = False,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        # Empirical anchors for this segment
        self.home_anchor: float = 56.00
        self.away_anchor: float = 54.00
        self.reported_dispersion_index: float = 0.187
        self.use_reported_dispersion: bool = use_reported_dispersion

        # Possession mechanics (structural priors, re-estimated by fit()).
        # Points per possession, league level.
        self.league_ppp: float = 1.15
        # Share of points from 3-pt, 2-pt, and free-throw events.
        self.point_share: Dict[int, float] = {3: 0.36, 2: 0.46, 1: 0.18}
        # Pace dispersion: Var(N) / E[N] for half possessions.
        self.pace_var_ratio: float = 0.08

        # Situational sensitivities (elasticities on the log scale)
        # Rest/fatigue effect on pace and on efficiency, per log1p(rest) unit,
        # relative to a reference rest level.
        self.rest_reference_days: float = 2.0
        self.rest_pace_elasticity: float = 0.010
        self.rest_eff_elasticity: float = 0.015
        # Weight given to attack/defense indices beyond what home_expected
        # already contains. A value of 0 means trust home_expected fully.
        self.matchup_weight: float = 0.25
        # Pace index elasticity on possessions.
        self.pace_elasticity: float = 1.0
        # Residual variance multiplier, learned in fit(). 1.0 = pure structure.
        self.variance_scale: float = 1.0

        # Lattice resolution: number of standard deviations covered.
        self.lattice_sd_span: float = 8.0

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _side_index(self, side: Any, key: str) -> float:
        """Multiplicative attack/defense index centred at 1.0."""
        if isinstance(side, dict):
            v = self._num(side.get(key))
            if v is not None and v > 0:
                return v
        return 1.0

    def _rest_term(self, rest: Any) -> float:
        r = self._num(rest)
        if r is None or r < 0:
            return 0.0
        return math.log1p(r) - math.log1p(self.rest_reference_days)

    def _pace_index(self, ctx: Dict[str, Any]) -> float:
        """Relative pace index; 1.0 = league average."""
        for key in ("pace_index", "tempo_index"):
            v = self._num(ctx.get(key))
            if v is not None and v > 0:
                return v
        hp = self._num(ctx.get("home_pace"))
        ap = self._num(ctx.get("away_pace"))
        lp = self._num(ctx.get("league_pace"))
        if hp and ap and lp and hp > 0 and ap > 0 and lp > 0:
            return math.sqrt(hp * ap) / lp
        return 1.0

    # ------------------------------------------------------------------ #
    # Moment construction
    # ------------------------------------------------------------------ #
    def _per_possession_var_ratio(self, ppp: float) -> float:
        """Var(X) / E[X] from the scoring mix and the identity in the notes."""
        tot = sum(self.point_share.values())
        second = sum(k * s for k, s in self.point_share.items()) / tot
        return max(second - ppp, 1e-6)

    def _moments(self, ctx: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
        h_exp = self._num(ctx.get("home_expected")) or self.home_anchor
        a_exp = self._num(ctx.get("away_expected")) or self.away_anchor

        home = ctx.get("home", {})
        away = ctx.get("away", {})

        # Matchup adjustment beyond the supplied expectation
        h_match = (self._side_index(home, "attack") / self._side_index(away, "defense"))
        a_match = (self._side_index(away, "attack") / self._side_index(home, "defense"))
        h_eff = self.matchup_weight * math.log(h_match)
        a_eff = self.matchup_weight * math.log(a_match)

        # Rest/fatigue: shared pace effect plus team-specific efficiency effect
        hr = self._rest_term(ctx.get("home_rest_days"))
        ar = self._rest_term(ctx.get("away_rest_days"))
        pace_log = (self.pace_elasticity * math.log(self._pace_index(ctx))
                    + self.rest_pace_elasticity * 0.5 * (hr + ar))
        h_eff += self.rest_eff_elasticity * hr
        a_eff += self.rest_eff_elasticity * ar

        mu_h = h_exp * math.exp(pace_log + h_eff)
        mu_a = a_exp * math.exp(pace_log + a_eff)

        # Decompose each mean into possessions x efficiency
        n_poss = 0.5 * (mu_h + mu_a) / self.league_ppp
        ppp_h = mu_h / n_poss
        ppp_a = mu_a / n_poss
        var_n = self.pace_var_ratio * n_poss

        var_h = n_poss * self._per_possession_var_ratio(ppp_h) * ppp_h + var_n * ppp_h ** 2
        var_a = n_poss * self._per_possession_var_ratio(ppp_a) * ppp_a + var_n * ppp_a ** 2
        cov = var_n * ppp_h * ppp_a

        var_h *= self.variance_scale
        var_a *= self.variance_scale
        cov *= self.variance_scale

        if self.use_reported_dispersion:
            # Rescale so that home Var/Mean equals the reported index, and
            # keep the correlation unchanged.
            rho = cov / math.sqrt(var_h * var_a)
            var_h = self.reported_dispersion_index * mu_h
            var_a = self.reported_dispersion_index * mu_a
            cov = rho * math.sqrt(var_h * var_a)

        return mu_h, mu_a, var_h, var_a, cov

    # ------------------------------------------------------------------ #
    # Distribution
    # ------------------------------------------------------------------ #
    def _joint_lattice(self, mu_h: float, mu_a: float, var_h: float,
                       var_a: float, cov: float) -> Dict[Tuple[int, int], float]:
        sd_h, sd_a = math.sqrt(var_h), math.sqrt(var_a)
        rho = cov / (sd_h * sd_a)

        def support(mu: float, sd: float) -> np.ndarray:
            lo = max(0, int(math.floor(mu - self.lattice_sd_span * sd)))
            hi = int(math.ceil(mu + self.lattice_sd_span * sd))
            return np.arange(lo, hi + 1)

        hs, as_ = support(mu_h, sd_h), support(mu_a, sd_a)

        # Home marginal: continuity-corrected Normal; lower edge absorbs <= 0
        upper_h = stats.norm.cdf((hs + 0.5 - mu_h) / sd_h)
        lower_h = np.concatenate(([0.0], upper_h[:-1]))
        p_h = upper_h - lower_h
        p_h[-1] += 1.0 - upper_h[-1]

        # Away conditional on home: Gaussian regression via the shared-pace covariance
        cond_sd = sd_a * math.sqrt(max(1.0 - rho ** 2, 1e-9))
        cond_mu = mu_a + rho * sd_a / sd_h * (hs - mu_h)
        z = (as_[None, :] + 0.5 - cond_mu[:, None]) / cond_sd
        upper_a = stats.norm.cdf(z)
        lower_a = np.concatenate((np.zeros((len(hs), 1)), upper_a[:, :-1]), axis=1)
        p_a_given_h = upper_a - lower_a
        p_a_given_h[:, -1] += 1.0 - upper_a[:, -1]

        joint = p_h[:, None] * p_a_given_h
        joint = np.clip(joint, 0.0, None)
        joint /= joint.sum()

        scores: Dict[Tuple[int, int], float] = {}
        for i, h in enumerate(hs):
            for j, a in enumerate(as_):
                p = float(joint[i, j])
                if p > 0.0:
                    scores[(int(h), int(a))] = p
        tot = sum(scores.values())
        return {k: v / tot for k, v in scores.items()}

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        ctx = game_context or {}
        mu_h, mu_a, var_h, var_a, cov = self._moments(ctx)
        scores = self._joint_lattice(mu_h, mu_a, var_h, var_a, cov)
        return DiscreteDistribution.from_scores(scores)

    # ------------------------------------------------------------------ #
    # Calibration on historical games
    # ------------------------------------------------------------------ #
    def fit(self, games: List[Dict[str, Any]]) -> "NBAHalf1HomeTotalPossessionModel":
        """
        Calibrate on historical games. Each game is a game_context dict plus
        realized 'home_score' and 'away_score' (first-half points).

        Steps:
          1. Re-anchor the home/away means from realized first-half scores.
          2. Grid-search the rest and matchup elasticities by out-of-fold
             squared error (deterministic 5-fold split, no shuffle).
          3. Estimate variance_scale and pace_var_ratio from the realized
             residual variance and covariance, using the compound identity.
        """
        rows = [g for g in games
                if self._num(g.get("home_score")) is not None
                and self._num(g.get("away_score")) is not None]
        if len(rows) < 30:
            return self

        yh = np.array([float(g["home_score"]) for g in rows])
        ya = np.array([float(g["away_score"]) for g in rows])
        self.home_anchor, self.away_anchor = float(yh.mean()), float(ya.mean())

        # Step 2: elasticities by out-of-fold error
        k = 5
        folds = np.arange(len(rows)) % k
        grid_rest = np.linspace(0.0, 0.04, 5)
        grid_match = np.linspace(0.0, 1.0, 5)
        best, best_err = (self.rest_eff_elasticity, self.matchup_weight), float("inf")
        for re_ in grid_rest:
            for mw in grid_match:
                self.rest_eff_elasticity, self.matchup_weight = float(re_), float(mw)
                err = 0.0
                for f in range(k):
                    idx = np.where(folds == f)[0]
                    pred = np.array([self._moments(rows[i])[0] for i in idx])
                    err += float(((yh[idx] - pred) ** 2).sum())
                if err < best_err:
                    best, best_err = (float(re_), float(mw)), err
        self.rest_eff_elasticity, self.matchup_weight = best

        # Step 3: variance calibration from residuals
        self.variance_scale = 1.0
        mh, ma, vh, va, cv = zip(*(self._moments(g) for g in rows))
        rh, ra = yh - np.array(mh), ya - np.array(ma)
        model_var_h = float(np.mean(vh))
        resid_var_h = float(np.mean(rh ** 2))
        resid_cov = float(np.mean(rh * ra))

        # Pace variance from the covariance: Cov = Var(N) * ppp_h * ppp_a
        ppp_h = np.array(mh) / (0.5 * (np.array(mh) + np.array(ma)) / self.league_ppp)
        ppp_a = np.array(ma) / (0.5 * (np.array(mh) + np.array(ma)) / self.league_ppp)
        n_poss = 0.5 * (np.array(mh) + np.array(ma)) / self.league_ppp
        denom = float(np.mean(n_poss * ppp_h * ppp_a))
        if resid_cov > 0 and denom > 0:
            self.pace_var_ratio = resid_cov / denom

        # Overall variance scale so the predictive variance matches realized
        mh2, _, vh2, _, _ = zip(*(self._moments(g) for g in rows))
        model_var_h = float(np.mean(vh2))
        if model_var_h > 0 and resid_var_h > 0:
            self.variance_scale = resid_var_h / model_var_h

        # Store the realized dispersion for diagnostics
        self.reported_dispersion_index = resid_var_h / float(np.mean(yh))
        return self

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #
    def home_marginal(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        mu_h, mu_a, var_h, var_a, cov = self._moments(game_context or {})
        joint = self._joint_lattice(mu_h, mu_a, var_h, var_a, cov)
        out: Dict[int, float] = {}
        for (h, _), p in joint.items():
            out[h] = out.get(h, 0.0) + p
        return out

    def prob_over(self, game_context: Dict[str, Any], line: float) -> float:
        marg = self.home_marginal(game_context)
        return float(sum(p for h, p in marg.items() if h > line))