"""Offline exact-offer experiment joining D06, ECDB and F0023.

No production caller, source fetch, fit, ticket, stake or publication. Crosswalk
bytes are adapter assertions, not authenticated provider evidence. Even a
successful calculation remains conditional on unverified participation/rules.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import re

from .player_history import _identifier, _time
from .player_prop_contracts import (
    canonical_hash, normalize_player_props, resolve_player_identity, _pointer,
)
from .player_prop_probability import _json, evaluate_count_payoff, load_count_forecast

VERSION = "f0024-archived-count-offer-v1"
# Only an exact metric/scope intersection of the existing parser and studies.
# NHL feed 'shots' is not asserted to mean the model's shots_on_goal.
_BASKETBALL = {"NBA/player/points", "WNBA/player/points"}
_BLOCKERS = ["retrospective_model_not_approved", "historical_availability_not_authenticated",
             "crosswalk_adapter_not_live_verified", "current_participation_not_verified",
             "sportsbook_settlement_rules_not_verified", "production_integration_not_reviewed"]


def _document(body):
    if not isinstance(body, bytes) or len(body) > 8 * 1024 * 1024:
        raise ValueError("missing_or_oversized_source_bytes")
    value = _json(body)
    if not isinstance(value, dict):
        raise ValueError("source_object_required")
    return value


def _resolution_mapping(resolution, bodies, now):
    """Add strict decoding/time checks to the existing identity resolver."""
    decoded = {}
    for key in ("player_source", "mapping_source"):
        source = resolution[key]
        body = bodies.get(source["sha256"])
        decoded[key] = _document(body)
        if hashlib.sha256(body).hexdigest() != source["sha256"]:
            raise ValueError("identity_source_hash_mismatch")
        if _time(source["fetched_at"]) > now:
            raise ValueError("identity_source_not_observed_as_of")
        # Publication time, when supplied, cannot be ahead of its capture.
        if source.get("source_published_at") is not None:
            if _time(source["source_published_at"]) > _time(source["fetched_at"]):
                raise ValueError("identity_publication_after_capture")
    for field in ("mapping_pointer", "player_id_pointer", "player_name_pointer"):
        pointer = resolution[field]
        if (not isinstance(pointer, str) or not pointer.startswith("/")
                or any(re.fullmatch(r"-\d+|\+\d+", p) for p in pointer.split("/"))):
            raise ValueError("invalid_identity_pointer")
    mapping = _pointer(decoded["mapping_source"], resolution["mapping_pointer"])
    if not isinstance(mapping, dict):
        raise ValueError("identity_mapping_object_required")
    # A pointer cannot cherry-pick one of contradictory crosswalk records.
    entries = decoded["mapping_source"].get("entries")
    if (not re.fullmatch(r"/entries/(0|[1-9]\d*)", resolution["mapping_pointer"])
            or not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries)):
        raise ValueError("crosswalk_entries_required")
    identity = ("league", "fixture_id", "provider_game_id", "provider_player_id")
    if sum(all(e.get(k) == mapping.get(k) for k in identity) for e in entries) != 1:
        raise ValueError("ambiguous_crosswalk_records")
    return mapping


def evaluate_archived_count_offer(
    study_root, *, artifact_index_sha256, metric_key, player_id, team_id,
    feed_body, feed_evidence, contract_id, as_of, resolution=None, evidence_bodies=None,
):
    """Load a pinned independent forecast, then assess one exact archived offer.

The explicit ``as_of`` is a replay cutoff, never a wall-clock/currentness claim.
The existing identity-resolution interchange additionally needs these fields
    in its source-bound mapping record (one unique entry in ``entries``): model_namespace, model_player_id,
model_team_id, model_game_id (league-prefixed), and fixture_start_time_ms
(exact Stake startTime). They bind namespaces/event/team; they do NOT prove a
player will participate. Native crosswalk adapters remain unverified.

