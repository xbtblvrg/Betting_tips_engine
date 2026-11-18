import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import special, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - primitives are normally pre-loaded in scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    DiscreteDistribution = globals().get("DiscreteDistribution")


class UELHalf1BothScoreModel(BaseCandidateModel):
    """
    First-half Both-Teams-To-Score model for the UEFA Europa League.

    Structure (derived from first-half scoring mechanics):

    1. Intensities. A first half is ~45 minutes of a 90+ minute match. Goal timing in
       European club football is back-loaded (fatigue, game-state chasing, substitutions),
       so first-half goal share is below 0.5. The anchors (home 0.80, away 0.60) are
       treated as first-half means. If the context supplies full-match expectations
       (detected by their magnitude relative to the anchors), they are mapped onto the
       half via the share implied by the anchors themselves.

    2. Situational scaling. This is log-linear and multiplicative: attack x opponent defence,
       tempo/pace, a rest differential via a saturating tanh, and weather drag. Each
       effect enters as a log-ratio against its neutral value, so a missing feature
       contributes exactly zero.

    3. Credibility shrinkage. Context expectations are shrunk toward the empirical
       anchors in log space. The weight comes from a gamma-Poisson conjugate update:
       the prior is the anchor, the pseudo-exposure is the anchor's information content,
       and the context is treated as one equivalent observation.

    4. Shared tempo frailty. Both teams' intensities share a lognormal match-tempo
       factor, integrated by Gauss-Hermite quadrature. This induces the positive
       goal correlation seen in open matches and makes the marginals mildly
       overdispersed. The frailty variance is set so the implied total-goals
       dispersion index matches the observed index (1.000 => near-Poisson), plus the
       irreducible uncertainty from the situational inputs.

    5. Low-score dependence. A Dixon-Coles tau correction is applied on {0,1}x{0,1}.
       Its rho is bounded analytically by the intensities, so the matrix stays a
       valid probability measure without arbitrary clamps.

    BTTS(yes) = 1 - P(H=0) - P(A=0) + P(H=0, A=0), read off the score lattice.
    """

    # Empirical domain anchors (first-half means for UEL)
    ANCHOR_HOME = 0.80
    ANCHOR_AWAY = 0.60
    OBSERVED_DISPERSION_INDEX = 1.000

    # Structural priors (domain mechanics, not clamps)
    FIRST_HALF_GOAL_SHARE = 0.445        # back-loaded goal timing in UEFA club football
    LOW_SCORE_DEPENDENCE = -0.06         # mild excess of 0-0 / 1-1 in first halves
    REST_SCALE_DAYS = 3.0                # rest effect saturation scale
    REST_MAX_LOG_EFFECT = 0.05           # asymptotic log-intensity gain from rest edge
    WIND_DRAG_PER_MS = 0.004             # log-intensity drag per m/s above calm
    TEMP_COMFORT_C = 15.0
    TEMP_DRAG_PER_C2 = 0.00008
    INPUT_UNCERTAINTY_SD = 0.10          # log-sd of situational-input error
    MIN_ODDS = 1.40
    TAIL_TOL = 1e-10
    HERMITE_NODES = 15

    def __init__(
        self,
        model_id: str = "codex_synth_uel_half_1_1108_151508",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "half_1",
        family: str = "both_score",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs
            )
        except TypeError:
            try:
                super().__init__()
            except Exception:
                pass
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self._gh_x, self._gh_w = np.polynomial.hermite.hermgauss(self.HERMITE_NODES)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _f(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            node: Any = ctx
            ok = True
            for part in k.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if ok:
                v = self._f(node)
                if v is not None:
                    return v
        return None

    @staticmethod
    def _log_ratio(x: Optional[float], neutral: float) -> float:
        if x is None or x <= 0 or neutral <= 0:
            return 0.0
        return math.log(x / neutral)

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        return self._get(
            ctx,
            f"{side}.{kind}",
            f"{side}_{kind}",
            f"{side}.{kind}_strength",
            f"{side}_{kind}_strength",
        )

    def _rating_neutral(self, ctx: Dict[str, Any], kind: str) -> float:
        # Ratings may be multiplicative (~1.0) or centred differences (~0.0).
        vals = [self._rating(ctx, s, kind) for s in ("home", "away")]
        vals = [v for v in vals if v is not None]
        if not vals:
            return 1.0
        return 1.0 if all(v > 0 for v in vals) else 0.0

    def _rating_log_effect(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        v = self._rating(ctx, side, kind)
        if v is None:
            return 0.0
        neutral = self._rating_neutral(ctx, kind)
        if neutral == 1.0:
            return self._log_ratio(v, 1.0)
        # Additive rating: treat as an approximate log-intensity deviation
        return float(np.tanh(v))

    # ------------------------------------------------------------ intensities
    def _base_intensities(self, ctx: Dict[str, Any]) -> Tuple[float, float, float]:
        h = self._get(ctx, "home_expected", "home.expected", "home_xg", "home.xg")
        a = self._get(ctx, "away_expected", "away.expected", "away_xg", "away.xg")
        anchor_total = self.ANCHOR_HOME + self.ANCHOR_AWAY

        # Detect full-match supply: if the supplied total is closer (in log space) to the
        # full-match equivalent of the anchors than to the anchors themselves, map it.
        share = self.FIRST_HALF_GOAL_SHARE
        if h is not None and a is not None and h > 0 and a > 0:
            tot = h + a
            full_equiv = anchor_total / share
            if abs(math.log(tot / full_equiv)) < abs(math.log(tot / anchor_total)):
                h, a = h * share, a * share

        # Gamma-Poisson credibility: prior shape = anchor information (sum of anchor
        # means = expected first-half goal count, i.e. pseudo-events); the context
        # contributes one match-equivalent of information scaled by its own magnitude.
        def shrink(x: Optional[float], anchor: float) -> Tuple[float, float]:
            if x is None or x <= 0:
                return anchor, 0.0
            prior_info = anchor_total
            ctx_info = x + anchor  # information from one equivalent observation
            w = ctx_info / (ctx_info + prior_info)
            return math.exp(w * math.log(x) + (1.0 - w) * math.log(anchor)), w

        lh, wh = shrink(h, self.ANCHOR_HOME)
        la, wa = shrink(a, self.ANCHOR_AWAY)
        return lh, la, 0.5 * (wh + wa)

    def _situational_log_effects(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        eh = 0.0
        ea = 0.0

        # Attack x opponent defence (defence rating > neutral = concedes more)
        eh += 0.5 * self._rating_log_effect(ctx, "home", "attack")
        eh += 0.5 * self._rating_log_effect(ctx, "away", "defense")
        ea += 0.5 * self._rating_log_effect(ctx, "away", "attack")
        ea += 0.5 * self._rating_log_effect(ctx, "home", "defense")

        # Tempo / pace: common multiplicative factor, first half scales with sqrt
        # (opening phases are more cagey, so pace transmits sub-linearly).
        pace = self._get(ctx, "pace", "tempo", "match_pace", "expected_pace")
        pace_ref = self._get(ctx, "league_pace", "pace_baseline")
        if pace is not None and pace > 0:
            ref = pace_ref if (pace_ref is not None and pace_ref > 0) else (
                1.0 if pace < 10 else pace)
            t = 0.5 * self._log_ratio(pace, ref)
            eh += t
            ea += t

        # Rest differential, saturating
        hr = self._get(ctx, "home_rest_days", "home.rest_days")
        ar = self._get(ctx, "away_rest_days", "away.rest_days")
        if hr is not None and ar is not None:
            d = math.tanh((hr - ar) / self.REST_SCALE_DAYS) * self.REST_MAX_LOG_EFFECT
            eh += d
            ea -= d

        # Weather drag (ignored indoors)
        dome = ctx.get("is_dome")
        if not (isinstance(dome, bool) and dome) and not (
            isinstance(ctx.get("weather"), dict) and ctx["weather"].get("is_dome") is True
        ):
            wind = self._get(ctx, "wind_speed", "weather.wind_speed")
            temp = self._get(ctx, "temp", "temperature", "weather.temp")
            drag = 0.0
            if wind is not None and wind > 0:
                drag -= self.WIND_DRAG_PER_MS * wind
            if temp is not None:
                drag -= self.TEMP_DRAG_PER_C2 * (temp - self.TEMP_COMFORT_C) ** 2
            eh += drag
            ea += drag

        return eh, ea

    def _frailty_variance(self, lh: float, la: float, n_features: int) -> float:
        # Total goals T under shared lognormal frailty: Var/Mean ~ 1 + mu*(e^{s2}-1).
        # Match observed dispersion index, then add irreducible input uncertainty.
        mu = lh + la
        excess = max(self.OBSERVED_DISPERSION_INDEX - 1.0, 0.0)
        s2_obs = math.log1p(excess / mu) if mu > 0 else 0.0
        s2_inp = self.INPUT_UNCERTAINTY_SD ** 2 * (1.0 + n_features) / (2.0 + n_features)
        return s2_obs + s2_inp

    # ------------------------------------------------------------ score lattice
    def _grid_size(self, lmax: float) -> int:
        n = int(stats.poisson.ppf(1.0 - self.TAIL_TOL, lmax)) + 2
        return max(n, 4)

    def _score_matrix(self, lh: float, la: float, s2: float) -> np.ndarray:
        s = math.sqrt(s2)
        lmax = max(lh, la) * math.exp(3.0 * s)
        n = self._grid_size(lmax)
        k = np.arange(n)
        mat = np.zeros((n, n))
        norm_w = self._gh_w / math.sqrt(math.pi)
        for x, w in zip(self._gh_x, norm_w):
            # Mean-preserving lognormal frailty
            f = math.exp(math.sqrt(2.0) * s * x - 0.5 * s2)
            ph = stats.poisson.pmf(k, lh * f)
            pa = stats.poisson.pmf(k, la * f)
            mat += w * np.outer(ph, pa)

        # Dixon-Coles low-score correction with analytically feasible rho
        rho = self.LOW_SCORE_DEPENDENCE
        lo = max(-1.0 / lh, -1.0 / la)
        hi = min(1.0 / (lh * la), 1.0)
        rho = min(max(rho, lo * 0.999), hi * 0.999)
        mat[0, 0] *= 1.0 - lh * la * rho
        mat[0, 1] *= 1.0 + lh * rho
        mat[1, 0] *= 1.0 + la * rho
        mat[1, 1] *= 1.0 - rho

        mat = np.clip(mat, 0.0, None)
        return mat / mat.sum()

    # --------------------------------------------------------------- interface
    def predict_distribution(self, game_context: Dict[str, Any]):
        ctx = game_context or {}
        bh, ba, cred = self._base_intensities(ctx)
        eh, ea = self._situational_log_effects(ctx)
        lh = bh * math.exp(eh)
        la = ba * math.exp(ea)

        n_feat = sum(
            1 for key in ("home_rest_days", "away_rest_days", "wind_speed", "temp", "pace")
            if self._get(ctx, key) is not None
        )
        s2 = self._frailty_variance(lh, la, n_feat)
        mat = self._score_matrix(lh, la, s2)

        p_h0 = float(mat[0, :].sum())
        p_a0 = float(mat[:, 0].sum())
        p_00 = float(mat[0, 0])
        p_yes = float(1.0 - p_h0 - p_a0 + p_00)
        p_home = float(np.tril(mat, -1).sum())
        p_away = float(np.triu(mat, 1).sum())
        p_draw = float(np.trace(mat))

        self.last_diagnostics = {
            "lambda_home": lh, "lambda_away": la, "frailty_var": s2,
            "credibility": cred, "p_btts_yes": p_yes, "p_btts_no": 1.0 - p_yes,
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
            "grid": mat.shape[0],
        }

        scores = {
            (int(i), int(j)): float(mat[i, j])
            for i in range(mat.shape[0]) for j in range(mat.shape[1])
            if mat[i, j] > 0.0
        }
        dist = None
        if DiscreteDistribution is not None:
            try:
                dist = DiscreteDistribution.from_scores(scores)
            except Exception:
                try:
                    dist = DiscreteDistribution(
                        probabilities={"yes": p_yes, "no": 1.0 - p_yes})
                except Exception:
                    dist = DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        if dist is not None:
            try:
                setattr(dist, "metadata", dict(self.last_diagnostics))
            except Exception:
                pass
        return dist

    # ---------------------------------------------------------------- pricing
    def both_score_probability(self, game_context: Dict[str, Any]) -> float:
        self.predict_distribution(game_context)
        return float(self.last_diagnostics["p_btts_yes"])

    def fair_price(self, game_context: Dict[str, Any], side: str = "yes") -> Dict[str, float]:
        """Continuous fair pricing (Polymarket-style share price in [0,1] and decimal odds)."""
        p = self.both_score_probability(game_context)
        q = p if str(side).lower() in ("yes", "over", "btts_yes") else 1.0 - p
        return {"probability": q, "share_price": q,
                "decimal_odds": (1.0 / q) if q > 0 else float("inf")}

    def expected_value(self, game_context: Dict[str, Any], decimal_odds: float,
                       side: str = "yes") -> float:
        q = self.fair_price(game_context, side)["probability"]
        return q * decimal_odds - 1.0

    def is_eligible(self, game_context: Dict[str, Any], decimal_odds: float,
                    side: str = "yes") -> bool:
        if decimal_odds is None or decimal_odds < self.MIN_ODDS:
            return False
        return self.expected_value(game_context, decimal_odds, side) > 0.0

    def kelly_fraction(self, game_context: Dict[str, Any], decimal_odds: float,
                       side: str = "yes") -> float:
        q = self.fair_price(game_context, side)["probability"]
        b = decimal_odds - 1.0
        if b <= 0:
            return 0.0
        return max((q * b - (1.0 - q)) / b, 0.0)


CandidateModel = UELHalf1BothScoreModel