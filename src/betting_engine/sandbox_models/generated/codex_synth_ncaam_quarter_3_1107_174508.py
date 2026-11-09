"""
Candidate model: NCAAM | quarter_3 | exact_total

Settlement caveat: NCAAM men's games use two 20-minute halves, so
"quarter_3" must be a synthetic 10-minute window. This model assumes it
covers 20:00 to 10:00 of the second half. That window is mostly before the
10-foul double bonus, so it has a lower free-throw share than a full game.
Check the settlement definition before going live. If the window is
different, override the scoring prior through game_context["q3_point_mix"].

Model structure
---------------
Points arrive in lumps (0, 1, 2, 3, 4 per possession), and both teams share
one possession count set by tempo. So the segment is modelled as a compound
process:

    M            : possessions per team in the window (shared by both teams)
    Y_h, Y_a     : points per possession, drawn from a categorical on {0..4}
    H = sum_{i<=M} Y_h,i ,   A = sum_{i<=M} Y_a,i
    Var(T) = E[M]*(Var Y_h + Var Y_a) + Var(M)*(E Y_h + E Y_a)^2

Possession count M: possessions form a renewal process in game time. For a
window of length t with mean possession length m and length CV c,
Var(count) ~ (t/m)*c^2. Uncertainty about the pace itself adds
(cv_pace*mu)^2. The result is UNDER-dispersed relative to Poisson, so the
pmf is a discretised normal on the integers, not a NegBin.

Points per possession: start from a domain prior on the NCAA scoring mix
for this window. Then apply the minimum-KL (exponential tilt) adjustment
that hits each team's required points per possession exactly:
    p_k ∝ prior_k * exp(theta * k)
This is the least-informative change to the prior that matches the given
mean. Nothing is clamped.

The joint (H, A) lattice is
    P(h, a) = sum_m P(M = m) * Y_h^{*m}(h) * Y_a^{*m}(a)
This builds in the positive home/away covariance from the shared pace and
the lattice lumpiness that a Poisson exact-total curve misses.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally preloaded; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # noqa: N816


class NCAAMQuarter3ExactTotalModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Domain priors. Every value can be overridden through game_context.
    # These are structural descriptions of the sport, not output clamps.
    # ------------------------------------------------------------------ #
    WINDOW_MINUTES = 10.0
    GAME_MINUTES = 40.0
    BASELINE_HOME = 20.00          # empirical segment mean (home)
    BASELINE_AWAY = 18.75          # empirical segment mean (away)
    PRIOR_PACE_40 = 68.0           # possessions per team per 40 minutes, D1 typical
    PRIOR_POSS_LENGTH_CV = 0.55    # CV of possession duration (renewal process)
    PRIOR_PACE_CV = 0.07           # uncertainty in the game-specific pace
    # Points-per-possession prior for the early-second-half window, before the
    # double bonus. Index = points scored on the possession.
    PRIOR_POINT_MIX = (0.495, 0.030, 0.335, 0.130, 0.010)
    REST_ELASTICITY = 0.010        # log-efficiency change per rest-day difference (fallback path)
    TAIL_EPS = 1e-12               # numerical truncation of the M support only

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_3_1107_174508",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_3",
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

    # ------------------------------------------------------------------ #
    # Context helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._num(ctx[k])
                if v is not None:
                    return v
        return None

    def _side_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        nested = ctx.get(side)
        if isinstance(nested, dict):
            v = self._num(nested.get(kind))
            if v is not None:
                return v
        return self._get(ctx, f"{side}_{kind}")

    # ------------------------------------------------------------------ #
    # Segment means
    # ------------------------------------------------------------------ #
    def _segment_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """
        If home_expected / away_expected are supplied, they are taken to
        already include matchup, venue and rest effects, and are used as
        given. Otherwise the empirical baselines are scaled by multiplicative
        attack/defense exposure and a log-linear rest adjustment.
        """
        h = self._get(ctx, "home_expected")
        a = self._get(ctx, "away_expected")
        if h is not None and a is not None and h > 0 and a > 0:
            return h, a

        h = h if (h is not None and h > 0) else self.BASELINE_HOME
        a = a if (a is not None and a > 0) else self.BASELINE_AWAY

        ha = self._side_rating(ctx, "home", "attack")
        hd = self._side_rating(ctx, "home", "defense")
        aa = self._side_rating(ctx, "away", "attack")
        ad = self._side_rating(ctx, "away", "defense")
        if ha is not None and ha > 0 and ad is not None and ad > 0:
            h *= ha * ad
        if aa is not None and aa > 0 and hd is not None and hd > 0:
            a *= aa * hd

        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        if hr is not None and ar is not None:
            elast = self._get(ctx, "rest_elasticity")
            elast = self.REST_ELASTICITY if elast is None else elast
            diff = math.log1p(max(hr, 0.0)) - math.log1p(max(ar, 0.0))
            h *= math.exp(elast * diff)
            a *= math.exp(-elast * diff)
        return h, a

    # ------------------------------------------------------------------ #
    # Possession process
    # ------------------------------------------------------------------ #
    def _possession_pmf(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, float]:
        pace = self._get(ctx, "pace", "tempo", "expected_pace")
        if pace is None:
            hp = self._get(ctx, "home_pace", "home_tempo")
            ap = self._get(ctx, "away_pace", "away_tempo")
            if hp is not None and ap is not None and hp > 0 and ap > 0:
                pace = math.sqrt(hp * ap)
        if pace is None or pace <= 0:
            pace = self.PRIOR_PACE_40

        window = self._get(ctx, "segment_minutes")
        window = self.WINDOW_MINUTES if window is None or window <= 0 else window
        mu = pace * window / self.GAME_MINUTES

        c_len = self._get(ctx, "possession_length_cv")
        c_len = self.PRIOR_POSS_LENGTH_CV if c_len is None or c_len < 0 else c_len
        c_pace = self._get(ctx, "pace_cv")
        c_pace = self.PRIOR_PACE_CV if c_pace is None or c_pace < 0 else c_pace

        var = mu * c_len ** 2 + (c_pace * mu) ** 2
        sd = math.sqrt(max(var, 1e-9))

        # Discretised normal on the non-negative integers, with continuity
        # correction. The support covers all but TAIL_EPS of the mass.
        z = stats.norm.isf(self.TAIL_EPS)
        hi = int(math.ceil(mu + z * sd)) + 1
        m = np.arange(0, hi + 1, dtype=float)
        cdf_hi = stats.norm.cdf(m + 0.5, loc=mu, scale=sd)
        cdf_lo = stats.norm.cdf(m - 0.5, loc=mu, scale=sd)
        cdf_lo[0] = 0.0
        pmf = np.clip(cdf_hi - cdf_lo, 0.0, None)
        pmf /= pmf.sum()
        mean_m = float((m * pmf).sum())
        return pmf, mean_m

    # ------------------------------------------------------------------ #
    # Points-per-possession mix (minimum-KL tilt of the domain prior)
    # ------------------------------------------------------------------ #
    def _prior_mix(self, ctx: Dict[str, Any]) -> np.ndarray:
        raw = ctx.get("q3_point_mix")
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            try:
                arr = np.asarray([float(x) for x in raw], dtype=float)
                if np.all(arr >= 0) and arr.sum() > 0:
                    return arr / arr.sum()
            except (TypeError, ValueError):
                pass
        arr = np.asarray(self.PRIOR_POINT_MIX, dtype=float)
        return arr / arr.sum()

    @staticmethod
    def _tilt(prior: np.ndarray, target_mean: float) -> np.ndarray:
        k = np.arange(prior.size, dtype=float)
        support_max = float(k[prior > 0].max())
        support_min = float(k[prior > 0].min())
        # The requested mean must lie strictly inside the support. If it does
        # not, the request is infeasible; take the limiting degenerate mix.
        if target_mean >= support_max:
            out = np.zeros_like(prior)
            out[int(support_max)] = 1.0
            return out
        if target_mean <= support_min:
            out = np.zeros_like(prior)
            out[int(support_min)] = 1.0
            return out

        logp = np.where(prior > 0, np.log(np.where(prior > 0, prior, 1.0)), -np.inf)

        def mean_at(theta: float) -> float:
            w = logp + theta * k
            w = np.exp(w - w.max())
            w /= w.sum()
            return float((w * k).sum()) - target_mean

        lo, hi = -1.0, 1.0
        while mean_at(lo) > 0:
            lo *= 2.0
        while mean_at(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_at, lo, hi, xtol=1e-14, maxiter=500)
        w = np.exp(logp + theta * k - (logp + theta * k).max())
        return w / w.sum()

    # ------------------------------------------------------------------ #
    # Compound lattice
    # ------------------------------------------------------------------ #
    def _joint_lattice(self, ctx: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a = self._segment_means(ctx)
        m_pmf, mean_m = self._possession_pmf(ctx)

        prior = self._prior_mix(ctx)
        # Possessions are shared, so per-possession efficiency is fixed by
        # the segment mean divided by expected possessions.
        y_h = self._tilt(prior, mu_h / mean_m)
        y_a = self._tilt(prior, mu_a / mean_m)

        max_m = m_pmf.size - 1
        kmax = y_h.size - 1
        size = max_m * kmax + 1
        joint = np.zeros((size, size), dtype=float)

        conv_h = np.zeros(size)
        conv_a = np.zeros(size)
        conv_h[0] = 1.0
        conv_a[0] = 1.0
        for m in range(0, max_m + 1):
            if m > 0:
                conv_h = np.convolve(conv_h, y_h)[:size]
                conv_a = np.convolve(conv_a, y_a)[:size]
            w = m_pmf[m]
            if w > 0.0:
                joint += w * np.outer(conv_h, conv_a)

        total = joint.sum()
        if total > 0:
            joint /= total
        return joint

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_total_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        joint = self._joint_lattice(game_context or {})
        n = joint.shape[0]
        flipped = np.fliplr(joint)
        totals = {}
        for t in range(0, 2 * n - 1):
            p = float(np.trace(flipped, offset=(n - 1) - t))
            if p > 0.0:
                totals[t] = p
        s = sum(totals.values())
        return {t: p / s for t, p in totals.items()} if s > 0 else totals

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        joint = self._joint_lattice(ctx)

        # Drop numerically empty cells only (well below any price resolution),
        # then renormalise so the mass is exact.
        floor = joint.max() * 1e-14
        hs, as_ = np.nonzero(joint > floor)
        mass = joint[hs, as_]
        mass = mass / mass.sum()
        scores = {(int(h), int(a)): float(p) for h, a, p in zip(hs, as_, mass)}
        return DiscreteDistribution.from_scores(scores)

    # Diagnostics, useful for calibration audits.
    def describe(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        ctx = game_context or {}
        pmf = self.predict_total_pmf(ctx)
        t = np.fromiter(pmf.keys(), dtype=float)
        p = np.fromiter(pmf.values(), dtype=float)
        mean = float((t * p).sum())
        var = float(((t - mean) ** 2 * p).sum())
        joint = self._joint_lattice(ctx)
        idx = np.arange(joint.shape[0], dtype=float)
        ph, pa = joint.sum(axis=1), joint.sum(axis=0)
        eh, ea = float((idx * ph).sum()), float((idx * pa).sum())
        cov = float((np.outer(idx - eh, idx - ea) * joint).sum())
        return {
            "mean_total": mean,
            "var_total": var,
            "dispersion_index": var / mean if mean > 0 else float("nan"),
            "home_mean": eh,
            "away_mean": ea,
            "home_away_cov": cov,
        }