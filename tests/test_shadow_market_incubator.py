"""Unit and Crucible tests for Autonomous Market Expansion & Shadow Graduation Department."""
import json
import sqlite3
from types import SimpleNamespace
import pytest
import numpy as np

from betting_engine.domain import Game
from betting_engine.market_decomposer import DecomposedContract
from betting_engine.shadow_market_incubator import (
    ShadowMarketIncubator,
    load_graduated_markets,
    save_graduated_markets
)


@pytest.fixture
def incubator(tmp_path):
    return ShadowMarketIncubator(data_dir=tmp_path)


def test_init_and_empty_status(incubator):
    status = incubator.get_incubator_status()
    assert status['department'] == 'Autonomous Market Expansion & Shadow Graduation Department (AMIE-GD)'
    assert status['active_incubations'] == 0
    assert status['graduated_markets_count'] == 0


def test_price_asian_lines(incubator):
    # Mock game and forecast
    game = Game(
        id='EPL:101', league='EPL', start='2026-10-15T15:00:00Z',
        home='Arsenal', away='Chelsea', home_id='ARS', away_id='CHE',
        home_abbr='ARS', away_abbr='CHE',
        state='scheduled', game_type='regular', season=2026
    )

    class MockSoccerModel:
        def predict_asian_handicap(self, home, away, line, side='home'):
            return {'win': 0.58, 'push': 0.12}

        def predict_totals(self, home, away, line):
            return {'over': 0.55, 'under': 0.40, 'push': 0.05}

    forecast = SimpleNamespace(soccer_model=MockSoccerModel())

    # Asian handicap quarter line (-0.75)
    contract_hcp = DecomposedContract(
        raw_market_name="Asian Handicap", league="EPL", fixture_id="101",
        scope="full_game", family="asian_lines", sub_family="asian_handicap",
        mathematical_model="split_distribution_margin", line=-0.75, side="home",
        selection="Arsenal (-0.75)", odds=1.95
    )
    p_win, p_push = incubator.price_contract(contract_hcp, game, forecast)
    assert p_win == 0.58
    assert p_push == 0.12


def test_price_tennis_sets(incubator):
    game = Game(
        id='ATP:201', league='ATP', start='2026-10-15T12:00:00Z',
        home='Alcaraz', away='Sinner', home_id='ALC', away_id='SIN',
        home_abbr='ALC', away_abbr='SIN',
        state='scheduled', game_type='regular', season=2026
    )
    forecast = SimpleNamespace(features={'elo_difference': 50.0})

    contract_set_win = DecomposedContract(
        raw_market_name="1st Set - Winner", league="ATP", fixture_id="201",
        scope="period_1", family="tennis_sets", sub_family="set_winner",
        mathematical_model="markov_chain_service_hold", line=None, side="home",
        selection="Alcaraz", odds=1.70
    )
    p_win, p_push = incubator.price_contract(contract_set_win, game, forecast)
    assert 0.40 <= p_win <= 0.80
    assert p_push == 0.0

    # Total games 9.5
    contract_set_tot = DecomposedContract(
        raw_market_name="1st Set - Total Games", league="ATP", fixture_id="201",
        scope="period_1", family="tennis_sets", sub_family="set_totals",
        mathematical_model="markov_chain_service_hold", line=9.5, side="over",
        selection="Over 9.5", odds=1.90
    )
    p_tot_win, p_tot_push = incubator.price_contract(contract_set_tot, game, forecast)
    assert 0.20 <= p_tot_win <= 0.70
    assert p_tot_push == 0.0


def test_incubation_and_settlement_asian_lines(incubator):
    game = Game(
        id='EPL:101', league='EPL', start='2026-10-15T15:00:00Z',
        home='Arsenal', away='Chelsea', home_id='ARS', away_id='CHE',
        home_abbr='ARS', away_abbr='CHE',
        state='scheduled', game_type='regular', season=2026
    )
    class MockSoccerModel:
        def predict_asian_handicap(self, home, away, line, side='home'):
            return {'win': 0.62 if side == 'home' else 0.38, 'push': 0.0}

    forecast = SimpleNamespace(soccer_model=MockSoccerModel())

    raw_markets = [{
        'name': 'Asian Handicap',
        'specifiers': 'hcp=-0.5',
        'outcomes': [
            {'name': 'Arsenal (-0.5)', 'odds': 1.90},
            {'name': 'Chelsea (+0.5)', 'odds': 1.90}
        ]
    }]

    # Ingest: Arsenal -0.5 has p_win=0.62, odds=1.90 -> EV = 0.62*1.90 - 1 = +0.178 (+17.8% EV)
    wagers = incubator.incubate_fixture_gap_markets(raw_markets, game, 'EPL', forecast)
    assert len(wagers) == 1
    assert wagers[0]['sub_family'] == 'asian_handicap'
    assert wagers[0]['ev'] > 0.035

    # Check status has 1 open wager
    status = incubator.get_incubator_status()
    assert status['status_counts'].get('open') == 1

    # Settle game: Arsenal wins 2 - 0
    game_final = Game(
        id='EPL:101', league='EPL', start='2026-10-15T15:00:00Z',
        home='Arsenal', away='Chelsea', home_id='ARS', away_id='CHE',
        home_abbr='ARS', away_abbr='CHE',
        state='final', home_score=2.0, away_score=0.0,
        game_type='regular', season=2026
    )
    settled = incubator.settle_incubated_wagers([game_final])
    assert settled == 1

    status_after = incubator.get_incubator_status()
    assert status_after['status_counts'].get('settled') == 1
    fam = status_after['incubated_families'][0]
    assert fam['pnl'] == 0.90  # 1.0 * (1.90 - 1.0) = +0.90


