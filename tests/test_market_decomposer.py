"""Unit tests for Dynamic Market Decomposer."""
import pytest
from betting_engine.market_decomposer import extract_line, decompose_market, DecomposedContract


def test_extract_line():
    assert extract_line("Asian Total", specifiers="total=2.25") == 2.25
    assert extract_line("Asian Handicap", specifiers="hcp=-0.75") == -0.75
    assert extract_line("Handicap", outcome_name="Team A (+1.5)") == 1.5
    assert extract_line("Total Games 9.5", outcome_name="Over 9.5") == 9.5
    assert extract_line("Winner") is None


def test_decompose_asian_handicap():
    market = {
        'name': 'Asian Handicap',
        'specifiers': 'hcp=-0.75',
        'outcomes': [
            {'name': 'Arsenal (-0.75)', 'odds': 1.95},
            {'name': 'Chelsea (+0.75)', 'odds': 1.85}
        ]
    }
    contracts = decompose_market(market, league='EPL', fixture_id='fix_123')
    assert len(contracts) == 2
    c0 = contracts[0]
    assert c0.family == 'asian_lines'
    assert c0.sub_family == 'asian_handicap'
    assert c0.line == -0.75
    assert c0.side == 'home'
    assert c0.odds == 1.95
    assert c0.metadata.get('is_quarter_line') is True


def test_decompose_asian_total():
    market = {
        'name': 'Asian Total',
        'specifiers': 'total=2.75',
        'outcomes': [
            {'name': 'Over (2.75)', 'odds': 2.05},
            {'name': 'Under (2.75)', 'odds': 1.80}
        ]
    }
    contracts = decompose_market(market, league='EPL', fixture_id='fix_123')
    assert len(contracts) == 2
    assert contracts[0].sub_family == 'asian_total'
    assert contracts[0].side == 'over'
    assert contracts[0].line == 2.75
    assert contracts[1].side == 'under'


def test_decompose_tennis_set_markets():
    # 1st Set Winner
    market_win = {
        'name': '1st Set - Winner',
        'outcomes': [
            {'name': 'Alcaraz', 'odds': 1.50},
            {'name': 'Sinner', 'odds': 2.60}
        ]
    }
    contracts = decompose_market(market_win, league='ATP', fixture_id='ten_1')
    assert len(contracts) == 2
    assert contracts[0].family == 'tennis_sets'
    assert contracts[0].sub_family == 'set_winner'
    assert contracts[0].scope == 'period_1'

    # 1st Set Total Games
    market_tot = {
        'name': '1st Set - Total Games',
        'specifiers': 'total=9.5',
        'outcomes': [
            {'name': 'Over 9.5', 'odds': 1.85},
            {'name': 'Under 9.5', 'odds': 1.95}
        ]
    }
    contracts_tot = decompose_market(market_tot, league='ATP', fixture_id='ten_1')
    assert len(contracts_tot) == 2
    assert contracts_tot[0].sub_family == 'set_totals'
    assert contracts_tot[0].line == 9.5


def test_decompose_bivariate_and_sequence():
    # Both Teams to Score
    market_btts = {
        'name': 'Both Teams to Score',
        'outcomes': [
            {'name': 'Yes', 'odds': 1.75},
            {'name': 'No', 'odds': 2.10}
        ]
    }
    contracts = decompose_market(market_btts, league='EPL', fixture_id='fix_btts')
    assert len(contracts) == 2
    assert contracts[0].family == 'bivariate_derivatives'
    assert contracts[0].sub_family == 'both_teams_score'

    # First to score
    market_first = {
        'name': '1st Goal',
        'outcomes': [
            {'name': 'Arsenal', 'odds': 1.65},
            {'name': 'Chelsea', 'odds': 2.30},
            {'name': 'No Goal', 'odds': 8.50}
        ]
    }
    contracts_first = decompose_market(market_first, league='EPL', fixture_id='fix_first')
    assert len(contracts_first) == 3
    assert contracts_first[0].family == 'sequence_hazard'
    assert contracts_first[0].sub_family == 'first_to_score'


def test_decompose_expanded_derivatives():
    # Winning margin
    m_wm = {
        'name': 'Winning Margin (Incl. Overtime)',
        'outcomes': [
            {'name': 'Detroit Pistons 1-5', 'odds': 4.5},
            {'name': 'Boston Celtics 6-10', 'odds': 3.8}
        ]
    }
    c_wm = decompose_market(m_wm, league='NBA', fixture_id='nba_1')
    assert len(c_wm) == 2
    assert c_wm[0].family == 'american_derivatives'
    assert c_wm[0].sub_family == 'winning_margin'

    # Overtime
    m_ot = {
        'name': 'Will There be Overtime',
        'outcomes': [{'name': 'Yes', 'odds': 12.0}, {'name': 'No', 'odds': 1.05}]
    }
    c_ot = decompose_market(m_ot, league='NBA', fixture_id='nba_1')
    assert len(c_ot) == 2
    assert c_ot[0].sub_family == 'overtime'
    assert c_ot[0].side == 'yes'

    # 1x2 & BTTS combo
    m_combo = {
        'name': '1x2 & Both Teams to Score',
        'outcomes': [
            {'name': '1 & Yes', 'odds': 3.2},
            {'name': '1 & No', 'odds': 2.8}
        ]
    }
    c_combo = decompose_market(m_combo, league='EPL', fixture_id='epl_1')
    assert len(c_combo) == 2
    assert c_combo[0].family == 'bivariate_derivatives'
    assert c_combo[0].sub_family == 'btts_combo'

    # Goal Range
    m_gr = {
        'name': 'Goal Range',
        'outcomes': [{'name': '0-1', 'odds': 3.5}, {'name': '2-3', 'odds': 2.0}]
    }
    c_gr = decompose_market(m_gr, league='EPL', fixture_id='epl_1')
    assert len(c_gr) == 2
    assert c_gr[0].sub_family == 'goal_range'

    # Double Result in Tennis
    m_dr = {
        'name': 'Double Result (1st Set/Match)',
        'outcomes': [{'name': 'Player A / Player A', 'odds': 1.6}, {'name': 'Player A / Player B', 'odds': 6.5}]
    }
    c_dr = decompose_market(m_dr, league='ATP', fixture_id='atp_1')
    assert len(c_dr) == 2
    assert c_dr[0].family == 'tennis_sets'
    assert c_dr[0].sub_family == 'double_result'


def test_decompose_excludes_statistical_props():
    """Verify that statistical props (corners, bookings, cards, touchdowns) are strictly excluded from score totals."""
    # Corners in soccer
    m_corners = {
        'name': '1st Half - Total Corners',
        'outcomes': [{'name': 'Under 4.5', 'odds': 1.88}, {'name': 'Over 4.5', 'odds': 1.95}]
    }
    assert decompose_market(m_corners, league='EPL', fixture_id='epl_1') == []

    # Bookings / cards in soccer
    m_bookings = {
        'name': 'Total Bookings',
        'outcomes': [{'name': 'Under 4.5', 'odds': 1.68}, {'name': 'Over 4.5', 'odds': 2.10}]
    }
    assert decompose_market(m_bookings, league='MLS', fixture_id='mls_1') == []

    # Touchdowns in NFL
    m_td = {
        'name': 'Total touchdowns (incl. overtime)',
        'outcomes': [{'name': 'Over 5.5', 'odds': 1.62}, {'name': 'Under 5.5', 'odds': 2.20}]
    }
    assert decompose_market(m_td, league='NFL', fixture_id='nfl_1') == []


