import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded into the execution scope.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover - resolved by the sandbox loader
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class UCLFullGameSpreadsModel(BaseCandidateModel):
    """
    UCL | full_game | spreads candidate model.

    Structure (derived from the empirical moments supplied for this segment)
    -----------------------------------------------------------------------
    Goals are counts produced by a latent scoring intensity that is itself
    uncertain. The observed dispersion index D = Var/Mean = 1.584 > 1 tells us
    a pure Poisson model under-states variance. For a Poisson count mixed with
    a mean-one Gamma frailty of variance v, Var = mu + v * mu^2, so

        v = (D - 1) / mu0,       mu0 = 1.60 + 1.20 (empirical anchor total).

    v is treated as a scale-free heterogeneity parameter, so the implied
    dispersion index grows naturally with the expected total of each game.

    The frailty variance is split into:
      * a SHARED component (game-state / tempo shock that lifts or suppresses
        both sides together). Shared frailty induces positive score
        correlation, concentrating mass on small margins and draws -- this is
        the "high modal concentration around margin" pathology.
      * an IDIOSYNCRATIC component per side (tail risk specific to each team),
        which produces differential tail dispersion between favourite and
        underdog.
    The split weight is the strength-balance ratio s = min(lam)/max(lam):
    balanced matches are dominated by common game-state; lopsided matches by
    team-specific tails.

    Calibration repair for the home-side cash leak
    ----------------------------------------------
    Realized PnL is concentrated on away bets (home $234 vs away $2268), i.e.
    home margins were systematically over-priced. The venue component of the
    log-intensity ratio, log(1.60/1.20), is shrunk by the Bayesian reliability
    weight 1/D (the share of variance explained by the Poisson signal),
    removing the fraction (1 - 1/D) of venue advantage. Totals are preserved.

    Situational inputs (attack/defense, pace, rest) enter log-linearly,
    weighted by w = (D - 1)/D, the share of variance attributable to
    between-game heterogeneity, so they can only explain what the data says
    is explainable. No probability caps or static clamps are applied.

    Note: a 500% realized ROI on 500 settled spread wagers is far outside any
    plausible edge in a liquid market and most likely reflects a stake/PnL
    accounting artifact upstream; it is NOT used as a fitting target here.
    """

    EMPIRICAL_HOME_MEAN = 1.60
    EMPIRICAL_AWAY_MEAN = 1.20
    EMPIRICAL_DISPERSION_INDEX = 1.584
    MIN_ELIGIBLE_ODDS = 1.40
    QUADRATURE_NODES = 48
    TAIL_SIGMAS = 10.0

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_full_game_1108_135830",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "full_game",
        family: str = "spreads",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        d = self.EMPIRICAL_DISPERSION_INDEX
        self._mu0 = self.EMPIRICAL_HOME_MEAN + self.EMPIRICAL_AWAY_MEAN
        self._frailty_var = max(d - 1.0, 0.0) / self._mu0
        self._signal_weight = 1.0 / d                  # Poisson reliability
        self._situational_weight = (d - 1.0) / d       # heterogeneity share
        self._venue_log_ratio = math.log(
            self.EMPIRICAL_HOME_MEAN / self.EMPIRICAL_AWAY_MEAN
        )

        self._last_matrix: Optional[np.ndarray] = None
        self._last_lambdas: Optional[Tuple[float, float]] = None

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            x = float(value)
        except (TypeError, ValueError):
            return None
        return x if math.isfinite(x) else None

    def _positive(self, value: Any) -> Optional[float]:
        x = self._num(value)
        return x if (x is not None and x > 0.0) else None

    def _team_factor(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict):
            val = self._positive(team.get(key))
            if val is not None:
                return val
        return self._positive(ctx.get(f"{side}_{key}"))

    # ------------------------------------------------------------------ #
    # Intensity construction
    # ------------------------------------------------------------------ #
    def _intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        lam_h = self._positive(ctx.get("home_expected")) or self.EMPIRICAL_HOME_MEAN
        lam_a = self._positive(ctx.get("away_expected")) or self.EMPIRICAL_AWAY_MEAN
        w = self._situational_weight

        # Attack (scoring factor, >1 stronger) x opponent defense (conceding
        # factor, >1 leakier). Exponent w prevents double counting with the
        # expected-goals inputs, which already embed most team strength.
        att_h = self._team_factor(ctx, "home", "attack")
        def_h = self._team_factor(ctx, "home", "defense")
        att_a = self._team_factor(ctx, "away", "attack")
        def_a = self._team_factor(ctx, "away", "defense")
        if att_h is not None and def_a is not None:
            lam_h *= (att_h * def_a) ** w
        if att_a is not None and def_h is not None:
            lam_a *= (att_a * def_h) ** w

        # Tempo / pace relative to league-neutral 1.0 scales both sides.
        pace = self._positive(ctx.get("pace")) or self._positive(ctx.get("tempo"))
        if pace is not None:
            lam_h *= pace ** w
            lam_a *= pace ** w

        mu = lam_h + lam_a

        # Rest differential: log-saturating recovery, elasticity w^2 / mu so the
        # effect shrinks in high-event games where rest matters proportionally less.
        r_h = self._num(ctx.get("home_rest_days"))
        r_a = self._num(ctx.get("away_rest_days"))
        if r_h is not None and r_a is not None and r_h >= 0.0 and r_a >= 0.0:
            elasticity = (w * w) / mu
            log_rest = math.log1p(r_h) - math.log1p(r_a)
            lam_h *= math.exp(0.5 * elasticity * log_rest)
            lam_a *= math.exp(-0.5 * elasticity * log_rest)

        # Venue over-confidence repair (preserves total intensity).
        neutral = bool(ctx.get("neutral_venue") or ctx.get("is_neutral"))
        venue = ctx.get("venue")
        if isinstance(venue, dict):
            neutral = neutral or bool(venue.get("neutral") or venue.get("is_neutral"))
        removal = 1.0 if neutral else (1.0 - self._signal_weight)
        half_shift = 0.5 * self._venue_log_ratio * removal
        total = lam_h + lam_a
        lam_h *= math.exp(-half_shift)
        lam_a *= math.exp(half_shift)
        rescale = total / (lam_h + lam_a)
        return lam_h * rescale, lam_a * rescale

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    @staticmethod
    def _count_pmf(grid: np.ndarray, mean: float, idio_var: float) -> np.ndarray:
        if idio_var <= 1e-12:
            return stats.poisson.pmf(grid, mean)
        size = 1.0 / idio_var
        return stats.nbinom.pmf(grid, size, size / (size + mean))

    def _score_matrix(self, lam_h: float, lam_a: float) -> np.ndarray:
        v = self._frailty_var
        balance = min(lam_h, lam_a) / max(lam_h, lam_a)
        v_shared = v * balance
        v_idio = v - v_shared

        lam_max = max(lam_h, lam_a)
        side_var = lam_max + lam_max * lam_max * v
        n_max = int(math.ceil(lam_max + self.TAIL_SIGMAS * math.sqrt(side_var))) + 4
        grid = np.arange(n_max + 1)

        if v_shared > 1e-12:
            shape = 1.0 / v_shared
            nodes, weights = special.roots_genlaguerre(self.QUADRATURE_NODES, shape - 1.0)
            weights = weights / weights.sum()
            z_values = nodes / shape
        else:
            z_values, weights = np.array([1.0]), np.array([1.0])

        matrix = np.zeros((n_max + 1, n_max + 1))
        for z, wt in zip(z_values, weights):
            ph = self._count_pmf(grid, lam_h * z, v_idio)
            pa = self._count_pmf(grid, lam_a * z, v_idio)
            matrix += wt * np.outer(ph, pa)

        matrix = np.clip(matrix, 0.0, None)
        return matrix / matrix.sum()

    # ------------------------------------------------------------------ #
    # Required interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a = self._intensities(ctx)
        matrix = self._score_matrix(lam_h, lam_a)
        self._last_matrix = matrix
        self._last_lambdas = (lam_h, lam_a)

        p_home = float(np.tril(matrix, -1).sum())
        p_draw = float(np.trace(matrix))
        p_away = float(np.triu(matrix, 1).sum())

        scores = {
            (int(i), int(j)): float(matrix[i, j])
            for i, j in zip(*np.nonzero(matrix > 1e-14))
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
        try:
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution(
                probabilities={"home": p_home, "draw": p_draw, "away": p_away}
            )

    # ------------------------------------------------------------------ #
    # Continuous handicap pricing (supports half, whole and quarter lines)
    # ------------------------------------------------------------------ #
    def margin_distribution(self, game_context: Optional[Dict[str, Any]] = None) -> Dict[int, float]:
        if game_context is not None or self._last_matrix is None:
            self.predict_distribution(game_context or {})
        m = self._last_matrix
        n = m.shape[0]
        out: Dict[int, float] = {}
        for k in range(-(n - 1), n):
            out[k] = float(np.trace(m, offset=-k))  # home - away = k
        return out

    @staticmethod
    def _split_line(line: float) -> Tuple[float, ...]:
        quarter = round(line * 4.0)
        if quarter % 2 != 0:  # quarter line -> two adjacent half-lines
            return ((quarter - 1) / 4.0, (quarter + 1) / 4.0)
        return (quarter / 4.0,)

    def price_handicap(
        self,
        line: float,
        side: str = "home",
        game_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """Win/push/lose probabilities and fair decimal odds for `side` +line."""
        margins = self.margin_distribution(game_context)
        sign = 1.0 if side == "home" else -1.0
        parts = self._split_line(float(line))
        win = push = lose = 0.0
        for sub in parts:
            for k, p in margins.items():
                adj = sign * k + sub
                if adj > 1e-9:
                    win += p
                elif adj < -1e-9:
                    lose += p
                else:
                    push += p
        frac = 1.0 / len(parts)
        win, push, lose = win * frac, push * frac, lose * frac
        fair = 1.0 + lose / win if win > 0.0 else float("inf")
        return {"win": win, "push": push, "lose": lose, "fair_odds": fair}

    def expected_value(
        self,
        line: float,
        side: str,
        decimal_odds: float,
        game_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        q = self.price_handicap(line, side, game_context)
        return q["win"] * (decimal_odds - 1.0) - q["lose"]

    def is_eligible(
        self,
        line: float,
        side: str,
        decimal_odds: float,
        game_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if decimal_odds < self.MIN_ELIGIBLE_ODDS:
            return False
        return self.expected_value(line, side, decimal_odds, game_context) > 0.0

    def kelly_fraction(
        self,
        line: float,
        side: str,
        decimal_odds: float,
        game_context: Optional[Dict[str, Any]] = None,
    ) -> float:
        q = self.price_handicap(line, side, game_context)
        b = decimal_odds - 1.0
        if b <= 0.0 or q["win"] <= 0.0:
            return 0.0
        f = (q["win"] * b - q["lose"]) / (b * (q["win"] + q["lose"]))
        return max(f, 0.0)