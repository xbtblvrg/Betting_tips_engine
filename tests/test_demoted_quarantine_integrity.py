"""Permanent anti-regression test suite to guarantee that demoted market leaks can NEVER recur.

Enforces Constitutional Commandment 6 & 7:
Demoted markets are strictly quarantined ($0.00 stake) and can NEVER receive
funded execution on Stake or Polymarket until passing the Walk-Forward Crucible.
"""
import pytest
from unittest.mock import MagicMock
from betting_engine.decisions import is_synthetic_test_candidate, rank_and_size, _get_demoted_segments
from betting_engine.contracts import canonical_segment_aliases
from betting_engine.storage import Store


def test_live_game_ids_never_classified_as_synthetic():
    """Live ESPN, NHL, NBA, NFL, EPL, ATP, and MLS game IDs must NEVER be treated as synthetic unit test mocks."""
    live_game_ids = [
        ('NBA:401909892', 'NBA'),
        ('NHL:2026020309', 'NHL'),
        ('NFL:2026_10_NE_DET', 'NFL'),
        ('EPL:401879250', 'EPL'),
        ('ATP:15e0fa73-5a45-4159-909d-816eb79e4931', 'ATP'),
        ('WTA:64875bf2-42bb-41ca-8dd0-08d974c91ee4', 'WTA'),
        ('MLS:12345678', 'MLS')
    ]
    for gid, lg in live_game_ids:
        tip = {'id': 'a1b2c3d4e5f607182930', 'game_id': gid, 'league': lg}
        assert not is_synthetic_test_candidate(tip), f"FATAL: Live game ID '{gid}' was misclassified as synthetic test mock!"


def test_demoted_markets_strictly_blocked_by_rank_and_size():
    """Demoted markets must be assigned $0.00 stake and routed to alternatives (quarantined)."""
    dem_segs = _get_demoted_segments()
    assert len(dem_segs) > 0, "Demoted segments table must not be empty in production."

    store = Store()
    settings = store.settings()

    sample_segs = [s for s in sorted(dem_segs) if s.count('|') == 2 and ':' not in s.split('|')[2] and s.split('|')[1] in ('full_game', 'half_1', 'period_1')][:3]
    test_cands = []
    for i, seg in enumerate(sample_segs):
        lg, sc, fam = seg.split('|')
        test_cands.append({
            'id': f'dem_cand_{i}',
            'game_id': f'{lg}:game_{i}',
            'league': lg,
            'market': fam,
            'model_scope': sc,
            'side': 'home',
            'line': 0.0,
            'expected_roi': 0.20,
            'stress_roi': 0.15,
            'probability': 0.65,
            'stress_probability': 0.60,
            'push_probability': 0.0,
            'reference_odds': 1.95,
            'reference_platform': 'Stake.com',
            'start': '2026-11-14T00:00:00Z',
            'created_at': '2026-11-13T12:00:00Z',
            'reasons': []
        })

    chosen, alts = rank_and_size(test_cands, settings, held_event_roster={})

    assert len(chosen) == 0, f"Demoted markets leaked into funded choices: {chosen}"
    assert len(alts) == 3, f"All 3 demoted markets must be quarantined to alternatives: {alts}"
    for a in alts:
        assert a.get('allocation_code') == 'capital_defense_quarantined'
        assert a.get('stake', 0.0) == 0.0


def test_storage_database_gate_rejects_demoted_recommendation():
    """The database storage layer must fail-closed and refuse to insert demoted recommendations."""
    store = Store()
    tip = {
        'id': 'd1e2f3a4b5c607182939',
        'game_id': 'NHL:2026020309',
        'league': 'NHL',
        'market': 'spreads',
        'model_scope': 'full_game',
        'side': 'home',
        'line': -1.5,
        'selection': 'Boston Bruins -1.5',
        'start': '2026-11-14T00:00:00Z',
        'valid_until': '2026-11-14T00:00:00Z',
        'reference_odds': 1.95,
        'stake': 2.50,
        'is_demoted': True,
        'is_graduated': False
    }

    # Attempt to record via storage gate
    with store.db() as c:
        Store.record_issuance(c, tip, issued_at='2026-11-13T12:00:00Z', settings=store.settings())

    # Verify that it was NOT inserted into system_decisions
    with store.ro_db() as cur:
        row = cur.execute("SELECT tip_id FROM system_decisions WHERE tip_id = ?", (tip['id'],)).fetchone()
        assert row is None, f"FATAL: Storage gate failed to reject demoted tip '{tip['id']}' from system_decisions!"
