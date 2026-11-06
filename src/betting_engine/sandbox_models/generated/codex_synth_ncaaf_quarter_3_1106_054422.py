import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

from betting_engine.sandbox_models.base import BaseCandidateModel

try:
    from betting_engine.sandbox_models.base import DiscreteDistribution
except ImportError:
    pass  # The sandbox pre-loads DiscreteDistribution into execution scope.


SCORE_VALUES = np.array([2.0, 3.0, 6.0, 7.0, 8.0])

DEFAULT_PARAMS: Dict[str, Any] = {
    # Segment anchors supplied in the brief.
    "baseline_home": 7.00,
    "baseline_away": 6.25,
    # Event shares aligned to SCORE_VALUES: safety, FG, TD with no conversion, TD+PAT, TD+2pt.
    # These imply a point-scale Var/Mean of about 6.5 and a margin SD of about 9.5.
    "scoring_weights": (0.01, 0.27, 0.03, 0.64, 0.05),
    # Variance of the mean-one Gamma tempo factor shared by both teams.
    "tempo_variance": 0.04,
    # Ratings are mostly priced into the expected points when those are supplied.
    "rating_beta_with_expected": 0.10,
    "rating_beta_without_expected": 0.50,
    "rest_beta": 0.02,
    "pace_beta": 0.25,
    # Quadratic wind effects on scoring rate and field-goal share.
    "wind_rate_beta": 0.00012,
    "wind_fg_beta": 0.0006,
    # Lattice half-width in margin SDs, chosen so FFT aliasing is negligible.
    "lattice_sd_multiple": 24.0,
}


def _num(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _positive(value: Any) -> Optional[float]:
    result = _num(value)
    return result if result is not None and result > 0.0 else None


def _lookup(ctx: Dict[str, Any], key: str) -> Any:
    if ctx.get(key) is not None:
        return ctx[key]
    for section in ("weather", "venue"):
        nested = ctx.get(section)
        if isinstance(nested, dict) and nested.get(key) is not None:
            return nested[key]
    return None


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "1.0", "true", "yes"}


def _rating(ctx: Dict[str, Any], side: str, name: str) -> Optional[float]:
    flat = _positive(ctx.get(f"{side}_{name}"))
    if flat is not None:
        return flat
    nested = ctx.get(side)
    return _positive(nested.get(name)) if isinstance(nested, dict) else None


def _log_ratio(numerator: Optional[float], denominator: Optional[float]) -> float:
    # Using ratios makes the term independent of the rating's units.
    if numerator is None or denominator is None:
        return 0.0
    return math.log(numerator / denominator)


