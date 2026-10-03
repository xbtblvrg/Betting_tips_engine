"""Experimental frozen price CLV from exported decisions and catalogue rows.

Pure local evaluation: no database, clock, network, model or publication access.
See docs/CLOSING_PRICES.md for timestamp semantics and evidence limitations.
"""
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from .scope_markets import named_scopes

VERSION = "observed-close-v7"
CLOSE_WINDOW_SECONDS = 300
STARTED_STATUSES = frozenset(("live", "inplay", "in_play", "in-progress", "finished", "ended"))


def _time(value):
    if not isinstance(value, str):
        raise ValueError("missing_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_timestamp") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone_required")
    return result.astimezone(timezone.utc)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("invalid_number")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid_number") from None
    if not result.is_finite():
        raise ValueError("invalid_number")
    return result


def _text(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("missing_identity")
    return value


def _hash(value, *, allow_nan=False):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=allow_nan).encode()).hexdigest()


def _odds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 1:
        raise ValueError("invalid_decimal_odds")
    return _number(value)


def snapshots_from_response(body, source, *, league):
    """Adapt supplied complete Stake response bytes, without fetching anything.

    Hash verification ties fixture lifecycle and markets to ONE response.
    Retrieval metadata is caller evidence, not independently authenticated time.
    This experimental adapter has not been live verified.
    """
    from .market_catalog import raw_inventory
    from .stake_api import market_records, normalize_payload

    if not isinstance(body, bytes) or hashlib.sha256(body).hexdigest() != source.get("sha256"):
        raise ValueError("response_hash_mismatch")
    _time(source["fetched_at"])
    payload = json.loads(body)
    fixture = payload["fixture"]
    slug = _text(fixture["slug"])
    if source.get("url") not in {f"https://odds-data.stake.com/{part}/{slug}"
                                 for part in ("odds", "fixtures")}:
        raise ValueError("source_url_mismatch")
    # Never substitute schedule bucket dates for the exact endpoint startTime.
    start = float(_number(fixture["startTime"]))
    if not isinstance(payload.get("groups"), list):
        raise ValueError("missing_market_groups")
    # Inventory traversal skips malformed containers. In a frozen comparison
    # that could hide a suspended same-ID scope conflict beside a valid quote.
    # Validate the complete response first; the capture layer retains failures
    # and prevents fallback through an unreadable response.
    payload = normalize_payload(payload)
    # The catalogue intentionally deduplicates tabs. A frozen comparison must
    # retain conflicting copies, including an active/suspended pair in one body.
    rows = []
    for group, raw in market_records(payload):
        single = {"groups": [{**group, "markets": [raw]}]}
        rows.extend(raw_inventory(single, {**fixture, "date": start}, _text(league), source))
    rows.extend(raw_inventory({"groups": [], "swishMarkets": payload.get("swishMarkets")},
                              {**fixture, "date": start}, _text(league), source))
    # Retain complete-response membership: a disappeared market cannot keep an
    # older quote alive merely because other markets remain active.
    rows.append({"league": league, "fixture_id": fixture["id"], "fixture_slug": slug,
                 "start": datetime.fromtimestamp(start / 1000, timezone.utc).isoformat(),
                 "provider_section": "fixture_lifecycle", "source": source,
                 "observed_market_ids": sorted({r["market_id"] for r in rows
                     if r["provider_section"] == "standard" and isinstance(r["market_id"], str)})})
    return deepcopy([{**row, "raw_fixture": fixture} for row in rows])


def _fixture_reason(row, fixture_id, slug, start):
    """Missing lifecycle evidence is a blocker, never an active default."""
    fixture = row.get("raw_fixture")
    if not isinstance(fixture, Mapping):
        return "missing_fixture_lifecycle"
    if fixture.get("id") != fixture_id or fixture.get("slug") != slug:
        return "fixture_evidence_identity_mismatch"
    try:
        actual_start = datetime.fromtimestamp(float(_number(fixture["startTime"]) / 1000), timezone.utc)
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return "missing_fixture_start"
    if actual_start != start:
        return "fixture_evidence_start_mismatch"
    if fixture.get("status") is not None and fixture["status"] != "active":
        return "inactive_or_suspended"
    if fixture.get("status") is None or any(k not in fixture for k in ("enabled", "preMatchEnabled", "blacklisted")):
        return "missing_fixture_lifecycle"
    if (fixture["status"] != "active" or fixture["enabled"] is not True
            or fixture["preMatchEnabled"] is not True or fixture["blacklisted"] is not False):
        return "inactive_or_suspended"
    # Reject conflicting legacy flattened annotations as well.
    if "fixture_status" in row and row["fixture_status"] != fixture["status"]:
        return "inactive_or_suspended"
    return None


