"""Internal evidence fixtures, not live provider responses or availability claims."""

from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
import json

import pytest

from betting_engine.participation import (
    Participation as P, Role, Uncertainty as U, ParticipationIdentity,
    SourceDocument, decode_document, validate_participation,
)


IDENTITY = ParticipationIdentity("NBA", "NBA:target", "p1", "t1", "player", "fixture-ids",
                                 "2026-09-29T18:00:00Z")
AS_OF = "2026-09-29T17:00:00Z"


def document(identity=IDENTITY, *, metadata=None, **changes):
    row = dict(schema="participation-evidence-v1", evidence_kind="current_event_report",
               **asdict(identity), valid_until="2026-09-29T17:10:00Z",
               source_published_at="2026-09-29T16:55:00Z", participation="available",
               role="starter", uncertainty="confirmed")
    row["start"] = identity.start.isoformat()
    row.update(changes)
    raw = json.dumps(row).encode()
    return SourceDocument(**(dict(body=raw, source_sha256=sha256(raw).hexdigest(),
        source_url="https://example.invalid/source-a", observed_at="2026-09-29T16:56:00Z",
        archive_reference="fixture:archive-a", verification="fixture") | (metadata or {})))


def assess(documents=(), identity=IDENTITY, **changes):
    return validate_participation(identity, documents, **(dict(as_of=AS_OF, max_age=timedelta(minutes=15)) | changes))


@pytest.mark.parametrize("league,group,role", [
    ("NBA", "player", "starter"), ("WNBA", "player", "bench"),
    ("NFL", "player", "starter"), ("NHL", "player", "starting_goalie"),
    ("MLB", "pitching", "starting_pitcher"), ("MLB", "hitting", "batting_lineup"),
    ("NCAAM", "player", "starter"), ("NCAAW", "player", "bench"),
    ("NCAAF", "player", "starter"),
])
def test_exact_family_evidence(league, group, role):
    identity = replace(IDENTITY, league=league, game_id=league + ":target", stat_group=group)
    source = document(identity, role=role)
    result = assess([source], identity)
    assert result.participation == P.AVAILABLE and result.role.value == role
    assert result.uncertainty == U.CONFIRMED
    assert result.valid_until.isoformat() == "2026-09-29T17:10:00+00:00"
    assert result.reviews[0].evidence.source_sha256 == sha256(source.body).hexdigest()
    assert result.reviews[0].evidence.verification == "fixture"
    assert not result.production_supported


def test_absence_dnp_out_and_unknown_role_are_distinct():
    assert assess().participation == P.UNKNOWN
    for status in ("dnp", "out", "available", "questionable", "doubtful", "unknown"):
        result = assess([document(participation=status, role="unknown")])
        assert result.participation.value == status
        assert result.role == Role.UNKNOWN
    assert assess([document(role="unknown")]).reasons == ("role_unknown",)


@pytest.mark.parametrize("field,value", [
    ("league", "WNBA"), ("game_id", "NBA:other"), ("player_id", "same-name-other-id"),
    ("team_id", "old-team"), ("stat_group", "pitching"),
    ("identity_namespace", "other-provider"), ("start", "2026-09-29T18:01:00Z"),
])
def test_identity_conflicts_are_rejected_and_retained(field, value):
    changes = {field: value}
    if field == "league":
        changes["game_id"] = "WNBA:target"
    result = assess([document(**changes)])
    assert result.participation == P.UNKNOWN and result.role == Role.UNKNOWN
    assert field + "_mismatch" in result.reviews[0].reasons
    assert getattr(result.reviews[0].evidence.identity, field) != getattr(IDENTITY, field)


@pytest.mark.parametrize("changes,metadata,reason", [
    ({"valid_until": AS_OF}, {}, "stale_evidence"),
    ({}, {"observed_at": "2026-09-29T17:00:01Z"}, "not_observed_as_of"),
    ({"valid_until": "2026-09-29T16:56:00Z"}, {}, "invalid_validity_interval"),
    ({"source_published_at": "2026-09-29T16:57:00Z"}, {}, "publication_after_observation"),
    ({"source_published_at": "2026-09-29T16:45:00Z"}, {}, "stale_publication"),
    ({"source_published_at": None}, {"observed_at": "2026-09-29T16:45:00Z"}, "stale_evidence"),
])
def test_timestamp_conflicts_fail_closed(changes, metadata, reason):
    result = assess([document(metadata=metadata, **changes)])
    assert result.participation == P.UNKNOWN and result.role == Role.UNKNOWN
    assert reason in result.reviews[0].reasons


def test_no_publication_time_is_invented_and_offsets_match():
    result = assess([document(source_published_at=None, start="2026-09-29T20:00:00+02:00")])
    assert result.participation == P.AVAILABLE
    assert result.reviews[0].evidence.source_published_at is None
    assert result.valid_until.isoformat() == "2026-09-29T17:10:00+00:00"
    assert assess([document()], as_of=IDENTITY.start).role == Role.UNKNOWN


