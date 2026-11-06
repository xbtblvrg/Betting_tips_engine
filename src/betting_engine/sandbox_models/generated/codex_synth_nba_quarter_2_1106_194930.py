"""Compound-possession model for NBA|quarter_2|away_total.

Away Q2 points are modelled as A = sum_{i=1..N} X_i:
  * X_i is the points scored on a possession, on {0, 1, 2, 3, 4}.
  * N is the number of possessions, shared by both teams.
  * PPP has a latent lognormal game-level shock.

Conditional on N and the PPP shock, the exact integer-valued pmf comes from
FFT convolution, so whole-number lines keep their push mass. The implied
moments are:
  mu      = E[N] * PPP
  sigma^2 = E[N] * Var(X) + Var(N) * PPP^2 + E[N]^2 * Var(PPP)
At the default priors and mu = 27, Var/Mean is about 1.5. The 1.000 in the
brief is a Poisson placeholder rather than an observed value.

STATUS AND LIMITATIONS
  * Not executed or tested. The betting_engine package was not available when
    this was written, so the DiscreteDistribution import and from_scores input
    format are assumptions. _to_distribution tries a {(home, away): p} dict,
    then a matrix.
  * There are 0 settled wagers, so nothing here is fitted to this market and
    there is no evidence of edge. DEFAULT_PRIORS and the unit-detection
    thresholds are domain estimates, not empirical fits. Fit the priors to Q2
    play-by-play and pass them through `priors`.
  * Keep this market out of live staking until out-of-sample calibration and
    closing-line value confirm an edge. The ROI benchmarks in the brief were
    not used as calibration targets.
  * If away_expected is derived from bookmaker lines, market information enters
    through the anchor. Confirm its source to meet the no-vig-blending rule.
  * attack/defense values are read as multiplicative rates, with defense > 1
    meaning more points allowed. Values above RATING_UNIT_THRESHOLD are read as
    per-100 ratings. Confirm this matches the feed.
  * Q2 bench rotations, bonus fouls and two-for-one sequences are not explicit
    features. They are represented only through the anchor, X and Var(N).
  * Weather and venue are ignored because they have no modelled effect on
    indoor NBA scoring.
"""

import math
from typing import Any, Dict, Mapping, Optional

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    pass  # The brief says this is pre-loaded in the execution scope.


DEFAULT_PRIORS: Dict[str, float] = {
    "baseline_home": 28.0,          # Q2 baseline from the brief
    "baseline_away": 27.0,          # Q2 baseline from the brief
    "league_pace": 99.0,            # possessions per 48 minutes
    "league_rating": 114.5,         # points per 100 possessions
    "q2_share": 0.25,               # Q2 share of full-game points/possessions
    "var_possessions": 4.0,         # Var(N) in Q2
    "ppp_log_sd": 0.05,             # game-level PPP shock (log scale)
    "anchor_exposure_weight": 0.25, # partial weight: anchor likely embeds features
    "rest_offense_effect": 0.03,    # log-PPP cost of own fatigue
    "rest_defense_effect": 0.015,   # log-PPP gain from opponent fatigue
    "rest_decay_days": 1.0,         # fatigue decay scale
    "typical_rest_days": 2.0,       # rest level treated as neutral
}

# Possession points on {0, 1, 2, 3, 4}; mean is about 1.145 before tilting.
BASE_OUTCOME_PMF = np.array([0.49, 0.04, 0.31, 0.155, 0.005])

# Unit detection only: these values separate scales and do not limit forecasts.
QUARTER_POINTS_UNIT_THRESHOLD = 60.0
QUARTER_PACE_UNIT_THRESHOLD = 60.0
RATING_UNIT_THRESHOLD = 5.0
QUARTERS_PER_GAME = 4.0

N_SHOCK_NODES = 9
N_SD_SPAN = 6.0
TAIL_TRIM = 1e-10
TILT_BRACKET = 30.0


