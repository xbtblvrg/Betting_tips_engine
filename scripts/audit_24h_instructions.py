import sqlite3
import json
from datetime import datetime, timezone, timedelta

conn = sqlite3.connect('data/engine.sqlite3')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

now = datetime.now(timezone.utc)
in_24h = now + timedelta(hours=24)

rows = cur.execute('SELECT * FROM bets WHERE status = "open"').fetchall()
print(f"Total open bets: {len(rows)}")

demoted_market_keys = {
    ('NFL', 'totals'),
    ('NHL', 'totals'),
    ('WNBA', 'totals'),
    ('EPL', 'totals'),
    ('MLS', 'totals'),
    ('EPL', 'draw_no_bet'),
    ('MLS', 'draw_no_bet'),
}

errors = []
verified_24h = []
verified_all = []

# To check opposing bets: game_id + market -> list of (side, line, tip_id)
market_contracts = {}

for r in rows:
    p = json.loads(r['payload']) if r['payload'] else {}
    tip_id = r['tip_id']
    league = p.get('league') or r['sport'] if 'sport' in r.keys() else p.get('league')
    market = p.get('market')
    side = p.get('side')
    line = p.get('line')
    game_id = p.get('game_id')
    stake = float(r['stake'])
    odds = float(r['odds'])
    prob = float(p.get('probability', 0.0))
    push_prob = float(p.get('push_probability', 0.0))
    exp_roi = float(p.get('expected_roi', 0.0))
    start_str = p.get('start')
    
    # Check start time
    if not start_str:
        errors.append((tip_id, "MISSING_START_TIME", "No start time in payload"))
        continue
    start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
    is_in_24h = (now < start_dt <= in_24h)
    is_future = (start_dt > now)

    if not is_future:
        errors.append((tip_id, "GAME_ALREADY_STARTED", f"Game started at {start_str}"))

    # 1. Stake bounds check: strictly $1.00 - $5.00
    if stake < 1.00 - 1e-6 or stake > 5.00 + 1e-6:
        errors.append((tip_id, "STAKE_OUT_OF_BOUNDS", f"Stake is ${stake:.2f}, must be between $1.00 and $5.00"))

    # 2. Demoted markets check
    if (league, market) in demoted_market_keys:
        errors.append((tip_id, "DEMOTED_MARKET", f"Bet on demoted market {league}:{market}"))

    # 3. Odds validity
    if odds <= 1.0:
        errors.append((tip_id, "INVALID_ODDS", f"Odds {odds} <= 1.0"))

    # 4. EV calculation
    # For push-eligible: EV = prob * (odds - 1) - (1 - prob - push_prob) * 1
    # Note: 1 - prob - push_prob = loss_prob
    # EV = prob * odds + push_prob - 1
    nominal_ev = prob * odds + push_prob - 1.0
    if nominal_ev <= 0.0:
        errors.append((tip_id, "NEGATIVE_EV", f"Nominal EV is {nominal_ev:.4f} <= 0 (p={prob:.3f}, push={push_prob:.3f}, odds={odds:.2f})"))

    # 5. Expected ROI recorded consistency
    if abs(exp_roi - nominal_ev) > 0.05 and abs(exp_roi - (prob * odds - 1)) > 0.05:
        errors.append((tip_id, "ROI_MISMATCH", f"Recorded ROI {exp_roi:.4f} differs from EV {nominal_ev:.4f}"))

    # 6. Minimum odds check
    min_odds = p.get('minimum_odds')
    fair_odds = p.get('fair_odds')
    if fair_odds and fair_odds > odds + 1e-4:
        errors.append((tip_id, "ODDS_BELOW_FAIR", f"Odds {odds} < fair_odds {fair_odds}"))

    # 7. Invariant check: conflicting bets on same game & market
    key = (game_id, market)
    if key not in market_contracts:
        market_contracts[key] = []
    market_contracts[key].append({
        'tip_id': tip_id,
        'side': side,
        'line': line,
        'selection': p.get('selection'),
        'stake': stake,
        'odds': odds
    })

    item_info = {
        'tip_id': tip_id,
        'league': league,
        'match': f"{p.get('home')} vs {p.get('away')}",
        'start': start_str,
        'selection': p.get('selection'),
        'market': market,
        'line': line,
        'side': side,
        'odds': odds,
        'stake': stake,
        'prob': prob,
        'push_prob': push_prob,
        'ev': nominal_ev,
        'expected_roi': exp_roi,
        'model': p.get('model_version')
    }

    if is_in_24h:
        verified_24h.append(item_info)
    verified_all.append(item_info)

# Check conflicting sides on identical lines
conflicts = []
for (game_id, market), picks in market_contracts.items():
    if len(picks) > 1:
        # Check if contradictory sides exist on the same line (e.g. over and under on same line)
        for i in range(len(picks)):
            for j in range(i+1, len(picks)):
                p1, p2 = picks[i], picks[j]
                if p1['line'] == p2['line'] and p1['side'] != p2['side']:
                    conflicts.append((p1, p2))

print(f"\n--- AUDIT RESULTS ---")
print(f"Total verified open bets: {len(verified_all)}")
print(f"Total verified in next 24h: {len(verified_24h)}")
print(f"Errors detected: {len(errors)}")
if errors:
    for e in errors[:10]:
        print("  ERROR:", e)
print(f"Conflicts detected: {len(conflicts)}")
if conflicts:
    for c in conflicts[:5]:
        print("  CONFLICT:", c)

# Print league breakdown for 24h
from collections import Counter
print("\n24h breakdown by league:")
for lg, cnt in Counter(x['league'] for x in verified_24h).items():
    stakes = [x['stake'] for x in verified_24h if x['league'] == lg]
    rois = [x['ev'] * 100 for x in verified_24h if x['league'] == lg]
    print(f"  {lg:6s}: {cnt:3d} bets | Avg Stake: ${sum(stakes)/len(stakes):.2f} | Avg EV: +{sum(rois)/len(rois):.1f}% | Stake range: [${min(stakes):.2f}, ${max(stakes):.2f}]")

print("\nSample 10 verified instructions in next 24h:")
for i, x in enumerate(verified_24h[:10]):
    print(f"  {i+1}. [{x['league']}] {x['match']} -> {x['selection']} (Odds: {x['odds']:.2f}, Stake: ${x['stake']:.2f}, EV: +{x['ev']*100:.1f}%)")
