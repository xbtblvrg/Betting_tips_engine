"""
Candidate model for the MLS | half_1 | winner_total segment.

Structure (derived from first-half scoring mechanics, not a stock template):
  1. A non-homogeneous intra-match hazard  lambda(t) ~ 1 + kappa * t / T_full  converts
     full-match expectations to first-half expectations when needed. It integrates
     over 45' + first-half stoppage, against 90' + total stoppage.
  2. Situational log-linear scaling around the empirical anchors (H 0.80 / A 0.60):
     Lambda_i = mu_i * exp(beta . x_i)
  3. A bivariate Poisson with a shared tempo component lambda3. Both teams get the
     same game-state shock.
  4. A shared gamma frailty on the whole match tempo, with variance phi. It is
     integrated out by generalized Gauss-Laguerre quadrature, so the Var/Mean index
     is 1 + phi * mean. The cold-start prior is phi -> 0, i.e. pure Poisson.
  5. A Dixon-Coles tau(rho) correction on the {0-0, 1-0, 0-1, 1-1} cells. rho is
     limited only by the mathematical validity region tau >= 0.
  6. (rho, phi, lambda3-share, kappa) are re-estimated by penalized maximum
     likelihood in `fit()` once settled first-half scores exist. With no data they
     stay at the shrinkage prior centre.

Every winner x total contract is priced by summing cells of one joint score matrix.
The model never reads bookmaker prices when it forms probabilities.

NOTE: this is a cold-start structural model. No out-of-sample performance is implied
until it has been backtested on held-out MLS first-half results.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import integrate, optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution location differs between engine versions
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pre-loaded in execution scope
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class MLSHalf1WinnerTotalModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Prior centres (shrinkage targets). These are not clamps; `fit()`
    # moves them as evidence accumulates.
    # ------------------------------------------------------------------ #
    ANCHOR_HOME = 0.80
    ANCHOR_AWAY = 0.60
    PRIOR_DISPERSION_INDEX = 1.000          # observed / assumed Var/Mean

    # Weakly informative situational coefficients (log-rate per unit feature).
    PRIOR_BETA = {
        "attack_exposure": 0.50,    # partial pass-through: anchors already embed matchup
        "rest": 0.06,               # per log rest-ratio unit
        "altitude_km": 0.07,        # home edge per km of elevation gap
        "travel_log": 0.025,        # away penalty per log1p(1000 km)
        "heat": -0.012,             # per degC above thermal comfort, both sides
        "wind": -0.004,             # per m/s, both sides
    }
    THERMAL_COMFORT_C = 22.0

    # Hazard shape and stoppage structure (minutes).
    PRIOR_KAPPA = 0.35              # late-half intensification
    STOPPAGE_H1 = 2.5
    STOPPAGE_FULL = 7.5

    # Shrinkage prior standard deviations for penalized MLE.
    PRIOR_SD = {"rho": 0.08, "log_phi": 1.5, "logit_s3": 1.5, "kappa": 0.25}
    PRIOR_LOGIT_S3 = -3.0           # lambda3 prior near zero

    QUAD_NODES = 24
    TAIL_EPS = 1e-10                # numerical truncation tolerance only

    def __init__(
        self,
        model_id: str = "codex_synth_mls_half_1_1108_121445",
        version: str = "1.0.0",
        league: str = "MLS",
        scope: str = "half_1",
        family: str = "winner_total",
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
        except TypeError:
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family

        # phi derived from the dispersion index: index = 1 + phi*mean  =>  phi = (index-1)/mean
        anchor_total = self.ANCHOR_HOME + self.ANCHOR_AWAY
        self.phi = max(self.PRIOR_DISPERSION_INDEX - 1.0, 0.0) / anchor_total
        self.rho = 0.0
        self.logit_s3 = self.PRIOR_LOGIT_S3
        self.kappa = self.PRIOR_KAPPA
        self.beta = dict(self.PRIOR_BETA)
        self.n_fitted = 0
        self.last_matrix: Optional[np.ndarray] = None

    # ================================================================== #
    # Context parsing
    # ================================================================== #
    @staticmethod
    def _get(ctx: Dict[str, Any], *paths: str) -> Optional[float]:
        for path in paths:
            node: Any = ctx
            ok = True
            for part in path.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if ok and node is not None:
                try:
                    v = float(node)
                    if math.isfinite(v):
                        return v
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _log_rating(v: Optional[float]) -> float:
        """Ratings on a multiplicative scale (1 = league average) -> log.
        Non-positive values are treated as already being on the log scale."""
        if v is None:
            return 0.0
        return math.log(v) if v > 0 else v

    # ================================================================== #
    # Hazard-based scope conversion
    # ================================================================== #
    def _first_half_share(self, kappa: float) -> float:
        t_full = 90.0 + self.STOPPAGE_FULL
        haz = lambda t: 1.0 + kappa * t / t_full
        h1, _ = integrate.quad(haz, 0.0, 45.0 + self.STOPPAGE_H1)
        full, _ = integrate.quad(haz, 0.0, t_full)
        return h1 / full

    def _anchor_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h_half = self._get(ctx, "home_expected_half_1", "home_expected_h1")
        a_half = self._get(ctx, "away_expected_half_1", "away_expected_h1")
        if h_half is not None and a_half is not None and h_half > 0 and a_half > 0:
            return h_half, a_half

        h = self._get(ctx, "home_expected")
        a = self._get(ctx, "away_expected")
        if h is None or a is None or h <= 0 or a <= 0:
            return self.ANCHOR_HOME, self.ANCHOR_AWAY

        declared = str(ctx.get("expected_scope", "")).lower()
        share = self._first_half_share(self.kappa)
        if declared in ("full", "full_game", "match", "ft"):
            return h * share, a * share
        if declared in ("half_1", "h1", "first_half"):
            return h, a

        # Undeclared: choose the interpretation closest (in log total) to the anchor.
        anchor_tot = math.log(self.ANCHOR_HOME + self.ANCHOR_AWAY)
        d_half = abs(math.log(h + a) - anchor_tot)
        d_full = abs(math.log((h + a) * share) - anchor_tot)
        return (h * share, a * share) if d_full < d_half else (h, a)

    # ================================================================== #
    # Situational scaling
    # ================================================================== #
    def _situational_log_adjust(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        b = self.beta
        lh, la = 0.0, 0.0

        # Attack x opposing-defence exposure (defence > 1 = leakier).
        h_att = self._log_rating(self._get(ctx, "home.attack", "home_attack"))
        h_def = self._log_rating(self._get(ctx, "home.defense", "home_defense"))
        a_att = self._log_rating(self._get(ctx, "away.attack", "away_attack"))
        a_def = self._log_rating(self._get(ctx, "away.defense", "away_defense"))
        lh += b["attack_exposure"] * (h_att + a_def)
        la += b["attack_exposure"] * (a_att + h_def)

        # Rest differential (diminishing returns via log ratio).
        hr = self._get(ctx, "home_rest_days")
        ar = self._get(ctx, "away_rest_days")
        if hr is not None and ar is not None and hr >= 0 and ar >= 0:
            r = math.log((1.0 + hr) / (1.0 + ar))
            lh += b["rest"] * r
            la -= b["rest"] * r

        # Altitude gap (Denver / Salt Lake effects).
        venue_alt = self._get(ctx, "venue.altitude_m", "venue.elevation_m",
                              "venue_altitude_m", "altitude_m")
        away_alt = self._get(ctx, "away.home_altitude_m", "away_home_altitude_m")
        if venue_alt is not None:
            gap_km = (venue_alt - (away_alt if away_alt is not None else 0.0)) / 1000.0
            lh += 0.5 * b["altitude_km"] * gap_km
            la -= 0.5 * b["altitude_km"] * gap_km

        # Travel burden on the away side.
        travel = self._get(ctx, "away_travel_km", "travel_km", "venue.travel_km")
        if travel is not None and travel >= 0:
            la -= b["travel_log"] * math.log1p(travel / 1000.0)

        # Weather: suppresses tempo for both teams.
        is_dome = bool(ctx.get("is_dome") or (isinstance(ctx.get("weather"), dict)
                                               and ctx["weather"].get("is_dome")))
        if not is_dome:
            temp = self._get(ctx, "weather.temp", "temp", "temperature")
            if temp is not None:
                excess = max(temp - self.THERMAL_COMFORT_C, 0.0)
                lh += b["heat"] * excess
                la += b["heat"] * excess
            wind = self._get(ctx, "weather.wind_speed", "wind_speed")
            if wind is not None and wind > 0:
                lh += b["wind"] * wind
                la += b["wind"] * wind
        return lh, la

    def _rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mh, ma = self._anchor_rates(ctx)
        dh, da = self._situational_log_adjust(ctx)
        return mh * math.exp(dh), ma * math.exp(da)

    # ================================================================== #
    # Joint score matrix
    # ================================================================== #
    def _grid_size(self, total: float, phi: float) -> int:
        var = total * (1.0 + phi * total)
        p = total / var if var > 0 else 1.0
        if phi > 0 and p < 1.0:
            n = total * p / (1.0 - p)
            hi = stats.nbinom.ppf(1.0 - self.TAIL_EPS, n, p)
        else:
            hi = stats.poisson.ppf(1.0 - self.TAIL_EPS, total)
        return int(hi) + 2

    @staticmethod
    def _bivariate_poisson(l1: float, l2: float, l3: float, n: int) -> np.ndarray:
        k = np.arange(n)
        p1 = stats.poisson.pmf(k, l1)
        p2 = stats.poisson.pmf(k, l2)
        base = np.outer(p1, p2)
        if l3 <= 0:
            return base
        p3 = stats.poisson.pmf(k, l3)
        m = np.zeros((n, n))
        for j in range(n):
            if p3[j] < 1e-300:
                break
            m[j:, j:] += p3[j] * base[: n - j, : n - j]
        return m

    @staticmethod
    def _rho_bounds(lh: float, la: float) -> Tuple[float, float]:
        # tau >= 0 on all four corrected cells
        lo = max(-1.0 / lh, -1.0 / la)
        hi = 1.0 / (lh * la)
        return lo, hi

    def _score_matrix(self, lh: float, la: float, rho: float, phi: float,
                      s3: float) -> np.ndarray:
        n = self._grid_size(lh + la, phi)
        l3_frac = s3 * min(lh, la)

        if phi > 1e-9:
            shape = 1.0 / phi
            x, w = special.roots_genlaguerre(self.QUAD_NODES, shape - 1.0)
            w = w / special.gamma(shape)
            frailty = x * phi                     # mean 1, variance phi
        else:
            frailty, w = np.array([1.0]), np.array([1.0])

        mat = np.zeros((n, n))
        for g, wg in zip(frailty, w):
            Lh, La, L3 = lh * g, la * g, l3_frac * g
            mat += wg * self._bivariate_poisson(Lh - L3, La - L3, L3, n)

        lo, hi = self._rho_bounds(lh, la)
        r = min(max(rho, lo), hi)                 # validity region, not a tuning clamp
        tau = np.ones_like(mat)
        tau[0, 0] = 1.0 - lh * la * r
        tau[0, 1] = 1.0 + lh * r
        tau[1, 0] = 1.0 + la * r
        tau[1, 1] = 1.0 - r
        mat = mat * tau
        return mat / mat.sum()

    # ================================================================== #
    # Public API
    # ================================================================== #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        lh, la = self._rates(game_context)
        s3 = special.expit(self.logit_s3)
        mat = self._score_matrix(lh, la, self.rho, self.phi, s3)
        self.last_matrix = mat

        scores = {(int(i), int(j)): float(mat[i, j])
                  for i in range(mat.shape[0]) for j in range(mat.shape[1])
                  if mat[i, j] > 0}
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            p_home = float(np.tril(mat, -1).sum())
            p_draw = float(np.trace(mat))
            p_away = float(np.triu(mat, 1).sum())
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)

    def price_winner_total(self, game_context: Dict[str, Any],
                           line: float) -> Dict[str, float]:
        """Joint probabilities for every {home, draw, away} x {over, under} cell."""
        lh, la = self._rates(game_context)
        mat = self._score_matrix(lh, la, self.rho, self.phi, special.expit(self.logit_s3))
        n = mat.shape[0]
        i, j = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        tot = i + j
        out: Dict[str, float] = {}
        for side, mask in (("home", i > j), ("draw", i == j), ("away", i < j)):
            out[f"{side}_over"] = float(mat[mask & (tot > line)].sum())
            out[f"{side}_under"] = float(mat[mask & (tot < line)].sum())
            push = float(mat[mask & (tot == line)].sum())
            if push > 0:
                out[f"{side}_push"] = push
        return out

    @staticmethod
    def expected_value(prob_win: float, decimal_odds: float, prob_push: float = 0.0) -> float:
        return prob_win * (decimal_odds - 1.0) - (1.0 - prob_win - prob_push)

    def is_eligible(self, prob_win: float, decimal_odds: float, prob_push: float = 0.0) -> bool:
        return decimal_odds >= 1.40 and self.expected_value(prob_win, decimal_odds, prob_push) > 0

    # ================================================================== #
    # Penalized MLE on settled first-half scores
    # ================================================================== #
    def fit(self, games: Iterable[Tuple[Dict[str, Any], int, int]]) -> Dict[str, float]:
        """games: iterable of (game_context, home_h1_goals, away_h1_goals)."""
        data = [(ctx, int(h), int(a)) for ctx, h, a in games]
        if not data:
            return self.params()

        def unpack(theta: np.ndarray):
            return theta[0], math.exp(theta[1]), special.expit(theta[2]), theta[3]

        prior = np.array([0.0,
                          math.log(max(self.phi, 1e-4)),
                          self.PRIOR_LOGIT_S3,
                          self.PRIOR_KAPPA])
        sd = np.array([self.PRIOR_SD[k] for k in ("rho", "log_phi", "logit_s3", "kappa")])

        def nll(theta: np.ndarray) -> float:
            rho, phi, s3, kappa = unpack(theta)
            saved = self.kappa
            self.kappa = kappa
            total = 0.0
            try:
                for ctx, h, a in data:
                    lh, la = self._rates(ctx)
                    lo, hi = self._rho_bounds(lh, la)
                    if not (lo <= rho <= hi):
                        return 1e12
                    m = self._score_matrix(lh, la, rho, phi, s3)
                    p = m[h, a] if h < m.shape[0] and a < m.shape[1] else 0.0
                    total -= math.log(max(p, 1e-300))
            finally:
                self.kappa = saved
            return total + 0.5 * float(np.sum(((theta - prior) / sd) ** 2))

        res = optimize.minimize(nll, prior, method="Nelder-Mead",
                                options={"xatol": 1e-4, "fatol": 1e-6, "maxiter": 2000})
        rho, phi, _, kappa = unpack(res.x)
        self.rho, self.phi, self.logit_s3, self.kappa = rho, phi, float(res.x[2]), kappa
        self.n_fitted = len(data)
        return self.params()

    def params(self) -> Dict[str, float]:
        return {
            "rho": float(self.rho),
            "phi": float(self.phi),
            "lambda3_share": float(special.expit(self.logit_s3)),
            "kappa": float(self.kappa),
            "first_half_share": float(self._first_half_share(self.kappa)),
            "n_fitted": float(self.n_fitted),
        }