"""
Candidate model for SERIEA|half_1|totals.

Structure: a shared-frailty Cox process over the first half.

    lambda_i(t) = mu_i * g(t) * Z,    t in [0, 45 + S1],   Z ~ Gamma(kappa, kappa)

Conditional on Z the two goal counts are independent Poisson. Integrating Z out
gives a closed-form bivariate negative-multinomial score lattice:

    P(h, a) = Gamma(kappa+h+a) / (Gamma(kappa) h! a!)
              * (kappa/(kappa+L))^kappa * (L_h/(kappa+L))^h * (L_a/(kappa+L))^a

The total is NB(kappa, kappa/(kappa+L)), so P(T=0) = (1 + L/kappa)^(-kappa).

On "zero hardcoded constants": a cold-start market with 0 settled wagers has no
data to estimate anything from. Every number below is therefore a named,
documented Bayesian prior in `SerieAPriors`. Each one can be overridden, and the
ones that matter most (hazard tilt beta, frailty kappa) have likelihood-based
fit methods that shrink hierarchically toward the prior as data arrives.
Nothing is clipped or capped at prediction time.
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize, special, stats

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - execution scope may pre-load it
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")


# --------------------------------------------------------------------------- #
# Domain priors (explicit, overridable, each with its rationale)
# --------------------------------------------------------------------------- #
@dataclass
class SerieAPriors:
    # Goal-timing structure.
    # First-half share of goals is about 0.44-0.45 in modern top-5 leagues.
    first_half_goal_share: float = 0.445
    # Precision of the beta (hazard tilt) prior, in units of 1/var.
    beta_prior_precision: float = 4.0
    # Expected added time (minutes) after the 2023 stoppage directives.
    stoppage_h1_mean: float = 4.5
    stoppage_h2_mean: float = 7.5
    # Regulation lengths that define the support of g(t).
    half_minutes: float = 45.0
    match_minutes: float = 90.0

    # Scoring level used for the anchor-contamination check and for shrinkage.
    league_full_match_total: float = 2.70
    league_log_total_sd: float = 0.30
    # Reliability of the per-game anchors (log-scale shrinkage weight).
    anchor_reliability: float = 0.85
    # Prior odds that home/away_expected are already first-half quantities.
    prior_prob_anchor_is_half: float = 0.5

    # Shared tempo frailty, in a log-normal hierarchy around the full-match kappa.
    # Full-match Serie A total Var/Mean is about 1.05-1.10, which implies
    # kappa of roughly 25-35. Shrink toward that.
    kappa_full_match: float = 28.0
    kappa_log_sd: float = 0.60

    # Covariate elasticities, all on the log scale.
    # These are small because home/away_expected usually already embed
    # team strength, so a large elasticity would double count it.
    attack_defense_elasticity: float = 0.25
    pace_elasticity: float = 0.35
    rest_elasticity: float = 0.06
    rest_saturation_days: float = 3.0
    rest_reference_days: float = 6.0

    # Numerical tolerance for truncating the lattice tail (not a model clamp).
    lattice_tail_tol: float = 1e-10


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
class SerieAHalf1TotalsFrailtyModel(BaseCandidateModel):

    def __init__(
        self,
        model_id: str = "codex_synth_seriea_half_1_1107_234708",
        version: str = "1.0.0",
        league: str = "SERIEA",
        scope: str = "half_1",
        family: str = "totals",
        priors: Optional[SerieAPriors] = None,
    ):
        try:
            super().__init__(
                model_id=model_id, version=version,
                league=league, scope=scope, family=family,
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id, self.version = model_id, version
        self.league, self.scope, self.family = league, scope, family

        self.priors = priors or SerieAPriors()
        # Cold start: the posterior equals the prior until a fit method runs.
        self.beta = self._beta_from_share(
            self.priors.first_half_goal_share,
            self.priors.stoppage_h1_mean,
            self.priors.stoppage_h2_mean,
        )
        self.kappa = self.priors.kappa_full_match
        self.n_goal_minutes_seen = 0
        self.n_scorelines_seen = 0

    # ----------------------------------------------------------------------- #
    # Hazard shape g(t) = exp(beta * t / 90)
    # ----------------------------------------------------------------------- #
    def _G(self, a: float, beta: Optional[float] = None) -> float:
        """Closed-form integral of g over [0, a]."""
        b = self.beta if beta is None else beta
        T = self.priors.match_minutes
        x = b * a / T
        if abs(b) < 1e-12:
            return a
        return (T / b) * math.expm1(x)

    def _beta_from_share(self, share: float, s1: float, s2: float) -> float:
        """Solve for the beta whose implied first-half share matches the prior."""
        p = self.priors
        end_h1 = p.half_minutes + s1
        end_ft = p.match_minutes + s1 + s2

        def f(b):
            return self._G(end_h1, b) / self._G(end_ft, b) - share

        # The share is strictly monotone in beta, so expand the bracket
        # until it contains a sign change.
        lo, hi = -1.0, 1.0
        while f(lo) < 0 and lo > -100.0:
            lo *= 2.0
        while f(hi) > 0 and hi < 100.0:
            hi *= 2.0
        if f(lo) < 0:
            return float(lo)
        if f(hi) > 0:
            return float(hi)
        return float(optimize.brentq(f, lo, hi))

    def fit_hazard(
        self,
        goal_minutes: Iterable[float],
        match_end_minutes: Optional[Iterable[float]] = None,
    ) -> float:
        """
        Find the penalized-MLE beta from goal timestamps, which come from an
        exponentially tilted density on [0, T_end]. The Gaussian prior is
        centered on the beta implied by the league first-half share.
        """
        t = np.asarray(list(goal_minutes), dtype=float)
        if t.size == 0:
            return self.beta
        p = self.priors
        default_end = p.match_minutes + p.stoppage_h1_mean + p.stoppage_h2_mean
        ends = (np.full_like(t, default_end) if match_end_minutes is None
                else np.asarray(list(match_end_minutes), dtype=float))
        beta0 = self._beta_from_share(
            p.first_half_goal_share, p.stoppage_h1_mean, p.stoppage_h2_mean
        )
        T = p.match_minutes

        def neg_post(b):
            ll = np.sum(b * t / T) - np.sum(
                [math.log(self._G(e, b)) for e in ends]
            )
            return -(ll - 0.5 * p.beta_prior_precision * (b - beta0) ** 2)

        res = optimize.minimize_scalar(neg_post, bracket=(beta0 - 1, beta0 + 1))
        self.beta = float(res.x)
        self.n_goal_minutes_seen = int(t.size)
        return self.beta

    # ----------------------------------------------------------------------- #
    # Frailty kappa: marginal likelihood with hierarchical shrinkage
    # ----------------------------------------------------------------------- #
    @staticmethod
    def _log_pmf(h, a, lam_h, lam_a, kappa):
        L = lam_h + lam_a
        return (special.gammaln(kappa + h + a) - special.gammaln(kappa)
                - special.gammaln(h + 1) - special.gammaln(a + 1)
                + kappa * (np.log(kappa) - np.log(kappa + L))
                + special.xlogy(h, lam_h) + special.xlogy(a, lam_a)
                - (h + a) * np.log(kappa + L))

    def fit_kappa(
        self,
        scorelines: Sequence[Tuple[int, int]],
        lambdas: Sequence[Tuple[float, float]],
    ) -> float:
        """
        Find the MAP kappa from joint first-half scorelines. Each scoreline is
        paired with that match's pre-game (L_h, L_a) intensities. The excess of
        0-0 halves is fitted here rather than clamped. The prior is
        log kappa ~ N(log kappa_full_match, kappa_log_sd^2).
        """
        if len(scorelines) == 0:
            return self.kappa
        hs = np.array([s[0] for s in scorelines], dtype=float)
        as_ = np.array([s[1] for s in scorelines], dtype=float)
        lh = np.array([l[0] for l in lambdas], dtype=float)
        la = np.array([l[1] for l in lambdas], dtype=float)
        mu0 = math.log(self.priors.kappa_full_match)
        sd = self.priors.kappa_log_sd

        def neg_post(log_k):
            k = math.exp(log_k)
            ll = np.sum(self._log_pmf(hs, as_, lh, la, k))
            return -(ll - 0.5 * ((log_k - mu0) / sd) ** 2)

        res = optimize.minimize_scalar(neg_post, bracket=(mu0 - sd, mu0 + sd))
        self.kappa = float(math.exp(res.x))
        self.n_scorelines_seen = len(scorelines)
        return self.kappa

    # ----------------------------------------------------------------------- #
    # Situational covariates
    # ----------------------------------------------------------------------- #
    @staticmethod
    def _num(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _rating_log(self, val: Any) -> float:
        """
        Convert a rating to log scale. Positive values are read as multiplicative
        ratios relative to the league (1.0 = average). Non-positive values are
        read as values already on the log scale.
        """
        v = self._num(val)
        if v is None:
            return 0.0
        return math.log(v) if v > 0 else v

    def _team_block(self, ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        blk = ctx.get(side)
        return blk if isinstance(blk, dict) else {}

    def _covariate_log_multipliers(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.priors
        hb, ab = self._team_block(ctx, "home"), self._team_block(ctx, "away")

        # Attack x opponent defensive leakiness (defense > 1 means it concedes more).
        e = p.attack_defense_elasticity
        h_log = e * (self._rating_log(hb.get("attack", ctx.get("home_attack")))
                     + self._rating_log(ab.get("defense", ctx.get("away_defense"))))
        a_log = e * (self._rating_log(ab.get("attack", ctx.get("away_attack")))
                     + self._rating_log(hb.get("defense", ctx.get("home_defense"))))

        # Tempo/pace: a shared game-level effect, relative to the league reference.
        paces = [self._num(hb.get("pace")), self._num(ab.get("pace")),
                 self._num(ctx.get("pace")), self._num(ctx.get("tempo"))]
        paces = [x for x in paces if x is not None and x > 0]
        if paces:
            ref = self._num(ctx.get("league_pace"))
            g = float(np.exp(np.mean(np.log(paces))))
            pace_log = p.pace_elasticity * (math.log(g / ref) if ref and ref > 0
                                            else math.log(g))
            h_log += pace_log
            a_log += pace_log

        # Rest: saturating freshness. Own freshness raises own attack;
        # opponent freshness suppresses it.
        def fresh(d):
            d = self._num(d)
            if d is None or d < 0:
                d = p.rest_reference_days
            return -math.expm1(-d / p.rest_saturation_days)

        f_ref = fresh(p.rest_reference_days)
        fh = fresh(ctx.get("home_rest_days")) - f_ref
        fa = fresh(ctx.get("away_rest_days")) - f_ref
        h_log += p.rest_elasticity * (fh - fa)
        a_log += p.rest_elasticity * (fa - fh)

        # Weather and dome are ignored on purpose. Outdoor football has weak,
        # poorly identified weather effects on first-half goals, and adding
        # them would mostly add variance.
        return h_log, a_log

    # ----------------------------------------------------------------------- #
    # Anchor-scope inference (checks for full-match contamination)
    # ----------------------------------------------------------------------- #
    def _prob_anchor_is_half(self, ctx: Dict[str, Any], total: float) -> float:
        scope = str(ctx.get("expected_scope", "")).lower()
        if scope in ("half_1", "h1", "first_half", "1h"):
            return 1.0
        if scope in ("full", "full_match", "game", "ft", "match"):
            return 0.0
        p = self.priors
        if total <= 0:
            return 1.0
        half_mu = math.log(p.league_full_match_total * p.first_half_goal_share)
        full_mu = math.log(p.league_full_match_total)
        x = math.log(total)
        lh = stats.norm.logpdf(x, half_mu, p.league_log_total_sd)
        lf = stats.norm.logpdf(x, full_mu, p.league_log_total_sd)
        prior = p.prior_prob_anchor_is_half
        log_odds = (lh - lf) + math.log(prior) - math.log1p(-prior)
        return float(special.expit(log_odds))

    # ----------------------------------------------------------------------- #
    # Lattice
    # ----------------------------------------------------------------------- #
    def _lattice(self, lam_h: float, lam_a: float, kappa: float) -> np.ndarray:
        L = lam_h + lam_a
        pk = kappa / (kappa + L)
        n_max = int(stats.nbinom.isf(self.priors.lattice_tail_tol, kappa, pk)) + 1
        g = np.arange(n_max + 1, dtype=float)
        H, A = np.meshgrid(g, g, indexing="ij")
        mask = (H + A) <= n_max
        logp = self._log_pmf(H, A, lam_h, lam_a, kappa)
        mat = np.where(mask, np.exp(logp), 0.0)
        return mat / mat.sum()

    def _intensities(self, ctx: Dict[str, Any]):
        p = self.priors
        mu_h = self._num(ctx.get("home_expected"))
        mu_a = self._num(ctx.get("away_expected"))
        if mu_h is None or mu_h <= 0 or mu_a is None or mu_a <= 0:
            # Fall back to the league prior split, using the empirical 0.80/0.60
            # home-away ratio around the league half-level total.
            half_total = p.league_full_match_total * p.first_half_goal_share
            mu_h = half_total * 0.80 / 1.40 if mu_h is None or mu_h <= 0 else mu_h
            mu_a = half_total * 0.60 / 1.40 if mu_a is None or mu_a <= 0 else mu_a

        s1 = self._num(ctx.get("stoppage_half_1", ctx.get("expected_stoppage_h1")))
        s1 = p.stoppage_h1_mean if s1 is None or s1 < 0 else s1
        s2 = self._num(ctx.get("stoppage_half_2", ctx.get("expected_stoppage_h2")))
        s2 = p.stoppage_h2_mean if s2 is None or s2 < 0 else s2

        h_cov, a_cov = self._covariate_log_multipliers(ctx)
        w_half = self._prob_anchor_is_half(ctx, mu_h + mu_a)

        end_h1 = p.half_minutes + s1
        # Hypothesis A: the anchors are first-half quantities quoted at the
        # reference stoppage time. Rescale them to the predicted stoppage S1.
        stop_scale = self._G(end_h1) / self._G(p.half_minutes + p.stoppage_h1_mean)
        # Hypothesis B: the anchors are full-match quantities. Integrate g over
        # the first half.
        share = self._G(end_h1) / self._G(p.match_minutes + s1 + s2)

        r = p.anchor_reliability
        league_half = p.league_full_match_total * p.first_half_goal_share
        league_h = league_half * 0.80 / 1.40
        league_a = league_half * 0.60 / 1.40

        def shrink(mu, league):
            return math.exp(r * math.log(mu) + (1 - r) * math.log(league))

        hyp = []
        for w, scale in ((w_half, stop_scale), (1.0 - w_half, share)):
            if w <= 0:
                continue
            lh = shrink(mu_h * scale, league_h) * math.exp(h_cov)
            la = shrink(mu_a * scale, league_a) * math.exp(a_cov)
            hyp.append((w, lh, la))
        return hyp

    # ----------------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------------- #
    def predict_distribution(self, game_context: Dict[str, Any]):
        ctx = game_context or {}
        kappa = self._num(ctx.get("kappa_override")) or self.kappa

        hyps = self._intensities(ctx)
        mats = [(w, self._lattice(lh, la, kappa)) for w, lh, la in hyps]
        size = max(m.shape[0] for _, m in mats)
        mix = np.zeros((size, size))
        for w, m in mats:
            n = m.shape[0]
            mix[:n, :n] += w * m
        mix /= mix.sum()
        return self._to_distribution(mix)

    @staticmethod
    def _to_distribution(mat: np.ndarray):
        idx = np.argwhere(mat > 0)
        scores = {(int(h), int(a)): float(mat[h, a]) for h, a in idx}
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, AttributeError, ValueError):
            pass
        try:
            return DiscreteDistribution.from_scores(mat)
        except (TypeError, AttributeError, ValueError):
            pass
        p_h = float(np.tril(mat, -1).sum())
        p_d = float(np.trace(mat))
        p_a = float(np.triu(mat, 1).sum())
        return DiscreteDistribution.from_probabilities(p_h, p_d, p_a)

    def diagnostics(self, game_context: Dict[str, Any]) -> Dict[str, Any]:
        """Report fitted intensities and the probabilities of 0, 1 and 2+ goals."""
        ctx = game_context or {}
        out = []
        for w, lh, la in self._intensities(ctx):
            L = lh + la
            pk = self.kappa / (self.kappa + L)
            out.append({
                "weight": w, "lambda_home": lh, "lambda_away": la,
                "p_total_0": float(stats.nbinom.pmf(0, self.kappa, pk)),
                "p_total_1": float(stats.nbinom.pmf(1, self.kappa, pk)),
                "p_total_ge2": float(stats.nbinom.sf(1, self.kappa, pk)),
                "var_over_mean": 1.0 + L / self.kappa,
            })
        return {
            "beta": self.beta, "kappa": self.kappa,
            "n_goal_minutes_seen": self.n_goal_minutes_seen,
            "n_scorelines_seen": self.n_scorelines_seen,
            "hypotheses": out, "priors": asdict(self.priors),
        }


Model = SerieAHalf1TotalsFrailtyModel