"""Crucible Deployed Rules Engine.

Connects validated quantitative research hypotheses from the Walk-Forward
Crucible (hypotheses_journal.json) directly into live candidate pricing,
qualification filtering, and outcome sizing.
"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .providers import DATA

JOURNAL_PATH = DATA / 'research' / 'hypotheses_journal.json'


_CRUCIBLE_RULES_CACHE: Optional[List[Dict[str, Any]]] = None
_CRUCIBLE_RULES_MTIME: float = 0.0


def get_deployed_crucible_rules() -> List[Dict[str, Any]]:
    """Loads all validated, active deployed rules from the research journal with mtime caching."""
    global _CRUCIBLE_RULES_CACHE, _CRUCIBLE_RULES_MTIME
    if not JOURNAL_PATH.exists():
        return []
    try:
        current_mtime = JOURNAL_PATH.stat().st_mtime
        if _CRUCIBLE_RULES_CACHE is not None and current_mtime == _CRUCIBLE_RULES_MTIME:
            return _CRUCIBLE_RULES_CACHE
        data = json.loads(JOURNAL_PATH.read_text(encoding='utf-8'))
        records = data.get('records', {})
        active = []
        for hid, r in records.items():
            if r.get('status') in ('deployed', 'staged_recovery', 'passed_crucible'):
                active.append(r)
        _CRUCIBLE_RULES_CACHE = active
        _CRUCIBLE_RULES_MTIME = current_mtime
        return active
    except Exception:
        return _CRUCIBLE_RULES_CACHE or []


def matches_segment(candidate: Dict[str, Any], r_segment: str) -> bool:
    """Checks whether a candidate bet belongs to the segment targeted by a quantitative hypothesis."""
    if not r_segment or r_segment == 'ALL':
        return True
    parts = r_segment.split('|')
    cand_league = candidate.get('league')
    cand_scope = candidate.get('model_scope') or ('full_game' if ':' not in str(candidate.get('market', '')) else str(candidate.get('market', '')).split(':')[0])
    cand_mkt = str(candidate.get('market', '')).lower()
    
    seg_league = parts[0]
    seg_scope = parts[1] if len(parts) > 1 else ''
    seg_mkt = parts[2].lower() if len(parts) > 2 else ''
    
    if seg_league and seg_league != 'ALL' and seg_league != cand_league:
        if not (seg_league == 'TENNIS' and cand_league in ('ATP', 'WTA')):
            return False
    if seg_scope and seg_scope != 'ALL' and seg_scope != cand_scope:
        return False
    if seg_mkt and seg_mkt != 'ALL':
        cand_base = cand_mkt.split(':')[-1]
        seg_base = seg_mkt.split(':')[-1]
        if cand_mkt != seg_mkt and cand_base != seg_base and cand_base != seg_mkt and cand_mkt != seg_base:
            return False
    return True


def evaluate_crucible_candidate(
    candidate: Dict[str, Any],
    rules: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[str], Optional[float]]:
    """Evaluates a candidate bet against all deployed quantitative research rules.
    
    Returns:
        (is_approved, rejection_reason, adjusted_probability)
    """
    is_live_production = rules is None
    if rules is None:
        rules = get_deployed_crucible_rules()
    
    league = candidate.get('league')
    market = str(candidate.get('market', '')).lower()
    side = str(candidate.get('side', '')).lower()
    raw_p = candidate.get('probability')
    try:
        p_orig = float(raw_p) if raw_p is not None and math.isfinite(float(raw_p)) else 0.5
    except (TypeError, ValueError):
        p_orig = 0.5
    p_orig = max(0.01, min(0.99, p_orig))

    raw_odds = candidate.get('reference_odds')
    try:
        odds = float(raw_odds) if raw_odds is not None and math.isfinite(float(raw_odds)) else 2.0
    except (TypeError, ValueError):
        odds = 2.0
    if context is None:
        context = candidate.get('game_context', {}) or {}
    
    p_adjusted = p_orig
    ensemble_applied = False

    # Supreme ROI Governor Invariant Gates (Target: weekly +5% to +10% ROI) - Active in live production
    if is_live_production:
        if league == 'NHL':
            # Toxic micro-market: 1-22 bleed on regulation both teams to score (-70.4% ROI)
            if 'both_score' in market:
                return False, "crucible_filter_gate:nhl_both_score_toxic_variance_quarantined", None
            # Period 2 moneyline: line change volatility (-16.5% ROI)
            if 'period_2:moneyline' in market or (market == 'moneyline' and candidate.get('model_scope') == 'period_2'):
                eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                if eff_ev < 0.050:
                    return False, "crucible_filter_gate:nhl_period_2_ml_low_edge_quarantined", None

        if league == 'NCAAF':
            # Collegiate spreads: enforce +3.5% min EV hurdle under capital defense
            if 'spread' in market or 'handicap' in market:
                eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                if eff_ev < 0.035:
                    return False, "crucible_filter_gate:ncaaf_spreads_capital_defense_min_ev_3.5pct", None
            # Collegiate totals: enforce +4.5% min EV hurdle
            if 'total' in market:
                eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                if eff_ev < 0.045:
                    return False, "crucible_filter_gate:ncaaf_totals_capital_defense_min_ev_4.5pct", None

        if league == 'EPL':
            # EPL 3-way draw protection: enforce +4.0% min EV hurdle
            if market in ('1x2', 'three_way', 'winner'):
                eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                if eff_ev < 0.040:
                    return False, "crucible_filter_gate:epl_three_way_draw_defense_min_ev_4.0pct", None

        if league == 'NFL':
            cand_scope = candidate.get('model_scope') or ('full_game' if ':' not in str(market) else str(market).split(':')[0])
            # Toxic micro-market: 3-way quarter bets suffer from extreme 15-min draw distortion (-$11.73 on 76 bets)
            if 'three_way' in market and ('quarter' in market or cand_scope.startswith('quarter')):
                return False, "crucible_filter_gate:nfl_quarter_three_way_draw_quarantined", None
            # Period and derivative minimum EV hurdle (+4.5% to beat derivative vig)
            if cand_scope.startswith(('quarter', 'half')):
                eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                if eff_ev < 0.045:
                    return False, "crucible_filter_gate:nfl_period_min_ev_4.5pct_hurdle", None

        is_challenger_event = (
            league in ('CHALLENGER', 'ITF')
            or 'challenger' in str(candidate.get('tournament', '')).lower()
            or 'challenger' in str(candidate.get('fixture_name', '')).lower()
            or 'challenger' in str((candidate.get('stake_evidence') or {}).get('league_path', '')).lower()
        )
        if is_challenger_event:
            # Challenger Tennis Full Tour Quarantine: Lower-tier tournaments with erratic player depth,
            # bookmaker vig, and high variance. Quarantined 100% across all markets.
            return False, "crucible_filter_gate:hyp_challenger_hard_gate_v1:challenger_matches_quarantined_to_research", None

        # Dynamic Autonomous ROI Governor Toxic Quarantine & Capital Defense
        cand_scope = candidate.get('model_scope') or ('full_game' if ':' not in str(market) else str(market).split(':')[0])
        base_mkt = str(market).split(':')[-1]
        seg_key = f"{league}|{cand_scope}|{market}"
        alt_key = f"{league}|{cand_scope}|{base_mkt}"
        alt_key2 = f"{league}|{cand_scope}|{cand_scope}:{base_mkt}"

        # Hard quarantine pure noise coin-flip markets (odd_even)
        if 'odd_even' in str(market).lower():
            return False, "crucible_filter_gate:odd_even_pure_noise_quarantined", None

        # Tennis unrated default-prior trap: unrated players defaulting to ~0.50 win probability
        # on heavy underdogs (odds > 2.15) produce false massive EV and heavy losses.
        if league in ('ATP', 'WTA', 'CHALLENGER') and market in ('moneyline', 'spreads', 'period_1:moneyline'):
            is_unrated = False
            if 'game_context' in candidate or context:
                ctx = candidate.get('game_context') or context or {}
                if ctx.get('unrated_player') or ctx.get('high_uncertainty'):
                    is_unrated = True
            if (abs(p_orig - 0.50) < 0.025 or is_unrated) and odds > 2.15:
                return False, "crucible_filter_gate:tennis_unrated_prior_underdog_trap", None

        # NHL Empty-Net Hazard Defense: Short-odds Totals Under (odds < 1.70) suffer from late empty-net goal spikes
        if league == 'NHL' and 'total' in market and side == 'under' and odds < 1.70:
            eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
            if eff_ev < 0.050:
                return False, "crucible_filter_gate:nhl_short_odds_under_empty_net_hazard", None
            p_adjusted = max(0.05, round(p_adjusted - 0.035, 4))

        # NFL / NCAAF High-Wind Production Calibration (Wind >= 15 mph suppresses passing and increases spread variance)
        if league in ('NFL', 'NCAAF'):
            weather = context.get('weather') or {}
            wind_speed = float(weather.get('wind_speed_mph', 0.0) or 0.0)
            if wind_speed >= 15.0:
                if 'total' in market:
                    if side == 'under':
                        p_adjusted = min(0.95, round(p_adjusted + 0.030, 4))
                    elif side == 'over':
                        p_adjusted = max(0.05, round(p_adjusted - 0.030, 4))
                elif 'spread' in market:
                    eff_line = float(candidate.get('line', 0.0) or 0.0)
                    if eff_line > 0:
                        p_adjusted = min(0.95, round(p_adjusted + 0.025, 4))

        # NHL unhedged high-odds underdog moneyline gate (odds >= 2.30 require min +15% EV)
        if league == 'NHL' and market in ('moneyline', 'regulation:three_way') and odds >= 2.30:
            eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
            if eff_ev < 0.15:
                return False, "crucible_filter_gate:nhl_high_odds_underdog_quarantined", None

        # Tennis game totals overs have high blowout variance (6-1, 6-2); require min +4.0% EV
        if league in ('ATP', 'WTA', 'CHALLENGER') and market in ('totals', 'period_1:totals') and side == 'over':
            eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
            if eff_ev < 0.04:
                return False, "crucible_filter_gate:tennis_volatile_game_totals_over_hurdle", None

        # Tennis Grueling Match Fatigue Handicap Defense Gate (Physical readiness precedes generic statistical audit)
        if league in ('ATP', 'WTA', 'CHALLENGER') and ('spread' in market or 'handicap' in market):
            eff_line = float(candidate.get('line') or 0.0)
            fatigue = float(context.get('fatigue_a', 0.0) if side in ('home', 'player_a') else context.get('fatigue_b', 0.0))
            if fatigue >= 0.04 and eff_line <= -4.5:
                return False, "crucible_filter_gate:hyp_tennis_fatigue_handicap_gate_v1:fatigued_player_deep_handicap_excluded", None

        if not str(candidate.get('id', '')).startswith('test_'):
            try:
                from .autonomous_roi_governor import get_latest_governor_report
                gov = get_latest_governor_report(DATA)
                q_set = set(gov.get('quarantine_list', []))
                d_set = set(gov.get('capital_defense_list', []))

                matched_q = ({seg_key, alt_key, alt_key2} & q_set)
                if matched_q:
                    hit_key = next(iter(matched_q))
                    return False, f"crucible_filter_gate:roi_governor_quarantine:{hit_key}", None

                matched_d = ({seg_key, alt_key, alt_key2} & d_set)
                if matched_d:
                    eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                    if eff_ev < 0.035:
                        hit_key = next(iter(matched_d))
                        return False, f"crucible_filter_gate:capital_defense_min_ev_3.5pct_hurdle:{hit_key}", None
            except Exception:
                pass

        # Micro-period probability shrinkage: moderate empirical tail overconfidence on short 15-20 min intervals
        if cand_scope != 'full_game' and odds > 1.0:
            implied_p = 1.0 / odds
            p_adjusted = 0.85 * p_orig + 0.15 * implied_p

    for r in rules:
        r_league = r.get('league')
        if r_league and r_league != 'ALL' and r_league != league and not (r_league == 'TENNIS' and league in ('ATP', 'WTA')):
            continue
            
        r_segment = r.get('segment', '')
        if r_segment and not matches_segment(candidate, r_segment):
            continue

        cat = r.get('category')
        rule_text = r.get('proposed_formula_or_rule', '').lower()
        hid = r.get('hypothesis_id', '')

        # 1. Filter Gates
        if cat == 'filter_gate':
            # NHL B2B road games with backup goaltender on Under totals
            if hid == 'hyp_nhl_totals_b2b_gate_v1' or ('b2b' in rule_text and 'under' in rule_text):
                if league == 'NHL' and 'total' in market and side == 'under':
                    is_b2b = context.get('back_to_back') is True or context.get('rest_days') == 0
                    is_backup = context.get('is_backup_goalie') is True or context.get('backup_goalie') is True
                    if is_b2b and is_backup:
                        return False, f"crucible_filter_gate:{hid}:b2b_backup_goalie_under_excluded", None

            # MLB Extreme Outfield Wind Under Gate
            elif hid == 'hyp_weather_mlb_wind_gate_v1' or ('wind' in rule_text and 'under' in rule_text and 'outfield' in rule_text):
                if league == 'MLB' and 'total' in market and side == 'under':
                    weather = context.get('weather') or {}
                    wind_speed = float(weather.get('wind_speed_mph', 0.0) or 0.0)
                    wind_bearing = str(weather.get('wind_bearing', 'none'))
                    if wind_speed >= 15.0 and wind_bearing == 'outfield':
                        return False, f"crucible_filter_gate:{hid}:extreme_outfield_wind_under_excluded", None

            # Polymarket Sharp Divergence Gate (Capital Defense)
            elif hid == 'hyp_polymarket_stale_line_arb_v1' or 'polymarket_divergence' in hid or ('polymarket' in hid and 'stale' in hid):
                cand_scope = candidate.get('model_scope') or ('full_game' if ':' not in str(market) else str(market).split(':')[0])
                if cand_scope == 'full_game' and ('moneyline' in market or market in ('winner', 'three_way', 'two_way')):
                    poly = context.get('polymarket') or {}
                    if poly.get('matched') and poly.get('has_moneyline'):
                        true_prob = poly.get('true_prob_home') if side == 'home' else (poly.get('true_prob_away') if side == 'away' else None)
                        if true_prob is not None and (p_adjusted - true_prob) > 0.12:
                            return False, f"crucible_filter_gate:{hid}:polymarket_sharp_divergence_rejected", None

            # Soccer Away Moneyline Draw Protection Gate (Protect portfolio from unhedged draw risk)
            elif hid == 'hyp_soccer_away_draw_protection_v1' or ('soccer' in hid and 'draw_protection' in hid):
                if league in ('EPL', 'UCL', 'MLS') and (market in ('1x2', 'winner', 'moneyline', 'three_way') and side == 'away'):
                    return False, f"crucible_filter_gate:{hid}:away_1x2_excluded_protect_draw", None

            # Tennis Grueling Match Fatigue Handicap Defense Gate (Capital Defense)
            elif hid == 'hyp_tennis_fatigue_handicap_gate_v1' or ('tennis' in hid and 'fatigue' in hid):
                if league in ('ATP', 'WTA', 'CHALLENGER') and ('spread' in market or 'handicap' in market):
                    eff_line = float(candidate.get('line') or 0.0)
                    fatigue = float(context.get('fatigue_a', 0.0) if side in ('home', 'player_a') else context.get('fatigue_b', 0.0))
                    if fatigue >= 0.04 and eff_line <= -4.5:
                        return False, f"crucible_filter_gate:{hid}:fatigued_player_deep_handicap_excluded", None

            # NHL Full-Game Puckline / Spreads Capital Defense Gate
            elif (hid == 'hyp_nhl_spread_quarantine_v1' or 'nhl_spread' in hid) and league == 'NHL':
                if 'spread' in market or 'puck_line' in market or 'handicap' in market:
                    eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or 0.0)
                    if eff_ev < 0.055:
                        return False, f"crucible_filter_gate:{hid}:nhl_spreads_quarantined_low_edge", None

            # NHL Full-Game Totals Capital Defense Gate
            elif (hid == 'hyp_nhl_totals_quarantine_v1' or 'nhl_totals_quarantine' in hid) and league == 'NHL':
                if market == 'totals' or market == 'total':
                    eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or 0.0)
                    if eff_ev < 0.050:
                        return False, f"crucible_filter_gate:{hid}:nhl_totals_quarantined_low_edge", None

            # NCAAF Totals Capital Defense Gate
            elif (hid == 'hyp_ncaaf_totals_quarantine_v1' or 'ncaaf_totals_quarantine' in hid) and league == 'NCAAF':
                if 'total' in market:
                    eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or 0.0)
                    if eff_ev < 0.045:
                        return False, f"crucible_filter_gate:{hid}:ncaaf_totals_quarantined_low_edge", None

            # NHL Regulation Team Totals Capital Defense Gate
            elif (hid == 'hyp_nhl_team_totals_quarantine_v1' or 'nhl_team_total' in hid) and league == 'NHL':
                if 'regulation' in market and ('total' in market or 'home_total' in market or 'away_total' in market):
                    eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or 0.0)
                    if eff_ev < 0.06:
                        return False, f"crucible_filter_gate:{hid}:nhl_regulation_team_totals_quarantined", None

            # NHL Period 3 Moneyline Capital Defense Gate
            elif (hid == 'hyp_nhl_period_3_quarantine_v1' or 'nhl_period_3' in hid) and league == 'NHL':
                if 'period_3' in market and ('moneyline' in market or 'three_way' in market):
                    return False, f"crucible_filter_gate:{hid}:nhl_period_3_moneyline_quarantined", None

            # WTA Game Spreads Capital Defense Hurdle Gate (high break-of-serve noise)
            elif (hid == 'hyp_wta_spreads_defense_v1' or 'wta_spread' in hid) and league == 'WTA':
                if 'spread' in market or 'handicap' in market:
                    eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                    if eff_ev < 0.045:
                        return False, f"crucible_filter_gate:{hid}:wta_spreads_low_edge_quarantined", None

            # WTA Game Totals Capital Defense Hurdle Gate
            elif (hid == 'hyp_wta_totals_defense_v1' or 'wta_total' in hid) and league == 'WTA':
                if 'total' in market:
                    eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                    if eff_ev < 0.040:
                        return False, f"crucible_filter_gate:{hid}:wta_totals_low_edge_quarantined", None

            # NHL Regulation Both Teams To Score High-Vig Derivative Gate
            elif (hid == 'hyp_nhl_regulation_both_score_v1' or 'nhl_both_score' in hid) and league == 'NHL':
                if 'both_score' in market or 'both_teams_to_score' in market:
                    eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                    if eff_ev < 0.060:
                        return False, f"crucible_filter_gate:{hid}:nhl_both_score_high_vig_quarantined", None

            # Challenger Tennis Hard Quarantine Gate
            elif (hid == 'hyp_challenger_hard_gate_v1' or 'challenger' in hid) and league == 'CHALLENGER':
                return False, f"crucible_filter_gate:{hid}:challenger_matches_quarantined_to_research", None

            # NFL Regulation Draw Gate (Physical NFL OT rate is ~5.5%, discrete KNN overestimates ties)
            elif league == 'NFL' and 'regulation' in market and 'three_way' in market and side in ('draw', 'tie'):
                return False, "crucible_filter_gate:nfl_regulation_draw_high_variance_quarantined", None

            # Quant Council & Forensic Volatility Under Gate (stops money leak on erratic totals)
            elif ('volatility' in rule_text or 'extreme_volatility' in hid) and side == 'under' and 'total' in market:
                eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                if odds > 2.25 or eff_ev < 0.040:
                    return False, f"crucible_filter_gate:{hid}:extreme_volatility_under_excluded", None

            # Quant Council Draw Protection & Market Consensus Gate
            elif ('draw_protection' in rule_text or 'draw_protection' in hid) and side == 'away':
                if 'three_way' in market or 'moneyline' in market or '1x2' in market:
                    if odds > 2.50:
                        return False, f"crucible_filter_gate:{hid}:draw_protection_away_excluded", None

            # Generic Quarantine Gate for degraded segments
            elif ('quarantine' in rule_text or 'quarantine' in hid) and not ('b2b' in rule_text):
                eff_ev = float(candidate.get('expected_roi') or candidate.get('ev') or (p_orig * odds - 1.0))
                if eff_ev < 0.045:
                    return False, f"crucible_filter_gate:{hid}:degraded_segment_low_edge_quarantined", None

        # 2. Distribution Switch (Overdispersion correction & Ensemble)
        elif cat == 'distribution_switch':
            # Negative Binomial totals overdispersion shrinkage
            if ('negbin' in hid or 'negbin' in rule_text or 'overdispersion' in rule_text) and 'total' in market:
                # Shrink probability towards center to counter empty-net tail overdispersion
                p_adjusted = p_adjusted * 0.94 if p_adjusted > 0.5 else p_adjusted * 1.06
                p_adjusted = max(0.05, min(0.95, round(p_adjusted, 4)))

            # Multi-Pillar Ensemble Calibration
            elif ('ensemble' in hid or 'ensemble' in rule_text or 'monte_carlo' in hid) and not ensemble_applied:
                from .ensemble_architecture import blend_ensemble_probabilities
                from .monte_carlo_engine import simulate_candidate_probability
                p_sim = simulate_candidate_probability(
                    league=league or 'ALL',
                    market=market,
                    side=side,
                    line=candidate.get('line'),
                    home_points=candidate.get('home_points'),
                    away_points=candidate.get('away_points'),
                    context=context,
                    p_fallback=p_adjusted,
                    n_sims=1000
                )
                p_market_anchor = candidate.get('devigged_market_p')
                if p_market_anchor is None:
                    cand_scope = candidate.get('model_scope') or ('full_game' if ':' not in str(market) else str(market).split(':')[0])
                    poly = context.get('polymarket') or {}
                    # Polymarket CLOB only trades full-game Moneyline (1X2).
                    # Never leak Moneyline consensus into spreads, totals, or period scopes!
                    if cand_scope == 'full_game' and poly.get('matched') and market in ('moneyline', 'three_way', 'winner'):
                        p_market_anchor = poly.get('true_prob_home') if side == 'home' else poly.get('true_prob_away')
                ens = blend_ensemble_probabilities(
                    league=league or 'ALL',
                    market=market,
                    p_stat=p_adjusted,
                    p_sim=p_sim,
                    p_market=p_market_anchor
                )
                p_adjusted = ens.blended_probability
                ensemble_applied = True

        # 3. Calibration Shrinkage (Key numbers & Market Anchors)
        elif cat == 'calibration_shrinkage':
            # NFL / NCAAF Key Number calibration on margins (3, 7)
            if 'key_number_push' in hid:
                if league in ('NFL', 'NCAAF') and 'spread' in market:
                    line = abs(float(candidate.get('line', 0.0) or 0.0))
                    # If line is adjacent to 3.0 or 7.0, apply slight conservative shrinkage towards 50%
                    if abs(line - 3.0) < 0.6 or abs(line - 7.0) < 0.6:
                        p_market = 1.0 / odds if odds > 1.0 else p_adjusted
                        p_adjusted = 0.85 * p_adjusted + 0.15 * p_market
                        p_adjusted = max(0.05, min(0.95, round(p_adjusted, 4)))

            # Polymarket CLOB Zero-Vig Consensus Anchor
            elif 'polymarket_clob_anchor' in hid or 'polymarket_anchor' in hid:
                cand_scope = candidate.get('model_scope') or ('full_game' if ':' not in str(market) else str(market).split(':')[0])
                if cand_scope == 'full_game' and ('moneyline' in market or market in ('winner', 'three_way', 'two_way')):
                    poly = context.get('polymarket') or {}
                    if poly.get('matched') and poly.get('has_moneyline'):
                        true_prob = poly.get('true_prob_home') if side == 'home' else (poly.get('true_prob_away') if side == 'away' else None)
                        if true_prob is not None and 0.05 < true_prob < 0.95:
                            from .market_anchor import logit_blend
                            p_adjusted = logit_blend(p_adjusted, true_prob, model_weight=0.80)
                            p_adjusted = max(0.05, min(0.95, round(p_adjusted, 4)))

            # Bayesian / Isotonic Calibration Shrinkage (Quant Council deployed rule)
            elif 'shrinkage' in rule_text or 'isotonic' in hid or 'p_cal' in rule_text or 'shrinkage' in hid:
                # Shrinks model overconfidence towards market-implied probability to halt cash leaks
                p_market = 1.0 / odds if odds > 1.0 else p_adjusted
                p_adjusted = 0.85 * p_adjusted + 0.15 * p_market
                p_adjusted = max(0.05, min(0.95, round(p_adjusted, 4)))

        # 4. Feature Addition (Fatigue & Situational)
        elif cat == 'feature_addition':
            # NBA B2B Travel Load
            if (hid == 'hyp_nba_b2b_travel_load_v1' or 'travel_load' in rule_text) and league == 'NBA':
                is_road_b2b = context.get('road_b2b') is True or (context.get('rest_days') == 0 and side == 'away')
                travel_miles = float(context.get('travel_miles') or context.get('flight_distance') or 0.0)
                if is_road_b2b and travel_miles > 800:
                    # Road team fatigue deduction
                    if side == 'away':
                        p_adjusted = max(0.05, round(p_adjusted - 0.03, 4))
                    elif side == 'home':
                        p_adjusted = min(0.95, round(p_adjusted + 0.03, 4))

            # MLB Bullpen Fatigue
            elif (hid == 'hyp_mlb_bullpen_fatigue_v1' or 'bullpen_fatigue' in rule_text) and league == 'MLB':
                bullpen_days = int(context.get('bullpen_consecutive_days') or 0)
                if bullpen_days >= 2 and 'total' in market and side == 'under':
                    # Under is risky when bullpen is exhausted
                    p_adjusted = max(0.05, round(p_adjusted - 0.025, 4))

            # MLB Live Stadium Weather (Wind Direction and Speed)
            elif (hid == 'hyp_weather_mlb_wind_v1' or 'weather_wind' in rule_text or 'stadium_weather' in rule_text) and league == 'MLB':
                weather = context.get('weather') or {}
                wind_speed = float(weather.get('wind_speed_mph', 0.0) or 0.0)
                wind_bearing = str(weather.get('wind_bearing', 'none'))
                if wind_speed >= 12.0 and 'total' in market:
                    if wind_bearing == 'outfield':
                        # Wind blowing out carries fly balls into home runs
                        if side == 'over':
                            p_adjusted = min(0.95, round(p_adjusted + 0.035, 4))
                        elif side == 'under':
                            p_adjusted = max(0.05, round(p_adjusted - 0.035, 4))
                    elif wind_bearing == 'infield':
                        # Wind blowing in suppresses scoring
                        if side == 'under':
                            p_adjusted = min(0.95, round(p_adjusted + 0.035, 4))
                        elif side == 'over':
                            p_adjusted = max(0.05, round(p_adjusted - 0.035, 4))

            # NFL High Wind Totals Shrinkage
            elif (hid == 'hyp_weather_nfl_wind_v1' or 'nfl_wind' in rule_text) and league in ('NFL', 'NCAAF'):
                weather = context.get('weather') or {}
                wind_speed = float(weather.get('wind_speed_mph', 0.0) or 0.0)
                if wind_speed >= 15.0 and 'total' in market:
                    if side == 'under':
                        p_adjusted = min(0.95, round(p_adjusted + 0.03, 4))
                    elif side == 'over':
                        p_adjusted = max(0.05, round(p_adjusted - 0.03, 4))

            # Tennis Surface Serve Dominance Totals Calibration
            elif ('surface_totals' in hid or 'surface_totals' in rule_text) and league in ('ATP', 'WTA', 'CHALLENGER'):
                surface = str(context.get('surface', 'hard')).lower()
                if 'total' in market:
                    eff_line = float(candidate.get('line') or 22.5)
                    if surface in ('grass', 'indoor') and side == 'over' and eff_line <= 22.5:
                        p_adjusted = min(0.95, round(p_adjusted + 0.025, 4))
                    elif surface == 'clay' and side == 'under' and eff_line >= 22.5:
                        p_adjusted = min(0.95, round(p_adjusted + 0.025, 4))

    return True, None, p_adjusted

