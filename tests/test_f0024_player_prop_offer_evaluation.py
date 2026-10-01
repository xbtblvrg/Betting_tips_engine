"""Synthetic end-to-end offer cases; observed archives are rejection probes only."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest
from scipy import stats

from betting_engine.player_prop_contracts import canonical_hash, normalize_player_props
from betting_engine.player_prop_offer_evaluation import evaluate_archived_count_offer
from test_f0023_player_prop_probability import synthetic_archive
from test_player_prop_contracts import original


NOW = "2026-09-29T18:43:03+00:00"
ROOT = Path(__file__).resolve().parents[1]


def encode(value):
    return json.dumps(value).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def case(root, pin, *, line="2", price=2., operator="Over", change=None):
    """Entire endpoint and identity interchange are synthetic, using observed schema."""
    payload = {
        "fixture": dict(id="synthetic-fixture", slug="synthetic-fixture", enabled=True,
                        preMatchEnabled=True, status="active", blacklisted=False,
                        startTime=1790721000000),
        "groups": [dict(id="synthetic-tab", name="player props", markets=[[dict(
            id="synthetic-market", name="Synthetic, Player total points (incl. overtime)",
            status="active", specifiers="player=sr:player:1|total=" + line,
            outcomes=[dict(id="synthetic-outcome", name=operator + " " + line,
                           odds=price, active=True)])]])],
    }
    if change:
        change(payload)
    body = encode(payload)
    evidence = dict(league="WNBA", fixture_id="synthetic-fixture", fixture_slug="synthetic-fixture",
                    url="https://odds-data.stake.com/odds/synthetic-fixture",
                    fetched_at=NOW, sha256=digest(body))
    row = normalize_player_props(body, evidence, league="WNBA")["contracts"][0]
    bodies = {}

    def source(value, url):
        raw = encode(value)
        bodies[digest(raw)] = raw
        return dict(sha256=digest(raw), url=url, fetched_at=NOW)

    mapping = dict(league="WNBA", fixture_id=row["fixture_id"], provider_game_id=row["provider_game_id"],
                   provider_player_name=row["player_name"], provider_player_id=row["player_id"],
                   official_player_id="official-p", official_player_name="Synthetic Player",
                   model_namespace="espn", model_player_id="p", model_team_id="t",
                   model_game_id="WNBA:synthetic-game", fixture_start_time_ms=1790721000000)
    resolution = dict(contract_id=row["contract_id"], feed_source_sha256=evidence["sha256"],
                      player_source=source({"players": [{"id": "official-p", "name": "Synthetic Player"}]},
                                           "https://www.wnba.com/synthetic-player-fixture"),
                      mapping_source=source({"entries": [mapping]},
                                            "https://odds-data.stake.com/synthetic-crosswalk-fixture"),
                      player_id_pointer="/players/0/id", player_name_pointer="/players/0/name",
                      mapping_pointer="/entries/0")
    return dict(study_root=root, artifact_index_sha256=pin, metric_key="WNBA/player/points",
                player_id="p", team_id="t", feed_body=body, feed_evidence=evidence,
                contract_id=row["contract_id"], as_of=NOW, resolution=resolution,
                evidence_bodies=bodies)


@pytest.fixture
def inputs(tmp_path):
    return case(*synthetic_archive(tmp_path, recipe="player_poisson"))


def change_mapping(inputs, operation):
    source = inputs["resolution"]["mapping_source"]
    value = json.loads(inputs["evidence_bodies"][source["sha256"]])
    operation(value["entries"][0])
    body = encode(value)
    inputs["evidence_bodies"][digest(body)] = body
    source["sha256"] = digest(body)


def test_pinned_model_to_exact_offer_with_push_and_preserved_provider_identity(inputs):
    before = deepcopy(inputs)
    out = evaluate_archived_count_offer(**inputs)
    assert out["state"] == "conditional_archived_offer_payoff" and not out["reasons"]
    p = out["payoff"]
    assert p["p_push"] == pytest.approx(stats.poisson.pmf(2, 3))
    assert p["p_win"] == pytest.approx(stats.poisson.sf(2, 3))
    assert p["scenario_net_ev_per_unit"] == pytest.approx(p["p_win"] - p["p_loss"])
    assert p["p_void"] is None
    assert out["contract"]["player_id"] == "sr:player:1"
    assert out["identity_resolution"]["official_player_id"] == "official-p"
    assert out["forecast"]["player_id"] == "p"
    assert out["contract"]["tabs"] == [{"id": "synthetic-tab", "name": "player props"}]
    assert out["contract"]["contract_id"] == inputs["contract_id"]
    assert out["forecast"]["validation_reasons"] == ("historical_availability_not_authenticated",)
    assert not out["publishable"] and not out["execution_eligible"]
    assert not out["production_props_enabled"] and not out["current_participation_verified"]
    assert "sportsbook_settlement_rules_not_verified" in out["activation_blockers"]
    assert out["source_published_at"] is None and inputs == before
    assert out == evaluate_archived_count_offer(**inputs)
    assert out["assessment_sha256"] == canonical_hash({k: v for k, v in out.items() if k != "assessment_sha256"})


def test_price_changes_ev_only_line_changes_contract_and_recomputes_probability(inputs):
    root, pin = inputs["study_root"], inputs["artifact_index_sha256"]
    a = evaluate_archived_count_offer(**inputs)
    b = evaluate_archived_count_offer(**case(root, pin, price=3.))
    c = evaluate_archived_count_offer(**case(root, pin, line="2.5"))
    d = evaluate_archived_count_offer(**case(root, pin, operator="Under"))
    assert a["forecast"] == b["forecast"] == c["forecast"] == d["forecast"]
    assert a["contract"]["contract_id"] == b["contract"]["contract_id"] != c["contract"]["contract_id"]
    assert a["assessment_sha256"] != b["assessment_sha256"]
    for field in ("p_win", "p_push", "p_loss"):
        assert a["payoff"][field] == b["payoff"][field]
    assert b["payoff"]["scenario_net_ev_per_unit"] > a["payoff"]["scenario_net_ev_per_unit"]
    assert c["payoff"]["p_push"] == 0
    assert c["payoff"]["p_loss"] > a["payoff"]["p_loss"]
    assert d["payoff"]["p_win"] == a["payoff"]["p_loss"]


@pytest.mark.parametrize("seconds,reason", [(-1, "quote_not_observed_as_of"), (0, None),
    (300, None), (300.000001, "quote_expired_at_as_of"), (20000, "event_started_at_as_of")])
def test_quote_boundary_and_started_events(inputs, seconds, reason):
    inputs["as_of"] = (datetime.fromisoformat(NOW) + timedelta(seconds=seconds)).isoformat()
    # Keep independent identity captures available in the future-quote case.
    for key in ("player_source", "mapping_source"):
        inputs["resolution"][key]["fetched_at"] = "2026-09-29T18:00:00Z"
    out = evaluate_archived_count_offer(**inputs)
    if reason:
        assert reason in out["reasons"] and out["payoff"] is None
    else:
        assert out["payoff"] is not None


@pytest.mark.parametrize("field,value", [
    ("model_namespace", "wnba"), ("model_player_id", "other"), ("model_team_id", "other"),
    ("model_game_id", "NBA:synthetic-game"), ("model_game_id", "WNBA:"),
    ("fixture_start_time_ms", 1790721000001), ("fixture_start_time_ms", 1790721000000.),
    ("provider_player_id", "sr:player:2"), ("fixture_id", "other"),
    ("provider_game_id", "other"), ("official_player_id", "other"),
])
def test_cross_namespace_event_transfer_and_schedule_mismatches_rejected(inputs, field, value):
    change_mapping(inputs, lambda m: m.update({field: value}))
    out = evaluate_archived_count_offer(**inputs)
    assert "invalid_source_bound_identity_resolution" in out["reasons"] and out["payoff"] is None


@pytest.mark.parametrize("kind", ["missing", "wrong_hash", "changed_body", "future_capture", "naive_capture",
    "bad_host", "future_publication", "wrong_contract", "wrong_feed", "negative_pointer", "duplicate_json"])
def test_missing_or_damaged_identity_never_falls_back_to_name_or_history(inputs, kind):
    r = inputs["resolution"]
    s = r["mapping_source"]
    if kind == "missing": inputs["resolution"] = None
    elif kind == "wrong_hash": s["sha256"] = "0" * 64
    elif kind == "changed_body": inputs["evidence_bodies"][s["sha256"]] = b'{}'
    elif kind == "future_capture": s["fetched_at"] = "2026-09-30T00:00:00Z"
    elif kind == "naive_capture": s["fetched_at"] = "2026-09-29T00:00:00"
    elif kind == "bad_host": s["url"] = "https://odds-data.stake.com.evil.invalid/map"
    elif kind == "future_publication": s["source_published_at"] = "2026-09-30T00:00:00Z"
    elif kind == "wrong_contract": r["contract_id"] = "0" * 64
    elif kind == "wrong_feed": r["feed_source_sha256"] = "0" * 64
    elif kind == "negative_pointer": r["mapping_pointer"] = "/entries/-1"
    elif kind == "duplicate_json":
        body = inputs["evidence_bodies"][s["sha256"]].replace(b'"model_namespace": "espn"',
                                                             b'"model_namespace": "bad", "model_namespace": "espn"')
        s["sha256"] = digest(body)
        inputs["evidence_bodies"][digest(body)] = body
    out = evaluate_archived_count_offer(**inputs)
    assert out["payoff"] is None and out["state"] == "rejected"
    assert any("identity_resolution" in r for r in out["reasons"])


@pytest.mark.parametrize("kind", ["suspended", "outcome_inactive", "prematch_disabled", "blacklisted",
    "period", "regulation", "other_metric", "unknown_scope", "quarter_line", "invalid_price",
    "conflicting_market", "missing_start", "string_start", "absent_contract", "bad_hash", "schedule"])
def test_unsupported_and_inactive_contracts_have_no_payoff(inputs, kind):
    def change(p):
        m = p["groups"][0]["markets"][0][0]
        if kind == "suspended": m["status"] = "suspended"
        elif kind == "outcome_inactive": m["outcomes"][0]["active"] = False
        elif kind == "prematch_disabled": p["fixture"]["preMatchEnabled"] = False
        elif kind == "blacklisted": p["fixture"]["blacklisted"] = True
        elif kind == "period": m["name"] = "Synthetic, Player total 1st quarter points (incl. overtime)"
        elif kind == "regulation": m["name"] = m["name"].replace("incl. overtime", "regular time")
        elif kind == "other_metric": m["name"] = m["name"].replace("points", "rebounds")
        elif kind == "unknown_scope": m["name"] = m["name"].replace(" (incl. overtime)", "")
        elif kind == "invalid_price": m["outcomes"][0]["odds"] = 1
        elif kind == "missing_start": p["fixture"].pop("startTime")
        elif kind == "string_start": p["fixture"]["startTime"] = "1790721000000"
        elif kind == "conflicting_market":
            other = deepcopy(m)
            other["outcomes"][0]["odds"] = 3
            p["groups"][0]["markets"][0].append(other)
    args = case(inputs["study_root"], inputs["artifact_index_sha256"],
                line="2.25" if kind == "quarter_line" else "2", change=change)
    if kind == "absent_contract": args["contract_id"] = "0" * 64
    if kind == "bad_hash": args["feed_evidence"]["sha256"] = "0" * 64
    if kind == "schedule": args["feed_evidence"]["fixture_start"] = "2026-09-29T22:31:00Z"
    out = evaluate_archived_count_offer(**args)
    assert out["payoff"] is None and out["reasons"] and not out["publishable"]


def test_synthetic_ladder_is_inclusive_without_push(inputs):
    def change(p):
        m = p["groups"][0]["markets"][0][0]
        m["name"] = "Player points (incl. overtime)"
        m["outcomes"][0]["name"] = "Synthetic, Player 2+"
    args = case(inputs["study_root"], inputs["artifact_index_sha256"], change=change)
    out = evaluate_archived_count_offer(**args)
    assert out["payoff"]["p_win"] == pytest.approx(stats.poisson.sf(1, 3))
    assert out["payoff"]["p_push"] == 0


@pytest.mark.parametrize("conflicting", [False, True])
def test_crosswalk_pointer_cannot_choose_among_duplicate_or_conflicting_bindings(inputs, conflicting):
    source = inputs["resolution"]["mapping_source"]
    payload = json.loads(inputs["evidence_bodies"][source["sha256"]])
    other = deepcopy(payload["entries"][0])
    if conflicting:
        other["model_team_id"] = "another-team"
    payload["entries"].append(other)
    body = encode(payload)
    source["sha256"] = digest(body)
    inputs["evidence_bodies"][digest(body)] = body
    out = evaluate_archived_count_offer(**inputs)
    assert out["payoff"] is None
    assert out["identity_diagnostic"] == "ambiguous_crosswalk_records"


def test_started_at_exact_boundary_is_rejected_even_with_fresh_quote(inputs):
    start = datetime.fromtimestamp(1790721000, tz=datetime.fromisoformat(NOW).tzinfo).isoformat()
    inputs["as_of"] = start
    inputs["feed_evidence"]["fetched_at"] = start
    out = evaluate_archived_count_offer(**inputs)
    assert out["quote_age_seconds_at_as_of"] == 0
    assert "event_started_at_as_of" in out["reasons"] and out["payoff"] is None


@pytest.mark.parametrize("kind", ["model_hash", "study_future", "naive_as_of", "changed_model"])
def test_model_integrity_and_chronology(inputs, kind):
    if kind == "model_hash": inputs["artifact_index_sha256"] = "0" * 64
    elif kind == "study_future": inputs["as_of"] = "2025-02-28T00:00:00Z"
    elif kind == "naive_as_of": inputs["as_of"] = "2026-09-29T18:43:03"
    else:
        path = inputs["study_root"] / "WNBA_player_points/player_poisson-test-model.json"
        path.write_bytes(b'{}')
    out = evaluate_archived_count_offer(**inputs)
    assert out["payoff"] is None and out["reasons"]


@pytest.mark.parametrize("name,metric", [("wnba-standard", "WNBA/player/points"),
    ("wnba-fixtures-endpoint", "WNBA/player/points"), ("nba-no-props", "NBA/player/points"),
    ("nhl-skater", "NHL/player/shots_on_goal"), ("nhl-goalie", "NHL/player/shots_on_goal"),
    ("nfl-swish", "NFL/player/rushing_yards"), ("ncaa-no-props", "NCAAF/player/points")])
def test_real_archives_never_become_instructions_or_synthetic_crosswalks(name, metric):
    body, evidence = original(name)
    rows = normalize_player_props(body, evidence, league=evidence["league"])["contracts"]
    root = ROOT / "docs/d06_player_model_study_evidence"
    out = evaluate_archived_count_offer(
        root, artifact_index_sha256=digest((root / "artifacts.json").read_bytes()), metric_key=metric,
        player_id="unmapped-probe", team_id="unmapped-probe", feed_body=body, feed_evidence=evidence,
        contract_id=rows[0]["contract_id"] if rows else "absent", as_of=evidence["fetched_at"],
    )
    assert out["payoff"] is None and out["reasons"] and not out["publishable"]
    if name.startswith("wnba"):
        assert "study_as_of_after_evaluation" in out["reasons"]
        assert "missing_source_bound_identity_resolution" in out["reasons"]
