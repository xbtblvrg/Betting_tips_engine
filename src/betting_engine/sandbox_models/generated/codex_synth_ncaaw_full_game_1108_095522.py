from typing import Any, Dict, Mapping, Optional, Sequence
import math

import numpy as np
from scipy.stats import nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# The execution environment supplies DiscreteDistribution.


class NCAAWDoubleChanceModel(BaseCandidateModel):
    """
    Integer-score model for NCAAW regulation double-chance markets.

    Settlement contract
    -------------------
    `full_game` means regulation for this candidate. Overtime-inclusive markets
    are rejected when identified in the context. The caller must verify the
    actual bookmaker settlement rules before using the forecast.

    Identification
    --------------
    The supplied cold-start anchors identify means, not possession structure,
    scoring-event frequencies, covariance, rest effects, or calibration skill.
    With no additional evidence, the model uses the stipulated unit-dispersion
    Poisson prior. It does not invent a draw-rate target or claim verified ROI.

    Optional context inputs
    -----------------------
    home_expected, away_expected:
        Regulation scoring means; defaults are the supplied 80 and 75 anchors.
    pace_multiplier:
        Positive multiplier applied to both means. Alternatively provide both
        pace (or tempo) and reference_pace in matching units.
    home, away:
        Optional dictionaries containing attack_multiplier and
        defense_multiplier. Defense means opponent scoring allowed: larger
        values increase the opposing team's expected score.
    rest_log_rate_coefficient:
        Externally fitted coefficient. Requires both teams' rest days.
        Multiplies home/away means by exp(+/- coefficient * rest_difference / 2).
    margin_prior_exposure:
        Nonnegative, externally fitted prior strength. Positive values require
        home/away exposure, supplied directly or within the team dictionaries.
        Shrinks the adjusted log scoring ratio toward the 80:75 anchor ratio.
    scoring_event_probabilities:
        Empirical probabilities for scoring jumps of 1, 2, and 3 points.
        Defaults to the unit-jump prior; simultaneous multi-shot sequences
        should be represented by their scored-point events.
    dispersion_index:
        Pooled marginal score variance / mean. Defaults to the jump process's
        intrinsic dispersion, which is 1 under the cold-start prior.
    intensity_dependence:
        "independent" (default) or "shared". Excess dispersion is represented
        by independent team gamma intensities or one shared gamma pace factor.

    For event moments m1=E[J], m2=E[J²], lambda_i=mu_i/m1:
        E[S_i] = mu_i
        Var[S_i | fixed intensity] = mu_i * m2/m1.

    Gamma mixing adds variance without changing either scoring mean. The
    independent specification matches each team's requested dispersion.
    The shared specification matches pooled marginal dispersion and explicitly
    induces covariance; its shared activity largely cancels in the margin.

    The Fourier calculation retains integer tie mass and scoring parity.
    Truncation is bounded using the exact distribution of total event count.
    Numerical tolerances are computational controls, not probability clamps.

    Output is the mutually exclusive home/draw/away partition. The engine
    should price 1X as home+draw, X2 as away+draw, and 12 as home+away. Odds and
    bookmaker probabilities never enter this forecast.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_full_game_1108_095522",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "full_game",
        family: str = "double_chance",
        *,
        tail_tolerance: Optional[float] = None,
        max_fft_points: int = 1 << 20,
    ):
        if (league, scope, family) != ("NCAAW", "full_game", "double_chance"):
            raise ValueError("This candidate supports only NCAAW regulation double chance.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )

        self.tail_tolerance = (
            math.sqrt(np.finfo(float).eps)
            if tail_tolerance is None
            else self._number(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must be strictly between zero and one.")
        if isinstance(max_fft_points, bool) or not isinstance(max_fft_points, int):
            raise TypeError("max_fft_points must be an integer.")
        if max_fft_points < 2:
            raise ValueError("max_fft_points must be at least two.")
        self.max_fft_points = max_fft_points

    @staticmethod
    def _number(value: Any, name: str, *, positive: bool = False) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise TypeError(f"{name} must be numeric.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0.0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @staticmethod
    def _team(context: Mapping[str, Any], name: str) -> Mapping[str, Any]:
        value = context.get(name, {})
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping.")
        return value

    @staticmethod
    def _validate_settlement(context: Mapping[str, Any]) -> None:
        if context.get("includes_overtime", False) is not False:
            raise ValueError("Overtime-inclusive settlement is unsupported.")
        period = context.get("settlement_period", "regulation")
        if period not in ("regulation", "regulation_only", "40_minutes"):
            raise ValueError("settlement_period must explicitly denote regulation.")

        for key, expected in (
            ("league", "NCAAW"),
            ("scope", "full_game"),
            ("family", "double_chance"),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"Incompatible {key}: {context[key]!r}.")

    def _means(self, context: Mapping[str, Any]) -> tuple:
        home = self._team(context, "home")
        away = self._team(context, "away")

        h = self._number(context.get("home_expected", 80.0), "home_expected", positive=True)
        a = self._number(context.get("away_expected", 75.0), "away_expected", positive=True)

        pace_keys = ("pace", "tempo", "reference_pace")
        if "pace_multiplier" in context:
            if any(key in context for key in pace_keys):
                raise ValueError("Specify pace_multiplier or a pace/reference pair.")
            pace_scale = self._number(
                context["pace_multiplier"], "pace_multiplier", positive=True
            )
        elif any(key in context for key in pace_keys):
            if "pace" in context and "tempo" in context:
                raise ValueError("Specify only one of pace and tempo.")
            pace = context.get("pace", context.get("tempo"))
            if pace is None or "reference_pace" not in context:
                raise ValueError("Pace adjustment requires pace and reference_pace.")
            pace_scale = (
                self._number(pace, "pace", positive=True)
                / self._number(
                    context["reference_pace"], "reference_pace", positive=True
                )
            )
        else:
            pace_scale = 1.0

        h *= pace_scale
        a *= pace_scale
        h *= self._number(
            home.get("attack_multiplier", 1.0), "home.attack_multiplier", positive=True
        )
        h *= self._number(
            away.get("defense_multiplier", 1.0), "away.defense_multiplier", positive=True
        )
        a *= self._number(
            away.get("attack_multiplier", 1.0), "away.attack_multiplier", positive=True
        )
        a *= self._number(
            home.get("defense_multiplier", 1.0), "home.defense_multiplier", positive=True
        )

        coefficient = self._number(
            context.get("rest_log_rate_coefficient", 0.0),
            "rest_log_rate_coefficient",
        )
        if coefficient != 0.0:
            if "home_rest_days" not in context or "away_rest_days" not in context:
                raise ValueError("A nonzero rest coefficient requires both rest inputs.")
            difference = (
                self._nonnegative(context["home_rest_days"], "home_rest_days")
                - self._nonnegative(context["away_rest_days"], "away_rest_days")
            )
            try:
                rest_scale = math.exp(coefficient * difference / 2.0)
                h *= rest_scale
                a /= rest_scale
            except (OverflowError, ZeroDivisionError) as exc:
                raise ValueError("Rest adjustment exceeds numerical range.") from exc

        h = self._number(h, "adjusted home mean", positive=True)
        a = self._number(a, "adjusted away mean", positive=True)
        total = self._number(h + a, "adjusted total", positive=True)

        prior = self._nonnegative(
            context.get("margin_prior_exposure", 0.0), "margin_prior_exposure"
        )
        if prior > 0.0:
            home_exposure = context.get("home_exposure", home.get("exposure"))
            away_exposure = context.get("away_exposure", away.get("exposure"))
            if home_exposure is None or away_exposure is None:
                raise ValueError("Margin shrinkage requires both exposure inputs.")
            eh = self._nonnegative(home_exposure, "home_exposure")
            ea = self._nonnegative(away_exposure, "away_exposure")

            # Harmonic effective exposure: uncertainty is dominated by the
            # less-observed team, and no observations give the prior ratio.
            effective = (
                0.0
                if eh == 0.0 or ea == 0.0
                else min(eh, ea) * (2.0 / (1.0 + min(eh, ea) / max(eh, ea)))
            )
            weight = effective / (effective + prior)
            log_ratio = (
                weight * (math.log(h) - math.log(a))
                + (1.0 - weight) * math.log(80.0 / 75.0)
            )
            # Stable conversion preserves the adjusted total.
            ratio = math.exp(-abs(log_ratio))
            smaller = total * ratio / (1.0 + ratio)
            larger = total / (1.0 + ratio)
            h, a = (larger, smaller) if log_ratio >= 0.0 else (smaller, larger)

        return (
            self._number(h, "final home mean", positive=True),
            self._number(a, "final away mean", positive=True),
        )

    def _event_weights(self, context: Mapping[str, Any]) -> np.ndarray:
        supplied: Sequence[float] = context.get(
            "scoring_event_probabilities", (1.0, 0.0, 0.0)
        )
        weights = np.asarray(supplied, dtype=float)
        if weights.shape != (3,):
            raise ValueError("Supply three event probabilities for jumps 1, 2, 3.")
        if not np.all(np.isfinite(weights)) or np.any(weights < 0.0):
            raise ValueError("Event probabilities must be finite and nonnegative.")
        total = float(weights.sum())
        if not math.isfinite(total) or total <= 0.0:
            raise ValueError("Event probabilities must have positive finite mass.")
        if not math.isclose(total, 1.0, rel_tol=self.tail_tolerance, abs_tol=0.0):
            raise ValueError("Event probabilities must sum to one.")
        return weights / total

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> "DiscreteDistribution":
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        self._validate_settlement(game_context)
        home_mean, away_mean = self._means(game_context)
        weights = self._event_weights(game_context)

        jumps = np.arange(1, 4, dtype=float)
        first = float(weights @ jumps)
        second = float(weights @ (jumps * jumps))
        intrinsic_dispersion = second / first

        dispersion = self._number(
            game_context.get("dispersion_index", intrinsic_dispersion),
            "dispersion_index",
            positive=True,
        )
        if dispersion < intrinsic_dispersion:
            raise ValueError(
                "Requested dispersion is below the event process minimum. "
                "Use compatible empirical event weights or a separately fitted "
                "possession model; reducing variance here would be incoherent."
            )

        dependence = game_context.get("intensity_dependence", "independent")
        if dependence not in ("independent", "shared"):
            raise ValueError("intensity_dependence must be independent or shared.")

        lambda_home = home_mean / first
        lambda_away = away_mean / first
        lambda_total = self._number(
            lambda_home + lambda_away, "total event intensity", positive=True
        )
        excess = dispersion - intrinsic_dispersion

        if excess == 0.0:
            count_distribution = poisson(mu=lambda_total)
            shape = None
        else:
            if dependence == "independent":
                shape = (home_mean + away_mean) / excess
            else:
                # Stable pooled calibration:
                # F = m2/m1 + (mu_h² + mu_a²)/(k * (mu_h + mu_a)).
                total_mean = home_mean + away_mean
                shape = (
                    home_mean * (home_mean / total_mean)
                    + away_mean * (away_mean / total_mean)
                ) / excess
            shape = self._number(shape, "gamma shape", positive=True)
            probability = 1.0 / (1.0 + lambda_total / shape)
            if not 0.0 < probability < 1.0:
                raise ArithmeticError("Dispersion is outside representable precision.")
            count_distribution = nbinom(n=shape, p=probability)

        event_limit = float(count_distribution.isf(self.tail_tolerance))
        if not math.isfinite(event_limit) or event_limit < 0.0:
            raise ArithmeticError("Could not determine a finite event-count bound.")
        event_limit = int(math.ceil(event_limit))
        while float(count_distribution.sf(event_limit)) > self.tail_tolerance:
            event_limit += 1

        largest_jump = int(np.flatnonzero(weights > 0.0)[-1]) + 1
        # On K <= event_limit, |H-A| <= largest_jump * event_limit.
        # A grid strictly wider than twice that support prevents aliasing there.
        required = 2 * largest_jump * event_limit + 1
        grid_size = 1 << (max(required, 2) - 1).bit_length()
        if grid_size > self.max_fft_points:
            raise ArithmeticError(
                f"Certified lattice needs {grid_size} points; "
                f"resource limit is {self.max_fft_points}."
            )

        angles = (2.0 * np.pi / grid_size) * np.arange(grid_size)
        event_delta = np.zeros(grid_size, dtype=np.complex128)
        for jump, weight in zip(jumps, weights):
            if weight > 0.0:
                event_delta += weight * np.expm1(1j * jump * angles)
        away_delta = event_delta.conjugate()

        if excess == 0.0:
            log_characteristic = (
                lambda_home * event_delta + lambda_away * away_delta
            )
        elif dependence == "shared":
            log_characteristic = -shape * np.log1p(
                -(lambda_home * event_delta + lambda_away * away_delta) / shape
            )
        else:
            # Team shapes add; their common negative-binomial success
            # probability gives the exact total-event count used above.
            home_shape = shape * (lambda_home / lambda_total)
            away_shape = shape * (lambda_away / lambda_total)
            log_characteristic = (
                -home_shape * np.log1p(-lambda_home * event_delta / home_shape)
                -away_shape * np.log1p(-lambda_away * away_delta / away_shape)
            )

        characteristic = np.exp(log_characteristic)
        characteristic[0] = 1.0
        inversion = np.fft.fft(characteristic) / grid_size
        masses = inversion.real

        # FFT roundoff correction only; no statistical probability floor.
        roundoff_budget = (
            np.finfo(float).eps * grid_size * max(1, grid_size.bit_length())
        )
        if not np.all(np.isfinite(inversion)):
            raise ArithmeticError("Nonfinite Fourier inversion.")
        if float(np.max(np.abs(inversion.imag))) > roundoff_budget:
            raise ArithmeticError("Fourier inversion has excessive imaginary residual.")
        negative_mass = -float(masses[masses < 0.0].sum())
        if negative_mass > roundoff_budget:
            raise ArithmeticError("Fourier inversion has excessive negative mass.")
        masses = np.where(masses < 0.0, 0.0, masses)
        mass_sum = float(masses.sum())
        if abs(mass_sum - 1.0) > roundoff_budget:
            raise ArithmeticError("Fourier inversion failed mass conservation.")
        masses /= mass_sum

        midpoint = grid_size // 2
        p_draw = float(masses[0])
        p_home = float(masses[1:midpoint].sum())
        p_away = float(masses[midpoint + 1:].sum())

        # The Nyquist bin is outside the certified central support. Its sign
        # is unknowable after wrapping; symmetric allocation adds at most the
        # certified tail error and preserves home/away exchange symmetry.
        unresolved = float(masses[midpoint])
        p_home += unresolved / 2.0
        p_away += unresolved / 2.0

        normalization = p_home + p_draw + p_away
        return DiscreteDistribution(
            probabilities={
                "home": p_home / normalization,
                "draw": p_draw / normalization,
                "away": p_away / normalization,
            }
        )


CandidateModel = NCAAWDoubleChanceModel