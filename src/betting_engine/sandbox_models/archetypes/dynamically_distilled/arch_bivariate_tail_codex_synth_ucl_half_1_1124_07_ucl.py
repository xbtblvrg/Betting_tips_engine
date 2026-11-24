import math
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - resolved from pre-loaded execution scope
    pass


_HALF_TOKENS = {"half_1", "1h", "h1", "first_half", "half1", "1st_half", "ht"}
_FULL_TOKENS = {"full", "full_game", "ft", "match", "game", "regulation", "90", "fulltime"}


def _finite(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


class codex_synth_ucl_half_1_1124_075022(BaseCandidateModel):
    """
    UCL first-half 1X2 model.

    Structure (first principles):
      1. Half-level scoring intensities lambda_H, lambda_A are anchored on the
         empirical UCL 1H baselines (0.80 / 0.60) and moved by situational
         log-multipliers (inputs, attack/defense exposure, tempo, rest, weather).
      2. Supplied strengths are partially pooled toward the baseline in log space
         (weight w < 1). This is the structural answer to the observed
         favourite/home overconfidence: input signals are noisy, so their
         log-ratio to the baseline is shrunk rather than taken at face value.
      3. If inputs are full-match expectations, they are mapped to 1H via the
         first-half goal fraction phi (UCL 1H is cagey: ~44-46% of goals).
      4. Marginals are Poisson when dispersion index <= 1 (as observed) and
         negative binomial when overdispersed.
      5. Low-score dependence via a Dixon-Coles tau correction (rho), restricted
         to its mathematically valid domain, plus a diagonal (draw) intensity
         multiplier capturing game-state stickiness at 0-0 / 1-1.
      6. 1X2 = sums over the lower/diagonal/upper triangles of the score lattice.

    Every structural constant is a prior hyperparameter set in __init__ and can
    be re-estimated by maximum likelihood on genuine 1H scorelines via fit().
    No bookmaker price is blended into the forecast.

    Note: the reported 500-bet autopsy reduces to N=3 settled bets; its moments
    are not used as fitted targets because doing so would fit noise.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ucl_half_1_1124_075022",
        version: str = "1.0.0",
        league: str = "UCL",
        scope: str = "half_1",
        family: str = "three_way",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Empirical UCL 1H anchors.
        self.baseline_home = 0.80
        self.baseline_away = 0.60

        # Prior hyperparameters (re-estimable by fit()).
        self.phi = 0.45                 # first-half share of full-match goals
        self.shrink_w = 0.80            # partial pooling of input log-ratios toward baseline
        self.rho = -0.08                # Dixon-Coles low-score dependence
        self.draw_infl = 0.06           # diagonal intensity multiplier (1 + draw_infl)
        self.dispersion_index = 1.0     # Var/Mean of 1H team goals (observed 1.000)

        # Situational sensitivities (log-scale effects).
        self.exposure_weight_no_expected = 1.0   # when only ratings are supplied
        self.exposure_weight_with_expected = 0.25  # avoid double counting with *_expected
        self.tempo_elasticity = 0.6
        self.rest_beta = 0.04
        self.rest_scale = 3.0
        self.wind_beta = 0.03
        self.wind_ref = 25.0            # km/h-scale reference before wind dampens scoring

        self.tail_mass = 1e-10

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _rating_to_log(v: Any) -> float:
        """Map a rating to a log-multiplier. [0.2, 5] -> multiplicative;
        (-1.6, 0.2) -> already a log offset; otherwise uninformative."""
        x = _finite(v)
        if x is None:
            return 0.0
        if 0.2 <= x <= 5.0:
            return math.log(x)
        if -1.6 < x < 0.2:
            return x
        return 0.0

    def _team_ratings(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        att = ctx.get(f"{side}_attack")
        dfn = ctx.get(f"{side}_defense", ctx.get(f"{side}_defence"))
        blk = ctx.get(side)
        if isinstance(blk, dict):
            att = blk.get("attack", att)
            dfn = blk.get("defense", blk.get("defence", dfn))
        # defense is interpreted as goals-conceded exposure (higher = leakier)
        return self._rating_to_log(att), self._rating_to_log(dfn)

    def _tempo_log(self, ctx: Dict[str, Any]) -> float:
        """Accepts tempo and/or pace; if both are present they are combined
        (never raises)."""
        vals = []
        for key in ("tempo", "pace"):
            v = _finite(ctx.get(key))
            if v is None or v <= 0:
                continue
            ref = _finite(ctx.get(f"{key}_reference", ctx.get(f"{key}_ref")))
            if ref is not None and ref > 0:
                vals.append(math.log(v / ref))
            elif 0.5 <= v <= 2.0:
                vals.append(math.log(v))
        if not vals:
            return 0.0
        return self.tempo_elasticity * float(np.mean(vals))

    def _rest_effects(self, ctx: Dict[str, Any]) -> float:
        rh = _finite(ctx.get("home_rest_days"))
        ra = _finite(ctx.get("away_rest_days"))
        if rh is None or ra is None:
            return 0.0
        return self.rest_beta * math.tanh((rh - ra) / self.rest_scale)

    def _weather_log(self, ctx: Dict[str, Any]) -> float:
        if bool(ctx.get("is_dome", False)):
            return 0.0
        wind = _finite(ctx.get("wind_speed"))
        if wind is None or wind <= self.wind_ref:
            return 0.0
        return -self.wind_beta * math.log(wind / self.wind_ref)

    def _inputs_are_full_match(
        self, ctx: Dict[str, Any], total_in: float, base_total: float, phi: float
    ) -> bool:
        ss = str(ctx.get("score_scope") or "").strip().lower()
        if ss in _HALF_TOKENS:
            return False
        if ss in _FULL_TOKENS:
            return True
        # Data-driven: which scale is the supplied total closer to (log distance)?
        d_half = abs(math.log(total_in / base_total))
        d_full = abs(math.log(total_in / (base_total / phi)))
        return d_full < d_half

    # ------------------------------------------------------------------ #
    # Intensities
    # ------------------------------------------------------------------ #
    def _intensities(
        self, ctx: Dict[str, Any], phi: float, shrink_w: float
    ) -> Tuple[float, float]:
        bh = _finite(ctx.get("baseline_home"))
        ba = _finite(ctx.get("baseline_away"))
        bh = bh if bh is not None and bh > 0 else self.baseline_home
        ba = ba if ba is not None and ba > 0 else self.baseline_away
        base_total = bh + ba

        he = _finite(ctx.get("home_expected"))
        ae = _finite(ctx.get("away_expected"))
        have_expected = he is not None and ae is not None and he > 0 and ae > 0

        log_h = math.log(bh)
        log_a = math.log(ba)

        if have_expected:
            if self._inputs_are_full_match(ctx, he + ae, base_total, phi):
                he, ae = he * phi, ae * phi
            log_h += shrink_w * (math.log(he) - math.log(bh))
            log_a += shrink_w * (math.log(ae) - math.log(ba))
            ew = self.exposure_weight_with_expected
        else:
            ew = self.exposure_weight_no_expected

        att_h, def_h = self._team_ratings(ctx, "home")
        att_a, def_a = self._team_ratings(ctx, "away")
        log_h += ew * shrink_w * (att_h + def_a)
        log_a += ew * shrink_w * (att_a + def_h)

        common = self._tempo_log(ctx) + self._weather_log(ctx)
        rest = self._rest_effects(ctx)
        log_h += common + rest
        log_a += common - rest

        return math.exp(log_h), math.exp(log_a)

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    def _marginal(self, lam: float, n: int, disp: float) -> np.ndarray:
        k = np.arange(n + 1)
        if disp > 1.0 + 1e-9:
            r = lam / (disp - 1.0)
            p = r / (r + lam)
            return stats.nbinom.pmf(k, r, p)
        return stats.poisson.pmf(k, lam)

    def _grid_size(self, lam: float, disp: float) -> int:
        q = 1.0 - self.tail_mass
        if disp > 1.0 + 1e-9:
            r = lam / (disp - 1.0)
            n = stats.nbinom.ppf(q, r, r / (r + lam))
        else:
            n = stats.poisson.ppf(q, lam)
        return int(max(6, n + 2)) if math.isfinite(n) else 15

    @staticmethod
    def _valid_rho(rho: float, lh: float, la: float) -> float:
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0, 1.0 / (lh * la))
        margin = 1e-6
        return min(max(rho, lo + margin), hi - margin)

    def _score_matrix(
        self, lh: float, la: float, rho: float, draw_infl: float, disp: float
    ) -> np.ndarray:
        n = max(self._grid_size(lh, disp), self._grid_size(la, disp))
        m = np.outer(self._marginal(lh, n, disp), self._marginal(la, n, disp))
        r = self._valid_rho(rho, lh, la)
        m[0, 0] *= 1.0 - lh * la * r
        m[0, 1] *= 1.0 + lh * r
        m[1, 0] *= 1.0 + la * r
        m[1, 1] *= 1.0 - r
        np.fill_diagonal(m, np.diag(m) * (1.0 + draw_infl))
        m = np.clip(m, 0.0, None)
        s = m.sum()
        return m / s if s > 0 else m

    @staticmethod
    def _three_way(m: np.ndarray) -> Tuple[float, float, float]:
        p_home = float(np.tril(m, -1).sum())
        p_draw = float(np.trace(m))
        p_away = float(np.triu(m, 1).sum())
        return p_home, p_draw, p_away

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, dict) else {}
        disp = _finite(ctx.get("dispersion_index"))
        disp = disp if disp is not None and disp > 0 else self.dispersion_index

        try:
            lh, la = self._intensities(ctx, self.phi, self.shrink_w)
            m = self._score_matrix(lh, la, self.rho, self.draw_infl, disp)
            p_h, p_d, p_a = self._three_way(m)
        except (ValueError, OverflowError, ZeroDivisionError, FloatingPointError):
            m = self._score_matrix(
                self.baseline_home, self.baseline_away, self.rho, self.draw_infl, 1.0
            )
            p_h, p_d, p_a = self._three_way(m)

        probs = np.array([p_h, p_d, p_a], dtype=float)
        if not np.all(np.isfinite(probs)) or probs.sum() <= 0:
            m = self._score_matrix(
                self.baseline_home, self.baseline_away, self.rho, self.draw_infl, 1.0
            )
            probs = np.array(self._three_way(m), dtype=float)
        probs = np.clip(probs, 0.0, None)
        probs /= probs.sum()

        return DiscreteDistribution(
            probabilities={
                "home": float(probs[0]),
                "draw": float(probs[1]),
                "away": float(probs[2]),
            }
        )

    # ------------------------------------------------------------------ #
    # Optional maximum-likelihood calibration on genuine 1H scorelines
    # ------------------------------------------------------------------ #
    def fit(self, samples: Iterable[Dict[str, Any]], *args: Any, **kwargs: Any):
        """
        samples: iterable of dicts with 'home_goals', 'away_goals' (first-half
        goals) and optional 'context' (a game_context dict). Estimates
        (phi, shrink_w, rho, draw_infl) by maximum likelihood over the full
        score lattice. Safe no-op on insufficient data.
        """
        data = []
        for s in samples or []:
            if not isinstance(s, dict):
                continue
            hg = _finite(s.get("home_goals"))
            ag = _finite(s.get("away_goals"))
            if hg is None or ag is None or hg < 0 or ag < 0:
                continue
            data.append((int(hg), int(ag), s.get("context") or {}))
        if len(data) < 30:
            return self

        disp = self.dispersion_index

        def nll(theta: np.ndarray) -> float:
            phi = _sigmoid(theta[0])
            w = _sigmoid(theta[1])
            rho = float(theta[2])
            dinf = math.exp(theta[3]) - 1.0
            total = 0.0
            for hg, ag, ctx in data:
                lh, la = self._intensities(ctx, phi, w)
                m = self._score_matrix(lh, la, rho, dinf, disp)
                p = m[hg, ag] if hg < m.shape[0] and ag < m.shape[1] else 0.0
                total -= math.log(max(p, 1e-300))
            return total

        x0 = np.array(
            [
                _logit(self.phi),
                _logit(self.shrink_w),
                self.rho,
                math.log1p(self.draw_infl),
            ]
        )
        bounds = [(-3.0, 3.0), (-4.0, 4.0), (-0.5, 0.5), (-0.5, 0.7)]
        try:
            res = optimize.minimize(nll, x0, method="L-BFGS-B", bounds=bounds)
            if res.success and np.all(np.isfinite(res.x)):
                self.phi = _sigmoid(res.x[0])
                self.shrink_w = _sigmoid(res.x[1])
                self.rho = float(res.x[2])
                self.draw_infl = math.exp(res.x[3]) - 1.0
        except (ValueError, FloatingPointError, OverflowError):
            pass
        return self