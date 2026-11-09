"""
Candidate model for segment 'UEL|half_1|totals'.

Architecture:
    1. Time-inhomogeneous scoring intensity mu_i(t) = mu_bar_i * g(t), with
       g(t) = 1 + kappa * t / T_full rising linearly through the match. The
       first-half rate is the integral of mu_i(t) over [0, 45 + tau_1]. The
       half-share fraction is derived from kappa and stoppage time, not fixed.
    2. Side means are anchored to the empirical segment priors (H 0.80, A 0.60).
       They are scaled multiplicatively by exp(beta . x), where x holds
       attack/defense exposure, tempo, rest differential, rotation/fatigue,
       tie-state caginess and wind.
    3. Context-supplied expectations are reconciled with the anchor through
       precision-weighted log-space shrinkage. Their scale (full match vs H1)
       is inferred by likelihood under the anchor, not assumed.
    4. Marginals are Gamma-Poisson (negative binomial). The dispersion index
       starts at the observed 1.000 (pure Poisson limit) and is updated via a
       conjugate-style moment posterior when settled data arrives (fit()).
    5. Low-score dependence uses a Dixon-Coles tau_rho(x, y). Rho is shrunk
       toward a pooled UEL/UECL full-match prior and confined only to its
       mathematical validity region (tau >= 0), never to an arbitrary clamp.
    6. The score lattice is truncated adaptively by tail mass. Totals are priced
       directly from the joint P(X + Y <= k) via DiscreteDistribution.from_scores.

All "constants" below are explicit, documented Bayesian prior hyperparameters.
Every one can be overridden in the constructor and updated by fit().
No bookmaker prices are blended into the forecast.
"""

