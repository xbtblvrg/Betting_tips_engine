"""Behavioral checks of the offline operator view; no production services."""
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine import development_restart_journal as journal
from betting_engine import development_supervision_history as history
from betting_engine import development_supervision_series as series
from betting_engine import development_supervision_view as view

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def stamp(seconds=0):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(history, 'MAX_CYCLES', 2)
    handles = {s: journal.create_journal(tmp_path.resolve(), service=s,
                                       created_at=stamp(), checkpoint_at=stamp(100))
               for s in ('engine', 'learning')}
    store = series.create_series(tmp_path.resolve(), handles, segments=3, created_at=stamp())
    return store, handles


def run(setup, at, observations=None, callbacks=None):
    store, handles = setup
    return series.record_cycle(store, str(at), observations or {s: None for s in handles},
                               request_restart=callbacks or {s: lambda *_: None for s in handles},
                               clock=lambda: stamp(at))


def health(at):
    return {'engine': {'engine': {'running': False, 'finished_at': stamp(at)}},
            'learning': {'enabled': True, 'state': 'idle', 'checked_at': stamp(at)}}


class DOM(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def test_initial_unknowns_not_success_or_complete(setup):
    report = view.inspect_status(setup[0], as_of=stamp())
    assert report['history']['cycle_count'] == 0
    assert report['attention_required']
    assert report['services']['engine']['report']['state'] == 'unobserved'
    assert report['unconfigured_services'] == ['development', 'release']
    assert not report['production_ready'] and not report['workflow_completion_verified']
    assert not report['production_recovery_verified'] and not report['atomic_snapshot']


def test_rendered_health_expires_and_read_does_not_persist_miss(setup, tmp_path):
    run(setup, 0, health(0))
    before = snapshot(tmp_path)
    fresh = view.render_status(setup[0], as_of=stamp(60))
    assert 'process_healthy' in fresh
    assert view.inspect_status(setup[0], as_of=stamp(60))['state'] == 'no_attention_in_configured_scope'
    stale = view.render_status(setup[0], as_of=stamp(101))
    assert 'observation_stale' in stale and 'Missed; persisted miss: no' in stale
    assert 'This saved view does not update' in stale
    assert snapshot(tmp_path) == before


def test_failed_attempt_and_original_checkpoint_survive_rollover_and_later_health(setup):
    run(setup, 0)
    run(setup, 60)

    def fail(*_):
        raise RuntimeError('restart denied')

    run(setup, 120, callbacks={s: fail for s in setup[1]})
    report = view.inspect_status(setup[0], as_of=stamp(120))
    attempt = report['services']['learning']['report']['attempts'][0]
    assert attempt['outcome'] == 'failed'
    run(setup, 180, health(179))
    later = view.inspect_status(setup[0], as_of=stamp(180))
    assert later['history']['historical_missed_checkpoints'] == ['engine', 'learning']
    current = later['services']['learning']['report']
    assert current['state'] == 'process_healthy'
    assert current['checkpoint_at'] == stamp(100) and current['checkpoint_miss_recorded']
    assert current['attempts'][0]['id'] == attempt['id']
    assert current['attempts'][0]['outcome'] == 'failed'
    body = view.render_status(setup[0], as_of=stamp(180))
    assert attempt['id'] in body and stamp(100) in body and 'restart denied' in body
    assert 'worker_heartbeat_progress_only' in body
    assert 'Later health does not erase these misses' in body


def test_corrupt_service_keeps_other_health_and_historical_attempt_identity(setup):
    for t in (0, 60, 120):
        run(setup, t)
    known = view.inspect_status(setup[0], as_of=stamp(120))
    attempt = known['services']['learning']['report']['attempts'][0]['id']
    folder = Path(setup[1]['learning']['root'])
    # Corrupt the pinned manifest, without touching any production database.
    (folder / 'manifest.json').write_text('{}')
    result = view.inspect_status(setup[0], as_of=stamp(120))
    assert result['services']['learning']['report'] is None
    assert result['services']['engine']['report'] is not None
    assert result['history']['historical_missed_checkpoints'] == ['engine', 'learning']
    body = view.render_status(setup[0], as_of=stamp(120))
    assert 'learning: attempt inventory unknown' in body
    assert attempt in body and 'Journal inspection error' in body


@pytest.mark.parametrize('damage', ['pin', 'missing_slot', 'symlink'])
def test_unreadable_series_has_unknown_counts_and_never_reads_services(setup, monkeypatch, damage):
    store = dict(setup[0])
    root, manifest = series._manifest(store)
    if damage == 'pin':
        store['manifest_sha256'] = '0' * 64
    elif damage == 'missing_slot':
        (root / manifest['segments'][0]['directory'] / 'manifest.json').unlink()
    else:
        file = root / 'manifest.json'
        data = file.read_bytes()
        file.unlink()
        (root / 'other.json').write_bytes(data)
        file.symlink_to(root / 'other.json')
    monkeypatch.setattr(view.cycle, 'inspect_services', lambda *_a, **_k: pytest.fail('Untrusted binding read'))
    report = view.inspect_status(store, as_of=stamp())
    assert report['history'] is None and report['services'] is None and report['attention_required']
    body = view.render_status(store, as_of=stamp())
    assert 'counts are unknown' in body and 'Cycles: 0' not in body


def test_errors_are_inert_text_and_evidence_details_collapsed(setup):
    for t in (0, 60):
        run(setup, t)
    attack = '<script>alert(1)</script><img src=x onerror="evil()">&'

    def fail(*_):
        raise RuntimeError(attack)

    run(setup, 120, callbacks={s: fail for s in setup[1]})
    dom = DOM(view.render_status(setup[0], as_of=stamp(120)))
    tags = [tag for tag, _ in dom.tags]
    assert not set(tags) & {'script', 'img', 'a', 'form', 'iframe', 'link', 'style'}
    assert 'table' in tags and 'details' in tags
    assert all('open' not in attrs for tag, attrs in dom.tags if tag == 'details')
    assert all(not key.startswith('on') for _, attrs in dom.tags for key in attrs)
    assert any('Content-Security-Policy' == attrs.get('http-equiv') for _, attrs in dom.tags)
    evidence = [json.loads(text) for text in dom.text if text.startswith('{')]
    assert any(item.get('error') == 'RuntimeError: ' + attack for item in evidence)


def test_capacity_visible_without_deletion(setup, tmp_path):
    for t in range(0, 360, 60):
        run(setup, t)
    before = snapshot(tmp_path)
    body = view.render_status(setup[0], as_of=stamp(300))
    assert 'Capacity exhausted: True' in body and 'Cycles: 6' in body
    assert snapshot(tmp_path) == before


def test_failed_cycle_and_incomplete_start_visible(setup):
    run(setup, 0, {'engine': [], 'learning': None})
    root, manifest = series._manifest(setup[0])
    (root / manifest['segments'][0]['directory'] / 'cycles' / history._cycle_key('lost')).mkdir()
    report = view.inspect_status(setup[0], as_of=stamp())
    assert report['history']['failed_cycle_count'] == 1
    assert report['history']['uncertain_cycle_count'] == 1
    body = view.render_status(setup[0], as_of=stamp())
    assert 'incomplete_start' in body and history._cycle_key('lost') in body
    assert 'Expected object or missing observation' in body


@pytest.mark.parametrize('at', [None, '', '2026-10-01', 'nonsense'])
def test_missing_or_naive_clock_rejected(setup, at):
    with pytest.raises((ValueError, TypeError)):
        view.inspect_status(setup[0], as_of=at)


def test_future_evidence_does_not_become_current(setup):
    run(setup, 120)
    report = view.inspect_status(setup[0], as_of=stamp(119))
    assert report['history'] is None and report['state'] == 'unknown'
    assert 'predates retained cycle' in report['history_error']['message']


def test_sports_schema_is_not_heartbeat_or_recovery(setup):
    # Schema inspected in the supplied official NHL play-by-play archive;
    # values here are synthetic, never current participation evidence.
    sports = {'id': 2022030145, 'startTimeUTC': stamp(), 'rosterSpots': [],
              'plays': [{'eventId': 1, 'periodDescriptor': {}, 'timeInPeriod': '00:01',
                         'sortOrder': 1, 'typeDescKey': 'shot-on-goal'}]}
    run(setup, 0, {s: sports for s in setup[1]})
    report = view.inspect_status(setup[0], as_of=stamp())
    assert all(e['report']['state'] == 'degraded' for e in report['services'].values())
    assert not report['production_recovery_verified']


def test_process_exit_and_new_reader_preserve_uncertain_attempt(setup, tmp_path):
    for t in (0, 60):
        run(setup, t)
    payload = json.dumps({'store': setup[0], 'at': stamp(120)})
    child = subprocess.run([sys.executable, '-c', '''
import json, os, sys
from betting_engine import development_supervision_series as series
from betting_engine import development_supervision_history as history
history.MAX_CYCLES = 2
p = json.load(sys.stdin)
def stop(*_):
    os._exit(29)
series.record_cycle(p['store'], 'interrupted', {'engine': None, 'learning': None},
    request_restart={'engine': stop, 'learning': stop}, clock=lambda: p['at'])
'''], input=payload, text=True, capture_output=True, timeout=20)
    assert child.returncode == 29, child.stderr
    before = snapshot(tmp_path)
    reader = subprocess.run([sys.executable, '-c', '''
import json, sys
from betting_engine.development_supervision_view import render_status
from betting_engine import development_supervision_history as history
history.MAX_CYCLES = 2
p = json.load(sys.stdin)
print(render_status(p['store'], as_of=p['at']))
'''], input=payload, text=True, capture_output=True, timeout=20)
    assert reader.returncode == 0, reader.stderr
    report = view.inspect_status(setup[0], as_of=stamp(120))
    attempt = report['services']['engine']['report']['attempts'][0]
    assert attempt['outcome'] == 'unknown'
    assert attempt['id'] in reader.stdout and 'interrupted' in reader.stdout
    assert 'uncertain or interrupted: 1' in reader.stdout
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('ack, outcome', [(None, 'succeeded'), ({'success': True}, 'unknown')])
def test_acknowledged_restart_does_not_claim_recovery(setup, ack, outcome):
    for t in (0, 60):
        run(setup, t)
    run(setup, 120, callbacks={s: lambda *_: ack for s in setup[1]})
    # The inherited dispatch contract requires None, so a dict remains
    # uncertain rather than becoming successful by truthiness.
    report = view.inspect_status(setup[0], as_of=stamp(120))
    for entry in report['services'].values():
        assert entry['report']['attempts'][0]['outcome'] == outcome
        assert entry['report']['attempts'][0]['recovery_observed_at'] is None
    assert not report['production_recovery_verified']
    body = view.render_status(setup[0], as_of=stamp(120))
    assert 'Request success is an acknowledgement, not verified service recovery' in body


def test_disabled_worker_is_distinct_from_unconfigured(tmp_path):
    handles = {'release': journal.create_journal(tmp_path.resolve(), service='release',
               created_at=stamp(), checkpoint_at=stamp(100))}
    store = series.create_series(tmp_path.resolve(), handles, created_at=stamp())
    run((store, handles), 0, {'release': {'enabled': False, 'state': 'disabled', 'checked_at': stamp()}})
    report = view.inspect_status(store, as_of=stamp())
    assert report['services']['release']['report']['state'] == 'process_disabled'
    assert report['unconfigured_services'] == ['engine', 'learning', 'development']
    assert report['state'] == 'no_attention_in_configured_scope'
    assert not report['production_ready']


def test_reader_uses_no_writer_lock(setup, tmp_path):
    with series._exclusive(setup[0]):
        before = snapshot(tmp_path)
        result = view.render_status(setup[0], as_of=stamp())
        assert 'Cycles: 0' in result
        assert snapshot(tmp_path) == before


def test_later_journal_evidence_cannot_be_used_at_earlier_view_time(setup):
    run(setup, 0, health(0))
    # The journal may advance independently of the last completed cycle. The
    # service reader must reject time travel independently for each journal.
    journal.record_observation(setup[1]['engine'], health(120)['engine'], observed_at=stamp(120))
    report = view.inspect_status(setup[0], as_of=stamp(60))
    assert report['history']['cycle_count'] == 1
    assert report['services']['engine']['report'] is None
    assert report['services']['learning']['report']['state'] == 'process_healthy'
    assert report['attention_required']
