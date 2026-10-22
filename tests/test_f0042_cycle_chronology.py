"""Scratch-only supervision chronology drills; no service or release authority."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development_restart_journal as journal
from betting_engine import development_supervision_history as history


BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def stamp(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(root).rglob('*') if p.is_file()}


def create(tmp_path, services=('learning',)):
    handles = {s: journal.create_journal(tmp_path.resolve(), service=s,
               created_at=stamp(0), checkpoint_at=stamp(100)) for s in services}
    return history.create_history(tmp_path.resolve(), handles, created_at=stamp(0)), handles


def record(store, handles, times, *, name='clock-case', callbacks=None):
    ticks = iter(times)
    return history.record_cycle(store, name, {s: None for s in handles},
        request_restart=callbacks or {s: lambda *_: pytest.fail('Unexpected restart')
                                     for s in handles}, clock=lambda: next(ticks))


def test_observation_cannot_predate_durable_cycle_start(tmp_path):
    store, handles = create(tmp_path)
    before = snapshot(Path(handles['learning']['root']))
    result = record(store, handles, map(stamp, [60, 50, 60, 60]))
    row = result['cycles'][0]
    assert row['state'] == 'failed'
    summary = row['result']['summary']
    assert summary['operation_errors'] == ['learning']
    assert 'backwards' in summary['services']['learning']['error']['message']
    # Dispatch may create its empty ownership lock before sampling time; the
    # database, observations and manifest must remain byte-identical.
    assert snapshot(Path(handles['learning']['root'])) == {
        **before, 'dispatch.lock': hashlib.sha256(b'').hexdigest()}
    assert history.inspect_history(store)['cycles'] == [row]


def test_service_clock_regression_keeps_prior_work_and_blocks_only_invalid_time(tmp_path):
    store, handles = create(tmp_path, ('engine', 'learning'))
    before = {s: snapshot(Path(h['root'])) for s, h in handles.items()}
    result = record(store, handles, map(stamp, [0, 10, 5, 10, 10]))
    row = result['cycles'][0]
    assert row['state'] == 'failed'
    assert row['result']['summary']['operation_errors'] == ['learning']
    assert before['engine'] != snapshot(Path(handles['engine']['root']))
    assert snapshot(Path(handles['learning']['root'])) == {
        **before['learning'], 'dispatch.lock': hashlib.sha256(b'').hexdigest()}
    retained = snapshot(tmp_path)
    reused = record(store, handles, [], name='clock-case')
    assert reused['reused'] and reused['cycles'] == [row]
    assert snapshot(tmp_path) == retained


def test_finish_cannot_predate_service_observation_and_reopen_never_replays(tmp_path):
    store, handles = create(tmp_path)
    with pytest.raises(ValueError, match='backwards'):
        record(store, handles, map(stamp, [0, 10, 5]))
    row = history.inspect_history(store)['cycles'][0]
    assert row['state'] == 'interrupted' and row['result'] is None
    retained = snapshot(tmp_path)
    # Independent interpreter reconstructs only the explicitly supplied scratch handle.
    code = '''
import json, sys
from betting_engine import development_supervision_history as history
handle = json.loads(sys.argv[1])
def forbidden(*args): raise AssertionError('Replayed interrupted cycle')
result = history.record_cycle(handle, 'clock-case', {'learning': None},
    request_restart={'learning': forbidden}, clock=forbidden)
print(json.dumps(result))
'''
    child = subprocess.run([sys.executable, '-c', code, json.dumps(store)],
                           env=os.environ.copy(), capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout)['cycles'] == [row]
    assert snapshot(tmp_path) == retained
    later = record(store, handles, [stamp(70)] * 3, name='later')
    assert later['cycles'][0]['state'] == 'completed'
    assert row in history.inspect_history(store)['cycles']


def test_regressed_outcome_time_preserves_unknown_attempt_and_checkpoint(tmp_path):
    store, handles = create(tmp_path)
    for second in (0, 60):
        record(store, handles, [stamp(second)] * 3, name=str(second))
    calls = []
    # Start, observation/reservation, callback outcome, error inspection, finish.
    result = record(store, handles, map(stamp, [120, 130, 125, 140, 140]),
                    callbacks={'learning': lambda *args: calls.append(args)})
    row = result['cycles'][0]
    assert len(calls) == 1 and row['state'] == 'failed'
    summary = row['result']['summary']
    assert summary['missed_checkpoints'] == ['learning']
    assert summary['services']['learning']['report']['checkpoint_at'] == stamp(100)
    assert summary['services']['learning']['report']['latest_attempt']['outcome'] == 'unknown'
    assert not summary['production_recovery_verified']
    retained = snapshot(tmp_path)
    assert record(store, handles, [])['cycles'] == [row]
    assert snapshot(tmp_path) == retained
    record(store, handles, [stamp(200)] * 3, name='cooldown')
    assert len(journal.inspect_journal(handles['learning'], as_of=stamp(200))['attempts']) == 1


def test_equivalent_offsets_are_not_clock_regression(tmp_path):
    store, handles = create(tmp_path)
    result = record(store, handles, ['2026-10-01T02:01:00+02:00',
                                    '2026-10-01T00:01:00Z', stamp(60)])
    row = result['cycles'][0]
    assert row['state'] == 'completed'
    assert row['result']['summary']['operation_errors'] == []
    assert not result['production_ready']
