"""
Candidate model for NBA | half_1 | home_total.

Design notes:

1. Diagnosis of the reported statistic.
   The reported Var/Mean of 0.187 around a mean of 56 implies a home first-half
   SD of about 3.2 points. That is structurally unreachable in the NBA.
   - The possession-quantized compound floor, Var(T) >= E[N]*Var(X), is
     roughly 49 * 1.5 ≈ 74, so the SD is at least about 8.6 even with a
     perfectly fixed possession count.
   - The 0.187 figure is most likely computed on something other than raw
     team points (e.g. residuals vs. a line, or normalized values).
   - Pricing to that variance would make every off-median line look like a
     huge edge. That is the classic "uncalibrated tail" failure.
   The model therefore treats variance as a structural quantity. The
   `calibrate()` method fits it, by method of moments, on real historical
   first-half points. It never imposes 0.187 as a clamp. The reported index
   enters only as a weak, sample-size-weighted pull, and that pull is bounded
   below by the compound floor.

2. Mechanics.
   N is the shared first-half possession count (both teams get about the same
   number). X is categorical points per possession over {0,1,2,3,4}. It is
   exponentially tilted so that E[N] * E[X] equals the situational mean.
   The joint score lattice is
       P(h, a) = sum_n P(N=n) * P(X_h^{*n} = h) * P(X_a^{*n} = a).
   It is exact and integer-valued, so no continuity correction is needed.
   The shared N induces the positive home/away correlation seen in real games.

3. Mean.
   The mean is anchored on home_expected / away_expected. Matchup, pace and
   rest enter as log-linear residual adjustments with shrinkage. This avoids
   double-counting information already embedded in the expectation. A
   first-half bias term (the probable source of the under-side leak) is
   learned in calibrate(); it is not assumed.

All priors are named constructor parameters and are overridable or learnable.
None of them is a hidden clamp.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # normally pre-loaded in scope; the import is a safe fallback
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NBAHalf1HomeTotalCompoundModel(BaseCandidateModel):
    """Possession-quantized compound model for NBA first-half home team points."""

    # Empirical per-possession outcome shape (0,1,2,3,4 points). It only fixes
    # the *shape*; the level is re-solved per game by exponential tilting.
    _PPP_SUPPORT = np.arange(5, dtype=float)

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_174927",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        *,
        anchor_home: float = 56.00,
        anchor_away: float = 54.00,
        reported_dispersion_index: float = 0.187,
        reported_sample_size: int = 500,
        ppp_shape: Tuple[float, ...] = (0.47, 0.035, 0.335, 0.15, 0.01),
        possession_sd_prior: float = 3.0,
        league_ppp_prior: float = 1.13,
        league_pace_prior: float = 99.5,
        matchup_weight: float = 0.35,
        pace_weight: float = 0.5,
        rest_effect_per_unit: float = 0.012,
        shrinkage_pseudo_n: float = 150.0,
        mass_tolerance: float = 1e-12,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family

        self.anchor_home = float(anchor_home)
        self.anchor_away = float(anchor_away)
        self.reported_dispersion_index = float(reported_dispersion_index)
        self.reported_sample_size = int(reported_sample_size)

        shape = np.asarray(ppp_shape, dtype=float)
        self.ppp_shape = shape / shape.sum()
        self.possession_var = float(possession_sd_prior) ** 2
        self.league_ppp = float(league_ppp_prior)
        self.league_pace = float(league_pace_prior)

        self.matchup_weight = float(matchup_weight)
        self.pace_weight = float(pace_weight)
        self.rest_effect = float(rest_effect_per_unit)
        self.shrink_k = float(shrinkage_pseudo_n)
        self.tol = float(mass_tolerance)

        # Learned in calibrate(). The neutral defaults mean no assumed bias.
        self.log_bias_home = 0.0
        self.log_bias_away = 0.0
        self.is_calibrated = False

        self._apply_reported_dispersion()

    # ------------------------------------------------------------------ #
    # Variance handling
    # ------------------------------------------------------------------ #
    def _structural_floor_var(self, mu: float, e_n: float) -> float:
        """Var(T) when Var(N) = 0. This is the minimum attainable variance."""
        p = self._tilt(mu / e_n)
        ex = float(p @ self._PPP_SUPPORT)
        ex2 = float(p @ self._PPP_SUPPORT ** 2)
        return e_n * (ex2 - ex * ex)

    def _apply_reported_dispersion(self) -> None:
        """Pull Var(N) toward the reported dispersion index, weighted by evidence.

        Var(N) cannot go negative. If the reported index sits below the
        structural floor (as 0.187 does), it can only drive Var(N) toward zero;
        it can never shrink the total below the possession-quantized floor.
        """
        e_n = (self.anchor_home + self.anchor_away) / (2.0 * self.league_ppp)
        target_var = self.reported_dispersion_index * self.anchor_home
        p = self._tilt(self.anchor_home / e_n)
        ex = float(p @ self._PPP_SUPPORT)
        implied_var_n = max(
            (target_var - self._structural_floor_var(self.anchor_home, e_n))
            / (ex * ex),
            0.0,
        )
        w = self.reported_sample_size / (self.reported_sample_size + self.shrink_k)
        # The reported statistic is of uncertain provenance, so it is down-weighted.
        w *= 0.5
        self.possession_var = (1.0 - w) * self.possession_var + w * implied_var_n

    # ------------------------------------------------------------------ #
    # Per-possession distribution
    # ------------------------------------------------------------------ #
    def _tilt(self, target_ppp: float) -> np.ndarray:
        """Exponentially tilt the PPP shape so that its mean equals target_ppp."""
        support, base = self._PPP_SUPPORT, self.ppp_shape
        hi_mean = float(support.max())
        target = min(max(target_ppp, 1e-6), hi_mean - 1e-6)

        def mean_gap(theta: float) -> float:
            w = base * np.exp(theta * support)
            return float((w @ support) / w.sum()) - target

        lo, hi = -10.0, 10.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        w = base * np.exp(theta * support)
        return w / w.sum()

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Return a multiplicative rating centred on 1.0, or None if absent."""
        block = ctx.get(side)
        raw = None
        if isinstance(block, dict):
            raw = self._num(block.get(key))
        if raw is None:
            raw = self._num(ctx.get(f"{side}_{key}"))
        if raw is None or raw <= 0:
            return None
        # Per-100 efficiency ratings are normalised by league points per 100.
        return raw / (self.league_ppp * 100.0) if raw > 10.0 else raw

    def _pace(self, ctx: Dict[str, Any], side: str) -> Optional[float]:
        block = ctx.get(side)
        v = None
        if isinstance(block, dict):
            v = self._num(block.get("pace"))
        if v is None:
            v = self._num(ctx.get(f"{side}_pace"))
        return v if (v is not None and v > 0) else None

    def _situational_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        mu_h = self._num(ctx.get("home_expected")) or self.anchor_home
        mu_a = self._num(ctx.get("away_expected")) or self.anchor_away

        # Matchup residual: home attack vs away defence, and vice versa.
        # It is shrunk because the anchor usually embeds part of this already.
        def matchup(att_side: str, def_side: str) -> float:
            att = self._rating(ctx, att_side, "attack")
            dfn = self._rating(ctx, def_side, "defense")
            parts = [math.log(v) for v in (att, dfn) if v is not None]
            return self.matchup_weight * sum(parts)

        adj_h = matchup("home", "away")
        adj_a = matchup("away", "home")

        # Rest: log-saturating differential, so no hard clamp is needed.
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            diff = math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0))
            adj_h += self.rest_effect * diff
            adj_a -= self.rest_effect * diff

        mu_h *= math.exp(adj_h + self.log_bias_home)
        mu_a *= math.exp(adj_a + self.log_bias_away)

        # Possessions are shared by both teams. The baseline is implied by the
        # anchors, with a residual tempo adjustment from the teams' paces.
        e_n = (mu_h + mu_a) / (2.0 * self.league_ppp)
        ph, pa = self._pace(ctx, "home"), self._pace(ctx, "away")
        paces = [p for p in (ph, pa) if p is not None]
        if paces:
            tempo = float(np.mean(paces)) / self.league_pace
            e_n *= math.exp(self.pace_weight * math.log(tempo))
            # Pace changes possessions, not efficiency, so the means scale with it.
            scale = math.exp(self.pace_weight * math.log(tempo))
            mu_h *= scale
            mu_a *= scale
        return mu_h, mu_a, e_n

    # ------------------------------------------------------------------ #
    # Core distribution
    # ------------------------------------------------------------------ #
    def _possession_pmf(self, e_n: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = math.sqrt(max(self.possession_var, 0.0))
        if sd < 1e-6:
            lo = int(math.floor(e_n))
            frac = e_n - lo
            ns = np.array([lo, lo + 1])
            return ns, np.array([1.0 - frac, frac])
        span = int(math.ceil(6.0 * sd)) + 1
        ns = np.arange(max(1, int(round(e_n)) - span), int(round(e_n)) + span + 1)
        cdf = stats.norm.cdf(np.append(ns - 0.5, ns[-1] + 0.5), e_n, sd)
        w = np.diff(cdf)
        return ns, w / w.sum()

    @staticmethod
    def _convolution_powers(p: np.ndarray, n_max: int) -> List[np.ndarray]:
        powers = [np.array([1.0])]
        cur = powers[0]
        for _ in range(n_max):
            cur = np.convolve(cur, p)
            powers.append(cur)
        return powers

    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a, e_n = self._situational_means(ctx)
        ns, wn = self._possession_pmf(e_n)
        # The PPP target uses E[N], so that E[T] = mu exactly under independence.
        p_h = self._tilt(mu_h / e_n)
        p_a = self._tilt(mu_a / e_n)
        n_max = int(ns.max())
        pow_h = self._convolution_powers(p_h, n_max)
        pow_a = self._convolution_powers(p_a, n_max)

        size = 4 * n_max + 1
        mat = np.zeros((size, size))
        for n, w in zip(ns, wn):
            if w <= 0:
                continue
            h, a = pow_h[n], pow_a[n]
            mat[: h.size, : a.size] += w * np.outer(h, a)

        mat[mat < 0] = 0.0
        # Trim negligible mass to keep the lattice compact (well under 250ms).
        rows = np.where(mat.sum(axis=1) > self.tol)[0]
        cols = np.where(mat.sum(axis=0) > self.tol)[0]
        mat = mat[: rows.max() + 1, : cols.max() + 1]
        return mat / mat.sum()

    def home_total_pmf(self, ctx: Dict[str, Any]) -> np.ndarray:
        return self.score_matrix(ctx).sum(axis=1)

    def prob_over(self, ctx: Dict[str, Any], line: float) -> float:
        pmf = self.home_total_pmf(ctx)
        k = np.arange(pmf.size)
        return float(pmf[k > line].sum())

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mat = self.score_matrix(game_context)
        scores = {
            (int(h), int(a)): float(mat[h, a])
            for h, a in zip(*np.nonzero(mat > self.tol))
        }
        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            return DiscreteDistribution.from_scores(mat)

    # ------------------------------------------------------------------ #
    # Calibration on historical games
    # ------------------------------------------------------------------ #
    def calibrate(self, history: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        """Fit the first-half mean bias and the possession variance by method of moments.

        Each record must contain 'game_context' plus realized
        'home_h1_points' (and optionally 'away_h1_points').
        Estimates are shrunk toward the priors with pseudo-count shrink_k.
        """
        self.log_bias_home = self.log_bias_away = 0.0
        rows = []
        for rec in history:
            ctx = rec.get("game_context", rec)
            yh = self._num(rec.get("home_h1_points"))
            if yh is None:
                continue
            ya = self._num(rec.get("away_h1_points"))
            mu_h, mu_a, e_n = self._situational_means(ctx)
            rows.append((yh, ya, mu_h, mu_a, e_n))
        n = len(rows)
        if n < 2:
            return {"n": float(n)}

        arr = np.array([[r[0], r[2], r[4]] for r in rows])
        y, mu, e_n = arr[:, 0], arr[:, 1], arr[:, 2]
        w = n / (n + self.shrink_k)

        self.log_bias_home = w * float(np.log(y.mean() / mu.mean()))
        away = [(r[1], r[3]) for r in rows if r[1] is not None]
        if away:
            ya, ma = np.array(away).T
            self.log_bias_away = w * float(np.log(ya.mean() / ma.mean()))

        # Method of moments: residual variance = E[N]Var(X) + E[X]^2 Var(N).
        mu_adj = mu * math.exp(self.log_bias_home)
        resid_var = float(np.var(y - mu_adj, ddof=1))
        floors, ex2s = [], []
        for m, en in zip(mu_adj, e_n):
            p = self._tilt(m / en)
            ex = float(p @ self._PPP_SUPPORT)
            floors.append(en * (float(p @ self._PPP_SUPPORT ** 2) - ex * ex))
            ex2s.append(ex * ex)
        var_n_hat = max((resid_var - float(np.mean(floors))) / float(np.mean(ex2s)), 0.0)
        self.possession_var = (1.0 - w) * self.possession_var + w * var_n_hat
        self.is_calibrated = True

        return {
            "n": float(n),
            "log_bias_home": self.log_bias_home,
            "log_bias_away": self.log_bias_away,
            "residual_var": resid_var,
            "structural_floor_var": float(np.mean(floors)),
            "possession_var": self.possession_var,
            "implied_dispersion_index": resid_var / float(y.mean()),
        }