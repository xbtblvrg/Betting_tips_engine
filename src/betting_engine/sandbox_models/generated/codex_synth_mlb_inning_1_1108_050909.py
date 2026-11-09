"""
MLB | inning_1 | double_chance candidate model.

Architecture
------------
1. Half-inning run distribution from a base-out absorbing Markov chain.
   A first-inning half is a sequence of plate appearances over 24 transient
   (outs, bases) states that ends at the third out. Per-PA event rates are
   scaled by a single on-base intensity `s`, solved so that E[R] equals the
   situational mean mu. Zero-inflation (~70% scoreless halves) and big-inning
   clustering (var/mean ~1.6-2.0) come out of the base-state mechanics instead
   of being imposed through a Poisson or dispersion constant. Because the
   bottom of the 1st is always played, no walk-off truncation applies.

2. Situational means. They are anchored at the empirical inning-1 baselines
   (home 0.69, away 0.63). When an upstream inning-scale `*_expected` exists,
   it is used directly. A game-scale expectation is mapped to inning 1 by the
   anchor share. Otherwise the anchor is scaled multiplicatively by matchup
   (attack x opposing defense), climate (temperature, dome) and rest.

3. Shared-environment coupling. A mean-one gamma frailty Z ~ Gamma(1/theta, theta)
   multiplies both means. It represents the park, weather, umpire zone, and
   first-time-through-the-order factors that both sides share. Theta grows with
   outdoor wind exposure. The joint mass is integrated exactly with generalized
   Gauss-Laguerre quadrature, so the 0-0 tie mass gets the positive
   correlation it needs.

4. Output is the 3-way (home/draw/away) mass. The double-chance legs follow
   exactly: 1X = p_h + p_d, X2 = p_d + p_a, 12 = p_h + p_a.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import roots_genlaguerre

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------- #
# Base-out Markov chain for one half-inning
# --------------------------------------------------------------------------- #
# Base encoding: bit0 = runner on 1st, bit1 = 2nd, bit2 = 3rd.
_EVENTS = ("bb", "1b", "2b", "3b", "hr")


def _n_runners(b: int) -> int:
    return (b & 1) + ((b >> 1) & 1) + ((b >> 2) & 1)


def _event_transitions(event: str, outs: int, b: int,
                       dp_rate: float, sf_rate: float):
    """List of (new_outs, new_bases, runs, prob) for one PA outcome."""
    b1, b2, b3 = b & 1, (b >> 1) & 1, (b >> 2) & 1
    if event == "bb":
        if not b1:
            return [(outs, b | 1, 0, 1.0)]
        if not b2:
            return [(outs, 3 | (4 if b3 else 0), 0, 1.0)]
        if not b3:
            return [(outs, 7, 0, 1.0)]
        return [(outs, 7, 1, 1.0)]
    if event == "1b":
        return [(outs, 1 | (2 if b1 else 0), b2 + b3, 1.0)]
    if event == "2b":
        return [(outs, 2 | (4 if b1 else 0), b2 + b3, 1.0)]
    if event == "3b":
        return [(outs, 4, _n_runners(b), 1.0)]
    if event == "hr":
        return [(outs, 0, _n_runners(b) + 1, 1.0)]
    # Batting out: double plays and sacrifice/productive outs (< 2 outs only).
    res = []
    p_rem = 1.0
    if b1 and outs < 2:
        res.append((outs + 2, b & ~1, 0, dp_rate))
        p_rem -= dp_rate
    if b3 and outs < 2:
        res.append((outs + 1, b & ~4, 1, p_rem * sf_rate))
        res.append((outs + 1, b, 0, p_rem * (1.0 - sf_rate)))
    else:
        res.append((outs + 1, b, 0, p_rem))
    return res


@lru_cache(maxsize=8)
def _build_run_lattice(mix: Tuple[float, ...], dp_rate: float, sf_rate: float,
                       run_cap: int, n_grid: int):
    """
    Precompute half-inning run pmfs on a grid of on-base intensities.
    Returns (means[n_grid], pmfs[n_grid, run_cap+1]); means strictly increasing.
    """
    R = run_cap + 1
    n_t = 24 * R

    def idx(o, b, r):
        return (o * 8 + b) * R + r

    events = ("out",) + _EVENTS
    Q_e = {e: np.zeros((n_t, n_t)) for e in events}
    A_e = {e: np.zeros((n_t, R)) for e in events}
    for o in range(3):
        for b in range(8):
            for e in events:
                for (no, nb, dr, p) in _event_transitions(e, o, b, dp_rate, sf_rate):
                    if p <= 0.0:
                        continue
                    for r in range(R):
                        nr = min(r + dr, run_cap)
                        if no >= 3:
                            A_e[e][idx(o, b, r), nr] += p
                        else:
                            Q_e[e][idx(o, b, r), idx(no, nb, nr)] += p

    m = np.asarray(mix, dtype=float)
    m_sum = float(m.sum())
    s_hi = (1.0 - 1e-3) / m_sum                     # keep P(out) > 0
    s_grid = np.linspace(s_hi / n_grid, s_hi, n_grid)
    start = np.zeros(n_t)
    start[idx(0, 0, 0)] = 1.0
    eye = np.eye(n_t)
    runs_axis = np.arange(R, dtype=float)

    means = np.empty(n_grid)
    pmfs = np.empty((n_grid, R))
    for g, s in enumerate(s_grid):
        p_out = 1.0 - s * m_sum
        Q = p_out * Q_e["out"]
        A = p_out * A_e["out"]
        for k, e in enumerate(_EVENTS):
            Q = Q + s * m[k] * Q_e[e]
            A = A + s * m[k] * A_e[e]
        y = np.linalg.solve((eye - Q).T, start)      # expected visits
        pmf = np.clip(y @ A, 0.0, None)
        pmf /= pmf.sum()
        pmfs[g] = pmf
        means[g] = float(pmf @ runs_axis)

    means = np.maximum.accumulate(means + np.arange(n_grid) * 1e-14)
    return means, pmfs


class MLBInning1DoubleChanceModel(BaseCandidateModel):
    """Markov-chain half-inning runs + shared gamma frailty -> exact 3-way mass."""

    # Empirical inning-1 anchors (segment baselines).
    ANCHOR_HOME = 0.69
    ANCHOR_AWAY = 0.63

    # Domain priors (league plate-appearance composition; overridable).
    EVENT_MIX = (0.090, 0.145, 0.045, 0.004, 0.032)  # BB/HBP, 1B, 2B, 3B, HR
    DP_RATE = 0.11          # P(GIDP | out, runner on 1st, <2 outs)
    SF_RATE = 0.28          # P(run scores on out | runner on 3rd, <2 outs)
    RUN_CAP = 20
    GRID_POINTS = 140

    # Scale mapping for game-level expectations.
    LEAGUE_TEAM_GAME_RUNS = 4.45
    INNING_SCALE_CEILING = 2.5

    # Shared-environment frailty (variance of mean-one multiplier).
    THETA_BASE = 0.035
    THETA_WIND_REF = 15.0   # mph at which outdoor wind doubles shared variance
    THETA_DOME_FACTOR = 0.6

    # Situational elasticities.
    TEMP_REF_F = 70.0
    TEMP_ELASTICITY = 0.0025  # log-run change per degF (outdoor only)
    REST_ELASTICITY = 0.01

    QUAD_NODES = 24

    def __init__(self,
                 model_id: str = "codex_synth_mlb_inning_1_1108_050909",
                 version: str = "1.0.0",
                 league: str = "MLB",
                 scope: str = "inning_1",
                 family: str = "double_chance",
                 **kwargs: Any) -> None:
        super().__init__(model_id=model_id, version=version, league=league,
                         scope=scope, family=family, **kwargs)
        self._means, self._pmfs = _build_run_lattice(
            tuple(self.EVENT_MIX), self.DP_RATE, self.SF_RATE,
            self.RUN_CAP, self.GRID_POINTS)

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _f(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._f(ctx[k])
                if v is not None:
                    return v
        weather = ctx.get("weather")
        if isinstance(weather, dict):
            for k in keys:
                v = self._f(weather.get(k))
                if v is not None:
                    return v
        return None

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        v = self._get(ctx, f"{side}_{kind}")
        if v is None and isinstance(ctx.get(side), dict):
            v = self._f(ctx[side].get(kind))
        return v if (v is not None and v > 0.0) else 1.0

    def _is_dome(self, ctx: Dict[str, Any]) -> bool:
        d = ctx.get("is_dome")
        if d is None and isinstance(ctx.get("weather"), dict):
            d = ctx["weather"].get("is_dome")
        if d is None and isinstance(ctx.get("venue"), dict):
            d = ctx["venue"].get("is_dome")
        return bool(d) if d is not None else False

    # ------------------------------------------------------------------ #
    # Mean construction
    # ------------------------------------------------------------------ #
    def _side_mean(self, ctx: Dict[str, Any], side: str, anchor: float) -> float:
        opp = "away" if side == "home" else "home"
        expected = self._get(ctx, f"{side}_expected")
        rest_off = self._get(ctx, f"{side}_rest_days")
        rest_def = self._get(ctx, f"{opp}_rest_days")
        rest_mult = 1.0
        if rest_off is not None and rest_def is not None:
            rest_mult = math.exp(self.REST_ELASTICITY *
                                 (math.log1p(max(rest_off, 0.0)) -
                                  math.log1p(max(rest_def, 0.0))))

        if expected is not None and expected > 0.0:
            if expected > self.INNING_SCALE_CEILING:     # game-level expectation
                mu = anchor * expected / self.LEAGUE_TEAM_GAME_RUNS
            else:
                mu = expected
            return mu * rest_mult

        matchup = self._rating(ctx, side, "attack") * self._rating(ctx, opp, "defense")
        climate = 1.0
        temp = self._get(ctx, "temp", "temperature")
        if temp is not None and not self._is_dome(ctx):
            climate = math.exp(self.TEMP_ELASTICITY * (temp - self.TEMP_REF_F))
        return anchor * matchup * climate * rest_mult

    def _theta(self, ctx: Dict[str, Any]) -> float:
        if self._is_dome(ctx):
            return self.THETA_BASE * self.THETA_DOME_FACTOR
        wind = self._get(ctx, "wind_speed", "wind")
        wind = max(wind, 0.0) if wind is not None else 0.0
        return self.THETA_BASE * (1.0 + wind / self.THETA_WIND_REF)

    # ------------------------------------------------------------------ #
    # Distribution primitives
    # ------------------------------------------------------------------ #
    def _run_pmf(self, mu: float) -> np.ndarray:
        """Half-inning pmf with mean mu via monotone interpolation on the lattice."""
        means, pmfs = self._means, self._pmfs
        if mu <= means[0]:
            # Below lattice floor: thin the first lattice pmf toward zero runs.
            w = mu / means[0] if means[0] > 0 else 0.0
            pmf = w * pmfs[0]
            pmf[0] += 1.0 - w
            return pmf
        if mu >= means[-1]:
            return pmfs[-1].copy()
        j = int(np.searchsorted(means, mu))
        lo, hi = means[j - 1], means[j]
        w = (mu - lo) / (hi - lo)
        return (1.0 - w) * pmfs[j - 1] + w * pmfs[j]

    def _frailty_nodes(self, theta: float):
        if theta <= 1e-8:
            return np.array([1.0]), np.array([1.0])
        k = 1.0 / theta
        x, w = roots_genlaguerre(self.QUAD_NODES, k - 1.0)
        w = np.asarray(w, dtype=float)
        w = w / w.sum()
        return theta * np.asarray(x, dtype=float), w

    @staticmethod
    def _three_way(pmf_h: np.ndarray, pmf_a: np.ndarray) -> Tuple[float, float, float]:
        cdf_a = np.cumsum(pmf_a)
        cdf_h = np.cumsum(pmf_h)
        p_draw = float(pmf_h @ pmf_a)
        p_home = float(pmf_h[1:] @ cdf_a[:-1])
        p_away = float(pmf_a[1:] @ cdf_h[:-1])
        return p_home, p_draw, p_away

    def joint_three_way(self, mu_h: float, mu_a: float,
                        theta: float) -> Tuple[float, float, float]:
        z, w = self._frailty_nodes(theta)
        ph = pd = pa = 0.0
        for zi, wi in zip(z, w):
            h, d, a = self._three_way(self._run_pmf(mu_h * zi), self._run_pmf(mu_a * zi))
            ph += wi * h
            pd += wi * d
            pa += wi * a
        tot = ph + pd + pa
        return ph / tot, pd / tot, pa / tot

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mu_h = self._side_mean(ctx, "home", self.ANCHOR_HOME)
        mu_a = self._side_mean(ctx, "away", self.ANCHOR_AWAY)
        p_h, p_d, p_a = self.joint_three_way(mu_h, mu_a, self._theta(ctx))
        return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)

    def double_chance(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        """Convenience: exact double-chance leg probabilities."""
        ctx = game_context or {}
        mu_h = self._side_mean(ctx, "home", self.ANCHOR_HOME)
        mu_a = self._side_mean(ctx, "away", self.ANCHOR_AWAY)
        p_h, p_d, p_a = self.joint_three_way(mu_h, mu_a, self._theta(ctx))
        return {"1X": p_h + p_d, "X2": p_d + p_a, "12": p_h + p_a}