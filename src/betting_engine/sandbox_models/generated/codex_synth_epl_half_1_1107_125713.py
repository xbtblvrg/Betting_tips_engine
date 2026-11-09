"""
Candidate model for EPL | half_1 | exact_total.

Cold-start caveat: there are 0 settled wagers in this segment. Every structural
parameter starts at a prior that reduces the model exactly to independent
Poisson (lambda_h = 0.80, lambda_a = 0.60) plus stoppage-time mixing. The
non-Poisson structure is not asserted. The fit() method can learn it from
settled first-half scores by penalised maximum likelihood, using a ranked
probability / log-loss objective on the total. No ROI or Brier-skill claim is
made until it has been validated out of sample.

Structure (derived, not templated):
  1. Each team scores as a counting process with intensity
         lambda_team(t) = lambda_team * f_team * g(t) * exp(theta_state(D(t)))
     where D(t) is that team's current goal difference, f_team is gamma
     frailty (uncertainty in true strength), and g(t) is the in-half time
     shape.
  2. The state feedback depends only on the scoreline, not on clock time.
     The process is therefore a time-changed homogeneous CTMC, so
         P(score at HT) = e_0^T expm(Q * G(T)),
     where G(T) = integral of g from 0 to T. The time shape enters only
     through the cumulative intensity G(45 + S), with stoppage S random.
  3. Dispersion is emergent rather than clamped:
       - frailty variance phi and the variance of S widen the total;
       - negative lead/trail feedback narrows it.
  4. A Dixon-Coles tau correction is applied to the low-score cells. Its
     rho is restricted to the range where tau stays nonnegative, which is a
     derived bound and not an arbitrary clamp.
"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.linalg import expm
from scipy.optimize import minimize
from scipy.special import gammaln, roots_genlaguerre

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover - resolved by the sandbox scope
    BaseCandidateModel = globals().get("BaseCandidateModel", object)

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.distributions import DiscreteDistribution  # type: ignore
    except ImportError:
        DiscreteDistribution = globals().get("DiscreteDistribution")


class EPLHalf1ExactTotalModel(BaseCandidateModel):
    """
    First-half EPL exact-total model.

    Built on a time-changed, state-feedback CTMC with gamma frailty and a
    random stoppage time.
    """

    # Parameter vector layout used by fit(). Every entry is 0 at the prior,
    # and the all-zero vector is the Poisson cold start.
    PARAM_NAMES = (
        "theta_lead",   # log-multiplier on the leading team's intensity
        "theta_trail",  # log-multiplier on the trailing team's intensity
        "u_phi",        # frailty variance phi = u_phi ** 2
        "rho_raw",      # Dixon-Coles dependence, mapped into its feasible range
        "log_omega",    # log multiplier on stoppage-minute intensity relative to the ramp end
        "beta_ramp",    # in-half linear intensity ramp slope (g(t) = 1 + beta*(t/45 - 1/2))
        "b_rest",       # coefficient on log rest-days ratio
        "b_tempo",      # coefficient on log tempo/pace index (both teams)
        "b_wind",       # coefficient on wind speed (both teams, outdoor only)
    )

    def __init__(
        self,
        model_id: str = "codex_synth_epl_half_1_1107_125713",
        version: str = "1.0.0",
        league: str = "EPL",
        scope: str = "half_1",
        family: str = "exact_total",
        baseline_home: float = 0.80,
        baseline_away: float = 0.60,
        stoppage_pmf: Optional[Dict[int, float]] = None,
        prior_sd: Optional[Dict[str, float]] = None,
        max_goals_per_team: int = 7,
        frailty_nodes: int = 5,
        regulation_minutes: float = 45.0,
    ):
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
            )
        except TypeError:
            super().__init__()

        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        # Empirical anchors supplied for this segment.
        self.baseline_home = float(baseline_home)
        self.baseline_away = float(baseline_away)
        self.regulation_minutes = float(regulation_minutes)

        # First-half added-time prior, in whole minutes. This is a domain
        # prior and should be replaced by the empirical histogram as soon as
        # it is available. It is passed in as data, not used as a clamp.
        if stoppage_pmf is None:
            stoppage_pmf = {
                1: 0.04, 2: 0.10, 3: 0.16, 4: 0.19, 5: 0.17,
                6: 0.13, 7: 0.09, 8: 0.06, 9: 0.04, 10: 0.02,
            }
        s_vals = np.array(sorted(stoppage_pmf.keys()), dtype=float)
        s_p = np.array([stoppage_pmf[int(k)] for k in s_vals], dtype=float)
        self.stoppage_minutes = s_vals
        self.stoppage_probs = s_p / s_p.sum()

        # Gaussian shrinkage scales for penalised MLE. With little data the
        # fit stays near the Poisson prior; with more data it moves away.
        default_sd = {
            "theta_lead": 0.25, "theta_trail": 0.25, "u_phi": 0.35,
            "rho_raw": 0.5, "log_omega": 0.3, "beta_ramp": 0.5,
            "b_rest": 0.1, "b_tempo": 0.5, "b_wind": 0.02,
        }
        if prior_sd:
            default_sd.update(prior_sd)
        self.prior_sd = np.array([default_sd[n] for n in self.PARAM_NAMES], dtype=float)

        self.N = int(max_goals_per_team)
        self.n_states = (self.N + 1) ** 2
        self.frailty_nodes = int(frailty_nodes)

        self.params = np.zeros(len(self.PARAM_NAMES), dtype=float)
        self.n_fitted_games = 0

        # Precompute the state index and goal difference for each state.
        self._idx = np.arange(self.n_states)
        self._hg = self._idx // (self.N + 1)
        self._ag = self._idx % (self.N + 1)
        self._diff = self._hg - self._ag

    # ------------------------------------------------------------------ #
    # Parameter helpers
    # ------------------------------------------------------------------ #
    def _p(self, params: np.ndarray) -> Dict[str, float]:
        return {n: float(v) for n, v in zip(self.PARAM_NAMES, params)}

    @staticmethod
    def _rho_bounds(lh: float, la: float) -> Tuple[float, float]:
        """
        Feasible rho range: the range in which every tau term is >= 0.

        The tau terms are:
          tau00 = 1 - lh*la*rho
          tau01 = 1 + lh*rho
          tau10 = 1 + la*rho
          tau11 = 1 - rho
        """
        lo = max(-1.0 / max(lh, 1e-12), -1.0 / max(la, 1e-12))
        hi = min(1.0 / max(lh * la, 1e-12), 1.0)
        return lo, hi

    def _rho(self, rho_raw: float, lh: float, la: float) -> float:
        lo, hi = self._rho_bounds(lh, la)
        # Smooth map from the real line into (lo, hi), with 0 mapping to 0.
        # tanh is odd, so asymmetric bounds are scaled separately on each side.
        t = math.tanh(rho_raw)
        return t * (hi if t >= 0 else -lo)

    # ------------------------------------------------------------------ #
    # Situational intensity
    # ------------------------------------------------------------------ #
    @staticmethod
    def _num(ctx: Dict[str, Any], *keys, default=None):
        for k in keys:
            v = ctx.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return default

    def _team_strength(self, ctx: Dict[str, Any], side: str) -> Tuple[Optional[float], Optional[float]]:
        blob = ctx.get(side)
        att = dfn = None
        if isinstance(blob, dict):
            att = self._num(blob, "attack", "attack_strength", "att")
            dfn = self._num(blob, "defense", "defence", "defense_strength", "def")
        att = att if att is not None else self._num(ctx, f"{side}_attack")
        dfn = dfn if dfn is not None else self._num(ctx, f"{side}_defense", f"{side}_defence")
        return att, dfn

    def _base_rates(self, ctx: Dict[str, Any], params: np.ndarray) -> Tuple[float, float]:
        p = self._p(params)
        lh = self._num(ctx, "home_expected")
        la = self._num(ctx, "away_expected")

        # If the expected goals are absent, build them from the empirical
        # anchors scaled by multiplicative attack x opponent-defence exposure.
        if lh is None or la is None:
            h_att, h_def = self._team_strength(ctx, "home")
            a_att, a_def = self._team_strength(ctx, "away")
            if lh is None:
                lh = self.baseline_home * (h_att or 1.0) * (a_def or 1.0)
            if la is None:
                la = self.baseline_away * (a_att or 1.0) * (h_def or 1.0)

        # Rest asymmetry, measured on the log scale (diminishing returns).
        rh = self._num(ctx, "home_rest_days")
        ra = self._num(ctx, "away_rest_days")
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            r = math.log((rh + 1.0) / (ra + 1.0))
            lh *= math.exp(p["b_rest"] * r)
            la *= math.exp(-p["b_rest"] * r)

        # Tempo / pace index, where 1 means league average.
        tempo = self._num(ctx, "tempo", "pace", "tempo_index", "pace_index")
        if tempo is not None and tempo > 0:
            m = math.exp(p["b_tempo"] * math.log(tempo))
            lh *= m
            la *= m

        # Wind effect, applied only outdoors.
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else ctx
        is_dome = bool(weather.get("is_dome", False)) if isinstance(weather, dict) else False
        wind = self._num(weather, "wind_speed") if isinstance(weather, dict) else None
        if wind is not None and not is_dome:
            m = math.exp(p["b_wind"] * wind)
            lh *= m
            la *= m

        return max(lh, 1e-9), max(la, 1e-9)

    # ------------------------------------------------------------------ #
    # Time shape -> cumulative intensity per stoppage outcome
    # ------------------------------------------------------------------ #
    def _cumulative_intensity(self, params: np.ndarray) -> np.ndarray:
        """
        Compute normalised G(45 + S) for each stoppage value S.

        G is scaled so that E_S[G] = 1, which keeps the mean anchored to
        home_expected + away_expected when there is no state feedback.

        The ramp g(t) = 1 + beta*(t/T - 1/2) integrates to T over [0, T]
        for any beta. Stoppage minutes run at omega * g(T) = omega * (1 + beta/2).
        """
        p = self._p(params)
        T = self.regulation_minutes
        g_end = max(1.0 + 0.5 * p["beta_ramp"], 1e-6)  # intensity is nonnegative by construction
        omega = math.exp(p["log_omega"]) * g_end
        g_raw = T + omega * self.stoppage_minutes
        return g_raw / float(np.dot(self.stoppage_probs, g_raw))

    # ------------------------------------------------------------------ #
    # CTMC generator with state feedback
    # ------------------------------------------------------------------ #
    def _generator(self, lh: float, la: float, p: Dict[str, float]) -> np.ndarray:
        N1 = self.N + 1
        d = self._diff
        mult_h = np.where(d > 0, math.exp(p["theta_lead"]),
                          np.where(d < 0, math.exp(p["theta_trail"]), 1.0))
        mult_a = np.where(d < 0, math.exp(p["theta_lead"]),
                          np.where(d > 0, math.exp(p["theta_trail"]), 1.0))
        rate_h = lh * mult_h * (self._hg < self.N)
        rate_a = la * mult_a * (self._ag < self.N)

        Q = np.zeros((self.n_states, self.n_states))
        idx = self._idx
        hm = self._hg < self.N
        am = self._ag < self.N
        Q[idx[hm], idx[hm] + N1] = rate_h[hm]
        Q[idx[am], idx[am] + 1] = rate_a[am]
        Q[idx, idx] = -(rate_h + rate_a)
        return Q

    def _frailty_quadrature(self, phi: float) -> Tuple[np.ndarray, np.ndarray]:
        """Gamma(mean 1, variance phi) quadrature. Returns a single node at phi = 0."""
        if phi < 1e-8:
            return np.array([1.0]), np.array([1.0])
        k = 1.0 / phi
        x, w = roots_genlaguerre(self.frailty_nodes, k - 1.0)
        w = w / math.exp(gammaln(k))
        return x * phi, w / w.sum()

    # ------------------------------------------------------------------ #
    # Core score matrix
    # ------------------------------------------------------------------ #
    def score_matrix(self, ctx: Dict[str, Any], params: Optional[np.ndarray] = None) -> np.ndarray:
        params = self.params if params is None else params
        p = self._p(params)
        lh, la = self._base_rates(ctx, params)
        G = self._cumulative_intensity(params)

        phi = p["u_phi"] ** 2
        fx, fw = self._frailty_quadrature(phi)

        P = np.zeros(self.n_states)
        for fh, wh in zip(fx, fw):
            for fa, wa in zip(fx, fw):
                Q = self._generator(lh * fh, la * fa, p)
                for g, ps in zip(G, self.stoppage_probs):
                    P += wh * wa * ps * expm(Q * g)[0]

        M = np.clip(P.reshape(self.N + 1, self.N + 1), 0.0, None)

        # Dixon-Coles low-score dependence.
        rho = self._rho(p["rho_raw"], lh, la)
        if rho != 0.0:
            M[0, 0] *= 1.0 - lh * la * rho
            M[0, 1] *= 1.0 + lh * rho
            M[1, 0] *= 1.0 + la * rho
            M[1, 1] *= 1.0 - rho

        s = M.sum()
        return M / s if s > 0 else M

    def total_pmf(self, ctx: Dict[str, Any], params: Optional[np.ndarray] = None) -> np.ndarray:
        M = self.score_matrix(ctx, params)
        n = 2 * self.N + 1
        out = np.zeros(n)
        for i in range(self.N + 1):
            for j in range(self.N + 1):
                out[i + j] += M[i, j]
        return out

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        M = self.score_matrix(game_context)
        scores = {
            (i, j): float(M[i, j])
            for i in range(self.N + 1)
            for j in range(self.N + 1)
            if M[i, j] > 0.0
        }
        try:
            return DiscreteDistribution.from_scores(scores)
        except (TypeError, AttributeError, ValueError):
            return DiscreteDistribution.from_scores(M)

    def expected_value(self, prob: float, decimal_odds: float) -> float:
        """Continuous EV per unit stake. Eligibility is odds >= 1.40 and EV > 0."""
        return prob * decimal_odds - 1.0

    def is_eligible(self, prob: float, decimal_odds: float) -> bool:
        return decimal_odds >= 1.40 and self.expected_value(prob, decimal_odds) > 0.0

    # ------------------------------------------------------------------ #
    # Calibration: penalised (hierarchical-prior) MLE on settled halves
    # ------------------------------------------------------------------ #
    def _neg_log_post(self, params: np.ndarray, games: Sequence[Tuple[Dict[str, Any], int, int]],
                      use_joint: bool) -> float:
        nll = 0.0
        for ctx, hg, ag in games:
            if use_joint:
                M = self.score_matrix(ctx, params)
                pr = M[min(hg, self.N), min(ag, self.N)]
            else:
                pmf = self.total_pmf(ctx, params)
                pr = pmf[min(hg + ag, len(pmf) - 1)]
            nll -= math.log(max(pr, 1e-300))
        penalty = 0.5 * float(np.sum((params / self.prior_sd) ** 2))
        return nll + penalty

    def fit(self, games: List[Dict[str, Any]], use_joint: bool = True, maxiter: int = 400) -> Dict[str, float]:
        """
        Fit the structural parameters by penalised maximum likelihood.

        games: a list of dicts {'context': {...}, 'home_goals_h1': int, 'away_goals_h1': int}.
        The Gaussian prior (prior_sd) is the hierarchical shrinkage term. With
        an empty sample the parameters stay at the Poisson prior.
        """
        data = []
        for g in games:
            ctx = g.get("context", g)
            hg = g.get("home_goals_h1", g.get("home_goals"))
            ag = g.get("away_goals_h1", g.get("away_goals"))
            if hg is None or ag is None:
                continue
            data.append((ctx, int(hg), int(ag)))
        if not data:
            return self._p(self.params)

        res = minimize(
            self._neg_log_post, self.params.copy(), args=(data, use_joint),
            method="Powell", options={"maxiter": maxiter, "xtol": 1e-3, "ftol": 1e-4},
        )
        if res.success or res.fun < self._neg_log_post(self.params, data, use_joint):
            self.params = np.asarray(res.x, dtype=float)
        self.n_fitted_games = len(data)
        return self._p(self.params)

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #
    def dispersion_index(self, ctx: Dict[str, Any]) -> float:
        pmf = self.total_pmf(ctx)
        k = np.arange(len(pmf))
        mu = float(np.dot(k, pmf))
        var = float(np.dot((k - mu) ** 2, pmf))
        return var / mu if mu > 0 else float("nan")

    @staticmethod
    def log_loss_buckets(pmf: np.ndarray, observed_total: int) -> float:
        """Log-loss over the settlement buckets {0, 1, 2, 3+}."""
        buckets = np.array([pmf[0], pmf[1], pmf[2], pmf[3:].sum()])
        b = min(observed_total, 3)
        return -math.log(max(buckets[b], 1e-300))