import sqlite3, json

conn = sqlite3.connect("data/engine.sqlite3")
c = conn.cursor()

c.execute("""
    SELECT tip_id, issued_at, payload
    FROM system_decisions 
    WHERE date(issued_at, "+2 hours") = "2026-10-15"
""")
rows = c.fetchall()

picks = []
for tid, issued_at, p_json in rows:
    p = json.loads(p_json)
    ev = float(p.get("expected_roi", 0))
    picks.append((ev, tid, issued_at, p))

picks.sort(key=lambda x: x[0], reverse=True)

print("=== TOP 10 HIGHEST EV PICKS TODAY ===")
for ev, tid, issued_at, p in picks[:10]:
    gid = p.get("game_id")
    lg = p.get("league")
    mkt = p.get("market")
    sel = p.get("selection")
    od = p.get("reference_odds") or (p.get("stake_evidence") or {}).get("decimal_odds")
    prob = p.get("probability")
    st = p.get("stake")
    model = p.get("model_version")
    reasons = p.get("reasons", [])
    print(f"\nTip {tid} | {lg} | {sel} | EV=+{ev*100:.1f}% | Odds={od} | Prob={prob:.2f} | Stake=${st}")
    print(f"  Game: {gid} | Market: {mkt} | Model: {model}")
    print(f"  Reasons: {reasons[:2]}")
