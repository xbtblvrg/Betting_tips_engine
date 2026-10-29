import pytest
from betting_engine.stake_preflight import (
    extract_fixture_slug,
    match_outcome_text,
    match_stake_feed_contract,
    evaluate_odds_drift_flexible,
    preflight_verify_contract,
)


def test_extract_fixture_slug():
    url1 = "https://stake.com/sports/ice-hockey/usa/nhl/46925085-carolina-hurricanes-columbus-blue-jackets"
    assert extract_fixture_slug(url1) == "46925085-carolina-hurricanes-columbus-blue-jackets"

    url2 = "https://stake.com/sports/basketball/usa/nba/47014902-new-orleans-pelicans-sacramento-kings?tab=quarters"
    assert extract_fixture_slug(url2) == "47014902-new-orleans-pelicans-sacramento-kings"

    assert extract_fixture_slug(None) is None
    assert extract_fixture_slug("https://stake.com/sports/all") is None


def test_match_outcome_text():
    assert match_outcome_text("Under 1.5", "Under 1.5") is True
    assert match_outcome_text("Under 1.5", "Under", line=1.5) is True
    assert match_outcome_text("Carolina Hurricanes -0.5", "Carolina Hurricanes (-0.5)") is True
    assert match_outcome_text("Over 224.5", "Over 224.5") is True
    assert match_outcome_text("Yes", "Yes") is True
    assert match_outcome_text("Under 1.5", "Over 1.5") is False
    assert match_outcome_text("Under 1.5", "Under 2.5") is False


def test_evaluate_odds_drift_flexible_with_model_probability():
    # If model probability is 0.50 (fair odds 2.00)
    # Case 1: live odds dropped from 2.20 to 2.10 -> Still positive EV (+5.0%) -> MUST BE ACCEPTED
    res1 = evaluate_odds_drift_flexible(live_odds=2.10, expected_odds=2.20, min_odds=2.00, model_probability=0.50)
    assert res1["acceptable"] is True
    assert res1["ev"] == 0.05

    # Case 2: live odds dropped from 2.20 to 1.95 -> EV = -2.5% -> Must be rejected
    res2 = evaluate_odds_drift_flexible(live_odds=1.95, expected_odds=2.20, min_odds=2.00, model_probability=0.50)
    assert res2["acceptable"] is False
    assert res2["ev"] == -0.025

    # Case 3: Absolute floor 1.40 enforcement even with high probability
    res3 = evaluate_odds_drift_flexible(live_odds=1.35, expected_odds=1.45, min_odds=1.40, model_probability=0.80)
    assert res3["acceptable"] is False
    assert "floor 1.40" in res3["reason"]


def test_evaluate_odds_drift_flexible_nominal():
    # When probability is unknown: allow up to 10% drift
    # Expected 2.50, live is 2.35 (6% drop) -> ACCEPTED
    res1 = evaluate_odds_drift_flexible(live_odds=2.35, expected_odds=2.50, min_odds=2.30)
    assert res1["acceptable"] is True

    # Expected 2.50, live is 2.15 (14% drop) -> REJECTED
    res2 = evaluate_odds_drift_flexible(live_odds=2.15, expected_odds=2.50, min_odds=2.30)
    assert res2["acceptable"] is False


def test_match_stake_feed_contract_synthetic():
    mock_feed = {
        "groups": [
            {
                "name": "periods",
                "markets": [
                    [
                        {
                            "name": "2nd Period - Total",
                            "specifiers": "periodnr=2|total=1.5",
                            "outcomes": [
                                {"id": "out-1", "name": "Over 1.5", "odds": 1.55, "active": True},
                                {"id": "out-2", "name": "Under 1.5", "odds": 2.45, "active": True},
                            ]
                        }
                    ],
                    [
                        {
                            "name": "1st Period - Handicap",
                            "specifiers": "periodnr=1|hcp=-0.5",
                            "outcomes": [
                                {"id": "out-3", "name": "Boston Bruins (-0.5)", "odds": 2.10, "active": True},
                                {"id": "out-4", "name": "Toronto Maple Leafs (0.5)", "odds": 1.70, "active": True},
                            ]
                        }
                    ]
                ]
            }
        ]
    }

    # Match period 2 totals Under 1.5
    m1 = match_stake_feed_contract(mock_feed, market="period_2:totals", selection="Under 1.5", line=1.5)
    assert m1 is not None
    assert m1["market_name"] == "2nd Period - Total"
    assert m1["outcome_name"] == "Under 1.5"
    assert m1["odds"] == 2.45
    assert m1["tab"] == "Periods"
    assert m1["outcome_id"] == "out-2"

    # Match period 1 spreads
    m2 = match_stake_feed_contract(mock_feed, market="period_1:spreads", selection="Boston Bruins -0.5", line=-0.5)
    assert m2 is not None
    assert m2["market_name"] == "1st Period - Handicap"
    assert m2["outcome_name"] == "Boston Bruins (-0.5)"
    assert m2["odds"] == 2.10
    assert m2["tab"] == "Periods"

    # Market not present
    m3 = match_stake_feed_contract(mock_feed, market="period_3:totals", selection="Under 1.5")
    assert m3 is None


