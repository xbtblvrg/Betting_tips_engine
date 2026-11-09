import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may be pre-loaded into scope; import defensively.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class BundesligaHalf2BothScoreModel(BaseCandidateModel):
    """
    Second-half Both-Teams-To-Score model for the Bundesliga.

    Structure (first principles)
    ----------------------------
    1. Goals in the second half are a counting process. Conditional on the
       game-level intensity, each side's goals are Poisson:
           X_h | Z ~ Poisson(lambda_h * Z),   X_a | Z ~ Poisson(lambda_a * Z)
    2. Z is a shared game-tempo frailty, Z ~ Gamma(k, 1/k) with E[Z] = 1.
       The shared factor models the empirical reality that second halves are
       either "open" (chasing, substitutions, fatigue) or "managed" for BOTH
       teams together, which induces positive dependence between the sides.
    3. k is not chosen by hand: it is solved from the dispersion index of
       second-half totals, D = Var(T)/E[T].  Under the mixture,
           Var(T) = mu + mu^2 / k   =>   D = 1 + mu / k   =>   k = mu / (D - 1).
       With the observed/prior index D = 1.000 the frailty collapses (k -> inf)
       and the model reduces exactly to independent Poisson. If D is supplied
       in the context (or later estimated), the dependence follows from it.
    4. P(BTTS) = 1 - P(X_h = 0) - P(X_a = 0) + P(X_h = 0, X_a = 0), integrated
       over Z with generalized Gauss-Laguerre quadrature. A full score lattice
       is also built so the engine can settle any derived contract.

    Rates are anchored at the empirical segment means (home 0.80, away 0.60)
    and scaled multiplicatively by situational inputs. When the context
    already supplies 'home_expected' / 'away_expected', those are treated as
    sufficient statistics for team strength (avoiding double counting), and
    only orthogonal situational effects (tempo, rest) are applied on top.
    """

    # ---- Empirical segment anchors (from the forensic autopsy) -------------
    BASE_HOME_RATE = 0.80
    BASE_AWAY_RATE = 0.60
    BASE_DISPERSION_INDEX = 1.000

    # ---- Domain priors (log-scale elasticities, not clamps) ----------------
    # Fatigue acts disproportionately in second halves; elasticity of own
    # scoring and of conceding to the log rest-day differential.
    REST_ELASTICITY_ATTACK = 0.04
    REST_ELASTICITY_DEFENSE = 0.03
    # Numerical tolerance for lattice tail mass (accuracy, not a model clamp).
    TAIL_TOL = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_bundesliga_half_2_1107_175910",
        version: str = "1.0.0",
        league: str = "BUNDESLIGA",
        scope: str = "half_2",
        family: str = "both_score",
    ):
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:  # pragma: no cover - tolerate positional base signatures
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            x = float(value)
            return x if math.isfinite(x) else None
        except (TypeError, ValueError):
            return None

    def _team_feature(self, ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
        nested = ctx.get(side)
        if isinstance(nested, dict):
            v = self._num(nested.get(name))
            if v is not None:
                return v
        return self._num(ctx.get(f"{side}_{name}"))

    @staticmethod
    def _as_multiplier(x: Optional[float]) -> float:
        """Interpret a strength/tempo index as a multiplicative factor.

        Indices centred at 1 (ratio form) are used directly; indices centred
        at 0 (log / z-score form) are exponentiated.
        """
        if x is None:
            return 1.0
        return x if x > 0 else math.exp(x)

    # --------------------------------------------------------- rate building
    def _base_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float, bool]:
        h = self._num(ctx.get("home_expected"))
        a = self._num(ctx.get("away_expected"))
        supplied = h is not None and h > 0 and a is not None and a > 0
        if not supplied:
            h = h if (h is not None and h > 0) else self.BASE_HOME_RATE
            a = a if (a is not None and a > 0) else self.BASE_AWAY_RATE
        return float(h), float(a), supplied

    def _strength_adjust(self, ctx: Dict[str, Any], lam_h: float, lam_a: float) -> Tuple[float, float]:
        h_att = self._as_multiplier(self._team_feature(ctx, "home", "attack"))
        h_def = self._as_multiplier(self._team_feature(ctx, "home", "defense"))
        a_att = self._as_multiplier(self._team_feature(ctx, "away", "attack"))
        a_def = self._as_multiplier(self._team_feature(ctx, "away", "defense"))
        # Multiplicative attack x opponent-defence-exposure structure.
        return lam_h * h_att * a_def, lam_a * a_att * h_def

    def _tempo_adjust(self, ctx: Dict[str, Any], lam_h: float, lam_a: float) -> Tuple[float, float]:
        pace = None
        for key in ("pace", "tempo", "game_pace", "expected_pace"):
            pace = self._num(ctx.get(key))
            if pace is not None:
                break
        m = self._as_multiplier(pace)
        return lam_h * m, lam_a * m

    def _rest_adjust(self, ctx: Dict[str, Any], lam_h: float, lam_a: float) -> Tuple[float, float]:
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None or rh < 0 or ra < 0:
            return lam_h, lam_a
        # log(1 + days) gives diminishing returns to additional rest.
        diff = math.log1p(rh) - math.log1p(ra)  # >0: home fresher
        lam_h *= math.exp(self.REST_ELASTICITY_ATTACK * diff + self.REST_ELASTICITY_DEFENSE * diff)
        lam_a *= math.exp(-self.REST_ELASTICITY_ATTACK * diff - self.REST_ELASTICITY_DEFENSE * diff)
        return lam_h, lam_a

    def _rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        lam_h, lam_a, supplied = self._base_rates(ctx)
        if not supplied:
            lam_h, lam_a = self._strength_adjust(ctx, lam_h, lam_a)
        lam_h, lam_a = self._tempo_adjust(ctx, lam_h, lam_a)
        lam_h, lam_a = self._rest_adjust(ctx, lam_h, lam_a)
        # Weather is intentionally not applied: is_dome/wind are not
        # mechanically material for Bundesliga open-air football at the
        # half-level resolution, and unvalidated effects add variance.
        return max(lam_h, np.finfo(float).tiny), max(lam_a, np.finfo(float).tiny)

    # ------------------------------------------------------- dependence (Z)
    def _frailty_nodes(self, ctx: Dict[str, Any], mu_total: float) -> Tuple[np.ndarray, np.ndarray]:
        d = None
        for key in ("dispersion_index", "half_2_dispersion_index", "total_dispersion_index"):
            d = self._num(ctx.get(key))
            if d is not None:
                break
        if d is None:
            d = self.BASE_DISPERSION_INDEX
        excess = d - 1.0
        if excess <= np.finfo(float).eps:
            # Equidispersed: degenerate frailty at Z = 1 (independent Poisson).
            return np.array([1.0]), np.array([1.0])
        k = mu_total / excess  # moment-matched gamma shape
        # Quadrature order scales with how diffuse the frailty is.
        n_nodes = int(np.clip(np.ceil(8.0 + 24.0 / math.sqrt(k)), 8, 64))
        x, w = special.roots_genlaguerre(n_nodes, k - 1.0)
        w = w / special.gamma(k)
        z = x / k
        w = w / w.sum()
        return z, w

    # --------------------------------------------------------- score lattice
    def _lattice(self, lam_h: float, lam_a: float, z: np.ndarray, w: np.ndarray) -> np.ndarray:
        zmax = float(z.max())
        n_h = int(stats.poisson.isf(self.TAIL_TOL, lam_h * zmax)) + 2
        n_a = int(stats.poisson.isf(self.TAIL_TOL, lam_a * zmax)) + 2
        gh = np.arange(n_h)
        ga = np.arange(n_a)
        mat = np.zeros((n_h, n_a))
        for zi, wi in zip(z, w):
            ph = stats.poisson.pmf(gh, lam_h * zi)
            pa = stats.poisson.pmf(ga, lam_a * zi)
            mat += wi * np.outer(ph, pa)
        s = mat.sum()
        return mat / s if s > 0 else mat

    @staticmethod
    def _btts_closed_form(lam_h: float, lam_a: float, z: np.ndarray, w: np.ndarray) -> float:
        p0h = np.exp(-lam_h * z)
        p0a = np.exp(-lam_a * z)
        p00 = np.exp(-(lam_h + lam_a) * z)
        return float(np.dot(w, 1.0 - p0h - p0a + p00))

    # ------------------------------------------------------------ interface
    def predict_probability(self, game_context: Dict[str, Any]) -> float:
        ctx = game_context or {}
        lam_h, lam_a = self._rates(ctx)
        z, w = self._frailty_nodes(ctx, lam_h + lam_a)
        return self._btts_closed_form(lam_h, lam_a, z, w)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a = self._rates(ctx)
        z, w = self._frailty_nodes(ctx, lam_h + lam_a)
        mat = self._lattice(lam_h, lam_a, z, w)

        scores = {
            (int(i), int(j)): float(mat[i, j])
            for i in range(mat.shape[0])
            for j in range(mat.shape[1])
            if mat[i, j] > 0.0
        }

        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            pass
        try:
            return DiscreteDistribution.from_scores(
                [(h, a, p) for (h, a), p in scores.items()]
            )
        except Exception:
            pass

        p_yes = self._btts_closed_form(lam_h, lam_a, z, w)
        p_yes = float(min(max(p_yes, 0.0), 1.0))
        return DiscreteDistribution(probabilities={"yes": p_yes, "no": 1.0 - p_yes})


# Engine discovery alias
CandidateModel = BundesligaHalf2BothScoreModel