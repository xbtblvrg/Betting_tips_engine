"""Experimental complete-delta check layered on the D13 handoff evaluator.

No filesystem access, integration, receipts, model activation or release authority.
The controller must authenticate the supplied pre-edit baseline and its pinned
digest independently. The production base is not the task baseline: the latter
can include previously accepted peers. Rebased work needs a newly reviewed base.
"""
import hashlib

from . import release_acceptance_rehearsal as handoff


def assess_candidate_delta(receipt_bytes, *, run_id, task_id, candidate_files,
                           archived_changes, current_base, task_baseline,
                           expected_baseline_sha256):
    """Verify that every task change is exactly declared and archived.

    Inputs are detached byte/hash snapshots, not paths or live mutable mappings.
    ``task_baseline`` is the complete controller-captured workspace hash manifest
    from before task edits (including accepted peers), pinned by the separately
    retained ``expected_baseline_sha256``. Both use the existing handoff canonical
    JSON digest. An empty/missing baseline is not proof of a new workspace.

    Deletions are blocked: candidate-acceptance-v1 archives extant changed bytes
    only. An unchanged file cannot stand in for an omitted change. Consistency
    proves neither authentic review nor test/model/settlement correctness.
    """
    checked = handoff.assess_candidate_acceptance(
        receipt_bytes, run_id=run_id, task_id=task_id,
        candidate_files=candidate_files, archived_changes=archived_changes,
        current_base=current_base)
    result = {**checked, 'scope': 'candidate_delta_rehearsal',
              'baseline_authentication_required': True}
    if checked['state'] != 'consistent_candidate_snapshot':
        return result
    result['state'] = 'blocked'
    try:
        baseline = handoff._hashes(task_baseline)
        baseline_digest = hashlib.sha256(handoff._canonical(baseline)).hexdigest()
        result['task_baseline_sha256'] = baseline_digest
        handoff._require(isinstance(expected_baseline_sha256, str)
                         and baseline_digest == expected_baseline_sha256,
                         'task_baseline_digest_mismatch')
        candidate = handoff._files(candidate_files)
        added = set(candidate) - set(baseline)
        deleted = set(baseline) - set(candidate)
        modified = {name for name in set(baseline) & set(candidate)
                    if baseline[name] != candidate[name]}
        actual = added | modified | deleted
        declared = set(checked['changed_files'])
        result.update(added_files=sorted(added), modified_files=sorted(modified),
                      deleted_files=sorted(deleted),
                      undeclared_changes=sorted(actual - declared),
                      declared_unchanged_files=sorted(declared - actual))
        handoff._require(not deleted, 'candidate_deletions_not_supported')
        handoff._require(actual == declared, 'candidate_delta_membership')
        return {**result, 'state': 'consistent_candidate_delta', 'reason': None}
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        return {**result, 'reason': str(exc)}
