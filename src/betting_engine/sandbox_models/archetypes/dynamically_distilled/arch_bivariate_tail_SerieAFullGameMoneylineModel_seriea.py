import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - resolved by execution scope
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded primitive


@dataclass(frozen=True)
class SerieADomainPriors:
    """
    Domain priors for Serie A full-game scoring. Every value is a structural
    prior (an interpretable physical quantity), not a clamp on outputs.

    league_home_goals / league_away_goals:
        Empirical baseline means (1.60 / 1.20) for the cold-start market.
    dispersion_index:
        Observed Var/Mean of goals (1.0, so Poisson marginals are used).
    supremacy_prior_sd:
        Cross-sectional SD of log-supremacy across Serie A matchups
        (the spread of team strength gaps). It sets the shrinkage strength.
    effective_matches:
        Effective sample behind the inputs. It sets the noise variance of the
        log-rate estimates, so home/away gaps are shrunk by their uncertainty.
    residual_rating_weight:
        Share of attack/defense information not already contained in
        'home_expected'/'away_expected' when both are supplied. The value
        prevents double counting.
    dc_rho:
        Dixon-Coles low-score dependence. Its negative sign inflates 0-0 and
        1-1, which matches the structurally elevated Serie A draw mass.
    rest_beta / rest_tau / rest_reference:
        Saturating recovery curve f(r) = beta * (1 - exp(-r / tau)), anchored
        at the weekly cycle.
    wind_threshold / wind_scale / wind_gamma:
        Smooth (softplus) tempo suppression from wind.
    heat_threshold / heat_scale / heat_gamma:
        Smooth tempo suppression from heat.
    pace_elasticity:
        Elasticity of total goals with respect to relative tempo.
    tail_eps:
        Score-lattice truncation mass. The lattice size is derived from the rates.
    min_decimal_odds:
        Eligibility floor (1.40) from the mandate.
    """

    league_home_goals: float = 1.60
    league_away_goals: float = 1.20
    dispersion_index: float = 1.000
    supremacy_prior_sd: float = 0.30
    effective_matches: float = 38.0
    residual_rating_weight: float = 0.25
    dc_rho: float = -0.08
    rest_beta: float = 0.06
    rest_tau: float = 3.0
    rest_reference: float = 7.0
    wind_threshold: float = 8.0
    wind_scale: float = 2.0
    wind_gamma: float = 0.015
    heat_threshold: float = 28.0
    heat_scale: float = 2.0
    heat_gamma: float = 0.010
    pace_elasticity: float = 0.5
    tail_eps: float = 1e-12
    min_decimal_odds: float = 1.40


