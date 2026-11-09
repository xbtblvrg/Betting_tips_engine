import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # Preferred location for the distribution primitive.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # Fall back to the pre-loaded execution scope.
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------------------
# Structural constants of a standard tiebreak set (rules of tennis, not fitted values).
# --------------------------------------------------------------------------------------
SET_SCORES: List[Tuple[int, int]] = [
    (6, 0), (6, 1), (6, 2), (6, 3), (6, 4), (7, 5), (7, 6),
    (0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 7), (6, 7),
]
MIN_SET_GAMES = 6.0    # 6-0
MAX_SET_GAMES = 13.0   # 7-6
MAX_PLAYER_GAMES = 7.0


def _is_terminal_set_state(i: int, j: int) -> bool:
    """Absorbing game scores of a tiebreak set (6-6 is resolved separately)."""
    if i == 6 and j <= 4:
        return True
    if j == 6 and i <= 4:
        return True
    return (i, j) in ((7, 5), (5, 7))


class ChallengerPeriod1ExactScoreModel(BaseCandidateModel):
    """
    First-set exact score model for ATP Challenger tennis.

    Architecture
    ------------
    1. Latent parameters are per-player service-hold probabilities (h_H, h_A), not
       game counts. They are recovered by inverting the exact set Markov chain so that
       the model-implied expected games equal (home_expected, away_expected).
    2. Game-level chain with alternating serve over states (i, j), absorbing on the 14
       legal set scores. Mass is mixed over the unknown first server (or set exactly if
       the context supplies it).
    3. 6-6 is resolved at point level. Point-on-serve rates are back-solved from hold
       rates through the closed-form game-from-points function, then pushed through
       an exact tiebreak recursion (A, BB, AA, ... serve pattern with the 2-point
       win-by rule at 6-6).
    4. Break clustering / match-day form: hold rates carry a Beta random effect with
       mean m and match-level SD sigma. The Beta concentration is kappa = m(1-m)/sigma^2,
       a smooth map that is always valid (no clipping). The mixture is integrated with
       Gauss-Jacobi quadrature, which is exact for the Beta weight. The inversion in (1)
       runs on the *mixture*, so the means stay anchored to the targets while the
       random effect only redistributes mass (fatter 6-0/6-1 and 7-6 tails).
    5. Situational information (serve/return exposure, pace, rest, wind) enters as
       additive shifts on the logit-hold scale after the anchor inversion.

    All elasticities are explicit, documented, constructor-overridable priors.
    """

    DEFAULT_PARAMS: Dict[str, float] = {
        # Empirical anchors for the segment (first-set games).
        "league_home_games": 5.76,
        "league_away_games": 5.31,
        # Observed dispersion index for the segment (Var/Mean); scales the random effect.
        "observed_dispersion_index": 1.0,
        # Match-to-match SD of a player's hold rate on the Challenger tour (prior).
        "hold_sd_match": 0.065,
        # Gauss-Jacobi nodes per player for the Beta random effect.
        "quadrature_nodes": 9,
        # Logit-hold elasticity to log(serve strength / opponent return strength).
        "serve_return_elasticity": 0.25,
        # Logit-hold elasticity to log(pace / court-speed index); faster -> more holds.
        "pace_elasticity": 0.40,
        # Logit-hold elasticity to log1p(rest days) relative to the reference rest.
        "rest_elasticity": 0.05,
        "reference_rest_days": 1.0,
        # Wind degrades serve: saturating logit-hold penalty w / (w + w_ref).
        "wind_break_scale": 0.20,
        "wind_reference": 20.0,
        # Starting point for the hold-rate inversion only (not a constraint).
        "hold_init": 0.76,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_challenger_period_1_1108_084826",
        version: str = "1.0.0",
        league: str = "CHALLENGER",
        scope: str = "period_1",
        family: str = "exact_score",
        **params: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__()
            self.model_id = model_id
            self.version = version
            self.league = league
            self.scope = scope
            self.family = family

        self.params: Dict[str, float] = dict(self.DEFAULT_PARAMS)
        for key, value in params.items():
            if key in self.params:
                self.params[key] = float(value)

        self._inversion_cache: Dict[Tuple[float, ...], Tuple[float, float]] = {}
        self.last_diagnostics: Dict[str, Any] = {}

    # ==================================================================================
    # Public API
    # ==================================================================================
    def predict_distribution(self, game_context: Dict[str, Any]):
        ctx = game_context or {}

        target_h, target_a = self._targets(ctx)
        sigma = self._effective_hold_sd(ctx)
        pi_home_first = self._home_first_probability(ctx)

        # (1) Anchor inversion on the full mixture model.
        m_h, m_a = self._invert_holds(target_h, target_a, sigma, pi_home_first)

        # (5) Situational shifts on the logit-hold scale.
        shift_h, shift_a = self._situational_logit_shifts(ctx)
        m_h_adj = float(special.expit(special.logit(m_h) + shift_h))
        m_a_adj = float(special.expit(special.logit(m_a) + shift_a))

        probs = self._score_probabilities(m_h_adj, m_a_adj, sigma, pi_home_first)
        total = float(probs.sum())
        probs = probs / total if total > 0 else np.full(len(SET_SCORES), 1.0 / len(SET_SCORES))

        scores = {score: float(p) for score, p in zip(SET_SCORES, probs)}

        exp_h = sum(s[0] * p for s, p in scores.items())
        exp_a = sum(s[1] * p for s, p in scores.items())
        self.last_diagnostics = {
            "target_home_games": target_h,
            "target_away_games": target_a,
            "anchor_hold_home": m_h,
            "anchor_hold_away": m_a,
            "adjusted_hold_home": m_h_adj,
            "adjusted_hold_away": m_a_adj,
            "logit_shift_home": shift_h,
            "logit_shift_away": shift_a,
            "hold_sd_match": sigma,
            "p_home_serves_first": pi_home_first,
            "model_expected_home_games": exp_h,
            "model_expected_away_games": exp_a,
            "p_home_wins_set": sum(p for s, p in scores.items() if s[0] > s[1]),
            "p_tiebreak": scores[(7, 6)] + scores[(6, 7)],
        }

        return self._build_distribution(scores)

    # ==================================================================================
    # Context parsing
    # ==================================================================================
    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        try:
            out = float(value)
        except (TypeError, ValueError):
            return None
        return out if math.isfinite(out) else None

    def _side_feature(self, ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            val = self._as_float(block.get(name))
            if val is not None:
                return val
        return self._as_float(ctx.get(f"{side}_{name}"))

    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        lh = self.params["league_home_games"]
        la = self.params["league_away_games"]
        th = self._as_float(ctx.get("home_expected"))
        ta = self._as_float(ctx.get("away_expected"))
        th = lh if th is None or th <= 0 else th
        ta = la if ta is None or ta <= 0 else ta

        # Structural feasibility of a single tiebreak set: 6 < total < 13, each < 7.
        # Inputs outside it (e.g., match-level game totals) keep their share and are
        # rescaled to the segment's empirical total.
        total = th + ta
        if not (MIN_SET_GAMES < total < MAX_SET_GAMES) or max(th, ta) >= MAX_PLAYER_GAMES:
            scale = (lh + la) / total
            th, ta = th * scale, ta * scale
        return th, ta

    def _effective_hold_sd(self, ctx: Dict[str, Any]) -> float:
        di = self._as_float(ctx.get("dispersion_index"))
        if di is None or di <= 0:
            di = self.params["observed_dispersion_index"]
        if di <= 0:
            di = 1.0
        return self.params["hold_sd_match"] * math.sqrt(di)

    def _home_first_probability(self, ctx: Dict[str, Any]) -> float:
        for key in ("home_serves_first", "home_first_server"):
            if key in ctx and ctx[key] is not None:
                val = ctx[key]
                if isinstance(val, bool):
                    return 1.0 if val else 0.0
                f = self._as_float(val)
                if f is not None and 0.0 <= f <= 1.0:
                    return f
        return 0.5

    def _situational_logit_shifts(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.params
        shift_h = 0.0
        shift_a = 0.0

        # Serve ("attack") vs opponent return ("defense") exposure.
        att_h = self._side_feature(ctx, "home", "attack")
        att_a = self._side_feature(ctx, "away", "attack")
        def_h = self._side_feature(ctx, "home", "defense")
        def_a = self._side_feature(ctx, "away", "defense")
        if att_h and att_h > 0 and def_a and def_a > 0:
            shift_h += p["serve_return_elasticity"] * (math.log(att_h) - math.log(def_a))
        if att_a and att_a > 0 and def_h and def_h > 0:
            shift_a += p["serve_return_elasticity"] * (math.log(att_a) - math.log(def_h))

        # Court pace / tempo index (1.0 = tour-average); applies to both servers.
        pace = None
        for key in ("pace", "tempo", "court_speed", "surface_speed"):
            pace = self._as_float(ctx.get(key))
            if pace is not None:
                break
        if pace is not None and pace > 0:
            common = p["pace_elasticity"] * math.log(pace)
            shift_h += common
            shift_a += common

        # Rest: Challenger schedules often mean back-to-back days; fatigue hurts serve.
        ref = math.log1p(max(p["reference_rest_days"], 0.0))
        rest_h = self._as_float(ctx.get("home_rest_days"))
        rest_a = self._as_float(ctx.get("away_rest_days"))
        if rest_h is not None and rest_h >= 0:
            shift_h += p["rest_elasticity"] * (math.log1p(rest_h) - ref)
        if rest_a is not None and rest_a >= 0:
            shift_a += p["rest_elasticity"] * (math.log1p(rest_a) - ref)

        # Wind (outdoor only) degrades both serves with a saturating penalty.
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        is_dome = ctx.get("is_dome", weather.get("is_dome", False))
        wind = self._as_float(ctx.get("wind_speed", weather.get("wind_speed")))
        if not bool(is_dome) and wind is not None and wind > 0:
            penalty = p["wind_break_scale"] * wind / (wind + p["wind_reference"])
            shift_h -= penalty
            shift_a -= penalty

        return shift_h, shift_a

    # ==================================================================================
    # Point / game / tiebreak / set mechanics (vectorised over quadrature grids)
    # ==================================================================================
    @staticmethod
    def _hold_from_point(q: np.ndarray) -> np.ndarray:
        """P(server holds) from point-on-serve probability q (exact, includes deuce)."""
        r = 1.0 - q
        pre_deuce = q ** 4 * (1.0 + 4.0 * r + 10.0 * r ** 2)
        deuce = 20.0 * q ** 3 * r ** 3 * (q ** 2 / (1.0 - 2.0 * q * r))
        return pre_deuce + deuce

    def _point_from_hold(self, h: np.ndarray) -> np.ndarray:
        """Invert the monotone game-from-points map by vectorised bisection."""
        h = np.asarray(h, dtype=float)
        lo = np.zeros_like(h)
        hi = np.ones_like(h)
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            above = self._hold_from_point(mid) > h
            hi = np.where(above, mid, hi)
            lo = np.where(above, lo, mid)
        return 0.5 * (lo + hi)

    @staticmethod
    def _tiebreak_first_server_wins(qa: np.ndarray, qb: np.ndarray) -> np.ndarray:
        """P(player A wins a 7-point tiebreak) when A serves the first point."""
        shape = np.broadcast(qa, qb).shape
        states: Dict[Tuple[int, int], np.ndarray] = {(0, 0): np.ones(shape)}
        win = np.zeros(shape)
        for total in range(12):
            for a in range(max(0, total - 6), min(total, 6) + 1):
                b = total - a
                pr = states.pop((a, b), None)
                if pr is None:
                    continue
                a_serves = ((total + 1) // 2) % 2 == 0
                pa = qa if a_serves else 1.0 - qb
                if a + 1 == 7:
                    win = win + pr * pa
                else:
                    states[(a + 1, b)] = states.get((a + 1, b), 0.0) + pr * pa
                if b + 1 < 7:
                    states[(a, b + 1)] = states.get((a, b + 1), 0.0) + pr * (1.0 - pa)
        pr66 = states.get((6, 6))
        if pr66 is not None:
            ww = qa * (1.0 - qb)
            ll = (1.0 - qa) * qb
            denom = ww + ll
            safe = np.where(denom > 0, denom, 1.0)
            p_from_66 = np.where(denom > 0, ww / safe, 0.5)
            win = win + pr66 * p_from_66
        return win

    def _set_score_grid(
        self,
        h_h: np.ndarray,
        h_a: np.ndarray,
        q_h: np.ndarray,
        q_a: np.ndarray,
        home_first: bool,
    ) -> Dict[Tuple[int, int], np.ndarray]:
        shape = np.broadcast(h_h, h_a).shape
        states: Dict[Tuple[int, int], np.ndarray] = {(0, 0): np.ones(shape)}
        out = {s: np.zeros(shape) for s in SET_SCORES}

        for total in range(12):
            home_serves = ((total % 2) == 0) == home_first
            p_home_game = h_h if home_serves else 1.0 - h_a
            for i in range(max(0, total - 6), min(total, 6) + 1):
                j = total - i
                pr = states.pop((i, j), None)
                if pr is None:
                    continue
                for ni, nj, pp in ((i + 1, j, p_home_game), (i, j + 1, 1.0 - p_home_game)):
                    mass = pr * pp
                    if _is_terminal_set_state(ni, nj):
                        out[(ni, nj)] = out[(ni, nj)] + mass
                    else:
                        states[(ni, nj)] = states.get((ni, nj), 0.0) + mass

        pr66 = states.pop((6, 6), None)
        if pr66 is not None:
            # Game 13 index is even -> the set's first server opens the tiebreak.
            if home_first:
                tb_home = self._tiebreak_first_server_wins(q_h, q_a)
            else:
                tb_home = 1.0 - self._tiebreak_first_server_wins(q_a, q_h)
            out[(7, 6)] = out[(7, 6)] + pr66 * tb_home
            out[(6, 7)] = out[(6, 7)] + pr66 * (1.0 - tb_home)
        return out

    # ==================================================================================
    # Beta random effect + mixture
    # ==================================================================================
    def _beta_quadrature(self, m: float, sigma: float) -> Tuple[np.ndarray, np.ndarray]:
        n = int(max(1, round(self.params["quadrature_nodes"])))
        if sigma <= 0 or n == 1:
            return np.array([m]), np.array([1.0])
        # Smooth, always-valid concentration: kappa = m(1-m)/sigma^2  (> 0 for all m).
        kappa = m * (1.0 - m) / (sigma ** 2)
        a = kappa * m
        b = kappa * (1.0 - m)
        x, w = special.roots_jacobi(n, b - 1.0, a - 1.0)
        nodes = 0.5 * (1.0 + x)
        weights = w / w.sum()
        return nodes, weights

    def _score_probabilities(
        self, m_h: float, m_a: float, sigma: float, pi_home_first: float
    ) -> np.ndarray:
        nodes_h, w_h = self._beta_quadrature(m_h, sigma)
        nodes_a, w_a = self._beta_quadrature(m_a, sigma)
        h_h = nodes_h[:, None]
        h_a = nodes_a[None, :]
        q_h = self._point_from_hold(h_h)
        q_a = self._point_from_hold(h_a)
        weight_grid = w_h[:, None] * w_a[None, :]

        probs = np.zeros(len(SET_SCORES))
        for home_first, pi in ((True, pi_home_first), (False, 1.0 - pi_home_first)):
            if pi <= 0:
                continue
            grid = self._set_score_grid(h_h, h_a, q_h, q_a, home_first)
            for k, s in enumerate(SET_SCORES):
                probs[k] += pi * float(np.sum(weight_grid * grid[s]))
        return probs

    def _expected_games(
        self, m_h: float, m_a: float, sigma: float, pi_home_first: float
    ) -> Tuple[float, float]:
        probs = self._score_probabilities(m_h, m_a, sigma, pi_home_first)
        e_h = float(sum(s[0] * p for s, p in zip(SET_SCORES, probs)))
        e_a = float(sum(s[1] * p for s, p in zip(SET_SCORES, probs)))
        return e_h, e_a

    def _invert_holds(
        self, target_h: float, target_a: float, sigma: float, pi_home_first: float
    ) -> Tuple[float, float]:
        key = (round(target_h, 6), round(target_a, 6), round(sigma, 6), round(pi_home_first, 6))
        cached = self._inversion_cache.get(key)
        if cached is not None:
            return cached

        def residuals(theta: np.ndarray) -> np.ndarray:
            m_h = float(special.expit(theta[0]))
            m_a = float(special.expit(theta[1]))
            e_h, e_a = self._expected_games(m_h, m_a, sigma, pi_home_first)
            return np.array([e_h - target_h, e_a - target_a])

        base = special.logit(self.params["hold_init"])
        # Initial share tilt only; the solver is unconstrained on the logit scale.
        tilt = (target_h - target_a) / max(target_h + target_a, 1e-9)
        x0 = np.array([base + tilt, base - tilt])

        sol = optimize.least_squares(residuals, x0, method="trf", xtol=1e-10, ftol=1e-10)
        m_h = float(special.expit(sol.x[0]))
        m_a = float(special.expit(sol.x[1]))
        self._inversion_cache[key] = (m_h, m_a)
        return m_h, m_a

    # ==================================================================================
    # Output
    # ==================================================================================
    @staticmethod
    def _build_distribution(scores: Dict[Tuple[int, int], float]):
        if DiscreteDistribution is None:
            raise RuntimeError("DiscreteDistribution primitive is not available in scope.")
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError, KeyError):
            return DiscreteDistribution(
                probabilities={f"{h}-{a}": p for (h, a), p in scores.items()}
            )