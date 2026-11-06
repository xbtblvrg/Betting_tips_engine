import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # DiscreteDistribution is pre-loaded into the sandbox execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NbaFullGameThreeWayModel(BaseCandidateModel):
    """Regulation-time home/draw/away model for NBA three-way markets.

    STATUS - READ BEFORE PROMOTING
    - This code has not been executed or backtested. The segment has 0 settled
      bets, so nothing here is fitted, and no ROI or Brier skill is claimed. The
      quoted benchmarks (e.g. +122% ROI) are not plausible as a sustained edge
      in this market, so treat them as a sign of small samples or leakage.
    - The class-level values are structural priors from general NBA knowledge,
      not empirical estimates. "Zero hardcoded constants" is not achievable with
      no data, so they are named here for refitting once bets settle. None of
      them is a probability clamp or an odds filter.
    - Assumes the market settles on the regulation result, with overtime
      excluded. "draw" means tied after 48 minutes. Check this against the
      settlement rules before use.

    STRUCTURE
    1. Means: home_expected / away_expected are used when present. Attack and
       defense ratings only build the means when those are absent, to avoid
       double counting. Rest then adjusts the margin.
    2. Mean uncertainty: the expected margin is a noisy signal. It is shrunk
       towards the league prior, and the remaining uncertainty is integrated
       out. This limits home over-confidence and keeps underdog tails realistic.
    3. Lattice: the margin is a sum of paired possessions scoring 0/1/2/3 points.
       Independent possessions overstate margin variance because of mean
       reversion (garbage time, rotation effects), so the effective possession
       count is set to match the prior margin SD and scales with pace and
       scoring mix.
    4. Exact mean: exponential tilting hits each target margin while preserving
       the integer lattice.
    5. Endgame: a one-step transition on close margins models last shots and
       intentional fouling. It lifts tie mass above what a Gaussian density
       implies, to roughly the observed NBA overtime rate of about 6%.

    INPUT ASSUMPTIONS (unverified against the real game_context schema)
    - Rest days: 0 means a back-to-back.
    - Ratings: values near 1.0 are multipliers, and values above
      RATING_SCALE_CUTOFF are points per 100 possessions. Higher defense means
      more points conceded.
    - Rest may already be priced into home_expected / away_expected. If so, set
      REST_EFFECT_POINTS to 0.
    - Weather is ignored because NBA games are played indoors.
    """

    # Anchors from the segment specification.
    BASE_HOME_POINTS = 112.0
    BASE_AWAY_POINTS = 108.0
    BASE_POSSESSIONS = 100.0

    # Share of points by points scored on a possession (1, 2, 3).
    POINT_SHARE = {1: 0.05, 2: 0.58, 3: 0.37}

    # Margin priors, in points.
    GAME_MARGIN_SD = 11.7        # Game-to-game SD around the true expected margin.
    SIGNAL_NOISE_SD = 3.0        # Error in the supplied expected margin.
    TRUE_MARGIN_PRIOR_SD = 6.0   # Spread of true expected margins across games.
    QUADRATURE_NODES = 9

    # Rest: effect(r) = -REST_EFFECT_POINTS * exp(-r / REST_DECAY_DAYS).
    REST_EFFECT_POINTS = 2.0
    REST_DECAY_DAYS = 1.0

    RATING_SCALE_CUTOFF = 10.0

    # Endgame: from a tie, chance of a decisive score, and its size.
    TIE_BREAK_PROB = 0.40
    TIE_BREAK_GAIN = {1: 0.10, 2: 0.70, 3: 0.20}

    # Endgame: lead -> [(new lead for the same team, probability)].
    # 0 is a tie, a negative value is a reversal, and the remainder stays put.
    LEAD_TRANSITIONS = {
        1: ((0, 0.12), (-1, 0.30), (3, 0.33)),
        2: ((0, 0.30), (-1, 0.10), (4, 0.35)),
        3: ((0, 0.20), (5, 0.45)),
        4: ((6, 0.50),),
        5: ((7, 0.50),),
        6: ((8, 0.50),),
    }
    ENDGAME_REACH = 8

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_232936",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "three_way",
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        home_points, away_points = self._expected_points(game_context)
        possessions = self._possessions(game_context, home_points, away_points)

        step = self._paired_possession_pmf(home_points / possessions, away_points / possessions)
        lattice_log_pmf, margins = self._lattice_log_pmf(step, possessions)

        pmf = self._integrate_mean_uncertainty(lattice_log_pmf, margins, home_points - away_points)
        home_scoring_share = home_points / (home_points + away_points)
        pmf, margins = self._apply_endgame(pmf, margins, home_scoring_share)

        p_home = float(pmf[margins > 0].sum())
        p_draw = float(pmf[margins == 0].sum())
        p_away = float(pmf[margins < 0].sum())
        total = p_home + p_draw + p_away
        return DiscreteDistribution(
            probabilities={
                "home": p_home / total,
                "draw": p_draw / total,
                "away": p_away / total,
            }
        )

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    @staticmethod
    def _positive(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0.0 else None

    @staticmethod
    def _non_negative(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0.0 else None

    def _rating(self, context: Dict[str, Any], side: str, kind: str) -> float:
        """Return a team rating as a multiplier, where 1.0 is league average."""
        nested = context.get(side)
        raw = nested.get(kind) if isinstance(nested, dict) else None
        if raw is None:
            raw = context.get(f"{side}_{kind}")
        value = self._positive(raw)
        if value is None:
            return 1.0
        if value > self.RATING_SCALE_CUTOFF:
            league_per_100 = (
                50.0 * (self.BASE_HOME_POINTS + self.BASE_AWAY_POINTS) / self.BASE_POSSESSIONS
            )
            return value / league_per_100
        return value

    def _expected_points(self, context: Dict[str, Any]) -> Tuple[float, float]:
        home = self._positive(context.get("home_expected"))
        away = self._positive(context.get("away_expected"))
        if home is None:
            home = (
                self.BASE_HOME_POINTS
                * self._rating(context, "home", "attack")
                * self._rating(context, "away", "defense")
            )
        if away is None:
            away = (
                self.BASE_AWAY_POINTS
                * self._rating(context, "away", "attack")
                * self._rating(context, "home", "defense")
            )

        home_rest = self._non_negative(context.get("home_rest_days"))
        away_rest = self._non_negative(context.get("away_rest_days"))
        if home_rest is not None and away_rest is not None:
            adjustment = self._rest_effect(home_rest) - self._rest_effect(away_rest)
            home += adjustment / 2.0
            away -= adjustment / 2.0
        return home, away

    def _rest_effect(self, rest_days: float) -> float:
        return -self.REST_EFFECT_POINTS * math.exp(-rest_days / self.REST_DECAY_DAYS)

    def _possessions(self, context: Dict[str, Any], home_points: float, away_points: float) -> float:
        pace = self._positive(context.get("pace"))
        if pace is None:
            home_pace = self._positive(context.get("home_pace"))
            away_pace = self._positive(context.get("away_pace"))
            if home_pace is not None and away_pace is not None:
                pace = (home_pace + away_pace) / 2.0
        if pace is None:
            # With no pace input, split a total's deviation evenly between
            # tempo and efficiency, hence the square root.
            base_total = self.BASE_HOME_POINTS + self.BASE_AWAY_POINTS
            pace = self.BASE_POSSESSIONS * math.sqrt((home_points + away_points) / base_total)

        # Feasibility bound: the probabilities of scoring must not exceed 1.
        scoring_rate_per_point = sum(share / k for k, share in self.POINT_SHARE.items())
        return max(pace, max(home_points, away_points) * scoring_rate_per_point)

    # ------------------------------------------------------------------
    # Margin lattice
    # ------------------------------------------------------------------
    def _team_possession_pmf(self, points_per_possession: float) -> np.ndarray:
        pmf = np.zeros(4)
        for k, share in self.POINT_SHARE.items():
            pmf[k] = points_per_possession * share / k
        pmf[0] = max(1.0 - pmf[1:].sum(), 0.0)
        return pmf / pmf.sum()

    def _paired_possession_pmf(self, home_ppp: float, away_ppp: float) -> np.ndarray:
        """Return the pmf of (home - away) points on margins -3..+3."""
        home = self._team_possession_pmf(home_ppp)
        away = self._team_possession_pmf(away_ppp)
        return np.convolve(home, away[::-1])

    @staticmethod
    def _step_variance(step: np.ndarray) -> float:
        values = np.arange(-3, 4)
        mean = float(np.dot(values, step))
        return float(np.dot((values - mean) ** 2, step))

    @staticmethod
    def _convolution_power(step: np.ndarray, power: int) -> np.ndarray:
        result = np.array([1.0])
        base = step.copy()
        while power > 0:
            if power & 1:
                result = np.convolve(result, base)
            power >>= 1
            if power:
                base = np.convolve(base, base)
        return result

    def _lattice_log_pmf(self, step: np.ndarray, possessions: float) -> Tuple[np.ndarray, np.ndarray]:
        """Build the margin lattice at the effective possession count.

        Requiring var = GAME_MARGIN_SD^2 at the anchors gives
        effective = GAME_MARGIN_SD^2 * (N / N0) / base_step_variance.
        Game variance therefore scales with pace and scoring mix, and the ratio
        of effective to actual possessions is the implied mean reversion.
        """
        base_step = self._paired_possession_pmf(
            self.BASE_HOME_POINTS / self.BASE_POSSESSIONS,
            self.BASE_AWAY_POINTS / self.BASE_POSSESSIONS,
        )
        effective = (
            self.GAME_MARGIN_SD ** 2
            * (possessions / self.BASE_POSSESSIONS)
            / self._step_variance(base_step)
        )

        lower = max(int(math.floor(effective)), 1)
        weight = min(effective - lower, 1.0) if effective > lower else 0.0

        low_pmf = self._convolution_power(step, lower)
        high_pmf = np.convolve(low_pmf, step)
        pmf = (1.0 - weight) * np.pad(low_pmf, 3) + weight * high_pmf

        reach = 3 * (lower + 1)
        margins = np.arange(-reach, reach + 1, dtype=float)
        with np.errstate(divide="ignore"):
            return np.log(pmf), margins

    @staticmethod
    def _tilt_to_mean(log_pmf: np.ndarray, margins: np.ndarray, target: float) -> np.ndarray:
        """Exponentially tilt the pmf so that its mean equals target."""

        def tilted(theta: float) -> np.ndarray:
            log_weights = log_pmf + theta * margins
            return np.exp(log_weights - logsumexp(log_weights))

        def gap(theta: float) -> float:
            return float(np.dot(margins, tilted(theta))) - target

        low, high = -0.25, 0.25
        for _ in range(40):
            if gap(low) <= 0.0:
                break
            low *= 2.0
        for _ in range(40):
            if gap(high) >= 0.0:
                break
            high *= 2.0
        return tilted(brentq(gap, low, high, xtol=1e-10))

    def _integrate_mean_uncertainty(
        self, log_pmf: np.ndarray, margins: np.ndarray, signal_margin: float
    ) -> np.ndarray:
        """Apply the normal-normal posterior over the true expected margin."""
        prior_mean = self.BASE_HOME_POINTS - self.BASE_AWAY_POINTS
        prior_var = self.TRUE_MARGIN_PRIOR_SD ** 2
        noise_var = self.SIGNAL_NOISE_SD ** 2

        posterior_var = prior_var * noise_var / (prior_var + noise_var)
        posterior_mean = (signal_margin * prior_var + prior_mean * noise_var) / (prior_var + noise_var)

        nodes, weights = np.polynomial.hermite_e.hermegauss(self.QUADRATURE_NODES)
        weights = weights / weights.sum()

        mixture = np.zeros_like(margins)
        for node, node_weight in zip(nodes, weights):
            target = posterior_mean + math.sqrt(posterior_var) * float(node)
            mixture += float(node_weight) * self._tilt_to_mean(log_pmf, margins, target)
        return mixture

    # ------------------------------------------------------------------
    # Endgame
    # ------------------------------------------------------------------
    def _apply_endgame(
        self, pmf: np.ndarray, margins: np.ndarray, home_scoring_share: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        padded = np.pad(pmf, self.ENDGAME_REACH)
        reach = int(margins[-1]) + self.ENDGAME_REACH
        out_margins = np.arange(-reach, reach + 1, dtype=float)
        zero = reach

        result = padded.copy()

        tie_moved = padded[zero] * self.TIE_BREAK_PROB
        result[zero] -= tie_moved
        for gain, share in self.TIE_BREAK_GAIN.items():
            result[zero + gain] += tie_moved * share * home_scoring_share
            result[zero - gain] += tie_moved * share * (1.0 - home_scoring_share)

        for lead, transitions in self.LEAD_TRANSITIONS.items():
            for sign in (1, -1):
                source = zero + sign * lead
                mass = padded[source]
                for new_lead, probability in transitions:
                    moved = mass * probability
                    result[source] -= moved
                    result[zero + sign * new_lead] += moved

        return result, out_margins