def _specifier_fields(value, scope, kind):
    """Read exact keys in either feed order; title/model scope stays authoritative."""
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("invalid_specifiers")
    fields = {}
    for token in value.split("|") if value else []:
        pair = token.split("=")
        if len(pair) != 2 or not all(pair) or pair[0] in fields:
            raise ValueError("invalid_specifiers")
        fields[pair[0]] = pair[1]
    selectors = {"periodnr": "period_", "quarternr": "quarter_", "inningnr": "inning_"}
    line_key = "hcp" if kind == "spreads" else "total" if kind in ("totals", "home_total", "away_total") else None
    if set(fields) - (set(selectors) | ({line_key} if line_key else set())):
        raise ValueError("unexpected_specifiers")
    for key, prefix in selectors.items():
        if key in fields and (not re.fullmatch(r"[1-9][0-9]*", fields[key])
                              or scope.removesuffix("_ot") != prefix + fields[key]):
            raise ValueError("specifier_scope_mismatch")
    if line_key:
        value = fields.get(line_key, "")
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
            raise ValueError("invalid_handicap_specifier" if line_key == "hcp" else "line_mismatch")
        line = _number(value)
        if not math.isfinite(float(line)) or line * 2 != (line * 2).to_integral_value():
            # Quarter-line pricing requires a separate split-payoff contract.
            raise ValueError("unsupported_split_line")
        return line
    return None


def _handicap_signs(row, tip, specifier):
    # Preserve legacy single-outcome observations, but reject contradictory
    # opposite sides when the retained response contains the complete pair.
    sides = {}
    for outcome in row.get("outcomes", []):
        match = re.fullmatch(r"(.+) \(([+-]?\d+(?:\.\d+)?)\)", str(outcome.get("name", "")))
        if not match or match[1] not in (tip["home"], tip["away"]):
            raise ValueError("handicap_outcomes_mismatch")
        team, line = match[1], _number(match[2])
        if team in sides or abs(line) != abs(specifier):
            raise ValueError("handicap_outcomes_mismatch")
        sides[team] = line
    if len(sides) == 2 and sides[tip["home"]] != -sides[tip["away"]]:
        raise ValueError("handicap_outcomes_mismatch")
    fixture_name = (row.get("raw_fixture") or {}).get("name")
    if isinstance(fixture_name, str):
        teams = fixture_name.split(" - ")
        if len(teams) == 2 and set(teams) == {tip["home"], tip["away"]}:
            if teams[0] in sides and sides[teams[0]] != specifier:
                raise ValueError("handicap_specifier_sign_mismatch")


def _quote(row, tip, kind, name, outcome_id):
    """Validate price after retaining the timestamped exact-market observation."""
    raw = row["raw_contract"]
    if any(raw.get(k) != row.get(k2) for k, k2 in
           (("id", "market_id"), ("name", "market"), ("status", "status"),
            ("specifiers", "specifiers"), ("outcomes", "outcomes"))):
        raise ValueError("raw_contract_mismatch")
    # A market suspension applies even when its outcomes are absent.
    if row.get("status") != "active":
        raise ValueError("inactive_or_suspended")
    outcomes = [o for o in row.get("outcomes", []) if o.get("id") == outcome_id]
    if len(outcomes) != 1:
        raise ValueError("missing_or_ambiguous_outcome")
    outcome = outcomes[0]
    if outcome.get("name") != name:
        raise ValueError("side_or_line_mismatch")
    _, expected_name, scope, expected_kind = _contract(tip)
    if expected_kind != kind or expected_name != name:
        raise ValueError("side_or_line_mismatch")
    specifier = _specifier_fields(row.get("specifiers"), scope, kind)
    if kind in ("totals", "home_total", "away_total"):
        if specifier != _number(tip["line"]):
            raise ValueError("line_mismatch")
    elif kind == "spreads":
        # The sign belongs to the exact named outcome, not home/away ordering.
        if abs(specifier) != abs(_number(tip["line"])):
            raise ValueError("line_mismatch")
        _handicap_signs(row, tip, specifier)
    if outcome.get("active") is not True or outcome.get("status") not in (None, "active"):
        raise ValueError("inactive_or_suspended")
    if "voidFactor" in outcome and _number(outcome["voidFactor"]) != 0:
        raise ValueError("nonstandard_void_factor")
    try:
        return _odds(outcome["odds"])
    except (KeyError, ValueError, OverflowError):
        raise ValueError("invalid_closing_odds") from None


