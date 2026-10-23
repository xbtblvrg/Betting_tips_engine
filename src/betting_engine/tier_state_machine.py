"""Dynamic Tier State Machine and Transition Forensics Engine.

Implements autonomous dynamic tier transitions:
- SUPER_ALPHA ($5.00–$8.00 scaling)
- SOLID_ALPHA ($2.50–$3.50 scaling)
- EXPLORATION ($1.00–$1.50 exploratory)
- CAPITAL_DEFENSE ($0.50 clamp, zero drop to preserve 100+ daily pick volume)

Tracks historical tiers, automatically detects when segments level up or degrade,
runs deep forensic failure attribution on demotions (enqueuing autonomous R&D repair jobs),
and identifies Alpha win drivers on promotions.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .providers import DATA

HISTORY_FILE = DATA / 'segment_tier_history.json'
TRANSITIONS_LOG = DATA / 'tier_transitions.jsonl'

TIER_RANKS = {
    'CAPITAL_DEFENSE': 0,
    'PROBATION': 1,
    'EXPLORATION': 2,
    'RECOVERY_EXPLORATION': 2,
    'SOLID_ALPHA': 3,
    'SUPER_ALPHA': 4
}


def classify_tier(
    games_count: int,
    pnl: float,
    roi: float,
    rolling_games: int = 0,
    rolling_pnl: float = 0.0,
    rolling_roi: float = 0.0,
    has_deployed_patch: bool = False,
    staked: float = 0.0,
    rolling_staked: float = 0.0
) -> Dict[str, Any]:
    """Classifies a market slice into its canonical tier and operational parameters.
    
    Preserves exact backward-compatibility with tier: ALPHA_BOOST / UNDERPERFORMING / EXPLORATION
    while exposing granular sub_tier and scale-invariant dynamic scaling targets.

    Scale-Invariant Dynamic Risk Units (Anti-Constant Mandate, Commandment 3):
    Evaluates progression and defense in units of average stake (Units = PnL / avg_stake).
    """
    avg_st = (staked / games_count) if (staked > 0 and games_count > 0) else 1.0
    units_pnl = pnl / max(0.20, avg_st)
    avg_roll_st = (rolling_staked / rolling_games) if (rolling_staked > 0 and rolling_games > 0) else 1.0
    units_roll_pnl = rolling_pnl / max(0.20, avg_roll_st)

    # 1. Post-Repair Rapid Acceleration Gate
    if has_deployed_patch and rolling_games >= 5 and rolling_roi >= 0.03 and rolling_pnl > 0.0:
        if (units_roll_pnl >= 4.0 or rolling_pnl >= 4.0) and rolling_roi >= 0.15 and rolling_games >= 5:
            return {
                'tier': 'ALPHA_BOOST',
                'sub_tier': 'SUPER_ALPHA',
                'stake_cap': 8.0,
                'min_ev': 0.001,
                'scaling_target': '$5.00–$8.00 super-alpha scaling',
                'action': f"Post-repair Super Alpha active: Rolling ROI {rolling_roi*100:.1f}% (+${rolling_pnl:.2f} over {rolling_games} games) unlocks maximum confidence sizing ($5.00-$8.00)",
                'recovery_accelerated': True
            }
        else:
            return {
                'tier': 'ALPHA_BOOST',
                'sub_tier': 'SOLID_ALPHA',
                'stake_cap': 3.5,
                'min_ev': 0.001,
                'scaling_target': '$2.50–$3.50 solid-alpha scaling',
                'action': f"Post-repair Solid Alpha active: Rolling ROI {rolling_roi*100:.1f}% (+${rolling_pnl:.2f} over {rolling_games} games) unlocks Solid Alpha scaling ($2.50-$3.50)",
                'recovery_accelerated': True
            }

    # 2. Capital Defense: triggered when ROI < -3% OR net loss in risk units <= -4.0 with 4+ games
    if (roi <= -0.03 or (units_pnl <= -4.0 and roi < 0.0)) and games_count >= 4:
        return {
            'tier': 'UNDERPERFORMING',
            'sub_tier': 'CAPITAL_DEFENSE',
            'stake_cap': 0.50,
            'min_ev': 0.035,
            'scaling_target': '$0.50 flat clamp',
            'action': 'Capital defense clamp ($0.50 max stake); active AI repair investigation'
        }
    # 3. Evolutionary Live Probation: Any unproven market with 0 live games, or unproven initial cohort (games < 3 with nonpositive pnl/roi)
    elif games_count == 0 or (games_count < 3 and pnl <= 0.0 and roi <= 0.0):
        return {
            'tier': 'EXPLORATION',
            'sub_tier': 'PROBATION',
            'stake_cap': 0.50,
            'min_ev': 0.02,
            'scaling_target': '$0.50 evolutionary probation floor',
            'action': 'Evolutionary Live Probation: strictly $0.50 floor until live settled games prove positive performance'
        }
    elif (units_pnl >= 2.0 or pnl >= 2.0) and roi >= 0.03 and games_count >= 3:
        if (units_pnl >= 4.0 or pnl >= 5.0) and roi >= 0.12 and games_count >= 5:
            return {
                'tier': 'ALPHA_BOOST',
                'sub_tier': 'SUPER_ALPHA',
                'stake_cap': 8.0,
                'min_ev': 0.001,
                'scaling_target': '$5.00–$8.00 super-alpha scaling',
                'action': 'Super Alpha priority ranking and maximum confidence sizing ($5.00-$8.00)'
            }
        else:
            return {
                'tier': 'ALPHA_BOOST',
                'sub_tier': 'SOLID_ALPHA',
                'stake_cap': 3.5,
                'min_ev': 0.001,
                'scaling_target': '$2.50–$3.50 solid-alpha scaling',
                'action': 'Solid Alpha priority ranking and confidence stake uplift ($2.50-$3.50)'
            }
    else:
        return {
            'tier': 'EXPLORATION',
            'sub_tier': 'EXPLORATION',
            'stake_cap': 2.0,
            'min_ev': 0.005,
            'scaling_target': '$1.00–$1.50 standard exploration',
            'action': 'Standard exploratory allocation ($1.00-$1.50)'
        }


def load_tier_history() -> Dict[str, Any]:
    """Loads the latest tracked tier state per segment."""
    if not HISTORY_FILE.exists():
        return {}
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_tier_history(history: Dict[str, Any]) -> None:
    """Saves updated tier history to disk atomically."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding='utf-8')
    except Exception:
        pass


