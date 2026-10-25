import json
import sqlite3
import pytest
from pathlib import Path
from starlette.testclient import TestClient

from betting_engine.stake_account import (
    get_masked_token,
    get_stake_account_summary,
    update_stake_account_config,
)
from betting_engine.server import app


def test_get_masked_token():
    assert get_masked_token(None) == "Not configured"
    assert get_masked_token("") == "Not configured"
    assert get_masked_token("short") == "sh••••••"
    masked = get_masked_token("ecb294abcdef1234567890407")
    assert masked.startswith("ecb294")
    assert masked.endswith("0407")
    assert "•" in masked
    assert "abcdef" not in masked


def test_stake_account_summary_and_update(tmp_path, monkeypatch):
    monkeypatch.setenv("STAKE_DISABLE_SAFARI_SYNC", "1")
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE bets (
            id TEXT PRIMARY KEY,
            tip_id TEXT,
            stake REAL,
            odds REAL,
            status TEXT,
            pnl REAL,
            payload TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE kv (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Insert open bets
    cur.execute(
        "INSERT INTO bets VALUES ('b1', 't1', 5.0, 1.9, 'open', NULL, ?)",
        (json.dumps({'league': 'NFL'}),),
    )
    cur.execute(
        "INSERT INTO bets VALUES ('b2', 't2', 3.5, 2.0, 'open', NULL, ?)",
        (json.dumps({'league': 'NFL'}),),
    )
    cur.execute(
        "INSERT INTO bets VALUES ('b3', 't3', 2.0, 1.8, 'open', NULL, ?)",
        (json.dumps({'league': 'WTA'}),),
    )

    # Insert settled bets
    cur.execute(
        "INSERT INTO bets VALUES ('b4', 't4', 2.0, 2.1, 'won', 2.2, ?)",
        (json.dumps({'league': 'NFL'}),),
    )
    cur.execute(
        "INSERT INTO bets VALUES ('b5', 't5', 3.0, 1.9, 'lost', -3.0, ?)",
        (json.dumps({'league': 'NBA'}),),
    )
    conn.commit()

    # Initial summary with default bankroll (1000)
    summary = get_stake_account_summary(conn)
    assert summary["status"] == "ok"
    assert summary["open_bets_count"] == 3
    assert summary["open_exposure"] == 10.5
    assert summary["settled_bets_count"] == 2
    assert summary["settled_pnl"] == -0.8
    assert summary["base_bankroll"] == 1000.0
    assert summary["total_balance"] == 999.2
    assert summary["available_balance"] == round(999.2 - 10.5, 2)
    assert summary["by_league"]["NFL"]["count"] == 2
    assert summary["by_league"]["NFL"]["exposure"] == 8.5
    assert summary["by_league"]["WTA"]["count"] == 1
    assert summary["by_league"]["WTA"]["exposure"] == 2.0

    # Update base bankroll
    updated = update_stake_account_config(conn, 1500.0)
    assert updated["base_bankroll"] == 1500.0
    assert updated["total_balance"] == 1499.2
    assert updated["available_balance"] == round(1499.2 - 10.5, 2)

    with pytest.raises(ValueError):
        update_stake_account_config(conn, -50.0)


def test_api_stake_account_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("STAKE_DISABLE_SAFARI_SYNC", "1")
    from betting_engine.server import engine
    from betting_engine.storage import Store
    test_store = Store(tmp_path / "test.sqlite")
    monkeypatch.setattr(engine, "store", test_store)
    client = TestClient(app)
    # Test GET
    resp = client.get("/api/stake-account")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "total_balance" in data
    assert "available_balance" in data
    assert "open_exposure" in data
    assert "open_bets_count" in data
    assert "token_masked" in data

    # Test POST update base bankroll
    post_resp = client.post("/api/stake-account", json={"base_bankroll": 1250.0})
    assert post_resp.status_code == 200
    post_data = post_resp.json()
    assert post_data["base_bankroll"] == 1250.0
    assert post_data["total_balance"] == round(1250.0 + post_data["settled_pnl"], 2)

    # Test invalid payload (negative)
    err_resp = client.post("/api/stake-account", json={"base_bankroll": -10.0})
    assert err_resp.status_code in (422, 400)


def test_sync_live_stake_from_safari_mocked(monkeypatch):
    import subprocess
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")

    mock_user_data = {
        "data": {
            "user": {
                "id": "mock-uid-123",
                "name": "blvrg",
                "balances": [
                    {"available": {"amount": 313.00, "currency": "usdt"}, "vault": {"amount": 0.0, "currency": "usdt"}},
                    {"available": {"amount": 128.94, "currency": "usdc"}, "vault": {"amount": 0.0, "currency": "usdc"}},
                ]
            }
        }
    }
    mock_bets_data = {
        "data": {
            "user": {
                "activeSportBets": [
                    {"id": "b1", "amount": 10.0, "currency": "usdt", "status": "active", "outcomes": [{"sport": {"name": "American Football"}, "fixture": {"name": "Bills - Chiefs", "tournament": {"name": "NFL"}}}]},
                    {"id": "b2", "amount": 15.0, "currency": "usdt", "status": "active", "outcomes": [{"sport": {"name": "Tennis"}, "fixture": {"name": "Player A - Player B", "tournament": {"name": "WTA Wuhan"}}}]},
                ]
            }
        }
    }

    combined_user = dict(mock_user_data["data"]["user"])
    mock_bets = mock_bets_data["data"]["user"]["activeSportBets"]

    def mock_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        class Res:
            if 'application "Safari" is running' in cmd_str:
                stdout = "true"
            else:
                stdout = json.dumps({
                    "user": combined_user,
                    "bets": mock_bets,
                })
            stderr = ""
        return Res()

    monkeypatch.setattr(subprocess, "run", mock_run)
    from betting_engine.stake_account import sync_live_stake_from_safari, get_stake_balance_history
    res = sync_live_stake_from_safari(conn)
    assert res is not None
    assert res["username"] == "blvrg"
    assert res["total_balance"] == round(313.00 + 128.94 + 25.0, 2)
    assert res["available_balance"] == round(313.00 + 128.94, 2)
    assert res["open_exposure"] == 25.0
    assert res["open_bets_count"] == 2
    assert res["by_league"]["WTA"]["count"] == 1
    assert res["by_league"]["WTA"]["exposure"] == 15.0
    assert res["by_league"]["NFL"]["count"] == 1
    assert res["by_league"]["NFL"]["exposure"] == 10.0

    # Verify history snapshot was recorded
    history = get_stake_balance_history(conn, period="all")
    assert history["period"] == "all"
    assert history["summary"]["current_balance"] == round(313.00 + 128.94 + 25.0, 2)
    assert len(history["points"]) >= 1


def test_stage_betslip_validation():
    from betting_engine.stake_account import stage_betslip_in_safari
    # Invalid URL
    res = stage_betslip_in_safari("https://google.com", "Team A", 2.50)
    assert res["status"] == "error"
    assert "Invalid Stake sports event URL" in res["message"]

    # Invalid stake
    res = stage_betslip_in_safari("https://stake.com/sports/soccer/match", "Team A", -5.0)
    assert res["status"] == "error"
    assert "Invalid stake amount" in res["message"]

    res = stage_betslip_in_safari("https://stake.com/sports/soccer/match", "Team A", 100.0)
    assert res["status"] == "error"
    assert "Invalid stake amount" in res["message"]


def test_stage_betslip_mock_safari(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari
    
    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "OPENED_NEW_TAB"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "outcomesCount": 10, "title": "Match Title"})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Team A", "current_odds": 1.95})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "2.50", "payout": "4.87500000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari("https://stake.com/sports/soccer/match-slug", "Team A", 2.50, odds=1.95)
    assert res["status"] == "ok"
    assert res["selection"] == "Team A"
    assert res["stake"] == 2.50
    assert res["odds"] == 1.95
    assert res["payout"] == "4.87500000"


def test_api_stage_betslip_endpoint(monkeypatch):
    import subprocess
    client = TestClient(app)
    
    # 1. Invalid payload
    resp = client.post("/api/stage-betslip", json={
        "event_url": "https://malicious.com",
        "selection": "Team A",
        "stake": 2.50
    })
    assert resp.status_code == 400

    # 2. Mocked successful staging
    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "outcomesCount": 5, "title": "Game"})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Over 2.5", "current_odds": 2.10})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "3.00", "payout": "6.30000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = client.post("/api/stage-betslip", json={
        "event_url": "https://stake.com/sports/soccer/england/match-123",
        "selection": "Over 2.5",
        "stake": 3.00,
        "odds": 2.10,
        "min_odds": 1.90
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["selection"] == "Over 2.5"
    assert data["stake"] == 3.00


def test_stage_betslip_period_market_resolution(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "OPENED_NEW_TAB"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Over 1.5", "current_odds": 1.49})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "1.49000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/ice-hockey/usa/nhl/46925257-new-york-islanders-vancouver-canucks",
        selection="Over 1.5",
        stake=1.00,
        odds=1.49,
        line=1.5,
        tab="Periods",
        market_name="3rd Period - Total",
        market="period_3:totals",
        outcome_name="Over 1.5",
    )
    assert res["status"] == "ok"
    assert res["selection"] == "Over 1.5"
    assert res["stake"] == 1.00
    assert res["odds"] == 1.49
    assert any("3rd period" in s.lower() for s in executed_scripts)


def test_api_stage_betslip_with_period_market(monkeypatch):
    import subprocess
    client = TestClient(app)

    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 6})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Over 1.5", "current_odds": 1.49})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "1.49000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = client.post("/api/stage-betslip", json={
        "event_url": "https://stake.com/sports/ice-hockey/usa/nhl/46925257-new-york-islanders-vancouver-canucks",
        "selection": "Over 1.5",
        "stake": 1.00,
        "odds": 1.49,
        "line": 1.5,
        "tab": "Periods",
        "market_name": "3rd Period - Total",
        "market": "period_3:totals",
        "outcome_name": "Over 1.5"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["selection"] == "Over 1.5"
    assert data["stake"] == 1.00


def test_stage_betslip_wta_tennis_resolution(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "OPENED_NEW_TAB"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 12})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Liudmila Samsonova", "current_odds": 2.05})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "2.00", "payout": "4.10000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/tennis/wta/wta-wuhan/samsonova-fernandez",
        selection="Liudmila Samsonova",
        stake=2.00,
        odds=2.05,
        league="WTA",
        market="period_1:moneyline",
        market_name="1st Set - Winner",
        outcome_name="Liudmila Samsonova",
    )
    assert res["status"] == "ok"
    assert res["selection"] == "Liudmila Samsonova"
    assert res["stake"] == 2.00
    assert res["odds"] == 2.05
    assert any("sets" in s.lower() for s in executed_scripts)
    assert any("1st set" in s.lower() for s in executed_scripts)


