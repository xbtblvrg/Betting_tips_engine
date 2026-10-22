"""Offline synthetic restart/rollover drills; no live services or wagering."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development_restart_journal as journal
from betting_engine import development_recovery_drill as files
from betting_engine import development_supervision_history as history
from betting_engine import development_supervision_series as series


BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def snapshot(root):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(root).rglob('*') if p.is_file()}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Small slots exercise the real rollover path cheaply; a separate test uses
    # the actual unchanged 100-cycle boundary.
    monkeypatch.setattr(history, 'MAX_CYCLES', 2)
    handles = {s: journal.create_journal(tmp_path.resolve(), service=s,
                                       created_at=stamp(), checkpoint_at=stamp(100))
               for s in ('engine', 'learning')}
    store = series.create_series(tmp_path.resolve(), handles, segments=3, created_at=stamp())
    return store, handles


def run(setup, at, *, name=None, observations=None, callbacks=None, clock=None):
    store, handles = setup
    return series.record_cycle(
        store, str(at) if name is None else name,
        {s: None for s in handles} if observations is None else observations,
        request_restart=callbacks or {s: lambda *_: None for s in handles},
        clock=clock or (lambda: stamp(at)))


def slot_handles(store):
    root, manifest = series._manifest(store)
    return [{'root': str(root / s['directory']), 'manifest_sha256': s['manifest_sha256']}
            for s in manifest['segments']]


def test_rollover_preserves_journals_misses_failure_and_cooldown(setup):
    run(setup, 0)
    run(setup, 60)
    old = snapshot(slot_handles(setup[0])[0]['root'])
    calls = []

    def fail(service, _intent):
        calls.append(service)
        raise RuntimeError('synthetic unavailable restart')

    third = run(setup, 120, callbacks={s: fail for s in setup[1]})
    assert third['segment'] == 2
    assert calls == ['engine', 'learning']  # third failure survives rollover
    summary = third['cycles'][0]['result']['summary']
    assert len(summary['failed_requests']) == 2
    assert summary['missed_checkpoints'] == ['engine', 'learning']
    run(setup, 180, callbacks={s: fail for s in setup[1]})
    run(setup, 240, callbacks={s: fail for s in setup[1]})
    assert len(calls) == 2  # rotation cannot reset cooldown
    reopened = series.inspect_series(json.loads(json.dumps(setup[0])))
    assert len(reopened['cycles']) == 5
    assert reopened['historical_missed_checkpoints'] == ['engine', 'learning']
    assert reopened['attention_required']
    assert old == snapshot(slot_handles(setup[0])[0]['root'])
    assert not reopened['production_recovery_verified']
    assert not reopened['workflow_completion_verified']
    assert not reopened['production_ready']


def test_real_100_cycle_boundary(tmp_path):
    handles = {'learning': journal.create_journal(tmp_path.resolve(), service='learning',
               created_at=stamp(), checkpoint_at=stamp(10000))}
    store = series.create_series(tmp_path.resolve(), handles, segments=2, created_at=stamp())
    for i in range(101):
        result = run((store, handles), i, observations={'learning': {'enabled': False}})
        assert result['segment'] == 1 + (i // 100)
    assert [s['cycle_count'] for s in series.inspect_series(store)['segments']] == [100, 1]


def test_duplicate_in_old_slot_is_read_only_even_after_capacity_exhaustion(setup):
    first = run(setup, 0)
    for t in (60, 120, 180, 240, 300):
        run(setup, t)
    before = snapshot(Path(setup[0]['root']).parent)
    duplicate = run(setup, 1000, name='0', clock=lambda: pytest.fail('Duplicate sampled clock'))
    assert duplicate['reused'] and duplicate['segment'] == 1
    assert duplicate['cycles'] == first['cycles']
    with pytest.raises(ValueError, match='capacity exhausted'):
        run(setup, 1000)
    assert snapshot(Path(setup[0]['root']).parent) == before
    assert series.inspect_series(setup[0])['capacity_exhausted']


def test_duplicate_conflict_and_uncomparable_rejection_survive_rotation(setup):
    run(setup, 0, observations={'engine': [], 'learning': None})
    run(setup, 60, observations={'engine': object(), 'learning': None})
    run(setup, 120)
    with pytest.raises(ValueError, match='input conflict'):
        run(setup, 180, name='0')
    same = run(setup, 180, name='0', observations={'engine': [], 'learning': None})
    assert same['reused'] and same['segment'] == 1
    with pytest.raises(ValueError, match='cannot be compared'):
        run(setup, 180, name='60', observations={'engine': object(), 'learning': None})


def test_malformed_service_evidence_still_isolated_after_rotation(setup):
    run(setup, 0)
    run(setup, 60)
    calls = []
    result = run(setup, 120, observations={'engine': [], 'learning': None},
                 callbacks={s: lambda s, _: calls.append(s) for s in setup[1]})
    assert result['segment'] == 2 and calls == ['learning']
    row = result['cycles'][0]
    assert row['state'] == 'failed'
    assert row['result']['summary']['operation_errors'] == ['engine']
    assert row in series.inspect_series(setup[0])['cycles']


def test_lock_spans_rollover_and_callbacks_and_inspection_is_read_only(setup):
    run(setup, 0)
    run(setup, 60)
    calls = []

    def callback(service, _):
        before = snapshot(Path(setup[0]['root']).parent)
        report = series.inspect_series(setup[0])
        assert next(r for r in report['cycles'] if r['start']['cycle_id'] == '120')['state'] == 'interrupted'
        assert snapshot(Path(setup[0]['root']).parent) == before
        with pytest.raises(BlockingIOError):
            run(setup, 120, name='concurrent')
        calls.append(service)

    run(setup, 120, callbacks={s: callback for s in setup[1]})
    assert calls == ['engine', 'learning']


@pytest.mark.parametrize('which', ['start.json', 'result.json'])
def test_uncertain_commit_in_new_slot_never_replays(setup, monkeypatch, which):
    run(setup, 0)
    run(setup, 60)
    real_sync = files._sync

    def fail_sync(path):
        if (path / which).exists() and (path / ('.commit-pending-' + which)).exists():
            raise OSError('synthetic directory sync failure')
        real_sync(path)

    with monkeypatch.context() as m:
        m.setattr(files, '_sync', fail_sync)
        with pytest.raises(OSError, match='sync failure'):
            run(setup, 120)
    callbacks = {s: lambda *_: pytest.fail('uncertain cycle replayed') for s in setup[1]}
    reused = run(setup, 180, name='120', callbacks=callbacks)
    assert reused['reused'] and reused['segment'] == 2
    assert reused['cycles'][0]['state'] == ('start_uncertain' if which == 'start.json' else 'result_uncertain')
    run(setup, 180)
    run(setup, 240)
    assert series.inspect_series(setup[0])['uncertain_cycles']


def test_child_process_exit_in_first_rotated_callback_is_retained(setup):
    run(setup, 0)
    run(setup, 60)
    code = '''
import json, os, sys
from betting_engine import development_supervision_history as h
from betting_engine import development_supervision_series as s
h.MAX_CYCLES = 2
store = json.loads(sys.argv[1])
s.record_cycle(store, '120', {'engine': None, 'learning': None},
 request_restart={x: lambda *_: os._exit(71) for x in ('engine', 'learning')},
 clock=lambda: '2026-10-01T00:02:00+00:00')
'''
    child = subprocess.run([sys.executable, '-c', code, json.dumps(setup[0])],
                           env=os.environ.copy(), capture_output=True, timeout=20)
    assert child.returncode == 71, child.stderr.decode()
    before = snapshot(Path(setup[0]['root']).parent)
    reopened = run(setup, 180, name='120')
    assert reopened['reused'] and reopened['cycles'][0]['state'] == 'interrupted'
    assert snapshot(Path(setup[0]['root']).parent) == before
    run(setup, 180)  # new cycle can proceed; retained intent remains uncertain
    report = series.inspect_series(setup[0])
    assert report['uncertain_cycles']
    summaries = [r['result']['summary'] for r in report['cycles'] if r['result']]
    assert any(s['uncertain_attempts'] for s in summaries)


def test_clock_cannot_regress_across_slots(setup):
    run(setup, 60)
    run(setup, 120)
    before = snapshot(Path(setup[0]['root']).parent)
    with pytest.raises(ValueError, match='clock moved backwards'):
        run(setup, 100)
    assert snapshot(Path(setup[0]['root']).parent) == before
    assert run(setup, 180)['segment'] == 2


def test_uncommitted_start_directory_consumes_id_without_inventing_time(setup):
    run(setup, 0)
    run(setup, 60)
    second = slot_handles(setup[0])[1]
    key = history._cycle_key('reserved')
    Path(second['root'], 'cycles', key).mkdir()
    before = snapshot(Path(setup[0]['root']).parent)
    result = run(setup, 120, name='reserved')
    assert result['reused']
    row = result['cycles'][0]
    assert row['state'] == 'incomplete_start' and row['start'] is None
    assert snapshot(Path(setup[0]['root']).parent) == before
    run(setup, 180)
    assert run(setup, 240)['segment'] == 3
    assert key in series.inspect_series(setup[0])['uncertain_cycles']


def test_late_healthy_progress_never_erases_misses_in_earlier_segment(setup):
    run(setup, 0)
    run(setup, 120)
    result = run(setup, 180, observations={
        'engine': {'engine': {'running': False, 'finished_at': stamp(180), 'error': None}},
        'learning': {'checked_at': stamp(180), 'counts': {}}})
    assert result['segment'] == 2
    assert series.inspect_series(setup[0])['historical_missed_checkpoints'] == ['engine', 'learning']


@pytest.mark.parametrize('kind', ['observations', 'callbacks', 'publication', 'id'])
def test_configuration_errors_at_rollover_leave_every_slot_unchanged(setup, kind):
    run(setup, 0)
    run(setup, 60)
    before = snapshot(Path(setup[0]['root']).parent)
    kwargs = {'observations': {'engine': None}, 'callbacks': {'engine': lambda *_: None},
              'name': '../unsafe'}
    if kind == 'publication':
        # A bad health publication is a retained per-service failure; only a
        # publication with no engine binding is a configuration error.
        root, manifest = series._manifest(setup[0])
        learning_only = series.create_series(root.parent, {'learning': setup[1]['learning']},
                                            segments=2, created_at=stamp())
        before = snapshot(Path(setup[0]['root']).parent)
        with pytest.raises(ValueError, match='Publication requires'):
            series.record_cycle(learning_only, '120', {'learning': None},
                                request_restart={'learning': lambda *_: None},
                                engine_publication={}, clock=lambda: stamp(120))
    else:
        field = 'name' if kind == 'id' else kind
        with pytest.raises(ValueError):
            run(setup, 120, **{field: kwargs[field]})
    assert snapshot(Path(setup[0]['root']).parent) == before


@pytest.mark.parametrize('bad', [0, 1, 33, True, 2.0, '2'])
def test_invalid_capacity_creates_nothing(tmp_path, bad):
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='two to 32'):
        series.create_series(tmp_path, {}, segments=bad, created_at=stamp())
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('kind', ['missing_slot', 'symlink_slot', 'corrupt_manifest', 'changed_result', 'extra_slot'])
def test_broken_history_blocks_new_observations_without_writes(setup, kind):
    run(setup, 0)
    run(setup, 60)
    handles = slot_handles(setup[0])
    root = Path(setup[0]['root'])
    if kind in ('missing_slot', 'symlink_slot'):
        path = Path(handles[0]['root'])
        moved = path.with_name(path.name + '-moved')
        path.rename(moved)
        if kind == 'symlink_slot':
            path.symlink_to(moved, target_is_directory=True)
    elif kind == 'corrupt_manifest':
        Path(handles[0]['root'], 'manifest.json').write_text('{}')
    elif kind == 'changed_result':
        result = next(Path(handles[0]['root']).glob('cycles/*/result.json'))
        result.write_text('{}')
    else:
        (root / 'unregistered').mkdir()
    before = snapshot(root)
    with pytest.raises((ValueError, KeyError, OSError)):
        run(setup, 120)
    assert snapshot(root) == before


def test_service_binding_tampering_rejected_even_with_new_child_pin(setup):
    root, manifest = series._manifest(setup[0])
    child = root / manifest['segments'][1]['directory'] / 'manifest.json'
    document = json.loads(child.read_text())
    document['services'].pop('learning')
    body = files._json(document)
    child.write_bytes(body)
    manifest['segments'][1]['manifest_sha256'] = journal._digest(body)
    body = files._json(manifest)
    (root / 'manifest.json').write_bytes(body)
    handle = {**setup[0], 'manifest_sha256': journal._digest(body)}
    with pytest.raises(ValueError, match='service/time binding'):
        series.inspect_series(handle)


def test_duplicate_and_out_of_sequence_direct_child_writes_rejected(setup):
    run(setup, 0)
    second = slot_handles(setup[0])[1]
    history.record_cycle(second, '0', {'engine': None, 'learning': None},
                         request_restart={s: lambda *_: None for s in setup[1]}, clock=lambda: stamp(60))
    with pytest.raises(ValueError, match='Nonsequential'):
        series.inspect_series(setup[0])
    first = slot_handles(setup[0])[0]
    history.record_cycle(first, '60', {'engine': None, 'learning': None},
                         request_restart={s: lambda *_: None for s in setup[1]}, clock=lambda: stamp(60))
    with pytest.raises(ValueError, match='Duplicate cycle'):
        series.inspect_series(setup[0])


def test_series_byte_limit_retains_readable_history_and_allows_duplicates(setup, monkeypatch):
    run(setup, 0)
    used = series.inspect_series(setup[0])['retained_bytes']
    monkeypatch.setattr(series, 'MAX_SERIES_BYTES', used + 2 * history.MAX_RECORD_BYTES - 1)
    assert series.inspect_series(setup[0])['capacity_exhausted']
    before = snapshot(Path(setup[0]['root']).parent)
    with pytest.raises(ValueError, match='capacity exhausted'):
        run(setup, 60)
    assert run(setup, 60, name='0')['reused']
    assert snapshot(Path(setup[0]['root']).parent) == before


def test_sports_log_shape_is_not_recovery_proof_across_rollover(setup):
    # Field names observed in the supplied official NHL play-by-play archive;
    # values here are synthetic, not current participation/availability evidence.
    sports = {'id': 123, 'season': 20252026, 'startTimeUTC': stamp(), 'rosterSpots': [],
              'plays': [{'eventId': 1, 'periodDescriptor': {'number': 1},
                         'timeInPeriod': '00:10', 'sortOrder': 1, 'typeDescKey': 'shot-on-goal'}]}
    run(setup, 0)
    run(setup, 60)
    result = run(setup, 120, observations={s: sports for s in setup[1]})
    for entry in result['cycles'][0]['result']['summary']['services'].values():
        assert entry['report']['state'] != 'process_healthy'
        assert not entry['report']['workflow_completion_verified']
    assert not series.inspect_series(setup[0])['production_recovery_verified']
