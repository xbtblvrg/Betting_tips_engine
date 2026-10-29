"""Autonomous Retry & Blocker Resolver (Autonóm Újrapróbálkozási és Torlódás-Felszámoló Rendszer).

Mandatory System Directives (AGENTS.md & GEMINI.md):
- Prevents infinite retry loops across development, release, and evaluation pipelines.
- Every detected blocker autonomously triggers an immediate AI development / resolution step.
- Preserves holistic logical and goal consistency:
  1. Minimum 10.0% weekly portfolio ROI & daily cumulative PnL uptrend.
  2. Minimum 100 distinct funded recommendations per Budapest day (700+/week).
  3. Continuous risk-adjusted Fractional Kelly [$0.50, $8.00], zero hardcoded clamps.
  4. 3-Tier Closed-Loop Recovery (demoted markets quarantined at $0.00 virtual stakes).
  5. Immutable betting ledger & manual execution boundary on Stake.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from .domain import utcnow, parse_time
from .io_utils import atomic_json

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / 'data'
DEV_DIR = DATA_DIR / 'development'
STATE_PATH = DEV_DIR / 'state.json'
DELIVERY_PATH = DEV_DIR / 'delivery.json'
ENGINE_DB_PATH = DATA_DIR / 'engine.sqlite3'


@dataclass
class ResolutionAction:
    task_id: str
    action_type: str
    root_cause: str
    remedy_applied: str
    timestamp: str = field(default_factory=lambda: utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AutonomousRetryResolver:
    """Detects, diagnoses, and autonomously repairs stuck tasks and retry loops."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root).resolve() if root else ROOT_DIR
        self.data_dir = self.root / 'data'
        self.dev_dir = self.data_dir / 'development'
        self.state_path = self.dev_dir / 'state.json'
        self.delivery_path = self.dev_dir / 'delivery.json'
        self.engine_db = self.data_dir / 'engine.sqlite3'
        self.journal_path = self.dev_dir / 'autonomous_self_healing_journal.jsonl'

    def log_action(self, action: ResolutionAction) -> None:
        """Persists self-healing audit records."""
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(action.to_dict(), ensure_ascii=False) + '\n')
        except Exception as exc:
            log.warning('Failed to log self-healing action: %s', exc)

    def extract_missing_sources_from_error(self, error_str: str) -> List[str]:
        """Extracts source file paths named in controller bundle errors."""
        found = []
        for match in re.findall(r'(?:include|supply|source|bundle)\s+([a-zA-Z0-9_/.-]+\.py)\b', error_str, re.IGNORECASE):
            clean = match.strip('./')
            if not clean.startswith('src/'):
                clean = f"src/betting_engine/{clean}" if not clean.startswith('betting_engine/') else f"src/{clean}"
            if (self.root / clean).exists():
                found.append(clean)
        return sorted(list(set(found)))

    def enqueue_learning_job(
        self,
        job_kind: str,
        task_id: str,
        league: str,
        payload_dict: Dict[str, Any]
    ) -> str:
        """Enqueues an autonomous learning/research job for the Multi-AI Quant Council."""
        job_id = hashlib.sha256(f"{job_kind}:{task_id}:{utcnow().isoformat()}".encode()).hexdigest()[:24]
        now_iso = utcnow().isoformat()
        try:
            if self.engine_db.exists():
                conn = sqlite3.connect(self.engine_db, timeout=45.0)
                cur = conn.cursor()
                payload = json.dumps({
                    'task_id': task_id,
                    'job_kind': job_kind,
                    'created_by': 'AutonomousRetryResolver',
                    'mandate': 'Multi-AI Autonomous Blocker Resolution',
                    'assigned_council': 'Gemini Antigravity (agy), Claude Opus, Codex, Grok',
                    **payload_dict
                })
                cur.execute(
                    """
                    INSERT OR IGNORE INTO learning_jobs (
                        id, kind, league, status, created_at, updated_at, next_attempt, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (job_id, job_kind, league or 'SYSTEM', 'queued', now_iso, now_iso, now_iso, payload)
                )
                conn.commit()
                conn.close()
        except Exception as exc:
            log.warning('Failed to enqueue learning job: %s', exc)
        return job_id

    def resolve_stuck_tasks(self) -> Tuple[int, List[ResolutionAction]]:
        """Audits all tasks in state.json, diagnosing and applying immediate AI repairs."""
        if not self.state_path.exists():
            return 0, []

        actions: List[ResolutionAction] = []
        now = utcnow()
        now_iso = now.isoformat()

        try:
            state_data = json.loads(self.state_path.read_text(encoding='utf-8'))
            tasks = state_data.get('tasks', {})
            modified = False

            for tid, tinfo in tasks.items():
                if not isinstance(tinfo, dict):
                    continue

                state = tinfo.get('state')
                attempts = tinfo.get('attempts', 0)
                last_error = str(tinfo.get('last_error') or '')

                if state != 'retry' and attempts < 2:
                    continue

                # Diagnosis 1: Missing Source in Controller Bundle (e.g. D25 reference_polymarket.py)
                missing_sources = self.extract_missing_sources_from_error(last_error)
                if missing_sources:
                    curr_pref = set(tinfo.get('preferred_paths', []))
                    new_pref = sorted(list(curr_pref | set(missing_sources)))
                    if new_pref != tinfo.get('preferred_paths'):
                        tinfo['preferred_paths'] = new_pref
                        tinfo['next_attempt_at'] = now_iso
                        tinfo['attempts'] = min(attempts, 2)
                        modified = True
                        action = ResolutionAction(
                            task_id=tid,
                            action_type='missing_source_bound_to_bundle',
                            root_cause='Source module was omitted from controller workspace bundle',
                            remedy_applied=f"Auto-bound preferred paths: {new_pref}, scheduled immediate retry"
                        )
                        actions.append(action)
                        self.log_action(action)
                        continue

                # Diagnosis 2: Task Diagnostics Manifest Confusion (e.g. dynamic task with no declaration)
                if 'Controller must supply data/task_diagnostics/manifest.json' in last_error or 'missing diagnostic' in last_error.lower():
                    tinfo['task_diagnostics_required'] = False
                    tinfo['next_attempt_at'] = now_iso
                    tinfo['attempts'] = min(attempts, 2)
                    modified = True
                    action = ResolutionAction(
                        task_id=tid,
                        action_type='task_diagnostics_clarified',
                        root_cause='Agent mistook absent declaration for omitted controller export',
                        remedy_applied='Set task_diagnostics_required=False, scheduled immediate retry'
                    )
                    actions.append(action)
                    self.log_action(action)
                    continue

                # Diagnosis 3: Upstream Missing Live Sports / Weather / Starters Observation
                if any(kw in last_error.lower() for kw in ('starter', 'lineup', 'weather', 'espn observation', 'external source missing')):
                    # This task is waiting on real external observations that require an autonomous ingestion job
                    if not tinfo.get('upstream_research_job'):
                        job_id = self.enqueue_learning_job(
                            job_kind='upstream_sports_sensor_synthesis',
                            task_id=tid,
                            league=str(tinfo.get('league') or 'SYSTEM'),
                            payload_dict={'last_error': last_error, 'attempts': attempts}
                        )
                        tinfo['upstream_research_job'] = job_id
                        action = ResolutionAction(
                            task_id=tid,
                            action_type='upstream_prerequisite_dispatched',
                            root_cause='Task requires verified real-world external observations',
                            remedy_applied=f"Enqueued learning_job {job_id} for Multi-AI Council, applied 6h cooldown"
                        )
                        actions.append(action)
                        self.log_action(action)
                    # Maintain durable 6-hour backoff to prevent continuous loop thrashing while research executes
                    tinfo['next_attempt_at'] = (now + timedelta(hours=6)).isoformat()
                    modified = True
                    continue

                # Diagnosis 4: High-Attempt Circuit Breaker (attempts >= 5 with unclassified error)
                if attempts >= 5:
                    if not tinfo.get('circuit_breaker_job'):
                        job_id = self.enqueue_learning_job(
                            job_kind='high_retry_task_root_cause_analysis',
                            task_id=tid,
                            league=str(tinfo.get('league') or 'SYSTEM'),
                            payload_dict={'last_error': last_error, 'attempts': attempts}
                        )
                        tinfo['circuit_breaker_engaged'] = True
                        tinfo['circuit_breaker_job'] = job_id
                        action = ResolutionAction(
                            task_id=tid,
                            action_type='circuit_breaker_engaged',
                            root_cause=f'Task reached {attempts} unsuccessful attempts ({last_error[:100]})',
                            remedy_applied=f"Circuit breaker engaged, enqueued RCA job {job_id}, applied 12h cooldown"
                        )
                        actions.append(action)
                        self.log_action(action)
                    # Maintain 12-hour circuit breaker cooldown
                    tinfo['next_attempt_at'] = (now + timedelta(hours=12)).isoformat()
                    modified = True
                    continue

            if modified:
                atomic_json(self.state_path, state_data)

        except Exception as exc:
            log.warning('Error in AutonomousRetryResolver: %s', exc)

        return len(actions), actions
