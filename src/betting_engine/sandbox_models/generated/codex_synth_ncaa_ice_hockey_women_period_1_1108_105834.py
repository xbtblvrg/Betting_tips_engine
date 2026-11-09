"""
Candidate model: NCAA_ICE_HOCKEY_WOMEN | period_1 | exact_score

Structure
---------
1. Per-team period-1 scoring rates are built on a log scale. The anchor is the
   empirical league baseline (home 0.53, away 0.40). Attack and defense
   exposure, a game pace multiplier and differential rest are additive
   log-effects on top of it. External ``home_expected`` / ``away_expected``
   values override the rating composition when present. They are
   scope-detected: full-game expectations are converted to period-1 rates.
2. Two gamma frailties sit on top of the Poisson rates:
     - a team-specific frailty (variance theta_team plus rating-uncertainty
       variance), which integrates analytically to a negative binomial;
     - a *shared* game frailty (variance theta_shared), which models common
       pace and special-teams volume. It induces positive covariance between
       the two teams' scores. It is integrated numerically with
       Gauss-Legendre quadrature on the gamma quantile scale, which stays
       stable for any shape.
3. A Dixon-Coles tau correction is applied to the {0-0, 1-0, 0-1, 1-1} cells.
   rho is projected only onto its mathematically feasible region, where every
   tau is >= 0. The grid is then renormalised.

Cold-start honesty
------------------
The only empirical moments available are the two baseline means and a
dispersion index of 1.000. By method of moments, a dispersion index of 1
implies theta = (DI - 1) / mean = 0. With no settled first-period scores,
there is no likelihood information for rho either. The model therefore
*starts* at its data-implied values: Poisson, independent, rho = 0. The
frailty, rho and rest parameters activate only through ``fit(records)``,
which estimates them from settled first-period scores:
  - rest coefficient: Poisson maximum likelihood with offsets;
  - theta_team and theta_shared: unbiased moment estimators (marginal excess
    variance and cross-moment);
  - rho: profile maximum likelihood.
Every estimate is positive-part shrunk by its own sampling error, so
parameters shrink toward the Poisson/independent model when evidence is weak.

Note on the stated hypothesis: independent Poisson at 0.53 / 0.40 already
places about 84.5% of the mass in the four low cells, not 75-80%. Any
deviation from that value should come from fitted theta_shared and rho, not
be assumed.

Weather inputs are ignored because play is on indoor rinks.
"""

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # pragma: no cover - location depends on engine layout
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except ImportError:
        try:
            from betting_engine.distributions import DiscreteDistribution
        except ImportError:
            DiscreteDistribution = globals().get("DiscreteDistribution")


