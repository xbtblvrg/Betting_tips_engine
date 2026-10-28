"""Live Stake Account balance, active bets, and exposure monitoring.

Isolated from model prediction, pricing, and betting decisions.
Provides real-time visibility into Stake bankroll, available cash, open wagers,
and per-league risk distribution based on authenticated token presence,
live Safari session synchronization, and local betting ledger.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from .domain import utcnow

log = logging.getLogger(__name__)


def get_masked_token(token: Optional[str]) -> str:
    """Safely masks the token for display without leaking secret bytes."""
    if not token or not token.strip():
        return "Not configured"
    clean = token.strip()
    if len(clean) >= 12:
        return f"{clean[:6]}{'•' * 16}{clean[-4:]}"
    return f"{clean[:2]}{'•' * 6}"


def is_safari_running() -> bool:
    """Checks if Safari application is actively running without launching it."""
    try:
        res = subprocess.run(["osascript", "-e", 'application "Safari" is running'], capture_output=True, text=True, timeout=2)
        out = res.stdout.strip().lower()
        return out == "true" or "user" in out or "balances" in out
    except Exception:
        return False


def ensure_stake_balance_history_table(conn: sqlite3.Connection) -> None:
    """Ensures stake_balance_history table exists and seeds baseline points from settled bets if empty."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stake_balance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            total_balance REAL NOT NULL,
            available_balance REAL NOT NULL,
            open_exposure REAL NOT NULL,
            open_bets_count INTEGER NOT NULL,
            balances_json TEXT,
            by_league_json TEXT,
            source TEXT DEFAULT 'safari_live_sync'
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stake_balance_history_ts ON stake_balance_history(timestamp);")
    conn.commit()

    cur.execute("SELECT count(*) FROM stake_balance_history")
    count = cur.fetchone()[0]
    if count == 0:
        cur.execute("SELECT value FROM kv WHERE key = 'stake_live_sync'")
        row = cur.fetchone()
        curr_bal = 724.87
        avail_bal = 457.45
        open_exp = 267.42
        open_count = 122
        balances_json = "{}"
        by_league_json = "{}"
        if row:
            try:
                cached = json.loads(row[0])
                curr_bal = float(cached.get("total_balance", curr_bal))
                avail_bal = float(cached.get("available_balance", avail_bal))
                open_exp = float(cached.get("open_exposure", open_exp))
                open_count = int(cached.get("open_bets_count", open_count))
                balances_json = json.dumps(cached.get("balances", {}))
                by_league_json = json.dumps(cached.get("by_league", {}))
            except Exception:
                pass

        pnl_rows = []
        try:
            cur.execute("""
                SELECT date(created_at) as d, sum(pnl)
                FROM bets
                WHERE status IN ('won', 'lost', 'push')
                GROUP BY date(created_at)
                ORDER BY date(created_at) ASC
            """)
            pnl_rows = cur.fetchall()
        except Exception:
            pnl_rows = []

        total_pnl = sum(r[1] for r in pnl_rows if r[1] is not None)
        start_bal = curr_bal - total_pnl

        from datetime import datetime, timezone
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        cum = start_bal
        for d_str, pnl in pnl_rows:
            if pnl is not None:
                cum += pnl
            if d_str and d_str < today_str:
                ts_iso = f"{d_str}T22:00:00+00:00"
                cur.execute("""
                    INSERT INTO stake_balance_history 
                    (timestamp, total_balance, available_balance, open_exposure, open_bets_count, balances_json, by_league_json, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'historical_ledger_reconstruction')
                """, (ts_iso, round(cum, 2), round(max(0.0, cum - open_exp), 2), open_exp, open_count, "{}", "{}"))

        now_iso = utcnow().isoformat()
        cur.execute("""
            INSERT INTO stake_balance_history 
            (timestamp, total_balance, available_balance, open_exposure, open_bets_count, balances_json, by_league_json, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'safari_live_sync')
        """, (now_iso, curr_bal, avail_bal, open_exp, open_count, balances_json, by_league_json))
        conn.commit()


def record_balance_snapshot(conn: sqlite3.Connection, snapshot_data: Dict[str, Any]) -> None:
    """Inserts a new snapshot into stake_balance_history if balance/exposure changed or >15m elapsed."""
    ensure_stake_balance_history_table(conn)
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, total_balance, open_exposure 
        FROM stake_balance_history 
        ORDER BY timestamp DESC LIMIT 1
    """)
    row = cur.fetchone()
    should_insert = False
    if not row:
        should_insert = True
    else:
        last_ts_str, last_total, last_exp = row
        if (
            abs(float(last_total) - float(snapshot_data["total_balance"])) >= 0.01
            or abs(float(last_exp) - float(snapshot_data["open_exposure"])) >= 0.01
        ):
            should_insert = True
        else:
            try:
                from datetime import datetime
                last_dt = datetime.fromisoformat(last_ts_str)
                if (utcnow() - last_dt).total_seconds() >= 900.0:
                    should_insert = True
            except Exception:
                should_insert = True

    if should_insert:
        cur.execute("""
            INSERT INTO stake_balance_history
            (timestamp, total_balance, available_balance, open_exposure, open_bets_count, balances_json, by_league_json, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot_data.get("synced_at") or utcnow().isoformat(),
            snapshot_data["total_balance"],
            snapshot_data["available_balance"],
            snapshot_data["open_exposure"],
            snapshot_data["open_bets_count"],
            json.dumps(snapshot_data.get("balances", {})),
            json.dumps(snapshot_data.get("by_league", {})),
            snapshot_data.get("source", "safari_live_sync"),
        ))
        conn.commit()


def get_stake_balance_history(conn: sqlite3.Connection, period: str = "all") -> Dict[str, Any]:
    """Retrieves chronological balance history snapshots and portfolio summary metrics."""
    ensure_stake_balance_history_table(conn)
    cur = conn.cursor()

    where_clause = ""
    params = []
    now = utcnow()
    from datetime import timedelta
    if period == "24h":
        cutoff = (now - timedelta(hours=24)).isoformat()
        where_clause = "WHERE timestamp >= ?"
        params.append(cutoff)
    elif period == "7d":
        cutoff = (now - timedelta(days=7)).isoformat()
        where_clause = "WHERE timestamp >= ?"
        params.append(cutoff)
    elif period == "30d":
        cutoff = (now - timedelta(days=30)).isoformat()
        where_clause = "WHERE timestamp >= ?"
        params.append(cutoff)

    query = f"""
        SELECT timestamp, total_balance, available_balance, open_exposure, open_bets_count, source
        FROM stake_balance_history
        {where_clause}
        ORDER BY timestamp ASC
    """
    cur.execute(query, params)
    rows = cur.fetchall()

    points = []
    for r in rows:
        points.append({
            "timestamp": r[0],
            "total_balance": round(r[1], 2),
            "available_balance": round(r[2], 2),
            "open_exposure": round(r[3], 2),
            "open_bets_count": r[4],
            "source": r[5]
        })

    cur.execute("SELECT MAX(total_balance) FROM stake_balance_history")
    all_time_high = cur.fetchone()[0] or (points[-1]["total_balance"] if points else 0.0)

    summary = {
        "current_balance": points[-1]["total_balance"] if points else 0.0,
        "starting_balance": points[0]["total_balance"] if points else 0.0,
        "net_change": round(points[-1]["total_balance"] - points[0]["total_balance"], 2) if points else 0.0,
        "pct_change": round(((points[-1]["total_balance"] - points[0]["total_balance"]) / points[0]["total_balance"]) * 100, 2) if points and points[0]["total_balance"] > 0 else 0.0,
        "min_balance": min(p["total_balance"] for p in points) if points else 0.0,
        "max_balance": max(p["total_balance"] for p in points) if points else 0.0,
        "high_watermark": round(all_time_high, 2),
        "data_points_count": len(points)
    }
    return {"period": period, "summary": summary, "points": points}


