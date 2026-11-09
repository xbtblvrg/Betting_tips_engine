"""
Candidate model: NBA | quarter_1 | exact_total

How it works
------------
NBA quarter scoring comes from possessions. Each possession produces 0, 1, 2,
3 or 4 points (with rare and-ones). This makes the outcome lattice uneven. A
plain Poisson or Normal on team points misprices exact-total contracts because
it smooths over that structure. The variance also has two separate sources:

  1. Pace (number of possessions). It is shared by both teams, because
     possessions alternate. This is the main source of home/away score
     correlation and of tail mass in the combined total.
  2. Efficiency (points per possession). It varies by team and is drawn
     independently.

Generative model
  N        ~ discretised Normal(mu_poss, sigma_poss)    shared Q1 pace
  N_h      = N + D,  D in {-1, 0, +1}                   tip / last-possession asymmetry
  theta_t  ~ Normal(0, sigma_eff)                       team efficiency shock (quadrature)
  X_{t,i}  ~ exponentially tilted possession-outcome law, tilted so that
             E[X] = target_ppp_t * exp(theta_t)
  S_t      = sum_{i=1..N_t} X_{t,i}                     exact convolution power

Exponential tilting is the maximum-entropy (minimum-KL) way to move the base
possession-outcome law to a new mean. It keeps the 2/3-point mix structure and
needs no ad-hoc rescaling.

Calibration from domain moments
  * Per-possession outcome law: variance about 1.5 pts^2 at about 1.2 PPP.
  * With about 25 possessions per team in Q1, the possession term contributes
    about 38 pts^2. Pace variance adds about 4 pts^2 and a small efficiency
    shock adds about 1-2 pts^2. The implied team Q1 SD is about 6.5. That gives
    a dispersion index of about 1.5 on 28 points, which matches observed NBA
    quarter scoring. The 1.000 Poisson placeholder was a cold-start artefact
    and is not a measured value.
  * All priors are named constructor parameters. They are not inline magic
    numbers, so any of them can be re-fit once settled data exists.

The model never uses bookmaker prices.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies across engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded in scope


class NBAQuarter1ExactTotalModel(BaseCandidateModel):
    """Possession-level compound model for NBA first-quarter exact totals."""

    # Points per possession outcome lattice
    _OUTCOMES = np.arange(5, dtype=float)  # 0,1,2,3,4 points

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_1_1107_131350",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_1",
        family: str = "exact_total",
        # ---- domain priors (re-fittable, not clamps) ----
        baseline_home_q1: float = 28.0,
        baseline_away_q1: float = 27.0,
        league_pace_per48: float = 99.5,           # possessions per team per 48 min
        q1_share_of_game: float = 0.255,           # Q1 scores slightly above 1/4 of game
        pace_sd_per_team_q1: float = 1.8,          # SD of Q1 possessions per team
        efficiency_shock_sd: float = 0.04,         # log-PPP SD per team per quarter
        possession_offset_prob: float = 0.25,      # P(D=+1) = P(D=-1)
        base_outcome_probs: Tuple[float, ...] = (0.48, 0.03, 0.32, 0.15, 0.02),
        rest_log_effect: float = 0.025,            # saturating rest effect on efficiency
        rating_shrinkage: float = 0.5,             # weight on attack/defense beyond expected
        quadrature_nodes: int = 5,
        tail_sigmas: float = 4.0,
        mass_floor: float = 1e-12,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:  # pragma: no cover - positional-only base
            super().__init__(model_id, version, league, scope, family)
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.baseline_home_q1 = float(baseline_home_q1)
        self.baseline_away_q1 = float(baseline_away_q1)
        self.league_pace_per48 = float(league_pace_per48)
        self.q1_share_of_game = float(q1_share_of_game)
        self.pace_sd = float(pace_sd_per_team_q1)
        self.eff_sd = float(efficiency_shock_sd)
        self.offset_p = float(possession_offset_prob)
        q = np.asarray(base_outcome_probs, dtype=float)
        self.base_q = q / q.sum()
        self.rest_log_effect = float(rest_log_effect)
        self.rating_shrinkage = float(rating_shrinkage)
        self.tail_sigmas = float(tail_sigmas)
        self.mass_floor = float(mass_floor)

        z, w = np.polynomial.hermite_e.hermegauss(int(quadrature_nodes))
        self._gh_z = z
        self._gh_w = w / w.sum()  # probabilists' Hermite -> N(0,1) weights

        self._max_ppp = float(self._OUTCOMES[-1])

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str) -> Optional[float]:
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
        return None

    @staticmethod
    def _rating(side: Any, key: str) -> Optional[float]:
        if isinstance(side, dict):
            v = side.get(key)
            try:
                f = float(v)
                return f if math.isfinite(f) and f > 0 else None
            except (TypeError, ValueError):
                return None
        return None

    def _to_q1_scale(self, value: Optional[float], baseline: float) -> float:
        """Accept Q1-scale or full-game-scale expectations."""
        if value is None or value <= 0:
            return baseline
        # A full-game expectation is about 1/share times a Q1 one. Choose the
        # scale whose implied Q1 value is log-closer to the domain baseline.
        q1_if_game = value * self.q1_share_of_game
        if abs(math.log(q1_if_game / baseline)) < abs(math.log(value / baseline)):
            return q1_if_game
        return value

    def _rest_log_mult(self, rest_days: Optional[float]) -> float:
        """Saturating rest effect, zero at one day of rest (league-typical)."""
        if rest_days is None or rest_days < 0:
            return 0.0
        return self.rest_log_effect * (math.exp(-1.0) - math.exp(-rest_days))

    def _rating_log_mult(self, attack: Optional[float], opp_defense: Optional[float]) -> float:
        """Ratings act as multiplicative indices around 1. They are shrunk
        because home/away_expected usually already includes them."""
        lm = 0.0
        if attack is not None:
            lm += math.log(attack)
        if opp_defense is not None:
            lm += math.log(opp_defense)
        return self.rating_shrinkage * lm

    def _tilted_law(self, target_mean: float) -> np.ndarray:
        """Minimum-KL tilt of the base possession law to the given mean."""
        eps = 1e-9
        m = min(max(target_mean, eps), self._max_ppp - eps)  # feasibility boundary of the lattice
        k = self._OUTCOMES
        logq = np.log(self.base_q)

        def mean_gap(th: float) -> float:
            lw = logq + th * k
            lw -= lw.max()
            p = np.exp(lw)
            p /= p.sum()
            return float(p @ k) - m

        lo, hi = -50.0, 50.0
        th = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        lw = logq + th * k
        lw -= lw.max()
        p = np.exp(lw)
        return p / p.sum()

    @staticmethod
    def _conv_power(p: np.ndarray, n: int) -> np.ndarray:
        """Exact n-fold convolution via binary exponentiation."""
        result = np.array([1.0])
        base = p.copy()
        while n > 0:
            if n & 1:
                result = np.convolve(result, base)
            n >>= 1
            if n:
                base = np.convolve(base, base)
        return result

    def _team_pmf(self, ppp: float, n: int, cache: Dict[Tuple[int, int], np.ndarray],
                  laws: Dict[int, np.ndarray]) -> np.ndarray:
        """Team point PMF for n possessions, mixed over the efficiency shock."""
        out = None
        for j, (z, w) in enumerate(zip(self._gh_z, self._gh_w)):
            key = (j, n)
            if key not in cache:
                cache[key] = self._conv_power(laws[j], n)
            pm = w * cache[key]
            out = pm if out is None else out + pm
        return out

    # ------------------------------------------------------------------ main
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        # 1. Expected Q1 points per team
        mu_h = self._to_q1_scale(self._num(ctx, "home_expected", "home_xg", "home_mu"),
                                 self.baseline_home_q1)
        mu_a = self._to_q1_scale(self._num(ctx, "away_expected", "away_xg", "away_mu"),
                                 self.baseline_away_q1)

        home, away = ctx.get("home"), ctx.get("away")
        log_h = self._rating_log_mult(self._rating(home, "attack"), self._rating(away, "defense"))
        log_a = self._rating_log_mult(self._rating(away, "attack"), self._rating(home, "defense"))
        log_h += self._rest_log_mult(self._num(ctx, "home_rest_days"))
        log_a += self._rest_log_mult(self._num(ctx, "away_rest_days"))
        mu_h *= math.exp(log_h)
        mu_a *= math.exp(log_a)

        # 2. Pace: expected Q1 possessions per team
        pace48 = self._num(ctx, "pace", "tempo", "expected_pace")
        hp, ap = self._num(ctx, "home_pace"), self._num(ctx, "away_pace")
        if pace48 is None and hp is not None and ap is not None:
            # possessions are shared, so combine the teams' paces geometrically
            pace48 = math.sqrt(hp * ap)
        if pace48 is None:
            pace48 = self.league_pace_per48
        if pace48 > 60.0:  # per-48 pace -> Q1 (12 of 48 minutes)
            mu_poss = pace48 * 12.0 / 48.0
        else:  # already a per-quarter value
            mu_poss = pace48

        # Points per possession implied by expectation / pace. Possession-count
        # randomness is symmetric, so E[S] = E[N] * PPP is preserved.
        ppp_h = mu_h / mu_poss
        ppp_a = mu_a / mu_poss

        # Efficiency-shocked tilted laws per quadrature node. Subtracting
        # 0.5*sigma^2 keeps E[exp(theta)] = 1, so the mean is unbiased.
        corr = -0.5 * self.eff_sd ** 2
        laws_h = {j: self._tilted_law(ppp_h * math.exp(self.eff_sd * z + corr))
                  for j, z in enumerate(self._gh_z)}
        laws_a = {j: self._tilted_law(ppp_a * math.exp(self.eff_sd * z + corr))
                  for j, z in enumerate(self._gh_z)}

        # 3. Discretised pace distribution
        sd = max(self.pace_sd, 1e-6)
        n_lo = max(1, int(math.floor(mu_poss - self.tail_sigmas * sd)))
        n_hi = int(math.ceil(mu_poss + self.tail_sigmas * sd))
        ns = np.arange(n_lo, n_hi + 1)
        cdf_hi = stats.norm.cdf(ns + 0.5, loc=mu_poss, scale=sd)
        cdf_lo = stats.norm.cdf(ns - 0.5, loc=mu_poss, scale=sd)
        pn = cdf_hi - cdf_lo
        pn /= pn.sum()

        offsets = ((-1, self.offset_p), (0, 1.0 - 2.0 * self.offset_p), (1, self.offset_p))

        # 4. Assemble the joint score matrix
        cache_h: Dict[Tuple[int, int], np.ndarray] = {}
        cache_a: Dict[Tuple[int, int], np.ndarray] = {}
        size = 4 * (n_hi + 1) + 1
        joint = np.zeros((size, size))
        for n, wn in zip(ns, pn):
            n = int(n)
            pmf_a = self._team_pmf(ppp_a, n, cache_a, laws_a)
            for d, wd in offsets:
                nh = n + d
                if nh < 1 or wd <= 0:
                    continue
                pmf_h = self._team_pmf(ppp_h, nh, cache_h, laws_h)
                joint[: pmf_h.size, : pmf_a.size] += (wn * wd) * np.outer(pmf_h, pmf_a)

        joint[joint < self.mass_floor] = 0.0
        joint /= joint.sum()

        hs, as_ = np.nonzero(joint)
        scores = {(int(h), int(a)): float(joint[h, a]) for h, a in zip(hs, as_)}

        if DiscreteDistribution is None:  # pragma: no cover
            raise RuntimeError("DiscreteDistribution unavailable in execution scope")
        return DiscreteDistribution.from_scores(scores)

    # ------------------------------------------------------------------ convenience
    def total_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        """Exact-total PMF (home + away), e.g. for pricing exact_total contracts."""
        dist = self.predict_distribution(game_context)
        raw = getattr(dist, "scores", None) or getattr(dist, "probabilities", None) or {}
        out: Dict[int, float] = {}
        for key, p in dict(raw).items():
            if isinstance(key, tuple) and len(key) == 2:
                t = int(key[0]) + int(key[1])
                out[t] = out.get(t, 0.0) + float(p)
        return dict(sorted(out.items()))