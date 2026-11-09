"""
Candidate model for NBA | quarter_3 | exact_total.

Structure
---------
The third-quarter total is a compound sum over possessions:

    T = H + A,   H = sum_{i=1..N_h} X_i^h,   A = sum_{j=1..N_a} X_j^a

* Pace (shared). Both teams draw from a common possession budget N. It is
  modelled as a discretised Gamma whose mean comes from both teams' paces and
  rest. Its coefficient of variation is the single dispersion knob that the
  settled history refits (method of moments, see `fit_dispersion`).
* Possession parity. N_h = N + e_h and N_a = N + e_a with e in {-1, 0, +1}.
  This covers end-of-quarter 2-for-1 sequences and who has the first or last
  possession.
* Possession outcome. X is categorical on {0, 1, 2, 3, 4}. A league-shape
  reference vector q is exponentially tilted, p_k ∝ q_k * exp(theta * k). The
  tilt is the minimum-KL departure from q that matches the team's required
  points per possession exactly, so E[T] equals home_expected + away_expected
  by construction.
* Game-level shooting variance. Each team gets an independent log-efficiency
  shock, integrated by Gauss-Hermite quadrature.

Conditional on n possessions, a team's score PMF is IFFT(FFT(p)^n), which is
the PGF composition G_N(G_X(z)) evaluated on the unit circle. The shared N
produces positive home/away covariance, so the joint score lattice is not an
outer product of independent marginals.

Implied total variance:
    Var(T) ≈ E[N]·(Var X_h + Var X_a) + Var(N)·(E X_h + E X_a)^2 + shock terms.
Var/Mean therefore emerges from the mechanics, typically around 1.3 to 1.6.
It is not set to 1.0.

Honest caveat: this is a cold-start prior with no settled bets behind it.
Nothing here guarantees any particular ROI. The domain priors below are
documented and overridable. Refit `pace_cv` with `fit_dispersion()` once
settled Q3 totals accumulate, and validate out-of-sample before staking.
"""

import math
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies across engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded primitive


