"""Quantitative timing evaluation for bet recommendations (>48h horizon).

Evaluates whether early betting (>48h out) has high alpha/CLV urgency (BET NOW)
or whether line volatility/uncertainty suggests waiting for the 48h window (WAIT).
"""
from datetime import datetime, timezone
import math
from .domain import parse_time


def evaluate_timing(tip, now=None):
    """Evaluate timing urgency for a tip.

    Returns a dict with:
      - action: 'BET_NOW' | 'WAIT' | 'IMMEDIATE' (for <= 48h)
      - label: 'BET NOW' | 'WAIT' | 'READY'
      - hours_to_start: float
      - urgency: 'high' | 'normal' | 'low'
      - reason: str (English)
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif isinstance(now, str):
        now = parse_time(now)

    start_str = tip.get('start')
    if not start_str:
        return {
            'action': 'IMMEDIATE',
            'label': 'READY',
            'hours_to_start': 0.0,
            'urgency': 'normal',
            'reason': 'Immediate execution window',
        }

    start_dt = parse_time(start_str)
    hours = (start_dt - now).total_seconds() / 3600.0

    if hours <= 48.0:
        return {
            'action': 'IMMEDIATE',
            'label': 'READY',
            'hours_to_start': round(max(0.0, hours), 1),
            'urgency': 'normal',
            'reason': 'Within standard 48h execution window',
        }

    # Horizon is > 48h
    market = str(tip.get('market') or '').lower()
    selection = str(tip.get('selection') or '').lower()
    side = str(tip.get('side') or '').lower()
    ev = float(tip.get('expected_roi') or 0.0)
    stress_ev = float(tip.get('stress_roi') or 0.0)
    odds = float(tip.get('reference_odds') or 0.0)
    p = float(tip.get('probability') or 0.0)
    scope = str(tip.get('model_scope') or 'full_game').lower()

    # 1. Check WAIT conditions first (vetoes where waiting is mathematically superior)
    # Totals Under: Public money systematically pushes game totals up closer to game time
    if 'totals' in market and ('under' in selection or side == 'under'):
        return {
            'action': 'WAIT',
            'label': 'WAIT',
            'hours_to_start': round(hours, 1),
            'urgency': 'low',
            'reason': 'Totals Under market: public volume often pushes lines higher closer to game time, offering better lines later.',
        }

    # Non-full-game / derivative period markets > 48h: Low early liquidity and wide spreads
    if scope != 'full_game':
        return {
            'action': 'WAIT',
            'label': 'WAIT',
            'hours_to_start': round(hours, 1),
            'urgency': 'low',
            'reason': 'Derivative / period market: early liquidity is low; odds and lines stabilize closer to match time.',
        }

    # 2. Check BET_NOW conditions (Alpha triggers where early capture is mandatory)
    # High EV or high stress EV: early sharp line mispricing
    if ev >= 0.045 or stress_ev >= 0.02:
        return {
            'action': 'BET_NOW',
            'label': 'BET NOW',
            'hours_to_start': round(hours, 1),
            'urgency': 'high',
            'reason': f'High early value (EV {ev:+.1%}): bookmakers frequently shorten sharp early lines; lock in price early.',
        }

    # Value Underdog (odds >= 2.00 with positive EV >= 1.5%)
    if odds >= 2.00 and ev >= 0.015:
        return {
            'action': 'BET_NOW',
            'label': 'BET NOW',
            'hours_to_start': round(hours, 1),
            'urgency': 'high',
            'reason': f'Value underdog ({odds:.2f} odds, EV {ev:+.1%}): high alpha capture before market syndicates adjust prices.',
        }

    # Solid Core Favorite / Spread (p >= 0.55 in moneyline/spreads with good edge)
    if p >= 0.55 and any(m in market for m in ('moneyline', 'spreads', 'three_way', '1x2')) and ev >= 0.02:
        return {
            'action': 'BET_NOW',
            'label': 'BET NOW',
            'hours_to_start': round(hours, 1),
            'urgency': 'high',
            'reason': f'Strong core favorite (Win Prob {p:.1%}, EV {ev:+.1%}): favorite lines typically shorten early.',
        }

    # Marginal edge: < 3.5% EV and stress EV <= 0.005 for low/moderate odds
    if ev < 0.035 and stress_ev <= 0.005:
        return {
            'action': 'WAIT',
            'label': 'WAIT',
            'hours_to_start': round(hours, 1),
            'urgency': 'low',
            'reason': 'Marginal edge (<3.5% EV): wait for confirmed lineups and team news within 48h window.',
        }

    # Default for remaining >48h bets: WAIT (monitor until closer)
    return {
        'action': 'WAIT',
        'label': 'WAIT',
        'hours_to_start': round(hours, 1),
        'urgency': 'low',
        'reason': 'Moderate early edge: monitor price development until the 48-hour window.',
    }
