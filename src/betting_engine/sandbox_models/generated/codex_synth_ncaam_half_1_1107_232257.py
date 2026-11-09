"""
Candidate model: NCAAM | half_1 | spreads

First-principles structure
--------------------------
First-half margin is modeled as a sum over a shared random possession count N
of discrete per-possession scoring packets on {0, 1, 2, 3}:

    M = sum_{i=1..N} (X^H_i - X^A_i)

1. Possessions. The expected count is the pace-blended tempo
   E[N] = 1/2 * pace_h * pace_a / pace_lg * g(rest), with g in a saturating
   (tanh) form. N is integrated over a discretized normal whose dispersion
   comes from the known variability of college possession counts. Both teams
   share N, since alternating possessions make the counts nearly identical.
2. Packets. Each side has a categorical distribution
   P(X=k) = s * q_k for k in {1,2,3} and P(X=0) = 1 - s, where q is the
   conditional shot/free-throw mix (foul-light first half). s is solved
   analytically so that E[N] * E[X] equals the posterior team expectation.
   The dispersion index (about 1.25-1.30) is therefore emergent rather than
   assumed to be Poisson.
3. Center. The raw margin (home_expected - away_expected) is shrunk toward a
   pace-scaled home-court prior with precision weight
   k = tau^2 / (tau^2 + sigma_rating^2). This curbs home overconfidence
   without a static clamp. The total is preserved.
4. Rating uncertainty. The posterior uncertainty of the true margin
   (variance k * sigma_rating^2) is integrated by Gauss-Hermite quadrature
   over a latent efficiency shift. It is not simply added as a normal
   blur, so lattice structure (pushes, +-2/+-3 bumps) survives.
5. Output. The full joint score lattice is assembled by exact convolution
   and emitted through DiscreteDistribution.from_scores. Downstream spread
   pricing therefore sees genuine push mass at integer lines.

No bookmaker prices enter the forecast. Every numeric prior is a documented
domain quantity (league tempo, home-court effect, shot mix), exposed as a
class attribute, and overridable from game_context.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally in execution scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NCAAMHalf1SpreadsModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Domain priors (documented physical quantities, not tuning clamps)  #
    # ------------------------------------------------------------------ #
    # Empirical segment baselines (first-half points).
    BASE_HOME_EXPECTED = 40.00
    BASE_AWAY_EXPECTED = 37.50

    # D1 full-game tempo (possessions per 40 min, per team).
    LEAGUE_PACE_FULL = 68.0
    # SD of a team's first-half possession count around its tempo expectation.
    HALF_POSSESSION_SD = 2.6

    # Full-game home-court advantage (points), halved for a 20-minute half
    # at league tempo, then scaled by realized tempo.
    HOME_COURT_FULL = 3.3

    # Cross-game SD of true first-half margins (signal) and cold-start SD
    # of rating error on that margin (noise).
    TRUE_MARGIN_SD = 7.0
    RATING_ERROR_SD = 3.6

    # Conditional scoring-packet mix given a scoring possession in a
    # foul-light first half: (1-pt FT trips, 2-pt, 3-pt).
    PACKET_MIX = (0.12, 0.58, 0.30)
    # Ceiling on the scoring-possession share (turnovers and empty trips
    # make s -> 1 physically unattainable).
    MAX_SCORING_SHARE = 0.80

    # Rest effect: points per half at saturation of the rest differential,
    # and the day scale over which it saturates.
    REST_EDGE_HALF_PTS = 0.6
    REST_SCALE_DAYS = 2.0
    # Tempo sensitivity to mean rest (fatigued teams play slower).
    REST_PACE_ELASTICITY = 0.015

    # Gauss-Hermite nodes for integrating rating uncertainty.
    GH_NODES = 7
    # Probability mass of the possession distribution retained (tails).
    POSSESSION_TAIL_Z = 4.0

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_half_1_1107_232257",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "half_1",
        family: str = "spreads",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
                **kwargs,
            )
        except TypeError:
            super().__init__()  # type: ignore[call-arg]
            self.model_id = model_id
            self.version = version
            self.league = league
            self.scope = scope
            self.family = family

    # ------------------------------------------------------------------ #
    # Feature helpers                                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            if value is None:
                return default
            v = float(value)
            if math.isnan(v) or math.isinf(v):
                return default
            return v
        except (TypeError, ValueError):
            return default

    def _side(self, ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        block = ctx.get(side)
        return block if isinstance(block, dict) else {}

    def _ratio_feature(self, block: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
        """Return a multiplicative rating (1.0 = league average) if present."""
        for k in keys:
            v = self._num(block.get(k))
            if v is not None and v > 0:
                # Accept either ratio form (~1.0) or per-100 efficiency form (~100+).
                return v / 100.0 if v > 10.0 else v
        return None

    def _team_pace(self, block: Dict[str, Any], ctx: Dict[str, Any], side: str) -> float:
        for k in ("pace", "tempo", "adj_tempo"):
            v = self._num(block.get(k))
            if v is not None and v > 0:
                return v
        v = self._num(ctx.get(f"{side}_pace"))
        return v if v is not None and v > 0 else self.LEAGUE_PACE_FULL

    def _packet_mix(self, block: Dict[str, Any]) -> np.ndarray:
        """Conditional packet mix, shifted by a team's three-point / FT rate when known."""
        q1, q2, q3 = self.PACKET_MIX
        three_rate = self._num(block.get("three_rate", block.get("three_point_rate")))
        ft_rate = self._num(block.get("ft_rate", block.get("free_throw_rate")))
        if three_rate is not None and 0.0 < three_rate < 1.0:
            # Re-split the field-goal share of the mix in proportion to 3PA rate.
            fg = q2 + q3
            league_three_share = q3 / fg
            # Make share of a scoring FG lean toward the team's 3PA tendency.
            share = league_three_share * (three_rate / max(league_three_share * 1.0, 1e-9)) ** 0.5
            share = share / (share + (1.0 - league_three_share) * ((1.0 - three_rate) / (1.0 - league_three_share)) ** 0.5)
            q3, q2 = fg * share, fg * (1.0 - share)
        if ft_rate is not None and ft_rate > 0:
            league_ft = 0.30  # league FTA/FGA reference
            q1 = q1 * (ft_rate / league_ft)
        q = np.array([q1, q2, q3], dtype=float)
        return q / q.sum()

    # ------------------------------------------------------------------ #
    # Core mechanics                                                     #
    # ------------------------------------------------------------------ #
    def _rest_terms(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None:
            return 1.0, 0.0
        diff = rh - ra
        edge = self.REST_EDGE_HALF_PTS * math.tanh(diff / self.REST_SCALE_DAYS)
        mean_rest = 0.5 * (rh + ra)
        # Short rest (< ~2 days) slows tempo smoothly; long rest saturates.
        g = 1.0 + self.REST_PACE_ELASTICITY * math.tanh((mean_rest - self.REST_SCALE_DAYS) / self.REST_SCALE_DAYS)
        return g, edge

    def _expected_possessions(self, ctx: Dict[str, Any], g_rest: float) -> float:
        ph = self._team_pace(self._side(ctx, "home"), ctx, "home")
        pa = self._team_pace(self._side(ctx, "away"), ctx, "away")
        return 0.5 * (ph * pa / self.LEAGUE_PACE_FULL) * g_rest

    def _raw_expectations(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._num(ctx.get("home_expected"))
        a = self._num(ctx.get("away_expected"))
        if h is not None and a is not None and h > 0 and a > 0:
            # Expectations already embed matchup quality; do not double count.
            return h, a
        # Fall back to attack/defense exposure around the empirical baseline.
        hb, ab = self._side(ctx, "home"), self._side(ctx, "away")
        h_att = self._ratio_feature(hb, ("attack", "offense", "adj_o")) or 1.0
        h_def = self._ratio_feature(hb, ("defense", "adj_d")) or 1.0
        a_att = self._ratio_feature(ab, ("attack", "offense", "adj_o")) or 1.0
        a_def = self._ratio_feature(ab, ("defense", "adj_d")) or 1.0
        h = h if h is not None and h > 0 else self.BASE_HOME_EXPECTED * h_att * a_def
        a = a if a is not None and a > 0 else self.BASE_AWAY_EXPECTED * a_att * h_def
        return h, a

    def _posterior_center(
        self, ctx: Dict[str, Any], h_raw: float, a_raw: float, n_exp: float, rest_edge: float
    ) -> Tuple[float, float, float]:
        """Precision-weighted shrinkage of the margin toward the home-court prior."""
        league_half_poss = 0.5 * self.LEAGUE_PACE_FULL
        tempo_scale = n_exp / league_half_poss
        neutral = bool(ctx.get("neutral_site") or ctx.get("is_neutral") or
                       (isinstance(ctx.get("venue"), dict) and ctx["venue"].get("neutral")))
        hca = 0.0 if neutral else 0.5 * self.HOME_COURT_FULL * tempo_scale
        prior_margin = hca + rest_edge

        tau = self._num(ctx.get("true_margin_sd"), self.TRUE_MARGIN_SD) * math.sqrt(tempo_scale)
        sig = self._num(ctx.get("rating_sd"), self.RATING_ERROR_SD) * math.sqrt(tempo_scale)
        k = tau * tau / (tau * tau + sig * sig)

        mu_raw = (h_raw - a_raw) + rest_edge
        mu = prior_margin + k * (mu_raw - prior_margin)
        total = h_raw + a_raw
        h_post = 0.5 * (total + mu)
        a_post = 0.5 * (total - mu)
        post_sd = math.sqrt(k) * sig
        return h_post, a_post, post_sd

    def _categorical(self, mean_per_poss: float, q: np.ndarray) -> np.ndarray:
        """P(X=k), k=0..3, with scoring share s solved exactly for the target mean."""
        mean_per_poss = max(mean_per_poss, 1e-6)
        c = float(np.dot(q, [1.0, 2.0, 3.0]))
        s = mean_per_poss / c
        if s > self.MAX_SCORING_SHARE:
            # Infeasible at this mix: lift the packet value toward 3s so that
            # s * c_new = mean holds at the physical scoring ceiling.
            s = self.MAX_SCORING_SHARE
            c_target = min(mean_per_poss / s, 3.0 - 1e-9)
            three = np.array([0.0, 0.0, 1.0])
            lam = (c_target - c) / (3.0 - c)
            q = (1.0 - lam) * q + lam * three
        p = np.empty(4)
        p[0] = 1.0 - s
        p[1:] = s * q
        return p / p.sum()

    def _possession_grid(self, n_exp: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = self.HALF_POSSESSION_SD * math.sqrt(n_exp / (0.5 * self.LEAGUE_PACE_FULL))
        lo = max(1, int(math.floor(n_exp - self.POSSESSION_TAIL_Z * sd)))
        hi = int(math.ceil(n_exp + self.POSSESSION_TAIL_Z * sd))
        ns = np.arange(lo, hi + 1)
        w = np.exp(-0.5 * ((ns - n_exp) / sd) ** 2)
        return ns, w / w.sum()

    @staticmethod
    def _power_table(cat: np.ndarray, n_max: int, size: int) -> np.ndarray:
        """Row n = n-fold convolution of cat, padded to `size`."""
        table = np.zeros((n_max + 1, size))
        cur = np.array([1.0])
        table[0, 0] = 1.0
        for n in range(1, n_max + 1):
            cur = np.convolve(cur, cat)
            table[n, : cur.size] = cur
        return table

    def _joint_lattice(
        self, h_mean: float, a_mean: float, post_sd: float, n_exp: float, q_h: np.ndarray, q_a: np.ndarray
    ) -> np.ndarray:
        ns, wn = self._possession_grid(n_exp)
        n_max = int(ns[-1])
        size = 3 * n_max + 1
        # Effective mean possessions under the discretized grid.
        n_eff = float(np.dot(ns, wn))

        nodes, weights = np.polynomial.hermite_e.hermegauss(self.GH_NODES)
        weights = weights / weights.sum()

        joint = np.zeros((size, size))
        for z, wz in zip(nodes, weights):
            delta = post_sd * z  # latent shift in true margin
            hm = max(h_mean + 0.5 * delta, 1e-3)
            am = max(a_mean - 0.5 * delta, 1e-3)
            cat_h = self._categorical(hm / n_eff, q_h)
            cat_a = self._categorical(am / n_eff, q_a)
            th = self._power_table(cat_h, n_max, size)
            ta = self._power_table(cat_a, n_max, size)
            for n, w in zip(ns, wn):
                joint += (wz * w) * np.outer(th[n], ta[n])
        joint /= joint.sum()
        return joint

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        g_rest, rest_edge = self._rest_terms(ctx)
        n_exp = self._expected_possessions(ctx, g_rest)
        h_raw, a_raw = self._raw_expectations(ctx)
        h_post, a_post, post_sd = self._posterior_center(ctx, h_raw, a_raw, n_exp, rest_edge)

        q_h = self._packet_mix(self._side(ctx, "home"))
        q_a = self._packet_mix(self._side(ctx, "away"))

        joint = self._joint_lattice(h_post, a_post, post_sd, n_exp, q_h, q_a)

        # Trim negligible lattice mass for a compact score map.
        eps = 1e-10
        idx_h, idx_a = np.nonzero(joint > eps)
        probs = joint[idx_h, idx_a]
        probs = probs / probs.sum()
        scores = {(int(i), int(j)): float(p) for i, j, p in zip(idx_h, idx_a, probs)}

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass

        # Fallback: collapse to cover / push / fail against the context line.
        line = None
        for k in ("home_spread", "spread", "line", "handicap"):
            line = self._num(ctx.get(k))
            if line is not None:
                break
        line = 0.0 if line is None else line
        margins = idx_h - idx_a
        adj = margins + line  # home covers when margin + spread > 0
        p_home = float(probs[adj > 1e-9].sum())
        p_push = float(probs[np.abs(adj) <= 1e-9].sum())
        p_away = float(probs[adj < -1e-9].sum())
        try:
            return DiscreteDistribution.from_probabilities(p_home, p_push, p_away)
        except Exception:
            return DiscreteDistribution(probabilities={"home": p_home, "draw": p_push, "away": p_away})


Model = NCAAMHalf1SpreadsModel