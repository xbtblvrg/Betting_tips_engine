"""
Candidate model: NFL | quarter_2 | exact_score

Second-quarter exact score built as a compound lattice process over shared,
alternating possessions.

Structure
---------
1. Total Q2 drives N follow a count law matched to a mean and a variance ratio:
     - shifted binomial when var/mean < 1 (the usual NFL case, because
       alternation and the clock make drive counts underdispersed),
     - Poisson when var/mean == 1,
     - negative binomial when var/mean > 1.
   The mean scales with tempo/pace.
2. Drives alternate. The team that receives first in Q2 gets ceil(N/2) drives
   and the other team gets floor(N/2). The final drive of the half belongs to
   whichever team owns drive index N, and it uses an end-of-half kernel:
   less time to finish, and a higher field-goal share.
3. Each drive emits a joint (own, opponent) points packet:
     - TD   -> {6, 7, 8} via XP make rate and 2PT attempt/success rates
     - FG   -> 3 (make rate depends on weather)
     - safety conceded       -> opponent +2
     - defensive/return TD   -> opponent {6, 7, 8}
   The cross-team packets, together with the shared drive count, create real
   dependence between home and away Q2 scores.
4. A per-team latent efficiency theta_i shifts the probability that a drive
   reaches scoring range, and partly shifts TD share. theta_i is solved by
   root-finding so that E[S_i] equals the situational target exactly.
   Attack/defense exposure, weather and pace change the shape of the score
   distribution (TD/FG mix, drive count). They do not change the calibrated
   mean, so mean information carried by home_expected/away_expected is not
   double counted.
5. P(S_h = a, S_a = b) = sum over (N, first possessor) of P(N) * P(first)
   times the convolution of all drive kernels.

The structural priors are explicit named domain parameters and can be
overridden through the constructor or through game_context['model_priors'].
They are not hidden clamps. With a cold-start sample of zero settled wagers,
no out-of-sample skill is claimed. Validate the model on held-out games
before staking.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live alongside the base class
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - expected to be pre-loaded in scope
    pass


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    eps = 1e-12
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


class NFLQuarter2ExactScoreLatticeModel(BaseCandidateModel):
    """Compound possession-lattice model for NFL second-quarter exact score."""

    DEFAULT_PRIORS: Dict[str, float] = {
        # Empirical anchors (Q2 points per team)
        "anchor_home": 6.12,
        "anchor_away": 5.38,
        # Log-space weight on situational expectations vs. empirical anchor
        "context_weight": 0.85,
        # Fraction of full-game points scored in Q2 (used only when inputs are full-game scale)
        "q2_share": 0.30,
        # Possession law: total Q2 drives (both teams), incl. carry-over and two-minute drives
        "mu_possessions": 5.8,
        "possession_dispersion": 0.45,  # var / mean
        # Drive mechanics
        "reach_base": 0.40,           # P(drive reaches scoring range | no turnover-return/safety)
        "td_given_reach": 0.58,       # P(TD | reached scoring range)
        "fg_make": 0.86,
        "xp_make": 0.955,
        "two_pt_rate": 0.09,
        "two_pt_success": 0.48,
        "safety_rate": 0.004,         # per drive, conceded by offense
        "def_td_rate": 0.009,         # per drive, scored by defense/return unit
        # End-of-half (final drive) adjustments, logit scale
        "last_reach_logit": -0.25,
        "last_td_logit": -0.55,
        # Coupling of latent efficiency and exposure into TD share
        "theta_td_coupling": 0.50,
        "exposure_td_coupling": 0.35,
        # Weather effects (logit scale)
        "wind_ref_mph": 10.0,
        "fg_wind_logit_per_mph": 0.035,
        "xp_wind_logit_per_mph": 0.020,
        "cold_ref_f": 40.0,
        "fg_cold_logit_per_deg": 0.012,
        "dome_fg_logit": 0.10,
        "wind_td_logit_per_mph": 0.010,  # wind pushes offenses to go for TD rather than kick
        # Rest effect on mean (log points per day of rest differential)
        "rest_coef": 0.008,
        # Numerical
        "grid_size": 72,
        "possession_tail_tol": 1e-12,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_2_1108_022307",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_2",
        family: str = "exact_score",
        priors: Optional[Dict[str, float]] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors: Dict[str, float] = dict(self.DEFAULT_PRIORS)
        if priors:
            self.priors.update({k: float(v) for k, v in priors.items() if _to_float(v) is not None})

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _nested(ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        flat = _to_float(ctx.get(f"{side}_{key}"))
        if flat is not None:
            return flat
        block = ctx.get(side)
        if isinstance(block, dict):
            return _to_float(block.get(key))
        return None

    def _priors_for(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        pr = dict(self.priors)
        override = ctx.get("model_priors")
        if isinstance(override, dict):
            for k, v in override.items():
                fv = _to_float(v)
                if fv is not None:
                    pr[k] = fv
        return pr

    def _targets(self, ctx: Dict[str, Any], pr: Dict[str, float]) -> Tuple[float, float]:
        a_h, a_a = pr["anchor_home"], pr["anchor_away"]
        h = _to_float(ctx.get("home_expected"))
        a = _to_float(ctx.get("away_expected"))
        h = a_h if h is None or h <= 0 else h
        a = a_a if a is None or a <= 0 else a

        scope_hint = str(ctx.get("expected_scope", "")).lower()
        full_game = scope_hint in ("full_game", "game", "match", "ft")
        if not scope_hint:
            # Full-game totals are roughly 1 / q2_share times Q2 totals.
            # Classify by the nearer scale in log space.
            q2_total = a_h + a_a
            fg_total = q2_total / pr["q2_share"]
            tot = h + a
            full_game = abs(math.log(tot / fg_total)) < abs(math.log(tot / q2_total))
        if full_game:
            h *= pr["q2_share"]
            a *= pr["q2_share"]

        w = pr["context_weight"]
        h = math.exp(w * math.log(h) + (1.0 - w) * math.log(a_h))
        a = math.exp(w * math.log(a) + (1.0 - w) * math.log(a_a))

        rh = _to_float(ctx.get("home_rest_days"))
        ra = _to_float(ctx.get("away_rest_days"))
        if rh is not None and ra is not None:
            diff = rh - ra
            h *= math.exp(pr["rest_coef"] * diff)
            a *= math.exp(-pr["rest_coef"] * diff)
        return h, a

    def _exposures(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        """Log exposure: attack strength x opponent defensive leakiness (Dixon-Coles convention)."""

        def log_rating(x: Optional[float]) -> float:
            if x is None:
                return 0.0
            if x > 0:
                return math.log(x)  # multiplicative rating around 1
            return x  # additive / already-log rating

        h_att = log_rating(self._nested(ctx, "home", "attack"))
        h_def = log_rating(self._nested(ctx, "home", "defense"))
        a_att = log_rating(self._nested(ctx, "away", "attack"))
        a_def = log_rating(self._nested(ctx, "away", "defense"))
        return h_att + a_def, a_att + h_def

    def _pace_factor(self, ctx: Dict[str, Any]) -> float:
        p = _to_float(ctx.get("pace")) or _to_float(ctx.get("tempo"))
        if p is not None and p > 0:
            return p
        hp = self._nested(ctx, "home", "pace") or self._nested(ctx, "home", "tempo")
        ap = self._nested(ctx, "away", "pace") or self._nested(ctx, "away", "tempo")
        vals = [v for v in (hp, ap) if v is not None and v > 0]
        if not vals:
            return 1.0
        return float(np.exp(np.mean(np.log(vals))))

    def _kicking(self, ctx: Dict[str, Any], pr: Dict[str, float]) -> Tuple[float, float, float]:
        """Return (fg_make, xp_make, td_logit_shift) given weather and venue."""
        is_dome = bool(ctx.get("is_dome", False))
        if not is_dome:
            venue = ctx.get("venue")
            if isinstance(venue, dict):
                is_dome = bool(venue.get("is_dome", False))
        wind = 0.0 if is_dome else (_to_float(ctx.get("wind_speed")) or 0.0)
        temp = None if is_dome else _to_float(ctx.get("temp"))

        excess_wind = max(0.0, wind - pr["wind_ref_mph"])
        cold = 0.0 if temp is None else max(0.0, pr["cold_ref_f"] - temp)

        fg_l = _logit(pr["fg_make"]) - pr["fg_wind_logit_per_mph"] * excess_wind \
            - pr["fg_cold_logit_per_deg"] * cold + (pr["dome_fg_logit"] if is_dome else 0.0)
        xp_l = _logit(pr["xp_make"]) - pr["xp_wind_logit_per_mph"] * excess_wind
        td_shift = pr["wind_td_logit_per_mph"] * excess_wind
        return _sigmoid(fg_l), _sigmoid(xp_l), td_shift

    # ------------------------------------------------------------------ #
    # Possession law
    # ------------------------------------------------------------------ #
    @staticmethod
    def _possession_pmf(mu: float, phi: float, tol: float) -> Tuple[np.ndarray, np.ndarray]:
        var = phi * mu
        if abs(phi - 1.0) < 1e-9:
            n_max = int(stats.poisson.ppf(1.0 - tol, mu)) + 1
            n = np.arange(0, n_max + 1)
            p = stats.poisson.pmf(n, mu)
        elif phi > 1.0:
            r = mu / (phi - 1.0)
            q = r / (r + mu)
            n_max = int(stats.nbinom.ppf(1.0 - tol, r, q)) + 1
            n = np.arange(0, n_max + 1)
            p = stats.nbinom.pmf(n, r, q)
        else:
            # Shifted binomial N = n0 + Bin(m, q), matched to mean and (approximately) variance.
            n0 = max(0, int(math.floor(mu - var - 1.0)))
            span = mu - n0
            q = 1.0 - var / span
            m = max(1, int(round(span / q)))
            q = span / m  # restore exact mean
            k = np.arange(0, m + 1)
            n = n0 + k
            p = stats.binom.pmf(k, m, q)
        p = np.asarray(p, dtype=float)
        keep = p > tol * 1e-3
        n, p = n[keep], p[keep]
        return n.astype(int), p / p.sum()

    # ------------------------------------------------------------------ #
    # Drive kernels
    # ------------------------------------------------------------------ #
    @staticmethod
    def _td_pmf(xp: float, tp_rate: float, tp_succ: float) -> Dict[int, float]:
        return {
            6: (1.0 - tp_rate) * (1.0 - xp) + tp_rate * (1.0 - tp_succ),
            7: (1.0 - tp_rate) * xp,
            8: tp_rate * tp_succ,
        }

    @staticmethod
    def _td_mean(td: Dict[int, float]) -> float:
        return sum(k * v for k, v in td.items())

    def _drive_kernel(
        self,
        r: float,
        t: float,
        fg: float,
        own_td: Dict[int, float],
        opp_td: Dict[int, float],
        s: float,
        d: float,
    ) -> Dict[Tuple[int, int], float]:
        live = 1.0 - s - d
        ker: Dict[Tuple[int, int], float] = {}

        def add(key: Tuple[int, int], p: float) -> None:
            if p > 0:
                ker[key] = ker.get(key, 0.0) + p

        for pts, w in own_td.items():
            add((pts, 0), live * r * t * w)
        add((3, 0), live * r * (1.0 - t) * fg)
        add((0, 2), s)
        for pts, w in opp_td.items():
            add((0, pts), d * w)
        add((0, 0), max(0.0, 1.0 - sum(ker.values())))
        return ker

    @staticmethod
    def _apply_kernel(M: np.ndarray, ker: Dict[Tuple[int, int], float], swap: bool) -> np.ndarray:
        """Convolve a (home, away) grid with a sparse drive kernel. swap=True means an away drive."""
        G = M.shape[0]
        out = np.zeros_like(M)
        for (own, opp), p in ker.items():
            i, j = (opp, own) if swap else (own, opp)
            if i >= G or j >= G:
                continue
            out[i:, j:] += p * M[: G - i, : G - j]
        return out

    # ------------------------------------------------------------------ #
    # Calibration
    # ------------------------------------------------------------------ #
    def _drive_probs(self, theta, base_r, base_t, kappa, exposure_shift, last, pr):
        r = _sigmoid(_logit(base_r) + theta)
        t = _sigmoid(_logit(base_t) + kappa * theta + exposure_shift)
        if last:
            r = _sigmoid(_logit(r) + pr["last_reach_logit"])
            t = _sigmoid(_logit(t) + pr["last_td_logit"])
        return r, t

    def _solve_theta(
        self,
        target_own: float,
        e_norm: float,
        e_last: float,
        own_td_mean: float,
        fg: float,
        s: float,
        d: float,
        exposure_shift: float,
        pr: Dict[str, float],
    ) -> float:
        live = 1.0 - s - d
        kappa = pr["theta_td_coupling"]

        def ppd(theta: float, last: bool) -> float:
            r, t = self._drive_probs(
                theta, pr["reach_base"], pr["td_given_reach"], kappa, exposure_shift, last, pr
            )
            return live * r * (t * own_td_mean + (1.0 - t) * 3.0 * fg)

        def f(theta: float) -> float:
            return e_norm * ppd(theta, False) + e_last * ppd(theta, True) - target_own

        lo, hi = -15.0, 15.0
        flo, fhi = f(lo), f(hi)
        if flo >= 0:
            return lo
        if fhi <= 0:
            return hi
        return optimize.brentq(f, lo, hi, xtol=1e-10, maxiter=200)

    # ------------------------------------------------------------------ #
    # Main entry
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        pr = self._priors_for(ctx)
        G = int(pr["grid_size"])

        target_h, target_a = self._targets(ctx, pr)
        exp_h, exp_a = self._exposures(ctx)
        fg, xp, td_wind_shift = self._kicking(ctx, pr)
        tp_rate, tp_succ = pr["two_pt_rate"], pr["two_pt_success"]
        s, d = pr["safety_rate"], pr["def_td_rate"]

        td_pmf = self._td_pmf(xp, tp_rate, tp_succ)
        td_mean = self._td_mean(td_pmf)

        mu_p = pr["mu_possessions"] * self._pace_factor(ctx)
        n_vals, n_probs = self._possession_pmf(
            mu_p, pr["possession_dispersion"], pr["possession_tail_tol"]
        )

        # First Q2 possessor: informative if supplied, otherwise symmetric.
        fp = str(ctx.get("q2_first_possession", "")).lower()
        if fp == "home":
            first = {"home": 1.0, "away": 0.0}
        elif fp == "away":
            first = {"home": 0.0, "away": 1.0}
        else:
            ph = _to_float(ctx.get("q2_home_first_prob"))
            ph = 0.5 if ph is None else min(max(ph, 0.0), 1.0)
            first = {"home": ph, "away": 1.0 - ph}

        # Expected drive counts by type
        def counts(n: int, f: str) -> Tuple[int, int, int, int]:
            h_cnt = (n + 1) // 2 if f == "home" else n // 2
            a_cnt = n - h_cnt
            if n == 0:
                return 0, 0, 0, 0
            last_is_first = (n % 2) == 1
            last_h = 1 if (last_is_first == (f == "home")) else 0
            last_a = 1 - last_h
            return h_cnt - last_h, last_h, a_cnt - last_a, last_a

        E = np.zeros(4)
        for n, pn in zip(n_vals, n_probs):
            for f, pf in first.items():
                if pf > 0:
                    E += pn * pf * np.array(counts(int(n), f), dtype=float)
        e_norm_h, e_last_h, e_norm_a, e_last_a = E

        # Exogenous points from the opponent's drives (safeties, return TDs)
        exo_to_home = (e_norm_a + e_last_a) * (2.0 * s + d * td_mean)
        exo_to_away = (e_norm_h + e_last_h) * (2.0 * s + d * td_mean)

        shift_h = pr["exposure_td_coupling"] * exp_h + td_wind_shift
        shift_a = pr["exposure_td_coupling"] * exp_a + td_wind_shift

        theta_h = self._solve_theta(
            max(target_h - exo_to_home, 1e-9), e_norm_h, e_last_h, td_mean, fg, s, d, shift_h, pr
        )
        theta_a = self._solve_theta(
            max(target_a - exo_to_away, 1e-9), e_norm_a, e_last_a, td_mean, fg, s, d, shift_a, pr
        )

        kappa = pr["theta_td_coupling"]
        kernels = {}
        for side, theta, shift in (("home", theta_h, shift_h), ("away", theta_a, shift_a)):
            for last in (False, True):
                r, t = self._drive_probs(
                    theta, pr["reach_base"], pr["td_given_reach"], kappa, shift, last, pr
                )
                kernels[(side, last)] = self._drive_kernel(r, t, fg, td_pmf, td_pmf, s, d)

        # Cache home-normal powers, then extend each combination with the remaining kernels
        max_h = int(max(n_vals)) // 2 + 1
        delta = np.zeros((G, G))
        delta[0, 0] = 1.0
        h_pows: List[np.ndarray] = [delta]
        for _ in range(max_h):
            h_pows.append(self._apply_kernel(h_pows[-1], kernels[("home", False)], swap=False))

        cache: Dict[Tuple[int, int, int, int], np.ndarray] = {}
        total = np.zeros((G, G))
        for n, pn in zip(n_vals, n_probs):
            for f, pf in first.items():
                if pf <= 0:
                    continue
                key = counts(int(n), f)
                if key not in cache:
                    nh, lh, na, la = key
                    M = h_pows[nh]
                    if lh:
                        M = self._apply_kernel(M, kernels[("home", True)], swap=False)
                    for _ in range(na):
                        M = self._apply_kernel(M, kernels[("away", False)], swap=True)
                    if la:
                        M = self._apply_kernel(M, kernels[("away", True)], swap=True)
                    cache[key] = M
                total += pn * pf * cache[key]

        total = np.clip(total, 0.0, None)
        mass = total.sum()
        if mass <= 0:
            total = delta.copy()
            mass = 1.0
        total /= mass

        # Trim negligible tail rows/cols for a compact lattice
        tol = 1e-13
        rows = np.where(total.sum(axis=1) > tol)[0]
        cols = np.where(total.sum(axis=0) > tol)[0]
        hmax = int(rows.max()) if rows.size else 0
        amax = int(cols.max()) if cols.size else 0
        matrix = total[: hmax + 1, : amax + 1]
        matrix = matrix / matrix.sum()

        self.last_diagnostics = {
            "target_home": target_h,
            "target_away": target_a,
            "model_mean_home": float((matrix.sum(axis=1) * np.arange(hmax + 1)).sum()),
            "model_mean_away": float((matrix.sum(axis=0) * np.arange(amax + 1)).sum()),
            "theta_home": theta_h,
            "theta_away": theta_a,
            "mu_possessions": mu_p,
            "fg_make": fg,
            "xp_make": xp,
            "p_scoreless": float(matrix[0, 0]),
        }

        try:
            return DiscreteDistribution.from_scores(matrix)  # type: ignore[name-defined]
        except Exception:
            scores = {
                (i, j): float(matrix[i, j])
                for i in range(matrix.shape[0])
                for j in range(matrix.shape[1])
                if matrix[i, j] > 0
            }
            return DiscreteDistribution.from_scores(scores)  # type: ignore[name-defined]