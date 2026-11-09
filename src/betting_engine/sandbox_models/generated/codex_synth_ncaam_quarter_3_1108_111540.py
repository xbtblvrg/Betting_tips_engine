import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution is normally pre-loaded into execution scope.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: B018
except NameError:  # pragma: no cover - fallback import paths
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except ImportError:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class NCAAMQuarter3ExactScoreModel(BaseCandidateModel):
    """
    Possession-lattice exact-score model for NCAAM 'quarter_3'.

    Settlement-window caveat
    ------------------------
    NCAAM is played in two 20-minute halves, so 'quarter_3' is not a native
    period. This model assumes 'quarter_3' = minutes 0-10 of the second half.
    The window is fully parameterised (half, start minute within the half, and
    length) and can be overridden per game via game_context. If the operator's
    settlement definition differs, the segment should be suspended or the
    window corrected; ``last_diagnostics['window_verified']`` reports whether
    the context asserted a verified mapping.

    Generative structure
    --------------------
    1. Shared possession count T for the window. Possessions alternate, so T is
       tightly concentrated: a discretised Normal with Var(T) = d * E[T], d < 1
       (underdispersed relative to Poisson).
    2. Possessions split ceil(T/2) / floor(T/2), with the larger share going
       to the team holding first possession (alternating-arrow team, or a fair
       coin if unknown). This induces positive score correlation.
    3. Each possession yields points in {0, 1, 2, 3} from a categorical built
       out of shot mechanics (2PM, 3PM, two-shot FT trips with FT% f). The
       categorical is solved so that E[T]/2 * ppp equals each team's
       situationally adjusted expectation.
    4. The free-throw share of points is reduced by the halftime team-foul
       reset. This is derived from a Poisson foul clock: the fraction of the
       window spent in one-and-one/double bonus versus the half average.
    5. A per-game latent efficiency multiplier (mean 1, log-normal, via
       Gauss-Hermite quadrature) captures shooting variance, which fattens
       exact-score tails relative to a pure multinomial.

    All domain priors live in DOMAIN_PRIORS and can be overridden through the
    constructor or per game via game_context['model_priors'].
    """

    DOMAIN_PRIORS: Dict[str, float] = {
        # League scoring structure (D-I men's, full game, per team)
        "league_ppp": 1.04,              # points per possession
        "league_pace": 67.5,             # possessions per team per 40 minutes
        "share_points_3pt": 0.31,        # share of points from made 3s
        "share_points_ft": 0.19,         # share of points from free throws
        "ft_pct": 0.715,
        "bonus_share_of_ft_points": 0.40,  # FT points from non-shooting bonus fouls
        "team_fouls_per_minute": 0.44,
        "one_and_one_threshold": 7,
        "double_bonus_threshold": 10,
        "half_minutes": 20.0,
        "game_minutes": 40.0,
        # Window definition ('quarter_3' -> second half, minutes 0-10)
        "window_half": 2,
        "window_start_minute": 0.0,
        "window_minutes": 10.0,
        # Possession process
        "possession_dispersion": 0.35,   # Var(T)/E[T]; < 1 because possessions alternate
        "log_sd_pace_signal": 0.06,      # reliability of a supplied pace figure
        "log_sd_ppp_prior": 0.09,        # reliability of league ppp for inferring T
        "min_empty_possession_rate": 0.25,
        # Efficiency variance and situational adjustments
        "efficiency_log_sd": 0.10,       # per-game shooting-luck dispersion
        "quadrature_nodes": 7,
        "matchup_residual_weight": 0.25,  # expectations already embed most matchup info
        "rating_reference": 100.0,       # reference for efficiency-scale ratings
        "rest_log_effect": 0.015,        # max log-efficiency gain from full rest
        "rest_scale_days": 2.0,
        "support_sd_multiple": 7.0,
        "cell_floor": 1e-12,             # numerical pruning only; mass is renormalised
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_3_1108_111540",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_3",
        family: str = "exact_score",
        priors: Optional[Dict[str, float]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )
        self.base_priors: Dict[str, float] = dict(self.DOMAIN_PRIORS)
        if priors:
            self.base_priors.update(priors)
        self.baseline_home_expected = 20.00
        self.baseline_away_expected = 18.75
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Context helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _num(self, ctx: Dict[str, Any], keys: List[str]) -> Optional[float]:
        for k in keys:
            if k in ctx:
                v = self._as_float(ctx.get(k))
                if v is not None:
                    return v
        return None

    def _side(self, ctx: Dict[str, Any], side: str, key: str) -> Optional[float]:
        block = ctx.get(side)
        if isinstance(block, dict) and key in block:
            v = self._as_float(block.get(key))
            if v is not None:
                return v
        return self._num(ctx, [f"{side}_{key}"])

    def _rating_log(self, value: Optional[float], pr: Dict[str, float]) -> float:
        """Map a rating to log-multiplier units (0 = league average).

        Accepts multiplicative indices (~1.0), additive log-style deltas (~0.0),
        or efficiency-scale numbers (~100 per 100 possessions).
        """
        if value is None:
            return 0.0
        if 0.5 < value < 2.0:
            return math.log(value)
        if abs(value) <= 0.5:
            return value
        if value > 0:
            return math.log(value / pr["rating_reference"])
        return 0.0

    # ------------------------------------------------------------------ #
    # Mechanics
    # ------------------------------------------------------------------ #
    @staticmethod
    def _avg_over_window(fn, start: float, length: float, n: int = 801) -> float:
        t = np.linspace(start, start + length, n)
        y = fn(t)
        return float((y[:-1] + y[1:]).sum() / (2.0 * (n - 1)))

    def _bonus_intensity(self, start: float, length: float, pr: Dict[str, float]) -> float:
        """Average FT-attempt intensity from the bonus over a window of the half.

        Opponent team fouls follow a Poisson clock that resets at halftime.
        One-and-one yields (1 + f) expected attempts versus 2 in double bonus,
        so it is weighted by (1 + f) / 2.
        """
        lam = pr["team_fouls_per_minute"]
        f = pr["ft_pct"]
        k1 = int(pr["one_and_one_threshold"])
        k2 = int(pr["double_bonus_threshold"])
        a1 = (1.0 + f) / 2.0

        def intensity(t: np.ndarray) -> np.ndarray:
            p1 = stats.poisson.sf(k1 - 1, lam * t)
            p2 = stats.poisson.sf(k2 - 1, lam * t)
            return a1 * (p1 - p2) + p2

        return self._avg_over_window(intensity, start, length)

    def _window_shares(self, pr: Dict[str, float]) -> Tuple[float, float, float, float]:
        start = pr["window_start_minute"]
        length = pr["window_minutes"]
        half = pr["half_minutes"]
        b_win = self._bonus_intensity(start, length, pr)
        b_half = self._bonus_intensity(0.0, half, pr)
        ratio = b_win / b_half if b_half > 0 else 1.0
        b = pr["bonus_share_of_ft_points"]
        s_ft_raw = pr["share_points_ft"] * ((1.0 - b) + b * ratio)

        # ppp suppression relative to full-game league ppp
        ppp_factor = 1.0 - pr["share_points_ft"] + s_ft_raw

        # Renormalised point shares within the window
        s3_full = pr["share_points_3pt"]
        s2_full = 1.0 - pr["share_points_3pt"] - pr["share_points_ft"]
        tot = s3_full + s2_full + s_ft_raw
        return s3_full / tot, s2_full / tot, s_ft_raw / tot, ppp_factor

    @staticmethod
    def _usage_per_point(s3: float, s2: float, sft: float, f: float) -> float:
        # Scoring events per point of expected output
        return s3 / 3.0 + s2 / 2.0 + sft / (2.0 * f)

    @staticmethod
    def _possession_categorical(
        ppp: float, s3: float, s2: float, sft: float, f: float
    ) -> Tuple[np.ndarray, bool]:
        r3 = ppp * s3 / 3.0
        r2 = ppp * s2 / 2.0
        rft = ppp * sft / (2.0 * f)
        p = np.zeros(4)
        p[3] = r3
        p[2] = r2 + rft * f * f
        p[1] = rft * 2.0 * f * (1.0 - f)
        p[0] = rft * (1.0 - f) ** 2
        used = r3 + r2 + rft
        feasible = used <= 1.0
        if feasible:
            p[0] += 1.0 - used
        else:
            p /= p.sum()
        return p, feasible

    @staticmethod
    def _power_pmfs(p: np.ndarray, n_max: int) -> List[np.ndarray]:
        out = [np.array([1.0])]
        cur = out[0]
        for _ in range(n_max):
            cur = np.convolve(cur, p)
            out.append(cur)
        return out

    # ------------------------------------------------------------------ #
    # Core prediction
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        pr = dict(self.base_priors)
        overrides = ctx.get("model_priors")
        if isinstance(overrides, dict):
            for k, v in overrides.items():
                fv = self._as_float(v)
                if fv is not None:
                    pr[k] = fv
        for ctx_key, pr_key in (
            ("window_start_minute", "window_start_minute"),
            ("window_minutes", "window_minutes"),
            ("window_half", "window_half"),
        ):
            v = self._num(ctx, [ctx_key])
            if v is not None:
                pr[pr_key] = v

        f = pr["ft_pct"]

        # ---- 1. Situational point expectations --------------------------
        mu_h = self._num(ctx, ["home_expected"])
        mu_a = self._num(ctx, ["away_expected"])
        mu_h = mu_h if mu_h is not None and mu_h > 0 else self.baseline_home_expected
        mu_a = mu_a if mu_a is not None and mu_a > 0 else self.baseline_away_expected

        w = pr["matchup_residual_weight"]
        atk_h = self._rating_log(self._side(ctx, "home", "attack"), pr)
        atk_a = self._rating_log(self._side(ctx, "away", "attack"), pr)
        # Defense is interpreted as points-allowed exposure (> average = leakier).
        def_h = self._rating_log(self._side(ctx, "home", "defense"), pr)
        def_a = self._rating_log(self._side(ctx, "away", "defense"), pr)
        log_adj_h = w * (atk_h + def_a)
        log_adj_a = w * (atk_a + def_h)

        def rest_gain(days: Optional[float]) -> float:
            if days is None or days < 0:
                return 0.0
            return 1.0 - math.exp(-days / pr["rest_scale_days"])

        rest_delta = pr["rest_log_effect"] * (
            rest_gain(self._num(ctx, ["home_rest_days"]))
            - rest_gain(self._num(ctx, ["away_rest_days"]))
        )
        log_adj_h += rest_delta / 2.0
        log_adj_a -= rest_delta / 2.0
        mu_h *= math.exp(log_adj_h)
        mu_a *= math.exp(log_adj_a)

        # ---- 2. Window shot mix and foul-reset suppression --------------
        s3, s2, sft, ppp_factor = self._window_shares(pr)
        k_use = self._usage_per_point(s3, s2, sft, f)
        window_frac = pr["window_minutes"] / pr["game_minutes"]

        # ---- 3. Possessions per team: precision-weighted pace vs points --
        ppp_prior = pr["league_ppp"] * ppp_factor
        tau_from_points = (mu_h + mu_a) / (2.0 * ppp_prior)

        pace = self._num(ctx, ["pace", "tempo", "expected_pace", "possessions"])
        if pace is None:
            t_h = self._side(ctx, "home", "tempo") or self._side(ctx, "home", "pace")
            t_a = self._side(ctx, "away", "tempo") or self._side(ctx, "away", "pace")
            if t_h and t_a and t_h > 0 and t_a > 0:
                pace = t_h * t_a / pr["league_pace"]
        if pace is not None and pace > 0:
            tau_pace = pace * window_frac
            prec_pace = 1.0 / pr["log_sd_pace_signal"] ** 2
            prec_pts = 1.0 / pr["log_sd_ppp_prior"] ** 2
            log_tau = (prec_pace * math.log(tau_pace) + prec_pts * math.log(tau_from_points)) / (
                prec_pace + prec_pts
            )
            tau = math.exp(log_tau)
        else:
            tau = tau_from_points

        # Feasibility: possession count must support the scoring rate at the
        # top efficiency quadrature node without exhausting empty possessions.
        sigma = pr["efficiency_log_sd"]
        n_nodes = max(int(pr["quadrature_nodes"]), 1)
        z, wq = np.polynomial.hermite_e.hermegauss(n_nodes)
        wq = wq / wq.sum()
        mults = np.exp(sigma * z - 0.5 * sigma * sigma)
        tau_min = max(mu_h, mu_a) * float(mults.max()) * k_use / (
            1.0 - pr["min_empty_possession_rate"]
        )
        possession_floor_binding = tau < tau_min
        tau = max(tau, tau_min)

        # ---- 4. Shared possession count distribution --------------------
        mean_T = 2.0 * tau
        sd_T = math.sqrt(pr["possession_dispersion"] * mean_T)
        span = pr["support_sd_multiple"] * sd_T
        t_lo = max(0, int(math.floor(mean_T - span)))
        t_hi = int(math.ceil(mean_T + span))
        ts = np.arange(t_lo, t_hi + 1)
        cdf_hi = stats.norm.cdf(ts + 0.5, mean_T, sd_T)
        cdf_lo = stats.norm.cdf(ts - 0.5, mean_T, sd_T)
        p_T = cdf_hi - cdf_lo
        p_T /= p_T.sum()
        e_T = float((ts * p_T).sum())

        # Points per possession that reproduce the targets exactly
        ppp_h = mu_h / (e_T / 2.0)
        ppp_a = mu_a / (e_T / 2.0)

        # ---- 5. Per-team possession pmfs with efficiency mixture --------
        n_max = int(math.ceil(t_hi / 2.0))
        infeasible_nodes = 0

        def mixed_pmfs(ppp: float) -> List[np.ndarray]:
            nonlocal infeasible_nodes
            mix = [np.zeros(3 * n + 1) for n in range(n_max + 1)]
            for m, wt in zip(mults, wq):
                p, ok = self._possession_categorical(ppp * m, s3, s2, sft, f)
                if not ok:
                    infeasible_nodes += 1
                for n, pmf in enumerate(self._power_pmfs(p, n_max)):
                    mix[n] += wt * pmf
            return mix

        home_pmf = mixed_pmfs(ppp_h)
        away_pmf = mixed_pmfs(ppp_a)

        # ---- 6. Joint score lattice -------------------------------------
        q_first = 0.5
        fp = ctx.get("home_first_possession", ctx.get("home_second_half_possession"))
        if isinstance(fp, bool):
            q_first = 1.0 if fp else 0.0
        else:
            fpv = self._as_float(fp)
            if fpv is not None and 0.0 <= fpv <= 1.0:
                q_first = fpv

        size = 3 * n_max + 1
        joint = np.zeros((size, size))
        for t, pt in zip(ts, p_T):
            hi, lo = (int(t) + 1) // 2, int(t) // 2
            if q_first > 0:
                h, a = home_pmf[hi], away_pmf[lo]
                joint[: h.size, : a.size] += pt * q_first * np.outer(h, a)
            if q_first < 1:
                h, a = home_pmf[lo], away_pmf[hi]
                joint[: h.size, : a.size] += pt * (1.0 - q_first) * np.outer(h, a)
        joint /= joint.sum()

        # ---- 7. Emit distribution + diagnostics -------------------------
        floor = pr["cell_floor"]
        idx_h, idx_a = np.nonzero(joint > floor)
        kept = joint[idx_h, idx_a]
        kept = kept / kept.sum()
        scores: Dict[Tuple[int, int], float] = {
            (int(h), int(a)): float(p) for h, a, p in zip(idx_h, idx_a, kept)
        }

        hs = np.arange(size)
        m_h = float((joint.sum(axis=1) * hs).sum())
        m_a = float((joint.sum(axis=0) * hs).sum())
        v_h = float((joint.sum(axis=1) * (hs - m_h) ** 2).sum())
        v_a = float((joint.sum(axis=0) * (hs - m_a) ** 2).sum())
        cov = float((joint * np.outer(hs - m_h, hs - m_a)).sum())
        mode = np.unravel_index(int(np.argmax(joint)), joint.shape)

        self.last_diagnostics = {
            "window": (int(pr["window_half"]), pr["window_start_minute"], pr["window_minutes"]),
            "window_verified": bool(ctx.get("scope_mapping_verified", False)),
            "target_means": (mu_h, mu_a),
            "model_means": (m_h, m_a),
            "dispersion_index": (v_h / m_h if m_h else float("nan"),
                                 v_a / m_a if m_a else float("nan")),
            "score_correlation": cov / math.sqrt(v_h * v_a) if v_h > 0 and v_a > 0 else 0.0,
            "possessions_per_team": tau,
            "expected_total_possessions": e_T,
            "ppp": (ppp_h, ppp_a),
            "implied_full_game_ppp_equivalent": (ppp_h / ppp_factor, ppp_a / ppp_factor),
            "ft_share_window": sft,
            "ppp_foul_reset_factor": ppp_factor,
            "possession_floor_binding": possession_floor_binding,
            "infeasible_quadrature_nodes": infeasible_nodes,
            "modal_score": (int(mode[0]), int(mode[1])),
            "modal_probability": float(joint[mode]),
            "pruned_mass": float(1.0 - joint[idx_h, idx_a].sum()),
        }

        return DiscreteDistribution.from_scores(scores)

    # ------------------------------------------------------------------ #
    # Wager gating (edge must exceed the book's overround)
    # ------------------------------------------------------------------ #
    @staticmethod
    def market_overround(decimal_odds: List[float]) -> float:
        valid = [o for o in decimal_odds if o and o > 1.0]
        return sum(1.0 / o for o in valid) - 1.0 if valid else 0.0

    def bet_signal(
        self,
        model_probability: float,
        decimal_odds: float,
        market_overround: float,
        min_odds: float = 1.40,
    ) -> Dict[str, float]:
        """Continuous EV evaluation; eligible only if EV exceeds the overround."""
        ev = model_probability * decimal_odds - 1.0
        hurdle = max(market_overround, 0.0)
        eligible = decimal_odds >= min_odds and ev > hurdle
        b = decimal_odds - 1.0
        kelly = (model_probability * b - (1.0 - model_probability)) / b if b > 0 else 0.0
        return {
            "expected_value": ev,
            "hurdle": hurdle,
            "eligible": float(eligible),
            "kelly_fraction": kelly if eligible else 0.0,
        }