import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from numpy.polynomial.hermite_e import hermegauss
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # DiscreteDistribution is pre-loaded into execution scope
    pass


class CodexSynthNBAQuarter4Totals(BaseCandidateModel):
    """
    NBA | quarter_4 | totals  --  Possession-compound, game-state-mixture model.

    Structural derivation
    ---------------------
    1. Fourth-quarter team points are a random sum S = sum_{k=1..N} X_k where N is
       the number of team possessions in the quarter and X_k in {0,1,2,3} is the
       points produced by possession k.  Both teams share (approximately) the same
       N because possessions alternate, which induces the positive home/away score
       correlation that independent-Poisson totals models ignore.

           E[S]   = E[N] E[X]
           Var[S] = E[N] Var[X] + Var[N] E[X]^2

    2. Dispersion diagnosis.  The reported Var/Mean index (0.114) is *below* the
       mechanical floor Var[X]/E[X] (~1.2 for basketball scoring units) that holds
       even when N is fixed, so it cannot describe raw quarter totals.  The model
       therefore tests feasibility of any supplied index: if feasible it solves
       Var[N] exactly from it; if infeasible it falls back to the physical
       possession-count variance.  No clamp is applied - the index is either
       physically admissible or it is not.

    3. Q4 is a regime-switching quarter.  The game state entering Q4 (margin after
       three quarters, d3) is integrated out by Gauss-Hermite quadrature:
         * close states  -> late intentional fouling, extra possessions (right tail)
         * decided states-> bench units, slower pace, lower efficiency (left mass)
       "Decidedness" g = 2*Phi(|d3| / sd_Q4_margin) - 1 is derived from the
       model's *own* quarter-margin variance, so no arbitrary blowout threshold.
       The mixture is mean-preserving: it reshapes tails without biasing the mean.

    4. Cash-leak repair.  Losses concentrated on overs => upstream expectations
       were biased high and the right tail was over-weighted.  The quarter mean is
       partially pooled (log-space) toward the empirical anchor (28 / 27) scaled
       by situational factors, and over-mass is reduced by the explicit
       blowout/bench regime instead of a symmetric Gaussian/NegBin tail.

    All tunables live in `self.params` and can be overridden per call through
    game_context['model_params'] for out-of-sample recalibration.
    """

    DEFAULT_PARAMS: Dict[str, float] = {
        # Empirical anchors from the settled forensic sample
        "anchor_home": 28.0,
        "anchor_away": 27.0,
        "observed_dispersion_index": 0.114,
        # League structure (overridable from context)
        "league_ppp": 1.14,                  # points per possession
        "league_pace": 99.5,                 # possessions per 48 min
        "full_game_reference_total": 229.0,  # used only if inputs are full-game
        "full_game_detect_ratio": 2.0,       # input total > ratio*anchor => full game
        # Partial pooling of upstream expectation vs. situational anchor prior
        "context_weight": 0.55,
        # Scoring-unit composition among scoring possessions (1, 2, 3 points)
        "share_1pt": 0.10,
        "share_2pt": 0.62,
        "share_3pt": 0.28,
        # Possession-count variance per possession (fallback when index infeasible)
        "pace_variance_ratio": 0.16,
        # Q4 game-state dynamics
        "foul_possession_boost": 0.06,   # extra possessions in a tied game state
        "bench_pace_drag": 0.04,         # possession loss in a fully decided state
        "bench_efficiency_drag": 0.05,   # ppp loss in a fully decided state
        "margin_uncertainty_sd": 3.0,    # pregame strength uncertainty (pts)
        "margin_nodes": 9,
        # Fatigue (legs matter most in Q4)
        "rest_offense_gamma": 0.012,
        "rest_defense_gamma": 0.008,
        "rest_reference_days": 2.0,
        # Numerics
        "tail_sd": 10.0,
        "prob_floor": 1e-12,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_4_1117_044347",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_4",
        family: str = "totals",
        **params: Any,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.params: Dict[str, float] = dict(self.DEFAULT_PARAMS)
        for key, value in params.items():
            if key in self.params:
                self.params[key] = float(value)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            x = float(value)
        except (TypeError, ValueError):
            return default
        return x if math.isfinite(x) else default

    @staticmethod
    def _team(ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        block = ctx.get(side)
        return block if isinstance(block, dict) else {}

    def _team_value(self, ctx: Dict[str, Any], side: str, keys: List[str]) -> Optional[float]:
        team = self._team(ctx, side)
        for k in keys:
            v = self._num(team.get(k))
            if v is not None:
                return v
            v = self._num(ctx.get(f"{side}_{k}"))
            if v is not None:
                return v
        return None

    def _merged_params(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        p = dict(self.params)
        overrides = ctx.get("model_params")
        if isinstance(overrides, dict):
            for k, v in overrides.items():
                fv = self._num(v)
                if k in p and fv is not None:
                    p[k] = fv
        for ctx_key, p_key in (
            ("league_ppp", "league_ppp"),
            ("league_pace", "league_pace"),
            ("league_full_game_total", "full_game_reference_total"),
            ("observed_dispersion_index", "observed_dispersion_index"),
        ):
            fv = self._num(ctx.get(ctx_key))
            if fv is not None and fv > 0:
                p[p_key] = fv
        return p

    # ------------------------------------------------------------------ #
    # Situational factors
    # ------------------------------------------------------------------ #
    def _attack_defense_factors(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """
        Multiplicative scoring factors from attack strength and defensive exposure
        (points-conceded orientation).  Reference is the geometric mean of the
        four supplied ratings, so the scale of the inputs is irrelevant.
        """
        att_keys = ["attack", "offense", "off_rating", "offensive_rating"]
        def_keys = ["defense", "defence", "def_rating", "defensive_rating", "defense_exposure"]
        ah = self._team_value(ctx, "home", att_keys)
        dh = self._team_value(ctx, "home", def_keys)
        aa = self._team_value(ctx, "away", att_keys)
        da = self._team_value(ctx, "away", def_keys)
        vals = [ah, dh, aa, da]
        if any(v is None or v <= 0 for v in vals):
            return 1.0, 1.0
        ref = math.exp(sum(math.log(v) for v in vals) / 4.0)
        f_home = math.sqrt((ah / ref) * (da / ref))
        f_away = math.sqrt((aa / ref) * (dh / ref))
        return f_home, f_away

    def _rest_factors(self, ctx: Dict[str, Any], p: Dict[str, float]) -> Tuple[float, float]:
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        ref = math.log1p(p["rest_reference_days"])
        dh = (math.log1p(max(rh, 0.0)) - ref) if rh is not None else 0.0
        da = (math.log1p(max(ra, 0.0)) - ref) if ra is not None else 0.0
        go, gd = p["rest_offense_gamma"], p["rest_defense_gamma"]
        f_home = math.exp(go * dh - gd * da)
        f_away = math.exp(go * da - gd * dh)
        return f_home, f_away

    def _pace(self, ctx: Dict[str, Any]) -> Optional[float]:
        direct = self._num(ctx.get("pace"))
        if direct is not None and direct > 0:
            return direct
        hp = self._team_value(ctx, "home", ["pace", "tempo"])
        ap = self._team_value(ctx, "away", ["pace", "tempo"])
        pts = [v for v in (hp, ap) if v is not None and v > 0]
        if not pts:
            return None
        return math.exp(sum(math.log(v) for v in pts) / len(pts))

    # ------------------------------------------------------------------ #
    # Mean resolution
    # ------------------------------------------------------------------ #
    def _resolve_means(self, ctx: Dict[str, Any], p: Dict[str, float]) -> Tuple[float, float, float]:
        anchor_h, anchor_a = p["anchor_home"], p["anchor_away"]
        anchor_total = anchor_h + anchor_a

        pace = self._pace(ctx)
        pace_factor = (pace / p["league_pace"]) if pace is not None else 1.0
        ad_h, ad_a = self._attack_defense_factors(ctx)
        rs_h, rs_a = self._rest_factors(ctx, p)

        prior_h = anchor_h * ad_h * rs_h * pace_factor
        prior_a = anchor_a * ad_a * rs_a * pace_factor

        ch = self._num(ctx.get("home_expected"))
        ca = self._num(ctx.get("away_expected"))
        if ch is not None and ca is not None and ch > 0 and ca > 0:
            if ch + ca > p["full_game_detect_ratio"] * anchor_total:
                share = anchor_total / p["full_game_reference_total"]
                ch, ca = ch * share, ca * share
            w = p["context_weight"]
            mu_h = math.exp(w * math.log(ch) + (1.0 - w) * math.log(prior_h))
            mu_a = math.exp(w * math.log(ca) + (1.0 - w) * math.log(prior_a))
        else:
            mu_h, mu_a = prior_h, prior_a

        if pace is not None:
            n_bar = pace / 4.0
        else:
            n_bar = (mu_h + mu_a) / (2.0 * p["league_ppp"])
        return mu_h, mu_a, n_bar

    # ------------------------------------------------------------------ #
    # Possession-level scoring unit
    # ------------------------------------------------------------------ #
    @staticmethod
    def _x_pmf(ppp: float, p: Dict[str, float]) -> np.ndarray:
        c = np.array([p["share_1pt"], p["share_2pt"], p["share_3pt"]], dtype=float)
        c = c / c.sum()
        e_points = float(c @ np.array([1.0, 2.0, 3.0]))
        q = ppp / e_points
        if q >= 1.0:  # beyond physically attainable scoring frequency
            q = 1.0
        pmf = np.empty(4)
        pmf[0] = 1.0 - q
        pmf[1:] = q * c
        return pmf

    @staticmethod
    def _moments(pmf: np.ndarray) -> Tuple[float, float]:
        k = np.arange(pmf.size, dtype=float)
        m = float(pmf @ k)
        v = float(pmf @ (k * k)) - m * m
        return m, v

    def _possession_variance(
        self, n_bar: float, m1h: float, v1h: float, m1a: float, v1a: float, p: Dict[str, float]
    ) -> Tuple[float, bool]:
        """Solve Var[N] from the observed index if feasible; else use pace physics."""
        d_obs = p["observed_dispersion_index"]
        mean_total = n_bar * (m1h + m1a)
        floor_var = n_bar * (v1h + v1a)
        if d_obs is not None and d_obs > 0 and d_obs * mean_total > floor_var:
            v_n = (d_obs * mean_total - floor_var) / (m1h + m1a) ** 2
            return v_n, True
        return p["pace_variance_ratio"] * n_bar, False

    @staticmethod
    def _possession_pmf(mean: float, var: float) -> Tuple[np.ndarray, int]:
        sd = math.sqrt(max(var, 1e-9))
        kmax = int(math.ceil(mean + 7.0 * sd))
        edges = np.arange(0, kmax + 2, dtype=float) - 0.5
        cdf = stats.norm.cdf(edges, loc=mean, scale=sd)
        pmf = np.diff(cdf)
        pmf[0] += cdf[0]  # fold negative-count mass onto zero
        pmf = np.clip(pmf, 0.0, None)
        pmf /= pmf.sum()
        return pmf, kmax

    # ------------------------------------------------------------------ #
    # Joint score lattice
    # ------------------------------------------------------------------ #
    def _joint_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        p = self._merged_params(ctx)
        mu_h, mu_a, n_bar = self._resolve_means(ctx, p)

        ppp_h, ppp_a = mu_h / n_bar, mu_a / n_bar
        m1h, v1h = self._moments(self._x_pmf(ppp_h, p))
        m1a, v1a = self._moments(self._x_pmf(ppp_a, p))

        v_n, index_used = self._possession_variance(n_bar, m1h, v1h, m1a, v1a, p)
        vn_ratio = v_n / n_bar

        # Game state entering Q4
        var_q_margin = n_bar * (v1h + v1a) + v_n * (m1h - m1a) ** 2
        sd_q = math.sqrt(var_q_margin)
        mean_d3 = 3.0 * (mu_h - mu_a)
        sd_d3 = math.sqrt(3.0 * var_q_margin + p["margin_uncertainty_sd"] ** 2)

        nodes, weights = hermegauss(int(p["margin_nodes"]))
        weights = weights / weights.sum()

        states = []
        for x, w in zip(nodes, weights):
            d3 = mean_d3 + sd_d3 * x
            g = 2.0 * stats.norm.cdf(abs(d3) / sd_q) - 1.0
            poss_mult = 1.0 + p["foul_possession_boost"] * (1.0 - g) ** 2 - p["bench_pace_drag"] * g ** 2
            eff_mult = 1.0 - p["bench_efficiency_drag"] * g ** 2
            states.append((float(w), poss_mult, eff_mult, g))

        # Mean-preserving normalisation of the regime mixture
        k_norm = 1.0 / sum(w * pm * em for w, pm, em, _ in states)

        max_pm = max(s[1] for s in states)
        sd_h = math.sqrt(n_bar * max_pm * (v1h + vn_ratio * m1h ** 2))
        sd_a = math.sqrt(n_bar * max_pm * (v1a + vn_ratio * m1a ** 2))
        L_h = int(math.ceil(mu_h * max_pm + p["tail_sd"] * sd_h)) + 1
        L_a = int(math.ceil(mu_a * max_pm + p["tail_sd"] * sd_a)) + 1

        M = np.zeros((L_h, L_a))
        for w, pm, em, _ in states:
            n_i = n_bar * pm
            pN, kmax = self._possession_pmf(n_i, vn_ratio * n_i)
            xh = self._x_pmf(ppp_h * em * k_norm, p)
            xa = self._x_pmf(ppp_a * em * k_norm, p)
            sh = np.zeros(L_h); sh[0] = 1.0
            sa = np.zeros(L_a); sa[0] = 1.0
            thresh = p["prob_floor"]
            for k in range(kmax + 1):
                pk = pN[k]
                if pk > thresh:
                    M += (w * pk) * np.outer(sh, sa)
                sh = np.convolve(sh, xh)[:L_h]
                sa = np.convolve(sa, xa)[:L_a]

        M = np.clip(M, 0.0, None)
        M /= M.sum()

        totals = self._total_pmf_from_matrix(M)
        t_idx = np.arange(totals.size)
        t_mean = float(totals @ t_idx)
        t_var = float(totals @ (t_idx ** 2)) - t_mean ** 2
        self.last_diagnostics = {
            "mu_home": mu_h,
            "mu_away": mu_a,
            "possessions_per_team": n_bar,
            "possession_variance": v_n,
            "observed_index_feasible": index_used,
            "model_total_mean": t_mean,
            "model_total_var_mean_ratio": t_var / t_mean if t_mean > 0 else float("nan"),
            "q4_margin_sd": sd_q,
        }
        return M

    # ------------------------------------------------------------------ #
    # Fallback (guarantees a valid distribution under malformed inputs)
    # ------------------------------------------------------------------ #
    def _fallback_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        p = self.params
        mh = self._num(ctx.get("home_expected"), p["anchor_home"]) or p["anchor_home"]
        ma = self._num(ctx.get("away_expected"), p["anchor_away"]) or p["anchor_away"]
        if mh + ma > p["full_game_detect_ratio"] * (p["anchor_home"] + p["anchor_away"]):
            share = (p["anchor_home"] + p["anchor_away"]) / p["full_game_reference_total"]
            mh, ma = mh * share, ma * share
        mh, ma = abs(mh), abs(ma)
        c = np.array([p["share_1pt"], p["share_2pt"], p["share_3pt"]])
        c = c / c.sum()
        unit_ratio = float(c @ np.array([1.0, 4.0, 9.0]) / (c @ np.array([1.0, 2.0, 3.0])))
        out = []
        for m in (mh, ma):
            sd = math.sqrt(max(m, 1.0) * unit_ratio)
            L = int(math.ceil(m + p["tail_sd"] * sd)) + 1
            edges = np.arange(L + 1) - 0.5
            pmf = np.diff(stats.norm.cdf(edges, loc=m, scale=sd))
            pmf = np.clip(pmf, 0.0, None)
            out.append(pmf / pmf.sum())
        M = np.outer(out[0], out[1])
        self.last_diagnostics = {"fallback": True}
        return M / M.sum()

    # ------------------------------------------------------------------ #
    # Outputs
    # ------------------------------------------------------------------ #
    @staticmethod
    def _total_pmf_from_matrix(M: np.ndarray) -> np.ndarray:
        L_h, L_a = M.shape
        totals = np.zeros(L_h + L_a - 1)
        for i in range(L_h):
            totals[i:i + L_a] += M[i]
        return totals

    def _emit(self, M: np.ndarray) -> "DiscreteDistribution":
        floor = self.params["prob_floor"]
        idx_h, idx_a = np.nonzero(M > floor)
        vals = M[idx_h, idx_a]
        vals = vals / vals.sum()
        scores = {(int(h), int(a)): float(v) for h, a, v in zip(idx_h, idx_a, vals)}
        return DiscreteDistribution.from_scores(scores)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, dict) else {}
        try:
            M = self._joint_matrix(ctx)
            if not np.all(np.isfinite(M)) or M.sum() <= 0:
                raise ValueError("degenerate lattice")
        except Exception:
            M = self._fallback_matrix(ctx)
        return self._emit(M)

    # Convenience accessors for totals evaluation
    def total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context if isinstance(game_context, dict) else {}
        try:
            M = self._joint_matrix(ctx)
        except Exception:
            M = self._fallback_matrix(ctx)
        return self._total_pmf_from_matrix(M)

    def prob_over(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        pmf = self.total_pmf(game_context)
        t = np.arange(pmf.size, dtype=float)
        over = float(pmf[t > line].sum())
        under = float(pmf[t < line].sum())
        push = float(pmf[t == line].sum())
        return {"over": over, "under": under, "push": push}