def test_graduation_crucible_rules(incubator, tmp_path):
    conn = incubator._get_connection()
    cur = conn.cursor()

    # Insert 25 simulated settled wagers for EPL Asian Handicap with positive edge:
    # 17 wins (odds 1.95, profit 0.95), 8 losses (-1.00), interleaved
    # Total stake = 25.0
    # Profit = 17 * 0.95 - 8 * 1.0 = 16.15 - 8.0 = +8.15 -> ROI = +32.6%
    # Interleaved: W, W, L, W, W, L... so max drawdown is very small (< 10%)
    for i in range(25):
        w_id = f"wager_{i}"
        is_win = (i % 3 != 0)  # 17 wins, 8 losses interleaved
        res = 'won' if is_win else 'lost'
        pnl = 0.95 if is_win else -1.0
        b_model = (0.60 - (1.0 if is_win else 0.0)) ** 2
        b_mkt = (0.5128 - (1.0 if is_win else 0.0)) ** 2

        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata
            ) VALUES (?, 'fix', ?, 'EPL', 'full_game', 'asian_lines', 'asian_handicap',
                      'Asian Handicap', 'Arsenal -0.5', -0.5, 1.95, 0.60, 0.0,
                      0.5128, 0.17, 20, '2026-10-10T12:00:00Z', 'settled',
                      '2026-10-10T14:00:00Z', ?, ?, ?, ?, '{}')
        """, (w_id, f"game_{i}", res, pnl, b_model, b_mkt))

    conn.commit()
    conn.close()

    # Run graduation crucible evaluation
    graduated = incubator.evaluate_graduation()
    assert len(graduated) == 1
    g = graduated[0]
    assert g['segment_key'] == 'EPL|full_game|asian_lines'
    assert g['stats']['sample_size'] == 25
    assert g['stats']['roi'] > 0.035
    assert g['stats']['delta_brier'] > 0.002
    assert g['stats']['max_drawdown'] < 0.15

    # Check persistence in graduated_markets.json
    loaded = load_graduated_markets(tmp_path)
    assert 'EPL|full_game|asian_lines' in loaded


def test_volume_deficit_expansion_graduation(incubator, tmp_path):
    conn = incubator._get_connection()
    cur = conn.cursor()

    # Insert 28 settled wagers for WTA Set Winner with positive edge and low drawdown
    for i in range(28):
        w_id = f"wta_wager_{i}"
        is_win = (i % 3 != 0)
        res = 'won' if is_win else 'lost'
        pnl = 0.80 if is_win else -1.0
        b_model = (0.65 - (1.0 if is_win else 0.0)) ** 2
        b_mkt = (0.555 - (1.0 if is_win else 0.0)) ** 2

        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata
            ) VALUES (?, 'fix', ?, 'WTA', 'period_1', 'tennis_sets', 'set_winner',
                      '1st Set - Winner', 'Swiatek', NULL, 1.80, 0.65, 0.0,
                      0.555, 0.17, 20, '2026-10-10T12:00:00Z', 'settled',
                      '2026-10-10T14:00:00Z', ?, ?, ?, ?, '{}')
        """, (w_id, f"game_{i}", res, pnl, b_model, b_mkt))

    conn.commit()
    conn.close()

    # Under volume deficit expansion mode (e.g. daily shortfall = 25), adaptive threshold (N >= 25) activates!
    deficit_grads = incubator.trigger_volume_deficit_expansion(daily_shortfall=25)
    assert len(deficit_grads) == 1
    g = deficit_grads[0]
    assert g['segment_key'] == 'WTA|period_1|tennis_sets'
    assert g['stats']['volume_expansion_triggered'] is True
    assert g['stats']['shortfall_context'] == 25
    assert g['stats']['roi'] >= 0.03
    assert g['stats']['delta_brier'] > 0.001
    assert g['stats']['max_drawdown'] < 0.15

    # Check persistence
    loaded = load_graduated_markets(tmp_path)
    assert 'WTA|period_1|tennis_sets' in loaded


def test_american_derivatives_decomposition_and_pricing(incubator):
    from betting_engine.market_decomposer import decompose_market
    from betting_engine.period_models import PeriodForecast

    # 1. Test Decomposing NFL, NBA, MLB, NHL markets
    nfl_mkt = {
        'market': '1st Half - Handicap',
        'specifiers': 'hcp=-3.5',
        'outcomes': [{'name': 'Kansas City Chiefs', 'odds': 1.91}, {'name': 'Buffalo Bills', 'odds': 1.91}]
    }
    contracts = decompose_market(nfl_mkt, league='NFL', fixture_id='nfl_101')
    assert len(contracts) == 2
    assert contracts[0].family == 'american_derivatives'
    assert contracts[0].sub_family == 'spreads'
    assert contracts[0].scope == 'half_1'
    assert contracts[0].line == -3.5

    # 2. Test Pricing with PeriodForecast
    game = Game(
        id='NFL:101', league='NFL', start='2026-10-15T20:00:00Z',
        home='Kansas City Chiefs', away='Buffalo Bills', home_id='KC', away_id='BUF',
        home_abbr='KC', away_abbr='BUF',
        state='scheduled', game_type='regular', season=2026
    )

    h_scores = np.array([14, 17, 10, 21])
    a_scores = np.array([10, 14, 14, 10])
    weights = np.array([0.25, 0.25, 0.25, 0.25])
    p_fc = PeriodForecast('NFL:101', 'NFL', 'half_1', 15.5, 12.0, (h_scores, a_scores, weights), {}, 'v1', [])

    segment_forecasts = {('NFL:101', 'half_1'): p_fc}
    p_win, p_push = incubator.price_contract(contracts[0], game, forecast=None, segment_forecasts=segment_forecasts)
    assert p_win == 0.50
    assert p_push == 0.0

    # 3. Test Team Total Decomposition (NBA / MLB)
    mlb_mkt = {
        'market': 'Innings 1 to 5 - Milwaukee Brewers Total',
        'specifiers': 'total=1.5',
        'outcomes': [{'name': 'Over 1.5', 'odds': 1.77}, {'name': 'Under 1.5', 'odds': 2.05}]
    }
    mlb_contracts = decompose_market(mlb_mkt, league='MLB', fixture_id='mlb_201')
    assert len(mlb_contracts) == 2
    assert mlb_contracts[0].family == 'american_derivatives'
    assert mlb_contracts[0].sub_family == 'team_total'
    assert mlb_contracts[0].scope == 'innings_1_5'
    assert mlb_contracts[0].line == 1.5