def _num(value: Any) -> Optional[float]:
    """Return a finite float, or None for missing or non-numeric input."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class NbaQuarter2AwayTotalModel(BaseCandidateModel):
    """Possession-level compound model of Q2 scoring with shared pace."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_2_1106_194930",
        version: str = "1.0.0",
        league: str = "NBA",
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
        self.priors = {**DEFAULT_PRIORS, **(priors or {})}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        return self._to_distribution(self._score_matrix(game_context))

    def away_total_probabilities(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Return exact over/push/under probabilities for an away-total line."""
        pmf = self._score_matrix(game_context).sum(axis=0)
        points = np.arange(pmf.size)
        return {
            "over": float(pmf[points > line].sum()),
            "push": float(pmf[points == line].sum()),
            "under": float(pmf[points < line].sum()),
        }

    # ---- Situational inputs ---------------------------------------------

    @staticmethod
    def _team_value(ctx: Mapping[str, Any], side: str, key: str) -> Optional[float]:
        flat = _num(ctx.get(f"{side}_{key}"))
        if flat is not None:
            return flat
        team = ctx.get(side)
        return _num(team.get(key)) if isinstance(team, dict) else None

    def _rate_multiplier(self, ctx: Mapping[str, Any], side: str, key: str) -> float:
        value = self._team_value(ctx, side, key)
        if value is None or value <= 0.0:
            return 1.0
        if value > RATING_UNIT_THRESHOLD:
            return value / self.priors["league_rating"]
        return value

    def _pace_per_48(self, value: Optional[float]) -> Optional[float]:
        if value is None or value <= 0.0:
            return None
        return value if value > QUARTER_PACE_UNIT_THRESHOLD else value * QUARTERS_PER_GAME

    def _matchup_pace(self, ctx: Mapping[str, Any]) -> float:
        league = self.priors["league_pace"]
        home = self._pace_per_48(self._team_value(ctx, "home", "pace"))
        away = self._pace_per_48(self._team_value(ctx, "away", "pace"))
        if home is not None and away is not None:
            return home * away / league
        if home is not None or away is not None:
            return home if home is not None else away
        for key in ("pace", "tempo"):
            shared = self._pace_per_48(_num(ctx.get(key)))
            if shared is not None:
                return shared
        return league

    def _fatigue(self, rest_days: Optional[float]) -> float:
        """Return fatigue relative to typical rest; missing rest is neutral."""
        if rest_days is None or rest_days < 0.0:
            return 0.0
        tau = self.priors["rest_decay_days"]
        return math.exp(-rest_days / tau) - math.exp(-self.priors["typical_rest_days"] / tau)

    def _expected_points(self, ctx: Mapping[str, Any], side: str, pace: float) -> float:
        opponent = "home" if side == "away" else "away"
        anchor = _num(ctx.get(f"{side}_expected"))
        supplied = anchor is not None and anchor > 0.0
        if not supplied:
            anchor = self.priors[f"baseline_{side}"]
        elif anchor > QUARTER_POINTS_UNIT_THRESHOLD:
            anchor *= self.priors["q2_share"]  # full-game input

        # A supplied anchor probably embeds these features, so damp them to
        # limit double counting. Without one, they apply in full.
        weight = self.priors["anchor_exposure_weight"] if supplied else 1.0
        log_adjustment = (
            math.log(pace / self.priors["league_pace"])
            + math.log(self._rate_multiplier(ctx, side, "attack"))
            + math.log(self._rate_multiplier(ctx, opponent, "defense"))
            - self.priors["rest_offense_effect"] * self._fatigue(_num(ctx.get(f"{side}_rest_days")))
            + self.priors["rest_defense_effect"] * self._fatigue(_num(ctx.get(f"{opponent}_rest_days")))
        )
        return anchor * math.exp(weight * log_adjustment)

    # ---- Distribution construction --------------------------------------

    @staticmethod
    def _possession_pmf(target_ppp: float) -> np.ndarray:
        """Exponentially tilt the base outcomes to hit the target PPP exactly."""
        outcomes = np.arange(BASE_OUTCOME_PMF.size, dtype=float)
        if not 0.0 < target_ppp < outcomes[-1]:
            raise ValueError(f"Target PPP {target_ppp:.3f} is outside the possession support")

        def tilted(theta: float) -> np.ndarray:
            weights = BASE_OUTCOME_PMF * np.exp(theta * outcomes)
            return weights / weights.sum()

        theta = brentq(
            lambda t: float(tilted(t) @ outcomes) - target_ppp,
            -TILT_BRACKET,
            TILT_BRACKET,
        )
        return tilted(theta)

    def _possession_count_pmf(self, expected_n: float) -> Dict[str, np.ndarray]:
        sd = math.sqrt(self.priors["var_possessions"])
        low = max(0, int(math.floor(expected_n - N_SD_SPAN * sd)))
        high = int(math.ceil(expected_n + N_SD_SPAN * sd))
        counts = np.arange(low, high + 1)
        probabilities = norm.cdf(counts + 0.5, expected_n, sd) - norm.cdf(counts - 0.5, expected_n, sd)
        return {"counts": counts, "probabilities": probabilities / probabilities.sum()}

    def _points_given_count(self, mu: float, expected_n: float, counts: np.ndarray) -> np.ndarray:
        """Return P(points = k | N = n), with rows for n and columns for k."""
        size = (BASE_OUTCOME_PMF.size - 1) * int(counts[-1]) + 1
        nodes, weights = np.polynomial.hermite_e.hermegauss(N_SHOCK_NODES)
        weights = weights / weights.sum()
        log_sd = self.priors["ppp_log_sd"]

        result = np.zeros((counts.size, size))
        for node, weight in zip(nodes, weights):
            target_ppp = (mu / expected_n) * math.exp(log_sd * node - 0.5 * log_sd ** 2)
            transform = np.fft.fft(self._possession_pmf(target_ppp), size)
            result += weight * np.fft.ifft(transform[None, :] ** counts[:, None], axis=1).real

        result = np.maximum(result, 0.0)  # removes FFT round-off only
        return result / result.sum(axis=1, keepdims=True)

    def _score_matrix(self, ctx: Mapping[str, Any]) -> np.ndarray:
        """Return the joint pmf, with rows for home points and columns for away."""
        pace = self._matchup_pace(ctx)
        expected_n = pace * self.priors["q2_share"]
        count_pmf = self._possession_count_pmf(expected_n)
        counts = count_pmf["counts"]

        home = self._points_given_count(self._expected_points(ctx, "home", pace), expected_n, counts)
        away = self._points_given_count(self._expected_points(ctx, "away", pace), expected_n, counts)

        # Shared N induces the positive pace correlation between team scores.
        joint = np.einsum("n,ni,nj->ij", count_pmf["probabilities"], home, away)

        home_end = int(np.searchsorted(np.cumsum(joint.sum(axis=1)), 1.0 - TAIL_TRIM)) + 1
        away_end = int(np.searchsorted(np.cumsum(joint.sum(axis=0)), 1.0 - TAIL_TRIM)) + 1
        joint = joint[:home_end, :away_end]
        return joint / joint.sum()

    @staticmethod
    def _to_distribution(matrix: np.ndarray) -> "DiscreteDistribution":
        scores = {
            (int(home), int(away)): float(matrix[home, away])
            for home, away in zip(*np.nonzero(matrix))
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError, KeyError):
            return DiscreteDistribution.from_scores(matrix)