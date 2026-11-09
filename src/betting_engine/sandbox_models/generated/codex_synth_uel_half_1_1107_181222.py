"""
Candidate model for UEL | half_1 | totals.

Architecture
------------
1. Time-varying goal hazard  h(t) = exp(beta * t / 90), integrated over first-half
   regulation plus stoppage, gives the share of match goal intensity that falls
   before half-time. This replaces flat 45% prorating. beta is not a free
   constant. It is solved in closed form from a domain prior on the first-half
   goal share, then refined by penalised MLE on minute-level goal timestamps
   when they are supplied to `fit`.
2. Situational intensity. Each side's first-half rate is built in log space:
   the segment baseline (0.80 / 0.60), shrunk toward the supplied expectation,
   then scaled by attack/defence exposure, tempo, rest differential, weather
   and knockout aggregate state. All of these are elasticities with neutral
   points, not clamps.
3. Dixon-Coles low-score dependence with rho modulated by stage and leg. The
   admissible rho interval is computed from the rates, so the correction cannot
   produce negative mass.
4. Shared gamma frailty (shape kappa) on both rates. This captures match-level
   overdispersion such as rotation and lineup uncertainty. The prior is Poisson
   (1/kappa = 0). `fit` updates it by empirical-Bayes moment shrinkage on
   settled first-half totals.

The output is a full first-half score lattice, normalised, with dynamically
sized support.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # resolve DiscreteDistribution from wherever the engine exposes it
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded scope


# --------------------------------------------------------------------------- #
# Domain priors. These are documented beliefs that data can update, not clamps.
# --------------------------------------------------------------------------- #
@dataclass
class UELHalf1Priors:
    # Segment empirical anchors (first-half goal means).
    baseline_home: float = 0.80
    baseline_away: float = 0.60
    # Pseudo-match weight of the baseline anchors in conjugate updating.
    baseline_pseudo_n: float = 40.0
    # Trust placed on the supplied expectation relative to the anchor (log-space).
    expectation_weight: float = 0.80

    # Hazard: prior share of regulation goals scored in the first half for
    # top-level European club football, and its uncertainty in beta units.
    first_half_share: float = 0.445
    beta_prior_sd: float = 0.25
    stoppage_h1: float = 2.0
    stoppage_h2: float = 5.0

    # Residual elasticities. The supplied expectation already carries most of
    # the team information, so ratings enter at partial elasticity.
    attack_elasticity: float = 0.35
    defence_elasticity: float = 0.35
    pace_elasticity: float = 0.60
    rest_elasticity: float = 0.04
    rest_reference_days: float = 3.0       # Thursday UEL vs weekend domestic cycle
    wind_semi_elasticity: float = 0.004    # per unit wind speed, open-air only

    # Knockout state.
    second_leg_caution: float = 0.06       # rate depression when tie is level
    aggregate_pressure: float = 0.06       # trailing side pushes, leader sits
    knockout_first_leg_caution: float = 0.02

    # Dixon-Coles dependence by stage.
    rho_by_stage: Dict[str, float] = field(default_factory=lambda: {
        "league_phase": -0.05,
        "first_leg": -0.08,
        "second_leg": -0.11,
        "final": -0.12,
    })
    rho_aggregate_decay: float = 1.0       # caginess decays as the aggregate gap widens

    # Dispersion: prior 1/kappa = 0 (Poisson), pseudo-matches of prior weight.
    inv_kappa_prior: float = 0.0
    dispersion_pseudo_n: float = 60.0
    frailty_nodes: int = 32

    # Support truncation tolerance (mass left in the tail).
    tail_tolerance: float = 1e-10


class UELHalf1TotalsModel(BaseCandidateModel):
    """First-half totals model for the UEFA Europa League."""

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_1_1107_181222",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_1",
        family: str = "totals",
        expected_scope: str = "half_1",
        priors: Optional[UELHalf1Priors] = None,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
        except TypeError:  # pragma: no cover - tolerate positional base signatures
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.priors = priors or UELHalf1Priors()
        # 'half_1' if supplied expectations are first-half scale, 'full' if full-match.
        self.expected_scope = expected_scope

        # Posterior state, initialised at the priors.
        self.mu_home = self.priors.baseline_home
        self.mu_away = self.priors.baseline_away
        self.beta = self._beta_from_share(self.priors.first_half_share)
        self.beta_prior_mean = self.beta
        self.inv_kappa = self.priors.inv_kappa_prior
        self.n_settled = 0

    # ------------------------------------------------------------------ #
    # Hazard mechanics
    # ------------------------------------------------------------------ #
    @staticmethod
    def _beta_from_share(share: float) -> float:
        """Closed-form inverse of share = 1 / (exp(beta/2) + 1) over 0..45 / 0..90."""
        return 2.0 * math.log((1.0 - share) / share)

    @staticmethod
    def _cum_hazard(beta: float, t0: float, t1: float) -> float:
        """Integral of exp(beta * t / 90) dt from t0 to t1 (minutes)."""
        if abs(beta) < 1e-12:
            return t1 - t0
        k = beta / 90.0
        return (math.exp(k * t1) - math.exp(k * t0)) / k

    def first_half_share(self, s1: Optional[float] = None, s2: Optional[float] = None) -> float:
        """Share of total match intensity (with stoppage) falling in the first half."""
        s1 = self.priors.stoppage_h1 if s1 is None else s1
        s2 = self.priors.stoppage_h2 if s2 is None else s2
        # The second half's clock restarts at 45, so stoppage extends each half's own window.
        h1 = self._cum_hazard(self.beta, 0.0, 45.0 + s1)
        h2 = self._cum_hazard(self.beta, 45.0, 90.0 + s2)
        return h1 / (h1 + h2)

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            v = ctx.get(k)
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                return float(v)
        return None

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            v = self._num(block, kind, f"{kind}_rating", f"{kind}_strength")
            if v is not None:
                return v
        return self._num(ctx, f"{side}_{kind}", f"{side}_{kind}_rating")

    @staticmethod
    def _log_ratio(x: Optional[float], ref: float = 1.0) -> float:
        """Log multiplicative deviation from a neutral reference; neutral if unusable."""
        if x is None or x <= 0.0 or ref <= 0.0:
            return 0.0
        return math.log(x / ref)

    def _stage(self, ctx: Dict[str, Any]) -> Tuple[str, Optional[float]]:
        stage = str(ctx.get("stage") or ctx.get("round") or ctx.get("phase") or "").lower()
        leg = ctx.get("leg")
        agg = self._num(ctx, "aggregate_diff", "home_aggregate_diff")
        if "final" in stage and "semi" not in stage and "quarter" not in stage:
            return "final", None
        if leg in (2, "2", "second") or "second" in stage:
            return "second_leg", agg if agg is not None else 0.0
        if leg in (1, "1", "first") or "first" in stage or "knockout" in stage \
                or "playoff" in stage or "16" in stage:
            return "first_leg", None
        return "league_phase", None

    # ------------------------------------------------------------------ #
    # Intensity construction
    # ------------------------------------------------------------------ #
    def _rates(self, ctx: Dict[str, Any]) -> Tuple[float, float, str, Optional[float]]:
        p = self.priors

        # 1. Anchor: the supplied expectation, rescaled to first-half intensity.
        eh = self._num(ctx, "home_expected_h1", "home_expected_half_1")
        ea = self._num(ctx, "away_expected_h1", "away_expected_half_1")
        if eh is None or ea is None:
            eh_raw = self._num(ctx, "home_expected")
            ea_raw = self._num(ctx, "away_expected")
            scope = str(ctx.get("expected_scope", self.expected_scope)).lower()
            share = self.first_half_share(self._num(ctx, "stoppage_h1"),
                                          self._num(ctx, "stoppage_h2"))
            scale = share if scope.startswith("full") else 1.0
            eh = eh_raw * scale if eh_raw is not None else None
            ea = ea_raw * scale if ea_raw is not None else None

        w = p.expectation_weight
        log_h = math.log(self.mu_home)
        log_a = math.log(self.mu_away)
        if eh is not None and eh > 0:
            log_h = (1 - w) * log_h + w * math.log(eh)
        if ea is not None and ea > 0:
            log_a = (1 - w) * log_a + w * math.log(ea)

        # 2. Attack and defence exposure (multiplicative ratings, neutral at 1.0).
        #    A defence rating above 1 means more goals conceded.
        log_h += p.attack_elasticity * self._log_ratio(self._team_rating(ctx, "home", "attack"))
        log_h += p.defence_elasticity * self._log_ratio(self._team_rating(ctx, "away", "defense")
                                                        or self._team_rating(ctx, "away", "defence"))
        log_a += p.attack_elasticity * self._log_ratio(self._team_rating(ctx, "away", "attack"))
        log_a += p.defence_elasticity * self._log_ratio(self._team_rating(ctx, "home", "defense")
                                                        or self._team_rating(ctx, "home", "defence"))

        # 3. Tempo or pace, shared by both sides.
        pace = self._num(ctx, "pace", "tempo", "expected_pace")
        pace_ref = self._num(ctx, "pace_reference", "league_pace") or 1.0
        log_pace = p.pace_elasticity * self._log_ratio(pace, pace_ref)
        log_h += log_pace
        log_a += log_pace

        # 4. Rest. Own freshness raises attack; opponent freshness raises their defence.
        rh = self._num(ctx, "home_rest_days")
        ra = self._num(ctx, "away_rest_days")
        ref = 1.0 + p.rest_reference_days
        fh = math.log((1.0 + rh) / ref) if rh is not None and rh >= 0 else 0.0
        fa = math.log((1.0 + ra) / ref) if ra is not None and ra >= 0 else 0.0
        log_h += p.rest_elasticity * (fh - 0.5 * fa)
        log_a += p.rest_elasticity * (fa - 0.5 * fh)

        # 5. Weather. Wind degrades final-third execution in open-air venues.
        if not bool(ctx.get("is_dome", False)):
            wind = self._num(ctx, "wind_speed")
            if wind is not None and wind > 0:
                log_h -= p.wind_semi_elasticity * wind
                log_a -= p.wind_semi_elasticity * wind

        # 6. Knockout aggregate state.
        stage, agg = self._stage(ctx)
        if stage == "second_leg" and agg is not None:
            caution = p.second_leg_caution * math.exp(-abs(agg))
            log_h += -caution - p.aggregate_pressure * math.tanh(agg)
            log_a += -caution + p.aggregate_pressure * math.tanh(agg)
        elif stage in ("first_leg", "final"):
            log_h -= p.knockout_first_leg_caution
            log_a -= p.knockout_first_leg_caution

        return math.exp(log_h), math.exp(log_a), stage, agg

    def _rho(self, lam: float, mu: float, stage: str, agg: Optional[float]) -> float:
        p = self.priors
        rho = p.rho_by_stage.get(stage, p.rho_by_stage["league_phase"])
        if stage == "second_leg" and agg is not None:
            base = p.rho_by_stage["league_phase"]
            rho = base + (rho - base) * math.exp(-p.rho_aggregate_decay * abs(agg))
        # Admissible interval keeping every tau factor non-negative (a property of
        # the Dixon-Coles construction, not a tuning clamp).
        lo = max(-1.0 / lam, -1.0 / mu)
        hi = min(1.0 / (lam * mu), 1.0)
        return float(np.clip(rho, lo, hi))

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    def _frailty_nodes(self) -> Tuple[np.ndarray, np.ndarray]:
        if self.inv_kappa <= 0.0:
            return np.array([1.0]), np.array([1.0])
        kappa = 1.0 / self.inv_kappa
        n = self.priors.frailty_nodes
        q = (np.arange(n) + 0.5) / n                  # equal-probability quantisation
        x = stats.gamma.ppf(q, a=kappa, scale=1.0 / kappa)
        x = x / x.mean()                               # preserve E[frailty] = 1 exactly
        return x, np.full(n, 1.0 / n)

    def _support(self, lam: float, mu: float) -> int:
        tol = self.priors.tail_tolerance
        nodes, _ = self._frailty_nodes()
        peak = (lam + mu) * float(nodes.max())
        return int(stats.poisson.ppf(1.0 - tol, peak)) + 2

    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        lam, mu, stage, agg = self._rates(ctx)
        rho = self._rho(lam, mu, stage, agg)
        g = self._support(lam, mu)
        k = np.arange(g + 1)
        mat = np.zeros((g + 1, g + 1))
        nodes, weights = self._frailty_nodes()
        for z, wgt in zip(nodes, weights):
            l, m = lam * z, mu * z
            ph = stats.poisson.pmf(k, l)
            pa = stats.poisson.pmf(k, m)
            sub = np.outer(ph, pa)
            r = float(np.clip(rho, max(-1.0 / l, -1.0 / m), min(1.0 / (l * m), 1.0)))
            sub[0, 0] *= 1.0 - l * m * r
            sub[0, 1] *= 1.0 + l * r
            sub[1, 0] *= 1.0 + m * r
            sub[1, 1] *= 1.0 - r
            mat += wgt * sub
        mat = np.clip(mat, 0.0, None)
        return mat / mat.sum()

    def total_distribution(self, ctx: Dict[str, Any]) -> np.ndarray:
        mat = self.score_matrix(ctx)
        g = mat.shape[0]
        tot = np.zeros(2 * g - 1)
        for i in range(g):
            tot[i:i + g] += mat[i]
        return tot

    def line_probabilities(self, ctx: Dict[str, Any], line: float) -> Dict[str, float]:
        """Over/under/push for a single (half or integer) totals line."""
        tot = self.total_distribution(ctx)
        n = np.arange(tot.size)
        return {
            "over": float(tot[n > line].sum()),
            "under": float(tot[n < line].sum()),
            "push": float(tot[n == line].sum()),
        }

    # ------------------------------------------------------------------ #
    # Engine interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mat = self.score_matrix(game_context or {})
        scores = {(int(i), int(j)): float(mat[i, j])
                  for i in range(mat.shape[0]) for j in range(mat.shape[1])
                  if mat[i, j] > 0.0}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(
                {f"{i}-{j}": p for (i, j), p in scores.items()})

    # ------------------------------------------------------------------ #
    # Empirical-Bayes learning from settled first halves
    # ------------------------------------------------------------------ #
    def fit(self, history: Iterable[Dict[str, Any]]) -> "UELHalf1TotalsModel":
        """
        Each record may contain:
          home_goals_h1, away_goals_h1  : settled first-half goals
          goal_minutes                  : list of full-match goal minutes (for hazard)
          context                       : game_context used at prediction time
        """
        rows: List[Dict[str, Any]] = [r for r in history
                                      if self._num(r, "home_goals_h1") is not None
                                      and self._num(r, "away_goals_h1") is not None]
        p = self.priors

        # (a) Hazard slope: penalised MLE on goal timestamps over a 0..T window.
        minutes = [float(t) for r in history for t in (r.get("goal_minutes") or [])
                   if isinstance(t, (int, float)) and t >= 0]
        if minutes:
            T = 90.0 + p.stoppage_h2
            t_arr = np.asarray(minutes)

            def nll(b: float) -> float:
                ll = (b / 90.0) * t_arr.sum() - t_arr.size * math.log(self._cum_hazard(b, 0.0, T))
                return -ll + 0.5 * ((b - self.beta_prior_mean) / p.beta_prior_sd) ** 2

            res = optimize.minimize_scalar(nll, bracket=(self.beta_prior_mean - 1.0,
                                                         self.beta_prior_mean + 1.0))
            if res.success and math.isfinite(res.x):
                self.beta = float(res.x)

        if not rows:
            return self

        # (b) Baseline means: gamma-Poisson conjugate shrinkage toward the anchors.
        n = len(rows)
        sh = sum(self._num(r, "home_goals_h1") for r in rows)
        sa = sum(self._num(r, "away_goals_h1") for r in rows)
        n0 = p.baseline_pseudo_n
        self.mu_home = (n0 * p.baseline_home + sh) / (n0 + n)
        self.mu_away = (n0 * p.baseline_away + sa) / (n0 + n)

        # (c) Dispersion: under shared gamma frailty, Var(Y) = m + m^2 / kappa.
        #     Moment estimate of 1/kappa from conditional residuals, then shrinkage.
        saved = self.inv_kappa
        self.inv_kappa = 0.0
        num, den = 0.0, 0.0
        for r in rows:
            ctx = r.get("context") or {}
            lam, mu, _, _ = self._rates(ctx)
            m = lam + mu
            y = self._num(r, "home_goals_h1") + self._num(r, "away_goals_h1")
            num += (y - m) ** 2 - y
            den += m * m
        self.inv_kappa = saved
        if den > 0:
            raw = max(num / den, 0.0)  # a gamma mixture cannot be under-dispersed
            wgt = n / (n + p.dispersion_pseudo_n)
            self.inv_kappa = wgt * raw + (1.0 - wgt) * p.inv_kappa_prior

        self.n_settled = n
        return self


# Engine entry point alias.
CandidateModel = UELHalf1TotalsModel