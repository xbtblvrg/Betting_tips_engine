"""
Candidate model for NBA | quarter_1 | winner_total.

Structure: a compound bivariate possession model.

  N            ~ discretised Normal(mu_N, var_N) on the integers.
                 This is the shared Q1 possession count. Real Q1 pace is
                 under-dispersed relative to Poisson (sd ~2 on a mean of ~24),
                 so a NegBin would be the wrong family here.
  X_i | team   ~ categorical on {0,1,2,3,4} points per possession.
                 Built as a maximum-entropy (exponential) tilt of the league
                 per-possession outcome shape, solved so E[X_i] equals the
                 team's target points per possession.
  S_i | N      = sum of N iid copies of X_i. The two teams are conditionally
                 independent given N.

  Joint PGF:   G(zH, zA) = E_N[(phi_H(zH) * phi_A(zA))^N]

The score lattice is computed by exact convolution, not simulation. It gives:
  - totals with Var(S) = E[N] Var(X) + Var(N) E[X]^2 (overdispersed vs Poisson);
  - a positive home/away correlation from the shared pace shock,
    Cov = Var(N) E[X_H] E[X_A];
  - explicit tie (draw) mass for the 3-way market.

Means are anchored exactly to home_expected and away_expected after two steps:
  - the home-court component is rescaled to its Q1 share;
  - the residual strength gap is starter-amplified and then shrunk by input
    reliability.

Every domain prior is a named, documented, overridable parameter. There are no
probability clamps and no odds filters.
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # DiscreteDistribution may live in base or be pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------- #
# Domain priors (documented, overridable via game_context['model_params'])     #
# --------------------------------------------------------------------------- #
@dataclass
class Q1Priors:
    # Fraction of regulation in Q1.
    period_fraction: float = 12.0 / 48.0

    # Cold-start empirical anchors for the segment.
    prior_home_mean: float = 28.0
    prior_away_mean: float = 27.0

    # League per-possession outcome shape on {0,1,2,3,4}. This is a
    # Q1-starter mix including FT trips and and-ones. Only the *shape* is
    # used; the mean is re-solved per team by exponential tilting.
    base_outcome_shape: Tuple[float, ...] = (0.470, 0.050, 0.300, 0.165, 0.015)

    # League offensive efficiency, used only when no pace input is given
    # (to infer possessions from the total).
    league_ppp: float = 1.13

    # Q1 possession-count volatility (sd of possessions per team per Q1).
    possession_sd: float = 2.1

    # Full-game home-court advantage in points (modern NBA).
    full_game_hca: float = 2.4

    # Starter share of minutes: Q1 vs full game. Their ratio amplifies the
    # per-minute strength gap.
    q1_starter_share: float = 0.86
    full_starter_share: float = 0.72

    # Reliability shrinkage of the input strength gap:
    #   w = tau^2 / (tau^2 + sigma^2)
    true_gap_sd: float = 2.0     # sd of true Q1 strength gaps across games
    input_noise_sd: float = 1.0  # noise of supplied Q1 expectations

    # Residual situational tilts. These are small because supplied
    # expectations usually already price these effects.
    rest_efficiency_beta: float = 0.012      # efficiency gain per unit rest-saturation gap
    rest_pace_beta: float = 0.006            # pace gain per unit mean rest saturation
    matchup_residual_weight: float = 0.15    # attack/defense residual when expectations given
    matchup_full_weight: float = 1.00        # attack/defense weight when building from scratch

    # Above this combined expectation, inputs are treated as full-game numbers.
    full_game_total_threshold: float = 120.0

    # Probability mass of N left outside the discretisation support.
    tail_mass: float = 1e-10


class NBAQuarter1WinnerTotalModel(BaseCandidateModel):
    """Compound possession-lattice model for NBA first-quarter winner/total markets."""

    def __init__(
        self,
        model_id: str = "codex_synth_nba_quarter_1_1107_122049",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "quarter_1",
        family: str = "winner_total",
        priors: Optional[Q1Priors] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = priors or Q1Priors()
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Small parsing helpers                                                #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _team_feature(self, ctx: Dict[str, Any], side: str, *keys: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict):
            for k in keys:
                v = self._num(team.get(k))
                if v is not None:
                    return v
        for k in keys:
            v = self._num(ctx.get(f"{side}_{k}"))
            if v is not None:
                return v
        return None

    def _resolve_priors(self, ctx: Dict[str, Any]) -> Q1Priors:
        overrides = ctx.get("model_params")
        if not isinstance(overrides, dict):
            return self.priors
        base = asdict(self.priors)
        for k, v in overrides.items():
            if k in base:
                base[k] = tuple(v) if k == "base_outcome_shape" else float(v)
        return Q1Priors(**base)

    # ------------------------------------------------------------------ #
    # Step 1: target Q1 team means                                         #
    # ------------------------------------------------------------------ #
    def _target_means(self, ctx: Dict[str, Any], P: Q1Priors) -> Dict[str, float]:
        h_exp = self._num(ctx.get("home_expected"))
        a_exp = self._num(ctx.get("away_expected"))
        supplied = h_exp is not None and a_exp is not None and h_exp > 0 and a_exp > 0
        if not supplied:
            h_exp, a_exp = P.prior_home_mean, P.prior_away_mean

        # Full-game inputs are scaled to the Q1 window.
        if h_exp + a_exp > P.full_game_total_threshold:
            h_exp *= P.period_fraction
            a_exp *= P.period_fraction

        total = h_exp + a_exp
        margin = h_exp - a_exp

        # Attack/defense matchup tilt. It is total-preserving and acts on the
        # log-ratio of team efficiencies.
        h_att = self._team_feature(ctx, "home", "attack", "offense", "off_rating")
        h_def = self._team_feature(ctx, "home", "defense", "def_rating")
        a_att = self._team_feature(ctx, "away", "attack", "offense", "off_rating")
        a_def = self._team_feature(ctx, "away", "defense", "def_rating")
        matchup_log_ratio = 0.0
        if None not in (h_att, h_def, a_att, a_def) and min(h_att, h_def, a_att, a_def) > 0:
            # Convention: attack > 1 is better; defense > 1 means more is conceded.
            matchup_log_ratio = math.log((h_att * a_def) / (a_att * h_def))
            weight = P.matchup_residual_weight if supplied else P.matchup_full_weight
            share_h = 0.5 * (1.0 + math.tanh(0.5 * weight * matchup_log_ratio))
            margin += total * (2.0 * share_h - 1.0)

        # Rest tilt. Saturating rest utility g(r) = 1 - exp(-r): back-to-back
        # (r = 0) hurts, and extra days give diminishing returns.
        h_rest = self._num(ctx.get("home_rest_days"))
        a_rest = self._num(ctx.get("away_rest_days"))
        g = lambda r: 1.0 - math.exp(-max(r, 0.0)) if r is not None else None
        gh, ga = g(h_rest), g(a_rest)
        pace_mult = 1.0
        if gh is not None and ga is not None:
            margin += total * P.rest_efficiency_beta * (gh - ga)
            pace_mult = 1.0 + P.rest_pace_beta * (0.5 * (gh + ga) - (1.0 - math.exp(-1.0)))

        # Home-court decomposition. The HCA component is set to its Q1 share.
        # The residual (strength) gap is starter-amplified, then shrunk by
        # input reliability.
        hca_q1 = P.full_game_hca * P.period_fraction
        strength_gap = margin - hca_q1
        amp = P.q1_starter_share / P.full_starter_share
        reliability = P.true_gap_sd ** 2 / (P.true_gap_sd ** 2 + P.input_noise_sd ** 2)
        margin_post = hca_q1 + strength_gap * amp * reliability

        total_post = total * pace_mult
        mu_h = 0.5 * (total_post + margin_post)
        mu_a = 0.5 * (total_post - margin_post)
        # A positive mean is required. Use a smooth softplus guard rather
        # than a clamp; it only binds in pathological inputs.
        sp = lambda x: math.log1p(math.exp(x)) if x < 30 else x
        mu_h, mu_a = sp(mu_h), sp(mu_a)

        return {
            "mu_home": mu_h, "mu_away": mu_a, "hca_q1": hca_q1,
            "margin_in": h_exp - a_exp, "margin_post": mu_h - mu_a,
            "matchup_log_ratio": matchup_log_ratio, "pace_mult": pace_mult,
            "inputs_supplied": supplied,
        }

    # ------------------------------------------------------------------ #
    # Step 2: possession-count distribution                                #
    # ------------------------------------------------------------------ #
    def _possession_pmf(self, ctx: Dict[str, Any], P: Q1Priors,
                        mu_h: float, mu_a: float, pace_mult: float) -> Tuple[np.ndarray, np.ndarray]:
        pace_h = self._team_feature(ctx, "home", "pace", "tempo")
        pace_a = self._team_feature(ctx, "away", "pace", "tempo")
        pace_lg = self._num(ctx.get("league_pace"))
        if pace_h and pace_a and pace_h > 0 and pace_a > 0:
            lg = pace_lg if (pace_lg and pace_lg > 0) else math.sqrt(pace_h * pace_a)
            mu_n = pace_h * pace_a / lg * P.period_fraction * pace_mult
        else:
            mu_n = (mu_h + mu_a) / (2.0 * P.league_ppp)

        var_n = P.possession_sd ** 2

        # Optional dispersion calibration: back out Var(N) from an observed
        # team-score Var/Mean index, using
        #   Var(S) = E[N] Var(X) + Var(N) E[X]^2.
        di = self._num(ctx.get("dispersion_index"))
        if di is not None and di > 0:
            ppp = 0.5 * (mu_h + mu_a) / mu_n
            vx = self._tilted_moments(self._tilt(P.base_outcome_shape, ppp))[1]
            implied = (di * ppp * mu_n - mu_n * vx) / (ppp ** 2)
            if implied > 0:  # only adopt if structurally consistent
                var_n = implied

        sd = math.sqrt(var_n)
        z = norm.isf(P.tail_mass)
        lo = max(int(math.floor(mu_n - z * sd)), 1)
        hi = int(math.ceil(mu_n + z * sd))
        n = np.arange(lo, hi + 1)
        # Integer bins of the Normal (continuity-corrected).
        pmf = norm.cdf(n + 0.5, mu_n, sd) - norm.cdf(n - 0.5, mu_n, sd)
        pmf = pmf / pmf.sum()
        return n, pmf

    # ------------------------------------------------------------------ #
    # Step 3: per-possession outcome distribution (max-entropy tilt)       #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tilt(shape: Tuple[float, ...], target_mean: float) -> np.ndarray:
        base = np.asarray(shape, dtype=float)
        base = base / base.sum()
        k = np.arange(base.size, dtype=float)
        kmax = k[-1]
        # The tilt family covers the open interval (0, kmax); a target in it
        # is always attainable.
        tgt = target_mean
        if not (0.0 < tgt < kmax):
            tgt = kmax / (1.0 + math.exp(-math.log(max(tgt, 1e-12) / kmax)))

        def mean_gap(theta: float) -> float:
            w = base * np.exp(theta * (k - k.mean()))
            return float((w * k).sum() / w.sum()) - tgt

        lo, hi = -1.0, 1.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = brentq(mean_gap, lo, hi, xtol=1e-14)
        w = base * np.exp(theta * (k - k.mean()))
        return w / w.sum()

    @staticmethod
    def _tilted_moments(p: np.ndarray) -> Tuple[float, float]:
        k = np.arange(p.size, dtype=float)
        m = float((p * k).sum())
        return m, float((p * k * k).sum()) - m * m

    # ------------------------------------------------------------------ #
    # Step 4: exact joint score lattice                                    #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _score_lattice(n_vals: np.ndarray, n_pmf: np.ndarray,
                       x_h: np.ndarray, x_a: np.ndarray) -> np.ndarray:
        n_max = int(n_vals[-1])
        size = (x_h.size - 1) * n_max + 1
        joint = np.zeros((size, size))
        pmf_by_n = dict(zip(n_vals.tolist(), n_pmf.tolist()))
        cur_h = np.array([1.0])
        cur_a = np.array([1.0])
        for n in range(1, n_max + 1):
            cur_h = np.convolve(cur_h, x_h)
            cur_a = np.convolve(cur_a, x_a)
            w = pmf_by_n.get(n)
            if w:
                L = cur_h.size
                joint[:L, :L] += w * np.outer(cur_h, cur_a)
        return joint / joint.sum()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        P = self._resolve_priors(ctx)
        means = self._target_means(ctx, P)
        mu_h, mu_a = means["mu_home"], means["mu_away"]

        n_vals, n_pmf = self._possession_pmf(ctx, P, mu_h, mu_a, means["pace_mult"])
        e_n = float((n_vals * n_pmf).sum())
        v_n = float(((n_vals - e_n) ** 2 * n_pmf).sum())

        # Exact mean anchoring: E[S_i] = E[N] * PPP_i.
        x_h = self._tilt(P.base_outcome_shape, mu_h / e_n)
        x_a = self._tilt(P.base_outcome_shape, mu_a / e_n)
        joint = self._score_lattice(n_vals, n_pmf, x_h, x_a)

        mh, vh = self._tilted_moments(x_h)
        ma, va = self._tilted_moments(x_a)
        var_sh = e_n * vh + v_n * mh ** 2
        var_sa = e_n * va + v_n * ma ** 2
        cov = v_n * mh * ma
        p_home = float(np.tril(joint, -1).sum())
        p_draw = float(np.trace(joint))
        p_away = float(np.triu(joint, 1).sum())
        self.last_diagnostics = {
            **means,
            "E_N": e_n, "Var_N": v_n,
            "ppp_home": mh, "ppp_away": ma,
            "var_per_poss_home": vh, "var_per_poss_away": va,
            "disp_home": var_sh / (e_n * mh), "disp_away": var_sa / (e_n * ma),
            "corr_home_away": cov / math.sqrt(var_sh * var_sa),
            "total_mean": e_n * (mh + ma),
            "total_sd": math.sqrt(var_sh + var_sa + 2 * cov),
            "margin_sd": math.sqrt(var_sh + var_sa - 2 * cov),
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        }
        return joint

    def total_pmf(self, game_context: Dict[str, Any]) -> np.ndarray:
        joint = self.score_matrix(game_context)
        size = joint.shape[0]
        out = np.zeros(2 * size - 1)
        for i in range(size):
            out[i:i + size] += joint[i]
        return out

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        joint = self.score_matrix(game_context)
        d = self.last_diagnostics

        # Sparse score dict. Negligible cells are dropped and the rest is
        # renormalised (pure numerical hygiene, not a probability clamp).
        idx = np.argwhere(joint > 1e-12)
        scores = {(int(h), int(a)): float(joint[h, a]) for h, a in idx}
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}

        builders = (
            lambda: DiscreteDistribution.from_scores(scores),
            lambda: DiscreteDistribution.from_scores(joint),
            lambda: DiscreteDistribution.from_probabilities(d["p_home"], d["p_draw"], d["p_away"]),
            lambda: DiscreteDistribution(
                probabilities={"home": d["p_home"], "draw": d["p_draw"], "away": d["p_away"]}
            ),
        )
        last_err: Optional[Exception] = None
        for build in builders:
            try:
                return build()
            except Exception as e:  # try the next supported constructor
                last_err = e
        raise RuntimeError(f"Unable to construct DiscreteDistribution: {last_err}")


# Alias for loaders that look up a conventional class name.
CandidateModel = NBAQuarter1WinnerTotalModel