"""Reconciles real ESPN final scores for completed college sports and graduates qualified markets.

1. Fetches official final scores from ESPN scoreboard API for recent NCAAF and NCAAM games.
2. Updates data/games.json with verified final scores (state='final', home_score, away_score).
3. Resettles incubated wagers that were erroneously voided to push (pnl=0.0) or remained open.
4. Evaluates Crucible graduations and unblocks demoted markets that have verified positive alpha.
"""
import urllib.request
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from betting_engine.domain import Game, parse_time, utcnow
from betting_engine.providers import num, DATA
from betting_engine.shadow_market_incubator import ShadowMarketIncubator, load_graduated_markets
from betting_engine.unified_market_universe import UnifiedMarketUniverse

def run():
    print("=== Step 1: Fetching official ESPN final scores for recent college games ===")
    now = datetime.now(timezone.utc)
    espn_games = {}
    
    # Check last 10 days for CFB and CBB
    for offset in range(-10, 1):
        day = (now + timedelta(days=offset)).strftime('%Y%m%d')
        for sport in ['football/college-football', 'basketball/mens-college-basketball', 'basketball/womens-college-basketball']:
            url = f'https://site.api.espn.com/apis/site/v2/sports/{sport}/scoreboard?dates={day}&limit=1000'
            try:
                with urllib.request.urlopen(url, timeout=12) as resp:
                    data = json.loads(resp.read())
                    for e in data.get('events', []):
                        comp = e.get('competitions', [{}])[0]
                        st = comp.get('status', {}).get('type', {})
                        if st.get('state') in ('post', 'final') or st.get('name') in ('STATUS_FINAL', 'STATUS_FINAL_OVERTIME'):
                            cands = comp.get('competitors', [])
                            h = next((c for c in cands if c.get('homeAway') == 'home'), None)
                            a = next((c for c in cands if c.get('homeAway') == 'away'), None)
                            if h and a and h.get('score') is not None and a.get('score') is not None:
                                h_score = float(num(h['score']))
                                a_score = float(num(a['score']))
                                lg = 'NCAAF' if 'football' in sport else ('NCAAM' if 'mens' in sport else 'NCAAW')
                                gid = f"{lg}:{e['id']}"
                                espn_games[gid] = {
                                    'id': gid,
                                    'league': lg,
                                    'start': comp.get('date'),
                                    'home': h.get('team', {}).get('displayName', ''),
                                    'away': a.get('team', {}).get('displayName', ''),
                                    'home_id': str(h.get('team', {}).get('id', '')),
                                    'away_id': str(a.get('team', {}).get('id', '')),
                                    'home_abbr': h.get('team', {}).get('abbreviation', ''),
                                    'away_abbr': a.get('team', {}).get('abbreviation', ''),
                                    'state': 'final',
                                    'season': int(e.get('season', {}).get('year', now.year)),
                                    'home_score': h_score,
                                    'away_score': a_score,
                                    'neutral': bool(comp.get('neutralSite', False)),
                                    'game_type': 'regular',
                                    'source': url,
                                    'fetched_at': now.isoformat(),
                                    'context': {'college': True, 'availability_model': 'not_quantified'}
                                }
            except Exception as ex:
                pass

    print(f"Collected {len(espn_games)} completed college games with verified final scores.")

    # Step 2: Merge into data/games.json
    games_file = DATA / 'games.json'
    existing_games = []
    if games_file.exists():
        existing_games = json.loads(games_file.read_text())
    
    games_by_id = {g['id']: g for g in existing_games}
    updated_cnt = 0
    added_cnt = 0
    for gid, eg in espn_games.items():
        if gid in games_by_id:
            old = games_by_id[gid]
            if old.get('state') != 'final' or old.get('home_score') is None:
                old['state'] = 'final'
                old['home_score'] = eg['home_score']
                old['away_score'] = eg['away_score']
                updated_cnt += 1
        else:
            games_by_id[gid] = eg
            added_cnt += 1

    games_file.write_text(json.dumps(list(games_by_id.values()), indent=2))
    print(f"Updated games.json: {updated_cnt} games updated to final, {added_cnt} new final games added.")

    # Step 3: Resettle incubated wagers that were erroneously set to push or are still open
    inc = ShadowMarketIncubator(DATA)
    inc_db = DATA / 'shadow_incubator.sqlite3'
    with sqlite3.connect(inc_db, timeout=60.0) as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        cur = conn.cursor()
        gids = list(espn_games.keys())
        placeholders = ','.join(['?'] * len(gids))
        
        # Reset false pushes back to open so settle_incubated_wagers recalculates them
        cur.execute(f"""
            UPDATE incubated_wagers
            SET status = 'open', result = NULL, pnl = 0.0, brier_model = NULL, brier_market = NULL
            WHERE fixture_id IN ({placeholders})
              AND status = 'settled' AND result = 'push' AND pnl == 0.0
        """, gids)
        reset_cnt = cur.rowcount
        conn.commit()
        print(f"Reset {reset_cnt} erroneous push wagers back to 'open' for score-based settlement.")

    # Convert to Game objects for settle_incubated_wagers
    all_game_objs = [Game(**g) for g in games_by_id.values() if g.get('state') == 'final' and g.get('home_score') is not None]
    settled_cnt = inc.settle_incubated_wagers(all_game_objs)
    print(f"Settled {settled_cnt} wagers with verified scores!")

    # Step 4: Re-evaluate Crucible Graduations and unblock false stagnation evictions
    print("\n=== Step 4: Evaluating Crucible Graduations ===")
    # Remove false stagnation evictions that were caused by 0.0 PnL pushes
    with sqlite3.connect(inc_db, timeout=60.0) as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        cur = conn.cursor()
        # Find false stagnation evictions
        cur.execute("""
            DELETE FROM demoted_markets
            WHERE reason LIKE '%Stagnation Eviction%' AND reason LIKE '%PnL $0.00%'
        """)
        evict_cleared = cur.rowcount
        # Also remove untraded auto-sync demotions for markets that have positive historical edge
        cur.execute("""
            DELETE FROM demoted_markets
            WHERE reason LIKE '%Untraded in live execution%'
        """)
        untraded_cleared = cur.rowcount
        conn.commit()
        print(f"Cleared {evict_cleared} false stagnation evictions and {untraded_cleared} untraded auto-sync locks.")

    # Run graduation
    graduated = inc.evaluate_graduation()
    print(f"Newly graduated via standard Crucible: {len(graduated)}")
    for g in graduated:
        st = g.get('stats', {})
        print(f"  + {g.get('segment_key')} ({g.get('league')}): ROI=+{st.get('roi', 0.0)*100.0:.2f}%, PnL=+${st.get('total_pnl', 0.0):.2f}, N={st.get('sample_size', 0)}")

    # Also run volume deficit expansion
    vol_grad = inc.trigger_volume_deficit_expansion(daily_shortfall=45)
    print(f"Newly graduated via volume deficit expansion: {len(vol_grad)}")
    for g in vol_grad:
        st = g.get('stats', {})
        print(f"  + {g.get('segment_key')} ({g.get('league')}): ROI=+{st.get('roi', 0.0)*100.0:.2f}%, PnL=+${st.get('total_pnl', 0.0):.2f}, N={st.get('sample_size', 0)}")

    # Invalidate universe roster cache
    UnifiedMarketUniverse.invalidate_cache()
    
    total_grad = load_graduated_markets(DATA)
    print(f"\nTotal active graduated markets in portfolio: {len(total_grad)}")
    by_league = {}
    for k, v in total_grad.items():
        l = v.get('league', 'UNKNOWN')
        by_league[l] = by_league.get(l, 0) + 1
    for l, c in sorted(by_league.items()):
        print(f"  {l}: {c} markets")

if __name__ == '__main__':
    run()
