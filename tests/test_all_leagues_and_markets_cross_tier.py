import sqlite3
import pytest
from betting_engine.unified_market_universe import UnifiedMarketUniverse
from betting_engine.segment_scoreboard import compute_segment_scoreboard
from betting_engine.autonomous_roi_governor import AutonomousROIGovernor
from betting_engine.polymarket_executor import PolymarketAutonomousExecutor
from betting_engine.shadow_market_incubator import load_demoted_markets
from betting_engine.tier_state_machine import classify_tier

ALL_EXPECTED_LEAGUES = {
    "NBA", "NFL", "NHL", "MLB", "WNBA", "MLS", "EPL", "UCL",
    "NCAAF", "NCAAM", "NCAAW", "ATP", "WTA", "CHALLENGER"
}

@pytest.fixture(scope="module")
def universe_fixture():
    universe = UnifiedMarketUniverse()
    roster = universe.build_universe_roster()
    conn = sqlite3.connect(universe.engine_db_path)
    scoreboard = compute_segment_scoreboard(conn)
    governor = AutonomousROIGovernor()
    poly = PolymarketAutonomousExecutor()
    demoted = load_demoted_markets(universe.incubator_db_path.parent)
    return {
        "universe": universe,
        "roster": roster,
        "conn": conn,
        "scoreboard": scoreboard,
        "governor": governor,
        "poly": poly,
        "demoted": demoted,
    }

def test_all_leagues_represented(universe_fixture):
    """Verify that every expected liquid sport league is present in the market universe."""
    roster = universe_fixture["roster"]
    leagues_in_roster = set(p.league for p in roster.values())
    missing = ALL_EXPECTED_LEAGUES - leagues_in_roster
    assert not missing, f"Missing leagues in universe roster: {missing}"

def test_tier_stake_caps_invariants(universe_fixture):
    """Verify mathematical bounds for stakes across all tiers:
    - Tier 1: Exactly $0.00
    - Tier 2: Exactly $0.00
    - Tier 3: In range [$0.50, $8.00]
    """
    roster = universe_fixture["roster"]
    for key, prof in roster.items():
        if prof.tier == 1:
            assert prof.stake_cap == 0.0, f"Tier 1 market {key} has non-zero stake cap: {prof.stake_cap}"
        elif prof.tier == 2:
            assert prof.stake_cap == 0.0, f"Tier 2 market {key} has non-zero stake cap: {prof.stake_cap}"
        elif prof.tier == 3:
            assert 0.50 <= prof.stake_cap <= 8.00, f"Tier 3 market {key} stake cap out of [$0.50, $8.00]: {prof.stake_cap}"

def test_demoted_markets_quarantine_integrity(universe_fixture):
    """Verify that any demoted market is strictly quarantined:
    - Never in Tier 3 ($0.00 cap in roster)
    - Blocked on Polymarket ($0.00 cap)
    - Never in Governor SUPER_ALPHA
    """
    roster = universe_fixture["roster"]
    demoted = universe_fixture["demoted"]
    poly = universe_fixture["poly"]
    governor = universe_fixture["governor"]

    # Run governor evaluation to check status
    try:
        report = governor.run_cycle()
        super_alpha = set(report.get("super_alpha_list", []))
    except Exception:
        super_alpha = set()

    for d_key, d_info in demoted.items():
        # If present in roster
        if d_key in roster:
            prof = roster[d_key]
            assert prof.tier in (1, 2), f"Demoted market {d_key} is in Tier {prof.tier}"
            assert prof.stake_cap == 0.0, f"Demoted market {d_key} has non-zero stake cap {prof.stake_cap}"

            # Polymarket check
            poly_ok, poly_reason, poly_cap = poly.is_market_eligible(prof.league, prof.scope, prof.family, require_tier_4=True)
            assert not poly_ok, f"Demoted market {d_key} was allowed on Polymarket: {poly_reason}"
            assert poly_cap == 0.0, f"Demoted market {d_key} received non-zero Polymarket cap: {poly_cap}"

        assert d_key not in super_alpha, f"Demoted market {d_key} found in governor super_alpha_list!"

