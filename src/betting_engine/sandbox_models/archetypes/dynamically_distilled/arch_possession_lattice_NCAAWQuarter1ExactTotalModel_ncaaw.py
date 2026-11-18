from math import exp, floor, fsum, isfinite, log
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAWQuarter1ExactTotalModel(BaseCandidateModel):
    """
    Deterministic, bookmaker-independent first-quarter score model.

    Cold-start identification:
        Means of 20.00 and 18.75 and an assumed dispersion index of one
        do not identify possession counts, shooting profiles, rest effects,
        or adjacent-integer scoring probabilities. Without independently
        estimated mechanics inputs, use the supplied independent-Poisson
        prior. This fallback is explicitly a prior, not measured calibration.

    Mechanics mode:
        Supply all three:
          possession_count_probabilities: {shared_count: probability}
          home_points_per_possession: {points: probability}
          away_points_per_possession: {points: probability}

        Optional possession_offsets:
          {(home_offset, away_offset): probability}
        Defaults to {(0, 0): 1}. Every offset pair must differ by at most
        one possession. Offset probabilities are independent of the shared
        count. Encode opening/closing-possession uncertainty here.

        Optional pace:
          quarter_possessions: desired mean shared possession count
        Or supply both:
          home.tempo, away.tempo: possessions per regulation game
          regulation_minutes, quarter_minutes

        Count and scoring profiles are exponentially tilted to match the
        requested means. Tilting preserves supplied support, including its
        lattice, and minimizes KL divergence from each supplied profile.

        Conditional on shared count N and offsets (d_h, d_a), the joint PGF is
            phi_h(x) ** (N + d_h) * phi_a(y) ** (N + d_a).
        Mixing these conditional laws preserves shared-pace covariance.
        Points-per-possession probabilities, including outcomes above three,
        must come from pregame, externally estimated information.

    Situational inputs:
        home_expected, away_expected are quarter-scale means.

        Optional mean_multipliers: {"home": positive, "away": positive}.
        Optional home/away.attack_multiplier and defense_multiplier are
        dimensionless scoring multipliers. A defense multiplier above one
        means more points allowed. They default to the multiplicative identity.

        Optional reference_quarter_possessions scales expected points by the
        actual/reference possession ratio. Omit it when expected scores
        already incorporate pace.

        Optional rest_effects:
          {
            "reference_days": finite,
            "scoring_log_coefficient": finite,
            "pace_log_coefficient": finite
          }
        Nonzero coefficients require both teams' rest days. Coefficients must
        be estimated out of sample; no rest effect is invented by default.

    Numerical limits:
        Mechanics distributions have finite supplied support and are fully
        convolved. The Poisson fallback uses a numerical tail tolerance,
        then normalizes the retained joint law. max_score_cells, if supplied,
        is a computational budget: exceeding it raises instead of clipping
        or changing the forecast.

    All model/profile estimation and validation must be chronological and
    external to prediction. No ROI or Brier-skill improvement is guaranteed.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_1_1108_020519",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_1",
        family: str = "exact_total",
        *,
        tail_probability: Optional[float] = None,
        max_score_cells: Optional[int] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.tail_probability = (
            float(np.sqrt(np.finfo(float).eps))
            if tail_probability is None
            else self._number(tail_probability, "tail_probability")
        )
        if not 0.0 < self.tail_probability < 1.0:
            raise ValueError("tail_probability must lie strictly between zero and one.")
        if max_score_cells is not None:
            max_score_cells = self._integer(max_score_cells, "max_score_cells")
            if max_score_cells < 1:
                raise ValueError("max_score_cells must be positive.")
        self.max_score_cells = max_score_cells

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _integer(cls, value: Any, name: str) -> int:
        result = cls._number(value, name)
        if result != floor(result):
            raise ValueError(f"{name} must be an integer.")
        return int(result)

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _pmf(cls, value: Any, name: str) -> Dict[int, float]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError(f"{name} must be a nonempty probability mapping.")
        result: Dict[int, float] = {}
        for key, weight in value.items():
            outcome = cls._integer(key, f"{name}.outcome")
            probability = cls._number(weight, f"{name}.probability")
            if outcome < 0 or probability < 0.0:
                raise ValueError(f"{name} requires nonnegative outcomes and weights.")
            if probability > 0.0:
                result[outcome] = result.get(outcome, 0.0) + probability
        return cls._normalize_mapping(result, name)

    @staticmethod
    def _normalize_mapping(value: Mapping, name: str) -> Dict:
        if not value:
            raise ValueError(f"{name} must contain positive probability mass.")
        scale = max(value.values())
        scaled = {key: weight / scale for key, weight in value.items()}
        mass = fsum(scaled.values())
        if not isfinite(mass) or mass <= 0.0:
            raise ValueError(f"{name} has invalid total mass.")
        return {key: weight / mass for key, weight in scaled.items()}

    @classmethod
    def _offsets(cls, value: Any) -> Dict[Tuple[int, int], float]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError("possession_offsets must be a nonempty mapping.")
        result: Dict[Tuple[int, int], float] = {}
        for pair, weight in value.items():
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("Each possession offset key must be a two-item tuple.")
            home = cls._integer(pair[0], "home possession offset")
            away = cls._integer(pair[1], "away possession offset")
            if abs(home - away) > 1:
                raise ValueError("Team possession counts may differ by at most one.")
            probability = cls._number(weight, "possession offset probability")
            if probability < 0.0:
                raise ValueError("Possession offset probabilities cannot be negative.")
            if probability > 0.0:
                key = (home, away)
                result[key] = result.get(key, 0.0) + probability
        return cls._normalize_mapping(result, "possession_offsets")

    @staticmethod
    def _mean(pmf: Mapping[int, float]) -> float:
        return fsum(outcome * probability for outcome, probability in pmf.items())

    @classmethod
    def _tilt(
        cls, pmf: Mapping[int, float], target: float, name: str
    ) -> Dict[int, float]:
        """KL-minimal mean adjustment with no support clipping."""
        target = cls._number(target, f"{name} target")
        support = np.asarray(sorted(pmf), dtype=float)
        lower, upper = float(support[0]), float(support[-1])
        if target < lower or target > upper:
            raise ValueError(
                f"{name} target {target} is outside supplied support [{lower}, {upper}]."
            )
        if target == lower:
            return {int(lower): 1.0}
        if target == upper:
            return {int(upper): 1.0}

        # Rescaling improves conditioning without changing the tilted family.
        scaled = (support - lower) / (upper - lower)
        scaled_target = (target - lower) / (upper - lower)
        log_weights = np.asarray([log(pmf[int(k)]) for k in support])

        def probabilities(theta: float) -> np.ndarray:
            values = log_weights + theta * scaled
            return np.exp(values - logsumexp(values))

        def residual(theta: float) -> float:
            return float(np.dot(scaled, probabilities(theta))) - scaled_target

        at_zero = residual(0.0)
        if at_zero == 0.0:
            return dict(pmf)

        left, right = -1.0, 1.0
        while residual(left) > 0.0:
            left *= 2.0
            if not isfinite(left):
                raise ArithmeticError(f"Could not bracket {name} tilt.")
        while residual(right) < 0.0:
            right *= 2.0
            if not isfinite(right):
                raise ArithmeticError(f"Could not bracket {name} tilt.")

        theta = brentq(
            residual,
            left,
            right,
            xtol=np.finfo(float).eps,
            rtol=4.0 * np.finfo(float).eps,
        )
        weights = probabilities(theta)
        return cls._normalize_mapping(
            {
                int(outcome): float(weight)
                for outcome, weight in zip(support, weights)
                if weight > 0.0
            },
            name,
        )

    def _check_cells(self, home_size: int, away_size: int) -> None:
        if (
            self.max_score_cells is not None
            and home_size * away_size > self.max_score_cells
        ):
            raise MemoryError(
                "Requested score support exceeds max_score_cells; "
                "no probability support was clipped."
            )

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    def _situational_inputs(
        self, context: Mapping, count_mean: Optional[float]
    ) -> Tuple[float, float, Optional[float]]:
        home = self._mapping(context.get("home", {}), "home")
        away = self._mapping(context.get("away", {}), "away")
        multipliers = self._mapping(
            context.get("mean_multipliers", {}), "mean_multipliers"
        )
        rest = self._mapping(context.get("rest_effects", {}), "rest_effects")

        scoring_beta = self._number(
            rest.get("scoring_log_coefficient", 0.0), "scoring_log_coefficient"
        )
        pace_beta = self._number(
            rest.get("pace_log_coefficient", 0.0), "pace_log_coefficient"
        )
        rest_home = rest_away = 0.0
        if scoring_beta != 0.0 or pace_beta != 0.0:
            reference = self._number(rest["reference_days"], "reference_days")
            home_days = self._number(context["home_rest_days"], "home_rest_days")
            away_days = self._number(context["away_rest_days"], "away_rest_days")
            if home_days < 0.0 or away_days < 0.0:
                raise ValueError("Rest days cannot be negative.")
            rest_home, rest_away = home_days - reference, away_days - reference

        pace = count_mean
        if "quarter_possessions" in context:
            pace = self._positive(context["quarter_possessions"], "quarter_possessions")
        elif "tempo" in home or "tempo" in away:
            home_tempo = self._positive(home["tempo"], "home.tempo")
            away_tempo = self._positive(away["tempo"], "away.tempo")
            game_minutes = self._positive(
                context["regulation_minutes"], "regulation_minutes"
            )
            quarter_minutes = self._positive(
                context["quarter_minutes"], "quarter_minutes"
            )
            # Each alternating pair consumes one possession from each team.
            pace = (
                2.0 / (1.0 / home_tempo + 1.0 / away_tempo)
                * quarter_minutes
                / game_minutes
            )

        if pace_beta != 0.0:
            if pace is None:
                raise ValueError("A pace rest effect requires a possession baseline.")
            pace *= exp(pace_beta * (rest_home + rest_away) / 2.0)
        if pace is not None:
            pace = self._positive(pace, "adjusted quarter possessions")

        pace_multiplier = 1.0
        if "reference_quarter_possessions" in context:
            if pace is None:
                raise ValueError("Reference pace requires an actual possession mean.")
            pace_multiplier = pace / self._positive(
                context["reference_quarter_possessions"],
                "reference_quarter_possessions",
            )

        means = []
        for side, own, opponent, anchor, rest_delta in (
            ("home", home, away, 20.00, rest_home),
            ("away", away, home, 18.75, rest_away),
        ):
            mean = self._number(context.get(f"{side}_expected", anchor), f"{side}_expected")
            if mean < 0.0:
                raise ValueError("Expected scores cannot be negative.")
            mean *= self._positive(multipliers.get(side, 1.0), f"{side} mean multiplier")
            mean *= self._positive(own.get("attack_multiplier", 1.0), "attack_multiplier")
            mean *= self._positive(
                opponent.get("defense_multiplier", 1.0), "defense_multiplier"
            )
            mean *= pace_multiplier * exp(scoring_beta * rest_delta)
            means.append(self._number(mean, f"adjusted {side} mean"))

        return means[0], means[1], pace

    def _poisson_scores(self, home_mean: float, away_mean: float) -> np.ndarray:
        # Union bound limits omitted joint mass by tail_probability.
        marginal_tail = self.tail_probability / 2.0
        if marginal_tail == 0.0:
            raise ValueError("tail_probability is too small for floating-point arithmetic.")

        def marginal(mean: float) -> np.ndarray:
            if mean == 0.0:
                return np.ones(1, dtype=float)
            quantile = float(poisson.isf(marginal_tail, mean))
            if not isfinite(quantile):
                raise ArithmeticError("Poisson support could not be resolved numerically.")
            upper = int(quantile)
            while poisson.sf(upper, mean) > marginal_tail:
                upper += 1
            return poisson.pmf(np.arange(upper + 1), mean)

        # Determine support before allocating the joint score matrix.
        home = marginal(home_mean)
        away = marginal(away_mean)
        self._check_cells(home.size, away.size)
        return np.outer(home, away)

    @staticmethod
    def _powers(
        pmf: Mapping[int, float], required: set
    ) -> Dict[int, np.ndarray]:
        atom = np.zeros(max(pmf) + 1, dtype=float)
        for points, probability in pmf.items():
            atom[points] = probability

        current = np.ones(1, dtype=float)
        result: Dict[int, np.ndarray] = {}
        if 0 in required:
            result[0] = current
        for count in range(1, max(required) + 1):
            current = np.convolve(current, atom)
            current /= current.sum()
            if count in required:
                result[count] = current
        return result

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        required_keys = (
            "possession_count_probabilities",
            "home_points_per_possession",
            "away_points_per_possession",
        )
        supplied = [key in context for key in required_keys]
        if any(supplied) and not all(supplied):
            raise ValueError(
                "Mechanics mode requires possession_count_probabilities and "
                "both teams' points-per-possession profiles."
            )

        counts = (
            self._pmf(context[required_keys[0]], required_keys[0])
            if all(supplied)
            else None
        )
        home_mean, away_mean, pace = self._situational_inputs(
            context, None if counts is None else self._mean(counts)
        )

        if counts is None:
            if "possession_offsets" in context:
                raise ValueError("possession_offsets requires mechanics-mode profiles.")
            matrix = self._poisson_scores(home_mean, away_mean)
        else:
            counts = self._tilt(counts, pace, "possession count")
            offsets = self._offsets(context.get("possession_offsets", {(0, 0): 1.0}))
            joint_counts: Dict[Tuple[int, int], float] = {}

            for count, count_weight in counts.items():
                for (home_offset, away_offset), offset_weight in offsets.items():
                    pair = (count + home_offset, count + away_offset)
                    if min(pair) < 0:
                        raise ValueError("An offset produces a negative possession count.")
                    joint_counts[pair] = (
                        joint_counts.get(pair, 0.0) + count_weight * offset_weight
                    )

            home_n = fsum(h * weight for (h, _), weight in joint_counts.items())
            away_n = fsum(a * weight for (_, a), weight in joint_counts.items())

            def calibrated_profile(side: str, mean: float, possessions: float) -> Dict:
                profile = self._pmf(
                    context[f"{side}_points_per_possession"],
                    f"{side}_points_per_possession",
                )
                if possessions == 0.0:
                    if mean != 0.0:
                        raise ValueError("Positive expected points require possessions.")
                    return {0: 1.0}
                return self._tilt(profile, mean / possessions, f"{side} scoring")

            home_profile = calibrated_profile("home", home_mean, home_n)
            away_profile = calibrated_profile("away", away_mean, away_n)
            home_counts = {pair[0] for pair in joint_counts}
            away_counts = {pair[1] for pair in joint_counts}

            home_size = max(home_counts) * max(home_profile) + 1
            away_size = max(away_counts) * max(away_profile) + 1
            self._check_cells(home_size, away_size)

            home_powers = self._powers(home_profile, home_counts)
            away_powers = self._powers(away_profile, away_counts)
            matrix = np.zeros((home_size, away_size), dtype=float)

            for (home_count, away_count), weight in joint_counts.items():
                home = home_powers[home_count]
                away = away_powers[away_count]
                matrix[:home.size, :away.size] += weight * np.outer(home, away)

        mass = float(matrix.sum())
        if (
            not np.all(np.isfinite(matrix))
            or np.any(matrix < 0.0)
            or not isfinite(mass)
            or mass <= 0.0
        ):
            raise ArithmeticError("Score distribution failed numerical validation.")
        matrix /= mass

        scores = {
            (int(home), int(away)): float(matrix[home, away])
            for home, away in zip(*np.nonzero(matrix))
        }
        return DiscreteDistribution.from_scores(scores)