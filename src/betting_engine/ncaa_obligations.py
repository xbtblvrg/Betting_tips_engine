"""Experimental, passive NCAA implementation obligations; never betting approval.

Consumes stake_api.tournaments registry/source references and market_catalog rows.
No model lookup, forecast, settlement, network call or production integration.
All obligations stay blocked until a separately reviewed validation/closure path
exists. In particular, absence and completed research cannot close them.
"""

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .stake_api import BASE, college_league

SCHEMA = "experimental-ncaa-obligations-v1"
REQUIREMENTS = (
    "independent_data", "independent_model", "independent_settlement",
    "current_exact_contract", "chronological_family_validation",
)
KNOWLEDGE = ("K02.1", "K03.1", "K03.3", "K08.2")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Explicit timezone-aware observation time required")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Naive observation time")
    return stamp.astimezone(timezone.utc)


def _source_problem(source, as_of):
    if not isinstance(source, dict):
        return "missing_source_reference"
    if not source.get("url") or not re.fullmatch(r"[a-f0-9]{64}", str(source.get("sha256", ""))):
        return "missing_source_url_or_hash"
    try:
        if _time(source.get("fetched_at")) > _time(as_of):
            return "source_observed_after_snapshot"
    except (ValueError, TypeError):
        return "missing_or_invalid_source_observation_time"
    return None


def snapshot_from_catalog(summary, endpoints, contracts):
    """Adapt an existing complete-catalogue publication, without reading archives.

    Old summary files contain league -> tournament slugs, not sport IDs. Recover
    a sport only from one matching observed USA tournament endpoint and the
    existing college_league naming rule. Ambiguous mappings remain explicit gaps.
    References are not proof that the archived raw bodies are locally available.
    """
    as_of = summary["checked_at"]
    _time(as_of)
    if any(doc.get("checked_at") != as_of for doc in (endpoints, contracts)):
        raise ValueError("Catalogue summary, endpoints and contracts must share a publication")
    sources = endpoints["tournament_sources"]
    sports = set()
    for source in sources:
        match = re.fullmatch(re.escape(BASE) + r"/sports/([a-z0-9-]+)/usa/tournaments", source.get("url", ""))
        if match:
            sports.add(match[1])
    registry = {}
    issues = copy.deepcopy(summary.get("issues", []) + contracts.get("issues", []))
    for league, slugs in summary["tournaments"].items():
        if not league.startswith("NCAA"):
            continue
        for slug in slugs:
            candidates = [s for s in sorted(sports) if college_league(s, {"slug": slug}) == league]
            if len(candidates) != 1:
                issues.append({"reason": "unresolved_summary_sport", "league": league, "slug": slug})
                continue
            sport = candidates[0]
            registry.setdefault(league, []).append({
                "sport": sport, "slug": slug, "category": "usa", "enabled": True,
                "league_path": f"{sport}/usa/{slug}",
                "identity_basis": "recorded_summary_and_unique_tournament_endpoint",
            })
    return {"registry": registry, "sources": copy.deepcopy(sources),
            "rows": copy.deepcopy(contracts["rows"]), "as_of": as_of, "issues": issues}


def _requirement_rows(identity):
    return {name: {"state": "missing_validation", "required_identity": copy.deepcopy(identity)}
            for name in REQUIREMENTS}


