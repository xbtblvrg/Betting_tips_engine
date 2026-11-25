import math
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np
from scipy import optimize, special

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - DiscreteDistribution is pre-loaded in the execution scope
    pass


class codex_synth_ncaam_full_game_1125_104606(BaseCandidateModel):
    """
    NCAAM full-game totals: a possession-based compound model with a robust combination step.

    Generative structure
    --------------------
    T = P * (e_h + e_a)
      P   : possessions. Each team's tempo is shrunk toward the league mean by exposure
            (games observed). The two tempos are then combined with a negative-exponent
            power mean, so the slower team dominates. If upstream expectations were built
            additively, they are rescaled by P_powermean / P_arithmetic. This ratio is <= 1
            and is largest when the tempos differ, which corrects the documented
            high-line / over inflation.
      e_i : league_ppp * Att_i * Def_j, anchored at 74.5 / 70.5. Ratings are shrunk by exposure.

    Variance decomposition
    ----------------------
    Var(T) = E[P] * D * (e_h + e_a)            (chunked scoring; D = empirical Var/Mean 0.668
                                                per team-possession)
           + (e_h + e_a)^2 * sigma_P^2          (pace uncertainty)
           + 2 * (E[P] * sigma_eff)^2           (game-level shooting / efficiency shocks)
           + lambda(|margin|) * sigma_foul^2    (late intentional-foul tail; decays with spread)
           + Var(mean estimate)                 (posterior + disagreement mixture variance)

    The skew is anchored at the empirical +0.20 and scales with the foul-tail share of the
    variance. A shifted-gamma law matches mean, variance and skew in closed form.

    Overconfidence / tail-hubris repair
    -----------------------------------
    The posted total is treated as a noisy *measurement* of the scoring mean (a sufficient
    statistic of public information), not as a vig-bearing price. Odds are never used.
    The model mean and the line are combined by precision weighting:
      - kappa0 = s_L^2 / (s_L^2 + s_mod^2).
      - s_mod widens with |log(L / L_league)|, because ratings extrapolate poorly at extreme lines.
      - A Student-t robustness weight shrinks kappa as the standardized disagreement grows.
        This directly targets rho(p_mod - p_mkt, y - p_mod) = -0.325.
      - The mixture variance kappa * (1 - kappa) * Delta^2 is added, so large disagreements
        widen the distribution instead of producing confident tail calls.

    Output: a joint (home, away) score lattice built by integer-total pmf x a parity-respecting
    conditional margin kernel (no ties), returned through DiscreteDistribution.from_scores.
    Everything is vectorized; typical latency is a few milliseconds.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_full_game_1125_104606",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "full_game",
        family: str = "totals",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family, **kwargs
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Empirical anchors (from the segment autopsy).
        self.base_home = 74.5
        self.base_away = 70.5
        self.dispersion_index = 0.668          # Var/Mean of points per team-possession
        self.skew_empirical = 0.200

        # Structural priors (all refit by .fit(); valid defaults without fitting).
        self.p_league = 68.0                   # league possessions per game
        self.tempo_power = -4.0                # power-mean exponent (<0 => slower team dominates)
        self.exposure_n0 = 8.0                 # pseudo-games for rating / tempo shrinkage
        self.default_exposure = 0.75           # exposure weight when games_played unknown
        self.sigma_P = 5.0                     # possession SD per game
        self.sigma_eff = 0.075                 # per-team game efficiency shock SD (pts/poss)
        self.sigma_foul = 5.0                  # foul-tail SD in close games
        self.foul_scale = 6.0                  # spread scale over which foul tail decays
        self.ref_abs_spread = 8.0              # typical |spread| for skew normalization
        self.cv_mod = 0.050                    # relative error of mechanistic mean
        self.cv_line = 0.022                   # relative error of posted total as mean estimate
        self.robust_nu = 4.0                   # Student-t df for disagreement robustness
        self.score_corr = 0.10                 # home/away score correlation
        self.rest_beta = 0.015                 # efficiency elasticity w.r.t. log(1 + rest)
        self.rest_ref = 3.0
        self.bias_total = 0.0                  # learnable additive bias (directional neutrality)

        self.last_diagnostics: Dict[str, float] = {}

    # ------------------------------------------------------------------ utilities
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        return v if math.isfinite(v) else default

    def _first(self, *vals: Any) -> Optional[float]:
        for v in vals:
            f = self._num(v)
            if f is not None:
                return f
        return None

    def _side(self, ctx: Dict[str, Any], side: str) -> Dict[str, Optional[float]]:
        d = ctx.get(side)
        d = d if isinstance(d, dict) else {}
        return {
            "att": self._first(d.get("attack"), d.get("off_rating"), d.get("adj_o"),
                               ctx.get(f"{side}_attack"), ctx.get(f"{side}_off_rating")),
            "def": self._first(d.get("defense"), d.get("def_rating"), d.get("adj_d"),
                               ctx.get(f"{side}_defense"), ctx.get(f"{side}_def_rating")),
            "pace": self._first(d.get("pace"), d.get("tempo"), ctx.get(f"{side}_pace"),
                                ctx.get(f"{side}_tempo")),
            "games": self._first(d.get("games_played"), d.get("games"), ctx.get(f"{side}_games_played")),
            "rest": self._first(ctx.get(f"{side}_rest_days"), d.get("rest_days")),
        }

    @staticmethod
    def _rating_multiplier(v: Optional[float], ppp_league: float) -> float:
        """Map an attack/defense rating to a multiplicative factor around 1.0.
        Ratings > 3 are treated as per-100-possession efficiencies."""
        if v is None or v <= 0.0:
            return 1.0
        if v > 3.0:
            return v / (100.0 * ppp_league)
        return v

    def _exposure(self, games: Optional[float]) -> float:
        if games is None or games < 0.0:
            return self.default_exposure
        return games / (games + self.exposure_n0)

    def _rest_factor(self, rest: Optional[float]) -> float:
        if rest is None:
            return 1.0
        r = max(rest, 0.0)
        return math.exp(self.rest_beta * (math.log1p(r) - math.log1p(self.rest_ref)))

    # ------------------------------------------------------------------ moments
    def _moments(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        base_h = self._num(ctx.get("baseline_home"), self.base_home) or self.base_home
        base_a = self._num(ctx.get("baseline_away"), self.base_away) or self.base_away
        P0 = self.p_league
        ppp_league = (base_h + base_a) / (2.0 * P0)

        ext_h = self._num(ctx.get("home_expected"))
        ext_a = self._num(ctx.get("away_expected"))
        has_ext = ext_h is not None and ext_a is not None and ext_h > 0 and ext_a > 0
        mu_h0 = ext_h if has_ext else base_h
        mu_a0 = ext_a if has_ext else base_a

        H = self._side(ctx, "home")
        A = self._side(ctx, "away")

        # --- possessions: exposure-shrunk tempos, slower-weighted power mean
        pace_factor = 1.0
        if H["pace"] is not None and A["pace"] is not None and H["pace"] > 0 and A["pace"] > 0:
            ph = P0 + self._exposure(H["games"]) * (H["pace"] - P0)
            pa = P0 + self._exposure(A["games"]) * (A["pace"] - P0)
            ph, pa = max(ph, 1e-6), max(pa, 1e-6)
            r = self.tempo_power
            p_pow = (0.5 * (ph ** r + pa ** r)) ** (1.0 / r)
            p_arith = 0.5 * (ph + pa)
            # Upstream expectations already embed an (additive) pace: correct only the blend bias.
            pace_factor = (p_pow / p_arith) if has_ext else (p_pow / P0)
        EP = P0 * pace_factor

        # --- efficiency: attack x opposing defense, exposure-shrunk
        gamma = 0.5 if has_ext else 1.0
        att_h = self._rating_multiplier(H["att"], ppp_league)
        def_h = self._rating_multiplier(H["def"], ppp_league)
        att_a = self._rating_multiplier(A["att"], ppp_league)
        def_a = self._rating_multiplier(A["def"], ppp_league)
        m_h = (att_h * def_a) ** (gamma * self._exposure(H["games"]))
        m_a = (att_a * def_h) ** (gamma * self._exposure(A["games"]))

        mu_h_mod = mu_h0 * pace_factor * m_h * self._rest_factor(H["rest"])
        mu_a_mod = mu_a0 * pace_factor * m_a * self._rest_factor(A["rest"])
        mu_mod = mu_h_mod + mu_a_mod + self.bias_total
        mu_mod = max(mu_mod, 1.0)
        e_sum = mu_mod / max(EP, 1e-6)

        # --- margin location (for the foul tail and lattice orientation)
        spread = self._first(ctx.get("spread_line"), ctx.get("home_handicap"))
        d_mod = mu_h_mod - mu_a_mod
        L_league = base_h + base_a

        # --- robust precision-weighted combination with the posted total
        L = self._num(ctx.get("total_line"))
        if L is not None and L > 0:
            ext_log = math.log(L / L_league)
            cv_mod_eff = math.sqrt(self.cv_mod ** 2 + ext_log ** 2)
            s_mod2 = (cv_mod_eff * mu_mod) ** 2
            s_L2 = (self.cv_line * L) ** 2
            kappa0 = s_L2 / (s_L2 + s_mod2)
            delta = mu_mod - L
            z2 = delta * delta / (s_L2 + s_mod2)
            w = (self.robust_nu + 1.0) / (self.robust_nu + z2)
            kappa = kappa0 * w / (1.0 + kappa0 * (w - 1.0))
            mu = L + kappa * delta
            var_mean = (s_mod2 * s_L2) / (s_mod2 + s_L2) + kappa * (1.0 - kappa) * delta * delta
        else:
            kappa, delta = 1.0, 0.0
            mu = mu_mod
            var_mean = (self.cv_mod * mu_mod) ** 2

        if spread is not None:
            mu_m = -spread + (kappa if L is not None else 0.5) * (d_mod + spread)
        else:
            mu_m = d_mod

        # --- variance decomposition
        lam = math.exp(-(mu_m / self.foul_scale) ** 2)
        lam_ref = math.exp(-(self.ref_abs_spread / self.foul_scale) ** 2)
        var_chunk = EP * self.dispersion_index * e_sum
        var_pace = (e_sum ** 2) * self.sigma_P ** 2
        var_eff = 2.0 * (EP * self.sigma_eff) ** 2
        var_foul = lam * self.sigma_foul ** 2
        var_core = var_chunk + var_pace + var_eff
        var_T = var_core + var_foul + var_mean
        sigma_T = math.sqrt(max(var_T, 1e-6))

        share = var_foul / var_T
        share_ref = lam_ref * self.sigma_foul ** 2 / (var_core + lam_ref * self.sigma_foul ** 2 + var_mean)
        skew = self.skew_empirical * (1.0 + share) / (1.0 + share_ref)

        return {
            "mu": mu, "sigma": sigma_T, "skew": skew, "mu_m": mu_m,
            "mu_mod": mu_mod, "kappa": kappa, "delta": delta, "EP": EP,
            "pace_factor": pace_factor, "lambda_foul": lam,
        }

    # ------------------------------------------------------------------ distribution
    def _total_cdf(self, x: np.ndarray, mu: float, sigma: float, skew: float) -> np.ndarray:
        if skew > 1e-3:
            k = 4.0 / (skew * skew)
            theta = sigma * skew / 2.0
            loc = mu - k * theta
            z = np.maximum(x - loc, 0.0) / theta
            return special.gammainc(k, z)
        return special.ndtr((x - mu) / sigma)

    def _score_matrix(self, mom: Dict[str, float]) -> np.ndarray:
        mu, sigma, skew, mu_m = mom["mu"], mom["sigma"], mom["skew"], mom["mu_m"]
        t_max = int(math.ceil(mu + 9.0 * sigma))
        t = np.arange(t_max + 1, dtype=float)
        cdf_hi = self._total_cdf(t + 0.5, mu, sigma, skew)
        cdf_lo = np.concatenate(([0.0], cdf_hi[:-1]))
        pmf_t = np.clip(cdf_hi - cdf_lo, 0.0, None)
        s = pmf_t.sum()
        pmf_t = pmf_t / s if s > 0 else np.full_like(pmf_t, 1.0 / pmf_t.size)

        rho = self.score_corr
        sigma_m = sigma * math.sqrt((1.0 - rho) / (1.0 + rho))
        n = int(math.ceil((t_max + abs(mu_m) + 5.0 * sigma_m) / 2.0)) + 1

        hs = np.arange(n)
        hh, aa = np.meshgrid(hs, hs, indexing="ij")
        tt = hh + aa
        mm = (hh - aa).astype(float)
        W = np.exp(-0.5 * ((mm - mu_m) / sigma_m) ** 2)
        W[hh == aa] = 0.0  # basketball games cannot end tied

        size = 2 * n - 1
        pmf_pad = np.zeros(size)
        upto = min(size, pmf_t.size)
        pmf_pad[:upto] = pmf_t[:upto]
        S = np.bincount(tt.ravel(), weights=W.ravel(), minlength=size)
        factor = np.divide(pmf_pad, S, out=np.zeros_like(pmf_pad), where=S > 0)
        P = W * factor[tt]
        tot = P.sum()
        if not np.isfinite(tot) or tot <= 0:
            P = np.zeros((n, n))
            h0 = min(n - 1, max(0, int(round((mu + mu_m) / 2.0))))
            a0 = min(n - 1, max(0, int(round((mu - mu_m) / 2.0))))
            if h0 == a0:
                h0 = min(n - 1, h0 + 1)
            P[h0, a0] = 1.0
            return P
        return P / tot

    def _emit(self, P: np.ndarray) -> "DiscreteDistribution":
        try:
            return DiscreteDistribution.from_scores(P)
        except Exception:
            pass
        try:
            idx = np.argwhere(P > 1e-12)
            vals = P[P > 1e-12]
            vals = vals / vals.sum()
            scores = {(int(h), int(a)): float(p) for (h, a), p in zip(idx, vals)}
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        hh, aa = np.indices(P.shape)
        p_home = float(P[hh > aa].sum())
        p_away = float(P[hh < aa].sum())
        z = p_home + p_away
        p_home, p_away = (p_home / z, p_away / z) if z > 0 else (0.5, 0.5)
        return DiscreteDistribution.from_probabilities(p_home, 0.0, p_away)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context if isinstance(game_context, dict) else {}
        mom = self._moments(ctx)
        self.last_diagnostics = {k: float(v) for k, v in mom.items()}
        return self._emit(self._score_matrix(mom))

    def over_probability(self, game_context: Dict[str, Any], line: Optional[float] = None) -> float:
        """Closed-form P(T > line) from the same moments; push mass excluded."""
        ctx = game_context if isinstance(game_context, dict) else {}
        mom = self._moments(ctx)
        L = self._num(line, self._num(ctx.get("total_line"), mom["mu"]))
        cdf = lambda x: float(self._total_cdf(np.array([x]), mom["mu"], mom["sigma"], mom["skew"])[0])
        if abs(L - round(L)) < 1e-9:
            p_over = 1.0 - cdf(L + 0.5)
            p_under = cdf(L - 0.5)
            z = p_over + p_under
            return p_over / z if z > 0 else 0.5
        return 1.0 - cdf(L)

    # ------------------------------------------------------------------ optional fitting
    _FIT_KEYS: Tuple[str, ...] = ("cv_mod", "sigma_P", "sigma_eff", "bias_total")

    def fit(self, history: Optional[Iterable[Dict[str, Any]]] = None, **kwargs: Any) -> "codex_synth_ncaam_full_game_1125_104606":
        """Optional out-of-sample refit by predictive log-likelihood of realized totals.
        Each record: {'context': {...}, 'actual_total': x} or with home_score / away_score."""
        if history is None:
            return self
        rows = []
        for rec in history:
            if not isinstance(rec, dict):
                continue
            ctx = rec.get("context") or rec.get("game_context") or rec
            y = self._num(rec.get("actual_total"))
            if y is None:
                hs, as_ = self._num(rec.get("home_score")), self._num(rec.get("away_score"))
                y = hs + as_ if hs is not None and as_ is not None else None
            if y is not None and isinstance(ctx, dict):
                rows.append((ctx, y))
        if len(rows) < 30:
            return self

        start = np.array([math.log(self.cv_mod), math.log(self.sigma_P),
                          math.log(self.sigma_eff), self.bias_total])
        saved = {k: getattr(self, k) for k in self._FIT_KEYS}

        def nll(theta: np.ndarray) -> float:
            self.cv_mod, self.sigma_P = math.exp(theta[0]), math.exp(theta[1])
            self.sigma_eff, self.bias_total = math.exp(theta[2]), float(theta[3])
            total = 0.0
            for ctx, y in rows:
                m = self._moments(ctx)
                z = (y - m["mu"]) / m["sigma"]
                total += 0.5 * z * z + math.log(m["sigma"])
            # weak Gaussian prior toward defaults (regularization against in-sample noise)
            total += 0.5 * float(np.sum(((theta - start) / 0.5) ** 2))
            return total

        try:
            res = optimize.minimize(nll, start, method="Nelder-Mead",
                                    options={"maxiter": 200, "xatol": 1e-3, "fatol": 1e-3})
            th = res.x if res.success or np.all(np.isfinite(res.x)) else start
            self.cv_mod, self.sigma_P = math.exp(th[0]), math.exp(th[1])
            self.sigma_eff, self.bias_total = math.exp(th[2]), float(th[3])
        except Exception:
            for k, v in saved.items():
                setattr(self, k, v)
        return self