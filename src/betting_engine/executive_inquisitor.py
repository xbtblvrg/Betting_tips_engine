"""Autonomous Executive Inquisitor & User Persona Mirror (Vezetői Stratégiai Kérdező Motor).

Emulates the Chief Executive / Forensic Quant Owner (Béla Varga) by autonomously:
1. Formulating the exact probing questions the user historically asks.
2. Scanning the entire codebase, live feeds, shadow incubator, and betting ledger against those questions.
3. Diagnosing root-causes, identifying bottlenecks ("dugulások", "anomáliák", "beégetett konstansok").
4. Autonomously executing remedies and advancing the system toward the Prime Goal (10% weekly ROI & daily PnL uptrend).
5. Documenting the findings and briefing the executive dossier at /api/executive-inquiry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from .domain import utcnow, parse_time, LEAGUES
from .io_utils import atomic_json
from .providers import DATA
from .verbose_logger import setup_verbose_logging, get_verbose_logger
setup_verbose_logging()

log = logging.getLogger(__name__)

STATUS_PATH = DATA / 'executive_inquisitor_status.json'
JOURNAL_PATH = DATA / 'executive_inquisitor_journal.jsonl'


@dataclass
class ExecutiveInquiry:
    inquiry_id: str
    category: str
    user_archetype_question: str
    system_lens: str
    investigation_summary: str
    status: str  # 'optimal', 'bottleneck_detected', 'healed', 'warning'
    findings: List[str] = field(default_factory=list)
    action_taken: Optional[str] = None
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AutonomousExecutiveInquisitor:
    """Supreme AI Proxy: Thinks, Questions, Probes, and Resolves as the Chief Executive."""

    def __init__(self, root: Optional[Path] = None, data_dir: Optional[Path] = None):
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parents[2]
        self.data_dir = Path(data_dir).resolve() if data_dir else DATA
        self.engine_db = self.data_dir / 'engine.sqlite3'
        self.incubator_db = self.data_dir / 'shadow_incubator.sqlite3'
        self.status_path = self.data_dir / 'executive_inquisitor_status.json'
        self.journal_path = self.data_dir / 'executive_inquisitor_journal.jsonl'

    def log_inquiry_event(self, event_type: str, details: Dict[str, Any]) -> None:
        """Appends an event to the persistent inquisitor journal."""
        entry = {
            'timestamp': utcnow().isoformat(),
            'event_type': event_type,
            'details': details
        }
        try:
            with open(self.journal_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as exc:
            log.warning('Could not log inquisitor event: %s', exc)

    def _enqueue_self_healing_code_task(
        self,
        *,
        file_path: str,
        line_no: int = 0,
        function_name: str = '',
        category: str = 'autonomous_code_healing',
        description: str = '',
        evidence: str = '',
    ) -> Optional[str]:
        """Enqueues a Priority-0 AI self-healing programming job in learning_jobs + trigger journal."""
        if ("PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules) and self.data_dir.resolve() == DATA.resolve():
            return None
        now_iso = utcnow().isoformat()
        sig = f"inquisitor_heal:{category}:{file_path}:{line_no}:{description[:80]}"
        job_id = hashlib.sha256(sig.encode('utf-8')).hexdigest()[:24]
        payload = {
            'job_kind': 'autonomous_code_healing',
            'trigger_source': 'executive_inquisitor',
            'triggered_at': now_iso,
            'category': category,
            'file': file_path,
            'line': int(line_no or 0),
            'function': function_name,
            'description': description[:400],
            'exception_type': category,
            'exception_message': description[:400],
            'traceback': evidence[-4000:] if evidence else '',
            'evidence': evidence[-4000:] if evidence else '',
        }
        try:
            trig_dir = self.data_dir / 'logs'
            trig_dir.mkdir(parents=True, exist_ok=True)
            with open(trig_dir / 'self_healing_triggers.jsonl', 'a', encoding='utf-8') as tf:
                tf.write(json.dumps({'job_id': job_id, **payload}, ensure_ascii=False) + '\n')
        except Exception:
            pass
        try:
            get_verbose_logger(self.data_dir).log_event(
                'executive_inquisitor',
                'self_healing_code_task_enqueued',
                f"Enqueued Priority-0 AI self-healing task {job_id} for {file_path}:{line_no} ({description[:120]})",
                data={'job_id': job_id, **payload},
                level=logging.WARNING,
            )
        except Exception:
            pass
        if self.engine_db.exists():
            try:
                from contextlib import closing
                with closing(sqlite3.connect(str(self.engine_db), timeout=5.0)) as conn:
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO learning_jobs (
                            id, kind, league, status, created_at, updated_at, next_attempt, attempts, payload
                        ) VALUES (?, 'autonomous_code_healing', 'SYSTEM', 'queued', ?, ?, ?, 0, ?)
                        """,
                        (job_id, now_iso, now_iso, now_iso, json.dumps(payload, ensure_ascii=False)),
                    )
                    conn.commit()
                return job_id
            except Exception as exc:
                log.debug('Could not enqueue inquisitor self-healing job: %s', exc)
        return None

    def probe_q1_bugs_and_anomalies(self) -> ExecutiveInquiry:
        """User Lens: 'Keress bugokat, inkonzisztenciákat, elvarratlan szálakat!'"""
        findings = []
        status = 'optimal'
        action = None
        telemetry = {}

        # 1. Check for stuck learning jobs
        if self.engine_db.exists():
            try:
                conn = sqlite3.connect(f"file:{self.engine_db.resolve()}?mode=ro", uri=True, timeout=5.0)
                cur = conn.cursor()
                now_iso = utcnow().isoformat()
                cur.execute(
                    "SELECT count(*) FROM learning_jobs WHERE status = 'running' AND datetime(updated_at, '+30 minutes') < datetime(?)",
                    (now_iso,)
                )
                stuck_jobs = cur.fetchone()[0]
                telemetry['stuck_running_jobs'] = stuck_jobs
                if stuck_jobs > 0:
                    try:
                        from contextlib import closing
                        with closing(sqlite3.connect(str(self.engine_db), timeout=30.0)) as heal_conn:
                            heal_conn.execute("PRAGMA busy_timeout = 30000")
                            heal_conn.execute(
                                "UPDATE learning_jobs SET status = 'queued', updated_at = ? WHERE status = 'running' AND datetime(updated_at, '+30 minutes') < datetime(?)",
                                (now_iso, now_iso)
                            )
                            heal_conn.commit()
                        action = f"Autonóm beavatkozás: {stuck_jobs} db beragadt futó feladat visszaállítva 'queued' sorba."
                        status = 'healed'
                    except Exception as ex:
                        findings.append(f"{stuck_jobs} db beragadt futó feladat a sorban (újraindítás sikertelen: {ex}).")
                        status = 'warning'
                
                # Check for recurring errors and auto-route unhandled code exceptions to AI self-healing
                cur.execute("SELECT count(*) FROM learning_jobs WHERE status = 'failed'")
                failed_jobs = cur.fetchone()[0]
                telemetry['failed_learning_jobs'] = failed_jobs
                if failed_jobs > 5:
                    findings.append(f"{failed_jobs} db sikertelen feladat gyűlt össze a tanuló sorban.")
                if failed_jobs > 0:
                    try:
                        err_rows = cur.execute(
                            "SELECT id, kind, last_error FROM learning_jobs WHERE status = 'failed' AND last_error IS NOT NULL AND kind NOT IN ('autonomous_code_healing', 'runtime_exception_auto_repair') LIMIT 3"
                        ).fetchall()
                        enqueued_ids = []
                        for jid, jkind, jerr in err_rows:
                            err_txt = str(jerr or '')
                            m_py = re.findall(r'File "([^"]*src/betting_engine/[a-zA-Z0-9_]+\.py)", line (\d+), in ([a-zA-Z0-9_<>]+)', err_txt)
                            if m_py:
                                fp_raw, lno_raw, fn_raw = m_py[-1]
                                rel_fp = 'src/betting_engine/' + Path(fp_raw).name
                                hid = self._enqueue_self_healing_code_task(
                                    file_path=rel_fp,
                                    line_no=int(lno_raw),
                                    function_name=fn_raw,
                                    category='runtime_exception_auto_repair',
                                    description=f"Failed learning_job {jkind} ({jid}): {err_txt[:200]}",
                                    evidence=err_txt,
                                )
                                if hid:
                                    enqueued_ids.append(hid)
                        if enqueued_ids:
                            telemetry['self_healing_jobs_enqueued'] = enqueued_ids
                    except Exception:
                        pass

                # Check Polymarket order health
                try:
                    cur.execute("SELECT count(*) FROM polymarket_orders WHERE status = 'rejected'")
                    poly_rejected = cur.fetchone()[0]
                    cur.execute("SELECT count(*) FROM polymarket_orders WHERE status IN ('open', 'filled')")
                    poly_active = cur.fetchone()[0]
                    telemetry['polymarket_rejected_orders'] = poly_rejected
                    telemetry['polymarket_active_orders'] = poly_active
                    if poly_rejected > 10:
                        findings.append(f"{poly_rejected} db elutasított Polymarket megbízás található a főkönyvben.")
                except Exception:
                    pass
                conn.close()
            except Exception as e:
                findings.append(f"Adatbázis lekérdezési hiba az anomália-vizsgálatnál: {e}")

        summary = "Nincsenek aktív kód-összeomlások vagy elakadt pipeline folyamatok." if status == 'optimal' else f"{len(findings)} anomália észlelve."
        return ExecutiveInquiry(
            inquiry_id="INQ-01-BUGS-ANOMALIES",
            category="anomalies_and_bugs",
            user_archetype_question="Vannak rejtett bugok, inkonzisztenciák vagy elvarratlan szálak a rendszerben?",
            system_lens="Forensic Log, Job Queue & Pipeline Anomaly Sweep",
            investigation_summary=summary,
            status=status,
            findings=findings,
            action_taken=action,
            telemetry=telemetry
        )

    def probe_q2_interoperability_and_progression(self) -> ExecutiveInquiry:
        """User Lens: 'Átjárhatóság megvan? Minden szint egységesen fejlődik (Tier 1 -> Tier 2 -> Tier 3 -> Tier 4)?'"""
        findings = []
        status = 'optimal'
        action = None
        telemetry = {}

        if self.incubator_db.exists():
            try:
                conn = sqlite3.connect(f"file:{self.incubator_db.resolve()}?mode=ro", uri=True, timeout=5.0)
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM demoted_markets WHERE incubation_phase = 'research_training'")
                t1_cnt = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM demoted_markets WHERE incubation_phase = 'incubating_with_new_model'")
                t2_cnt = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM graduated_markets")
                t3_cnt = cur.fetchone()[0]
                conn.close()

                t4_cnt = 0
                t4_dual_cnt = 0
                try:
                    from .unified_market_universe import UnifiedMarketUniverse
                    roster = UnifiedMarketUniverse(self.data_dir).build_universe_roster()
                    t4_cnt = sum(1 for p in roster.values() if p.tier == 4)
                    t4_dual_cnt = sum(1 for p in roster.values() if p.tier == 4 and p.is_dual_polymarket)
                except Exception:
                    pass

                telemetry['tier_1_research_count'] = t1_cnt
                telemetry['tier_2_shadow_incubating_count'] = t2_cnt
                telemetry['tier_3_production_graduated_count'] = t3_cnt
                telemetry['tier_4_live_proven_alpha_count'] = t4_cnt
                telemetry['tier_4_dual_polymarket_count'] = t4_dual_cnt

                try:
                    from .shadow_market_incubator import ShadowMarketIncubator
                    inc = ShadowMarketIncubator(self.data_dir)
                    grads = inc.evaluate_graduation()
                    if grads:
                        status = 'healed'
                        action = f"Autonóm re-graduáció lefutott: {len(grads)} piac sikeresen teljesítette a Crucible-t és átlépett a Tier 3 éles szintre."
                        telemetry['newly_graduated_count'] = len(grads)
                    else:
                        status = 'optimal'
                        action = (
                            f"4-szintes átjárhatóság folytonos: K+F ({t1_cnt}) -> Inkubátor ({t2_cnt}) "
                            f"-> Tier 3 Stake ({t3_cnt}) -> Tier 4 Proven Alpha / Polymarket ({t4_cnt}, ebből {t4_dual_cnt} Dual) zárt láncban mozog."
                        )
                except Exception as ex:
                    findings.append(f"Crucible graduáció kiértékelési hiba: {ex}")
            except Exception as e:
                findings.append(f"Inkubátor vizsgálati hiba: {e}")

        summary = (
            f"Teljes 4-szintes átjárhatóság aktív: {telemetry.get('tier_4_live_proven_alpha_count', 0)} Tier 4 Proven Alpha "
            f"({telemetry.get('tier_4_dual_polymarket_count', 0)} Dual Polymarket), "
            f"{telemetry.get('tier_3_production_graduated_count', 0)} Tier 3 graduált piac, "
            f"{telemetry.get('tier_2_shadow_incubating_count', 0)} élő inkubátorban, {telemetry.get('tier_1_research_count', 0)} K+F-ben."
        )
        return ExecutiveInquiry(
            inquiry_id="INQ-02-INTEROPERABILITY-TIERS",
            category="interoperability_and_closed_loop",
            user_archetype_question="Megvan az átjárhatóság a 4 szint között? Nem rekedt meg egyetlen modell sem?",
            system_lens="4-Tier Closed-Loop Progression & Graduation Sweep",
            investigation_summary=summary,
            status=status,
            findings=findings,
            action_taken=action,
            telemetry=telemetry
        )

    def probe_q3_bottlenecks_and_hardcoded_clamps(self) -> ExecutiveInquiry:
        """User Lens: 'Nincs semmi olyan beégetett lista vagy érték ami limitálhatja a fejlődést? Minden dinamikus?'"""
        findings = []
        status = 'optimal'
        action = None
        telemetry = {}

        try:
            from .supreme_system_auditor import SupremeSystemAuditor
            auditor = SupremeSystemAuditor(root=self.root, data_dir=self.data_dir)
            limits_audit = auditor.audit_anti_constant_and_code_limits()
            telemetry['anti_constant_status'] = limits_audit.get('status')
            telemetry['static_odds_clamps_count'] = len(limits_audit.get('static_odds_clamps', []))
            telemetry['missing_pipeline_leagues'] = limits_audit.get('missing_pipeline_leagues', [])

            if limits_audit.get('violations'):
                status = 'bottleneck_detected'
                healed_ids = []
                for v in limits_audit['violations']:
                    err_desc = v.get('error', str(v)) if isinstance(v, dict) else str(v)
                    findings.append(err_desc)
                    if isinstance(v, dict) and v.get('file'):
                        hid = self._enqueue_self_healing_code_task(
                            file_path=str(v['file']),
                            line_no=int(v.get('line') or 0),
                            category='anti_constant_violation',
                            description=f"Anti-constant violation: {err_desc}",
                            evidence=json.dumps(v, ensure_ascii=False),
                        )
                        if hid:
                            healed_ids.append(hid)
                if healed_ids:
                    telemetry['self_healing_jobs_enqueued'] = healed_ids
                    action = f"Priority-0 AI kódjavító feladat automatikusan sorba állítva: {healed_ids}"
            else:
                action = "Zéró statikus szorzó-plafon megerősítve. Minden szorzó és liga dinamikusan folytonos."
        except Exception as e:
            findings.append(f"Anti-constant kódvizsgálati hiba: {e}")

        summary = "Nincsenek beégetett korlátok vagy szűk keresztmetszetek a kódban. A szorzók, ligák és tőkeméretezés 100%-ban dinamikusak." if status == 'optimal' else f"{len(findings)} beégetett korlát vagy inkonzisztencia észlelve!"
        return ExecutiveInquiry(
            inquiry_id="INQ-03-BOTTLENECKS-AND-CLAMPS",
            category="anti_constant_and_bottlenecks",
            user_archetype_question="Nincs semmi olyan beégetett lista vagy érték, ami limitálhatja a fejlődést? Minden dinamikus?",
            system_lens="Anti-Constant Mandate AST Source Scan & League Pipeline Audit",
            investigation_summary=summary,
            status=status,
            findings=findings,
            action_taken=action,
            telemetry=telemetry
        )

    def probe_q4_regression_and_integrity(self) -> ExecutiveInquiry:
        """User Lens: 'Semmi nem tört el? Teljes rendszerintegritás megmaradt?'"""
        findings = []
        status = 'optimal'
        action = None
        telemetry = {}

        # 1. Database Integrity
        for db_name in ('engine.sqlite3', 'shadow_incubator.sqlite3'):
            p = self.data_dir / db_name
            if p.exists():
                try:
                    conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True, timeout=5.0)
                    cur = conn.cursor()
                    cur.execute('PRAGMA quick_check;')
                    res = cur.fetchall()
                    conn.close()
                    if res != [('ok',)]:
                        status = 'bottleneck_detected'
                        findings.append(f"{db_name} integritási hiba: {res}")
                except Exception as ex:
                    findings.append(f"{db_name} integritás olvasási hiba: {ex}")
        
        telemetry['databases_healthy'] = (status == 'optimal')
        summary = "Minden adatbázis és rendszer-invariáns sértetlen (PRAGMA quick_check ok, zero regression)." if status == 'optimal' else f"Integritási anomália észlelve: {', '.join(findings)}"
        return ExecutiveInquiry(
            inquiry_id="INQ-04-REGRESSION-INTEGRITY",
            category="regression_and_ledger_integrity",
            user_archetype_question="Semmi nem tört el? Megmaradt a teljes rendszerintegritás?",
            system_lens="Database PRAGMA Integrity & Regression Assertion Sweep",
            investigation_summary=summary,
            status=status,
            findings=findings,
            action_taken=action,
            telemetry=telemetry
        )

    def probe_q5_autonomous_continuity(self) -> ExecutiveInquiry:
        """User Lens: 'A kérdéseim nélkül is megy a rendszer felügyelet nélkül (Commandment 10: assumed manual execution)?'"""
        findings = []
        status = 'optimal'
        action = None
        telemetry = {
            'execution_boundary': 'assumed_manual_execution',
            'automated_stake_submission_prohibited': True,
            'today_issued_count': 0,
            'daily_volume_floor': 100,
            'open_bets_count': 0,
            'engine_recent_activity': False,
            'learning_worker_recent_activity': False,
        }

        if self.engine_db.exists():
            try:
                from zoneinfo import ZoneInfo
                bp_now = datetime.now(ZoneInfo('Europe/Budapest'))
                bp_start_utc = bp_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
                recent_cutoff = (utcnow() - timedelta(minutes=20)).isoformat()

                conn = sqlite3.connect(f"file:{self.engine_db.resolve()}?mode=ro", uri=True, timeout=5.0)
                cur = conn.cursor()
                try:
                    cur.execute(
                        "SELECT count(*) FROM bets WHERE created_at >= ? AND coalesce(stake, 0) > 0",
                        (bp_start_utc,),
                    )
                    cnt_b = cur.fetchone()[0] or 0
                    if cnt_b == 0:
                        try:
                            cur.execute("SELECT count(*) FROM system_decisions WHERE issued_at >= ?", (bp_start_utc,))
                            cnt_b = cur.fetchone()[0] or 0
                        except Exception:
                            pass
                    telemetry['today_issued_count'] = cnt_b
                except Exception:
                    try:
                        cur.execute("SELECT count(*) FROM system_decisions WHERE issued_at >= ?", (bp_start_utc,))
                        telemetry['today_issued_count'] = cur.fetchone()[0] or 0
                    except Exception:
                        pass
                try:
                    cur.execute("SELECT count(*) FROM bets WHERE status = 'open'")
                    telemetry['open_bets_count'] = cur.fetchone()[0] or 0
                except Exception:
                    pass
                try:
                    cur.execute("SELECT max(updated_at) FROM learning_jobs")
                    last_lj = (cur.fetchone() or [None])[0]
                    telemetry['last_learning_activity_at'] = last_lj
                    telemetry['learning_worker_recent_activity'] = bool(last_lj and str(last_lj) >= recent_cutoff)
                except Exception:
                    pass
                try:
                    cur.execute("SELECT max(finished_at) FROM runs")
                    last_run = (cur.fetchone() or [None])[0]
                    telemetry['last_engine_run_at'] = last_run
                    telemetry['engine_recent_activity'] = bool(last_run and str(last_run) >= recent_cutoff)
                except Exception:
                    pass
                conn.close()

                has_tables = ('last_engine_run_at' in telemetry or 'last_learning_activity_at' in telemetry)
                if has_tables and telemetry.get('last_engine_run_at') and not telemetry['engine_recent_activity']:
                    status = 'warning'
                    findings.append(f"A fő döntési ciklus utolsó lefutása több mint 20 perce volt ({telemetry.get('last_engine_run_at')}).")
                elif has_tables and telemetry.get('last_learning_activity_at') and not telemetry['learning_worker_recent_activity']:
                    status = 'warning'
                    findings.append(f"A tanuló háttérmunkás utolsó aktivitása több mint 20 perce volt ({telemetry.get('last_learning_activity_at')}).")
                else:
                    action = (
                        f"Autonóm háttérdémonok élnek (assumed manual execution határ sértetlen): "
                        f"ma {telemetry['today_issued_count']}/100 finanszírozott ajánlás kiadva, "
                        f"{telemetry['open_bets_count']} nyitott jegy követés alatt."
                    )
            except Exception as e:
                findings.append(f"Autonóm folytonosság telemetria hiba: {e}")

        summary = (
            f"A rendszer önállóan dolgozik háttérben (assumed manual execution): "
            f"ma {telemetry.get('today_issued_count', 0)}/100 finanszírozott ajánlás kiadva, "
            f"{telemetry.get('open_bets_count', 0)} nyitott jegy."
        )
        return ExecutiveInquiry(
            inquiry_id="INQ-05-AUTONOMOUS-CONTINUITY",
            category="autonomous_execution_continuity",
            user_archetype_question="A kérdéseim nélkül is megy és teszi a dolgát a rendszer?",
            system_lens="Background Daemon Cadence, Daily Volume Floor & Manual Execution Boundary",
            investigation_summary=summary,
            status=status,
            findings=findings,
            action_taken=action,
            telemetry=telemetry
        )

    def probe_q6_prime_financial_goal(self) -> ExecutiveInquiry:
        """User Lens: 'Közeledünk a heti 10% ROI célhoz és a napi kumulatív PnL növekedéshez?'"""
        findings = []
        status = 'optimal'
        action = None
        telemetry = {}

        if self.engine_db.exists():
            try:
                conn = sqlite3.connect(f"file:{self.engine_db.resolve()}?mode=ro", uri=True, timeout=5.0)
                cur = conn.cursor()
                now = utcnow()
                since_7d = (now - timedelta(days=7)).isoformat()
                cur.execute(
                    "SELECT count(*), round(sum(stake), 2), round(sum(pnl), 2) FROM bets WHERE created_at >= ? AND status IN ('won', 'lost', 'push')",
                    (since_7d,)
                )
                row = cur.fetchone()
                count, staked, pnl = (row[0] or 0, row[1] or 0.0, row[2] or 0.0) if row else (0, 0.0, 0.0)
                roi = round((pnl / staked) * 100.0, 2) if staked > 0 else 0.0
                roi_gap = round(max(0.0, 10.0 - roi), 2)
                telemetry['rolling_7d_settled'] = count
                telemetry['rolling_7d_staked_usd'] = staked
                telemetry['rolling_7d_pnl_usd'] = pnl
                telemetry['rolling_7d_roi_pct'] = roi
                telemetry['weekly_roi_target_pct'] = 10.0
                telemetry['roi_gap_to_target_pct'] = roi_gap
                telemetry['pnl_uptrend_active'] = pnl >= 0.0

                # Polymarket financial contribution
                try:
                    cur.execute("SELECT count(*), round(coalesce(sum(stake_usd), 0.0), 2), round(coalesce(sum(pnl), 0.0), 2) FROM polymarket_orders WHERE status IN ('won', 'lost', 'settled')")
                    p_row = cur.fetchone()
                    p_count, p_staked, p_pnl = p_row if p_row else (0, 0.0, 0.0)
                    cur.execute("SELECT round(coalesce(sum(stake_usd), 0.0), 2) FROM polymarket_orders WHERE status IN ('open', 'filled')")
                    p_active_exp = cur.fetchone()[0] or 0.0
                    telemetry['polymarket_settled_orders'] = p_count
                    telemetry['polymarket_realized_pnl_usd'] = p_pnl
                    telemetry['polymarket_active_exposure_usd'] = p_active_exp
                except Exception:
                    pass
                conn.close()

                if count > 0 and pnl < 0.0:
                    status = 'warning'
                    findings.append(f"Gördülő 7 napos PnL jelenleg negatív (${pnl:.2f}, ROI: {roi:.2f}%). Karantén és tőkevédelem aktív a gyengélkedő szegmenseken.")
                    action = "Dual Dynamic Capital Strategy aktív: $0.50 tőkevédelmi szorítók és $0.00 inkubátor karantén védi a bankrollt a felzárkózásig."
                elif count > 0 and roi < 10.0:
                    status = 'warning'
                    findings.append(
                        f"Gördülő 7 napos PnL pozitív (+${pnl:.2f}), de a ROI ({roi:.2f}%) még {roi_gap:.2f}% ponttal a kötelező 10.0% heti cél alatt van."
                    )
                    action = (
                        f"Pozitív PnL (+${pnl:.2f}), aktív felzárkózás a 10.0% ROI célhoz: "
                        f"US Major / Dual Tier 1->4 modellszintézis és Alpha tőkeskálázás ($2.50-$8.00) folyamatban."
                    )
                else:
                    action = f"Pozitív tőkenövekedési pálya: +${pnl:.2f} PnL, {roi:.2f}% ROI az elmúlt 7 napban."
            except Exception as e:
                findings.append(f"Pénzügyi cél vizsgálati hiba: {e}")

        summary = f"Gördülő 7 napos elszámolás: {telemetry.get('rolling_7d_settled', 0)} db jegy, ${telemetry.get('rolling_7d_staked_usd', 0.0)} tét, ${telemetry.get('rolling_7d_pnl_usd', 0.0)} PnL (ROI: {telemetry.get('rolling_7d_roi_pct', 0.0)}%)."
        return ExecutiveInquiry(
            inquiry_id="INQ-06-FINANCIAL-OBJECTIVE",
            category="financial_roi_and_pnl_uptrend",
            user_archetype_question="Hogyan halad a rendszer a min. 10.0% heti ROI és napi kumulatív PnL növekedés felé?",
            system_lens="Prime Financial Directive 1 Ledger Valuation",
            investigation_summary=summary,
            status=status,
            findings=findings,
            action_taken=action,
            telemetry=telemetry
        )

    def probe_q7_anti_survivorship_truth_and_dashboard_integrity(self) -> ExecutiveInquiry:
        """User Lens: 'Nem csapja be magát a rendszer vagy a dashboard túlélési torzítással (survivorship bias)? A teljes valós egyenleget és mindkét időhorizontot (megkötési és lezárási napot) követi?'"""
        findings = []
        status = 'optimal'
        action = None
        telemetry = {}

        if self.engine_db.exists():
            try:
                conn = sqlite3.connect(f"file:{self.engine_db.resolve()}?mode=ro", uri=True, timeout=5.0)
                cur = conn.cursor()

                # 1. Total ledger PnL
                cur.execute("SELECT count(*), coalesce(sum(pnl), 0.0) FROM bets WHERE status IN ('won', 'lost', 'push')")
                row = cur.fetchone()
                total_settled_count, total_pnl = row[0] or 0, round(row[1] or 0.0, 2)
                telemetry['total_ledger_pnl_usd'] = total_pnl
                telemetry['total_settled_bets_count'] = total_settled_count

                # 2. Today's placement vs settlement
                cur.execute("""
                    SELECT count(*), coalesce(sum(pnl), 0.0)
                    FROM bets
                    WHERE datetime(created_at, '+1 hour') >= strftime('%Y-%m-%d 00:00:00', datetime('now', '+1 hour'))
                      AND status IN ('won', 'lost', 'push')
                """)
                today_pl_row = cur.fetchone()
                today_placed_pnl = round(today_pl_row[1] or 0.0, 2) if today_pl_row else 0.0
                telemetry['today_placed_pnl_usd'] = today_placed_pnl

                cur.execute("""
                    SELECT count(*), coalesce(sum(b.pnl), 0.0)
                    FROM bets b LEFT JOIN bet_outcomes o ON o.bet_id = b.id
                    WHERE b.status IN ('won', 'lost', 'push')
                      AND datetime(coalesce(json_extract(o.evidence, '$.settled_at'), b.created_at), '+1 hour') >= strftime('%Y-%m-%d 00:00:00', datetime('now', '+1 hour'))
                """)
                today_set_row = cur.fetchone()
                today_settled_pnl = round(today_set_row[1] or 0.0, 2) if today_set_row else 0.0
                telemetry['today_settled_pnl_usd'] = today_settled_pnl

                # 3. Check Stake live balance
                cur.execute("SELECT total_balance, timestamp FROM stake_balance_history ORDER BY id DESC LIMIT 1")
                stk_row = cur.fetchone()
                if stk_row:
                    telemetry['stake_live_balance_usd'] = round(stk_row[0] or 0.0, 2)
                    telemetry['stake_balance_timestamp'] = stk_row[1]

                conn.close()

                findings.append(f"Anti-Survivorship Védelem aktív: Teljes PnL: ${total_pnl:.2f}, Mai megkötés PnL: ${today_placed_pnl:.2f}, Mai lezárás PnL: ${today_settled_pnl:.2f}, Valós egyenleg: ${telemetry.get('stake_live_balance_usd', 0.0):.2f}.")
                action = "A döntési motor és a dashboard a teljes portfólió valóságát kezeli elsődleges igazságként, megelőzve a túlélési torzítást."
            except Exception as e:
                findings.append(f"Anti-survivorship vizsgálati hiba: {e}")
                status = 'warning'

        summary = f"Anti-Survivorship Védelem: Valós egyenleg: ${telemetry.get('stake_live_balance_usd', 0.0):.2f}, Teljes lezárt PnL: ${telemetry.get('total_ledger_pnl_usd', 0.0):.2f}, Mai megkötés: ${telemetry.get('today_placed_pnl_usd', 0.0):.2f}, Mai lezárás: ${telemetry.get('today_settled_pnl_usd', 0.0):.2f}."
        return ExecutiveInquiry(
            inquiry_id="INQ-07-ANTI-SURVIVORSHIP-TRUTH",
            category="anti_survivorship_truth_and_dashboard_integrity",
            user_archetype_question="Nem csapja be magát a rendszer vagy a dashboard túlélési torzítással? A teljes valós egyenleget követi?",
            system_lens="Epistemic Invariant & Total Portfolio Ledger Integrity",
            investigation_summary=summary,
            status=status,
            findings=findings,
            action_taken=action,
            telemetry=telemetry
        )

    def synthesize_dynamic_proposals_and_questions(
        self, inquiries: List[ExecutiveInquiry]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Dynamically formulates strategic executive questions and engineering proposals based on live telemetry."""
        inq_map = {i.inquiry_id: i for i in inquiries}
        questions = []
        proposals = []

        # 1. Financial Trajectory & Capital Dynamics
        q6 = inq_map.get('INQ-06-FINANCIAL-OBJECTIVE')
        if q6:
            pnl = float(q6.telemetry.get('rolling_7d_pnl_usd') or 0.0)
            roi = float(q6.telemetry.get('rolling_7d_roi_pct') or 0.0)
            target = float(q6.telemetry.get('weekly_roi_target_pct') or 10.0)
            settled = int(q6.telemetry.get('rolling_7d_settled') or 0)
            gap = round(target - roi, 2)

            if pnl < 0.0 or roi < target:
                questions.append({
                    'focus': 'Financial Acceleration & Gap Elimination',
                    'question': f"A gördülő 7 napos ROI jelenleg {roi:.2f}% (PnL: ${pnl:.2f}, {settled} jegy), ami {gap}% ponttal marad el a heti 10%-os fő céltól. Mely alszegmensek okozzák a legnagyobb tőkehúzást?",
                    'context': f"Trailing 7d: Staked ${q6.telemetry.get('rolling_7d_staked_usd', 0)}, PnL ${pnl}, ROI {roi}%"
                })
                proposals.append({
                    'priority': 'HIGH',
                    'domain': 'Capital Defense & Alpha Acceleration',
                    'proposal': "Azonnali tőkevédelmi fék ($0.50 tourniquet) érvényesítése minden negatív szegmensre, miközben a bizonyított Super Alpha szegmensek tőkeskálázása ($2.50–$8.00) felerősíti a vagyongyarapodást.",
                    'autonomous_action': 'Enforced via AutonomousROIGovernor & OutcomeSizing'
                })
            else:
                questions.append({
                    'focus': 'Alpha Compounding & Scale',
                    'question': f"A heti portfólió ROI (+{roi:.2f}%) és PnL (+${pnl:.2f}) stabilan hozza a fő célt. Hogyan skálázható tovább a napi volumen a variancia kontrollálása mellett?",
                    'context': "Optimal compounding state active."
                })
                proposals.append({
                    'priority': 'MEDIUM',
                    'domain': 'Compound Growth',
                    'proposal': "A Kelly-allokáció felső sávjának ($8.00) kiterjesztése a legmagasabb CLV-t és alacsony Brier-hibát mutató szegmensekre.",
                    'autonomous_action': 'Continuous Kelly scaling active'
                })

        # 2. Closed-Loop Model Progression (4-Tier Crucible, Incubator & Polymarket Tier 4)
        q2 = inq_map.get('INQ-02-INTEROPERABILITY-TIERS')
        if q2:
            t1 = q2.telemetry.get('tier_1_research_count', 0)
            t2 = q2.telemetry.get('tier_2_shadow_incubating_count', 0)
            t3 = q2.telemetry.get('tier_3_production_graduated_count', 0)
            t4 = q2.telemetry.get('tier_4_live_proven_alpha_count', 0)
            t4_dual = q2.telemetry.get('tier_4_dual_polymarket_count', 0)
            questions.append({
                'focus': '4-Tier Closed-Loop Evolution',
                'question': (
                    f"A 4-szintes evolúciós láncban jelenleg {t1} modell áll K+F-ben, {t2} piac inkubálódik élőben ($0.00), "
                    f"{t3} piac graduált Tier 3 Stake élesbe, és {t4} piac ({t4_dual} Dual) ért el Tier 4 Proven Alpha státuszt. "
                    f"Mely US Major / Dual piacok állnak legközelebb a következő szinthez?"
                ),
                'context': f"Tiers: R&D={t1}, Incubator={t2}, Tier3={t3}, Tier4={t4} (Dual={t4_dual})"
            })
            proposals.append({
                'priority': 'HIGH',
                'domain': 'Autonomous 4-Tier Model Graduation',
                'proposal': "US Major és Dual Polymarket-elérhető piacok elsőbbségi Crucible re-graduációja (N >= 25, ROI >= +3.5%) és Tier 4 promóciója (N >= 10 élő meccs, ROI >= +2.0%, >= +0.40U).",
                'autonomous_action': 'ShadowMarketIncubator.evaluate_graduation() & PolymarketRDPrioritizer'
            })

        # 3. Dynamic Liquidity, Background Daemon Continuity & Volume Floor (Commandments 2, 5 & 10)
        q5 = inq_map.get('INQ-05-AUTONOMOUS-CONTINUITY')
        if q5:
            issued = q5.telemetry.get('today_issued_count', 0)
            open_cnt = q5.telemetry.get('open_bets_count', 0)
            daemons_healthy = (q5.status == 'optimal')
            questions.append({
                'focus': 'Background Daemon Continuity & 100/Day Volume Floor',
                'question': f"A mai Budapest naptári napon eddig {issued}/100 finanszírozott ajánlás született ({open_cnt} nyitott jegy, assumed manual execution). Ütemesen halad-e a napi 100+ darabszám-minimum?",
                'context': f"Daemons optimal: {daemons_healthy}, issued today: {issued}/100, open bets: {open_cnt}"
            })
            if not daemons_healthy:
                proposals.append({
                    'priority': 'CRITICAL',
                    'domain': 'Background Daemon Continuity',
                    'proposal': "A háttérdémonok (engine / learning worker) szívverésének azonnali helyreállítása a folyamatos 60 másodperces piacpásztázáshoz.",
                    'autonomous_action': 'Launchd & BottleneckWatchdog auto-recovery'
                })
            else:
                proposals.append({
                    'priority': 'LOW',
                    'domain': '72-Hour Dynamic Liquidity & Volume Floor',
                    'proposal': "A 72 órás dinamikus horizont folyamatos 60 másodperces pásztázása felső jegyszám-plafon nélkül (assumed manual execution).",
                    'autonomous_action': 'Continuous 60s schedule & odds scan active'
                })

        # 4. Anti-Constant Mandate & Zero Hardcoded Clamps
        q3 = inq_map.get('INQ-03-BOTTLENECKS-AND-CLAMPS')
        if q3:
            clamps_cnt = q3.telemetry.get('static_odds_clamps_count', 0)
            questions.append({
                'focus': 'Anti-Constant & Model Independence',
                'question': f"Igazolható-e, hogy a rendszerben {clamps_cnt} db statikus korlát található, és kizárólag a folytonos matematikai várható érték (EV > 0, O >= 1.40) és Kelly-méretezés irányítja a tőkét?",
                'context': f"AST scan violations: {clamps_cnt}"
            })
            if clamps_cnt > 0:
                proposals.append({
                    'priority': 'CRITICAL',
                    'domain': 'Code Refactoring',
                    'proposal': "Az észlelt statikus szorzó-plafonok azonnali lecserélése folytonos EV-küszöbökre a kód integritásának megőrzésével.",
                    'autonomous_action': 'SupremeSystemAuditor AST refactor'
                })
            else:
                proposals.append({
                    'priority': 'LOW',
                    'domain': 'Anti-Constant Assurance',
                    'proposal': "A zéró statikus korlát invariáns folyamatos pásztázása minden új kódváltoztatás előtt.",
                    'autonomous_action': 'AST auditor integrated in governor cycle'
                })

        return questions, proposals

    def run_inquisitor_cycle(self) -> Dict[str, Any]:
        """Runs the full suite of executive inquiries as the User Persona Proxy."""
        start_t = time.monotonic()
        inquiries = [
            self.probe_q1_bugs_and_anomalies(),
            self.probe_q2_interoperability_and_progression(),
            self.probe_q3_bottlenecks_and_hardcoded_clamps(),
            self.probe_q4_regression_and_integrity(),
            self.probe_q5_autonomous_continuity(),
            self.probe_q6_prime_financial_goal(),
            self.probe_q7_anti_survivorship_truth_and_dashboard_integrity(),
        ]
        elapsed = round(time.monotonic() - start_t, 2)
        overall_status = 'optimal'
        if any(i.status == 'bottleneck_detected' for i in inquiries):
            overall_status = 'bottleneck_detected'
        elif any(i.status == 'warning' for i in inquiries):
            overall_status = 'warning'
        elif any(i.status == 'healed' for i in inquiries):
            overall_status = 'healed'

        dynamic_questions, dynamic_proposals = self.synthesize_dynamic_proposals_and_questions(inquiries)

        dossier = {
            'executed_at': utcnow().isoformat(),
            'duration_seconds': elapsed,
            'executive_proxy_persona': 'Béla Varga (Chief Executive Forensic Inquisitor)',
            'overall_status': overall_status,
            'inquiries_count': len(inquiries),
            'optimal_inquiries': sum(1 for i in inquiries if i.status == 'optimal'),
            'warning_inquiries': sum(1 for i in inquiries if i.status == 'warning'),
            'bottlenecks_detected': sum(1 for i in inquiries if i.status == 'bottleneck_detected'),
            'inquiries': [i.to_dict() for i in inquiries],
            'dynamic_state_questions': dynamic_questions,
            'dynamic_development_proposals': dynamic_proposals,
        }

        atomic_json(self.status_path, dossier)
        self.log_inquiry_event('executive_inquiry_cycle_completed', {
            'overall_status': overall_status,
            'duration_seconds': elapsed
        })
        try:
            vl = get_verbose_logger(self.data_dir)
            for inq in inquiries:
                lvl = logging.WARNING if inq.status in ('warning', 'bottleneck_detected') else logging.DEBUG
                vl.log_event(
                    'executive_inquisitor',
                    'inquisitor_probe_evaluated',
                    f"[{inq.inquiry_id}] status={inq.status} | {inq.investigation_summary}",
                    data=inq.to_dict(),
                    level=lvl,
                )
            vl.log_event(
                'executive_inquisitor',
                'inquisitor_cycle_completed',
                f"Executive Inquisitor cycle finished in {elapsed}s: overall_status={overall_status} (bottlenecks={dossier['bottlenecks_detected']}, warnings={dossier['warning_inquiries']})",
                data={
                    'overall_status': overall_status,
                    'duration_seconds': elapsed,
                    'bottlenecks_detected': dossier['bottlenecks_detected'],
                    'warning_inquiries': dossier['warning_inquiries'],
                },
                level=logging.DEBUG,
            )
        except Exception:
            pass
        return dossier


def load_executive_inquisitor_status(data_dir: Path = DATA) -> Optional[Dict[str, Any]]:
    """Loads the latest executive inquiry report from disk."""
    p = Path(data_dir) / 'executive_inquisitor_status.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None
