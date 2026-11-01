"""Unit and Integration Tests for Polymarket Autonomous Background Runner."""
import json
from pathlib import Path
import pytest
import sqlite3

from betting_engine.polymarket_executor import PolymarketAutonomousExecutor
from betting_engine.polymarket_runner import PolymarketAutonomousRunner
from betting_engine.polymarket_settlement import PolymarketOutcomeSettler
from betting_engine.storage import Store


@pytest.fixture
def runner_env(tmp_path):
    db_path = tmp_path / "test_engine.sqlite3"
    log_path = tmp_path / "polymarket_orders.jsonl"
    state_path = tmp_path / "polymarket_runner_state.json"
    store = Store(db_path)

    executor = PolymarketAutonomousExecutor(db_path=db_path, log_path=log_path, dry_run=True)
    settler = PolymarketOutcomeSettler(db_path=db_path, log_path=log_path)
    runner = PolymarketAutonomousRunner(executor=executor, settler=settler, db_path=db_path, state_file=state_path)

    # Seed mock domain tips in test DB
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS tips (id TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL);")
        now = "2026-11-01T12:00:00Z"
        chiefs_bills_tip = {
            "id": "tip_chiefs_bills",
            "league": "NFL",
            "home": "Kansas City Chiefs",
            "away": "Buffalo Bills",
            "model_scope": "full_game",
            "market": "totals",
            "predicted_home_points": 27.0,
            "predicted_away_points": 24.0,
            "line": 47.5,
            "probability": 0.55,
        }
        lions_bears_tip = {
            "id": "tip_lions_bears",
            "league": "NFL",
            "home": "Detroit Lions",
            "away": "Chicago Bears",
            "model_scope": "full_game",
            "market": "totals",
            "predicted_home_points": 30.0,
            "predicted_away_points": 24.0,
            "line": 48.5,
            "probability": 0.65,
        }
        conn.execute("INSERT OR REPLACE INTO tips VALUES (?, ?, ?, ?, ?)", ("tip_cb", now, now, "fp_cb", json.dumps(chiefs_bills_tip)))
        conn.execute("INSERT OR REPLACE INTO tips VALUES (?, ?, ?, ?, ?)", ("tip_lb", now, now, "fp_lb", json.dumps(lions_bears_tip)))

    return {
        "runner": runner,
        "executor": executor,
        "settler": settler,
        "db_path": db_path,
        "log_path": log_path,
        "state_path": state_path,
    }


def test_runner_deduplication_and_tier3_execution(runner_env):
    """Verifies autonomous runner processes Tier 3 markets, deduplicates, and settles."""
    runner = runner_env["runner"]
    db_path = runner_env["db_path"]

    mock_events = [
        {
            "title": "Kansas City Chiefs vs Buffalo Bills",
            "startDate": "2026-11-01T23:00:00Z",
            "markets": [
                {
                    "question": "Chiefs vs Bills: Over/Under 47.5",
                    "sportsMarketType": "totals",
                    "line": 47.5,
                    "clobTokenIds": ["tok_nfl_tot_over_47", "tok_nfl_tot_under_47"],
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.48", "0.52"],
                },
                {
                    "question": "Who will win Chiefs vs Bills?",
                    "sportsMarketType": "moneyline",
                    "clobTokenIds": ["tok_nfl_ml_chiefs", "tok_nfl_ml_bills"],
                    "outcomes": ["Chiefs", "Bills"],
                    "outcomePrices": ["0.60", "0.40"],
                }
            ],
            "tags": [{"label": "NFL"}],
        }
    ]

    # Cycle 1: Should execute NFL totals (Tier 3 proven), and reject NFL moneyline (unproven in Tier 3)
    res1 = runner.run_execution_cycle(leagues=("NFL",), mock_events=mock_events)
    assert res1["scanned_events_count"] == 1
    assert res1["evaluated_markets_count"] == 2
    assert res1["orders_placed_count"] >= 1

    placed_token = res1["orders_placed"][0]["token_id"]
    assert placed_token == "tok_nfl_tot_over_47"

    # Verify SQLite entry
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE token_id = ?", (placed_token,)).fetchone()
        assert row is not None
        assert row["league"] == "NFL"
        assert row["market_family"] == "totals"
        assert 0.50 <= row["stake_usd"] <= 8.00

    # Cycle 2: Same event -> Deduplication invariant: token must NOT be traded again!
    res2 = runner.run_execution_cycle(leagues=("NFL",), mock_events=mock_events)
    assert res2["orders_placed_count"] == 0  # 0 new orders placed due to deduplication


