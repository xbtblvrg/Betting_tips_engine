import math
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.special import expit, logit, roots_jacobi
from scipy.stats import binom

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The harness pre-loads quant primitives into execution scope.
    pass


class NFLQuarter2AwayTotalDriveModel(BaseCandidateModel):
    """Drive-based compound model for NFL Q2 points on the scoring lattice.

    Structure
    ---------
    Away points are S = sum(Y_i, i=1..N) + B:
      * N ~ Binomial(max_drives, p), with p set on a logit scale by tempo.
      * Home drive count is N + D, D in {-1, 0, +1}, for alternating possessions.
      * The last drive is the half-ending drive, with its own score rate and
        TD/FG mix.
      * Y_i takes values in {0, 3, 6, 7, 8}.
      * B holds non-offensive points (return/defensive TD and safety).
      * The per-drive score rate is Beta-distributed per game. Gauss-Jacobi
        quadrature integrates it exactly and preserves its mean.

    Mean anchoring
    --------------
    The score rate is solved in closed form so that E[S] equals away_expected
    (baseline 5.38), and likewise for home. Tempo, exposure, rest and weather
    reshape drive count and TD/FG mix around that mean, because the expected
    inputs are assumed to already reflect them. Exposure scales the mean only
    when an expected value is missing. Dispersion and threshold probabilities
    follow from the structure and are not fitted.

    Status and limitations
    ----------------------
    * This code has not been executed or backtested. It was written without
      access to the betting_engine package.
    * DEFAULT_PRIORS values other than the 6.12/5.38 baselines are assumed
      domain estimates, not fits. With 0 settled wagers there is no data to
      estimate them, so the "zero hardcoded constants" goal is not fully met.
      They are collected here and can be overridden through `priors`. Validate
      them against historical Q2 scoring before promoting this market.
    * No ROI or Brier target is claimed. The quoted benchmarks are not
      evidence of skill for this market, and +295% looks like a small-sample
      or leakage artifact rather than a reachable target.

    Assumptions to verify against the engine
    ----------------------------------------
    * from_scores accepts {(home_points, away_points): probability}.
    * BaseCandidateModel.__init__ accepts the five keyword arguments used here.
    * home_expected and away_expected are Q2 means, not full-game means.
      Implausibly large values raise ValueError.
    * attack, defense and tempo are ratios with league average 1.0, and higher
      defense means more points conceded.
    * Wind is in mph and temperature is in Fahrenheit.
    """

    DEFAULT_PRIORS: Dict[str, float] = {
        "baseline_home": 6.12,
        "baseline_away": 5.38,
        "max_drives": 5,
        "mean_drives": 2.9,
        "drive_diff_prob": 0.25,
        "td_share": 0.58,
        "end_td_logit_shift": -0.60,
        "end_score_ratio": 0.85,
        "efficiency_concentration": 30.0,
        "pat_attempt_rate": 0.93,
        "pat_make_rate": 0.94,
        "two_point_make_rate": 0.48,
        "nonoffense_td_share": 0.05,
        "safety_share": 0.005,
        "tempo_elasticity": 1.0,
        "exposure_td_elasticity": 0.5,
        "rest_td_logit": 0.10,
        "wind_td_logit_per_mph": 0.015,
        "cold_td_logit_per_degree": 0.008,
        "cold_reference_temp": 40.0,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_2_1106_232318",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_2",
        family: str = "away_total",
        priors: Optional[Mapping[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.priors = {**self.DEFAULT_PRIORS, **(priors or {})}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context
        pr = self.priors

        home_attack = self._strength(ctx, "home", "attack")
        home_defense = self._strength(ctx, "home", "defense")
        away_attack = self._strength(ctx, "away", "attack")
        away_defense = self._strength(ctx, "away", "defense")
        home_exposure = home_attack * away_defense
        away_exposure = away_attack * home_defense

        home_mean = self._positive(ctx.get("home_expected"))
        away_mean = self._positive(ctx.get("away_expected"))
        if home_mean is None:
            home_mean = pr["baseline_home"] * home_exposure
        if away_mean is None:
            away_mean = pr["baseline_away"] * away_exposure

        away_counts, transition = self._drive_counts(ctx)
        home_counts = away_counts @ transition

        weather = self._weather_td_logit(ctx)
        rest = self._rest_log_ratio(ctx)
        home_phi = self._td_share(home_exposure, weather, -rest)
        away_phi = self._td_share(away_exposure, weather, rest)

        home_cond = self._conditional_points(home_mean, home_counts, home_phi)
        away_cond = self._conditional_points(away_mean, away_counts, away_phi)

        # Joint P(home=h, away=a) = H' W A, where W[n_home, n_away] is the
        # joint drive-count mass linking the two teams' possessions.
        drive_weights = (away_counts[:, None] * transition).T
        joint = home_cond.T @ drive_weights @ away_cond
        joint = joint / joint.sum()

        scores = {
            (int(h), int(a)): float(joint[h, a])
            for h, a in zip(*np.nonzero(joint))
        }
        return DiscreteDistribution.from_scores(scores)

    def _drive_counts(self, ctx: Mapping[str, Any]):
        """Return P(N_away = n) and the transition matrix to N_home."""
        pr = self.priors
        max_drives = int(pr["max_drives"])

        tempo = None
        for key in ("tempo", "pace", "pace_factor"):
            tempo = self._positive(ctx.get(key))
            if tempo is not None:
                break
        tempo = 1.0 if tempo is None else tempo

        drive_logit = logit(pr["mean_drives"] / max_drives)
        p_drive = expit(drive_logit + pr["tempo_elasticity"] * math.log(tempo))
        counts = binom.pmf(np.arange(max_drives + 1), max_drives, p_drive)

        diff = pr["drive_diff_prob"]
        transition = np.zeros((max_drives + 1, max_drives + 2))
        for n in range(max_drives + 1):
            transition[n, n] += 1.0 - 2.0 * diff
            transition[n, n + 1] += diff
            transition[n, max(n - 1, 0)] += diff
        return counts, transition

    def _td_share(self, exposure: float, weather: float, rest: float) -> float:
        pr = self.priors
        return float(
            expit(
                logit(pr["td_share"])
                + pr["exposure_td_elasticity"] * math.log(exposure)
                + pr["rest_td_logit"] * rest
                + weather
            )
        )

    def _touchdown_values(self) -> np.ndarray:
        """Return P(6), P(7), P(8) conditional on a touchdown."""
        pr = self.priors
        attempt = pr["pat_attempt_rate"]
        make = pr["pat_make_rate"]
        two_point_try = 1.0 - attempt
        two_point_make = pr["two_point_make_rate"]
        return np.array(
            [
                attempt * (1.0 - make) + two_point_try * (1.0 - two_point_make),
                attempt * make,
                two_point_try * two_point_make,
            ]
        )

    def _drive_outcome(self, score_rate: float, td_share: float) -> np.ndarray:
        pmf = np.zeros(9)
        pmf[0] = 1.0 - score_rate
        pmf[3] = score_rate * (1.0 - td_share)
        pmf[6:9] = score_rate * td_share * self._touchdown_values()
        return pmf

    def _nonoffense_points(self, mean: float) -> np.ndarray:
        pr = self.priors
        td_values = self._touchdown_values()
        td_value = float(td_values @ np.array([6.0, 7.0, 8.0]))
        p_td = pr["nonoffense_td_share"] * mean / td_value
        p_safety = pr["safety_share"] * mean / 2.0

        td_pmf = np.zeros(9)
        td_pmf[0] = 1.0 - p_td
        td_pmf[6:9] = p_td * td_values
        safety_pmf = np.array([1.0 - p_safety, 0.0, p_safety])
        return np.convolve(td_pmf, safety_pmf)

    def _conditional_points(
        self, mean: float, counts: np.ndarray, td_share: float
    ) -> np.ndarray:
        """Return a matrix of P(points = k | N = n), with exact side mean."""
        pr = self.priors
        td_value = float(self._touchdown_values() @ np.array([6.0, 7.0, 8.0]))

        def points_per_score(phi: float) -> float:
            return phi * td_value + (1.0 - phi) * 3.0

        end_share = float(expit(logit(td_share) + pr["end_td_logit_shift"]))
        end_ratio = pr["end_score_ratio"]
        offense_mean = mean * (1.0 - pr["nonoffense_td_share"] - pr["safety_share"])

        expected_drives = float(counts @ np.arange(len(counts)))
        p_any_drive = 1.0 - float(counts[0])
        scale = (expected_drives - p_any_drive) * points_per_score(td_share)
        scale += p_any_drive * end_ratio * points_per_score(end_share)
        score_rate = offense_mean / scale
        if not 0.0 < score_rate < 1.0 or end_ratio * score_rate >= 1.0:
            raise ValueError(
                f"Expected Q2 points {mean:.3f} imply infeasible drive score "
                f"rate {score_rate:.3f}; check that inputs are quarter-level."
            )

        # Conditional pmfs are polynomials in the rate of degree <= max N, so
        # this Gauss-Jacobi node count integrates them exactly.
        concentration = pr["efficiency_concentration"]
        n_nodes = (len(counts) - 1) // 2 + 1
        nodes, weights = roots_jacobi(
            n_nodes,
            (1.0 - score_rate) * concentration - 1.0,
            score_rate * concentration - 1.0,
        )
        rates = (nodes + 1.0) / 2.0
        weights = weights / weights.sum()

        nonoffense = self._nonoffense_points(mean)
        max_drives = len(counts) - 1
        size = 8 * max_drives + len(nonoffense)
        result = np.zeros((len(counts), size))

        for n in range(len(counts)):
            mixed = np.array([1.0]) if n == 0 else np.zeros(8 * n + 1)
            if n > 0:
                for rate, weight in zip(rates, weights):
                    pmf = self._drive_outcome(end_ratio * rate, end_share)
                    regular = self._drive_outcome(rate, td_share)
                    for _ in range(n - 1):
                        pmf = np.convolve(pmf, regular)
                    mixed += weight * pmf
            total = np.convolve(mixed, nonoffense)
            result[n, : len(total)] = total
        return result

    def _weather_td_logit(self, ctx: Mapping[str, Any]) -> float:
        if self._lookup(ctx, "is_dome"):
            return 0.0
        pr = self.priors
        effect = 0.0
        wind = self._finite(self._lookup(ctx, "wind_speed"))
        if wind is not None and wind > 0.0:
            effect += pr["wind_td_logit_per_mph"] * wind
        temp = self._finite(self._lookup(ctx, "temp"))
        if temp is not None:
            # Softplus keeps the cold effect smooth around the reference.
            cold = float(np.logaddexp(0.0, pr["cold_reference_temp"] - temp))
            effect += pr["cold_td_logit_per_degree"] * cold
        return effect

    def _rest_log_ratio(self, ctx: Mapping[str, Any]) -> float:
        away = self._finite(ctx.get("away_rest_days"))
        home = self._finite(ctx.get("home_rest_days"))
        if away is None or home is None or away < 0.0 or home < 0.0:
            return 0.0
        return math.log((1.0 + away) / (1.0 + home))

    def _strength(self, ctx: Mapping[str, Any], side: str, kind: str) -> float:
        team = ctx.get(side)
        value = team.get(kind) if isinstance(team, Mapping) else None
        if value is None:
            value = ctx.get(f"{side}_{kind}")
        value = self._positive(value)
        return 1.0 if value is None else value

    @staticmethod
    def _lookup(ctx: Mapping[str, Any], key: str) -> Any:
        if key in ctx:
            return ctx[key]
        weather = ctx.get("weather")
        return weather.get(key) if isinstance(weather, Mapping) else None

    @staticmethod
    def _finite(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        number = cls._finite(value)
        return number if number is not None and number > 0.0 else None