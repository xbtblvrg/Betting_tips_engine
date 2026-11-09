"""
NCAAW | quarter_3 | moneyline candidate model.

The model has four layers.

1. Possession layer.
   Both teams share a common possession count N for the quarter, so home and
   away get the same number of trips. N is drawn from a count law that matches
   the supplied dispersion index D = Var/Mean:
       D < 1  -> binomial (alternating possessions are underdispersed)
       D = 1  -> Poisson
       D > 1  -> negative binomial
   E[N] comes from pace (possessions per 40 minutes, scaled to one 10-minute
   quarter). If pace is missing, it falls back to the domain pace prior.

2. Per-possession scoring layer.
   Each possession yields points k in {0, 1, 2, 3, 4} from a categorical law.
   The baseline NCAAW outcome shape is exponentially tilted,
       p_k(theta) ∝ b_k * exp(theta * k),
   and theta is solved so that E[N] * E[pts | theta] reproduces each team's
   adjusted expected points exactly. Tilting keeps the lumpy 0/1/2/3 structure.
   This gives margin variance of about N * E[pts^2], not the Skellam value
   of N * E[pts].

3. Margin layer.
   Conditional on N = n, each team's points are the n-fold convolution of its
   per-possession law. The margin law is the home law convolved with the
   reversed away law, then mixed over the distribution of N. Pace correlation
   therefore cancels in the margin mean but still widens the margin's spread.

4. Home-edge layer.
   The raw gap mu_H - mu_A is not treated as pure home edge. Its home-court
   portion is shrunk toward the quarter share of the full-game home advantage.
   Its strength portion is shrunk toward a ratings-implied strength gap when
   ratings exist. The total is preserved; only the split between teams moves.

The moneyline output is P(M > 0), P(M = 0) and P(M < 0). The tie mass is kept
explicit, so the settlement layer can either void it (push rules) or settle it
three-way.

All structural priors live in `NCAAWQuarterPriors`. They are documented domain
fundamentals, not clamps, and every one can be overridden per game through
`game_context` or at construction time.
"""

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

# --------------------------------------------------------------------------- #
# Engine imports (with graceful fallback to pre-loaded execution scope)
# --------------------------------------------------------------------------- #
try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - sandbox pre-loads the symbol
    BaseCandidateModel = globals().get("BaseCandidateModel", object)  # type: ignore

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------- #
# Structural domain priors
# --------------------------------------------------------------------------- #
@dataclass
class NCAAWQuarterPriors:
    # Empirical segment anchors (third-quarter expected points)
    home_expected_anchor: float = 20.00
    away_expected_anchor: float = 18.75

    # Game structure
    regulation_minutes: float = 40.0
    quarter_minutes: float = 10.0
    pace_per40_prior: float = 70.0  # NCAAW possessions per team per 40 minutes

    # Count dispersion of the possession count N (Var/Mean); observed index = 1.0
    possession_dispersion_index: float = 1.0

    # Baseline per-possession outcome shape for NCAAW, over points {0,1,2,3,4}.
    # Index 1 covers split FT trips and technicals; index 4 covers and-one
    # threes and 2+FT sequences. Tilting re-centres the mean, so only the
    # shape matters here.
    outcome_shape: Tuple[float, ...] = (0.505, 0.050, 0.330, 0.100, 0.015)

    # Home court: full-game NCAAW home advantage in points, applied to the quarter
    full_game_hca_points: float = 3.0
    # Prior SD of the quarter home-court effect (controls shrinkage of the raw gap)
    hca_quarter_prior_sd: float = 0.35
    # Prior SD of the quarter strength gap when no ratings are available
    strength_quarter_prior_sd: float = 2.5
    # Noise SD of the supplied expected-gap signal (quarter scale)
    gap_signal_sd: float = 1.5

    # Exposure elasticity: how strongly attack/defense ratios move expectations
    # beyond what home_expected and away_expected already encode (0 = fully
    # encoded, 1 = raw).
    exposure_elasticity: float = 0.25
    # Tempo elasticity of possessions relative to team pace ratios
    tempo_elasticity: float = 1.0
    # Rest: proportional efficiency change per log(1 + rest-day advantage)
    rest_efficiency_slope: float = 0.012

    # Numerical tail coverage for the possession count
    count_tail_mass: float = 1e-10


