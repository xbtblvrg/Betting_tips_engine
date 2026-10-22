import urllib.request
import json
from datetime import datetime, timezone, timedelta

res = urllib.request.urlopen('http://127.0.0.1:8765/api/dashboard')
data = json.loads(res.read().decode())

now = datetime.now(timezone.utc)
in_24h = now + timedelta(hours=24)
in_48h = now + timedelta(hours=48)

tracked = data.get('tracked_bets', [])
print(f"Total tracked bets: {len(tracked)}")

# Groups by tip_id
groups = {}
for b in tracked:
    tip_id = b['tip_id']
    groups.setdefault(tip_id, []).append(b)

print(f"Unique tracked bets: {len(groups)}")

# Budapest day for each item
# Format: YYYY-MM-DD in Europe/Budapest
import zoneinfo
budapest_tz = zoneinfo.ZoneInfo("Europe/Budapest")
today_budapest = datetime.now(budapest_tz).strftime('%Y-%m-%d')
print(f"Today Budapest: {today_budapest}")

issuance_rows = data.get('status', {}).get('daily_progress', {}).get('issuance_rows', [])
today_issued_ids = set()
for r in issuance_rows:
    issued_at = r.get('issued_at') or r.get('start')
    if issued_at:
        try:
            dt = datetime.fromisoformat(issued_at.replace('Z', '+00:00')).astimezone(budapest_tz)
            if dt.strftime('%Y-%m-%d') == today_budapest:
                today_issued_ids.add(r['tip_id'])
        except Exception:
            pass

print(f"Today issued IDs in Budapest day: {len(today_issued_ids)}")

items = []
for tip_id, bets in groups.items():
    first = min(bets, key=lambda x: x['created_at'])
    payload = first['payload']
    start_str = payload.get('start')
    start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')) if start_str else None
    
    odds = sum(b['stake'] * b['odds'] for b in bets) / sum(b['stake'] for b in bets)
    stake = sum(b['stake'] for b in bets)
    
    items.append({
        'id': tip_id,
        'payload': payload,
        'start_dt': start_dt,
        'start_str': start_str,
        'league': payload.get('league'),
        'home': payload.get('home'),
        'away': payload.get('away'),
        'selection': payload.get('selection'),
        'market': payload.get('market'),
        'line': payload.get('line'),
        'odds': odds,
        'stake': stake,
        'probability': payload.get('probability', 0.0),
        'push_probability': payload.get('push_probability', 0.0),
        'expected_roi': payload.get('expected_roi', 0.0),
        'stress_roi': payload.get('stress_roi', 0.0),
        'created_at': first['created_at'],
        'status': first.get('status'),
        'is_today_issued': (tip_id in today_issued_ids),
        'instruction_review': first.get('instruction_review', {})
    })

# Check future items
future_items = [it for it in items if it['start_dt'] and it['start_dt'] > now]
print(f"Total future items: {len(future_items)}")

future_24h = [it for it in future_items if it['start_dt'] <= in_24h]
future_48h = [it for it in future_items if it['start_dt'] <= in_48h]

print(f"Future within 24h: {len(future_24h)}")
print(f"Future within 48h: {len(future_48h)}")

from collections import Counter
print("24h future league counts:", Counter(it['league'] for it in future_24h))
print("48h future league counts:", Counter(it['league'] for it in future_48h))

# Today issued and future within 24h:
today_24h = [it for it in future_24h if it['is_today_issued']]
print(f"Today issued AND future within 24h: {len(today_24h)}")
print("Today issued 24h league counts:", Counter(it['league'] for it in today_24h))

# Also check open bets issued in the last 24 hours regardless of match start
created_in_24h = [it for it in items if datetime.fromisoformat(it['created_at'].replace('Z', '+00:00')) >= now - timedelta(hours=24)]
print(f"Items created in the last 24h: {len(created_in_24h)}")
print("Created in last 24h league counts:", Counter(it['league'] for it in created_in_24h))
