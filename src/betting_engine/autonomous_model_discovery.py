"""Autonomous AI Model Discovery & Expansion Engine.

Continuously discovers, synthesizes, and tests domain-specific mathematical model archetypes
drawn from sports quantitative finance literature (Dixon-Coles, Skellam, Markov Chains,
Weibull Renewal, Extreme Value Theory, Copulas, Overdispersion, Zero-Inflation).

Targets unresolved demoted markets in Tier 1 (research_training), proves out-of-sample edge
in the Walk-Forward Crucible on historical match data, and automatically promotes qualified
markets to Tier 2 (Shadow Incubator).
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .demoted_market_bridge import DemotedMarketIncubationBridge
from .historical_trainer import HistoricalCandidateTrainer, CandidateTrainingResult
from .providers import DATA
from .shadow_market_incubator import ShadowMarketIncubator
from .tournament_engine import TournamentEngine


DISCOVERY_LOG_FILE = DATA / "research" / "model_discoveries.jsonl"
INCUBATOR_DB = DATA / "shadow_incubator.sqlite3"


@dataclass
class ModelDiscoveryRecord:
    discovery_id: str
    timestamp: str
    segment_key: str
    league: str
    scope: str
    family: str
    winning_archetype: str
    winning_model_id: str
    historical_roi: float
    historical_brier_delta: float
    historical_sample_size: int
    historical_max_drawdown: float
    parameters: Dict[str, Any]
    scientific_basis: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AutonomousModelDiscoveryEngine:
    """Autonomous quantitative model discovery synthesizer."""

    LITERATURE_TAXONOMY: Dict[str, str] = {
        'DixonColesSoccerModel': 'Dixon & Coles (1997) Bivariate Poisson with low-scoring dependency adjustment',
        'SkellamDifferentialModel': 'J.G. Skellam (1946) Discrete Bessel difference distribution for spreads/3-way',
        'MarkovChainTennisModel': 'Absorbing Markov Chain for point-to-game-to-set tennis transition probabilities',
        'WeibullRenewalHazardModel': 'Weibull accelerating hazard process for late-period empty-net & injury-time dynamics',
        'ExtremeValueGumbelModel': 'Fisher-Tippett-Gnedenko Extreme Value Theory for heavy-tail underdog blowouts',
        'BivariateCopulaHazardModel': 'Sklar (1959) Gaussian Copula coupling marginal Poisson distributions for tempo correlation',
        'NegativeBinomialOverdispersionModel': 'Compound Poisson-Gamma overdispersion for variance > mean sports scoring',
        'ZeroInflatedPoissonModel': 'Lambert (1992) Zero-Inflated Poisson for defensive lockouts and excess draw clustering',
        'OddsDampedKellyShrinkageModel': 'Empirical Bayes shrinkage against bookmaker vigorish and favorite-longshot bias',
        'SituationalFatigueModel': 'Biorhythmic rest asymmetry and back-to-back schedule fatigue decay',
    }

    def __init__(
        self,
        db_path: Optional[Path] = None,
        tournament_engine: Optional[TournamentEngine] = None,
        historical_trainer: Optional[HistoricalCandidateTrainer] = None,
    ):
        self.db_path = db_path or INCUBATOR_DB
        self.tournament_engine = tournament_engine or TournamentEngine()
        self.historical_trainer = historical_trainer or HistoricalCandidateTrainer(db_path=self.db_path)
        self.bridge = DemotedMarketIncubationBridge(
            db_path=self.db_path,
        )

    def list_available_archetypes(self) -> List[Dict[str, str]]:
        """Returns all dynamically discovered mathematical archetypes in the engine."""
        class_map = self.tournament_engine._get_class_map()
        catalog = []
        for name in sorted(class_map.keys()):
            catalog.append({
                'archetype_name': name,
                'scientific_basis': self.LITERATURE_TAXONOMY.get(
                    name, 'Advanced domain quantitative model archetype'
                ),
            })
        return catalog

    def scan_unresolved_tier1_markets(self) -> List[Dict[str, Any]]:
        """Finds all demoted markets currently sitting in Tier 1 research_training."""
        if not self.db_path.exists():
            return []

        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, segment_key, league, scope, family, sub_family, rolling_roi, rolling_pnl, games, demoted_at
                FROM demoted_markets
                WHERE incubation_phase = 'research_training'
                ORDER BY league, scope, family
            """)
            rows = [dict(r) for r in cur.fetchall()]
            return rows
        finally:
            conn.close()

    def run_discovery_cycle(self, max_markets: Optional[int] = None) -> List[ModelDiscoveryRecord]:
        """Runs the autonomous model discovery cycle across unresolved demoted markets.
        
        Evaluates candidate models across all available archetypes on the historical match database.
        When a model achieves statistical edge (ROI >= +3.5%, Delta Brier > 0, Max DD < 25%),
        it binds the model and admits the market to Tier 2 (Shadow Incubator).
        """
        tier1_markets = self.scan_unresolved_tier1_markets()
        if max_markets:
            tier1_markets = tier1_markets[:max_markets]

        new_discoveries: List[ModelDiscoveryRecord] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for market in tier1_markets:
            segment_key = market['segment_key']
            league = market['league']
            scope = market['scope']
            family = market['family']
            d_id = market.get('id')

            # Run historical training across the expanded archetype library
            best_cand = self.historical_trainer.train_and_screen_demoted_market(segment_key)
            if not best_cand:
                continue

            # Directly admit and bind champion to the demoted market in Tier 2
            success = self.bridge.admit_champion_to_market(d_id, segment_key, best_cand)
            if not success:
                continue

            # Record successful edge discovery
            disc_id = f"disc_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{segment_key.replace('|', '_')}"
            record = ModelDiscoveryRecord(
                discovery_id=disc_id,
                timestamp=now_iso,
                segment_key=segment_key,
                league=league,
                scope=scope,
                family=family,
                winning_archetype=best_cand.model_archetype,
                winning_model_id=best_cand.model_id,
                historical_roi=best_cand.oos_simulated_roi,
                historical_brier_delta=best_cand.oos_delta_brier,
                historical_sample_size=best_cand.oos_sample_size,
                historical_max_drawdown=best_cand.oos_max_drawdown,
                parameters=best_cand.model_params,
                scientific_basis=self.LITERATURE_TAXONOMY.get(
                    best_cand.model_archetype, 'Autonomous domain formulation'
                ),
            )
            new_discoveries.append(record)
            self._log_discovery(record)

        return new_discoveries

    def _log_discovery(self, record: ModelDiscoveryRecord) -> None:
        """Appends discovery record to persistent audit journal."""
        DISCOVERY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with DISCOVERY_LOG_FILE.open('a', encoding='utf-8') as f:
                f.write(json.dumps(record.to_dict()) + '\n')
        except Exception:
            pass

    def get_discovery_summary(self) -> Dict[str, Any]:
        """Provides a complete quantitative summary of the model library and Tier progression."""
        available_archetypes = self.list_available_archetypes()
        unresolved = self.scan_unresolved_tier1_markets()

        # Count Tier 2 incubating markets
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT model_archetype, COUNT(*) as count
                FROM demoted_markets
                WHERE incubation_phase = 'incubating_with_new_model'
                GROUP BY model_archetype
            """)
            tier2_breakdown = {r['model_archetype']: r['count'] for r in cur.fetchall()}
            tier2_total = sum(tier2_breakdown.values())
        except Exception:
            tier2_breakdown = {}
            tier2_total = 0
        finally:
            conn.close()

        return {
            'total_archetypes_in_library': len(available_archetypes),
            'available_archetypes': available_archetypes,
            'tier2_incubating_count': tier2_total,
            'tier2_by_archetype': tier2_breakdown,
            'tier1_unresolved_count': len(unresolved),
        }
