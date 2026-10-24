"""Demoted Market Incubation Bridge.

Connects the R&D historical model trainer directly to the Shadow Incubator.
Discovers out-of-sample positive-edge candidate models on historical settled wagers,
binds them to demoted market segments, and formally transitions them into the
Shadow Incubator with active model tracking for Crucible re-graduation.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .historical_trainer import HistoricalCandidateTrainer
from .providers import DATA
from .quant_rd_agent import append_rd_log
from .tournament_engine import TournamentEngine, normalize_segment_key


INCUBATOR_DB = DATA / 'shadow_incubator.sqlite3'


class DemotedMarketIncubationBridge:
    """Orchestrates the binding of historically proven candidate models to demoted markets."""

    def __init__(self, data_dir: Path = DATA, db_path: Optional[Path] = None):
        self.data_dir = data_dir
        self.db_path = db_path or (data_dir / 'shadow_incubator.sqlite3')
        self.trainer = HistoricalCandidateTrainer(db_path=self.db_path)
        self.tournament_engine = TournamentEngine(data_dir=data_dir, db_path=self.db_path)

    def synchronize_demoted_markets(self, max_train_per_run: int = 100) -> Dict[str, Any]:
        """Scans all demoted markets, trains candidate models on historical data, and admits them into the incubator."""
        if not self.db_path.exists():
            return {'status': 'no_db', 'newly_admitted_count': 0}

        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, segment_key, league, scope, family, sub_family, rolling_roi, rolling_pnl, games, incubation_phase, incubated_model_id
                FROM demoted_markets
            """)
            all_demoted = cur.fetchall()
        finally:
            conn.close()

        if not all_demoted:
            return {'status': 'no_demoted_markets', 'newly_admitted_count': 0}

        newly_admitted = []
        already_incubating = 0
        still_researching = 0

        for row in all_demoted:
            d_id, seg_key, league, scope, family, sub_fam, r_roi, r_pnl, g_count, phase, model_id = row

            if phase == 'incubating_with_new_model' and model_id:
                already_incubating += 1
                continue

            # Run historical training & out-of-sample Crucible validation
            champ_res = self.trainer.train_and_screen_demoted_market(seg_key)
            if not champ_res or not champ_res.is_qualified:
                still_researching += 1
                continue

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

        return {
            'status': 'success',
            'newly_admitted_count': len(newly_admitted),
            'already_incubating_count': already_incubating,
            'still_researching_count': still_researching,
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
        cls_target = class_map.get(champ_res.model_archetype)
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

        import time
        for attempt in range(5):
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=60.0)
                try:
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
                    conn.commit()
                    break
                finally:
                    conn.close()
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 4:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise

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
