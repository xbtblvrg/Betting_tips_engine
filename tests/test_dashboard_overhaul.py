from __future__ import annotations

import copy
import sqlite3
import time
from pathlib import Path

import pytest

from betting_engine import outcome_sizing, statistics
from betting_engine.funnel_proportionality_supervisor import (
    FunnelProportionalitySupervisor,
    is_dual_reachable_contract,
)
from betting_engine.polymarket_executor import PolymarketAutonomousExecutor


def _make_ticket_bundle(
    *,
    bet_id: int,
    tip_id: str,
    league: str,
    market: str,
    side: str,
    line: float | None,
    stake: float,
    odds: float,
    status: str,
    pnl: float,
    home_score: float | None,
    away_score: float | None,
    model_version: str = 'epl_dixon_coles_v2',
    outcome_model_version: str | None = None,
    evidence_extra: dict | None = None,
) -> tuple[dict, dict, dict]:
    payload = {
        'id': tip_id,
        'ownership': 'engine',
        'league': league,
        'game_id': f'game_{bet_id}',
        'home': 'Arsenal',
        'away': 'Chelsea',
        'start': '2026-11-20T15:00:00+00:00',
        'market': market,
        'market_display': 'Asian Handicap' if market == 'spreads' else ('Asian Total' if market == 'totals' else 'Winner'),
        'model_scope': 'full_game',
        'side': side,
        'line': line,
        'selection': f'{side} {line}' if line is not None else side,
        'probability': 0.56,
        'push_probability': 0.0,
        'reference_odds': odds,
        'stake': stake,
        'tracking_source': 'auto_assumed_placed',
        'model_version': model_version,
    }
    evidence = {
        'source': 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard',
        'source_url': 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard',
        'fetched_at': '2026-11-20T17:55:00+00:00',
        'settled_at': '2026-11-20T18:00:00+00:00',
        'sha256': 'a' * 64,
        **(
            {'home_score': home_score, 'away_score': away_score, 'score_scope': {'scope': 'full_game'}}
            if home_score is not None and away_score is not None
            else {}
        ),
        **(evidence_extra or {}),
    }
    bet_row = {
        'id': bet_id,
        'tip_id': tip_id,
        'stake': stake,
        'odds': odds,
        'platform': 'Stake.com',
        'status': status,
        'pnl': pnl,
        'payload': copy.deepcopy(payload),
        'created_at': '2026-11-20T12:00:00+00:00',
        'settled_at': '2026-11-20T18:00:00+00:00',
    }
    decision_row = {
        'family': f'{league}|game_{bet_id}|{market}',
        'tip_id': tip_id,
        'league': league,
        'game_id': f'game_{bet_id}',
        'market': market,
        'status': status,
        'pnl': pnl,
        'issued_at': '2026-11-20T12:00:00+00:00',
        'settled_at': '2026-11-20T18:00:00+00:00',
        'payload': copy.deepcopy(payload),
        'outcome_evidence': copy.deepcopy(evidence),
    }
    outcome_row = {
        'bet_id': bet_id,
        'tip_id': tip_id,
        'league': league,
        'game_id': f'game_{bet_id}',
        'market': market,
        'model_version': outcome_model_version or model_version,
        'predicted_probability': 0.56,
        'outcome': 1 if status == 'won' else (0 if status == 'lost' else None),
        'result': status,
        'home_score': home_score,
        'away_score': away_score,
        'settled_at': '2026-11-20T18:00:00+00:00',
        'source_url': 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard',
        'evidence': copy.deepcopy(evidence),
    }
    return bet_row, decision_row, outcome_row


