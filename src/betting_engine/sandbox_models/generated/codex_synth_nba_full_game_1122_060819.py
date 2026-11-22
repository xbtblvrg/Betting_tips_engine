"""
NBA | half_1 | home_total candidate model.

Structure: the home first-half total is modeled as a compound sum
T = sum_{i=1..N} X_i. N is a discretized Normal number of possessions and
X_i is a per-possession points outcome on {0,1,2,3,4}. The outcome mix is
exponentially tilted so that E[T] matches the situational mean. The pmf is
computed exactly via FFT (no Poisson/NegBin Var/Mean >= 1 assumption is
imposed).

The total variance is then calibrated by a single dispersion multiplier,
lambda, fitted by penalized maximum likelihood on settled games. lambda
rescales the compound distribution about its mean, so the mean is preserved.

Note on the 0.187 "dispersion index": at a mean of ~56, Var/Mean = 0.187
implies a score SD of about 3.2 points. That is far below the realistic
spread of NBA first-half team scoring. It most likely measures something
other than game-level residual variance, such as the spread of pre-game
expectations. It is therefore kept as a diagnostic only and not used as a
calibration target. Calibrating to it would make the model badly
overconfident. Let the data decide via fit().

Benchmark ROI figures (+90% to +122%) are not realistic out-of-sample
targets for a liquid totals market. Judge this model on held-out log-loss
or Brier score and on closing-line value, not on in-sample ROI.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # DiscreteDistribution is pre-loaded into execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


_DEFAULT_CONFIG: Dict[str, Any] = {
    # Empirical anchors (segment means from the settled sample)
    "anchor_home_mean": 56.0,
    "anchor_away_mean": 54.0,
    # Possession process priors (per half)
    "possessions_per_half": 49.5,
    "possession_sd": 2.5,
    # Per-possession points outcome mix prior for {0,1,2,3,4}; tilted per game
    "ppp_outcome_mix": (0.47, 0.04, 0.31, 0.16, 0.02),
    # League offensive rating (per 100 possessions) used to normalize ratings
    "league_ortg": 115.0,
    # Rest effect on log-mean per unit of log1p(rest) differential (refit in fit())
    "rest_beta": 0.012,
    # Multiplicative mean bias (refit in fit())
    "mean_bias": 1.0,
    # Variance multiplier relative to pure compound mechanics (refit in fit())
    "dispersion_lambda": 1.0,
    "lambda_prior_log_sd": 0.35,
    "lambda_search_log_span": 3.0,
    # Numerics
    "tail_tol": 1e-10,
    "min_fit_samples": 50,
    # Diagnostic only (see module docstring)
    "observed_dispersion_index": 0.187,
}

_OUTCOME_KEYS = (
    "actual_home_total",
    "home_points",
    "home_score",
    "home_half_points",
    "home_score_h1",
    "home_points_h1",
)


def _num(ctx: Dict[str, Any], keys: Sequence[str], default: Optional[float] = None) -> Optional[float]:
    for k in keys:
        v = ctx.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f
    return default


def _side_rating(ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
    nested = ctx.get(side)
    if isinstance(nested, dict):
        v = _num(nested, (kind, f"{kind}_rating", "ortg" if kind == "attack" else "drtg"))
        if v is not None:
            return v
    return _num(ctx, (f"{side}_{kind}", f"{side}_{kind}_rating"))


class NbaHalf1HomeTotalCompoundModel(BaseCandidateModel):
    """Compound possession x points-per-possession model for NBA 1H home totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1122_060819",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        **config: Any,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        except TypeError:
            try:
                super().__init__()
            except TypeError:
                pass
        for name, val in (("model_id", model_id), ("version", version), ("league", league),
                          ("scope", scope), ("family", family)):
            if getattr(self, name, None) != val:
                try:
                    setattr(self, name, val)
                except AttributeError:
                    pass

        self.config: Dict[str, Any] = dict(_DEFAULT_CONFIG)
        self.config.update(config)
        mix = np.asarray(self.config["ppp_outcome_mix"], dtype=float)
        if mix.ndim != 1 or mix.size < 2 or np.any(mix < 0) or mix.sum() <= 0:
            raise ValueError("ppp_outcome_mix must be a non-negative vector with positive mass")
        self._base_mix = mix / mix.sum()
        self._points = np.arange(self._base_mix.size, dtype=float)
        self.fit_report: Dict[str, Any] = {}

    # ------------------------------------------------------------------ features
    def _possession_params(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        cfg = self.config
        prior_mu = float(cfg["possessions_per_half"])
        paces: List[float] = []
        for side in ("home", "away"):
            p = _num(ctx, (f"{side}_pace",))
            if p is None and isinstance(ctx.get(side), dict):
                p = _num(ctx[side], ("pace",))
            if p is not None and p > 0:
                paces.append(p)
        game_pace = _num(ctx, ("pace", "expected_pace"))
        if game_pace is not None and game_pace > 0:
            paces.append(game_pace)
        if paces:
            pace = float(np.mean(paces))
            # Per-48 pace values are about 2x the per-half possession count
            mu_n = pace / 2.0 if pace > 1.5 * prior_mu else pace
        else:
            mu_n = prior_mu
        sigma_n = float(cfg["possession_sd"]) * math.sqrt(mu_n / prior_mu)
        return mu_n, sigma_n

    def _rest_delta(self, ctx: Dict[str, Any]) -> float:
        hr = _num(ctx, ("home_rest_days",))
        ar = _num(ctx, ("away_rest_days",))
        if hr is None or ar is None:
            return 0.0
        return math.log1p(max(hr, 0.0)) - math.log1p(max(ar, 0.0))

    def _rating_ratio(self, value: Optional[float]) -> float:
        if value is None or value <= 0:
            return 1.0
        # Ratings may be per-100 (approx. league_ortg) or already relative (approx. 1)
        return value / float(self.config["league_ortg"]) if value > 10.0 else value

    def _raw_means(self, ctx: Dict[str, Any], mu_n: float) -> Tuple[float, float]:
        cfg = self.config
        h_exp = _num(ctx, ("home_expected",))
        a_exp = _num(ctx, ("away_expected",))
        prior_n = float(cfg["possessions_per_half"])
        if h_exp is None or h_exp <= 0:
            base_ppp = float(cfg["anchor_home_mean"]) / prior_n
            h_exp = mu_n * base_ppp * self._rating_ratio(_side_rating(ctx, "home", "attack")) \
                * self._rating_ratio(_side_rating(ctx, "away", "defense"))
        if a_exp is None or a_exp <= 0:
            base_ppp = float(cfg["anchor_away_mean"]) / prior_n
            a_exp = mu_n * base_ppp * self._rating_ratio(_side_rating(ctx, "away", "attack")) \
                * self._rating_ratio(_side_rating(ctx, "home", "defense"))
        return float(h_exp), float(a_exp)

    def _home_mean(self, ctx: Dict[str, Any], mu_n: float) -> float:
        h_raw, _ = self._raw_means(ctx, mu_n)
        return h_raw * float(self.config["mean_bias"]) * math.exp(
            float(self.config["rest_beta"]) * self._rest_delta(ctx))

    # ------------------------------------------------------------------ mechanics
    def _possession_weights(self, mu_n: float, sigma_n: float) -> Tuple[np.ndarray, np.ndarray]:
        tol = float(self.config["tail_tol"])
        if sigma_n <= 0:
            n = np.array([max(1, int(round(mu_n)))])
            return n, np.ones(1)
        z = float(stats.norm.isf(tol / 2.0))
        lo = max(1, int(math.floor(mu_n - z * sigma_n)))
        hi = max(lo, int(math.ceil(mu_n + z * sigma_n)))
        n = np.arange(lo, hi + 1)
        w = stats.norm.cdf(n + 0.5, mu_n, sigma_n) - stats.norm.cdf(n - 0.5, mu_n, sigma_n)
        w = np.maximum(w, 0.0)
        return n, w / w.sum()

    def _tilted_mix(self, rho: float) -> np.ndarray:
        k, p = self._points, self._base_mix
        kmax = k[p > 0].max()
        target = min(max(rho, 1e-9), kmax * (1 - 1e-9))

        def gap(theta: float) -> float:
            lw = np.log(np.where(p > 0, p, 1e-300)) + theta * k
            lw -= lw.max()
            w = np.exp(lw) * (p > 0)
            return float((w * k).sum() / w.sum()) - target

        lo, hi = -1.0, 1.0
        while gap(lo) > 0:
            lo *= 2.0
        while gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(gap, lo, hi, xtol=1e-12)
        lw = np.log(np.where(p > 0, p, 1e-300)) + theta * k
        lw -= lw.max()
        w = np.exp(lw) * (p > 0)
        return w / w.sum()

    def _compound_pmf(self, mean: float, mu_n: float, sigma_n: float) -> np.ndarray:
        n, w = self._possession_weights(mu_n, sigma_n)
        e_n = float((n * w).sum())
        mix = self._tilted_mix(mean / e_n)
        max_pts = int(self._points[-1]) * int(n[-1])
        size = 1 << int(math.ceil(math.log2(max_pts + 2)))
        px = np.zeros(size)
        px[: mix.size] = mix
        phi = np.fft.rfft(px)
        mixed = (w[:, None] * phi[None, :] ** n[:, None]).sum(axis=0)
        pmf = np.fft.irfft(mixed, size)[: max_pts + 1]
        pmf = np.where(pmf > 0, pmf, 0.0)  # remove FFT round-off negatives
        return pmf / pmf.sum()

    @staticmethod
    def _rescaled_cdf(pmf: np.ndarray, center: float, scale: float, x: np.ndarray) -> np.ndarray:
        knots_x = np.arange(pmf.size + 1) - 0.5
        knots_y = np.concatenate(([0.0], np.cumsum(pmf)))
        return np.interp(center + (x - center) / scale, knots_x, knots_y, left=0.0, right=1.0)

    def _rescale(self, pmf: np.ndarray, center: float, lam: float) -> np.ndarray:
        if abs(lam - 1.0) < 1e-12:
            out = pmf
        else:
            s = math.sqrt(lam)
            k_max = max(pmf.size - 1, int(math.ceil(center + s * (pmf.size - 0.5 - center))) + 1)
            edges = np.arange(k_max + 2) - 0.5
            g = self._rescaled_cdf(pmf, center, s, edges)
            g[0], g[-1] = 0.0, 1.0
            out = np.diff(g)
            out = np.where(out > 0, out, 0.0)
        cdf = np.cumsum(out)
        last = int(np.searchsorted(cdf, cdf[-1] * (1.0 - float(self.config["tail_tol"])))) + 1
        out = out[: max(last, 1)]
        return out / out.sum()

    def home_pmf(self, ctx: Dict[str, Any]) -> np.ndarray:
        mu_n, sigma_n = self._possession_params(ctx)
        m = self._home_mean(ctx, mu_n)
        raw = self._compound_pmf(m, mu_n, sigma_n)
        return self._rescale(raw, m, float(self.config["dispersion_lambda"]))

    def _away_pmf(self, ctx: Dict[str, Any]) -> np.ndarray:
        mu_n, sigma_n = self._possession_params(ctx)
        _, a_raw = self._raw_means(ctx, mu_n)
        m = a_raw * float(self.config["mean_bias"]) * math.exp(
            -float(self.config["rest_beta"]) * self._rest_delta(ctx))
        raw = self._compound_pmf(m, mu_n, sigma_n)
        return self._rescale(raw, m, float(self.config["dispersion_lambda"]))

    # ------------------------------------------------------------------ interface
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        hp = self.home_pmf(ctx)
        ap = self._away_pmf(ctx)
        # Home marginal is exact; away is attached for score-lattice compatibility
        joint = np.outer(hp, ap)
        joint /= joint.sum()
        return self._emit(joint, hp)

    def _emit(self, joint: np.ndarray, home_pmf: np.ndarray) -> "DiscreteDistribution":
        try:
            return DiscreteDistribution.from_scores(joint)
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
        scores = {(int(i), int(j)): float(joint[i, j])
                  for i, j in zip(*np.nonzero(joint > 0))}
        total = sum(scores.values())
        scores = {k: v / total for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
        probs = {str(k): float(p) for k, p in enumerate(home_pmf) if p > 0}
        total = sum(probs.values())
        return DiscreteDistribution(probabilities={k: v / total for k, v in probs.items()})

    def price_line(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        """Over/under/push probabilities for a home first-half total line."""
        pmf = self.home_pmf(game_context or {})
        k = np.arange(pmf.size)
        over = float(pmf[k > line].sum())
        push = float(pmf[k == line].sum()) if float(line).is_integer() else 0.0
        return {"over": over, "under": max(0.0, 1.0 - over - push), "push": push}

    # ------------------------------------------------------------------ calibration
    def fit(self, records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """Fit mean bias, rest effect and dispersion lambda on settled games.

        Each record is either a game_context dict containing the realized home
        first-half points, or {'game_context': ctx, <outcome key>: y}.
        """
        cfg = self.config
        ctxs: List[Dict[str, Any]] = []
        ys: List[float] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            ctx = r.get("game_context", r)
            y = _num(r, _OUTCOME_KEYS)
            if y is None and isinstance(ctx, dict):
                y = _num(ctx, _OUTCOME_KEYS)
            if y is None or y < 0 or not isinstance(ctx, dict):
                continue
            ctxs.append(ctx)
            ys.append(float(round(y)))
        n_obs = len(ys)
        if n_obs < int(cfg["min_fit_samples"]):
            self.fit_report = {"status": "skipped", "n": n_obs}
            return self.fit_report

        y = np.asarray(ys)
        poss = [self._possession_params(c) for c in ctxs]
        raw_m = np.array([self._raw_means(c, p[0])[0] for c, p in zip(ctxs, poss)])
        delta = np.array([self._rest_delta(c) for c in ctxs])

        # Step 1: mean structure via least squares on the natural scale
        def resid(theta: np.ndarray) -> np.ndarray:
            return y - raw_m * np.exp(theta[0] + theta[1] * delta)

        x0 = np.array([math.log(float(cfg["mean_bias"])), float(cfg["rest_beta"])])
        sol = optimize.least_squares(resid, x0)
        bias, beta = math.exp(sol.x[0]), float(sol.x[1])
        means = raw_m * np.exp(sol.x[0] + beta * delta)

        # Step 2: dispersion lambda via penalized profile likelihood
        raw_pmfs = [self._compound_pmf(m, p[0], p[1]) for m, p in zip(means, poss)]
        mech_var = np.array([float(((np.arange(q.size) - m) ** 2 * q).sum())
                             for q, m in zip(raw_pmfs, means)])
        prior_log = math.log(float(cfg["dispersion_lambda"]))
        prior_sd = float(cfg["lambda_prior_log_sd"])

        def nll(log_lam: float) -> float:
            s = math.exp(0.5 * log_lam)
            total = 0.0
            for q, m, yi in zip(raw_pmfs, means, y):
                lo = 0.0 if yi <= 0 else float(self._rescaled_cdf(q, m, s, np.array([yi - 0.5]))[0])
                hi = float(self._rescaled_cdf(q, m, s, np.array([yi + 0.5]))[0])
                total -= math.log(max(hi - lo, 1e-300))
            return total + 0.5 * ((log_lam - prior_log) / prior_sd) ** 2

        moment_lam = float(np.mean((y - means) ** 2) / np.mean(mech_var))
        center = math.log(moment_lam) if moment_lam > 0 else prior_log
        span = float(cfg["lambda_search_log_span"])
        res = optimize.minimize_scalar(nll, bounds=(center - span, center + span), method="bounded")
        lam = math.exp(float(res.x))

        cfg["mean_bias"], cfg["rest_beta"], cfg["dispersion_lambda"] = bias, beta, lam
        fitted_var = lam * mech_var
        self.fit_report = {
            "status": "fitted",
            "n": n_obs,
            "mean_bias": bias,
            "rest_beta": beta,
            "dispersion_lambda": lam,
            "moment_lambda": moment_lam,
            "mean_nll": float(res.fun) / n_obs,
            "implied_var_over_mean": float(np.mean(fitted_var / means)),
            "empirical_var_over_mean": float(np.var(y - means) / np.mean(means)),
            "reported_dispersion_index": float(cfg["observed_dispersion_index"]),
        }
        return self.fit_report