"""Unit tests for System Resource Governor & Multi-Core Acceleration."""
import os
from pathlib import Path
import tempfile
import pytest

from betting_engine.system_resource_governor import (
    inspect_system_resources,
    update_governor_status_file,
    log_acceleration_event,
    get_memory_info,
    SystemMetrics,
    MIN_RESERVED_CORES,
    MAX_SAFE_WORKERS,
)


def test_inspect_system_resources():
    metrics = inspect_system_resources()
    assert isinstance(metrics, SystemMetrics)
    assert metrics.total_cpu_cores >= 1
    assert metrics.load_1m >= 0.0
    assert metrics.total_ram_gb > 0.0
    assert metrics.available_ram_gb >= 0.0
    # Safe worker limit must respect upper and lower bounds
    assert 1 <= metrics.safe_worker_limit <= MAX_SAFE_WORKERS
    # Core reservation invariant
    if metrics.total_cpu_cores > MIN_RESERVED_CORES:
        assert metrics.safe_worker_limit <= metrics.total_cpu_cores - MIN_RESERVED_CORES


def test_get_memory_info():
    total_ram, avail_ram = get_memory_info()
    assert total_ram > 0.0
    assert avail_ram >= 0.0


def test_update_governor_status_file():
    status = update_governor_status_file()
    assert "total_cpu_cores" in status
    assert "safe_worker_limit" in status
    assert "available_ram_gb" in status
    assert "throttle_recommended" in status


def test_log_acceleration_event():
    log_acceleration_event("test_event", {"metric": "unit_test", "value": 123})
    from betting_engine.system_resource_governor import ACCELERATION_LOG_PATH
    assert ACCELERATION_LOG_PATH.exists()
    content = ACCELERATION_LOG_PATH.read_text(encoding="utf-8")
    assert "test_event" in content
