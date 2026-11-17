"""
Candidate model for NBA | half_1 | home_total.

How the model works: compound possession process
-------------------------------------------------
Home first-half points are S = sum_{i=1..N} X_i, where:
  N   = home possessions in 24 minutes, set by the joint pace of both teams
  X_i = points on one possession, taking values in {0, 1, 2, 3, 4}

Law of total variance:
  E[S]   = E[N] * E[X]
  Var[S] = E[N] * Var(X) + Var(N) * E[X]^2

A note on the 0.187 dispersion index
------------------------------------
Typical NBA possession outcome frequencies give Var(X) of about 1.3 and E[X]
of about 1.1. With about 50 half-possessions, the noise from X alone puts
Var[S]/E[S] near 1.2, even when Var(N) = 0.

An index of 0.187 is therefore below the lowest value the scoring mechanics
allow for realized half totals. It most likely measures the dispersion of the
*expected* totals, not of the outcomes. A distribution that tight would be
overconfident around the posted line.

So the model works like this:
  * Var(N) is solved from the target index when that solution is feasible.
  * When the target is infeasible, the model falls back to the mechanical
    floor, E[N]*Var(X), and records that it did so in `self.last_diagnostics`.
  * The upper tail is thinner than Poisson or negative binomial
    (index about 1.2 to 1.4), which matches the under-side PnL concentration.

Every structural parameter is a named domain prior. Each one can be refit
from history with `fit()` or overridden per call through `game_context`.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally preloaded; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NBAHalf1HomeTotalPossessionModel(BaseCandidateModel):
    """Compound possession model for NBA first-half team totals."""

    # ---- Domain priors. Each is refittable through fit() or overridable per call.
    # League anchors for first-half team points.
    ANCHOR_HOME = 56.00
    ANCHOR_AWAY = 54.00
    # Full-game possessions per team per 48 minutes (league pace).
    LEAGUE_PACE_48 = 99.5
    # Points-per-possession outcome mix: P(X = 0, 1, 2, 3, 4).
    # The 4-point value covers and-ones on threes.
    PPP_OUTCOME_PROBS = (0.470, 0.055, 0.335, 0.135, 0.005)
    # Possession count noise in a half. Pace is bounded by the shot clock, so
    # it is small; the standard deviation is used only when the target is infeasible.
    POSSESSION_SD_HALF = 2.6
    # Empirical Var/Mean index taken from the autopsy.
    TARGET_DISPERSION_INDEX = 0.187
    # Rest effects: multiplicative change in efficiency per day of rest
    # differential, saturating at REST_SATURATION_DAYS.
    REST_EFFECT_PER_DAY = 0.006
    REST_SATURATION_DAYS = 3.0
    # Probability-mass tail cutoff, in standard deviations, for the discretized lattice.
    LATTICE_SD_SPAN = 7.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_181102",
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
        self.ppp_probs = np.asarray(self.PPP_OUTCOME_PROBS, dtype=float)
        self.ppp_probs = self.ppp_probs / self.ppp_probs.sum()
        self.possession_sd = float(self.POSSESSION_SD_HALF)
        self.anchor_home = float(self.ANCHOR_HOME)
        self.anchor_away = float(self.ANCHOR_AWAY)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _side_feature(self, ctx: Dict[str, Any], side: str, names: Iterable[str]) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            for n in names:
                v = self._num(block.get(n))
                if v is not None:
                    return v
        for n in names:
            v = self._num(ctx.get(f"{side}_{n}"))
            if v is not None:
                return v
        return None

    @staticmethod
    def _ratio(v: Optional[float]) -> float:
        """Normalize a rating to a multiplier.

        Values near 1 are taken as ratios already. Values of 50 or more are read
        as ORtg/DRtg on a 100-point scale. Anything else maps to 1.
        """
        if v is None or v <= 0:
            return 1.0
        if v > 50.0:
            return v / 110.0 if v > 0 else 1.0
        if 0.3 < v < 3.0:
            return v
        return 1.0

    @property
    def _ex(self) -> float:
        return float(np.dot(np.arange(self.ppp_probs.size), self.ppp_probs))

    @property
    def _varx(self) -> float:
        k = np.arange(self.ppp_probs.size)
        m = self._ex
        return float(np.dot((k - m) ** 2, self.ppp_probs))

    # ------------------------------------------------------------- fitting
    def fit(self, history: Iterable[Dict[str, Any]]) -> "NBAHalf1HomeTotalPossessionModel":
        """Refit the anchors and possession noise from settled games.

        Each row needs 'home_points_h1' and 'away_points_h1'. Rows may also
        carry 'home_expected' and 'away_expected'.

        The anchors are moment-matched. Residual variance not explained by
        possession-outcome noise is assigned to Var(N), with a floor at 0.
        """
        rows = list(history or [])
        hs = np.array([self._num(r.get("home_points_h1"), np.nan) for r in rows], dtype=float)
        aws = np.array([self._num(r.get("away_points_h1"), np.nan) for r in rows], dtype=float)
        he = np.array([self._num(r.get("home_expected"), np.nan) for r in rows], dtype=float)
        ok = np.isfinite(hs)
        if ok.sum() < 30:
            return self
        self.anchor_home = float(np.mean(hs[ok]))
        if np.isfinite(aws).sum() >= 30:
            self.anchor_away = float(np.nanmean(aws))
        resid = hs[ok] - np.where(np.isfinite(he[ok]), he[ok], self.anchor_home)
        resid_var = float(np.var(resid, ddof=1))
        ex, varx = self._ex, self._varx
        n_hat = self.anchor_home / ex
        var_n = max(0.0, (resid_var - n_hat * varx) / (ex * ex))
        self.possession_sd = math.sqrt(var_n)
        return self

    # ------------------------------------------------------------- core
    def _side_moments(
        self, ctx: Dict[str, Any], side: str, other: str, anchor: float, n_joint: float, pace_mult: float
    ) -> Tuple[float, float]:
        expected = self._num(ctx.get(f"{side}_expected"))
        ex, varx = self._ex, self._varx

        if expected is not None and expected > 0:
            # The supplied expectation already prices in team quality, so the
            # attack/defense ratings are not applied a second time.
            mu = expected
        else:
            att = self._ratio(self._side_feature(ctx, side, ("attack", "off_rating", "ortg", "offense")))
            dfn = self._ratio(self._side_feature(ctx, other, ("defense", "def_rating", "drtg")))
            mu = anchor * att * dfn * pace_mult

        # Rest differential acts on efficiency (E[X]), not on pace.
        r_s = self._num(ctx.get(f"{side}_rest_days"))
        r_o = self._num(ctx.get(f"{other}_rest_days"))
        if r_s is not None and r_o is not None:
            sat = float(self.REST_SATURATION_DAYS)
            diff = max(-sat, min(sat, r_s - r_o))
            mu *= 1.0 + self.REST_EFFECT_PER_DAY * diff

        mu = max(mu, 1.0)
        # Var(N) solved from the target index, never below the mechanical floor.
        target_idx = self._num(ctx.get("dispersion_index"), self.TARGET_DISPERSION_INDEX)
        floor_var = n_joint * varx
        var_n_from_target = (target_idx * mu - floor_var) / (ex * ex)
        feasible = var_n_from_target >= 0.0
        var_n = var_n_from_target if feasible else self.possession_sd ** 2
        n_side = mu / ex
        var = n_side * varx + var_n * ex * ex
        self.last_diagnostics[f"{side}_target_feasible"] = bool(feasible)
        self.last_diagnostics[f"{side}_mechanical_floor_index"] = (n_side * varx) / mu
        return mu, var

    def _lattice(self, mu: float, var: float) -> Tuple[np.ndarray, np.ndarray]:
        """Discretize a normal with continuity correction onto integer points."""
        sd = math.sqrt(max(var, 1e-6))
        lo = max(0, int(math.floor(mu - self.LATTICE_SD_SPAN * sd)))
        hi = int(math.ceil(mu + self.LATTICE_SD_SPAN * sd))
        k = np.arange(lo, hi + 1)
        edges = np.concatenate(([k[0] - 0.5], k + 0.5))
        cdf = stats.norm.cdf(edges, loc=mu, scale=sd)
        cdf[0] = 0.0  # Fold all mass below the lattice into the lowest point (no negative scores).
        cdf[-1] = 1.0
        p = np.clip(np.diff(cdf), 0.0, None)
        p /= p.sum()
        return k, p

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        self.last_diagnostics = {}

        pace_h = self._side_feature(ctx, "home", ("pace", "tempo"))
        pace_a = self._side_feature(ctx, "away", ("pace", "tempo"))
        lg = float(self._num(ctx.get("league_pace"), self.LEAGUE_PACE_48))
        if pace_h and pace_a and pace_h > 0 and pace_a > 0:
            joint_pace = pace_h * pace_a / lg
        else:
            joint_pace = lg
        pace_mult = joint_pace / lg
        n_joint = 24.0 * joint_pace / 48.0  # possessions per team in a half

        mu_h, var_h = self._side_moments(ctx, "home", "away", self.anchor_home, n_joint, pace_mult)
        mu_a, var_a = self._side_moments(ctx, "away", "home", self.anchor_away, n_joint, pace_mult)
        self.last_diagnostics.update(
            home_mean=mu_h, home_var=var_h, away_mean=mu_a, away_var=var_a, n_possessions=n_joint
        )

        kh, ph = self._lattice(mu_h, var_h)
        ka, pa = self._lattice(mu_a, var_a)
        joint = np.outer(ph, pa)
        joint[joint < 1e-12] = 0.0
        joint /= joint.sum()

        ih, ia = np.nonzero(joint)
        scores: Dict[Tuple[int, int], float] = {
            (int(kh[i]), int(ka[j])): float(joint[i, j]) for i, j in zip(ih, ia)
        }
        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items()}
        return DiscreteDistribution.from_scores(scores)


# Engine discovery alias.
CandidateModel = NBAHalf1HomeTotalPossessionModel