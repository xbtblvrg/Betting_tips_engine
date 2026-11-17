"""
Candidate model for segment NBA|half_1|home_total.

The model is a compound possession process with a shared pace factor.

    Team first-half points  S = X_1 + ... + X_N
    N   = shared number of first-half possessions (common to both teams)
    X_i = points scored on one possession, with support {0, 1, 2, 3, 4}

Moments implied by this structure:
    E[S]   = E[N] * E[X]
    Var[S] = E[N] * Var[X] + Var[N] * E[X]^2

So the variance comes from two places: shot-outcome noise (Var[X]) and
tempo uncertainty (Var[N]). Because both teams use the same N, the
home and away scores are positively correlated through pace.

Calibration notes:
  * The reported dispersion index (0.187) is below the floor that
    possession mechanics allow for team point totals. Var[X] alone gives
    Var/Mean of roughly 1.1. The index therefore cannot describe the
    variance of raw points. It is read as the dispersion of the possession
    count, i.e. Var[N] = d * E[N]. This gives a first-half pace SD of
    about 3 possessions, which matches observed NBA tempo variability.
  * The possession-outcome law is built by exponentially tilting a
    league-average outcome shape until its mean equals the required
    points per possession. Among all laws with that mean, this is the
    minimum-KL (maximum-entropy) one. No arbitrary scaling or clamping
    is applied.
  * The model never reads bookmaker prices. Situational inputs (rest,
    attack/defense ratings, pace) adjust the anchor in log space, with
    shrinkage so they do not double count information already contained
    in 'home_expected'.
  * fit() re-estimates the pace dispersion from historical residuals by
    method of moments.

Latency: one root-solve per team plus a batched FFT convolution.
Typical runtime is a few milliseconds.
"""

