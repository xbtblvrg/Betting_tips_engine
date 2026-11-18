"""
Candidate model for NCAAM | quarter_4_ot | moneyline.

Segment interpretation
----------------------
NCAAM plays two 20-minute halves, so "quarter_4_ot" is treated as the final
10 minutes of regulation, plus overtime when `include_ot` is True. That is
seg_frac = 0.25 of regulation, with 0.75 elapsed before the window opens.

Structure
---------
1. Anchor and shrink the means.
   - The segment total T = home_expected + away_expected is kept exactly.
   - The projected segment margin g = home_expected - away_expected is
     precision-shrunk toward a structural prior: the ratings-implied split of
     T plus a segment-scaled home-court term. This limits how much full-game
     home edge carries into the segment.
2. Possession count.
   - Two estimates are combined by geometric mean: possessions implied by T at
     league points per possession, and possessions implied by team tempo.
3. Endgame regimes, from a Gaussian state model of the lead entering the window.
   - Normal regime: base possessions with the base shot-value mix.
   - Close regime: the lead entering the window is within the fouling window.
     Fouling adds possessions, and the mix shifts toward free throws and
     chased threes.
   - Close + OT regime: the game reaches overtime. OT possessions are added,
     using the close-regime mix.
   - Possession-count mean is preserved across the regimes.
   - Overdispersion of possessions comes from the regime mixture. Within a
     regime, the dispersion index defaults to the observed 1.000 (Poisson).
4. Points per possession.
   - Each team draws from a categorical distribution over {0, 1, 2, 3, 4}.
   - Each team's per-possession expectation matches its anchored mean.
   - Efficiency changes are split equally between scoring rate and shot value.
     The shot-value part is applied as an exponential tilt of the regime's
     shot mix.
5. Exact margin lattice.
   - Paired possessions are convolved n times and mixed over P(N = n).
   - Alternating possession parity adds one extra possession to either team
     with probability 1/4 each.
   - P(margin = 0) is reported explicitly as 'draw'. Push versus 3-way
     settlement is left to the engine.

All domain priors are named, documented, and overridable via constructor
kwargs or per-game context keys. There are no odds caps or price filters, and
no bookmaker information enters the forecast.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import integrate, optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

if "DiscreteDistribution" not in globals():
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class NCAAMQuarter4OTMoneylineModel(BaseCandidateModel):
    """Possession-level endgame-regime model for the NCAAM final-10-minutes (+OT) moneyline."""

    # Named domain priors (NCAAM). Every one can be overridden via kwargs or game_context.
    DEFAULT_PRIORS: Dict[str, float] = {
        # Regulation minutes covered by the segment, as a share of 40 minutes.
        "segment_fraction": 0.25,
        # League points per possession.
        "league_ppp": 1.03,
        # League tempo, in possessions per 40 minutes.
        "league_tempo": 68.0,
        # Residual SD of the full-game margin.
        "full_game_margin_sd": 11.0,
        # Entering-lead window (in points) in which endgame fouling is live.
        "foul_window_points": 8.0,
        # Extra possessions per team created by fouling in the close regime.
        "foul_extra_possessions": 2.5,
        # Full-game home-court advantage, in points.
        "home_court_full_game": 3.0,
        # SD of the true segment edge around its structural prior.
        "segment_edge_prior_sd": 1.0,
        # SD of the projection noise in home_expected - away_expected.
        "segment_projection_sd": 1.0,
        # Full-game margin effect per rest day of difference, saturating.
        "rest_margin_per_day": 0.4,
        # Saturation scale for the rest-day difference.
        "rest_saturation_days": 3.0,
        # OT length relative to the segment (5 / 10 minutes).
        "ot_length_ratio": 0.5,
        # Within-regime possession Var/Mean (defaults to the observed index).
        "possession_dispersion": 1.0,
    }

    # Shot-value mix, conditional on scoring, over points {1, 2, 3, 4}.
    # 1 = split FT trip; 2 = two-point make or 2-of-2 FTs; 3 = three-point make
    # or and-one; 4 = four-point play or foul + retained possession.
    NORMAL_MIX = np.array([0.08, 0.60, 0.30, 0.02])
    CLOSE_MIX = np.array([0.13, 0.55, 0.29, 0.03])

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_4_ot_1108_071724",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_4_ot",
        family: str = "moneyline",
        include_ot: bool = True,
        defense_is_concession: bool = True,
        **prior_overrides: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__()
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family
        self.include_ot = bool(include_ot)
        self.defense_is_concession = bool(defense_is_concession)
        self.priors = dict(self.DEFAULT_PRIORS)
        for k, v in prior_overrides.items():
            if k in self.priors and self._num(v) is not None:
                self.priors[k] = float(v)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _prior(self, ctx: Dict[str, Any], key: str) -> float:
        v = self._num(ctx.get(key))
        return v if v is not None else self.priors[key]

    def _side_value(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        blk = ctx.get(side)
        v = blk.get(key) if isinstance(blk, dict) else None
        if v is None:
            v = ctx.get(f"{side}_{key}")
        return self._num(v)

    # ------------------------------------------------------------ components
    def _anchored_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        """Return home/away segment means: total T is preserved, margin is precision-shrunk."""
        seg = self._prior(ctx, "segment_fraction")
        h_exp = self._num(ctx.get("home_expected"), 20.00)
        a_exp = self._num(ctx.get("away_expected"), 18.75)
        total = max(h_exp + a_exp, 1e-6)
        g_obs = h_exp - a_exp

        # Ratings-implied split of T (multiplicative attack x opponent defense).
        ah, dh = self._side_value(ctx, "home", "attack"), self._side_value(ctx, "home", "defense")
        aa, da = self._side_value(ctx, "away", "attack"), self._side_value(ctx, "away", "defense")
        g_ratings = 0.0
        if None not in (ah, dh, aa, da) and min(ah, dh, aa, da) > 0:
            if not self.defense_is_concession:
                dh, da = 1.0 / dh, 1.0 / da
            f_h, f_a = ah * da, aa * dh
            g_ratings = total * (f_h - f_a) / (f_h + f_a)

        # Rest differential, as a saturating full-game effect scaled to the segment.
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        g_rest = 0.0
        if rh is not None and ra is not None:
            g_rest = (
                self._prior(ctx, "rest_margin_per_day")
                * self._prior(ctx, "rest_saturation_days")
                * math.tanh((rh - ra) / self._prior(ctx, "rest_saturation_days"))
                * seg
            )

        neutral = bool(ctx.get("neutral_site") or ctx.get("is_neutral"))
        hca_seg = 0.0 if neutral else self._prior(ctx, "home_court_full_game") * seg
        prior_mean = g_ratings + hca_seg + g_rest

        tau2 = self._prior(ctx, "segment_edge_prior_sd") ** 2
        sig2 = self._prior(ctx, "segment_projection_sd") ** 2
        g_post = (g_obs / sig2 + prior_mean / tau2) / (1.0 / sig2 + 1.0 / tau2)

        # The posterior margin cannot exceed the total; scale it if it would.
        if abs(g_post) >= total:
            g_post = math.copysign(total * (1 - 1e-6), g_post)
        mu_h, mu_a = 0.5 * (total + g_post), 0.5 * (total - g_post)
        return mu_h, mu_a, {
            "total": total, "g_obs": g_obs, "g_prior": prior_mean, "g_post": g_post,
            "g_ratings": g_ratings, "g_rest": g_rest, "hca_seg": hca_seg,
        }

    def _possession_mean(self, ctx: Dict[str, Any], total: float) -> float:
        """Per-team possessions: geometric mean of T-implied and tempo-implied counts."""
        seg = self._prior(ctx, "segment_fraction")
        n_from_total = total / (2.0 * self._prior(ctx, "league_ppp"))
        paces = [self._side_value(ctx, s, k) for s in ("home", "away") for k in ("tempo", "pace")]
        paces = [p for p in paces if p is not None and p > 0]
        tempo = self._num(ctx.get("tempo")) or self._num(ctx.get("pace"))
        if paces:
            tempo = float(np.mean(paces))
        if tempo is None or tempo <= 0:
            return n_from_total
        return math.sqrt(n_from_total * tempo * seg)

    def _ppp_pmf(self, mix: np.ndarray, target_ppp: float, ctx: Dict[str, Any]) -> np.ndarray:
        """Per-possession pmf over {0..4}. The efficiency change is split equally
        between scoring rate and shot value (exponential tilt of the mix)."""
        pts = np.arange(1, 5, dtype=float)
        mix = mix / mix.sum()
        ppp_ref = self._prior(ctx, "league_ppp")
        s_ref = ppp_ref / float(mix @ pts)
        s = s_ref * math.sqrt(max(target_ppp, 1e-9) / ppp_ref)
        m_target = target_ppp / s
        eps = 1e-6
        if not (1.0 + eps < m_target < 4.0 - eps):  # Keep within the feasible lattice.
            m_target = min(max(m_target, 1.0 + eps), 4.0 - eps)
            s = target_ppp / m_target
        s = min(s, 1.0 - eps)

        logm = np.log(mix)

        def gap(theta: float) -> float:
            z = logm + theta * pts
            w = np.exp(z - z.max())
            return float(w @ pts / w.sum()) - m_target

        theta = optimize.brentq(gap, -60.0, 60.0, xtol=1e-12)
        z = logm + theta * pts
        w = np.exp(z - z.max())
        w /= w.sum()
        return np.concatenate([[1.0 - s], s * w])

    def _count_pmf(self, mu: float, phi: float) -> np.ndarray:
        mu = max(mu, 1e-6)
        if phi > 1.0 + 1e-9:
            k = mu / (phi - 1.0)
            dist = stats.nbinom(k, k / (k + mu))
        else:
            dist = stats.poisson(mu)
        nmax = int(dist.ppf(1.0 - 1e-10)) + 1
        pmf = dist.pmf(np.arange(nmax + 1))
        return pmf / pmf.sum()

    @staticmethod
    def _margin_pmf(h: np.ndarray, a: np.ndarray, n_pmf: np.ndarray) -> Tuple[np.ndarray, int]:
        """Exact margin lattice for N paired possessions plus alternating-parity extra."""
        d = np.convolve(h, a[::-1])  # Index k corresponds to margin k - 4.
        nmax = n_pmf.size - 1
        acc = np.zeros(8 * nmax + 1)
        cur = np.array([1.0])
        for n in range(nmax + 1):
            if n > 0:
                cur = np.convolve(cur, d)
            if n_pmf[n] > 0:
                off = 4 * (nmax - n)
                acc[off: off + cur.size] += n_pmf[n] * cur
        kernel = np.zeros(9)
        kernel[4] += 0.5
        kernel[4:9] += 0.25 * h          # Home gets the extra possession.
        kernel[0:5] += 0.25 * a[::-1]    # Away gets the extra possession.
        out = np.convolve(acc, kernel)
        out /= out.sum()
        return out, 4 * nmax + 4         # (pmf, index of margin 0)

    def _regime_weights(
        self, ctx: Dict[str, Any], g_seg: float, sd_seg: float
    ) -> Tuple[float, float, float, float, float]:
        """Weights for (normal, close, close+OT) from a Gaussian model of the entering lead."""
        seg = self._prior(ctx, "segment_fraction")
        elapsed = 1.0 - seg
        full_spread = self._num(ctx.get("full_game_expected_margin"))
        if full_spread is None:
            full_spread = g_seg / seg
        m_pre = full_spread * elapsed
        sd_pre = self._prior(ctx, "full_game_margin_sd") * math.sqrt(elapsed)
        W = self._prior(ctx, "foul_window_points")

        p_close = stats.norm.cdf((W - m_pre) / sd_pre) - stats.norm.cdf((-W - m_pre) / sd_pre)
        sd_final = math.sqrt(sd_pre ** 2 + sd_seg ** 2)
        m_final = m_pre + g_seg
        p_ot = stats.norm.cdf((0.5 - m_final) / sd_final) - stats.norm.cdf((-0.5 - m_final) / sd_final)
        if not self.include_ot:
            return 1.0 - p_close, p_close, 0.0, p_close, 0.0

        def integrand(L: float) -> float:
            tie = stats.norm.cdf((0.5 - L - g_seg) / sd_seg) - stats.norm.cdf((-0.5 - L - g_seg) / sd_seg)
            return stats.norm.pdf(L, m_pre, sd_pre) * tie

        p_joint, _ = integrate.quad(integrand, -W, W, limit=200)
        p_joint = min(max(p_joint, 0.0), min(p_close, p_ot))
        w_ot = p_ot
        w_close = p_close - p_joint
        w_norm = max(1.0 - w_close - w_ot, 0.0)
        z = w_norm + w_close + w_ot
        return w_norm / z, w_close / z, w_ot / z, p_close, p_ot

    # ------------------------------------------------------------------- main
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mu_h, mu_a, mean_diag = self._anchored_means(ctx)
        n_mu = self._possession_mean(ctx, mean_diag["total"])
        phi = max(self._prior(ctx, "possession_dispersion"), 1.0)
        extra = self._prior(ctx, "foul_extra_possessions")

        # Base-regime per-possession pmfs, used to derive the segment margin SD.
        ppp_h0, ppp_a0 = mu_h / (n_mu + 0.25), mu_a / (n_mu + 0.25)
        pts = np.arange(5, dtype=float)
        h0, a0 = self._ppp_pmf(self.NORMAL_MIX, ppp_h0, ctx), self._ppp_pmf(self.NORMAL_MIX, ppp_a0, ctx)
        var_poss = float(h0 @ pts ** 2 - (h0 @ pts) ** 2 + a0 @ pts ** 2 - (a0 @ pts) ** 2)
        sd_seg = math.sqrt(max(n_mu * var_poss, 1e-6))

        w_n, w_c, w_ot, p_close, p_ot = self._regime_weights(ctx, mean_diag["g_post"], sd_seg)

        # Preserve the possession mean: foul possessions are drawn from the normal regime.
        mu_ot = n_mu * self._prior(ctx, "ot_length_ratio")
        mu_normal = max(n_mu - (w_c + w_ot) * extra, 1e-3)
        mu_close = mu_normal + extra

        components = []
        if w_n > 0:
            n_pmf = self._count_pmf(mu_normal, phi)
            h = self._ppp_pmf(self.NORMAL_MIX, mu_h / (n_mu + 0.25), ctx)
            a = self._ppp_pmf(self.NORMAL_MIX, mu_a / (n_mu + 0.25), ctx)
            components.append((w_n, self._margin_pmf(h, a, n_pmf)))
        if w_c > 0 or w_ot > 0:
            h = self._ppp_pmf(self.CLOSE_MIX, mu_h / (n_mu + 0.25), ctx)
            a = self._ppp_pmf(self.CLOSE_MIX, mu_a / (n_mu + 0.25), ctx)
            n_close = self._count_pmf(mu_close, phi)
            if w_c > 0:
                components.append((w_c, self._margin_pmf(h, a, n_close)))
            if w_ot > 0:
                n_with_ot = np.convolve(n_close, self._count_pmf(mu_ot, phi))
                n_with_ot /= n_with_ot.sum()
                components.append((w_ot, self._margin_pmf(h, a, n_with_ot)))

        p_home = p_draw = p_away = 0.0
        for w, (pmf, zero) in components:
            p_away += w * float(pmf[:zero].sum())
            p_draw += w * float(pmf[zero])
            p_home += w * float(pmf[zero + 1:].sum())
        z = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / z, p_draw / z, p_away / z

        self.last_diagnostics = {
            **mean_diag, "mu_home": mu_h, "mu_away": mu_a, "possessions_per_team": n_mu,
            "segment_margin_sd": sd_seg, "p_close": p_close, "p_ot": p_ot,
            "w_normal": w_n, "w_close": w_c, "w_close_ot": w_ot,
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        }

        if hasattr(DiscreteDistribution, "from_probabilities"):
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})