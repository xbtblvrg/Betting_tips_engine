from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAMSecondHalfMoneylineModel(BaseCandidateModel):
    """
    Independent second-half score model with explicit settlement semantics.

    Identification
    --------------
    The supplied cold-start information identifies score means (40, 37.5)
    and a provisional variance/mean ratio of one. It does not identify
    possession counts, rest effects, lead reversion, or home-edge shrinkage.
    No fitted effects or performance improvement are claimed.

    When possession information is available, each shared possession produces
    independent team scores on an explicitly specified scoring lattice.
    Maximum-entropy possession probabilities match the requested scoring mean
    and variance. Shared possession-count uncertainty induces score covariance.

    Without possession information, independent Poisson scores implement the
    supplied cold-start prior. This fallback is not represented as an empirical
    description of basketball possessions.

    Context contract
    ----------------
    home_expected, away_expected:
        Expected points for the entire second half, excluding overtime.
    possessions:
        Expected possessions per team during the second half.
    pace:
        Alias for possessions; MUST already be expressed per second half.
    points_per_possession:
        Alternative common PPP used to infer expected possession count.
    possession_count_probabilities:
        Optional mapping of integer counts to probabilities. Captures pace
        uncertainty directly; cannot be combined with the fields above.
    home_scoring_support, away_scoring_support:
        Possession scoring support; defaults to basketball's 0, 1, 2, 3.
        Supply expanded support when modeling four-point possessions, etc.
    home_possession_variance, away_possession_variance:
        Conditional per-possession variance. Otherwise PPP times the supplied
        dispersion_index (default one).
    home_attack_multiplier, away_attack_multiplier:
        Positive independently estimated offensive exposure multipliers.
    home_defense_multiplier, away_defense_multiplier:
        Positive points-allowed multipliers; applied to the opposing offense.
    reference_possessions:
        Optional exposure at which supplied expected points were estimated.
        Requires possession information and rescales both means.
    rest_log_rate_coefficient, travel_log_rate_coefficient:
        Optional externally estimated coefficients. Nonzero coefficients
        require both teams' corresponding rest/travel inputs.
    margin_scale:
        Optional externally estimated shrinkage of the adjusted margin.
        Defaults to identity; no unsupported home-advantage estimate is added.
    lead_reversion_coefficient:
        Optional externally estimated second-half response to halftime lead.
    halftime_home_lead:
        Signed integer home lead at halftime.
    settlement_rule:
        "push" (default), "three_way", or "include_overtime".
        For push markets, draw represents refunded probability, not a loss.
    overtime_margin_probabilities:
        Required for include_overtime: an independently estimated distribution
        of HOME minus AWAY points across ALL overtime periods, conditional on
        a regulation game tie. Integer support must exclude zero. Overtime is
        triggered by the FULL-GAME tie, not by a second-half score tie.

    No bookmaker probabilities enter the forecast. A downstream bettor should
    evaluate a push contract using p_win * (decimal_odds - 1) - p_loss.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_half_2_1108_012429",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "half_2",
        family: str = "moneyline",
    ) -> None:
        if (league, scope, family) != ("NCAAM", "half_2", "moneyline"):
            raise ValueError("This model supports only NCAAM|half_2|moneyline.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

    @staticmethod
    def _number(
        context: Mapping[str, Any],
        key: str,
        default: Optional[float] = None,
    ) -> float:
        value = context.get(key, default)
        if value is None or isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{key} must be a finite number.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{key} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{key} must be finite.")
        return result

    @classmethod
    def _integer(cls, value: Any, name: str) -> int:
        numeric = cls._number({name: value}, name)
        if not numeric.is_integer():
            raise ValueError(f"{name} must be an integer.")
        return int(numeric)

    @classmethod
    def _mass_mapping(
        cls, value: Any, name: str, *, nonnegative_support: bool
    ) -> Dict[int, float]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError(f"{name} must be a nonempty probability mapping.")
        result: Dict[int, float] = {}
        for key, mass in value.items():
            point = cls._integer(key, f"{name} support")
            if nonnegative_support and point < 0:
                raise ValueError(f"{name} support must be nonnegative.")
            probability = cls._number({"mass": mass}, "mass")
            if probability < 0:
                raise ValueError(f"{name} probabilities must be nonnegative.")
            result[point] = result.get(point, 0.0) + probability
        total = math.fsum(result.values())
        if not math.isfinite(total) or total <= 0:
            raise ValueError(f"{name} must have positive finite total mass.")
        tolerance = math.sqrt(np.finfo(float).eps)
        if not math.isclose(total, 1.0, rel_tol=tolerance, abs_tol=tolerance):
            raise ValueError(f"{name} probabilities must sum to one.")
        return {point: mass / total for point, mass in result.items() if mass > 0}

    @classmethod
    def _counts(
        cls, context: Mapping[str, Any], total_mean: float
    ) -> Optional[Dict[int, float]]:
        scalar_keys = ("possessions", "pace", "points_per_possession")
        present = [key for key in scalar_keys if key in context]
        if "possession_count_probabilities" in context:
            if present:
                raise ValueError("Specify a count distribution OR a scalar pace.")
            return cls._mass_mapping(
                context["possession_count_probabilities"],
                "possession_count_probabilities",
                nonnegative_support=True,
            )
        if len(present) > 1:
            raise ValueError("Specify only one possession-count input.")
        if not present:
            return None
        key = present[0]
        value = cls._number(context, key)
        if value <= 0:
            raise ValueError(f"{key} must be positive.")
        count = total_mean / (2.0 * value) if key == "points_per_possession" else value
        if not math.isfinite(count) or count <= 0:
            raise ValueError("Inferred possession count must be positive and finite.")
        lower = math.floor(count)
        fraction = count - lower
        if fraction == 0:
            return {lower: 1.0}
        # The minimum-variance integer count distribution with this expectation.
        return {lower: 1.0 - fraction, lower + 1: fraction}

    @staticmethod
    def _possession_pmf(
        mean: float, variance: float, support: Any
    ) -> np.ndarray:
        raw = list(support)
        if not raw:
            raise ValueError("Scoring support cannot be empty.")
        points = np.array(
            sorted({
                NCAAMSecondHalfMoneylineModel._integer(x, "scoring support")
                for x in raw
            }),
            dtype=float,
        )
        if points[0] < 0:
            raise ValueError("Possession scores must be nonnegative.")
        if not math.isfinite(variance) or variance < 0:
            raise ValueError("Possession variance must be finite and nonnegative.")
        if not points[0] <= mean <= points[-1]:
            raise ValueError("PPP is outside the scoring support.")

        scale = max(1.0, float(points[-1] ** 2), abs(mean), variance)
        tolerance = math.sqrt(np.finfo(float).eps) * scale
        lower = points[points <= mean][-1]
        upper = points[points >= mean][0]
        minimum = (mean - lower) * (upper - mean)
        maximum = (mean - points[0]) * (points[-1] - mean)
        if variance < minimum - tolerance or variance > maximum + tolerance:
            raise ValueError(
                "Possession moments are infeasible on the scoring support; "
                "supply calibrated variance or expanded scoring support."
            )

        def two_point(a: float, b: float) -> np.ndarray:
            result = np.zeros(int(points[-1]) + 1)
            if a == b:
                result[int(a)] = 1.0
            else:
                result[int(a)] = (b - mean) / (b - a)
                result[int(b)] = (mean - a) / (b - a)
            return result

        if abs(variance - minimum) <= tolerance:
            return two_point(lower, upper)
        if abs(variance - maximum) <= tolerance:
            return two_point(points[0], points[-1])

        # Exponential-family entropy maximizer under two moment constraints.
        centered = (points - mean) / math.sqrt(variance)
        features = np.column_stack((centered, centered ** 2))
        target = np.array([0.0, 1.0])

        def probabilities(theta: np.ndarray) -> np.ndarray:
            logits = features @ theta
            return np.exp(logits - logsumexp(logits))

        def residual(theta: np.ndarray) -> np.ndarray:
            return probabilities(theta) @ features - target

        def jacobian(theta: np.ndarray) -> np.ndarray:
            weights = probabilities(theta)
            average = weights @ features
            deviations = features - average
            return (deviations.T * weights) @ deviations

        solution = least_squares(
            residual,
            np.zeros(2),
            jac=jacobian,
            ftol=np.finfo(float).eps ** 0.75,
            xtol=np.finfo(float).eps ** 0.75,
            gtol=np.finfo(float).eps ** 0.75,
        )
        if not solution.success or np.linalg.norm(
            residual(solution.x), ord=np.inf
        ) > math.sqrt(np.finfo(float).eps):
            raise ArithmeticError("Possession moment calibration did not converge.")
        result = np.zeros(int(points[-1]) + 1)
        result[points.astype(int)] = probabilities(solution.x)
        return result

    @staticmethod
    def _convolution_power(probabilities: np.ndarray, count: int) -> np.ndarray:
        result = np.array([1.0])
        factor = probabilities
        while count:
            if count & 1:
                result = np.convolve(result, factor)
            count >>= 1
            if count:
                factor = np.convolve(factor, factor)
        return result

    @staticmethod
    def _poisson_pmf(mean: float) -> np.ndarray:
        if mean == 0:
            return np.array([1.0])
        # Tail truncation is determined by floating-point precision, not odds.
        maximum = int(poisson.ppf(np.nextafter(1.0, 0.0), mean))
        probabilities = poisson.pmf(np.arange(maximum + 1), mean)
        return probabilities / probabilities.sum()

    def _means(
        self, context: Mapping[str, Any]
    ) -> Tuple[float, float, Optional[Dict[int, float]]]:
        home = self._number(context, "home_expected", 40.0)
        away = self._number(context, "away_expected", 37.5)
        if home < 0 or away < 0:
            raise ValueError("Expected points must be nonnegative.")
        counts = self._counts(context, home + away)

        for side, opponent in (("home", "away"), ("away", "home")):
            attack = self._number(context, f"{side}_attack_multiplier", 1.0)
            defense = self._number(context, f"{opponent}_defense_multiplier", 1.0)
            if attack <= 0 or defense <= 0:
                raise ValueError("Attack and defense multipliers must be positive.")
            if side == "home":
                home *= attack * defense
            else:
                away *= attack * defense

        if "reference_possessions" in context:
            reference = self._number(context, "reference_possessions")
            if counts is None or reference <= 0:
                raise ValueError("A positive reference and possession input are required.")
            ratio = math.fsum(n * p for n, p in counts.items()) / reference
            home *= ratio
            away *= ratio

        relative_log_rate = 0.0
        for coefficient, feature in (
            ("rest_log_rate_coefficient", "rest_days"),
            ("travel_log_rate_coefficient", "travel_distance"),
        ):
            beta = self._number(context, coefficient, 0.0)
            if beta != 0:
                h = self._number(context, f"home_{feature}")
                a = self._number(context, f"away_{feature}")
                if h < 0 or a < 0:
                    raise ValueError(f"{feature} must be nonnegative.")
                relative_log_rate += beta * (h - a)
        try:
            home *= math.exp(relative_log_rate / 2.0)
            away *= math.exp(-relative_log_rate / 2.0)
        except OverflowError as exc:
            raise ValueError("Situational coefficients produce overflowing means.") from exc

        total = home + away
        margin_scale = self._number(context, "margin_scale", 1.0)
        if margin_scale < 0:
            raise ValueError("margin_scale must be nonnegative.")
        reversion = self._number(context, "lead_reversion_coefficient", 0.0)
        lead = 0
        if reversion != 0:
            lead = self._integer(
                context.get("halftime_home_lead"), "halftime_home_lead"
            )
        margin = (home - away) * margin_scale - reversion * lead
        home, away = (total + margin) / 2.0, (total - margin) / 2.0
        if not all(math.isfinite(x) and x >= 0 for x in (home, away)):
            raise ValueError("Situational adjustments imply invalid expected scores.")
        return home, away, counts

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        home, away, counts = self._means(game_context)
        dispersion = self._number(game_context, "dispersion_index", 1.0)
        if dispersion < 0:
            raise ValueError("dispersion_index must be nonnegative.")

        margin_mass: Dict[int, float] = {}

        if counts is None:
            if dispersion != 1.0:
                raise ValueError(
                    "Non-Poisson dispersion requires possession information."
                )
            if any(
                f"{side}_possession_variance" in game_context
                for side in ("home", "away")
            ):
                raise ValueError("Possession variance requires possession information.")
            home_pmf = self._poisson_pmf(home)
            away_pmf = self._poisson_pmf(away)
            probabilities = np.convolve(home_pmf, away_pmf[::-1])
            offset = len(away_pmf) - 1
            margin_mass = {
                index - offset: float(mass)
                for index, mass in enumerate(probabilities)
                if mass > 0
            }
        else:
            expected_count = math.fsum(n * p for n, p in counts.items())
            if expected_count == 0:
                if home != 0 or away != 0:
                    raise ValueError("Zero possessions cannot produce positive scores.")
                margin_mass = {0: 1.0}
            else:
                team_pmfs = []
                for side, mean in (("home", home), ("away", away)):
                    ppp = mean / expected_count
                    variance = self._number(
                        game_context,
                        f"{side}_possession_variance",
                        ppp * dispersion,
                    )
                    support = game_context.get(
                        f"{side}_scoring_support", (0, 1, 2, 3)
                    )
                    team_pmfs.append(self._possession_pmf(ppp, variance, support))
                per_possession = np.convolve(team_pmfs[0], team_pmfs[1][::-1])
                away_offset = len(team_pmfs[1]) - 1
                for count, weight in sorted(counts.items()):
                    probabilities = self._convolution_power(per_possession, count)
                    offset = count * away_offset
                    for index, mass in enumerate(probabilities):
                        if mass > 0:
                            margin = index - offset
                            margin_mass[margin] = (
                                margin_mass.get(margin, 0.0) + weight * float(mass)
                            )

        rule = game_context.get("settlement_rule", "push")
        if rule not in {"push", "three_way", "include_overtime"}:
            raise ValueError("Unsupported settlement_rule.")

        if rule == "include_overtime":
            lead = self._integer(
                game_context.get("halftime_home_lead"), "halftime_home_lead"
            )
            overtime = self._mass_mapping(
                game_context.get("overtime_margin_probabilities"),
                "overtime_margin_probabilities",
                nonnegative_support=False,
            )
            if 0 in overtime:
                raise ValueError(
                    "Completed overtime must resolve the full-game tie."
                )
            triggering_margin = -lead
            triggering_mass = margin_mass.pop(triggering_margin, 0.0)
            for overtime_margin, probability in overtime.items():
                final_margin = triggering_margin + overtime_margin
                margin_mass[final_margin] = (
                    margin_mass.get(final_margin, 0.0)
                    + triggering_mass * probability
                )

        home_probability = math.fsum(
            mass for margin, mass in margin_mass.items() if margin > 0
        )
        away_probability = math.fsum(
            mass for margin, mass in margin_mass.items() if margin < 0
        )
        draw_probability = margin_mass.get(0, 0.0)
        total = math.fsum(
            (home_probability, draw_probability, away_probability)
        )
        if not math.isfinite(total) or total <= 0:
            raise ArithmeticError("Invalid predictive probability mass.")

        return DiscreteDistribution(
            probabilities={
                "home": home_probability / total,
                "draw": draw_probability / total,
                "away": away_probability / total,
            }
        )