class NBAQuarter3ExactTotalModel(BaseCandidateModel):
    # ---- Domain priors (documented, overridable; not clamps) ----------------
    # NBA league pace ≈ 99-101 possessions/team/48 min -> ≈ 25 per 12-min quarter.
    DEFAULT_LEAGUE_PACE_48 = 100.0
    QUARTER_MINUTES = 12.0
    GAME_MINUTES = 48.0
    # Reference possession outcome shape (0,1,2,3,4+ points). Shares come from
    # league play-by-play: empty possessions, single-FT trips/split pairs, 2s
    # (incl. 2-FT trips), 3s, and-ones/4-pt plays. Only the *shape* matters;
    # the level is set exactly by the exponential tilt.
    REFERENCE_OUTCOME_SHAPE = (0.47, 0.06, 0.30, 0.15, 0.02)
    # Shared pace CV for a single quarter (std of possessions ≈ 1.5 on 25).
    DEFAULT_PACE_CV = 0.06
    # Probability that a team gets one extra or one fewer possession than the
    # shared count (2-for-1 dynamics, first/last possession asymmetry).
    DEFAULT_PARITY_RATE = 0.40
    # Game-level log-efficiency shock (hot/cold shooting within a quarter).
    DEFAULT_EFF_SHOCK_SD = 0.04
    # Efficiency elasticity to rest advantage, saturating via tanh.
    DEFAULT_REST_ELASTICITY = 0.012
    DEFAULT_REST_SATURATION_DAYS = 3.0
    # Q3 pace relative to full-game average (starters return, slightly slower).
    DEFAULT_Q3_PACE_RATIO = 0.99
    # Empirical anchors for this segment.
    BASELINE_HOME = 28.00
    BASELINE_AWAY = 27.00

    _TAIL_EPS = 1e-10
    _GH_NODES = 7

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_3_1107_154837",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_3",
        family: str = "exact_total",
        pace_cv: Optional[float] = None,
        parity_rate: Optional[float] = None,
        eff_shock_sd: Optional[float] = None,
        rest_elasticity: Optional[float] = None,
        q3_pace_ratio: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.pace_cv = float(self.DEFAULT_PACE_CV if pace_cv is None else pace_cv)
        self.parity_rate = float(self.DEFAULT_PARITY_RATE if parity_rate is None else parity_rate)
        self.eff_shock_sd = float(self.DEFAULT_EFF_SHOCK_SD if eff_shock_sd is None else eff_shock_sd)
        self.rest_elasticity = float(
            self.DEFAULT_REST_ELASTICITY if rest_elasticity is None else rest_elasticity
        )
        self.q3_pace_ratio = float(self.DEFAULT_Q3_PACE_RATIO if q3_pace_ratio is None else q3_pace_ratio)

        q = np.asarray(self.REFERENCE_OUTCOME_SHAPE, dtype=float)
        self._q = q / q.sum()
        self._k = np.arange(self._q.size, dtype=float)
        self._gh_x, self._gh_w = np.polynomial.hermite_e.hermegauss(self._GH_NODES)
        self._gh_w = self._gh_w / self._gh_w.sum()

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(ctx: Dict[str, Any], key: str, default: Optional[float] = None) -> Optional[float]:
        v = ctx.get(key, default)
        try:
            v = float(v)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _team_block(ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        blk = ctx.get(side)
        return blk if isinstance(blk, dict) else {}

    def _rating(self, blk: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            v = self._num(blk, k)
            if v is not None and v > 0:
                return v
        return None

    # ------------------------------------------------------ expected points
    def _quarter_expectations(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._num(ctx, "home_expected")
        a = self._num(ctx, "away_expected")

        if h is None or a is None or h <= 0 or a <= 0:
            # Build from attack/defense multiplicative ratings around 1.0.
            hb, ab = self._team_block(ctx, "home"), self._team_block(ctx, "away")
            h_att = self._rating(hb, "attack", "offense", "off_rating") or 1.0
            h_def = self._rating(hb, "defense", "def_rating") or 1.0
            a_att = self._rating(ab, "attack", "offense", "off_rating") or 1.0
            a_def = self._rating(ab, "defense", "def_rating") or 1.0
            # Ratings given on a per-100 scale are normalised to multipliers.
            norm = lambda r: r / 110.0 if r > 10 else r
            h = self.BASELINE_HOME * norm(h_att) * norm(a_def)
            a = self.BASELINE_AWAY * norm(a_att) * norm(h_def)

        # Full-game expectations passed in are mapped to one quarter by time share.
        full_game_threshold = 2.0 * (self.BASELINE_HOME + self.BASELINE_AWAY) / 2.0 * 1.5
        share = self.QUARTER_MINUTES / self.GAME_MINUTES
        if h > full_game_threshold:
            h *= share
        if a > full_game_threshold:
            a *= share

        # Rest advantage acts on efficiency, saturating smoothly with tanh.
        hr = self._num(ctx, "home_rest_days")
        ar = self._num(ctx, "away_rest_days")
        if hr is not None and ar is not None:
            s = self.DEFAULT_REST_SATURATION_DAYS
            adv = math.tanh((hr - ar) / s) * s
            h *= math.exp(self.rest_elasticity * adv)
            a *= math.exp(-self.rest_elasticity * adv)
        return float(h), float(a)

    # ------------------------------------------------------------ possessions
    def _possession_mean(self, ctx: Dict[str, Any]) -> float:
        league_pace = self._num(ctx, "league_pace", self.DEFAULT_LEAGUE_PACE_48)
        hb, ab = self._team_block(ctx, "home"), self._team_block(ctx, "away")
        hp = self._num(ctx, "home_pace") or self._rating(hb, "pace", "tempo") or league_pace
        ap = self._num(ctx, "away_pace") or self._rating(ab, "pace", "tempo") or league_pace
        # Log-5 style pace interaction: game pace = hp * ap / league.
        game_pace_48 = hp * ap / league_pace
        mu = game_pace_48 * self.QUARTER_MINUTES / self.GAME_MINUTES * self.q3_pace_ratio

        hr = self._num(ctx, "home_rest_days")
        ar = self._num(ctx, "away_rest_days")
        if hr is not None and ar is not None:
            # Fatigue (back-to-backs) slows transition play slightly.
            fatigue = math.exp(-min(hr, ar)) - math.exp(-self.DEFAULT_REST_SATURATION_DAYS)
            mu *= math.exp(-self.rest_elasticity * fatigue)
        return float(mu)

    def _possession_pmf(self, mu: float) -> Tuple[np.ndarray, np.ndarray]:
        cv = max(self.pace_cv, 1e-6)
        shape = 1.0 / (cv * cv)
        dist = stats.gamma(a=shape, scale=mu / shape)
        lo = max(0, int(math.floor(dist.ppf(self._TAIL_EPS))) - 1)
        hi = int(math.ceil(dist.ppf(1.0 - self._TAIL_EPS))) + 1
        n = np.arange(lo, hi + 1)
        edges = np.concatenate([[n[0] - 0.5], n + 0.5])
        w = np.diff(dist.cdf(np.clip(edges, 0.0, None)))
        w = np.clip(w, 0.0, None)
        return n, w / w.sum()

    # --------------------------------------------------------------- outcomes
    def _tilted_outcome(self, ppp_target: float) -> np.ndarray:
        q, k = self._q, self._k
        kmax = k[-1]
        target = min(max(ppp_target, 1e-6), kmax * (1 - 1e-9))

        def mean_gap(theta: float) -> float:
            lw = np.log(q) + theta * k
            w = np.exp(lw - lw.max())
            return float((w * k).sum() / w.sum() - target)

        lo, hi = -10.0, 10.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        lw = np.log(q) + theta * k
        p = np.exp(lw - lw.max())
        return p / p.sum()

    def _team_score_given_n(
        self, expected_pts: float, mu_poss: float, n_support: np.ndarray, L: int
    ) -> np.ndarray:
        """Return matrix S[i, s] = P(team scores s | shared count n_support[i]),
        integrating parity offsets and efficiency shocks."""
        r = self.parity_rate
        offsets = np.array([-1, 0, 1])
        off_w = np.array([r / 2.0, 1.0 - r, r / 2.0])

        sd = self.eff_shock_sd
        base_ppp = expected_pts / mu_poss
        out = np.zeros((n_support.size, L))
        for z, gw in zip(self._gh_x, self._gh_w):
            # Mean-preserving lognormal shock on points per possession.
            ppp = base_ppp * math.exp(sd * z - 0.5 * sd * sd)
            p = self._tilted_outcome(ppp)
            F = np.fft.rfft(np.pad(p, (0, L - p.size)))
            for e, ew in zip(offsets, off_w):
                n_eff = np.clip(n_support + e, 0, None)
                pmf = np.fft.irfft(F[None, :] ** n_eff[:, None], n=L, axis=1)
                out += gw * ew * pmf
        out = np.clip(out, 0.0, None)
        out /= out.sum(axis=1, keepdims=True)
        return out

    # ------------------------------------------------------------- core model
    def _joint_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        h_exp, a_exp = self._quarter_expectations(ctx)
        mu = self._possession_mean(ctx)
        n, w = self._possession_pmf(mu)

        max_points = int(self._k[-1]) * (int(n.max()) + 1)
        L = 1 << int(math.ceil(math.log2(max_points + 1)))  # no circular aliasing

        # Normalise by realised E[N] so tilted PPP reproduces targets exactly.
        mu_eff = float((n * w).sum())
        S_h = self._team_score_given_n(h_exp, mu_eff, n, L)
        S_a = self._team_score_given_n(a_exp, mu_eff, n, L)

        # Shared pace -> mixture of outer products (positive covariance).
        joint = np.einsum("i,ih,ia->ha", w, S_h, S_a, optimize=True)

        # Trim negligible lattice tails, then renormalise.
        mh, ma = joint.sum(axis=1), joint.sum(axis=0)
        hmax = int(np.searchsorted(np.cumsum(mh), 1.0 - self._TAIL_EPS)) + 1
        amax = int(np.searchsorted(np.cumsum(ma), 1.0 - self._TAIL_EPS)) + 1
        joint = joint[: hmax + 1, : amax + 1]
        return joint / joint.sum()

    def total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        joint = self._joint_matrix(game_context)
        H, A = joint.shape
        tot = np.zeros(H + A - 1)
        for h in range(H):
            tot[h : h + A] += joint[h]
        return tot / tot.sum()

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        joint = self._joint_matrix(ctx)

        # Preferred: score lattice (engine derives exact totals, overs, spreads).
        try:
            return DiscreteDistribution.from_scores(joint)
        except Exception:
            pass
        try:
            scores = {
                (int(h), int(a)): float(joint[h, a])
                for h, a in zip(*np.nonzero(joint > 0))
            }
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        # Fallback: explicit exact-total PMF.
        H, A = joint.shape
        tot = np.zeros(H + A - 1)
        for h in range(H):
            tot[h : h + A] += joint[h]
        tot /= tot.sum()
        return DiscreteDistribution(
            probabilities={str(t): float(p) for t, p in enumerate(tot) if p > 0}
        )

    # -------------------------------------------------------------- refitting
    def implied_moments(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        pmf = self.total_pmf(game_context)
        t = np.arange(pmf.size)
        m = float((t * pmf).sum())
        v = float(((t - m) ** 2 * pmf).sum())
        joint = self._joint_matrix(game_context)
        hh = np.arange(joint.shape[0])[:, None]
        aa = np.arange(joint.shape[1])[None, :]
        eh, ea = (joint * hh).sum(), (joint * aa).sum()
        cov = float((joint * (hh - eh) * (aa - ea)).sum())
        return {"mean": m, "var": v, "dispersion_index": v / m if m > 0 else float("nan"),
                "home_away_cov": cov}

    def fit_dispersion(
        self,
        settled: Iterable[Tuple[Dict[str, Any], float]],
        min_samples: int = 30,
    ) -> float:
        """Method-of-moments refit of `pace_cv` from (context, realised Q3 total)
        pairs. Matches the mean squared standardised residual to 1. Returns the
        fitted CV. The prior is left unchanged when data are insufficient."""
        data = [(c, float(t)) for c, t in settled if t is not None and math.isfinite(float(t))]
        if len(data) < min_samples:
            return self.pace_cv

        def gap(log_cv: float) -> float:
            self.pace_cv = math.exp(log_cv)
            z2 = []
            for c, t in data:
                mom = self.implied_moments(c)
                z2.append((t - mom["mean"]) ** 2 / mom["var"])
            return float(np.mean(z2)) - 1.0

        original = self.pace_cv
        lo, hi = math.log(1e-3), math.log(0.5)
        try:
            g_lo, g_hi = gap(lo), gap(hi)
            if g_lo <= 0:
                self.pace_cv = math.exp(lo)  # data tighter than mechanics floor
            elif g_hi >= 0:
                self.pace_cv = math.exp(hi)  # extra variance beyond pace; revisit shock sd
            else:
                self.pace_cv = math.exp(optimize.brentq(gap, lo, hi, xtol=1e-4))
        except Exception:
            self.pace_cv = original
        return self.pace_cv