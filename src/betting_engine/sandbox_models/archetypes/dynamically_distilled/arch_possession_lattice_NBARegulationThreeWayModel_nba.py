"""Regulation-time three-way model for NBA|full_game|three_way.

STATUS: this code has not been run or tested against betting_engine. All priors
are unvalidated values from memory, so verify them on data. Keep this market at
zero stake until fit() sets stake_eligible=True. The engine must read that flag
for the gate to take effect, because a DiscreteDistribution can't carry it.

CHANGES FROM THE SUPPLIED HYPOTHESIS
- A plain NegBin(N) can't work. It forces Var(N) > E[N] (about 100), but
  SD(total)=18 and SD(margin)=12 imply Var(N) of about 37. N is therefore
  L + NegBin, where the integer floor L represents the regulation clock.
- SD 12/18 implies a score correlation of 0.385, not 0.35:
  Cov=(324-144)/4=45 and Var(H)=(324+144)/4=117.
- Box-score possession variance (about 1.3) would give SD(margin) of about 16.
  The model instead solves for an effective variance (about 0.72) that absorbs
  in-game negative feedback. Possession pmfs are effective, not literal shot
  outcomes. They are smooth by design, so kappa alone carries the tie excess.

LIMITS
- "Zero hardcoded constants" can't be met with zero settled bets. Every number
  is a named prior in PRIOR, and fit() re-estimates the main ones.
- The quoted +122% / +90% ROI benchmarks are not treated as targets. Returns of
  that size are more likely to reflect leakage or small samples than real skill.
- If home_expected already prices rest, the rest shift double-counts it. Fitting
  rest_margin should reveal this by moving it toward zero.
- Overtime inflation of full-game means is ignored. It is nearly symmetric, so
  it barely moves mu and changes sigma by under 1%.
"""
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:
    # DiscreteDistribution is documented as pre-loaded in the execution scope.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _side_feature(ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
    value = _finite(ctx.get(f"{side}_{name}"))
    if value is None and isinstance(ctx.get(side), dict):
        value = _finite(ctx[side].get(name))
    return value


def _possession_pmf(mean: float, variance: float) -> np.ndarray:
    """Maximum-entropy pmf on {0,1,2,3} with the given mean and variance."""
    points = np.arange(4.0)
    features = np.column_stack([points, points ** 2])
    target = np.array([mean, variance + mean ** 2])

    def dual(theta: np.ndarray) -> float:
        return float(logsumexp(features @ theta) - theta @ target)

    theta = np.zeros(2)
    for _ in range(60):
        log_weights = features @ theta
        pmf = np.exp(log_weights - logsumexp(log_weights))
        moments = features.T @ pmf
        gradient = moments - target
        if np.max(np.abs(gradient)) < 1e-10:
            break
        hessian = (features * pmf[:, None]).T @ features - np.outer(moments, moments)
        step = np.linalg.solve(hessian + 1e-12 * np.eye(2), gradient)
        size, current = 1.0, dual(theta)
        while size > 1e-6 and dual(theta - size * step) > current - 1e-4 * size * (gradient @ step):
            size *= 0.5
        theta = theta - size * step
    log_weights = features @ theta
    return np.exp(log_weights - logsumexp(log_weights))