def test_api_stage_betslip_wta_tennis(monkeypatch):
    import subprocess
    client = TestClient(app)

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Samsonova", "current_odds": 2.05})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "2.00", "payout": "4.10000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = client.post("/api/stage-betslip", json={
        "event_url": "https://stake.com/sports/tennis/wta/wta-wuhan/samsonova-fernandez",
        "selection": "Liudmila Samsonova",
        "stake": 2.00,
        "odds": 2.05,
        "league": "WTA",
        "market": "period_1:moneyline",
        "market_name": "1st Set - Winner",
        "outcome_name": "Liudmila Samsonova"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["selection"] == "Samsonova"
    assert data["stake"] == 2.00
    assert any("sets" in s.lower() for s in executed_scripts)


def test_stage_betslip_nba_handicap_routing(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 14})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Phoenix Suns (+8.5)", "current_odds": 1.88})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "1.88000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/basketball/usa/nba-preseason/46894519-san-antonio-spurs-phoenix-suns",
        selection="Phoenix Suns +8.5",
        stake=1.00,
        odds=1.88,
        min_odds=1.00,
        league="NBA",
        market="spreads",
        market_name="Handicap (Incl. Overtime)",
        line=8.5,
        outcome_name="Phoenix Suns",
    )
    assert res["status"] == "ok"
    assert res["selection"] == "Phoenix Suns (+8.5)"
    assert res["stake"] == 1.00
    assert res["odds"] == 1.88
    # Verifies Handicap tab was chosen and included in fallbackTabs
    assert any("handicap" in s.lower() for s in executed_scripts)


