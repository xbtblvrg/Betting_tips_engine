"""
Candidate model for NCAAW | half_2 | moneyline.

Validation status:
    The settled historical sample for this segment is 0 bets, so this model has
    no out-of-sample validation for this market. The referenced benchmarks
    (+85.0% ROI, +0.2109 delta-Brier) are not claimed or implied here. An ROI of
    that size on a liquid moneyline is almost certainly a small-sample or
    leakage artefact rather than a reproducible target. The model should be
    paper-traded and evaluated (Brier, log-loss, CLV) before any capital
    allocation.

Structural priors:
    A cold-start market has no data to fit against, so "zero constants" is not
    achievable. Every structural quantity below is a named, documented,
    overridable domain prior (constructor `priors=` or game_context
    `model_priors`), not a hidden clamp. Once settled 2H results accumulate,
    each prior should be re-estimated.

Architecture (exact lattice, no Gaussian approximation of the sign):
    1. Anchor scope resolution: 40.0 / 37.5 is ambiguous (high for an NCAAW half,
       low for a full game). Resolve via the geometric midpoint between the
       league half-game and full-game reference totals, unless the scope is
       given explicitly.
    2. Split the deviation of the total from the league reference symmetrically
       (in log space) between pace (possessions) and efficiency (PPP).
    3. Per-possession outcome pmf on {0,1,2,3,4}: a minimum-relative-entropy
       (exponential) tilt of an NCAAW possession template, solved so that
       E[X] = team PPP. This keeps the 2/3/paired-FT lattice structure that
       raw-point Poisson/Skellam models destroy.
    4. Shared possession count N ~ discretised Normal (possession counts within
       a fixed clock are under-dispersed, so NegBin cannot represent them).
       Margin characteristic function: G(w) = sum_n P(n) * D(w)^n, where D is
       the per-possession-pair margin increment CF. Inverted exactly by FFT.
    5. Home edge: Bayesian shrink of the anchor-implied margin toward a per-half
       home-court prior (limits home over-confidence). Residual attack/defense
       and rest information enters as a partial-weight efficiency tilt.
    6. End-game fouling: extra variance tau^2 scaled by the model-implied
       probability that the full game is close.
    7. Tie / overtime: a 2H moneyline settles on 2H score (+OT where the book
       includes it). OT occurs only when the full game is tied, i.e.
       M2 = -M1, so the 1H margin pmf (pregame) or the realised 1H margin
       (live) determines which 2H ties become OT, and OT margin is itself
       modelled on the lattice.
    8. Live: mean-reversion -beta * margin_1H for state-dependent catch-up play.

Note: pure scale effects (garbage-time compression of tails) do not change
sign(M), so they affect the moneyline only through the mean and the tie mass,
which steps 5-8 already handle.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - sandbox may pre-load it
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")


DEFAULT_PRIORS: Dict[str, Any] = {
    # League scoring environment (NCAA D-I women's)
    "league_ppp": 0.93,                # points per possession
    "league_poss_per_40": 70.0,        # possessions per team per 40 minutes
    "share_2h": 0.51,                  # 2H share of full-game scoring (late FTs)
    # Home court
    "hca_full_game": 2.6,              # points; ~1.3 per half
    # Bayesian shrink of anchor-implied 2H margin
    "sigma_true_margin_half": 6.0,     # between-matchup sd of true 2H margin
    "sigma_anchor_noise_half": 2.5,    # error sd of supplied anchors per half
    # Pace dispersion
    "pace_sd_frac": 0.06,              # sd(N)/E[N] within a half
    # Residual situational information (anchors assumed to already include most)
    "rating_weight": 0.35,             # exponent on attack/defense relative tilt
    "rest_eff_per_logday": 0.012,      # PPP multiplier per log(1+rest) diff
    # End-game fouling
    "late_foul_extra_trips": 4.0,      # extra FT trips when the game is close
    "ft_pct": 0.71,
    "close_window": 6,                 # full-game margin defining "close"
    # Live state dependence
    "live_beta": 0.05,                 # 2H mean reversion per point of 1H margin
    # Overtime
    "ot_minutes_frac": 0.25,           # 5-min OT / 20-min half
    # Possession outcome template over {0,1,2,3,4}
    "shape_template": [0.50, 0.045, 0.33, 0.115, 0.010],
    # Settlement: 'ot_included' (default US book rule) or 'regulation_only'
    "tie_rule": "ot_included",
    # FFT lattice size (margin support +/- L/2)
    "lattice_size": 1024,
}


class NCAAWHalf2MoneylineModel(BaseCandidateModel):
    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_half_2_1107_231734",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "half_2",
        family: str = "moneyline",
        priors: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
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
        self.priors: Dict[str, Any] = dict(DEFAULT_PRIORS)
        if priors:
            self.priors.update(priors)

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], keys, default: Optional[float] = None) -> Optional[float]:
        for k in keys:
            v = ctx.get(k)
            if v is None:
                continue
            try:
                f = float(v)
                if math.isfinite(f):
                    return f
            except (TypeError, ValueError):
                continue
        return default

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        val = self._num(ctx, [f"{side}_{kind}", f"{side}_{kind}_rating", f"{side}_{kind[:3]}"])
        if val is not None:
            return val
        team = ctx.get(side)
        if isinstance(team, dict):
            return self._num(team, [kind, f"{kind}_rating", kind[:3]])
        return None

    @staticmethod
    def _is_neutral(ctx: Dict[str, Any]) -> bool:
        for k in ("neutral_site", "is_neutral", "neutral"):
            if bool(ctx.get(k)):
                return True
        venue = ctx.get("venue")
        if isinstance(venue, dict):
            return bool(venue.get("neutral") or venue.get("neutral_site"))
        return False

    # ------------------------------------------------------------------ #
    # Mechanics primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tilt(template: np.ndarray, target_mean: float) -> np.ndarray:
        """Min-KL exponential tilt of template on {0..K} with mean = target."""
        support = np.arange(len(template), dtype=float)
        lo, hi = 1e-6, support[-1] - 1e-6  # numerical domain of a mean on {0..K}
        m = float(np.clip(target_mean, lo, hi))
        logq = np.log(template)

        def mean_gap(theta: float) -> float:
            w = logq + theta * support
            w -= w.max()
            p = np.exp(w)
            p /= p.sum()
            return float(p @ support) - m

        theta = optimize.brentq(mean_gap, -60.0, 60.0, xtol=1e-12)
        w = logq + theta * support
        w -= w.max()
        p = np.exp(w)
        return p / p.sum()

    @staticmethod
    def _pmf_var(p: np.ndarray) -> float:
        s = np.arange(len(p), dtype=float)
        mu = float(p @ s)
        return float(p @ (s - mu) ** 2)

    def _margin_pmf(
        self, n_mean: float, n_sd: float, ph: np.ndarray, pa: np.ndarray, tau2: float
    ) -> np.ndarray:
        """Exact lattice pmf of sum_{i<=N}(X_h - X_a), N ~ discretised Normal."""
        L = int(self.priors["lattice_size"])
        inc = np.zeros(L)
        for i, pi in enumerate(ph):
            for j, pj in enumerate(pa):
                inc[(i - j) % L] += pi * pj
        D = np.fft.fft(inc)

        n_sd = max(n_sd, 1e-9)
        n_lo = max(0, int(math.floor(n_mean - 6.0 * n_sd)))
        n_hi = int(math.ceil(n_mean + 6.0 * n_sd))
        ns = np.arange(n_lo, n_hi + 1)
        w = np.exp(-0.5 * ((ns - n_mean) / n_sd) ** 2)
        w /= w.sum()

        G = (w[:, None] * np.power(D[None, :], ns[:, None])).sum(axis=0)
        if tau2 > 0.0:
            omega = 2.0 * np.pi * np.fft.fftfreq(L)
            G = G * np.exp(-0.5 * tau2 * omega ** 2)

        pmf = np.real(np.fft.ifft(G))
        pmf = np.where(pmf > 0.0, pmf, 0.0)  # remove FFT round-off only
        return pmf / pmf.sum()

    def _ordered(self, pmf: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        L = len(pmf)
        ms = np.arange(-L // 2, L // 2)
        return ms, pmf[ms % L]

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = dict(game_context or {})
        P = dict(self.priors)
        if isinstance(ctx.get("model_priors"), dict):
            P.update(ctx["model_priors"])

        template = np.asarray(P["shape_template"], dtype=float)
        template = template / template.sum()
        share_2h = float(P["share_2h"])
        league_ppp = float(P["league_ppp"])
        poss_half_ref = float(P["league_poss_per_40"]) / 2.0

        # 1. Anchors and scope resolution ------------------------------
        h2 = self._num(ctx, ["home_expected_2h", "home_expected_half_2"])
        a2 = self._num(ctx, ["away_expected_2h", "away_expected_half_2"])
        if h2 is None or a2 is None:
            h = self._num(ctx, ["home_expected"], 40.0)
            a = self._num(ctx, ["away_expected"], 37.5)
            scope_hint = str(ctx.get("expected_scope", "")).lower()
            half_ref_total = 2.0 * poss_half_ref * league_ppp
            full_ref_total = half_ref_total / share_2h
            if scope_hint in ("full", "full_game", "game"):
                is_full = True
            elif scope_hint in ("half", "half_2", "2h", "second_half"):
                is_full = False
            else:
                is_full = (h + a) > math.sqrt(half_ref_total * full_ref_total)
            scale = share_2h if is_full else 1.0
            h2, a2 = h * scale, a * scale

        total_2h = max(h2 + a2, 1e-6)

        # 2. Pace / efficiency split ------------------------------------
        pace = self._num(ctx, ["pace", "tempo", "expected_pace", "possessions"])
        ref_total = 2.0 * poss_half_ref * league_ppp
        if pace is not None and pace > 0:
            n_mean = pace / 2.0 if pace > 1.5 * poss_half_ref else pace
        else:
            n_mean = poss_half_ref * math.sqrt(total_2h / ref_total)
        n_sd = float(P["pace_sd_frac"]) * n_mean

        ppp_h = h2 / n_mean
        ppp_a = a2 / n_mean

        # 3. Residual situational tilts (attack/defense, rest) ----------
        att_h = self._team_rating(ctx, "home", "attack")
        def_h = self._team_rating(ctx, "home", "defense")
        att_a = self._team_rating(ctx, "away", "attack")
        def_a = self._team_rating(ctx, "away", "defense")
        log_rel = 0.0
        if all(v is not None and v > 0 for v in (att_h, def_h, att_a, def_a)):
            att_gm = math.sqrt(att_h * att_a)
            def_gm = math.sqrt(def_h * def_a)
            mult_h = (att_h / att_gm) * (def_a / def_gm)
            mult_a = (att_a / att_gm) * (def_h / def_gm)
            log_rel += 0.5 * float(P["rating_weight"]) * math.log(mult_h / mult_a)

        rest_h = self._num(ctx, ["home_rest_days"])
        rest_a = self._num(ctx, ["away_rest_days"])
        if rest_h is not None and rest_a is not None:
            log_rel += 0.5 * float(P["rest_eff_per_logday"]) * (
                math.log1p(max(rest_h, 0.0)) - math.log1p(max(rest_a, 0.0))
            )
        ppp_h *= math.exp(log_rel)
        ppp_a *= math.exp(-log_rel)
        # (Weather inputs are ignored: indoor sport.)

        # 4. Bayesian home-edge shrink ----------------------------------
        hca_half = 0.0 if self._is_neutral(ctx) else float(P["hca_full_game"]) * share_2h
        s2_true = float(P["sigma_true_margin_half"]) ** 2
        s2_obs = float(P["sigma_anchor_noise_half"]) ** 2
        w_anchor = s2_true / (s2_true + s2_obs)
        d_raw = n_mean * (ppp_h - ppp_a)
        mu_margin = w_anchor * d_raw + (1.0 - w_anchor) * hca_half

        # 5. Live conditioning on 1H score ------------------------------
        h1 = self._num(ctx, ["half1_home_score", "home_score_1h", "home_1h"])
        a1 = self._num(ctx, ["half1_away_score", "away_score_1h", "away_1h"])
        live = h1 is not None and a1 is not None
        m1 = int(round(h1 - a1)) if live else 0
        if live:
            mu_margin -= float(P["live_beta"]) * m1

        ppp_sum = ppp_h + ppp_a
        ppp_h = 0.5 * (ppp_sum + mu_margin / n_mean)
        ppp_a = 0.5 * (ppp_sum - mu_margin / n_mean)

        ph = self._tilt(template, ppp_h)
        pa = self._tilt(template, ppp_a)

        # 6. First-pass pmfs and end-game fouling variance --------------
        pmf2_base = self._margin_pmf(n_mean, n_sd, ph, pa, 0.0)
        n1_mean = n_mean * (1.0 - share_2h) / share_2h
        L = len(pmf2_base)
        if live:
            pmf1 = np.zeros(L)
            pmf1[m1 % L] = 1.0
        else:
            pmf1 = self._margin_pmf(n1_mean, float(P["pace_sd_frac"]) * n1_mean, ph, pa, 0.0)

        full_pmf = np.real(np.fft.ifft(np.fft.fft(pmf2_base) * np.fft.fft(pmf1)))
        full_pmf = np.where(full_pmf > 0.0, full_pmf, 0.0)
        full_pmf /= full_pmf.sum()
        ms_f, pf = self._ordered(full_pmf)
        p_close = float(pf[np.abs(ms_f) <= int(P["close_window"])].sum())

        ft = float(P["ft_pct"])
        var_trip = 2.0 * ft * (1.0 - ft) + 0.5 * (self._pmf_var(ph) + self._pmf_var(pa))
        tau2 = float(P["late_foul_extra_trips"]) * p_close * var_trip

        pmf2 = self._margin_pmf(n_mean, n_sd, ph, pa, tau2)

        # 7. Settlement with tie / overtime logic -----------------------
        ms, p2 = self._ordered(pmf2)
        tie_rule = str(ctx.get("tie_rule", P["tie_rule"])).lower()

        if tie_rule == "regulation_only":
            p_home = float(p2[ms > 0].sum())
            p_draw = float(p2[ms == 0].sum())
            p_away = float(p2[ms < 0].sum())
        else:
            # Full game tied after regulation  <=>  M1 = -M2
            p_game_tied = pmf1[(-ms) % L]

            n_ot = n_mean * float(P["ot_minutes_frac"])
            ot = self._margin_pmf(n_ot, float(P["pace_sd_frac"]) * n_ot, ph, pa, 0.0)
            ot[0] = 0.0                 # repeated OT until decided
            ot /= ot.sum()
            _, ot_ord = self._ordered(ot)
            ot_cdf = np.cumsum(ot_ord)
            idx = np.clip(-ms + L // 2, 0, L - 1)     # position of value -m2
            p_ot_gt = 1.0 - ot_cdf[idx]               # P(OT > -m2)
            p_ot_eq = ot_ord[idx]                     # P(OT = -m2)

            no_ot = p2 * (1.0 - p_game_tied)
            with_ot = p2 * p_game_tied
            p_home = float(no_ot[ms > 0].sum() + (with_ot * p_ot_gt).sum())
            p_draw = float(no_ot[ms == 0].sum() + (with_ot * p_ot_eq).sum())
            p_away = max(1.0 - p_home - p_draw, 0.0)

        z = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / z, p_draw / z, p_away / z

        self.last_diagnostics = {
            "h2_anchor": h2, "a2_anchor": a2,
            "n_mean": n_mean, "ppp_home": ppp_h, "ppp_away": ppp_a,
            "mu_margin": mu_margin, "hca_half": hca_half, "w_anchor": w_anchor,
            "tau2_foul": tau2, "p_close_full_game": p_close,
            "live": live, "m1": m1, "tie_rule": tie_rule,
        }

        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )