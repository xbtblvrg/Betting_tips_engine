import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - resolved by the execution scope
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")


class NCAAWQuarter3MoneylineModel(BaseCandidateModel):
    """
    NCAAW | quarter_3 | moneyline: compound-possession lattice model.

    Generative structure (built from basketball mechanics, not from a template):

      1. Total quarter possessions T is a renewal count over a 10-minute window.
         Renewal theory gives Var(T) ~= E[T] * cv^2(possession length), so T is
         *under*-dispersed relative to Poisson (cv^2 < 1). T is discretized from a
         Gaussian with that variance.
      2. Possessions alternate, so the team that starts Q3 gets ceil(T/2) and the
         other gets floor(T/2). The starter comes from the context if known.
         Otherwise the model averages over both starters.
      3. Points per possession X in {0,1,2,3} use a categorical law. Its shape
         comes from NCAAW point-source shares (FT / 2PT / 3PT). It is
         exponentially tilted so E[X] equals the team's points-per-possession
         exactly. Clumped scoring (FT trips, and-ones, threes) therefore gives
         per-team Var/Mean ~= E[X^2]/E[X] > 1 automatically.
      4. Team strength enters as a log-share l = log(lambda_H / lambda_A) with the
         quarter total held fixed. l is
           - adjusted by attack/defense exposure and rest differential,
           - shrunk by a halftime-regression factor and a blowout factor g(|D|)
             (starters sit, so the favourite's margin compresses),
           - integrated over cold-start rating uncertainty with Gauss-Hermite
             quadrature. This spreads the margin and tempers favourites.
      5. The margin pmf is the exact convolution S_H(n_H) - S_A(n_A), mixed over
         T, the starter, and the quadrature nodes.
         P(home)=P(M>=1), P(draw)=P(M=0), P(away)=P(M<=-1).

    All structural priors are exposed as constructor arguments, each with a
    domain derivation in the comments below. No probability caps or odds
    filters are applied.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_3_1107_183031",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_3",
        family: str = "moneyline",
        # Empirical anchors (cold-start segment means).
        home_anchor: float = 20.00,
        away_anchor: float = 18.75,
        # NCAAW plays ~68-72 possessions per team per 40 min, so ~17.5 per quarter.
        possessions_per_quarter_prior: float = 17.5,
        # Possession-length coefficient of variation squared. Possessions are
        # roughly gamma-like with cv ~0.5-0.55, giving Var(T) ~= 0.28 * E[T].
        possession_cv2: float = 0.28,
        # Share of points from FT / 2PT / 3PT in NCAAW (~17% / 56% / 27%).
        point_shares: Tuple[float, float, float] = (0.17, 0.56, 0.27),
        # Cold-start error of the quarter mean margin, in points. Full-game
        # projection error ~4.5-5 pts of systematic bias scales ~linearly to a
        # quarter, giving ~1.2.
        rating_sd_points: float = 1.2,
        # Second-half regression of the first-half efficiency gap after
        # halftime adjustments.
        halftime_regression: float = 0.95,
        # Full-game margin scale at which garbage-time rotation materially
        # erodes the favourite's Q3 edge. g(D) = D0 / sqrt(D0^2 + D^2).
        blowout_scale_points: float = 35.0,
        # Quarter-margin value of a saturated rest edge (points), applied
        # through tanh(delta_rest / rest_scale_days).
        rest_effect_points: float = 0.20,
        rest_scale_days: float = 2.0,
        # Partial weight on attack/defense exposure. home_expected and
        # away_expected already embed most matchup info, so this avoids
        # double counting.
        exposure_weight: float = 0.5,
        # Gauss-Hermite nodes used for rating-uncertainty integration.
        quadrature_nodes: int = 7,
        # "three_way" keeps the tie as 'draw' so the engine can price the push.
        # "push_renormalize" redistributes it over the non-push outcomes.
        tie_mode: str = "three_way",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.home_anchor = float(home_anchor)
        self.away_anchor = float(away_anchor)
        self.poss_prior = float(possessions_per_quarter_prior)
        self.poss_cv2 = float(possession_cv2)
        self.point_shares = np.asarray(point_shares, dtype=float)
        self.point_shares = self.point_shares / self.point_shares.sum()
        self.rating_sd_points = float(rating_sd_points)
        self.halftime_regression = float(halftime_regression)
        self.blowout_scale = float(blowout_scale_points)
        self.rest_effect_points = float(rest_effect_points)
        self.rest_scale_days = float(rest_scale_days)
        self.exposure_weight = float(exposure_weight)
        self.tie_mode = tie_mode

        z, w = np.polynomial.hermite_e.hermegauss(int(quadrature_nodes))
        self._gh_nodes = z
        self._gh_weights = w / w.sum()

        # Base categorical shape at the league reference points-per-possession.
        ref_ppp = 0.5 * (self.home_anchor + self.away_anchor) / self.poss_prior
        p123 = ref_ppp * self.point_shares / np.array([1.0, 2.0, 3.0])
        p0 = 1.0 - p123.sum()
        if p0 <= 0.0:
            # Infeasible shares: fall back to a uniform base shape, which the
            # tilt can still bend to any feasible mean.
            self._base_shape = np.full(4, 0.25)
        else:
            self._base_shape = np.concatenate([[p0], p123])
        self._log_base = np.log(self._base_shape)
        self._points = np.arange(4, dtype=float)
        self.last_diagnostics: Dict[str, float] = {}

    # Feature extraction.

    @staticmethod
    def _finite(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._finite(ctx[k])
                if v is not None:
                    return v
        return None

    def _side(self, ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
        v = self._get(ctx, f"{side}_{name}")
        if v is not None:
            return v
        block = ctx.get(side)
        if isinstance(block, dict):
            return self._finite(block.get(name))
        return None

    def _possession_mean(self, ctx: Dict[str, Any]) -> float:
        paces = []
        for key in ("home_pace", "away_pace", "home_tempo", "away_tempo"):
            v = self._get(ctx, key)
            if v is not None and v > 0:
                paces.append(v)
        if not paces:
            v = self._get(ctx, "pace", "tempo", "possessions")
            if v is not None and v > 0:
                paces.append(v)
        if not paces:
            return self.poss_prior
        m = float(np.mean(paces))
        # More than a quarter's worth of possessions implies a per-game figure.
        return m / 4.0 if m > 2.0 * self.poss_prior else m

    # Scoring primitives.

    def _tilted_pmf(self, ppp: float) -> np.ndarray:
        """Exponential tilt of the base shape so that E[X] = ppp exactly."""
        eps = 1e-9
        target = min(max(ppp, eps), 3.0 - eps)  # numerical domain of {0..3}, not a model clamp

        def mean_gap(theta: float) -> float:
            lw = self._log_base + theta * self._points
            lw -= lw.max()
            p = np.exp(lw)
            p /= p.sum()
            return float(p @ self._points) - target

        theta = optimize.brentq(mean_gap, -60.0, 60.0, xtol=1e-12)
        lw = self._log_base + theta * self._points
        lw -= lw.max()
        p = np.exp(lw)
        return p / p.sum()

    def _possession_distribution(self, m: float) -> Tuple[np.ndarray, np.ndarray]:
        mu_t = 2.0 * m
        sd_t = math.sqrt(max(mu_t * self.poss_cv2, 1e-9))
        lo = max(0, int(math.floor(mu_t - 6.0 * sd_t)))
        hi = int(math.ceil(mu_t + 6.0 * sd_t))
        t = np.arange(lo, hi + 1)
        cdf_hi = stats.norm.cdf((t + 0.5 - mu_t) / sd_t)
        cdf_lo = stats.norm.cdf((t - 0.5 - mu_t) / sd_t)
        p = cdf_hi - cdf_lo
        if lo == 0:
            p[0] = cdf_hi[0]
        p = np.clip(p, 0.0, None)
        return t, p / p.sum()

    def _margin_pmf(
        self,
        ppp_h: float,
        ppp_a: float,
        t_support: np.ndarray,
        t_probs: np.ndarray,
        p_home_starts: float,
    ) -> Tuple[np.ndarray, int]:
        xh = self._tilted_pmf(ppp_h)
        xa = self._tilted_pmf(ppp_a)
        nmax = int((t_support.max() + 1) // 2)

        sh = [np.array([1.0])]
        sa = [np.array([1.0])]
        for _ in range(nmax):
            sh.append(np.convolve(sh[-1], xh))
            sa.append(np.convolve(sa[-1], xa))

        offset = 3 * nmax
        out = np.zeros(6 * nmax + 1)
        starters = ((True, p_home_starts), (False, 1.0 - p_home_starts))
        for t, pt in zip(t_support, t_probs):
            if pt <= 0.0:
                continue
            big, small = (int(t) + 1) // 2, int(t) // 2
            for home_starts, ps in starters:
                w = pt * ps
                if w <= 0.0:
                    continue
                nh, na = (big, small) if home_starts else (small, big)
                m = np.convolve(sh[nh], sa[na][::-1])
                start = offset - 3 * na
                out[start:start + m.size] += w * m
        return out, offset

    # Main entry point.

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        lam_h = self._get(ctx, "home_expected")
        lam_a = self._get(ctx, "away_expected")
        lam_h = lam_h if (lam_h is not None and lam_h > 0) else self.home_anchor
        lam_a = lam_a if (lam_a is not None and lam_a > 0) else self.away_anchor

        # Attack/defense exposure: attack > 1 means a stronger offence, and
        # defense > 1 means more points conceded.
        def ratio(v: Optional[float]) -> float:
            return v if (v is not None and v > 0) else 1.0

        a_h, d_h = ratio(self._side(ctx, "home", "attack")), ratio(self._side(ctx, "home", "defense"))
        a_a, d_a = ratio(self._side(ctx, "away", "attack")), ratio(self._side(ctx, "away", "defense"))
        lam_h *= (a_h * d_a) ** self.exposure_weight
        lam_a *= (a_a * d_h) ** self.exposure_weight

        total = lam_h + lam_a
        ell = math.log(lam_h / lam_a)  # log-share strength gap; d(gap)/d(ell) ~= total/2
        pts_to_ell = 2.0 / total

        # Rest differential, saturating smoothly.
        r_h, r_a = self._side(ctx, "home", "rest_days"), self._side(ctx, "away", "rest_days")
        if r_h is not None and r_a is not None and self.rest_scale_days > 0:
            ell += self.rest_effect_points * pts_to_ell * math.tanh((r_h - r_a) / self.rest_scale_days)

        # Blowout compression, driven by the full-game projected margin.
        full_margin = self._get(ctx, "full_game_margin", "projected_margin")
        if full_margin is None:
            home_spread = self._get(ctx, "home_spread", "spread")
            if home_spread is not None:
                full_margin = -home_spread
        if full_margin is None:
            full_margin = 4.0 * total * math.tanh(ell / 2.0)
        g = self.blowout_scale / math.sqrt(self.blowout_scale ** 2 + full_margin ** 2)
        ell *= self.halftime_regression * g

        # Possession process.
        m = self._possession_mean(ctx)
        t_support, t_probs = self._possession_distribution(m)
        starter = ctx.get("q3_home_first_possession", ctx.get("home_starts_q3"))
        p_home_starts = 0.5 if starter is None else (1.0 if bool(starter) else 0.0)

        # Integrate over cold-start rating uncertainty.
        sd_ell = self.rating_sd_points * pts_to_ell
        margin = None
        offset = 0
        for z, w in zip(self._gh_nodes, self._gh_weights):
            share = 1.0 / (1.0 + math.exp(-(ell + sd_ell * z)))
            lh, la = total * share, total * (1.0 - share)
            pmf, off = self._margin_pmf(lh / m, la / m, t_support, t_probs, p_home_starts)
            if margin is None:
                margin, offset = w * pmf, off
            else:
                margin += w * pmf
        margin = np.clip(margin, 0.0, None)
        margin /= margin.sum()

        p_home = float(margin[offset + 1:].sum())
        p_draw = float(margin[offset])
        p_away = float(margin[:offset].sum())

        support = np.arange(margin.size) - offset
        mu_m = float(support @ margin)
        var_m = float(((support - mu_m) ** 2) @ margin)
        self.last_diagnostics = {
            "lambda_home": total / (1.0 + math.exp(-ell)),
            "lambda_away": total / (1.0 + math.exp(ell)),
            "possessions_per_team": m,
            "blowout_factor": g,
            "margin_mean": mu_m,
            "margin_sd": math.sqrt(var_m),
            "margin_var_over_total": var_m / total,
            "tie_mass": p_draw,
        }

        if self.tie_mode == "push_renormalize":
            s = p_home + p_away
            p_home, p_away, p_draw = p_home / s, p_away / s, 0.0

        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

        try:
            return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})
        except TypeError:
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)