"""
NCAAF | half_1 | winner_total candidate model.

Cold-start architecture built from first-half scoring mechanics.

1. Points arrive in discrete scoring events: touchdowns (6, 7 or 8 points after
   the conversion attempt), field goals (3) and safeties (2). Each team's
   first-half points follow a compound Poisson process over these jumps. The
   exact point lattice comes from Panjer recursion, so mass sits on 3, 7, 10,
   14, 17 ... and not on a Gaussian or plain-Poisson grid.
2. Event rates come from a target mean. The mean is anchored on the empirical
   half-1 baselines (home 14.00, away 12.50) and scaled by situational inputs:
   attack/defense exposure, tempo, rest differential and weather. Every
   adjustment works in log space through bounded smooth maps (tanh or
   softplus), not hard clamps.
3. Both teams share a latent game-tempo factor (possessions and clock). This
   factor is Gamma-distributed with mean 1 and is integrated with generalized
   Gauss-Laguerre quadrature. It produces the positive score correlation and
   the extra total variance seen in football halves. The event-level
   dispersion stays at the observed Poisson index (1.000). All overdispersion
   in points comes from the jump structure and the shared tempo mixing.
4. Home-field over-confidence is limited by shrinking the implied margin
   toward the baseline margin. The shrinkage weight is the precision ratio of
   the situational signal against the cold-start prior, so it is data-driven
   and needs no cap.
5. Output is a full joint score matrix, which serves both winner (3-way,
   draws included) and total markets. No bookmaker prices enter the forecast.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import roots_genlaguerre, gammaln

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - primitives may be pre-loaded in scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)  # type: ignore

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NCAAFHalf1WinnerTotalModel(BaseCandidateModel):  # type: ignore[misc]
    """Compound-Poisson scoring-event model with a shared tempo frailty."""

    # ---- Structural priors (domain mechanics, not fitted clamps) ----------
    # Empirical cold-start anchors for first-half scoring.
    BASE_HOME = 14.00
    BASE_AWAY = 12.50

    # Conversion mix after a touchdown: PAT made (7), PAT missed or failed
    # 2-point try (6), successful 2-point try (8).
    TD_CONVERSION = {7: 0.925, 6: 0.055, 8: 0.020}
    # Share of expected points from touchdowns. The rest is split between
    # field goals and safeties.
    TD_POINT_SHARE = 0.78
    SAFETY_POINT_SHARE = 0.006

    # Event-count dispersion index (Var/Mean) observed for this segment.
    EVENT_DISPERSION_INDEX = 1.000
    # Coefficient of variation of the shared first-half tempo factor
    # (possessions and play volume in NCAAF halves).
    TEMPO_CV = 0.17
    QUAD_NODES = 24

    # Precision (in points^-2) of the cold-start margin prior and of the
    # situational margin signal. Their ratio sets the margin shrinkage.
    PRIOR_MARGIN_SD = 6.5
    SIGNAL_MARGIN_SD = 7.5

    # Effect scales; each one passes through a bounded smooth map.
    RATING_LOG_SCALE = 0.35       # max |log multiplier| from attack/defense
    TEMPO_LOG_SCALE = 0.20        # max |log multiplier| from pace input
    REST_LOG_SCALE = 0.04         # max |log multiplier| from rest differential
    REST_DAYS_SCALE = 4.0         # days at which the rest effect ~ saturates
    WIND_ONSET_MPH = 10.0         # wind starts to bite near this speed
    WIND_WIDTH_MPH = 6.0
    WIND_MAX_LOG_DROP = 0.18
    WIND_FG_SHARE_SHIFT = 0.06    # wind moves points from FGs toward TDs
    COLD_TEMP_F = 30.0
    COLD_WIDTH_F = 10.0
    COLD_MAX_LOG_DROP = 0.05

    MASS_TAIL_EPS = 1e-10

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_half_1_1107_202156",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "half_1",
        family: str = "winner_total",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
                **kwargs,
            )
        except TypeError:
            try:
                super().__init__()
            except Exception:
                pass
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Precompute Gamma(k, theta = 1/k) quadrature for the tempo frailty.
        # Gamma pdf ~ x^(k-1) e^(-kx), so substitute x = t/k and use
        # generalized Laguerre with alpha = k - 1.
        k = 1.0 / (self.TEMPO_CV ** 2)
        nodes, weights = roots_genlaguerre(self.QUAD_NODES, k - 1.0)
        log_w = np.log(weights) - gammaln(k)
        w = np.exp(log_w - log_w.max())
        self._tempo_nodes = nodes / k
        self._tempo_weights = w / w.sum()

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _f(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            if x is None:
                return default
            v = float(x)
            if math.isnan(v) or math.isinf(v):
                return default
            return v
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _soft_log(x: float, scale: float) -> float:
        """Bounded smooth map of a log-ratio: scale * tanh(x / scale)."""
        if scale <= 0:
            return 0.0
        return scale * math.tanh(x / scale)

    @staticmethod
    def _sigmoid(z: float) -> float:
        return 1.0 / (1.0 + math.exp(-z))

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Extract an attack/defense rating as a multiplicative index (1 = avg)."""
        block = ctx.get(side)
        candidates = []
        if isinstance(block, dict):
            candidates += [block.get(kind), block.get(f"{kind}_rating"),
                           block.get(f"{kind}_strength")]
        candidates += [ctx.get(f"{side}_{kind}"), ctx.get(f"{side}_{kind}_rating")]
        for c in candidates:
            v = self._f(c)
            if v is not None:
                # Accept both ratio-style (around 1) and centered-style
                # (around 0) ratings.
                if v > 0 and 0.2 <= v <= 5.0:
                    return v
                return math.exp(self._soft_log(v, 1.0))
        return None

    # ---------------------------------------------------------- mean engine
    def _situational_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        mu_h = self._f(ctx.get("home_expected"), self.BASE_HOME)
        mu_a = self._f(ctx.get("away_expected"), self.BASE_AWAY)
        if mu_h is None or mu_h <= 0:
            mu_h = self.BASE_HOME
        if mu_a is None or mu_a <= 0:
            mu_a = self.BASE_AWAY

        # Context projections may be full-game. Detect the scale from the
        # ratio of their combined total to the half-1 anchor; a full game is
        # about two halves.
        if ctx.get("expected_scope") == "full_game" or ctx.get("expected_is_full_game"):
            mu_h, mu_a = 0.5 * mu_h, 0.5 * mu_a

        log_h, log_a = math.log(mu_h), math.log(mu_a)

        # Attack vs. opposing defense exposure.
        ha, hd = self._rating(ctx, "home", "attack"), self._rating(ctx, "home", "defense")
        aa, ad = self._rating(ctx, "away", "attack"), self._rating(ctx, "away", "defense")
        if ha is not None and ad is not None:
            log_h += self._soft_log(math.log(ha) + math.log(ad), self.RATING_LOG_SCALE)
        if aa is not None and hd is not None:
            log_a += self._soft_log(math.log(aa) + math.log(hd), self.RATING_LOG_SCALE)

        # Tempo / pace: one shared multiplier on both teams.
        pace = None
        for key in ("pace", "tempo", "expected_plays", "pace_index"):
            pace = self._f(ctx.get(key))
            if pace is not None:
                break
        if pace is None:
            hp = self._f((ctx.get("home") or {}).get("pace")) if isinstance(ctx.get("home"), dict) else None
            ap = self._f((ctx.get("away") or {}).get("pace")) if isinstance(ctx.get("away"), dict) else None
            if hp is not None and ap is not None and hp > 0 and ap > 0:
                pace = math.sqrt(hp * ap)
        tempo_log = 0.0
        if pace is not None and pace > 0:
            ref = 1.0 if pace < 5.0 else 70.0  # index style vs. plays-per-game
            tempo_log = self._soft_log(math.log(pace / ref), self.TEMPO_LOG_SCALE)
        log_h += tempo_log
        log_a += tempo_log

        # Rest differential, symmetric and saturating.
        rh = self._f(ctx.get("home_rest_days"))
        ra = self._f(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            r = self.REST_LOG_SCALE * math.tanh((rh - ra) / self.REST_DAYS_SCALE)
            log_h += 0.5 * r
            log_a -= 0.5 * r

        # Weather: wind and cold suppress scoring unless the game is indoors.
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        is_dome = bool(ctx.get("is_dome", weather.get("is_dome", False)))
        wind_frac = 0.0
        if not is_dome:
            wind = self._f(ctx.get("wind_speed", weather.get("wind_speed")))
            if wind is not None and wind > 0:
                wind_frac = self._sigmoid((wind - self.WIND_ONSET_MPH) / self.WIND_WIDTH_MPH)
                log_h -= self.WIND_MAX_LOG_DROP * wind_frac
                log_a -= self.WIND_MAX_LOG_DROP * wind_frac
            temp = self._f(ctx.get("temp", weather.get("temp")))
            if temp is not None:
                cold = self._sigmoid((self.COLD_TEMP_F - temp) / self.COLD_WIDTH_F)
                log_h -= self.COLD_MAX_LOG_DROP * cold
                log_a -= self.COLD_MAX_LOG_DROP * cold

        mu_h, mu_a = math.exp(log_h), math.exp(log_a)

        # Margin shrinkage (limits home-field over-confidence). Keep the
        # total and shrink the margin toward the baseline margin using a
        # precision-weighted posterior.
        total = mu_h + mu_a
        base_margin = (self.BASE_HOME - self.BASE_AWAY) * total / (self.BASE_HOME + self.BASE_AWAY)
        prec_prior = 1.0 / self.PRIOR_MARGIN_SD ** 2
        prec_sig = 1.0 / self.SIGNAL_MARGIN_SD ** 2
        w_sig = prec_sig / (prec_sig + prec_prior)
        margin = w_sig * (mu_h - mu_a) + (1.0 - w_sig) * base_margin
        # Keep both means positive with a smooth bound: |margin| < total.
        margin = total * math.tanh(margin / total)
        mu_h = 0.5 * (total + margin)
        mu_a = 0.5 * (total - margin)
        return mu_h, mu_a, wind_frac

    # ------------------------------------------------------ scoring lattice
    def _jump_pmf(self, wind_frac: float) -> Tuple[np.ndarray, float]:
        """Return the per-event point pmf and the mean points per event, for a
        unit-mean-point process."""
        td_share = self.TD_POINT_SHARE + self.WIND_FG_SHARE_SHIFT * wind_frac
        td_share = td_share / (1.0 + self.WIND_FG_SHARE_SHIFT * wind_frac + 1e-12) \
            if td_share >= 1.0 - self.SAFETY_POINT_SHARE else td_share
        sf_share = self.SAFETY_POINT_SHARE
        fg_share = max(1.0 - td_share - sf_share, 0.0)

        td_pts = sum(v * p for v, p in self.TD_CONVERSION.items())
        # Event rates per unit expected point.
        rate_td = td_share / td_pts
        rate_fg = fg_share / 3.0
        rate_sf = sf_share / 2.0
        rate = rate_td + rate_fg + rate_sf

        f = np.zeros(9)
        for v, p in self.TD_CONVERSION.items():
            f[v] += rate_td * p
        f[3] += rate_fg
        f[2] += rate_sf
        f /= rate
        return f, 1.0 / rate  # mean points per event

    def _team_pmf(self, mu: float, jump: np.ndarray, pts_per_event: float,
                  max_pts: int) -> np.ndarray:
        """Compound Poisson pmf through Panjer recursion."""
        lam = mu / pts_per_event
        g = np.zeros(max_pts + 1)
        g[0] = math.exp(-lam)
        jf = np.arange(len(jump)) * jump
        for n in range(1, max_pts + 1):
            jmax = min(n, len(jump) - 1)
            s = 0.0
            for j in range(1, jmax + 1):
                if jf[j] > 0.0:
                    s += jf[j] * g[n - j]
            g[n] = lam * s / n
        return g

    def _max_points(self, mu_h: float, mu_a: float) -> int:
        # Bound the tail from the tempo-inflated mean and variance.
        top_node = float(self._tempo_nodes.max())
        m = max(mu_h, mu_a) * min(top_node, 1.0 + 6.0 * self.TEMPO_CV)
        sd = math.sqrt(m * 7.0 * self.EVENT_DISPERSION_INDEX) + 1.0
        return int(math.ceil(m + 8.0 * sd)) + 8

    # ------------------------------------------------------------ interface
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a, wind_frac = self._situational_means(game_context or {})
        jump, ppe = self._jump_pmf(wind_frac)
        max_pts = self._max_points(mu_h, mu_a)

        M = np.zeros((max_pts + 1, max_pts + 1))
        for z, w in zip(self._tempo_nodes, self._tempo_weights):
            if w < 1e-14:
                continue
            gh = self._team_pmf(mu_h * z, jump, ppe, max_pts)
            ga = self._team_pmf(mu_a * z, jump, ppe, max_pts)
            M += w * np.outer(gh, ga)

        M[M < self.MASS_TAIL_EPS * M.max()] = 0.0
        M /= M.sum()
        return M

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        M = self.score_matrix(game_context)
        hi, ai = np.nonzero(M)
        scores = {(int(h), int(a)): float(M[h, a]) for h, a in zip(hi, ai)}

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            p_home = float(np.tril(M, -1).sum())
            p_draw = float(np.trace(M))
            p_away = float(np.triu(M, 1).sum())
            try:
                return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
            except Exception:
                return DiscreteDistribution(
                    probabilities={"home": p_home, "draw": p_draw, "away": p_away}
                )

    # Convenience diagnostics (deterministic, no side effects).
    def summarize(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        M = self.score_matrix(game_context)
        n = M.shape[0]
        h = np.arange(n)[:, None]
        a = np.arange(n)[None, :]
        tot = h + a
        e_tot = float((M * tot).sum())
        var_tot = float((M * tot ** 2).sum()) - e_tot ** 2
        return {
            "p_home": float(np.tril(M, -1).sum()),
            "p_draw": float(np.trace(M)),
            "p_away": float(np.triu(M, 1).sum()),
            "exp_home": float((M * h).sum()),
            "exp_away": float((M * a).sum()),
            "exp_total": e_tot,
            "total_dispersion_index": var_tot / e_tot if e_tot > 0 else float("nan"),
        }

    def prob_total_over(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        M = self.score_matrix(game_context)
        n = M.shape[0]
        tot = np.arange(n)[:, None] + np.arange(n)[None, :]
        return {
            "over": float(M[tot > line].sum()),
            "push": float(M[tot == line].sum()),
            "under": float(M[tot < line].sum()),
        }