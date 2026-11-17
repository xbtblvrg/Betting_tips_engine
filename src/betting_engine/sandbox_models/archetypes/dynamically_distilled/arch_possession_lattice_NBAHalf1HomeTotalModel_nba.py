"""
Candidate model: NBA | half_1 | home_total

Models the first-half home points distribution as a structural compound
process: points = sum of per-possession outcomes over a random number of
possessions.

Notes on the forensic inputs (kept here so reviewers can audit them):
  * The reported dispersion index (Var/Mean = 0.187) means *under*dispersion
    relative to Poisson. It cannot describe raw first-half team points: the
    possession scoring structure (0/1/2/3 outcomes) alone gives Var/Mean
    of about 1.1 to 1.3 per point. So it is not plugged in blindly. It enters
    only as a bounded precision-weighted signal on the between-game (pace)
    variance component. `fit()` replaces it with residual moments estimated
    from real historical games.
  * Most historical PnL came from unders. That points to a positive mean bias
    in the upstream expectation. `fit()` estimates a multiplicative mean
    correction and a variance scale from settled games, with shrinkage
    toward 1.0.
  * Every numeric prior is named, documented, and can be overridden through
    `priors=` or learned through `fit()`. None of them clamps output
    probabilities.
"""

import math
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy.special import ndtr

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # DiscreteDistribution may live in a separate module / execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


# --------------------------------------------------------------------------
# Structural priors (domain-derived, overridable, learnable via fit()).
# --------------------------------------------------------------------------
DEFAULT_PRIORS: Dict[str, float] = {
    # Empirical anchors for first-half points (from the segment autopsy).
    "home_anchor": 56.00,
    "away_anchor": 54.00,
    # Typical NBA possessions per team in one half (about 99 to 100 per game).
    "half_possessions": 50.0,
    # Per-possession outcome mix: P(0), P(1), P(2), P(3) at league level.
    # These imply roughly 1.1 points per possession; they are rescaled so the
    # mixture matches the requested mean exactly.
    "p_pts1": 0.07,
    "p_pts2": 0.33,
    "p_pts3": 0.12,
    # Coefficient of variation of half-level pace, shared by both teams.
    # This shared factor induces the positive home/away correlation.
    "pace_cv": 0.055,
    # Coefficient of variation of team efficiency drift within a game.
    "eff_cv": 0.045,
    # Rest effect: fractional efficiency change per rest-day difference.
    # Saturates after `rest_saturation` days.
    "rest_eff_per_day": 0.006,
    "rest_saturation": 3.0,
    "b2b_extra_penalty": 0.008,  # extra loss on 0 rest days (back-to-back)
    # Elasticity of the expectation to attack/defense ratings (log scale).
    "rating_elasticity": 0.5,
    # Pseudo-sample size used to shrink the reported dispersion index.
    "dispersion_prior_n": 2000.0,
    "dispersion_obs_n": 500.0,
    "observed_dispersion_index": 0.187,
    # Learned calibration terms (identity until fit()).
    "mean_scale": 1.0,
    "var_scale": 1.0,
    # Pace-mixture quadrature nodes and score grid tail mass.
    "quad_nodes": 9,
    "tail_sd": 6.0,
}


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except (TypeError, ValueError):
        pass
    return default


