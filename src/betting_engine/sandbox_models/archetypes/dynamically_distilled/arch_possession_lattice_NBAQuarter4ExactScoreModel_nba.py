import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, signal, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pre-loaded into execution scope by the sandbox
    DiscreteDistribution = globals().get("DiscreteDistribution")


class NBAQuarter4ExactScoreModel(BaseCandidateModel):
    """
    NBA | quarter_4 | exact_score

    Compound, game-state-mixed possession model for fourth-quarter exact scores.

    Structure
    ---------
    1. Shared possession stream: home possessions N ~ discretised Normal(mu_N, (cv*mu_N)^2).
       Q4 possessions are clock-bounded, so they are close to Poisson or underdispersed.
       That is why N is not forced to be NegBin. Away possessions are N + D, where
       D in {-1, 0, +1} with probabilities (1/4, 1/2, 1/4), reflecting alternation.
    2. Points per possession X in {0,1,2,3,4}. The shape is built mechanistically from
       turnover, FT-trip, shot-mix, make-rate, and-one and offensive-rebound rates. It is
       then exponentially tilted (the minimum-KL change of measure) so that the
       regime-mixture mean exactly matches the situational expected Q4 points.
    3. Game-state mixture over the entering margin Delta_3. Delta_3 is either observed or
       integrated over a prior implied by the quarter-level margin variance:
         - blowout regime: P(|margin| >= B at mid-quarter); pace and efficiency fall.
         - foul-game regime: P(1 <= |margin| <= W late); extra shared possessions follow
           Poisson(lambda). The leader shoots FT pairs and the trailer takes quick, 3-heavy shots.
         - normal regime: the remainder.
       The within-quarter margin volatility is derived from the possession model itself:
       sigma^2 = mu_N (Var X_h + Var X_a) + Var(N)(m_h - m_a)^2.
    4. The exact-score grid is computed by conditional n-fold convolution given the shared
       N. Positive home/away correlation and super-Poisson per-team dispersion
       (approx. E[X^2]/E[X]) therefore come out of the structure rather than from clamps.

    Every structural parameter lives in PRIORS. Each one is a domain-derived league
    fundamental and can be overridden through constructor kwargs. Weather inputs are
    irrelevant for an indoor sport and are deliberately ignored.
    """

    PRIORS: Dict[str, float] = {
        # Anchors (cold-start empirical means for Q4)
        "baseline_home_q4": 28.0,
        "baseline_away_q4": 27.0,
        "q4_share_of_game_points": 0.245,     # used only if full-game expectations are supplied
        "full_game_detection_points": 60.0,   # a Q4 team total above this is implausible
        # Possession-outcome mechanics (league fundamentals)
        "tov_rate": 0.125,
        "ft_trip_rate": 0.110,
        "ft_pct": 0.785,
        "three_share": 0.400,
        "three_pct": 0.362,
        "two_pct": 0.545,
        "and1_rate": 0.022,
        "oreb_rate": 0.255,
        # Possession stream
        "q4_pace_ratio": 0.975,               # Q4 possessions relative to the per-48 pace / 4
        "possession_cv": 0.085,
        # Blowout / garbage-time regime
        "blowout_margin": 18.0,
        "blowout_check_frac": 0.50,
        "blowout_pace_drop": 0.05,
        "blowout_eff_drop": 0.06,
        # Foul-game regime
        "foul_window_margin": 8.0,
        "foul_start_frac": 0.875,
        "foul_extra_possessions": 2.0,
        "foul_ft_pct": 0.800,
        "quick_tov": 0.100,
        "quick_three_share": 0.620,
        "quick_three_pct": 0.335,
        "quick_two_pct": 0.500,
        "quick_oreb": 0.200,
        # Situational residual adjustments
        "rating_residual_weight": 0.35,       # expectations usually embed ratings already
        "league_rating_per100": 114.5,
        "rest_eff_coef": 0.020,
        "rest_pace_coef": 0.010,
        # Numerics
        "tail_mass": 1e-10,
        "hermite_nodes": 48,
    }

    MARGIN_KEYS = ("margin_after_q3", "q3_margin", "score_diff_q3", "live_margin", "entering_margin")
    PACE_KEYS = ("pace", "expected_pace", "game_pace")

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_4_1108_025922",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_4",
        family: str = "exact_score",
        **prior_overrides: Any,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        except TypeError:
            super().__init__()
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.p: Dict[str, float] = dict(self.PRIORS)
        for k, v in prior_overrides.items():
            if k in self.p:
                self.p[k] = float(v)

        p = self.p
        self._k = np.arange(5, dtype=float)
        self._base = self._mechanics_pmf(
            p["tov_rate"], p["ft_trip_rate"], p["ft_pct"], p["three_share"],
            p["three_pct"], p["two_pct"], p["and1_rate"], p["oreb_rate"],
        )
        f = p["foul_ft_pct"]
        self._ft_pair = np.array([(1 - f) ** 2, 2 * f * (1 - f), f ** 2, 0.0, 0.0])
        self._quick = self._mechanics_pmf(
            p["quick_tov"], 0.0, p["ft_pct"], p["quick_three_share"],
            p["quick_three_pct"], p["quick_two_pct"], 0.0, p["quick_oreb"],
        )
        base_mean = self._mean(self._base)
        self._theta_blow = self._solve_tilt(self._base, (1.0 - p["blowout_eff_drop"]) * base_mean)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ primitives
    @staticmethod
    def _mechanics_pmf(tov, ft_trip, ft_pct, three_share, p3, p2, and1, oreb) -> np.ndarray:
        pmf = np.zeros(5)
        pmf[0] += tov
        pmf[0] += ft_trip * (1 - ft_pct) ** 2
        pmf[1] += ft_trip * 2 * ft_pct * (1 - ft_pct)
        pmf[2] += ft_trip * ft_pct ** 2
        fga = max(1.0 - tov - ft_trip, 0.0)
        m3 = fga * three_share * p3
        m2 = fga * (1 - three_share) * p2
        pmf[3] += m3 * (1 - and1 * ft_pct)
        pmf[4] += m3 * and1 * ft_pct
        pmf[2] += m2 * (1 - and1 * ft_pct)
        pmf[3] += m2 * and1 * ft_pct
        miss = fga - m3 - m2
        second = miss * oreb
        pmf[2] += second * p2
        pmf[0] += second * (1 - p2) + miss * (1 - oreb)
        return pmf / pmf.sum()

    def _tilt(self, pmf: np.ndarray, theta: float) -> np.ndarray:
        logw = np.log(np.maximum(pmf, 1e-300)) + theta * self._k
        logw -= logw.max()
        w = np.exp(logw) * (pmf > 0)
        return w / w.sum()

    def _mean(self, pmf: np.ndarray) -> float:
        return float(np.dot(self._k[: len(pmf)], pmf))

    def _var(self, pmf: np.ndarray) -> float:
        m = self._mean(pmf)
        return float(np.dot(self._k[: len(pmf)] ** 2, pmf) - m * m)

    def _solve_tilt(self, pmf: np.ndarray, target_mean: float) -> float:
        support_max = float(self._k[pmf > 0].max())
        target = min(max(target_mean, 1e-6), support_max - 1e-6)
        g = lambda th: self._mean(self._tilt(pmf, th)) - target
        return float(optimize.brentq(g, -30.0, 30.0, xtol=1e-12))

    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ situational inputs
    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        val = None
        block = ctx.get(side)
        if isinstance(block, dict):
            val = self._num(block.get(kind))
        if val is None:
            val = self._num(ctx.get(f"{side}_{kind}"))
        if val is None or val <= 0:
            return 1.0
        return val / self.p["league_rating_per100"] if val > 5.0 else val

    @staticmethod
    def _fatigue(rest: Optional[float]) -> float:
        # Diminishing-returns fatigue: -1 on a back-to-back, approaching 0 with more rest.
        return -math.exp(-max(rest, 0.0)) if rest is not None else 0.0

    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        p = self.p
        h = self._num(ctx.get("home_expected"), p["baseline_home_q4"])
        a = self._num(ctx.get("away_expected"), p["baseline_away_q4"])
        if h <= 0:
            h = p["baseline_home_q4"]
        if a <= 0:
            a = p["baseline_away_q4"]
        if max(h, a) > p["full_game_detection_points"]:
            h *= p["q4_share_of_game_points"]
            a *= p["q4_share_of_game_points"]

        w = p["rating_residual_weight"]
        h *= math.exp(w * 0.5 * (math.log(self._rating(ctx, "home", "attack")) + math.log(self._rating(ctx, "away", "defense"))))
        a *= math.exp(w * 0.5 * (math.log(self._rating(ctx, "away", "attack")) + math.log(self._rating(ctx, "home", "defense"))))

        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        gh = self._fatigue(rh) if rh is not None else None
        ga = self._fatigue(ra) if ra is not None else None
        if gh is not None and ga is not None:
            delta = p["rest_eff_coef"] * (gh - ga)
            h *= math.exp(0.5 * delta)
            a *= math.exp(-0.5 * delta)
        pace_g = np.mean([g for g in (gh, ga) if g is not None]) if (gh is not None or ga is not None) else 0.0
        pace_factor = math.exp(p["rest_pace_coef"] * float(pace_g))
        return h, a, pace_factor

    def _possessions(self, ctx: Dict[str, Any], mu_h: float, mu_a: float, pace_factor: float) -> float:
        pace = None
        for key in self.PACE_KEYS:
            pace = self._num(ctx.get(key))
            if pace:
                break
        if not pace:
            hp, ap = self._num(ctx.get("home_pace")), self._num(ctx.get("away_pace"))
            vals = [v for v in (hp, ap) if v and v > 0]
            pace = float(np.mean(vals)) if vals else None
        if pace and pace > 0:
            per_q = pace / 4.0 if pace > 40.0 else pace
            mu_n = per_q * self.p["q4_pace_ratio"] * pace_factor
        else:
            mu_n = 0.5 * (mu_h + mu_a) / self._mean(self._base) * pace_factor
        # Feasibility: implied points per possession must lie inside the {0..4} support.
        return max(mu_n, max(mu_h, mu_a) / 3.5)

    # ------------------------------------------------------------------ game-state regimes
    def _regimes(self, ctx, mu_h, mu_a, mu_n, xh, xa) -> Dict[str, float]:
        p = self.p
        sd_n = p["possession_cv"] * mu_n
        sigma_q = math.sqrt(mu_n * (self._var(xh) + self._var(xa)) + sd_n ** 2 * (self._mean(xh) - self._mean(xa)) ** 2)
        drift = mu_h - mu_a

        observed = None
        for key in self.MARGIN_KEYS:
            observed = self._num(ctx.get(key))
            if observed is not None:
                break
        if observed is not None:
            deltas, weights = np.array([observed]), np.array([1.0])
        else:
            nodes, w = np.polynomial.hermite_e.hermegauss(int(p["hermite_nodes"]))
            deltas = 3.0 * drift + math.sqrt(3.0) * sigma_q * nodes
            weights = w / w.sum()

        tb, tc = p["blowout_check_frac"], p["foul_start_frac"]
        mb = deltas + drift * tb
        sb = sigma_q * math.sqrt(tb)
        B = p["blowout_margin"]
        p_blow = stats.norm.sf(B, mb, sb) + stats.norm.cdf(-B, mb, sb)

        mc = deltas + drift * tc
        sc = sigma_q * math.sqrt(tc)
        W = p["foul_window_margin"]
        p_home_lead = stats.norm.cdf(W + 0.5, mc, sc) - stats.norm.cdf(0.5, mc, sc)
        p_away_lead = stats.norm.cdf(-0.5, mc, sc) - stats.norm.cdf(-W - 0.5, mc, sc)
        p_close = (p_home_lead + p_away_lead) * (1.0 - p_blow)

        pi_b = float(np.dot(weights, p_blow))
        pi_c = float(np.dot(weights, p_close))
        num = float(np.dot(weights, p_home_lead * (1.0 - p_blow)))
        q = num / pi_c if pi_c > 1e-15 else 0.5
        return {"blow": pi_b, "close": pi_c, "normal": max(1.0 - pi_b - pi_c, 0.0),
                "home_lead_given_close": q, "sigma_q4_margin": sigma_q}

    def _solve_side_tilt(self, target: float, mu_n: float, R: Dict[str, float], lead_prob: float) -> float:
        p = self.p
        lam = p["foul_extra_possessions"]
        extras = lam * (lead_prob * self._mean(self._ft_pair) + (1 - lead_prob) * self._mean(self._quick))
        mu_b = mu_n * (1.0 - p["blowout_pace_drop"])

        def total_mean(th: float) -> float:
            m = self._mean(self._tilt(self._base, th))
            mb = self._mean(self._tilt(self._base, th + self._theta_blow))
            return (R["normal"] * mu_n * m + R["blow"] * mu_b * mb + R["close"] * (mu_n * m + extras)) - target

        lo, hi = -30.0, 30.0
        if total_mean(lo) >= 0:
            return lo
        if total_mean(hi) <= 0:
            return hi
        return float(optimize.brentq(total_mean, lo, hi, xtol=1e-12))

    # ------------------------------------------------------------------ convolution engine
    @staticmethod
    def _powers(pmf: np.ndarray, count: int, L: int) -> np.ndarray:
        pw = np.zeros((count + 1, L))
        pw[0, 0] = 1.0
        for n in range(1, count + 1):
            pw[n] = np.convolve(pw[n - 1], pmf)[:L]
        return pw

    @staticmethod
    def _n_pmf(mu: float, sd: float, n_max: int) -> np.ndarray:
        edges = np.arange(n_max + 2) - 0.5
        cdf = stats.norm.cdf(edges, mu, sd)
        pmf = np.diff(cdf)
        return pmf / pmf.sum()

    @staticmethod
    def _block(pn: np.ndarray, pw_h: np.ndarray, pw_a: np.ndarray) -> np.ndarray:
        n_max = len(pn) - 1
        idx = np.arange(n_max + 1)
        a_mix = 0.25 * pw_a[np.maximum(idx - 1, 0)] + 0.5 * pw_a[idx] + 0.25 * pw_a[idx + 1]
        return (pw_h[: n_max + 1] * pn[:, None]).T @ a_mix

    # ------------------------------------------------------------------ main entry
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        p = self.p
        mu_h, mu_a, pace_factor = self._targets(ctx)
        mu_n = self._possessions(ctx, mu_h, mu_a, pace_factor)

        # Pass 1: regime-free tilt, used to derive the margin volatility.
        xh0 = self._tilt(self._base, self._solve_tilt(self._base, mu_h / mu_n))
        xa0 = self._tilt(self._base, self._solve_tilt(self._base, mu_a / mu_n))
        R = self._regimes(ctx, mu_h, mu_a, mu_n, xh0, xa0)
        q = R["home_lead_given_close"]

        # Pass 2: tilts that anchor the full regime-mixture means exactly.
        th_h = self._solve_side_tilt(mu_h, mu_n, R, q)
        th_a = self._solve_side_tilt(mu_a, mu_n, R, 1.0 - q)
        xh, xa = self._tilt(self._base, th_h), self._tilt(self._base, th_a)
        xh_b, xa_b = self._tilt(self._base, th_h + self._theta_blow), self._tilt(self._base, th_a + self._theta_blow)

        sd_n = p["possession_cv"] * mu_n
        mu_b = mu_n * (1.0 - p["blowout_pace_drop"])
        n_max = int(math.ceil(mu_n + 7.0 * sd_n)) + 1
        lam = p["foul_extra_possessions"]
        k_max = int(stats.poisson.ppf(1.0 - p["tail_mass"], lam)) + 1
        L = 4 * (n_max + 1 + k_max) + 1

        pn_norm = self._n_pmf(mu_n, sd_n, n_max)
        pn_blow = self._n_pmf(mu_b, p["possession_cv"] * mu_b, n_max)

        J_norm = self._block(pn_norm, self._powers(xh, n_max + 1, L), self._powers(xa, n_max + 1, L))
        J_blow = self._block(pn_blow, self._powers(xh_b, n_max + 1, L), self._powers(xa_b, n_max + 1, L))

        # Foul-game extras: K ~ Poisson(lam) shared extra possessions; leader -> FT pairs, trailer -> quick shots.
        Lk = 4 * k_max + 1
        ft_pw = self._powers(self._ft_pair, k_max, Lk)
        qk_pw = self._powers(self._quick, k_max, Lk)
        pk = stats.poisson.pmf(np.arange(k_max + 1), lam)
        pk /= pk.sum()
        kern = q * (ft_pw * pk[:, None]).T @ qk_pw + (1.0 - q) * (qk_pw * pk[:, None]).T @ ft_pw
        J_close = np.maximum(signal.fftconvolve(J_norm, kern)[:L, :L], 0.0)

        J = R["normal"] * J_norm + R["blow"] * J_blow + R["close"] * J_close
        J = np.maximum(J, 0.0)
        J /= J.sum()

        # Tail trimming driven by the cumulative-mass tolerance.
        tol = p["tail_mass"]
        mh_cdf, ma_cdf = np.cumsum(J.sum(axis=1)), np.cumsum(J.sum(axis=0))
        h_hi = int(np.searchsorted(mh_cdf, 1.0 - tol)) + 1
        a_hi = int(np.searchsorted(ma_cdf, 1.0 - tol)) + 1
        G = J[: h_hi + 1, : a_hi + 1]
        G = G / G.sum()

        hs, as_ = np.arange(G.shape[0]), np.arange(G.shape[1])
        Eh, Ea = float(G.sum(1) @ hs), float(G.sum(0) @ as_)
        Vh = float(G.sum(1) @ hs ** 2) - Eh ** 2
        Va = float(G.sum(0) @ as_ ** 2) - Ea ** 2
        cov = float(hs @ G @ as_) - Eh * Ea
        self.last_diagnostics = {
            "target_home": mu_h, "target_away": mu_a, "model_home_mean": Eh, "model_away_mean": Ea,
            "home_dispersion_index": Vh / Eh, "away_dispersion_index": Va / Ea,
            "home_away_corr": cov / math.sqrt(Vh * Va), "mu_possessions": mu_n,
            "regime_normal": R["normal"], "regime_blowout": R["blow"], "regime_foul_game": R["close"],
            "home_lead_given_foul_game": q, "sigma_q4_margin": R["sigma_q4_margin"],
            "p_home_win_q4": float(np.tril(G, -1).sum()), "p_draw_q4": float(np.trace(G)),
        }

        floor = tol * 1e-2
        scores = {(int(i), int(j)): float(G[i, j]) for i, j in zip(*np.nonzero(G > floor))}
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}
        return self._emit(scores)

    @staticmethod
    def _emit(scores: Dict[Tuple[int, int], float]) -> "DiscreteDistribution":
        dd = DiscreteDistribution if DiscreteDistribution is not None else globals().get("DiscreteDistribution")
        try:
            return dd.from_scores(scores)
        except (TypeError, ValueError, KeyError, AttributeError):
            return dd.from_scores({f"{h}-{a}": v for (h, a), v in scores.items()})