import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, sparse
from scipy.sparse.linalg import expm_multiply

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pre-loaded in execution scope
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class LaligaHalf1ExactTotalModel(BaseCandidateModel):
    """
    LALIGA | half_1 | exact_total: a state-switching counting process.

    Structure
    ---------
    Each team's intensity is
        lambda_i(t) = b_i * g(t) * exp(kappa * 1{scoreline not level}).
    g(t) is the within-half time profile, normalised to integral 1 over the half.
    Because g multiplies every state's generator equally, the end-of-half score
    distribution depends on g only through its integral (time-change invariance).
    The profile therefore shapes in-play timing but not the settled exact total.
    It is integrated analytically, so no arbitrary profile constant is introduced.

    kappa is the opener-driven regime switch: the trailing side commits bodies
    and the leader counters, so the rate rises once the match is no longer level.

    rho is a low-score dependence term on the {0,1}x{0,1} block. It moves mass
    delta into (0,0) and (1,1) and takes the same delta out of (0,1) and (1,0).
    This preserves both marginals and E[T] exactly, raises P(T=0) by delta and
    raises Var(T) by 2*delta. rho lies in [-1, 1], which are the exact
    non-negativity limits of the cells, not a clamp.

    The base rates b_H and b_A are solved by fixed point so that E[H] = mu_H and
    E[A] = mu_A for every (kappa, rho). The anchors are never distorted.
    (kappa, rho) are fitted by matching the target P(T=0) and Var(T)/E(T).

    Cold-start honesty
    ------------------
    No settled sample exists. The default targets therefore come only from the
    stated dispersion index D. With D = 1 the zero target is e^{-m}, and the fit
    returns kappa = rho = 0, so the model is anchored Poisson. When empirical
    first-half moments are supplied, through `calibration` or the context keys
    `empirical_p_zero` / `dispersion_index`, the fit moves off Poisson toward
    the data. That would capture the extra 0-0 mass and the post-opener
    switching if they are present.

    The truncation is adaptive: probability that leaks above the lattice is
    tracked and the lattice grows until the leak is below the tolerance.
    """

    # Documented domain priors. Every entry can be overridden via `calibration`.
    DOMAIN_PRIORS: Dict[str, float] = {
        "home_expected": 0.80,         # empirical LALIGA 1H home baseline
        "away_expected": 0.60,         # empirical LALIGA 1H away baseline
        "dispersion_index": 1.00,      # observed / prior Var(T)/E(T)
        "recovery_tau_days": 3.0,      # ~72h neuromuscular/glycogen recovery constant
        "rest_elasticity": 0.25,       # 1H share sensitivity to freshness log-ratio
        "kappa_search_halfwidth": 2.0, # optimiser search region (e^±2 state multiplier)
    }

    _LEAK_TOL = 1e-12
    _MEAN_TOL = 1e-13
    _MAX_FP_ITER = 200

    def __init__(
        self,
        model_id: str = "codex_synth_laliga_half_1_1108_015537",
        version: str = "1.0.0",
        league: str = "LALIGA",
        scope: str = "half_1",
        family: str = "exact_total",
        calibration: Optional[Dict[str, float]] = None,
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors: Dict[str, float] = dict(self.DOMAIN_PRIORS)
        if calibration:
            self.priors.update({k: float(v) for k, v in calibration.items()})
        self.last_fit: Dict[str, float] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) else default

    def _team_value(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(key))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{key}"))

    @staticmethod
    def _logit(p: float) -> float:
        return math.log(p) - math.log1p(-p)

    @staticmethod
    def _expit(z: float) -> float:
        return 1.0 / (1.0 + math.exp(-z))

    # ------------------------------------------------------- situational rates
    def _intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h0 = self._num(ctx.get("home_expected"), self.priors["home_expected"])
        a0 = self._num(ctx.get("away_expected"), self.priors["away_expected"])
        if h0 is None or h0 <= 0:
            h0 = self.priors["home_expected"]
        if a0 is None or a0 <= 0:
            a0 = self.priors["away_expected"]

        total = h0 + a0
        z = self._logit(h0 / total)

        # Attack x opponent-defense exposure. Defense is read as a conceding
        # index (higher = more exposed). It is pooled in logit space, at equal
        # precision, with the anchor share. Only the split moves, so the anchored
        # total is not double-counted.
        att_h = self._team_value(ctx, "home", "attack")
        def_h = self._team_value(ctx, "home", "defense")
        att_a = self._team_value(ctx, "away", "attack")
        def_a = self._team_value(ctx, "away", "defense")
        if all(v is not None and v > 0 for v in (att_h, def_h, att_a, def_a)):
            xh, xa = att_h * def_a, att_a * def_h
            z = 0.5 * (z + self._logit(xh / (xh + xa)))

        # Rest: exponential recovery freshness f(r) = 1 - exp(-r / tau).
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        tau = self.priors["recovery_tau_days"]
        if rh is not None and ra is not None and rh > 0 and ra > 0 and tau > 0:
            fh, fa = -math.expm1(-rh / tau), -math.expm1(-ra / tau)
            z += self.priors["rest_elasticity"] * math.log(fh / fa)

        # Tempo: goal intensity is proportional to possession volume.
        pace_ratio = 1.0
        league_pace = self._num(ctx.get("league_pace"))
        pace = self._num(ctx.get("pace"))
        if pace is None:
            ph, pa = self._team_value(ctx, "home", "pace"), self._team_value(ctx, "away", "pace")
            if ph is not None and pa is not None and ph > 0 and pa > 0:
                pace = math.sqrt(ph * pa)
        if pace is not None and pace > 0:
            if league_pace is not None and league_pace > 0:
                pace_ratio = pace / league_pace
            elif self._num(ctx.get("pace_index")) is None and pace < 5.0:
                pace_ratio = pace  # already a relative index
        pidx = self._num(ctx.get("pace_index"))
        if pidx is not None and pidx > 0:
            pace_ratio = pidx
        total *= pace_ratio

        share = self._expit(z)
        return total * share, total * (1.0 - share)

    # --------------------------------------------------------- CTMC solution
    def _propagate_fixed(self, bH: float, bA: float, kappa: float, N: int):
        H, A = np.indices((N + 1, N + 1))
        mask = (H + A) <= N
        h, a = H[mask], A[mask]
        n = h.size
        index = -np.ones((N + 1, N + 1), dtype=int)
        index[h, a] = np.arange(n)
        overflow = n

        mult = np.where(h != a, math.exp(kappa), 1.0)
        rh, ra = bH * mult, bA * mult
        inside = (h + a + 1) <= N
        th = np.where(inside, index[np.minimum(h + 1, N), a], overflow)
        ta = np.where(inside, index[h, np.minimum(a + 1, N)], overflow)

        src = np.arange(n)
        rows = np.concatenate([src, th, ta])
        cols = np.concatenate([src, src, src])
        vals = np.concatenate([-(rh + ra), rh, ra])
        QT = sparse.csc_matrix((vals, (rows, cols)), shape=(n + 1, n + 1))

        p0 = np.zeros(n + 1)
        p0[index[0, 0]] = 1.0
        pt = np.maximum(expm_multiply(QT, p0), 0.0)  # integrated half exposure = 1

        P = np.zeros((N + 1, N + 1))
        P[h, a] = pt[:n]
        return P, float(pt[n])

    def _propagate(self, bH: float, bA: float, kappa: float) -> np.ndarray:
        from scipy.stats import poisson

        lam_eff = (bH + bA) * max(1.0, math.exp(kappa))
        N = max(2, int(poisson.isf(self._LEAK_TOL, lam_eff)) + 1)
        while True:
            P, leak = self._propagate_fixed(bH, bA, kappa, N)
            if leak <= self._LEAK_TOL:
                return P / P.sum()
            N += max(2, N // 2)

    def _matched_lattice(self, muH: float, muA: float, kappa: float) -> np.ndarray:
        bH, bA = muH, muA
        P = None
        for _ in range(self._MAX_FP_ITER):
            P = self._propagate(bH, bA, kappa)
            k = np.arange(P.shape[0])
            eH = float(P.sum(axis=1) @ k)
            eA = float(P.sum(axis=0) @ k)
            rH, rA = muH / eH, muA / eA
            if max(abs(rH - 1.0), abs(rA - 1.0)) < self._MEAN_TOL:
                break
            bH *= rH
            bA *= rA
        return P

    @staticmethod
    def _apply_rho(P: np.ndarray, rho: float) -> np.ndarray:
        Q = P.copy()
        if rho >= 0:
            delta = rho * min(Q[0, 1], Q[1, 0])
        else:
            delta = rho * min(Q[0, 0], Q[1, 1])
        Q[0, 0] += delta
        Q[1, 1] += delta
        Q[0, 1] -= delta
        Q[1, 0] -= delta
        return np.maximum(Q, 0.0)

    @staticmethod
    def _total_moments(P: np.ndarray) -> Tuple[float, float, float]:
        H, A = np.indices(P.shape)
        tot = np.bincount((H + A).ravel(), weights=P.ravel())
        k = np.arange(tot.size)
        s = tot.sum()
        tot = tot / s
        m = float(tot @ k)
        v = float(tot @ (k * k)) - m * m
        return m, v, float(tot[0])

    # --------------------------------------------------------------- targets
    def _targets(self, ctx: Dict[str, Any], mean_total: float) -> Tuple[float, float]:
        D = self._num(ctx.get("dispersion_index"))
        if D is None:
            D = self._num(ctx.get("empirical_dispersion"), self.priors["dispersion_index"])
        p0 = self._num(ctx.get("empirical_p_zero"))
        if p0 is None:
            p0 = self.priors.get("empirical_p_zero")
        if p0 is None or not (0.0 < p0 < 1.0):
            # Zero mass implied by mean and dispersion alone:
            # NB (D > 1) and binomial (D < 1) both give D^{m/(1-D)}; D -> 1 gives Poisson.
            if abs(D - 1.0) < 1e-12:
                p0 = math.exp(-mean_total)
            else:
                p0 = D ** (mean_total / (1.0 - D))
        return float(p0), float(D)

    # ------------------------------------------------------------------ fit
    def _fit(self, muH: float, muA: float, p0_t: float, D_t: float) -> Tuple[float, float, np.ndarray]:
        cache: Dict[float, np.ndarray] = {}

        def lattice(kappa: float) -> np.ndarray:
            key = float(kappa)
            if key not in cache:
                cache[key] = self._matched_lattice(muH, muA, key)
            return cache[key]

        def resid(x: np.ndarray) -> np.ndarray:
            rho, kappa = float(x[0]), float(x[1])
            m, v, p0 = self._total_moments(self._apply_rho(lattice(kappa), rho))
            return np.array([(p0 - p0_t) / p0_t, (v / m - D_t) / D_t])

        x0 = np.zeros(2)
        if np.linalg.norm(resid(x0)) < 1e-9:
            return 0.0, 0.0, lattice(0.0)

        K = self.priors["kappa_search_halfwidth"]
        sol = optimize.least_squares(
            resid, x0=x0, bounds=([-1.0, -K], [1.0, K]), method="trf",
            diff_step=1e-6, xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=300,
        )
        rho, kappa = float(sol.x[0]), float(sol.x[1])
        return rho, kappa, lattice(kappa)

    # ------------------------------------------------------------- interface
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        muH, muA = self._intensities(ctx)
        p0_t, D_t = self._targets(ctx, muH + muA)
        rho, kappa, P = self._fit(muH, muA, p0_t, D_t)
        P = self._apply_rho(P, rho)
        P = P / P.sum()

        m, v, p0 = self._total_moments(P)
        self.last_fit = {
            "mu_home": muH, "mu_away": muA, "rho": rho, "kappa": kappa,
            "target_p_zero": p0_t, "target_dispersion": D_t,
            "fitted_p_zero": p0, "fitted_dispersion": v / m, "mean_total": m,
        }

        scores: Dict[Tuple[int, int], float] = {}
        for h in range(P.shape[0]):
            for a in range(P.shape[1]):
                p = float(P[h, a])
                if p > 0.0:
                    scores[(h, a)] = p
        s = sum(scores.values())
        scores = {k: p / s for k, p in scores.items()}
        return DiscreteDistribution.from_scores(scores)