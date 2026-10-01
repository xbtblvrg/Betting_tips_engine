"""Synthetic boundary cases plus an unchanged supplied public observation."""
import copy
import json
import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from betting_engine.closing_prices import evaluate_closing_price, snapshots_from_response


START = "2026-09-29T20:00:00+00:00"
CUTOFF = "2026-09-29T21:00:00+00:00"


def decision():
    return {"family": "contract:issued", "tip_id": "issued",
            "issued_at": "2026-09-29T19:00:00+00:00", "status": "lost", "pnl": -2,
            "payload": {"id": "issued", "game_id": "NBA:synthetic", "league": "NBA",
                        "home": "Home", "away": "Away", "start": START,
                        "market": "totals", "model_scope": "full_game", "side": "over", "line": 210.5,
                        "reference_platform": "Stake.com", "reference_odds": 2.1,
                        "reference_market_id": "stake:fixture:market:outcome",
                        "stake": 2, "model_version": "frozen-test-v1", "probability": .55,
                        "stake_evidence": {"fixture_slug": "synthetic-fixture",
                                           "market_name": "Total (Incl. Overtime)", "outcome_name": "Over 210.5"}}}


def snapshot(odds=2., at="2026-09-29T19:59:00+00:00"):
    outcomes = [{"id": "outcome", "name": "Over 210.5", "active": True, "odds": odds}]
    return {"league": "NBA", "fixture_id": "fixture", "fixture_slug": "synthetic-fixture",
            "start": START, "market_id": "market", "market": "Total (Incl. Overtime)",
            "scope": "full_game", "provider_section": "standard", "status": "active",
            "specifiers": "total=210.5", "outcomes": outcomes,
            "raw_fixture": {"id": "fixture", "slug": "synthetic-fixture",
                            "startTime": datetime.fromisoformat(START).timestamp() * 1000,
                            "status": "active", "enabled": True, "preMatchEnabled": True,
                            "blacklisted": False},
            "raw_contract": {"id": "market", "name": "Total (Incl. Overtime)", "status": "active",
                             "specifiers": "total=210.5", "outcomes": copy.deepcopy(outcomes)},
            "source": {"url": "https://odds-data.stake.com/odds/synthetic-fixture",
                       "fetched_at": at, "sha256": "a" * 64}}


def synchronize_raw(row):
    for field, target in (("market_id", "id"), ("market", "name"), ("outcomes", "outcomes"),
                          ("status", "status"), ("specifiers", "specifiers")):
        row["raw_contract"][target] = copy.deepcopy(row[field])
    return row


def evaluate(rows, d=None, cutoff=CUTOFF):
    return evaluate_closing_price(d or decision(), rows, as_of=cutoff)


@pytest.mark.parametrize("close,expected", [(2., .05), (2.1, 0.), (2.5, -.16)])
def test_price_clv_uses_original_odds_including_losing_decisions(close, expected):
    d, rows = decision(), [snapshot(close)]
    before = copy.deepcopy((d, rows))
    result = evaluate(rows, d)
    assert result["status"] == "comparable"
    assert result["price_clv"] == pytest.approx(expected)
    assert result["closing_price"]["source_publication_at"] is None
    assert result["closing_price"]["source_updated_at"] is None
    assert not result["account_execution_verified"]
    assert (d, rows) == before


def test_latest_time_not_best_price_and_input_order_does_not_matter():
    rows = [snapshot(1.5, "2026-09-29T19:58:00Z"), snapshot(2.5)]
    assert evaluate(rows)["price_clv"] == pytest.approx(-.16)
    assert evaluate(rows)["comparison_sha256"] == evaluate(rows[::-1])["comparison_sha256"]


def test_settlement_changes_do_not_rewrite_frozen_price_comparison():
    d = decision()
    original = evaluate([snapshot()], d)
    d.update(status="won", pnl=2.2, outcome_evidence={"source": "test"})
    assert evaluate([snapshot()], d)["comparison_sha256"] == original["comparison_sha256"]


@pytest.mark.parametrize("league,suffix", [("WNBA", "Overtime"), ("NFL", "Overtime"),
    ("NHL", "Overtime and Penalties"), ("MLB", "Extra Innings"),
    ("NCAAF", "Overtime"), ("NCAAM", "Overtime"), ("NCAAW", "Overtime")])
