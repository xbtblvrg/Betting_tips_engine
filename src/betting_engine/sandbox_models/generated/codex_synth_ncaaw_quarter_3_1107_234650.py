"""
Candidate model for NCAAW | quarter_3 | winner_total.

Structure:
  1. One possession count N, shared by both teams, drawn from a negative binomial.
     This links home and away scoring and is the source of their covariance.
  2. Each team's points per possession come from an exponentially tilted packet
     distribution over {0, 1, 2, 3}. The tilt is solved exactly so that the
     quarter means match the anchored home/away expectations.
  3. Pre-game home advantage decays into Q3. The model shifts part of it toward
     neutral while keeping the expected total unchanged.
  4. The joint lattice P(S_home, S_away) is built exactly by convolution,
     conditional on N, then mixed over N.
  5. winner_total contracts are priced on that lattice, with the tie diagonal
     settled explicitly.

All structural parameters are named priors. Any of them can be overridden from
game_context, or re-estimated with the classmethod fitting helpers
(fit_possession_dispersion, fit_q3_hca_retention).
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:
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


class NCAAWQuarter3WinnerTotalModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Domain priors (named, documented, overridable via game_context).
    # ------------------------------------------------------------------ #
    # Empirical anchors for Q3 scoring when context is silent.
    PRIOR_HOME_EXPECTED = 20.00
    PRIOR_AWAY_EXPECTED = 18.75
    # Regulation length and quarter length, in minutes.
    GAME_MINUTES = 40.0
    QUARTER_MINUTES = 10.0
    # Baseline possession-outcome packet shape for NCAAW: empty possession,
    # 1-point packet, 2-point packet (field goal or two free throws), 3-point
    # packet (three-pointer or and-one). Only the shape matters; the mean is
    # re-solved for every team by exponential tilting.
    PRIOR_PACKET_SHAPE = np.array([0.50, 0.06, 0.33, 0.11])
    # Negative-binomial size for quarter possessions. Larger means less
    # overdispersion. Re-estimate with fit_possession_dispersion().
    PRIOR_POSSESSION_K = 60.0
    # Fraction of the pre-game home edge still active in Q3.
    # Re-estimate with fit_q3_hca_retention().
    PRIOR_Q3_HCA_RETENTION = 0.55
    # Prior full-game NCAAW home advantage in points, used only when the
    # context does not supply one.
    PRIOR_GAME_HCA_POINTS = 3.0
    # Log-ppp elasticity per day of rest differential, with diminishing returns
    # through tanh scaling.
    PRIOR_REST_ELASTICITY = 0.012
    PRIOR_REST_SCALE_DAYS = 3.0
    # Shrinkage weight on attack/defense tilts. home_expected and away_expected
    # usually already embed team ratings, so only the residual is applied.
    PRIOR_RATING_SHRINK = 0.25
    # Mass truncation tolerance for the possession support.
    TAIL_EPS = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_3_1107_234650",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_3",
        family: str = "winner_total",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
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
        self._last_lattice: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ #
    # Context helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], key: str, default: float) -> float:
        v = ctx.get(key, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return float(default)
        return v if math.isfinite(v) else float(default)

    @staticmethod
    def _team_rating(ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        team = ctx.get(side)
        candidates = []
        if isinstance(team, dict):
            candidates.append(team.get(kind))
        candidates.append(ctx.get(f"{side}_{kind}"))
        for c in candidates:
            try:
                f = float(c)
                if math.isfinite(f) and f > 0:
                    return f
            except (TypeError, ValueError):
                continue
        return None

    # ------------------------------------------------------------------ #
    # Packet distribution via exponential tilting
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tilted_packet(base: np.ndarray, target_mean: float) -> np.ndarray:
        """Return q_i ∝ b_i * exp(θ i) on {0,1,2,3}, with θ solved so E[q] = target_mean.

        This is the minimum-KL departure from the base shape that hits the
        target mean, so no clipping of probabilities is needed.
        """
        pts = np.arange(len(base), dtype=float)
        base = np.asarray(base, dtype=float)
        base = base / base.sum()
        lo_mean, hi_mean = pts[base > 0].min(), pts[base > 0].max()
        # The mean of a tilted family is restricted to the open interval
        # (min, max). Map the target into it smoothly by relative position.
        span = hi_mean - lo_mean
        m = lo_mean + span * (1.0 / (1.0 + math.exp(-(
            math.log(max(target_mean - lo_mean, 1e-12)) - math.log(max(hi_mean - target_mean, 1e-12))
        ))))

        def mean_gap(theta: float) -> float:
            w = base * np.exp(theta * (pts - pts.mean()))
            w /= w.sum()
            return float(w @ pts) - m

        a, b = -1.0, 1.0
        while mean_gap(a) > 0:
            a *= 2.0
        while mean_gap(b) < 0:
            b *= 2.0
        theta = optimize.brentq(mean_gap, a, b, xtol=1e-14)
        w = base * np.exp(theta * (pts - pts.mean()))
        return w / w.sum()

    # ------------------------------------------------------------------ #
    # Possession distribution
    # ------------------------------------------------------------------ #
    def _possession_pmf(self, mu_n: float, k_n: float) -> Tuple[np.ndarray, np.ndarray]:
        if not math.isfinite(k_n) or k_n <= 0:
            dist = stats.poisson(mu_n)
        else:
            dist = stats.nbinom(k_n, k_n / (k_n + mu_n))
        n_max = int(dist.ppf(1.0 - self.TAIL_EPS)) + 1
        support = np.arange(0, n_max + 1)
        pmf = dist.pmf(support)
        pmf /= pmf.sum()
        return support, pmf

    # ------------------------------------------------------------------ #
    # Parameter assembly
    # ------------------------------------------------------------------ #
    def _parameters(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        h_exp = self._num(ctx, "home_expected", self.PRIOR_HOME_EXPECTED)
        a_exp = self._num(ctx, "away_expected", self.PRIOR_AWAY_EXPECTED)
        h_exp = h_exp if h_exp > 0 else self.PRIOR_HOME_EXPECTED
        a_exp = a_exp if a_exp > 0 else self.PRIOR_AWAY_EXPECTED
        total = h_exp + a_exp

        base = np.asarray(ctx.get("packet_shape", self.PRIOR_PACKET_SHAPE), dtype=float)
        if base.shape != (4,) or np.any(base < 0) or base.sum() <= 0:
            base = self.PRIOR_PACKET_SHAPE.copy()
        base = base / base.sum()
        base_ppp = float(base @ np.arange(4))

        # Shared possessions per team in the quarter.
        pace = None
        for key in ("pace", "tempo", "possessions_per_game", "expected_pace"):
            if key in ctx:
                p = self._num(ctx, key, float("nan"))
                if math.isfinite(p) and p > 0:
                    pace = p
                    break
        if pace is not None:
            mu_n = pace * self.QUARTER_MINUTES / self.GAME_MINUTES
        else:
            # Infer possessions from the anchored total and the base efficiency.
            mu_n = total / (2.0 * base_ppp)
        k_n = self._num(ctx, "possession_dispersion_k", self.PRIOR_POSSESSION_K)

        # Home-advantage decay into Q3. The shift preserves the total.
        neutral = bool(ctx.get("neutral_site", False)) or str(ctx.get("venue", "")).lower() in (
            "neutral",
            "neutral_site",
        )
        game_hca = 0.0 if neutral else self._num(ctx, "home_advantage_points", self.PRIOR_GAME_HCA_POINTS)
        quarter_hca = game_hca * self.QUARTER_MINUTES / self.GAME_MINUTES
        retention = self._num(ctx, "q3_hca_retention", self.PRIOR_Q3_HCA_RETENTION)
        decay_shift = 0.5 * (1.0 - retention) * quarter_hca
        h_adj, a_adj = h_exp - decay_shift, a_exp + decay_shift

        # Rest differential, as a symmetric log-ppp tilt that preserves the total.
        rest_diff = self._num(ctx, "home_rest_days", 0.0) - self._num(ctx, "away_rest_days", 0.0)
        rest_tilt = self.PRIOR_REST_ELASTICITY * math.tanh(rest_diff / self.PRIOR_REST_SCALE_DAYS)

        # Residual attack/defense tilt, shrunk because the expectations
        # already contain most of the rating signal.
        rating_tilt = 0.0
        ha, hd = self._team_rating(ctx, "home", "attack"), self._team_rating(ctx, "home", "defense")
        aa, ad = self._team_rating(ctx, "away", "attack"), self._team_rating(ctx, "away", "defense")
        if None not in (ha, hd, aa, ad):
            rating_tilt = 0.5 * self.PRIOR_RATING_SHRINK * (
                math.log(ha / ad) - math.log(aa / hd)
            )

        tilt = rest_tilt + rating_tilt
        h_raw, a_raw = h_adj * math.exp(tilt), a_adj * math.exp(-tilt)
        scale = total / (h_raw + a_raw)  # keep the anchored total unchanged
        h_final, a_final = h_raw * scale, a_raw * scale

        return {
            "mu_n": mu_n,
            "k_n": k_n,
            "base": base,
            "home_mean": h_final,
            "away_mean": a_final,
            "ppp_home": h_final / mu_n,
            "ppp_away": a_final / mu_n,
        }

    # ------------------------------------------------------------------ #
    # Exact joint lattice
    # ------------------------------------------------------------------ #
    def joint_lattice(self, ctx: Dict[str, Any]) -> np.ndarray:
        prm = self._parameters(ctx)
        q_h = self._tilted_packet(prm["base"], prm["ppp_home"])
        q_a = self._tilted_packet(prm["base"], prm["ppp_away"])
        support, w = self._possession_pmf(prm["mu_n"], prm["k_n"])

        n_max = int(support[-1])
        size = 3 * n_max + 1
        lattice = np.zeros((size, size))
        conv_h = np.zeros(size)
        conv_h[0] = 1.0
        conv_a = conv_h.copy()
        for n in range(n_max + 1):
            if n > 0:
                conv_h = np.convolve(conv_h, q_h)[:size]
                conv_a = np.convolve(conv_a, q_a)[:size]
            if w[n] > 0:
                lattice += w[n] * np.outer(conv_h, conv_a)
        lattice /= lattice.sum()
        self._last_lattice = lattice
        return lattice

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]):
        ctx = game_context or {}
        lattice = self.joint_lattice(ctx)

        p_home = float(np.tril(lattice, -1).sum())  # rows = home score
        p_away = float(np.triu(lattice, 1).sum())
        p_draw = float(np.trace(lattice))

        if DiscreteDistribution is None:
            raise RuntimeError("DiscreteDistribution not available in execution scope")

        nz = np.argwhere(lattice > 1e-12)
        scores = {(int(h), int(a)): float(lattice[h, a]) for h, a in nz}
        tot = sum(scores.values())
        scores = {k: v / tot for k, v in scores.items()}

        from_scores = getattr(DiscreteDistribution, "from_scores", None)
        if callable(from_scores):
            for payload in (scores, lattice):
                try:
                    return from_scores(payload)
                except Exception:
                    continue
        from_probs = getattr(DiscreteDistribution, "from_probabilities", None)
        if callable(from_probs):
            try:
                return from_probs(p_home, p_draw, p_away)
            except Exception:
                pass
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})

    def price_winner_total(
        self,
        game_context: Dict[str, Any],
        winner: str,
        line: float,
        over: bool,
        tie_rule: str = "push",
    ) -> Dict[str, float]:
        """Price a 'winner AND total over/under line' contract on the lattice.

        tie_rule controls the tied-quarter diagonal: 'push' refunds the stake,
        'loss' settles it as a loss. Integer lines also push when the total
        lands exactly on the line.
        """
        lattice = self._last_lattice if self._last_lattice is not None else self.joint_lattice(game_context)
        size = lattice.shape[0]
        h_idx, a_idx = np.indices((size, size))
        totals = h_idx + a_idx
        win_mask = (h_idx > a_idx) if winner == "home" else (a_idx > h_idx)
        total_win = (totals > line) if over else (totals < line)
        total_push = np.isclose(totals, line)
        tie_mask = h_idx == a_idx

        p_win = float(lattice[win_mask & total_win].sum())
        p_push = float(lattice[win_mask & total_push].sum())
        if tie_rule == "push":
            p_push += float(lattice[tie_mask & ~total_push].sum())
        p_push = min(p_push, 1.0 - p_win)
        p_loss = max(0.0, 1.0 - p_win - p_push)
        return {"win": p_win, "push": p_push, "loss": p_loss}

    @staticmethod
    def expected_value(prices: Dict[str, float], decimal_odds: float) -> float:
        """EV per unit stake. Eligibility is continuous: odds >= 1.40 and EV > 0."""
        return prices["win"] * (decimal_odds - 1.0) - prices["loss"]

    # ------------------------------------------------------------------ #
    # Calibration helpers (moment-based, to replace priors once data exists)
    # ------------------------------------------------------------------ #
    @staticmethod
    def fit_possession_dispersion(quarter_possessions) -> float:
        """Method of moments: Var = μ + μ²/k, so k = μ² / (Var − μ).

        Returns inf for a Poisson-or-tighter sample.
        """
        x = np.asarray(quarter_possessions, dtype=float)
        mu, var = x.mean(), x.var(ddof=1)
        return float(mu * mu / (var - mu)) if var > mu else float("inf")

    @staticmethod
    def fit_q3_hca_retention(first_half_margins, second_half_margins, game_hca_points: float) -> float:
        """Retention = mean second-half home margin / expected half share of HCA."""
        sh = np.asarray(second_half_margins, dtype=float)
        _ = np.asarray(first_half_margins, dtype=float)
        expected_half = 0.5 * game_hca_points
        return float(sh.mean() / expected_half) if expected_half != 0 else 0.0


CandidateModel = NCAAWQuarter3WinnerTotalModel