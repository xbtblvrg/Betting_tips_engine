"""
UEL | half_2 | totals : candidate model built on structural game-state mixtures.

Architecture
------------
Each side's second-half goal count is a Poisson process whose intensity carries
three multiplicative random factors:

    X_i | Z, U_i ~ Poisson(lambda_i * Z * U_i)

  * lambda_i : a log-linear situational intensity anchored on the empirical
               second-half means (home 0.80, away 0.60).
  * U_i      : an idiosyncratic Gamma(1/theta_i, theta_i) factor. It comes from
               the unobserved half-time game state (trailing teams push,
               leading teams sit). Its variance is derived analytically from
               the Skellam law of the first-half score difference, so it is
               not a free constant.
  * Z        : a shared Gamma(1/theta_s, theta_s) "open game" frailty. It
               induces positive home/away covariance, Cov = theta_s*l_h*l_a.
               It starts at the Poisson prior (dispersion index 1.000) and
               moves only as observed moments accumulate, through conjugate-
               style pseudo-count shrinkage.

Integrating U_i analytically gives conditionally negative-binomial marginals.
Integrating Z with generalized Gauss-Laguerre quadrature gives the joint score
lattice. The totals line is priced from that convolution, not from
Poisson(lambda_h + lambda_a).
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats, special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live beside the base class or be pre-loaded.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded in scope


class UELHalf2TotalsGameStateMixtureModel(BaseCandidateModel):
    """Score-state-aware bivariate Gamma-Poisson mixture for UEL second-half totals."""

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_2_1108_001423",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_2",
        family: str = "totals",
        **overrides: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Empirical second-half anchors (segment moments).
        self.mu_home: float = float(overrides.get("mu_home", 0.80))
        self.mu_away: float = float(overrides.get("mu_away", 0.60))

        # Second-half share of match goals. Stoppage time, fatigue and
        # attacking substitutions push this above one half. It is used only
        # (a) to rescale full-match inputs and (b) to infer first-half rates
        # for the half-time game-state mixture.
        self.second_half_share: float = float(overrides.get("second_half_share", 0.55))

        # Log-linear elasticities (structural priors; overridable / fit externally).
        self.beta_attack: float = float(overrides.get("beta_attack", 0.30))
        self.beta_defense: float = float(overrides.get("beta_defense", 0.30))
        self.beta_pace: float = float(overrides.get("beta_pace", 0.50))
        self.beta_rest: float = float(overrides.get("beta_rest", 0.06))
        self.beta_wind: float = float(overrides.get("beta_wind", 0.004))   # per km/h above calm
        self.beta_heat: float = float(overrides.get("beta_heat", 0.003))   # per degC off comfort

        # Game-state response: log-intensity swing for trailing (+) / leading (-).
        self.gamma_state: float = float(overrides.get("gamma_state", 0.22))
        # Two-leg aggregate response (per goal of aggregate deficit, saturating).
        self.gamma_aggregate: float = float(overrides.get("gamma_aggregate", 0.15))
        # Dead-rubber log-intensity shift (rotation, low urgency, more open play).
        self.gamma_dead_rubber: float = float(overrides.get("gamma_dead_rubber", 0.05))

        # Weight on the situational input expectation versus the anchor. This
        # is the precision ratio of the input relative to the segment prior.
        self.input_reliability: float = float(overrides.get("input_reliability", 0.65))

        # Shared-frailty prior: Poisson (theta_s = 0) with n0 pseudo-observations.
        self.theta_shared_prior: float = float(overrides.get("theta_shared_prior", 0.0))
        self.prior_pseudo_n: float = float(overrides.get("prior_pseudo_n", 200.0))
        self.observed_dispersion_index: Optional[float] = overrides.get("observed_dispersion_index")
        self.observed_covariance: Optional[float] = overrides.get("observed_covariance")
        self.observed_n: float = float(overrides.get("observed_n", 0.0))

        # Numerical controls (accuracy settings, not model clamps).
        self.quadrature_nodes: int = int(overrides.get("quadrature_nodes", 40))
        self.tail_mass: float = float(overrides.get("tail_mass", 1e-12))
        self.min_eligible_odds: float = float(overrides.get("min_eligible_odds", 1.40))

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _log_rating(self, x: Any) -> float:
        """Map a rating to log-relative scale.

        Positive values are treated as multiplicative ratings centred at 1.
        Non-positive values are treated as additive z-scores.
        """
        v = self._num(x)
        if v is None:
            return 0.0
        return math.log(v) if v > 0 else v

    def _team_block(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        block = ctx.get(side)
        att = dfn = None
        if isinstance(block, dict):
            att = block.get("attack", block.get("att"))
            dfn = block.get("defense", block.get("def", block.get("defence")))
        att = ctx.get(f"{side}_attack", att)
        dfn = ctx.get(f"{side}_defense", ctx.get(f"{side}_defence", dfn))
        return self._log_rating(att), self._log_rating(dfn)

    def _scope_expected(self, ctx: Dict[str, Any], key: str) -> Optional[float]:
        v = self._num(ctx.get(key))
        if v is None or v <= 0:
            return None
        scope = str(ctx.get("expected_scope", ctx.get("scope", "half_2"))).lower()
        if scope in ("full", "full_game", "match", "ft", "90"):
            v *= self.second_half_share
        elif scope in ("half_1", "1h", "first_half"):
            v *= self.second_half_share / (1.0 - self.second_half_share)
        return v

    # ------------------------------------------------------------------ #
    # Intensity construction
    # ------------------------------------------------------------------ #
    def _base_intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        # Anchor, then geometric shrink toward the situational expectation.
        lam = {}
        for side, mu in (("home", self.mu_home), ("away", self.mu_away)):
            ext = self._scope_expected(ctx, f"{side}_expected")
            if ext is None:
                lam[side] = mu
            else:
                w = self.input_reliability
                lam[side] = math.exp((1.0 - w) * math.log(mu) + w * math.log(ext))

        att_h, def_h = self._team_block(ctx, "home")
        att_a, def_a = self._team_block(ctx, "away")

        pace = self._log_rating(ctx.get("pace", ctx.get("tempo")))

        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        rest = 0.0
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            rest = math.log1p(rh) - math.log1p(ra)

        env = 0.0
        if not bool(ctx.get("is_dome", False)):
            wind = self._num(ctx.get("wind_speed"))
            if wind is not None and wind > 0:
                env -= self.beta_wind * wind
            temp = self._num(ctx.get("temp", ctx.get("temperature")))
            if temp is not None:
                comfort = self._num(ctx.get("comfort_temp")) or 15.0
                env -= self.beta_heat * abs(temp - comfort)

        eta_h = (
            self.beta_attack * att_h
            - self.beta_defense * def_a
            + self.beta_pace * pace
            + self.beta_rest * rest
            + env
        )
        eta_a = (
            self.beta_attack * att_a
            - self.beta_defense * def_h
            + self.beta_pace * pace
            - self.beta_rest * rest
            + env
        )

        # Competition-structure effects.
        agg = self._num(ctx.get("aggregate_diff", ctx.get("aggregate_goal_diff")))
        if agg is not None:  # home perspective entering the match
            swing = self.gamma_aggregate * math.tanh(agg)
            eta_h -= swing
            eta_a += swing
        if bool(ctx.get("dead_rubber", ctx.get("is_dead_rubber", False))):
            eta_h += self.gamma_dead_rubber
            eta_a += self.gamma_dead_rubber

        return lam["home"] * math.exp(eta_h), lam["away"] * math.exp(eta_a)

    def _game_state_adjustment(
        self, ctx: Dict[str, Any], lam_h: float, lam_a: float
    ) -> Tuple[float, float, float, float]:
        """Return (lam_h, lam_a, theta_h, theta_a).

        If the half-time score is known, apply a deterministic state shift.
        Otherwise integrate over the Skellam law of the first-half difference
        and convert the induced log-intensity variance to a Gamma-equivalent
        dispersion, theta = exp(Var[log m]) - 1. The mean is preserved by
        renormalising the mixture.
        """
        hh = self._num(ctx.get("ht_home_goals", ctx.get("home_goals_ht")))
        ha = self._num(ctx.get("ht_away_goals", ctx.get("away_goals_ht")))
        g = self.gamma_state

        if hh is not None and ha is not None:
            d = hh - ha
            s = math.tanh(d)  # saturating response to margin
            return lam_h * math.exp(-g * s), lam_a * math.exp(g * s), 0.0, 0.0

        r = (1.0 - self.second_half_share) / self.second_half_share
        f_h, f_a = lam_h * r, lam_a * r
        span = int(stats.poisson.ppf(1.0 - self.tail_mass, f_h + f_a)) + 2
        d = np.arange(-span, span + 1)
        pd = stats.skellam.pmf(d, f_h, f_a)
        pd = pd / pd.sum()
        s = np.tanh(d)

        # Mean-preserving normalisers and log-variance per side.
        mh, ma = np.exp(-g * s), np.exp(g * s)
        nh, na = float(pd @ mh), float(pd @ ma)
        lh, la = np.log(mh / nh), np.log(ma / na)
        var_h = float(pd @ (lh - pd @ lh) ** 2)
        var_a = float(pd @ (la - pd @ la) ** 2)
        return lam_h, lam_a, math.expm1(var_h), math.expm1(var_a)

    def _shared_theta(self, ctx: Dict[str, Any], lam_h: float, lam_a: float) -> float:
        """Shrink the shared-frailty variance from the Poisson prior toward observed moments."""
        n = self._num(ctx.get("observed_n")) or self.observed_n
        di = self._num(ctx.get("observed_dispersion_index", self.observed_dispersion_index))
        cov = self._num(ctx.get("observed_covariance", self.observed_covariance))

        est = None
        if n and n > 0:
            if cov is not None:
                est = cov / (lam_h * lam_a)
            elif di is not None:
                # Var(T)/E(T) - 1 = theta * (l_h + l_a) under the shared frailty.
                est = (di - 1.0) / (lam_h + lam_a)
        if est is None:
            return max(self.theta_shared_prior, 0.0)

        n0 = self.prior_pseudo_n
        theta = (n * est + n0 * self.theta_shared_prior) / (n + n0)
        return max(theta, 0.0)  # variance is non-negative by definition

    # ------------------------------------------------------------------ #
    # Joint lattice
    # ------------------------------------------------------------------ #
    @staticmethod
    def _marginal_pmf(grid: np.ndarray, mean: np.ndarray, theta: float) -> np.ndarray:
        """Return pmf rows for each conditional mean (Poisson if theta == 0)."""
        mean = np.atleast_1d(mean)[:, None]
        if theta <= 0.0:
            return stats.poisson.pmf(grid[None, :], mean)
        k = 1.0 / theta
        p = k / (k + mean)
        return stats.nbinom.pmf(grid[None, :], k, p)

    def _frailty_nodes(self, theta_s: float) -> Tuple[np.ndarray, np.ndarray]:
        if theta_s <= 0.0:
            return np.array([1.0]), np.array([1.0])
        a = 1.0 / theta_s
        u, w = special.roots_genlaguerre(self.quadrature_nodes, a - 1.0)
        w = np.asarray(w, dtype=float)
        w = w / w.sum()  # sum(w) = Gamma(a); normalising avoids overflow
        return theta_s * np.asarray(u, dtype=float), w

    def _support_cap(self, lam: float, theta_i: float, theta_s: float) -> int:
        th = theta_i + theta_s + theta_i * theta_s  # total multiplicative variance
        if th <= 0:
            return int(stats.poisson.ppf(1.0 - self.tail_mass, lam)) + 1
        k = 1.0 / th
        return int(stats.nbinom.ppf(1.0 - self.tail_mass, k, k / (k + lam))) + 1

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        lam_h, lam_a = self._base_intensities(ctx)
        lam_h, lam_a, th_h, th_a = self._game_state_adjustment(ctx, lam_h, lam_a)
        th_s = self._shared_theta(ctx, lam_h, lam_a)

        nh = self._support_cap(lam_h, th_h, th_s)
        na = self._support_cap(lam_a, th_a, th_s)
        gh, ga = np.arange(nh + 1), np.arange(na + 1)

        z, w = self._frailty_nodes(th_s)
        ph = self._marginal_pmf(gh, lam_h * z, th_h)  # (Q, nh+1)
        pa = self._marginal_pmf(ga, lam_a * z, th_a)  # (Q, na+1)
        mat = np.einsum("q,qi,qj->ij", w, ph, pa)
        return mat / mat.sum()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mat = self.score_matrix(game_context)
        scores = {
            (int(i), int(j)): float(mat[i, j])
            for i in range(mat.shape[0])
            for j in range(mat.shape[1])
            if mat[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            try:
                return DiscreteDistribution.from_scores(mat)
            except Exception:
                p_home = float(np.tril(mat, -1).sum())
                p_draw = float(np.trace(mat))
                p_away = float(np.triu(mat, 1).sum())
                return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)

    def total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        mat = self.score_matrix(game_context)
        n = mat.shape[0] + mat.shape[1] - 1
        out = np.zeros(n)
        for i in range(mat.shape[0]):
            out[i : i + mat.shape[1]] += mat[i]
        return out

    def total_line_probabilities(
        self, game_context: Dict[str, Any], line: float
    ) -> Dict[str, float]:
        """Return over/under/push probabilities for whole and half lines."""
        pmf = self.total_pmf(game_context)
        t = np.arange(pmf.size)
        return {
            "over": float(pmf[t > line].sum()),
            "under": float(pmf[t < line].sum()),
            "push": float(pmf[t == line].sum()),
        }

    def expected_value(
        self, game_context: Dict[str, Any], line: float, side: str, decimal_odds: float
    ) -> float:
        """Return EV per unit stake. Quarter lines are split into two half-stakes."""
        frac = round((line * 4) % 2)
        if frac == 1:  # quarter line, e.g. 1.25 -> {1.0, 1.5}
            lo, hi = line - 0.25, line + 0.25
            return 0.5 * (
                self.expected_value(game_context, lo, side, decimal_odds)
                + self.expected_value(game_context, hi, side, decimal_odds)
            )
        p = self.total_line_probabilities(game_context, line)
        win = p["over"] if side.lower().startswith("o") else p["under"]
        lose = p["under"] if side.lower().startswith("o") else p["over"]
        return win * (decimal_odds - 1.0) - lose

    def is_eligible(
        self, game_context: Dict[str, Any], line: float, side: str, decimal_odds: float
    ) -> bool:
        return decimal_odds >= self.min_eligible_odds and self.expected_value(
            game_context, line, side, decimal_odds
        ) > 0.0