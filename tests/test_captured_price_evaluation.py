"""Independent D17 regressions; successful prices/times are synthetic fixtures."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from betting_engine.captured_price_evaluation import FrozenCaptureReports
from betting_engine.closing_capture import capture_issued_prices

START = "2026-09-29T20:00:00+00:00"
CUTOFF = "2026-09-29T21:00:00+00:00"


def decision(identifier="held", fixture="fixture"):
    return {"family": "contract:" + identifier, "tip_id": identifier,
            "issued_at": "2026-09-29T19:00:00+00:00", "status": "lost", "pnl": -3,
            "outcome_evidence": {"basis": "synthetic result"},
            "payload": {"id": identifier, "game_id": "NBA:example", "league": "NBA",
                        "home": "Home", "away": "Away", "start": START,
                        "market": "totals", "model_scope": "full_game", "side": "over", "line": 210.5,
                        "reference_platform": "Stake.com", "reference_odds": 2.1,
                        "reference_market_id": f"stake:{fixture}:market:outcome", "stake": 3,
                        "probability": .56, "model_version": "synthetic-frozen-v1",
                        "stake_evidence": {"fixture_slug": "synthetic-event",
                                           "market_name": "Total (Incl. Overtime)",
                                           "outcome_name": "Over 210.5"}}}


def response(odds=2.0):
    return {"fixture": {"id": "fixture", "slug": "synthetic-event",
                        "startTime": int(datetime.fromisoformat(START).timestamp() * 1000),
                        "status": "active", "enabled": True, "preMatchEnabled": True,
                        "blacklisted": False},
            "groups": [{"id": "main", "name": "main", "markets": [[
                {"id": "market", "name": "Total (Incl. Overtime)", "status": "active",
                 "specifiers": "total=210.5", "outcomes": [
                     {"id": "outcome", "name": "Over 210.5", "active": True, "odds": odds}]}]]}]}


def archive(root, payload=None, observed="2026-09-29T19:59:00+00:00", endpoint="odds"):
    body = json.dumps(response() if payload is None else payload).encode()
    sha = hashlib.sha256(body).hexdigest()
    relative = f"stake_api_archive/{sha}.json.gz"
    path = root / relative
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(gzip.compress(body, mtime=0))
    url = f"https://odds-data.stake.com/{endpoint}/synthetic-event"
    identifier = hashlib.sha256((url + "\n" + observed + "\n" + sha).encode()).hexdigest()
    return {"id": identifier, "fixture_id": "fixture", "league": "NBA", "observed_at": observed,
            "archive": relative, "sha256": sha, "source_url": url}


def freeze(tmp_path, captures, decisions=None, store=None, cutoff=CUTOFF):
    store = store or FrozenCaptureReports.create(tmp_path / "evaluation.sqlite3")
    return store.freeze([decision()] if decisions is None else decisions, captures,
                        archive_root=tmp_path, as_of=cutoff)


def test_actual_capture_writer_schema_roundtrip_in_temporary_store(tmp_path):
    """Use the inspected production collector with a tiny local-only store port."""
    class ExportStore:
        path = tmp_path / "export.sqlite3"

        def system_decisions(self):
            return [decision()]

        @contextmanager
        def db(self):
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    row = archive(tmp_path)
    store = ExportStore()
    source = {"url": row["source_url"], "sha256": row["sha256"],
              "archive": row["archive"], "fetched_at": row["observed_at"]}
    audit = {"api_audit": {"fixtures": [{"fixture_id": "fixture", "league": "NBA",
                                         "responses": {"odds": source}}]}}
    result = capture_issued_prices(store, audit, datetime.fromisoformat(row["observed_at"]))
    assert result["new_captures"] == 1
    with store.db() as db:
        exported = [dict(r) for r in db.execute("SELECT * FROM closing_price_captures")]
    original_db = store.path.read_bytes()
    result = freeze(tmp_path, exported)
    report = result["reports"][0]
    assert report["status"] == "comparable"
    assert report["price_clv"] == pytest.approx(.05)
    assert report["issued_odds"] == 2.1
    assert report["decision_export"]["status"] == "lost"
    assert report["decision_export"]["pnl"] == -3
    assert report["closing_price"]["source_sha256"] == row["sha256"]
    assert report["closing_price"]["source_publication_at"] is None
    assert not report["live_source_verified"]
    assert store.path.read_bytes() == original_db


@pytest.mark.parametrize("blocker", ["suspended", "empty", "absent", "conflict", "scope", "fixture_suspended"])
def test_later_bad_capture_blocks_older_price_and_remains_durable(tmp_path, blocker):
    early = archive(tmp_path, observed="2026-09-29T19:58:00+00:00")
    payload = response()
    market = payload["groups"][0]["markets"][0][0]
    if blocker == "suspended":
        market.update(status="suspended", outcomes=[])
    elif blocker == "empty":
        market["outcomes"] = []
    elif blocker == "absent":
        payload["groups"] = []
    elif blocker == "scope":
        market.update(name="1st Half - Total", outcomes=None)
    elif blocker == "fixture_suspended":
        payload["fixture"]["status"] = "suspended"
        payload["groups"] = []
    else:
        other = deepcopy(market)
        other.update(status="suspended", outcomes=[])
        payload["groups"][0]["markets"][0].append(other)
    late = archive(tmp_path, payload)
    result = freeze(tmp_path, [early, late])
    assert result["reports"][0]["status"] == "missing_closing_price"
    assert result["reports"][0]["closing_price"] is None
    assert result["reports"][0]["price_clv"] is None
    assert result["coverage"]["decisions"] == 1
    assert result["coverage"]["status_counts"]["missing_closing_price"] == 1
    store = FrozenCaptureReports(tmp_path / "evaluation.sqlite3")
    (tmp_path / late["archive"]).unlink()
    assert store.read(result["study_id"]) == result
    assert json.loads(store.source_body(late["sha256"])) == payload


@pytest.mark.parametrize("bad", ["hash", "missing", "gzip", "backdate", "url", "path", "fixture", "schema"])
def test_invalid_capture_retained_and_never_allows_older_fallback(tmp_path, bad):
    early = archive(tmp_path, observed="2026-09-29T19:58:00+00:00")
    late = archive(tmp_path, response(1.8))
    if bad == "hash":
        (tmp_path / late["archive"]).write_bytes(gzip.compress(b'{}'))
    elif bad == "missing":
        (tmp_path / late["archive"]).unlink()
    elif bad == "gzip":
        (tmp_path / late["archive"]).write_bytes(b'not gzip')
    elif bad == "backdate":
        late["observed_at"] = "2026-09-29T19:58:30+00:00"
    elif bad == "url":
        late["source_url"] = "https://example.invalid/odds/synthetic-event"
    elif bad == "path":
        late["archive"] = "../outside.json.gz"
    elif bad == "fixture":
        payload = response(1.8)
        payload["fixture"]["id"] = "conflicting-fixture"
        late = archive(tmp_path, payload)
    else:
        late.pop("observed_at")
    result = freeze(tmp_path, [early, late])
    report = result["reports"][0]
    assert report["reason"] == "invalid_capture_evidence"
    assert report["price_clv"] is None
    assert len(report["capture_errors"]) == 1
    assert result["coverage"]["invalid_capture_rows"] == 1
    assert len(result["manifest"]["captures"]) == 2


@pytest.mark.parametrize("case,reason", [
    ("post_start", "at_or_after_start"), ("after_cutoff", "after_frozen_cutoff"),
    ("backdated_update", "source_update_after_observation"),
    ("old", "outside_closing_window"), ("pre_issue", "before_issuance")])
def test_price_times_are_not_reconstructed_from_source_update(tmp_path, case, reason):
    payload = response()
    observed = {"post_start": START, "after_cutoff": "2026-09-29T21:00:01+00:00",
                "old": "2026-09-29T19:54:59+00:00", "pre_issue": "2026-09-29T18:59:00+00:00"}.get(
                    case, "2026-09-29T19:59:00+00:00")
    payload["groups"][0]["markets"][0][0]["updatedAt"] = int(
        datetime.fromisoformat(START if case == "backdated_update" else "2026-09-29T18:00:00+00:00").timestamp() * 1000)
    result = freeze(tmp_path, [archive(tmp_path, payload, observed)])
    report = result["reports"][0]
    assert report["status"] == "missing_closing_price"
    assert report["rejection_counts"][reason] >= 1
    assert report["price_clv"] is None


def test_idempotent_across_restart_order_duplicates_and_append_only_source_changes(tmp_path):
    captures = [archive(tmp_path, response(2.2), "2026-09-29T19:58:00+00:00"), archive(tmp_path)]
    decisions = [decision(), decision("missing", "other")]
    before = deepcopy((decisions, captures))
    result = freeze(tmp_path, captures, decisions)
    store = FrozenCaptureReports(tmp_path / "evaluation.sqlite3")
    repeat = freeze(tmp_path, [*captures[::-1], captures[0]], [*decisions[::-1], decisions[0]], store)
    assert repeat == result
    assert (decisions, captures) == before
    assert result["coverage"]["comparable_fraction"] == .5
    assert result["coverage"]["status_counts"]["missing_closing_price"] == 1
    late = archive(tmp_path, response(2.5), "2026-09-29T19:59:30+00:00")
    changed = freeze(tmp_path, [*captures, late], decisions, store)
    assert changed["study_id"] != result["study_id"]
    assert changed["reports"][0]["price_clv"] == pytest.approx(-.16)
    assert store.read(result["study_id"]) == result
    assert result["reports"][0]["price_clv"] == pytest.approx(.05)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM studies").fetchone()[0] == 2
        for table in ("issuances", "studies", "decision_reports", "source_blobs"):
            with pytest.raises(sqlite3.IntegrityError, match="frozen_evidence"):
                db.execute(f"DELETE FROM {table}")


@pytest.mark.parametrize("field,value", [("reference_odds", 3.0), ("stake", 4), ("probability", .9), ("line", 211.5)])
def test_changed_issuance_is_rejected_atomically_without_rewriting_reports(tmp_path, field, value):
    captures = [archive(tmp_path)]
    result = freeze(tmp_path, captures)
    store = FrozenCaptureReports(tmp_path / "evaluation.sqlite3")
    altered = decision()
    altered["payload"][field] = value
    with pytest.raises(ValueError, match="immutable_issuance_conflict"):
        freeze(tmp_path, captures, [decision("aaa_new"), altered], store)
    assert store.read(result["study_id"]) == result
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM issuances").fetchone()[0] == 1


def test_later_settlement_export_is_versioned_and_never_erases_losing_record(tmp_path):
    captures = [archive(tmp_path)]
    result = freeze(tmp_path, captures)
    store = FrozenCaptureReports(tmp_path / "evaluation.sqlite3")
    revised = decision()
    revised.update(status="won", pnl=3.3)
    changed = freeze(tmp_path, captures, [revised], store)
    assert changed["study_id"] != result["study_id"]
    assert changed["reports"][0]["comparison_sha256"] == result["reports"][0]["comparison_sha256"]
    assert store.read(result["study_id"])["reports"][0]["decision_export"]["pnl"] == -3


def test_invalid_and_pending_decisions_stay_in_denominator_and_empty_is_not_success(tmp_path):
    bad = decision("bad")
    bad["payload"]["reference_odds"] = 1
    pending = decision("pending")
    pending["payload"]["start"] = "2026-09-30T20:00:00+00:00"
    result = freeze(tmp_path, [], [decision(), bad, pending])
    assert result["coverage"]["status_counts"] == {
        "comparable": 0, "missing_closing_price": 1, "invalid_decision": 1, "pending_start": 1}
    assert result["coverage"]["decisions"] == 3
    store = FrozenCaptureReports(tmp_path / "evaluation.sqlite3")
    empty = freeze(tmp_path, [], [], store)
    assert empty["coverage"]["comparable_fraction"] is None


def test_existing_database_is_not_modified_or_accepted_as_report_store(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE system_decisions(payload TEXT)")
        db.execute("INSERT INTO system_decisions VALUES ('loss')")
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        FrozenCaptureReports.create(path)
    with pytest.raises(ValueError, match="not_an_isolated"):
        FrozenCaptureReports(path)
    assert path.read_bytes() == before


def test_symlink_outside_archive_copy_rejected(tmp_path):
    row = archive(tmp_path)
    original = tmp_path / row["archive"]
    outside = tmp_path.parent / (tmp_path.name + "-outside.gz")
    outside.write_bytes(original.read_bytes())
    try:
        original.unlink()
        original.symlink_to(outside)
        result = freeze(tmp_path, [row])
        assert result["reports"][0]["capture_errors"][0]["reason"] == "archive_outside_export_root"
    finally:
        outside.unlink()


def test_supplied_public_observation_cannot_supply_unavailable_raw_capture(tmp_path):
    row = json.loads((Path(__file__).parent / "fixtures/closing_catalog_observation.json").read_text())
    source = row["source"]
    sha, observed, url = source["sha256"], source["fetched_at"], source["url"]
    capture = {"id": hashlib.sha256((url + "\n" + observed + "\n" + sha).encode()).hexdigest(),
               "fixture_id": row["fixture_id"], "league": row["league"], "observed_at": observed,
               "source_url": url, "sha256": sha, "archive": f"stake_api_archive/{sha}.json.gz"}
    # No real decision or unavailable response is fabricated. Retain the failed
    # source in an empty study; this is not a real measured CLV observation.
    result = freeze(tmp_path, [capture], [])
    assert result["manifest"]["captures"][0]["error"] == "archive_unavailable"
    assert result["coverage"]["decisions"] == 0
    assert result["coverage"]["invalid_capture_rows"] == 1


def test_simultaneous_fixture_and_odds_conflict_is_not_collapsed(tmp_path):
    captures = [archive(tmp_path, response(2.0)), archive(tmp_path, response(2.5), endpoint="fixtures")]
    result = freeze(tmp_path, captures)
    assert result["reports"][0]["reason"] == "conflicting_latest_snapshots"
    store = FrozenCaptureReports(tmp_path / "evaluation.sqlite3")
    assert freeze(tmp_path, captures[::-1], store=store) == result
    recovery = archive(tmp_path, response(2.2), "2026-09-29T19:59:30+00:00")
    later = freeze(tmp_path, [*captures, recovery], store=store)
    assert later["reports"][0]["price_clv"] == pytest.approx(2.1 / 2.2 - 1)
    assert store.read(result["study_id"])["reports"][0]["price_clv"] is None


@pytest.mark.parametrize("change", ["fixture", "league", "duplicate_id"])
def test_conflicting_routing_metadata_cannot_hide_blocker(tmp_path, change):
    early = archive(tmp_path, observed="2026-09-29T19:58:00+00:00")
    payload = response(1.8)
    payload["groups"] = []
    late = archive(tmp_path, payload)
    captures = [early, late]
    if change == "fixture":
        late["fixture_id"] = "wrong-fixture"
    elif change == "league":
        late["league"] = "NFL"
    else:
        captures.append({**late, "fixture_id": "wrong-fixture"})
    result = freeze(tmp_path, captures)
    assert result["reports"][0]["reason"] == "invalid_capture_evidence"
    assert result["reports"][0]["price_clv"] is None


def test_damaged_compressed_archive_is_frozen_for_diagnosis(tmp_path):
    capture = archive(tmp_path)
    damaged = b"not a gzip file"
    (tmp_path / capture["archive"]).write_bytes(damaged)
    result = freeze(tmp_path, [capture])
    store = FrozenCaptureReports(tmp_path / "evaluation.sqlite3")
    evidence = result["manifest"]["captures"][0]
    assert evidence["error"] == "invalid_gzip_archive"
    assert store.source_body(evidence["archive_bytes_sha256"]) == damaged


def test_export_conflict_and_bad_envelope_fail_explicitly_without_partial_cohort(tmp_path):
    store = FrozenCaptureReports.create(tmp_path / "evaluation.sqlite3")
    other = decision()
    other["pnl"] = 100
    with pytest.raises(ValueError, match="conflicting_decision_exports"):
        freeze(tmp_path, [], [decision(), other], store)
    with pytest.raises(ValueError, match="invalid_decision_export_envelope"):
        freeze(tmp_path, [], [decision(), {"tip_id": "broken"}], store)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM studies").fetchone()[0] == 0


def test_unrelated_failed_capture_does_not_hide_valid_other_event(tmp_path):
    good = archive(tmp_path)
    payload = response()
    payload["fixture"]["id"] = "other"
    unrelated = archive(tmp_path, payload)
    unrelated["fixture_id"] = "other"
    # A genuinely unrelated event must also have a different event URL. A
    # conflicting ID on the held event's URL is the review's P2 blocker case.
    unrelated['source_url']='https://odds-data.stake.com/odds/other-event'
    unrelated['id']=hashlib.sha256((unrelated['source_url']+'\n'+unrelated['observed_at']+'\n'+unrelated['sha256']).encode()).hexdigest()
    (tmp_path / unrelated["archive"]).unlink()
    result = freeze(tmp_path, [good, unrelated])
    assert result["reports"][0]["status"] == "comparable"
    assert result["coverage"]["invalid_capture_rows"] == 1
