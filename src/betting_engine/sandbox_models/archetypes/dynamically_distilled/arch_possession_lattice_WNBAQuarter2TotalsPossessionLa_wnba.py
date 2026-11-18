"""
WNBA | quarter_2 | totals: possession-lattice compound model.

Structure
---------
Second-quarter points are a compound process over a shared, clock-bound
possession count N:

    H | N ~ sum_{i=1..N} X_i^H,   A | N ~ sum_{i=1..N} X_i^A,   X in {0, 1, 2, 3}

    N   : discretised Normal(mu_N, (cv_N * mu_N)^2)
    X   : categorical over the scoring lattice, exponentially tilted so that
          E[N] * E[X] reproduces each team's situational expectation.
    eps : shared game-environment efficiency shock (shooting variance,
          officiating crew, whistle density), log-normal, integrated by
          Gauss-Hermite quadrature.

Design notes
------------
* N is modelled with a discretised Normal, not a Negative Binomial. Possession
  counts in a 10-minute quarter are bounded by the clock and are under-dispersed
  relative to Poisson (Var(N) < E[N]). A NegBin is always over-dispersed, so it
  cannot represent this. The excess dispersion of the total comes from two
  sources: the 0/1/2/3 lattice, and the shared pace and efficiency shocks. That
  shared component also induces positive home/away covariance.
* Q2 bench and foul dynamics move lattice mass toward free-throw outcomes and
  slightly lower efficiency. The weight of that shift is a prior parameter.
* All structural constants are explicit, documented priors. They can be
  overridden through the constructor and updated by `update_from_observations`
  with hierarchical shrinkage. Lattice bounds (0 <= E[X] <= 3) are physical
  support limits, not tuning clamps.

Caveat
------
The cold-start segment has 0 settled bets, so nothing here is validated
out-of-sample. The quoted +89% to +118% ROI benchmarks are far above what any
sustainable totals edge produces. Treat them as likely artefacts of small
samples or leakage, not as targets this model can be expected to reproduce.
Evaluate this model on log-loss and Brier score on held-out Q2 results before
staking.
"""

