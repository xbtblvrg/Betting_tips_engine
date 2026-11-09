"""
NCAAW | quarter_4 | totals candidate model.

The Q4 total is modeled as a compound possession process mixed over game-state
regimes. Each team scores T = sum_{i=1..N} X_i, where:
  * N is the number of possessions. Its mean comes from tempo, and it is
    under-dispersed relative to Poisson because the clock is fixed and
    possessions alternate. Both teams share a common possession driver, so
    their totals are positively correlated.
  * X_i takes values in {0, 1, 2, 3}. The mix (free-throw, 2PT, 3PT) depends on
    the regime. The scoring rate is solved so that the regime mixture
    reproduces the anchored team expectation exactly.

Regimes come from a Brownian margin process. The spread comes from the anchors,
and the variance scales with time elapsed.
  * CLOSE: |margin| is small with about 2 minutes left and the game was not a
    blowout entering Q4. Intentional fouling and the bonus add possessions and
    raise the free-throw share.
  * BLOWOUT: |margin| is large entering Q4. Bench units and clock-milking cut
    possessions and efficiency.
  * NEUTRAL: everything else.

The dispersion is not fixed at 1.0. It emerges from
Var(T)/E[T] = E[X^2]/E[X] + E[X] * Var(N)/E[N], combined with the regime mixture.

The domain priors below are structural rather than fitted clamps. Each one is a
physical or rule-based quantity of the women's college game: 10-minute
quarters, 30-second shot clock, possessions per 40 minutes, the
foul-game possession window, and typical scoring mixes. They are kept in one
place so they can be recalibrated as settled data accrues.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # normally pre-loaded in the execution scope
    DiscreteDistribution  # type: ignore  # noqa: F821
except NameError:  # pragma: no cover
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class NCAAWQuarter4TotalsModel(BaseCandidateModel):
    # ---- Structural domain priors (rule/physics based, recalibratable) ----
    DOMAIN = {
        # Empirical segment anchors (Q4 points per team)
        "anchor_home": 20.00,
        "anchor_away": 18.75,
        # Game clock
        "game_minutes": 40.0,
        "quarter_minutes": 10.0,
        "foul_window_minutes": 2.0,
        # League tempo (possessions per team per 40 minutes)
        "league_pace": 70.0,
        # Full-game margin standard deviation (NCAAW, points)
        "margin_sd_full": 12.5,
        # "Close" means within about three possessions with 2 minutes left.
        # Fouling is rational in this window.
        "close_margin": 8.0,
        # A margin entering Q4 at which coaches empty the bench
        "blowout_margin": 20.0,
        # Possession-count variance relative to its mean (clock-constrained, < 1)
        "possession_dispersion": 0.35,
        # Extra possessions in the foul game, as a fraction of the Q4 baseline
        "foul_game_possession_lift": 0.14,
        # Lost possessions in garbage time (milking the 30-second clock)
        "blowout_possession_drag": 0.09,
        # Conditional scoring-possession mixes (FT trip, 2PT, 3PT) by regime
        "mix_neutral": (0.20, 0.55, 0.25),
        "mix_close": (0.38, 0.40, 0.22),
        "mix_blowout": (0.17, 0.60, 0.23),
        # Relative efficiency of bench lineups in blowouts
        "blowout_efficiency": 0.94,
        # Rest: efficiency elasticity w.r.t. log(rest days), saturating
        "rest_elasticity": 0.012,
        # A threshold above which inputs are treated as full-game points.
        # Q4 points for one team cannot plausibly exceed 2x the anchor.
        "full_game_detect_mult": 2.0,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_4_1107_215356",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_4",
        family: str = "totals",
    ):
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:  # tolerate base classes with positional-only signatures
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _f(ctx: Dict[str, Any], *keys: str, default: Optional[float] = None) -> Optional[float]:
        for k in keys:
            v = ctx.get(k)
            if v is None:
                continue
            try:
                fv = float(v)
                if math.isfinite(fv):
                    return fv
            except (TypeError, ValueError):
                continue
        return default

    @staticmethod
    def _rating(block: Any, *keys: str) -> Optional[float]:
        if not isinstance(block, dict):
            return None
        for k in keys:
            v = block.get(k)
            try:
                fv = float(v)
                if math.isfinite(fv) and fv > 0:
                    return fv
            except (TypeError, ValueError):
                continue
        return None

    def _quarter_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float, float]:
        """Return (mu_home_q4, mu_away_q4, full_game_spread, pace_ratio)."""
        D = self.DOMAIN
        mh = self._f(ctx, "home_expected", default=D["anchor_home"])
        ma = self._f(ctx, "away_expected", default=D["anchor_away"])
        mh = mh if mh and mh > 0 else D["anchor_home"]
        ma = ma if ma and ma > 0 else D["anchor_away"]

        q_share = D["quarter_minutes"] / D["game_minutes"]
        anchor_total = D["anchor_home"] + D["anchor_away"]
        if (mh + ma) > D["full_game_detect_mult"] * anchor_total:
            # Inputs are full-game points. Project the Q4 share, scaled to the
            # segment's empirical Q4 level relative to a flat quarter.
            full_h, full_a = mh, ma
            mh, ma = mh * q_share, ma * q_share
        else:
            full_h, full_a = mh / q_share, ma / q_share

        # The full-game spread drives the game-state regimes.
        spread = self._f(ctx, "full_game_spread", "home_margin_expected", default=None)
        if spread is None:
            sp_line = self._f(ctx, "spread", "spread_line", default=None)
            # Bookmaker convention: a negative home line means home is favored.
            spread = -sp_line if sp_line is not None else (full_h - full_a)

        # Tempo relative to league
        pace = self._f(ctx, "pace", "tempo", "expected_pace", default=None)
        if pace is None:
            hp = self._rating(ctx.get("home"), "pace", "tempo")
            ap = self._rating(ctx.get("away"), "pace", "tempo")
            pace = (hp + ap) / 2.0 if (hp and ap) else (hp or ap)
        pace_ratio = (pace / D["league_pace"]) if pace else 1.0

        # Attack/defense exposure. The anchors already embed the matchup, so
        # ratings shift them only through a geometric-mean residual, which
        # damps double counting.
        h_blk, a_blk = ctx.get("home"), ctx.get("away")
        h_att = self._rating(h_blk, "attack", "offense", "off_rating")
        h_def = self._rating(h_blk, "defense", "def_rating")
        a_att = self._rating(a_blk, "attack", "offense", "off_rating")
        a_def = self._rating(a_blk, "defense", "def_rating")
        if h_att and a_def:
            mh *= math.sqrt(self._norm_rating(h_att) * self._norm_rating(a_def))
        if a_att and h_def:
            ma *= math.sqrt(self._norm_rating(a_att) * self._norm_rating(h_def))

        # Rest: saturating log effect relative to the opponent
        hr = self._f(ctx, "home_rest_days", default=None)
        ar = self._f(ctx, "away_rest_days", default=None)
        if hr is not None and ar is not None:
            e = D["rest_elasticity"]
            mh *= math.exp(e * (math.log1p(max(hr, 0.0)) - math.log1p(max(ar, 0.0))))
            ma *= math.exp(e * (math.log1p(max(ar, 0.0)) - math.log1p(max(hr, 0.0))))
            # Late-season fatigue hits Q4 legs for both teams
            mean_rest = 0.5 * (hr + ar)
            mh *= math.exp(-e * max(0.0, math.log(2.0) - math.log1p(mean_rest)))
            ma *= math.exp(-e * max(0.0, math.log(2.0) - math.log1p(mean_rest)))

        return mh, ma, float(spread), float(pace_ratio)

    @staticmethod
    def _norm_rating(r: float) -> float:
        # Ratings may be multiplicative (~1.0) or per-100 efficiency (~100).
        return r / 100.0 if r > 5.0 else r

    # ------------------------------------------------------------------ #
    # Regime probabilities from Brownian margin
    # ------------------------------------------------------------------ #
    def _regime_weights(self, spread: float) -> Dict[str, float]:
        D = self.DOMAIN
        T = D["game_minutes"]
        t_q4 = T - D["quarter_minutes"]
        t_late = T - D["foul_window_minutes"]
        s2 = D["margin_sd_full"] ** 2 / T  # variance per minute

        mu = np.array([spread * t_q4 / T, spread * t_late / T])
        cov = np.array([[s2 * t_q4, s2 * t_q4], [s2 * t_q4, s2 * t_late]])
        mvn = stats.multivariate_normal(mean=mu, cov=cov, allow_singular=True)

        g, c = D["blowout_margin"], D["close_margin"]

        def rect(x0, x1, y0, y1):
            return max(0.0, mvn.cdf([x1, y1]) - mvn.cdf([x0, y1]) - mvn.cdf([x1, y0]) + mvn.cdf([x0, y0]))

        sd_q4 = math.sqrt(cov[0, 0])
        p_blow = float(stats.norm.sf(g, mu[0], sd_q4) + stats.norm.cdf(-g, mu[0], sd_q4))
        p_close = float(rect(-g, g, -c, c))
        p_neutral = max(0.0, 1.0 - p_blow - p_close)
        z = p_blow + p_close + p_neutral
        return {"close": p_close / z, "blowout": p_blow / z, "neutral": p_neutral / z}

    # ------------------------------------------------------------------ #
    # Compound possession machinery
    # ------------------------------------------------------------------ #
    def _possession_pmf(self, mean_n: float) -> Tuple[np.ndarray, np.ndarray]:
        """Clock-constrained (under-dispersed) discrete possession count."""
        var_n = max(mean_n * self.DOMAIN["possession_dispersion"], 1e-6)
        sd = math.sqrt(var_n)
        lo = max(0, int(math.floor(mean_n - 6 * sd)))
        hi = int(math.ceil(mean_n + 6 * sd)) + 1
        n = np.arange(lo, hi + 1)
        w = stats.norm.cdf(n + 0.5, mean_n, sd) - stats.norm.cdf(n - 0.5, mean_n, sd)
        w = np.clip(w, 0.0, None)
        return n, w / w.sum()

    @staticmethod
    def _x_pmf(score_rate: float, mix: Tuple[float, float, float]) -> np.ndarray:
        q = np.asarray(mix, dtype=float)
        q = q / q.sum()
        s = min(max(score_rate, 0.0), 1.0)
        return np.array([1.0 - s, s * q[0], s * q[1], s * q[2]])

    @staticmethod
    def _conv_powers(x: np.ndarray, n_max: int) -> list:
        pows = [np.array([1.0])]
        cur = pows[0]
        for _ in range(n_max):
            cur = np.convolve(cur, x)
            pows.append(cur)
        return pows

    def _regime_specs(self, pace_ratio: float) -> Dict[str, Dict[str, Any]]:
        D = self.DOMAIN
        base_n = D["league_pace"] * pace_ratio * D["quarter_minutes"] / D["game_minutes"]
        return {
            "neutral": {"n": base_n, "mix": D["mix_neutral"], "eff": 1.0},
            "close": {"n": base_n * (1.0 + D["foul_game_possession_lift"]), "mix": D["mix_close"], "eff": 1.0},
            "blowout": {"n": base_n * (1.0 - D["blowout_possession_drag"]), "mix": D["mix_blowout"],
                        "eff": D["blowout_efficiency"]},
        }

    def _solve_rates(self, mu: float, specs: Dict[str, Dict[str, Any]], w: Dict[str, float]) -> Dict[str, float]:
        """Return per-regime scoring-possession rates whose mixture mean equals mu."""
        denom = 0.0
        for r, sp in specs.items():
            q = np.asarray(sp["mix"], float)
            q = q / q.sum()
            m = float(q @ np.array([1.0, 2.0, 3.0]))
            sp["_m"] = m
            denom += w[r] * sp["n"] * sp["eff"] * m
        base = mu / max(denom, 1e-9)
        rates = {r: base * sp["eff"] for r, sp in specs.items()}
        # If a rate would exceed 1, the possession volume is understated for
        # this matchup. Lift N to preserve the mean rather than clamp it.
        worst = max(rates.values())
        if worst > 0.97:
            lift = worst / 0.97
            for sp in specs.values():
                sp["n"] *= lift
            rates = {r: v / lift for r, v in rates.items()}
        return rates

    def _joint_matrix(self, mu_h: float, mu_a: float, spread: float, pace_ratio: float) -> np.ndarray:
        w = self._regime_weights(spread)
        specs_h = self._regime_specs(pace_ratio)
        specs_a = self._regime_specs(pace_ratio)
        rates_h = self._solve_rates(mu_h, specs_h, w)
        rates_a = self._solve_rates(mu_a, specs_a, w)

        mats = []
        for r in ("neutral", "close", "blowout"):
            if w[r] <= 1e-12:
                continue
            # Shared possession driver: both teams see the same game tempo.
            # Each team's count is scaled by its own mean-preserving N.
            n_h_mean, n_a_mean = specs_h[r]["n"], specs_a[r]["n"]
            n_vals, n_w = self._possession_pmf(0.5 * (n_h_mean + n_a_mean))
            n_max = int(math.ceil(n_vals.max() * max(n_h_mean, n_a_mean) / max(0.5 * (n_h_mean + n_a_mean), 1e-9))) + 2
            xh = self._x_pmf(rates_h[r], specs_h[r]["mix"])
            xa = self._x_pmf(rates_a[r], specs_a[r]["mix"])
            ph, pa = self._conv_powers(xh, n_max), self._conv_powers(xa, n_max)
            size = 3 * n_max + 1
            M = np.zeros((size, size))
            ratio_h = n_h_mean / max(0.5 * (n_h_mean + n_a_mean), 1e-9)
            ratio_a = n_a_mean / max(0.5 * (n_h_mean + n_a_mean), 1e-9)
            for n, pn in zip(n_vals, n_w):
                if pn < 1e-10:
                    continue
                nh = min(int(round(n * ratio_h)), n_max)
                na = min(int(round(n * ratio_a)), n_max)
                vh, va = ph[nh], pa[na]
                M[: len(vh), : len(va)] += pn * np.outer(vh, va)
            mats.append((w[r], M))

        size = max(m.shape[0] for _, m in mats)
        J = np.zeros((size, size))
        for wr, M in mats:
            J[: M.shape[0], : M.shape[1]] += wr * M
        J /= J.sum()
        return J

    def _apply_overtime(self, J: np.ndarray, mu_h: float, mu_a: float, spread: float, ctx: Dict[str, Any]) -> np.ndarray:
        """Optionally add OT scoring if the book settles Q4 including overtime."""
        if not bool(ctx.get("q4_includes_ot", ctx.get("quarter_includes_overtime", False))):
            return J
        D = self.DOMAIN
        sd = D["margin_sd_full"]
        p_ot = float(stats.norm.cdf(0.5, spread, sd) - stats.norm.cdf(-0.5, spread, sd))
        if p_ot <= 0:
            return J
        ot_frac = 5.0 / D["quarter_minutes"]
        oh, oa = mu_h * ot_frac, mu_a * ot_frac
        kmax = int(oh + oa + 8 * math.sqrt(oh + oa)) + 1
        k = np.arange(kmax + 1)
        ot = np.outer(stats.poisson.pmf(k, oh), stats.poisson.pmf(k, oa))
        size = J.shape[0] + kmax
        out = np.zeros((size, size))
        out[: J.shape[0], : J.shape[1]] += (1 - p_ot) * J
        from scipy.signal import fftconvolve
        conv = np.clip(fftconvolve(J, ot), 0, None)
        out[: conv.shape[0], : conv.shape[1]] += p_ot * conv
        return out / out.sum()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        mu_h, mu_a, spread, pace_ratio = self._quarter_means(ctx)
        J = self._joint_matrix(mu_h, mu_a, spread, pace_ratio)
        J = self._apply_overtime(J, mu_h, mu_a, spread, ctx)

        # Trim negligible tail mass for efficiency, then renormalize.
        J[J < 1e-12] = 0.0
        J /= J.sum()
        idx = np.argwhere(J > 0)
        scores = {(int(h), int(a)): float(J[h, a]) for h, a in idx}
        self._last_diagnostics = self._diagnostics(J, mu_h, mu_a, spread)
        try:
            return DiscreteDistribution.from_scores(scores)  # type: ignore  # noqa: F821
        except Exception:
            return DiscreteDistribution.from_scores(J)  # type: ignore  # noqa: F821

    def _diagnostics(self, J: np.ndarray, mu_h: float, mu_a: float, spread: float) -> Dict[str, float]:
        h = np.arange(J.shape[0])[:, None]
        a = np.arange(J.shape[1])[None, :]
        tot = h + a
        mean_t = float((J * tot).sum())
        var_t = float((J * tot ** 2).sum() - mean_t ** 2)
        return {
            "mu_home_q4": mu_h,
            "mu_away_q4": mu_a,
            "full_game_spread": spread,
            "mean_total": mean_t,
            "var_total": var_t,
            "dispersion_index": var_t / mean_t if mean_t > 0 else float("nan"),
            **{f"w_{k}": v for k, v in self._regime_weights(spread).items()},
        }

    def total_over_probability(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Return P(over), P(under) and P(push) for a Q4 total line. Convenience helper."""
        mu_h, mu_a, spread, pace_ratio = self._quarter_means(game_context or {})
        J = self._apply_overtime(self._joint_matrix(mu_h, mu_a, spread, pace_ratio), mu_h, mu_a, spread,
                                 game_context or {})
        h = np.arange(J.shape[0])[:, None]
        a = np.arange(J.shape[1])[None, :]
        tot = h + a
        return {
            "over": float(J[tot > line].sum()),
            "under": float(J[tot < line].sum()),
            "push": float(J[np.isclose(tot, line)].sum()),
        }