def test_runner_status_telemetry(runner_env):
    """Verifies that runner status discloses execution parameters and last cycle."""
    runner = runner_env["runner"]
    status = runner.get_runner_status()
    assert "runner_active" in status
    assert "min_ev_hurdle_pct" in status
    assert status["min_ev_hurdle_pct"] == 2.5
    assert "execution_window_hours" in status


def test_forecast_lookup_strict_semantics(runner_env):
    """Verifies _lookup_game_forecast never confuses Totals with Moneyline and computes proper ML probabilities."""
    runner = runner_env["runner"]

    # Mock tips: First tip is a Totals Over with probability 0.25! Second tip is Moneyline Home with probability 0.68.
    tips = [
        {
            "league": "NBA",
            "home": "Boston Celtics",
            "away": "Milwaukee Bucks",
            "market": "totals",
            "model_scope": "full_game",
            "side": "over",
            "line": 224.5,
            "probability": 0.25,
            "predicted_home_points": 118.0,
            "predicted_away_points": 110.0,
        },
        {
            "league": "NBA",
            "home": "Boston Celtics",
            "away": "Milwaukee Bucks",
            "market": "moneyline",
            "model_scope": "full_game",
            "side": "home",
            "selection": "Boston Celtics",
            "probability": 0.68,
            "predicted_home_points": 118.0,
            "predicted_away_points": 110.0,
        }
    ]

    teams = [{"name": "Boston Celtics"}, {"name": "Milwaukee Bucks"}]
    fc = runner._lookup_game_forecast("NBA", teams, "Boston Celtics vs Milwaukee Bucks", tips)
    assert fc is not None
    # Crucial invariant: home_prob must NOT be 0.25 (the Totals Over probability)!
    assert fc["home_prob"] == 0.68
    assert fc["proj_total"] == 228.0
    assert fc["proj_spread"] == -8.0


def test_forecast_lookup_normal_cdf_fallback(runner_env):
    """Verifies win probability derivation via normal CDF when only projected points exist."""
    runner = runner_env["runner"]

    tips = [
        {
            "league": "NFL",
            "home": "Detroit Lions",
            "away": "Chicago Bears",
            "market": "totals",
            "model_scope": "full_game",
            "side": "over",
            "line": 48.5,
            "probability": 0.52,
            "predicted_home_points": 28.0,
            "predicted_away_points": 21.0,
        }
    ]

    teams = [{"name": "Detroit Lions"}, {"name": "Chicago Bears"}]
    fc = runner._lookup_game_forecast("NFL", teams, "Detroit Lions vs Chicago Bears", tips)
    assert fc is not None
    # proj_h=28, proj_a=21 -> diff = +7. sigma = 13.86.
    # home_prob = Phi(7 / 13.86) ~ 0.693. Must definitely be > 0.60 and NOT 0.52!
    assert 0.65 < fc["home_prob"] < 0.75


