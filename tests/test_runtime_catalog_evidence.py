"""Offline regressions: replayed observations and explicitly synthetic failures."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.catalog_evidence import fixture_context
from betting_engine.component_health import audit_components
from betting_engine.market_catalog import raw_inventory, refresh_catalog
from betting_engine.stake_api import BASE, market_records


STAMP = "2026-09-30T00:12:27.766450+00:00"
NOW = datetime.fromisoformat(STAMP)
FIXTURE = {
    "id": "synthetic-fixture", "slug": "synthetic-fixture",
    "name": "Houston Astros - Chicago White Sox", "date": 1790715600000,
    "seed_name_resolution_required": {
        "schedule_name": "AL Seed 3 - AL Seed 6",
        "fixture_name": "Houston Astros - Chicago White Sox",
    },
}


def market(name="Handicap (Incl. Extra Innings)", specifiers="hcp=2.5"):
    return {"id": name, "name": name, "status": "active", "specifiers": specifiers,
            "outcomes": [{"id": "home", "name": "Houston Astros (2.5)",
                          "odds": 4.2, "active": True, "voidFactor": 0},
                         {"id": "away", "name": "Chicago White Sox (-2.5)",
                          "odds": 1.18, "active": True, "voidFactor": 0}]}


def payload():
    return {"fixture": {"id": FIXTURE["id"], "slug": FIXTURE["slug"],
                        "name": FIXTURE["name"], "startTime": FIXTURE["date"],
                        "enabled": False, "preMatchEnabled": False,
                        "status": "active", "futureProviderField": {"raw": 1}},
            "groups": [{"id": "main", "name": "main", "markets": [[market()]]}],
            "swishMarkets": {"playerProps": [{"id": "prop", "marketName": "hits",
                                              "outcomes": [{"over": 1.9}]}]}}


class OfflineFeed:
    """Test responses, not live observations or authenticated source times."""
    def __init__(self, bodies):
        self.bodies = bodies

    def json(self, url, ttl):
        body = self.bodies[url]
        if isinstance(body, Exception):
            raise body
        digest = hashlib.sha256(json.dumps(body).encode()).hexdigest()
        return deepcopy(body), {"url": url, "sha256": digest, "fetched_at": STAMP,
                                "archive": "synthetic/" + digest + ".json.gz"}


def run_refresh(tmp_path, monkeypatch, bodies, fixture=None):
    import betting_engine.stake_api as api
    monkeypatch.setattr(api, "tournaments", lambda f: ({"MLB": []}, {"MLB"}, [], []))
    monkeypatch.setattr(api, "league_fixtures", lambda *args:
                        ([deepcopy(fixture or FIXTURE)], [], [], True))
    result = refresh_catalog(tmp_path, fetcher=OfflineFeed(bodies))
    rows = json.loads((tmp_path / result["contracts_archive"]).read_text())["rows"]
    return result, rows


def bodies(first=None, second=None):
    return {BASE + "/fixtures/" + FIXTURE["slug"]: first if first is not None else payload(),
            BASE + "/odds/" + FIXTURE["slug"]: second if second is not None else payload()}


def test_refresh_retains_unresolved_disabled_fixture_with_all_market_observations(tmp_path, monkeypatch):
    result, rows = run_refresh(tmp_path, monkeypatch, bodies())
    assert result["fixtures_fetched"] == 1
    assert result["complete_endpoint_responses"] == 2
    assert len(rows) == 2  # Standard and unverified Swish both remain inventoried.
    assert rows[0]["raw_contract"] == market()
    assert rows[0]["outcomes"] == market()["outcomes"]
    assert rows[0]["specifiers"] == "hcp=2.5"
    for row in rows:
        context = row["fixture_evidence"]
        assert context["bundle_identity_verified"] is False
        assert context["execution_authorized"] is False
        assert context["response_fixture"] == payload()["fixture"]
        assert context["schedule_fixture"] == FIXTURE
        assert context["response_endpoint"] == "odds"
        assert context["bundle_seed_name_resolution"]["schedule_name"] == "AL Seed 3 - AL Seed 6"
        assert {e["reason"] for e in context["bundle_errors"]} == {
            "stake_seed_identity_unresolved", "stake_fixture_not_active"}
        assert row["source"]["url"].endswith("/odds/synthetic-fixture")
        assert not row["recognition_is_model_support"]
    assert result["state"] == "partial"
    assert result["issues"][0]["reason"] == "stake_seed_identity_unresolved"


def test_companion_only_market_keeps_own_response_context_and_source(tmp_path, monkeypatch):
    first = payload()
    first["fixture"]["preMatchEnabled"] = True
    first["groups"][0]["markets"][0].append(market("1st Inning - Total", "total=0.5"))
    result, rows = run_refresh(tmp_path, monkeypatch, bodies(first=first))
    companion = next(r for r in rows if r.get("companion_response_only"))
    assert companion["market"] == "1st Inning - Total"
    assert companion["scope"] == "inning_1"
    assert companion["fixture_evidence"]["response_fixture"] == first["fixture"]
    assert companion["fixture_evidence"]["response_endpoint"] == "fixtures"
    assert companion["fixture_evidence"]["bundle_selected_endpoint"] == "odds"
    assert companion["source"]["url"].endswith("/fixtures/synthetic-fixture")
    assert companion["source"]["sha256"] != rows[0]["source"]["sha256"]
    assert not companion["fixture_evidence"]["execution_authorized"]
    assert result["state"] == "partial"


def test_endpoint_failure_remains_visible_on_fallback_rows(tmp_path, monkeypatch):
    result, rows = run_refresh(tmp_path, monkeypatch, bodies(second=ValueError("offline failure")))
    assert result["complete_endpoint_responses"] == 1
    assert all(r["fixture_evidence"]["response_endpoint"] == "fixtures" for r in rows)
    assert any(e["reason"] == "stake_fixture_endpoint_error"
               for e in rows[0]["fixture_evidence"]["bundle_errors"])


def test_schedule_time_is_not_substituted_for_exact_response_time(tmp_path, monkeypatch):
    schedule = {**FIXTURE, "date": FIXTURE["date"] - 60000}
    _, rows = run_refresh(tmp_path, monkeypatch, bodies(), schedule)
    context = rows[0]["fixture_evidence"]
    assert context["schedule_fixture"]["date"] == schedule["date"]
    assert context["response_fixture"]["startTime"] == FIXTURE["date"]
    # The legacy row start is preserved; exact response evidence remains distinct.
    assert rows[0]["start"] == datetime.fromtimestamp(schedule["date"] / 1000, timezone.utc).isoformat()


@pytest.mark.parametrize("identity", [None, 1, "true", "false", False, True])
def test_context_does_not_convert_missing_or_non_boolean_checks_into_approval(identity):
    result = fixture_context({}, FIXTURE, {"identity_verified": identity})
    assert result["bundle_identity_verified"] is (identity if type(identity) is bool else None)
    assert result["response_fixture"] is None
    assert result["response_endpoint"] is None
    assert result["execution_authorized"] is False


def test_context_does_not_mutate_or_alias_input_evidence():
    body, fixture = payload(), deepcopy(FIXTURE)
    audit = {"errors": [{"reason": "unresolved"}], "identity_verified": False}
    before = deepcopy((body, fixture, audit))
    result = fixture_context(body, fixture, audit)
    assert (body, fixture, audit) == before
    result["response_fixture"]["futureProviderField"]["raw"] = 9
    result["schedule_fixture"]["seed_name_resolution_required"].clear()
    result["bundle_errors"][0]["reason"] = "changed"
    assert (body, fixture, audit) == before


@pytest.mark.parametrize("name,league", [("wnba-standard", "WNBA"), ("nfl-swish", "NFL"),
                                        ("nhl-skater", "NHL"), ("nba-no-props", "NBA"),
                                        ("ncaa-no-props", "NCAAF")])
def test_real_archived_schemas_preserve_contracts_and_fixture_metadata(name, league):
    folder = Path(__file__).parent / "fixtures/player_props"
    manifest = json.loads((folder / "manifest.json").read_text())
    meta = next(f for f in manifest["fixtures"] if f["name"] == name)
    raw = gzip.decompress((folder / meta["body_file"]).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == meta["sha256"]
    body = json.loads(raw)
    schedule = {**body["fixture"], "date": body["fixture"]["startTime"]}
    before = deepcopy(body)
    rows = raw_inventory(body, schedule, league, meta)
    assert rows
    observed = [m for _, m in market_records(body)]
    for row in rows:
        assert row["fixture_evidence"]["response_fixture"] == body["fixture"]
        assert row["fixture_evidence"]["bundle_identity_verified"] is None
        assert row["fixture_evidence"]["execution_authorized"] is False
        assert row["source"]["sha256"] == meta["sha256"]
        assert row["source"]["fetched_at"] == meta["fetched_at"]
        assert "source_published_at" not in row["source"]
        if row["provider_section"] == "standard":
            assert row["raw_contract"] in observed
            assert row["outcomes"] == row["raw_contract"]["outcomes"]
    assert body == before


def test_health_exposes_root_causes_without_closing_fully_fetched_catalogue():
    issue = {"reason": "stake_seed_identity_unresolved", "league": "MLB",
             "fixture": "b6b938c3-627b-425d-84f9-70e828709315"}
    source = {"source": "NCAAF/injuries", "ok": False, "state": "not_integrated"}
    catalog = {"checked_at": STAMP, "discovered_fixtures": 118, "fixtures_fetched": 118,
               "complete_endpoint_responses": 236, "issues": [issue]}
    report = audit_components({"status": {"data_sources": [source]}}, {}, catalog, {}, NOW)
    checks = {c["component"]: c for c in report["checks"]}
    check = checks["Full market catalogue"]
    assert check["state"] == "degraded"
    assert "118/118" in check["detail"] and "stake_seed_identity_unresolved" in check["detail"]
    assert check["issues"] == [issue]
    assert checks["Independent sports sources"]["source_failures"] == [source]
    assert checks["Independent sports sources"]["state"] == "degraded"
    check["issues"][0]["reason"] = "modified"
    checks["Independent sports sources"]["source_failures"][0]["ok"] = True
    assert issue["reason"] == "stake_seed_identity_unresolved" and source["ok"] is False


def test_old_or_future_catalogue_does_not_recover_by_omitting_issues():
    for stamp in ["2026-09-29T00:00:00Z", "2026-10-01T00:00:00Z"]:
        result = audit_components({}, {}, {"checked_at": stamp, "issues": []}, {}, NOW)
        check = next(c for c in result["checks"] if c["component"] == "Full market catalogue")
        assert check["state"] == "degraded"
        assert check["issues"] == []
