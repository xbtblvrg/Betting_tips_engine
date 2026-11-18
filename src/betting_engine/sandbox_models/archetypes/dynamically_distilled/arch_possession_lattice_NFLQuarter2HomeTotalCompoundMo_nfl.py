import math
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.special import softmax

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The batch execution scope pre-loads this name when base does not export it.
    pass


class NFLQuarter2HomeTotalCompoundModel(BaseCandidateModel):
    """Compound-Poisson scoring lattice for NFL second-quarter team totals.

    Structure
    ---------
    S_side = sum_k v_k * N_k, with v_k in {2, 3, 6, 7, 8} and N_k ~ Poisson(lambda_k).
    lambda_k = Lambda * p_k, where p_k = softmax(alpha_k + q_k * tau) and
    Lambda = expected / sum_k(v_k * p_k), so the mean always equals the expected input.
    The exact PMF comes from Panjer recursion. Var/Mean = sum(v^2 * lambda) / sum(v * lambda).

    Role of possessions
    -------------------
    D * p_k with a solved scale makes D cancel from the event rate once the mean is
    fixed. D therefore acts through points per drive: the same expected points on more
    drives means lower efficiency and a more field-goal-heavy mix. Pace, rest, ratings,
    weather and Q1 state change the shape, not the mean, which avoids double-counting
    anything already priced into the expected input.

    Status and limitations
    ----------------------
    - NOT FITTED AND NOT EXECUTED. Every value in DEFAULT_PARAMS is an illustrative
      prior chosen by judgment, not an estimate. Only the 6.12 and 5.38 baselines come
      from the brief. Fit the rest on historical Q2 scoring data and validate out of
      sample against closing lines before promoting this market.
    - No edge is demonstrated. With 0 settled wagers, this model makes no claim to meet
      the ROI or Brier benchmarks in the brief.
    - The expected inputs must be Q2 points. Full-game values would be used unscaled and
      would badly misprice the market.
    - Attack and defense ratings must be centered on a shared scale, so that their
      difference is near 0 for an average matchup.
    - The Poisson counts do not cap scoring events at the number of drives, and home and
      away scores are independent given the inputs. The home_total marginal does not
      depend on the independence assumption.
    - The DiscreteDistribution.from_scores input format is assumed, since the engine
      source was unavailable: a {(home, away): probability} dict, then a matrix.
    - Optional keys assumed by this class: pace_factor, or pace/tempo with league_pace;
      q1_home_score and q1_away_score; q2_start_possession ('home' or 'away').
    """

    SCORE_VALUES = np.array([2, 3, 6, 7, 8], dtype=int)
    # Loading on the touchdown-versus-field-goal axis; safeties do not load.
    TD_AXIS_LOADING = np.array([0.0, -1.0, 1.0, 1.0, 1.0])

    DEFAULT_PARAMS: Dict[str, Any] = {
        "baseline_home_expected": 6.12,
        "baseline_away_expected": 5.38,
        "event_shares": (0.006, 0.420, 0.035, 0.520, 0.019),  # 2, 3, 6, 7, 8 points
        "base_drives": 2.75,            # team drives at reference pace
        "two_minute_net_drives": 0.25,  # drill gain net of half-ending truncation
        "rating_tilt": 0.50,            # per unit of attack minus opposing defense
        "rest_tilt": 0.01,              # per day of rest advantage
        "efficiency_tilt": 0.50,        # per log unit of points-per-drive ratio
        "wind_tilt": 0.012,             # per mph above reference
        "reference_wind_mph": 6.0,
        "carryover_log_drives": 0.12,   # opening Q2 with or without the ball
        "q1_margin_log_drives": 0.005,  # per Q1 point of lead
        "tail_sd_multiple": 12.0,       # lattice truncation width
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_2_1106_195344",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_2",
        family: str = "home_total",
        params: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.params = {**self.DEFAULT_PARAMS, **dict(params or {})}
        self._alpha = np.log(np.asarray(self.params["event_shares"], dtype=float))

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        home_pmf = self.side_score_pmf(game_context, "home")
        away_pmf = self.side_score_pmf(game_context, "away")
        joint = np.outer(home_pmf, away_pmf)

        scores = {
            (int(home), int(away)): float(joint[home, away])
            for home, away in zip(*np.nonzero(joint))
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(joint)

    def side_score_pmf(self, game_context: Mapping[str, Any], side: str) -> np.ndarray:
        """Return P(S = s) for s = 0..support_max for 'home' or 'away'."""
        expected = self._expected_points(game_context, side)
        drives = self._drives(game_context, side)
        tau = self._td_axis_tilt(game_context, side, expected, drives)
        shares = softmax(self._alpha + self.TD_AXIS_LOADING * tau)
        return self._compound_poisson_pmf(expected, shares)

    def _compound_poisson_pmf(self, expected: float, shares: np.ndarray) -> np.ndarray:
        values = self.SCORE_VALUES
        event_rate = expected / float(np.dot(values, shares))
        variance = event_rate * float(np.dot(values.astype(float) ** 2, shares))
        support_max = int(
            math.ceil(expected + self.params["tail_sd_multiple"] * math.sqrt(variance))
        ) + int(values.max())

        pmf = np.zeros(support_max + 1)
        pmf[0] = math.exp(-event_rate)
        for score in range(1, support_max + 1):
            reachable = values <= score
            pmf[score] = (event_rate / score) * float(
                np.sum(
                    values[reachable] * shares[reachable] * pmf[score - values[reachable]]
                )
            )
        return pmf / pmf.sum()

    def _expected_points(self, ctx: Mapping[str, Any], side: str) -> float:
        expected = self._number(ctx.get(f"{side}_expected"))
        if expected is None or expected <= 0.0:
            return float(self.params[f"baseline_{side}_expected"])
        return expected

    def _reference_drives(self) -> float:
        return float(self.params["base_drives"] + self.params["two_minute_net_drives"])

    def _drives(self, ctx: Mapping[str, Any], side: str) -> float:
        p = self.params
        regulation = p["base_drives"] * self._pace_factor(ctx)
        log_shift = (
            p["carryover_log_drives"] * self._carryover_sign(ctx, side)
            - p["q1_margin_log_drives"] * self._q1_margin(ctx, side)
        )
        return float((regulation + p["two_minute_net_drives"]) * math.exp(log_shift))

    def _td_axis_tilt(
        self, ctx: Mapping[str, Any], side: str, expected: float, drives: float
    ) -> float:
        p = self.params
        baseline = float(p[f"baseline_{side}_expected"])
        efficiency = math.log(
            (expected / drives) / (baseline / self._reference_drives())
        )
        return float(
            p["rating_tilt"] * self._rating_edge(ctx, side)
            + p["rest_tilt"] * self._rest_edge(ctx, side)
            + p["efficiency_tilt"] * efficiency
            + p["wind_tilt"] * (self._wind_mph(ctx) - p["reference_wind_mph"])
        )

    def _pace_factor(self, ctx: Mapping[str, Any]) -> float:
        factor = self._number(ctx.get("pace_factor"))
        if factor is not None and factor > 0.0:
            return factor
        pace = self._number(ctx.get("pace", ctx.get("tempo")))
        league_pace = self._number(ctx.get("league_pace"))
        if pace is not None and league_pace is not None and pace > 0.0 and league_pace > 0.0:
            return pace / league_pace
        # Raw pace has unknown units without a league reference, so it stays neutral.
        return 1.0

    def _rating_edge(self, ctx: Mapping[str, Any], side: str) -> float:
        opponent = self._opponent(side)
        attack = self._team_rating(ctx, side, "attack")
        defense = self._team_rating(ctx, opponent, "defense")
        return attack - defense if attack is not None and defense is not None else 0.0

    def _team_rating(self, ctx: Mapping[str, Any], side: str, rating: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, Mapping) and rating in block:
            return self._number(block.get(rating))
        return self._number(ctx.get(f"{side}_{rating}"))

    def _rest_edge(self, ctx: Mapping[str, Any], side: str) -> float:
        own = self._number(ctx.get(f"{side}_rest_days"))
        other = self._number(ctx.get(f"{self._opponent(side)}_rest_days"))
        return own - other if own is not None and other is not None else 0.0

    def _wind_mph(self, ctx: Mapping[str, Any]) -> float:
        weather = ctx.get("weather")
        merged = {**(weather if isinstance(weather, Mapping) else {}), **ctx}
        if merged.get("is_dome"):
            return 0.0
        wind = self._number(merged.get("wind_speed"))
        return wind if wind is not None else float(self.params["reference_wind_mph"])

    def _q1_margin(self, ctx: Mapping[str, Any], side: str) -> float:
        home = self._number(ctx.get("q1_home_score"))
        away = self._number(ctx.get("q1_away_score"))
        if home is None or away is None:
            return 0.0
        return home - away if side == "home" else away - home

    def _carryover_sign(self, ctx: Mapping[str, Any], side: str) -> float:
        possession = ctx.get("q2_start_possession")
        if possession == side:
            return 1.0
        return -1.0 if possession == self._opponent(side) else 0.0

    @staticmethod
    def _opponent(side: str) -> str:
        return "away" if side == "home" else "home"

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None