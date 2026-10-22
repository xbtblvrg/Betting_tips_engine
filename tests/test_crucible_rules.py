"""Unit tests for Crucible Deployed Rules Engine."""
import pytest
from betting_engine.crucible_rules import evaluate_crucible_candidate


def test_nhl_b2b_backup_goalie_under_filter():
    rules = [{
        'hypothesis_id': 'hyp_nhl_totals_b2b_gate_v1',
        'league': 'NHL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'exclude if is_road_b2b and is_backup_goalie and side == "under"'
    }]
    
    # Candidate with B2B road game and backup goalie on Under
    bad_cand = {
        'league': 'NHL',
        'market': 'totals',
        'side': 'under',
        'probability': 0.55,
        'reference_odds': 1.95,
        'game_context': {'back_to_back': True, 'is_backup_goalie': True}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(bad_cand, rules)
    assert approved is False
    assert 'b2b_backup_goalie_under_excluded' in reason
    
    # Same game on Over -> approved
    good_cand = {
        'league': 'NHL',
        'market': 'totals',
        'side': 'over',
        'probability': 0.55,
        'reference_odds': 1.95,
        'game_context': {'back_to_back': True, 'is_backup_goalie': True}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(good_cand, rules)
    assert approved is True


def test_nhl_negbin_totals_shrinkage():
    rules = [{
        'hypothesis_id': 'hyp_nhl_totals_negbin_v1',
        'league': 'NHL',
        'category': 'distribution_switch',
        'proposed_formula_or_rule': 'negbin'
    }]
    cand = {
        'league': 'NHL',
        'market': 'totals',
        'side': 'over',
        'probability': 0.60,
        'reference_odds': 1.80,
        'game_context': {}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(cand, rules)
    assert approved is True
    # 0.60 * 0.94 = 0.564
    assert p_adj < 0.60
    assert abs(p_adj - 0.564) < 0.001


def test_nfl_key_number_calibration():
    rules = [{
        'hypothesis_id': 'hyp_nfl_key_number_push_v1',
        'league': 'NFL',
        'category': 'calibration_shrinkage',
        'proposed_formula_or_rule': 'key_number'
    }]
    cand = {
        'league': 'NFL',
        'market': 'spreads',
        'side': 'home',
        'line': -3.0,
        'probability': 0.56,
        'reference_odds': 1.90,
        'game_context': {}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(cand, rules)
    assert approved is True
    assert p_adj is not None
    # 0.85 * 0.56 + 0.15 * (1/1.90) = 0.476 + 0.0789 = 0.5549
    assert p_adj < 0.56


def test_mlb_weather_wind_outfield_gate_and_boost():
    rules = [
        {
            'hypothesis_id': 'hyp_weather_mlb_wind_gate_v1',
            'league': 'MLB',
            'category': 'filter_gate',
            'proposed_formula_or_rule': 'exclude if wind speed >= 15 and wind outfield on under'
        },
        {
            'hypothesis_id': 'hyp_weather_mlb_wind_v1',
            'league': 'MLB',
            'category': 'feature_addition',
            'proposed_formula_or_rule': 'adjust total by wind speed and bearing'
        }
    ]

    # Extreme outfield wind on Under -> excluded by gate
    bad_cand = {
        'league': 'MLB',
        'market': 'totals',
        'side': 'under',
        'probability': 0.52,
        'reference_odds': 1.95,
        'game_context': {'weather': {'wind_speed_mph': 16.5, 'wind_bearing': 'outfield'}}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(bad_cand, rules)
    assert approved is False
    assert 'extreme_outfield_wind_under_excluded' in reason

    # Outfield wind on Over -> boosted by +0.035
    over_cand = {
        'league': 'MLB',
        'market': 'totals',
        'side': 'over',
        'probability': 0.52,
        'reference_odds': 1.95,
        'game_context': {'weather': {'wind_speed_mph': 14.0, 'wind_bearing': 'outfield'}}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(over_cand, rules)
    assert approved is True
    assert p_adj == 0.555

    # Infield wind on Under -> boosted by +0.035
    infield_under_cand = {
        'league': 'MLB',
        'market': 'totals',
        'side': 'under',
        'probability': 0.52,
        'reference_odds': 1.95,
        'game_context': {'weather': {'wind_speed_mph': 14.0, 'wind_bearing': 'infield'}}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(infield_under_cand, rules)
    assert approved is True
    assert p_adj == 0.555


def test_nfl_weather_wind_shrinkage():
    rules = [{
        'hypothesis_id': 'hyp_weather_nfl_wind_v1',
        'league': 'NFL',
        'category': 'feature_addition',
        'proposed_formula_or_rule': 'high wind nfl totals boost under and penalize over'
    }]
    over_cand = {
        'league': 'NFL',
        'market': 'totals',
        'side': 'over',
        'probability': 0.55,
        'reference_odds': 1.91,
        'game_context': {'weather': {'wind_speed_mph': 18.0}}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(over_cand, rules)
    assert approved is True
    assert p_adj == 0.52


def test_polymarket_sharp_divergence_gate():
    rules = [{
        'hypothesis_id': 'hyp_polymarket_stale_line_arb_v1',
        'league': 'NFL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'reject when sharp true prob is >12% below model'
    }]

    # Model says 65% for home, but Polymarket sharp midpoint says 45% (>12% gap) -> REJECT
    bad_cand = {
        'league': 'NFL',
        'market': 'moneyline',
        'side': 'home',
        'probability': 0.65,
        'reference_odds': 1.80,
        'game_context': {
            'polymarket': {
                'matched': True,
                'has_moneyline': True,
                'true_prob_home': 0.45,
                'true_prob_away': 0.55
            }
        }
    }
    approved, reason, p_adj = evaluate_crucible_candidate(bad_cand, rules)
    assert approved is False
    assert 'polymarket_sharp_divergence_rejected' in reason

    # Model says 55% for home, Polymarket says 52% (gap 3% <= 12%) -> APPROVED
    good_cand = {
        'league': 'NFL',
        'market': 'moneyline',
        'side': 'home',
        'probability': 0.55,
        'reference_odds': 1.95,
        'game_context': {
            'polymarket': {
                'matched': True,
                'has_moneyline': True,
                'true_prob_home': 0.52,
                'true_prob_away': 0.48
            }
        }
    }
    approved, reason, p_adj = evaluate_crucible_candidate(good_cand, rules)
    assert approved is True


def test_polymarket_clob_anchor_shrinkage():
    rules = [{
        'hypothesis_id': 'hyp_polymarket_clob_anchor_v1',
        'league': 'ALL',
        'category': 'calibration_shrinkage',
        'proposed_formula_or_rule': 'logit blend 20% towards sharp midpoint'
    }]
    cand = {
        'league': 'NBA',
        'market': 'moneyline',
        'side': 'home',
        'probability': 0.60,
        'reference_odds': 1.80,
        'game_context': {
            'polymarket': {
                'matched': True,
                'has_moneyline': True,
                'true_prob_home': 0.50,
                'true_prob_away': 0.50
            }
        }
    }
    approved, reason, p_adj = evaluate_crucible_candidate(cand, rules)
    assert approved is True
    # 0.60 blended 20% towards 0.50 -> 0.5804
    assert 0.57 < p_adj < 0.59


def test_edge_case_robustness_totals_and_nulls():
    rules = [
        {
            'hypothesis_id': 'hyp_polymarket_stale_line_arb_v1',
            'league': 'ALL',
            'category': 'filter_gate',
            'proposed_formula_or_rule': 'polymarket'
        },
        {
            'hypothesis_id': 'hyp_polymarket_clob_anchor_v1',
            'league': 'ALL',
            'category': 'calibration_shrinkage',
            'proposed_formula_or_rule': 'polymarket'
        }
    ]

    # Total Over candidate on game with Polymarket ML present: MUST NOT be rejected or modified by ML anchor
    totals_cand = {
        'league': 'NBA',
        'market': 'totals',
        'side': 'under',
        'probability': 0.55,
        'reference_odds': 1.90,
        'game_context': {
            'polymarket': {
                'matched': True,
                'has_moneyline': True,
                'true_prob_home': 0.20,
                'true_prob_away': 0.80
            }
        }
    }
    approved, reason, p_adj = evaluate_crucible_candidate(totals_cand, rules)
    assert approved is True
    assert p_adj == 0.55  # Unchanged!

    # Candidate with None / missing values
    null_cand = {
        'league': 'NFL',
        'market': 'moneyline',
        'side': 'home',
        'probability': None,
        'reference_odds': None,
        'game_context': None
    }
    approved2, reason2, p_adj2 = evaluate_crucible_candidate(null_cand, rules)
    assert approved2 is True
    assert 0.01 <= p_adj2 <= 0.99


def test_soccer_away_draw_protection_gate():
    rules = [{
        'hypothesis_id': 'hyp_soccer_away_draw_protection_v1',
        'league': 'EPL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'away_1x2_excluded_protect_draw'
    }]
    
    # Raw Away 1X2 Moneyline in EPL -> must be rejected by gate
    away_cand = {
        'league': 'EPL',
        'market': '1x2',
        'side': 'away',
        'probability': 0.40,
        'reference_odds': 2.70,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(away_cand, rules)
    assert approved is False
    assert 'away_1x2_excluded_protect_draw' in reason

    # Home 1X2 Moneyline in EPL -> approved
    home_cand = {
        'league': 'EPL',
        'market': '1x2',
        'side': 'home',
        'probability': 0.55,
        'reference_odds': 1.95,
        'game_context': {}
    }
    approved_h, reason_h, _ = evaluate_crucible_candidate(home_cand, rules)
    assert approved_h is True


def test_deployed_ensemble_monte_carlo_candidate_evaluation():
    """Verifies that deployed ensemble rule actively runs Monte Carlo and blends probabilities."""
    rules = [{
        'hypothesis_id': 'hyp_ensemble_multi_pillar_v1',
        'league': 'ALL',
        'category': 'distribution_switch',
        'proposed_formula_or_rule': 'bma_logit_ensemble_v1',
        'status': 'deployed'
    }]
    cand = {
        'league': 'NHL',
        'market': 'totals',
        'side': 'over',
        'line': 6.0,
        'probability': 0.62,
        'reference_odds': 1.95,
        'home_points': 3.4,
        'away_points': 2.9,
        'game_context': {}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(cand, rules)
    assert approved is True
    assert 0.50 < p_adj < 0.62
    assert p_adj != 0.62


def test_nhl_and_challenger_capital_defense_quarantine_gates():
    """Verifies that toxic bleeding segments are blocked by capital defense quarantine gates."""
    # 1. NHL low EV puckline
    rules_nhl_spread = [{
        'hypothesis_id': 'hyp_nhl_spread_quarantine_v1',
        'league': 'NHL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'exclude NHL spreads unless EV >= 0.055'
    }]
    cand_low_ev = {
        'league': 'NHL', 'market': 'spreads', 'side': 'home', 'line': -1.5,
        'probability': 0.52, 'reference_odds': 1.95, 'expected_roi': 0.014
    }
    app, reason, _ = evaluate_crucible_candidate(cand_low_ev, rules_nhl_spread)
    assert app is False
    assert 'nhl_spreads_quarantined_low_edge' in reason

    # Exceptional edge NHL puckline -> passes
    cand_high_ev = {
        'league': 'NHL', 'market': 'spreads', 'side': 'home', 'line': -1.5,
        'probability': 0.62, 'reference_odds': 2.10, 'expected_roi': 0.075
    }
    app_high, _, _ = evaluate_crucible_candidate(cand_high_ev, rules_nhl_spread)
    assert app_high is True

    # 2. NHL regulation team totals gate
    rules_nhl_totals = [{
        'hypothesis_id': 'hyp_nhl_team_totals_quarantine_v1',
        'league': 'NHL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'exclude NHL regulation team totals'
    }]
    cand_reg_total = {
        'league': 'NHL', 'market': 'regulation:away_total', 'side': 'over',
        'probability': 0.54, 'reference_odds': 1.90, 'expected_roi': 0.026
    }
    app_rt, reason_rt, _ = evaluate_crucible_candidate(cand_reg_total, rules_nhl_totals)
    assert app_rt is False
    assert 'nhl_regulation_team_totals_quarantined' in reason_rt

    # 3. NHL period 3 moneyline gate
    rules_nhl_p3 = [{
        'hypothesis_id': 'hyp_nhl_period_3_quarantine_v1',
        'league': 'NHL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'exclude NHL period 3 moneyline'
    }]
    cand_p3 = {
        'league': 'NHL', 'market': 'period_3:moneyline', 'side': 'home',
        'probability': 0.55, 'reference_odds': 1.95, 'expected_roi': 0.072
    }
    app_p3, reason_p3, _ = evaluate_crucible_candidate(cand_p3, rules_nhl_p3)
    assert app_p3 is False
    assert 'nhl_period_3_moneyline_quarantined' in reason_p3

    # 4. Challenger tennis hard quarantine gate
    rules_challenger = [{
        'hypothesis_id': 'hyp_challenger_hard_gate_v1',
        'league': 'CHALLENGER',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'exclude all challenger matches'
    }]
    cand_ch = {
        'league': 'CHALLENGER', 'market': 'moneyline', 'side': 'player_a',
        'probability': 0.65, 'reference_odds': 1.85, 'expected_roi': 0.20
    }
    app_ch, reason_ch, _ = evaluate_crucible_candidate(cand_ch, rules_challenger)
    assert app_ch is False
    assert 'challenger_matches_quarantined_to_research' in reason_ch

    # 5. NHL full-game totals quarantine gate (EV < 0.050 rejected, >= 0.050 approved)
    rules_nhl_totals_gate = [{
        'hypothesis_id': 'hyp_nhl_totals_quarantine_v1',
        'league': 'NHL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'exclude NHL totals unless EV >= 0.050'
    }]
    cand_nhl_tot_low = {
        'league': 'NHL', 'market': 'totals', 'side': 'under',
        'probability': 0.53, 'reference_odds': 1.90, 'expected_roi': 0.007
    }
    app_nt_low, reason_nt_low, _ = evaluate_crucible_candidate(cand_nhl_tot_low, rules_nhl_totals_gate)
    assert app_nt_low is False
    assert 'nhl_totals_quarantined_low_edge' in reason_nt_low

    cand_nhl_tot_high = {
        'league': 'NHL', 'market': 'totals', 'side': 'under',
        'probability': 0.58, 'reference_odds': 1.95, 'expected_roi': 0.065
    }
    app_nt_high, reason_nt_high, _ = evaluate_crucible_candidate(cand_nhl_tot_high, rules_nhl_totals_gate)
    assert app_nt_high is True

    # 6. NCAAF totals quarantine gate (EV < 0.045 rejected, >= 0.045 approved)
    rules_ncaaf_totals_gate = [{
        'hypothesis_id': 'hyp_ncaaf_totals_quarantine_v1',
        'league': 'NCAAF',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'exclude NCAAF totals unless EV >= 0.045'
    }]
    cand_ncaaf_low = {
        'league': 'NCAAF', 'market': 'totals', 'side': 'over',
        'probability': 0.53, 'reference_odds': 1.91, 'expected_roi': 0.012
    }
    app_nc_low, reason_nc_low, _ = evaluate_crucible_candidate(cand_ncaaf_low, rules_ncaaf_totals_gate)
    assert app_nc_low is False
    assert 'ncaaf_totals_quarantined_low_edge' in reason_nc_low

    cand_ncaaf_high = {
        'league': 'NCAAF', 'market': 'totals', 'side': 'over',
        'probability': 0.57, 'reference_odds': 1.91, 'expected_roi': 0.052
    }
    app_nc_high, reason_nc_high, _ = evaluate_crucible_candidate(cand_ncaaf_high, rules_ncaaf_totals_gate)
    assert app_nc_high is True


def test_polymarket_anchor_isolation_across_all_leagues():
    """Verifies that Polymarket Moneyline true probability never leaks into spreads or totals."""
    poly_context = {
        'matched': True,
        'has_moneyline': True,
        'true_prob_home': 0.85,
        'true_prob_away': 0.15
    }
    rules = [{
        'hypothesis_id': 'hyp_ensemble_mc_stat_poly_v1',
        'league': 'ALL',
        'category': 'ensemble_blending',
        'proposed_formula_or_rule': 'ensemble_blending'
    }]
    
    # 1. Spreads market (EPL -2.5): Moneyline 85% must NOT be blended as market anchor!
    cand_spread = {
        'league': 'EPL',
        'market': 'spreads',
        'side': 'home',
        'line': -2.5,
        'home_points': 2.0,
        'away_points': 1.0,
        'probability': 0.20,
        'reference_odds': 4.50,
        'game_context': {'polymarket': poly_context}
    }
    approved, reason, p_adj = evaluate_crucible_candidate(cand_spread, rules)
    # The spread probability must stay realistic (< 0.30), not inflated to ~60-70% by the 85% ML anchor
    assert p_adj < 0.30, f"Spread probability inflated by ML anchor: {p_adj}"

    # 2. Totals market (NBA Over 225.5): Moneyline 85% must NOT be blended into totals!
    cand_total = {
        'league': 'NBA',
        'market': 'totals',
        'side': 'over',
        'line': 225.5,
        'home_points': 112.0,
        'away_points': 110.0,
        'probability': 0.48,
        'reference_odds': 1.91,
        'game_context': {'polymarket': poly_context}
    }
    approved_t, reason_t, p_adj_t = evaluate_crucible_candidate(cand_total, rules)
    assert 0.35 < p_adj_t < 0.65, f"Totals probability contaminated: {p_adj_t}"

    # 3. Period Moneyline (NHL period_2:moneyline): Full-game ML 85% must NOT leak into period ML!
    cand_period = {
        'league': 'NHL',
        'market': 'period_2:moneyline',
        'model_scope': 'period_2',
        'side': 'home',
        'probability': 0.42,
        'reference_odds': 2.30,
        'game_context': {'polymarket': poly_context}
    }
    approved_p, reason_p, p_adj_p = evaluate_crucible_candidate(cand_period, rules)
    assert p_adj_p == 0.42 or abs(p_adj_p - 0.42) < 0.05, f"Period moneyline contaminated by full-game ML: {p_adj_p}"


def test_volatility_under_gate_stops_money_leak():
    rules = [{
        'hypothesis_id': 'hyp_council_epl_full_game_totals_v1',
        'league': 'EPL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'exclude_extreme_volatility_under'
    }]
    # High odds, low edge Under candidate -> rejected to stop money leak
    bad_cand = {
        'league': 'EPL',
        'market': 'totals',
        'side': 'under',
        'probability': 0.45,
        'reference_odds': 2.35,
        'expected_roi': 0.02,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(bad_cand, rules)
    assert approved is False
    assert 'extreme_volatility_under_excluded' in reason

    # Controlled Under with solid edge and moderate odds -> approved
    good_cand = {
        'league': 'EPL',
        'market': 'totals',
        'side': 'under',
        'probability': 0.58,
        'reference_odds': 1.95,
        'expected_roi': 0.06,
        'game_context': {}
    }
    approved, _, _ = evaluate_crucible_candidate(good_cand, rules)
    assert approved is True


def test_draw_protection_away_gate():
    rules = [{
        'hypothesis_id': 'hyp_council_mlb_full_game_moneyline_v1',
        'league': 'MLB',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'draw_protection_and_market_consensus'
    }]
    # Away candidate with long odds (>2.50) -> excluded
    bad_cand = {
        'league': 'MLB',
        'market': 'moneyline',
        'side': 'away',
        'probability': 0.38,
        'reference_odds': 2.65,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(bad_cand, rules)
    assert approved is False
    assert 'draw_protection_away_excluded' in reason


def test_bayesian_calibration_shrinkage_shrinks_overconfidence():
    rules = [{
        'hypothesis_id': 'hyp_council_nhl_full_game_spreads_v1',
        'league': 'NHL',
        'category': 'calibration_shrinkage',
        'proposed_formula_or_rule': 'key_number_kernel_shrinkage'
    }]
    # Model thinks p = 0.65, but book odds = 2.0 (market p = 0.50)
    cand = {
        'league': 'NHL',
        'market': 'spreads',
        'side': 'home',
        'probability': 0.65,
        'reference_odds': 2.0,
        'game_context': {}
    }
    approved, _, p_adj = evaluate_crucible_candidate(cand, rules)
    assert approved is True
    # 0.85 * 0.65 + 0.15 * 0.50 = 0.5525 + 0.075 = 0.6275
    assert abs(p_adj - 0.6275) < 0.001
    assert p_adj < 0.65  # Overconfidence shrunken towards market


def test_synthetic_historical_replay_fallback():
    """Verifies that Crucible uses broader synthetic replay when system_decisions sample size is small."""
    from betting_engine.quant_crucible import run_crucible_walk_forward, run_synthetic_historical_replay
    from betting_engine.hypothesis_engine import ResearchRecord
    import sqlite3, json

    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE system_decisions (
            payload TEXT,
            status TEXT,
            pnl REAL,
            outcome_evidence TEXT,
            issued_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY,
            payload TEXT,
            status TEXT,
            pnl REAL,
            odds REAL,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE bet_outcomes (
            bet_id INTEGER,
            league TEXT,
            market TEXT,
            result TEXT,
            probability REAL
        )
    """)

    # Populate 6 historical synthetic wagers in bets & bet_outcomes
    for i in range(6):
        payload = {'stake': 2.0, 'reference_odds': 2.0, 'side': 'home', 'market': 'spreads'}
        conn.execute("INSERT INTO bets VALUES (?, ?, ?, ?, ?, ?)",
                     (i + 1, json.dumps(payload), 'won' if i % 2 == 0 else 'lost', 2.0 if i % 2 == 0 else -2.0, 2.0, f"2026-10-0{i+1}"))
        conn.execute("INSERT INTO bet_outcomes VALUES (?, ?, ?, ?, ?)",
                     (i + 1, 'NHL', 'spreads', 'won' if i % 2 == 0 else 'lost', 0.55))
    conn.commit()

    rec = ResearchRecord(
        hypothesis_id="hyp_test_synthetic_replay_v1",
        segment="NHL|full_game|spreads",
        league="NHL",
        category="calibration_shrinkage",
        title="Test Synthetic Replay",
        rationale="Shrinkage towards market",
        proposed_formula_or_rule="key_number_kernel_shrinkage",
        status="active",
        created_at="2026-10-12T00:00:00Z",
        updated_at="2026-10-12T00:00:00Z"
    )

    # system_decisions is empty, so it must fall back to synthetic replay
    result = run_crucible_walk_forward(rec, conn, min_sample_size=4)
    assert result is not None
    assert result.sample_size == 6
    assert result.evidence_payload.get('synthetic_replay') is True
    assert result.passed is True
    assert result.candidate_brier > 0.0
    conn.close()


def test_crucible_segment_specific_quarantine_isolation():
    """Verify that a segment-specific quarantine rule (e.g. NHL regulation both_score)
    does not leak into other segments (e.g. NHL full_game moneyline)."""
    rules = [{
        'hypothesis_id': 'hyp_council_nhl_regulation_regulation_both_score_v4',
        'segment': 'NHL|regulation|regulation:both_score',
        'league': 'NHL',
        'category': 'filter_gate',
        'proposed_formula_or_rule': 'quarantine_high_variance_outliers'
    }]

    # NHL Moneyline candidate (different segment) -> must be APPROVED
    ml_cand = {
        'league': 'NHL',
        'market': 'moneyline',
        'side': 'home',
        'model_scope': 'full_game',
        'probability': 0.55,
        'reference_odds': 1.95,
        'expected_roi': 0.0725,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(ml_cand, rules)
    assert approved is True, f"NHL Moneyline was incorrectly rejected: {reason}"

    # NHL regulation both_score candidate with low EV (< 4.5%) -> must be QUARANTINED
    both_score_cand = {
        'league': 'NHL',
        'market': 'regulation:both_score',
        'side': 'yes',
        'model_scope': 'regulation',
        'probability': 0.50,
        'reference_odds': 2.02,
        'expected_roi': 0.01,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(both_score_cand, rules)
    assert approved is False
    assert 'degraded_segment_low_edge_quarantined' in reason


def test_nfl_quarter_three_way_quarantine_live():
    # NFL Quarter 3-way bet in live production (rules=None) -> quarantined
    cand = {
        'league': 'NFL',
        'market': 'quarter_3:three_way',
        'model_scope': 'quarter_3',
        'side': 'home',
        'probability': 0.45,
        'reference_odds': 2.40,
        'expected_roi': 0.08,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(cand, rules=None)
    assert approved is False
    assert 'nfl_quarter_three_way_draw_quarantined' in reason

    # 2-way Spread on Quarter 3 with strong EV -> approved
    spread_cand = {
        'league': 'NFL',
        'market': 'quarter_3:spreads',
        'model_scope': 'quarter_3',
        'side': 'home',
        'probability': 0.58,
        'reference_odds': 1.95,
        'expected_roi': 0.13,
        'game_context': {}
    }
    approved2, reason2, p_adj = evaluate_crucible_candidate(spread_cand, rules=None)
    assert approved2 is True
    assert p_adj is not None


def test_tennis_unrated_prior_underdog_trap():
    # Tennis underdog with 0.50 unrated prior at odds > 2.15 must be rejected
    cand_trap = {
        'id': 'test_trap',
        'league': 'ATP',
        'market': 'moneyline',
        'side': 'home',
        'probability': 0.50,
        'reference_odds': 3.20,
        'expected_roi': 0.60,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(cand_trap, rules=None)
    assert approved is False
    assert 'tennis_unrated_prior_underdog_trap' in reason

    # Tennis favorite with 0.50 prior (odds 1.85) -> not blocked by this rule
    cand_fav = {
        'id': 'test_fav',
        'league': 'ATP',
        'market': 'moneyline',
        'side': 'home',
        'probability': 0.50,
        'reference_odds': 1.85,
        'expected_roi': -0.075,
        'game_context': {}
    }
    approved_fav, reason_fav, _ = evaluate_crucible_candidate(cand_fav, rules=None)
    assert 'tennis_unrated_prior_underdog_trap' not in (reason_fav or '')

    # Calibrated tennis prediction with real probability (e.g. 0.62 at 1.75) -> approved
    cand_real = {
        'id': 'test_real',
        'league': 'ATP',
        'market': 'moneyline',
        'side': 'home',
        'probability': 0.62,
        'reference_odds': 1.75,
        'expected_roi': 0.085,
        'game_context': {}
    }
    approved_real, reason_real, _ = evaluate_crucible_candidate(cand_real, rules=None)
    assert approved_real is True


def test_nhl_high_odds_underdog_filter():
    # NHL Moneyline on extreme dog with odds 2.60 and moderate EV (<15%) -> rejected
    cand_dog = {
        'id': 'test_nhl_dog',
        'league': 'NHL',
        'market': 'moneyline',
        'side': 'away',
        'probability': 0.42,
        'reference_odds': 2.60,
        'expected_roi': 0.092,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(cand_dog, rules=None)
    assert approved is False
    assert 'nhl_high_odds_underdog_quarantined' in reason

    # NHL Moneyline with odds 1.95 (close match) -> approved
    cand_norm = {
        'id': 'test_nhl_norm',
        'league': 'NHL',
        'market': 'moneyline',
        'side': 'home',
        'probability': 0.55,
        'reference_odds': 1.95,
        'expected_roi': 0.0725,
        'game_context': {}
    }
    approved_norm, reason_norm, _ = evaluate_crucible_candidate(cand_norm, rules=None)
    assert approved_norm is True


def test_tennis_volatile_game_totals_over_filter():
    # Tennis Over on totals with low EV (<4%) -> rejected
    cand_over_low = {
        'id': 'test_wta_over_low',
        'league': 'WTA',
        'market': 'totals',
        'side': 'over',
        'probability': 0.52,
        'reference_odds': 1.95,
        'expected_roi': 0.014,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(cand_over_low, rules=None)
    assert approved is False
    assert 'tennis_volatile_game_totals_over_hurdle' in reason

    # Tennis Over on totals with strong EV (>=4%) -> approved
    cand_over_high = {
        'id': 'test_wta_over_high',
        'league': 'WTA',
        'market': 'totals',
        'side': 'over',
        'probability': 0.55,
        'reference_odds': 1.95,
        'expected_roi': 0.0725,
        'game_context': {}
    }
    approved_high, reason_high, _ = evaluate_crucible_candidate(cand_over_high, rules=None)
    assert approved_high is True


def test_wta_spreads_defense_gate():
    # Low EV WTA spread (< 4.5%) is quarantined
    cand_low = {
        'id': 'test_wta_spread_low',
        'league': 'WTA',
        'market': 'spreads',
        'side': 'home',
        'probability': 0.52,
        'reference_odds': 1.95,
        'expected_roi': 0.014,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(cand_low, rules=None)
    assert approved is False
    assert 'wta_spreads_low_edge_quarantined' in reason

    # Strong EV WTA spread (>= 4.5%) is approved
    cand_high = {
        'id': 'test_wta_spread_high',
        'league': 'WTA',
        'market': 'spreads',
        'side': 'home',
        'probability': 0.55,
        'reference_odds': 1.95,
        'expected_roi': 0.0725,
        'game_context': {}
    }
    approved_high, _, _ = evaluate_crucible_candidate(cand_high, rules=None)
    assert approved_high is True


def test_nhl_regulation_both_score_defense_gate():
    # NHL regulation both score is toxic variance quarantined in production
    cand = {
        'id': 'test_nhl_btts',
        'league': 'NHL',
        'market': 'regulation:both_score',
        'side': 'yes',
        'probability': 0.52,
        'reference_odds': 1.95,
        'expected_roi': 0.014,
        'game_context': {}
    }
    approved, reason, _ = evaluate_crucible_candidate(cand, rules=None)
    assert approved is False
    assert 'nhl_both_score_toxic_variance_quarantined' in reason











