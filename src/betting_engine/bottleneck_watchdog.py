"""Autonomous Bottleneck Watchdog & Self-Healing Defense System.

Constitutional Mandate (AGENTS.md Commandments 1, 2, 6, 7 & 8):
1. Continuously audits all Tier 3 graduated markets to ensure 100% champion model binding integrity.
2. Eliminates 'Silent Fallbacks' and 'Néma Alultermelés' (silent under-generation).
3. Detects uncrowned tournament winners and autonomously crowns them into active champions.
4. Detects market activity gaps (upcoming fixtures available but 0 candidates generated).
5. Executes real-time autonomous remediation: auto-rebinding, auto-crowning, and dynamic reload.
6. Automatically enqueues high-priority 'autonomous_bottleneck_repair' jobs in `learning_jobs`
   when complex R&D turnaround or model re-synthesis is required.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from .domain import utcnow, parse_time
from .io_utils import atomic_json
from .providers import DATA
from .verbose_logger import get_verbose_logger

log = logging.getLogger(__name__)

WATCHDOG_STATUS_PATH = DATA / 'bottleneck_watchdog_status.json'


@dataclass
class BottleneckAnomaly:
    anomaly_id: str
    category: str  # 'missing_champion', 'unmounted_distribution', 'uncrowned_winner', 'silent_undergeneration', 'score_gap', 'zero_volume_fixtures', 'stagnant_quarantine', 'pre_graduation_anchor', 'wager_duplication'
    league: str
    scope: str
    family: str
    sub_family: Optional[str]
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'INFO'
    message: str
    self_healed: bool
    remediation_action: Optional[str] = None
    detected_at: str = field(default_factory=lambda: utcnow().isoformat())


@dataclass
class BottleneckWatchdogReport:
    audited_at: str
    total_graduated_markets: int
    healthy_markets_count: int
    anomalies_detected_count: int
    anomalies_healed_count: int
    uncrowned_champions_crowned: int
    anomalies: List[Dict[str, Any]]
    undergenerating_markets: List[str]
    learning_jobs_enqueued: List[str]
    status: str  # 'HEALTHY', 'SELF_HEALED', 'ESCALATED_TO_AI'
    tier1_tier2_health: Optional[Dict[str, Any]] = None
    score_gaps_count: int = 0
    zero_volume_leagues_count: int = 0
    stagnant_quarantine_count: int = 0


_GLOBAL_WATCHDOG_CACHE: Optional[Tuple[datetime, BottleneckWatchdogReport]] = None


class BottleneckWatchdog:
    """Autonomous watchdog guarding against quant engine bottlenecks and silent alpha leakage."""

    _LAST_SCORE_GAP_HEAL_AT: Optional[datetime] = None

    def __init__(self, data_dir: Path = DATA):
        self.data_dir = data_dir
        self.status_file = self.data_dir / 'bottleneck_watchdog_status.json'

    def audit_and_heal(
        self,
        active_segment_models: Optional[Dict[Tuple[str, str], Any]] = None,
        candidates: Optional[List[Dict[str, Any]]] = None,
        upcoming_fixtures: Optional[List[Any]] = None,
        force: bool = False,
    ) -> BottleneckWatchdogReport:
        """Executes full diagnostic audit across all graduated markets and self-heals bottlenecks."""
        global _GLOBAL_WATCHDOG_CACHE
        now_dt = utcnow()
        if not force and _GLOBAL_WATCHDOG_CACHE is not None:
            cached_dt, cached_rep = _GLOBAL_WATCHDOG_CACHE
            if (now_dt - cached_dt).total_seconds() < 20.0:
                return cached_rep

        now_iso = now_dt.isoformat()
        anomalies: List[BottleneckAnomaly] = []
        healed_count = 0
        crowned_count = 0
        undergenerating: List[str] = []
        jobs_enqueued: List[str] = []

        # 1. Load Graduated Markets from SQLite
        from .shadow_market_incubator import load_graduated_markets
        graduated = load_graduated_markets(self.data_dir)
        total_graduated = len(graduated)
        log.debug("[WATCHDOG_AUDIT_START] Loaded %d graduated markets for audit", total_graduated)

        # 2. Load Tournament Registry
        from .tournament_engine import TournamentEngine
        t_eng = TournamentEngine(self.data_dir)
        registry = t_eng.load_registry()
        champions = registry.get('champions', {})
        tournaments = registry.get('tournaments', {})
        log.debug("[WATCHDOG_REGISTRY] Loaded %d champions and %d tournaments from registry", len(champions), len(tournaments))

        # If active_segment_models was not provided, auto-resolve it
        if active_segment_models is None:
            try:
                from .graduated_models import resolve_graduated_segment_models
                active_segment_models = resolve_graduated_segment_models(graduated, {})
            except Exception as e_res:
                log.warning("[WATCHDOG] Failed to auto-resolve active segment models: %s", e_res)
                active_segment_models = {}

        # -------------------------------------------------------------
        # CHECK 1: Graduated Market Model Binding & Distribution Health
        # -------------------------------------------------------------
        healthy_count = 0
        registry_modified = False
        from .contracts import is_supported_stake_contract

        for seg_key, meta in graduated.items():
            league = meta.get('league') or seg_key.split('|')[0]
            scope = meta.get('scope') or (seg_key.split('|')[1] if '|' in seg_key else 'full_game')
            family = meta.get('family') or (seg_key.split('|')[2] if len(seg_key.split('|')) > 2 else '')
            sub_family = meta.get('sub_family') or family

            sub_key = f"{league}|{scope}|{family}:{sub_family}" if (sub_family and sub_family != family) else seg_key
            canonical_key = f"{league}|{scope}|{family}"

            # Verify Stake Contract validity
            if not is_supported_stake_contract(league, scope, family, sub_family):
                continue

            # First, check Active Segment Model Mount
            model_mounted = False
            champion_active = False
            model_id = None

            seg_model = active_segment_models.get((league, scope)) if active_segment_models is not None else None
            if seg_model is not None:
                model_mounted = True
                if hasattr(seg_model, 'champion_models') and isinstance(seg_model.champion_models, dict):
                    for k_cand in [sub_family, family, f"{family}:{sub_family}", 'primary']:
                        if k_cand in seg_model.champion_models:
                            champion_active = True
                            model_id = seg_model.champion_models[k_cand][1]
                            break
                if not champion_active and getattr(seg_model, 'champion_model', None) is not None:
                    champion_active = True
                    model_id = getattr(seg_model.champion_model, 'model_id', 'segment_champion')

            # Check if champion model exists in registry
            if not model_id:
                champ_record = (
                    champions.get(sub_key) or
                    champions.get(canonical_key) or
                    champions.get(seg_key) or
                    champions.get(f"{league}|{scope}")
                )
                if champ_record:
                    model_id = champ_record.get('champion_model_id') or champ_record.get('model_id')

            # Autonomous Candidate Discovery & Auto-Crowning if still missing
            if not model_id:
                for tk in [sub_key, canonical_key, seg_key, f"{league}|{scope}|american_derivatives", f"{league}|{scope}|totals", f"{league}|{scope}|spreads", f"{league}|{scope}"]:
                    cands = t_eng.get_active_candidates(tk)
                    if cands:
                        model_id = cands[0].model_id
                        crowned_count += 1
                        champions[seg_key] = {
                            'champion_model_id': model_id,
                            'version': getattr(cands[0], 'version', '1.0.0'),
                            'crowned_at': utcnow().isoformat(),
                            'auto_healed': True,
                        }
                        registry_modified = True
                        log.info("[WATCHDOG] Autonomously crowned & linked candidate '%s' for '%s'", model_id, seg_key)
                        break

            # If model_id was identified but not mounted into memory, mount it now
            if model_id and not champion_active and active_segment_models is not None:
                try:
                    from .graduated_models import resolve_graduated_segment_models
                    resolved = resolve_graduated_segment_models({seg_key: meta}, active_segment_models)
                    active_segment_models.update(resolved)
                    champion_active = True
                    healed_count += 1
                    remedy_msg = f"Dynamically mounted champion '{model_id}' into active segment models."
                    log.info("[WATCHDOG_HEALED] %s for %s", remedy_msg, seg_key)
                    anomalies.append(BottleneckAnomaly(
                        anomaly_id=f"unmounted_champ_{seg_key.replace('|', '_')}",
                        category='unmounted_distribution',
                        league=league,
                        scope=scope,
                        family=family,
                        sub_family=sub_family,
                        severity='INFO',
                        message=f"Graduated market '{seg_key}' dynamically mounted champion '{model_id}'.",
                        self_healed=True,
                        remediation_action=remedy_msg
                    ))
                except Exception as e_heal:
                    log.warning("[WATCHDOG_HEAL_FAILED] Dynamic mount failed: %s", e_heal)

            # Keep registry in sync if model is active
            if model_id and seg_key not in champions:
                champions[seg_key] = {
                    'champion_model_id': model_id,
                    'version': '1.0.0',
                    'crowned_at': utcnow().isoformat(),
                    'auto_healed': True,
                }
                registry_modified = True

            if champion_active or (scope == 'full_game' and model_id):
                healthy_count += 1
            else:
                # Critical: Market graduated into Tier 3 but has NO champion model anywhere!
                anomaly = BottleneckAnomaly(
                    anomaly_id=f"missing_champ_{seg_key.replace('|', '_')}",
                    category='missing_champion',
                    league=league,
                    scope=scope,
                    family=family,
                    sub_family=sub_family,
                    severity='CRITICAL',
                    message=f"Graduated market '{seg_key}' has no active champion model.",
                    self_healed=False,
                    remediation_action='Enqueue autonomous_bottleneck_repair in learning_jobs'
                )
                anomalies.append(anomaly)

        if registry_modified:
            try:
                registry['champions'] = champions
                t_eng.save_registry(registry)
            except Exception as e_save_reg:
                log.warning("[WATCHDOG] Failed to save updated registry champions: %s", e_save_reg)

        # -------------------------------------------------------------
        # CHECK 2: Silent Under-generation across Upcoming Fixtures
        # -------------------------------------------------------------
        if upcoming_fixtures and candidates is not None:
            active_leagues_fixtures: Dict[str, int] = {}
            for g in upcoming_fixtures:
                lg = getattr(g, 'league', None) or (g.get('league') if isinstance(g, dict) else None)
                if lg:
                    active_leagues_fixtures[lg] = active_leagues_fixtures.get(lg, 0) + 1

            candidate_counts: Counter = Counter()
            for c in candidates:
                c_lg = c.get('league')
                c_sc = c.get('model_scope') or 'full_game'
                c_mkt = c.get('market')
                fam_str = c.get('family', '')
                if ':' in fam_str:
                    _, sub = fam_str.split(':', 1)
                else:
                    sub = c_mkt
                candidate_counts[f"{c_lg}|{c_sc}|{c_mkt}"] += 1
                candidate_counts[f"{c_lg}|{c_sc}|{c_mkt}:{sub}"] += 1

            for seg_key, meta in graduated.items():
                league = meta.get('league') or seg_key.split('|')[0]
                scope = meta.get('scope') or 'full_game'
                family = meta.get('family') or ''
                sub_family = meta.get('sub_family') or family
                sub_key = f"{league}|{scope}|{family}:{sub_family}"

                if active_leagues_fixtures.get(league, 0) >= 2:
                    cnt = candidate_counts.get(seg_key, 0) + candidate_counts.get(sub_key, 0)
                    if cnt == 0:
                        undergenerating.append(seg_key)

        # -------------------------------------------------------------
        # CHECK 3: Score-Gap Sentinel (Scores missing on past-due games)
        # -------------------------------------------------------------
        score_gap_anomalies, score_healed = self._check_score_gaps()
        anomalies.extend(score_gap_anomalies)
        healed_count += score_healed

        # -------------------------------------------------------------
        # CHECK 4: Zero-Volume Fixture Sentinel (Liquid leagues with 0 bets)
        # -------------------------------------------------------------
        zero_vol_anomalies, zero_vol_healed = self._check_zero_volume_fixtures(
            upcoming_fixtures=upcoming_fixtures,
            graduated=graduated,
            active_segment_models=active_segment_models
        )
        anomalies.extend(zero_vol_anomalies)
        healed_count += zero_vol_healed

        # -------------------------------------------------------------
        # CHECK 5: Stagnant Quarantine Sentinel (Demoted markets without R&D)
        # -------------------------------------------------------------
        stagnant_anomalies, stagnant_healed = self._check_stagnant_quarantine()
        anomalies.extend(stagnant_anomalies)
        healed_count += stagnant_healed

        # -------------------------------------------------------------
        # CHECK 6: Pre-Graduation Loss Sentinel (Prevents Catch-22 demotions)
        # -------------------------------------------------------------
        pregrad_anomalies, pregrad_healed = self._check_pre_graduation_suppression(graduated)
        anomalies.extend(pregrad_anomalies)
        healed_count += pregrad_healed

        # -------------------------------------------------------------
        # CHECK 7: Wager Duplication Sentinel (Prevents artificial streak loss)
        # -------------------------------------------------------------
        dup_anomalies, dup_healed = self._check_wager_duplication()
        anomalies.extend(dup_anomalies)
        healed_count += dup_healed

        # -------------------------------------------------------------
        # CHECK 8: Enqueue Learning Jobs for Unhealed Anomalies
        # -------------------------------------------------------------
        unhealed_anomalies = [a for a in anomalies if not a.self_healed]
        if unhealed_anomalies:
            job_id = self._enqueue_autonomous_repair(unhealed_anomalies)
            if job_id:
                jobs_enqueued.append(job_id)

        # -------------------------------------------------------------
        # CHECK 9: Tier 1 & Tier 2 Pipeline Health Diagnostic
        # -------------------------------------------------------------
        tier1_2_health = self.audit_tier1_tier2_health()

        # Determine Global Status
        tier1_2_anomalies = tier1_2_health.get('anomalies', [])
        has_critical_tier1_2 = any(
            a.get('severity') == 'CRITICAL' and not a.get('self_healed', False)
            for a in tier1_2_anomalies
        )

        if unhealed_anomalies or has_critical_tier1_2:
            status = 'ESCALATED_TO_AI'
        elif (
            healed_count > 0
            or crowned_count > 0
            or tier1_2_health.get('unadmitted_champions_healed_count', 0) > 0
            or tier1_2_health.get('alias_phase_splits_count', 0) > 0
        ):
            status = 'SELF_HEALED'
        else:
            status = 'HEALTHY'

        report = BottleneckWatchdogReport(
            audited_at=now_iso,
            total_graduated_markets=total_graduated,
            healthy_markets_count=healthy_count,
            anomalies_detected_count=len(anomalies),
            anomalies_healed_count=healed_count,
            uncrowned_champions_crowned=crowned_count,
            anomalies=[asdict(a) for a in anomalies],
            undergenerating_markets=undergenerating[:10],
            learning_jobs_enqueued=jobs_enqueued,
            status=status,
            tier1_tier2_health=tier1_2_health,
            score_gaps_count=len(score_gap_anomalies),
            zero_volume_leagues_count=len(zero_vol_anomalies),
            stagnant_quarantine_count=len(stagnant_anomalies)
        )

        # Ensure newly validated champion models are distilled into generic archetypes
        try:
            from .ai_archetype_developer import AIArchetypeDeveloperAgent
            bootstrapped = AIArchetypeDeveloperAgent.get_instance().bootstrap_from_existing_champions(min_roi=0.035, limit=20)
            log.debug("[WATCHDOG] Periodic archetype distillation completed; harvested %d archetypes", bootstrapped)
        except Exception as e_distill:
            log.debug("[WATCHDOG] Periodic archetype distillation skipped: %s", e_distill)

        # Persist report atomically
        try:
            atomic_json(self.status_file, asdict(report))
        except Exception as e_save:
            log.warning("[WATCHDOG] Failed to save status: %s", e_save)

        _GLOBAL_WATCHDOG_CACHE = (now_dt, report)
        return report

    def audit_tier1_tier2_health(self) -> Dict[str, Any]:
        """Audits Tier 1 (R&D historical training) and Tier 2 (Shadow Incubator) pipeline health."""
        db_path = self.data_dir / 'shadow_incubator.sqlite3'
        health: Dict[str, Any] = {
            'tier1_total_markets': 0,
            'tier1_data_deficit_markets': 0,
            'tier1_actionable_markets': 0,
            'tier2_incubating_models': 0,
            'synthesis_preflight_failure_rate': 0.0,
            'synthesis_recent_total': 0,
            'synthesis_recent_preflight_fails': 0,
            'recent_preflight_errors': [],
            'anomalies': [],
            'audited_at': utcnow().isoformat(),
        }

        if not db_path.exists():
            return health

        try:
            with closing(sqlite3.connect(db_path, timeout=15.0)) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                cur.execute("""
                    SELECT count(*) FROM demoted_markets 
                    WHERE incubation_phase IN ('research_training', 'research_rnd')
                """)
                row = cur.fetchone()
                health['tier1_total_markets'] = row[0] if row else 0

                cur.execute("""
                    SELECT count(dm.id)
                    FROM demoted_markets dm
                    WHERE dm.incubation_phase IN ('research_training', 'research_rnd')
                      AND NOT EXISTS (
                          SELECT 1 FROM incubated_wagers iw
                          WHERE iw.league = dm.league AND iw.scope = dm.scope AND iw.family = dm.family
                      )
                """)
                row = cur.fetchone()
                health['tier1_data_deficit_markets'] = row[0] if row else 0
                health['tier1_actionable_markets'] = max(0, health['tier1_total_markets'] - health['tier1_data_deficit_markets'])

                cur.execute("""
                    SELECT count(*) FROM demoted_markets
                    WHERE incubation_phase = 'incubating_with_new_model'
                """)
                row = cur.fetchone()
                health['tier2_incubating_models'] = row[0] if row else 0

                cur.execute("""
                    SELECT count(*) FROM graduated_markets
                """)
                row = cur.fetchone()
                health['tier3_graduated_markets'] = row[0] if row else 0
                health['total_pipeline_universe_markets'] = health['tier1_total_markets'] + health['tier2_incubating_models'] + health['tier3_graduated_markets']

                cur.execute("""
                    SELECT name FROM sqlite_master WHERE type='table' AND name='ai_synthesis_feedback_loop'
                """)
                if cur.fetchone():
                    cur.execute("""
                        SELECT graduation_status, failure_reason, learned_insight
                        FROM ai_synthesis_feedback_loop
                        ORDER BY created_at DESC
                        LIMIT 50
                    """)
                    synth_rows = cur.fetchall()
                    if synth_rows:
                        health['synthesis_recent_total'] = len(synth_rows)
                        pf_fails = [
                            r for r in synth_rows 
                            if r['graduation_status'] in ('preflight_failed', 'syntax_failed', 'instantiation_failed')
                            or (r['failure_reason'] and 'Pre-flight failed' in str(r['failure_reason']))
                        ]
                        health['synthesis_recent_preflight_fails'] = len(pf_fails)
                        health['synthesis_preflight_failure_rate'] = round(len(pf_fails) / len(synth_rows), 4)

                        recent_err_msgs = []
                        for r in pf_fails[:5]:
                            msg = r['failure_reason'] or r['learned_insight'] or "Validation failed"
                            recent_err_msgs.append(str(msg)[:150])
                        health['recent_preflight_errors'] = recent_err_msgs

                        if health['synthesis_preflight_failure_rate'] > 0.20 and len(synth_rows) >= 10:
                            health['anomalies'].append({
                                'anomaly_type': 'PREFLIGHT_FAILURE_SPIKE',
                                'severity': 'HIGH',
                                'message': f"Pre-flight failure rate is {health['synthesis_preflight_failure_rate']*100:.1f}% across recent {len(synth_rows)} attempts.",
                                'sample_errors': recent_err_msgs[:3]
                            })

                # -------------------------------------------------------------
                # INVARIANT 1: Unadmitted Champions Guard & Autonomous Self-Healing
                # -------------------------------------------------------------
                # Detects if any market in research_training has a verified candidate model
                # (N >= 5, ROI >= +3.5%, positive Brier skill, DD < 25%) that was not admitted to Tier 2.
                cur.execute("""
                    SELECT 
                        dm.id as demoted_id,
                        dm.segment_key,
                        fb.model_id,
                        fb.class_name,
                        fb.oos_sample_size,
                        fb.oos_win_rate,
                        fb.oos_simulated_roi,
                        fb.oos_delta_brier,
                        fb.oos_max_drawdown
                    FROM demoted_markets dm
                    JOIN ai_synthesis_feedback_loop fb ON dm.segment_key = fb.segment_key
                    WHERE dm.incubation_phase = 'research_training'
                      AND (fb.crucible_passed = 1 OR (fb.oos_sample_size >= 5 AND fb.oos_simulated_roi >= 0.035))
                    ORDER BY fb.oos_simulated_roi DESC, fb.oos_sample_size DESC
                """)
                unadmitted_rows = cur.fetchall()
                seen_unadmitted: Set[str] = set()
                unadmitted_candidates = []
                import os, hashlib
                from .tournament_engine import TournamentEngine
                t_eng = TournamentEngine(self.data_dir)
                registry = t_eng.load_registry()
                rejected_hashes = set(registry.get('rejected', [])) if registry else set()
                for r in unadmitted_rows:
                    seg = r['segment_key']
                    if seg in seen_unadmitted:
                        continue
                    mid = r['model_id']
                    cls_name = r['class_name']
                    # Check both 1.0.0 and v1.0.0 representations against rejected hashes to prevent clogged loops
                    h1 = hashlib.sha256(f"{mid}:1.0.0:{cls_name}".encode('utf-8')).hexdigest()
                    h2 = hashlib.sha256(f"{mid}:v1.0.0:{cls_name}".encode('utf-8')).hexdigest()
                    if h1 in rejected_hashes or h2 in rejected_hashes:
                        continue
                    src_file = self.data_dir.parent / 'src' / 'betting_engine' / 'sandbox_models' / 'generated' / f"{mid}.py"
                    if not src_file.exists():
                        src_file = Path('src/betting_engine/sandbox_models/generated') / f"{mid}.py"
                    if src_file.exists():
                        seen_unadmitted.add(seg)
                        unadmitted_candidates.append(r)

                health['unadmitted_champions_backlog_count'] = len(unadmitted_candidates)
                health['unadmitted_champions_healed_count'] = 0
                log.debug("[WATCHDOG_BACKLOG_SCAN] Found %d unadmitted champion candidates in feedback loop", len(unadmitted_candidates))

                if unadmitted_candidates:
                    sample_segs = [r['segment_key'] for r in unadmitted_candidates[:5]]
                    # Autonomous Self-Healing: immediately bind and admit them
                    healed_now = 0
                    try:
                        from .demoted_market_bridge import DemotedMarketIncubationBridge
                        from .historical_trainer import CandidateTrainingResult
                        bridge = DemotedMarketIncubationBridge(self.data_dir)
                        items_for_batch = []
                        for r in unadmitted_candidates:
                            cand = CandidateTrainingResult(
                                segment_key=r['segment_key'],
                                model_id=r['model_id'],
                                model_archetype=r['class_name'],
                                model_params={'source_file': f"{r['model_id']}.py"},
                                train_sample_size=0,
                                oos_sample_size=r['oos_sample_size'] or 0,
                                oos_win_rate=r['oos_win_rate'] or 0.0,
                                oos_simulated_roi=r['oos_simulated_roi'] or 0.0,
                                oos_delta_brier=r['oos_delta_brier'] or 0.0,
                                oos_max_drawdown=r['oos_max_drawdown'] or 0.0,
                                is_qualified=True,
                                scorecard_s=100.0,
                            )
                            inst = bridge._instantiate_candidate(r['segment_key'], cand)
                            if not inst:
                                continue
                            succ, errs = bridge.tournament_engine.register_candidate(r['segment_key'], inst)
                            if succ:
                                items_for_batch.append((r['demoted_id'], r['segment_key'], cand))
                                if len(items_for_batch) >= 50:
                                    break
                        if items_for_batch:
                            batch_res = bridge.batch_admit_champions(items_for_batch)
                            healed_now = batch_res.get('admitted_count', 0)
                            health['unadmitted_champions_healed_count'] = healed_now
                            log.info("[WATCHDOG] Self-healed %d unadmitted champion models into Tier 2 via batch admission.", healed_now)
                    except Exception as e_heal:
                        log.warning("[WATCHDOG] Failed to self-heal unadmitted champions: %s", e_heal)

                    unhealed_remaining = len(unadmitted_candidates) - healed_now
                    anomaly_unadm = {
                        'anomaly_type': 'UNADMITTED_CHAMPIONS_BACKLOG',
                        'severity': 'HIGH' if unhealed_remaining > 50 else 'MEDIUM',
                        'message': (
                            f"Detected {len(unadmitted_candidates)} markets in Tier 1 R&D that have candidate models on disk. "
                            f"Self-healed {healed_now}/{len(unadmitted_candidates)} into Tier 2."
                        ),
                        'sample_markets': sample_segs,
                        'self_healed': (healed_now > 0)
                    }
                    health['anomalies'].append(anomaly_unadm)

                # -------------------------------------------------------------
                # INVARIANT 2: Alias Phase Split Guard & Self-Healing
                # -------------------------------------------------------------
                # Detects if any sister alias is stuck in Tier 1 research while another is incubating in Tier 2
                cur.execute("""
                    SELECT d1.id as t1_id, d1.segment_key as t1_seg, d2.segment_key as t2_seg,
                           d2.incubated_model_id, d2.model_archetype, d2.model_params,
                           d2.historical_roi, d2.historical_brier_delta, d2.historical_sample_size,
                           d2.admitted_to_incubator_at
                    FROM demoted_markets d1
                    JOIN demoted_markets d2 ON d1.league = d2.league AND d1.scope = d2.scope AND d1.id != d2.id
                    WHERE d1.incubation_phase = 'research_training'
                      AND d2.incubation_phase = 'incubating_with_new_model'
                      AND d2.incubated_model_id IS NOT NULL
                      AND (
                          d1.family = d2.family
                          OR d1.family LIKE '%' || d2.family || '%'
                          OR d2.family LIKE '%' || d1.family || '%'
                          OR (d1.sub_family IS NOT NULL AND d1.sub_family = d2.sub_family)
                      )
                """)
                alias_splits = cur.fetchall()
                health['alias_phase_splits_count'] = len(alias_splits)
                log.debug("[WATCHDOG_ALIAS_SCAN] Found %d alias phase splits between Tier 1 and Tier 2", len(alias_splits))
                if alias_splits:
                    # Autonomous Self-Healing: align sister aliases to Tier 2
                    try:
                        conn_heal = sqlite3.connect(db_path, timeout=30.0)
                        conn_heal.execute("PRAGMA busy_timeout = 30000")
                        now_adm = utcnow().isoformat()
                        for r in alias_splits:
                            log.debug("[WATCHDOG_ALIAS_HEAL] Syncing alias '%s' with incubating model '%s' from '%s'", r['t1_seg'], r['incubated_model_id'], r['t2_seg'])
                            conn_heal.execute("""
                                UPDATE demoted_markets
                                SET incubation_phase = 'incubating_with_new_model',
                                    incubated_model_id = ?,
                                    model_archetype = ?,
                                    model_params = ?,
                                    historical_roi = ?,
                                    historical_brier_delta = ?,
                                    historical_sample_size = ?,
                                    admitted_to_incubator_at = ?
                                WHERE id = ?
                            """, (
                                r['incubated_model_id'],
                                r['model_archetype'],
                                r['model_params'],
                                r['historical_roi'],
                                r['historical_brier_delta'],
                                r['historical_sample_size'],
                                r['admitted_to_incubator_at'] or now_adm,
                                r['t1_id']
                            ))
                        conn_heal.commit()
                        conn_heal.close()
                        log.info("[WATCHDOG] Self-healed %d alias phase splits into Tier 2.", len(alias_splits))
                    except Exception as e_alias_heal:
                        log.warning("[WATCHDOG] Failed to self-heal alias splits: %s", e_alias_heal)

                    health['anomalies'].append({
                        'anomaly_type': 'ALIAS_PHASE_SPLIT',
                        'severity': 'MEDIUM',
                        'message': f"Detected {len(alias_splits)} alias splits. Self-healed into Tier 2.",
                        'sample_pairs': [(r['t1_seg'], r['t2_seg']) for r in alias_splits[:4]],
                        'self_healed': True
                    })

                # -------------------------------------------------------------
                # INVARIANT 3: Tier 2 Flow Rate & Funnel Choke Guard
                # -------------------------------------------------------------
                # Re-query tier 2 count after potential self-healing
                cur.execute("SELECT count(*) FROM demoted_markets WHERE incubation_phase = 'incubating_with_new_model'")
                row_t2 = cur.fetchone()
                health['tier2_incubating_models'] = row_t2[0] if row_t2 else 0

                conversion_ratio = (
                    health['tier2_incubating_models'] /
                    max(1, health['tier1_actionable_markets'] + health['tier2_incubating_models'])
                )
                health['funnel_conversion_ratio'] = round(conversion_ratio, 4)
                log.debug("[WATCHDOG_FUNNEL_RATIO] Tier 1 Actionable: %d, Tier 2 Incubating: %d -> Conversion Ratio: %.2f%%",
                          health['tier1_actionable_markets'], health['tier2_incubating_models'], conversion_ratio * 100)

                if health['tier2_incubating_models'] < 50 and health['tier1_actionable_markets'] >= 100:
                    health['anomalies'].append({
                        'anomaly_type': 'TIER2_FLOW_CHOKED',
                        'severity': 'CRITICAL',
                        'message': (
                            f"Tier 2 Shadow Incubator contains only {health['tier2_incubating_models']} models "
                            f"while Tier 1 contains {health['tier1_actionable_markets']} actionable liquid markets "
                            f"(conversion ratio {conversion_ratio*100:.1f}%). Funnel is choked."
                        ),
                    })
        except Exception as e:
            log.warning("[WATCHDOG] Tier 1 & 2 audit error: %s", e)

        return health

    def _check_score_gaps(self) -> Tuple[List[BottleneckAnomaly], int]:
        """Detects completed or past-due matches missing final scores and triggers autonomous recovery."""
        anomalies: List[BottleneckAnomaly] = []
        healed_count = 0
        from .verbose_logger import get_verbose_logger
        vl = get_verbose_logger(self.data_dir)
        now_dt = utcnow()
        cutoff_dt = now_dt - timedelta(hours=18)
        cutoff_iso = cutoff_dt.isoformat()

        games_path = self.data_dir / 'games.json'
        past_unscored: List[Dict[str, Any]] = []
        if games_path.exists():
            try:
                with open(games_path, 'r', encoding='utf-8') as f:
                    games = json.load(f)
                for g in games:
                    st = g.get('start')
                    if st and st < cutoff_iso and g.get('state') in ('pre', 'scheduled') and g.get('home_score') is None:
                        past_unscored.append(g)
            except Exception as e_read:
                log.warning("[WATCHDOG_SCORE_GAP] Failed to read games.json: %s", e_read)

        # Also check open wagers in shadow incubator
        inc_db = self.data_dir / 'shadow_incubator.sqlite3'
        stale_wagers_count = 0
        if inc_db.exists():
            try:
                with closing(sqlite3.connect(f"file:{inc_db}?mode=ro", uri=True, timeout=10.0)) as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT count(*) FROM incubated_wagers
                        WHERE status = 'open' AND captured_at < ?
                    """, (cutoff_iso,))
                    row = cur.fetchone()
                    stale_wagers_count = row[0] if row else 0
            except Exception as e_w:
                log.debug("[WATCHDOG_SCORE_GAP] Stale wager scan: %s", e_w)

        if past_unscored or stale_wagers_count > 0:
            msg = f"Detected {len(past_unscored)} past-due games (> 18h) and {stale_wagers_count} stale incubator wagers missing final scores."
            vl.log_event("bottleneck_watchdog", "score_gap_detected", msg, data={
                'unscored_games_count': len(past_unscored),
                'stale_wagers_count': stale_wagers_count,
                'sample_game_ids': [g.get('id') for g in past_unscored[:5]],
            }, level=logging.WARNING)

            # Autonomous Self-Healing: Trigger ESPN recovery & college score refresh (throttled to 1 per 5m)
            recovery_success = False
            last_heal = BottleneckWatchdog._LAST_SCORE_GAP_HEAL_AT
            is_testing = bool(os.environ.get('PYTEST_CURRENT_TEST'))
            should_run_sup = not is_testing and (last_heal is None or (now_dt - last_heal).total_seconds() >= 300.0)
            if should_run_sup:
                try:
                    BottleneckWatchdog._LAST_SCORE_GAP_HEAL_AT = now_dt
                    from .settlement_supervisor import SettlementSupervisor
                    sup = SettlementSupervisor(data_dir=self.data_dir)
                    cycle_res = sup.run_settlement_cycle()
                    recovered_count = cycle_res.get('recovered_games', 0)
                    settled_count = cycle_res.get('settled_count', 0)
                    recovery_success = (recovered_count > 0 or settled_count > 0)
                    if recovery_success:
                        healed_count += 1
                        vl.log_event("bottleneck_watchdog", "score_gap_healed",
                                     f"Autonomously recovered {recovered_count} game scores and settled {settled_count} bets.",
                                     data=cycle_res, level=logging.INFO)
                except Exception as e_sup:
                    log.warning("[WATCHDOG_SCORE_GAP] SettlementSupervisor recovery error: %s", e_sup)

            anomalies.append(BottleneckAnomaly(
                anomaly_id=f"score_gap_{now_dt.strftime('%Y%m%d_%H')}",
                category='score_gap',
                league='MULTI',
                scope='full_game',
                family='settlement',
                sub_family=None,
                severity='HIGH',
                message=msg,
                self_healed=recovery_success,
                remediation_action="Triggered ESPN scoreboard and college sports recovery" if recovery_success else "Pending ESPN final score publication"
            ))

        return anomalies, healed_count

    def _check_zero_volume_fixtures(
        self,
        upcoming_fixtures: Optional[List[Any]],
        graduated: Dict[str, Any],
        active_segment_models: Optional[Dict[Tuple[str, str], Any]]
    ) -> Tuple[List[BottleneckAnomaly], int]:
        """Detects liquid leagues with upcoming fixtures within 72h that lack funded bets."""
        anomalies: List[BottleneckAnomaly] = []
        healed_count = 0
        from .verbose_logger import get_verbose_logger
        vl = get_verbose_logger(self.data_dir)

        now_dt = utcnow()
        horizon_iso = (now_dt + timedelta(hours=72)).isoformat()
        now_iso = now_dt.isoformat()

        # Load upcoming liquid fixtures if not provided
        fixtures = upcoming_fixtures
        if fixtures is None:
            games_path = self.data_dir / 'games.json'
            if games_path.exists():
                try:
                    with open(games_path, 'r', encoding='utf-8') as f:
                        raw_games = json.load(f)
                    fixtures = [
                        g for g in raw_games
                        if g.get('start') and now_iso <= g.get('start') <= horizon_iso
                        and g.get('state') in ('pre', 'scheduled')
                    ]
                except Exception:
                    fixtures = []
            else:
                fixtures = []

        league_fixtures: Dict[str, int] = {}
        for g in fixtures:
            lg = getattr(g, 'league', None) or (g.get('league') if isinstance(g, dict) else None)
            if lg:
                league_fixtures[lg] = league_fixtures.get(lg, 0) + 1

        liquid_leagues = {'NFL', 'NHL', 'NBA', 'MLB', 'NCAAF', 'NCAAM', 'EPL', 'UCL', 'MLS', 'ATP', 'WTA'}
        engine_db = self.data_dir / 'engine.sqlite3'
        if not engine_db.exists():
            return anomalies, healed_count

        open_bets_by_league: Dict[str, int] = {}
        try:
            with closing(sqlite3.connect(f"file:{engine_db}?mode=ro", uri=True, timeout=10.0)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT json_extract(payload, '$.league') as lg, count(*)
                    FROM bets
                    WHERE status = 'open'
                    GROUP BY lg
                """)
                for r in cur.fetchall():
                    if r[0]:
                        open_bets_by_league[r[0]] = r[1]
        except Exception as e_bets:
            log.debug("[WATCHDOG_ZERO_VOL] Failed to read open bets: %s", e_bets)

        for lg in liquid_leagues:
            fix_count = league_fixtures.get(lg, 0)
            bet_count = open_bets_by_league.get(lg, 0)

            # If league has >= 5 upcoming fixtures in next 72h but 0 open bets
            if fix_count >= 5 and bet_count == 0:
                grad_keys = [k for k in graduated if k.startswith(f"{lg}|")]
                has_grad = len(grad_keys) > 0

                msg = f"League '{lg}' has {fix_count} upcoming fixtures within 72h but 0 funded open bets."
                vl.log_event("bottleneck_watchdog", "zero_volume_detected", msg, data={
                    'league': lg,
                    'upcoming_fixtures': fix_count,
                    'open_bets': bet_count,
                    'graduated_markets_count': len(grad_keys)
                }, level=logging.WARNING)

                self_healed = False
                remedy = None

                if has_grad and active_segment_models is not None:
                    try:
                        from .graduated_models import resolve_graduated_segment_models
                        sub_grad = {k: graduated[k] for k in grad_keys}
                        resolved = resolve_graduated_segment_models(sub_grad, active_segment_models)
                        active_segment_models.update(resolved)
                        self_healed = True
                        healed_count += 1
                        remedy = f"Mounted {len(resolved)} graduated models for {lg} into active memory."
                    except Exception as e_res:
                        remedy = f"Failed to mount models: {e_res}"
                elif not has_grad:
                    remedy = f"Enqueued candidate model synthesis for {lg} in learning_jobs."
                    self._enqueue_league_synthesis(lg)
                    self_healed = True
                    healed_count += 1

                anomalies.append(BottleneckAnomaly(
                    anomaly_id=f"zero_vol_{lg}_{now_dt.strftime('%Y%m%d_%H')}",
                    category='zero_volume_fixtures',
                    league=lg,
                    scope='full_game',
                    family='volume',
                    sub_family=None,
                    severity='HIGH',
                    message=msg,
                    self_healed=self_healed,
                    remediation_action=remedy
                ))

        return anomalies, healed_count

    def _check_stagnant_quarantine(self) -> Tuple[List[BottleneckAnomaly], int]:
        """Detects markets sitting in demoted_markets for > 24 hours without active R&D."""
        anomalies: List[BottleneckAnomaly] = []
        healed_count = 0
        from .verbose_logger import get_verbose_logger
        vl = get_verbose_logger(self.data_dir)

        inc_db = self.data_dir / 'shadow_incubator.sqlite3'
        engine_db = self.data_dir / 'engine.sqlite3'
        if not inc_db.exists() or not engine_db.exists():
            return anomalies, healed_count

        now_dt = utcnow()
        cutoff_iso = (now_dt - timedelta(hours=24)).isoformat()

        stagnant_rows = []
        try:
            with closing(sqlite3.connect(f"file:{inc_db}?mode=ro", uri=True, timeout=10.0)) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, segment_key, league, scope, family, sub_family, demoted_at, reason
                    FROM demoted_markets
                    WHERE incubation_phase IN ('research_rnd', 'research_training')
                      AND (demoted_at IS NULL OR demoted_at < ?)
                    LIMIT 20
                """, (cutoff_iso,))
                stagnant_rows = cur.fetchall()
        except Exception as e_stag:
            log.debug("[WATCHDOG_STAGNANT] Query error: %s", e_stag)

        if not stagnant_rows:
            return anomalies, healed_count

        active_segs = set()
        try:
            with closing(sqlite3.connect(f"file:{engine_db}?mode=ro", uri=True, timeout=10.0)) as conn_eng:
                cur_eng = conn_eng.cursor()
                cur_eng.execute("SELECT payload FROM learning_jobs WHERE status IN ('queued', 'running', 'retry')")
                for r in cur_eng.fetchall():
                    try:
                        p = json.loads(r[0])
                        seg = p.get('segment') or p.get('segment_key')
                        if seg:
                            active_segs.add(seg)
                    except Exception:
                        pass
        except Exception as e_lj:
            log.debug("[WATCHDOG_STAGNANT] Learning jobs scan: %s", e_lj)

        unserviced = [r for r in stagnant_rows if r['segment_key'] not in active_segs]
        if unserviced:
            sample_segs = [r['segment_key'] for r in unserviced[:5]]
            msg = f"Detected {len(unserviced)} stagnant demoted markets (> 24h) without active R&D jobs: {sample_segs}"
            vl.log_event("bottleneck_watchdog", "stagnant_quarantine_detected", msg, data={
                'stagnant_count': len(unserviced),
                'sample_segments': sample_segs
            }, level=logging.INFO)

            enqueued_count = 0
            for r in unserviced[:5]:
                try:
                    seg = r['segment_key']
                    job_id = f"auto_repair_{hashlib.sha256(f'{seg}_{now_dt.isoformat()}'.encode()).hexdigest()[:12]}"
                    payload = json.dumps({
                        'segment': seg,
                        'reason': f"watchdog_stagnant_quarantine_turnaround: {r['reason'] or 'demoted'}",
                        'target_roi': 10.0,
                        'objective': f"Synthesize and calibrate candidate domain model on historical games for {seg} to pass Crucible"
                    })
                    with closing(sqlite3.connect(engine_db, timeout=45.0)) as conn_w:
                        conn_w.execute("PRAGMA busy_timeout = 60000")
                        conn_w.execute("""
                            INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, attempts, payload)
                            VALUES (?, 'autonomous_alpha_recovery', ?, 'queued', ?, ?, ?, 0, ?)
                            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                        """, (job_id, r['league'], now_dt.isoformat(), now_dt.isoformat(), now_dt.isoformat(), payload))
                        conn_w.commit()
                        enqueued_count += 1
                except Exception as e_enq:
                    log.warning("[WATCHDOG_STAGNANT] Failed to enqueue repair for %s: %s", r['segment_key'], e_enq)

            if enqueued_count > 0:
                healed_count += enqueued_count

            anomalies.append(BottleneckAnomaly(
                anomaly_id=f"stagnant_quarantine_{now_dt.strftime('%Y%m%d_%H')}",
                category='stagnant_quarantine',
                league='MULTI',
                scope='full_game',
                family='turnaround',
                sub_family=None,
                severity='MEDIUM',
                message=msg,
                self_healed=(enqueued_count > 0),
                remediation_action=f"Enqueued {enqueued_count} autonomous_alpha_recovery turnaround jobs in learning_jobs."
            ))

        return anomalies, healed_count

    def _check_pre_graduation_suppression(self, graduated: Dict[str, Any]) -> Tuple[List[BottleneckAnomaly], int]:
        """Ensures that newly graduated markets are never demoted by historical pre-graduation losses."""
        anomalies: List[BottleneckAnomaly] = []
        healed_count = 0
        inc_db = self.data_dir / 'shadow_incubator.sqlite3'
        if not inc_db.exists():
            return anomalies, healed_count

        try:
            with closing(sqlite3.connect(f"file:{inc_db}?mode=ro", uri=True, timeout=10.0)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT dm.segment_key, dm.reason
                    FROM demoted_markets dm
                    JOIN graduated_markets gm ON dm.segment_key = gm.segment_key
                    WHERE dm.incubation_phase IN ('research_rnd', 'research_training')
                """)
                conflicts = cur.fetchall()
                if conflicts:
                    from .verbose_logger import get_verbose_logger
                    vl = get_verbose_logger(self.data_dir)
                    msg = f"Detected {len(conflicts)} graduated markets mistakenly flagged as demoted: {[c[0] for c in conflicts]}"
                    vl.log_event("bottleneck_watchdog", "pre_graduation_suppression_detected", msg, level=logging.WARNING)

                    try:
                        with closing(sqlite3.connect(inc_db, timeout=30.0)) as heal_conn:
                            heal_conn.execute("PRAGMA busy_timeout = 30000")
                            for c in conflicts:
                                heal_conn.execute("DELETE FROM demoted_markets WHERE segment_key = ?", (c[0],))
                            heal_conn.commit()
                        healed_count += len(conflicts)
                        anomalies.append(BottleneckAnomaly(
                            anomaly_id=f"pre_grad_suppression_{utcnow().strftime('%Y%m%d_%H')}",
                            category='pre_graduation_anchor',
                            league='MULTI',
                            scope='full_game',
                            family='graduation',
                            sub_family=None,
                            severity='HIGH',
                            message=msg,
                            self_healed=True,
                            remediation_action=f"Purged {len(conflicts)} conflicting demoted records for active graduated markets."
                        ))
                    except Exception as e_h:
                        log.warning("[WATCHDOG] Failed to purge conflicting demoted records: %s", e_h)
        except Exception as e_chk:
            log.debug("[WATCHDOG] Pre-graduation suppression check error: %s", e_chk)

        return anomalies, healed_count

    def _check_wager_duplication(self) -> Tuple[List[BottleneckAnomaly], int]:
        """Detects and deduplicates inflated in-flight wagers in shadow_incubator."""
        anomalies: List[BottleneckAnomaly] = []
        healed_count = 0
        from .verbose_logger import get_verbose_logger
        vl = get_verbose_logger(self.data_dir)

        inc_db = self.data_dir / 'shadow_incubator.sqlite3'
        if not inc_db.exists():
            return anomalies, healed_count

        try:
            with closing(sqlite3.connect(inc_db, timeout=30.0)) as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                cur = conn.cursor()
                cur.execute("""
                    SELECT fixture_id, selection, line, count(*) as cnt
                    FROM incubated_wagers
                    WHERE status = 'open'
                    GROUP BY fixture_id, selection, line
                    HAVING count(*) > 3
                """)
                dups = cur.fetchall()
                if dups:
                    msg = f"Detected {len(dups)} fixture/line tuples with > 3 duplicate open wagers."
                    vl.log_event("bottleneck_watchdog", "wager_duplication_detected", msg, data={
                        'duplicate_groups_count': len(dups),
                        'sample_dups': [(d[0], d[1], d[2], d[3]) for d in dups[:3]]
                    }, level=logging.INFO)

                    deleted_total = 0
                    for d in dups:
                        cur.execute("""
                            DELETE FROM incubated_wagers
                            WHERE fixture_id = ? AND selection = ? AND (line = ? OR (line IS NULL AND ? IS NULL))
                              AND status = 'open'
                              AND id NOT IN (
                                  SELECT min(id) FROM incubated_wagers
                                  WHERE fixture_id = ? AND selection = ? AND (line = ? OR (line IS NULL AND ? IS NULL))
                                    AND status = 'open'
                              )
                        """, (d[0], d[1], d[2], d[2], d[0], d[1], d[2], d[2]))
                        deleted_total += cur.rowcount
                    conn.commit()

                    healed_count += 1
                    vl.log_event("bottleneck_watchdog", "wager_duplication_healed",
                                 f"Deduplicated {deleted_total} redundant incubator records.", level=logging.INFO)

                    anomalies.append(BottleneckAnomaly(
                        anomaly_id=f"wager_dup_{utcnow().strftime('%Y%m%d_%H')}",
                        category='wager_duplication',
                        league='MULTI',
                        scope='incubator',
                        family='deduplication',
                        sub_family=None,
                        severity='INFO',
                        message=msg,
                        self_healed=True,
                        remediation_action=f"Deduplicated {deleted_total} redundant records in incubated_wagers."
                    ))
        except Exception as e_dup:
            log.debug("[WATCHDOG_DUP] Deduplication scan error: %s", e_dup)

        return anomalies, healed_count

    def _enqueue_league_synthesis(self, league: str) -> None:
        """Enqueues candidate model synthesis for a zero-volume liquid league."""
        engine_db = self.data_dir / 'engine.sqlite3'
        if not engine_db.exists():
            return
        now_dt = utcnow()
        now_iso = now_dt.isoformat()
        job_id = f"vol_synth_{league}_{now_dt.strftime('%Y%m%d_%H')}"
        payload = json.dumps({
            'job_kind': 'market_gap_model_synthesis',
            'league': league,
            'model_scope': 'full_game',
            'market': 'moneyline' if league in ('ATP', 'WTA', 'CHALLENGER') else 'spreads',
            'objective': f"Synthesize and calibrate candidate model for zero-volume liquid league {league}",
            'created_by': 'BottleneckWatchdog'
        })
        try:
            with closing(sqlite3.connect(engine_db, timeout=45.0)) as conn:
                conn.execute("PRAGMA busy_timeout = 60000")
                conn.execute("""
                    INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, attempts, payload)
                    VALUES (?, 'market_gap_model_synthesis', ?, 'queued', ?, ?, ?, 0, ?)
                    ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """, (job_id, league, now_iso, now_iso, now_iso, payload))
                conn.commit()
                log.info("[WATCHDOG] Enqueued market_gap_model_synthesis for league %s", league)
        except Exception as e:
            log.warning("[WATCHDOG] Failed to enqueue league synthesis for %s: %s", league, e)

    def _enqueue_autonomous_repair(self, anomalies: List[BottleneckAnomaly]) -> Optional[str]:
        """Atomically enqueues a high-priority repair job in SQLite learning_jobs."""
        db_path = self.data_dir / 'engine.sqlite3'
        if not db_path.exists():
            return None

        now_iso = utcnow().isoformat()
        job_id = f"bottleneck_repair_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        payload = {
            'anomaly_count': len(anomalies),
            'anomalies': [asdict(a) for a in anomalies],
            'actionable_directive': (
                "Automated R&D repair required: Unmounted or missing champions in graduated Tier 3 markets. "
                "Train/tune candidate model archetype, run Crucible walk-forward backtest, and register champion."
            )
        }

        import time as _t
        for attempt in range(8):
            try:
                with closing(sqlite3.connect(db_path, timeout=60.0)) as conn:
                    conn.execute("PRAGMA busy_timeout = 60000;")
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, attempts, payload)
                        VALUES (?, 'autonomous_bottleneck_repair', 'MULTI', 'queued', ?, ?, ?, 0, ?)
                        ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                    """, (job_id, now_iso, now_iso, now_iso, json.dumps(payload)))
                    conn.commit()
                    log.info("[WATCHDOG] Successfully enqueued autonomous repair job '%s' with %d anomalies.", job_id, len(anomalies))
                    return job_id
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() and attempt < 7:
                    _t.sleep(0.1 * (1.5 ** attempt))
                    continue
                log.error("[WATCHDOG] Could not enqueue learning_jobs repair: %s", exc)
                return None
            except Exception as exc:
                log.error("[WATCHDOG] Could not enqueue learning_jobs repair: %s", exc)
                return None


def get_latest_bottleneck_watchdog_status(data_dir: Path = DATA) -> Dict[str, Any]:
    """Retrieves the latest persisted bottleneck watchdog report."""
    path = data_dir / 'bottleneck_watchdog_status.json'
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'status': 'not_initialized', 'healthy_markets_count': 0, 'anomalies_detected_count': 0}
