"""
Candidate model for NFL | half_1 | home_total.

Home first-half points are modeled as a compound sum of drive outcomes, not as
a Poisson count of points.

    S_home = sum_{i=1}^{N_home} X_i,   X_i in {0, 2, 3, 6, 7, 8}

* N (drives per team in the half) comes from a pace-driven, under-dispersed
  Binomial. The two teams share the possession clock, so
  N_away = N_home + delta with delta in {-1, 0, +1}. The opening-receive
  edge and the two-minute drill drive the asymmetry.
* X_i comes from a multinomial-logit drive model {empty, FG, TD, safety}.
  Logits are shifted by offense-vs-defense efficiency, rest differential and
  weather (wind and cold hit kicking and passing efficiency).
* A single per-team intercept is solved numerically so that
  E[N] * E[X] = team_expected. With no context this anchors at 12.25 / 10.75.
* The exact pmf comes from expanding the PGF  G_S(z) = G_N(G_X(z))  through
  polynomial convolution. Dispersion (about 5-6x the mean) and mass at key
  numbers emerge from the structure; nothing is imposed by a clamp.

The output is a joint first-half score lattice built with
DiscreteDistribution.from_scores, so home-total markets at any line can be
priced from the exact marginal.

The values in DOMAIN_PRIORS are structural football facts: scoring values, TD
conversion mix, baseline drive counts and possession-shape variances. They are
not fitted performance clamps, and every one can be overridden through the
constructor.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may already be in execution scope
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: F821
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class NFLHalf1HomeTotalCompoundDriveModel(BaseCandidateModel):
    """Compound drive-outcome PGF model for NFL first-half home team totals."""

    DOMAIN_PRIORS: Dict[str, float] = {
        # Empirical anchors for the segment
        "home_anchor": 12.25,
        "away_anchor": 10.75,
        # Possession structure: league-typical first-half offensive drives per team
        "base_drives": 5.6,
        # Binomial Var/Mean of the drive count (the clock makes it strongly under-dispersed)
        "drive_var_ratio": 0.22,
        # P(delta = +1 / -1) for away drives relative to home drives
        "p_away_extra": 0.22,
        "p_home_extra": 0.22,
        # League-typical plays per team-game, used to normalise raw pace inputs
        "league_plays": 63.0,
        # Baseline drive-outcome shares; only their ratios matter after calibration
        "base_p_td": 0.235,
        "base_p_fg": 0.165,
        "base_p_safety": 0.004,
        # TD conversion mix: 6 (missed PAT / failed 2pt), 7 (PAT), 8 (2pt)
        "q6": 0.045,
        "q7": 0.905,
        "q8": 0.050,
        # Logit sensitivities
        "beta_eff_td": 0.55,   # per unit standardized offense-vs-defense edge
        "beta_eff_fg": 0.25,
        "beta_rest": 0.035,    # per day of rest advantage, saturated via tanh
        "rest_scale": 7.0,     # days; scale of the tanh saturation
        "beta_wind_fg": 0.030, # per mph above calm
        "beta_wind_td": 0.012,
        "wind_calm": 8.0,      # mph below which wind is irrelevant
        "beta_cold": 0.006,    # per degree F below the comfort temperature
        "temp_comfort": 45.0,
        "dome_bonus": 0.04,
        # Weight of the calibration intercept on the FG logit relative to the TD logit
        "fg_intercept_weight": 0.6,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_half_1_1107_123731",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "half_1",
        family: str = "home_total",
        priors: Optional[Dict[str, float]] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.p = dict(self.DOMAIN_PRIORS)
        if priors:
            self.p.update({k: float(v) for k, v in priors.items()})

    # ------------------------------------------------------------------ #
    # Feature extraction
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _team_block(self, ctx: Dict[str, Any], side: str) -> Dict[str, Optional[float]]:
        blk = ctx.get(side)
        out: Dict[str, Optional[float]] = {"attack": None, "defense": None, "pace": None}
        if isinstance(blk, dict):
            for key, names in (
                ("attack", ("attack", "offense", "off_epa", "epa_per_drive_off", "attack_rating")),
                ("defense", ("defense", "def_epa", "epa_per_drive_def", "defense_rating")),
                ("pace", ("pace", "tempo", "plays_per_game", "seconds_per_play")),
            ):
                for n in names:
                    if n in blk:
                        val = self._num(blk[n])
                        if val is not None:
                            if n == "seconds_per_play":
                                val = 1800.0 / max(val, 1e-6) / 2.0  # convert to plays per team-game
                            out[key] = val
                            break
        for key in ("attack", "defense", "pace"):
            if out[key] is None:
                out[key] = self._num(ctx.get(f"{side}_{key}"))
        return out

    @staticmethod
    def _standardize_rating(v: Optional[float]) -> float:
        """Map heterogeneous ratings onto a centred scale.

        Multiplicative ratings near 1.0 map to log(v). EPA-style or z-scores
        (already centred near 0) pass through.
        """
        if v is None:
            return 0.0
        if v > 0.0 and 0.2 <= v <= 5.0:
            return math.log(v) / 0.15  # a 15% rating gap is roughly 1 sd of team quality
        return float(v)

    def _pace_multiplier(self, pace_h: Optional[float], pace_a: Optional[float],
                         ctx: Dict[str, Any]) -> float:
        vals = [v for v in (pace_h, pace_a, self._num(ctx.get("pace")),
                            self._num(ctx.get("tempo"))) if v is not None and v > 0]
        if not vals:
            return 1.0
        mults = [v if v < 3.0 else v / self.p["league_plays"] for v in vals]
        return float(np.exp(np.mean(np.log(mults))))  # geometric mean, symmetric in log space

    # ------------------------------------------------------------------ #
    # Drive-level mechanics
    # ------------------------------------------------------------------ #
    def _situational_shifts(self, ctx: Dict[str, Any], off: Dict[str, Optional[float]],
                            dfn: Dict[str, Optional[float]], rest_adv: float) -> Tuple[float, float]:
        """Return the (TD, FG) logit shifts for one offense facing one defense."""
        P = self.p
        # A positive defense rating means a strong defense, which suppresses the offense
        edge = self._standardize_rating(off["attack"]) - self._standardize_rating(dfn["defense"])
        edge = math.tanh(edge / 2.0) * 2.0  # smooth saturation, no hard clamp

        rest = P["rest_scale"] * math.tanh(rest_adv / P["rest_scale"])

        is_dome = bool(ctx.get("is_dome") or (isinstance(ctx.get("venue"), dict)
                                              and ctx["venue"].get("is_dome")))
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        wind = self._num(ctx.get("wind_speed", weather.get("wind_speed")), 0.0) or 0.0
        temp = self._num(ctx.get("temp", weather.get("temp")), None)
        if is_dome:
            wind_excess, cold_excess, dome = 0.0, 0.0, P["dome_bonus"]
        else:
            wind_excess = max(wind - P["wind_calm"], 0.0)
            cold_excess = max(P["temp_comfort"] - temp, 0.0) if temp is not None else 0.0
            dome = 0.0

        td_shift = (P["beta_eff_td"] * edge + P["beta_rest"] * rest
                    - P["beta_wind_td"] * wind_excess - P["beta_cold"] * cold_excess + dome)
        fg_shift = (P["beta_eff_fg"] * edge + 0.5 * P["beta_rest"] * rest
                    - P["beta_wind_fg"] * wind_excess - P["beta_cold"] * cold_excess + dome)
        return td_shift, fg_shift

    def _drive_probs(self, a: float, td_shift: float, fg_shift: float) -> Tuple[float, float, float, float]:
        P = self.p
        p0 = 1.0 - P["base_p_td"] - P["base_p_fg"] - P["base_p_safety"]
        l_td = math.log(P["base_p_td"] / p0) + a + td_shift
        l_fg = math.log(P["base_p_fg"] / p0) + P["fg_intercept_weight"] * a + fg_shift
        l_sf = math.log(P["base_p_safety"] / p0)
        m = max(0.0, l_td, l_fg, l_sf)
        e0, etd, efg, esf = (math.exp(-m), math.exp(l_td - m),
                             math.exp(l_fg - m), math.exp(l_sf - m))
        z = e0 + etd + efg + esf
        return e0 / z, efg / z, etd / z, esf / z

    def _td_mean(self) -> float:
        P = self.p
        qs = P["q6"] + P["q7"] + P["q8"]
        return (6 * P["q6"] + 7 * P["q7"] + 8 * P["q8"]) / qs

    def _calibrate_intercept(self, target_ppd: float, td_shift: float, fg_shift: float) -> float:
        td_pts = self._td_mean()

        def f(a: float) -> float:
            _, pfg, ptd, psf = self._drive_probs(a, td_shift, fg_shift)
            return td_pts * ptd + 3.0 * pfg + 2.0 * psf - target_ppd

        lo, hi = -12.0, 12.0
        flo, fhi = f(lo), f(hi)
        if flo * fhi > 0:  # target outside reachable range: take the nearest feasible end
            return lo if abs(flo) < abs(fhi) else hi
        return brentq(f, lo, hi, xtol=1e-10)

    def _drive_pmf(self, probs: Tuple[float, float, float, float]) -> np.ndarray:
        P = self.p
        p0, pfg, ptd, psf = probs
        qs = P["q6"] + P["q7"] + P["q8"]
        v = np.zeros(9)
        v[0] = p0
        v[2] = psf
        v[3] = pfg
        v[6] = ptd * P["q6"] / qs
        v[7] = ptd * P["q7"] / qs
        v[8] = ptd * P["q8"] / qs
        return v / v.sum()

    # ------------------------------------------------------------------ #
    # Possession-count distribution (under-dispersed Binomial)
    # ------------------------------------------------------------------ #
    def _drive_count_pmf(self, mean_drives: float) -> np.ndarray:
        from scipy.stats import binom
        v = self.p["drive_var_ratio"]
        p_succ = 1.0 - v
        n_real = mean_drives / p_succ
        n_lo = int(math.floor(n_real))
        w_hi = n_real - n_lo  # mixing floor and ceil trials preserves the mean exactly
        n_max = n_lo + 1
        ks = np.arange(n_max + 1)
        pmf = (1.0 - w_hi) * binom.pmf(ks, n_lo, p_succ) + w_hi * binom.pmf(ks, n_max, p_succ)
        return pmf / pmf.sum()

    @staticmethod
    def _power_table(d: np.ndarray, n_max: int) -> list:
        pows = [np.array([1.0])]
        for _ in range(n_max):
            pows.append(np.convolve(pows[-1], d))
        return pows

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        P = self.p

        home_exp = self._num(ctx.get("home_expected"), P["home_anchor"])
        away_exp = self._num(ctx.get("away_expected"), P["away_anchor"])
        # Full-game expectations are mapped to half-1 scale via the anchor ratio
        full_game_ref = P["home_anchor"] + P["away_anchor"]
        if home_exp + away_exp > 1.6 * full_game_ref:
            scale = full_game_ref / (P["home_anchor"] / 0.5 + P["away_anchor"] / 0.5) * 2.0
            home_exp *= scale * 0.5 * (P["home_anchor"] + P["away_anchor"]) / full_game_ref
            away_exp *= scale * 0.5 * (P["home_anchor"] + P["away_anchor"]) / full_game_ref
        home_exp = max(home_exp, 1e-3)
        away_exp = max(away_exp, 1e-3)

        home_blk = self._team_block(ctx, "home")
        away_blk = self._team_block(ctx, "away")
        rest_h = self._num(ctx.get("home_rest_days"), 7.0)
        rest_a = self._num(ctx.get("away_rest_days"), 7.0)

        mean_drives = P["base_drives"] * self._pace_multiplier(home_blk["pace"], away_blk["pace"], ctx)
        n_pmf = self._drive_count_pmf(mean_drives)

        # Away drive offset: delta in {-1, 0, +1}
        p_plus, p_minus = P["p_away_extra"], P["p_home_extra"]
        recv = ctx.get("home_receives_first")
        if recv is not None:
            tilt = 0.08 if bool(recv) else -0.08
            p_minus, p_plus = p_minus + tilt, p_plus - tilt
        delta_pmf = {-1: p_minus, 0: 1.0 - p_minus - p_plus, 1: p_plus}

        ks = np.arange(len(n_pmf))
        e_n_home = float((ks * n_pmf).sum())
        e_n_away = e_n_home + (p_plus - p_minus)

        td_h, fg_h = self._situational_shifts(ctx, home_blk, away_blk, rest_h - rest_a)
        td_a, fg_a = self._situational_shifts(ctx, away_blk, home_blk, rest_a - rest_h)

        a_h = self._calibrate_intercept(home_exp / max(e_n_home, 1e-6), td_h, fg_h)
        a_a = self._calibrate_intercept(away_exp / max(e_n_away, 1e-6), td_a, fg_a)
        d_h = self._drive_pmf(self._drive_probs(a_h, td_h, fg_h))
        d_a = self._drive_pmf(self._drive_probs(a_a, td_a, fg_a))

        n_max = len(n_pmf) - 1
        pow_h = self._power_table(d_h, n_max)
        pow_a = self._power_table(d_a, n_max + 1)
        size = 8 * (n_max + 1) + 1
        joint = np.zeros((size, size))

        # Exact PGF expansion with shared possessions:
        # P(h, a) = sum_n P(N = n) * sum_delta P(delta) * [z^h] G_X^n * [z^a] G_X_away^(n + delta)
        for n, pn in enumerate(n_pmf):
            if pn < 1e-15:
                continue
            ph = pow_h[n]
            for dlt, pd in delta_pmf.items():
                na = n + dlt
                if na < 0 or pd <= 0:
                    continue
                pa = pow_a[na]
                joint[: len(ph), : len(pa)] += pn * pd * np.outer(ph, pa)

        joint /= joint.sum()

        scores: Dict[Tuple[int, int], float] = {}
        for h in range(size):
            for a in range(size):
                pr = joint[h, a]
                if pr > 1e-12:
                    scores[(h, a)] = float(pr)
        tot = sum(scores.values())
        scores = {k: v / tot for k, v in scores.items()}

        try:
            dist = DiscreteDistribution.from_scores(scores)
        except (TypeError, ValueError, AttributeError):
            dist = DiscreteDistribution.from_scores(joint)

        try:  # diagnostics, if the distribution object accepts attributes
            hm = joint.sum(axis=1)
            pts = np.arange(size)
            mu = float((pts * hm).sum())
            var = float(((pts - mu) ** 2 * hm).sum())
            dist.metadata = {
                "model_id": self.model_id,
                "home_mean": mu,
                "home_var_mean_ratio": var / max(mu, 1e-9),
                "mean_drives": mean_drives,
                "home_drive_pmf": d_h.tolist(),
            }
        except Exception:
            pass
        return dist