def test_verify_summary_memoization_speed_and_nested_tamper_detection():
    _, decision_row, _ = _make_ticket_bundle(
        bet_id=1,
        tip_id='t1',
        league='NBA',
        market='moneyline',
        side='home',
        line=None,
        stake=2.0,
        odds=2.10,
        status='won',
        pnl=2.20,
        home_score=112,
        away_score=104,
        model_version='nba_v1',
    )
    snap = outcome_sizing.build_snapshot([decision_row], as_of='2026-11-25T00:00:00+00:00')
    summary = outcome_sizing.summary_for(snap, decision_row['payload'])
    assert outcome_sizing.verify_summary(summary) is True
    assert summary['independent_games'] == 1

    # Benchmark 5,000 repeated verifications to prove memoization eliminates redundant O(N) re-hashing
    t0 = time.perf_counter()
    for _ in range(5000):
        assert outcome_sizing.verify_summary(summary) is True
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, f'5,000 memoized verify_summary calls took {elapsed:.3f}s (> 0.5s)'

    # Nested mutation inside games dict must fail closed even when summary_sha256 is unchanged
    tampered_nested = copy.deepcopy(summary)
    tampered_nested['games']['game_1']['pnl'] = round(tampered_nested['games']['game_1']['pnl'] + 0.01, 6)
    assert outcome_sizing.verify_summary(tampered_nested) is False

    # Type coercion (bool vs int) must also fail closed
    tampered_type = copy.deepcopy(summary)
    tampered_type['independent_games'] = True
    assert outcome_sizing.verify_summary(tampered_type) is False


def test_quarter_line_asian_settlement_math_accepts_exact_half_splits_and_rejects_wrong_pnl():
    # Case 1: Home -0.25 at odds 2.00, Stake $4.00, Score 1-1 -> half push ($0.00), half loss (-$2.00) => PnL = -$2.00
    b1, d1, o1 = _make_ticket_bundle(
        bet_id=10,
        tip_id='q_half_loss',
        league='EPL',
        market='spreads',
        side='home',
        line=-0.25,
        stake=4.00,
        odds=2.00,
        status='lost',
        pnl=-2.00,
        home_score=1,
        away_score=1,
    )

    # Case 2: Over 2.75 at odds 1.90, Stake $4.00, Score 2-1 (total 3) -> half win (+$1.80), half push ($0.00) => PnL = +$1.80
    b2, d2, o2 = _make_ticket_bundle(
        bet_id=11,
        tip_id='q_half_win',
        league='EPL',
        market='totals',
        side='over',
        line=2.75,
        stake=4.00,
        odds=1.90,
        status='won',
        pnl=1.80,
        home_score=2,
        away_score=1,
    )

    # Case 3: Authorized walkover void (Tennis retirement/walkover before completion, $0.00 PnL, no final scores)
    b3, d3, o3 = _make_ticket_bundle(
        bet_id=12,
        tip_id='atp_walkover',
        league='ATP',
        market='moneyline',
        side='home',
        line=None,
        stake=2.50,
        odds=1.75,
        status='void',
        pnl=0.0,
        home_score=None,
        away_score=None,
        model_version='atp_elo_v2',
        outcome_model_version='walkover_void_supervisor',
        evidence_extra={'basis': 'stake_rules_walkover_void_resolution'},
    )

    report_clean = statistics.build_statistics(
        [b1, b2, b3],
        [d1, d2, d3],
        [o1, o2, o3],
        as_of='2026-11-25T00:00:00+00:00',
    )
    assert report_clean['resolution_audit']['issues'] == []

    # Adversarial check A: Full win score (3-0 on Home -0.25) recorded with half-win PnL MUST be flagged!
    b_bad1, d_bad1, o_bad1 = _make_ticket_bundle(
        bet_id=13,
        tip_id='q_falsified_half_win',
        league='EPL',
        market='spreads',
        side='home',
        line=-0.25,
        stake=4.00,
        odds=2.00,
        status='won',
        pnl=2.00,  # Should be full win +$4.00!
        home_score=3,
        away_score=0,
    )
    # Adversarial check B: walkover_void_supervisor claiming 'won' with positive PnL MUST be flagged!
    b_bad2, d_bad2, o_bad2 = _make_ticket_bundle(
        bet_id=14,
        tip_id='fake_walkover_win',
        league='ATP',
        market='moneyline',
        side='home',
        line=None,
        stake=2.50,
        odds=1.80,
        status='won',
        pnl=2.00,
        home_score=None,
        away_score=None,
        model_version='atp_elo_v2',
        outcome_model_version='walkover_void_supervisor',
        evidence_extra={'basis': 'stake_rules_walkover_void_resolution'},
    )

    report_bad = statistics.build_statistics(
        [b1, b2, b3, b_bad1, b_bad2],
        [d1, d2, d3, d_bad1, d_bad2],
        [o1, o2, o3, o_bad1, o_bad2],
        as_of='2026-11-25T00:00:00+00:00',
    )
    issues = report_bad['resolution_audit']['issues']
    assert any(i.get('id') == 13 and i['code'] == 'recorded_pnl_disagrees_with_result' for i in issues)
    assert any(i.get('id') == 14 and i['code'] == 'bet_outcome_model_version_mismatch' for i in issues)
    for item in issues:
        assert isinstance(item.get('reason'), str) and len(item['reason']) > 10
        assert isinstance(item.get('detail'), str) and len(item['detail']) > 10


