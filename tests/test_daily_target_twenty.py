"""Active target migration preserves the meaning of original funded decisions."""
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from betting_engine import server, storage
from betting_engine.domain import utcnow
from test_autotracking import store, tip
from test_operations import publication


def test_twenty_target_preserves_originals_checkpoint_and_distinct_first_issuances(tmp_path, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(storage, 'utcnow', lambda: now)
    s = store(tmp_path, daily_risk_limit=None, max_bet=5)
    rows = [tip(id=f'original-{n}', game_id=f'NBA:{n}') for n in range(3)]
    s.publish({'recommendations': rows})
    checkpoint = s.reset_daily_progress(now.isoformat(), release_id='original', reason='Explicit prior user instruction')
    original_bets, original_decisions = s.bets(), s.system_decisions()
    with s.db() as db:
        issues = [tuple(r) for r in db.execute('SELECT * FROM recommendation_issuance')]
    before = s.daily_progress()
    s.save_settings({'daily_pick_target': 20})
    after = s.daily_progress()
    assert after['target'] == 20 and after['shortfall'] == 20 - after['issued']
    assert {k:v for k,v in after.items() if k not in {'target','shortfall'}} == {k:v for k,v in before.items() if k not in {'target','shortfall'}}
    assert after['checkpoint'] == checkpoint
    assert s.bets() == original_bets and s.system_decisions() == original_decisions
    with s.db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM recommendation_issuance')] == issues
    # Redisplaying/repricing and zero-stake research cannot fulfil the raised goal.
    s.publish({'recommendations': [*rows, tip(id='research', game_id='NBA:research', stake=0)]})
    assert s.daily_progress()['issued'] == before['issued']
    now += timedelta(seconds=1)
    s.publish({'recommendations': [tip(id='genuinely-new', game_id='NBA:new')]})
    assert s.daily_progress()['issued'] == before['issued'] + 1
    assert s.settings()['daily_risk_limit'] is None and s.settings()['max_bet'] == 5


def test_partial_settings_api_preserves_twenty_and_funding_policy(tmp_path, monkeypatch):
    s = store(tmp_path, daily_pick_target=20, daily_risk_limit=None,
              recommendation_policy='continuous_best_available_v1',
              model_pick_sizing_policy='all_tiers_weighted_v3')
    monkeypatch.setattr(server.engine, 'store', s)
    monkeypatch.setattr(server.engine, 'refresh', lambda: True)
    client = TestClient(server.app)
    for values in ({'unit_stake': 2}, {'bankroll': None}, {}):
        response = client.put('/api/settings', json=values)
        assert response.status_code == 200
        actual = response.json()
        assert actual['daily_pick_target'] == 20 and actual['daily_risk_limit'] is None
        assert actual['max_bet'] == 5
        assert actual['recommendation_policy'] == 'continuous_best_available_v1'
        assert actual['model_pick_sizing_policy'] == 'all_tiers_weighted_v3'
    assert storage.Store(s.path).daily_progress()['target'] == 20
    # An old open form cannot silently restore the superseded target15.
    for lower in (1,15,19):
        assert client.put('/api/settings',json={'daily_pick_target':lower}).status_code == 422
        assert s.settings()['daily_pick_target'] == 20
    assert client.put('/api/settings',json={'daily_pick_target':25}).json()['daily_pick_target'] == 25


def test_delivery_shortfall_reports_twenty_without_fabricating_picks(tmp_path):
    from betting_engine.operations import operating_audit
    from betting_engine.engineering_loop import _collect
    s = store(tmp_path, daily_pick_target=20)
    p = publication()
    progress = s.daily_progress()
    audit = operating_audit(p, progress)
    found, _, _ = _collect(tmp_path, p, audit, {}, {'tasks':[]}, {}, utcnow())
    gap = found['SYSTEM:daily_delivery']
    assert gap['evidence']['shortfall'] == 20
    assert gap['bucket'] == 'delivery'
    assert s.bets() == [] and progress['issued'] == 0