def test_api_stage_betslip_nba_handicap(monkeypatch):
    import subprocess
    client = TestClient(app)

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 14})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Phoenix Suns (+8.5)", "current_odds": 1.88})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "1.88000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = client.post("/api/stage-betslip", json={
        "event_url": "https://stake.com/sports/basketball/usa/nba-preseason/46894519-san-antonio-spurs-phoenix-suns",
        "selection": "Phoenix Suns +8.5",
        "stake": 1.00,
        "odds": 1.88,
        "min_odds": 1.00,
        "league": "NBA",
        "market": "spreads",
        "market_name": "Handicap (Incl. Overtime)",
        "line": 8.5,
        "outcome_name": "Phoenix Suns"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["selection"] == "Phoenix Suns (+8.5)"
    assert any("handicap" in s.lower() for s in executed_scripts)


def test_stage_betslip_mlb_baseball_resolution(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Under 1.5", "current_odds": 1.77})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "1.77000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/baseball/usa/mlb/46951962-nlcs-finalist-2-nlcs-finalist-1",
        selection="Under 1.5",
        stake=1.00,
        odds=1.77,
        min_odds=1.74,
        league="MLB",
        market="innings_1_5:away_total",
        market_name="Innings 1 to 5 - Milwaukee Brewers Total",
        line=1.5,
        outcome_name="Under 1.5",
    )
    assert res["status"] == "ok"
    assert res["selection"] == "Under 1.5"
    assert res["stake"] == 1.00
    assert res["odds"] == 1.77
    # Verifies Innings 1 to 5 tab was chosen and included in fallbackTabs
    assert any("innings 1 to 5" in s.lower() for s in executed_scripts)


def test_api_stage_betslip_mlb_baseball(monkeypatch):
    import subprocess
    client = TestClient(app)

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Under 1.5", "current_odds": 1.77})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "1.77000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = client.post("/api/stage-betslip", json={
        "event_url": "https://stake.com/sports/baseball/usa/mlb/46951962-nlcs-finalist-2-nlcs-finalist-1",
        "selection": "Under 1.5",
        "stake": 1.00,
        "odds": 1.77,
        "min_odds": 1.74,
        "league": "MLB",
        "market": "innings_1_5:away_total",
        "market_name": "Innings 1 to 5 - Milwaukee Brewers Total",
        "line": 1.5,
        "outcome_name": "Under 1.5"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["selection"] == "Under 1.5"
    assert any("innings 1 to 5" in s.lower() for s in executed_scripts)


def test_stage_betslip_auto_place_one_click(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Under 1.5", "current_odds": 1.77})
        elif call_idx == 4:
            r.stdout = json.dumps({"status": "ok", "stake": "2.50", "payout": "4.42500000"})
        elif call_idx == 5:
            # click_place_js
            r.stdout = json.dumps({"status": "clicked"})
        elif call_idx == 6:
            # check_success_js
            r.stdout = json.dumps({"state": "success"})
        elif call_idx == 7:
            # script_close_tab
            r.stdout = "CLOSED"
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/baseball/usa/mlb/46951962-nlcs-finalist-2-nlcs-finalist-1",
        selection="Under 1.5",
        stake=2.50,
        odds=1.77,
        league="MLB",
        market="innings_1_5:away_total",
        auto_place=True,
    )
    assert res["status"] == "ok"
    assert res["placed"] is True
    assert res["closed"] is True
    assert "sikeresen megkötve" in res["message"]


def test_api_stage_betslip_auto_place(monkeypatch):
    import subprocess
    client = TestClient(app)

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Under 1.5", "current_odds": 1.77})
        elif call_idx == 4:
            r.stdout = json.dumps({"status": "ok", "stake": "2.50", "payout": "4.42500000"})
        elif call_idx == 5:
            # click_place_js
            r.stdout = json.dumps({"status": "clicked"})
        elif call_idx == 6:
            # check_success_js
            r.stdout = json.dumps({"state": "success"})
        elif call_idx == 7:
            # script_close_tab
            r.stdout = "CLOSED"
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    resp = client.post("/api/stage-betslip", json={
        "event_url": "https://stake.com/sports/baseball/usa/mlb/46951962-nlcs-finalist-2-nlcs-finalist-1",
        "selection": "Under 1.5",
        "stake": 2.50,
        "odds": 1.77,
        "league": "MLB",
        "auto_place": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["placed"] is True
    assert data["closed"] is True
    assert "place_delay" in data
    assert 3.0 <= data["place_delay"] <= 6.0


def test_stage_betslip_place_delay_jitter(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Under 1.5", "current_odds": 1.77})
        elif call_idx == 4:
            r.stdout = json.dumps({"status": "ok", "stake": "2.50", "payout": "4.42500000"})
        elif call_idx == 5:
            r.stdout = json.dumps({"status": "clicked"})
        elif call_idx == 6:
            r.stdout = json.dumps({"state": "success"})
        elif call_idx == 7:
            r.stdout = "CLOSED"
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/baseball/usa/mlb/46951962-nlcs-finalist-2-nlcs-finalist-1",
        selection="Under 1.5",
        stake=2.50,
        odds=1.77,
        league="MLB",
        market="innings_1_5:away_total",
        auto_place=True,
        min_place_delay=1.1,
        max_place_delay=1.7,
    )
    assert res["status"] == "ok"
    assert res["placed"] is True
    assert "place_delay" in res
    assert 1.1 <= res["place_delay"] <= 1.7


def test_stage_betslip_detects_300_limit_in_check_success(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Under 1.5", "current_odds": 1.77})
        elif call_idx == 4:
            r.stdout = json.dumps({"status": "ok", "stake": "2.50", "payout": "4.42500000"})
        elif call_idx == 5:
            r.stdout = json.dumps({"status": "clicked"})
        elif call_idx == 6:
            # check_success_js detects 300 limit error!
            r.stdout = json.dumps({"state": "error", "limit_reached": True, "error": "Elérted a Stake maximális aktív fogadási limitjét (max 300 bet)!"})
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/baseball/usa/mlb/46951962-nlcs-finalist-2-nlcs-finalist-1",
        selection="Under 1.5",
        stake=2.50,
        odds=1.77,
        league="MLB",
        auto_place=True,
    )
    assert res["status"] == "error"
    assert res["placed"] is False
    assert res["closed"] is False
    assert res["limit_reached"] is True
    assert "300" in res["message"]


def test_stage_betslip_wta_tennis_total_sets_resolution(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    executed_scripts = []

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        executed_scripts.append(cmd_str)
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "OPENED_NEW_TAB"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 6})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Over 2.5", "current_odds": 2.60})
        else:
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "2.60000000"})
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/tennis/wta/wta-ningbo/eala-parry",
        selection="Over 2.5",
        stake=1.00,
        odds=2.60,
        league="WTA",
        market="total_sets",
        outcome_name="Over 2.5",
    )
    assert res["status"] == "ok"
    assert res["selection"] == "Over 2.5"
    assert res["stake"] == 1.00
    assert any("sets" in s.lower() for s in executed_scripts)
    assert any("total sets" in s.lower() for s in executed_scripts)


def test_stage_betslip_network_disconnected_handling(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Calgary Flames", "current_odds": 3.40})
        elif call_idx == 4:
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "3.40000000"})
        elif call_idx == 5:
            r.stdout = json.dumps({"status": "clicked"})
        elif call_idx == 6:
            # check_success_js detects network disconnect!
            r.stdout = json.dumps({"state": "error", "network_disconnected": True, "error": "A Stake kapcsolata megszakadt (Network disconnected). Kérlek ellenőrizd az internet / VPN kapcsolatot!"})
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/ice-hockey/usa/nhl/flames-hurricanes",
        selection="Calgary Flames",
        stake=1.00,
        odds=3.40,
        league="NHL",
        auto_place=True,
    )
    assert res["status"] == "error"
    assert res["placed"] is False
    assert res["network_disconnected"] is True
    assert "Network disconnected" in res["message"]


