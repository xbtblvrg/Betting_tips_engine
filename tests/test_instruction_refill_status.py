"""Read-only status must never claim a scan or modify the wager ledger."""
from fastapi.testclient import TestClient
from betting_engine import server
from betting_engine.storage import Store


def test_refill_status_is_read_only_and_distinguishes_unrequested_from_failure(tmp_path, monkeypatch):
    store = Store(tmp_path / 'engine.sqlite3')
    monkeypatch.setattr(server.engine, 'store', store)
    client = TestClient(server.app)
    assert client.get('/api/instruction-refill').json() == {
        'version': 'instruction-refill-v1', 'state': 'not_requested'}
    with store.db() as db:
        db.execute("INSERT INTO instruction_refill_requests(requested_at,state,payload) VALUES (?,?,?)",
                   ('2026-10-01T11:00:00+00:00', 'failed',
                    '{"version":"instruction-refill-v1","error":"source interrupted"}'))
        before = list(db.iterdump())
    for _ in range(2):
        response = client.get('/api/instruction-refill')
        assert response.status_code == 200
        assert response.json()['state'] == 'failed'
        assert response.json()['error'] == 'source interrupted'
    with store.db() as db:
        assert list(db.iterdump()) == before
