# Plan: model Q2 away points as a compound drive process (drives -> scoring events -> 2/3/6/7/8-point
# values) and return a joint home/away score lattice. No bookmaker prices are used.
#
# REVIEW BEFORE PROMOTION:
# - Status: this code has not been run or backtested, and no performance is claimed. It isn't shown to
#   meet the listed ROI/Brier benchmarks. A +295% ROI or +0.4983 Brier delta likely signals leakage or a
#   tiny sample, so audit those figures before using them as targets.
# - Priors: with 0 settled bets, the class-level values are unfitted domain priors, so "zero hardcoded
#   constants" isn't met. Fit them on historical Q2 scores.
# - Baseline: 6.12 + 5.38 = 11.50 looks like a game total split evenly by quarter. Q2 usually scores above
#   a quarter-average rate, so check for downward bias against actual Q2 means.
# - Dispersion: the 1.000 index is a placeholder and isn't used. This lattice implies Var/Mean well above
#   1 (roughly 4-5 at baseline) because points arrive in 3s and 7s.
# - Assumed interfaces, unverified:
#     * DiscreteDistribution.from_scores accepts {(home_pts, away_pts): probability}.
#     * BaseCandidateModel.__init__ accepts these five keyword arguments.
#     * home_expected/away_expected are Q2-level means; values above FULL_GAME_THRESHOLD are read as
#       full-game means.
#     * Higher defense means more points allowed.
#     * Pace is a multiplier centered on 1.0.
from typing import Any, Dict, Optional

