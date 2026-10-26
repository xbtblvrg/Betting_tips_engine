import json, sqlite3, re
from email.utils import parsedate_to_datetime
from datetime import datetime

with open('data/stake_active_bets_snapshot.json') as f:
    stake_bets = json.load(f)

conn = sqlite3.connect('data/engine.sqlite3')
cur = conn.cursor()

# Load autorun placements
cur.execute('SELECT tip_id, placed_at, selection, stake, odds, adapted, league, source FROM autorun_placements')
ar_rows = cur.fetchall()

# Load tips
cur.execute('SELECT id, payload FROM tips')
tips_by_id = {}
for tid, p_str in cur.fetchall():
    try:
        p = json.loads(p_str)
        tips_by_id[tid] = p
    except Exception:
        pass

def norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s).lower())

def norm_handicap(s):
    s = s.strip()
    s = re.sub(r'\(\+?([0-9.]+)\)', r'+\1', s)
    s = re.sub(r'\((-([0-9.]+))\)', r'-\2', s)
    return norm(s)

def fixture_words(f_str):
    if not f_str: return set()
    words = re.findall(r'[a-zA-Z]{3,}', f_str.lower())
    stopwords = {'the', 'and', 'vs', 'club', 'city', 'united'}
    return set(w for w in words if w not in stopwords)

# Build a lookup list of autorun items with their tip payload
ar_items = []
for r in ar_rows:
    tid = r[0]
    p = tips_by_id.get(tid, {})
    home = p.get('home', '')
    away = p.get('away', '')
    fixture = f"{home} - {away}" if home and away else p.get('fixture', '')
    f_words = fixture_words(fixture)
    
    ar_items.append({
        'tip_id': tid,
        'placed_at': datetime.fromisoformat(r[1]),
        'ar_sel': r[2],
        'ar_stake': float(r[3]),
        'ar_odds': float(r[4]),
        'adapted': r[5],
        'league': r[6],
        'home': home,
        'away': away,
        'fixture': fixture,
        'f_words': f_words,
        'orig_sel': p.get('selection', ''),
        'orig_market': p.get('market', ''),
        'orig_odds': p.get('odds') or p.get('reference_odds'),
        'orig_stake': p.get('stake', 0.0),
    })

results = []
used_ar = set()

for sb in stake_bets:
    s_id = sb['id']
    s_amt = float(sb['amount'])
    s_time = parsedate_to_datetime(sb['createdAt'])
    oc = sb['outcomes'][0] if sb.get('outcomes') else {}
    s_odds = float(oc.get('odds', 0.0))
    s_mkt = oc.get('market', {}).get('name', '')
    s_sel = oc.get('outcome', {}).get('name', '')
    s_fix = oc.get('fixture', {}).get('name', '')
    s_fwords = fixture_words(s_fix)
    
    best_match = None
    best_score = -999999
    best_idx = None
    
    for idx, ar in enumerate(ar_items):
        if idx in used_ar:
            continue
            
        time_diff = abs((s_time - ar['placed_at']).total_seconds())
        if time_diff > 300: # must be within 5 minutes
            continue
            
        # Match score calculation
        score = 0
        
        # Fixture overlap
        overlap = s_fwords.intersection(ar['f_words'])
        if len(overlap) >= 2:
            score += 1000
        elif len(overlap) == 1:
            score += 400
        else:
            continue # MUST match at least one team word!
            
        # Time proximity
        score += max(0, 300 - time_diff)
        
        # Selection match
        if norm_handicap(s_sel) == norm_handicap(ar['ar_sel']) or norm_handicap(s_sel) == norm_handicap(ar['orig_sel']):
            score += 500
        elif norm(s_sel) in norm(ar['ar_sel']) or norm(ar['ar_sel']) in norm(s_sel):
            score += 300
            
        # Stake match
        if abs(s_amt - ar['ar_stake']) < 0.01:
            score += 200
            
        # Odds match
        if abs(s_odds - ar['ar_odds']) < 0.05:
            score += 100
            
        if score > best_score:
            best_score = score
            best_match = ar
            best_idx = idx
            
    if best_match:
        used_ar.add(best_idx)
        results.append({
            'status': 'MATCHED_AUTORUN',
            'stake_bet_id': s_id,
            'stake_fixture': s_fix,
            'stake_sel': s_sel,
            'stake_amt': s_amt,
            'stake_odds': s_odds,
            'stake_mkt': s_mkt,
            'ar': best_match,
            'score': best_score
        })
    else:
        results.append({
            'status': 'NON_AUTORUN',
            'stake_bet_id': s_id,
            'stake_time': str(s_time),
            'stake_fixture': s_fix,
            'stake_sel': s_sel,
            'stake_amt': s_amt,
            'stake_odds': s_odds,
            'stake_mkt': s_mkt
        })

autorun_matches = [r for r in results if r['status'] == 'MATCHED_AUTORUN']
non_autorun = [r for r in results if r['status'] == 'NON_AUTORUN']

print(f"Total active bets on Stake: {len(stake_bets)}")
print(f"Matched to Auto-Run: {len(autorun_matches)}")
print(f"Non-AutoRun (Manual/Earlier): {len(non_autorun)}")

# Now evaluate fidelity of autorun_matches:
exact_sel = 0
exact_stake = 0
opp_mismatch = 0
for m in autorun_matches:
    ar = m['ar']
    s_sel = m['stake_sel']
    orig_sel = ar['orig_sel']
    ar_sel = ar['ar_sel']
    
    if norm_handicap(s_sel) == norm_handicap(orig_sel) or norm_handicap(s_sel) == norm_handicap(ar_sel):
        exact_sel += 1
    else:
        print(f"SELECTION MISMATCH: {m['stake_fixture']} Stake: '{s_sel}' vs Orig: '{orig_sel}' (ar: '{ar_sel}')")
        
    if abs(m['stake_amt'] - ar['ar_stake']) < 0.01:
        exact_stake += 1
    else:
        print(f"STAKE MISMATCH: {m['stake_fixture']} Stake amt: {m['stake_amt']} vs Ar stake: {ar['ar_stake']}")

print(f"\n--- ACCURACY OF AUTORUN PLACED BETS ---")
print(f"Exact Selection Match: {exact_sel} / {len(autorun_matches)} ({exact_sel/len(autorun_matches)*100:.1f}%)")
print(f"Exact Stake Match: {exact_stake} / {len(autorun_matches)} ({exact_stake/len(autorun_matches)*100:.1f}%)")
