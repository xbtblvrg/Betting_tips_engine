"""
Candidate model for CHALLENGER | period_2 | moneyline.

ATP Challenger tennis, second set, winner market.

Mechanics
---------
* "home"/"away" is draw order only. Nothing in the structure gives a home edge.
  All asymmetry comes from situational inputs.
* The model is a hierarchical point -> game -> tiebreak -> set Markov chain.
  The serve-point probabilities (p_h, p_a) are found by numerically inverting
  the chain, so that E[games | p_h, p_a] matches 'home_expected' and
  'away_expected'.
* Set-level scoring is bounded and negatively correlated, so it is
  under-dispersed compared with Poisson. No count model is used.
* Set-1 information enters in two ways:
  (a) the observed set-1 games update the game-rate targets by precision
      weighting;
  (b) a shrunk logit shift beta * sign(set-1 winner) uses a zero-mean prior.
      Beta stays at zero until evidence is supplied through
      `update_set1_effect`.
* Rest, attack/defense exposure and wind enter only as small residual logit
  priors on point-win probability. Each one is shrunk toward zero.
* Tiebreak and break-back paths limit favourite over-confidence on their own.
  There are no clamps or odds caps.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

try:  # pragma: no cover - resolved by the sandbox
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:  # pragma: no cover
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")


# --------------------------------------------------------------------------- #
# Pure tennis-chain primitives
# --------------------------------------------------------------------------- #
def _logistic(u: float) -> float:
    if u >= 0:
        z = math.exp(-u)
        return 1.0 / (1.0 + z)
    z = math.exp(u)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    return math.log(p) - math.log1p(-p)


def hold_probability(p: float) -> float:
    """P(server holds) when the server wins each point with prob p.

    This is algebraically identical to p^4 (15 - 4p - 10p^2 / (1 - 2pq)).
    """
    q = 1.0 - p
    return p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q) + 20.0 * p ** 3 * q ** 3 * (p * p) / (1.0 - 2.0 * p * q)


def tiebreak_win_probability(pa: float, pb: float, a_serves_first: bool) -> float:
    """P(A wins a 7-point, win-by-2 tiebreak).

    pa : P(A wins a point on A's serve)
    pb : P(B wins a point on B's serve)
    The first server serves 1 point, then the server alternates every 2 points.
    """
    # From 6-6, each pair of points has one A serve and one B serve.
    win_pair = pa * (1.0 - pb)
    lose_pair = (1.0 - pa) * pb
    deuce = win_pair / (win_pair + lose_pair)

    mass = {(0, 0): 1.0}
    a_win = 0.0
    for t in range(13):
        for x in range(t + 1):
            y = t - x
            m = mass.pop((x, y), 0.0)
            if m == 0.0:
                continue
            if x == 6 and y == 6:
                a_win += m * deuce
                continue
            first_server_serving = ((t + 1) // 2) % 2 == 0
            a_serving = first_server_serving == a_serves_first
            pw = pa if a_serving else (1.0 - pb)
            if x + 1 == 7:
                a_win += m * pw
            else:
                mass[(x + 1, y)] = mass.get((x + 1, y), 0.0) + m * pw
            if y + 1 != 7:
                mass[(x, y + 1)] = mass.get((x, y + 1), 0.0) + m * (1.0 - pw)
    return a_win


def set_chain(pa: float, pb: float, a_serves_first: bool) -> Tuple[float, float, float, Dict[Tuple[int, int], float]]:
    """Exact set chain (tiebreak at 6-6).

    Returns P(A wins the set), E[games A], E[games B] and the terminal
    score lattice.
    """
    ha = hold_probability(pa)
    hb = hold_probability(pb)
    mass = {(0, 0): 1.0}
    terminal: Dict[Tuple[int, int], float] = {}

    def _term(state, m):
        terminal[state] = terminal.get(state, 0.0) + m

    for k in range(13):
        for i in range(k + 1):
            j = k - i
            m = mass.pop((i, j), 0.0)
            if m == 0.0:
                continue
            a_serving = (k % 2 == 0) == a_serves_first
            if i == 6 and j == 6:
                tb = tiebreak_win_probability(pa, pb, a_serving)
                _term((7, 6), m * tb)
                _term((6, 7), m * (1.0 - tb))
                continue
            ga = ha if a_serving else (1.0 - hb)
            ni, nj = i + 1, j
            if (ni == 6 and nj <= 4) or ni == 7:
                _term((ni, nj), m * ga)
            else:
                mass[(ni, nj)] = mass.get((ni, nj), 0.0) + m * ga
            ni, nj = i, j + 1
            if (nj == 6 and ni <= 4) or nj == 7:
                _term((ni, nj), m * (1.0 - ga))
            else:
                mass[(ni, nj)] = mass.get((ni, nj), 0.0) + m * (1.0 - ga)

    p_a = sum(v for (x, y), v in terminal.items() if x > y)
    e_a = sum(v * x for (x, _), v in terminal.items())
    e_b = sum(v * y for (_, y), v in terminal.items())
    return p_a, e_a, e_b, terminal


# --------------------------------------------------------------------------- #
# Candidate model
# --------------------------------------------------------------------------- #
class ChallengerPeriod2MoneylineModel(BaseCandidateModel):
    """Point -> game -> set Markov model for the Challenger second-set winner."""

    # Empirical segment moments (games per set). They are used only as a
    # symmetric prior centre, so there is no structural home edge.
    EMPIRICAL_HOME_MEAN = 5.76
    EMPIRICAL_AWAY_MEAN = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_challenger_period_2_1107_233932",
        version: str = "1.0.0",
        league: str = "CHALLENGER",
        scope: str = "period_2",
        family: str = "moneyline",
        hyperparameters: Optional[Dict[str, float]] = None,
    ):
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
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

        # Documented, tunable priors. Every one is a shrinkage weight, not a clamp.
        hp = {
            # Ridge precision pulling the logit serve-probs to the segment prior
            # centre (weak: target matching dominates).
            "prior_precision": 0.05,
            # Pre-match information weight, in units of "sets", used when
            # precision-weighting the observed set-1 games.
            "prior_set_weight": 4.0,
            # Residual logit coefficients. These are small and act as priors only.
            "rest_coef": 0.02,
            "exposure_coef": 0.05,
            "wind_coef": 0.003,
            # Set-1 momentum effect: hierarchical zero-mean prior.
            "set1_beta_hat": 0.0,
            "set1_beta_nobs": 0.0,
            "set1_beta_kappa": 200.0,
        }
        if hyperparameters:
            hp.update({k: float(v) for k, v in hyperparameters.items()})
        self.hp = hp

        sym = 0.5 * (self.EMPIRICAL_HOME_MEAN + self.EMPIRICAL_AWAY_MEAN)
        self._prior_u = self._symmetric_prior_logit(sym)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Hierarchical set-1 effect (zero-mean shrinkage)
    # ------------------------------------------------------------------ #
    def update_set1_effect(self, beta_hat: float, n_obs: float) -> None:
        """Supply an externally estimated set-1 winner logit effect."""
        self.hp["set1_beta_hat"] = float(beta_hat)
        self.hp["set1_beta_nobs"] = float(n_obs)

    def _set1_beta(self) -> float:
        n = self.hp["set1_beta_nobs"]
        return self.hp["set1_beta_hat"] * n / (n + self.hp["set1_beta_kappa"])

    # ------------------------------------------------------------------ #
    # Feature access
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
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

    def _exposure(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(kind))
            if v is not None:
                return v
        return self._get(ctx, f"{side}_{kind}")

    def _set1_state(self, ctx: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[bool]]:
        gh = self._get(ctx, "set1_home_games", "period_1_home", "period_1_home_score", "home_period_1")
        ga = self._get(ctx, "set1_away_games", "period_1_away", "period_1_away_score", "away_period_1")
        winner = ctx.get("set1_winner") or ctx.get("period_1_winner")
        if winner not in ("home", "away"):
            winner = None
            if gh is not None and ga is not None and gh != ga:
                winner = "home" if gh > ga else "away"
        first = ctx.get("set1_first_server") or ctx.get("first_server")
        home_first_set2: Optional[bool] = None
        if first in ("home", "away") and gh is not None and ga is not None:
            n_games = int(round(gh + ga))  # a tiebreak counts as one game
            set1_first_home = first == "home"
            home_first_set2 = set1_first_home if n_games % 2 == 0 else (not set1_first_home)
        explicit = ctx.get("set2_first_server")
        if explicit in ("home", "away"):
            home_first_set2 = explicit == "home"
        return (
            None if gh is None else int(round(gh)),
            None if ga is None else int(round(ga)),
            winner,
            home_first_set2,
        )

    # ------------------------------------------------------------------ #
    # Chain evaluation and inversion
    # ------------------------------------------------------------------ #
    @staticmethod
    def _averaged_chain(ph: float, pa: float, home_first: Optional[bool]):
        if home_first is None:
            r1 = set_chain(ph, pa, True)
            r2 = set_chain(ph, pa, False)
            return 0.5 * (r1[0] + r2[0]), 0.5 * (r1[1] + r2[1]), 0.5 * (r1[2] + r2[2])
        r = set_chain(ph, pa, home_first)
        return r[0], r[1], r[2]

    def _symmetric_prior_logit(self, games_mean: float) -> float:
        def loss(u):
            p = _logistic(float(u[0]))
            _, e, _ = self._averaged_chain(p, p, None)
            return (e - games_mean) ** 2

        start = _logit(0.6)
        res = minimize(loss, x0=np.array([start]), method="Nelder-Mead",
                       options={"xatol": 1e-7, "fatol": 1e-12, "maxiter": 400})
        return float(res.x[0])

    def _invert(self, t_h: float, t_a: float, home_first: Optional[bool]) -> Tuple[float, float, float]:
        lam = self.hp["prior_precision"]
        u0 = self._prior_u

        def loss(u):
            ph, pa = _logistic(float(u[0])), _logistic(float(u[1]))
            _, eh, ea = self._averaged_chain(ph, pa, home_first)
            fit = (eh - t_h) ** 2 / max(t_h, 1e-9) + (ea - t_a) ** 2 / max(t_a, 1e-9)
            return fit + lam * ((u[0] - u0) ** 2 + (u[1] - u0) ** 2)

        res = minimize(loss, x0=np.array([u0, u0]), method="Nelder-Mead",
                       options={"xatol": 1e-6, "fatol": 1e-12, "maxiter": 1200})
        return float(res.x[0]), float(res.x[1]), float(res.fun)

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        hp = self.hp

        t_h = self._get(ctx, "home_expected")
        t_a = self._get(ctx, "away_expected")
        sym = 0.5 * (self.EMPIRICAL_HOME_MEAN + self.EMPIRICAL_AWAY_MEAN)
        if t_h is None or t_h <= 0:
            t_h = sym
        if t_a is None or t_a <= 0:
            t_a = sym

        # Set-1 information: precision-weighted update of the game-rate targets.
        g1h, g1a, s1_winner, home_first = self._set1_state(ctx)
        if g1h is not None and g1a is not None:
            w = hp["prior_set_weight"]
            t_h = (w * t_h + g1h) / (w + 1.0)
            t_a = (w * t_a + g1a) / (w + 1.0)

        u_h, u_a, fit_loss = self._invert(t_h, t_a, home_first)

        # Residual situational priors on point-win logits (shrunk, small).
        rest_h = self._get(ctx, "home_rest_days")
        rest_a = self._get(ctx, "away_rest_days")
        if rest_h is not None and rest_a is not None and rest_h >= 0 and rest_a >= 0:
            d_rest = math.log1p(rest_h) - math.log1p(rest_a)
            u_h += 0.5 * hp["rest_coef"] * d_rest
            u_a -= 0.5 * hp["rest_coef"] * d_rest

        att_h, def_h = self._exposure(ctx, "home", "attack"), self._exposure(ctx, "home", "defense")
        att_a, def_a = self._exposure(ctx, "away", "attack"), self._exposure(ctx, "away", "defense")
        if all(v is not None and v > 0 for v in (att_h, def_h, att_a, def_a)):
            # Serve strength vs. the opponent's return strength.
            # Higher 'defense' means a better returner.
            u_h += hp["exposure_coef"] * (math.log(att_h) - math.log(def_a))
            u_a += hp["exposure_coef"] * (math.log(att_a) - math.log(def_h))

        wind = self._get(ctx, "wind_speed")
        is_dome = bool(ctx.get("is_dome", False))
        if wind is not None and wind > 0 and not is_dome:
            # Wind degrades serving symmetrically, so break rates rise.
            u_h -= hp["wind_coef"] * wind
            u_a -= hp["wind_coef"] * wind

        ph, pa = _logistic(u_h), _logistic(u_a)
        p_home, e_h, e_a = self._averaged_chain(ph, pa, home_first)

        # Set-1 winner momentum: shrunk logit shift with a zero-mean prior.
        beta = self._set1_beta()
        if s1_winner is not None and beta != 0.0:
            sign = 1.0 if s1_winner == "home" else -1.0
            p_home = _logistic(_logit(p_home) + beta * sign)

        p_away = 1.0 - p_home

        self.last_diagnostics = {
            "target_home_games": t_h,
            "target_away_games": t_a,
            "serve_point_home": ph,
            "serve_point_away": pa,
            "hold_home": hold_probability(ph),
            "hold_away": hold_probability(pa),
            "model_home_games": e_h,
            "model_away_games": e_a,
            "inversion_loss": fit_loss,
            "set1_beta_effective": beta,
            "set2_home_serves_first": home_first,
            "p_home": p_home,
        }

        probs = {"home": p_home, "draw": 0.0, "away": p_away}
        if DiscreteDistribution is None:  # pragma: no cover
            return probs  # type: ignore[return-value]
        try:
            return DiscreteDistribution(probabilities=probs)
        except TypeError:  # pragma: no cover
            return DiscreteDistribution.from_probabilities(p_home, 0.0, p_away)


# Aliases for loaders that look for a conventional name.
CandidateModel = ChallengerPeriod2MoneylineModel
Model = ChallengerPeriod2MoneylineModel