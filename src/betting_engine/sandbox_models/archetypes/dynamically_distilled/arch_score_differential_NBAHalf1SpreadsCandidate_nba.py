from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.signal import fftconvolve
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAHalf1SpreadsCandidate(BaseCandidateModel):
    """
    Integer-score model for NBA first-half spreads.

    Derivation
    ----------
    Write H = U + C and A = V + C, with independent nonnegative
    integer components. All components have variance / mean D.
    Consequently:

        E[H] = mu_h
        E[A] = mu_a
        Var(H) = D * mu_h
        Var(A) = D * mu_a
        Cov(H, A) = D * E[C]
        Var(H - A) = D * (mu_h + mu_a - 2 * E[C])

    Gamma-mixed Poisson components give negative-binomial counts when
    D > 1. Their common success probability makes each marginal exactly
    negative binomial. C represents equal-score common variation, not
    literal simultaneous baskets. Every integer margin, including a
    push, remains representable.

    The supplied dispersion index alone does not identify covariance or
    differential tails. With no additional evidence, covariance is zero:
    the independent joint distribution maximizes entropy for the selected
    marginals. Supply a held-out calibrated shared_fraction or conditional
    margin variance to identify common variation. This model cannot
    represent negative score covariance and rejects incompatible moments.

    Expected scores must refer to the first half. Exposure inputs are
    positive multiplicative adjustments relative to 1, with defense
    exposure meaning opponent scoring allowed. Rest coefficients must be
    estimated outside this inference class using chronological training
    data; their default zero avoids inventing a rest effect.

    Aggregate side PnL does not identify an away-side probability penalty.
    Neither bookmaker lines nor odds enter this distribution. Pricing,
    the requested minimum odds of 1.40, and positive-EV eligibility belong
    in the downstream settlement-aware contract evaluator.

    Truncation is controlled by numerical tolerance, not score or odds
    clamps. Latency and profitability require measurement on the actual
    engine and deployment hardware; neither can be guaranteed by a class.
    """

    HOME_BASELINE = 56.75
    AWAY_BASELINE = 55.25
    OBSERVED_DISPERSION = 13.420

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1119_154758",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "spreads",
        *,
        dispersion_index: float = OBSERVED_DISPERSION,
        shared_fraction: float = 0.0,
        rest_coefficients: Optional[Mapping[str, float]] = None,
        tail_probability: Optional[float] = None,
    ) -> None:
        if (league, scope, family) != ("NBA", "half_1", "spreads"):
            pass

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.dispersion_index = self._finite(
            dispersion_index, "dispersion_index"
        )
        if self.dispersion_index < 1.0:
            raise ValueError("dispersion_index must be at least one.")

        self.shared_fraction = self._fraction(
            shared_fraction, "shared_fraction"
        )

        coefficients = dict(rest_coefficients or {})
        allowed = {"home_own", "home_opponent", "away_own", "away_opponent"}
        if set(coefficients) - allowed:
            raise ValueError("Unknown rest coefficient.")
        self.rest_coefficients = {
            name: self._finite(coefficients.get(name, 0.0), name)
            for name in allowed
        }

        tolerance = (
            np.sqrt(np.finfo(np.float64).eps)
            if tail_probability is None
            else tail_probability
        )
        self.tail_probability = self._finite(tolerance, "tail_probability")
        if not 0.0 < self.tail_probability < 1.0:
            raise ValueError("tail_probability must lie strictly between 0 and 1.")

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not np.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _fraction(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{name} must lie between zero and one.")
        return result

    def _means(self, context: Dict[str, Any]) -> Tuple[float, float]:
        home = self._positive(
            context.get("home_expected", self.HOME_BASELINE), "home_expected"
        )
        away = self._positive(
            context.get("away_expected", self.AWAY_BASELINE), "away_expected"
        )

        pace_ratio = self._positive(
            context.get("pace_ratio", 1.0), "pace_ratio"
        )
        if "pace" in context:
            if "reference_pace" not in context:
                raise ValueError("pace requires reference_pace.")
            if "pace_ratio" in context:
                raise ValueError("Supply pace_ratio or pace/reference_pace, not both.")
            pace_ratio = (
                self._positive(context["pace"], "pace")
                / self._positive(context["reference_pace"], "reference_pace")
            )

        home_team = context.get("home", {})
        away_team = context.get("away", {})
        if not isinstance(home_team, Mapping) or not isinstance(away_team, Mapping):
            raise ValueError("home and away must be feature mappings.")

        # Raw attack/defense ratings have unspecified units. Only explicitly
        # normalized exposures enter the mean equation.
        home_attack = self._positive(
            home_team.get("attack_exposure", 1.0), "home.attack_exposure"
        )
        home_defense = self._positive(
            home_team.get("defense_exposure", 1.0), "home.defense_exposure"
        )
        away_attack = self._positive(
            away_team.get("attack_exposure", 1.0), "away.attack_exposure"
        )
        away_defense = self._positive(
            away_team.get("defense_exposure", 1.0), "away.defense_exposure"
        )

        beta = self.rest_coefficients
        rest_home = rest_away = 0.0
        if any(value != 0.0 for value in beta.values()):
            required = (
                "home_rest_days",
                "away_rest_days",
                "home_reference_rest_days",
                "away_reference_rest_days",
            )
            if any(name not in context for name in required):
                raise ValueError("Nonzero rest coefficients require rest and reference days.")
            values = {name: self._finite(context[name], name) for name in required}
            if any(value < 0.0 for value in values.values()):
                raise ValueError("Rest days must be nonnegative.")
            rest_home = (
                np.log1p(values["home_rest_days"])
                - np.log1p(values["home_reference_rest_days"])
            )
            rest_away = (
                np.log1p(values["away_rest_days"])
                - np.log1p(values["away_reference_rest_days"])
            )

        log_home = (
            np.log(home)
            + np.log(pace_ratio)
            + np.log(home_attack)
            + np.log(away_defense)
            + beta["home_own"] * rest_home
            + beta["home_opponent"] * rest_away
        )
        log_away = (
            np.log(away)
            + np.log(pace_ratio)
            + np.log(away_attack)
            + np.log(home_defense)
            + beta["away_own"] * rest_away
            + beta["away_opponent"] * rest_home
        )
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            mu_home = np.exp(log_home)
            mu_away = np.exp(log_away)
        return (
            self._positive(mu_home, "adjusted home expectation"),
            self._positive(mu_away, "adjusted away expectation"),
        )

    def _component_pmf(self, mean: float, dispersion: float) -> np.ndarray:
        if mean == 0.0:
            return np.ones(1, dtype=np.float64)

        # Three independent components: the union bound controls omitted
        # joint probability by tail_probability.
        component_tail = self.tail_probability / 3.0
        if dispersion == 1.0:
            law = poisson(mu=mean)
        else:
            shape = mean / (dispersion - 1.0)
            success_probability = 1.0 / dispersion
            law = nbinom(n=shape, p=success_probability)

        upper = law.isf(component_tail)
        if not np.isfinite(upper) or upper < 0.0:
            raise ArithmeticError("Unable to construct finite score support.")

        endpoint = int(np.ceil(upper))
        while law.sf(endpoint) > component_tail:
            endpoint += 1

        support = np.arange(endpoint + 1, dtype=np.float64)
        probabilities = np.asarray(law.pmf(support), dtype=np.float64)
        mass = float(probabilities.sum())
        if (
            not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
            or mass <= 0.0
        ):
            raise ArithmeticError("Invalid component probability mass.")
        return probabilities / mass

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        mu_home, mu_away = self._means(game_context)
        dispersion = self._finite(
            game_context.get("dispersion_index", self.dispersion_index),
            "dispersion_index",
        )
        if dispersion < 1.0:
            raise ValueError("dispersion_index must be at least one.")

        if (
            "margin_variance" in game_context
            and "shared_fraction" in game_context
        ):
            raise ValueError("Supply margin_variance or shared_fraction, not both.")

        if "margin_variance" in game_context:
            margin_variance = self._finite(
                game_context["margin_variance"], "margin_variance"
            )
            lower = dispersion * abs(mu_home - mu_away)
            upper = dispersion * (mu_home + mu_away)
            if not lower <= margin_variance <= upper:
                raise ValueError(
                    "margin_variance is incompatible with this nonnegative-"
                    "covariance model; fit a different joint family."
                )
            shared_mean = (
                mu_home + mu_away - margin_variance / dispersion
            ) / 2.0
            # Correct boundary cancellation through exact boundary formulas.
            if margin_variance == lower:
                shared_mean = min(mu_home, mu_away)
            elif margin_variance == upper:
                shared_mean = 0.0
        else:
            fraction = self._fraction(
                game_context.get("shared_fraction", self.shared_fraction),
                "shared_fraction",
            )
            shared_mean = fraction * min(mu_home, mu_away)

        home_pmf = self._component_pmf(mu_home - shared_mean, dispersion)
        away_pmf = self._component_pmf(mu_away - shared_mean, dispersion)
        common_pmf = self._component_pmf(shared_mean, dispersion)

        scores = np.outer(home_pmf, away_pmf)
        if common_pmf.size > 1:
            scores = fftconvolve(scores, np.diag(common_pmf), mode="full")

            # FFT roundoff repair only; this does not constrain model tails
            # or alter substantive negative probabilities.
            roundoff = (
                np.finfo(np.float64).eps
                * scores.size
                * float(np.max(np.abs(scores)))
            )
            if float(np.min(scores)) < -roundoff:
                raise ArithmeticError("Convolution produced material negative mass.")
            scores[scores < 0.0] = 0.0

        if not np.all(np.isfinite(scores)):
            raise ArithmeticError("Nonfinite joint score probability.")
        total_mass = float(scores.sum(dtype=np.float64))
        if total_mass <= 0.0:
            raise ArithmeticError("Joint score distribution has no mass.")

        scores /= total_mass
        # Resolve the final floating-point summation residual at the mode.
        modal_index = np.unravel_index(int(np.argmax(scores)), scores.shape)
        scores[modal_index] += 1.0 - float(scores.sum(dtype=np.float64))

        if np.any(scores < 0.0):
            raise ArithmeticError("Normalization produced negative probability.")

        # Rows are home first-half points; columns are away first-half points.
        # The engine must apply each contract's signed handicap to H - A and
        # retain equality as a refunded push.
        return DiscreteDistribution.from_scores(scores)