def test_american_derivatives_settlement(incubator):
    conn = incubator._get_connection()
    cur = conn.cursor()

    # Insert open american derivatives wager
    cur.execute("""
        INSERT INTO incubated_wagers (
            id, fixture_id, game_id, league, scope, family, sub_family,
            raw_market_name, selection, line, odds, model_prob, push_prob,
            market_prob, ev, dynamic_sample_power_req, captured_at, status,
            metadata
        ) VALUES (
            'w_nfl_1', 'nfl_101', 'NFL:101', 'NFL', 'half_1', 'american_derivatives', 'spreads',
            '1st Half - Handicap', 'Chiefs -3.5', -3.5, 1.91, 0.58, 0.0,
            0.523, 0.10, 20, '2026-10-15T12:00:00Z', 'open', '{}'
        )
    """)
    conn.commit()
    conn.close()

    # Final game with segment_scores
    game = Game(
        id='NFL:101', league='NFL', start='2026-10-15T20:00:00Z',
        home='Chiefs', away='Bills', home_id='KC', away_id='BUF',
        home_abbr='KC', away_abbr='BUF',
        state='final', game_type='regular', season=2026,
        home_score=27, away_score=20,
        context={'segment_scores': {'half_1': {'home': 17, 'away': 10, 'complete': True}}}
    )

    settled = incubator.settle_incubated_wagers([game])
    assert settled == 1

    conn = incubator._get_connection()
    cur = conn.cursor()
    cur.execute("SELECT status, result, pnl FROM incubated_wagers WHERE id = 'w_nfl_1'")
    row = cur.fetchone()
    conn.close()

    assert row[0] == 'settled'
    assert row[1] == 'won'
    assert abs(row[2] - 0.91) < 1e-4


