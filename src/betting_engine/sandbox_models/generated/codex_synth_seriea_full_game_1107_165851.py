"""
SERIEA | full_game | winner_total candidate model.

The model builds the joint (home goals, away goals) score lattice from
first principles and prices every result x total cell from it.

1.  Intensity anchor. Each side's match-level goal intensity comes from the
    empirical league means (H = 1.60, A = 1.20). The home/away ratio is
    shrunk toward the post-2020 Serie A home-advantage level, with a weight
    set by how much evidence has been observed.
2.  Situational scaling. Intensities are log-linear in attack, opponent
    defence, pace, rest and weather. Every coefficient is shrunk toward the
    neutral value (multiplier 1) in proportion to the sample size behind it.
    Externally supplied `home_expected` / `away_expected` values are fused
    with the structural estimate by precision weighting in log space, so
    upstream xG information is neither ignored nor double counted.
3.  Game-state coupling. Scoring runs as a continuous-time Markov chain on
    the score lattice:
        r_h(t) = lambda_h * exp( beta * g(s(t)))
        r_a(t) = lambda_a * exp(-beta * g(s(t)))
    where s is the home goal differential and g = tanh gives a saturating
    response. With beta < 0, the leader's rate falls and the trailer's rate
    rises. This compresses margins while leaving totals unchanged.
4.  Low-score correction. A Dixon-Coles tau_rho factor is applied to the
    four low-score cells. rho is shrunk toward its prior and moves only with
    the observed draw-rate residual or the penalised likelihood fit.
5.  Mean preservation. The base rates are found by a fixed-point iteration,
    so the final lattice (after coupling and tau) reproduces the target
    expected goals exactly. The coupling and the tau factor reshape the
    joint distribution, not the marginal means.
6.  Pricing. Winner x total cells are summed directly from the lattice.
    Integer and quarter lines are settled with push and half-stake
    semantics. EV is computed from the model probability only, with no vig
    blending and no odds caps beyond the eligibility floor.

Numerical resolution (grid size, time steps) is derived from tolerances,
not fixed constants. All domain priors live in one documented,
overridable structure.
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - resolved in execution scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")


# --------------------------------------------------------------------------- #
# Priors: every domain assumption lives here and can be overridden.
# --------------------------------------------------------------------------- #
@dataclass
class SerieAPriors:
    # Empirical league moments supplied by the forensic autopsy.
    home_mean: float = 1.60
    away_mean: float = 1.20

    # Post-2020 Serie A home-advantage ratio and its pseudo-count (prior strength).
    home_adv_target_ratio: float = 1.20
    home_adv_prior_games: float = 380.0  # about one season of evidence

    # Game-state coupling: prior mean / sd of beta (leader suppression).
    beta_prior_mean: float = -0.10
    beta_prior_sd: float = 0.10

    # Dixon-Coles dependence: prior centred at 0, as the hypothesis specifies.
    rho_prior_mean: float = 0.0
    rho_prior_sd: float = 0.08
    draw_rate_prior_games: float = 380.0

    # Shrinkage pseudo-counts for team-level multipliers (games of evidence).
    team_rating_prior_games: float = 10.0
    default_team_games: float = 0.0  # cold start: ratings carry no weight unless backed by a sample

    # Precision fusion of structural vs supplied expected goals (log-scale sds).
    structural_log_sd: float = 0.20
    supplied_log_sd: float = 0.12

    # Situational elasticities (log-multiplier per unit of normalised input).
    rest_elasticity: float = 0.03
    rest_reference_days: float = 6.0
    rest_scale_days: float = 3.0
    pace_elasticity: float = 1.0          # pace given as a ratio to league pace
    wind_elasticity_per_10kph: float = -0.015

    # Numerical tolerances: grid and time resolution are derived from these.
    tail_tolerance: float = 1e-10
    max_step_event_prob: float = 0.01
    mean_match_tolerance: float = 1e-9
    max_calibration_iters: int = 50

    # Market eligibility.
    min_decimal_odds: float = 1.40


# --------------------------------------------------------------------------- #
# Candidate model
# --------------------------------------------------------------------------- #
class SerieAWinnerTotalStateCoupledModel(BaseCandidateModel):

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_full_game_1107_165851",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "full_game",
        family: str = "winner_total",
        priors: Optional[SerieAPriors] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family
        self.priors = priors or SerieAPriors()

        # Posterior state (cold start = priors).
        self.beta: float = self.priors.beta_prior_mean
        self.rho: float = self.priors.rho_prior_mean
        self.n_obs: int = 0
        self.obs_home_goals: float = 0.0
        self.obs_away_goals: float = 0.0
        self.obs_draws: float = 0.0
        self._lattice_cache: Dict[Tuple, np.ndarray] = {}
        self.last_score_matrix: Optional[np.ndarray] = None
        self.last_params: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # Context parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _get(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        """Accept both ctx['home']['attack'] and ctx['home_attack'] layouts."""
        nested = ctx.get(side)
        if isinstance(nested, dict) and key in nested:
            return self._num(nested.get(key))
        return self._num(ctx.get(f"{side}_{key}"))

    def _rating_log_mult(self, value: Optional[float], games: Optional[float]) -> float:
        """Shrunk log-multiplier from a rating expressed as a ratio to league average."""
        if value is None or value <= 0:
            return 0.0
        n = games if games is not None else self.priors.default_team_games
        w = n / (n + self.priors.team_rating_prior_games)
        return w * math.log(value)

    # ------------------------------------------------------------------ #
    # Home-advantage shrinkage
    # ------------------------------------------------------------------ #
    def _baseline_means(self) -> Tuple[float, float, float]:
        p = self.priors
        total0 = p.home_mean + p.away_mean
        r_emp = p.home_mean / p.away_mean
        if self.n_obs > 0 and self.obs_away_goals > 0:
            r_emp = self.obs_home_goals / self.obs_away_goals
            total0 = (self.obs_home_goals + self.obs_away_goals) / self.n_obs
        w = self.n_obs / (self.n_obs + p.home_adv_prior_games)
        log_r = w * math.log(r_emp) + (1.0 - w) * math.log(p.home_adv_target_ratio)
        r = math.exp(log_r)
        h = total0 * r / (1.0 + r)
        a = total0 - h
        correction = r / (p.home_mean / p.away_mean)  # applied to supplied ratios
        return h, a, correction

    # ------------------------------------------------------------------ #
    # Situational intensity construction
    # ------------------------------------------------------------------ #
    def _target_lambdas(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.priors
        base_h, base_a, ha_corr = self._baseline_means()

        # Structural estimate: baseline x attack x opponent defence.
        hg = self._get(ctx, "home", "games_played")
        ag = self._get(ctx, "away", "games_played")
        log_h = math.log(base_h)
        log_a = math.log(base_a)
        log_h += self._rating_log_mult(self._get(ctx, "home", "attack"), hg)
        log_h += self._rating_log_mult(self._get(ctx, "away", "defense"), ag)
        log_a += self._rating_log_mult(self._get(ctx, "away", "attack"), ag)
        log_a += self._rating_log_mult(self._get(ctx, "home", "defense"), hg)

        # Fuse with supplied expectations (precision-weighted, log space).
        sup_h = self._num(ctx.get("home_expected"))
        sup_a = self._num(ctx.get("away_expected"))
        if sup_h and sup_a and sup_h > 0 and sup_a > 0:
            tot = sup_h + sup_a
            ratio = (sup_h / sup_a) * ha_corr  # damp the home-field share
            sup_h, sup_a = tot * ratio / (1 + ratio), tot / (1 + ratio)
            w_s = p.structural_log_sd ** -2
            w_x = p.supplied_log_sd ** -2
            log_h = (w_s * log_h + w_x * math.log(sup_h)) / (w_s + w_x)
            log_a = (w_s * log_a + w_x * math.log(sup_a)) / (w_s + w_x)

        # Pace / tempo (ratio to league pace) scales both sides symmetrically.
        pace = self._num(ctx.get("pace")) or self._num(ctx.get("tempo"))
        if pace and pace > 0:
            log_h += p.pace_elasticity * math.log(pace)
            log_a += p.pace_elasticity * math.log(pace)

        # Rest: saturating response. Own rest helps attack; opponent rest hurts it.
        def rest_term(days: Optional[float]) -> float:
            if days is None:
                return 0.0
            return math.tanh((days - p.rest_reference_days) / p.rest_scale_days)

        rh = rest_term(self._num(ctx.get("home_rest_days")))
        ra = rest_term(self._num(ctx.get("away_rest_days")))
        log_h += p.rest_elasticity * (rh - ra)
        log_a += p.rest_elasticity * (ra - rh)

        # Weather: wind suppresses open-play quality (ignored under a roof).
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        is_dome = bool(weather.get("is_dome", False)) if isinstance(weather, dict) else False
        wind = self._num(weather.get("wind_speed")) if isinstance(weather, dict) else None
        if wind is not None and not is_dome:
            adj = p.wind_elasticity_per_10kph * (wind / 10.0)
            log_h += adj
            log_a += adj

        return math.exp(log_h), math.exp(log_a)

    # ------------------------------------------------------------------ #
    # Lattice engine
    # ------------------------------------------------------------------ #
    def _grid_size(self, lh: float, la: float, beta: float) -> int:
        inflate = math.exp(abs(beta))
        lam_max = max(lh, la) * inflate
        return int(stats.poisson.ppf(1.0 - self.priors.tail_tolerance, lam_max)) + 2

    def _n_steps(self, lh: float, la: float, beta: float) -> int:
        peak = (lh + la) * math.exp(abs(beta))
        return max(1, int(math.ceil(peak / self.priors.max_step_event_prob)))

    def _state_coupled_lattice(self, lh: float, la: float, beta: float) -> np.ndarray:
        """Forward Kolmogorov propagation of the state-dependent scoring chain."""
        N = self._grid_size(lh, la, beta)
        T = self._n_steps(lh, la, beta)
        idx = np.arange(N + 1)
        g = np.tanh(idx[:, None] - idx[None, :])
        rh = lh * np.exp(beta * g)
        ra = la * np.exp(-beta * g)
        tot = rh + ra
        p_event = -np.expm1(-tot / T)
        ph = p_event * rh / tot
        pa = p_event * ra / tot
        stay = 1.0 - p_event

        P = np.zeros((N + 1, N + 1))
        P[0, 0] = 1.0
        for _ in range(T):
            nxt = P * stay
            nxt[1:, :] += (P * ph)[:-1, :]
            nxt[:, 1:] += (P * pa)[:, :-1]
            P = nxt
        return P / P.sum()

    @staticmethod
    def _rho_bounds(lh: float, la: float) -> Tuple[float, float]:
        """Mathematical validity region of tau_rho (all cells non-negative)."""
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)
        return lo, hi

    def _apply_tau(self, P: np.ndarray, lh: float, la: float, rho: float) -> np.ndarray:
        lo, hi = self._rho_bounds(lh, la)
        rho = min(max(rho, lo), hi)
        Q = P.copy()
        Q[0, 0] *= 1.0 - lh * la * rho
        Q[0, 1] *= 1.0 + lh * rho
        Q[1, 0] *= 1.0 + la * rho
        Q[1, 1] *= 1.0 - rho
        return Q / Q.sum()

    def _calibrated_lattice(self, th: float, ta: float, beta: float, rho: float) -> np.ndarray:
        """Fixed point: find base rates whose final lattice reproduces (th, ta)."""
        key = (round(th, 6), round(ta, 6), round(beta, 6), round(rho, 6))
        if key in self._lattice_cache:
            return self._lattice_cache[key]
        lh, la = th, ta
        M = None
        for _ in range(self.priors.max_calibration_iters):
            M = self._apply_tau(self._state_coupled_lattice(lh, la, beta), th, ta, rho)
            idx = np.arange(M.shape[0])
            eh = float((M.sum(axis=1) * idx).sum())
            ea = float((M.sum(axis=0) * idx).sum())
            if abs(eh - th) < self.priors.mean_match_tolerance and abs(ea - ta) < self.priors.mean_match_tolerance:
                break
            lh *= th / eh
            la *= ta / ea
        self._lattice_cache[key] = M
        return M

    # ------------------------------------------------------------------ #
    # Draw-rate driven rho (cold start = prior)
    # ------------------------------------------------------------------ #
    def _effective_rho(self) -> float:
        p = self.priors
        if self.n_obs == 0:
            return self.rho
        obs_draw = self.obs_draws / self.n_obs
        h, a, _ = self._baseline_means()

        def draw_gap(r: float) -> float:
            return float(np.trace(self._calibrated_lattice(h, a, self.beta, r))) - obs_draw

        lo, hi = self._rho_bounds(h, a)
        lo, hi = lo * 0.999, hi * 0.999
        try:
            rho_star = optimize.brentq(draw_gap, lo, hi) if draw_gap(lo) * draw_gap(hi) < 0 else self.rho
        except ValueError:
            rho_star = self.rho
        w = self.n_obs / (self.n_obs + p.draw_rate_prior_games)
        return w * rho_star + (1.0 - w) * self.rho

    # ------------------------------------------------------------------ #
    # Penalised likelihood fit (optional; leaves priors intact at cold start)
    # ------------------------------------------------------------------ #
    def fit(self, history: Iterable[Dict[str, Any]]) -> "SerieAWinnerTotalStateCoupledModel":
        rows = []
        for g in history:
            hg_, ag_ = self._num(g.get("home_goals")), self._num(g.get("away_goals"))
            if hg_ is None or ag_ is None:
                continue
            rows.append((g, int(hg_), int(ag_)))
        if not rows:
            return self

        self.n_obs = len(rows)
        self.obs_home_goals = float(sum(r[1] for r in rows))
        self.obs_away_goals = float(sum(r[2] for r in rows))
        self.obs_draws = float(sum(1 for r in rows if r[1] == r[2]))
        self._lattice_cache.clear()

        targets = [(self._target_lambdas(ctx), h, a) for ctx, h, a in rows]
        p = self.priors

        def neg_post(theta: np.ndarray) -> float:
            beta, rho = float(theta[0]), float(theta[1])
            nll = 0.0
            for (th, ta), h, a in targets:
                lo, hi = self._rho_bounds(th, ta)
                if not lo < rho < hi:
                    return 1e12
                M = self._calibrated_lattice(th, ta, beta, rho)
                prob = M[h, a] if h < M.shape[0] and a < M.shape[1] else p.tail_tolerance
                nll -= math.log(max(prob, p.tail_tolerance))
            nll += 0.5 * ((beta - p.beta_prior_mean) / p.beta_prior_sd) ** 2
            nll += 0.5 * ((rho - p.rho_prior_mean) / p.rho_prior_sd) ** 2
            return nll

        res = optimize.minimize(
            neg_post, x0=np.array([self.beta, self.rho]), method="Nelder-Mead",
            options={"xatol": 1e-4, "fatol": 1e-6},
        )
        if res.success or np.isfinite(res.fun):
            self.beta, self.rho = float(res.x[0]), float(res.x[1])
        self._lattice_cache.clear()
        return self

    # ------------------------------------------------------------------ #
    # Core interface
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        th, ta = self._target_lambdas(game_context or {})
        rho = self._effective_rho()
        M = self._calibrated_lattice(th, ta, self.beta, rho)
        self.last_score_matrix = M
        self.last_params = {"lambda_home": th, "lambda_away": ta, "beta": self.beta, "rho": rho}
        return M

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        M = self.score_matrix(game_context)
        scores = {(int(i), int(j)): float(M[i, j])
                  for i in range(M.shape[0]) for j in range(M.shape[1])
                  if M[i, j] > self.priors.tail_tolerance}
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}

        p_home = float(np.tril(M, -1).sum())
        p_draw = float(np.trace(M))
        p_away = float(np.triu(M, 1).sum())

        dist = None
        for builder in (
            lambda: DiscreteDistribution.from_scores(scores),
            lambda: DiscreteDistribution.from_scores([(h, a, p_) for (h, a), p_ in scores.items()]),
            lambda: DiscreteDistribution.from_probabilities(p_home, p_draw, p_away),
            lambda: DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away}),
        ):
            try:
                dist = builder()
                break
            except Exception:
                continue
        if dist is None:
            raise RuntimeError("Unable to construct DiscreteDistribution from score lattice.")
        for attr, val in (("score_matrix", M), ("model_params", dict(self.last_params))):
            try:
                setattr(dist, attr, val)
            except Exception:
                pass
        return dist

    # ------------------------------------------------------------------ #
    # Winner x total pricing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _split_line(line: float) -> List[Tuple[float, float]]:
        """Quarter lines settle as two half-stakes on the adjacent lines."""
        frac = round((line * 4) % 4)
        if frac in (1, 3):
            return [(line - 0.25, 0.5), (line + 0.25, 0.5)]
        return [(line, 1.0)]

    def winner_total_outcome(self, M: np.ndarray, side: str, line: float, direction: str) -> Dict[str, float]:
        """Returns {'win', 'push', 'lose'} probabilities for a result-and-total contract."""
        idx = np.arange(M.shape[0])
        H, A = np.meshgrid(idx, idx, indexing="ij")
        side = side.lower()
        if side == "home":
            result = H > A
        elif side == "away":
            result = A > H
        else:
            result = H == A
        win = push = 0.0
        for sub_line, stake in self._split_line(line):
            tot = H + A
            if direction.lower() == "over":
                w_mask, p_mask = tot > sub_line, np.isclose(tot, sub_line)
            else:
                w_mask, p_mask = tot < sub_line, np.isclose(tot, sub_line)
            win += stake * float(M[result & w_mask].sum())
            push += stake * float(M[result & p_mask].sum())
        return {"win": win, "push": push, "lose": 1.0 - win - push}

    def expected_value(self, game_context: Dict[str, Any], side: str, line: float,
                       direction: str, decimal_odds: float) -> Dict[str, Any]:
        M = self.score_matrix(game_context)
        o = self.winner_total_outcome(M, side, line, direction)
        ev = o["win"] * (decimal_odds - 1.0) - o["lose"]
        eligible = decimal_odds >= self.priors.min_decimal_odds and ev > 0.0
        denom = 1.0 - o["push"]
        fair = (denom / o["win"]) if o["win"] > 0 else float("inf")
        return {"ev_per_unit": ev, "eligible": eligible, "fair_decimal_odds": fair, **o}

    def get_config(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id, "version": self.version, "league": self.league,
            "scope": self.scope, "family": self.family, "priors": asdict(self.priors),
            "beta": self.beta, "rho": self.rho, "n_obs": self.n_obs,
        }


CandidateModel = SerieAWinnerTotalStateCoupledModel