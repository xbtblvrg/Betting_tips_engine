"""Synthetic discovery tests plus replay of an archived real Stake odds body."""

import copy
import gzip
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from betting_engine.market_catalog import raw_inventory
from betting_engine.ncaa_obligations import (
    REQUIREMENTS, check_dependency_identity, evolve, snapshot_from_catalog, update_file,
)
from betting_engine.stake_api import BASE, SPORTS, college_league, tournaments

STAMP = "2026-09-29T20:00:00+00:00"


def source(url):
    # Synthetic source reference; never offered as a live source observation.
    return {"url": url, "fetched_at": STAMP, "sha256": "a" * 64}


def snapshot(sport="volleyball", slug="ncaa-women"):
    league = college_league(sport, {"slug": slug})
    tournament = {"sport": sport, "slug": slug, "enabled": True, "category": "usa",
                  "league_path": f"{sport}/usa/{slug}"}
    row = {"league": league, "fixture_id": "synthetic-fixture", "market_id": "synthetic-market",
           "market": "Winner", "family": "score_contract", "scope": "regulation",
           "provider_section": "standard", "dependencies": ["exact_payoff_rule"],
           "tabs": [{"id": "main-id", "name": "main"}],
           "source": source(BASE + "/odds/synthetic-fixture"), "outcomes": [],
           "start": "2026-10-20T20:00:00+00:00"}
    return {"as_of": STAMP, "registry": {league: [tournament]}, "rows": [row],
            "sources": [source(f"{BASE}/sports/{sport}/usa/tournaments")], "issues": []}


def families(state):
    return [o for o in state["obligations"].values() if o["identity"]["kind"] == "market_family"]


def test_unknown_enabled_sport_flows_from_existing_tournament_adapter():
    class Feed:
        def json(self, url, ttl):
            if url == BASE + "/sports":
                body = [{"slug": "lacrosse", "enabled": True, "type": "sport"}]
            elif url.endswith("/lacrosse/categories"):
                body = {"categories": [{"slug": "usa", "enabled": True}]}
            elif url.endswith("/lacrosse/usa/tournaments"):
                body = {"tournaments": [{"slug": "ncaa-women", "name": "NCAA Women", "enabled": True}]}
            elif any(url == f"{BASE}/sports/{s}/usa/tournaments" for s in SPORTS.values()):
                body = {"tournaments": []}
            else:
                raise AssertionError(url)
            return body, source(url)

    registry, _, sources, issues = tournaments(Feed())
    snap = snapshot("lacrosse")
    snap.update(registry=registry, sources=sources, issues=issues)
    state = evolve(None, snap)
    assert len(state["obligations"]) == 2
    for obligation in state["obligations"].values():
        assert obligation["identity"]["sport"] == "lacrosse"
        assert set(obligation["requirements"]) == set(REQUIREMENTS)
        assert obligation["model_fallback_allowed"] is False
        assert obligation["counts_as_instruction"] is False
        assert obligation["state"] == "blocked_on_validation"


@pytest.mark.parametrize("component", REQUIREMENTS)
@pytest.mark.parametrize("field,value", [("league", "NBA"), ("league", "NCAAW"),
                                         ("sport", "basketball"), ("scope", "full_game"),
                                         ("tournament", "ncaa-men")])
def test_pro_unrelated_college_and_scope_aliases_never_satisfy_requirements(component, field, value):
    obligation = families(evolve(None, snapshot()))[0]
    identity = {**obligation["identity"], field: value}
    result = check_dependency_identity(obligation, component, identity)
    assert not result["accepted"] and result["mismatched_fields"] == [field]
    assert result["reason"] == "independent_scope_required"


def test_even_matching_identity_is_not_validation_or_closure():
    obligation = families(evolve(None, snapshot()))[0]
    result = check_dependency_identity(obligation, "independent_model", obligation["identity"])
    assert result == {"accepted": False, "identity_matches": True, "mismatched_fields": [],
                      "reason": "validation_not_implemented"}


def test_families_scopes_sports_and_tournaments_are_separate():
    snap = snapshot()
    row = snap["rows"][0]
    snap["rows"] += [{**row, "family": "player_props"}, {**row, "scope": "set_1"},
                     {**row, "family": "future_unclassified", "provider_section": "swishMarkets.future"}]
    state = evolve(None, snap)
    state = evolve(state, snapshot("lacrosse"))
    state = evolve(state, snapshot("volleyball", "ncaa-men"))
    assert len(families(state)) == 6
    assert len(state["obligations"]) == 9
    assert all(o["state"] == "blocked_on_validation" for o in state["obligations"].values())


def test_durable_union_survives_empty_failed_catalogue_and_completed_research(tmp_path):
    path = tmp_path / "ncaa.json"
    original = update_file(path, snapshot())
    empty = {"as_of": "2026-10-01T20:00:00+00:00", "registry": {}, "rows": [], "sources": [],
             "issues": [{"reason": "source_failure"}],
             "research_dossiers": [{"state": "completed", "implemented": True}]}
    updated = update_file(path, empty)
    assert updated["obligations"] == original["obligations"]
    assert len(updated["snapshots"]) == 2
    assert any(s["discovery_state"] == "partial" for s in updated["snapshots"].values())
    assert json.loads(path.read_text()) == updated
    raw = path.read_bytes()
    assert update_file(path, empty) == updated
    assert path.read_bytes() == raw
    empty.update(issues=[], as_of="2026-10-02T20:00:00+00:00")
    final = update_file(path, empty)
    assert final["obligations"] == original["obligations"]
    assert any(s["discovery_state"] == "no_discovery" for s in final["snapshots"].values())