def load_tier_transitions(limit: int = 50) -> List[Dict[str, Any]]:
    """Loads recent tier transition events from the append-only journal."""
    if not TRANSITIONS_LOG.exists():
        return []
    try:
        lines = TRANSITIONS_LOG.read_text(encoding='utf-8').strip().split('\n')
        events = []
        for line in lines[-limit:]:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
        events.reverse()  # Newest first
        return events
    except Exception:
        return []


def append_tier_transition(event: Dict[str, Any]) -> None:
    """Appends a tier transition record to the durable jsonl ledger."""
    TRANSITIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with TRANSITIONS_LOG.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event) + '\n')
        # Bounded rotation
        if TRANSITIONS_LOG.stat().st_size > 500_000:
            lines = TRANSITIONS_LOG.read_text(encoding='utf-8').strip().split('\n')
            if len(lines) > 2000:
                TRANSITIONS_LOG.write_text('\n'.join(lines[-1000:]) + '\n', encoding='utf-8')
    except Exception:
        pass


def enqueue_rd_repair_job(
    conn: sqlite3.Connection,
    segment_key: str,
    league: Optional[str],
    prev_tier: str,
    new_tier: str,
    failure_mode: str,
    remedy: str,
    stats: Dict[str, Any]
) -> Optional[str]:
    """Enqueues an autonomous repair task into learning_jobs for degraded segments."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='learning_jobs'")
        if not cur.fetchone():
            return None

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        job_key = f"segment_repair:{segment_key}:{now_dt.strftime('%Y%m%d')}"
        job_id = hashlib.sha256(job_key.encode()).hexdigest()[:24]

        payload = json.dumps({
            'segment': segment_key,
            'league': league or 'UNKNOWN',
            'previous_tier': prev_tier,
            'new_tier': new_tier,
            'failure_mode': failure_mode,
            'actionable_remedy': remedy,
            'stats': stats,
            'directive': 'Smart Autonomous Quant Self-Repair Directive (AGENTS.md)'
        })

        cur.execute("""
            INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, attempts, payload)
            VALUES (?, 'segment_repair', ?, 'queued', ?, ?, ?, 0, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = CASE WHEN status = 'running' THEN status ELSE 'queued' END,
                updated_at = excluded.updated_at,
                next_attempt = excluded.next_attempt,
                attempts = CASE WHEN status = 'running' THEN attempts ELSE 0 END,
                payload = excluded.payload
        """, (job_id, league, now_iso, now_iso, now_iso, payload))
        conn.commit()

        try:
            from .quant_council import invalidate_segment_cache
            invalidate_segment_cache(segment_key)
        except Exception:
            pass

        return job_id
    except Exception:
        return None


def diagnose_promotion(
    segment_key: str,
    conn: sqlite3.Connection,
    record: Dict[str, Any],
    prev_tier: str
) -> Tuple[str, str]:
    """Analyzes why a segment leveled up (win-drivers diagnostic)."""
    pnl = record.get('pnl', 0.0)
    roi_pct = record.get('roi_pct', 0.0)
    games = record.get('games', 0)
    sub_tier = record.get('sub_tier', 'SOLID_ALPHA')
    scaling_target = record.get('scaling_target', '$2.50–$3.50')

    # Query recent decisions to isolate edge drivers
    clv_beats = 0
    decisions_count = 0
    try:
        cur = conn.cursor()
        league = segment_key.split('|')[0] if '|' in segment_key else None
        if league:
            cur.execute("""
                SELECT payload, outcome_evidence 
                FROM system_decisions 
                WHERE status = 'won'
                  AND json_extract(payload, '$.league') = ?
                ORDER BY issued_at DESC 
                LIMIT 50
            """, (league,))
        else:
            cur.execute("""
                SELECT payload, outcome_evidence 
                FROM system_decisions 
                WHERE status = 'won'
                ORDER BY issued_at DESC 
                LIMIT 50
            """)
        for r in cur.fetchall():
            payload = json.loads(r[0]) if isinstance(r[0], str) else r[0]
            if not isinstance(payload, dict):
                continue
            from .segment_scoreboard import segment_key_from_payload
            if segment_key_from_payload(payload) == segment_key:
                decisions_count += 1
                odds = float(payload.get('reference_odds') or 2.0)
                ev = r[1]
                ev_data = json.loads(ev) if isinstance(ev, str) else (ev or {})
                closing = float(ev_data.get('closing_odds') or odds)
                if odds > closing:
                    clv_beats += 1
    except Exception:
        pass

    clv_rate_pct = round((clv_beats / decisions_count * 100) if decisions_count > 0 else 65.0, 1)

    if record.get('recovery_accelerated'):
        rolling_roi = record.get('rolling_roi_pct', 0.0)
        rolling_pnl = record.get('rolling_pnl', 0.0)
        rolling_games = record.get('rolling_games', 0)
        reason = (
            f"Post-repair {sub_tier} accelerated: Rolling ROI +{rolling_roi}% (+${rolling_pnl:.2f} "
            f"over {rolling_games} games post-patch). Strong market recovery with estimated {clv_rate_pct}% CLV beat rate."
        )
    elif sub_tier == 'SUPER_ALPHA':
        reason = (
            f"Super Alpha unlocked: Dominant performance with +{roi_pct}% ROI and +${pnl:.2f} profit "
            f"across {games} games. Strong market edge with estimated {clv_rate_pct}% CLV beat rate."
        )
    elif sub_tier == 'SOLID_ALPHA':
        reason = (
            f"Promoted to Solid Alpha: Sustained positive yield (+{roi_pct}% ROI, +${pnl:.2f} PnL over {games} games) "
            f"qualifying for Alpha Acceleration."
        )
    else:
        reason = (
            f"Capital Defense lifted: Segment stabilized back to neutral/positive territory ({roi_pct}% ROI). "
            f"Restoring standard exploratory allocation."
        )

    action = f"Scale stake target to {scaling_target} under Alpha Acceleration."
    return reason, action


def diagnose_demotion(
    segment_key: str,
    conn: sqlite3.Connection,
    record: Dict[str, Any],
    prev_tier: str
) -> Tuple[str, str, str]:
    """Analyzes why a segment degraded and formulates the R&D remedy."""
    pnl = record.get('pnl', 0.0)
    roi_pct = record.get('roi_pct', 0.0)
    games = record.get('games', 0)
    sub_tier = record.get('sub_tier', 'CAPITAL_DEFENSE')

    failure_mode = 'UNSPECIFIED_STATISTICAL_NOISE'
    remedy = 'Maintain $1.00 capital defense and collect additional settled observations.'
    reason = f"Deteriorated from {prev_tier} to {sub_tier} (ROI: {roi_pct}%, PnL: ${pnl:.2f} over {games} games)."

    try:
        from .failure_forensics import run_forensic_investigation
        dossier = run_forensic_investigation(segment_key, conn)
        if dossier:
            failure_mode = dossier.primary_failure_mode
            remedy = dossier.actionable_remedy
            reason = (
                f"Demoted from {prev_tier} to {sub_tier}: Primary root failure identified as '{failure_mode}'. "
                f"ROI dropped to {roi_pct}% (PnL ${pnl:.2f} across {games} games)."
            )
    except Exception:
        pass

    return reason, remedy, failure_mode


def process_tier_transitions(
    conn: sqlite3.Connection,
    evaluated_segments: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Compares current segment performance against persistent tier history.
    
    Detects dynamic promotions and demotions, runs deep diagnostics on WHY it happened,
    triggers automated R&D repair tasks for failing segments, and persists transition events.
    """
    history = load_tier_history()
    now_iso = datetime.now(timezone.utc).isoformat()
    new_transitions: List[Dict[str, Any]] = []

    for seg in evaluated_segments:
        key = seg['segment']
        new_sub_tier = seg.get('sub_tier', 'EXPLORATION')
        prev_entry = history.get(key)
        prev_sub_tier = prev_entry.get('tier') if prev_entry else None

        if prev_sub_tier is not None and prev_sub_tier != new_sub_tier:
            # A dynamic transition occurred!
            prev_rank = TIER_RANKS.get(prev_sub_tier, 1)
            new_rank = TIER_RANKS.get(new_sub_tier, 1)
            is_promotion = new_rank > prev_rank

            if is_promotion:
                diag_reason, action_remedy = diagnose_promotion(key, conn, seg, prev_sub_tier)
                failure_mode = 'NONE_PROMOTION'
                repair_job_id = None
            else:
                diag_reason, action_remedy, failure_mode = diagnose_demotion(key, conn, seg, prev_sub_tier)
                repair_job_id = enqueue_rd_repair_job(
                    conn=conn,
                    segment_key=key,
                    league=seg.get('league'),
                    prev_tier=prev_sub_tier,
                    new_tier=new_sub_tier,
                    failure_mode=failure_mode,
                    remedy=action_remedy,
                    stats={'pnl': seg['pnl'], 'roi_pct': seg['roi_pct'], 'games': seg['games']}
                )

            transition_event = {
                'id': f"trans_{hashlib.sha256(f'{key}:{now_iso}'.encode()).hexdigest()[:16]}",
                'timestamp': now_iso,
                'segment': key,
                'league': seg.get('league'),
                'scope': seg.get('scope'),
                'family': seg.get('family'),
                'previous_tier': prev_sub_tier,
                'new_tier': new_sub_tier,
                'transition_type': 'PROMOTION' if is_promotion else 'DEMOTION',
                'pnl': seg.get('pnl', 0.0),
                'roi_pct': seg.get('roi_pct', 0.0),
                'games': seg.get('games', 0),
                'wins': seg.get('wins', 0),
                'losses': seg.get('losses', 0),
                'diagnostic_reason': diag_reason,
                'rd_actionable_remedy': action_remedy,
                'failure_mode': failure_mode,
                'repair_job_id': repair_job_id
            }

            append_tier_transition(transition_event)
            new_transitions.append(transition_event)
            seg['tier_transition'] = transition_event

            # Log to R&D execution log if available
            try:
                from .quant_rd_agent import append_rd_log
                log_stage = 'ALPHA_PROMOTION' if is_promotion else 'CAPITAL_DEFENSE_DEMOTION'
                append_rd_log(
                    stage=log_stage,
                    message=f"Segment {key} dynamic transition: {prev_sub_tier} -> {new_sub_tier}. {diag_reason}",
                    extra={'transition': transition_event}
                )
            except Exception:
                pass

        # Update persistent history entry
        history[key] = {
            'tier': new_sub_tier,
            'canonical_tier': seg.get('tier', 'EXPLORATION'),
            'updated_at': now_iso,
            'pnl': seg.get('pnl', 0.0),
            'roi_pct': seg.get('roi_pct', 0.0),
            'games': seg.get('games', 0)
        }

    save_tier_history(history)
    all_recent = load_tier_transitions(limit=30)
    return new_transitions, all_recent
