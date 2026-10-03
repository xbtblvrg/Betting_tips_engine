"""The forty-instruction objective changes the target, never the ledger/count."""
from fastapi.testclient import TestClient
from betting_engine import server, storage
from test_autotracking import store, tip


def test_forty_target_preserves_checkpoint_and_original_issuance(tmp_path, monkeypatch):
    s = store(tmp_path, daily_pick_target=20, max_bet=5, daily_risk_limit=None)
    s.publish({'recommendations': [tip(id='original', game_id='NBA:original')]})
    before = s.daily_progress()
    originals = s.system_decisions()
    tickets = s.bets()
    monkeypatch.setattr(server.engine, 'store', s)
    monkeypatch.setattr(server.engine, 'refresh', lambda: True)
    client = TestClient(server.app)
    assert client.put('/api/settings', json={'daily_pick_target':40}).status_code == 200
    after = s.daily_progress()
    assert after['target'] == 40 and after['shortfall'] == 40 - after['issued']
    assert {k:v for k,v in before.items() if k not in ('target','shortfall')} == {k:v for k,v in after.items() if k not in ('target','shortfall')}
    assert s.system_decisions() == originals and s.bets() == tickets
    assert client.put('/api/settings',json={'unit_stake':1}).json()['daily_pick_target'] == 40
    assert storage.Store(s.path).settings()['daily_pick_target'] == 40
    assert s.settings()['daily_risk_limit'] is None and s.settings()['max_bet'] == 5


def test_old_settings_form_cannot_lower_active_forty_target(tmp_path,monkeypatch):
    s=store(tmp_path,daily_pick_target=40)
    monkeypatch.setattr(server.engine,'store',s)
    monkeypatch.setattr(server.engine,'refresh',lambda:True)
    client=TestClient(server.app)
    for lower in (20,25,39):
        assert client.put('/api/settings',json={'daily_pick_target':lower}).status_code==422
        assert s.settings()['daily_pick_target']==40
    assert client.put('/api/settings',json={'daily_pick_target':45}).json()['daily_pick_target']==45


def test_old_settings_form_cannot_lower_active_hundred_target(tmp_path,monkeypatch):
    """A stale form must not silently undo the activated 100-issuance target."""
    s=store(tmp_path,daily_pick_target=100)
    monkeypatch.setattr(server.engine,'store',s)
    monkeypatch.setattr(server.engine,'refresh',lambda:True)
    client=TestClient(server.app)
    for lower in (20,40,99):
        assert client.put('/api/settings',json={'daily_pick_target':lower}).status_code==422
        assert s.settings()['daily_pick_target']==100
    assert client.put('/api/settings',json={'unit_stake':1}).json()['daily_pick_target']==100
