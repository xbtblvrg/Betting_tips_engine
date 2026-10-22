import sqlite3, json

conn = sqlite3.connect("data/engine.sqlite3")
c = conn.cursor()

c.execute("""
    SELECT tip_id, issued_at, payload
    FROM system_decisions 
    WHERE date(issued_at, "+2 hours") = "2026-10-15"
""")
rows = c.fetchall()
total = len(rows)

print(f"=== SUBSTANTIATION AUDIT FOR ALL {total} TODAY DECISIONS ===")

missing_model = 0
missing_prob = 0
missing_odds = 0
missing_ev = 0
missing_stake = 0
missing_reasons = 0
missing_url = 0
missing_evidence = 0

model_distribution = {}

for tid, issued_at, p_json in rows:
    p = json.loads(p_json)
    
    # 1. Model version
    model = p.get("model_version")
    if not model or model == "unknown":
        missing_model += 1
    else:
        model_distribution[model] = model_distribution.get(model, 0) + 1
        
    # 2. Probability
    prob = p.get("probability")
    if prob is None or prob <= 0 or prob >= 1:
        missing_prob += 1
        
    # 3. Odds
    odds = p.get("reference_odds") or (p.get("stake_evidence") or {}).get("decimal_odds")
    if odds is None or odds <= 1.0:
        missing_odds += 1
        
    # 4. EV
    ev = p.get("expected_roi")
    if ev is None or ev <= 0:
        missing_ev += 1
        
    # 5. Stake
    st = p.get("stake")
    if st is None or st < 1.0 or st > 5.0:
        missing_stake += 1
        
    # 6. Reasons
    reasons = p.get("reasons")
    if not reasons or len(reasons) == 0:
        missing_reasons += 1
        
    # 7. Reference URL
    url = p.get("reference_url")
    if not url or not url.startswith("https://stake.com"):
        missing_url += 1
        
    # 8. Stake evidence object
    ev_obj = p.get("stake_evidence")
    if not ev_obj or not ev_obj.get("decimal_odds"):
        missing_evidence += 1

print("\nAUDIT CHECKLIST (0 failures expected):")
print(f"  Missing or invalid model_version:   {missing_model} / {total}")
print(f"  Missing or invalid probability:     {missing_prob} / {total}")
print(f"  Missing or invalid decimal odds:    {missing_odds} / {total}")
print(f"  Missing or non-positive EV:         {missing_ev} / {total}")
print(f"  Missing or out-of-bounds stake:     {missing_stake} / {total}")
print(f"  Missing rationale / reasons array:  {missing_reasons} / {total}")
print(f"  Missing or invalid Stake URL:       {missing_url} / {total}")
print(f"  Missing stake evidence snapshot:    {missing_evidence} / {total}")

print(f"\nACTIVE QUANT MODELS RESPONSIBLE ({len(model_distribution)} distinct models):")
for m, cnt in sorted(model_distribution.items(), key=lambda x: x[1], reverse=True)[:15]:
    print(f"  {cnt:3} picks: {m}")

# Sample random 3 reasons to demonstrate concrete model projections
print("\nSAMPLE RATIONALE FROM ACTUAL ISSUED PICKS:")
for tid, issued_at, p_json in rows[::len(rows)//3][:3]:
    p = json.loads(p_json)
    print(f"\nPick {tid} ({p.get('league')} - {p.get('selection')} @ {p.get('reference_odds')}):")
    print(f"  Model: {p.get('model_version')}")
    print(f"  Prob:  {float(p.get('probability',0))*100:.1f}% | EV: +{float(p.get('expected_roi',0))*100:.1f}% | Stake: ${p.get('stake')}")
    for r in p.get("reasons", []):
        print(f"  - Rationale: {r}")