def test_evaluate_demotions_and_substitutions(incubator, tmp_path):
    # Setup: a graduated market with live deteriorating settled decisions
    from src.betting_engine.shadow_market_incubator import save_graduated_markets, load_graduated_markets
    seg_key = 'NFL|quarter_1|american_derivatives'
    fake_grad = {
        seg_key: {
            'id': 'grad_nfl_q1',
            'segment_key': seg_key,
            'league': 'NFL',
            'scope': 'quarter_1',
            'family': 'american_derivatives',
            'sub_family': 'spreads',
            'stats': {'roi': 0.05, 'sample_size': 25}
        }
    }
    save_graduated_markets(fake_grad, incubator.data_dir)
    conn_inc = incubator._get_connection()
    conn_inc.execute("""
        INSERT OR REPLACE INTO graduated_markets (
            id, segment_key, league, scope, family, sub_family, graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
        ) VALUES ('grad_nfl_q1', ?, 'NFL', 'quarter_1', 'american_derivatives', 'spreads', '2026-10-15T10:00:00Z', 25, 0.05, 0.005, 0.05, 0.6, '{}')
    """, (seg_key,))
    conn_inc.commit()
    conn_inc.close()

    # Create mock engine DB with 6 lost decisions in NFL quarter_1
    eng_db = tmp_path / 'engine.sqlite3'
    conn = sqlite3.connect(eng_db)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE system_decisions (
            id TEXT PRIMARY KEY,
            payload TEXT,
            status TEXT,
            pnl REAL,
            issued_at TEXT
        )
    """)
    for i in range(6):
        payload = json.dumps({'league': 'NFL', 'market': '1st Quarter - Handicap', 'stake': 1.0})
        cur.execute("INSERT INTO system_decisions VALUES (?, ?, 'lost', -1.0, ?)",
                    (f"d_{i}", payload, f"2026-10-15T12:0{i}:00Z"))
    conn.commit()
    conn.close()

    res = incubator.evaluate_demotions_and_substitutions(engine_db_path=eng_db)
    assert len(res['demotions']) == 1
    assert res['demotions'][0]['segment_key'] == seg_key
    assert res['demotions'][0]['rolling_pnl'] == -6.0

    # Verify market was removed from active graduated markets in JSON AND SQLite
    grads_after = load_graduated_markets(incubator.data_dir)
    assert seg_key not in grads_after

    conn_check = incubator._get_connection()
    cur_check = conn_check.cursor()
    cur_check.execute("SELECT count(*) FROM graduated_markets WHERE segment_key = ?", (seg_key,))
    assert cur_check.fetchone()[0] == 0

    # Verify market was inserted into demoted_markets
    cur_check.execute("SELECT segment_key, rolling_roi, rolling_pnl FROM demoted_markets WHERE segment_key = ?", (seg_key,))
    row_dem = cur_check.fetchone()
    assert row_dem is not None
    assert row_dem[0] == seg_key
    assert row_dem[2] == -6.0
    conn_check.close()


def test_automated_demotion_and_regraduation_lifecycle(incubator, tmp_path):
    """Verifies that:
    1. A degrading market is demoted to demoted_markets and pruned from graduated.
    2. It CANNOT re-graduate on old pre-demotion wagers (probation guard).
    3. Once new wagers are captured and settled strictly AFTER demotion with proven edge,
       it successfully re-graduates and is cleared from demoted_markets.
    """
    from src.betting_engine.shadow_market_incubator import save_graduated_markets, load_graduated_markets

    seg_key = 'EPL|full_game|asian_lines'
    conn = incubator._get_connection()
    cur = conn.cursor()

    # 1. Insert 25 initial settled wagers dated 2026-10-10
    for i in range(25):
        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (
                ?, 'f_1', 'g_1', 'EPL', 'full_game', 'asian_lines', 'asian_handicap',
                'Asian Handicap', 'Arsenal -0.5', -0.5, 1.95, 0.60, 0.0,
                0.51, 0.17, 20, '2026-10-10T10:00:00Z', 'settled',
                '2026-10-10T12:00:00Z', 'won', 0.95, 0.16, 0.24, '{}', 'gap_market'
            )
        """, (f"old_w_{i}",))

    # Mark as graduated in SQLite and JSON
    fake_grad = {
        seg_key: {
            'id': 'grad_epl_asian',
            'segment_key': seg_key,
            'league': 'EPL',
            'scope': 'full_game',
            'family': 'asian_lines',
            'sub_family': 'asian_handicap',
            'stats': {'roi': 0.10}
        }
    }
    save_graduated_markets(fake_grad, incubator.data_dir)
    cur.execute("""
        INSERT INTO graduated_markets (
            id, segment_key, league, scope, family, sub_family, graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
        ) VALUES ('grad_epl_asian', ?, 'EPL', 'full_game', 'asian_lines', 'asian_handicap', '2026-10-10T12:00:00Z', 25, 0.10, 0.01, 0.02, 0.65, '{}')
    """, (seg_key,))
    conn.commit()

    # 2. Simulate 6 live losses causing demotion on 2026-10-15
    eng_db = tmp_path / 'engine.sqlite3'
    conn_eng = sqlite3.connect(eng_db)
    conn_eng.execute("""
        CREATE TABLE system_decisions (
            id TEXT PRIMARY KEY, payload TEXT, status TEXT, pnl REAL, issued_at TEXT
        )
    """)
    for i in range(6):
        conn_eng.execute("INSERT INTO system_decisions VALUES (?, ?, 'lost', -1.0, ?)",
                         (f"d_{i}", json.dumps({'league': 'EPL', 'market': 'Asian Handicap', 'stake': 1.0}),
                          f"2026-10-15T12:0{i}:00Z"))
    conn_eng.commit()
    conn_eng.close()

    # Run demotion
    dem_res = incubator.evaluate_demotions_and_substitutions(engine_db_path=eng_db, as_of="2026-10-15T14:00:00Z")
    assert len(dem_res['demotions']) == 1
    assert dem_res['demotions'][0]['segment_key'] == seg_key
    assert seg_key not in load_graduated_markets(incubator.data_dir)

    # 3. Call evaluate_graduation(): old wagers are from 2026-10-10 (before demotion 2026-10-15),
    # so probation guard prevents re-graduation!
    regrad_fail = incubator.evaluate_graduation(as_of="2026-10-15T15:00:00Z")
    assert len(regrad_fail) == 0
    assert seg_key not in load_graduated_markets(incubator.data_dir)

    # 4. Now simulate AI repair + new post-demotion wagers captured and settled on 2026-10-16
    for i in range(25):
        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (
                ?, 'f_2', 'g_2', 'EPL', 'full_game', 'asian_lines', 'asian_handicap',
                'Asian Handicap', 'Arsenal -0.5', -0.5, 1.95, 0.65, 0.0,
                0.51, 0.26, 20, '2026-10-16T10:00:00Z', 'settled',
                '2026-10-16T12:00:00Z', 'won', 0.95, 0.12, 0.24, '{}', 'gap_market'
            )
        """, (f"new_w_{i}",))
    conn.commit()

    # 5. Evaluate graduation again: post-demotion wagers qualify and pass Crucible!
    regrad_success = incubator.evaluate_graduation(as_of="2026-10-16T14:00:00Z")
    assert len(regrad_success) == 1
    assert regrad_success[0]['segment_key'] == seg_key

    # Verify it is back in active graduated markets
    grads_restored = load_graduated_markets(incubator.data_dir)
    assert seg_key in grads_restored

    # Verify it has been cleared from demoted_markets table
    cur.execute("SELECT count(*) FROM demoted_markets WHERE segment_key = ?", (seg_key,))
    assert cur.fetchone()[0] == 0
    conn.close()


def test_price_and_settle_expanded_derivatives(incubator):
    # Test soccer model derivatives
    game_epl = Game(
        id='EPL:201', league='EPL', start='2026-10-25T15:00:00Z',
        home='Arsenal', away='Chelsea', home_id='ARS', away_id='CHE',
        home_abbr='ARS', away_abbr='CHE',
        state='scheduled', game_type='regular', season=2026
    )

    from betting_engine.soccer_model import SoccerModel
    sm = SoccerModel()
    fc_epl = SimpleNamespace(soccer_model=sm, home_points=1.8, away_points=1.1, home_probability=0.55, away_probability=0.22, draw_probability=0.23)

    # 1. 1x2 & BTTS
    c_btts_combo = DecomposedContract(
        raw_market_name="1x2 & Both Teams to Score", league="EPL", fixture_id="201",
        scope="full_game", family="bivariate_derivatives", sub_family="btts_combo",
        mathematical_model="dixon_coles_bivariate", selection="1 & Yes", odds=3.2
    )
    p_btts, push_btts = incubator.price_contract(c_btts_combo, game_epl, fc_epl)
    assert 0.10 < p_btts < 0.40
    assert push_btts == 0.0

    # 2. Goal Range
    c_gr = DecomposedContract(
        raw_market_name="Goal Range", league="EPL", fixture_id="201",
        scope="full_game", family="bivariate_derivatives", sub_family="goal_range",
        mathematical_model="dixon_coles_bivariate", selection="2-3", odds=2.0
    )
    p_gr, push_gr = incubator.price_contract(c_gr, game_epl, fc_epl)
    assert 0.30 < p_gr < 0.65
    assert push_gr == 0.0

    # 3. Basketball Winning Margin
    game_nba = Game(
        id='NBA:301', league='NBA', start='2026-10-25T19:00:00Z',
        home='Detroit Pistons', away='Boston Celtics', home_id='DET', away_id='BOS',
        home_abbr='DET', away_abbr='BOS',
        state='scheduled', game_type='regular', season=2027
    )
    fc_nba = SimpleNamespace(margin=4.5, home_points=112.0, away_points=107.5)

    c_wm = DecomposedContract(
        raw_market_name="Winning Margin (Incl. Overtime)", league="NBA", fixture_id="301",
        scope="full_game", family="american_derivatives", sub_family="winning_margin",
        mathematical_model="empirical_score_margin", selection="Detroit Pistons 1-5", side="Detroit Pistons 1-5", odds=4.5
    )
    p_wm, push_wm = incubator.price_contract(c_wm, game_nba, fc_nba)
    assert 0.10 < p_wm < 0.30
    assert push_wm == 0.0

    # 4. Basketball Overtime
    c_ot = DecomposedContract(
        raw_market_name="Will There be Overtime", league="NBA", fixture_id="301",
        scope="full_game", family="american_derivatives", sub_family="overtime",
        mathematical_model="empirical_regulation_tie", selection="Yes", side="yes", odds=12.0
    )
    p_ot, push_ot = incubator.price_contract(c_ot, game_nba, fc_nba)
    assert 0.02 < p_ot < 0.10
    assert push_ot == 0.0


def test_enforce_minimum_league_quotas(incubator, tmp_path):
    from src.betting_engine.shadow_market_incubator import load_graduated_markets, MIN_LEAGUE_GRADUATED_QUOTA

    conn = incubator._get_connection()
    cur = conn.cursor()

    # Insert 12 settled wagers for NBA Away Total with positive edge
    # 10 wins (odds 1.80, profit 0.80), 2 losses (-1.00)
    # Total stake = 12.0
    # Profit = 10 * 0.80 - 2 * 1.0 = 8.0 - 2.0 = +6.00 -> ROI = +50.0%
    for i in range(12):
        w_id = f"nba_quota_wager_{i}"
        is_win = (i < 10)
        res = 'won' if is_win else 'lost'
        pnl = 0.80 if is_win else -1.0
        b_model = (0.65 - (1.0 if is_win else 0.0)) ** 2
        b_mkt = (0.555 - (1.0 if is_win else 0.0)) ** 2

        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (?, 'nba_fix', ?, 'NBA', 'full_game', 'away_total', 'away_total',
                      'Away Total', 'Over 108.5', 108.5, 1.80, 0.65, 0.0,
                      0.555, 0.17, 10, '2026-10-10T12:00:00Z', 'settled',
                      '2026-10-10T14:00:00Z', ?, ?, ?, ?, '{}', 'high_probability')
        """, (w_id, f"nba_game_{i}", res, pnl, b_model, b_mkt))

    conn.commit()
    conn.close()

    # Enforce minimum quota of 1 for NBA
    quota_grads = incubator.enforce_minimum_league_quotas(min_quota=1, min_sample_size=10)
    assert len(quota_grads) == 1
    g = quota_grads[0]
    assert g['segment_key'] == 'NBA|full_game|away_total'
    assert g['league'] == 'NBA'
    assert g['stats']['quota_expansion_triggered'] is True
    assert g['stats']['roi'] > 0.03
    assert g['stats']['total_pnl'] > 0.0

    # Verify persistence
    loaded = load_graduated_markets(tmp_path)
    assert 'NBA|full_game|away_total' in loaded

    # Subsequent call with min_quota=1 sees quota already fulfilled and returns 0 new
    subsequent = incubator.enforce_minimum_league_quotas(min_quota=1, min_sample_size=10)
    assert len(subsequent) == 0


