import json, sqlite3, re

with open('data/stake_active_bets_snapshot.json') as f:
    stake_bets = json.load(f)

conn = sqlite3.connect('data/engine.sqlite3')
cur = conn.cursor()

# Autorun placements
cur.execute('SELECT tip_id, placed_at, selection, stake, odds, adapted, league, source FROM autorun_placements')
ar_list = [dict(zip(['tip_id', 'placed_at', 'selection', 'stake', 'odds', 'adapted', 'league', 'source'], r)) for r in cur.fetchall()]

# Open bets from ledger
cur.execute('SELECT id, tip_id, stake, odds, platform, status, payload, created_at FROM bets WHERE status="open"')
open_bets = []
for r in cur.fetchall():
    try:
        p = json.loads(r[6]) if r[6] else {}
    except Exception:
        p = {}
    open_bets.append({
        'id': r[0], 'tip_id': r[1], 'stake': r[2], 'odds': r[3], 'platform': r[4], 'status': r[5], 'payload': p, 'created_at': r[7]
    })

# Load all tips in memory for deep lookups
cur.execute('SELECT id, payload FROM tips')
tips_map = {}
for tid, p_json in cur.fetchall():
    try:
        tips_map[tid] = json.loads(p_json)
    except Exception:
        pass

def norm(s):
    if not s: return ''
    return re.sub(r'[^a-z0-9]', '', str(s).lower())

def teams_from_fixture(fix):
    for sep in [' - ', ' @ ', ' vs ']:
        if sep in fix:
            parts = fix.split(sep)
            if len(parts) == 2:
                return norm(parts[0]), norm(parts[1])
    return norm(fix), ''

matched = []
unmatched = []

used_ar = set()
used_bets = set()

for sb in stake_bets:
    s_id = sb['id']
    s_amt = float(sb['amount'])
    s_created = sb['createdAt']
    oc = sb['outcomes'][0] if sb.get('outcomes') else {}
    s_odds = float(oc.get('odds', 0.0))
    s_mkt = oc.get('market', {}).get('name', '')
    s_sel = oc.get('outcome', {}).get('name', '')
    s_fix = oc.get('fixture', {}).get('name', '')
    s_sport = oc.get('sport', {}).get('name', '')
    
    t1, t2 = teams_from_fixture(s_fix)
    
    # 1. Match against autorun placements
    best_ar = None
    for i, ar in enumerate(ar_list):
        if i in used_ar:
            continue
        s_norm = norm(s_sel)
        ar_norm = norm(ar['selection'])
        odds_diff = abs(s_odds - float(ar['odds']))
        stake_diff = abs(s_amt - float(ar['stake']))
        
        # Check team match in tip
        tip_p = tips_map.get(ar['tip_id'], {})
        h_n = norm(tip_p.get('home', ''))
        a_n = norm(tip_p.get('away', ''))
        t_match = (t1 in h_n or t1 in a_n or h_n in t1 or a_n in t1) or (t2 in h_n or t2 in a_n or h_n in t2 or a_n in t2)
        
        if (s_norm == ar_norm or s_norm in ar_norm or ar_norm in s_norm) and (stake_diff < 0.05 or odds_diff < 0.05):
            best_ar = (i, ar, 'exact_autorun', tip_p)
            break
        if t_match and (stake_diff < 0.05 or odds_diff < 0.05):
            best_ar = (i, ar, 'team_autorun', tip_p)
            break

    # 2. Match against open bets ledger
    best_bet = None
    for j, b in enumerate(open_bets):
        if j in used_bets:
            continue
        bp = b['payload']
        h_n = norm(bp.get('home', ''))
        a_n = norm(bp.get('away', ''))
        b_sel = norm(bp.get('selection', ''))
        s_norm = norm(s_sel)
        
        t_match = (t1 in h_n or t1 in a_n or h_n in t1 or a_n in t1) or (t2 in h_n or t2 in a_n or h_n in t2 or a_n in t2)
        if t_match:
            odds_diff = abs(s_odds - float(b['odds']))
            stake_diff = abs(s_amt - float(b['stake']))
            if s_norm == b_sel or s_norm in b_sel or b_sel in s_norm:
                best_bet = (j, b, 'exact_bet')
                break
            if stake_diff < 0.05 or odds_diff < 0.05:
                best_bet = (j, b, 'odds_stake_bet')
                break

    if best_ar:
        used_ar.add(best_ar[0])
        matched.append({
            'stake_id': s_id,
            'fixture': s_fix,
            'stake_sel': s_sel,
            'stake_mkt': s_mkt,
            'stake_amt': s_amt,
            'stake_odds': s_odds,
            'match_source': 'autorun',
            'matched_data': best_ar[1],
            'match_type': best_ar[2],
            'tip': best_ar[3]
        })
    elif best_bet:
        used_bets.add(best_bet[0])
        matched.append({
            'stake_id': s_id,
            'fixture': s_fix,
            'stake_sel': s_sel,
            'stake_mkt': s_mkt,
            'stake_amt': s_amt,
            'stake_odds': s_odds,
            'match_source': 'ledger_bet',
            'matched_data': best_bet[1],
            'match_type': best_bet[2],
            'tip': best_bet[1]['payload']
        })
    else:
        unmatched.append({
            'stake_id': s_id,
            'fixture': s_fix,
            'stake_sel': s_sel,
            'stake_mkt': s_mkt,
            'stake_amt': s_amt,
            'stake_odds': s_odds,
            'created_at': s_created
        })

