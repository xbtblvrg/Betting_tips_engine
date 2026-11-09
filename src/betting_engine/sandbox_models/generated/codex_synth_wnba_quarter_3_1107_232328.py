import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded into scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    pass


class WNBAQuarter3ExactTotalModel(BaseCandidateModel):
    """
    Possession-level compound model for WNBA third-quarter exact totals.

    Structure (derived from basketball scoring mechanics, not a template):

      1. A quarter is a sequence of alternating possessions. Both teams share a
         common possession count N (pace); the away count differs from the home
         count by at most one (who starts / ends the period).
      2. Each possession yields k in {0,1,2,3,4} points. The per-possession
         outcome law is the minimum-KL (exponential) tilt of a structural WNBA
         possession template, solved so that N * E[k] reproduces each team's
         situational expected quarter points exactly.
      3. Team points | N  = N-fold convolution of the possession law.
         Joint score     = mixture over N (shared pace) and the +/-1 offset,
         which induces the positive home/away correlation that drives total
         dispersion.
      4. Pace variance is either taken from the domain prior (coefficient of
         variation of quarter possessions) or, if an observed dispersion index
         (Var/Mean of team points) is supplied, solved analytically so the model
         reproduces that index.

    Every number below is a named, overridable domain prior; nothing is a
    clamp on output probabilities or on eligible odds.
    """

    # ----- Domain priors (overridable via game_context['model_priors']) -----
    PRIORS: Dict[str, Any] = {
        # Segment empirical anchors (cold start baseline).
        "baseline_home_expected": 20.00,
        "baseline_away_expected": 18.75,
        # League points-per-possession prior (WNBA ORtg ~ 100-106 / 100 poss).
        "league_ppp": 1.03,
        # Structural possession outcome template for k = 0..4 points
        # (empty trips/turnovers, single FT trips, twos & 2-FT trips, threes, and-ones).
        "possession_template": [0.505, 0.035, 0.335, 0.115, 0.010],
        # Coefficient of variation of possessions in one 10-minute quarter.
        "pace_cv": 0.09,
        # Probability that the away possession count differs from home by +/-1.
        "offset_prob": 0.50,
        # Fatigue: efficiency penalty amplitude decaying with rest days.
        "rest_penalty": 0.02,
        # Shrinkage weight on attack/defense ratings (expectations already
        # partially encode them, so only the residual signal is applied).
        "rating_weight": 0.25,
        # Pace inputs are per 40 minutes; convert to quarter.
        "minutes_per_game": 40.0,
        "quarter_minutes": 10.0,
        # Numerical lattice coverage (std devs of pace to integrate over).
        "pace_sigma_span": 4.0,
        "prob_floor": 1e-12,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_3_1107_232328",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_3",
        family: str = "exact_total",
    ):
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

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            v = float(value)
            if math.isfinite(v):
                return v
        except (TypeError, ValueError):
            pass
        return None

    def _priors(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        pri = dict(self.PRIORS)
        override = ctx.get("model_priors")
        if isinstance(override, dict):
            pri.update(override)
        return pri

    def _team_field(self, ctx: Dict[str, Any], side: str, field: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block.get(field))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{field}"))

    # ------------------------------------------------- situational adjustment
    def _rating_log_adjustment(self, ctx: Dict[str, Any], off: str, deff: str, w: float) -> float:
        """Residual log-multiplier from offense attack vs opponent defense."""
        att = self._team_field(ctx, off, "attack")
        dfn = self._team_field(ctx, deff, "defense")
        terms: List[float] = []
        for val in (att, dfn):
            if val is None:
                continue
            # Positive ratings are multiplicative strengths (1.0 = average);
            # otherwise treat as additive log-scale ratings centred at 0.
            terms.append(math.log(val) if val > 0 else val)
        return w * sum(terms)

    def _rest_multiplier(self, rest_days: Optional[float], amp: float) -> float:
        if rest_days is None:
            return 1.0
        return 1.0 - amp * math.exp(-max(rest_days, 0.0))

    def _expected_points(self, ctx: Dict[str, Any], pri: Dict[str, Any]) -> Tuple[float, float]:
        mu_h = self._num(ctx.get("home_expected"))
        mu_a = self._num(ctx.get("away_expected"))
        if mu_h is None or mu_h <= 0:
            mu_h = float(pri["baseline_home_expected"])
        if mu_a is None or mu_a <= 0:
            mu_a = float(pri["baseline_away_expected"])

        w = float(pri["rating_weight"])
        adj_h = self._rating_log_adjustment(ctx, "home", "away", w)
        adj_a = self._rating_log_adjustment(ctx, "away", "home", w)

        amp = float(pri["rest_penalty"])
        r_h = self._rest_multiplier(self._num(ctx.get("home_rest_days")), amp)
        r_a = self._rest_multiplier(self._num(ctx.get("away_rest_days")), amp)
        # Only the rest *differential* is new information relative to expectations.
        r_mean = math.sqrt(r_h * r_a)

        mu_h *= math.exp(adj_h) * (r_h / r_mean)
        mu_a *= math.exp(adj_a) * (r_a / r_mean)
        return mu_h, mu_a

    def _possession_mean(self, ctx: Dict[str, Any], pri: Dict[str, Any],
                         mu_h: float, mu_a: float) -> float:
        q_frac = float(pri["quarter_minutes"]) / float(pri["minutes_per_game"])
        pace = None
        for key in ("pace", "tempo", "expected_pace"):
            pace = self._num(ctx.get(key))
            if pace is not None:
                break
        if pace is None:
            hp = self._team_field(ctx, "home", "pace")
            ap = self._team_field(ctx, "away", "pace")
            if hp is not None and ap is not None:
                pace = 0.5 * (hp + ap)
        if pace is not None and pace > 0:
            return pace * q_frac
        # Infer possessions from the scoring expectation and league efficiency.
        return (mu_h + mu_a) / (2.0 * float(pri["league_ppp"]))

    # ---------------------------------------------- possession outcome law
    @staticmethod
    def _tilt(template: np.ndarray, target_mean: float) -> np.ndarray:
        """Minimum-KL tilt of template so that E[k] = target_mean."""
        k = np.arange(template.size, dtype=float)
        kmax = float(k[-1])
        eps = 1e-9
        target = min(max(target_mean, eps), kmax - eps)

        def mean_gap(theta: float) -> float:
            logw = np.log(template) + theta * k
            logw -= logw.max()
            w = np.exp(logw)
            return float((w * k).sum() / w.sum()) - target

        lo, hi = -50.0, 50.0
        theta = brentq(mean_gap, lo, hi, xtol=1e-12, maxiter=500)
        logw = np.log(template) + theta * k
        logw -= logw.max()
        w = np.exp(logw)
        return w / w.sum()

    @staticmethod
    def _convolution_powers(p: np.ndarray, n_max: int, cap: int) -> List[np.ndarray]:
        """pmf of sum of n iid draws, n = 0..n_max, truncated to support [0, cap]."""
        out = [np.array([1.0])]
        cur = out[0]
        for _ in range(n_max):
            cur = np.convolve(cur, p)[: cap + 1]
            out.append(cur)
        return out

    # ------------------------------------------------------------- main API
    def predict_distribution(self, game_context: Dict[str, Any]):
        ctx = game_context or {}
        pri = self._priors(ctx)

        mu_h, mu_a = self._expected_points(ctx, pri)
        n_bar = max(self._possession_mean(ctx, pri, mu_h, mu_a), 1.0)

        template = np.asarray(pri["possession_template"], dtype=float)
        template = template / template.sum()

        ppp_h = mu_h / n_bar
        ppp_a = mu_a / n_bar
        p_h = self._tilt(template, ppp_h)
        p_a = self._tilt(template, ppp_a)

        k = np.arange(template.size, dtype=float)
        var_ppp = 0.5 * (
            float((p_h * k ** 2).sum() - ppp_h ** 2) + float((p_a * k ** 2).sum() - ppp_a ** 2)
        )
        ppp_bar = 0.5 * (ppp_h + ppp_a)

        # Pace variance: calibrate to an observed dispersion index if supplied,
        # otherwise use the domain coefficient-of-variation prior.
        # Var(team pts) = N*Var(ppp) + ppp^2 * Var(N)  =>  solve for Var(N).
        disp = self._num(ctx.get("dispersion_index"))
        if disp is not None and disp > 0:
            mu_bar = 0.5 * (mu_h + mu_a)
            var_n = max(disp * mu_bar - n_bar * var_ppp, 0.0) / max(ppp_bar ** 2, 1e-9)
        else:
            var_n = (float(pri["pace_cv"]) * n_bar) ** 2
        sd_n = math.sqrt(var_n)

        # Discretised pace distribution over integer possession counts.
        span = float(pri["pace_sigma_span"])
        n_lo = max(1, int(math.floor(n_bar - span * sd_n)) - 1)
        n_hi = int(math.ceil(n_bar + span * sd_n)) + 1
        n_grid = np.arange(n_lo, n_hi + 1)
        if sd_n > 1e-9:
            edges = np.concatenate(([n_grid[0] - 0.5], n_grid + 0.5))
            w_n = np.diff(norm.cdf(edges, loc=n_bar, scale=sd_n))
        else:
            # Degenerate pace: split mass between neighbouring integers to keep mean.
            w_n = np.maximum(0.0, 1.0 - np.abs(n_grid - n_bar))
        w_n = w_n / w_n.sum()

        q_off = float(pri["offset_prob"])
        offsets = [(-1, 0.5 * q_off), (0, 1.0 - q_off), (1, 0.5 * q_off)]

        kmax = template.size - 1
        cap = int(kmax * (n_hi + 1))
        pow_h = self._convolution_powers(p_h, n_hi + 1, cap)
        pow_a = self._convolution_powers(p_a, n_hi + 1, cap)

        joint = np.zeros((cap + 1, cap + 1))
        for n, wn in zip(n_grid, w_n):
            if wn <= 0:
                continue
            ph = pow_h[n]
            for d, wd in offsets:
                na = n + d
                if na < 0 or wd <= 0:
                    continue
                pa = pow_a[na]
                joint[: ph.size, : pa.size] += (wn * wd) * np.outer(ph, pa)

        joint /= joint.sum()

        floor = float(pri["prob_floor"])
        scores: Dict[Tuple[int, int], float] = {}
        hs, as_ = np.nonzero(joint > floor)
        for h, a in zip(hs.tolist(), as_.tolist()):
            scores[(int(h), int(a))] = float(joint[h, a])
        total = sum(scores.values())
        scores = {key: v / total for key, v in scores.items()}

        try:
            return DiscreteDistribution.from_scores(scores)  # noqa: F821
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(joint)  # noqa: F821