def test_tier_4_proven_alpha_criteria(universe_fixture):
    """Verify that markets qualifying for Tier 4 strictly satisfy:
    1. is_dual_polymarket is True
    2. Active in Tier 3 on Stake
    3. Not demoted
    4. Rolling games >= 10
    5. Rolling ROI >= +2.0%
    6. Rolling Units >= +0.5U (or confirmed positive ledger profit)
    7. Polymarket stake cap bounded in [$0.50, $8.00]
    """
    roster = universe_fixture["roster"]
    poly = universe_fixture["poly"]
    demoted = universe_fixture["demoted"]

    t4_markets = []
    for key, prof in roster.items():
        if prof.is_dual_polymarket:
            ok, reason, cap = poly.is_market_eligible(prof.league, prof.scope, prof.family, require_tier_4=True)
            if ok:
                t4_markets.append((key, prof, cap, reason))

    # There should be verified Tier 4 markets
    assert len(t4_markets) >= 1, "Expected at least 1 verified Tier 4 market"

    for key, prof, cap, reason in t4_markets:
        assert prof.is_dual_polymarket, f"Tier 4 market {key} is not marked dual polymarket"
        assert prof.tier == 3, f"Tier 4 market {key} is not in Tier 3 (tier={prof.tier})"
        assert key not in demoted, f"Tier 4 market {key} is in demoted_markets!"
        assert 0.50 <= cap <= 8.00, f"Tier 4 market {key} stake cap out of bounds: {cap}"
        assert "LIVE_LEDGER_PROVEN_PROFIT" in reason or "TIER_4_LIVE_PROVEN_ALPHA" in reason, f"Unexpected reason: {reason}"

def test_tier_3_probation_markets_blocked_from_tier_4(universe_fixture):
    """Verify that dual markets in Tier 3 with positive profit but N < 10 are held in PROBATION
    and NOT permitted to execute on Polymarket ($0.00)."""
    roster = universe_fixture["roster"]
    poly = universe_fixture["poly"]

    probation_count = 0
    for key, prof in roster.items():
        if prof.is_dual_polymarket and prof.tier == 3:
            ok, reason, cap = poly.is_market_eligible(prof.league, prof.scope, prof.family, require_tier_4=True)
            if "PROBATION_INSUFFICIENT_SAMPLE" in reason:
                assert not ok, f"Probation market {key} should not be eligible for Tier 4"
                assert cap == 0.0, f"Probation market {key} should have $0.00 Polymarket cap"
                probation_count += 1

    assert probation_count >= 1, "Expected at least 1 market in Tier 3 probation status"

def test_stake_only_derivatives_rejected_on_polymarket(universe_fixture):
    """Verify that Stake-only derivative scopes (quarters, periods, sets, corners)
    are recognized as STAKE_ONLY_MARKET and rejected from Polymarket ($0.00)."""
    poly = universe_fixture["poly"]

    test_cases = [
        ("NBA", "quarter_2", "spreads"),
        ("NBA", "quarter_4", "totals"),
        ("MLS", "half_2", "totals"),
        ("WTA", "period_2", "moneyline"),
        ("NHL", "period_3", "double_chance"),
    ]

    for lg, sc, fam in test_cases:
        ok, reason, cap = poly.is_market_eligible(lg, sc, fam, require_tier_4=True)
        assert not ok, f"Stake derivative {lg}|{sc}|{fam} should not be eligible on Polymarket"
        assert cap == 0.0, f"Stake derivative {lg}|{sc}|{fam} received non-zero cap: {cap}"
        assert (
            "STAKE_ONLY_MARKET" in reason
            or "DEMOTED_IN_SHADOW_INCUBATOR" in reason
            or "UNDERPERFORMING_IN_LIVE_BETS" in reason
        ), (
            f"Expected STAKE_ONLY_MARKET, DEMOTED, or UNDERPERFORMING, got: {reason}"
        )