def test_league_specific_settlement_titles(league, suffix):
    d, row = decision(), snapshot()
    title = f"Total (Incl. {suffix})"
    d["payload"].update(league=league)
    d["payload"]["stake_evidence"]["market_name"] = title
    row.update(league=league, market=title)
    assert evaluate([synchronize_raw(row)], d)["status"] == "comparable"
    row["market"] = "Total"
    assert evaluate([synchronize_raw(row)], d)["status"] == "missing_closing_price"


@pytest.mark.parametrize("field,value,reason", [
    ("fixture_id", "another", "event_mismatch"),
    ("fixture_slug", "another", "event_mismatch"),
    ("league", "WNBA", "event_mismatch"),
    ("scope", "half_1", "market_or_scope_mismatch"),
    ("market_id", "another", "market_or_scope_mismatch"),
    ("market", "1st Half - Total", "market_or_scope_mismatch"),
    ("start", "2026-09-29T20:01:00Z", "start_mismatch"),
    ("provider_section", "swishMarkets.playerProps", "unverified_provider_section"),
    ("specifiers", "total=211.5", "line_mismatch"),
    ("status", "suspended", "inactive_or_suspended"),
    ("fixture_status", "live", "inactive_or_suspended"),
])
def test_exact_contract_and_activity_rejections(field, value, reason):
    row = snapshot()
    row[field] = value
    result = evaluate([synchronize_raw(row)])
    assert result["status"] == "missing_closing_price"
    assert result["price_clv"] is None
    assert result["rejection_counts"] == {reason: 1}


@pytest.mark.parametrize("field,value,reason", [
    ("id", "another", "missing_or_ambiguous_outcome"),
    ("name", "Under 210.5", "side_or_line_mismatch"),
    ("name", "Over 211.5", "side_or_line_mismatch"),
    ("active", False, "inactive_or_suspended"),
    ("status", "suspended", "inactive_or_suspended"),
    ("odds", float("nan"), "invalid_closing_odds"),
    ("odds", float("inf"), "invalid_closing_odds"),
    ("odds", 1, "invalid_closing_odds"),
    ("odds", True, "invalid_closing_odds"),
    ("odds", "2.0", "invalid_closing_odds"),
])
def test_outcome_rejections(field, value, reason):
    row = snapshot()
    row["outcomes"][0][field] = value
    result = evaluate([synchronize_raw(row)])
    assert result["status"] == "missing_closing_price"
    assert result["rejection_counts"] == {reason: 1}
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("at,reason", [
    ("2026-09-29T19:54:59.999999Z", "outside_closing_window"),
    ("2026-09-29T18:59:59Z", "before_issuance"),
    (START, "at_or_after_start"),
    ("2026-09-29T20:00:01Z", "at_or_after_start"),
    ("2026-09-29T21:00:01Z", "after_frozen_cutoff"),
    ("2026-09-29T19:59:00", "timezone_required"),
    (None, "missing_timestamp"),
])
def test_time_boundaries(at, reason):
    result = evaluate([snapshot(at=at)])
    assert result["status"] == "missing_closing_price"
    assert result["rejection_counts"] == {reason: 1}


def test_close_window_inclusive_lower_and_exclusive_upper_with_timezone_conversion():
    assert evaluate([snapshot(at="2026-09-29T21:55:00+02:00")])["status"] == "comparable"
    assert evaluate([snapshot(at="2026-09-29T19:59:59.999999Z")])["status"] == "comparable"
    assert evaluate([snapshot()], cutoff=START)["status"] == "comparable"
    assert evaluate([snapshot()], cutoff="2026-09-29T19:59:59Z")["status"] == "pending_start"


def test_future_snapshots_cannot_change_a_frozen_comparison():
    before = evaluate([snapshot()])
    after = evaluate([snapshot(), snapshot(9., "2026-09-29T22:00:00Z")])
    assert before["comparison_sha256"] == after["comparison_sha256"]
    assert after["rejection_counts"] == {"after_frozen_cutoff": 1}


