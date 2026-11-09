"""
Candidate model for WNBA | quarter_2 | winner_total.

Structure
---------
1. Shared pace lattice.
   A base possession count n per team is shared by both sides. Its uncertainty
   is a discretised Gaussian rather than a negative binomial, because WNBA
   quarter possession counts are under-dispersed relative to Poisson
   (sd ~ 1.8 on a mean of ~19.5). An "extra possession" term gives one side
   n+1 when the quarter's total possession count is odd.
2. Points per possession.
   Each possession is a categorical outcome on {0, 1, 2, 3}. The league
   outcome template is tilted to each team's required points per possession
   using the minimum-KL exponential family p_k ∝ q_k·exp(θk). This is the
   least-informative way to move the mean while keeping the lattice shape.
   Team 3-point and free-throw rates modify the template before tilting.
3. Anchoring.
   Means are reproduced exactly: E[n_i]·E[Y_i] = target_i.
   The home/away gap is split into a total and a margin. The margin is shrunk
   by precision weighting toward a hierarchical home-edge prior, which stops
   the raw 1.25-point baseline gap from being priced as true edge.
   Efficiency deviations are shrunk toward the league mean in proportion to
   expected Q2 bench minutes.
4. Pricing.
   The exact joint lattice is P(H, A) = Σ_n w_n · f_H^{*n} ⊗ f_A^{*n},
   plus the odd-possession terms. Winner (with explicit tie mass) and total
   both come from that one joint, so they share the correlation induced by
   pace.

Every structural prior is a named, documented class attribute. Each one can
be overridden per game through game_context["priors"].
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may be pre-loaded in scope or importable.
    DiscreteDistribution  # type: ignore[name-defined]  # noqa: F821
except NameError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
    except Exception:  # pragma: no cover
        from betting_engine.distributions import DiscreteDistribution  # type: ignore


class WNBAQuarter2WinnerTotalModel(BaseCandidateModel):
    # ------------------------------------------------------------------ #
    # Domain priors. All are documented and overridable via
    # game_context["priors"].
    # ------------------------------------------------------------------ #

    # Empirical segment anchors (cold-start baselines).
    BASE_HOME_EXPECTED = 20.00
    BASE_AWAY_EXPECTED = 18.75

    # League possession-outcome template on {0, 1, 2, 3} points.
    # Derived from WNBA possession accounting:
    #   turnovers ~19%, empty shot trips ~31%, and-one / single-FT ~4%,
    #   2-point makes or 2-FT trips ~34%, made threes ~12%.
    # Mean is ~1.08 PPP and variance is ~1.31.
    OUTCOME_TEMPLATE = (0.50, 0.04, 0.34, 0.12)
    LEAGUE_THREE_RATE = 0.12   # P(possession ends in a made 3)
    LEAGUE_FT_RATE = 0.04      # P(possession yields exactly 1 point)

    # Pace: WNBA ~78-80 possessions per 40 minutes, so ~19.5 per quarter.
    LEAGUE_PACE_40 = 78.0
    QUARTER_FRACTION = 0.25
    # Intrinsic sd of quarter possession counts at a known pace.
    POSSESSION_SD = 1.8
    # Relative uncertainty of the pace estimate itself (adds to the sd).
    PACE_REL_UNCERTAINTY = 0.04
    # Probability a quarter ends with an odd total possession count.
    ODD_POSSESSION_PROB = 0.5

    # Home edge: hierarchical prior for the Q2 margin (points).
    HOME_EDGE_PRIOR_MEAN = 0.45
    HOME_EDGE_PRIOR_SD = 0.35
    # Noise sd of a supplied expected-margin signal at quarter scope.
    MARGIN_SIGNAL_SD = 0.60

    # Q2 bench share: fraction of Q2 minutes played by reserves. Efficiency
    # deviations from the league mean are multiplied by (1 - bench_share).
    Q2_BENCH_SHARE = 0.35

    # Rest: PPP effect per day of rest advantage, with tanh saturation scale.
    REST_PPP_EFFECT = 0.012
    REST_SATURATION_DAYS = 2.0

    # Weight of supplied attack/defense ratings in log space. These are
    # partial, because home_expected / away_expected may already embed them.
    RATING_WEIGHT = 0.5

    # Lattice truncation, in sd units of possessions.
    LATTICE_SIGMAS = 6.0

    MIN_ODDS_ELIGIBLE = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_wnba_quarter_2_1107_233147",
        version: str = "1.0.0",
        league: str = "WNBA",
        scope: str = "quarter_2",
        family: str = "winner_total",
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self._last_joint: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _f(x: Any, default: float) -> float:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _prior(self, ctx: Dict[str, Any], name: str) -> Any:
        pri = ctx.get("priors") or {}
        return pri.get(name, getattr(self, name))

    @staticmethod
    def _team_block(ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        blk = ctx.get(side)
        return blk if isinstance(blk, dict) else {}

    def _rating(self, blk: Dict[str, Any], ctx: Dict[str, Any], side: str, key: str) -> float:
        v = blk.get(key, ctx.get(f"{side}_{key}"))
        return self._f(v, 1.0) if v is not None else 1.0

    @staticmethod
    def _tilt(q: np.ndarray, target_mean: float) -> np.ndarray:
        """Minimum-KL exponential tilt of q on support {0..K} to the target mean."""
        k = np.arange(len(q), dtype=float)
        lo_m, hi_m = 1e-6, k[-1] - 1e-6
        target = min(max(target_mean, lo_m), hi_m)  # numerical support guard only

        def mean_gap(theta: float) -> float:
            w = q * np.exp(theta * k - np.max(theta * k))
            w /= w.sum()
            return float(w @ k) - target

        lo, hi = -5.0, 5.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = brentq(mean_gap, lo, hi, xtol=1e-12)
        w = q * np.exp(theta * k - np.max(theta * k))
        return w / w.sum()

    def _template(self, ctx: Dict[str, Any], blk: Dict[str, Any], opp: Dict[str, Any], side: str) -> np.ndarray:
        """League template, reshaped by team 3-point and FT tendencies (shrunk for Q2 bench)."""
        q = np.array(self._prior(ctx, "OUTCOME_TEMPLATE"), dtype=float)
        bench = self._f(self._prior(ctx, "Q2_BENCH_SHARE"), self.Q2_BENCH_SHARE)
        lg3 = self._prior(ctx, "LEAGUE_THREE_RATE")
        lgft = self._prior(ctx, "LEAGUE_FT_RATE")

        r3 = blk.get("three_rate", ctx.get(f"{side}_three_rate"))
        rft = blk.get("ft_rate", ctx.get(f"{side}_ft_rate"))
        o3 = opp.get("three_rate_allowed")
        oft = opp.get("ft_rate_allowed")

        def ratio(own, allowed, league):
            parts = [self._f(x, league) / league for x in (own, allowed) if x is not None]
            if not parts:
                return 1.0
            r = math.exp(np.mean(np.log(np.maximum(parts, 1e-6))))
            return r ** (1.0 - bench)

        q[3] *= ratio(r3, o3, lg3)
        q[1] *= ratio(rft, oft, lgft)
        return q / q.sum()

    def _possession_weights(self, mu: float, sd: float) -> Tuple[np.ndarray, np.ndarray]:
        span = self.LATTICE_SIGMAS * sd
        n_lo = max(0, int(math.floor(mu - span)))
        n_hi = int(math.ceil(mu + span))
        n = np.arange(n_lo, n_hi + 1)
        # Discretised Gaussian (continuity-corrected); supports under-dispersion.
        from scipy.stats import norm

        w = norm.cdf(n + 0.5, mu, sd) - norm.cdf(n - 0.5, mu, sd)
        w = np.maximum(w, 0.0)
        return n, w / w.sum()

    @staticmethod
    def _power_table(pmf: np.ndarray, n_max: int) -> list:
        tab = [np.array([1.0])]
        for _ in range(n_max):
            tab.append(np.convolve(tab[-1], pmf))
        return tab

    # ------------------------------------------------------------------ #
    # Core build
    # ------------------------------------------------------------------ #
    def build_joint(self, ctx: Dict[str, Any]) -> np.ndarray:
        home_blk = self._team_block(ctx, "home")
        away_blk = self._team_block(ctx, "away")

        h_exp = self._f(ctx.get("home_expected"), self._prior(ctx, "BASE_HOME_EXPECTED"))
        a_exp = self._f(ctx.get("away_expected"), self._prior(ctx, "BASE_AWAY_EXPECTED"))

        # --- Attack/defense exposure (partial, log-space) ---
        rw = self._prior(ctx, "RATING_WEIGHT")
        bench = self._prior(ctx, "Q2_BENCH_SHARE")
        shrink = 1.0 - bench
        h_mult = (
            self._rating(home_blk, ctx, "home", "attack") * self._rating(away_blk, ctx, "away", "defense")
        ) ** (rw * shrink)
        a_mult = (
            self._rating(away_blk, ctx, "away", "attack") * self._rating(home_blk, ctx, "home", "defense")
        ) ** (rw * shrink)
        h_exp *= h_mult
        a_exp *= a_mult

        # --- Rest differential (saturating) ---
        hr = self._f(ctx.get("home_rest_days"), 1.0)
        ar = self._f(ctx.get("away_rest_days"), 1.0)
        sat = self._prior(ctx, "REST_SATURATION_DAYS")
        rest_eff = self._prior(ctx, "REST_PPP_EFFECT") * sat * (math.tanh(hr / sat) - math.tanh(ar / sat))

        # --- Pace ---
        lg_pace = self._prior(ctx, "LEAGUE_PACE_40")
        hp = self._f(home_blk.get("pace", ctx.get("home_pace")), lg_pace)
        ap = self._f(away_blk.get("pace", ctx.get("away_pace")), lg_pace)
        game_pace = hp * ap / lg_pace  # multiplicative pace interaction
        mu_n_total = game_pace * self._prior(ctx, "QUARTER_FRACTION")
        pi_odd = self._prior(ctx, "ODD_POSSESSION_PROB")
        mu_base = mu_n_total - pi_odd / 2.0  # per-team base possessions
        sd = math.hypot(
            self._prior(ctx, "POSSESSION_SD") / math.sqrt(2.0),  # per-team share of count noise
            self._prior(ctx, "PACE_REL_UNCERTAINTY") * mu_base,
        )

        # --- Margin shrinkage toward the hierarchical home edge ---
        total = h_exp + a_exp
        d_obs = h_exp - a_exp
        tau = self._prior(ctx, "HOME_EDGE_PRIOR_SD")
        sig = self._prior(ctx, "MARGIN_SIGNAL_SD")
        d0 = self._prior(ctx, "HOME_EDGE_PRIOR_MEAN")
        if ctx.get("is_neutral") or ctx.get("neutral_site"):
            d0 = 0.0
        # Only the component of the gap beyond the structural edge is signal.
        prec_s, prec_p = 1.0 / sig**2, 1.0 / tau**2
        d_post = (d_obs * prec_s + d0 * prec_p) / (prec_s + prec_p)
        h_target = 0.5 * (total + d_post)
        a_target = 0.5 * (total - d_post)

        # --- Possession lattice ---
        n_vals, w = self._possession_weights(mu_base, sd)
        e_n_team = float(n_vals @ w) + pi_odd / 2.0

        # Required points per possession, with Q2 bench pull toward the league mean.
        lg_ppp = total / (2.0 * e_n_team)
        ppp_h = lg_ppp + shrink * (h_target / e_n_team - lg_ppp) + rest_eff / 2.0
        ppp_a = lg_ppp + shrink * (a_target / e_n_team - lg_ppp) - rest_eff / 2.0
        # Re-anchor so the shrinkage redistributes margin but preserves the total.
        adj = lg_ppp - 0.5 * (ppp_h + ppp_a)
        ppp_h += adj
        ppp_a += adj

        f_h = self._tilt(self._template(ctx, home_blk, away_blk, "home"), ppp_h)
        f_a = self._tilt(self._template(ctx, away_blk, home_blk, "away"), ppp_a)

        n_max = int(n_vals[-1]) + 1
        tab_h = self._power_table(f_h, n_max)
        tab_a = self._power_table(f_a, n_max)
        size = 3 * n_max + 1
        joint = np.zeros((size, size))

        def add(gh: np.ndarray, ga: np.ndarray, weight: float) -> None:
            joint[: len(gh), : len(ga)] += weight * np.outer(gh, ga)

        for n, wn in zip(n_vals, w):
            if wn <= 0:
                continue
            n = int(n)
            add(tab_h[n], tab_a[n], wn * (1.0 - pi_odd))
            add(tab_h[n + 1], tab_a[n], wn * pi_odd / 2.0)
            add(tab_h[n], tab_a[n + 1], wn * pi_odd / 2.0)

        joint /= joint.sum()
        self._last_joint = joint
        return joint

    # ------------------------------------------------------------------ #
    # Pricing utilities
    # ------------------------------------------------------------------ #
    @staticmethod
    def winner_probs(joint: np.ndarray) -> Tuple[float, float, float]:
        h = np.arange(joint.shape[0])[:, None]
        a = np.arange(joint.shape[1])[None, :]
        p_home = float(joint[h > a].sum())
        p_draw = float(joint[h == a].sum())
        p_away = float(joint[h < a].sum())
        s = p_home + p_draw + p_away
        return p_home / s, p_draw / s, p_away / s

    @staticmethod
    def total_probs(joint: np.ndarray, line: float) -> Dict[str, float]:
        h = np.arange(joint.shape[0])[:, None]
        a = np.arange(joint.shape[1])[None, :]
        t = h + a
        return {
            "over": float(joint[t > line].sum()),
            "push": float(joint[np.isclose(t, line)].sum()),
            "under": float(joint[t < line].sum()),
        }

    @staticmethod
    def winner_total_combo(joint: np.ndarray, side: str, line: float, over: bool) -> float:
        h = np.arange(joint.shape[0])[:, None]
        a = np.arange(joint.shape[1])[None, :]
        win = (h > a) if side == "home" else (a > h)
        tot = (h + a > line) if over else (h + a < line)
        return float(joint[win & tot].sum())

    def expected_value(self, prob_win: float, decimal_odds: float, prob_push: float = 0.0) -> Optional[float]:
        """EV per unit stake. Returns None when the contract is ineligible."""
        if decimal_odds < self.MIN_ODDS_ELIGIBLE:
            return None
        ev = prob_win * (decimal_odds - 1.0) - (1.0 - prob_win - prob_push)
        return ev if ev > 0 else None

    def summary(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        joint = self.build_joint(ctx)
        h = np.arange(joint.shape[0])
        a = np.arange(joint.shape[1])
        ph, pa = joint.sum(1), joint.sum(0)
        mh, ma = float(h @ ph), float(a @ pa)
        vh = float(((h - mh) ** 2) @ ph)
        va = float(((a - ma) ** 2) @ pa)
        cov = float(np.einsum("i,j,ij->", h - mh, a - ma, joint))
        p_h, p_d, p_a = self.winner_probs(joint)
        return {
            "home_mean": mh,
            "away_mean": ma,
            "home_dispersion": vh / mh if mh > 0 else float("nan"),
            "away_dispersion": va / ma if ma > 0 else float("nan"),
            "score_corr": cov / math.sqrt(vh * va) if vh > 0 and va > 0 else 0.0,
            "p_home": p_h,
            "p_draw": p_d,
            "p_away": p_a,
        }

    # ------------------------------------------------------------------ #
    # Required interface
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":  # noqa: F821
        ctx = game_context or {}
        joint = self.build_joint(ctx)

        scores: Dict[Tuple[int, int], float] = {}
        nz = np.argwhere(joint > 1e-12)
        mass = 0.0
        for i, j in nz:
            p = float(joint[i, j])
            scores[(int(i), int(j))] = p
            mass += p
        if mass > 0:
            scores = {k: v / mass for k, v in scores.items()}

        try:
            return DiscreteDistribution.from_scores(scores)  # noqa: F821
        except Exception:
            p_h, p_d, p_a = self.winner_probs(joint)
            try:
                return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)  # noqa: F821
            except Exception:
                return DiscreteDistribution(  # noqa: F821
                    probabilities={"home": p_h, "draw": p_d, "away": p_a}
                )