def test_resolve_stake_market_tab_derivatives():
    from betting_engine.stake_account import resolve_stake_market_tab

    # Soccer half team totals
    assert resolve_stake_market_tab("UCL", "half_1:home_total", "1st Half - Como Total", [{"name": "1st2ndhalfmarkets"}]) == "Half"
    assert resolve_stake_market_tab("UCL", "half_2:away_total", "2nd Half - Real Madrid Total", [{"name": "1st2ndhalfmarkets"}]) == "Half"
    # Soccer match team totals
    assert resolve_stake_market_tab("UCL", "home_total", "Bayern Munich Total", [{"name": "goals"}]) == "Goals"
    assert resolve_stake_market_tab("UCL", "away_total", "Arsenal Total", [{"name": "goals"}]) == "Goals"
    # Tennis total sets
    assert resolve_stake_market_tab("ATP", "total_sets", "Total Sets", [{"name": "sets"}]) == "Sets"
    # NBA half Asian markets (routed to 'Half' tab on Stake, NOT nonexistent 'Asian Lines')
    assert resolve_stake_market_tab("NBA", "half_1:totals", "1st Half - Asian Total", [{"name": "half"}]) == "Half"
    assert resolve_stake_market_tab("NBA", "half_1:spreads", "1st Half - Asian Handicap", [{"name": "half"}]) == "Half"
    # NBA quarter team totals & 3-way
    assert resolve_stake_market_tab("NBA", "quarter_1:away_total", "1st Quarter - Atlanta Hawks total", [{"name": "quarters"}]) == "Quarters"
    assert resolve_stake_market_tab("NBA", "quarter_1:three_way", "1st Quarter - 1x2", [{"name": "quarters"}]) == "Quarters"
    # NBA half team totals
    assert resolve_stake_market_tab("NBA", "half_1:home_total", "1st Half - Toronto Raptors Total", [{"name": "half"}]) == "Half"
    # NHL periods
    assert resolve_stake_market_tab("NHL", "period_1:totals", "1st Period - Total", [{"name": "periods"}]) == "Periods"
    # Baseball innings 1 to 5
    assert resolve_stake_market_tab("MLB", "innings_1_5:totals", "Innings 1 to 5 - Total", [{"name": "innings 1 to 5"}]) == "Innings 1 to 5"