class NCAAFThirdQuarterMoneylineModel(BaseCandidateModel):
    """Exact margin lattice for NCAAF Q3 moneylines.

    Each side's score is S_i = sum_k k * N_ik, where
    N_ik | T ~ Poisson(lambda_i * w_ik * T) and T ~ Gamma(1/s2, 1/s2).

    No separate home-field term is added. Home advantage enters only through
    the expected-points gap.
    """

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_quarter_3_1106_054422",
        version: str = "1.0.0",
        league: str = "NCAAF",
        scope: str = "quarter_3",
        family: str = "moneyline",
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.params: Dict[str, Any] = {**DEFAULT_PARAMS, **(params or {})}

    def _wind_speed(self, ctx: Dict[str, Any]) -> float:
        if _flag(_lookup(ctx, "is_dome")):
            return 0.0
        return max(_num(_lookup(ctx, "wind_speed")) or 0.0, 0.0)

    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        p = self.params
        supplied_home = _positive(ctx.get("home_expected"))
        supplied_away = _positive(ctx.get("away_expected"))

        baseline_home, baseline_away = p["baseline_home"], p["baseline_away"]
        if _flag(_lookup(ctx, "neutral_site")):
            # The baseline gap is home advantage, which doesn't apply at a neutral site.
            baseline_home = baseline_away = 0.5 * (baseline_home + baseline_away)

        mu_home = supplied_home if supplied_home is not None else baseline_home
        mu_away = supplied_away if supplied_away is not None else baseline_away

        has_expected = supplied_home is not None and supplied_away is not None
        rating_beta = p["rating_beta_with_expected" if has_expected else "rating_beta_without_expected"]
        rating_edge = _log_ratio(
            _rating(ctx, "home", "attack"), _rating(ctx, "away", "attack")
        ) + _log_ratio(
            _rating(ctx, "away", "defense"), _rating(ctx, "home", "defense")
        )

        rest_edge = 0.0
        rest_home = _num(ctx.get("home_rest_days"))
        rest_away = _num(ctx.get("away_rest_days"))
        if rest_home is not None and rest_away is not None:
            rest_edge = math.log((1.0 + max(rest_home, 0.0)) / (1.0 + max(rest_away, 0.0)))

        # Split each edge evenly so it moves the margin while leaving the total nearly unchanged.
        tilt = 0.5 * (rating_beta * rating_edge + p["rest_beta"] * rest_edge)

        pace = _positive(ctx.get("pace"))
        if pace is None:
            pace = _positive(ctx.get("tempo"))
        pace_term = p["pace_beta"] * _log_ratio(pace, _positive(ctx.get("league_pace")))

        wind_term = -p["wind_rate_beta"] * self._wind_speed(ctx) ** 2

        common = pace_term + wind_term
        return mu_home * math.exp(common + tilt), mu_away * math.exp(common - tilt)

    def _scoring_mix(self, ctx: Dict[str, Any]) -> np.ndarray:
        weights = np.asarray(self.params["scoring_weights"], dtype=float).copy()
        wind = self._wind_speed(ctx)
        weights[SCORE_VALUES == 3.0] *= math.exp(-self.params["wind_fg_beta"] * wind**2)
        return weights / weights.sum()

    def margin_pmf(self, game_context: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
        """Return integer home-minus-away margins and their probabilities."""
        mu_home, mu_away = self._expected_points(game_context)
        weights = self._scoring_mix(game_context)
        tempo_variance = max(float(self.params["tempo_variance"]), 0.0)

        mean_points = float(weights @ SCORE_VALUES)
        second_moment = float(weights @ SCORE_VALUES**2)
        lambda_home = mu_home / mean_points
        lambda_away = mu_away / mean_points

        mean_margin = mu_home - mu_away
        variance = (lambda_home + lambda_away) * second_moment + tempo_variance * mean_margin**2
        half_width = abs(mean_margin) + self.params["lattice_sd_multiple"] * math.sqrt(variance)
        size = 2 ** int(math.ceil(math.log2(2.0 * half_width + 2.0)))

        t = 2.0 * np.pi * np.arange(size) / size
        phase = np.outer(t, SCORE_VALUES)
        psi = lambda_home * ((np.exp(1j * phase) - 1.0) @ weights) + lambda_away * (
            (np.exp(-1j * phase) - 1.0) @ weights
        )

        if tempo_variance > 0.0:
            # Re(1 - s2 * psi) >= 1, so the principal log branch stays continuous.
            phi = np.exp(-np.log(1.0 - tempo_variance * psi) / tempo_variance)
        else:
            phi = np.exp(psi)

        pmf = np.clip(np.real(np.fft.fft(phi)) / size, 0.0, None)
        pmf /= pmf.sum()
        margins = np.rint(np.fft.fftfreq(size, d=1.0 / size)).astype(int)
        order = np.argsort(margins)
        return margins[order], pmf[order]

    def _outcome_probabilities(self, game_context: Dict[str, Any]) -> Tuple[float, float, float]:
        margins, pmf = self.margin_pmf(game_context)
        return (
            float(pmf[margins > 0].sum()),
            float(pmf[margins == 0].sum()),
            float(pmf[margins < 0].sum()),
        )

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        p_home, p_draw, p_away = self._outcome_probabilities(game_context)
        return DiscreteDistribution(
            probabilities={"home": p_home, "draw": p_draw, "away": p_away}
        )

    def yes_probability(
        self, game_context: Dict[str, Any], side: str = "home", tie_rule: str = "split"
    ) -> float:
        """Price a YES contract under its tie rule: 'split', 'no' or 'void'."""
        if side not in {"home", "away"}:
            raise ValueError(f"Unknown side: {side!r}")
        p_home, p_draw, p_away = self._outcome_probabilities(game_context)
        p_side = p_home if side == "home" else p_away
        if tie_rule == "split":
            return p_side + 0.5 * p_draw
        if tie_rule == "no":
            return p_side
        if tie_rule == "void":
            return p_side / (p_home + p_away)
        raise ValueError(f"Unknown tie rule: {tie_rule!r}")