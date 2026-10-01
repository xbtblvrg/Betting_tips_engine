from copy import deepcopy
from datetime import timedelta

import pytest

from betting_engine.domain import utcnow
from betting_engine.governance import EvidenceGuard, KnowledgeAgent, verify_publication
from betting_engine.instructions import current_execution_review
from betting_engine.stake_website_availability import EVENT_SLUGS, website_availability_issue
from test_governance import guard_case


def observed(slug):
    now = utcnow()
    return {'league': 'NHL', 'market': 'regulation:double_chance', 'model_scope': 'regulation',
            'reference_platform': 'Stake.com', 'reference_url': 'https://stake.com/sports/ice-hockey/usa/nhl/' + slug,
            'stake_evidence': {'fixture_slug': slug}, 'quote_at': now.isoformat(),
            'valid_until': (now + timedelta(minutes=5)).isoformat(), 'start': (now + timedelta(hours=2)).isoformat()}


@pytest.mark.parametrize('slug', sorted(EVENT_SLUGS))
def test_both_website_conflicts_block_current_instructions_and_publication(slug):
    tip = observed(slug); original = deepcopy(tip)
    issue = website_availability_issue(tip)
    assert issue['code'] == 'stake_website_market_unconfirmed'
    review = current_execution_review(tip, utcnow(), .03)
    assert review['state'] == 'website_feed_conflict' and not review['actionable']
    assert review['website_evidence'] == issue
    with pytest.raises(ValueError, match='stake_website_market_unconfirmed'):
        verify_publication(tip, utcnow())
    assert tip == original


@pytest.mark.parametrize('market', ['moneyline', 'regulation:three_way', 'period_1:double_chance', 'period_2:double_chance', 'period_3:double_chance', 'totals', 'spreads'])
def test_distinct_markets_on_the_same_event_are_not_blocked(market):
    tip = observed(next(iter(EVENT_SLUGS))); tip['market'] = market
    assert website_availability_issue(tip) is None


def test_missing_route_alone_and_unobserved_events_are_not_blocked():
    tip = observed('another-nhl-event'); tip['stake_evidence']['market_groups'] = [{'id': 'w', 'name': 'winner'}]
    assert website_availability_issue(tip) is None
    tip['league'] = 'MLB'
    assert website_availability_issue(tip) is None


@pytest.mark.parametrize('missing', ['reference_url', 'stake_evidence'])
def test_available_exact_event_identity_still_detects_conflict(missing):
    tip = observed(next(iter(EVENT_SLUGS))); tip.pop(missing)
    assert website_availability_issue(tip) is not None


def test_evidence_guard_records_the_specific_website_failure():
    args = guard_case()
    args[0].update(observed(next(iter(EVENT_SLUGS))))
    review = EvidenceGuard(KnowledgeAgent()).check(*args)
    assert not review['passed']
    assert 'stake_website_market_unconfirmed' in review['errors']


def test_normal_guarded_publication_is_unchanged():
    args = guard_case()
    EvidenceGuard(KnowledgeAgent()).check(*args)
    verify_publication(args[0], utcnow())


@pytest.mark.parametrize('after', ['changed', None, 'SYMLINK'])
def test_autonomous_candidates_cannot_change_remove_or_replace_the_website_hold(after):
    from betting_engine.development import validate_changes
    path = 'src/betting_engine/stake_website_availability.py'
    before = {path: 'original'}
    changed = {path: after} if after is not None else {}
    with pytest.raises(ValueError, match='Protected file changed'):
        validate_changes(before, changed)
