import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class CodexSynthATPPeriod2TotalSets(BaseCandidateModel):
    """
    ATP | period_2 (second set) | total_sets (set game-count market).

    Structure
    ---------
    A second set is a finite Markov chain built from point-level serve dynamics:

        point p_serve  ->  game hold probability (closed form, with deuce)
                       ->  tiebreak win probability (exact DP with the 1-2-2 serve rotation)
                       ->  set score lattice {6-0 ... 6-4, 7-5, 7-6 and mirrors}

    Instead of fixing serve strengths, the model inverts the chain. It solves
    for the two serve-point probabilities (home, away) whose implied expected
    games-per-player in the set match the situational targets. The targets are
    the empirical anchors (5.76 / 5.31) rescaled by the context. This makes the
    total and the score-shape (tiebreak mass, blowout mass) a structural consequence
    of tennis scoring rather than a Poisson/NegBin approximation. A set game count is
    bounded in [6, 13] and strongly non-Poisson.

    Who serves first in set 2 depends on how set 1 ended (unknown pre-match), so
    the lattice averages over both server orders. When parity information is
    supplied ('set2_first_server' in {'home', 'away'}), the lattice uses it.

    Parameter uncertainty about serve strength is integrated with Gauss-Hermite
    quadrature on the logit scale. The noise scale is derived from the
    information content of the inputs and is not a fixed constant. This widens tails
    only when the inputs are uninformative, which addresses the tail-mispricing pathology.
    No bookmaker prices enter the forecast.
    """

    # Empirical anchors for this segment (observed moments, not tuning knobs).
    _BASE_HOME = 5.76
    _BASE_AWAY = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_atp_period_2_1107_153701",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "period_2",
        family: str = "total_sets",
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self._cache: Dict[Tuple, Dict[Tuple[int, int], float]] = {}

    # ------------------------------------------------------------------ #
    # Point -> game -> tiebreak -> set primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _hold(p: float) -> float:
        """Probability that the server holds a standard (ad-scoring) game."""
        q = 1.0 - p
        pre_deuce = p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
        deuce_reach = 20.0 * p ** 3 * q ** 3
        deuce_win = p * p / (p * p + q * q)
        return pre_deuce + deuce_reach * deuce_win

    @staticmethod
    def _tb_server(k: int) -> int:
        """Server of tiebreak point k (0 = player who serves first in the TB)."""
        return 0 if ((k + 1) // 2) % 2 == 0 else 1

    def _tiebreak(self, pa: float, pb: float) -> float:
        """
        P(A wins tiebreak), with A serving first.
        pa = P(A wins point on A serve), pb = P(B wins point on B serve).
        """
        win_on = (pa, 1.0 - pb)  # P(A wins point) when server is A / B
        dp = np.zeros((8, 8))
        dp[0, 0] = 1.0
        p_win = 0.0
        for total in range(0, 12):
            for i in range(0, min(total, 6) + 1):
                j = total - i
                if j > 6 or j < 0:
                    continue
                m = dp[i, j]
                if m == 0.0:
                    continue
                w = win_on[self._tb_server(total)]
                ni, nj = i + 1, j + 1
                if ni == 7 and j <= 5:
                    p_win += m * w
                else:
                    dp[ni, j] += m * w
                if nj == 7 and i <= 5:
                    pass
                else:
                    dp[i, nj] += m * (1.0 - w)
        reach66 = dp[6, 6]
        # From 6-6 each two-point cycle contains exactly one serve by each player.
        w2 = win_on[0] * win_on[1]
        l2 = (1.0 - win_on[0]) * (1.0 - win_on[1])
        p_from66 = w2 / (w2 + l2) if (w2 + l2) > 0 else 0.5
        return p_win + reach66 * p_from66

    def _set_lattice(
        self, p_h: float, p_a: float, home_serves_first: bool
    ) -> Dict[Tuple[int, int], float]:
        """Exact distribution of the final set score (home games, away games)."""
        key = (round(p_h, 10), round(p_a, 10), home_serves_first)
        if key in self._cache:
            return self._cache[key]

        hold_h = self._hold(p_h)
        hold_a = self._hold(p_a)

        dp = np.zeros((8, 8))
        dp[0, 0] = 1.0
        out: Dict[Tuple[int, int], float] = {}

        def terminal(h: int, a: int) -> bool:
            if (h >= 6 or a >= 6) and abs(h - a) >= 2:
                return True
            return h == 7 or a == 7

        for total in range(0, 12):
            for h in range(0, total + 1):
                a = total - h
                if h > 7 or a > 7:
                    continue
                m = dp[h, a]
                if m == 0.0 or terminal(h, a):
                    continue
                home_serving = (total % 2 == 0) == home_serves_first
                p_home_game = hold_h if home_serving else 1.0 - hold_a
                for nh, na, pr in ((h + 1, a, p_home_game), (h, a + 1, 1.0 - p_home_game)):
                    if terminal(nh, na):
                        out[(nh, na)] = out.get((nh, na), 0.0) + m * pr
                    else:
                        dp[nh, na] += m * pr

        # 6-6 -> tiebreak; game 13's server is the set's first server.
        reach66 = dp[6, 6]
        if reach66 > 0.0:
            if home_serves_first:
                p_tb_home = self._tiebreak(p_h, p_a)
            else:
                p_tb_home = 1.0 - self._tiebreak(p_a, p_h)
            out[(7, 6)] = out.get((7, 6), 0.0) + reach66 * p_tb_home
            out[(6, 7)] = out.get((6, 7), 0.0) + reach66 * (1.0 - p_tb_home)

        s = sum(out.values())
        out = {k: v / s for k, v in out.items()}
        self._cache[key] = out
        return out

    def _mixed_lattice(
        self, p_h: float, p_a: float, first: Optional[str]
    ) -> Dict[Tuple[int, int], float]:
        if first == "home":
            return self._set_lattice(p_h, p_a, True)
        if first == "away":
            return self._set_lattice(p_h, p_a, False)
        a = self._set_lattice(p_h, p_a, True)
        b = self._set_lattice(p_h, p_a, False)
        keys = set(a) | set(b)
        return {k: 0.5 * a.get(k, 0.0) + 0.5 * b.get(k, 0.0) for k in keys}

    @staticmethod
    def _moments(lat: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
        eh = sum(h * p for (h, _), p in lat.items())
        ea = sum(a * p for (_, a), p in lat.items())
        return eh, ea

    # ------------------------------------------------------------------ #
    # Situational target construction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _side_strength(self, side: Any, key: str) -> Optional[float]:
        if isinstance(side, dict):
            v = self._num(side.get(key))
            if v is not None and v > 0:
                return v
        return None

    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        """
        Returns (target_home_games, target_away_games, information weight in [0,1]).
        The information weight measures how many independent situational signals
        were observed. It drives the parameter-uncertainty width.
        """
        th = self._num(ctx.get("home_expected"))
        ta = self._num(ctx.get("away_expected"))
        signals = 0
        n_possible = 3

        if th is None or th <= 0:
            th = self._BASE_HOME
        else:
            signals += 1
        if ta is None or ta <= 0:
            ta = self._BASE_AWAY

        # Attack/defense exposure: log-symmetric matchup ratio on game share.
        h_att = self._side_strength(ctx.get("home"), "attack")
        h_def = self._side_strength(ctx.get("home"), "defense")
        a_att = self._side_strength(ctx.get("away"), "attack")
        a_def = self._side_strength(ctx.get("away"), "defense")
        if None not in (h_att, h_def, a_att, a_def):
            signals += 1
            edge = 0.5 * (math.log(h_att / a_def) - math.log(a_att / h_def))
            # Shift game share on the logit scale, preserving the total.
            tot = th + ta
            share = th / tot
            share = 1.0 / (1.0 + math.exp(-(math.log(share / (1.0 - share)) + edge)))
            th, ta = tot * share, tot * (1.0 - share)

        # Rest differential: relative recovery 1/(1+d) as the fatigue proxy.
        hr = self._num(ctx.get("home_rest_days"))
        ar = self._num(ctx.get("away_rest_days"))
        if hr is not None and ar is not None and hr >= 0 and ar >= 0:
            signals += 1
            fh, fa = 1.0 / (1.0 + hr), 1.0 / (1.0 + ar)
            # Fatigue asymmetry, scaled by its own magnitude (bounded in (-1, 1)).
            asym = (fa - fh) / (fa + fh) if (fa + fh) > 0 else 0.0
            tot = th + ta
            share = th / tot
            logit = math.log(share / (1.0 - share)) + asym * share * (1.0 - share)
            share = 1.0 / (1.0 + math.exp(-logit))
            th, ta = tot * share, tot * (1.0 - share)

        return th, ta, signals / n_possible

    # ------------------------------------------------------------------ #
    # Inversion: targets -> serve point probabilities
    # ------------------------------------------------------------------ #
    def _invert(self, th: float, ta: float, first: Optional[str]) -> Tuple[float, float]:
        def to_p(z: float) -> float:
            return 1.0 / (1.0 + math.exp(-z))

        def resid(z: np.ndarray) -> np.ndarray:
            lat = self._mixed_lattice(to_p(z[0]), to_p(z[1]), first)
            eh, ea = self._moments(lat)
            return np.array([eh - th, ea - ta])

        # Start from the symmetric anchor implied by the empirical baseline ratio.
        base_share = self._BASE_HOME / (self._BASE_HOME + self._BASE_AWAY)
        z0 = np.array([math.log(base_share / (1 - base_share)) + 0.5,
                       0.5])
        sol = least_squares(resid, z0, method="trf", xtol=1e-10, ftol=1e-10)
        return to_p(sol.x[0]), to_p(sol.x[1])

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        ctx = game_context or {}
        first = ctx.get("set2_first_server")
        first = first if first in ("home", "away") else None

        th, ta, info = self._targets(ctx)
        p_h, p_a = self._invert(th, ta, first)

        # Parameter uncertainty on the logit serve scale. The width equals the
        # sampling s.e. of a serve-point rate estimated from one set's worth of
        # service points (~ games * points/game). It is shrunk by the information weight.
        zh = math.log(p_h / (1 - p_h))
        za = math.log(p_a / (1 - p_a))
        hold_h, hold_a = self._hold(p_h), self._hold(p_a)
        exp_pts_per_game = 4.0 + 2.0 * (1 - hold_h * hold_a)  # deuce-length proxy
        n_srv = 0.5 * (th + ta) * exp_pts_per_game
        sd_h = (1.0 - info) / math.sqrt(max(n_srv * p_h * (1 - p_h), 1e-9))
        sd_a = (1.0 - info) / math.sqrt(max(n_srv * p_a * (1 - p_a), 1e-9))

        nodes, weights = np.polynomial.hermite_e.hermegauss(5)
        weights = weights / weights.sum()

        scores: Dict[Tuple[int, int], float] = {}
        if sd_h < 1e-6 and sd_a < 1e-6:
            scores = dict(self._mixed_lattice(p_h, p_a, first))
        else:
            for xi, wi in zip(nodes, weights):
                for xj, wj in zip(nodes, weights):
                    ph = 1.0 / (1.0 + math.exp(-(zh + sd_h * xi)))
                    pa = 1.0 / (1.0 + math.exp(-(za + sd_a * xj)))
                    lat = self._mixed_lattice(ph, pa, first)
                    w = wi * wj
                    for k, v in lat.items():
                        scores[k] = scores.get(k, 0.0) + w * v

        s = sum(scores.values())
        scores = {k: v / s for k, v in scores.items() if v > 0}

        try:
            return DiscreteDistribution.from_scores(scores)
        except TypeError:
            # Fallback encoding: list of (home, away, prob) tuples.
            return DiscreteDistribution.from_scores(
                [(h, a, p) for (h, a), p in sorted(scores.items())]
            )