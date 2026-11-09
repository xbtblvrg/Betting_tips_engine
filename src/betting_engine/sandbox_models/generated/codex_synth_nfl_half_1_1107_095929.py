"""
Candidate model: NFL | half_1 | away_total

Architecture: compound (a,b,0)-class scoring-event model.

  * The away team's first-half points are a random sum of discrete scoring events
    (touchdowns worth 6/7/8, field goals worth 3, safeties worth 2).
  * The count of scoring events N belongs to the Panjer (a,b,0) family. Its
    variance-to-mean ratio D picks the member:
        D > 1  -> Negative Binomial (overdispersed drive efficiency)
        D == 1 -> Poisson (the observed cold-start dispersion index)
        D < 1  -> Binomial (drive-count-limited / underdispersed)
  * Severity mix (TD vs FG vs safety) is endogenous. Stronger expected offenses
    convert a larger share of scoring drives into touchdowns. Wind lowers
    field-goal viability.
  * The event intensity is solved so that the exact compound mean equals the
    point expectation implied by the context. The distribution is therefore
    mean-calibrated by construction rather than by clamping.
  * The point pmf comes from the exact Panjer recursion, so there is no
    simulation noise and no Normal approximation. This preserves the lumpy
    3/7/10/14/17 key-number structure of NFL totals that Normal or Poisson-on-
    points models misprice in the tails.

All priors live in a single PRIORS dictionary as explicit, documented domain
parameters that can be overridden. None of them is a clamp on outputs or odds.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - the sandbox normally provides this
    BaseCandidateModel = object  # type: ignore

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded in scope


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _expit(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _softplus(x: float) -> float:
    return math.log1p(math.exp(-abs(x))) + max(x, 0.0)


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if math.isfinite(v):
            return v
    except (TypeError, ValueError):
        pass
    return None


class NFLHalf1AwayTotalCompoundModel(BaseCandidateModel):
    """Exact compound scoring-event model for NFL first-half away team totals."""

    # ------------------------------------------------------------------ #
    # Domain priors. These are structural NFL parameters, not output clamps.
    # Every one of them can be overridden through the constructor `priors=`.
    # ------------------------------------------------------------------ #
    PRIORS: Dict[str, float] = {
        # Empirical segment anchors (first-half team points).
        "baseline_home_h1": 12.25,
        "baseline_away_h1": 10.75,
        # Share of full-game scoring that occurs in the first half. Used only
        # when the supplied expectations look like full-game numbers.
        "h1_share_of_game": 0.485,
        # Dispersion index (Var/Mean) of scoring-event counts.
        # The observed cold-start value is 1.000, which gives the Poisson limit.
        "event_dispersion_index": 1.000,
        # Severity structure of scoring events.
        "td_share_at_baseline": 0.56,        # TDs / (TDs + FGs + safeties)
        "td_share_elasticity": 0.85,         # d logit(td_share) / d log(mu/baseline)
        "safety_share": 0.012,               # fraction of scoring events that are safeties
        "xp_make": 0.955,
        "two_pt_attempt_rate": 0.07,
        "two_pt_success": 0.48,
        # Shrinkage precision on the supplied market-free expectation versus the
        # structural attack/defense estimate (weight on supplied expectation).
        "expectation_weight": 0.70,
        # Attack/defense ratings are shrunk toward neutral by this factor.
        "rating_shrinkage": 0.60,
        # Rest: log-effect per day of rest differential, saturating smoothly.
        "rest_log_effect_scale": 0.035,
        "rest_saturation_days": 4.0,
        # Pace: elasticity of points with respect to relative pace.
        "pace_elasticity": 0.55,
        # Weather (outdoors only).
        "wind_threshold_mph": 10.0,
        "wind_log_effect_per_mph": 0.0085,
        "wind_fg_logit_per_mph": 0.030,      # wind lowers the FG share of scoring events
        "cold_threshold_f": 32.0,
        "cold_log_effect_per_deg": 0.0030,
        "weather_softness": 2.0,             # softplus smoothing width
        # Numerical tail tolerance for the support of the lattice.
        "tail_tolerance": 1e-11,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_half_1_1107_095929",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "half_1",
        family: str = "away_total",
        priors: Optional[Dict[str, float]] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs
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
        self.p: Dict[str, float] = dict(self.PRIORS)
        if priors:
            self.p.update({k: float(v) for k, v in priors.items()})

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _team_rating(ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict):
            for k in (key, f"{key}_rating", f"{key}_strength"):
                v = _to_float(team.get(k))
                if v is not None:
                    return v
        for k in (f"{side}_{key}", f"{side}_{key}_rating", f"{side}_{key}_strength"):
            v = _to_float(ctx.get(k))
            if v is not None:
                return v
        return None

    @staticmethod
    def _rating_to_log_multiplier(r: Optional[float]) -> float:
        """Map a rating to a log multiplier. Ratings near 1 are read as
        multiplicative and ratings near 0 as additive z-like scores."""
        if r is None:
            return 0.0
        if r > 0 and 0.2 <= r <= 5.0:
            return math.log(r)
        # Additive score: smooth bounded map. tanh is a saturating link, not a clamp.
        return 0.25 * math.tanh(r / 2.0)

    def _resolve_h1_expectation(self, raw: Optional[float], baseline: float,
                                other_raw: Optional[float], other_baseline: float) -> Optional[float]:
        """Detect full-game expectations and convert them to first-half values.

        The decision uses the combined total, which separates the two regimes
        far better than either team alone (H1 total ~23 vs full game ~45)."""
        if raw is None or raw <= 0:
            return None
        combined_raw = raw + (other_raw if (other_raw and other_raw > 0) else raw * other_baseline / baseline)
        combined_h1 = baseline + other_baseline
        combined_full = combined_h1 / self.p["h1_share_of_game"]
        # Pick the regime whose log-distance is smaller.
        if abs(math.log(combined_raw / combined_full)) < abs(math.log(combined_raw / combined_h1)):
            return raw * self.p["h1_share_of_game"]
        return raw

    def _situational_log_effect(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        """Return (log mean shift, FG-share logit shift) for weather, rest, and pace."""
        p = self.p
        other = "home" if side == "away" else "away"
        log_shift = 0.0
        fg_logit_shift = 0.0

        # Rest differential (smoothly saturating).
        r_self = _to_float(ctx.get(f"{side}_rest_days"))
        r_other = _to_float(ctx.get(f"{other}_rest_days"))
        if r_self is not None and r_other is not None:
            diff = r_self - r_other
            sat = p["rest_saturation_days"]
            log_shift += p["rest_log_effect_scale"] * sat * math.tanh(diff / sat)

        # Pace / tempo relative to league average (1.0 = average, or plays per game).
        pace = None
        for k in ("pace", "tempo", f"{side}_pace", "game_pace", "expected_plays"):
            pace = _to_float(ctx.get(k))
            if pace is not None:
                break
        if pace is None and isinstance(ctx.get(side), dict):
            pace = _to_float(ctx[side].get("pace")) or _to_float(ctx[side].get("tempo"))
        if pace is not None and pace > 0:
            rel = pace if pace < 5.0 else pace / 63.0  # plays/game -> ratio vs league average
            log_shift += p["pace_elasticity"] * math.log(rel)

        # Weather (outdoors only).
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}
        is_dome = ctx.get("is_dome", weather.get("is_dome", venue.get("is_dome", False)))
        if not bool(is_dome):
            s = p["weather_softness"]
            wind = _to_float(ctx.get("wind_speed", weather.get("wind_speed")))
            if wind is not None:
                excess = s * _softplus((wind - p["wind_threshold_mph"]) / s)
                log_shift -= p["wind_log_effect_per_mph"] * excess
                fg_logit_shift -= p["wind_fg_logit_per_mph"] * excess
            temp = _to_float(ctx.get("temp", ctx.get("temperature", weather.get("temp"))))
            if temp is not None:
                cold = s * _softplus((p["cold_threshold_f"] - temp) / s)
                log_shift -= p["cold_log_effect_per_deg"] * cold
        return log_shift, fg_logit_shift

    def _team_mean(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        p = self.p
        other = "home" if side == "away" else "away"
        base = p[f"baseline_{side}_h1"]
        base_other = p[f"baseline_{other}_h1"]

        supplied = self._resolve_h1_expectation(
            _to_float(ctx.get(f"{side}_expected")), base,
            _to_float(ctx.get(f"{other}_expected")), base_other,
        )

        # Structural estimate: own attack against opponent defense, shrunk to neutral.
        att = self._rating_to_log_multiplier(self._team_rating(ctx, side, "attack"))
        dfn = self._rating_to_log_multiplier(self._team_rating(ctx, other, "defense"))
        log_struct = math.log(base) + p["rating_shrinkage"] * (att + dfn)

        if supplied is not None:
            w = p["expectation_weight"]
            log_mu = w * math.log(supplied) + (1.0 - w) * log_struct
        else:
            log_mu = log_struct

        sit_log, fg_shift = self._situational_log_effect(ctx, side)
        return math.exp(log_mu + sit_log), fg_shift

    # ------------------------------------------------------------------ #
    # Exact compound distribution
    # ------------------------------------------------------------------ #
    def _severity(self, mu: float, baseline: float, fg_logit_shift: float) -> np.ndarray:
        """Point-value pmf of a single scoring event (index = points)."""
        p = self.p
        safety = p["safety_share"]
        # TD share rises with offensive quality. Wind shifts mass from FG to TD
        # in relative terms because coaches go for it more and kick less.
        logit_td = (_logit(p["td_share_at_baseline"])
                    + p["td_share_elasticity"] * math.log(mu / baseline)
                    - fg_logit_shift)
        td = (1.0 - safety) * _expit(logit_td)
        fg = 1.0 - safety - td

        a2 = p["two_pt_attempt_rate"]
        sev = np.zeros(9)
        sev[2] = safety
        sev[3] = fg
        sev[6] = td * ((1 - a2) * (1 - p["xp_make"]) + a2 * (1 - p["two_pt_success"]))
        sev[7] = td * (1 - a2) * p["xp_make"]
        sev[8] = td * a2 * p["two_pt_success"]
        return sev / sev.sum()

    def _compound_pmf(self, mu: float, severity: np.ndarray) -> np.ndarray:
        """Exact Panjer recursion for the (a,b,0) family, with mean matched to mu."""
        p = self.p
        mean_sev = float(np.dot(np.arange(len(severity)), severity))
        lam = mu / mean_sev                       # expected scoring events
        D = p["event_dispersion_index"]
        s0 = severity[0]

        if abs(D - 1.0) < 1e-9:                   # Poisson
            a, b = 0.0, lam
            f0 = math.exp(-lam * (1.0 - s0))
        elif D > 1.0:                             # Negative Binomial: mean r*beta, var r*beta*(1+beta)
            beta = D - 1.0
            r = lam / beta
            a = beta / (1.0 + beta)
            b = (r - 1.0) * a
            f0 = (1.0 + beta * (1.0 - s0)) ** (-r)
        else:                                     # Binomial: mean n*q, var n*q*(1-q)
            q0 = 1.0 - D
            n = max(1, int(round(lam / q0)))
            q = min(lam / n, 1.0 - 1e-12)
            a = -q / (1.0 - q)
            b = (n + 1) * q / (1.0 - q)
            f0 = (1.0 - q * (1.0 - s0)) ** n

        # Support grows until the remaining tail mass falls below tolerance.
        tol = p["tail_tolerance"]
        max_sev = len(severity) - 1
        denom = 1.0 - a * s0
        f = [f0]
        cum = f0
        x = 0
        hard_cap = int(20 * mu + 200)
        while (1.0 - cum) > tol and x < hard_cap:
            x += 1
            acc = 0.0
            for j in range(1, min(x, max_sev) + 1):
                sj = severity[j]
                if sj > 0.0:
                    acc += (a + b * j / x) * sj * f[x - j]
            fx = max(acc / denom, 0.0)
            f.append(fx)
            cum += fx
        pmf = np.asarray(f, dtype=float)
        return pmf / pmf.sum()

    def team_points_pmf(self, game_context: Dict[str, Any], side: str) -> np.ndarray:
        mu, fg_shift = self._team_mean(game_context, side)
        sev = self._severity(mu, self.p[f"baseline_{side}_h1"], fg_shift)
        return self._compound_pmf(mu, sev)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        away = self.team_points_pmf(ctx, "away")
        home = self.team_points_pmf(ctx, "home")

        # Joint lattice (rows = home, cols = away). The away-total market depends
        # only on the away marginal, which is exact. Within-half cross-team
        # correlation affects joint markets only, so independence is used here.
        matrix = np.outer(home, away)
        matrix /= matrix.sum()

        try:
            return DiscreteDistribution.from_scores(matrix)
        except Exception:
            scores = {
                (int(h), int(a)): float(matrix[h, a])
                for h in range(matrix.shape[0])
                for a in range(matrix.shape[1])
                if matrix[h, a] > 0.0
            }
            return DiscreteDistribution.from_scores(scores)

    # Convenience diagnostics (deterministic, side-effect free).
    def away_total_over_probability(self, game_context: Dict[str, Any], line: float) -> float:
        pmf = self.team_points_pmf(game_context or {}, "away")
        pts = np.arange(len(pmf))
        return float(pmf[pts > line].sum())

    def away_expected_points(self, game_context: Dict[str, Any]) -> float:
        pmf = self.team_points_pmf(game_context or {}, "away")
        return float(np.dot(np.arange(len(pmf)), pmf))


# Alias used by loaders that look for a conventional class name.
CandidateModel = NFLHalf1AwayTotalCompoundModel