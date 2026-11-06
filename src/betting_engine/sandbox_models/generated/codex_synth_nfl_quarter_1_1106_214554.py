"""Candidate model for NFL|quarter_1|away_total: compound drive process.

Status and caveats:
- Neither this code nor its priors have been executed, fitted or backtested. Run
  it against the real base class, then evaluate it out of sample against closing
  lines before making any edge claim. There are 0 settled bets, so no leak or
  dispersion has been observed.
- PRIORS are approximate domain values from memory, not fitted estimates. The
  "zero hardcoded constants" goal cannot be met without data, so replace them
  through `priors=` once they are fitted on drive data.
- Q1 mean: 5.38 and 6.12 look like uniform quarter splits of about 21.5 and 24.5,
  so inputs are rescaled to q1_share * game points (away baseline about 4.09, not
  5.38). This q1_share (0.19) is unverified. If the anchors or inputs are true Q1
  means, set q1_share=0.25 to reproduce them. This choice materially moves prices.
- Dispersion: the 1.000 index is ignored. Dispersion emerges from the compound
  structure (Var/mean well above 1), and tempo changes shape, not mean.
- Benchmarks: not used as targets. +295% ROI and +0.4983 delta Brier are
  implausible for a betting market and should be audited for leakage.

Assumed interfaces and schema (not visible, so verify them):
- DiscreteDistribution is importable from the base module or pre-loaded in scope.
- from_scores accepts a matrix indexed [home_points, away_points], with a
  {(home, away): probability} dict as fallback. If it expects raw score samples,
  the probabilities would be misread.
- attack and defense ratings are positive and multiplicative, with higher defense
  meaning more points conceded.
- pace or tempo is either a multiplier near 1 or plays per team-game. Seconds per
  play would be misread.
- away_receives_first is an optional boolean context key.
"""

import math
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.optimize import brentq
from scipy.stats import gamma

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # Resolved from the pre-loaded execution scope at call time.


