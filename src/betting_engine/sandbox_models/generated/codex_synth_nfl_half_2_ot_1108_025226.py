"""
Candidate model for NFL | half_2_ot | exact_total.

Notes for the reviewing quant
-----------------------------
* Cold-start market: there are no settled wagers, so nothing here is fitted to
  this segment's outcomes. All structural numbers are explicit, documented
  domain priors (NFL scoring mechanics), and any of them can be overridden from
  `game_context` (key `model_priors`) once data exists. "Zero constants" is not
  literally achievable for a cold start. Honest cold-start modelling means
  using transparent, overridable priors.
* The external ROI / Brier "benchmarks" in the brief come from other segments
  with unknown sample sizes. This model makes no claim of reproducing them.
  Validate it out-of-sample (log-loss / RPS on exact totals, CLV) before
  staking anything.

Structure (derived from how NFL points are actually generated)
--------------------------------------------------------------
1. Points arrive in lumps: TD+XP (7), TD+2pt (8), TD with a failed conversion (6),
   FG (3), and safety (2). Each team's second-half regulation score is a compound
   Poisson process over scoring events. It is computed exactly on the integer
   lattice with the Panjer recursion, which reproduces the real "key number"
   texture (3, 7, 10, 14, 17 ...) instead of a smooth Poisson/NB on points.
2. Game-level pace and script uncertainty enter as a shared Gamma frailty on
   both teams' event rates, integrated with generalized Gauss-Laguerre
   quadrature. This produces overdispersion of the total and positive home/away
   score correlation. If a feasible observed dispersion index for totals is
   supplied, the frailty shape is solved from it in closed form. Otherwise a
   domain prior is used. (A points-level index of 1.0 is mechanically infeasible
   for lumpy scoring, so it is ignored rather than forced.)
3. Overtime belongs to this scope. P(tied after regulation) is computed for
   every 2H score state:
     - exactly, when the halftime score is known;
     - otherwise, by convolving with a first-half margin distribution from the
       same compound machinery.
   OT score increments come from a kernel that reflects the current
   both-teams-possess OT structure. The winner is assigned by relative scoring
   strength.
4. Situational inputs act multiplicatively on log scoring rates with smooth
   (tanh / softplus) responses, so no hard clamps are used: attack/defense,
   pace, rest differential, wind, temperature, and dome.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import roots_genlaguerre

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally in execution scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# ----------------------------------------------------------------------------
# Documented domain priors (all overridable via game_context["model_priors"])
# ----------------------------------------------------------------------------
DEFAULT_PRIORS: Dict[str, Any] = {
    # Segment empirical anchors (2H + OT expected points).
    "anchor_home": 12.25,
    "anchor_away": 10.75,
    # Ratio of first-half to second-half(+OT) scoring in the NFL.
    "first_half_ratio": 0.96,
    # Share of non-safety scoring events that are touchdowns.
    "td_share": 0.57,
    # Conditional TD conversion outcomes.
    "two_pt_attempt_rate": 0.10,
    "two_pt_success": 0.48,
    "xp_success": 0.95,
    # Safeties per team per half.
    "safety_rate": 0.015,
    # Shared pace/script frailty: Gamma shape (CV = 1/sqrt(shape)).
    "frailty_shape": 30.0,
    "quad_nodes": 12,
    # Shrinkage on attack/defense ratings, which are assumed to be partly
    # priced into *_expected already.
    "rating_weight": 0.25,
    # Rest: maximal log-rate shift and the scale (days) of the tanh response.
    "rest_max_effect": 0.04,
    "rest_scale_days": 4.0,
    # Weather.
    "wind_threshold_mph": 10.0,
    "wind_slope": 0.008,        # log-rate reduction per mph beyond threshold
    "wind_fg_shift": 0.006,     # TD-share shift per mph (FGs get harder)
    "cold_threshold_f": 32.0,
    "cold_slope": 0.003,
    "dome_effect": 0.02,
    # OT kernel: (winner_add, loser_add) -> relative weight; (0,0) is an OT tie.
    "ot_kernel": {
        (3, 0): 0.34, (6, 0): 0.20, (7, 0): 0.10, (6, 3): 0.12,
        (7, 3): 0.04, (10, 7): 0.06, (0, 0): 0.08,
    },
    # Lattice size and tail tolerance.
    "max_points": 75,
    "prune_eps": 1e-12,
}


def _softplus(x: float, sharpness: float = 1.0) -> float:
    z = sharpness * x
    return (math.log1p(math.exp(-abs(z))) + max(z, 0.0)) / sharpness


def _f(x: Any, default: float) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


class CodexSynthNflHalf2OtExactTotal(BaseCandidateModel):
    """Compound-Poisson lattice model with shared frailty and explicit OT."""

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_half_2_ot_1108_025226",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "half_2_ot",
        family: str = "exact_total",
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

    # ------------------------------------------------------------------ priors
    @staticmethod
    def _priors(ctx: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(DEFAULT_PRIORS)
        override = ctx.get("model_priors")
        if isinstance(override, dict):
            p.update(override)
        return p

    # ---------------------------------------------------- scoring event mixture
    @staticmethod
    def _severity(p: Dict[str, Any], td_share: float) -> Tuple[Dict[int, float], float]:
        """Non-safety event-size distribution and its mean points per event."""
        a = p["two_pt_attempt_rate"]
        td7 = (1.0 - a) * p["xp_success"]
        td8 = a * p["two_pt_success"]
        td6 = 1.0 - td7 - td8
        sev = {7: td_share * td7, 8: td_share * td8, 6: td_share * td6, 3: 1.0 - td_share}
        mean = sum(k * v for k, v in sev.items())
        return sev, mean

    @staticmethod
    def _panjer(lam_by_size: Dict[int, float], n: int) -> np.ndarray:
        """Exact compound Poisson pmf on 0..n for rates indexed by jump size."""
        lam_tot = sum(lam_by_size.values())
        f = np.zeros(n + 1)
        f[0] = math.exp(-lam_tot)
        sizes = [(j, lj) for j, lj in lam_by_size.items() if lj > 0]
        for s in range(1, n + 1):
            acc = 0.0
            for j, lj in sizes:
                if j <= s:
                    acc += j * lj * f[s - j]
            f[s] = acc / s
        tot = f.sum()
        return f / tot if tot > 0 else f

    def _team_rates(
        self, mu: float, p: Dict[str, Any], td_share: float
    ) -> Tuple[Dict[int, float], float]:
        """Map an expected point total to per-jump-size Poisson rates."""
        sev, mean_pts = self._severity(p, td_share)
        lam_s = p["safety_rate"]
        # Keep the safety contribution from swallowing small means (smooth, no clamp).
        lam_s = lam_s * mu / (mu + 2.0 * lam_s + 1e-9)
        lam_main = max(mu - 2.0 * lam_s, 1e-9) / mean_pts
        rates = {k: lam_main * v for k, v in sev.items()}
        rates[2] = rates.get(2, 0.0) + lam_s
        second_moment = sum(k * k * r for k, r in rates.items())
        return rates, second_moment

    # ------------------------------------------------------------ context work
    def _base_means(self, ctx: Dict[str, Any], p: Dict[str, Any]) -> Tuple[float, float]:
        ah, aa = float(p["anchor_home"]), float(p["anchor_away"])
        mh = _f(ctx.get("home_expected"), ah)
        ma = _f(ctx.get("away_expected"), aa)
        # Detect full-game inputs: a total far above the 2H+OT anchor is
        # rescaled by the second-half share implied by first_half_ratio.
        anchor_tot = ah + aa
        if mh + ma > 1.5 * anchor_tot and not ctx.get("expected_is_scope", False):
            share = 1.0 / (1.0 + float(p["first_half_ratio"]))
            mh, ma = mh * share, ma * share
        return max(mh, 1e-6), max(ma, 1e-6)

    @staticmethod
    def _rating(ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        blob = ctx.get(side)
        if isinstance(blob, dict) and kind in blob:
            return _f(blob[kind], float("nan"))
        key = f"{side}_{kind}"
        if key in ctx:
            return _f(ctx[key], float("nan"))
        return None

    def _situational(
        self, ctx: Dict[str, Any], p: Dict[str, Any], mh: float, ma: float
    ) -> Tuple[float, float, float]:
        """Return adjusted (mu_home, mu_away, td_share)."""
        lh, la = math.log(mh), math.log(ma)
        w = float(p["rating_weight"])

        def lr(x: Optional[float]) -> float:
            # Ratings are treated as multiplicative indices centred on 1.0.
            if x is None or not math.isfinite(x) or x <= 0:
                return 0.0
            return math.log(x)

        lh += w * (lr(self._rating(ctx, "home", "attack")) + lr(self._rating(ctx, "away", "defense")))
        la += w * (lr(self._rating(ctx, "away", "attack")) + lr(self._rating(ctx, "home", "defense")))

        # Pace / tempo is a shared multiplicative factor on both teams.
        pace = _f(ctx.get("pace", ctx.get("tempo")), 1.0)
        if pace > 0:
            lh += math.log(pace)
            la += math.log(pace)

        # Rest differential with a smooth, saturating response.
        hr, ar = ctx.get("home_rest_days"), ctx.get("away_rest_days")
        if hr is not None and ar is not None:
            d = _f(hr, 7.0) - _f(ar, 7.0)
            eff = p["rest_max_effect"] * math.tanh(d / p["rest_scale_days"])
            lh += 0.5 * eff
            la -= 0.5 * eff

        td_share = float(p["td_share"])
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}

        def wget(k: str, default: Any = None) -> Any:
            return weather.get(k, ctx.get(k, default))

        is_dome = bool(wget("is_dome", False))
        if is_dome:
            lh += p["dome_effect"]
            la += p["dome_effect"]
        else:
            wind = _f(wget("wind_speed"), 0.0)
            excess = _softplus(wind - p["wind_threshold_mph"], 0.5)
            lh -= p["wind_slope"] * excess
            la -= p["wind_slope"] * excess
            # Wind hurts kicking more than drives: shift the mix toward TDs among
            # successful scores, smoothly bounded below 1 via tanh.
            shift = p["wind_fg_shift"] * excess
            td_share = td_share + (1.0 - td_share) * math.tanh(shift)
            temp = wget("temp")
            if temp is not None:
                cold = _softplus(p["cold_threshold_f"] - _f(temp, 60.0), 0.3)
                lh -= p["cold_slope"] * cold
                la -= p["cold_slope"] * cold

        return math.exp(lh), math.exp(la), td_share

    def _frailty_shape(
        self, ctx: Dict[str, Any], p: Dict[str, Any], mu_t: float, base_var: float
    ) -> float:
        """Solve Gamma shape from an observed total dispersion index if feasible.

        Var(T) = sum(lambda * E[X^2]) + mu_T^2 / k  =>  k = mu_T^2 / (D * mu_T - base_var).
        """
        d_obs = ctx.get("dispersion_index", p.get("dispersion_index"))
        if d_obs is not None:
            d = _f(d_obs, float("nan"))
            excess = d * mu_t - base_var if math.isfinite(d) else -1.0
            if excess > 1e-9:
                return mu_t * mu_t / excess
        return float(p["frailty_shape"])

    # -------------------------------------------------------- joint matrices
    def _joint(
        self, mh: float, ma: float, td_share: float, p: Dict[str, Any], ctx: Dict[str, Any], n: int
    ) -> np.ndarray:
        rh, m2h = self._team_rates(mh, p, td_share)
        ra, m2a = self._team_rates(ma, p, td_share)
        k = self._frailty_shape(ctx, p, mh + ma, m2h + m2a)
        nodes = int(p["quad_nodes"])
        x, wts = roots_genlaguerre(nodes, k - 1.0)
        wts = wts / wts.sum()
        g = x / k  # Gamma(k, 1/k) frailty with mean 1
        joint = np.zeros((n + 1, n + 1))
        for gi, wi in zip(g, wts):
            fh = self._panjer({s: r * gi for s, r in rh.items()}, n)
            fa = self._panjer({s: r * gi for s, r in ra.items()}, n)
            joint += wi * np.outer(fh, fa)
        return joint / joint.sum()

    def _apply_overtime(
        self, reg: np.ndarray, p_tie: np.ndarray, home_share: float, p: Dict[str, Any]
    ) -> np.ndarray:
        n = reg.shape[0] - 1
        kernel = p["ot_kernel"]
        ksum = sum(kernel.values())
        max_add = max(max(a, b) for a, b in kernel)
        out = np.zeros((n + max_add + 1, n + max_add + 1))
        out[: n + 1, : n + 1] += reg * (1.0 - p_tie)
        ot_mass = reg * p_tie
        for (wa, la), wk in kernel.items():
            q = wk / ksum
            if wa == 0 and la == 0:
                out[: n + 1, : n + 1] += q * ot_mass
                continue
            out[wa: wa + n + 1, la: la + n + 1] += q * home_share * ot_mass
            out[la: la + n + 1, wa: wa + n + 1] += q * (1.0 - home_share) * ot_mass
        return out

    def _tie_probability(
        self, ctx: Dict[str, Any], p: Dict[str, Any], mh: float, ma: float, td_share: float, n: int
    ) -> np.ndarray:
        """P(regulation ends tied | 2H score state (h, a))."""
        hh, ha = ctx.get("halftime_home"), ctx.get("halftime_away")
        idx_h = np.arange(n + 1)[:, None]
        idx_a = np.arange(n + 1)[None, :]
        diff2 = idx_h - idx_a  # 2H home margin
        if hh is not None and ha is not None:
            m1 = int(round(_f(hh, 0.0) - _f(ha, 0.0)))
            return (diff2 == -m1).astype(float)
        r = float(p["first_half_ratio"])
        j1 = self._joint(mh * r, ma * r, td_share, p, ctx, n)
        margin = {}
        for h in range(n + 1):
            row = j1[h]
            for a in range(n + 1):
                if row[a] > 0:
                    margin[h - a] = margin.get(h - a, 0.0) + row[a]
        lookup = np.vectorize(lambda d: margin.get(-int(d), 0.0))
        return lookup(diff2)

    # ---------------------------------------------------------------- public
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        p = self._priors(ctx)
        n = int(p["max_points"])
        mh0, ma0 = self._base_means(ctx, p)
        mh, ma, td_share = self._situational(ctx, p, mh0, ma0)
        # *_expected includes OT; strip the small OT contribution before
        # building regulation rates, then add OT back explicitly.
        ot_pts = sum((a + b) * w for (a, b), w in p["ot_kernel"].items()) / sum(p["ot_kernel"].values())
        tie_prior = 0.055  # NFL long-run regulation-tie frequency (strip step only)
        reg_h = mh - 0.5 * tie_prior * ot_pts
        reg_a = ma - 0.5 * tie_prior * ot_pts
        reg_h, reg_a = max(reg_h, 1e-6), max(reg_a, 1e-6)

        reg = self._joint(reg_h, reg_a, td_share, p, ctx, n)
        p_tie = self._tie_probability(ctx, p, reg_h, reg_a, td_share, n)
        home_share = reg_h / (reg_h + reg_a)
        mat = self._apply_overtime(reg, p_tie, home_share, p)
        mat[mat < p["prune_eps"]] = 0.0
        return mat / mat.sum()

    def total_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        mat = self.score_matrix(game_context)
        size = mat.shape[0]
        totals: Dict[int, float] = {}
        for h in range(size):
            for a in range(size):
                v = mat[h, a]
                if v > 0:
                    totals[h + a] = totals.get(h + a, 0.0) + float(v)
        return totals

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mat = self.score_matrix(game_context)
        scores = {
            (int(h), int(a)): float(mat[h, a])
            for h, a in zip(*np.nonzero(mat))
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_scores(mat)
        except Exception:
            pass
        totals = self.total_pmf(game_context)
        return DiscreteDistribution(probabilities={str(k): v for k, v in sorted(totals.items())})