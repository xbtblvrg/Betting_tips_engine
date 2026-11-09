"""
Candidate model for SERIEA | half_2 | moneyline (cold start).

Approach: a second-half goal process derived from Serie A scoring mechanics.

1. Anchor intensities to the empirical second-half means (home 0.80, away 0.60).
2. Read the situational expectations and decide whether they are full-match
   or half-scoped. The decision uses a log-distance test against the league
   full-match prior, not a fixed threshold.
3. Apply log-linear situational adjustments. Each one is shrunk by a
   reliability weight so features do not double-count information that is
   already in the expectations:
   - attack/defense exposure
   - fatigue, which matters more in the second half (an exponential
     recovery curve in rest days)
   - tempo/pace
   - weather (wind, heat)
4. Limit home-field over-confidence. Excess log home/away ratio beyond the
   empirical baseline is shrunk with a precision-weighted (Bayesian) factor,
   and total goal mass is preserved.
5. Joint score law:
   - Bivariate Poisson. The shared component is derived from the dispersion
     index: Var/Mean = 1 + 2*cov/mean, so cov = 0 when the index is 1.000.
   - Dixon-Coles low-score dependence. Its rho is clipped only to the
     mathematically feasible interval.
6. Integrate the score lattice, truncated at a 1e-12 tail mass, into
   home / draw / away probabilities.

No bookmaker prices are used inside the forecast.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may be exported by base or pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class SerieAHalf2MoneylineModel(BaseCandidateModel):
    """Second-half 3-way moneyline model for Serie A."""

    # ------------------------------------------------------------------ #
    # Domain priors. These are documented empirical / structural moments,
    # not static clamps on outputs.
    # ------------------------------------------------------------------ #
    PRIORS: Dict[str, float] = {
        # Empirical second-half baseline means (forensic autopsy)
        "home_mu_h2": 0.80,
        "away_mu_h2": 0.60,
        # Serie A full-match goal total (long-run league moment), used only
        # to infer the scope of incoming expectations.
        "full_match_total": 2.60,
        # Observed dispersion index of the total (Var/Mean)
        "dispersion_index": 1.000,
        # Dixon-Coles low-score dependence for a full match. It is scaled by
        # the half's share of goal mass because dependence accrues with
        # game-state interactions.
        "dc_rho_full": -0.10,
        # Reliability (signal variance / total variance) of the home/away
        # log-ratio signal versus the league baseline prior.
        "ha_signal_var": 0.09,
        "ha_prior_var": 0.04,
        # Rating shrinkage: full weight when ratings are the only signal,
        # partial weight when expectations already embed them.
        "rating_weight_alone": 0.85,
        "rating_weight_with_expected": 0.35,
        # Fatigue: exponential recovery time constant (days), reference rest,
        # and log-intensity sensitivities in the second half.
        "recovery_tau_days": 2.5,
        "reference_rest_days": 7.0,
        "fatigue_attack_beta": 0.18,
        "fatigue_defense_beta": 0.12,
        # Tempo elasticity of goal total
        "pace_elasticity": 0.60,
        # Weather: wind (m/s) softplus onset and log slope; heat onset (°C)
        "wind_onset": 8.0,
        "wind_slope": 0.012,
        "heat_onset": 28.0,
        "heat_slope": 0.008,
        # Numerical tail mass for score lattice truncation
        "tail_mass": 1e-12,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_2_1107_225420",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_2",
        family: str = "moneyline",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
                **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = dict(self.PRIORS)

    # ------------------------------------------------------------------ #
    # Parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) else default

    def _team_feature(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        blob = ctx.get(side)
        if isinstance(blob, dict):
            for k in (key, f"{key}_rating", f"{key}_strength"):
                if k in blob:
                    v = self._num(blob.get(k))
                    if v is not None:
                        return v
        for k in (f"{side}_{key}", f"{side}_{key}_rating"):
            if k in ctx:
                v = self._num(ctx.get(k))
                if v is not None:
                    return v
        return None

    @staticmethod
    def _to_log_rating(v: Optional[float]) -> float:
        """Positive values are multiplicative (centred on 1). Non-positive
        values are already on the additive log scale (centred on 0)."""
        if v is None:
            return 0.0
        return math.log(v) if v > 0.0 else v

    @staticmethod
    def _softplus(x: float) -> float:
        return math.log1p(math.exp(-abs(x))) + max(x, 0.0)

    # ------------------------------------------------------------------ #
    # Intensity construction
    # ------------------------------------------------------------------ #
    def _half_share(self) -> float:
        p = self.priors
        return (p["home_mu_h2"] + p["away_mu_h2"]) / p["full_match_total"]

    def _base_intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float, bool]:
        p = self.priors
        base_h, base_a = p["home_mu_h2"], p["away_mu_h2"]

        # Explicit half-scoped inputs take precedence.
        hh = self._num(ctx.get("half_2_home_expected"))
        ha = self._num(ctx.get("half_2_away_expected"))
        if hh is not None and ha is not None and hh > 0 and ha > 0:
            return hh, ha, True

        eh = self._num(ctx.get("home_expected"))
        ea = self._num(ctx.get("away_expected"))
        if eh is None or ea is None or eh <= 0 or ea <= 0:
            return base_h, base_a, False

        # Infer scope: is the provided total log-closer to the half prior or
        # to the full-match prior?
        total = eh + ea
        half_total = base_h + base_a
        d_half = abs(math.log(total / half_total))
        d_full = abs(math.log(total / p["full_match_total"]))
        scope_hint = str(ctx.get("expected_scope", "")).lower()
        if scope_hint in ("full", "full_game", "match", "ft"):
            is_full = True
        elif scope_hint in ("half", "half_2", "h2", "second_half"):
            is_full = False
        else:
            is_full = d_full < d_half

        if is_full:
            share = self._half_share()
            return eh * share, ea * share, True
        return eh, ea, True

    def _fatigue(self, rest: Optional[float]) -> float:
        """Positive means more fatigued than the reference rest."""
        if rest is None or rest < 0:
            return 0.0
        tau = self.priors["recovery_tau_days"]
        ref = self.priors["reference_rest_days"]
        return math.exp(-rest / tau) - math.exp(-ref / tau)

    def _intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.priors
        lam_h, lam_a, has_expected = self._base_intensities(ctx)
        log_h, log_a = math.log(lam_h), math.log(lam_a)

        # Attack / defense exposure. A defense rating > 1 means a stronger
        # defense, which suppresses the opponent's goals.
        w = p["rating_weight_with_expected"] if has_expected else p["rating_weight_alone"]
        h_att = self._to_log_rating(self._team_feature(ctx, "home", "attack"))
        h_def = self._to_log_rating(self._team_feature(ctx, "home", "defense"))
        a_att = self._to_log_rating(self._team_feature(ctx, "away", "attack"))
        a_def = self._to_log_rating(self._team_feature(ctx, "away", "defense"))
        log_h += w * (h_att - a_def)
        log_a += w * (a_att - h_def)

        # Fatigue: hurts own attack and own defense, and the effect is
        # concentrated late in the match.
        f_h = self._fatigue(self._num(ctx.get("home_rest_days")))
        f_a = self._fatigue(self._num(ctx.get("away_rest_days")))
        log_h += -p["fatigue_attack_beta"] * f_h + p["fatigue_defense_beta"] * f_a
        log_a += -p["fatigue_attack_beta"] * f_a + p["fatigue_defense_beta"] * f_h

        # Tempo / pace (relative to league = 1) scales total goal mass.
        pace = None
        for k in ("pace", "tempo", "expected_pace"):
            pace = self._num(ctx.get(k))
            if pace is not None:
                break
        if pace is not None and pace > 0:
            s = p["pace_elasticity"] * math.log(pace)
            log_h += s
            log_a += s

        # Weather. A dome neutralises it.
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        is_dome = bool(weather.get("is_dome", ctx.get("is_dome", False)))
        if not is_dome:
            wind = self._num(weather.get("wind_speed", ctx.get("wind_speed")))
            temp = self._num(weather.get("temp", ctx.get("temp")))
            adj = 0.0
            if wind is not None:
                adj -= p["wind_slope"] * self._softplus(wind - p["wind_onset"])
            if temp is not None:
                adj -= p["heat_slope"] * self._softplus(temp - p["heat_onset"])
            log_h += adj
            log_a += adj

        lam_h, lam_a = math.exp(log_h), math.exp(log_a)
        return self._limit_home_confidence(lam_h, lam_a)

    def _limit_home_confidence(self, lam_h: float, lam_a: float) -> Tuple[float, float]:
        """Shrink deviations of log(lam_h/lam_a) from the empirical baseline
        ratio with a precision-weighted factor, preserving the total."""
        p = self.priors
        r0 = math.log(p["home_mu_h2"] / p["away_mu_h2"])
        r = math.log(lam_h / lam_a)
        kappa = p["ha_prior_var"] / (p["ha_prior_var"] + p["ha_signal_var"])
        reliability = 1.0 - kappa
        r_post = r0 + reliability * (r - r0)
        total = lam_h + lam_a
        share_h = 1.0 / (1.0 + math.exp(-r_post))
        return total * share_h, total * (1.0 - share_h)

    # ------------------------------------------------------------------ #
    # Joint score law
    # ------------------------------------------------------------------ #
    def _covariance_component(self, lam_h: float, lam_a: float) -> float:
        d = self.priors["dispersion_index"]
        total = lam_h + lam_a
        cov = max(0.0, (d - 1.0) * total / 2.0)
        return min(cov, 0.999 * min(lam_h, lam_a))

    def _score_matrix(self, lam_h: float, lam_a: float) -> np.ndarray:
        p = self.priors
        lam3 = self._covariance_component(lam_h, lam_a)
        l1, l2 = lam_h - lam3, lam_a - lam3

        n = int(stats.poisson.isf(p["tail_mass"], max(lam_h, lam_a))) + 2
        g = np.arange(n + 1)
        pm1 = stats.poisson.pmf(g, l1)
        pm2 = stats.poisson.pmf(g, l2)

        if lam3 <= 0.0:
            mat = np.outer(pm1, pm2)
        else:
            pm3 = stats.poisson.pmf(g, lam3)
            mat = np.zeros((n + 1, n + 1))
            for k in range(n + 1):
                sub = np.outer(pm1[: n + 1 - k], pm2[: n + 1 - k]) * pm3[k]
                mat[k:, k:] += sub

        # Dixon-Coles dependence, scaled to the half and clipped only to the
        # feasibility region that keeps all tau factors non-negative.
        rho = p["dc_rho_full"] * self._half_share()
        lo = max(-1.0 / max(lam_h, 1e-12), -1.0 / max(lam_a, 1e-12))
        hi = min(1.0 / max(lam_h * lam_a, 1e-12), 1.0)
        rho = min(max(rho, lo), hi)
        mat[0, 0] *= 1.0 - lam_h * lam_a * rho
        mat[0, 1] *= 1.0 + lam_h * rho
        mat[1, 0] *= 1.0 + lam_a * rho
        mat[1, 1] *= 1.0 - rho

        mat = np.clip(mat, 0.0, None)
        s = mat.sum()
        return mat / s if s > 0 else mat

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a = self._intensities(ctx)
        mat = self._score_matrix(lam_h, lam_a)

        p_home = float(np.tril(mat, -1).sum())  # home goals > away goals
        p_draw = float(np.trace(mat))
        p_away = float(np.triu(mat, 1).sum())
        z = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / z, p_draw / z, p_away / z

        if hasattr(DiscreteDistribution, "from_probabilities"):
            try:
                return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
            except TypeError:
                pass
        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )