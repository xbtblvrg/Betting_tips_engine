import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.special import gammaln, roots_hermitenorm
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - resolved from execution scope / alternate module path
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class SerieAHalf1DoubleChanceModel(BaseCandidateModel):
    """
    SERIEA | half_1 | double_chance

    First-half score model: Dixon-Coles-corrected Poisson lattice with full posterior
    integration over rate uncertainty, overdispersion, and the low-score dependence
    parameter rho.

    Structure
    ---------
    1. Rates
       lambda_h = base_h * pi * r_h,   lambda_a = base_a * pi * r_a
       base_* comes from the league half-1 anchors (0.80 / 0.60). If the context supplies
       match expectations, they are converted to half-1 scope (when needed) and shrunk
       toward the anchors in log space, with weights set by sample size. Otherwise the
       anchors are scaled by attack x opposing-defence ratings.
       pi is a pace multiplier normalised to a league mean of 1. The rest factors are
       differential (r_h * r_a = 1), so the league-mean anchors are preserved exactly.

    2. Low-score dependence
       The Dixon-Coles tau correction is applied to cells (0,0), (1,0), (0,1), (1,1). The
       correction preserves total mass. P(draw) is linear in rho:
           P_draw(rho) = P_draw_ind - rho * (lh*la*p00 + p11)
       so rho has a closed form. The target draw rate is a Beta posterior. Its prior is
       centred on the *independent-Poisson* draw rate at the anchors, and it is updated
       with the observed league halftime draw frequency. Small samples therefore shrink
       rho toward 0 automatically, with no fixed rho constant.

    3. Uncertainty
       log-lambda ~ Normal with variance 1/(n_eff * lambda) (Poisson estimation error)
       plus log(1 + (D-1)/lambda) (gamma-mixing overdispersion implied by the dispersion
       index D). rho uncertainty is propagated from the Beta posterior on the draw rate.
       Probabilities are posterior means computed by Gauss-Hermite quadrature. Posterior
       SDs of 1X / X2 / 12 drive the bet filter.

    4. Pricing
       P(1X) = 1 - P(away leads),  P(X2) = 1 - P(home leads),  P(12) = 1 - P(draw).
       A bet is eligible when odds >= min_odds, EV > 0, and EV exceeds one posterior SD
       of EV. Stake uses Kelly on the one-SD lower-bound probability.

    All tunable quantities live in `self.config` and can be overridden per call through
    game_context['model_config'].
    """

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_1_1108_055819",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_1",
        family: str = "double_chance",
        config: Optional[Dict[str, Any]] = None,
    ):
        try:
            super().__init__(model_id=model_id, version=version, league=league,
                             scope=scope, family=family)
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

        # Empirical / domain inputs. These are data, not clamps; all are overridable.
        self.config: Dict[str, Any] = {
            # League half-1 scoring anchors (empirical baseline means).
            "anchor_home": 0.80,
            "anchor_away": 0.60,
            # Observed dispersion index (Var/Mean). 1.0 = Poisson, >1 adds gamma mixing.
            "dispersion_index": 1.0,
            # League full-match goal mean. Used only to convert full-game expectations
            # to half-1 scope.
            "league_full_match_total": 2.75,
            # Observed Serie A halftime draw frequency and the number of halftimes behind it.
            "league_ht_draw_rate": 0.42,
            "league_ht_sample": 1520,
            # Prior strength (pseudo-halftimes) of the independence prior on the draw rate.
            # One league season.
            "rho_prior_strength": 380.0,
            # Pseudo-matches of league-anchor information when shrinking match expectations.
            "anchor_pseudo_matches": 10.0,
            # Default number of matches behind supplied team ratings / expectations.
            "default_rating_sample": 38.0,
            # Rest/fatigue: saturating recovery curve g(d) = 1 - exp(-d / tau).
            "rest_recovery_tau_days": 2.0,
            "rest_elasticity": 0.05,
            # Pace elasticity of goal rates (1 = proportional).
            "pace_elasticity": 1.0,
            # Quadrature resolution.
            "gh_nodes_lambda": 5,
            "gh_nodes_rho": 3,
            # Score-lattice truncation tail mass.
            "tail_mass": 1e-12,
            # Market rules.
            "min_odds": 1.40,
            "uncertainty_z": 1.0,
        }
        if config:
            self.config.update(config)

        self.last_diagnostics: Dict[str, Any] = {}
        self._rho_cache: Optional[Tuple[Tuple, Dict[str, float]]] = None

    # ------------------------------------------------------------------ utilities
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            if math.isfinite(v):
                return v
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def _pmf(lam: float, n: int) -> np.ndarray:
        k = np.arange(n + 1, dtype=float)
        return np.exp(k * math.log(lam) - lam - gammaln(k + 1.0))

    def _grid_size(self, lam_max: float) -> int:
        return int(poisson.ppf(1.0 - self.config["tail_mass"], lam_max)) + 2

    @staticmethod
    def _rho_bounds(lh: float, la: float) -> Tuple[float, float]:
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)
        # Strictly inside the feasible region so every tau stays non-negative.
        return float(np.nextafter(lo, 0.0)), float(np.nextafter(hi, 0.0))

    def _score_matrix(self, lh: float, la: float, rho: float, n: int) -> np.ndarray:
        ph, pa = self._pmf(lh, n), self._pmf(la, n)
        m = np.outer(ph, pa)
        lo, hi = self._rho_bounds(lh, la)
        r = min(max(rho, lo), hi)
        m[0, 0] *= 1.0 - lh * la * r
        m[0, 1] *= 1.0 + lh * r
        m[1, 0] *= 1.0 + la * r
        m[1, 1] *= 1.0 - r
        s = m.sum()
        return m / s if s > 0 else m

    @staticmethod
    def _outcomes(m: np.ndarray) -> Tuple[float, float, float]:
        p_home = float(np.tril(m, -1).sum())  # rows = home goals > cols = away goals
        p_draw = float(np.trace(m))
        p_away = float(np.triu(m, 1).sum())
        tot = p_home + p_draw + p_away
        return p_home / tot, p_draw / tot, p_away / tot

    # ------------------------------------------------------------------ rho calibration
    def _rho_posterior(self) -> Dict[str, float]:
        c = self.config
        key = (c["anchor_home"], c["anchor_away"], c["league_ht_draw_rate"],
               c["league_ht_sample"], c["rho_prior_strength"], c["gh_nodes_rho"])
        if self._rho_cache and self._rho_cache[0] == key:
            return self._rho_cache[1]

        lh, la = float(c["anchor_home"]), float(c["anchor_away"])
        n = self._grid_size(max(lh, la))
        ind = np.outer(self._pmf(lh, n), self._pmf(la, n))
        ind /= ind.sum()
        p_ind = float(np.trace(ind))
        slope = lh * la * ind[0, 0] + ind[1, 1]  # -dP_draw/drho

        kappa = float(c["rho_prior_strength"])
        n_obs = max(float(c["league_ht_sample"]), 0.0)
        draws = n_obs * float(c["league_ht_draw_rate"])
        a = p_ind * kappa + draws
        b = (1.0 - p_ind) * kappa + (n_obs - draws)
        mean = a / (a + b)
        sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1.0)))

        lo, hi = self._rho_bounds(lh, la)
        nodes, weights = roots_hermitenorm(int(c["gh_nodes_rho"]))
        weights = weights / weights.sum()
        rhos = [min(max((p_ind - (mean + z * sd)) / slope, lo), hi) for z in nodes]

        out = {
            "p_draw_independent": p_ind,
            "draw_target_mean": mean,
            "draw_target_sd": sd,
            "rho_mean": float(np.dot(weights, rhos)),
            "rho_nodes": rhos,
            "rho_weights": weights.tolist(),
        }
        self._rho_cache = (key, out)
        return out

    # ------------------------------------------------------------------ rate construction
    def _rating(self, side: Dict[str, Any], keys: List[str]) -> Optional[float]:
        for k in keys:
            v = self._num(side.get(k))
            if v is not None:
                return v if v > 0 else math.exp(v)  # non-positive => log-scale rating
        return None

    def _base_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float, float, str]:
        c = self.config
        ah, aa = float(c["anchor_home"]), float(c["anchor_away"])
        n_anchor = float(c["anchor_pseudo_matches"])
        n_ctx = self._num(ctx.get("rating_sample"), float(c["default_rating_sample"]))

        eh, ea = self._num(ctx.get("home_expected")), self._num(ctx.get("away_expected"))
        if eh is not None and ea is not None and eh > 0 and ea > 0:
            scope_tag = str(ctx.get("expected_scope", "")).lower()
            half_total, full_total = ah + aa, float(c["league_full_match_total"])
            looks_full = (eh + ea) > math.sqrt(half_total * full_total)  # geometric midpoint
            if scope_tag in ("full", "full_game", "game", "match") or (not scope_tag and looks_full):
                share = half_total / full_total
                eh, ea = eh * share, ea * share
            w = n_ctx / (n_ctx + n_anchor)
            bh = math.exp(w * math.log(eh) + (1 - w) * math.log(ah))
            ba = math.exp(w * math.log(ea) + (1 - w) * math.log(aa))
            return bh, ba, n_ctx + n_anchor, "context_expected"

        home = ctx.get("home") if isinstance(ctx.get("home"), dict) else {}
        away = ctx.get("away") if isinstance(ctx.get("away"), dict) else {}
        att_h = self._rating(home, ["attack", "att", "attack_rating"])
        def_h = self._rating(home, ["defense", "defence", "def", "defense_rating"])
        att_a = self._rating(away, ["attack", "att", "attack_rating"])
        def_a = self._rating(away, ["defense", "defence", "def", "defense_rating"])
        if None not in (att_h, def_h, att_a, def_a):
            w = n_ctx / (n_ctx + n_anchor)
            mh = (att_h * def_a) ** w  # shrink rating product toward 1 (league mean)
            ma = (att_a * def_h) ** w
            return ah * mh, aa * ma, n_ctx + n_anchor, "ratings"
        return ah, aa, n_anchor, "anchor_only"

    def _pace_factor(self, ctx: Dict[str, Any]) -> float:
        pace = None
        for k in ("pace", "tempo", "pace_index"):
            pace = self._num(ctx.get(k))
            if pace is not None:
                break
        if pace is None or pace <= 0:
            return 1.0
        league_pace = self._num(ctx.get("league_pace"))
        rel = pace / league_pace if league_pace and league_pace > 0 else pace
        return rel ** float(self.config["pace_elasticity"])

    def _rest_factors(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        rh, ra = self._num(ctx.get("home_rest_days")), self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None:
            return 1.0, 1.0
        tau = float(self.config["rest_recovery_tau_days"])
        g = lambda d: 1.0 - math.exp(-max(d, 0.0) / tau)
        diff = g(rh) - g(ra)
        eta = float(self.config["rest_elasticity"])
        f = math.exp(0.5 * eta * diff)
        return f, 1.0 / f  # product = 1 -> league mean preserved

    # ------------------------------------------------------------------ core inference
    def _posterior(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        saved = None
        if isinstance(ctx.get("model_config"), dict):
            saved = dict(self.config)
            self.config.update(ctx["model_config"])
        try:
            bh, ba, n_eff, source = self._base_rates(ctx)
            pi = self._pace_factor(ctx)
            fh, fa = self._rest_factors(ctx)
            lh, la = bh * pi * fh, ba * pi * fa

            D = max(float(self.config["dispersion_index"]), 1.0)
            var_h = 1.0 / (n_eff * lh) + math.log1p((D - 1.0) / lh)
            var_a = 1.0 / (n_eff * la) + math.log1p((D - 1.0) / la)
            sh, sa = math.sqrt(var_h), math.sqrt(var_a)

            rp = self._rho_posterior()
            zl, wl = roots_hermitenorm(int(self.config["gh_nodes_lambda"]))
            wl = wl / wl.sum()
            # Mean-preserving log-normal: E[lambda] stays at the point estimate.
            mu_h, mu_a = math.log(lh) - 0.5 * var_h, math.log(la) - 0.5 * var_a
            n = self._grid_size(math.exp(mu_h + zl.max() * sh) * 1.0 + math.exp(mu_a + zl.max() * sa))

            acc, acc2, wsum = np.zeros(3), np.zeros(3), 0.0
            for i, zi in enumerate(zl):
                Lh = math.exp(mu_h + zi * sh)
                for j, zj in enumerate(zl):
                    La = math.exp(mu_a + zj * sa)
                    for rho, wr in zip(rp["rho_nodes"], rp["rho_weights"]):
                        w = wl[i] * wl[j] * wr
                        o = np.array(self._outcomes(self._score_matrix(Lh, La, rho, n)))
                        dc = np.array([o[0] + o[1], o[1] + o[2], o[0] + o[2]])  # 1X, X2, 12
                        acc += w * o
                        acc2 += w * dc ** 2
                        wsum += w
            mean = acc / wsum
            p_h, p_d, p_a = (float(x) for x in mean / mean.sum())
            dc_mean = np.array([p_h + p_d, p_d + p_a, p_h + p_a])
            dc_sd = np.sqrt(np.maximum(acc2 / wsum - dc_mean ** 2, 0.0))

            point = self._score_matrix(lh, la, rp["rho_mean"], n)
            diag = {
                "lambda_home": lh, "lambda_away": la, "rate_source": source,
                "pace_factor": pi, "rest_factor_home": fh, "rest_factor_away": fa,
                "log_sd_home": sh, "log_sd_away": sa, "n_eff": n_eff,
                "rho_mean": rp["rho_mean"], "rho_nodes": rp["rho_nodes"],
                "draw_target_mean": rp["draw_target_mean"],
                "p_draw_independent_anchor": rp["p_draw_independent"],
                "p_home": p_h, "p_draw": p_d, "p_away": p_a,
                "p_1X": float(dc_mean[0]), "p_X2": float(dc_mean[1]), "p_12": float(dc_mean[2]),
                "sd_1X": float(dc_sd[0]), "sd_X2": float(dc_sd[1]), "sd_12": float(dc_sd[2]),
                "score_matrix_point": point,
            }
            return diag
        finally:
            if saved is not None:
                self.config = saved
                self._rho_cache = None

    # ------------------------------------------------------------------ public API
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        diag = self._posterior(ctx)
        self.last_diagnostics = diag
        p_h, p_d, p_a = diag["p_home"], diag["p_draw"], diag["p_away"]
        try:
            return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)
        except (AttributeError, TypeError):
            return DiscreteDistribution(probabilities={"home": p_h, "draw": p_d, "away": p_a})

    def evaluate_selection(self, game_context: Dict[str, Any], selection: str,
                           decimal_odds: float) -> Dict[str, Any]:
        """Price a double-chance selection ('1X', 'X2', '12') against decimal odds."""
        diag = self._posterior(game_context or {})
        self.last_diagnostics = diag
        sel = selection.upper().replace(" ", "").replace("/", "")
        alias = {"1X": "1X", "X1": "1X", "HOMEDRAW": "1X", "X2": "X2", "2X": "X2",
                 "AWAYDRAW": "X2", "12": "12", "21": "12", "HOMEAWAY": "12"}
        key = alias.get(sel)
        if key is None:
            raise ValueError(f"Unknown double_chance selection: {selection}")

        p, sd = diag[f"p_{key}"], diag[f"sd_{key}"]
        o = float(decimal_odds)
        ev = p * o - 1.0
        ev_sd = o * sd
        z = float(self.config["uncertainty_z"])
        eligible = (o >= float(self.config["min_odds"])) and ev > 0.0 and ev > z * ev_sd
        p_lb = max(p - z * sd, 0.0)
        kelly = max((p_lb * o - 1.0) / (o - 1.0), 0.0) if (eligible and o > 1.0) else 0.0
        return {
            "selection": key, "probability": p, "probability_sd": sd,
            "fair_odds": 1.0 / p if p > 0 else float("inf"),
            "decimal_odds": o, "expected_value": ev, "ev_sd": ev_sd,
            "eligible": bool(eligible), "kelly_fraction_conservative": kelly,
        }

    def should_bet(self, game_context: Dict[str, Any], selection: str,
                   decimal_odds: float) -> bool:
        return self.evaluate_selection(game_context, selection, decimal_odds)["eligible"]