def test_missing_is_separate_from_zero_clv():
    missing = evaluate([])
    zero = evaluate([snapshot(2.1)])
    assert missing["status"] == "missing_closing_price" and missing["price_clv"] is None
    assert zero["status"] == "comparable" and zero["price_clv"] == 0


@pytest.mark.parametrize("invalid", ["suspended", "invalid_odds"])
def test_latest_unavailable_observation_blocks_fallback(invalid):
    last = snapshot()
    if invalid == "suspended":
        last["status"] = "suspended"
    else:
        last["outcomes"][0]["odds"] = float("nan")
    result = evaluate([snapshot(at="2026-09-29T19:58:00Z"), synchronize_raw(last)])
    assert result["status"] == "missing_closing_price"
    assert result["reason"].startswith("latest_")


def test_simultaneous_conflicts_are_missing_not_arbitrary_and_duplicates_are_harmless():
    assert evaluate([snapshot(), snapshot(1.8)])["reason"] == "conflicting_latest_snapshots"
    assert evaluate([snapshot(), snapshot()])["comparison_sha256"] == evaluate([snapshot()])["comparison_sha256"]


def test_raw_contract_disagreement_and_duplicate_outcomes_are_rejected():
    row = snapshot()
    row["raw_contract"]["outcomes"][0]["odds"] = 99
    assert evaluate([row])["rejection_counts"] == {"raw_contract_mismatch": 1}
    row = snapshot()
    row["outcomes"] *= 2
    assert evaluate([synchronize_raw(row)])["rejection_counts"] == {"missing_or_ambiguous_outcome": 1}


def test_source_updated_at_is_preserved_never_substituted_for_fetch_time():
    row = snapshot()
    row["raw_contract"]["updatedAt"] = 1790711880000  # 2026-09-29 19:58 UTC
    result = evaluate([row])
    assert result["closing_price"]["source_updated_at"] == "2026-09-29T19:58:00+00:00"
    row["raw_contract"]["updatedAt"] += 120000
    assert evaluate([row])["rejection_counts"] == {"source_update_after_observation": 1}
    del row["source"]["fetched_at"]
    assert evaluate([row])["status"] == "missing_closing_price"


@pytest.mark.parametrize("field,value", [("sha256", ""), ("url", "https://example.com/odds")])
def test_missing_source_provenance(field, value):
    row = snapshot()
    row["source"][field] = value
    assert evaluate([row])["status"] == "missing_closing_price"


@pytest.mark.parametrize("field,value", [("side", "under"), ("line", -210.5),
    ("model_scope", "half_1"), ("market", "half_1:totals"),
    ("reference_odds", float("nan")), ("reference_odds", True), ("reference_market_id", "missing")])
def test_invalid_issued_contract_is_not_missing_close(field, value):
    d = decision()
    d["payload"][field] = value
    assert evaluate([snapshot()], d)["status"] == "invalid_decision"


def test_signed_handicap_and_inning_scopes_are_distinct():
    d, row = decision(), snapshot()
    d["payload"].update(market="spreads", side="away", line=1.5)
    d["payload"]["stake_evidence"].update(market_name="Handicap (Incl. Overtime)", outcome_name="Away (1.5)")
    row.update(market="Handicap (Incl. Overtime)", specifiers="hcp=-1.5")
    row["outcomes"][0]["name"] = "Away (1.5)"
    assert evaluate([synchronize_raw(row)], d)["status"] == "comparable"
    row["specifiers"] = "hcp=-2.5"
    assert evaluate([synchronize_raw(row)], d)["rejection_counts"] == {"line_mismatch": 1}
    row["specifiers"] = "hcp=-1.5"
    row["outcomes"][0]["name"] = "Away (-1.5)"
    assert evaluate([synchronize_raw(row)], d)["status"] == "missing_closing_price"
    d, row = decision(), snapshot()
    d["payload"].update(league="MLB", market="innings_1_5:totals", model_scope="innings_1_5", line=4.5)
    d["payload"]["stake_evidence"].update(market_name="Innings 1 to 5 - Total", outcome_name="Over 4.5")
    row.update(league="MLB", market="Innings 1 to 5 - Total", scope="innings_1_5", specifiers="total=4.5")
    row["outcomes"][0]["name"] = "Over 4.5"
    assert evaluate([synchronize_raw(row)], d)["status"] == "comparable"
    row["scope"] = "inning_1"
    assert evaluate([row], d)["status"] == "missing_closing_price"


