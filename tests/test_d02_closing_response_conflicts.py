"""Synthetic response-shape conflicts; no live captures or profit claims."""
from copy import deepcopy
import json

import pytest

from betting_engine.captured_price_evaluation import FrozenCaptureReports
from test_captured_price_evaluation import archive, freeze, response
from test_closing_prices import adapted, evaluate, response_fixture, snapshot


def malformed_copy(payload, shape):
    """Hide a same-ID suspended scope conflict in an invalid market container."""
    conflict = deepcopy(payload["groups"][0]["markets"][0][0])
    conflict.update(name="1st Half - Total", status="suspended", outcomes=[])
    group = {"id": "half", "name": "Half", "markets": [[conflict]]}
    if shape == "mapping":
        group["markets"] = conflict
    elif shape == "extra_nesting":
        group["markets"] = [[[conflict]]]
    elif shape == "mixed_bucket":
        group["markets"] = [[conflict, "unreadable market"]]
    else:
        raise AssertionError(shape)
    payload["groups"].append(group)
    return payload


@pytest.mark.parametrize("shape", ["mapping", "extra_nesting", "mixed_bucket"])
@pytest.mark.parametrize("reverse", [False, True])
def test_adapter_rejects_entire_malformed_response_instead_of_losing_conflict(shape, reverse):
    body, source = response_fixture()
    payload = malformed_copy(json.loads(body), shape)
    if reverse:
        payload["groups"].reverse()
    before = deepcopy(payload)
    with pytest.raises(ValueError, match="Invalid market (group schema|array)"):
        adapted(payload, source)
    assert payload == before


@pytest.mark.parametrize("shape", ["mapping", "extra_nesting", "mixed_bucket"])
def test_frozen_capture_retains_bad_response_and_blocks_earlier_quote(tmp_path, shape):
    early = archive(tmp_path, observed="2026-09-29T19:58:00+00:00")
    bad = archive(tmp_path, malformed_copy(response(), shape))
    store = FrozenCaptureReports.create(tmp_path / "study.sqlite3")
    result = freeze(tmp_path, [early, bad], store=store)
    report = result["reports"][0]
    assert report["status"] == "missing_closing_price"
    assert report["reason"] == "invalid_capture_evidence"
    assert report["price_clv"] is None and report["closing_price"] is None
    assert result["coverage"]["invalid_capture_rows"] == 1
    assert report["capture_errors"][0]["capture"] == bad
    assert report["capture_errors"][0]["reason"].startswith("Invalid market ")
    assert json.loads(store.source_body(bad["sha256"])) == malformed_copy(response(), shape)
    assert store.read(result["study_id"]) == result
    assert freeze(tmp_path, [bad, early], store=store)["study_id"] == result["study_id"]


@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("simultaneous", [False, True])
def test_valid_layout_keeps_identity_blocker_until_later_exact_recovery(flat, simultaneous):
    body, source = response_fixture()
    payload = json.loads(body)
    conflict = deepcopy(payload["groups"][0]["markets"][0][0])
    conflict.update(name="1st Half - Total", status="suspended")
    conflict.pop("outcomes")
    payload["groups"][0]["markets"] = (
        [[payload["groups"][0]["markets"][0][0], conflict]] if simultaneous else [[conflict]])
    if flat:
        payload["groups"][0]["markets"] = payload["groups"][0]["markets"][0]
    conflict_rows = adapted(payload, source)
    rows = [snapshot(at="2026-09-29T19:58:00Z"), *conflict_rows]
    for order in (rows, rows[::-1]):
        result = evaluate(order)
        assert result["status"] == "missing_closing_price"
        assert result["reason"] == (
            "conflicting_latest_snapshots" if simultaneous else "latest_market_or_scope_mismatch")
        assert result["price_clv"] is None
        recovered_body, recovered_source = response_fixture(snapshot(2.5, "2026-09-29T19:59:30Z"))
        recovery = adapted(json.loads(recovered_body), recovered_source)
        recovered = evaluate([*order, *recovery])
        assert recovered["status"] == "comparable"
        assert recovered["price_clv"] == pytest.approx(-.16)
        assert recovered["closing_price"]["source_sha256"] == recovered_source["sha256"]


@pytest.mark.parametrize("flat", [False, True])
def test_valid_unchanged_copy_and_missing_close_remain_distinct(flat):
    body, source = response_fixture(snapshot(2.1))
    payload = json.loads(body)
    if flat:
        payload["groups"][0]["markets"] = payload["groups"][0]["markets"][0]
    payload["groups"].append(deepcopy(payload["groups"][0]))
    rows = adapted(payload, source)
    assert evaluate(rows)["price_clv"] == 0
    assert evaluate([])["price_clv"] is None
    assert evaluate([])["status"] == "missing_closing_price"
