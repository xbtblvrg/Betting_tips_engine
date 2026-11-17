"""
NBA | half_1 | home_total candidate model.

Structure: possession-lattice compound model.

    Home 1H points  S_h = sum_{i=1..N} X_i,   Away 1H points  S_a = sum_{j=1..N} Y_j

  * N (1H possessions) is shared by both teams. Possessions alternate, so the two
    counts differ by at most ~1. This shared N is the only coupling between the
    teams, and it produces the positive pace correlation in the joint score lattice.
  * X_i are i.i.d. points-per-possession on the {0,1,2,3,4} lattice. The pmf is an
    exponential tilt (max-entropy projection) of league outcome shares, solved so
    E[X] equals the situational efficiency.
  * Game-level shooting variance is a mean-preserving lognormal shock on
    efficiency, integrated by Gauss-Hermite quadrature.

  Var(S) = E[N]Var(X) + E[X]^2 Var(N) + mu^2 * tau^2, which is fully decomposed.

Note on the reported dispersion index (0.187): for raw 1H team points, the
Bernoulli/lattice floor alone (E[N]*Var(X) ~ 50*1.3) gives Var/Mean ~ 1.2, so 0.187
cannot be a raw-points moment. It is most likely a residual or line-relative
statistic. This model does not force phi=0.187. Instead, `fit()` estimates the
efficiency-shock scale tau, the possession dispersion, and the mean bias by maximum
likelihood on historical 1H home points. All structural values below are
overridable priors (constructor `params=` or `fit()`), not clamps.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally provided in execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


_DEFAULT_PARAMS: Dict[str, Any] = {
    # Empirical anchors (segment baseline means for 1H points)
    "home_baseline": 56.0,
    "away_baseline": 54.0,
    # Possession structure
    "league_pace": 99.5,            # full-game possessions per team
    "h1_possession_share": 0.505,   # 1H carries slightly more than half of regulation possessions
    "possession_sd_frac": 0.05,     # sd(N)/E[N]; pace is near-deterministic
    # Per-possession outcome shares (0,1,2,3,4 points), used only as the tilt base
    "outcome_base": [0.495, 0.045, 0.330, 0.127, 0.003],
    # Game-level efficiency shock (log-sd); refit by MLE in fit()
    "tau_eff": 0.055,
    "quad_nodes": 7,
    # Full-game to 1H conversion, used only if inputs look like full-game projections
    "h1_point_share": 0.508,
    "ot_rate": 0.06,
    "ot_points_per_team": 11.0,
    "full_game_detect_ratio": 1.6,
    # Matchup residual adjustments, shrunk because *_expected already embeds them
    "matchup_weight": 0.25,
    "league_rating": 114.0,
    # Rest: saturating fatigue curve, effect relative to reference rest
    "rest_beta": 0.025,
    "rest_scale": 1.0,
    "rest_ref": 2.0,
    "rest_def_share": 0.5,
    # Mean-bias multiplier, learned in fit()
    "mean_bias_home": 1.0,
    "mean_bias_away": 1.0,
    # Numerical tail tolerance for lattice truncation
    "tail_eps": 1e-11,
}


class NBAHalf1HomeTotalModel(BaseCandidateModel):
    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_202229",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family
        self.p: Dict[str, Any] = dict(_DEFAULT_PARAMS)
        if params:
            self.p.update(params)
        self._cache: Dict[Tuple, Tuple[np.ndarray, np.ndarray]] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _side(self, ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        s = ctx.get(side)
        return s if isinstance(s, dict) else {}

    def _rating_index(self, v: Optional[float]) -> Optional[float]:
        """Return a multiplicative index (~1.0). Accepts index-scale or rating-scale inputs."""
        if v is None or v <= 0:
            return None
        return v / self.p["league_rating"] if v > 3.0 else v

    def _rest_effect(self, r: Optional[float]) -> float:
        if r is None:
            return 0.0
        b, s, ref = self.p["rest_beta"], self.p["rest_scale"], self.p["rest_ref"]
        f = lambda d: b * (1.0 - math.exp(-max(d, 0.0) / s))
        return f(r) - f(ref)

    def _to_half(self, exp_pts: float, baseline: float) -> float:
        """Convert a full-game projection to 1H using regulation-only scoring structure."""
        if exp_pts > self.p["full_game_detect_ratio"] * baseline:
            regulation = exp_pts - self.p["ot_rate"] * self.p["ot_points_per_team"]
            return regulation * self.p["h1_point_share"]
        return exp_pts

    # ------------------------------------------------------------ situational means
    def _situational(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        p = self.p
        H, A = self._side(ctx, "home"), self._side(ctx, "away")

        mu_h = self._num(ctx.get("home_expected"), p["home_baseline"])
        mu_a = self._num(ctx.get("away_expected"), p["away_baseline"])
        mu_h = self._to_half(mu_h, p["home_baseline"])
        mu_a = self._to_half(mu_a, p["away_baseline"])

        # Residual matchup exposure (shrunk to avoid double counting)
        w = p["matchup_weight"]
        ha, hd = self._rating_index(self._num(H.get("attack"))), self._rating_index(self._num(H.get("defense")))
        aa, ad = self._rating_index(self._num(A.get("attack"))), self._rating_index(self._num(A.get("defense")))
        if ha is not None and ad is not None:
            mu_h *= math.exp(w * (math.log(ha) + math.log(ad)))
        if aa is not None and hd is not None:
            mu_a *= math.exp(w * (math.log(aa) + math.log(hd)))

        # Rest: own fatigue hurts offense; opponent fatigue partially hurts their defense
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        ds = p["rest_def_share"]
        mu_h *= math.exp(self._rest_effect(rh) - ds * self._rest_effect(ra))
        mu_a *= math.exp(self._rest_effect(ra) - ds * self._rest_effect(rh))

        mu_h *= p["mean_bias_home"]
        mu_a *= p["mean_bias_away"]

        # Pace: geometric blend of team paces, fallback to explicit game pace, then league
        pace = self._num(ctx.get("pace")) or self._num(ctx.get("tempo"))
        hp = self._num(H.get("pace")) or self._num(H.get("tempo"))
        ap = self._num(A.get("pace")) or self._num(A.get("tempo"))
        if pace is None and hp and ap:
            pace = math.sqrt(hp * ap)
        if pace is None:
            pace = hp or ap or p["league_pace"]
        n_mean = pace * p["h1_possession_share"]
        return max(mu_h, 1e-6), max(mu_a, 1e-6), max(n_mean, 1.0)

    # ------------------------------------------------------------ lattice engine
    def _tilted_pmf(self, target_mean: float) -> np.ndarray:
        q = np.asarray(self.p["outcome_base"], dtype=float)
        q = q / q.sum()
        k = np.arange(q.size, dtype=float)
        kmax = k[-1]
        target = min(max(target_mean, 1e-6), kmax - 1e-6)

        def mean_at(theta: float) -> float:
            z = q * np.exp(theta * k - theta * kmax * (theta > 0))
            return float((z * k).sum() / z.sum())

        theta = brentq(lambda t: mean_at(t) - target, -40.0, 40.0, xtol=1e-12)
        z = q * np.exp(theta * k - theta * kmax * (theta > 0))
        return z / z.sum()

    def _conditional_pmfs(self, mu: float, n_support: np.ndarray) -> Dict[int, np.ndarray]:
        """P(S = s | N = n) for each n, integrated over the efficiency shock."""
        tau = max(self.p["tau_eff"], 0.0)
        m = int(self.p["quad_nodes"]) if tau > 0 else 1
        nodes, weights = np.polynomial.hermite_e.hermegauss(m)
        weights = weights / weights.sum()
        n_max = int(n_support.max())
        n_set = set(int(n) for n in n_support)
        out: Dict[int, np.ndarray] = {int(n): np.zeros(4 * n_max + 1) for n in n_support}
        n_mean = float(self._n_mean_cache)

        for z, wz in zip(nodes, weights):
            mu_g = mu * math.exp(tau * z - 0.5 * tau * tau)   # mean-preserving shock
            x = self._tilted_pmf(mu_g / n_mean)
            power = np.array([1.0])
            for n in range(1, n_max + 1):
                power = np.convolve(power, x)
                if n in n_set:
                    out[n][: power.size] += wz * power
        return out

    def _build(self, mu_h: float, mu_a: float, n_mean: float) -> Tuple[np.ndarray, np.ndarray]:
        key = (round(mu_h, 3), round(mu_a, 3), round(n_mean, 3), self.p["tau_eff"],
               self.p["possession_sd_frac"])
        hit = self._cache.get(key)
        if hit is not None:
            return hit

        sd_n = max(self.p["possession_sd_frac"] * n_mean, 1e-6)
        lo = max(1, int(math.floor(n_mean - 5.0 * sd_n)))
        hi = int(math.ceil(n_mean + 5.0 * sd_n))
        n_support = np.arange(lo, hi + 1)
        # Discretized normal possession count (continuity-corrected cell masses)
        from scipy.stats import norm
        pn = norm.cdf(n_support + 0.5, n_mean, sd_n) - norm.cdf(n_support - 0.5, n_mean, sd_n)
        pn = np.clip(pn, 0.0, None)
        pn /= pn.sum()

        self._n_mean_cache = n_mean
        cond_h = self._conditional_pmfs(mu_h, n_support)
        cond_a = self._conditional_pmfs(mu_a, n_support)

        size = 4 * int(n_support.max()) + 1
        joint = np.zeros((size, size))
        for n, w in zip(n_support, pn):
            if w > 0:
                joint += w * np.outer(cond_h[int(n)], cond_a[int(n)])

        joint = np.clip(joint, 0.0, None)
        joint /= joint.sum()
        # Truncate negligible tails, then renormalize
        eps = self.p["tail_eps"]
        mh, ma = joint.sum(axis=1), joint.sum(axis=0)
        th = int(np.searchsorted(np.cumsum(mh), 1.0 - eps)) + 1
        ta = int(np.searchsorted(np.cumsum(ma), 1.0 - eps)) + 1
        joint = joint[: min(th, size), : min(ta, size)]
        joint /= joint.sum()
        marginal = joint.sum(axis=1)

        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[key] = (joint, marginal)
        return joint, marginal

    # ------------------------------------------------------------ public API
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a, n_mean = self._situational(game_context or {})
        return self._build(mu_h, mu_a, n_mean)[0]

    def home_points_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a, n_mean = self._situational(game_context or {})
        return self._build(mu_h, mu_a, n_mean)[1]

    def prob_over(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        pmf = self.home_points_pmf(game_context)
        s = np.arange(pmf.size)
        over = float(pmf[s > line].sum())
        under = float(pmf[s < line].sum())
        push = max(0.0, 1.0 - over - under)
        return {"over": over, "under": under, "push": push}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.score_matrix(game_context)
        try:
            return DiscreteDistribution.from_scores(joint)
        except Exception:
            pass
        scores: Dict[Tuple[int, int], float] = {}
        nz = np.argwhere(joint > 0.0)
        for h, a in nz:
            scores[(int(h), int(a))] = float(joint[h, a])
        tot = sum(scores.values())
        scores = {k: v / tot for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            # Final fallback: 3-way outcome from the joint lattice
            p_home = float(np.tril(joint, -1).sum())
            p_draw = float(np.trace(joint))
            p_away = max(0.0, 1.0 - p_home - p_draw)
            t = p_home + p_draw + p_away
            return DiscreteDistribution(probabilities={"home": p_home / t,
                                                       "draw": p_draw / t,
                                                       "away": p_away / t})

    # ------------------------------------------------------------ calibration
    def fit(self, games: Iterable[Dict[str, Any]],
            home_key: str = "home_score_1h", away_key: str = "away_score_1h") -> Dict[str, float]:
        """
        Maximum-likelihood calibration on historical first halves.
          1. Mean bias: realized/predicted ratio for each side. This corrects
             full-game-halving leakage and any anchor drift.
          2. Dispersion: joint grid MLE over (tau_eff, possession_sd_frac) using
             the home 1H marginal lattice likelihood.
        """
        rows: List[Tuple[Dict[str, Any], float, Optional[float]]] = []
        for g in games or []:
            ctx = g.get("context", g)
            yh = self._num(g.get(home_key))
            if yh is None:
                continue
            rows.append((ctx, yh, self._num(g.get(away_key))))
        if len(rows) < 30:
            return {}

        self.p["mean_bias_home"] = self.p["mean_bias_away"] = 1.0
        pred = [self._situational(c) for c, _, _ in rows]
        ph = np.array([x[0] for x in pred])
        yh = np.array([r[1] for r in rows])
        self.p["mean_bias_home"] = float(yh.sum() / ph.sum())
        ya = [(r[2], x[1]) for r, x in zip(rows, pred) if r[2] is not None]
        if ya:
            self.p["mean_bias_away"] = float(sum(a for a, _ in ya) / sum(b for _, b in ya))

        sit = [self._situational(c) for c, _, _ in rows]
        best = (-np.inf, self.p["tau_eff"], self.p["possession_sd_frac"])
        for tau in np.linspace(0.0, 0.12, 13):
            for sdf in np.linspace(0.02, 0.10, 5):
                self.p["tau_eff"], self.p["possession_sd_frac"] = float(tau), float(sdf)
                self._cache.clear()
                ll = 0.0
                for (mh, ma, nm), y in zip(sit, yh):
                    pmf = self._build(mh, ma, nm)[1]
                    yi = int(round(y))
                    ll += math.log(pmf[yi] if 0 <= yi < pmf.size and pmf[yi] > 0 else 1e-300)
                if ll > best[0]:
                    best = (ll, float(tau), float(sdf))
        self.p["tau_eff"], self.p["possession_sd_frac"] = best[1], best[2]
        self._cache.clear()
        return {"loglik": best[0], "tau_eff": best[1], "possession_sd_frac": best[2],
                "mean_bias_home": self.p["mean_bias_home"],
                "mean_bias_away": self.p["mean_bias_away"]}


CandidateModel = NBAHalf1HomeTotalModel