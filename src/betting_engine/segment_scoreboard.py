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
        ORDER BY issued_at ASC
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
            'games': set(),
            'wagers': []
        })
        seg['stake'] += stake
        seg['pnl'] += pnl
        game_id = payload.get('game_id') if isinstance(payload, dict) else None
        seg['wagers'].append({'pnl': pnl, 'stake': stake, 'game_id': game_id})
        if status == 'won':
            seg['wins'] += 1
        elif status == 'lost':
            seg['losses'] += 1
        elif status == 'push':
            seg['pushes'] += 1

        if game_id:
            seg['games'].add(str(game_id))

    # Identify segments with active deployed repair patches
    try:
        from .crucible_rules import get_deployed_crucible_rules
        deployed_rules = get_deployed_crucible_rules()
        patched_segments = {r.get('segment') for r in deployed_rules if r.get('segment')}
    except Exception:
        patched_segments = set()

    # Classify segments
    alpha_segments = []
    exploration_segments = []
    underperforming_segments = []

    for key, s in segments.items():
        games_count = len(s['games'])
        stake_val = round(s['stake'], 2)
        pnl_val = round(s['pnl'], 2)
        roi_val = round(pnl_val / stake_val, 4) if stake_val > 0 else 0.0
        roi_pct = round(roi_val * 100, 2)
        decisive = s['wins'] + s['losses']
        win_rate = s['wins'] / decisive if decisive > 0 else 0.0

        # Rolling Post-Repair Cohort (last 20 wagers)
        recent_wagers = s.get('wagers', [])[-20:]
        rolling_stake = round(sum(w['stake'] for w in recent_wagers), 2)
        rolling_pnl = round(sum(w['pnl'] for w in recent_wagers), 2)
        rolling_roi = round(rolling_pnl / rolling_stake, 4) if rolling_stake > 0 else 0.0
        rolling_games = len(set(str(w['game_id']) for w in recent_wagers if w.get('game_id')))
        has_deployed_patch = key in patched_segments

        record = {
            'segment': key,
            'league': s['league'],
            'scope': s['scope'],
            'family': s['family'],
            'stake': stake_val,
            'pnl': pnl_val,
            'roi_pct': roi_pct,
            'roi': roi_val,
            'games': games_count,
            'wins': s['wins'],
            'losses': s['losses'],
            'pushes': s['pushes'],
            'win_rate_pct': round(win_rate * 100, 1),
            'rolling_stake': rolling_stake,
            'rolling_pnl': rolling_pnl,
            'rolling_roi_pct': round(rolling_roi * 100, 2),
            'rolling_games': rolling_games,
            'has_deployed_patch': has_deployed_patch,
        }

        from .tier_state_machine import classify_tier
        tier_info = classify_tier(
            games_count=games_count,
            pnl=pnl_val,
            roi=roi_val,
            rolling_games=rolling_games,
            rolling_pnl=rolling_pnl,
            rolling_roi=rolling_roi,
            has_deployed_patch=has_deployed_patch
        )
        record.update(tier_info)

        if record['tier'] == 'ALPHA_BOOST':
            if record['pnl'] > 0:
                alpha_segments.append(record)
            else:
                exploration_segments.append(record)
        elif record['tier'] == 'UNDERPERFORMING':
            underperforming_segments.append(record)
        else:
            exploration_segments.append(record)

    alpha_segments.sort(key=lambda x: x['pnl'], reverse=True)
    exploration_segments.sort(key=lambda x: x['games'], reverse=True)
    underperforming_segments.sort(key=lambda x: x['pnl'])

    all_evaluated = alpha_segments + exploration_segments + underperforming_segments
    from .tier_state_machine import process_tier_transitions
    new_transitions, recent_transitions = process_tier_transitions(conn, all_evaluated)

    total_staked_clean = round(sum(s['stake'] for s in all_evaluated), 2)
    total_pnl_clean = round(sum(s['pnl'] for s in all_evaluated), 2)
    overall_roi = round((total_pnl_clean / total_staked_clean * 100) if total_staked_clean > 0 else 0.0, 2)

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_staked': total_staked_clean,
        'total_pnl': total_pnl_clean,
        'overall_roi_pct': overall_roi,
        'counts': {
            'alpha': len(alpha_segments),
            'super_alpha': sum(1 for s in alpha_segments if s.get('sub_tier') == 'SUPER_ALPHA'),
            'solid_alpha': sum(1 for s in alpha_segments if s.get('sub_tier') == 'SOLID_ALPHA'),
            'exploration': len(exploration_segments),
            'underperforming': len(underperforming_segments),
            'capital_defense': len(underperforming_segments),
            'total_segments': len(segments)
        },
        'recent_transitions': new_transitions,
        'transitions_history': recent_transitions,
        'alpha_segments': alpha_segments,
        'exploration_segments': exploration_segments,
        'underperforming_segments': underperforming_segments,
    }

