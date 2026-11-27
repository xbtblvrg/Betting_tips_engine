import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class codex_synth_nfl_quarter_2_1127_012054(BaseCandidateModel):
    """
    NFL | quarter_2 | spreads candidate model.

    Structure (first principles):
      1. Q2 points per team = compound sum of discrete scoring events.
         N ~ NegBin (count overdispersion from the observed dispersion index),
         each event draws points from {2, 3, 6, 7, 8}, so margin mass lands on the
         true 3/7/10 lattice instead of a smooth Gaussian.
      2. A shared latent game-state factor (Gauss-Hermite mixture) carries part of
         the excess dispersion. It produces positive score correlation and fatter
         margin tails, which addresses tail mispricing in high-line regimes.
      3. Situational scaling: attack/defense, pace, rest, wind/dome/temperature.
         The home edge is shrunk structurally. The autopsy shows home overrating.
      4. Calibration layer applied to the score lattice as exact cover-mass
         reweighting. It removes three effects:
           - global overconfidence      E[y-p] = -0.1041 -> logit temperature
           - line-magnitude drift       rho(line, resid) = -0.085 -> |line|-dependent temperature
           - home directional bias      E[y-p]_home = -0.2794 -> partially pooled logit offset
         The output stays a valid joint score distribution (non-negative, sums to 1).

    Every coefficient lives in __init__ with its empirical derivation, so a later
    .fit() can overwrite it. No static clamps are applied to output probabilities.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_2_1127_185500",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_2",
        family: str = "spreads",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family, **kwargs)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # ---------------- Empirical moments (autopsy) ----------------
        self.base_home = 6.12
        self.base_away = 5.38
        self.dispersion_index = 1.326
        self.margin_skew = -0.302
        self.n_total = 512.0
        self.n_home = 168.0
        self.resid_global = -0.1041
        self.resid_home = -0.2794
        self.resid_away = 0.0029
        self.rho_line_resid = -0.0847
        self.resid_low_line = -0.0890
        self.resid_high_line = -0.1889

        # Q2 share of full-game points. Used only when the inputs look like
        # full-game expectations. Derived as the Q2 baseline sum over a typical
        # full-game total: (6.12 + 5.38) / ~43.5.
        self.q2_share = (self.base_home + self.base_away) / 43.5

        # ---------------- Scoring-event mechanics ----------------
        # Q2 drives near the two-minute warning convert to TDs at an elevated rate.
        self.td_share_base = 0.58
        self.xp_make = 0.94          # 7 pts
        self.two_pt_share = 0.025    # 8 pts (attempted and converted)
        self.safety_rate = 0.012     # 2-pt events per scoring event
        self.grid_max = 45
        self.k_max = 10

        # Split excess dispersion between a shared latent state and team-specific NB.
        # The negative margin skew implies asymmetric tails driven by game state,
        # so the shared share scales with |skew|.
        self.shared_dispersion_share = min(abs(self.margin_skew) / (1.0 + abs(self.margin_skew)), 0.9)
        self.gh_nodes, self.gh_weights = np.polynomial.hermite_e.hermegauss(7)
        self.gh_weights = self.gh_weights / self.gh_weights.sum()

        # ---------------- Structural home-edge shrinkage ----------------
        # Home cover bias -0.2794 over N=168. Pool partially toward 0 with a prior
        # strength equal to the full sample. The weight then shrinks the home
        # surplus over the away baseline.
        self.home_pool_w = self.n_home / (self.n_home + self.n_total)
        self.home_edge_retention = 1.0 - self.home_pool_w

        # ---------------- Calibration layer ----------------
        # Logit temperature. Overconfidence of 0.104 against an average |p - 0.5|
        # implies slope k0 ~= 1 - 2*|resid|.
        self.k0 = 1.0 - 2.0 * abs(self.resid_global)
        # Line-dependent extra shrink. The high-line residual exceeds low-line
        # by ~0.10, so temperature decays with |h| / sigma_margin.
        self.line_gain = 2.0 * abs(self.resid_high_line - self.resid_low_line) \
            + abs(self.rho_line_resid)
        # Home-side logit offset. dlogit ~= resid / (p(1-p)) at p ~= 0.5, partially pooled.
        self.home_logit_offset = self.home_pool_w * (self.resid_home - self.resid_away) * 4.0 * 0.5

        # Situational sensitivities (log-multiplicative).
        self.rest_coef = 0.012
        self.wind_coef = 0.010
        self.dome_coef = 0.03
        self.cold_coef = 0.004

        self._event_cache: Dict[float, np.ndarray] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _f(x: Any, default: float) -> float:
        try:
            v = float(x)
            if math.isfinite(v):
                return v
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def _logit(p: float) -> float:
        p = min(max(p, 1e-9), 1.0 - 1e-9)
        return math.log(p / (1.0 - p))

    @staticmethod
    def _sigmoid(z: float) -> float:
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        """Return a multiplicative rating (1.0 = neutral)."""
        val = None
        blob = ctx.get(side)
        if isinstance(blob, dict):
            val = blob.get(kind, blob.get(f"{kind}_rating"))
        if val is None:
            val = ctx.get(f"{side}_{kind}", ctx.get(f"{side}_{kind}_rating"))
        v = self._f(val, 1.0)
        if v <= 0:
            return 1.0
        # Ratings may be given as ratios (~1) or as additive z-scores (~0).
        if v < 0.2 or v > 5.0:
            return math.exp(math.tanh(v / 10.0) * 0.25)
        return v

    # ------------------------------------------------------- event lattice
    def _event_powers(self, td_share: float) -> np.ndarray:
        key = round(td_share, 3)
        cached = self._event_cache.get(key)
        if cached is not None:
            return cached
        G = self.grid_max
        ev = np.zeros(G + 1)
        s = self.safety_rate
        td = td_share * (1.0 - s)
        fg = (1.0 - td_share) * (1.0 - s)
        ev[2] += s
        ev[3] += fg
        ev[7] += td * self.xp_make
        ev[8] += td * self.two_pt_share
        ev[6] += td * max(1.0 - self.xp_make - self.two_pt_share, 0.0)
        ev /= ev.sum()
        powers = np.zeros((self.k_max + 1, G + 1))
        powers[0, 0] = 1.0
        for k in range(1, self.k_max + 1):
            powers[k] = np.convolve(powers[k - 1], ev)[: G + 1]
        if len(self._event_cache) > 64:
            self._event_cache.clear()
        self._event_cache[key] = powers
        return powers

    def _event_mean(self, td_share: float) -> float:
        s = self.safety_rate
        td = td_share * (1.0 - s)
        fg = (1.0 - td_share) * (1.0 - s)
        pts6 = max(1.0 - self.xp_make - self.two_pt_share, 0.0)
        return 2 * s + 3 * fg + td * (7 * self.xp_make + 8 * self.two_pt_share + 6 * pts6)

    def _team_pmf(self, mu_pts: float, td_share: float, r_nb: float) -> np.ndarray:
        powers = self._event_powers(td_share)
        lam = max(mu_pts / self._event_mean(td_share), 1e-6)
        k = np.arange(self.k_max + 1)
        p = r_nb / (r_nb + lam)
        w = stats.nbinom.pmf(k, r_nb, p)
        w = w / w.sum()
        pmf = w @ powers
        tot = pmf.sum()
        return pmf / tot if tot > 0 else powers[0]

    # ----------------------------------------------------------- expectations
    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        bh = self._f(ctx.get("baseline_home"), self.base_home)
        ba = self._f(ctx.get("baseline_away"), self.base_away)
        mh = self._f(ctx.get("home_expected"), bh)
        ma = self._f(ctx.get("away_expected"), ba)
        # Detect full-game inputs and project them to Q2.
        if mh + ma > 2.2 * (self.base_home + self.base_away):
            mh *= self.q2_share
            ma *= self.q2_share
        if bh + ba > 2.2 * (self.base_home + self.base_away):
            bh *= self.q2_share
            ba *= self.q2_share
        # Anchor to empirical reality by geometric pooling with the baseline.
        mh = math.sqrt(max(mh, 0.1) * max(bh, 0.1))
        ma = math.sqrt(max(ma, 0.1) * max(ba, 0.1))

        # Attack/defense exposure.
        mh *= math.sqrt(self._team_rating(ctx, "home", "attack") /
                        self._team_rating(ctx, "away", "defense"))
        ma *= math.sqrt(self._team_rating(ctx, "away", "attack") /
                        self._team_rating(ctx, "home", "defense"))

        # Pace / tempo (shared).
        pace = self._f(ctx.get("pace", ctx.get("tempo")), 1.0)
        if pace > 5.0:
            pace = pace / 63.0  # plays per game, normalised by league average
        pace = math.exp(0.5 * math.tanh(math.log(max(pace, 0.2))))
        mh *= pace
        ma *= pace

        # Rest differential (smooth, saturating).
        rest_d = self._f(ctx.get("home_rest_days"), 7.0) - self._f(ctx.get("away_rest_days"), 7.0)
        rest_eff = self.rest_coef * 7.0 * math.tanh(rest_d / 7.0)
        mh *= math.exp(rest_eff / 2.0)
        ma *= math.exp(-rest_eff / 2.0)

        # Weather.
        is_dome = bool(ctx.get("is_dome", False)) or str(ctx.get("venue", "")).lower().find("dome") >= 0
        wind = 0.0 if is_dome else max(self._f(ctx.get("wind_speed"), 0.0), 0.0)
        temp = self._f(ctx.get("temp"), 60.0)
        wx = -self.wind_coef * wind - self.cold_coef * max(32.0 - temp, 0.0) * (not is_dome)
        wx += self.dome_coef if is_dome else 0.0
        mh *= math.exp(wx)
        ma *= math.exp(wx)

        # Structural shrinkage of the home surplus (autopsy: home overrated).
        center = 0.5 * (mh + ma)
        half = 0.5 * (mh - ma)
        base_half = 0.5 * (self.base_home - self.base_away)
        excess = half - base_half * (mh + ma) / (self.base_home + self.base_away)
        half = half - (1.0 - self.home_edge_retention) * max(excess, 0.0) \
            - self.home_pool_w * base_half
        mh, ma = center + half, center - half

        # TD share: wind suppresses TDs relative to FGs only weakly. FG range
        # suffers too, so the net effect on TD share is a mild rise.
        td_share = self.td_share_base + 0.002 * min(wind, 30.0)
        return max(mh, 0.2), max(ma, 0.2), td_share

    # ------------------------------------------------------------ handicap
    def _handicap(self, ctx: Dict[str, Any]) -> Optional[float]:
        for key in ("home_handicap", "spread_line", "handicap", "line"):
            if ctx.get(key) is not None:
                v = self._f(ctx.get(key), float("nan"))
                if math.isfinite(v):
                    return v
        return None

    # ---------------------------------------------------------------- core
    def _score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        mh, ma, td_share = self._expected_points(ctx)
        D = max(self.dispersion_index, 1.0 + 1e-6)
        # Excess dispersion at the event-count level: Var/Mean - 1.
        excess = D - 1.0
        shared = self.shared_dispersion_share * excess
        own = excess - shared
        lam_mean = 0.5 * (mh + ma) / self._event_mean(td_share)
        sigma_shared = math.sqrt(max(shared / max(lam_mean, 1e-6), 1e-8))
        r_h = max(mh / self._event_mean(td_share) / max(own, 1e-6), 0.5)
        r_a = max(ma / self._event_mean(td_share) / max(own, 1e-6), 0.5)

        G = self.grid_max
        M = np.zeros((G + 1, G + 1))
        for z, w in zip(self.gh_nodes, self.gh_weights):
            f = math.exp(sigma_shared * z - 0.5 * sigma_shared ** 2)
            ph = self._team_pmf(mh * f, td_share, r_h)
            pa = self._team_pmf(ma * f, td_share, r_a)
            M += w * np.outer(ph, pa)
        M = np.clip(M, 0.0, None)
        return M / M.sum()

    def _calibrate(self, M: np.ndarray, h: float) -> np.ndarray:
        G = self.grid_max
        idx = np.arange(G + 1)
        margin = idx[:, None] - idx[None, :]
        adj = margin + h
        home_mask = adj > 1e-9
        away_mask = adj < -1e-9
        p_h = float(M[home_mask].sum())
        p_a = float(M[away_mask].sum())
        p_push = max(1.0 - p_h - p_a, 0.0)
        live = p_h + p_a
        if live <= 1e-12 or p_h <= 1e-12 or p_a <= 1e-12:
            return M

        p_cond = p_h / live
        mu_m = float((M * margin).sum())
        sd_m = math.sqrt(max(float((M * margin ** 2).sum()) - mu_m ** 2, 1e-6))
        k = self.k0 / (1.0 + self.line_gain * abs(h) / sd_m)
        z = k * self._logit(p_cond) + self.home_logit_offset
        p_target = self._sigmoid(z)

        a = p_target * live / p_h
        b = (1.0 - p_target) * live / p_a
        out = M.copy()
        out[home_mask] *= a
        out[away_mask] *= b
        out = np.clip(out, 0.0, None)
        s = out.sum()
        return out / s if s > 0 else M

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        ctx = game_context if isinstance(game_context, dict) else {}
        try:
            M = self._score_matrix(ctx)
            h = self._handicap(ctx)
            h_eff = h if h is not None else 0.0
            M = self._calibrate(M, h_eff)
        except Exception:
            M = np.zeros((self.grid_max + 1, self.grid_max + 1))
            M[0, 0] = 1.0
            h_eff = 0.0

        G = self.grid_max
        idx = np.arange(G + 1)
        margin = idx[:, None] - idx[None, :]
        adj = margin + h_eff
        p_home = float(M[adj > 1e-9].sum())
        p_away = float(M[adj < -1e-9].sum())
        p_draw = max(1.0 - p_home - p_away, 0.0)
        tot = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / tot, p_draw / tot, p_away / tot

        nz = np.argwhere(M > 1e-12)
        scores = {(int(i), int(j)): float(M[i, j]) for i, j in nz}
        s = sum(scores.values())
        scores = {k_: v / s for k_, v in scores.items()}

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        except Exception:
            pass
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})