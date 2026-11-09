"""
Candidate model for NBA|quarter_4|winner_total.

Each team's fourth-quarter points are modeled as a game-state-dependent
compound Poisson process, priced over the margin entering the quarter (M3).

  * Possessions:  N_i ~ Poisson(lambda_i). Each possession scores X in {0,1,2,3}.
    The distribution of X is built from shot mix: 3PA share, 3P%, 2P%, FT-trip
    rate, FT%, turnover rate and offensive-rebound second chances.
    Var/Mean = E[X^2]/E[X] is therefore a derived quantity (about 2.2-2.4 for
    NBA shot mixes), not an assumed 1.0.
  * Blowout regime: pace decays smoothly in |M3|, and the margin drift
    -kappa(|M3|)*M3 mean-reverts against the leader in place of a static home edge.
  * Close-game regime ("foul trap"): when the projected late margin is within the
    close band, the leader receives a geometric number of extra 2-shot FT trips.
    The trailer receives a geometric number of extra high-3PA possessions.
    Both fatten the right tail of the total.
  * Anchoring: the inputs home_expected / away_expected are treated as
    unconditional Q4 means. Baseline intensities are solved by fixed-point
    iteration so that E_M3[state mean] reproduces them exactly. The state model
    redistributes probability mass; it does not invent mean drift.
  * Pregame: integrate over M3 ~ Normal(3*(mu_h - mu_a), sd) with Gauss-Hermite
    quadrature. In-play: condition on the observed M3.
  * The tie mass is kept explicitly as 'draw'. Settlement (push/void) is applied
    downstream, or the 'tie_rule' config renormalizes it for 2-way markets.

Honest caveats:
  * The structural priors in DEFAULT_CONFIG (kappa, trap trips, pace decay,
    shot-mix league norms) are domain-informed starting values. They are not
    fitted parameters. They should be estimated from play-by-play data before
    deployment; each is exposed in the config to support that.
  * With 0 settled wagers in this segment, no ROI or Brier claim can be made for
    this model. The quoted benchmark ROIs (+42% to +122%) are far outside what is
    realistically sustainable in efficient NBA markets. Validate with proper
    out-of-sample backtests before staking.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - fall back to pre-loaded scope
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


DEFAULT_CONFIG: Dict[str, Any] = {
    # ---- Unconditional anchors (overridden by game_context) ----
    "default_home_expected": 28.0,
    "default_away_expected": 27.0,

    # ---- League shot-mix priors (per possession / per attempt) ----
    "three_rate": 0.39,        # share of FGA that are 3PA
    "three_pct": 0.36,
    "two_pct": 0.54,
    "ft_trip_rate": 0.12,      # possessions ending in a 2-shot FT trip
    "ft_pct": 0.78,
    "tov_rate": 0.13,
    "oreb_rate": 0.25,

    # ---- Desperation possessions (trailing team, close & late) ----
    "desp_three_rate": 0.65,
    "desp_oreb_rate": 0.20,

    # ---- Blowout regime ----
    "pace_drop_max": 0.06,     # max fractional pace reduction in garbage time
    "blowout_center": 15.0,    # |M3| where the bench-unit regime is half-on
    "blowout_width": 4.0,
    "kappa_base": 0.03,        # baseline Q4 mean reversion per point of lead
    "kappa_blowout": 0.07,     # additional reversion in blowouts

    # ---- Close-game foul trap ----
    "close_band": 6.0,         # |margin| at which the trailer fouls intentionally
    "trap_window_frac": 10.0 / 12.0,  # fraction of Q4 elapsed when trap starts
    "trap_trips": 2.5,         # expected extra FT trips given the trap is active
    "desp_response": 0.9,      # trailer's extra possessions per leader FT trip

    # ---- Situational adjustments (shrunk; the inputs likely embed them already) ----
    "matchup_weight": 0.35,
    "rest_ref_days": 2.0,
    "rest_coef": 0.012,        # relative efficiency per missing rest day
    "pace_weight": 0.5,
    "league_pace": 99.0,

    # ---- Pregame M3 distribution ----
    "strength_sd_per_quarter": 2.0,   # per-quarter team-strength uncertainty
    "gh_nodes": 21,

    # ---- Numerics / output ----
    "grid_size": 256,
    "anchor_iterations": 6,
    "tie_rule": "draw",        # 'draw' (3-way) or 'split' (renormalize to 2-way)
    "min_odds": 1.40,
}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class CandidateModel(BaseCandidateModel):
    """Game-state-dependent compound Poisson model for NBA Q4 winner/total."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_4_1107_160119",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_4",
        family: str = "winner_total",
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.cfg: Dict[str, Any] = dict(DEFAULT_CONFIG)
        if config:
            self.cfg.update(config)

        n = int(self.cfg["grid_size"])
        self._z = np.exp(2j * np.pi * np.arange(n) / n)  # PGF evaluation points

        self.last_total_pmf: Optional[np.ndarray] = None
        self.last_home_pmf: Optional[np.ndarray] = None
        self.last_away_pmf: Optional[np.ndarray] = None
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Context extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(v: Any) -> Optional[float]:
        try:
            f = float(v)
            return f if math.isfinite(f) else None
        except (TypeError, ValueError):
            return None

    def _get(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Look up '<side>_<key>', ctx[side][key], or ctx[side + '_stats'][key]."""
        v = self._num(ctx.get(f"{side}_{key}"))
        if v is not None:
            return v
        for container_key in (side, f"{side}_stats", f"{side}_team"):
            sub = ctx.get(container_key)
            if isinstance(sub, dict):
                v = self._num(sub.get(key))
                if v is not None:
                    return v
        return None

    def _team_stat(self, ctx: Dict[str, Any], side: str, key: str) -> float:
        v = self._get(ctx, side, key)
        return float(self.cfg[key]) if v is None else v

    def _observed_m3(self, ctx: Dict[str, Any]) -> Optional[float]:
        for k in ("margin_entering_q4", "m3", "margin_after_q3", "home_margin_q3"):
            v = self._num(ctx.get(k))
            if v is not None:
                return v
        for hk, ak in (
            ("home_score_q3", "away_score_q3"),
            ("home_score_through_q3", "away_score_through_q3"),
            ("home_score_3q", "away_score_3q"),
        ):
            h, a = self._num(ctx.get(hk)), self._num(ctx.get(ak))
            if h is not None and a is not None:
                return h - a
        return None

    # ------------------------------------------------------------------ #
    # Possession scoring distributions
    # ------------------------------------------------------------------ #
    @staticmethod
    def _possession_pmf(three_rate: float, p3: float, p2: float, ft_trip: float,
                        ft_pct: float, tov: float, oreb: float) -> np.ndarray:
        """P(X = 0..3) for one possession, including OREB second-chance loops."""
        three_rate = min(max(three_rate, 0.0), 1.0)
        shot_share = max(1.0 - tov - ft_trip, 0.0)
        make3 = three_rate * p3
        make2 = (1.0 - three_rate) * p2
        miss = max(1.0 - make3 - make2, 0.0)
        loop = 1.0 / max(1.0 - oreb * miss, 1e-9)  # geometric second chances
        pmf = np.zeros(4)
        pmf[3] = shot_share * make3 * loop
        pmf[2] = shot_share * make2 * loop + ft_trip * ft_pct ** 2
        pmf[1] = ft_trip * 2.0 * ft_pct * (1.0 - ft_pct)
        pmf[0] = max(1.0 - pmf[1:].sum(), 0.0)
        return pmf / pmf.sum()

    @staticmethod
    def _ft_trip_pmf(ft_pct: float) -> np.ndarray:
        return np.array([(1 - ft_pct) ** 2, 2 * ft_pct * (1 - ft_pct), ft_pct ** 2])

    def _pgf(self, sev: np.ndarray) -> np.ndarray:
        return sum(sev[j] * self._z ** j for j in range(len(sev)))

    def _team_pmf(self, lam: float, sev_pgf: np.ndarray,
                  m_ft: float, ft_pgf: np.ndarray,
                  m_desp: float, desp_pgf: np.ndarray) -> np.ndarray:
        """Compound Poisson (+ two compound geometric) pmf via exact PGF inversion."""
        g = np.exp(lam * (sev_pgf - 1.0))
        g = g / (1.0 + m_ft * (1.0 - ft_pgf))
        g = g / (1.0 + m_desp * (1.0 - desp_pgf))
        pmf = np.real(np.fft.fft(g)) / len(g)
        pmf[pmf < 0.0] = 0.0
        return pmf / pmf.sum()

    # ------------------------------------------------------------------ #
    # Game-state mechanics
    # ------------------------------------------------------------------ #
    def _pace_factor(self, m3: float) -> float:
        c = self.cfg
        s = _sigmoid((abs(m3) - c["blowout_center"]) / c["blowout_width"])
        return 1.0 - c["pace_drop_max"] * s

    def _kappa(self, m3: float) -> float:
        c = self.cfg
        s = _sigmoid((abs(m3) - c["blowout_center"]) / c["blowout_width"])
        return c["kappa_base"] + c["kappa_blowout"] * s

    def _trap(self, m3: float, mu_h: float, mu_a: float, sd_q: float
              ) -> Tuple[float, float, float, float]:
        """Expected extra (home FT trips, home desperation poss, away FT trips, away desp)."""
        c = self.cfg
        f = c["trap_window_frac"]
        kap = self._kappa(m3)
        center = m3 + f * (mu_h - mu_a) - f * kap * m3
        sd = max(sd_q * math.sqrt(f), 1e-6)
        band = c["close_band"]
        p_home_leads_close = _norm_cdf((band - center) / sd) - _norm_cdf(-center / sd)
        p_away_leads_close = _norm_cdf(-center / sd) - _norm_cdf((-band - center) / sd)
        t, r = c["trap_trips"], c["desp_response"]
        return (t * p_home_leads_close, t * p_away_leads_close * r,
                t * p_away_leads_close, t * p_home_leads_close * r)

    # ------------------------------------------------------------------ #
    # Situational adjustment of unconditional means
    # ------------------------------------------------------------------ #
    def _situational_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        c = self.cfg
        mu_h = self._num(ctx.get("home_expected"))
        mu_a = self._num(ctx.get("away_expected"))
        mu_h = c["default_home_expected"] if mu_h is None or mu_h <= 0 else mu_h
        mu_a = c["default_away_expected"] if mu_a is None or mu_a <= 0 else mu_a

        # Matchup: scale-free relative attack x opponent-defense (points-allowed index).
        att_h, att_a = self._get(ctx, "home", "attack"), self._get(ctx, "away", "attack")
        def_h, def_a = self._get(ctx, "home", "defense"), self._get(ctx, "away", "defense")
        w = c["matchup_weight"]
        if att_h and att_a and def_h and def_a and min(att_h, att_a, def_h, def_a) > 0:
            att_mean, def_mean = 0.5 * (att_h + att_a), 0.5 * (def_h + def_a)
            mu_h *= ((att_h / att_mean) * (def_a / def_mean)) ** w
            mu_a *= ((att_a / att_mean) * (def_h / def_mean)) ** w

        # Rest: total-preserving relative fatigue shift (Q4 legs effect).
        r_h = self._num(ctx.get("home_rest_days"))
        r_a = self._num(ctx.get("away_rest_days"))
        if r_h is not None and r_a is not None:
            ref = c["rest_ref_days"]
            fat_h = max(0.0, ref - min(r_h, ref))
            fat_a = max(0.0, ref - min(r_a, ref))
            shift = c["rest_coef"] * (fat_a - fat_h) * 0.5 * (mu_h + mu_a)
            mu_h += 0.5 * shift
            mu_a -= 0.5 * shift

        # Tempo: shrunk relative pace scaling (inputs may already embed pace).
        p_h, p_a = self._get(ctx, "home", "pace"), self._get(ctx, "away", "pace")
        game_pace = self._num(ctx.get("pace"))
        if game_pace is None and p_h and p_a:
            game_pace = 0.5 * (p_h + p_a)
        league_pace = self._num(ctx.get("league_pace")) or c["league_pace"]
        if game_pace and game_pace > 0:
            scale = (game_pace / league_pace) ** c["pace_weight"]
            mu_h *= scale
            mu_a *= scale

        # Indoor sport: weather/wind fields are intentionally ignored.
        return mu_h, mu_a

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        c = self.cfg
        ctx = game_context or {}
        in_h, in_a = self._situational_means(ctx)

        # Per-team possession severity distributions.
        sev, desp_sev, ft_sev = {}, {}, {}
        for side in ("home", "away"):
            st = {k: self._team_stat(ctx, side, k) for k in
                  ("three_rate", "three_pct", "two_pct", "ft_trip_rate",
                   "ft_pct", "tov_rate", "oreb_rate")}
            sev[side] = self._possession_pmf(
                st["three_rate"], st["three_pct"], st["two_pct"],
                st["ft_trip_rate"], st["ft_pct"], st["tov_rate"], st["oreb_rate"])
            desp_sev[side] = self._possession_pmf(
                c["desp_three_rate"], st["three_pct"], st["two_pct"],
                st["ft_trip_rate"], st["ft_pct"], st["tov_rate"], c["desp_oreb_rate"])
            ft_sev[side] = self._ft_trip_pmf(st["ft_pct"])

        pts = np.arange(4)
        ex = {s: float(sev[s] @ pts) for s in sev}
        ex2 = {s: float(sev[s] @ pts ** 2) for s in sev}
        disp = {s: ex2[s] / ex[s] for s in sev}                 # derived Var/Mean
        ex_desp = {s: float(desp_sev[s] @ pts) for s in sev}
        ex_ft = {s: float(ft_sev[s] @ np.arange(3)) for s in sev}

        # Pregame M3 distribution (always needed for anchoring).
        nodes, weights = np.polynomial.hermite_e.hermegauss(int(c["gh_nodes"]))
        weights = weights / weights.sum()
        m0 = 3.0 * (in_h - in_a)
        spread_m3 = self._num(ctx.get("pregame_m3_mean"))
        if spread_m3 is not None:
            m0 = spread_m3

        def m3_sd(sd_q: float) -> float:
            override = self._num(ctx.get("pregame_m3_sd"))
            if override is not None and override > 0:
                return override
            return math.sqrt(3.0 * sd_q ** 2 + (3.0 * c["strength_sd_per_quarter"]) ** 2)

        # Fixed-point anchoring: E_M3[state mean] == unconditional input mean.
        base_h, base_a = in_h, in_a
        for _ in range(int(c["anchor_iterations"])):
            sd_q = math.sqrt(base_h * disp["home"] + base_a * disp["away"])
            m3_nodes = m0 + m3_sd(sd_q) * nodes
            e_g = e_drift = e_trap_h = e_trap_a = 0.0
            for m3, wt in zip(m3_nodes, weights):
                g = self._pace_factor(m3)
                drift = -self._kappa(m3) * m3
                fh, dh, fa, da = self._trap(m3, base_h * g, base_a * g, sd_q)
                e_g += wt * g
                e_drift += wt * drift
                e_trap_h += wt * (fh * ex_ft["home"] + dh * ex_desp["home"])
                e_trap_a += wt * (fa * ex_ft["away"] + da * ex_desp["away"])
            base_h = max((in_h - 0.5 * e_drift - e_trap_h) / e_g, 1e-6)
            base_a = max((in_a + 0.5 * e_drift - e_trap_a) / e_g, 1e-6)

        sd_q = math.sqrt(base_h * disp["home"] + base_a * disp["away"])

        # Integration nodes: observed M3 (in-play) or the pregame distribution.
        obs = self._observed_m3(ctx)
        if obs is not None:
            m3_list, w_list = np.array([obs]), np.array([1.0])
        else:
            m3_list, w_list = m0 + m3_sd(sd_q) * nodes, weights

        pgf = {s: self._pgf(sev[s]) for s in sev}
        pgf_desp = {s: self._pgf(desp_sev[s]) for s in sev}
        pgf_ft = {s: self._pgf(ft_sev[s]) for s in sev}

        n = int(c["grid_size"])
        pmf_h_mix = np.zeros(n)
        pmf_a_mix = np.zeros(n)
        total_mix = np.zeros(2 * n - 1)
        p_home = p_draw = p_away = 0.0

        for m3, wt in zip(m3_list, w_list):
            g = self._pace_factor(m3)
            drift = -self._kappa(m3) * m3
            mu_h_s = max(base_h * g + 0.5 * drift, 1e-6)
            mu_a_s = max(base_a * g - 0.5 * drift, 1e-6)
            fh, dh, fa, da = self._trap(m3, base_h * g, base_a * g, sd_q)

            ph = self._team_pmf(mu_h_s / ex["home"], pgf["home"],
                                fh, pgf_ft["home"], dh, pgf_desp["home"])
            pa = self._team_pmf(mu_a_s / ex["away"], pgf["away"],
                                fa, pgf_ft["away"], da, pgf_desp["away"])

            cdf_a = np.cumsum(pa)
            d = float(ph @ pa)
            h = float(ph[1:] @ cdf_a[:-1])
            p_draw += wt * d
            p_home += wt * h
            p_away += wt * max(1.0 - h - d, 0.0)
            pmf_h_mix += wt * ph
            pmf_a_mix += wt * pa
            total_mix += wt * np.convolve(ph, pa)

        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s
        if str(c["tie_rule"]).lower() == "split":
            nd = p_home + p_away
            p_home, p_away, p_draw = p_home / nd, p_away / nd, 0.0

        self.last_home_pmf = pmf_h_mix
        self.last_away_pmf = pmf_a_mix
        self.last_total_pmf = total_mix / total_mix.sum()
        grid = np.arange(len(self.last_total_pmf))
        t_mean = float(self.last_total_pmf @ grid)
        t_var = float(self.last_total_pmf @ grid ** 2) - t_mean ** 2
        self.last_diagnostics = {
            "input_means": (in_h, in_a),
            "anchored_base": (base_h, base_a),
            "derived_dispersion": (disp["home"], disp["away"]),
            "total_mean": t_mean,
            "total_var_over_mean": t_var / t_mean if t_mean > 0 else float("nan"),
            "in_play_m3": obs,
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        }

        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )

    # ------------------------------------------------------------------ #
    # Total-market helpers (use the most recent predict_distribution call)
    # ------------------------------------------------------------------ #
    def prob_total_over(self, line: float) -> Tuple[float, float, float]:
        """Return (P(over), P(push), P(under)) for a Q4 total line."""
        if self.last_total_pmf is None:
            raise RuntimeError("Call predict_distribution first.")
        grid = np.arange(len(self.last_total_pmf))
        p_over = float(self.last_total_pmf[grid > line].sum())
        p_push = float(self.last_total_pmf[grid == line].sum())
        return p_over, p_push, max(1.0 - p_over - p_push, 0.0)

    def expected_value(self, p_win: float, decimal_odds: float, p_push: float = 0.0) -> float:
        """EV per unit stake; eligible if odds >= min_odds and EV > 0 (no other caps)."""
        if decimal_odds < self.cfg["min_odds"]:
            return float("-inf")
        p_lose = max(1.0 - p_win - p_push, 0.0)
        return p_win * (decimal_odds - 1.0) - p_lose