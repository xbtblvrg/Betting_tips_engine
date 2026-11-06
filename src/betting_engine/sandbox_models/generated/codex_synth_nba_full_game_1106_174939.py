"""Regulation-time three-way model for NBA|full_game|three_way.

Structure
---------
1. Convert full-game expected points to regulation expected points, because this
   market settles before overtime.
2. Apply a saturating rest effect, then shrink the expected margin towards the
   league baseline to limit home-side over-confidence.
3. Model the margin before the final sequence as a discretized normal. Its
   variance scales with possessions and includes the remaining uncertainty in
   the mean.
4. Apply a mass-conserving endgame kernel. It keeps ties alive and moves close
   games onto a tie, which produces the draw excess a plain Skellam model misses.

Evidence status (read before promotion)
---------------------------------------
- With 0 settled wagers, nothing here is fitted. Every value in NBAThreeWayPriors
  is a provisional domain prior, not an empirical estimate, so the "zero hardcoded
  constants" objective cannot be met until these are fitted to data. They are
  isolated and can be overridden through the constructor.
- The endgame kernel values are judgment-based. By hand calculation they give a
  draw rate near 6-7%, the approximate NBA overtime rate. Fit them to play-by-play
  data.
- The code has not been executed or backtested, and no ROI or Brier skill is
  claimed. The quoted benchmarks (e.g. +122% ROI, +0.279 Brier skill) are not
  credible targets for a liquid market and are more likely to reflect small
  samples or leakage. They were not used as calibration targets.
- Validate out of sample on regulation-result log loss and draw calibration. The
  112/108 anchors should be refreshed from current-season data.

Assumptions about game_context
------------------------------
- home_expected and away_expected include overtime points by default (set
  expected_includes_overtime=False if not) and already reflect attack/defense
  strength. Attack/defense multipliers (1.0 = league average) are therefore used
  only when an expected value is missing, to avoid double counting.
- Expected values are assumed not to include rest. If they do, set
  rest_margin_scale=0.
- Pace above pace_possessions_cutoff is read as possessions per team per 48
  minutes. A smaller positive value is read as a multiplier on implied possessions.

Differences from the supplied hypothesis
----------------------------------------
- No explicit tau term: the kernel only reallocates mass among near-zero margins,
  and scaling the mean by (1 - tau/48) would bias it without a matching drift.
- The kernel is parametric rather than fitted, because no play-by-play data is
  available to this class.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import ndtr

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # DiscreteDistribution may instead be pre-loaded in the execution scope.
    pass


@dataclass(frozen=True)
class NBAThreeWayPriors:
    """Provisional priors, to be replaced by fitted values."""

    # Segment anchors (points per team, full game).
    home_anchor: float = 112.0
    away_anchor: float = 108.0

    # Overtime length as a share of regulation; set by the rules of the game.
    overtime_fraction: float = 5.0 / 48.0
    expected_includes_overtime: bool = True

    # Points per possession, used to imply possessions when pace is missing.
    points_per_possession: float = 1.14
    pace_possessions_cutoff: float = 3.0

    # Effective margin variance per possession. This sits below the raw
    # possession-level variance because leading teams ease off and margins
    # compress, targeting a residual margin SD of roughly 12-12.5 points.
    margin_variance_per_possession: float = 1.55

    # Shrinkage: prior spread of true matchup margins and error in the input mean.
    matchup_margin_variance: float = 36.0
    mean_error_variance: float = 4.0

    # Rest effect: B * (exp(-away_rest / k) - exp(-home_rest / k)).
    rest_margin_scale: float = 2.5
    rest_decay_days: float = 1.5

    # Endgame kernel, indexed by gap = 1, 2, 3, ...
    tie_retention: float = 0.55
    tie_by_gap: Tuple[float, ...] = (0.18, 0.30, 0.22, 0.06, 0.03)
    flip_by_gap: Tuple[float, ...] = (0.25, 0.08, 0.01, 0.00, 0.00)


def _number(value: Any) -> Optional[float]:
    """Return value as a finite float, or None if it is unusable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _positive(value: Any) -> Optional[float]:
    result = _number(value)
    return result if result is not None and result > 0.0 else None


def _side_value(context: Mapping[str, Any], side: str, key: str) -> Optional[float]:
    """Read '<side>_<key>' or context[side][key], accepting positive values only."""
    flat = _positive(context.get(f"{side}_{key}"))
    if flat is not None:
        return flat
    nested = context.get(side)
    if isinstance(nested, Mapping):
        return _positive(nested.get(key))
    return None


