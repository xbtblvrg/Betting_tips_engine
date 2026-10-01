"""Synthetic scenarios unless explicitly loading the supplied public archive."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.live_shadow import (
    LiveEvidenceError, ModelEvidenceStore, assess_live_shadow, contract_digest,
    inspect_nhl_observation, snapshot_digest,
)


NOW = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)


def at(milliseconds=0):
    return (NOW + timedelta(milliseconds=milliseconds)).isoformat()


def scenario(league="NBA"):
    state = {
        "schema": "live-shadow-state-v1", "league": league,
        "sports_event_id": "test-sports-1", "fixture_id": "test-stake-1",
        "home_id": "test-home", "away_id": "test-away",
        "mapping_reference": "synthetic-team-and-start-match",
        "status": "in_play", "score": [40, 39], "possession": "test-home",
        "clock": {"phase": "REG", "period": 2, "remaining_ms": 100000, "running": True},
        "source_type": "independent_sports", "source_sha256": "a" * 64,
        "source_url": "https://example.invalid/synthetic-sports",
        "source_updated_at": at(-900), "received_at": at(-800),
        **{key + "_observed_at": at(-1000) for key in ("state", "score", "clock", "possession")},
    }
    if league in ("NFL", "NCAAF"):
        state["football"] = {"down": 2, "yards_to_go": 8, "yards_to_goal": 45}
    if league == "MLB":
        state.update(clock=None, score=[1, 2], possession="test-away",
                     baseball={"inning": 3, "half": "top", "outs": 1, "balls": 2,
                               "strikes": 1, "occupied_bases": [True, False, False]})
    quote = {
        "league": league, "sports_event_id": "test-sports-1", "fixture_id": "test-stake-1",
        "fixture_slug": "synthetic-fixture",
        "market_id": "test-market", "outcome_id": "test-outcome",
        "market": "Total (Incl. Overtime)", "outcome": "Over 180.5",
        "scope": "full_game_including_overtime", "settlement_rule": "synthetic-rule-v1",
        "specifiers": "total=180.5", "line": 180.5, "participant_id": None,
        "tabs": [{"id": "test-tab", "name": "main"}], "provider_section": "standard",
        "in_play": True, "fixture_status": "active", "market_status": "active",
        "outcome_status": "active", "odds": 1.95, "source_sha256": "b" * 64,
        "source_url": "https://odds-data.stake.com/odds/synthetic-fixture",
        "source_updated_at": at(-400), "received_at": at(-300),
    }
    forecast = {
        "model_kind": "state_conditioned_in_play", "training_target": "independently_observed_live_state",
        "sports_inputs_only": True, "model_sha256": "c" * 64,
        "training_inputs_sha256": "d" * 64, "training_code_sha256": "e" * 64,
        "training_reference": "synthetic-shadow-study-not-approval",
        "state_sha256": snapshot_digest(state), "contract_sha256": contract_digest(quote),
        "generated_at": at(-600),
    }
    store = synthetic_model(_EVIDENCE_ROOT / league, state, quote, forecast)
    return dict(model_evidence=store, state=state, forecast=forecast, reviewed_quote=deepcopy(quote), current_quote=quote,
                as_of=at(), limits={"state_age_ms": 5000, "quote_age_ms": 5000, "execution_latency_ms": 5000},
                latency={"manual_delay_ms": 1000, "acceptance_delay_ms": 500,
                         "clock_uncertainty_ms": 100, "measurement_reference": "synthetic-delay-study"})


_EVIDENCE_ROOT = None


@pytest.fixture(autouse=True)
def evidence_directory(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "_EVIDENCE_ROOT", tmp_path)


def synthetic_model(root, state, quote, forecast):
    """Toy provenance fixture, not a fitted or approved sports model."""
    root.mkdir(parents=True, exist_ok=True)
    historical = deepcopy(state)
    for key in list(historical):
        if key.endswith("_at"):
            historical[key] = at(-172800000)
    inputs = {"schema": "live-shadow-training-inputs-v1", "league": state["league"],
              "odds_used": False, "observations": [{
                  "state": historical, "state_available_at": at(-172800000),
                  "prediction_at": at(-172799000), "target_available_at": at(-160000000),
                  "target_score": [80, 79], "target_source_sha256": "a" * 64}]}

    def write(name, value):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        (root / name).write_bytes(body)
        return hashlib.sha256(body).hexdigest()

    input_hash = write("inputs.json", inputs)
    code_hash = write("training.py", b"# Synthetic provenance fixture; no fitted model.\n")
    metadata = {
        "league": state["league"], "version": "synthetic-live-1",
        "model_kind": "state_conditioned_in_play",
        "training_target": "independently_observed_live_state", "sports_inputs_only": True,
        "training_inputs_sha256": input_hash, "training_code_sha256": code_hash,
        "features": ["score", "clock", "possession"],
        "contracts": [{k: quote[k] for k in ("market", "scope", "settlement_rule")}],
    }
    report_hash = write("report.json", dict(metadata, trained_at=at(-86400000)))
    artifact_hash = write("model.json", {"schema": "live-shadow-model-v1",
                                        "report_sha256": report_hash, "provenance": metadata})
    row = {"league": state["league"], "version": metadata["version"],
           "artifact": "model.json", "artifact_sha256": artifact_hash,
           "report": "report.json", "report_sha256": report_hash,
           "training_inputs": "inputs.json", "training_code": "training.py"}
    pin = write("manifest.json", {"models": [row]})
    forecast.update(model_sha256=artifact_hash, training_inputs_sha256=input_hash,
                    training_code_sha256=code_hash, training_reference="report.json")
    return ModelEvidenceStore(root, pin)


def reject(args, reason):
    result = assess_live_shadow(**args)
    assert not result.shadow_eligible
    assert result.reasons == (reason,)
    assert result.production_eligible is False
    return result


@pytest.mark.parametrize("league", ["NBA", "WNBA", "NHL", "NFL", "MLB", "NCAAF", "NCAAM", "NCAAW"])
def test_valid_synthetic_state_only_grants_shadow_admission(league):
    args = scenario(league)
    original = deepcopy(args)
    result = assess_live_shadow(**args)
    assert result.shadow_eligible, result.reasons
    assert result.production_eligible is False
    assert "independent_state_conditioned_training_not_approved" in result.production_blockers
    assert result.projected_execution_at == at(1600)
    assert args == original


@pytest.mark.parametrize("kind", ["pregame", "full_game", None])
def test_pregame_forecast_cannot_be_used_for_live(kind):
    args = scenario()
    args["forecast"]["model_kind"] = kind
    reject(args, "pregame_or_untyped_live_model")


@pytest.mark.parametrize("component", ["state", "score", "clock", "possession"])
def test_refetch_does_not_make_stale_components_fresh(component):
    args = scenario()
    args["state"][component + "_observed_at"] = at(-5001)
    args["state"]["received_at"] = at(-700)
    reject(args, "stale_" + component)


def test_age_includes_manual_delay_acceptance_and_clock_uncertainty():
    args = scenario()
    args["limits"]["state_age_ms"] = 2600
    assert assess_live_shadow(**args).shadow_eligible
    args["latency"]["manual_delay_ms"] += 1
    reject(args, "stale_state")


def test_quote_can_expire_during_manual_action():
    args = scenario()
    args["limits"]["quote_age_ms"] = 2000
    assert assess_live_shadow(**args).shadow_eligible
    args["limits"]["quote_age_ms"] = 1999
    reject(args, "stale_quote")


def test_transport_and_processing_time_count_towards_latency():
    args = scenario()
    args["limits"]["execution_latency_ms"] = 2599
    reject(args, "end_to_end_latency_exceeded")
    args["limits"]["execution_latency_ms"] = 1599
    reject(args, "execution_latency_exceeded")


@pytest.mark.parametrize("field", ["fixture_status", "market_status", "outcome_status"])
def test_suspension_at_any_level_rejects(field):
    args = scenario()
    args["current_quote"][field] = "suspended"
    reject(args, "inactive_" + field)


@pytest.mark.parametrize("field,value", [
    ("line", -180.5), ("scope", "regulation"), ("outcome_id", "other"),
    ("market_id", "other"), ("fixture_id", "other"), ("sports_event_id", "other"),
    ("settlement_rule", "different-rule"), ("specifiers", "total=181.5"),
    ("participant_id", "other-team"), ("tabs", [{"id": "other", "name": "periods"}]),
])
def test_changed_exact_contract_requires_new_forecast_and_review(field, value):
    args = scenario()
    args["current_quote"][field] = value
    reject(args, "contract_changed")


def test_repricing_and_swish_are_not_current_approved_quotes():
    args = scenario()
    args["current_quote"]["odds"] = 1.96
    reject(args, "price_changed")
    args["current_quote"]["provider_section"] = "swish"
    reject(args, "unverified_quote_section")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.0, True, "1.95"])
def test_invalid_odds_rejected(value):
    args = scenario()
    args["current_quote"]["odds"] = value
    reject(args, "invalid_decimal_odds")


@pytest.mark.parametrize("key,value", [("score", [41, 39]), ("possession", "test-away"),
                                     ("clock", {"phase": "REG", "period": 2, "remaining_ms": 99000, "running": True})])
def test_score_clock_or_possession_change_invalidates_forecast(key, value):
    args = scenario()
    args["state"][key] = value
    reject(args, "forecast_state_changed")


@pytest.mark.parametrize("field,reason", [("state", "missing_state"), ("forecast", "missing_forecast"),
                                        ("current_quote", "missing_current_quote"), ("latency", "missing_latency")])
def test_absent_evidence_fails_closed(field, reason):
    args = scenario()
    args[field] = None
    reject(args, reason)


@pytest.mark.parametrize("key,value,reason", [
    ("score", None, "invalid_score"), ("score", [True, 0], "invalid_score"),
    ("clock", None, "missing_clock"), ("possession", None, "missing_or_invalid_possession"),
    ("possession", "unknown-team", "missing_or_invalid_possession"),
    ("status", "finished", "state_not_in_play"), ("status", "pregame", "state_not_in_play"),
    ("league", "NCAA_UNKNOWN", "unsupported_live_state_league"),
    ("source_type", "sportsbook", "non_independent_state"),
    ("source_updated_at", None, "missing_state_source_updated_at"),
    ("source_updated_at", at(100), "state_time_order"),
    ("received_at", at(100), "state_time_order"),
    ("clock_observed_at", None, "missing_clock_observed_at"),
    ("clock_observed_at", at(100), "state_observation_time_order"),
])
def test_missing_invalid_or_future_state(key, value, reason):
    args = scenario()
    args["state"][key] = value
    reject(args, reason)


def test_naive_time_and_unbounded_latency_rejected():
    args = scenario()
    args["as_of"] = "2026-09-29T20:00:00"
    reject(args, "naive_as_of")
    args["as_of"] = at()
    args["latency"]["manual_delay_ms"] = -1
    reject(args, "invalid_latency_manual_delay_ms")
    args["latency"]["manual_delay_ms"] = 1
    args["limits"]["quote_age_ms"] = 300001
    reject(args, "invalid_limit_quote_age_ms")


def test_no_training_attestation_or_cross_event_forecast():
    args = scenario()
    args["forecast"].pop("training_inputs_sha256")
    reject(args, "missing_training_inputs_sha256")
    args = scenario()
    args["forecast"]["contract_sha256"] = "f" * 64
    reject(args, "forecast_contract_mismatch")


def test_no_implicit_nba_clock_for_college_or_regulation_clock_for_overtime():
    args = scenario("NCAAM")
    args["state"]["clock"]["period"] = 3
    reject(args, "invalid_period")
    args = scenario("NHL")
    args["state"]["clock"]["phase"] = "SO"
    reject(args, "unsupported_live_phase")


def test_baseball_does_not_invent_clock_or_pitch_state():
    args = scenario("MLB")
    args["state"]["baseball"].pop("occupied_bases")
    reject(args, "missing_base_occupancy")
    args = scenario("MLB")
    args["state"]["possession"] = "test-home"
    reject(args, "batting_team_mismatch")


def test_later_evaluation_expires_previous_shadow_result():
    args = scenario()
    assert assess_live_shadow(**args).shadow_eligible
    args["as_of"] = at(5000)
    reject(args, "stale_state")


def test_running_clock_must_not_cross_period_boundary_before_manual_execution():
    args = scenario()
    args["state"]["clock"]["remaining_ms"] = 2600
    args["forecast"]["state_sha256"] = snapshot_digest(args["state"])
    reject(args, "clock_boundary_before_execution")
    args["state"]["clock"]["remaining_ms"] = 2601
    args["forecast"]["state_sha256"] = snapshot_digest(args["state"])
    assert assess_live_shadow(**args).shadow_eligible
    args["state"]["clock"].update(remaining_ms=1000, running=False)
    args["forecast"]["state_sha256"] = snapshot_digest(args["state"])
    assert assess_live_shadow(**args).shadow_eligible


def test_delayed_quote_response_cannot_replace_newer_review():
    args = scenario()
    args["current_quote"]["source_updated_at"] = at(-500)
    reject(args, "quote_version_regressed")


def test_quote_arriving_before_model_or_missing_source_time_rejects():
    args = scenario()
    args["reviewed_quote"]["received_at"] = at(-700)
    args["reviewed_quote"]["source_updated_at"] = at(-750)
    reject(args, "quote_review_time_order")
    args = scenario()
    args["current_quote"]["source_updated_at"] = None
    reject(args, "missing_quote_source_updated_at")


def test_state_event_cannot_be_mapped_to_another_contract():
    args = scenario()
    args["state"]["fixture_id"] = "another-event"
    args["forecast"]["state_sha256"] = snapshot_digest(args["state"])
    reject(args, "state_contract_identity_mismatch")


@pytest.mark.parametrize("url", ["https://stake.com.evil.invalid/odds/synthetic-fixture",
                                "http://odds-data.stake.com/odds/synthetic-fixture",
                                "https://odds-data.stake.com/odds/another-fixture"])
def test_quote_source_must_identify_exact_stake_fixture(url):
    args = scenario()
    args["current_quote"]["source_url"] = url
    reject(args, "invalid_stake_quote_source")


def test_actual_supplied_nhl_archive_is_historical_not_live_evidence():
    root = Path(__file__).resolve().parent / "fixtures/d12_origin/sports_source_evidence"
    manifest = json.loads((root / "manifest.json").read_bytes())
    sources = [s for s in manifest["sources"] if s.get("format") == "nhl_json"]
    assert sources
    for source in sources:
        body = (root / source["blob_path"]).read_bytes()
        inspected = inspect_nhl_observation(body, source)
        state = inspected["state"]
        raw = json.loads(body)
        assert state["score"] == [raw["homeTeam"]["score"], raw["awayTeam"]["score"]]
        assert state["clock"]["remaining_ms"] == raw["clock"]["secondsRemaining"] * 1000
        assert state["source_sha256"] == hashlib.sha256(body).hexdigest()
        assert state["received_at"] == source["fetched_at"]
        assert state["source_updated_at"] is None
        assert state["possession"] is None
        assert state["clock_observed_at"] is None
        assert inspected["adapter_live_verified"] is False
        args = scenario("NHL")
        args["state"] = state
        reject(args, "state_not_in_play")


def test_source_adapter_hash_and_unknown_possession_are_fail_closed():
    body = json.dumps({"id": 12, "gameState": "LIVE", "homeTeam": {"id": 1, "score": 2},
                       "awayTeam": {"id": 2, "score": 1},
                       "clock": {"secondsRemaining": 70, "running": True},
                       "periodDescriptor": {"number": 3, "periodType": "REG"},
                       "plays": [{"details": {"eventOwnerTeamId": 1}}]}).encode()
    with pytest.raises(LiveEvidenceError, match="source_hash_mismatch"):
        inspect_nhl_observation(body, {"source_sha256": "f" * 64})
    result = inspect_nhl_observation(body, {"source_sha256": hashlib.sha256(body).hexdigest()})
    assert result["state"]["possession"] is None
    assert result["state"]["source_updated_at"] is None
    assert result["state"]["received_at"] is None
    assert result["adapter_live_verified"] is False


def repin(args, *, refresh_report=False, refresh_artifact=False):
    """Synthetic registry change, never production approval."""
    root = args["model_evidence"].root
    manifest = json.loads((root / "manifest.json").read_bytes())
    row = manifest["models"][0]
    if refresh_report:
        row["report_sha256"] = hashlib.sha256((root / row["report"]).read_bytes()).hexdigest()
    if refresh_artifact:
        row["artifact_sha256"] = hashlib.sha256((root / row["artifact"]).read_bytes()).hexdigest()
        args["forecast"]["model_sha256"] = row["artifact_sha256"]
    (root / "manifest.json").write_text(json.dumps(manifest))
    args["model_evidence"] = ModelEvidenceStore(
        root, hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest())


def test_actual_pregame_artifact_cannot_be_laundered_with_live_declarations():
    args = scenario()
    root = Path(__file__).resolve().parent / "fixtures/d12_origin/model_evidence"
    body = (root / "manifest.json").read_bytes()
    nba = next(row for row in json.loads(body)["models"] if row["league"] == "NBA")
    report = json.loads((root / nba["report"]).read_bytes())
    args["model_evidence"] = ModelEvidenceStore(root, hashlib.sha256(body).hexdigest())
    args["forecast"].update(model_sha256=nba["artifact_sha256"],
                            training_reference=nba["report"],
                            training_code_sha256=report["code_sha256"])
    # Same genuine artifact/report used in the failed previous review, with
    # all of the deceptive live declarations left intact.
    reject(args, "pregame_or_untyped_model_evidence")


def test_model_hash_and_training_reference_alone_are_not_evidence():
    args = scenario()
    args.pop("model_evidence")
    reject(args, "missing_verified_model_evidence")


@pytest.mark.parametrize("filename", ["manifest.json", "model.json", "report.json", "inputs.json", "training.py"])
def test_all_referenced_bytes_are_reverified_on_every_evaluation(filename):
    args = scenario()
    assert assess_live_shadow(**args).shadow_eligible
    path = args["model_evidence"].root / filename
    path.write_bytes(path.read_bytes() + b" ")
    reject(args, "model_evidence_hash_mismatch")


@pytest.mark.parametrize("filename", ["model.json", "report.json", "inputs.json", "training.py"])
def test_missing_model_inputs_cannot_be_replaced_by_hash_strings(filename):
    args = scenario()
    (args["model_evidence"].root / filename).unlink()
    reject(args, "missing_model_evidence_file")


def test_unknown_model_duplicate_registry_and_wrong_training_reference():
    args = scenario()
    args["forecast"]["model_sha256"] = "f" * 64
    reject(args, "missing_or_ambiguous_model_provenance")
    args = scenario()
    root = args["model_evidence"].root
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["models"] *= 2
    (root / "manifest.json").write_text(json.dumps(manifest))
    repin(args)
    reject(args, "missing_or_ambiguous_model_provenance")
    args = scenario()
    args["forecast"]["training_reference"] = "another-report.json"
    reject(args, "training_reference_mismatch")


@pytest.mark.parametrize("field,value,reason", [
    ("model_kind", "pregame", "pregame_or_untyped_model_evidence"),
    ("training_target", "full_game_scores", "non_live_training_evidence"),
    ("league", "WNBA", "artifact_report_mismatch"),
])
def test_live_forecast_labels_cannot_override_registry_report(field, value, reason):
    args = scenario()
    path = args["model_evidence"].root / "report.json"
    report = json.loads(path.read_bytes())
    report[field] = value
    path.write_text(json.dumps(report))
    repin(args, refresh_report=True)
    reject(args, reason)


def test_artifact_provenance_must_match_the_verified_report():
    args = scenario()
    path = args["model_evidence"].root / "model.json"
    artifact = json.loads(path.read_bytes())
    artifact["provenance"]["model_kind"] = "pregame"
    path.write_text(json.dumps(artifact))
    repin(args, refresh_artifact=True)
    reject(args, "artifact_provenance_mismatch")


def test_verified_live_report_cannot_upgrade_an_opaque_pregame_artifact():
    args = scenario()
    path = args["model_evidence"].root / "model.json"
    path.write_bytes((Path(__file__).resolve().parent / "fixtures/d12_origin/model_evidence/NBA.joblib").read_bytes())
    repin(args, refresh_artifact=True)
    reject(args, "unsupported_model_evidence_format")


@pytest.mark.parametrize("key", ["training_inputs_sha256", "training_code_sha256"])
def test_forecast_cannot_substitute_unrelated_training_hash(key):
    args = scenario()
    args["forecast"][key] = "f" * 64
    reject(args, "forecast_model_evidence_mismatch")


def test_paths_cannot_escape_pinned_evidence_root(tmp_path):
    args = scenario()
    root = args["model_evidence"].root
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["models"][0]["artifact"] = "../external.json"
    (root / "manifest.json").write_text(json.dumps(manifest))
    repin(args)
    reject(args, "model_evidence_path_escape")
    manifest["models"][0]["artifact"] = "escape.json"
    (tmp_path / "external.json").write_bytes(b"{}")
    (root / "escape.json").symlink_to(tmp_path / "external.json")
    (root / "manifest.json").write_text(json.dumps(manifest))
    repin(args)
    reject(args, "model_evidence_path_escape")


def rewrite_training(args, mutate):
    root = args["model_evidence"].root
    inputs = json.loads((root / "inputs.json").read_bytes())
    mutate(inputs)
    (root / "inputs.json").write_text(json.dumps(inputs))
    digest = hashlib.sha256((root / "inputs.json").read_bytes()).hexdigest()
    report = json.loads((root / "report.json").read_bytes())
    report["training_inputs_sha256"] = digest
    (root / "report.json").write_text(json.dumps(report))
    artifact = json.loads((root / "model.json").read_bytes())
    artifact["provenance"]["training_inputs_sha256"] = digest
    artifact["report_sha256"] = hashlib.sha256((root / "report.json").read_bytes()).hexdigest()
    (root / "model.json").write_text(json.dumps(artifact))
    args["forecast"]["training_inputs_sha256"] = digest
    repin(args, refresh_report=True, refresh_artifact=True)


@pytest.mark.parametrize("field,value,reason", [
    ("state_available_at", None, "missing_training_state_available_at"),
    ("state_available_at", at(), "training_availability_order"),
    ("target_available_at", at(), "training_availability_order"),
    ("target_score", None, "missing_observed_training_target"),
])
def test_training_availability_and_targets_must_exist_in_hashed_inputs(field, value, reason):
    args = scenario()
    rewrite_training(args, lambda inputs: inputs["observations"][0].update({field: value}))
    reject(args, reason)


def test_pregame_rows_and_empty_training_archive_reject():
    args = scenario()
    rewrite_training(args, lambda inputs: inputs["observations"][0]["state"].update(status="pregame"))
    reject(args, "state_not_in_play")
    args = scenario()
    rewrite_training(args, lambda inputs: inputs.update(observations=[]))
    reject(args, "missing_live_training_observations")


@pytest.mark.parametrize("league,period,duration,reason", [
    ("NCAAM", 2, 1200000, None), ("NCAAM", 3, 100, "invalid_period"),
    ("NCAAW", 4, 600000, None), ("NCAAW", 2, 600001, "invalid_clock"),
    ("NCAAB", 2, 100, "unsupported_live_state_league"),
])
def test_ncaa_identifiers_and_clock_rules_match_feed(league, period, duration, reason):
    args = scenario(league)
    args["state"]["clock"].update(period=period, remaining_ms=duration)
    args["forecast"]["state_sha256"] = snapshot_digest(args["state"])
    if reason:
        reject(args, reason)
    else:
        assert assess_live_shadow(**args).shadow_eligible


@pytest.mark.parametrize("market,outcome,line,specifiers", [
    ("Winner (Incl. Overtime)", "test-home", None, ""),
    ("Handicap (Incl. Overtime)", "test-home -2.5", -2.5, "hcp=-2.5"),
    ("Total (Incl. Overtime)", "Over 180.5", 180.5, "total=180.5"),
])
def test_core_market_identity_survives_shadow_evaluation(market, outcome, line, specifiers):
    args = scenario()
    args["current_quote"].update(market=market, outcome=outcome, line=line, specifiers=specifiers)
    args["reviewed_quote"] = deepcopy(args["current_quote"])
    args["forecast"]["contract_sha256"] = contract_digest(args["current_quote"])
    args["model_evidence"] = synthetic_model(_EVIDENCE_ROOT / "core", args["state"],
                                              args["current_quote"], args["forecast"])
    assert assess_live_shadow(**args).shadow_eligible
    assert args["current_quote"]["line"] == line


def test_relabelled_contract_requires_its_own_training_scope():
    args = scenario()
    args["current_quote"].update(market="1st Quarter - Total", scope="quarter_1")
    args["reviewed_quote"] = deepcopy(args["current_quote"])
    args["forecast"]["contract_sha256"] = contract_digest(args["current_quote"])
    reject(args, "untrained_live_contract")
