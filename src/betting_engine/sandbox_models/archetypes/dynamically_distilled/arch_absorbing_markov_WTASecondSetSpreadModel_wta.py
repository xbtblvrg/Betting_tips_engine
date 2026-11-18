"""
WTA | period_2 (second set) | spreads -- set-lattice Markov candidate model.

The model is built from how a tennis set is scored, not from Poisson goal counts.

  * Point -> game: the hold probability is the closed form of the deuce-game
    Markov chain, h(p) = p^4 (15 - 4p - 10p^2 / (1 - 2p(1-p))).
  * Game -> set: an exact forward pass over (games_A, games_B, server). It
    absorbs at 6-0 .. 6-4 and 7-5, and at 6-6 it hands off to a point-level
    tiebreak chain. The tiebreak follows the real serve pattern (1, 2, 2, ...)
    and uses an exact closed form once it reaches 6-6.
  * The second-set first server is derived from set-1 information when it is
    available. Otherwise the model averages both serve orders 50/50.
  * Serve-point probabilities (p_A, p_B) come from a MAP fit. The anchors
    (home_expected, away_expected) act as soft observations. A logit-normal
    prior centred on the WTA serve-point baseline, shifted by situational
    features, shrinks the estimates. Anchors that no tennis set can produce
    are therefore reconciled smoothly instead of being clamped.
  * The "home" label carries no structural edge. The hold function is the
    same for both players, with no home term anywhere.
  * Spread prices come straight from the discrete game-margin PMF, which only
    puts mass on achievable margins: +-1 (7-6), +-2 (7-5, 6-4), +-3 .. +-6.

Every numeric prior is exposed as a documented constructor argument, with its
domain meaning, so it can be re-estimated once settled history exists.
Expected out-of-sample ROI is not guaranteed. This is a cold-start market,
and the model must be validated on settled bets before staking.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # pragma: no cover - resolution depends on the host engine layout
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")


# --------------------------------------------------------------------------- #
# Pure tennis mechanics
# --------------------------------------------------------------------------- #
_EPS = 1e-9


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def _expit(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def hold_probability(p: float) -> float:
    """Closed-form probability that the server holds a standard deuce game."""
    p = min(max(p, _EPS), 1.0 - _EPS)
    q = 1.0 - p
    return p ** 4 * (15.0 - 4.0 * p - 10.0 * p * p / (1.0 - 2.0 * p * q))


def tiebreak_probability(p_a: float, p_b: float, a_serves_first: bool) -> float:
    """
    Probability that A wins a first-to-7, win-by-2 tiebreak.

    p_a: A's serve-point win probability. p_b: B's serve-point win probability.
    Point k (0-indexed) is served by the first server when ((k + 1) // 2) is
    even, which gives the 1-2-2-2 rotation.
    """
    x = p_a * (1.0 - p_b)                 # A wins a full two-point cycle
    y = (1.0 - p_a) * p_b                 # B wins a full two-point cycle
    deuce_win = x / (x + y) if (x + y) > 0 else 0.5
    memo: Dict[Tuple[int, int], float] = {}

    def f(i: int, j: int) -> float:
        if i >= 7 and i - j >= 2:
            return 1.0
        if j >= 7 and j - i >= 2:
            return 0.0
        if i >= 6 and i == j:
            # From 6-6 on, every two-point cycle has one serve each, so the
            # chain collapses to a gambler's-ruin closed form.
            return deuce_win
        key = (i, j)
        if key in memo:
            return memo[key]
        k = i + j
        first_serves = ((k + 1) // 2) % 2 == 0
        a_serving = first_serves == a_serves_first
        w = p_a if a_serving else 1.0 - p_b
        val = w * f(i + 1, j) + (1.0 - w) * f(i, j + 1)
        memo[key] = val
        return val

    return f(0, 0)


def _is_terminal(a: int, b: int) -> bool:
    if a == 7 or b == 7:
        return True
    return (a >= 6 or b >= 6) and abs(a - b) >= 2


def set_score_distribution(p_a: float, p_b: float,
                           a_serves_first: bool) -> Dict[Tuple[int, int], float]:
    """Exact distribution of final set scores (games_A, games_B)."""
    h_a = hold_probability(p_a)
    h_b = hold_probability(p_b)
    # The tiebreak's first server is the set's first server (game 13 is odd).
    tb_a = tiebreak_probability(p_a, p_b, a_serves_first)

    frontier: Dict[Tuple[int, int, bool], float] = {(0, 0, a_serves_first): 1.0}
    final: Dict[Tuple[int, int], float] = {}
    for _ in range(13):
        nxt: Dict[Tuple[int, int, bool], float] = {}
        for (a, b, a_srv), mass in frontier.items():
            if mass <= 0.0:
                continue
            if a == 6 and b == 6:
                final[(7, 6)] = final.get((7, 6), 0.0) + mass * tb_a
                final[(6, 7)] = final.get((6, 7), 0.0) + mass * (1.0 - tb_a)
                continue
            a_wins_game = h_a if a_srv else 1.0 - h_b
            for (na, nb), w in (((a + 1, b), a_wins_game),
                                ((a, b + 1), 1.0 - a_wins_game)):
                m = mass * w
                if _is_terminal(na, nb):
                    final[(na, nb)] = final.get((na, nb), 0.0) + m
                else:
                    key = (na, nb, not a_srv)
                    nxt[key] = nxt.get(key, 0.0) + m
        frontier = nxt
        if not frontier:
            break
    total = sum(final.values())
    return {k: v / total for k, v in final.items()} if total > 0 else final


# --------------------------------------------------------------------------- #
# Candidate model
# --------------------------------------------------------------------------- #
class WTASecondSetSpreadModel(BaseCandidateModel):
    """Second-set game-spread model for the WTA, built on the exact set lattice."""

    # Population anchors observed for this segment. They are used only when
    # the game context carries no anchors of its own.
    POP_HOME_EXPECTED = 5.76
    POP_AWAY_EXPECTED = 5.31

    def __init__(
        self,
        model_id: str = "codex_synth_wta_period_2_1107_161759",
        version: str = "1.0.0",
        league: str = "WTA",
        scope: str = "period_2",
        family: str = "spreads",
        # --- domain priors (documented, re-estimable) --------------------- #
        serve_point_baseline: float = 0.56,
        prior_logit_sd: float = 0.22,
        anchor_sd_games: float = 0.45,
        set1_loser_logit_shrink: float = 0.15,
        surface_logit_offsets: Optional[Dict[str, float]] = None,
        exposure_logit_beta: float = 0.20,
        rest_logit_beta: float = 0.03,
        indoor_logit_offset: float = 0.03,
        wind_logit_per_mps: float = -0.004,
        min_decimal_odds: float = 1.40,
    ) -> None:
        """
        Parameter meanings:

        serve_point_baseline: tour-wide WTA serve-point win rate.
        prior_logit_sd: cross-player spread of serve strength on the logit
            scale. It controls how hard the MAP fit shrinks toward baseline.
        anchor_sd_games: uncertainty of the incoming game-count anchors.
        set1_loser_logit_shrink: extra pull toward baseline for the players'
            difference. It captures set-1 losers adjusting and retirements
            truncating the set.
        surface_logit_offsets: serve dominance by surface relative to hard
            court.
        exposure_logit_beta: elasticity of serve strength with respect to
            attack/defense ratings.
        rest_logit_beta: fatigue effect per log(1 + rest-day) advantage.
        indoor_logit_offset, wind_logit_per_mps: ball-speed and conditions
            effects on serve.
        """
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.serve_point_baseline = serve_point_baseline
        self.prior_logit_sd = prior_logit_sd
        self.anchor_sd_games = anchor_sd_games
        self.set1_loser_logit_shrink = set1_loser_logit_shrink
        self.surface_logit_offsets = surface_logit_offsets or {
            "hard": 0.0, "clay": -0.06, "grass": 0.08, "carpet": 0.06,
            "indoor_hard": 0.04,
        }
        self.exposure_logit_beta = exposure_logit_beta
        self.rest_logit_beta = rest_logit_beta
        self.indoor_logit_offset = indoor_logit_offset
        self.wind_logit_per_mps = wind_logit_per_mps
        self.min_decimal_odds = min_decimal_odds

        self._last_fit: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        block = ctx.get(side)
        val = None
        if isinstance(block, dict):
            val = self._num(block.get(key))
        if val is None:
            val = self._num(ctx.get(f"{side}_{key}"))
        return val if (val is not None and val > 0) else 1.0

    def _prior_logits(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        base = _logit(self.serve_point_baseline)

        surface = str(ctx.get("surface") or (ctx.get("venue") or {}).get("surface", "")
                      if isinstance(ctx.get("venue"), dict) else ctx.get("surface") or "")
        surface = surface.strip().lower().replace(" ", "_")
        common = base + self.surface_logit_offsets.get(surface, 0.0)

        if bool(ctx.get("is_dome") or ctx.get("indoor")):
            common += self.indoor_logit_offset
        wind = self._num(ctx.get("wind_speed"), 0.0) or 0.0
        if not bool(ctx.get("is_dome") or ctx.get("indoor")):
            common += self.wind_logit_per_mps * max(wind, 0.0)

        # Serve strength rises with own attack and opponent's defensive
        # leakage (multiplicative rating convention, league average = 1).
        att_h, def_h = self._rating(ctx, "home", "attack"), self._rating(ctx, "home", "defense")
        att_a, def_a = self._rating(ctx, "away", "attack"), self._rating(ctx, "away", "defense")
        exp_h = self.exposure_logit_beta * (math.log(att_h) + math.log(def_a))
        exp_a = self.exposure_logit_beta * (math.log(att_a) + math.log(def_h))

        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        rest = 0.0
        if rh is not None and ra is not None:
            rest = self.rest_logit_beta * (math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0)))

        return common + exp_h + rest / 2.0, common + exp_a - rest / 2.0

    def _anchors(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._num(ctx.get("home_expected"))
        a = self._num(ctx.get("away_expected"))
        if h is None or h <= 0:
            h = self.POP_HOME_EXPECTED
        if a is None or a <= 0:
            a = self.POP_AWAY_EXPECTED
        return h, a

    @staticmethod
    def _home_serves_first_weight(ctx: Dict[str, Any]) -> float:
        """Probability that the home-labelled player serves first in set 2."""
        explicit = ctx.get("set2_first_server")
        if isinstance(explicit, str) and explicit.lower() in ("home", "away"):
            return 1.0 if explicit.lower() == "home" else 0.0
        s1_first = ctx.get("set1_first_server")
        s1_games = ctx.get("set1_total_games")
        if s1_games is None:
            hg, ag = ctx.get("set1_home_games"), ctx.get("set1_away_games")
            if hg is not None and ag is not None:
                try:
                    s1_games = int(hg) + int(ag)
                except (TypeError, ValueError):
                    s1_games = None
        if isinstance(s1_first, str) and s1_first.lower() in ("home", "away") and s1_games is not None:
            # The player who did not serve the last game of set 1 opens set 2.
            # An odd game total (a tiebreak counts as game 13) flips the order.
            home_first_s1 = s1_first.lower() == "home"
            flip = int(s1_games) % 2 == 1
            return 1.0 if (home_first_s1 != flip) else 0.0
        return 0.5

    # ------------------------------------------------------------------ #
    # Core distribution
    # ------------------------------------------------------------------ #
    def _mixture_scores(self, p_h: float, p_a: float, w_home_first: float
                        ) -> Dict[Tuple[int, int], float]:
        out: Dict[Tuple[int, int], float] = {}
        for first, w in ((True, w_home_first), (False, 1.0 - w_home_first)):
            if w <= 0.0:
                continue
            for k, v in set_score_distribution(p_h, p_a, first).items():
                out[k] = out.get(k, 0.0) + w * v
        return out

    @staticmethod
    def _moments(scores: Dict[Tuple[int, int], float]) -> Tuple[float, float]:
        eh = sum(h * p for (h, _), p in scores.items())
        ea = sum(a * p for (_, a), p in scores.items())
        return eh, ea

    def _fit_serve_probabilities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mu_h, mu_a = self._prior_logits(ctx)
        t_h, t_a = self._anchors(ctx)
        w_first = self._home_serves_first_weight(ctx)
        tau, sig = self.prior_logit_sd, self.anchor_sd_games

        def residuals(x: np.ndarray) -> np.ndarray:
            ph, pa = _expit(x[0]), _expit(x[1])
            eh, ea = self._moments(self._mixture_scores(ph, pa, w_first))
            return np.array([
                (eh - t_h) / sig,
                (ea - t_a) / sig,
                (x[0] - mu_h) / tau,
                (x[1] - mu_a) / tau,
            ])

        sol = least_squares(residuals, x0=np.array([mu_h, mu_a]), method="trf",
                            x_scale=tau, xtol=1e-10, ftol=1e-10, max_nfev=200)
        lh, la = float(sol.x[0]), float(sol.x[1])

        # Set-1-loser adjustment and retirement truncation: compress the
        # players' strength difference toward their common level.
        mid = 0.5 * (lh + la)
        k = 1.0 - self.set1_loser_logit_shrink
        lh, la = mid + k * (lh - mid), mid + k * (la - mid)

        ph, pa = _expit(lh), _expit(la)
        self._last_fit = {
            "p_home_serve": ph, "p_away_serve": pa,
            "hold_home": hold_probability(ph), "hold_away": hold_probability(pa),
            "anchor_home": t_h, "anchor_away": t_a,
            "home_serves_first_weight": w_first,
            "fit_cost": float(sol.cost),
        }
        return ph, pa

    def score_distribution(self, game_context: Dict[str, Any]) -> Dict[Tuple[int, int], float]:
        ph, pa = self._fit_serve_probabilities(game_context)
        scores = self._mixture_scores(ph, pa, self._home_serves_first_weight(game_context))
        eh, ea = self._moments(scores)
        self._last_fit.update({"model_expected_home": eh, "model_expected_away": ea})
        return scores

    @staticmethod
    def margin_pmf(scores: Dict[Tuple[int, int], float]) -> Dict[int, float]:
        pmf: Dict[int, float] = {}
        for (h, a), p in scores.items():
            pmf[h - a] = pmf.get(h - a, 0.0) + p
        return dict(sorted(pmf.items()))

    # ------------------------------------------------------------------ #
    # Engine interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        scores = self.score_distribution(game_context)

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_scores(
                [(h, a, p) for (h, a), p in scores.items()])
        except Exception:
            pass
        p_home = sum(p for (h, a), p in scores.items() if h > a)
        p_away = 1.0 - p_home
        try:
            return DiscreteDistribution.from_probabilities(p_home, 0.0, p_away)
        except Exception:
            return DiscreteDistribution(probabilities={"home": p_home, "draw": 0.0, "away": p_away})

    # ------------------------------------------------------------------ #
    # Spread pricing straight off the lattice PMF
    # ------------------------------------------------------------------ #
    def spread_probabilities(self, game_context: Dict[str, Any], home_line: float
                             ) -> Dict[str, float]:
        """
        The home side covers when (home_games - away_games) + home_line > 0.
        Returns the win, push and loss probabilities for the home side.
        """
        pmf = self.margin_pmf(self.score_distribution(game_context))
        win = sum(p for m, p in pmf.items() if m + home_line > 1e-12)
        push = sum(p for m, p in pmf.items() if abs(m + home_line) <= 1e-12)
        return {"home_cover": win, "push": push, "away_cover": 1.0 - win - push}

    def spread_expected_value(self, game_context: Dict[str, Any], home_line: float,
                              home_odds: float, away_odds: float) -> Dict[str, float]:
        pr = self.spread_probabilities(game_context, home_line)
        ev_home = pr["home_cover"] * (home_odds - 1.0) - pr["away_cover"]
        ev_away = pr["away_cover"] * (away_odds - 1.0) - pr["home_cover"]
        return {"home_ev": ev_home, "away_ev": ev_away, **pr}

    def recommend(self, game_context: Dict[str, Any], home_line: float,
                  home_odds: float, away_odds: float) -> List[Dict[str, Any]]:
        """Every side with odds >= min_decimal_odds and positive EV is eligible."""
        ev = self.spread_expected_value(game_context, home_line, home_odds, away_odds)
        picks: List[Dict[str, Any]] = []
        for side, odds, key, prob in (("home", home_odds, "home_ev", ev["home_cover"]),
                                      ("away", away_odds, "away_ev", ev["away_cover"])):
            if odds >= self.min_decimal_odds and ev[key] > 0.0:
                picks.append({"side": side, "line": home_line if side == "home" else -home_line,
                              "odds": odds, "prob": prob, "ev": ev[key]})
        return sorted(picks, key=lambda d: d["ev"], reverse=True)

    def diagnostics(self) -> Dict[str, Any]:
        return dict(self._last_fit)