def test_enforce_unlimited_proven_graduation_zero_rationing(incubator, tmp_path):
    """Verifies that the graduation mandate enforces UNLIMITED maximum ceiling and zero rationing:
    every single candidate that demonstrates statistical proof (N>=10, PnL>0, ROI>=1.5%)
    graduates automatically without being capped or truncated.
    """
    from src.betting_engine.shadow_market_incubator import load_graduated_markets

    conn = incubator._get_connection()
    cur = conn.cursor()

    # Insert 3 separate qualifying submarket families for NFL
    test_families = [
        ('quarter_4', 'american_derivatives', 'spreads', 'Q4 Spread', 12, 10, 0.85, 2.0),
        ('half_2', 'american_derivatives', 'moneyline', '2H Moneyline', 14, 11, 0.90, 2.1),
        ('half_2', 'american_derivatives', 'totals', '2H Total', 15, 12, 0.80, 1.95),
    ]

    for scope, fam, sub, raw_name, n_wagers, n_wins, profit_per_win, odds in test_families:
        for i in range(n_wagers):
            w_id = f"nfl_cand_{scope}_{sub}_{i}"
            is_win = (i < n_wins)
            res = 'won' if is_win else 'lost'
            pnl = profit_per_win if is_win else -1.0
            b_model = (0.60 - (1.0 if is_win else 0.0)) ** 2
            b_mkt = (0.50 - (1.0 if is_win else 0.0)) ** 2

            cur.execute("""
                INSERT INTO incubated_wagers (
                    id, fixture_id, game_id, league, scope, family, sub_family,
                    raw_market_name, selection, line, odds, model_prob, push_prob,
                    market_prob, ev, dynamic_sample_power_req, captured_at, status,
                    settled_at, result, pnl, brier_model, brier_market, metadata, cohort
                ) VALUES (?, 'nfl_fix_unlim', ?, 'NFL', ?, ?, ?,
                          ?, 'Test Sel', 0.0, ?, 0.60, 0.0,
                          0.50, 0.15, 10, '2026-10-15T12:00:00Z', 'settled',
                          '2026-10-15T16:00:00Z', ?, ?, ?, ?, '{}', 'gap_market')
            """, (w_id, f"nfl_game_{scope}_{sub}_{i}", scope, fam, sub, raw_name, odds, res, pnl, b_model, b_mkt))

    conn.commit()
    conn.close()

    # Call enforce_unlimited_proven_graduation() with min_sample_size=10
    graduated = incubator.enforce_unlimited_proven_graduation(min_sample_size=10)

    # Zero Rationing: ALL 3 qualifying families MUST graduate!
    assert len(graduated) == 3
    grad_keys = {g['segment_key'] for g in graduated}
    assert 'NFL|quarter_4|american_derivatives' in grad_keys or 'NFL|quarter_4|american_derivatives:spreads' in grad_keys
    for g in graduated:
        assert g['stats']['unlimited_proven_graduation'] is True
        assert g['stats']['total_pnl'] > 0
        assert g['stats']['roi'] >= 0.015
        assert g['league'] == 'NFL'

    # Verify persistence in graduated_markets.json
    loaded = load_graduated_markets(tmp_path)
    for g in graduated:
        assert g['segment_key'] in loaded

    # Subsequent execution produces 0 duplicates
    second_run = incubator.enforce_unlimited_proven_graduation(min_sample_size=10)
    assert len(second_run) == 0


