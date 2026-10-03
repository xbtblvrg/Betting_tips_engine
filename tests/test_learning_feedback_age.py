from datetime import timedelta

from betting_engine.domain import utcnow
from betting_engine.learning import choose_learning_job


def row(kind, id, now, *, created_seconds=60, next_attempt=None, status='queued'):
    created = now - timedelta(seconds=created_seconds)
    return {
        'id': id,
        'kind': kind,
        'status': status,
        'created_at': created.isoformat(),
        'next_attempt': (next_attempt or created).isoformat(),
    }


def test_aged_feedback_overtakes_continuous_source_refresh_and_shadow_queue():
    now = utcnow()
    rows = [
        row('source_refresh', 'source-1', now, created_seconds=3600),
        row('source_refresh', 'source-2', now, created_seconds=1800),
        row('shadow_evaluation', 'shadow', now, created_seconds=1200),
        row('outcome_review', 'outcome', now, created_seconds=300),
    ]

    selected, proof = choose_learning_job(rows, [], now)

    assert selected['id'] == 'outcome'
    assert proof['reason'] == 'aged_feedback_sla'
    assert proof['selected_kind'] == 'outcome_review'
    assert proof['feedback_wait_seconds'] >= 300


def test_feedback_sla_has_299_second_no_age_and_300_second_boundary():
    now = utcnow()
    source = row('source_refresh', 'source', now, created_seconds=0)
    feedback = row('outcome_review', 'feedback', now, created_seconds=299)

    selected, proof = choose_learning_job([source, feedback], [], now)
    assert selected['id'] == 'source'
    assert proof['reason'] == 'normal_priority'

    feedback = row('outcome_review', 'feedback', now, created_seconds=300)
    selected, proof = choose_learning_job([source, feedback], [], now)
    assert selected['id'] == 'feedback'
    assert proof['reason'] == 'aged_feedback_sla'


def test_feedback_age_uses_eligible_at_and_excludes_future_retry():
    now = utcnow()
    source = row('source_refresh', 'source', now, created_seconds=0)

    # Old creation time does not make a retry aged while its backoff is active.
    future = row('outcome_review', 'future', now, created_seconds=3600,
                 next_attempt=now + timedelta(seconds=1), status='retry')
    selected, _ = choose_learning_job([source, future], [], now)
    assert selected['id'] == 'source'

    # The retry becomes due, but its eligible age starts at next_attempt.
    feedback = row('outcome_review', 'feedback', now, created_seconds=3600,
                   next_attempt=now - timedelta(seconds=299), status='retry')
    selected, proof = choose_learning_job([source, feedback], [], now)
    assert selected['id'] == 'source'
    assert proof['reason'] == 'normal_priority'

    feedback['next_attempt'] = (now - timedelta(seconds=300)).isoformat()
    selected, proof = choose_learning_job([source, feedback], [], now)
    assert selected['id'] == 'feedback'
    assert proof['reason'] == 'aged_feedback_sla'


def test_all_due_restore_kinds_precede_aged_feedback_and_background_fairness():
    now = utcnow()
    for kind in ('model_restore', 'segment_restore', 'period_restore'):
        rows = [
            row('source_refresh', 'source', now, created_seconds=3600),
            row('outcome_review', 'feedback', now, created_seconds=1800),
            row('model_search', 'background', now, created_seconds=3600),
            row(kind, 'restore', now, created_seconds=60),
        ]
        selected, proof = choose_learning_job(
            rows, [{'kind': 'source_refresh'}] * 3, now
        )
        assert selected['id'] == 'restore'
        assert proof['reason'] == 'normal_priority'


def test_feedback_tie_break_is_deterministic():
    now = utcnow()
    first = row('outcome_review', 'z-outcome', now, created_seconds=600)
    second = row('outcome_review', 'a-outcome', now, created_seconds=600)

    selected, proof = choose_learning_job([first, second], [], now)

    assert selected['id'] == 'a-outcome'
    assert proof['reason'] == 'aged_feedback_sla'


def test_unaged_feedback_keeps_legacy_priority_order():
    now = utcnow()
    rows = [
        row('source_refresh', 'source', now, created_seconds=0),
        row('closing_evaluation', 'closing', now, created_seconds=299),
    ]

    selected, proof = choose_learning_job(rows, [], now)

    assert selected['id'] == 'source'
    assert proof['reason'] == 'normal_priority'
