"""Tests for High-Probability Cohort in Shadow Market Incubator (AMIE-GD)."""
import json
import sqlite3
from pathlib import Path
import pytest

from betting_engine.domain import Game
from betting_engine.shadow_market_incubator import (
    ShadowMarketIncubator,
    seed_historical_probability_picks,
    load_graduated_markets,
)


@pytest.fixture
def incubator_dir(tmp_path):
    return tmp_path


@pytest.fixture
def incubator(incubator_dir):
    return ShadowMarketIncubator(data_dir=incubator_dir)


def test_high_probability_candidate_ingestion_and_deduplication(incubator, incubator_dir):
    candidates = [
        # Eligible: High prob (0.65), odds 1.50, sub-threshold EV (-0.025), stake 0
        {
            'id': 'cand_1',
            'game_id': 'NFL:20261015:01',
            'league': 'NFL',
            'market': 'spreads',
            'model_scope': 'full_game',
            'market_family': 'spreads',
            'sub_family': 'spreads',
            'selection': 'Chiefs -3.5',
            'side': 'home',
            'line': -3.5,
            'reference_odds': 1.50,
            'probability': 0.65,
            'push_probability': 0.0,
            'expected_roi': -0.025,
            'stake': 0.0,
            'description': 'Kansas City Chiefs (-3.5)'
        },
        # Excluded: already funded in live portfolio (stake > 0)
        {
            'id': 'cand_funded',
            'game_id': 'NFL:20261015:02',
            'league': 'NFL',
            'market': 'spreads',
            'model_scope': 'full_game',
            'market_family': 'spreads',
            'sub_family': 'spreads',
            'selection': 'Bills -2.5',
            'side': 'home',
            'line': -2.5,
            'reference_odds': 1.85,
            'probability': 0.60,
            'push_probability': 0.0,
            'expected_roi': 0.11,
            'stake': 2.50,
            'description': 'Buffalo Bills (-2.5)'
        },
        # Excluded: probability < 0.55
        {
            'id': 'cand_low_p',
            'game_id': 'NBA:20261015:03',
            'league': 'NBA',
            'market': 'totals',
            'model_scope': 'full_game',
            'market_family': 'totals',
            'sub_family': 'totals',
            'selection': 'Over 220.5',
            'side': 'over',
            'line': 220.5,
            'reference_odds': 2.10,
            'probability': 0.48,
            'push_probability': 0.0,
            'expected_roi': 0.008,
            'stake': 0.0,
            'description': 'Game Total Over 220.5'
        }
    ]

    new_wagers = incubator.incubate_high_probability_candidates(candidates)
    assert len(new_wagers) == 1
    assert new_wagers[0]['league'] == 'NFL'
    assert new_wagers[0]['cohort'] == 'high_probability'

    # Deduplication test: Running it a second time produces 0 new rows
    second_run = incubator.incubate_high_probability_candidates(candidates)
    assert len(second_run) == 0

    # Verify DB contents
    conn = sqlite3.connect(str(incubator_dir / 'shadow_incubator.sqlite3'))
    cur = conn.cursor()
    cur.execute("SELECT cohort, status, model_prob, odds FROM incubated_wagers WHERE cohort = 'high_probability'")
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == 'high_probability'
    assert rows[0][1] == 'open'
    assert rows[0][2] == 0.65
    assert rows[0][3] == 1.50


