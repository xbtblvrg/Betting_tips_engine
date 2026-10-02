"""Exact original D20 regression inputs for private controller validation only.

No current research export, network, model loading or decision/ledger mutation.
The original manifest and every file it names stay intact because an unchanged
regression verifies their immutability, including eight injury-related entries.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from . import release_fixture_inputs as base
from . import workspace_copy

CONFIG = 'config/release_sports_fixture_inputs.json'
VERSION = 'release-sports-fixture-inputs-v1'
ORIGIN = '20261001T130457680400-D20'
REPORT = 'docs/D20_PUNTER_RECOVERY_INPUTS.json'
INPUT = 'data/sports_source_evidence'
RECEIPT = '.release-sports-fixture-receipt.json'
TEST_NAMES = ('archive_isolation', 'correction_availability', 'correction_schema_121517',
    'defensive_identity_recovery', 'manifest_boundary', 'missing_role_team',
    'native_marker_consistency', 'native_phase_recovery', 'nhl_opposing_roles',
    'nhl_turnover_penalty_identity', 'passer_identity_recovery', 'possession_indicator_recovery',
    'publication_provenance', 'punter_identity_recovery', 'recovered_goal_credit_and_terminal',
    'recovered_native', 'recovered_scoring_identity', 'recovered_terminal_chronology',
    'required_identity_schema', 'return_team_recovery', 'review_recovery',
    'scoring_schema_recovery', 'touchdown_marker_recovery')
TESTS = tuple('tests/test_d20_' + name + '.py' for name in TEST_NAMES)
MAX_FILE = 24 * 1024**2
MAX_TOTAL = 48 * 1024**2
SCOPE = 'Complete original frozen regression manifest closure only; not current research, model approval or task acceptance.'


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def sports_fixture_required(workspace):
    """Presence-only controller routing hint; it grants no delivery authority."""
    workspace = Path(workspace)
    return any(os.path.lexists(workspace / name) for name in (*TESTS, INPUT + '/' + RECEIPT))


def _declaration(root, workspace):
    present = [name for name in TESTS if base._path(workspace, name).exists()]
    receipt = base._path(workspace, INPUT + '/' + RECEIPT)
    if not present:
        if receipt.exists():
            raise ValueError('Sports fixture collecting tests removed after delivery')
        return None
    if set(present) != set(TESTS):
        raise ValueError('Sports fixture requires the complete original test cohort')
    body = base._read(base._path(root, CONFIG), 64 * 1024)
    value = base._json(body)
    base._keys(value, ('version', 'owner', 'source_run', 'acceptance_sha256', 'input_report', 'tests', 'files', 'cohort'))
    if value['version'] != VERSION or value['owner'] != 'D20' or value['source_run'] != ORIGIN:
        raise ValueError('Unrecognized sports fixture authority')
    if not _digest(value['acceptance_sha256']):
        raise ValueError('Invalid sports fixture acceptance pin')
    base._keys(value['input_report'], ('path', 'sha256'))
    if value['input_report']['path'] != REPORT or not _digest(value['input_report']['sha256']):
        raise ValueError('Invalid sports fixture input report')
    tests = value['tests']
    if not isinstance(tests, list) or len(tests) != len(TESTS):
        raise ValueError('Invalid sports fixture test cohort')
    seen = set()
    for row in tests:
        base._keys(row, ('path', 'sha256'))
        if row['path'] not in TESTS or row['path'] in seen or not _digest(row['sha256']):
            raise ValueError('Invalid sports fixture test binding')
        seen.add(row['path'])
        if _sha(base._read(base._path(workspace, row['path']), 2 * 1024**2)) != row['sha256']:
            raise ValueError('Sports fixture collecting test changed')
    files = value['files']
    if not isinstance(files, list) or len(files) != 25:
        raise ValueError('Sports fixture requires exactly twenty-five original bodies')
    seen = set(); total = 0
    for row in files:
        base._keys(row, ('path', 'sha256', 'bytes'))
        name = row['path']
        if (not isinstance(name, str) or not re.fullmatch(r'manifest\.json|raw/[0-9a-f]{64}\.(data|json)', name)
                or name in seen or not _digest(row['sha256']) or type(row['bytes']) is not int
                or not 0 < row['bytes'] <= (1024**2 if name.endswith('.json') else MAX_FILE)):
            raise ValueError('Invalid sports fixture file binding')
        seen.add(name); total += row['bytes']
    if 'manifest.json' not in seen or total > MAX_TOTAL:
        raise ValueError('Sports fixture closure exceeds its bound')
    cohort = value['cohort']
    base._keys(cohort, ('manifest_created_at', 'native_sources', 'original_manifest_entries',
                        'selected_manifest_entries', 'complete_manifest_export'))
    if (not isinstance(cohort['manifest_created_at'], str)
            or any(type(cohort[k]) is not int for k in ('native_sources', 'original_manifest_entries', 'selected_manifest_entries'))
            or (cohort['native_sources'], cohort['original_manifest_entries'], cohort['selected_manifest_entries']) != (8, 24, 24)
            or cohort['complete_manifest_export'] is not True):
        raise ValueError('Invalid sports fixture cohort scope')
    acceptance_body = base._read(base._path(root, 'data/development/runs/' + ORIGIN + '/candidate-acceptance.json'), 2 * 1024**2)
    if _sha(acceptance_body) != value['acceptance_sha256']:
        raise ValueError('Original sports fixture acceptance changed')
    acceptance = base._json(acceptance_body)
    if (acceptance.get('run_id') != ORIGIN or acceptance.get('integration_state') != 'committed'
            or acceptance.get('result', {}).get('task_id') != 'D20'
            or any(acceptance.get('candidate_manifest', {}).get(row['path']) != row['sha256'] for row in tests)
            or acceptance.get('candidate_manifest', {}).get(REPORT) != value['input_report']['sha256']):
        raise ValueError('Original acceptance does not pin sports tests and input report')
    report_body = base._read(base._path(root, 'data/development/runs/' + ORIGIN + '/accepted-files/' + REPORT), 2 * 1024**2)
    if _sha(report_body) != value['input_report']['sha256']:
        raise ValueError('Original sports fixture input report changed')
    report = base._json(report_body)
    if report.get('task') != 'D20' or report.get('dispatch_run_id') != ORIGIN:
        raise ValueError('Original sports fixture report identity changed')
    return value, _sha(body), report


def _sources(root, declaration):
    value, _, report = declaration
    bodies = {}
    for row in value['files']:
        source = base._path(root, 'data/release_fixture_archives/' + row['sha256'] + '.bin')
        body = base._read(source, MAX_FILE)
        if len(body) != row['bytes'] or _sha(body) != row['sha256']:
            raise ValueError('Original sports fixture archive changed')
        bodies[row['path']] = body
    manifest = base._json(bodies['manifest.json'])
    matched = [r for r in report.get('verified_manifests', []) if isinstance(r, dict) and r.get('path') == INPUT + '/manifest.json']
    if (len(matched) != 1 or matched[0].get('sha256') != _sha(bodies['manifest.json'])
            or matched[0].get('errors') != [] or matched[0].get('verified_files') != 24):
        raise ValueError('Accepted input report does not pin the original sports manifest')
    files = manifest.get('files', {})
    if (manifest.get('schema') != 'public-development-sports-evidence-v1'
            or manifest.get('created_at') != value['cohort']['manifest_created_at']
            or not isinstance(files, dict) or len(files) != 24):
        raise ValueError('Original sports manifest cohort changed')
    sources = manifest.get('sources')
    if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
        raise ValueError('Invalid original sports sources')
    native = [s for s in sources if s.get('kind') == 'native_events']
    if len(native) != 8:
        raise ValueError('Sports native source cohort incomplete')
    expected = {'manifest.json'}
    for source in native:
        for key in ('blob_path', 'metadata_path'):
            if not isinstance(source.get(key), str):
                raise ValueError('Missing native sports source path')
            expected.add(source[key])
    if not expected.issubset(bodies) or set(bodies) != {'manifest.json', *files}:
        raise ValueError('Sports fixture does not exactly cover original manifest and native sources')
    report_files = matched[0].get('files')
    if not isinstance(report_files, list):
        raise ValueError('Original sports report file pins missing')
    for row in value['files']:
        if row['path'] == 'manifest.json':
            continue
        expected_row = {'bytes': row['bytes'], 'sha256': row['sha256']}
        if files.get(row['path']) != expected_row or [r for r in report_files if isinstance(r, dict) and r.get('path') == row['path']] != [row]:
            raise ValueError('Native sports archive differs from original manifest/report')
    for source in native:
        if (_sha(bodies[source['blob_path']]) != source.get('source_sha256')
                or _sha(bodies[source['metadata_path']]) != source.get('metadata_sha256')):
            raise ValueError('Native sports source body/sidecar identity changed')
    return bodies


def _receipt(declaration):
    value, config_hash, _ = declaration
    return {'version': VERSION, 'state': 'frozen_release_sports_fixture', 'scope': SCOPE,
        'config_sha256': config_hash, 'exporter_sha256': _sha(Path(__file__).read_bytes()),
        'fixture_helper_sha256': _sha(Path(base.__file__).read_bytes()),
        'directory_helper_sha256': _sha(Path(workspace_copy.__file__).read_bytes()),
        'owner': 'D20', 'source_run': ORIGIN, 'acceptance_sha256': value['acceptance_sha256'],
        'input_report': value['input_report'], 'tests': sorted(value['tests'], key=lambda r: r['path']),
        'files': sorted(value['files'], key=lambda r: r['path']), 'cohort': value['cohort'],
        'complete_manifest_export': True, 'current_research_export': False}


def _verify_destination(workspace, expected):
    target = base._path(workspace, INPUT)
    if not target.is_dir() or {p.name for p in target.iterdir()} != {'manifest.json', 'raw', RECEIPT}:
        raise ValueError('Sports fixture destination incomplete or contains extra entries')
    raw = base._path(workspace, INPUT + '/raw')
    raw_names = {Path(row['path']).name for row in expected['files'] if row['path'].startswith('raw/')}
    if not raw.is_dir() or {p.name for p in raw.iterdir()} != raw_names:
        raise ValueError('Sports fixture raw closure incomplete or contains extra entries')
    if base._json(base._read(base._path(workspace, INPUT + '/' + RECEIPT), 64 * 1024)) != expected:
        raise ValueError('Sports fixture receipt changed or missing')
    for row in expected['files']:
        body = base._read(base._path(workspace, INPUT + '/' + row['path']), MAX_FILE)
        if len(body) != row['bytes'] or _sha(body) != row['sha256']:
            raise ValueError('Delivered sports fixture changed')
    return expected


def verify_release_sports_fixture_inputs(root, workspace):
    root = Path(root).resolve(strict=True)
    workspace = Path(os.path.abspath(workspace))
    if not sports_fixture_required(workspace):
        return {'state': 'not_required'}
    root, workspace = base._workspace(root, workspace)
    declaration = _declaration(root, workspace)
    if declaration is None:
        return {'state': 'not_required'}
    _sources(root, declaration)
    result = _verify_destination(workspace, _receipt(declaration))
    if _declaration(root, workspace) != declaration:
        raise ValueError('Sports fixture authority changed during verification')
    return result


def export_release_sports_fixture_inputs(root, workspace):
    root = Path(root).resolve(strict=True)
    workspace = Path(os.path.abspath(workspace))
    if not sports_fixture_required(workspace):
        return {'state': 'not_required'}
    root, workspace = base._workspace(root, workspace)
    declaration = _declaration(root, workspace)
    if declaration is None:
        return {'state': 'not_required'}
    bodies = _sources(root, declaration)
    expected = _receipt(declaration)
    target = base._path(workspace, INPUT)
    if target.exists():
        return verify_release_sports_fixture_inputs(root, workspace)
    data = base._path(workspace, 'data'); data.mkdir(exist_ok=True)
    parent_fd = base._directory(data)
    try:
        original_parent = os.fstat(parent_fd)
        name = '.release-sports-fixture-' + secrets.token_hex(16)
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        temporary_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        raw_fd = None
        try:
            os.mkdir('raw', mode=0o700, dir_fd=temporary_fd)
            raw_fd = os.open('raw', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=temporary_fd)
            for row in expected['files']:
                base._write(raw_fd if row['path'].startswith('raw/') else temporary_fd,
                            Path(row['path']).name, bodies[row['path']])
            receipt = json.dumps(expected, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n'
            base._write(temporary_fd, RECEIPT, receipt)
            os.fsync(raw_fd); os.fsync(temporary_fd)
            if _declaration(root, workspace) != declaration or _sources(root, declaration) != bodies:
                raise ValueError('Sports fixture authority changed during preparation')
            current = base._path(workspace, 'data').stat()
            if (current.st_dev, current.st_ino) != (original_parent.st_dev, original_parent.st_ino):
                raise ValueError('Sports fixture destination parent changed')
            base._path(workspace, INPUT)
            base._publish(parent_fd, name, 'sports_source_evidence', temporary_fd)
            delivered = os.stat('sports_source_evidence', dir_fd=parent_fd, follow_symlinks=False)
            original = os.fstat(temporary_fd)
            if (delivered.st_dev, delivered.st_ino) != (original.st_dev, original.st_ino):
                raise ValueError('Published sports fixture directory replaced')
            os.fsync(parent_fd)
            return verify_release_sports_fixture_inputs(root, workspace)
        finally:
            # Preserve interrupted private preparations; never recursively delete
            # a pathname which another process could have replaced.
            if raw_fd is not None:
                os.close(raw_fd)
            os.close(temporary_fd)
    finally:
        os.close(parent_fd)