import math
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may be exported by the base module or pre-loaded into scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class WNBAQuarter2TotalsPossessionLatticeModel(BaseCandidateModel):
    """Compound possession-lattice model for WNBA second-quarter totals."""

    # ------------------------------------------------------------------ #
    # Domain priors (overridable)
    # ------------------------------------------------------------------ #
    DEFAULT_PRIORS: Dict[str, float] = {
        # Empirical segment anchors (Q2 points)
        "baseline_home": 20.00,
        "baseline_away": 18.75,

        # Pace: about 80 possessions per 40 minutes, so about 20 per team per quarter
        "possessions_per_quarter": 20.0,
        # Coefficient of variation of Q2 possessions (observed range about 17-23)
        "pace_cv": 0.085,
        # Q2 rotation pace factor (bench units, more stoppages); 1.0 means neutral
        "q2_rotation_pace": 0.985,
        # Elasticity of pace with respect to log rest advantage (fatigue lowers tempo)
        "rest_pace_elasticity": 0.015,
        # Typical rest days (neutral reference)
        "rest_reference_days": 2.0,

        # Base per-possession outcome lattice P(0), P(1), P(2), P(3).
        # Mean is about 1.01 points per possession. "1" captures one-of-two FT
        # trips and lone FTs; "2" includes FG2 and two-shot FT trips; "3"
        # includes and-ones.
        "p0": 0.515,
        "p1": 0.050,
        "p2": 0.335,
        "p3": 0.100,
        # Q2 foul-trouble / bench free-throw mass shift (fraction of FG mass moved to FT outcomes)
        "q2_ft_shift": 0.06,

        # Shared game-environment efficiency shock (log-sd)
        "efficiency_sigma": 0.075,
        # Partial-pooling weight on attack/defence exposure (expectations usually embed most of it)
        "exposure_weight": 0.35,

        # Numerical settings
        "gh_nodes": 7.0,
        "pace_tail_sd": 6.0,
        "prune_mass": 1e-10,

        # Hierarchical shrinkage strength (pseudo-games) for online updates
        "prior_games": 150.0,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_2_1107_181400",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_2",
        family: str = "totals",
        priors: Optional[Dict[str, float]] = None,
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

        self.priors: Dict[str, float] = dict(self.DEFAULT_PRIORS)
        if priors:
            self.priors.update({k: float(v) for k, v in priors.items()})

        gh_n = max(int(self.priors["gh_nodes"]), 1)
        x, w = np.polynomial.hermite_e.hermegauss(gh_n)
        self._gh_z = x
        self._gh_w = w / w.sum()

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _as_float(v: Any) -> Optional[float]:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    def _get(self, ctx: Dict[str, Any], side: Optional[str], *names: str) -> Optional[float]:
        """Look up a feature as flat 'side_name', nested ctx[side][name], or plain name."""
        for name in names:
            if side is not None:
                v = self._as_float(ctx.get(f"{side}_{name}"))
                if v is not None:
                    return v
                sub = ctx.get(side)
                if isinstance(sub, dict):
                    v = self._as_float(sub.get(name))
                    if v is not None:
                        return v
            else:
                v = self._as_float(ctx.get(name))
                if v is not None:
                    return v
        return None

    @staticmethod
    def _to_multiplier(v: Optional[float], league_ref: Optional[float]) -> float:
        """Map a rating in any common convention to a multiplicative factor around 1."""
        if v is None:
            return 1.0
        if league_ref is not None and league_ref > 0 and v > 3.0:
            return v / league_ref           # per-100 style rating relative to league mean
        if 0.3 < v <= 3.0:
            return v                        # already multiplicative
        if abs(v) <= 0.3 or v < 0:
            return math.exp(v)              # log-scale / additive-centred rating
        return 1.0                          # unrecognised scale: neutral

    def _exposure(self, ctx: Dict[str, Any], att_side: str, def_side: str) -> float:
        league_ref = self._get(ctx, None, "league_rating", "league_ortg", "league_efficiency")
        att = self._to_multiplier(self._get(ctx, att_side, "attack", "offense", "ortg"), league_ref)
        dfn = self._to_multiplier(self._get(ctx, def_side, "defense", "defence", "drtg"), league_ref)
        # A defence multiplier > 1 means more points allowed (exposure convention)
        log_exp = math.log(max(att, 1e-9)) + math.log(max(dfn, 1e-9))
        return math.exp(self.priors["exposure_weight"] * log_exp)

    def _pace_mean(self, ctx: Dict[str, Any]) -> float:
        base = self.priors["possessions_per_quarter"]
        hp = self._get(ctx, "home", "pace", "tempo")
        ap = self._get(ctx, "away", "pace", "tempo")
        league_pace = self._get(ctx, None, "league_pace")

        def rel(p: Optional[float]) -> float:
            if p is None:
                return 1.0
            if league_pace is not None and league_pace > 0 and p > 3.0:
                return p / league_pace
            if p > 3.0:                      # possessions per 40 minutes
                return (p / 4.0) / base
            return p if p > 0 else 1.0       # already relative

        pace_rel = math.sqrt(rel(hp) * rel(ap))

        hr = self._get(ctx, None, "home_rest_days")
        ar = self._get(ctx, None, "away_rest_days")
        ref = self.priors["rest_reference_days"]
        rest_terms = []
        for r in (hr, ar):
            if r is not None and r >= 0:
                rest_terms.append(math.log1p(r) - math.log1p(ref))
        rest_effect = math.exp(self.priors["rest_pace_elasticity"] * (sum(rest_terms) / 2.0)) \
            if rest_terms else 1.0

        return base * pace_rel * rest_effect * self.priors["q2_rotation_pace"]

    def _team_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mh = self._get(ctx, None, "home_expected")
        ma = self._get(ctx, None, "away_expected")
        mh = mh if (mh is not None and mh > 0) else self.priors["baseline_home"]
        ma = ma if (ma is not None and ma > 0) else self.priors["baseline_away"]
        mh *= self._exposure(ctx, "home", "away")
        ma *= self._exposure(ctx, "away", "home")
        return mh, ma

    # ------------------------------------------------------------------ #
    # Lattice mechanics
    # ------------------------------------------------------------------ #
    def _base_lattice(self) -> np.ndarray:
        p = np.array([self.priors["p0"], self.priors["p1"], self.priors["p2"], self.priors["p3"]], float)
        p = p / p.sum()
        # Q2 bench / foul-trouble: move a fraction of FG mass into FT-type outcomes
        s = self.priors["q2_ft_shift"]
        moved2, moved3 = s * p[2], s * p[3]
        p[2] -= moved2
        p[3] -= moved3
        p[1] += 0.5 * (moved2 + moved3)   # one-of-two / lone free throws
        p[2] += 0.5 * (moved2 + moved3)   # two-shot trips
        return p / p.sum()

    @staticmethod
    def _tilt(base: np.ndarray, target_mean: float) -> np.ndarray:
        """Minimum-KL (exponential) tilt of the lattice to hit a target per-possession mean."""
        k = np.arange(base.size, dtype=float)
        eps = 1e-6
        m = min(max(target_mean, eps), k[-1] - eps)  # physical lattice support

        def mean_gap(theta: float) -> float:
            w = base * np.exp(theta * k)
            return float((w * k).sum() / w.sum()) - m

        lo, hi = -40.0, 40.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        w = base * np.exp(theta * k)
        return w / w.sum()

    def _pace_pmf(self, mu_n: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = max(self.priors["pace_cv"] * mu_n, 1e-6)
        span = self.priors["pace_tail_sd"] * sd
        n_lo = max(1, int(math.floor(mu_n - span)))
        n_hi = max(n_lo + 1, int(math.ceil(mu_n + span)))
        n = np.arange(n_lo, n_hi + 1)
        cdf = stats.norm.cdf((np.append(n, n_hi + 1) - 0.5 - mu_n) / sd)
        pmf = np.diff(cdf)
        pmf = np.clip(pmf, 0.0, None)
        return n, pmf / pmf.sum()

    @staticmethod
    def _convolution_powers(cat: np.ndarray, n_max: int) -> list:
        powers = [np.array([1.0])]
        cur = powers[0]
        for _ in range(n_max):
            cur = np.convolve(cur, cat)
            powers.append(cur)
        return powers

    # ------------------------------------------------------------------ #
    # Core joint distribution
    # ------------------------------------------------------------------ #
    def joint_score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a = self._team_means(game_context)
        mu_n = self._pace_mean(game_context)
        n_vals, n_pmf = self._pace_pmf(mu_n)
        e_n = float((n_vals * n_pmf).sum())
        n_max = int(n_vals[-1])

        base = self._base_lattice()
        sig = self.priors["efficiency_sigma"]
        size = 3 * n_max + 1
        joint = np.zeros((size, size))

        for z, wz in zip(self._gh_z, self._gh_w):
            shock = math.exp(sig * z - 0.5 * sig * sig)   # mean-preserving
            cat_h = self._tilt(base, mu_h * shock / e_n)
            cat_a = self._tilt(base, mu_a * shock / e_n)
            pow_h = self._convolution_powers(cat_h, n_max)
            pow_a = self._convolution_powers(cat_a, n_max)
            for n, pn in zip(n_vals, n_pmf):
                if pn <= 0:
                    continue
                h, a = pow_h[n], pow_a[n]
                joint[: h.size, : a.size] += wz * pn * np.outer(h, a)

        joint /= joint.sum()
        return joint

    def total_distribution(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        joint = self.joint_score_matrix(game_context)
        size = joint.shape[0]
        tot = np.zeros(2 * size - 1)
        for i in range(size):
            tot[i : i + size] += joint[i]
        return {t: float(p) for t, p in enumerate(tot) if p > 0}

    def price_total(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Over/under/push probabilities from the discrete total (handles integer pushes)."""
        dist = self.total_distribution(game_context)
        over = sum(p for t, p in dist.items() if t > line)
        under = sum(p for t, p in dist.items() if t < line)
        push = sum(p for t, p in dist.items() if t == line)
        return {"over": over, "under": under, "push": push}

    # ------------------------------------------------------------------ #
    # Required interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.joint_score_matrix(game_context)
        cut = self.priors["prune_mass"]
        scores: Dict[Tuple[int, int], float] = {}
        idx = np.argwhere(joint > cut)
        for i, j in idx:
            scores[(int(i), int(j))] = float(joint[i, j])
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            mat = np.zeros_like(joint)
            for (i, j), p in scores.items():
                mat[i, j] = p
            return DiscreteDistribution.from_scores(mat)

    # ------------------------------------------------------------------ #
    # Online calibration with hierarchical shrinkage
    # ------------------------------------------------------------------ #
    def update_from_observations(
        self,
        observations: Iterable[Dict[str, Any]],
    ) -> Dict[str, float]:
        """
        Moment-matching update of pace_cv and efficiency_sigma from settled Q2 games.

        Each observation needs 'home_q2', 'away_q2' (realised points) plus the
        pre-game context fields used by the model. The observed covariance of
        home/away residuals identifies the shared variance (pace + efficiency).
        The update is shrunk toward the prior using `prior_games` pseudo-games.
        """
        obs = list(observations)
        if len(obs) < 2:
            return {"pace_cv": self.priors["pace_cv"], "efficiency_sigma": self.priors["efficiency_sigma"]}

        rh, ra, mh_list, ma_list = [], [], [], []
        for o in obs:
            h, a = self._as_float(o.get("home_q2")), self._as_float(o.get("away_q2"))
            if h is None or a is None:
                continue
            mh, ma = self._team_means(o)
            rh.append(h - mh)
            ra.append(a - ma)
            mh_list.append(mh)
            ma_list.append(ma)
        n = len(rh)
        if n < 2:
            return {"pace_cv": self.priors["pace_cv"], "efficiency_sigma": self.priors["efficiency_sigma"]}

        cov = float(np.cov(np.array(rh), np.array(ra))[0, 1])
        prod = float(np.mean(np.array(mh_list) * np.array(ma_list)))
        # Shared multiplicative variance s^2 is approximately cv_N^2 + sigma_eff^2,
        # so Cov(H, A) is approximately mu_H * mu_A * s^2
        shared_obs = max(cov / prod, 0.0) if prod > 0 else 0.0

        cv0, sg0 = self.priors["pace_cv"], self.priors["efficiency_sigma"]
        shared_prior = cv0 ** 2 + sg0 ** 2
        w = n / (n + self.priors["prior_games"])
        shared_post = (1 - w) * shared_prior + w * shared_obs
        scale = math.sqrt(shared_post / shared_prior) if shared_prior > 0 else 1.0
        self.priors["pace_cv"] = cv0 * scale
        self.priors["efficiency_sigma"] = sg0 * scale
        return {"pace_cv": self.priors["pace_cv"], "efficiency_sigma": self.priors["efficiency_sigma"]}

    # ------------------------------------------------------------------ #
    # Wager evaluation helper (continuous EV, no step filters)
    # ------------------------------------------------------------------ #
    def expected_value(self, game_context: Dict[str, Any], line: float, side: str, decimal_odds: float) -> float:
        probs = self.price_total(game_context, line)
        p_win = probs["over"] if side.lower().startswith("o") else probs["under"]
        return p_win * (decimal_odds - 1.0) - (1.0 - p_win - probs["push"])

    def is_eligible(self, game_context: Dict[str, Any], line: float, side: str, decimal_odds: float) -> bool:
        return decimal_odds >= 1.40 and self.expected_value(game_context, line, side, decimal_odds) > 0.0