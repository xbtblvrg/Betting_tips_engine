"""
Candidate model: BUNDESLIGA | half_1 | spreads

Structure
---------
1. First-half intensity: the margin is a (possibly over-dispersed) Skellam on
   integrated intensities  Lambda_i = int_0^{45+s} lambda_i(t) dt,
   with lambda_i(t) = lambda_bar_i * g(t) and g(t) an increasing hazard. When
   only full-match expectations are supplied, the first-half share
   int_0^{45+s1} g / int_0^{90+s1+s2} g converts them to half-1 intensities.
2. Home advantage: the home share of the first-half intensity is shrunk in
   logit space toward the league first-half home/away ratio. The weight is
   the credibility of the supplied expectations. This limits home-field
   over-confidence, because first-half home advantage is muted.
3. Low-score dependence: a Dixon-Coles tau(x, y; rho) correction is applied.
   rho is a credibility-weighted blend of a segment estimate and the league
   full-match prior. At cold start (n = 0) it is the pure prior. rho is
   bounded by the analytic positivity region of tau, not by an ad-hoc clamp.
4. Pricing: Asian half, whole and quarter handicaps are priced by summing
   exact margin mass. Push and split-stake settlement are handled exactly,
   with no normal approximation.

Every structural prior is gathered in PRIORS. Each one is a named, documented
quantity that can be overridden through the constructor or refit when data
arrives. Numeric tolerances (tail mass, epsilons) are computational, not
modeling, choices.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import integrate, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may be exported from base or pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------- #
# Structural priors (league domain knowledge; every entry is overridable)
# --------------------------------------------------------------------------- #
PRIORS: Dict[str, float] = {
    # Empirical first-half anchors for the segment
    "league_h1_home_mean": 0.80,
    "league_h1_away_mean": 0.60,
    # Goal-timing hazard g(t) = 1 + beta * t / 90 (normalized below).
    # beta > 0 means intensity rises through the match.
    "timing_hazard_slope": 0.35,
    "stoppage_h1_minutes": 2.5,
    "stoppage_h2_minutes": 5.0,
    # Dixon-Coles rho prior (full-match Bundesliga scale) and its credibility
    # constant. rho_post = (n * rho_seg + k * rho_prior) / (n + k)
    "dc_rho_prior": -0.08,
    "dc_rho_credibility_k": 400.0,
    # Credibility of supplied expectations for the home share. Logit share is
    # w * ctx + (1 - w) * league, where w = r / (r + k_share).
    "share_credibility_k": 1.0,
    "default_expectation_reliability": 1.0,
    # Dispersion index Var/Mean of first-half goals (1.0 means Poisson)
    "dispersion_index": 1.0,
    # Rest-day elasticity on log-intensity (first half is less fatigue-exposed)
    "rest_log_elasticity": 0.03,
    # Tempo elasticity on total log-intensity
    "pace_elasticity": 1.0,
    # Settlement eligibility floor (decimal odds), per specification
    "min_decimal_odds": 1.40,
}

_TAIL_EPS = 1e-12  # numerical truncation tolerance for the score lattice


class BundesligaHalf1SpreadsModel(BaseCandidateModel):
    """First-half Bundesliga Asian handicap model.

    Uses a timing-integrated Skellam margin with a Dixon-Coles low-score
    correction.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_half_1_1108_011800",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "half_1",
        family: str = "spreads",
        priors: Optional[Dict[str, float]] = None,
        segment_sample_size: int = 0,
        segment_rho_estimate: Optional[float] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:  # tolerate positional-only base signatures
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors: Dict[str, float] = dict(PRIORS)
        if priors:
            self.priors.update(priors)
        self.segment_sample_size = int(max(segment_sample_size, 0))
        self.segment_rho_estimate = segment_rho_estimate
        self._last_matrix: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ #
    # Goal-timing hazard
    # ------------------------------------------------------------------ #
    def _g(self, t: float) -> float:
        return 1.0 + self.priors["timing_hazard_slope"] * t / 90.0

    def first_half_share(self) -> float:
        """Fraction of full-match intensity accumulated in the first half."""
        s1 = self.priors["stoppage_h1_minutes"]
        s2 = self.priors["stoppage_h2_minutes"]
        h1, _ = integrate.quad(self._g, 0.0, 45.0 + s1)
        # The second half restarts at minute 45, so its clock is offset
        h2, _ = integrate.quad(self._g, 45.0, 90.0 + s2)
        return h1 / (h1 + h2)

    # ------------------------------------------------------------------ #
    # Intensities
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _rating(self, side: Any, key: str) -> Optional[float]:
        if isinstance(side, dict):
            v = self._num(side.get(key))
            return v if v is not None and v > 0 else None
        return None

    def _base_intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.priors
        lh = self._num(ctx.get("home_expected"))
        la = self._num(ctx.get("away_expected"))
        scope_tag = str(ctx.get("expected_scope", "half_1")).lower()

        if lh is not None and la is not None and lh > 0 and la > 0:
            if scope_tag in ("full", "full_game", "match", "ft"):
                share = self.first_half_share()
                lh, la = lh * share, la * share
            return lh, la

        # Fallback: league anchors scaled by multiplicative attack/defense
        # ratings, where 1.0 is the league average
        lh = p["league_h1_home_mean"]
        la = p["league_h1_away_mean"]
        h, a = ctx.get("home"), ctx.get("away")
        ha, hd = self._rating(h, "attack"), self._rating(h, "defense")
        aa, ad = self._rating(a, "attack"), self._rating(a, "defense")
        if ha is not None:
            lh *= ha
        if ad is not None:
            lh *= ad
        if aa is not None:
            la *= aa
        if hd is not None:
            la *= hd
        return lh, la

    def _situational(self, ctx: Dict[str, Any], lh: float, la: float) -> Tuple[float, float]:
        p = self.priors

        # Rest asymmetry: log-ratio of (rest + 1), with diminishing returns
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            d = p["rest_log_elasticity"] * math.log((rh + 1.0) / (ra + 1.0))
            lh *= math.exp(d)
            la *= math.exp(-d)

        # Tempo: rescale the total and preserve the share
        pace = self._num(ctx.get("pace", ctx.get("tempo")))
        league_pace = self._num(ctx.get("league_pace"))
        if pace and league_pace and pace > 0 and league_pace > 0:
            m = (pace / league_pace) ** p["pace_elasticity"]
            lh, la = lh * m, la * m
        return lh, la

    def _shrink_home_share(self, ctx: Dict[str, Any], lh: float, la: float) -> Tuple[float, float]:
        """Shrink the logit home share toward the league first-half ratio.

        The total intensity is preserved.
        """
        p = self.priors
        total = lh + la
        share_ctx = lh / total
        share_lg = p["league_h1_home_mean"] / (
            p["league_h1_home_mean"] + p["league_h1_away_mean"]
        )
        rel = self._num(ctx.get("expectation_reliability"))
        if rel is None or rel < 0:
            rel = p["default_expectation_reliability"]
        w = rel / (rel + p["share_credibility_k"])
        z = w * special.logit(share_ctx) + (1.0 - w) * special.logit(share_lg)
        s = float(special.expit(z))
        return total * s, total * (1.0 - s)

    def intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        lh, la = self._base_intensities(ctx)
        lh, la = self._situational(ctx, lh, la)
        lh, la = self._shrink_home_share(ctx, lh, la)
        return lh, la

    # ------------------------------------------------------------------ #
    # Marginals and dependence
    # ------------------------------------------------------------------ #
    def _marginal_pmf(self, lam: float, nmax: int) -> np.ndarray:
        k = np.arange(nmax + 1)
        di = self.priors["dispersion_index"]
        if di > 1.0 + 1e-9:
            # Gamma-Poisson mixture: Var = mu * DI, so r = mu / (DI - 1)
            r = lam / (di - 1.0)
            pmf = stats.nbinom.pmf(k, r, r / (r + lam))
        else:
            pmf = stats.poisson.pmf(k, lam)
        return pmf

    def _nmax(self, lh: float, la: float) -> int:
        di = max(self.priors["dispersion_index"], 1.0)
        lam = max(lh, la)
        n = int(stats.poisson.ppf(1.0 - _TAIL_EPS, lam * di)) + 2
        return max(n, 6)

    def _rho(self, lh: float, la: float) -> float:
        p = self.priors
        n = self.segment_sample_size
        k = p["dc_rho_credibility_k"]
        rho_seg = (
            self.segment_rho_estimate
            if self.segment_rho_estimate is not None
            else p["dc_rho_prior"]
        )
        rho = (n * rho_seg + k * p["dc_rho_prior"]) / (n + k)
        # Analytic positivity region of tau
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)
        return float(np.clip(rho, lo, hi))

    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        lh, la = self.intensities(ctx)
        n = self._nmax(lh, la)
        M = np.outer(self._marginal_pmf(lh, n), self._marginal_pmf(la, n))
        rho = self._rho(lh, la)
        M[0, 0] *= 1.0 - lh * la * rho
        M[0, 1] *= 1.0 + lh * rho
        M[1, 0] *= 1.0 + la * rho
        M[1, 1] *= 1.0 - rho
        M = np.clip(M, 0.0, None)
        M /= M.sum()
        self._last_matrix = M
        return M

    # ------------------------------------------------------------------ #
    # Distribution interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        M = self.score_matrix(game_context or {})
        scores = {
            (int(i), int(j)): float(M[i, j])
            for i in range(M.shape[0])
            for j in range(M.shape[1])
            if M[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            p_home = float(np.tril(M, -1).sum())
            p_draw = float(np.trace(M))
            p_away = float(np.triu(M, 1).sum())
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)

    # ------------------------------------------------------------------ #
    # Exact margin mass and Asian handicap settlement
    # ------------------------------------------------------------------ #
    @staticmethod
    def margin_pmf(M: np.ndarray) -> Dict[int, float]:
        n = M.shape[0]
        out: Dict[int, float] = {}
        for d in range(-(n - 1), n):
            out[d] = float(np.trace(M, offset=-d))  # home minus away equals d
        return out

    @staticmethod
    def _settle_single(margin: Dict[int, float], line: float) -> Tuple[float, float, float]:
        """Return (win, push, loss) for a home bet at a half or whole line.

        The adjusted result is margin + line.
        """
        w = p = l = 0.0
        for d, m in margin.items():
            r = d + line
            if r > 1e-9:
                w += m
            elif r < -1e-9:
                l += m
            else:
                p += m
        return w, p, l

    def settle(self, margin: Dict[int, float], line: float, side: str) -> Tuple[float, float, float]:
        """Return (win, push, loss) for an Asian handicap.

        side is 'home' (the line is applied to home) or 'away'. Quarter
        lines are split stakes: each half of the stake is settled at
        line - 0.25 and line + 0.25.
        """
        if side == "away":
            margin = {-d: m for d, m in margin.items()}
        frac = abs(line * 4.0) % 2.0
        if abs(frac - 1.0) < 1e-9:  # quarter line
            a = self._settle_single(margin, line - 0.25)
            b = self._settle_single(margin, line + 0.25)
            return tuple(0.5 * (x + y) for x, y in zip(a, b))  # type: ignore
        return self._settle_single(margin, line)

    def expected_value(
        self, game_context: Dict[str, Any], line: float, side: str, decimal_odds: float
    ) -> Dict[str, float]:
        """Return expected profit per unit stake under exact settlement.

        Quarter lines are linear in their split halves, so averaging the two
        (win, push, loss) triples and applying full-win, push and loss
        payoffs is exact.
        """
        M = self.score_matrix(game_context or {})
        margin = self.margin_pmf(M)
        w, p, l = self.settle(margin, line, side)
        ev = w * (decimal_odds - 1.0) - l
        eligible = decimal_odds >= self.priors["min_decimal_odds"] and ev > 0.0
        fair = (w + l) / w if w > 0 else float("inf")  # push-neutral fair odds
        return {
            "p_win": w,
            "p_push": p,
            "p_loss": l,
            "ev": ev,
            "fair_odds": fair,
            "eligible": float(eligible),
        }


# Alias for loaders that discover a conventional class name
CandidateModel = BundesligaHalf1SpreadsModel