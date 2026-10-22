from betting_engine.shadow_market_incubator import ShadowMarketIncubator
from betting_engine.providers import DATA
import json

inc = ShadowMarketIncubator(DATA)
reconciled = inc.reconcile_aged_and_completed_wagers(min_age_hours=24.0)
print("Reconciled aged wagers:", reconciled)

graduated = inc.enforce_unlimited_proven_graduation()
print("Newly graduated in this pass:", len(graduated))
for g in graduated:
    st = g.get('stats', {})
    seg = g.get('segment_key')
    lg = g.get('league')
    roi = st.get('roi', 0.0) * 100.0
    pnl = st.get('total_pnl', 0.0)
    n = st.get('sample_size', 0)
    print("  + %s (%s): ROI=+%.2f%%, PnL=+$%.2f, N=%d" % (seg, lg, roi, pnl, n))

with open('data/graduated_markets.json') as f:
    total_grad = json.load(f)
print("\nTotal currently active graduated markets in portfolio: %d" % len(total_grad))
by_league = {}
for k, v in total_grad.items():
    l = v.get('league', 'UNKNOWN')
    by_league[l] = by_league.get(l, 0) + 1
for l, c in sorted(by_league.items()):
    print("  %s: %d markets" % (l, c))
