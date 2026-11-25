from __future__ import annotations
import math
from typing import Any, Dict, Optional
from betting_engine.sandbox_models.base import (
    BaseCandidateModel,
    DiscreteDistribution,
    build_copula_score_lattice,
    build_universal_score_lattice,
)

class EvoSharedShockCopulaCrossover_NFL_full_game(BaseCandidateModel):
    """Evolutionary bivariate dependence crossover with line-magnitude variance adaptation."""

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_full_game_1125_171007",
        version: str = "2.0.0",
        league: str = "NFL",
        scope: str = "full_game",
        family: str = "spreads",
        line_slope: float = -0.0985,
        dispersion_alpha: float = 0.55,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_expected', 'away_expected'],
            metadata=metadata or {'line_slope': line_slope, 'dispersion_alpha': dispersion_alpha},
        )
        self.line_slope = line_slope
        self.dispersion_alpha = dispersion_alpha

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        h_base = float(game_context.get('home_expected') or game_context.get('home_points') or 24.5)
        a_base = float(game_context.get('away_expected') or game_context.get('away_points') or 21.5)
        ref_tot = float(game_context.get('total_line') or game_context.get('reference_total') or (h_base + a_base))
        base_tot = max(0.5, 46.0)
        rel_mag = math.tanh((ref_tot - base_tot) / max(1.0, base_tot))
        mag_adj = max(0.85, min(1.15, 1.0 + self.line_slope * rel_mag))

        h_mu = max(0.15, h_base * 1.027 * mag_adj)
        a_mu = max(0.15, a_base * 0.9232 * mag_adj)
        eff_alpha = max(0.02, min(0.60, self.dispersion_alpha * (1.0 + 0.25 * abs(rel_mag))))
        try:
            if max(h_mu, a_mu) < 12.0:
                scores = build_copula_score_lattice(h_mu, a_mu, copula_family="frank", theta=1.15, dispersion_alpha=eff_alpha, max_score=8)
            else:
                scores = build_universal_score_lattice(h_mu, a_mu, dispersion_alpha=eff_alpha)
        except Exception:
            scores = build_universal_score_lattice(h_mu, a_mu, dispersion_alpha=eff_alpha)
        return DiscreteDistribution.from_scores(scores)
