"""Target self-evaluation and automated AI solution search module.

Monitors and enforces the unified mandate:
- Minimum 700 distinct funded recommendations per rolling week
- Minimum +10.0% sustained weekly ROI target
- Consistent cumulative daily PnL uptrend

If the target is not fulfilled or pacing falls behind, the AI automatically
initiates an end-to-end quantitative failure investigation, cognitive sports
reasoning, Multi-AI Quant Council deliberation, and leak-free Walk-Forward
Crucible backtesting to repair underperforming segments and guarantee target achievement.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from .domain import parse_time, utcnow
from .io_utils import atomic_json
from .providers import DATA

TARGET_EVALUATION_VERSION = 'unified-700-target-eval-v1'
WEEKLY_BET_TARGET = 700
WEEKLY_ROI_TARGET = 10.0
CHECKPOINT_REGIME_START = '2026-10-12T00:00:00+00:00'
CHECKPOINT_DUE_DATE = '2026-10-17T00:00:00+00:00'


def evaluate_target_metrics(conn: sqlite3.Connection, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Computes exact empirical progress toward the 700 bets / 10% ROI mandate."""
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor()

    # 1. Rolling 7-day performance
    seven_days_ago = (now - timedelta(days=7)).isoformat()
    cur.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as won,
            SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as lost,
            SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END) as push,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open,
            ROUND(SUM(CASE WHEN status != 'open' THEN pnl ELSE 0 END), 2) as pnl,
            ROUND(SUM(CASE WHEN status != 'open' THEN stake ELSE 0 END), 2) as settled_stake,
            ROUND(SUM(CASE WHEN status = 'open' THEN stake ELSE 0 END), 2) as open_stake,
            ROUND(SUM(CASE WHEN status != 'open' THEN pnl ELSE 0 END) / NULLIF(SUM(CASE WHEN status != 'open' THEN stake ELSE 0 END), 0) * 100, 2) as roi_pct
        FROM bets
        WHERE created_at >= ?
    ''', (seven_days_ago,))
    row7 = cur.fetchone()
    rolling_7d = {
        'total_bets': row7[0] or 0,
        'won': row7[1] or 0,
        'lost': row7[2] or 0,
        'push': row7[3] or 0,
        'open': row7[4] or 0,
        'pnl': float(row7[5] or 0.0),
        'settled_stake': float(row7[6] or 0.0),
        'open_stake': float(row7[7] or 0.0),
        'roi_pct': float(row7[8]) if row7[8] is not None else None,
    }

    # 2. Post-Oct 12 Dual Dynamic Execution regime performance
    cur.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as won,
            SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as lost,
            SUM(CASE WHEN status = 'push' THEN 1 ELSE 0 END) as push,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open,
            ROUND(SUM(CASE WHEN status != 'open' THEN pnl ELSE 0 END), 2) as pnl,
            ROUND(SUM(CASE WHEN status != 'open' THEN stake ELSE 0 END), 2) as settled_stake,
            ROUND(SUM(CASE WHEN status = 'open' THEN stake ELSE 0 END), 2) as open_stake,
            ROUND(SUM(CASE WHEN status != 'open' THEN pnl ELSE 0 END) / NULLIF(SUM(CASE WHEN status != 'open' THEN stake ELSE 0 END), 0) * 100, 2) as roi_pct
        FROM bets
        WHERE created_at >= ?
    ''', (CHECKPOINT_REGIME_START,))
    row_regime = cur.fetchone()
    regime_stats = {
        'total_bets': row_regime[0] or 0,
        'won': row_regime[1] or 0,
        'lost': row_regime[2] or 0,
        'push': row_regime[3] or 0,
        'open': row_regime[4] or 0,
        'pnl': float(row_regime[5] or 0.0),
        'settled_stake': float(row_regime[6] or 0.0),
        'open_stake': float(row_regime[7] or 0.0),
        'roi_pct': float(row_regime[8]) if row_regime[8] is not None else None,
    }

    # 3. Gap & Pacing analysis
    volume_gap = max(0, WEEKLY_BET_TARGET - regime_stats['total_bets'])
    current_roi = regime_stats['roi_pct'] if regime_stats['roi_pct'] is not None else (rolling_7d['roi_pct'] or 0.0)
    roi_gap = round(WEEKLY_ROI_TARGET - current_roi, 2)

    # 4. Status determination
    shortfall_reasons = []
    if rolling_7d['roi_pct'] is not None and rolling_7d['roi_pct'] < WEEKLY_ROI_TARGET:
        shortfall_reasons.append(f"Rolling 7d ROI ({rolling_7d['roi_pct']:.2f}%) below target ({WEEKLY_ROI_TARGET:.1f}%)")
    if regime_stats['total_bets'] < WEEKLY_BET_TARGET:
        shortfall_reasons.append(f"Regime bet volume ({regime_stats['total_bets']}) below full 700 milestone (gap: {volume_gap})")
    if regime_stats['pnl'] < 0:
        shortfall_reasons.append(f"Regime net PnL is negative (${regime_stats['pnl']:.2f})")

    if rolling_7d['total_bets'] >= WEEKLY_BET_TARGET and (rolling_7d['roi_pct'] or 0.0) >= WEEKLY_ROI_TARGET:
        status = 'FULFILLED'
    elif not shortfall_reasons or (current_roi >= WEEKLY_ROI_TARGET and volume_gap <= 200):
        status = 'ON_TRACK'
    else:
        status = 'TARGET_SHORTFALL'

    return {
        'version': TARGET_EVALUATION_VERSION,
        'evaluated_at': now.isoformat(),
        'status': status,
        'mandate': {
            'target_weekly_bets': WEEKLY_BET_TARGET,
            'target_weekly_roi_pct': WEEKLY_ROI_TARGET,
            'checkpoint_regime_start': CHECKPOINT_REGIME_START,
            'checkpoint_due_date': CHECKPOINT_DUE_DATE
        },
        'rolling_7d': rolling_7d,
        'regime_stats': regime_stats,
        'gaps': {
            'volume_gap': volume_gap,
            'roi_gap': roi_gap,
            'shortfall_reasons': shortfall_reasons
        }
    }