# --------------------------------------------------------------------------- #
# Candidate model
# --------------------------------------------------------------------------- #
class NCAAWQuarter3MoneylineModel(BaseCandidateModel):  # type: ignore[misc]
    """Shared-possession, lumpy-scoring margin model for NCAAW third quarters."""

    POINT_SUPPORT = np.arange(5, dtype=float)  # {0, 1, 2, 3, 4}

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_3_1107_194200",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_3",
        family: str = "moneyline",
        priors: Optional[NCAAWQuarterPriors] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            try:
                super().__init__(model_id, version, league, scope, family)
            except TypeError:
                super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = priors or NCAAWQuarterPriors()
        base = np.asarray(self.priors.outcome_shape, dtype=float)
        self._log_base = np.log(base / base.sum())

    # ------------------------------------------------------------------ #
    # Context helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(value: Any) -> Optional[float]:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        """Return the first finite numeric value at any key.

        Keys may be flat ('home_attack') or nested with dot paths ('home.attack').
        """
        for key in keys:
            node: Any = ctx
            ok = True
            for part in key.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if ok:
                v = self._num(node)
                if v is not None:
                    return v
        return None

    def _team_feature(self, ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
        return self._get(
            ctx,
            f"{side}_{name}",
            f"{side}.{name}",
            f"{side}_team.{name}",
            f"{side}_stats.{name}",
        )

    def _prior(self, ctx: Dict[str, Any], name: str) -> float:
        """Allow per-game override of any structural prior."""
        override = self._get(ctx, name, f"priors.{name}")
        return override if override is not None else float(getattr(self.priors, name))

    # ------------------------------------------------------------------ #
    # Layer A: situational expectations
    # ------------------------------------------------------------------ #
    @staticmethod
    def _ratio(x: Optional[float]) -> float:
        """Read an exposure value as a ratio around 1.

        Values centred on 0 (z-scores or net ratings near 0) and values
        centred on 1 (multipliers) are both supported.
        """
        if x is None:
            return 1.0
        if x > 0.0 and 0.2 < x < 5.0:
            return x
        return math.exp(x / 100.0) if abs(x) > 5.0 else math.exp(x * 0.1)

    def _situational_means(self, ctx: Dict[str, Any]) -> Tuple[float, float, Dict[str, float]]:
        mu_h = self._get(ctx, "home_expected", "home.expected") or self._prior(ctx, "home_expected_anchor")
        mu_a = self._get(ctx, "away_expected", "away.expected") or self._prior(ctx, "away_expected_anchor")

        # Scale full-game expectations to the quarter if needed: a quarter mean
        # cannot plausibly exceed half a game, so values above one half of a
        # full-game total indicate full-game units.
        full_game_total_scale = self._prior(ctx, "regulation_minutes") / self._prior(ctx, "quarter_minutes")
        anchor_total = self._prior(ctx, "home_expected_anchor") + self._prior(ctx, "away_expected_anchor")
        if (mu_h + mu_a) > anchor_total * full_game_total_scale / 2.0:
            mu_h /= full_game_total_scale
            mu_a /= full_game_total_scale

        # Attack/defense exposure (geometric, elasticity-shrunk to avoid double counting)
        el = self._prior(ctx, "exposure_elasticity")
        att_h = self._ratio(self._team_feature(ctx, "home", "attack"))
        def_h = self._ratio(self._team_feature(ctx, "home", "defense"))
        att_a = self._ratio(self._team_feature(ctx, "away", "attack"))
        def_a = self._ratio(self._team_feature(ctx, "away", "defense"))
        # A defense ratio > 1 means the defense concedes more (exposure convention)
        mu_h *= (att_h * def_a) ** el
        mu_a *= (att_a * def_h) ** el

        # Rest: efficiency tilt proportional to log rest advantage (symmetric)
        rest_h = self._team_feature(ctx, "home", "rest_days")
        rest_a = self._team_feature(ctx, "away", "rest_days")
        if rest_h is not None and rest_a is not None:
            slope = self._prior(ctx, "rest_efficiency_slope")
            adv = math.log1p(max(rest_h, 0.0)) - math.log1p(max(rest_a, 0.0))
            mu_h *= math.exp(+0.5 * slope * adv)
            mu_a *= math.exp(-0.5 * slope * adv)

        diagnostics = {
            "att_h": att_h, "def_h": def_h, "att_a": att_a, "def_a": def_a,
            "mu_h_situational": mu_h, "mu_a_situational": mu_a,
        }
        return mu_h, mu_a, diagnostics

    # ------------------------------------------------------------------ #
    # Layer B: home-edge decomposition (Gaussian shrinkage of the gap)
    # ------------------------------------------------------------------ #
    def _decompose_edge(self, ctx: Dict[str, Any], mu_h: float, mu_a: float,
                        diag: Dict[str, float]) -> Tuple[float, float]:
        q_frac = self._prior(ctx, "quarter_minutes") / self._prior(ctx, "regulation_minutes")
        neutral = bool(ctx.get("neutral_site") or ctx.get("is_neutral") or
                       (isinstance(ctx.get("venue"), dict) and ctx["venue"].get("neutral")))
        hca_prior = 0.0 if neutral else self._prior(ctx, "full_game_hca_points") * q_frac
        sd_hca = self._prior(ctx, "hca_quarter_prior_sd")

        # Strength prior: ratings-implied quarter gap if available, otherwise 0
        r_h = self._team_feature(ctx, "home", "rating")
        r_a = self._team_feature(ctx, "away", "rating")
        if r_h is not None and r_a is not None:
            strength_prior = (r_h - r_a) * q_frac
            sd_str = self._prior(ctx, "strength_quarter_prior_sd") * 0.5
        else:
            strength_prior = 0.0
            sd_str = self._prior(ctx, "strength_quarter_prior_sd")

        # Observed gap = strength + hca + noise. The joint Gaussian posterior
        # mean of (strength + hca) given the gap gives the margin mean.
        gap = mu_h - mu_a
        prior_mean = strength_prior + hca_prior
        prior_var = sd_str ** 2 + sd_hca ** 2
        noise_var = self._prior(ctx, "gap_signal_sd") ** 2
        w = prior_var / (prior_var + noise_var)
        post_margin = prior_mean + w * (gap - prior_mean)

        total = mu_h + mu_a
        new_h = 0.5 * (total + post_margin)
        new_a = 0.5 * (total - post_margin)
        diag.update({"gap_raw": gap, "gap_posterior": post_margin, "hca_quarter_prior": hca_prior,
                     "shrink_weight": w})
        floor = np.finfo(float).eps
        return max(new_h, floor), max(new_a, floor)

    # ------------------------------------------------------------------ #
    # Layer C: possession count law
    # ------------------------------------------------------------------ #
    def _expected_possessions(self, ctx: Dict[str, Any]) -> float:
        q_frac = self._prior(ctx, "quarter_minutes") / self._prior(ctx, "regulation_minutes")
        pace_prior = self._prior(ctx, "pace_per40_prior")
        pace_h = self._team_feature(ctx, "home", "pace")
        pace_a = self._team_feature(ctx, "away", "pace")
        explicit = self._get(ctx, "expected_possessions", "quarter_possessions")
        if explicit is not None and explicit > 0:
            return explicit
        el = self._prior(ctx, "tempo_elasticity")
        ratio_h = (pace_h / pace_prior) if pace_h else 1.0
        ratio_a = (pace_a / pace_prior) if pace_a else 1.0
        pace = pace_prior * (math.sqrt(ratio_h * ratio_a) ** el)
        return pace * q_frac

    def _count_pmf(self, ctx: Dict[str, Any], mean_n: float) -> Tuple[np.ndarray, np.ndarray]:
        d = self._get(ctx, "dispersion_index", "possession_dispersion_index")
        d = d if (d is not None and d > 0) else self._prior(ctx, "possession_dispersion_index")
        tail = self._prior(ctx, "count_tail_mass")
        rel = abs(d - 1.0)

        if rel < 1e-9:
            dist = stats.poisson(mean_n)
        elif d < 1.0:
            # Binomial with Var/Mean = 1 - p
            p = 1.0 - d
            n_trials = max(int(round(mean_n / p)), 1)
            p = mean_n / n_trials
            dist = stats.binom(n_trials, min(p, 1.0))
        else:
            # Negative binomial with Var = mean * d
            p = 1.0 / d
            r = mean_n * p / (1.0 - p)
            dist = stats.nbinom(r, p)

        lo = int(max(dist.ppf(tail), 0))
        hi = int(dist.ppf(1.0 - tail)) + 1
        ns = np.arange(lo, hi + 1)
        pmf = dist.pmf(ns)
        pmf = pmf / pmf.sum()
        return ns, pmf

    # ------------------------------------------------------------------ #
    # Layer D: per-possession tilted scoring law
    # ------------------------------------------------------------------ #
    def _tilted_law(self, target_ppp: float) -> np.ndarray:
        k = self.POINT_SUPPORT
        max_pts = k[-1]
        target = float(np.clip(target_ppp, 1e-6, max_pts - 1e-6))

        def mean_gap(theta: float) -> float:
            logits = self._log_base + theta * k
            w = np.exp(logits - logits.max())
            w /= w.sum()
            return float(w @ k) - target

        lo, hi = -10.0, 10.0
        while mean_gap(lo) > 0:
            lo *= 2.0
        while mean_gap(hi) < 0:
            hi *= 2.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        logits = self._log_base + theta * k
        w = np.exp(logits - logits.max())
        return w / w.sum()

    # ------------------------------------------------------------------ #
    # Layer E: margin convolution mixed over possession count
    # ------------------------------------------------------------------ #
    @staticmethod
    def _margin_pmf(law_h: np.ndarray, law_a: np.ndarray,
                    ns: np.ndarray, weights: np.ndarray) -> Tuple[np.ndarray, int]:
        k_max = len(law_h) - 1
        n_max = int(ns[-1])
        offset = k_max * n_max  # index of margin 0
        margin = np.zeros(2 * offset + 1)

        # Per-possession margin law D = X_h - X_a; the margin given n is D^{*n}
        d_law = np.convolve(law_h, law_a[::-1])  # support -k_max..k_max
        d_off = k_max

        cur = np.array([1.0])
        cur_off = 0
        weight_by_n = dict(zip(ns.tolist(), weights.tolist()))
        for n in range(0, n_max + 1):
            if n > 0:
                cur = np.convolve(cur, d_law)
                cur_off += d_off
            wn = weight_by_n.get(n)
            if wn:
                start = offset - cur_off
                margin[start:start + len(cur)] += wn * cur
        margin = np.clip(margin, 0.0, None)
        margin /= margin.sum()
        return margin, offset

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def compute_probabilities(self, game_context: Dict[str, Any]) -> Dict[str, float]:
        ctx = game_context or {}
        mu_h, mu_a, diag = self._situational_means(ctx)
        mu_h, mu_a = self._decompose_edge(ctx, mu_h, mu_a, diag)

        mean_n = self._expected_possessions(ctx)
        ns, w_n = self._count_pmf(ctx, mean_n)
        e_n = float(ns @ w_n)

        law_h = self._tilted_law(mu_h / e_n)
        law_a = self._tilted_law(mu_a / e_n)

        margin, zero = self._margin_pmf(law_h, law_a, ns, w_n)
        p_home = float(margin[zero + 1:].sum())
        p_draw = float(margin[zero])
        p_away = float(margin[:zero].sum())
        s = p_home + p_draw + p_away

        support = np.arange(len(margin)) - zero
        m_mean = float(support @ margin)
        m_var = float(((support - m_mean) ** 2) @ margin)

        self.last_diagnostics = {
            **diag,
            "mu_h_final": mu_h, "mu_a_final": mu_a,
            "expected_possessions": e_n,
            "ppp_home": mu_h / e_n, "ppp_away": mu_a / e_n,
            "margin_mean": m_mean, "margin_var": m_var,
            "skellam_var_equiv": mu_h + mu_a,
            "variance_inflation_vs_skellam": m_var / (mu_h + mu_a),
            "p_tie": p_draw / s,
            "p_home_no_tie": p_home / max(p_home + p_away, np.finfo(float).tiny),
        }
        return {"home": p_home / s, "draw": p_draw / s, "away": p_away / s}

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        probs = self.compute_probabilities(game_context)
        if DiscreteDistribution is None:
            raise RuntimeError("DiscreteDistribution is not available in the execution scope.")
        try:
            return DiscreteDistribution(probabilities=probs)
        except TypeError:
            return DiscreteDistribution.from_probabilities(probs["home"], probs["draw"], probs["away"])

    def get_params(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id, "version": self.version, "league": self.league,
            "scope": self.scope, "family": self.family, "priors": asdict(self.priors),
        }


# Canonical alias for engine discovery
CandidateModel = NCAAWQuarter3MoneylineModel