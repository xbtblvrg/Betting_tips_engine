import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.special import ndtr

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pre-loaded into execution scope by the harness
    DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


_SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


class NBAHalf2SpreadsModel(BaseCandidateModel):
    """
    NBA | half_2 | spreads: second-half score lattice built from possession mechanics.

    Structure
    ---------
    Mean margin:
        mu_M = eta_h2 + kappa * (diff - g0) + delta_rest - rho * (M1 - E[M1])
      * eta_h2: home-court share that survives into the second half. Most HCA is
        realized before halftime, so this is far below the full baseline gap g0.
        This term targets the home-side over-pricing in the ledger.
      * kappa: shrinkage of the team-quality signal in half 2 (bench minutes,
        lead protection).
      * rho: in-play reversion on first-half surprise. Applied only if a
        first-half score is supplied.
      * delta_rest: saturating fatigue differential, 1 - exp(-rest / tau).

    Variance:
        Var_indep = N * (v_h + v_a), where v(ppp) comes from a 0/1/2/3 possession
        outcome law. The dependence factor phi < 1 scales it to account for
        negative serial dependence (lineup and lead-state effects).
        A state term grows with |M1| (foul-up / garbage-time regimes).

    Shape:
        The margin is a variance-preserving mixture of
        * a narrow core (close-game modal concentration), and
        * a skew-normal wide component whose skew fattens the trailing side's tail.
        The mixture mean is held exactly at mu_M.

    Totals:
        Gaussian on T = H + A. Its variance comes from possession noise plus
        pace uncertainty.
        Joint lattice: P(h, a) ∝ f_T(h + a) * f_M(h - a).

    Caveats (documented, not hidden)
    --------------------------------
    * The reported dispersion index 0.080 is implausible for raw half scores,
      which run near Poisson or higher. It is NOT used. Pass
      'audited_margin_sd_h2' in game_context to override the derived sigma once
      it has been re-derived on game data.
    * 162 settled bets cannot identify eta_h2 / kappa / rho. The defaults below
      are league-structure priors. Fit them hierarchically on out-of-sample game
      logs and pass them via game_context['h2_priors'].
    """

    # League-structure priors (overridable per call via game_context['h2_priors']).
    PRIORS: Dict[str, float] = {
        "baseline_home_h2": 56.0,     # empirical segment anchor
        "baseline_away_h2": 54.0,     # empirical segment anchor
        "eta_h2": 0.75,               # residual 2nd-half home-court points
        "kappa": 0.85,                # 2nd-half quality-signal shrinkage
        "rho": 0.08,                  # reversion on first-half surprise
        "beta_rest": 0.6,             # 2nd-half fatigue effect (points)
        "tau_rest": 1.0,              # rest saturation scale (days)
        "league_ppp": 1.14,           # points per possession
        "frac1": 0.08,                # scoring-possession mix: 1 pt
        "frac2": 0.67,                # 2 pts
        "frac3": 0.25,                # 3 pts
        "phi": 0.62,                  # possession serial-dependence factor
        "pace_sd_poss": 3.0,          # SD of possessions per team in a half
        "core_weight": 0.25,          # close-game modal mass
        "core_scale": 0.55,           # core sd relative to total sd
        "skew_alpha": 1.2,            # trailing-tail skew strength
        "tail_gamma": 0.12,           # state variance inflation from |M1|
        "tail_scale": 12.0,           # |M1| saturation scale
        "grid_sigmas": 7.0,           # lattice coverage
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nba_half_2_1109_164337",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "half_2",
        family: str = "spreads",
    ):
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
                if math.isfinite(f) and f > 0.0:
                    return f
            except (TypeError, ValueError):
                return None
        return None

    def _priors(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        p = dict(self.PRIORS)
        over = ctx.get("h2_priors")
        if isinstance(over, dict):
            for k, v in over.items():
                try:
                    f = float(v)
                    if math.isfinite(f):
                        p[k] = f
                except (TypeError, ValueError):
                    pass
        return p

    @staticmethod
    def _poss_var(ppp: float, p: Dict[str, float]) -> float:
        """Per-possession point variance from a 0/1/2/3 outcome law with mean ppp."""
        f1, f2, f3 = p["frac1"], p["frac2"], p["frac3"]
        s = f1 + f2 + f3
        f1, f2, f3 = f1 / s, f2 / s, f3 / s
        m = f1 + 2.0 * f2 + 3.0 * f3
        m2 = f1 + 4.0 * f2 + 9.0 * f3
        p_score = ppp / m
        if p_score >= 1.0:  # extreme efficiency: degenerate scoring; use Poisson-like
            return max(ppp, 1e-6)
        return max(p_score * m2 - ppp * ppp, 1e-6)

    # ------------------------------------------------------------------ core
    def _moments(self, ctx: Dict[str, Any], p: Dict[str, float]) -> Tuple[float, float, float, float, float]:
        base_h, base_a = p["baseline_home_h2"], p["baseline_away_h2"]

        he = self._num(ctx, "home_expected", "home_expected_h2")
        ae = self._num(ctx, "away_expected", "away_expected_h2")
        if he is None or ae is None:
            home, away = ctx.get("home"), ctx.get("away")
            att_h, def_h = self._rating(home, "attack"), self._rating(home, "defense")
            att_a, def_a = self._rating(away, "attack"), self._rating(away, "defense")
            if he is None:
                he = base_h * (att_h or 1.0) * (def_a or 1.0)
            if ae is None:
                ae = base_a * (att_a or 1.0) * (def_h or 1.0)
        he, ae = max(he, 1.0), max(ae, 1.0)

        # Possessions per team in the half
        pace = self._num(ctx, "pace", "expected_pace")
        if pace is None:
            hp, ap = self._num(ctx, "home_pace"), self._num(ctx, "away_pace")
            if hp is not None and ap is not None:
                pace = 0.5 * (hp + ap)
        if pace is not None and pace > 0:
            n_poss = pace / 2.0
        else:
            n_poss = (he + ae) / (2.0 * p["league_ppp"])
        n_poss = max(n_poss, 1.0)
        ppp_h, ppp_a = he / n_poss, ae / n_poss

        # ---- mean margin
        diff = he - ae
        g0 = base_h - base_a
        mu_m = p["eta_h2"] + p["kappa"] * (diff - g0)

        rh = self._num(ctx, "home_rest_days")
        ra = self._num(ctx, "away_rest_days")
        if rh is not None and ra is not None:
            tau = max(p["tau_rest"], 1e-6)
            mu_m += p["beta_rest"] * (
                (1.0 - math.exp(-max(rh, 0.0) / tau)) - (1.0 - math.exp(-max(ra, 0.0) / tau))
            )

        m1 = self._num(ctx, "h1_margin", "first_half_margin")
        if m1 is None:
            h1h = self._num(ctx, "home_score_h1", "home_h1")
            h1a = self._num(ctx, "away_score_h1", "away_h1")
            if h1h is not None and h1a is not None:
                m1 = h1h - h1a
        if m1 is not None:
            e1 = self._num(ctx, "h1_expected_margin")
            if e1 is None:
                e1 = diff
            mu_m -= p["rho"] * (m1 - e1)

        # ---- margin variance from possession structure
        v_h, v_a = self._poss_var(ppp_h, p), self._poss_var(ppp_a, p)
        var_indep = n_poss * (v_h + v_a)
        audited_sd = self._num(ctx, "audited_margin_sd_h2")
        var_m = audited_sd ** 2 if (audited_sd and audited_sd > 0) else p["phi"] * var_indep
        if m1 is not None:
            var_m *= 1.0 + p["tail_gamma"] * math.tanh(abs(m1) / max(p["tail_scale"], 1e-6))

        # ---- total variance: possession noise + pace uncertainty
        var_t = p["phi"] * var_indep + (p["pace_sd_poss"] ** 2) * (ppp_h + ppp_a) ** 2
        mu_t = he + ae

        return mu_m, math.sqrt(var_m), mu_t, math.sqrt(var_t), diff

    def _margin_density(self, x: np.ndarray, mu: float, sd: float, p: Dict[str, float]) -> np.ndarray:
        w = min(max(p["core_weight"], 0.0), 0.95)
        r = p["core_scale"]
        if w * r * r >= 1.0:
            w = 0.0
        s_core = r * sd
        s_wide = sd * math.sqrt((1.0 - w * r * r) / (1.0 - w)) if w < 1.0 else sd

        z = (x - mu) / s_core
        core = np.exp(-0.5 * z * z) * (_INV_SQRT_2PI / s_core)

        # Skew toward the trailing (underdog) side; strength rises with |mu|/sd.
        alpha = -math.copysign(1.0, mu) * p["skew_alpha"] * math.tanh(abs(mu) / sd) if mu != 0 else 0.0
        delta = alpha / math.sqrt(1.0 + alpha * alpha)
        omega = s_wide / math.sqrt(1.0 - 2.0 * delta * delta / math.pi)
        xi = mu - omega * delta * _SQRT_2_OVER_PI
        u = (x - xi) / omega
        wide = 2.0 / omega * _INV_SQRT_2PI * np.exp(-0.5 * u * u) * ndtr(alpha * u)

        return w * core + (1.0 - w) * wide

    def _score_matrix(self, ctx: Dict[str, Any]):
        p = self._priors(ctx)
        mu_m, sd_m, mu_t, sd_t, _ = self._moments(ctx, p)

        mu_h, mu_a = 0.5 * (mu_t + mu_m), 0.5 * (mu_t - mu_m)
        sd_side = 0.5 * math.sqrt(sd_t ** 2 + sd_m ** 2)
        k = p["grid_sigmas"]
        h_vals = np.arange(max(0, int(math.floor(mu_h - k * sd_side))), int(math.ceil(mu_h + k * sd_side)) + 1)
        a_vals = np.arange(max(0, int(math.floor(mu_a - k * sd_side))), int(math.ceil(mu_a + k * sd_side)) + 1)

        H, A = np.meshgrid(h_vals, a_vals, indexing="ij")
        T = (H + A).astype(float)
        M = (H - A).astype(float)
        zt = (T - mu_t) / sd_t
        P = np.exp(-0.5 * zt * zt) * self._margin_density(M, mu_m, sd_m, p)
        P = np.where(np.isfinite(P) & (P > 0.0), P, 0.0)
        s = P.sum()
        if not (s > 0.0 and math.isfinite(s)):
            raise ValueError("degenerate score lattice")
        return h_vals, a_vals, P / s

    @staticmethod
    def _three_way(h_vals, a_vals, P) -> Tuple[float, float, float]:
        M = h_vals[:, None] - a_vals[None, :]
        ph = float(P[M > 0].sum())
        pd = float(P[M == 0].sum())
        pa = float(P[M < 0].sum())
        s = ph + pd + pa
        return ph / s, pd / s, pa / s

    def _emit(self, h_vals, a_vals, P):
        scores: Dict[Tuple[int, int], float] = {}
        idx = np.argwhere(P > 1e-12)
        for i, j in idx:
            scores[(int(h_vals[i]), int(a_vals[j]))] = float(P[i, j])
        tot = sum(scores.values())
        scores = {k: v / tot for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            ph, pd, pa = self._three_way(h_vals, a_vals, P)
            try:
                return DiscreteDistribution.from_probabilities(ph, pd, pa)
            except Exception:
                return DiscreteDistribution(probabilities={"home": ph, "draw": pd, "away": pa})

    # ------------------------------------------------------------------ API
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, dict) else {}
        try:
            h_vals, a_vals, P = self._score_matrix(ctx)
        except Exception:
            # Fall back to the segment anchor only (no situational inputs).
            h_vals, a_vals, P = self._score_matrix({})
        return self._emit(h_vals, a_vals, P)

    def cover_probability(self, game_context: Dict[str, Any], home_line: float) -> Dict[str, float]:
        """P(home covers), P(push), P(away covers) for a home handicap (e.g. -1.5)."""
        h_vals, a_vals, P = self._score_matrix(game_context if isinstance(game_context, dict) else {})
        adj = (h_vals[:, None] - a_vals[None, :]).astype(float) + float(home_line)
        return {
            "home": float(P[adj > 0].sum()),
            "push": float(P[adj == 0].sum()),
            "away": float(P[adj < 0].sum()),
        }