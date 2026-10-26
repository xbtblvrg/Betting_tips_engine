import json, sqlite3, re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

with open('data/stake_active_bets_snapshot.json') as f:
    stake_bets = json.load(f)

conn = sqlite3.connect('data/engine.sqlite3')
cur = conn.cursor()

# 1. Load autorun placements
cur.execute('SELECT tip_id, placed_at, selection, stake, odds, adapted, league, source FROM autorun_placements')
ar_rows = cur.fetchall()
ar_list = []
for r in ar_rows:
    dt = datetime.fromisoformat(r[1])
    ar_list.append({
        'tip_id': r[0],
        'placed_at': dt,
        'selection': r[2],
        'stake': float(r[3]),
        'odds': float(r[4]),
        'adapted': r[5],
        'league': r[6],
        'source': r[7]
    })

# 2. Load recommendation issuance
cur.execute('SELECT family, tip_id, issued_at, league, start, stake, odds, source FROM recommendation_issuance')
iss_by_tip = {r[1]: dict(zip(['family', 'tip_id', 'issued_at', 'league', 'start', 'stake', 'odds', 'source'], r)) for r in cur.fetchall()}

# 3. Load tips
cur.execute('SELECT id, payload FROM tips')
tips_by_id = {}
for tid, p_json in cur.fetchall():
    try:
        tips_by_id[tid] = json.loads(p_json)
    except Exception:
        pass

# 4. Load bets
cur.execute('SELECT id, tip_id, stake, odds, platform, status, payload, created_at FROM bets')
bets_by_tip = {}
for r in cur.fetchall():
    try:
        p = json.loads(r[6]) if r[6] else {}
    except Exception:
        p = {}
    bets_by_tip[r[1]] = {
        'id': r[0], 'tip_id': r[1], 'stake': r[2], 'odds': r[3], 'platform': r[4], 'status': r[5], 'payload': p, 'created_at': r[7]
    }

def parse_stake_time(t_str):
    # e.g. "Mon, 26 Oct 2026 15:26:22 GMT"
    try:
        return parsedate_to_datetime(t_str)
    except Exception:
        return None

def norm(s):
    if not s: return ''
    return re.sub(r'[^a-z0-9]', '', str(s).lower())

results = []
used_ar = set()

for sb in stake_bets:
    s_id = sb['id']
    s_amt = float(sb['amount'])
    s_time = parse_stake_time(sb['createdAt'])
    oc = sb['outcomes'][0] if sb.get('outcomes') else {}
    s_odds = float(oc.get('odds', 0.0))
    s_mkt = oc.get('market', {}).get('name', '')
    s_sel = oc.get('outcome', {}).get('name', '')
    s_fix = oc.get('fixture', {}).get('name', '')
    s_sport = oc.get('sport', {}).get('name', '')
    
    # Match against autorun placements
    best_match = None
    best_diff = 999999
    
    for i, ar in enumerate(ar_list):
        if i in used_ar:
            continue
        # Compare time
        if s_time and ar['placed_at']:
            time_diff = abs((s_time - ar['placed_at']).total_seconds())
        else:
            time_diff = 999999
            
        s_norm = norm(s_sel)
        ar_norm = norm(ar['selection'])
        sel_match = (s_norm == ar_norm or s_norm in ar_norm or ar_norm in s_norm)
        stake_match = abs(s_amt - ar['stake']) < 0.05
        odds_match = abs(s_odds - ar['odds']) < 0.05
        
        # High confidence match: close in time (< 120s) and stake/odds/sel match
        if time_diff < 120 and (sel_match or (stake_match and odds_match)):
            if time_diff < best_diff:
                best_diff = time_diff
                best_match = (i, ar, 'autorun_timestamp_exact')
        elif sel_match and stake_match and odds_match:
            if time_diff < best_diff:
                best_diff = time_diff
                best_match = (i, ar, 'autorun_values_exact')
                
    if best_match:
        used_ar.add(best_match[0])
        ar = best_match[1]
        tid = ar['tip_id']
        tip = tips_by_id.get(tid, {})
        iss = iss_by_tip.get(tid, {})
        b_rec = bets_by_tip.get(tid, {})
        
        results.append({
            'status': 'MATCHED_AUTORUN',
            'stake_bet_id': s_id,
            'stake_time': str(s_time),
            'stake_fixture': s_fix,
            'stake_market': s_mkt,
            'stake_selection': s_sel,
            'stake_amount': s_amt,
            'stake_odds': s_odds,
            'tip_id': tid,
            'issued_selection': iss.get('selection') or tip.get('selection') or ar['selection'],
            'issued_stake': iss.get('stake') or ar['stake'],
            'issued_odds': iss.get('odds') or ar['odds'],
            'issued_league': iss.get('league') or ar['league'],
            'model_ev': tip.get('expected_roi'),
            'time_delta_sec': round(best_diff, 1)
        })
    else:
        # Match against bets table or recommendation_issuance by fixture/teams
        results.append({
            'status': 'MANUAL_OR_EARLIER',
            'stake_bet_id': s_id,
            'stake_time': str(s_time),
            'stake_fixture': s_fix,
            'stake_market': s_mkt,
            'stake_selection': s_sel,
            'stake_amount': s_amt,
            'stake_odds': s_odds,
            'tip_id': None
        })

print(f'=== TOTAL ACTIVE BETS ON STAKE: {len(stake_bets)} ===')
matched_ar = [r for r in results if r['status'] == 'MATCHED_AUTORUN']
other_bets = [r for r in results if r['status'] != 'MATCHED_AUTORUN']

print(f'Matched to Auto-Run Placements: {len(matched_ar)} ({len(matched_ar)/len(stake_bets)*100:.1f}%)')
print(f'Earlier / Manual Bets: {len(other_bets)}')

# Check accuracy on matched autorun bets
perfect_selection = 0
perfect_stake = 0
perfect_odds = 0

for m in matched_ar:
    if norm(m['stake_selection']) == norm(m['issued_selection']) or norm(m['stake_selection']) in norm(m['issued_selection']) or norm(m['issued_selection']) in norm(m['stake_selection']):
        perfect_selection += 1
    if abs(m['stake_amount'] - float(m['issued_stake'])) < 0.01:
        perfect_stake += 1
    if abs(m['stake_odds'] - float(m['issued_odds'])) < 0.05:
        perfect_odds += 1

print(f'\n=== ACCURACY AUDIT ON AUTORUN BETS ({len(matched_ar)} bets) ===')
print(f'1. Selection match: {perfect_selection}/{len(matched_ar)} ({perfect_selection/len(matched_ar)*100:.1f}%)')
print(f'2. Stake amount match: {perfect_stake}/{len(matched_ar)} ({perfect_stake/len(matched_ar)*100:.1f}%)')
print(f'3. Odds match (within 0.05 of quote): {perfect_odds}/{len(matched_ar)} ({perfect_odds/len(matched_ar)*100:.1f}%)')

if other_bets:
    print(f'\n=== EARLIER / MANUAL BETS ON STAKE ({len(other_bets)}) ===')
    for ob in other_bets:
        print(f"  [{ob['stake_time']}] {ob['stake_fixture']} | {ob['stake_market']} | {ob['stake_selection']} | ${ob['stake_amount']} @ {ob['stake_odds']}")

with open('data/reconciliation_report.json', 'w') as f:
    json.dump(results, f, indent=2)
print('\nComplete reconciliation report written to data/reconciliation_report.json')
