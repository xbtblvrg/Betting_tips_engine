"""Runtime lifecycle regressions. All successful quotes/results are synthetic."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
import sqlite3

import pytest

from betting_engine.captured_price_evaluation import FrozenCaptureReports
from betting_engine.closing_runtime import closed_cohorts, evaluate_game
from test_captured_price_evaluation import START, archive, decision


class ReadOnlyExportStore:
    """Only the export/read port; no wager-writing API or production default."""

    def __init__(self, root, rows, captures=()):
        self.rows = rows
        self.reads = 0
        self.path = root / 'input.sqlite3'
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute('CREATE TABLE closing_price_captures '
                           '(id, fixture_id, league, observed_at, archive, sha256, source_url)')
                db.executemany('INSERT INTO closing_price_captures VALUES (?,?,?,?,?,?,?)',
                               [tuple(c[k] for k in ('id', 'fixture_id', 'league', 'observed_at',
                                                    'archive', 'sha256', 'source_url')) for c in captures])
        finally:
            db.close()

    def system_decisions(self):
        self.reads += 1
        return deepcopy(self.rows)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()


def run(store, root):
    result = evaluate_game(store, root, 'NBA:example', START,
                           datetime.fromisoformat(START) + timedelta(seconds=90))
    return FrozenCaptureReports(root / 'closing_studies.sqlite3').read(result['study_id'])


@pytest.mark.parametrize('status,pnl', [('lost', -3), ('won', 3.3), ('push', 0), ('open', None)])
def test_runtime_preserves_complete_lifecycle_in_frozen_study(tmp_path, status, pnl):
    original = decision()
    original.update(status=status, pnl=pnl, outcome_evidence=None if status == 'open' else {
        'score_scope': 'full_game', 'source': 'synthetic official-score fixture',
        'settled_at': '2026-09-29T23:00:00+00:00', 'unknown_publication_at': None})
    store = ReadOnlyExportStore(tmp_path, [original], [archive(tmp_path)])
    before = store.path.read_bytes()
    study = run(store, tmp_path)
    assert store.reads == 1
    assert study['reports'][0]['decision_export'] == original
    assert study['manifest']['decision_exports'] == [original]
    report = study['reports'][0]
    assert report['status'] == 'comparable'
    assert report['price_clv'] == pytest.approx(.05)
    assert study['manifest']['as_of'] == START
    summary = json.loads((tmp_path / 'closing_evaluation.json').read_text())
    row = summary['reports'][0]
    assert row['outcome_snapshot'] == {k: original[k] for k in ('status', 'pnl', 'outcome_evidence')}
    assert row['issued_stake'] == original['payload']['stake']
    assert row['issued_odds'] == original['payload']['reference_odds']
    assert 'not availability at the quote cutoff' in row['lifecycle_basis']
    assert not summary['profit_established']
    assert store.rows == [original]
    assert store.path.read_bytes() == before


def test_later_outcome_export_appends_without_rewriting_price_or_job_identity(tmp_path):
    original = decision()
    original.update(status='open', pnl=None, outcome_evidence=None)
    store = ReadOnlyExportStore(tmp_path, [original], [archive(tmp_path)])
    now = datetime.fromisoformat(START) + timedelta(seconds=90)
    planned = closed_cohorts(store.rows, now)
    first = run(store, tmp_path)
    store.rows[0].update(status='lost', pnl=-3, outcome_evidence={
        'source': 'synthetic later final score', 'fetched_at': '2026-09-29T23:00:00+00:00'})
    second = run(store, tmp_path)
    assert closed_cohorts(store.rows, now) == planned
    assert first['study_id'] != second['study_id']
    assert first['reports'][0]['comparison_sha256'] == second['reports'][0]['comparison_sha256']
    assert first['reports'][0]['closing_price'] == second['reports'][0]['closing_price']
    reopened = FrozenCaptureReports(tmp_path / 'closing_studies.sqlite3')
    assert reopened.read(first['study_id']) == first
    assert reopened.read(second['study_id']) == second
    assert first['reports'][0]['decision_export']['status'] == 'open'
    assert second['reports'][0]['decision_export']['status'] == 'lost'
    assert run(store, tmp_path) == second


def test_runtime_keeps_related_contracts_together_and_missing_closes_missing(tmp_path):
    rows = [decision('total-210'), decision('total-211')]
    rows[1]['payload'].update(line=211.5, reference_market_id='stake:fixture:other-market:other-outcome')
    rows[1]['payload']['stake_evidence']['outcome_name'] = 'Over 211.5'
    unrelated = decision('elsewhere')
    unrelated['payload']['game_id'] = 'NBA:other'
    store = ReadOnlyExportStore(tmp_path, rows + [unrelated])
    study = run(store, tmp_path)
    assert study['coverage']['decisions'] == 2
    assert study['coverage']['status_counts']['missing_closing_price'] == 2
    assert {r['decision_export']['family'] for r in study['reports']} == {r['family'] for r in rows}
    assert all(r['price_clv'] is None and r['closing_price'] is None for r in study['reports'])
    summary = json.loads((tmp_path / 'closing_evaluation.json').read_text())
    assert summary['evaluated_games'] == 1
    assert summary['evaluated_decisions'] == 2
    assert [r['outcome_snapshot']['pnl'] for r in summary['reports']] == [-3, -3]


def test_absent_outcome_fields_remain_absent(tmp_path):
    row = decision()
    for key in ('status', 'pnl', 'outcome_evidence'):
        row.pop(key)
    store = ReadOnlyExportStore(tmp_path, [row])
    study = run(store, tmp_path)
    assert study['reports'][0]['decision_export'] == row
    summary = json.loads((tmp_path / 'closing_evaluation.json').read_text())
    assert summary['reports'][0]['outcome_snapshot'] == {}
