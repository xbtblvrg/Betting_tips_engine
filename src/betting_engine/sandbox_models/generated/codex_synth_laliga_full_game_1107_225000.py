"""
LALIGA | full_game | spreads candidate model.

The model is a game-state-dependent bivariate goal process with exact Asian
handicap settlement over the margin pmf.

Structure
---------
1. Pre-match intensities mu_H, mu_A start from the empirical anchors
   (home 1.60, away 1.20). They are then scaled in log space by:
   - attack/defense exposure,
   - pace/tempo,
   - rest-day differential,
   - a hierarchically shrunk home-advantage offset (replacing the raw
     home/away ratio).

2. In-play, each team scores as a Poisson process whose rate depends on the
   current signed goal difference s(t) from that team's perspective:
       lambda_i(t) = c_i * mu_i * exp(-b_lead * max(s_i, 0) + b_trail * max(-s_i, 0))
   A leading team throttles its attack and a trailing team pushes. This thins
   the favourite tail and adds mass near zero margin.
   - The scale factors c_i are solved by fixed-point iteration so that the
     integrated expected goals equal the pre-match targets. Game-state
     dynamics redistribute mass but leave the means unchanged.
   - The forward Kolmogorov equations are integrated exactly on a truncated
     score lattice, using competing-exponential transitions per time slice.

3. A residual Dixon-Coles tau(rho) correction is applied to the low-score
   cells. rho is solved so that P(draw) matches the league draw rate. The
   rate comes from the context if provided, otherwise from the league prior.
   rho is restricted to its mathematically valid region; no ad-hoc clamps.

4. Spreads are priced by exact settlement over the margin pmf P(D = k). This
   covers integer, half and quarter lines, including full-win, half-win,
   push, half-loss and full-loss mass.

Cold start
----------
There are no settled bets. The structural parameters (b_lead, b_trail,
home-advantage prior, draw-rate prior) are documented domain priors. Each one
can be overridden through the context or re-estimated by maximum likelihood
with `fit()` once score histories exist.
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy import optimize

try:  # Normal sandbox path
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - primitives may be pre-loaded in scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)  # type: ignore

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


class LaLigaFullGameSpreadsModel(BaseCandidateModel):  # type: ignore[misc]
    """Game-state bivariate goal process with exact Asian-handicap settlement."""

    # ------------------------------------------------------------------ #
    # Domain priors (cold start). Every value is overridable via the
    # context or re-estimable with fit().
    # ------------------------------------------------------------------ #

    # Empirical anchors supplied for this segment.
    BASE_HOME = 1.60
    BASE_AWAY = 1.20

    # Post-2020 LaLiga home advantage, as a log home/away goal-rate ratio.
    # Embedded raw ratio: log(1.60 / 1.20) ~ 0.288.
    # Post-2020 literature estimate: roughly 0.18-0.22.
    # Prior precision is expressed in "effective seasons" (kappa).
    HFA_PRIOR_LOG = 0.20
    HFA_PRIOR_KAPPA = 3.0

    # League full-time draw-rate prior (roughly 25-27% historically).
    DRAW_RATE_PRIOR = 0.26

    # Game-state elasticities, per goal of lead or deficit, on log intensity.
    BETA_LEAD = 0.12
    BETA_TRAIL = 0.07

    # Rest-day log elasticity on the scoring-rate differential (applied as
    # +/- half to each side).
    REST_ELASTICITY = 0.035

    # Weight given to attack/defense exposure when home_expected/away_expected
    # are supplied. Those expectations usually embed team strength already,
    # so exposure is only partially re-applied to avoid double counting.
    EXPOSURE_WEIGHT_WITH_XG = 0.35

    # Numerical settings. These control resolution only, not the model.
    MAX_GOALS = 12
    TIME_STEPS = 240
    CALIBRATION_ITERS = 6

    MIN_ELIGIBLE_ODDS = 1.40

    def __init__(
        self,
        model_id: str = "codex_synth_laliga_full_game_1107_225000",
        version: str = "1.0.0",
        league: str = "LALIGA",
        scope: str = "full_game",
        family: str = "spreads",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
                **kwargs,
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

        self.beta_lead = float(kwargs.get("beta_lead", self.BETA_LEAD))
        self.beta_trail = float(kwargs.get("beta_trail", self.BETA_TRAIL))
        self.hfa_prior_log = float(kwargs.get("hfa_prior_log", self.HFA_PRIOR_LOG))
        self.draw_rate_prior = float(kwargs.get("draw_rate_prior", self.DRAW_RATE_PRIOR))

        self.last_margin_pmf: Dict[int, float] = {}
        self.last_diagnostics: Dict[str, Any] = {}

    # ================================================================== #
    # Feature extraction helpers
    # ================================================================== #

    @staticmethod
    def _num(x: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            v = float(x)
            if math.isfinite(v):
                return v
        except (TypeError, ValueError):
            pass
        return default

    def _team_block(self, ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        block = ctx.get(side)
        return block if isinstance(block, dict) else {}

    def _log_rating(self, value: Any) -> float:
        """
        Map a rating onto a log multiplicative scale.

        - Strictly positive ratings near 1 are treated as ratios: log(x).
        - Signed ratings are treated as already in log space.
        """
        v = self._num(value)
        if v is None:
            return 0.0
        if v > 0.0 and 0.2 < v < 5.0:
            return math.log(v)
        if -1.5 < v < 1.5:
            return v
        return 0.0  # unknown scale; contribute nothing rather than guess

    def _exposure(self, ctx: Dict[str, Any], side: str, other: str) -> float:
        """Log-scale scoring exposure: own attack plus opponent defensive leak."""
        own = self._team_block(ctx, side)
        opp = self._team_block(ctx, other)

        att = self._log_rating(own.get("attack", ctx.get(f"{side}_attack")))

        # Defense rating convention: >1 or positive means a strong defense, so
        # its effect on the opponent's scoring is negative.
        dfn = self._log_rating(opp.get("defense", ctx.get(f"{other}_defense")))

        return att - dfn

    def _pace_log(self, ctx: Dict[str, Any]) -> float:
        for key in ("pace", "tempo", "pace_factor", "tempo_factor"):
            if key in ctx:
                return self._log_rating(ctx.get(key))

        h = self._team_block(ctx, "home")
        a = self._team_block(ctx, "away")
        hp = self._log_rating(h.get("pace", h.get("tempo")))
        ap = self._log_rating(a.get("pace", a.get("tempo")))
        return 0.5 * (hp + ap)

    def _rest_log_diff(self, ctx: Dict[str, Any]) -> float:
        rh = self._num(ctx.get("home_rest_days"))
        ra = self._num(ctx.get("away_rest_days"))
        if rh is None or ra is None:
            return 0.0
        # Diminishing returns: log1p captures that the gap between 3 and 6
        # days matters far more than the gap between 10 and 13.
        return self.REST_ELASTICITY * (math.log1p(max(rh, 0.0)) - math.log1p(max(ra, 0.0)))

    def _weather_log(self, ctx: Dict[str, Any]) -> float:
        """Small total-goal suppression from strong wind or extreme heat."""
        if ctx.get("is_dome"):
            return 0.0
        wind = self._num(ctx.get("wind_speed"), 0.0) or 0.0
        temp = self._num(ctx.get("temp"))

        # Wind (km/h or mph scale-agnostic, via a soft threshold): a smooth
        # penalty that only bites at high values.
        w = -0.03 * math.log1p(math.exp((wind - 30.0) / 6.0))

        # Heat: Spanish summer kick-offs above ~30C reduce tempo.
        t = 0.0 if temp is None else -0.02 * math.log1p(math.exp((temp - 30.0) / 3.0))
        return w + t

    # ================================================================== #
    # Home advantage: hierarchical shrinkage
    # ================================================================== #

    def _posterior_hfa(self, ctx: Dict[str, Any]) -> float:
        """
        Precision-weighted posterior of the log home/away rate ratio.

        Levels of the hierarchy:
        - League post-2020 prior (HFA_PRIOR_LOG, precision HFA_PRIOR_KAPPA).
        - The anchor-embedded ratio log(1.60 / 1.20), treated as one
          effective season of evidence because it mixes pre- and post-2020
          regimes.
        - An optional venue-specific estimate with its own sample size
          (seasons), via ctx['venue_hfa_log'] and ctx['venue_hfa_n'].
        """
        prior = self._num(ctx.get("league_hfa_log"), self.hfa_prior_log)
        k0 = self.HFA_PRIOR_KAPPA

        embedded = math.log(self.BASE_HOME / self.BASE_AWAY)
        k1 = 1.0

        num = k0 * prior + k1 * embedded
        den = k0 + k1

        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}
        v_est = self._num(ctx.get("venue_hfa_log", venue.get("hfa_log")))
        v_n = self._num(ctx.get("venue_hfa_n", venue.get("hfa_n")), 0.0) or 0.0
        if v_est is not None and v_n > 0.0:
            num += v_n * v_est
            den += v_n

        return num / den

    # ================================================================== #
    # Pre-match intensities
    # ================================================================== #

    def _pre_match_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        h_xg = self._num(ctx.get("home_expected"))
        a_xg = self._num(ctx.get("away_expected"))
        have_xg = h_xg is not None and a_xg is not None and h_xg > 0 and a_xg > 0

        if have_xg:
            log_h, log_a = math.log(h_xg), math.log(a_xg)
            w_exp = self.EXPOSURE_WEIGHT_WITH_XG
        else:
            log_h, log_a = math.log(self.BASE_HOME), math.log(self.BASE_AWAY)
            w_exp = 1.0

        # Replace the HFA embedded in the anchor ratio with the shrunk
        # posterior. Shift symmetrically so the total rate is unchanged.
        embedded = math.log(self.BASE_HOME / self.BASE_AWAY)
        hfa_shift = 0.5 * (self._posterior_hfa(ctx) - embedded)
        log_h += hfa_shift
        log_a -= hfa_shift

        # Attack/defense exposure.
        log_h += w_exp * self._exposure(ctx, "home", "away")
        log_a += w_exp * self._exposure(ctx, "away", "home")

        # Rest differential (zero-sum).
        r = self._rest_log_diff(ctx)
        log_h += 0.5 * r
        log_a -= 0.5 * r

        # Pace and weather scale total goals.
        common = self._pace_log(ctx) + self._weather_log(ctx)
        log_h += common
        log_a += common

        return math.exp(log_h), math.exp(log_a)

    # ================================================================== #
    # Game-state forward integration
    # ================================================================== #

    def _state_multipliers(self, G: int) -> Tuple[np.ndarray, np.ndarray]:
        i = np.arange(G + 1)[:, None]
        j = np.arange(G + 1)[None, :]
        s_home = (i - j).astype(float)  # home perspective
        s_away = -s_home
        m_h = np.exp(-self.beta_lead * np.maximum(s_home, 0) + self.beta_trail * np.maximum(-s_home, 0))
        m_a = np.exp(-self.beta_lead * np.maximum(s_away, 0) + self.beta_trail * np.maximum(-s_away, 0))
        return m_h, m_a

    def _integrate(self, rate_h: float, rate_a: float) -> np.ndarray:
        """Forward Kolmogorov integration on the score lattice over unit match time."""
        G, T = self.MAX_GOALS, self.TIME_STEPS
        dt = 1.0 / T
        m_h, m_a = self._state_multipliers(G)

        lam_h = rate_h * m_h
        lam_a = rate_a * m_a

        # Reflect truncation: no transitions out of the lattice edge (mass
        # stays put), which keeps total probability exactly 1.
        lam_h[G, :] = 0.0
        lam_a[:, G] = 0.0

        lam = lam_h + lam_a
        stay = np.exp(-lam * dt)
        with np.errstate(divide="ignore", invalid="ignore"):
            frac_h = np.where(lam > 0, lam_h / lam, 0.0)
        jump = 1.0 - stay
        p_h = frac_h * jump
        p_a = jump - p_h

        P = np.zeros((G + 1, G + 1))
        P[0, 0] = 1.0
        for _ in range(T):
            nxt = P * stay
            nxt[1:, :] += (P * p_h)[:-1, :]
            nxt[:, 1:] += (P * p_a)[:, :-1]
            P = nxt

        return P / P.sum()

    def _calibrated_grid(self, mu_h: float, mu_a: float) -> np.ndarray:
        """Solve the rate scales so integrated E[H] and E[A] equal the targets."""
        G = self.MAX_GOALS
        goals = np.arange(G + 1)
        c_h, c_a = mu_h, mu_a
        P = self._integrate(c_h, c_a)
        for _ in range(self.CALIBRATION_ITERS):
            e_h = float((P.sum(axis=1) * goals).sum())
            e_a = float((P.sum(axis=0) * goals).sum())
            if e_h <= 0 or e_a <= 0:
                break
            c_h *= mu_h / e_h
            c_a *= mu_a / e_a
            P = self._integrate(c_h, c_a)
            if abs(e_h - mu_h) < 1e-5 and abs(e_a - mu_a) < 1e-5:
                break
        return P

    # ================================================================== #
    # Dixon-Coles residual correction
    # ================================================================== #

    @staticmethod
    def _apply_tau(P: np.ndarray, rho: float, lh: float, la: float) -> np.ndarray:
        Q = P.copy()
        Q[0, 0] *= 1.0 - lh * la * rho
        Q[0, 1] *= 1.0 + lh * rho
        Q[1, 0] *= 1.0 + la * rho
        Q[1, 1] *= 1.0 - rho
        Q = np.clip(Q, 0.0, None)
        return Q / Q.sum()

    def _fit_rho(self, P: np.ndarray, lh: float, la: float, draw_target: float) -> float:
        # Valid region: every tau factor must stay non-negative.
        lo = max(-1.0 / max(lh, 1e-9), -1.0 / max(la, 1e-9))
        hi = min(1.0 / max(lh * la, 1e-9), 1.0)
        eps = 1e-9
        lo, hi = lo + eps, hi - eps

        def gap(r: float) -> float:
            return float(np.trace(self._apply_tau(P, r, lh, la))) - draw_target

        g_lo, g_hi = gap(lo), gap(hi)
        if g_lo * g_hi < 0:
            return float(optimize.brentq(gap, lo, hi, xtol=1e-10))

        # Target unreachable: take the closest attainable rho.
        res = optimize.minimize_scalar(lambda r: gap(r) ** 2, bounds=(lo, hi), method="bounded")
        return float(res.x)

    # ================================================================== #
    # Core API
    # ================================================================== #

    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        mu_h, mu_a = self._pre_match_means(ctx)
        P = self._calibrated_grid(mu_h, mu_a)

        draw_target = self._num(ctx.get("league_draw_rate"), self.draw_rate_prior)
        state_draw = float(np.trace(P))
        rho = self._fit_rho(P, mu_h, mu_a, draw_target)
        P = self._apply_tau(P, rho, mu_h, mu_a)

        self.last_diagnostics = {
            "mu_home": mu_h,
            "mu_away": mu_a,
            "hfa_posterior_log": self._posterior_hfa(ctx),
            "draw_from_state_process": state_draw,
            "draw_target": draw_target,
            "rho": rho,
            "beta_lead": self.beta_lead,
            "beta_trail": self.beta_trail,
        }
        return P

    def margin_pmf_from_matrix(self, P: np.ndarray) -> Dict[int, float]:
        G = P.shape[0] - 1
        pmf: Dict[int, float] = {}
        for k in range(-G, G + 1):
            pmf[k] = float(np.trace(P, offset=-k))  # home - away = k
        return pmf

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":  # type: ignore[name-defined]
        P = self.score_matrix(game_context)
        self.last_margin_pmf = self.margin_pmf_from_matrix(P)

        G = P.shape[0] - 1
        scores = {(i, j): float(P[i, j]) for i in range(G + 1) for j in range(G + 1) if P[i, j] > 0.0}

        try:
            return DiscreteDistribution.from_scores(scores)  # type: ignore[union-attr]
        except Exception:
            p_home = float(np.tril(P, -1).sum())
            p_draw = float(np.trace(P))
            p_away = float(np.triu(P, 1).sum())
            try:
                return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)  # type: ignore[union-attr]
            except Exception:
                return DiscreteDistribution(probabilities={"home": p_home, "draw": p_draw, "away": p_away})  # type: ignore[misc]

    # ================================================================== #
    # Asian handicap settlement
    # ================================================================== #

    @staticmethod
    def _split_line(line: float) -> List[float]:
        """Quarter lines split into two half-stakes on adjacent half/integer lines."""
        q = round(line * 4.0)
        if q % 2 != 0:  # quarter line, e.g. -0.25, -0.75
            return [(q - 1) / 4.0, (q + 1) / 4.0]
        return [q / 4.0]

    def settlement_probs(
        self, line: float, side: str = "home", pmf: Optional[Dict[int, float]] = None
    ) -> Dict[str, float]:
        """
        Return the probability of each outcome for a handicap `line` applied
        to `side`: full win, half win, push, half loss, full loss.
        """
        pmf = pmf if pmf is not None else self.last_margin_pmf
        sign = 1 if side == "home" else -1
        parts = self._split_line(line)
        out = {"win": 0.0, "half_win": 0.0, "push": 0.0, "half_loss": 0.0, "loss": 0.0}

        for k, p in pmf.items():
            if p <= 0.0:
                continue
            res = [sign * k + h for h in parts]
            wins = sum(1 for r in res if r > 1e-12)
            losses = sum(1 for r in res if r < -1e-12)

            if len(parts) == 1:
                key = "win" if wins else ("loss" if losses else "push")
            else:
                if wins == 2:
                    key = "win"
                elif losses == 2:
                    key = "loss"
                elif wins == 1 and losses == 0:
                    key = "half_win"
                elif losses == 1 and wins == 0:
                    key = "half_loss"
                else:
                    key = "push"
            out[key] += p

        return out

    def expected_value(
        self, line: float, odds: float, side: str = "home", pmf: Optional[Dict[int, float]] = None
    ) -> float:
        """Expected profit per unit stake at decimal `odds`."""
        s = self.settlement_probs(line, side, pmf)
        b = odds - 1.0
        return s["win"] * b + s["half_win"] * 0.5 * b - s["half_loss"] * 0.5 - s["loss"]

    def fair_odds(self, line: float, side: str = "home", pmf: Optional[Dict[int, float]] = None) -> float:
        """Decimal odds at which EV = 0, solved in closed form from the settlement mass."""
        s = self.settlement_probs(line, side, pmf)
        w = s["win"] + 0.5 * s["half_win"]
        lose = s["loss"] + 0.5 * s["half_loss"]
        return float("inf") if w <= 0 else 1.0 + lose / w

    def evaluate_spread(
        self, game_context: Dict[str, Any], line: float, odds: float, side: str = "home"
    ) -> Dict[str, Any]:
        self.predict_distribution(game_context)
        ev = self.expected_value(line, odds, side)
        return {
            "side": side,
            "line": line,
            "odds": odds,
            "fair_odds": self.fair_odds(line, side),
            "ev_per_unit": ev,
            "settlement": self.settlement_probs(line, side),
            "eligible": bool(odds >= self.MIN_ELIGIBLE_ODDS and ev > 0.0),
            "diagnostics": dict(self.last_diagnostics),
        }

    # ================================================================== #
    # Maximum-likelihood refit once data exists
    # ================================================================== #

    def fit(self, matches: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        """
        Joint MLE of (beta_lead, beta_trail) from final scores.

        Each match dict needs the usual context features plus 'home_goals' and
        'away_goals'. rho is re-solved per match from the draw target, so the
        betas capture the margin-shape information beyond the draw rate.

        A weak Gaussian prior centred on the domain priors regularises small
        samples. Its scale is the prior's own magnitude, so the prior's
        influence vanishes as n grows.
        """
        data = [m for m in matches if "home_goals" in m and "away_goals" in m]
        if not data:
            return {"beta_lead": self.beta_lead, "beta_trail": self.beta_trail, "n": 0}

        prior = np.array([self.BETA_LEAD, self.BETA_TRAIL])
        prior_sd = np.abs(prior) + 1e-6
        G = self.MAX_GOALS

        def nll(theta: np.ndarray) -> float:
            self.beta_lead, self.beta_trail = float(theta[0]), float(theta[1])
            total = 0.0
            for m in data:
                P = self.score_matrix(m)
                hg = min(int(m["home_goals"]), G)
                ag = min(int(m["away_goals"]), G)
                total -= math.log(max(P[hg, ag], 1e-300))
            total += 0.5 * float((((theta - prior) / prior_sd) ** 2).sum())
            return total

        res = optimize.minimize(nll, prior.copy(), method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-4})
        self.beta_lead, self.beta_trail = float(res.x[0]), float(res.x[1])
        return {"beta_lead": self.beta_lead, "beta_trail": self.beta_trail, "n": len(data), "nll": float(res.fun)}


# Alias for loaders that look up a conventional name.
CandidateModel = LaLigaFullGameSpreadsModel