def test_diagnose_and_repair_stuck_incubator_markets_lifecycle(incubator, tmp_path):
    """Verifies that:
    1. A failing/stuck market in the incubator (N >= 20, ROI < 0) is diagnosed by R&D.
    2. Failure mode and actionable remedy are assigned and recorded in incubator_diagnostic_repairs.
    3. Old losing wagers are quarantined behind recalibrated_at.
    4. Post-recalibration winning wagers allow the market to graduate and clear the repair record.
    """
    conn = incubator._get_connection()
    cur = conn.cursor()

    seg_key = 'EPL|half_2|asian_lines:asian_total'

    # Insert 25 losing wagers dated 2026-10-10
    for i in range(25):
        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (
                ?, 'f_old', 'g_old', 'EPL', 'half_2', 'asian_lines', 'asian_total',
                '2nd Half Asian Total', 'Over 1.5', 1.5, 1.90, 0.65, 0.0,
                0.52, 0.23, 20, '2026-10-10T10:00:00Z', 'settled',
                '2026-10-10T12:00:00Z', 'lost', -1.0, 0.35, 0.24, '{}', 'gap_market'
            )
        """, (f"stuck_w_{i}",))
    conn.commit()

    # 1. Diagnose and repair stuck incubator markets
    repairs = incubator.diagnose_and_repair_stuck_incubator_markets(min_samples=20, as_of="2026-10-11T12:00:00Z")
    assert len(repairs) >= 1
    repaired_keys = [r['segment_key'] for r in repairs]
    assert seg_key in repaired_keys

    # Check failure mode attribution
    target_repair = next(r for r in repairs if r['segment_key'] == seg_key)
    assert target_repair['failure_mode'] == 'SCOPE_TOTALS_MISMATCH'
    assert 'Half-time goal scaling deployed' in target_repair['remedy']

    # 2. evaluate_graduation() cannot graduate on old wagers prior to recalibrated_at
    grad_fail = incubator.evaluate_graduation(as_of="2026-10-11T14:00:00Z")
    assert not any(g['segment_key'] == seg_key for g in grad_fail)

    # 3. Simulate post-recalibration wagers with the repaired half-time model
    for i in range(25):
        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (
                ?, 'f_new', 'g_new', 'EPL', 'half_2', 'asian_lines', 'asian_total',
                '2nd Half Asian Total', 'Under 1.5', 1.5, 1.95, 0.65, 0.0,
                0.51, 0.26, 20, '2026-10-12T10:00:00Z', 'settled',
                '2026-10-12T12:00:00Z', 'won', 0.95, 0.12, 0.24, '{}', 'gap_market'
            )
        """, (f"fixed_w_{i}",))
    conn.commit()

    # 4. evaluate_graduation() succeeds for the repaired market
    grad_success = incubator.evaluate_graduation(as_of="2026-10-12T14:00:00Z")
    assert any(g['segment_key'] in (seg_key, 'EPL|half_2|asian_lines') for g in grad_success)

    # 5. Cleared from incubator_diagnostic_repairs table upon graduation
    cur.execute("SELECT count(*) FROM incubator_diagnostic_repairs WHERE segment_key IN (?, 'EPL|half_2|asian_lines')", (seg_key,))
    assert cur.fetchone()[0] == 0
    conn.close()