class NcaaIceHockeyWomenPeriod1ExactScoreModel(BaseCandidateModel):
    # Empirical inputs supplied for this segment (not tuning knobs).
    LEAGUE_HOME_MEAN = 0.53
    LEAGUE_AWAY_MEAN = 0.40
    LEAGUE_DISPERSION_INDEX = 1.0
    # Rule of the sport: three regulation periods.
    REGULATION_PERIODS = 3

    def __init__(
        self,
        model_id: str = "codex_synth_ncaa_ice_hockey_women_period_1_1108_105834",
        version: str = "1.0.0",
        league: str = "NCAA_ICE_HOCKEY_WOMEN",
        scope: str = "period_1",
        family: str = "exact_score",
        rho: Optional[float] = None,
        theta_team: Optional[float] = None,
        theta_shared: Optional[float] = None,
        rest_coef: Optional[float] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Numerical tolerances are derived from machine precision.
        self._eps = float(np.finfo(float).eps)
        self._tail_tol = math.sqrt(self._eps)
        # Integration resolution: double-precision-adequate Gauss-Legendre order.
        self._quad_order = int(math.ceil(-math.log2(self._eps))) - 12

        # Method-of-moments dispersion implied by the observed index.
        base_mean = 0.5 * (self.LEAGUE_HOME_MEAN + self.LEAGUE_AWAY_MEAN)
        theta_from_di = max(0.0, (self.LEAGUE_DISPERSION_INDEX - 1.0) / base_mean)

        # Log-scale variance of a team's rate when its rating is unknown.
        # Lognormal moment match: var(log) = log(1 + theta).
        self.league_log_rating_var = math.log1p(theta_from_di)

        self.theta_team = float(theta_team) if theta_team is not None else theta_from_di
        self.theta_shared = float(theta_shared) if theta_shared is not None else 0.0
        self.rho = float(rho) if rho is not None else 0.0
        self.rest_coef = float(rest_coef) if rest_coef is not None else 0.0

        self.n_fit = 0
        self.fit_diagnostics: Dict[str, Any] = {}
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Context helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _lookup(ctx: Mapping[str, Any], *paths: str) -> Optional[float]:
        for path in paths:
            node: Any = ctx
            found = True
            for key in path.split("."):
                if isinstance(node, Mapping) and key in node:
                    node = node[key]
                else:
                    found = False
                    break
            if not found or node is None or isinstance(node, Mapping):
                continue
            try:
                val = float(node)
            except (TypeError, ValueError):
                continue
            if math.isfinite(val):
                return val
        return None

    def _rating(
        self, ctx: Mapping[str, Any], side: str, kind: str
    ) -> Tuple[Optional[float], float]:
        """Return (log-effect, log-variance) of a rating.

        Positive ratings are treated as multiplicative strengths around 1
        (log taken). Non-positive ratings are treated as additive log-effects.
        For defense, values above 1 mean a team concedes more (exposure).
        """
        val = self._lookup(
            ctx,
            f"{side}.{kind}",
            f"{side}_{kind}",
            f"{side}.{kind}_rating",
            f"{side}_{kind}_rating",
            f"{side}_team.{kind}",
        )
        se = self._lookup(
            ctx,
            f"{side}.{kind}_se",
            f"{side}_{kind}_se",
            f"{side}.{kind}_rating_se",
            f"{side}_{kind}_rating_se",
        )
        if val is None:
            return None, self.league_log_rating_var
        if val > 0:
            log_eff = math.log(val)
            log_var = (se / val) ** 2 if se is not None else 0.0  # delta method
        else:
            log_eff = val
            log_var = se ** 2 if se is not None else 0.0
        return log_eff, log_var

    def _external_expectations(
        self, ctx: Mapping[str, Any]
    ) -> Tuple[Optional[float], Optional[float]]:
        out: List[Optional[float]] = []
        scope_flag = ctx.get("expected_scope") if isinstance(ctx, Mapping) else None
        for side, base in (("home", self.LEAGUE_HOME_MEAN), ("away", self.LEAGUE_AWAY_MEAN)):
            explicit = self._lookup(
                ctx,
                f"period_1_{side}_expected",
                f"{side}_expected_period_1",
                f"{side}_expected_p1",
            )
            if explicit is not None and explicit > 0:
                out.append(explicit)
                continue
            val = self._lookup(ctx, f"{side}_expected", f"{side}.expected")
            if val is None or val <= 0:
                out.append(None)
                continue
            if scope_flag in ("full_game", "game", "regulation"):
                out.append(val / self.REGULATION_PERIODS)
            elif scope_flag in ("period_1", "period", "p1"):
                out.append(val)
            else:
                # Log-nearest scope: period-1 baseline vs full-game baseline.
                d_period = abs(math.log(val / base))
                d_game = abs(math.log(val / (base * self.REGULATION_PERIODS)))
                out.append(val / self.REGULATION_PERIODS if d_game < d_period else val)
        return out[0], out[1]

    def _pace_log(self, ctx: Mapping[str, Any]) -> float:
        league_pace = self._lookup(ctx, "league_pace", "league_tempo")
        game = self._lookup(ctx, "pace", "tempo", "game_pace", "expected_pace")
        hp = self._lookup(ctx, "home_pace", "home.pace", "home_tempo", "home.tempo")
        ap = self._lookup(ctx, "away_pace", "away.pace", "away_tempo", "away.tempo")
        if game is None and hp is not None and ap is not None and hp > 0 and ap > 0:
            game = math.sqrt(hp * ap)
        if game is None or game <= 0:
            return 0.0
        if league_pace is not None and league_pace > 0:
            game /= league_pace
        # Unit elasticity: goals scale with event volume by construction.
        return math.log(game)

    def _rest_diff(self, ctx: Mapping[str, Any]) -> float:
        rh = self._lookup(ctx, "home_rest_days", "home.rest_days")
        ra = self._lookup(ctx, "away_rest_days", "away.rest_days")
        if rh is None or ra is None:
            return 0.0
        return math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0))

    def _rates(
        self, ctx: Mapping[str, Any], rest_coef: Optional[float] = None
    ) -> Tuple[float, float, float, float]:
        beta = self.rest_coef if rest_coef is None else rest_coef
        ext_h, ext_a = self._external_expectations(ctx)
        logs: Dict[str, float] = {}
        lvars: Dict[str, float] = {}
        for side, opp, base, ext in (
            ("home", "away", self.LEAGUE_HOME_MEAN, ext_h),
            ("away", "home", self.LEAGUE_AWAY_MEAN, ext_a),
        ):
            if ext is not None:
                # External expectations already encode the matchup strength.
                logs[side], lvars[side] = math.log(ext), 0.0
                continue
            att, v_att = self._rating(ctx, side, "attack")
            dfn, v_dfn = self._rating(ctx, opp, "defense")
            logs[side] = math.log(base) + (att or 0.0) + (dfn or 0.0)
            lvars[side] = v_att + v_dfn

        pace = self._pace_log(ctx)
        d_rest = self._rest_diff(ctx)
        log_h = logs["home"] + pace + beta * d_rest
        log_a = logs["away"] + pace - beta * d_rest
        return math.exp(log_h), math.exp(log_a), lvars["home"], lvars["away"]

    # ------------------------------------------------------------------ #
    # Probability machinery
    # ------------------------------------------------------------------ #
    def _nb_pmf(self, k: np.ndarray, mean: float, theta: float) -> np.ndarray:
        if theta <= self._eps:
            return stats.poisson.pmf(k, mean)
        r = 1.0 / theta
        return stats.nbinom.pmf(k, r, r / (r + mean))

    def _upper_quantile(self, mean: float, theta_eff: float) -> int:
        excess = theta_eff * mean * mean
        if excess <= self._eps:
            return int(stats.poisson.ppf(1.0 - self._tail_tol, mean))
        r = mean * mean / excess
        return int(stats.nbinom.ppf(1.0 - self._tail_tol, r, r / (r + mean)))

    def _base_grid(
        self,
        lh: float,
        la: float,
        th_h: float,
        th_a: float,
        th_s: float,
        min_size: int = 0,
    ) -> np.ndarray:
        eff_h = th_h + th_s + th_h * th_s
        eff_a = th_a + th_s + th_a * th_s
        n_max = max(
            self._upper_quantile(lh, eff_h),
            self._upper_quantile(la, eff_a),
            1,
            min_size,
        )
        k = np.arange(n_max + 1)

        if th_s <= self._eps:
            nodes, weights = np.array([1.0]), np.array([1.0])
        else:
            u, w = np.polynomial.legendre.leggauss(self._quad_order)
            u = 0.5 * (u + 1.0)
            shape = 1.0 / th_s
            nodes = stats.gamma.ppf(u, a=shape, scale=th_s)
            weights = w / w.sum()

        grid = np.zeros((n_max + 1, n_max + 1))
        for g, w in zip(nodes, weights):
            grid += w * np.outer(self._nb_pmf(k, lh * g, th_h), self._nb_pmf(k, la * g, th_a))
        total = grid.sum()
        return grid / total if total > 0 else grid

    @staticmethod
    def _rho_bounds(lh: float, la: float) -> Tuple[float, float]:
        return max(-1.0 / lh, -1.0 / la), min(1.0 / (lh * la), 1.0)

    @staticmethod
    def _tau(lh: float, la: float, rho: float) -> Dict[Tuple[int, int], float]:
        return {
            (0, 0): 1.0 - lh * la * rho,
            (0, 1): 1.0 + lh * rho,
            (1, 0): 1.0 + la * rho,
            (1, 1): 1.0 - rho,
        }

    def _apply_dc(self, grid: np.ndarray, lh: float, la: float, rho: float) -> np.ndarray:
        lo, hi = self._rho_bounds(lh, la)
        rho = min(max(rho, lo), hi)  # feasibility projection, not a tuning clamp
        out = grid.copy()
        for (i, j), t in self._tau(lh, la, rho).items():
            out[i, j] *= t
        out = np.clip(out, 0.0, None)
        return out / out.sum()

    def _thetas(self, vh: float, va: float) -> Tuple[float, float, float]:
        # Lognormal-to-gamma moment match of rating uncertainty: exp(v) - 1.
        th_h = max(0.0, self.theta_team) + math.expm1(vh)
        th_a = max(0.0, self.theta_team) + math.expm1(va)
        return th_h, th_a, max(0.0, self.theta_shared)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        lh, la, vh, va = self._rates(ctx)
        th_h, th_a, th_s = self._thetas(vh, va)
        grid = self._apply_dc(self._base_grid(lh, la, th_h, th_a, th_s), lh, la, self.rho)
        self.last_diagnostics = {
            "lambda_home": lh,
            "lambda_away": la,
            "theta_home": th_h,
            "theta_away": th_a,
            "theta_shared": th_s,
            "rho": self.rho,
            "low_cell_mass": float(grid[:2, :2].sum()),
            "p_0_0": float(grid[0, 0]),
            "grid_size": grid.shape[0],
        }
        return grid

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        grid = self.score_matrix(game_context)
        n = grid.shape[0]
        scores = {
            (i, j): float(grid[i, j])
            for i in range(n)
            for j in range(n)
            if grid[i, j] > 0.0
        }
        attempts = (
            lambda: DiscreteDistribution.from_scores(scores),
            lambda: DiscreteDistribution.from_scores(grid),
            lambda: DiscreteDistribution.from_scores({f"{i}-{j}": p for (i, j), p in scores.items()}),
        )
        last_err: Optional[Exception] = None
        for attempt in attempts:
            try:
                return attempt()
            except (TypeError, ValueError, KeyError, AttributeError) as err:
                last_err = err
        raise RuntimeError(f"Unable to construct DiscreteDistribution: {last_err}")

    # ------------------------------------------------------------------ #
    # Estimation from settled first-period scores
    # ------------------------------------------------------------------ #
    @staticmethod
    def _positive_part_shrink(est: float, se: float) -> float:
        if est == 0.0 or not math.isfinite(se):
            return 0.0
        return est * max(0.0, 1.0 - (se * se) / (est * est))

    def _parse(self, records: Iterable[Mapping[str, Any]]) -> List[Tuple[int, int, Mapping[str, Any]]]:
        rows = []
        for rec in records:
            ctx = rec.get("context", rec) if isinstance(rec, Mapping) else {}
            yh = self._lookup(rec, "home_goals", "home_score", "period_1_home_goals")
            ya = self._lookup(rec, "away_goals", "away_score", "period_1_away_goals")
            if yh is None or ya is None or yh < 0 or ya < 0:
                continue
            rows.append((int(round(yh)), int(round(ya)), ctx))
        return rows

    def fit(self, records: Iterable[Mapping[str, Any]]) -> "NcaaIceHockeyWomenPeriod1ExactScoreModel":
        rows = self._parse(records)
        n = len(rows)
        if n == 0:
            return self
        yh = np.array([r[0] for r in rows], dtype=float)
        ya = np.array([r[1] for r in rows], dtype=float)

        # 1) Rest coefficient: Poisson MLE with structural offsets.
        offs = np.array([self._rates(c, rest_coef=0.0)[:2] for _, _, c in rows])
        d = np.array([self._rest_diff(c) for _, _, c in rows])
        if np.any(d != 0.0):
            loh, loa = np.log(offs[:, 0]), np.log(offs[:, 1])

            def nll_beta(b: float) -> float:
                eh, ea = loh + b * d, loa - b * d
                return -float(np.sum(yh * eh - np.exp(eh) + ya * ea - np.exp(ea)))

            b_hat = float(optimize.minimize_scalar(nll_beta, method="brent").x)
            info = float(np.sum(d * d * (np.exp(loh + b_hat * d) + np.exp(loa - b_hat * d))))
            se_b = 1.0 / math.sqrt(info) if info > 0 else float("inf")
            self.rest_coef = self._positive_part_shrink(b_hat, se_b)

        # 2) Dispersion: unbiased moment estimators.
        rates = [self._rates(c) for _, _, c in rows]
        lh = np.array([r[0] for r in rates])
        la = np.array([r[1] for r in rates])
        vh = np.expm1(np.array([r[2] for r in rates]))
        va = np.expm1(np.array([r[3] for r in rates]))

        z = np.concatenate([(yh - lh) ** 2 - yh - vh * lh ** 2, (ya - la) ** 2 - ya - va * la ** 2])
        lam2 = np.concatenate([lh ** 2, la ** 2])
        th_marg = float(z.sum() / lam2.sum())
        se_marg = math.sqrt(float(np.sum((z - th_marg * lam2) ** 2))) / float(lam2.sum())

        c = (yh - lh) * (ya - la)
        lhla = lh * la
        th_s_hat = float(c.sum() / lhla.sum())
        se_s = math.sqrt(float(np.sum((c - th_s_hat * lhla) ** 2))) / float(lhla.sum())

        self.theta_shared = max(0.0, self._positive_part_shrink(th_s_hat, se_s))
        self.theta_team = max(
            0.0,
            self._positive_part_shrink(th_marg - self.theta_shared, math.hypot(se_marg, se_s)),
        )

        # 3) rho: profile likelihood over the Dixon-Coles low-score cells.
        cells = ((0, 0), (0, 1), (1, 0), (1, 1))
        base_cells, obs_p, obs_cell, lam_pairs = [], [], [], []
        lo, hi = -float("inf"), float("inf")
        for (h, a, _), (l_h, l_a, v_h, v_a) in zip(rows, rates):
            th_h, th_a, th_s = self._thetas(v_h, v_a)
            g = self._base_grid(l_h, l_a, th_h, th_a, th_s, min_size=max(h, a))
            base_cells.append([g[i, j] for i, j in cells])
            obs_p.append(max(g[h, a], self._eps))
            obs_cell.append((h, a))
            lam_pairs.append((l_h, l_a))
            b_lo, b_hi = self._rho_bounds(l_h, l_a)
            lo, hi = max(lo, b_lo), min(hi, b_hi)

        base_cells_arr = np.array(base_cells)
        obs_p_arr = np.log(np.array(obs_p))

        def nll_rho(rho: float) -> float:
            total = 0.0
            for k, (l_h, l_a) in enumerate(lam_pairs):
                tau = self._tau(l_h, l_a, rho)
                z_norm = 1.0 + sum(base_cells_arr[k, m] * (tau[cl] - 1.0) for m, cl in enumerate(cells))
                t_obs = tau.get(obs_cell[k], 1.0)
                if t_obs <= 0.0 or z_norm <= 0.0:
                    return float("inf")
                total += obs_p_arr[k] + math.log(t_obs) - math.log(z_norm)
            return -total

        span = hi - lo
        res = optimize.minimize_scalar(
            nll_rho,
            bounds=(lo + self._tail_tol * span, hi - self._tail_tol * span),
            method="bounded",
        )
        rho_hat = float(res.x)
        h_step = self._tail_tol ** 0.5 * max(span, 1.0)
        f0 = nll_rho(rho_hat)
        fp, fm = nll_rho(rho_hat + h_step), nll_rho(rho_hat - h_step)
        curv = (fp - 2.0 * f0 + fm) / (h_step * h_step)
        se_rho = 1.0 / math.sqrt(curv) if curv > 0 and math.isfinite(curv) else float("inf")
        self.rho = self._positive_part_shrink(rho_hat, se_rho)

        self.n_fit = n
        self.fit_diagnostics = {
            "n": n,
            "rest_coef": self.rest_coef,
            "theta_team": self.theta_team,
            "theta_shared": self.theta_shared,
            "theta_marginal_raw": th_marg,
            "theta_shared_raw": th_s_hat,
            "rho_raw": rho_hat,
            "rho_se": se_rho,
            "rho": self.rho,
            "rho_feasible": (lo, hi),
        }
        return self

    # ------------------------------------------------------------------ #
    # Valuation helper: continuous, no odds caps beyond the stated eligibility
    # ------------------------------------------------------------------ #
    def expected_value(self, game_context: Dict[str, Any], home_goals: int, away_goals: int, decimal_odds: float) -> float:
        grid = self.score_matrix(game_context)
        n = grid.shape[0]
        p = float(grid[home_goals, away_goals]) if home_goals < n and away_goals < n else 0.0
        return p * decimal_odds - 1.0