class NBARegulationThreeWayModel(BaseCandidateModel):
    """Prices the regulation result with a pace-linked possession margin lattice.

    N = L + NegBin(m, r), and H, A | N are sums of N effective possession scores.
    The margin pmf comes from inverting the exact characteristic function:
        phi_M(t) = G_N(phi_H(t) * phi_A(-t))
    The tie is then repriced by exp(kappa0 + kappa1 * (mu / sigma)^2).
    """

    PRIOR: Dict[str, float] = {
        "ref_home": 112.0,        # supplied baseline
        "ref_away": 108.0,        # supplied baseline
        "ref_possessions": 100.0,  # approximate modern league pace
        "sd_margin": 12.0,        # closing-line residual SD, from memory
        "sd_total": 18.0,         # closing-line residual SD, from memory
        "ot_rate": 0.06,          # historical overtime frequency, from memory
        "sd_pregame_mu": 6.0,     # spread of pregame margins, from memory
        "compression": 0.03,      # blowout variance shrink, weak prior
        "kappa1": 0.0,            # no prior view on tie excess vs mismatch
        "rest_margin": 2.3,       # about 2 points for a back-to-back vs rested
        "rest_pace": -0.005,      # slight slowdown per unit of fatigue
        "rest_tau": 1.0,          # days for fatigue to decay
        "skew_fraction": 0.5,     # share of Var(N) assigned to the NegBin mean
    }
    GRID_SIZE = 1024  # far wider than any plausible margin, so aliasing is negligible

    def __init__(
        self,
        model_id: str = "codex_synth_nba_full_game_1106_200045",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "three_way",
    ) -> None:
        super().__init__(
            model_id=model_id, version=version, league=league, scope=scope, family=family
        )
        self.params = self._prior_params()
        self.stake_eligible = False
        self.validation_report: Optional[Dict[str, float]] = None

    @classmethod
    def _prior_params(cls) -> Dict[str, float]:
        params = dict(cls.PRIOR)
        # A smooth lattice ties at about E[1/(sigma*sqrt(2pi))*exp(-mu^2/2sigma^2)]
        # = 1/sqrt(2pi(sigma^2+tau^2)). kappa0 lifts that to the overtime rate.
        spread = params["sd_margin"] ** 2 + params["sd_pregame_mu"] ** 2
        params["kappa0"] = math.log(params["ot_rate"] * math.sqrt(2.0 * math.pi * spread))
        return params

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        p_home, p_draw, p_away = self._three_way_from_context(game_context, self.params)
        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )

    # ------------------------------------------------------------------ inputs
    @staticmethod
    def _expected(ctx: Dict[str, Any], params: Dict[str, float]) -> Tuple[float, float]:
        home = _finite(ctx.get("home_expected"))
        away = _finite(ctx.get("away_expected"))
        if home is None or home <= 0.0:
            # Assumes multiplicative ratings centred on 1. Used only as a fallback,
            # since a supplied expectation should already embed team strength.
            home = (
                params["ref_home"]
                * (_side_feature(ctx, "home", "attack") or 1.0)
                * (_side_feature(ctx, "away", "defense") or 1.0)
            )
        if away is None or away <= 0.0:
            away = (
                params["ref_away"]
                * (_side_feature(ctx, "away", "attack") or 1.0)
                * (_side_feature(ctx, "home", "defense") or 1.0)
            )
        return home, away

    @classmethod
    def _inputs(
        cls, ctx: Dict[str, Any], params: Dict[str, float]
    ) -> Tuple[float, float, float]:
        home, away = cls._expected(ctx, params)
        ref_points = (params["ref_home"] + params["ref_away"]) / (2.0 * params["ref_possessions"])

        paces = [_side_feature(ctx, "home", "pace"), _side_feature(ctx, "away", "pace")]
        paces = [p for p in paces if p is not None and p > 0.0]
        shared = _finite(ctx.get("pace"))
        if shared is None:
            shared = _finite(ctx.get("tempo"))
        if paces:
            possessions = float(np.mean(paces))
        elif shared is not None and shared > 0.0:
            possessions = shared
        else:
            possessions = (home + away) / (2.0 * ref_points)

        home_rest = _finite(ctx.get("home_rest_days"))
        away_rest = _finite(ctx.get("away_rest_days"))
        if home_rest is not None and away_rest is not None:
            home_fatigue = math.exp(-max(home_rest, 0.0) / params["rest_tau"])
            away_fatigue = math.exp(-max(away_rest, 0.0) / params["rest_tau"])
            shift = params["rest_margin"] * (away_fatigue - home_fatigue)
            home, away = home + 0.5 * shift, away - 0.5 * shift
            possessions *= 1.0 + params["rest_pace"] * (home_fatigue + away_fatigue)

        # A possession scores at most 3, so a smaller count is a unit error in pace.
        if 3.0 * possessions <= max(home, away):
            possessions = (home + away) / (2.0 * ref_points)
        return home, away, possessions

    # --------------------------------------------------------------- structure
    @classmethod
    def _margin_pmf(
        cls, home: float, away: float, possessions: float, params: Dict[str, float]
    ) -> np.ndarray:
        ref_n = params["ref_possessions"]
        ref_home = params["ref_home"] / ref_n
        ref_away = params["ref_away"] / ref_n
        var_margin = params["sd_margin"] ** 2
        var_total = params["sd_total"] ** 2

        # Identified from Var(T)-Var(M) = 4*Var(N)*e_h*e_a.
        ref_var_n = max((var_total - var_margin) / (4.0 * ref_home * ref_away), 0.0)
        ref_variance = max(
            (var_margin - ref_var_n * (ref_home - ref_away) ** 2) / (2.0 * ref_n), 1e-6
        )

        z_score = (home - away) / params["sd_margin"]
        variance = ref_variance / (1.0 + params["compression"] * z_score ** 2)
        var_n = ref_var_n * (possessions / ref_n) ** 2

        floor_n = max(int(math.floor(possessions - params["skew_fraction"] * var_n)), 0)
        mean_x = max(possessions - floor_n, 1e-6)
        size = mean_x ** 2 / max(var_n - mean_x, 1e-6)

        angles = 2.0 * np.pi * np.arange(cls.GRID_SIZE) / cls.GRID_SIZE
        phases = np.exp(1j * np.outer(angles, np.arange(4.0)))
        phi_home = phases @ cls._team_pmf(home / possessions, variance)
        phi_away = phases @ cls._team_pmf(away / possessions, variance)
        z = phi_home * np.conj(phi_away)
        phi_margin = z ** floor_n * (1.0 + (mean_x / size) * (1.0 - z)) ** (-size)

        # Index m holds P(M=m), and negative margins wrap to GRID_SIZE + m.
        pmf = np.clip(np.fft.fft(phi_margin).real / cls.GRID_SIZE, 0.0, None)
        return pmf / pmf.sum()

    @staticmethod
    def _team_pmf(mean: float, variance: float) -> np.ndarray:
        # Project onto the variance range that {0,1,2,3} can achieve for this mean.
        fraction = mean - math.floor(mean)
        low = fraction * (1.0 - fraction)
        high = mean * (3.0 - mean)
        margin = 1e-3 * (high - low)
        return _possession_pmf(mean, float(np.clip(variance, low + margin, high - margin)))

    @classmethod
    def _three_way(
        cls, pmf: np.ndarray, mu: float, params: Dict[str, float]
    ) -> Tuple[float, float, float]:
        half = cls.GRID_SIZE // 2
        up, down = float(pmf[1:half].sum()), float(pmf[half:].sum())
        z_score = mu / params["sd_margin"]
        multiplier = math.exp(params["kappa0"] + params["kappa1"] * z_score ** 2)
        p_draw = min(float(pmf[0]) * multiplier, 1.0 - 1e-12)
        remaining = 1.0 - p_draw
        return remaining * up / (up + down), p_draw, remaining * down / (up + down)

    @classmethod
    def _three_way_from_context(
        cls, ctx: Dict[str, Any], params: Dict[str, float]
    ) -> Tuple[float, float, float]:
        home, away, possessions = cls._inputs(ctx, params)
        return cls._three_way(cls._margin_pmf(home, away, possessions, params), home - away, params)

    # --------------------------------------------------------------- estimation
    def fit(self, games: Sequence[Dict[str, Any]], n_folds: int = 4) -> Dict[str, float]:
        """Walk-forward validation, then a refit of the live parameters on all games.

        games must be in chronological order. Each record holds the context keys
        plus home_regulation_score and away_regulation_score, which must be
        regulation-end scores. Optional closing_home_odds, closing_draw_odds and
        closing_away_odds (decimal) are needed to pass the stake gate.
        """
        rows = [
            g
            for g in games
            if _finite(g.get("home_regulation_score")) is not None
            and _finite(g.get("away_regulation_score")) is not None
        ]
        if not any(g["home_regulation_score"] == g["away_regulation_score"] for g in rows):
            raise ValueError("No regulation ties in sample; these look like final scores.")

        records: List[Tuple[Tuple[float, float, float], int, Optional[List[float]]]] = []
        for fold in range(1, n_folds + 1):
            start = len(rows) * fold // (n_folds + 1)
            end = len(rows) * (fold + 1) // (n_folds + 1)
            params = self._fit_params(rows[:start])
            for game in rows[start:end]:
                records.append(
                    (
                        self._three_way_from_context(game, params),
                        self._outcome(game),
                        self._closing_probabilities(game),
                    )
                )

        self.params = self._fit_params(rows)
        self.validation_report = self._validate(records)
        self.stake_eligible = bool(self.validation_report["passed"])
        return self.validation_report

    def _fit_params(self, rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
        start = self._prior_params()
        residuals = []
        for game in rows:
            home, away = self._expected(game, start)
            residuals.append(
                float(game["home_regulation_score"])
                + float(game["away_regulation_score"])
                - home
                - away
            )
        start["sd_total"] = float(np.std(residuals))

        def unpack(x: np.ndarray) -> Dict[str, float]:
            params = dict(start)
            params.update(
                sd_margin=math.exp(x[0]),
                compression=math.exp(x[1]),
                kappa0=float(x[2]),
                kappa1=float(x[3]),
                rest_margin=float(x[4]),
            )
            return params

        def objective(x: np.ndarray) -> float:
            params = unpack(x)
            return float(np.mean([-self._margin_log_lik(g, params) for g in rows]))

        x0 = np.array(
            [
                math.log(start["sd_margin"]),
                math.log(start["compression"]),
                start["kappa0"],
                start["kappa1"],
                start["rest_margin"],
            ]
        )
        simplex = np.vstack([x0, x0 + 0.1 * np.eye(len(x0))])
        result = minimize(
            objective,
            x0,
            method="Nelder-Mead",
            options={"initial_simplex": simplex, "maxiter": 300, "xatol": 1e-3, "fatol": 1e-5},
        )
        return unpack(result.x)

    @classmethod
    def _margin_log_lik(cls, game: Dict[str, Any], params: Dict[str, float]) -> float:
        home, away, possessions = cls._inputs(game, params)
        pmf = cls._margin_pmf(home, away, possessions, params)
        probs = cls._three_way(pmf, home - away, params)
        margin = int(round(float(game["home_regulation_score"]) - float(game["away_regulation_score"])))
        if margin == 0:
            return math.log(max(probs[1], 1e-15))
        half = cls.GRID_SIZE // 2
        side_prob, side_mass = (
            (probs[0], pmf[1:half].sum()) if margin > 0 else (probs[2], pmf[half:].sum())
        )
        return math.log(max(side_prob * pmf[margin % cls.GRID_SIZE] / side_mass, 1e-15))

    @staticmethod
    def _outcome(game: Dict[str, Any]) -> int:
        margin = float(game["home_regulation_score"]) - float(game["away_regulation_score"])
        return 0 if margin > 0 else (2 if margin < 0 else 1)

    @staticmethod
    def _closing_probabilities(game: Dict[str, Any]) -> Optional[List[float]]:
        odds = [
            _finite(game.get(key))
            for key in ("closing_home_odds", "closing_draw_odds", "closing_away_odds")
        ]
        if any(o is None or o <= 1.0 for o in odds):
            return None
        inverse = [1.0 / o for o in odds]
        return [value / sum(inverse) for value in inverse]

    @staticmethod
    def _validate(
        records: Sequence[Tuple[Tuple[float, float, float], int, Optional[List[float]]]]
    ) -> Dict[str, float]:
        """The stake gate: P(D) is calibrated and log-loss beats the de-vigged close."""
        draw_p = np.array([r[0][1] for r in records])
        draw_y = np.array([float(r[1] == 1) for r in records])
        model_loss = np.array([-math.log(max(r[0][r[1]], 1e-15)) for r in records])
        calibration_z = float(
            (draw_p.sum() - draw_y.sum()) / math.sqrt(float((draw_p * (1.0 - draw_p)).sum()))
        )
        report: Dict[str, float] = {
            "n_oos": float(len(records)),
            "model_log_loss": float(model_loss.mean()),
            "mean_predicted_draw": float(draw_p.mean()),
            "realized_draw_rate": float(draw_y.mean()),
            "draw_calibration_z": calibration_z,
            "edge_t_stat": float("nan"),
            "closing_log_loss": float("nan"),
            "passed": 0.0,
        }
        if records and all(r[2] is not None for r in records):
            closing_loss = np.array([-math.log(max(r[2][r[1]], 1e-15)) for r in records])
            edge = closing_loss - model_loss
            spread = float(edge.std(ddof=1))
            t_stat = float(edge.mean()) / (spread / math.sqrt(len(edge))) if spread > 0.0 else 0.0
            report["closing_log_loss"] = float(closing_loss.mean())
            report["edge_t_stat"] = t_stat
            report["passed"] = float(abs(calibration_z) < 1.96 and t_stat > 1.645)
        return report