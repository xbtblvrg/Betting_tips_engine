"""
Candidate model for NCAAM | half_1 | moneyline.

Design:
  * First-half possessions N are shared by both teams and are random. They are
    built from the two teams' tempos and the league tempo, using the first-half
    share of possessions.
  * Each possession scores X in {0, 1, 2, 3}. A base shape comes from turnover
    rate, three-point attempt rate and free-throw rate. That shape is then
    exponentially tilted so E[X] matches the efficiency implied by the anchors.
    Variance therefore follows from the possession mix, with no Poisson
    (Var/Mean = 1) assumption.
  * The half-1 margin pmf is computed exactly on the integer lattice:
        P(D = d) = sum_n P(N = n) * [home_n (*) reverse(away_n)](d)
    where (*) is convolution and home_n is the n-fold convolution of the
    possession pmf. Shared N creates the pace correlation automatically. The
    tie mass p(0) is computed exactly rather than approximated.
  * Home over-confidence control. The anchor margin is shrunk by precision
    weighting toward a structural first-half home-court prior, never toward a
    bookmaker line, so no vig enters the forecast. The remaining parameter
    uncertainty is integrated out by convolving the margin with a discretised
    normal kernel. Its variance falls as settled sample size grows.
  * Output: 3-way {home, draw, away}, where draw is the halftime tie mass.

All domain priors are named, documented parameters that can be overridden
through constructor kwargs or game_context. There are no probability clamps or
odds caps.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

try:  # Normal engine layout
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - primitives may be pre-loaded in scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)  # type: ignore

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class NCAAMHalf1MoneylineModel(BaseCandidateModel):  # type: ignore[misc]
    """Possession-lattice margin model for NCAAM first-half moneylines."""

    # ------------------------------------------------------------------ #
    # Domain priors (documented, overridable; not probability clamps)
    # ------------------------------------------------------------------ #
    DEFAULT_PRIORS: Dict[str, float] = {
        # Empirical segment anchors (half-1 points)
        "anchor_home_expected": 40.00,
        "anchor_away_expected": 37.50,
        # Tempo: full-game possessions per team and first-half share
        "league_pace": 68.0,
        "half1_possession_share": 0.49,   # H2 has more late-game fouling
        "possession_sd": 2.4,             # game-to-game sd of H1 possessions given tempo
        # Possession-ending mix (league-typical shape; mean is re-fitted by tilting)
        "tov_rate": 0.175,
        "three_rate": 0.385,              # 3PA / FGA
        "ft_rate": 0.32,                  # FTA / FGA
        "ft_trip_per_fta": 0.44,          # possessions-equivalent per FTA
        "ft_pct": 0.715,
        "fg2_eff_make": 0.555,            # 2P make prob incl. offensive-rebound recycling
        "fg3_eff_make": 0.375,            # 3P make prob incl. offensive-rebound recycling
        "and_one_share": 0.035,           # share of 2P makes converted into 3-point trips
        # Matchup and rest adjustments
        "league_offensive_eff": 104.0,    # points per 100, for normalising raw ratings
        "matchup_pooling_weight": 0.5,    # partial pooling; anchors already embed matchup
        "rest_eff_per_log_day": 0.010,    # efficiency elasticity w.r.t. log(1 + rest)
        # Structural home edge for half 1 (about half of the ~3.2 full-game HCA)
        "half1_hca_prior": 1.6,
        "hca_prior_sd": 1.25,             # uncertainty of the structural prior (points)
        "anchor_margin_sd_cold": 2.0,     # anchor-margin uncertainty with no settled data
        "anchor_pseudo_count": 150.0,     # settled bets that halve anchor variance
        # Lattice integration width (in sd units of N)
        "possession_grid_sd": 4.5,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_half_1_1107_234836",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "half_1",
        family: str = "moneyline",
        **overrides: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
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
        self.priors: Dict[str, float] = dict(self.DEFAULT_PRIORS)
        for k, v in overrides.items():
            if k in self.priors and v is not None:
                self.priors[k] = float(v)
        self._pmf_cache: Dict[Tuple, np.ndarray] = {}

    # ------------------------------------------------------------------ #
    # Feature helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], key: str, default: float) -> float:
        v = self._num(ctx.get(key))
        return default if v is None else v

    def _team(self, ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        t = ctx.get(side)
        return t if isinstance(t, dict) else {}

    def _team_feat(self, ctx: Dict[str, Any], side: str, names, default: float) -> float:
        team = self._team(ctx, side)
        for n in names:
            v = self._num(team.get(n))
            if v is None:
                v = self._num(ctx.get(f"{side}_{n}"))
            if v is not None:
                return v
        return default

    def _rating_ratio(self, value: Optional[float]) -> float:
        """Map an attack/defense rating to a multiplicative ratio centred at 1."""
        if value is None or value <= 0:
            return 1.0
        if value > 3.0:  # per-100-possession efficiency scale
            return value / self.priors["league_offensive_eff"]
        return value

    # ------------------------------------------------------------------ #
    # Possession mechanics
    # ------------------------------------------------------------------ #
    def _base_possession_pmf(self, tov: float, three_rate: float, ft_rate: float) -> np.ndarray:
        p = self.priors
        tov = float(np.clip(tov, 1e-4, 1 - 1e-4))
        three_rate = float(np.clip(three_rate, 1e-4, 1 - 1e-4))
        ft_rate = max(ft_rate, 0.0)

        live = 1.0 - tov
        ft_trip_weight = ft_rate * p["ft_trip_per_fta"]
        q_ft = ft_trip_weight / (1.0 + ft_trip_weight)
        q_fg = 1.0 - q_ft

        ftp = p["ft_pct"]
        ft = np.array([(1 - ftp) ** 2, 2 * ftp * (1 - ftp), ftp ** 2, 0.0])

        m2, m3, a1 = p["fg2_eff_make"], p["fg3_eff_make"], p["and_one_share"]
        two = (1 - three_rate) * np.array([1 - m2, 0.0, m2 * (1 - a1), m2 * a1])
        three = three_rate * np.array([1 - m3, 0.0, 0.0, m3])

        pmf = np.array([tov, 0.0, 0.0, 0.0]) + live * (q_ft * ft + q_fg * (two + three))
        return pmf / pmf.sum()

    @staticmethod
    def _tilt_to_mean(base: np.ndarray, target_mean: float) -> np.ndarray:
        """Minimum-KL (exponential) tilt of base so that E[X] = target_mean."""
        x = np.arange(base.size, dtype=float)
        lo, hi = 1e-9, x[-1] - 1e-9
        target = min(max(target_mean, lo), hi)
        logb = np.log(np.maximum(base, 1e-300))

        def mean_gap(theta: float) -> float:
            w = logb + theta * x
            w = np.exp(w - w.max())
            w /= w.sum()
            return float(w @ x) - target

        a, b = -40.0, 40.0
        while mean_gap(a) > 0:
            a *= 2.0
        while mean_gap(b) < 0:
            b *= 2.0
        theta = optimize.brentq(mean_gap, a, b, xtol=1e-12)
        w = np.exp(logb + theta * x - (logb + theta * x).max())
        return w / w.sum()

    @staticmethod
    def _discrete_normal(mean: float, sd: float, lo: int, hi: int) -> Tuple[np.ndarray, np.ndarray]:
        support = np.arange(lo, hi + 1)
        cdf = stats.norm.cdf(support + 0.5, mean, sd) - stats.norm.cdf(support - 0.5, mean, sd)
        cdf = np.maximum(cdf, 0.0)
        return support, cdf / cdf.sum()

    def _margin_pmf(
        self, pmf_h: np.ndarray, pmf_a: np.ndarray, n_mean: float, n_sd: float
    ) -> Tuple[np.ndarray, int]:
        """Exact margin pmf by mixing over shared possessions N."""
        k = self.priors["possession_grid_sd"]
        n_lo = max(1, int(math.floor(n_mean - k * n_sd)))
        n_hi = max(n_lo, int(math.ceil(n_mean + k * n_sd)))
        ns, wn = self._discrete_normal(n_mean, n_sd, n_lo, n_hi)

        size = 6 * n_hi + 1
        centre = 3 * n_hi
        margin = np.zeros(size)

        sum_h = np.array([1.0])
        sum_a = np.array([1.0])
        for n in range(1, n_hi + 1):
            sum_h = np.convolve(sum_h, pmf_h)
            sum_a = np.convolve(sum_a, pmf_a)
            if n < n_lo:
                continue
            w = wn[n - n_lo]
            if w <= 0:
                continue
            d = np.convolve(sum_h, sum_a[::-1])   # index 3n is margin 0
            start = centre - 3 * n
            margin[start:start + d.size] += w * d
        margin /= margin.sum()
        return margin, centre

    def _integrate_mean_uncertainty(self, margin: np.ndarray, centre: int, tau: float) -> Tuple[np.ndarray, int]:
        if tau <= 1e-9:
            return margin, centre
        half = int(math.ceil(6.0 * tau)) + 1
        _, kern = self._discrete_normal(0.0, tau, -half, half)
        out = np.convolve(margin, kern)
        return out / out.sum(), centre + half

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        p = dict(self.priors)
        for k in list(p):
            v = self._num(ctx.get(k))
            if v is not None:
                p[k] = v

        # 1. Anchors -------------------------------------------------------
        home_exp = self._get(ctx, "home_expected", p["anchor_home_expected"])
        away_exp = self._get(ctx, "away_expected", p["anchor_away_expected"])
        home_exp = max(home_exp, 1e-6)
        away_exp = max(away_exp, 1e-6)

        # 2. Shared first-half possessions -----------------------------------
        league_pace = p["league_pace"]
        pace_h = self._team_feat(ctx, "home", ["pace", "tempo"], league_pace)
        pace_a = self._team_feat(ctx, "away", ["pace", "tempo"], league_pace)
        n_mean = (pace_h * pace_a / league_pace) * p["half1_possession_share"]
        n_mean = self._get(ctx, "half1_possessions", n_mean)
        n_sd = max(p["possession_sd"], 1e-6)

        # 3. Matchup and rest multipliers on efficiency -----------------------
        w = p["matchup_pooling_weight"]
        att_h = self._rating_ratio(self._num(self._team(ctx, "home").get("attack")))
        def_h = self._rating_ratio(self._num(self._team(ctx, "home").get("defense")))
        att_a = self._rating_ratio(self._num(self._team(ctx, "away").get("attack")))
        def_a = self._rating_ratio(self._num(self._team(ctx, "away").get("defense")))
        mult_h = (att_h * def_a) ** w
        mult_a = (att_a * def_h) ** w

        rest_h = max(self._get(ctx, "home_rest_days", 0.0), 0.0)
        rest_a = max(self._get(ctx, "away_rest_days", 0.0), 0.0)
        rest_delta = math.log1p(rest_h) - math.log1p(rest_a)
        rest_mult = math.exp(0.5 * p["rest_eff_per_log_day"] * rest_delta)
        mult_h *= rest_mult
        mult_a /= rest_mult

        pts_h = home_exp * mult_h
        pts_a = away_exp * mult_a

        # 4. Precision-weighted shrinkage of the home edge --------------------
        total = pts_h + pts_a
        mu_anchor = pts_h - pts_a
        neutral = bool(ctx.get("neutral_site") or (isinstance(ctx.get("venue"), dict)
                                                   and ctx["venue"].get("neutral")))
        mu_prior = 0.0 if neutral else p["half1_hca_prior"]
        n_settled = max(self._get(ctx, "segment_settled_sample", 0.0), 0.0)
        var_anchor = p["anchor_margin_sd_cold"] ** 2 * p["anchor_pseudo_count"] / (
            p["anchor_pseudo_count"] + n_settled)
        # The prior constrains only the home-advantage component, so its
        # variance includes the true matchup spread around HCA
        # (|mu_anchor - mu_prior|^2 acts as an empirical-Bayes signal term).
        var_prior = p["hca_prior_sd"] ** 2 + (mu_anchor - mu_prior) ** 2
        prec_a, prec_p = 1.0 / var_anchor, 1.0 / var_prior
        mu_post = (prec_a * mu_anchor + prec_p * mu_prior) / (prec_a + prec_p)
        tau_post = math.sqrt(1.0 / (prec_a + prec_p))

        tgt_h = max(0.5 * (total + mu_post), 1e-6)
        tgt_a = max(0.5 * (total - mu_post), 1e-6)

        # 5. Possession pmfs (shape from style, mean from targets) -------------
        def style(side: str, opp: str) -> np.ndarray:
            tov = 0.5 * (self._team_feat(ctx, side, ["tov_rate", "to_rate"], p["tov_rate"])
                         + self._team_feat(ctx, opp, ["forced_tov_rate", "opp_tov_rate"], p["tov_rate"]))
            r3 = 0.5 * (self._team_feat(ctx, side, ["three_rate", "3pa_rate"], p["three_rate"])
                        + self._team_feat(ctx, opp, ["opp_three_rate"], p["three_rate"]))
            ftr = 0.5 * (self._team_feat(ctx, side, ["ft_rate", "fta_rate"], p["ft_rate"])
                         + self._team_feat(ctx, opp, ["opp_ft_rate"], p["ft_rate"]))
            return self._base_possession_pmf(tov, r3, ftr)

        pmf_h = self._tilt_to_mean(style("home", "away"), tgt_h / n_mean)
        pmf_a = self._tilt_to_mean(style("away", "home"), tgt_a / n_mean)

        # 6. Exact margin lattice plus parameter-uncertainty integration -------
        margin, centre = self._margin_pmf(pmf_h, pmf_a, n_mean, n_sd)
        margin, centre = self._integrate_mean_uncertainty(margin, centre, tau_post)

        p_draw = float(margin[centre])
        p_home = float(margin[centre + 1:].sum())
        p_away = float(margin[:centre].sum())
        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

        self.last_diagnostics = {
            "possessions_mean": n_mean,
            "possessions_sd": n_sd,
            "target_home_pts": tgt_h,
            "target_away_pts": tgt_a,
            "margin_mean_anchor": mu_anchor,
            "margin_mean_posterior": mu_post,
            "margin_mean_uncertainty_sd": tau_post,
            "margin_sd": float(np.sqrt(
                ((np.arange(margin.size) - centre) ** 2 * margin).sum()
                - ((np.arange(margin.size) - centre) * margin).sum() ** 2)),
            "home_possession_var_mean_ratio": float(
                (pmf_h @ np.arange(4) ** 2 - (pmf_h @ np.arange(4)) ** 2) / (pmf_h @ np.arange(4))),
            "p_tie": p_draw,
        }

        if hasattr(DiscreteDistribution, "from_probabilities"):
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})


# Engine discovery alias
CandidateModel = NCAAMHalf1MoneylineModel