import sqlite3
import json
from pathlib import Path
from betting_engine.shadow_market_incubator import load_graduated_markets

conn = sqlite3.connect('data/shadow_incubator.sqlite3')
cur = conn.cursor()

# 1. Profitable slices in incubator
rows = cur.execute('''
    SELECT league, scope, family, sub_family, count(*), sum(pnl), sum(pnl)/count(*)
    FROM incubated_wagers
    WHERE status='settled'
    GROUP BY league, scope, family, sub_family
    HAVING count(*) >= 15 AND sum(pnl) > 0
    ORDER BY sum(pnl) DESC
''').fetchall()
print(f"Total profitable submarket slices (N>=15, PnL>0): {len(rows)}")
total_pnl = sum((r[5] or 0.0) for r in rows)
print(f"Total cumulative profit across all profitable incubator slices: USD {total_pnl:.2f}")

# 2. Graduated markets in JSON (active file read by live engine)
json_grads = load_graduated_markets(Path('data'))
print(f"Total active graduated markets in graduated_markets.json: {len(json_grads)}")

# 3. Check live connection in engine.sqlite3
conn_eng = sqlite3.connect('data/engine.sqlite3')
cur_eng = conn_eng.cursor()
live_decisions_count = cur_eng.execute('SELECT count(*) FROM system_decisions').fetchone()[0]
today_decisions = cur_eng.execute("SELECT count(*) FROM system_decisions WHERE date(issued_at) = date('now')").fetchone()[0]
print(f"Total decisions in engine ledger: {live_decisions_count}")
print(f"Today decisions issued: {today_decisions}")

# 4. Check how many recommendations currently in the dashboard payload
try:
    with open('data/dashboard_payload.json') as f:
        dash = json.load(f)
        recs = dash.get('recommendations', [])
        print(f"Current live dashboard recommendations: {len(recs)}")
        # Check leagues and markets in recs
        leagues = {}
        for r in recs:
            l = r.get('league')
            leagues[l] = leagues.get(l, 0) + 1
        print("Live recommendation distribution by league:", leagues)
except Exception as e:
    print(f"Dashboard payload check error: {e}")