def test_actual_supplied_catalogue_schema_does_not_invent_a_closing_quote():
    row = json.loads((Path(__file__).parent / "fixtures/closing_catalog_observation.json").read_text())
    d = decision()  # Synthetic issued decision, NOT an actual issuance claim.
    outcome = row["outcomes"][0]
    d["issued_at"] = "2026-09-29T14:00:00+00:00"
    d["payload"].update(home="Detroit Pistons", away="Boston Celtics", start=row["start"],
                        market="moneyline", side="home", line=None,
                        reference_market_id=f"stake:{row['fixture_id']}:{row['market_id']}:{outcome['id']}")
    d["payload"]["stake_evidence"].update(fixture_slug=row["fixture_slug"], market_name=row["market"], outcome_name=outcome["name"])
    result = evaluate([row], d, cutoff=row["start"])
    assert result["status"] == "missing_closing_price"
    assert result["rejection_counts"] == {"outside_closing_window": 1}


def test_invalid_cutoff_is_an_api_error():
    with pytest.raises(ValueError, match="timezone_required"):
        evaluate([], cutoff="2026-09-29T21:00:00")


@pytest.mark.parametrize("outcomes", [[], None, "omitted"])
def test_market_suspension_without_outcomes_blocks_earlier_price(outcomes):
    early, late = snapshot(at="2026-09-29T19:58:00Z"), snapshot()
    late["status"] = late["raw_contract"]["status"] = "suspended"
    if outcomes == "omitted":
        del late["outcomes"], late["raw_contract"]["outcomes"]
    else:
        late["outcomes"] = late["raw_contract"]["outcomes"] = outcomes
    for rows in ([early, late], [late, early]):
        result = evaluate(rows)
        assert result["status"] == "missing_closing_price"
        assert result["reason"] == "latest_inactive_or_suspended"
        assert result["rejection_counts"] == {"inactive_or_suspended": 1}
        assert result["price_clv"] is None


def test_active_market_losing_selected_outcome_does_not_resurrect_old_quote():
    late = snapshot()
    late["outcomes"] = []
    result = evaluate([snapshot(at="2026-09-29T19:58:00Z"), synchronize_raw(late)])
    assert result["reason"] == "latest_missing_or_ambiguous_outcome"


@pytest.mark.parametrize("field", ["raw_fixture", "status", "enabled", "preMatchEnabled", "blacklisted"])
def test_missing_fixture_lifecycle_is_not_assumed_active_or_backfilled(field):
    row = snapshot()
    if field == "raw_fixture":
        del row[field]
        row["fixture_status"] = "active"  # An unsupported annotation is insufficient.
    else:
        del row["raw_fixture"][field]
    result = evaluate([snapshot(at="2026-09-29T19:58:00Z"), row])
    assert result["reason"] == "latest_missing_fixture_lifecycle"
    assert result["status"] == "missing_closing_price"


@pytest.mark.parametrize("field,value,reason", [
    ("status", "suspended", "inactive_or_suspended"),
    ("status", "live", "inactive_or_suspended"),
    ("enabled", False, "inactive_or_suspended"),
    ("preMatchEnabled", False, "inactive_or_suspended"),
    ("blacklisted", True, "inactive_or_suspended"),
    ("enabled", 1, "inactive_or_suspended"),
    ("id", "another", "fixture_evidence_identity_mismatch"),
    ("slug", "another", "fixture_evidence_identity_mismatch"),
    ("startTime", 0, "fixture_evidence_start_mismatch"),
    ("startTime", None, "missing_fixture_start"),
])
def test_fixture_evidence_must_validate_even_when_market_and_outcome_active(field, value, reason):
    row = snapshot()
    row["raw_fixture"][field] = value
    result = evaluate([row])
    assert result["status"] == "missing_closing_price"
    assert result["rejection_counts"] == {reason: 1}


