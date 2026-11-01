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


def test_tennis_walkover_detection_and_void_settlement(tmp_path):
    """Verify pre-match walkover / withdrawal voids all tickets as push (0.0 PnL) under Stake rules."""
    gid = 'WTA:mock_walkover_rybakina'
    mock_archive = {
        gid: {
            'source': 'tennisexplorer',
            'league': 'WTA',
            'p1': 'Fernandez L.',
            'p2': 'Rybakina E.',
            'w1': True,
            'w2': False,
            'sets1': 0,
            'sets2': 0,
            'games1': 0,
            'games2': 0,
            'set_scores': [],
            'is_p1_home': False,
            'sets_home': 0,
            'sets_away': 0,
            'games_home': 0,
            'games_away': 0,
            'is_walkover': True,
            'url': 'https://test.tennis/mock_walkover',
        }
    }
    (tmp_path / 'tennis_results_archive.json').write_text(json.dumps(mock_archive))

    pending_bets = [
        {
            'id': 101,
            'status': 'open',
            'payload': {
                'game_id': gid,
                'league': 'WTA',
                'home': 'Elena Rybakina',
                'away': 'Leylah Fernandez',
                'start': '2026-10-30T05:30:00+00:00',
                'market': 'period_1:totals',
                'side': 'under',
                'line': 9.5,
                'reference_odds': 1.85,
                'stake': 5.0,
                'ledger_contract': 'sportsbook',
                'platform': 'Stake.com',
            }
        },
        {
            'id': 102,
            'status': 'open',
            'payload': {
                'game_id': gid,
                'league': 'WTA',
                'home': 'Elena Rybakina',
                'away': 'Leylah Fernandez',
                'start': '2026-10-30T05:30:00+00:00',
                'market': 'period_2:moneyline',
                'side': 'away',
                'reference_odds': 2.10,
                'stake': 5.0,
                'ledger_contract': 'sportsbook',
                'platform': 'Stake.com',
            }
        }
    ]

    recovered_games, status = recover_pending_tennis([], pending_bets, [], data_dir=tmp_path)
    assert status['recovered_games'] == 1
    assert len(recovered_games) == 1

    g = recovered_games[0]
    assert g.id == gid
    assert g.state == 'walkover'
    assert g.home_score == 0.0
    assert g.away_score == 0.0
    assert g.context['is_walkover'] is True
    assert g.context['set_scores'] == []

    # Verify settlement_scores does not raise ValueError on period derivative scopes for walkovers
    hs_p1, as_p1, ev_p1 = settlement_scores({'market': 'period_1:totals'}, g)
    assert hs_p1 == 0.0 and as_p1 == 0.0
    assert ev_p1.get('is_walkover') is True

    hs_p2, as_p2, ev_p2 = settlement_scores({'market': 'period_2:moneyline'}, g)
    assert hs_p2 == 0.0 and as_p2 == 0.0
    assert ev_p2.get('is_walkover') is True

    # Test engine settlement
    from unittest.mock import MagicMock
    mock_store = MagicMock()
    mock_store.open_bets.return_value = [
        {
            'id': 101,
            'status': 'open',
            'odds': 1.85,
            'stake': 5.0,
            'platform': 'Stake.com',
            'payload': pending_bets[0]['payload'],
        },
        {
            'id': 102,
            'status': 'open',
            'odds': 2.10,
            'stake': 5.0,
            'platform': 'Stake.com',
            'payload': pending_bets[1]['payload'],
        },
    ]

    from src.betting_engine.engine import Engine
    eng = Engine.__new__(Engine)
    eng.store = mock_store
    eng.games = recovered_games

    eng.settle_bets()

    # Both bets must be settled as push with 0.0 PnL
    assert mock_store.settle.call_count == 2
    mock_store.settle.assert_any_call(101, 'push', 0.0, evidence=pytest.approx(mock_store.settle.call_args_list[0][1]['evidence']))
    assert mock_store.settle.call_args_list[0][0][1] == 'push'
    assert mock_store.settle.call_args_list[0][0][2] == 0.0
    assert mock_store.settle.call_args_list[1][0][1] == 'push'
    assert mock_store.settle.call_args_list[1][0][2] == 0.0


def test_tennisexplorer_tiebreak_score_parsing(monkeypatch):
    """Anti-regression test: TennisExplorer HTML with <sup>tiebreak</sup> must parse exact game counts without losing set scores."""
    from unittest.mock import patch, MagicMock
    from src.betting_engine.tennis_results import fetch_tennisexplorer_results

    html_fixture = '''
    <table class="result">
        <tr id="r12">
            <td class="first time">15:10</td>
            <td class="t-name"><a href="/player/vacherot/">Vacherot V.</a></td>
            <td class="result">2</td>
            <td class="score">7</td>
            <td class="score">6</td>
            <td class="score">&nbsp;</td>
        </tr>
        <tr id="r12b">
            <td class="t-name"><a href="/player/fritz-f1aa7/">Fritz T.</a> (6)</td>
            <td class="result">0</td>
            <td class="score">6<sup>5</sup></td>
            <td class="score">3</td>
            <td class="score">&nbsp;</td>
        </tr>
    </table>
    '''
    class DummyResp:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return html_fixture.encode('utf-8')

    with patch('urllib.request.urlopen', return_value=DummyResp()):
        results = fetch_tennisexplorer_results(days=('31',))
        vacherot = next((r for r in results if 'Vacherot' in r['p1']), None)
        assert vacherot is not None
        assert vacherot['sets1'] == 2 and vacherot['sets2'] == 0
        assert vacherot['games1'] == 13 and vacherot['games2'] == 9
        assert vacherot['set_scores'] == [(7, 6), (6, 3)]
        assert vacherot['is_walkover'] is False


def test_tennisexplorer_dynamic_date_handling():
    """Anti-regression test: TennisExplorer URL construction must handle month/year transitions dynamically."""
    from unittest.mock import patch
    import datetime
    from src.betting_engine.tennis_results import fetch_tennisexplorer_results

    captured_urls = []
    class DummyResp:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'<html></html>'

    def mock_urlopen(req, *args, **kwargs):
        captured_urls.append(req.full_url)
        return DummyResp()

    with patch('urllib.request.urlopen', side_effect=mock_urlopen):
        # Pass a date in November
        nov_date = datetime.date(2026, 11, 1)
        fetch_tennisexplorer_results(days=(nov_date,))
        
        assert len(captured_urls) > 0
        assert any('year=2026&month=11&day=01' in u for u in captured_urls)
        assert not any('month=10' in u for u in captured_urls)