def test_soccer_half_scaled_pricing_in_incubator(incubator):
    """Verifies that soccer half derivative contracts are priced with scope-scaled expected goals."""
    from betting_engine.domain import Game
    from betting_engine.market_decomposer import DecomposedContract

    class DummySoccerModel:
        def compute_expected_goals(self, home, away):
            return 1.8, 1.2
        def predict_asian_handicap(self, home, away, line, side='home'):
            return {'win': 0.60, 'push': 0.10}
        def predict_totals(self, home, away, line=2.5):
            return {'over': 0.70, 'under': 0.30, 'push': 0.0}

    class DummyForecast:
        soccer_model = DummySoccerModel()
        home_points = 1.8
        away_points = 1.2

    game = Game(
        id='g_test', league='EPL', home='Arsenal', away='Chelsea', start='2026-10-25T15:00:00Z',
        home_id='ARS', away_id='CHE', home_abbr='ARS', away_abbr='CHE',
        state='scheduled', game_type='regular', season=2026
    )

    # Full game contract
    full_contract = DecomposedContract(
        raw_market_name='Asian Total', league='EPL', fixture_id='f_1', scope='full_game',
        family='asian_lines', sub_family='asian_total', mathematical_model='bivariate_poisson',
        selection='Over 2.5', side='over', line=2.5, odds=1.90
    )
    p_win_full, p_push_full = incubator.price_contract(full_contract, game, DummyForecast())
    assert p_win_full == 0.70

    # Half 1 contract: MUST use half-time score matrix (not full game 70%!)
    half_contract = DecomposedContract(
        raw_market_name='1st Half Asian Total', league='EPL', fixture_id='f_1', scope='half_1',
        family='asian_lines', sub_family='asian_total', mathematical_model='bivariate_poisson',
        selection='Over 1.5', side='over', line=1.5, odds=1.90
    )
    p_win_h1, p_push_h1 = incubator.price_contract(half_contract, game, DummyForecast())
    # In the 1st half (scale 0.45 * 3.0 = 1.35 goals), Over 1.5 probability is ~38%, much lower than full game 70%!
    assert p_win_h1 < 0.50
    assert p_win_h1 > 0.20

    # Sequence hazard for 1st half:
    seq_contract = DecomposedContract(
        raw_market_name='1st Half First Goal', league='EPL', fixture_id='f_1', scope='half_1',
        family='sequence_hazard', sub_family='first_to_score', mathematical_model='poisson_hazard',
        selection='No Goal', side='no_goal', odds=3.50
    )
    p_win_seq, _ = incubator.price_contract(seq_contract, game, DummyForecast())
    # 0 goals in 1st half is around ~26%, not full-game 5%!
    assert p_win_seq > 0.20
    assert p_win_seq < 0.35


def test_dual_track_core_full_game_market_demotion_and_regraduation(incubator, tmp_path):
    """Verifies that Core Full-Game Markets go through the Dual-Track Incubator:
    1. A degrading core market (e.g. NFL|full_game|spreads) is detected, demoted, and clamped.
    2. It is inserted into demoted_markets and enqueued for Quant Council repair.
    3. It accumulates shadow challenger candidates in incubated_wagers under cohort='core_challenger'.
    4. Once post-demotion wagers prove positive alpha in the Crucible, it re-graduates and lifts the demotion.
    """
    from src.betting_engine.shadow_market_incubator import (
        load_graduated_markets,
        load_demoted_markets,
        get_demoted_segments
    )

    eng_db = tmp_path / 'engine.sqlite3'
    conn_eng = sqlite3.connect(eng_db)
    conn_eng.execute("""
        CREATE TABLE system_decisions (
            id TEXT PRIMARY KEY, payload TEXT, status TEXT, pnl REAL, issued_at TEXT
        )
    """)
    # 6 settled losses in NFL spreads (core full-game market not previously graduated)
    for i in range(6):
        p = {
            'league': 'NFL', 'market': 'spreads', 'model_scope': 'full_game',
            'stake': 1.0, 'selection': 'Chiefs -3.5', 'game_id': f'nfl_{i}'
        }
        conn_eng.execute("INSERT INTO system_decisions VALUES (?, ?, 'lost', -1.0, ?)",
                         (f"d_nfl_{i}", json.dumps(p), f"2026-10-15T12:0{i}:00Z"))
    conn_eng.commit()
    conn_eng.close()

    # Step 1: Run evaluate_demotions_and_substitutions
    dem_res = incubator.evaluate_demotions_and_substitutions(engine_db_path=eng_db, as_of="2026-10-15T14:00:00Z")
    assert any(d['segment_key'] == 'NFL|full_game|spreads' for d in dem_res['demotions'])
    target_dem = next(d for d in dem_res['demotions'] if d['segment_key'] == 'NFL|full_game|spreads')
    assert target_dem['is_core'] is True
    assert target_dem['rolling_pnl'] == -6.0

    # Step 2: Check demoted_markets table and get_demoted_segments
    demoted = get_demoted_segments(incubator.data_dir)
    assert 'NFL|full_game|spreads' in demoted

    # Step 3: Candidate incubation accepts demoted candidates under cohort='core_challenger'
    cand = {
        'id': 'cand_nfl_1', 'game_id': 'nfl_game_100', 'league': 'NFL',
        'market': 'spreads', 'model_scope': 'full_game', 'selection': 'Bills -2.5',
        'probability': 0.52, 'expected_roi': 0.02, 'reference_odds': 1.95,
        'description': 'Handicap (Incl. Overtime)'
    }
    wagers = incubator.incubate_high_probability_candidates([cand], as_of="2026-10-16T10:00:00Z")
    assert len(wagers) == 1
    assert wagers[0]['cohort'] == 'core_challenger'

    # Step 4: Simulate 25 winning post-demotion wagers
    conn = incubator._get_connection()
    cur = conn.cursor()
    for i in range(25):
        cur.execute("""
            INSERT INTO incubated_wagers (
                id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, line, odds, model_prob, push_prob,
                market_prob, ev, dynamic_sample_power_req, captured_at, status,
                settled_at, result, pnl, brier_model, brier_market, metadata, cohort
            ) VALUES (
                ?, 'fix_nfl', 'g_nfl_post', 'NFL', 'full_game', 'spreads', 'spreads',
                'Handicap', 'Chiefs -3.5', -3.5, 1.95, 0.60, 0.0,
                0.51, 0.17, 20, '2026-10-16T12:00:00Z', 'settled',
                '2026-10-16T15:00:00Z', 'won', 0.95, 0.15, 0.25, '{}', 'core_challenger'
            )
        """, (f"post_dem_wager_{i}",))
    conn.commit()
    conn.close()

    # Step 5: evaluate_graduation() graduates the repaired core challenger model
    grads = incubator.evaluate_graduation(as_of="2026-10-16T16:00:00Z")
    assert any(g['segment_key'] == 'NFL|full_game|spreads' for g in grads)
    target_grad = next(g for g in grads if g['segment_key'] == 'NFL|full_game|spreads')
    assert target_grad['initial_tier'] == 'SOLID_ALPHA'

    # Step 6: Verified cleared from demoted_markets and present in graduated_markets
    demoted_after = get_demoted_segments(incubator.data_dir)
    assert 'NFL|full_game|spreads' not in demoted_after

    graduated_after = load_graduated_markets(incubator.data_dir)
    assert 'NFL|full_game|spreads' in graduated_after