def test_fixture_suspension_with_no_outcomes_blocks_fallback_and_can_reopen():
    blocked = snapshot(at="2026-09-29T19:58:30Z")
    blocked["raw_fixture"]["status"] = "suspended"
    blocked["outcomes"] = blocked["raw_contract"]["outcomes"] = []
    early = snapshot(at="2026-09-29T19:58:00Z")
    assert evaluate([early, blocked])["reason"] == "latest_inactive_or_suspended"
    assert evaluate([early, blocked, snapshot()])["status"] == "comparable"


def test_observed_early_live_start_cannot_be_undone_by_later_active_flags():
    live = snapshot(at="2026-09-29T19:54:00Z")
    live["raw_fixture"]["status"] = "live"
    live["market_id"] = "other-market"
    for rows in ([live, snapshot()], [snapshot(), live]):
        assert evaluate(rows)["reason"] == "fixture_started_before_scheduled_start"
    del live["raw_fixture"]["blacklisted"]
    assert evaluate([live, snapshot()])["reason"] == "fixture_started_before_scheduled_start"


def test_post_cutoff_lifecycle_observation_does_not_change_frozen_comparison():
    future = snapshot(at="2026-09-29T22:00:00Z")
    future["raw_fixture"]["status"] = "live"
    assert evaluate([snapshot(), future])["comparison_sha256"] == evaluate([snapshot()])["comparison_sha256"]


def response_fixture(row=None):
    """Synthetic response with the actual nested Stake groups/fixture schema."""
    row = row or snapshot()
    payload = {"fixture": row["raw_fixture"],
               "groups": [{"id": "main", "name": "main", "markets": [[row["raw_contract"]]]}]}
    body = json.dumps(payload).encode()
    source = {**row["source"], "sha256": hashlib.sha256(body).hexdigest()}
    return body, source


@pytest.mark.parametrize("endpoint", ["odds", "fixtures"])
def test_response_adapter_keeps_lifecycle_and_quote_from_same_hashed_body(endpoint):
    body, source = response_fixture()
    source["url"] = source["url"].replace("/odds/", f"/{endpoint}/")
    rows = snapshots_from_response(body, source, league="NBA")
    assert evaluate(rows)["price_clv"] == pytest.approx(.05)
    assert rows[0]["source"]["sha256"] == hashlib.sha256(body).hexdigest()
    rows[0]["source"]["fetched_at"] = "changed"
    assert source["fetched_at"] == "2026-09-29T19:59:00+00:00"


def test_response_adapter_preserves_suspensions_and_rejects_altered_bytes():
    row = snapshot()
    row["status"] = "suspended"
    row["outcomes"] = []
    body, source = response_fixture(synchronize_raw(row))
    rows = snapshots_from_response(body, source, league="NBA")
    assert evaluate([snapshot(at="2026-09-29T19:58:00Z"), *rows])["reason"] == "latest_inactive_or_suspended"
    with pytest.raises(ValueError, match="response_hash_mismatch"):
        snapshots_from_response(body + b" ", source, league="NBA")


def test_response_adapter_does_not_substitute_schedule_or_catalogue_times():
    body, source = response_fixture()
    del source["fetched_at"]
    source["checked_at"] = CUTOFF
    with pytest.raises(KeyError):
        snapshots_from_response(body, source, league="NBA")
    row = snapshot()
    row["raw_fixture"]["date"] = row["raw_fixture"].pop("startTime")
    body, source = response_fixture(row)
    with pytest.raises(KeyError):
        snapshots_from_response(body, source, league="NBA")


def test_catalogue_schema_without_lifecycle_stays_unverified_even_inside_window():
    # Synthetic time-shift to isolate the schema gap; NOT a real capture.
    row = snapshot()
    del row["raw_fixture"]
    assert evaluate([row])["rejection_counts"] == {"missing_fixture_lifecycle": 1}


@pytest.mark.parametrize("invalid", [None, [], "active"])
def test_malformed_lifecycle_does_not_allow_fallback(invalid):
    row = snapshot()
    row["raw_fixture"] = invalid
    result = evaluate([snapshot(at="2026-09-29T19:58:00Z"), row])
    assert result["reason"] == "latest_missing_fixture_lifecycle"