class SerieAFullGameMoneylineModel(BaseCandidateModel):
    """
    SERIEA | full_game | moneyline (3-way: home / draw / away).

    Architecture (first principles):
      1. Rate formation in log space. The empirical anchors (1.60 / 1.20) are
         adjusted by situational inputs. The model takes 'home_expected' and
         'away_expected' when they are supplied. Without them, it builds rates
         multiplicatively from the anchors and the attack/defense ratings.
      2. Total/supremacy decomposition. The log-rates are split into a level
         t = (lh + la) / 2 and a supremacy s = (lh - la) / 2. Tempo, pace and
         weather act on t only. Rest asymmetry acts on s only, so total goals
         are preserved.
      3. Home over-confidence control. Supremacy is shrunk toward the league
         home-advantage prior s0 = 0.5 * log(1.6 / 1.2). The empirical-Bayes
         weight is w = sd0^2 / (sd0^2 + noise^2). The noise variance is the
         Poisson information of the inputs. This shrinkage is precision-based,
         not a clamp.
      4. Scoring structure. Marginals are Poisson (dispersion index 1.0).
         A Dixon-Coles low-score correction is applied, and rho is mapped
         smoothly into its admissible region via a tanh projection.
      5. Outcome mass. The model integrates the lattice into P(H), P(D), P(A)
         with no bookmaker information. The pricing helpers support continuous
         (Polymarket-style) prices.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_full_game_1108_074825",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "full_game",
        family: str = "moneyline",
        priors: Optional[SerieADomainPriors] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family
        self.priors = priors or SerieADomainPriors()
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for key in keys:
            node: Any = ctx
            for part in key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    node = None
                    break
            if node is None or isinstance(node, bool):
                continue
            try:
                val = float(node)
            except (TypeError, ValueError):
                continue
            if math.isfinite(val):
                return val
        return None

    @staticmethod
    def _to_log_strength(v: Optional[float]) -> float:
        """
        Map a rating to a log-multiplicative effect.

        Positive ratings near 1 are treated as multipliers (Dixon-Coles
        convention). Small signed values are treated as log-scale effects.
        """
        if v is None:
            return 0.0
        if 0.25 < v < 4.0:
            return math.log(v)
        if abs(v) <= 0.25:
            return v
        return math.copysign(math.log1p(abs(v)), v)

    def _ratings(self, ctx: Dict[str, Any], side: str) -> Tuple[float, float]:
        att = self._num(ctx, f"{side}.attack", f"{side}_attack", f"{side}_attack_rating")
        dfn = self._num(ctx, f"{side}.defense", f"{side}_defense", f"{side}_defense_rating")
        # 'defense' is interpreted as a conceding multiplier (>1 = leakier).
        return self._to_log_strength(att), self._to_log_strength(dfn)

    # ------------------------------------------------------------------ #
    # Rate construction
    # ------------------------------------------------------------------ #
    def _rest_curve(self, r: Optional[float]) -> float:
        p = self.priors
        r = p.rest_reference if r is None else max(r, 0.0)
        return p.rest_beta * (1.0 - math.exp(-r / p.rest_tau))

    @staticmethod
    def _softplus(x: float) -> float:
        return math.log1p(math.exp(-abs(x))) + max(x, 0.0)

    def _rates(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        p = self.priors
        h_exp = self._num(ctx, "home_expected", "home_xg", "lambda_home")
        a_exp = self._num(ctx, "away_expected", "away_xg", "lambda_away")
        supplied = h_exp is not None and h_exp > 0 and a_exp is not None and a_exp > 0

        lh = math.log(h_exp if supplied else p.league_home_goals)
        la = math.log(a_exp if supplied else p.league_away_goals)

        # Attack/defense: full weight on cold build, residual weight otherwise.
        w_r = p.residual_rating_weight if supplied else 1.0
        ah, dh = self._ratings(ctx, "home")
        aa, da = self._ratings(ctx, "away")
        lh += w_r * (ah + da)
        la += w_r * (aa + dh)

        # Decompose into level and supremacy.
        t = 0.5 * (lh + la)
        s = 0.5 * (lh - la)

        # Tempo / pace act on the level only.
        pace = self._num(ctx, "pace", "tempo", "pace_factor", "tempo_factor")
        if pace is not None and pace > 0:
            t += p.pace_elasticity * math.log(pace)

        is_dome = bool(ctx.get("is_dome") or (ctx.get("weather") or {}).get("is_dome", False))
        if not is_dome:
            wind = self._num(ctx, "wind_speed", "weather.wind_speed")
            if wind is not None:
                t -= p.wind_gamma * p.wind_scale * self._softplus((wind - p.wind_threshold) / p.wind_scale)
            temp = self._num(ctx, "temp", "temperature", "weather.temp")
            if temp is not None:
                t -= p.heat_gamma * p.heat_scale * self._softplus((temp - p.heat_threshold) / p.heat_scale)

        # Rest asymmetry acts on supremacy only, so the total is preserved.
        rh = self._num(ctx, "home_rest_days")
        ra = self._num(ctx, "away_rest_days")
        rest_shift = 0.5 * (self._rest_curve(rh) - self._rest_curve(ra))
        s += rest_shift

        # Empirical-Bayes shrinkage of supremacy toward league home advantage.
        s0 = 0.5 * math.log(p.league_home_goals / p.league_away_goals)
        n_eff = self._num(ctx, "sample_size", "n_matches", "effective_matches")
        n_eff = n_eff if (n_eff is not None and n_eff > 0) else p.effective_matches
        lam_h_raw, lam_a_raw = math.exp(t + s), math.exp(t - s)
        noise_var = (1.0 / lam_h_raw + 1.0 / lam_a_raw) / (4.0 * n_eff)
        prior_var = p.supremacy_prior_sd ** 2
        w_s = prior_var / (prior_var + noise_var)
        s_post = s0 + w_s * (s - s0)

        lam_h, lam_a = math.exp(t + s_post), math.exp(t - s_post)
        diag = {
            "inputs_supplied": float(supplied),
            "rest_shift": rest_shift,
            "supremacy_raw": s,
            "supremacy_post": s_post,
            "shrink_weight": w_s,
            "level": t,
        }
        return lam_h, lam_a, diag

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    def _admissible_rho(self, lam_h: float, lam_a: float) -> float:
        rho0 = self.priors.dc_rho
        if rho0 < 0:
            bound = -1.0 / max(lam_h, lam_a)  # tau(0,1), tau(1,0) >= 0
            return bound * math.tanh(rho0 / bound)
        bound = min(1.0, 1.0 / (lam_h * lam_a))  # tau(0,0), tau(1,1) >= 0
        return bound * math.tanh(rho0 / bound) if rho0 > 0 else 0.0

    def score_matrix(self, lam_h: float, lam_a: float) -> np.ndarray:
        eps = self.priors.tail_eps
        n = int(max(poisson.ppf(1.0 - eps, lam_h), poisson.ppf(1.0 - eps, lam_a))) + 2
        goals = np.arange(n + 1)
        ph, pa = poisson.pmf(goals, lam_h), poisson.pmf(goals, lam_a)
        m = np.outer(ph, pa)
        rho = self._admissible_rho(lam_h, lam_a)
        m[0, 0] *= 1.0 - lam_h * lam_a * rho
        m[0, 1] *= 1.0 + lam_h * rho
        m[1, 0] *= 1.0 + lam_a * rho
        m[1, 1] *= 1.0 - rho
        m = np.clip(m, 0.0, None)
        self.last_diagnostics["dc_rho_eff"] = rho
        return m / m.sum()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a, diag = self._rates(ctx)
        self.last_diagnostics = {"lambda_home": lam_h, "lambda_away": lam_a, **diag}
        m = self.score_matrix(lam_h, lam_a)

        p_home = float(np.tril(m, -1).sum())
        p_draw = float(np.trace(m))
        p_away = float(np.triu(m, 1).sum())
        z = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / z, p_draw / z, p_away / z

        self.last_diagnostics.update({"p_home": p_home, "p_draw": p_draw, "p_away": p_away})
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})

    # ------------------------------------------------------------------ #
    # Continuous pricing helpers (Polymarket-style prices or decimal odds)
    # ------------------------------------------------------------------ #
    @staticmethod
    def price_to_decimal(price: float, fee: float = 0.0) -> float:
        """Convert a binary-contract price in (0, 1) to effective decimal odds net of a proportional fee."""
        if not (0.0 < price < 1.0):
            return float("nan")
        return 1.0 + (1.0 - price) * (1.0 - fee) / price

    def evaluate_price(
        self,
        model_prob: float,
        decimal_odds: Optional[float] = None,
        price: Optional[float] = None,
        fee: float = 0.0,
    ) -> Dict[str, float]:
        odds = decimal_odds if decimal_odds is not None else (
            self.price_to_decimal(price, fee) if price is not None else float("nan")
        )
        if not math.isfinite(odds) or odds <= 1.0:
            return {"decimal_odds": odds, "ev": float("nan"), "kelly": 0.0, "eligible": 0.0}
        ev = model_prob * odds - 1.0
        b = odds - 1.0
        kelly = max((model_prob * b - (1.0 - model_prob)) / b, 0.0)
        eligible = float(odds >= self.priors.min_decimal_odds and ev > 0.0)
        return {"decimal_odds": odds, "ev": ev, "kelly": kelly, "eligible": eligible}