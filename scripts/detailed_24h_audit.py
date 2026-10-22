import sqlite3
import json
from datetime import datetime, timezone, timedelta

conn = sqlite3.connect('data/engine.sqlite3')
conn.row_factory = sqlite3.Row
rows = conn.cursor().execute('SELECT * FROM bets WHERE status = "open"').fetchall()

now = datetime.now(timezone.utc)
in_24h = now + timedelta(hours=24)

bets_24h = []
for r in rows:
    p = json.loads(r['payload']) if r['payload'] else {}
    start_str = p.get('start')
    if start_str:
        s_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        if now < s_dt <= in_24h:
            prob = float(p.get('probability', 0.0))
            push = float(p.get('push_probability', 0.0))
            odds = float(r['odds'])
            ev = prob * odds + push - 1.0
            tier = p.get('funding_basis', {}).get('tier') if p.get('funding_basis') else None
            bets_24h.append({
                'id': r['id'],
                'tip_id': r['tip_id'],
                'created_at': r['created_at'],
                'league': p.get('league'),
                'home': p.get('home'),
                'away': p.get('away'),
                'selection': p.get('selection'),
                'market': p.get('market'),
                'line': p.get('line'),
                'odds': odds,
                'stake': float(r['stake']),
                'prob': prob,
                'push': push,
                'ev': ev,
                'tier': tier
            })

print(f"Total 24h bets: {len(bets_24h)}")

neg_ev = [b for b in bets_24h if b['ev'] <= 0]
print(f"Negative EV bets in 24h: {len(neg_ev)}")
for b in neg_ev:
    print(f"  NEG EV: {b['created_at'][:10]} | {b['league']} | {b['home']} vs {b['away']} -> {b['selection']} | odds: {b['odds']:.2f} | ev: {b['ev']*100:.2f}% | tier: {b['tier']}")

pos_ev = [b for b in bets_24h if b['ev'] > 0]
print(f"Positive EV bets in 24h: {len(pos_ev)}")

stakes = [b['stake'] for b in bets_24h]
print(f"Stake range: min=${min(stakes):.2f}, max=${max(stakes):.2f}, avg=${sum(stakes)/len(stakes):.2f}")

odds = [b['odds'] for b in bets_24h]
print(f"Odds range: min={min(odds):.2f}, max={max(odds):.2f}, avg={sum(odds)/len(odds):.2f}")

evs = [b['ev']*100 for b in pos_ev]
print(f"Positive EV range: min=+{min(evs):.2f}%, max=+{max(evs):.2f}%, avg=+{sum(evs)/len(evs):.2f}%")

from collections import Counter
print("\nBreakdown by league in 24h:")
for lg, cnt in Counter(b['league'] for b in bets_24h).most_common():
    lg_bets = [b for b in bets_24h if b['league'] == lg]
    lg_stakes = [b['stake'] for b in lg_bets]
    lg_evs = [b['ev']*100 for b in lg_bets]
    print(f"  {lg:6s}: {cnt:3d} bets | Stake avg: ${sum(lg_stakes)/len(lg_stakes):.2f} | EV avg: +{sum(lg_evs)/len(lg_evs):.1f}% | Min EV: {min(lg_evs):.1f}%")

print("\nBreakdown by creation date in 24h:")
for dt, cnt in Counter(b['created_at'][:10] for b in bets_24h).items():
    print(f"  Issued on {dt}: {cnt} bets")