@pytest.mark.parametrize("changes,field", [
    ({"participation": "out", "role": "unknown"}, "participation"),
    ({"role": "bench"}, "role"),
    ({"participation": "questionable", "uncertainty": "reported"}, "participation"),
])
def test_multisource_conflicts_visible_in_both_orders(changes, field):
    a = document()
    b = document(metadata={"source_url": "https://example.invalid/source-b"}, **changes)
    for sources in ([a, b], [b, a]):
        result = assess(sources)
        assert result.participation == P.UNKNOWN and result.role == Role.UNKNOWN
        assert result.uncertainty == U.CONFLICT and field in result.conflicts
        assert len(result.reviews) == 2 and all(not r.reasons for r in result.reviews)


def test_same_source_revision_not_silently_selected_and_duplicates_do_not_vote():
    a, b = document(), document(role="bench")
    assert assess([a, a, b]).conflicts == ("role",)
    assert assess([a, a]).uncertainty == U.CONFIRMED
    assert len(assess([a, a]).reviews) == 2


def test_cross_source_nonparticipation_and_role_conflict():
    result = assess([document(participation="out", role="unknown"), document(participation="unknown")])
    assert "nonparticipation_role" in result.conflicts
    assert result.role == Role.UNKNOWN


def test_stale_role_never_fills_new_unknown_role_and_future_revision_cannot_erase_current():
    current = document(role="unknown")
    stale = document(valid_until=AS_OF)
    future = document(role="bench", metadata={"observed_at": "2026-09-29T17:01:00Z"})
    result = assess([current, stale, future])
    assert result.participation == P.AVAILABLE and result.role == Role.UNKNOWN
    assert not result.conflicts


def test_uncertainty_never_becomes_confirmed_or_numeric_probability():
    assert assess([document(uncertainty="unknown")]).role == Role.UNKNOWN
    result = assess([document(uncertainty="reported", participation="questionable")])
    assert result.uncertainty == U.REPORTED and result.participation == P.QUESTIONABLE


@pytest.mark.parametrize("source", [
    document(metadata={"source_sha256": "0" * 64}),
    document(metadata={"observed_at": "2026-09-29T16:56:00"}),
    document(metadata={"archive_reference": ""}),
    document(metadata={"source_url": 123}),
    document(metadata={"source_url": "http://example.invalid"}),
    document(metadata={"verification": "live_verified"}),
    document(valid_until=None), document(source_published_at="not-a-time"),
    document(participation=True), document(role="probably_starter"),
    document(evidence_kind="historical_boxscore"), document(uncertainty="conflict"),
])
def test_malformed_evidence_is_a_diagnostic(source):
    result = assess([source])
    assert result.participation == P.UNKNOWN
    assert result.reviews[0].reasons[0] == "invalid_evidence"


def test_duplicate_json_fields_cannot_change_a_claim():
    raw = document().body[:-1] + b', "role": "bench"}'
    source = replace(document(), body=raw, source_sha256=sha256(raw).hexdigest())
    assert "duplicate JSON field" in assess([source]).reviews[0].reasons


def test_hash_bound_claim_cannot_be_changed_and_validity_requires_explicit_field():
    source = document()
    tampered = replace(source, body=source.body.replace(b"starter", b"bench"))
    assert "source_hash_mismatch" in assess([tampered]).reviews[0].reasons
    row = json.loads(source.body)
    del row["valid_until"]
    raw = json.dumps(row).encode()
    assert assess([replace(source, body=raw, source_sha256=sha256(raw).hexdigest())]).role == Role.UNKNOWN


def test_dynamic_ncaa_is_retained_as_unsupported_and_roles_do_not_cross_families():
    unknown = replace(IDENTITY, league="NCAA_VOLLEYBALL", game_id="NCAA_VOLLEYBALL:g")
    result = assess([document(unknown)], unknown)
    assert result.identity.league == "NCAA_VOLLEYBALL"
    assert "unsupported_model_family" in result.reasons
    assert result.participation == P.UNKNOWN
    assert "role_not_valid_for_family" in assess([document(role="starting_goalie")]).reviews[0].reasons


@pytest.mark.parametrize("played", [True, False, None])
def test_actual_historical_schema_never_establishes_current_participation(played):
    # Same nullable fields and JSON stats encoding as supplied NFL-2026.parquet.
    row = dict(league="NFL", game_id="NFL:2026_01_ATL_PIT", start="2026-09-13T17:00:00Z",
               player_id="00-0023459", team_id="PIT", stat_group="player", played=played,
               starter=None, minutes=None, stats='{"rushing_yards": -3.0}',
               source_url="https://example.invalid/historical", source_sha256="a" * 64,
               source_fetched_at=AS_OF)
    before = dict(row)
    result = assess([row])
    assert result.participation == P.UNKNOWN and result.role == Role.UNKNOWN
    assert result.reviews[0].historical_played is played
    assert result.reviews[0].reasons == ("historical_log_not_current_evidence",)
    assert row == before


def test_invalid_query_and_nonparticipation_role():
    with pytest.raises(ValueError):
        assess(max_age=timedelta(0))
    with pytest.raises(ValueError):
        assess(as_of="2026-09-29T17:00:00")
    with pytest.raises(ValueError):
        replace(IDENTITY, player_id="")
    assert "nonparticipation_role_conflict" in assess([document(participation="dnp")]).reviews[0].reasons
    assert decode_document(document()).observed_at.tzinfo is not None