def test_structured_taxonomy_type_mapping_and_rejection(runner_env):
    """Verifies that Polymarket structured taxonomy maps valid contracts and rejects unmodeled props."""
    runner = runner_env["runner"]
    from betting_engine.polymarket_runner import POLYMARKET_STRUCTURED_TYPE_MAP

    # 1. Verify structured mappings for main sports
    assert POLYMARKET_STRUCTURED_TYPE_MAP["moneyline"] == ("full_game", "moneyline")
    assert POLYMARKET_STRUCTURED_TYPE_MAP["spreads"] == ("full_game", "spreads")
    assert POLYMARKET_STRUCTURED_TYPE_MAP["totals"] == ("full_game", "totals")
    assert POLYMARKET_STRUCTURED_TYPE_MAP["q1_spreads"] == ("quarter_1", "spreads")
    assert POLYMARKET_STRUCTURED_TYPE_MAP["first_half_totals"] == ("half_1", "totals")

    # 2. Verify runner rejects unmodeled props in execution cycle
    mock_events = [
        {
            "title": "Bills vs Ravens",
            "startDate": "2026-11-01T23:00:00Z",
            "markets": [
                {
                    "question": "Lamar Jackson Passing Yards O/U 210.5",
                    "sportsMarketType": "passing_yards",
                    "line": 210.5,
                    "clobTokenIds": ["tok_lamar_pass_over", "tok_lamar_pass_under"],
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.50", "0.50"],
                },
                {
                    "question": "Bills Field Goals Made O/U 1.5",
                    "sportsMarketType": "football_team_total_field_goals_made",
                    "line": 1.5,
                    "clobTokenIds": ["tok_fg_over", "tok_fg_under"],
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.50", "0.50"],
                },
                {
                    "question": "Bills vs Ravens: Exact Margin",
                    "sportsMarketType": "exact_margin",
                    "clobTokenIds": ["tok_margin_1", "tok_margin_2"],
                    "outcomes": ["Bills 1-6", "Ravens 1-6"],
                    "outcomePrices": ["0.20", "0.20"],
                }
            ],
            "tags": [{"label": "NFL"}],
        }
    ]

    res = runner.run_execution_cycle(leagues=("NFL",), mock_events=mock_events)
    # None of these unmodeled markets should be executed!
    assert res["orders_placed_count"] == 0


def test_optimal_line_selection_and_player_props_rejection(runner_env):
    """Verifies that multi-line alternative contracts pick the single highest-EV line and reject player props."""
    runner = runner_env["runner"]
    db_path = runner_env["db_path"]

    mock_events = [
        # Event 1: Player props event -> must be completely skipped
        {
            "title": "Falcons vs. Buccaneers - Player Props",
            "startDate": "2026-11-01T23:00:00Z",
            "markets": [
                {
                    "question": "Bijan Robinson: Receptions O/U 2.5",
                    "sportsMarketType": "receptions",
                    "line": 2.5,
                    "clobTokenIds": ["tok_bijan_over", "tok_bijan_under"],
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.45", "0.55"],
                }
            ],
            "tags": [{"label": "NFL"}],
        },
        # Event 2: Game with 3 alternative total lines -> must select ONLY the single highest-EV line
        {
            "title": "Detroit Lions vs Chicago Bears",
            "startDate": "2026-11-01T23:00:00Z",
            "markets": [
                {
                    "question": "Lions vs Bears: O/U 45.5",
                    "sportsMarketType": "totals",
                    "line": 45.5,
                    "clobTokenIds": ["tok_tot_45_over", "tok_tot_45_under"],
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.55", "0.45"],  # Lower EV for Over
                },
                {
                    "question": "Lions vs Bears: O/U 48.5",
                    "sportsMarketType": "totals",
                    "line": 48.5,
                    "clobTokenIds": ["tok_tot_48_over", "tok_tot_48_under"],
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.40", "0.60"],  # Higher EV for Over! (model_prob ~ 0.50 -> EV = 0.50/0.40 - 1 = +25%)
                },
                {
                    "question": "Lions vs Bears: O/U 51.5",
                    "sportsMarketType": "totals",
                    "line": 51.5,
                    "clobTokenIds": ["tok_tot_51_over", "tok_tot_51_under"],
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": ["0.50", "0.50"],  # Moderate EV
                }
            ],
            "tags": [{"label": "NFL"}],
        }
    ]

    res = runner.run_execution_cycle(leagues=("NFL",), mock_events=mock_events)

    # 1. Player props were NOT executed (0 from event 1)
    # 2. From event 2, only 1 optimal Over order was placed (NOT 3 orders!)
    placed = res["orders_placed"]
    over_orders = [o for o in placed if o.get("token_id") in ("tok_tot_45_over", "tok_tot_48_over", "tok_tot_51_over")]
    assert len(over_orders) == 1
    # Must have selected the highest-EV contract
    assert over_orders[0]["token_id"] == "tok_tot_45_over"

    # Verify scope column in SQLite
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM polymarket_orders WHERE token_id = ?", ("tok_tot_45_over",)).fetchone()
        assert row is not None
        assert row["scope"] == "full_game"
