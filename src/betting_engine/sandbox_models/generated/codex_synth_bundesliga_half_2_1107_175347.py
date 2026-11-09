"""
Candidate model: BUNDESLIGA | half_2 | three_way

Second-half 1X2 is settled on goals scored in the second half only. The model is
built from football scoring mechanics:

1. Goal arrivals per team are counting processes with intensities lambda_h and
   lambda_a. These are anchored to the empirical second-half means
   (home 0.80, away 0.60) and adjusted in log space by situational evidence.
2. Several noisy evidence sources are combined by precision-weighted log-linear
   pooling: the supplied expectations, the attack/defense-implied rates, and the
   empirical baseline prior.
3. Uncertainty in the intensity estimate is integrated out, not ignored. The
   integration uses Gauss-Hermite quadrature over a correlated lognormal mixing
   law with two components:
   - A shared tempo factor, which moves both rates together.
   - A differential strength factor, which moves the margin.
   Its variance comes from the observed dispersion index (Var/Mean). The
   disagreement between evidence sources adds to it. A dispersion index of
   1.000 combined with full agreement between sources reduces to pure Poisson.
4. Low-score dependence (0-0, 1-0, 0-1, 1-1) is handled with a Dixon-Coles
   correction. Its rho is kept inside the analytic validity region for the
   current intensities, so the correction never produces negative mass.
5. Second-half dynamics that are specific to this scope:
   - Differential fatigue from rest days, which acts more strongly late in
     matches.
   - Optional live game state from the first-half score. The trailing team
     raises its attacking intensity. The leading team lowers its own and also
     concedes more as the game opens up.
   Both effects saturate smoothly through tanh, with no hard clamps.
6. The score lattice is truncated adaptively, at the Poisson quantile where the
   neglected tail mass falls below machine-relevant tolerance. No fixed
   max-goals constant is used.

Bookmaker prices are never used as inputs.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

try:  # Preferred import path
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - sandbox may preload into scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")


class BundesligaHalf2ThreeWayModel(BaseCandidateModel):
    """Precision-pooled, uncertainty-integrated Dixon-Coles model for 2H 1X2."""

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_half_2_1107_175347",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "half_2",
        family: str = "three_way",
        # Empirical anchors (observed segment moments)
        baseline_home: float = 0.80,
        baseline_away: float = 0.60,
        dispersion_index: float = 1.000,
        # Structural priors (all overridable via game_context)
        second_half_share: float = 0.555,     # share of match goals scored after HT
        low_score_rho: float = -0.06,          # Dixon-Coles dependence prior
        rest_elasticity: float = 0.035,        # log-rate per log-rest-ratio
        state_elasticity: float = 0.12,        # log-rate per unit (saturated) deficit
        quadrature_nodes: int = 9,
        tail_tolerance: float = 1e-12,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.baseline_home = float(baseline_home)
        self.baseline_away = float(baseline_away)
        self.dispersion_index = float(dispersion_index)
        self.second_half_share = float(second_half_share)
        self.low_score_rho = float(low_score_rho)
        self.rest_elasticity = float(rest_elasticity)
        self.state_elasticity = float(state_elasticity)
        self.quadrature_nodes = int(quadrature_nodes)
        self.tail_tolerance = float(tail_tolerance)

        nodes, weights = np.polynomial.hermite.hermgauss(self.quadrature_nodes)
        # Convert physicists' Hermite to standard-normal expectation
        self._gh_z = nodes * math.sqrt(2.0)
        self._gh_w = weights / math.sqrt(math.pi)

    # ------------------------------------------------------------------ #
    # Feature extraction helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
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
        """Accept nested {'home': {'attack': x}} or flat 'home_attack' layouts."""
        nested = ctx.get(side)
        if isinstance(nested, dict):
            for k in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = self._num(nested.get(k))
                if v is not None:
                    return v
        return self._get(ctx, f"{side}_{kind}", f"{side}_{kind}_rating", f"{side}_{kind}_strength")

    @staticmethod
    def _as_log_multiplier(r: Optional[float]) -> Optional[float]:
        """
        Ratings can arrive as multiplicative indices (centred at 1) or as
        additive log-scale offsets (centred at 0). Values that are strictly
        positive and away from zero are treated as multipliers. All other
        values are treated as log offsets.
        """
        if r is None:
            return None
        if r > 0.2:
            return math.log(r)
        return r

    # ------------------------------------------------------------------ #
    # Intensity construction
    # ------------------------------------------------------------------ #
    def _scale_to_half(self, h: float, a: float, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Map supplied expectations onto second-half scale if they are full-match."""
        share = self._get(ctx, "second_half_share") or self.second_half_share
        declared = str(ctx.get("expected_scope", "")).lower()
        if declared in ("full_game", "full", "match", "ft"):
            return h * share, a * share
        if declared in ("half_2", "second_half", "2h"):
            return h, a
        # Undeclared: choose the scale that is closer, in log distance, to the
        # empirical half-2 anchor.
        base_total = self.baseline_home + self.baseline_away
        tot = h + a
        if tot <= 0:
            return h, a
        d_half = abs(math.log(tot / base_total))
        d_full = abs(math.log(tot * share / base_total))
        return (h * share, a * share) if d_full < d_half else (h, a)

    def _pooled_log_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        """
        Precision-weighted pooling in log space.

        Returns (log_lambda_home, log_lambda_away, disagreement_variance).
        """
        lb_h, lb_a = math.log(self.baseline_home), math.log(self.baseline_away)

        # Source variances, set in log units. Smaller means more trusted.
        # The baseline variance reflects cross-match heterogeneity: when no
        # match-specific evidence exists, the anchor dominates.
        var_base = 0.20
        var_ctx = 0.04
        var_feat = 0.09

        est_h = [(lb_h, 1.0 / var_base)]
        est_a = [(lb_a, 1.0 / var_base)]

        he = self._get(ctx, "half_2_home_expected", "home_expected")
        ae = self._get(ctx, "half_2_away_expected", "away_expected")
        if he is not None and ae is not None and he > 0 and ae > 0:
            if "half_2_home_expected" in ctx:
                sh, sa = he, ae
            else:
                sh, sa = self._scale_to_half(he, ae, ctx)
            est_h.append((math.log(sh), 1.0 / var_ctx))
            est_a.append((math.log(sa), 1.0 / var_ctx))

        ha = self._as_log_multiplier(self._team_rating(ctx, "home", "attack"))
        hd = self._as_log_multiplier(self._team_rating(ctx, "home", "defense"))
        aa = self._as_log_multiplier(self._team_rating(ctx, "away", "attack"))
        ad = self._as_log_multiplier(self._team_rating(ctx, "away", "defense"))
        if ha is not None and ad is not None:
            est_h.append((lb_h + ha + ad, 1.0 / var_feat))
        if aa is not None and hd is not None:
            est_a.append((lb_a + aa + hd, 1.0 / var_feat))

        def pool(est):
            w = np.array([p for _, p in est])
            x = np.array([m for m, _ in est])
            mu = float(np.dot(w, x) / w.sum())
            # Weighted between-source dispersion. Disagreement means uncertainty.
            dis = float(np.dot(w, (x - mu) ** 2) / w.sum()) if len(est) > 1 else 0.0
            post_var = 1.0 / w.sum()
            return mu, dis, post_var

        mh, dh, vh = pool(est_h)
        ma, da, va = pool(est_a)
        disagreement = 0.5 * (dh + da) + 0.5 * (vh + va) * (len(est_h) == 1 and len(est_a) == 1)
        return mh, ma, disagreement

    def _situational_shift(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Log-rate shifts from second-half-specific mechanics."""
        dh = da = 0.0

        # Fatigue: rest asymmetry matters most late in matches.
        rh = self._get(ctx, "home_rest_days")
        ra = self._get(ctx, "away_rest_days")
        rest_el = self._get(ctx, "rest_elasticity") or self.rest_elasticity
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            x = math.tanh(math.log((1.0 + rh) / (1.0 + ra)))
            dh += rest_el * x
            da -= rest_el * x

        # Tempo / pace index (centred at 1 or 0). Shared multiplier.
        pace = self._get(ctx, "tempo", "pace", "pace_index")
        lp = self._as_log_multiplier(pace)
        if lp is not None:
            dh += lp
            da += lp

        # Weather: strong wind and cold degrade open-play finishing modestly.
        # Indoor venues neutralise the effect.
        is_dome = bool(ctx.get("is_dome", False))
        if not is_dome:
            wind = self._get(ctx, "wind_speed")
            temp = self._get(ctx, "temp", "temperature")
            w_eff = 0.0
            if wind is not None and wind > 0:
                w_eff -= 0.02 * math.tanh(wind / 25.0)
            if temp is not None:
                w_eff -= 0.015 * math.tanh(max(0.0, 0.0 - temp) / 10.0)
            dh += w_eff
            da += w_eff

        # Live game state at half-time, if supplied.
        h1h = self._get(ctx, "half_1_home_score", "ht_home_score", "first_half_home_goals")
        h1a = self._get(ctx, "half_1_away_score", "ht_away_score", "first_half_away_goals")
        st_el = self._get(ctx, "state_elasticity") or self.state_elasticity
        if h1h is not None and h1a is not None:
            s = math.tanh((h1h - h1a) / 1.5)  # +: home leads, saturating
            # Trailing team pushes. Leading team sits deeper but is exposed.
            dh += st_el * (-s) + 0.5 * st_el * abs(s) * (s > 0) * 0.0
            da += st_el * s
            # Open-game effect: a non-level score raises total intensity slightly.
            dh += 0.25 * st_el * abs(s)
            da += 0.25 * st_el * abs(s)

        return dh, da

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    def _max_goals(self, lam: float) -> int:
        return int(stats.poisson.isf(self.tail_tolerance, lam)) + 2

    @staticmethod
    def _dc_rho_bounds(lh: float, la: float) -> Tuple[float, float]:
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)
        return lo, hi

    def _outcome_probs(self, lh: float, la: float, rho: float) -> np.ndarray:
        n_h, n_a = self._max_goals(lh), self._max_goals(la)
        ph = stats.poisson.pmf(np.arange(n_h + 1), lh)
        pa = stats.poisson.pmf(np.arange(n_a + 1), la)
        grid = np.outer(ph, pa)

        lo, hi = self._dc_rho_bounds(lh, la)
        # Smooth projection into the validity interval, with no hard clip at the edges.
        mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
        r = mid + half * math.tanh((rho - mid) / half) * 0.98
        grid[0, 0] *= 1.0 - lh * la * r
        grid[0, 1] *= 1.0 + lh * r
        grid[1, 0] *= 1.0 + la * r
        grid[1, 1] *= 1.0 - r
        grid /= grid.sum()

        p_home = float(np.tril(grid, -1).sum())
        p_draw = float(np.trace(grid))
        p_away = float(np.triu(grid, 1).sum())
        return np.array([p_home, p_draw, p_away])

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        mh, ma, disagreement = self._pooled_log_rates(ctx)
        sh, sa = self._situational_shift(ctx)
        mh += sh
        ma += sa

        # Mixing variance implied by the observed dispersion index:
        # Var/Mean = 1 + mean * (exp(s^2) - 1)  =>  s^2 = log(1 + (D - 1) / mean)
        D = self._get(ctx, "dispersion_index") or self.dispersion_index
        mean_total = math.exp(mh) + math.exp(ma)
        s2_disp = math.log1p(max(D - 1.0, 0.0) / mean_total) if D > 1.0 else 0.0

        # Split the variance into a shared tempo factor (from dispersion) and a
        # differential strength factor (from evidence disagreement).
        s_tempo = math.sqrt(s2_disp)
        s_diff = math.sqrt(max(disagreement, 0.0))

        rho = self._get(ctx, "low_score_rho") or self.low_score_rho

        # Lognormal mixing with mean preservation: E[exp(sZ - s^2/2)] = 1.
        out = np.zeros(3)
        for zt, wt in zip(self._gh_z, self._gh_w):
            for zd, wd in zip(self._gh_z, self._gh_w):
                lt = s_tempo * zt - 0.5 * s_tempo ** 2
                ldh = s_diff * zd / math.sqrt(2.0) - 0.25 * s_diff ** 2
                lda = -s_diff * zd / math.sqrt(2.0) - 0.25 * s_diff ** 2
                lh = math.exp(mh + lt + ldh)
                la = math.exp(ma + lt + lda)
                out += wt * wd * self._outcome_probs(lh, la, rho)
                if s_diff == 0.0:
                    break
            if s_tempo == 0.0:
                if s_diff == 0.0:
                    break

        # Normalise to remove quadrature weights consumed by early breaks.
        total = out.sum()
        if not math.isfinite(total) or total <= 0:
            out = self._outcome_probs(math.exp(mh), math.exp(ma), rho)
            total = out.sum()
        out /= total

        p_home, p_draw, p_away = (float(x) for x in out)
        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )


# Conventional alias for engine discovery
CandidateModel = BundesligaHalf2ThreeWayModel