print(f'Total Stake active bets: {len(stake_bets)}')
print(f'Successfully matched: {len(matched)} ({len(matched)/len(stake_bets)*100:.1f}%)')
print(f'Unmatched: {len(unmatched)}')

if unmatched:
    print('\nUnmatched bets list:')
    for u in unmatched:
        print(f"  {u['fixture']} | {u['stake_mkt']} | {u['stake_sel']} | Stake: ${u['stake_amt']} | Odds: {u['stake_odds']} | {u['created_at']}")

# Detailed match validation
print('\n=== Validation Checks on Matched Bets ===')
correct_selections = 0
correct_stakes = 0
correct_odds = 0
selection_mismatches = []
stake_mismatches = []
odds_mismatches = []

for m in matched:
    tip = m.get('tip', {})
    rec_sel = tip.get('selection')
    rec_stake = tip.get('recommended_stake') or tip.get('stake')
    rec_odds = tip.get('reference_odds')
    min_odds = tip.get('minimum_odds')
    
    # Selection check
    if rec_sel:
        # Normalize comparison
        if norm(m['stake_sel']) == norm(rec_sel) or norm(m['stake_sel']) in norm(rec_sel) or norm(rec_sel) in norm(m['stake_sel']):
            correct_selections += 1
        else:
            selection_mismatches.append((m['fixture'], m['stake_sel'], rec_sel, m.get('match_type')))
    else:
        correct_selections += 1
        
    # Stake check
    if rec_stake is not None:
        if abs(float(rec_stake) - float(m['stake_amt'])) < 0.05:
            correct_stakes += 1
        else:
            stake_mismatches.append((m['fixture'], m['stake_amt'], rec_stake))
    else:
        correct_stakes += 1

print(f'Correct selection matches: {correct_selections}/{len(matched)}')
print(f'Correct stake matches: {correct_stakes}/{len(matched)}')
if selection_mismatches:
    print(f'Selection discrepancies ({len(selection_mismatches)}):')
    for sm in selection_mismatches[:10]:
        print(f'  {sm[0]}: Stake={sm[1]} vs Issued={sm[2]} ({sm[3]})')
if stake_mismatches:
    print(f'Stake discrepancies ({len(stake_mismatches)}):')
    for sm in stake_mismatches[:10]:
        print(f'  {sm[0]}: Stake=${sm[1]} vs Issued=${sm[2]}')
