"""
Candidate model for NCAAM | quarter_1 | spreads.

Settlement note: NCAAM is played in two 20-minute halves, so "quarter_1" has
no native boundary. This model assumes the feed settles on the first 10:00 of
game clock (window_minutes / game_minutes = 10 / 40). Verify that mapping
against the feed's grading rules before deployment. If the feed defines the
window differently, set `window_minutes` to match, because a misaligned
window invalidates every price this model emits.

Structure, derived from scoring mechanics rather than a Skellam/Poisson
template:

  1. Total possessions T in the window follow a renewal-process count.
     Possession durations have a coefficient of variation well below 1,
     so the count is UNDER-dispersed relative to Poisson. Tempo uncertainty
     across games adds extra variance on top:
         Var[T] = CV^2 * E[T] + (2 * sd_tempo_window)^2
     Possessions alternate between teams, so given T, home receives
     ceil(T/2) or floor(T/2) with equal probability (tip / arrow).

  2. Each possession yields k in {0,1,2,3,4} points (4 = and-one triple).
     The per-team outcome law is the minimum-relative-entropy
     (exponential) tilt of a domain baseline possession-outcome profile,
     chosen so that E[n_team] * E[X] reproduces the team's expected
     window score exactly.

  3. The joint score law is computed exactly with the bivariate PGF
         P(z1, z2) = E[ g_h(z1)^{n_h} * g_a(z2)^{n_a} ]
     evaluated on roots of unity and inverted by a 2-D inverse DFT. The
     grid equals the support bound, so there is no aliasing. Integer
     margin mass, including push mass on short lines, is therefore exact.

  4. Shared possessions induce positive score correlation, which shrinks
     margin variance relative to independent scores. Lumpy 1/2/3-point
     arrivals inflate per-team Var/Mean to roughly 2.2-2.5. Both effects
     are priced rather than assumed away.

  5. Home edge enters only through the efficiency gap. The observed gap is
     shrunk by a reliability weight toward a home-court prior proportional
     to segment length, which limits home over-confidence.

Every numeric value below is a named, documented domain prior exposed on
the instance, not an anonymous clamp.
"""

import math
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")


