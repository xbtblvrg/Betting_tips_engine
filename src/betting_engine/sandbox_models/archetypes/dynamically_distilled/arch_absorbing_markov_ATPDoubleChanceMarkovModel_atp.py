"""
ATP | full_game | double_chance — hierarchical point→game→tiebreak→set→match model.

Design summary
--------------
* Tennis has no draw state, so the 3-way output is P(home), P(draw)=0, P(away).
  "Home or Draw" therefore equals P(home wins) and "Away or Draw" equals P(away wins).
* No home-court term is added. The "home"/"away" labels are listing order only.
* The expected games (home_expected, away_expected) are not treated as independent
  Poisson counts. They are inverted exactly through the tennis Markov chain into
  serve-point win probabilities (p_A, p_B). This is a MAP fit with a weak surface prior
  on serve strength. The posterior over (logit p_A, logit p_B) uses a Laplace
  approximation and is integrated with 2-D Gauss–Hermite quadrature. This widens the
  forecast where the inputs carry little information, which addresses the tail
  overconfidence noted in the prompt.
* When the match format (best-of-3 or best-of-5) is not supplied, it is inferred
  through Bayesian model averaging using Laplace evidence.
* Rest days, prior-match duration, wind, indoor play and temperature enter as
  logit-scale shifts on serve/return strength. No probability clamps are applied.
* Retirements are handled according to the book's settlement rule:
  - 'void': stakes are refunded, so the price is P(A | match completed).
  - 'advance': the advancing player is paid, so retirement mass is credited.

Honesty note
------------
This is a cold-start market with zero settled bets. The domain priors below (surface
serve rates, fatigue/weather sensitivities, retirement base rate, best-of-5 share)
come from general ATP knowledge, not from fitting this segment. Every prior is
overridable through constructor kwargs and should be re-estimated once settled data
exist. No out-of-sample ROI or Brier target can be promised before validation.
"""

import math
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize
from scipy.special import expit, logit

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live in base or be pre-loaded into execution scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------------------
# Exact tennis Markov-chain primitives
# --------------------------------------------------------------------------------------

def _hold(p: float) -> float:
    """P(server holds a standard ad-scoring game) given serve-point win prob p."""
    q = 1.0 - p
    deuce_reach = 20.0 * p ** 3 * q ** 3
    deuce_win = p * p / (p * p + q * q)
    return p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q) + deuce_reach * deuce_win


