"""
Candidate model for NCAAF | quarter_1 | winner_total.

This is a cold-start model. No settled wagers exist for this segment, so it is
built from football scoring mechanics rather than fitted to bet outcomes.

1. Scoring is lumpy. First-quarter points are not continuous or Poisson in
   points. They come from discrete scoring events:
       touchdown with try   -> 6 / 7 / 8 points
       field goal           -> 3 points
       safety               -> 2 points
   Each team's Q1 score is therefore a compound Poisson process over scoring
   events. Its exact lattice pmf comes from the Panjer recursion. This keeps
   the real mass spikes at 0, 3, 7, 10 and 14, along with the high Q1 tie
   mass (0-0, 7-7, 3-3) that a winner market has to price correctly.

2. Shared game tempo. Both teams draw possessions from the same clock. When
   the observed event dispersion index is above 1, a shared Gamma frailty on
   tempo is integrated exactly with generalized Gauss-Laguerre quadrature.
   This gives positive score correlation and overdispersed totals. At the
   observed cold-start index of 1.000 it reduces to the Poisson baseline.

3. Means. Means are anchored to the empirical baselines (home 7.00,
   away 6.25). They are scaled multiplicatively in log space by:
       - attack/defense exposure,
       - tempo/pace elasticity,
       - rest differential, through a smooth saturating tanh response,
       - weather: wind suppresses field goals and passing, and cold
         suppresses scoring, both through smooth softplus responses.
   If the caller supplies expectations, situational terms enter only as
   partially pooled residuals. This avoids double-counting information the
   expectations already contain.

4. Home-field over-confidence control. The home/away log-ratio is shrunk
   toward the empirical anchor ratio by a reliability coefficient. The game
   total is preserved exactly by this shrinkage.

5. No bookmaker information enters the forecast, and there are no hard
   probability clamps or odds caps. Every structural parameter is a named,
   documented domain prior that can be overridden per call through
   game_context['model_overrides'].
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import roots_genlaguerre

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - the execution harness normally provides it
    BaseCandidateModel = object  # type: ignore

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        DiscreteDistribution  # type: ignore  # noqa: F821  (pre-loaded in scope)
    except NameError:  # pragma: no cover
        DiscreteDistribution = None  # type: ignore


# --------------------------------------------------------------------------- #
# Domain priors (documented, overridable, no hidden constants)
# --------------------------------------------------------------------------- #
@dataclass
class NCAAFQuarter1Priors:
    # Empirical Q1 baselines for this segment.
    home_mean: float = 7.00
    away_mean: float = 6.25

    # Typical NCAAF full-game scoring for one team. Used only to detect and
    # convert full-game expectations into Q1 expectations.
    full_game_team_mean: float = 28.5

    # Observed event-count dispersion index (Var/Mean). A value of 1.0 means Poisson.
    dispersion_index: float = 1.000

    # Shares of points by scoring mechanism (NCAAF structural averages).
    share_td: float = 0.775
    share_fg: float = 0.215
    share_safety: float = 0.010

    # Touchdown try mechanics.
    xp_success: float = 0.975        # PAT kick conversion
    two_pt_attempt: float = 0.045    # Q1 two-point attempt rate (low early in games)
    two_pt_success: float = 0.45     # two-point conversion rate

    # Tempo elasticity: Q1 points scale like plays^elasticity.
    pace_elasticity: float = 0.85
    league_plays_per_game: float = 70.0

    # Rest: saturating log-mean response to the rest-day differential.
    rest_log_effect: float = 0.035   # maximum log shift at saturation
    rest_scale_days: float = 4.0

    # Weather (open-air venues only).
    wind_onset_mph: float = 12.0
    wind_softness_mph: float = 3.0
    wind_total_log_coef: float = 0.012   # log-mean drop per mph above onset
    wind_fg_log_coef: float = 0.035      # extra FG-share decay per mph above onset
    cold_onset_f: float = 32.0
    cold_softness_f: float = 6.0
    cold_log_coef: float = 0.004         # log-mean drop per °F below onset
    dome_log_effect: float = 0.015       # small scoring lift indoors

    # Attack/defense exposure weight in log space.
    strength_weight: float = 1.0

    # Weight on situational terms when caller expectations are present
    # (partial pooling against double counting).
    residual_weight: float = 0.5

    # Reliability of the home/away separation (shrinks the margin toward
    # the anchor ratio).
    margin_reliability: float = 0.85

    # Numerical settings.
    quadrature_nodes: int = 24
    tail_tolerance: float = 1e-12

    extra: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Candidate model
# --------------------------------------------------------------------------- #
class NCAAFQuarter1WinnerTotalModel(BaseCandidateModel):  # type: ignore[misc]
    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_quarter_1_1107_202804",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "quarter_1",
        family: str = "winner_total",
        priors: Optional[NCAAFQuarter1Priors] = None,
    ) -> None:
        init_kwargs = dict(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )
        try:
            super().__init__(**init_kwargs)  # type: ignore[call-arg]
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)  # type: ignore[call-arg]
            except TypeError:
                super().__init__()  # type: ignore[call-arg]
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = priors or NCAAFQuarter1Priors()

    # ------------------------------------------------------------------ #
    # Small numeric helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _f(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    @staticmethod
    def _softplus(x: float) -> float:
        return x + math.log1p(math.exp(-x)) if x > 0 else math.log1p(math.exp(x))

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._f(ctx[k])
                if v is not None:
                    return v
        return None

    def _team_feature(self, ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict):
            v = self._f(team.get(name))
            if v is not None:
                return v
        return self._get(ctx, f"{side}_{name}")

    @staticmethod
    def _log_strength(x: Optional[float]) -> float:
        """Map a strength rating to log space.

        Positive ratings are treated as multiplicative ratios. Non-positive
        ratings are treated as already being on a log scale.
        """
        if x is None:
            return 0.0
        return math.log(x) if x > 0 else x

    def _resolve_priors(self, ctx: Dict[str, Any]) -> NCAAFQuarter1Priors:
        p = NCAAFQuarter1Priors(**{k: v for k, v in asdict(self.priors).items()})
        overrides = ctx.get("model_overrides")
        if isinstance(overrides, dict):
            for k, v in overrides.items():
                if hasattr(p, k):
                    fv = self._f(v)
                    if fv is not None:
                        setattr(p, k, type(getattr(p, k))(fv) if not isinstance(getattr(p, k), dict) else v)
        di = self._get(ctx, "dispersion_index", "q1_dispersion_index")
        if di is not None and di > 0:
            p.dispersion_index = di
        return p

    # ------------------------------------------------------------------ #
    # Mean construction
    # ------------------------------------------------------------------ #
    def _to_q1(self, value: Optional[float], anchor: float, p: NCAAFQuarter1Priors) -> Optional[float]:
        """Accept either a Q1 or a full-game expectation.

        A value is classified as full-game if it is closer, in log distance,
        to the full-game team mean than to the Q1 anchor. Full-game values
        are rescaled by the Q1 share of scoring.
        """
        if value is None or value <= 0:
            return None
        d_q1 = abs(math.log(value / anchor))
        d_fg = abs(math.log(value / p.full_game_team_mean))
        if d_fg < d_q1:
            q1_share = (p.home_mean + p.away_mean) / (2.0 * p.full_game_team_mean)
            return value * q1_share
        return value

    def _pace_log_factor(self, ctx: Dict[str, Any], p: NCAAFQuarter1Priors) -> float:
        pace = self._get(ctx, "pace", "tempo", "expected_plays")
        if pace is None:
            hp = self._team_feature(ctx, "home", "pace") or self._team_feature(ctx, "home", "tempo")
            ap = self._team_feature(ctx, "away", "pace") or self._team_feature(ctx, "away", "tempo")
            vals = [v for v in (hp, ap) if v is not None and v > 0]
            pace = float(np.mean(vals)) if vals else None
        if pace is None or pace <= 0:
            return 0.0
        # Large values are absolute play counts; values near 1 are ratios.
        ratio = pace / p.league_plays_per_game if pace > 5.0 else pace
        return p.pace_elasticity * math.log(ratio)

    def _rest_log_shift(self, ctx: Dict[str, Any], p: NCAAFQuarter1Priors) -> float:
        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        if hr is None or ar is None:
            return 0.0
        return p.rest_log_effect * math.tanh((hr - ar) / p.rest_scale_days)

    def _weather(self, ctx: Dict[str, Any], p: NCAAFQuarter1Priors) -> Tuple[float, float]:
        """Return (log total-scoring shift, log FG-share shift)."""
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}

        def wget(*keys: str) -> Any:
            for src in (weather, venue, ctx):
                for k in keys:
                    if k in src:
                        return src[k]
            return None

        is_dome = bool(wget("is_dome", "dome", "indoor"))
        if is_dome:
            return p.dome_log_effect, 0.0

        log_total, log_fg = 0.0, 0.0
        wind = self._f(wget("wind_speed", "wind", "wind_mph"))
        if wind is not None and wind > 0:
            excess = p.wind_softness_mph * self._softplus((wind - p.wind_onset_mph) / p.wind_softness_mph)
            log_total -= p.wind_total_log_coef * excess
            log_fg -= p.wind_fg_log_coef * excess
        temp = self._f(wget("temp", "temperature", "temp_f"))
        if temp is not None:
            deficit = p.cold_softness_f * self._softplus((p.cold_onset_f - temp) / p.cold_softness_f)
            log_total -= p.cold_log_coef * deficit
        return log_total, log_fg

    def _means(self, ctx: Dict[str, Any], p: NCAAFQuarter1Priors) -> Tuple[float, float, float]:
        h_exp = self._to_q1(self._get(ctx, "home_expected"), p.home_mean, p)
        a_exp = self._to_q1(self._get(ctx, "away_expected"), p.away_mean, p)
        have_expectations = h_exp is not None and a_exp is not None

        if have_expectations:
            log_h, log_a = math.log(h_exp), math.log(a_exp)
            w = p.residual_weight
        else:
            log_h, log_a = math.log(p.home_mean), math.log(p.away_mean)
            w = 1.0
            if h_exp is not None:
                log_h = math.log(h_exp)
            if a_exp is not None:
                log_a = math.log(a_exp)

        # Attack/defense exposure (log-linear). Partially pooled if expectations exist.
        ha = self._log_strength(self._team_feature(ctx, "home", "attack"))
        hd = self._log_strength(self._team_feature(ctx, "home", "defense"))
        aa = self._log_strength(self._team_feature(ctx, "away", "attack"))
        ad = self._log_strength(self._team_feature(ctx, "away", "defense"))
        s = p.strength_weight * w
        log_h += s * (ha + ad)  # defense rating > 1 means a leakier defense (exposure)
        log_a += s * (aa + hd)

        # Shared environment terms.
        pace = self._pace_log_factor(ctx, p)
        wx_total, wx_fg = self._weather(ctx, p)
        rest = self._rest_log_shift(ctx, p)

        log_h += w * (pace + wx_total + 0.5 * rest)
        log_a += w * (pace + wx_total - 0.5 * rest)

        # Margin-reliability shrinkage. The total (log sum) is preserved and
        # the log-ratio is shrunk toward the anchor ratio.
        anchor_ratio = math.log(p.home_mean / p.away_mean)
        center = 0.5 * (log_h + log_a)
        ratio = log_h - log_a
        ratio = anchor_ratio + p.margin_reliability * (ratio - anchor_ratio)
        log_h, log_a = center + 0.5 * ratio, center - 0.5 * ratio

        mu_h, mu_a = math.exp(log_h), math.exp(log_a)
        if not (math.isfinite(mu_h) and mu_h > 0):
            mu_h = p.home_mean
        if not (math.isfinite(mu_a) and mu_a > 0):
            mu_a = p.away_mean
        return mu_h, mu_a, wx_fg

    # ------------------------------------------------------------------ #
    # Scoring-event severity and compound-Poisson lattice
    # ------------------------------------------------------------------ #
    def _event_structure(self, mu: float, fg_log_shift: float, p: NCAAFQuarter1Priors) -> Tuple[float, np.ndarray]:
        """Return the total event rate Λ and the severity pmf f[x] over points x."""
        s_td, s_fg, s_sf = p.share_td, p.share_fg * math.exp(fg_log_shift), p.share_safety
        z = s_td + s_fg + s_sf
        s_td, s_fg, s_sf = s_td / z, s_fg / z, s_sf / z

        go2, conv, xp = p.two_pt_attempt, p.two_pt_success, p.xp_success
        td_vals = {8: go2 * conv, 7: (1 - go2) * xp, 6: go2 * (1 - conv) + (1 - go2) * (1 - xp)}
        e_td = sum(v * q for v, q in td_vals.items())

        lam_td = s_td * mu / e_td
        lam_fg = s_fg * mu / 3.0
        lam_sf = s_sf * mu / 2.0
        lam = lam_td + lam_fg + lam_sf

        f = np.zeros(9)
        for v, q in td_vals.items():
            f[v] += lam_td * q / lam
        f[3] += lam_fg / lam
        f[2] += lam_sf / lam
        return lam, f

    def _panjer(self, lam: float, f: np.ndarray, p: NCAAFQuarter1Priors) -> np.ndarray:
        """Exact compound-Poisson pmf on the integer points lattice."""
        mean = lam * float(np.dot(np.arange(len(f)), f))
        var = lam * float(np.dot(np.arange(len(f)) ** 2, f))
        n_max = int(math.ceil(mean + 14.0 * math.sqrt(max(var, 1e-9)))) + len(f) * 2
        g = np.zeros(n_max + 1)
        g[0] = math.exp(-lam * (1.0 - f[0]))
        xf = np.arange(len(f)) * f
        for s in range(1, n_max + 1):
            kmax = min(s, len(f) - 1)
            acc = 0.0
            for x in range(1, kmax + 1):
                if xf[x] != 0.0:
                    acc += xf[x] * g[s - x]
            g[s] = lam * acc / s
            if s > mean and g[s] < p.tail_tolerance and g[s - 1] < p.tail_tolerance:
                g = g[: s + 1]
                break
        g = np.clip(g, 0.0, None)
        return g / g.sum()

    def _score_matrix(self, mu_h: float, mu_a: float, fg_shift: float, p: NCAAFQuarter1Priors) -> np.ndarray:
        lam_h, f_h = self._event_structure(mu_h, fg_shift, p)
        lam_a, f_a = self._event_structure(mu_a, fg_shift, p)

        d = p.dispersion_index
        if d <= 1.0 + 1e-9:
            gh = self._panjer(lam_h, f_h, p)
            ga = self._panjer(lam_a, f_a, p)
            return np.outer(gh, ga)

        # Shared Gamma tempo frailty G ~ Gamma(k, 1/k). The event-count
        # dispersion index is Var/Mean = 1 + Λ/k, so k = Λ_total / (d - 1).
        lam_tot = lam_h + lam_a
        k = lam_tot / (d - 1.0)
        nodes, weights = roots_genlaguerre(p.quadrature_nodes, k - 1.0)
        weights = weights / weights.sum()
        mats = []
        for x, w in zip(nodes, weights):
            gfac = x / k
            gh = self._panjer(lam_h * gfac, f_h, p)
            ga = self._panjer(lam_a * gfac, f_a, p)
            mats.append((w, gh, ga))
        nh = max(len(m[1]) for m in mats)
        na = max(len(m[2]) for m in mats)
        M = np.zeros((nh, na))
        for w, gh, ga in mats:
            M[: len(gh), : len(ga)] += w * np.outer(gh, ga)
        return M / M.sum()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":  # type: ignore[name-defined]
        ctx = game_context or {}
        p = self._resolve_priors(ctx)
        mu_h, mu_a, fg_shift = self._means(ctx, p)
        M = self._score_matrix(mu_h, mu_a, fg_shift, p)

        p_home = float(np.tril(M, -1).sum())
        p_draw = float(np.trace(M))
        p_away = float(np.triu(M, 1).sum())

        scores = {
            (int(i), int(j)): float(M[i, j])
            for i, j in zip(*np.nonzero(M > p.tail_tolerance))
        }
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}

        dist_cls = DiscreteDistribution
        if dist_cls is not None:
            if hasattr(dist_cls, "from_scores"):
                for payload in (scores, M):
                    try:
                        return dist_cls.from_scores(payload)
                    except Exception:
                        continue
            if hasattr(dist_cls, "from_probabilities"):
                try:
                    return dist_cls.from_probabilities(p_home, p_draw, p_away)
                except Exception:
                    pass
            return dist_cls(probabilities={"home": p_home, "draw": p_draw, "away": p_away})
        raise RuntimeError("DiscreteDistribution is not available in the execution scope.")

    # Diagnostic helper (not required by the harness).
    def describe(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        p = self._resolve_priors(game_context or {})
        mu_h, mu_a, fg_shift = self._means(game_context or {}, p)
        M = self._score_matrix(mu_h, mu_a, fg_shift, p)
        hi, ai = np.arange(M.shape[0]), np.arange(M.shape[1])
        tot = hi[:, None] + ai[None, :]
        e_tot = float((M * tot).sum())
        return {
            "mu_home": mu_h,
            "mu_away": mu_a,
            "p_home": float(np.tril(M, -1).sum()),
            "p_draw": float(np.trace(M)),
            "p_away": float(np.triu(M, 1).sum()),
            "expected_total": e_tot,
            "total_var": float((M * (tot - e_tot) ** 2).sum()),
            "p_zero_zero": float(M[0, 0]),
        }


CandidateModel = NCAAFQuarter1WinnerTotalModel