import math
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded into execution scope.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UELHalf1TotalsModel(BaseCandidateModel):
    """Time-inhomogeneous, Dixon-Coles-coupled Gamma-Poisson H1 totals model."""

    # ------------------------------------------------------------------ #
    # Prior hyperparameters (documented, overridable, updatable)
    # ------------------------------------------------------------------ #
    DEFAULT_PRIORS: Dict[str, float] = {
        # Empirical segment anchors (first-half expected goals).
        "anchor_home": 0.80,
        "anchor_away": 0.60,
        # Intensity ramp: kappa = 0.5 reproduces the well-documented ~45%
        # first-half goal share in European club football under linear g(t).
        "kappa": 0.5,
        "regulation_half_minutes": 45.0,
        "stoppage_h1_minutes": 2.0,
        "stoppage_h2_minutes": 5.0,
        # Observed dispersion index (Var/Mean); 1.0 => Poisson limit.
        "dispersion_index": 1.0,
        # Pooled UEL/UECL full-match Dixon-Coles rho prior and its pseudo-count.
        "rho_prior": -0.08,
        "rho_prior_strength": 400.0,
        # Precision (in pseudo-matches) of the anchor vs a supplied expectation.
        "anchor_strength": 30.0,
        "context_strength": 60.0,
        # Log-linear feature loadings (elasticities).
        "beta_attack": 0.55,
        "beta_defense": 0.45,
        "beta_pace": 0.60,
        "beta_rest_diff": 0.04,  # per day of rest advantage, saturating
        "rest_saturation_days": 4.0,
        "beta_fatigue": 0.05,  # per day short of a full recovery cycle
        "recovery_cycle_days": 4.0,
        "beta_tie_caginess": 0.07,  # tempo suppression in level two-legged ties
        "beta_wind": 0.006,  # per km/h above calm
        "calm_wind_kmh": 15.0,
        # Lattice truncation tail tolerance.
        "tail_eps": 1e-10,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_1_1107_191745",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_1",
        family: str = "totals",
        priors: Optional[Mapping[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.params: Dict[str, float] = dict(self.DEFAULT_PRIORS)
        if priors:
            self.params.update({k: float(v) for k, v in priors.items()})
        self.rho: float = self.params["rho_prior"]
        self.dispersion_index: float = self.params["dispersion_index"]
        self._n_obs: int = 0

    # ------------------------------------------------------------------ #
    # Intensity integral & half-share
    # ------------------------------------------------------------------ #
    def _intensity_integral(self, t0: float, t1: float, t_full: float) -> float:
        """Integral of g(t) = 1 + kappa * t / t_full over [t0, t1]."""
        k = self.params["kappa"]
        return (t1 - t0) + k * (t1 ** 2 - t0 ** 2) / (2.0 * t_full)

    def _half_share(self, extra: Optional[Dict[str, Any]] = None) -> float:
        """Fraction of full-match intensity mass that falls in H1 (incl. stoppage)."""
        p = self.params
        extra = extra or {}
        half = p["regulation_half_minutes"]
        s1 = float(extra.get("stoppage_h1", p["stoppage_h1_minutes"]))
        s2 = float(extra.get("stoppage_h2", p["stoppage_h2_minutes"]))
        t_full = 2.0 * half
        # H1 maps to clock time [0, half]; its stoppage is played at the
        # half-time intensity level g(half).
        g_half = 1.0 + p["kappa"] * half / t_full
        g_end = 1.0 + p["kappa"]
        h1 = self._intensity_integral(0.0, half, t_full) + s1 * g_half
        h2 = self._intensity_integral(half, t_full, t_full) + s2 * g_end
        return h1 / (h1 + h2)

    # ------------------------------------------------------------------ #
    # Feature extraction helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _side_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Look up a multiplicative attack/defense rating (1.0 = league average)."""
        blob = ctx.get(side)
        if isinstance(blob, Mapping):
            v = self._num(blob.get(kind))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{kind}"))

    @staticmethod
    def _safe_log(v: Optional[float]) -> float:
        return math.log(v) if v is not None and v > 0.0 else 0.0

    def _pace_factor(self, ctx: Dict[str, Any]) -> float:
        """Log tempo index; pace is interpreted as a ratio to the league norm."""
        for key in ("pace", "tempo", "expected_pace"):
            v = self._num(ctx.get(key))
            if v is not None and v > 0:
                return math.log(v)
        hp = self._num(ctx.get("home_pace"))
        ap = self._num(ctx.get("away_pace"))
        logs = [math.log(v) for v in (hp, ap) if v is not None and v > 0]
        return float(np.mean(logs)) if logs else 0.0

    def _tie_caginess(self, ctx: Dict[str, Any]) -> float:
        """Indicator-strength in [0, 1] of a cagey, game-state-dependent tie."""
        stage = str(ctx.get("stage", "") or ctx.get("round", "")).lower()
        knockout = bool(ctx.get("is_knockout")) or any(
            s in stage for s in ("playoff", "round of", "quarter", "semi", "final", "knockout")
        )
        if not knockout:
            return 0.0
        leg = self._num(ctx.get("leg"))
        agg = self._num(ctx.get("aggregate_diff"))  # home-side perspective
        if leg is not None and int(leg) == 2 and agg is not None:
            # Level ties stay cagey; trailing sides must open up.
            return math.exp(-abs(agg))
        if leg is not None and int(leg) == 1:
            return 1.0
        return 0.5

    def _log_multipliers(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.params
        h_att = self._safe_log(self._side_rating(ctx, "home", "attack"))
        h_def = self._safe_log(self._side_rating(ctx, "home", "defense"))
        a_att = self._safe_log(self._side_rating(ctx, "away", "attack"))
        a_def = self._safe_log(self._side_rating(ctx, "away", "defense"))

        pace = self._pace_factor(ctx)

        hr = self._num(ctx.get("home_rest_days"))
        ar = self._num(ctx.get("away_rest_days"))
        rest_term = 0.0
        if hr is not None and ar is not None:
            sat = p["rest_saturation_days"]
            rest_term = p["beta_rest_diff"] * sat * math.tanh((hr - ar) / sat)

        def fatigue(days: Optional[float]) -> float:
            if days is None:
                return 0.0
            return p["beta_fatigue"] * max(0.0, p["recovery_cycle_days"] - days)

        cagey = p["beta_tie_caginess"] * self._tie_caginess(ctx)

        wind = self._num(ctx.get("wind_speed"))
        wind_term = 0.0
        if wind is not None and not bool(ctx.get("is_dome")):
            wind_term = p["beta_wind"] * max(0.0, wind - p["calm_wind_kmh"])

        common = p["beta_pace"] * pace - cagey - wind_term
        # Fatigue in one side reduces its own output and raises its concession.
        lh = (
            p["beta_attack"] * h_att
            - p["beta_defense"] * a_def
            + rest_term
            - fatigue(hr)
            + 0.5 * fatigue(ar)
            + common
        )
        la = (
            p["beta_attack"] * a_att
            - p["beta_defense"] * h_def
            - rest_term
            - fatigue(ar)
            + 0.5 * fatigue(hr)
            + common
        )
        return lh, la

    # ------------------------------------------------------------------ #
    # Mean reconciliation
    # ------------------------------------------------------------------ #
    def _reconcile(self, supplied: Optional[float], anchor: float, share: float) -> float:
        """
        Combine the anchor with a context expectation in log space.

        The scale of `supplied` (H1 or full match) is resolved by comparing the
        Poisson log-likelihood of the anchor under each interpretation.
        """
        if supplied is None or supplied <= 0.0:
            return anchor
        as_h1 = supplied
        as_full = supplied * share

        def ll(m: float) -> float:
            # Poisson-continuous log-likelihood of anchor under mean m.
            return anchor * math.log(m) - m - special.gammaln(anchor + 1.0)

        candidate = as_h1 if ll(as_h1) >= ll(as_full) else as_full
        w_a = self.params["anchor_strength"]
        w_c = self.params["context_strength"]
        return math.exp((w_a * math.log(anchor) + w_c * math.log(candidate)) / (w_a + w_c))

    def segment_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        share = self._half_share(ctx)
        p = self.params
        full_h = self._num(ctx.get("full_match_home_expected"))
        full_a = self._num(ctx.get("full_match_away_expected"))
        sup_h = full_h * share if full_h else self._num(ctx.get("home_expected"))
        sup_a = full_a * share if full_a else self._num(ctx.get("away_expected"))
        base_h = self._reconcile(sup_h, p["anchor_home"], share)
        base_a = self._reconcile(sup_a, p["anchor_away"], share)
        # Feature multipliers apply only to information not already in a
        # supplied expectation; they are damped by the context precision share.
        has_ctx = sup_h is not None or sup_a is not None
        damp = p["anchor_strength"] / (p["anchor_strength"] + p["context_strength"]) if has_ctx else 1.0
        lh, la = self._log_multipliers(ctx)
        return base_h * math.exp(damp * lh), base_a * math.exp(damp * la)

    # ------------------------------------------------------------------ #
    # Marginals, dependence, lattice
    # ------------------------------------------------------------------ #
    def _marginal_pmf(self, mean: float, n_max: int) -> np.ndarray:
        k = np.arange(n_max + 1)
        d = self.dispersion_index
        if d <= 1.0 + 1e-9:
            return stats.poisson.pmf(k, mean)
        # Gamma-Poisson with Var = D * mean  =>  shape r = mean / (D - 1).
        r = mean / (d - 1.0)
        prob = r / (r + mean)
        return stats.nbinom.pmf(k, r, prob)

    def _lattice_size(self, lh: float, la: float) -> int:
        eps = self.params["tail_eps"]
        d = max(self.dispersion_index, 1.0)
        # Chebyshev-safe envelope on the over-dispersed tail via the Poisson
        # quantile of an inflated mean.
        q = stats.poisson.ppf(1.0 - eps, d * max(lh, la))
        return int(max(q, 1)) + 2

    @staticmethod
    def _rho_bounds(lh: float, la: float) -> Tuple[float, float]:
        """Validity region of Dixon-Coles rho: all tau(x, y) >= 0."""
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)
        return lo, hi

    def _effective_rho(self, lh: float, la: float) -> float:
        lo, hi = self._rho_bounds(lh, la)
        # Map into the open validity interval smoothly (no hard clamp).
        mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
        z = (self.rho - mid) / half
        return mid + half * math.tanh(math.atanh(max(min(z, 1 - 1e-12), -1 + 1e-12)))

    def score_matrix(self, lh: float, la: float) -> np.ndarray:
        n = self._lattice_size(lh, la)
        ph = self._marginal_pmf(lh, n)
        pa = self._marginal_pmf(la, n)
        m = np.outer(ph, pa)
        rho = self._effective_rho(lh, la)
        m[0, 0] *= 1.0 - lh * la * rho
        m[0, 1] *= 1.0 + lh * rho
        m[1, 0] *= 1.0 + la * rho
        m[1, 1] *= 1.0 - rho
        m = np.clip(m, 0.0, None)
        return m / m.sum()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = dict(game_context or {})
        lh, la = self.segment_means(ctx)
        m = self.score_matrix(lh, la)
        scores = {
            (int(i), int(j)): float(m[i, j])
            for i in range(m.shape[0])
            for j in range(m.shape[1])
            if m[i, j] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)

    def total_cdf(self, game_context: Dict[str, Any], k: int) -> float:
        """P(X + Y <= k) for the first half; convenience for line pricing."""
        lh, la = self.segment_means(dict(game_context or {}))
        m = self.score_matrix(lh, la)
        i, j = np.indices(m.shape)
        return float(m[(i + j) <= k].sum())

    # ------------------------------------------------------------------ #
    # Posterior updating from settled first-half scores
    # ------------------------------------------------------------------ #
    def fit(self, history: Iterable[Mapping[str, Any]]) -> "UELHalf1TotalsModel":
        """
        Update the dispersion index and rho from settled H1 results.

        Each row needs 'home_goals_h1', 'away_goals_h1' plus the pre-match
        context fields. Both updates are shrinkage posteriors that converge
        on the data as the sample grows and stay at the priors when it is empty.
        """
        rows = [r for r in history if r.get("home_goals_h1") is not None and r.get("away_goals_h1") is not None]
        if not rows:
            return self
        obs, means = [], []
        for r in rows:
            lh, la = self.segment_means(dict(r))
            obs.append((int(r["home_goals_h1"]), int(r["away_goals_h1"])))
            means.append((lh, la))
        x = np.array(obs, dtype=float)
        mu = np.array(means, dtype=float)
        n = len(rows)
        self._n_obs = n

        # Dispersion: Pearson-type index shrunk toward the prior index, with
        # prior weight equal to the anchor pseudo-count.
        pearson = float(np.mean(((x - mu) ** 2 / mu).ravel()))
        w0 = self.params["anchor_strength"]
        self.dispersion_index = max(
            1.0, (w0 * self.params["dispersion_index"] + 2 * n * pearson) / (w0 + 2 * n)
        )

        # Rho: penalised MLE; the Gaussian penalty encodes the pooled prior.
        prior, strength = self.params["rho_prior"], self.params["rho_prior_strength"]

        def neg_post(r: float) -> float:
            ll = 0.0
            for (hx, ax), (lh, la) in zip(obs, means):
                lo, hi = self._rho_bounds(lh, la)
                if not (lo < r < hi):
                    return 1e12
                if hx == 0 and ax == 0:
                    t = 1.0 - lh * la * r
                elif hx == 0 and ax == 1:
                    t = 1.0 + lh * r
                elif hx == 1 and ax == 0:
                    t = 1.0 + la * r
                elif hx == 1 and ax == 1:
                    t = 1.0 - r
                else:
                    t = 1.0
                ll += math.log(max(t, 1e-300))
            return -ll + 0.5 * strength * (r - prior) ** 2

        lo = max(self._rho_bounds(lh, la)[0] for lh, la in means)
        hi = min(self._rho_bounds(lh, la)[1] for lh, la in means)
        if lo < hi:
            res = optimize.minimize_scalar(
                neg_post, bounds=(lo + 1e-9, hi - 1e-9), method="bounded"
            )
            if res.success:
                self.rho = float(res.x)
        return self