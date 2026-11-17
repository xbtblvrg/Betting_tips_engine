"""
Candidate model for NBA | half_1 | home_total.

The model is a possession-level compound process with a shared latent pace:

    HomePts_H1 = sum_{i=1..N} X_i^H,    AwayPts_H1 = sum_{i=1..N} X_i^A
    N   ~ discretised Normal(N_bar, sigma_N)      shared half-game possession count
    X_i ~ exponentially tilted possession-outcome law on {0,1,2,3,4},
          tilted so that E[X] = PPP * exp(eps - tau^2/2)
    eps ~ Normal(0, tau^2)                        game-level shooting/efficiency shock

The shared N carries the pace correlation between the two teams' scores.
Variance comes from three structural sources: per-possession outcome variance,
pace variance, and efficiency-shock variance. No variance cap or clamp is applied.

Note on the reported "Dispersion Index = 0.187": a Var/Mean below 1 is
under-dispersion, which contradicts the reported "heavy right tail". Even a pure
Poisson count has Var/Mean = 1. NBA team-half points are structurally
over-dispersed: possession outcomes alone give Var/Mean of about 1.2.
So 0.187 cannot describe raw home first-half points. It most likely refers to a
residual or settled-outcome statistic. This model therefore derives dispersion
structurally and exposes `calibrate()` to fit (tau, home efficiency bias) by
maximum likelihood on historical home first-half points. It does not force the
0.187 figure into the model.

The benchmark ROIs (+122%, +90%) are far outside what an honest out-of-sample
totals model can sustain. Treat them as likely artefacts of small samples or
leakage, not as targets.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally exported next to the base class.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # Otherwise it is provided by the pre-loaded execution scope.
    pass


class NBAFirstHalfHomeTotalModel(BaseCandidateModel):
    """Compound possession model for NBA first-half home team points."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_075814",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        *,
        # Empirical anchors (first-half team points).
        baseline_home: float = 56.0,
        baseline_away: float = 54.0,
        # Possession outcome shape on {0,1,2,3,4} points (league frequencies);
        # the exponential tilt moves its mean to the matchup PPP while keeping
        # the shape of the scoring lattice.
        possession_outcome_shape: Sequence[float] = (0.475, 0.055, 0.330, 0.130, 0.010),
        # Game-to-game sd of first-half possessions.
        half_possession_sd: float = 3.0,
        # Game-level efficiency shock (log-PPP sd); refit via calibrate().
        efficiency_shock_sd: float = 0.045,
        # Multiplicative home PPP bias; refit via calibrate().
        home_efficiency_bias: float = 1.0,
        # Precision-weighted fusion of prior / input / rating-based means.
        prior_team_sd: float = 5.0,
        input_sd: float = 2.0,
        rating_sd: float = 3.5,
        # Fraction of full-game scoring attributable to the first half; used
        # only if inputs arrive on a full-game scale.
        half_share: float = 0.5,
        # Reference for per-100 style ratings when ratings are not multiplicative.
        league_rating_ref: float = 114.0,
        # Rest / fatigue dynamics (smooth exponential recovery, no step filters).
        rest_fatigue_amp: float = 0.025,
        rest_decay_days: float = 1.0,
        reference_rest_days: float = 2.0,
        defensive_rest_weight: float = 0.5,
        # Numerical controls.
        quadrature_nodes: int = 7,
        pace_sd_span: float = 5.0,
        tail_mass: float = 1e-12,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.baseline_home = float(baseline_home)
        self.baseline_away = float(baseline_away)

        shape = np.asarray(possession_outcome_shape, dtype=float)
        if shape.ndim != 1 or shape.size < 2 or np.any(shape < 0) or shape.sum() <= 0:
            raise ValueError("possession_outcome_shape must be a non-negative 1-D vector.")
        self._shape = shape / shape.sum()
        self._k = np.arange(self._shape.size, dtype=float)
        self._log_shape = np.log(np.where(self._shape > 0, self._shape, np.finfo(float).tiny))
        self._kmax = float(self._k[-1])
        self._shape_mean = float(self._shape @ self._k)

        self.half_possession_sd = float(half_possession_sd)
        self.efficiency_shock_sd = float(efficiency_shock_sd)
        self.home_efficiency_bias = float(home_efficiency_bias)
        self.prior_team_sd = float(prior_team_sd)
        self.input_sd = float(input_sd)
        self.rating_sd = float(rating_sd)
        self.half_share = float(half_share)
        self.league_rating_ref = float(league_rating_ref)
        self.rest_fatigue_amp = float(rest_fatigue_amp)
        self.rest_decay_days = float(rest_decay_days)
        self.reference_rest_days = float(reference_rest_days)
        self.defensive_rest_weight = float(defensive_rest_weight)
        self.quadrature_nodes = int(quadrature_nodes)
        self.pace_sd_span = float(pace_sd_span)
        self.tail_mass = float(tail_mass)

        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _side_value(self, ctx: Dict[str, Any], side: str, names: Sequence[str]) -> Optional[float]:
        for name in names:
            v = self._num(ctx.get(f"{side}_{name}"))
            if v is not None:
                return v
        sub = ctx.get(side)
        if isinstance(sub, dict):
            for name in names:
                v = self._num(sub.get(name))
                if v is not None:
                    return v
        return None

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        aliases = {
            "attack": ("attack", "offense", "offence", "off_rating", "ortg"),
            "defense": ("defense", "defence", "def_rating", "drtg"),
        }[kind]
        v = self._side_value(ctx, side, aliases)
        if v is None or v <= 0:
            return None
        # Per-100-possession ratings are converted to multiplicative form.
        if v > 10.0:
            v = v / self.league_rating_ref
        return v

    def _to_half_scale(self, value: Optional[float], baseline: float) -> Optional[float]:
        if value is None or value <= 0:
            return None
        # Full-game inputs sit near 2x the half anchor; half inputs near 1x.
        # The midpoint in log space separates the two scales.
        if value > baseline * math.sqrt(1.0 / self.half_share):
            return value * self.half_share
        return value

    @staticmethod
    def _precision_combine(estimates: List[Tuple[float, float]]) -> float:
        w = np.array([1.0 / (sd * sd) for _, sd in estimates])
        m = np.array([mu for mu, _ in estimates])
        return float((w @ m) / w.sum())

    def _fatigue(self, rest_days: Optional[float]) -> float:
        r = self.reference_rest_days if rest_days is None or rest_days < 0 else rest_days
        d = self.rest_decay_days
        return self.rest_fatigue_amp * (math.exp(-r / d) - math.exp(-self.reference_rest_days / d))

    def _team_mean(self, ctx: Dict[str, Any], side: str, opp: str, baseline: float) -> float:
        estimates: List[Tuple[float, float]] = [(baseline, self.prior_team_sd)]

        expected = self._to_half_scale(self._num(ctx.get(f"{side}_expected")), baseline)
        if expected is not None:
            estimates.append((expected, self.input_sd))

        att = self._rating(ctx, side, "attack")
        dfn = self._rating(ctx, opp, "defense")
        if att is not None or dfn is not None:
            estimates.append((baseline * (att or 1.0) * (dfn or 1.0), self.rating_sd))

        return self._precision_combine(estimates)

    def _half_possessions(self, ctx: Dict[str, Any], mu_h: float, mu_a: float) -> float:
        paces = [self._num(ctx.get("pace"))]
        if paces[0] is None:
            paces = [p for p in (self._side_value(ctx, "home", ("pace",)),
                                 self._side_value(ctx, "away", ("pace",))) if p is not None]
        paces = [p for p in paces if p is not None and p > 0]
        if paces:
            pace = float(np.mean(paces))
            # Per-48 pace (about 100) becomes first-half possessions; a value
            # already on the half scale is kept as is.
            implied_half = (mu_h + mu_a) / (2.0 * self._shape_mean)
            return pace * self.half_share if pace > implied_half * math.sqrt(1.0 / self.half_share) else pace
        return (mu_h + mu_a) / (2.0 * self._shape_mean)

    # ------------------------------------------------------------------ #
    # Possession-level probability engine
    # ------------------------------------------------------------------ #
    def _tilted_pmf(self, target_mean: float) -> np.ndarray:
        eps = 1e-9
        target = min(max(target_mean, eps), self._kmax - eps)

        def gap(theta: float) -> float:
            return float(special.softmax(self._log_shape + theta * self._k) @ self._k) - target

        lo, hi = -1.0, 1.0
        while gap(lo) > 0:
            lo *= 2.0
        while gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(gap, lo, hi, xtol=1e-12)
        return special.softmax(self._log_shape + theta * self._k)

    def _possession_grid(self, n_bar: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = self.half_possession_sd
        if sd <= 0:
            lo = math.floor(n_bar)
            frac = n_bar - lo
            n_vals = np.array([max(lo, 1), max(lo + 1, 1)], dtype=int)
            w = np.array([1.0 - frac, frac])
        else:
            lo = max(1, int(math.floor(n_bar - self.pace_sd_span * sd)))
            hi = max(lo + 1, int(math.ceil(n_bar + self.pace_sd_span * sd)))
            n_vals = np.arange(lo, hi + 1, dtype=int)
            w = stats.norm.cdf(n_vals + 0.5, n_bar, sd) - stats.norm.cdf(n_vals - 0.5, n_bar, sd)
        w = np.clip(w, 0.0, None)
        return n_vals, w / w.sum()

    def _conditional_stack(self, ppp: float, n_vals: np.ndarray, fft_size: int) -> np.ndarray:
        """Rows: P(points | N = n) after mixing over the efficiency shock."""
        x, gw = np.polynomial.hermite_e.hermegauss(self.quadrature_nodes)
        gw = gw / gw.sum()
        tau = self.efficiency_shock_sd
        node_ppp = ppp * np.exp(tau * x - 0.5 * tau * tau)

        spectra = np.stack([np.fft.rfft(self._tilted_pmf(m), fft_size) for m in node_ppp])  # (J, F)
        powered = spectra[None, :, :] ** n_vals[:, None, None]                              # (K, J, F)
        pmfs = np.fft.irfft(powered, fft_size, axis=-1)                                     # (K, J, M)
        stack = np.einsum("j,kjm->km", gw, pmfs)
        stack = np.clip(stack, 0.0, None)  # removes FFT round-off only
        return stack / stack.sum(axis=1, keepdims=True)

    def _fft_size(self, n_vals: np.ndarray) -> int:
        support = int(self._kmax * n_vals.max()) + 1
        return 1 << int(math.ceil(math.log2(support + 1)))

    def _cutoff(self, marginal: np.ndarray) -> int:
        cum = np.cumsum(marginal)
        return int(min(np.searchsorted(cum, 1.0 - self.tail_mass) + 1, marginal.size))

    def _structural_inputs(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        mu_h = self._team_mean(ctx, "home", "away", self.baseline_home)
        mu_a = self._team_mean(ctx, "away", "home", self.baseline_away)
        n_bar = self._half_possessions(ctx, mu_h, mu_a)

        rest_h = self._num(ctx.get("home_rest_days"))
        rest_a = self._num(ctx.get("away_rest_days"))
        f_h, f_a = self._fatigue(rest_h), self._fatigue(rest_a)
        mult_h = (1.0 - f_h) * (1.0 + self.defensive_rest_weight * f_a)
        mult_a = (1.0 - f_a) * (1.0 + self.defensive_rest_weight * f_h)

        ppp_h = mu_h / n_bar * mult_h * self.home_efficiency_bias
        ppp_a = mu_a / n_bar * mult_a
        return {"mu_home": mu_h, "mu_away": mu_a, "n_bar": n_bar,
                "ppp_home": ppp_h, "ppp_away": ppp_a}

    def home_marginal(self, ctx: Dict[str, Any]) -> np.ndarray:
        s = self._structural_inputs(ctx)
        n_vals, w = self._possession_grid(s["n_bar"])
        m = self._fft_size(n_vals)
        marginal = w @ self._conditional_stack(s["ppp_home"], n_vals, m)
        marginal = marginal / marginal.sum()
        return marginal

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        s = self._structural_inputs(ctx)
        n_vals, w = self._possession_grid(s["n_bar"])
        m = self._fft_size(n_vals)

        home_stack = self._conditional_stack(s["ppp_home"], n_vals, m)
        away_stack = self._conditional_stack(s["ppp_away"], n_vals, m)

        # Joint law: the shared possession count induces pace correlation.
        joint = np.einsum("k,ki,kj->ij", w, home_stack, away_stack)
        hi_h = self._cutoff(joint.sum(axis=1))
        hi_a = self._cutoff(joint.sum(axis=0))
        joint = np.clip(joint[:hi_h, :hi_a], 0.0, None)
        joint /= joint.sum()

        home_marg = joint.sum(axis=1)
        pts = np.arange(home_marg.size, dtype=float)
        mean_h = float(home_marg @ pts)
        var_h = float(home_marg @ (pts - mean_h) ** 2)
        away_marg = joint.sum(axis=0)
        pts_a = np.arange(away_marg.size, dtype=float)
        mean_a = float(away_marg @ pts_a)
        var_a = float(away_marg @ (pts_a - mean_a) ** 2)
        cov = float(pts @ joint @ pts_a) - mean_h * mean_a
        self.last_diagnostics = {
            **s,
            "home_mean": mean_h,
            "home_sd": math.sqrt(var_h),
            "home_dispersion_index": var_h / mean_h if mean_h > 0 else float("nan"),
            "pace_correlation": cov / math.sqrt(var_h * var_a) if var_h > 0 and var_a > 0 else 0.0,
        }

        scores = {
            (int(i), int(j)): float(joint[i, j])
            for i, j in zip(*np.nonzero(joint > self.tail_mass))
        }
        total = sum(scores.values())
        scores = {key: p / total for key, p in scores.items()}

        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            try:
                return DiscreteDistribution.from_scores(joint)
            except (TypeError, ValueError, AttributeError):
                return DiscreteDistribution(
                    probabilities={str(i): float(p) for i, p in enumerate(home_marg) if p > 0}
                )

    def calibrate(
        self,
        games: Iterable[Dict[str, Any]],
        outcome_key: str = "home_points",
        context_key: str = "game_context",
    ) -> Dict[str, float]:
        """
        Maximum-likelihood fit of (efficiency_shock_sd, home_efficiency_bias)
        on settled historical games. Each item is either a context dict that
        contains `outcome_key`, or a dict with `context_key` plus `outcome_key`.
        Returns the fitted parameters and the mean log-likelihood.
        """
        data: List[Tuple[Dict[str, Any], int]] = []
        for g in games:
            ctx = g.get(context_key, g) if isinstance(g, dict) else None
            y = self._num(g.get(outcome_key)) if isinstance(g, dict) else None
            if isinstance(ctx, dict) and y is not None and y >= 0:
                data.append((ctx, int(round(y))))
        if not data:
            return {"efficiency_shock_sd": self.efficiency_shock_sd,
                    "home_efficiency_bias": self.home_efficiency_bias,
                    "mean_log_lik": float("nan")}

        floor = np.finfo(float).tiny
        saved = (self.efficiency_shock_sd, self.home_efficiency_bias)

        def nll(theta: np.ndarray) -> float:
            self.efficiency_shock_sd = float(np.exp(theta[0]))
            self.home_efficiency_bias = float(np.exp(theta[1]))
            total = 0.0
            for ctx, y in data:
                marg = self.home_marginal(ctx)
                p = marg[y] if y < marg.size else 0.0
                total -= math.log(max(p, floor))
            return total / len(data)

        x0 = np.array([math.log(max(saved[0], 1e-4)), math.log(saved[1])])
        try:
            res = optimize.minimize(nll, x0, method="Nelder-Mead",
                                    options={"xatol": 1e-4, "fatol": 1e-7, "maxiter": 400})
            self.efficiency_shock_sd = float(np.exp(res.x[0]))
            self.home_efficiency_bias = float(np.exp(res.x[1]))
            ll = -float(res.fun)
        except (ValueError, FloatingPointError, OverflowError):
            self.efficiency_shock_sd, self.home_efficiency_bias = saved
            ll = float("nan")

        return {"efficiency_shock_sd": self.efficiency_shock_sd,
                "home_efficiency_bias": self.home_efficiency_bias,
                "mean_log_lik": ll}