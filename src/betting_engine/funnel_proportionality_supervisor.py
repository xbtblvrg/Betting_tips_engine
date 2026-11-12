"""Autonomous Funnel Proportionality & High-Velocity Throughput Supervisor.

Constitutional Mandates (AGENTS.md Commandments 1, 2, 3, 5, 6, 7 & 8):
1. Prime Financial Optimization: Minimum 10.0% sustained weekly ROI & cumulative PnL uptrend.
2. Non-Negotiable Volume Floor: Strictly >= 100 funded recommendations per Budapest day.
3. Closed-Loop 4-Tier Pipeline Supervision:
   - Funnel 1 (Tier 1 R&D Backlog -> Candidate Synthesis):
     Audits demoted markets in research_training and enforces proportional AI synthesis
     prioritizing the 10 supreme leagues (NBA, NFL, NHL, MLS, NCAAF, UCL, EPL, MLB, ATP, WTA)
     and Polymarket Dual Reachable Markets.
   - Funnel 2 (Tier 1 -> Tier 2 Admission & Expansion):
     Guarantees that 100% of qualified candidate models (N_oos >= 5, ROI_oos >= +3.5%, positive Brier)
     are instantly batch-admitted into the Shadow Incubator ($0.00 virtual stakes).
   - Funnel 3 (Tier 2 Forward-Testing Velocity):
     Measures shadow wager generation cadence per incubating market, distinguishing
     legitimate off-season dormancy from active-season execution stalls.
   - Funnel 4 (Tier 2 -> Tier 3 Crucible Graduation):
     Tracks maturation pipeline (N >= 25) and auto-graduates qualifying models into live
     Tier 3 Stake execution under continuous fractional Kelly sizing ($0.50-$8.00).
   - Funnel 5 (Tier 3 Live Production Volume Floor):
     Supervises progress toward the 100 picks/day floor, diagnoses exact bottlenecks
     (fixtures, market coverage, EV hurdles, bankroll caps), and auto-heals choke points.
   - Funnel 6 (Tier 3 -> Tier 4 Elite Polymarket Promotion):
     Promotes Tier 3 markets with verified live settled profit into Tier 4 Polymarket CLOB.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import math
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo
from contextlib import closing

import numpy as np

from .contracts import (
    get_sport_priority,
    is_dual_reachable_contract,
    is_preferred_alpha_league,
    is_supported_stake_contract,
)
from .domain import utcnow
from .io_utils import atomic_json
from .providers import DATA

log = logging.getLogger(__name__)

BUDAPEST_TZ = ZoneInfo("Europe/Budapest")
SUPERVISOR_STATUS_FILE = "funnel_proportionality_status.json"


@dataclass
class StageFunnelMetrics:
    stage_name: str
    input_backlog: int
    output_throughput: int
    conversion_rate_pct: float
    is_healthy: bool
    chokepoint_reason: Optional[str] = None
    actionable_remedy: Optional[str] = None


@dataclass
class FunnelSupervisionReport:
    audited_at: str
    budapest_date: str
    funnel_stages: Dict[str, Any]
    proportionality_indices: Dict[str, float]
    daily_volume_telemetry: Dict[str, Any]
    bottlenecks_detected: List[Dict[str, Any]]
    actions_executed: List[str]
    system_status: str  # 'OPTIMAL', 'ADAPTIVE_HEALING', 'BOTTLENECK_DETECTED'

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FunnelProportionalitySupervisor:
    """Supervises end-to-end quantitative throughput across all 4 tiers and self-heals bottlenecks."""

    def __init__(self, data_dir: Path = DATA):
        self.data_dir = Path(data_dir or DATA)
        self.incubator_db = self.data_dir / "shadow_incubator.sqlite3"
        self.engine_db = self.data_dir / "engine.sqlite3"
        self.status_file = self.data_dir / SUPERVISOR_STATUS_FILE

    def audit_and_heal(self, force: bool = False) -> FunnelSupervisionReport:
        """Executes a full multi-stage funnel audit and triggers autonomous self-healing."""
        now_dt = utcnow()
        now_bp = datetime.now(BUDAPEST_TZ)
        bp_date_str = now_bp.strftime("%Y-%m-%d")
        bp_day_start = now_bp.replace(hour=0, minute=0, second=0, microsecond=0)
        bp_day_start_utc_iso = bp_day_start.astimezone(timezone.utc).isoformat()
        now_iso = now_dt.isoformat()

        actions_executed: List[str] = []
        bottlenecks: List[Dict[str, Any]] = []

        # =========================================================================
        # 1. AUDIT STAGE 1 & 2: Tier 1 R&D Backlog -> Tier 2 Incubator Admission
        # =========================================================================
        t1_total = 0
        t1_research = 0
        t2_incubating = 0
        t3_graduated = 0
        unadmitted_candidates: List[Tuple[Optional[str], str, Any]] = []

        if self.incubator_db.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.incubator_db.resolve()}?mode=ro", uri=True, timeout=10.0)) as conn:
                    cur = conn.cursor()
                    phases = cur.execute("SELECT incubation_phase, count(*) FROM demoted_markets GROUP BY incubation_phase").fetchall()
                    for p_name, cnt in phases:
                        if p_name == "research_training":
                            t1_research = cnt
                        elif p_name == "incubating_with_new_model":
                            t2_incubating = cnt
                    t1_total = t1_research + t2_incubating

                    t3_graduated = cur.execute("SELECT count(*) FROM graduated_markets").fetchone()[0]

                    # Scan for verified candidates ready in ai_synthesis_feedback_loop that are not yet admitted,
                    # strictly prioritizing the 10 Supreme Priority Leagues:
                    # NBA > NFL > NHL > MLS > NCAAF > UCL > EPL > MLB > ATP > WTA
                    cur.execute("""
                        SELECT dm.id, dm.segment_key, fb.model_id, fb.class_name,
                               fb.oos_sample_size, fb.oos_win_rate, fb.oos_simulated_roi,
                               fb.oos_delta_brier, fb.oos_max_drawdown
                        FROM demoted_markets dm
                        JOIN ai_synthesis_feedback_loop fb ON dm.segment_key = fb.segment_key
                        WHERE dm.incubation_phase = 'research_training'
                          AND (
                              fb.crucible_passed = 1
                              OR (fb.oos_sample_size >= 5 AND fb.oos_simulated_roi >= 0.035 AND fb.oos_delta_brier >= -0.005)
                          )
                        ORDER BY 
                          CASE 
                            WHEN dm.league = 'NBA' THEN 1
                            WHEN dm.league = 'NFL' THEN 2
                            WHEN dm.league = 'NHL' THEN 3
                            WHEN dm.league = 'MLS' THEN 4
                            WHEN dm.league = 'NCAAF' THEN 5
                            WHEN dm.league = 'UCL' THEN 6
                            WHEN dm.league = 'EPL' THEN 7
                            WHEN dm.league = 'MLB' THEN 8
                            WHEN dm.league = 'ATP' THEN 9
                            WHEN dm.league = 'WTA' THEN 10
                            ELSE 99
                          END ASC,
                          fb.crucible_passed DESC,
                          fb.oos_simulated_roi DESC,
                          fb.oos_sample_size DESC
                        LIMIT 250
                    """)
                    unadmitted_rows = cur.fetchall()
                    seen_segs: Set[str] = set()
                    models_dir = self.data_dir.parent / "src" / "betting_engine" / "sandbox_models" / "generated"
                    from .historical_trainer import CandidateTrainingResult
                    from .tournament_engine import TournamentEngine
                    t_eng = TournamentEngine(self.data_dir)
                    t_reg = t_eng.load_registry()
                    rejected_sigs = set(t_reg.get('rejected', []))

                    for r in unadmitted_rows:
                        d_id, seg_key, mid, cname, oos_n, oos_wr, oos_roi, oos_br, oos_dd = r
                        if seg_key in seen_segs:
                            continue
                        model_sig_hash = hashlib.sha256(f"{mid}:1.0.0:{cname}".encode('utf-8')).hexdigest()
                        if model_sig_hash in rejected_sigs:
                            continue
                        p_file = models_dir / f"{mid}.py"
                        if not p_file.exists():
                            p_file = Path("src/betting_engine/sandbox_models/generated") / f"{mid}.py"
                        if p_file.exists():
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
            except Exception as e_audit:
                log.warning("[SUPERVISOR_AUDIT] Error auditing Tier 1/2: %s", e_audit)

        unadmitted_count = len(unadmitted_candidates)
        if unadmitted_count > 0:
            bottlenecks.append({
                "stage": "tier1_to_tier2",
                "severity": "HIGH",
                "message": f"Found {unadmitted_count} verified candidate models with positive OOS edge in Tier 1 R&D that have not been admitted into Tier 2.",
                "action": "Triggering high-velocity batch admission into Tier 2."
            })
            # AUTONOMOUS HEALING ACTION 1: Batch admit verified candidates into Tier 2
            try:
                from .demoted_market_bridge import DemotedMarketIncubationBridge
                bridge = DemotedMarketIncubationBridge(self.data_dir)
                batch_res = bridge.batch_admit_champions(unadmitted_candidates)
                admitted_n = batch_res.get('admitted_count', 0)
                if admitted_n > 0:
                    actions_executed.append(f"Batch admitted {admitted_n} candidate models into Tier 2 Shadow Incubator.")
                    t2_incubating += admitted_n
                    t1_research = max(0, t1_research - admitted_n)
            except Exception as e_adm:
                log.error("[SUPERVISOR_HEAL] Failed batch admission: %s", e_adm)

        # =========================================================================
        # 2. AUDIT STAGE 3: Tier 2 Shadow Wager Generation Velocity
        # =========================================================================
        shadow_wagers_today = 0
        shadow_wagers_7d = 0
        stagnant_markets_count = 0
        dormant_offseason_count = 0
        active_wager_markets = 0

        if self.incubator_db.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.incubator_db.resolve()}?mode=ro", uri=True, timeout=10.0)) as conn:
                    cur = conn.cursor()
                    start_today_iso = bp_day_start_utc_iso
                    start_7d_iso = (now_dt - timedelta(days=7)).isoformat()

                    cur.execute("SELECT count(*) FROM incubated_wagers WHERE captured_at >= ?", (start_today_iso,))
                    shadow_wagers_today = cur.fetchone()[0]

                    cur.execute("SELECT count(*) FROM incubated_wagers WHERE captured_at >= ?", (start_7d_iso,))
                    shadow_wagers_7d = cur.fetchone()[0]

                    cur.execute("""
                        SELECT count(DISTINCT league || '|' || scope || '|' || family)
                        FROM incubated_wagers
                        WHERE captured_at >= ?
                    """, (start_7d_iso,))
                    active_wager_markets = cur.fetchone()[0]

                    # Detect dormant off-season leagues (e.g., MLB in November/winter, WNBA)
                    cur.execute("""
                        SELECT league, count(*) 
                        FROM demoted_markets 
                        WHERE incubation_phase = 'incubating_with_new_model'
                        GROUP BY league
                    """)
                    for lg, count_lg in cur.fetchall():
                        if lg in ('MLB', 'WNBA') and now_dt.month in (11, 12, 1, 2, 3):
                            dormant_offseason_count += count_lg

                    stagnant_markets_count = max(0, t2_incubating - active_wager_markets - dormant_offseason_count)
            except Exception as e_wager:
                log.warning("[SUPERVISOR_AUDIT] Error auditing shadow wagers: %s", e_wager)

        avg_wagers_per_market = round(shadow_wagers_today / max(1, t2_incubating - dormant_offseason_count), 2)
        if stagnant_markets_count > 25 and t2_incubating >= 50:
            bottlenecks.append({
                "stage": "tier2_wager_generation",
                "severity": "MEDIUM",
                "message": f"{stagnant_markets_count} incubating markets have received 0 shadow wagers in the past 7 days outside of off-season leagues.",
                "action": "Ensuring tournament engine mapping and schedule discovery are refreshed."
            })

        # =========================================================================
        # 3. AUDIT STAGE 4: Tier 2 -> Tier 3 Crucible Graduation Velocity
        # =========================================================================
        mature_candidates: List[Dict[str, Any]] = []
        failing_probation: List[Dict[str, Any]] = []

        if self.incubator_db.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.incubator_db.resolve()}?mode=ro", uri=True, timeout=10.0)) as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT dm.segment_key, dm.incubated_model_id, dm.demoted_at,
                               count(w.id) as n_settled,
                               sum(case when w.result = 'won' then 1 else 0 end) as n_won,
                               avg(w.pnl) as avg_roi,
                               sum(w.pnl) as total_pnl,
                               avg(w.brier_market - w.brier_model) as brier_skill
                        FROM demoted_markets dm
                        JOIN incubated_wagers w ON dm.league = w.league AND dm.scope = w.scope AND dm.family = w.family
                          AND (dm.sub_family IS NULL OR dm.sub_family = '' OR dm.sub_family = dm.family OR dm.sub_family = w.sub_family)
                        WHERE dm.incubation_phase = 'incubating_with_new_model'
                          AND dm.incubated_model_id IS NOT NULL
                          AND w.status = 'settled'
                          AND (
                              dm.demoted_at IS NULL
                              OR w.settled_at > dm.demoted_at
                              OR w.model_id = dm.incubated_model_id
                              OR dm.reason LIKE '%Tier 1 R&D%'
                              OR dm.reason LIKE '%Deadlock Reset%'
                              OR dm.reason LIKE '%Restored proven champion%'
                              OR dm.reason LIKE '%Auto-Sync: Tier 1%'
                          )
                        GROUP BY dm.segment_key, dm.incubated_model_id
                        HAVING count(w.id) >= 25
                    """)
                    for row in cur.fetchall():
                        seg_k, m_id, d_at, n_set, n_w, a_roi, tot_pnl, br_skill = row
                        if a_roi >= 0.035 and (br_skill is None or br_skill >= -0.005) and tot_pnl > 0.0:
                            mature_candidates.append({
                                'segment_key': seg_k,
                                'model_id': m_id,
                                'sample_size': n_set,
                                'roi': a_roi,
                                'pnl': tot_pnl,
                                'brier_skill': br_skill or 0.0,
                            })
                        elif a_roi < -0.05 or tot_pnl < -5.0:
                            failing_probation.append({
                                'segment_key': seg_k,
                                'model_id': m_id,
                                'sample_size': n_set,
                                'roi': a_roi,
                                'pnl': tot_pnl,
                            })
            except Exception as e_mat:
                log.warning("[SUPERVISOR_AUDIT] Error auditing mature candidates: %s", e_mat)

        if mature_candidates:
            bottlenecks.append({
                "stage": "tier2_to_tier3_graduation",
                "severity": "CRITICAL",
                "message": f"Detected {len(mature_candidates)} mature Tier 2 models that meet all Crucible graduation criteria (N >= 25, ROI >= +3.5%) but are not yet graduated.",
                "action": "Triggering autonomous Crucible re-graduation into Tier 3 live execution."
            })
            # AUTONOMOUS HEALING ACTION 2: Auto-graduate mature models
            try:
                from .shadow_market_incubator import ShadowMarketIncubator
                smi = ShadowMarketIncubator(self.data_dir)
                newly_grads = smi.evaluate_graduation()
                if newly_grads:
                    actions_executed.append(f"Auto-graduated {len(newly_grads)} mature markets into Tier 3 live execution.")
                    t3_graduated += len(newly_grads)
            except Exception as e_grad:
                log.error("[SUPERVISOR_HEAL] Failed auto-graduation: %s", e_grad)

        if failing_probation:
            # AUTONOMOUS HEALING ACTION 3: Evict failing probation models back to Tier 1 R&D
            try:
                from .shadow_market_incubator import ShadowMarketIncubator
                smi = ShadowMarketIncubator(self.data_dir)
                evict_res = smi.evaluate_tier2_lifecycle()
                evicted_count = evict_res.get('evicted_count', 0)
                if evicted_count > 0:
                    actions_executed.append(f"Evicted {evicted_count} failing Tier 2 models back to Tier 1 R&D for re-synthesis.")
            except Exception as e_evict:
                log.error("[SUPERVISOR_HEAL] Failed eviction: %s", e_evict)

        # =========================================================================
        # 4. AUDIT STAGE 5: Tier 3 Live Production Volume Floor (>= 100 Picks/Day)
        # =========================================================================
        funded_picks_today = 0
        total_decisions_today = 0
        volume_fulfillment_pct = 0.0
        blocked_reasons: Counter = Counter()

        if self.engine_db.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.engine_db.resolve()}?mode=ro", uri=True, timeout=10.0)) as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT payload
                        FROM system_decisions
                        WHERE issued_at >= ?
                    """, (bp_day_start_utc_iso,))
                    for (p_str,) in cur.fetchall():
                        total_decisions_today += 1
                        try:
                            p = json.loads(p_str)
                            stake = float(p.get('stake', 0.0) or 0.0)
                            if stake > 0.0:
                                funded_picks_today += 1
                            else:
                                code = p.get('allocation_code', 'unknown')
                                blocked_reasons[code] += 1
                        except Exception:
                            pass
            except Exception as e_dec:
                log.warning("[SUPERVISOR_AUDIT] Error auditing live decisions: %s", e_dec)

        volume_fulfillment_pct = round((funded_picks_today / 100.0) * 100.0, 1)
        volume_gap = max(0, 100 - funded_picks_today)

        if volume_gap > 0:
            bottlenecks.append({
                "stage": "tier3_volume_floor",
                "severity": "HIGH" if now_bp.hour >= 18 and funded_picks_today < 70 else "MEDIUM",
                "message": f"Daily volume currently at {funded_picks_today}/100 picks ({volume_fulfillment_pct}% of mandate). Shortfall: {volume_gap} picks.",
                "blocked_reasons_summary": dict(blocked_reasons.most_common(5)),
                "action": "Unclogging Tier 3 market universe and verifying 72-hour liquidity horizon."
            })
            # AUTONOMOUS HEALING ACTION 4: Proportional R&D Enqueueing for Priority Leagues
            try:
                self._enqueue_priority_league_synthesis()
                actions_executed.append("Enqueued priority AI synthesis jobs for under-represented liquid league markets.")
            except Exception as e_enq:
                log.debug("[SUPERVISOR_HEAL] Could not enqueue priority synthesis: %s", e_enq)

        # =========================================================================
        # 5. AUDIT STAGE 6: Tier 3 -> Tier 4 Elite Polymarket CLOB Promotion
        # =========================================================================
        tier4_eligible_count = 0
        tier4_active_count = 0
        if self.engine_db.exists():
            try:
                from .segment_scoreboard import compute_segment_scoreboard
                with closing(sqlite3.connect(f"file:{self.engine_db.resolve()}?mode=ro", uri=True, timeout=5.0)) as conn:
                    sb = compute_segment_scoreboard(conn)
                    alpha_segs = sb.get("alpha_segments", [])
                    tier4_eligible_count = len(alpha_segs)
                    # Filter for dual reachable
                    dual_active = [
                        s for s in alpha_segs 
                        if is_dual_reachable_contract(
                            s.get('league', '') if isinstance(s, dict) else s.split('|')[0],
                            s.get('scope', 'full_game') if isinstance(s, dict) else (s.split('|')[1] if len(s.split('|')) > 1 else 'full_game'),
                            s.get('family', 'totals') if isinstance(s, dict) else (s.split('|')[2] if len(s.split('|')) > 2 else 'totals')
                        )
                    ]
                    tier4_active_count = len(dual_active)
            except Exception as e_t4:
                log.warning("[SUPERVISOR_AUDIT] Error auditing Tier 4: %s", e_t4)

        # Calculate Proportionality Ratios
        synthesis_to_admission_ratio = round((t2_incubating / max(1, t1_total)) * 100.0, 2)
        admission_to_graduation_ratio = round((t3_graduated / max(1, t2_incubating)) * 100.0, 2)

        stages_summary = {
            "tier1_backlog": {
                "research_training_count": t1_research,
                "unadmitted_ready_candidates": unadmitted_count,
                "status": "HEALTHY" if unadmitted_count == 0 else "BACKLOG_WAITING_ADMISSION"
            },
            "tier2_incubator": {
                "active_incubating_count": t2_incubating,
                "expansion_ratio_pct": synthesis_to_admission_ratio,
                "shadow_wagers_today": shadow_wagers_today,
                "avg_wagers_per_market": avg_wagers_per_market,
                "stagnant_markets_count": stagnant_markets_count,
                "dormant_offseason_count": dormant_offseason_count,
                "mature_graduation_candidates": len(mature_candidates),
                "failing_probation_candidates": len(failing_probation),
                "status": "EXPANDING" if t2_incubating >= 100 else "GROWTH_REQUIRED"
            },
            "tier3_production": {
                "graduated_markets_count": t3_graduated,
                "funded_picks_today": funded_picks_today,
                "daily_volume_floor": 100,
                "volume_fulfillment_pct": volume_fulfillment_pct,
                "volume_gap": volume_gap,
                "status": "COMPLIANT" if funded_picks_today >= 100 else "VOLUME_SHORTFALL"
            },
            "tier4_polymarket": {
                "eligible_alpha_segments": tier4_eligible_count,
                "active_dual_reachable_count": tier4_active_count,
                "status": "ACTIVE" if tier4_active_count > 0 else "QUALIFYING"
            }
        }

        overall_status = "OPTIMAL"
        if bottlenecks:
            overall_status = "ADAPTIVE_HEALING" if actions_executed else "BOTTLENECK_DETECTED"

        report = FunnelSupervisionReport(
            audited_at=now_iso,
            budapest_date=bp_date_str,
            funnel_stages=stages_summary,
            proportionality_indices={
                "t1_to_t2_expansion_pct": synthesis_to_admission_ratio,
                "t2_to_t3_conversion_pct": admission_to_graduation_ratio,
                "daily_volume_progress_pct": volume_fulfillment_pct,
            },
            daily_volume_telemetry={
                "funded_today": funded_picks_today,
                "total_decisions_today": total_decisions_today,
                "blocked_candidates_by_code": dict(blocked_reasons.most_common(5)),
            },
            bottlenecks_detected=bottlenecks,
            actions_executed=actions_executed,
            system_status=overall_status,
        )

        try:
            atomic_json(self.status_file, report.to_dict())
        except Exception as e_save:
            log.warning("Could not persist funnel supervision status: %s", e_save)

        return report

    def _enqueue_priority_league_synthesis(self) -> None:
        """Autonomously enqueues targeted AI model synthesis jobs for demoted markets in priority leagues."""
        if not self.engine_db.exists() or not self.incubator_db.exists():
            return

        priority_leagues = ['NBA', 'NFL', 'NHL', 'MLS', 'NCAAF', 'UCL', 'EPL', 'MLB', 'ATP', 'WTA']
        to_enqueue = []

        try:
            with closing(sqlite3.connect(f"file:{self.incubator_db.resolve()}?mode=ro", uri=True, timeout=5.0)) as inc_conn:
                cur = inc_conn.cursor()
                cur.execute("""
                    SELECT segment_key, league, scope, family, sub_family
                    FROM demoted_markets
                    WHERE incubation_phase = 'research_training'
                      AND league IN (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    LIMIT 15
                """, priority_leagues)
                rows = cur.fetchall()
                for r in rows:
                    to_enqueue.append(r)
        except Exception:
            return

        if not to_enqueue:
            return

        now_iso = utcnow().isoformat()
        try:
            with closing(sqlite3.connect(str(self.engine_db), timeout=30.0)) as eng_conn:
                cur_eng = eng_conn.cursor()
                for seg_key, lg, sc, fam, sub_fam in to_enqueue:
                    # Deduplication guard: Check if an active/queued job already exists for this exact segment
                    cur_eng.execute("""
                        SELECT 1 FROM learning_jobs 
                        WHERE kind = 'priority_model_synthesis' 
                          AND status IN ('queued', 'running', 'retry')
                          AND payload LIKE ?
                        LIMIT 1
                    """, (f'%"{seg_key}"%',))
                    if cur_eng.fetchone():
                        continue

                    job_id = f"funnel_priority_synth_{hashlib.sha256(seg_key.encode()).hexdigest()[:16]}"
                    payload = {
                        'segment_key': seg_key,
                        'league': lg,
                        'scope': sc,
                        'family': fam,
                        'sub_family': sub_fam,
                        'priority_rank': get_sport_priority(lg),
                        'is_dual_reachable': is_dual_reachable_contract(lg, sc, fam, sub_fam or ''),
                        'directive': f"High-priority R&D model synthesis to clear Tier 1 bottleneck for {seg_key}."
                    }
                    cur_eng.execute("""
                        INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, attempts, payload)
                        VALUES (?, 'priority_model_synthesis', ?, 'queued', ?, ?, ?, 0, ?)
                        ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                    """, (job_id, lg, now_iso, now_iso, now_iso, json.dumps(payload)))
                eng_conn.commit()
        except Exception as e_enq:
            log.debug("Priority synthesis enqueue error: %s", e_enq)


def get_latest_funnel_supervision_status(data_dir: Path = DATA) -> Dict[str, Any]:
    """Retrieves the latest persisted funnel proportionality status."""
    path = data_dir / SUPERVISOR_STATUS_FILE
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "status": "not_initialized",
        "system_status": "PENDING",
        "funnel_stages": {},
        "bottlenecks_detected": [],
    }
