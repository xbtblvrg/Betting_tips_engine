"""Demoted Market Incubation Bridge.

Connects the R&D historical model trainer directly to the Shadow Incubator.
Discovers out-of-sample positive-edge candidate models on historical settled wagers,
binds them to demoted market segments, and formally transitions them into the
Shadow Incubator with active model tracking for Crucible re-graduation.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union
from contextlib import closing

from .historical_trainer import HistoricalCandidateTrainer
from .providers import DATA
from .quant_rd_agent import append_rd_log
from .tournament_engine import TournamentEngine, normalize_segment_key

log = logging.getLogger(__name__)


INCUBATOR_DB = DATA / 'shadow_incubator.sqlite3'


class DemotedMarketIncubationBridge:
    """Orchestrates the binding of historically proven candidate models to demoted markets."""

    def __init__(self, data_dir: Union[Path, str] = DATA, db_path: Optional[Union[Path, str]] = None):
        self.data_dir = Path(data_dir) if isinstance(data_dir, str) else data_dir
        self.db_path = Path(db_path) if db_path else (self.data_dir / 'shadow_incubator.sqlite3')
        self.trainer = HistoricalCandidateTrainer(db_path=self.db_path)
        self.tournament_engine = TournamentEngine(data_dir=data_dir, db_path=self.db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensures last_screened_at column exists for fair round-robin scheduling."""
        if not self.db_path.exists():
            return
        try:
            with closing(sqlite3.connect(str(self.db_path), timeout=30.0)) as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                conn.execute("ALTER TABLE demoted_markets ADD COLUMN last_screened_at TEXT")
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            pass

    def synchronize_demoted_markets(self, max_train_per_run: int = 25) -> Dict[str, Any]:
        """Scans demoted markets, trains candidate models on historical data, and admits them into the incubator."""
        if not self.db_path.exists():
            return {'status': 'no_db', 'newly_admitted_count': 0}

        # Only sync roster if demoted_markets table is empty to avoid expensive full-universe rebuilds
        try:
            with closing(sqlite3.connect(str(self.db_path), timeout=5.0)) as c_chk:
                has_rows = bool(c_chk.execute("SELECT 1 FROM demoted_markets LIMIT 1").fetchone())
        except Exception:
            has_rows = False

        if not has_rows and (self.data_dir.resolve() == DATA.resolve() or (self.data_dir / 'engine.sqlite3').exists()):
            try:
                from .unified_market_universe import UnifiedMarketUniverse
                UnifiedMarketUniverse(self.data_dir).build_universe_roster(force_refresh=False)
            except Exception as e_sync:
                log.debug("Could not auto-sync roster to demoted_markets: %s", e_sync)

        try:
            conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=30.0)
            conn.execute("PRAGMA busy_timeout = 30000")
        except sqlite3.OperationalError:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.execute("PRAGMA busy_timeout = 30000")

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, segment_key, league, scope, family, sub_family, rolling_roi, rolling_pnl, games, incubation_phase, incubated_model_id
                FROM demoted_markets
                ORDER BY 
                    CASE WHEN incubation_phase = 'incubating_with_new_model' THEN 1 ELSE 0 END,
                    CASE WHEN last_screened_at IS NULL THEN 0 ELSE 1 END,
                    -- STRICT MANDATE: Prioritize Dual Reachable Markets (Stake + Polymarket CLOB)
                    CASE 
                        WHEN (league IN ('NBA', 'NFL', 'NHL', 'MLS', 'EPL', 'UCL', 'UEL', 'ATP', 'WTA', 'NCAAF', 'NCAAB', 'NCAAM', 'NCAAW', 'MLB')
                              AND scope IN ('full_game', 'half_1', 'quarter_1', 'quarter_2', 'period_1')
                              AND (family LIKE '%moneyline%' OR family LIKE '%spread%' OR family LIKE '%total%' OR family LIKE '%winner%' OR family LIKE '%three_way%'))
                        THEN 0 ELSE 1 
                    END,
                    CASE 
                        WHEN league IN ('NBA', 'NFL', 'EPL', 'MLS') THEN 0
                        WHEN league = 'NHL' THEN 1
                        WHEN league IN ('MLB', 'UCL', 'ATP', 'WTA') THEN 2
                        ELSE 3
                    END,
                    CASE WHEN rolling_pnl < 0.0 THEN 0 ELSE 1 END,
                    COALESCE(last_screened_at, '') ASC,
                    id ASC
            """)
            all_demoted = cur.fetchall()
        finally:
            conn.close()

        if not all_demoted:
            return {'status': 'no_demoted_markets', 'newly_admitted_count': 0}

        newly_admitted = []
        already_incubating = 0
        still_researching = 0

        to_screen = []
        for row in all_demoted:
            d_id, seg_key, league, scope, family, sub_fam, r_roi, r_pnl, g_count, phase, model_id = row

            if phase == 'incubating_with_new_model' and model_id:
                already_incubating += 1
                continue

            if len(to_screen) < max_train_per_run:
                to_screen.append(row)

        attempted_this_run = len(to_screen)

        # Batch touch last_screened_at in a single atomic transaction before parallel screening
        if to_screen:
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                with closing(sqlite3.connect(str(self.db_path), timeout=30.0)) as touch_conn:
                    touch_conn.execute("PRAGMA busy_timeout = 30000")
                    touch_conn.execute(
                        f"UPDATE demoted_markets SET last_screened_at = ? WHERE id IN ({','.join('?' for _ in to_screen)})",
                        [now_iso] + [r[0] for r in to_screen]
                    )
                    touch_conn.commit()
            except Exception as e_touch:
                log.debug("Demoted markets batch touch error: %s", e_touch)

        def _screen_archetype_market(row):
            d_id, seg_key, league, scope, family, sub_fam, r_roi, r_pnl, g_count, phase, model_id = row
            # Run historical training & out-of-sample Crucible validation (100% read-only)
            champ_res = self.trainer.train_and_screen_demoted_market(seg_key)
            return d_id, seg_key, row, champ_res

        from concurrent.futures import ThreadPoolExecutor
        unresolved = []
        if to_screen:
            with ThreadPoolExecutor(max_workers=min(8, len(to_screen))) as pool:
                screened_results = list(pool.map(_screen_archetype_market, to_screen))

            for d_id, seg_key, row, champ_res in screened_results:
                if champ_res and champ_res.is_qualified:
                    success = self.admit_champion_to_market(d_id, seg_key, champ_res)
                    if success:
                        newly_admitted.append({
                            'segment_key': seg_key,
                            'model_id': champ_res.model_id,
                            'model_archetype': champ_res.model_archetype,
                            'historical_roi': champ_res.oos_simulated_roi,
                            'historical_brier_delta': champ_res.oos_delta_brier,
                            'historical_sample_size': champ_res.oos_sample_size,
                        })
                    else:
                        unresolved.append(row)
                else:
                    unresolved.append(row)

            # Phase 2: Sequential Controlled AI Model Synthesis for unresolved markets
            # Strictly prioritizes Dual Reachable Markets (Stake + Polymarket CLOB)
            if unresolved and len(newly_admitted) < max_train_per_run:
                def _dual_priority_key(r):
                    lg = (r[2] or '').upper()
                    sc = (r[3] or '').lower()
                    fm = (r[4] or '').lower()
                    is_dual = (lg in ('NBA', 'NFL', 'EPL', 'MLS') and sc in ('full_game', 'half_1', 'quarter_1') and any(k in fm for k in ('spread', 'total', 'moneyline')))
                    return 0 if is_dual else 1

                unresolved.sort(key=_dual_priority_key)
                max_ai_syntheses = min(max_train_per_run - len(newly_admitted), len(unresolved))
                for target_row in unresolved[:max_ai_syntheses]:
                    t_did, t_seg_key = target_row[0], target_row[1]
                    try:
                        from .codex_model_synthesizer import CodexModelSynthesizer
                        synthesizer = CodexModelSynthesizer(data_dir=self.data_dir)
                        synth_rec = synthesizer.synthesize_and_screen_candidate(
                            segment_key=t_seg_key,
                            domain_problem=f"Demoted market {t_seg_key} requiring targeted quantitative repair."
                        )
                        if synth_rec.crucible_passed and synth_rec.is_admitted_to_incubator:
                            from .historical_trainer import CandidateTrainingResult
                            ai_champ = CandidateTrainingResult(
                                segment_key=t_seg_key,
                                model_id=synth_rec.model_id,
                                model_archetype=synth_rec.class_name,
                                model_params={'source_file': f"{synth_rec.model_id}.py", 'provider': synth_rec.generator_provider},
                                train_sample_size=0,
                                oos_sample_size=synth_rec.oos_sample_size,
                                oos_win_rate=synth_rec.oos_win_rate,
                                oos_simulated_roi=synth_rec.oos_simulated_roi,
                                oos_delta_brier=synth_rec.oos_delta_brier,
                                oos_max_drawdown=synth_rec.oos_max_drawdown,
                                is_qualified=True,
                                scorecard_s=100.0,
                            )
                            if self.admit_champion_to_market(t_did, t_seg_key, ai_champ):
                                newly_admitted.append({
                                    'segment_key': t_seg_key,
                                    'model_id': ai_champ.model_id,
                                    'model_archetype': ai_champ.model_archetype,
                                    'historical_roi': ai_champ.oos_simulated_roi,
                                    'historical_brier_delta': ai_champ.oos_delta_brier,
                                    'historical_sample_size': ai_champ.oos_sample_size,
                                })
                    except Exception as e_synth:
                        log.debug("Autonomous synthesis on %s: %s", t_seg_key, e_synth)

            still_researching = max(0, attempted_this_run - len(newly_admitted))

        return {
            'status': 'success',
            'newly_admitted_count': len(newly_admitted),
            'already_incubating_count': already_incubating,
            'still_researching_count': still_researching,
            'attempted_this_run': attempted_this_run,
            'newly_admitted': newly_admitted,
        }

    def admit_champion_to_market(
        self,
        d_id: Optional[str],
        seg_key: str,
        champ_res: CandidateTrainingResult,
    ) -> bool:
        """Instantiates, registers in tournament, and commits champion candidate model to demoted market."""
        parts = seg_key.split('|')
        league = parts[0] if len(parts) > 0 else 'UNKNOWN'
        scope = parts[1] if len(parts) > 1 else 'full_game'
        family = parts[2] if len(parts) > 2 else 'totals'

        # Instantiate model archetype with fitted parameters
        class_map = self.tournament_engine._get_class_map()
        clean_arch = champ_res.model_archetype.removeprefix('Dynamic_')
        cls_target = class_map.get(champ_res.model_archetype) or class_map.get(clean_arch)
        if not cls_target:
            return False

        model_meta = {
            'params': champ_res.model_params,
            'historical_roi': champ_res.oos_simulated_roi,
            'historical_delta_brier': champ_res.oos_delta_brier,
            'historical_sample_size': champ_res.oos_sample_size,
            'trained_at': datetime.now(timezone.utc).isoformat(),
        }

        init_kwargs = {
            'model_id': champ_res.model_id,
            'version': champ_res.version,
            'league': league,
            'scope': scope,
            'family': family,
            'metadata': model_meta,
        }
        init_kwargs.update(champ_res.model_params)

        import inspect
        try:
            sig = inspect.signature(cls_target.__init__)
            has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if not has_var_kwargs:
                filtered_kwargs = {k: v for k, v in init_kwargs.items() if k in sig.parameters}
            else:
                filtered_kwargs = init_kwargs
            candidate_instance = cls_target(**filtered_kwargs)
        except Exception:
            return False

        # Register candidate model in TournamentEngine
        canonical = normalize_segment_key(seg_key)
        self.tournament_engine.register_candidate(canonical, candidate_instance)

        # Update demoted_markets record to formally bind the model and transition phase
        now_iso = datetime.now(timezone.utc).isoformat()
        params_json = json.dumps(champ_res.model_params)

        # Check if already graduated during training (e.g. concurrent engine cycle)
        try:
            with closing(sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=5.0)) as chk_conn:
                is_grad = chk_conn.execute("SELECT 1 FROM graduated_markets WHERE segment_key = ?", (seg_key,)).fetchone()
                if is_grad:
                    log.info("Market %s already graduated to Tier 3 during candidate training.", seg_key)
                    return True
        except Exception:
            pass

        import time
        for attempt in range(8):
            try:
                with closing(sqlite3.connect(str(self.db_path), timeout=60.0, isolation_level=None)) as conn:
                    conn.execute("PRAGMA busy_timeout = 60000")
                    conn.execute("""
                        UPDATE demoted_markets
                        SET incubated_model_id = ?,
                            model_archetype = ?,
                            model_params = ?,
                            historical_roi = ?,
                            historical_brier_delta = ?,
                            historical_sample_size = ?,
                            incubation_phase = 'incubating_with_new_model',
                            admitted_to_incubator_at = ?
                        WHERE segment_key = ? OR id = ?
                    """, (
                        champ_res.model_id,
                        champ_res.model_archetype,
                        params_json,
                        champ_res.oos_simulated_roi,
                        champ_res.oos_delta_brier,
                        champ_res.oos_sample_size,
                        now_iso,
                        seg_key,
                        d_id or seg_key,
                    ))
                    break
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 7:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                log.warning("Could not commit champion candidate model for %s: %s", seg_key, e)
                return False

        # Record audit telemetry
        append_rd_log(
            stage='DEMOTED_INCUBATION_ADMISSION',
            message=(
                f"Demoted market {seg_key} successfully bound to new candidate model "
                f"'{champ_res.model_id}' ({champ_res.model_archetype}) and admitted to Shadow Incubator! "
                f"Historical Out-of-Sample proof: ROI={champ_res.oos_simulated_roi*100:.1f}%, "
                f"ΔBrier={champ_res.oos_delta_brier:+.5f}, N={champ_res.oos_sample_size} games."
            ),
            level='SUCCESS',
            extra={
                'segment_key': seg_key,
                'model_id': champ_res.model_id,
                'archetype': champ_res.model_archetype,
                'params': champ_res.model_params,
                'historical_roi': champ_res.oos_simulated_roi,
                'sample_size': champ_res.oos_sample_size,
            }
        )
        return True

    def proactively_screen_market(
        self,
        segment_key: str,
        incumbent_roi: Optional[float] = None,
        incumbent_delta_brier: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Proactive Unified Crucible Architecture (PUCA):
        Screens a market against all 10 candidate archetypes on historical settled wagers.
        If an incumbent model exists, only approves if challenger outperforms by >= 1.5% ROI and positive Brier.
        """
        champ_res = self.trainer.train_and_screen_demoted_market(segment_key)
        if not champ_res or not champ_res.is_qualified:
            return None

        if incumbent_roi is not None:
            passed, reasons = self.trainer.screen_candidate_against_incumbent(
                champ_res,
                incumbent_roi=incumbent_roi,
                incumbent_delta_brier=incumbent_delta_brier or 0.0,
            )
            if not passed:
                log.debug("Challenger for %s failed incumbent screen: %s", segment_key, reasons)
                return None

        # Return discovered candidate credentials
        return {
            'segment_key': segment_key,
            'model_id': champ_res.model_id,
            'model_archetype': champ_res.model_archetype,
            'model_params': champ_res.model_params,
            'historical_roi': champ_res.oos_simulated_roi,
            'historical_brier_delta': champ_res.oos_delta_brier,
            'historical_sample_size': champ_res.oos_sample_size,
            'scorecard_s': champ_res.scorecard_s,
        }
