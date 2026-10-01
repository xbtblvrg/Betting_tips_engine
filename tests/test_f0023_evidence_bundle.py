"""F0023 staging omission recovery; no production filesystem or service access."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from betting_engine.player_model_study import METRICS
from betting_engine.player_prop_evidence_bundle import (
    audit_count_evidence_stage, build_count_evidence_bundle, verify_count_evidence_bundle,
)
from betting_engine.player_prop_probability import load_count_forecast, evaluate_count_payoff


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/F0023_COUNT_EVIDENCE_BUNDLE.json"
PIN = "02c1ac891e4f53a4f4273bd9c2d9ac97acbd3a39e81fb37cba3601bd6d10c907"


def verify(body):
    return verify_count_evidence_bundle(body, expected_index_sha256=PIN)


def stage(tmp_path):
    root = tmp_path / "isolated-study"
    files = verify(BUNDLE.read_bytes())
    # Explicitly assemble only into pytest's new directory, never a live path.
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return root, files


def test_empty_stage_reports_every_exact_missing_reference_without_writing(tmp_path):
    root = tmp_path / "missing"
    report = audit_count_evidence_stage(root, BUNDLE.read_bytes(), expected_index_sha256=PIN)
    assert not root.exists()
    assert report["state"] == "incomplete"
    assert report["required_files"] == len(report["failures"]) == 13
    assert report["verified_files"] == 0
    assert {x["reason"] for x in report["failures"]} == {"missing"}
    assert "artifacts.json" in {x["path"] for x in report["failures"]}
    assert not report["production_props_enabled"] and not report["release_authorized"]


def test_clean_stage_replays_all_original_count_reader_dependencies(tmp_path):
    root, files = stage(tmp_path)
    before = deepcopy(files)
    for spec in METRICS:
        if spec.kind != "count":
            continue
        folder = spec.key.replace("/", "_")
        lock = json.loads(files[folder + "/selection-lock.json"])
        model = json.loads(files[folder + "/" + lock["selected_recipe"] + "-test-model.json"])
        player_key = next(iter(model["player_team_means"]))
        player, team = json.loads(player_key)
        forecast = load_count_forecast(root, artifact_index_sha256=PIN,
                                       metric_key=spec.key, player_id=player, team_id=team)
        assert forecast.mean == max(model["player_team_means"][player_key], 1e-9)
        assert forecast.recipe == lock["selected_recipe"]
        assert "historical_availability_not_authenticated" in forecast.validation_reasons
        result = evaluate_count_payoff(forecast, operator="over", line="2")
        assert not result["publishable"] and not result["production_props_enabled"]
    for metric, reason in [("NFL/player/rushing_yards", "discrete_distribution_required"),
                           ("NCAAM/player/points", "unsupported_metric")]:
        with pytest.raises(ValueError, match=reason):
            load_count_forecast(root, artifact_index_sha256=PIN, metric_key=metric,
                                player_id="unverified", team_id="unverified")
    report = audit_count_evidence_stage(root, BUNDLE.read_bytes(), expected_index_sha256=PIN)
    assert report["state"] == "reader_dependencies_verified" and not report["failures"]
    assert not report["training_corpus_included"]
    assert {name: (root / name).read_bytes() for name in before} == before
    assert build_count_evidence_bundle(root, expected_index_sha256=PIN) == BUNDLE.read_bytes()


def test_stage_corruption_and_unsafe_links_are_distinct_from_absence(tmp_path):
    root, _ = stage(tmp_path)
    (root / "plan.json").write_text("{}")
    (root / "report.json").unlink()
    lock = root / "NBA_player_points/selection-lock.json"
    lock.unlink()
    lock.symlink_to(root / "plan.json")
    result = audit_count_evidence_stage(root, BUNDLE.read_bytes(), expected_index_sha256=PIN)
    assert {x["path"]: x["reason"] for x in result["failures"]} == {
        "plan.json": "hash_mismatch", "report.json": "missing",
        "NBA_player_points/selection-lock.json": "unreadable_or_unsafe"}
    assert result["verified_files"] == 10


@pytest.mark.parametrize("name", list(verify(BUNDLE.read_bytes())))
def test_every_bundled_dependency_is_authenticated(name):
    bundle = json.loads(BUNDLE.read_bytes())
    bundle["files"][name] += " "
    with pytest.raises(ValueError, match="hash_mismatch"):
        verify(json.dumps(bundle).encode())


@pytest.mark.parametrize("mutation", ["missing", "extra", "traversal", "bad_version", "self_pin", "bad_type", "duplicate"])
def test_malformed_bundle_cannot_bypass_the_external_pin_or_membership(mutation):
    bundle = json.loads(BUNDLE.read_bytes())
    if mutation == "missing":
        del bundle["files"]["plan.json"]
    elif mutation == "extra":
        bundle["files"]["extra.json"] = "{}"
    elif mutation == "traversal":
        bundle["files"]["../plan.json"] = bundle["files"].pop("plan.json")
    elif mutation == "bad_version":
        bundle["version"] = "approved"
    elif mutation == "self_pin":
        bundle["artifact_index_sha256"] = "a" * 64
    elif mutation == "bad_type":
        bundle["files"]["plan.json"] = {}
    body = json.dumps(bundle).encode()
    if mutation == "duplicate":
        body = b'{"version":"duplicate",' + body[1:]
    with pytest.raises(ValueError):
        verify(body)


def test_builder_does_not_synthesize_missing_or_corrupt_dependencies(tmp_path):
    root, _ = stage(tmp_path)
    path = root / "WNBA_player_points/player_nb-test-model.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        build_count_evidence_bundle(root, expected_index_sha256=PIN)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        build_count_evidence_bundle(root, expected_index_sha256=PIN)
    assert not path.exists()


def test_wrong_external_pin_is_rejected():
    with pytest.raises(ValueError):
        verify_count_evidence_bundle(BUNDLE.read_bytes(), expected_index_sha256="b" * 64)
