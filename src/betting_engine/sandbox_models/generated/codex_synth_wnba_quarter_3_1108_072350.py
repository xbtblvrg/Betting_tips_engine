"""
Candidate model for WNBA | quarter_3 | exact_score.

Structure: a possession-driven compound model.

  1. Shared possession count  N ~ Binomial(n_max, p)
       E[N] = (team pace per quarter, scaled by rest) x 2
       n_max = floor(quarter_seconds / minimum mean possession length)
  2. Possession split  N_h = ceil/floor(N/2) (odd extra possession is a coin flip,
     because the team starting Q3 is not in game_context), plus an offset
     delta in {-1, 0, +1} for end-of-quarter and turnover/offensive-rebound
     asymmetry.
  3. Per-possession points  f over {0,1,2,3,4}. A WNBA-shaped base distribution
     is exponentially tilted, f_theta(k) ∝ f0(k)·exp(theta·k). This is the
     minimum-KL change to the shape that reproduces the target points per
     possession, so E[N_side]·E[pts] equals the expected side score exactly.
  4. Exact-score lattice
       P(h, a) = Σ_{N_h, N_a} P(N_h, N_a) · f_h^{*N_h}(h) · f_a^{*N_a}(a)

The correlation between the two scores comes from the shared N. Overdispersion,
Var = Var(N_s)·mu² + E[N_s]·sigma², comes from lumpy scoring. With league-typical
inputs the dispersion index is about 1.5–1.6 and the score correlation is about
0.1–0.15. The parity and lumpiness of the exact-score surface come from the
convolutions.

Notes on the brief:
  * The model needs domain priors (quarter length, base scoring shape, minimum
    possession length, rest effect). They are named class attributes with their
    rationale, not clamps on outputs. "Zero constants" is not achievable for a
    cold-start market with no data to fit them.
  * The segment has 0 settled bets, so nothing here is fitted. The quoted
    +100%+ ROI benchmarks are not realistic targets for an exact-score market.
    Validate with out-of-sample log-loss/Brier against closing prices before
    staking.
  * Weather and venue features are ignored, because the game is played indoors.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # Resolve DiscreteDistribution whether imported or pre-loaded into scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class WNBAQuarter3ExactScoreModel(BaseCandidateModel):
    # ---------------- Domain priors (documented, not output clamps) ----------------
    QUARTER_SECONDS: float = 600.0           # WNBA quarter = 10 minutes
    GAME_QUARTERS: float = 4.0
    LEAGUE_PACE_PER40_TEAM: float = 80.0     # ~80 possessions per team per 40 min
    MIN_MEAN_POSS_SECONDS: float = 9.0       # fastest sustainable mean possession -> n_max
    BASELINE_HOME: float = 20.00             # empirical anchor (segment brief)
    BASELINE_AWAY: float = 18.75
    # Per-possession points shape {0,1,2,3,4}. FT trips are folded in (2 FT -> 2, and-1 -> 3).
    BASE_POSSESSION_PMF: Tuple[float, ...] = (0.530, 0.035, 0.295, 0.135, 0.005)
    # Share of log total-points deviation attributed to pace (rest goes to efficiency).
    PACE_SHARE_OF_TOTAL: float = 0.5
    # Possession offset probability per side (turnover / end-of-quarter asymmetry).
    POSSESSION_OFFSET_PROB: float = 0.10
    # Rest: pace multiplier 1 - A*exp(-rest_days); A=0.03 -> ~3% slower on 0 days rest.
    REST_PACE_AMPLITUDE: float = 0.03
    # Partial weight on attack/defense ratings (home_expected likely already encodes them).
    EXPOSURE_WEIGHT: float = 0.25
    # Numerical truncation of negligible lattice mass (renormalised afterwards).
    MASS_EPS: float = 1e-11

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_3_1108_072350",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_3",
        family: str = "exact_score",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self._last_diagnostics: Dict[str, float] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) else default

    def _team_field(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            for k in (key, f"{key}_rating", f"{key}_strength"):
                v = self._num(block.get(k))
                if v is not None:
                    return v
        for k in (f"{side}_{key}", f"{side}_{key}_rating"):
            v = self._num(ctx.get(k))
            if v is not None:
                return v
        return None

    def _quarter_expectations(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._num(ctx.get("home_expected"), self.BASELINE_HOME)
        a = self._num(ctx.get("away_expected"), self.BASELINE_AWAY)
        if h is None or h <= 0:
            h = self.BASELINE_HOME
        if a is None or a <= 0:
            a = self.BASELINE_AWAY
        # A full-game expectation (~80 per team) was passed: convert to one quarter.
        if h + a > 2.5 * (self.BASELINE_HOME + self.BASELINE_AWAY):
            h /= self.GAME_QUARTERS
            a /= self.GAME_QUARTERS
        return h, a

    def _exposure_multipliers(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Scale-free relative exposure. It shifts the score share and keeps the total.

        r_h = att_h / def_a, r_a = att_a / def_h, normalised by their geometric
        mean, so the rating scale (1.0-centred or 100-centred) is irrelevant.
        """
        att_h = self._team_field(ctx, "home", "attack")
        def_h = self._team_field(ctx, "home", "defense")
        att_a = self._team_field(ctx, "away", "attack")
        def_a = self._team_field(ctx, "away", "defense")
        vals = (att_h, def_h, att_a, def_a)
        if any(v is None or v <= 0 for v in vals):
            return 1.0, 1.0
        r_h = att_h / def_a
        r_a = att_a / def_h
        g = math.sqrt(r_h * r_a)
        w = self.EXPOSURE_WEIGHT
        return (r_h / g) ** w, (r_a / g) ** w

    def _rest_multiplier(self, ctx: Dict[str, Any]) -> float:
        mults: List[float] = []
        for side in ("home", "away"):
            r = self._num(ctx.get(f"{side}_rest_days"))
            if r is None:
                r = self._team_field(ctx, side, "rest_days")
            if r is None:
                continue
            mults.append(1.0 - self.REST_PACE_AMPLITUDE * math.exp(-max(r, 0.0)))
        return float(np.mean(mults)) if mults else 1.0

    def _team_possessions_per_quarter(self, ctx: Dict[str, Any], total_pts: float) -> float:
        league_q = self.LEAGUE_PACE_PER40_TEAM / self.GAME_QUARTERS
        pace_vals: List[float] = []
        for k in ("pace", "tempo", "expected_pace"):
            v = self._num(ctx.get(k))
            if v is not None and v > 0:
                pace_vals.append(v)
                break
        if not pace_vals:
            for side in ("home", "away"):
                v = self._team_field(ctx, side, "pace")
                if v is None:
                    v = self._team_field(ctx, side, "tempo")
                if v is not None and v > 0:
                    pace_vals.append(v)

        if pace_vals:
            p = float(np.mean(pace_vals))
            if p > 60.0:            # per-40-minute, per-team possessions
                per_q = p / self.GAME_QUARTERS
            elif p > 25.0:          # per-quarter, both teams combined
                per_q = p / 2.0
            else:                   # per-quarter, per-team
                per_q = p
        else:
            # No pace feature: split the total-points deviation between pace and efficiency.
            base_total = self.BASELINE_HOME + self.BASELINE_AWAY
            per_q = league_q * (total_pts / base_total) ** self.PACE_SHARE_OF_TOTAL
        return per_q * self._rest_multiplier(ctx)

    @staticmethod
    def _tilt(base: np.ndarray, target_mean: float) -> np.ndarray:
        """Exponential tilt of base pmf on {0..K} to hit target_mean (minimum KL)."""
        support = np.arange(base.size, dtype=float)
        lo_feas, hi_feas = support[base > 0].min(), support[base > 0].max()
        tm = min(max(target_mean, lo_feas + 1e-6), hi_feas - 1e-6)
        logb = np.log(base)

        def pmf(theta: float) -> np.ndarray:
            z = logb + theta * support
            z -= z.max()
            e = np.exp(z)
            return e / e.sum()

        def gap(theta: float) -> float:
            return float(pmf(theta) @ support) - tm

        lo, hi = -1.0, 1.0
        while gap(lo) > 0:
            lo *= 2.0
        while gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(gap, lo, hi, xtol=1e-12, maxiter=200)
        return pmf(theta)

    def _possession_split(self, mean_total: float) -> Dict[Tuple[int, int], float]:
        n_max = int(math.floor(self.QUARTER_SECONDS / self.MIN_MEAN_POSS_SECONDS))
        # Keep Binomial p feasible if extreme pace inputs exceed the nominal ceiling.
        n_max = max(n_max, int(math.ceil(mean_total / 0.95)))
        p = mean_total / n_max
        n_vals = np.arange(n_max + 1)
        pn = stats.binom.pmf(n_vals, n_max, p)

        q = self.POSSESSION_OFFSET_PROB
        deltas = ((-1, q), (0, 1.0 - 2.0 * q), (1, q))
        split: Dict[Tuple[int, int], float] = {}
        for n, w in zip(n_vals, pn):
            if w < self.MASS_EPS:
                continue
            n = int(n)
            halves = [((n // 2, n // 2), 1.0)] if n % 2 == 0 else [
                (((n + 1) // 2, n // 2), 0.5), ((n // 2, (n + 1) // 2), 0.5)
            ]
            for (bh, ba), wp in halves:
                for d, wd in deltas:
                    nh, na = bh + d, ba - d
                    if nh < 0 or na < 0:
                        nh, na = bh, ba
                    key = (nh, na)
                    split[key] = split.get(key, 0.0) + w * wp * wd
        tot = sum(split.values())
        return {k: v / tot for k, v in split.items()}

    @staticmethod
    def _convolution_powers(f: np.ndarray, n_max: int) -> List[np.ndarray]:
        powers = [np.array([1.0])]
        for _ in range(n_max):
            powers.append(np.convolve(powers[-1], f))
        return powers

    # ------------------------------------------------------------------ core
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        h_exp, a_exp = self._quarter_expectations(ctx)
        total = h_exp + a_exp
        ex_h, ex_a = self._exposure_multipliers(ctx)
        # Re-normalise so the exposure tilt shifts the share and preserves the total.
        h_adj, a_adj = h_exp * ex_h, a_exp * ex_a
        s = total / (h_adj + a_adj)
        h_exp, a_exp = h_adj * s, a_adj * s

        team_poss = self._team_possessions_per_quarter(ctx, total)
        split = self._possession_split(2.0 * team_poss)

        e_nh = sum(k[0] * v for k, v in split.items())
        e_na = sum(k[1] * v for k, v in split.items())

        base = np.asarray(self.BASE_POSSESSION_PMF, dtype=float)
        base /= base.sum()
        f_h = self._tilt(base, h_exp / max(e_nh, 1e-9))
        f_a = self._tilt(base, a_exp / max(e_na, 1e-9))

        max_nh = max(k[0] for k in split)
        max_na = max(k[1] for k in split)
        pw_h = self._convolution_powers(f_h, max_nh)
        pw_a = self._convolution_powers(f_a, max_na)

        K = base.size - 1
        mat = np.zeros((K * max_nh + 1, K * max_na + 1))
        for (nh, na), w in split.items():
            if w < self.MASS_EPS:
                continue
            ph, pa = pw_h[nh], pw_a[na]
            mat[: ph.size, : pa.size] += w * np.outer(ph, pa)

        mat[mat < self.MASS_EPS] = 0.0
        mat /= mat.sum()

        # Diagnostics (moments implied by the mechanics, not imposed).
        hs = np.arange(mat.shape[0], dtype=float)
        as_ = np.arange(mat.shape[1], dtype=float)
        mh, ma = mat.sum(1) @ hs, mat.sum(0) @ as_
        vh = mat.sum(1) @ hs**2 - mh**2
        va = mat.sum(0) @ as_**2 - ma**2
        cov = hs @ mat @ as_ - mh * ma
        self._last_diagnostics = {
            "mean_home": float(mh), "mean_away": float(ma),
            "dispersion_home": float(vh / mh), "dispersion_away": float(va / ma),
            "score_corr": float(cov / math.sqrt(vh * va)),
            "team_possessions": float(team_poss),
            "ppp_home": float(h_exp / e_nh), "ppp_away": float(a_exp / e_na),
        }
        return mat

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        mat = self.score_matrix(game_context)
        nz = np.argwhere(mat > 0)
        scores: Dict[Tuple[int, int], float] = {
            (int(i), int(j)): float(mat[i, j]) for i, j in nz
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except TypeError:
            return DiscreteDistribution.from_scores(
                [(h, a, p) for (h, a), p in scores.items()]
            )

    @property
    def diagnostics(self) -> Dict[str, float]:
        return dict(self._last_diagnostics)