import numpy as np
from scipy.special import betaln, comb, expit, logit

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # DiscreteDistribution is documented as pre-loaded in the execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NFLQuarter2AwayTotalModel(BaseCandidateModel):
    """Drive-based Q2 score model with a shared drive count and uncertain per-drive scoring rates."""

    # Anchors supplied with the segment.
    BASE_HOME_MEAN = 6.12
    BASE_AWAY_MEAN = 5.38

    # Unfitted domain priors.
    Q2_DRIVES = 3.0
    DRIVE_SD = 0.85
    Q2_SHARE_OF_GAME = 0.30
    FULL_GAME_THRESHOLD = 14.0
    MAX_SCORING_RATE = 0.75  # Raises the drive count for extreme means so scoring rates stay feasible.
    RATE_CONCENTRATION = 15.0  # Beta concentration: forecast uncertainty in per-drive scoring rate.

    TD_SHARE = 0.56
    TD_SHARE_MEAN_ELASTICITY = 0.35
    TD_SHARE_WIND_SLOPE = 0.03
    SAFETY_SHARE = 0.004
    TD_VALUE_PMF = {6: 0.05, 7: 0.92, 8: 0.03}

    # Inputs such as expected points probably already reflect these effects, so they get partial weight
    # when an expected value is supplied. This limits double counting.
    PRICED_IN_WEIGHT = 0.25
    REST_EFFECT = 0.02
    REST_SCALE_DAYS = 7.0
    WIND_EFFECT_PER_MPH = 0.007
    WIND_ONSET_MPH = 10.0
    WIND_SMOOTHING_MPH = 2.0
    COLD_EFFECT_PER_DEGREE = 0.003
    COLD_ONSET_F = 32.0
    COLD_SMOOTHING_F = 5.0

    MIN_REPORTED_PROBABILITY = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_2_1107_062234",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_2",
        family: str = "away_total",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        context = game_context or {}
        wind_excess, cold_excess = self._weather_excess(context)
        pace = self._pace(context)

        common_effect = -(
            self.WIND_EFFECT_PER_MPH * wind_excess + self.COLD_EFFECT_PER_DEGREE * cold_excess
        )
        away_edge = self._matchup_log_tilt(context) + self._rest_log_effect(context)

        home_mean = self._team_mean(
            context.get("home_expected"), self.BASE_HOME_MEAN, common_effect - away_edge, pace
        )
        away_mean = self._team_mean(
            context.get("away_expected"), self.BASE_AWAY_MEAN, common_effect + away_edge, pace
        )

        home_td_share = self._td_share(home_mean, self.BASE_HOME_MEAN, wind_excess)
        away_td_share = self._td_share(away_mean, self.BASE_AWAY_MEAN, wind_excess)
        home_event = self._event_pmf(home_td_share)
        away_event = self._event_pmf(away_td_share)
        points = np.arange(home_event.size)
        home_value = float(points @ home_event)
        away_value = float(points @ away_event)

        # Pace changes drive volume. The scoring rate below adjusts so team means are preserved.
        required_drives = max(home_mean / home_value, away_mean / away_value) / self.MAX_SCORING_RATE
        drive_center = max(self.Q2_DRIVES * pace, required_drives)
        drive_weights = self._drive_weights(drive_center)
        expected_drives = float(np.arange(drive_weights.size) @ drive_weights)

        home_given_drives = self._points_given_drives(
            home_mean / (expected_drives * home_value), home_event, drive_weights.size - 1
        )
        away_given_drives = self._points_given_drives(
            away_mean / (expected_drives * away_value), away_event, drive_weights.size - 1
        )

        # Teams alternate possessions, so both share a drive count. They are conditionally independent
        # given that count, which adds positive correlation through game tempo.
        joint = np.zeros((home_given_drives.shape[1], away_given_drives.shape[1]))
        for drives, weight in enumerate(drive_weights):
            joint += weight * np.outer(home_given_drives[drives], away_given_drives[drives])

        rows, columns = np.nonzero(joint > self.MIN_REPORTED_PROBABILITY)
        retained = joint[rows, columns]
        retained = retained / retained.sum()
        scores = {
            (int(home_points), int(away_points)): float(probability)
            for home_points, away_points, probability in zip(rows, columns, retained)
        }
        return DiscreteDistribution.from_scores(scores)

    def _team_mean(
        self, expected: Any, baseline: float, log_effect: float, pace: float
    ) -> float:
        value = self._as_float(expected)
        if value is not None and value > 0.0:
            if value > self.FULL_GAME_THRESHOLD:
                value *= self.Q2_SHARE_OF_GAME
            return value * float(np.exp(self.PRICED_IN_WEIGHT * log_effect))
        return baseline * pace * float(np.exp(log_effect))

    def _td_share(self, mean: float, baseline: float, wind_excess: float) -> float:
        # Stronger offenses finish more drives in the end zone; wind makes field goals less viable.
        return float(
            expit(
                logit(self.TD_SHARE)
                + self.TD_SHARE_MEAN_ELASTICITY * np.log(mean / baseline)
                + self.TD_SHARE_WIND_SLOPE * wind_excess
            )
        )

    def _event_pmf(self, td_share: float) -> np.ndarray:
        non_safety = 1.0 - self.SAFETY_SHARE
        pmf = np.zeros(max(self.TD_VALUE_PMF) + 1)
        pmf[2] = self.SAFETY_SHARE
        pmf[3] = non_safety * (1.0 - td_share)
        for value, probability in self.TD_VALUE_PMF.items():
            pmf[value] = non_safety * td_share * probability
        return pmf

    def _drive_weights(self, center: float) -> np.ndarray:
        max_drives = int(np.ceil(center + 4.0 * self.DRIVE_SD))
        drives = np.arange(max_drives + 1)
        weights = np.exp(-0.5 * ((drives - center) / self.DRIVE_SD) ** 2)
        return weights / weights.sum()

    def _points_given_drives(
        self, scoring_rate: float, event_pmf: np.ndarray, max_drives: int
    ) -> np.ndarray:
        """Return P(points | drives), using a Beta-Binomial count of scoring drives."""
        max_value = event_pmf.size - 1
        size = max_drives * max_value + 1

        event_sums = np.zeros((max_drives + 1, size))
        event_sums[0, 0] = 1.0
        current = np.array([1.0])
        for events in range(1, max_drives + 1):
            current = np.convolve(current, event_pmf)
            event_sums[events, : current.size] = current

        alpha = scoring_rate * self.RATE_CONCENTRATION
        beta = (1.0 - scoring_rate) * self.RATE_CONCENTRATION
        result = np.zeros((max_drives + 1, size))
        for drives in range(max_drives + 1):
            events = np.arange(drives + 1)
            event_count_pmf = comb(drives, events) * np.exp(
                betaln(events + alpha, drives - events + beta) - betaln(alpha, beta)
            )
            result[drives] = event_count_pmf @ event_sums[: drives + 1]
        return result

    def _matchup_log_tilt(self, context: Dict[str, Any]) -> float:
        # This ratio cancels the rating scale, so per-game and normalized ratings both work.
        away_attack = self._team_rating(context, "away", "attack")
        home_defense = self._team_rating(context, "home", "defense")
        home_attack = self._team_rating(context, "home", "attack")
        away_defense = self._team_rating(context, "away", "defense")
        ratings = (away_attack, home_defense, home_attack, away_defense)
        if any(rating is None or rating <= 0.0 for rating in ratings):
            return 0.0
        return 0.5 * float(np.log((away_attack * home_defense) / (home_attack * away_defense)))

    def _rest_log_effect(self, context: Dict[str, Any]) -> float:
        home_rest = self._as_float(context.get("home_rest_days"))
        away_rest = self._as_float(context.get("away_rest_days"))
        if home_rest is None or away_rest is None:
            return 0.0
        return self.REST_EFFECT * float(np.tanh((away_rest - home_rest) / self.REST_SCALE_DAYS))

    def _weather_excess(self, context: Dict[str, Any]) -> tuple:
        if self._is_dome(context):
            return 0.0, 0.0
        wind = self._lookup_number(context, "wind_speed")
        temperature = self._lookup_number(context, "temp")
        wind_excess = (
            0.0
            if wind is None
            else self._softplus(wind - self.WIND_ONSET_MPH, self.WIND_SMOOTHING_MPH)
        )
        cold_excess = (
            0.0
            if temperature is None
            else self._softplus(self.COLD_ONSET_F - temperature, self.COLD_SMOOTHING_F)
        )
        return wind_excess, cold_excess

    def _pace(self, context: Dict[str, Any]) -> float:
        for key in ("pace_factor", "tempo", "pace"):
            value = self._as_float(context.get(key))
            # Values outside this range are probably raw plays or seconds, not a relative multiplier.
            if value is not None and 0.5 < value < 2.0:
                return value
        return 1.0

    def _is_dome(self, context: Dict[str, Any]) -> bool:
        for source in self._context_sources(context):
            if source.get("is_dome") is not None:
                return bool(source["is_dome"])
        return False

    def _lookup_number(self, context: Dict[str, Any], key: str) -> Optional[float]:
        for source in self._context_sources(context):
            value = self._as_float(source.get(key))
            if value is not None:
                return value
        return None

    def _team_rating(self, context: Dict[str, Any], side: str, rating: str) -> Optional[float]:
        team = context.get(side)
        if isinstance(team, dict):
            value = self._as_float(team.get(rating))
            if value is not None:
                return value
        return self._as_float(context.get(f"{side}_{rating}"))

    @staticmethod
    def _context_sources(context: Dict[str, Any]) -> list:
        sources = [context]
        for key in ("weather", "venue"):
            nested = context.get(key)
            if isinstance(nested, dict):
                sources.append(nested)
        return sources

    @staticmethod
    def _softplus(value: float, scale: float) -> float:
        return scale * float(np.logaddexp(0.0, value / scale))

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if np.isfinite(number) else None