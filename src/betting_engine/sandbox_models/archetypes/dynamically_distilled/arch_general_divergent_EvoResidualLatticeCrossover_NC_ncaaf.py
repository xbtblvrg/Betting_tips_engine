from __future__ import annotations
import math
from typing import Any, Dict, Optional
from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

class EvoResidualLatticeCrossover_NCAAF_full_game(BaseCandidateModel):
    """Evolutionary crossover integrating empirical residual bias damping and situational rest/pace dynamics."""

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaf_full_game_1122_011314",
        version: str = "2.0.0",
        league: str = "NCAAF",
        scope: str = "full_game",
        family: str = "moneyline",
        home_drift: float = 1.0432,
        away_drift: float = 0.953,
        dispersion_alpha: float = 0.12,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {'home_drift': home_drift, 'away_drift': away_drift, 'dispersion_alpha': dispersion_alpha},
        )
        self.home_drift = home_drift
        self.away_drift = away_drift
        self.dispersion_alpha = dispersion_alpha

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        h_base = float(game_context.get('home_expected') or game_context.get('home_points') or 28.0)
        a_base = float(game_context.get('away_expected') or game_context.get('away_points') or 25.0)
        h_rest = float(game_context.get('home_rest_days') or game_context.get('home_rest') or 2.0)
        a_rest = float(game_context.get('away_rest_days') or game_context.get('away_rest') or 2.0)
        rest_adj = math.tanh((h_rest - a_rest) * 0.08) * 0.04
        wind = float(game_context.get('wind_speed') or game_context.get('wind_kph') or 0.0)
        is_dome = bool(game_context.get('is_dome', False))
        wind_damp = max(0.88, 1.0 - max(0.0, wind - 18.0) * 0.004) if not is_dome else 1.0

        h_mu = max(0.15, h_base * self.home_drift * (1.0 + rest_adj) * wind_damp)
        a_mu = max(0.15, a_base * self.away_drift * (1.0 - rest_adj) * wind_damp)
        max_sc = 8 if max(h_mu, a_mu) < 12.0 else None
        scores = build_universal_score_lattice(h_mu, a_mu, dispersion_alpha=self.dispersion_alpha, max_discrete_score=max_sc)
        return DiscreteDistribution.from_scores(scores)
