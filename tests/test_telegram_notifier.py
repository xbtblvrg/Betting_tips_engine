import pytest
from unittest.mock import patch, MagicMock
from betting_engine.telegram_notifier import (
    get_telegram_config,
    format_recommendation_message,
    notify_new_recommendations,
    send_telegram_message
)


def test_telegram_config_loaded():
    cfg = get_telegram_config()
    assert cfg['token'] is not None
    assert cfg['chat_id'] is not None
    assert '8338145462' in cfg['token']
    assert cfg['chat_id'] == '1031716246'


def test_format_recommendation_message():
    sample_tip = {
        'id': 'test_nfl_tip_001',
        'league': 'NFL',
        'away': 'Kansas City Chiefs',
        'home': 'Buffalo Bills',
        'start': '2026-10-10T17:00:00Z',
        'market': 'spreads',
        'market_display': 'Handicap (Incl. Overtime)',
        'selection': 'Kansas City Chiefs +2.5',
        'stake': 3.50,
        'reference_odds': 1.95,
        'minimum_odds': 1.82,
        'probability': 0.564,
        'expected_roi': 0.0998,
        'stress_roi': 0.045,
        'navigation': {'event_url': 'https://stake.com/sports/football/nfl/sample'},
        'game_context': {'polymarket': {'matched': True}}
    }
    msg = format_recommendation_message(sample_tip)
    assert 'NEW BET RECOMMENDATION' in msg
    assert 'Kansas City Chiefs @ Buffalo Bills' in msg
    assert 'Kansas City Chiefs +2.5' in msg
    assert '$3.50 USD' in msg
    assert 'ALPHA ACCELERATION' in msg
    assert 'https://stake.com/sports/football/nfl/sample' in msg


@patch('urllib.request.urlopen')
def test_notify_new_recommendations_deduplication(mock_urlopen, tmp_path, monkeypatch):
    test_persistence = tmp_path / 'notified_tips.json'
    monkeypatch.setattr('betting_engine.telegram_notifier.PERSISTENCE_PATH', test_persistence)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 999}}'
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    tips = [
        {'id': 'tip_001', 'stake': 2.50, 'selection': 'Under 6.5', 'league': 'NHL', 'away': 'A', 'home': 'B'},
        {'id': 'tip_002', 'stake': 1.00, 'selection': 'Home Win', 'league': 'NBA', 'away': 'C', 'home': 'D'},
        {'id': 'tip_unfunded', 'stake': 0.00, 'selection': 'Over 220', 'league': 'NBA', 'away': 'E', 'home': 'F'}
    ]

    # First run: 2 funded tips notified
    sent = notify_new_recommendations(tips)
    assert sent == 2
    assert mock_urlopen.call_count == 2

    # Second run with same tips: 0 sent (deduplication)
    sent_second = notify_new_recommendations(tips)
    assert sent_second == 0
    assert mock_urlopen.call_count == 2  # No new network calls


@patch('urllib.request.urlopen')
def test_notify_system_issues_alerting(mock_urlopen, tmp_path, monkeypatch):
    from betting_engine.telegram_notifier import notify_system_issues
    test_persistence = tmp_path / 'notified_issues.json'
    monkeypatch.setattr('betting_engine.telegram_notifier.ISSUES_PERSISTENCE_PATH', test_persistence)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 1001}}'
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    issues = [
        {
            'league': 'CHALLENGER',
            'event': 'Moez Echargui - Danill Glinka',
            'reason': 'stake_seed_identity_unresolved',
            'detail': 'Schedule placeholders and named fixture require a matching active full-game Winner pair.'
        }
    ]

    sent = notify_system_issues(issues)
    assert sent == 1
    assert mock_urlopen.call_count == 1

    # Second run with same issue: deduplicated
    sent_again = notify_system_issues(issues)
    assert sent_again == 0
    assert mock_urlopen.call_count == 1


@patch('urllib.request.urlopen')
def test_capital_defense_stake_050_is_notified(mock_urlopen, tmp_path, monkeypatch):
    test_persistence = tmp_path / 'notified_tips_cap_def.json'
    monkeypatch.setattr('betting_engine.telegram_notifier.PERSISTENCE_PATH', test_persistence)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 1002}}'
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    tips = [
        {
            'id': 'cap_def_tip_001',
            'league': 'NHL',
            'market': 'totals',
            'selection': 'Under 6.0',
            'stake': 0.50,
            'reference_odds': 1.85,
            'minimum_odds': 1.75,
            'probability': 0.58,
            'expected_roi': 0.073,
            'stress_roi': -0.021,
            'stake_note': 'Capped at $0.50 emergency capital defense tourniquet.',
            'funding_basis': {'tier': 'model_pick', 'minimum_odds': 1.72}
        }
    ]

    sent = notify_new_recommendations(tips)
    assert sent == 1
    assert mock_urlopen.call_count == 1


def test_format_recommendation_super_alpha_and_effective_min_odds():
    super_tip = {
        'id': 'super_alpha_wta_001',
        'league': 'WTA',
        'away': 'Ekaterina Alexandrova',
        'home': 'Leylah Fernandez',
        'start': '2026-10-23T05:00:00Z',
        'market': 'moneyline',
        'selection': 'Leylah Fernandez',
        'stake': 5.95,
        'reference_odds': 2.07,
        'minimum_odds': 2.22,  # stress minimum
        'probability': 0.493,
        'expected_roi': 0.021,
        'stress_roi': -0.041,
        'funding_basis': {'tier': 'model_pick', 'minimum_odds': 2.027},
        'navigation': {'event_url': 'https://stake.com/sports/tennis/sample', 'tab': 'Main'}
    }

    msg = format_recommendation_message(super_tip)
    assert 'SUPER ALPHA' in msg
    assert '$5.95 USD' in msg
    assert 'Min. required: 2.03' in msg  # Uses effective model min odds, not stress 2.22!
    assert 'Winner' in msg  # Tennis moneyline formatted to Winner
    assert '<b>Stress EV:</b> -4.1%' in msg  # Clean negative formatting, no '+-'
    assert '+-' not in msg


def test_notify_daily_council_briefing_format(monkeypatch):
    from betting_engine.telegram_notifier import notify_daily_council_briefing
    sent = []
    monkeypatch.setattr('betting_engine.telegram_notifier.send_telegram_message', lambda msg: sent.append(msg) or True)

    ok = notify_daily_council_briefing(
        date_str="2026-10-24",
        alpha_targets=["NFL|full_game|spreads", "WNBA|full_game|moneyline"],
        summary="High conviction targets identified by Multi-AI Council."
    )
    assert ok is True
    assert len(sent) == 1
    assert "DAILY MULTI-AI QUANT COUNCIL BRIEFING - 2026-10-24" in sent[0]
    assert "NFL|full_game|spreads" in sent[0]
    assert "High conviction targets" in sent[0]