def execute_target_investigation_and_repair(conn: sqlite3.Connection, evaluation: Dict[str, Any], data_dir: Path) -> Dict[str, Any]:
    """Initiates an automated forensic investigation and solution search when target is in shortfall."""
    from .segment_scoreboard import compute_segment_scoreboard
    from .quant_rd_agent import SupervisedQuantRDAgent, append_rd_log

    append_rd_log(
        'TARGET_EVALUATOR_ALERT',
        f"Target evaluation trigger: status={evaluation['status']}. Initiating automated investigation and solution search. "
        f"Reasons: {'; '.join(evaluation['gaps']['shortfall_reasons'])}",
        level='WARN'
    )

    scoreboard = compute_segment_scoreboard(conn)
    underperforming = scoreboard.get('underperforming_segments', [])

    # Filter to segments with genuine statistical drag (scale-invariant risk units)
    dragging = []
    for s in underperforming:
        g = s.get('games') or s.get('sample_size') or 0
        stk = s.get('staked', 0.0)
        pnl = s.get('pnl', 0.0)
        avg_s = stk / max(1, g) if g > 0 and stk > 0 else 1.0
        u_pnl = s.get('units_pnl', pnl / max(0.5, avg_s))
        if g >= 4 and ((s.get('roi_pct') or 0.0) < -3.0 or u_pnl <= -4.0):
            dragging.append(s)
    # Sort worst first
    dragging.sort(key=lambda s: (s.get('pnl') or 0.0))

    investigation_summary = {
        'dragging_segments_count': len(dragging),
        'segments_investigated': [],
        'hypotheses_deployed': 0,
        'hypotheses_rejected': 0,
    }

    # Run R&D repair & Autonomous Capability Evolution on dragging segments (up to 5)
    agent = SupervisedQuantRDAgent(conn)
    from .autonomous_capability_orchestrator import AutonomousCapabilityOrchestrator
    orch = AutonomousCapabilityOrchestrator(conn)

    for seg in dragging[:5]:
        seg_key = seg.get('segment')
        append_rd_log(
            'TARGET_REPAIR_DISPATCH',
            f"Autonomous AI active positive turnaround dispatched for dragging segment {seg_key} (PnL: ${seg.get('pnl', 0):.2f}, ROI: {seg.get('roi_pct', 0):.1f}%)."
        )
        try:
            summary = agent.run_autonomous_cycle(target_segment=seg_key)
            evo_res = orch.run_supervised_evolution_cycle(target_segment=seg_key)
            investigation_summary['segments_investigated'].append({
                'segment': seg_key,
                'pnl': seg.get('pnl'),
                'roi_pct': seg.get('roi_pct'),
                'cycle_id': summary.cycle_id,
                'hypotheses_passed': summary.candidates_passed,
                'hypotheses_rejected': summary.candidates_rejected,
                'evolution_cycle_id': evo_res.cycle_id,
                'capability_plans_enacted': evo_res.capability_plans_enacted,
                'turnaround_mandate': 'Mandatory conversion to positive alpha'
            })
            investigation_summary['hypotheses_deployed'] += summary.candidates_passed
            investigation_summary['hypotheses_rejected'] += summary.candidates_rejected
        except Exception as e:
            append_rd_log('TARGET_REPAIR_ERROR', f"Error in active positive turnaround for {seg_key}: {e}", level='ERROR')

    # Pillar 2: Mandatory Volume-Deficit Expansion (User directive 2026-10-14: R05)
    volume_gap = evaluation.get('gaps', {}).get('volume_gap', 0)
    if volume_gap > 0 or evaluation.get('status') == 'TARGET_SHORTFALL':
        try:
            from .shadow_market_incubator import ShadowMarketIncubator
            incubator = ShadowMarketIncubator(data_dir)
            new_graduations = incubator.trigger_volume_deficit_expansion(daily_shortfall=volume_gap)
            if new_graduations:
                append_rd_log(
                    'VOLUME_DEFICIT_EXPANSION',
                    f"Volume gap of {volume_gap} detected. Autonomously graduated {len(new_graduations)} profitable market families to fulfill 100+ volume mandate: "
                    f"{[g['segment_key'] for g in new_graduations]}",
                    level='INFO'
                )
                investigation_summary['graduated_markets_expanded'] = [g['segment_key'] for g in new_graduations]
        except Exception as exc:
            append_rd_log('VOLUME_EXPANSION_ERROR', f"Error in volume deficit expansion: {exc}", level='ERROR')

    return investigation_summary


