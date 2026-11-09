from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import logsumexp
from scipy.stats import nbinom, poisson, skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


@dataclass(frozen=True)
class RegulationCalibration:
    """
    An externally fitted, strictly out-of-sample calibration artifact.

    Coefficients act on residual log(home_mean / away_mean), preserving
    the expected total. The rest feature is:
        log1p(home_rest_days) - log1p(away_rest_days).

    tie_log_odds shifts draw log odds while preserving the home/away
    conditional odds. Fit it using Bernoulli log loss with the uncalibrated
    draw log odds as an offset.

    Fit all coefficients using regulation scores only. Do not use closing
    prices, overtime-inclusive scores, or the prediction game's outcome.
    """

    trained_through: Optional[datetime] = None
    sample_size: int = 0
    home_log_ratio_residual: float = 0.0
    rest_log_ratio_coefficient: float = 0.0
    tie_log_odds: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.sample_size, bool) or not isinstance(self.sample_size, int):
            raise ValueError("sample_size must be an integer.")
        if self.sample_size < 0:
            raise ValueError("sample_size must be nonnegative.")

        coefficients = (
            self.home_log_ratio_residual,
            self.rest_log_ratio_coefficient,
            self.tie_log_odds,
        )
        if not all(math.isfinite(float(value)) for value in coefficients):
            raise ValueError("Calibration coefficients must be finite.")

        if self.sample_size == 0 and any(value != 0.0 for value in coefficients):
            raise ValueError("Cold-start calibration must be the identity.")

        if self.sample_size > 0:
            if self.trained_through is None:
                raise ValueError("A fitted calibration requires trained_through.")
            _utc(self.trained_through)


def _utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware datetimes or ISO strings.")
    return value.astimezone(timezone.utc)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean.")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite.")
    if positive and result <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return result


