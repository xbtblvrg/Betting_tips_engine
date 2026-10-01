"""Synthetic quote-lifetime regressions; no live observations or model approval."""
from copy import deepcopy

import pytest

import test_d12_live_shadow as fixtures
from betting_engine.live_shadow import assess_live_shadow


@pytest.fixture
def args(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, "_EVIDENCE_ROOT", tmp_path)
    return fixtures.scenario()


def test_fresh_matching_quote_cannot_resurrect_expired_review(args):
    args["reviewed_quote"]["source_updated_at"] = fixtures.at(-5001)
    original = deepcopy(args)
    verdict = assess_live_shadow(**args)
    assert verdict.reasons == ("stale_reviewed_quote",)
    assert not verdict.shadow_eligible
    assert not verdict.production_eligible
    assert args == original


@pytest.mark.parametrize("extra_age,eligible", [(0, True), (1, False)])
def test_review_lifetime_includes_projected_manual_execution(args, extra_age, eligible):
    # The 1600 ms manual/acceptance/uncertainty budget makes this age exactly
    # 5000 ms at execution, although it is only 3400 ms at assessment time.
    args["reviewed_quote"]["source_updated_at"] = fixtures.at(-3400 - extra_age)
    verdict = assess_live_shadow(**args)
    assert verdict.shadow_eligible is eligible
    assert verdict.reasons == (() if eligible else ("stale_reviewed_quote",))
    assert not verdict.production_eligible


def test_later_receipt_does_not_refresh_review_source_time(args):
    args["reviewed_quote"].update(source_updated_at=fixtures.at(-3401),
                                  received_at=fixtures.at(-200))
    args["current_quote"]["received_at"] = fixtures.at(-100)
    assert assess_live_shadow(**args).reasons == ("stale_reviewed_quote",)


def test_expired_review_requires_new_review_snapshot(args):
    args["reviewed_quote"]["source_updated_at"] = fixtures.at(-5001)
    assert assess_live_shadow(**args).reasons == ("stale_reviewed_quote",)
    # An explicit new review of identical terms can use the fresh observation.
    args["reviewed_quote"] = deepcopy(args["current_quote"])
    verdict = assess_live_shadow(**args)
    assert verdict.shadow_eligible
    assert not verdict.production_eligible
    assert "independent_state_conditioned_training_not_approved" in verdict.production_blockers


def test_expired_current_quote_retains_existing_reason(args):
    args["reviewed_quote"]["source_updated_at"] = fixtures.at(-6000)
    args["current_quote"]["source_updated_at"] = fixtures.at(-5000)
    assert assess_live_shadow(**args).reasons == ("stale_quote",)


def test_source_rollback_remains_rejected_even_if_both_quotes_are_fresh(args):
    args["current_quote"].update(source_updated_at=fixtures.at(-500),
                                 received_at=fixtures.at(-100))
    assert assess_live_shadow(**args).reasons == ("quote_version_regressed",)


@pytest.mark.parametrize("mutation,reason", [
    ({"market_status": "suspended"}, "inactive_market_status"),
    ({"line": 181.5, "specifiers": "total=181.5"}, "contract_changed"),
    ({"odds": 2.01}, "price_changed"),
])
def test_new_review_cannot_bypass_other_admission_checks(args, mutation, reason):
    args["current_quote"].update(mutation)
    assert assess_live_shadow(**args).reasons == (reason,)
