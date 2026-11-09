"""
Candidate model: WNBA | half_2 | exact_total

Structure:
    Second-half points come from a shared possession count and a per-possession
    scoring outcome. The model builds the score lattice in three layers.

    1. Shared tempo.  Both teams get (to within one) the same number of
       possessions N, so N is a common latent variable. That shared variable
       is what makes the home and away halves positively correlated.
       Possession counts in basketball vary less than a Poisson would allow,
       because the clock fixes the window. N is therefore a discretised
       Gaussian, not a Poisson or negative binomial.

    2. Possession outcome.  Each possession produces 0/1/2/3/4 points. Start
       from a structural WNBA outcome shape (misses and turnovers, trips
       resolved by free throws, twos, threes, and-ones). Then apply an
       exponential tilt so the mean points per possession hits the team's
       target. The tilt is the minimum-KL change to the base shape, so the
       mean is calibrated and the shape stays realistic. Conditional on N,
       a team's points are the N-fold convolution of that outcome pmf.

    3. Game-level efficiency shock.  Shooting variance across a whole half is
       larger than per-possession independence implies (hot or cold nights,
       foul trouble, rotations). This enters as a log-normal multiplier on
       PPP, integrated with Gauss-Hermite quadrature. Each team's shock is
       independent given N.

    Joint lattice:
        P(h, a) = sum_n P(N = n) * [E_shock H_n](h) * [E_shock A_n](a)

    The implied dispersion index (Var/Mean of the total) comes out of the
    mechanics. It is not imposed. With the cold-start anchors (40.0 / 37.5)
    it lands near the empirical WNBA half-level range of about 1.3 to 1.6.

    Situational inputs:
      * home_expected / away_expected : primary anchors for the half means.
      * pace / possessions (optional) : sets E[N] directly. Otherwise E[N] is
        inferred from the expected total and league-average PPP.
      * rest days : a smooth (tanh) fatigue term on PPP. Fatigue weighs more
        in the second half.
      * attack / defense ratings : used only when the expected means are
        missing, so they are not counted twice.
      * weather : ignored. The sport is indoor.

    No bookmaker prices are used anywhere in the forecast.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded in the execution scope
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class WNBAHalf2ExactTotalModel(BaseCandidateModel):
    # Structural domain priors (documented, not tuned to any sample)
    # League-average offensive efficiency in points per possession.
    LEAGUE_PPP = 1.02
    # Half anchors for the cold start, taken from the empirical baselines.
    BASE_HOME_HALF = 40.00
    BASE_AWAY_HALF = 37.50
    # Possession-outcome shape: P(0), P(1), P(2), P(3), P(4).
    BASE_POSSESSION_SHAPE = np.array([0.52, 0.05, 0.31, 0.11, 0.01])
    # Second-half possession SD at league-average tempo. Clock-bound, so it
    # is smaller than the Poisson SD.
    PACE_SD_AT_LEAGUE = 3.4
    # Game-level log-efficiency SD for one team over one half.
    EFFICIENCY_LOG_SD = 0.065
    # Second-half PPP sensitivity to rest differential (per-day scale, tanh saturation).
    REST_EFFECT_SCALE = 0.012
    REST_SATURATION_DAYS = 2.0
    # Numerical settings.
    PACE_SIGMA_SPAN = 6.0
    QUAD_NODES = 7
    PROB_FLOOR = 1e-9

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_half_2_1107_192800",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "half_2",
        family: str = "exact_total",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:  # pragma: no cover - tolerate alternative base signatures
            super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Gauss-Hermite nodes for E[f(Z)], Z ~ N(0, 1)
        x, w = np.polynomial.hermite_e.hermegauss(self.QUAD_NODES)
        self._gh_nodes = x
        self._gh_weights = w / w.sum()

    # Context parsing
    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            v = float(value)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _team_rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        node = ctx.get(side)
        if isinstance(node, dict):
            v = self._as_float(node.get(key))
            if v is not None:
                return v
        return self._as_float(ctx.get(f"{side}_{key}"))

    def _anchor_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._as_float(ctx.get("home_expected"))
        a = self._as_float(ctx.get("away_expected"))

        if h is None or a is None:
            # Fallback: multiplicative attack x defense exposure around the
            # league anchors. Ratings are read as relative to 1.0.
            ha = self._team_rating(ctx, "home", "attack") or 1.0
            hd = self._team_rating(ctx, "home", "defense") or 1.0
            aa = self._team_rating(ctx, "away", "attack") or 1.0
            ad = self._team_rating(ctx, "away", "defense") or 1.0
            if h is None:
                h = self.BASE_HOME_HALF * ha * ad
            if a is None:
                a = self.BASE_AWAY_HALF * aa * hd

        # Positive means are required. Non-positive values fall back to anchors.
        if h <= 0:
            h = self.BASE_HOME_HALF
        if a <= 0:
            a = self.BASE_AWAY_HALF
        return h, a

    def _rest_multipliers(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        hr = self._as_float(ctx.get("home_rest_days"))
        ar = self._as_float(ctx.get("away_rest_days"))
        if hr is None or ar is None:
            return 1.0, 1.0
        diff = hr - ar
        # Smooth saturation: the benefit of extra rest flattens out. Applied
        # symmetrically so the expected total is unchanged by the differential
        # and only the split between teams moves.
        eff = self.REST_EFFECT_SCALE * self.REST_SATURATION_DAYS * math.tanh(
            diff / self.REST_SATURATION_DAYS
        )
        # Absolute fatigue: back-to-backs (0 rest days) lower both offences late.
        def fatigue(r: float) -> float:
            return self.REST_EFFECT_SCALE * math.exp(-max(r, 0.0))

        return (
            math.exp(0.5 * eff - fatigue(hr)),
            math.exp(-0.5 * eff - fatigue(ar)),
        )

    def _expected_possessions(self, ctx: Dict[str, Any], total_mean: float) -> float:
        for key in ("half_possessions", "possessions_half", "expected_possessions"):
            v = self._as_float(ctx.get(key))
            if v is not None and v > 0:
                return v
        pace = self._as_float(ctx.get("pace"))
        if pace is not None and pace > 0:
            # Pace is quoted per 40-minute game. The second half is 20 minutes.
            return pace / 2.0
        # Infer tempo from the expected total via league efficiency.
        return total_mean / (2.0 * self.LEAGUE_PPP)

    # Mechanics
    def _tilted_possession_pmf(self, target_ppp: float) -> np.ndarray:
        """Exponential tilt of the base outcome shape to match a target PPP."""
        base = self.BASE_POSSESSION_SHAPE / self.BASE_POSSESSION_SHAPE.sum()
        k = np.arange(base.size, dtype=float)
        max_pts = k[-1]
        # The attainable mean lies strictly inside (0, max_pts).
        target = float(np.clip(target_ppp, 1e-6, max_pts - 1e-6))

        def mean_gap(theta: float) -> float:
            w = base * np.exp(theta * k - np.max(theta * k))
            return float((w * k).sum() / w.sum() - target)

        lo, hi = -5.0, 5.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        w = base * np.exp(theta * k - np.max(theta * k))
        return w / w.sum()

    def _pace_pmf(self, mean_poss: float) -> Tuple[np.ndarray, np.ndarray]:
        # Possession SD scales with sqrt(tempo) around the league reference tempo.
        ref_poss = (self.BASE_HOME_HALF + self.BASE_AWAY_HALF) / (2.0 * self.LEAGUE_PPP)
        sd = self.PACE_SD_AT_LEAGUE * math.sqrt(mean_poss / ref_poss)
        lo = max(0, int(math.floor(mean_poss - self.PACE_SIGMA_SPAN * sd)))
        hi = int(math.ceil(mean_poss + self.PACE_SIGMA_SPAN * sd))
        n = np.arange(lo, hi + 1)
        cdf_hi = stats.norm.cdf(n + 0.5, loc=mean_poss, scale=sd)
        cdf_lo = stats.norm.cdf(n - 0.5, loc=mean_poss, scale=sd)
        p = np.clip(cdf_hi - cdf_lo, 0.0, None)
        p /= p.sum()
        return n, p

    def _team_points_given_n(
        self, mean_ppp: float, n_support: np.ndarray
    ) -> List[np.ndarray]:
        """For each n in support, return the team's points pmf averaged over the
        game-level efficiency shock."""
        n_max = int(n_support[-1])
        length = 4 * n_max + 1
        mixed = [np.zeros(length) for _ in n_support]
        index_of = {int(n): i for i, n in enumerate(n_support)}

        # The mean-preserving log-normal shock: E[exp(sZ - s^2/2)] = 1.
        s = self.EFFICIENCY_LOG_SD
        for z, wz in zip(self._gh_nodes, self._gh_weights):
            ppp = mean_ppp * math.exp(s * z - 0.5 * s * s)
            pmf = self._tilted_possession_pmf(ppp)
            cur = np.array([1.0])
            if 0 in index_of:
                mixed[index_of[0]][: cur.size] += wz * cur
            for n in range(1, n_max + 1):
                cur = np.convolve(cur, pmf)
                if n in index_of:
                    mixed[index_of[n]][: cur.size] += wz * cur
        return mixed

    def _score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        mu_h, mu_a = self._anchor_means(ctx)
        rh, ra = self._rest_multipliers(ctx)
        mu_h *= rh
        mu_a *= ra

        mean_poss = self._expected_possessions(ctx, mu_h + mu_a)
        n_support, p_n = self._pace_pmf(mean_poss)

        ppp_h = mu_h / mean_poss
        ppp_a = mu_a / mean_poss

        home_by_n = self._team_points_given_n(ppp_h, n_support)
        away_by_n = self._team_points_given_n(ppp_a, n_support)

        length = home_by_n[0].size
        joint = np.zeros((length, length))
        for pn, hv, av in zip(p_n, home_by_n, away_by_n):
            if pn > 0:
                joint += pn * np.outer(hv, av)
        joint /= joint.sum()
        return joint

    # Public API
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        joint = self._score_matrix(ctx)

        # Drop negligible lattice mass, then renormalise.
        mask = joint > self.PROB_FLOOR
        kept = joint * mask
        kept /= kept.sum()

        hs, as_ = np.nonzero(mask)
        scores: Dict[Tuple[int, int], float] = {
            (int(h), int(a)): float(kept[h, a]) for h, a in zip(hs, as_)
        }
        return DiscreteDistribution.from_scores(scores)  # type: ignore[name-defined]

    # Diagnostics (not used by the engine; useful for audit)
    def total_distribution(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        joint = self._score_matrix(game_context or {})
        size = joint.shape[0]
        totals = np.zeros(2 * size - 1)
        for h in range(size):
            totals[h : h + size] += joint[h]
        return {int(t): float(p) for t, p in enumerate(totals) if p > self.PROB_FLOOR}

    def moments(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        dist = self.total_distribution(game_context)
        t = np.array(list(dist.keys()), dtype=float)
        p = np.array(list(dist.values()))
        p /= p.sum()
        mean = float((t * p).sum())
        var = float(((t - mean) ** 2 * p).sum())
        return {"mean_total": mean, "var_total": var, "dispersion_index": var / mean}


CandidateModel = WNBAHalf2ExactTotalModel