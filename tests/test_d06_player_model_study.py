"""Synthetic evidence only, plus a separately documented real archive study."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from scipy import stats

from betting_engine import player_model_study as study


CUTS = dict(selection_start="2025-01-15T00:00:00Z", test_start="2025-02-15T00:00:00Z",
            as_of="2025-03-15T00:00:00Z")


def rows_for(spec=study.METRICS[0]):
    rows = []
    for month in (1, 2, 3):
        for day in range(1, 13):
            for player in range(10):
                rows.append(dict(league=spec.league, stat_group=spec.group,
                                 game_id=f"{spec.league}:{month}-{day}", player_id=str(player),
                                 team_id="team", identity_namespace=spec.namespace,
                                 scope="player_full_game", stat_scope=spec.scope,
                                 start=f"2025-{month:02}-{day:02}T12:00:00Z",
                                 available_at=f"2025-{month:02}-{day:02}T20:00:00Z",
                                 game_identity_fetched_at=f"2025-{month:02}-{day:02}T20:00:00Z",
                                 source_fetched_at=f"2025-{month:02}-{day:02}T20:00:00Z",
                                 source_published_at=None, played=None,
                                 source_url="https://example.invalid/synthetic", source_sha256="a" * 64,
                                 game_identity_sha256="b" * 64, availability_basis="archived_observation",
                                 availability_reference="synthetic-fixture-only",
                                 stats=json.dumps({spec.field: player * 3 + day % 3 - (10 if spec.kind == "continuous" else 0)})))
    return rows


def snapshot(tmp_path, rows):
    root = tmp_path / "snapshot"
    root.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), root / "observations.parquet")
    body = (root / "observations.parquet").read_bytes()
    (root / "manifest.json").write_text(json.dumps({
        "observation_count": len(rows), "files": {"observations.parquet": {
            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}}}))
    return root


def run(tmp_path, rows=None, spec=study.METRICS[0]):
    source = snapshot(tmp_path, rows if rows is not None else rows_for(spec))
    output = tmp_path / "study"
    report = study.run_study(source, output, metric_keys=[spec.key], **CUTS)
    return report, output


def audit(output):
    with gzip.open(output / "row-audit.jsonl.gz", "rt") as stream:
        return [json.loads(line) for line in stream]


def test_archive_precedes_every_fit_and_choice_precedes_test(tmp_path, monkeypatch):
    fit, score = study._fit, study._score
    phases = []

    def checked_fit(rows, recipe):
        output = tmp_path / "study"
        assert (output / "plan.json").exists()
        assert (output / "inputs/observations.parquet").exists()
        assert (output / "inputs/player_model_study.py").read_bytes() == Path(study.__file__).read_bytes()
        assert len(audit(output)) == 360
        assert all(r["split"] != "test" for r in rows)
        phases.append(recipe)
        return fit(rows, recipe)

    def checked_score(model, rows):
        if rows[0]["split"] == "test":
            assert (tmp_path / "study/NBA_player_points/selection-lock.json").exists()
        return score(model, rows)

    monkeypatch.setattr(study, "_fit", checked_fit)
    monkeypatch.setattr(study, "_score", checked_score)
    report, output = run(tmp_path)
    result = report["studies"][0]
    assert len(phases) >= 4
    assert result["state"] == "retrospective_comparison_completed"
    assert result["split_counts"]["train"]["rows"] == 120
    assert not result["validation_gate"]["accepted"]
    assert not report["production_props_enabled"]
    assert report["current_participation"] == "unknown"
    assert not report["odds_used"]
    checksums = json.loads((output / "artifacts.json").read_text())
    assert all(hashlib.sha256((output / name).read_bytes()).hexdigest() == h for name, h in checksums.items())


def test_test_outcome_changes_cannot_change_selection_or_fitted_models(tmp_path):
    original = rows_for()
    modified = deepcopy(original)
    for row in modified[240:]:
        row["stats"] = json.dumps({"points": 250})
    first, a = run(tmp_path / "a", original)
    second, b = run(tmp_path / "b", modified)
    x, y = first["studies"][0], second["studies"][0]
    assert x["selection"] == y["selection"]
    assert x["selected_recipe"] == y["selected_recipe"]
    assert x["test"] != y["test"]
    for path in (a / "NBA_player_points").glob("*-model.json"):
        assert path.read_bytes() == (b / "NBA_player_points" / path.name).read_bytes()


def test_market_odds_and_postgame_exposure_are_not_features(tmp_path):
    original = rows_for()
    changed = deepcopy(original)
    for row in changed:
        row.update(odds=999, closing_odds=.1, implied_probability=.99, minutes=200, starter=True)
        row["stats"] = json.dumps(json.loads(row["stats"]) | {"odds": 900, "market_rating": 9999})
    a, _ = run(tmp_path / "a", original)
    b, _ = run(tmp_path / "b", changed)
    assert a["studies"] == b["studies"]
    with pytest.raises(ValueError, match="allowlisted"):
        study.run_study(tmp_path / "a/snapshot", tmp_path / "blocked", metric_keys=["NBA/player/odds"], **CUTS)


@pytest.mark.parametrize("spec", study.METRICS, ids=lambda s: s.key)
def test_all_six_real_schema_metrics_have_temporal_baselines(tmp_path, spec):
    report, _ = run(tmp_path, spec=spec)
    result = report["studies"][0]
    assert result["state"] == "retrospective_comparison_completed"
    expected = {"pooled_poisson", "player_poisson", "player_nb"} if spec.kind == "count" else {"pooled_normal", "player_normal", "player_laplace"}
    assert set(result["selection"]) == expected
    assert len(result["test"]) <= 2
    assert result["timestamp_limitations"]["publication_time_unknown"] == 360


@pytest.mark.parametrize("change,reason", [
    ({"stats": '{"points":null}'}, "missing_target"),
    ({"stats": '{"points":true}'}, "invalid_target"),
    ({"stats": '{"points":NaN}'}, "invalid_target"),
    ({"stats": '{"points":1,"points":2}'}, "invalid_stats"),
    ({"stats": '{"points":-1}'}, "invalid_count_target"),
    ({"stats": '{"points":1.5}'}, "invalid_count_target"),
    ({"played": False}, "recorded_dnp"),
    ({"player_id": ""}, "invalid_player_id"),
    ({"identity_namespace": "guessed-name"}, "identity_namespace_mismatch"),
    ({"stat_scope": "first_half"}, "scope_mismatch"),
    ({"available_at": "2025-04-01T00:00Z"}, "available_at_after_as_of"),
    ({"available_at": "2025-01-01T12:00Z"}, "availability_not_after_start"),
    ({"start": "2025-01-01"}, "invalid_start"),
    ({"source_sha256": "invented"}, "invalid_source_sha256"),
])
def test_each_excluded_row_is_accounted_for(tmp_path, change, reason):
    rows = rows_for()
    rows[0].update(change)
    report, out = run(tmp_path, rows)
    assert audit(out)[0]["reason"] == reason
    assert report["input_rows"] == sum(report["row_dispositions"].values())
    assert report["exclusion_counts"][reason] == 1


def test_duplicate_transfers_and_conflicting_start_do_not_cross_splits(tmp_path):
    rows = rows_for()
    rows.append(rows[0] | {"team_id": "new-team"})
    rows[10]["start"] = "2025-03-01T12:00:00Z"
    rows[10]["available_at"] = "2025-03-01T20:00:00Z"
    _, out = run(tmp_path, rows)
    entries = audit(out)
    assert entries[0]["reason"] == entries[-1]["reason"] == "duplicate_player_game"
    assert all(r["reason"] == "conflicting_game_start" for r in entries[10:20])


def test_late_and_missing_timestamps_never_become_historical_evidence(tmp_path):
    rows = rows_for()
    rows[0].update(available_at=None, availability_reference=None)
    rows[1]["available_at"] = "2025-03-14T00:00Z"
    report, out = run(tmp_path, rows)
    assert "missing_availability" in audit(out)[0]["timestamp_limitations"]
    assert "observation_not_available_before_fit_cutoff" in audit(out)[1]["timestamp_limitations"]
    assert report["studies"][0]["validation_gate"]["accepted"] is False
    assert audit(out)[0]["available_at"] is None


def test_insufficient_events_reject_without_fit(tmp_path, monkeypatch):
    rows = rows_for()
    for row in rows:
        month = row["start"][5:7]
        row["game_id"] = "NBA:" + month
        row["start"] = f"2025-{month}-01T12:00Z"
        row["player_id"] = row["player_id"] + row["available_at"]
    monkeypatch.setattr(study, "_fit", lambda *_: pytest.fail("insufficient event evidence must not fit"))
    result, _ = run(tmp_path, rows)
    assert result["studies"][0]["state"] == "rejected_insufficient_evidence"


def test_zero_counts_signed_yards_and_likelihood_formulas():
    rows = [dict(y=0., player="p", team="t", game="g", index=0)]
    model = study._fit(rows, "player_nb")
    scores, predictions = study._score(model, rows)
    assert model["alpha"] == 0
    assert scores["row_nll"] == pytest.approx(1e-9)
    assert predictions[0]["actual"] == 0
    continuous = [dict(y=y, player="p", team="t", game=f"g{i}", index=i) for i, y in enumerate((-4., 2., 8.))]
    model = study._fit(continuous, "player_normal")
    scores, _ = study._score(model, continuous)
    assert model["global_mean"] == 2
    assert scores["row_nll"] == pytest.approx(float(-stats.norm.logpdf([-4, 2, 8], 2, np.sqrt(24)).mean()))


def test_snapshot_hash_and_row_count_and_existing_run_fail_closed(tmp_path):
    _, output = run(tmp_path)
    with pytest.raises(FileExistsError):
        study.run_study(tmp_path / "snapshot", output, **CUTS)
    path = tmp_path / "snapshot/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["observation_count"] += 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="row_count"):
        study.run_study(path.parent, tmp_path / "bad-count", **CUTS)
    manifest["files"]["observations.parquet"]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="hash_or_size"):
        study.run_study(path.parent, tmp_path / "bad-hash", **CUTS)


def test_boundary_is_in_later_split_and_offsets_are_normalized(tmp_path):
    rows = rows_for()
    rows[0].update(start="2025-01-15T01:00:00+01:00", game_id="NBA:boundary", available_at="2025-01-15T20:00Z")
    _, out = run(tmp_path, rows)
    assert audit(out)[0]["disposition"] == "selection"
    with pytest.raises(ValueError, match="selection_start"):
        study.run_study(tmp_path / "snapshot", tmp_path / "invalid", **(CUTS | {"test_start": CUTS["selection_start"]}))


def test_event_balanced_score_does_not_treat_many_players_as_many_games():
    rows = [dict(y=0., player=str(i), team="t", game="same-game", index=i) for i in range(9)]
    rows.append(dict(y=4., player="last", team="t", game="other-game", index=9))
    model = dict(recipe="pooled_poisson", player_team_means={}, global_mean=1.)
    result, _ = study._score(model, rows)
    expected = (-stats.poisson.logpmf(0, 1) - stats.poisson.logpmf(4, 1)) / 2
    assert result["game_balanced_nll"] == pytest.approx(expected)
    assert result["row_nll"] != pytest.approx(expected)
    assert result["games"] == 2


def test_new_team_uses_frozen_pooled_prior():
    rows = [dict(y=y, player=p, team="old", game=str(i), index=i)
            for i, (y, p) in enumerate(((2., "low"), (18., "high")))]
    model = study._fit(rows, "player_poisson")
    target = dict(y=5., player="high", team="new", game="next", index=2)
    _, predictions = study._score(model, [target])
    assert predictions[0]["mean"] == 10.
    assert model["training_rows"] == 2


def test_empty_and_unrequested_ncaa_evidence_stays_rejected(tmp_path):
    for label, rows in (("empty", []), ("ncaa", [rows_for()[0] | {"league": "NCAAM"}])):
        report, output = run(tmp_path / label, rows)
        assert report["studies"][0]["state"] == "rejected_insufficient_evidence"
        assert not report["production_props_enabled"]
        assert len(audit(output)) == len(rows)
        if rows:
            assert report["exclusion_counts"] == {"outside_requested_metrics": 1}


def test_future_revision_cannot_erase_earlier_fit(tmp_path):
    rows = rows_for()
    a, _ = run(tmp_path / "a", rows)
    rows.append(rows[0] | {"available_at": "2025-04-01T00:00Z", "stats": '{"points":999}'})
    b, out = run(tmp_path / "b", rows)
    assert a["studies"] == b["studies"]
    assert audit(out)[-1]["reason"] == "available_at_after_as_of"


def test_corrupt_declared_upstream_evidence_prevents_any_fit(tmp_path, monkeypatch):
    source = snapshot(tmp_path, rows_for())
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["quarantine.json"] = dict(bytes=2, sha256="0" * 64)
    manifest_path.write_text(json.dumps(manifest))
    (source / "quarantine.json").write_text("{}")
    monkeypatch.setattr(study, "_fit", lambda *_: pytest.fail("corrupt evidence must not fit"))
    with pytest.raises(ValueError, match="evidence_hash_or_size"):
        study.run_study(source, tmp_path / "out", **CUTS)


def test_nonfinite_scoring_rejects_without_test_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "_score", lambda *_: (_ for _ in ()).throw(ValueError("nonfinite_model_score")))
    report, _ = run(tmp_path)
    result = report["studies"][0]
    assert result["state"] == "rejected_numerical_evidence"
    assert "nonfinite_model_score" in result["validation_gate"]["reasons"]
    assert "test" not in result
