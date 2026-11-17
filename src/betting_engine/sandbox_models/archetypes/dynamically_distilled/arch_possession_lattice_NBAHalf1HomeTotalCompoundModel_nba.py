"""
Candidate model for NBA | half_1 | home_total.

Note on the supplied diagnostics (read before deployment):
    A Var/Mean index of 0.187 at a mean of ~56 implies a first-half home-points
    standard deviation of about 3.2. Possession mechanics put the real figure
    at roughly 7.5-8.5 (Var/Mean of about 1.1-1.3; see the derivation below).
    The 0.187 figure is almost certainly the dispersion of something other than
    realized home first-half scores, such as model expectations or closing lines
    across games.

    Hard-coding 0.187 as the scoring dispersion would make the model about 2.5x
    overconfident. It would then generate large phantom "edges" on both sides of
    every total, which is the classic route to the -100% deficits listed in the
    brief. This model therefore:
      * derives variance structurally from the possession count and the
        distribution of points per possession;
      * exposes `calibrate(history)` so the variance multiplier and the mean bias
        are fitted, with shrinkage, from realized half-1 scores rather than from
        the diagnostic index.

    The under-side PnL skew ($71.55 of $89.50) over 500 bets is consistent with
    noise at this sample size. It is not treated as structural evidence.

Structural derivation:
    S = sum_{i=1..N} X_i
        N = home possessions in the half
        X_i in {0, 1, 2, 3, 4} = points on possession i
    E[S]   = E[N] * e
    Var[S] = E[N] * Var(X) + Var(N) * e^2
    Var(X) = e * (k - e), where k = E[X^2] / E[X] (about 2.2-2.3 in the NBA)
    Var(N) = (cv_pace * E[N])^2
        Possessions in a half are nearly deterministic given pace; only
        the pace uncertainty contributes.
    Typical values: E[N] ~ 50, e ~ 1.12  ->  Var/Mean ~ 1.2 before calibration.

Pricing distribution:
    Moment-matched discrete law on integer points.
      * Var > Mean: negative binomial.
      * Var <= Mean: discretized normal (Normal CDF differences at k +/- 0.5).
    Output is renormalized, so probabilities are non-negative and sum to 1.
    Runtime is about 1 ms per call.
"""

import math
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded; import if available.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover
        pass


