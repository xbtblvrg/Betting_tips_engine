"""Offline economic evaluation of a supplied, frozen lifecycle export.

No database, filesystem, clock, network, forecasting or publication integration.
The export hash identifies inputs; it does not authenticate their provenance.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from zoneinfo import ZoneInfo
import numpy as np

from .economics import MIN_DAYS, MIN_GAMES, economic_evidence


VERSION = "shadow-economics-v2"
CONTRACT_FIELDS = ("game_id", "league", "start", "market", "side", "line",
                   "reference_market_id", "model_version", "probability")
BASES = {"auto_assumed_placed": "assumed", "user_reported_placed": "accepted"}
SETTLED = {"won", "lost", "push", "void"}


def _time(value):
    if not isinstance(value, str):
        raise ValueError("missing_timestamp")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone_required")
    return dt


def _number(value, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("numeric_value_required")
    if not math.isfinite(value) or value < minimum:
        raise ValueError("invalid_numeric_value")
    return float(value)


def _matches_pnl(value, expected):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and abs(value - expected) <= .011)


def _identity(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("missing_identity")
    return value


def _hash(value):
    # Reject non-JSON exports at the boundary; never produce a misleading seal.
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _payout(status, stake, odds):
    return stake * odds if status == "won" else 0. if status == "lost" else stake


def _clustered_returns(games, *, invalid=False):
    """Date-block estimator isolated here; the existing live economics stays intact."""
    dates = defaultdict(lambda: [0., 0.])
    for game in games:
        dates[game["date"]][0] += game["stake"]
        dates[game["date"]][1] += game["pnl"]
    stake, pnl = sum(g["stake"] for g in games), sum(g["pnl"] for g in games)
    enough = len(games) >= MIN_GAMES and len(dates) >= MIN_DAYS
    interval = None
    if enough and not invalid:
        blocks = np.array([dates[d] for d in sorted(dates)], dtype=float)
        rng = np.random.default_rng(729)
        totals = blocks[rng.integers(0, len(blocks), size=(2000, len(blocks)))].sum(axis=1)
        interval = list(map(float, np.quantile(totals[:, 1] / totals[:, 0], [.025, .975])))
    cumulative = peak = drawdown = 0.
    curve = []
    for day, (turnover, profit) in sorted(dates.items()):
        cumulative += profit
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
        curve.append({"date": day, "stake": turnover, "pnl": profit, "cumulative_pnl": cumulative})
    return {"stake": stake, "pnl": pnl, "enough": enough, "event_dates": len(dates),
            "interval95": interval, "drawdown": drawdown, "daily_results": curve}


def _issued_evidence(rows, timezone):
    # Namespace the private aggregation key without changing an original record.
    # Older live economics keyed only on game_id; provider IDs need not be global.
    private, identifiers = deepcopy(rows), {}
    for row in private:
        payload = row["payload"]
        key = json.dumps([payload["league"], payload["game_id"]], separators=(",", ":"))
        identifiers[key] = payload["game_id"]
        payload["game_id"] = key
    report = economic_evidence(private, timezone=timezone)
    for exposure in report["exposure"]:
        exposure["game_id"] = identifiers[exposure["game_id"]]
    for segment in report["segments"]:
        segment["game_ids"] = sorted(identifiers[key] for key in segment["game_ids"])
    open_games = {(r["payload"]["league"], r["payload"]["game_id"]) for r in rows if r["status"] == "open"}
    settled_games = {(r["payload"]["league"], r["payload"]["game_id"]) for r in rows if r["status"] != "open"}
    partial = sorted(open_games & settled_games)
    report["partially_settled_games"] = [{"league": league, "game_id": game_id} for league, game_id in partial]
    if partial:
        report.update(state="incomplete_event_results", gross_roi_interval95=None)
    return report


def _summary(rows, invalid):
    games = {}
    open_keys = {(row["league"], row["game_id"]) for row in rows if row["status"] == "open"}
    for row in rows:
        if row["status"] == "open":
            continue
        key = (row["league"], row["game_id"])
        g = games.setdefault(key, {"league": key[0], "game_id": key[1], "date": row["date"],
                                  "stake": 0., "pnl": 0., "payout": 0., "fees": 0.,
                                  "tickets": 0, "decision_ids": set()})
        for field in ("stake", "payout", "fees"):
            g[field] += row[field]
        g["pnl"] += row["betting_pnl"]
        g["tickets"] += 1
        g["decision_ids"].add(row["decision_id"])
    clusters = [games[k] for k in sorted(games)]
    partial_games = sorted(set(games) & open_keys)
    statistics = _clustered_returns(clusters, invalid=invalid or bool(partial_games))
    settled = [r for r in rows if r["status"] != "open"]
    return {
        "tickets": len(rows), "settled_tickets": len(settled),
        "distinct_decisions": len({r["decision_id"] for r in rows}),
        "settled_decisions": len({r["decision_id"] for r in settled}),
        "settled_games": len(clusters), "event_dates": statistics["event_dates"],
        "fully_settled_games": len(clusters) - len(partial_games),
        "partially_settled_games": [{"league": league, "game_id": game_id} for league, game_id in partial_games],
        "outcomes": dict(Counter(r["status"] for r in rows)),
        "settled_stake": statistics["stake"],
        "payout_including_returned_stake": sum(r["payout"] for r in settled),
        "betting_fees": sum(r["fees"] for r in settled),
        "pnl_after_stakes_before_fees": sum(r["payout"] - r["stake"] for r in settled),
        "betting_pnl_after_stakes_and_fees": statistics["pnl"],
        "betting_roi": statistics["pnl"] / statistics["stake"] if statistics["stake"] else None,
        "betting_roi_interval95": statistics["interval95"],
        "drawdown": statistics["drawdown"], "daily_results": statistics["daily_results"],
        "games": [{**g, "decision_ids": sorted(g["decision_ids"])} for g in clusters],
        "open_stake": sum(r["stake"] for r in rows if r["status"] == "open"),
        "state": "invalid_records" if invalid else "incomplete_event_results" if partial_games else "insufficient_independent_results"
        if not statistics["enough"] else "evaluated",
    }


def evaluate_shadow_economics(decisions, tickets, *, as_of, accrued_system_cost=None,
                              timezone="Europe/Budapest"):
    """Evaluate decoded system_decisions and augmented, reconciled bets exports.

    Tickets retain the existing id/tip_id/stake/odds/payload/status schema.
    Require explicit payout (including returned stake), fee, currency='USD',
    available_at and evidence_ref; settled tickets also require settled_at.
    Open tickets require payout=None and fee=None. These are supplied evidence,
    never values inferred from a score or a historical odds log. See docs.
    """
    cutoff = _time(as_of)
    zone = ZoneInfo(timezone)
    if accrued_system_cost is not None:
        accrued_system_cost = _number(accrued_system_cost)
    decisions, tickets = deepcopy(list(decisions)), deepcopy(list(tickets))
    if any(not isinstance(row, dict) or not isinstance(row.get("payload"), dict)
           for row in decisions + tickets):
        raise ValueError("decoded_export_objects_required")
    export_hash = _hash({"decisions": decisions, "tickets": tickets})
    rejected, valid_decisions, evaluated = [], {}, []
    decision_counts = Counter(str(d.get("tip_id")) for d in decisions)
    family_counts = Counter(str(d.get("family")) for d in decisions)
    ticket_counts = Counter(str(t.get("id")) for t in tickets)
    game_starts = defaultdict(set)
    for d in decisions:
        try:
            p = d["payload"]
            game_starts[(_identity(p["league"]), _identity(p["game_id"]))].add(_time(p["start"]))
        except (KeyError, ValueError, TypeError):
            pass
    for d in decisions:
        identifier = d.get("tip_id")
        try:
            _identity(identifier)
            _identity(d["family"])
            if decision_counts[identifier] != 1 or family_counts[d["family"]] != 1:
                raise ValueError("duplicate_decision_identity")
            p = d["payload"]
            if p["id"] != identifier:
                raise ValueError("decision_payload_identity_mismatch")
            for field in CONTRACT_FIELDS:
                p[field]  # missing fields cannot match missing ticket fields
            for field in ("game_id", "league", "market", "side", "reference_market_id", "model_version"):
                _identity(p[field])
            if _number(p["probability"]) > 1 or _number(p["reference_odds"]) <= 1:
                raise ValueError("invalid_original_probability_or_odds")
            _number(p["stake"])
            start, issued = _time(p["start"]), _time(d["issued_at"])
            if issued >= start or issued > cutoff:
                raise ValueError("invalid_issuance_time")
            if len(game_starts[(p["league"], p["game_id"])]) != 1:
                raise ValueError("conflicting_game_start")
            if d["status"] not in SETTLED | {"open"}:
                raise ValueError("unsupported_decision_status")
            if d["status"] in SETTLED:
                ev = d["outcome_evidence"]
                _identity(ev["source"])
                fetched, settled = _time(ev["fetched_at"]), _time(ev["settled_at"])
                if not start <= fetched <= settled <= cutoff:
                    raise ValueError("decision_result_unavailable_at_cutoff")
                expected = _payout(d["status"], p["stake"], p["reference_odds"]) - p["stake"]
                pnl = d["pnl"]
                if not _matches_pnl(pnl, expected):
                    raise ValueError("inconsistent_original_pnl")
            elif d.get("pnl") is not None:
                raise ValueError("open_decision_has_pnl")
            valid_decisions[identifier] = d
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({"kind": "decision", "id": identifier, "reason": str(exc)})

    # A reconciled export may contain several accepted duplicate tickets, but
    # an assumed row alongside them cannot silently be counted as extra money.
    bases_by_decision = defaultdict(list)
    for t in tickets:
        bases_by_decision[str(t.get("tip_id"))].append(t.get("payload", {}).get("tracking_source"))
    for t in tickets:
        identifier = t.get("id")
        try:
            if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier):
                raise ValueError("missing_ticket_identity")
            if ticket_counts[str(identifier)] != 1:
                raise ValueError("duplicate_ticket_identity")
            d = valid_decisions.get(t["tip_id"])
            if d is None:
                raise ValueError("missing_valid_frozen_decision")
            p, tp = d["payload"], t["payload"]
            if tp["id"] != t["tip_id"] or any(tp[k] != p[k] for k in CONTRACT_FIELDS):
                raise ValueError("ticket_contract_or_model_mismatch")
            for field in ("model_scope", "home", "away", "reference_platform", "settlement_scope"):
                if tp.get(field) != p.get(field):
                    raise ValueError("ticket_contract_or_model_mismatch")
            basis = BASES.get(tp.get("tracking_source"))
            if basis is None:
                raise ValueError("unknown_execution_basis")
            modes = bases_by_decision[t["tip_id"]]
            if "auto_assumed_placed" in modes and len(modes) > 1:
                raise ValueError("unreconciled_assumed_execution")
            if tp.get("execution_verified") is not False:
                raise ValueError("account_verification_not_supported")
            _identity(t["evidence_ref"])
            if t["currency"] != "USD":
                raise ValueError("unsupported_currency")
            available = _time(t["available_at"])
            created = _time(t["created_at"])
            if not _time(d["issued_at"]) <= created <= available <= cutoff:
                raise ValueError("ticket_unavailable_at_cutoff")
            stake, odds = _number(t["stake"]), _number(t["odds"])
            if stake <= 0 or odds <= 1:
                raise ValueError("invalid_ticket_terms")
            if basis == "assumed":
                if stake != p["stake"] or odds != p["reference_odds"] or tp.get("odds_basis") != "recommendation_snapshot":
                    raise ValueError("assumed_terms_differ_from_issuance")
                if _time(tp["recorded_at"]) != created or created >= _time(p["start"]):
                    raise ValueError("invalid_assumed_recording_time")
            else:
                _identity(tp["reported_source"])
                if tp.get("odds_basis") != "user_reported_accepted_odds" or not created <= _time(tp["reported_at"]) <= available:
                    raise ValueError("missing_accepted_execution_evidence")
            status = t["status"]
            if status == "open":
                if t["payout"] is not None or t["fee"] is not None or t.get("pnl") is not None:
                    raise ValueError("open_ticket_has_settlement")
                payout = fee = None
            elif status in SETTLED:
                if not _time(p["start"]) <= _time(t["settled_at"]) <= available:
                    raise ValueError("invalid_settlement_time")
                payout, fee = _number(t["payout"]), _number(t["fee"])
                if abs(payout - _payout(status, stake, odds)) > .011:
                    raise ValueError("payout_status_terms_mismatch")
                if "pnl" in t and not _matches_pnl(t["pnl"], payout - stake):
                    raise ValueError("ticket_pnl_payout_mismatch")
            else:
                raise ValueError("unsupported_ticket_status")
            evaluated.append({"ticket_id": identifier, "decision_id": t["tip_id"],
                              "league": p["league"], "game_id": p["game_id"],
                              "date": _time(p["start"]).astimezone(zone).date().isoformat(),
                              "issuance_date": _time(d["issued_at"]).astimezone(zone).date().isoformat(),
                              "execution_basis": basis, "status": status, "stake": stake,
                              "original_decision_status": d["status"],
                              "outcome_matches_original": status == d["status"],
                              # Report/import time is not placement time. Do not
                              # invent a placement timestamp for accepted tickets.
                              "placement_time_known": basis == "assumed",
                              "odds": odds, "payout": payout, "fees": fee,
                              "betting_pnl": payout - stake - fee if payout is not None else None,
                              "original_stake": p["stake"], "original_odds": p["reference_odds"],
                              "source_row_sha256": _hash(t)})
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({"kind": "ticket", "id": identifier, "reason": str(exc)})
    evaluated.sort(key=lambda r: (r["date"], r["league"], r["game_id"], str(r["ticket_id"])))
    missing = sorted(set(valid_decisions) - {r["decision_id"] for r in evaluated})
    incomplete = bool(rejected or missing)
    totals = _summary(evaluated, incomplete)
    issued_rows = [d for d in valid_decisions.values() if d["payload"]["stake"] > 0]
    issued = _issued_evidence(issued_rows, timezone)
    if incomplete:
        issued.update(state="invalid_records", gross_roi_interval95=None)
    return {
        "version": VERSION, "shadow_only": True, "profit_established": False,
        "as_of": as_of, "timezone": timezone, "currency": "USD",
        "input_sha256": export_hash,
        "study_sha256": _hash({"input": export_hash, "as_of": as_of, "timezone": timezone,
                               "cost": accrued_system_cost, "version": VERSION}),
        "state": totals["state"], "totals": totals,
        "execution_groups": {b: _summary([r for r in evaluated if r["execution_basis"] == b], incomplete)
                             for b in ("assumed", "accepted")},
        "issued_decision_evidence": issued,
        "original_zero_allocation_decisions": sorted(d["tip_id"] for d in valid_decisions.values()
            if d["payload"].get("original_recommendation_stake", d["payload"]["stake"]) == 0),
        "accrued_system_cost": accrued_system_cost,
        "net_pnl_after_operating_costs": totals["betting_pnl_after_stakes_and_fees"] - accrued_system_cost
            if accrued_system_cost is not None and not incomplete else None,
        "cost_state": "unknown" if accrued_system_cost is None else "supplied",
        "rows": evaluated, "excluded_records": rejected, "decisions_without_ticket_evidence": missing,
        "settlement_discrepancies": [{"ticket_id": row["ticket_id"], "decision_id": row["decision_id"],
                                        "original_status": row["original_decision_status"], "ticket_status": row["status"]}
                                      for row in evaluated if not row["outcome_matches_original"]],
        "minimum_games_for_interval": MIN_GAMES, "minimum_dates_for_interval": MIN_DAYS,
        "interval_basis": f"Whole {timezone} event dates resampled, retaining all related game selections. "
            "Descriptive 95% percentile interval; cross-day dependence and selection bias may remain.",
        "drawdown_basis": "Settled betting P&L after stakes and fees, grouped by event date; "
            "not cash-balance or intraday drawdown. Open exposure is separate.",
        "execution_basis": "Accepted means user-reported, not account-verified. Assumed returns are estimates. "
            "Totals describe only eligible exported rows, not a proven complete ledger.",
        "note": "Brier or closing-price gains do not establish betting profit. Unknown operating costs "
            "do not block betting evaluation. No stake changes, market approval or wagering is performed.",
    }