class NFLQuarter1AwayTotalDriveModel(BaseCandidateModel):
    """Joint Q1 score PMF from a shared drive clock and per-team drive outcomes.

    M, the drives resolved in Q1, is a renewal count with Gamma drive durations:
        P(M >= m) = GammaCDF(T; m * k, tau / k)
    Possessions alternate, so the first receiver gets ceil(M / 2) drives and the
    other team gets floor(M / 2). Shared M induces home/away score correlation.

    Each team's points are S = sum_{i=1..N} X_i, where X has support
    {0, 2, 3, 6, 7, 8}. A logit intensity on FG and TD outcomes is solved so that
    E[N] * E[X] equals the situational Q1 mean. The PMF comes from exact
    convolution, which preserves the empty lattice gaps around key numbers.
    """

    PRIORS: Dict[str, float] = {
        "anchor_home": 6.12,
        "anchor_away": 5.38,
        "q1_share": 0.19,
        "quarter_seconds": 900.0,
        "game_seconds": 3600.0,
        "drives_per_team_game": 11.3,
        "drive_duration_shape": 2.7,
        "plays_per_team_game": 63.0,
        "base_td_rate": 0.23,
        "base_fg_rate": 0.145,
        "td_elasticity": 1.0,
        "fg_elasticity": 0.5,
        "xp_make": 0.945,
        "two_point_rate": 0.02,
        "two_point_make": 0.48,
        "non_offensive_td_per_drive": 0.005,
        "safety_per_drive": 0.0015,
        "rest_effect": 0.03,
        "rest_scale_days": 7.0,
        "matchup_effect": 0.05,
        "wind_reference_mph": 35.0,
        "wind_mean_mph": 8.0,
        "wind_scoring_effect": 0.25,
    }

    _DRIVE_MAX_POINTS = 8
    _TAIL_TOLERANCE = 1e-12  # Numerical precision only, not a modeling clamp.
    _INTENSITY_BOUND = 40.0

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_1_1106_214554",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_1",
        family: str = "away_total",
        priors: Optional[Mapping[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.priors: Dict[str, float] = {**self.PRIORS, **dict(priors or {})}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mu_home, mu_away = self._q1_means(game_context)
        possessions = self._possession_pmf(self._pace_ratio(game_context))
        p_away_first = self._p_away_first(game_context)

        counts = np.arange(possessions.size)
        ceil_half = (counts + 1) // 2
        floor_half = counts // 2
        e_away = float(
            possessions @ (p_away_first * ceil_half + (1.0 - p_away_first) * floor_half)
        )
        e_home = float(
            possessions @ (p_away_first * floor_half + (1.0 - p_away_first) * ceil_half)
        )

        excess_wind = self._excess_wind(game_context)
        fg_factor = math.exp(-excess_wind)
        away_drive = self._drive_pmf(mu_away / e_away, fg_factor)
        home_drive = self._drive_pmf(mu_home / e_home, fg_factor)

        max_drives = int(ceil_half[-1])
        away_powers = self._convolution_powers(away_drive, max_drives)
        home_powers = self._convolution_powers(home_drive, max_drives)

        size = self._DRIVE_MAX_POINTS * max_drives + 1
        joint = np.zeros((size, size), dtype=float)
        for m, p_m in enumerate(possessions):
            first, second = (m + 1) // 2, m // 2
            self._add_branch(
                joint, p_m * p_away_first, home_powers[second], away_powers[first]
            )
            self._add_branch(
                joint, p_m * (1.0 - p_away_first), home_powers[first], away_powers[second]
            )

        return self._to_distribution(self._trim_and_normalize(joint))

    def _q1_means(self, ctx: Mapping[str, Any]) -> "tuple[float, float]":
        """Return situational Q1 means for (home, away)."""
        pr = self.priors
        home = self._positive(self._number(ctx, "home_expected"), pr["anchor_home"])
        away = self._positive(self._number(ctx, "away_expected"), pr["anchor_away"])

        # The geometric midpoint of the quarter and game scales is 2 * anchor sum.
        # Totals below it are treated as uniform quarter splits.
        scale_midpoint = 2.0 * (pr["anchor_home"] + pr["anchor_away"])
        to_game = 4.0 if home + away < scale_midpoint else 1.0
        base_home = pr["q1_share"] * to_game * home
        base_away = pr["q1_share"] * to_game * away

        rest_gap = self._rest_gap(ctx)
        rest_tilt = pr["rest_effect"] * math.tanh(rest_gap / pr["rest_scale_days"])
        matchup_tilt = pr["matchup_effect"] * math.tanh(self._matchup_exposure(ctx))
        away_tilt = rest_tilt + matchup_tilt
        weather = -pr["wind_scoring_effect"] * self._excess_wind(ctx)

        return (
            base_home * math.exp(weather - away_tilt),
            base_away * math.exp(weather + away_tilt),
        )

    def _rest_gap(self, ctx: Mapping[str, Any]) -> float:
        home_rest = self._number(ctx, "home_rest_days")
        away_rest = self._number(ctx, "away_rest_days")
        if home_rest is None or away_rest is None:
            return 0.0
        return away_rest - home_rest

    def _matchup_exposure(self, ctx: Mapping[str, Any]) -> float:
        """Scale-free log matchup edge for the away team over the home team."""
        ratings = (
            self._rating(ctx, "away", "attack"),
            self._rating(ctx, "home", "defense"),
            self._rating(ctx, "home", "attack"),
            self._rating(ctx, "away", "defense"),
        )
        if any(value is None or value <= 0.0 for value in ratings):
            return 0.0
        away_attack, home_defense, home_attack, away_defense = ratings
        return 0.5 * math.log(
            (away_attack * home_defense) / (home_attack * away_defense)
        )

    def _excess_wind(self, ctx: Mapping[str, Any]) -> float:
        """Quadratic wind load relative to the league-average outdoor baseline."""
        pr = self.priors
        weather = ctx.get("weather")
        nested = weather if isinstance(weather, Mapping) else {}
        is_dome = ctx.get("is_dome", nested.get("is_dome", False))
        wind = self._number(ctx, "wind_speed")
        if wind is None:
            wind = self._number(nested, "wind_speed")
        if is_dome:
            wind = 0.0
        elif wind is None:
            wind = pr["wind_mean_mph"]
        return (wind**2 - pr["wind_mean_mph"] ** 2) / pr["wind_reference_mph"] ** 2

    def _pace_ratio(self, ctx: Mapping[str, Any]) -> float:
        value = self._number(ctx, "pace")
        if value is None:
            value = self._number(ctx, "tempo")
        if value is None or value <= 0.0:
            return 1.0
        plays = self.priors["plays_per_team_game"]
        # Choose whichever reading, multiplier or plays per game, is nearer in log space.
        if abs(math.log(value)) < abs(math.log(value / plays)):
            return value
        return value / plays

    @staticmethod
    def _p_away_first(ctx: Mapping[str, Any]) -> float:
        known = ctx.get("away_receives_first")
        if known is None:
            return 0.5  # The coin toss makes the first receiver exogenous pre-game.
        return 1.0 if known else 0.0

    def _possession_pmf(self, pace_ratio: float) -> np.ndarray:
        """Return the PMF of drives resolved in Q1 from a Gamma renewal process."""
        pr = self.priors
        mean_duration = (
            pr["game_seconds"] / (2.0 * pr["drives_per_team_game"]) / pace_ratio
        )
        shape = pr["drive_duration_shape"]
        scale = mean_duration / shape

        survival = [1.0]
        m = 1
        while survival[-1] > self._TAIL_TOLERANCE:
            survival.append(float(gamma.cdf(pr["quarter_seconds"], a=m * shape, scale=scale)))
            m += 1
        survival.append(0.0)

        pmf = -np.diff(np.asarray(survival, dtype=float))
        return pmf / pmf.sum()

    def _drive_pmf(self, target_points: float, fg_factor: float) -> np.ndarray:
        """Solve the drive intensity so that E[X] equals target_points."""
        pr = self.priors
        two_rate = pr["two_point_rate"]
        p_seven = (1.0 - two_rate) * pr["xp_make"]
        p_eight = two_rate * pr["two_point_make"]
        p_six = 1.0 - p_seven - p_eight
        td_points = 6.0 * p_six + 7.0 * p_seven + 8.0 * p_eight

        non_offensive = pr["non_offensive_td_per_drive"]
        safety = pr["safety_per_drive"]
        regular = 1.0 - non_offensive - safety
        td_weight = pr["base_td_rate"]
        fg_weight = pr["base_fg_rate"] * fg_factor
        empty_weight = 1.0 - pr["base_td_rate"] - pr["base_fg_rate"]

        def outcome_probabilities(intensity: float) -> "tuple[float, float, float]":
            fg = fg_weight * math.exp(pr["fg_elasticity"] * intensity)
            td = td_weight * math.exp(pr["td_elasticity"] * intensity)
            total = empty_weight + fg + td
            return empty_weight / total, fg / total, td / total

        def mean_points(intensity: float) -> float:
            _, fg, td = outcome_probabilities(intensity)
            return regular * (3.0 * fg + td_points * td) + 7.0 * non_offensive + 2.0 * safety

        low, high = -self._INTENSITY_BOUND, self._INTENSITY_BOUND
        if not mean_points(low) < target_points < mean_points(high):
            raise ValueError(
                f"Target of {target_points:.4f} points per drive is outside the feasible range."
            )
        intensity = brentq(lambda d: mean_points(d) - target_points, low, high, xtol=1e-10)
        empty, fg, td = outcome_probabilities(intensity)

        pmf = np.zeros(self._DRIVE_MAX_POINTS + 1, dtype=float)
        pmf[0] = regular * empty
        pmf[2] = safety
        pmf[3] = regular * fg
        pmf[6] = regular * td * p_six
        pmf[7] = regular * td * p_seven + non_offensive
        pmf[8] = regular * td * p_eight
        return pmf

    @staticmethod
    def _convolution_powers(drive: np.ndarray, max_drives: int) -> "list[np.ndarray]":
        powers = [np.array([1.0], dtype=float)]
        for _ in range(max_drives):
            powers.append(np.convolve(powers[-1], drive))
        return powers

    @staticmethod
    def _add_branch(
        joint: np.ndarray, weight: float, home_pmf: np.ndarray, away_pmf: np.ndarray
    ) -> None:
        joint[: home_pmf.size, : away_pmf.size] += weight * np.outer(home_pmf, away_pmf)

    def _trim_and_normalize(self, joint: np.ndarray) -> np.ndarray:
        home_support = np.nonzero(joint.sum(axis=1) > self._TAIL_TOLERANCE)[0]
        away_support = np.nonzero(joint.sum(axis=0) > self._TAIL_TOLERANCE)[0]
        trimmed = joint[: home_support[-1] + 1, : away_support[-1] + 1]
        return trimmed / trimmed.sum()

    @staticmethod
    def _to_distribution(matrix: np.ndarray) -> "DiscreteDistribution":
        try:
            return DiscreteDistribution.from_scores(matrix)
        except (TypeError, ValueError, AttributeError):
            scores = {
                (int(home), int(away)): float(matrix[home, away])
                for home, away in zip(*np.nonzero(matrix))
            }
            return DiscreteDistribution.from_scores(scores)

    def _rating(self, ctx: Mapping[str, Any], team: str, kind: str) -> Optional[float]:
        flat = self._number(ctx, f"{team}_{kind}")
        if flat is not None:
            return flat
        nested = ctx.get(team)
        return self._number(nested, kind) if isinstance(nested, Mapping) else None

    @staticmethod
    def _number(ctx: Mapping[str, Any], key: str) -> Optional[float]:
        value = ctx.get(key)
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _positive(value: Optional[float], fallback: float) -> float:
        return value if value is not None and value > 0.0 else fallback