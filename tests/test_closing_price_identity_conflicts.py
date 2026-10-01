"""Synthetic exact-market conflicts; no live source verification is claimed."""
import copy
import json

import pytest

from test_closing_prices import adapted, evaluate, response_fixture, snapshot, synchronize_raw


@pytest.mark.parametrize("title", ["Total", "1st Half - Total"])
@pytest.mark.parametrize("status", ["active", "suspended"])
@pytest.mark.parametrize("same_tab", [False, True])
def test_hash_verified_simultaneous_scope_conflict_blocks_both_input_orders(title, status, same_tab):
    body, source = response_fixture()
    payload = json.loads(body)
    other = copy.deepcopy(payload["groups"][0])
    other["id"] = "other-tab"
    other["markets"][0][0].update(name=title, status=status)
    if status == "suspended":
        other["markets"][0][0].pop("outcomes")
    if same_tab:
        payload["groups"][0]["markets"][0].append(other["markets"][0][0])
    else:
        payload["groups"].append(other)

    before = copy.deepcopy(payload)
    rows = adapted(payload, source)
    assert {row["scope"] for row in rows if "scope" in row} == {
        "full_game", "regulation" if title == "Total" else "half_1"}
    original_hash = None
    for captures in (rows, rows[::-1]):
        result = evaluate(captures)
        assert result["status"] == "missing_closing_price"
        assert result["reason"] == "conflicting_latest_snapshots"
        assert result["rejection_counts"]["market_or_scope_mismatch"] == 1
        assert result["price_clv"] is None and result["closing_price"] is None
        if original_hash is not None:
            assert result["comparison_sha256"] == original_hash
        original_hash = result["comparison_sha256"]
    assert payload == before


@pytest.mark.parametrize("status", ["active", "suspended"])
@pytest.mark.parametrize("outcomes", [[], None, "omitted"])
def test_hash_verified_later_scope_conflict_blocks_fallback_until_exact_recovery(status, outcomes):
    body, source = response_fixture()
    payload = json.loads(body)
    market = payload["groups"][0]["markets"][0][0]
    market.update(name="Total", status=status)
    if outcomes == "omitted":
        market.pop("outcomes")
    else:
        market["outcomes"] = outcomes
    conflict = adapted(payload, source)
    early = snapshot(at="2026-09-29T19:58:00Z")
    for captures in ([early, *conflict], [*conflict, early]):
        result = evaluate(captures)
        assert result["status"] == "missing_closing_price"
        assert result["reason"] == "latest_market_or_scope_mismatch"
        assert result["price_clv"] is None and result["closing_price"] is None
        recovered_body, recovered_source = response_fixture(snapshot(2.5, "2026-09-29T19:59:30Z"))
        recovery = adapted(json.loads(recovered_body), recovered_source)
        recovered = evaluate([*recovery, *captures])
        assert recovered["status"] == "comparable"
        assert recovered["price_clv"] == pytest.approx(-.16)
        assert recovered["closing_price"]["source_sha256"] == recovered_source["sha256"]


@pytest.mark.parametrize("change", [
    {"market": "Total"}, {"scope": "regulation"},
    {"market": "Total", "scope": "regulation"},
])
def test_direct_rows_retain_title_and_scope_conflicts_independently(change):
    conflict = snapshot()
    conflict.update(change)
    synchronize_raw(conflict)
    early = snapshot(at="2026-09-29T19:58:00Z")
    assert evaluate([early, conflict])["reason"] == "latest_market_or_scope_mismatch"
    assert evaluate([snapshot(), conflict])["reason"] == "conflicting_latest_snapshots"


@pytest.mark.parametrize("ineligible", ["same_time", "suspended", "missing_lifecycle", "post_start", "other_market"])
def test_identity_conflict_requires_strictly_later_eligible_exact_capture(ineligible):
    early = snapshot(at="2026-09-29T19:58:00Z")
    conflict = synchronize_raw({**snapshot(), "market": "Total", "scope": "regulation"})
    recovery = snapshot(at="2026-09-29T19:59:30Z")
    if ineligible == "same_time":
        recovery["source"]["fetched_at"] = conflict["source"]["fetched_at"]
    elif ineligible == "suspended":
        recovery["status"] = "suspended"
        synchronize_raw(recovery)
    elif ineligible == "missing_lifecycle":
        del recovery["raw_fixture"]
    elif ineligible == "post_start":
        recovery["source"]["fetched_at"] = "2026-09-29T20:00:00Z"
    else:
        recovery["market_id"] = "another-market"
        synchronize_raw(recovery)
    result = evaluate([early, conflict, recovery])
    assert result["status"] == "missing_closing_price"
    assert result["price_clv"] is None and result["closing_price"] is None


def test_unrelated_regulation_market_does_not_block_exact_market():
    other = snapshot()
    other.update(market_id="another-market", market="Total", scope="regulation", status="suspended")
    result = evaluate([snapshot(at="2026-09-29T19:58:00Z"), synchronize_raw(other)])
    assert result["status"] == "comparable"
    assert result["price_clv"] == pytest.approx(.05)
