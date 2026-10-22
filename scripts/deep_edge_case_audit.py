import sqlite3, json
from datetime import datetime, timezone

conn = sqlite3.connect("data/engine.sqlite3")
c = conn.cursor()

c.execute("""
    SELECT tip_id, issued_at, payload, status, pnl
    FROM system_decisions 
    WHERE date(issued_at, "+2 hours") = "2026-10-15"
""")
rows = c.fetchall()

print(f"Total rows today: {len(rows)}")

# 1. Check start times vs issued_at
past_starts = []
future_starts = []

for tid, issued_at, p_json, status, pnl in rows:
    p = json.loads(p_json)
    start = p.get("start")
    if start:
        if start < issued_at:
            past_starts.append((tid, p.get("game_id"), start, issued_at))
        else:
            future_starts.append((tid, start))

print(f"Bets issued AFTER match start: {len(past_starts)}")
if past_starts:
    for ps in past_starts[:5]:
        print("  PAST START:", ps)

# 2. Check 3-way multi-picks
three_way_picks = {}
for tid, issued_at, p_json, status, pnl in rows:
    p = json.loads(p_json)
    mkt = p.get("market", "")
    gid = p.get("game_id", "")
    if "three_way" in mkt or mkt in ("moneyline", "three_way"):
        key = (gid, mkt)
        if key not in three_way_picks:
            three_way_picks[key] = []
        three_way_picks[key].append({
            "tid": tid,
            "selection": p.get("selection"),
            "side": p.get("side"),
            "odds": p.get("reference_odds") or (p.get("stake_evidence") or {}).get("decimal_odds"),
            "prob": p.get("probability"),
            "stake": p.get("stake")
        })

multi_three_way = {k: v for k, v in three_way_picks.items() if len(v) > 1}
print(f"\nGames with >1 pick on same moneyline/three-way market: {len(multi_three_way)}")
for k, v in multi_three_way.items():
    print(f"  Game {k[0]} market {k[1]}:")
    for item in v:
        s = item['selection']
        od = item['odds']
        pr = item['prob']
        st = item['stake']
        print(f"    - {s} @ {od} (p={pr:.2f}, stake=${st})")

# 3. Check Over AND Under on the same game (any scope)
totals_by_game = {}
for tid, issued_at, p_json, status, pnl in rows:
    p = json.loads(p_json)
    mkt = p.get("market", "")
    gid = p.get("game_id", "")
    side = p.get("side", "")
    if "total" in mkt or side in ("over", "under"):
        if gid not in totals_by_game:
            totals_by_game[gid] = []
        totals_by_game[gid].append({
            "tid": tid,
            "market": mkt,
            "selection": p.get("selection"),
            "side": side,
            "line": p.get("line"),
            "odds": p.get("reference_odds") or (p.get("stake_evidence") or {}).get("decimal_odds"),
            "prob": p.get("probability"),
            "stake": p.get("stake")
        })

over_under_mix = {}
for gid, t_list in totals_by_game.items():
    sides = set(t["side"] for t in t_list)
    if "over" in sides and "under" in sides:
        over_under_mix[gid] = t_list

print(f"\nGames with BOTH Over and Under picks: {len(over_under_mix)}")
for gid, t_list in over_under_mix.items():
    print(f"  Game {gid}:")
    for item in t_list:
        m = item['market']
        s = item['selection']
        od = item['odds']
        st = item['stake']
        print(f"    - {m} | {s} @ {od} (stake=${st})")

# 4. Check status & settled PnL
statuses = {}
pnl_sum = 0.0
for tid, issued_at, p_json, status, pnl in rows:
    statuses[status] = statuses.get(status, 0) + 1
    if pnl is not None:
        pnl_sum += float(pnl)

print(f"\nBet Statuses: {statuses}")
print(f"Settled PnL so far from today decisions: ${pnl_sum:.2f}")

# 5. Check duplicate tip_ids or exact duplicate contracts
seen_contracts = {}
duplicate_contracts = []
for tid, issued_at, p_json, status, pnl in rows:
    p = json.loads(p_json)
    gid = p.get("game_id")
    mkt = p.get("market")
    sel = p.get("selection")
    key = (gid, mkt, sel)
    if key in seen_contracts:
        duplicate_contracts.append((key, seen_contracts[key], tid))
    else:
        seen_contracts[key] = tid

print(f"\nDuplicate exact contracts issued: {len(duplicate_contracts)}")
for dc in duplicate_contracts:
    print("  DUPLICATE:", dc)
