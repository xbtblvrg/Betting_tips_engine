"""Read-only operator view for explicitly pinned OFFLINE supervision series.

No runtime wiring, callbacks, locks, ledger access or service discovery. Reuses
candidate journal freshness and history validation. This synchronous file reader
belongs in an isolated renderer, never an HTTP/SSE event loop. Caller pins are
consistency checks, not authenticated observations or release approval.
"""
from html import escape
import json

from . import development_restart_journal as journal
from . import development_supervision_cycle as cycle
from . import development_supervision_series as series

VERSION = "offline-supervision-view-v1"


def _historical_attention(state, summary):
    """Keep recorded attention distinct from cycle completion and current health.

    A completed check can report degraded sources or a failed restart request.
    Project its original reason lists, not a reassessment at the view clock.
    Missing summaries stay unknown; they never imply a clean observation.
    """
    reasons = []
    if state != "completed":
        reasons.append({"code": "cycle_" + state, "evidence": None})
    if summary is None:
        reasons.append({"code": "assessment_unavailable", "evidence": None})
    else:
        for key in ("operation_errors", "unreadable_services", "degraded_services",
                    "missed_checkpoints", "unknown_checkpoint_services",
                    "uncertain_attempts", "failed_requests"):
            if summary.get(key):
                reasons.append({"code": key, "evidence": summary[key]})
        if summary.get("attention_required") and not reasons:
            reasons.append({"code": "recorded_attention", "evidence": None})
    return reasons


def inspect_status(handle, *, as_of):
    """Project history and current journal evidence without mutating either.

    Require an explicit clock so exporting/copying evidence cannot refresh it.
    Reads are not atomic. If history is unreadable, do not use unverified service
    bindings; display unknown history and health, rather than zero failures.
    An unreadable individual journal does not hide the other services or the
    previously retained checkpoint/attempt evidence. Invalid as_of is an error.
    """
    now = journal._stamp(as_of)
    result = {"version": VERSION, "as_of": now, "state": "unknown",
              "attention_required": True, "history_error": None,
              "history": None, "services": None, "configured_services": None,
              "unconfigured_services": None, "atomic_snapshot": False,
              "source_provenance": "caller_supplied_unverified",
              "production_ready": False, "production_recovery_verified": False,
              "workflow_completion_verified": False}
    try:
        _, manifest = series._manifest(handle)
        retained = series.inspect_series(handle)
        journal._check(journal._time(now) >= journal._time(manifest["created_at"]),
                       "View predates series creation")
        rows = []
        for row in retained["cycles"]:
            start, finish = row["start"], row["result"]
            for record in (start, finish):
                if record:
                    journal._check(journal._time(record["at"]) <= journal._time(now),
                                   "View predates retained cycle evidence")
            summary = finish.get("summary") if finish else None
            reasons = _historical_attention(row["state"], summary)
            rows.append({"key": row["key"], "cycle_id": start["cycle_id"] if start else None,
                         "segment": row["segment"], "state": row["state"],
                         "started_at": start["at"] if start else None,
                         "finished_at": finish["at"] if finish else None,
                         "result_sha256": row["result_sha256"],
                         "error": finish.get("error") if finish else None,
                         "attention_required": bool(reasons), "attention_reasons": reasons,
                         "services": summary["services"] if summary else None})
        rows.sort(key=lambda r: (r["segment"], r["started_at"] or "", r["key"]))
        result["history"] = {
            "cycles": rows, "cycle_count": len(rows),
            "failed_cycle_count": len(retained["failed_cycles"]),
            "uncertain_cycle_count": len(retained["uncertain_cycles"]),
            "attention_cycle_count": sum(row["attention_required"] for row in rows),
            "historical_missed_checkpoints": retained["historical_missed_checkpoints"],
            "segments": retained["segments"], "retained_bytes": retained["retained_bytes"],
            "capacity_exhausted": retained["capacity_exhausted"],
            "next_segment": retained["next_segment"],
            "attention_required": retained["attention_required"]}
        current = cycle.inspect_services(manifest["services"], as_of=now)
        result.update(services=current["services"],
                      configured_services=current["configured_services"],
                      unconfigured_services=current["unconfigured_services"],
                      attention_required=retained["attention_required"] or current["attention_required"],
                      state="attention" if retained["attention_required"] or current["attention_required"]
                      else "no_attention_in_configured_scope")
    except Exception as exc:
        result.update(history_error=cycle._error(exc), history=None, services=None)
    return result


def _text(value):
    return escape("Unknown" if value is None else str(value), quote=True)


def _json_details(label, value):
    return ("<details><summary>" + _text(label) + "</summary><pre>" +
            _text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False)) + "</pre></details>")


def _table(headers, rows, *, identity):
    return ('<table id="' + identity + '"><thead><tr>' +
            ''.join('<th scope="col">' + _text(h) + '</th>' for h in headers) +
            '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + cell + '</td>'
            for cell in row) + '</tr>' for row in rows) + '</tbody></table>')


