"""Autonomous Auto-Run Telemetry, Diagnostic Logging & AI Remediation Kernel.

Provides exhaustive, machine-readable structured logging for all automated betting
runs (15m cycles, 30m cycles, manual auto-queues, and live tests). Every event,
market navigation, line drift, adaptive fallback calculation, verification gate,
stake input, placement receipt, and failure is recorded both in a high-speed SQLite
database table and an append-only JSONL log file for autonomous AI root-cause analysis.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = ROOT / "data" / "logs"
JSONL_LOG_PATH = LOG_DIR / "autorun.jsonl"
DEFAULT_DB_PATH = ROOT / "data" / "engine.sqlite3"

BUDAPEST_TZ = ZoneInfo("Europe/Budapest")

# Standardized AI diagnostic error codes
class AutoRunErrorCode:
    MARKET_NOT_FOUND = "MARKET_NOT_FOUND"
    MARKET_SUSPENDED = "MARKET_SUSPENDED"
    OUTCOME_NOT_FOUND = "OUTCOME_NOT_FOUND"
    LINE_DRIFT_NO_EV = "LINE_DRIFT_NO_EV"
    BETSLIP_VERIFICATION_FAILED = "BETSLIP_VERIFICATION_FAILED"
    INPUT_FIELD_MISSING = "INPUT_FIELD_MISSING"
    STAKE_LIMIT_300 = "STAKE_LIMIT_300"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    NETWORK_DISCONNECTED = "NETWORK_DISCONNECTED"
    PAGE_LOAD_TIMEOUT = "PAGE_LOAD_TIMEOUT"
    STAKE_REJECTED = "STAKE_REJECTED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


def _get_db_path(store: Any = None) -> Path:
    if store and hasattr(store, "path") and store.path:
        return Path(store.path)
    if DEFAULT_DB_PATH.exists():
        return DEFAULT_DB_PATH
    p = ROOT / "var" / "engine.db"
    if p.exists():
        return p
    return DEFAULT_DB_PATH


def init_autorun_db(db_path: Optional[Path] = None) -> None:
    """Initialize autorun_events database schema if not already present."""
    target_path = db_path or _get_db_path()
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target_path, timeout=5.0) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autorun_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    logged_at TEXT NOT NULL,
                    budapest_time TEXT NOT NULL,
                    cycle_id TEXT NOT NULL,
                    run_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    tip_id TEXT,
                    game_id TEXT,
                    league TEXT,
                    selection TEXT,
                    line REAL,
                    odds REAL,
                    stake REAL,
                    market TEXT,
                    market_name TEXT,
                    status TEXT,
                    message TEXT,
                    error_code TEXT,
                    error_details TEXT,
                    ai_remediation_hint TEXT,
                    payload_json TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_cycle ON autorun_events(cycle_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_tip ON autorun_events(tip_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_sev ON autorun_events(severity)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_time ON autorun_events(logged_at)")
            conn.commit()
    except Exception as e:
        log.warning(f"Failed to initialize autorun_events DB at {target_path}: {e}")


def log_autorun_event(
    cycle_id: str,
    stage: str,
    severity: str = "INFO",
    tip_id: Optional[str] = None,
    game_id: Optional[str] = None,
    league: Optional[str] = None,
    selection: Optional[str] = None,
    line: Optional[float] = None,
    odds: Optional[float] = None,
    stake: Optional[float] = None,
    market: Optional[str] = None,
    market_name: Optional[str] = None,
    status: Optional[str] = None,
    message: str = "",
    error_code: Optional[str] = None,
    error_details: Optional[str] = None,
    ai_remediation_hint: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    run_type: str = "auto_runner",
    store: Any = None,
) -> Dict[str, Any]:
    """Record an immutable, structured autorun telemetry event."""
    now_utc = datetime.now(timezone.utc)
    logged_at_iso = now_utc.isoformat()
    budapest_time_str = now_utc.astimezone(BUDAPEST_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # Generate smart AI remediation hint if error_code is provided without hint
    if error_code and not ai_remediation_hint:
        if error_code == AutoRunErrorCode.MARKET_NOT_FOUND:
            ai_remediation_hint = f"Verify Stake accordion naming for market '{market_name or market}'. Check if league '{league}' has custom accordion names."
        elif error_code == AutoRunErrorCode.LINE_DRIFT_NO_EV:
            ai_remediation_hint = "Bookmaker shifted line beyond positive mathematical expectation (+3.0% hurdle). No action needed unless model forecast can be refreshed."
        elif error_code == AutoRunErrorCode.BETSLIP_VERIFICATION_FAILED:
            ai_remediation_hint = "Betslip content did not match target selection or line. Review matchesLine logic or entity naming."
        elif error_code == AutoRunErrorCode.STAKE_LIMIT_300:
            ai_remediation_hint = "Stake active wager capacity reached (300/300). System must pause execution until settled games clear."
        elif error_code == AutoRunErrorCode.LOGIN_REQUIRED:
            ai_remediation_hint = "Stake session expired or logged out. User must authenticate in Safari."
        elif error_code == AutoRunErrorCode.NETWORK_DISCONNECTED:
            ai_remediation_hint = "Stake reported network disconnected. Re-attempt with exponential backoff."

    event_record = {
        "logged_at": logged_at_iso,
        "budapest_time": budapest_time_str,
        "cycle_id": cycle_id or f"cycle_{int(time.time())}",
        "run_type": run_type,
        "stage": stage,
        "severity": severity.upper(),
        "tip_id": tip_id,
        "game_id": game_id,
        "league": league,
        "selection": selection,
        "line": float(line) if line is not None else None,
        "odds": float(odds) if odds is not None else None,
        "stake": float(stake) if stake is not None else None,
        "market": market,
        "market_name": market_name,
        "status": status,
        "message": message,
        "error_code": error_code,
        "error_details": str(error_details) if error_details is not None else None,
        "ai_remediation_hint": ai_remediation_hint,
        "payload": payload or {},
    }

    # 1. Append to JSONL log file
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(JSONL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.debug(f"Could not write to autorun.jsonl: {e}")

    # 2. Insert into SQLite table
    db_path = _get_db_path(store)
    try:
        init_autorun_db(db_path)
        with sqlite3.connect(db_path, timeout=5.0) as conn:
            conn.execute(
                """
                INSERT INTO autorun_events (
                    logged_at, budapest_time, cycle_id, run_type, stage, severity,
                    tip_id, game_id, league, selection, line, odds, stake,
                    market, market_name, status, message, error_code, error_details,
                    ai_remediation_hint, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    logged_at_iso,
                    budapest_time_str,
                    event_record["cycle_id"],
                    run_type,
                    stage,
                    event_record["severity"],
                    tip_id,
                    game_id,
                    league,
                    selection,
                    event_record["line"],
                    event_record["odds"],
                    event_record["stake"],
                    market,
                    market_name,
                    status,
                    message,
                    error_code,
                    event_record["error_details"],
                    ai_remediation_hint,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
    except Exception as e:
        log.debug(f"Could not insert autorun event into DB: {e}")

    return event_record


def get_autorun_logs(
    limit: int = 100,
    cycle_id: Optional[str] = None,
    severity: Optional[str] = None,
    tip_id: Optional[str] = None,
    store: Any = None,
) -> List[Dict[str, Any]]:
    """Retrieve structured autorun log events from database or fallback to JSONL file."""
    db_path = _get_db_path(store)
    records = []

    if db_path.exists():
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                query = "SELECT * FROM autorun_events WHERE 1=1"
                params = []
                if cycle_id:
                    query += " AND cycle_id = ?"
                    params.append(cycle_id)
                if severity:
                    query += " AND severity = ?"
                    params.append(severity.upper())
                if tip_id:
                    query += " AND tip_id = ?"
                    params.append(tip_id)
                query += " ORDER BY id DESC LIMIT ?"
                params.append(max(1, min(1000, limit)))

                rows = conn.execute(query, params).fetchall()
                for r in rows:
                    rec = dict(r)
                    if rec.get("payload_json"):
                        try:
                            rec["payload"] = json.loads(rec["payload_json"])
                        except Exception:
                            rec["payload"] = {}
                    records.append(rec)
                return records
        except Exception as e:
            log.debug(f"DB query for autorun logs failed, trying JSONL: {e}")

    # Fallback to reading JSONL from tail
    if JSONL_LOG_PATH.exists():
        try:
            lines = JSONL_LOG_PATH.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    if cycle_id and rec.get("cycle_id") != cycle_id:
                        continue
                    if severity and rec.get("severity") != severity.upper():
                        continue
                    if tip_id and rec.get("tip_id") != tip_id:
                        continue
                    records.append(rec)
                    if len(records) >= limit:
                        break
                except Exception:
                    pass
        except Exception:
            pass

    return records


def get_autorun_diagnostics(hours: float = 24.0, store: Any = None) -> Dict[str, Any]:
    """Compile comprehensive AI diagnostic report summarizing recent auto-run behavior."""
    since_ts = time.time() - (hours * 3600.0)
    since_iso = datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()

    db_path = _get_db_path(store)
    total_events = 0
    total_cycles = set()
    total_placed = 0
    total_adapted = 0
    total_skipped = 0
    error_counts = {}
    top_issues = []
    actionable_recommendations = []

    if db_path.exists():
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT cycle_id, stage, severity, status, error_code, error_details, ai_remediation_hint FROM autorun_events WHERE logged_at >= ?",
                    (since_iso,),
                )
                rows = cur.fetchall()
                total_events = len(rows)
                for r in rows:
                    cid = r["cycle_id"]
                    if cid:
                        total_cycles.add(cid)
                    status = r["status"]
                    if status == "placed":
                        total_placed += 1
                    elif status == "adapted":
                        total_adapted += 1
                        total_placed += 1
                    elif status == "skipped":
                        total_skipped += 1

                    err_code = r["error_code"]
                    if err_code:
                        error_counts[err_code] = error_counts.get(err_code, 0) + 1
                        if r["ai_remediation_hint"] and r["ai_remediation_hint"] not in actionable_recommendations:
                            actionable_recommendations.append(r["ai_remediation_hint"])
        except Exception as e:
            log.debug(f"Error computing autorun diagnostics: {e}")

    # Build top issues list sorted by occurrence
    for err_code, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True):
        top_issues.append({"error_code": err_code, "count": count})

    return {
        "timeframe_hours": hours,
        "total_events": total_events,
        "total_cycles_run": len(total_cycles),
        "total_placed": total_placed,
        "total_adapted": total_adapted,
        "total_skipped": total_skipped,
        "error_counts": error_counts,
        "top_issues": top_issues,
        "actionable_recommendations": actionable_recommendations[:10],
        "log_file": str(JSONL_LOG_PATH),
    }
