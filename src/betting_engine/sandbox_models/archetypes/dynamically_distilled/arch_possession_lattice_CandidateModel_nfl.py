"""
Candidate model for NFL | quarter_1 | home_total.

The first-quarter home score is modelled as a sum of scoring events.

  * Possessions. Each team gets a near-binomial, under-dispersed number of
    first-quarter drives. A 15-minute quarter limits how many drives can fit,
    and pace moves the expected count.
  * Drive outcomes. Each drive ends in a touchdown, a field goal or no score.
    A touchdown converts into 6, 7 or 8 points depending on the try.
  * Non-offensive scores. Defensive and special-teams touchdowns and safeties
    arrive as an independent Poisson layer.
  * Projection uncertainty. The team's scoring rate is itself uncertain. It is
    integrated over a Gamma posterior using equal-probability quantile nodes.
    This produces dispersion from parameter uncertainty, not from an arbitrary
    variance knob.

Each component is solved so that the model's mean matches the situational
expectation exactly. The expectation is anchored on the empirical segment
means (home 6.12, away 5.38). Inputs given as full-game expectations are
mapped into quarter scope automatically.

Every structural number is a named, documented domain prior. Nothing is fitted
to bookmaker prices, and there are no odds caps or step filters.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally provided by the engine
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - pre-loaded in execution scope
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# ---------------------------------------------------------------------------
# Structural domain priors (each one documented; none is a betting clamp)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NFLQuarterPriors:
    # Empirical segment anchors (first-quarter expected points)
    home_anchor: float = 6.12
    away_anchor: float = 5.38

    # League full-game scoring per team. Used only to detect full-game inputs
    # and convert them to quarter scope.
    full_game_team_mean: float = 22.5

    # Possession structure. NFL teams average about 11 drives per game, which
    # is about 2.75 per quarter. A quarter can hold roughly 6 drives at most.
    q1_drives_mean: float = 2.75
    q1_drive_capacity: int = 6

    # Drive outcome mix. League-wide, about 22% of drives end in a touchdown
    # and about 15% in a field goal.
    td_to_fg_ratio: float = 0.22 / 0.15
    # Highest share of drives that can realistically score. This is a physical
    # feasibility bound, not a betting clamp.
    max_scoring_drive_share: float = 0.80

    # Touchdown try outcomes: point after (7), missed or failed try (6), and
    # successful two-point try (8).
    td_try_pmf: Tuple[Tuple[int, float], ...] = ((6, 0.045), (7, 0.925), (8, 0.030))

    # Non-offensive scoring per team per quarter (defensive/special-teams TD
    # and safety)
    non_off_td_rate: float = 0.055
    safety_rate: float = 0.012

    # Projection uncertainty: coefficient of variation of the team scoring rate
    rate_cv: float = 0.18
    quadrature_nodes: int = 9

    # Situational elasticities, written as log-multipliers
    rest_elasticity_per_day: float = 0.012      # first-quarter scripting gains from extra prep
    rest_effect_cap_days: float = 7.0           # a bye gives about a week of extra prep
    wind_td_elasticity: float = -0.010          # per mph above calm, on touchdown share
    wind_fg_elasticity: float = -0.018          # per mph, on field-goal share (kicking hit harder)
    wind_calm_mph: float = 8.0
    cold_temp_f: float = 32.0
    cold_elasticity_per_deg: float = -0.003
    pace_drive_elasticity: float = 1.0          # drives scale proportionally with pace ratio

    # Shrinkage applied to attack/defense ratings. When a market expectation is
    # supplied it already contains most matchup information, so the ratings
    # get only a small residual weight.
    rating_weight_with_expectation: float = 0.15
    rating_weight_without_expectation: float = 1.0

    # Joint lattice support (points per team per quarter)
    max_points: int = 49


class CandidateModel(BaseCandidateModel):
    """Possession-compound scoring model for NFL first-quarter home totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_1_1107_130417",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_1",
        family: str = "home_total",
        priors: Optional[NFLQuarterPriors] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)  # type: ignore[misc]
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = priors or NFLQuarterPriors()

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _f(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._f(ctx[k])
                if v is not None:
                    return v
        return None

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Extract an attack or defense rating, accepting flat or nested layouts."""
        blob = ctx.get(side)
        if isinstance(blob, dict):
            for k in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = self._f(blob.get(k))
                if v is not None:
                    return v
        return self._get(ctx, f"{side}_{kind}", f"{side}_{kind}_rating", f"{side}_{kind}_strength")

    @staticmethod
    def _rating_to_log(r: Optional[float]) -> float:
        """Ratings near 1 are multiplicative; ratings near 0 are additive in log space."""
        if r is None:
            return 0.0
        if r > 0.2:
            return math.log(r)
        return r

    # --------------------------------------------------------- expectations
    def _to_quarter_scope(self, value: Optional[float], anchor: float) -> float:
        """Map an input expectation into first-quarter units.

        A value that is closer, in log distance, to the full-game team mean
        than to the quarter anchor is treated as a full-game expectation and
        rescaled by the empirical quarter share (anchor / full-game mean).
        """
        p = self.priors
        if value is None or value <= 0:
            return anchor
        d_quarter = abs(math.log(value / anchor))
        d_full = abs(math.log(value / p.full_game_team_mean))
        if d_full < d_quarter:
            return value * anchor / p.full_game_team_mean
        return value

    def _situational(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        p = self.priors
        h_raw = self._get(ctx, "home_expected", "home_expected_q1", "lambda_home")
        a_raw = self._get(ctx, "away_expected", "away_expected_q1", "lambda_away")
        has_expectation = h_raw is not None and a_raw is not None
        lam_h = self._to_quarter_scope(h_raw, p.home_anchor)
        lam_a = self._to_quarter_scope(a_raw, p.away_anchor)

        # Matchup ratings (shrunk when a market expectation is supplied)
        w = p.rating_weight_with_expectation if has_expectation else p.rating_weight_without_expectation
        h_att = self._rating_to_log(self._rating(ctx, "home", "attack"))
        h_def = self._rating_to_log(self._rating(ctx, "home", "defense"))
        a_att = self._rating_to_log(self._rating(ctx, "away", "attack"))
        a_def = self._rating_to_log(self._rating(ctx, "away", "defense"))
        # A higher defense rating is treated as weaker defense (more points
        # allowed), matching the multiplicative attack/defense convention.
        log_h = w * (h_att + a_def)
        log_a = w * (a_att + h_def)

        # Rest: first-quarter scripted drives benefit from preparation time
        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        if hr is not None and ar is not None:
            diff = float(np.clip(hr - ar, -p.rest_effect_cap_days, p.rest_effect_cap_days))
            log_h += 0.5 * p.rest_elasticity_per_day * diff
            log_a -= 0.5 * p.rest_elasticity_per_day * diff

        # Weather: dome games neutralise wind and temperature
        is_dome = bool(ctx.get("is_dome") or str(ctx.get("venue_type", "")).lower() in ("dome", "indoor", "closed"))
        wind = 0.0 if is_dome else max(0.0, (self._get(ctx, "wind_speed", "wind_mph") or 0.0) - p.wind_calm_mph)
        temp = self._get(ctx, "temp", "temperature")
        cold = 0.0 if (is_dome or temp is None) else max(0.0, p.cold_temp_f - temp)
        log_cold = p.cold_elasticity_per_deg * cold
        log_h += log_cold
        log_a += log_cold

        # Pace sets the expected number of drives
        pace = self._get(ctx, "pace", "tempo", "pace_ratio")
        hp = self._get(ctx, "home_pace")
        ap = self._get(ctx, "away_pace")
        if pace is None and hp is not None and ap is not None and hp > 0 and ap > 0:
            pace = math.sqrt(hp * ap)
        pace_ratio = 1.0
        if pace is not None and pace > 0:
            # Accept either a ratio (around 1) or plays per game (around 63)
            pace_ratio = pace if pace < 5 else pace / 63.0
        drives_mean = p.q1_drives_mean * pace_ratio ** p.pace_drive_elasticity

        return {
            "lam_home": lam_h * math.exp(log_h),
            "lam_away": lam_a * math.exp(log_a),
            "wind_excess": wind,
            "drives_mean": drives_mean,
        }

    # ------------------------------------------------------- score engine
    def _team_pmf(self, lam: float, drives_mean: float, wind_excess: float) -> np.ndarray:
        """PMF of one team's first-quarter points, integrated over rate uncertainty."""
        p = self.priors
        L = p.max_points + 1
        td_pts = np.array([t for t, _ in p.td_try_pmf])
        td_w = np.array([w for _, w in p.td_try_pmf])
        td_w = td_w / td_w.sum()
        e_td = float(td_pts @ td_w)

        # Wind shifts the touchdown/field-goal mix; kicking suffers more
        ratio = p.td_to_fg_ratio * math.exp((p.wind_td_elasticity - p.wind_fg_elasticity) * wind_excess)

        # Non-offensive scoring layer (independent Poisson components)
        non_off_mean = 7.0 * p.non_off_td_rate + 2.0 * p.safety_rate
        k = np.arange(L)
        td_ext = stats.poisson.pmf(k, p.non_off_td_rate)
        sf = stats.poisson.pmf(k, p.safety_rate)
        non_off = np.zeros(L)
        for i in range(L):
            if td_ext[i] < 1e-14 or 7 * i >= L:
                continue
            for j in range(L):
                pts = 7 * i + 2 * j
                if pts >= L:
                    break
                non_off[pts] += td_ext[i] * sf[j]

        # Equal-probability Gamma nodes for the rate, with mean preserved
        n = p.quadrature_nodes
        shape = 1.0 / p.rate_cv ** 2
        qs = (np.arange(n) + 0.5) / n
        nodes = stats.gamma.ppf(qs, a=shape, scale=lam / shape)
        nodes *= lam / nodes.mean()

        out = np.zeros(L)
        for lam_k in nodes:
            off_mean = max(lam_k - non_off_mean, 1e-9)
            d_mean = drives_mean
            ppd = off_mean / d_mean
            p_fg = ppd / (ratio * e_td + 3.0)
            p_td = ratio * p_fg
            # Feasibility: if the scoring share exceeds the physical bound,
            # the excess must come from extra drives rather than an
            # impossible scoring rate.
            if p_td + p_fg > p.max_scoring_drive_share:
                scale = p.max_scoring_drive_share / (p_td + p_fg)
                p_td *= scale
                p_fg *= scale
                d_mean = off_mean / (p_td * e_td + p_fg * 3.0)
            n_cap = max(p.q1_drive_capacity, int(math.ceil(d_mean)) + 2)
            d_pmf = stats.binom.pmf(np.arange(n_cap + 1), n_cap, min(d_mean / n_cap, 1.0))

            drive = np.zeros(L)
            drive[0] = 1.0 - p_td - p_fg
            drive[3] += p_fg
            for pts, w in zip(td_pts, td_w):
                drive[int(pts)] += p_td * w

            acc = np.zeros(L)
            conv = np.zeros(L)
            conv[0] = 1.0
            for d in range(n_cap + 1):
                if d > 0:
                    conv = np.convolve(conv, drive)[:L]
                acc += d_pmf[d] * conv
            out += np.convolve(acc, non_off)[:L] / n

        s = out.sum()
        return out / s if s > 0 else out

    # ------------------------------------------------------------- public
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        s = self._situational(ctx)
        ph = self._team_pmf(s["lam_home"], s["drives_mean"], s["wind_excess"])
        pa = self._team_pmf(s["lam_away"], s["drives_mean"], s["wind_excess"])

        joint = np.outer(ph, pa)
        joint[joint < 1e-12] = 0.0
        joint /= joint.sum()

        scores: Dict[Tuple[int, int], float] = {}
        hi, ai = np.nonzero(joint)
        for h, a in zip(hi, ai):
            scores[(int(h), int(a))] = float(joint[h, a])

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            # Some engine versions expect a dense matrix
            return DiscreteDistribution.from_scores(joint)

    # Diagnostic helper (not part of the engine contract)
    def home_total_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        s = self._situational(game_context or {})
        ph = self._team_pmf(s["lam_home"], s["drives_mean"], s["wind_excess"])
        return {i: float(v) for i, v in enumerate(ph) if v > 1e-12}