def test_fixture_suspension_with_entirely_empty_market_response_is_retained():
    body, source = response_fixture()
    payload = json.loads(body)
    payload["groups"] = []
    payload["fixture"]["status"] = "suspended"
    body = json.dumps(payload).encode()
    source["sha256"] = hashlib.sha256(body).hexdigest()
    rows = snapshots_from_response(body, source, league="NBA")
    assert len(rows) == 1
    assert evaluate([snapshot(at="2026-09-29T19:58:00Z"), *rows])["reason"] == "latest_inactive_or_suspended"


def test_fixture_suspension_on_another_market_blocks_older_exact_quote():
    late = snapshot()
    late["market_id"] = "other"
    late["raw_fixture"]["status"] = "suspended"
    assert evaluate([snapshot(at="2026-09-29T19:58:00Z"), late])["reason"] == "latest_inactive_or_suspended"
    reopened = snapshot(at="2026-09-29T19:59:30Z")
    reopened["market_id"] = "other"
    assert evaluate([snapshot(at="2026-09-29T19:58:00Z"), late, reopened])["reason"] == "latest_inactive_or_suspended"
    assert evaluate([late, snapshot(at="2026-09-29T19:59:30Z")])["status"] == "comparable"


def test_early_live_evidence_before_issuance_still_blocks_nominal_close():
    live = snapshot(at="2026-09-29T18:59:00Z")
    live["raw_fixture"]["status"] = "live"
    result = evaluate([live, snapshot()])
    assert result["reason"] == "fixture_started_before_scheduled_start"
    assert result["price_clv"] is None
    assert result["rejection_counts"] == {"before_issuance": 1}


@pytest.mark.parametrize("void_factor", [.5, 1, -1, None, True])
def test_nonstandard_void_payoff_cannot_be_used_as_standard_price(void_factor):
    late = snapshot()
    late["outcomes"][0]["voidFactor"] = void_factor
    result = evaluate([snapshot(at="2026-09-29T19:58:00Z"), synchronize_raw(late)])
    assert result["status"] == "missing_closing_price"
    assert result["price_clv"] is None


def adapted(payload, source):
    body = json.dumps(payload).encode()
    return snapshots_from_response(body, {**source, "sha256": hashlib.sha256(body).hexdigest()}, league="NBA")


@pytest.mark.parametrize("conflict", ["price", "suspension"])
def test_adapter_preserves_conflicting_market_copies_across_tabs(conflict):
    body, source = response_fixture()
    payload = json.loads(body)
    other = copy.deepcopy(payload["groups"][0])
    other["id"] = "other-tab"
    market = other["markets"][0][0]
    if conflict == "price":
        market["outcomes"][0]["odds"] = 3
    else:
        market["status"] = "suspended"
        market["outcomes"] = []
    payload["groups"].append(other)
    for groups in (payload["groups"], list(reversed(payload["groups"]))):
        rows = adapted({**payload, "groups": groups}, source)
        result = evaluate(rows)
        assert result["status"] == "missing_closing_price"
        assert result["reason"] == "conflicting_latest_snapshots"


@pytest.mark.parametrize("remaining_market", [False, True])
def test_complete_response_missing_exact_market_blocks_old_quote_until_reobserved(remaining_market):
    body, source = response_fixture()
    payload = json.loads(body)
    if remaining_market:
        payload["groups"][0]["markets"][0][0]["id"] = "other-market"
    else:
        payload["groups"] = []
    early = snapshot(at="2026-09-29T19:58:00Z")
    rows = adapted(payload, source)
    result = evaluate([early, *rows])
    assert result["reason"] == "latest_exact_market_absent_from_response"
    assert result["price_clv"] is None
    assert evaluate([early, *rows, snapshot(at="2026-09-29T19:59:30Z")])["status"] == "comparable"


def test_adapter_identical_market_copies_do_not_create_ambiguity():
    body, source = response_fixture()
    payload = json.loads(body)
    payload["groups"].append(copy.deepcopy(payload["groups"][0]))
    assert evaluate(adapted(payload, source))["price_clv"] == pytest.approx(.05)
