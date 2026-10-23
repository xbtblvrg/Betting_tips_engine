"""Unit tests for the Autonomous Model Generator."""
import pytest
from betting_engine.tournament_engine import TournamentEngine
from betting_engine.model_generator import synthesize_candidate_slate


def test_synthesize_candidate_slate_for_demoted_market(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True)
    engine = TournamentEngine(data_dir=data_dir, db_path=data_dir / 'shadow_incubator.sqlite3')

    segment_key = 'NHL|period_3|totals'
    results = synthesize_candidate_slate(segment_key, tournament_engine=engine)

    # Must synthesize 4 diverse candidates
    assert len(results) == 4

    for cand, success, errors in results:
        assert success is True, f"Failed registering {cand.model_id}: {errors}"
        assert cand.league == 'NHL'
        assert cand.scope == 'period_3'
        assert cand.family == 'totals'

    # Check that registry now has the tournament and 4 candidates
    reg = engine.load_registry()
    assert segment_key in reg['tournaments']
    assert len(reg['tournaments'][segment_key]['candidates']) == 4
