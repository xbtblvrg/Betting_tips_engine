"""
Candidate model for CHALLENGER | period_2 | total_sets.

The second-set game total is a bounded outcome on {6..13}. It is produced by a
point -> game -> set Markov chain, so it is not modeled as a Poisson/NB count.

Pipeline
--------
1. Anchor inversion. Find the latent serve-point win rates (s_home, s_away)
   such that the Beta-mixed exact set chain reproduces E[G_home] and
   E[G_away]. These come from game_context, or the segment anchors 5.76 / 5.31
   when absent. The fit is a moment-matching root-find in logit space. A weak
   ridge keeps it well posed when anchors are infeasible; no clamps are used.
2. Situational logit shifts on serve rates: rest/fatigue, serve-vs-return
   exposure, court pace, wind, and a hierarchical set-1-winner effect. The
   set-1 effect is shrunk to 0 under cold start.
3. Exact set recursion. Servers alternate, with an explicit point-level
   tiebreak sub-chain at 6-6. When the set-2 first server is unknown, the two
   service orders are averaged; when set-1 information allows, it is derived.
4. Beta posterior mixing on the serve rates, using quantile Gauss-Legendre
   quadrature. This is the only source of extra dispersion. The resulting
   Var/Mean is computed from the model, not assumed.

Every prior/hyperparameter is an explicit, documented constructor argument.
Each can be overridden or updated online (see `observe_set1_effect`).

Integration note: the `DiscreteDistribution.from_scores` input format is
assumed to be {(home_games, away_games): prob}. Verify this against the
engine's base module before deployment.
"""

import math
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pre-loaded execution scope fallback
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:
    DiscreteDistribution = globals().get("DiscreteDistribution")


# ----------------------------------------------------------------------------
# Exact tennis mechanics
# ----------------------------------------------------------------------------
def _logit(p: float) -> float:
    return math.log(p) - math.log1p(-p)


