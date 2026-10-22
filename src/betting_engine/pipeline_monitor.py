"""Pre-Publication Opportunity Funnel Monitor & Telemetry Engine.

Continuously audits candidate throughput across the decision pipeline:
1. Tracks total evaluated candidates, positive-EV opportunities, and funded selections.
2. Identifies and quantifies opportunity bottlenecks (e.g. event limits, period limits,
   family caps, or quarantine filters blocking high-conviction positive-EV bets).
3. Emits structured telemetry to `data/opportunity_funnel_status.json`.
4. Automatically flags bottlenecked games and alerts the autonomous AI investigator
   and learning worker to prevent silent profit loss / opportunity starvation.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .domain import utcnow
from .io_utils import atomic_json
from .providers import DATA

log = logging.getLogger(__name__)

FUNNEL_STATUS_PATH = DATA / 'opportunity_funnel_status.json'


@dataclass
class ThrottledGameDetail:
    game_id: str
    league: str
    home: str
    away: str
    blocked_count: int
    max_ev: float
    best_market: str
    reasons: List[str]


def record_cycle_funnel(
    candidates: List[Dict[str, Any]],
    recommendations: List[Dict[str, Any]],
    alternatives: List[Dict[str, Any]],
    settings: Dict[str, Any],
    data_dir: Path = DATA,
) -> Dict[str, Any]:
    """Audits and records the candidate opportunity funnel for the current publication cycle."""
    now = utcnow()
    now_iso = now.isoformat()

    total_candidates = len(candidates)
    funded_issued = [t for t in recommendations if float(t.get('stake', 0.0) or 0.0) > 0.0]
    funded_count = len(funded_issued)

    # Inspect all evaluated rows (chosen + alternatives)
    all_evaluated = recommendations + alternatives
    seen_ids = set()
    unique_evaluated: List[Dict[str, Any]] = []
    for item in all_evaluated:
        cid = item.get('id')
        if cid and cid not in seen_ids:
            seen_ids.add(cid)
            unique_evaluated.append(item)

    pos_ev_items: List[Dict[str, Any]] = []
    high_conviction_items: List[Dict[str, Any]] = []
    blocked_pos_ev_by_code: Counter = Counter()
    game_blocked_pos_ev: Dict[str, List[Dict[str, Any]]] = {}

    for item in unique_evaluated:
        ev = float(item.get('expected_roi') or 0.0)
        stake = float(item.get('stake') or 0.0)
        code = item.get('allocation_code', 'unknown')

        if ev > 0.0:
            pos_ev_items.append(item)
            if ev >= 0.025:
                high_conviction_items.append(item)

            if stake == 0.0:
                blocked_pos_ev_by_code[code] += 1
                gid = item.get('game_id')
                if gid:
                    if gid not in game_blocked_pos_ev:
                        game_blocked_pos_ev[gid] = []
                    game_blocked_pos_ev[gid].append(item)

    # Identify games where positive EV candidates were throttled by event/period limits
    throttled_games: List[Dict[str, Any]] = []
    potential_missed_alpha = 0.0

    for gid, items in game_blocked_pos_ev.items():
        limit_blocked = [it for it in items if it.get('allocation_code') in ('event_limit', 'period_limit', 'event_family_limit', 'event_family_cycle_limit')]
        if len(limit_blocked) >= 3:
            first = limit_blocked[0]
            max_ev_item = max(limit_blocked, key=lambda x: float(x.get('expected_roi') or 0.0))
            max_ev = float(max_ev_item.get('expected_roi') or 0.0)
            best_mkt = f"{max_ev_item.get('market')} {max_ev_item.get('side')} {max_ev_item.get('line')}"
            reasons = list({it.get('allocation_code') for it in limit_blocked})

            for it in limit_blocked:
                potential_missed_alpha += float(it.get('expected_roi') or 0.0) * 1.0  # Heuristic $1 base missed return

            throttled_games.append({
                'game_id': gid,
                'league': first.get('league', 'UNKNOWN'),
                'home': first.get('home', ''),
                'away': first.get('away', ''),
                'blocked_count': len(limit_blocked),
                'max_ev': round(max_ev, 4),
                'best_market': best_mkt,
                'reasons': reasons,
            })

    throttled_games.sort(key=lambda x: x['max_ev'], reverse=True)

    bottleneck_detected = len(throttled_games) > 0
    bottleneck_msg = ""
    if bottleneck_detected:
        bottleneck_msg = (
            f"Opportunity Bottleneck: {len(throttled_games)} games have >= 3 positive-EV candidates "
            f"throttled by event/period limits. Top game: {throttled_games[0]['game_id']} "
            f"({throttled_games[0]['blocked_count']} blocked, best EV {throttled_games[0]['max_ev']:+.2%})."
        )
        log.info("[OPPORTUNITY_FUNNEL] %s", bottleneck_msg)

    funnel_summary = {
        'monitored_at': now_iso,
        'total_evaluated_candidates': total_candidates,
        'positive_ev_candidates_count': len(pos_ev_items),
        'high_conviction_candidates_count': len(high_conviction_items),
        'funded_recommendations_count': funded_count,
        'conversion_rate_pct': round((funded_count / len(pos_ev_items) * 100) if pos_ev_items else 0.0, 1),
        'blocked_positive_ev_by_code': dict(blocked_pos_ev_by_code),
        'bottleneck_detected': bottleneck_detected,
        'bottleneck_message': bottleneck_msg,
        'throttled_games_count': len(throttled_games),
        'throttled_games': throttled_games[:10],
        'estimated_throttled_alpha_usd': round(potential_missed_alpha, 2),
    }
    anchor_mode = settings.get('market_anchor_mode', 'shadow')
    if anchor_mode != 'shadow':
        log.error("[PIPELINE_MONITOR] Critical invariant violation: market_anchor_mode is '%s', must be 'shadow'!", anchor_mode)
        funnel_summary['critical_invariant_alert'] = f"market_anchor_mode is '{anchor_mode}', violating AGENTS.md independent model mandate"

    # Enqueue repair job if there is a severe opportunity bottleneck or starvation
    if bottleneck_detected and len(throttled_games) >= 5:
        try:
            import sqlite3
            db_file = data_dir / 'engine.sqlite3'
            if db_file.exists():
                with sqlite3.connect(db_file) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='learning_jobs'")
                    if cur.fetchone():
                        now_dt = datetime.now(timezone.utc)
                        job_id = f"funnel_repair_{now_dt.strftime('%Y%m%d_%H')}"
                        payload = json.dumps({
                            'bottleneck_games': len(throttled_games),
                            'top_game': throttled_games[0]['game_id'],
                            'missed_alpha': round(potential_missed_alpha, 2),
                            'actionable_remedy': 'Review event/period allocation caps and cycle limits under publication_limits_version v3'
                        })
                        cur.execute("""
                            INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, attempts, payload)
                            VALUES (?, 'opportunity_funnel_repair', 'MULTI', 'queued', ?, ?, ?, 0, ?)
                            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                        """, (job_id, now_iso, now_iso, now_iso, payload))
                        # Coalesce older funnel repair jobs: mark older queued/retry jobs as superseded
                        cur.execute("""
                            UPDATE learning_jobs
                            SET status = 'superseded',
                                result = json_object('state', 'superseded', 'reason', 'Superseded by latest hourly opportunity funnel repair', 'superseded_by', ?),
                                updated_at = ?
                            WHERE kind = 'opportunity_funnel_repair'
                              AND id != ?
                              AND status IN ('queued', 'retry')
                        """, (job_id, now_iso, job_id))
                        conn.commit()
        except Exception as exc:
            log.warning("Could not enqueue opportunity funnel repair job: %s", exc)

    try:
        from .system_resource_governor import inspect_system_resources
        from .in_memory_cache import GLOBAL_IN_MEMORY_CACHE
        sys_metrics = inspect_system_resources()
        funnel_summary['system_resource_governor'] = {
            'safe_worker_limit': sys_metrics.safe_worker_limit,
            'load_1m': sys_metrics.load_1m,
            'available_ram_gb': sys_metrics.available_ram_gb,
            'throttle_recommended': sys_metrics.throttle_recommended,
            'cache_hit_rate': GLOBAL_IN_MEMORY_CACHE.stats()['hit_rate'],
        }
    except Exception as exc:
        log.debug("Resource governor metrics collection skipped: %s", exc)

    try:
        atomic_json(data_dir / 'opportunity_funnel_status.json', funnel_summary)
    except Exception as exc:
        log.warning("Failed to persist opportunity funnel status: %s", exc)

    return funnel_summary


def get_latest_opportunity_funnel_status(data_dir: Path = DATA) -> Dict[str, Any]:
    """Retrieves the latest persisted opportunity funnel status."""
    path = data_dir / 'opportunity_funnel_status.json'
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'bottleneck_detected': False, 'status': 'no_funnel_recorded'}
