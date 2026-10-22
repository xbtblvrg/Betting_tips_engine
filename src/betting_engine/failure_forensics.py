"""Forensic Failure Attribution Engine.

Analyzes settled bets in underperforming segments to decompose losses into
underlying failure modes: Closing Line Value (CLV) drift, tail variance (OT,
empty net, extra innings), schedule/fatigue blindspots, and calibration bias.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
import sqlite3
from typing import Any, Dict, List, Optional

from .contracts import allocation_family, split_market
from .segment_scoreboard import segment_key_from_payload


@dataclass
class ForensicDossier:
    segment: str
    league: str
    scope: str
    family: str
    evaluated_at: str
    total_bets: int
    losses: int
    wins: int
    pushes: int
    win_rate_pct: float
    total_staked: float
    total_pnl: float
    roi_pct: float
    primary_failure_mode: str
    confidence_score: float
    failure_breakdown: Dict[str, Any]
    actionable_remedy: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def analyze_clv(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Measures closing line value and price movement against our wagers."""
    drift_sum = 0.0
    drift_count = 0
    adverse_moves = 0
    favorable_moves = 0

    for d in decisions:
        payload = d.get('payload', {})
        placed_odds = float(payload.get('reference_odds') or d.get('odds') or 1.0)
        # Closing odds or current market price if available in evidence/payload
        evidence = d.get('evidence') or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = {}
        
        closing_odds = float(evidence.get('closing_odds') or payload.get('closing_odds') or placed_odds)
        if placed_odds > 1.0 and closing_odds > 1.0:
            drift = (placed_odds - closing_odds) / closing_odds
            drift_sum += drift
            drift_count += 1
            if closing_odds > placed_odds * 1.02:
                adverse_moves += 1  # Market lengthened, indicating smart money was on other side
            elif placed_odds > closing_odds * 1.02:
                favorable_moves += 1  # We beat the closing line

    avg_drift_pct = (drift_sum / drift_count * 100.0) if drift_count > 0 else 0.0
    adverse_rate = (adverse_moves / drift_count) if drift_count > 0 else 0.0

    return {
        'avg_clv_drift_pct': round(avg_drift_pct, 2),
        'adverse_moves_count': adverse_moves,
        'favorable_moves_count': favorable_moves,
        'adverse_move_rate': round(adverse_rate, 3),
        'clv_deficit_detected': adverse_rate > 0.40 or avg_drift_pct < -2.5
    }


def analyze_tail_variance(decisions: List[Dict[str, Any]], league: str) -> Dict[str, Any]:
    """Detects external variance drivers: overtime, extra innings, empty-net goals."""
    ot_events = 0
    close_losses = 0
    total_losses = 0

    for d in decisions:
        if d.get('status') != 'lost':
            continue
        total_losses += 1
        evidence = d.get('evidence') or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except Exception:
                evidence = {}
        
        # Check overtime / extra periods in sports evidence
        is_ot = (
            evidence.get('overtime') is True
            or evidence.get('extra_innings') is True
            or 'ot' in str(evidence.get('settlement_note', '')).lower()
            or 'overtime' in str(evidence.get('settlement_note', '')).lower()
            or 'penalties' in str(evidence.get('settlement_note', '')).lower()
        )
        if is_ot:
            ot_events += 1

        # Check margin of loss (e.g. 1-goal / 1-point difference or half-point total beat)
        margin = evidence.get('margin')
        if margin is not None and abs(float(margin)) <= 1.0:
            close_losses += 1

    ot_rate = (ot_events / total_losses) if total_losses > 0 else 0.0
    close_rate = (close_losses / total_losses) if total_losses > 0 else 0.0

    return {
        'overtime_losses': ot_events,
        'close_margin_losses': close_losses,
        'tail_variance_rate': round(max(ot_rate, close_rate), 3),
        'tail_variance_detected': ot_rate >= 0.30 or close_rate >= 0.50
    }


