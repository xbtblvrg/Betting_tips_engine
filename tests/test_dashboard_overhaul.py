from __future__ import annotations

import copy
import json
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


def _create_settlement_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY,
            tip_id TEXT,
            stake REAL,
            odds REAL,
            platform TEXT,
            status TEXT,
            pnl REAL,
            payload TEXT,
            created_at TEXT,
            settled_at TEXT
        );
        CREATE TABLE system_decisions (
            tip_id TEXT PRIMARY KEY,
            game_id TEXT,
            league TEXT,
            market TEXT,
            payload TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT
        );
        CREATE TABLE bet_outcomes (
            bet_id INTEGER PRIMARY KEY,
            tip_id TEXT,
            league TEXT,
            game_id TEXT,
            market TEXT,
            model_version TEXT,
            predicted_probability REAL,
            outcome INTEGER,
            result TEXT,
            home_score REAL,
            away_score REAL,
            settled_at TEXT,
            source_url TEXT,
            evidence TEXT
        );
        """
    )
    return conn


def _insert_settled_ticket(
    conn: sqlite3.Connection,
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
) -> None:
    payload = {
        'id': tip_id,
        'league': league,
        'game_id': f'game_{bet_id}',
        'home': 'Arsenal',
        'away': 'Chelsea',
        'start': '2026-11-20T15:00:00+00:00',
        'market': market,
        'market_display': 'Asian Handicap' if market == 'spreads' else 'Asian Total',
        'model_scope': 'full_game',
        'side': side,
        'line': line,
        'selection': f'{side} {line}',
        'probability': 0.56,
        'push_probability': 0.0,
        'reference_odds': odds,
        'stake': stake,
        'tracking_source': 'auto_assumed_placed',
        'model_version': model_version,
    }
    evidence = {
        'source_url': 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard',
        'fetched_at': '2026-11-20T18:00:00+00:00',
        'sha256': 'a' * 64,
        **(evidence_extra or {}),
    }
    conn.execute(
        'INSERT INTO bets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            bet_id,
            tip_id,
            stake,
            odds,
            'Stake.com',
            status,
            pnl,
            json.dumps(payload),
            '2026-11-20T12:00:00+00:00',
            '2026-11-20T18:00:00+00:00',
        ),
    )
    conn.execute(
        'INSERT INTO system_decisions VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            tip_id,
            f'game_{bet_id}',
            league,
            market,
            json.dumps(payload),
            '2026-11-20T12:00:00+00:00',
            '2026-11-20T12:00:00+00:00',
        ),
    )
    conn.execute(
        'INSERT INTO bet_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            bet_id,
            tip_id,
            league,
            f'game_{bet_id}',
            market,
            outcome_model_version or model_version,
            0.56,
            1 if status == 'won' else (0 if status == 'lost' else None),
            status,
            home_score,
            away_score,
            '2026-11-20T18:00:00+00:00',
            'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard',
            json.dumps(evidence),
        ),
    )
    conn.commit()


def test_verify_summary_memoization_speed_and_nested_tamper_detection(tmp_path: Path):
    db_path = tmp_path / 'outcomes.sqlite3'
    conn = _create_settlement_db(db_path)
    _insert_settled_ticket(
        conn,
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
    conn.close()

    evidence = outcome_sizing.outcome_sizing_evidence(db_path, as_of='2026-11-25T00:00:00+00:00')
    summary = evidence['segments']['NBA|full_game|moneyline']
    assert outcome_sizing.verify_summary(summary) is True

    # Benchmark 5,000 repeated verifications to prove memoization eliminates O(N) re-hashing
    t0 = time.perf_counter()
    for _ in range(5000):
        assert outcome_sizing.verify_summary(summary) is True
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, f'5,000 memoized verify_summary calls took {elapsed:.3f}s (> 0.5s)'

    # Nested mutation inside games[] must fail closed even when summary_sha256 is unchanged
    tampered_nested = copy.deepcopy(summary)
    tampered_nested['games'][0]['game_roi'] = round(tampered_nested['games'][0]['game_roi'] + 0.01, 6)
    assert outcome_sizing.verify_summary(tampered_nested) is False

    # Type coercion (bool vs int) must also fail closed
    tampered_type = copy.deepcopy(summary)
    tampered_type['settled_decisions'] = True
    assert outcome_sizing.verify_summary(tampered_type) is False


def test_quarter_line_asian_settlement_math_accepts_exact_half_splits_and_rejects_wrong_pnl(tmp_path: Path):
    db_path = tmp_path / 'quarter.sqlite3'
    conn = _create_settlement_db(db_path)

    # Case 1: Home -0.25 at odds 2.00, Stake $4.00, Score 1-1 -> half push ($2.00), half loss (-$2.00) => PnL = -$2.00
    _insert_settled_ticket(
        conn,
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
    _insert_settled_ticket(
        conn,
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
    _insert_settled_ticket(
        conn,
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
        evidence_extra={'method': 'stake_rules_walkover_void_resolution'},
    )
    conn.close()

    report_clean = statistics.build_statistics(db_path)
    assert report_clean['resolution_audit']['issues'] == []

    # Adversarial check A: Full win score (3-0 on Home -0.25) recorded with half-win PnL MUST be flagged!
    conn = sqlite3.connect(str(db_path))
    _insert_settled_ticket(
        conn,
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
    _insert_settled_ticket(
        conn,
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
        evidence_extra={'method': 'stake_rules_walkover_void_resolution'},
    )
    conn.close()

    report_bad = statistics.build_statistics(db_path)
    issues = report_bad['resolution_audit']['issues']
    codes_by_tip = {(i.get('tip_id') or i.get('bet_id')): i['code'] for i in issues}
    assert any(i.get('bet_id') == 13 and i['code'] == 'recorded_pnl_disagrees_with_result' for i in issues)
    assert any(i.get('bet_id') == 14 for i in issues)
    for item in issues:
        assert isinstance(item.get('reason'), str) and len(item['reason']) > 10
        assert isinstance(item.get('detail'), str) and len(item['detail']) > 10


def test_funnel_supervisor_tier4_counts_match_executor_eligibility(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake_scoreboard = {
        'alpha_segments': [
            # Meets Stake alpha list, and only MLS|full_game|totals + ATP|period_1|totals pass Tier-4 check
            {'segment': 'MLS|full_game|totals', 'league': 'MLS', 'scope': 'full_game', 'family': 'totals', 'games': 14, 'pnl': 18.5, 'roi_pct': 22.0},
            # Has < 10 games / fails strict Tier 4 check even though in raw alpha_segments
            {'segment': 'NBA|full_game|spreads', 'league': 'NBA', 'scope': 'full_game', 'family': 'spreads', 'games': 5, 'pnl': 4.0, 'roi_pct': 12.0},
            # Single-platform non-Polymarket market
            {'segment': 'ATP|period_1|totals', 'league': 'ATP', 'scope': 'period_1', 'family': 'totals', 'games': 15, 'pnl': 9.0, 'roi_pct': 14.0},
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
            (league, scope, market_family) in {('MLS', 'full_game', 'totals'), ('ATP', 'period_1', 'totals')},
            'mock_check',
            {},
        ),
    )

    conn = sqlite3.connect(str(tmp_path / 'engine.sqlite3'))
    conn.execute('CREATE TABLE IF NOT EXISTS learning_jobs (id INTEGER PRIMARY KEY, kind TEXT, status TEXT, league TEXT, payload TEXT, result TEXT, updated_at TEXT)')
    conn.commit()
    conn.close()

    rep = FunnelProportionalitySupervisor(tmp_path).audit_and_heal().to_dict()
    s6 = rep['stages_summary']['tier4_polymarket']
    assert is_dual_reachable_contract('MLS', 'full_game', 'totals') is True
    assert is_dual_reachable_contract('ATP', 'period_1', 'totals') is False
    assert s6['stake_alpha_segments_count'] == 3
    assert s6['eligible_alpha_segments'] == 2
    assert s6['active_dual_reachable_count'] == 1
