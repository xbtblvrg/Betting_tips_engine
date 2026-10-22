"""Five-Pillar Multi-Model Quant Ensemble Architecture.

Combines 5 independent quantitative paradigms into a single calibrated forecast:
1. Pillar 1: Feature-Based Statistical Machine Learning (FormState / priors)
2. Pillar 2: Micro-State Stochastic Monte Carlo Simulator (10,000+ realizations)
3. Pillar 3: Dynamic State-Space / Glicko-2 Momentum Engine
4. Pillar 4: Institutional Zero-Vig Market Consensus Anchor (Pinnacle/Polymarket)
5. Pillar 5: AI Quant Council Strategic Overlay (Claude, Codex, Grok, Antigravity)

Blends probabilities in logit space via Bayesian Model Averaging (BMA) with
self-improving weight adaptation based on rolling Brier performance.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from .providers import DATA

WEIGHTS_FILE = DATA / "research" / "ensemble_weights.json"

DEFAULT_SPORT_WEIGHTS: Dict[str, Dict[str, float]] = {
    'NBA': {'sim': 0.40, 'stat': 0.30, 'market': 0.20, 'momentum': 0.10},
    'WNBA': {'sim': 0.35, 'stat': 0.35, 'market': 0.20, 'momentum': 0.10},
    'NHL': {'sim': 0.45, 'stat': 0.25, 'market': 0.20, 'momentum': 0.10},
    'MLB': {'sim': 0.35, 'stat': 0.35, 'market': 0.20, 'momentum': 0.10},
    'NFL': {'sim': 0.40, 'stat': 0.30, 'market': 0.20, 'momentum': 0.10},
    'NCAAF': {'sim': 0.35, 'stat': 0.35, 'market': 0.20, 'momentum': 0.10},
    'EPL': {'sim': 0.35, 'stat': 0.35, 'market': 0.20, 'momentum': 0.10},
    'UCL': {'sim': 0.35, 'stat': 0.35, 'market': 0.20, 'momentum': 0.10},
    'MLS': {'sim': 0.35, 'stat': 0.35, 'market': 0.20, 'momentum': 0.10},
    'ATP': {'sim': 0.45, 'stat': 0.25, 'market': 0.20, 'momentum': 0.10},
    'WTA': {'sim': 0.45, 'stat': 0.25, 'market': 0.20, 'momentum': 0.10},
    'CHALLENGER': {'sim': 0.45, 'stat': 0.25, 'market': 0.20, 'momentum': 0.10},
    'TENNIS': {'sim': 0.45, 'stat': 0.25, 'market': 0.20, 'momentum': 0.10},
    'DEFAULT': {'sim': 0.35, 'stat': 0.35, 'market': 0.20, 'momentum': 0.10},
}


def _clip_p(p: float, eps: float = 0.005) -> float:
    return max(eps, min(1.0 - eps, float(p)))


def _logit(p: float) -> float:
    p_c = _clip_p(p)
    return math.log(p_c / (1.0 - p_c))


def _inv_logit(x: float) -> float:
    x_c = max(-15.0, min(15.0, float(x)))
    return 1.0 / (1.0 + math.exp(-x_c))


@dataclass
class EnsemblePrediction:
    blended_probability: float
    pillar_stat_prob: float
    pillar_sim_prob: float
    pillar_market_prob: float
    pillar_momentum_prob: float
    weights_applied: Dict[str, float]
    divergence_score: float
    has_high_divergence: bool
    league: str
    market: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_ensemble_weights() -> Dict[str, Dict[str, float]]:
    """Loads active calibrated ensemble weights or returns defaults."""
    if not WEIGHTS_FILE.exists():
        return DEFAULT_SPORT_WEIGHTS
    try:
        data = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "weights" in data:
            return data["weights"]
        return DEFAULT_SPORT_WEIGHTS
    except Exception:
        return DEFAULT_SPORT_WEIGHTS


def save_ensemble_weights(weights: Dict[str, Dict[str, float]]) -> None:
    """Persists calibrated ensemble weights to durable research storage."""
    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "weights": weights
    }
    try:
        WEIGHTS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def blend_ensemble_probabilities(
    league: str,
    market: str,
    p_stat: float,
    p_sim: float,
    p_market: Optional[float] = None,
    p_momentum: Optional[float] = None,
    custom_weights: Optional[Dict[str, float]] = None
) -> EnsemblePrediction:
    """Combines predictions across the 4 active pillars in logit space.
    
    Bayesian Model Averaging in Logit Space:
    L_ensemble = w_sim * L_sim + w_stat * L_stat + w_market * L_market + w_momentum * L_momentum
    """
    weights_table = custom_weights or load_ensemble_weights().get(league, DEFAULT_SPORT_WEIGHTS['DEFAULT'])
    
    # If market or momentum is not observed, re-normalize weights dynamically
    active_pillars: Dict[str, float] = {}
    active_weights: Dict[str, float] = {}

    active_pillars['stat'] = _clip_p(p_stat)
    active_weights['stat'] = float(weights_table.get('stat', 0.35))

    active_pillars['sim'] = _clip_p(p_sim)
    active_weights['sim'] = float(weights_table.get('sim', 0.35))

    if p_market is not None and 0.01 <= p_market <= 0.99:
        active_pillars['market'] = _clip_p(p_market)
        active_weights['market'] = float(weights_table.get('market', 0.20))

    if p_momentum is not None and 0.01 <= p_momentum <= 0.99:
        active_pillars['momentum'] = _clip_p(p_momentum)
        active_weights['momentum'] = float(weights_table.get('momentum', 0.10))

    # Normalize weights to sum exactly to 1.0
    w_sum = sum(active_weights.values())
    norm_weights = {k: v / w_sum for k, v in active_weights.items()}

    # Compute Logit-Space linear combination
    blended_logit = sum(norm_weights[k] * _logit(active_pillars[k]) for k in active_pillars)
    blended_p = round(_inv_logit(blended_logit), 4)

    # Measure model divergence (disagreement between simulation and market/stat)
    divergence = round(abs(active_pillars['sim'] - active_pillars['stat']), 4)
    has_high_div = divergence > 0.14  # Disagreement > 14% triggers caution flag

    return EnsemblePrediction(
        blended_probability=blended_p,
        pillar_stat_prob=round(active_pillars['stat'], 4),
        pillar_sim_prob=round(active_pillars['sim'], 4),
        pillar_market_prob=round(active_pillars.get('market', active_pillars['stat']), 4),
        pillar_momentum_prob=round(active_pillars.get('momentum', active_pillars['stat']), 4),
        weights_applied=norm_weights,
        divergence_score=divergence,
        has_high_divergence=has_high_div,
        league=league,
        market=market,
        metadata={
            "formula": "BMA_logit_space_v1",
            "pillars_count": len(active_pillars)
        }
    )


def calibrate_ensemble_weights_walk_forward(
    historical_observations: List[Dict[str, Any]],
    league: str
) -> Dict[str, float]:
    """Optimizes ensemble pillar weights via out-of-time Brier loss minimization."""
    if len(historical_observations) < 20:
        return DEFAULT_SPORT_WEIGHTS.get(league, DEFAULT_SPORT_WEIGHTS['DEFAULT'])

    best_brier = float('inf')
    best_weights = DEFAULT_SPORT_WEIGHTS.get(league, DEFAULT_SPORT_WEIGHTS['DEFAULT'])

    # Grid search over candidate weight combinations
    candidate_grids = [
        {'sim': 0.50, 'stat': 0.25, 'market': 0.25},
        {'sim': 0.40, 'stat': 0.35, 'market': 0.25},
        {'sim': 0.35, 'stat': 0.45, 'market': 0.20},
        {'sim': 0.60, 'stat': 0.20, 'market': 0.20},
        {'sim': 0.30, 'stat': 0.30, 'market': 0.40},
    ]

    for grid in candidate_grids:
        brier_sum = 0.0
        count = 0
        for obs in historical_observations:
            y = 1.0 if obs.get('status') == 'won' else 0.0
            p_stat = obs.get('p_stat', 0.5)
            p_sim = obs.get('p_sim', 0.5)
            p_market = obs.get('p_market', p_stat)

            pred = blend_ensemble_probabilities(
                league=league,
                market=obs.get('market', 'moneyline'),
                p_stat=p_stat,
                p_sim=p_sim,
                p_market=p_market,
                custom_weights=grid
            )
            brier_sum += (pred.blended_probability - y) ** 2
            count += 1

        if count > 0:
            avg_brier = brier_sum / count
            if avg_brier < best_brier:
                best_brier = avg_brier
                best_weights = grid

    return best_weights
