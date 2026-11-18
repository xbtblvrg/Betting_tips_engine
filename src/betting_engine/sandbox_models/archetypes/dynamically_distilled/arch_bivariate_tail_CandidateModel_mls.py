from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location varies across engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded in sandbox scope


# ---------------------------------------------------------------------------
# Prior hyperparameters.
#
# The market is cold start (0 settled wagers), so nothing below is fitted to
# segment outcomes. These are *priors*: domain-informed starting points that
# the posterior update in `update()` overrides as half-1 results settle. Every
# value can be overridden through the constructor `priors=` argument.
# ---------------------------------------------------------------------------
DEFAULT_PRIORS: Dict[str, float] = {
    # Empirical half-1 anchors supplied for the segment
    "anchor_home": 0.80,
    "anchor_away": 0.60,
    # Gamma prior pseudo-exposure for the league-level half-1 multipliers
    # (in units of "matches worth of expected goals")
    "gamma_prior_strength": 40.0,
    # Reliability weight on the supplied home/away_expected versus the
    # structural anchor model. Expressed as the prior precision ratio.
    "feed_precision": 3.0,
    "structural_precision": 1.0,
    # Elasticities of rate with respect to relative attack/defense/pace
    "beta_attack": 0.85,
    "beta_defense": 0.85,
    "beta_pace": 0.60,
    # Rest: log-rate effect of rest differential, saturating via tanh
    "gamma_rest": 0.06,
    "rest_scale_days": 3.0,
    # Travel / altitude: log-rate penalty to the away side per 1000 m of venue altitude
    "gamma_altitude_per_km": 0.045,
    # Heat: log-rate penalty to both sides per 10 C above the comfort point
    "gamma_heat_per_10c": 0.05,
    "heat_comfort_c": 24.0,
    # Turf effect (log-rate, both sides). Prior centered at zero, i.e. no effect.
    "gamma_turf": 0.0,
    # Wind (outdoor only): log-rate penalty per 10 km/h above the threshold
    "gamma_wind_per_10kmh": 0.03,
    "wind_threshold_kmh": 20.0,
    # Stoppage: expected first-half added time (minutes)
    "mean_stoppage_min": 2.5,
    # Dixon-Coles low-score dependence: normal prior on rho
    "rho_prior_mean": -0.05,
    "rho_prior_sd": 0.06,
    # Conway-Maxwell tail shape on the total: normal prior on log(nu).
    # nu = 1 reproduces the observed dispersion index of 1.000.
    "log_nu_prior_mean": 0.0,
    "log_nu_prior_sd": 0.10,
    # Lattice truncation tolerance (tail mass ignored)
    "tail_tolerance": 1e-12,
}


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _get_side(ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
    """Read e.g. ctx['home']['attack'] or ctx['home_attack']."""
    block = ctx.get(side)
    if isinstance(block, dict):
        v = _safe_float(block.get(key))
        if v is not None:
            return v
    return _safe_float(ctx.get(f"{side}_{key}"))


class CandidateModel(BaseCandidateModel):
    """
    MLS | half_1 | exact_total

    Structure
    ---------
    1. Side rates. Each side's first-half goals are the integral of a
       time-inhomogeneous intensity over 45 + stoppage minutes. Only the
       integral matters for the count distribution, so the rate is built as

           log lam_i = w * log(feed_i) + (1 - w) * log(struct_i) + log(m_i) + log(s)

       - feed_i: the supplied home/away_expected
       - struct_i: anchor_i * (att_i)^b_a * (def_j)^b_d * pace^b_p * g(rest, altitude, heat, turf, wind)
       - w: precision weight of the feed versus the structural model
       - m_i: posterior league multiplier (Gamma-Poisson conjugate, prior mean 1)
       - s: stoppage-length scaling (45 + s) / (45 + s_bar)

       Structural features enter only the structural branch, so effects already
       priced into the feed are not double counted.

    2. Joint law. Independent Poisson margins are coupled through the
       Dixon-Coles tau on the {0,1}^2 cells. rho is kept inside the analytic
       feasibility region implied by the current rates (a derived bound, not
       a static clamp).

    3. Total tail. The total is reweighted by the Conway-Maxwell factor
       (t!)^(1 - nu). The side rates are then rescaled by root-finding so the
       expected total equals the structural forecast. nu therefore changes
       shape only, never location.

    4. Learning. `update()` performs conjugate updates of the league
       multipliers and grid posteriors over (rho, log nu) from settled
       half-1 scores.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1107_125338",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "exact_total",
        priors: Optional[Dict[str, float]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:  # pragma: no cover - older base signatures
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family

        self.p: Dict[str, float] = dict(DEFAULT_PRIORS)
        if priors:
            self.p.update({k: float(v) for k, v in priors.items()})

        # Gamma(a, b) posteriors on the league multipliers (prior mean 1)
        k = self.p["gamma_prior_strength"]
        self._gamma = {"home": [k, k], "away": [k, k]}

        # Grid posterior over (rho, log nu)
        self._rho_grid = np.linspace(
            self.p["rho_prior_mean"] - 4 * self.p["rho_prior_sd"],
            self.p["rho_prior_mean"] + 4 * self.p["rho_prior_sd"],
            33,
        )
        self._lnu_grid = np.linspace(
            self.p["log_nu_prior_mean"] - 4 * self.p["log_nu_prior_sd"],
            self.p["log_nu_prior_mean"] + 4 * self.p["log_nu_prior_sd"],
            25,
        )
        R, L = np.meshgrid(self._rho_grid, self._lnu_grid, indexing="ij")
        self._log_post = stats.norm.logpdf(
            R, self.p["rho_prior_mean"], self.p["rho_prior_sd"]
        ) + stats.norm.logpdf(L, self.p["log_nu_prior_mean"], self.p["log_nu_prior_sd"])
        self._n_obs = 0

    # ------------------------------------------------------------------ rates
    def _multiplier(self, side: str) -> float:
        a, b = self._gamma[side]
        return a / b

    def _feed_weight(self) -> float:
        fp, sp = self.p["feed_precision"], self.p["structural_precision"]
        return fp / (fp + sp)

    def _structural_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.p
        lh, la = math.log(p["anchor_home"]), math.log(p["anchor_away"])

        # Relative attack/defense ratings (1.0 = league average). For defense,
        # values > 1 mean the side concedes more.
        ha, hd = _get_side(ctx, "home", "attack"), _get_side(ctx, "home", "defense")
        aa, ad = _get_side(ctx, "away", "attack"), _get_side(ctx, "away", "defense")
        if ha and ha > 0:
            lh += p["beta_attack"] * math.log(ha)
        if ad and ad > 0:
            lh += p["beta_defense"] * math.log(ad)
        if aa and aa > 0:
            la += p["beta_attack"] * math.log(aa)
        if hd and hd > 0:
            la += p["beta_defense"] * math.log(hd)

        # Pace / tempo relative to league average (multiplicative, shared)
        pace = _safe_float(ctx.get("pace")) or _safe_float(ctx.get("tempo"))
        if pace and pace > 0:
            lp = p["beta_pace"] * math.log(pace)
            lh += lp
            la += lp

        # Rest differential, saturating via tanh
        hr = _safe_float(ctx.get("home_rest_days"))
        ar = _safe_float(ctx.get("away_rest_days"))
        if hr is not None and ar is not None:
            d = math.tanh((hr - ar) / p["rest_scale_days"])
            lh += p["gamma_rest"] * d
            la -= p["gamma_rest"] * d

        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}

        def vget(key: str) -> Optional[float]:
            return _safe_float(venue.get(key), _safe_float(ctx.get(key)))

        def wget(key: str) -> Optional[float]:
            return _safe_float(weather.get(key), _safe_float(ctx.get(key)))

        # Altitude: acclimatisation asymmetry penalises the visitor
        alt_m = vget("altitude_m") or vget("altitude")
        if alt_m and alt_m > 0:
            la -= p["gamma_altitude_per_km"] * alt_m / 1000.0

        is_dome = bool(weather.get("is_dome", ctx.get("is_dome", False)))
        if not is_dome:
            temp = wget("temp")
            if temp is not None and temp > p["heat_comfort_c"]:
                pen = p["gamma_heat_per_10c"] * (temp - p["heat_comfort_c"]) / 10.0
                lh -= pen
                la -= pen
            wind = wget("wind_speed")
            if wind is not None and wind > p["wind_threshold_kmh"]:
                pen = p["gamma_wind_per_10kmh"] * (wind - p["wind_threshold_kmh"]) / 10.0
                lh -= pen
                la -= pen

        surface = str(venue.get("surface", ctx.get("surface", ""))).lower()
        if "turf" in surface or "artificial" in surface:
            lh += p["gamma_turf"]
            la += p["gamma_turf"]

        return math.exp(lh), math.exp(la)

    def _rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        sh, sa = self._structural_rates(ctx)
        fh = _safe_float(ctx.get("home_expected_half1")) or _safe_float(ctx.get("home_expected"))
        fa = _safe_float(ctx.get("away_expected_half1")) or _safe_float(ctx.get("away_expected"))
        w = self._feed_weight()

        def combine(feed: Optional[float], struct: float) -> float:
            if feed is None or feed <= 0:
                return struct
            return math.exp(w * math.log(feed) + (1.0 - w) * math.log(struct))

        lam_h = combine(fh, sh) * self._multiplier("home")
        lam_a = combine(fa, sa) * self._multiplier("away")

        stop = _safe_float(ctx.get("expected_stoppage"))
        if stop is not None and stop >= 0:
            s = (45.0 + stop) / (45.0 + self.p["mean_stoppage_min"])
            lam_h *= s
            lam_a *= s
        return lam_h, lam_a

    # ------------------------------------------------------------ joint law
    @staticmethod
    def _rho_feasible(rho: float, lh: float, la: float) -> float:
        """Project rho into the region where every tau-adjusted cell stays non-negative."""
        lo = max(-1.0 / max(lh, 1e-9), -1.0 / max(la, 1e-9))
        hi = min(1.0 / max(lh * la, 1e-9), 1.0)
        eps = 1e-9
        return float(min(max(rho, lo + eps), hi - eps))

    def _grid_size(self, lh: float, la: float) -> int:
        tol = self.p["tail_tolerance"]
        return int(max(stats.poisson.ppf(1 - tol, max(lh, la) * 1.5), 6)) + 2

    def _joint(self, lh: float, la: float, rho: float, nu: float, n: int) -> np.ndarray:
        g = np.arange(n + 1)
        ph = stats.poisson.pmf(g, lh)
        pa = stats.poisson.pmf(g, la)
        M = np.outer(ph, pa)
        r = self._rho_feasible(rho, lh, la)
        M[0, 0] *= 1.0 - lh * la * r
        M[0, 1] *= 1.0 + lh * r
        M[1, 0] *= 1.0 + la * r
        M[1, 1] *= 1.0 - r
        if abs(nu - 1.0) > 1e-12:
            T = g[:, None] + g[None, :]
            M = M * np.exp((1.0 - nu) * special.gammaln(T + 1.0))
        M = np.clip(M, 0.0, None)
        return M / M.sum()

    def _calibrated_joint(
        self, lh: float, la: float, rho: float, nu: float
    ) -> Tuple[np.ndarray, float, float]:
        """Rescale rates so E[total] matches lh + la after the nu reweighting."""
        n = self._grid_size(lh, la)
        target = lh + la
        if abs(nu - 1.0) < 1e-12 and abs(rho) < 1e-12:
            return self._joint(lh, la, rho, nu, n), lh, la
        g = np.arange(n + 1)
        T = g[:, None] + g[None, :]

        def gap(log_k: float) -> float:
            k = math.exp(log_k)
            return float((self._joint(lh * k, la * k, rho, nu, n) * T).sum() - target)

        try:
            log_k = optimize.brentq(gap, -2.0, 2.0, xtol=1e-10)
        except ValueError:
            log_k = 0.0
        k = math.exp(log_k)
        return self._joint(lh * k, la * k, rho, nu, n), lh * k, la * k

    def _posterior_point(self) -> Tuple[float, float]:
        w = np.exp(self._log_post - self._log_post.max())
        w /= w.sum()
        rho = float((w.sum(axis=1) * self._rho_grid).sum())
        lnu = float((w.sum(axis=0) * self._lnu_grid).sum())
        return rho, math.exp(lnu)

    # -------------------------------------------------------------- public
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lh, la = self._rates(ctx)
        rho, nu = self._posterior_point()
        M, _, _ = self._calibrated_joint(lh, la, rho, nu)

        scores: Dict[Tuple[int, int], float] = {}
        n = M.shape[0]
        for h in range(n):
            for a in range(n):
                v = float(M[h, a])
                if v > 0.0:
                    scores[(h, a)] = v

        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, AttributeError, ValueError):
            totals: Dict[str, float] = {}
            for (h, a), v in scores.items():
                totals[str(h + a)] = totals.get(str(h + a), 0.0) + v
            return DiscreteDistribution(probabilities=totals)

    def total_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        """Convenience: exact first-half total pmf."""
        lh, la = self._rates(game_context or {})
        rho, nu = self._posterior_point()
        M, _, _ = self._calibrated_joint(lh, la, rho, nu)
        n = M.shape[0]
        out: Dict[int, float] = {}
        for h in range(n):
            for a in range(n):
                out[h + a] = out.get(h + a, 0.0) + float(M[h, a])
        return out

    def update(self, observations: Iterable[Dict[str, Any]]) -> None:
        """
        Bayesian update from settled first halves.
        Each observation: {'home_goals': int, 'away_goals': int, 'context': {...}}.
        """
        obs: List[Tuple[int, int, float, float]] = []
        for o in observations:
            hg, ag = o.get("home_goals"), o.get("away_goals")
            if hg is None or ag is None:
                continue
            ctx = o.get("context", {}) or {}
            # Exposure excludes the current multiplier (pure conjugate exposure)
            lh, la = self._rates(ctx)
            lh /= self._multiplier("home")
            la /= self._multiplier("away")
            obs.append((int(hg), int(ag), lh, la))
        if not obs:
            return

        # 1) Gamma-Poisson conjugate update of the league multipliers
        for hg, ag, lh, la in obs:
            self._gamma["home"][0] += hg
            self._gamma["home"][1] += lh
            self._gamma["away"][0] += ag
            self._gamma["away"][1] += la

        # 2) Grid likelihood for (rho, log nu), using the updated multipliers
        mh, ma = self._multiplier("home"), self._multiplier("away")
        for i, rho in enumerate(self._rho_grid):
            for j, lnu in enumerate(self._lnu_grid):
                nu = math.exp(lnu)
                ll = 0.0
                for hg, ag, lh, la in obs:
                    M, _, _ = self._calibrated_joint(lh * mh, la * ma, float(rho), nu)
                    if hg < M.shape[0] and ag < M.shape[1]:
                        ll += math.log(max(M[hg, ag], 1e-300))
                    else:
                        ll += math.log(1e-300)
                self._log_post[i, j] += ll
        self._n_obs += len(obs)


# Engine discovery alias
Model = CandidateModel