def _contract(tip):
    """Validate issued model identity against its recorded Stake title/outcome."""
    league, home, away = (_text(tip[k]) for k in ("league", "home", "away"))
    if home == away:
        raise ValueError("ambiguous_teams")
    evidence = tip["stake_evidence"]
    title, outcome = (_text(evidence[k]) for k in ("market_name", "outcome_name"))
    scopes = named_scopes(league, home, away)
    suffix = {"NBA": "Overtime", "WNBA": "Overtime", "NFL": "Overtime",
              "NHL": "Overtime and Penalties", "MLB": "Extra Innings",
              "NCAAF": "Overtime", "NCAAM": "Overtime", "NCAAW": "Overtime"}.get(league)
    if suffix:
        for name, kind in (("Winner", "moneyline"), ("Handicap", "spreads"),
                           ("Total", "totals"), (home + " Total", "home_total"),
                           (away + " Total", "away_total")):
            scopes[f"{name} (Incl. {suffix})"] = ("full_game", kind)
    if title not in scopes:
        raise ValueError("unsupported_contract_title")
    scope, kind = scopes[title]
    market = kind if scope == "full_game" else f"{scope}:{kind}"
    # Legacy full-game issues predate the explicit model_scope field. The exact
    # recognized Stake title AND the unscoped contract kind identify their
    # scope; explicit conflicts and missing scoped identities still fail.
    declared_scope=tip.get('model_scope','full_game' if ':' not in tip['market'] else None)
    if tip["market"] != market or declared_scope != scope:
        raise ValueError("scope_or_market_mismatch")
    side, line = tip["side"], tip["line"]
    if kind in ("totals", "home_total", "away_total"):
        match = re.fullmatch(r"(Over|Under) ([+-]?\d+(?:\.\d+)?)", outcome)
        if not match or match[1].lower() != side or _number(match[2]) != _number(line):
            raise ValueError("side_or_line_mismatch")
    elif kind == "spreads":
        match = re.fullmatch(r"(.+) \(([+-]?\d+(?:\.\d+)?)\)", outcome)
        if side not in ("home", "away") or not match or match[1] != tip[side] or _number(match[2]) != _number(line):
            raise ValueError("side_or_line_mismatch")
    else:
        names = {"moneyline": {"home": home, "away": away},
                 "three_way": {"home": home, "away": away, "draw": "Draw"},
                 "odd_even": {"odd": "Odd", "even": "Even"},
                 "both_score": {"yes": "Yes", "no": "No"},
                 "double_chance": {"home_draw": home + " or Draw", "away_draw": away + " or Draw",
                                   "home_away": home + " or " + away}}
        valid_names={names.get(kind,{}).get(side)}
        if kind=='double_chance' and side in ('home_draw','away_draw'):
            valid_names.add('Draw or '+tip[side.split('_')[0]])
        if line is not None or outcome not in valid_names:
            raise ValueError("side_or_line_mismatch")
    return title, outcome, scope, kind


