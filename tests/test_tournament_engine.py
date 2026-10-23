"""Unit tests for the Multi-Model Tournament Engine."""
from pathlib import Path
import sqlite3
import pytest

from betting_engine.tournament_engine import TournamentEngine, calculate_crucible_scorecard
from betting_engine.sandbox_models.archetypes.negative_binomial import NegativeBinomialOverdispersionModel
from betting_engine.sandbox_models.archetypes.odds_damped_kelly import OddsDampedKellyShrinkageModel


@pytest.fixture
def temp_tournament_env(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True)
    db_path = data_dir / 'shadow_incubator.sqlite3'

    # Initialize SQLite schema
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE incubated_wagers (
            id TEXT PRIMARY KEY,
            fixture_id TEXT,
            game_id TEXT,
            league TEXT,
            scope TEXT,
            family TEXT,
            sub_family TEXT,
            raw_market_name TEXT,
            selection TEXT,
            side TEXT,
            line REAL,
            odds REAL,
            model_prob REAL,
            push_prob REAL,
            market_prob REAL,
            ev REAL,
            dynamic_sample_power_req INT,
            captured_at TEXT,
            status TEXT,
            settled_at TEXT,
            result TEXT,
            pnl REAL,
            brier_model REAL,
            brier_market REAL,
            metadata TEXT,
            cohort TEXT,
            model_id TEXT,
            model_version TEXT
        )
    """)
    conn.commit()
    conn.close()

    engine = TournamentEngine(data_dir=data_dir, db_path=db_path)
    return engine, db_path


def test_scorecard_calculation():
    # S = ROI * sqrt(N) * (DeltaBrier / max(0.05, MaxDD))
    s1 = calculate_crucible_scorecard(roi=0.10, sample_size=100, delta_brier=0.010, max_drawdown=0.05)
    # 0.10 * 10 * (0.010 / 0.05) = 1.0 * 0.2 = 0.20
    assert pytest.approx(s1, 0.001) == 0.20

    # Negative ROI yields negative score
    s2 = calculate_crucible_scorecard(roi=-0.05, sample_size=100, delta_brier=0.010, max_drawdown=0.05)
    assert s2 < 0.0


def test_tournament_candidate_registration_and_anti_looping(temp_tournament_env):
    engine, _ = temp_tournament_env
    model = NegativeBinomialOverdispersionModel(model_id="cand_nb_1", league='NHL', scope='period_3', family='totals')

    success, errors = engine.register_candidate('NHL|period_3|totals', model)
    assert success is True
    assert len(errors) == 0

    reg = engine.load_registry()
    assert 'NHL|period_3|totals' in reg['tournaments']
    assert 'cand_nb_1' in reg['tournaments']['NHL|period_3|totals']['candidates']


def test_tournament_standings_and_crowning(temp_tournament_env):
    engine, db_path = temp_tournament_env
    segment = 'NBA|full_game|spreads'

    # Register two competing candidates
    model_a = OddsDampedKellyShrinkageModel(model_id="cand_damped_a", version="1.0", damping_strength=0.25)
    model_b = OddsDampedKellyShrinkageModel(model_id="cand_damped_b", version="1.0", damping_strength=0.05)

    engine.register_candidate(segment, model_a)
    engine.register_candidate(segment, model_b)

    # Insert 30 simulated settled wagers for model_a (high win rate, positive ROI)
    # and 30 for model_b (losing wagers)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Model A: 20 wins, 10 losses interleaved (2 wins, 1 loss) -> PnL = 20 * 0.95 - 10 = +9.0, ROI = +30.0%
    for i in range(10):
        # 2 wins
        for w in range(2):
            cur.execute("""
                INSERT INTO incubated_wagers (id, fixture_id, game_id, league, scope, family, sub_family,
                    raw_market_name, selection, odds, model_prob, market_prob, ev, dynamic_sample_power_req,
                    captured_at, status, settled_at, result, pnl, brier_model, brier_market, model_id, model_version)
                VALUES (?, 'f1', 'g1', 'NBA', 'full_game', 'spreads', 'handicap', 'Spread', 'Home', 1.95,
                    0.58, 0.51, 0.13, 25, '2026-10-01', 'settled', '2026-10-02', 'won', 0.95, 0.1764, 0.2401, 'cand_damped_a', '1.0')
            """, (f"w_a_win_{i}_{w}",))
        # 1 loss
        cur.execute("""
            INSERT INTO incubated_wagers (id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, odds, model_prob, market_prob, ev, dynamic_sample_power_req,
                captured_at, status, settled_at, result, pnl, brier_model, brier_market, model_id, model_version)
            VALUES (?, 'f1', 'g1', 'NBA', 'full_game', 'spreads', 'handicap', 'Spread', 'Home', 1.95,
                0.58, 0.51, 0.13, 25, '2026-10-01', 'settled', '2026-10-02', 'lost', -1.0, 0.3364, 0.2401, 'cand_damped_a', '1.0')
        """, (f"w_a_lost_{i}",))

    # Model B: 8 wins, 22 losses -> negative ROI
    for i in range(8):
        cur.execute("""
            INSERT INTO incubated_wagers (id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, odds, model_prob, market_prob, ev, dynamic_sample_power_req,
                captured_at, status, settled_at, result, pnl, brier_model, brier_market, model_id, model_version)
            VALUES (?, 'f1', 'g1', 'NBA', 'full_game', 'spreads', 'handicap', 'Spread', 'Home', 1.95,
                0.55, 0.51, 0.07, 25, '2026-10-01', 'settled', '2026-10-02', 'won', 0.95, 0.2025, 0.2401, 'cand_damped_b', '1.0')
        """, (f"w_b_win_{i}",))
    for i in range(22):
        cur.execute("""
            INSERT INTO incubated_wagers (id, fixture_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, odds, model_prob, market_prob, ev, dynamic_sample_power_req,
                captured_at, status, settled_at, result, pnl, brier_model, brier_market, model_id, model_version)
            VALUES (?, 'f1', 'g1', 'NBA', 'full_game', 'spreads', 'handicap', 'Spread', 'Home', 1.95,
                0.55, 0.51, 0.07, 25, '2026-10-01', 'settled', '2026-10-02', 'lost', -1.0, 0.3025, 0.2401, 'cand_damped_b', '1.0')
        """, (f"w_b_lost_{i}",))

    conn.commit()
    conn.close()

    standings = engine.evaluate_tournament(segment)
    assert len(standings) == 2

    # Model A should rank #1 with positive scorecard S
    assert standings[0].model_id == 'cand_damped_a'
    assert standings[0].is_qualified is True
    assert standings[0].simulated_roi > 0.0
    assert standings[0].scorecard_s > 0.0

    # Model B should rank #2 and be rejected
    assert standings[1].model_id == 'cand_damped_b'
    assert standings[1].is_qualified is False
    assert standings[1].status == 'rejected'

    # Crown champion
    champ = engine.crown_champion(segment)
    assert champ is not None
    assert champ.model_id == 'cand_damped_a'

    reg = engine.load_registry()
    assert segment in reg['champions']
    assert reg['champions'][segment]['champion_model_id'] == 'cand_damped_a'
    # Model B signature hash should now be in rejected list to prevent looping
    assert len(reg['rejected']) >= 1


def test_active_candidates_retrieval(temp_tournament_env):
    engine, _ = temp_tournament_env
    segment = 'NHL|period_3|totals'
    model = NegativeBinomialOverdispersionModel(model_id="cand_nb_active", league='NHL', scope='period_3', family='totals')
    engine.register_candidate(segment, model)

    active = engine.get_active_candidates(segment)
    assert len(active) == 1
    assert active[0].model_id == "cand_nb_active"
    assert active[0].league == 'NHL'
    assert active[0].scope == 'period_3'


def test_get_model_by_id_retrieval(temp_tournament_env):
    engine, _ = temp_tournament_env
    segment = 'NBA|full_game|spreads'
    model = OddsDampedKellyShrinkageModel(model_id="cand_lookup_test_1", league='NBA', scope='full_game', family='spreads')
    engine.register_candidate(segment, model)

    fetched = engine.get_model_by_id("cand_lookup_test_1")
    assert fetched is not None
    assert fetched.model_id == "cand_lookup_test_1"
    assert fetched.league == 'NBA'
    assert isinstance(fetched, OddsDampedKellyShrinkageModel)

    # Non-existent ID returns None
    assert engine.get_model_by_id("non_existent_id") is None


def test_segment_key_normalization_and_alias_resolution(temp_tournament_env):
    engine, _ = temp_tournament_env
    # Register with scoped family: ATP|period_1|period_1:spreads
    model = NegativeBinomialOverdispersionModel(model_id="cand_alias_test", league='ATP', scope='period_1', family='spreads')
    success, errors = engine.register_candidate('ATP|period_1|period_1:spreads', model)
    assert success is True

    # Both base and scoped queries must find the candidate
    cands_base = engine.get_active_candidates('ATP|period_1|spreads')
    cands_scoped = engine.get_active_candidates('ATP|period_1|period_1:spreads')

    assert len(cands_base) == 1
    assert len(cands_scoped) == 1
    assert cands_base[0].model_id == "cand_alias_test"
    assert cands_scoped[0].model_id == "cand_alias_test"


