"""
Candidate model for NCAAW | quarter_1 | totals.

How the model works
-------------------
1. Possessions are shared. Both teams draw from one possession count
   N ~ Lattice-Normal(mu_N, var_N), where mu_N = pace * (10 / 40).
   A fixed 10-minute clock makes possessions *under*-dispersed against
   Poisson, but uncertainty about pace adds back a mu_N**2 * cv**2 term.
2. Points come in lumps. Each team's per-possession value is
   X in {0, 1, 2, 3}. The shape of X comes from structural shot-mix
   priors (3-point share, free-throw share, and-ones). Its mean is solved
   so that E[N] * E[X] equals the adjusted expected points.
3. Efficiency is uncertain. Each team's points-per-possession multiplier
   is integrated with Gauss-Hermite quadrature on a log-normal scale.
   This widens the tails when the talent gap is large.
4. The joint score lattice is
   P(h, a) = sum_n P(N = n) * [sum_j w_j X_Hj^{*n}](h) * [sum_k w_k X_Ak^{*n}](a).
   The home and away scores are conditionally independent given N and
   positively correlated once N is integrated out.

The implied dispersion index of the total is about 1.9 to 2.4, not 1.0.
The probability tails are therefore wide enough to price the hook honestly.

Every structural prior is a named, documented class attribute. Each can
be overridden through game_context. None of them clamps the output.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded; try the known homes.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: F821
except NameError:  # pragma: no cover - depends on host layout
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:
        try:
            from betting_engine.distributions import DiscreteDistribution  # type: ignore
        except ImportError:
            from betting_engine.core.distributions import DiscreteDistribution  # type: ignore


class NCAAWQuarter1TotalsModel(BaseCandidateModel):
    # ---------------- Empirical anchors (segment baselines) ----------------
    BASE_HOME_EXPECTED: float = 20.00
    BASE_AWAY_EXPECTED: float = 18.75

    # ---------------- Structural domain priors (overridable) ---------------
    PERIOD_FRACTION: float = 10.0 / 40.0       # Q1 share of regulation clock
    PRIOR_PACE_40: float = 72.0                # NCAAW possessions per 40 min
    INTRINSIC_POSS_DISPERSION: float = 0.45    # Var/Mean of N at a known pace
    PACE_CV: float = 0.07                      # uncertainty in game pace
    EFFICIENCY_LOG_SD: float = 0.10            # per-team PPP uncertainty
    THREE_SHARE_OF_MAKES: float = 0.26         # share of scoring poss. worth 3
    FT_ONLY_SHARE_OF_MAKES: float = 0.10       # share worth exactly 1 (Q1 light bonus)
    AND_ONE_SHARE_OF_MAKES: float = 0.03       # 2+1 trips folded into 3-value
    REST_REFERENCE_DAYS: float = 2.0
    REST_EFFICIENCY_ELASTICITY: float = 0.015  # d log(PPP) / d log(1+rest)
    REST_PACE_ELASTICITY: float = 0.010
    RATING_SHRINKAGE: float = 0.35             # weight on attack/defense ratings
    GH_NODES: int = 7
    TAIL_SDS: float = 9.0

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_1_1107_215142",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_1",
        family: str = "totals",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.last_diagnostics: Dict[str, float] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], keys: Sequence[str], default: Optional[float]) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._num(ctx[k])
                if v is not None:
                    return v
        return default

    def _team_field(self, ctx: Dict[str, Any], side: str, field: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict):
            v = self._num(team.get(field))
            if v is not None:
                return v
        return self._get(ctx, [f"{side}_{field}"], None)

    @staticmethod
    def _rating_to_multiplier(r: Optional[float]) -> float:
        """Map a rating to a multiplicative factor near 1.

        Ratings may be centered on 1 (ratio form) or on 0 (log or additive
        form). Ratio form is assumed for strictly positive values.
        """
        if r is None:
            return 1.0
        if r > 0.0 and abs(r - 1.0) < 1.0:
            return r
        if abs(r) < 1.0:
            return math.exp(r)
        return 1.0  # unrecognized scale -> neutral, no hard clamp

    # ----------------------------------------------------------- components
    def _scoring_mix(self, ctx: Dict[str, Any], side: str) -> np.ndarray:
        """Return value weights (q1, q2, q3) for a scoring possession."""
        p3 = self._team_field(ctx, side, "three_share")
        p3 = self.THREE_SHARE_OF_MAKES if p3 is None else p3
        p1 = self._team_field(ctx, side, "ft_share")
        p1 = self.FT_ONLY_SHARE_OF_MAKES if p1 is None else p1
        p3_total = p3 + self.AND_ONE_SHARE_OF_MAKES
        q = np.array([p1, max(1.0 - p1 - p3_total, 0.0), p3_total], dtype=float)
        return q / q.sum()

    @staticmethod
    def _possession_pmf(mean_ppp: float, mix: np.ndarray) -> np.ndarray:
        """Return the pmf on {0, 1, 2, 3} with the given mean and scoring mix.

        If the mean is too high for the mix to reach, the mix is tilted
        smoothly (exponential tilt) toward higher values until it fits.
        """
        values = np.array([1.0, 2.0, 3.0])
        target = min(mean_ppp, 3.0 - 1e-9)
        v = float(mix @ values)
        q = mix
        if target > v:
            lo, hi = 0.0, 50.0
            for _ in range(80):
                t = 0.5 * (lo + hi)
                w = mix * np.exp(t * values)
                w /= w.sum()
                if w @ values < target:
                    lo = t
                else:
                    hi = t
            q = mix * np.exp(hi * values)
            q /= q.sum()
            v = float(q @ values)
        p_score = target / v
        pmf = np.empty(4)
        pmf[0] = 1.0 - p_score
        pmf[1:] = p_score * q
        return pmf

    def _possession_lattice(self, mu_n: float, var_n: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = math.sqrt(var_n)
        n_lo = max(0, int(math.floor(mu_n - self.TAIL_SDS * sd)))
        n_hi = int(math.ceil(mu_n + self.TAIL_SDS * sd))
        ns = np.arange(n_lo, n_hi + 1)
        from scipy.stats import norm

        p = norm.cdf(ns + 0.5, mu_n, sd) - norm.cdf(ns - 0.5, mu_n, sd)
        p /= p.sum()
        return ns, p

    def _conditional_marginals(
        self, mean_ppp: float, mix: np.ndarray, ns: np.ndarray
    ) -> List[np.ndarray]:
        """Return P(points | N = n), integrated over efficiency uncertainty."""
        x, w = np.polynomial.hermite_e.hermegauss(self.GH_NODES)
        w = w / w.sum()
        s = self.EFFICIENCY_LOG_SD
        # Mean-preserving log-normal multipliers.
        mults = np.exp(s * x - 0.5 * s * s)
        node_pmfs = [self._possession_pmf(mean_ppp * m, mix) for m in mults]

        n_max = int(ns[-1])
        out_len = 3 * n_max + 1
        result: Dict[int, np.ndarray] = {}
        powers = [np.array([1.0]) for _ in node_pmfs]
        wanted = set(int(n) for n in ns)
        for n in range(n_max + 1):
            if n in wanted:
                acc = np.zeros(out_len)
                for wj, pw in zip(w, powers):
                    acc[: pw.size] += wj * pw
                result[n] = acc
            powers = [np.convolve(pw, pm) for pw, pm in zip(powers, node_pmfs)]
        return [result[int(n)] for n in ns]

    # ------------------------------------------------------------- main API
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        # 1. Expected points anchored on the segment baselines.
        mu_h = self._get(ctx, ["home_expected", "home_q1_expected"], self.BASE_HOME_EXPECTED)
        mu_a = self._get(ctx, ["away_expected", "away_q1_expected"], self.BASE_AWAY_EXPECTED)
        if mu_h is None or mu_h <= 0:
            mu_h = self.BASE_HOME_EXPECTED
        if mu_a is None or mu_a <= 0:
            mu_a = self.BASE_AWAY_EXPECTED

        # 2. Attack/defense exposure, shrunk toward neutral.
        lam = self.RATING_SHRINKAGE
        h_att = self._rating_to_multiplier(self._team_field(ctx, "home", "attack"))
        h_def = self._rating_to_multiplier(self._team_field(ctx, "home", "defense"))
        a_att = self._rating_to_multiplier(self._team_field(ctx, "away", "attack"))
        a_def = self._rating_to_multiplier(self._team_field(ctx, "away", "defense"))
        mu_h *= (h_att * a_def) ** lam
        mu_a *= (a_att * h_def) ** lam

        # 3. Rest effects on efficiency (per team) and on shared pace.
        r_ref = math.log1p(self.REST_REFERENCE_DAYS)
        r_h = self._get(ctx, ["home_rest_days"], self.REST_REFERENCE_DAYS)
        r_a = self._get(ctx, ["away_rest_days"], self.REST_REFERENCE_DAYS)
        lr_h = math.log1p(max(r_h, 0.0)) - r_ref
        lr_a = math.log1p(max(r_a, 0.0)) - r_ref
        mu_h *= math.exp(self.REST_EFFICIENCY_ELASTICITY * lr_h)
        mu_a *= math.exp(self.REST_EFFICIENCY_ELASTICITY * lr_a)

        # 4. Possession process.
        pace = self._get(ctx, ["pace", "tempo", "expected_pace"], None)
        if pace is None:
            hp = self._team_field(ctx, "home", "pace")
            ap = self._team_field(ctx, "away", "pace")
            if hp and ap and hp > 0 and ap > 0:
                pace = math.sqrt(hp * ap)
            else:
                pace = hp or ap or self.PRIOR_PACE_40
        pace *= math.exp(self.REST_PACE_ELASTICITY * 0.5 * (lr_h + lr_a))
        mu_n = pace * self.PERIOD_FRACTION
        var_n = self.INTRINSIC_POSS_DISPERSION * mu_n + (self.PACE_CV * mu_n) ** 2
        ns, p_n = self._possession_lattice(mu_n, var_n)

        # 5. Per-possession value distributions (mean solved to hit mu).
        ppp_h = mu_h / mu_n
        ppp_a = mu_a / mu_n
        mix_h = self._scoring_mix(ctx, "home")
        mix_a = self._scoring_mix(ctx, "away")
        cond_h = self._conditional_marginals(ppp_h, mix_h, ns)
        cond_a = self._conditional_marginals(ppp_a, mix_a, ns)

        # 6. Joint score lattice, mixed over the shared possession count.
        size = cond_h[0].size
        joint = np.zeros((size, size))
        for pn, ph, pa in zip(p_n, cond_h, cond_a):
            joint += pn * np.outer(ph, pa)
        joint /= joint.sum()

        # 7. Trim negligible tail mass (relative, not a level clamp).
        mh = joint.sum(axis=1)
        ma = joint.sum(axis=0)
        eps = 1e-12
        h_max = int(np.nonzero(mh > eps)[0].max())
        a_max = int(np.nonzero(ma > eps)[0].max())
        joint = joint[: h_max + 1, : a_max + 1]
        joint /= joint.sum()

        # 8. Diagnostics.
        hh = np.arange(joint.shape[0])[:, None]
        aa = np.arange(joint.shape[1])[None, :]
        tot = hh + aa
        e_t = float((joint * tot).sum())
        v_t = float((joint * tot**2).sum() - e_t**2)
        e_h = float((joint * hh).sum())
        e_a = float((joint * aa).sum())
        cov = float((joint * hh * aa).sum() - e_h * e_a)
        self.last_diagnostics = {
            "mu_home": e_h,
            "mu_away": e_a,
            "mu_total": e_t,
            "var_total": v_t,
            "dispersion_index_total": v_t / e_t if e_t > 0 else float("nan"),
            "cov_home_away": cov,
            "mu_possessions": mu_n,
            "var_possessions": var_n,
        }

        scores = {
            (int(i), int(j)): float(joint[i, j])
            for i in range(joint.shape[0])
            for j in range(joint.shape[1])
            if joint[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)  # type: ignore[name-defined]
        except (TypeError, ValueError, AttributeError):
            return DiscreteDistribution.from_scores(joint)  # type: ignore[name-defined]