"""
Candidate model for WNBA | quarter_1 | winner_total.

Model structure, derived from how basketball possessions work:

* Possession counting. A quarter is a fixed 10-minute window filled by a
  renewal process of possessions. Possession durations have coefficient of
  variation c_v, bounded above by the 24-second shot clock. Renewal theory
  then gives the combined possession count M approximately
  Normal(m, m * c_v^2). Possessions alternate, so the team that wins the
  jump ball (a 50/50 event) gets ceil(M/2) and the other gets floor(M/2).
  Both teams therefore share one pace shock. That shared shock creates the
  positive correlation between home and away scores, which matters for
  totals.

* Scoring per possession. Each possession yields X in {0, 1, 2, 3, 4} points.
  A structural prior pmf for X is exponentially tilted, solving one
  parameter with brentq, so that E[X] equals the situational
  points-per-possession target exactly.

* Exact score lattice. P(H = h, A = a) = sum_M P(M) * sum_jump f_h^{*N_h}(h) * f_a^{*N_a}(a).
  Here f^{*n} is the n-fold convolution. The lattice is exact, with no
  normal approximation of the scores. It produces sub-Poisson tie mass and
  correct even/odd lattice effects, and its dispersion follows from
  Var = E[N] Var(X) + Var(N) E[X]^2. With WNBA-like possession pmfs, the
  per-team variance-to-mean ratio comes out around 1.3–1.5 instead of an
  assumed Poisson value of 1.0.

* Situational inputs:
  - home_expected and away_expected anchor the scoring means. They default
    to the empirical means 20.00 and 18.75.
  - Attack and defense ratings apply a log-linear, partially pooled
    adjustment. The pooling avoids double counting with the expected values.
  - Pace or tempo scales the mean possession count.
  - Rest days apply a saturating fatigue multiplier on efficiency.
  - Margin shrinkage pulls the expected margin toward zero while keeping the
    total unchanged. This limits home-court over-confidence in a noisy
    10-minute window.

Note: this is a cold-start structural prior. Every tunable value is a named
constructor parameter, not a hidden clamp. The model makes no claim to reach
the referenced historical ROI or Brier benchmarks. It must be validated
out-of-sample with walk-forward settlement before going live.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # usually pre-loaded in the execution scope; import defensively
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class WNBAQuarter1WinnerTotalModel(BaseCandidateModel):
    """Possession-renewal compound lattice model for WNBA first-quarter winner and total markets."""

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_1_1107_170227",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_1",
        family: str = "winner_total",
        # --- structural domain priors (all overridable) ---
        empirical_home_mean: float = 20.00,
        empirical_away_mean: float = 18.75,
        possessions_per_team_q: float = 20.0,  # roughly 80 possessions per 40 minutes, divided by 4
        possession_duration_cv: float = 0.55,  # shot clock truncates the duration distribution
        # Prior possession outcome shape for {0, 1, 2, 3, 4} points
        # (empty trip, FT split, two-point make/FT pair, three/and-one, four-point play)
        possession_shape: Tuple[float, ...] = (0.50, 0.06, 0.30, 0.13, 0.01),
        rating_pooling: float = 0.5,  # weight on attack/defense beyond the expected values
        rest_fatigue_amplitude: float = 0.03,  # maximum efficiency loss on zero rest
        rest_decay_days: float = 1.0,  # fatigue recovery time constant in days
        reference_rest_days: float = 2.0,  # typical WNBA rest between games
        margin_shrinkage: float = 0.15,  # pull the Q1 margin toward zero, total preserved
        tail_mass: float = 1e-10,  # lattice truncation tolerance
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.empirical_home_mean = float(empirical_home_mean)
        self.empirical_away_mean = float(empirical_away_mean)
        self.possessions_per_team_q = float(possessions_per_team_q)
        self.possession_duration_cv = float(possession_duration_cv)
        shape = np.asarray(possession_shape, dtype=float)
        self.possession_shape = shape / shape.sum()
        self.point_values = np.arange(len(shape), dtype=float)
        self.rating_pooling = float(rating_pooling)
        self.rest_fatigue_amplitude = float(rest_fatigue_amplitude)
        self.rest_decay_days = float(rest_decay_days)
        self.reference_rest_days = float(reference_rest_days)
        self.margin_shrinkage = float(margin_shrinkage)
        self.tail_mass = float(tail_mass)

        self._last_lattice: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ #
    # Context parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Accepts ctx[side][kind], ctx[f'{side}_{kind}'] or ctx[side][f'{kind}_rating']."""
        team = ctx.get(side)
        if isinstance(team, dict):
            for key in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = self._num(team.get(key))
                if v is not None:
                    return v
        for key in (f"{side}_{kind}", f"{side}_{kind}_rating"):
            v = self._num(ctx.get(key))
            if v is not None:
                return v
        return None

    @staticmethod
    def _rating_to_log_multiplier(r: Optional[float]) -> float:
        """
        Convert a rating to a log multiplier.

        Ratios near 1 are used as multiplicative. Ratings on a per-100 scale
        (for example 105) are treated relative to 100.
        """
        if r is None or r <= 0:
            return 0.0
        if r > 20.0:  # efficiency-style scale (points per 100 possessions)
            return math.log(r / 100.0)
        return math.log(r)

    def _fatigue_multiplier(self, rest_days: Optional[float]) -> float:
        if rest_days is None or rest_days < 0:
            return 1.0
        g = lambda r: 1.0 - self.rest_fatigue_amplitude * math.exp(-r / self.rest_decay_days)
        return g(rest_days) / g(self.reference_rest_days)

    def _pace_multiplier(self, ctx: Dict[str, Any]) -> float:
        for key in ("pace", "tempo", "expected_pace"):
            v = self._num(ctx.get(key))
            if v is not None and v > 0:
                # Per-40-minute possessions vs. a relative index near 1
                return (v / 4.0) / self.possessions_per_team_q if v > 10.0 else v
        hp = self._num(ctx.get("home_pace"))
        ap = self._num(ctx.get("away_pace"))
        if hp and ap and hp > 0 and ap > 0:
            v = math.sqrt(hp * ap)
            return (v / 4.0) / self.possessions_per_team_q if v > 10.0 else v
        return 1.0

    # ------------------------------------------------------------------ #
    # Situational means
    # ------------------------------------------------------------------ #
    def _situational_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        mu_h = self._num(ctx.get("home_expected"))
        mu_a = self._num(ctx.get("away_expected"))
        mu_h = mu_h if mu_h and mu_h > 0 else self.empirical_home_mean
        mu_a = mu_a if mu_a and mu_a > 0 else self.empirical_away_mean

        # The expected values may be full-game figures. If so, rescale them to
        # the empirical quarter scale while keeping the home/away ratio.
        anchor_total = self.empirical_home_mean + self.empirical_away_mean
        if mu_h + mu_a > 2.5 * anchor_total:
            scale = (mu_h + mu_a) / anchor_total
            mu_h, mu_a = mu_h / scale, mu_a / scale

        # Partially pooled attack/defense adjustment (log-linear).
        w = self.rating_pooling
        lh = self._rating_to_log_multiplier(self._team_rating(ctx, "home", "attack")) + \
             self._rating_to_log_multiplier(self._team_rating(ctx, "away", "defense"))
        la = self._rating_to_log_multiplier(self._team_rating(ctx, "away", "attack")) + \
             self._rating_to_log_multiplier(self._team_rating(ctx, "home", "defense"))
        mu_h *= math.exp(w * 0.5 * lh)
        mu_a *= math.exp(w * 0.5 * la)

        # Rest and fatigue act on each team's own offensive efficiency.
        mu_h *= self._fatigue_multiplier(self._num(ctx.get("home_rest_days")))
        mu_a *= self._fatigue_multiplier(self._num(ctx.get("away_rest_days")))

        # Margin shrinkage keeps the total fixed and limits Q1 home over-confidence.
        centre = 0.5 * (mu_h + mu_a)
        half_margin = 0.5 * (mu_h - mu_a) * (1.0 - self.margin_shrinkage)
        mu_h, mu_a = centre + half_margin, centre - half_margin

        possessions = self.possessions_per_team_q * self._pace_multiplier(ctx)
        return max(mu_h, 1e-6), max(mu_a, 1e-6), max(possessions, 1.0)

    # ------------------------------------------------------------------ #
    # Possession outcome pmf: exponential tilt matching target PPP
    # ------------------------------------------------------------------ #
    def _possession_pmf(self, target_ppp: float) -> np.ndarray:
        k, b = self.point_values, self.possession_shape
        upper = k[b > 0].max()
        target = min(max(target_ppp, 1e-6), upper - 1e-6)

        def tilted(theta: float) -> np.ndarray:
            logw = np.log(np.where(b > 0, b, 1e-300)) + theta * k
            logw -= logw.max()
            w = np.exp(logw) * (b > 0)
            return w / w.sum()

        f = lambda th: float(tilted(th) @ k) - target
        lo, hi = -5.0, 5.0
        while f(lo) > 0 and lo > -100.0:
            lo *= 2.0
        while f(hi) < 0 and hi < 100.0:
            hi *= 2.0
        if f(lo) > 0:
            theta = lo
        elif f(hi) < 0:
            theta = hi
        else:
            theta = brentq(f, lo, hi, xtol=1e-12)
        return tilted(theta)

    # ------------------------------------------------------------------ #
    # Possession count distribution (renewal process, shared pace shock)
    # ------------------------------------------------------------------ #
    def _combined_possession_pmf(self, per_team: float) -> Dict[int, float]:
        m = 2.0 * per_team
        s = math.sqrt(max(m * self.possession_duration_cv ** 2, 1e-6))
        z = norm.isf(self.tail_mass)
        lo, hi = max(0, int(math.floor(m - z * s))), int(math.ceil(m + z * s))
        ks = np.arange(lo, hi + 1)
        probs = norm.cdf((ks + 0.5 - m) / s) - norm.cdf((ks - 0.5 - m) / s)
        probs = probs / probs.sum()
        return {int(k): float(p) for k, p in zip(ks, probs) if p > 0}

    # ------------------------------------------------------------------ #
    # Exact joint score lattice
    # ------------------------------------------------------------------ #
    def score_lattice(self, game_context: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a, per_team = self._situational_means(game_context)
        m_pmf = self._combined_possession_pmf(per_team)

        # Expected possessions per team, E[N] = E[M]/2, sets points per possession.
        expected_n = sum(k * p for k, p in m_pmf.items()) / 2.0
        f_h = self._possession_pmf(mu_h / expected_n)
        f_a = self._possession_pmf(mu_a / expected_n)

        n_max = max(m_pmf) // 2 + 1
        max_pts = int(self.point_values[-1]) * n_max
        conv_h, conv_a = self._convolution_powers(f_h, n_max), self._convolution_powers(f_a, n_max)

        lattice = np.zeros((max_pts + 1, max_pts + 1))
        for m, pm in m_pmf.items():
            n_lo, n_hi = m // 2, m - m // 2
            splits = [(n_lo, n_hi)] if n_lo == n_hi else [(n_hi, n_lo), (n_lo, n_hi)]
            for n_h, n_a in splits:
                w = pm / len(splits)
                ph, pa = conv_h[n_h], conv_a[n_a]
                lattice[: len(ph), : len(pa)] += w * np.outer(ph, pa)

        lattice /= lattice.sum()
        # Trim negligible tails.
        rows = np.where(lattice.sum(axis=1) > self.tail_mass)[0]
        cols = np.where(lattice.sum(axis=0) > self.tail_mass)[0]
        lattice = lattice[: rows.max() + 1, : cols.max() + 1]
        lattice /= lattice.sum()
        self._last_lattice = lattice
        return lattice

    @staticmethod
    def _convolution_powers(f: np.ndarray, n_max: int) -> Dict[int, np.ndarray]:
        powers = {0: np.array([1.0])}
        cur = powers[0]
        for n in range(1, n_max + 1):
            cur = np.convolve(cur, f)
            powers[n] = cur
        return powers

    # ------------------------------------------------------------------ #
    # Market summaries
    # ------------------------------------------------------------------ #
    @staticmethod
    def outcome_probabilities(lattice: np.ndarray) -> Tuple[float, float, float]:
        p_home = float(np.tril(lattice, -1).sum())
        p_draw = float(np.trace(lattice))
        p_away = float(np.triu(lattice, 1).sum())
        z = p_home + p_draw + p_away
        return p_home / z, p_draw / z, p_away / z

    @staticmethod
    def total_probabilities(lattice: np.ndarray, line: float) -> Dict[str, float]:
        h, a = np.indices(lattice.shape)
        tot = h + a
        return {
            "over": float(lattice[tot > line].sum()),
            "under": float(lattice[tot < line].sum()),
            "push": float(lattice[tot == line].sum()),
        }

    @staticmethod
    def winner_total_joint(lattice: np.ndarray, line: float) -> Dict[str, float]:
        """Joint winner × total probabilities (for example 'home&over')."""
        h, a = np.indices(lattice.shape)
        tot = h + a
        out: Dict[str, float] = {}
        for wname, wmask in (("home", h > a), ("draw", h == a), ("away", h < a)):
            for tname, tmask in (("over", tot > line), ("under", tot < line), ("push", tot == line)):
                out[f"{wname}&{tname}"] = float(lattice[wmask & tmask].sum())
        return out

    def moments(self, lattice: np.ndarray) -> Dict[str, float]:
        h, a = np.indices(lattice.shape)
        eh, ea = float((lattice * h).sum()), float((lattice * a).sum())
        vh = float((lattice * (h - eh) ** 2).sum())
        va = float((lattice * (a - ea) ** 2).sum())
        cov = float((lattice * (h - eh) * (a - ea)).sum())
        return {
            "home_mean": eh, "away_mean": ea,
            "home_dispersion": vh / eh if eh > 0 else float("nan"),
            "away_dispersion": va / ea if ea > 0 else float("nan"),
            "correlation": cov / math.sqrt(vh * va) if vh > 0 and va > 0 else 0.0,
        }

    @staticmethod
    def expected_value(prob: float, decimal_odds: float, min_odds: float = 1.40) -> Optional[float]:
        """EV per unit stake. Returns None when below the eligibility floor (odds >= 1.40)."""
        if decimal_odds is None or decimal_odds < min_odds:
            return None
        return prob * decimal_odds - 1.0

    # ------------------------------------------------------------------ #
    # Required interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lattice = self.score_lattice(ctx)

        # Prefer the full score lattice so winner, total and joint markets
        # can all be priced from it.
        builder = getattr(DiscreteDistribution, "from_scores", None)
        if callable(builder):
            for payload in (
                lattice,
                {(int(i), int(j)): float(lattice[i, j])
                 for i, j in zip(*np.nonzero(lattice > self.tail_mass))},
            ):
                try:
                    return builder(payload)
                except Exception:
                    continue

        p_h, p_d, p_a = self.outcome_probabilities(lattice)
        try:
            return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)
        except Exception:
            return DiscreteDistribution(probabilities={"home": p_h, "draw": p_d, "away": p_a})


CandidateModel = WNBAQuarter1WinnerTotalModel