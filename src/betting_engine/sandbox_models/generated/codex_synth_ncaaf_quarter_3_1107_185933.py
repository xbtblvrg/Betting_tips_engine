import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - sandbox pre-loads primitives
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")


class NCAAFQuarter3SpreadsDriveLatticeModel(BaseCandidateModel):
    """
    NCAAF third-quarter spread model on the exact drive-outcome lattice.

    Structure
    ---------
    1. Drive count. The third quarter starts with a kickoff, and possession then
       alternates. So with T drives starting in Q3, the second-half receiver gets
       ceil(T/2) drives and the kicking team gets floor(T/2). This is a hard
       constraint (|N_h - N_a| <= 1). A Poisson split ignores it.
    2. T is the count of a time-bounded renewal process (a 15-minute window filled
       by drives of random length). Renewal theory gives
       Var[T] ~= Lambda * cv_drive^2, which is underdispersed relative to Poisson.
       Game-level tempo heterogeneity adds Lambda^2 * phi_tempo on top. Because T
       is shared, both sides' volumes move together, which is the source of
       positive score correlation. Alternation supplies the negative coupling.
    3. Drive outcomes form a bivariate lattice. Each drive yields one of:
       offensive TD (6/7/8), FG (3), nothing, a safety (2 to the defense), or a
       defensive/return TD (7 to the defense).
    4. Efficiency vs. volume. Deviations of total expected points from the
       empirical anchor (7.00 + 6.25) are split between drive volume (elasticity
       eps) and per-drive scoring. Per-drive scoring probability is then solved
       exactly so that each side's mean equals its target. The TD:FG mix is
       logit-shifted by attack/defense exposure and kicking weather. It affects
       the shape of the margin lattice, not the mean, so the exposures are not
       double-counted against home_expected/away_expected.
    5. Home-field / strength differential. The log-ratio of side means is shrunk
       with weight D/(D + k), where D is the expected Q3 drive count. A
       single-quarter window therefore cannot carry full-game differential
       confidence. The total is preserved.
    6. The exact joint score pmf comes from the 2-D characteristic function:
       phi(w) = sum_T P(T) * sum_receiver q * F_h(w)^{n_h} * F_a(w)^{n_a},
       inverted by 2-D FFT on a lattice large enough that aliasing is negligible.
    """

    # ---- Empirical anchors for this segment ----
    ANCHOR_HOME = 7.00
    ANCHOR_AWAY = 6.25

    # ---- Structural NCAAF domain priors (documented, not tuning clamps) ----
    BASE_Q3_DRIVES = 6.6         # ~26-28 total possessions per game / 4, Q3 has no 2-minute drill
    DRIVE_DURATION_CV = 0.65     # NCAAF drive-length CV (mean ~2.5 min, sd ~1.6 min)
    TEMPO_CV = 0.15              # between-game variability in possession volume
    VOLUME_ELASTICITY = 0.35     # share of log-mean deviation absorbed by drive count
    DIFF_SHRINK_PSEUDO_DRIVES = 1.0  # prior pseudo-drives shrinking the side differential
    TD_SHARE_BASE = 0.64         # TD / (TD + FG) among offensive scoring drives
    TD_SHARE_EXPOSURE_BETA = 0.9 # logit sensitivity of TD share to log exposure
    PAT_MIX = {7: 0.935, 6: 0.040, 8: 0.025}  # PAT good / missed-or-failed 2pt / 2pt good
    P_SAFETY = 0.004             # per-drive safety rate (2 pts to defense)
    P_DEF_TD = 0.015             # per-drive defensive / return TD rate (7 to defense)
    MAX_DRIVE_SCORE_PROB = 0.80  # physical ceiling on offensive scoring per drive
    REST_LOGRATIO_PER_DAY = 0.012
    WIND_FG_THRESHOLD = 10.0     # mph above which FG range degrades
    WIND_FG_RATE = 0.035         # logit shift in TD share per mph of excess wind
    COLD_THRESHOLD_F = 35.0
    COLD_FG_RATE = 0.01
    LATTICE = 128                # FFT grid; per-side support far exceeds plausible Q3 scores
    OUTPUT_MAX_SCORE = 63

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_quarter_3_1107_185933",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "quarter_3",
        family: str = "spreads",
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
        self._last_joint: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _f(value: Any, default: Optional[float]) -> Optional[float]:
        try:
            if value is None:
                return default
            v = float(value)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _rating(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict):
            for k in (key, f"{key}_rating", f"{key}_strength"):
                if k in block:
                    return self._f(block[k], None)
        for k in (f"{side}_{key}", f"{side}_{key}_rating", f"{side}_{key}_strength"):
            if k in ctx:
                return self._f(ctx[k], None)
        return None

    def _log_exposure(self, attack: Optional[float], opp_defense: Optional[float]) -> float:
        """Log offensive exposure: attack strength x opponent defensive leakiness."""
        vals = [v for v in (attack, opp_defense) if v is not None]
        if not vals:
            return 0.0
        if all(v > 0 for v in vals):
            return float(sum(math.log(v) for v in vals))
        return float(sum(vals))  # additive-scale ratings already centred on zero

    def _receiver_prob_home(self, ctx: Dict[str, Any]) -> float:
        rec = ctx.get("second_half_receiver") or ctx.get("q3_receiver")
        if isinstance(rec, str):
            r = rec.strip().lower()
            if r in ("home", "h"):
                return 1.0
            if r in ("away", "a"):
                return 0.0
        opener = ctx.get("opening_kickoff_receiver") or ctx.get("first_half_receiver")
        if isinstance(opener, str):
            r = opener.strip().lower()
            if r in ("home", "h"):
                return 0.0
            if r in ("away", "a"):
                return 1.0
        return 0.5

    def _kicking_logit_shift(self, ctx: Dict[str, Any]) -> float:
        """Adverse kicking conditions shift scoring drives away from FGs."""
        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}

        def pick(key: str) -> Any:
            for src in (ctx, weather, venue):
                if key in src:
                    return src[key]
            return None

        if bool(pick("is_dome")):
            return 0.0
        wind = self._f(pick("wind_speed"), 0.0) or 0.0
        temp = self._f(pick("temp"), None)

        def softplus(x: float) -> float:
            return math.log1p(math.exp(-abs(x))) + max(x, 0.0)

        shift = self.WIND_FG_RATE * softplus(wind - self.WIND_FG_THRESHOLD)
        if temp is not None:
            shift += self.COLD_FG_RATE * softplus(self.COLD_THRESHOLD_F - temp)
        return shift

    # ------------------------------------------------------------------ #
    # Drive-count distribution
    # ------------------------------------------------------------------ #
    def _drive_count_pmf(self, lam: float) -> np.ndarray:
        var = lam * self.DRIVE_DURATION_CV ** 2 + (lam ** 2) * self.TEMPO_CV ** 2
        sd = math.sqrt(max(var, 1e-9))
        t_max = int(math.ceil(lam + 8.0 * sd)) + 2
        t = np.arange(t_max + 1, dtype=float)
        # Continuity-corrected discretised normal over the non-negative integers
        from scipy.stats import norm

        cdf_hi = norm.cdf((t + 0.5 - lam) / sd)
        cdf_lo = norm.cdf((t - 0.5 - lam) / sd)
        pmf = cdf_hi - cdf_lo
        pmf[0] += norm.cdf((-0.5 - lam) / sd)
        return pmf / pmf.sum()

    @staticmethod
    def _expected_split(t_pmf: np.ndarray, q_home: float) -> Tuple[float, float]:
        t = np.arange(len(t_pmf))
        e_ceil = float(np.sum(t_pmf * np.ceil(t / 2.0)))
        e_floor = float(np.sum(t_pmf * np.floor(t / 2.0)))
        e_home = q_home * e_ceil + (1.0 - q_home) * e_floor
        e_away = q_home * e_floor + (1.0 - q_home) * e_ceil
        return e_home, e_away

    # ------------------------------------------------------------------ #
    # Mean targets with differential shrinkage
    # ------------------------------------------------------------------ #
    def _targets(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        mu_h = self._f(ctx.get("home_expected"), self.ANCHOR_HOME) or self.ANCHOR_HOME
        mu_a = self._f(ctx.get("away_expected"), self.ANCHOR_AWAY) or self.ANCHOR_AWAY
        mu_h = max(mu_h, 1e-3)
        mu_a = max(mu_a, 1e-3)

        rest_h = self._f(ctx.get("home_rest_days"), None)
        rest_a = self._f(ctx.get("away_rest_days"), None)
        log_ratio = math.log(mu_h / mu_a)
        if rest_h is not None and rest_a is not None:
            # Second-half fatigue/recovery effect, saturating for bye-week extremes
            log_ratio += self.REST_LOGRATIO_PER_DAY * 7.0 * math.tanh((rest_h - rest_a) / 7.0)

        total = mu_h + mu_a
        drives = self.BASE_Q3_DRIVES * (total / (self.ANCHOR_HOME + self.ANCHOR_AWAY)) ** self.VOLUME_ELASTICITY
        w = drives / (drives + self.DIFF_SHRINK_PSEUDO_DRIVES)
        shrunk = w * log_ratio
        share_h = 1.0 / (1.0 + math.exp(-shrunk))
        return total * share_h, total * (1.0 - share_h)

    # ------------------------------------------------------------------ #
    # Per-drive bivariate outcome lattices
    # ------------------------------------------------------------------ #
    def _drive_grid(self, p_score: float, td_share: float, p_saf: float, p_def: float,
                    offense_is_home: bool) -> np.ndarray:
        n = self.LATTICE
        g = np.zeros((n, n), dtype=float)
        p_td = p_score * td_share
        p_fg = p_score * (1.0 - td_share)

        def put(off_pts: int, def_pts: int, p: float) -> None:
            if offense_is_home:
                g[off_pts, def_pts] += p
            else:
                g[def_pts, off_pts] += p

        for pts, frac in self.PAT_MIX.items():
            put(pts, 0, p_td * frac)
        put(3, 0, p_fg)
        put(0, 2, p_saf)
        put(0, 7, p_def)
        put(0, 0, max(1.0 - p_score - p_saf - p_def, 0.0))
        return g / g.sum()

    def _mean_td_points(self) -> float:
        return float(sum(k * v for k, v in self.PAT_MIX.items()) / sum(self.PAT_MIX.values()))

    # ------------------------------------------------------------------ #
    # Core joint distribution
    # ------------------------------------------------------------------ #
    def joint_score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        mu_h, mu_a = self._targets(ctx)
        total = mu_h + mu_a

        pace = self._f(ctx.get("pace_factor"), 1.0) or 1.0
        pace = pace if pace > 0 else 1.0
        lam = (self.BASE_Q3_DRIVES * pace
               * (total / (self.ANCHOR_HOME + self.ANCHOR_AWAY)) ** self.VOLUME_ELASTICITY)

        q_home = self._receiver_prob_home(ctx)

        # TD:FG mix from exposures and kicking conditions
        kick = self._kicking_logit_shift(ctx)
        base_logit = math.log(self.TD_SHARE_BASE / (1.0 - self.TD_SHARE_BASE))
        exp_h = self._log_exposure(self._rating(ctx, "home", "attack"), self._rating(ctx, "away", "defense"))
        exp_a = self._log_exposure(self._rating(ctx, "away", "attack"), self._rating(ctx, "home", "defense"))

        def sig(x: float) -> float:
            return 1.0 / (1.0 + math.exp(-x))

        r_h = sig(base_logit + self.TD_SHARE_EXPOSURE_BETA * exp_h + kick)
        r_a = sig(base_logit + self.TD_SHARE_EXPOSURE_BETA * exp_a + kick)
        td_pts = self._mean_td_points()
        pts_h = r_h * td_pts + (1.0 - r_h) * 3.0
        pts_a = r_a * td_pts + (1.0 - r_a) * 3.0
        ns_unit = 7.0 * self.P_DEF_TD + 2.0 * self.P_SAFETY

        # Solve per-drive scoring so means match exactly. Raise volume only if the
        # physical per-drive ceiling would otherwise be breached.
        for _ in range(50):
            t_pmf = self._drive_count_pmf(lam)
            n_h, n_a = self._expected_split(t_pmf, q_home)
            # Non-offensive points scaled smoothly so they never exceed a side's mean
            raw_ns_h = n_a * ns_unit
            raw_ns_a = n_h * ns_unit
            ns_h = raw_ns_h * mu_h / (mu_h + raw_ns_h)
            ns_a = raw_ns_a * mu_a / (mu_a + raw_ns_a)
            ps_h = (mu_h - ns_h) / max(n_h * pts_h, 1e-12)
            ps_a = (mu_a - ns_a) / max(n_a * pts_a, 1e-12)
            worst = max(ps_h, ps_a)
            if worst <= self.MAX_DRIVE_SCORE_PROB:
                break
            lam *= worst / self.MAX_DRIVE_SCORE_PROB

        # Defensive event rates realised on each offense's drives
        scale_def_on_away_drives = ns_h / raw_ns_h if raw_ns_h > 0 else 0.0
        scale_def_on_home_drives = ns_a / raw_ns_a if raw_ns_a > 0 else 0.0

        g_h = self._drive_grid(ps_h, r_h, self.P_SAFETY * scale_def_on_home_drives,
                               self.P_DEF_TD * scale_def_on_home_drives, True)
        g_a = self._drive_grid(ps_a, r_a, self.P_SAFETY * scale_def_on_away_drives,
                               self.P_DEF_TD * scale_def_on_away_drives, False)
        F_h = np.fft.fft2(g_h)
        F_a = np.fft.fft2(g_a)

        phi = np.zeros_like(F_h)
        t_max = len(t_pmf) - 1
        pow_h = [np.ones_like(F_h)]
        pow_a = [np.ones_like(F_a)]
        for _ in range((t_max + 1) // 2 + 1):
            pow_h.append(pow_h[-1] * F_h)
            pow_a.append(pow_a[-1] * F_a)

        for t, pt in enumerate(t_pmf):
            if pt <= 0.0:
                continue
            hi, lo = (t + 1) // 2, t // 2
            if q_home > 0.0:
                phi += pt * q_home * pow_h[hi] * pow_a[lo]
            if q_home < 1.0:
                phi += pt * (1.0 - q_home) * pow_h[lo] * pow_a[hi]

        joint = np.real(np.fft.ifft2(phi))
        joint[joint < 0.0] = 0.0  # remove floating-point ringing only
        m = self.OUTPUT_MAX_SCORE + 1
        joint = joint[:m, :m]
        joint /= joint.sum()
        self._last_joint = joint
        return joint

    # ------------------------------------------------------------------ #
    # Spread utilities
    # ------------------------------------------------------------------ #
    @staticmethod
    def margin_pmf(joint: np.ndarray) -> Dict[int, float]:
        n = joint.shape[0]
        out: Dict[int, float] = {}
        for d in range(-(n - 1), n):
            p = float(np.trace(joint, offset=-d))  # home - away = d
            if p > 0.0:
                out[d] = p
        return out

    def cover_probabilities(self, game_context: Dict[str, Any], home_line: float) -> Dict[str, float]:
        """P(home covers), P(push), P(away covers) for a home spread line (e.g. -3.5)."""
        joint = self.joint_score_matrix(game_context)
        win = push = lose = 0.0
        for d, p in self.margin_pmf(joint).items():
            adj = d + home_line
            if abs(adj) < 1e-9:
                push += p
            elif adj > 0:
                win += p
            else:
                lose += p
        return {"home": win, "push": push, "away": lose}

    # ------------------------------------------------------------------ #
    # Required interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.joint_score_matrix(game_context)
        dist_cls = DiscreteDistribution if DiscreteDistribution is not None else globals().get("DiscreteDistribution")

        try:
            return dist_cls.from_scores(joint)
        except Exception:
            pass
        try:
            scores = {
                (int(h), int(a)): float(joint[h, a])
                for h in range(joint.shape[0])
                for a in range(joint.shape[1])
                if joint[h, a] > 1e-12
            }
            return dist_cls.from_scores(scores)
        except Exception:
            pass

        p_home = float(np.tril(joint, -1).sum())
        p_draw = float(np.trace(joint))
        p_away = float(np.triu(joint, 1).sum())
        try:
            return dist_cls.from_probabilities(p_home, p_draw, p_away)
        except Exception:
            return dist_cls(probabilities={"home": p_home, "draw": p_draw, "away": p_away})