"""Passive acceptance checkpoint assessment over detached controller evidence.

No paths, writes, receipt creation, service actions or gap resolution. Pins must
come from an independently retained controller snapshot. Consistency is not
authenticated provenance, independent review, deployment or model approval.
"""
import hashlib
import json

from . import release_acceptance_rehearsal as handoff
from .release_acceptance_delta import assess_candidate_delta


MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


def _document(body):
    handoff._require(isinstance(body, bytes), 'evidence_requires_bytes')
    handoff._require(len(body) <= MAX_DOCUMENT_BYTES, 'evidence_byte_bound')
    value = json.loads(body, object_pairs_hook=handoff._object,
                       parse_constant=handoff._constant)
    handoff._require(isinstance(value, dict), 'evidence_requires_object')
    # Also rejects overflowing JSON exponents, including unused nested fields.
    handoff._canonical(value)
    return value


def assess_acceptance_checkpoint(gap_bytes, *, expected_gap_sha256, as_of,
                                 receipt_bytes, run_id, task_id, candidate_files,
                                 archived_changes, current_base, task_baseline,
                                 expected_baseline_sha256):
    """Inspect one open acceptance_missing gap without mutating its history.

    Reuses the existing complete-delta evaluator. Adds pinned gap identity,
    deadline/as-of chronology and preservation of every baseline test. Only the
    original run can satisfy this assessment; a later repair run requires the
    controller's separate attribution. An elapsed deadline without consistent
    evidence is not proof that an unobserved historical handoff never happened.
    Late consistent evidence retains a missed checkpoint. The supplied gap
    always stays open, even when the assessment is consistent.
    """
    report = {
        'version': 'acceptance-checkpoint-assessment-v1',
        'scope': 'offline_candidate_checkpoint', 'state': 'blocked',
        'reason': None, 'checkpoint_state': 'unassessed',
        'checkpoint_missed': None, 'gap_resolution_authorized': False,
        'promotion_authorized': False, 'model_activation_authorized': False,
        'production_ready': False, 'production_recovery_verified': False,
        'controller_authentication_required': True,
        'independent_review_verified': False,
    }
    try:
        gap = _document(gap_bytes)
        gap_hash = hashlib.sha256(gap_bytes).hexdigest()
        handoff._require(gap_hash == expected_gap_sha256, 'gap_pin_mismatch')
        handoff._require(isinstance(task_id, str) and bool(task_id)
                         and isinstance(run_id, str) and bool(run_id), 'invalid_identity')
        handoff._require(gap.get('id') == task_id + ':acceptance_missing'
                         and gap.get('task_id') == task_id and gap.get('run_id') == run_id
                         and gap.get('code') == 'acceptance_missing'
                         and gap.get('state') == 'open', 'gap_identity_or_state')
        first = handoff._time(gap.get('first_seen'))
        last = handoff._time(gap.get('last_seen'))
        due = handoff._time(gap.get('due_at'))
        now = handoff._time(as_of)
        handoff._require(first <= last <= now and first <= due, 'gap_time_order')
        report.update(gap=gap, gap_sha256=gap_hash, as_of=as_of,
                      checkpoint_state=('deadline_elapsed_without_consistent_acceptance'
                                        if now > due else 'awaiting_acceptance'),
                      checkpoint_missed=now > due)
        if receipt_bytes is not None:
            receipt = _document(receipt_bytes)
            report['receipt_sha256'] = hashlib.sha256(receipt_bytes).hexdigest()
        else:
            receipt = None
        delta = assess_candidate_delta(
            receipt_bytes, run_id=run_id, task_id=task_id,
            candidate_files=candidate_files, archived_changes=archived_changes,
            current_base=current_base, task_baseline=task_baseline,
            expected_baseline_sha256=expected_baseline_sha256)
        report['candidate_assessment'] = delta
        handoff._require(delta['state'] == 'consistent_candidate_delta', delta['reason'])
        integrated = handoff._time(receipt.get('integrated_at'))
        accepted = receipt['result']
        started = handoff._time(accepted.get('started_at'))
        finished = handoff._time(accepted.get('finished_at'))
        handoff._require(started <= finished <= integrated <= now,
                         'acceptance_outside_observed_time')
        handoff._require(first <= integrated, 'acceptance_predates_missing_gap')
        changed_tests = sorted(name for name, digest in task_baseline.items()
                               if name.startswith('tests/')
                               and hashlib.sha256(candidate_files[name]).hexdigest() != digest)
        report['changed_existing_tests'] = changed_tests
        handoff._require(not changed_tests, 'existing_tests_changed')
        handoff._require(any(name.startswith('tests/test_') and name.endswith('.py')
                             for name in delta['added_files']), 'new_regression_required')
        handoff._require(any(name.startswith('src/betting_engine/')
                             for name in delta['changed_files']), 'implementation_required')
        return {**report, 'state': 'consistent_acceptance_evidence',
                'checkpoint_state': ('accepted_late' if integrated > due else 'accepted_on_time'),
                'checkpoint_missed': integrated > due, 'accepted_at': receipt['integrated_at'],
                'reason': None}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        return {**report, 'reason': str(exc)}
