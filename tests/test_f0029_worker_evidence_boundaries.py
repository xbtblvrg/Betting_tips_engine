"""Reject contradictory supplied evidence without activating disabled workers."""
from datetime import datetime, timedelta, timezone

import pytest

from betting_engine import development_restart_journal as journal
from betting_engine.development_worker_health import worker_problem


def stamp(seconds=0):
    return (datetime(2026, 9, 30, tzinfo=timezone.utc)
            + timedelta(seconds=seconds)).isoformat()


def test_explicit_disabled_release_flag_blocks_intent_even_with_conflicting_state(tmp_path):
    handle = journal.create_journal(tmp_path.resolve(), service="release",
                                    checkpoint_at=stamp(200), created_at=stamp())
    for seconds in (0, 60, 120, 1020):
        report = journal.record_observation(handle, {
            "state": "failed", "enabled": False, "checked_at": stamp(seconds),
        }, observed_at=stamp(seconds))
        assert report["state"] == "degraded"
        assert report["current_process_reason"]
        assert report["new_intent"] is None
        assert report["attempts"] == []
    assert report["checkpoint_missed"]


@pytest.mark.parametrize("parallel", [False, True])
def test_task_progress_cannot_postdate_its_containing_snapshot(parallel):
    health = {"state": "running", "checked_at": stamp(10)}
    if parallel:
        health.update(active_count=1, active_tasks=[{
            "task_id": "fixture", "step": "implementation",
            "checked_at": stamp(11), "step_started_at": stamp(0),
            "step_timeout_seconds": 300,
        }])
    else:
        health["run_started_at"] = stamp(11)
    assert worker_problem("development", health, stamp(20))


@pytest.mark.parametrize("service", ["learning", "development", "release"])
def test_failed_acknowledgement_is_idempotent_and_never_certifies_recovery(tmp_path, service):
    handle = journal.create_journal(tmp_path.resolve(), service=service,
                                    checkpoint_at=stamp(200), created_at=stamp())
    for seconds in (0, 60, 120):
        report = journal.record_observation(handle, None, observed_at=stamp(seconds))
    intent = report["new_intent"]["id"]
    first = journal.record_outcome(handle, intent, "failed", error="fixture error",
                                   observed_at=stamp(121))
    again = journal.record_outcome(handle, intent, "failed", error="fixture error",
                                   observed_at=stamp(122))
    assert first["events"] == again["events"]
    with pytest.raises(ValueError, match="Conflicting"):
        journal.record_outcome(handle, intent, "succeeded", observed_at=stamp(123))
    reopened = journal.inspect_journal(handle, as_of=stamp(124))
    assert reopened["attempts"][0]["outcome"] == "failed"
    assert reopened["attempts"][0]["recovery_observed_at"] is None
    assert reopened["new_intent"] is None
