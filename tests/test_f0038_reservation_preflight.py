"""Offline reservation compatibility and durable recovery, without services."""
from datetime import datetime, timedelta, timezone
from functools import partial
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development_restart_dispatch as dispatch
from betting_engine import development_restart_journal as journal
from betting_engine import development_worker_dispatch as worker


def stamp(seconds):
    return (datetime(2026, 10, 1, tzinfo=timezone.utc)
            + timedelta(seconds=seconds)).isoformat()


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def incompatible(kind):
    def wrong_signature(handle, *, intent, at):
        pytest.fail("incompatible reservation executed")

    async def coroutine(handle, intent, at):
        pytest.fail("deferred reservation executed")

    def generator(handle, intent, at):
        pytest.fail("deferred reservation executed")
        yield

    async def async_generator(handle, intent, at):
        pytest.fail("deferred reservation executed")
        yield

    class Deferred:
        async def __call__(self, handle, intent, at):
            pytest.fail("deferred callable reservation executed")

    if kind == "text_signature":
        wrong_signature.__text_signature__ = "(handle, intent, at)"
    return {"wrong_signature": wrong_signature, "text_signature": wrong_signature,
            "not_callable": None, "coroutine": coroutine, "generator": generator,
            "async_generator": async_generator,
            "partial_coroutine": partial(coroutine),
            "partial_object": partial(Deferred())}[kind]


@pytest.mark.parametrize("entry", ["preflight", "inspect", "dispatch"])
@pytest.mark.parametrize("kind", [
    "missing", "not_callable", "wrong_signature", "coroutine", "generator",
    "async_generator", "partial_coroutine", "partial_object", "text_signature",
])
def test_invalid_reservation_fails_before_journal_or_callbacks(
        tmp_path, monkeypatch, entry, kind):
    handle = journal.create_journal(tmp_path.resolve(), service="learning",
                                    created_at=stamp(0), checkpoint_at=stamp(200))
    before = snapshot(tmp_path)
    if kind == "missing":
        monkeypatch.delattr(dispatch, "_reserve")
    else:
        monkeypatch.setattr(dispatch, "_reserve", incompatible(kind))

    def forbidden(*args, **kwargs):
        pytest.fail("incompatible reservation reached journal access or callbacks")

    monkeypatch.setattr(journal, "_validate_handle", forbidden)
    monkeypatch.setattr(journal, "_open", forbidden)
    with pytest.raises(ValueError, match="Incompatible worker restart dependencies"):
        if entry == "preflight":
            worker.validate_worker_dependencies()
        elif entry == "inspect":
            worker.inspect_worker_dispatch(handle, service="learning", as_of=stamp(120))
        else:
            worker.dispatch_worker_observation(
                handle, None, service="learning", request_restart=forbidden, clock=forbidden)
    assert snapshot(tmp_path) == before


CHILD = r'''
import json, os, sys
from betting_engine import development_worker_dispatch as worker
handle, service = json.loads(sys.argv[1]), sys.argv[2]
def interrupted(actual_service, intent_id):
    assert actual_service == service
    report = worker.inspect_worker_dispatch(
        handle, service=service, as_of='2026-10-01T00:02:00+00:00')
    assert len(report['attempts']) == 1
    assert report['attempts'][0]['id'] == intent_id
    assert report['dispatches'][0]['intent_id'] == intent_id
    assert report['dispatches'][0]['state'] == 'uncertain'
    os._exit(73)
worker.dispatch_worker_observation(
    handle, None, service=service, request_restart=interrupted,
    clock=lambda: '2026-10-01T00:02:00+00:00')
raise AssertionError('interrupted callback returned')
'''


@pytest.mark.parametrize("service", ["learning", "development", "release"])
def test_reservation_repair_then_process_exit_retains_cooldown_and_checkpoint(
        tmp_path, monkeypatch, service):
    handle = journal.create_journal(tmp_path.resolve(), service=service,
                                    created_at=stamp(0), checkpoint_at=stamp(200))

    def forbidden(*args):
        pytest.fail("old intent replayed or cooldown bypassed")

    def observe(at, callback=forbidden):
        return worker.dispatch_worker_observation(
            handle, None, service=service, request_restart=callback, clock=lambda: stamp(at))

    assert observe(0)["attempts"] == []
    assert observe(60)["attempts"] == []
    before = snapshot(tmp_path)
    with monkeypatch.context() as skew:
        skew.setattr(dispatch, "_reserve", incompatible("generator"))
        with pytest.raises(ValueError, match="Incompatible worker restart dependencies"):
            observe(120)
        assert snapshot(tmp_path) == before

    # Fresh interpreter, repaired dependencies, then death AFTER committed
    # reservation but BEFORE acknowledgement. There is no actual service restart.
    child = subprocess.run(
        [sys.executable, "-c", CHILD, json.dumps(handle), service],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "OMP_NUM_THREADS": "1"}, capture_output=True, text=True, timeout=15)
    assert child.returncode == 73, child.stderr
    before = snapshot(tmp_path)
    reopened = worker.inspect_worker_dispatch(handle, service=service, as_of=stamp(180))
    assert snapshot(tmp_path) == before
    assert len(reopened["attempts"]) == 1
    uncertain = reopened["dispatches"][0]
    assert uncertain["state"] == "uncertain" and uncertain["replay_permitted"] is False
    assert observe(180)["dispatches"] == [uncertain]
    missed = observe(240)
    assert missed["checkpoint_at"] == stamp(200)
    assert missed["checkpoint_missed"] and missed["checkpoint_miss_recorded"]
    assert observe(960)["dispatches"] == [uncertain]
    calls = []
    later = observe(1020, lambda s, i: calls.append((s, i)))
    assert calls == [(service, later["attempts"][1]["id"])]
    assert later["attempts"][0] == missed["attempts"][0]
    assert later["dispatches"][0] == uncertain
    assert later["dispatches"][1]["state"] == "request_succeeded"
    assert later["checkpoint_at"] == stamp(200) and later["checkpoint_miss_recorded"]
    for claim in ("service_recovery_verified", "production_recovery_verified",
                  "workflow_completion_verified", "production_ready"):
        assert later[claim] is False
