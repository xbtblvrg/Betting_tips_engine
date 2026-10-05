"""Retained original outcomes are diagnostics, not independent new trials.

No database, Engine, fitting, source fetch or account verification is involved.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.contracts import contract_value, split_market
from betting_engine.domain import parse_time
from betting_engine.period_contracts import scoped_total_line, validate_scoped_specifiers

ROOT = Path(__file__).resolve().parents[1] / 'data/validation_sources/lifecycle-outcomes-v1'


def read_bound(entry):
    path = ROOT / entry['path']
    assert path.resolve().is_relative_to((ROOT / 'data').resolve())
    assert not path.is_symlink()
    raw = path.read_bytes()
    assert len(raw) == entry['bytes']
    assert hashlib.sha256(raw).hexdigest() == entry['sha256']
    return json.loads(raw)


def test_all_attached_loss_diagnostics_reproduce_original_payoffs_by_game():
    manifest = json.loads((ROOT / 'data/development_inputs/manifest.json').read_text())
    export = read_bound(next(f for f in manifest['files']
                             if f['path'] == 'data/development_inputs/system_decisions.json'))
    assert not export['truncated']
    assert export['total_records'] == export['exported_records'] == len(export['records'])
    original = deepcopy(export)
    records = {r['family']: r for r in export['records']}
    assert len(records) == len(export['records'])
    evidence_manifest = json.loads((ROOT / 'data/engineering-evidence-manifest.json').read_text())
    checked = set()
    clusters = defaultdict(list)
    gaps = 0
    for entry in evidence_manifest['files']:
        gap = read_bound(entry).get('gap', {})
        if not gap.get('id', '').startswith('outcome:'):
            continue
        gaps += 1
        evidence = gap['evidence']['proposal']['evidence']
        selected = [records[k] for k in evidence['original_decision_ids']]
        assert len(selected) == evidence['statistics']['count']
        assert {r['payload']['game_id'] for r in selected} == set(evidence['game_ids'])
        assert len(set(evidence['game_ids'])) == evidence['statistics']['games']
        assert sum(r['pnl'] for r in selected) == pytest.approx(evidence['statistics']['pnl'])
        for row in sorted(selected, key=lambda r: parse_time(r['issued_at'])):
            tip, outcome = row['payload'], row['outcome_evidence']
            assert tip['id'] == row['tip_id']
            assert parse_time(row['issued_at']) < parse_time(tip['start'])
            assert parse_time(row['issued_at']) < parse_time(outcome['settled_at'])
            scope, _ = split_market(tip['market'])
            assert outcome['score_scope']['scope'] == scope != 'full_game'
            assert outcome['score_scope']['source_sha256']
            value = contract_value(tip['market'], tip['side'], tip['line'],
                                   outcome['home_score'], outcome['away_score'])
            status = 'won' if value > 0 else 'lost' if value < 0 else 'push'
            pnl = tip['stake'] * (tip['reference_odds'] - 1) if value > 0 else -tip['stake'] if value < 0 else 0
            assert row['status'] == status
            assert row['pnl'] == pytest.approx(round(pnl, 4))
            # Reproduce nominal return from the original probability and price,
            # never a current model substituted into a historical decision.
            assert tip['expected_roi'] == pytest.approx(
                tip['probability'] * tip['reference_odds'] + tip['push_probability'] - 1)
            if row['family'] not in checked:
                checked.add(row['family'])
                clusters[tip['game_id']].append(row)
    assert gaps == 8
    assert len(checked) == 14
    assert len(clusters) == 4  # Related contracts must not become 14 independent games.
    assert sum(r['pnl'] for rows in clusters.values() for r in rows) == pytest.approx(-13.85)
    assert export == original


def test_oversized_scope_evidence_cannot_be_silently_accepted():
    # Same bounded specifier envelope as the existing handicap parser.
    for specifier in ('total=' + '0' * 513, 'periodnr=' + '1' * 513):
        with pytest.raises(ValueError, match='oversized'):
            scoped_total_line(specifier, 'period_1')
        with pytest.raises(ValueError, match='oversized'):
            validate_scoped_specifiers(specifier, 'period_1')
