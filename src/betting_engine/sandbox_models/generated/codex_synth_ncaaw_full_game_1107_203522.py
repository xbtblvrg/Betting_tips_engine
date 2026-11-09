from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import norm, skellam, t

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAWRegulationThreeWayModel(BaseCandidateModel):
    """
    Deterministic regulation-result model with an explicit cold-start fallback.

    The supplied score means identify expected scoring, but do not identify
    possession variance, score covariance, blowout tails, or excess ties.
    Consequently, the cold-start model uses the supplied Poisson prior.
    Richer margin dynamics activate only when their parameters are supplied.

    No bookmaker prices enter the forecast. No historical ROI or calibration
    improvement is asserted in the absence of out-of-sample observations.

    Context schema
    --------------
    home_expected, away_expected:
        Baseline regulation score means; defaults are the supplied 80 and 75.
    pace_multiplier:
        Relative pace, or use possessions / baseline_possessions instead.
    home, away:
        Optional mappings containing attack_multiplier and defense_multiplier.
        Defense multipliers describe points conceded. All multipliers must be
        positive. Raw, unscaled attack/defense ratings are intentionally ignored.
    home_rest_days, away_rest_days, rest_log_efficiency_coefficient:
        Optional rest adjustment. The coefficient must be estimated externally.
    home_travel_distance, away_travel_distance,
    travel_log_efficiency_coefficient:
        Optional travel adjustment, in the coefficient's fitted distance units.
    home_court_margin_prior, margin_signal_precision, margin_prior_precision:
        Optional precision-weighted shrinkage of the margin mean. The prior is
        in regulation points at the current pace and before rest/travel effects.
    possessions, efficiency_difference_variance, possession_cv_squared:
        Mechanistic margin-variance inputs:
            Var(D) = E[N] Var(e_h - e_a) + E[D]^2 CV(N)^2.
        Efficiency variance must include within-possession scoring dependence.
    margin_variance:
        An alternative externally estimated regulation margin variance.
    home_dispersion_index, away_dispersion_index, score_covariance:
        An alternative variance specification:
            Var(D) = H * I_h + A * I_a - 2 * Cov(H, A).
        Dispersion indices default to the supplied Poisson prior of one.
    margin_degrees_of_freedom:
        Optional fitted Student-t degrees of freedom, strictly greater than two.
        With a supplied variance and no finite degrees of freedom, use a
        discretized normal distribution. No tail parameter is invented.
    tie_transfer_fraction:
        Fitted fraction in [0, 1] governing transfer from margins +/-1, +/-2,
        and +/-3 onto zero. Defaults to zero until calibrated.
    settlement:
        If supplied, must be "regulation".
    includes_overtime:
        Must be false when supplied.

    The integer lattice has unit spacing because free throws allow odd margins;
    two- and three-point field goals do not justify an even-score restriction.
    Detailed possession score frequencies cannot be recovered from two means.

    Parameters are inference artifacts: estimate them using only information
    available before each forecast. fit_tie_transfer mutates the model and must
    be called on training data, never evaluation outcomes.
    """

    SEGMENT = ("NCAAW", "full_game", "three_way")
    BASELINE_HOME = 80.0
    BASELINE_AWAY = 75.0
    POISSON_DISPERSION = 1.0
    CLOSE_MARGIN_RADIUS = 3
    HALF_LATTICE_WIDTH = 0.5
    LABELS = ("home", "draw", "away")

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_full_game_1107_203522",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "full_game",
        family: str = "three_way",
        *,
        tie_transfer_fraction: float = 0.0,
    ) -> None:
        if (league, scope, family) != self.SEGMENT:
            raise ValueError("This model supports only NCAAW|full_game|three_way.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.tie_transfer_fraction = self._fraction(
            tie_transfer_fraction, "tie_transfer_fraction"
        )

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be a real number, not a boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite real number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _fraction(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{name} must lie in [0, 1].")
        return result

    @staticmethod
    def _validate_context(context: Mapping[str, Any]) -> None:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")
        for key, expected in zip(
            ("league", "scope", "family"),
            NCAAWRegulationThreeWayModel.SEGMENT,
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"{key} must be {expected!r}.")
        if context.get("settlement", "regulation") != "regulation":
            raise ValueError("Three-way forecasts require regulation settlement.")
        overtime = context.get("includes_overtime", False)
        if not isinstance(overtime, (bool, np.bool_)) or overtime:
            raise ValueError("includes_overtime must be false.")

    def _score_means(self, context: Mapping[str, Any]) -> Tuple[float, float, float]:
        home = self._positive(
            context.get("home_expected", self.BASELINE_HOME), "home_expected"
        )
        away = self._positive(
            context.get("away_expected", self.BASELINE_AWAY), "away_expected"
        )

        if "pace_multiplier" in context and "baseline_possessions" in context:
            raise ValueError("Supply one pace adjustment specification.")
        if "pace_multiplier" in context:
            pace = self._positive(context["pace_multiplier"], "pace_multiplier")
        elif "baseline_possessions" in context:
            pace = self._positive(context["possessions"], "possessions") / (
                self._positive(
                    context["baseline_possessions"], "baseline_possessions"
                )
            )
        else:
            pace = 1.0

        teams = []
        for side in ("home", "away"):
            team = context.get(side, {})
            if not isinstance(team, Mapping):
                raise ValueError(f"{side} must be a mapping.")
            teams.append(
                (
                    self._positive(
                        team.get("attack_multiplier", 1.0),
                        f"{side}.attack_multiplier",
                    ),
                    self._positive(
                        team.get("defense_multiplier", 1.0),
                        f"{side}.defense_multiplier",
                    ),
                )
            )

        home *= pace * teams[0][0] * teams[1][1]
        away *= pace * teams[1][0] * teams[0][1]
        home = self._positive(home, "adjusted home mean")
        away = self._positive(away, "adjusted away mean")

        total = self._positive(home + away, "adjusted total mean")
        margin = home - away
        shrinkage_keys = {
            "home_court_margin_prior",
            "margin_signal_precision",
            "margin_prior_precision",
        }
        if shrinkage_keys.intersection(context):
            if not shrinkage_keys.issubset(context):
                raise ValueError("Margin shrinkage requires a prior and both precisions.")
            signal_precision = self._nonnegative(
                context["margin_signal_precision"], "margin_signal_precision"
            )
            prior_precision = self._nonnegative(
                context["margin_prior_precision"], "margin_prior_precision"
            )
            precision_scale = max(signal_precision, prior_precision)
            if precision_scale == 0.0:
                raise ValueError("At least one margin precision must be positive.")
            signal_weight = signal_precision / precision_scale
            prior_weight = prior_precision / precision_scale
            prior = self._number(
                context["home_court_margin_prior"], "home_court_margin_prior"
            )
            weight_sum = signal_weight + prior_weight
            margin = (
                (signal_weight / weight_sum) * margin
                + (prior_weight / weight_sum) * prior
            )
            if abs(margin) >= total:
                raise ValueError("Shrunken margin implies a nonpositive score mean.")
            home = (total + margin) / 2.0
            away = (total - margin) / 2.0

        log_adjustment = 0.0
        if "rest_log_efficiency_coefficient" in context:
            coefficient = self._number(
                context["rest_log_efficiency_coefficient"],
                "rest_log_efficiency_coefficient",
            )
            home_rest = self._nonnegative(context["home_rest_days"], "home_rest_days")
            away_rest = self._nonnegative(context["away_rest_days"], "away_rest_days")
            log_adjustment += coefficient * (home_rest - away_rest)

        if "travel_log_efficiency_coefficient" in context:
            coefficient = self._number(
                context["travel_log_efficiency_coefficient"],
                "travel_log_efficiency_coefficient",
            )
            home_travel = self._nonnegative(
                context["home_travel_distance"], "home_travel_distance"
            )
            away_travel = self._nonnegative(
                context["away_travel_distance"], "away_travel_distance"
            )
            log_adjustment += coefficient * (home_travel - away_travel)

        try:
            home *= math.exp(log_adjustment / 2.0)
            away *= math.exp(-log_adjustment / 2.0)
        except OverflowError as exc:
            raise ValueError("Situational adjustment exceeds numerical range.") from exc

        home = self._positive(home, "situational home mean")
        away = self._positive(away, "situational away mean")
        self._positive(home + away, "situational total mean")
        return home, away, home - away

    def _variance(
        self, context: Mapping[str, Any], home: float, away: float, margin: float
    ) -> Tuple[float, bool]:
        mechanistic = any(
            key in context
            for key in ("efficiency_difference_variance", "possession_cv_squared")
        )
        score_moments = any(
            key in context
            for key in (
                "home_dispersion_index",
                "away_dispersion_index",
                "score_covariance",
            )
        )
        explicit = "margin_variance" in context
        if sum((mechanistic, score_moments, explicit)) > 1:
            raise ValueError("Supply exactly one margin variance specification.")

        if explicit:
            variance = self._positive(context["margin_variance"], "margin_variance")
        elif mechanistic:
            required = (
                "possessions",
                "efficiency_difference_variance",
                "possession_cv_squared",
            )
            if any(key not in context for key in required):
                raise ValueError(f"Mechanistic variance requires {required}.")
            possessions = self._positive(context["possessions"], "possessions")
            efficiency_variance = self._nonnegative(
                context["efficiency_difference_variance"],
                "efficiency_difference_variance",
            )
            pace_cv_squared = self._nonnegative(
                context["possession_cv_squared"], "possession_cv_squared"
            )
            variance = (
                possessions * efficiency_variance
                + margin * margin * pace_cv_squared
            )
        else:
            home_variance = home * self._positive(
                context.get("home_dispersion_index", self.POISSON_DISPERSION),
                "home_dispersion_index",
            )
            away_variance = away * self._positive(
                context.get("away_dispersion_index", self.POISSON_DISPERSION),
                "away_dispersion_index",
            )
            covariance = self._number(
                context.get("score_covariance", 0.0), "score_covariance"
            )
            covariance_bound = math.sqrt(home_variance) * math.sqrt(away_variance)
            if abs(covariance) > covariance_bound:
                raise ValueError("Score covariance violates positive semidefiniteness.")
            variance = home_variance + away_variance - 2.0 * covariance

        variance = self._positive(variance, "derived margin variance")
        poisson_fallback = not (
            explicit
            or mechanistic
            or score_moments
            or "margin_degrees_of_freedom" in context
        )
        return variance, poisson_fallback

    @staticmethod
    def _normalize(probabilities: np.ndarray) -> np.ndarray:
        if not np.all(np.isfinite(probabilities)) or np.any(probabilities < 0.0):
            raise ArithmeticError("Probability computation produced invalid mass.")
        total = math.fsum(float(value) for value in probabilities)
        if total <= 0.0:
            raise ArithmeticError("Probability computation produced zero total mass.")
        return probabilities / total

    def _components(
        self, context: Mapping[str, Any]
    ) -> Tuple[np.ndarray, np.ndarray, float]:
        self._validate_context(context)
        home, away, margin = self._score_means(context)
        variance, poisson_fallback = self._variance(context, home, away, margin)

        if poisson_fallback:
            distribution = skellam(mu1=home, mu2=away)
            probabilities = np.array(
                [
                    distribution.sf(0),
                    distribution.pmf(0),
                    distribution.cdf(-1),
                ],
                dtype=float,
            )
            positive_donor = math.fsum(
                float(distribution.pmf(k))
                for k in range(1, self.CLOSE_MARGIN_RADIUS + 1)
            )
            negative_donor = math.fsum(
                float(distribution.pmf(-k))
                for k in range(1, self.CLOSE_MARGIN_RADIUS + 1)
            )
        else:
            if "margin_degrees_of_freedom" in context:
                degrees = self._number(
                    context["margin_degrees_of_freedom"],
                    "margin_degrees_of_freedom",
                )
                if degrees <= 2.0:
                    raise ValueError("Finite-variance Student-t requires df > 2.")
                scale = math.sqrt(variance) * math.sqrt(1.0 - 2.0 / degrees)
                distribution = t(df=degrees, loc=margin, scale=scale)
            else:
                distribution = norm(loc=margin, scale=math.sqrt(variance))

            def interval_mass(lower: float, upper: float) -> float:
                # Use survival probabilities in the upper tail to avoid
                # subtracting two values rounded close to one.
                if lower >= margin:
                    value = distribution.sf(lower) - distribution.sf(upper)
                else:
                    value = distribution.cdf(upper) - distribution.cdf(lower)
                return float(value)

            half = self.HALF_LATTICE_WIDTH
            boundary = self.CLOSE_MARGIN_RADIUS + half
            probabilities = np.array(
                [
                    distribution.sf(half),
                    interval_mass(-half, half),
                    distribution.cdf(-half),
                ],
                dtype=float,
            )
            positive_donor = interval_mass(half, boundary)
            negative_donor = interval_mass(-boundary, -half)

        raw_total = math.fsum(float(value) for value in probabilities)
        probabilities = self._normalize(probabilities)
        donors = np.array([positive_donor, 0.0, negative_donor]) / raw_total
        if not np.all(np.isfinite(donors)) or np.any(donors < 0.0):
            raise ArithmeticError("Invalid close-margin mass.")

        # This projection corrects floating-point inconsistency only: each
        # donor event is mathematically a subset of its corresponding outcome.
        tolerance = np.finfo(float).eps * len(self.LABELS)
        if np.any(donors > probabilities + tolerance):
            raise ArithmeticError("Close-margin mass exceeds its outcome mass.")
        donors = np.minimum(donors, probabilities)

        standardized_margin = margin / math.sqrt(variance)
        closeness = math.exp(-0.5 * standardized_margin * standardized_margin)
        return probabilities, donors, closeness

    def _apply_kernel(
        self,
        probabilities: np.ndarray,
        donors: np.ndarray,
        closeness: float,
        transfer_fraction: float,
    ) -> np.ndarray:
        """
        Move a bounded fraction of nearby integer-margin mass onto a tie.

        This is a mass-conserving transition kernel, so it cannot demand more
        tie inflation than the neighboring outcomes can supply.
        """
        moved = donors * (transfer_fraction * closeness)
        result = probabilities - moved
        result[1] += math.fsum(float(value) for value in moved)
        return self._normalize(result)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        probabilities, donors, closeness = self._components(game_context)
        transfer = self._fraction(
            game_context.get(
                "tie_transfer_fraction", self.tie_transfer_fraction
            ),
            "tie_transfer_fraction",
        )
        result = self._apply_kernel(
            probabilities, donors, closeness, transfer
        )
        return DiscreteDistribution(
            probabilities={
                label: float(probability)
                for label, probability in zip(self.LABELS, result)
            }
        )

    def fit_tie_transfer(
        self,
        contexts: Sequence[Mapping[str, Any]],
        regulation_outcomes: Sequence[str],
        sample_weights: Optional[Sequence[float]] = None,
    ) -> Dict[str, float]:
        """
        Fit the single tie-kernel coefficient by weighted multinomial likelihood.

        Inputs must contain regulation labels, not final overtime winners.
        Baseline means and variance/tail parameters must already be estimated
        independently. This method neither fits those parameters nor establishes
        out-of-sample skill.
        """
        count = len(contexts)
        if count == 0 or len(regulation_outcomes) != count:
            raise ValueError("Nonempty contexts and matching outcomes are required.")
        if sample_weights is None:
            weights = np.ones(count, dtype=float)
        else:
            if len(sample_weights) != count:
                raise ValueError("sample_weights must match the number of contexts.")
            weights = np.array(
                [
                    self._nonnegative(value, "sample_weight")
                    for value in sample_weights
                ],
                dtype=float,
            )
        if not np.any(weights > 0.0):
            raise ValueError("At least one sample weight must be positive.")
        weights /= weights.max()
        weights /= weights.sum()

        rows = []
        for context, outcome, weight in zip(contexts, regulation_outcomes, weights):
            if outcome not in self.LABELS:
                raise ValueError("Outcomes must be 'home', 'draw', or 'away'.")
            if "tie_transfer_fraction" in context:
                raise ValueError("Training contexts must omit tie_transfer_fraction.")
            if weight == 0.0:
                continue
            base, donors, closeness = self._components(context)
            direction = -donors * closeness
            direction[1] += float(donors.sum()) * closeness
            index = self.LABELS.index(outcome)
            rows.append((base[index], direction[index], float(weight)))

        if not any(direction != 0.0 for _, direction, _ in rows):
            raise ValueError("Tie transfer is unidentifiable in the supplied sample.")

        def negative_log_likelihood(fraction: float) -> float:
            terms = []
            for base, direction, weight in rows:
                probability = base + fraction * direction
                if probability <= 0.0 or not math.isfinite(probability):
                    return math.inf
                terms.append(-weight * math.log(probability))
            return math.fsum(terms)

        optimized = minimize_scalar(
            negative_log_likelihood,
            bounds=(0.0, 1.0),
            method="bounded",
            options={"xatol": math.sqrt(np.finfo(float).eps)},
        )
        if not optimized.success:
            raise ArithmeticError(f"Tie calibration failed: {optimized.message}")

        candidates = (0.0, float(optimized.x), 1.0)
        fitted = min(candidates, key=negative_log_likelihood)
        fitted_loss = negative_log_likelihood(fitted)
        if not math.isfinite(fitted_loss):
            raise ArithmeticError("Calibration sample has numerically impossible outcomes.")

        self.tie_transfer_fraction = fitted
        return {
            "tie_transfer_fraction": fitted,
            "training_negative_log_likelihood": fitted_loss,
            "baseline_training_negative_log_likelihood": negative_log_likelihood(0.0),
            "effective_sample_size": float(1.0 / np.dot(weights, weights)),
        }