def test_stage_betslip_js_contains_odds_scoring_and_betslip_reset(monkeypatch):
    """Verifies that generated JS includes market accordion targeting, odds proximity scoring, and reset."""
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    intercepted_codes = []
    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            intercepted_codes.append(cmd[3])
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            intercepted_codes.append(cmd[3])
            r.stdout = json.dumps({"status": "clicked", "selection": "Over 1.5", "current_odds": 4.80})
        elif call_idx == 4:
            intercepted_codes.append(cmd[3])
            r.stdout = json.dumps({"status": "ok", "stake": "1.00", "payout": "4.80000000"})
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/soccer/international-clubs/uefa-champions-league/46853696-como-manchester-united",
        selection="Over 1.5",
        stake=1.00,
        odds=4.80,
        tab="Half",
        market_name="1st Half - Como Total",
        league="UCL",
        auto_place=False,
    )
    assert res["status"] == "ok"
    assert len(intercepted_codes) >= 2
    # Verify stage_js or helpers_js contains targetOdds scoring and betslip reset
    combined = " ".join(intercepted_codes)
    assert "oddsScore" in combined
    assert "scoreAndSelectOutcome" in combined
    assert "reset-betslip" in combined
    assert "betslip-bet-remove" in combined
    assert "remainingBets" in combined


