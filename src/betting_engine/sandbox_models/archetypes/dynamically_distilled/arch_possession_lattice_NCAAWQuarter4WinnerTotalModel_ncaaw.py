import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # Resolve the distribution primitive from the engine, falling back to execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # pre-loaded quant primitive


class NCAAWQuarter4WinnerTotalModel(BaseCandidateModel):
    """
    NCAAW | quarter_4 | winner_total: possession-lattice compound model.

    Structure, derived from basketball mechanics rather than a generic count model:

      1. Possessions. In a fixed 10-minute window both teams draw on one shared
         possession count N, because possessions alternate. N is a renewal count
         over variable possession durations, so
             Var(N) ~= E[N] * CV_dur^2.
         That is underdispersed relative to Poisson. Late-game fouling lengthens
         the duration tail.

      2. Per-possession yield X on {0, 1, 2, 3}. We use the maximum-entropy
         distribution relative to a structural NCAAW possession-outcome base
         measure, subject to E[X] = PPP:
             p_k proportional to w_k * exp(theta * k),
         with theta solved by root-finding. As the expected PPP moves, the shot
         mix tilts continuously.

      3. Team points:
             S_team | N = X_1 + ... + X_N   (exact N-fold convolution)
         The joint score matrix is
             P(h, a) = sum_n P(N = n) * f_h^{*n}(h) * f_a^{*n}(a).
         Shared pace induces positive total covariance. The integer lattice of
         1/2/3-point outcomes produces a realistic tied-quarter mass.

      4. Dispersion. The observed Var/Mean index is precision-weighted against the
         structural (renewal) dispersion using its sample count. A cold-start
         placeholder of 1.000 with n = 0 therefore receives zero weight, and
         unobserved values cannot distort the variance.

      5. Situational scaling. Log-linear adjustments come from attack/defense,
         tempo, rest (saturating tanh), and venue. Home edge is shrunk toward the
         empirical baseline log-ratio with credibility weighting, which prevents
         home-court over-confidence.

    All tunable quantities live in PRIORS and can be overridden per call via
    game_context['model_priors'].
    """

    # Domain priors (overridable). These are structural beliefs, not output clamps.
    PRIORS: Dict[str, float] = {
        # Empirical Q4 anchors for this segment
        "baseline_home_q4": 20.00,
        "baseline_away_q4": 18.75,
        # League full-game scoring prior (used only to detect full-game inputs)
        "league_full_game_team_pts": 68.0,
        # Possession-outcome base measure for NCAAW (empty, FT-only, 2-pt, 3-pt)
        "w0": 0.53,
        "w1": 0.06,
        "w2": 0.29,
        "w3": 0.12,
        # League points-per-possession prior for Q4 (includes late-game FT inflation)
        "ppp_prior": 1.03,
        # Coefficient of variation of possession duration (renewal pace variance)
        "possession_duration_cv": 0.55,
        # Credibility mass (pseudo-games) for the observed dispersion index
        "dispersion_prior_n": 40.0,
        # Credibility mass for the context-supplied home/away ratio vs. empirical ratio
        "venue_ratio_prior_n": 12.0,
        # Elasticities of log scoring rate
        "attack_defense_elasticity": 0.35,
        "rest_effect": 0.015,
        "rest_scale_days": 3.0,
        "tempo_blend": 0.5,
        # Numerical tail tolerance (support truncation; mass is renormalized)
        "tail_tol": 1e-10,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_4_1107_232121",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_4",
        family: str = "winner_total",
    ):
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
        self.last_score_matrix: Optional[np.ndarray] = None
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _f(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            return v if math.isfinite(v) else default
        except (TypeError, ValueError):
            return default

    def _priors(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        p = dict(self.PRIORS)
        override = ctx.get("model_priors")
        if isinstance(override, dict):
            for k, v in override.items():
                fv = self._f(v)
                if fv is not None:
                    p[k] = fv
        return p

    @staticmethod
    def _rating_to_log(v: Optional[float]) -> float:
        """
        Interpret a rating either as a multiplicative strength (centered at 1)
        or as a log/additive offset (centered at 0).
        """
        if v is None:
            return 0.0
        if v > 0.5:
            return math.log(v)
        return v

    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> float:
        node = ctx.get(side)
        val = None
        if isinstance(node, dict):
            val = self._f(node.get(kind))
            if val is None:
                val = self._f(node.get(f"{kind}_rating"))
        if val is None:
            val = self._f(ctx.get(f"{side}_{kind}"))
        return self._rating_to_log(val)

    # ------------------------------------------------------ rate construction
    def _expected_points(self, ctx: Dict[str, Any], P: Dict[str, float]) -> Tuple[float, float]:
        bh, ba = P["baseline_home_q4"], P["baseline_away_q4"]
        h = self._f(ctx.get("home_expected"))
        a = self._f(ctx.get("away_expected"))
        if h is None or h <= 0:
            h = bh
        if a is None or a <= 0:
            a = ba

        # Detect full-game expectations and project them to Q4 using the empirical Q4 share.
        q4_share = (bh + ba) / (2.0 * P["league_full_game_team_pts"])
        if (h + a) > 2.0 * (bh + ba):
            h, a = h * q4_share, a * q4_share

        # Matchup exposure: home attack vs away defense, and vice versa.
        # A positive defense rating means a stronger (point-suppressing) defense.
        el = P["attack_defense_elasticity"]
        log_h = math.log(h) + el * (
            self._team_rating(ctx, "home", "attack") - self._team_rating(ctx, "away", "defense")
        )
        log_a = math.log(a) + el * (
            self._team_rating(ctx, "away", "attack") - self._team_rating(ctx, "home", "defense")
        )

        # Rest differential with saturating response.
        hr = self._f(ctx.get("home_rest_days"))
        ar = self._f(ctx.get("away_rest_days"))
        if hr is not None and ar is not None:
            r = P["rest_effect"] * math.tanh((hr - ar) / max(P["rest_scale_days"], 1e-9))
            log_h += r
            log_a -= r

        # Neutral site removes the venue component of the empirical baseline asymmetry.
        venue = ctx.get("venue")
        neutral = bool(ctx.get("neutral_site")) or (
            isinstance(venue, dict) and bool(venue.get("neutral"))
        )
        base_log_ratio = 0.0 if neutral else math.log(bh / ba)

        # Credibility shrinkage of the home/away log-ratio (limits home over-confidence).
        n_ctx = self._f(ctx.get("games_played"), None)
        if n_ctx is None:
            hs = ctx.get("home") if isinstance(ctx.get("home"), dict) else {}
            aw = ctx.get("away") if isinstance(ctx.get("away"), dict) else {}
            gh = self._f(hs.get("games_played"), None) if hs else None
            ga = self._f(aw.get("games_played"), None) if aw else None
            n_ctx = min(gh, ga) if (gh is not None and ga is not None) else P["venue_ratio_prior_n"]
        z = n_ctx / (n_ctx + P["venue_ratio_prior_n"])

        mean_log = 0.5 * (log_h + log_a)
        ratio = z * (log_h - log_a) + (1.0 - z) * base_log_ratio
        return math.exp(mean_log + 0.5 * ratio), math.exp(mean_log - 0.5 * ratio)

    def _possessions(self, ctx: Dict[str, Any], mu_h: float, mu_a: float, P: Dict[str, float]) -> float:
        m_implied = (mu_h + mu_a) / (2.0 * P["ppp_prior"])
        tempos = []
        for key in ("pace", "tempo"):
            v = self._f(ctx.get(key))
            if v:
                tempos.append(v)
        for side in ("home", "away"):
            v = self._f(ctx.get(f"{side}_pace")) or self._f(ctx.get(f"{side}_tempo"))
            node = ctx.get(side)
            if v is None and isinstance(node, dict):
                v = self._f(node.get("pace")) or self._f(node.get("tempo"))
            if v:
                tempos.append(v)
        if not tempos:
            return m_implied
        t = float(np.mean(tempos))
        # Tempo is reported per 40 minutes; Q4 inherits the same Q4/full-game uplift as scoring.
        q4_share = (P["baseline_home_q4"] + P["baseline_away_q4"]) / (
            2.0 * P["league_full_game_team_pts"]
        )
        m_ctx = t * q4_share if t > 2.0 * m_implied else t
        w = P["tempo_blend"]
        return math.exp(w * math.log(m_ctx) + (1.0 - w) * math.log(m_implied))

    # ------------------------------------------------------ lattice mechanics
    @staticmethod
    def _tilted_pmf(base: np.ndarray, mu: float) -> np.ndarray:
        """Maximum-entropy possession pmf on {0, 1, 2, 3} with E[X] = mu, relative to base."""
        k = np.arange(len(base), dtype=float)
        eps = 1e-6
        mu = float(np.clip(mu, eps, k[-1] - eps))  # feasibility of the support, not a model clamp

        def mean_gap(theta: float) -> float:
            w = base * np.exp(theta * k - theta * k.max())
            return float((w * k).sum() / w.sum()) - mu

        lo, hi = -60.0, 60.0
        theta = optimize.brentq(mean_gap, lo, hi, xtol=1e-12)
        w = base * np.exp(theta * k - theta * k.max())
        return w / w.sum()

    def _possession_count_pmf(self, m: float, var: float, tol: float) -> np.ndarray:
        sd = math.sqrt(max(var, 1e-9))
        z = abs(stats.norm.ppf(tol))
        n_max = int(math.ceil(m + z * sd)) + 1
        edges = np.arange(-0.5, n_max + 1.0, 1.0)
        cdf = stats.norm.cdf(edges, loc=m, scale=sd)
        pmf = np.diff(cdf)
        pmf[0] += cdf[0]  # fold the negative tail into N = 0
        pmf = np.clip(pmf, 0.0, None)
        return pmf / pmf.sum()

    def _dispersion_target(self, ctx: Dict[str, Any], P: Dict[str, float], structural_D: float) -> float:
        obs = self._f(ctx.get("dispersion_index"))
        n_obs = self._f(ctx.get("dispersion_sample_size"), 0.0) or 0.0
        if obs is None or obs <= 0 or n_obs <= 0:
            return structural_D
        w = n_obs / (n_obs + P["dispersion_prior_n"])
        return w * obs + (1.0 - w) * structural_D

    def score_matrix(self, ctx: Dict[str, Any]) -> np.ndarray:
        P = self._priors(ctx)
        mu_h, mu_a = self._expected_points(ctx, P)
        m = self._possessions(ctx, mu_h, mu_a, P)

        base = np.array([P["w0"], P["w1"], P["w2"], P["w3"]], dtype=float)
        base = base / base.sum()
        ph = self._tilted_pmf(base, mu_h / m)
        pa = self._tilted_pmf(base, mu_a / m)

        k = np.arange(4, dtype=float)
        ex = 0.5 * (float((ph * k).sum()) + float((pa * k).sum()))
        vx = 0.5 * (
            float((ph * k**2).sum()) - float((ph * k).sum()) ** 2
            + float((pa * k**2).sum()) - float((pa * k).sum()) ** 2
        )

        # Renewal pace variance, then reconcile with the credibility-weighted dispersion target.
        var_n_struct = m * P["possession_duration_cv"] ** 2
        mean_team = 0.5 * (mu_h + mu_a)
        D_struct = (m * vx + var_n_struct * ex**2) / mean_team
        D_target = self._dispersion_target(ctx, P, D_struct)
        var_n = max((D_target * mean_team - m * vx) / ex**2, var_n_struct * 1e-3)

        tol = P["tail_tol"]
        pN = self._possession_count_pmf(m, var_n, tol)
        n_max = len(pN) - 1
        size = 3 * n_max + 1
        M = np.zeros((size, size))
        fh = np.array([1.0])
        fa = np.array([1.0])
        for n in range(n_max + 1):
            if pN[n] > tol * 1e-3:
                M[: len(fh), : len(fa)] += pN[n] * np.outer(fh, fa)
            fh = np.convolve(fh, ph)
            fa = np.convolve(fa, pa)

        # Trim the negligible tail and renormalize.
        rows = np.cumsum(M.sum(axis=1))
        cols = np.cumsum(M.sum(axis=0))
        rh = int(np.searchsorted(rows, 1.0 - tol)) + 1
        ca = int(np.searchsorted(cols, 1.0 - tol)) + 1
        M = M[: min(rh, size), : min(ca, size)]
        M /= M.sum()

        hh = np.arange(M.shape[0])
        aa = np.arange(M.shape[1])
        self.last_score_matrix = M
        self.last_diagnostics = {
            "mu_home": mu_h,
            "mu_away": mu_a,
            "possessions": m,
            "var_possessions": var_n,
            "ppp_home": mu_h / m,
            "ppp_away": mu_a / m,
            "dispersion_structural": D_struct,
            "dispersion_used": D_target,
            "model_mean_home": float((M.sum(axis=1) * hh).sum()),
            "model_mean_away": float((M.sum(axis=0) * aa).sum()),
        }
        return M

    # ------------------------------------------------------- market helpers
    @staticmethod
    def _outcomes(M: np.ndarray) -> Tuple[float, float, float]:
        h_idx, a_idx = np.indices(M.shape)
        p_home = float(M[h_idx > a_idx].sum())
        p_draw = float(M[h_idx == a_idx].sum())
        p_away = float(M[h_idx < a_idx].sum())
        s = p_home + p_draw + p_away
        return p_home / s, p_draw / s, p_away / s

    def total_probabilities(self, game_context: Dict[str, Any], line: float) -> Dict[str, float]:
        M = self.score_matrix(game_context)
        h_idx, a_idx = np.indices(M.shape)
        tot = h_idx + a_idx
        return {
            "over": float(M[tot > line].sum()),
            "push": float(M[tot == line].sum()),
            "under": float(M[tot < line].sum()),
        }

    def winner_total_probabilities(
        self, game_context: Dict[str, Any], line: float
    ) -> Dict[str, float]:
        """Joint winner × total cells (e.g., 'home_over', 'away_under', 'draw_push')."""
        M = self.score_matrix(game_context)
        h_idx, a_idx = np.indices(M.shape)
        tot = h_idx + a_idx
        win = {"home": h_idx > a_idx, "draw": h_idx == a_idx, "away": h_idx < a_idx}
        tl = {"over": tot > line, "push": tot == line, "under": tot < line}
        return {f"{w}_{t}": float(M[wm & tm].sum()) for w, wm in win.items() for t, tm in tl.items()}

    @staticmethod
    def expected_value(prob_win: float, decimal_odds: float, prob_push: float = 0.0) -> float:
        """EV per unit stake. Eligibility is continuous: odds >= 1.40 with EV > 0."""
        return prob_win * (decimal_odds - 1.0) - (1.0 - prob_win - prob_push)

    # ------------------------------------------------------------- interface
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        M = self.score_matrix(ctx)
        p_home, p_draw, p_away = self._outcomes(M)

        scores = {
            (int(h), int(a)): float(M[h, a])
            for h in range(M.shape[0])
            for a in range(M.shape[1])
            if M[h, a] > 0.0
        }
        builder = getattr(DiscreteDistribution, "from_scores", None)
        if callable(builder):
            for payload in (scores, M):
                try:
                    return builder(payload)
                except Exception:
                    continue
        builder = getattr(DiscreteDistribution, "from_probabilities", None)
        if callable(builder):
            try:
                return builder(p_home, p_draw, p_away)
            except Exception:
                pass
        return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})