@lru_cache(maxsize=200_000)
def _tiebreak(p_first: float, p_second: float, target: int) -> float:
    """
    P(first server wins a first-to-`target`, win-by-2 tiebreak).

    p_first is the first server's serve-point win prob; p_second is the second
    server's. Serve order is A, BB, AA, BB, ...
    """
    dp: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    win = 0.0
    for k in range(2 * (target - 1)):
        first_serves = ((k + 1) // 2) % 2 == 0
        w = p_first if first_serves else 1.0 - p_second
        nxt: Dict[Tuple[int, int], float] = {}
        for (i, j), p in dp.items():
            if i + 1 == target:
                win += p * w
            else:
                nxt[(i + 1, j)] = nxt.get((i + 1, j), 0.0) + p * w
            if j + 1 != target:
                nxt[(i, j + 1)] = nxt.get((i, j + 1), 0.0) + p * (1.0 - w)
        dp = nxt
    tie = dp.get((target - 1, target - 1), 0.0)
    # From a tie, each pair of points has exactly one serve per player.
    x = p_first * (1.0 - p_second)
    y = (1.0 - p_first) * p_second
    return win + tie * (x / (x + y))


def _set_terminal(a: int, b: int) -> bool:
    return (a >= 6 and a - b >= 2) or (b >= 6 and b - a >= 2)


@lru_cache(maxsize=200_000)
def _set_outcomes(h_first: float, h_second: float, tb_first: float) -> Tuple[Tuple[float, int, int], ...]:
    """Distribution of final set scores (prob, games_first, games_second); 6-6 goes to tiebreak."""
    out: Dict[Tuple[int, int], float] = {}
    dp: Dict[Tuple[int, int], float] = {(0, 0): 1.0}
    for n in range(12):
        w = h_first if n % 2 == 0 else 1.0 - h_second
        nxt: Dict[Tuple[int, int], float] = {}
        for (i, j), p in dp.items():
            for ni, nj, pr in ((i + 1, j, w), (i, j + 1, 1.0 - w)):
                m = p * pr
                if _set_terminal(ni, nj):
                    out[(ni, nj)] = out.get((ni, nj), 0.0) + m
                else:
                    nxt[(ni, nj)] = nxt.get((ni, nj), 0.0) + m
        dp = nxt
    p66 = dp.get((6, 6), 0.0)
    out[(7, 6)] = out.get((7, 6), 0.0) + p66 * tb_first
    out[(6, 7)] = out.get((6, 7), 0.0) + p66 * (1.0 - tb_first)
    return tuple((p, a, b) for (a, b), p in out.items())


@lru_cache(maxsize=200_000)
def _match_stats(pa: float, pb: float, best_of: int, final_tb: int) -> Tuple[float, float, float]:
    """
    Exact match roll-up.

    Returns (P(A wins), E[games A], E[games B]). The first server is a fair coin toss.
    Set-to-set serve order follows game-count parity; a tiebreak counts as one game.
    """
    ha, hb = _hold(pa), _hold(pb)

    def outcomes(target: int):
        a_first = _set_outcomes(ha, hb, _tiebreak(pa, pb, target))
        b_first_raw = _set_outcomes(hb, ha, _tiebreak(pb, pa, target))
        b_first = tuple((p, gb, ga) for p, ga, gb in b_first_raw)  # remap to (gA, gB)
        return a_first, b_first

    reg_a, reg_b = outcomes(7)
    fin_a, fin_b = outcomes(final_tb) if final_tb != 7 else (reg_a, reg_b)

    need = (best_of + 1) // 2
    states: Dict[Tuple[int, int, int], float] = {(0, 0, 0): 0.5, (0, 0, 1): 0.5}
    p_win = e_ga = e_gb = 0.0
    for _ in range(best_of):
        nxt: Dict[Tuple[int, int, int], float] = {}
        for (sa, sb, f), p in states.items():
            final = sa == need - 1 and sb == need - 1
            outs = (fin_a if f == 0 else fin_b) if final else (reg_a if f == 0 else reg_b)
            for q, ga, gb in outs:
                m = p * q
                e_ga += m * ga
                e_gb += m * gb
                nsa, nsb = (sa + 1, sb) if ga > gb else (sa, sb + 1)
                nf = f if (ga + gb) % 2 == 0 else 1 - f
                if nsa == need:
                    p_win += m
                elif nsb != need:
                    key = (nsa, nsb, nf)
                    nxt[key] = nxt.get(key, 0.0) + m
        states = nxt
    return p_win, e_ga, e_gb


def _stats_logit(xa: float, xb: float, best_of: int, final_tb: int) -> Tuple[float, float, float]:
    # Rounding only improves cache reuse; precision is far below model noise.
    return _match_stats(round(float(expit(xa)), 10), round(float(expit(xb)), 10), best_of, final_tb)


# --------------------------------------------------------------------------------------
# Candidate model
# --------------------------------------------------------------------------------------

class ATPDoubleChanceMarkovModel(BaseCandidateModel):
    """Hierarchical Markov-chain model for ATP double-chance (draw-free) pricing."""

    DEFAULT_CONFIG: Dict[str, Any] = {
        # Empirical segment anchors (from the supplied baseline means).
        "baseline_home_games": 12.80,
        "baseline_away_games": 11.80,
        # Tour-level serve-point win-rate priors by surface (domain prior, not a clamp).
        "surface_serve_prior": {
            "hard": 0.645, "clay": 0.625, "grass": 0.665, "indoor": 0.655, "carpet": 0.66,
        },
        "default_surface": "hard",
        # Spread of player serve strength around the tour mean, on the logit scale.
        "prior_serve_logit_sd": 0.30,
        # Forecast error of the supplied expected-games inputs (games).
        "expected_games_sd": 1.0,
        # Prior share of best-of-5 matches when the format is not supplied.
        "prior_bo5_share": 0.12,
        "final_set_tb_bo3": 7,
        "final_set_tb_bo5": 10,
        # Fatigue: f = (1 + prior_minutes / ref_minutes) * exp(-rest / tau).
        "fatigue_tau_days": 2.0,
        "fatigue_ref_minutes": 120.0,
        "fatigue_logit_coef": 0.06,
        # Conditions: serve-logit multiplier exp(-k_w * wind + k_t * (temp - t_ref) + k_i * indoor).
        "wind_logit_decay": 0.010,
        "temp_logit_coef": 0.004,
        "temp_reference": 70.0,
        "indoor_logit_coef": 0.04,
        # Retirement / walkover-after-start base rate per match.
        "retirement_base_rate": 0.025,
        "retirement_rule": "void",  # 'void' or 'advance'
        "gauss_hermite_nodes": 7,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_atp_full_game_1108_054737",
        version: str = "1.0.0",
        league: str = "ATP",
        scope: str = "full_game",
        family: str = "double_chance",
        **config_overrides: Any,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league, scope=scope, family=family)
        except TypeError:  # pragma: no cover - tolerate differing base signatures
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family
        self.config: Dict[str, Any] = dict(self.DEFAULT_CONFIG)
        self.config.update(config_overrides)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ feature access
    @staticmethod
    def _num(ctx: Dict[str, Any], keys: List[str], default: Optional[float] = None) -> Optional[float]:
        for k in keys:
            v = ctx.get(k)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                return fv
        return default

    def _side_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        nested = ctx.get(side)
        if isinstance(nested, dict):
            v = self._num(nested, [kind, f"{kind}_rating", f"{kind}_strength"])
            if v is not None:
                return v
        return self._num(ctx, [f"{side}_{kind}", f"{side}_{kind}_rating"])

    def _expected_games(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h = self._num(ctx, ["home_expected", "home_expected_games"])
        a = self._num(ctx, ["away_expected", "away_expected_games"])
        if h is not None and a is not None and h > 0 and a > 0:
            return h, a
        # Fall back to multiplicative attack/defense exposure around the empirical anchors.
        # This path is used only when expectations are absent, to avoid double counting.
        bh, ba = self.config["baseline_home_games"], self.config["baseline_away_games"]
        ah = self._side_rating(ctx, "home", "attack") or 1.0
        dh = self._side_rating(ctx, "home", "defense") or 1.0
        aa = self._side_rating(ctx, "away", "attack") or 1.0
        da = self._side_rating(ctx, "away", "defense") or 1.0
        tiny = np.finfo(float).tiny
        return (h if h and h > 0 else bh * max(ah, tiny) / max(da, tiny),
                a if a and a > 0 else ba * max(aa, tiny) / max(dh, tiny))

    def _serve_prior_logits(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        surface = str(ctx.get("surface") or ctx.get("venue_surface") or self.config["default_surface"]).lower()
        if ctx.get("is_dome") or ctx.get("indoor"):
            surface = "indoor" if surface == "hard" else surface
        table = self.config["surface_serve_prior"]
        base = table.get(surface, table[self.config["default_surface"]])
        ph = self._num(ctx, ["home_serve_point_pct", "home_serve_points_won"], base)
        pa = self._num(ctx, ["away_serve_point_pct", "away_serve_points_won"], base)
        # Accept either percentage or fraction inputs.
        ph = ph / 100.0 if ph > 1.0 else ph
        pa = pa / 100.0 if pa > 1.0 else pa
        return float(logit(ph)), float(logit(pa))

    def _formats(self, ctx: Dict[str, Any]) -> List[Tuple[int, int, float]]:
        """Return a list of (best_of, final_tb_target, prior_weight)."""
        cfg = self.config
        bo = self._num(ctx, ["best_of", "sets_best_of"])
        override_tb = self._num(ctx, ["final_set_tiebreak_target"])
        if bo is None:
            level = str(ctx.get("tournament_level") or ctx.get("tourney_level") or "").lower()
            if "slam" in level or level == "g":
                bo = 5.0
        if bo is not None:
            b = 5 if int(round(bo)) >= 5 else 3
            tb = int(override_tb) if override_tb else (cfg["final_set_tb_bo5"] if b == 5 else cfg["final_set_tb_bo3"])
            return [(b, tb, 1.0)]
        s5 = cfg["prior_bo5_share"]
        return [(3, cfg["final_set_tb_bo3"], 1.0 - s5), (5, cfg["final_set_tb_bo5"], s5)]

    def _fatigue(self, ctx: Dict[str, Any], side: str) -> float:
        rest = self._num(ctx, [f"{side}_rest_days"])
        if rest is None:
            return 0.0
        mins = self._num(ctx, [f"{side}_prev_match_minutes", f"{side}_prior_match_minutes"], 0.0)
        cfg = self.config
        return (1.0 + mins / cfg["fatigue_ref_minutes"]) * math.exp(-max(rest, 0.0) / cfg["fatigue_tau_days"])

    def _condition_multiplier(self, ctx: Dict[str, Any]) -> float:
        cfg = self.config
        indoor = bool(ctx.get("is_dome") or ctx.get("indoor"))
        z = 0.0
        if indoor:
            z += cfg["indoor_logit_coef"]
        else:
            wind = self._num(ctx, ["wind_speed"])
            if wind is not None:
                z -= cfg["wind_logit_decay"] * max(wind, 0.0)
            temp = self._num(ctx, ["temp", "temperature"])
            if temp is not None:
                z += cfg["temp_logit_coef"] * (temp - cfg["temp_reference"])
        return math.exp(z)

    # ------------------------------------------------------------------ inference
    def _fit_format(self, h: float, a: float, best_of: int, final_tb: int,
                    mu: Tuple[float, float], tau: float, sg: float):
        def resid(x: np.ndarray) -> np.ndarray:
            _, ea, eb = _stats_logit(x[0], x[1], best_of, final_tb)
            return np.array([(ea - h) / sg, (eb - a) / sg, (x[0] - mu[0]) / tau, (x[1] - mu[1]) / tau])

        share = h / (h + a)
        d0 = 2.0 * float(logit(share))
        x0 = np.array([mu[0] + 0.5 * d0, mu[1] - 0.5 * d0])
        res = optimize.least_squares(resid, x0, method="trf", x_scale="jac")
        jac = np.asarray(res.jac, dtype=float)
        prec = jac.T @ jac
        cov = np.linalg.pinv(prec)
        sign, logdet = np.linalg.slogdet(prec)
        log_evidence = -float(res.cost) - 0.5 * (logdet if sign > 0 else 0.0)
        return res.x.copy(), cov, log_evidence

    def _integrate_win_prob(self, mean: np.ndarray, cov: np.ndarray, best_of: int, final_tb: int) -> float:
        n = int(self.config["gauss_hermite_nodes"])
        t, w = np.polynomial.hermite.hermgauss(n)
        vals, vecs = np.linalg.eigh(0.5 * (cov + cov.T))
        root = vecs @ np.diag(np.sqrt(np.maximum(vals, 0.0)))
        total = 0.0
        for i in range(n):
            for j in range(n):
                z = mean + root @ (math.sqrt(2.0) * np.array([t[i], t[j]]))
                p, _, _ = _stats_logit(z[0], z[1], best_of, final_tb)
                total += w[i] * w[j] * p
        return total / math.pi

    # ------------------------------------------------------------------ public API
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        cfg = self.config

        h, a = self._expected_games(ctx)
        mu = self._serve_prior_logits(ctx)
        tau = cfg["prior_serve_logit_sd"]
        sg = self._num(ctx, ["expected_games_sd"], cfg["expected_games_sd"])

        # Situational shifts (logit scale): fatigue hurts one's own serve and return.
        f_h, f_a = self._fatigue(ctx, "home"), self._fatigue(ctx, "away")
        c = cfg["fatigue_logit_coef"]
        shift = np.array([-c * f_h + c * f_a, -c * f_a + c * f_h])
        cond = self._condition_multiplier(ctx)

        fits = []
        for best_of, final_tb, prior_w in self._formats(ctx):
            x_hat, cov, log_ev = self._fit_format(h, a, best_of, final_tb, mu, tau, sg)
            mean = (x_hat + shift) * cond
            cov_adj = cov * cond * cond
            p_fmt = self._integrate_win_prob(mean, cov_adj, best_of, final_tb)
            fits.append((best_of, prior_w, log_ev, p_fmt, x_hat))

        log_w = np.array([math.log(fw[1]) + fw[2] for fw in fits])
        post = np.exp(log_w - log_w.max())
        post /= post.sum()
        p_home_completed = float(sum(pw * fw[3] for pw, fw in zip(post, fits)))

        # Retirement settlement.
        base_r = cfg["retirement_base_rate"]
        r_h = base_r * (1.0 + f_h) / (2.0 + f_h + f_a)
        r_a = base_r * (1.0 + f_a) / (2.0 + f_h + f_a)
        rule = str(ctx.get("retirement_rule") or cfg["retirement_rule"]).lower()
        if rule.startswith("adv"):
            p_home = p_home_completed * (1.0 - r_h - r_a) + r_a
        else:  # void: refunded stakes => price conditional on completion
            p_home = p_home_completed
        p_away = 1.0 - p_home

        self.last_diagnostics = {
            "home_expected": h, "away_expected": a,
            "format_posterior": {int(fw[0]): float(pw) for pw, fw in zip(post, fits)},
            "serve_point_probs": {int(fw[0]): tuple(float(v) for v in expit(fw[4])) for fw in fits},
            "fatigue": (f_h, f_a), "condition_multiplier": cond,
            "retirement": (r_h, r_a, rule), "p_home": p_home,
        }

        return DiscreteDistribution(probabilities={"home": p_home, "draw": 0.0, "away": p_away})