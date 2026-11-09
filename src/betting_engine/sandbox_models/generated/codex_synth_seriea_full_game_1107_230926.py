"""
SERIEA | full_game | three_way candidate model.

Structure
---------
Goals follow a bivariate Poisson with a shared shock, plus a Dixon-Coles
low-score correction:

    U_H ~ Pois(lam_H - lam_3),  U_A ~ Pois(lam_A - lam_3),  W ~ Pois(lam_3)
    X = U_H + W,  Y = U_A + W
    P*(x, y) = tau_rho(x, y; lam_H, lam_A) * P_BP(x, y) / Z

Marginals scale log-linearly around the league anchors (1.60 / 1.20):

    log lam_H = log(base_H) + mu + (h - h0) + beta * rest_diff / 2
    log lam_A = log(base_A) + mu - (h - h0) - beta * rest_diff / 2

Here h0 is the home advantage already embedded in the anchors,
h0 = 0.5 * log(anchor_H / anchor_A). The parameter h is shrunk toward a
post-2020 prior, which limits home-win over-confidence.

The shared shock is a fraction of the smaller marginal, so lam_3 < min(lam_H, lam_A):

    lam_3 = min(lam_H, lam_A) * eta^2 / (1 + eta^2),
    eta   = gamma_0 + gamma^T z
    z     = (|log(lam_H / lam_A)|, pace, rest_diff)

Setting gamma = 0 gives exact independence, which is the cold-start prior.

rho is a single global latent. For each match it is mapped smoothly into that
match's Dixon-Coles feasibility interval, so every tau(x, y) >= 0 and
rho_raw = 0 gives rho = 0. No static clamps are used.

Every parameter has a Gaussian prior. All except h are centred at zero
(independence / no correction). The parameters are refit by MAP (empirical
Bayes) as settled results arrive. 1/X/2 are then read off the joint pmf.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # DiscreteDistribution may live elsewhere or be pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover - rely on execution-scope primitive
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


_PARAM_NAMES = ("mu", "h", "beta", "rho_raw", "g0", "g_parity", "g_pace", "g_rest")


class SerieABivariatePoissonDCModel(BaseCandidateModel):
    """Bivariate-Poisson + Dixon-Coles three-way model with empirical-Bayes updating."""

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_full_game_1107_230926",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "full_game",
        family: str = "three_way",
        anchor_home: float = 1.60,
        anchor_away: float = 1.20,
        hfa_prior_mean: Optional[float] = None,
        prior_sd: Optional[Dict[str, float]] = None,
        tail_tolerance: float = 1e-12,
        min_refit_matches: int = 1,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)  # type: ignore[misc]

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # League anchors are empirical moments, not tuning knobs.
        self.anchor_home = float(anchor_home)
        self.anchor_away = float(anchor_away)
        self.h0 = 0.5 * math.log(self.anchor_home / self.anchor_away)

        # The hierarchical HFA prior defaults to the anchor-implied value.
        # A post-2020 posterior can be injected via hfa_prior_mean.
        h_mean = self.h0 if hfa_prior_mean is None else float(hfa_prior_mean)

        # Prior means: independence (gamma=0, rho=0), no calibration drift (mu=0),
        # no rest effect (beta=0), and h at its hierarchical mean.
        self.prior_mean = np.array([0.0, h_mean, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        # Prior scales are on the natural (log / logit) scale of each parameter.
        # Each one bounds the size of effect that is plausible for its parameter.
        default_sd = {
            "mu": 0.10,        # ~10% calibration drift in league scoring
            "h": 0.08,         # HFA uncertainty on half-log-ratio scale
            "beta": 0.10,      # rest effect per log-day differential
            "rho_raw": 1.00,   # weakly informative on the logit-mapped rho
            "g0": 1.00,
            "g_parity": 1.00,
            "g_pace": 1.00,
            "g_rest": 1.00,
        }
        if prior_sd:
            default_sd.update({k: float(v) for k, v in prior_sd.items() if k in default_sd})
        self.prior_sd = np.array([default_sd[n] for n in _PARAM_NAMES])

        self.tail_tolerance = float(tail_tolerance)
        self.min_refit_matches = int(min_refit_matches)

        self.theta = self.prior_mean.copy()
        self._history: List[Tuple[Dict[str, Any], int, int]] = []

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _strength(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Multiplicative attack/defense rating (1.0 = league average) if supplied."""
        block = ctx.get(side)
        if isinstance(block, dict):
            for key in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = self._num(block.get(key))
                if v is not None and v > 0:
                    return v
        v = self._num(ctx.get(f"{side}_{kind}"))
        return v if v is not None and v > 0 else None

    def _base_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Start from the context's expected goals.

        If those are missing, build rates from the league anchors and the
        attack/defense exposure.
        """
        eh = self._num(ctx.get("home_expected"))
        ea = self._num(ctx.get("away_expected"))
        if eh is not None and eh > 0 and ea is not None and ea > 0:
            return eh, ea

        att_h = self._strength(ctx, "home", "attack") or 1.0
        def_h = self._strength(ctx, "home", "defense") or 1.0
        att_a = self._strength(ctx, "away", "attack") or 1.0
        def_a = self._strength(ctx, "away", "defense") or 1.0
        bh = eh if (eh is not None and eh > 0) else self.anchor_home * att_h * def_a
        ba = ea if (ea is not None and ea > 0) else self.anchor_away * att_a * def_h
        return bh, ba

    def _rest_diff(self, ctx: Dict[str, Any]) -> float:
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None or rh < 0 or ra < 0:
            return 0.0
        # Fatigue recovery has diminishing returns, so use log1p.
        return math.log1p(rh) - math.log1p(ra)

    def _pace(self, ctx: Dict[str, Any], lam_h: float, lam_a: float) -> float:
        """Log tempo relative to the league anchor total.

        An explicit pace/tempo index is used if one is supplied.
        """
        for key in ("pace", "tempo", "combined_pace"):
            v = self._num(ctx.get(key))
            if v is not None and v > 0:
                return math.log(v)
        return math.log((lam_h + lam_a) / (self.anchor_home + self.anchor_away))

    # ------------------------------------------------------------------ #
    # Core structural mapping theta -> (lam_H, lam_A, lam_3, rho)
    # ------------------------------------------------------------------ #
    def _structural(
        self, ctx: Dict[str, Any], theta: np.ndarray
    ) -> Tuple[float, float, float, float]:
        mu, h, beta, rho_raw, g0, g_par, g_pace, g_rest = theta
        base_h, base_a = self._base_rates(ctx)
        rd = self._rest_diff(ctx)

        lam_h = base_h * math.exp(mu + (h - self.h0) + 0.5 * beta * rd)
        lam_a = base_a * math.exp(mu - (h - self.h0) - 0.5 * beta * rd)

        # Shared shock: a fraction of the smaller marginal; gamma = 0 means independence.
        parity = abs(math.log(lam_h / lam_a))
        pace = self._pace(ctx, lam_h, lam_a)
        eta = g0 + g_par * parity + g_pace * pace + g_rest * rd
        frac = eta * eta / (1.0 + eta * eta)
        lam_3 = min(lam_h, lam_a) * frac

        # Map the global rho latent into this match's feasible Dixon-Coles interval.
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        offset = special.logit(-lo / (hi - lo))  # makes rho_raw = 0 give rho = 0
        rho = lo + (hi - lo) * special.expit(rho_raw + offset)
        return lam_h, lam_a, lam_3, rho

    def _lattice_size(self, lam_h: float, lam_a: float) -> int:
        """Choose the score lattice size so the truncated tail mass is below tolerance."""
        tail = self.tail_tolerance
        n = max(stats.poisson.isf(tail, lam_h), stats.poisson.isf(tail, lam_a))
        return int(n) + 2

    def _joint_pmf(
        self, lam_h: float, lam_a: float, lam_3: float, rho: float, n: Optional[int] = None
    ) -> np.ndarray:
        n = n if n is not None else self._lattice_size(lam_h, lam_a)
        g = np.arange(n)
        p_uh = stats.poisson.pmf(g, lam_h - lam_3)
        p_ua = stats.poisson.pmf(g, lam_a - lam_3)
        p_w = stats.poisson.pmf(g, lam_3) if lam_3 > 0 else np.eye(1, n, 0).ravel()

        joint = np.zeros((n, n))
        for k in range(n):
            if p_w[k] == 0.0:
                continue
            m = n - k
            joint[k:, k:] += p_w[k] * np.outer(p_uh[:m], p_ua[:m])

        # Dixon-Coles low-score correction on {0,1}^2. Feasible by construction of rho.
        joint[0, 0] *= 1.0 - lam_h * lam_a * rho
        joint[0, 1] *= 1.0 + lam_h * rho
        joint[1, 0] *= 1.0 + lam_a * rho
        joint[1, 1] *= 1.0 - rho

        total = joint.sum()
        return joint / total

    # ------------------------------------------------------------------ #
    # Empirical Bayes / MAP updating
    # ------------------------------------------------------------------ #
    def _neg_log_posterior(self, theta: np.ndarray, data: Sequence[Tuple[Dict[str, Any], int, int]]) -> float:
        nll = 0.0
        for ctx, hg, ag in data:
            lam_h, lam_a, lam_3, rho = self._structural(ctx, theta)
            n = max(self._lattice_size(lam_h, lam_a), hg + 1, ag + 1)
            p = self._joint_pmf(lam_h, lam_a, lam_3, rho, n)[hg, ag]
            nll -= math.log(p) if p > 0 else -np.log(np.finfo(float).tiny)
        z = (theta - self.prior_mean) / self.prior_sd
        return nll + 0.5 * float(z @ z)

    def fit(self, history: Sequence[Tuple[Dict[str, Any], int, int]]) -> Dict[str, float]:
        """MAP-fit all structural parameters from (context, home_goals, away_goals) rows."""
        data = [(c, int(h), int(a)) for c, h, a in history]
        if len(data) < self.min_refit_matches:
            return self.params
        # The shared-shock map is even in eta, so start slightly off zero to
        # avoid the symmetric stationary point.
        x0 = self.theta.copy()
        if x0[4] == 0.0:
            x0[4] = self.prior_sd[4] * np.finfo(float).eps ** 0.25
        res = optimize.minimize(
            self._neg_log_posterior, x0, args=(data,), method="L-BFGS-B"
        )
        if res.success or res.fun < self._neg_log_posterior(self.theta, data):
            self.theta = np.asarray(res.x, dtype=float)
        return self.params

    def observe_result(self, game_context: Dict[str, Any], home_goals: int, away_goals: int) -> Dict[str, float]:
        """Append a settled match and refit, so the model updates against the observed draw frequency."""
        self._history.append((dict(game_context), int(home_goals), int(away_goals)))
        return self.fit(self._history)

    @property
    def params(self) -> Dict[str, float]:
        return {n: float(v) for n, v in zip(_PARAM_NAMES, self.theta)}

    # ------------------------------------------------------------------ #
    # Prediction
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        lam_h, lam_a, lam_3, rho = self._structural(game_context, self.theta)
        return self._joint_pmf(lam_h, lam_a, lam_3, rho)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.score_matrix(game_context)
        p_home = float(np.tril(joint, -1).sum())  # X > Y
        p_draw = float(np.trace(joint))           # X = Y
        p_away = float(np.triu(joint, 1).sum())   # X < Y
        total = p_home + p_draw + p_away
        return DiscreteDistribution(
            probabilities={"home": p_home / total, "draw": p_draw / total, "away": p_away / total}
        )

    def expected_value(self, game_context: Dict[str, Any], decimal_odds: Dict[str, float]) -> Dict[str, float]:
        """Continuous EV per outcome. Eligibility is odds >= 1.40 with EV > 0, and no other filters."""
        dist = self.predict_distribution(game_context)
        probs = getattr(dist, "probabilities", None) or {}
        out: Dict[str, float] = {}
        for side, odds in decimal_odds.items():
            o = self._num(odds)
            if o is None or side not in probs:
                continue
            out[side] = probs[side] * o - 1.0
        return out


CandidateModel = SerieABivariatePoissonDCModel