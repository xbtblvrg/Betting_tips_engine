"""
Candidate model for NCAA_BASEBALL | quarter_2 | totals.

Segment interpretation
----------------------
Baseball has no quarters. "quarter_2" is treated as a contiguous mid-game block
of K half-innings per side (default K = 2). The empirical segment means
(home 0.40, away 0.30) are the anchor. They already embed whatever
times-through-the-order uplift exists in this block, so the TTO factor defaults
to a neutral ratio and is applied only when the context explicitly supplies one.
This avoids double counting.

Generative structure (per side, per half-inning)
------------------------------------------------
    scoring event   ~ Bernoulli(p(g)),  p(g) = 1 - exp(-r0 * g)
    runs | scoring  ~ zero-truncated Geometric(q),  mean 1 / (1 - q)
    g               ~ shared Gamma frailty (mean 1, variance theta)

g is common to both teams and to all half-innings in the game. It captures
park, wind, umpire, and weather co-movement, so it induces positive home/away
correlation and extra-Poisson dispersion.

Calibration (no static clamps)
------------------------------
* r0 is solved exactly on the discretised frailty measure so that every
  half-inning has marginal mean mu_side / K.
* q is solved by root-finding so that the marginal side-segment dispersion
  index (Var/Mean, including the frailty contribution) equals the posterior
  dispersion D.
* D starts at the observed prior index (1.000). The brief's hypothesis asserts
  realised dispersion "well above 1", but the settled sample is zero, so no
  such evidence exists yet. D therefore moves only through
  `update_posterior`, using a gamma-style shrinkage of pooled Pearson
  dispersion.
* theta is updated from the home/away residual cross-moment. It is bounded
  below by 0 because it is a variance (a support constraint, not a tuning
  clamp).

All numeric defaults live in `SegmentPriors`. They are explicit, overridable
domain priors, not hidden constants.
"""

import math
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may be pre-loaded in scope or exported by base.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - resolved from execution scope
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------- #
# Priors / configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SegmentPriors:
    # Empirical anchors for the segment (runs per side over the block).
    baseline_home: float = 0.40
    baseline_away: float = 0.30
    # Optional full-context reference levels for 'home_expected' / 'away_expected'.
    reference_home_expected: Optional[float] = None
    reference_away_expected: Optional[float] = None
    # Dispersion prior (observed index) and its pseudo-sample strength.
    dispersion_prior: float = 1.0
    dispersion_prior_strength: float = 30.0
    # Shared frailty variance prior and its pseudo-sample strength.
    frailty_var_prior: float = 0.03
    frailty_prior_strength: float = 60.0
    # Segment structure.
    half_innings_per_side: int = 2
    # Situational elasticities (log-linear).
    temp_reference_f: float = 70.0
    temp_elasticity_per_f: float = 0.0025
    wind_frailty_per_mph: float = 0.02
    rest_reference_days: float = 1.0
    rest_elasticity: float = 0.03
    # If True, 'home_expected'/'away_expected' are assumed to already include
    # matchup and situational effects, so attack/defense/rest/temperature are
    # not re-applied on top of them. Variance effects (wind) are always applied.
    context_expected_is_complete: bool = True
    # Numerical resolution.
    frailty_nodes: int = 32
    tail_tolerance: float = 1e-10
    max_support: int = 4096
    # Wager eligibility floor from the mandate (decimal odds).
    min_decimal_odds: float = 1.40


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _truthy(x: Any) -> bool:
    if isinstance(x, str):
        return x.strip().lower() in ("1", "true", "yes", "y", "t")
    return bool(x)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