def test_discovered_tournament_without_markets_has_independent_foundation():
    snap = snapshot()
    snap["rows"] = []
    state = evolve(None, snap)
    assert len(state["obligations"]) == 1 and not families(state)
    assert set(next(iter(state["obligations"].values()))["requirements"]) == set(REQUIREMENTS)


def test_ambiguous_tournament_mapping_retains_row_as_unresolved_evidence():
    snap = snapshot("american-football", "ncaa-regular")
    snap["registry"]["NCAAF"].append({**snap["registry"]["NCAAF"][0], "slug": "ncaa-playoffs",
                                      "league_path": "american-football/usa/ncaa-playoffs"})
    state = evolve(None, snap)
    assert len(state["obligations"]) == 2 and not families(state)
    issue = next(iter(state["snapshots"].values()))["issues"][0]
    assert issue["reason"] == "unbound_catalogue_row" and issue["row"] == snap["rows"][0]
    snap["rows"][0]["league_path"] = "american-football/usa/ncaa-regular"
    state = evolve(state, snap)
    assert len(families(state)) == 1


@pytest.mark.parametrize("bad", [{}, {"fetched_at": "2026-10-01T20:00:00Z"},
                                  {"fetched_at": "2026-09-29T20:00:00"}, {"sha256": "unknown"}])
def test_missing_or_future_sources_are_explicit_and_never_filled_in(bad):
    snap = snapshot()
    snap["rows"][0]["source"] = {**snap["rows"][0]["source"], **bad} if bad else {}
    before = copy.deepcopy(snap)
    state = evolve(None, snap)
    assert next(iter(state["snapshots"].values()))["issues"]
    evidence = next(iter(families(state)[0]["evidence"].values()))
    assert evidence["source_publication_time"] is None
    assert evidence["catalogue_row"]["source"] == before["rows"][0]["source"]
    assert snap == before


def test_no_discovery_does_not_invent_sport_and_preserves_orphan_row():
    snap = snapshot()
    snap["registry"] = {}
    state = evolve(None, snap)
    assert not state["obligations"]
    assert next(iter(state["snapshots"].values()))["issues"][0]["row"] == snap["rows"][0]


def test_reordered_history_preserves_first_and_last_observation_without_mutation():
    snap = snapshot()
    newer = copy.deepcopy(snap)
    newer["as_of"] = "2026-10-01T00:00:00Z"
    original = evolve(None, newer)
    before = copy.deepcopy(original)
    merged = evolve(original, snap)
    assert original == before
    for obligation in merged["obligations"].values():
        assert obligation["first_observed_at"] == STAMP
        assert obligation["last_observed_at"] == newer["as_of"]


def test_locked_writers_union_separate_sports(tmp_path):
    path = tmp_path / "obligations.json"
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda sport: update_file(path, snapshot(sport)), ["volleyball", "lacrosse", "waterpolo"]))
    assert len(json.loads(path.read_text())["obligations"]) == 6


def test_corrupt_or_closed_store_is_not_silently_reset(tmp_path):
    path = tmp_path / "obligations.json"
    path.write_text("not json")
    with pytest.raises(ValueError):
        update_file(path, snapshot())
    assert path.read_text() == "not json"
    state = evolve(None, snapshot())
    next(iter(state["obligations"].values()))["state"] = "implemented"
    with pytest.raises(ValueError, match="closure"):
        evolve(state, snapshot())


def test_real_archived_ncaa_odds_schema_retains_exact_rows_and_outside_48h():
    folder = Path(__file__).parent / "fixtures/player_props"
    manifest = json.loads((folder / "manifest.json").read_text())
    meta = next(f for f in manifest["fixtures"] if f["name"] == "ncaa-no-props")
    raw = gzip.decompress((folder / meta["body_file"]).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == meta["sha256"]
    body = json.loads(raw)
    fixture = {**body["fixture"], "date": body["fixture"]["startTime"]}
    rows = raw_inventory(body, fixture, "NCAAF", meta)
    snap = snapshot("american-football", "ncaa-regular")
    snap["rows"] = rows
    state = evolve(None, snap)
    retained = [e["catalogue_row"] for o in families(state) for e in o["evidence"].values()]
    assert len(retained) == len(rows) > 0
    assert sorted(retained, key=lambda r: r["market_id"]) == sorted(rows, key=lambda r: r["market_id"])
    assert {r["fixture_id"] for r in retained} == {meta["fixture_id"]}
    assert any(o["identity"]["scope"] == "half_1" for o in families(state))
    assert all(not o["counts_as_instruction"] for o in families(state))


def test_summary_adapter_matches_observed_sport_and_rejects_mixed_publications():
    snap = snapshot("american-football", "ncaa-regular")
    summary = {"checked_at": STAMP, "tournaments": {"NCAAF": ["ncaa-regular"], "NCAAM": []}}
    endpoints = {"checked_at": STAMP, "tournament_sources": snap["sources"]}
    contracts = {"checked_at": STAMP, "rows": snap["rows"]}
    adapted = snapshot_from_catalog(summary, endpoints, contracts)
    assert adapted["registry"]["NCAAF"][0]["sport"] == "american-football"
    assert len(families(evolve(None, adapted))) == 1
    assert "NCAAM" not in adapted["registry"]
    contracts["checked_at"] = "2026-10-01T00:00:00Z"
    with pytest.raises(ValueError, match="share a publication"):
        snapshot_from_catalog(summary, endpoints, contracts)


def test_summary_without_sport_evidence_does_not_guess_from_pro_league():
    summary = {"checked_at": STAMP, "tournaments": {"NCAAF": ["ncaa-regular"]}}
    snap = snapshot_from_catalog(summary, {"checked_at": STAMP, "tournament_sources": []},
                                 {"checked_at": STAMP, "rows": []})
    assert not snap["registry"] and snap["issues"][0]["reason"] == "unresolved_summary_sport"
