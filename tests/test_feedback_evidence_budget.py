"""Regression: feedback_total_evidence_limit lock-out of the outcome-feedback loop.

Reproduction (pre-fix): every registry row pins its first/latest-changed review
archive. The reader loaded each distinct archive (each embeds the complete
settled inputs) into one bounded budget, and the writer refused to record any
new review while the reader failed. Accumulated generations therefore locked
the improvement loop in evidence_invalid permanently. The bounded budget itself
is unchanged; tests scale it with monkeypatch instead of creating 64 MiB files.
"""
from datetime import timedelta
import hashlib
import json

import pytest

import betting_engine.outcome_feedback as feedback
from betting_engine.outcome_feedback import record_outcome_feedback, read_outcome_feedback
from betting_engine.io_utils import atomic_json
from test_outcome_feedback import cohort, NOW


def merged(*parts):
    result = {'bets': [], 'decisions': [], 'bet_outcomes': []}
    for index, part in enumerate(parts):
        for bet in part['bets']: bet['id'] = index + 1
        for outcome in part['bet_outcomes']: outcome['bet_id'] = index + 1
        for key in result: result[key] += part[key]
    return result


def leagues(n):
    names = ('NBA', 'NHL', 'MLB', 'NFL', 'WNBA', 'NCAAF', 'NCAAM', 'NCAAW')
    return [cohort(game=f'{name}:g{i}', identity=f'd-{name}', league=name) for i, name in enumerate(names[:n])]


def generations(root, count):
    reports = []
    for n in range(1, count + 1):
        reports.append(record_outcome_feedback(root / 'data', **merged(*leagues(n)), as_of=NOW + timedelta(minutes=n)))
    return reports


