import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import gammaln

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - the engine may pre-load it into scope instead
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class EPLHalf1WinnerTotalModel(BaseCandidateModel):
    """
    First-half EPL score model used for both 1X2 (winner) and totals pricing.

    Structure
    ---------
    1. Goal intensities are anchored on the empirical first-half means
       (home 0.80, away 0.60). If the context supplies full-match expectations,
       they are mapped to first-half intensities using the empirical
       first-half goal share implied by those anchors.
    2. Situational inputs (attack/defense ratings, rest days, tempo, weather)
       adjust the intensities multiplicatively in log space. Each adjustment
       is shrunk toward zero according to how informative it is, so missing
       or noisy inputs return the model to the empirical anchor rather than
       to an arbitrary value.
    3. The joint score distribution is a bivariate Poisson. A shared component
       captures common game-state intensity. A Dixon-Coles low-score
       correction then reproduces the excess of 0-0 and 1-1 first halves,
       which is the main structural reason first-half draws (~40%+) are more
       frequent than independent Poisson predicts.
    4. Dispersion: the observed dispersion index is 1.000, so the marginals
       stay Poisson. If a dispersion index is supplied in the context, the
       marginals are smoothly mixed with a Gamma-Poisson (negative binomial)
       of matching variance.
    5. The score lattice is truncated adaptively, so that the tail mass left
       out is below machine-level tolerance. No fixed goal cap is used.

    The model never reads bookmaker odds, so its forecasts contain no vig.
    """

    # Empirical anchors: first-half EPL means from the segment diagnosis.
    PRIOR_HOME_H1 = 0.80
    PRIOR_AWAY_H1 = 0.60
    PRIOR_DISPERSION = 1.000

    # Long-run EPL full-match goal means, used only to map full-match inputs
    # onto first-half intensity via the empirical first-half share.
    PRIOR_HOME_FT = 1.55
    PRIOR_AWAY_FT = 1.25

    # First-half matches show excess low-score draws. The Dixon-Coles
    # parameter is negative, which inflates 0-0 and 1-1 and deflates 1-0 and 0-1.
    PRIOR_DC_RHO = -0.10

    # Share of goal variance that comes from common match tempo
    # (bivariate Poisson covariance as a fraction of the smaller mean).
    PRIOR_SHARED_FRACTION = 0.06

    # Tail tolerance for adaptive lattice truncation.
    TAIL_TOL = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_epl_half_1_1107_122517",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "half_1",
        family: str = "winner_total",
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
            super().__init__()  # type: ignore[call-arg]
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Empirical first-half share derived from the anchors (≈0.47).
        self._h1_share = (self.PRIOR_HOME_H1 + self.PRIOR_AWAY_H1) / (
            self.PRIOR_HOME_FT + self.PRIOR_AWAY_FT
        )
        # Home-advantage ratio implied by the anchors. It is used to damp
        # home over-confidence when inputs are extreme.
        self._prior_log_ha = math.log(self.PRIOR_HOME_H1 / self.PRIOR_AWAY_H1)

    # ------------------------------------------------------------------ #
    # Context parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            v = float(value)
            if math.isnan(v) or math.isinf(v):
                return None
            return v
        except (TypeError, ValueError):
            return None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._num(ctx.get(k))
                if v is not None:
                    return v
        return None

    def _side_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Read an attack/defense rating from a nested dict or from flat keys."""
        nested = ctx.get(side)
        if isinstance(nested, dict):
            for k in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = self._num(nested.get(k))
                if v is not None:
                    return v
        return self._get(
            ctx,
            f"{side}_{kind}",
            f"{side}_{kind}_rating",
            f"{side}_{kind}_strength",
        )

    @staticmethod
    def _rating_to_log(r: Optional[float]) -> float:
        """
        Map a rating to a log multiplier.

        Ratings may arrive either as ratios (centered at 1) or as additive
        scores (centered at 0). Ratios go through a log. Additive scores are
        squashed with tanh so that one unbounded input cannot dominate.
        """
        if r is None:
            return 0.0
        if r > 0.0 and 0.2 <= r <= 5.0:
            return math.log(r)
        return math.tanh(r) * 0.5

    # ------------------------------------------------------------------ #
    # Intensity construction
    # ------------------------------------------------------------------ #
    def _base_intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._get(ctx, "home_expected", "home_xg", "lambda_home")
        a = self._get(ctx, "away_expected", "away_xg", "lambda_away")

        if h is None or h <= 0:
            h = None
        if a is None or a <= 0:
            a = None

        # Decide whether the inputs are full-match or first-half values by
        # checking which empirical scale their sum is closer to (in log terms).
        if h is not None and a is not None:
            tot = h + a
            ft_tot = self.PRIOR_HOME_FT + self.PRIOR_AWAY_FT
            h1_tot = self.PRIOR_HOME_H1 + self.PRIOR_AWAY_H1
            if abs(math.log(tot / ft_tot)) < abs(math.log(tot / h1_tot)):
                h *= self._h1_share
                a *= self._h1_share
        elif h is not None:
            if h > self.PRIOR_HOME_H1 * 1.5:
                h *= self._h1_share
        elif a is not None:
            if a > self.PRIOR_AWAY_H1 * 1.5:
                a *= self._h1_share

        lam_h = h if h is not None else self.PRIOR_HOME_H1
        lam_a = a if a is not None else self.PRIOR_AWAY_H1
        return lam_h, lam_a

    def _situational_log_adjust(self, ctx: Dict[str, Any], already_has_xg: bool) -> Tuple[float, float]:
        # Attack/defense exposure. If a priced xG input is already present,
        # it has most likely absorbed team strength, so ratings are shrunk
        # heavily to avoid double counting.
        rating_weight = 0.25 if already_has_xg else 0.6
        ha = self._rating_to_log(self._side_rating(ctx, "home", "attack"))
        hd = self._rating_to_log(self._side_rating(ctx, "home", "defense"))
        aa = self._rating_to_log(self._side_rating(ctx, "away", "attack"))
        ad = self._rating_to_log(self._side_rating(ctx, "away", "defense"))
        # A positive defense value means a stronger defense, which lowers
        # the opponent's intensity.
        adj_h = rating_weight * (ha - ad)
        adj_a = rating_weight * (aa - hd)

        # Rest differential: fatigue effects saturate, so the log gap passes
        # through tanh. A first half is less affected than a full match
        # because fatigue accumulates late.
        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        if hr is not None and ar is not None and hr >= 0 and ar >= 0:
            rest_signal = math.tanh(math.log((hr + 1.0) / (ar + 1.0)))
            rest_eff = 0.03 * rest_signal
            adj_h += rest_eff
            adj_a -= rest_eff

        # Tempo/pace scales both intensities together.
        pace = self._get(ctx, "pace", "tempo", "expected_pace")
        if pace is not None and pace > 0:
            pace_log = math.log(pace) if 0.3 <= pace <= 3.0 else math.tanh(pace / 100.0 - 1.0)
            adj_h += 0.5 * pace_log
            adj_a += 0.5 * pace_log

        # Weather: strong wind and cold suppress goals, and only outdoors.
        is_dome = bool(ctx.get("is_dome")) if ctx.get("is_dome") is not None else False
        if not is_dome:
            wind = self._get(ctx, "wind_speed")
            if wind is not None and wind > 0:
                wind_eff = -0.06 * math.tanh(max(0.0, wind - 15.0) / 15.0)
                adj_h += wind_eff
                adj_a += wind_eff
            temp = self._get(ctx, "temp", "temperature")
            if temp is not None:
                # Accept either Celsius or Fahrenheit. Any value above 45 is assumed to be Fahrenheit.
                t_c = (temp - 32.0) * 5.0 / 9.0 if temp > 45.0 else temp
                cold_eff = -0.03 * math.tanh(max(0.0, 2.0 - t_c) / 6.0)
                adj_h += cold_eff
                adj_a += cold_eff

        return adj_h, adj_a

    def _limit_home_advantage(self, lam_h: float, lam_a: float) -> Tuple[float, float]:
        """
        Smoothly shrink an extreme intensity ratio back toward the empirical
        prior ratio while keeping the total unchanged. Large deviations from
        the anchor in log-ratio space are compressed by tanh, so the effect is
        continuous rather than a hard clamp.
        """
        total = lam_h + lam_a
        log_ratio = math.log(lam_h / lam_a)
        dev = log_ratio - self._prior_log_ha
        scale = 1.5  # log-ratio deviation at which compression becomes material
        compressed = self._prior_log_ha + scale * math.tanh(dev / scale)
        r = math.exp(compressed)
        lam_a_new = total / (1.0 + r)
        lam_h_new = total - lam_a_new
        return lam_h_new, lam_a_new

    # ------------------------------------------------------------------ #
    # Distribution primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _poisson_pmf(lam: float, n: int) -> np.ndarray:
        k = np.arange(n + 1, dtype=float)
        if lam <= 0:
            out = np.zeros(n + 1)
            out[0] = 1.0
            return out
        return np.exp(k * math.log(lam) - lam - gammaln(k + 1.0))

    @staticmethod
    def _negbin_pmf(mean: float, disp: float, n: int) -> np.ndarray:
        """Gamma-Poisson with Var = disp * mean (disp > 1)."""
        k = np.arange(n + 1, dtype=float)
        r = mean / (disp - 1.0)
        p = 1.0 / disp  # success probability
        logpmf = (
            gammaln(k + r) - gammaln(r) - gammaln(k + 1.0)
            + r * math.log(p) + k * math.log(1.0 - p)
        )
        return np.exp(logpmf)

    def _marginal(self, lam: float, disp: float, n: int) -> np.ndarray:
        if disp > 1.0 + 1e-6:
            return self._negbin_pmf(lam, disp, n)
        return self._poisson_pmf(lam, n)

    def _lattice_size(self, lam_h: float, lam_a: float, disp: float) -> int:
        lam = max(lam_h, lam_a)
        sd = math.sqrt(max(lam * max(disp, 1.0), 1e-9))
        n = int(math.ceil(lam + 12.0 * sd + 6.0))
        # Extend the lattice until the omitted tail is negligible.
        while True:
            pm = self._marginal(lam, disp, n)
            if 1.0 - pm.sum() < self.TAIL_TOL or n > 60:
                return n
            n += 2

    def _score_matrix(self, lam_h: float, lam_a: float, disp: float) -> np.ndarray:
        n = self._lattice_size(lam_h, lam_a, disp)

        # Bivariate Poisson split: X = U + W, Y = V + W, with W the shared tempo component.
        lam_c = self.PRIOR_SHARED_FRACTION * min(lam_h, lam_a)
        l1 = max(lam_h - lam_c, 1e-9)
        l2 = max(lam_a - lam_c, 1e-9)

        p1 = self._marginal(l1, disp, n)
        p2 = self._marginal(l2, disp, n)
        pc = self._poisson_pmf(lam_c, n)

        indep = np.outer(p1, p2)
        mat = np.zeros((n + 1, n + 1))
        for w in range(n + 1):
            if pc[w] < 1e-16:
                continue
            mat[w:, w:] += pc[w] * indep[: n + 1 - w, : n + 1 - w]

        # Dixon-Coles low-score correction. The value of rho is limited
        # analytically so that every adjusted cell stays non-negative.
        rho = self.PRIOR_DC_RHO
        rho_lo = max(-1.0 / max(lam_h, 1e-9), -1.0 / max(lam_a, 1e-9))
        rho_hi = min(1.0 / max(lam_h * lam_a, 1e-9), 1.0)
        rho = min(max(rho, 0.95 * rho_lo), 0.95 * rho_hi)

        mat[0, 0] *= 1.0 - lam_h * lam_a * rho
        mat[0, 1] *= 1.0 + lam_h * rho
        mat[1, 0] *= 1.0 + lam_a * rho
        mat[1, 1] *= 1.0 - rho

        mat = np.clip(mat, 0.0, None)
        s = mat.sum()
        return mat / s if s > 0 else mat

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def compute_intensities(self, game_context: Dict[str, Any]) -> Tuple[float, float, float]:
        ctx = game_context or {}
        has_xg = (
            self._get(ctx, "home_expected", "home_xg", "lambda_home") is not None
            and self._get(ctx, "away_expected", "away_xg", "lambda_away") is not None
        )
        lam_h, lam_a = self._base_intensities(ctx)
        adj_h, adj_a = self._situational_log_adjust(ctx, has_xg)
        lam_h *= math.exp(adj_h)
        lam_a *= math.exp(adj_a)

        lam_h = max(lam_h, 1e-6)
        lam_a = max(lam_a, 1e-6)
        lam_h, lam_a = self._limit_home_advantage(lam_h, lam_a)

        disp = self._get(ctx, "dispersion_index", "dispersion")
        if disp is None or disp <= 0:
            disp = self.PRIOR_DISPERSION
        # Shrink any supplied dispersion toward the empirical prior. A first
        # half has fewer events, so less overdispersion can be identified.
        disp = 1.0 + 0.5 * max(disp - 1.0, 0.0)
        return lam_h, lam_a, disp

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        lam_h, lam_a, disp = self.compute_intensities(game_context)
        mat = self._score_matrix(lam_h, lam_a, disp)

        p_home = float(np.tril(mat, -1).sum())
        p_draw = float(np.trace(mat))
        p_away = float(np.triu(mat, 1).sum())
        z = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / z, p_draw / z, p_away / z

        # Prefer the full score lattice so that the engine can price both
        # the winner and the totals legs from one consistent joint distribution.
        scores = {}
        n = mat.shape[0]
        for i in range(n):
            for j in range(n):
                v = float(mat[i, j])
                if v > 1e-14:
                    scores[(i, j)] = v

        dist = None
        if hasattr(DiscreteDistribution, "from_scores"):
            try:
                dist = DiscreteDistribution.from_scores(scores)
            except Exception:
                dist = None
        if dist is None and hasattr(DiscreteDistribution, "from_probabilities"):
            try:
                dist = DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
            except Exception:
                dist = None
        if dist is None:
            dist = DiscreteDistribution(
                probabilities={"home": p_home, "draw": p_draw, "away": p_away}
            )

        # Attach diagnostics without breaking objects that do not allow new attributes.
        try:
            totals_pmf = np.zeros(2 * n - 1)
            for i in range(n):
                for j in range(n):
                    totals_pmf[i + j] += mat[i, j]
            setattr(dist, "metadata", {
                "lambda_home": lam_h,
                "lambda_away": lam_a,
                "dispersion": disp,
                "p_home": p_home,
                "p_draw": p_draw,
                "p_away": p_away,
                "expected_total": float(np.dot(np.arange(len(totals_pmf)), totals_pmf)),
                "p_over_0_5": float(1.0 - totals_pmf[0]),
                "p_over_1_5": float(1.0 - totals_pmf[:2].sum()),
                "p_over_2_5": float(1.0 - totals_pmf[:3].sum()),
                "model_id": self.model_id,
            })
        except Exception:
            pass

        return dist