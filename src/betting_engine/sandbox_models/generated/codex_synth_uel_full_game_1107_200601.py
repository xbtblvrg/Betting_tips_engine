import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded into the execution scope.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: F821
except NameError:  # pragma: no cover - fallback for standalone import
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UELFullGameExactTotalModel(BaseCandidateModel):
    """
    UEL | full_game | exact_total. Shared-frailty negative multinomial with a
    stage-conditioned Dixon-Coles low-score correction.

    Generative structure
    --------------------
      eps ~ Gamma(k, 1/k)                 (E[eps]=1, Var[eps]=1/k)
      H | eps ~ Poisson(lam_H * eps)
      A | eps ~ Poisson(lam_A * eps)

    Integrating out eps gives a closed-form negative multinomial score law:
      log P(h,a) = lnG(k+h+a) - lnG(k) - ln h! - ln a!
                   + k ln(k/(k+L)) + h ln(lam_H/(k+L)) + a ln(lam_A/(k+L)),
      L = lam_H + lam_A.
    So T = H + A ~ NegBin(k, k/(k+L)) and Var(T)/E(T) = 1 + L/k. The shared
    frailty models match-level rate uncertainty: quality gaps, rotation, and
    travel. It thickens both the 0-1 and 4+ total tails. As k -> inf the model
    reduces exactly to independent Poisson.

    A Dixon-Coles tau_rho is then applied to the {0-0, 1-0, 0-1, 1-1} cells.
    rho is conditioned on a game-state bucket: league phase, KO leg 1, KO leg 2
    by first-leg aggregate gap, and final. The lattice is renormalised.

    Cold-start parameter policy
    ---------------------------
    The segment has no settled sample, so nothing is fit yet. Defaults come
    from the stated empirical moments:
      * means:       home 1.60, away 1.20 (constructor arguments)
      * dispersion:  k = mean_total / (D - 1) with D = 1.000 observed, which
                     gives k = inf (Poisson limit)
      * rho:         0 in every bucket (no evidence of low-score dependence)
      * pace:        elasticity 1 (goals scale with possession volume)
      * rest:        weak prior elasticity, documented below
    Every one of these is a constructor argument and is re-estimated by
    penalised MLE in `fit()` once UEL results exist. There are no static
    probability or odds clamps. Lattice truncation is set by a numerical tail
    tolerance, and rho's admissible range is the analytic region where all tau
    values stay non-negative.
    """

    _BUCKETS: Tuple[str, ...] = (
        "league_phase",
        "ko_leg1",
        "ko_leg2_level",
        "ko_leg2_close",
        "ko_leg2_lopsided",
        "ko_final",
    )

    def __init__(
        self,
        model_id: str = "codex_synth_uel_full_game_1107_200601",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "full_game",
        family: str = "exact_total",
        home_mean_prior: float = 1.60,
        away_mean_prior: float = 1.20,
        dispersion_index: float = 1.000,
        rest_elasticity_prior: float = 0.04,
        pace_elasticity_prior: float = 1.0,
        rho_prior: Optional[Dict[str, float]] = None,
        rho_shrinkage_sd: float = 0.10,
        tail_tolerance: float = 1e-12,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:  # positional-only base signature
            super().__init__(model_id, version, league, scope, family)

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.home_mean = float(home_mean_prior)
        self.away_mean = float(away_mean_prior)

        # Dispersion from the observed index: Var/Mean = 1 + L/k  =>  k = L/(D-1).
        mean_total = self.home_mean + self.away_mean
        excess = float(dispersion_index) - 1.0
        self.k: float = mean_total / excess if excess > 0.0 else math.inf

        # Rest elasticity prior: lambda scales as ((1+rest_own)/(1+rest_opp))^beta.
        # 0.04 is roughly a 3% rate change per doubling of the rest ratio. It is
        # a weak domain prior for Thursday-Sunday congestion and is replaced by
        # the MLE in fit().
        self.beta_rest = float(rest_elasticity_prior)
        self.beta_pace = float(pace_elasticity_prior)

        self.rho: Dict[str, float] = {b: 0.0 for b in self._BUCKETS}
        if rho_prior:
            self.rho.update({str(b): float(v) for b, v in rho_prior.items()})
        self.rho_shrinkage_sd = float(rho_shrinkage_sd)
        self.tail_tolerance = float(tail_tolerance)
        self.is_fitted = False

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) else None

    def _side_rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            for k in (key, f"{key}_rating", f"{key}_strength"):
                v = self._num(block.get(k))
                if v is not None and v > 0.0:
                    return v
        for k in (f"{side}_{key}", f"{side}_{key}_rating", f"{side}_{key}_strength"):
            v = self._num(ctx.get(k))
            if v is not None and v > 0.0:
                return v
        return None

    def _rest_log_ratio(self, ctx: Dict[str, Any]) -> float:
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None or rh < 0.0 or ra < 0.0:
            return 0.0
        return math.log((1.0 + rh) / (1.0 + ra))

    def _pace_log(self, ctx: Dict[str, Any]) -> float:
        for key in ("pace", "tempo", "pace_index", "tempo_index"):
            v = self._num(ctx.get(key))
            if v is not None and v > 0.0:
                return math.log(v)
        return 0.0

    def _base_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """
        Matchup expectation before rest and pace.

        If the feed supplies home_expected / away_expected, those are taken as
        the matchup-specific means: they already encode attack/defense, so
        multiplying again would double count. Otherwise the 1.60 / 1.20 UEL
        anchors are scaled by multiplicative attack x opponent-defense
        exposure.
        """
        eh = self._num(ctx.get("home_expected"))
        ea = self._num(ctx.get("away_expected"))
        if eh is not None and ea is not None and eh > 0.0 and ea > 0.0:
            return eh, ea

        att_h = self._side_rating(ctx, "home", "attack") or 1.0
        def_h = self._side_rating(ctx, "home", "defense") or 1.0
        att_a = self._side_rating(ctx, "away", "attack") or 1.0
        def_a = self._side_rating(ctx, "away", "defense") or 1.0
        lam_h = (eh if eh is not None and eh > 0.0 else self.home_mean * att_h * def_a)
        lam_a = (ea if ea is not None and ea > 0.0 else self.away_mean * att_a * def_h)
        return lam_h, lam_a

    def _rates(
        self, ctx: Dict[str, Any], beta_rest: Optional[float] = None, beta_pace: Optional[float] = None
    ) -> Tuple[float, float]:
        br = self.beta_rest if beta_rest is None else beta_rest
        bp = self.beta_pace if beta_pace is None else beta_pace
        lam_h, lam_a = self._base_rates(ctx)
        r = self._rest_log_ratio(ctx)
        p = self._pace_log(ctx)
        lam_h *= math.exp(br * r + bp * p)
        lam_a *= math.exp(-br * r + bp * p)
        return lam_h, lam_a

    def _bucket(self, ctx: Dict[str, Any]) -> str:
        stage = str(ctx.get("stage", ctx.get("round", ctx.get("phase", "")))).lower()
        is_ko = bool(ctx.get("is_knockout")) or any(
            tag in stage
            for tag in ("knockout", "playoff", "play-off", "round of", "r16", "quarter", "semi", "final")
        )
        if not is_ko or "league" in stage or "group" in stage:
            return "league_phase"
        if "final" in stage and "semi" not in stage and "quarter" not in stage:
            return "ko_final"
        leg = self._num(ctx.get("leg"))
        if leg is None or int(leg) != 2:
            return "ko_leg1"
        agg = self._num(ctx.get("first_leg_aggregate_diff"))
        if agg is None:
            fh = self._num(ctx.get("first_leg_home_goals"))
            fa = self._num(ctx.get("first_leg_away_goals"))
            agg = (fh - fa) if fh is not None and fa is not None else 0.0
        gap = abs(agg)
        if gap < 0.5:
            return "ko_leg2_level"
        if gap < 1.5:
            return "ko_leg2_close"
        return "ko_leg2_lopsided"

    # ------------------------------------------------------------------ #
    # Probability core
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rho_bounds(lam_h: float, lam_a: float) -> Tuple[float, float]:
        """Analytic region keeping tau(0,0), tau(0,1), tau(1,0), tau(1,1) >= 0."""
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        return lo, hi

    @staticmethod
    def _log_nm(h: np.ndarray, a: np.ndarray, lam_h: float, lam_a: float, k: float) -> np.ndarray:
        if not math.isfinite(k):
            return stats.poisson.logpmf(h, lam_h) + stats.poisson.logpmf(a, lam_a)
        L = lam_h + lam_a
        log_den = math.log(k + L)
        return (
            special.gammaln(k + h + a)
            - special.gammaln(k)
            - special.gammaln(h + 1.0)
            - special.gammaln(a + 1.0)
            + k * (math.log(k) - log_den)
            + h * (math.log(lam_h) - log_den)
            + a * (math.log(lam_a) - log_den)
        )

    def _max_total(self, lam_h: float, lam_a: float, k: float) -> int:
        L = lam_h + lam_a
        q = 1.0 - self.tail_tolerance
        if math.isfinite(k):
            n = stats.nbinom.ppf(q, k, k / (k + L))
        else:
            n = stats.poisson.ppf(q, L)
        return int(max(n, 1)) + 1

    def _score_lattice(
        self, lam_h: float, lam_a: float, k: float, rho: float
    ) -> Dict[Tuple[int, int], float]:
        n_max = self._max_total(lam_h, lam_a, k)
        hh, aa = np.meshgrid(np.arange(n_max + 1), np.arange(n_max + 1), indexing="ij")
        mask = (hh + aa) <= n_max
        h = hh[mask].astype(float)
        a = aa[mask].astype(float)
        p = np.exp(self._log_nm(h, a, lam_h, lam_a, k))

        lo, hi = self._rho_bounds(lam_h, lam_a)
        rho = min(max(rho, lo), hi)  # analytic validity region, not a tuning clamp
        tau = np.ones_like(p)
        tau[(h == 0) & (a == 0)] = 1.0 - lam_h * lam_a * rho
        tau[(h == 1) & (a == 0)] = 1.0 + lam_a * rho
        tau[(h == 0) & (a == 1)] = 1.0 + lam_h * rho
        tau[(h == 1) & (a == 1)] = 1.0 - rho
        p = p * tau
        p /= p.sum()
        return {(int(hi_), int(ai_)): float(pi) for hi_, ai_, pi in zip(h, a, p) if pi > 0.0}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a = self._rates(ctx)
        rho = self.rho.get(self._bucket(ctx), 0.0)
        scores = self._score_lattice(lam_h, lam_a, self.k, rho)
        return DiscreteDistribution.from_scores(scores)

    def total_goals_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        """Exact-total marginal, convenient for pricing and diagnostics."""
        ctx = game_context or {}
        lam_h, lam_a = self._rates(ctx)
        scores = self._score_lattice(lam_h, lam_a, self.k, self.rho.get(self._bucket(ctx), 0.0))
        out: Dict[int, float] = {}
        for (h, a), p in scores.items():
            out[h + a] = out.get(h + a, 0.0) + p
        return dict(sorted(out.items()))

    @staticmethod
    def expected_value(prob: float, decimal_odds: float) -> float:
        """Per-unit EV. Eligibility is continuous (odds >= 1.40 and EV > 0); there is no step filter."""
        return prob * decimal_odds - 1.0

    # ------------------------------------------------------------------ #
    # Estimation (activates once UEL results exist)
    # ------------------------------------------------------------------ #
    def fit(self, records: Sequence[Dict[str, Any]]) -> "UELFullGameExactTotalModel":
        """
        Penalised MLE over (log k, beta_rest, beta_pace, rho_bucket).

        Each record needs a 'context' (game_context dict) plus 'home_goals'
        and 'away_goals'. The rho_bucket values get a Gaussian prior centred
        on the pooled rho, with sd = rho_shrinkage_sd, so sparse knockout
        buckets shrink toward the league-wide value instead of overfitting.
        The UEL mean anchors are re-estimated from the data first.
        """
        rows: List[Tuple[Dict[str, Any], int, int, int]] = []
        for r in records:
            ctx = r.get("context", r)
            hg, ag = self._num(r.get("home_goals")), self._num(r.get("away_goals"))
            if hg is None or ag is None or hg < 0 or ag < 0:
                continue
            rows.append((ctx, int(hg), int(ag), self._BUCKETS.index(self._bucket(ctx))))
        if len(rows) < 2:
            return self

        self.home_mean = float(np.mean([h for _, h, _, _ in rows])) or self.home_mean
        self.away_mean = float(np.mean([a for _, _, a, _ in rows])) or self.away_mean

        H = np.array([h for _, h, _, _ in rows], dtype=float)
        A = np.array([a for _, _, a, _ in rows], dtype=float)
        B = np.array([b for _, _, _, b in rows], dtype=int)
        base = np.array([self._base_rates(c) for c, _, _, _ in rows])
        R = np.array([self._rest_log_ratio(c) for c, _, _, _ in rows])
        P = np.array([self._pace_log(c) for c, _, _, _ in rows])
        nb = len(self._BUCKETS)

        def low_cell_probs(lh: np.ndarray, la: np.ndarray, k: float) -> np.ndarray:
            cells = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], dtype=float)
            return np.stack(
                [np.exp(self._log_nm_vec(c[0], c[1], lh, la, k)) for c in cells], axis=1
            )

        def nll(theta: np.ndarray) -> float:
            log_k, br, bp = theta[0], theta[1], theta[2]
            rho0, delta = theta[3], theta[4:]
            k = math.exp(log_k)
            lh = base[:, 0] * np.exp(br * R + bp * P)
            la = base[:, 1] * np.exp(-br * R + bp * P)
            rho = rho0 + delta[B]
            t00 = 1.0 - lh * la * rho
            t10 = 1.0 + la * rho
            t01 = 1.0 + lh * rho
            t11 = 1.0 - rho
            if np.any(t00 <= 0) or np.any(t10 <= 0) or np.any(t01 <= 0) or np.any(t11 <= 0):
                return 1e300
            ll = self._log_nm_vec(H, A, lh, la, k)
            tau = np.ones_like(H)
            tau = np.where((H == 0) & (A == 0), t00, tau)
            tau = np.where((H == 1) & (A == 0), t10, tau)
            tau = np.where((H == 0) & (A == 1), t01, tau)
            tau = np.where((H == 1) & (A == 1), t11, tau)
            pc = low_cell_probs(lh, la, k)
            Z = 1.0 + ((np.stack([t00, t10, t01, t11], axis=1) - 1.0) * pc).sum(axis=1)
            obj = -(ll + np.log(tau) - np.log(Z)).sum()
            obj += 0.5 * float(np.sum(delta ** 2)) / self.rho_shrinkage_sd ** 2
            return float(obj)

        mean_total = float(np.mean(H + A))
        var_total = float(np.var(H + A, ddof=1))
        k0 = mean_total ** 2 / (var_total - mean_total) if var_total > mean_total else 1e3
        x0 = np.concatenate(
            [[math.log(k0), self.beta_rest, self.beta_pace, 0.0], np.zeros(nb)]
        )
        res = optimize.minimize(nll, x0, method="Nelder-Mead",
                                options={"maxiter": 20000, "xatol": 1e-6, "fatol": 1e-8})
        res = optimize.minimize(nll, res.x, method="Powell",
                                options={"maxiter": 20000, "xtol": 1e-7, "ftol": 1e-10})
        th = res.x
        self.k = float(math.exp(th[0]))
        self.beta_rest, self.beta_pace = float(th[1]), float(th[2])
        self.rho = {b: float(th[3] + th[4 + i]) for i, b in enumerate(self._BUCKETS)}
        self.is_fitted = bool(res.success) or math.isfinite(res.fun)
        return self

    @staticmethod
    def _log_nm_vec(h: Any, a: Any, lh: np.ndarray, la: np.ndarray, k: float) -> np.ndarray:
        h = np.asarray(h, dtype=float)
        a = np.asarray(a, dtype=float)
        L = lh + la
        log_den = np.log(k + L)
        return (
            special.gammaln(k + h + a)
            - special.gammaln(k)
            - special.gammaln(h + 1.0)
            - special.gammaln(a + 1.0)
            + k * (math.log(k) - log_den)
            + h * (np.log(lh) - log_den)
            + a * (np.log(la) - log_den)
        )


CandidateModel = UELFullGameExactTotalModel