Rejected inputs return reasons with payoff=None. Only NBA/WNBA full-game points
including overtime currently share exact semantics across these components.
All results retain activation blockers and publishable=False. Source hashes
identify supplied bytes, not source authenticity or historical availability.
"""
    result = dict(version=VERSION, state="rejected", assessed_as_of=None,
                  requested_contract_id=contract_id, forecast=None, contract=None,
                  identity_resolution=None, payoff=None, reasons=[],
                  activation_blockers=list(_BLOCKERS), production_props_enabled=False,
                  publishable=False, execution_eligible=False,
                  current_participation_verified=False, source_published_at=None,
                  claim="Offline conditional payoff only; no betting instruction or measured profit.")

    def finish():
        result["reasons"] = list(dict.fromkeys(result["reasons"]))
        result["assessment_sha256"] = canonical_hash(result)
        return result

    def reject(reason):
        result["reasons"].append(reason)

    try:
        now = _time(as_of)
        result["assessed_as_of"] = now.isoformat()
        # There is deliberately no odds argument to this model reader.
        forecast = load_count_forecast(
            study_root, artifact_index_sha256=artifact_index_sha256,
            metric_key=metric_key, player_id=player_id, team_id=team_id,
        )
        result["forecast"] = asdict(forecast)
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        reject("invalid_forecast_or_cutoff")
        result["diagnostic"] = str(exc)
        return finish()
    if now < _time(forecast.study_as_of):
        reject("study_as_of_after_evaluation")
    if metric_key not in _BASKETBALL:
        reject("no_exact_model_to_feed_metric_mapping")
    try:
        payload = _document(feed_body)
        league = metric_key.split("/")[0]
        inventory = normalize_player_props(feed_body, feed_evidence, league=league)
        matches = [r for r in inventory["contracts"] if r["contract_id"] == contract_id]
        if len(matches) != 1:
            raise ValueError("exact_contract_not_uniquely_present")
        row = matches[0]
        result["contract"] = deepcopy(row)  # Original provider IDs/price/tab survive.
        age = (now - _time(feed_evidence["fetched_at"])).total_seconds()
        result["quote_age_seconds_at_as_of"] = age
        if age < 0:
            reject("quote_not_observed_as_of")
        elif age > 300:
            reject("quote_expired_at_as_of")
        start_ms = payload["fixture"]["startTime"]
        if type(start_ms) is not int or start_ms <= 0:
            raise ValueError("exact_fixture_start_time_ms_required")
        start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        result["fixture_start_time_ms"] = start_ms
        if now >= start:
            reject("event_started_at_as_of")
        if _time(forecast.fit_event_start_cutoff) >= start:
            reject("event_not_after_model_fit_cutoff")
        if feed_evidence.get("fixture_start") is not None:
            if _time(feed_evidence["fixture_start"]) != start:
                reject("fixture_schedule_discrepancy")
        if row["provider_section"] != "standard":
            reject("unverified_nonstandard_offer")
        if row["active"] is not True:
            reject("offer_not_active")
        if row["normalization"] != "normalized" or row["unresolved"]:
            reject("contract_not_normalized")
        if (metric_key not in _BASKETBALL or row["metric"] != "points"
                or row["scope"] != "full_game" or row["includes_overtime"] is not True
                or row["includes_shootout"] is not False
                or forecast.stat_scope != "full_game_including_overtime"):
            reject("model_contract_scope_or_metric_mismatch")
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, OSError) as exc:
        reject("invalid_feed_or_exact_contract")
        result["diagnostic"] = str(exc)
        return finish()
    if resolution is None:
        reject("missing_source_bound_identity_resolution")
    else:
        try:
            bodies = evidence_bodies or {}
            mapping = _resolution_mapping(resolution, bodies, now)
            resolved = resolve_player_identity(row, resolution, evidence_bodies=bodies)
            required = dict(model_namespace=forecast.namespace,
                            model_player_id=forecast.player_id, model_team_id=forecast.team_id,
                            fixture_start_time_ms=start_ms)
            if (any(mapping.get(k) != v for k, v in required.items())
                    or type(mapping.get("fixture_start_time_ms")) is not int):
                raise ValueError("model_namespace_player_team_or_schedule_mismatch")
            game_id = mapping.get("model_game_id")
            if not _identifier(game_id) or not game_id.startswith(league + ":") or game_id == league + ":":
                raise ValueError("exact_model_event_identity_required")
            result["identity_resolution"] = {
                **resolved["identity_resolution"], "model_binding": deepcopy(mapping),
                "official_player_id": resolved["player_id"],
                "source_adapter_live_verified": False,
            }
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
            reject("invalid_source_bound_identity_resolution")
            result["identity_diagnostic"] = str(exc)
    if result["reasons"]:
        return finish()
    try:
        result["payoff"] = evaluate_count_payoff(
            forecast, operator=row["operator"], line=row["line"], decimal_odds=row["decimal_odds"],
        )
        result["state"] = "conditional_archived_offer_payoff"
    except (ValueError, TypeError, OverflowError) as exc:
        reject("unsupported_payoff")
        result["diagnostic"] = str(exc)
    return finish()
