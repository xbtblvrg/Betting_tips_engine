"""
Candidate model for NHL | period_1 | exact_total.

Approach
--------
First-period goals are close to a pure Poisson process. There is no empty-net
regime, there are few score-effect distortions, and the observed baseline
dispersion index is 1.000. What actually misprices the tails is uncertainty
in the scoring rate, not intrinsic overdispersion. The model is built in five
steps:

1. Rate construction (log-linear):
       log(lam_side) = log(base_side) + scale-normalised input signal
                       + matchup (attack x opposing defence) + pace + rest
   All terms are normalised against the empirical first-period baseline
   (home 1.02, away 0.92). Full-game inputs are detected by magnitude and
   mapped to period 1 through the period goal share.

2. Bayesian shrinkage on the log scale:
   The situational log-rate is a noisy measurement of the true rate. It is
   precision-weighted toward the empirical baseline, and the posterior
   variance is kept as the parameter uncertainty.

3. Shared tempo/rate uncertainty:
   A single Gamma multiplier G (mean 1, variance tau^2) scales both teams'
   rates, because pace and officiating affect both sides of the same period.
   tau^2 combines:
     - intrinsic overdispersion, recovered from the dispersion index
       (DI = 1 + tau^2 * mu)
     - posterior log-rate variance from step 2 (Var[e^X] approx s^2 for small s)
   Integrating G gives a closed-form negative-multinomial joint score pmf.
   Its marginal total is negative binomial, so the tails widen only by what
   the data and the uncertainty justify.

4. Exact score lattice:
   The lattice is truncated adaptively at a tail mass below 1e-12 and then
   renormalised. It is emitted through DiscreteDistribution.from_scores, so
   the engine can derive the exact_total probabilities.

5. Overrides:
   Every structural quantity can be overridden from game_context (period
   share, dispersion index, prior / measurement variances, elasticities), so
   none of them is a hard clamp.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import gammaln

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally available in the execution scope.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: F821
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except Exception:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class NHLPeriod1ExactTotalModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Empirical anchors (observed first-period moments for this segment)
    # ------------------------------------------------------------------ #
    BASE_HOME_P1 = 1.02
    BASE_AWAY_P1 = 0.92
    BASE_DISPERSION_INDEX = 1.000

    # ------------------------------------------------------------------ #
    # Domain structure. These are default priors, all overridable from
    # game_context.
    # ------------------------------------------------------------------ #
    # Share of regulation goals scored in period 1. P1 is the lowest-scoring
    # period; P3 carries the empty-net goals.
    PERIOD1_GOAL_SHARE = 0.31
    # Prior SD of the true period log-rate around the league baseline:
    # team-quality spread in the NHL is roughly +/-20% on scoring rates.
    PRIOR_LOG_SD = 0.20
    # Measurement SD of a situational log-rate estimate: pre-game expected
    # goals carry roughly 15% relative error.
    MEASUREMENT_LOG_SD = 0.15
    # Elasticity of the log-rate to log rating ratios.
    MATCHUP_ELASTICITY = 0.5
    PACE_ELASTICITY = 1.0
    # Rest effect: log-rate change per rest-day differential, saturating
    # because fatigue matters mainly on back-to-backs.
    REST_LOG_EFFECT = 0.03
    REST_SATURATION_DAYS = 2.0

    TAIL_EPS = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_nhl_period_1_1107_132257",
        version: str = "1.0.0",
        league: str = "NHL",
        scope: str = "period_1",
        family: str = "exact_total",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
                **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

    # ------------------------------------------------------------------ #
    # Parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v):
            return None
        return v

    def _get(self, ctx: Dict[str, Any], key: str, default: float) -> float:
        v = self._num(ctx.get(key))
        return default if v is None else v

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Read a side's attack/defence rating from flat or nested keys."""
        candidates = [
            ctx.get(f"{side}_{kind}"),
            ctx.get(f"{side}_{kind}_rating"),
            ctx.get(f"{side}_{kind}_strength"),
        ]
        nested = ctx.get(side)
        if isinstance(nested, dict):
            candidates += [
                nested.get(kind),
                nested.get(f"{kind}_rating"),
                nested.get(f"{kind}_strength"),
            ]
        for c in candidates:
            v = self._num(c)
            if v is not None:
                return v
        return None

    @staticmethod
    def _rating_to_log_ratio(v: Optional[float]) -> float:
        """
        Map a rating onto a log-multiplier around neutral.

        Ratings arrive either as multiplicative indices centred near 1.0
        (strictly positive) or as additive z-scores centred near 0. If a value
        is positive and not too far from 1, it is treated as a ratio; any
        other value is treated as an additive log-score.
        """
        if v is None:
            return 0.0
        if 0.0 < v and abs(math.log(v)) < 1.5:
            return math.log(v)
        return float(np.tanh(v / 3.0) * 1.5)  # bounded additive score

    # ------------------------------------------------------------------ #
    # Rate construction
    # ------------------------------------------------------------------ #
    def _scale_inputs(
        self, h: Optional[float], a: Optional[float], share: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Detect whether the expected-goals inputs are full-game or period-1.

        The full-game equivalent of the P1 baseline is base / share. The input
        total is compared to both scales on the log axis, and the closer scale
        wins.
        """
        if h is None or a is None or h <= 0 or a <= 0:
            return h, a
        tot = h + a
        p1_ref = self.BASE_HOME_P1 + self.BASE_AWAY_P1
        fg_ref = p1_ref / share
        if abs(math.log(tot / fg_ref)) < abs(math.log(tot / p1_ref)):
            return h * share, a * share
        return h, a

    def _log_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        share = self._get(ctx, "period_share", self.PERIOD1_GOAL_SHARE)
        share = share if 0.0 < share < 1.0 else self.PERIOD1_GOAL_SHARE

        h_in, a_in = self._scale_inputs(
            self._num(ctx.get("home_expected")),
            self._num(ctx.get("away_expected")),
            share,
        )

        base_h = math.log(self.BASE_HOME_P1)
        base_a = math.log(self.BASE_AWAY_P1)

        # Situational signal on the log scale, relative to the baseline.
        sig_h = math.log(h_in) - base_h if (h_in and h_in > 0) else 0.0
        sig_a = math.log(a_in) - base_a if (a_in and a_in > 0) else 0.0
        has_xg = h_in is not None and a_in is not None and h_in > 0 and a_in > 0

        # Matchup: own attack against the opponent's defence. A higher defence
        # rating suppresses goals.
        k_m = self._get(ctx, "matchup_elasticity", self.MATCHUP_ELASTICITY)
        ha = self._rating_to_log_ratio(self._rating(ctx, "home", "attack"))
        hd = self._rating_to_log_ratio(self._rating(ctx, "home", "defense"))
        aa = self._rating_to_log_ratio(self._rating(ctx, "away", "attack"))
        ad = self._rating_to_log_ratio(self._rating(ctx, "away", "defense"))
        match_h = k_m * (ha - ad)
        match_a = k_m * (aa - hd)
        # If expected goals were supplied, they already embed the matchup, so
        # only half the rating signal is added to avoid double counting.
        if has_xg:
            match_h *= 0.5
            match_a *= 0.5

        # Pace / tempo: a shared multiplicative factor around 1.
        pace = self._num(ctx.get("pace") or ctx.get("tempo"))
        pace_log = 0.0
        if pace is not None and pace > 0:
            ref = self._get(ctx, "pace_reference", 1.0 if pace < 5 else pace)
            if ref > 0:
                pace_log = self._get(ctx, "pace_elasticity", self.PACE_ELASTICITY) * math.log(
                    pace / ref
                )

        # Rest: fatigued teams concede more and score less. The effect
        # saturates through tanh.
        rest_eff = self._get(ctx, "rest_log_effect", self.REST_LOG_EFFECT)
        sat = self.REST_SATURATION_DAYS
        hr = self._num(ctx.get("home_rest_days"))
        ar = self._num(ctx.get("away_rest_days"))
        rest_h = rest_a = 0.0
        if hr is not None and ar is not None:
            diff = math.tanh((hr - ar) / sat)
            rest_h = rest_eff * diff
            rest_a = -rest_eff * diff

        obs_h = sig_h + match_h + pace_log + rest_h
        obs_a = sig_a + match_a + pace_log + rest_a

        # Precision-weighted shrinkage toward the empirical baseline.
        pv = self._get(ctx, "prior_log_var", self.PRIOR_LOG_SD ** 2)
        mv = self._get(ctx, "measurement_log_var", self.MEASUREMENT_LOG_SD ** 2)
        n_signals = sum(
            1
            for v in (has_xg, any(x != 0 for x in (ha, hd, aa, ad)), pace_log != 0, rest_h != 0)
            if v
        )
        if n_signals == 0:
            post_var = pv
            w = 0.0
        else:
            # Independent signals add precision, with diminishing returns
            # because they are correlated: effective count = sqrt(n).
            eff_mv = mv / math.sqrt(n_signals)
            w = pv / (pv + eff_mv)
            post_var = 1.0 / (1.0 / pv + 1.0 / eff_mv)

        # Posterior log-mean. Subtract half the variance so that E[rate]
        # stays unbiased under the lognormal-to-gamma mapping.
        mu_h = base_h + w * obs_h - 0.5 * post_var
        mu_a = base_a + w * obs_a - 0.5 * post_var
        return mu_h, mu_a, post_var

    # ------------------------------------------------------------------ #
    # Joint score lattice: negative multinomial (Poisson x shared Gamma)
    # ------------------------------------------------------------------ #
    def _score_matrix(self, lam_h: float, lam_a: float, tau2: float) -> np.ndarray:
        mu = lam_h + lam_a
        # Adaptive truncation: grow until the total-goal tail is negligible.
        var_t = mu + tau2 * mu * mu
        n_max = int(math.ceil(mu + 12.0 * math.sqrt(var_t) + 6))

        h = np.arange(n_max + 1)[:, None].astype(float)
        a = np.arange(n_max + 1)[None, :].astype(float)

        if tau2 < 1e-10:
            logp = (
                h * math.log(lam_h) - lam_h - gammaln(h + 1)
                + a * math.log(lam_a) - lam_a - gammaln(a + 1)
            )
        else:
            k = 1.0 / tau2
            th = tau2
            denom = math.log1p(th * mu)
            logp = (
                gammaln(k + h + a) - gammaln(k) - gammaln(h + 1) - gammaln(a + 1)
                + h * math.log(th * lam_h) + a * math.log(th * lam_a)
                - (k + h + a) * denom
            )
        P = np.exp(logp - logp.max())
        P /= P.sum()

        # Trim to the smallest square that keeps 1 - TAIL_EPS of the mass.
        tot = np.add.outer(np.arange(n_max + 1), np.arange(n_max + 1))
        cum = np.array([P[tot <= t].sum() for t in range(2 * n_max + 1)])
        t_keep = int(np.searchsorted(cum, 1.0 - self.TAIL_EPS)) + 1
        m = min(n_max, max(t_keep, 1))
        P = P[: m + 1, : m + 1]
        return P / P.sum()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        mu_h, mu_a, post_var = self._log_rates(ctx)
        lam_h = math.exp(mu_h + 0.5 * post_var)
        lam_a = math.exp(mu_a + 0.5 * post_var)

        # Intrinsic overdispersion from the dispersion index:
        # DI = 1 + tau^2 * mu.
        di = self._get(ctx, "dispersion_index", self.BASE_DISPERSION_INDEX)
        mu_base = self.BASE_HOME_P1 + self.BASE_AWAY_P1
        tau2_intrinsic = max(di - 1.0, 0.0) / mu_base

        # Parameter uncertainty of a lognormal multiplier mapped to the gamma
        # variance: Var[e^X] = e^{s^2} - 1.
        tau2_param = math.expm1(post_var)

        tau2 = tau2_intrinsic + tau2_param

        P = self._score_matrix(lam_h, lam_a, tau2)

        scores: Dict[Tuple[int, int], float] = {}
        for i in range(P.shape[0]):
            for j in range(P.shape[1]):
                p = float(P[i, j])
                if p > 0.0:
                    scores[(i, j)] = p

        dist = DiscreteDistribution.from_scores(scores)

        # Attach diagnostics where the distribution object allows it.
        try:
            dist.metadata = {  # type: ignore[attr-defined]
                "lambda_home": lam_h,
                "lambda_away": lam_a,
                "expected_total": lam_h + lam_a,
                "tau2": tau2,
                "tau2_intrinsic": tau2_intrinsic,
                "tau2_parameter": tau2_param,
                "model_id": self.model_id,
                "version": self.version,
            }
        except Exception:
            pass
        return dist


# Alias for loaders that look up a conventional class name.
CandidateModel = NHLPeriod1ExactTotalModel