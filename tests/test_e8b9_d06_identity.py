"""Synthetic ambiguity regressions using the supplied D06 observation schema."""
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from betting_engine import player_model_study as study


CUTS = dict(selection_start="2025-02-01T00:00:00Z",
            test_start="2025-03-01T00:00:00Z", as_of="2025-04-01T00:00:00Z")


def observation(spec=study.METRICS[0], **changes):
    row = dict(league=spec.league, stat_group=spec.group, game_id=spec.league + ":1",
               player_id="player-1", team_id="team-1", identity_namespace=spec.namespace,
               scope="player_full_game", stat_scope=spec.scope,
               start="2025-01-01T12:00:00Z", available_at="2025-01-02T00:00:00Z",
               source_fetched_at="2025-01-02T00:00:00Z",
               game_identity_fetched_at="2025-01-02T00:00:00Z",
               source_published_at=None, played=None,
               source_sha256="a" * 64, game_identity_sha256="b" * 64,
               source_url="https://example.invalid/synthetic-only",
               availability_basis="archived_observation", availability_reference="fixture",
               stats=json.dumps({spec.field: 3}))
    return row | changes


def prepare(rows, specs=None):
    return study._prepare(pa.Table.from_pylist(rows), specs or (study.METRICS[0],),
                          *(study._time(v) for v in CUTS.values()))


@pytest.mark.parametrize("changes,reason", [
    ({"played": False}, "recorded_dnp"),
    ({"stats": "{}"}, "missing_target"),
    ({"stats": '{"points":true}'}, "invalid_target"),
    ({"stats": '{"points":-1}'}, "invalid_count_target"),
    ({"stats": "not-json"}, "invalid_stats"),
])
def test_ineligible_copy_cannot_silently_choose_valid_target(changes, reason):
    rows = [observation(), observation(team_id="different-team", **changes)]
    before = deepcopy(rows)
    groups, audit = prepare(rows)
    assert groups[study.METRICS[0].key] == []
    assert audit[0]["reason"] == "duplicate_player_game"
    assert audit[1]["reason"] == reason
    assert all(a["identity_conflicts"] == ["duplicate_player_game"] for a in audit)
    assert rows == before


@pytest.mark.parametrize("changes", [{"played": False}, {"stats": "{}"}])
def test_ineligible_row_with_later_start_quarantines_entire_event(changes):
    rows = [observation(), observation(player_id="other-player"),
            observation(player_id="third-player", start="2025-03-01T12:00:00Z",
                        available_at="2025-03-02T00:00:00Z", **changes)]
    groups, audit = prepare(rows)
    assert not groups[study.METRICS[0].key]
    assert all(a["disposition"] == "excluded" for a in audit)
    assert all(a["identity_conflicts"] == ["conflicting_game_start"] for a in audit)
    assert all(a["reason"] == "conflicting_game_start" for a in audit[:2])


def test_mlb_event_conflict_is_detected_when_other_group_not_requested():
    hitting, pitching = study.METRICS[3:5]
    rows = [observation(hitting), observation(pitching, player_id="pitcher",
            start="2025-03-01T12:00:00Z", available_at="2025-03-02T00:00:00Z")]
    groups, audit = prepare(rows, (hitting,))
    assert not groups[hitting.key]
    assert audit[0]["reason"] == "conflicting_game_start"
    assert audit[1]["reason"] == "outside_requested_metrics"
    assert audit[1]["identity_conflicts"] == ["conflicting_game_start"]


def test_mlb_same_player_two_stat_groups_are_not_duplicate_targets():
    specs = study.METRICS[3:5]
    groups, audit = prepare([observation(s) for s in specs], specs)
    assert all(len(groups[s.key]) == 1 for s in specs)
    assert all(not a["identity_conflicts"] for a in audit)


def test_duplicate_player_does_not_remove_other_players_from_same_event():
    groups, audit = prepare([observation(), observation(played=False),
                             observation(player_id="unambiguous")])
    assert [r["player"] for r in groups[study.METRICS[0].key]] == ["unambiguous"]
    assert audit[2]["disposition"] == "train"
    assert audit[2]["identity_conflicts"] == []


def test_equivalent_offsets_do_not_create_false_event_conflicts():
    groups, audit = prepare([observation(), observation(player_id="other",
                             start="2025-01-01T13:00:00+01:00", played=False)])
    assert len(groups[study.METRICS[0].key]) == 1
    assert all(not a["identity_conflicts"] for a in audit)


@pytest.mark.parametrize("changes", [
    {"available_at": "2025-05-01T00:00:00Z"},
    {"source_fetched_at": "2025-05-01T00:00:00Z"},
    {"game_identity_fetched_at": "2025-05-01T00:00:00Z"},
    {"source_published_at": "2025-05-01T00:00:00Z"},
    {"identity_namespace": "wrong-provider"},
    {"source_sha256": "invalid"},
    {"stat_scope": "first_half"},
])
def test_unusable_identity_evidence_does_not_erase_earlier_sample(changes):
    groups, audit = prepare([observation(), observation(played=False, **changes)])
    assert len(groups[study.METRICS[0].key]) == 1
    assert audit[0]["disposition"] == "train"
    assert audit[1]["disposition"] == "excluded"


def test_conflict_order_is_deterministic_and_never_resolves_by_row_order():
    rows = [observation(), observation(start="2025-03-01T12:00:00Z",
             available_at="2025-03-02T00:00:00Z", played=False)]
    for ordered in (rows, rows[::-1]):
        groups, audit = prepare(ordered)
        assert not groups[study.METRICS[0].key]
        assert all(a["identity_conflicts"] == ["conflicting_game_start", "duplicate_player_game"]
                   for a in audit)


def test_conflicts_are_archived_before_fit_and_reduce_independent_samples(tmp_path, monkeypatch):
    rows = []
    for month in (1, 2, 3):
        for day in range(1, 11):
            for player in range(10):
                rows.append(observation(game_id=f"NBA:{month}-{day}", player_id=str(player),
                    start=f"2025-{month:02}-{day:02}T12:00:00Z",
                    available_at=f"2025-{month:02}-{day:02}T20:00:00Z"))
    # Every formerly eligible target has a conflicting DNP record. This must
    # reject fitting instead of reporting 100 rows / 10 games in each split.
    rows += [r | {"played": False} for r in rows[:]]
    source = tmp_path / "snapshot"
    source.mkdir()
    pq.write_table(pa.Table.from_pylist(rows), source / "observations.parquet")
    body = (source / "observations.parquet").read_bytes()
    (source / "manifest.json").write_text(json.dumps({"observation_count": len(rows),
        "files": {"observations.parquet": {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}}}))
    monkeypatch.setattr(study, "_fit", lambda *_: pytest.fail("ambiguous targets must not fit"))
    out = tmp_path / "study"
    report = study.run_study(source, out, metric_keys=[study.METRICS[0].key], **CUTS)
    assert report["studies"][0]["state"] == "rejected_insufficient_evidence"
    assert report["exclusion_counts"] == {"duplicate_player_game": 300, "recorded_dnp": 300}
    assert report["identity_conflict_counts"] == {"duplicate_player_game": 600}
    with gzip.open(out / "row-audit.jsonl.gz", "rt") as stream:
        audit = [json.loads(line) for line in stream]
    assert [a["row_index"] for a in audit] == list(range(600))
    assert Counter(a["disposition"] for a in audit) == {"excluded": 600}
    assert all(a["identity_conflicts"] for a in audit)
    assert report["production_props_enabled"] is False
    assert report["historical_availability_authenticated"] is False
    assert report["studies"][0]["validation_gate"]["accepted"] is False