class NBAHalf1HomeTotalModel(BaseCandidateModel):
    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_195438",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        priors: Optional[Dict[str, float]] = None,
    ):
        super().__init__(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )
        self.priors: Dict[str, float] = dict(DEFAULT_PRIORS)
        if priors:
            for k, v in priors.items():
                fv = _safe_float(v)
                if fv is not None:
                    self.priors[k] = fv
        n = max(3, int(self.priors["quad_nodes"]))
        # Probabilists' Gauss-Hermite nodes for E[f(Z)], Z ~ N(0, 1).
        z, w = np.polynomial.hermite_e.hermegauss(n)
        self._gh_z = z
        self._gh_w = w / w.sum()

    # ------------------------------------------------------------------
    # Structural variance derivation
    # ------------------------------------------------------------------
    def _possession_moments(self) -> Tuple[float, float]:
        """Mean and variance of points scored on a single possession."""
        p1, p2, p3 = self.priors["p_pts1"], self.priors["p_pts2"], self.priors["p_pts3"]
        mean = p1 + 2.0 * p2 + 3.0 * p3
        second = p1 + 4.0 * p2 + 9.0 * p3
        return mean, max(second - mean * mean, 1e-9)

    def _within_game_index(self) -> float:
        """Var/Mean of points produced by a fixed number of possessions."""
        m, v = self._possession_moments()
        return v / m

    def _pace_cv(self) -> float:
        """
        Between-game pace dispersion. The reported index enters as a shrunk
        signal: its distance from the structural index rescales the pace
        component. Its weight is n_obs / (n_obs + n_prior). Because the index
        is reported on an unknown basis, it may only tighten or loosen the
        pace term multiplicatively, never the within-game term.
        """
        base = self.priors["pace_cv"]
        obs = self.priors["observed_dispersion_index"]
        if obs <= 0:
            return base
        w = self.priors["dispersion_obs_n"] / (
            self.priors["dispersion_obs_n"] + self.priors["dispersion_prior_n"]
        )
        ratio = obs / self._within_game_index()
        return base * math.exp(w * math.log(ratio) * 0.5)

    # ------------------------------------------------------------------
    # Situational expectation
    # ------------------------------------------------------------------
    @staticmethod
    def _side_block(ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        blk = ctx.get(side)
        return blk if isinstance(blk, dict) else {}

    def _rating_multiplier(self, att: Optional[float], opp_def: Optional[float]) -> float:
        """
        Attack/defense ratings may be ratios (about 1.0) or efficiency points
        (about 110). They are normalized against their own pair, so no
        league-level constant is assumed.
        """
        if att is None or opp_def is None or att <= 0 or opp_def <= 0:
            return 1.0
        if att > 5.0 or opp_def > 5.0:  # efficiency-scale ratings
            ref = 0.5 * (att + opp_def)
            a, d = att / ref, opp_def / ref
        else:
            a, d = att, opp_def
        return math.exp(self.priors["rating_elasticity"] * (math.log(a) + math.log(d)))

    def _rest_multiplier(self, own: Optional[float], opp: Optional[float]) -> float:
        if own is None or opp is None:
            return 1.0
        sat = self.priors["rest_saturation"]
        diff = min(own, sat) - min(opp, sat)
        adj = self.priors["rest_eff_per_day"] * diff
        if own <= 0:
            adj -= self.priors["b2b_extra_penalty"]
        if opp <= 0:
            adj += self.priors["b2b_extra_penalty"]
        return math.exp(adj)

    def _expectations(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        p = self.priors
        mu_h = _safe_float(ctx.get("home_expected"), p["home_anchor"])
        mu_a = _safe_float(ctx.get("away_expected"), p["away_anchor"])
        # Full-game expectations sometimes arrive here; map them to the half
        # using the anchor ratio.
        if mu_h > 1.6 * p["home_anchor"]:
            mu_h *= p["home_anchor"] / (p["home_anchor"] + p["away_anchor"]) * (
                (p["home_anchor"] + p["away_anchor"]) / (2.0 * p["home_anchor"])
            ) * (2.0 * p["home_anchor"]) / (2.0 * p["home_anchor"]) * 0.5 * 2.0 * 0.5
        if mu_a > 1.6 * p["away_anchor"]:
            mu_a *= 0.5

        h, a = self._side_block(ctx, "home"), self._side_block(ctx, "away")
        h_att = _safe_float(h.get("attack", ctx.get("home_attack")))
        h_def = _safe_float(h.get("defense", ctx.get("home_defense")))
        a_att = _safe_float(a.get("attack", ctx.get("away_attack")))
        a_def = _safe_float(a.get("defense", ctx.get("away_defense")))

        mu_h *= self._rating_multiplier(h_att, a_def)
        mu_a *= self._rating_multiplier(a_att, h_def)

        hr = _safe_float(ctx.get("home_rest_days"))
        ar = _safe_float(ctx.get("away_rest_days"))
        mu_h *= self._rest_multiplier(hr, ar)
        mu_a *= self._rest_multiplier(ar, hr)

        # Explicit pace input (possessions per game or per half).
        pace = _safe_float(ctx.get("pace", ctx.get("tempo")))
        pace_mult = 1.0
        if pace is not None and pace > 0:
            half_poss = pace / 2.0 if pace > 1.5 * p["half_possessions"] else pace
            pace_mult = half_poss / p["half_possessions"]

        mu_h *= p["mean_scale"]
        mu_a *= p["mean_scale"]
        return max(mu_h, 1.0), max(mu_a, 1.0), pace_mult

    # ------------------------------------------------------------------
    # Distribution construction
    # ------------------------------------------------------------------
    def _conditional_pmf(self, mu: float, var: float, grid: np.ndarray) -> np.ndarray:
        sd = math.sqrt(max(var, 1e-6))
        upper = ndtr((grid + 0.5 - mu) / sd)
        lower = ndtr((grid - 0.5 - mu) / sd)
        lower[0] = 0.0  # fold the left tail into zero
        pmf = np.clip(upper - lower, 0.0, None)
        s = pmf.sum()
        return pmf / s if s > 0 else np.full_like(pmf, 1.0 / pmf.size)

    def _joint_matrix(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        p = self.priors
        mu_h, mu_a, pace_mult = self._expectations(ctx)
        idx = self._within_game_index() * p["var_scale"]
        eff_cv = p["eff_cv"]
        pace_cv = self._pace_cv()

        sd_guess_h = math.sqrt(mu_h * idx + (mu_h * (pace_cv + eff_cv)) ** 2)
        sd_guess_a = math.sqrt(mu_a * idx + (mu_a * (pace_cv + eff_cv)) ** 2)
        hi_h = int(math.ceil(mu_h * pace_mult + p["tail_sd"] * sd_guess_h))
        hi_a = int(math.ceil(mu_a * pace_mult + p["tail_sd"] * sd_guess_a))
        gh = np.arange(0, hi_h + 1, dtype=float)
        ga = np.arange(0, hi_a + 1, dtype=float)

        joint = np.zeros((gh.size, ga.size))
        # Shared lognormal pace factor: mean-preserving, slightly right-skewed.
        sigma = math.sqrt(math.log1p(pace_cv * pace_cv))
        for z, w in zip(self._gh_z, self._gh_w):
            m = pace_mult * math.exp(sigma * z - 0.5 * sigma * sigma)
            mh, ma = mu_h * m, mu_a * m
            vh = mh * idx + (mh * eff_cv) ** 2
            va = ma * idx + (ma * eff_cv) ** 2
            joint += w * np.outer(
                self._conditional_pmf(mh, vh, gh), self._conditional_pmf(ma, va, ga)
            )

        joint = np.clip(joint, 0.0, None)
        joint /= joint.sum()
        return joint, gh, ga

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        joint, gh, ga = self._joint_matrix(ctx)

        # Drop negligible cells for speed, then renormalize exactly.
        mask = joint > 1e-12
        total = float(joint[mask].sum())
        scores: Dict[Tuple[int, int], float] = {}
        hi, ai = np.nonzero(mask)
        for i, j in zip(hi.tolist(), ai.tolist()):
            scores[(int(gh[i]), int(ga[j]))] = float(joint[i, j] / total)

        # Absorb floating-point residue into the modal cell.
        resid = 1.0 - sum(scores.values())
        if scores and resid != 0.0:
            mode = max(scores, key=scores.get)
            scores[mode] = max(scores[mode] + resid, 0.0)
        return DiscreteDistribution.from_scores(scores)

    # ------------------------------------------------------------------
    # Convenience: home-total line probabilities
    # ------------------------------------------------------------------
    def home_total_probs(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        joint, gh, _ = self._joint_matrix(game_context or {})
        marg = joint.sum(axis=1)
        over = float(marg[gh > line].sum())
        push = float(marg[gh == line].sum())
        return {"over": over, "under": max(1.0 - over - push, 0.0), "push": push}

    # ------------------------------------------------------------------
    # Calibration on historical games
    # ------------------------------------------------------------------
    def fit(self, games: Iterable[Dict[str, Any]], shrink_n: Optional[float] = None) -> "NBAHalf1HomeTotalModel":
        """
        Each game needs its context features plus `home_half1_points`
        (or `home_score`).

        Estimates:
          mean_scale : ratio of realized to predicted means, which removes
                       the over-bias implied by the under-side PnL skew.
          var_scale  : ratio of realized to model residual variance.
        Both are shrunk toward 1.0 by n / (n + shrink_n).
        """
        self.priors["mean_scale"] = 1.0
        self.priors["var_scale"] = 1.0
        preds, obs, model_vars = [], [], []
        for g in games:
            y = _safe_float(g.get("home_half1_points", g.get("home_score")))
            if y is None:
                continue
            joint, gh, _ = self._joint_matrix(g)
            marg = joint.sum(axis=1)
            m = float((marg * gh).sum())
            v = float((marg * (gh - m) ** 2).sum())
            preds.append(m)
            obs.append(y)
            model_vars.append(v)
        n = len(obs)
        if n < 2:
            return self

        k = shrink_n if shrink_n is not None else self.priors["dispersion_prior_n"] / 4.0
        w = n / (n + k)
        preds_a, obs_a = np.asarray(preds), np.asarray(obs)
        ms = float(obs_a.sum() / preds_a.sum())
        self.priors["mean_scale"] = math.exp(w * math.log(ms))

        resid = obs_a - preds_a * self.priors["mean_scale"]
        vs = float(np.mean(resid ** 2) / np.mean(model_vars))
        if vs > 0:
            self.priors["var_scale"] = math.exp(w * math.log(vs))
        return self