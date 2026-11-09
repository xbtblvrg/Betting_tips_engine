from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy import sparse
from scipy.optimize import least_squares
from scipy.sparse.linalg import expm_multiply
from scipy.stats import poisson, skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NHLFullGameExactScoreCandidate(BaseCandidateModel):
    """
    Deterministic NHL exact-score model with explicit settlement semantics.

    Assumption: full-game settlement includes overtime and credits the shootout
    winner with one additional goal. Regulation ties therefore become adjacent
    off-diagonal scores. Markets using different settlement rules are rejected.

    The supplied 3.10/2.80 means are interpreted as FULL-GAME means by default.
    Regulation intensities are calibrated so the final distribution preserves
    those means. Set expected_basis="regulation" when inputs describe regulation.

    Cold-start identification:
        Means and a dispersion index of one do not identify score effects,
        goalie-pull behavior, overtime strength, or rest effects. Their neutral
        defaults are independent regulation scoring, no rest adjustment, and
        symmetric tie resolution. These defaults are assumptions, not estimates.
        There is no empirical evidence here establishing an existing cash leak,
        excess ties, overdispersion, or attainable betting ROI.

    Optional fitted mechanics:
        trailing_log_rate: scoring-rate effect when trailing.
        leading_log_rate: scoring-rate effect when leading.
        late_tie_log_rate: scoring-rate effect while tied in the late window.
        late_tie_start: start of that window, as a regulation-time fraction.
        pull_start: start of the goalie-pull window.
        pull_probabilities: {positive_deficit: fitted_pull_probability}.
        pulled_team_log_rate: own scoring-rate effect after pulling.
        empty_net_log_rate: opponent scoring-rate effect against an empty net.

    A pull probability defines an effective conditional hazard, rather than a
    latent, persistent goalie state. Estimate these hazards from goal counts
    and state exposure durations, using only training data available before the
    forecast. A persistent pull-state model requires additional state variables.

    Regulation is a continuous-time pure-birth chain on (home_score, away_score).
    Only one goal can occur at a jump; correlation arises from state dependence,
    not artificial simultaneous goals. Piecewise-constant generators are solved
    by sparse matrix exponentiation. An absorbing overflow state measures lost
    mass. Numerical tolerance and max_states are computational controls, not
    probability clamps or betting filters.

    Situational inputs:
        home_expected, away_expected: positive means in expected_basis.
        home/away: mappings containing positive attack and defense multipliers;
            defense means opponent scoring exposure, not defensive strength.
            These multipliers apply only when that team's expected mean is absent.
        pace: positive common exposure multiplier.
        home_rest_days, away_rest_days: nonnegative; their difference enters with
            fitted rest_log_rate_coefficient.
        home_ot_win_probability, home_shootout_win_probability,
        ot_resolution_probability: optional separately fitted probabilities.

    The overtime mixture is:
        P(home wins tie) = w * P(home wins in OT)
                          + (1-w) * P(home wins shootout).
    With no evidence, both conditional win probabilities equal one half, so the
    unidentified mixture weight has no effect. No bookmaker price enters forecasts.

    Mechanics, rest, and overtime parameters must be estimated externally and
    evaluated with chronological holdouts using log loss, Brier scores, calibration,
    and settlement-consistent returns. This class does not claim they are fitted.
    """

    BASE_HOME_MEAN = 3.10
    BASE_AWAY_MEAN = 2.80
    MINIMUM_DECIMAL_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_full_game_1108_042701",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "full_game",
        family: str = "exact_score",
        *,
        mechanics: Optional[Mapping[str, Any]] = None,
        expected_basis: str = "full_game",
        rest_log_rate_coefficient: float = 0.0,
        home_ot_win_probability: float = 0.5,
        home_shootout_win_probability: float = 0.5,
        ot_resolution_probability: float = 0.5,
        numerical_tolerance: float = 1e-10,
        max_states: int = 250_000,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("NHL", "full_game", "exact_score"):
            raise ValueError("This candidate supports only NHL|full_game|exact_score.")
        if expected_basis not in {"full_game", "regulation"}:
            raise ValueError("expected_basis must be 'full_game' or 'regulation'.")

        self.expected_basis = expected_basis
        self.rest_coefficient = self._finite(
            rest_log_rate_coefficient, "rest_log_rate_coefficient"
        )
        self.ot_home = self._probability(
            home_ot_win_probability, "home_ot_win_probability"
        )
        self.so_home = self._probability(
            home_shootout_win_probability, "home_shootout_win_probability"
        )
        self.ot_resolution = self._probability(
            ot_resolution_probability, "ot_resolution_probability"
        )
        self.tolerance = self._positive(numerical_tolerance, "numerical_tolerance")
        if not np.finfo(float).eps < self.tolerance < 1.0:
            raise ValueError("numerical_tolerance must lie between machine epsilon and one.")
        if isinstance(max_states, bool) or int(max_states) != max_states or max_states < 2:
            raise ValueError("max_states must be an integer of at least two.")
        self.max_states = int(max_states)

        cfg = dict(mechanics or {})
        allowed = {
            "trailing_log_rate",
            "leading_log_rate",
            "late_tie_log_rate",
            "late_tie_start",
            "pull_start",
            "pull_probabilities",
            "pulled_team_log_rate",
            "empty_net_log_rate",
        }
        unknown = set(cfg) - allowed
        if unknown:
            raise ValueError(f"Unknown mechanics fields: {sorted(unknown)}")

        self.trailing_factor = self._exp(
            self._finite(cfg.get("trailing_log_rate", 0.0), "trailing_log_rate")
        )
        self.leading_factor = self._exp(
            self._finite(cfg.get("leading_log_rate", 0.0), "leading_log_rate")
        )
        self.tie_factor = self._exp(
            self._finite(cfg.get("late_tie_log_rate", 0.0), "late_tie_log_rate")
        )
        self.pull_for_factor = self._exp(
            self._finite(cfg.get("pulled_team_log_rate", 0.0), "pulled_team_log_rate")
        )
        self.pull_against_factor = self._exp(
            self._finite(cfg.get("empty_net_log_rate", 0.0), "empty_net_log_rate")
        )

        self.tie_start = self._window_start(cfg, "late_tie_start", self.tie_factor != 1.0)
        raw_pulls = cfg.get("pull_probabilities", {})
        if not isinstance(raw_pulls, Mapping):
            raise ValueError("pull_probabilities must map deficits to probabilities.")
        self.pull_probabilities = {}
        for deficit, probability in raw_pulls.items():
            if isinstance(deficit, bool):
                raise ValueError("Pull deficits must be positive integers.")
            try:
                parsed = int(deficit)
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError("Pull deficits must be positive integers.") from exc
            if parsed < 1 or str(parsed) != str(deficit):
                raise ValueError("Pull deficits must be positive integers.")
            self.pull_probabilities[parsed] = self._probability(
                probability, f"pull_probabilities[{deficit}]"
            )
        self.pull_start = self._window_start(
            cfg, "pull_start", any(self.pull_probabilities.values())
        )
        self.has_dynamics = (
            self.trailing_factor != 1.0
            or self.leading_factor != 1.0
            or self.tie_factor != 1.0
            or (
                any(self.pull_probabilities.values())
                and (self.pull_for_factor != 1.0 or self.pull_against_factor != 1.0)
            )
        )

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _probability(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{name} must lie in [0, 1].")
        return result

    @staticmethod
    def _exp(value: float) -> float:
        try:
            result = math.exp(value)
        except OverflowError as exc:
            raise ValueError("Rate multiplier exceeds floating-point capacity.") from exc
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError("Rate multiplier is not numerically representable.")
        return result

    @classmethod
    def _window_start(cls, cfg: Mapping[str, Any], name: str, required: bool) -> float:
        if name not in cfg:
            if required:
                raise ValueError(f"{name} is required for an active fitted mechanism.")
            return 1.0
        return cls._probability(cfg[name], name)

    def _targets(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        home = context.get("home", {})
        away = context.get("away", {})
        if not isinstance(home, Mapping) or not isinstance(away, Mapping):
            raise ValueError("home and away must be feature mappings.")

        def mean(side: str, own: Mapping, opponent: Mapping, baseline: float) -> float:
            supplied = context.get(f"{side}_expected")
            if supplied is not None:
                return self._positive(supplied, f"{side}_expected")
            attack = self._positive(own.get("attack", 1.0), f"{side}.attack")
            defense = self._positive(opponent.get("defense", 1.0), "opponent.defense")
            return self._positive(baseline * attack * defense, f"{side} derived mean")

        h = mean("home", home, away, self.BASE_HOME_MEAN)
        a = mean("away", away, home, self.BASE_AWAY_MEAN)
        pace = self._positive(context.get("pace", 1.0), "pace")

        home_rest = context.get("home_rest_days")
        away_rest = context.get("away_rest_days")
        for name, value in (("home_rest_days", home_rest), ("away_rest_days", away_rest)):
            if value is not None and self._finite(value, name) < 0.0:
                raise ValueError(f"{name} must be nonnegative.")

        rest_adjustment = 0.0
        if self.rest_coefficient != 0.0:
            if home_rest is None or away_rest is None:
                raise ValueError("Both rest-day inputs are required by the fitted rest effect.")
            rest_adjustment = self.rest_coefficient * (float(home_rest) - float(away_rest))

        return (
            self._positive(h * pace * self._exp(rest_adjustment), "home target"),
            self._positive(a * pace * self._exp(-rest_adjustment), "away target"),
        )

    def _tie_win_probability(self, context: Mapping[str, Any]) -> float:
        ot = self._probability(
            context.get("home_ot_win_probability", self.ot_home),
            "home_ot_win_probability",
        )
        shootout = self._probability(
            context.get("home_shootout_win_probability", self.so_home),
            "home_shootout_win_probability",
        )
        weight = self._probability(
            context.get("ot_resolution_probability", self.ot_resolution),
            "ot_resolution_probability",
        )
        return weight * ot + (1.0 - weight) * shootout

    def _rate_factors(self, width: int, time: float) -> Tuple[np.ndarray, np.ndarray]:
        h, a = np.indices((width, width))
        difference = h - a
        home_factor = np.where(
            difference < 0, self.trailing_factor,
            np.where(difference > 0, self.leading_factor, 1.0),
        )
        away_factor = np.where(
            difference > 0, self.trailing_factor,
            np.where(difference < 0, self.leading_factor, 1.0),
        )

        if time >= self.tie_start:
            tied = difference == 0
            home_factor[tied] *= self.tie_factor
            away_factor[tied] *= self.tie_factor

        if time >= self.pull_start:
            for deficit, probability in self.pull_probabilities.items():
                own_multiplier = (1.0 - probability) + probability * self.pull_for_factor
                opponent_multiplier = (
                    (1.0 - probability) + probability * self.pull_against_factor
                )
                home_trails = difference == -deficit
                away_trails = difference == deficit
                home_factor[home_trails] *= own_multiplier
                away_factor[home_trails] *= opponent_multiplier
                away_factor[away_trails] *= own_multiplier
                home_factor[away_trails] *= opponent_multiplier

        return home_factor.ravel(), away_factor.ravel()

    def _regulation_grid(self, mu_h: float, mu_a: float) -> np.ndarray:
        # A dominating Poisson process bounds the number of total goal jumps.
        multiplier_bound = (
            max(1.0, self.trailing_factor, self.leading_factor)
            * max(1.0, self.tie_factor)
            * max(1.0, self.pull_for_factor, self.pull_against_factor)
        )
        jump_mean = self._positive(
            (mu_h + mu_a) * multiplier_bound, "dominating goal intensity"
        )
        quantile = poisson.isf(self.tolerance, jump_mean)
        if not np.isfinite(quantile):
            raise ArithmeticError("Unable to construct a finite score support.")
        cutoff = int(quantile)
        while poisson.sf(cutoff, jump_mean) > self.tolerance:
            cutoff += 1

        width = cutoff + 1
        size = width * width
        if size + 1 > self.max_states:
            raise ValueError(
                f"Required score lattice has {size + 1} states; "
                f"configured max_states is {self.max_states}."
            )

        overflow = size
        source = np.arange(size)
        home_score = source // width
        away_score = source % width
        home_destination = np.where(home_score < cutoff, source + width, overflow)
        away_destination = np.where(away_score < cutoff, source + 1, overflow)

        state = np.zeros(size + 1)
        state[0] = 1.0
        boundaries = sorted({0.0, self.tie_start, self.pull_start, 1.0})

        for start, end in zip(boundaries[:-1], boundaries[1:]):
            home_factor, away_factor = self._rate_factors(width, (start + end) / 2.0)
            home_rates = mu_h * home_factor
            away_rates = mu_a * away_factor
            generator = sparse.coo_matrix(
                (
                    np.concatenate(
                        (home_rates, away_rates, -(home_rates + away_rates))
                    ),
                    (
                        np.concatenate((home_destination, away_destination, source)),
                        np.concatenate((source, source, source)),
                    ),
                ),
                shape=(size + 1, size + 1),
            ).tocsr()
            state = expm_multiply(generator * (end - start), state)

        if not np.all(np.isfinite(state)):
            raise ArithmeticError("Nonfinite score probabilities.")
        if state.min() < -self.tolerance:
            raise ArithmeticError("Matrix exponential produced negative probability mass.")
        # Remove floating-point roundoff only; never floor substantive probabilities.
        state[state < 0.0] = 0.0
        if abs(float(state.sum()) - 1.0) > self.tolerance:
            raise ArithmeticError("Regulation probability conservation failed.")
        if state[overflow] > self.tolerance:
            raise ArithmeticError("Score-support truncation exceeded tolerance.")

        grid = state[:size].reshape(width, width)
        retained_mass = float(grid.sum())
        if retained_mass <= 0.0:
            raise ArithmeticError("No probability mass retained.")
        return grid / retained_mass

    @staticmethod
    def _regulation_moments(grid: np.ndarray) -> np.ndarray:
        scores = np.arange(grid.shape[0], dtype=float)
        return np.array((scores @ grid.sum(axis=1), scores @ grid.sum(axis=0)))

    def _calibrate_intensities(
        self,
        targets: Tuple[float, float],
        basis: str,
        tie_home: float,
    ) -> Tuple[float, float]:
        target = np.asarray(targets, dtype=float)
        allocation = np.array((tie_home, 1.0 - tie_home))
        if basis == "full_game" and float(target.sum()) <= 1.0:
            raise ValueError(
                "Positive regulation intensities require full-game expected total > 1."
            )

        if basis == "regulation" and not self.has_dynamics:
            return targets

        def predicted_means(log_rates: np.ndarray) -> np.ndarray:
            rates = tuple(self._exp(float(value)) for value in log_rates)
            if not self.has_dynamics:
                means = np.asarray(rates)
                tie_mass = float(skellam.pmf(0, rates[0], rates[1]))
            else:
                grid = self._regulation_grid(*rates)
                means = self._regulation_moments(grid)
                tie_mass = float(np.trace(grid))
            return means + allocation * tie_mass if basis == "full_game" else means

        result = least_squares(
            lambda log_rates: (predicted_means(log_rates) - target) / target,
            np.log(target),
            ftol=self.tolerance,
            xtol=self.tolerance,
            gtol=self.tolerance,
        )
        residual = np.abs(predicted_means(result.x) - target) / target
        if not result.success or np.max(residual) > math.sqrt(self.tolerance):
            raise ArithmeticError(
                "Could not reconcile requested means with the fitted scoring mechanics."
            )
        return tuple(self._exp(float(value)) for value in result.x)

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        for field, expected in (
            ("league", "NHL"), ("scope", "full_game"), ("family", "exact_score")
        ):
            if field in game_context and game_context[field] != expected:
                raise ValueError(f"Incompatible {field}: {game_context[field]!r}")

        settlement = game_context.get(
            "settlement", "including_overtime_and_shootout_winner_goal"
        )
        if settlement != "including_overtime_and_shootout_winner_goal":
            raise ValueError("Unsupported exact-score settlement convention.")

        basis = game_context.get("expected_basis", self.expected_basis)
        if basis not in {"full_game", "regulation"}:
            raise ValueError("expected_basis must be 'full_game' or 'regulation'.")

        targets = self._targets(game_context)
        tie_home = self._tie_win_probability(game_context)
        intensities = self._calibrate_intensities(targets, basis, tie_home)
        regulation = self._regulation_grid(*intensities)

        width = regulation.shape[0]
        final = np.zeros((width + 1, width + 1))
        final[:width, :width] = regulation
        diagonal = np.arange(width)
        tie_mass = regulation[diagonal, diagonal].copy()
        final[diagonal, diagonal] = 0.0
        final[diagonal + 1, diagonal] += tie_mass * tie_home
        final[diagonal, diagonal + 1] += tie_mass * (1.0 - tie_home)

        total_mass = float(final.sum())
        if abs(total_mass - 1.0) > self.tolerance:
            raise ArithmeticError("Full-game probability conservation failed.")
        if np.any(np.diag(final) != 0.0):
            raise ArithmeticError("Full-game distribution contains draw mass.")
        final /= total_mass

        if basis == "full_game":
            actual = self._regulation_moments(final)
            relative_error = np.abs(actual - np.asarray(targets)) / np.asarray(targets)
            if np.max(relative_error) > math.sqrt(self.tolerance):
                raise ArithmeticError("Final expected scores do not match target means.")

        scores = {
            (int(home), int(away)): float(final[home, away])
            for home, away in zip(*np.nonzero(final))
        }
        return DiscreteDistribution.from_scores(scores)

    @classmethod
    def contract_value(cls, probability: float, decimal_odds: float) -> Dict[str, Any]:
        """
        Unit-stake valuation only. No price blending, odds cap, or staking claim.
        Every contract satisfying the specified odds floor and positive EV qualifies.
        """
        probability = cls._probability(probability, "probability")
        decimal_odds = cls._positive(decimal_odds, "decimal_odds")
        if decimal_odds < 1.0:
            raise ValueError("Decimal odds must be at least one.")
        expected_profit = probability * decimal_odds - 1.0
        return {
            "probability": probability,
            "decimal_odds": decimal_odds,
            "expected_profit_per_unit": expected_profit,
            "eligible": (
                decimal_odds >= cls.MINIMUM_DECIMAL_ODDS and expected_profit > 0.0
            ),
        }