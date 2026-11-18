"""
Candidate model for WTA | period_2 (second set) | double_chance.

Cold-start notes:
  * There are zero settled bets in this segment, so nothing below has been
    validated out of sample. The ROI and Brier figures quoted in the brief are
    targets. This code is not evidence that it reaches them.
  * The coefficients in HYPER are explicit, configurable priors. They should be
    fit on held-out WTA second-set data before any capital is deployed.
    Coefficients whose sign is empirically ambiguous (for example momentum vs.
    loser rebound) default to 0.0.

Structure:
  1. Situational scaling of the per-player expected set-2 games, done in logit
     space so no hard clamps are needed.
  2. Inversion of the expected games into service-hold probabilities
     (h_H, h_A), using an exact set-level Markov chain over (g_H, g_A, server).
  3. Bayesian Beta update of the holds on observed set-1 service games, with
     prior strength scaled by rest days and pace.
  4. Exact set resolution. At 6-6 the tiebreak is played from point-level
     serve probabilities, obtained by inverting the game-from-points recursion.
  5. A set cannot be drawn, so P(draw) = 0, P(1X) = P(home wins set) and
     P(X2) = 1 - P(1X).
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq, least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # Normally provided by the engine's execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    pass


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    return math.log(p) - math.log1p(-p)


def _pos_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if (math.isfinite(v) and v > 0.0) else None


def _nonneg_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if (math.isfinite(v) and v >= 0.0) else None


class CandidateModel(BaseCandidateModel):
    # Empirical baseline second-set means for this segment.
    BASE_HOME_EXPECTED = 5.76
    BASE_AWAY_EXPECTED = 5.31

    # Structural bounds of a tiebreak set. These follow from the rules, not
    # from tuning: a set ends after at least 6 and at most 13 games.
    SET_MIN_GAMES = 6.0
    SET_MAX_GAMES = 13.0

    # Configurable priors. Fit these on held-out data.
    HYPER: Dict[str, float] = {
        # Weight of the attack/defense log-ratio on the logit of the game share.
        "share_strength_coef": 0.50,
        # Weight of the rest-day differential, log1p(rest_H) - log1p(rest_A),
        # on the logit of the game share.
        "share_rest_coef": 0.05,
        # Weight of log(pace) on the logit of the normalised set total.
        "total_pace_coef": 0.50,
        # Number of "sets' worth" of pre-match information behind the hold
        # prior. Prior pseudo-count = prior_sets * expected service games per
        # player in one set.
        "prior_sets": 4.0,
        # How rest inflates the prior strength. More rest means set-1
        # fatigue/form signals persist less, so shrink harder toward pre-match.
        "shrink_rest_coef": 0.25,
        # How deviation of pace from neutral inflates the prior strength.
        "shrink_pace_coef": 0.50,
        # Momentum (+) vs. loser rebound (-) logit shift per unit of set-1
        # game-margin share. The sign is ambiguous in the literature: 0.0
        # until fitted.
        "momentum_coef": 0.0,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_wta_period_2_1108_053504",
        version: str = "1.0.0",
        league: str = "WTA",
        scope: str = "period_2",
        family: str = "double_chance",
        hyper: Optional[Dict[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.hyper = dict(self.HYPER)
        if hyper:
            self.hyper.update(hyper)
        self._hold_cache: Dict[Tuple[float, float], Tuple[float, float]] = {}
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Point -> game -> tiebreak -> set primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _hold_from_point(p: float) -> float:
        """P(server holds) from P(server wins a point), with deuce handled exactly."""
        q = 1.0 - p
        pre_deuce = p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
        deuce = 20.0 * p ** 3 * q ** 3 * (p * p / (1.0 - 2.0 * p * q))
        return pre_deuce + deuce

    def _point_from_hold(self, h: float) -> float:
        lo, hi = 1e-9, 1.0 - 1e-9
        f_lo = self._hold_from_point(lo) - h
        f_hi = self._hold_from_point(hi) - h
        if f_lo >= 0.0:
            return lo
        if f_hi <= 0.0:
            return hi
        return brentq(lambda p: self._hold_from_point(p) - h, lo, hi, xtol=1e-12)

    @staticmethod
    def _tiebreak_home(p_h: float, p_a: float, home_first: bool) -> float:
        """P(home wins the tiebreak). p_h and p_a are each player's
        point-win probability on their own serve."""
        memo: Dict[Tuple[int, int], float] = {}

        # Once the score reaches 6-6, every subsequent pair of points has one
        # serve from each player, which gives a closed form.
        win_pair = p_h * (1.0 - p_a)
        lose_pair = (1.0 - p_h) * p_a
        denom = win_pair + lose_pair
        tied_late = win_pair / denom if denom > 0.0 else 0.5

        def home_serving(n: int) -> bool:
            if n == 0:
                return home_first
            first_block = ((n - 1) // 2) % 2 == 1
            return home_first if first_block else (not home_first)

        def f(a: int, b: int) -> float:
            if a >= 7 and a - b >= 2:
                return 1.0
            if b >= 7 and b - a >= 2:
                return 0.0
            if a == 6 and b == 6:
                return tied_late
            key = (a, b)
            if key in memo:
                return memo[key]
            p = p_h if home_serving(a + b) else (1.0 - p_a)
            val = p * f(a + 1, b) + (1.0 - p) * f(a, b + 1)
            memo[key] = val
            return val

        return f(0, 0)

    def _set_outcome(self, h_h: float, h_a: float, home_first: bool) -> Tuple[float, float, float]:
        """Exact set chain. Returns (P(home wins set), E[home games], E[away games])."""
        p_pt_h = self._point_from_hold(h_h)
        p_pt_a = self._point_from_hold(h_a)

        cur: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
        p_win = 0.0
        e_h = 0.0
        e_a = 0.0
        for k in range(12):
            nxt: Dict[Tuple[int, int], float] = {}
            home_serving = (k % 2 == 0) == home_first
            p_home_game = h_h if home_serving else (1.0 - h_a)
            for (gh, ga), p in cur.items():
                for (ngh, nga), q in (
                    ((gh + 1, ga), p_home_game),
                    ((gh, ga + 1), 1.0 - p_home_game),
                ):
                    mass = p * q
                    if mass == 0.0:
                        continue
                    if (ngh >= 6 or nga >= 6) and abs(ngh - nga) >= 2:
                        e_h += mass * ngh
                        e_a += mass * nga
                        if ngh > nga:
                            p_win += mass
                    else:
                        nxt[(ngh, nga)] = nxt.get((ngh, nga), 0.0) + mass
            cur = nxt

        # After 12 games only (6, 6) can remain. The tiebreak's first server is
        # whoever served first in the set, because 12 games is an even count.
        p66 = cur.get((6, 6), 0.0)
        if p66 > 0.0:
            p_tb = self._tiebreak_home(p_pt_h, p_pt_a, home_first)
            p_win += p66 * p_tb
            e_h += p66 * (6.0 + p_tb)
            e_a += p66 * (7.0 - p_tb)
        return p_win, e_h, e_a

    def _set_outcome_mixed(self, h_h: float, h_a: float, p_home_first: float) -> Tuple[float, float, float]:
        out = np.zeros(3)
        if p_home_first > 0.0:
            out += p_home_first * np.array(self._set_outcome(h_h, h_a, True))
        if p_home_first < 1.0:
            out += (1.0 - p_home_first) * np.array(self._set_outcome(h_h, h_a, False))
        return float(out[0]), float(out[1]), float(out[2])

    # ------------------------------------------------------------------ #
    # Inversion: expected games -> hold probabilities
    # ------------------------------------------------------------------ #
    def _solve_holds(self, mu_h: float, mu_a: float) -> Tuple[float, float]:
        key = (round(mu_h, 6), round(mu_a, 6))
        if key in self._hold_cache:
            return self._hold_cache[key]

        # Parameterisation: a sets the overall hold level, which drives the set
        # length; b sets the asymmetry, which drives the game share. The serve
        # order is unknown before the set, so average over it.
        def resid(x: np.ndarray) -> np.ndarray:
            h_h = _sigmoid(x[0] + x[1])
            h_a = _sigmoid(x[0] - x[1])
            _, e_h, e_a = self._set_outcome_mixed(h_h, h_a, 0.5)
            return np.array([e_h - mu_h, e_a - mu_a])

        share = mu_h / (mu_h + mu_a)
        x0 = np.array([_logit(0.65), 0.5 * _logit(share) * 4.0])
        sol = least_squares(resid, x0, method="trf", xtol=1e-10, ftol=1e-10)
        h_h = _sigmoid(sol.x[0] + sol.x[1])
        h_a = _sigmoid(sol.x[0] - sol.x[1])
        self._hold_cache[key] = (h_h, h_a)
        return h_h, h_a

    # ------------------------------------------------------------------ #
    # Situational scaling
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rating(ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = _pos_float(block.get(key))
            if v is not None:
                return v
        return _pos_float(ctx.get(f"{side}_{key}"))

    def _scaled_expectations(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        mu_h = _pos_float(ctx.get("home_expected")) or self.BASE_HOME_EXPECTED
        mu_a = _pos_float(ctx.get("away_expected")) or self.BASE_AWAY_EXPECTED
        diag: Dict[str, float] = {}

        total = mu_h + mu_a
        share_logit = _logit(mu_h / total)

        # Attack/defense: the log-ratio is scale-free, so the rating units cancel.
        ah, dh = self._rating(ctx, "home", "attack"), self._rating(ctx, "home", "defense")
        aa, da = self._rating(ctx, "away", "attack"), self._rating(ctx, "away", "defense")
        if None not in (ah, dh, aa, da):
            strength = (math.log(ah) - math.log(da)) - (math.log(aa) - math.log(dh))
            share_logit += self.hyper["share_strength_coef"] * strength
            diag["strength_logratio"] = strength

        rh = _nonneg_float(ctx.get("home_rest_days"))
        ra = _nonneg_float(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            rest_diff = math.log1p(rh) - math.log1p(ra)
            share_logit += self.hyper["share_rest_coef"] * rest_diff
            diag["rest_diff"] = rest_diff

        # Pace acts on set length through the logit of the normalised total, so
        # the result stays strictly inside the (6, 13) bounds without clamping.
        pace = _pos_float(ctx.get("pace")) or _pos_float(ctx.get("tempo"))
        span = self.SET_MAX_GAMES - self.SET_MIN_GAMES
        t = (total - self.SET_MIN_GAMES) / span
        if pace is not None and 0.0 < t < 1.0:
            t = _sigmoid(_logit(t) + self.hyper["total_pace_coef"] * math.log(pace))
            total = self.SET_MIN_GAMES + span * t
            diag["pace"] = pace

        share = _sigmoid(share_logit)
        return total * share, total * (1.0 - share), diag

    # ------------------------------------------------------------------ #
    # Set-1 conditioning
    # ------------------------------------------------------------------ #
    def _set1_info(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        g_h = _nonneg_float(ctx.get("set1_home_games"))
        g_a = _nonneg_float(ctx.get("set1_away_games"))
        if g_h is None or g_a is None or (g_h + g_a) <= 0:
            return None
        g_h, g_a = int(round(g_h)), int(round(g_a))
        n1 = g_h + g_a
        tiebreak = n1 == 13
        n_serv = 12 if tiebreak else n1

        first = ctx.get("set1_first_server")
        info: Dict[str, Any] = {"g_h": g_h, "g_a": g_a, "n1": n1}
        if first in ("home", "away"):
            home_first = first == "home"
            s_first = (n_serv + 1) // 2
            s_second = n_serv // 2
            s_h = s_first if home_first else s_second
            s_a = s_second if home_first else s_first
            # Game n1 (0-indexed) opens set 2. The service rotation continues,
            # and that also holds after a tiebreak.
            info["set2_home_first"] = (n1 % 2 == 0) == home_first
        else:
            s_h = s_a = n_serv / 2.0

        # Exclude the tiebreak "game" from the service-game accounting.
        tb_h = 1 if (tiebreak and g_h > g_a) else 0
        tb_a = 1 if (tiebreak and g_a > g_h) else 0
        gh_s, ga_s = g_h - tb_h, g_a - tb_a

        b_h = _nonneg_float(ctx.get("set1_home_breaks"))  # breaks won by home
        b_a = _nonneg_float(ctx.get("set1_away_breaks"))  # breaks won by away
        if b_h is None or b_a is None:
            # Identity: gh_s = (s_h - b_a) + b_h, so b_h - b_a = gh_s - s_h.
            # Fall back to the minimal-break solution. This understates the
            # break count in back-and-forth sets.
            d = gh_s - s_h
            b_h, b_a = (d, 0.0) if d >= 0 else (0.0, -d)
        info.update(
            {
                "serv_h": s_h,
                "serv_a": s_a,
                "held_h": max(s_h - b_a, 0.0),
                "held_a": max(s_a - b_h, 0.0),
            }
        )
        return info

    def _update_holds(
        self, h0_h: float, h0_a: float, mu_h: float, mu_a: float,
        info: Dict[str, Any], ctx: Dict[str, Any],
    ) -> Tuple[float, float, Dict[str, float]]:
        serv_per_player = 0.5 * (mu_h + mu_a)
        n0 = self.hyper["prior_sets"] * serv_per_player

        # Shrinkage strength grows with rest and with deviation from neutral pace.
        rh = _nonneg_float(ctx.get("home_rest_days")) or 0.0
        ra = _nonneg_float(ctx.get("away_rest_days")) or 0.0
        pace = _pos_float(ctx.get("pace")) or _pos_float(ctx.get("tempo")) or 1.0
        n0 *= math.exp(
            self.hyper["shrink_rest_coef"] * math.log1p(0.5 * (rh + ra))
            + self.hyper["shrink_pace_coef"] * abs(math.log(pace))
        )

        def post(h0: float, held: float, served: float) -> float:
            return (h0 * n0 + held) / (n0 + served)

        h_h = post(h0_h, info["held_h"], info["serv_h"])
        h_a = post(h0_a, info["held_a"], info["serv_a"])

        beta = self.hyper["momentum_coef"]
        if beta != 0.0:
            margin = (info["g_h"] - info["g_a"]) / info["n1"]
            w = info["n1"] / (info["n1"] + n0)
            h_h = _sigmoid(_logit(h_h) + beta * w * margin)
            h_a = _sigmoid(_logit(h_a) - beta * w * margin)
        return h_h, h_a, {"prior_pseudocount": n0}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mu_h, mu_a, diag = self._scaled_expectations(ctx)
        h0_h, h0_a = self._solve_holds(mu_h, mu_a)
        h_h, h_a = h0_h, h0_a

        p_home_first = 0.5
        explicit = ctx.get("set2_first_server")
        info = self._set1_info(ctx)
        if info is not None:
            h_h, h_a, upd = self._update_holds(h0_h, h0_a, mu_h, mu_a, info, ctx)
            diag.update(upd)
            if "set2_home_first" in info:
                p_home_first = 1.0 if info["set2_home_first"] else 0.0
        if explicit in ("home", "away"):
            p_home_first = 1.0 if explicit == "home" else 0.0

        p_home, e_h, e_a = self._set_outcome_mixed(h_h, h_a, p_home_first)
        p_away = 1.0 - p_home

        self.last_diagnostics = {
            **diag,
            "mu_home": mu_h,
            "mu_away": mu_a,
            "hold_home_prior": h0_h,
            "hold_away_prior": h0_a,
            "hold_home": h_h,
            "hold_away": h_a,
            "p_home_serves_first": p_home_first,
            "model_e_home_games": e_h,
            "model_e_away_games": e_a,
            "p_1X": p_home,
            "p_X2": p_away,
        }
        # A set cannot be drawn, so 1X is the home set win and X2 is the away
        # set win.
        return DiscreteDistribution.from_probabilities(p_home, 0.0, p_away)

    @staticmethod
    def expected_value(p_win: float, decimal_odds: float) -> float:
        """EV per unit stake. The engine decides eligibility (odds >= 1.40, EV > 0)."""
        return p_win * decimal_odds - 1.0