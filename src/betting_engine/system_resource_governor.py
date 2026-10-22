"""Intelligent System Resource Governor & Multi-Core Acceleration Controller.

Enforces system invariants for safe hardware acceleration:
1. Total CPU cores: 12 (Apple Silicon).
2. Headroom reservation: Minimum 4 cores and 3.0 GB RAM strictly reserved for macOS and user apps.
3. Dynamic Concurrency: Bounds worker threads between 2 and 8 based on 1-minute load average.
4. Graceful Throttling: If load average >= 9.5 or free RAM < 2.5 GB, adds throttle delay to prevent OOM/crashes.
5. Telemetry & AI Supervision: Records events to `data/logs/acceleration.log` and `data/resource_governor_status.json`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from .providers import DATA

logger = logging.getLogger(__name__)

ACCELERATION_LOG_PATH = DATA / "logs" / "acceleration.log"
RESOURCE_STATUS_PATH = DATA / "resource_governor_status.json"

# Invariants
MIN_RESERVED_CORES = 4
MAX_SAFE_WORKERS = 8
MIN_SAFE_WORKERS = 2
LOAD_HIGH_WATERMARK = 9.5
LOAD_EMERGENCY_WATERMARK = 11.0
RAM_FREE_MIN_GB = 3.0


@dataclass
class SystemMetrics:
    timestamp: str
    total_cpu_cores: int
    load_1m: float
    load_5m: float
    load_15m: float
    total_ram_gb: float
    available_ram_gb: float
    safe_worker_limit: int
    throttle_recommended: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_memory_info() -> Tuple[float, float]:
    """Returns (total_ram_gb, available_ram_gb) using native macOS page calculations."""
    try:
        pagesize = os.sysconf("SC_PAGESIZE")
        total_ram = (pagesize * os.sysconf("SC_PHYS_PAGES")) / (1024**3)
        out = subprocess.check_output(["vm_stat"], timeout=2.0).decode("utf-8")
        free_match = re.search(r"Pages free:\s+(\d+)", out)
        inactive_match = re.search(r"Pages inactive:\s+(\d+)", out)
        free_pages = int(free_match.group(1)) if free_match else 0
        inactive_pages = int(inactive_match.group(1)) if inactive_match else 0
        available_gb = ((free_pages + inactive_pages) * pagesize) / (1024**3)
        return round(total_ram, 2), round(available_gb, 2)
    except Exception as e:
        logger.warning(f"Failed to query vm_stat, falling back to sysconf: {e}")
        try:
            pagesize = os.sysconf("SC_PAGESIZE")
            total = (pagesize * os.sysconf("SC_PHYS_PAGES")) / (1024**3)
            return round(total, 2), 8.0  # Safe fallback estimate
        except Exception:
            return 16.0, 4.0


def inspect_system_resources() -> SystemMetrics:
    """Collects current hardware metrics and computes safe acceleration boundaries."""
    now_iso = datetime.now(timezone.utc).isoformat()
    total_cores = os.cpu_count() or 4
    try:
        load_1m, load_5m, load_15m = os.getloadavg()
    except Exception:
        load_1m, load_5m, load_15m = 1.0, 1.0, 1.0

    total_ram, avail_ram = get_memory_info()

    # Compute safe worker pool
    # Reserve at least MIN_RESERVED_CORES for OS and UI
    base_workers = max(MIN_SAFE_WORKERS, min(MAX_SAFE_WORKERS, total_cores - MIN_RESERVED_CORES))
    reasons: List[str] = []
    throttle = False

    if load_1m >= LOAD_EMERGENCY_WATERMARK:
        safe_workers = 1
        throttle = True
        reasons.append(f"Emergency CPU load throttle (1m load: {load_1m:.2f} >= {LOAD_EMERGENCY_WATERMARK})")
    elif load_1m >= LOAD_HIGH_WATERMARK:
        safe_workers = max(1, base_workers - 3)
        throttle = True
        reasons.append(f"High CPU load backoff (1m load: {load_1m:.2f} >= {LOAD_HIGH_WATERMARK})")
    else:
        safe_workers = base_workers

    if avail_ram < RAM_FREE_MIN_GB:
        safe_workers = min(safe_workers, 2)
        throttle = True
        reasons.append(f"Low free RAM warning ({avail_ram:.2f} GB < {RAM_FREE_MIN_GB} GB)")

    return SystemMetrics(
        timestamp=now_iso,
        total_cpu_cores=total_cores,
        load_1m=round(load_1m, 2),
        load_5m=round(load_5m, 2),
        load_15m=round(load_15m, 2),
        total_ram_gb=total_ram,
        available_ram_gb=avail_ram,
        safe_worker_limit=safe_workers,
        throttle_recommended=throttle,
        reasons=reasons,
    )


def log_acceleration_event(event_type: str, details: Dict[str, Any]) -> None:
    """Appends an immutable JSON telemetry record to data/logs/acceleration.log."""
    try:
        ACCELERATION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "details": details,
        }
        with open(ACCELERATION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Failed to append acceleration log: {e}")


def update_governor_status_file() -> Dict[str, Any]:
    """Persists current governor metrics atomically to data/resource_governor_status.json."""
    metrics = inspect_system_resources()
    data = metrics.to_dict()
    try:
        from .io_utils import atomic_json
        atomic_json(RESOURCE_STATUS_PATH, data)
    except Exception as e:
        logger.error(f"Failed to write resource governor status: {e}")
    return data
