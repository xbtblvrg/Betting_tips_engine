import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, special, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - resolved from pre-loaded execution scope
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Structural domain priors (NCAAF drive mechanics).
# These are not clamps or caps. They are physical and structural parameters
# of the drive process. Every one of them can be overridden per call through
# game_context["model_priors"]. Per-drive scoring efficiency is NOT a prior:
# it is solved on the fly so the lattice reproduces home_expected and
# away_expected exactly.
# ---------------------------------------------------------------------------
DEFAULT_PRIORS: Dict[str, float] = {
    # Clock / possession structure
    "quarter_seconds": 900.0,
    "league_drives_per_team_game": 12.5,      # NCAAF possessions per team per game
    "drive_duration_cv": 0.65,                # coefficient of variation of drive clock time
    "league_plays_per_team_game": 70.0,       # used only to normalise raw tempo inputs
    # Baseline per-drive terminal outcome mix (league-average offense vs defense)
    "base_p_td": 0.235,
    "base_p_fg_attempt": 0.170,
    "base_p_return_td": 0.012,                # pick-six / fumble-six against the offense
    "base_p_safety": 0.004,
    "fg_efficiency_loading": 0.35,            # FG-attempt sensitivity to offensive efficiency (TD loading = 1)
    # Kicking / conversion structure
    "fg_make_rate": 0.76,
    "fg_wind_logit_slope": -0.035,            # per mph
    "fg_cold_logit_slope": -0.010,            # per degF below reference
    "fg_temp_reference": 50.0,
    "two_point_attempt_rate": 0.05,
    "two_point_success_rate": 0.45,
    "pat_kick_success_rate": 0.965,
    # Situational modulators (smooth, bounded by construction via tanh/softplus)
    "rest_effect_log_scale": 0.03,
    "rest_effect_day_scale": 7.0,
    "blowout_clock_log_scale": 0.10,          # running clock / run-heavy play in lopsided games
    "blowout_margin_scale": 21.0,
    "spread_to_halftime_margin": 0.5,
    # Cold-start empirical anchors
    "anchor_home_expected": 7.00,
    "anchor_away_expected": 6.25,
    # Numerical
    "survival_tolerance": 1e-12,
    "efficiency_search_radius": 10.0,
    "default_home_receives_prob": 0.5,
}