def evolve(previous, snapshot):
    """Pure, idempotent union of discoveries; retains all prior evidence and gaps.

    Tournament foundations are distinct from observed family/scope/section
    obligations. No synthetic family is created for an empty tournament. Unknown
    family names/scopes are retained as supplied, never mapped to a pro model.
    """
    as_of = snapshot["as_of"]
    _time(as_of)
    state = copy.deepcopy(previous) if previous is not None else {
        "schema": SCHEMA, "obligations": {}, "snapshots": {},
        "production_supported": False, "closure_enabled": False,
    }
    if state.get("schema") != SCHEMA:
        raise ValueError("Unsupported obligation schema")
    # This utility has no evidence verifier that can accept implementation claims.
    if state.get("closure_enabled") is not False or state.get("production_supported") is not False:
        raise ValueError("Unsupported approval state")
    for obligation in state["obligations"].values():
        if obligation.get("state") != "blocked_on_validation" or any(
            r.get("state") != "missing_validation" for r in obligation["requirements"].values()
        ):
            raise ValueError("Unsupported obligation closure")
    snapshot_id = _digest(snapshot)
    if snapshot_id in state["snapshots"]:
        return state
    issues = copy.deepcopy(snapshot.get("issues", []))
    sources = snapshot["sources"]
    discovered = {}
    seen = set()

    def record(identity, evidence, dependencies=()):
        key = _digest(identity)
        seen.add(key)
        obligation = state["obligations"].setdefault(key, {
            "id": key, "identity": identity, "state": "blocked_on_validation",
            "first_observed_at": as_of, "last_observed_at": as_of,
            "requirements": _requirement_rows(identity), "evidence": {},
            "catalogue_dependencies": [], "knowledge_references": list(KNOWLEDGE),
            "model_fallback_allowed": False, "counts_as_instruction": False,
        })
        obligation["first_observed_at"] = min(obligation["first_observed_at"], as_of, key=_time)
        obligation["last_observed_at"] = max(obligation["last_observed_at"], as_of, key=_time)
        obligation["evidence"][_digest(evidence)] = copy.deepcopy(evidence)
        obligation["catalogue_dependencies"] = sorted(set(obligation["catalogue_dependencies"]) | set(dependencies))

    for league, tournaments in snapshot["registry"].items():
        if not league.startswith("NCAA"):
            continue
        for tournament in tournaments:
            sport, slug = tournament.get("sport"), tournament.get("slug")
            if (not isinstance(sport, str) or not re.fullmatch(r"[a-z0-9-]+", sport)
                    or not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9-]+", slug)
                    or tournament.get("category") != "usa" or tournament.get("enabled") is not True
                    or college_league(sport, tournament) != league):
                issues.append({"reason": "invalid_ncaa_discovery", "league": league, "record": tournament})
                continue
            path = f"{sport}/usa/{slug}"
            if tournament.get("league_path") != path:
                issues.append({"reason": "conflicting_tournament_path", "league": league, "record": tournament})
                continue
            refs = [s for s in sources if s.get("url") == f"{BASE}/sports/{sport}/usa/tournaments"]
            identity = {"sport": sport, "league": league, "tournament": slug, "category": "usa"}
            discovered.setdefault(league, {})[path] = identity
            problems = [_source_problem(s, as_of) for s in refs] or ["missing_tournament_source"]
            issues.extend({"reason": problem, "identity": identity} for problem in problems if problem)
            record({**identity, "kind": "sport_foundation"}, {
                "tournament": tournament, "sources": refs, "observed_at": as_of,
                "source_publication_time": None, "raw_bodies_verified": False,
            })

    for row in snapshot["rows"]:
        league = row.get("league", "")
        if not league.startswith("NCAA"):
            continue
        candidates = discovered.get(league, {})
        path = row.get("league_path")
        matches = ([candidates[path]] if path in candidates else []) if path else list(candidates.values())
        if len(matches) != 1:
            issues.append({"reason": "unbound_catalogue_row", "row": copy.deepcopy(row)})
            continue
        # Preserve the recorded classifier output; its default scope is not a
        # validated sportsbook rule. Missing fields do not silently mean full game.
        family, scope, section = (row.get(k) for k in ("family", "scope", "provider_section"))
        if not all(isinstance(v, str) and v for v in (family, scope, section)):
            issues.append({"reason": "missing_catalogue_family_identity", "row": copy.deepcopy(row)})
            continue
        identity = {**matches[0], "kind": "market_family", "family": family,
                    "scope": scope, "provider_section": section}
        problem = _source_problem(row.get("source"), as_of)
        if problem:
            issues.append({"reason": problem, "identity": identity, "fixture_id": row.get("fixture_id")})
        record(identity, {"catalogue_row": row, "observed_at": as_of,
                          "source_publication_time": None, "raw_bodies_verified": False},
               row.get("dependencies", []))

    state["snapshots"][snapshot_id] = {
        "as_of": as_of, "observed_obligation_ids": sorted(seen), "issues": issues,
        "discovery_state": "partial" if issues else "observed" if discovered else "no_discovery",
        "absent_is_implemented": False,
    }
    return state


def check_dependency_identity(obligation, component, proposed_identity):
    """Reject aliases explicitly; identity matching alone never accepts a model."""
    if component not in REQUIREMENTS:
        raise ValueError("Unknown implementation requirement")
    required = obligation["requirements"][component]["required_identity"]
    mismatches = sorted(k for k, v in required.items() if proposed_identity.get(k) != v)
    return {"accepted": False, "identity_matches": not mismatches,
            "mismatched_fields": mismatches,
            "reason": "independent_scope_required" if mismatches else "validation_not_implemented"}


def update_file(path, snapshot):
    """Explicit isolated JSON state only; serialized read/union/atomic replace.

    No default path or engine data-directory lookup. Caller owns the output path.
    Corrupt state raises; it is never reset to an empty queue.
    """
    path = Path(path)
    if path.suffix != ".json":
        raise ValueError("Obligation store must be a dedicated JSON file")
    with path.with_suffix(".json.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(path.read_text()) if path.exists() else None
        state = evolve(previous, snapshot)
        if state == previous:
            return state
        raw = json.dumps(state, sort_keys=True, indent=2, allow_nan=False) + "\n"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
                temporary = out.name
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return state
