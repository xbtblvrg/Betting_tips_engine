"""Offline worker-health policies for the experimental restart journal.

Consumes copied observations only. No controller imports, service discovery or
execution. A healthy heartbeat proves neither completed work nor release safety.
Limits follow supervisor.problem; malformed/future heartbeat or step evidence
fails closed. Optional dispatch diagnostics apply only while current.
"""
import math

from .development_acceptance import _check, _time


SERVICES = frozenset({"learning", "development", "release"})


def _age(value, now, limit, label):
    age = (_time(now) - _time(value)).total_seconds()
    _check(0 <= age <= limit, label + " is future or stale")


def _limit(value, maximum):
    _check(type(value) in (int, float) and math.isfinite(value)
           and 0 < value <= maximum, "Invalid step deadline")
    return value


def _count(value):
    _check(type(value) is int and value >= 0, "Invalid worker count")
    return value


def _development(health, now):
    # Delivery evaluates runnable work and resource admission separately from
    # the worker heartbeat. Preserve supervisor.problem's fresh explicit stall
    # veto: an idle coordinator can still be failing to dispatch eligible work.
    # Missing/expired diagnostics do not establish a current stall; neither a
    # runnable count nor an attention flag alone overrides a legitimate wait.
    progress = health.get("progress", {})
    try:
        progress_age = (_time(now) - _time(progress.get("checked_at"))).total_seconds()
        fresh_progress = 0 <= progress_age <= 180
    except (TypeError, ValueError, AttributeError, OverflowError):
        fresh_progress = False
    if fresh_progress:
        _check(progress.get("dispatch_stalled") is not True,
               "Development dispatcher has eligible unclaimed work but no active task for over 180 seconds")
    state = health.get("state")
    _check(state in {"running", "idle", "retry_wait", "failed", "disabled"},
           "Invalid development state")
    active = health.get("active_tasks")
    if active is None and "active_count" in health:
        _check(_count(health["active_count"]) == 0, "Active count without task inventory")
    if active is not None:
        _check(isinstance(active, list), "Invalid active tasks")
        _check(len(active) <= 100, "Too many active tasks")
        _check(_count(health.get("active_count", len(active))) == len(active),
               "Active task count mismatch")
        _check(not active or state == "running", "Active tasks in non-running worker")
    if state == "running":
        if active:
            identities = set()
            for row in active:
                _check(isinstance(row, dict), "Invalid active task")
                task = row.get("task_id")
                _check(isinstance(task, str) and bool(task.strip()) and task not in identities,
                       "Missing or duplicate task identity")
                identities.add(task)
                step = row.get("step")
                _check(isinstance(step, str) and bool(step.strip()), "Missing task step")
                maximum = 3600 if step in {"waiting-integration", "waiting-preparation"} else 1800
                limit = _limit(row.get("step_timeout_seconds"), maximum)
                _age(row.get("checked_at"), now, 180, "Development task heartbeat")
                _age(row.get("step_started_at"), now, limit + 30, "Development task step")
                # DevelopmentWorker.heartbeat samples the child checked_at,
                # then a new step_started_at, then the coordinator checked_at.
                # A retained step may predate the child heartbeat; a new one
                # may postdate it. Both must be inside the containing snapshot,
                # with their independent freshness/deadline checks unchanged.
                _check(_time(row["step_started_at"]) <= _time(health.get("checked_at")),
                       "Task step postdates coordinator snapshot")
                _check(_time(row["checked_at"]) <= _time(health.get("checked_at")),
                       "Task heartbeat postdates coordinator snapshot")
        else:
            # Actual legacy worker schema has no active_tasks list.
            _age(health.get("run_started_at"), now, 3000, "Development attempt")
            _check(_time(health["run_started_at"]) <= _time(health.get("checked_at")),
                   "Development attempt postdates worker snapshot")
    # A failed candidate is not a stopped worker. Its fresh heartbeat is valid;
    # attempt failure remains in the original payload and does not imply success.


def worker_disabled(service, health):
    # An explicit disable remains a veto even when other fields conflict.
    # The validator still reports the contradiction; it cannot request activation.
    return (service in {"development", "release"} and isinstance(health, dict)
            and (health.get("state") == "disabled" or health.get("enabled") is False))


def worker_problem(service, health, now):
    """Return a process-health reason, independent of capability/delivery gaps."""
    _check(service in SERVICES, "Unknown worker service")
    try:
        _check(isinstance(health, dict) and bool(health), "Missing worker heartbeat")
        limit = {"learning": 300, "development": 180, "release": 90}[service]
        if service == "learning":
            _check(not health.get("error"), "Learning worker reported an error")
            counts = health.get("counts", {})
            _check(isinstance(counts, dict), "Invalid learning counts")
            for count in counts.values():
                _count(count)
            if counts.get("running", 0):
                limit = 7200
        elif service == "development":
            _development(health, now)
        else:
            state = health.get("state")
            _check(state in {"active", "watching", "disabled", "failed"}, "Invalid release state")
            if "enabled" in health:
                _check(type(health["enabled"]) is bool and
                       health["enabled"] == (state != "disabled"), "Inconsistent release enabled flag")
            _check(state != "failed" and not health.get("service_error_active"),
                   "Release worker failed")
            if state == "active":
                step_limit = _limit(health.get("step_timeout_seconds"), 1560)
                _age(health.get("step_started_at"), now, step_limit + 30, "Release step")
                _check(_time(health["step_started_at"]) <= _time(health.get("checked_at")),
                       "Release step starts after its heartbeat")
        _age(health.get("checked_at"), now, limit, "Worker heartbeat")
    except (TypeError, ValueError, AttributeError, OverflowError) as exc:
        return str(exc) or "Invalid worker health"
    return None


def worker_progress_after(service, health, requested_at, now):
    """Fresh post-intent progress only, never workflow recovery certification."""
    if worker_problem(service, health, now) or worker_disabled(service, health):
        return False
    return _time(requested_at) < _time(health["checked_at"]) <= _time(now)
