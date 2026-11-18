"""
Candidate model for UEL | full_game | moneyline.

Model structure (derived from football scoring mechanics):

1. Goals are rare, roughly independent arrivals, so each side's count is
   modeled as Poisson. The observed dispersion index is 1.000, so no
   extra-Poisson dispersion is added at the per-match level.

2. Situational inputs act multiplicatively on the scoring intensity
   (log-linear), anchored on the empirical UEL means (home 1.60, away 1.20):
     - attack/defense exposure: log-deviations from the league norm
     - rest: fatigue recovery as a saturating exponential in rest days
     - tempo/pace: a shared scale on both intensities
     - weather: mild shared suppression of total intensity

3. Home-field confidence is limited in two ways:
     (a) Empirical-Bayes shrinkage of the log intensity ratio toward the
         empirical UEL home edge, log(1.60 / 1.20). The shrinkage weight
         comes from a signal-to-noise ratio, not a fixed clamp.
     (b) Uncertainty in the strength estimate itself is integrated out
         with Gauss-Hermite quadrature over a shared tempo factor and a
         relative-strength factor. This yields a mixture of Poissons,
         which spreads margin mass and stops extreme favourites from
         being over-priced.

4. Low-score dependence (0-0, 1-0, 0-1, 1-1) uses a Dixon-Coles tau
   correction. Its rho is limited only to the mathematically feasible
   region, where every adjusted cell stays non-negative.

5. The score lattice is truncated adaptively: it grows until the omitted
   tail mass is negligible. It is then reduced to home / draw / away.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded; import defensively.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: F821
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class UELFullGameMoneylineModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Domain priors. Each value is derived from an empirical moment or a
    # physiological / structural property of UEL football, not tuned.
    # ------------------------------------------------------------------ #
    EMP_HOME_MEAN = 1.60          # empirical UEL home goals per match
    EMP_AWAY_MEAN = 1.20          # empirical UEL away goals per match
    EMP_DISPERSION_INDEX = 1.000  # observed Var/Mean, so Poisson kernel

    # Effective number of matches informing a team-strength estimate.
    # Roughly one domestic season plus the European campaign.
    PRIOR_INFO_MATCHES = 30.0

    # Between-fixture heterogeneity (SD) of the true log intensity ratio
    # in UEL. Sets the signal variance for empirical-Bayes shrinkage.
    LOG_RATIO_HETEROGENEITY_SD = 0.40

    # Muscle-glycogen and neuromuscular recovery follows ~48-72h
    # kinetics, which gives a recovery time constant of about 2 days.
    REST_RECOVERY_TAU_DAYS = 2.0
    # Maximum log-intensity swing between a fully fatigued and a fully
    # recovered side.
    REST_MAX_LOG_EFFECT = 0.08

    # Dixon-Coles low-score dependence prior for European club football.
    DC_RHO_PRIOR = -0.06

    # Gauss-Hermite nodes per dimension for the uncertainty integration.
    GH_NODES = 9
    # Tail mass below which the score lattice stops growing.
    LATTICE_TAIL_TOL = 1e-10

    MIN_ELIGIBLE_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_uel_full_game_1107_212134",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "full_game",
        family: str = "moneyline",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:  # tolerate base classes with positional signatures
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self._gh_x, self._gh_w = np.polynomial.hermite.hermgauss(self.GH_NODES)

    # ------------------------------------------------------------------ #
    # Feature extraction helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        """Return value as a finite float, or None if that isn't possible."""
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        """Return the first finite numeric value among the given keys."""
        for k in keys:
            if k in ctx:
                v = self._num(ctx[k])
                if v is not None:
                    return v
        return None

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Return a multiplicative attack/defense rating (1.0 = league average).

        Accepts nested ({'home': {'attack': x}}) or flat ('home_attack')
        layouts.
        """
        nested = ctx.get(side)
        if isinstance(nested, dict):
            for key in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = self._num(nested.get(key))
                if v is not None and v > 0:
                    return v
        v = self._get(ctx, f"{side}_{kind}", f"{side}_{kind}_rating", f"{side}_{kind}_strength")
        if v is not None and v > 0:
            return v
        return None

    def _rest_recovery(self, days: Optional[float]) -> Optional[float]:
        """Fraction of full recovery after `days` of rest: 1 - exp(-d / tau)."""
        if days is None or days < 0:
            return None
        return 1.0 - math.exp(-days / self.REST_RECOVERY_TAU_DAYS)

    @staticmethod
    def _is_neutral(ctx: Dict[str, Any]) -> bool:
        """Detect a neutral venue (for example, the final)."""
        if bool(ctx.get("neutral_site") or ctx.get("is_neutral")):
            return True
        venue = ctx.get("venue")
        if isinstance(venue, dict):
            return bool(venue.get("neutral") or venue.get("is_neutral"))
        if isinstance(venue, str):
            return "neutral" in venue.lower()
        return False

    # ------------------------------------------------------------------ #
    # Intensity construction
    # ------------------------------------------------------------------ #
    def _base_intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float, int]:
        """Build home and away scoring intensities from situational features.

        Returns (lam_home, lam_away, n_feature_groups_used).
        """
        lam_h = self._get(ctx, "home_expected", "home_xg", "lambda_home")
        lam_a = self._get(ctx, "away_expected", "away_xg", "lambda_away")
        informed = 0
        if lam_h is not None and lam_h > 0:
            informed += 1
        else:
            lam_h = self.EMP_HOME_MEAN
        if lam_a is not None and lam_a > 0:
            informed += 1
        else:
            lam_a = self.EMP_AWAY_MEAN

        log_h, log_a = math.log(lam_h), math.log(lam_a)

        # Attack/defense exposure. If upstream expected goals already exist,
        # ratings partly duplicate that information. Weight them by the
        # share of information they add: equal weighting when both sources
        # are present, full weight when the ratings are the only source.
        ha = self._team_rating(ctx, "home", "attack")
        hd = self._team_rating(ctx, "home", "defense")
        aa = self._team_rating(ctx, "away", "attack")
        ad = self._team_rating(ctx, "away", "defense")
        rating_weight = 0.5 if informed > 0 else 1.0
        # A defense rating above 1 means more goals conceded (exposure).
        if ha is not None or ad is not None:
            log_h += rating_weight * (math.log(ha or 1.0) + math.log(ad or 1.0))
            informed += 1
        if aa is not None or hd is not None:
            log_a += rating_weight * (math.log(aa or 1.0) + math.log(hd or 1.0))
            informed += 1

        # Rest and fatigue: the better-recovered side gains intensity, and
        # the effect is antisymmetric across the two sides.
        rh = self._rest_recovery(self._get(ctx, "home_rest_days", "home_rest"))
        ra = self._rest_recovery(self._get(ctx, "away_rest_days", "away_rest"))
        if rh is not None and ra is not None:
            delta = self.REST_MAX_LOG_EFFECT * (rh - ra)
            log_h += delta
            log_a -= delta
            informed += 1

        # Tempo/pace scales both sides' intensity. Expressed relative to a
        # league norm of 1.0.
        pace = self._get(ctx, "pace", "tempo", "pace_factor", "tempo_factor")
        if pace is not None and pace > 0:
            log_h += math.log(pace)
            log_a += math.log(pace)

        # Weather (open-air football only): strong wind and extreme cold
        # disrupt passing and finishing, which suppresses total intensity.
        if not bool(ctx.get("is_dome", False)):
            wind = self._get(ctx, "wind_speed", "wind")
            temp = self._get(ctx, "temp", "temperature")
            weather_log = 0.0
            if wind is not None and wind > 0:
                # Wind drag scales with the square of the speed. Normalise by
                # a gale threshold of ~40 km/h; at that speed, ~3% of
                # intensity is lost.
                weather_log -= 0.03 * (wind / 40.0) ** 2
            if temp is not None and temp < 0:
                # Frozen pitch / cold-muscle effect, linear below freezing.
                weather_log -= 0.002 * (-temp)
            log_h += weather_log
            log_a += weather_log

        return math.exp(log_h), math.exp(log_a), informed

    def _shrink_home_edge(
        self, lam_h: float, lam_a: float, n_info: float, neutral: bool
    ) -> Tuple[float, float, float]:
        """Empirical-Bayes shrinkage of the log intensity ratio.

        The ratio is pulled toward the UEL home-edge anchor; total
        intensity (the geometric mean) is preserved.

        Returns (lam_home, lam_away, posterior_sd_of_log_ratio).
        """
        anchor = 0.0 if neutral else math.log(self.EMP_HOME_MEAN / self.EMP_AWAY_MEAN)
        log_ratio = math.log(lam_h / lam_a)
        geo = 0.5 * (math.log(lam_h) + math.log(lam_a))

        # Sampling variance of a log Poisson-rate ratio estimated from
        # n_info matches: Var ≈ (1/λh + 1/λa) / n.
        noise_var = (1.0 / lam_h + 1.0 / lam_a) / max(n_info, 1.0)
        signal_var = self.LOG_RATIO_HETEROGENEITY_SD ** 2
        k = signal_var / (signal_var + noise_var)

        shrunk = anchor + k * (log_ratio - anchor)
        post_sd = math.sqrt(k * noise_var)  # posterior SD of the log ratio
        return math.exp(geo + 0.5 * shrunk), math.exp(geo - 0.5 * shrunk), post_sd

    # ------------------------------------------------------------------ #
    # Score-lattice machinery
    # ------------------------------------------------------------------ #
    def _lattice_size(self, lam_max: float) -> int:
        """Smallest goal count n such that P(X > n) < tolerance at the
        largest intensity reached anywhere in the quadrature."""
        n = int(poisson.isf(self.LATTICE_TAIL_TOL, lam_max)) + 1
        return max(n, 8)

    @staticmethod
    def _dc_feasible_rho(rho: float, lh: float, la: float) -> float:
        """Project rho onto the region where every tau factor is >= 0."""
        lo = max(-1.0 / max(lh, 1e-12), -1.0 / max(la, 1e-12))
        hi = min(1.0 / max(lh * la, 1e-12), 1.0)
        return min(max(rho, lo), hi)

    def _score_matrix(self, lh: float, la: float, rho: float, n: int) -> np.ndarray:
        goals = np.arange(n + 1)
        ph = poisson.pmf(goals, lh)
        pa = poisson.pmf(goals, la)
        m = np.outer(ph, pa)
        r = self._dc_feasible_rho(rho, lh, la)
        m[0, 0] *= 1.0 - lh * la * r
        m[1, 0] *= 1.0 + la * r
        m[0, 1] *= 1.0 + lh * r
        m[1, 1] *= 1.0 - r
        s = m.sum()
        return m / s if s > 0 else m

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        neutral = self._is_neutral(ctx)

        lam_h, lam_a, n_groups = self._base_intensities(ctx)

        # Information content. Use explicit sample sizes when provided;
        # otherwise scale the prior by how many feature groups were present.
        n_info = self._get(ctx, "sample_size", "games_played", "n_matches")
        if n_info is None or n_info <= 0:
            n_info = self.PRIOR_INFO_MATCHES * (0.5 + 0.5 * min(n_groups, 4) / 4.0)

        lam_h, lam_a, sd_ratio = self._shrink_home_edge(lam_h, lam_a, n_info, neutral)

        # Tempo uncertainty: a shared log-scale error. Its variance is the
        # sampling variance of a log total rate.
        sd_tempo = math.sqrt(1.0 / ((lam_h + lam_a) * max(n_info, 1.0)))

        rho = self._num(ctx.get("dc_rho"))
        if rho is None:
            rho = self.DC_RHO_PRIOR

        # Gauss-Hermite integration over (tempo z1, relative strength z2).
        # The -σ²/2 terms are lognormal mean corrections, so E[λ] is unchanged.
        nodes = math.sqrt(2.0) * self._gh_x
        weights = self._gh_w / math.sqrt(math.pi)
        half_r = 0.5 * sd_ratio
        corr = 0.5 * (sd_tempo ** 2 + half_r ** 2)

        lam_max = max(lam_h, lam_a) * math.exp(
            (sd_tempo + half_r) * float(np.max(np.abs(nodes)))
        )
        n = self._lattice_size(lam_max)

        p_home = p_draw = p_away = 0.0
        tri_lower = np.tril_indices(n + 1, k=-1)  # home goals > away goals
        tri_upper = np.triu_indices(n + 1, k=1)
        for i, z1 in enumerate(nodes):
            for j, z2 in enumerate(nodes):
                w = weights[i] * weights[j]
                lh = lam_h * math.exp(sd_tempo * z1 + half_r * z2 - corr)
                la = lam_a * math.exp(sd_tempo * z1 - half_r * z2 - corr)
                m = self._score_matrix(lh, la, rho, n)
                p_home += w * float(m[tri_lower].sum())
                p_away += w * float(m[tri_upper].sum())
                p_draw += w * float(np.trace(m))

        total = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total

        try:
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        except (AttributeError, TypeError):
            return DiscreteDistribution(
                probabilities={"home": p_home, "draw": p_draw, "away": p_away}
            )

    # ------------------------------------------------------------------ #
    # Valuation helpers: continuous EV, with no discrete filters beyond
    # the minimum-odds rule.
    # ------------------------------------------------------------------ #
    @staticmethod
    def expected_value(probability: float, decimal_odds: float) -> float:
        return probability * decimal_odds - 1.0

    def is_eligible(self, probability: float, decimal_odds: float) -> bool:
        return (
            decimal_odds >= self.MIN_ELIGIBLE_ODDS
            and self.expected_value(probability, decimal_odds) > 0.0
        )