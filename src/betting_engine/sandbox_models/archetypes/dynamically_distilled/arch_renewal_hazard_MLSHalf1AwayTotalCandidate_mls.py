"""Candidate model for MLS|half_1|away_total: shared-frailty Cox-Poisson score lattice."""

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy.special import gammaln
from scipy.stats import nbinom

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # The batch harness documents DiscreteDistribution as pre-loaded in execution scope.
    pass


class MLSHalf1AwayTotalCandidate(BaseCandidateModel):
    """Prices first-half MLS scores from a Gamma-mixed Poisson process.

    Structure
    ---------
    Conditional on a shared match-intensity frailty G ~ Gamma(kappa, kappa):

        N_home | G ~ Poisson(mu_home * G)
        N_away | G ~ Poisson(mu_away * G)

    Integrating out G gives the closed-form joint lattice, with s = kappa + mu_home + mu_away:

        P(h, a) = Gamma(kappa + h + a) / (Gamma(kappa) * h! * a!)
                  * (kappa / s)^kappa * (mu_home / s)^h * (mu_away / s)^a

    The away marginal is negative binomial, so:

        P(N_away = 0) = (1 + mu_away / kappa)^(-kappa)
        Var / Mean    = 1 + mu_away / kappa

    Poisson is recovered as kappa -> infinity. Missing inputs lower kappa, so
    uncertainty about the match widens the distribution without moving its mean.

    Calibration status
    ------------------
    This model has NOT been fit, backtested or executed. The segment has 0 settled
    wagers, so no edge or repair of a leak has been demonstrated. The anchors (0.60,
    0.80) come from the brief. Every other constructor default is a judgment prior,
    not an estimate, and should be replaced by a hierarchical marginal-likelihood fit
    on historical MLS first-half goals, then validated out of sample before staking.

    Simplifications
    ---------------
    - The time-varying hazard and score-state feedback (lambda_0(t), gamma) are not
      modeled explicitly. They need minute-level data, so only their marginal effect
      is represented, through dispersion.
    - Travel, altitude, turf and weather are excluded because their keys or units are
      not specified. Add them once those are confirmed.
    - Market odds are never read, so no bookmaker probability enters the forecast.

    Interface assumptions to confirm against the engine
    ---------------------------------------------------
    - DiscreteDistribution.from_scores accepts a {(home_goals, away_goals): prob} dict.
    - BaseCandidateModel.__init__ accepts model_id, version, league, scope and family
      as keyword arguments.
    - home_expected and away_expected are first-half means. If they are full-match
      means, set expected_scope_share to the first-half share of scoring.
    - Attack and defense are positive indices centered on 1.0, where a higher defense
      value means the team concedes more. Non-positive values are treated as missing.
    - Pace comes from 'pace_index' (centered on 1.0) or from 'pace' / 'league_pace'.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1106_230130",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "away_total",
        home_anchor: float = 0.80,
        away_anchor: float = 0.60,
        expected_scope_share: float = 1.0,
        beta_expected: float = 0.80,
        beta_pace: float = 0.50,
        beta_matchup: float = 0.10,
        beta_rest: float = 0.04,
        kappa_prior: float = 12.0,
        missing_feature_variance: float = 0.05,
        lattice_tail_mass: float = 1e-10,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.home_anchor = home_anchor
        self.away_anchor = away_anchor
        self.expected_scope_share = expected_scope_share
        self.beta_expected = beta_expected
        self.beta_pace = beta_pace
        self.beta_matchup = beta_matchup
        self.beta_rest = beta_rest
        self.kappa_prior = kappa_prior
        self.missing_feature_variance = missing_feature_variance
        self.lattice_tail_mass = lattice_tail_mass

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        """Return value as a finite float, or None if it is not one."""
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @classmethod
    def _positive(cls, value: Any) -> Optional[float]:
        number = cls._number(value)
        return number if number is not None and number > 0.0 else None

    @classmethod
    def _team_rating(
        cls, game_context: Mapping[str, Any], side: str, rating: str
    ) -> Optional[float]:
        """Read a rating from game_context[side][rating] or game_context[f'{side}_{rating}']."""
        team = game_context.get(side)
        if isinstance(team, Mapping):
            nested = cls._positive(team.get(rating))
            if nested is not None:
                return nested
        return cls._positive(game_context.get(f"{side}_{rating}"))

    @classmethod
    def _pace_ratio(cls, game_context: Mapping[str, Any]) -> Optional[float]:
        index = cls._positive(game_context.get("pace_index"))
        if index is not None:
            return index
        pace = cls._positive(game_context.get("pace"))
        reference = cls._positive(game_context.get("league_pace"))
        return pace / reference if pace is not None and reference is not None else None

    def _expected_log_ratio(self, expected: Optional[float], anchor: float) -> float:
        if expected is None:
            return 0.0
        return math.log(expected * self.expected_scope_share / anchor)

    def _match_parameters(
        self, game_context: Mapping[str, Any]
    ) -> Tuple[float, float, float]:
        """Return (mu_home, mu_away, kappa) for the first half."""
        home_expected = self._positive(game_context.get("home_expected"))
        away_expected = self._positive(game_context.get("away_expected"))
        home_attack = self._team_rating(game_context, "home", "attack")
        home_defense = self._team_rating(game_context, "home", "defense")
        away_attack = self._team_rating(game_context, "away", "attack")
        away_defense = self._team_rating(game_context, "away", "defense")
        home_rest = self._number(game_context.get("home_rest_days"))
        away_rest = self._number(game_context.get("away_rest_days"))
        home_rest = home_rest if home_rest is not None and home_rest >= 0.0 else None
        away_rest = away_rest if away_rest is not None and away_rest >= 0.0 else None
        pace_ratio = self._pace_ratio(game_context)

        features = (
            home_expected,
            away_expected,
            home_attack,
            home_defense,
            away_attack,
            away_defense,
            home_rest,
            away_rest,
            pace_ratio,
        )
        missing_share = sum(feature is None for feature in features) / len(features)

        # A log difference gives rest diminishing returns without a discrete cap.
        rest_edge = 0.0
        if home_rest is not None and away_rest is not None:
            rest_edge = math.log1p(away_rest) - math.log1p(home_rest)

        pace_term = self.beta_pace * math.log(pace_ratio) if pace_ratio is not None else 0.0

        # Expected goals may already embed these ratings, so beta_matchup is a small
        # residual adjustment rather than a full multiplicative effect.
        away_matchup = sum(
            math.log(rating) for rating in (away_attack, home_defense) if rating is not None
        )
        home_matchup = sum(
            math.log(rating) for rating in (home_attack, away_defense) if rating is not None
        )

        mu_away = self.away_anchor * math.exp(
            self.beta_expected * self._expected_log_ratio(away_expected, self.away_anchor)
            + pace_term
            + self.beta_matchup * away_matchup
            + self.beta_rest * rest_edge
        )
        mu_home = self.home_anchor * math.exp(
            self.beta_expected * self._expected_log_ratio(home_expected, self.home_anchor)
            + pace_term
            + self.beta_matchup * home_matchup
            - self.beta_rest * rest_edge
        )

        # Var(G) = 1 / kappa, so missing-information variance adds to the prior variance.
        frailty_variance = (
            1.0 / self.kappa_prior + self.missing_feature_variance * missing_share
        )
        return mu_home, mu_away, 1.0 / frailty_variance

    def _lattice_size(self, mu: float, kappa: float) -> int:
        """Return the goal count covering all but lattice_tail_mass of a marginal."""
        quantile = nbinom.ppf(1.0 - self.lattice_tail_mass, kappa, kappa / (kappa + mu))
        return int(quantile) + 1

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mu_home, mu_away, kappa = self._match_parameters(game_context)

        home_goals = np.arange(self._lattice_size(mu_home, kappa) + 1)
        away_goals = np.arange(self._lattice_size(mu_away, kappa) + 1)
        home_grid, away_grid = np.meshgrid(home_goals, away_goals, indexing="ij")

        total_rate = kappa + mu_home + mu_away
        log_probability = (
            gammaln(kappa + home_grid + away_grid)
            - gammaln(kappa)
            - gammaln(home_grid + 1)
            - gammaln(away_grid + 1)
            + kappa * math.log(kappa / total_rate)
            + home_grid * math.log(mu_home / total_rate)
            + away_grid * math.log(mu_away / total_rate)
        )
        probability = np.exp(log_probability)
        probability /= probability.sum()

        scores = {
            (int(home), int(away)): float(probability[home, away])
            for home in home_goals
            for away in away_goals
        }
        return DiscreteDistribution.from_scores(scores)