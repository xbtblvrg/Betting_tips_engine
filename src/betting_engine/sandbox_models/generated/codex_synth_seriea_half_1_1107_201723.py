"""
Candidate model for SERIEA | half_1 | spreads.

Model chain:
    situational context -> online Bayesian log-linear rate model
        -> home-ratio governor driven by posterior variance
        -> bivariate Poisson (shared component) score lattice
        -> Dixon-Coles low-score correction, theta solved per match so the
           level-score mass hits an empirical-Bayes half-time draw target
        -> exact margin PMF -> Asian handicap pricing (quarter lines split)

Cold start: every learned coefficient has prior mean zero. A context with no
features therefore reproduces the 0.80 / 0.60 anchor. All prior
hyperparameters are explicit constructor arguments, expressed in observable
units (draw rate, pseudo-halves, log-scale SD). No clamps are applied other
than the mathematical validity domains of the distributions.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pre-loaded into the sandbox execution scope
    DiscreteDistribution = globals().get("DiscreteDistribution")


def _finite(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _positive(x: Any) -> Optional[float]:
    v = _finite(x)
    return v if (v is not None and v > 0.0) else None


def _lookup(ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
    """Read ctx[side][key] (nested) or ctx[f'{side}_{key}'] (flat)."""
    nested = ctx.get(side)
    if isinstance(nested, dict) and key in nested:
        v = _finite(nested.get(key))
        if v is not None:
            return v
    return _finite(ctx.get(f"{side}_{key}"))


class SerieAHalf1SpreadsModel(BaseCandidateModel):
    """Bivariate-Poisson / Dixon-Coles first-half margin model for Serie A spreads."""

    # Standardised situational features (learned slopes, prior mean 0).
    _FEATURES: Tuple[str, ...] = ("pace", "rest", "wind", "temp")
    # Design layout: [home_intercept, away_intercept, matchup, pace, rest, wind, temp]
    _DIM: int = 7

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_1_1107_201723",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_1",
        family: str = "spreads",
        *,
        # Empirical anchors (segment moments).
        base_home: float = 0.80,
        base_away: float = 0.60,
        # Prior half-time draw rate for Serie A and its strength in pseudo-halves.
        prior_ht_draw_rate: float = 0.42,
        draw_prior_halves: float = 200.0,
        # Prior shared-component fraction lambda3 / min(lambda_H, lambda_A).
        prior_shared_frac: float = 0.05,
        shared_prior_halves: float = 200.0,
        # Prior SDs on the log scale for intercepts and standardised slopes.
        intercept_prior_sd: float = 0.10,
        feature_prior_sd: float = 0.10,
        # Numerical lattice truncation (tail probability), not a modelling clamp.
        tail_eps: float = 1e-12,
        # Eligibility floor specified by the operating mandate.
        min_odds: float = 1.40,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        for attr, val in (
            ("model_id", model_id),
            ("version", version),
            ("league", league),
            ("scope", scope),
            ("family", family),
        ):
            if not hasattr(self, attr):
                setattr(self, attr, val)

        self.base_home = float(base_home)
        self.base_away = float(base_away)
        self.tail_eps = float(tail_eps)
        self.min_odds = float(min_odds)

        # Gaussian (Laplace / assumed-density) posterior over log-rate coefficients.
        prior_sd = np.array([intercept_prior_sd] * 2 + [feature_prior_sd] * (self._DIM - 2), dtype=float)
        self._beta = np.zeros(self._DIM)
        self._prec = np.diag(1.0 / prior_sd ** 2)

        # Online standardisation (Welford) for situational features.
        self._feat_stats: Dict[str, List[float]] = {f: [0.0, 0.0, 0.0] for f in self._FEATURES}

        # Gamma posterior on the draw-inflation ratio R = P(draw) / P_indep(draw).
        p_ref = self._indep_draw(self.base_home, self.base_away)
        self._draw_a = draw_prior_halves * prior_ht_draw_rate
        self._draw_b = draw_prior_halves * p_ref

        # Moment-matched posterior for the shared-component fraction.
        m_ref = min(self.base_home, self.base_away)
        self._cov_num = shared_prior_halves * prior_shared_frac * m_ref
        self._cov_den = shared_prior_halves * m_ref

        self._n_obs = 0
        self._last: Dict[str, Any] = {}

    # ------------------------------------------------------------------ features
    def _raw_features(self, ctx: Dict[str, Any]) -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {f: None for f in self._FEATURES}

        # Pace / tempo on the log scale, relative to the league pace if supplied.
        pace = _positive(ctx.get("pace")) or _positive(ctx.get("tempo"))
        if pace is None:
            side_logs = [
                math.log(v)
                for v in (
                    _lookup(ctx, "home", "pace") or _lookup(ctx, "home", "tempo"),
                    _lookup(ctx, "away", "pace") or _lookup(ctx, "away", "tempo"),
                )
                if v is not None and v > 0.0
            ]
            if side_logs:
                pace = math.exp(sum(side_logs) / len(side_logs))
        if pace is not None:
            league_pace = _positive(ctx.get("league_pace"))
            out["pace"] = math.log(pace) - (math.log(league_pace) if league_pace else 0.0)

        # Rest differential. log1p gives diminishing returns to extra days.
        hr, ar = _finite(ctx.get("home_rest_days")), _finite(ctx.get("away_rest_days"))
        if hr is not None and ar is not None and hr >= 0.0 and ar >= 0.0:
            out["rest"] = math.log1p(hr) - math.log1p(ar)

        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        is_dome = bool(weather.get("is_dome", ctx.get("is_dome", False)))
        wind = _finite(weather.get("wind_speed", ctx.get("wind_speed")))
        out["wind"] = 0.0 if is_dome else wind
        out["temp"] = _finite(weather.get("temp", ctx.get("temp")))
        return out

    def _standardize(self, name: str, raw: Optional[float]) -> float:
        n, mean, m2 = self._feat_stats[name]
        if raw is None or n < 2.0:
            return 0.0
        var = m2 / (n - 1.0)
        return (raw - mean) / math.sqrt(var) if var > 0.0 else 0.0

    def _update_feature_stats(self, raw: Dict[str, Optional[float]]) -> None:
        for name, x in raw.items():
            if x is None:
                continue
            s = self._feat_stats[name]
            s[0] += 1.0
            delta = x - s[1]
            s[1] += delta / s[0]
            s[2] += delta * (x - s[1])

    @staticmethod
    def _matchup(ctx: Dict[str, Any]) -> Tuple[float, float]:
        """s_H = log(att_H) - log(def_A); s_A = log(att_A) - log(def_H).
        Ratings are multiplicative strengths around 1. A larger defense value
        means a stronger defense."""
        def lg(v: Optional[float]) -> float:
            return math.log(v) if (v is not None and v > 0.0) else 0.0

        s_h = lg(_lookup(ctx, "home", "attack")) - lg(_lookup(ctx, "away", "defense"))
        s_a = lg(_lookup(ctx, "away", "attack")) - lg(_lookup(ctx, "home", "defense"))
        return s_h, s_a

    def _design(self, ctx: Dict[str, Any]) -> Tuple[float, float, np.ndarray, np.ndarray]:
        he = next(
            (v for v in (_positive(ctx.get(k)) for k in ("half_1_home_expected", "home_expected_half_1", "home_expected")) if v),
            None,
        )
        ae = next(
            (v for v in (_positive(ctx.get(k)) for k in ("half_1_away_expected", "away_expected_half_1", "away_expected")) if v),
            None,
        )
        s_h, s_a = self._matchup(ctx)

        # If an upstream expectation is supplied, it presumably already embeds
        # the ratings, so the matchup enters only through a learned (prior-0)
        # slope to avoid double counting. Otherwise the ratings build the mean
        # structurally from the anchor.
        if he is not None:
            base_h, m_h = he, s_h
        else:
            base_h, m_h = self.base_home * math.exp(s_h), 0.0
        if ae is not None:
            base_a, m_a = ae, s_a
        else:
            base_a, m_a = self.base_away * math.exp(s_a), 0.0

        raw = self._raw_features(ctx)
        z = {f: self._standardize(f, raw[f]) for f in self._FEATURES}
        z_h = np.array([1.0, 0.0, m_h, z["pace"], z["rest"], z["wind"], z["temp"]])
        z_a = np.array([0.0, 1.0, m_a, z["pace"], -z["rest"], z["wind"], z["temp"]])
        return base_h, base_a, z_h, z_a

    # ------------------------------------------------------------------ rates
    def _means(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        base_h, base_a, z_h, z_a = self._design(ctx)
        sigma = np.linalg.inv(self._prec)
        eta_h = math.log(base_h) + float(z_h @ self._beta)
        eta_a = math.log(base_a) + float(z_a @ self._beta)
        v_h = float(z_h @ sigma @ z_h)
        v_a = float(z_a @ sigma @ z_a)
        # Posterior-predictive (log-normal) means.
        lam_h = math.exp(eta_h + 0.5 * v_h)
        lam_a = math.exp(eta_a + 0.5 * v_a)

        # Home over-confidence governor. Shrink the log-ratio deviation from
        # the empirical anchor by its signal-to-noise ratio
        # (James-Stein form w = d^2 / (d^2 + V)). V comes from the coefficient
        # posterior plus any stated variance of the upstream inputs. Total
        # goal expectation is preserved.
        log_r0 = math.log(self.base_home / self.base_away)
        d = math.log(lam_h / lam_a) - log_r0
        dz = z_h - z_a
        ext_var = _finite(ctx.get("expected_log_ratio_var"))
        var_d = float(dz @ sigma @ dz) + (ext_var if (ext_var is not None and ext_var >= 0.0) else 0.0)
        denom = d * d + var_d
        w = (d * d / denom) if denom > 0.0 else 0.0
        r = math.exp(log_r0 + w * d)
        total = lam_h + lam_a
        lam_h, lam_a = total * r / (1.0 + r), total / (1.0 + r)
        return lam_h, lam_a, {"ratio_weight": w, "ratio_var": var_d, "raw_log_ratio_dev": d}

    # ------------------------------------------------------------------ lattice
    @staticmethod
    def _indep_draw(lh: float, la: float) -> float:
        """Exact independent-Poisson draw probability:
        exp(-lh-la) * I0(2*sqrt(lh*la)), computed stably."""
        return math.exp(-(math.sqrt(lh) - math.sqrt(la)) ** 2) * float(special.i0e(2.0 * math.sqrt(lh * la)))

    def _shared_frac(self) -> float:
        # Validity domain of the bivariate Poisson: 0 <= lambda3 <= min(lh, la).
        return float(np.clip(self._cov_num / self._cov_den, 0.0, 1.0))

    def _draw_ratio(self) -> float:
        return self._draw_a / self._draw_b

    def _bivariate(self, lh: float, la: float, c: float, n: int) -> np.ndarray:
        l3 = c * min(lh, la)
        l1, l2 = lh - l3, la - l3
        k = np.arange(n + 1)
        p1, p2, p3 = stats.poisson.pmf(k, l1), stats.poisson.pmf(k, l2), stats.poisson.pmf(k, l3)
        m = np.zeros((n + 1, n + 1))
        for j in range(n + 1):
            if p3[j] <= 0.0:
                break  # Poisson(l3 <= ~1) is monotone decreasing, so the rest is zero
            m[j:, j:] += p3[j] * np.outer(p1[: n + 1 - j], p2[: n + 1 - j])
        return m / m.sum()

    @staticmethod
    def _apply_dc(m: np.ndarray, lh: float, la: float, theta: float) -> np.ndarray:
        out = m.copy()
        out[0, 0] *= 1.0 - lh * la * theta
        out[0, 1] *= 1.0 + lh * theta
        out[1, 0] *= 1.0 + la * theta
        out[1, 1] *= 1.0 - theta
        return out / out.sum()

    def _solve_theta(self, base: np.ndarray, lh: float, la: float, target: float) -> float:
        # Non-negativity domain of the Dixon-Coles tau.
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)

        def f(t: float) -> float:
            return float(np.trace(self._apply_dc(base, lh, la, t))) - target

        f_lo, f_hi = f(lo), f(hi)
        if f_lo * f_hi > 0.0:
            # Target unreachable inside the valid domain: take the nearest feasible point.
            return lo if abs(f_lo) < abs(f_hi) else hi
        return float(optimize.brentq(f, lo, hi, xtol=1e-12))

    def _score_matrix(self, lh: float, la: float) -> Tuple[np.ndarray, float, float]:
        n = max(int(stats.poisson.isf(self.tail_eps, lh + la)) + 1, 2)
        c = self._shared_frac()
        base = self._bivariate(lh, la, c, n)
        target = self._draw_ratio() * self._indep_draw(lh, la)
        theta = self._solve_theta(base, lh, la, target)
        return self._apply_dc(base, lh, la, theta), theta, c

    # ------------------------------------------------------------------ output
    @staticmethod
    def _to_distribution(m: np.ndarray):
        scores = {(int(i), int(j)): float(m[i, j]) for i, j in zip(*np.nonzero(m > 0.0))}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
        try:
            return DiscreteDistribution.from_scores(m)
        except (TypeError, ValueError, AttributeError, KeyError):
            pass
        p_home = float(np.tril(m, -1).sum())
        p_draw = float(np.trace(m))
        p_away = float(np.triu(m, 1).sum())
        return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lh, la, diag = self._means(ctx)
        m, theta, c = self._score_matrix(lh, la)
        self._last = {
            "lambda_home": lh,
            "lambda_away": la,
            "dc_theta": theta,
            "shared_frac": c,
            "draw_ratio": self._draw_ratio(),
            "p_draw": float(np.trace(m)),
            "n_obs": self._n_obs,
            **diag,
        }
        return self._to_distribution(m)

    # ------------------------------------------------------------------ pricing
    @staticmethod
    def margin_pmf(m: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        n = m.shape[0] - 1
        idx = np.subtract.outer(np.arange(n + 1), np.arange(n + 1)) + n
        pmf = np.bincount(idx.ravel(), weights=m.ravel(), minlength=2 * n + 1)
        return np.arange(-n, n + 1), pmf

    def price_spread(
        self, game_context: Dict[str, Any], line: float, odds: float, side: str = "home"
    ) -> Dict[str, Any]:
        """Price an Asian handicap. `line` is applied to `side`, e.g.
        home -0.75 settles on home_margin - 0.75. Quarter lines are split into
        two half-stakes on line +/- 0.25."""
        ctx = game_context or {}
        lh, la, _ = self._means(ctx)
        m, _, _ = self._score_matrix(lh, la)
        margins, pmf = self.margin_pmf(m)
        if str(side).lower().startswith("a"):
            margins = -margins

        q = line * 4.0
        halves = [line - 0.25, line + 0.25] if int(round(q)) % 2 != 0 else [line]
        tol = 1e-9
        win_frac = np.zeros_like(pmf)
        loss_frac = np.zeros_like(pmf)
        for h in halves:
            adj = margins + h
            win_frac += (adj > tol).astype(float) / len(halves)
            loss_frac += (adj < -tol).astype(float) / len(halves)
        push_frac = 1.0 - win_frac - loss_frac

        w_mass = float(np.sum(pmf * win_frac))
        l_mass = float(np.sum(pmf * loss_frac))
        ev = w_mass * (odds - 1.0) - l_mass
        fair = (1.0 + l_mass / w_mass) if w_mass > 0.0 else math.inf

        def mass(mask: np.ndarray) -> float:
            return float(pmf[mask].sum())

        return {
            "side": side,
            "line": line,
            "odds": odds,
            "p_win": mass(win_frac > 1 - tol),
            "p_half_win": mass((win_frac > tol) & (win_frac < 1 - tol)),
            "p_push": mass(push_frac > 1 - tol),
            "p_half_loss": mass((loss_frac > tol) & (loss_frac < 1 - tol)),
            "p_loss": mass(loss_frac > 1 - tol),
            "expected_value": ev,
            "fair_odds": fair,
            "eligible": bool(odds >= self.min_odds and ev > 0.0),
            "lambda_home": lh,
            "lambda_away": la,
        }

    # ------------------------------------------------------------------ learning
    def observe(self, game_context: Dict[str, Any], home_goals: int, away_goals: int) -> None:
        """Online empirical-Bayes update from one settled first half."""
        ctx = game_context or {}
        x, y = int(home_goals), int(away_goals)

        # Predictive means used for the draw and covariance sufficient statistics.
        lh_pred, la_pred, _ = self._means(ctx)

        # Laplace / assumed-density step for the Poisson log-linear posterior.
        base_h, base_a, z_h, z_a = self._design(ctx)
        mu_h = math.exp(math.log(base_h) + float(z_h @ self._beta))
        mu_a = math.exp(math.log(base_a) + float(z_a @ self._beta))
        grad = z_h * (x - mu_h) + z_a * (y - mu_a)
        hess = mu_h * np.outer(z_h, z_h) + mu_a * np.outer(z_a, z_a)
        self._prec = self._prec + hess
        self._beta = self._beta + np.linalg.solve(self._prec, grad)

        # Draw-inflation ratio (Gamma-Poisson conjugate in exposure units).
        self._draw_a += 1.0 if x == y else 0.0
        self._draw_b += self._indep_draw(lh_pred, la_pred)

        # Shared component: Cov(X, Y) = lambda3 = c * min(lh, la).
        self._cov_num += (x - lh_pred) * (y - la_pred)
        self._cov_den += min(lh_pred, la_pred)

        self._update_feature_stats(self._raw_features(ctx))
        self._n_obs += 1

    def diagnostics(self) -> Dict[str, Any]:
        return dict(self._last)