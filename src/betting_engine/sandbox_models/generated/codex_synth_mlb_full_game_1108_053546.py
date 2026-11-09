from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy.integrate import quad
from scipy.stats import gamma, nbinom, poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

# Some sandbox runners inject this primitive directly into execution scope.
try:
    DiscreteDistribution
except NameError:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class MLBFullGameDoubleChanceModel(BaseCandidateModel):
    """
    Pregame outcome model conditional on a completed, action-standing MLB game.

    Cold-start anchors are 4.60 home runs and 4.20 away runs. No bookmaker
    probabilities enter the forecast. The available evidence does not identify
    overdispersion, shared environmental variance, rest effects, or an
    extra-inning home advantage; none is invented.

    Model:
        G ~ Gamma(shape=1/v, scale=v), or G=1 when v=0.
        E[R_s | G] = mu_s * G.
        Var(R_s | G) = mu_s*G + alpha_s*(mu_s*G)^2 / innings.

    Consequently:
        Cov(R_h, R_a) = mu_h*mu_a*v
        Var(R_s)/mu_s = 1 + mu_s*v
                           + alpha_s*mu_s*(1+v)/innings.

    Conditional regulation totals are Poisson or negative binomial. Shared
    frailty is integrated BEFORE resolving regulation ties, retaining the
    information a tie provides about the scoring environment.

    Extra innings use an absorbing repeated-inning chain when calibrated
    extra-inning inputs are supplied:
        p_home_extra = P(H_inning > A_inning) /
                       [P(H_inning > A_inning) + P(A_inning > H_inning)].

    Otherwise a symmetric 1/2 prior resolves ties. This is an explicit
    cold-start assumption, not an estimated extra-inning success rate.

    Walk-off truncation changes recorded scores but not the winner event in
    a latent completed-half-inning model with nonnegative run increments.
    The model therefore returns outcomes, never fabricated final scores.
    Expected-run inputs should represent full scheduled offensive exposure;
    historical recorded home totals are only a proxy until exposure-adjusted
    estimates are supplied.

    Context contract:
      home_expected, away_expected:
          Already adjusted latent regulation means. When supplied, additional
          feature adjustments are not applied to that side.
      home / away:
          Optional mappings with positive relative attack and defense factors.
          A larger defense factor means more runs conceded.
      park_factor, pace_factor, home_exposure, away_exposure:
          Positive multiplicative factors, defaulting to the identity.
      feature_coefficients:
          Optional previously fitted log-mean coefficients:
          rest_log1p, wind_speed, temp.
      feature_centers:
          Required reference values for supplied weather coefficients.
      home_rest_days, away_rest_days, weather:
          Used only with supplied fitted coefficients.
      shared_environment_variance:
          Variance of unit-mean G; defaults to zero.
      home_dispersion_index, away_dispersion_index:
          Marginal regulation Var/Mean, with the conditional-Poisson boundary
          as default. Incompatible moments raise instead of being clipped.
      home_inning_observations, away_inning_observations:
          Alternative dispersion inputs, each a sequence of mappings with
          runs and expected. Must be historical, exposure-adjusted, uncensored
          observations with out-of-sample expected values.
      extra_home_pmf, extra_away_pmf:
          Alternative calibrated full-half-inning scoring PMFs, indexed by runs.
      extra_home_expected, extra_away_expected:
          Alternative ordinary extra-half-inning means before a placed runner.
      extra_home_dispersion_index, extra_away_dispersion_index:
          Var/Mean for those ordinary extra-half-inning runs.
      automatic_runner:
          Explicit rule flag, or inferred from is_postseason when provided.
      extra_home_runner_probability, extra_away_runner_probability:
          Required for the parametric extra-inning model when a runner is
          placed. Model the runner's incremental run as Bernoulli; these
          probabilities must be externally calibrated.
      includes_extra_innings:
          Must be True for this candidate.
      is_live:
          Must be False; this is a pregame model.

    Rules source:
      [MLB designated-runner rule]
      (https://www.mlb.com/glossary/rules/designated-runner)

    Regular-season extras use a placed runner; postseason extras do not.
    Double-chance "12" covers both winners and has event probability one.
    Whether that contract is payable, unavailable, or void is a sportsbook
    settlement rule, not something inferable from an outcome distribution.

    Numerical tolerances and resource limits control approximation only.
    No probability clamps, odds blending, or purported ROI guarantees are used.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_mlb_full_game_1108_053546",
        version: str = "1.0.0",
        league: str = "MLB",
        scope: str = "full_game",
        family: str = "double_chance",
        *,
        numerical_tolerance: float = 1e-8,
        max_support: int = 100000,
        integration_limit: int = 200,
    ):
        if (league, scope, family) != ("MLB", "full_game", "double_chance"):
            raise ValueError("This candidate supports MLB|full_game|double_chance.")
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.tolerance = self._number(
            numerical_tolerance, "numerical_tolerance", positive=True
        )
        if not 0 < self.tolerance < 1:
            raise ValueError("numerical_tolerance must be between zero and one.")
        if (
            isinstance(max_support, bool)
            or int(max_support) != max_support
            or max_support < 1
        ):
            raise ValueError("max_support must be a positive integer.")
        if (
            isinstance(integration_limit, bool)
            or int(integration_limit) != integration_limit
            or integration_limit < 1
        ):
            raise ValueError("integration_limit must be a positive integer.")
        self.max_support = int(max_support)
        self.integration_limit = int(integration_limit)

    @staticmethod
    def _number(value: Any, name: str, *, positive: bool = False) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        if positive and result <= 0:
            raise ValueError(f"{name} must be positive.")
        return result

    @classmethod
    def _nonnegative(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if result < 0:
            raise ValueError(f"{name} must be nonnegative.")
        return result

    @classmethod
    def _probability(cls, value: Any, name: str) -> float:
        result = cls._number(value, name)
        if not 0 <= result <= 1:
            raise ValueError(f"{name} must lie in [0, 1].")
        return result

    @staticmethod
    def _mapping(value: Any, name: str) -> Mapping:
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a mapping.")
        return value

    def _mean(self, context: Mapping, side: str) -> float:
        explicit = f"{side}_expected"
        if explicit in context:
            return self._number(context[explicit], explicit, positive=True)

        opponent = "away" if side == "home" else "home"
        team = self._mapping(context.get(side, {}), side)
        other = self._mapping(context.get(opponent, {}), opponent)
        anchor = 4.60 if side == "home" else 4.20

        factors = (
            (team.get("attack", 1.0), f"{side}.attack"),
            (other.get("defense", 1.0), f"{opponent}.defense"),
            (context.get("park_factor", 1.0), "park_factor"),
            (context.get("pace_factor", 1.0), "pace_factor"),
            (context.get(f"{side}_exposure", 1.0), f"{side}_exposure"),
        )
        log_mean = math.log(anchor)
        for value, name in factors:
            log_mean += math.log(self._number(value, name, positive=True))

        coefficients = self._mapping(
            context.get("feature_coefficients", {}), "feature_coefficients"
        )
        if "rest_log1p" in coefficients:
            own = self._nonnegative(context[f"{side}_rest_days"], "rest_days")
            opposing = self._nonnegative(
                context[f"{opponent}_rest_days"], "opponent_rest_days"
            )
            beta = self._number(coefficients["rest_log1p"], "rest_log1p")
            log_mean += beta * (math.log1p(own) - math.log1p(opposing))

        weather = self._mapping(context.get("weather", {}), "weather")
        centers = self._mapping(
            context.get("feature_centers", {}), "feature_centers"
        )
        dome = weather.get("is_dome", False)
        if not isinstance(dome, (bool, np.bool_)):
            raise ValueError("weather.is_dome must be boolean.")
        for feature in ("wind_speed", "temp"):
            if feature in coefficients and not dome:
                beta = self._number(coefficients[feature], feature + "_coefficient")
                observed = self._number(weather[feature], feature)
                center = self._number(centers[feature], feature + "_center")
                log_mean += beta * (observed - center)

        try:
            mean = math.exp(log_mean)
        except OverflowError as exc:
            raise ValueError("Feature adjustments overflow the expected runs.") from exc
        return self._number(mean, f"{side}_adjusted_expected", positive=True)

    def _alpha(
        self,
        context: Mapping,
        side: str,
        mean: float,
        shared_variance: float,
        innings: int,
    ) -> float:
        observations = context.get(f"{side}_inning_observations")
        index_key = f"{side}_dispersion_index"
        if observations is not None and index_key in context:
            raise ValueError("Supply dispersion moments or inning observations, not both.")

        if observations is not None:
            numerator = []
            denominator = []
            for row in observations:
                row = self._mapping(row, "inning observation")
                runs = self._nonnegative(row["runs"], "inning runs")
                if not runs.is_integer():
                    raise ValueError("Inning runs must be integers.")
                expected = self._number(
                    row["expected"], "inning expected", positive=True
                )
                numerator.append(
                    (runs - expected) ** 2 - runs
                    - shared_variance * expected**2
                )
                denominator.append((1 + shared_variance) * expected**2)
            if not denominator:
                raise ValueError("Inning observations cannot be empty.")
            # Projection onto the nonnegative variance-component parameter space.
            return max(0.0, math.fsum(numerator) / math.fsum(denominator))

        boundary = 1 + mean * shared_variance
        index = self._number(context.get(index_key, boundary), index_key, positive=True)
        if index < boundary:
            raise ValueError(
                f"{index_key} is incompatible with shared_environment_variance."
            )
        return innings * (index - boundary) / (mean * (1 + shared_variance))

    def _pmf(self, mean: float, shape: Optional[float]) -> np.ndarray:
        mean = self._nonnegative(mean, "conditional expected runs")
        if mean == 0:
            return np.ones(1, dtype=float)

        if shape is None:
            law = poisson(mu=mean)
        else:
            shape = self._number(shape, "negative-binomial shape", positive=True)
            success = shape / (shape + mean)
            if not 0 < success < 1:
                raise ArithmeticError("Negative-binomial parameters lost precision.")
            law = nbinom(n=shape, p=success)

        # Numerical truncation, not a sporting score cap.
        tail_budget = self.tolerance**2
        upper = float(law.isf(tail_budget))
        if not math.isfinite(upper):
            raise ArithmeticError("Could not bound the scoring-distribution tail.")
        size = int(math.ceil(upper)) + 1
        if size > self.max_support:
            raise ArithmeticError("Required score support exceeds max_support.")
        mass = np.asarray(law.pmf(np.arange(size)), dtype=float)
        total = float(mass.sum())
        if (
            not np.all(np.isfinite(mass))
            or np.any(mass < 0)
            or total <= 0
            or float(law.sf(size - 1)) > self.tolerance
        ):
            raise ArithmeticError("Scoring-distribution evaluation failed.")
        return mass / total

    @staticmethod
    def _outcomes(home: np.ndarray, away: np.ndarray) -> Tuple[float, float, float]:
        home_cdf = np.cumsum(home)
        away_cdf = np.cumsum(away)
        h_scores = np.arange(home.size)
        a_scores = np.arange(away.size)

        away_below = np.zeros(home.size)
        home_below = np.zeros(away.size)
        away_below[1:] = away_cdf[
            np.minimum(h_scores[1:] - 1, away.size - 1)
        ]
        home_below[1:] = home_cdf[
            np.minimum(a_scores[1:] - 1, home.size - 1)
        ]
        p_home = float(np.dot(home, away_below))
        p_away = float(np.dot(away, home_below))
        common = min(home.size, away.size)
        p_tie = float(np.dot(home[:common], away[:common]))
        total = math.fsum((p_home, p_tie, p_away))
        if not math.isfinite(total) or total <= 0:
            raise ArithmeticError("Invalid outcome mass.")
        return p_home / total, p_tie / total, p_away / total

    def _provided_pmf(self, values: Sequence[float], name: str) -> np.ndarray:
        mass = np.asarray(values, dtype=float)
        if (
            mass.ndim != 1
            or mass.size == 0
            or mass.size > self.max_support
            or not np.all(np.isfinite(mass))
            or np.any(mass < 0)
        ):
            raise ValueError(f"{name} must be a finite nonnegative run PMF.")
        total = float(mass.sum())
        if abs(total - 1) > self.tolerance:
            raise ValueError(f"{name} must sum to one.")
        return mass / total

    def _extra_probability(self, context: Mapping, environment: float) -> float:
        pmf_keys = ("extra_home_pmf", "extra_away_pmf")
        mean_keys = ("extra_home_expected", "extra_away_expected")
        has_pmf = any(key in context for key in pmf_keys)
        has_mean = any(key in context for key in mean_keys)

        if has_pmf and has_mean:
            raise ValueError("Supply extra-inning PMFs or parametric means, not both.")

        if has_pmf:
            if not all(key in context for key in pmf_keys):
                raise ValueError("Both extra-inning PMFs are required.")
            home, away = (
                self._provided_pmf(context[key], key) for key in pmf_keys
            )
        elif has_mean:
            if not all(key in context for key in mean_keys):
                raise ValueError("Both extra-inning means are required.")
            runner = context.get("automatic_runner")
            if runner is None:
                postseason = context.get("is_postseason")
                if not isinstance(postseason, (bool, np.bool_)):
                    raise ValueError(
                        "Specify automatic_runner or is_postseason for extra means."
                    )
                runner = not postseason
            if not isinstance(runner, (bool, np.bool_)):
                raise ValueError("automatic_runner must be boolean.")

            pmfs = []
            for side in ("home", "away"):
                mean = self._number(
                    context[f"extra_{side}_expected"],
                    f"extra_{side}_expected",
                    positive=True,
                )
                index = self._number(
                    context.get(f"extra_{side}_dispersion_index", 1.0),
                    f"extra_{side}_dispersion_index",
                    positive=True,
                )
                if index < 1:
                    raise ValueError("Extra-inning dispersion index must be >= 1.")
                shape = None if index == 1 else mean / (index - 1)
                mass = self._pmf(mean * environment, shape)
                if runner:
                    key = f"extra_{side}_runner_probability"
                    probability = self._probability(context[key], key)
                    mass = np.convolve(mass, [1 - probability, probability])
                pmfs.append(mass)
            home, away = pmfs
        else:
            return 1 / 2

        home_win, _, away_win = self._outcomes(home, away)
        absorbing_mass = home_win + away_win
        if absorbing_mass <= 0:
            raise ValueError("Extra-inning scoring model never produces a winner.")
        return home_win / absorbing_mass

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        context = self._mapping(game_context, "game_context")
        if context.get("includes_extra_innings", True) is not True:
            raise ValueError("This candidate requires settlement including extra innings.")
        if context.get("is_live", False) is not False:
            raise ValueError("This candidate supports pregame predictions only.")

        innings_value = self._number(
            context.get("scheduled_innings", 9), "scheduled_innings", positive=True
        )
        if not innings_value.is_integer():
            raise ValueError("scheduled_innings must be an integer.")
        innings = int(innings_value)
        shared_variance = self._nonnegative(
            context.get("shared_environment_variance", 0.0),
            "shared_environment_variance",
        )

        home_mean = self._mean(context, "home")
        away_mean = self._mean(context, "away")
        home_alpha = self._alpha(
            context, "home", home_mean, shared_variance, innings
        )
        away_alpha = self._alpha(
            context, "away", away_mean, shared_variance, innings
        )
        home_shape = None if home_alpha == 0 else innings / home_alpha
        away_shape = None if away_alpha == 0 else innings / away_alpha

        def conditional_home(environment: float) -> float:
            home = self._pmf(home_mean * environment, home_shape)
            away = self._pmf(away_mean * environment, away_shape)
            p_home, p_tie, _ = self._outcomes(home, away)
            return p_home + p_tie * self._extra_probability(context, environment)

        if shared_variance == 0:
            p_home = conditional_home(1.0)
        else:
            frailty = gamma(a=1 / shared_variance, scale=shared_variance)

            def integrand(quantile: float) -> float:
                environment = float(frailty.ppf(quantile))
                if not math.isfinite(environment) or environment < 0:
                    raise ArithmeticError("Shared-frailty quantile evaluation failed.")
                return conditional_home(environment)

            result = quad(
                integrand,
                0.0,
                1.0,
                epsabs=self.tolerance,
                epsrel=self.tolerance,
                limit=self.integration_limit,
                full_output=1,
            )
            p_home, error = float(result[0]), float(result[1])
            if len(result) != 3 or error > self.tolerance:
                raise ArithmeticError("Shared-frailty integration did not converge.")

        if not math.isfinite(p_home) or not 0 <= p_home <= 1:
            raise ArithmeticError("Invalid final winner probability.")
        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": 0.0, "away": 1 - p_home}
        )

    def value_double_chance(
        self,
        game_context: Dict[str, Any],
        selection: str,
        decimal_odds: float,
        *,
        contract_is_payable: bool,
    ) -> Dict[str, Any]:
        """
        Unit-stake valuation conditional on a completed action-standing game.

        The caller must explicitly establish payability from house rules.
        Nonpayable/void contracts have zero net return. No arbitrary upper
        odds limit or additional edge threshold is imposed.
        """
        if selection not in {"1X", "X2", "12"}:
            raise ValueError("selection must be 1X, X2, or 12.")
        odds = self._number(decimal_odds, "decimal_odds", positive=True)
        if odds < 1:
            raise ValueError("Decimal odds must be at least one.")
        if not isinstance(contract_is_payable, bool):
            raise ValueError("contract_is_payable must be explicitly boolean.")

        distribution = self.predict_distribution(game_context)
        probabilities = distribution.probabilities
        event_probability = {
            "1X": probabilities["home"] + probabilities["draw"],
            "X2": probabilities["away"] + probabilities["draw"],
            "12": probabilities["home"] + probabilities["away"],
        }[selection]
        expected_net = (
            event_probability * odds - 1 if contract_is_payable else 0.0
        )
        return {
            "selection": selection,
            "event_probability": event_probability,
            "expected_net_per_unit_stake": expected_net,
            "eligible": (
                contract_is_payable and odds >= 1.40 and expected_net > 0
            ),
        }