def test_resolve_stake_market_tab_nba_half_and_nfl_totals():
    from betting_engine.stake_account import resolve_stake_market_tab

    # NBA 1st Half Asian Handicap must resolve to 'Half', never 'Asian Lines'
    t1 = resolve_stake_market_tab(
        league="NBA",
        market="half_1:spreads",
        market_name="1st Half - Asian Handicap",
        groups=[{"name": "half"}],
        event_url="https://stake.com/sports/basketball/usa/nba/46992411-charlotte-hornets-miami-heat"
    )
    assert t1 == "Half"

    # NBA 1st Half Asian Total must resolve to 'Half', never 'Asian Lines'
    t2 = resolve_stake_market_tab(
        league="NBA",
        market="half_1:totals",
        market_name="1st Half - Asian Total",
        groups=[{"name": "half"}],
        event_url="https://stake.com/sports/basketball/usa/nba/46992411-charlotte-hornets-miami-heat"
    )
    assert t2 == "Half"

    # NBA Quarter market resolves to 'Quarters'
    t3 = resolve_stake_market_tab(
        league="NBA",
        market="quarter_1:spreads",
        market_name="1st Quarter - Handicap",
        groups=[{"name": "quarters"}],
        event_url="https://stake.com/sports/basketball/usa/nba/46992411-charlotte-hornets-miami-heat"
    )
    assert t3 == "Quarters"

    # NFL Total must resolve to 'Total', NEVER 'Goals'
    t4 = resolve_stake_market_tab(
        league="NFL",
        market="totals",
        market_name="Total (Incl. Overtime)",
        groups=[{"name": "total"}],
        event_url="https://stake.com/sports/american-football/usa/nfl/46992412-panthers-broncos"
    )
    assert t4 == "Total"

    # NCAAF Total must resolve to 'Total', NEVER 'Goals'
    t5 = resolve_stake_market_tab(
        league="NCAAF",
        market="totals",
        market_name="Total (Incl. Overtime)",
        groups=[{"name": "total"}],
        event_url="https://stake.com/sports/american-football/usa/ncaaf/46992413-army-navy"
    )
    assert t5 == "Total"

    # Full multi-sport audit covering Tennis, NBA, NFL, NHL, MLB, Soccer
    multi_sport_cases = [
        ("ATP", "spreads", "Game Handicap", [{"name": "games"}], "Games"),
        ("ATP", "totals", "Total Games", [{"name": "games"}], "Games"),
        ("WTA", "period_1:moneyline", "1st Set - Winner", [{"name": "sets"}], "Sets"),
        ("NBA", "home_total", "Charlotte Hornets Total (Incl. Overtime)", [{"name": "main"}, {"name": "points"}], "Popular"),
        ("NBA", "away_total", "Miami Heat Total (Incl. Overtime)", [{"name": "main"}, {"name": "points"}], "Popular"),
        ("NBA", "quarter_3:totals", "3rd Quarter - Total", [{"name": "quarters"}], "Quarters"),
        ("NHL", "moneyline", "Winner (Incl. Overtime and Penalties)", [{"name": "main"}], "Popular"),
        ("NHL", "spreads", "Handicap (Puck Line)", [{"name": "main"}], "Popular"),
        ("NHL", "totals", "Total (Incl. Overtime and Penalties)", [{"name": "main"}], "Popular"),
        ("NHL", "period_1:totals", "1st Period - Total", [{"name": "periods"}], "Periods"),
        ("EPL", "spreads", "Asian Handicap", [{"name": "asianlines"}], "Asian Lines"),
        ("EPL", "totals", "Total Goals", [{"name": "goals"}], "Goals"),
        ("MLB", "moneyline", "Winner (Incl. Extra Innings)", [{"name": "main"}], "Popular"),
        ("MLB", "spreads", "Handicap (Incl. Extra Innings)", [{"name": "main"}], "Popular"),
        ("MLB", "totals", "Total (Incl. Extra Innings)", [{"name": "main"}], "Popular"),
        ("MLB", "innings_1_5:totals", "Innings 1 to 5 - Total", [{"name": "innings 1 to 5"}], "Innings 1 to 5"),
        ("MLB", "inning_1:totals", "1st Inning - Total", [{"name": "innings"}], "Innings"),
    ]
    for lg, m, mn, grps, exp in multi_sport_cases:
        assert resolve_stake_market_tab(lg, m, mn, grps) == exp, f"Failed for {lg} {m} ({mn})"


def test_stage_betslip_verification_gate_rejects_full_game_slip_for_half_bet(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Charlotte Hornets (+3.5)", "current_odds": 1.90})
        elif call_idx == 4:
            # Simulate betslip holding full-game Handicap (Incl. Overtime) instead of 1st Half
            r.stdout = json.dumps({
                "status": "verification_failed",
                "error": "Szelvény verifikációs hiba: A betslipben szereplő piac nem a kért időszakra (1st half) vonatkozik! Betslip tartalom: 'Charlotte Hornets - Miami Heat Handicap (Incl. Overtime) Charlotte Hornets (3.5)'"
            })
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/basketball/usa/nba/46992411-charlotte-hornets-miami-heat",
        selection="Charlotte Hornets +3.5",
        stake=1.50,
        odds=1.90,
        line=3.5,
        league="NBA",
        market="half_1:spreads",
        market_name="1st Half - Asian Handicap",
        auto_place=True,
    )
    assert res["status"] == "error"
    assert res["placed"] is False
    assert res.get("verification_failed") is True
    assert "Szelvény verifikációs hiba" in res["message"]


