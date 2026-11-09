from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import nbinom, poisson, skellam

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAQuarter4DoubleChanceModel(BaseCandidateModel):
    """
    Deterministic, bookmaker-independent run model.

    DiscreteDistribution is supplied by the execution environment.

    Cold start
    ----------
    Segment means are 0.40 home and 0.30 away. With no dispersion
    observations, run increments are Poisson. There is no historical
    evidence here establishing an edge or a calibration improvement.

    Scope semantics
    ---------------
    Baseball has no quarters. By default, the supplied means describe a
    fixed-exposure segment; no inning mapping or stopping rule is invented.

    To model sequential innings, provide:
        scope_innings: consecutive inning numbers
        scheduled_final_inning: regulation's final inning
        initial_home_score, initial_away_score: scores BEFORE the segment
        home_inning_weights, away_inning_weights: optional exposure weights

    Sequential means describe potential runs over the specified exposure
    BEFORE any skipped or shortened half-inning. Do not supply means already
    adjusted for those stopping rules.

    If the segment reaches the scheduled final inning, additionally provide:
        walkoff_overshoot_probabilities:
            Conditional probabilities for extra credited runs beyond the
            winning run. Index zero means no overshoot. For example, [1.0]
            explicitly requests a first-go-ahead-run approximation. This is
            not assumed automatically: a walk-off home run can credit more
            than the single winning run.

    Optional explicit mercy rules:
        mercy_rules: [
            {"inning": integer, "after": "top" or "bottom",
             "lead": positive integer}
        ]
    Each entry is a settlement/competition rule supplied by the caller.
    After a top half, only a qualifying home lead terminates the game.
    After a bottom half, a qualifying lead by either team terminates it.
    Inning-specific rules must be listed explicitly.

    Situational adjustment
    ----------------------
    For side s:
        mu_s = supplied_mean_or_anchor
               * exposure_s * pace
               * exp(sum(beta_sj * feature_sj))

    Provide:
        home_exposure, away_exposure, pace: nonnegative multipliers
        home_log_features, away_log_features: numeric feature dictionaries

    Coefficients are constructor inputs fitted outside this class using
    chronological training data. Raw attack, defense, rest, weather, and
    venue fields are not assigned fabricated effects. Their transformations,
    reference levels, and fitted coefficients belong in those feature maps.

    Overdispersion
    --------------
    For observed Fano index D > 1, a gamma-Poisson increment has
        shape = mu / (D - 1), p = 1 / D,
    giving E[R] = mu and Var[R] = D * mu. Increments are independent;
    shared game-level latent effects are not implicitly introduced.
    D = 1 gives Poisson exactly. Underdispersion is rejected.

    The proposed hurdle construction is not used: taking a positive
    geometric mean kappa and hurdle probability 1-exp(-lambda/kappa)
    gives mean kappa*(1-exp(-lambda/kappa)), not lambda, and does not
    recover Poisson at kappa=1.

    Sequential calculations omit at most tail_tolerance probability before
    floating-point error. Outputs condition on retained mass; each event's
    absolute truncation error is consequently at most tail_tolerance.
    Resource limits raise rather than silently alter predictions.
    """

    HOME_ANCHOR = 0.40
    AWAY_ANCHOR = 0.30

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_baseball_quarter_4_1108_110215",
        version: str = "1.0.0",
        league: str = "NCAA_BASEBALL",
        scope: str = "quarter_4",
        family: str = "double_chance",
        *,
        coefficients: Optional[Mapping[str, Mapping[str, float]]] = None,
        tail_tolerance: float = math.sqrt(np.finfo(float).eps),
        max_states: int = 1_000_000,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != (
            "NCAA_BASEBALL", "quarter_4", "double_chance"
        ):
            raise ValueError("This candidate is specific to its declared segment.")

        self.tail_tolerance = self._number(tail_tolerance, "tail_tolerance")
        if not 0.0 < self.tail_tolerance < 1.0:
            raise ValueError("tail_tolerance must lie strictly between zero and one.")
        self.max_states = self._integer(max_states, "max_states", minimum=1)

        supplied = {} if coefficients is None else dict(coefficients)
        if set(supplied) - {"home", "away"}:
            raise ValueError("Coefficient groups must be 'home' or 'away'.")
        self.coefficients = {
            side: {
                name: self._number(value, f"{side} coefficient {name}")
                for name, value in dict(supplied.get(side, {})).items()
            }
            for side in ("home", "away")
        }

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, str, bytes)):
            raise ValueError(f"{name} must be a finite number.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0.0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _integer(cls, value: Any, name: str, minimum: int = 0) -> int:
        number = cls._number(value, name)
        if not number.is_integer() or number < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}.")
        return int(number)

    def _mean(self, context: Mapping[str, Any], side: str) -> float:
        anchor = self.HOME_ANCHOR if side == "home" else self.AWAY_ANCHOR
        base = self._nonnegative(
            context.get(f"{side}_expected", anchor), f"{side}_expected"
        )
        exposure = self._nonnegative(
            context.get(f"{side}_exposure", 1.0), f"{side}_exposure"
        )
        pace = self._nonnegative(context.get("pace", 1.0), "pace")
        features = context.get(f"{side}_log_features", {})
        if not isinstance(features, Mapping):
            raise ValueError(f"{side}_log_features must be a mapping.")

        terms = []
        for name, coefficient in self.coefficients[side].items():
            if name not in features:
                raise ValueError(f"Missing fitted feature: {side}.{name}")
            terms.append(
                coefficient * self._number(features[name], f"{side}.{name}")
            )
        linear_predictor = math.fsum(terms)
        if not math.isfinite(linear_predictor):
            raise ValueError("The feature linear predictor is not finite.")
        if base == 0.0 or exposure == 0.0 or pace == 0.0:
            return 0.0

        log_mean = (
            math.log(base) + math.log(exposure) + math.log(pace)
            + linear_predictor
        )
        try:
            mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Adjusted expected runs overflow float64.") from exc
        if not math.isfinite(mean) or mean == 0.0:
            raise ValueError("Adjusted expected runs exceed float64's usable range.")
        return mean

    def _dispersion(self, context: Mapping[str, Any], side: str) -> float:
        value = self._number(
            context.get(
                f"{side}_dispersion_index",
                context.get("dispersion_index", 1.0),
            ),
            f"{side}_dispersion_index",
        )
        if value < 1.0:
            raise ValueError("This run-count family requires dispersion_index >= 1.")
        return value

    def _pmf(self, mean: float, dispersion: float, budget: float) -> np.ndarray:
        if mean == 0.0:
            return np.array([1.0], dtype=float)
        law = (
            poisson(mu=mean)
            if dispersion == 1.0
            else nbinom(n=mean / (dispersion - 1.0), p=1.0 / dispersion)
        )
        quantile = float(law.isf(budget))
        if not math.isfinite(quantile) or quantile < 0.0:
            raise ArithmeticError("Unable to construct a finite run-count support.")
        upper = int(math.ceil(quantile))
        while float(law.sf(upper)) > budget:
            upper += 1
            if upper + 1 > self.max_states:
                raise RuntimeError("Run-count support exceeds max_states.")
        if upper + 1 > self.max_states:
            raise RuntimeError("Run-count support exceeds max_states.")

        values = np.asarray(law.pmf(np.arange(upper + 1)), dtype=float)
        if not np.all(np.isfinite(values)) or np.any(values < 0.0):
            raise ArithmeticError("Run-count probability computation failed.")
        return values

    @staticmethod
    def _distribution(home: float, draw: float, away: float) -> DiscreteDistribution:
        probabilities = (float(home), float(draw), float(away))
        if any(not math.isfinite(p) or p < 0.0 for p in probabilities):
            raise ArithmeticError("Invalid outcome probabilities.")
        mass = math.fsum(probabilities)
        if mass <= 0.0:
            raise ArithmeticError("No probability mass remains.")
        return DiscreteDistribution(
            probabilities={
                "home": probabilities[0] / mass,
                "draw": probabilities[1] / mass,
                "away": probabilities[2] / mass,
            }
        )

    def _fixed_exposure(
        self, home: float, away: float, dh: float, da: float
    ) -> DiscreteDistribution:
        if dh == da == 1.0:
            if home == 0.0:
                tie = math.exp(-away)
                return self._distribution(0.0, tie, -math.expm1(-away))
            if away == 0.0:
                tie = math.exp(-home)
                return self._distribution(-math.expm1(-home), tie, 0.0)
            return self._distribution(
                skellam.sf(0, home, away),
                skellam.pmf(0, home, away),
                skellam.cdf(-1, home, away),
            )

        hp = self._pmf(home, dh, self.tail_tolerance / 2.0)
        ap = self._pmf(away, da, self.tail_tolerance / 2.0)
        common = min(len(hp), len(ap))
        draw = float(np.dot(hp[:common], ap[:common]))
        acdf, hcdf = np.cumsum(ap), np.cumsum(hp)
        ph = math.fsum(
            float(hp[i]) * float(acdf[min(i - 1, len(ap) - 1)])
            for i in range(1, len(hp))
        )
        pa = math.fsum(
            float(ap[i]) * float(hcdf[min(i - 1, len(hp) - 1)])
            for i in range(1, len(ap))
        )
        return self._distribution(ph, draw, pa)

    def _weights(
        self, context: Mapping[str, Any], side: str, count: int
    ) -> Sequence[float]:
        raw = context.get(f"{side}_inning_weights")
        if raw is None:
            return [1.0 / count] * count
        if len(raw) != count:
            raise ValueError(f"{side}_inning_weights must match scope_innings.")
        values = [
            self._nonnegative(v, f"{side}_inning_weights") for v in raw
        ]
        total = math.fsum(values)
        if not math.isfinite(total) or total <= 0.0:
            raise ValueError("Inning weights must have positive finite total.")
        return [v / total for v in values]

    def _sequential(
        self,
        context: Mapping[str, Any],
        home: float,
        away: float,
        dh: float,
        da: float,
    ) -> DiscreteDistribution:
        innings = [
            self._integer(v, "scope_innings entry", 1)
            for v in context["scope_innings"]
        ]
        if not innings or any(
            right != left + 1 for left, right in zip(innings, innings[1:])
        ):
            raise ValueError("scope_innings must be nonempty and consecutive.")
        final = self._integer(
            context["scheduled_final_inning"], "scheduled_final_inning", 1
        )
        if innings[-1] > final:
            raise ValueError("Extra-inning rules require a separate explicit model.")

        initial_home = self._integer(
            context["initial_home_score"], "initial_home_score"
        )
        initial_away = self._integer(
            context["initial_away_score"], "initial_away_score"
        )
        initial_lead = initial_home - initial_away
        weights_h = self._weights(context, "home", len(innings))
        weights_a = self._weights(context, "away", len(innings))

        overshoot = None
        if final in innings:
            if "walkoff_overshoot_probabilities" not in context:
                raise ValueError(
                    "Final-inning modeling requires an explicit "
                    "walkoff_overshoot_probabilities kernel."
                )
            overshoot = [
                self._nonnegative(v, "walkoff overshoot probability")
                for v in context["walkoff_overshoot_probabilities"]
            ]
            if not overshoot:
                raise ValueError("The walk-off kernel must be nonempty.")
            total = math.fsum(overshoot)
            if abs(total - 1.0) > np.finfo(float).eps * len(overshoot):
                raise ValueError("Walk-off kernel probabilities must sum to one.")
            overshoot = [v / total for v in overshoot]

        mercy: Dict[Tuple[int, str], int] = {}
        for rule in context.get("mercy_rules", []):
            inning = self._integer(rule["inning"], "mercy inning", 1)
            phase = rule["after"]
            if phase not in ("top", "bottom"):
                raise ValueError("Mercy rule phase must be 'top' or 'bottom'.")
            key = (inning, phase)
            if key in mercy:
                raise ValueError("Duplicate mercy-rule checkpoint.")
            mercy[key] = self._integer(rule["lead"], "mercy lead", 1)

        # h and a are segment runs, not whole-game scores.
        states = {(0, 0, False): 1.0}
        budget = self.tail_tolerance / (2 * len(innings))

        for index, inning in enumerate(innings):
            for phase, mean, dispersion in (
                ("top", away * weights_a[index], da),
                ("bottom", home * weights_h[index], dh),
            ):
                pmf = self._pmf(mean, dispersion, budget)
                next_states = defaultdict(float)

                for (h, a, ended), state_probability in states.items():
                    lead_before = initial_lead + h - a
                    if ended or (
                        phase == "bottom" and inning == final and lead_before > 0
                    ):
                        next_states[(h, a, True)] += state_probability
                        continue

                    for runs, run_probability in enumerate(pmf):
                        probability = state_probability * float(run_probability)
                        if probability == 0.0:
                            continue

                        if phase == "top":
                            outcomes = ((h, a + runs, False, 1.0),)
                        elif inning == final and lead_before + runs > 0:
                            needed = 1 - lead_before
                            # Only overshoots compatible with the latent run
                            # total are allowed; condition the supplied kernel.
                            compatible = overshoot[: runs - needed + 1]
                            kernel_mass = math.fsum(compatible)
                            if kernel_mass <= 0.0:
                                raise ValueError(
                                    "Walk-off kernel has no compatible outcome."
                                )
                            outcomes = (
                                (h + needed + extra, a, True, p / kernel_mass)
                                for extra, p in enumerate(compatible)
                                if p > 0.0
                            )
                        else:
                            outcomes = ((h + runs, a, False, 1.0),)

                        for nh, na, stopped, conditional_probability in outcomes:
                            lead = initial_lead + nh - na
                            threshold = mercy.get((inning, phase))
                            if threshold is not None:
                                stopped = stopped or (
                                    lead >= threshold
                                    if phase == "top"
                                    else abs(lead) >= threshold
                                )
                            if phase == "top" and inning == final and lead > 0:
                                stopped = True
                            next_states[(nh, na, stopped)] += (
                                probability * conditional_probability
                            )
                            if len(next_states) > self.max_states:
                                raise RuntimeError(
                                    "Sequential state space exceeds max_states."
                                )
                states = next_states

        ph = math.fsum(p for (h, a, _), p in states.items() if h > a)
        pd = math.fsum(p for (h, a, _), p in states.items() if h == a)
        pa = math.fsum(p for (h, a, _), p in states.items() if h < a)
        retained = math.fsum((ph, pd, pa))
        rounding = np.finfo(float).eps * (
            len(states) + 2 * len(innings)
        )
        if not math.isfinite(retained) or abs(1.0 - retained) > (
            self.tail_tolerance + rounding
        ):
            raise ArithmeticError("Sequential probability-mass check failed.")
        return self._distribution(ph, pd, pa)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        """
        Return mutually exclusive segment home/draw/away probabilities.

        Double-chance contracts must be formed downstream:
            1X = P(home) + P(draw)
            X2 = P(away) + P(draw)
            12 = P(home) + P(away)

        These overlapping contracts must not be normalized together.
        For decimal odds o and contract probability p, EV per unit stake
        is o*p - 1. The stated odds eligibility floor of 1.40 belongs to
        execution, not to the predictive distribution.
        """
        if not isinstance(game_context, Mapping):
            raise TypeError("game_context must be a mapping.")
        for key, expected in (
            ("league", "NCAA_BASEBALL"),
            ("scope", "quarter_4"),
            ("family", "double_chance"),
        ):
            if key in game_context and game_context[key] != expected:
                raise ValueError(f"Unexpected {key} for this candidate.")

        home = self._mean(game_context, "home")
        away = self._mean(game_context, "away")
        dh = self._dispersion(game_context, "home")
        da = self._dispersion(game_context, "away")

        if "scope_innings" in game_context:
            return self._sequential(game_context, home, away, dh, da)
        if game_context.get("mercy_rules") or (
            "walkoff_overshoot_probabilities" in game_context
        ):
            raise ValueError("Stopping rules require an explicit scope_innings map.")
        return self._fixed_exposure(home, away, dh, da)