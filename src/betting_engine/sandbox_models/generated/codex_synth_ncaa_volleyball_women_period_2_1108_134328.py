"""
Candidate model: NCAA_VOLLEYBALL_WOMEN | period_2 | moneyline (Set 2 winner).

Architecture (first principles, rally-point volleyball):
  1. Set 2 is a single rally-scored set to 25, win-by-2, with no draw. Its
     outcome is a function of two rally-level quantities: each team's side-out
     probability (winning a rally while receiving).
  2. A strength signal is built from 'home_expected' / 'away_expected'
     (empirical baselines 0.53 / 0.40). It is converted to a set-win target
     under a Jeffreys Beta(1/2, 1/2) prior. Signals with small magnitude are
     shrunk toward 0.5 automatically, which limits home-court over-confidence
     in a cold-start market.
  3. The target is inverted through an exact rally-lattice solver (DP plus a
     closed-form deuce solution) into a rally-level log-odds gap d.
  4. Situational residuals (attack/defense matchups, rest, neutral venue) are
     added on the set-logit scale with inverse-variance weights, mapped back to
     rally scale through the local Jacobian of the lattice, and propagated
     through the lattice again. This keeps margin mass consistent with the
     sport's scoring structure.
  5. The set-2 first server is unknown pre-match. Alternation depends on set 1,
     so the model integrates over both serving states with equal weight.

No bookmaker prices are blended into the forecast.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.special import expit, logit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies across engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pre-loaded in execution scope
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NcaaVolleyballWomenPeriod2MoneylineModel(BaseCandidateModel):
    # Domain structure of a non-deciding NCAA women's set (sets 1-4).
    SET_TARGET_POINTS = 25
    WIN_BY = 2
    # Domain prior: NCAA Division I women's side-out rate is roughly 0.58-0.62.
    # It can be overridden per game via game_context['sideout_rate'].
    PRIOR_SIDEOUT_RATE = 0.60
    # Empirical segment baselines from the forensic report.
    BASELINE_HOME = 0.53
    BASELINE_AWAY = 0.40
    # Jeffreys prior pseudo-counts: Beta(1/2, 1/2).
    JEFFREYS_ALPHA = 0.5

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_volleyball_women_period_2_1108_134328",
        version: str = "1.0.0",
        league: str = "NCAA_VOLLEYBALL_WOMEN",
        scope: str = "period_2",
        family: str = "moneyline",
        min_odds: float = 1.40,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.min_odds = float(min_odds)

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Accept flat keys ('home_attack') or nested dicts (ctx['home']['attack'])."""
        v = self._num(ctx.get(f"{side}_{key}"))
        if v is not None:
            return v
        nested = ctx.get(side)
        if isinstance(nested, dict):
            return self._num(nested.get(key))
        return None

    # ------------------------------------------------------------------ #
    # Rally lattice: exact set-win probability
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rally_probs(d: float, so0: float) -> Tuple[float, float]:
        """
        Rally-level log-odds gap d splits symmetrically around the base
        side-out logit.
          so_h: P(home wins rally | away serving)
          q_h : P(home wins rally | home serving) = 1 - so_a
        """
        base = logit(so0)
        so_h = float(expit(base + 0.5 * d))
        so_a = float(expit(base - 0.5 * d))
        return so_h, 1.0 - so_a

    @staticmethod
    def _deuce(so_h: float, q_h: float) -> Tuple[float, float]:
        """
        Closed form for a tied score at or beyond (T-1, T-1).
        Returns (D_H, D_A) = P(home wins set | tie, home serves / away serves).
          D_H = q_h*(q_h + (1-q_h)*D_A) + (1-q_h)*so_h*D_H
          D_A = so_h*(q_h + (1-q_h)*D_A) + (1-so_h)*so_h*D_H
        """
        A = np.array(
            [
                [1.0 - (1.0 - q_h) * so_h, -q_h * (1.0 - q_h)],
                [-(1.0 - so_h) * so_h, 1.0 - so_h * (1.0 - q_h)],
            ]
        )
        b = np.array([q_h * q_h, so_h * q_h])
        dh, da = np.linalg.solve(A, b)
        return float(dh), float(da)

    def _set_win_prob(self, d: float, so0: float) -> float:
        so_h, q_h = self._rally_probs(d, so0)
        d_h, d_a = self._deuce(so_h, q_h)
        T = self.SET_TARGET_POINTS
        W = self.WIN_BY
        memo: Dict[Tuple[int, int, int], float] = {}

        def V(i: int, j: int, s: int) -> float:  # s = 1 if home serving
            if i >= T and i - j >= W:
                return 1.0
            if j >= T and j - i >= W:
                return 0.0
            if i == j and i >= T - 1:
                return d_h if s == 1 else d_a
            key = (i, j, s)
            if key in memo:
                return memo[key]
            p = q_h if s == 1 else so_h  # P(home wins this rally)
            val = p * V(i + 1, j, 1) + (1.0 - p) * V(i, j + 1, 0)
            memo[key] = val
            return val

        # The set-2 first server is unknown pre-match: integrate both states.
        return 0.5 * (V(0, 0, 1) + V(0, 0, 0))

    def _invert(self, target: float, so0: float) -> float:
        """Find the rally gap d such that the lattice set-win probability equals target."""
        target = float(np.clip(target, np.finfo(float).eps, 1.0 - np.finfo(float).eps))
        f = lambda d: self._set_win_prob(d, so0) - target
        lo, hi = -1.0, 1.0
        while f(lo) > 0.0 and lo > -50.0:
            lo *= 2.0
        while f(hi) < 0.0 and hi < 50.0:
            hi *= 2.0
        return float(brentq(f, lo, hi, xtol=1e-10))

    def _jacobian(self, d: float, so0: float) -> float:
        """d(set logit)/d(rally gap), by central difference at the operating point."""
        h = 1e-4
        p_up = self._set_win_prob(d + h, so0)
        p_dn = self._set_win_prob(d - h, so0)
        return float((logit(p_up) - logit(p_dn)) / (2.0 * h))

    # ------------------------------------------------------------------ #
    # Strength signals
    # ------------------------------------------------------------------ #
    def _shrunk_share(self, h: float, a: float) -> float:
        """Jeffreys posterior mean: small-magnitude signals shrink toward 0.5."""
        k = self.JEFFREYS_ALPHA
        return (h + k) / (h + a + 2.0 * k)

    def _matchup_logit(self, ctx: Dict[str, Any]) -> Optional[float]:
        ha, hd = self._get(ctx, "home", "attack"), self._get(ctx, "home", "defense")
        aa, ad = self._get(ctx, "away", "attack"), self._get(ctx, "away", "defense")
        if None in (ha, hd, aa, ad) or min(ha, hd, aa, ad) <= 0.0:
            return None
        # Multiplicative attack/defense exposure. Defense is treated as
        # "points conceded" intensity: higher means weaker.
        return math.log(ha / ad) - math.log(aa / hd) if False else (
            math.log(ha) + math.log(ad) - math.log(aa) - math.log(hd)
        )

    @staticmethod
    def _rest_logit(ctx: Dict[str, Any], getter) -> Optional[float]:
        rh, ra = getter(ctx, "home", "rest_days"), getter(ctx, "away", "rest_days")
        if rh is None or ra is None:
            return None
        rh, ra = max(rh, 0.0), max(ra, 0.0)
        # Saturating recovery fraction 1 - e^{-r}, floored at the one-day level
        # so back-to-back matches are penalized without diverging.
        rec = lambda r: 1.0 - math.exp(-max(r, 1.0))
        return math.log(rec(rh) / rec(ra))

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        so0 = self._num(ctx.get("sideout_rate"))
        if so0 is None or not (0.0 < so0 < 1.0):
            so0 = self.PRIOR_SIDEOUT_RATE

        h = self._num(ctx.get("home_expected"))
        a = self._num(ctx.get("away_expected"))
        if h is None or h < 0.0:
            h = self.BASELINE_HOME
        if a is None or a < 0.0:
            a = self.BASELINE_AWAY

        base_share = self._shrunk_share(h, a)
        base_logit = float(logit(base_share))

        # Neutral venue: remove the home-court component implied by the
        # empirical segment baselines.
        if bool(ctx.get("neutral_site") or ctx.get("is_neutral")):
            hca = float(logit(self._shrunk_share(self.BASELINE_HOME, self.BASELINE_AWAY)))
            base_logit -= hca

        # Inverse-variance combination of independent noisy estimates of the
        # same latent strength gap. Each available channel gets equal weight.
        residuals = [
            r
            for r in (self._matchup_logit(ctx), self._rest_logit(ctx, self._get))
            if r is not None
        ]
        n_channels = 1 + len(residuals)
        set_logit = base_logit + sum(residuals) / n_channels

        # Route through the rally lattice so the probability respects
        # volleyball scoring structure.
        d_base = self._invert(float(expit(base_logit)), so0)
        slope = self._jacobian(d_base, so0)
        d = d_base + (set_logit - base_logit) / slope if slope > 0.0 else d_base

        p_home = float(np.clip(self._set_win_prob(d, so0), 0.0, 1.0))
        p_away = 1.0 - p_home

        if hasattr(DiscreteDistribution, "from_probabilities"):
            return DiscreteDistribution.from_probabilities(p_home, 0.0, p_away)
        return DiscreteDistribution(probabilities={"home": p_home, "draw": 0.0, "away": p_away})

    def expected_value(self, prob: float, decimal_odds: float) -> float:
        """EV per unit stake: p * (odds - 1) - (1 - p)."""
        return prob * (decimal_odds - 1.0) - (1.0 - prob)

    def is_eligible(self, prob: float, decimal_odds: float) -> bool:
        """Continuous rule: any price at or above min_odds with positive expectation."""
        return decimal_odds >= self.min_odds and self.expected_value(prob, decimal_odds) > 0.0