def test_funnel_supervisor_tier4_counts_match_executor_eligibility(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake_scoreboard = {
        'alpha_segments': [
            # Meets Stake alpha list, and only MLS|full_game|totals + CHALLENGER|full_game|odd_even pass Tier-4 check
            {'segment': 'MLS|full_game|totals', 'league': 'MLS', 'scope': 'full_game', 'family': 'totals', 'games': 14, 'pnl': 18.5, 'roi_pct': 22.0},
            # Has < 10 games / fails strict Tier 4 check even though in raw alpha_segments
            {'segment': 'NBA|full_game|spreads', 'league': 'NBA', 'scope': 'full_game', 'family': 'spreads', 'games': 5, 'pnl': 4.0, 'roi_pct': 12.0},
            # Single-platform non-Polymarket market
            {'segment': 'CHALLENGER|full_game|odd_even', 'league': 'CHALLENGER', 'scope': 'full_game', 'family': 'odd_even', 'games': 15, 'pnl': 9.0, 'roi_pct': 14.0},
        ],
        'underperforming_segments': [],
    }
    monkeypatch.setattr(
        'betting_engine.segment_scoreboard.compute_segment_scoreboard',
        lambda conn: fake_scoreboard,
    )
    monkeypatch.setattr(
        PolymarketAutonomousExecutor,
        'is_market_eligible',
        lambda self, league, market_family='totals', scope='full_game', require_tier_4=True: (
            (league, scope, market_family) in {('MLS', 'full_game', 'totals'), ('CHALLENGER', 'full_game', 'odd_even')},
            'mock_check',
            {},
        ),
    )

    conn = sqlite3.connect(str(tmp_path / 'engine.sqlite3'))
    conn.execute('CREATE TABLE IF NOT EXISTS learning_jobs (id INTEGER PRIMARY KEY, kind TEXT, status TEXT, league TEXT, payload TEXT, result TEXT, updated_at TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS system_decisions (tip_id TEXT PRIMARY KEY, game_id TEXT, league TEXT, market TEXT, payload TEXT, issued_at TEXT, first_seen_at TEXT, last_seen_at TEXT)')
    conn.commit()
    conn.close()

    rep = FunnelProportionalitySupervisor(tmp_path).audit_and_heal().to_dict()
    s6 = rep['funnel_stages']['tier4_polymarket']
    assert is_dual_reachable_contract('MLS', 'full_game', 'totals') is True
    assert is_dual_reachable_contract('CHALLENGER', 'full_game', 'odd_even') is False
    assert s6['stake_alpha_segments_count'] == 3
    assert s6['eligible_alpha_segments'] == 2
    assert s6['active_dual_reachable_count'] == 1
