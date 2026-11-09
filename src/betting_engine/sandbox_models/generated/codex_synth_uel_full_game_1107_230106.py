"""
Candidate model for UEL | full_game | totals.

Design summary
--------------
1. Pre-match rates: lambda_i = mu_i * strength ratios * exp(beta_r * drest_i + beta_p * log pace ratio).
   mu_i comes from the context ('home_expected' / 'away_expected') or the empirical
   segment means (1.60 / 1.20).
2. In-match state dependence (knockout ties): intensity lambda_i(t) = lambda_i * exp(gamma * s_i(t) * t / T).
   s_i = +1 if side i trails on aggregate, -1 if it leads, 0 if level. This is integrated
   exactly as a continuous-time Markov chain on the score lattice. Regulation time only;
   extra time is excluded from "full_game".
3. Rotation uncertainty: each side's rate gets an independent mean-1 gamma multiplier.
   Its variance comes from the observed spread of pre-match rate estimates in the context.
   With no spread supplied, the dispersion index is exactly 1.0 (Poisson). It grows only
   as rotation uncertainty grows.
4. Dixon-Coles tau(rho) correction on the low-score cells. rho is bounded only by the
   feasibility region that keeps tau >= 0, not by a static clamp.

Cold start
----------
There are zero settled wagers. All structural coefficients (beta_r, beta_p, gamma, rho)
therefore default to their neutral values, so the model reduces exactly to independent
Poisson until `fit()` estimates them by penalised maximum likelihood on historical
UEL match results (not betting results). The ridge penalty shrinks toward neutral to
protect out-of-sample validity.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # pre-loaded in the execution scope; fall back to explicit imports if not
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


_PARAM_NAMES = ("beta_rest", "beta_pace", "gamma_ko", "gamma_state", "rho")


class UELFullGameTotalsModel(BaseCandidateModel):
    """State-dependent, rotation-mixed, Dixon-Coles corrected score model for UEL totals."""

    # Empirical segment priors (the only data-derived anchors available at cold start).
    PRIOR_HOME_MEAN = 1.60
    PRIOR_AWAY_MEAN = 1.20

    def __init__(
        self,
        model_id: str = "codex_synth_uel_full_game_1107_230106",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "full_game",
        family: str = "totals",
        params: Optional[Dict[str, float]] = None,
        tail_mass: float = 1e-7,
        n_quadrature: int = 12,
        step_tolerance: float = 1e-3,
        ridge_precision: float = 1.0,
        min_odds: float = 1.40,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        # Neutral defaults: the model is exactly independent Poisson until fitted.
        self.params: Dict[str, float] = {name: 0.0 for name in _PARAM_NAMES}
        if params:
            self.params.update({k: float(v) for k, v in params.items() if k in self.params})

        # Numerical-accuracy controls (they set precision, they do not shape probabilities).
        self.tail_mass = float(tail_mass)
        self.n_quadrature = int(n_quadrature)
        self.step_tolerance = float(step_tolerance)
        self.ridge_precision = float(ridge_precision)
        self.min_odds = float(min_odds)
        self.regulation_minutes = 90.0

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str, default: Optional[float] = None) -> Optional[float]:
        for key in keys:
            node: Any = ctx
            for part in key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    node = None
                    break
            if node is None:
                continue
            try:
                val = float(node)
            except (TypeError, ValueError):
                continue
            if math.isfinite(val):
                return val
        return default

    def _strength_ratios(self, ctx: Dict[str, Any], priced_in: bool) -> Tuple[float, float]:
        """
        Self-normalised attack/defence multipliers.

        If the context already supplies market-independent expected goals, the strengths
        are assumed priced in (exponent 0) to avoid double counting.
        """
        if priced_in:
            return 1.0, 1.0
        ah = self._num(ctx, "home.attack", "home_attack")
        aa = self._num(ctx, "away.attack", "away_attack")
        dh = self._num(ctx, "home.defense", "home_defense", "home.defence")
        da = self._num(ctx, "away.defense", "away_defense", "away.defence")
        mult_h, mult_a = 1.0, 1.0
        if ah and aa and ah > 0 and aa > 0:
            ref = math.sqrt(ah * aa)
            mult_h *= ah / ref
            mult_a *= aa / ref
        if dh and da and dh > 0 and da > 0:
            # Defence is read as goals-conceded propensity: a higher value means leakier.
            ref = math.sqrt(dh * da)
            mult_h *= da / ref
            mult_a *= dh / ref
        return mult_h, mult_a

    def _base_rates(self, ctx: Dict[str, Any], p: Dict[str, float]) -> Tuple[float, float]:
        mu_h = self._num(ctx, "home_expected")
        mu_a = self._num(ctx, "away_expected")
        priced_in = mu_h is not None and mu_a is not None and mu_h > 0 and mu_a > 0
        if not priced_in:
            mu_h, mu_a = self.PRIOR_HOME_MEAN, self.PRIOR_AWAY_MEAN

        s_h, s_a = self._strength_ratios(ctx, priced_in)

        rh = self._num(ctx, "home_rest_days")
        ra = self._num(ctx, "away_rest_days")
        d_rest = (rh - ra) if (rh is not None and ra is not None) else 0.0

        pace = self._num(ctx, "pace", "tempo", "match_pace")
        pace_ref = self._num(ctx, "league_pace", "pace_reference")
        log_pace = math.log(pace / pace_ref) if (pace and pace_ref and pace > 0 and pace_ref > 0) else 0.0

        lam_h = mu_h * s_h * math.exp(p["beta_rest"] * d_rest + p["beta_pace"] * log_pace)
        lam_a = mu_a * s_a * math.exp(-p["beta_rest"] * d_rest + p["beta_pace"] * log_pace)
        return lam_h, lam_a

    def _rate_variance(self, ctx: Dict[str, Any], side: str, lam: float) -> float:
        """Rotation and lineup uncertainty taken from the observed spread of pre-match rate estimates."""
        sd = self._num(ctx, f"{side}_expected_sd", f"{side}.expected_sd")
        if sd is not None and sd > 0:
            return sd * sd
        ests = ctx.get(f"{side}_rate_estimates") or (ctx.get(side) or {}).get("rate_estimates") \
            if isinstance(ctx.get(side), dict) or ctx.get(f"{side}_rate_estimates") else None
        if isinstance(ests, (list, tuple)) and len(ests) >= 2:
            arr = np.asarray([float(x) for x in ests if x is not None], dtype=float)
            arr = arr[np.isfinite(arr) & (arr > 0)]
            if arr.size >= 2:
                return float(np.var(arr, ddof=1))
        return 0.0

    def _state_context(self, ctx: Dict[str, Any], p: Dict[str, float]) -> Tuple[Optional[int], float]:
        """
        Return (aggregate goal difference entering the match from home's view, gamma).

        For the second leg of a tie the first-leg result defines the starting aggregate.
        Otherwise gamma_state (single-match game state) applies with a level start.
        """
        fl_h = self._num(ctx, "first_leg_home_team_goals", "aggregate_home_goals")
        fl_a = self._num(ctx, "first_leg_away_team_goals", "aggregate_away_goals")
        if fl_h is not None and fl_a is not None:
            return int(round(fl_h - fl_a)), p["gamma_ko"]
        if p["gamma_state"] != 0.0:
            return 0, p["gamma_state"]
        return None, 0.0

    # ------------------------------------------------------------------ #
    # Score-lattice engines
    # ------------------------------------------------------------------ #
    def _grid_size(self, lam_h: float, lam_a: float, var_h: float, var_a: float, gamma: float) -> int:
        z = stats.norm.isf(self.tail_mass)
        boost = math.exp(abs(gamma))
        upper = max(lam_h + z * math.sqrt(var_h), lam_a + z * math.sqrt(var_a)) * boost
        return int(stats.poisson.isf(self.tail_mass, max(upper, 1e-9))) + 1

    @staticmethod
    def _poisson_matrix(lam_h: float, lam_a: float, g: int) -> np.ndarray:
        k = np.arange(g + 1)
        return np.outer(stats.poisson.pmf(k, lam_h), stats.poisson.pmf(k, lam_a))

    def _state_matrix(self, lam_h: float, lam_a: float, agg0: int, gamma: float, g: int) -> np.ndarray:
        """Integrate the state-dependent intensity CTMC over regulation time on a (g+1)^2 lattice."""
        h_idx, a_idx = np.meshgrid(np.arange(g + 1), np.arange(g + 1), indexing="ij")
        agg = agg0 + h_idx - a_idx
        s_h = np.where(agg < 0, 1.0, np.where(agg > 0, -1.0, 0.0))
        s_a = -s_h

        peak = (lam_h + lam_a) * math.exp(abs(gamma))
        n_steps = max(1, int(math.ceil(peak / self.step_tolerance)))
        dt = 1.0 / n_steps  # time measured in fractions of regulation

        P = np.zeros((g + 1, g + 1))
        P[0, 0] = 1.0
        for step in range(n_steps):
            frac = (step + 0.5) * dt
            rh = lam_h * np.exp(gamma * s_h * frac)
            ra = lam_a * np.exp(gamma * s_a * frac)
            r = rh + ra
            p_stay = np.exp(-r * dt)
            p_go = 1.0 - p_stay
            with np.errstate(invalid="ignore", divide="ignore"):
                ph = np.where(r > 0, p_go * rh / r, 0.0)
                pa = np.where(r > 0, p_go * ra / r, 0.0)
            flow_h, flow_a = P * ph, P * pa
            new = P * p_stay
            new[1:, :] += flow_h[:-1, :]
            new[:, 1:] += flow_a[:, :-1]
            new[-1, :] += flow_h[-1, :]  # tail mass beyond the lattice (< tail_mass)
            new[:, -1] += flow_a[:, -1]
            P = new
        return P

    def _mixing_nodes(self, lam: float, var: float) -> Tuple[np.ndarray, np.ndarray]:
        """Gauss-Laguerre nodes for a mean-1 gamma multiplier with variance var/lam^2."""
        if var <= 0 or lam <= 0:
            return np.array([1.0]), np.array([1.0])
        shape = lam * lam / var
        x, w = special.roots_genlaguerre(self.n_quadrature, shape - 1.0)
        w = np.asarray(w, dtype=float)
        if not np.all(np.isfinite(w)) or w.sum() <= 0:
            return np.array([1.0]), np.array([1.0])
        return x / shape, w / w.sum()

    @staticmethod
    def _dixon_coles(P: np.ndarray, lam_h: float, lam_a: float, rho: float) -> np.ndarray:
        if rho == 0.0 or P.shape[0] < 2:
            return P
        # Feasibility region keeping every tau >= 0.
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        rho = min(max(rho, lo), hi)
        Q = P.copy()
        Q[0, 0] *= 1.0 - lam_h * lam_a * rho
        Q[0, 1] *= 1.0 + lam_h * rho
        Q[1, 0] *= 1.0 + lam_a * rho
        Q[1, 1] *= 1.0 - rho
        return Q / Q.sum()

    # ------------------------------------------------------------------ #
    # Core
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any], params: Optional[Dict[str, float]] = None) -> np.ndarray:
        p = dict(self.params)
        if params:
            p.update(params)
        ctx = game_context or {}

        lam_h, lam_a = self._base_rates(ctx, p)
        var_h = self._rate_variance(ctx, "home", lam_h)
        var_a = self._rate_variance(ctx, "away", lam_a)
        agg0, gamma = self._state_context(ctx, p)
        g = self._grid_size(lam_h, lam_a, var_h, var_a, gamma)

        xh, wh = self._mixing_nodes(lam_h, var_h)
        xa, wa = self._mixing_nodes(lam_a, var_a)

        M = np.zeros((g + 1, g + 1))
        for mh, wgh in zip(xh, wh):
            for ma, wga in zip(xa, wa):
                lh, la = lam_h * mh, lam_a * ma
                if agg0 is None or gamma == 0.0:
                    M += wgh * wga * self._poisson_matrix(lh, la, g)
                else:
                    M += wgh * wga * self._state_matrix(lh, la, agg0, gamma, g)
        M = np.clip(M, 0.0, None)
        M /= M.sum()
        return self._dixon_coles(M, lam_h, lam_a, p["rho"])

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        M = self.score_matrix(game_context)
        scores = {
            (int(h), int(a)): float(M[h, a])
            for h in range(M.shape[0])
            for a in range(M.shape[1])
            if M[h, a] > 0.0
        }
        return DiscreteDistribution.from_scores(scores)

    # ------------------------------------------------------------------ #
    # Totals pricing and settlement helpers
    # ------------------------------------------------------------------ #
    def total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        M = self.score_matrix(game_context)
        n = M.shape[0] + M.shape[1] - 1
        pmf = np.zeros(n)
        for h in range(M.shape[0]):
            pmf[h:h + M.shape[1]] += M[h, :]
        return pmf

    def over_under(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Probabilities of over, under and push (push applies to integer lines)."""
        pmf = self.total_pmf(game_context)
        k = np.arange(pmf.size)
        return {
            "over": float(pmf[k > line].sum()),
            "under": float(pmf[k < line].sum()),
            "push": float(pmf[k == line].sum()),
        }

    def expected_value(self, game_context: Dict[str, Any], line: float, side: str, odds: float) -> Optional[float]:
        """EV per unit stake. Returns None below the eligibility floor (odds < min_odds)."""
        if odds < self.min_odds:
            return None
        pr = self.over_under(game_context, line)
        win = pr[side]
        lose = pr["under" if side == "over" else "over"]
        return win * (odds - 1.0) - lose

    @staticmethod
    def settle_total(home_goals_regulation: int, away_goals_regulation: int, line: float) -> str:
        """Full-game settlement uses the 90 minutes plus stoppage time only. Extra time and penalties are excluded."""
        total = home_goals_regulation + away_goals_regulation
        if total > line:
            return "over"
        if total < line:
            return "under"
        return "push"

    # ------------------------------------------------------------------ #
    # Offline estimation on historical UEL match data
    # ------------------------------------------------------------------ #
    def fit(
        self,
        matches: Iterable[Tuple[Dict[str, Any], int, int]],
        free_params: Sequence[str] = _PARAM_NAMES,
        max_iter: int = 400,
    ) -> Dict[str, float]:
        """
        Penalised maximum likelihood on (context, home_goals_90, away_goals_90) records.

        The ridge prior centred on the neutral values shrinks every coefficient toward
        independent Poisson, so the fitted structure has to earn its place in the data.
        """
        data: List[Tuple[Dict[str, Any], int, int]] = [(c, int(h), int(a)) for c, h, a in matches]
        if not data:
            return dict(self.params)
        names = [n for n in free_params if n in self.params]
        x0 = np.array([self.params[n] for n in names], dtype=float)

        def nll(x: np.ndarray) -> float:
            trial = dict(zip(names, x))
            total = 0.0
            for ctx, h, a in data:
                M = self.score_matrix(ctx, trial)
                if h < M.shape[0] and a < M.shape[1]:
                    prob = M[h, a]
                else:
                    prob = self.tail_mass
                total -= math.log(max(prob, np.finfo(float).tiny))
            return total + 0.5 * self.ridge_precision * float(np.dot(x, x))

        res = optimize.minimize(nll, x0, method="Nelder-Mead", options={"maxiter": max_iter, "xatol": 1e-4, "fatol": 1e-6})
        self.params.update(dict(zip(names, map(float, res.x))))
        return dict(self.params)