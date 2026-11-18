import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # DiscreteDistribution is pre-loaded into the execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class WNBAQuarter2PossessionLatticeModel(BaseCandidateModel):
    """
    Possession-lattice exact-score model for WNBA 2nd-quarter scoring.

    Structure
    ---------
    1. Pace layer. A shared per-team possession count M ~ discretised Normal(m, s^2).
       Alternation sets the home/away split to N_h - N_a in {-1, 0, +1}.
       The pace mean comes from the context's pace or tempo, adjusted for rest and fatigue.
       The pace variance follows a renewal-process argument: Var/Mean of the count is small.
    2. Efficiency layer. Each possession yields k in {0,1,2,3,4} points.
       The per-team PMF is the minimum-KL (exponential) tilt of a WNBA possession-outcome
       prior, solved so that E[N] * E[pts/poss] + end-of-half bonus mass = team expectation.
       This anchors the means exactly while keeping the empirical lattice shape.
    3. Q2 end-of-half layer. With probability q a team gets a rushed bonus possession
       (a 2-for-1 or a heave) with a low-efficiency, three-heavy PMF.
    4. Joint PMF.
           P(H=h, A=a) = sum_M sum_d P(M) P(d) [p_h^{*N_h} * K_h](h) [p_a^{*N_a} * K_a](a)
       Dispersion (about 1.3-1.45), parity bias and positive cross-team covariance all
       come out of the structure endogenously. Nothing is clamped.
    """

    # ---- Domain priors (WNBA possession mechanics) ---------------------------------
    # Possession outcome prior: P(0), P(1), P(2), P(3), P(4).
    # This reflects empty trips, 1-of-2 FT / single-FT trips, 2s (dominant), 3s,
    # and and-one 3s.
    _POSS_PRIOR = np.array([0.505, 0.055, 0.315, 0.120, 0.005])
    # Rushed end-of-half bonus possession: mostly empty, three-heavy.
    _HEAVE_PMF = np.array([0.80, 0.02, 0.08, 0.10, 0.00])
    _EOH_BONUS_PROB = 0.30            # P(team gains a rushed extra possession before half)
    _LEAGUE_PACE_40 = 80.0            # possessions per team per 40 minutes
    _QUARTER_SHARE = 0.25             # quarter fraction of regulation
    _Q2_PACE_FACTOR = 1.01            # Q2: bench stints + late-half 2-for-1s run slightly faster
    _PACE_VAR_RATIO = 0.12            # Var/Mean of the per-team possession count
    _SPLIT_PROB = 0.25                # P(N_h - N_a = +1) = P(-1)
    _FATIGUE_PACE_ELASTICITY = 0.015  # pace drag per unit of exp(-rest) fatigue
    _FATIGUE_VAR_ELASTICITY = 0.25    # extra pace variance from fatigue (rotation chaos)
    _REST_REF_DAYS = 2.0
    _REST_EFF_ELASTICITY = 0.012      # used only when expectations must be built from ratings
    _BASE_HOME = 20.00
    _BASE_AWAY = 18.75
    _FULL_GAME_THRESHOLD = 45.0       # inputs above this are treated as full-game expectations

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_2_1108_060542",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_2",
        family: str = "exact_score",
    ):
        super().__init__(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )

    # ------------------------------------------------------------------ helpers ----
    @staticmethod
    def _f(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _team(ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        t = ctx.get(side)
        return t if isinstance(t, dict) else {}

    def _rating(self, team: Dict[str, Any], ctx: Dict[str, Any], side: str, key: str) -> float:
        for v in (team.get(key), ctx.get(f"{side}_{key}")):
            f = self._f(v)
            if f is not None and f > 0:
                return f
        return 1.0

    def _rest(self, ctx: Dict[str, Any], side: str) -> float:
        r = self._f(ctx.get(f"{side}_rest_days"))
        return r if r is not None and r >= 0 else self._REST_REF_DAYS

    def _expectations(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h_rest, a_rest = self._rest(ctx, "home"), self._rest(ctx, "away")
        out = []
        for side, base, opp in (("home", self._BASE_HOME, "away"), ("away", self._BASE_AWAY, "home")):
            v = self._f(ctx.get(f"{side}_expected"))
            if v is not None and v > 0:
                # Accept full-game inputs by mapping them onto quarter scale.
                if v > self._FULL_GAME_THRESHOLD:
                    v *= self._QUARTER_SHARE
                out.append(v)
                continue
            # Fallback: build the expectation from exposure ratings and the rest differential.
            att = self._rating(self._team(ctx, side), ctx, side, "attack")
            dfn = self._rating(self._team(ctx, opp), ctx, opp, "defense")
            own_rest = h_rest if side == "home" else a_rest
            opp_rest = a_rest if side == "home" else h_rest
            rest_eff = 1.0 + self._REST_EFF_ELASTICITY * math.tanh(own_rest - opp_rest)
            out.append(base * att * dfn * rest_eff)
        return out[0], out[1]

    def _pace_per_team_quarter(self, ctx: Dict[str, Any]) -> float:
        cands = []
        for key in ("pace", "tempo", "expected_pace"):
            v = self._f(ctx.get(key))
            if v is not None and v > 0:
                cands.append(v)
        for side in ("home", "away"):
            t = self._team(ctx, side)
            for key in ("pace", "tempo"):
                v = self._f(t.get(key))
                if v is not None and v > 0:
                    cands.append(v)
        pace40 = float(np.mean(cands)) if cands else self._LEAGUE_PACE_40
        # Interpret small values as already quarter-scaled.
        per_q = pace40 * self._QUARTER_SHARE if pace40 > 40.0 else pace40
        return per_q * self._Q2_PACE_FACTOR

    def _fatigue(self, ctx: Dict[str, Any]) -> float:
        ref = 2.0 * math.exp(-self._REST_REF_DAYS)
        return (
            math.exp(-self._rest(ctx, "home")) + math.exp(-self._rest(ctx, "away")) - ref
        )

    def _tilt(self, target_ppp: float) -> np.ndarray:
        """Minimum-KL tilt of the possession prior with mean == target_ppp."""
        k = np.arange(len(self._POSS_PRIOR), dtype=float)
        logb = np.log(self._POSS_PRIOR)
        kmax = k[-1]
        # Support of the tilt family is the open interval (0, kmax).
        eps = 1e-9
        t = min(max(target_ppp, eps), kmax - eps)

        def mean_at(theta: float) -> float:
            w = logb + theta * k
            w = np.exp(w - w.max())
            return float((w * k).sum() / w.sum()) - t

        lo, hi = -1.0, 1.0
        while mean_at(lo) > 0:
            lo *= 2.0
        while mean_at(hi) < 0:
            hi *= 2.0
        theta = brentq(mean_at, lo, hi, xtol=1e-12)
        w = np.exp(logb + theta * k - (logb + theta * k).max())
        return w / w.sum()

    @staticmethod
    def _powers(p: np.ndarray, kernel: np.ndarray, n_max: int, L: int) -> np.ndarray:
        """rows[n] = (p^{*n} * kernel) truncated to L lattice points."""
        rows = np.zeros((n_max + 1, L))
        cur = np.zeros(L)
        cur[0] = 1.0
        for n in range(n_max + 1):
            rows[n] = np.convolve(cur, kernel)[:L]
            cur = np.convolve(cur, p)[:L]
        return rows

    # --------------------------------------------------------------- core model ----
    def _joint_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a = self._expectations(ctx)

        # Pace layer.
        fatigue = self._fatigue(ctx)
        m_team = self._pace_per_team_quarter(ctx) * (1.0 - self._FATIGUE_PACE_ELASTICITY * fatigue)
        var_m = m_team * self._PACE_VAR_RATIO * (1.0 + self._FATIGUE_VAR_ELASTICITY * max(fatigue, 0.0))
        s = math.sqrt(var_m)
        w = self._SPLIT_PROB
        m_core = m_team - w  # E[N_team] = E[M] + w for both teams (symmetric split)

        lo = max(0, int(math.floor(m_core - 7.0 * s)))
        hi = int(math.ceil(m_core + 7.0 * s))
        Ms = np.arange(lo, hi + 1)
        edges = norm.cdf((np.append(Ms, hi + 1) - 0.5 - m_core) / s)
        pM = np.diff(edges)
        pM /= pM.sum()
        e_m = float((Ms * pM).sum())
        e_n = e_m + w

        # End-of-half bonus kernel.
        q = self._EOH_BONUS_PROB
        heave_mean = float((np.arange(5) * self._HEAVE_PMF).sum())
        kernel = q * self._HEAVE_PMF
        kernel[0] += 1.0 - q

        # Efficiency layer: solve the PPP so that team means are matched exactly.
        p_h = self._tilt((mu_h - q * heave_mean) / e_n)
        p_a = self._tilt((mu_a - q * heave_mean) / e_n)

        # Lattice extent from the implied dispersion (numerical, not a probability clamp).
        def var_pp(p):
            k = np.arange(5)
            return float((p * k * k).sum() - (p * k).sum() ** 2)

        sd_max = math.sqrt(max(
            e_n * var_pp(p_h) + var_m * (mu_h / e_n) ** 2,
            e_n * var_pp(p_a) + var_m * (mu_a / e_n) ** 2,
        ))
        L = int(math.ceil(max(mu_h, mu_a) + 10.0 * sd_max)) + 1

        n_max = int(Ms.max()) + 1
        R_h = self._powers(p_h, kernel, n_max, L)
        R_a = self._powers(p_a, kernel, n_max, L)

        J = np.zeros((L, L))
        split = ((1, 0, w), (0, 0, 1.0 - 2.0 * w), (0, 1, w))
        for M, pm in zip(Ms, pM):
            for dh, da, pd in split:
                J += (pm * pd) * np.outer(R_h[M + dh], R_a[M + da])

        J /= J.sum()
        return J

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        J = self._joint_matrix(ctx)
        tol = 1e-12
        idx = np.argwhere(J > tol)
        mass = J[J > tol].sum()
        scores = {(int(h), int(a)): float(J[h, a] / mass) for h, a in idx}
        return DiscreteDistribution.from_scores(scores)

    # ----------------------------------------------------------- diagnostics ----
    def summary_moments(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        J = self._joint_matrix(game_context or {})
        g = np.arange(J.shape[0])
        ph, pa = J.sum(axis=1), J.sum(axis=0)
        eh, ea = float((g * ph).sum()), float((g * pa).sum())
        vh = float((g * g * ph).sum() - eh ** 2)
        va = float((g * g * pa).sum() - ea ** 2)
        cov = float((np.outer(g, g) * J).sum() - eh * ea)
        return {
            "home_mean": eh,
            "away_mean": ea,
            "home_dispersion": vh / eh,
            "away_dispersion": va / ea,
            "correlation": cov / math.sqrt(vh * va),
            "p_home_win": float(np.tril(J, -1).sum()),
            "p_draw": float(np.trace(J)),
            "p_away_win": float(np.triu(J, 1).sum()),
        }