def test_high_probability_settlement_and_brier(incubator, incubator_dir):
    candidates = [
        {
            'id': 'cand_win',
            'game_id': 'MLS:20261015:01',
            'league': 'MLS',
            'market': 'totals',
            'model_scope': 'full_game',
            'market_family': 'totals',
            'sub_family': 'totals',
            'selection': 'Over 2.5',
            'side': 'over',
            'line': 2.5,
            'reference_odds': 1.60,
            'probability': 0.68,
            'push_probability': 0.0,
            'expected_roi': 0.088,
            'stake': 0.0,
            'description': 'Over 2.5 Goals'
        }
    ]
    # Set EV to marginal so it passes
    candidates[0]['expected_roi'] = 0.02
    incubator.incubate_high_probability_candidates(candidates)

    game = Game(
        id='MLS:20261015:01', league='MLS', start='2026-10-15T18:00:00Z',
        home='Inter Miami', away='LA Galaxy', home_id='MIA', away_id='LAG',
        home_abbr='MIA', away_abbr='LAG',
        state='final', game_type='regular', season=2026,
        home_score=3, away_score=1
    )

    settled = incubator.settle_incubated_wagers([game])
    assert settled == 1

    conn = sqlite3.connect(str(incubator_dir / 'shadow_incubator.sqlite3'))
    cur = conn.cursor()
    cur.execute("SELECT status, result, pnl, brier_model, brier_market FROM incubated_wagers WHERE cohort = 'high_probability'")
    row = cur.fetchone()
    conn.close()

    assert row[0] == 'settled'
    assert row[1] == 'won'
    assert pytest.approx(row[2], 0.01) == 0.60  # $1 * (1.60 - 1)
    # y = 1.0 -> brier_model = (0.68 - 1)^2 = 0.1024
    assert pytest.approx(row[3], 0.001) == (0.68 - 1.0) ** 2
    # brier_market = ((1/1.60) - 1)^2 = (0.625 - 1)^2 = 0.1406
    assert pytest.approx(row[4], 0.001) == ((1.0 / 1.60) - 1.0) ** 2


def test_high_probability_crucible_graduation(incubator, incubator_dir):
    conn = sqlite3.connect(str(incubator_dir / 'shadow_incubator.sqlite3'))
    cur = conn.cursor()

    # Seed 25 settled winning wagers for NFL|full_game|spreads
    # Win rate 80% (20 wins, 5 losses) at odds 1.91 -> strong positive ROI
    for i in range(25):
        is_win = (i < 20)
        pnl = 0.91 if is_win else -1.0
        res = 'won' if is_win else 'lost'
        y = 1.0 if is_win else 0.0
        brier_model = (0.65 - y) ** 2
        brier_mkt = ((1.0 / 1.91) - y) ** 2
        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, side, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (?, ?, ?, 'NFL', 'full_game', 'spreads', 'spreads',
                      'Spread', 'Home', 'home', -3.5, 1.91, 0.65, 0.0,
                      0.523, 0.02, 20, '2026-10-15T12:00:00Z', 'settled', '2026-10-15T15:00:00Z',
                      ?, ?, ?, ?, '{}', 'high_probability')
        """, (f"w_nfl_{i}", f"f_{i}", f"g_{i}", res, pnl, brier_model, brier_mkt))

    # Seed 25 settled losing wagers for ATP|full_game|spreads (Win rate 30%, ROI < 0)
    for i in range(25):
        is_win = (i < 8)
        pnl = 0.90 if is_win else -1.0
        res = 'won' if is_win else 'lost'
        y = 1.0 if is_win else 0.0
        brier_model = (0.58 - y) ** 2
        brier_mkt = ((1.0 / 1.90) - y) ** 2
        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, side, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (?, ?, ?, 'ATP', 'full_game', 'spreads', 'spreads',
                      'Game Spread', 'Home', 'home', -2.5, 1.90, 0.58, 0.0,
                      0.526, 0.01, 20, '2026-10-15T12:00:00Z', 'settled', '2026-10-15T15:00:00Z',
                      ?, ?, ?, ?, '{}', 'high_probability')
        """, (f"w_atp_{i}", f"f_atp_{i}", f"g_atp_{i}", res, pnl, brier_model, brier_mkt))

    conn.commit()
    conn.close()

    grads = incubator.evaluate_graduation()
    grad_keys = [g['segment_key'] for g in grads]

    # NFL|full_game|spreads MUST graduate with SOLID_ALPHA
    assert 'NFL|full_game|spreads' in grad_keys
    nfl_grad = next(g for g in grads if g['segment_key'] == 'NFL|full_game|spreads')
    assert nfl_grad['cohort'] == 'high_probability'
    assert nfl_grad['initial_tier'] == 'SOLID_ALPHA'
    assert nfl_grad['stats']['win_rate'] == 0.80

    # ATP|full_game|spreads MUST NOT graduate
    assert 'ATP|full_game|spreads' not in grad_keys

    # Check persistence in graduated_markets.json
    loaded = load_graduated_markets(incubator_dir)
    assert 'NFL|full_game|spreads' in loaded