def run_target_self_evaluation(store, data_dir: Path, now: Optional[datetime] = None, force_investigation: bool = False) -> Dict[str, Any]:
    """Full scheduled entry point for target self-evaluation."""
    now = now or datetime.now(timezone.utc)
    with store.db() as conn:
        evaluation = evaluate_target_metrics(conn, now)

        investigation_result = None
        if evaluation['status'] == 'TARGET_SHORTFALL' or force_investigation:
            investigation_result = execute_target_investigation_and_repair(conn, evaluation, data_dir)
            evaluation['investigation'] = investigation_result

        # Save durable records in data/research
        research_dir = data_dir / 'research'
        research_dir.mkdir(parents=True, exist_ok=True)

        latest_path = research_dir / 'target_evaluation_latest.json'
        atomic_json(latest_path, evaluation)

        history_path = research_dir / 'target_evaluations.jsonl'
        try:
            with history_path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(evaluation) + '\n')
            # Bounded rotation: prune if larger than 400 KB
            if history_path.stat().st_size > 400_000:
                lines = history_path.read_text(encoding='utf-8').strip().split('\n')
                if len(lines) > 1000:
                    history_path.write_text('\n'.join(lines[-500:]) + '\n', encoding='utf-8')
        except Exception:
            pass

        return evaluation
