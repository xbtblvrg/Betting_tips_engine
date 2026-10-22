"""Calculates total system edge compared to zero across all historical and settled bets."""
import sqlite3
import json

conn = sqlite3.connect('data/engine.sqlite3')
cur = conn.cursor()

# 1. Bets table summary
cur.execute('''
    SELECT 
        COUNT(*),
        SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status IN ('won', 'lost', 'push') THEN stake ELSE 0 END),
        SUM(CASE WHEN status IN ('won', 'lost', 'push') THEN COALESCE(pnl, 0) ELSE 0 END),
        SUM(CASE WHEN status = 'open' THEN stake ELSE 0 END)
    FROM bets
''')
row = cur.fetchone()
total_bets, won, lost, push, open_b, settled_stake, settled_pnl, open_stake = row

roi = (settled_pnl / settled_stake * 100) if settled_stake else 0.0
win_rate = (won / (won + lost) * 100) if (won + lost) else 0.0

print('=== BETS TABLE OVERALL METRICS ===')
print(f'Total bets in ledger: {total_bets}')
print(f'Settled bets: {won + lost + push} (Won: {won}, Lost: {lost}, Push: {push})')
print(f'Open bets: {open_b} (Open stake: ${open_stake:.2f})')
print(f'Total Settled Stake: ${settled_stake:.2f}')
print(f'Total Settled Realized PnL: ${settled_pnl:+.2f}')
print(f'Actual Realized ROI (PnL / Stake): {roi:+.2f}%')
print(f'Actual Win Rate (excluding push): {win_rate:.2f}%')

# 2. Average odds and implied probability
cur.execute('''
    SELECT AVG(odds), AVG(1.0 / odds) 
    FROM bets 
    WHERE status IN ('won', 'lost', 'push')
''')
avg_odds, avg_implied = cur.fetchone()
print(f'Average accepted odds: {avg_odds:.3f}')
print(f'Implied break-even win rate needed: {avg_implied*100:.2f}%')
print(f'Win Rate Delta (Win Rate - Break-even): {win_rate - avg_implied*100:+.2f}% points')

# 3. Model Expected Probability and Theoretical EV at issuance
cur.execute('''
    SELECT payload, odds, status, pnl, stake
    FROM bets
    WHERE status IN ('won', 'lost', 'push')
''')
settled_rows = cur.fetchall()

ev_list = []
model_p_list = []

for payload_str, odds, status, pnl, stake in settled_rows:
    try:
        p = json.loads(payload_str) if isinstance(payload_str, str) else {}
        prob = p.get('probability') or p.get('model_probability')
        ev = p.get('expected_value') or p.get('ev')
        if prob is not None:
            model_p_list.append(float(prob))
        if ev is not None:
            ev_list.append(float(ev))
    except Exception:
        pass

if ev_list:
    print(f'Theoretical Model Expected EV at issuance: {sum(ev_list)/len(ev_list)*100:+.2f}%')
if model_p_list:
    print(f'Theoretical Model Probability: {sum(model_p_list)/len(model_p_list)*100:.2f}%')

# 4. Closing Price Captures
cur.execute('SELECT COUNT(*) FROM closing_price_captures')
captures_count = cur.fetchone()[0]
print(f'Total Closing Price Evidence Captures: {captures_count}')

# 5. By sport breakdown
print('\n=== BY SPORT BREAKDOWN ===')
cur.execute('''
    SELECT 
        json_extract(payload, '$.league') as league,
        COUNT(*),
        SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as w,
        SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as l,
        SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END) as pu,
        SUM(stake) as total_stake,
        SUM(COALESCE(pnl, 0)) as total_pnl
    FROM bets
    WHERE status IN ('won', 'lost', 'push')
    GROUP BY league
    ORDER BY total_pnl DESC
''')
for lg, cnt, w, l, pu, st, pnl_val in cur.fetchall():
    s_roi = (pnl_val / st * 100) if st else 0.0
    s_wr = (w / (w + l) * 100) if (w + l) else 0.0
    lg_name = lg if lg else "UNKNOWN"
    print(f'{lg_name:<12} | Bets: {cnt:3d} | W-L-P: {w:3d}-{l:3d}-{pu:2d} ({s_wr:5.1f}%) | Stake: ${st:7.2f} | PnL: ${pnl_val:+7.2f} | ROI: {s_roi:+6.2f}%')

# 6. By Market Family breakdown
print('\n=== BY MARKET FAMILY BREAKDOWN ===')
cur.execute('''
    SELECT 
        json_extract(payload, '$.market') as market,
        COUNT(*),
        SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as w,
        SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as l,
        SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END) as pu,
        SUM(stake) as total_stake,
        SUM(COALESCE(pnl, 0)) as total_pnl
    FROM bets
    WHERE status IN ('won', 'lost', 'push')
    GROUP BY market
    ORDER BY total_pnl DESC
''')
for mkt, cnt, w, l, pu, st, pnl_val in cur.fetchall():
    s_roi = (pnl_val / st * 100) if st else 0.0
    s_wr = (w / (w + l) * 100) if (w + l) else 0.0
    mkt_name = mkt if mkt else "UNKNOWN"
    print(f'{mkt_name:<20} | Bets: {cnt:3d} | W-L-P: {w:3d}-{l:3d}-{pu:2d} ({s_wr:5.1f}%) | Stake: ${st:7.2f} | PnL: ${pnl_val:+7.2f} | ROI: {s_roi:+6.2f}%')

conn.close()
