"""Extreme Value Theory (EVT) Gumbel Model Archetype.

Models asymmetric heavy tails and blowout probability differentials using
Gumbel Generalized Extreme Value (GEV) marginal distributions. Resolves Gaussian
thin-tail collapse for high-odds underdogs and wide handicap lines.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.stats import gumbel_r

from ..base import BaseCandidateModel, DiscreteDistribution


class ExtremeValueGumbelModel(BaseCandidateModel):
    """Candidate model utilizing Gumbel Extreme Value distributions for asymmetric tail alpha."""

    def __init__(
        self,
        model_id: str = "archetype_evt_gumbel_v1",
        version: str = "1.0.0",
        league: str = "NBA",
        scope: str = "full_game",
        family: str = "spreads",
        tail_scale_beta: float = 1.25,
        underdog_skew: float = 0.10,
        max_score: int = 150,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
            required_features=['home_power', 'away_power'],
            metadata=metadata or {'tail_scale_beta': tail_scale_beta, 'underdog_skew': underdog_skew},
        )
        self.tail_scale_beta = max(0.5, min(3.0, tail_scale_beta))
        self.underdog_skew = max(-0.3, min(0.3, underdog_skew))
        self.max_score = max_score

    def predict_distribution(self, game_context: Dict[str, Any]) -> DiscreteDistribution:
        default_h = 110.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (24.0 if self.league in ('NFL', 'NCAAF') else (2.8 if self.league == 'NHL' else 1.5))
        default_a = 106.0 if self.league in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') else (21.0 if self.league in ('NFL', 'NCAAF') else (2.4 if self.league == 'NHL' else 1.2))
        h_mu = float(game_context.get('home_expected') or game_context.get('home_points') or default_h)
        a_mu = float(game_context.get('away_expected') or game_context.get('away_points') or default_a)

        # Apply asymmetric underdog skew
        if h_mu < a_mu:
            h_mu += abs(h_mu - a_mu) * self.underdog_skew
        else:
            a_mu += abs(h_mu - a_mu) * self.underdog_skew

        scores: Dict[Tuple[int, int], float] = {}
        is_high_scoring = (h_mu > 15.0 or a_mu > 15.0)

        if not is_high_scoring:
            # Low scoring: discrete Gumbel discretization
            lim_h = max(8, int(math.ceil(h_mu * 2.8)))
            lim_a = max(8, int(math.ceil(a_mu * 2.8)))
            scale_h = max(0.4, math.sqrt(h_mu) * self.tail_scale_beta * 0.78)
            scale_a = max(0.4, math.sqrt(a_mu) * self.tail_scale_beta * 0.78)
            loc_h = h_mu - 0.5772 * scale_h
            loc_a = a_mu - 0.5772 * scale_a

            pmf_h = [max(1e-6, float(gumbel_r.pdf(h + 0.5, loc=loc_h, scale=scale_h))) for h in range(lim_h + 1)]
            pmf_a = [max(1e-6, float(gumbel_r.pdf(a + 0.5, loc=loc_a, scale=scale_a))) for a in range(lim_a + 1)]
            sum_h, sum_a = sum(pmf_h), sum(pmf_a)
            pmf_h = [p / sum_h for p in pmf_h]
            pmf_a = [p / sum_a for p in pmf_a]

            for h in range(lim_h + 1):
                for a in range(lim_a + 1):
                    scores[(h, a)] = pmf_h[h] * pmf_a[a]
        else:
            # High scoring: lattice discretization around expectation
            h_center, a_center = int(round(h_mu)), int(round(a_mu))
            scale = math.sqrt(h_mu + a_mu) * 0.75 * self.tail_scale_beta
            step = 2 if (h_mu > 50.0) else 1
            radius = 20 if (h_mu > 50.0) else 14

            for dh in range(-radius, radius + 1, step):
                h_val = h_center + dh
                if h_val < 0:
                    continue
                p_h = max(1e-7, float(gumbel_r.pdf(h_val, loc=h_mu - 0.5772 * scale, scale=scale)))
                for da in range(-radius, radius + 1, step):
                    a_val = a_center + da
                    if a_val < 0:
                        continue
                    p_a = max(1e-7, float(gumbel_r.pdf(a_val, loc=a_mu - 0.5772 * scale, scale=scale)))
                    scores[(h_val, a_val)] = p_h * p_a

        return DiscreteDistribution.from_scores(scores)