def test_core_full_game_market_crucible_graduation(incubator, tmp_path):
    """Verifies that Core Full-Game Markets with proven live positive alpha
    graduate into graduated_markets.json as Diplomás Főpiacok.
    """
    from src.betting_engine.shadow_market_incubator import load_graduated_markets
    from src.betting_engine.markets import _market_is_graduated

    eng_db = incubator.data_dir / 'engine.sqlite3'
    conn_eng = sqlite3.connect(eng_db)
    conn_eng.execute("""
        CREATE TABLE system_decisions (
            family TEXT, tip_id TEXT, issued_at TEXT, payload TEXT, status TEXT, pnl REAL, outcome_evidence TEXT
        )
    """)
    # Insert 26 settled live decisions in WTA full_game moneyline with high positive alpha
    for i in range(26):
        is_win = i < 18
        pnl = 0.90 if is_win else -1.0
        p = {
            'league': 'WTA', 'market': 'moneyline', 'model_scope': 'full_game',
            'stake': 1.0, 'selection': f'Player_{i}', 'game_id': f'wta_{i}',
            'reference_odds': 1.90
        }
        conn_eng.execute(
            "INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            ('moneyline', f'tip_{i}', f'2026-10-18T10:{i:02d}:00Z', json.dumps(p), 'won' if is_win else 'lost', pnl, '{}')
        )
    conn_eng.commit()
    conn_eng.close()

    # Run evaluate_graduation
    grads = incubator.evaluate_graduation(as_of="2026-10-18T12:00:00Z")
    assert any(g['segment_key'] == 'WTA|full_game|moneyline' for g in grads)
    target_grad = next(g for g in grads if g['segment_key'] == 'WTA|full_game|moneyline')
    assert target_grad['scope'] == 'full_game'
    assert target_grad['family'] == 'moneyline'
    assert target_grad['initial_tier'] == 'SUPER_ALPHA'
    assert target_grad['stats']['is_core'] is True
    assert target_grad['stats']['roi'] > 0.10

    # Verify present in graduated_markets.json
    graduated = load_graduated_markets(incubator.data_dir)
    assert 'WTA|full_game|moneyline' in graduated

    # Verify _market_is_graduated in markets.py returns True for this core market
    g_meta = graduated['WTA|full_game|moneyline']
    assert _market_is_graduated(g_meta, 'WTA', 'full_game', 'moneyline', 'moneyline')


def test_candidate_models_receive_enriched_market_context(incubator):
    """Verifies candidate models receive projected points, features, and rest context."""
    from betting_engine.sandbox_models.archetypes.negative_binomial import NegativeBinomialOverdispersionModel
    from betting_engine.tournament_engine import TournamentEngine

    t_eng = TournamentEngine(data_dir=incubator.data_dir)
    incubator._tournament_engine = t_eng

    # Register candidate model in tournament
    cand = NegativeBinomialOverdispersionModel(
        model_id="test_negbin_ctx",
        league="NHL",
        scope="full_game",
        family="totals",
        dispersion_alpha=0.20
    )
    ok, errors = t_eng.register_candidate("NHL|full_game|totals", cand)
    assert ok, f"Candidate registration failed: {errors}"

    game = Game(
        id='NHL:999', league='NHL', start='2026-10-25T19:00:00Z',
        home='Edmonton Oilers', away='Calgary Flames',
        home_id='EDM', away_id='CGY', home_abbr='EDM', away_abbr='CGY',
        state='scheduled', game_type='regular', season=2026
    )

    forecast = SimpleNamespace(
        home_points=3.85,
        away_points=2.65,
        features={'home_rest': 2.0, 'away_rest': 0.0}
    )

    raw_markets = [{
        'name': 'Total Goals',
        'outcomes': [
            {'name': 'Over 6.5', 'price': 2.10},
            {'name': 'Under 6.5', 'price': 1.80}
        ]
    }]

    # Run incubate_fixture_gap_markets
    incubator.incubate_fixture_gap_markets(
        raw_markets=raw_markets,
        game=game,
        league='NHL',
        forecast=forecast
    )

    # Verify that incubated_wagers contains the candidate wager priced with dynamic context
    conn = incubator._get_connection()
    c_wagers = conn.execute(
        "SELECT model_id, model_prob, ev FROM incubated_wagers WHERE model_id = 'test_negbin_ctx'"
    ).fetchall()
    conn.close()

    assert len(c_wagers) > 0, "Candidate model should have generated incubated wagers"
    # Over 6.5 with mu = 3.85 + 2.65 = 6.50 should have a valid model probability
    assert c_wagers[0][1] > 0.0