def render_status(handle, *, as_of):
    """Return standalone inert English HTML from a fresh read-only inspection.

    No script, network assets, forms, event handlers or action links. Every
    evidence string is escaped. Native details elements expose retained evidence
    on demand; failed/uncertain counts and checkpoint misses stay visible.
    """
    status = inspect_status(handle, as_of=as_of)
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
             'base-uri \'none\'; form-action \'none\'">',
             '<title>Offline supervision evidence</title></head><body><main>',
             '<h1>Offline supervision evidence</h1>',
             '<p>Read-only candidate view. Caller-supplied evidence is unverified. '
             'No production recovery, workflow delivery or independent review is verified.</p>',
             '<p>Evaluated as of <time>' + _text(status['as_of']) + '</time>. '
             'This saved view does not update. Reads are not an atomic snapshot.</p>',
             '<p role="status">' + ('Attention required' if status['attention_required'] else
             'No attention condition in the configured scope') + '</p>']
    if status['history_error']:
        parts += ['<p>History and service health unknown. Retained evidence could not be '
                  'validated; failure and checkpoint counts are unknown.</p>',
                  _json_details('Inspection error', status['history_error'])]
    else:
        parts += ['<p>Configured services: ' + _text(', '.join(status['configured_services'])) +
                  '. Unconfigured services: ' + _text(', '.join(status['unconfigured_services']) or 'None') +
                  '. Unconfigured services have no health assessment.</p>',
                  '<h2>Current journal assessment</h2>']
        service_rows = []
        for name, entry in status['services'].items():
            report = entry['report']
            if report is None:
                service_rows.append([_text(name), 'Unknown', 'Unknown', 'Unknown',
                                     _json_details('Journal inspection error', entry)])
                continue
            checkpoint = ('Missed' if report['checkpoint_missed'] else 'Not missed as of this view')
            checkpoint += '; persisted miss: ' + ('yes' if report['checkpoint_miss_recorded'] else 'no')
            service_rows.append([_text(name), _text(report['state']), _text(report['checkpoint_at']),
                                 _text(checkpoint), _json_details('Reason and recovery scope', {
                                     'reason': report['current_process_reason'],
                                     'engine_assessment': report.get('engine_assessment'),
                                     'recovery_scope': report['recovery_scope'],
                                     'workflow_completion_verified': False})])
        parts.append(_table(['Service', 'Process observation', 'Original checkpoint', 'Checkpoint assessment',
                             'Evidence scope'], service_rows, identity='services'))
        parts += ['<h2>Retained restart attempts</h2>',
                  '<p>Request success is an acknowledgement, not verified service recovery. '
                  'Source-degraded engine cycles remain attention conditions without restarting '
                  'a progressing process or certifying recovery. '
                  'Worker recovery observations prove heartbeat progress only. Unknown attempts '
                  'must not be replayed from this view.</p>']
        attempt_rows = []
        for name, entry in status['services'].items():
            report = entry['report']
            if report is None:
                parts.append('<p>' + _text(name) + ': attempt inventory unknown.</p>')
                continue
            for attempt in report['attempts']:
                attempt_rows.append([_text(name), _text(attempt['id']), _text(attempt['requested_at']),
                                     _text(attempt['outcome']), _text(attempt['recovery_observed_at']),
                                     _json_details('Retained attempt', attempt)])
        parts.append(_table(['Service', 'Attempt ID', 'Requested at', 'Request outcome',
                             'Scope-limited recovery observation', 'Details'], attempt_rows, identity='attempts'))
        retained = status['history']
        parts += ['<h2>Historical cycle evidence</h2>',
                  '<p>Cycles: ' + _text(retained['cycle_count']) +
                  '; failed: ' + _text(retained['failed_cycle_count']) +
                  '; uncertain or interrupted: ' + _text(retained['uncertain_cycle_count']) + '.</p>',
                  '<p>Historical checkpoint misses: ' +
                  _text(', '.join(retained['historical_missed_checkpoints']) or 'None recorded') +
                  '. Later health does not erase these misses.</p>',
                  '<p>Capacity exhausted: ' + _text(retained['capacity_exhausted']) +
                  '; next segment: ' + _text(retained['next_segment']) +
                  '; retained cycle bytes: ' + _text(retained['retained_bytes']) + '.</p>',
                  _json_details('Segment capacity', retained['segments']),
                  '<p>Historical cycles requiring attention: ' +
                  _text(retained['attention_cycle_count']) + '. '
                  'These are retained observations, not current incidents or distinct restart attempts. '
                  'A completed check does not mean healthy services. Later health does not erase '
                  'earlier attention reasons.</p>']
        attention_rows = []
        for row in retained['cycles']:
            if row['attention_required']:
                attention_rows.append([
                    _text(row['segment']), _text(row['cycle_id'] or row['key']),
                    _text(row['started_at']), _text(row['state']),
                    '<ul>' + ''.join('<li>' + _text(reason['code']) + ': ' +
                                    (_text(json.dumps(reason['evidence'], sort_keys=True, allow_nan=False))
                                     if reason['evidence'] is not None else 'See retained cycle evidence') +
                                    '</li>' for reason in row['attention_reasons']) + '</ul>'])
        parts.append(_table(['Segment', 'Cycle identity', 'Observed start', 'Cycle state',
                             'Recorded attention reasons'], attention_rows, identity='historical-attention'))
        parts.append('<details><summary>Individual cycle history</summary>')
        rows = []
        for row in retained['cycles']:
            rows.append([_text(row['segment']), _text(row['cycle_id'] or row['key']), _text(row['state']),
                         _text(row['started_at']), _text(row['finished_at']),
                         _json_details('Historical checkpoint, attempt and error evidence', row)])
        parts += [_table(['Segment', 'Cycle identity', 'State', 'Started at', 'Finished at', 'Evidence'],
                         rows, identity='cycles'), '</details>']
    parts.append('<p>This view cannot restart services, repair history, change deadlines, '
                 'approve a release or submit a wager.</p></main></body></html>')
    return ''.join(parts)
