"""Experimental current participation evidence checks; no I/O or production caller.

The JSON contract is an internal evidence interchange, not a verified vendor API.
Archive timestamps and identity mappings must be authenticated by its caller.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
from urllib.parse import urlsplit


class Participation(str, Enum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    OUT = "out"
    DNP = "dnp"
    QUESTIONABLE = "questionable"
    DOUBTFUL = "doubtful"


class Role(str, Enum):
    UNKNOWN = "unknown"
    STARTER = "starter"
    BENCH = "bench"
    STARTING_PITCHER = "starting_pitcher"
    RELIEF_PITCHER = "relief_pitcher"
    BATTING_LINEUP = "batting_lineup"
    STARTING_GOALIE = "starting_goalie"
    BACKUP_GOALIE = "backup_goalie"
    SKATER = "skater"


class Uncertainty(str, Enum):
    UNKNOWN = "unknown"
    REPORTED = "reported"
    CONFIRMED = "confirmed"
    CONFLICT = "conflict"


# College families remain distinct identities despite sharing role vocabulary.
_ROLES = {
    (league, "player"): frozenset((Role.UNKNOWN, Role.STARTER, Role.BENCH))
    for league in ("NBA", "WNBA", "NFL", "NCAAM", "NCAAW", "NCAAF")
}
_ROLES.update({
    ("MLB", "hitting"): frozenset((Role.UNKNOWN, Role.BATTING_LINEUP, Role.BENCH)),
    ("MLB", "pitching"): frozenset((Role.UNKNOWN, Role.STARTING_PITCHER, Role.RELIEF_PITCHER)),
    ("NHL", "player"): frozenset((Role.UNKNOWN, Role.STARTING_GOALIE, Role.BACKUP_GOALIE, Role.SKATER)),
})


def _time(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value


@dataclass(frozen=True)
class ParticipationIdentity:
    league: str
    game_id: str
    player_id: str
    team_id: str
    stat_group: str
    identity_namespace: str
    start: datetime

    def __post_init__(self):
        for key in ("league", "game_id", "player_id", "team_id", "stat_group", "identity_namespace"):
            if not _identifier(getattr(self, key)):
                raise ValueError(f"invalid {key}")
        if not self.game_id.startswith(self.league + ":") or self.game_id == self.league + ":":
            raise ValueError("game_id must be league-prefixed")
        object.__setattr__(self, "start", _time(self.start))


@dataclass(frozen=True)
class SourceDocument:
    body: bytes
    source_sha256: str
    source_url: str
    observed_at: datetime
    archive_reference: str
    # Explicitly retain synthetic/unverified status even when checks pass.
    verification: str = "unverified"


@dataclass(frozen=True)
class ParticipationEvidence:
    identity: ParticipationIdentity
    source_sha256: str
    source_url: str
    observed_at: datetime
    valid_until: datetime
    source_published_at: datetime | None
    archive_reference: str
    verification: str
    participation: Participation
    role: Role
    uncertainty: Uncertainty


@dataclass(frozen=True)
class EvidenceReview:
    index: int
    reasons: tuple[str, ...]
    evidence: ParticipationEvidence | None = None
    # Preserve references even when decoding/identity/time checks fail.
    source_sha256: str | None = None
    source_url: str | None = None
    historical_played: bool | None = None


@dataclass(frozen=True)
class ParticipationAssessment:
    identity: ParticipationIdentity
    as_of: datetime
    participation: Participation
    role: Role
    uncertainty: Uncertainty
    valid_until: datetime | None
    reasons: tuple[str, ...]
    conflicts: tuple[str, ...]
    reviews: tuple[EvidenceReview, ...]
    production_supported: bool = False


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def decode_document(document: SourceDocument) -> ParticipationEvidence:
    """Verify bytes and decode an internal participation-evidence-v1 snapshot.

    Missing publication time stays None. observed_at is archive metadata, never
    copied from scheduled start, filesystem time or a historical log fetch.
    """
    if not isinstance(document.body, bytes) or sha256(document.body).hexdigest() != document.source_sha256:
        raise ValueError("source_hash_mismatch")
    if not _identifier(document.source_url):
        raise ValueError("invalid_source_metadata")
    url = urlsplit(document.source_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or not _identifier(document.archive_reference)
            or document.verification not in ("fixture", "unverified", "archived_observation")):
        raise ValueError("invalid_source_metadata")
    row = json.loads(document.body, object_pairs_hook=_unique_object)
    if not isinstance(row, dict) or row.get("schema") != "participation-evidence-v1":
        raise ValueError("unsupported_source_schema")
    if row.get("evidence_kind") != "current_event_report":
        raise ValueError("not_current_participation_evidence")
    identity = ParticipationIdentity(**{k: row[k] for k in ParticipationIdentity.__dataclass_fields__})
    uncertainty = Uncertainty(row["uncertainty"])
    if uncertainty == Uncertainty.CONFLICT:
        raise ValueError("conflict_is_a_validator_result")
    return ParticipationEvidence(
        identity, document.source_sha256, document.source_url, _time(document.observed_at),
        _time(row["valid_until"]),
        _time(row["source_published_at"]) if row.get("source_published_at") is not None else None,
        document.archive_reference, document.verification,
        Participation(row["participation"]), Role(row["role"]), uncertainty,
    )


def validate_participation(
    identity: ParticipationIdentity,
    documents: Iterable[SourceDocument | Mapping], *,
    as_of: datetime | str, max_age: timedelta,
) -> ParticipationAssessment:
    """Validate exact event evidence, retaining all rejected/conflicting claims.

    max_age is an explicit research policy, not a new production threshold.
    Conflicting eligible sources (including revisions from one source) are never
    resolved by input order, voting, or choosing the most optimistic report.
    """
    if not isinstance(identity, ParticipationIdentity):
        raise ValueError("typed identity required")
    cutoff = _time(as_of)
    if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
        raise ValueError("positive max_age required")
    supported = (identity.league, identity.stat_group) in _ROLES
    reviews = []
    eligible = []
    for index, document in enumerate(documents):
        if isinstance(document, Mapping):
            historical = all(k in document for k in ("game_id", "stats", "source_fetched_at"))
            reviews.append(EvidenceReview(
                index, ("historical_log_not_current_evidence" if historical else "missing_source_document",),
                source_sha256=document.get("source_sha256"), source_url=document.get("source_url"),
                historical_played=document.get("played") if type(document.get("played")) is bool else None,
            ))
            continue
        if not isinstance(document, SourceDocument):
            reviews.append(EvidenceReview(index, ("invalid_document",)))
            continue
        try:
            evidence = decode_document(document)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            reviews.append(EvidenceReview(index, ("invalid_evidence", str(error)),
                                          source_sha256=document.source_sha256, source_url=document.source_url))
            continue
        reasons = []
        for key in ParticipationIdentity.__dataclass_fields__:
            if getattr(evidence.identity, key) != getattr(identity, key):
                reasons.append(key + "_mismatch")
        if not supported:
            reasons.append("unsupported_model_family")
        elif evidence.role not in _ROLES[(identity.league, identity.stat_group)]:
            reasons.append("role_not_valid_for_family")
        if evidence.observed_at > cutoff:
            reasons.append("not_observed_as_of")
        if evidence.valid_until <= evidence.observed_at:
            reasons.append("invalid_validity_interval")
        published = evidence.source_published_at
        if published is not None and published > evidence.observed_at:
            reasons.append("publication_after_observation")
        if evidence.valid_until <= cutoff or cutoff - evidence.observed_at >= max_age:
            reasons.append("stale_evidence")
        # Re-fetching an old published report cannot renew its freshness.
        if published is not None and cutoff - published >= max_age:
            reasons.append("stale_publication")
        if evidence.participation in (Participation.OUT, Participation.DNP) and evidence.role != Role.UNKNOWN:
            reasons.append("nonparticipation_role_conflict")
        if cutoff >= identity.start:
            reasons.append("event_started")
        reviews.append(EvidenceReview(index, tuple(reasons), evidence,
                                      evidence.source_sha256, evidence.source_url))
        if not reasons:
            eligible.append(evidence)

    conflicts = []
    for field in ("participation", "role"):
        values = {getattr(e, field).value for e in eligible} - {"unknown"}
        if len(values) > 1:
            conflicts.append(field)
    if (any(e.participation in (Participation.OUT, Participation.DNP) for e in eligible)
            and any(e.role != Role.UNKNOWN for e in eligible)):
        conflicts.append("nonparticipation_role")
    reasons = []
    participation, role, uncertainty = Participation.UNKNOWN, Role.UNKNOWN, Uncertainty.UNKNOWN
    valid_until = None
    if not supported:
        reasons.append("unsupported_model_family")
    if cutoff >= identity.start:
        reasons.append("event_started")
    if conflicts:
        reasons.append("source_conflict")
        uncertainty = Uncertainty.CONFLICT
    elif eligible:
        known = [e for e in eligible if e.uncertainty != Uncertainty.UNKNOWN]
        statuses = {e.participation for e in known} - {Participation.UNKNOWN}
        roles = {e.role for e in known} - {Role.UNKNOWN}
        participation = next(iter(statuses), Participation.UNKNOWN)
        role = next(iter(roles), Role.UNKNOWN)
        contributors = [e for e in known if e.participation != Participation.UNKNOWN or e.role != Role.UNKNOWN]
        if contributors:
            uncertainty = (Uncertainty.REPORTED if any(e.uncertainty == Uncertainty.REPORTED for e in contributors)
                           else Uncertainty.CONFIRMED)
            valid_until = min(min(e.valid_until, identity.start, e.observed_at + max_age,
                                  (e.source_published_at or e.observed_at) + max_age) for e in contributors)
        if participation == Participation.UNKNOWN:
            reasons.append("participation_unknown")
        if role == Role.UNKNOWN:
            reasons.append("role_unknown")
    else:
        reasons.append("no_current_evidence")
    return ParticipationAssessment(identity, cutoff, participation, role, uncertainty,
                                   valid_until, tuple(reasons), tuple(conflicts), tuple(reviews))
