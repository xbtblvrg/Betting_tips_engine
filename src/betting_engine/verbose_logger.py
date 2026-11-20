"""High-fidelity, verbose debug-level quant telemetry and logging infrastructure.

Provides structured JSONL and formatted rotating log streams capturing every granular
detail of models, decisions, Kelly sizing, Crucible evaluations, and runtime anomalies.
Enriches AI autonomous learning and self-healing prompts with real runtime telemetry.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

from .domain import utcnow

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BETTING_DATA_DIR", ROOT / "data"))
if not DATA.is_absolute():
    DATA = ROOT / DATA

VERBOSE_LOG_NAME = "verbose_debug.log"
VERBOSE_JSONL_NAME = "verbose_debug.jsonl"
SELF_HEALING_TRIGGERS_NAME = "self_healing_triggers.jsonl"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
DEFAULT_BACKUP_COUNT = 5
TRIGGER_COOLDOWN_SECONDS = 300.0

_LOCK = threading.RLock()
_INITIALIZED = False
_TRIGGER_COOLDOWN: Dict[str, float] = {}
_IN_TRIGGER_HOOK = threading.local()


class JsonlLogHandler(logging.Handler):
    """Appends structured JSONL log records for machine ingestion and AI analysis,
    and triggers the real-time autonomous AI self-healing loop on runtime errors.
    """

    def __init__(self, filepath: Path, max_bytes: int = DEFAULT_MAX_BYTES):
        super().__init__()
        self.filepath = Path(filepath)
        self.max_bytes = max_bytes
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.triggers_filepath = self.filepath.parent / SELF_HEALING_TRIGGERS_NAME
        self.data_dir = self.filepath.parent.parent

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            msg = self.format(record)
            entry: Dict[str, Any] = {
                "timestamp": utcnow().isoformat(),
                "epoch_ns": time.time_ns(),
                "level": record.levelname,
                "logger": record.name,
                "module": record.module,
                "func": record.funcName,
                "lineno": record.lineno,
                "process": record.process,
                "thread": record.threadName,
                "message": msg,
            }
            if record.exc_info and record.exc_info[0]:
                entry["exception"] = {
                    "type": record.exc_info[0].__name__,
                    "message": str(record.exc_info[1]),
                    "traceback": "".join(traceback.format_exception(*record.exc_info)),
                }
            if hasattr(record, "quant_data") and isinstance(record.quant_data, dict):
                entry["quant_data"] = record.quant_data
            if hasattr(record, "component"):
                entry["component"] = str(record.component)
            if hasattr(record, "event_type"):
                entry["event_type"] = str(record.event_type)

            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(line)

            # Real-Time Log-Triggered AI Self-Healing Loop:
            # Intercept ERROR/CRITICAL records or tracebacks from betting_engine and enqueue
            # immediate Priority-0 autonomous AI code healing.
            if (
                (record.levelno >= logging.ERROR or "exception" in entry or "Traceback (most recent call last):" in msg)
                and not getattr(record, "_skip_self_healing", False)
                and not getattr(_IN_TRIGGER_HOOK, "active", False)
            ):
                self._trigger_self_healing_from_log(record, entry)
        except Exception:
            self.handleError(record)

    def _trigger_self_healing_from_log(self, record: logging.LogRecord, entry: Dict[str, Any]) -> None:
        """Extracts frame & debug context from an error log and triggers Priority-0 AI self-healing."""
        _IN_TRIGGER_HOOK.active = True
        try:
            exc_info_dict = entry.get("exception") or {}
            tb_text = exc_info_dict.get("traceback") or (entry.get("message") if "Traceback" in str(entry.get("message")) else "")
            exc_type = exc_info_dict.get("type") or "RuntimeError"
            exc_msg = exc_info_dict.get("message") or str(entry.get("message") or "")[:300]

            target_file = getattr(record, "pathname", "") or ""
            target_line = int(getattr(record, "lineno", 0) or 0)
            target_func = getattr(record, "funcName", "") or ""
            code_line = ""

            if tb_text:
                frame_pattern = re.compile(r'  File "([^"]+)", line (\d+), in ([a-zA-Z0-9_<>]+)\n    ([^\n]+)')
                for fm in frame_pattern.finditer(tb_text):
                    fp, lno, fn, cline = fm.groups()
                    p_cand = Path(fp).resolve()
                    if "src/betting_engine" in str(p_cand):
                        target_file = str(p_cand)
                        target_line = int(lno)
                        target_func = fn
                        code_line = cline.strip()

            if not target_file or "src/betting_engine" not in str(Path(target_file).resolve()):
                return

            rel_file = str(Path(target_file).resolve().relative_to(ROOT)) if str(Path(target_file).resolve()).startswith(str(ROOT)) else target_file
            sig_key = f"{rel_file}:{target_line}:{exc_type}"
            now_mono = time.monotonic()
            with _LOCK:
                last_trig = _TRIGGER_COOLDOWN.get(sig_key, 0.0)
                if (now_mono - last_trig) < TRIGGER_COOLDOWN_SECONDS:
                    return
                _TRIGGER_COOLDOWN[sig_key] = now_mono

            now_iso = utcnow().isoformat()
            job_id = hashlib.sha256(f"log_trigger_heal:{sig_key}:{exc_msg[:60]}".encode()).hexdigest()[:24]
            trigger_payload = {
                "job_kind": "runtime_exception_auto_repair",
                "trigger_source": "verbose_logger_realtime_hook",
                "triggered_at": now_iso,
                "file": rel_file,
                "line": target_line,
                "function": target_func,
                "code_line": code_line,
                "exception_type": exc_type,
                "exception_message": exc_msg[:400],
                "traceback": tb_text[-4000:] if tb_text else "",
                "logger": entry.get("logger"),
                "quant_data": entry.get("quant_data", {}),
            }

            # 1. Persist durable trigger receipt to self_healing_triggers.jsonl
            try:
                with open(self.triggers_filepath, "a", encoding="utf-8") as tf:
                    tf.write(json.dumps({"job_id": job_id, **trigger_payload}, ensure_ascii=False) + "\n")
            except Exception:
                pass

            # 2. Enqueue Priority-0 fast-track job in engine.sqlite3 (learning_jobs)
            # Skip synchronous SQLite write if the exception itself is a transient SQLite lock
            engine_db = self.data_dir / "engine.sqlite3"
            if engine_db.exists() and "database is locked" not in exc_msg.lower():
                try:
                    with closing(sqlite3.connect(str(engine_db), timeout=1.5)) as conn:
                        conn.execute("PRAGMA busy_timeout = 1500")
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO learning_jobs (
                                id, kind, league, status, created_at, updated_at, next_attempt, attempts, payload
                            ) VALUES (?, 'runtime_exception_auto_repair', 'SYSTEM', 'queued', ?, ?, ?, 0, ?)
                            """,
                            (job_id, now_iso, now_iso, now_iso, json.dumps(trigger_payload, ensure_ascii=False)),
                        )
                        conn.commit()
                except Exception:
                    pass
        finally:
            _IN_TRIGGER_HOOK.active = False


