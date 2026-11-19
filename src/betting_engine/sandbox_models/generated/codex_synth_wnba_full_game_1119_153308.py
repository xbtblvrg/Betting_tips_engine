"""
Candidate model for NBA | half_1 | home_total.

Design notes
------------
1. Reading the "dispersion index" of 0.187.
   Var/Mean = 0.187 at a mean of 56 implies a standard deviation of about 3.2
   points. Realized NBA first-half team scoring is much more variable than that
   (its SD is typically around 7 to 8 points). The 0.187 figure most likely
   measures the spread of *expected* values across games, not realized outcomes.
   If this model priced lines with a 3.2-point SD, it would be heavily
   overconfident and would reproduce the "uncalibrated tail" failures listed
   in the deficits.

   For that reason, outcome variance here comes from possession mechanics
   (a compound sum). The observed index can still be imposed through
   `dispersion_override` if downstream validation shows it describes realized
   scores. It is not hard-wired.

2. Structure.
   S_home = sum_{i=1}^{N} Y_i, where N is the number of first-half possessions
   (driven by pace and shared by both teams) and Y_i is points per possession.
       E[S]   = E[N] * theta
       Var[S] = E[N] * Var(Y) + theta^2 * Var(N)
   Both teams share the same N, which induces positive home/away covariance:
       Cov(S_h, S_a) = theta_h * theta_a * Var(N)

3. Output.
   A joint integer score lattice built from a discretized bivariate Gaussian
   (CDF differences with half-point continuity correction). The lattice is
   renormalized to sum to exactly 1 and fully vectorized, so latency stays
   well under 250 ms.

4. Shrinkage.
   The projected mean is shrunk toward the empirical anchor (56 / 54) with a
   precision weight n / (n + k). This keeps high-pace inputs from overstating
   over-side mass, which matches the historical edge being concentrated on
   unders.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in the execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Domain priors (named and documented; each can be overridden at construction)
# ---------------------------------------------------------------------------
# Empirical anchors from the settled sample.
ANCHOR_HOME_MEAN = 56.0
ANCHOR_AWAY_MEAN = 54.0
SETTLED_SAMPLE = 500

# NBA possession mechanics.
LEAGUE_PACE_48 = 99.0            # possessions per team per 48 minutes
FIRST_HALF_POSS_SHARE = 0.505    # first halves run slightly faster than second halves
PACE_CV = 0.045                  # coefficient of variation of realized possessions

# Points-per-possession outcome law, used to derive Var(Y)/E[Y].
# Typical NBA split: P(0), P(1), P(2), P(3) per possession.
_PPP_PMF = np.array([0.47, 0.06, 0.34, 0.13])
_PPP_VALS = np.array([0.0, 1.0, 2.0, 3.0])

# Rest effect: points per possession lost on a back-to-back, relative to 2+ days of rest.
REST_PPP_PENALTY_B2B = 0.012

# Shrinkage prior strength, in pseudo-games.
SHRINK_PRIOR_GAMES = 150.0

# Lattice coverage, in standard deviations.
LATTICE_SIGMAS = 6.5


class NBAHalf1HomeTotalModel(BaseCandidateModel):
    """Compound possession model for NBA first-half home team totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_full_game_1119_153308",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        dispersion_override: Optional[float] = None,
        shrink_prior_games: float = SHRINK_PRIOR_GAMES,
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
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.dispersion_override = dispersion_override
        self.shrink_prior_games = float(shrink_prior_games)

        # Moments of the per-possession scoring law, precomputed once.
        ey = float(np.dot(_PPP_PMF, _PPP_VALS))
        ey2 = float(np.dot(_PPP_PMF, _PPP_VALS ** 2))
        self._ppp_mean = ey
        self._ppp_var_ratio = (ey2 - ey * ey) / ey  # Var(Y)/E[Y]; scale-free
        self._ppp_cv2 = (ey2 - ey * ey) / (ey * ey)  # Var(Y)/E[Y]^2

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any, default: float) -> float:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _team_field(self, ctx: Dict[str, Any], side: str, keys: Tuple[str, ...]) -> Optional[float]:
        team = ctx.get(side)
        candidates = []
        if isinstance(team, dict):
            candidates.extend(team.get(k) for k in keys)
        candidates.extend(ctx.get(f"{side}_{k}") for k in keys)
        for c in candidates:
            if c is None:
                continue
            try:
                v = float(c)
                if math.isfinite(v):
                    return v
            except (TypeError, ValueError):
                continue
        return None

    def _rest_ppp_adj(self, rest_days: float) -> float:
        # Smooth, monotone recovery curve: full penalty at 0 days of rest,
        # decaying exponentially to zero with more rest.
        r = max(rest_days, 0.0)
        return -REST_PPP_PENALTY_B2B * math.exp(-1.5 * r)

    # ------------------------------------------------------------ mechanics
    def _possessions(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        pace_h = self._team_field(ctx, "home", ("pace", "tempo"))
        pace_a = self._team_field(ctx, "away", ("pace", "tempo"))
        game_pace = ctx.get("pace")
        if game_pace is not None:
            pace = self._num(game_pace, LEAGUE_PACE_48)
        elif pace_h is not None and pace_a is not None:
            # Matchup pace: product form relative to the league baseline.
            pace = pace_h * pace_a / LEAGUE_PACE_48
        elif pace_h is not None or pace_a is not None:
            pace = 0.5 * ((pace_h or LEAGUE_PACE_48) + (pace_a or LEAGUE_PACE_48))
        else:
            pace = LEAGUE_PACE_48
        e_n = pace * FIRST_HALF_POSS_SHARE
        var_n = (PACE_CV * e_n) ** 2
        return e_n, var_n

    def _ppp(self, ctx: Dict[str, Any], side: str, opp: str, rest: float) -> Optional[float]:
        off = self._team_field(ctx, side, ("ortg", "off_rating", "attack", "offense"))
        dfn = self._team_field(ctx, opp, ("drtg", "def_rating", "defense"))
        if off is None and dfn is None:
            return None
        # Ratings may arrive per-100 (around 110) or as multiplicative strengths (around 1.0).
        def to_ppp(v: Optional[float]) -> float:
            if v is None:
                return self._ppp_mean
            return v / 100.0 if v > 10.0 else v * self._ppp_mean
        theta = to_ppp(off) * to_ppp(dfn) / self._ppp_mean
        return theta + self._rest_ppp_adj(rest)

    def _side_moments(self, ctx: Dict[str, Any], side: str, opp: str,
                      anchor: float, e_n: float, var_n: float) -> Tuple[float, float, float]:
        rest = self._num(ctx.get(f"{side}_rest_days"), 2.0)
        expected = ctx.get(f"{side}_expected")

        theta_mech = self._ppp(ctx, side, opp, rest)
        if expected is not None:
            mu_raw = self._num(expected, anchor)
            # The market-independent projection already embeds matchup and pace;
            # apply only the incremental rest effect.
            mu_raw += self._rest_ppp_adj(rest) * e_n
            if theta_mech is not None:
                # Combine with the mechanistic projection when ratings are available.
                mu_raw = 0.5 * mu_raw + 0.5 * theta_mech * e_n
        elif theta_mech is not None:
            mu_raw = theta_mech * e_n
        else:
            mu_raw = anchor

        # Precision shrinkage toward the empirical anchor.
        n_eff = self._num(ctx.get("sample_games"), SETTLED_SAMPLE)
        w = n_eff / (n_eff + self.shrink_prior_games)
        mu = w * mu_raw + (1.0 - w) * anchor
        mu = max(mu, 1.0)

        theta = mu / e_n
        if self.dispersion_override is not None and self.dispersion_override > 0:
            var = self.dispersion_override * mu
        else:
            # Compound variance: E[N] Var(Y) + theta^2 Var(N), with Var(Y)
            # scaled to the matchup's efficiency through a fixed CV.
            var_y = self._ppp_cv2 * theta * theta
            var = e_n * var_y + theta * theta * var_n
        return mu, var, theta

    # ------------------------------------------------------------- predict
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        e_n, var_n = self._possessions(ctx)

        mu_h, var_h, th_h = self._side_moments(ctx, "home", "away", ANCHOR_HOME_MEAN, e_n, var_n)
        mu_a, var_a, th_a = self._side_moments(ctx, "away", "home", ANCHOR_AWAY_MEAN, e_n, var_n)

        sd_h = math.sqrt(max(var_h, 1e-6))
        sd_a = math.sqrt(max(var_a, 1e-6))
        cov = th_h * th_a * var_n
        rho = cov / (sd_h * sd_a)
        rho = float(np.clip(rho, -0.999, 0.999))  # numerical guard only

        # Integer lattice covering ±LATTICE_SIGMAS standard deviations.
        h_lo = max(0, int(math.floor(mu_h - LATTICE_SIGMAS * sd_h)))
        h_hi = int(math.ceil(mu_h + LATTICE_SIGMAS * sd_h))
        a_lo = max(0, int(math.floor(mu_a - LATTICE_SIGMAS * sd_a)))
        a_hi = int(math.ceil(mu_a + LATTICE_SIGMAS * sd_a))
        hs = np.arange(h_lo, h_hi + 1, dtype=float)
        as_ = np.arange(a_lo, a_hi + 1, dtype=float)

        # Home marginal, with half-point continuity correction.
        p_h = norm.cdf((hs + 0.5 - mu_h) / sd_h) - norm.cdf((hs - 0.5 - mu_h) / sd_h)

        # Away conditional on home (bivariate Gaussian), vectorized.
        cond_mu = mu_a + rho * (sd_a / sd_h) * (hs - mu_h)
        cond_sd = sd_a * math.sqrt(1.0 - rho * rho)
        upper = (as_[None, :] + 0.5 - cond_mu[:, None]) / cond_sd
        lower = (as_[None, :] - 0.5 - cond_mu[:, None]) / cond_sd
        p_a_given_h = norm.cdf(upper) - norm.cdf(lower)
        row_sum = p_a_given_h.sum(axis=1, keepdims=True)
        p_a_given_h = np.divide(p_a_given_h, row_sum,
                                out=np.zeros_like(p_a_given_h), where=row_sum > 0)

        joint = p_h[:, None] * p_a_given_h
        joint = np.clip(joint, 0.0, None)
        total = joint.sum()
        if not np.isfinite(total) or total <= 0:
            # Degenerate fallback: point mass at the rounded anchors.
            joint = np.zeros((len(hs), len(as_)))
            joint[int(np.argmin(np.abs(hs - mu_h))), int(np.argmin(np.abs(as_ - mu_a)))] = 1.0
            total = 1.0
        joint /= total

        # Sparse dictionary; drop negligible cells, then renormalize exactly.
        idx_h, idx_a = np.nonzero(joint > 1e-12)
        vals = joint[idx_h, idx_a]
        vals = vals / vals.sum()
        scores = {
            (int(hs[i]), int(as_[j])): float(p)
            for i, j, p in zip(idx_h, idx_a, vals)
        }
        return DiscreteDistribution.from_scores(scores)