def test_stage_betslip_verification_gate_passes_matching_half_bet(monkeypatch):
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    call_idx = 0
    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Charlotte Hornets (+3.5)", "current_odds": 1.90})
        elif call_idx == 4:
            # Simulate matching verification and success
            r.stdout = json.dumps({"status": "ok", "stake": "1.50", "payout": "2.85000000"})
        elif call_idx == 5:
            # click_place_js
            r.stdout = json.dumps({"status": "clicked", "button": "betSlip-place-bets-button"})
        elif call_idx == 6:
            # check_success_js
            r.stdout = json.dumps({"state": "success"})
        elif call_idx == 7:
            r.stdout = "CLOSED"
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/basketball/usa/nba/46992411-charlotte-hornets-miami-heat",
        selection="Charlotte Hornets +3.5",
        stake=1.50,
        odds=1.90,
        line=3.5,
        league="NBA",
        market="half_1:spreads",
        market_name="1st Half - Asian Handicap",
        auto_place=True,
    )
    assert res["status"] == "ok"
    assert res["placed"] is True


def test_stage_betslip_auto_extracts_line_from_selection(monkeypatch):
    """Verify that when line is None, numeric line is auto-extracted from selection string."""
    from betting_engine.stake_account import stage_betslip_in_safari
    import subprocess
    import json

    captured_js = []
    call_idx = 0

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        class Res:
            stderr = ""
        r = Res()
        if len(cmd) > 3:
            captured_js.append(cmd[3])
        if call_idx == 1:
            r.stdout = "ALREADY_OPEN"
        elif call_idx == 2:
            # Poll returns ready
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 6})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Under 120.5", "current_odds": 1.74})
        elif call_idx == 4:
            r.stdout = json.dumps({"status": "ok", "stake": "0.50", "payout": "0.87"})
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/basketball/usa/nba/46992412-philadelphia-76ers-milwaukee-bucks",
        selection="Under 120.5",
        stake=0.50,
        odds=1.74,
        line=None,  # Intentionally omitted line
        league="NBA",
        market="home_total",
        market_name="Philadelphia 76ers Total (Incl. Overtime)",
        auto_place=False,
    )
    assert res["status"] == "ok"
    # Ensure line variants were embedded in JS
    assert any('"120.5"' in js for js in captured_js)


def test_resolve_stake_market_tab_all_sports():
    """Verify multi-sport tab resolution across all major sports and market families."""
    from betting_engine.stake_account import resolve_stake_market_tab

    cases = [
        # Ice Hockey (NHL)
        ("NHL", None, "2nd Period - Total", "Periods"),
        ("NHL", "period_2:totals", "2nd Period - Total", "Periods"),
        ("NHL", "period_1:three_way", "1st Period - 1x2", "Periods"),
        ("NHL", "totals", "Total (Incl. Overtime)", "Popular"),
        ("NHL", "spreads", "Handicap (Puck Line)", "Popular"),
        ("NHL", "moneyline", "Winner (Incl. Overtime)", "Popular"),
        ("NHL", "both_score", "Both Teams to Score", "Goals"),

        # Tennis (ATP / WTA)
        ("ATP", "period_1:moneyline", "1st Set - Winner", "Sets"),
        ("WTA", "period_1:totals", "1st Set - Total Games", "Sets"),
        ("ATP", "total_sets", "Total Sets", "Sets"),
        ("ATP", "totals", "Total Games", "Games"),
        ("WTA", "spreads", "Game Handicap", "Games"),
        ("ATP", "moneyline", "Winner", "Popular"),

        # Basketball (NBA / WNBA / NCAAB)
        ("NBA", "half_1:spreads", "1st Half - Asian Handicap", "Half"),
        ("NBA", "half_1:totals", "1st Half - Asian Total", "Half"),
        ("NBA", "quarter_1:totals", "1st Quarter - Total", "Quarters"),
        ("NBA", "quarter_2:three_way", "2nd Quarter - 1x2", "Quarters"),
        ("NBA", "spreads", "Handicap (Incl. Overtime)", "Popular"),
        ("NBA", "totals", "Total (Incl. Overtime)", "Popular"),
        ("NBA", "home_total", "Cleveland Cavaliers Total (Incl. Overtime)", "Popular"),

        # American Football (NFL / NCAAF)
        ("NFL", "half_1:totals", "1st Half - Asian Total", "Half"),
        ("NCAAF", "half_1:three_way", "1st Half - 1x2", "Half"),
        ("NFL", "quarter_3:spreads", "3rd Quarter - Handicap", "Quarters"),
        ("NFL", "quarter_1:totals", "1st Quarter - Total", "Quarters"),
        ("NFL", "spreads", "Handicap (Incl. Overtime)", "Popular"),
        ("NCAAF", "moneyline", "Winner (Incl. Overtime)", "Popular"),

        # Soccer (MLS / EPL / UCL)
        ("MLS", "half_1:spreads", "1st Half - Asian Handicap", "Half"),
        ("MLS", "half_2:three_way", "2nd Half - 1x2", "Half"),
        ("MLS", "spreads", "Asian Handicap", "Asian Lines"),
        ("EPL", "totals", "Asian Total", "Asian Lines"),
        ("MLS", "moneyline", "Draw No Bet", "Popular"),
        ("EPL", "three_way", "1x2", "Popular"),
        ("MLS", "both_score", "Both Teams to Score", "Goals"),

        # Baseball (MLB)
        ("MLB", "innings_1_5:spreads", "Innings 1 to 5 - Handicap", "Innings 1 to 5"),
        ("MLB", "inning_1:totals", "1st Inning - Total", "Innings"),
        ("MLB", "spreads", "Handicap (Incl. Extra Innings)", "Popular"),
        ("MLB", "totals", "Total (Incl. Extra Innings)", "Popular"),
    ]

    for lg, m, mn, expected in cases:
        actual = resolve_stake_market_tab(lg, m, mn)
        assert actual == expected, f"Failed for {lg} | {m} | {mn}: got {actual}, expected {expected}"


