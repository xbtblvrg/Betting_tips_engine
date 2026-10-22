"""Detached engineering attachments are evidence inventory, never approval."""
import hashlib
import json

import pytest

from betting_engine.requirement_acceptance import engineering_observations


CUTOFF = "2026-10-01T14:00:00+00:00"


def attach(root, documents):
    directory = root / "data/engineering-evidence"
    directory.mkdir(parents=True)
    files = []
    for index, document in enumerate(documents):
        path = directory / f"{index}.json"
        raw = json.dumps(document).encode()
        path.write_bytes(raw)
        files.append({"path": str(path.relative_to(root)), "bytes": len(raw),
                      "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = root / "data/engineering-evidence-manifest.json"
    manifest.write_text(json.dumps({"task_id": "E6BF9ACD30D", "files": files}))
    return files


@pytest.mark.parametrize("gap_fields", [{}, {"gap": {}}, {"gap": {"id": ""}}])
def test_gapless_receipt_is_visible_but_not_an_observed_gap(tmp_path, gap_fields):
    receipt = {"kind": "source_bound_production_receipt", "task_id": "D17",
               "observed_at": "2026-09-29T19:30:03.252501+00:00", **gap_fields}
    future = {"kind": "gap_observed", "observed_at": "2026-10-01T14:07:19+00:00",
              "gap": {"id": "development:D17", "state": "candidate_ready"}}
    files = attach(tmp_path, [receipt, future])
    originals = {item["path"]: (tmp_path / item["path"]).read_bytes() for item in files}
    report = engineering_observations(tmp_path, as_of=CUTOFF)
    inventory = report["attachment_inventory"]
    assert inventory["attached_files"] == 2
    assert inventory["gap_ids"] == ["", "development:D17"]
    assert inventory["observed_by_cutoff_gap_ids"] == []
    assert inventory["outcome_diagnostic_files"] == 0
    assert not inventory["complete_registry"]
    assert not report["resolved_by_evaluator"]
    assert report["observations"][0]["diagnostic"] == receipt
    assert report["observations"][1]["diagnostic"] == future
    assert [row["sha256"] for row in report["observations"]] == [f["sha256"] for f in files]

    # Equality at the cutoff admits the actual finding, never the receipt's
    # task ID or its empty gap marker. No export or copy timestamp is used.
    later = engineering_observations(tmp_path, as_of="2026-10-01T16:07:19+02:00")
    assert later["attachment_inventory"]["gap_ids"] == inventory["gap_ids"]
    assert later["attachment_inventory"]["observed_by_cutoff_gap_ids"] == ["development:D17"]
    assert not later["resolved_by_evaluator"]
    assert originals == {name: (tmp_path / name).read_bytes() for name in originals}


@pytest.mark.parametrize("gap", [None, "malformed", [], {"id": None}, {"id": 17}])
def test_malformed_gap_remains_raw_diagnostic_without_invented_id(tmp_path, gap):
    document = {"observed_at": CUTOFF, "gap": gap}
    attach(tmp_path, [document])
    report = engineering_observations(tmp_path, as_of=CUTOFF)
    inventory = report["attachment_inventory"]
    assert report["observations"][0]["diagnostic"] == document
    assert inventory["attached_files"] == 1
    assert inventory["gap_ids"] == inventory["observed_by_cutoff_gap_ids"] == []
    assert inventory["outcome_diagnostic_files"] == 0
    assert not report["resolved_by_evaluator"]


@pytest.mark.parametrize("observed", [None, "invalid", "2026-10-01T13:00:00"])
def test_missing_observation_cannot_be_supplied_by_export_time(tmp_path, observed):
    document = {"observed_at": observed, "exported_at": CUTOFF,
                "gap": {"id": "development:D17"}}
    attach(tmp_path, [document])
    report = engineering_observations(tmp_path, as_of=CUTOFF)
    assert report["attachment_inventory"]["gap_ids"] == ["development:D17"]
    assert report["attachment_inventory"]["observed_by_cutoff_gap_ids"] == []
    assert report["observations"][0]["temporal_state"] == "observation_time_missing_or_invalid"


def test_gapless_attachments_do_not_inflate_outcome_diagnostic_count(tmp_path):
    attach(tmp_path, [
        {"kind": "source_bound_production_receipt", "observed_at": CUTOFF},
        {"gap": {"id": "outcome:loss"}, "observed_at": CUTOFF},
        {"gap": {"id": "outcome:loss"}, "observed_at": "2026-10-02T14:00:00Z"},
    ])
    report = engineering_observations(tmp_path, as_of=CUTOFF)
    inventory = report["attachment_inventory"]
    assert inventory["attached_files"] == 3
    assert inventory["gap_ids"] == ["", "outcome:loss"]
    assert inventory["outcome_diagnostic_files"] == 2  # Files, not unique gaps.
    assert inventory["observed_by_cutoff_gap_ids"] == ["outcome:loss"]
    assert not report["resolved_by_evaluator"]


def test_gapless_receipt_still_requires_exact_manifest_pin(tmp_path):
    files = attach(tmp_path, [{"kind": "source_bound_production_receipt",
                              "observed_at": CUTOFF}])
    path = tmp_path / files[0]["path"]
    path.write_bytes(path.read_bytes().replace(b"receipt", b"release"))
    with pytest.raises(ValueError, match="engineering evidence hash/size mismatch"):
        engineering_observations(tmp_path, as_of=CUTOFF)