class NCAAMFullGameThreeWayCandidate(BaseCandidateModel):
    """
    Deterministic regulation-result model for NCAAM three-way contracts.

    Settlement:
        home = home leads after regulation
        draw = regulation tie, irrespective of the overtime winner
        away = away leads after regulation

    The market's settlement rules must use this definition. An explicitly
    overtime-inclusive game_context is rejected.

    Cold-start identification:
        Means 80/75 and a prior variance/mean ratio of one do not identify
        possession counts, scoring frequencies, covariance, or tie inflation.
        Without possession_mean/pace, the supplied Poisson prior is retained.
        This fallback is explicit; it is not evidence of NCAAM calibration.

    Possession model:
        N is shared by both teams and is Poisson or Gamma-Poisson.
        Conditional on N, each team's possession scores are independent
        categorical draws on {0, 1, 2, 3}. Thus

          E[S_i]   = E[N] e_i
          Var(S_i) = E[N] v_i + Var(N) e_i**2
          Cov(H,A) = Var(N) e_H e_A
          Var(H-A) = E[N](v_H+v_A) + Var(N)(e_H-e_A)**2

        Shared tempo need not compress margin variance below Skellam.
        That conclusion depends on the possession scoring distributions.

        Supplied categorical weights are exponentially tilted to preserve
        their relative structure while matching each expected score.
        With no weights, maximum entropy on the specified support is used.
        This is an identifiable modeling assumption, not an empirical fit.

        The exact compound margin generating function is inverted by FFT.
        The FFT length is selected using a possession-count tail bound.
        Numerical tolerances and memory budgets are computational controls,
        not betting filters or probability clamps.

    Recognized context inputs:
        home_expected, away_expected:
            Regulation point means, authoritative when supplied.
        home / away:
            Optional attack and defense efficiencies, measured in the same
            units as efficiency_reference. Defense means points conceded;
            larger values therefore increase the opponent's expected score.
            These inputs adjust baseline means only when an explicit expected
            score is absent.
        efficiency_reference:
            Required if attack/defense efficiencies are used.
        possession_mean or pace:
            Expected possessions per team during regulation.
        reference_possession_mean:
            Optional pace associated with the baseline score means. Required
            to rescale baseline means by pace; explicit expected scores are
            never rescaled a second time.
        home_possession_probabilities, away_possession_probabilities:
            Four nonnegative scoring weights ordered as 0, 1, 2, 3 points.
        possession_variance:
            Observed or independently predicted variance of possession count.
        total_score_variance:
            Alternative observed/predicted regulation-total variance used to
            identify possession variance.
        home_rest_days, away_rest_days, neutral_venue:
            Used only through fitted residual coefficients.
        prediction_time:
            Required with a nonempty fitted calibration artifact.
        settlement:
            If supplied, must equal "regulation".
        includes_overtime:
            Must be false.

    Four-point possessions, offensive-rebound sequencing, and state-dependent
    endgame tactics are not identified by the supplied cold-start moments.
    The model does not claim to have estimated these effects.

    This class forecasts probabilities only. A downstream execution layer may
    select every outcome with decimal odds >= 1.40 and p * odds > 1, subject
    to the user's sizing and execution policies. Prices never enter forecasts.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_full_game_1108_020328",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "full_game",
        family: str = "three_way",
        *,
        calibration: Optional[RegulationCalibration] = None,
        baseline_home_expected: float = 80.0,
        baseline_away_expected: float = 75.0,
        numerical_tail_tolerance: Optional[float] = None,
        max_fft_size: int = 1 << 20,
    ) -> None:
        if (league, scope, family) != ("NCAAM", "full_game", "three_way"):
            raise ValueError("This candidate supports NCAAM|full_game|three_way only.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.calibration = calibration or RegulationCalibration()
        self.baseline_home_expected = _number(
            baseline_home_expected, "baseline_home_expected", positive=True
        )
        self.baseline_away_expected = _number(
            baseline_away_expected, "baseline_away_expected", positive=True
        )

        tolerance = (
            math.sqrt(np.finfo(float).eps)
            if numerical_tail_tolerance is None
            else numerical_tail_tolerance
        )
        self.numerical_tail_tolerance = _number(
            tolerance, "numerical_tail_tolerance", positive=True
        )
        if not np.finfo(float).eps <= self.numerical_tail_tolerance < 1.0:
            raise ValueError("Tail tolerance must lie in [machine epsilon, 1).")
        if isinstance(max_fft_size, bool) or not isinstance(max_fft_size, int):
            raise ValueError("max_fft_size must be an integer.")
        if max_fft_size < 1:
            raise ValueError("max_fft_size must be positive.")
        self.max_fft_size = max_fft_size

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        context = dict(game_context)

        if context.get("settlement", "regulation") != "regulation":
            raise ValueError("Three-way settlement must refer to regulation.")
        if context.get("includes_overtime", False) is not False:
            raise ValueError("Overtime-inclusive scores cannot price regulation draws.")

        self._validate_calibration_time(context)
        pace = self._pace(context)
        home_mean, away_mean = self._expected_scores(context, pace)

        if pace is None:
            possession_fields = (
                "possession_variance",
                "total_score_variance",
                "home_possession_probabilities",
                "away_possession_probabilities",
            )
            if any(context.get(key) is not None for key in possession_fields):
                raise ValueError("Possession inputs require possession_mean or pace.")

            # The stated unit-dispersion prior is the only supplied cold-start
            # count law; no unobserved pace or correlation is fabricated.
            raw = np.array(
                [
                    skellam.sf(0, home_mean, away_mean),
                    skellam.pmf(0, home_mean, away_mean),
                    skellam.cdf(-1, home_mean, away_mean),
                ],
                dtype=float,
            )
        else:
            home_points = self._possession_law(
                home_mean / pace,
                context.get("home_possession_probabilities"),
                "home",
            )
            away_points = self._possession_law(
                away_mean / pace,
                context.get("away_possession_probabilities"),
                "away",
            )
            count_variance = self._count_variance(
                context, pace, home_points, away_points
            )
            raw = self._compound_margin(pace, count_variance, home_points, away_points)

        probabilities = self._normalize(raw)
        probabilities = self._calibrate_draw(probabilities)

        return DiscreteDistribution(
            probabilities={
                "home": float(probabilities[0]),
                "draw": float(probabilities[1]),
                "away": float(probabilities[2]),
            }
        )

    def _validate_calibration_time(self, context: Mapping[str, Any]) -> None:
        if self.calibration.sample_size == 0:
            return
        if context.get("prediction_time") is None:
            raise ValueError("Fitted calibration requires prediction_time.")
        if _utc(self.calibration.trained_through) >= _utc(context["prediction_time"]):
            raise ValueError("Calibration must precede the prediction timestamp.")

    @staticmethod
    def _pace(context: Mapping[str, Any]) -> Optional[float]:
        values = [
            _number(context[key], key, positive=True)
            for key in ("possession_mean", "pace")
            if context.get(key) is not None
        ]
        if not values:
            return None
        if len(values) == 2 and values[0] != values[1]:
            raise ValueError("pace and possession_mean disagree.")
        return values[0]

    def _expected_scores(
        self, context: Mapping[str, Any], pace: Optional[float]
    ) -> Tuple[float, float]:
        teams = {}
        for side in ("home", "away"):
            team = context.get(side, {})
            if not isinstance(team, Mapping):
                raise ValueError(f"{side} must be a mapping.")
            teams[side] = team

        means = []
        for side, opponent, baseline in (
            ("home", "away", self.baseline_home_expected),
            ("away", "home", self.baseline_away_expected),
        ):
            key = f"{side}_expected"
            if context.get(key) is not None:
                mean = _number(context[key], key, positive=True)
            else:
                log_mean = math.log(baseline)
                for team, feature in ((side, "attack"), (opponent, "defense")):
                    value = teams[team].get(feature)
                    if value is not None:
                        reference = _number(
                            context.get("efficiency_reference"),
                            "efficiency_reference",
                            positive=True,
                        )
                        efficiency = _number(
                            value, f"{team}.{feature}", positive=True
                        )
                        log_mean += math.log(efficiency) - math.log(reference)

                if context.get("reference_possession_mean") is not None:
                    if pace is None:
                        raise ValueError("Pace scaling requires possession_mean.")
                    reference_pace = _number(
                        context["reference_possession_mean"],
                        "reference_possession_mean",
                        positive=True,
                    )
                    log_mean += math.log(pace) - math.log(reference_pace)

                try:
                    mean = math.exp(log_mean)
                except OverflowError as exc:
                    raise ValueError("Expected-score inputs overflow.") from exc
                mean = _number(mean, key, positive=True)
            means.append(mean)

        home_mean, away_mean = means
        neutral = context.get("neutral_venue", False)
        if not isinstance(neutral, bool):
            raise ValueError("neutral_venue must be boolean.")

        adjustment = (
            0.0 if neutral else self.calibration.home_log_ratio_residual
        )
        if self.calibration.rest_log_ratio_coefficient != 0.0:
            rest = []
            for side in ("home", "away"):
                key = f"{side}_rest_days"
                days = _number(context.get(key), key)
                if days < 0.0:
                    raise ValueError("Rest days must be nonnegative.")
                rest.append(math.log1p(days))
            adjustment += self.calibration.rest_log_ratio_coefficient * (
                rest[0] - rest[1]
            )

        total = home_mean + away_mean
        if not math.isfinite(total):
            raise ValueError("Expected total overflows.")

        if adjustment != 0.0:
            logits = np.array(
                [math.log(home_mean) + adjustment, math.log(away_mean)]
            )
            weights = np.exp(logits - logsumexp(logits))
            home_mean, away_mean = total * weights
            if home_mean <= 0.0 or away_mean <= 0.0:
                raise ValueError("Calibration exceeds representable score precision.")

        return float(home_mean), float(away_mean)

    @staticmethod
    def _possession_law(
        mean: float, weights: Optional[Sequence[float]], side: str
    ) -> np.ndarray:
        points = np.arange(4, dtype=float)
        if weights is None:
            prior = np.ones(points.size, dtype=float)
        else:
            prior = np.asarray(weights, dtype=float)
            if prior.shape != points.shape:
                raise ValueError(f"{side} possession weights must have length four.")
            if not np.all(np.isfinite(prior)) or np.any(prior < 0.0):
                raise ValueError(f"{side} possession weights must be finite and nonnegative.")

        support = prior > 0.0
        if not np.any(support):
            raise ValueError(f"{side} possession weights have empty support.")

        x = points[support]
        if not math.isfinite(mean) or mean < x[0] or mean > x[-1]:
            raise ValueError(
                f"{side} expected points per possession are outside scoring support."
            )

        result = np.zeros(points.size, dtype=float)
        if mean == x[0] or mean == x[-1]:
            result[int(mean)] = 1.0
            return result

        log_weights = np.log(prior[support])

        def tilted(theta: float) -> np.ndarray:
            # Centering avoids positive exponential overflow.
            anchor = x[-1] if theta >= 0.0 else x[0]
            logits = log_weights + theta * (x - anchor)
            return np.exp(logits - logsumexp(logits))

        def residual(theta: float) -> float:
            return float(np.dot(x, tilted(theta)) - mean)

        lower, upper = -1.0, 1.0
        while residual(lower) > 0.0:
            lower *= 2.0
            if not math.isfinite(lower):
                raise ArithmeticError("Unable to bracket possession tilt.")
        while residual(upper) < 0.0:
            upper *= 2.0
            if not math.isfinite(upper):
                raise ArithmeticError("Unable to bracket possession tilt.")

        theta = brentq(
            residual,
            lower,
            upper,
            xtol=np.finfo(float).tiny,
            rtol=4.0 * np.finfo(float).eps,
        )
        result[support] = tilted(theta)
        return result

    @staticmethod
    def _count_variance(
        context: Mapping[str, Any],
        pace: float,
        home: np.ndarray,
        away: np.ndarray,
    ) -> float:
        if (
            context.get("possession_variance") is not None
            and context.get("total_score_variance") is not None
        ):
            raise ValueError("Specify one variance calibration target, not both.")

        if context.get("possession_variance") is not None:
            variance = _number(
                context["possession_variance"], "possession_variance"
            )
        elif context.get("total_score_variance") is not None:
            total_variance = _number(
                context["total_score_variance"], "total_score_variance"
            )
            points = np.arange(4, dtype=float)
            home_efficiency = float(np.dot(points, home))
            away_efficiency = float(np.dot(points, away))
            home_variance = float(
                np.dot((points - home_efficiency) ** 2, home)
            )
            away_variance = float(
                np.dot((points - away_efficiency) ** 2, away)
            )
            variance = (
                total_variance - pace * (home_variance + away_variance)
            ) / (home_efficiency + away_efficiency) ** 2
        else:
            # No observed overdispersion: the Gamma-Poisson boundary is Poisson.
            variance = pace

        if not math.isfinite(variance) or variance < pace:
            raise ValueError(
                "Variance target is incompatible with Gamma-Poisson possessions "
                "and the supplied scoring laws; an underdispersed possession "
                "model or different scoring data is required."
            )
        return variance

    def _compound_margin(
        self,
        pace: float,
        count_variance: float,
        home: np.ndarray,
        away: np.ndarray,
    ) -> np.ndarray:
        excess = count_variance - pace
        if excess == 0.0:
            count_law = poisson(mu=pace)
            shape = None
            scale = None
        else:
            scale = excess / pace
            shape = pace / scale
            success_probability = pace / count_variance
            if (
                not math.isfinite(shape)
                or shape <= 0.0
                or not 0.0 < success_probability < 1.0
            ):
                raise ArithmeticError("Possession dispersion is not numerically representable.")
            count_law = nbinom(n=shape, p=success_probability)

        upper = float(count_law.isf(self.numerical_tail_tolerance))
        if not math.isfinite(upper) or upper < 0.0:
            raise ArithmeticError("Unable to bound the possession-count tail.")
        count_limit = int(math.ceil(upper))
        while float(count_law.sf(count_limit)) > self.numerical_tail_tolerance:
            count_limit += 1
            if 6 * count_limit + 1 > self.max_fft_size:
                raise ValueError("Possession tail exceeds the configured FFT budget.")

        required_size = 6 * count_limit + 1
        fft_size = 1 << (required_size - 1).bit_length()
        if fft_size > self.max_fft_size:
            raise ValueError(
                "Requested tail accuracy exceeds max_fft_size; increase the "
                "computational budget or supply an explicit numerical tolerance."
            )

        # One paired possession has margin support {-3, ..., +3}.
        increment = np.zeros(fft_size, dtype=float)
        for home_points, home_probability in enumerate(home):
            for away_points, away_probability in enumerate(away):
                increment[(home_points - away_points) % fft_size] += (
                    home_probability * away_probability
                )

        transform = np.fft.fft(increment)
        if shape is None:
            log_transform = pace * (transform - 1.0)
        else:
            log_transform = -shape * np.log1p(scale * (1.0 - transform))

        compounded = np.exp(log_transform)
        compounded[0] = 1.0
        lattice = np.fft.ifft(compounded).real

        numerical_error = (
            np.finfo(float).eps
            * fft_size
            * max(1.0, math.log2(fft_size))
        )
        if not np.all(np.isfinite(lattice)) or np.any(lattice < -numerical_error):
            raise ArithmeticError("FFT inversion failed numerical validation.")

        # Remove floating-point roundoff only; this is not a statistical clamp.
        lattice[lattice < 0.0] = 0.0
        lattice /= lattice.sum()

        # The Nyquist cell is necessarily in the bounded aliasing tail.
        # Split its sign ambiguity symmetrically.
        midpoint = fft_size // 2
        if fft_size == 1:
            return np.array([0.0, 1.0, 0.0])
        nyquist = float(lattice[midpoint])
        return np.array(
            [
                float(lattice[1:midpoint].sum()) + nyquist / 2.0,
                float(lattice[0]),
                float(lattice[midpoint + 1 :].sum()) + nyquist / 2.0,
            ]
        )

    @staticmethod
    def _normalize(probabilities: np.ndarray) -> np.ndarray:
        if (
            probabilities.shape != (3,)
            or not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
        ):
            raise ArithmeticError("Invalid three-way probability vector.")
        total = float(probabilities.sum())
        if total <= 0.0:
            raise ArithmeticError("Three-way probability mass is empty.")
        return probabilities / total

    def _calibrate_draw(self, probabilities: np.ndarray) -> np.ndarray:
        offset = self.calibration.tie_log_odds
        if offset == 0.0:
            return probabilities

        # Exponential tilting is valid for every finite fitted coefficient.
        # Multiplying p(draw) directly by an unconstrained empirical ratio
        # can exceed one; an odds tilt avoids that defect without clipping.
        log_mass = np.full(3, -np.inf, dtype=float)
        positive = probabilities > 0.0
        log_mass[positive] = np.log(probabilities[positive])
        log_mass[1] += offset
        calibrated = np.exp(log_mass - logsumexp(log_mass))
        return self._normalize(calibrated)