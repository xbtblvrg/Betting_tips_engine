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

from .historical_trainer import CandidateTrainingResult, HistoricalCandidateTrainer
from .providers import DATA
from .quant_rd_agent import append_rd_log
from .tournament_engine import TournamentEngine, normalize_segment_key

log = logging.getLogger(__name__)


INCUBATOR_DB = DATA / 'shadow_incubator.sqlite3'


def is_market_graduated(segment_key: str, data_dir: Optional[Path] = None) -> bool:
    """Checks whether a market segment is officially graduated into Tier 3 live execution."""
    try:
        from .contracts import canonical_segment_aliases
        from .decisions import _get_graduated_segments
        grad_segs = _get_graduated_segments()
        parts = segment_key.split('|')
        lg = parts[0]
        sc = parts[1] if len(parts) > 1 else 'full_game'
        mkt = parts[2] if len(parts) > 2 else ''
        aliases = canonical_segment_aliases(lg, sc, mkt)
        if aliases & grad_segs:
            return True
        from .shadow_market_incubator import load_graduated_markets
        target_dir = data_dir or DATA
        grads = load_graduated_markets(target_dir)
        if any(a in grads for a in aliases):
            return True
        return False
    except Exception:
        return False


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

    def synchronize_demoted_markets(self, max_train_per_run: int = 100) -> Dict[str, Any]:
        """Scans demoted markets, trains candidate models on historical data, and admits them into the incubator."""
        if not self.db_path.exists():
            return {'status': 'no_db', 'newly_admitted_count': 0}

        # Only sync roster if demoted_markets table is empty to avoid expensive full-universe rebuilds
        try:
            with closing(sqlite3.connect(str(self.db_path), timeout=30.0)) as c_chk:
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
                SELECT id, segment_key, league, scope, family, sub_family, rolling_roi, rolling_pnl, games, incubation_phase, incubated_model_id, last_screened_at
                FROM demoted_markets
            """)
            raw_demoted = cur.fetchall()
            from .contracts import get_live_active_leagues, is_dual_reachable_contract, get_sport_priority
            live_active = get_live_active_leagues(self.data_dir)

            def _bridge_sort_key(r):
                d_id, seg_key, lg, sc, fam, sub_fam, r_roi, r_pnl, g_cnt, phase, mid, l_scr = r
                lg_u = str(lg or '').upper().strip()
                is_inc = 1 if (phase == 'incubating_with_new_model' and mid) else 0
                is_live_lg = 0 if lg_u in live_active else 1
                is_dual = 0 if is_dual_reachable_contract(lg_u, sc or 'full_game', fam or '', sub_fam or '') else 1
                never_scr = 0 if not l_scr else 1
                scr_bucket = str(l_scr or '')[:13]
                sp_prio = get_sport_priority(lg_u)
                neg_pnl = 0 if float(r_pnl or 0.0) < 0.0 else 1
                return (is_inc, is_live_lg, is_dual, never_scr, scr_bucket, sp_prio, neg_pnl, str(l_scr or ''), str(d_id))

            raw_demoted.sort(key=_bridge_sort_key)
            all_demoted = [r[:11] for r in raw_demoted]
            log.debug(
                "[BRIDGE_ACTIVE_DUAL_PRIORITY] Sorted %d demoted markets (live_active=%s, top_5=%s)",
                len(all_demoted),
                sorted(live_active),
                [r[1] for r in all_demoted[:5]],
            )
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
        to_batch_admit = []
        if to_screen:
            with ThreadPoolExecutor(max_workers=min(8, len(to_screen))) as pool:
                screened_results = list(pool.map(_screen_archetype_market, to_screen))

            for d_id, seg_key, row, champ_res in screened_results:
                if not champ_res or not champ_res.is_qualified:
                    # Check persistent AI feedback loop memory for pre-synthesized champion
                    try:
                        with closing(sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=5.0)) as fb_conn:
                            fb_row = fb_conn.execute("""
                                SELECT model_id, class_name, oos_sample_size, oos_win_rate, oos_simulated_roi, oos_delta_brier, oos_max_drawdown
                                FROM ai_synthesis_feedback_loop
                                WHERE segment_key = ? 
                                  AND (crucible_passed = 1 OR (oos_sample_size >= 5 AND oos_simulated_roi >= 0.035 AND oos_delta_brier >= -0.005 AND oos_max_drawdown < 0.25))
                                ORDER BY oos_sample_size DESC, oos_simulated_roi DESC
                                LIMIT 1
                            """, (seg_key,)).fetchone()
                            if fb_row:
                                from .historical_trainer import CandidateTrainingResult
                                champ_res = CandidateTrainingResult(
                                    segment_key=seg_key,
                                    model_id=fb_row[0],
                                    model_archetype=fb_row[1],
                                    model_params={'source_file': f"{fb_row[0]}.py"},
                                    train_sample_size=0,
                                    oos_sample_size=fb_row[2] or 0,
                                    oos_win_rate=fb_row[3] or 0.0,
                                    oos_simulated_roi=fb_row[4] or 0.0,
                                    oos_delta_brier=fb_row[5] or 0.0,
                                    oos_max_drawdown=fb_row[6] or 0.0,
                                    is_qualified=True,
                                    scorecard_s=100.0,
                                )
                    except Exception:
                        pass

                if champ_res and champ_res.is_qualified:
                    to_batch_admit.append((d_id, seg_key, champ_res, row))
                else:
                    unresolved.append(row)

            if to_batch_admit:
                items_for_batch = [(d_id, seg_key, c_res) for (d_id, seg_key, c_res, _) in to_batch_admit]
                batch_res = self.batch_admit_champions(items_for_batch)
                admitted_keys = set(batch_res.get('admitted_keys', []))
                for (d_id, seg_key, c_res, orig_row) in to_batch_admit:
                    if seg_key in admitted_keys:
                        newly_admitted.append({
                            'segment_key': seg_key,
                            'model_id': c_res.model_id,
                            'model_archetype': c_res.model_archetype,
                            'historical_roi': c_res.oos_simulated_roi,
                            'historical_brier_delta': c_res.oos_delta_brier,
                            'historical_sample_size': c_res.oos_sample_size,
                        })
                    else:
                        unresolved.append(orig_row)

            # Phase 2: Sequential Controlled AI Model Synthesis for unresolved markets
            # Strictly prioritizes US Majors -> Premier Soccer -> ATP/WTA (and Dual Reachable)
            # Strictly filters out fringe, deprioritized Tier 4 leagues (Challenger, minor NCAA, etc.)
            if unresolved and len(newly_admitted) < max_train_per_run:
                from .contracts import get_sport_priority, is_dual_reachable_contract, is_preferred_alpha_league, get_live_active_leagues
                live_active_p2 = get_live_active_leagues(self.data_dir)

                # Filter to active leagues first, then preferred alpha leagues
                preferred_unresolved = [r for r in unresolved if str(r[2] or '').upper().strip() in live_active_p2 and (is_preferred_alpha_league(r[2]) or str(r[2] or '').upper() in ('NCAAM', 'NCAAW'))]
                if not preferred_unresolved:
                    preferred_unresolved = [r for r in unresolved if is_preferred_alpha_league(r[2])]
                if not preferred_unresolved:
                    preferred_unresolved = unresolved

                def _strategic_priority_key(r):
                    lg = r[2]
                    prio = get_sport_priority(lg)
                    is_dual = is_dual_reachable_contract(lg, r[3], r[4], r[5] if len(r) > 5 else '')
                    dual_rank = 0 if is_dual else 1
                    # Leaky markets with negative rolling PnL (tourniquet turnaround first)
                    pnl_drag = float(r[8] or 0.0) if len(r) > 8 else 0.0
                    return (dual_rank, prio, pnl_drag)

                preferred_unresolved.sort(key=_strategic_priority_key)
                max_ai_syntheses = min(6, max(1, max_train_per_run - len(newly_admitted)), len(preferred_unresolved))
                ai_candidates = preferred_unresolved[:max_ai_syntheses]

                def _bridge_synthesize_target(target_row):
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
                                return {
                                    'segment_key': t_seg_key,
                                    'model_id': ai_champ.model_id,
                                    'model_archetype': ai_champ.model_archetype,
                                    'historical_roi': ai_champ.oos_simulated_roi,
                                    'historical_brier_delta': ai_champ.oos_delta_brier,
                                    'historical_sample_size': ai_champ.oos_sample_size,
                                }
                    except Exception as e_synth:
                        log.debug("Autonomous synthesis on %s: %s", t_seg_key, e_synth)
                    return None

                if ai_candidates:
                    with ThreadPoolExecutor(max_workers=min(4, len(ai_candidates))) as ai_pool:
                        synth_results = list(ai_pool.map(_bridge_synthesize_target, ai_candidates))
                        for sr in synth_results:
                            if sr:
                                newly_admitted.append(sr)

            still_researching = max(0, attempted_this_run - len(newly_admitted))

        return {
            'status': 'success',
            'newly_admitted_count': len(newly_admitted),
            'already_incubating_count': already_incubating,
            'still_researching_count': still_researching,
            'attempted_this_run': attempted_this_run,
            'newly_admitted': newly_admitted,
        }

    def _instantiate_candidate(
        self,
        seg_key: str,
        champ_res: CandidateTrainingResult,
    ) -> Any:
        """Dynamically instantiates candidate model archetype with parameters."""
        parts = seg_key.split('|')
        league = parts[0] if len(parts) > 0 else 'UNKNOWN'
        scope = parts[1] if len(parts) > 1 else 'full_game'
        family = parts[2] if len(parts) > 2 else 'totals'

        # Instantiate model archetype with fitted parameters
        cls_target = None
        if champ_res.model_id:
            candidate_module_names = [champ_res.model_id]
            if champ_res.model_params and isinstance(champ_res.model_params, dict):
                src_id = champ_res.model_params.get('source_model_id')
                if src_id and src_id not in candidate_module_names:
                    candidate_module_names.append(src_id)
            if champ_res.model_id.startswith('dyn_'):
                clean_id = champ_res.model_id.removeprefix('dyn_')
                if clean_id not in candidate_module_names:
                    candidate_module_names.append(clean_id)

            for mod_name in candidate_module_names:
                try:
                    import importlib
                    pkg = __package__ or 'betting_engine'
                    mod = importlib.import_module(f'.sandbox_models.generated.{mod_name}', package=pkg)
                    clean_arch = champ_res.model_archetype.removeprefix('Dynamic_')
                    cls_target = getattr(mod, champ_res.model_archetype, None) or getattr(mod, clean_arch, None)
                    if not cls_target:
                        import inspect
                        for item_name, item in inspect.getmembers(mod, inspect.isclass):
                            if (any(b.__name__ == 'BaseCandidateModel' for b in getattr(item, '__mro__', []))
                                    and item.__name__ != 'BaseCandidateModel'):
                                cls_target = item
                                break
                    if cls_target:
                        break
                except Exception:
                    pass

        if not cls_target:
            class_map = self.tournament_engine._get_class_map()
            clean_arch = champ_res.model_archetype.removeprefix('Dynamic_')
            cls_target = class_map.get(champ_res.model_archetype) or class_map.get(clean_arch)

        if not cls_target:
            log.debug("[BRIDGE_ADMIT] Archetype class '%s' not found for model '%s' on seg '%s'", champ_res.model_archetype, champ_res.model_id, seg_key)
            return None

        model_meta = {
            'params': champ_res.model_params,
            'historical_roi': champ_res.oos_simulated_roi,
            'historical_delta_brier': champ_res.oos_delta_brier,
            'historical_sample_size': champ_res.oos_sample_size,
            'trained_at': datetime.now(timezone.utc).isoformat(),
        }

        clean_params = {
            k: v for k, v in (champ_res.model_params or {}).items()
            if k not in ('source_file', 'provider', 'model_archetype', 'class_name')
        }

        init_kwargs = {
            'model_id': champ_res.model_id,
            'version': champ_res.version,
            'league': league,
            'scope': scope,
            'family': family,
            'metadata': model_meta,
        }
        init_kwargs.update(clean_params)

        import inspect
        try:
            sig = inspect.signature(cls_target.__init__)
            params = sig.parameters
            has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            if not has_var_kwargs:
                filtered_kwargs = {k: v for k, v in init_kwargs.items() if k in params}
            else:
                filtered_kwargs = {k: v for k, v in init_kwargs.items() if k in params or k in ('model_id', 'version', 'league', 'scope', 'family', 'metadata')}
            try:
                return cls_target(**filtered_kwargs)
            except TypeError:
                return cls_target(
                    model_id=champ_res.model_id,
                    version=champ_res.version,
                    league=league,
                    scope=scope,
                    family=family
                )
        except Exception as e_inst:
            log.debug("[BRIDGE_ADMIT] Instantiation failed for '%s' (%s): %s", champ_res.model_id, cls_target, e_inst)
            return None

    def batch_admit_champions(
        self,
        items: List[Tuple[Optional[str], str, CandidateTrainingResult]],
    ) -> Dict[str, Any]:
        """Instantiates, batch-registers in tournament, and commits champion candidate models in bulk."""
        if not items:
            return {'admitted_count': 0, 'failed_count': 0, 'admitted_keys': []}

        # 1. Instantiate candidate models
        candidate_items = []
        valid_records = []
        for d_id, seg_key, champ_res in items:
            inst = self._instantiate_candidate(seg_key, champ_res)
            if inst is not None:
                candidate_items.append((seg_key, inst, None))
                valid_records.append((d_id, seg_key, champ_res, inst))

        if not candidate_items:
            return {'admitted_count': 0, 'failed_count': len(items), 'admitted_keys': []}

        # 2. Batch register in TournamentEngine (single disk write pass)
        reg_results = self.tournament_engine.register_candidates_batch(candidate_items)

        # 3. Filter successful registrations
        to_commit = []
        now_iso = datetime.now(timezone.utc).isoformat()
        admitted_keys = []

        for d_id, seg_key, champ_res, inst in valid_records:
            succ, errs = reg_results.get(seg_key, (False, []))
            if succ:
                params_json = json.dumps(champ_res.model_params or {})
                to_commit.append((
                    champ_res.model_id,
                    champ_res.model_archetype,
                    params_json,
                    float(champ_res.oos_simulated_roi or 0.0),
                    float(champ_res.oos_delta_brier or 0.0),
                    int(champ_res.oos_sample_size or 0),
                    now_iso,
                    seg_key,
                    d_id or '',
                ))
                admitted_keys.append(seg_key)

        # 4. Atomic batch update in SQLite
        if to_commit and self.db_path.exists():
            import sqlite3
            for attempt in range(5):
                try:
                    with closing(sqlite3.connect(str(self.db_path), timeout=60.0)) as conn:
                        conn.execute("PRAGMA busy_timeout = 60000")
                        conn.executemany("""
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
                        """, to_commit)
                        conn.commit()
                        break
                except sqlite3.OperationalError:
                    import time
                    time.sleep(0.5)

        log.info("[BRIDGE_BATCH_ADMIT] Successfully admitted %d/%d champion models into Tier 2 in single batch.", len(admitted_keys), len(items))
        return {
            'admitted_count': len(admitted_keys),
            'failed_count': len(items) - len(admitted_keys),
            'admitted_keys': admitted_keys,
        }

    def admit_champion_to_market(
        self,
        d_id: Optional[str],
        seg_key: str,
        champ_res: CandidateTrainingResult,
    ) -> bool:
        """Instantiates, registers in tournament, and commits champion candidate model to demoted market."""
        candidate_instance = self._instantiate_candidate(seg_key, champ_res)
        if candidate_instance is None:
            return False

        # Register candidate model in TournamentEngine
        canonical = normalize_segment_key(seg_key)
        self.tournament_engine.register_candidate(canonical, candidate_instance)
        log.debug("[BRIDGE_ADMIT_REGISTER] Candidate registered in TournamentEngine: '%s' for '%s'", champ_res.model_id, canonical)
        log.debug("[BRIDGE_ADMIT_REGISTER] Candidate registered in TournamentEngine: '%s' for '%s'", champ_res.model_id, canonical)

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
                    log.debug("[BRIDGE_ADMIT_COMMIT] Committed candidate model '%s' (%s) for '%s' to demoted_markets Tier 2",
                              champ_res.model_id, champ_res.model_archetype, seg_key)
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

    def admit_unadmitted_candidates(self) -> Dict[str, Any]:
        """Fast (<0.1s) non-blocking admission of already-verified candidate models from feedback loop into Tier 2."""
        if not self.db_path.exists():
            return {'admitted_count': 0, 'newly_admitted': []}
        unadmitted_candidates = []
        try:
            with closing(sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=5.0)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT dm.id, dm.segment_key, fb.model_id, fb.class_name,
                           fb.oos_sample_size, fb.oos_win_rate, fb.oos_simulated_roi,
                           fb.oos_delta_brier, fb.oos_max_drawdown
                    FROM demoted_markets dm
                    JOIN ai_synthesis_feedback_loop fb
                      ON fb.segment_key = dm.segment_key
                      OR fb.segment_key = (dm.league || '|' || dm.scope || '|' || COALESCE(dm.sub_family, dm.family))
                    WHERE dm.incubation_phase = 'research_training'
                      AND (fb.crucible_passed = 1 OR (fb.oos_sample_size >= 5 AND fb.oos_simulated_roi >= 0.035 AND fb.oos_delta_brier >= -0.005))
                    ORDER BY fb.crucible_passed DESC, fb.oos_simulated_roi DESC
                    LIMIT 50
                """)
                rows = cur.fetchall()
                seen_segs = set()
                models_dir = self.data_dir.parent / "src" / "betting_engine" / "sandbox_models" / "generated"
                for d_id, seg_key, mid, cname, oos_n, oos_wr, oos_roi, oos_br, oos_dd in rows:
                    if seg_key in seen_segs:
                        continue
                    if (models_dir / f"{mid}.py").exists():
                        seen_segs.add(seg_key)
                        cand = CandidateTrainingResult(
                            segment_key=seg_key,
                            model_id=mid,
                            model_archetype=cname,
                            model_params={'source_file': f"{mid}.py"},
                            train_sample_size=0,
                            oos_sample_size=oos_n or 0,
                            oos_win_rate=oos_wr or 0.0,
                            oos_simulated_roi=oos_roi or 0.0,
                            oos_delta_brier=oos_br or 0.0,
                            oos_max_drawdown=oos_dd or 0.0,
                            is_qualified=True,
                            scorecard_s=100.0,
                        )
                        unadmitted_candidates.append((d_id, seg_key, cand))
        except Exception:
            return {'admitted_count': 0, 'newly_admitted': []}

        if not unadmitted_candidates:
            return {'admitted_count': 0, 'newly_admitted': []}
        return self.batch_admit_champions(unadmitted_candidates)
