"""
Candidate model: NCAA_ICE_HOCKEY_WOMEN | full_game | double_chance

Structure
---------
Regulation (60:00, excluding 3v3 OT and shootout) is modelled as a
time-inhomogeneous, score-state-dependent competing-hazard process:

    lambda_h(t, s) = mu_h * g_h(s, t) + EN_h(s, t)
    lambda_a(t, s) = mu_a * g_a(s, t) + EN_a(s, t)

where s = home_goals - away_goals.

1. Base rates (mu_h, mu_a) come from the 'home_expected' and 'away_expected'
   anchors. They are scaled by attack/defense exposure, pace and rest, then
   pass through a home-advantage shrinkage that preserves the total.
2. g(s, t) is the score-effect multiplier. The trailing team's rate rises as
   the game progresses and the leader's rate falls (territorial tilt).
3. The empty-net regime applies when the trailing team is down 1 or 2 and
   t > T - tau_k. It boosts the trailing team's 6v5 rate and adds an
   empty-net hazard for the leader.
4. The joint score distribution is propagated with a Kolmogorov forward
   recursion on a sub-minute grid. Base rates are then rescaled by a
   fixed-point step so that expected regulation goals equal the anchored
   means (score effects redistribute goals; they do not create them).
5. A Dixon-Coles low-score correction rho(gap) = rho0 * exp(-rho_gap * gap),
   with gap = |log(mu_h / mu_a)|, adds low-score ties in balanced games and
   fades out in mismatches. rho is bounded only by the mathematical validity
   region of the correction factors.
6. The parameters (score effects, tau, empty-net multipliers, rho, shrinkage,
   rest elasticity, exposure weight) are fitted by penalised maximum
   likelihood in `fit()`. The fit uses power-de-vigged closing 1X2 prices
   and/or observed regulation results. Domain-informed Gaussian priors act
   as regularisation; they are starting beliefs, not clamps.

Double-chance legs: P(1X) = 1 - P(A), P(X2) = 1 - P(H), P(12) = 1 - P(D).
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # DiscreteDistribution may be pre-loaded in the execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


# --------------------------------------------------------------------------- #
# Domain anchors: empirical segment moments and structural facts of the sport.
# --------------------------------------------------------------------------- #
_REGULATION_MINUTES = 60.0          # three 20-minute periods (structural)
_ANCHOR_HOME_EXPECTED = 1.60        # empirical segment mean
_ANCHOR_AWAY_EXPECTED = 1.20        # empirical segment mean
_STEPS_PER_MINUTE = 2               # 30-second grid; P(>1 goal per step) is negligible
_MEAN_MATCH_ITERS = 3               # fixed-point iterations for mean preservation

# Parameter specification: name -> (transform, prior mean in natural space, prior sd in z-space)
# Transforms map an unconstrained z onto the natural domain without static clamps.
_PARAM_SPEC: Dict[str, Tuple[str, float, float]] = {
    "kappa_trail":     ("log",      0.25, 0.75),   # trailing-team territorial lift at t = T
    "kappa_lead":      ("log",      0.15, 0.75),   # leader shell-in suppression at t = T
    "tau1":            ("log",      2.00, 0.50),   # minutes remaining when the goalie is pulled, down 1
    "tau2_ratio":      ("log",      0.75, 0.75),   # tau2 = tau1 * (1 + ratio), down 2
    "en_boost":        ("log",      2.50, 0.50),   # 6v5 scoring multiplier for the trailing team
    "en_hazard":       ("log",      0.20, 0.50),   # empty-net goals per minute for the leader
    "rho0":            ("identity", -0.10, 0.10),  # Dixon-Coles correlation at zero strength gap
    "rho_gap":         ("log",      1.00, 0.75),   # decay of rho with |log mu_h / mu_a|
    "home_shrink":     ("logit",    0.30, 1.00),   # fraction of baseline home edge removed
    "rest_elast":      ("identity", 0.04, 0.05),   # elasticity on log(1 + rest days) differential
    "exposure_weight": ("logit",    0.50, 1.00),   # partial pass-through of attack/defense ratings
}


def _to_natural(transform: str, z: float) -> float:
    if transform == "log":
        return math.exp(z)
    if transform == "logit":
        return 1.0 / (1.0 + math.exp(-z))
    return z


def _to_unconstrained(transform: str, x: float) -> float:
    if transform == "log":
        return math.log(x)
    if transform == "logit":
        return math.log(x / (1.0 - x))
    return x


def _finite_positive(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if (math.isfinite(v) and v > 0.0) else None


def _finite(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class NcaaWomensHockeyDoubleChanceModel(BaseCandidateModel):
    """Score-state-dependent regulation process with empty-net regime and Dixon-Coles closure."""

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_ice_hockey_women_full_game_1108_130426",
        version: str = "1.0.0",
        league: str = "NCAA_ICE_HOCKEY_WOMEN",
        scope: str = "full_game",
        family: str = "double_chance",
        params: Optional[Dict[str, float]] = None,
    ) -> None:
        super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        self.params: Dict[str, float] = {k: spec[1] for k, spec in _PARAM_SPEC.items()}
        if params:
            for k, v in params.items():
                if k in self.params and _finite(v) is not None:
                    self.params[k] = float(v)
        self.home_expected_anchor = _ANCHOR_HOME_EXPECTED
        self.away_expected_anchor = _ANCHOR_AWAY_EXPECTED
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _team_rating(ctx: Dict[str, Any], side: str, kind: str) -> float:
        """
        Multiplicative rating centred at 1.0.
        Positive values are read as ratios; non-positive values are read as log-ratings.
        attack > 1: stronger offence. defense > 1: leakier defence (goals-allowed index).
        """
        raw = None
        block = ctx.get(side)
        if isinstance(block, dict):
            raw = block.get(kind)
        if raw is None:
            raw = ctx.get(f"{side}_{kind}")
        v = _finite(raw)
        if v is None:
            return 1.0
        return v if v > 0.0 else math.exp(v)

    @staticmethod
    def _pace_factor(ctx: Dict[str, Any]) -> float:
        for key in ("pace_factor", "tempo_factor"):
            v = _finite_positive(ctx.get(key))
            if v is not None:
                return v
        pace = _finite_positive(ctx.get("pace", ctx.get("tempo")))
        league_pace = _finite_positive(ctx.get("league_pace", ctx.get("league_tempo")))
        if pace is not None and league_pace is not None:
            return pace / league_pace
        hp = _finite_positive(ctx.get("home_pace"))
        ap = _finite_positive(ctx.get("away_pace"))
        if hp is not None and ap is not None and league_pace is not None:
            return math.sqrt(hp * ap) / league_pace
        return 1.0

    def _base_rates(self, ctx: Dict[str, Any], p: Dict[str, float]) -> Tuple[float, float]:
        mu_h = _finite_positive(ctx.get("home_expected")) or self.home_expected_anchor
        mu_a = _finite_positive(ctx.get("away_expected")) or self.away_expected_anchor

        # Attack/defense exposure (partial pass-through, since expectations may already embed it)
        w = p["exposure_weight"]
        exp_h = self._team_rating(ctx, "home", "attack") * self._team_rating(ctx, "away", "defense")
        exp_a = self._team_rating(ctx, "away", "attack") * self._team_rating(ctx, "home", "defense")
        mu_h *= exp_h ** w
        mu_a *= exp_a ** w

        # Pace scales both teams (total), leaving the split unchanged
        pace = self._pace_factor(ctx)
        mu_h *= pace
        mu_a *= pace

        # Rest: concave fatigue response, applied symmetrically to the log-ratio
        rh = _finite(ctx.get("home_rest_days"))
        ra = _finite(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            d = p["rest_elast"] * (math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0)))
            mu_h *= math.exp(0.5 * d)
            mu_a *= math.exp(-0.5 * d)

        # Home-advantage shrinkage: remove a fraction of the baseline home log-edge,
        # keeping the total fixed so only the split moves.
        baseline_edge = math.log(self.home_expected_anchor / self.away_expected_anchor)
        total = mu_h + mu_a
        r = math.log(mu_h / mu_a) - p["home_shrink"] * baseline_edge
        share_h = 1.0 / (1.0 + math.exp(-r))
        return total * share_h, total * (1.0 - share_h)

    # ------------------------------------------------------------------ #
    # Forward recursion over score states
    # ------------------------------------------------------------------ #
    @staticmethod
    def _grid_size(total: float) -> int:
        # Captures far-tail mass for the Poisson-like total (mean + 8 sd), plus empty-net headroom
        return int(math.ceil(total + 8.0 * math.sqrt(max(total, 1e-9)))) + 4

    def _propagate(self, mu_h: float, mu_a: float, p: Dict[str, float], n: int) -> np.ndarray:
        T = _REGULATION_MINUTES
        steps = int(T * _STEPS_PER_MINUTE)
        dt = 1.0 / _STEPS_PER_MINUTE
        idx = np.arange(n + 1)
        diff = idx[:, None] - idx[None, :]           # home - away
        absd = np.abs(diff)
        home_trails = diff < 0
        away_trails = diff > 0
        sat = 1.0 - np.exp(-absd.astype(float))      # saturating score-gap intensity, 0 when tied

        tau1 = p["tau1"]
        tau2 = tau1 * (1.0 + p["tau2_ratio"])
        base_h = mu_h / T
        base_a = mu_a / T

        P = np.zeros((n + 1, n + 1))
        P[0, 0] = 1.0
        for k in range(steps):
            t = (k + 0.5) * dt
            remaining = T - t
            phase = t / T

            up_trail = np.exp(p["kappa_trail"] * phase * sat)
            down_lead = np.exp(-p["kappa_lead"] * phase * sat)
            g_h = np.where(home_trails, up_trail, np.where(away_trails, down_lead, 1.0))
            g_a = np.where(away_trails, up_trail, np.where(home_trails, down_lead, 1.0))

            en_active = ((absd == 1) & (remaining < tau1)) | ((absd == 2) & (remaining < tau2))
            home_pulls = en_active & home_trails
            away_pulls = en_active & away_trails

            lam_h = base_h * g_h * np.where(home_pulls, p["en_boost"], 1.0) + np.where(away_pulls, p["en_hazard"], 0.0)
            lam_a = base_a * g_a * np.where(away_pulls, p["en_boost"], 1.0) + np.where(home_pulls, p["en_hazard"], 0.0)

            tot = lam_h + lam_a
            p_any = -np.expm1(-tot * dt)
            ph = p_any * lam_h / tot
            pa = p_any * lam_a / tot

            flow_h = P * ph
            flow_a = P * pa
            newP = P * (1.0 - p_any)
            newP[1:, :] += flow_h[:-1, :]
            newP[:, 1:] += flow_a[:, :-1]
            newP[-1, :] += flow_h[-1, :]             # absorb at grid edge (negligible mass)
            newP[:, -1] += flow_a[:, -1]
            P = newP
        return P / P.sum()

    def _score_matrix(self, mu_h: float, mu_a: float, p: Dict[str, float]) -> np.ndarray:
        n = self._grid_size(mu_h + mu_a)
        idx = np.arange(n + 1)
        sh, sa = mu_h, mu_a
        P = None
        # Fixed-point: rescale base rates so regulation means equal the anchored targets
        for _ in range(_MEAN_MATCH_ITERS):
            P = self._propagate(sh, sa, p, n)
            eh = float((P.sum(axis=1) * idx).sum())
            ea = float((P.sum(axis=0) * idx).sum())
            sh *= mu_h / eh
            sa *= mu_a / ea
        P = self._propagate(sh, sa, p, n)

        # Dixon-Coles low-score closure, decaying with strength gap
        gap = abs(math.log(mu_h / mu_a))
        rho = p["rho0"] * math.exp(-p["rho_gap"] * gap)
        lo = max(-1.0 / mu_h, -1.0 / mu_a)
        hi = min(1.0 / (mu_h * mu_a), 1.0)
        rho = min(max(rho, lo), hi)                  # mathematical validity region, not a tuning clamp
        P = P.copy()
        P[0, 0] *= 1.0 - mu_h * mu_a * rho
        P[0, 1] *= 1.0 + mu_h * rho
        P[1, 0] *= 1.0 + mu_a * rho
        P[1, 1] *= 1.0 - rho
        P = np.clip(P, 0.0, None)
        self.last_diagnostics = {"rho": rho, "mu_h": mu_h, "mu_a": mu_a, "grid": n}
        return P / P.sum()

    @staticmethod
    def _outcomes(P: np.ndarray) -> Tuple[float, float, float]:
        p_h = float(np.tril(P, -1).sum())
        p_d = float(np.trace(P))
        p_a = float(np.triu(P, 1).sum())
        s = p_h + p_d + p_a
        return p_h / s, p_d / s, p_a / s

    def _three_way(self, ctx: Dict[str, Any], p: Dict[str, float]) -> Tuple[float, float, float]:
        mu_h, mu_a = self._base_rates(ctx, p)
        return self._outcomes(self._score_matrix(mu_h, mu_a, p))

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        p_h, p_d, p_a = self._three_way(game_context or {}, self.params)
        self.last_diagnostics.update({
            "p_home": p_h, "p_draw": p_d, "p_away": p_a,
            "p_1X": 1.0 - p_a, "p_X2": 1.0 - p_h, "p_12": 1.0 - p_d,
        })
        if hasattr(DiscreteDistribution, "from_probabilities"):
            return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)
        return DiscreteDistribution(probabilities={"home": p_h, "draw": p_d, "away": p_a})

    def double_chance_probabilities(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        p_h, p_d, p_a = self._three_way(game_context or {}, self.params)
        return {"1X": 1.0 - p_a, "X2": 1.0 - p_h, "12": 1.0 - p_d}

    # ------------------------------------------------------------------ #
    # Calibration: penalised MLE on de-vigged closing prices and regulation results
    # ------------------------------------------------------------------ #
    @staticmethod
    def _power_devig(odds: Sequence[float]) -> Optional[np.ndarray]:
        o = np.asarray([_finite_positive(x) for x in odds], dtype=object)
        if any(v is None or v <= 1.0 for v in o):
            return None
        implied = np.array([1.0 / float(v) for v in o])
        if implied.sum() <= 1.0:
            return implied / implied.sum()
        f = lambda k: float(np.sum(implied ** k) - 1.0)
        try:
            k = optimize.brentq(f, 1.0, 50.0)
        except ValueError:
            return implied / implied.sum()
        q = implied ** k
        return q / q.sum()

    @staticmethod
    def _parse_target(rec: Dict[str, Any]) -> Tuple[Optional[np.ndarray], Optional[int]]:
        q = None
        probs = rec.get("closing_probs")
        if isinstance(probs, dict):
            arr = [_finite_positive(probs.get(k)) for k in ("home", "draw", "away")]
            if all(v is not None for v in arr):
                q = np.array(arr) / sum(arr)
        if q is None:
            odds = rec.get("closing_odds")
            if isinstance(odds, dict):
                q = NcaaWomensHockeyDoubleChanceModel._power_devig(
                    [odds.get("home"), odds.get("draw"), odds.get("away")]
                )
        res = rec.get("result", rec.get("regulation_result"))
        mapping = {"home": 0, "h": 0, "1": 0, "draw": 1, "d": 1, "x": 1, "tie": 1, "away": 2, "a": 2, "2": 2}
        y = mapping.get(str(res).strip().lower()) if res is not None else None
        return q, y

    def fit(
        self,
        records: List[Dict[str, Any]],
        price_weight: float = 1.0,
        result_weight: float = 1.0,
        max_iter: int = 200,
    ) -> Dict[str, Any]:
        """
        records: [{'game_context': {...},
                   'closing_odds': {'home','draw','away'} or 'closing_probs': {...},
                   'result': 'home'|'draw'|'away'  (regulation)}]
        Minimises the cross-entropy to de-vigged closing prices plus the negative
        log-likelihood of regulation results, with Gaussian priors in z-space.
        """
        data = []
        for rec in records or []:
            ctx = rec.get("game_context")
            if not isinstance(ctx, dict):
                continue
            q, y = self._parse_target(rec)
            if q is not None or y is not None:
                data.append((ctx, q, y))
        if not data:
            return {"success": False, "reason": "no usable records", "params": dict(self.params)}

        names = list(_PARAM_SPEC.keys())
        z_prior = np.array([_to_unconstrained(_PARAM_SPEC[k][0], _PARAM_SPEC[k][1]) for k in names])
        z_sd = np.array([_PARAM_SPEC[k][2] for k in names])
        z0 = np.array([_to_unconstrained(_PARAM_SPEC[k][0], self.params[k]) for k in names])

        def unpack(z: np.ndarray) -> Dict[str, float]:
            return {k: _to_natural(_PARAM_SPEC[k][0], float(z[i])) for i, k in enumerate(names)}

        def objective(z: np.ndarray) -> float:
            p = unpack(z)
            loss = 0.0
            for ctx, q, y in data:
                probs = np.maximum(np.array(self._three_way(ctx, p)), 1e-12)
                if q is not None:
                    loss -= price_weight * float(np.dot(q, np.log(probs)))
                if y is not None:
                    loss -= result_weight * float(math.log(probs[y]))
            loss += 0.5 * float(np.sum(((z - z_prior) / z_sd) ** 2))
            return loss if math.isfinite(loss) else 1e12

        res = optimize.minimize(objective, z0, method="L-BFGS-B", options={"maxiter": max_iter})
        if res.success or res.fun < objective(z0):
            self.params = unpack(res.x)
        return {
            "success": bool(res.success),
            "objective": float(res.fun),
            "n_records": len(data),
            "params": dict(self.params),
        }