def test_preflight_verify_contract_fallback_on_invalid_url(monkeypatch):
    res = preflight_verify_contract(
        event_url="https://stake.com/sports/all",
        market="period_2:totals",
        selection="Under 1.5"
    )
    assert res["status"] == "fallback_to_dom"


def test_preflight_verify_contract_live_mock(monkeypatch):
    from betting_engine import stake_preflight

    mock_feed = {
        "groups": [
            {
                "name": "periods",
                "markets": [
                    [
                        {
                            "name": "2nd Period - Total",
                            "specifiers": "periodnr=2|total=1.5",
                            "outcomes": [
                                {"id": "out-under", "name": "Under 1.5", "odds": 2.40, "active": True}
                            ]
                        }
                    ]
                ]
            }
        ]
    }

    monkeypatch.setattr(stake_preflight, "fetch_stake_fixture_odds", lambda slug: mock_feed)

    # 1. OK case with positive EV
    res_ok = preflight_verify_contract(
        event_url="https://stake.com/sports/ice-hockey/usa/nhl/46925085-carolina-hurricanes-columbus-blue-jackets",
        market="period_2:totals",
        selection="Under 1.5",
        line=1.5,
        expected_odds=2.45,
        min_odds=2.20,
        model_probability=0.45,  # EV = 0.45 * 2.40 - 1 = +8%
    )
    assert res_ok["status"] == "ok"
    assert res_ok["exact_accordion"] == "2nd Period - Total"
    assert res_ok["exact_tab"] == "Periods"
    assert res_ok["exact_outcome_name"] == "Under 1.5"
    assert res_ok["live_odds"] == 2.40

    # 2. Skip drift case when odds drop below EV
    res_drift = preflight_verify_contract(
        event_url="https://stake.com/sports/ice-hockey/usa/nhl/46925085-carolina-hurricanes-columbus-blue-jackets",
        market="period_2:totals",
        selection="Under 1.5",
        line=1.5,
        expected_odds=2.45,
        min_odds=2.20,
        model_probability=0.35,  # EV = 0.35 * 2.40 - 1 = -16%
    )
    assert res_drift["status"] == "skip_drift"
    assert res_drift["ev"] < 0

    # 3. Market not found in feed
    res_not_found = preflight_verify_contract(
        event_url="https://stake.com/sports/ice-hockey/usa/nhl/46925085-carolina-hurricanes-columbus-blue-jackets",
        market="quarter_4:totals",
        selection="Under 13.5",
    )
    assert res_not_found["status"] == "market_not_found"


def test_tennis_sets_preflight(monkeypatch):
    sample_tennis_feed = {
        "groups": [
            {
                "name": "sets",
                "markets": [
                    [
                        {
                            "name": "2nd Set - Winner",
                            "specifiers": "setnr=2",
                            "outcomes": [
                                {"id": "out-1", "name": "Player A", "odds": 1.45, "active": True},
                                {"id": "out-2", "name": "Player B", "odds": 2.65, "active": True},
                            ],
                        }
                    ]
                ],
            }
        ]
    }
    monkeypatch.setattr(
        "betting_engine.stake_preflight.fetch_stake_fixture_odds",
        lambda slug: sample_tennis_feed,
    )

    res = preflight_verify_contract(
        event_url="https://stake.com/sports/tennis/wta/tournament/47015978-slug",
        market="period_2:moneyline",
        selection="Player B",
        expected_odds=2.60,
        league="WTA",
    )
    assert res["status"] == "ok"
    assert res["exact_tab"] == "Sets"
    assert res["exact_accordion"] == "2nd Set - Winner"
    assert res["exact_outcome_id"] == "out-2"
    assert res["exact_outcome_name"] == "Player B"
    assert res["live_odds"] == 2.65

