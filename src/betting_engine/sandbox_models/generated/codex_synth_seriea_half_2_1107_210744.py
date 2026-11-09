"""
Candidate model: SERIEA | half_2 | both_score

Second-half "both teams to score". The model builds a full second-half score
lattice so the engine can settle BTTS and any derived market from the same
distribution.

Structure, derived from the scoring process:

1.  Goals in a 45-minute segment come from a thinned point process, so each
    side's count is conditionally Poisson with segment intensity lambda_i.
2.  Intensities are anchored to the SERIEA second-half empirical means
    (home 0.80, away 0.60). Situational inputs act multiplicatively in log
    space: attack versus opposing defence, tempo, and second-half fatigue
    from rest asymmetry.
3.  Cold start: we have no settled sample, so each situational signal is
    shrunk toward the anchor with a precision weight. The weight equals the
    information carried by the input relative to the prior. With no data the
    anchor dominates; with strong inputs the inputs dominate.
4.  Dependence and dispersion come from a shared multiplicative match-tempo
    frailty Z ~ Gamma(k, 1/k), so E[Z] = 1. The observed dispersion index of
    total goals is D = 1 + mu_tot / k, which gives k = mu_tot / (D - 1).
    - When D = 1, k is infinite and the model reduces to independent Poisson.
    - When D > 1, the frailty adds both overdispersion and the positive
      cross-team correlation seen in open, end-to-end second halves.
    The frailty is integrated out exactly by generalised Gauss-Laguerre
    quadrature.
5.  The lattice is truncated adaptively. It grows until the residual tail mass
    falls below numerical tolerance, so no fixed goal cap is imposed.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import gammaln, roots_genlaguerre

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # pre-loaded in execution scope; import defensively for static tooling
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class SerieAHalf2BothScoreModel(BaseCandidateModel):
    # Empirical SERIEA second-half anchors (segment autopsy).
    ANCHOR_HOME = 0.80
    ANCHOR_AWAY = 0.60
    ANCHOR_DISPERSION = 1.0

    # Share of full-match goals scored after half time in top-flight football.
    # Used only when inputs are detected to be full-match expectations.
    HALF2_SHARE = (ANCHOR_HOME + ANCHOR_AWAY) / ((ANCHOR_HOME + ANCHOR_AWAY) / 0.555)

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_2_1107_210744",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_2",
        family: str = "both_score",
        prior_precision: float = 1.0,
        fatigue_elasticity: float = 0.04,
        tail_tolerance: float = 1e-10,
        quadrature_nodes: int = 32,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Prior precision: pseudo-observations backing the anchor (cold start = 1).
        self.prior_precision = float(prior_precision)
        # Log-rate response per log rest-day ratio. Second-half intensity is the
        # part of the match most sensitive to accumulated fatigue.
        self.fatigue_elasticity = float(fatigue_elasticity)
        self.tail_tolerance = float(tail_tolerance)
        self.quadrature_nodes = int(quadrature_nodes)

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._num(ctx[k])
                if v is not None:
                    return v
        return None

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        nested = ctx.get(side)
        if isinstance(nested, dict):
            for k in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = self._num(nested.get(k))
                if v is not None and v > 0:
                    return v
        v = self._get(ctx, f"{side}_{kind}", f"{side}_{kind}_rating", f"{side}_{kind}_strength")
        return v if v is not None and v > 0 else None

    # --------------------------------------------------------- intensities
    def _segment_scale(self, ctx: Dict[str, Any], h: float, a: float) -> float:
        """Map supplied expectations onto a 45-minute second-half basis.

        If the context declares the scope, use it. Otherwise choose whichever
        interpretation (segment or full match) lies closer to the anchor in
        log space.
        """
        declared = str(ctx.get("expected_scope", ctx.get("expected_basis", ""))).lower()
        if declared in ("full", "full_game", "match", "ft", "game"):
            return self.HALF2_SHARE
        if declared in ("half_2", "h2", "second_half", "segment"):
            return 1.0
        total = h + a
        anchor = self.ANCHOR_HOME + self.ANCHOR_AWAY
        if total <= 0:
            return 1.0
        d_seg = abs(math.log(total / anchor))
        d_full = abs(math.log(total * self.HALF2_SHARE / anchor))
        return self.HALF2_SHARE if d_full < d_seg else 1.0

    def _shrink(self, log_signal: float, info: float) -> float:
        """Precision-weighted posterior of a log multiplier under a 0-centred prior."""
        w = info / (info + self.prior_precision)
        return w * log_signal

    def _intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        log_h = math.log(self.ANCHOR_HOME)
        log_a = math.log(self.ANCHOR_AWAY)

        # (a) Market-independent expected-goals inputs.
        h_in = self._get(ctx, "home_expected", "home_xg", "home_lambda")
        a_in = self._get(ctx, "away_expected", "away_xg", "away_lambda")
        if h_in is not None and a_in is not None and h_in > 0 and a_in > 0:
            s = self._segment_scale(ctx, h_in, a_in)
            h_seg, a_seg = h_in * s, a_in * s
            # Information in a rate estimate is proportional to its Poisson
            # Fisher information, i.e. the expected count itself.
            sample_n = self._get(ctx, "sample_size", "games_observed")
            info_scale = max(sample_n, 0.0) if sample_n is not None else 1.0
            log_h += self._shrink(math.log(h_seg / self.ANCHOR_HOME), info_scale * (1.0 + h_seg))
            log_a += self._shrink(math.log(a_seg / self.ANCHOR_AWAY), info_scale * (1.0 + a_seg))

        # (b) Attack/defence exposure (ratings centred on 1 = league average).
        ha, hd = self._team_rating(ctx, "home", "attack"), self._team_rating(ctx, "home", "defense")
        aa, ad = self._team_rating(ctx, "away", "attack"), self._team_rating(ctx, "away", "defense")
        if h_in is None or a_in is None:  # avoid double counting if xG already embeds ratings
            if ha is not None and ad is not None:
                log_h += self._shrink(math.log(ha) + math.log(ad), 1.0)
            if aa is not None and hd is not None:
                log_a += self._shrink(math.log(aa) + math.log(hd), 1.0)

        # (c) Tempo: a common multiplier shared by both teams.
        pace = self._get(ctx, "pace", "tempo", "pace_factor", "tempo_factor")
        if pace is not None and pace > 0:
            lp = self._shrink(math.log(pace), 1.0)
            log_h += lp
            log_a += lp

        # (d) Second-half fatigue. The more rested side gains intensity late on.
        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        if hr is not None and ar is not None and hr >= 0 and ar >= 0:
            r = math.log((hr + 1.0) / (ar + 1.0))
            log_h += self.fatigue_elasticity * r
            log_a -= self.fatigue_elasticity * r

        # (e) Weather. Strong wind degrades finishing and passing for both
        #     sides; a dome removes the effect.
        if not bool(ctx.get("is_dome", False)):
            wind = self._get(ctx, "wind_speed")
            if wind is not None and wind > 0:
                # Smooth saturating penalty scaled by a gale-force reference (~50 km/h).
                lw = -math.log1p((wind / 50.0) ** 2) * 0.1
                log_h += lw
                log_a += lw

        return math.exp(log_h), math.exp(log_a)

    # ------------------------------------------------------------ lattice
    def _dispersion(self, ctx: Dict[str, Any]) -> float:
        d = self._get(ctx, "dispersion_index", "total_dispersion")
        return d if d is not None and d > 0 else self.ANCHOR_DISPERSION

    def _support(self, lam_max: float) -> int:
        n = int(math.ceil(lam_max + 1))
        while True:
            k = np.arange(n + 1)
            cdf = np.exp(k * math.log(lam_max) - lam_max - gammaln(k + 1)).sum()
            if 1.0 - cdf < self.tail_tolerance:
                return n
            n += 1

    def _poisson_pmf(self, lam: float, n: int) -> np.ndarray:
        k = np.arange(n + 1)
        return np.exp(k * math.log(lam) - lam - gammaln(k + 1))

    def _score_matrix(self, lam_h: float, lam_a: float, dispersion: float) -> np.ndarray:
        mu_tot = lam_h + lam_a
        excess = dispersion - 1.0
        if excess <= self.tail_tolerance:
            n = self._support(max(lam_h, lam_a))
            M = np.outer(self._poisson_pmf(lam_h, n), self._poisson_pmf(lam_a, n))
            return M / M.sum()

        # Shared gamma frailty: Var/Mean = 1 + mu_tot / k  =>  k = mu_tot / excess.
        k = mu_tot / excess
        x, w = roots_genlaguerre(self.quadrature_nodes, k - 1.0)
        z = x / k
        w = w / math.exp(gammaln(k))
        w = w / w.sum()
        n = self._support(max(lam_h, lam_a) * float(z.max()))
        M = np.zeros((n + 1, n + 1))
        for zi, wi in zip(z, w):
            if wi <= 0 or zi <= 0:
                continue
            M += wi * np.outer(self._poisson_pmf(lam_h * zi, n), self._poisson_pmf(lam_a * zi, n))
        return M / M.sum()

    # ------------------------------------------------------------- public
    def predict_distribution(self, game_context: Dict[str, Any]):
        ctx = game_context or {}
        lam_h, lam_a = self._intensities(ctx)
        M = self._score_matrix(lam_h, lam_a, self._dispersion(ctx))

        scores = {
            (i, j): float(M[i, j])
            for i in range(M.shape[0])
            for j in range(M.shape[1])
            if M[i, j] > 0.0
        }

        # Diagnostics retained for auditing and calibration tracking.
        p_btts = float(1.0 - M[0, :].sum() - M[:, 0].sum() + M[0, 0])
        self.last_diagnostics = {
            "lambda_home_h2": lam_h,
            "lambda_away_h2": lam_a,
            "p_both_score": p_btts,
            "p_home_win_h2": float(np.tril(M, -1).sum()),
            "p_draw_h2": float(np.trace(M)),
            "p_away_win_h2": float(np.triu(M, 1).sum()),
        }

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            try:
                return DiscreteDistribution.from_scores(M)
            except Exception:
                return DiscreteDistribution(probabilities={"yes": p_btts, "no": 1.0 - p_btts})


CandidateModel = SerieAHalf2BothScoreModel