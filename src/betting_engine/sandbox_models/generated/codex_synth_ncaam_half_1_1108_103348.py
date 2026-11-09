import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded in execution scope


class NCAAMHalf1DoubleChanceModel(BaseCandidateModel):
    """
    Possession-level compound lattice model for NCAAM first-half double chance.

    Structure
    ---------
    D = sum_{i=1}^{N} (X_i^H - X_i^A)

    * N        : first-half possession pairs, discretised Gaussian (possession counts
                 are under-dispersed vs Poisson because both teams alternate).
                 Mean is a precision-weighted blend of a tempo-implied estimate and a
                 points-implied estimate.
    * X^H, X^A : per-possession points on {0,1,2,3}. The shape is the maximum-entropy
                 (exponential-tilt) deformation of a league possession-outcome prior,
                 tilted so that E[N] * E[X] equals the team's expected first-half points.
    * Margin   : exact lattice pmf by FFT inversion of G_N(phi_Y(t)).
    * Home edge: the raw expected margin is shrunk (normal-normal, James-Stein style)
                 toward a half-game home-court prior, so full-game HCA is not
                 imported wholesale into a 20-minute half.
    * Buzzer   : "hold for last shot" correction moves mass from |D|=1 to 0 and |D|=2.
                 Its size is a Gamma-Poisson posterior on the ratio of observed to
                 model-predicted ties, updated online via `observe_result`.

    Output is the 3-way (home / draw / away) halftime distribution; double-chance
    prices are P(home)+P(draw) and P(away)+P(draw).
    """

    # ---------- domain priors (named, documented; not static output clamps) ----------
    # League first-half scoring anchors (empirical segment means).
    PRIOR_HOME_HALF_PTS = 40.0
    PRIOR_AWAY_HALF_PTS = 37.5
    # Full-game NCAAM adjusted tempo (possessions / 40 min) and its half share.
    PRIOR_LEAGUE_TEMPO = 68.0
    # Between-game sd of half possession counts (pairs).
    PRIOR_POSS_SD = 2.6
    # League possession-outcome shape for {0,1,2,3} points (FT trips, 2s, 3s).
    PRIOR_POSS_SHAPE = np.array([0.52, 0.05, 0.30, 0.13])
    # Full-game NCAAM home-court advantage (points); half share by time fraction.
    PRIOR_FULL_GAME_HCA = 3.2
    # Cross-matchup variance of true half margins and input-forecast noise variance.
    PRIOR_TRUE_MARGIN_VAR = 30.0
    PRIOR_INPUT_NOISE_VAR = 6.0
    # Rest effect: max relative efficiency swing and day scale of saturation.
    PRIOR_REST_EFFECT = 0.012
    PRIOR_REST_SCALE = 2.0
    # Last-possession tie inflation prior: ratio of real to modelled ties, and strength.
    PRIOR_TIE_RATIO = 1.10
    PRIOR_TIE_STRENGTH = 40.0
    # Share of |D|=1 mass displaced by the buzzer effect that lands on 0 (rest -> |D|=2).
    PRIOR_TIE_SPLIT = 0.6

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_half_1_1108_103348",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "half_1",
        family: str = "double_chance",
    ) -> None:
        super().__init__(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )
        # Gamma-Poisson state for the tie ratio: shape a, rate b (mean a/b).
        self._tie_a = self.PRIOR_TIE_RATIO * self.PRIOR_TIE_STRENGTH
        self._tie_b = self.PRIOR_TIE_STRENGTH

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _side(self, ctx: Dict[str, Any], side: str, *keys: str) -> Optional[float]:
        for k in keys:
            v = self._num(ctx.get(f"{side}_{k}"))
            if v is not None:
                return v
        sub = ctx.get(side)
        if isinstance(sub, dict):
            for k in keys:
                v = self._num(sub.get(k))
                if v is not None:
                    return v
        return None

    def _is_neutral(self, ctx: Dict[str, Any]) -> bool:
        for key in ("neutral_site", "is_neutral", "neutral"):
            if bool(ctx.get(key)):
                return True
        venue = ctx.get("venue")
        if isinstance(venue, dict):
            return bool(venue.get("neutral") or venue.get("neutral_site"))
        if isinstance(venue, str):
            return "neutral" in venue.lower()
        return False

    # ------------------------------------------------------- expected scoring
    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        he = self._num(ctx.get("home_expected"))
        ae = self._num(ctx.get("away_expected"))

        if he is None or ae is None:
            # Build from attack x opponent-defense exposure (ratings ~1.0 = league avg).
            ha = self._side(ctx, "home", "attack", "offense", "off_rating") or 1.0
            hd = self._side(ctx, "home", "defense", "def_rating") or 1.0
            aa = self._side(ctx, "away", "attack", "offense", "off_rating") or 1.0
            ad = self._side(ctx, "away", "defense", "def_rating") or 1.0
            if he is None:
                he = self.PRIOR_HOME_HALF_PTS * max(ha, 1e-6) * max(ad, 1e-6)
            if ae is None:
                ae = self.PRIOR_AWAY_HALF_PTS * max(aa, 1e-6) * max(hd, 1e-6)

        # Full-game inputs passed in by mistake are detected by scale and halved.
        if he + ae > 1.5 * (self.PRIOR_HOME_HALF_PTS + self.PRIOR_AWAY_HALF_PTS):
            he, ae = 0.5 * he, 0.5 * ae

        # Rest differential: smooth saturating efficiency transfer.
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            s = self.PRIOR_REST_EFFECT * math.tanh((rh - ra) / self.PRIOR_REST_SCALE)
            he *= 1.0 + s
            ae *= 1.0 - s

        # Shrink the margin toward a half-game home-court prior; preserve the total.
        total = he + ae
        mu_obs = he - ae
        mu_prior = 0.0 if self._is_neutral(ctx) else 0.5 * self.PRIOR_FULL_GAME_HCA
        w = self.PRIOR_TRUE_MARGIN_VAR / (self.PRIOR_TRUE_MARGIN_VAR + self.PRIOR_INPUT_NOISE_VAR)
        mu = w * mu_obs + (1.0 - w) * mu_prior
        he = 0.5 * (total + mu)
        ae = 0.5 * (total - mu)
        eps = 1e-3
        return max(he, eps), max(ae, eps)

    # ------------------------------------------------------------ possessions
    def _possession_moments(self, ctx: Dict[str, Any], total_pts: float) -> Tuple[float, float]:
        league_tempo = self._num(ctx.get("league_tempo")) or self.PRIOR_LEAGUE_TEMPO
        th = self._side(ctx, "home", "tempo", "pace")
        ta = self._side(ctx, "away", "tempo", "pace")

        baseline_ppp = float(np.dot(np.arange(4), self.PRIOR_POSS_SHAPE))
        n_points = total_pts / (2.0 * baseline_ppp)          # points-implied pairs
        var_points = (0.15 * n_points) ** 2                  # ppp uncertainty propagated

        if th is not None and ta is not None and th > 0 and ta > 0:
            n_tempo = 0.5 * th * ta / league_tempo
            var_tempo = self.PRIOR_POSS_SD ** 2
            wt = (1.0 / var_tempo) / (1.0 / var_tempo + 1.0 / var_points)
            n_mean = wt * n_tempo + (1.0 - wt) * n_points
        else:
            n_mean = n_points
        return max(n_mean, 1.0), self.PRIOR_POSS_SD

    # ---------------------------------------------------- per-possession pmf
    def _tilted_shape(self, target_ppp: float) -> np.ndarray:
        k = np.arange(4, dtype=float)
        base = self.PRIOR_POSS_SHAPE / self.PRIOR_POSS_SHAPE.sum()
        # Numerical feasibility guard only: the mean of a {0..3} variable lies in (0,3).
        target = min(max(target_ppp, 1e-4), 3.0 - 1e-4)

        def mean_gap(theta: float) -> float:
            w = base * np.exp(theta * k - np.max(theta * k))
            return float(np.dot(k, w) / w.sum()) - target

        theta = brentq(mean_gap, -40.0, 40.0, xtol=1e-12)
        w = base * np.exp(theta * k - np.max(theta * k))
        return w / w.sum()

    # --------------------------------------------------------- margin lattice
    def _margin_pmf(self, ph: np.ndarray, pa: np.ndarray, n_mean: float, n_sd: float):
        # Pair difference Y = X^H - X^A on {-3..3}.
        py = np.convolve(ph, pa[::-1])  # index j -> y = j - 3

        n_max = int(math.ceil(n_mean + 7.0 * n_sd)) + 1
        n = np.arange(0, n_max + 1)
        z_hi = (n + 0.5 - n_mean) / n_sd
        z_lo = (n - 0.5 - n_mean) / n_sd
        from scipy.special import ndtr
        pn = ndtr(z_hi) - ndtr(z_lo)
        pn = pn / pn.sum()

        L = 1
        while L < 6 * n_max + 8:
            L <<= 1
        t = 2.0 * np.pi * np.arange(L) / L
        y = np.arange(-3, 4)
        phi = (py[None, :] * np.exp(-1j * np.outer(t, y))).sum(axis=1)

        G = np.zeros(L, dtype=complex)
        power = np.ones(L, dtype=complex)
        for prob in pn:
            G += prob * power
            power *= phi
        pmf_wrapped = np.fft.ifft(G).real
        pmf_wrapped = np.clip(pmf_wrapped, 0.0, None)  # remove FFT round-off negatives
        half = L // 2
        d = np.arange(-half, half)
        pmf = np.concatenate([pmf_wrapped[half:], pmf_wrapped[:half]])
        pmf /= pmf.sum()
        return d, pmf

    # ------------------------------------------------- last-possession effect
    def _tie_ratio(self) -> float:
        return self._tie_a / self._tie_b

    def _apply_buzzer_correction(self, d: np.ndarray, pmf: np.ndarray) -> np.ndarray:
        out = pmf.copy()
        idx = {int(v): i for i, v in enumerate(d)}
        i0, ip1, im1, ip2, im2 = idx[0], idx[1], idx[-1], idx[2], idx[-2]
        base_tie = out[i0]
        needed = max(self._tie_ratio() - 1.0, 0.0) * base_tie  # extra tie mass
        if needed <= 0.0:
            return out
        avail = out[ip1] + out[im1]
        split = self.PRIOR_TIE_SPLIT
        # Total displaced from |D|=1, saturating smoothly as it approaches availability.
        want = needed / split
        moved = avail * (1.0 - math.exp(-want / max(avail, 1e-12)))
        frac_p = out[ip1] / avail if avail > 0 else 0.5
        mp, mm = moved * frac_p, moved * (1.0 - frac_p)
        out[ip1] -= mp
        out[im1] -= mm
        out[i0] += split * moved
        out[ip2] += (1.0 - split) * mp
        out[im2] += (1.0 - split) * mm
        return out / out.sum()

    # ------------------------------------------------------------ core engine
    def _compute(self, ctx: Dict[str, Any], correct: bool = True):
        he, ae = self._expected_points(ctx)
        n_mean, n_sd = self._possession_moments(ctx, he + ae)
        ph = self._tilted_shape(he / n_mean)
        pa = self._tilted_shape(ae / n_mean)
        d, pmf = self._margin_pmf(ph, pa, n_mean, n_sd)
        if correct:
            pmf = self._apply_buzzer_correction(d, pmf)
        return d, pmf

    # --------------------------------------------------------------- public
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        d, pmf = self._compute(ctx, correct=True)
        p_home = float(pmf[d > 0].sum())
        p_draw = float(pmf[d == 0].sum())
        p_away = float(pmf[d < 0].sum())
        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s
        return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)

    def double_chance_probabilities(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        d, pmf = self._compute(game_context or {}, correct=True)
        p_home = float(pmf[d > 0].sum())
        p_draw = float(pmf[d == 0].sum())
        p_away = float(pmf[d < 0].sum())
        return {
            "home_or_draw": p_home + p_draw,
            "away_or_draw": p_away + p_draw,
            "home_or_away": p_home + p_away,
        }

    def margin_moments(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        d, pmf = self._compute(game_context or {}, correct=True)
        mu = float(np.dot(d, pmf))
        var = float(np.dot((d - mu) ** 2, pmf))
        return {"mean": mu, "sd": math.sqrt(max(var, 0.0)), "p_tie": float(pmf[d == 0].sum())}

    def observe_result(
        self, game_context: Dict[str, Any], home_half_points: float, away_half_points: float
    ) -> None:
        """Online Gamma-Poisson update of the tie-inflation ratio from settled halves."""
        _, pmf_base = self._compute(game_context or {}, correct=False)
        d, _ = self._compute(game_context or {}, correct=False)
        expected_tie = float(pmf_base[d == 0].sum())
        tie = 1.0 if int(round(home_half_points)) == int(round(away_half_points)) else 0.0
        self._tie_a += tie
        self._tie_b += expected_tie

    def predict(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        return self.predict_distribution(game_context)


CandidateModel = NCAAMHalf1DoubleChanceModel