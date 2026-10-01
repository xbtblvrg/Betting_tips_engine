"""Offline D17 integration. Only explicitly supplied exports and copied archives.

No Store, production paths, clock, fetching, settlement or publication integration.
Create a NEW isolated database with FrozenCaptureReports.create(path); reopening
requires this module's schema. Reports and evidence are append-only study versions.
"""
from collections import Counter
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import zlib

from .closing_prices import VERSION as EVALUATOR_VERSION
from .closing_prices import _time, evaluate_closing_price, snapshots_from_response

VERSION = "captured-price-study-v2"
CORE = ("family", "tip_id", "issued_at", "payload")
CAPTURE_FIELDS = ("id", "fixture_id", "league", "observed_at", "archive", "sha256", "source_url")
TABLES = {"issuances", "source_blobs", "studies", "decision_reports"}
APPLICATION_ID = 0x44313743


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _digest(value):
    return _sha(_json(value).encode())


def _capture(row, root):
    """Retain failures as evidence; never silently drop a corrupted observation."""
    evidence = {"capture": row, "error": None, "body_sha256": None}
    blobs = {}
    try:
        if any(not isinstance(row.get(k), str) or not row[k] for k in CAPTURE_FIELDS):
            raise ValueError("invalid_capture_schema")
        _time(row["observed_at"])
        sha = row["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or row["archive"] != f"stake_api_archive/{sha}.json.gz":
            raise ValueError("invalid_archive_identity")
        identity = _sha((row["source_url"] + "\n" + row["observed_at"] + "\n" + sha).encode())
        if row["id"] != identity:
            raise ValueError("capture_identity_mismatch")
        path = (root / row["archive"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("archive_outside_export_root")
        try:
            compressed = path.read_bytes()
        except OSError:
            raise ValueError("archive_unavailable") from None
        evidence["archive_bytes_sha256"] = _sha(compressed)
        blobs[_sha(compressed)] = compressed
        try:
            body = gzip.decompress(compressed)
        except (OSError, EOFError, zlib.error):
            raise ValueError("invalid_gzip_archive") from None
        evidence["body_sha256"] = _sha(body)
        blobs[_sha(body)] = body
        if evidence["body_sha256"] != sha:
            raise ValueError("response_hash_mismatch")
        payload = json.loads(body)
        evidence["archived_fixture_id"] = payload["fixture"]["id"]
        if payload["fixture"]["id"] != row["fixture_id"]:
            raise ValueError("capture_fixture_mismatch")
        source = {"url": row["source_url"], "fetched_at": row["observed_at"],
                  "sha256": sha, "archive": row["archive"]}
        snapshots = snapshots_from_response(body, source, league=row["league"])
        return evidence, blobs, snapshots
    except (ValueError, KeyError, TypeError, OverflowError, OSError, AttributeError) as exc:
        evidence["error"] = str(exc)
        return evidence, blobs, []


def _relevant(capture, decision):
    """Unknown routing fails closed; a verified unrelated event stays separate."""
    tip = decision["payload"]
    ids = str(tip.get("reference_market_id", "")).split(":")
    fixture = ids[1] if len(ids) == 4 else None
    # Fixture IDs are exact provider identifiers. A conflicting league label
    # for the SAME fixture must not hide a potentially blocking observation.
    slug=tip.get('stake_evidence',{}).get('fixture_slug')
    event_urls={f'https://odds-data.stake.com/{part}/{slug}' for part in ('fixtures','odds')} if slug else set()
    return (not capture.get("fixture_id") or not fixture or capture["fixture_id"] == fixture
            or capture.get('source_url') in event_urls)


def _evidence_relevant(item, decision):
    return (_relevant(item["capture"], decision)
            or (item.get("archived_fixture_id") is not None
                and _relevant({"fixture_id": item["archived_fixture_id"]}, decision)))


class FrozenCaptureReports:
    """Dedicated SQLite artifact, never an engine/wager database.

    Inputs are decoded system_decisions exports and closing_price_captures row
    exports (dicts). archive_root is an explicitly supplied local archive copy.
    No source database is opened. No environment/default data paths are used.
    """

    @classmethod
    def create(cls, path):
        path = Path(path)
        # Exclusive creation prevents even schema writes to an existing ledger.
        with path.open("xb"):
            pass
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(f"""
                PRAGMA application_id={APPLICATION_ID};
                CREATE TABLE issuances(family TEXT PRIMARY KEY, tip_id TEXT UNIQUE NOT NULL,
                    core_json TEXT NOT NULL);
                CREATE TABLE source_blobs(sha256 TEXT PRIMARY KEY, body BLOB NOT NULL);
                CREATE TABLE studies(id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL,
                    coverage_json TEXT NOT NULL);
                CREATE TABLE decision_reports(study_id TEXT NOT NULL REFERENCES studies(id),
                    family TEXT NOT NULL REFERENCES issuances(family), report_json TEXT NOT NULL,
                    PRIMARY KEY(study_id,family));
            """)
            for table in sorted(TABLES):
                for operation in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER frozen_{table}_{operation} BEFORE {operation} ON {table} "
                               "BEGIN SELECT RAISE(ABORT, 'frozen_evidence'); END")
        return cls(path)

    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        # Read-only inspection before permitting any writes on reopen.
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID or tables != TABLES:
                raise ValueError("not_an_isolated_capture_report_store")

    def _db(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def freeze(self, decisions, captures, *, archive_root, as_of):
        """Atomically freeze a whole cohort, including missing and invalid closes.

        Identical inputs are idempotent irrespective of order/duplicate rows.
        Changed capture evidence, cutoff or settlement exports creates a new
        study, never overwrites a report. Changed issuance fields are rejected.
        Malformed export envelopes fail the batch explicitly (no silent loss).
        """
        cutoff = _time(as_of).isoformat()
        decisions = json.loads(_json(list(decisions)))
        captures = json.loads(_json(list(captures)))
        by_family = {}
        for decision in decisions:
            if (not isinstance(decision, dict) or any(k not in decision for k in CORE)
                    or not isinstance(decision["payload"], dict)
                    or any(not isinstance(decision[k], str) or not decision[k]
                           for k in ("family", "tip_id", "issued_at"))):
                raise ValueError("invalid_decision_export_envelope")
            family = decision["family"]
            if family in by_family and by_family[family] != decision:
                raise ValueError("conflicting_decision_exports")
            by_family[family] = decision
        decisions = [by_family[k] for k in sorted(by_family)]
        if any(not isinstance(row, dict) for row in captures):
            raise ValueError("invalid_capture_export_envelope")
        captures = [json.loads(row) for row in sorted({_json(row) for row in captures})]
        root = Path(archive_root).resolve(strict=True)
        evidence, bodies, rows = [], {}, []
        id_counts = Counter(row.get("id") for row in captures if isinstance(row.get("id"), str))
        for capture in captures:
            item, blobs, snapshots = _capture(capture, root)
            if isinstance(capture.get("id"), str) and id_counts[capture["id"]] > 1:
                item["error"] = "conflicting_capture_identity"
                snapshots = []
            evidence.append(item)
            bodies.update(blobs)
            rows.extend(snapshots)
        manifest = {"version": VERSION, "evaluator_version": EVALUATOR_VERSION,
                    "as_of": cutoff, "decision_exports": decisions, "captures": evidence}
        study_id = _digest(manifest)
        reports = []
        for decision in decisions:
            relevant_rows = [row for row in rows if _relevant(row, decision)]
            report = evaluate_closing_price(decision, relevant_rows, as_of=cutoff)
            errors = [{"capture": item["capture"], "reason": item["error"]}
                      for item in evidence if item["error"] and _evidence_relevant(item, decision)]
            errors.extend({"capture": item["capture"], "reason": "capture_league_mismatch"}
                          for item in evidence if not item["error"] and _evidence_relevant(item, decision)
                          and item["capture"]["league"] != decision["payload"].get("league"))
            # A same-event revised schedule cannot disappear before blocker
            # handling just because the pure evaluator needs exact start time.
            # We cannot choose which conflicting schedule is the true close.
            for row in relevant_rows:
                try:
                    if (_time(row['source']['fetched_at']) <= _time(cutoff)
                            and _time(row['start']) != _time(decision['payload']['start'])):
                        errors.append({'capture':row['source'],'reason':'conflicting_fixture_start'})
                except (KeyError,TypeError,ValueError):
                    errors.append({'capture':row.get('source',{}),'reason':'invalid_schedule_evidence'})
            if errors and report["status"] not in ("invalid_decision", "pending_start"):
                # Unreadable evidence cannot establish whether a later capture
                # suspended this exact market. Never fall back through it.
                report.update(status="missing_closing_price", reason="invalid_capture_evidence",
                              closing_price=None, price_clv=None)
                report["comparison_sha256"] = _digest({k: report[k] for k in
                    ("version", "as_of", "decision_sha256", "status", "reason", "closing_price", "price_clv")})
            report.update(study_id=study_id, integration_version=VERSION,
                          capture_errors=errors, decision_export=decision,
                          source_capture_ids=[item["capture"].get("id") for item in evidence
                                              if _evidence_relevant(item, decision)],
                          timestamp_authentication="export_claim_only",
                          interpretation="Price diagnostic; not realized ROI or profit.")
            report["report_sha256"] = _digest(report)
            reports.append(report)
        statuses = Counter(report["status"] for report in reports)
        coverage = {"decisions": len(decisions),
                    "status_counts": {key: statuses[key] for key in
                                      ("comparable", "missing_closing_price", "invalid_decision", "pending_start")},
                    "comparable_fraction": statuses["comparable"] / len(decisions) if decisions else None,
                    "reason_counts": dict(Counter(r["reason"] for r in reports if r.get("reason"))),
                    "capture_rows": len(captures), "invalid_capture_rows": sum(bool(e["error"]) for e in evidence),
                    "unit": "distinct exported decisions, not tickets or independent event observations",
                    "live_source_verified": False, "account_execution_verified": False}
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for decision in decisions:
                core = _json({k: decision[k] for k in CORE})
                previous = db.execute("SELECT core_json FROM issuances WHERE family=?", (decision["family"],)).fetchone()
                if previous and previous[0] != core:
                    raise ValueError("immutable_issuance_conflict")
                if not previous:
                    db.execute("INSERT INTO issuances VALUES (?,?,?)", (decision["family"], decision["tip_id"], core))
            existing = db.execute("SELECT 1 FROM studies WHERE id=?", (study_id,)).fetchone()
            if not existing:
                for sha, body in sorted(bodies.items()):
                    db.execute("INSERT OR IGNORE INTO source_blobs VALUES (?,?)", (sha, body))
                db.execute("INSERT INTO studies VALUES (?,?,?)", (study_id, _json(manifest), _json(coverage)))
                db.executemany("INSERT INTO decision_reports VALUES (?,?,?)",
                               [(study_id, d["family"], _json(r)) for d, r in zip(decisions, reports)])
        return self.read(study_id)

    def read(self, study_id):
        """Read frozen artifacts without consulting mutable input archives."""
        with closing(self._db()) as db:
            study = db.execute("SELECT manifest_json,coverage_json FROM studies WHERE id=?", (study_id,)).fetchone()
            if study is None:
                raise KeyError(study_id)
            reports = [json.loads(r[0]) for r in db.execute(
                "SELECT report_json FROM decision_reports WHERE study_id=? ORDER BY family", (study_id,))]
        manifest = json.loads(study[0])
        if _digest(manifest) != study_id:
            raise ValueError("stored_manifest_hash_mismatch")
        if len(reports) != len(manifest["decision_exports"]):
            raise ValueError("stored_report_count_mismatch")
        for report, decision in zip(reports, manifest["decision_exports"]):
            if (report.get("study_id") != study_id or report.get("decision_export") != decision
                    or report.get("report_sha256") != _digest({k: v for k, v in report.items()
                                                               if k != "report_sha256"})):
                raise ValueError("stored_report_hash_mismatch")
        return {"study_id": study_id, "manifest": manifest,
                "coverage": json.loads(study[1]), "reports": reports}

    def source_body(self, sha256):
        with closing(self._db()) as db:
            row = db.execute("SELECT body FROM source_blobs WHERE sha256=?", (sha256,)).fetchone()
        if row is None:
            raise KeyError(sha256)
        if _sha(row[0]) != sha256:
            raise ValueError("stored_source_hash_mismatch")
        return row[0]
