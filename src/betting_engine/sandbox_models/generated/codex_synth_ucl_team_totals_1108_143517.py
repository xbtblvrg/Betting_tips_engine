from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import root
from scipy.sparse import coo_matrix, diags
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class CodexSynthUCLTeamTotals(BaseCandidateModel):
    """
    Regulation-time joint goal distribution and team-total settlement utilities.

    DiscreteDistribution is supplied by the candidate execution environment.
    Its from_scores factory must accept {(home_goals, away_goals): probability}.

    Identification:
        Zero settled observations and a dispersion prior of one do not identify
        game-state effects, zero inflation, or negative score dependence. The
        default is therefore independent Poisson. Nonzero dynamics must come
        from externally fitted, chronologically validated parameters.

    Dynamics, on normalized regulation time u in [0, 1]:
        intensity_i(u, h, a)
            = exp(location_i + state_effect_i(h, a) + time_trend * u)

    Locations are solved numerically so that final expected goals match the
    requested situational means. Goals during extra time are excluded. Expected
    goals must include whatever regulation stoppage time the source assumes.

    Context schema:
        home_expected, away_expected:
            Final situational means; when supplied, additional feature
            adjustments are not applied to that team's mean.
        home / away:
            Optional dictionaries containing attack_multiplier and
            defense_multiplier, both positive. Larger defense_multiplier
            means stronger defense. Raw ratings are deliberately not guessed.
        pace_multiplier:
            Positive multiplicative exposure, default neutral.
        home_rest_days, away_rest_days:
            Used only when rest_log_effect is nonzero; both then required.
        aggregate_goal_difference:
            Home aggregate advantage BEFORE this match, integer, default zero.

    Parameters:
        lead_log_effect, trail_log_effect:
            Effects on a team's own scoring intensity.
        opponent_trailing_log_effect:
            Transition exposure when the opponent trails.
        time_trend:
            Log intensity change across regulation time.
        rest_log_effect:
            Log expected-goal change per additional rest day.

    No bookmaker probability enters the predictive distribution. Numerical
    tolerances control approximation error, not the shape of scoring tails.
    This class makes no claim of demonstrated calibration or profitable ROI.
    """

    SEGMENT = "UCL|team_totals|totals"
    HOME_ANCHOR = 1.60
    AWAY_ANCHOR = 1.20
    MIN_DECIMAL_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_team_totals_1108_143517",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "team_totals",
        family: str = "totals",
        *,
        lead_log_effect: float = 0.0,
        trail_log_effect: float = 0.0,
        opponent_trailing_log_effect: float = 0.0,
        time_trend: float = 0.0,
        rest_log_effect: float = 0.0,
        numerical_tolerance: Optional[float] = None,
        max_score_states: Optional[int] = None,
    ) -> None:
        if (league, scope, family) != ("UCL", "team_totals", "totals"):
            raise ValueError("This candidate supports only " + self.SEGMENT)

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.lead = self._finite(lead_log_effect, "lead_log_effect")
        self.trail = self._finite(trail_log_effect, "trail_log_effect")
        self.transition = self._finite(
            opponent_trailing_log_effect, "opponent_trailing_log_effect"
        )
        self.time_trend = self._finite(time_trend, "time_trend")
        self.rest_effect = self._finite(rest_log_effect, "rest_log_effect")

        self.tolerance = (
            math.sqrt(np.finfo(float).eps)
            if numerical_tolerance is None
            else self._finite(numerical_tolerance, "numerical_tolerance")
        )
        if not np.finfo(float).eps < self.tolerance < 1.0:
            raise ValueError("numerical_tolerance must lie between epsilon and one")

        if max_score_states is not None:
            if isinstance(max_score_states, bool) or int(max_score_states) != max_score_states:
                raise ValueError("max_score_states must be a positive integer")
            if max_score_states < 1:
                raise ValueError("max_score_states must be positive")
        self.max_score_states = max_score_states

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive")
        return result

    @staticmethod
    def _exp(value: float) -> float:
        try:
            result = math.exp(value)
        except OverflowError as exc:
            raise ValueError("Intensity exceeds floating-point range") from exc
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError("Intensity is outside representable positive range")
        return result

    def _means(self, context: Mapping[str, Any]) -> np.ndarray:
        means = []
        for side, opponent, anchor in (
            ("home", "away", self.HOME_ANCHOR),
            ("away", "home", self.AWAY_ANCHOR),
        ):
            key = side + "_expected"
            if key in context:
                means.append(self._positive(context[key], key))
                continue

            own = context.get(side, {})
            other = context.get(opponent, {})
            if not isinstance(own, Mapping) or not isinstance(other, Mapping):
                raise ValueError("home and away features must be mappings")

            attack = self._positive(
                own.get("attack_multiplier", 1.0), side + ".attack_multiplier"
            )
            defense = self._positive(
                other.get("defense_multiplier", 1.0),
                opponent + ".defense_multiplier",
            )
            pace = self._positive(
                context.get("pace_multiplier", 1.0), "pace_multiplier"
            )
            location = (
                math.log(anchor)
                + math.log(attack)
                - math.log(defense)
                + math.log(pace)
            )
            if self.rest_effect != 0.0:
                own_key = side + "_rest_days"
                other_key = opponent + "_rest_days"
                if own_key not in context or other_key not in context:
                    raise ValueError("A fitted rest effect requires both rest inputs")
                own_rest = self._finite(context[own_key], own_key)
                other_rest = self._finite(context[other_key], other_key)
                if min(own_rest, other_rest) < 0.0:
                    raise ValueError("Rest days cannot be negative")
                location += self.rest_effect * (own_rest - other_rest)
            means.append(self._exp(location))

        # Weather and venue are not assigned unsupported cold-start effects.
        return np.asarray(means, dtype=float)

    def _aggregate_difference(self, context: Mapping[str, Any]) -> int:
        value = self._finite(
            context.get("aggregate_goal_difference", 0),
            "aggregate_goal_difference",
        )
        if not value.is_integer():
            raise ValueError("aggregate_goal_difference must be an integer")
        return int(value)

    def _support(self, dominating_means: np.ndarray) -> Tuple[int, int, float]:
        caps = []
        budget = self.tolerance / len(dominating_means)
        for mean in dominating_means:
            quantile = poisson.isf(budget, float(mean))
            if not math.isfinite(float(quantile)):
                raise ArithmeticError("Unable to construct a finite score support")
            cap = int(quantile)
            while poisson.sf(cap, mean) > budget:
                cap += 1
            caps.append(cap)

        states = (caps[0] + 1) * (caps[1] + 1)
        if self.max_score_states is not None and states > self.max_score_states:
            raise MemoryError(
                f"Requested support needs {states} states; "
                "increase max_score_states or review supplied intensities"
            )

        bound = sum(
            float(poisson.sf(cap, mean))
            for cap, mean in zip(caps, dominating_means)
        )
        return caps[0], caps[1], bound

    @staticmethod
    def _moments(matrix: np.ndarray) -> np.ndarray:
        return np.asarray(
            [
                np.dot(np.arange(matrix.shape[0]), matrix.sum(axis=1)),
                np.dot(np.arange(matrix.shape[1]), matrix.sum(axis=0)),
            ],
            dtype=float,
        )

    def _poisson_joint(
        self, means: np.ndarray
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        home_cap, away_cap, bound = self._support(means)
        home_log = poisson.logpmf(np.arange(home_cap + 1), means[0])
        away_log = poisson.logpmf(np.arange(away_cap + 1), means[1])
        joint_log = home_log[:, None] + away_log[None, :]
        log_mass = float(logsumexp(joint_log))
        matrix = np.exp(joint_log - log_mass)
        return matrix, {
            "omitted_mass_bound": bound,
            "retained_mass": math.exp(log_mass),
            "location": np.log(means).tolist(),
            "method": "independent_poisson",
        }

    def _dynamic_joint(
        self, location: np.ndarray, aggregate: int
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        # For either team, leading implies that its opponent is trailing.
        largest_state_effect = max(0.0, self.lead + self.transition, self.trail)
        dominating_means = np.asarray(
            [
                self._exp(
                    float(value)
                    + largest_state_effect
                    + max(0.0, self.time_trend)
                )
                for value in location
            ]
        )
        home_cap, away_cap, bound = self._support(dominating_means)
        shape = (home_cap + 1, away_cap + 1)
        home, away = np.indices(shape)
        difference = home - away + aggregate

        home_effect = np.where(
            difference > 0,
            self.lead + self.transition,
            np.where(difference < 0, self.trail, 0.0),
        )
        away_effect = np.where(
            difference < 0,
            self.lead + self.transition,
            np.where(difference > 0, self.trail, 0.0),
        )

        home_rate = np.exp(location[0] + home_effect).ravel()
        away_rate = np.exp(location[1] + away_effect).ravel()
        if not (
            np.all(np.isfinite(home_rate))
            and np.all(np.isfinite(away_rate))
        ):
            raise ArithmeticError("Nonfinite state intensity")

        index = np.arange(home.size).reshape(shape)
        home_sources = index[:-1, :].ravel()
        away_sources = index[:, :-1].ravel()
        sources = np.concatenate((home_sources, away_sources))
        destinations = np.concatenate(
            (index[1:, :].ravel(), index[:, 1:].ravel())
        )
        rates = np.concatenate(
            (home_rate[home_sources], away_rate[away_sources])
        )

        # Column generator. Boundary outflow is killed, never reflected or
        # accumulated in the last score cell. Dominating processes bound it.
        generator = coo_matrix(
            (rates, (destinations, sources)),
            shape=(home.size, home.size),
        ).tocsc() - diags(home_rate + away_rate, format="csc")

        # The common time factor permits an exact operational-time transform.
        horizon = (
            1.0
            if self.time_trend == 0.0
            else math.expm1(self.time_trend) / self.time_trend
        )
        if not math.isfinite(horizon) or horizon <= 0.0:
            raise ArithmeticError("Invalid integrated time exposure")

        initial = np.zeros(home.size)
        initial[0] = 1.0
        numerical_budget = self.tolerance / home.size
        solution = solve_ivp(
            lambda _, probability: generator @ probability,
            (0.0, horizon),
            initial,
            method="BDF",
            jac=generator,
            rtol=self.tolerance,
            atol=numerical_budget,
            t_eval=[horizon],
        )
        if not solution.success or solution.y.shape[1] != 1:
            raise ArithmeticError("Score propagation failed: " + solution.message)

        probability = solution.y[:, -1]
        if not np.all(np.isfinite(probability)):
            raise ArithmeticError("Nonfinite propagated probabilities")

        negative_mass = float(-probability[probability < 0.0].sum())
        if negative_mass > self.tolerance:
            raise ArithmeticError("Propagation produced excessive negative mass")
        # Remove numerical roundoff only; this is not a distributional clamp.
        probability = np.maximum(probability, 0.0)
        mass = float(probability.sum())
        if (
            mass <= 0.0
            or mass > 1.0 + self.tolerance
            or 1.0 - mass > bound + math.sqrt(self.tolerance)
        ):
            raise ArithmeticError("Score propagation failed its mass check")

        return (probability / mass).reshape(shape), {
            "omitted_mass_bound": bound,
            "retained_mass": mass,
            "negative_roundoff_mass": negative_mass,
            "location": location.tolist(),
            "method": "state_dependent_birth_process",
            "solver_tolerance": self.tolerance,
        }

    def score_probabilities(
        self, game_context: Dict[str, Any]
    ) -> Tuple[Dict[Tuple[int, int], float], Dict[str, Any]]:
        targets = self._means(game_context)
        aggregate = self._aggregate_difference(game_context)

        if self.lead + self.transition == 0.0 and self.trail == 0.0:
            # A deterministic time profile alone still gives Poisson counts
            # after matching integrated means.
            matrix, diagnostics = self._poisson_joint(targets)
        else:
            def residual(location: np.ndarray) -> np.ndarray:
                candidate, _ = self._dynamic_joint(location, aggregate)
                achieved = self._moments(candidate)
                if np.any(achieved <= 0.0):
                    raise ArithmeticError("Mean calibration lost positive support")
                return np.log(achieved) - np.log(targets)

            fit = root(
                residual,
                np.log(targets),
                method="hybr",
                options={"xtol": math.sqrt(self.tolerance)},
            )
            if not fit.success:
                raise ArithmeticError("Mean calibration failed: " + str(fit.message))
            matrix, diagnostics = self._dynamic_joint(fit.x, aggregate)

        achieved = self._moments(matrix)
        relative_error = np.abs(achieved - targets) / targets
        if np.any(relative_error > math.sqrt(self.tolerance)):
            raise ArithmeticError("Final distribution failed mean calibration")

        diagnostics.update(
            target_means=targets.tolist(),
            achieved_means=achieved.tolist(),
            relative_mean_error=relative_error.tolist(),
            score_shape=matrix.shape,
            calibrated_from_settled_results=False,
        )
        scores = {
            (int(home), int(away)): float(matrix[home, away])
            for home, away in np.ndindex(matrix.shape)
            if matrix[home, away] > 0.0
        }
        return scores, diagnostics

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        scores, _ = self.score_probabilities(game_context)
        return DiscreteDistribution.from_scores(scores)

    def team_pmf(
        self, game_context: Dict[str, Any], team: str
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        if team not in ("home", "away"):
            raise ValueError("team must be 'home' or 'away'")
        scores, diagnostics = self.score_probabilities(game_context)
        axis = 0 if team == "home" else 1
        pmf = np.zeros(max(score[axis] for score in scores) + 1)
        for score, probability in scores.items():
            pmf[score[axis]] += probability
        return pmf, diagnostics

    @staticmethod
    def _validated_pmf(pmf: Any) -> np.ndarray:
        result = np.asarray(pmf, dtype=float)
        if (
            result.ndim != 1
            or result.size == 0
            or not np.all(np.isfinite(result))
            or np.any(result < 0.0)
        ):
            raise ValueError("pmf must be a finite nonnegative vector")
        tolerance = math.sqrt(np.finfo(float).eps)
        if abs(float(result.sum()) - 1.0) > tolerance:
            raise ValueError("pmf must sum to one")
        return result / result.sum()

    @classmethod
    def settlement(
        cls,
        pmf: Any,
        line: float,
        side: str = "over",
        settlement_type: str = "binary",
    ) -> Dict[str, float]:
        """
        binary:
            YES is X > line; NO is its complement, including integer equality.
            No push and no Asian quarter-line splitting.

        asian:
            Integer equality refunds that stake leg. Quarter lines split stake
            equally across adjacent integer/half lines. Returned win/loss/refund
            values are expected stake fractions, not probabilities of mutually
            exclusive whole-ticket outcomes.
        """
        probabilities = cls._validated_pmf(pmf)
        line = cls._finite(line, "line")
        if side not in ("over", "under"):
            raise ValueError("side must be 'over' or 'under'")
        counts = np.arange(probabilities.size)

        if settlement_type == "binary":
            mask = counts > line
            if side == "under":
                mask = ~mask
            win = float(probabilities[mask].sum())
            return {"win": win, "loss": 1.0 - win, "refund": 0.0}

        if settlement_type != "asian":
            raise ValueError("settlement_type must be 'binary' or 'asian'")
        quarter = line * 4.0
        if not math.isfinite(quarter) or not quarter.is_integer():
            raise ValueError("Asian settlement requires a multiple of 0.25")

        legs = (
            (line,)
            if int(quarter) % 2 == 0
            else (math.floor(line * 2.0) / 2.0, math.ceil(line * 2.0) / 2.0)
        )
        result = {"win": 0.0, "loss": 0.0, "refund": 0.0}
        for leg in legs:
            win_mask = counts > leg if side == "over" else counts < leg
            refund_mask = counts == leg
            loss_mask = ~(win_mask | refund_mask)
            for key, mask in (
                ("win", win_mask),
                ("loss", loss_mask),
                ("refund", refund_mask),
            ):
                result[key] += float(probabilities[mask].sum()) / len(legs)
        return result

    @classmethod
    def contract_value(
        cls,
        pmf: Any,
        line: float,
        decimal_odds: float,
        *,
        side: str = "over",
        settlement_type: str = "binary",
        cost_per_unit_stake: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Expected net return per unit stake.

        For a binary share bought at q and paying one, decimal_odds = 1/q.
        Costs must be supplied in units of initial stake. Outcome-dependent
        fees must first be converted to their expected cost by the caller.
        """
        odds = cls._positive(decimal_odds, "decimal_odds")
        if odds < 1.0:
            raise ValueError("Decimal odds cannot be below one")
        cost = cls._finite(cost_per_unit_stake, "cost_per_unit_stake")
        if cost < 0.0:
            raise ValueError("cost_per_unit_stake cannot be negative")

        fractions = cls.settlement(pmf, line, side, settlement_type)
        expected_return = (
            fractions["win"] * (odds - 1.0) - fractions["loss"] - cost
        )
        fair_odds = (
            math.inf
            if fractions["win"] == 0.0
            else 1.0 + fractions["loss"] / fractions["win"]
        )
        return {
            **fractions,
            "fair_decimal_odds_before_costs": fair_odds,
            "expected_net_return_per_unit_stake": expected_return,
            "eligible": odds >= cls.MIN_DECIMAL_ODDS and expected_return > 0.0,
        }

    @classmethod
    def calibration_observation(
        cls,
        pmf: Any,
        observed_goals: int,
        *,
        pit_uniform: Optional[float] = None,
        market_tail_to_zero_ratio: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Diagnostics for a forecast frozen before kickoff.

        Discrete PIT is an interval. A randomized PIT is returned only when
        the caller supplies an independent U[0,1] draw; computation stays
        deterministic. CRPS includes observations beyond numerical support.

        Market ladder comparison is diagnostic only. It neither fits nor
        modifies the PMF. A single observation cannot establish calibration.
        """
        probabilities = cls._validated_pmf(pmf)
        if (
            isinstance(observed_goals, (bool, np.bool_))
            or int(observed_goals) != observed_goals
            or observed_goals < 0
        ):
            raise ValueError("observed_goals must be a nonnegative integer")
        observed = int(observed_goals)
        cdf = np.cumsum(probabilities)
        lower = float(probabilities[:observed].sum())
        point_mass = (
            float(probabilities[observed])
            if observed < probabilities.size
            else 0.0
        )
        upper = lower + point_mass

        grid = np.arange(probabilities.size)
        crps = float(np.square(cdf - (grid >= observed)).sum())
        crps += max(0, observed - probabilities.size)

        zero = float(probabilities[0])
        tail = float(probabilities[3:].sum())
        ratio = tail / zero if zero > 0.0 else math.inf
        result: Dict[str, Any] = {
            "pit_interval": (lower, upper),
            "crps": crps,
            "observed_probability": point_mass,
            "negative_log_score": (
                -math.log(point_mass) if point_mass > 0.0 else math.inf
            ),
            "zero_probability": zero,
            "three_plus_probability": tail,
            "tail_to_zero_ratio": ratio,
            "observation_outside_numerical_support": observed >= probabilities.size,
        }
        if pit_uniform is not None:
            uniform = cls._finite(pit_uniform, "pit_uniform")
            if not 0.0 <= uniform <= 1.0:
                raise ValueError("pit_uniform must lie in [0, 1]")
            result["randomized_pit"] = lower + uniform * point_mass

        if market_tail_to_zero_ratio is not None:
            market_ratio = cls._positive(
                market_tail_to_zero_ratio, "market_tail_to_zero_ratio"
            )
            result["market_tail_to_zero_ratio"] = market_ratio
            result["log_tail_ratio_difference"] = (
                math.log(ratio) - math.log(market_ratio)
                if ratio > 0.0
                else -math.inf
            )
        return result