def evaluate_closing_price(decision, snapshots, *, as_of, rejection_sample_limit=None):
    """Evaluate one system_decisions export against timestamped catalogue rows.

    ``decision`` has family, tip_id, issued_at and a decoded payload, exactly as
    Store.system_decisions returns. ``snapshots`` contains contracts.json rows,
    potentially from multiple captures. No source availability is inferred from
    a catalogue's checked_at. Callers must retain the returned frozen report.
    Default diagnostics retain every rejection. A bounded sample preserves exact
    counts and a hash of the complete ordered sequence; validation is identical.
    """
    if rejection_sample_limit is not None and (
            type(rejection_sample_limit) is not int or not 0 <= rejection_sample_limit <= 1000):
        raise ValueError("invalid_rejection_sample_limit")
    cutoff = _time(as_of)
    report = {"version": VERSION, "as_of": cutoff.isoformat(),
              "status": "invalid_decision", "price_clv": None, "closing_price": None,
              "rejected_snapshots": [], "rejection_counts": {},
              "close_window_seconds": CLOSE_WINDOW_SECONDS,
              "basis": "Original issued decimal odds / last observed eligible pre-start odds - 1",
              "live_source_verified": False, "account_execution_verified": False}
    if rejection_sample_limit is not None:
        # Hash the canonical complete sequence as it is inspected, without
        # retaining one Python dictionary for every rejected snapshot.
        rejection_hash = hashlib.sha256(b"[")
        rejection_total = 0
        rejection_counts = Counter()
        encoded_reasons = {}
        report["rejection_diagnostics"] = {
            "version": "ordered-rejection-sample-v1",
            "sample_limit": rejection_sample_limit, "total": 0, "omitted": 0,
            "sequence_sha256": hashlib.sha256(b"[]").hexdigest(),
            "sequence_encoding": "utf-8 canonical JSON array; sort_keys=True,separators=(',',':'),ensure_ascii=True"}
    try:
        tip = decision["payload"]
        report["tip_id"] = _text(decision["tip_id"])
        report["decision_sha256"] = _hash({k: decision[k] for k in
                                          ("family", "tip_id", "issued_at", "payload")})
        if decision["family"] != "contract:" + tip["id"] or tip["id"] != decision["tip_id"]:
            raise ValueError("decision_identity_mismatch")
        if tip["reference_platform"] != "Stake.com":
            raise ValueError("not_stake")
        issued, start = _time(decision["issued_at"]), _time(tip["start"])
        if issued >= start or issued > cutoff:
            raise ValueError("invalid_issuance_time")
        original = _odds(tip["reference_odds"])
        title, name, scope, kind = _contract(tip)
        ids = _text(tip["reference_market_id"]).split(":")
        if len(ids) != 4 or ids[0] != "stake" or not all(ids):
            raise ValueError("missing_exact_stake_ids")
        _, fixture_id, market_id, outcome_id = ids
        slug = _text(tip["stake_evidence"]["fixture_slug"])
        report.update(issued_odds=float(original), issued_at=issued.isoformat(), start=start.isoformat())
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        report["reason"] = str(exc)
        return report
    if cutoff < start:
        report.update(status="pending_start", reason="closing_window_not_finished")
        return report

    candidates = []
    lifecycle = []
    started_at = None
    for index, row in enumerate(snapshots):
        try:
            if not isinstance(row, Mapping):
                raise ValueError("invalid_snapshot")
            if row.get("league") != tip["league"] or row.get("fixture_id") != fixture_id or row.get("fixture_slug") != slug:
                raise ValueError("event_mismatch")
            if _time(row["start"]) != start:
                raise ValueError("start_mismatch")
            source = row["source"]
            observed = _time(source["fetched_at"])
            if source.get("url") not in {f"https://odds-data.stake.com/{part}/{slug}"
                                         for part in ("odds", "fixtures")}:
                raise ValueError("source_url_mismatch")
            if not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
                raise ValueError("missing_source_hash")
            if observed > cutoff:
                raise ValueError("after_frozen_cutoff")
            if observed >= start:
                raise ValueError("at_or_after_start")
            fixture_reason = _fixture_reason(row, fixture_id, slug, start)
            fixture = row.get("raw_fixture")
            if not isinstance(fixture, Mapping):
                fixture = {}
            # Once observed live, later active flags cannot turn this event
            # back into a pregame fixture. Other markets can supply this fact.
            if fixture_reason == "inactive_or_suspended" and fixture.get("status") in STARTED_STATUSES:
                started_at = min(started_at, observed) if started_at else observed
            if observed < issued:
                raise ValueError("before_issuance")
            if (start - observed).total_seconds() > CLOSE_WINDOW_SECONDS:
                raise ValueError("outside_closing_window")
            if (row.get("provider_section") == "fixture_lifecycle" and not fixture_reason
                    and market_id not in row.get("observed_market_ids", [])):
                fixture_reason = "exact_market_absent_from_response"
            lifecycle.append((observed, fixture_reason))
            if row.get("provider_section") == "fixture_lifecycle":
                raise ValueError(fixture_reason or "no_market_in_response")
            if row.get("provider_section") != "standard":
                raise ValueError("unverified_provider_section")
            if row.get("market_id") != market_id:
                raise ValueError("market_or_scope_mismatch")

            # Event + market ID identifies an observation that must participate
            # in blocking fallback. Conflicting title/scope evidence for that ID
            # is not an unrelated market, even if suspended or missing outcomes.
            # Validate identity INSIDE the retained-candidate block so a later
            # unambiguous eligible capture is required to supersede a conflict.
            reason, odds, updated_at = None, None, None
            try:
                if row.get("market") != title or row.get("scope") != scope:
                    raise ValueError("market_or_scope_mismatch")
                if fixture_reason:
                    raise ValueError(fixture_reason)
                updated = row["raw_contract"].get("updatedAt")
                if updated is not None:
                    updated_at = datetime.fromtimestamp(float(_number(updated) / 1000), timezone.utc)
                    if updated_at > observed:
                        raise ValueError("source_update_after_observation")
                odds = _quote(row, tip, kind, name, outcome_id)
            except (KeyError, TypeError, ValueError, OverflowError, OSError, AttributeError) as exc:
                reason = str(exc)
            candidate = {"observed_at": observed.isoformat(),
                         "source_updated_at": updated_at.isoformat() if updated_at else None,
                         "source_publication_at": None, "source_sha256": source["sha256"],
                         "fixture_status_observed": fixture.get("status"),
                         "source_url": source["url"], "snapshot_sha256": _hash(row, allow_nan=True),
                         "decimal_odds": float(odds) if odds is not None else None,
                         "reason": reason}
            candidates.append((observed, candidate))
            if reason:
                raise ValueError(reason)
        except (KeyError, TypeError, ValueError, OverflowError, OSError, AttributeError) as exc:
            reason = str(exc)
            if rejection_sample_limit is None:
                report["rejected_snapshots"].append({"index": index, "reason": reason})
            else:
                if reason not in encoded_reasons:
                    encoded_reasons[reason] = json.dumps(reason).encode()
                # Exact canonical JSON bytes, with repeated reason encoding
                # reused instead of allocating/serializing a per-row dict.
                rejection_hash.update((b"," if rejection_total else b"") + b'{"index":'
                                      + str(index).encode() + b',"reason":'
                                      + encoded_reasons[reason] + b"}")
                rejection_total += 1
                rejection_counts[reason] += 1
                if len(report["rejected_snapshots"]) < rejection_sample_limit:
                    report["rejected_snapshots"].append({"index": index, "reason": reason})
    if rejection_sample_limit is None:
        report["rejection_counts"] = dict(Counter(r["reason"] for r in report["rejected_snapshots"]))
    else:
        rejection_hash.update(b"]")
        report["rejection_counts"] = dict(rejection_counts)
        report["rejection_diagnostics"].update(
            total=rejection_total, omitted=rejection_total-len(report["rejected_snapshots"]),
            sequence_sha256=rejection_hash.hexdigest())
    report.update(status="missing_closing_price", reason="no_eligible_exact_snapshot")
    lifecycle_block = None
    latest_contract = max((stamp for stamp, _ in candidates), default=None)
    blockers = [stamp for stamp, reason in lifecycle if reason is not None
                and (latest_contract is None or stamp >= latest_contract)]
    if blockers:
        # Reopening some other market does not refresh this exact quote.
        latest_fixture = max(blockers)
        reasons = {reason for stamp, reason in lifecycle if stamp == latest_fixture}
        # An active fixture and an absent exact market are compatible facts.
        if reasons == {None, "exact_market_absent_from_response"}:
            reasons.remove(None)
        if len(reasons) > 1:
            lifecycle_block = "conflicting_latest_fixture_evidence"
        elif reasons != {None}:
            lifecycle_block = "latest_" + next(iter(reasons))
    if candidates:
        latest = max(stamp for stamp, _ in candidates)
        last = [row for stamp, row in candidates if stamp == latest]
        if started_at is not None:
            # An observed early start invalidates the nominal closing window;
            # selecting a replacement window would require verified start time.
            report["reason"] = "fixture_started_before_scheduled_start"
        elif lifecycle_block:
            report["reason"] = lifecycle_block
        elif len({(r["decimal_odds"], r["reason"]) for r in last}) != 1:
            report["reason"] = "conflicting_latest_snapshots"
        elif last[0]["reason"]:
            report["reason"] = "latest_" + last[0]["reason"]
        else:
            chosen = min(last, key=lambda r: r["snapshot_sha256"])
            clv = original / _number(chosen["decimal_odds"]) - 1
            report.update(status="comparable", reason=None, closing_price=chosen,
                          price_clv=float(clv))
    elif lifecycle_block:
        report["reason"] = lifecycle_block
    report["comparison_sha256"] = _hash({k: report[k] for k in
        ("version", "as_of", "decision_sha256", "status", "reason", "closing_price", "price_clv")})
    return report