import math
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NBAHalf1HomeTotalCompoundPossessionModel(BaseCandidateModel):
    # Domain priors: structural NBA facts, all overridable.

    # League-average single-possession outcome shape on {0, 1, 2, 3, 4} points.
    # 4 points corresponds to an and-one three or a three plus foul sequence.
    # Implied mean is about 1.17 points per possession.
    BASE_POSSESSION_SHAPE = np.array([0.47, 0.05, 0.33, 0.14, 0.01], dtype=float)

    # Anchors from the empirical segment autopsy.
    EMPIRICAL_HOME_MEAN = 56.00
    EMPIRICAL_AWAY_MEAN = 54.00
    EMPIRICAL_DISPERSION_INDEX = 0.187

    # Rest-fatigue response.
    # Offensive efficiency change = coef * (exp(-ref) - exp(-rest_days)).
    # Fatigue decays exponentially in days of rest.
    REST_EFFICIENCY_COEF = 0.03
    REST_REFERENCE_DAYS = 1.5

    # Shrinkage applied to attack/defense ratings when 'home_expected'
    # is already given, since that value already embeds team strength.
    RESIDUAL_RATING_SHRINK = 0.25

    # Tail coverage of the pace distribution and score grid, in SD units.
    TAIL_SD = 7.0

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_183000",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        pace_dispersion: Optional[float] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.base_shape = self.BASE_POSSESSION_SHAPE / self.BASE_POSSESSION_SHAPE.sum()
        self.support = np.arange(self.base_shape.size, dtype=float)
        self.base_ppp = float(self.base_shape @ self.support)
        self.pace_dispersion = (
            float(pace_dispersion)
            if pace_dispersion is not None
            else self.EMPIRICAL_DISPERSION_INDEX
        )
        self._tilt_cache: Dict[float, np.ndarray] = {}

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], key: str) -> Optional[float]:
        v = ctx.get(key)
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    @staticmethod
    def _rating(ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        block = ctx.get(side)
        candidates = []
        if isinstance(block, dict):
            candidates.append(block.get(kind))
        candidates.append(ctx.get(f"{side}_{kind}"))
        for v in candidates:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f) and f > 0:
                return f
        return None

    def _rest_effect(self, rest_days: Optional[float]) -> float:
        if rest_days is None or rest_days < 0:
            return 0.0
        return self.REST_EFFICIENCY_COEF * (
            math.exp(-self.REST_REFERENCE_DAYS) - math.exp(-rest_days)
        )

    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mu_h = self._num(ctx, "home_expected")
        mu_a = self._num(ctx, "away_expected")
        anchored = mu_h is not None and mu_a is not None and mu_h > 0 and mu_a > 0
        if not anchored:
            mu_h, mu_a = self.EMPIRICAL_HOME_MEAN, self.EMPIRICAL_AWAY_MEAN

        # Attack/defense ratings, expressed as multiplicative indices around 1.
        # When an expectation is already supplied, only a shrunken residual
        # adjustment is applied.
        w = self.RESIDUAL_RATING_SHRINK if anchored else 1.0
        h_att = self._rating(ctx, "home", "attack")
        h_def = self._rating(ctx, "home", "defense")
        a_att = self._rating(ctx, "away", "attack")
        a_def = self._rating(ctx, "away", "defense")
        log_h = (math.log(h_att) if h_att else 0.0) + (math.log(a_def) if a_def else 0.0)
        log_a = (math.log(a_att) if a_att else 0.0) + (math.log(h_def) if h_def else 0.0)

        # Rest: a team's own fatigue lowers its offense; opponent fatigue
        # weakens the defense it faces. The defensive channel is assumed
        # to carry half the weight of the offensive channel.
        r_h = self._rest_effect(self._num(ctx, "home_rest_days"))
        r_a = self._rest_effect(self._num(ctx, "away_rest_days"))
        log_h += w * 0.0 + math.log1p(r_h) - 0.5 * math.log1p(r_a)
        log_a += math.log1p(r_a) - 0.5 * math.log1p(r_h)

        mu_h *= math.exp(w * log_h if (h_att or a_def) else log_h)
        mu_a *= math.exp(w * log_a if (a_att or h_def) else log_a)
        return mu_h, mu_a

    def _expected_possessions(self, ctx: Dict[str, Any], mu_h: float, mu_a: float) -> float:
        # Pace is quoted per 48 minutes, so a half has pace / 2 possessions.
        paces = [
            p
            for p in (self._num(ctx, "home_pace"), self._num(ctx, "away_pace"))
            if p is not None and p > 0
        ]
        pace = self._num(ctx, "pace")
        if pace is not None and pace > 0:
            return pace / 2.0
        if paces:
            # Opponents meet at a common tempo; the geometric mean is
            # symmetric and scale-consistent.
            return math.exp(sum(math.log(p) for p in paces) / len(paces)) / 2.0
        # Otherwise invert E[S] = E[N] * PPP using league-average PPP.
        return 0.5 * (mu_h + mu_a) / self.base_ppp

    # ------------------------------------------------------------------ #
    # Possession outcome law: minimum-KL exponential tilt to a target PPP
    # ------------------------------------------------------------------ #
    def _tilted_outcome(self, ppp: float) -> np.ndarray:
        key = round(ppp, 6)
        cached = self._tilt_cache.get(key)
        if cached is not None:
            return cached

        k = self.support
        logb = np.log(self.base_shape)

        def mean_gap(theta: float) -> float:
            z = logb + theta * k
            z -= z.max()
            q = np.exp(z)
            q /= q.sum()
            return float(q @ k) - ppp

        lo, hi = -1.0, 1.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = brentq(mean_gap, lo, hi, xtol=1e-12)

        z = logb + theta * k
        z -= z.max()
        q = np.exp(z)
        q /= q.sum()
        if len(self._tilt_cache) > 4096:
            self._tilt_cache.clear()
        self._tilt_cache[key] = q
        return q

    # ------------------------------------------------------------------ #
    # Core: joint first-half score lattice
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a = self._expected_points(game_context)
        n_bar = self._expected_possessions(game_context, mu_h, mu_a)

        q_h = self._tilted_outcome(mu_h / n_bar)
        q_a = self._tilted_outcome(mu_a / n_bar)

        # Pace law: discretised normal on the integers with variance d * E[N].
        var_n = self.pace_dispersion * n_bar
        sd_n = math.sqrt(var_n) if var_n > 0 else 0.0
        if sd_n > 0:
            span = int(math.ceil(self.TAIL_SD * sd_n))
            centre = int(round(n_bar))
            ns = np.arange(max(1, centre - span), centre + span + 1)
            logw = -0.5 * ((ns - n_bar) / sd_n) ** 2
            w = np.exp(logw - logw.max())
        else:
            ns = np.array([max(1, int(round(n_bar)))])
            w = np.ones(1)
        w /= w.sum()

        # Grid size: max score mean + TAIL_SD * SD. The FFT length is set
        # past that point so circular aliasing is negligible.
        def side_sd(q: np.ndarray) -> Tuple[float, float]:
            m = float(q @ self.support)
            v = float(q @ self.support ** 2) - m * m
            return n_bar * m, math.sqrt(n_bar * v + var_n * m * m)

        m_h, s_h = side_sd(q_h)
        m_a, s_a = side_sd(q_a)
        L = int(max(m_h + self.TAIL_SD * s_h, m_a + self.TAIL_SD * s_a)) + 1
        L_fft = 1 << int(math.ceil(math.log2(max(L, 8) * 2)))

        phi_h = np.fft.fft(q_h, L_fft)
        phi_a = np.fft.fft(q_a, L_fft)
        nn = ns[:, None].astype(float)
        H = np.fft.ifft(phi_h[None, :] ** nn, axis=1).real[:, :L]
        A = np.fft.ifft(phi_a[None, :] ** nn, axis=1).real[:, :L]

        # Mixture over the shared pace gives the pace-induced correlation.
        M = (H * w[:, None]).T @ A
        # Remove FFT round-off negatives (around 1e-17).
        M[M < 0.0] = 0.0
        M /= M.sum()
        return M

    def predict_distribution(self, game_context: Dict[str, Any]):
        from_scores = DiscreteDistribution.from_scores  # noqa: F821, injected in scope
        M = self.score_matrix(game_context or {})
        return from_scores(M)

    # Market helper: probability that the home first-half total goes over or under a line.
    def home_total_probabilities(
        self, game_context: Dict[str, Any], line: float
    ) -> Dict[str, float]:
        marg = self.score_matrix(game_context).sum(axis=1)
        pts = np.arange(marg.size, dtype=float)
        over = float(marg[pts > line].sum())
        push = float(marg[pts == line].sum())
        return {"over": over, "under": 1.0 - over - push, "push": push}

    # ------------------------------------------------------------------ #
    # Calibration on historical games: method-of-moments estimate of the
    # pace dispersion from home first-half scoring residuals.
    #   Var(S - E[S]) = E[N] Var[X] + Var[N] E[X]^2   =>   solve for Var[N].
    # ------------------------------------------------------------------ #
    def fit(self, games: Iterable[Dict[str, Any]]) -> "NBAHalf1HomeTotalCompoundPossessionModel":
        excess, n_bars = [], []
        for g in games:
            actual = self._num(g, "home_score_half_1")
            if actual is None:
                actual = self._num(g, "home_score")
            if actual is None:
                continue
            mu_h, mu_a = self._expected_points(g)
            n_bar = self._expected_possessions(g, mu_h, mu_a)
            q = self._tilted_outcome(mu_h / n_bar)
            m = float(q @ self.support)
            v = float(q @ self.support ** 2) - m * m
            # Unbiased per-game contribution to Var[N], scaled by E[N]
            # to give the dispersion index.
            excess.append(((actual - mu_h) ** 2 - n_bar * v) / (m * m * n_bar))
            n_bars.append(n_bar)

        if len(excess) >= 30:
            est = float(np.mean(excess))
            # Precision-weighted shrinkage toward the empirical segment
            # index. The weight grows with sample size relative to the
            # prior's effective size (the 500 settled wagers).
            n = len(excess)
            prior_n = 500.0
            pooled = (n * est + prior_n * self.EMPIRICAL_DISPERSION_INDEX) / (n + prior_n)
            if pooled > 0 and math.isfinite(pooled):
                self.pace_dispersion = pooled
        return self