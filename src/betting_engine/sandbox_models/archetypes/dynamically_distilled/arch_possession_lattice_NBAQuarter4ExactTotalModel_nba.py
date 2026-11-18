"""
NBA | quarter_4 | exact_total candidate model.

Architecture: compound possession process with regime mixing
------------------------------------------------------------
For each team, Q4 points are T = sum_{i=1}^{N} X_i + F, where

  N  shared possession count for the quarter. It is clock-limited and
     underdispersed inside a regime, so it is binomial when phi_N < 1,
     Poisson at 1 and negative binomial above 1.
  X  points on one possession, a categorical law on {0,1,2,3,4}. The
     league shape is reshaped by the matchup three-point exposure. It is
     then exponentially tilted, p_k ∝ b_k e^{θk}, so that E[X] equals
     the anchored points per possession exactly. The tilt is the
     minimum-KL change of the league law.
  F  late-game free-throw trips in close games. They form a compound
     Poisson of Binomial(2, ft%) trips, evaluated exactly with the
     Panjer recursion.

The game state entering Q4 is M3 ~ Normal(m3, s3). It gives mixture
weights over three regimes: close, competitive and blowout. Each regime
changes the possession mean, the possession dispersion, the efficiency
and the foul-trip intensity.

The per-team possession efficiency is solved in closed form. This keeps
the regime-weighted mean of each team at its anchor (home_expected,
away_expected). The regimes reshape the probability mass but never move
the first moment.

The exact-total pmf is the convolution of the per-possession laws,
G_T(z) = G_N(G_X(z)) * G_F(z), so integer and parity structure is kept.
Away possessions are N + δ with δ in {-1, 0, +1} because possessions
alternate. Sharing N gives the positive home/away correlation that real
pace produces.

Implied dispersion is Var(T)/E[T] ≈ 1.3–1.7. It comes from the moments
and is not hardcoded. fit() calibrates phi_N to an observed dispersion
index by moment matching with Bayesian shrinkage toward the domain prior.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # resolve DiscreteDistribution from the engine (or pre-loaded scope)
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NBAQuarter4ExactTotalModel(BaseCandidateModel):
    """Compound-possession, regime-mixed exact-total model for the NBA 4th quarter."""

    # Domain priors: league structural facts. These are not fitted clamps,
    # and every one can be overridden through constructor kwargs or game_context.
    PRIORS: Dict[str, float] = {
        # Anchors from the empirical baseline (quarter-level points)
        "anchor_home": 28.0,
        "anchor_away": 27.0,
        # Share of full-game scoring that falls in Q4 (12 of 48 regulation minutes)
        "q4_share": 12.0 / 48.0,
        # League possession outcome law on {0,1,2,3,4} points (E[X] ≈ 1.14)
        "px0": 0.49, "px1": 0.04, "px2": 0.32, "px3": 0.14, "px4": 0.01,
        # Elasticity of three-point share to matchup offensive exposure
        "three_share_elasticity": 0.5,
        # Possession count dispersion inside a regime: Var(N)/E[N]
        "phi_possessions": 0.12,
        # Full-game home margin SD. The margin entering Q4 scales by sqrt(3/4).
        "full_game_margin_sd": 13.5,
        # Regime boundaries on |margin entering Q4|
        "close_margin": 8.0,
        "blowout_margin": 20.0,
        # Close regime: total intentional / late-foul FT trips, and the leader's share
        "close_foul_trips": 2.0,
        "leader_foul_share": 0.75,
        "close_pace_mult": 1.02,
        # Blowout regime: bench units, uneven pace, lower efficiency
        "blowout_pace_mult": 0.98,
        "blowout_eff_mult": 0.96,
        "blowout_phi_mult": 1.6,
        # League free-throw percentage
        "ft_pct": 0.78,
        # Q4 pace relative to the full-game average possession rate
        "q4_pace_ratio": 0.98,
        # Rest elasticity of efficiency on the log1p(rest days) scale
        "rest_beta": 0.015,
        "typical_rest_days": 1.0,
        # Probability of a possession-count offset between the teams (each side)
        "possession_offset_prob": 0.25,
        # Shrinkage strength (pseudo-games) used when fitting dispersion
        "prior_strength_games": 60.0,
        # Numerical tail tolerance
        "tail_eps": 1e-12,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_4_1107_144838",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_4",
        family: str = "exact_total",
        **prior_overrides: Any,
    ) -> None:
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        for k, v in (("model_id", model_id), ("version", version), ("league", league),
                     ("scope", scope), ("family", family)):
            try:
                setattr(self, k, v)
            except Exception:
                pass
        self.params: Dict[str, float] = dict(self.PRIORS)
        for k, v in prior_overrides.items():
            if k in self.params:
                self.params[k] = float(v)
        self.last_total_pmf: Dict[int, float] = {}
        self.last_diagnostics: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], keys: Iterable[str], default: Optional[float]) -> Optional[float]:
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
        return default

    def _p(self, ctx: Dict[str, Any], name: str) -> float:
        v = self._num(ctx, (name,), None)
        return float(v) if v is not None else float(self.params[name])

    def _ratings(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        att, dfn = None, None
        block = ctx.get(side)
        if isinstance(block, dict):
            att = self._num(block, ("attack", "attack_rating", "offense", "off"), None)
            dfn = self._num(block, ("defense", "defense_rating", "defence", "def"), None)
        if att is None:
            att = self._num(ctx, (f"{side}_attack", f"{side}_offense"), None)
        if dfn is None:
            dfn = self._num(ctx, (f"{side}_defense", f"{side}_defence"), None)
        att = att if (att is not None and att > 0) else 1.0
        dfn = dfn if (dfn is not None and dfn > 0) else 1.0
        return att, dfn

    def _quarter_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        """Return quarter-level means and the full-game scale factor (1/q4_share or 1)."""
        q4_share = self._p(ctx, "q4_share")
        h = self._num(ctx, ("home_expected", "home_q4_expected", "lambda_home"), None)
        a = self._num(ctx, ("away_expected", "away_q4_expected", "lambda_away"), None)
        anchor_h, anchor_a = self.params["anchor_home"], self.params["anchor_away"]
        if h is None or h <= 0:
            h = anchor_h
        if a is None or a <= 0:
            a = anchor_a
        scope_hint = str(ctx.get("expected_scope", "")).lower()
        if scope_hint in ("full", "full_game", "game"):
            is_full = True
        elif scope_hint in ("quarter", "quarter_4", "q4"):
            is_full = False
        else:
            # Pick the nearer lattice point in log space: quarter scale (1)
            # or full-game scale (1/q4_share).
            r = math.log((h + a) / (anchor_h + anchor_a))
            is_full = abs(r - math.log(1.0 / q4_share)) < abs(r)
        if is_full:
            return h * q4_share, a * q4_share, 1.0 / q4_share
        return h, a, 1.0

    def _rest_factor(self, ctx: Dict[str, Any], side: str) -> float:
        rest = self._num(ctx, (f"{side}_rest_days", f"{side}_rest"), None)
        if rest is None or rest < 0:
            return 1.0
        beta = self._p(ctx, "rest_beta")
        typ = self._p(ctx, "typical_rest_days")
        return math.exp(beta * (math.log1p(rest) - math.log1p(typ)))

    # ------------------------------------------------------------------ #
    # Probability primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tilt(base: np.ndarray, target_mean: float) -> np.ndarray:
        """Exponential tilt p_k ∝ b_k e^{θk} with E[X] = target_mean (minimum-KL)."""
        k = np.arange(base.size, dtype=float)
        hi_support = float(k[-1])
        m = min(max(target_mean, 1e-9), hi_support - 1e-9)

        def gap(theta: float) -> float:
            w = base * np.exp(theta * (k - k.mean()))
            return float((w * k).sum() / w.sum()) - m

        lo, hi = -1.0, 1.0
        while gap(lo) > 0:
            lo *= 2.0
        while gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(gap, lo, hi, xtol=1e-12)
        w = base * np.exp(theta * (k - k.mean()))
        return w / w.sum()

    def _possession_pmf(self, mean: float, phi: float, eps: float) -> Tuple[np.ndarray, int]:
        """Possession-count pmf: binomial (phi<1), Poisson (phi=1), NB (phi>1)."""
        sd = math.sqrt(max(mean * phi, 1e-9))
        n_hi = int(math.ceil(mean + 15.0 * sd + 5.0))
        grid = np.arange(0, n_hi + 1)
        if phi < 1.0 - 1e-9:
            trials = max(int(round(mean / (1.0 - phi))), int(math.ceil(mean)) + 1)
            pmf = stats.binom.pmf(grid, trials, mean / trials)
        elif phi > 1.0 + 1e-9:
            r = mean / (phi - 1.0)
            pmf = stats.nbinom.pmf(grid, r, r / (r + mean))
        else:
            pmf = stats.poisson.pmf(grid, mean)
        pmf = np.where(pmf < eps, 0.0, pmf)
        nz = np.nonzero(pmf)[0]
        lo = int(nz[0]) if nz.size else 0
        pmf = pmf[lo:]
        return pmf / pmf.sum(), lo

    @staticmethod
    def _foul_pmf(lam: float, ft: float, length: int) -> np.ndarray:
        """Compound Poisson(λ) of Binomial(2, ft) trips, computed by Panjer recursion."""
        g = np.zeros(length)
        if lam <= 0:
            g[0] = 1.0
            return g
        b = np.array([(1 - ft) ** 2, 2 * ft * (1 - ft), ft ** 2])
        g[0] = math.exp(-lam * (1.0 - b[0]))
        for s in range(1, length):
            acc = 0.0
            for j in (1, 2):
                if s - j >= 0:
                    acc += j * b[j] * g[s - j]
            g[s] = lam / s * acc
        return g / g.sum()

    @staticmethod
    def _power_table(x: np.ndarray, n_max: int, length: int) -> List[np.ndarray]:
        out = [np.zeros(length)]
        out[0][0] = 1.0
        cur = out[0]
        for _ in range(n_max):
            cur = np.convolve(cur, x)[:length]
            out.append(cur)
        return out

    # ------------------------------------------------------------------ #
    # Core joint score construction
    # ------------------------------------------------------------------ #
    def _joint_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        P = lambda name: self._p(ctx, name)  # noqa: E731
        eps = P("tail_eps")
        mu_h, mu_a, full_scale = self._quarter_means(ctx)

        # Rest modifies the anchored mean unless upstream already priced it
        if not bool(ctx.get("expected_includes_rest", False)):
            mu_h *= self._rest_factor(ctx, "home")
            mu_a *= self._rest_factor(ctx, "away")

        # Possession outcome shape reshaped by the matchup three-point exposure
        base = np.array([P("px0"), P("px1"), P("px2"), P("px3"), P("px4")], dtype=float)
        base = base / base.sum()
        ppp_prior = float((base * np.arange(5)).sum())
        att_h, def_h = self._ratings(ctx, "home")
        att_a, def_a = self._ratings(ctx, "away")
        gamma = P("three_share_elasticity")

        def shaped(expo: float) -> np.ndarray:
            b = base.copy()
            b[3] *= expo ** gamma
            b[4] *= expo ** gamma
            return b / b.sum()

        base_h = shaped(math.sqrt(att_h * def_a))
        base_a = shaped(math.sqrt(att_a * def_h))

        # Q4 possessions per team
        pace = self._num(ctx, ("pace", "expected_pace", "game_pace"), None)
        if pace is None:
            ph = self._num(ctx, ("home_pace",), None)
            pa = self._num(ctx, ("away_pace",), None)
            if ph is not None and pa is not None:
                pace = 0.5 * (ph + pa)
            else:
                pace = ph if ph is not None else pa
        if pace is not None and pace > 0:
            n_q4 = pace * P("q4_share") * P("q4_pace_ratio")
        else:
            n_q4 = 0.5 * (mu_h + mu_a) / ppp_prior

        # Distribution of the margin entering Q4
        m3_live = self._num(ctx, ("margin_entering_q4", "current_margin", "q3_margin"), None)
        if m3_live is not None:
            m3, s3 = m3_live, max(P("full_game_margin_sd") * 0.05, 1e-3)
        else:
            m3 = 3.0 * (mu_h - mu_a)
            s3 = self._num(ctx, ("margin_sd",), None) or P("full_game_margin_sd")
            s3 = s3 * math.sqrt(0.75)
        k_c, k_b = P("close_margin"), P("blowout_margin")
        cdf = lambda x: stats.norm.cdf((x - m3) / s3)  # noqa: E731
        w_close = cdf(k_c) - cdf(-k_c)
        w_blow = (1.0 - cdf(k_b)) + cdf(-k_b)
        w_comp = max(1.0 - w_close - w_blow, 0.0)
        p_home_lead_close = (cdf(k_c) - cdf(0.0)) / max(w_close, eps)

        phi = P("phi_possessions")
        lam_tot = P("close_foul_trips")
        lead_share = P("leader_foul_share")
        lam_h_close = lam_tot * (lead_share * p_home_lead_close
                                 + (1 - lead_share) * (1 - p_home_lead_close))
        lam_a_close = lam_tot - lam_h_close
        ft = P("ft_pct")

        regimes = [
            # (weight, pace_mult, eff_mult, phi, lam_home, lam_away)
            (w_close, P("close_pace_mult"), 1.0, phi, lam_h_close, lam_a_close),
            (w_comp, 1.0, 1.0, phi, 0.0, 0.0),
            (w_blow, P("blowout_pace_mult"), P("blowout_eff_mult"),
             phi * P("blowout_phi_mult"), 0.0, 0.0),
        ]
        regimes = [r for r in regimes if r[0] > eps]
        wsum = sum(r[0] for r in regimes)
        regimes = [(r[0] / wsum,) + r[1:] for r in regimes]

        # Closed-form efficiency that preserves each team's anchored mean
        denom = sum(w * n_q4 * pm * em for w, pm, em, _, _, _ in regimes)
        ft_h = sum(w * lh * 2 * ft for w, _, _, _, lh, _ in regimes)
        ft_a = sum(w * la * 2 * ft for w, _, _, _, _, la in regimes)
        ppp_h = max(mu_h - ft_h, eps) / denom
        ppp_a = max(mu_a - ft_a, eps) / denom

        # Support length from a conservative moment bound
        var_x = float((base * np.arange(5) ** 2).sum()) - ppp_prior ** 2
        sd_team = math.sqrt(max(mu_h, mu_a) * (var_x / ppp_prior + 1.0))
        L = int(math.ceil(max(mu_h, mu_a) + 14.0 * sd_team + 20.0))

        q_off = P("possession_offset_prob")
        offsets = ((-1, q_off), (0, 1.0 - 2.0 * q_off), (1, q_off))

        joint = np.zeros((L, L))
        for w, pm, em, ph_r, lam_h, lam_a in regimes:
            x_h = self._tilt(base_h, ppp_h * em)
            x_a = self._tilt(base_a, ppp_a * em)
            n_pmf, n_lo = self._possession_pmf(n_q4 * pm, ph_r, eps)
            n_max = n_lo + n_pmf.size
            pow_h = self._power_table(x_h, n_max + 1, L)
            pow_a = self._power_table(x_a, n_max + 1, L)
            f_h = self._foul_pmf(lam_h, ft, L)
            f_a = self._foul_pmf(lam_a, ft, L)
            for i, pn in enumerate(n_pmf):
                if pn <= eps:
                    continue
                n = n_lo + i
                h_vec = np.convolve(pow_h[n], f_h)[:L]
                a_vec = np.zeros(L)
                for d, qd in offsets:
                    if qd <= 0:
                        continue
                    a_vec += qd * pow_a[max(n + d, 0)]
                a_vec = np.convolve(a_vec, f_a)[:L]
                joint += (w * pn) * np.outer(h_vec, a_vec)

        joint /= joint.sum()
        hs = np.arange(L)
        tot_pmf = np.bincount((hs[:, None] + hs[None, :]).ravel(),
                              weights=joint.ravel())
        mean_t = float((np.arange(tot_pmf.size) * tot_pmf).sum())
        var_t = float((np.arange(tot_pmf.size) ** 2 * tot_pmf).sum()) - mean_t ** 2
        self.last_total_pmf = {int(t): float(p) for t, p in enumerate(tot_pmf) if p > eps}
        self.last_diagnostics = {
            "mu_home_q4": mu_h, "mu_away_q4": mu_a, "full_game_scale": full_scale,
            "possessions_q4": n_q4, "w_close": w_close, "w_competitive": w_comp,
            "w_blowout": w_blow, "total_mean": mean_t, "total_var": var_t,
            "dispersion_index": var_t / mean_t if mean_t > 0 else float("nan"),
        }
        return joint

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = dict(game_context or {})
        joint = self._joint_matrix(ctx)
        eps = self._p(ctx, "tail_eps")
        idx = np.argwhere(joint > eps)
        scores = {(int(h), int(a)): float(joint[h, a]) for h, a in idx}
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_scores(joint)
        except Exception:
            pass
        return DiscreteDistribution(probabilities={str(t): p for t, p in self.last_total_pmf.items()})

    def predict_total_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        self._joint_matrix(dict(game_context or {}))
        return dict(self.last_total_pmf)

    def fit(self, history: Optional[Iterable[Dict[str, Any]]] = None, *args: Any, **kwargs: Any):
        """Moment-match phi_N to the observed Q4 dispersion index, shrunk toward the prior.

        history: iterable of dicts with observed Q4 'home_score' and 'away_score'.
        With a cold start (no data) the domain priors are kept unchanged.
        """
        rows = list(history or [])
        h_obs, a_obs = [], []
        for r in rows:
            h = self._num(r, ("home_score", "home_q4_score", "home_points"), None)
            a = self._num(r, ("away_score", "away_q4_score", "away_points"), None)
            if h is not None and a is not None:
                h_obs.append(h)
                a_obs.append(a)
        n = len(h_obs)
        if n < 2:
            return self
        h_arr, a_arr = np.asarray(h_obs), np.asarray(a_obs)
        tot = h_arr + a_arr
        n0 = self.params["prior_strength_games"]
        # Posterior anchors: precision-weighted toward the prior baseline
        self.params["anchor_home"] = (n0 * self.params["anchor_home"] + h_arr.sum()) / (n0 + n)
        self.params["anchor_away"] = (n0 * self.params["anchor_away"] + a_arr.sum()) / (n0 + n)
        d_obs = float(tot.var(ddof=1) / tot.mean())
        ref = {"home_expected": self.params["anchor_home"],
               "away_expected": self.params["anchor_away"],
               "expected_scope": "quarter"}
        phi_prior = self.params["phi_possessions"]

        def gap(phi: float) -> float:
            self.params["phi_possessions"] = phi
            self._joint_matrix(ref)
            return self.last_diagnostics["dispersion_index"] - d_obs

        try:
            lo, hi = 1e-3, 1.0
            while gap(hi) < 0 and hi < 1e3:
                hi *= 2.0
            phi_hat = optimize.brentq(gap, lo, hi, xtol=1e-4) if gap(lo) < 0 else lo
        except Exception:
            phi_hat = phi_prior
        self.params["phi_possessions"] = (n0 * phi_prior + n * phi_hat) / (n0 + n)
        return self