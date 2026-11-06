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
from concurrent.futures import ThreadPoolExecutor
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
        data_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
        tournament_engine: Optional[TournamentEngine] = None,
        historical_trainer: Optional[HistoricalCandidateTrainer] = None,
    ):
        from .providers import DATA
        self.data_dir = Path(data_dir or DATA)
        self.db_path = Path(db_path) if db_path else (self.data_dir / 'shadow_incubator.sqlite3')
        self.tournament_engine = tournament_engine or TournamentEngine(data_dir=self.data_dir, db_path=self.db_path)
        self.historical_trainer = historical_trainer or HistoricalCandidateTrainer(db_path=self.db_path)
        self.bridge = DemotedMarketIncubationBridge(
            data_dir=self.data_dir,
            db_path=self.db_path,
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensures feedback loop and last_screened_at schema exist for round-robin discovery."""
        if not self.db_path.exists():
            return
        try:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ai_synthesis_feedback_loop (
                        id TEXT PRIMARY KEY,
                        model_id TEXT NOT NULL,
                        segment_key TEXT NOT NULL,
                        league TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        family TEXT NOT NULL,
                        generator_provider TEXT,
                        class_name TEXT,
                        oos_roi REAL,
                        oos_delta_brier REAL,
                        oos_win_rate REAL,
                        oos_max_drawdown REAL,
                        crucible_passed INTEGER,
                        created_at TEXT
                    )
                """)
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(demoted_markets)")
                cols = [r[1] for r in cur.fetchall()]
                if 'last_screened_at' not in cols and cols:
                    conn.execute("ALTER TABLE demoted_markets ADD COLUMN last_screened_at TEXT")
                conn.commit()
        except Exception:
            pass

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
                SELECT dm.id, dm.segment_key, dm.league, dm.scope, dm.family, dm.sub_family, dm.rolling_roi, dm.rolling_pnl, dm.games, dm.demoted_at, dm.last_screened_at,
                       COUNT(fb.id) as attempts
                FROM demoted_markets dm
                LEFT JOIN ai_synthesis_feedback_loop fb ON dm.segment_key = fb.segment_key
                WHERE dm.incubation_phase = 'research_training' 
                   OR (dm.incubation_phase = 'incubating_with_new_model' AND (dm.incubated_model_id IS NULL OR dm.incubated_model_id = '' OR dm.historical_roi <= 0.0))
                GROUP BY dm.id
                ORDER BY 
                    -- 1. Untouched markets with 0 attempts get top priority (ensures all 89 markets are reached immediately)
                    CASE WHEN COUNT(fb.id) = 0 THEN 0 ELSE 1 END,
                    -- 2. Strict Constitutional Mandate (Commandment 6.5): Prioritize Dual Reachable Markets (Stake + Polymarket CLOB)
                    CASE 
                        WHEN (dm.league IN ('NBA', 'NFL', 'EPL', 'MLS', 'UCL', 'NHL', 'MLB', 'ATP', 'WTA')
                              AND dm.scope IN ('full_game', 'half_1', 'half_2', 'quarter_1', 'quarter_2', 'quarter_3', 'quarter_4', 'period_1', 'period_2')
                              AND (dm.family LIKE '%moneyline%' OR dm.family LIKE '%spread%' OR dm.family LIKE '%totals%' OR dm.family = 'totals' OR dm.family LIKE '%winner%' OR dm.family LIKE '%three_way%' OR dm.family LIKE '%both%')
                              AND dm.family NOT LIKE '%home_total%' AND dm.family NOT LIKE '%away_total%' AND dm.family NOT LIKE '%team_total%')
                        THEN 0 ELSE 1
                    END,
                    -- 3. Leaky markets with negative rolling PnL (tourniquet turnaround)
                    CASE WHEN dm.rolling_pnl < 0.0 THEN 0 ELSE 1 END,
                    -- 4. Tier 1 League preference
                    CASE 
                        WHEN dm.league IN ('NBA', 'NFL', 'EPL', 'MLS') THEN 0
                        WHEN dm.league IN ('NHL', 'UCL') THEN 1
                        WHEN dm.league IN ('MLB', 'ATP', 'WTA') THEN 2
                        WHEN dm.league IN ('NCAAF', 'NCAAM', 'NCAAW') THEN 3
                        ELSE 4
                    END,
                    -- 5. Stratified Aging Round-Robin: oldest screened first
                    CASE WHEN dm.last_screened_at IS NULL THEN 0 ELSE 1 END,
                    COALESCE(dm.last_screened_at, '') ASC,
                    -- 6. Fewest attempts first (prevent 400+ repeat attempts)
                    COUNT(fb.id) ASC,
                    dm.scope, dm.family
            """)
            raw_rows = [dict(r) for r in cur.fetchall()]
            from .contracts import is_supported_stake_contract
            actionable_rows = [
                r for r in raw_rows
                if is_supported_stake_contract(r.get('league', ''), r.get('scope', 'full_game'), r.get('family', ''), r.get('sub_family', ''))
            ]
            return actionable_rows
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

        if not tier1_markets:
            return []

        # Batch touch last_screened_at immediately so the queue advances even if a worker crashes
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(str(self.db_path), timeout=30.0) as touch_conn:
                touch_conn.execute("PRAGMA busy_timeout = 30000")
                touch_conn.execute(
                    f"UPDATE demoted_markets SET last_screened_at = ? WHERE id IN ({','.join('?' for _ in tier1_markets)})",
                    [now_iso] + [m['id'] for m in tier1_markets]
                )
                touch_conn.commit()
        except Exception:
            pass

        # Multi-core rapid archetype screening across the 97k match pool
        def _screen_archetype(m):
            try:
                cand = self.historical_trainer.train_and_screen_demoted_market(m['segment_key'])
                return m['segment_key'], cand
            except Exception:
                return m['segment_key'], None

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(tier1_markets)))) as pool:
            archetype_results = dict(pool.map(_screen_archetype, tier1_markets))

        new_discoveries: List[ModelDiscoveryRecord] = []

        for market in tier1_markets:
            segment_key = market['segment_key']
            league = market['league']
            scope = market['scope']
            family = market['family']
            d_id = market.get('id')

            try:
                best_cand = archetype_results.get(segment_key)
                if not best_cand:
                    # If standard library has no edge, invoke autonomous Codex/Claude model synthesis
                    try:
                        from .codex_model_synthesizer import CodexModelSynthesizer
                        synthesizer = CodexModelSynthesizer(self.data_dir)
                        synth_rec = synthesizer.synthesize_and_screen_candidate(
                            segment_key=segment_key,
                            domain_problem=f"Standard archetypes failed out-of-sample backtest on {segment_key}. Requires novel mathematical formulation."
                        )
                        if synth_rec.crucible_passed and synth_rec.is_admitted_to_incubator:
                            candidate_result = CandidateTrainingResult(
                                segment_key=segment_key,
                                model_id=synth_rec.model_id,
                                model_archetype=synth_rec.class_name,
                                model_params={'source_file': f"{synth_rec.model_id}.py"},
                                train_sample_size=0,
                                oos_sample_size=synth_rec.oos_sample_size,
                                oos_win_rate=synth_rec.oos_win_rate,
                                oos_simulated_roi=synth_rec.oos_simulated_roi,
                                oos_delta_brier=synth_rec.oos_delta_brier,
                                oos_max_drawdown=synth_rec.oos_max_drawdown,
                                is_qualified=True,
                                scorecard_s=100.0,
                            )
                            self.bridge.admit_champion_to_market(d_id, segment_key, candidate_result)

                            disc_id = f"disc_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{segment_key.replace('|', '_')}"
                            record = ModelDiscoveryRecord(
                                discovery_id=disc_id,
                                timestamp=now_iso,
                                segment_key=segment_key,
                                league=league,
                                scope=scope,
                                family=family,
                                winning_archetype=synth_rec.class_name,
                                winning_model_id=synth_rec.model_id,
                                historical_roi=synth_rec.oos_simulated_roi,
                                historical_brier_delta=synth_rec.oos_delta_brier,
                                historical_sample_size=synth_rec.oos_sample_size,
                                historical_max_drawdown=synth_rec.oos_max_drawdown,
                                parameters={'synthesizer': synth_rec.generator_provider, 'rationale': synth_rec.rationale},
                                scientific_basis=f"Codex-synthesized mathematical formulation: {synth_rec.class_name}",
                            )
                            new_discoveries.append(record)
                            self._log_discovery(record)
                    except Exception:
                        pass
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
            except Exception:
                # Isolate unexpected training failures and proceed with other candidate markets
                continue
            finally:
                # Always advance last_screened_at for this market
                try:
                    with sqlite3.connect(str(self.db_path), timeout=10.0) as fin_conn:
                        fin_conn.execute("PRAGMA busy_timeout = 10000")
                        fin_conn.execute(
                            "UPDATE demoted_markets SET last_screened_at = ? WHERE id = ?",
                            (datetime.now(timezone.utc).isoformat(), d_id)
                        )
                        fin_conn.commit()
                except Exception:
                    pass

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