class QuantVerboseLogger:
    """Singleton controller managing verbose logging and AI telemetry extraction."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir).resolve() if data_dir else DATA
        self.logs_dir = self.data_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.logs_dir / VERBOSE_LOG_NAME
        self.jsonl_file = self.logs_dir / VERBOSE_JSONL_NAME
        self.text_handler: Optional[RotatingFileHandler] = None
        self.jsonl_handler: Optional[JsonlLogHandler] = None
        self._init_logging()

    def _init_logging(self) -> None:
        with _LOCK:
            # Text rotating handler
            self.text_handler = RotatingFileHandler(
                str(self.log_file),
                maxBytes=DEFAULT_MAX_BYTES,
                backupCount=DEFAULT_BACKUP_COUNT,
                encoding="utf-8",
            )
            formatter = logging.Formatter(
                "%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s:%(funcName)s:%(lineno)d] "
                "[P:%(process)d|T:%(threadName)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            self.text_handler.setFormatter(formatter)
            self.text_handler.setLevel(logging.DEBUG)

            # JSONL structured handler
            self.jsonl_handler = JsonlLogHandler(self.jsonl_file, max_bytes=DEFAULT_MAX_BYTES)
            self.jsonl_handler.setLevel(logging.DEBUG)

            # Configure root and betting_engine loggers
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.DEBUG)
            be_logger = logging.getLogger("betting_engine")
            be_logger.setLevel(logging.DEBUG)

            be_logger.addHandler(self.text_handler)
            be_logger.addHandler(self.jsonl_handler)

    def close(self) -> None:
        """Closes and removes handlers associated with this instance."""
        be_logger = logging.getLogger("betting_engine")
        if self.text_handler and self.text_handler in be_logger.handlers:
            be_logger.removeHandler(self.text_handler)
            self.text_handler.close()
        if self.jsonl_handler and self.jsonl_handler in be_logger.handlers:
            be_logger.removeHandler(self.jsonl_handler)
            self.jsonl_handler.close()

    # -------------------------------------------------------------------------
    # Quant Telemetry API
    # -------------------------------------------------------------------------
    def log_event(
        self,
        component: str,
        event_type: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        level: int = logging.DEBUG,
        exc_info: bool = False,
    ) -> None:
        """Emits a structured quant event record to both text and JSONL logs."""
        logger = logging.getLogger(f"betting_engine.{component}")
        extra = {
            "component": component,
            "event_type": event_type,
            "quant_data": data or {},
        }
        logger.log(level, f"[{component.upper()}:{event_type}] {message}", extra=extra, exc_info=exc_info)

    def trace_decision(
        self,
        candidate_id: str,
        fixture: str,
        market: str,
        prob: float,
        odds: float,
        ev: float,
        kelly_fraction: float,
        final_stake: float,
        accepted: bool,
        rejection_reason: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Traces the complete mathematical decision process for a betting contract."""
        data = {
            "candidate_id": candidate_id,
            "fixture": fixture,
            "market": market,
            "model_probability": round(prob, 5),
            "market_odds": round(odds, 3),
            "ev": round(ev, 5),
            "kelly_fraction": round(kelly_fraction, 5),
            "final_stake_usd": round(final_stake, 2),
            "accepted": accepted,
            "rejection_reason": rejection_reason,
            **(details or {}),
        }
        msg = (
            f"Candidate {candidate_id} ({fixture} | {market}): P={prob:.3f}, Odds={odds:.2f}, "
            f"EV={ev:+.3%}, Kelly={kelly_fraction:.4f}, Stake=${final_stake:.2f} -> "
            f"{'ACCEPTED' if accepted else f'REJECTED ({rejection_reason})'}"
        )
        self.log_event("decisions", "candidate_evaluation", msg, data=data, level=logging.DEBUG)

    def trace_sizing(
        self,
        candidate_id: str,
        raw_kelly: float,
        bounded_stake: float,
        scaling_factors: Dict[str, float],
        applied_clamps: List[str],
    ) -> None:
        """Traces the continuous risk-adjusted Kelly sizing calculations."""
        data = {
            "candidate_id": candidate_id,
            "raw_kelly": round(raw_kelly, 6),
            "bounded_stake_usd": round(bounded_stake, 2),
            "scaling_factors": {k: round(v, 4) for k, v in scaling_factors.items()},
            "applied_clamps": applied_clamps,
        }
        msg = f"Sizing for {candidate_id}: raw={raw_kelly:.5f} -> final=${bounded_stake:.2f} (clamps: {applied_clamps})"
        self.log_event("outcome_sizing", "stake_calculation", msg, data=data, level=logging.DEBUG)

    def trace_crucible(
        self,
        segment: str,
        model_name: str,
        phase: str,
        metric: str,
        value: float,
        threshold: float,
        passed: bool,
        sample_size: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Traces statistical validation checks in the Walk-Forward Crucible."""
        data = {
            "segment": segment,
            "model_name": model_name,
            "crucible_phase": phase,
            "metric": metric,
            "measured_value": round(value, 5),
            "required_threshold": round(threshold, 5),
            "passed": passed,
            "sample_size": sample_size,
            **(details or {}),
        }
        msg = (
            f"Crucible check for {segment} [{model_name}]: {metric}={value:.4f} vs threshold={threshold:.4f} "
            f"(N={sample_size}) -> {'PASSED' if passed else 'FAILED'}"
        )
        self.log_event("crucible", "rule_evaluation", msg, data=data, level=logging.DEBUG)

    def trace_runtime_anomaly(
        self,
        component: str,
        anomaly_type: str,
        details: Dict[str, Any],
        exc: Optional[BaseException] = None,
    ) -> None:
        """Traces runtime exceptions, locking delays, and unexpected pipeline degradations."""
        msg = f"Anomaly in {component} [{anomaly_type}]: {details}"
        self.log_event(
            component,
            "runtime_anomaly",
            msg,
            data={"anomaly_type": anomaly_type, **details},
            level=logging.ERROR if exc else logging.WARNING,
            exc_info=bool(exc),
        )

    # -------------------------------------------------------------------------
    # AI Cognitive Telemetry Retrieval & Enrichment
    # -------------------------------------------------------------------------
    def get_recent_telemetry(
        self,
        component: Optional[str] = None,
        event_type: Optional[str] = None,
        min_level: Optional[str] = None,
        limit: int = 50,
        max_bytes: int = 65536,
    ) -> List[Dict[str, Any]]:
        """Retrieves recent structured events from verbose_debug.jsonl within a size bound."""
        if not self.jsonl_file.exists():
            return []

        level_weights = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
        min_weight = level_weights.get((min_level or "DEBUG").upper(), 10)

        results: List[Dict[str, Any]] = []
        try:
            with open(self.jsonl_file, "r", encoding="utf-8", errors="replace") as f:
                # Read from tail efficiently
                f.seek(0, os.SEEK_END)
                size = f.tell()
                read_size = min(size, max_bytes * 4)
                f.seek(max(0, size - read_size))
                lines = f.readlines()

            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue

                if component and entry.get("component") != component:
                    continue
                if event_type and entry.get("event_type") != event_type:
                    continue
                if level_weights.get(entry.get("level", "DEBUG"), 10) < min_weight:
                    continue

                results.append(entry)
                if len(results) >= limit:
                    break
        except Exception:
            pass

        return list(reversed(results))

    def format_telemetry_for_ai(
        self,
        task: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None,
        max_chars: int = 12000,
    ) -> str:
        """Formats real verbose debug telemetry, recent errors, and execution traces for AI prompts.

        Zero prompt anchoring: Only raw empirical facts, observed error traces,
        mathematical states, and recent log records are presented.
        """
        sections: List[str] = []

        # 1. Error logs from service-error.log, supervisor-error.log, learning-error.log, and self_healing_triggers.jsonl if present
        err_logs = [
            self.logs_dir / "service-error.log",
            self.logs_dir / "supervisor-error.log",
            self.logs_dir / "learning-error.log",
            self.logs_dir / SELF_HEALING_TRIGGERS_NAME,
        ]
        error_snippets: List[str] = []
        for p in err_logs:
            if p.exists() and p.stat().st_size > 0:
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    tail = content[-2500:].strip()
                    if tail:
                        error_snippets.append(f"--- TAIL OF {p.name} ---\n{tail}")
                except Exception:
                    pass

        if error_snippets:
            sections.append("### RECENT SERVICE & PROCESS ERRORS:\n" + "\n\n".join(error_snippets))

        # 2. Extract matching structured events from verbose_debug.jsonl
        task_id = str((task or {}).get("id", "")).lower()
        component_hint = None
        if "supervisor" in str(prompt).lower():
            component_hint = "supervisor"
        elif "decision" in str(prompt).lower() or "sizing" in str(prompt).lower():
            component_hint = "decisions"

        events = self.get_recent_telemetry(component=component_hint, limit=40, max_bytes=32768) if component_hint else []
        # If no specific component or no events found for hint, retrieve recent events across components
        if not events:
            events = self.get_recent_telemetry(limit=25, max_bytes=32768)

        if events:
            jsonl_repr = "\n".join(json.dumps(e, ensure_ascii=False) for e in events[-20:])
            sections.append("### STRUCTURED QUANT DEBUG TELEMETRY (JSONL STREAM):\n" + jsonl_repr)

        if not sections:
            return "No recent runtime errors or abnormal debug events recorded in telemetry stream."

        combined = "\n\n".join(sections)
        if len(combined) > max_chars:
            combined = combined[-max_chars:]
        return combined

    def get_segment_debug_telemetry_summary(
        self,
        league: str,
        family: str,
        limit: int = 6,
        max_bytes: int = 65536,
    ) -> str:
        """Extracts recent verbose_debug.jsonl DEBUG/WARNING/ERROR traces for a specific league/family to feed back into AI synthesis.

        Enforces Zero Prompt Anchoring (Commandment 10): only numerical telemetry events
        (residual_attribution, decision_trace, rule_evaluation, runtime_anomaly) are included,
        stripping any model class names or distribution buzzwords.
        """
        if not self.jsonl_file.exists():
            return ""
        lg_u = (league or "").upper().strip()
        fam_l = (family or "").lower().strip()
        base_fam = fam_l.split(":")[-1] if ":" in fam_l else fam_l
        allowed_events = {"residual_attribution", "decision_trace", "rule_evaluation", "runtime_anomaly"}
        forbidden_tokens = (
            "negativebinomial", "poisson", "copula", "weibull", "markov", "skellam",
            "dixon", "laplace", "hurdle", "kalman", "gumbel", "clayton", "frank",
            "jamesstein", "broken", "model_",
        )

        matched: List[str] = []
        try:
            with open(self.jsonl_file, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                lines = f.readlines()

            for line in reversed(lines):
                if lg_u and lg_u not in line.upper():
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                ev_type = str(entry.get("event_type") or "")
                lvl = str(entry.get("level") or "DEBUG")
                if ev_type not in allowed_events and lvl not in ("WARNING", "ERROR", "CRITICAL"):
                    continue
                msg = str(entry.get("message") or "")
                data = entry.get("data") or {}
                blob = f"{msg} {json.dumps(data, ensure_ascii=False)}".lower()
                if base_fam and base_fam not in blob:
                    continue
                if any(tok in blob.replace("_", "").replace("-", "") for tok in forbidden_tokens):
                    continue
                comp = entry.get("component") or entry.get("module") or "engine"
                clean_msg = msg.replace("\n", " ")[:280]
                summary_line = f"- [{lvl}] {comp} ({ev_type or 'log'}): {clean_msg}"
                if summary_line not in matched:
                    matched.append(summary_line)
                if len(matched) >= limit:
                    break
        except Exception:
            return ""

        if not matched:
            return ""
        return (
            f"\nLIVE VERBOSE DEBUG TELEMETRY FEEDBACK ({lg_u} | {base_fam} from verbose_debug.jsonl):\n"
            + "\n".join(reversed(matched))
        )


# Global singleton access
_VERBOSE_LOGGER: Optional[QuantVerboseLogger] = None


def get_verbose_logger(data_dir: Optional[Path] = None) -> QuantVerboseLogger:
    """Returns the singleton instance of QuantVerboseLogger."""
    global _VERBOSE_LOGGER
    with _LOCK:
        if _VERBOSE_LOGGER is None:
            _VERBOSE_LOGGER = QuantVerboseLogger(data_dir=data_dir)
        return _VERBOSE_LOGGER


def setup_verbose_logging(data_dir: Optional[Path] = None) -> QuantVerboseLogger:
    """Ensures verbose logging is initialized and active."""
    return get_verbose_logger(data_dir=data_dir)