class NCAAMQuarter1SpreadsPossessionModel(BaseCandidateModel):
    """Possession-lattice margin model for the first 10:00 of NCAAM games."""

    # ------------------------------------------------------------------ #
    # Domain priors (named, documented, overridable)
    # ------------------------------------------------------------------ #
    # Empirical segment baselines (forensic autopsy anchors).
    BASELINE_HOME_EXPECTED = 20.00
    BASELINE_AWAY_EXPECTED = 18.75

    # Clock geometry.
    GAME_MINUTES = 40.0
    WINDOW_MINUTES = 10.0  # VERIFY against feed settlement rules.

    # Tempo: D-I average possessions per team per 40 minutes, and the
    # cross-game standard deviation of realized tempo.
    LEAGUE_TEMPO = 68.0
    TEMPO_SD_FULL_GAME = 5.0

    # Squared coefficient of variation of possession duration (renewal
    # theory: Var[count] ~ CV^2 * E[count]). Values below 1 mean the
    # count is under-dispersed relative to Poisson.
    POSSESSION_DURATION_CV2 = 0.35

    # Baseline possession-outcome profile for k = 0, 1, 2, 3, 4 points.
    # Reflects D-I shot mix: ~35-38% of FGA from three, FT trips, and-ones.
    # Its mean is ~1.05 points per possession and is re-tilted per team.
    BASE_OUTCOME_PROFILE = (0.500, 0.060, 0.330, 0.105, 0.005)

    # Home court: ~3 points per 40 minutes, scaled by segment length.
    HCA_FULL_GAME = 3.0

    # Reliability shrinkage of the input efficiency gap (window units).
    # The weight is signal_var / (signal_var + noise_var).
    MARGIN_SIGNAL_SD_WINDOW = 3.0
    MARGIN_NOISE_SD_WINDOW = 1.0

    # Rest: saturating fatigue effect on efficiency.
    # Multiplier = exp(+/- REST_KAPPA * (tanh(r_h/tau) - tanh(r_a/tau)) / 2).
    REST_KAPPA = 0.02
    REST_TAU_DAYS = 2.0

    # Width of the possession-count support, in standard deviations.
    POSSESSION_SUPPORT_SIGMAS = 7.0

    # Numerical mass-pruning floor applied when emitting the score lattice.
    MASS_FLOOR = 1e-13

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_1_1107_170949",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_1",
        family: str = "spreads",
        window_minutes: Optional[float] = None,
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
        if window_minutes is not None:
            self.WINDOW_MINUTES = float(window_minutes)
        base = np.asarray(self.BASE_OUTCOME_PROFILE, dtype=float)
        self._base_profile = base / base.sum()
        self._k = np.arange(self._base_profile.size, dtype=float)

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _lookup(ctx: Dict[str, Any], path: Sequence[str]) -> Optional[float]:
        node: Any = ctx
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        if node is None:
            return None
        try:
            val = float(node)
        except (TypeError, ValueError):
            return None
        return val if math.isfinite(val) else None

    def _first(self, ctx: Dict[str, Any], *paths: Sequence[str]) -> Optional[float]:
        for p in paths:
            v = self._lookup(ctx, p)
            if v is not None:
                return v
        return None

    def _tempo(self, ctx: Dict[str, Any], side: str) -> Optional[float]:
        return self._first(
            ctx,
            (side, "tempo"),
            (side, "pace"),
            (f"{side}_tempo",),
            (f"{side}_pace",),
        )

    def _expected_scores(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._first(ctx, ("home_expected",))
        a = self._first(ctx, ("away_expected",))
        if h is not None and a is not None and h > 0 and a > 0:
            # Inputs are already situational; use them as the anchor.
            return h, a

        # Otherwise, scale the empirical baseline by multiplicative
        # attack/defense ratings (1.0 = league average).
        h_att = self._first(ctx, ("home", "attack"), ("home_attack",))
        h_def = self._first(ctx, ("home", "defense"), ("home_defense",))
        a_att = self._first(ctx, ("away", "attack"), ("away_attack",))
        a_def = self._first(ctx, ("away", "defense"), ("away_defense",))
        h_mult = (h_att if h_att and h_att > 0 else 1.0) * (
            a_def if a_def and a_def > 0 else 1.0
        )
        a_mult = (a_att if a_att and a_att > 0 else 1.0) * (
            h_def if h_def and h_def > 0 else 1.0
        )
        h_out = h if (h is not None and h > 0) else self.BASELINE_HOME_EXPECTED * h_mult
        a_out = a if (a is not None and a > 0) else self.BASELINE_AWAY_EXPECTED * a_mult
        return h_out, a_out

    # ------------------------------------------------------------------ #
    # Mechanics
    # ------------------------------------------------------------------ #
    @property
    def window_fraction(self) -> float:
        return self.WINDOW_MINUTES / self.GAME_MINUTES

    def _possession_pmf(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        """Pmf of TOTAL possessions (both teams) in the window."""
        th = self._tempo(ctx, "home")
        ta = self._tempo(ctx, "away")
        tl = self.LEAGUE_TEMPO
        if th is not None and ta is not None and th > 0 and ta > 0:
            game_tempo = th * ta / tl
        elif th is not None and th > 0:
            game_tempo = th
        elif ta is not None and ta > 0:
            game_tempo = ta
        else:
            game_tempo = tl

        frac = self.window_fraction
        mean_total = 2.0 * game_tempo * frac
        tempo_sd_window = self.TEMPO_SD_FULL_GAME * frac
        var_total = self.POSSESSION_DURATION_CV2 * mean_total + (2.0 * tempo_sd_window) ** 2
        sd_total = math.sqrt(var_total)

        lo = max(0, int(math.floor(mean_total - self.POSSESSION_SUPPORT_SIGMAS * sd_total)))
        hi = int(math.ceil(mean_total + self.POSSESSION_SUPPORT_SIGMAS * sd_total))
        support = np.arange(lo, hi + 1)
        # Continuity-corrected discretized normal on the integer lattice.
        cdf_hi = norm.cdf(support + 0.5, loc=mean_total, scale=sd_total)
        cdf_lo = norm.cdf(support - 0.5, loc=mean_total, scale=sd_total)
        pmf = cdf_hi - cdf_lo
        pmf = pmf / pmf.sum()
        return support, pmf

    def _rest_multipliers(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        rh = self._first(ctx, ("home_rest_days",), ("home", "rest_days"))
        ra = self._first(ctx, ("away_rest_days",), ("away", "rest_days"))
        if rh is None or ra is None:
            return 1.0, 1.0
        tau = self.REST_TAU_DAYS
        delta = math.tanh(max(rh, 0.0) / tau) - math.tanh(max(ra, 0.0) / tau)
        m = math.exp(0.5 * self.REST_KAPPA * delta)
        return m, 1.0 / m

    def _shrunk_targets(self, h: float, a: float) -> Tuple[float, float]:
        """Reliability-shrink the efficiency gap toward a length-scaled HCA.

        The total is preserved exactly.
        """
        total = h + a
        prior_gap = self.HCA_FULL_GAME * self.window_fraction
        s2 = self.MARGIN_SIGNAL_SD_WINDOW ** 2
        n2 = self.MARGIN_NOISE_SD_WINDOW ** 2
        w = s2 / (s2 + n2)
        gap = prior_gap + w * ((h - a) - prior_gap)
        return 0.5 * (total + gap), 0.5 * (total - gap)

    def _tilted_profile(self, target_ppp: float) -> np.ndarray:
        """Min-KL exponential tilt of the baseline profile hitting target_ppp."""
        k = self._k
        b = self._base_profile
        kmax = float(k[-1])
        eps = 1e-9
        target = min(max(target_ppp, eps), kmax - eps)

        def mean_at(theta: float) -> float:
            w = b * np.exp(theta * (k - k.mean()))
            return float((w * k).sum() / w.sum()) - target

        lo, hi = -50.0, 50.0
        theta = brentq(mean_at, lo, hi, xtol=1e-12, maxiter=500)
        w = b * np.exp(theta * (k - k.mean()))
        return w / w.sum()

    def joint_score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        """Exact joint pmf P[S_home = i, S_away = j] for the window."""
        h_exp, a_exp = self._expected_scores(ctx)
        h_exp, a_exp = self._shrunk_targets(h_exp, a_exp)
        rm_h, rm_a = self._rest_multipliers(ctx)
        # Rest re-allocates efficiency and preserves the total.
        total = h_exp + a_exp
        h_adj, a_adj = h_exp * rm_h, a_exp * rm_a
        scale = total / (h_adj + a_adj)
        h_exp, a_exp = h_adj * scale, a_adj * scale

        support, pmf_t = self._possession_pmf(ctx)
        mean_team_poss = 0.5 * float((support * pmf_t).sum())
        p_h = self._tilted_profile(h_exp / mean_team_poss)
        p_a = self._tilted_profile(a_exp / mean_team_poss)

        kmax = p_h.size - 1
        n_team_max = int(math.ceil(support[-1] / 2.0))
        L = kmax * n_team_max + 1  # exact support bound, so no aliasing

        roots = np.exp(-2j * np.pi * np.arange(L) / L)
        powers = roots[None, :] ** np.arange(kmax + 1)[:, None]
        g_h = p_h @ powers
        g_a = p_a @ powers

        F = np.zeros((L, L), dtype=complex)
        for t, pt in zip(support, pmf_t):
            if pt <= 0.0:
                continue
            t = int(t)
            lo_n, hi_n = t // 2, t - t // 2
            if lo_n == hi_n:
                F += pt * np.outer(g_h ** hi_n, g_a ** lo_n)
            else:
                F += 0.5 * pt * (
                    np.outer(g_h ** hi_n, g_a ** lo_n) + np.outer(g_h ** lo_n, g_a ** hi_n)
                )

        P = np.fft.ifft2(F).real
        P[P < 0.0] = 0.0
        P /= P.sum()
        return P

    # ------------------------------------------------------------------ #
    # Convenience pricing helpers
    # ------------------------------------------------------------------ #
    def margin_pmf(self, ctx: Dict[str, Any]) -> Dict[int, float]:
        P = self.joint_score_matrix(ctx)
        L = P.shape[0]
        out: Dict[int, float] = {}
        idx = np.arange(L)
        diff = idx[:, None] - idx[None, :]
        for d in range(-(L - 1), L):
            m = float(P[diff == d].sum())
            if m > self.MASS_FLOOR:
                out[d] = m
        return out

    def cover_probabilities(self, ctx: Dict[str, Any], home_line: float) -> Dict[str, float]:
        """Home covers when margin + home_line > 0. Push mass is reported separately."""
        pmf = self.margin_pmf(ctx)
        win = sum(p for d, p in pmf.items() if d + home_line > 0)
        push = sum(p for d, p in pmf.items() if d + home_line == 0)
        lose = sum(p for d, p in pmf.items() if d + home_line < 0)
        return {"home_cover": win, "push": push, "away_cover": lose}

    # ------------------------------------------------------------------ #
    # Required interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        P = self.joint_score_matrix(ctx)
        ii, jj = np.nonzero(P > self.MASS_FLOOR)
        mass = P[ii, jj]
        mass = mass / mass.sum()
        scores = {(int(i), int(j)): float(m) for i, j, m in zip(ii, jj, mass)}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            Q = np.zeros_like(P)
            Q[ii, jj] = mass
            return DiscreteDistribution.from_scores(Q)