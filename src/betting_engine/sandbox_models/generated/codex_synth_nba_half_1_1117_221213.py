"""
Candidate model for NBA | half_1 | home_total.

Structure
---------
A team's first-half points are modeled as a compound sum over possessions:

    T = sum_{i=1..N} X_i

* N is the number of possessions in the half. It is shared by both teams,
  since possession counts differ by at most about one. Clock mechanics make N
  strongly *under*-dispersed relative to Poisson, so Var(N) = kappa * E[N]
  with kappa << 1. Sharing N induces the positive home/away correlation
  through pace.
* X_i is drawn from {0, 1, 2, 3, 4}. Its probabilities come from a
  possession-terminal mechanics tree: turnover, free-throw trip, 2PA, 3PA,
  and-ones, and offensive-rebound continuation. The tree is re-centred to the
  matchup-implied points-per-possession by a single logit shift on
  field-goal make rates. Because the shift is a logit, every probability stays
  valid without clamps.
* Matchup-level efficiency uncertainty, meaning model error in E[X], is
  integrated with Gauss-Hermite quadrature using a mean-preserving lognormal.

The implied first-principles variance is

    Var(T) = E[N] Var(X) + Var(N) E[X]^2 + mu^2 (e^{s^2} - 1)

Calibration note
----------------
The supplied dispersion index of 0.187 is below the irreducible shot-outcome
floor Var(X)/E[X], which is about 1.2-1.4 for NBA possession mechanics. It
therefore cannot be the Var/Mean of raw home half-point totals. It is most
likely a residual or line-scaled statistic, so it is recorded for diagnostics
and never used to shrink the structural variance. The reported 65.6% win rate
over 500 bets should also be audited for look-ahead or closing-line leakage
before being trusted. Use `fit()` on settled historical games to calibrate the
mean-bias and efficiency-uncertainty terms by method of moments, with
precision-weighted shrinkage.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from numpy.polynomial.hermite_e import hermegauss
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is pre-loaded in the execution scope; import if exposed here.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    pass


class NBAHalf1HomeTotalCompoundPossessionModel(BaseCandidateModel):
    """Compound-possession joint score model for NBA first-half home totals."""

    # Domain priors. Every one is overridable via the constructor `config`;
    # none acts as a clamp on outputs.
    DEFAULTS: Dict[str, float] = {
        # Empirical anchors for the segment (first-half points).
        "home_baseline": 56.00,
        "away_baseline": 54.00,
        "first_half_share": 0.505,      # share of full-game points scored in H1
        # Possession-terminal mechanics (league-typical rates).
        "p_tov": 0.130,                 # turnover-ending attempts
        "p_ft_trip": 0.100,             # two-shot FT trips ending attempts
        "three_share": 0.400,           # 3PA share of field-goal attempts
        "m2": 0.545,                    # 2P make rate
        "m3": 0.362,                    # 3P make rate
        "mft": 0.785,                   # FT make rate
        "and1_2": 0.050,                # and-one rate on made 2s
        "and1_3": 0.012,                # and-one rate on made 3s
        "oreb": 0.240,                  # offensive-rebound rate on FG misses
        # Possession-count dynamics.
        "poss_var_ratio": 0.12,         # Var(N)/E[N]: clock-constrained underdispersion
        "pace_log_share": 0.27,         # share of log-mean deviation attributed to pace
        "h1_pace_factor": 1.00,         # H1 pace relative to full-game per-half pace
        "league_rating_per100": 115.0,  # for converting per-100 ratings to multipliers
        # Uncertainty structure.
        "sigma_eff_home": 0.045,        # log-sd of matchup efficiency error (home)
        "sigma_eff_away": 0.045,        # log-sd of matchup efficiency error (away)
        "sigma_anchor": 0.040,          # log-sd of supplied expectation
        "sigma_struct": 0.070,          # log-sd of rating-built structural mean
        "rest_gamma_off": 0.015,        # offensive efficiency sensitivity to rest
        "rest_gamma_def": 0.012,        # defensive efficiency sensitivity to rest
        "rest_reference_days": 2.0,
        # Bias-calibration prior (log scale) for fit().
        "bias_prior_sd": 0.030,
        # Numerics.
        "gh_nodes": 5,
        "poss_sd_span": 5.0,
        "tail_tol": 1e-10,
        # Diagnostics only (see module docstring).
        "empirical_dispersion": 0.187,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_1_1117_221213",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_1",
        family: str = "home_total",
        config: Optional[Dict[str, float]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.cfg: Dict[str, float] = dict(self.DEFAULTS)
        if config:
            self.cfg.update({k: float(v) for k, v in config.items()})

        self.bias_home: float = 1.0
        self.bias_away: float = 1.0
        self.last_diagnostics: Dict[str, Any] = {}

        gh_z, gh_w = hermegauss(int(self.cfg["gh_nodes"]))
        self._gh_z = gh_z
        self._gh_w = gh_w / gh_w.sum()

        self._ppp0 = self._ppp(self._x_pmf(0.0))
        self._league_poss = 0.5 * (self.cfg["home_baseline"] + self.cfg["away_baseline"]) / self._ppp0
        self._delta_cache: Dict[float, float] = {}

    # ------------------------------------------------------------------ #
    # Possession mechanics
    # ------------------------------------------------------------------ #
    @staticmethod
    def _sigmoid(x: float) -> float:
        return 1.0 / (1.0 + math.exp(-x))

    @staticmethod
    def _logit(p: float) -> float:
        return math.log(p / (1.0 - p))

    def _x_pmf(self, delta: float) -> np.ndarray:
        """Points-per-possession pmf on {0..4} after a logit efficiency shift."""
        c = self.cfg
        m2 = self._sigmoid(self._logit(c["m2"]) + delta)
        m3 = self._sigmoid(self._logit(c["m3"]) + delta)
        mft = c["mft"]
        p_shot = 1.0 - c["p_tov"] - c["p_ft_trip"]
        p3 = p_shot * c["three_share"]
        p2 = p_shot - p3

        terminal = np.zeros(5)
        terminal[0] += c["p_tov"]
        terminal[0] += c["p_ft_trip"] * (1.0 - mft) ** 2
        terminal[1] += c["p_ft_trip"] * 2.0 * mft * (1.0 - mft)
        terminal[2] += c["p_ft_trip"] * mft ** 2

        a2 = c["and1_2"] * mft
        terminal[2] += p2 * m2 * (1.0 - a2)
        terminal[3] += p2 * m2 * a2
        terminal[0] += p2 * (1.0 - m2) * (1.0 - c["oreb"])

        a3 = c["and1_3"] * mft
        terminal[3] += p3 * m3 * (1.0 - a3)
        terminal[4] += p3 * m3 * a3
        terminal[0] += p3 * (1.0 - m3) * (1.0 - c["oreb"])

        # Offensive-rebound continuation is a geometric restart, so the
        # possession outcome is the terminal-event distribution renormalized.
        return terminal / terminal.sum()

    @staticmethod
    def _ppp(pmf: np.ndarray) -> float:
        return float(np.dot(np.arange(pmf.size), pmf))

    def _solve_delta(self, target_ppp: float) -> float:
        key = round(target_ppp, 5)
        if key in self._delta_cache:
            return self._delta_cache[key]
        f = lambda d: self._ppp(self._x_pmf(d)) - target_ppp
        lo, hi = -6.0, 6.0
        f_lo, f_hi = f(lo), f(hi)
        if f_lo >= 0.0:
            delta = lo
        elif f_hi <= 0.0:
            delta = hi
        else:
            delta = optimize.brentq(f, lo, hi, xtol=1e-9)
        if len(self._delta_cache) > 20000:
            self._delta_cache.clear()
        self._delta_cache[key] = delta
        return delta

    def _x_moments(self, ppp: float) -> Tuple[float, float]:
        pmf = self._x_pmf(self._solve_delta(ppp))
        x = np.arange(pmf.size)
        mean = float(np.dot(x, pmf))
        return mean, float(np.dot(x * x, pmf) - mean * mean)

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(v: Any) -> Optional[float]:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Return a multiplicative rating (1.0 = league average) if available."""
        raw = None
        for k in (f"{side}_{key}", f"{side}_{key}_rating"):
            if k in ctx:
                raw = self._num(ctx.get(k))
                if raw is not None:
                    break
        if raw is None and isinstance(ctx.get(side), dict):
            sub = ctx[side]
            for k in (key, f"{key}_rating", key[:3]):
                if k in sub:
                    raw = self._num(sub.get(k))
                    if raw is not None:
                        break
        if raw is None or raw <= 0.0:
            return None
        # Values > 3 are per-100-possession ratings; otherwise multipliers.
        # Defense is read as points allowed, so a value > 1 is a leakier defense.
        return raw / self.cfg["league_rating_per100"] if raw > 3.0 else raw

    def _pace(self, ctx: Dict[str, Any], side: str) -> Optional[float]:
        v = self._num(ctx.get(f"{side}_pace"))
        if v is None and isinstance(ctx.get(side), dict):
            v = self._num(ctx[side].get("pace"))
        return v if (v is not None and v > 0.0) else None

    def _half_expectation(self, raw: Optional[float], baseline: float) -> Optional[float]:
        if raw is None or raw <= 0.0:
            return None
        # A full-game expectation is far above any plausible half total.
        if raw > 1.6 * baseline:
            return raw * self.cfg["first_half_share"]
        return raw

    def _rest_term(self, days: Optional[float]) -> float:
        ref = 1.0 - math.exp(-self.cfg["rest_reference_days"])
        if days is None or days < 0.0:
            return 0.0
        return (1.0 - math.exp(-days)) - ref

    def _means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        c = self.cfg
        mu = {}
        for side, opp, base_key in (("home", "away", "home_baseline"), ("away", "home", "away_baseline")):
            base = c[base_key]
            anchor = self._half_expectation(self._num(ctx.get(f"{side}_expected")), base)
            att = self._rating(ctx, side, "attack")
            dfn = self._rating(ctx, opp, "defense")
            struct = None
            if att is not None or dfn is not None:
                struct = base * (att or 1.0) * (dfn or 1.0)
            if anchor is not None and struct is not None:
                wa = 1.0 / c["sigma_anchor"] ** 2
                ws = 1.0 / c["sigma_struct"] ** 2
                m = math.exp((wa * math.log(anchor) + ws * math.log(struct)) / (wa + ws))
            else:
                m = anchor or struct or base
            # Rest: own legs help offense; a tired opponent concedes efficiency.
            r_own = self._rest_term(self._num(ctx.get(f"{side}_rest_days")))
            r_opp = self._rest_term(self._num(ctx.get(f"{opp}_rest_days")))
            m *= math.exp(c["rest_gamma_off"] * r_own - c["rest_gamma_def"] * r_opp)
            mu[side] = m

        mu_h = mu["home"] * self.bias_home
        mu_a = mu["away"] * self.bias_away

        ph, pa = self._pace(ctx, "home"), self._pace(ctx, "away")
        if ph is not None and pa is not None:
            league_full = 2.0 * self._league_poss
            per_half = lambda p: p / 2.0 if p > 1.5 * league_full / 2.0 else p
            ph, pa = per_half(ph), per_half(pa)
            en = ph * pa / self._league_poss * c["h1_pace_factor"]
        else:
            ratio = (mu_h + mu_a) / (c["home_baseline"] + c["away_baseline"])
            en = self._league_poss * ratio ** c["pace_log_share"] * c["h1_pace_factor"]
        return mu_h, mu_a, en

    # ------------------------------------------------------------------ #
    # Distribution construction
    # ------------------------------------------------------------------ #
    def _poss_weights(self, en: float) -> Tuple[np.ndarray, np.ndarray]:
        sd = math.sqrt(max(self.cfg["poss_var_ratio"] * en, 1e-9))
        span = self.cfg["poss_sd_span"] * sd
        lo = max(1, int(math.floor(en - span)))
        hi = max(lo, int(math.ceil(en + span)))
        n = np.arange(lo, hi + 1)
        w = stats.norm.cdf(n + 0.5, en, sd) - stats.norm.cdf(n - 0.5, en, sd)
        w = np.clip(w, 0.0, None)
        s = w.sum()
        if s <= 0.0:
            n = np.array([int(round(en))])
            w = np.array([1.0])
        else:
            w = w / s
        return n, w

    def _team_sum_pmfs(self, ppp: float, sigma: float, n_vals: np.ndarray) -> np.ndarray:
        """Rows give P(T=t | N=n), mixed over efficiency uncertainty."""
        n_max = int(n_vals[-1])
        width = 4 * n_max + 1
        out = np.zeros((n_vals.size, width))
        idx = {int(n): i for i, n in enumerate(n_vals)}
        for z, g in zip(self._gh_z, self._gh_w):
            target = ppp * math.exp(sigma * z - 0.5 * sigma * sigma)
            x = self._x_pmf(self._solve_delta(target))
            cur = np.array([1.0])
            for n in range(1, n_max + 1):
                cur = np.convolve(cur, x)
                if n in idx:
                    out[idx[n], : cur.size] += g * cur
        return out

    def _joint(self, ctx: Dict[str, Any]) -> Tuple[np.ndarray, Dict[str, float]]:
        mu_h, mu_a, en = self._means(ctx)
        n_vals, n_w = self._poss_weights(en)
        H = self._team_sum_pmfs(mu_h / en, self.cfg["sigma_eff_home"], n_vals)
        A = self._team_sum_pmfs(mu_a / en, self.cfg["sigma_eff_away"], n_vals)
        joint = np.einsum("n,nh,na->ha", n_w, H, A, optimize=True)
        joint = np.clip(joint, 0.0, None)

        tol = self.cfg["tail_tol"]
        mh, ma = joint.sum(axis=1), joint.sum(axis=0)
        h_keep = np.nonzero(np.cumsum(mh[::-1])[::-1] > tol)[0]
        a_keep = np.nonzero(np.cumsum(ma[::-1])[::-1] > tol)[0]
        h_hi = int(h_keep[-1]) + 1 if h_keep.size else joint.shape[0]
        a_hi = int(a_keep[-1]) + 1 if a_keep.size else joint.shape[1]
        joint = joint[:h_hi, :a_hi]
        joint /= joint.sum()

        ex_h, vx_h = self._x_moments(mu_h / en)
        var_n = self.cfg["poss_var_ratio"] * en
        s2 = self.cfg["sigma_eff_home"] ** 2
        var_struct = en * vx_h + var_n * ex_h ** 2 + mu_h ** 2 * (math.exp(s2) - 1.0)
        hv = np.arange(joint.shape[0])
        pm = joint.sum(axis=1)
        m_out = float(np.dot(hv, pm))
        diag = {
            "mu_home": mu_h,
            "mu_away": mu_a,
            "expected_possessions": en,
            "var_home_identity": var_struct,
            "var_home_realized": float(np.dot((hv - m_out) ** 2, pm)),
            "dispersion_structural": var_struct / mu_h,
            "dispersion_floor": vx_h / ex_h,
            "empirical_dispersion_input": self.cfg["empirical_dispersion"],
            "empirical_dispersion_feasible": float(self.cfg["empirical_dispersion"] >= vx_h / ex_h),
        }
        return joint, diag

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        joint, diag = self._joint(ctx)
        self.last_diagnostics = diag

        h_idx, a_idx = np.nonzero(joint > 0.0)
        probs = joint[h_idx, a_idx]
        probs = probs / probs.sum()
        scores = {(int(h), int(a)): float(p) for h, a, p in zip(h_idx, a_idx, probs)}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, AttributeError, ValueError):
            return DiscreteDistribution.from_scores(joint)

    def home_total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        joint, diag = self._joint(game_context or {})
        self.last_diagnostics = diag
        return joint.sum(axis=1)

    # ------------------------------------------------------------------ #
    # Calibration on settled historical games
    # ------------------------------------------------------------------ #
    @staticmethod
    def _observed(rec: Dict[str, Any], side: str) -> Optional[float]:
        for k in (f"{side}_half_1_points", f"{side}_h1_points", f"{side}_half_points", f"{side}_score_h1"):
            if k in rec:
                try:
                    v = float(rec[k])
                    if math.isfinite(v):
                        return v
                except (TypeError, ValueError):
                    pass
        return None

    def fit(self, records: Iterable[Dict[str, Any]]) -> "NBAHalf1HomeTotalCompoundPossessionModel":
        """Method-of-moments calibration of mean bias and efficiency uncertainty.

        1. Precision-weighted, shrunk log-bias of the mean for each side.
        2. sigma_eff^2 = log(1 + excess second moment / E[mu^2]), where the
           excess is observed squared residuals minus the irreducible
           possession-identity variance. When the excess is non-positive,
           sigma_eff is set to zero rather than clamped to a fixed number.
        """
        recs: List[Dict[str, Any]] = [r for r in records if isinstance(r, dict)]
        if not recs:
            return self
        saved_bias = (self.bias_home, self.bias_away)
        self.bias_home = self.bias_away = 1.0

        for side, sig_key in (("home", "sigma_eff_home"), ("away", "sigma_eff_away")):
            ys, mus, vstruct = [], [], []
            for r in recs:
                y = self._observed(r, side)
                if y is None:
                    continue
                mu_h, mu_a, en = self._means(r)
                mu = mu_h if side == "home" else mu_a
                ex, vx = self._x_moments(mu / en)
                ys.append(y)
                mus.append(mu)
                vstruct.append(en * vx + self.cfg["poss_var_ratio"] * en * ex ** 2)
            if len(ys) < 2:
                continue
            y = np.asarray(ys)
            mu = np.asarray(mus)
            vs = np.asarray(vstruct)

            log_b = math.log(max(y.sum(), 1e-9) / mu.sum())
            prec_data = float((mu ** 2).sum() / (vs.sum() + 1e-12)) * len(y)
            prec_prior = 1.0 / self.cfg["bias_prior_sd"] ** 2
            b = math.exp(log_b * prec_data / (prec_data + prec_prior))

            mu_b = mu * b
            excess = float(np.mean((y - mu_b) ** 2) - np.mean(vs * b))
            sigma = math.sqrt(math.log1p(excess / float(np.mean(mu_b ** 2)))) if excess > 0.0 else 0.0

            if side == "home":
                self.bias_home = b
            else:
                self.bias_away = b
            self.cfg[sig_key] = sigma

        if (self.bias_home, self.bias_away) == (1.0, 1.0) and saved_bias != (1.0, 1.0):
            self.bias_home, self.bias_away = saved_bias
        self._delta_cache.clear()
        return self


CandidateModel = NBAHalf1HomeTotalCompoundPossessionModel