class NCAABaseballQuarter2TotalsModel(BaseCandidateModel):
    """Hurdle-compound, shared-frailty segment model for NCAA baseball totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_quarter_2_1108_084009",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "quarter_2",
        family: str = "totals",
        priors: Optional[SegmentPriors] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        for name, val in (
            ("model_id", model_id),
            ("version", version),
            ("league", league),
            ("scope", scope),
            ("family", family),
        ):
            try:
                setattr(self, name, val)
            except AttributeError:
                pass

        self.priors: SegmentPriors = priors or SegmentPriors()
        if self.priors.half_innings_per_side < 1:
            raise ValueError("half_innings_per_side must be >= 1")

        # Online posterior sufficient statistics.
        self._n_pearson: float = 0.0
        self._sum_pearson: float = 0.0
        self._n_pairs: float = 0.0
        self._sum_cross_resid: float = 0.0
        self._sum_mu_product: float = 0.0
        self._ref_home_sum: float = 0.0
        self._ref_away_sum: float = 0.0
        self._ref_n: float = 0.0

        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Posterior state
    # ------------------------------------------------------------------ #
    @property
    def dispersion_posterior(self) -> float:
        p = self.priors
        n0 = p.dispersion_prior_strength
        return (n0 * p.dispersion_prior + self._sum_pearson) / (n0 + self._n_pearson)

    @property
    def frailty_var_posterior(self) -> float:
        p = self.priors
        n0 = p.frailty_prior_strength
        theta_hat_mass = (
            self._sum_cross_resid / self._sum_mu_product if self._sum_mu_product > 0 else 0.0
        )
        theta = (n0 * p.frailty_var_prior + self._n_pairs * theta_hat_mass) / (n0 + self._n_pairs)
        return max(0.0, theta)

    def _reference_levels(self, ctx: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
        rh = _num(ctx.get("league_home_expected"))
        ra = _num(ctx.get("league_away_expected"))
        if rh is None:
            rh = self.priors.reference_home_expected
        if ra is None:
            ra = self.priors.reference_away_expected
        if (rh is None or ra is None) and self._ref_n > 0:
            rh = rh if rh is not None else self._ref_home_sum / self._ref_n
            ra = ra if ra is not None else self._ref_away_sum / self._ref_n
        return rh, ra

    # ------------------------------------------------------------------ #
    # Situational mean construction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rating(ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Multiplicative rating; non-positive values are read as log-scale."""
        v = _num(ctx.get(f"{side}_{key}"))
        if v is None:
            block = ctx.get(side)
            if isinstance(block, dict):
                v = _num(block.get(key))
        if v is None:
            return None
        return v if v > 0 else math.exp(v)

    def _venue_weather(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}

        def pick(*keys: str) -> Any:
            for src in (ctx, weather, venue):
                for k in keys:
                    if k in src and src[k] is not None:
                        return src[k]
            return None

        return {
            "is_dome": _truthy(pick("is_dome", "dome", "indoor")),
            "temp": _num(pick("temp", "temperature", "temp_f")),
            "wind_speed": _num(pick("wind_speed", "wind_mph", "wind")),
            "park_factor": _num(pick("park_factor", "run_factor", "park_run_factor")),
        }

    def _segment_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float, Dict[str, Any]]:
        p = self.priors
        diag: Dict[str, Any] = {}
        mu_h, mu_a = p.baseline_home, p.baseline_away

        he = _num(ctx.get("home_expected"))
        ae = _num(ctx.get("away_expected"))
        context_has_expected = he is not None and ae is not None and he > 0 and ae > 0

        if context_has_expected:
            rh, ra = self._reference_levels(ctx)
            if rh and ra and rh > 0 and ra > 0:
                mu_h *= he / rh
                mu_a *= ae / ra
                diag["expected_mode"] = "ratio_to_reference"
            else:
                # Scale-free: keep the anchored segment total, adopt context split.
                total = p.baseline_home + p.baseline_away
                share = he / (he + ae)
                mu_h, mu_a = total * share, total * (1.0 - share)
                diag["expected_mode"] = "split_only"
        else:
            diag["expected_mode"] = "baseline"

        apply_structural = not (context_has_expected and p.context_expected_is_complete)
        vw = self._venue_weather(ctx)

        if apply_structural:
            ha, hd = self._rating(ctx, "home", "attack"), self._rating(ctx, "home", "defense")
            aa, ad = self._rating(ctx, "away", "attack"), self._rating(ctx, "away", "defense")
            # Defense rating convention: runs-allowed multiplier (>1 = leakier).
            if ha is not None:
                mu_h *= ha
            if ad is not None:
                mu_h *= ad
            if aa is not None:
                mu_a *= aa
            if hd is not None:
                mu_a *= hd

            # Rest: a pitching staff with less rest yields more mid-game runs.
            hr, ar = _num(ctx.get("home_rest_days")), _num(ctx.get("away_rest_days"))
            ref = math.log1p(max(p.rest_reference_days, 0.0))
            if ar is not None and ar >= 0:
                mu_h *= math.exp(-p.rest_elasticity * (math.log1p(ar) - ref))
            if hr is not None and hr >= 0:
                mu_a *= math.exp(-p.rest_elasticity * (math.log1p(hr) - ref))

            if not vw["is_dome"] and vw["temp"] is not None:
                t_mult = math.exp(p.temp_elasticity_per_f * (vw["temp"] - p.temp_reference_f))
                mu_h *= t_mult
                mu_a *= t_mult
                diag["temp_mult"] = t_mult

            if vw["park_factor"] is not None and vw["park_factor"] > 0:
                mu_h *= vw["park_factor"]
                mu_a *= vw["park_factor"]

        # Times-through-order: neutral unless explicitly supplied (baseline already embeds it).
        tto = _num(ctx.get("tto_factor", ctx.get("times_through_order_factor")))
        if tto is not None and tto > 0:
            mu_h *= tto
            mu_a *= tto
            diag["tto_factor"] = tto

        # Shared frailty variance: wind adds common, direction-agnostic volatility.
        theta = self.frailty_var_posterior
        if not vw["is_dome"] and vw["wind_speed"] is not None and vw["wind_speed"] > 0:
            theta *= 1.0 + p.wind_frailty_per_mph * vw["wind_speed"]

        diag.update({"mu_home": mu_h, "mu_away": mu_a, "theta": theta, **vw})
        return mu_h, mu_a, theta, diag

    # ------------------------------------------------------------------ #
    # Numerical core
    # ------------------------------------------------------------------ #
    def _frailty_nodes(self, theta: float) -> Tuple[np.ndarray, np.ndarray]:
        n = int(self.priors.frailty_nodes)
        if theta <= 0.0 or n <= 1:
            return np.array([1.0]), np.array([1.0])
        u = (np.arange(n) + 0.5) / n
        g = stats.gamma.ppf(u, a=1.0 / theta, scale=theta)
        g = g / g.mean()
        var_g = float(np.var(g))
        if var_g > 0:
            g_adj = 1.0 + (g - 1.0) * math.sqrt(theta / var_g)  # moment-match variance
            if np.all(g_adj > 0):
                g = g_adj
        return g, np.full(n, 1.0 / n)

    @staticmethod
    def _solve_event_rate(s: float, g: np.ndarray, w: np.ndarray) -> float:
        """Find r >= 0 with E_w[1 - exp(-r g)] = s, for 0 < s < 1."""
        if s <= 0.0:
            return 0.0

        def f(r: float) -> float:
            return float(np.dot(w, -np.expm1(-r * g))) - s

        hi = -math.log1p(-s)
        for _ in range(200):
            if f(hi) >= 0.0:
                break
            hi *= 2.0
        return optimize.brentq(f, 0.0, hi, xtol=1e-14, rtol=1e-12)

    @staticmethod
    def _kfold(p_score: np.ndarray, q: float, k: int, n_support: int) -> np.ndarray:
        n_nodes = p_score.shape[0]
        single = np.zeros((n_nodes, n_support))
        single[:, 0] = 1.0 - p_score
        if n_support > 1:
            j = np.arange(1, n_support)
            geo = (1.0 - q) * np.power(q, j - 1)
            single[:, 1:] = p_score[:, None] * geo[None, :]
        out = single
        for _ in range(k - 1):
            out = np.vstack(
                [np.convolve(out[i], single[i])[:n_support] for i in range(n_nodes)]
            )
        return out

    def _side_conditional(self, q: float, r0: float, g: np.ndarray, k: int) -> np.ndarray:
        tol, cap = self.priors.tail_tolerance, int(self.priors.max_support)
        p_score = -np.expm1(-r0 * g)
        n_support = 16
        while True:
            cond = self._kfold(p_score, q, k, n_support)
            if cond.sum(axis=1).min() >= 1.0 - tol or n_support >= cap:
                return cond
            n_support = min(2 * n_support, cap)

    @staticmethod
    def _index_of(cond: np.ndarray, w: np.ndarray) -> Tuple[float, float]:
        m = w @ cond
        mass = m.sum()
        x = np.arange(m.shape[0])
        mean = float(m @ x) / mass
        var = float(m @ (x * x)) / mass - mean * mean
        return mean, (var / mean if mean > 0 else 1.0)

    def _calibrate_side(
        self, mu_side: float, d_target: float, g: np.ndarray, w: np.ndarray
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        k = self.priors.half_innings_per_side
        mu_half = mu_side / k
        info: Dict[str, Any] = {"mu_side": mu_side, "d_target": d_target}
        if mu_half <= 0:
            cond = np.zeros((g.shape[0], 1))
            cond[:, 0] = 1.0
            info.update({"q": 0.0, "r0": 0.0, "d_achieved": 1.0})
            return cond, info

        # Feasibility: s = mu_half (1 - q) < 1.
        q_lo = 0.0 if mu_half < 1.0 else (1.0 - 1.0 / mu_half) + 1e-9

        def build(q: float) -> Tuple[np.ndarray, float]:
            r0 = self._solve_event_rate(mu_half * (1.0 - q), g, w)
            return self._side_conditional(q, r0, g, k), r0

        def d_of(q: float) -> float:
            cond, _ = build(q)
            return self._index_of(cond, w)[1] - d_target

        f_lo = d_of(q_lo)
        if f_lo >= 0.0:
            q_star = q_lo
            info["binding"] = "dispersion_floor"  # target below structural minimum
        else:
            q_hi = max(q_lo + 1e-6, (d_target + mu_half) / (d_target + 2.0 + mu_half))
            q_hi = min(q_hi, 1.0 - 1e-9)
            while d_of(q_hi) < 0.0 and (1.0 - q_hi) > 1e-6:
                q_hi = 1.0 - (1.0 - q_hi) / 2.0
            q_star = optimize.brentq(d_of, q_lo, q_hi, xtol=1e-10)
            info["binding"] = "moment_matched"

        cond, r0 = build(q_star)
        mean, d_ach = self._index_of(cond, w)
        info.update(
            {
                "q": q_star,
                "r0": r0,
                "mean_achieved": mean,
                "d_achieved": d_ach,
                "mean_cluster_size": 1.0 / (1.0 - q_star),
            }
        )
        return cond, info

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        mu_h, mu_a, theta, diag = self._segment_means(ctx)
        g, w = self._frailty_nodes(theta)
        d_target = self.dispersion_posterior

        cond_h, info_h = self._calibrate_side(mu_h, d_target, g, w)
        cond_a, info_a = self._calibrate_side(mu_a, d_target, g, w)

        joint = cond_h.T @ (w[:, None] * cond_a)
        joint = np.clip(joint, 0.0, None)
        joint /= joint.sum()

        totals = np.zeros(joint.shape[0] + joint.shape[1] - 1)
        for i in range(joint.shape[0]):
            totals[i : i + joint.shape[1]] += joint[i]
        t = np.arange(totals.shape[0])
        t_mean = float(totals @ t)
        t_var = float(totals @ (t * t)) - t_mean**2

        self.last_diagnostics = {
            **diag,
            "dispersion_posterior": d_target,
            "frailty_var_posterior": self.frailty_var_posterior,
            "home": info_h,
            "away": info_a,
            "total_mean": t_mean,
            "total_index": t_var / t_mean if t_mean > 0 else float("nan"),
            "p_total_zero": float(totals[0]),
            "p_total_zero_poisson_ref": math.exp(-(mu_h + mu_a)),
            "home_away_corr": self._corr(joint),
        }
        return joint

    @staticmethod
    def _corr(joint: np.ndarray) -> float:
        h = np.arange(joint.shape[0])
        a = np.arange(joint.shape[1])
        ph, pa = joint.sum(axis=1), joint.sum(axis=0)
        eh, ea = ph @ h, pa @ a
        vh, va = ph @ (h * h) - eh**2, pa @ (a * a) - ea**2
        cov = float(h @ joint @ a) - eh * ea
        return cov / math.sqrt(vh * va) if vh > 0 and va > 0 else 0.0

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.score_matrix(game_context)
        floor = self.priors.tail_tolerance * 1e-3
        scores = {
            (int(i), int(j)): float(joint[i, j])
            for i in range(joint.shape[0])
            for j in range(joint.shape[1])
            if joint[i, j] > floor
        }
        z = sum(scores.values())
        scores = {key: v / z for key, v in scores.items()}
        dd = DiscreteDistribution if DiscreteDistribution is not None else globals().get(
            "DiscreteDistribution"
        )
        if dd is None:
            raise RuntimeError("DiscreteDistribution is not available in execution scope")
        return dd.from_scores(scores)

    def total_line_probabilities(
        self, game_context: Dict[str, Any], line: float
    ) -> Dict[str, float]:
        joint = self.score_matrix(game_context)
        totals = np.zeros(joint.shape[0] + joint.shape[1] - 1)
        for i in range(joint.shape[0]):
            totals[i : i + joint.shape[1]] += joint[i]
        t = np.arange(totals.shape[0])
        return {
            "over": float(totals[t > line].sum()),
            "under": float(totals[t < line].sum()),
            "push": float(totals[np.isclose(t, line)].sum()),
        }

    def evaluate_total_line(
        self,
        game_context: Dict[str, Any],
        line: float,
        over_odds: Optional[float] = None,
        under_odds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Pure model EV (no vig blending). Eligible iff odds >= floor and EV > 0."""
        probs = self.total_line_probabilities(game_context, line)
        out: Dict[str, Any] = {"line": line, **probs}
        for side, odds, p_win, p_lose in (
            ("over", over_odds, probs["over"], probs["under"]),
            ("under", under_odds, probs["under"], probs["over"]),
        ):
            o = _num(odds)
            if o is None or o <= 1.0:
                continue
            ev = p_win * (o - 1.0) - p_lose  # push refunds stake
            out[f"{side}_ev"] = ev
            out[f"{side}_eligible"] = bool(o >= self.priors.min_decimal_odds and ev > 0.0)
        return out

    # ------------------------------------------------------------------ #
    # Online learning
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_runs(rec: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
        for hk, ak in (
            ("home_segment_runs", "away_segment_runs"),
            ("home_runs", "away_runs"),
            ("home_score", "away_score"),
        ):
            h, a = _num(rec.get(hk)), _num(rec.get(ak))
            if h is not None and a is not None:
                return h, a
        return None, None

    def update_posterior(self, settled: Sequence[Dict[str, Any]]) -> Dict[str, float]:
        """Update dispersion / frailty posteriors from settled segment outcomes.

        Each record holds segment runs ('home_segment_runs'/'away_segment_runs',
        or 'home_runs'/'away_runs') and either 'home_mu'/'away_mu' (the model
        means at bet time) or a 'context' dict from which means are recomputed.
        """
        for rec in settled or []:
            yh, ya = self._extract_runs(rec)
            if yh is None or ya is None or yh < 0 or ya < 0:
                continue
            ctx = rec.get("context") if isinstance(rec.get("context"), dict) else rec
            mh, ma = _num(rec.get("home_mu")), _num(rec.get("away_mu"))
            if mh is None or ma is None:
                mh, ma, _, _ = self._segment_means(ctx)
            if mh <= 0 or ma <= 0:
                continue
            self._sum_pearson += (yh - mh) ** 2 / mh + (ya - ma) ** 2 / ma
            self._n_pearson += 2.0
            self._sum_cross_resid += (yh - mh) * (ya - ma)
            self._sum_mu_product += mh * ma
            self._n_pairs += 1.0
            he, ae = _num(ctx.get("home_expected")), _num(ctx.get("away_expected"))
            if he is not None and ae is not None and he > 0 and ae > 0:
                self._ref_home_sum += he
                self._ref_away_sum += ae
                self._ref_n += 1.0
        return {
            "dispersion_posterior": self.dispersion_posterior,
            "frailty_var_posterior": self.frailty_var_posterior,
            "n_settled_pairs": self._n_pairs,
        }

    observe_settled = update_posterior

    def fit(self, history: Optional[Sequence[Dict[str, Any]]] = None, **kwargs: Any):
        if history:
            self.update_posterior(history)
        return self

    def with_priors(self, **overrides: Any) -> "NCAABaseballQuarter2TotalsModel":
        return NCAABaseballQuarter2TotalsModel(
            model_id=getattr(self, "model_id", "codex_synth_ncaa_baseball_quarter_2_1108_084009"),
            version=getattr(self, "version", "1.0.0"),
            league=getattr(self, "league", "NCAA_BASEBALL"),
            scope=getattr(self, "scope", "quarter_2"),
            family=getattr(self, "family", "totals"),
            priors=replace(self.priors, **overrides),
        )


CandidateModel = NCAABaseballQuarter2TotalsModel