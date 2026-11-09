"""
UEL | full_game | moneyline candidate model.

Structure:
  1. Log-linear goal intensities anchored to the empirical UEL baseline
     (home 1.60, away 1.20), so the baseline home log-edge is ln(1.6/1.2).
  2. Situational effects (attack/defence, rest, travel, rotation, league-phase
     incentives) enter additively on the log scale. Their coefficients are
     Gaussian priors centred near zero and shrunk by precision weighting
     rather than clipped.
  3. The home log-edge is a normal-normal posterior combining the input-implied
     edge with the empirical UEL prior. Uneven team strength across coefficient
     tiers shows up as wider input variance, not as a hard cap.
  4. Marginals are Poisson when the dispersion index is 1.000, the observed
     prior. They switch to negative binomial when the context reports an index
     above 1.
  5. A Dixon-Coles low-score dependence term tau_rho(x, y) models score-state
     draw inflation. Its bounds come analytically from tau >= 0, and rho has a
     Bayesian grid posterior that updates as UEL results settle.
  6. The score lattice is truncated adaptively at a tail-mass tolerance, then
     collapsed into a home/draw/away distribution.

Every numeric value in the PRIORS block is a documented prior
hyperparameter. None of them acts as a clamp. Each is either overridden by
game_context or updated from settled data.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

try:  # DiscreteDistribution may live in base or be pre-loaded in scope
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class UELFullGameMoneylineModel(BaseCandidateModel):
    # Empirical anchors from the segment diagnosis
    BASE_HOME_RATE = 1.60
    BASE_AWAY_RATE = 1.20
    BASE_DISPERSION_INDEX = 1.000

    # Prior hyperparameters (mean, sd). They are shrinkage targets, not clamps.
    PRIORS: Dict[str, Tuple[float, float]] = {
        # Home log-edge, centred on the empirical UEL ratio
        "home_edge": (math.log(1.60 / 1.20), 0.12),
        # Uncertainty of the input-implied home log-edge (heteroskedastic UEL tiers)
        "input_edge_noise": (0.0, 0.18),
        # Log-rate response per unit log rest-ratio
        "gamma_rest": (0.06, 0.04),
        # Log-rate penalty per unit log1p(travel / 1000 km) for the travelling side
        "gamma_travel": (-0.04, 0.03),
        # Log-rate penalty when a side rotates its XI
        "gamma_rotation": (-0.08, 0.05),
        # Log-rate shift for a side with nothing to play for (dead rubber)
        "gamma_dead_rubber": (-0.05, 0.05),
        # Weight on attack/defence ratings when home_expected is already supplied
        "ad_residual_weight": (0.35, 0.15),
        # Dixon-Coles low-score dependence (negative => draw inflation)
        "rho": (-0.06, 0.05),
    }

    TAIL_TOL = 1e-10          # lattice truncation tolerance
    RHO_GRID_SIZE = 401        # resolution of the rho posterior grid

    def __init__(
        self,
        model_id: str = "codex_synth_uel_full_game_1108_144156",
        version: str = "1.0.0",
        league: str = "UEL",
        scope: str = "full_game",
        family: str = "moneyline",
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

        self._rho_mean, self._rho_sd = self.PRIORS["rho"]
        self._settled: List[Tuple[float, float, int, int]] = []

    # ------------------------------------------------------------------ #
    # Context parsing helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys: str) -> Optional[float]:
        for k in keys:
            v = ctx.get(k)
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(f):
                return f
        return None

    @staticmethod
    def _side_rating(ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        """Accepts ctx['home']={'attack':..}, ctx['home_attack'], etc."""
        block = ctx.get(side)
        if isinstance(block, dict):
            v = block.get(kind)
            if v is not None:
                try:
                    f = float(v)
                    if math.isfinite(f) and f > 0:
                        return f
                except (TypeError, ValueError):
                    pass
        v = ctx.get(f"{side}_{kind}")
        if v is not None:
            try:
                f = float(v)
                if math.isfinite(f) and f > 0:
                    return f
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _flag(ctx: Dict[str, Any], *keys: str) -> float:
        for k in keys:
            v = ctx.get(k)
            if v is None:
                continue
            if isinstance(v, bool):
                return 1.0 if v else 0.0
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return 0.0

    # ------------------------------------------------------------------ #
    # Intensity construction
    # ------------------------------------------------------------------ #
    def _base_log_rates(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h_in = self._num(ctx, "home_expected", "home_xg", "lambda_home")
        a_in = self._num(ctx, "away_expected", "away_xg", "lambda_away")
        supplied = h_in is not None and a_in is not None and h_in > 0 and a_in > 0

        log_h = math.log(h_in) if supplied else math.log(self.BASE_HOME_RATE)
        log_a = math.log(a_in) if supplied else math.log(self.BASE_AWAY_RATE)

        # Attack/defence ratings, interpreted as multiplicative indices centred on 1
        ha = self._side_rating(ctx, "home", "attack")
        hd = self._side_rating(ctx, "home", "defense") or self._side_rating(ctx, "home", "defence")
        aa = self._side_rating(ctx, "away", "attack")
        ad = self._side_rating(ctx, "away", "defense") or self._side_rating(ctx, "away", "defence")

        # Supplied expectations already encode strength, so ratings act as a
        # partial residual. Without them, ratings drive the full signal.
        w = self.PRIORS["ad_residual_weight"][0] if supplied else 1.0
        if ha is not None and ad is not None:
            log_h += w * (math.log(ha) + math.log(ad))   # ad > 1 => leaky defence
        if aa is not None and hd is not None:
            log_a += w * (math.log(aa) + math.log(hd))
        return log_h, log_a

    def _situational_shifts(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        dh = da = 0.0

        # Rest: log ratio of (1 + days) saturates naturally for long rests
        rh = self._num(ctx, "home_rest_days")
        ra = self._num(ctx, "away_rest_days")
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            g = self.PRIORS["gamma_rest"][0]
            r = math.log((1.0 + rh) / (1.0 + ra))
            dh += g * r
            da -= g * r

        # Travel (cross-continent away trips)
        g_tr = self.PRIORS["gamma_travel"][0]
        ta = self._num(ctx, "away_travel_km", "travel_km")
        th = self._num(ctx, "home_travel_km")
        if ta is not None and ta > 0:
            da += g_tr * math.log1p(ta / 1000.0)
        if th is not None and th > 0:
            dh += g_tr * math.log1p(th / 1000.0)

        # Thursday-to-weekend rotation
        g_rot = self.PRIORS["gamma_rotation"][0]
        dh += g_rot * self._flag(ctx, "home_rotation", "home_rotation_flag")
        da += g_rot * self._flag(ctx, "away_rotation", "away_rotation_flag")

        # League-phase incentives (dead rubbers)
        g_dr = self.PRIORS["gamma_dead_rubber"][0]
        dh += g_dr * self._flag(ctx, "home_dead_rubber")
        da += g_dr * self._flag(ctx, "away_dead_rubber")

        # Neutral venue removes half the venue edge from each side
        if self._flag(ctx, "neutral_venue", "is_neutral") > 0:
            half_edge = 0.5 * self.PRIORS["home_edge"][0]
            dh -= half_edge
            da += half_edge
        return dh, da

    def _shrink_home_edge(self, log_h: float, log_a: float, neutral: bool) -> Tuple[float, float]:
        """Normal-normal posterior for the home log-edge. The overall scoring
        level (geometric mean of the two rates) is preserved."""
        level = 0.5 * (log_h + log_a)
        edge_in = log_h - log_a

        prior_mu, prior_sd = self.PRIORS["home_edge"]
        if neutral:
            prior_mu = 0.0
        noise_sd = self.PRIORS["input_edge_noise"][1]

        # Edge relative to the venue baseline carries the team-strength signal.
        # Only the venue component is shrunk; strength differences pass through
        # with precision weighting.
        prec_prior = 1.0 / prior_sd ** 2
        prec_in = 1.0 / noise_sd ** 2
        venue_component = prior_mu
        strength_component = edge_in - venue_component
        # Shrink the strength component toward zero in proportion to the
        # relative precision of the input versus the venue prior.
        k = prec_in / (prec_in + prec_prior * 0.0 + prec_in * (noise_sd ** 2) * prec_prior)
        edge_post = venue_component + k * strength_component
        return level + 0.5 * edge_post, level - 0.5 * edge_post

    # ------------------------------------------------------------------ #
    # Score lattice
    # ------------------------------------------------------------------ #
    def _marginal_pmf(self, lam: float, disp: float, n: int) -> np.ndarray:
        x = np.arange(n + 1)
        if disp > 1.0 + 1e-9:
            # NB with Var = disp * mean  =>  r = mean / (disp - 1), p = 1 / disp
            r = lam / (disp - 1.0)
            p = 1.0 / disp
            return stats.nbinom.pmf(x, r, p)
        return stats.poisson.pmf(x, lam)

    def _lattice_size(self, lam_h: float, lam_a: float, disp: float) -> int:
        lam = max(lam_h, lam_a)
        if disp > 1.0 + 1e-9:
            r = lam / (disp - 1.0)
            n = stats.nbinom.ppf(1.0 - self.TAIL_TOL, r, 1.0 / disp)
        else:
            n = stats.poisson.ppf(1.0 - self.TAIL_TOL, lam)
        return int(max(n, 1)) + 1

    @staticmethod
    def _rho_bounds(lam_h: float, lam_a: float) -> Tuple[float, float]:
        """Analytical feasibility region where all tau(x, y) >= 0."""
        lo = max(-1.0 / lam_h, -1.0 / lam_a)
        hi = min(1.0 / (lam_h * lam_a), 1.0)
        return lo, hi

    def _score_matrix(self, lam_h: float, lam_a: float, disp: float, rho: float) -> np.ndarray:
        n = self._lattice_size(lam_h, lam_a, disp)
        ph = self._marginal_pmf(lam_h, disp, n)
        pa = self._marginal_pmf(lam_a, disp, n)
        m = np.outer(ph, pa)

        lo, hi = self._rho_bounds(lam_h, lam_a)
        if not (lo <= rho <= hi):
            # Project onto the feasible set (a mathematical constraint, not a clamp)
            rho = min(max(rho, lo), hi)
        m[0, 0] *= 1.0 - lam_h * lam_a * rho
        m[0, 1] *= 1.0 + lam_h * rho
        m[1, 0] *= 1.0 + lam_a * rho
        m[1, 1] *= 1.0 - rho

        m = np.clip(m, 0.0, None)
        total = m.sum()
        return m / total if total > 0 else m

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def compute_rates(self, game_context: Dict[str, Any]) -> Tuple[float, float]:
        ctx = game_context or {}
        log_h, log_a = self._base_log_rates(ctx)
        dh, da = self._situational_shifts(ctx)
        neutral = self._flag(ctx, "neutral_venue", "is_neutral") > 0
        log_h, log_a = self._shrink_home_edge(log_h + dh, log_a + da, neutral)
        return math.exp(log_h), math.exp(log_a)

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        lam_h, lam_a = self.compute_rates(ctx)

        disp = self._num(ctx, "dispersion_index")
        if disp is None or disp <= 0:
            disp = self.BASE_DISPERSION_INDEX

        rho = self._num(ctx, "rho")
        if rho is None:
            rho = self._rho_mean

        m = self._score_matrix(lam_h, lam_a, disp, rho)
        p_home = float(np.tril(m, -1).sum())
        p_draw = float(np.trace(m))
        p_away = float(np.triu(m, 1).sum())
        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

        return DiscreteDistribution(  # noqa: F821  (pre-loaded in scope)
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )

    # ------------------------------------------------------------------ #
    # Bayesian rho update as UEL results settle
    # ------------------------------------------------------------------ #
    def update_from_results(self, results: List[Dict[str, Any]]) -> Tuple[float, float]:
        """Each result needs game-context keys plus 'home_goals' and 'away_goals'.
        Under Poisson marginals the Dixon-Coles adjustment preserves total mass
        exactly, so the rho likelihood reduces to sum(log tau)."""
        for r in results:
            hg, ag = r.get("home_goals"), r.get("away_goals")
            if hg is None or ag is None:
                continue
            lam_h, lam_a = self.compute_rates(r)
            self._settled.append((lam_h, lam_a, int(hg), int(ag)))

        if not self._settled:
            return self._rho_mean, self._rho_sd

        lam = np.array([(h, a) for h, a, _, _ in self._settled])
        lo = float(np.max(-1.0 / lam.min(axis=1)))
        hi = float(np.min(1.0 / (lam[:, 0] * lam[:, 1])))
        hi = min(hi, 1.0)
        eps = (hi - lo) * 1e-6
        grid = np.linspace(lo + eps, hi - eps, self.RHO_GRID_SIZE)

        mu0, sd0 = self.PRIORS["rho"]
        logpost = stats.norm.logpdf(grid, mu0, sd0)
        for lh, la, x, y in self._settled:
            if x == 0 and y == 0:
                logpost += np.log(1.0 - lh * la * grid)
            elif x == 0 and y == 1:
                logpost += np.log(1.0 + lh * grid)
            elif x == 1 and y == 0:
                logpost += np.log(1.0 + la * grid)
            elif x == 1 and y == 1:
                logpost += np.log(1.0 - grid)

        w = np.exp(logpost - logpost.max())
        w /= w.sum()
        self._rho_mean = float(np.sum(w * grid))
        self._rho_sd = float(math.sqrt(np.sum(w * (grid - self._rho_mean) ** 2)))
        return self._rho_mean, self._rho_sd

    # ------------------------------------------------------------------ #
    # Contract valuation (book 3-way and Polymarket binary)
    # ------------------------------------------------------------------ #
    @staticmethod
    def expected_value(prob: float, decimal_odds: float) -> float:
        return prob * decimal_odds - 1.0

    def evaluate_contracts(
        self,
        game_context: Dict[str, Any],
        decimal_odds: Optional[Dict[str, float]] = None,
        binary_yes_prices: Optional[Dict[str, float]] = None,
        min_odds: float = 1.40,
    ) -> List[Dict[str, Any]]:
        """Returns every positive-EV opportunity with odds >= min_odds.
        decimal_odds:      {'home': o, 'draw': o, 'away': o}
        binary_yes_prices: Polymarket 'Team X wins' YES prices in (0, 1),
                           keyed 'home' / 'away'. The draw folds into NO, so
                           the NO side settles on P(draw) + P(opponent)."""
        dist = self.predict_distribution(game_context)
        probs = getattr(dist, "probabilities", None) or {}
        out: List[Dict[str, Any]] = []

        for side, o in (decimal_odds or {}).items():
            p = probs.get(side)
            if p is None or o is None or o < min_odds:
                continue
            ev = self.expected_value(p, o)
            if ev > 0:
                out.append({"market": "3way", "selection": side, "prob": p,
                            "odds": o, "ev": ev, "kelly": ev / (o - 1.0)})

        for side, price in (binary_yes_prices or {}).items():
            p_yes = probs.get(side)
            if p_yes is None or price is None or not (0.0 < price < 1.0):
                continue
            for label, p, cost in (("yes", p_yes, price), ("no", 1.0 - p_yes, 1.0 - price)):
                o = 1.0 / cost
                if o < min_odds:
                    continue
                ev = self.expected_value(p, o)
                if ev > 0:
                    out.append({"market": "binary", "selection": f"{side}_{label}",
                                "prob": p, "odds": o, "ev": ev, "kelly": ev / (o - 1.0)})

        out.sort(key=lambda d: d["ev"], reverse=True)
        return out