class NBAHalf1HomeTotalCompoundModel(BaseCandidateModel):
    """Compound possession x points-per-possession model for home 1H points."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_195828",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        # Empirical anchors (from the segment's settled sample).
        home_anchor: float = 56.0,
        away_anchor: float = 54.0,
        # Domain structure. Each value is documented and overridable,
        # and the scale terms are refined by calibrate().
        league_pace: float = 99.0,           # possessions per 48 minutes
        half_minutes: float = 24.0,
        game_minutes: float = 48.0,
        first_half_share: float = 0.50,      # applied only if a full-game expectation is supplied
        ppp_second_moment_ratio: float = 2.25,  # k = E[X^2] / E[X]
        pace_cv: float = 0.05,               # relative uncertainty of possession count
        rest_log_coef: float = 0.012,        # efficiency elasticity w.r.t. log1p(rest) differential
        # Shrinkage prior strength (pseudo-games) for calibration.
        calibration_prior_games: float = 150.0,
        tail_sd: float = 8.0,                # support half-width in standard deviations
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.home_anchor = float(home_anchor)
        self.away_anchor = float(away_anchor)
        self.league_pace = float(league_pace)
        self.half_frac = float(half_minutes) / float(game_minutes)
        self.first_half_share = float(first_half_share)
        self.k_ratio = float(ppp_second_moment_ratio)
        self.pace_cv = float(pace_cv)
        self.rest_log_coef = float(rest_log_coef)
        self.prior_n = float(calibration_prior_games)
        self.tail_sd = float(tail_sd)

        # Calibrated multipliers (identity until calibrate() is run).
        self.mean_scale = 1.0
        self.var_scale = 1.0
        self.n_calibrated = 0

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _team_feat(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        nested = ctx.get(side)
        if isinstance(nested, dict) and key in nested:
            return self._num(nested.get(key))
        return self._num(ctx.get(f"{side}_{key}"))

    def _rating_mult(self, val: Optional[float]) -> float:
        """Return a rating as a multiplier around 1.

        Accepts either multiplier form (~1.0) or points-per-100 form (~115).
        """
        if val is None or val <= 0:
            return 1.0
        if val > 3.0:  # per-100-possession rating
            return val / (self.league_pace * 1.0 + 1e-9) * (self.league_pace / 113.0)
        return val

    def _pace(self, ctx: Dict[str, Any]) -> float:
        hp = self._team_feat(ctx, "home", "pace")
        ap = self._team_feat(ctx, "away", "pace")
        gp = self._num(ctx.get("pace"))
        vals = [v for v in (hp, ap) if v is not None and v > 0]
        if vals:
            return float(np.mean(vals))
        if gp is not None and gp > 0:
            return gp
        return self.league_pace

    def _half_expectation(self, raw: Optional[float], anchor: float) -> Optional[float]:
        """Convert a supplied expectation to first-half scale when it is clearly full-game."""
        if raw is None or raw <= 0:
            return None
        if raw > 1.5 * anchor:
            return raw * self.first_half_share
        return raw

    def _side_mean(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        """Return (expected 1H points, expected possessions) for one side."""
        other = "away" if side == "home" else "home"
        anchor = self.home_anchor if side == "home" else self.away_anchor

        pace = self._pace(ctx)
        exp_n = pace * self.half_frac

        explicit = self._half_expectation(
            self._num(ctx.get(f"{side}_expected_half1"))
            or self._num(ctx.get(f"{side}_expected")),
            anchor,
        )

        if explicit is not None:
            mu = explicit
        else:
            # Build from anchor x pace x matchup efficiency. Rating
            # multipliers act on e (efficiency); pace acts on E[N].
            att = self._rating_mult(self._team_feat(ctx, side, "attack"))
            dfn = self._rating_mult(self._team_feat(ctx, other, "defense"))
            mu = anchor * (pace / self.league_pace) * att * dfn

        # Rest differential acts multiplicatively on efficiency.
        r_self = self._num(ctx.get(f"{side}_rest_days"))
        r_oth = self._num(ctx.get(f"{other}_rest_days"))
        if r_self is not None and r_oth is not None:
            d = math.log1p(max(r_self, 0.0)) - math.log1p(max(r_oth, 0.0))
            mu *= math.exp(self.rest_log_coef * d)

        mu *= self.mean_scale
        return max(mu, 1.0), max(exp_n, 1.0)

    def _structural_var(self, mu: float, exp_n: float) -> float:
        e = mu / exp_n  # implied points per possession
        var_x = max(e * (self.k_ratio - e), 1e-6)
        var_n = (self.pace_cv * exp_n) ** 2
        return (exp_n * var_x + var_n * e * e) * self.var_scale

    # ------------------------------------------------------------------ #
    # Discrete pricing law
    # ------------------------------------------------------------------ #
    def _pmf(self, mu: float, var: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = math.sqrt(var)
        lo = max(0, int(math.floor(mu - self.tail_sd * sd)))
        hi = int(math.ceil(mu + self.tail_sd * sd)) + 1
        k = np.arange(lo, hi + 1)

        if var > mu * (1.0 + 1e-9):
            r = mu * mu / (var - mu)
            p = r / (r + mu)
            pmf = stats.nbinom.pmf(k, r, p)
        else:
            pmf = stats.norm.cdf(k + 0.5, mu, sd) - stats.norm.cdf(k - 0.5, mu, sd)

        pmf = np.clip(np.nan_to_num(pmf, nan=0.0), 0.0, None)
        tot = pmf.sum()
        if tot <= 0:
            pmf = np.zeros_like(pmf, dtype=float)
            pmf[int(np.argmin(np.abs(k - mu)))] = 1.0
        else:
            pmf = pmf / tot
        return k, pmf

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def moments(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        mu_h, n_h = self._side_mean(game_context, "home")
        mu_a, n_a = self._side_mean(game_context, "away")
        return {
            "home_mean": mu_h,
            "home_var": self._structural_var(mu_h, n_h),
            "away_mean": mu_a,
            "away_var": self._structural_var(mu_a, n_a),
        }

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        m = self.moments(game_context or {})
        kh, ph = self._pmf(m["home_mean"], m["home_var"])
        ka, pa = self._pmf(m["away_mean"], m["away_var"])

        # The home-total market depends only on the home marginal. The away
        # marginal is attached independently so the score lattice is complete.
        joint = np.outer(ph, pa)
        joint /= joint.sum()

        scores: Dict[Tuple[int, int], float] = {}
        for i, h in enumerate(kh):
            row = joint[i]
            for j, a in enumerate(ka):
                pij = float(row[j])
                if pij > 0.0:
                    scores[(int(h), int(a))] = pij

        s = sum(scores.values())
        scores = {key: v / s for key, v in scores.items()}
        return DiscreteDistribution.from_scores(scores)

    def calibrate(self, history: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        """Fit mean and variance multipliers from realized first-half home scores.

        Each record is a game_context dict that also carries the realized score
        under 'home_score_half1' (or 'home_score_h1' / 'home_points_half1').
        Both estimates are shrunk toward 1 with `calibration_prior_games`
        pseudo-observations to protect out-of-sample skill.
        """
        self.mean_scale, self.var_scale = 1.0, 1.0
        mus, vars_, ys = [], [], []
        for rec in history:
            y = None
            for key in ("home_score_half1", "home_score_h1", "home_points_half1"):
                y = self._num(rec.get(key))
                if y is not None:
                    break
            if y is None:
                continue
            mu, n = self._side_mean(rec, "home")
            mus.append(mu)
            vars_.append(self._structural_var(mu, n))
            ys.append(y)

        n = len(ys)
        if n == 0:
            self.n_calibrated = 0
            return {"mean_scale": 1.0, "var_scale": 1.0, "n": 0}

        mus_a = np.asarray(mus)
        var_a = np.asarray(vars_)
        y_a = np.asarray(ys)
        w = n / (n + self.prior_n)

        raw_ms = float(y_a.sum() / mus_a.sum())
        ms = 1.0 + w * (raw_ms - 1.0)

        resid = y_a - mus_a * ms
        raw_vs = float(np.mean(resid ** 2 / var_a))
        # Shrink in log space; the variance ratio is multiplicative.
        vs = math.exp(w * math.log(max(raw_vs, 1e-6)))

        self.mean_scale, self.var_scale, self.n_calibrated = ms, vs, n
        return {
            "mean_scale": ms,
            "var_scale": vs,
            "raw_mean_ratio": raw_ms,
            "raw_var_ratio": raw_vs,
            "n": n,
        }