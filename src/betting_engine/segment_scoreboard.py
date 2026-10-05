"""Segment Scoreboard and Dynamic Capital Allocation Engine.

Fulfills D29: Segment scoreboard and evidence-weighted allocation.
Evaluates historical performance across league|scope|market_family,
dynamically classifies segments into ALPHA_BOOST, EXPLORATION, and
UNDERPERFORMING, applies capital defense, and routes failing segments
into autonomous AI learning/repair tasks.
"""
from datetime import datetime, timezone
import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from .contracts import allocation_family, split_market


def segment_key_from_payload(payload: dict) -> Optional[str]:
    """Generates the canonical segment key league|scope|allocation_family."""
    if not isinstance(payload, dict):
        return None
    league = payload.get('league')
    market = payload.get('market')
    if not league or not market:
        return None
    scope, _ = split_market(market)
    fam = allocation_family(payload)
    return f"{league}|{scope}|{fam}"


def compute_segment_scoreboard(conn: sqlite3.Connection) -> dict:
    """Computes auditable performance metrics and classifications per segment.
    
    Uses immutable settled system_decisions (status in won, lost, push).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT payload, status, pnl 
        FROM system_decisions 
        WHERE status IN ('won', 'lost', 'push')
    """)
    rows = cur.fetchall()

    segments: Dict[str, dict] = {}
    total_staked = 0.0
    total_pnl = 0.0

    for row in rows:
        payload_raw, status, pnl_raw = row[0], row[1], row[2]
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        key = segment_key_from_payload(payload)
        if not key:
            continue

        pnl = float(pnl_raw) if pnl_raw is not None else 0.0
        stake = float(payload.get('stake', 1.0)) if isinstance(payload, dict) else 1.0
        if stake <= 0:
            stake = 1.0

        total_staked += stake
        total_pnl += pnl

        seg = segments.setdefault(key, {
            'segment': key,
            'league': payload.get('league'),
            'scope': key.split('|')[1] if '|' in key else 'full_game',
            'family': key.split('|')[2] if '|' in key else 'unknown',
            'stake': 0.0,
            'pnl': 0.0,
            'wins': 0,
            'losses': 0,
            'pushes': 0,
            'games': set()
        })

        seg['stake'] += stake
        seg['pnl'] += pnl
        if status == 'won':
            seg['wins'] += 1
        elif status == 'lost':
            seg['losses'] += 1
        elif status == 'push':
            seg['pushes'] += 1

        game_id = payload.get('game_id')
        if game_id:
            seg['games'].add(str(game_id))

    # Classify segments
    alpha_segments = []
    exploration_segments = []
    underperforming_segments = []

    for key, s in segments.items():
        games_count = len(s['games'])
        stake = s['stake']
        pnl = s['pnl']
        roi = pnl / stake if stake > 0 else 0.0
        decisive = s['wins'] + s['losses']
        win_rate = s['wins'] / decisive if decisive > 0 else 0.0

        record = {
            'segment': key,
            'league': s['league'],
            'scope': s['scope'],
            'family': s['family'],
            'stake': round(stake, 2),
            'pnl': round(pnl, 2),
            'roi_pct': round(roi * 100, 2),
            'roi': round(roi, 4),
            'games': games_count,
            'wins': s['wins'],
            'losses': s['losses'],
            'pushes': s['pushes'],
            'win_rate_pct': round(win_rate * 100, 1),
        }

        # Classification criteria:
        # 1. ALPHA_BOOST: Positive PnL > $2.00, ROI > +3.0%, at least 3 independent games
        # 2. UNDERPERFORMING: Negative ROI < -3.0%, at least 4 independent games
        # 3. EXPLORATION: Emerging markets, low sample, or neutral performance
        if pnl >= 2.0 and roi >= 0.03 and games_count >= 3:
            record['tier'] = 'ALPHA_BOOST'
            record['action'] = 'Priority ranking and confidence stake uplift ($2.00-$5.00)'
            record['stake_cap'] = 5.0
            record['min_ev'] = 0.001
            alpha_segments.append(record)
        elif roi <= -0.03 and games_count >= 4:
            record['tier'] = 'UNDERPERFORMING'
            record['action'] = 'Capital defense clamp ($1.00 max stake); active AI repair investigation'
            record['stake_cap'] = 1.0
            record['min_ev'] = 0.035  # Require higher conviction on troubled segments
            underperforming_segments.append(record)
        else:
            record['tier'] = 'EXPLORATION'
            record['action'] = 'Standard exploratory allocation ($1.00-$1.50)'
            record['stake_cap'] = 2.0
            record['min_ev'] = 0.005
            exploration_segments.append(record)

    alpha_segments.sort(key=lambda x: x['pnl'], reverse=True)
    exploration_segments.sort(key=lambda x: x['games'], reverse=True)
    underperforming_segments.sort(key=lambda x: x['pnl'])

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_staked': round(total_staked, 2),
        'total_pnl': round(total_pnl, 2),
        'overall_roi_pct': round((total_pnl / total_staked * 100) if total_staked > 0 else 0.0, 2),
        'counts': {
            'alpha': len(alpha_segments),
            'exploration': len(exploration_segments),
            'underperforming': len(underperforming_segments),
            'total_segments': len(segments)
        },
        'alpha_segments': alpha_segments,
        'exploration_segments': exploration_segments,
        'underperforming_segments': underperforming_segments,
    }