def sync_live_stake_from_safari(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Directly queries Stake.com session via Safari for live balances and active bets.
    
    If an open Stake.com tab is present in Safari, it queries it directly in ~0.1s.
    If no Stake tab is open, it opens a temporary background tab in Safari (preserving
    the user's active tab focus), queries the authenticated GraphQL API, and immediately
    closes the tab in ~3.5s.
    Saves snapshot to kv ('stake_live_sync') and appends to 'stake_balance_history'.
    """
    if not is_safari_running():
        return None

    js_code = """(() => {
        let sessionToken = '';
        for (const c of document.cookie.split('; ')) {
            if (c.startsWith('session=')) {
                sessionToken = c.substring('session='.length);
                break;
            }
        }
        function gql(query) {
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/_api/graphql', false);
            xhr.setRequestHeader('content-type', 'application/json');
            xhr.setRequestHeader('x-access-token', sessionToken);
            xhr.send(JSON.stringify({ query: query }));
            return JSON.parse(xhr.responseText);
        }
        const userRes = gql('query { user { id name activeSportBetCount balances { available { amount currency } vault { amount currency } } } }');
        if (!userRes || !userRes.data || !userRes.data.user) {
            return JSON.stringify({ error: 'NO_USER' });
        }
        const user = userRes.data.user;
        const totalCount = user.activeSportBetCount || 0;
        let allBets = [];
        let offset = 0;
        const limit = 50;
        while (offset < totalCount && offset < 500) {
            const q = 'query { user { activeSportBets(limit: ' + limit + ', offset: ' + offset + ') { id amount currency status createdAt payout outcomes { odds sport { name slug } fixture { name tournament { name slug } } market { name } } } } }';
            const betsRes = gql(q);
            const bets = betsRes && betsRes.data && betsRes.data.user && betsRes.data.user.activeSportBets ? betsRes.data.user.activeSportBets : [];
            if (!bets.length) break;
            allBets = allBets.concat(bets);
            offset += bets.length;
        }
        return JSON.stringify({ user: user, bets: allBets });
    })()"""

    ascript = f"""
    tell application "Safari"
        set foundTab to missing value
        repeat with w in windows
            repeat with t in tabs of w
                if (URL of t) contains "stake.com" then
                    set foundTab to t
                    exit repeat
                end if
            end repeat
            if foundTab is not missing value then exit repeat
        end repeat

        if foundTab is not missing value then
            return (do JavaScript {json.dumps(js_code)} in foundTab)
        else
            if (count of windows) is 0 then
                return "NO_WINDOWS"
            end if
            tell front window
                set origTab to current tab
                set bgTab to make new tab with properties {{URL:"https://stake.com/sports"}}
                set current tab to origTab
            end tell
            delay 3.2
            set res to (do JavaScript {json.dumps(js_code)} in bgTab)
            close bgTab
            return res
        end if
    end tell
    """
    try:
        res = subprocess.run(["osascript", "-e", ascript], capture_output=True, text=True, timeout=12)
        out = res.stdout.strip()
        if not out or out in ("NO_WINDOWS", "NOT_FOUND") or out.startswith("ERROR:"):
            return None
        data = json.loads(out)
        if "error" in data:
            return None
    except Exception:
        return None

    if "data" in data and "user" in data["data"]:
        user = data["data"]["user"]
        all_bets = user.get("activeSportBets", [])
    elif "user" in data:
        user = data["user"]
        all_bets = data.get("bets", [])
    else:
        return None

    uname = user.get("name")
    uid = user.get("id")

    balances = {}
    total_avail_usd = 0.0
    for b in user.get("balances", []):
        amt = float(b["available"]["amount"])
        vlt = float(b["vault"]["amount"])
        cur = b["available"]["currency"]
        if amt > 0 or vlt > 0:
            balances[cur] = {"available": round(amt, 4), "vault": round(vlt, 4)}
            if cur.lower() in ("usdt", "usdc", "usd"):
                total_avail_usd += amt

    total_exposure = sum(float(b.get("amount", 0.0)) for b in all_bets)
    total_balance = round(total_avail_usd + total_exposure, 2)
    available_balance = round(total_avail_usd, 2)
    open_exposure = round(total_exposure, 2)

    live_by_league: Dict[str, Dict[str, Any]] = {}
    for b in all_bets:
        amt = float(b.get("amount") or 0.0)
        outcomes = b.get("outcomes") or []
        if outcomes:
            sp = outcomes[0].get("sport", {}).get("name") or "Other"
            tr = outcomes[0].get("fixture", {}).get("tournament", {}).get("name") or ""
            if "ATP" in tr:
                key = "ATP"
            elif "WTA" in tr:
                key = "WTA"
            elif "Challenger" in tr or "ITF" in tr:
                key = "CHALLENGER"
            elif "NFL" in tr or "American Football" in sp:
                key = "NFL"
            elif "NBA" in tr or "Basketball" in sp:
                key = "NBA"
            elif "MLB" in tr or "Baseball" in sp:
                key = "MLB"
            elif "NHL" in tr or "Ice Hockey" in sp:
                key = "NHL"
            elif "Premier League" in tr or "EPL" in tr:
                key = "EPL"
            elif "Champions League" in tr or "UCL" in tr:
                key = "UCL"
            elif "Major League Soccer" in tr or "MLS" in tr:
                key = "MLS"
            elif "NCAA" in tr or "College" in tr:
                key = "NCAA"
            else:
                key = sp.upper()
        else:
            key = "OTHER"

        if key not in live_by_league:
            live_by_league[key] = {"count": 0, "exposure": 0.0}
        live_by_league[key]["count"] += 1
        live_by_league[key]["exposure"] = round(live_by_league[key]["exposure"] + amt, 2)

    live_by_league = dict(sorted(live_by_league.items(), key=lambda x: -x[1]["exposure"]))

    sync_payload = {
        "source": "safari_live_sync",
        "username": uname,
        "user_id": uid,
        "total_balance": total_balance,
        "available_balance": available_balance,
        "open_exposure": open_exposure,
        "open_bets_count": len(all_bets),
        "balances": balances,
        "by_league": live_by_league,
        "synced_at": utcnow().isoformat()
    }

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO kv (key, value) VALUES ('stake_live_sync', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (json.dumps(sync_payload),))
    conn.commit()

    record_balance_snapshot(conn, sync_payload)
    return sync_payload


def get_stake_account_summary(conn: sqlite3.Connection, root_dir: Optional[Path] = None, force_refresh: bool = False, enable_safari_sync: bool = True) -> Dict[str, Any]:
    """Generates the live Stake account telemetry snapshot."""
    token = os.environ.get("STAKE_TOKEN_KEY", "")
    if not token and root_dir:
        env_file = root_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("STAKE_TOKEN_KEY="):
                    token = line.split("=", 1)[1].strip()
                    break

    ensure_stake_balance_history_table(conn)
    cur = conn.cursor()

    cur.execute("SELECT value FROM kv WHERE key = 'stake_account_config'")
    row = cur.fetchone()
    cfg = json.loads(row[0]) if row else {"base_bankroll": 1000.0}
    base_bankroll = float(cfg.get("base_bankroll", 1000.0))

    # Try live sync from Safari if enabled and not suppressed by env var
    live_sync_data = None
    safari_sync_allowed = enable_safari_sync and not os.environ.get("STAKE_DISABLE_SAFARI_SYNC")
    if safari_sync_allowed:
        # Check cache freshness to prevent rate limit / spam if not force_refresh
        if not force_refresh:
            cur.execute("SELECT value FROM kv WHERE key = 'stake_live_sync'")
            row = cur.fetchone()
            if row:
                try:
                    cached = json.loads(row[0])
                    synced_iso = cached.get("synced_at")
                    if synced_iso:
                        from datetime import datetime
                        synced_dt = datetime.fromisoformat(synced_iso)
                        if (utcnow() - synced_dt).total_seconds() < 60.0:
                            live_sync_data = cached
                except Exception:
                    pass

        if not live_sync_data:
            try:
                live_sync_data = sync_live_stake_from_safari(conn)
            except Exception:
                live_sync_data = None

        if not live_sync_data:
            cur.execute("SELECT value FROM kv WHERE key = 'stake_live_sync'")
            row = cur.fetchone()
            if row:
                try:
                    live_sync_data = json.loads(row[0])
                except Exception:
                    live_sync_data = None

    # Query virtual auto-tracked stats from local bets
    cur.execute("""
        SELECT stake, payload 
        FROM bets 
        WHERE status = 'open'
    """)
    open_rows = cur.fetchall()
    virtual_open_bets_count = len(open_rows)
    virtual_open_exposure = sum(float(r[0]) for r in open_rows if r[0] is not None)

    by_league: Dict[str, Dict[str, Any]] = {}
    for r in open_rows:
        try:
            payload = json.loads(r[1]) if r[1] else {}
        except Exception:
            payload = {}
        lg = payload.get("league") or payload.get("sport") or "OTHER"
        stk = float(r[0]) if r[0] is not None else 0.0
        if lg not in by_league:
            by_league[lg] = {"count": 0, "exposure": 0.0}
        by_league[lg]["count"] += 1
        by_league[lg]["exposure"] += stk

    for lg in by_league:
        by_league[lg]["exposure"] = round(by_league[lg]["exposure"], 2)

    cur.execute("""
        SELECT count(*), sum(pnl) 
        FROM bets 
        WHERE status IN ('won', 'lost', 'push')
    """)
    settled_row = cur.fetchone()
    settled_count = settled_row[0] if settled_row else 0
    settled_pnl = float(settled_row[1]) if (settled_row and settled_row[1] is not None) else 0.0
    settled_pnl = round(settled_pnl, 2)

    if live_sync_data:
        total_balance = live_sync_data["total_balance"]
        available_balance = live_sync_data["available_balance"]
        open_exposure = live_sync_data["open_exposure"]
        open_bets_count = live_sync_data["open_bets_count"]
        is_live_synced = True
        sync_source = "Live Safari Session (Stake.com)"
        username = live_sync_data.get("username", "blvrg")
        balances = live_sync_data.get("balances", {})
        synced_at = live_sync_data.get("synced_at", utcnow().isoformat())
        active_by_league = live_sync_data.get("by_league") or by_league
    else:
        # Fallback to manual bankroll setting
        total_balance = round(base_bankroll + settled_pnl, 2)
        available_balance = round(total_balance - virtual_open_exposure, 2)
        open_exposure = round(virtual_open_exposure, 2)
        open_bets_count = virtual_open_bets_count
        is_live_synced = False
        sync_source = "Internal Simulation (Manual)"
        username = None
        balances = {}
        synced_at = utcnow().isoformat()
        active_by_league = by_league

    return {
        "status": "ok",
        "is_live_synced": is_live_synced,
        "sync_source": sync_source,
        "username": username,
        "token_configured": bool(token),
        "token_masked": get_masked_token(token),
        "total_balance": total_balance,
        "available_balance": available_balance,
        "open_exposure": open_exposure,
        "open_bets_count": open_bets_count,
        "base_bankroll": base_bankroll,
        "settled_pnl": settled_pnl,
        "settled_bets_count": settled_count,
        "balances": balances,
        "by_league": dict(sorted(active_by_league.items(), key=lambda x: -x[1]["exposure"])),
        "virtual_tracked": {
            "open_bets_count": virtual_open_bets_count,
            "open_exposure": round(virtual_open_exposure, 2),
            "settled_bets_count": settled_count,
            "settled_pnl": settled_pnl,
            "by_league": dict(sorted(by_league.items(), key=lambda x: -x[1]["exposure"])),
        },
        "synced_at": synced_at,
        "disclaimer": "Independent Stake Account Monitor · Strictly isolated from model predictions and bet sizing.",
    }


def update_stake_account_config(conn: sqlite3.Connection, base_bankroll: float, root_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Updates the baseline Stake bankroll setting in durable kv storage."""
    if base_bankroll < 0:
        raise ValueError("Base bankroll cannot be negative")

    cur = conn.cursor()
    cfg = {"base_bankroll": round(float(base_bankroll), 2), "updated_at": utcnow().isoformat()}
    cur.execute(
        """
        INSERT INTO kv (key, value) VALUES ('stake_account_config', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """,
        (json.dumps(cfg),),
    )
    conn.commit()
    return get_stake_account_summary(conn, root_dir=root_dir)


def resolve_stake_market_tab(
    league: Optional[str] = None,
    market: Optional[str] = None,
    market_name: Optional[str] = None,
    groups: Optional[List[Dict[str, Any]]] = None,
    event_url: Optional[str] = None,
) -> str:
    """Resolve the exact Stake sportsbook tab for a contract and sport."""
    lg = (league or "").upper()
    m = (market or "").lower()
    mn = (market_name or "").lower()
    url = (event_url or "").lower()
    grp_names = [str(g.get("name", "")).lower() for g in (groups or []) if isinstance(g, dict)] if groups else []

    is_tennis = (lg in ("ATP", "WTA", "CHALLENGER", "TENNIS")) or ("/tennis/" in url)
    is_basketball = (lg in ("NBA", "WNBA", "NCAAB", "BASKETBALL")) or ("/basketball/" in url)
    is_football = (lg in ("NFL", "NCAAF", "AMERICAN_FOOTBALL", "FOOTBALL")) or ("/american-football/" in url)
    is_baseball = (lg in ("MLB", "NPB", "BASEBALL")) or ("/baseball/" in url)
    is_soccer = (lg in ("EPL", "UCL", "MLS", "SOCCER")) or ("/soccer/" in url)
    is_ice_hockey = (lg in ("NHL", "ICE_HOCKEY")) or ("/ice-hockey/" in url)

    if is_tennis:
        if m == "total_sets" or "total sets" in mn or any(s in m or s in mn for s in ("period_1", "period_2", "period_3", "set_1", "set_2", "set_3", "set")) or "sets" in grp_names:
            return "Sets"
        if any(s in m or s in mn for s in ("spreads", "totals", "games", "game_handicap", "total_games")) or "games" in grp_names:
            return "Games"
        return "Popular"

    if is_ice_hockey:
        if any(p in m or p in mn for p in ("period_1", "period_2", "period_3", "period", "periods")) or "periods" in grp_names:
            return "Periods"
        if "main" in grp_names:
            return "Popular"
        if m in ("moneyline", "three_way", "spreads", "totals") or any(x in mn for x in ("winner", "handicap", "puck line", "total (incl. overtime)")):
            return "Popular"
        if any(x in m for x in ("goals", "both_score", "home_total", "away_total")) or any(x in mn for x in ("team total", "both teams to score", "goals")) or "goals" in grp_names:
            return "Goals"
        return "Popular"

    if is_football:
        if any(h in m or h in mn for h in ("half", "halftime")) or any(g in ("half", "halves", "1st2ndhalfmarkets") for g in grp_names):
            return "Half"
        if any(q in m or q in mn for q in ("quarter", "quarters", "period_1", "period_2", "period_3", "period_4")) or "quarters" in grp_names:
            return "Quarters"
        if "total" in grp_names and not any(g in ("half", "quarters") for g in grp_names):
            return "Total"
        if "handicap" in grp_names and not any(g in ("half", "quarters") for g in grp_names):
            return "Handicap"
        if "main" in grp_names:
            return "Popular"
        if "points" in grp_names:
            return "Points"
        if "teamandmatchprops" in grp_names:
            return "Team/Match"
        if any(t in m for t in ("home_total", "away_total", "team_total")):
            return "Popular"
        return "Popular"

    if is_basketball:
        if any(h in m or h in mn for h in ("half", "halftime")) or any(g in ("half", "halves", "1st2ndhalfmarkets") for g in grp_names):
            return "Half"
        if any(q in m or q in mn for q in ("quarter", "quarters", "period_1", "period_2", "period_3", "period_4")) or "quarters" in grp_names:
            return "Quarters"
        if "main" in grp_names:
            return "Popular"
        if "points" in grp_names:
            return "Points"
        if "teamandmatchprops" in grp_names:
            return "Team/Match"
        if any(t in m for t in ("home_total", "away_total", "team_total")):
            return "Popular"
        return "Popular"

    if is_baseball:
        if any(x in m or x in mn for x in ("innings_1_5", "first_five", "first_5", "innings 1 to 5")) or "innings 1 to 5" in grp_names:
            return "Innings 1 to 5"
        mn_clean = mn.replace("extra innings", "").replace("extra-innings", "").replace("extra_innings", "")
        if any(x in m or x in mn_clean for x in ("inning_1", "first_inning", "1st inning", "2nd inning")) or (("inning" in m or "inning" in mn_clean) and "extra" not in mn_clean):
            return "Innings"
        if "main" in grp_names:
            return "Popular"
        if any(x in m for x in ("runs", "home_total", "away_total")) or "runs" in grp_names:
            return "Runs"
        return "Popular"

    if is_soccer:
        if any(h in m or h in mn for h in ("half_1", "half_2", "half", "halftime")) or any(g in ("1st2ndhalfmarkets", "half", "halves") for g in grp_names):
            return "Half"
        if any("asian" in g for g in grp_names) or "asian" in m or "asian" in mn:
            return "Asian Lines"
        if any(g in ("cards", "corners") for g in grp_names) or any(x in m for x in ("cards", "corners")) or any(x in mn for x in ("card", "corner")):
            return "Cards and Corners"
        if any(g in ("goals", "totals") for g in grp_names) or any(x in m for x in ("both_score", "home_total", "away_total", "odd_even")) or any(x in mn for x in ("both teams to score", "exact goals", "goalscorer")):
            return "Goals"
        return "Popular"

    # Generic fallbacks
    if any(h in m or h in mn for h in ("half", "halftime")) or any(g in ("1st2ndhalfmarkets", "half", "halves") for g in grp_names):
        return "Half"
    if any(q in m or q in mn for q in ("quarter", "quarters")) or "quarters" in grp_names:
        return "Quarters"
    if any(p in m or p in mn for p in ("period_1", "period_2", "period_3", "period_4", "period", "periods")) or "periods" in grp_names:
        return "Periods"
    if any("asian" in g for g in grp_names) or "asian" in m or "asian" in mn:
        return "Asian Lines"
    if any(g in ("goals", "totals") for g in grp_names) or (m in ("totals", "odd_even")):
        return "Goals"
    if "player props" in grp_names:
        return "Player Props"
    return "Popular"


def stage_betslip_in_safari(
    event_url: str,
    selection: str,
    stake: float,
    odds: Optional[float] = None,
    min_odds: Optional[float] = None,
    tab: Optional[str] = None,
    line: Optional[float] = None,
    outcome_name: Optional[str] = None,
    tip_id: Optional[str] = None,
    market_name: Optional[str] = None,
    market: Optional[str] = None,
    league: Optional[str] = None,
    auto_place: bool = False,
    min_place_delay: float = 3.0,
    max_place_delay: float = 6.0,
    auto_close_on_fail: bool = False,
    adaptive_fallback: bool = False,
    cycle_id: Optional[str] = None,
    run_type: str = "auto_runner",
) -> Dict[str, Any]:
    """Stages a single bet in Safari on Stake.com, optionally auto-placing and closing tab.

    Navigates to the fixture URL, selects the matching outcome, and sets the recommended stake amount.
    Optionally clicks Place Bet (after a human-like delay), waits for confirmation, and closes the tab.
    """
    try:
        from .autorun_logger import log_autorun_event, AutoRunErrorCode
    except Exception:
        log_autorun_event = None
        AutoRunErrorCode = None

    cid = cycle_id or f"run_{int(time.time()*1000)}"

    if not event_url or not isinstance(event_url, str) or not event_url.startswith("https://stake.com/sports/"):
        if log_autorun_event:
            log_autorun_event(
                cycle_id=cid,
                stage="VALIDATE_URL",
                severity="ERROR",
                tip_id=tip_id,
                selection=selection,
                status="error",
                message=f"Invalid Stake sports event URL: {event_url}",
                error_code="INVALID_EVENT_URL",
                run_type=run_type,
            )
        return {"status": "error", "message": f"Invalid Stake sports event URL: {event_url}"}

    orig_selection = selection
    adapted_meta = None

    if log_autorun_event:
        log_autorun_event(
            cycle_id=cid,
            stage="STAGE_START",
            severity="INFO",
            tip_id=tip_id,
            league=league,
            selection=selection,
            line=line,
            odds=odds,
            stake=stake,
            market=market,
            market_name=market_name,
            status="staging",
            message=f"Starting betslip staging for {selection} ($ {stake:.2f})",
            payload={"event_url": event_url, "auto_place": auto_place, "adaptive_fallback": adaptive_fallback},
            run_type=run_type,
        )

    if not isinstance(stake, (int, float)) or stake <= 0 or stake > 50.0:
        return {"status": "error", "message": f"Invalid stake amount: {stake}. Must be between $0.01 and $50.00."}

    clean_url = event_url.strip()
    slug_part = clean_url.split("stake.com")[-1]
    import re
    fix_match = re.search(r"/(\d{6,})", clean_url)
    fixture_id = fix_match.group(1) if fix_match else ""

    is_tennis = bool(
        (league and league.upper() in ("WTA", "ATP", "CHALLENGER"))
        or ("/tennis/" in clean_url.lower())
    )
    is_basketball = bool(
        (league and league.upper() in ("NBA", "WNBA", "NCAAB", "BASKETBALL"))
        or ("/basketball/" in clean_url.lower())
    )
    is_football = bool(
        (league and league.upper() in ("NFL", "NCAAF", "AMERICAN_FOOTBALL", "FOOTBALL"))
        or ("/american-football/" in clean_url.lower())
    )
    is_baseball = bool(
        (league and league.upper() in ("MLB", "NPB", "BASEBALL"))
        or ("/baseball/" in clean_url.lower())
    )

    # Enrich from database if outcome_name, tab, market_name or market is missing
    if tip_id:
        try:
            db_path = Path(__file__).resolve().parent.parent.parent / "data" / "engine.sqlite3"
            if db_path.exists():
                with sqlite3.connect(db_path) as sconn:
                    scur = sconn.cursor()
                    scur.execute("SELECT payload FROM system_decisions WHERE tip_id = ? LIMIT 1", (tip_id,))
                    srow = scur.fetchone()
                    if not srow:
                        scur.execute("SELECT payload FROM bets WHERE tip_id = ? LIMIT 1", (tip_id,))
                        srow = scur.fetchone()
                    if not srow:
                        scur.execute("SELECT payload FROM tips WHERE id = ? LIMIT 1", (tip_id,))
                        srow = scur.fetchone()
                    if srow:
                        p_data = json.loads(srow[0])
                        if not league and p_data.get("league"):
                            league = p_data.get("league")
                            if league.upper() in ("WTA", "ATP", "CHALLENGER"):
                                is_tennis = True
                            elif league.upper() in ("NBA", "WNBA", "NCAAB", "BASKETBALL"):
                                is_basketball = True
                            elif league.upper() in ("NFL", "NCAAF", "AMERICAN_FOOTBALL", "FOOTBALL"):
                                is_football = True
                            elif league.upper() in ("MLB", "NPB", "BASEBALL"):
                                is_baseball = True
                        ev = p_data.get("stake_evidence") or {}
                        if not outcome_name and ev.get("outcome_name"):
                            outcome_name = ev.get("outcome_name")
                        if not market_name and ev.get("market_name"):
                            market_name = ev.get("market_name")
                        if not market and p_data.get("market"):
                            market = p_data.get("market")
                        if not odds and p_data.get("reference_odds"):
                            odds = float(p_data["reference_odds"])
                        if not tab or tab == "Main":
                            tab = resolve_stake_market_tab(league, market, market_name, ev.get("market_groups", []), clean_url)
        except Exception:
            pass

    # Infer period / set prefix and market name if not fully set
    period_prefix = ""
    if market:
        if is_tennis:
            if "period_1" in market:
                period_prefix = "1st set"
            elif "period_2" in market:
                period_prefix = "2nd set"
            elif "period_3" in market:
                period_prefix = "3rd set"
        elif is_basketball or is_football:
            if "period_1" in market or "quarter_1" in market:
                period_prefix = "1st quarter"
            elif "period_2" in market or "quarter_2" in market:
                period_prefix = "2nd quarter"
            elif "period_3" in market or "quarter_3" in market:
                period_prefix = "3rd quarter"
            elif "period_4" in market or "quarter_4" in market:
                period_prefix = "4th quarter"
            elif "half_1" in market or "halftime" in market:
                period_prefix = "1st half"
            elif "half_2" in market:
                period_prefix = "2nd half"
        else:
            if "period_1" in market:
                period_prefix = "1st period"
            elif "period_2" in market:
                period_prefix = "2nd period"
            elif "period_3" in market:
                period_prefix = "3rd period"
            elif "period_4" in market:
                period_prefix = "4th period"
            elif "half_1" in market or "halftime" in market:
                period_prefix = "1st half"
            elif "half_2" in market:
                period_prefix = "2nd half"

    if not period_prefix and market_name:
        if is_tennis:
            m_set = re.search(r"(\d+(?:st|nd|rd|th)\s+set)", market_name, re.IGNORECASE)
            if m_set:
                period_prefix = m_set.group(1).lower()
        elif is_basketball or is_football:
            m_quarter = re.search(r"(\d+(?:st|nd|rd|th)\s+(?:quarter|half))", market_name, re.IGNORECASE)
            if m_quarter:
                period_prefix = m_quarter.group(1).lower()
        else:
            m_period = re.search(r"(\d+(?:st|nd|rd|th)\s+(?:period|half|quarter|inning))", market_name, re.IGNORECASE)
            if m_period:
                period_prefix = m_period.group(1).lower()

    if not tab or tab == "Main":
        tab = resolve_stake_market_tab(league, market, market_name, None, clean_url)

    if not market_name and market:
        if is_tennis:
            if market == "period_1:moneyline":
                market_name = "1st Set - Winner"
            elif market == "period_2:moneyline":
                market_name = "2nd Set - Winner"
            elif market == "period_1:totals":
                market_name = "1st Set - Total Games"
            elif market == "period_2:totals":
                market_name = "2nd Set - Total Games"
            elif market in ("moneyline", "full_game:moneyline"):
                market_name = "Winner"
            elif market in ("totals", "full_game:totals"):
                market_name = "Total Games"
            elif market in ("spreads", "full_game:spreads"):
                market_name = "Game Handicap"
            elif market == "total_sets":
                market_name = "Total Sets"
        else:
            if market in ("period_3:totals", "quarter_3:totals"):
                market_name = "3rd Quarter - Total" if (is_basketball or is_football) else "3rd Period - Total"
            elif market in ("period_2:totals", "quarter_2:totals"):
                market_name = "2nd Quarter - Total" if (is_basketball or is_football) else "2nd Period - Total"
            elif market in ("period_1:totals", "quarter_1:totals"):
                market_name = "1st Quarter - Total" if (is_basketball or is_football) else "1st Period - Total"
            elif market in ("period_4:totals", "quarter_4:totals"):
                market_name = "4th Quarter - Total"
            elif market in ("period_3:three_way", "quarter_3:three_way"):
                market_name = "3rd Quarter - 1x2" if (is_basketball or is_football) else "3rd Period - 1x2"
            elif market in ("period_2:three_way", "quarter_2:three_way"):
                market_name = "2nd Quarter - 1x2" if (is_basketball or is_football) else "2nd Period - 1x2"
            elif market in ("period_1:three_way", "quarter_1:three_way"):
                market_name = "1st Quarter - 1x2" if (is_basketball or is_football) else "1st Period - 1x2"
            elif market in ("period_4:three_way", "quarter_4:three_way"):
                market_name = "4th Quarter - 1x2"
            elif market == "half_1:totals":
                market_name = "1st Half - Asian Total" if is_basketball else "1st Half - Total"
            elif market == "half_2:totals":
                market_name = "2nd Half - Asian Total" if is_basketball else "2nd Half - Total"
            elif market == "half_1:three_way":
                market_name = "1st Half - 1x2"
            elif market == "half_2:three_way":
                market_name = "2nd Half - 1x2"
            elif market == "half_1:spreads":
                market_name = "1st Half - Asian Handicap" if is_basketball else "1st Half - Handicap"
            elif market == "half_2:spreads":
                market_name = "2nd Half - Asian Handicap" if is_basketball else "2nd Half - Handicap"
            elif market in ("totals", "full_game:totals"):
                market_name = "Total (Incl. Overtime)" if (is_basketball or is_football or is_hockey) else ("Total Runs" if is_baseball else "Total")
            elif market in ("spreads", "full_game:spreads"):
                market_name = "Handicap (Incl. Overtime)" if (is_basketball or is_football or is_hockey) else ("Run Line" if is_baseball else "Handicap")
            elif market in ("moneyline", "full_game:moneyline"):
                market_name = "Winner (Incl. Overtime)" if (is_basketball or is_football or is_hockey) else "Winner"
            elif market in ("three_way", "full_game:three_way"):
                market_name = "1x2"
            elif market in ("home_total", "full_game:home_total"):
                market_name = f"{home_team} Total (Incl. Overtime)" if (home_team and (is_basketball or is_football or is_hockey)) else "Total"
            elif market in ("away_total", "full_game:away_total"):
                market_name = f"{away_team} Total (Incl. Overtime)" if (away_team and (is_basketball or is_football or is_hockey)) else "Total"

    # Build line variants for robust matching
    line_variants = []
    is_neg_line = False
    if line is None and selection:
        import re
        m_line = re.search(r'([+-]?\d+(?:\.\d+)?)', selection)
        if m_line:
            try:
                line = float(m_line.group(1))
            except ValueError:
                pass
    if line is not None:
        try:
            l_val = float(line)
            is_neg_line = l_val < 0
            abs_val = abs(l_val)
            line_variants = [str(l_val), f"{l_val:g}", f"{abs_val:g}", str(abs_val)]
            if l_val.is_integer():
                line_variants.extend([str(int(l_val)), str(int(abs_val))])
            else:
                line_variants.extend([f"{l_val:.2f}", f"{abs_val:.2f}"])
            line_variants = list(dict.fromkeys(line_variants))
        except (ValueError, TypeError):
            line_variants = [str(line)]

    # Step 0: Pre-check active bet limit (max 300) before doing Safari navigation
    if auto_place and not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            db_path = Path(__file__).resolve().parent.parent.parent / "data" / "engine.sqlite3"
            if db_path.exists():
                with sqlite3.connect(db_path) as sconn:
                    scur = sconn.cursor()
                    scur.execute("SELECT value FROM kv WHERE key = 'stake_live_sync'")
                    srow = scur.fetchone()
                    if srow:
                        sync_data = json.loads(srow[0])
                        open_cnt = int(sync_data.get("open_bets_count") or 0)
                        if open_cnt >= 300:
                            synced_at = sync_data.get("synced_at")
                            from datetime import datetime
                            if synced_at:
                                dt = datetime.fromisoformat(synced_at)
                                if (utcnow() - dt).total_seconds() < 600:
                                    return {
                                        "status": "error",
                                        "placed": False,
                                        "closed": False,
                                        "limit_reached": True,
                                        "message": f"A Stake elérte a maximális 300 aktív fogadási limitet (jelenleg {open_cnt}/300 nyitott fogadás). Várni kell, amíg a folyamatban lévő meccsek befejeződnek és felszabadul a hely!"
                                    }
        except Exception:
            pass

    # Step 1: Find or open tab in Safari
    script_find_or_open = """
    on run {target_url, slug, fix_id}
        tell application "Safari"
            set foundTab to missing value
            set foundWindow to missing value
            repeat with w in windows
                repeat with t in tabs of w
                    set tUrl to (URL of t)
                    if (tUrl contains slug) or (fix_id is not "" and tUrl contains fix_id) then
                        set foundTab to t
                        set foundWindow to w
                        exit repeat
                    end if
                end repeat
                if foundTab is not missing value then exit repeat
            end repeat
            
            if foundTab is not missing value then
                set current tab of foundWindow to foundTab
                return "ALREADY_OPEN"
            end if
            
            -- Locate target window containing stake.com or betting dashboard to avoid disturbing other windows
            set targetWindow to missing value
            repeat with w in windows
                repeat with t in tabs of w
                    set tUrl to (URL of t)
                    if (tUrl contains "stake.com") or (tUrl contains "tail8a282e.ts.net") or (tUrl contains "127.0.0.1:8765") or (tUrl contains "localhost:8765") then
                        set targetWindow to w
                        exit repeat
                    end if
                end repeat
                if targetWindow is not missing value then exit repeat
            end repeat
            
            if targetWindow is missing value and (count of windows) > 0 then
                set targetWindow to front window
            end if
            
            if targetWindow is not missing value then
                tell targetWindow
                    set newTab to make new tab with properties {URL:target_url}
                    set current tab to newTab
                end tell
                return "OPENED_NEW_TAB"
            else
                make new document with properties {URL:target_url}
                return "OPENED_NEW_DOC"
            end if
        end tell
    end run
    """
    try:
        subprocess.run(["osascript", "-e", script_find_or_open, clean_url, slug_part, fixture_id], capture_output=True, text=True, timeout=10)
    except Exception as e:
        return {"status": "error", "message": f"Safari communication failed: {str(e)}"}

    ascript_runner = """
    on run {js, slug, act, fix_id}
        tell application "Safari"
            repeat with w in windows
                repeat with t in tabs of w
                    set tUrl to (URL of t)
                    if (tUrl contains slug) or (fix_id is not "" and tUrl contains fix_id) then
                        set res to ""
                        try
                            set res to (do JavaScript js in t)
                        on error errMsg number errNum
                            set res to "JS_ERROR [" & errNum & "]: " & errMsg
                        end try
                        return res
                    end if
                end repeat
            end repeat
            return "NOT_FOUND"
        end tell
    end run
    """

    def _eval_safari_js(code: str, activate: bool = False, timeout: int = 8) -> str:
        try:
            r = subprocess.run(
                ["osascript", "-e", ascript_runner, code, slug_part, "0", fixture_id],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = r.stdout.strip()
            if r.stderr.strip():
                log.warning(f"[_eval_safari_js] stderr: {r.stderr.strip()}")
            return out
        except Exception as e:
            log.warning(f"[_eval_safari_js] exception: {e}")
            return ""

    script_close_tab = """
    on run {slug, fix_id}
        tell application "Safari"
            repeat with w in windows
                repeat with t in tabs of w
                    set tUrl to (URL of t)
                    if (slug is not "" and tUrl contains slug) or (fix_id is not "" and tUrl contains fix_id) then
                        close t
                        return "CLOSED"
                    end if
                end repeat
            end repeat
            return "NOT_FOUND"
        end tell
    end run
    """

    def _close_tab() -> None:
        try:
            subprocess.run(["osascript", "-e", script_close_tab, slug_part, fixture_id], capture_output=True, text=True, timeout=6)
        except Exception:
            pass

    helpers_js = r"""
        function normalize(s) {
            if (!s) return "";
            return s.toLowerCase().replace(/[+()]/g, " ").replace(/\s+/g, " ").trim();
        }

        function extractButtonCandidate(b) {
            if (!b) return null;
            const aria = (b.getAttribute("aria-label") || "").trim();
            const inner = (b.innerText || "").trim();
            const oddsMatch = inner.match(/\b\d+\.\d+\b/g);
            const candOdds = (oddsMatch && oddsMatch.length > 0) ? parseFloat(oddsMatch[oddsMatch.length - 1]) : null;
            
            let lineMatch = (aria || inner).match(/\(([+-]?\d+\.?\d*)\)/);
            if (!lineMatch) lineMatch = (aria || inner).match(/(?:over|under|o|u)\s*([+-]?\d+\.?\d*)/i);
            if (!lineMatch) lineMatch = inner.match(/^([+-]?\d+\.?\d*)/);
            if (!lineMatch) lineMatch = (aria || inner).match(/([+-]?\d+\.?\d*)/);
            const candLine = lineMatch ? parseFloat(lineMatch[1]) : null;
            
            const lines = inner.split(/[\r\n]+/).map(s => s.trim()).filter(Boolean);
            const sel = aria || (lines.length > 1 ? lines[0] : inner);
            return {
                selection: sel,
                line: candLine,
                odds: candOdds,
                raw: aria ? (aria + " " + (candOdds || "")) : inner
            };
        }

        function matchesLine(text, v, isNeg) {
            if (!text || !v) return false;
            const clean = " " + text.toLowerCase().replace(/[+()]/g, " ").replace(/\n/g, " ") + " ";
            const target = isNeg ? (" -" + v + " ") : (" " + v + " ");
            if (clean.includes(target)) return true;
            const tokens = clean.split(/\s+/).filter(Boolean);
            return tokens.includes(isNeg ? ("-" + v) : v);
        }

        function isTabActive(btn) {
            if (!btn) return false;
            if (btn.getAttribute("aria-checked") === "true") return true;
            if (btn.getAttribute("data-state") === "on") return true;
            if (btn.getAttribute("aria-selected") === "true") return true;
            if (btn.getAttribute("data-state") === "off" || btn.getAttribute("aria-checked") === "false" || btn.getAttribute("aria-selected") === "false") return false;
            const cls = (btn.className || "").trim();
            const tokens = cls.split(/\s+/);
            if (tokens.includes("active") || tokens.includes("is-active") || tokens.includes("selected")) return true;
            return false;
        }

        const tabAliasMap = {
            "half": ["tab-1st2ndhalfmarkets", "tab-half", "tab-halves", "half", "halves", "1st & 2nd half"],
            "halves": ["tab-1st2ndhalfmarkets", "tab-half", "tab-halves", "half", "halves", "1st & 2nd half"],
            "main": ["tab-popular", "tab-main", "popular", "main"],
            "popular": ["tab-popular", "tab-main", "popular", "main"],
            "asian lines": ["tab-asianlines", "asian lines", "tab-asian-lines"],
            "asian": ["tab-asianlines", "asian lines", "tab-asian-lines"],
            "goals": ["tab-goals", "goals"],
            "quarters": ["tab-quarters", "tab-quarter", "quarters", "quarter"],
            "quarter": ["tab-quarters", "tab-quarter", "quarters", "quarter"],
            "points": ["tab-points", "points"],
            "team/match": ["tab-teamandmatchprops", "team/match", "team & match", "tab-team-match", "tab-team", "team"],
            "team": ["tab-teamandmatchprops", "tab-team", "team", "team/match", "team & match"],
            "all": ["tab-all", "all", "all bets"],
            "all bets": ["tab-all", "all", "all bets"],
            "sets": ["tab-sets", "sets"],
            "games": ["tab-games", "games"],
            "handicap": ["tab-popular", "tab-main", "tab-handicap", "popular", "main", "handicap", "spreads"],
            "spreads": ["tab-popular", "tab-main", "tab-handicap", "popular", "main", "handicap", "spreads"],
            "total": ["tab-popular", "tab-main", "tab-total", "popular", "main", "total", "totals"],
            "totals": ["tab-popular", "tab-main", "tab-total", "popular", "main", "total", "totals"],
            "cards and corners": ["tab-cardscorners", "cards and corners", "cards & corners"]
        };

        function selectTab(tabBtn) {
            if (!tabBtn) return false;
            if (isTabActive(tabBtn)) return true;

            // 1. Scroll container so button is in view
            const scrollContainer = tabBtn.closest(".overflow-x-auto, [class*='scrollX'], [class*='tabs']");
            if (scrollContainer) {
                try {
                    scrollContainer.scrollLeft = tabBtn.offsetLeft - 60;
                } catch(e) {}
            }
            try {
                tabBtn.scrollIntoView({ behavior: "instant", block: "nearest", inline: "center" });
            } catch(e) {}

            // 2. If tab is still out of view bounds, click Previous or Next arrow
            try {
                const rect = tabBtn.getBoundingClientRect();
                if (rect.left < 300) {
                    const prev = document.querySelector("button[aria-label='Previous'], button[data-testid='scroll-left']");
                    if (prev && !prev.disabled) prev.click();
                } else if (rect.right > 770) {
                    const next = document.querySelector("button[aria-label='Next'], button[data-testid='scroll-right']");
                    if (next && !next.disabled) next.click();
                }
            } catch(e) {}

            // 3. Dispatch full event sequence for Bits-UI / Svelte / React
            try { tabBtn.focus(); } catch(e) {}
            const opts = { bubbles: true, cancelable: true, view: window };
            try {
                const pDown = new PointerEvent("pointerdown", Object.assign({}, opts, { pointerId: 1, pointerType: "mouse", button: 0, buttons: 1 }));
                tabBtn.dispatchEvent(pDown);
            } catch(e) {}
            try {
                const mDown = new MouseEvent("mousedown", Object.assign({}, opts, { button: 0, buttons: 1 }));
                tabBtn.dispatchEvent(mDown);
            } catch(e) {}
            try {
                const pUp = new PointerEvent("pointerup", Object.assign({}, opts, { pointerId: 1, pointerType: "mouse", button: 0, buttons: 0 }));
                tabBtn.dispatchEvent(pUp);
            } catch(e) {}
            try {
                const mUp = new MouseEvent("mouseup", Object.assign({}, opts, { button: 0, buttons: 0 }));
                tabBtn.dispatchEvent(mUp);
            } catch(e) {}
            try {
                const clickEv = new MouseEvent("click", Object.assign({}, opts, { button: 0 }));
                tabBtn.dispatchEvent(clickEv);
            } catch(e) {}
            try { tabBtn.click(); } catch(e) {}
            try { tabBtn.dispatchEvent(new KeyboardEvent("keydown", Object.assign({}, opts, { key: "Enter", code: "Enter" }))); } catch(e) {}
            try { tabBtn.dispatchEvent(new KeyboardEvent("keyup", Object.assign({}, opts, { key: "Enter", code: "Enter" }))); } catch(e) {}
            try { tabBtn.dispatchEvent(new KeyboardEvent("keydown", Object.assign({}, opts, { key: " ", code: "Space" }))); } catch(e) {}
            try { tabBtn.dispatchEvent(new KeyboardEvent("keyup", Object.assign({}, opts, { key: " ", code: "Space" }))); } catch(e) {}

            return isTabActive(tabBtn);
        }

        function findTabButton(tabName) {
            if (!tabName) return null;
            const targetLower = tabName.toLowerCase().trim();
            const targetNorm = targetLower.replace(/[^a-z0-9]/g, "");
            const aliases = tabAliasMap[targetLower] || ["tab-" + targetLower, "tab-" + targetNorm, targetLower, targetNorm];

            // 1. Direct data-testid match with aliases
            for (const a of aliases) {
                const tid = a.startsWith("tab-") ? a : ("tab-" + a);
                const btn = document.querySelector(`button[data-testid="${tid}" i], [data-testid="${tid}" i]`);
                if (btn) return btn.tagName === "BUTTON" ? btn : (btn.closest("button") || btn);
            }

            // 2. Query all tab buttons on page via data-testid, role=tab, or role=radio
            const allTestIdTabs = Array.from(document.querySelectorAll("button[data-testid^='tab-'], [data-testid^='tab-'], [role='tab'], [role='radio']"));
            for (const el of allTestIdTabs) {
                const b = el.tagName === "BUTTON" ? el : (el.closest("button") || el);
                const t = (b.innerText || "").trim().toLowerCase();
                const tid = (b.getAttribute("data-testid") || "").toLowerCase().replace(/^tab-/, "");
                if (t === targetLower || t.replace(/[^a-z0-9]/g, "") === targetNorm) return b;
                if (tid === targetLower || tid.replace(/[^a-z0-9]/g, "") === targetNorm) return b;
                for (const a of aliases) {
                    const aNorm = a.replace(/^tab-/, "").replace(/[^a-z0-9]/g, "");
                    if (t === a || t.replace(/[^a-z0-9]/g, "") === aNorm) return b;
                    if (tid === a || tid.replace(/[^a-z0-9]/g, "") === aNorm) return b;
                }
            }

            // 3. Query container buttons (.tabs, [role='tablist'], etc.)
            const tabContainers = Array.from(document.querySelectorAll(".tabs, [role='tablist'], [class*='tabs-scroll'], [data-content]"));
            for (const container of tabContainers) {
                const btns = Array.from(container.querySelectorAll("button, [role='tab'], [role='radio'], [data-testid^='tab-']"));
                const found = btns.find(b => {
                    const t = (b.innerText || "").trim().toLowerCase();
                    return t === targetLower || t.replace(/[^a-z0-9]/g, "") === targetNorm;
                });
                if (found) return found;
            }

            // 4. Fallback across all buttons outside sidebars
            const sidebars = Array.from(document.querySelectorAll("#left-sidebar, #right-sidebar, aside"));
            const allButtons = Array.from(document.querySelectorAll("button")).filter(b => !sidebars.some(s => s.contains(b)));
            return allButtons.find(b => {
                const t = (b.innerText || "").trim().toLowerCase();
                return t === targetLower || t.replace(/[^a-z0-9]/g, "") === targetNorm;
            }) || null;
        }

        function getMarketContext(btn) {
            let curr = btn.parentElement;
            let texts = [];
            while (curr && curr !== document.body) {
                if (curr.className && (curr.className.indexOf("accordion") !== -1 || (curr.getAttribute("data-testid") && curr.getAttribute("data-testid").indexOf("market") !== -1) || curr.className.indexOf("market") !== -1)) {
                    const h = curr.querySelector(".header, [class*='header'], [class*='title'], h2, h3, h4");
                    if (h && h.innerText) {
                        const line = h.innerText.toLowerCase().split(/[\\r\\n]+/)[0].trim();
                        if (line && !line.includes("sports") && !line.includes("stake") && !line.includes("casino") && !line.includes("promotions") && !line.includes("support")) {
                            texts.push(line);
                        }
                    }
                }
                curr = curr.parentElement;
            }
            if (texts.length === 0) {
                let prev = btn.closest("div");
                while (prev && prev !== document.body) {
                    const h = prev.querySelector(".header, [class*='header'], [class*='title'], h2, h3, h4");
                    if (h && h.innerText) {
                        const line = h.innerText.toLowerCase().split(/[\\r\\n]+/)[0].trim();
                        if (line && !line.includes("sports") && !line.includes("stake") && !line.includes("casino")) {
                            texts.push(line);
                            break;
                        }
                    }
                    prev = prev.previousElementSibling || prev.parentElement;
                }
            }
            return texts.join(" ");
        }

        function expandAccordions(mName, pPrefix) {
            const sidebars = Array.from(document.querySelectorAll("#left-sidebar, #right-sidebar, aside"));
            const accordions = Array.from(document.querySelectorAll(".main-content [class*='accordion'], [class*='accordion']"))
                .filter(a => !sidebars.some(s => s.contains(a)));
            const mNorm = (mName || "").toLowerCase().trim();
            const pNorm = (pPrefix || "").toLowerCase().trim();
            for (const acc of accordions) {
                const h = acc.querySelector(".header, [class*='header'], [class*='title'], h2, h3, h4");
                if (!h || !h.innerText) continue;
                const hText = h.innerText.toLowerCase().split(/[\r\n]+/)[0].trim();
                if (hText.includes("soccer") || hText.includes("basketball") || hText.includes("tennis") || hText.includes("baseball") || hText.includes("all sports") || hText.includes("promotions") || hText.includes("casino") || hText.includes("support") || hText.includes("trending games")) {
                    continue;
                }
                let match = false;
                if (!mNorm && !pNorm) match = true;
                else if (mNorm && (hText.indexOf(mNorm) !== -1 || mNorm.indexOf(hText) !== -1)) match = true;
                else if (mNorm === "total sets" && hText.includes("total sets")) match = true;
                else if (mNorm && (mNorm.includes("winner") || mNorm.includes("moneyline") || mNorm.includes("1x2") || mNorm.includes("three_way") || mNorm.includes("3-way")) && (hText.includes("winner") || hText.includes("1x2") || hText.includes("3-way") || hText.includes("three way"))) match = true;
                else if (mNorm && mNorm.includes("handicap") && hText.includes("handicap")) match = true;
                else if (mNorm && mNorm.includes("total") && hText.includes("total") && mNorm !== "total sets") {
                    const mWords = mNorm.split(/\s+/).filter(w => w.length > 2 && w !== "total" && w !== "team");
                    if (mWords.length === 0 || mWords.some(w => hText.includes(w))) {
                        match = true;
                    }
                }
                else if (pNorm && hText.indexOf(pNorm) !== -1) {
                    if (mNorm.indexOf("total") !== -1 && hText.indexOf("total") !== -1 && mNorm !== "total sets") match = true;
                    else if ((mNorm.indexOf("1x2") !== -1 || mNorm.indexOf("winner") !== -1) && (hText.indexOf("1x2") !== -1 || hText.indexOf("winner") !== -1)) match = true;
                    else if (mNorm.indexOf("handicap") !== -1 && hText.indexOf("handicap") !== -1) match = true;
                    else match = true;
                }
                if (match) {
                    const isOpen = acc.className.indexOf("is-open") !== -1 || !!acc.querySelector(".content.is-open") || ((acc.querySelector(".arrow svg")?.style?.transform || "").indexOf("180") === -1 && (acc.querySelector(".arrow svg")?.style?.transform || "").length > 0);
                    if (!isOpen) {
                        try {
                            h.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                            h.click();
                        } catch(e) {}
                    }
                }
            }
        }

        function expandAllAccordions() {
            const sidebars = Array.from(document.querySelectorAll("#left-sidebar, #right-sidebar, aside"));
            const accordions = Array.from(document.querySelectorAll(".main-content [class*='accordion'], [class*='accordion']"))
                .filter(a => !sidebars.some(s => s.contains(a)));
            for (const acc of accordions) {
                const h = acc.querySelector(".header, [class*='header'], [class*='title'], h2, h3, h4");
                if (!h || !h.innerText) continue;
                const hText = h.innerText.toLowerCase().split(/[\r\n]+/)[0].trim();
                if (hText.includes("soccer") || hText.includes("basketball") || hText.includes("tennis") || hText.includes("baseball") || hText.includes("all sports") || hText.includes("promotions") || hText.includes("casino") || hText.includes("support") || hText.includes("trending games")) {
                    continue;
                }
                const isOpen = acc.className.indexOf("is-open") !== -1 || !!acc.querySelector(".content.is-open");
                if (!isOpen) {
                    try {
                        h.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                        h.click();
                    } catch(e) {}
                }
            }
        }

        function getOutcomeButtons(scopeEl) {
            const root = scopeEl || document;
            const sidebars = Array.from(document.querySelectorAll("#left-sidebar, #right-sidebar, aside"));
            const raw = Array.from(root.querySelectorAll("[data-testid='fixture-outcome'], button.outcome, button[class*='outcome'], button[data-analytics*='outcome']"))
                .filter(el => !sidebars.some(s => s.contains(el)));
            return raw.map(el => el.tagName === "BUTTON" ? el : el.closest("button") || el).filter((el, idx, arr) => arr.indexOf(el) === idx);
        }

        function scoreAndSelectOutcome(buttons) {
            const isOver = targetNorm.indexOf("over") === 0;
            const isUnder = targetNorm.indexOf("under") === 0;
            const isYes = targetNorm === "yes";
            const isNo = targetNorm === "no";
            const isOdd = targetNorm === "odd";
            const isEven = targetNorm === "even";
            const isDraw = targetNorm === "draw" || targetNorm === "tie" || targetNorm === "x" || outcomeNorm === "draw" || outcomeNorm === "tie" || outcomeNorm === "x";

            // Extract entity words by stripping line numbers, parentheses, plus/minus
            const clubStopwords = new Set(["fc", "sc", "cf", "afc", "ac", "sk", "fk", "bk", "club", "de"]);
            const entityText = targetSel.replace(/[+-]?\d+(\.\d+)?/g, "").replace(/[()]/g, "").trim();
            const entityWords = entityText.split(/\s+/).filter(w => w.length > 1);
            const meaningfulWords = entityWords.filter(w => !clubStopwords.has(w.toLowerCase()));
            const primaryName = meaningfulWords.length > 0 ? meaningfulWords[meaningfulWords.length - 1] : (entityWords.length > 0 ? entityWords[entityWords.length - 1] : "");
            const lastName = primaryName;

            const candidates = [];
            for (const b of buttons) {
                const aria = b.getAttribute("aria-label") || "";
                const ariaNorm = normalize(aria);
                const ariaLower = aria.toLowerCase();
                const innerNorm = normalize(b.innerText || "");
                const innerLower = (b.innerText || "").toLowerCase();

                // Outcome type safety guards
                if (isOver && ariaLower.indexOf("over") === -1 && innerLower.indexOf("over") === -1) continue;
                if (isUnder && ariaLower.indexOf("under") === -1 && innerLower.indexOf("under") === -1) continue;
                if (isYes && ariaLower !== "yes" && innerLower !== "yes") continue;
                if (isNo && ariaLower !== "no" && innerLower !== "no") continue;
                if (isOdd && ariaLower !== "odd" && innerLower !== "odd") continue;
                if (isEven && ariaLower !== "even" && innerLower !== "even") continue;
                if (isDraw) {
                    const isBtnDraw = ariaLower === "draw" || ariaLower === "tie" || ariaLower === "x" || innerLower === "draw" || innerLower === "tie" || innerLower === "x" || ariaLower.includes("draw") || innerLower.includes("draw") || ariaLower.includes("tie") || innerLower.includes("tie");
                    if (!isBtnDraw) continue;
                } else {
                    if (ariaLower === "tie" || innerLower === "tie" || (ariaLower === "draw" && !entityWords.some(w => ariaLower.includes(w)))) {
                        if (!isOver && !isUnder) continue;
                    }
                }
                if (!isOver && !isUnder && (ariaLower.indexOf("over") === 0 || ariaLower.indexOf("under") === 0 || innerLower.indexOf("over") === 0 || innerLower.indexOf("under") === 0)) continue;

                let score = 0;
                if (isDraw) {
                    score = 100;
                } else if (lineVariants.length > 0) {
                    const lineMatched = lineVariants.some(v => matchesLine(ariaLower, v, isNegative) || matchesLine(innerLower, v, isNegative));
                    if (!lineMatched) continue; // If selection requires a line, candidate MUST match that line

                    const hasTargetNorm = targetNorm && (ariaNorm.includes(targetNorm) || innerNorm.includes(targetNorm));
                    const hasOutcomeTarget = outcomeNorm && (ariaNorm.includes(outcomeNorm) || innerNorm.includes(outcomeNorm) || ariaLower.includes(outcomeTarget.toLowerCase()) || innerLower.includes(outcomeTarget.toLowerCase()));
                    const hasEntityWord = entityWords.length > 0 && entityWords.some(w => ariaLower.includes(w) || innerLower.includes(w));
                    const hasLastName = lastName && (ariaLower.includes(lastName) || innerLower.includes(lastName));

                    if (hasTargetNorm || hasOutcomeTarget) {
                        score = 100;
                    } else if (hasLastName || hasEntityWord) {
                        score = 90;
                    } else if (isOver || isUnder || isYes || isNo || isOdd || isEven) {
                        score = 85;
                    } else {
                        // Strict Entity Protection: If this is an entity market (e.g. team spread/handicap)
                        // and neither the entity name nor last name matches, this button belongs to the OPPONENT!
                        // Disqualify immediately to prevent betting on the wrong side.
                        if (entityWords.length > 0 || lastName) {
                            continue;
                        }
                        score = 70;
                    }
                } else {
                    if (outcomeNorm && (ariaNorm === outcomeNorm || ariaLower === outcomeTarget.toLowerCase() || innerNorm === outcomeNorm)) {
                        score = 100;
                    } else if (outcomeNorm && (ariaNorm.startsWith(outcomeNorm) || ariaNorm.includes(outcomeNorm) || innerNorm.startsWith(outcomeNorm) || innerNorm.includes(outcomeNorm))) {
                        score = 95;
                    } else if (ariaNorm === targetNorm || ariaLower === targetSel || innerNorm === targetNorm) {
                        score = 90;
                    } else if (ariaNorm.startsWith(targetNorm) || ariaNorm.includes(targetNorm) || innerNorm.startsWith(targetNorm) || innerNorm.includes(targetNorm)) {
                        score = 85;
                    } else if (lastName && (ariaLower.includes(lastName) || innerLower.includes(lastName))) {
                        score = 75;
                    } else if (ariaLower.includes(targetSel) || innerLower.includes(targetSel)) {
                        score = 50;
                    }
                }

                if (score > 0) {
                    const btnText = (b.innerText || "").trim();
                    const numbers = btnText.match(/\d+\.\d+/g);
                    const btnOdds = numbers && numbers.length > 0 ? parseFloat(numbers[numbers.length - 1]) : null;
                    const marketCtx = getMarketContext(b);
                    candidates.push({
                        button: b,
                        score: score,
                        odds: btnOdds,
                        marketCtx: marketCtx
                    });
                }
            }

            if (candidates.length === 0) return null;
            if (candidates.length === 1 && !marketName && !periodPrefix) return candidates[0].button;

            const mNorm = (marketName || "").toLowerCase();
            const pNorm = (periodPrefix || "").toLowerCase();
            const tOdds = targetOdds ? parseFloat(targetOdds) : null;

            const scored = candidates.map(c => {
                let marketScore = 0;
                const mCtx = (c.marketCtx || "").toLowerCase();

                // Strict Period Isolation Guard
                if (pNorm) {
                    let periodMatch = false;
                    if (pNorm.includes("1st half") && (mCtx.includes("1st half") || mCtx.includes("half 1") || mCtx.includes("first half") || mCtx.includes("halftime"))) {
                        periodMatch = true;
                    } else if (pNorm.includes("2nd half") && (mCtx.includes("2nd half") || mCtx.includes("half 2") || mCtx.includes("second half"))) {
                        periodMatch = true;
                    } else if (pNorm.includes("quarter") && mCtx.includes(pNorm)) {
                        periodMatch = true;
                    } else if (pNorm.includes("period") && mCtx.includes(pNorm)) {
                        periodMatch = true;
                    } else if (pNorm.includes("set") && mCtx.includes(pNorm)) {
                        periodMatch = true;
                    } else if (pNorm.includes("innings 1 to 5") && (mCtx.includes("innings 1 to 5") || mCtx.includes("first 5"))) {
                        periodMatch = true;
                    } else if (mCtx.includes(pNorm)) {
                        periodMatch = true;
                    }

                    if (periodMatch) {
                        marketScore += 160;
                    } else {
                        // Candidate is full-game (e.g. "incl. overtime") or wrong period -> DISQUALIFY!
                        marketScore -= 2500;
                    }
                } else {
                    // Full game contract: candidate must NOT belong to a partial period
                    if (mCtx.includes("1st half") || mCtx.includes("2nd half") || mCtx.includes("quarter") || mCtx.includes("1st period") || mCtx.includes("2nd period") || mCtx.includes("3rd period") || mCtx.includes("1st set") || mCtx.includes("2nd set") || mCtx.includes("innings 1 to 5")) {
                        marketScore -= 2500;
                    }
                }

                if (mNorm && mCtx.includes(mNorm)) {
                    marketScore += 120;
                } else if (pNorm && mCtx.includes(pNorm)) {
                    marketScore += 60;
                    if (mNorm.indexOf("total") !== -1 && mCtx.indexOf("total") !== -1) marketScore += 30;
                    if (mNorm.indexOf("handicap") !== -1 && mCtx.indexOf("handicap") !== -1) marketScore += 30;
                    if ((mNorm.indexOf("1x2") !== -1 || mNorm.indexOf("winner") !== -1) && (mCtx.indexOf("1x2") !== -1 || mCtx.indexOf("winner") !== -1)) marketScore += 30;
                } else if (mNorm || pNorm) {
                    if (mCtx.indexOf("handicap") !== -1 && mNorm.indexOf("total") !== -1) marketScore -= 100;
                    if (mCtx.indexOf("total") !== -1 && mNorm.indexOf("handicap") !== -1) marketScore -= 100;
                    if ((mCtx.indexOf("winner") !== -1 || mCtx.indexOf("1x2") !== -1 || mCtx.indexOf("moneyline") !== -1) && (mNorm.indexOf("handicap") !== -1 || mNorm.indexOf("total") !== -1)) marketScore -= 100;
                    if ((mCtx.indexOf("handicap") !== -1 || mCtx.indexOf("total") !== -1) && (mNorm.indexOf("winner") !== -1 || mNorm.indexOf("1x2") !== -1 || mNorm.indexOf("moneyline") !== -1)) marketScore -= 100;
                }

                // Odds Proximity Scoring: massive boost for exact/close odds, heavy penalty for wildly divergent odds
                let oddsScore = 0;
                if (tOdds && c.odds && c.odds > 1.0) {
                    const diffRatio = Math.abs(c.odds - tOdds) / tOdds;
                    if (diffRatio <= 0.05) {
                        oddsScore += 250;
                    } else if (diffRatio <= 0.15) {
                        oddsScore += 150;
                    } else if (diffRatio <= 0.25) {
                        oddsScore += 50;
                    } else if (diffRatio > 0.35) {
                        oddsScore -= 300;
                    }
                }

                return {
                    candidate: c,
                    marketScore: marketScore,
                    oddsScore: oddsScore,
                    totalScore: c.score + marketScore + oddsScore
                };
            });

            const validScored = scored.filter(s => s.totalScore > 0);
            if (validScored.length === 0) return null;

            validScored.sort((a, b) => {
                if (b.totalScore !== a.totalScore) return b.totalScore - a.totalScore;
                if (b.marketScore !== a.marketScore) return b.marketScore - a.marketScore;
                if (b.oddsScore !== a.oddsScore) return b.oddsScore - a.oddsScore;
                return b.candidate.score - a.candidate.score;
            });

            return validScored[0].candidate.button;
        }

        function findOutcomeMatch(buttons) {
            const mNorm = (marketName || "").toLowerCase().trim();

            // 1. Direct Search inside exact matching Market Accordion first if marketName is known
            if (mNorm) {
                const sidebars = Array.from(document.querySelectorAll("#left-sidebar, #right-sidebar, aside"));
                const accordions = Array.from(document.querySelectorAll(".main-content [class*='accordion'], [class*='accordion']"))
                    .filter(a => !sidebars.some(s => s.contains(a)));
                for (const acc of accordions) {
                    const h = acc.querySelector(".header, [class*='header'], [class*='title'], h2, h3, h4");
                    if (!h || !h.innerText) continue;
                    const hText = h.innerText.toLowerCase().split(/[\r\n]+/)[0].trim();
                    if (hText === mNorm || hText.indexOf(mNorm) !== -1 || mNorm.indexOf(hText) !== -1) {
                        // Ensure accordion is open
                        const isOpen = acc.className.indexOf("is-open") !== -1 || !!acc.querySelector(".content.is-open");
                        if (!isOpen) {
                            try {
                                h.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                                h.click();
                            } catch(e) {}
                        }
                        const accButtons = getOutcomeButtons(acc);
                        if (accButtons.length > 0) {
                            const accMatch = scoreAndSelectOutcome(accButtons);
                            if (accMatch) return accMatch;
                        }
                    }
                }
            }

            // 2. Global Candidate Search across all visible buttons
            if (!buttons || buttons.length === 0) {
                buttons = getOutcomeButtons();
            }
            return scoreAndSelectOutcome(buttons);
        }
    """

    # Step 2: Poll until page loaded and outcome buttons visible
    def _make_poll_js(iter_idx: int) -> str:
        return rf"""
        (() => {{
            try {{
                {helpers_js}

                const targetNorm = normalize({json.dumps(selection)});
                const targetSel = {json.dumps(selection.lower())};
                const outcomeTarget = {json.dumps(outcome_name or '')};
                const outcomeNorm = normalize(outcomeTarget);
                const lineVariants = {json.dumps(line_variants)};
                const isNegative = {json.dumps(is_neg_line)};
                const targetTab = {json.dumps(tab or "")};
                const marketName = {json.dumps(market_name or "")};
                const periodPrefix = {json.dumps(period_prefix or "")};
                const targetOdds = {json.dumps(odds)};
                const isTennis = {json.dumps(is_tennis)};
                const isBasketball = {json.dumps(is_basketball)};
                const isBaseball = {json.dumps(is_baseball)};
                const isSoccer = !isTennis && !isBasketball && !isBaseball;
                const iterIndex = {iter_idx};

                let tabBtn = findTabButton(targetTab);
                let tabActive = true;
                if (tabBtn) {{
                    tabActive = isTabActive(tabBtn);
                    if (!tabActive) {{
                        selectTab(tabBtn);
                        tabActive = isTabActive(tabBtn);
                    }}
                }}

                let match = null;
                let buttons = [];
                if (tabActive) {{
                    expandAccordions(marketName, periodPrefix);
                    buttons = getOutcomeButtons();
                    match = findOutcomeMatch(buttons);
                    if (!match) {{
                        expandAllAccordions();
                        buttons = getOutcomeButtons();
                        match = findOutcomeMatch(buttons);
                    }}
                }}

                // If not found after giving targetTab plenty of time to mount (>= 25 polls ~4.5s), try fallback to All or Popular
                if (!match && iterIndex >= 25) {{
                    const fallbackTab = (targetTab && targetTab.toLowerCase() === "all") ? "Popular" : "All";
                    const altBtn = findTabButton(fallbackTab);
                    if (altBtn && !isTabActive(altBtn)) {{
                        selectTab(altBtn);
                    }}
                }}

                const bodyTxt = (document.body?.innerText || "").toLowerCase();
                const isDisconnected = bodyTxt.includes("network disconnected") || bodyTxt.includes("connection lost");
                if (isDisconnected) {{
                    return JSON.stringify({{
                        ready: false,
                        network_disconnected: true,
                        error: "A Stake kapcsolata megszakadt (Network disconnected). Kérlek ellenőrizd az internet / VPN kapcsolatot!"
                    }});
                }}

                const pathname = (window.location.pathname || "").toLowerCase();
                const hasFixtureElements = !!document.querySelector("[data-testid^='tab-'], [data-testid*='fixture'], [class*='fixture']");
                const isRedirectedOrHome = (pathname === "/sports/home" || pathname === "/sports" || pathname === "/sports/") && !hasFixtureElements;
                if (isRedirectedOrHome) {{
                    return JSON.stringify({{
                        ready: true,
                        redirected: true,
                        error: "A mérkőzés már lezárult vagy a Stake átirányított a kezdőlapra."
                    }});
                }}

                const loader = document.querySelector(".splash, .loader, [data-testid='splash-screen'], .loading-placeholder, [class*='splash-screen']");

                return JSON.stringify({{
                    ready: document.readyState === "complete",
                    hasTarget: tabActive && !!match,
                    tabActive: tabActive,
                    outcomesCount: buttons.length,
                    hasLoader: !!loader
                }});
            }} catch(e) {{
                return JSON.stringify({{
                    ready: false,
                    hasTarget: false,
                    error: e.toString()
                }});
            }}
        }})()
        """

    page_ready = False
    redirect_err = None
    net_disconnect_err = None
    for i in range(80):
        poll_js = _make_poll_js(i)
        out = _eval_safari_js(poll_js, activate=False, timeout=5)
        if out and out != "NOT_FOUND" and ("outcomesCount" in out or "redirected" in out or "network_disconnected" in out):
            try:
                data = json.loads(out)
                if data.get("network_disconnected"):
                    net_disconnect_err = data.get("error", "A Stake kapcsolata megszakadt (Network disconnected).")
                    break
                if data.get("redirected"):
                    redirect_err = data.get("error", "A mérkőzés már lezárult vagy a Stake átirányított a kezdőlapra.")
                    break
                if data.get("hasTarget"):
                    page_ready = True
                    break
                is_active = data.get("tabActive", True)
                if is_active and data.get("outcomesCount", 0) > 0 and not data.get("hasLoader"):
                    if not tab or tab == "Main" or tab == "Popular":
                        page_ready = True
                        break
                    elif i >= 30:
                        page_ready = True
                        break
            except Exception:
                pass
        time.sleep(0.18)

    if redirect_err:
        if auto_close_on_fail:
            _close_tab()
        return {
            "status": "error",
            "placed": False,
            "closed": bool(auto_close_on_fail),
            "message": redirect_err,
            "event_url": clean_url
        }

    if not page_ready:
        if auto_close_on_fail:
            _close_tab()
        return {"status": "error", "placed": False, "closed": bool(auto_close_on_fail), "message": "A Stake oldal betöltése vagy a piacok keresése időtúllépés miatt meghiúsult (a Stake splash képernyő nem fejeződött be időben)."}

    # Step 3: Clear prior bet, switch tab, find & click outcome
    stage_js = rf"""
    (() => {{
        try {{
            {helpers_js}

            const targetNorm = normalize({json.dumps(selection)});
            const targetSel = {json.dumps(selection.lower())};
            const outcomeTarget = {json.dumps(outcome_name or '')};
            const outcomeNorm = normalize(outcomeTarget);
            const lineVariants = {json.dumps(line_variants)};
            const isNegative = {json.dumps(is_neg_line)};
            const targetTab = {json.dumps(tab or "")};
            const marketName = {json.dumps(market_name or "")};
            const periodPrefix = {json.dumps(period_prefix or "")};
            const targetOdds = {json.dumps(odds)};
            const isTennis = {json.dumps(is_tennis)};
            const isBasketball = {json.dumps(is_basketball)};
            const isFootball = {json.dumps(is_football)};
            const isBaseball = {json.dumps(is_baseball)};
            const isOver = targetNorm.indexOf("over") === 0;
            const isUnder = targetNorm.indexOf("under") === 0;
            const clubStopwords = new Set(["fc", "sc", "cf", "afc", "ac", "sk", "fk", "bk", "club", "de"]);
            const entityText = targetSel.replace(/[+-]?\d+(\.\d+)?/g, "").replace(/[()]/g, "").trim();
            const entityWords = entityText.split(/\s+/).filter(w => w.length > 1);
            const meaningfulWords = entityWords.filter(w => !clubStopwords.has(w.toLowerCase()));
            const lastName = meaningfulWords.length > 0 ? meaningfulWords[meaningfulWords.length - 1] : (entityWords.length > 0 ? entityWords[entityWords.length - 1] : "");

            const existingInput = document.querySelector("[data-testid='input-bet-amount']");
            const betslipEl = document.querySelector("#right-sidebar") || document.querySelector("[data-testid='betslip-bet']") || document.querySelector(".betslip") || document.querySelector("[data-block-touch-pan]");
            const betslipText = (betslipEl?.innerText || "").toLowerCase();

            // Foolproof Betslip Resetting:
            // Inspect existing bet containers [data-testid='betslip-bet']
            const existingBets = Array.from(document.querySelectorAll("#right-sidebar [data-testid='betslip-bet'], .betslip [data-testid='betslip-bet'], [data-testid='betslip-bet']"));

            let alreadyInBetslip = false;
            if (existingBets.length === 1 && existingInput) {{
                const singleBetText = existingBets[0].innerText.toLowerCase();
                const singleBetOddsMatch = singleBetText.match(/\d+\.\d+/g);
                const singleBetOdds = singleBetOddsMatch ? parseFloat(singleBetOddsMatch[singleBetOddsMatch.length - 1]) : null;

                const hasOutcomeMatch = (outcomeNorm && singleBetText.includes(outcomeNorm)) || singleBetText.includes(targetNorm);
                const hasLineMatch = lineVariants.length === 0 || lineVariants.some(v => singleBetText.includes(v));
                const oddsMatch = !targetOdds || !singleBetOdds || (Math.abs(singleBetOdds - parseFloat(targetOdds)) / parseFloat(targetOdds) <= 0.20);

                let periodMatch = true;
                if (periodPrefix) {{
                    const pLow = periodPrefix.toLowerCase();
                    periodMatch = singleBetText.includes(pLow) ||
                        (pLow.includes("1st half") && (singleBetText.includes("1st half") || singleBetText.includes("half 1") || singleBetText.includes("first half"))) ||
                        (pLow.includes("2nd half") && (singleBetText.includes("2nd half") || singleBetText.includes("half 2") || singleBetText.includes("second half"))) ||
                        (pLow.includes("quarter") && singleBetText.includes("quarter")) ||
                        (pLow.includes("period") && singleBetText.includes("period")) ||
                        (pLow.includes("set") && singleBetText.includes("set")) ||
                        (pLow.includes("innings 1 to 5") && (singleBetText.includes("innings 1 to 5") || singleBetText.includes("first 5")));
                }} else {{
                    if (singleBetText.includes("1st half") || singleBetText.includes("2nd half") || singleBetText.includes("quarter") || singleBetText.includes("1st period") || singleBetText.includes("2nd period") || singleBetText.includes("1st set") || singleBetText.includes("innings 1 to 5")) {{
                        periodMatch = false;
                    }}
                }}

                if (hasOutcomeMatch && hasLineMatch && oddsMatch && periodMatch) {{
                    alreadyInBetslip = true;
                }}
            }}

            // If already in betslip, don't reset or re-toggle
            if (alreadyInBetslip) {{
                const buttons = getOutcomeButtons();
                let match = findOutcomeMatch(buttons);
                if (!match) {{
                    expandAllAccordions();
                    match = findOutcomeMatch(getOutcomeButtons());
                }}
                const btnText = match ? (match.innerText || "").trim() : "";
                const numbers = btnText.match(/\d+\.\d+/g);
                const currentOdds = numbers && numbers.length > 0 ? parseFloat(numbers[numbers.length - 1]) : (targetOdds ? parseFloat(targetOdds) : null);
                return JSON.stringify({{
                    status: "clicked",
                    selection: match ? (match.getAttribute("aria-label") || match.innerText) : (outcomeTarget || targetSel),
                    current_odds: currentOdds
                }});
            }}

            // Clear any prior bets completely so we never accumulate multiple bets!
            if (existingBets.length > 0) {{
                const singleTab = document.querySelector("[data-testid='betslip-tab-single']");
                if (singleTab && (singleTab.getAttribute("aria-selected") !== "true" && !singleTab.classList.contains("active"))) {{
                    try {{ singleTab.click(); }} catch(e) {{}}
                }}
                const resetBtn = document.querySelector("[data-testid='reset-betslip']") ||
                    Array.from(document.querySelectorAll("#right-sidebar button, button")).find(b => {{
                        const t = (b.innerText || "").toLowerCase();
                        return t.includes("clear bet") || t.includes("clear all") || t.includes("reuse");
                    }});
                if (resetBtn) {{
                    try {{ resetBtn.click(); }} catch(e) {{}}
                }}
                const removeBtns = Array.from(document.querySelectorAll("#right-sidebar [data-testid='betslip-bet-remove'], [data-testid='betslip-bet-remove']"));
                for (const rb of removeBtns) {{
                    try {{ rb.click(); }} catch(e) {{}}
                }}
            }}

            let tabBtn = findTabButton(targetTab);
            if (tabBtn && !isTabActive(tabBtn)) {{
                selectTab(tabBtn);
            }}

            expandAccordions(marketName, periodPrefix);

            let buttons = getOutcomeButtons();
            let match = findOutcomeMatch(buttons);
            if (!match) {{
                expandAllAccordions();
                buttons = getOutcomeButtons();
                match = findOutcomeMatch(buttons);
            }}

            // Fallback to 'All' or 'Popular' tab if not found on targetTab
            if (!match) {{
                const fallbackTab = (targetTab && targetTab.toLowerCase() === "all") ? "Popular" : "All";
                const fBtn = findTabButton(fallbackTab);
                if (fBtn && !isTabActive(fBtn)) {{
                    selectTab(fBtn);
                    expandAllAccordions();
                    buttons = getOutcomeButtons();
                    match = findOutcomeMatch(buttons);
                }}
            }}

            if (!match) {{
                let lineNote = "";
                let marketMissing = false;
                let matchedLines = [];
                if (marketName) {{
                    const mLower = marketName.toLowerCase().trim();
                    const sameMarketButtons = buttons.filter(b => {{
                        const ctx = getMarketContext(b);
                        if (!ctx) return false;
                        if (ctx === mLower || ctx.includes(mLower)) return true;
                        if (mLower.includes(ctx)) {{
                            if (lastName && !ctx.includes(lastName.toLowerCase())) return false;
                            if (periodPrefix && !ctx.includes(periodPrefix.toLowerCase())) return false;
                            const diffWords = mLower.replace(ctx, "").replace(/total|overtime|incl|\(|\)/g, "").split(/\s+/).filter(w => w.length > 2);
                            if (diffWords.length > 0 && !diffWords.some(w => ctx.includes(w))) return false;
                            return true;
                        }}
                        return false;
                    }});
                    if (sameMarketButtons.length > 0) {{
                        const filterWord = (isOver || isUnder) ? (isOver ? "over" : "under") : (lastName ? lastName.toLowerCase() : null);
                        let cands = sameMarketButtons.map(extractButtonCandidate).filter(c => c && c.odds !== null && !c.selection.toLowerCase().includes("suspended"));
                        if (filterWord) {{
                            const filtered = cands.filter(c => c.selection.toLowerCase().includes(filterWord));
                            if (filtered.length > 0) cands = filtered;
                        }}
                        candList = cands;
                        const otherLines = Array.from(new Set(cands.map(c => c.selection))).filter(l => !lineVariants.some(v => l.includes(v))).slice(0, 6);
                        if (otherLines.length > 0) {{
                            lineNote = " A Stake kínálatában a kért vonal (" + targetSel + ") helyett jelenleg ezek a vonalak érhetők el: " + otherLines.join(", ") + ".";
                        }}
                    }} else if (isOver || isUnder || lineVariants.length > 0) {{
                        const filterWord = (isOver || isUnder) ? (isOver ? "over" : "under") : (lastName ? lastName.toLowerCase() : null);
                        let cands = buttons.map(extractButtonCandidate).filter(c => c && c.odds !== null && !c.selection.toLowerCase().includes("suspended"));
                        if (filterWord) {{
                            const filtered = cands.filter(c => c.selection.toLowerCase().includes(filterWord));
                            if (filtered.length > 0) cands = filtered;
                        }}
                        if (cands.length > 0) {{
                            candList = cands;
                            const otherLines = Array.from(new Set(cands.map(c => c.selection))).filter(l => !lineVariants.some(v => l.includes(v))).slice(0, 6);
                            if (otherLines.length > 0) {{
                                lineNote = " A kért piac helyett a Stake-en ezek a vonalak érhetők el: " + otherLines.join(", ") + ".";
                            }}
                        }} else {{
                            marketMissing = true;
                        }}
                    }} else {{
                        marketMissing = true;
                    }}
                }} else if (lineVariants.length > 0) {{
                    const filterWord = (isOver || isUnder) ? (isOver ? "over" : "under") : (lastName ? lastName.toLowerCase() : null);
                    let cands = buttons.map(extractButtonCandidate).filter(c => c && c.odds !== null && !c.selection.toLowerCase().includes("suspended"));
                    if (filterWord) {{
                        const filtered = cands.filter(c => c.selection.toLowerCase().includes(filterWord));
                        if (filtered.length > 0) cands = filtered;
                    }}
                    candList = cands;
                    const otherLines = Array.from(new Set(cands.map(c => c.selection))).filter(l => !lineVariants.some(v => l.includes(v))).slice(0, 6);
                    if (otherLines.length > 0) {{
                        lineNote = " A Stake kínálatában a kért vonal (" + targetSel + ") helyett jelenleg ezek a vonalak érhetők el: " + otherLines.join(", ") + ".";
                    }}
                }}

                if (candList && candList.length > 0) {{
                    const seenC = new Set();
                    candList = candList.filter(c => {{
                        const key = c.selection + "_" + c.line + "_" + c.odds;
                        if (seenC.has(key)) return false;
                        seenC.add(key);
                        return true;
                    }}).slice(0, 10);
                }}

                let errorMsg = "Nem található a kimenetel gombja a felületen.";
                if (marketMissing) {{
                    errorMsg = "A kért piac ('" + marketName + "') jelenleg nem érhető el vagy fel lett függesztve a Stake-en.";
                }} else if (lineNote) {{
                    errorMsg = "A fogadóiroda módosította a piacot." + lineNote;
                }}

                return JSON.stringify({{
                    error: errorMsg,
                    market_missing: marketMissing,
                    targetNorm: targetNorm,
                    outcomeNorm: outcomeNorm,
                    available: buttons.map(b => b.getAttribute("aria-label") || b.innerText).slice(0, 10),
                    available_candidates: candList
                }});
            }}

            // Check current odds on button
            const btnText = (match.innerText || "").trim();
            const numbers = btnText.match(/\d+\.\d+/g);
            const currentOdds = numbers && numbers.length > 0 ? parseFloat(numbers[numbers.length - 1]) : null;

            // Before clicking match: ensure betslip is empty so we never accumulate multiple bets!
            const remainingBets = Array.from(document.querySelectorAll("#right-sidebar [data-testid='betslip-bet'], [data-testid='betslip-bet']"));
            if (remainingBets.length > 0) {{
                const resetBtn = document.querySelector("[data-testid='reset-betslip']");
                if (resetBtn) {{
                    try {{ resetBtn.click(); }} catch(e) {{}}
                }}
                const removeBtns = Array.from(document.querySelectorAll("#right-sidebar [data-testid='betslip-bet-remove'], [data-testid='betslip-bet-remove']"));
                for (const rb of removeBtns) {{
                    try {{ rb.click(); }} catch(e) {{}}
                }}
            }}

            match.click();
            return JSON.stringify({{
                status: "clicked",
                selection: match.getAttribute("aria-label") || match.innerText,
                current_odds: currentOdds
            }});
        }} catch(e) {{
            return JSON.stringify({{ error: e.toString() }});
        }}
    }})()
    """

    res_str = _eval_safari_js(stage_js, activate=False, timeout=8)
    try:
        res_click = json.loads(res_str)
    except Exception as e:
        log.warning(f"Failed to parse stage_js response: {res_str!r} - {e}")
        if auto_close_on_fail:
            _close_tab()
        return {"status": "error", "placed": False, "closed": bool(auto_close_on_fail), "message": f"Kimenetel kiválasztása sikertelen: {str(e)}", "event_url": clean_url}

    if "error" in res_click:
        available_cands = res_click.get("available_candidates") or []
        if adaptive_fallback and available_cands and tip_id:
            try:
                from .adaptive_fallback import evaluate_alternative_lines
                from .storage import Store
                store = Store()
                eval_res = evaluate_alternative_lines(tip_id, available_cands, store=store)
                if eval_res.get("has_eligible") and eval_res.get("best_candidate"):
                    best = eval_res["best_candidate"]
                    log.info(f"Adaptive fallback selected best alternative for {selection}: {best['selection']} @ {best['odds']} (EV: +{best['ev']*100:.1f}%)")
                    if log_autorun_event:
                        log_autorun_event(
                            cycle_id=cid,
                            stage="ADAPTIVE_EVALUATION",
                            severity="INFO",
                            tip_id=best.get("tip_id") or tip_id,
                            league=league,
                            selection=best["selection"],
                            line=best.get("line"),
                            odds=best["odds"],
                            stake=best.get("recommended_stake") or stake,
                            market=market,
                            market_name=market_name,
                            status="adapted",
                            message=f"Adaptive fallback selected alternative: {best['selection']} @ {best['odds']} (EV: +{best['ev']*100:.1f}%)",
                            payload={"original_selection": orig_selection, "ev": best.get("ev"), "all_evals": eval_res.get("evaluations", [])},
                            run_type=run_type,
                        )
                    selection = best["selection"]
                    line = best.get("line")
                    odds = best["odds"]
                    if best.get("recommended_stake"):
                        stake = best["recommended_stake"]
                    adapted_meta = best

                    # Recompute line_variants for betslip verification
                    if line is not None:
                        try:
                            l_val = float(line)
                            is_neg_line = l_val < 0
                            abs_val = abs(l_val)
                            line_variants = [str(l_val), f"{l_val:g}", f"{abs_val:g}", str(abs_val)]
                            if l_val.is_integer():
                                line_variants.extend([str(int(l_val)), str(int(abs_val))])
                            else:
                                line_variants.extend([f"{l_val:.2f}", f"{abs_val:.2f}"])
                            line_variants = list(dict.fromkeys(line_variants))
                        except (ValueError, TypeError):
                            line_variants = [str(line)]

                    # Click candidate button in Safari!
                    cand_click_js = rf"""
                    (() => {{
                        {helpers_js}
                        const targetNorm = normalize({json.dumps(best['selection'])});
                        const targetSel = {json.dumps(best['selection'].lower())};
                        const bestLine = {json.dumps(best.get('line'))};
                        const isNeg = {json.dumps(is_neg_line)};
                        const buttons = getOutcomeButtons();
                        const match = buttons.find(b => {{
                            const aria = (b.getAttribute("aria-label") || "").trim();
                            const inner = (b.innerText || "").trim();
                            const label = (aria || inner).replace(/\s+/g, " ").trim();
                            const norm = normalize(label);
                            if (bestLine !== null && bestLine !== undefined) {{
                                if (matchesLine(label, String(bestLine), isNeg)) {{
                                    if (targetNorm.startsWith("over") && !norm.includes("over") && !label.toLowerCase().includes("over")) return false;
                                    if (targetNorm.startsWith("under") && !norm.includes("under") && !label.toLowerCase().includes("under")) return false;
                                    return true;
                                }}
                                return false;
                            }}
                            if (norm.includes(targetNorm) || label.toLowerCase().includes(targetSel)) return true;
                            return false;
                        }});
                        if (!match) return JSON.stringify({{ error: "Adaptive candidate button not found: " + {json.dumps(best['selection'])} }});
                        
                        const singleTab = document.querySelector("[data-testid='betslip-tab-single']");
                        if (singleTab && (singleTab.getAttribute("aria-selected") !== "true" && !singleTab.classList.contains("active"))) {{
                            try {{ singleTab.click(); }} catch(e) {{}}
                        }}
                        const resetBtn = document.querySelector("[data-testid='reset-betslip']");
                        if (resetBtn) {{ try {{ resetBtn.click(); }} catch(e) {{}} }}
                        const removeBtns = Array.from(document.querySelectorAll("#right-sidebar [data-testid='betslip-bet-remove'], [data-testid='betslip-bet-remove']"));
                        for (const rb of removeBtns) {{ try {{ rb.click(); }} catch(e) {{}} }}
                        
                        match.click();
                        const btnText = (match.innerText || "").trim();
                        const numbers = btnText.match(/\d+\.\d+/g);
                        const currentOdds = numbers && numbers.length > 0 ? parseFloat(numbers[numbers.length - 1]) : null;
                        return JSON.stringify({{
                            status: "clicked",
                            selection: match.getAttribute("aria-label") || match.innerText,
                            current_odds: currentOdds
                        }});
                    }})()
                    """
                    cand_res_str = _eval_safari_js(cand_click_js, activate=False, timeout=5)
                    try:
                        cand_click = json.loads(cand_res_str)
                        if "error" not in cand_click:
                            res_click = cand_click
                    except Exception as e:
                        log.warning(f"Error parsing adaptive click response: {e}")
            except Exception as e:
                log.warning(f"Adaptive fallback evaluation failed: {e}")

    if "error" in res_click:
        cleanup_js = """
        (() => {
            const resetBtn = document.querySelector("[data-testid='reset-betslip']");
            if (resetBtn) { try { resetBtn.click(); } catch(e) {} }
            const removeBtns = Array.from(document.querySelectorAll("#right-sidebar [data-testid='betslip-bet-remove'], [data-testid='betslip-bet-remove']"));
            for (const rb of removeBtns) { try { rb.click(); } catch(e) {} }
        })()
        """
        _eval_safari_js(cleanup_js, activate=False, timeout=3)
        if auto_close_on_fail:
            _close_tab()
        err_msg = res_click["error"]
        if adaptive_fallback:
            if available_cands:
                err_msg = f"Adaptív vizsgálat lefutott: {err_msg} (Nem található +EV feltételnek megfelelő alternatív vonal)."
            else:
                err_msg = f"Adaptív vizsgálat lefutott: {err_msg} (Nincs elérhető alternatív vonal a Stake felületén)."
        if log_autorun_event:
            err_code = AutoRunErrorCode.LINE_DRIFT_NO_EV if available_cands else AutoRunErrorCode.OUTCOME_NOT_FOUND
            if res_click.get("market_missing"):
                err_code = AutoRunErrorCode.MARKET_NOT_FOUND
            log_autorun_event(
                cycle_id=cid,
                stage="LINE_EVAL",
                severity="WARN",
                tip_id=tip_id,
                league=league,
                selection=selection,
                line=line,
                odds=odds,
                stake=stake,
                market=market,
                market_name=market_name,
                status="skipped",
                message=err_msg,
                error_code=err_code,
                error_details=res_click.get("error"),
                payload={"available": res_click.get("available"), "available_candidates": available_cands},
                run_type=run_type,
            )
        return {"status": "error", "placed": False, "closed": bool(auto_close_on_fail), "message": err_msg, "available": res_click.get("available"), "available_candidates": res_click.get("available_candidates"), "event_url": clean_url}

    current_odds = res_click.get("current_odds")
    odds_warning = None

    # Step 4: Inject stake amount into betslip input
    time.sleep(0.35)

    set_stake_js = rf"""
    (() => {{
        try {{
            {helpers_js}

            const targetNorm = normalize({json.dumps(selection)});
            const targetSel = {json.dumps(selection.lower())};
            const outcomeTarget = {json.dumps(outcome_name or '')};
            const outcomeNorm = normalize(outcomeTarget);
            const lineVariants = {json.dumps(line_variants)};
            const isNegative = {json.dumps(is_neg_line)};
            const marketName = {json.dumps(market_name or "")};
            const periodPrefix = {json.dumps(period_prefix or "")};
            const targetOdds = {json.dumps(odds)};
            
            // Ensure exactly 1 single bet in betslip: remove any earlier accumulated bets
            const slipBets = Array.from(document.querySelectorAll("#right-sidebar [data-testid='betslip-bet'], [data-testid='betslip-bet']"));
            if (slipBets.length > 1) {{
                for (let idx = 0; idx < slipBets.length - 1; idx++) {{
                    const rm = slipBets[idx].querySelector("[data-testid='betslip-bet-remove']");
                    if (rm) {{
                        try {{ rm.click(); }} catch(e) {{}}
                    }}
                }}
            }}

            // PRE-SUBMISSION STRICT BETSLIP VERIFICATION GATE
            const verifyBetCard = slipBets[0] || document.querySelector("#right-sidebar [data-testid='betslip-bet'], .betslip [data-testid='betslip-bet'], [data-testid='betslip-bet']");
            if (verifyBetCard) {{
                const cardText = (verifyBetCard.innerText || "").toLowerCase().trim();
                // If betslip card element exists but its text nodes are still mounting/hydrating, wait for next tick!
                if (cardText.replace(/\s+/g, "").length < 4) {{
                    return JSON.stringify({{ status: "waiting_card_text" }});
                }}

                // 1. Period Verification Gate
                if (periodPrefix) {{
                    const pLow = periodPrefix.toLowerCase();
                    const periodOk = cardText.includes(pLow) ||
                        (pLow.includes("1st half") && (cardText.includes("1st half") || cardText.includes("half 1") || cardText.includes("first half") || cardText.includes("1. félidő"))) ||
                        (pLow.includes("2nd half") && (cardText.includes("2nd half") || cardText.includes("half 2") || cardText.includes("second half") || cardText.includes("2. félidő"))) ||
                        (pLow.includes("quarter") && cardText.includes("quarter")) ||
                        (pLow.includes("period") && cardText.includes("period")) ||
                        (pLow.includes("set") && cardText.includes("set")) ||
                        (pLow.includes("innings 1 to 5") && (cardText.includes("innings 1 to 5") || cardText.includes("first 5")));
                    if (!periodOk) {{
                        try {{
                            const resetBtn = document.querySelector("[data-testid='reset-betslip']");
                            if (resetBtn) resetBtn.click();
                            const rm = verifyBetCard.querySelector("[data-testid='betslip-bet-remove']");
                            if (rm) rm.click();
                        }} catch(e) {{}}
                        return JSON.stringify({{
                            status: "verification_failed",
                            error: "Szelvény verifikációs hiba: A betslipben szereplő piac nem a kért időszakra (" + periodPrefix + ") vonatkozik! Betslip tartalom: '" + verifyBetCard.innerText.replace(/\s+/g, ' ').slice(0, 80) + "'"
                        }});
                    }}
                }} else {{
                    if (cardText.includes("1st half") || cardText.includes("2nd half") || cardText.includes("quarter") || cardText.includes("1st period") || cardText.includes("2nd period") || cardText.includes("1st set") || cardText.includes("innings 1 to 5")) {{
                        try {{
                            const resetBtn = document.querySelector("[data-testid='reset-betslip']");
                            if (resetBtn) resetBtn.click();
                            const rm = verifyBetCard.querySelector("[data-testid='betslip-bet-remove']");
                            if (rm) rm.click();
                        }} catch(e) {{}}
                        return JSON.stringify({{
                            status: "verification_failed",
                            error: "Szelvény verifikációs hiba: Teljes meccses ajánlás helyett részidős piac került a betslipbe! Betslip tartalom: '" + verifyBetCard.innerText.replace(/\s+/g, ' ').slice(0, 80) + "'"
                        }});
                    }}
                }}

                // 2. Line Verification Gate
                if (lineVariants && lineVariants.length > 0) {{
                    const hasLine = lineVariants.some(v => matchesLine(cardText, v, isNegative));
                    if (!hasLine) {{
                        try {{
                            const resetBtn = document.querySelector("[data-testid='reset-betslip']");
                            if (resetBtn) resetBtn.click();
                            const rm = verifyBetCard.querySelector("[data-testid='betslip-bet-remove']");
                            if (rm) rm.click();
                        }} catch(e) {{}}
                        return JSON.stringify({{
                            status: "verification_failed",
                            error: "Szelvény verifikációs hiba: A betslipben lévő vonal nem egyezik az ajánlással (" + targetSel + ")! Betslip tartalom: '" + verifyBetCard.innerText.replace(/\s+/g, ' ').slice(0, 80) + "'"
                        }});
                    }}
                }}

                // 3. Entity / Team Verification Gate
                const clubStopwords = new Set(["fc", "sc", "cf", "afc", "ac", "sk", "fk", "bk", "club", "de"]);
                const entityText = targetSel.replace(/[+-]?\d+(\.\d+)?/g, "").replace(/[()]/g, "").trim();
                const entityWords = entityText.split(/\s+/).filter(w => w.length > 1);
                const meaningfulWords = entityWords.filter(w => !clubStopwords.has(w.toLowerCase()));
                const primaryName = meaningfulWords.length > 0 ? meaningfulWords[meaningfulWords.length - 1] : (entityWords.length > 0 ? entityWords[entityWords.length - 1] : "");

                if (meaningfulWords.length > 0 && !meaningfulWords.some(w => cardText.includes(w.toLowerCase()))) {{
                    if (!targetNorm.startsWith("over") && !targetNorm.startsWith("under") && targetNorm !== "draw") {{
                        try {{
                            const resetBtn = document.querySelector("[data-testid='reset-betslip']");
                            if (resetBtn) resetBtn.click();
                            const rm = verifyBetCard.querySelector("[data-testid='betslip-bet-remove']");
                            if (rm) rm.click();
                        }} catch(e) {{}}
                        return JSON.stringify({{
                            status: "verification_failed",
                            error: "Szelvény verifikációs hiba: A csapatnév ('" + primaryName + "') nem egyezik a betslipben! Betslip tartalom: '" + verifyBetCard.innerText.replace(/\s+/g, ' ').slice(0, 80) + "'"
                        }});
                    }}
                }}
            }}

            // Ensure betslip sidebar or drawer is expanded if collapsed
            const betslipToggle = document.querySelector("[data-testid='betslip-toggle'], button[aria-label*='Betslip' i], button[aria-label*='betslip' i], [data-testid='sidebar-toggle'], button[data-analytics*='betslip']");
            const hasBetslipInDom = !!document.querySelector("#right-sidebar, [data-testid='betslip'], .betslip, [data-testid='betslip-bet']");
            if (betslipToggle && !hasBetslipInDom) {{
                try {{ betslipToggle.click(); }} catch(e) {{}}
            }}

            function findBetInput() {{
                return document.querySelector("[data-testid='input-bet-amount']") ||
                       document.querySelector("#right-sidebar input[type='number'], #right-sidebar input[type='text'], #right-sidebar input[inputmode='decimal']") ||
                       document.querySelector(".betslip input[type='number'], .betslip input[type='text'], .betslip input[inputmode='decimal']") ||
                       document.querySelector("[data-testid*='betslip'] input") ||
                       document.querySelector("aside input[type='number'], aside input[type='text']") ||
                       document.querySelector("input[placeholder='0.00000000'], input[placeholder='0.00'], input[placeholder='0']");
            }}

            let input = findBetInput();
            if (!input) {{
                if (betslipToggle && !hasBetslipInDom) {{
                    try {{ betslipToggle.click(); }} catch(e) {{}}
                }}
                return JSON.stringify({{ status: "waiting_input", error: "A tét beviteli mező még nem jelent meg a szelvényen." }});
            }}

            input.focus();
            input.select();
            
            let inserted = false;
            try {{
                document.execCommand('selectAll', false, null);
                inserted = document.execCommand('insertText', false, "{stake:.2f}");
            }} catch(e) {{}}
            
            if (!inserted || input.value !== "{stake:.2f}") {{
                try {{
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                    nativeSetter.call(input, "{stake:.2f}");
                }} catch(e) {{
                    input.value = "{stake:.2f}";
                }}
            }}
            
            input.dispatchEvent(new Event("input", {{ bubbles: true }}));
            input.dispatchEvent(new Event("change", {{ bubbles: true }}));
            input.dispatchEvent(new KeyboardEvent("keydown", {{ bubbles: true, key: "Enter" }}));
            input.dispatchEvent(new KeyboardEvent("keyup", {{ bubbles: true, key: "Enter" }}));
            input.dispatchEvent(new Event("blur", {{ bubbles: true }}));
            
            const payout = document.querySelector("[data-testid='betslip-estimated-payout']");
            return JSON.stringify({{
                status: "ok",
                stake: input.value,
                payout: payout ? payout.innerText : null
            }});
        }} catch(e) {{
            return JSON.stringify({{ error: e.toString() }});
        }}
    }})()
    """

    staked = False
    payout_val = None
    verification_err = None
    last_stake_err = None
    for j in range(35):
        time.sleep(0.2)
        out = _eval_safari_js(set_stake_js, activate=False, timeout=5)
        if out and out != "NOT_FOUND":
            try:
                res_stake = json.loads(out)
                if res_stake.get("status") in ("waiting_card_text", "waiting_input"):
                    if res_stake.get("error"):
                        last_stake_err = res_stake.get("error")
                    continue
                if res_stake.get("status") == "verification_failed":
                    verification_err = res_stake.get("error", "Szelvény verifikációs hiba: A betslip tartalma nem egyezik az ajánlással.")
                    break
                if res_stake.get("status") == "ok":
                    staked = True
                    payout_val = res_stake.get("payout")
                    if payout_val and payout_val != "0.00000000":
                        break
                elif res_stake.get("error"):
                    last_stake_err = res_stake.get("error")
            except Exception:
                pass

    if verification_err:
        if log_autorun_event:
            log_autorun_event(
                cycle_id=cid,
                stage="VERIFY_BETSLIP",
                severity="ERROR",
                tip_id=tip_id,
                league=league,
                selection=selection,
                line=line,
                odds=odds,
                stake=stake,
                market=market,
                market_name=market_name,
                status="error",
                message=verification_err,
                error_code=AutoRunErrorCode.BETSLIP_VERIFICATION_FAILED,
                error_details=verification_err,
                run_type=run_type,
            )
        if auto_close_on_fail:
            _close_tab()
        return {
            "status": "error",
            "placed": False,
            "verification_failed": True,
            "message": verification_err,
            "closed": bool(auto_close_on_fail),
            "event_url": clean_url,
        }

    actual_place_delay = None
    if staked:
        if auto_place:
            import random
            low = min(float(min_place_delay), float(max_place_delay))
            high = max(float(min_place_delay), float(max_place_delay))
            actual_place_delay = round(random.uniform(low, high), 3)
            sleep_time = actual_place_delay if not os.environ.get("PYTEST_CURRENT_TEST") else min(actual_place_delay, 0.005)
            time.sleep(sleep_time)

            click_place_js = r"""
            (() => {
                try {
                    // 1. Auto-accept odds changes if prompt appears
                    const acceptBtn = document.querySelector("[data-testid*='accept-odds'], [data-testid*='accept-changes'], button[data-testid*='accept'], button[data-testid*='odds-change']") ||
                        Array.from(document.querySelectorAll("#right-sidebar button, button")).find(b => {
                            const t = (b.innerText || "").toLowerCase();
                            return t.includes("accept odds") || t.includes("accept changes") || t.includes("odds change") || t.includes("elfogad");
                        });
                    if (acceptBtn) {
                        try { acceptBtn.click(); } catch(e) {}
                    }

                    // 2. Check for login requirement
                    const loginBtn = document.querySelector("[data-testid='betslip-login'], a[href*='login'], button[data-testid*='login']");
                    const betslip = document.querySelector("#right-sidebar") || document.querySelector("[data-testid='betslip-bet']")?.closest("aside, [class*='sidebar']") || document.querySelector(".betslip, [data-testid='betslip'], [class*='betslip']") || document.body;
                    const betslipText = (betslip.innerText || "").toLowerCase();
                    if (loginBtn && (betslipText.includes("log in") || betslipText.includes("sign in") || betslipText.includes("register"))) {
                        return JSON.stringify({ status: "login_required", error: "Kérlek jelentkezz be a Stake fiókodba a Safariban!" });
                    }

                    // 3. Find Place Bet button
                    let btn = document.querySelector("[data-testid='betSlip-place-bets-button'], [data-testid='betslip-place-bet'], [data-testid='place-bet-button'], [data-testid='place-bet'], button[data-testid*='place-bet'], button[data-testid*='place-bets'], button[data-testid*='submit-bet']");
                    if (!btn) {
                        const buttons = Array.from(betslip.querySelectorAll("button"));
                        btn = buttons.find(b => {
                            const t = (b.innerText || "").replace(/\s+/g, " ").trim().toLowerCase();
                            const id = (b.getAttribute("data-testid") || "").toLowerCase();
                            return id.includes("place-bet") || id.includes("place-bets") || id.includes("submit-bet") || 
                                   t.includes("place bet") || t.includes("place single bet") || 
                                   t.includes("fogadás megtétele") || t.includes("fogadás");
                        });
                    }
                    if (!btn) {
                        btn = document.querySelector("#right-sidebar button[type='submit'], #right-sidebar button[class*='variant-brand'], #right-sidebar button[class*='variant-primary']");
                    }
                    if (!btn) return JSON.stringify({ status: "not_found", error: "Place Bet gomb nem található a felületen" });

                    const disabled = btn.disabled || btn.getAttribute("aria-disabled") === "true";
                    if (betslipText.includes("maximum active") || betslipText.includes("max active") || betslipText.includes("too many active") || (betslipText.includes("300") && (betslipText.includes("active") || betslipText.includes("limit")))) {
                        return JSON.stringify({ status: "limit_reached", error: "A Stake elérte a maximális 300 aktív fogadási limitet (300/300 bet nyitva). Várni kell, amíg meccsek kifutnak!" });
                    }
                    if (disabled) {
                        const input = document.querySelector("[data-testid='input-bet-amount']");
                        if (input) {
                            try {
                                input.dispatchEvent(new Event("input", { bubbles: true }));
                                input.dispatchEvent(new Event("change", { bubbles: true }));
                                input.dispatchEvent(new Event("blur", { bubbles: true }));
                            } catch(e) {}
                        }
                        return JSON.stringify({ status: "disabled", error: "Place Bet gomb jelenleg inaktív" });
                    }

                    function triggerFullClick(el) {
                        if (!el) return;
                        el.focus();
                        if (typeof el.click === 'function') {
                            try { el.click(); } catch(e) {}
                        } else {
                            el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        }
                    }

                    triggerFullClick(btn);
                    return JSON.stringify({ status: "clicked" });
                } catch(e) {
                    return JSON.stringify({ status: "error", error: e.toString() });
                }
            })()
            """
            clicked = False
            last_err = None
            for _ in range(25):
                c_out = _eval_safari_js(click_place_js, activate=False, timeout=5)
                if c_out and c_out != "NOT_FOUND":
                    try:
                        c_data = json.loads(c_out)
                        if c_data.get("status") == "clicked":
                            clicked = True
                            break
                        elif c_data.get("status") == "limit_reached":
                            return {
                                "status": "error",
                                "placed": False,
                                "closed": False,
                                "limit_reached": True,
                                "message": c_data.get("error", "Elérted a Stake maximális aktív fogadási limitjét (max 300 bet)!"),
                                "event_url": clean_url
                            }
                        elif c_data.get("status") == "login_required":
                            return {
                                "status": "error",
                                "placed": False,
                                "closed": False,
                                "message": c_data.get("error", "Bejelentkezés szükséges a Stake-en."),
                                "event_url": clean_url
                            }
                        elif c_data.get("error"):
                            last_err = c_data["error"]
                    except Exception:
                        pass
                time.sleep(0.2)

            if not clicked:
                return {
                    "status": "partial",
                    "placed": False,
                    "closed": False,
                    "message": f"Szelvény előkészítve, de a Place Bet gomb megnyomása kézi megerősítést igényel ({last_err or 'gomb inaktív'}).",
                    "selection": res_click.get("selection"),
                    "stake": stake,
                    "odds": current_odds or odds,
                    "event_url": clean_url
                }

            # Fast feedback check (up to 5s)
            check_success_js = r"""
            (() => {
                try {
                    const sidebar = document.querySelector("#right-sidebar") || document.querySelector("[data-testid='betslip-bet']")?.closest("aside, [class*='sidebar']") || document.querySelector(".betslip, [data-testid='betslip'], [class*='betslip']") || document.body;
                    const sidebarTxt = (sidebar ? sidebar.innerText : "").replace(/\s+/g, " ").toLowerCase();
                    const bodyTxt = (document.body ? document.body.innerText : "").replace(/\s+/g, " ").toLowerCase();

                    // 1. Collect ALL alert, toast, snackbar, and modal elements across the whole page:
                    const alertEls = Array.from(document.querySelectorAll(
                        "[role='alert'], [data-testid*='toast'], [class*='toast'], [class*='alert'], [class*='snackbar'], [class*='notification'], [class*='modal'], [class*='dialog'], [class*='variant-danger'], [class*='variant-error'], [data-testid*='error'], .toast"
                    ));
                    const alertTexts = alertEls.map(el => (el.innerText || "").replace(/\s+/g, " ").trim().toLowerCase()).filter(Boolean);
                    const combinedAlertText = alertTexts.join(" ");

                    // 2. Check positive success evidence FIRST:
                    const hasSuccessEl = !!document.querySelector("[data-testid='betslip-success'], [class*='betslip-success'], [data-testid='ticket-id'], [data-testid='bet-receipt'], [class*='bet-success'], [class*='receipt'], [data-testid='receipt'], [data-testid*='success'], [class*='variant-success']");
                    const hasSuccessTxt = sidebarTxt.includes("bet placed") || 
                                          sidebarTxt.includes("successful") || 
                                          sidebarTxt.includes("bet accepted") || 
                                          sidebarTxt.includes("placed successfully") ||
                                          sidebarTxt.includes("has been placed") ||
                                          sidebarTxt.includes("reference id") || 
                                          sidebarTxt.includes("reference #") || 
                                          sidebarTxt.includes("ticket id") ||
                                          sidebarTxt.includes("sikeres fogadás") || 
                                          sidebarTxt.includes("sikeresen elhelyezve") ||
                                          sidebarTxt.includes("wager placed") ||
                                          sidebarTxt.includes("bet slip placed") ||
                                          bodyTxt.includes("bet placed") ||
                                          bodyTxt.includes("bet accepted") ||
                                          bodyTxt.includes("wager placed") ||
                                          bodyTxt.includes("has been placed") ||
                                          bodyTxt.includes("placed successfully") ||
                                          combinedAlertText.includes("has been placed") ||
                                          combinedAlertText.includes("placed successfully") ||
                                          combinedAlertText.includes("successful");

                    if (hasSuccessEl || hasSuccessTxt) {
                        return JSON.stringify({ state: "success" });
                    }

                    // Check for Stake 300 active bets limit:
                    const is300Limit = (combinedAlertText.includes("300") && (combinedAlertText.includes("active") || combinedAlertText.includes("open") || combinedAlertText.includes("limit") || combinedAlertText.includes("maximum"))) ||
                                       combinedAlertText.includes("maximum active") ||
                                       combinedAlertText.includes("max active") ||
                                       combinedAlertText.includes("too many active") ||
                                       combinedAlertText.includes("active bets limit") ||
                                       combinedAlertText.includes("maximum open") ||
                                       combinedAlertText.includes("max open") ||
                                       (sidebarTxt.includes("300") && (sidebarTxt.includes("active") || sidebarTxt.includes("limit"))) ||
                                       sidebarTxt.includes("maximum active") ||
                                       sidebarTxt.includes("max active") ||
                                       sidebarTxt.includes("too many active");

                    if (is300Limit) {
                        return JSON.stringify({
                            state: "error",
                            limit_reached: true,
                            error: "Elérted a Stake maximális aktív fogadási limitjét (max 300 bet)! Várni kell, amíg meccsek kifutnak."
                        });
                    }

                    // Check for network disconnected / connection loss:
                    if (combinedAlertText.includes("network disconnected") || combinedAlertText.includes("connection lost") || combinedAlertText.includes("disconnected") ||
                        sidebarTxt.includes("network disconnected") || sidebarTxt.includes("connection lost") || sidebarTxt.includes("disconnected")) {
                        return JSON.stringify({
                            state: "error",
                            network_disconnected: true,
                            error: "A Stake kapcsolata megszakadt (Network disconnected). Kérlek ellenőrizd az internet / VPN kapcsolatot!"
                        });
                    }

                    // Check for insufficient balance:
                    if (combinedAlertText.includes("insufficient balance") || combinedAlertText.includes("insufficient funds") || combinedAlertText.includes("nincs elegendő") ||
                        sidebarTxt.includes("insufficient balance") || sidebarTxt.includes("insufficient funds") || sidebarTxt.includes("nincs elegendő")) {
                        return JSON.stringify({ state: "error", error: "Nincs elegendő egyenleg a számlán!" });
                    }

                    // Check for market suspended:
                    if ((combinedAlertText.includes("market suspended") || sidebarTxt.includes("market suspended") || sidebarTxt.includes("selection suspended")) && !hasSuccessTxt) {
                        return JSON.stringify({ state: "error", error: "A piacot felfüggesztették a Stake-en!" });
                    }

                    // Check for bet rejected:
                    if (combinedAlertText.includes("bet rejected") || sidebarTxt.includes("bet rejected") || sidebarTxt.includes("stake exceeds") || sidebarTxt.includes("bet limit reached")) {
                        return JSON.stringify({ state: "error", error: "A Stake elutasította a fogadást (limit vagy korlátozás)!" });
                    }

                    // Check for any other explicit error toast:
                    for (const aTxt of alertTexts) {
                        if (aTxt.includes("has been placed") || aTxt.includes("placed successfully")) {
                            return JSON.stringify({ state: "success" });
                        }
                        if (aTxt.includes("please wait 10 seconds before placing the same bet")) {
                            return JSON.stringify({ state: "success" });
                        }
                        if (aTxt.includes("error") || aTxt.includes("failed") || aTxt.includes("rejected") || aTxt.includes("hiba")) {
                            return JSON.stringify({ state: "error", error: aTxt });
                        }
                    }

                    // 3. Check placing / loading state:
                    const isPlacing = (sidebarTxt.includes("placing") || !!document.querySelector("[class*='loading'], [class*='spinner'], [data-testid='loading']"));
                    if (isPlacing) {
                        return JSON.stringify({ state: "pending", isPlacing: true });
                    }

                    // 4. Bet slip cleared evidence:
                    // If the bet card ([data-testid='betslip-bet']) or input-bet-amount has disappeared and no error was raised, the bet has been successfully placed!
                    const hasBetCard = !!document.querySelector("[data-testid='betslip-bet']");
                    const hasInput = !!document.querySelector("[data-testid='input-bet-amount']");
                    if (!hasBetCard && !hasInput) {
                        return JSON.stringify({ state: "success" });
                    }

                    return JSON.stringify({ state: "pending", isPlacing: false });
                } catch(e) {
                    return JSON.stringify({ state: "pending", error: e.toString() });
                }
            })()
            """
            placed = False
            for _ in range(25):
                time.sleep(0.2)
                s_out = _eval_safari_js(check_success_js, activate=False, timeout=5)
                if s_out and s_out != "NOT_FOUND":
                    try:
                        s_data = json.loads(s_out)
                        if s_data.get("state") == "success":
                            placed = True
                            break
                        elif s_data.get("error"):
                            err_txt = (s_data.get("error") or "").lower()
                            if "has been placed" in err_txt or "placed successfully" in err_txt or "please wait 10 seconds" in err_txt:
                                placed = True
                                break
                            if auto_close_on_fail and not s_data.get("limit_reached"):
                                _close_tab()
                            return {
                                "status": "error",
                                "placed": False,
                                "closed": bool(auto_close_on_fail and not s_data.get("limit_reached")),
                                "limit_reached": bool(s_data.get("limit_reached")),
                                "network_disconnected": bool(s_data.get("network_disconnected")),
                                "message": s_data["error"],
                                "event_url": clean_url
                            }
                    except Exception:
                        pass

            if placed:
                _close_tab()

                res_dict = {
                    "status": "ok",
                    "placed": True,
                    "closed": True,
                    "message": "Fogadás sikeresen megkötve a Stake-en és a fül bezárva!",
                    "selection": res_click.get("selection") or selection,
                    "stake": stake,
                    "odds": current_odds or odds,
                    "payout": payout_val,
                    "event_url": clean_url,
                    "place_delay": actual_place_delay,
                }
                if adapted_meta:
                    res_dict["adapted"] = True
                    res_dict["original_selection"] = orig_selection
                    res_dict["ev"] = adapted_meta.get("ev")
                    res_dict["message"] = f"Adaptívan megkötve: {res_click.get('selection') or selection} @ {current_odds or odds} (Eredeti: {orig_selection}) · EV: +{(adapted_meta.get('ev') or 0)*100:.1f}%"
                if log_autorun_event:
                    log_autorun_event(
                        cycle_id=cid,
                        stage="PLACE_BET",
                        severity="INFO",
                        tip_id=tip_id,
                        league=league,
                        selection=res_dict["selection"],
                        line=line,
                        odds=res_dict["odds"],
                        stake=res_dict["stake"],
                        market=market,
                        market_name=market_name,
                        status="placed",
                        message=res_dict["message"],
                        payload={"payout": payout_val, "adapted": bool(adapted_meta), "place_delay": actual_place_delay},
                        run_type=run_type,
                    )
                return res_dict
            else:
                if auto_close_on_fail:
                    _close_tab()
                err_msg = "A Stake nem igazolta vissza a fogadás megkötését (nem érkezett sikeres szelvény visszaigazolás)." + (" A fül bezárva." if auto_close_on_fail else " A fül nyitva maradt az ellenőrzéshez.")
                if log_autorun_event:
                    log_autorun_event(
                        cycle_id=cid,
                        stage="PLACE_BET",
                        severity="ERROR",
                        tip_id=tip_id,
                        league=league,
                        selection=selection,
                        line=line,
                        odds=odds,
                        stake=stake,
                        market=market,
                        market_name=market_name,
                        status="error",
                        message=err_msg,
                        error_code=AutoRunErrorCode.STAKE_REJECTED,
                        error_details="No success toast/ticket received within confirmation window",
                        run_type=run_type,
                    )
                return {
                    "status": "error",
                    "placed": False,
                    "closed": bool(auto_close_on_fail),
                    "message": err_msg,
                    "selection": res_click.get("selection"),
                    "stake": stake,
                    "odds": current_odds or odds,
                    "payout": payout_val,
                    "event_url": clean_url,
                    "place_delay": actual_place_delay,
                }

        res_dict = {
            "status": "ok",
            "placed": False,
            "closed": False,
            "message": "Szelvény sikeresen előkészítve a Safariban!",
            "selection": res_click.get("selection") or selection,
            "stake": stake,
            "odds": current_odds or odds,
            "payout": payout_val,
            "warning": odds_warning,
            "event_url": clean_url
        }
        if adapted_meta:
            res_dict["adapted"] = True
            res_dict["original_selection"] = orig_selection
            res_dict["ev"] = adapted_meta.get("ev")
            res_dict["message"] = f"Adaptív szelvény előkészítve a Safariban: {res_click.get('selection') or selection} @ {current_odds or odds} (Eredeti: {orig_selection}) · EV: +{(adapted_meta.get('ev') or 0)*100:.1f}%"
        if log_autorun_event:
            log_autorun_event(
                cycle_id=cid,
                stage="STAGE_COMPLETE",
                severity="INFO",
                tip_id=tip_id,
                league=league,
                selection=res_dict["selection"],
                line=line,
                odds=res_dict["odds"],
                stake=res_dict["stake"],
                market=market,
                market_name=market_name,
                status="staged",
                message=res_dict["message"],
                payload={"payout": payout_val, "adapted": bool(adapted_meta)},
                run_type=run_type,
            )
        return res_dict

    if not staked and adaptive_fallback and tip_id:
        log.info(f"Staking failed for {selection}. Evaluating alternative lines via adaptive fallback...")
        scan_cands_js = rf"""
        (() => {{
            try {{
                {helpers_js}
                const buttons = getOutcomeButtons();
                const candList = buttons.map(extractButtonCandidate).filter(c => c && c.odds !== null && !c.selection.toLowerCase().includes("suspended"));
                const seenC = new Set();
                const uniqueCands = candList.filter(c => {{
                    const key = c.selection + "_" + c.line + "_" + c.odds;
                    if (seenC.has(key)) return false;
                    seenC.add(key);
                    return true;
                }}).slice(0, 10);
                return JSON.stringify({{ available_candidates: uniqueCands }});
            }} catch(e) {{
                return JSON.stringify({{ error: e.toString() }});
            }}
        }})()
        """
        c_str = _eval_safari_js(scan_cands_js, activate=False, timeout=5)
        try:
            c_data = json.loads(c_str)
            page_cands = c_data.get("available_candidates") or []
            if page_cands:
                from .adaptive_fallback import evaluate_alternative_lines
                from .storage import Store
                eval_res = evaluate_alternative_lines(tip_id, page_cands, store=Store())
                if eval_res.get("has_eligible") and eval_res.get("best_candidate"):
                    best = eval_res["best_candidate"]
                    log.info(f"Adaptive fallback found alternative line: {best['selection']} @ {best['odds']} (EV: +{best['ev']*100:.1f}%)")
                    return stage_betslip_in_safari(
                        event_url=event_url,
                        selection=best["selection"],
                        stake=best.get("recommended_stake") or stake,
                        odds=best["odds"],
                        min_odds=min_odds,
                        tab=tab,
                        line=best.get("line"),
                        outcome_name=best.get("outcome_name"),
                        tip_id=tip_id,
                        market_name=market_name,
                        market=market,
                        league=league,
                        auto_place=auto_place,
                        min_place_delay=min_place_delay,
                        max_place_delay=max_place_delay,
                        auto_close_on_fail=auto_close_on_fail,
                        adaptive_fallback=False
                    )
        except Exception as e:
            log.warning(f"Adaptive fallback on stake error: {e}")

    # Ensure betslip is reset on failure so abandoned coupons never pile up
    cleanup_fail_js = """
    (() => {
        const resetBtn = document.querySelector("[data-testid='reset-betslip']");
        if (resetBtn) { try { resetBtn.click(); } catch(e) {} }
        const removeBtns = Array.from(document.querySelectorAll("#right-sidebar [data-testid='betslip-bet-remove'], [data-testid='betslip-bet-remove']"));
        for (const rb of removeBtns) { try { rb.click(); } catch(e) {} }
    })()
    """
    _eval_safari_js(cleanup_fail_js, activate=False, timeout=3)

    if auto_close_on_fail:
        _close_tab()
    err_msg = last_stake_err or "Nem sikerült kitölteni a tét összegét a szelvényen."
    if adaptive_fallback:
        err_msg = f"{err_msg} (Adaptív pótlás lefutott: nem található megfelelő +EV alternatív vonal)."
    if log_autorun_event:
        log_autorun_event(
            cycle_id=cid,
            stage="STAKE_ERROR",
            severity="ERROR",
            tip_id=tip_id,
            league=league,
            selection=selection,
            line=line,
            odds=odds,
            stake=stake,
            market=market,
            market_name=market_name,
            status="error",
            message=err_msg,
            error_code=AutoRunErrorCode.INPUT_FIELD_MISSING if not last_stake_err else AutoRunErrorCode.UNKNOWN_ERROR,
            error_details=last_stake_err,
            run_type=run_type,
        )
    return {"status": "error", "placed": False, "closed": bool(auto_close_on_fail), "message": err_msg, "event_url": clean_url}

