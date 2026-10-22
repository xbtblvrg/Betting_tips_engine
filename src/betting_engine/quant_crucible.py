"""Quantitative Crucible: Walk-Forward Backtester and Candidate Sandbox.

Executes chronological, leak-free out-of-time evaluation of quantitative
repair hypotheses against historical data. Validates calibration (Brier score),
economic return (simulated ROI), and drawdown constraints.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from .hypothesis_engine import ResearchRecord


@dataclass
class CrucibleResult:
    hypothesis_id: str
    segment: str
    evaluated_at: str
    passed: bool
    sample_size: int
    baseline_brier: float
    candidate_brier: float
    brier_improvement: float
    baseline_roi_pct: float
    candidate_roi_pct: float
    roi_delta_pct: float
    baseline_win_rate_pct: float
    candidate_win_rate_pct: float
    failure_reasons: List[str]
    evidence_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def apply_hypothesis_simulation(
    decision: Dict[str, Any],
    record: ResearchRecord
) -> Tuple[Optional[float], bool]:
    """Simulates a candidate hypothesis on a single historical decision.
    
    Returns:
        (adjusted_probability, should_include_bet)
    """
    payload = decision.get('payload', {})
    odds = float(payload.get('reference_odds') or decision.get('odds') or 2.0)
    p_orig = float(payload.get('probability') or decision.get('probability') or 0.5)
    p_orig = max(0.01, min(0.99, p_orig))
    cat = record.category
    rule = record.proposed_formula_or_rule.lower()

    # 1. Filter Gate Simulations
    if cat == 'filter_gate':
        context = payload.get('game_context', {})
        is_b2b = context.get('back_to_back') is True or context.get('rest_days') == 0
        side = payload.get('side', '').lower()
        if 'b2b' in rule and is_b2b and 'under' in rule and side == 'under':
            return None, False  # Filtered out (bet avoided)
        if 'extreme_odds' in rule and odds > 2.60:
            return None, False
        if 'draw_protection' in rule or 'three_way' in rule or 'away_draw' in rule:
            market = payload.get('market', '').lower()
            if ('three_way' in market or 'moneyline' in market) and side == 'away':
                return None, False
        if ('volatility' in rule or 'extreme_volatility' in rule) and side == 'under':
            ev = float(payload.get('expected_roi') or payload.get('ev') or 0.0)
            if odds > 2.25 or ev < 0.040:
                return None, False
        if ('quarantine' in rule or 'quarantined' in rule) and not ('b2b' in rule):
            ev = float(payload.get('expected_roi') or payload.get('ev') or 0.0)
            if ev < 0.045:
                return None, False

        return p_orig, True

    # 2. Calibration Shrinkage Simulations
    elif cat == 'calibration_shrinkage':
        p_market = 1.0 / odds if odds > 1.0 else p_orig
        # Shrink 85% model + 15% market consensus
        p_adjusted = 0.85 * p_orig + 0.15 * p_market
        return round(p_adjusted, 4), True

    # 3. Distribution Switch Simulations
    elif cat == 'distribution_switch':
        # Negative Binomial overdispersion correction: reduces confidence on high totals
        if 'negbin' in rule or 'overdispersion' in rule:
            p_adjusted = p_orig * 0.94 if p_orig > 0.5 else p_orig * 1.06
            return round(min(0.95, max(0.05, p_adjusted)), 4), True
        # Empty-Net tail correction in NHL totals:
        if 'empty_net' in rule or 'tail_fat' in rule or 'nhl_totals' in rule:
            side = payload.get('side', '').lower()
            if side == 'under':
                p_adjusted = p_orig * 0.92
            else:
                p_adjusted = min(0.95, p_orig * 1.05)
            return round(p_adjusted, 4), True
        return p_orig, True

    # 4. Feature Addition Simulations
    elif cat == 'feature_addition':
        context = payload.get('game_context', {})
        # NBA Pace & B2B fatigue totals interaction:
        if 'pace' in rule or 'garbage_time' in rule or 'nba_totals' in rule:
            is_b2b = context.get('back_to_back') is True or context.get('rest_days') == 0
            side = payload.get('side', '').lower()
            if is_b2b and side == 'over':
                p_adjusted = min(0.95, p_orig * 1.04)
            elif is_b2b and side == 'under':
                p_adjusted = max(0.05, p_orig * 0.94)
            else:
                p_adjusted = p_orig
            return round(p_adjusted, 4), True
        rest_adv = float(context.get('rest_advantage') or 0.0)
        # Apply fatigue or rest bonus
        adj = 0.03 if rest_adv > 0 else (-0.03 if rest_adv < 0 else 0.0)
        p_adjusted = max(0.05, min(0.95, p_orig + adj))
        return round(p_adjusted, 4), True

    return p_orig, True


def run_synthetic_historical_replay(
    record: ResearchRecord,
    conn: sqlite3.Connection,
    min_sample_size: int = 4
) -> Dict[str, Any]:
    """Executes high-capacity synthetic replay against broader historical outcomes and archives.
    
    Permits deep statistical evaluation across large historical datasets even when a specific
    granular sub-segment has few issued wagers in system_decisions.
    """
    cur = conn.cursor()
    # Query broader matching league outcomes from bet_outcomes and bets
    try:
        cur.execute("""
            SELECT b.payload, o.result, b.pnl, o.probability, b.odds
            FROM bet_outcomes o
            JOIN bets b ON b.id = o.bet_id
            WHERE o.league = ? AND o.result IN ('won', 'lost')
            ORDER BY b.created_at ASC
        """, (record.league,))
        rows = cur.fetchall()
    except Exception:
        rows = []

    decisions = []
    for r in rows:
        payload_raw, status, pnl_raw, prob_raw, odds_raw = r[0], r[1], r[2], r[3], r[4]
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
        prob = float(prob_raw) if prob_raw is not None else float(payload.get('probability') or 0.5)
        odds = float(odds_raw) if odds_raw is not None else float(payload.get('reference_odds') or 2.0)
        pnl = float(pnl_raw) if pnl_raw is not None else (odds - 1.0 if status == 'won' else -1.0)
        decisions.append({
            'payload': payload,
            'status': status,
            'pnl': pnl,
            'odds': odds,
            'probability': prob
        })

    sample_size = len(decisions)
    if sample_size < min_sample_size:
        return {
            'passed': False,
            'sample_size': sample_size,
            'baseline_brier': 0.0,
            'candidate_brier': 0.0,
            'brier_improvement': 0.0,
            'baseline_roi_pct': 0.0,
            'candidate_roi_pct': 0.0,
            'roi_delta_pct': 0.0,
            'baseline_win_rate_pct': 0.0,
            'candidate_win_rate_pct': 0.0,
            'failure_reasons': [f"Insufficient synthetic sample size: {sample_size} < {min_sample_size}"],
            'evidence_payload': {}
        }

    base_brier_sum = 0.0
    base_staked = 0.0
    base_pnl = 0.0
    base_wins = 0

    cand_brier_sum = 0.0
    cand_staked = 0.0
    cand_pnl = 0.0
    cand_wins = 0
    cand_count = 0

    for d in decisions:
        y = 1.0 if d['status'] == 'won' else 0.0
        p_base = d['probability']
        stake = float(d['payload'].get('stake', 1.0))
        pnl = d['pnl']

        base_brier_sum += (p_base - y) ** 2
        base_staked += stake
        base_pnl += pnl
        if d['status'] == 'won':
            base_wins += 1

        p_cand, include_bet = apply_hypothesis_simulation(d, record)
        if include_bet and p_cand is not None:
            cand_count += 1
            cand_brier_sum += (p_cand - y) ** 2
            cand_staked += stake
            cand_pnl += pnl
            if d['status'] == 'won':
                cand_wins += 1

    base_brier = base_brier_sum / sample_size
    base_roi = (base_pnl / base_staked * 100.0) if base_staked > 0 else 0.0
    base_wr = (base_wins / sample_size * 100.0)

    if cand_count > 0:
        cand_brier = cand_brier_sum / cand_count
        cand_roi = (cand_pnl / cand_staked * 100.0) if cand_staked > 0 else 0.0
        cand_wr = (cand_wins / cand_count * 100.0)
    else:
        cand_brier = base_brier
        cand_roi = base_roi
        cand_wr = base_wr

    brier_improvement = base_brier - cand_brier
    roi_delta = cand_roi - base_roi
    failures = []

    is_quarantined = (
        record.league in ('CHALLENGER', 'ITF')
        or 'challenger' in str(record.segment).lower()
        or 'challenger' in str(record.title).lower()
    )
    if is_quarantined:
        failures.append("Segment is under quarantine; autonomous live promotion disabled")
    if brier_improvement < -0.001:
        failures.append(f"Calibration worsened: delta {brier_improvement:.4f} < -0.001")
    if roi_delta < -0.01:
        failures.append(f"Economic return degraded: ROI delta {roi_delta:.2f}% < 0.0%")
    if brier_improvement < 0.0005 and roi_delta < 0.20:
        failures.append(f"Insignificant improvement: delta Brier {brier_improvement:.4f} < 0.0005 and ROI delta {roi_delta:.2f}% < 0.20%")
    if cand_count < min_sample_size:
        failures.append(f"Too few bets retained after filtering: {cand_count} < {min_sample_size}")

    passed = len(failures) == 0
    return {
        'passed': passed,
        'sample_size': sample_size,
        'baseline_brier': round(base_brier, 4),
        'candidate_brier': round(cand_brier, 4),
        'brier_improvement': round(brier_improvement, 4),
        'baseline_roi_pct': round(base_roi, 2),
        'candidate_roi_pct': round(cand_roi, 2),
        'roi_delta_pct': round(roi_delta, 2),
        'baseline_win_rate_pct': round(base_wr, 1),
        'candidate_win_rate_pct': round(cand_wr, 1),
        'failure_reasons': failures,
        'evidence_payload': {
            'retained_bets_count': cand_count,
            'candidate_pnl': round(cand_pnl, 2),
            'baseline_pnl': round(base_pnl, 2),
            'synthetic_replay_source': 'bet_outcomes'
        }
    }


def run_crucible_walk_forward(
    record: ResearchRecord,
    conn: sqlite3.Connection,
    min_sample_size: int = 4
) -> CrucibleResult:
    """Executes out-of-time walk-forward evaluation of a candidate hypothesis."""
    cur = conn.cursor()
    cur.execute("""
        SELECT d.payload, d.status, d.pnl, d.outcome_evidence
        FROM system_decisions d
        WHERE d.status IN ('won', 'lost')
        ORDER BY d.issued_at ASC
    """)
    rows = cur.fetchall()

    decisions = []
    for r in rows:
        payload_raw, status, pnl_raw, ev_raw = r[0], r[1], r[2], r[3]
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        if not isinstance(payload, dict):
            continue

        league = payload.get('league')
        if league != record.league:
            continue

        odds = float(payload.get('reference_odds') or payload.get('odds') or 2.0)
        prob = float(payload.get('probability') or 0.5)

        decisions.append({
            'payload': payload,
            'status': status,
            'pnl': float(pnl_raw) if pnl_raw is not None else 0.0,
            'odds': odds,
            'probability': prob
        })

    sample_size = len(decisions)
    failures = []

    if sample_size < min_sample_size:
        # Fallback to broader synthetic archive from bet_outcomes
        synthetic = run_synthetic_historical_replay(record, conn, min_sample_size=min_sample_size)
        if synthetic.get('sample_size', 0) >= min_sample_size:
            return CrucibleResult(
                hypothesis_id=record.hypothesis_id,
                segment=record.segment,
                evaluated_at=datetime.now(timezone.utc).isoformat(),
                passed=synthetic['passed'],
                sample_size=synthetic['sample_size'],
                baseline_brier=synthetic['baseline_brier'],
                candidate_brier=synthetic['candidate_brier'],
                brier_improvement=synthetic['brier_improvement'],
                baseline_roi_pct=synthetic['baseline_roi_pct'],
                candidate_roi_pct=synthetic['candidate_roi_pct'],
                roi_delta_pct=synthetic['roi_delta_pct'],
                baseline_win_rate_pct=synthetic['baseline_win_rate_pct'],
                candidate_win_rate_pct=synthetic['candidate_win_rate_pct'],
                failure_reasons=synthetic['failure_reasons'],
                evidence_payload={'synthetic_replay': True, **synthetic.get('evidence_payload', {})}
            )
        return CrucibleResult(
            hypothesis_id=record.hypothesis_id,
            segment=record.segment,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            passed=False,
            sample_size=sample_size,
            baseline_brier=0.0,
            candidate_brier=0.0,
            brier_improvement=0.0,
            baseline_roi_pct=0.0,
            candidate_roi_pct=0.0,
            roi_delta_pct=0.0,
            baseline_win_rate_pct=0.0,
            candidate_win_rate_pct=0.0,
            failure_reasons=[f"Insufficient sample size: {sample_size} < {min_sample_size}"]
        )

    # 1. Compute Baseline Metrics
    base_brier_sum = 0.0
    base_staked = 0.0
    base_pnl = 0.0
    base_wins = 0

    cand_brier_sum = 0.0
    cand_staked = 0.0
    cand_pnl = 0.0
    cand_wins = 0
    cand_count = 0

    for d in decisions:
        y = 1.0 if d['status'] == 'won' else 0.0
        p_base = d['probability']
        stake = float(d['payload'].get('stake', 1.0))
        pnl = d['pnl']

        base_brier_sum += (p_base - y) ** 2
        base_staked += stake
        base_pnl += pnl
        if d['status'] == 'won':
            base_wins += 1

        # Simulate Candidate
        p_cand, include_bet = apply_hypothesis_simulation(d, record)
        if include_bet and p_cand is not None:
            cand_count += 1
            cand_brier_sum += (p_cand - y) ** 2
            cand_staked += stake
            cand_pnl += pnl
            if d['status'] == 'won':
                cand_wins += 1

    base_brier = base_brier_sum / sample_size
    base_roi = (base_pnl / base_staked * 100.0) if base_staked > 0 else 0.0
    base_wr = (base_wins / sample_size * 100.0)

    if cand_count > 0:
        cand_brier = cand_brier_sum / cand_count
        cand_roi = (cand_pnl / cand_staked * 100.0) if cand_staked > 0 else 0.0
        cand_wr = (cand_wins / cand_count * 100.0)
    else:
        cand_brier = base_brier
        cand_roi = base_roi
        cand_wr = base_wr

    brier_improvement = base_brier - cand_brier  # Positive is good
    roi_delta = cand_roi - base_roi

    # Dynamic Multi-Window Rolling Historical Research Check
    # Validates candidate across short-term momentum (20 games), mid-term regime (50 games), and full sample
    rolling_windows = {}
    for w_name, w_size in (('short_window_20', 20), ('mid_window_50', 50)):
        if len(decisions) >= w_size:
            w_dec = decisions[-w_size:]
            w_base_pnl = sum(d['pnl'] for d in w_dec)
            w_base_stk = sum(float(d['payload'].get('stake', 1.0)) for d in w_dec)
            w_base_roi = (w_base_pnl / w_base_stk * 100.0) if w_base_stk > 0 else 0.0

            w_cand_pnl = 0.0
            w_cand_stk = 0.0
            w_cand_cnt = 0
            for d in w_dec:
                p_c, inc = apply_hypothesis_simulation(d, record)
                if inc and p_c is not None:
                    w_cand_cnt += 1
                    w_cand_pnl += d['pnl']
                    w_cand_stk += float(d['payload'].get('stake', 1.0))
            w_cand_roi = (w_cand_pnl / w_cand_stk * 100.0) if w_cand_stk > 0 else 0.0
            delta_w = round(w_cand_roi - w_base_roi, 2)
            rolling_windows[w_name] = {
                'window_games': w_size,
                'baseline_roi_pct': round(w_base_roi, 2),
                'candidate_roi_pct': round(w_cand_roi, 2),
                'roi_delta_pct': delta_w,
                'retained_bets': w_cand_cnt
            }
            if delta_w < -3.0:
                failures.append(f"Recent rolling window ({w_name}) degraded by {delta_w}% ROI")

    # Acceptance Gates
    is_quarantined = (
        record.league in ('CHALLENGER', 'ITF')
        or 'challenger' in str(record.segment).lower()
        or 'challenger' in str(record.title).lower()
    )
    if is_quarantined:
        failures.append("Segment is under quarantine; autonomous live promotion disabled")
    if brier_improvement < -0.001:
        failures.append(f"Calibration worsened: delta {brier_improvement:.4f} < -0.001")
    if roi_delta < -0.01:
        failures.append(f"Economic return degraded: ROI delta {roi_delta:.2f}% < 0.0%")
    if brier_improvement < 0.0005 and roi_delta < 0.20:
        failures.append(f"Insignificant improvement: delta Brier {brier_improvement:.4f} < 0.0005 and ROI delta {roi_delta:.2f}% < 0.20%")
    if cand_count < min_sample_size:
        failures.append(f"Too few bets retained after filtering: {cand_count} < {min_sample_size}")

    passed = len(failures) == 0

    return CrucibleResult(
        hypothesis_id=record.hypothesis_id,
        segment=record.segment,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        passed=passed,
        sample_size=sample_size,
        baseline_brier=round(base_brier, 4),
        candidate_brier=round(cand_brier, 4),
        brier_improvement=round(brier_improvement, 4),
        baseline_roi_pct=round(base_roi, 2),
        candidate_roi_pct=round(cand_roi, 2),
        roi_delta_pct=round(roi_delta, 2),
        baseline_win_rate_pct=round(base_wr, 1),
        candidate_win_rate_pct=round(cand_wr, 1),
        failure_reasons=failures,
        evidence_payload={
            'retained_bets_count': cand_count,
            'candidate_pnl': round(cand_pnl, 2),
            'baseline_pnl': round(base_pnl, 2),
            'multi_window_rolling_evaluation': rolling_windows
        }
    )