class NBAFullGameThreeWayModel(BaseCandidateModel):
    """Possession-scaled margin model with an endgame tie kernel."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_174939",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "three_way",
        priors: Optional[NBAThreeWayPriors] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.priors = priors if priors is not None else NBAThreeWayPriors()

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        p = self.priors
        home_full, away_full = self._expected_points(game_context)

        overtime_rate = 0.0
        p_home, p_draw, p_away = 0.0, 0.0, 0.0
        # The overtime rate and the draw probability are the same event, so
        # solve for them with a short fixed-point iteration.
        passes = 3 if p.expected_includes_overtime else 1
        for _ in range(passes):
            inflation = 1.0 + overtime_rate * p.overtime_fraction
            home_reg = home_full / inflation
            away_reg = away_full / inflation
            p_home, p_draw, p_away = self._regulation_probabilities(
                game_context, home_reg, away_reg
            )
            if p.expected_includes_overtime:
                overtime_rate = p_draw

        total = p_home + p_draw + p_away
        return DiscreteDistribution(
            probabilities={
                "home": p_home / total,
                "draw": p_draw / total,
                "away": p_away / total,
            }
        )

    def _expected_points(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        p = self.priors
        home = _positive(context.get("home_expected"))
        away = _positive(context.get("away_expected"))
        if home is None:
            home = (
                p.home_anchor
                * (_side_value(context, "home", "attack") or 1.0)
                * (_side_value(context, "away", "defense") or 1.0)
            )
        if away is None:
            away = (
                p.away_anchor
                * (_side_value(context, "away", "attack") or 1.0)
                * (_side_value(context, "home", "defense") or 1.0)
            )
        return home, away

    def _rest_shift(self, context: Mapping[str, Any]) -> float:
        p = self.priors
        home_rest = _number(context.get("home_rest_days"))
        away_rest = _number(context.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        # The effect saturates: an extra day matters most for tired teams.
        home_fatigue = math.exp(-max(home_rest, 0.0) / p.rest_decay_days)
        away_fatigue = math.exp(-max(away_rest, 0.0) / p.rest_decay_days)
        return p.rest_margin_scale * (away_fatigue - home_fatigue)

    def _possessions(
        self, context: Mapping[str, Any], home_reg: float, away_reg: float
    ) -> float:
        p = self.priors
        implied = (home_reg + away_reg) / (2.0 * p.points_per_possession)

        pace = _positive(context.get("pace"))
        if pace is None:
            pace = _positive(context.get("tempo"))
        if pace is None:
            sides = [
                value
                for value in (
                    _side_value(context, "home", "pace"),
                    _side_value(context, "away", "pace"),
                )
                if value is not None
            ]
            pace = sum(sides) / len(sides) if sides else None

        if pace is None:
            return implied
        return implied * pace if pace < p.pace_possessions_cutoff else pace

    def _regulation_probabilities(
        self, context: Mapping[str, Any], home_reg: float, away_reg: float
    ) -> Tuple[float, float, float]:
        p = self.priors

        # Posterior mean and variance of the true margin under a normal prior.
        baseline = p.home_anchor - p.away_anchor
        reliability = p.matchup_margin_variance / (
            p.matchup_margin_variance + p.mean_error_variance
        )
        raw_margin = home_reg - away_reg + self._rest_shift(context)
        mean = baseline + reliability * (raw_margin - baseline)
        mean_variance = reliability * p.mean_error_variance

        possessions = self._possessions(context, home_reg, away_reg)
        sd = math.sqrt(
            possessions * p.margin_variance_per_possession + mean_variance
        )

        # Margin lattice before the final sequence, with closed-form tails.
        tie_rates = np.asarray(p.tie_by_gap, dtype=float)
        flip_rates = np.asarray(p.flip_by_gap, dtype=float)
        gaps = np.arange(1, tie_rates.size + 1, dtype=float)
        max_gap = float(gaps[-1])

        home_ahead = ndtr((gaps + 0.5 - mean) / sd) - ndtr((gaps - 0.5 - mean) / sd)
        away_ahead = ndtr((-gaps + 0.5 - mean) / sd) - ndtr((-gaps - 0.5 - mean) / sd)
        tied = float(ndtr((0.5 - mean) / sd) - ndtr((-0.5 - mean) / sd))
        home_clear = float(ndtr((mean - max_gap - 0.5) / sd))
        away_clear = float(ndtr((-max_gap - 0.5 - mean) / sd))

        # Share of tie-breaking scores, from relative scoring strength.
        home_break_share = home_reg / (home_reg + away_reg)
        broken_tie = (1.0 - p.tie_retention) * tied
        hold_rates = 1.0 - tie_rates - flip_rates

        p_draw = p.tie_retention * tied + float(
            np.sum(tie_rates * (home_ahead + away_ahead))
        )
        p_home = (
            home_clear
            + float(np.sum(hold_rates * home_ahead))
            + float(np.sum(flip_rates * away_ahead))
            + broken_tie * home_break_share
        )
        p_away = (
            away_clear
            + float(np.sum(hold_rates * away_ahead))
            + float(np.sum(flip_rates * home_ahead))
            + broken_tie * (1.0 - home_break_share)
        )
        return p_home, p_draw, p_away