def analyze_fatigue_and_schedule(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyzes schedule density, back-to-back games, and rest disparities."""
    b2b_losses = 0
    b2b_total = 0

    for d in decisions:
        payload = d.get('payload', {})
        context = payload.get('game_context', {})
        if not isinstance(context, dict):
            context = {}

        is_b2b = (
            context.get('back_to_back') is True
            or context.get('rest_days') == 0
            or context.get('rest_advantage') is not None and context.get('rest_advantage') < 0
        )
        if is_b2b:
            b2b_total += 1
            if d.get('status') == 'lost':
                b2b_losses += 1

    b2b_loss_rate = (b2b_losses / b2b_total) if b2b_total > 0 else 0.0

    return {
        'b2b_bets_count': b2b_total,
        'b2b_losses_count': b2b_losses,
        'b2b_loss_rate': round(b2b_loss_rate, 3),
        'fatigue_vulnerability_detected': b2b_total >= 2 and b2b_loss_rate >= 0.65
    }


def analyze_calibration(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes empirical calibration, Brier score, and overconfidence bias."""
    brier_sum = 0.0
    pred_sum = 0.0
    wins = 0
    total = len(decisions)

    if total == 0:
        return {'brier_score': 0.0, 'calibration_bias': 0.0, 'overconfidence_detected': False}

    for d in decisions:
        payload = d.get('payload', {})
        p = float(payload.get('probability') or d.get('probability') or 0.5)
        p = max(0.01, min(0.99, p))
        y = 1.0 if d.get('status') == 'won' else 0.0
        if d.get('status') == 'won':
            wins += 1

        brier_sum += (p - y) ** 2
        pred_sum += p

    brier = brier_sum / total
    avg_pred = pred_sum / total
    actual_win_rate = wins / total
    bias = avg_pred - actual_win_rate  # Positive means model was systematically overconfident

    return {
        'brier_score': round(brier, 4),
        'avg_model_prob': round(avg_pred, 3),
        'actual_win_rate': round(actual_win_rate, 3),
        'calibration_bias': round(bias, 3),
        'overconfidence_detected': bias > 0.08 and total >= 4
    }


def analyze_feature_anomalies(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Detects micro-level feature and prior defects:
    1. Unrated 0.50 default prior trap (p approx 0.50 on odds > 2.15).
    2. Phantom high-EV trap (EV > 30% on high odds >= 2.50).
    3. Extreme underdog vig bleed.
    """
    unrated_prior_losses = 0
    unrated_prior_total = 0
    phantom_ev_losses = 0
    phantom_ev_total = 0

    for d in decisions:
        payload = d.get('payload', {})
        p = float(payload.get('probability') or d.get('probability') or 0.5)
        odds = float(payload.get('reference_odds') or d.get('odds') or 2.0)
        ev = float(payload.get('expected_roi') or payload.get('ev') or (p * odds - 1.0))
        is_loss = d.get('status') == 'lost'

        # Flat 0.50 prior on underdog odds > 2.15
        if abs(p - 0.50) < 0.005 and odds > 2.15:
            unrated_prior_total += 1
            if is_loss:
                unrated_prior_losses += 1

        # Phantom EV on longshots (odds >= 2.50 with EV >= 30%)
        if odds >= 2.50 and ev >= 0.30:
            phantom_ev_total += 1
            if is_loss:
                phantom_ev_losses += 1

    unrated_prior_detected = (unrated_prior_total >= 2 and (unrated_prior_losses / unrated_prior_total) >= 0.60)
    phantom_ev_detected = (phantom_ev_total >= 2 and (phantom_ev_losses / phantom_ev_total) >= 0.60)

    return {
        'unrated_prior_bets': unrated_prior_total,
        'unrated_prior_losses': unrated_prior_losses,
        'unrated_prior_detected': unrated_prior_detected,
        'phantom_ev_bets': phantom_ev_total,
        'phantom_ev_losses': phantom_ev_losses,
        'phantom_ev_detected': phantom_ev_detected,
    }


def analyze_odds_brackets(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Breaks down performance by decimal odds bracket."""
    brackets = {
        'favorites': {'count': 0, 'wins': 0, 'pnl': 0.0, 'stake': 0.0},
        'balanced': {'count': 0, 'wins': 0, 'pnl': 0.0, 'stake': 0.0},
        'longshots': {'count': 0, 'wins': 0, 'pnl': 0.0, 'stake': 0.0}
    }

    for d in decisions:
        payload = d.get('payload', {})
        odds = float(payload.get('reference_odds') or d.get('odds') or 2.0)
        stake = float(payload.get('stake', 1.0))
        pnl = float(d.get('pnl', 0.0))
        status = d.get('status')

        if odds < 1.70:
            b = brackets['favorites']
        elif odds <= 2.30:
            b = brackets['balanced']
        else:
            b = brackets['longshots']

        b['count'] += 1
        b['stake'] += stake
        b['pnl'] += pnl
        if status == 'won':
            b['wins'] += 1

    summary = {}
    for name, b in brackets.items():
        cnt = b['count']
        roi = (b['pnl'] / b['stake'] * 100.0) if b['stake'] > 0 else 0.0
        wr = (b['wins'] / cnt * 100.0) if cnt > 0 else 0.0
        summary[name] = {
            'count': cnt,
            'roi_pct': round(roi, 1),
            'win_rate_pct': round(wr, 1),
            'pnl': round(b['pnl'], 2)
        }

    longshot_drag = brackets['longshots']['count'] >= 2 and brackets['longshots']['pnl'] < -2.0
    return {
        'brackets': summary,
        'longshot_bias_detected': longshot_drag
    }


def run_forensic_investigation(
    segment_key: str,
    conn: sqlite3.Connection,
    limit: int = 50
) -> Optional[ForensicDossier]:
    """Performs deep forensic attribution on an underperforming segment."""
    cur = conn.cursor()
    league = segment_key.split('|')[0] if '|' in segment_key else None
    if league:
        cur.execute("""
            SELECT d.payload, d.status, d.pnl, d.outcome_evidence
            FROM system_decisions d
            WHERE d.status IN ('won', 'lost', 'push')
              AND json_extract(d.payload, '$.league') = ?
            ORDER BY d.issued_at DESC
        """, (league,))
    else:
        cur.execute("""
            SELECT d.payload, d.status, d.pnl, d.outcome_evidence
            FROM system_decisions d
            WHERE d.status IN ('won', 'lost', 'push')
            ORDER BY d.issued_at DESC
        """)
    rows = cur.fetchall()

    decisions = []
    total_staked = 0.0
    total_pnl = 0.0
    wins = 0
    losses = 0
    pushes = 0

    for r in rows:
        payload_raw, status, pnl_raw, evidence_raw = r[0], r[1], r[2], r[3]
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        if not isinstance(payload, dict):
            continue

        key = segment_key_from_payload(payload)
        if key != segment_key:
            continue

        stake = float(payload.get('stake', 1.0))
        pnl = float(pnl_raw) if pnl_raw is not None else 0.0
        prob = float(payload.get('probability') or 0.5)
        odds = float(payload.get('reference_odds') or payload.get('odds') or 2.0)
        total_staked += stake
        total_pnl += pnl

        if status == 'won':
            wins += 1
        elif status == 'lost':
            losses += 1
        elif status == 'push':
            pushes += 1

        decisions.append({
            'payload': payload,
            'status': status,
            'pnl': pnl,
            'evidence': evidence_raw,
            'probability': prob,
            'odds': odds
        })
        if len(decisions) >= limit:
            break

    total_bets = len(decisions)
    if total_bets < 4:
        return None

    decisive = wins + losses
    win_rate_pct = round((wins / decisive * 100.0) if decisive > 0 else 0.0, 1)
    roi_pct = round((total_pnl / total_staked * 100.0) if total_staked > 0 else 0.0, 2)

    parts = segment_key.split('|')
    league = parts[0] if len(parts) > 0 else 'UNKNOWN'
    scope = parts[1] if len(parts) > 1 else 'full_game'
    family = parts[2] if len(parts) > 2 else 'unknown'

    # Run analytical breakdown
    clv_res = analyze_clv(decisions)
    tail_res = analyze_tail_variance(decisions, league)
    fatigue_res = analyze_fatigue_and_schedule(decisions)
    calib_res = analyze_calibration(decisions)
    bracket_res = analyze_odds_brackets(decisions)
    anomaly_res = analyze_feature_anomalies(decisions)

    # Determine primary failure mode by score
    scores = {
        'UNRATED_PRIOR_DEFECT': 1.25 if anomaly_res['unrated_prior_detected'] else 0.0,
        'PHANTOM_EV_ILLUSION': 1.15 if anomaly_res['phantom_ev_detected'] else 0.0,
        'CALIBRATION_OVERCONFIDENCE': 1.0 if calib_res['overconfidence_detected'] else 0.0,
        'TAIL_VARIANCE_OVERDISPERSION': 1.0 if tail_res['tail_variance_detected'] else 0.0,
        'FATIGUE_SCHEDULE_BLINDSPOT': 0.9 if fatigue_res['fatigue_vulnerability_detected'] else 0.0,
        'CLV_ADVERSE_SELECTION': 0.85 if clv_res['clv_deficit_detected'] else 0.0,
        'LONGSHOT_VIG_EROSION': 0.8 if bracket_res['longshot_bias_detected'] else 0.0
    }

    best_mode, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score < 0.5:
        best_mode = 'UNSPECIFIED_STATISTICAL_NOISE'
        best_score = 0.4

    remedies = {
        'UNRATED_PRIOR_DEFECT': 'Quarantine unrated 0.50 default prior wagers on underdogs (odds > 2.15) and enforce minimum 3-match rating history.',
        'PHANTOM_EV_ILLUSION': 'Quarantine extreme phantom EV (>30%) on high odds (>=2.50) as uncalibrated model estimation artifact.',
        'CALIBRATION_OVERCONFIDENCE': 'Apply isotonic probability calibration and tighten minimum EV threshold.',
        'TAIL_VARIANCE_OVERDISPERSION': 'Replace Poisson with Negative Binomial dispersion and filter close-line totals.',
        'FATIGUE_SCHEDULE_BLINDSPOT': 'Incorporate rest differential and travel distance penalty into offensive ratings.',
        'CLV_ADVERSE_SELECTION': 'Filter out late bets when odds drift unfavorably (>2% lengthening against our pick).',
        'LONGSHOT_VIG_EROSION': 'Cap decimal odds at 2.35 or apply asymmetric longshot shrinkage.',
        'UNSPECIFIED_STATISTICAL_NOISE': 'Maintain $1.00 capital defense and collect 5 additional settled observations.'
    }

    return ForensicDossier(
        segment=segment_key,
        league=league,
        scope=scope,
        family=family,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        total_bets=total_bets,
        losses=losses,
        wins=wins,
        pushes=pushes,
        win_rate_pct=win_rate_pct,
        total_staked=round(total_staked, 2),
        total_pnl=round(total_pnl, 2),
        roi_pct=roi_pct,
        primary_failure_mode=best_mode,
        confidence_score=round(best_score, 2),
        failure_breakdown={
            'clv': clv_res,
            'tail_variance': tail_res,
            'fatigue': fatigue_res,
            'calibration': calib_res,
            'brackets': bracket_res,
            'feature_anomalies': anomaly_res
        },
        actionable_remedy=remedies[best_mode],
        metadata={'sample_depth': total_bets}
    )