class NCAAFQuarter3DriveLatticeModel(BaseCandidateModel):
    """
    NCAAF 3rd-quarter three-way (home / draw / away) model built as a drive process.

    Mechanics
    ---------
    1. Clock: drive clock durations are i.i.d. Gamma(mean=mu, CV=c). The number of
       drives that complete inside the 900 s quarter is the renewal count C, with
       P(C >= k) = P(S_k <= T), where S_k ~ Gamma(k / c^2, mu * c^2). The drive that
       straddles the end of the quarter scores nothing in Q3, because a score ends a
       drive by definition. Tempo, pace and game-state (garbage-time) inputs move mu.
    2. Possession: Q3 opens with the second-half kickoff. The receiving team drives
       first (known from context or mixed 50/50). Possession alternates after TDs,
       FGs, punts, turnovers and safeties. After a defensive return TD the scorer
       kicks off, so the same offense gets the ball back.
    3. Drive outcome: a multinomial over {TD, FG attempt, return-TD against, safety,
       empty}, using softmax logits with an offensive efficiency shift x. TD values
       resolve to {6, 7, 8} through the PAT and two-point structure. FG attempts
       resolve to make or miss through a weather-dependent logistic make rate.
    4. Calibration: (x_home, x_away) are solved jointly so that exact expected Q3
       points equal the situational targets (anchored at 7.00 / 6.25).
    5. Output: the exact joint score lattice is built by sequential 2-D convolution
       over the possession state, mixed over C. P(draw) is the lattice trace, which
       puts tie mass on 0-0, 3-3, 7-7, and so on.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_quarter_3_1107_231747",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "quarter_3",
        family: str = "three_way",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            **kwargs,
        )
        self.priors: Dict[str, float] = dict(DEFAULT_PRIORS)
        self.last_diagnostics: Dict[str, Any] = {}
        self.last_lattice: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._to_float(ctx.get(k))
                if v is not None:
                    return v
        return None

    def _team_feature(self, ctx: Dict[str, Any], side: str, *names: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            for n in names:
                v = self._to_float(block.get(n))
                if v is not None:
                    return v
        for n in names:
            v = self._to_float(ctx.get(f"{side}_{n}"))
            if v is not None:
                return v
        return None

    @staticmethod
    def _as_multiplier(rating: Optional[float]) -> float:
        """Positive ratings are read as multiplicative indices. Non-positive ones as log-scale."""
        if rating is None:
            return 1.0
        return rating if rating > 0.0 else math.exp(rating)

    def _pace_multiplier(self, raw: Optional[float], pr: Dict[str, float]) -> float:
        if raw is None:
            return 1.0
        if raw > 5.0:  # raw plays-per-game style input
            return raw / pr["league_plays_per_team_game"]
        return self._as_multiplier(raw)

    def _resolve_priors(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        pr = dict(self.priors)
        overrides = ctx.get("model_priors")
        if isinstance(overrides, dict):
            for k, v in overrides.items():
                fv = self._to_float(v)
                if fv is not None:
                    pr[k] = fv
        return pr

    # --------------------------------------------------------- situational
    def _targets(self, ctx: Dict[str, Any], pr: Dict[str, float]) -> Tuple[float, float]:
        t_h = self._get(ctx, "home_expected", "home_q3_expected", "home_expected_points")
        t_a = self._get(ctx, "away_expected", "away_q3_expected", "away_expected_points")

        if t_h is None or t_a is None:
            att_h = self._as_multiplier(self._team_feature(ctx, "home", "attack", "offense", "attack_rating"))
            def_h = self._as_multiplier(self._team_feature(ctx, "home", "defense", "defense_rating"))
            att_a = self._as_multiplier(self._team_feature(ctx, "away", "attack", "offense", "attack_rating"))
            def_a = self._as_multiplier(self._team_feature(ctx, "away", "defense", "defense_rating"))
            if t_h is None:
                t_h = pr["anchor_home_expected"] * att_h * def_a
            if t_a is None:
                t_a = pr["anchor_away_expected"] * att_a * def_h

        # Rest differential: smooth, symmetric, saturating log-multiplier.
        r_h = self._get(ctx, "home_rest_days")
        r_a = self._get(ctx, "away_rest_days")
        if r_h is not None and r_a is not None:
            shift = pr["rest_effect_log_scale"] * math.tanh((r_h - r_a) / pr["rest_effect_day_scale"])
            t_h *= math.exp(shift)
            t_a *= math.exp(-shift)

        eps = 1e-9
        return max(t_h, eps), max(t_a, eps)

    def _fg_make_prob(self, ctx: Dict[str, Any], pr: Dict[str, float]) -> float:
        is_dome = bool(ctx.get("is_dome", False))
        venue = ctx.get("venue")
        if isinstance(venue, dict):
            is_dome = is_dome or bool(venue.get("is_dome", False))
            weather = venue.get("weather") if isinstance(venue.get("weather"), dict) else {}
        else:
            weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}

        wind = self._get(ctx, "wind_speed")
        if wind is None and weather:
            wind = self._to_float(weather.get("wind_speed"))
        temp = self._get(ctx, "temp", "temperature")
        if temp is None and weather:
            temp = self._to_float(weather.get("temp", weather.get("temperature")))

        if is_dome:
            wind, temp = 0.0, pr["fg_temp_reference"]
        wind = abs(wind) if wind is not None else 0.0
        temp = temp if temp is not None else pr["fg_temp_reference"]

        cold = 5.0 * float(np.logaddexp(0.0, (pr["fg_temp_reference"] - temp) / 5.0))  # softplus hinge
        logit = special.logit(pr["fg_make_rate"])
        logit += pr["fg_wind_logit_slope"] * wind + pr["fg_cold_logit_slope"] * cold
        return float(special.expit(logit))

    def _td_value_dist(self, pr: Dict[str, float]) -> np.ndarray:
        """Probabilities of a TD being worth 6, 7, 8 points."""
        q2 = pr["two_point_attempt_rate"]
        s2 = pr["two_point_success_rate"]
        sk = pr["pat_kick_success_rate"]
        p8 = q2 * s2
        p7 = (1.0 - q2) * sk
        p6 = q2 * (1.0 - s2) + (1.0 - q2) * (1.0 - sk)
        v = np.array([p6, p7, p8], dtype=float)
        return v / v.sum()

    def _drive_clock(self, ctx: Dict[str, Any], pr: Dict[str, float]) -> Tuple[float, float]:
        mu0 = 3600.0 / (2.0 * pr["league_drives_per_team_game"])

        pace_h = self._pace_multiplier(
            self._team_feature(ctx, "home", "pace", "tempo", "plays_per_game"), pr)
        pace_a = self._pace_multiplier(
            self._team_feature(ctx, "away", "pace", "tempo", "plays_per_game"), pr)
        game_pace = self._get(ctx, "pace", "tempo")
        pace = math.sqrt(max(pace_h, 1e-9) * max(pace_a, 1e-9)) * self._pace_multiplier(game_pace, pr)

        # Game state: halftime margin if known, otherwise the expected halftime margin from the spread.
        margin = self._get(ctx, "halftime_margin", "live_margin")
        if margin is None:
            hs = self._get(ctx, "halftime_home_score", "home_score")
            as_ = self._get(ctx, "halftime_away_score", "away_score")
            if hs is not None and as_ is not None:
                margin = hs - as_
        if margin is None:
            spread = self._get(ctx, "spread", "home_spread")
            margin = spread * pr["spread_to_halftime_margin"] if spread is not None else 0.0

        blowout = pr["blowout_clock_log_scale"] * math.tanh(abs(margin) / pr["blowout_margin_scale"])
        mu = mu0 / max(pace, 1e-9) * math.exp(blowout)
        return mu, pr["drive_duration_cv"]

    def _completion_survival(self, mu: float, cv: float, pr: Dict[str, float]) -> np.ndarray:
        """s_k = P(C >= k), k = 1..K, for the Gamma renewal count of completed drives."""
        shape = 1.0 / (cv * cv)
        scale = mu / shape
        T = pr["quarter_seconds"]
        tol = pr["survival_tolerance"]
        k_bound = int(math.ceil(4.0 * T / mu + 30))
        surv: List[float] = []
        for k in range(1, k_bound + 1):
            s = float(stats.gamma.cdf(T, a=k * shape, scale=scale))
            if s < tol:
                break
            surv.append(s)
        return np.asarray(surv, dtype=float)

    def _home_receives_prob(self, ctx: Dict[str, Any], pr: Dict[str, float]) -> float:
        for key in ("q3_receiving", "second_half_receiving", "second_half_receiver", "h2_kickoff_receiver"):
            val = ctx.get(key)
            if isinstance(val, str):
                v = val.strip().lower()
                if v in ("home", "h"):
                    return 1.0
                if v in ("away", "a"):
                    return 0.0
        # Deferral structure: the team receiving the opening kickoff kicks off in the 2nd half.
        opener = ctx.get("opening_kickoff_receiver")
        if isinstance(opener, str):
            v = opener.strip().lower()
            if v in ("home", "h"):
                return 0.0
            if v in ("away", "a"):
                return 1.0
        p = self._get(ctx, "home_receives_q3_prob")
        return p if p is not None and 0.0 <= p <= 1.0 else pr["default_home_receives_prob"]

    # --------------------------------------------------------- drive model
    def _base_logits(self, pr: Dict[str, float]) -> np.ndarray:
        p = np.array([pr["base_p_td"], pr["base_p_fg_attempt"],
                      pr["base_p_return_td"], pr["base_p_safety"]], dtype=float)
        p_none = 1.0 - p.sum()
        return np.log(p / p_none)

    def _drive_probs(self, x: float, base: np.ndarray, beta_fg: float, f_make: float) -> Dict[str, float]:
        z = np.array([base[0] + x, base[1] + beta_fg * x, base[2] - x, base[3] - x, 0.0])
        p = special.softmax(z)
        return {
            "td": float(p[0]),
            "fg": float(p[1] * f_make),
            "ret": float(p[2]),
            "saf": float(p[3]),
            "none": float(p[4] + p[1] * (1.0 - f_make)),
        }

    def _expected_points(
        self, x_h: float, x_a: float, base: np.ndarray, beta_fg: float, f_make: float,
        td_ev: float, surv: np.ndarray, w_home: float,
    ) -> np.ndarray:
        ph = self._drive_probs(x_h, base, beta_fg, f_make)
        pa = self._drive_probs(x_a, base, beta_fg, f_make)
        off = (ph["td"] * td_ev + 3.0 * ph["fg"], pa["td"] * td_ev + 3.0 * pa["fg"])
        dfn = (ph["ret"] * td_ev + 2.0 * ph["saf"], pa["ret"] * td_ev + 2.0 * pa["saf"])
        keep = (ph["ret"], pa["ret"])

        poss_h, poss_a = w_home, 1.0 - w_home
        e_h = e_a = 0.0
        for s in surv:
            e_h += s * (poss_h * off[0] + poss_a * dfn[1])
            e_a += s * (poss_a * off[1] + poss_h * dfn[0])
            poss_h, poss_a = (
                poss_h * keep[0] + poss_a * (1.0 - keep[1]),
                poss_a * keep[1] + poss_h * (1.0 - keep[0]),
            )
        return np.array([e_h, e_a])

    def _calibrate(
        self, t_h: float, t_a: float, base: np.ndarray, beta_fg: float, f_make: float,
        td_ev: float, surv: np.ndarray, w_home: float, pr: Dict[str, float],
    ) -> Tuple[float, float, np.ndarray]:
        target = np.array([t_h, t_a])
        eps = 1e-12

        def resid(x: np.ndarray) -> np.ndarray:
            e = self._expected_points(x[0], x[1], base, beta_fg, f_make, td_ev, surv, w_home)
            return np.log((e + eps) / (target + eps))

        lim = pr["efficiency_search_radius"]
        sol = optimize.least_squares(
            resid, x0=np.zeros(2), bounds=(-lim * np.ones(2), lim * np.ones(2)),
            xtol=1e-12, ftol=1e-12, gtol=1e-12, method="trf",
        )
        e = self._expected_points(sol.x[0], sol.x[1], base, beta_fg, f_make, td_ev, surv, w_home)
        return float(sol.x[0]), float(sol.x[1]), e

    def _outcomes(self, probs: Dict[str, float], td_vals: np.ndarray) -> List[Tuple[int, int, bool, float]]:
        """(points_to_offense, points_to_defense, offense_keeps_ball_next, probability)."""
        out: List[Tuple[int, int, bool, float]] = []
        for pts, pv in zip((6, 7, 8), td_vals):
            out.append((pts, 0, False, probs["td"] * pv))
            out.append((0, pts, True, probs["ret"] * pv))
        out.append((3, 0, False, probs["fg"]))
        out.append((0, 2, False, probs["saf"]))
        out.append((0, 0, False, probs["none"]))
        return [o for o in out if o[3] > 0.0]

    def _score_lattice(
        self, x_h: float, x_a: float, base: np.ndarray, beta_fg: float, f_make: float,
        td_vals: np.ndarray, surv: np.ndarray, w_home: float,
    ) -> np.ndarray:
        K = len(surv)
        S = 8 * K + 1
        L = np.zeros((2, S, S))
        L[0, 0, 0] = w_home
        L[1, 0, 0] = 1.0 - w_home

        outcomes = (
            self._outcomes(self._drive_probs(x_h, base, beta_fg, f_make), td_vals),
            self._outcomes(self._drive_probs(x_a, base, beta_fg, f_make), td_vals),
        )
        s_full = np.concatenate(([1.0], surv, [0.0]))
        lattice = np.zeros((S, S))

        for k in range(K + 1):
            p_c = s_full[k] - s_full[k + 1]          # P(exactly k completed drives)
            if p_c > 0.0:
                lattice += p_c * L.sum(axis=0)
            if k == K:
                break
            new = np.zeros_like(L)
            for team in (0, 1):
                src = L[team]
                for pts_off, pts_def, keep, prob in outcomes[team]:
                    dh, da = (pts_off, pts_def) if team == 0 else (pts_def, pts_off)
                    dest = team if keep else 1 - team
                    new[dest, dh:, da:] += prob * src[: S - dh, : S - da]
            L = new

        total = lattice.sum()
        return lattice / total if total > 0 else lattice

    # -------------------------------------------------------------- public
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        pr = self._resolve_priors(ctx)

        t_h, t_a = self._targets(ctx, pr)
        f_make = self._fg_make_prob(ctx, pr)
        td_vals = self._td_value_dist(pr)
        td_ev = float(np.dot(td_vals, [6.0, 7.0, 8.0]))
        mu, cv = self._drive_clock(ctx, pr)
        surv = self._completion_survival(mu, cv, pr)
        w_home = self._home_receives_prob(ctx, pr)
        base = self._base_logits(pr)
        beta_fg = pr["fg_efficiency_loading"]

        x_h, x_a, e_fit = self._calibrate(t_h, t_a, base, beta_fg, f_make, td_ev, surv, w_home, pr)
        lattice = self._score_lattice(x_h, x_a, base, beta_fg, f_make, td_vals, surv, w_home)

        p_home = float(np.tril(lattice, -1).sum())   # home index (row) > away index (col)
        p_draw = float(np.trace(lattice))
        p_away = float(np.triu(lattice, 1).sum())
        z = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / z, p_draw / z, p_away / z

        idx = np.arange(lattice.shape[0])
        self.last_lattice = lattice
        self.last_diagnostics = {
            "target_home": t_h,
            "target_away": t_a,
            "fitted_home": float(e_fit[0]),
            "fitted_away": float(e_fit[1]),
            "lattice_mean_home": float((lattice.sum(axis=1) * idx).sum()),
            "lattice_mean_away": float((lattice.sum(axis=0) * idx).sum()),
            "efficiency_home": x_h,
            "efficiency_away": x_a,
            "drive_clock_mean_s": mu,
            "expected_completed_drives": float(surv.sum()),
            "home_receives_prob": w_home,
            "fg_make_prob": f_make,
            "draw_0_0": float(lattice[0, 0]),
            "draw_3_3": float(lattice[3, 3]) if lattice.shape[0] > 3 else 0.0,
            "draw_7_7": float(lattice[7, 7]) if lattice.shape[0] > 7 else 0.0,
        }

        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})


CandidateModel = NCAAFQuarter3DriveLatticeModel