"""
Candidate model for NCAAW | quarter_3 | spreads.

Structure
---------
Points in a 10-minute NCAAW third quarter are modeled as a compound process:

    N            ~ shared possession count (pace), mean-variance matched
                   (Binomial when under-dispersed, NegBin when over-dispersed)
    N_home       = N
    N_away       = N + D,  D in {-1, 0, +1}  (alternating-possession / end-of-quarter
                   parity jitter)
    X_i (team)   ~ i.i.d. per-possession outcome on {0, 1, 2, 3}

Per-possession pmfs are an exponential tilt of a structural shape prior
p_k ∝ w_k * exp(theta * k). theta is solved so that the expected points per
possession reproduce each team's target mean. This is the max-entropy pmf
consistent with the target, and it is feasible for any PPP in (0, 3) without clamps.

The joint score matrix is exact:

    J = Σ_n P(N=n) Σ_d P(D=d) · outer(X_home^{*n}, X_away^{*(n+d)})

The margin pmf therefore keeps the true lattice mass at ±1/±2/±3 and the push
probability at integer lines. Shared pace couples the two scores positively.
Lumpy scoring (2s and 3s) inflates per-team Var/Mean to about 2. Both effects
come out of the mechanics; neither is imposed.

Home-edge treatment (Q3-specific)
---------------------------------
The input gap g = home_expected - away_expected is split into a venue component
(league HFA per quarter) and a team-quality residual. The venue component is
scaled by a Q3 reset factor (halftime adjustments, team-foul reset, starter
minutes). The quality residual is scaled by a Q3 retention factor. The total is
preserved, and only the split between the teams changes.

Situational inputs (rest, attack/defense) act multiplicatively on efficiency
(points per possession), not on the points mean directly.

All structural priors are named, documented, and overridable via constructor
kwargs or via game_context keys. They are domain priors for a cold-start
market and should be re-estimated once settled Q3 data accumulates.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # locate DiscreteDistribution without assuming a single module layout
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded primitive


class NCAAWQuarter3SpreadsModel(BaseCandidateModel):
    """Possession-compound exact-lattice model for NCAAW 3rd-quarter spreads."""

    # ---------------------------------------------------------------------
    # Structural domain priors (cold start). Each one is overridable.
    # ---------------------------------------------------------------------
    DEFAULT_PRIORS: Dict[str, float] = {
        # Empirical segment anchors (provided baseline means)
        "baseline_home_expected": 20.00,
        "baseline_away_expected": 18.75,
        # Pace: possessions per team per 40 minutes, and per-quarter possession SD
        "pace_per40": 70.0,
        "quarter_possession_sd": 2.3,
        # Probability that possession counts differ by one in either direction
        "possession_parity_jitter": 0.25,
        # Per-possession outcome shape prior on {0,1,2,3} (NCAAW play-by-play mix)
        "w_empty": 0.54,
        "w_one": 0.05,   # FT-only trips / split pairs
        "w_two": 0.31,
        "w_three": 0.10,
        # Venue: full-game NCAAW home edge (points); per-quarter = /4
        "full_game_hfa": 3.0,
        # Q3 compression of the venue edge (halftime adj., foul reset, starters)
        "q3_hfa_reset": 0.85,
        # Q3 retention of the team-quality gap (halftime coaching regression)
        "q3_quality_retention": 0.92,
        # Rest-fatigue efficiency penalty: 1 - beta * exp(-rest / tau)
        "rest_beta": 0.04,
        "rest_tau": 1.5,
        "rest_reference_days": 3.0,
        "rest_defensive_weight": 0.5,
        # Residual attack/defense tilt exponent (partial, to avoid double counting
        # strength already embedded in *_expected)
        "attack_defense_exponent": 0.25,
        # Effect of opponent foul rate on FT-trip mass in Q3 (bonus after 5th foul)
        "foul_rate_elasticity": 0.6,
        # Lattice tail coverage
        "tail_mass": 1e-9,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_3_1107_203000",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_3",
        family: str = "spreads",
        **prior_overrides: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:  # positional-signature base classes
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.priors: Dict[str, float] = dict(self.DEFAULT_PRIORS)
        for key, val in prior_overrides.items():
            if key in self.priors and val is not None:
                self.priors[key] = float(val)

        self.last_diagnostics: Dict[str, Any] = {}
        self._last_joint: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    # Input helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _num(value: Any, default: float) -> float:
        try:
            out = float(value)
            return out if math.isfinite(out) else default
        except (TypeError, ValueError):
            return default

    def _prior(self, ctx: Dict[str, Any], key: str) -> float:
        """Read a prior, letting game_context override it per game."""
        return self._num(ctx.get(key), self.priors[key])

    @staticmethod
    def _team_block(ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        block = ctx.get(side)
        return block if isinstance(block, dict) else {}

    def _rating(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        block = self._team_block(ctx, side)
        val = block.get(kind, ctx.get(f"{side}_{kind}"))
        r = self._num(val, 1.0)
        return r if r > 0 else 1.0

    def _is_neutral(self, ctx: Dict[str, Any]) -> bool:
        if bool(ctx.get("neutral_site", False)) or bool(ctx.get("is_neutral", False)):
            return True
        venue = ctx.get("venue")
        if isinstance(venue, dict):
            return bool(venue.get("neutral", False) or venue.get("is_neutral", False))
        if isinstance(venue, str):
            return "neutral" in venue.lower()
        return False

    # ------------------------------------------------------------------
    # Mechanics components
    # ------------------------------------------------------------------
    def _q3_targets(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        """Preserve the total. Re-split the gap into a Q3-compressed venue
        component and a Q3-retained quality component."""
        mu_h = self._num(ctx.get("home_expected"), self.priors["baseline_home_expected"])
        mu_a = self._num(ctx.get("away_expected"), self.priors["baseline_away_expected"])
        mu_h, mu_a = max(mu_h, 1e-6), max(mu_a, 1e-6)

        total = mu_h + mu_a
        gap = mu_h - mu_a
        hfa_quarter = 0.0 if self._is_neutral(ctx) else self._prior(ctx, "full_game_hfa") / 4.0
        quality_gap = gap - hfa_quarter
        gap_q3 = (
            self._prior(ctx, "q3_quality_retention") * quality_gap
            + self._prior(ctx, "q3_hfa_reset") * hfa_quarter
        )
        # Keep both means strictly positive under extreme inputs: map onto the
        # feasible open interval with a smooth (tanh) transform, not a clamp.
        half = total / 2.0
        gap_q3 = total * math.tanh(gap_q3 / total) if total > 0 else 0.0
        t_h, t_a = half + gap_q3 / 2.0, half - gap_q3 / 2.0
        return t_h, t_a, {
            "input_gap": gap,
            "hfa_quarter": hfa_quarter,
            "q3_gap": gap_q3,
            "total": total,
        }

    def _rest_multiplier(self, rest_days: float, ctx: Dict[str, Any]) -> float:
        beta = self._prior(ctx, "rest_beta")
        tau = max(self._prior(ctx, "rest_tau"), 1e-6)
        ref = self._prior(ctx, "rest_reference_days")
        f = lambda r: 1.0 - beta * math.exp(-max(r, 0.0) / tau)
        return f(rest_days) / f(ref)

    def _efficiency_multipliers(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        ref = self._prior(ctx, "rest_reference_days")
        r_h = self._num(ctx.get("home_rest_days"), ref)
        r_a = self._num(ctx.get("away_rest_days"), ref)
        rh, ra = self._rest_multiplier(r_h, ctx), self._rest_multiplier(r_a, ctx)
        dw = self._prior(ctx, "rest_defensive_weight")
        # A fatigued defense concedes more: the opponent's offense is boosted by the inverse.
        rest_h = rh * ra ** (-dw)
        rest_a = ra * rh ** (-dw)

        lam = self._prior(ctx, "attack_defense_exponent")
        ad_h = (self._rating(ctx, "home", "attack") * self._rating(ctx, "away", "defense")) ** lam
        ad_a = (self._rating(ctx, "away", "attack") * self._rating(ctx, "home", "defense")) ** lam
        return rest_h * ad_h, rest_a * ad_a

    def _shape_weights(self, ctx: Dict[str, Any], side: str, opp: str) -> np.ndarray:
        w = np.array(
            [
                self._prior(ctx, "w_empty"),
                self._prior(ctx, "w_one"),
                self._prior(ctx, "w_two"),
                self._prior(ctx, "w_three"),
            ],
            dtype=float,
        )
        block, opp_block = self._team_block(ctx, side), self._team_block(ctx, opp)
        # Optional team-specific shape info: three-point attempt share, FT rate, opponent fouls
        three_rate = block.get("three_point_rate", ctx.get(f"{side}_three_point_rate"))
        if three_rate is not None:
            base_share = w[3] / (w[2] + w[3])
            share = self._num(three_rate, base_share)
            if 0.0 < share < 1.0:
                fg = w[2] + w[3]
                w[2], w[3] = fg * (1 - share), fg * share
        ft_rate = block.get("ft_rate", ctx.get(f"{side}_ft_rate"))
        opp_foul = opp_block.get("foul_rate", ctx.get(f"{opp}_foul_rate"))
        ft_mult = 1.0
        if ft_rate is not None:
            ft_mult *= max(self._num(ft_rate, 1.0), 1e-6)
        if opp_foul is not None:
            ft_mult *= max(self._num(opp_foul, 1.0), 1e-6) ** self._prior(ctx, "foul_rate_elasticity")
        w[1] *= ft_mult
        w = np.maximum(w, 1e-12)
        return w / w.sum()

    @staticmethod
    def _tilted_pmf(weights: np.ndarray, target_ppp: float) -> np.ndarray:
        """Max-entropy tilt p_k ∝ w_k e^{θk} with E[k] = target_ppp, k ∈ {0,1,2,3}."""
        k = np.arange(len(weights), dtype=float)
        kmax = k[-1]
        eps = 1e-9
        target = min(max(target_ppp, eps), kmax - eps)  # open-interval feasibility only
        logw = np.log(weights)

        def mean_minus_target(theta: float) -> float:
            z = logw + theta * k
            z -= z.max()
            p = np.exp(z)
            p /= p.sum()
            return float(p @ k) - target

        lo, hi = -5.0, 5.0
        while mean_minus_target(lo) > 0:
            lo *= 2.0
        while mean_minus_target(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_minus_target, lo, hi, xtol=1e-12)
        z = logw + theta * k
        z -= z.max()
        p = np.exp(z)
        return p / p.sum()

    def _possession_pmf(self, mean: float, var: float, tail: float) -> np.ndarray:
        """Mean-variance matched count distribution on {0, 1, ...}."""
        mean = max(mean, 1e-6)
        var = max(var, 1e-9)
        if var < mean:  # under-dispersed: Binomial(n, p)
            p = 1.0 - var / mean
            n = max(int(round(mean / p)), 1)
            p = mean / n
            support = np.arange(0, n + 1)
            pmf = stats.binom.pmf(support, n, p)
        elif var > mean:  # over-dispersed: NegBin
            k = mean**2 / (var - mean)
            p = k / (k + mean)
            upper = int(stats.nbinom.ppf(1.0 - tail, k, p)) + 1
            support = np.arange(0, upper + 1)
            pmf = stats.nbinom.pmf(support, k, p)
        else:
            upper = int(stats.poisson.ppf(1.0 - tail, mean)) + 1
            support = np.arange(0, upper + 1)
            pmf = stats.poisson.pmf(support, mean)
        pmf = np.asarray(pmf, dtype=float)
        return pmf / pmf.sum()

    @staticmethod
    def _convolution_powers(p: np.ndarray, n_max: int) -> list:
        powers = [np.array([1.0])]
        for _ in range(n_max):
            powers.append(np.convolve(powers[-1], p))
        return powers

    # ------------------------------------------------------------------
    # Core joint distribution
    # ------------------------------------------------------------------
    def joint_score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        t_h, t_a, gap_diag = self._q3_targets(ctx)
        eff_h, eff_a = self._efficiency_multipliers(ctx)
        t_h, t_a = t_h * eff_h, t_a * eff_a

        pace = self._num(
            ctx.get("pace", ctx.get("expected_pace")), self.priors["pace_per40"]
        )
        if pace <= 0:
            pace = self.priors["pace_per40"]
        mean_poss = pace * 10.0 / 40.0
        sd_poss = self._prior(ctx, "quarter_possession_sd")
        jitter = min(max(self._prior(ctx, "possession_parity_jitter"), 0.0), 0.5)
        # Parity jitter adds variance to the away count, so remove it from the
        # shared component so the total possession variance matches the prior.
        shared_var = max(sd_poss**2 - jitter, 1e-6)

        tail = self._prior(ctx, "tail_mass")
        n_pmf = self._possession_pmf(mean_poss, shared_var, tail)
        n_max = len(n_pmf) - 1
        e_n = float(np.arange(n_max + 1) @ n_pmf)  # E[N]; parity jitter is mean-zero

        ppp_h, ppp_a = t_h / e_n, t_a / e_n
        x_h = self._tilted_pmf(self._shape_weights(ctx, "home", "away"), ppp_h)
        x_a = self._tilted_pmf(self._shape_weights(ctx, "away", "home"), ppp_a)

        pow_h = self._convolution_powers(x_h, n_max)
        pow_a = self._convolution_powers(x_a, n_max + 1)

        size_h = 3 * n_max + 1
        size_a = 3 * (n_max + 1) + 1
        joint = np.zeros((size_h, size_a), dtype=float)
        d_probs = {-1: jitter, 0: 1.0 - 2.0 * jitter, 1: jitter}

        for n, pn in enumerate(n_pmf):
            if pn <= 0.0:
                continue
            h = pow_h[n]
            a_mix = np.zeros(size_a)
            for d, pd in d_probs.items():
                m = n + d
                if pd <= 0.0 or m < 0:
                    continue
                a = pow_a[m]
                a_mix[: len(a)] += pd * a
            joint[: len(h), :] += pn * np.outer(h, a_mix)

        joint /= joint.sum()

        # Diagnostics
        hs, as_ = np.arange(size_h), np.arange(size_a)
        ph, pa = joint.sum(axis=1), joint.sum(axis=0)
        m_h, m_a = float(hs @ ph), float(as_ @ pa)
        v_h = float((hs**2) @ ph) - m_h**2
        v_a = float((as_**2) @ pa) - m_a**2
        cov = float(hs @ joint @ as_) - m_h * m_a
        self.last_diagnostics = {
            **gap_diag,
            "target_home": t_h,
            "target_away": t_a,
            "eff_mult_home": eff_h,
            "eff_mult_away": eff_a,
            "mean_possessions": e_n,
            "ppp_home": ppp_h,
            "ppp_away": ppp_a,
            "model_mean_home": m_h,
            "model_mean_away": m_a,
            "dispersion_home": v_h / m_h if m_h > 0 else float("nan"),
            "dispersion_away": v_a / m_a if m_a > 0 else float("nan"),
            "score_correlation": cov / math.sqrt(v_h * v_a) if v_h > 0 and v_a > 0 else 0.0,
            "margin_sd": math.sqrt(max(v_h + v_a - 2 * cov, 0.0)),
        }
        self._last_joint = joint
        return joint

    def margin_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        joint = self.joint_score_matrix(game_context)
        out: Dict[int, float] = {}
        rows, cols = joint.shape
        for d in range(-(cols - 1), rows):
            mass = float(np.trace(joint, offset=-d))  # Σ joint[a+d, a] -> home - away = d
            if mass > 0.0:
                out[d] = mass
        return out

    def cover_probabilities(self, game_context: Dict[str, Any], home_line: float) -> Dict[str, float]:
        """Home covers when margin + home_line > 0. Integer lines yield a push mass."""
        pmf = self.margin_pmf(game_context)
        win = sum(p for d, p in pmf.items() if d + home_line > 1e-12)
        push = sum(p for d, p in pmf.items() if abs(d + home_line) <= 1e-12)
        return {"home_cover": win, "push": push, "away_cover": max(1.0 - win - push, 0.0)}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.joint_score_matrix(game_context)

        scores: Dict[Tuple[int, int], float] = {}
        nz_h, nz_a = np.nonzero(joint > 0.0)
        for i, j in zip(nz_h.tolist(), nz_a.tolist()):
            scores[(i, j)] = float(joint[i, j])

        if hasattr(DiscreteDistribution, "from_scores"):
            for payload in (scores, joint):
                try:
                    return DiscreteDistribution.from_scores(payload)
                except (TypeError, ValueError, AttributeError):
                    continue

        # Fallback: 3-way outcome derived from the exact margin lattice
        p_home = float(np.tril(joint, k=-1).sum())
        p_draw = float(np.trace(joint))
        p_away = max(1.0 - p_home - p_draw, 0.0)
        if hasattr(DiscreteDistribution, "from_probabilities"):
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})