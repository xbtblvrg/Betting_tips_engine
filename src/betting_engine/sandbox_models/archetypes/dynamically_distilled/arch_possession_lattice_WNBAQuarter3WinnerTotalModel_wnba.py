"""
Candidate model for WNBA | quarter_3 | winner_total.

Structure: a shared-possession compound scoring model.

    N        ~ discretised Gaussian possession count, shared by both teams.
               Possessions alternate, so the count is UNDER-dispersed relative
               to Poisson. Its spread is a parameter, not an assumption.
    Y_i      ~ categorical points-per-possession on {0,1,2,3,4}. It is
               exponentially tilted from a league-shape prior so that
               E[S_i] matches the anchored expectation exactly.
    S_i | N  = sum_{k=1..N} Y_ik, with the two teams conditionally
               independent given N.

What follows from this structure (law of total variance):
    Var(S_i)       = E[N] Var(Y_i) + Var(N) E[Y_i]^2
    Cov(S_h, S_a)  = Var(N) E[Y_h] E[Y_a]   (>0: widens totals, narrows margin)

The joint score lattice is built by exact convolution. Winner, tie and total
probabilities are all read off the same matrix, so they are internally
consistent.

Honest caveat: this is a cold-start structural prior with zero settled bets
behind it. The ROI and Brier "benchmarks" cited for other WNBA segments are
not a realistic expectation for this market. Treat the outputs as
probabilities to be validated out-of-sample, not as guaranteed edge. Every
prior below is an exposed, documented parameter, meant to be re-estimated
once settled data exists.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - resolved by execution scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")


class WNBAQuarter3WinnerTotalModel(BaseCandidateModel):
    # ---------------- Empirical anchors (segment baseline means) -------------
    ANCHOR_HOME = 20.00
    ANCHOR_AWAY = 18.75

    # ---------------- Structural priors (re-estimate from settled data) ------
    # League-shape prior for points per possession on {0,1,2,3,4}.
    # Its mean is ~0.985 PPP, about 19.7 possessions per team per 10-minute
    # quarter at the anchors.
    PPP_SHAPE = np.array([0.545, 0.055, 0.285, 0.100, 0.015])
    # Per-quarter possession SD at the reference pace. This is under-dispersed
    # vs Poisson.
    POSS_SD_REF = 2.0
    # Fraction of a full 40-minute game played in one quarter.
    QUARTER_FRACTION = 10.0 / 40.0
    # Per-quarter home advantage prior, in points.
    HCA_QUARTER_PRIOR = 0.4
    # SD of the true matchup margin per quarter, and the noise in the supplied
    # margin. Together these set the shrinkage weight.
    MARGIN_SIGNAL_SD = 2.5
    MARGIN_INPUT_NOISE_SD = 1.0
    # Residual weight on attack/defense ratings. This is kept small because
    # home_expected and away_expected are assumed to already reflect them.
    RATING_RESIDUAL_WEIGHT = 0.25
    # Maximum efficiency effect of rest asymmetry, applied smoothly via tanh.
    REST_EFFECT_MAX = 0.012
    REST_SCALE_DAYS = 2.0
    # Lattice tail tolerance.
    TAIL_EPS = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_3_1107_231122",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_3",
        family: str = "winner_total",
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(ctx: Dict[str, Any], keys, default: Optional[float] = None) -> Optional[float]:
        for k in keys:
            v = ctx.get(k)
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f):
                return f
        return default

    @staticmethod
    def _team_rating(ctx: Dict[str, Any], side: str, kind: str) -> float:
        """
        Return the log multiplicative rating (0 means neutral).

        Accepts either nested dicts (ctx[side][kind]) or flat keys such as
        'home_attack'. A positive value is read as a multiplier; anything
        else is ignored.
        """
        val = None
        team = ctx.get(side)
        if isinstance(team, dict):
            val = team.get(kind, team.get(kind[:3]))
        if val is None:
            val = ctx.get(f"{side}_{kind}")
        try:
            f = float(val)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(f) or f <= 0.0:
            return 0.0
        return math.log(f)

    # --------------------------------------------------------- PPP tilting
    def _tilted_ppp(self, target_mean: float) -> np.ndarray:
        """Exponential tilt of PPP_SHAPE to hit target_mean exactly."""
        pts = np.arange(len(self.PPP_SHAPE), dtype=float)
        base = self.PPP_SHAPE / self.PPP_SHAPE.sum()
        hi_feasible = pts[-1] - 1e-9
        target = min(max(target_mean, 1e-6), hi_feasible)

        def mean_at(theta: float) -> float:
            w = base * np.exp(theta * (pts - pts.mean()))
            w /= w.sum()
            return float(w @ pts) - target

        lo, hi = -5.0, 5.0
        while mean_at(lo) > 0:
            lo *= 2.0
        while mean_at(hi) < 0:
            hi *= 2.0
        theta = brentq(mean_at, lo, hi, xtol=1e-12)
        w = base * np.exp(theta * (pts - pts.mean()))
        return w / w.sum()

    # ------------------------------------------------- possession pmf
    def _possession_pmf(self, mu: float, sd: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = max(sd, 1e-3)
        z = math.sqrt(-2.0 * math.log(self.TAIL_EPS))
        n_lo = max(0, int(math.floor(mu - z * sd)))
        n_hi = int(math.ceil(mu + z * sd))
        n = np.arange(n_lo, n_hi + 1)
        pmf = np.exp(-0.5 * ((n - mu) / sd) ** 2)
        pmf /= pmf.sum()
        return n, pmf

    @staticmethod
    def _conv_powers(y: np.ndarray, n_max: int) -> np.ndarray:
        """Row n holds the pmf of the sum of n iid draws of y."""
        width = (len(y) - 1) * n_max + 1
        out = np.zeros((n_max + 1, width))
        out[0, 0] = 1.0
        cur = np.array([1.0])
        for n in range(1, n_max + 1):
            cur = np.convolve(cur, y)
            out[n, : len(cur)] = cur
        return out

    # ---------------------------------------------------------- core
    def _resolve_inputs(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        h = self._num(ctx, ["home_expected", "home_q3_expected"], self.ANCHOR_HOME)
        a = self._num(ctx, ["away_expected", "away_q3_expected"], self.ANCHOR_AWAY)
        h = h if h > 0 else self.ANCHOR_HOME
        a = a if a > 0 else self.ANCHOR_AWAY
        total = h + a

        # 1) Shrink the supplied margin toward the per-quarter HCA prior.
        #    The weight is a signal-to-noise ratio, not a clamp, and the total
        #    is preserved.
        w = self.MARGIN_SIGNAL_SD ** 2 / (
            self.MARGIN_SIGNAL_SD ** 2 + self.MARGIN_INPUT_NOISE_SD ** 2
        )
        neutral = ctx.get("neutral_site") or ctx.get("is_neutral")
        hca = 0.0 if neutral else self.HCA_QUARTER_PRIOR
        margin = hca + w * ((h - a) - hca)

        # 2) Residual matchup tilt from attack/defense ratings, on the log
        #    scale. This is zero-sum, so it moves the margin but not the total.
        r_h = self._team_rating(ctx, "home", "attack") - self._team_rating(ctx, "away", "defense")
        r_a = self._team_rating(ctx, "away", "attack") - self._team_rating(ctx, "home", "defense")
        tilt = self.RATING_RESIDUAL_WEIGHT * 0.5 * (r_h - r_a)
        margin += total * 0.5 * math.tanh(tilt)

        # 3) Rest asymmetry: a smooth efficiency effect.
        rh = self._num(ctx, ["home_rest_days"], None)
        ra = self._num(ctx, ["away_rest_days"], None)
        if rh is not None and ra is not None:
            rest = self.REST_EFFECT_MAX * math.tanh((rh - ra) / self.REST_SCALE_DAYS)
            margin += total * rest

        home_mu = 0.5 * (total + margin)
        away_mu = 0.5 * (total - margin)

        # 4) Possessions. Use explicit pace if supplied (per 40 minutes);
        #    otherwise imply it from the total and the league PPP shape.
        base_ppp = float(self.PPP_SHAPE @ np.arange(len(self.PPP_SHAPE))) / self.PPP_SHAPE.sum()
        ph = self._num(ctx, ["home_pace"], None)
        pa = self._num(ctx, ["away_pace"], None)
        pace = self._num(ctx, ["pace", "tempo", "expected_pace"], None)
        if pace is None and ph is not None and pa is not None:
            pace = 0.5 * (ph + pa)
        if pace is not None and pace > 0:
            mu_n = pace * self.QUARTER_FRACTION
        else:
            mu_n = total / (2.0 * base_ppp)
        ref_mu = (self.ANCHOR_HOME + self.ANCHOR_AWAY) / (2.0 * base_ppp)
        sd_n = self.POSS_SD_REF * math.sqrt(mu_n / ref_mu)

        return {
            "home_mu": max(home_mu, 1e-3),
            "away_mu": max(away_mu, 1e-3),
            "mu_n": mu_n,
            "sd_n": sd_n,
            "shrink_w": w,
        }

    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        p = self._resolve_inputs(ctx)
        n_vals, n_pmf = self._possession_pmf(p["mu_n"], p["sd_n"])
        e_n = float(n_vals @ n_pmf)

        # Match the means exactly: E[S] = E[N] * E[Y].
        y_h = self._tilted_ppp(p["home_mu"] / e_n)
        y_a = self._tilted_ppp(p["away_mu"] / e_n)

        n_max = int(n_vals[-1])
        pow_h = self._conv_powers(y_h, n_max)
        pow_a = self._conv_powers(y_a, n_max)

        # Joint pmf: sum_n P(N=n) * outer(S_h|n, S_a|n).
        A = pow_h[n_vals] * n_pmf[:, None]
        M = A.T @ pow_a[n_vals]

        # Trim negligible tails.
        keep_h = np.where(M.sum(axis=1) > self.TAIL_EPS)[0]
        keep_a = np.where(M.sum(axis=0) > self.TAIL_EPS)[0]
        M = M[: keep_h.max() + 1, : keep_a.max() + 1]
        M = M / M.sum()

        # Diagnostics.
        hh = np.arange(M.shape[0])[:, None]
        aa = np.arange(M.shape[1])[None, :]
        mh, ma = float((M * hh).sum()), float((M * aa).sum())
        vh = float((M * (hh - mh) ** 2).sum())
        va = float((M * (aa - ma) ** 2).sum())
        cov = float((M * (hh - mh) * (aa - ma)).sum())
        self.last_diagnostics = {
            "mean_home": mh, "mean_away": ma,
            "disp_home": vh / mh, "disp_away": va / ma,
            "corr": cov / math.sqrt(vh * va),
            "p_home": float(np.tril(M, -1).sum()),
            "p_tie": float(np.trace(M)),
            "p_away": float(np.triu(M, 1).sum()),
            "exp_possessions": e_n,
            "margin_shrink_weight": p["shrink_w"],
        }
        return M

    # ------------------------------------------------------------ public API
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        M = self.score_matrix(ctx)

        scores = {
            (int(i), int(j)): float(M[i, j])
            for i, j in zip(*np.nonzero(M > 1e-12))
        }
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_scores(M)
        except Exception:
            pass

        d = self.last_diagnostics
        try:
            return DiscreteDistribution.from_probabilities(d["p_home"], d["p_tie"], d["p_away"])
        except Exception:
            return DiscreteDistribution(
                probabilities={"home": d["p_home"], "draw": d["p_tie"], "away": d["p_away"]}
            )