def _expit(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _hold_prob(p: float) -> float:
    """P(server holds) given serve-point win prob p (deuce in closed form)."""
    q = 1.0 - p
    pre_deuce = p ** 4 * (1.0 + 4.0 * q + 10.0 * q * q)
    reach_deuce = 20.0 * p ** 3 * q ** 3
    win_from_deuce = p * p / (1.0 - 2.0 * p * q)
    return pre_deuce + reach_deuce * win_from_deuce


def _tiebreak_win(pa: float, pb: float) -> float:
    """P(A wins a 7-point tiebreak) when A serves the first point.

    Serve order is A, B, B, A, A, B, B, ... The server of point n is A iff
    ((n + 1) // 2) is even. From 6-6, every pair of points contains one A
    serve and one B serve, which gives a closed form.
    """
    P = np.zeros((7, 7))
    P[0, 0] = 1.0
    win = 0.0
    for n in range(12):
        for a in range(max(0, n - 6), min(n, 6) + 1):
            b = n - a
            m = P[a, b]
            if m == 0.0:
                continue
            x = pa if ((n + 1) // 2) % 2 == 0 else 1.0 - pb
            if a == 6:
                win += m * x
            else:
                P[a + 1, b] += m * x
            if b < 6:
                P[a, b + 1] += m * (1.0 - x)
    d = pa * (1.0 - pb) + (1.0 - pa) * pb
    return win + P[6, 6] * (pa * (1.0 - pb) / d)


def _is_set_terminal(a: int, b: int) -> bool:
    return (a >= 6 or b >= 6) and abs(a - b) >= 2


def _set_matrix(hA: float, hB: float, tbA: float) -> np.ndarray:
    """Final set-score pmf F[a, b], with A serving the first game of the set."""
    G = np.zeros((8, 8))
    F = np.zeros((8, 8))
    G[0, 0] = 1.0
    for n in range(14):
        for a in range(max(0, n - 7), min(n, 7) + 1):
            b = n - a
            m = G[a, b]
            if m == 0.0:
                continue
            if _is_set_terminal(a, b):
                F[a, b] += m
                continue
            if a == 6 and b == 6:
                # Game index 12 is even, so A serves first in the tiebreak.
                F[7, 6] += m * tbA
                F[6, 7] += m * (1.0 - tbA)
                continue
            pA = hA if n % 2 == 0 else 1.0 - hB
            G[a + 1, b] += m * pA
            G[a, b + 1] += m * (1.0 - pA)
    return F


def _set_matrix_home_away(s_h: float, s_a: float, p_home_first: float) -> np.ndarray:
    """Set pmf indexed [home_games, away_games], mixed over the first server."""
    hh, ha = _hold_prob(s_h), _hold_prob(s_a)
    out = np.zeros((8, 8))
    if p_home_first > 0.0:
        out += p_home_first * _set_matrix(hh, ha, _tiebreak_win(s_h, s_a))
    if p_home_first < 1.0:
        out += (1.0 - p_home_first) * _set_matrix(ha, hh, _tiebreak_win(s_a, s_h)).T
    return out


@lru_cache(maxsize=4096)
def _mixed_set_matrix_cached(
    lh: float, la: float, kappa: float, n_nodes: int, p_home_first: float
) -> Tuple[Tuple[float, ...], ...]:
    """Beta-mixed set pmf.

    Serve rates s ~ Beta(m * kappa, (1 - m) * kappa), with m = expit(logit).
    Integration uses Gauss-Legendre quadrature on the quantile scale.
    """
    mh, ma = _expit(lh), _expit(la)
    x, w = np.polynomial.legendre.leggauss(n_nodes)
    u = 0.5 * (x + 1.0)
    w = 0.5 * w
    if math.isfinite(kappa) and kappa > 0.0:
        sh = stats.beta.ppf(u, mh * kappa, (1.0 - mh) * kappa)
        sa = stats.beta.ppf(u, ma * kappa, (1.0 - ma) * kappa)
        wh, wa = w, w
    else:  # degenerate: point estimate only
        sh, sa = np.array([mh]), np.array([ma])
        wh, wa = np.array([1.0]), np.array([1.0])
    tiny = np.finfo(float).tiny
    sh = np.clip(sh, tiny, 1.0 - np.finfo(float).eps)  # numerical domain guard only
    sa = np.clip(sa, tiny, 1.0 - np.finfo(float).eps)
    M = np.zeros((8, 8))
    for i in range(len(sh)):
        for j in range(len(sa)):
            M += wh[i] * wa[j] * _set_matrix_home_away(float(sh[i]), float(sa[j]), p_home_first)
    M /= M.sum()
    return tuple(tuple(float(v) for v in row) for row in M)


def _mixed_set_matrix(lh, la, kappa, n_nodes, p_home_first) -> np.ndarray:
    return np.array(
        _mixed_set_matrix_cached(
            round(float(lh), 10), round(float(la), 10), float(kappa),
            int(n_nodes), round(float(p_home_first), 10),
        )
    )


def _expected_games(M: np.ndarray) -> Tuple[float, float]:
    idx = np.arange(8)
    return float((M.sum(axis=1) * idx).sum()), float((M.sum(axis=0) * idx).sum())


# ----------------------------------------------------------------------------
# Candidate model
# ----------------------------------------------------------------------------
class ChallengerPeriod2TotalSetsModel(BaseCandidateModel):
    """Exact Markov set-chain model for Challenger second-set game totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_challenger_period_2_1108_014924",
        version: str = "1.0.0",
        league: str = "CHALLENGER",
        scope: str = "period_2",
        family: str = "total_sets",
        # --- empirical segment anchors (expected set-2 games won) ---
        anchor_home_games: float = 5.76,
        anchor_away_games: float = 5.31,
        # --- inversion regularisation ---
        # Prior centre for the serve-point rate (Challenger-level serve dominance).
        prior_serve_point_rate: float = 0.63,
        # Ridge weight; only binds when the anchors are infeasible or near the boundary.
        anchor_ridge_weight: float = 1e-4,
        # --- Beta posterior on serve-point rates ---
        # Effective serve points of information about a player's in-set serve
        # rate. Lower values give wider mixing and more dispersion. Learnable.
        serve_rate_concentration: float = 300.0,
        quadrature_nodes: int = 5,
        # --- situational logit-shift priors (weak; update when data arrives) ---
        # Signed by mechanics: more rest helps both serve and return.
        beta_rest: float = 0.02,
        # Serve rating vs opponent return rating; applied to log ratings when positive.
        beta_serve_return: float = 0.10,
        # Faster pace (z-score) raises both serve rates.
        beta_pace: float = 0.03,
        # Wind degrades serve efficiency outdoors (per unit wind speed).
        beta_wind: float = -0.003,
        # No signed prior for temperature, so cold start leaves it inactive.
        beta_temp: float = 0.0,
        temp_reference: Optional[float] = None,
        # --- hierarchical set-1-winner effect on the winner's serve logit ---
        set1_effect_sum: float = 0.0,  # sufficient statistic: summed residuals
        set1_effect_n: float = 0.0,  # effective observations
        set1_effect_prior_strength: float = 50.0,  # shrinkage toward 0
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            try:
                super().__init__()
            except TypeError:
                pass
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.anchor_home_games = float(anchor_home_games)
        self.anchor_away_games = float(anchor_away_games)
        self.prior_logit = _logit(float(prior_serve_point_rate))
        self.anchor_ridge_weight = float(anchor_ridge_weight)
        self.kappa = float(serve_rate_concentration)
        self.n_nodes = int(quadrature_nodes)

        self.beta_rest = float(beta_rest)
        self.beta_serve_return = float(beta_serve_return)
        self.beta_pace = float(beta_pace)
        self.beta_wind = float(beta_wind)
        self.beta_temp = float(beta_temp)
        self.temp_reference = temp_reference

        self.set1_effect_sum = float(set1_effect_sum)
        self.set1_effect_n = float(set1_effect_n)
        self.set1_effect_prior_strength = float(set1_effect_prior_strength)

        self._inversion_cache: Dict[Tuple[float, float], Tuple[float, float, float]] = {}
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _get(ctx: Mapping[str, Any], *keys: str) -> Any:
        """Return the first present value for flat or dotted/nested keys."""
        for key in keys:
            if key in ctx and ctx[key] is not None:
                return ctx[key]
            parts = key.split(".")
            node: Any = ctx
            ok = True
            for p in parts:
                if isinstance(node, Mapping) and p in node and node[p] is not None:
                    node = node[p]
                else:
                    ok = False
                    break
            if ok and len(parts) > 1:
                return node
        return None

    @classmethod
    def _num(cls, ctx: Mapping[str, Any], *keys: str) -> Optional[float]:
        v = cls._get(ctx, *keys)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    @staticmethod
    def _rating_transform(x: float) -> float:
        # Multiplicative ratings (centred near 1) use log; additive ratings pass through.
        return math.log(x) if x > 0.0 else x

    @staticmethod
    def _side(v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s in ("home", "h", "1", "player1", "p1"):
            return "home"
        if s in ("away", "a", "2", "player2", "p2"):
            return "away"
        return None

    # ------------------------------------------------------- set-1 hierarchy
    @property
    def set1_effect(self) -> float:
        """Posterior-mean logit shift for the set-1 winner, shrunk toward 0."""
        return self.set1_effect_sum / (self.set1_effect_n + self.set1_effect_prior_strength)

    def observe_set1_effect(self, winner_serve_logit_residual: float, weight: float = 1.0) -> None:
        """Online update with an observed residual.

        The residual is the set-1 winner's realised set-2 serve logit minus the
        model's pre-shift logit.
        """
        if math.isfinite(winner_serve_logit_residual) and weight > 0:
            self.set1_effect_sum += weight * winner_serve_logit_residual
            self.set1_effect_n += weight

    # ----------------------------------------------------------- inversion
    def _invert_anchors(self, t_h: float, t_a: float) -> Tuple[float, float, float]:
        key = (round(t_h, 6), round(t_a, 6))
        if key in self._inversion_cache:
            return self._inversion_cache[key]
        ridge = math.sqrt(self.anchor_ridge_weight)
        l0 = self.prior_logit

        def resid(x: np.ndarray) -> np.ndarray:
            M = _mixed_set_matrix(x[0], x[1], self.kappa, self.n_nodes, 0.5)
            eh, ea = _expected_games(M)
            return np.array([eh - t_h, ea - t_a, ridge * (x[0] - l0), ridge * (x[1] - l0)])

        sol = optimize.least_squares(resid, x0=np.array([l0, l0]), method="trf", x_scale="jac")
        r = resid(sol.x)
        out = (float(sol.x[0]), float(sol.x[1]), float(math.hypot(r[0], r[1])))
        self._inversion_cache[key] = out
        return out

    # ------------------------------------------------------- situational
    def _situational_shifts(self, ctx: Mapping[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        dh = da = 0.0
        parts: Dict[str, float] = {}

        # Rest / fatigue: better rest improves own serve and own return.
        r_h = self._num(ctx, "home_rest_days")
        r_a = self._num(ctx, "away_rest_days")
        if r_h is not None and r_a is not None and r_h >= 0 and r_a >= 0:
            z = math.log1p(r_h) - math.log1p(r_a)
            dh += self.beta_rest * z
            da -= self.beta_rest * z
            parts["rest"] = self.beta_rest * z

        # Serve (attack) vs opponent return (defense) exposure.
        att_h = self._num(ctx, "home_attack", "home.attack")
        def_h = self._num(ctx, "home_defense", "home.defense")
        att_a = self._num(ctx, "away_attack", "away.attack")
        def_a = self._num(ctx, "away_defense", "away.defense")
        if att_h is not None and def_a is not None:
            v = self.beta_serve_return * (self._rating_transform(att_h) - self._rating_transform(def_a))
            dh += v
            parts["home_serve_vs_return"] = v
        if att_a is not None and def_h is not None:
            v = self.beta_serve_return * (self._rating_transform(att_a) - self._rating_transform(def_h))
            da += v
            parts["away_serve_vs_return"] = v

        # Court pace / tempo. Standardise if a reference is supplied, else treat as a z-score.
        pace = self._num(ctx, "pace_z", "court_pace", "pace", "tempo", "venue.pace")
        if pace is not None:
            mu = self._num(ctx, "pace_mean")
            sd = self._num(ctx, "pace_std")
            z = (pace - mu) / sd if (mu is not None and sd is not None and sd > 0) else pace
            dh += self.beta_pace * z
            da += self.beta_pace * z
            parts["pace"] = self.beta_pace * z

        # Weather (indoor events neutralise it).
        is_dome = self._get(ctx, "is_dome", "weather.is_dome", "indoor")
        outdoor = not bool(is_dome) if is_dome is not None else True
        if outdoor:
            wind = self._num(ctx, "wind_speed", "weather.wind_speed")
            if wind is not None and wind >= 0:
                dh += self.beta_wind * wind
                da += self.beta_wind * wind
                parts["wind"] = self.beta_wind * wind
            temp = self._num(ctx, "temp", "weather.temp")
            ref = self.temp_reference if self.temp_reference is not None else self._num(ctx, "temp_reference")
            if temp is not None and ref is not None and self.beta_temp != 0.0:
                dh += self.beta_temp * (temp - ref)
                da += self.beta_temp * (temp - ref)
                parts["temp"] = self.beta_temp * (temp - ref)

        # Set-1 state dependence.
        winner = self._side(self._get(ctx, "set1_winner", "period_1_winner"))
        s1h = self._num(ctx, "set1_home_games", "period_1_home")
        s1a = self._num(ctx, "set1_away_games", "period_1_away")
        if winner is None and s1h is not None and s1a is not None and s1h != s1a:
            winner = "home" if s1h > s1a else "away"
        if winner is not None:
            d = self.set1_effect
            if winner == "home":
                dh += d
            else:
                da += d
            parts["set1_winner_effect"] = d
        return dh, da, parts

    def _p_home_serves_first(self, ctx: Mapping[str, Any]) -> float:
        explicit = self._side(self._get(ctx, "set2_first_server", "period_2_first_server"))
        if explicit is not None:
            return 1.0 if explicit == "home" else 0.0
        s1_first = self._side(self._get(ctx, "set1_first_server", "first_server"))
        s1h = self._num(ctx, "set1_home_games", "period_1_home")
        s1a = self._num(ctx, "set1_away_games", "period_1_away")
        if s1_first is not None and s1h is not None and s1a is not None:
            n_games = int(round(s1h + s1a))  # a tiebreak counts as one game in the rotation
            same = n_games % 2 == 0
            home_first = (s1_first == "home") == same
            return 1.0 if home_first else 0.0
        return 0.5

    # ------------------------------------------------------------- predict
    def predict_distribution(self, game_context: Dict[str, Any]):
        ctx = game_context or {}
        t_h = self._num(ctx, "home_expected")
        t_a = self._num(ctx, "away_expected")
        if t_h is None or t_h <= 0:
            t_h = self.anchor_home_games
        if t_a is None or t_a <= 0:
            t_a = self.anchor_away_games

        lh0, la0, anchor_err = self._invert_anchors(t_h, t_a)
        dh, da, parts = self._situational_shifts(ctx)
        lh, la = lh0 + dh, la0 + da
        p_home_first = self._p_home_serves_first(ctx)

        M = _mixed_set_matrix(lh, la, self.kappa, self.n_nodes, p_home_first)

        scores: Dict[Tuple[int, int], float] = {}
        for i in range(8):
            for j in range(8):
                if M[i, j] > 0.0:
                    scores[(i, j)] = float(M[i, j])

        totals = np.zeros(14)
        for (i, j), p in scores.items():
            totals[i + j] += p
        k = np.arange(14)
        mean_t = float((k * totals).sum())
        var_t = float(((k - mean_t) ** 2 * totals).sum())
        eh, ea = _expected_games(M)
        self.last_diagnostics = {
            "anchor_targets": (t_h, t_a),
            "anchor_fit_error": anchor_err,
            "serve_point_rates_base": (_expit(lh0), _expit(la0)),
            "serve_point_rates": (_expit(lh), _expit(la)),
            "hold_rates": (_hold_prob(_expit(lh)), _hold_prob(_expit(la))),
            "logit_shift_components": parts,
            "p_home_serves_first": p_home_first,
            "expected_games": (eh, ea),
            "total_mean": mean_t,
            "total_var": var_t,
            "dispersion_index": var_t / mean_t if mean_t > 0 else float("nan"),
            "total_pmf": {int(t): float(totals[t]) for t in range(6, 14)},
        }

        if DiscreteDistribution is None:
            raise RuntimeError("DiscreteDistribution is not available in the execution scope.")
        return DiscreteDistribution.from_scores(scores)


# Engine entry-point alias
CandidateModel = ChallengerPeriod2TotalSetsModel