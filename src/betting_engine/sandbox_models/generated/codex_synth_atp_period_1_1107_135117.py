"""
Candidate model for ATP | period_1 | home_total.

The target is the number of games the home player wins in the first set.

The model is built bottom-up from tennis scoring rules rather than fitted
to an arbitrary count distribution:

    point-on-serve probability
        -> service-game hold probability (closed-form Markov chain)
        -> tiebreak win probability (point-level DP with 1-2-2 serve rotation)
        -> exact first-set score lattice (game-level DP with serve alternation,
           first server unknown, so a 50/50 coin toss)

The two latent serve-point probabilities (home and away) are solved
numerically. The solution makes the model's expected home and away first-set
games match the context's 'home_expected' and 'away_expected', with defaults of
5.76 and 5.31.

Point-level parameter uncertainty is integrated out with Gauss-Hermite
quadrature on the logit scale, and the centres are re-solved with that
uncertainty included. The resulting mixture is the source of realistic tail
mass (6-0, 6-1 and 7-6 cells) without any ad-hoc clamps. The Poisson-like
dispersion index of 1.0 is not imposed: set scoring is strongly
under-dispersed and bounded, and the lattice reproduces this structurally.

Notes on situational inputs:
- 'home_expected' and 'away_expected' are treated as sufficient statistics
  when they are present. Attack/defense ratings are used only to build the
  targets when those expectations are missing, to avoid double counting.
- Rest asymmetry enters as a smooth (tanh-saturated) logit shift on
  serve-point strength.
- Weather and venue are ignored except for 'is_dome' and 'wind_speed'. Wind
  degrades serve dominance by shrinking both serve logits toward parity.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in scope; import if available.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# ---------------------------------------------------------------------------
# Tennis scoring primitives
# ---------------------------------------------------------------------------

def _hold_prob(p: float) -> float:
    """Probability that the server holds a standard game.

    p is the server's probability of winning a point on serve.
    """
    q = 1.0 - p
    pre_deuce = p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
    deuce_reach = 20.0 * p ** 3 * q ** 3
    deuce_win = p * p / (1.0 - 2.0 * p * q)
    return pre_deuce + deuce_reach * deuce_win


def _tiebreak_prob(p_a: float, p_b: float) -> float:
    """Probability that player A wins a first-to-7, win-by-2 tiebreak.

    A serves point 0, then B serves points 1-2, A serves points 3-4, and so on.
    p_a and p_b are each player's point-win probability on their own serve.
    """
    def a_serves(k: int) -> bool:
        return ((k + 1) // 2) % 2 == 0

    # From 6-6 onward, every pair of points contains one serve by each player,
    # so the deuce phase reduces to a geometric race between two outcomes.
    win_pair = p_a * (1.0 - p_b)
    lose_pair = (1.0 - p_a) * p_b
    deuce_a = win_pair / (win_pair + lose_pair) if (win_pair + lose_pair) > 0 else 0.5

    dp = np.zeros((8, 8))
    dp[0, 0] = 1.0
    p_win = 0.0
    for total in range(0, 13):
        for i in range(0, 7):
            j = total - i
            if j < 0 or j > 6:
                continue
            mass = dp[i, j]
            if mass == 0.0:
                continue
            if i == 6 and j == 6:
                p_win += mass * deuce_a
                continue
            pa_point = p_a if a_serves(total) else 1.0 - p_b
            ni, nj = i + 1, j + 1
            if ni == 7:
                p_win += mass * pa_point
            else:
                dp[ni, j] += mass * pa_point
            if nj != 7:
                dp[i, nj] += mass * (1.0 - pa_point)
    return float(p_win)


def _set_lattice_given_server(p_h: float, p_a: float, home_serves_first: bool) -> Dict[Tuple[int, int], float]:
    """Exact distribution of the final first-set score (home games, away games)."""
    hold_h = _hold_prob(p_h)
    hold_a = _hold_prob(p_a)
    if home_serves_first:
        tb_home = _tiebreak_prob(p_h, p_a)
    else:
        tb_home = 1.0 - _tiebreak_prob(p_a, p_h)

    out: Dict[Tuple[int, int], float] = {}
    dp = {(0, 0): 1.0}
    for _ in range(13):
        nxt: Dict[Tuple[int, int], float] = {}
        for (i, j), mass in dp.items():
            if i == 6 and j == 6:
                out[(7, 6)] = out.get((7, 6), 0.0) + mass * tb_home
                out[(6, 7)] = out.get((6, 7), 0.0) + mass * (1.0 - tb_home)
                continue
            home_serving = ((i + j) % 2 == 0) == home_serves_first
            p_home_game = hold_h if home_serving else 1.0 - hold_a
            for (ni, nj), w in (((i + 1, j), p_home_game), ((i, j + 1), 1.0 - p_home_game)):
                m = mass * w
                if m == 0.0:
                    continue
                finished = ((ni >= 6 or nj >= 6) and abs(ni - nj) >= 2) or ni == 7 or nj == 7
                if finished:
                    out[(ni, nj)] = out.get((ni, nj), 0.0) + m
                else:
                    nxt[(ni, nj)] = nxt.get((ni, nj), 0.0) + m
        dp = nxt
        if not dp:
            break
    return out


@lru_cache(maxsize=4096)
def _set_lattice(p_h: float, p_a: float) -> Tuple[Tuple[Tuple[int, int], float], ...]:
    """Set lattice averaged over a 50/50 toss for who serves first."""
    a = _set_lattice_given_server(p_h, p_a, True)
    b = _set_lattice_given_server(p_h, p_a, False)
    keys = set(a) | set(b)
    return tuple(sorted((k, 0.5 * (a.get(k, 0.0) + b.get(k, 0.0))) for k in keys))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


# ---------------------------------------------------------------------------
# Candidate model
# ---------------------------------------------------------------------------

class ATPPeriod1HomeTotalModel(BaseCandidateModel):
    """Structural first-set games model for the home player's game total."""

    # Domain priors (league-level, not tuned to any bet sample).
    BASE_HOME_EXPECTED = 5.76
    BASE_AWAY_EXPECTED = 5.31
    # Cross-matchup spread of ATP serve-point win rates on the logit scale
    # (empirical serve-point SD is about 0.035 around a mean near 0.64).
    LOGIT_UNCERTAINTY = 0.15
    # Maximum serve-logit effect of rest asymmetry, reached through smooth
    # tanh saturation rather than a hard clamp.
    REST_LOGIT_SCALE = 0.04
    # Wind speed (km/h) at which serve dominance shrinks by a factor of 1/e.
    WIND_DECAY_KMH = 120.0
    GH_NODES = 5

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_1_1107_135117",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_1",
        family: str = "home_total",
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        nodes, weights = np.polynomial.hermite_e.hermegauss(self.GH_NODES)
        self._gh_nodes = nodes
        self._gh_weights = weights / weights.sum()

    # ---------------------------------------------------------------- features
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _side_rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(key))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{key}"))

    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mu_h = self._num(ctx.get("home_expected"))
        mu_a = self._num(ctx.get("away_expected"))
        if mu_h is not None and mu_a is not None and mu_h > 0 and mu_a > 0:
            return mu_h, mu_a

        # Fallback: multiplicative attack/defense ratings around a neutral 1.0.
        mu_h = mu_h if (mu_h and mu_h > 0) else self.BASE_HOME_EXPECTED
        mu_a = mu_a if (mu_a and mu_a > 0) else self.BASE_AWAY_EXPECTED
        ha, hd = self._side_rating(ctx, "home", "attack"), self._side_rating(ctx, "home", "defense")
        aa, ad = self._side_rating(ctx, "away", "attack"), self._side_rating(ctx, "away", "defense")
        if ha and ha > 0 and ad and ad > 0:
            mu_h *= math.sqrt(ha * ad)
        if aa and aa > 0 and hd and hd > 0:
            mu_a *= math.sqrt(aa * hd)
        return mu_h, mu_a

    def _logit_shifts(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        """Return (home_shift, away_shift, serve_dominance_scale)."""
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        shift = 0.0
        if rh is not None and ra is not None:
            # Fatigue matters most at short rest, so compare log(1 + days).
            shift = self.REST_LOGIT_SCALE * math.tanh(math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0)))

        scale = 1.0
        is_dome = bool(ctx.get("is_dome") or (ctx.get("weather") or {}).get("is_dome") if isinstance(ctx.get("weather"), dict) else ctx.get("is_dome"))
        wind = self._num(ctx.get("wind_speed"))
        if wind is None and isinstance(ctx.get("weather"), dict):
            wind = self._num(ctx["weather"].get("wind_speed"))
        if not is_dome and wind is not None and wind > 0:
            scale = math.exp(-wind / self.WIND_DECAY_KMH)
        return shift, -shift, scale

    # ------------------------------------------------------------- core maths
    def _mixture(self, c_h: float, c_a: float, sh: float, sa: float, scale: float) -> Dict[Tuple[int, int], float]:
        """Score lattice integrated over logit-scale serve uncertainty."""
        sigma = self.LOGIT_UNCERTAINTY
        out: Dict[Tuple[int, int], float] = {}
        for zi, wi in zip(self._gh_nodes, self._gh_weights):
            lh = scale * (c_h + sigma * zi) + sh
            ph = round(_sigmoid(lh), 5)
            for zj, wj in zip(self._gh_nodes, self._gh_weights):
                la = scale * (c_a + sigma * zj) + sa
                pa = round(_sigmoid(la), 5)
                w = wi * wj
                for k, v in _set_lattice(ph, pa):
                    out[k] = out.get(k, 0.0) + w * v
        return out

    @staticmethod
    def _moments(lattice: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
        eh = sum(k[0] * v for k, v in lattice.items())
        ea = sum(k[1] * v for k, v in lattice.items())
        return eh, ea

    def _solve(self, mu_h: float, mu_a: float, sh: float, sa: float, scale: float) -> Tuple[float, float]:
        def resid(x: np.ndarray) -> np.ndarray:
            eh, ea = self._moments(self._mixture(x[0], x[1], sh, sa, scale))
            return np.array([eh - mu_h, ea - mu_a])

        x0 = np.array([_logit(0.64), _logit(0.64)]) / max(scale, 1e-6)
        res = least_squares(resid, x0, bounds=([-1.0, -1.0], [4.0, 4.0]) if scale >= 1.0 else
                            ([-1.0 / scale, -1.0 / scale], [4.0 / scale, 4.0 / scale]),
                            xtol=1e-8, ftol=1e-10, max_nfev=200)
        return float(res.x[0]), float(res.x[1])

    # ---------------------------------------------------------------- public
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mu_h, mu_a = self._targets(ctx)
        sh, sa, scale = self._logit_shifts(ctx)

        c_h, c_a = self._solve(mu_h, mu_a, sh, sa, scale)
        lattice = self._mixture(c_h, c_a, sh, sa, scale)

        total = sum(lattice.values())
        scores = {k: v / total for k, v in lattice.items() if v > 0.0}

        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, AttributeError, ValueError):
            return DiscreteDistribution.from_scores([(h, a, p) for (h, a), p in scores.items()])


# Common aliases so engine loaders can find the class.
CandidateModel = ATPPeriod1HomeTotalModel
Model = ATPPeriod1HomeTotalModel