"""
Candidate model for NCAAW | half_1 | three_way (home / draw / away at halftime).

Structure
---------
1. Shared possession count. Both teams draw from one possession count N for the
   half. Its mean is the league prior scaled by the pace/tempo input. Its variance
   comes from the between-game coefficient of variation of pace. A binomial lattice
   is used when the variance is below the mean (the usual case, because possessions
   alternate). A negative binomial is used when it is above. A one-possession
   asymmetry term (one team gets one extra trip) captures parity effects at the
   horn.
2. Packet-valued possessions. Each possession yields k in {0,1,2,3,4} points:
   empty trips, single free throws, 2-point FGs and FT pairs, threes and and-1s,
   and four-point plays. The league base mix is exponentially tilted, p_k ∝ b_k·e^{θk},
   so that E[N]·E[X] reproduces each team's situationally adjusted expected points
   exactly. This is the minimum-KL departure from the league packet mix.
3. Exact margin lattice. The margin characteristic function is
       Φ(ω) = Σ_n P(N=n)·[φ_H(ω)·φ_A(−ω)]^n · [(1−q) + q/2·φ_H(ω) + q/2·φ_A(−ω)]
   and is inverted by FFT on an alias-free integer grid.
4. Hierarchical home-edge shrinkage. The input expected margin is treated as a
   noisy observation of the true half margin. The prior is centred on the league
   first-half home-court advantage, or 0 at a neutral site. The posterior
   uncertainty is integrated out with Gauss–Hermite quadrature on the log
   scoring-share scale, which keeps both team means positive. No clamps or tie
   inflation are used.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - sandbox may pre-load into scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")


# --------------------------------------------------------------------------- #
# League-level structural priors (domain parameters, not output clamps)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NCAAWHalfPriors:
    # Empirical segment anchors
    home_expected: float = 40.00
    away_expected: float = 37.50
    # Possessions per team per half (10-minute quarters, 20-minute half)
    possessions_mean: float = 36.0
    # Between-game coefficient of variation of half possession counts
    possessions_cv: float = 0.075
    # Probability that one team gets one extra possession in the half
    extra_possession_prob: float = 0.55
    # League base packet mix over {0,1,2,3,4} points per possession
    packet_base: Tuple[float, ...] = (0.505, 0.055, 0.315, 0.115, 0.010)
    # Hierarchical home-edge prior (points per half)
    hca_prior_mean: float = 1.75
    true_margin_sd: float = 6.5   # spread of true first-half expected margins
    input_margin_sd: float = 2.25  # noise in upstream half-margin projection
    # Situational elasticities
    rest_pts_per_log_day: float = 0.35  # half points per unit log1p(rest) gap
    rating_elasticity: float = 0.35     # partial pass-through of att/def ratings
    pace_full_game_threshold: float = 55.0  # above this, pace is per 40 min
    # Numerics
    quadrature_nodes: int = 9
    tail_mass: float = 1e-12


class NCAAWHalf1ThreeWayModel(BaseCandidateModel):
    """Shared-possession, packet-lattice halftime three-way model for NCAAW."""

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_half_1_1107_234008",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "half_1",
        family: str = "three_way",
        priors: Optional[NCAAWHalfPriors] = None,
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
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
        self.priors = priors or NCAAWHalfPriors()
        base = np.asarray(self.priors.packet_base, dtype=float)
        self._packet_base = base / base.sum()
        self._packet_support = np.arange(base.size, dtype=float)

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._num(ctx[k])
                if v is not None:
                    return v
        return None

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        v = self._get(ctx, f"{side}_{kind}", f"{side}_{kind}_rating")
        if v is not None:
            return v
        blob = ctx.get(side)
        if isinstance(blob, dict):
            for k in (kind, f"{kind}_rating", f"{kind}_strength"):
                if k in blob:
                    v = self._num(blob[k])
                    if v is not None:
                        return v
        return None

    def _is_neutral(self, ctx: Dict[str, Any]) -> bool:
        for k in ("neutral_site", "is_neutral", "neutral"):
            if k in ctx:
                return bool(ctx[k])
        venue = ctx.get("venue")
        if isinstance(venue, dict):
            for k in ("neutral_site", "is_neutral", "neutral"):
                if k in venue:
                    return bool(venue[k])
        return False

    def _situational_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        pr = self.priors
        h = self._get(ctx, "home_expected", "home_expected_h1")
        a = self._get(ctx, "away_expected", "away_expected_h1")
        h = h if (h is not None and h > 0) else pr.home_expected
        a = a if (a is not None and a > 0) else pr.away_expected

        # Attack/defense exposure: multiplicative ratings centred on 1.0,
        # passed through partially (upstream means already embed part of them).
        def ratio(r: Optional[float]) -> float:
            return r if (r is not None and r > 0) else 1.0

        e = pr.rating_elasticity
        h_att, h_def = ratio(self._rating(ctx, "home", "attack")), ratio(self._rating(ctx, "home", "defense"))
        a_att, a_def = ratio(self._rating(ctx, "away", "attack")), ratio(self._rating(ctx, "away", "defense"))
        h *= (h_att * a_def) ** e
        a *= (a_att * h_def) ** e

        # Rest differential: concave in days, zero-sum on margin, total-preserving.
        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        if hr is not None and ar is not None:
            gap = math.log1p(max(hr, 0.0)) - math.log1p(max(ar, 0.0))
            shift = 0.5 * pr.rest_pts_per_log_day * gap
            total = h + a
            share = (h + shift) / total
            # Logit-space application keeps both means strictly positive.
            base_share = h / total
            lo = math.log(base_share / (1 - base_share))
            lo += (share - base_share) / (base_share * (1 - base_share))
            p = 1.0 / (1.0 + math.exp(-lo))
            h, a = total * p, total * (1 - p)
        return h, a

    def _possession_mean(self, ctx: Dict[str, Any]) -> float:
        pr = self.priors
        pace = self._get(ctx, "pace", "tempo", "expected_pace", "possessions")
        if pace is None or pace <= 0:
            hp = self._get(ctx, "home_pace", "home_tempo")
            ap = self._get(ctx, "away_pace", "away_tempo")
            if hp and ap and hp > 0 and ap > 0:
                pace = math.sqrt(hp * ap)
        if pace is None or pace <= 0:
            return pr.possessions_mean
        return pace / 2.0 if pace > pr.pace_full_game_threshold else pace

    # ------------------------------------------------------------------ #
    # Distribution primitives
    # ------------------------------------------------------------------ #
    def _possession_pmf(self, mu: float) -> Tuple[np.ndarray, np.ndarray]:
        pr = self.priors
        var = (pr.possessions_cv * mu) ** 2
        if var < mu:  # under-dispersed: binomial lattice
            n_trials = max(int(math.ceil(mu * mu / (mu - var))), int(math.ceil(mu)) + 1)
            dist = stats.binom(n_trials, mu / n_trials)
        elif var > mu:  # over-dispersed: negative binomial
            r = mu * mu / (var - mu)
            dist = stats.nbinom(r, r / (r + mu))
        else:
            dist = stats.poisson(mu)
        lo = int(max(dist.ppf(pr.tail_mass), 0))
        hi = int(dist.ppf(1.0 - pr.tail_mass)) + 1
        n = np.arange(lo, hi + 1)
        p = dist.pmf(n)
        return n, p / p.sum()

    def _tilted_packets(self, target_ppp: float) -> np.ndarray:
        b, k = self._packet_base, self._packet_support

        def mean_gap(theta: float) -> float:
            w = b * np.exp(theta * (k - k.mean()))
            return float((w * k).sum() / w.sum()) - target_ppp

        lo, hi = -1.0, 1.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        w = b * np.exp(theta * (k - k.mean()))
        return w / w.sum()

    def _three_way_given_means(
        self, h: float, a: float, n_vals: np.ndarray, n_pmf: np.ndarray, mu_n: float
    ) -> np.ndarray:
        q = self.priors.extra_possession_prob
        # Expected possessions per team = mu_n + q/2; ppp keeps E[pts] exact.
        eff_poss = mu_n + 0.5 * q
        ph = self._tilted_packets(h / eff_poss)
        pa = self._tilted_packets(a / eff_poss)

        k_max = ph.size - 1
        span = k_max * (int(n_vals.max()) + 1)
        L = 1 << int(math.ceil(math.log2(2 * span + 2)))

        xh = np.zeros(L)
        xh[: ph.size] = ph
        xa = np.zeros(L)
        xa[0] = pa[0]
        xa[-np.arange(1, pa.size)] = pa[1:]  # away points enter as negative margin

        phi = np.fft.fft(xh) * np.fft.fft(xa)
        # Σ_n P(n)·phi^n via log-space power (stable for |phi| ≤ 1)
        log_phi = np.log(phi + 0j)
        cf = np.zeros(L, dtype=complex)
        for n, p in zip(n_vals, n_pmf):
            cf += p * np.exp(n * log_phi)
        cf *= (1.0 - q) + 0.5 * q * (np.fft.fft(xh) + np.fft.fft(xa))

        pmf = np.real(np.fft.ifft(cf))
        pmf = np.where(pmf > 0, pmf, 0.0)
        pmf /= pmf.sum()
        half = L // 2
        p_home = pmf[1:half].sum()
        p_draw = pmf[0]
        p_away = pmf[half:].sum()
        return np.array([p_home, p_draw, p_away])

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        pr = self.priors
        ctx = game_context or {}

        h, a = self._situational_means(ctx)
        total = h + a

        # Hierarchical shrinkage of the expected margin toward the league HCA.
        prior_mean = 0.0 if self._is_neutral(ctx) else pr.hca_prior_mean
        tau2, sig2 = pr.true_margin_sd ** 2, pr.input_margin_sd ** 2
        m_obs = h - a
        m_post = (tau2 * m_obs + sig2 * prior_mean) / (tau2 + sig2)
        s_post = math.sqrt(tau2 * sig2 / (tau2 + sig2))

        # Map to the log scoring-share scale for positivity-preserving quadrature.
        p_share = 0.5 + 0.5 * m_post / total
        r_mean = math.log(p_share / (1.0 - p_share))
        r_sd = s_post / (2.0 * total * p_share * (1.0 - p_share))

        mu_n = self._possession_mean(ctx)
        n_vals, n_pmf = self._possession_pmf(mu_n)

        nodes, weights = np.polynomial.hermite.hermgauss(pr.quadrature_nodes)
        weights = weights / weights.sum()
        probs = np.zeros(3)
        for x, w in zip(nodes, weights):
            r = r_mean + math.sqrt(2.0) * r_sd * x
            share = 1.0 / (1.0 + math.exp(-r))
            probs += w * self._three_way_given_means(
                total * share, total * (1.0 - share), n_vals, n_pmf, mu_n
            )

        probs = probs / probs.sum()
        p_home, p_draw, p_away = (float(v) for v in probs)
        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )


CandidateModel = NCAAWHalf1ThreeWayModel