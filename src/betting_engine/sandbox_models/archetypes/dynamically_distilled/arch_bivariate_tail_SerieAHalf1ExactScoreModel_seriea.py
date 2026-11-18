"""
Candidate model for SERIEA | half_1 | exact_score.

How the model is built
----------------------
1. Base intensities. The first-half goal intensities (lambda_h, lambda_a) start
   from the supplied `home_expected` / `away_expected`. These are combined with
   the empirical segment moments (0.80 / 0.60) by precision-weighted Bayesian
   shrinkage in log space. Because this segment has no settled history, the
   prior acts as a regulariser and does not override the context. If only
   full-match expectations are supplied, they are converted using the
   first-half goal share.

2. Situational scaling. Each adjustment is a multiplicative log-linear effect
   that is centred so it is neutral at league-average inputs:
     * attack/defence exposure (relative strength ratios),
     * tempo / pace (a common multiplier on total intensity),
     * rest differential (bounded smoothly with tanh, so there is no hard clamp),
     * weather (wind and temperature, with no effect when `is_dome` is true).

3. Dispersion. A shared Gamma frailty W with E[W]=1 and Var[W]=v multiplies
   both intensities. Its variance comes from the dispersion index of totals:
       Var(T) = mu + v * mu^2   =>   v = (D - 1) / mu.
   When D = 1 (the observed prior), v = 0 and the model reduces exactly to
   independent Poisson. When D > 1, the shared frailty produces both
   overdispersion and positive goal correlation. The mixture is integrated
   with generalised Gauss-Laguerre quadrature.

4. Low-score dependence. A Dixon-Coles tau correction is applied to the four
   cells {0-0, 1-0, 0-1, 1-1}. rho is kept inside its analytically feasible
   interval, derived from lambda, so every cell stays non-negative. Its
   magnitude decays with total intensity, because the extra 0-0 / 1-1 mass is
   largest when the game is low-event.

5. Lattice truncation. The grid size is chosen adaptively from Poisson tail
   quantiles of the frailty-inflated intensities. The final matrix is
   renormalised to sum to one.

All hyper-parameters live in one overridable `PRIORS` dict. Each has a stated
domain rationale, and each can be replaced through the constructor or through
per-game context keys.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally in scope; fall back to an import.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: F821
except NameError:  # pragma: no cover
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class SerieAHalf1ExactScoreModel(BaseCandidateModel):
    """Frailty-mixed, Dixon-Coles-corrected Poisson lattice for 1H exact scores."""

    # Domain priors. Each entry is a documented hyper-parameter and can be
    # overridden through the constructor or the game context.
    PRIORS: Dict[str, float] = {
        # Empirical segment moments (first half, Serie A).
        "base_home": 0.80,
        "base_away": 0.60,
        # Observed dispersion index of 1H totals (Var/Mean).
        "dispersion_index": 1.000,
        # Share of Serie A goals scored before half-time (used only when
        # full-match expectations are supplied).
        "first_half_goal_share": 0.445,
        # Pseudo-observation weight of the segment prior relative to a
        # context estimate backed by `context_n` matches.
        "prior_pseudo_games": 6.0,
        "default_context_n": 30.0,
        # Elasticities of log-intensity w.r.t. log strength ratios.
        "attack_elasticity": 0.60,
        "defense_elasticity": 0.60,
        # Elasticity of total intensity w.r.t. log pace ratio.
        "pace_elasticity": 0.50,
        # Maximum asymptotic log-effect of rest differential (tanh bounded),
        # and the rest-day scale at which it saturates.
        "rest_max_log_effect": 0.06,
        "rest_scale_days": 3.0,
        # Weather: log-effect per m/s of wind above the reference, and per
        # degree C away from the comfort temperature.
        "wind_ref_ms": 4.0,
        "wind_log_slope": -0.008,
        "temp_comfort_c": 16.0,
        "temp_log_slope": -0.0025,
        # Dixon-Coles base dependence at the reference total intensity.
        "rho_base": -0.10,
        # Lattice tail tolerance and frailty quadrature order.
        "tail_eps": 1e-7,
        "quadrature_nodes": 24,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_1_1108_062455",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_1",
        family: str = "exact_score",
        priors: Optional[Dict[str, float]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            **kwargs,
        )
        self.priors: Dict[str, float] = dict(self.PRIORS)
        if priors:
            self.priors.update({k: float(v) for k, v in priors.items()})

    # ------------------------------------------------------------------ #
    # Generic helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        """Return x as a finite float, or None if it is missing or invalid."""
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        """Look up the first valid number among several (possibly dotted) keys."""
        for key in keys:
            node: Any = ctx
            ok = True
            for part in key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if ok:
                v = self._num(node)
                if v is not None:
                    return v
        return None

    def _p(self, ctx: Dict[str, Any], name: str) -> float:
        """Read a prior, letting the game context override it per game."""
        v = self._num(ctx.get(name)) if isinstance(ctx, dict) else None
        return v if v is not None else self.priors[name]

    @staticmethod
    def _safe_log_ratio(a: Optional[float], b: Optional[float]) -> float:
        """log(a/b) when both are positive; otherwise 0, i.e. neutral."""
        if a is None or b is None or a <= 0.0 or b <= 0.0:
            return 0.0
        return math.log(a / b)

    # ------------------------------------------------------------------ #
    # Stage 1: base intensities with Bayesian shrinkage
    # ------------------------------------------------------------------ #
    def _base_intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        base_h = self._p(ctx, "base_home")
        base_a = self._p(ctx, "base_away")

        h = self._get(ctx, "home_expected", "half_1_home_expected", "home_expected_h1")
        a = self._get(ctx, "away_expected", "half_1_away_expected", "away_expected_h1")

        # Convert full-match expectations if the context declares them, or if
        # only full-match fields are present.
        exp_scope = str(ctx.get("expected_scope", ctx.get("expectation_scope", "")) or "").lower()
        share = self._p(ctx, "first_half_goal_share")
        if exp_scope in ("full", "full_game", "match", "ft") and h is not None and a is not None:
            h, a = h * share, a * share
        if h is None or a is None:
            fh = self._get(ctx, "full_game_home_expected", "home_expected_ft")
            fa = self._get(ctx, "full_game_away_expected", "away_expected_ft")
            if h is None and fh is not None:
                h = fh * share
            if a is None and fa is not None:
                a = fa * share

        if h is None or h <= 0.0:
            h = base_h
        if a is None or a <= 0.0:
            a = base_a

        # Log-space precision weighting: w = n / (n + k).
        n_ctx = self._get(ctx, "context_n", "sample_size", "n_matches")
        n_ctx = n_ctx if (n_ctx is not None and n_ctx > 0) else self._p(ctx, "default_context_n")
        k = max(self._p(ctx, "prior_pseudo_games"), 0.0)
        w = n_ctx / (n_ctx + k) if (n_ctx + k) > 0 else 1.0

        lh = math.exp(w * math.log(h) + (1.0 - w) * math.log(base_h))
        la = math.exp(w * math.log(a) + (1.0 - w) * math.log(base_a))
        return lh, la

    # ------------------------------------------------------------------ #
    # Stage 2: situational log-linear adjustments
    # ------------------------------------------------------------------ #
    def _situational(self, ctx: Dict[str, Any], lh: float, la: float) -> Tuple[float, float]:
        ea = self._p(ctx, "attack_elasticity")
        ed = self._p(ctx, "defense_elasticity")

        # Strength ratios, read from either nested or flat keys.
        h_att = self._get(ctx, "home.attack", "home_attack")
        h_def = self._get(ctx, "home.defense", "home_defense")
        a_att = self._get(ctx, "away.attack", "away_attack")
        a_def = self._get(ctx, "away.defense", "away_defense")
        lg_att = self._get(ctx, "league_attack", "league.attack")
        lg_def = self._get(ctx, "league_defense", "league.defense")

        # Use the league average if given; otherwise the matchup mean, so the
        # adjustment changes relative strength without moving the total.
        ref_att = lg_att if lg_att is not None else (
            math.sqrt(h_att * a_att) if (h_att and a_att and h_att > 0 and a_att > 0) else None
        )
        ref_def = lg_def if lg_def is not None else (
            math.sqrt(h_def * a_def) if (h_def and a_def and h_def > 0 and a_def > 0) else None
        )

        # Defence is read as "goals conceded" strength: higher means leakier.
        log_h = ea * self._safe_log_ratio(h_att, ref_att) + ed * self._safe_log_ratio(a_def, ref_def)
        log_a = ea * self._safe_log_ratio(a_att, ref_att) + ed * self._safe_log_ratio(h_def, ref_def)

        # Tempo / pace acts on total intensity.
        pace = self._get(ctx, "pace", "tempo", "expected_pace")
        pace_ref = self._get(ctx, "league_pace", "pace_ref")
        if pace is None:
            hp = self._get(ctx, "home.pace", "home_pace", "home.tempo")
            ap = self._get(ctx, "away.pace", "away_pace", "away.tempo")
            if hp and ap and hp > 0 and ap > 0:
                pace = math.sqrt(hp * ap)
        log_pace = self._p(ctx, "pace_elasticity") * self._safe_log_ratio(pace, pace_ref) if pace_ref else 0.0
        log_h += log_pace
        log_a += log_pace

        # Rest differential, bounded smoothly. Fresher teams press more early.
        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        if hr is not None and ar is not None:
            m = self._p(ctx, "rest_max_log_effect")
            s = max(self._p(ctx, "rest_scale_days"), 1e-9)
            d = math.tanh((hr - ar) / s)
            log_h += 0.5 * m * d
            log_a -= 0.5 * m * d

        # Weather, which is neutral indoors.
        is_dome = bool(ctx.get("is_dome", False))
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        is_dome = is_dome or bool(weather.get("is_dome", False))
        if not is_dome:
            wind = self._get(ctx, "wind_speed", "weather.wind_speed")
            temp = self._get(ctx, "temp", "temperature", "weather.temp")
            log_w = 0.0
            if wind is not None:
                excess = max(wind - self._p(ctx, "wind_ref_ms"), 0.0)
                log_w += self._p(ctx, "wind_log_slope") * excess
            if temp is not None:
                log_w += self._p(ctx, "temp_log_slope") * abs(temp - self._p(ctx, "temp_comfort_c"))
            log_h += log_w
            log_a += log_w

        return lh * math.exp(log_h), la * math.exp(log_a)

    # ------------------------------------------------------------------ #
    # Stage 3-5: score lattice
    # ------------------------------------------------------------------ #
    def _frailty_nodes(self, v: float, n_nodes: int) -> Tuple[np.ndarray, np.ndarray]:
        """Quadrature nodes and weights for W ~ Gamma(shape=1/v, scale=v)."""
        if v <= 1e-10:
            return np.array([1.0]), np.array([1.0])
        shape = 1.0 / v
        x, w = special.roots_genlaguerre(int(n_nodes), shape - 1.0)
        w = np.asarray(w, dtype=float)
        w = w / w.sum()
        return np.asarray(x, dtype=float) * v, w

    def _grid_size(self, lh: float, la: float, v: float, eps: float) -> int:
        """Pick a lattice size large enough that the truncated tail is below eps."""
        # Inflate the means by the frailty's upper quantile so the mixture tail
        # is covered.
        if v > 1e-10:
            w_hi = stats.gamma.ppf(1.0 - eps, a=1.0 / v, scale=v)
        else:
            w_hi = 1.0
        m = max(lh, la) * max(w_hi, 1.0)
        return int(stats.poisson.ppf(1.0 - eps, m)) + 2

    def _rho(self, ctx: Dict[str, Any], lh: float, la: float) -> float:
        rho0 = self._p(ctx, "rho_base")
        mu_ref = self._p(ctx, "base_home") + self._p(ctx, "base_away")
        mu = lh + la
        # Low-score dependence shrinks as the game becomes more open.
        rho = rho0 * math.exp(-(mu - mu_ref) / max(mu_ref, 1e-9))
        # Feasible interval that keeps all tau-adjusted cells non-negative.
        lo = max(-1.0 / lh, -1.0 / la) if lh > 0 and la > 0 else 0.0
        hi = min(1.0 / (lh * la), 1.0) if lh > 0 and la > 0 else 0.0
        # Smooth projection into the open interval.
        mid, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
        if half <= 0:
            return 0.0
        return mid + half * math.tanh((rho - mid) / half) * (1.0 - 1e-9)

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        lh, la = self._base_intensities(ctx)
        lh, la = self._situational(ctx, lh, la)
        lh, la = max(lh, 1e-9), max(la, 1e-9)

        disp = self._get(ctx, "dispersion_index") or self._p(ctx, "dispersion_index")
        v = max(disp - 1.0, 0.0) / (lh + la)

        eps = self._p(ctx, "tail_eps")
        n = self._grid_size(lh, la, v, eps)
        goals = np.arange(n + 1)

        nodes, weights = self._frailty_nodes(v, int(self._p(ctx, "quadrature_nodes")))
        mat = np.zeros((n + 1, n + 1))
        for wn, ww in zip(nodes, weights):
            ph = stats.poisson.pmf(goals, lh * wn)
            pa = stats.poisson.pmf(goals, la * wn)
            mat += ww * np.outer(ph, pa)

        # Dixon-Coles low-score correction, using the marginal-mean intensities.
        rho = self._rho(ctx, lh, la)
        mat[0, 0] *= 1.0 - lh * la * rho
        mat[1, 0] *= 1.0 + la * rho
        mat[0, 1] *= 1.0 + lh * rho
        mat[1, 1] *= 1.0 - rho

        mat = np.clip(mat, 0.0, None)
        total = mat.sum()
        return mat / total if total > 0 else mat

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mat = self.score_matrix(game_context)
        scores_tuple = {
            (int(i), int(j)): float(mat[i, j])
            for i in range(mat.shape[0])
            for j in range(mat.shape[1])
            if mat[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores_tuple)
        except (TypeError, ValueError, KeyError, AttributeError):
            # Fall back to string keys if the engine expects "h-a" labels.
            scores_str = {f"{h}-{a}": p for (h, a), p in scores_tuple.items()}
            return DiscreteDistribution.from_scores(scores_str)