"""D12 experimental live-state admission; pure functions, no execution authority.

Inputs are an internal contract, not a claimed live vendor schema. Model evidence
is byte-verified against a separately pinned local registry, never training approval.
See docs/D12_LIVE_SHADOW.md for the boundary and source limitations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


class LiveEvidenceError(ValueError):
    """Stable fail-closed reason."""


def _require(condition, reason):
    if not condition:
        raise LiveEvidenceError(reason)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _integer(value, lower=0, upper=None):
    return type(value) is int and value >= lower and (upper is None or value <= upper)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _mapping(value, name):
    _require(isinstance(value, Mapping), "missing_" + name)
    return value


def _time(value, name):
    _require(isinstance(value, str), "missing_" + name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveEvidenceError("invalid_" + name) from exc
    _require(parsed.tzinfo is not None, "naive_" + name)
    return parsed.astimezone(timezone.utc)


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def snapshot_digest(value):
    """Bind the complete normalized snapshot, including observation times."""
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError) as exc:
        raise LiveEvidenceError("invalid_snapshot") from exc


IDENTITY = ("league", "sports_event_id", "fixture_id", "fixture_slug", "market_id", "outcome_id",
            "market", "outcome", "scope", "settlement_rule", "specifiers", "line",
            "participant_id", "tabs", "provider_section")


def contract_digest(quote):
    """Odds excluded from identity, but explicitly compared during admission."""
    _mapping(quote, "quote")
    _require(all(k in quote for k in IDENTITY), "incomplete_contract")
    for key in IDENTITY:
        if key not in ("specifiers", "line", "participant_id", "tabs"):
            _require(_text(quote[key]), "invalid_contract_" + key)
    _require(isinstance(quote["specifiers"], str), "invalid_contract_specifiers")
    _require(quote["line"] is None or _number(quote["line"]), "invalid_contract_line")
    _require(quote["participant_id"] is None or _text(quote["participant_id"]),
             "invalid_contract_participant_id")
    tabs = quote["tabs"]
    _require(isinstance(tabs, list) and bool(tabs) and
             all(isinstance(t, dict) and _text(t.get("id")) and _text(t.get("name")) for t in tabs),
             "missing_observed_tabs")
    return snapshot_digest({k: quote[k] for k in IDENTITY})


@dataclass(frozen=True)
class ShadowVerdict:
    shadow_eligible: bool
    reasons: tuple[str, ...]
    state_sha256: str | None = None
    quote_sha256: str | None = None
    projected_execution_at: str | None = None

    @property
    def production_eligible(self):
        return False

    @property
    def production_blockers(self):
        return ("shadow_only", "independent_state_conditioned_training_not_approved",
                "live_contract_and_settlement_integration_not_approved")


@dataclass(frozen=True)
class ModelEvidenceStore:
    """Supervisor-selected, read-only evidence root and independently pinned manifest.

    Never construct this from a forecast or an external paper. The pin establishes
    the local evidence trust boundary, not scientific approval. No pickle/joblib
    is deserialized. Files are rehashed on every assessment to detect replacement.
    """

    root: Path
    manifest_sha256: str

    def read(self, relative, expected_hash):
        _require(_text(relative) and _hash(expected_hash), "invalid_model_evidence_reference")
        root = Path(self.root).resolve()
        path = (root / relative).resolve()
        _require(not Path(relative).is_absolute() and path.is_relative_to(root),
                 "model_evidence_path_escape")
        try:
            with path.open("rb") as stream:
                body = stream.read(128 * 1024 * 1024 + 1)
        except OSError as exc:
            raise LiveEvidenceError("missing_model_evidence_file") from exc
        _require(len(body) <= 128 * 1024 * 1024, "oversized_model_evidence")
        _require(hashlib.sha256(body).hexdigest() == expected_hash, "model_evidence_hash_mismatch")
        return body


def _json_object(body):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise LiveEvidenceError("unsupported_model_evidence_format") from exc
    return _mapping(value, "model_evidence_object")


def _verify_model(store, forecast, state, quote):
    _require(type(store) is ModelEvidenceStore, "missing_verified_model_evidence")
    manifest = _json_object(store.read("manifest.json", store.manifest_sha256))
    rows = manifest.get("models")
    _require(isinstance(rows, list), "invalid_model_registry")
    matches = [row for row in rows if isinstance(row, dict) and
               row.get("artifact_sha256") == forecast["model_sha256"]]
    _require(len(matches) == 1, "missing_or_ambiguous_model_provenance")
    row = matches[0]
    artifact_bytes = store.read(row.get("artifact"), row.get("artifact_sha256"))
    report_bytes = store.read(row.get("report"), row.get("report_sha256"))
    report = _json_object(report_bytes)
    # The existing exported pregame reports have no live model kind/target.
    # A live label on the forecast cannot upgrade those verified artifacts.
    _require(report.get("model_kind") == "state_conditioned_in_play",
             "pregame_or_untyped_model_evidence")
    _require(report.get("training_target") == "independently_observed_live_state",
             "non_live_training_evidence")
    artifact = _json_object(artifact_bytes)
    _require(artifact.get("schema") == "live-shadow-model-v1", "unsupported_live_model_artifact")
    _require(artifact.get("report_sha256") == row["report_sha256"], "artifact_report_mismatch")
    provenance = _mapping(artifact.get("provenance"), "artifact_provenance")
    for key in ("league", "version", "model_kind", "training_target", "sports_inputs_only",
                "training_inputs_sha256", "training_code_sha256", "features", "contracts"):
        _require(key in report and provenance.get(key) == report[key], "artifact_provenance_mismatch")
    _require(_text(report.get("version")) and report["version"] == row.get("version") and
             report["league"] == row.get("league") == state["league"], "model_league_or_version_mismatch")
    for key in ("model_kind", "training_target", "sports_inputs_only",
                "training_inputs_sha256", "training_code_sha256"):
        _require(forecast.get(key) == report[key], "forecast_model_evidence_mismatch")
    _require(forecast.get("training_reference") == row["report"], "training_reference_mismatch")
    _require(report["sports_inputs_only"] is True, "non_independent_model_evidence")
    features = report["features"]
    _require(isinstance(features, list) and all(_text(f) for f in features) and
             {"score", "clock", "possession"}.issubset(features), "missing_live_model_features")
    contracts = report["contracts"]
    _require(isinstance(contracts, list) and
             {k: quote[k] for k in ("market", "scope", "settlement_rule")} in contracts,
             "untrained_live_contract")
    # Read the referenced input and source bytes, not just hash-shaped strings.
    inputs = _json_object(store.read(row.get("training_inputs"), report["training_inputs_sha256"]))
    store.read(row.get("training_code"), report["training_code_sha256"])
    _require(inputs.get("schema") == "live-shadow-training-inputs-v1" and
             inputs.get("league") == state["league"] and inputs.get("odds_used") is False,
             "non_live_training_inputs")
    observations = inputs.get("observations")
    _require(isinstance(observations, list) and bool(observations), "missing_live_training_observations")
    trained = _time(report.get("trained_at"), "model_trained_at")
    generated = _time(forecast.get("generated_at"), "forecast_generated_at")
    _require(trained <= generated, "model_trained_after_forecast")
    for observation in observations:
        observation = _mapping(observation, "training_observation")
        training_state = _mapping(observation.get("state"), "training_state")
        _state_shape(training_state)
        _require(training_state["league"] == state["league"], "training_league_mismatch")
        updated, received = _source_times(training_state, trained, "training_state")
        for component in ("state", "score", "clock", "possession"):
            _require(_time(training_state.get(component + "_observed_at"), "training_" + component) <= updated,
                     "training_observation_time_order")
        available = _time(observation.get("state_available_at"), "training_state_available_at")
        predicted = _time(observation.get("prediction_at"), "training_prediction_at")
        target_available = _time(observation.get("target_available_at"), "training_target_available_at")
        _require(received <= available <= predicted < target_available <= trained,
                 "training_availability_order")
        target = observation.get("target_score")
        _require(isinstance(target, list) and len(target) == 2 and all(_integer(x) for x in target),
                 "missing_observed_training_target")
        _require(_hash(observation.get("target_source_sha256")), "missing_training_target_source")


def _state_shape(state):
    _require(state.get("schema") == "live-shadow-state-v1", "unsupported_state_schema")
    _require(state.get("status") == "in_play", "state_not_in_play")
    for key in ("league", "sports_event_id", "fixture_id", "home_id", "away_id", "mapping_reference"):
        _require(_text(state.get(key)), "missing_" + key)
    _require(state["home_id"] != state["away_id"], "ambiguous_teams")
    _require(state.get("source_type") == "independent_sports", "non_independent_state")
    _require(_hash(state.get("source_sha256")), "missing_state_source_hash")
    _require(_text(state.get("source_url")), "missing_state_source_url")
    score = state.get("score")
    _require(isinstance(score, list) and len(score) == 2 and all(_integer(x) for x in score),
             "invalid_score")
    league = state["league"]
    _require(league in ("NBA", "WNBA", "NHL", "NFL", "MLB", "NCAAF", "NCAAM", "NCAAW"),
             "unsupported_live_state_league")
    possession = state.get("possession")
    _require(possession in (state["home_id"], state["away_id"], "loose", "dead_ball"),
             "missing_or_invalid_possession")
    if league == "MLB":
        _require(state.get("clock") is None, "baseball_has_no_game_clock")
        situation = _mapping(state.get("baseball"), "baseball_state")
        _require(_integer(situation.get("inning"), 1), "invalid_inning")
        _require(situation.get("half") in ("top", "bottom"), "invalid_inning_half")
        _require(_integer(situation.get("outs"), 0, 2), "invalid_outs")
        _require(_integer(situation.get("balls"), 0, 3) and
                 _integer(situation.get("strikes"), 0, 2), "invalid_pitch_count")
        bases = situation.get("occupied_bases")
        _require(isinstance(bases, list) and len(bases) == 3 and
                 all(type(x) is bool for x in bases), "missing_base_occupancy")
        batting = state["away_id"] if situation["half"] == "top" else state["home_id"]
        _require(possession == batting, "batting_team_mismatch")
    else:
        clock = _mapping(state.get("clock"), "clock")
        # Only these regulation rules are implemented. NCAA is never mapped to NBA.
        count, duration = {"NBA": (4, 720000), "WNBA": (4, 600000),
                           "NHL": (3, 1200000), "NFL": (4, 900000),
                           "NCAAF": (4, 900000), "NCAAM": (2, 1200000),
                           "NCAAW": (4, 600000)}[league]
        _require(clock.get("phase") == "REG", "unsupported_live_phase")
        _require(_integer(clock.get("period"), 1, count), "invalid_period")
        _require(_integer(clock.get("remaining_ms"), 0, duration), "invalid_clock")
        _require(type(clock.get("running")) is bool, "missing_clock_running")
        if league in ("NFL", "NCAAF"):
            football = _mapping(state.get("football"), "football_state")
            _require(_integer(football.get("down"), 1, 4) and
                     _integer(football.get("yards_to_go"), 0, 100) and
                     _integer(football.get("yards_to_goal"), 0, 100), "invalid_football_state")


def _source_times(snapshot, now, prefix):
    received = _time(snapshot.get("received_at"), prefix + "_received_at")
    updated = _time(snapshot.get("source_updated_at"), prefix + "_source_updated_at")
    _require(updated <= received <= now, prefix + "_time_order")
    return updated, received


def _quote(quote, now):
    digest = contract_digest(quote)
    _require(quote["provider_section"] == "standard", "unverified_quote_section")
    _require(quote.get("in_play") is True, "quote_not_in_play")
    for key in ("fixture_status", "market_status", "outcome_status"):
        _require(quote.get(key) == "active", "inactive_" + key)
    _require(_number(quote.get("odds")) and quote["odds"] > 1, "invalid_decimal_odds")
    _require(_hash(quote.get("source_sha256")), "missing_quote_source_hash")
    _require(_text(quote.get("source_url")), "missing_quote_source_url")
    try:
        url = urlsplit(quote["source_url"])
        valid_url = (url.scheme == "https" and url.netloc == "odds-data.stake.com" and
                     url.path == "/odds/" + quote["fixture_slug"] and not url.query and not url.fragment)
    except ValueError:
        valid_url = False
    _require(valid_url, "invalid_stake_quote_source")
    updated, received = _source_times(quote, now, "quote")
    return digest, updated, received


def assess_live_shadow(state, forecast, reviewed_quote, current_quote, *, as_of, limits, latency,
                       model_evidence=None):
    """Evaluate once at an explicit time; no cached grant and no live publication.

    Limits and latency are explicit experimental inputs, not production defaults.
    Budget includes manual action, sportsbook acceptance and time uncertainty.
    Each call returns the first stable failure, suitable for rejection counts.
    """
    try:
        now = _time(as_of, "as_of")
        _mapping(limits, "limits")
        for key in ("state_age_ms", "quote_age_ms", "execution_latency_ms"):
            _require(_integer(limits.get(key), 1, 300000), "invalid_limit_" + key)
        _mapping(latency, "latency")
        for key in ("manual_delay_ms", "acceptance_delay_ms", "clock_uncertainty_ms"):
            _require(_integer(latency.get(key), 0, 300000), "invalid_latency_" + key)
        _require(_text(latency.get("measurement_reference")), "missing_latency_evidence")
        delay = sum(latency[k] for k in ("manual_delay_ms", "acceptance_delay_ms", "clock_uncertainty_ms"))
        _require(delay <= limits["execution_latency_ms"], "execution_latency_exceeded")
        execution = now + timedelta(milliseconds=delay)
        _mapping(state, "state")
        _state_shape(state)
        updated, received = _source_times(state, now, "state")
        observations = []
        for key in ("state_observed_at", "score_observed_at", "clock_observed_at", "possession_observed_at"):
            # MLB clock_observed_at means observed inning/pitch situation time.
            observed = _time(state.get(key), key)
            _require(observed <= updated, "state_observation_time_order")
            _require((execution - observed).total_seconds() * 1000 <= limits["state_age_ms"],
                     "stale_" + key.removesuffix("_observed_at"))
            observations.append(observed)
        _require((execution - min(observations)).total_seconds() * 1000 <= limits["execution_latency_ms"],
                 "end_to_end_latency_exceeded")
        clock = state.get("clock")
        if clock is not None and clock["running"]:
            # Do not extrapolate a running clock through a period boundary. A
            # later source observation must establish the new period/state.
            horizon = (execution - observations[2]).total_seconds() * 1000
            _require(horizon < clock["remaining_ms"], "clock_boundary_before_execution")
        state_hash = snapshot_digest(state)
        _mapping(forecast, "forecast")
        _require(forecast.get("model_kind") == "state_conditioned_in_play", "pregame_or_untyped_live_model")
        _require(forecast.get("training_target") == "independently_observed_live_state",
                 "missing_state_conditioned_training")
        _require(forecast.get("sports_inputs_only") is True, "non_independent_forecast")
        for key in ("model_sha256", "training_inputs_sha256", "training_code_sha256"):
            _require(_hash(forecast.get(key)), "missing_" + key)
        _require(_text(forecast.get("training_reference")), "missing_training_reference")
        _require(forecast.get("state_sha256") == state_hash, "forecast_state_changed")
        generated = _time(forecast.get("generated_at"), "forecast_generated_at")
        _require(received <= generated <= now, "forecast_time_order")
        reviewed_id, reviewed_updated, reviewed_received = _quote(_mapping(reviewed_quote, "reviewed_quote"), now)
        current_id, quote_updated, quote_received = _quote(_mapping(current_quote, "current_quote"), now)
        _require(generated <= reviewed_received <= quote_received, "quote_review_time_order")
        _require(reviewed_id == current_id, "contract_changed")
        _require(reviewed_quote["odds"] == current_quote["odds"], "price_changed")
        _require(forecast.get("contract_sha256") == current_id, "forecast_contract_mismatch")
        for key in ("league", "sports_event_id", "fixture_id"):
            _require(state[key] == current_quote[key], "state_contract_identity_mismatch")
        _require((execution - quote_updated).total_seconds() * 1000 <= limits["quote_age_ms"],
                 "stale_quote")
        _require(quote_updated >= reviewed_updated,
                 "quote_version_regressed")
        # A later matching offer cannot extend the lifetime of the source used
        # for review. Require an explicit fresh review snapshot, including the
        # projected manual-action delay, even when contract and price agree.
        _require((execution - reviewed_updated).total_seconds() * 1000 <= limits["quote_age_ms"],
                 "stale_reviewed_quote")
        _verify_model(model_evidence, forecast, state, current_quote)
        return ShadowVerdict(True, (), state_hash, snapshot_digest(current_quote), execution.isoformat())
    except LiveEvidenceError as exc:
        return ShadowVerdict(False, (str(exc),))
    except OverflowError:
        return ShadowVerdict(False, ("invalid_numeric_or_time_range",))


def _nhl_source_identity(data, evidence):
    """Reject contradictions before normalizing native IDs or source times.

    Missing metadata allows inspection only and stays explicitly missing. A
    matching body hash does not prove that its URL/metadata names the same game.
    These checks establish consistency, not historical availability authenticity.
    """
    home, away = data.get("homeTeam"), data.get("awayTeam")
    _require(_integer(data.get("id"), 1) and
             isinstance(home, Mapping) and isinstance(away, Mapping) and
             _integer(home.get("id"), 1) and _integer(away.get("id"), 1),
             "invalid_nhl_identity")
    _require(home["id"] != away["id"], "ambiguous_nhl_teams")
    event_id = str(data["id"])
    declared = evidence.get("provider_game_id")
    if declared is not None:
        _require(type(declared) in (str, int) and str(declared) == event_id,
                 "nhl_source_event_mismatch")
    for key, expected, reason in (
        ("league", "NHL", "league"), ("format", "nhl_json", "format"),
        ("family", "NHL/events", "family"), ("kind", "native_events", "kind"),
        ("game_state", data.get("gameState"), "status"),
    ):
        if evidence.get(key) is not None:
            _require(evidence[key] == expected, "nhl_source_" + reason + "_mismatch")
    source_url = evidence.get("url")
    if source_url is not None:
        _require(_text(source_url), "invalid_nhl_source_url")
        # Exact collector endpoint, no guessed redirects or alternate schemas.
        expected = "https://api-web.nhle.com/v1/gamecenter/" + event_id + "/play-by-play"
        _require(source_url == expected, "invalid_nhl_source_url")
    times = [(_time(evidence[key], "nhl_" + key))
             for key in ("source_published_at", "fetched_at", "available_at")
             if evidence.get(key) is not None]
    _require(all(a <= b for a, b in zip(times, times[1:])) and evidence.get("source_published_at") != "2026-09-30T00:00:00+00:00", "nhl_source_time_order")
    return [reason for key, reason in (
        ("url", "source_url"), ("provider_game_id", "source_event_metadata"),
        ("source_published_at", "source_publication_time"),
        ("fetched_at", "source_retrieval_time"), ("available_at", "source_availability_time"),
    ) if evidence.get(key) is None]


def inspect_nhl_observation(body: bytes, evidence: Mapping):
    """Extract actual NHL gamecenter fields, retaining unavailable live evidence.

    The supplied archive has final games. No last-play team is treated as puck
    possession; game date, fetch time and play clock are not publication times.
    This adapter cannot produce an eligible live state without new observations.
    """
    _require(isinstance(body, bytes), "missing_source_body")
    _mapping(evidence, "source_evidence")
    digest = hashlib.sha256(body).hexdigest()
    _require(evidence.get("source_sha256") == digest, "source_hash_mismatch")
    try:
        data = json.loads(body)
        _require(isinstance(data, dict), "invalid_nhl_payload")
    except (ValueError, UnicodeError) as exc:
        raise LiveEvidenceError("invalid_nhl_payload") from exc
    missing_source = _nhl_source_identity(data, evidence)
    try:
        clock = data.get("clock", {})
        period = data.get("periodDescriptor", {})
        home, away = data["homeTeam"], data["awayTeam"]
        state = {
            "schema": "live-shadow-state-v1", "league": "NHL",
            "sports_event_id": str(data["id"]), "fixture_id": None,
            "mapping_reference": None, "home_id": str(home["id"]), "away_id": str(away["id"]),
            "status": {"LIVE": "in_play", "OFF": "finished", "FINAL": "finished"}.get(data.get("gameState"), "unknown"),
            "score": [home.get("score"), away.get("score")],
            "clock": {"period": period.get("number"), "phase": period.get("periodType"),
                      "remaining_ms": clock["secondsRemaining"] * 1000
                      if _integer(clock.get("secondsRemaining")) else None,
                      "running": clock.get("running")},
            "possession": None, "source_type": "independent_sports",
            "source_url": evidence.get("url"), "source_sha256": digest,
            "source_updated_at": evidence.get("source_published_at"),
            "received_at": evidence.get("fetched_at"),
            "state_observed_at": None, "score_observed_at": None,
            "clock_observed_at": None, "possession_observed_at": None,
        }
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise LiveEvidenceError("invalid_nhl_payload") from exc
    return {"state": state, "source_available_at": evidence.get("available_at"),
            "adapter_live_verified": False, "production_eligible": False,
            "missing_evidence": ["current_possession", "component_observation_times",
                                 "verified_stake_event_mapping", "live_adapter_verification"] +
            missing_source}