def tree_hashes(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*.json'))}


def strip_to_legacy(root):
    """Exact pre-fix registry shape: no observation receipts."""
    path = root / 'data/outcome_feedback/latest.json'; state = json.loads(path.read_text())
    for row in state['proposals'].values():
        row.pop('review_receipt', None); row.pop('first_receipt', None)
    state.pop('observation_migrations', None); atomic_json(path, state)
    for p in (root / 'data/outcome_feedback/observations').glob('*.json'): p.unlink()


def sizes(root, sub):
    return sum(p.stat().st_size for p in (root / 'data/outcome_feedback' / sub).glob('*.json'))


def loss_ids(report):
    return {p['league']: p for p in report['proposals'] if p['kind'] == 'negative_original_pnl'}


def test_reproduces_legacy_lockout_then_writer_recovers_without_discarding_evidence(tmp_path, monkeypatch):
    # Deterministic reference run: what the repaired reader needs after recovery.
    reference = generations(tmp_path / 'reference', 6)[-1]
    budget = (tmp_path / 'reference' / reference['review_path']).stat().st_size + sizes(tmp_path / 'reference', 'observations')
    reports = generations(tmp_path, 5)
    assert sizes(tmp_path, 'reviews') > budget  # legacy fan-out exceeds the same budget
    strip_to_legacy(tmp_path)
    monkeypatch.setattr(feedback, 'TOTAL_READ_BUDGET', budget)
    before = read_outcome_feedback(tmp_path / 'data', now=NOW + timedelta(minutes=5))
    assert before['state'] == 'evidence_invalid' and 'feedback_total_evidence_limit' in before['error']
    archives = tree_hashes(tmp_path / 'data/outcome_feedback/reviews')
    first = {league: (p['first_seen'], p['due_at']) for league, p in loss_ids(reports[-1]).items()}

    # Valid new settled evidence must reach a durable learning proposal.
    after = record_outcome_feedback(tmp_path / 'data', **merged(*leagues(6)), as_of=NOW + timedelta(minutes=6))
    assert after['source_verified'] and after['state'] == 'attention_required', after.get('error')
    current = loss_ids(after)
    assert set(current) == {'NBA', 'NHL', 'MLB', 'NFL', 'WNBA', 'NCAAF'}
    assert {league: (p['first_seen'], p['due_at']) for league, p in current.items() if league in first} == first
    assert current['NCAAF']['first_seen'] == (NOW + timedelta(minutes=6)).isoformat()
    # Every original archive is retained byte-for-byte; nothing was dropped.
    assert {k: v for k, v in tree_hashes(tmp_path / 'data/outcome_feedback/reviews').items() if k in archives} == archives
    state = json.loads((tmp_path / 'data/outcome_feedback/latest.json').read_text())
    assert state['observation_migrations'][0]['rows'] > 0
    reread = read_outcome_feedback(tmp_path / 'data', now=NOW + timedelta(minutes=7))
    assert reread['source_verified'] and loss_ids(reread)['NBA']['evidence']['original_decisions']


def test_many_generations_read_only_the_current_review_and_bounded_observations(tmp_path, monkeypatch):
    reports = generations(tmp_path, 8)
    loaded = []
    original = feedback._load_review
    monkeypatch.setattr(feedback, '_load_review', lambda data, ref, cache: loaded.append(ref['generation']) or original(data, ref, cache))
    latest = tmp_path / reports[-1]['review_path']
    monkeypatch.setattr(feedback, 'TOTAL_READ_BUDGET', latest.stat().st_size + sizes(tmp_path, 'observations'))
    value = read_outcome_feedback(tmp_path / 'data', now=NOW + timedelta(minutes=9))
    assert value['source_verified'], value.get('error')
    assert loaded == [reports[-1]['generation']]
    nba = loss_ids(value)['NBA']
    assert nba['evidence_path'] == reports[0]['review_path'] and not nba['current_generation']
    assert nba['evidence']['original_decisions'] == leagues(1)[0]['decisions']


def test_oversized_current_evidence_still_fails_closed(tmp_path, monkeypatch):
    report = generations(tmp_path, 2)[-1]
    monkeypatch.setattr(feedback, 'TOTAL_READ_BUDGET', (tmp_path / report['review_path']).stat().st_size - 1)
    value = read_outcome_feedback(tmp_path / 'data', now=NOW + timedelta(minutes=3))
    assert value['state'] == 'evidence_invalid' and 'feedback_total_evidence_limit' in value['error']
    with pytest.raises(ValueError, match='existing_feedback_evidence_invalid'):
        record_outcome_feedback(tmp_path / 'data', **merged(*leagues(3)), as_of=NOW + timedelta(minutes=3))


def test_tampered_historical_archive_blocks_writer_and_is_not_repaired(tmp_path):
    reports = generations(tmp_path, 3)
    old = tmp_path / reports[0]['review_path']; old.write_bytes(old.read_bytes() + b' ')
    tampered = old.read_bytes()
    with pytest.raises(ValueError, match='existing_feedback_evidence_invalid.*feedback_archive_hash_mismatch'):
        record_outcome_feedback(tmp_path / 'data', **merged(*leagues(4)), as_of=NOW + timedelta(minutes=4))
    assert old.read_bytes() == tampered


@pytest.mark.parametrize('mutation', ['bytes', 'swap', 'deadline', 'missing'])
def test_observation_tampering_or_rebinding_is_evidence_invalid(tmp_path, mutation):
    generations(tmp_path, 3)
    path = tmp_path / 'data/outcome_feedback/latest.json'; state = json.loads(path.read_text())
    nba = next(r for r in state['proposals'].values() if r['review']['generation'] != state['latest_review']['generation'] and 'review_receipt' in r)
    target = tmp_path / 'data' / nba['review_receipt']['path']
    if mutation == 'bytes': target.write_bytes(target.read_bytes().replace(b'"priority":1', b'"priority":0', 1))
    elif mutation == 'swap':
        other = next(r for r in state['proposals'].values() if r['id'] != nba['id'] and 'first_receipt' in r)
        nba['review_receipt'] = other['first_receipt']; atomic_json(path, state)
    elif mutation == 'deadline':
        nba['due_at'] = (NOW + timedelta(days=9)).isoformat(); atomic_json(path, state)
    else: target.unlink()
    value = read_outcome_feedback(tmp_path / 'data', now=NOW + timedelta(minutes=4))
    assert value['state'] == 'evidence_invalid' and not value['source_verified']


def test_same_generation_rerun_writes_no_observation_or_registry_change(tmp_path):
    generations(tmp_path, 2)
    before = tree_hashes(tmp_path / 'data/outcome_feedback')
    record_outcome_feedback(tmp_path / 'data', **merged(*leagues(2)), as_of=NOW + timedelta(minutes=2))
    after = tree_hashes(tmp_path / 'data/outcome_feedback')
    assert {k: v for k, v in after.items() if k != 'latest.json'} == {k: v for k, v in before.items() if k != 'latest.json'}
