from typing import Any, Dict, Mapping, Optional, Tuple
import math

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class CandidateModel(BaseCandidateModel):
    """
    Independent UEL regulation-time totals model.

    Cold-start prior:
        E[H] = 1.60, E[A] = 1.20, Var(T) / E[T] = 1.

    Conditional on total goals T, H is binomial with probability
    E[H] / E[T], and A = T - H. The Poisson total therefore reproduces
    independent Poisson team scores exactly.

    When an independently estimated dispersion_index D > 1 is supplied,
    a shared gamma intensity gives:
        E[T] = mu
        Var(T) = D * mu
        gamma shape = mu / (D - 1).

    No dispersion, rest, weather, or team-strength effects are inferred
    from nonexistent historical wagers. Optional feature coefficients
    must be fitted outside this class using information available before
    kickoff. Odds never enter the predictive distribution.

    Context conventions:
      * home_expected / away_expected are final baseline goal means.
        If supplied, nested attack/defense ratings are not reapplied.
      * Otherwise, nested home/away attack and defense are positive,
        dimensionless multipliers centered at one. A larger defense
        value means more goals conceded.
      * home_exposure / away_exposure and tempo / pace are nonnegative
        relative multipliers, centered at one. Supply tempo OR pace.
      * log_rate_adjustments optionally contains home/away mappings:
            feature: {"value": x, "reference": x0, "coefficient": beta}
        These add beta * (x - x0) to the corresponding log rate.
        Features must refer to numeric, top-level context fields.
        This supports externally estimated rest and weather effects.
      * dispersion_index describes the current game's total-goal
        variance divided by its mean, not raw between-game variance.

    Score support is selected by a numerical tail-error budget rather
    than a fixed goal ceiling. Predictions are deterministic.

    This class supplies a principled cold-start forecast; calibration
    and out-of-sample profitability require subsequent validation.
    """

    HOME_PRIOR = 1.60
    AWAY_PRIOR = 1.20
    MINIMUM_ELIGIBLE_ODDS = 1.40

    def __init__(
        self,
        model_id="codex_synth_uel_full_game_1108_145051",
        version="1.0.0",
        league="UEL",
        scope="full_game",
        family="totals",
        *,
        tail_tolerance: Optional[float] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        if (league, scope, family) != ("UEL", "full_game", "totals"):
            raise ValueError("This model supports UEL|full_game|totals only.")

        tolerance = (
            float(np.finfo(float).eps)
            if tail_tolerance is None
            else self._number(tail_tolerance, "tail_tolerance")
        )
        if not 0.0 < tolerance < 1.0:
            raise ValueError("tail_tolerance must be strictly between zero and one.")
        self.tail_tolerance = tolerance

    @staticmethod
    def _number(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
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
    def _rating(cls, team: Any, feature: str, name: str) -> float:
        if team is None or isinstance(team, str):
            return 1.0
        if not isinstance(team, Mapping):
            raise ValueError(f"{name} must be a team name or a feature mapping.")
        value = cls._number(team.get(feature, 1.0), f"{name}.{feature}")
        if value <= 0.0:
            raise ValueError(f"{name}.{feature} must be strictly positive.")
        return value

    def _means(self, context: Mapping[str, Any]) -> Tuple[float, float]:
        if not isinstance(context, Mapping):
            raise TypeError("game_context must be a mapping.")

        if "tempo" in context and "pace" in context:
            raise ValueError("Supply tempo or pace, not both.")
        tempo = self._nonnegative(
            context.get("tempo", context.get("pace", 1.0)),
            "tempo/pace",
        )

        adjustments = context.get("log_rate_adjustments", {})
        if not isinstance(adjustments, Mapping):
            raise ValueError("log_rate_adjustments must be a mapping.")
        if set(adjustments) - {"home", "away"}:
            raise ValueError("log_rate_adjustments supports home and away only.")

        means = []
        for side, opponent, prior in (
            ("home", "away", self.HOME_PRIOR),
            ("away", "home", self.AWAY_PRIOR),
        ):
            expected_key = f"{side}_expected"
            if expected_key in context:
                baseline = self._nonnegative(context[expected_key], expected_key)
            else:
                baseline = (
                    prior
                    * self._rating(context.get(side), "attack", side)
                    * self._rating(context.get(opponent), "defense", opponent)
                )

            exposure = self._nonnegative(
                context.get(f"{side}_exposure", 1.0),
                f"{side}_exposure",
            )
            terms = adjustments.get(side, {})
            if not isinstance(terms, Mapping):
                raise ValueError(f"log_rate_adjustments.{side} must be a mapping.")

            log_effects = []
            for feature, specification in terms.items():
                if not isinstance(specification, Mapping):
                    raise ValueError(f"Adjustment for {feature} must be a mapping.")
                if feature not in context:
                    raise ValueError(f"Missing adjusted context feature: {feature}.")
                if "reference" not in specification or "coefficient" not in specification:
                    raise ValueError(
                        f"{feature} requires an explicit reference and coefficient."
                    )
                value = self._number(context[feature], str(feature))
                reference = self._number(
                    specification["reference"], f"{feature}.reference"
                )
                coefficient = self._number(
                    specification["coefficient"], f"{feature}.coefficient"
                )
                effect = coefficient * (value - reference)
                if not math.isfinite(effect):
                    raise ValueError(f"Nonfinite log-rate effect for {feature}.")
                log_effects.append(effect)

            if not math.isfinite(baseline):
                raise ValueError(f"{side} baseline overflowed.")

            if baseline == 0.0 or exposure == 0.0 or tempo == 0.0:
                mean = 0.0
            else:
                try:
                    log_mean = math.fsum(
                        [
                            math.log(baseline),
                            math.log(exposure),
                            math.log(tempo),
                            *log_effects,
                        ]
                    )
                    mean = math.exp(log_mean)
                except (OverflowError, ValueError) as exc:
                    raise ValueError(f"{side} expected goals overflowed.") from exc
                if mean == 0.0 or not math.isfinite(mean):
                    raise ValueError(f"{side} expected goals are not representable.")
            means.append(mean)

        if not math.isfinite(means[0] + means[1]):
            raise ValueError("Total expected goals overflowed.")
        return means[0], means[1]

    def _law(self, context: Mapping[str, Any]):
        home, away = self._means(context)
        mean = home + away
        dispersion = self._number(
            context.get("dispersion_index", 1.0), "dispersion_index"
        )
        if dispersion < 1.0:
            raise ValueError(
                "dispersion_index below one requires an underdispersed model."
            )
        if mean == 0.0 or dispersion == 1.0:
            return home, away, stats.poisson(mu=mean)

        shape = mean / (dispersion - 1.0)
        success_probability = 1.0 / dispersion
        if not math.isfinite(shape) or shape <= 0.0:
            raise ValueError("Negative-binomial shape is not representable.")
        if not 0.0 < success_probability < 1.0:
            raise ValueError("Negative-binomial probability is not representable.")
        return home, away, stats.nbinom(n=shape, p=success_probability)

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        home, away, law = self._law(game_context)
        mean = home + away
        if mean == 0.0:
            return DiscreteDistribution.from_scores({(0, 0): 1.0})

        endpoint = float(law.isf(self.tail_tolerance))
        if not math.isfinite(endpoint):
            raise ArithmeticError("Could not resolve a finite score-support endpoint.")
        maximum_total = max(0, int(math.ceil(endpoint)))

        while True:
            omitted_mass = float(law.sf(maximum_total))
            if not math.isfinite(omitted_mass):
                raise ArithmeticError("Nonfinite total-goal tail probability.")
            if omitted_mass <= self.tail_tolerance:
                break
            maximum_total = maximum_total + max(1, maximum_total)

        totals = np.arange(maximum_total + 1, dtype=np.int64)
        total_mass = np.asarray(law.pmf(totals), dtype=float)
        if np.any(~np.isfinite(total_mass)) or np.any(total_mass < 0.0):
            raise ArithmeticError("Invalid total-goal probability mass.")

        home_share = home / mean
        scores: Dict[Tuple[int, int], float] = {}
        for total, probability in enumerate(total_mass):
            if probability == 0.0:
                continue
            home_goals = np.arange(total + 1, dtype=np.int64)
            allocation = np.asarray(
                stats.binom.pmf(home_goals, total, home_share), dtype=float
            )
            allocation_sum = math.fsum(allocation.tolist())
            if (
                np.any(~np.isfinite(allocation))
                or np.any(allocation < 0.0)
                or allocation_sum <= 0.0
            ):
                raise ArithmeticError("Invalid conditional score allocation.")
            allocation /= allocation_sum
            for home_score, conditional_probability in enumerate(allocation):
                mass = float(probability * conditional_probability)
                if mass > 0.0:
                    scores[(home_score, total - home_score)] = mass

        retained_mass = math.fsum(scores.values())
        if not math.isfinite(retained_mass) or retained_mass <= 0.0:
            raise ArithmeticError("Score distribution has no finite positive mass.")

        # Conditioning on retained support changes probabilities by at most
        # the omitted tail mass, apart from floating-point rounding.
        scores = {score: mass / retained_mass for score, mass in scores.items()}
        return DiscreteDistribution.from_scores(scores)

    def value_contract(
        self,
        game_context: Dict[str, Any],
        *,
        line: float,
        side: str,
        decimal_odds: float,
        settlement: str = "binary",
        outcome: str = "yes",
        fee_per_unit_stake: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Value an arbitrary real-valued totals threshold without line rounding.

        binary:
            The proposition is T > line for over and T < line for under.
            Equality resolves NO. outcome='no' buys its complement.
            Use the actual market's resolution rules when choosing this mode.

        push:
            Equality refunds stake. Only outcome='yes' is supported.
            Asian quarter-line split settlements require separate valuation
            of their component contracts.

        Decimal odds include returned stake. A Polymarket share bought at
        price q has decimal odds 1/q before fees. fee_per_unit_stake is
        an externally computed expected fee or other execution cost.

        Uses the untruncated analytical law, independent of score support.
        Eligibility is exactly odds >= 1.40 and net expectation > 0.
        """
        threshold = self._number(line, "line")
        odds = self._number(decimal_odds, "decimal_odds")
        fee = self._nonnegative(fee_per_unit_stake, "fee_per_unit_stake")
        if odds < 1.0:
            raise ValueError("decimal_odds must be at least one.")
        if side not in {"over", "under"}:
            raise ValueError("side must be 'over' or 'under'.")
        if settlement not in {"binary", "push"}:
            raise ValueError("settlement must be 'binary' or 'push'.")
        if outcome not in {"yes", "no"}:
            raise ValueError("outcome must be 'yes' or 'no'.")
        if settlement == "push" and outcome != "yes":
            raise ValueError("NO contracts require binary settlement.")

        _, _, law = self._law(game_context)

        below = float(law.cdf(math.ceil(threshold) - 1))
        above = float(law.sf(math.floor(threshold)))
        equal = (
            float(law.pmf(int(threshold)))
            if threshold >= 0.0 and threshold.is_integer()
            else 0.0
        )
        probabilities = (below, equal, above)
        if any(not math.isfinite(p) or p < 0.0 for p in probabilities):
            raise ArithmeticError("Invalid analytical settlement probabilities.")

        normalization = math.fsum(probabilities)
        if normalization <= 0.0:
            raise ArithmeticError("Settlement probabilities have zero mass.")
        below, equal, above = (p / normalization for p in probabilities)

        win = above if side == "over" else below
        opposite = below if side == "over" else above
        if settlement == "binary":
            loss = math.fsum((opposite, equal))
            push = 0.0
            if outcome == "no":
                win, loss = loss, win
        else:
            loss, push = opposite, equal

        expected_profit = math.fsum((win * (odds - 1.0), -loss, -fee))
        if not math.isfinite(expected_profit):
            raise ArithmeticError("Expected profit overflowed.")

        return {
            "line": threshold,
            "side": side,
            "outcome": outcome,
            "settlement": settlement,
            "win_probability": win,
            "loss_probability": loss,
            "push_probability": push,
            "decimal_odds": odds,
            "fee_per_unit_stake": fee,
            "expected_profit_per_unit_stake": expected_profit,
            "eligible": (
                odds >= self.MINIMUM_ELIGIBLE_ODDS and expected_profit > 0.0
            ),
        }