def test_stage_betslip_never_activates_safari_window(monkeypatch):
    """Verifies that all AppleScript scripts executed by stage_betslip never call 'activate',
    preventing Safari from stealing focus or jumping into view when the user is working in other apps."""
    import subprocess
    from betting_engine.stake_account import stage_betslip_in_safari

    intercepted_scripts = []
    call_idx = 0

    def mock_run(cmd, *args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        # Intercept the script text (usually cmd[2] for -e)
        if len(cmd) >= 3 and cmd[1] == "-e":
            intercepted_scripts.append(cmd[2])
        class Res:
            stderr = ""
        r = Res()
        if call_idx == 1:
            r.stdout = "OPENED_NEW_TAB"
        elif call_idx == 2:
            r.stdout = json.dumps({"ready": True, "hasTarget": True, "tabActive": True, "outcomesCount": 8})
        elif call_idx == 3:
            r.stdout = json.dumps({"status": "clicked", "selection": "Over 2.5", "current_odds": 2.10})
        elif call_idx == 4:
            r.stdout = json.dumps({"status": "ok", "stake": "2.50", "payout": "5.25000000"})
        elif call_idx == 5:
            r.stdout = json.dumps({"status": "clicked"})
        elif call_idx == 6:
            r.stdout = json.dumps({"success": True, "status": "ok", "error": "Your bet has been placed!"})
        else:
            r.stdout = ""
        return r

    monkeypatch.setattr(subprocess, "run", mock_run)
    res = stage_betslip_in_safari(
        event_url="https://stake.com/sports/ice-hockey/usa/nhl/oilers-canucks",
        selection="Over 2.5",
        stake=2.50,
        odds=2.10,
        league="NHL",
        auto_place=True,
    )
    assert res["status"] == "ok"
    assert res["placed"] is True

    # Assert that NO AppleScript ever contained the 'activate' keyword
    assert len(intercepted_scripts) > 0
    for script in intercepted_scripts:
        # Check that 'activate' is not used as an AppleScript command
        lines = [line.strip().lower() for line in script.split("\n")]
        assert "activate" not in lines, f"Forbidden 'activate' command found in AppleScript:\n{script}"


def test_api_autorun_endpoints():
    client = TestClient(app)

    # 1. GET /api/autorun
    resp = client.get("/api/autorun")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "today_placed_count" in data
    assert "windows" in data
    assert "h24" in data["windows"]
    assert "h48" in data["windows"]
    assert "open_bets_count" in data
    assert data["limit"] == 300

    # 2. POST /api/autorun/heartbeat
    resp_hb = client.post("/api/autorun/heartbeat", json={
        "enabled": True,
        "next_trigger_at": 1792953742394,
        "last_status": "✅ Ciklus kész! Megkötve: 2 db szelvény.",
        "last_cycle_placed": 2,
        "runner_id": "MacBook Pro (Safari)"
    })
    assert resp_hb.status_code == 200
    hb_data = resp_hb.json()
    assert hb_data["status"] == "ok"
    assert hb_data["active"] is True
    assert hb_data["runner_id"] == "MacBook Pro (Safari)"
    assert hb_data["last_status"] == "✅ Ciklus kész! Megkötve: 2 db szelvény."
    assert hb_data["last_cycle_placed"] == 2

    # 3. GET /api/autorun reflects active heartbeat
    resp2 = client.get("/api/autorun")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["active"] is True
    assert data2["runner_id"] == "MacBook Pro (Safari)"











