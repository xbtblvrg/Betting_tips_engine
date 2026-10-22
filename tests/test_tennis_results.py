"""Unit tests for the independent tennis result recovery and settlement engine."""
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from src.betting_engine.domain import Game
from src.betting_engine.contracts import settlement_scores, contract_value
from src.betting_engine.tennis_results import (
    _norm_tokens,
    _players_match,
    fetch_espn_linescores,
    recover_pending_tennis,
)


def test_norm_tokens_and_player_matching():
    """Verify player token extraction and direct / reversed matching."""
    tokens = _norm_tokens("Coppejans, Kimmer")
    assert "coppejans" in tokens
    assert "kimmer" in tokens

    # Direct match
    res_direct = _players_match("Fabian Marozsan", "Zachary Svajda", "Fabian Marozsan", "Zachary Svajda")
    assert res_direct == (True, 'direct')

    # Reversed order match
    res_rev = _players_match("Coppejans, Kimmer", "Stefanos Tsitsipas", "Stefanos Tsitsipas", "Kimmer Coppejans")
    assert res_rev == (False, 'reversed')

    # Disjoint players
    res_none = _players_match("Carlos Alcaraz", "Jannik Sinner", "Novak Djokovic", "Daniil Medvedev")
    assert res_none is None


def test_tennis_settlement_scores_scopes():
    """Verify that settlement_scores dispatches sets for moneyline and games for spreads/totals."""
    game = Game(
        id='ATP:test_match_1',
        league='ATP',
        start='2026-10-08T05:00:00+00:00',
        home='Player A',
        away='Player B',
        home_id='ATP:test_match_1:A',
        away_id='ATP:test_match_1:B',
        home_abbr='PLA',
        away_abbr='PLB',
        state='final',
        season=2026,
        home_score=15.0,
        away_score=13.0,
        context={
            'tennis': True,
            'sets_home': 2.0,
            'sets_away': 1.0,
            'games_home': 15.0,
            'games_away': 13.0,
            'set_scores': [(6, 4), (3, 6), (6, 3)],
        }
    )

    # 1. Moneyline settlement must use sets
    hs_ml, as_ml, ev_ml = settlement_scores({'market': 'moneyline'}, game)
    assert hs_ml == 2.0
    assert as_ml == 1.0
    val_ml = contract_value('moneyline', 'home', None, hs_ml, as_ml)
    assert val_ml > 0  # Player A won 2 sets to 1

    # 2. Spreads settlement must use total games
    hs_sp, as_sp, ev_sp = settlement_scores({'market': 'spreads'}, game)
    assert hs_sp == 15.0
    assert as_sp == 13.0
    val_sp = contract_value('spreads', 'home', -1.5, hs_sp, as_sp)
    assert val_sp > 0  # (15 - 13) - 1.5 = +0.5 > 0 (Player A covered -1.5 games)

    # 3. Totals settlement must use total games
    hs_tot, as_tot, ev_tot = settlement_scores({'market': 'totals'}, game)
    assert hs_tot == 15.0
    assert as_tot == 13.0
    val_tot_over = contract_value('totals', 'over', 27.5, hs_tot, as_tot)
    assert val_tot_over > 0  # 15 + 13 = 28 > 27.5 (Over 27.5 won)


def test_recover_pending_tennis_mock(tmp_path):
    """Verify end-to-end recovery of pending tennis games with mock archive."""
    gid = 'ATP:mock_test_fixture_100'
    mock_archive = {
        gid: {
            'source': 'test_archive',
            'league': 'ATP',
            'p1': 'Novak Djokovic',
            'p2': 'Carlos Alcaraz',
            'w1': True,
            'w2': False,
            'sets1': 2,
            'sets2': 1,
            'games1': 16,
            'games2': 14,
            'set_scores': [(7, 6), (3, 6), (6, 2)],
            'is_p1_home': True,
            'sets_home': 2,
            'sets_away': 1,
            'games_home': 16,
            'games_away': 14,
            'url': 'https://test.tennis/mock_match',
        }
    }
    (tmp_path / 'tennis_results_archive.json').write_text(json.dumps(mock_archive))

    pending_decisions = [{
        'status': 'open',
        'payload': {
            'game_id': gid,
            'league': 'ATP',
            'home': 'Novak Djokovic',
            'away': 'Carlos Alcaraz',
            'start': '2026-10-08T02:00:00+00:00',
            'market': 'moneyline',
            'side': 'home',
            'reference_odds': 1.85,
            'stake': 5.0,
        }
    }]

    recovered_games, status = recover_pending_tennis([], [], pending_decisions, data_dir=tmp_path)
    assert status['recovered_games'] == 1
    assert len(recovered_games) == 1

    g = recovered_games[0]
    assert g.id == gid
    assert g.state == 'final'
    assert g.home_score == 16.0
    assert g.away_score == 14.0
    assert g.context['sets_home'] == 2
    assert g.context['sets_away'] == 1
    assert g.context['tennis'] is True
