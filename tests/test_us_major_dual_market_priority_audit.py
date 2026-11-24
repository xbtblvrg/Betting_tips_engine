"""Independent behavioral verification of US Major & Dual-Reachable Market R&D/Development Priority.

Validates Commandment 6.5 invariants across:
1. LearningWorker job scheduler (choose_learning_job) — live US Majors & Dual markets preempt
   off-season (MLB/WNBA/WTA) and deprioritized fringe (CHALLENGER/NCAAW) R&D jobs across both
   normal and fairness-budget turns.
2. BottleneckWatchdog._check_stagnant_quarantine — selects live Dual-Reachable US Major
   segments ahead of off-season / non-dual / fringe rows regardless of SQLite insertion order.
3. PolymarketRDPrioritizer._execute_gap_scan — re-queues stale completed Tier 1 Polymarket
   gaps after the 6h cooldown while leaving recent completed or Tier 2 incubating rows intact.
4. PolymarketRunner gap deduplication — collapses CBB/NCAAB/NCAAM and CFB/NCAAF aliases into
   single canonical jobs without triplicate queue bloat.
5. EngineeringLoop task_focus — orders multi-league bucket evidence and leagues with NBA/NFL
   ahead of ATP/CHALLENGER/off-season leagues.
"""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import MagicMock, patch

from betting_engine.bottleneck_watchdog import BottleneckWatchdog
from betting_engine.engineering_loop import sync_engineering_loop
from betting_engine.learning import PRIORITY, choose_learning_job
from betting_engine.polymarket_rd_prioritizer import PolymarketRDPrioritizer


def test_urgent_polymarket_repair_is_priority_1_and_beats_offseason_and_fringe_rnd(monkeypatch):
    assert PRIORITY['urgent_polymarket_model_repair'] == 1
    now = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        'betting_engine.contracts.get_live_active_leagues',
        lambda data_dir=None: {'NBA', 'NFL', 'NHL', 'MLS', 'NCAAF', 'EPL', 'UCL', 'NCAAM'},
    )

    rows = [
        {
            'id': 'offseason_mlb',
            'kind': 'autonomous_alpha_recovery',
            'league': 'MLB',
            'status': 'queued',
            'created_at': (now - timedelta(minutes=25)).isoformat(),
            'next_attempt': (now - timedelta(minutes=25)).isoformat(),
            'payload': json.dumps({'segment': 'MLB|innings_1_5|three_way'}),
        },
        {
            'id': 'fringe_challenger',
            'kind': 'market_research',
            'league': 'CHALLENGER',
            'status': 'queued',
            'created_at': (now - timedelta(minutes=28)).isoformat(),
            'next_attempt': (now - timedelta(minutes=28)).isoformat(),
            'payload': json.dumps({'segment': 'CHALLENGER|full_game|moneyline'}),
        },
        {
            'id': 'live_nba_turnaround',
            'kind': 'submarket_turnaround_research',
            'league': 'NBA',
            'status': 'queued',
            'created_at': (now - timedelta(minutes=2)).isoformat(),
            'next_attempt': (now - timedelta(minutes=2)).isoformat(),
            'payload': json.dumps({'segment': 'NBA|quarter_4|spreads'}),
        },
    ]

    # Even though MLB autonomous_alpha_recovery has raw PRIORITY=1 and is older,
    # MLB is off-season so live Dual NBA submarket_turnaround_research (PRIORITY=2) must win!
    selected, _ = choose_learning_job(rows, [], now=now)
    assert selected['id'] == 'live_nba_turnaround'

    # Under fairness turn (after 3 high-priority runs), live Dual NBA still beats older CHALLENGER (< 30m cap)
    history = [{'kind': 'shadow_evaluation'}] * 3
    selected_bg, meta_bg = choose_learning_job(rows, history, now=now)
    assert selected_bg['id'] == 'live_nba_turnaround'
    assert meta_bg['reason'] == 'background_turn_after_priority_budget'


def test_dual_market_beats_single_platform_within_same_league_and_nfl_follows_nba(monkeypatch):
    now = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        'betting_engine.contracts.get_live_active_leagues',
        lambda data_dir=None: {'NBA', 'NFL', 'NHL'},
    )

    rows = [
        {
            'id': 'nba_stake_only_team_total',
            'kind': 'autonomous_alpha_recovery',
            'league': 'NBA',
            'status': 'queued',
            'created_at': (now - timedelta(minutes=20)).isoformat(),
            'next_attempt': (now - timedelta(minutes=20)).isoformat(),
            'payload': json.dumps({'segment': 'NBA|full_game|home_total'}),
        },
        {
            'id': 'nfl_dual_spread',
            'kind': 'autonomous_alpha_recovery',
            'league': 'NFL',
            'status': 'queued',
            'created_at': (now - timedelta(minutes=5)).isoformat(),
            'next_attempt': (now - timedelta(minutes=5)).isoformat(),
            'payload': json.dumps({'segment': 'NFL|full_game|spreads'}),
        },
        {
            'id': 'nba_dual_total',
            'kind': 'urgent_polymarket_model_repair',
            'league': 'NBA',
            'status': 'queued',
            'created_at': (now - timedelta(minutes=1)).isoformat(),
            'next_attempt': (now - timedelta(minutes=1)).isoformat(),
            'payload': json.dumps({'segment_key': 'NBA|full_game|totals'}),
        },
    ]

    first, _ = choose_learning_job(rows, [], now=now)
    assert first['id'] == 'nba_dual_total'

    second, _ = choose_learning_job([r for r in rows if r['id'] != 'nba_dual_total'], [], now=now)
    assert second['id'] == 'nfl_dual_spread'


def test_watchdog_stagnant_quarantine_skips_offseason_and_fringe_in_favor_of_nba_nfl_dual(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    future_iso = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()

    # Live upcoming games ONLY in NBA and NFL (MLB/WNBA/CHALLENGER have 0 live games)
    (data_dir / 'games.json').write_text(json.dumps([
        {'id': 'nba_1', 'league': 'NBA', 'state': 'pre', 'start': future_iso},
        {'id': 'nfl_1', 'league': 'NFL', 'state': 'pre', 'start': future_iso},
    ]))

    inc_db = data_dir / 'shadow_incubator.sqlite3'
    with sqlite3.connect(inc_db) as c:
        c.execute("""
            CREATE TABLE demoted_markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment_key TEXT, league TEXT, scope TEXT, family TEXT, sub_family TEXT,
                incubation_phase TEXT, demoted_at TEXT, reason TEXT
            )
        """)
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        # Insert 25 off-season MLB / fringe CHALLENGER rows FIRST so naive LIMIT 20 would miss NBA/NFL!
        for idx in range(15):
            c.execute(
                "INSERT INTO demoted_markets(segment_key, league, scope, family, sub_family, incubation_phase, demoted_at, reason) VALUES (?,?,?,?,?,?,?,?)",
                (f"MLB|innings_1_5|totals_{idx}", "MLB", "innings_1_5", "totals", "", "research_training", old_ts, "offseason"),
            )
        for idx in range(10):
            c.execute(
                "INSERT INTO demoted_markets(segment_key, league, scope, family, sub_family, incubation_phase, demoted_at, reason) VALUES (?,?,?,?,?,?,?,?)",
                (f"CHALLENGER|full_game|moneyline_{idx}", "CHALLENGER", "full_game", "moneyline", "", "research_training", old_ts, "fringe"),
            )
        c.execute(
            "INSERT INTO demoted_markets(segment_key, league, scope, family, sub_family, incubation_phase, demoted_at, reason) VALUES (?,?,?,?,?,?,?,?)",
            ("NBA|full_game|home_total", "NBA", "full_game", "home_total", "", "research_training", old_ts, "stake_only"),
        )
        c.execute(
            "INSERT INTO demoted_markets(segment_key, league, scope, family, sub_family, incubation_phase, demoted_at, reason) VALUES (?,?,?,?,?,?,?,?)",
            ("NFL|half_1|spreads", "NFL", "half_1", "spreads", "", "research_training", old_ts, "dual_nfl"),
        )
        c.execute(
            "INSERT INTO demoted_markets(segment_key, league, scope, family, sub_family, incubation_phase, demoted_at, reason) VALUES (?,?,?,?,?,?,?,?)",
            ("NBA|full_game|moneyline", "NBA", "full_game", "moneyline", "", "research_training", old_ts, "dual_nba"),
        )

    eng_db = data_dir / 'engine.sqlite3'
    with sqlite3.connect(eng_db) as c:
        c.execute("""
            CREATE TABLE learning_jobs (
                id TEXT PRIMARY KEY, kind TEXT, league TEXT, status TEXT,
                created_at TEXT, updated_at TEXT, next_attempt TEXT, attempts INTEGER, payload TEXT
            )
        """)

    wd = BottleneckWatchdog(data_dir)
    wd._check_stagnant_quarantine()

    with sqlite3.connect(eng_db) as c:
        queued = [
            (r[0], json.loads(r[1])['segment'])
            for r in c.execute("SELECT league, payload FROM learning_jobs WHERE status='queued' ORDER BY rowid ASC").fetchall()
        ]

    # Only the 3 live US Major markets are enqueued (0 MLB, 0 CHALLENGER), with Dual NBA & Dual NFL first!
    assert queued == [
        ('NBA', 'NBA|full_game|moneyline'),
        ('NFL', 'NFL|half_1|spreads'),
        ('NBA', 'NBA|full_game|home_total'),
    ]


def test_polymarket_rd_prioritizer_requeues_stale_completed_tier1_gaps(tmp_path, monkeypatch):
    db_path = tmp_path / 'engine.sqlite3'
    old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    with sqlite3.connect(db_path) as c:
        c.execute("""
            CREATE TABLE learning_jobs (
                id TEXT PRIMARY KEY, kind TEXT, league TEXT, status TEXT,
                created_at TEXT, updated_at TEXT, next_attempt TEXT, attempts INTEGER DEFAULT 0, payload TEXT
            )
        """)
        c.execute(
            "INSERT INTO learning_jobs VALUES ('poly_gap_nba_full_game_moneyline', 'urgent_polymarket_model_repair', 'NBA', 'completed', ?, ?, ?, 1, '{}')",
            (old_ts, old_ts, old_ts),
        )
        c.execute(
            "INSERT INTO learning_jobs VALUES ('poly_gap_nfl_full_game_moneyline', 'urgent_polymarket_model_repair', 'NFL', 'completed', ?, ?, ?, 1, '{}')",
            (recent_ts, recent_ts, recent_ts),
        )

    monkeypatch.setattr('betting_engine.contracts.get_live_active_leagues', lambda data_dir=None: {'NBA', 'NFL'})
    mock_exec = MagicMock()
    mock_exec.is_market_eligible.return_value = (False, 'MARKET_DEMOTED_TIER_1', 0.0)
    prio = PolymarketRDPrioritizer(db_path=db_path, cache_dir=tmp_path / 'poly_cache', executor=mock_exec)
    monkeypatch.setattr(prio.service, 'get_open_events', lambda league, horizon_hours=72.0: [])

    prio.scan_and_prioritize_gaps(leagues=('NBA', 'NFL'), force_refresh=True)

    with sqlite3.connect(db_path) as c:
        nba_st = c.execute("SELECT status FROM learning_jobs WHERE id='poly_gap_nba_full_game_moneyline'").fetchone()[0]
        nfl_st = c.execute("SELECT status FROM learning_jobs WHERE id='poly_gap_nfl_full_game_moneyline'").fetchone()[0]

    # 5-day-old completed Tier 1 gap is re-queued; 30-min-old completed gap respects 6h cooldown
    assert nba_st == 'queued'
    assert nfl_st == 'completed'


def test_engineering_loop_places_nba_and_nfl_ahead_of_challenger_and_atp_in_task_focus(tmp_path, monkeypatch):
    monkeypatch.setattr('betting_engine.engineering_loop.get_live_active_leagues', lambda: {'NBA', 'NFL', 'ATP', 'CHALLENGER'})
    now = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)
    stamp = now.isoformat()
    plan = {'tasks': [{'id': 'D09', 'milestone': 'M04', 'priority': 9, 'depends_on': []}], 'milestones': []}
    rows = [
        {'league': 'CHALLENGER', 'section': 'period', 'market': '1st Set - Total Games', 'observed_outcomes': 2, 'active_standard_outcomes': 0, 'supported_outcomes': 0, 'evaluated_outcomes': 0},
        {'league': 'ATP', 'section': 'period', 'market': '1st Set - Total Games', 'observed_outcomes': 2, 'active_standard_outcomes': 0, 'supported_outcomes': 0, 'evaluated_outcomes': 0},
        {'league': 'NFL', 'section': 'period', 'market': '1st Quarter - Total', 'observed_outcomes': 2, 'active_standard_outcomes': 0, 'supported_outcomes': 0, 'evaluated_outcomes': 0},
        {'league': 'NBA', 'section': 'period', 'market': '1st Quarter - Total', 'observed_outcomes': 2, 'active_standard_outcomes': 0, 'supported_outcomes': 0, 'evaluated_outcomes': 0},
    ]
    payload = {'generated_at': stamp, 'market_types': rows, 'opportunity_inventory': {}}
    ops = {'checked_at': stamp, 'publication_at': stamp, 'gaps': [], 'tracked_gaps': [], 'leagues': [], 'daily_progress': {'target': 100, 'issued': 100, 'shortfall': 0}}
    comps = {'checked_at': stamp, 'checks': []}

    report = sync_engineering_loop(tmp_path, plan, {}, payload=payload, operations=ops, components=comps, now=now)
    focus = report['task_focus']['D09']
    assert focus['leagues'] == ['NBA', 'NFL', 'ATP', 'CHALLENGER']
    assert [e['id'].split(':')[1] for e in focus['evidence']] == ['NBA', 'NFL', 'ATP', 'CHALLENGER']


def test_cross_kind_segment_synthesis_jobs_share_exclusivity_key(tmp_path):
    from betting_engine.learning import LearningWorker
    from betting_engine.storage import Store
    w = LearningWorker(Store(tmp_path / 'e.db'), tmp_path / 'data')

    k1 = w._exclusivity_key({'kind': 'urgent_polymarket_model_repair', 'league': 'NBA', 'payload': '{"segment_key": "NBA|half_2|moneyline"}'})
    k2 = w._exclusivity_key({'kind': 'submarket_turnaround_research', 'league': 'NBA', 'payload': '{"segment": "NBA|half_2|moneyline"}'})
    k3 = w._exclusivity_key({'kind': 'autonomous_alpha_recovery', 'league': 'NBA', 'payload': '{"segment": "NBA|half_2|moneyline"}'})
    k4 = w._exclusivity_key({'kind': 'priority_model_synthesis', 'league': 'NBA', 'payload': '{"segment_key": "NBA|half_2|moneyline"}'})

    assert k1 == k2 == k3 == k4 == 'seg_synth:NBA|half_2|moneyline'


def test_verbose_logger_self_healing_skips_production_db_during_pytest_but_allows_tmp_path(tmp_path):
    import logging
    from betting_engine.verbose_logger import JsonlLogHandler, ROOT

    src_file = str((ROOT / 'src/betting_engine/learning.py').resolve())

    # 1. Handler bound to tmp_path MUST enqueue runtime_exception_auto_repair into tmp_path/engine.sqlite3
    tmp_db = tmp_path / 'engine.sqlite3'
    with sqlite3.connect(tmp_db) as c:
        c.execute("""
            CREATE TABLE learning_jobs (
                id TEXT PRIMARY KEY, kind TEXT, league TEXT, status TEXT,
                created_at TEXT, updated_at TEXT, next_attempt TEXT, attempts INTEGER, payload TEXT
            )
        """)
    h_tmp = JsonlLogHandler(tmp_path / 'logs' / 'verbose_debug.jsonl')
    rec = logging.LogRecord('test', logging.ERROR, src_file, 100, 'tmp runtime boom', (), None)
    h_tmp.emit(rec)
    with sqlite3.connect(tmp_db) as c:
        assert c.execute("SELECT COUNT(*) FROM learning_jobs WHERE kind='runtime_exception_auto_repair'").fetchone()[0] == 1

    # 2. Handler bound to ROOT / 'data' MUST NOT enqueue pytest fault-injection jobs into production engine.sqlite3
    prod_db = ROOT / 'data' / 'engine.sqlite3'
    if prod_db.exists():
        with sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True) as c:
            before_cnt = c.execute("SELECT COUNT(*) FROM learning_jobs WHERE kind='runtime_exception_auto_repair'").fetchone()[0]
        h_prod = JsonlLogHandler(ROOT / 'data' / 'logs' / 'verbose_debug.jsonl')
        rec_prod = logging.LogRecord('test', logging.ERROR, src_file, 99999, 'simulated pytest fault unique_xyz', (), None)
        h_prod._trigger_self_healing_from_log(rec_prod, {'message': 'simulated pytest fault unique_xyz', 'logger': 'test'})
        with sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True) as c:
            after_cnt = c.execute("SELECT COUNT(*) FROM learning_jobs WHERE kind='runtime_exception_auto_repair'").fetchone()[0]
        assert after_cnt == before_cnt


def test_historical_trainer_rescales_cross_scope_lines_by_exact_scope_ratio(tmp_path):
    from betting_engine.historical_trainer import HistoricalCandidateTrainer

    db_file = tmp_path / 'shadow_incubator.sqlite3'
    with sqlite3.connect(db_file) as c:
        c.execute("""
            CREATE TABLE incubated_wagers (
                id TEXT PRIMARY KEY, game_id TEXT, league TEXT, scope TEXT,
                family TEXT, sub_family TEXT, selection TEXT, side TEXT,
                line REAL, odds REAL, model_prob REAL, market_prob REAL,
                captured_at TEXT, status TEXT, result TEXT, pnl REAL
            )
        """)
        rows = [
            ('w_fg', 'g1', 'NFL', 'full_game', 'totals', 'totals', 'Over 45.0', 'over', 45.0, 1.91, 0.54, 0.52, '2026-11-01T18:00:00Z', 'settled', 'won', 0.91),
            ('w_h1', 'g2', 'NFL', 'half_1', 'totals', 'totals', 'Over 22.5', 'over', 22.5, 1.91, 0.54, 0.52, '2026-11-02T18:00:00Z', 'settled', 'lost', -1.0),
            ('w_q1', 'g3', 'NFL', 'quarter_1', 'totals', 'totals', 'Over 10.5', 'over', 10.5, 1.91, 0.54, 0.52, '2026-11-03T18:00:00Z', 'settled', 'won', 0.91),
        ]
        c.executemany("INSERT INTO incubated_wagers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    trainer = HistoricalCandidateTrainer(db_path=db_file)
    wagers = trainer._fetch_settled_wagers('NFL', 'half_2', 'totals')
    rescaled_lines = [r[1] for r in wagers]

    # full_game 45.0 * (0.50/1.00) = 22.5; half_1 22.5 * (0.50/0.50) = 22.5; quarter_1 10.5 * (0.50/0.25) = 21.0
    assert rescaled_lines == [22.5, 22.5, 21.0]


def test_validator_scales_total_line_to_nba_context_and_provides_scope_handicap_keys():
    from betting_engine.historical_trainer import get_default_segment_context
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution
    from betting_engine.sandbox_models.validator import validate_candidate_model

    ctx = get_default_segment_context('NBA', 'half_2')
    assert ctx['score_scope'] == 'half_2'
    assert ctx['scope'] == 'half_2'
    assert ctx['home_handicap'] == -1.5

    class StrictScopeNBAModel(BaseCandidateModel):
        def __init__(self):
            super().__init__(model_id='m_nba_h2', version='1.0.0', league='NBA', scope='half_2', family='totals')

        def predict_distribution(self, game_context):
            if game_context.get('score_scope') != 'half_2':
                raise ValueError("Explicit score_scope='half_2' is required.")
            if 'home_handicap' not in game_context:
                raise ValueError("home_handicap is required.")
            return DiscreteDistribution.from_scores({(56, 54): 0.52, (53, 55): 0.48})

        def evaluate_market(self, market, selection, line, odds, game_context, side=None):
            dist = self.predict_distribution(game_context)
            if market == 'totals':
                # Without clamping, static line=5.5 on 110-point NBA half would give p=1.0 and fail [0.01, 0.99]!
                p = sum(prob for (h, a), prob in dist.scores.items() if (h + a) > float(line))
                return p, round(p * odds - 1.0, 4)
            return super().evaluate_market(market, selection, line, odds, game_context, side=side)

    ok, errs = validate_candidate_model(StrictScopeNBAModel(), ctx)
    assert ok is True, f"Expected pre-flight pass on NBA half_2 model, got: {errs}"


def test_executive_inquisitor_manual_boundary_4tier_and_honest_10pct_roi_gap(tmp_path):
    import pytest
    from datetime import datetime, timedelta, timezone
    from betting_engine.executive_inquisitor import AutonomousExecutiveInquisitor

    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(minutes=3)).isoformat()
    settled_ts = (now - timedelta(days=1)).isoformat()

    with sqlite3.connect(tmp_path / 'engine.sqlite3') as c:
        c.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, finished_at TEXT)")
        c.execute("CREATE TABLE learning_jobs (id TEXT PRIMARY KEY, status TEXT, updated_at TEXT)")
        c.execute("CREATE TABLE system_decisions (id TEXT PRIMARY KEY, issued_at TEXT, stake REAL)")
        c.execute("CREATE TABLE bets (id INTEGER PRIMARY KEY, created_at TEXT, stake REAL, odds REAL, status TEXT, pnl REAL)")
        c.execute("INSERT INTO runs VALUES ('r1', ?)", (recent_ts,))
        c.execute("INSERT INTO learning_jobs VALUES ('j1', 'completed', ?)", (recent_ts,))
        c.execute("INSERT INTO system_decisions VALUES ('d1', ?, 1.50)", (recent_ts,))
        # Positive PnL (+$15 on $800 stake = +1.88% ROI), strictly below 10.0% weekly target!
        c.execute("INSERT INTO bets VALUES (1, ?, 800.0, 2.0, 'won', 15.0)", (settled_ts,))

    with sqlite3.connect(tmp_path / 'shadow_incubator.sqlite3') as ic:
        ic.execute("CREATE TABLE demoted_markets (id TEXT PRIMARY KEY, segment_key TEXT, incubation_phase TEXT)")
        ic.execute("CREATE TABLE graduated_markets (id TEXT PRIMARY KEY, segment_key TEXT)")
        ic.execute("INSERT INTO demoted_markets VALUES ('dm1', 'NBA|half_1|totals', 'research_training')")
        ic.execute("INSERT INTO demoted_markets VALUES ('dm2', 'NFL|half_1|spreads', 'incubating_with_new_model')")
        ic.execute("INSERT INTO graduated_markets VALUES ('gm1', 'NBA|full_game|spreads')")

    inq = AutonomousExecutiveInquisitor(data_dir=tmp_path)
    q2 = inq.probe_q2_interoperability_and_progression()
    assert 'tier_4_live_proven_alpha_count' in q2.telemetry
    assert q2.telemetry['tier_1_research_count'] == 1
    assert q2.telemetry['tier_2_shadow_incubating_count'] == 1
    assert q2.telemetry['tier_3_production_graduated_count'] == 1

    q5 = inq.probe_q5_autonomous_continuity()
    assert q5.status == 'optimal'
    assert q5.telemetry['execution_boundary'] == 'assumed_manual_execution'
    assert q5.telemetry['automated_stake_submission_prohibited'] is True
    assert not any('Safari' in f for f in q5.findings)

    q6 = inq.probe_q6_prime_financial_goal()
    assert q6.status == 'warning'
    assert q6.telemetry['roi_gap_to_target_pct'] == pytest.approx(8.12, abs=0.02)
    assert any('10.0%' in f for f in q6.findings)


def test_target_evaluator_detects_rolling_7d_volume_shortfall_after_1000_regime_bets(tmp_path):
    from datetime import datetime, timedelta, timezone
    from betting_engine.target_evaluator import evaluate_target_metrics

    now = datetime(2026, 11, 24, 1, 0, tzinfo=timezone.utc)
    old_regime_ts = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc).isoformat()
    recent_ts = (now - timedelta(days=1)).isoformat()

    db_file = tmp_path / 'engine.sqlite3'
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE bets (
                id INTEGER PRIMARY KEY, tip_id TEXT, created_at TEXT,
                stake REAL, odds REAL, platform TEXT, status TEXT, pnl REAL, payload TEXT, request_id TEXT
            )
        """)
        # 800 old bets (>7 days ago, post-Oct 12) + only 50 bets in rolling 7d (even with +25% ROI)
        rows = [(i, f'old_{i}', old_regime_ts, 1.0, 2.0, 'stake', 'won', 0.25, '{}', 'req') for i in range(1, 801)]
        rows += [(800 + i, f'rec_{i}', recent_ts, 1.0, 2.0, 'stake', 'won', 0.25, '{}', 'req') for i in range(1, 51)]
        conn.executemany("INSERT INTO bets VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

        res = evaluate_target_metrics(conn, now=now)

    assert res['regime_stats']['total_bets'] == 850
    assert res['rolling_7d']['total_bets'] == 50
    assert res['gaps']['volume_gap'] == 650
    assert res['status'] == 'TARGET_SHORTFALL'
    assert any('Rolling 7d bet volume (50) below 700/week floor' in r for r in res['gaps']['shortfall_reasons'])


def test_daily_quant_council_excludes_offseason_leagues_when_live_schedule_present(tmp_path):
    import json
    from betting_engine.daily_quant_council import convene_daily_quant_council

    # Active November schedule: NBA, NFL, NHL, NCAAM, MLS (NO WNBA, NO MLB)
    (tmp_path / 'games.json').write_text(json.dumps([
        {'id': 'g1', 'league': 'NBA', 'state': 'pre'},
        {'id': 'g2', 'league': 'NFL', 'state': 'pre'},
        {'id': 'g3', 'league': 'NHL', 'state': 'pre'},
        {'id': 'g4', 'league': 'NCAAM', 'state': 'pre'},
        {'id': 'g5', 'league': 'MLS', 'state': 'pre'},
    ]), encoding='utf-8')

    directive = convene_daily_quant_council(
        data_dir=tmp_path,
        as_of='2026-11-24T08:00:00Z',
        force_refresh=True,
        policy_override={'enabled': False},
    )
    target_leagues = {t.split('|')[0] for t in directive.high_conviction_alpha_targets}
    assert 'WNBA' not in target_leagues
    assert 'MLB' not in target_leagues
    assert 'NBA' in target_leagues
    assert 'NFL' in target_leagues


def test_inquisitor_verbose_debug_and_priority0_self_healing_enqueue(tmp_path, monkeypatch):
    import json
    import logging
    from pathlib import Path
    from betting_engine import verbose_logger as vl_mod
    from betting_engine.executive_inquisitor import AutonomousExecutiveInquisitor

    # Bind singleton verbose logger to tmp_path so we verify structured JSONL debug records directly
    custom_vl = vl_mod.QuantVerboseLogger(data_dir=tmp_path)
    monkeypatch.setattr(vl_mod, '_VERBOSE_LOGGER', custom_vl)
    try:
        src_target = str((Path(__file__).resolve().parents[1] / 'src/betting_engine/learning.py').resolve())
        tb_sample = (
            'Traceback (most recent call last):\n'
            f'  File "{src_target}", line 412, in run_once\n'
            '    raise ValueError("synthetic defect for closed-loop test")\n'
            'ValueError: synthetic defect for closed-loop test\n'
        )

        with sqlite3.connect(tmp_path / 'engine.sqlite3') as c:
            c.execute("""
                CREATE TABLE learning_jobs (
                    id TEXT PRIMARY KEY, kind TEXT, league TEXT, status TEXT,
                    created_at TEXT, updated_at TEXT, next_attempt TEXT, attempts INTEGER,
                    payload TEXT, last_error TEXT
                )
            """)
            c.execute(
                "INSERT INTO learning_jobs VALUES ('fail_1', 'target_evaluation', 'NBA', 'failed', '2026-11-24T00:00:00Z', '2026-11-24T00:01:00Z', '2026-11-24T00:05:00Z', 3, '{}', ?)",
                (tb_sample,),
            )

        inq = AutonomousExecutiveInquisitor(data_dir=tmp_path)
        dossier = inq.run_inquisitor_cycle()
        assert dossier['inquiries_count'] == 7

        # 1. Verify structured DEBUG/WARNING records in verbose_debug.jsonl
        jsonl_lines = (tmp_path / 'logs' / 'verbose_debug.jsonl').read_text(encoding='utf-8').strip().splitlines()
        events = [json.loads(line).get('event_type') for line in jsonl_lines]
        assert 'inquisitor_probe_evaluated' in events
        assert 'inquisitor_cycle_completed' in events
        assert 'self_healing_code_task_enqueued' in events

        # 2. Verify Priority-0 autonomous_code_healing job enqueued in learning_jobs
        with sqlite3.connect(tmp_path / 'engine.sqlite3') as c:
            row = c.execute(
                "SELECT kind, status, payload FROM learning_jobs WHERE kind = 'autonomous_code_healing'"
            ).fetchone()
        assert row is not None
        assert row[1] == 'queued'
        pl = json.loads(row[2])
        assert pl['file'] == 'src/betting_engine/learning.py'
        assert pl['line'] == 412
        assert pl['function'] == 'run_once'
    finally:
        if custom_vl.text_handler:
            logging.getLogger().removeHandler(custom_vl.text_handler)
            logging.getLogger('betting_engine').removeHandler(custom_vl.text_handler)
        if custom_vl.jsonl_handler:
            logging.getLogger().removeHandler(custom_vl.jsonl_handler)
            logging.getLogger('betting_engine').removeHandler(custom_vl.jsonl_handler)


def test_supreme_auditor_escalates_unresolved_defect_to_engineering_loop_priority0_task(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    from betting_engine.supreme_system_auditor import SupremeSystemAuditor
    from betting_engine.engineering_loop import engineering_input_generation, sync_engineering_loop

    (tmp_path / 'src/betting_engine').mkdir(parents=True, exist_ok=True)
    dummy_mod = tmp_path / 'src/betting_engine/sample_mod.py'
    dummy_mod.write_text("def calc():\n    return 1 / 0\n", encoding='utf-8')

    gen_before = engineering_input_generation(tmp_path, {})

    auditor = SupremeSystemAuditor(root=tmp_path, data_dir=tmp_path / 'data')
    tb_txt = (
        'Traceback (most recent call last):\n'
        f'  File "{dummy_mod}", line 2, in calc\n'
        '    return 1 / 0\n'
        'ZeroDivisionError: division by zero\n'
    )
    res = auditor.execute_autonomous_code_healing({
        'category': 'runtime_exception_auto_repair',
        'exception_type': 'ZeroDivisionError',
        'exception_message': 'division by zero',
        'traceback': tb_txt,
    })

    # Stage 1 has no config/ai_delegation.json in tmp_path -> must escalate to Stage 2 EngineeringLoop!
    assert res['status'] == 'unresolved'
    gap_id = res.get('escalated_gap_id')
    assert gap_id and gap_id.startswith('self_heal:')

    # EngineeringLoop wakeup must detect the new escalation immediately
    assert engineering_input_generation(tmp_path, {}) != gen_before

    now = datetime.now(timezone.utc) + timedelta(seconds=5)
    pub = {'generated_at': now.isoformat(), 'market_types': [], 'status': {'model_quality': {}}, 'models': {}}
    ops = {'checked_at': now.isoformat(), 'publication_at': now.isoformat(), 'state': 'healthy', 'gaps': [], 'tracked_gaps': [], 'leagues': [], 'daily_progress': {'target': 100, 'issued': 105, 'shortfall': 0}}
    rep = sync_engineering_loop(tmp_path, plan={'tasks': []}, state={'tasks': {}}, payload=pub, operations=ops, components={}, now=now)

    extra_buckets = {t['engineering_bucket']: t for t in rep['extra_tasks']}
    assert 'self_healing_code' in extra_buckets
    sh_task = extra_buckets['self_healing_code']
    assert sh_task['priority'] == 0
    assert gap_id in sh_task['engineering_gap_ids']

    # Once resolved later, sync_engineering_loop must retire the extra task
    esc_file = tmp_path / 'data/development/self_healing_escalations.json'
    esc_doc = json.loads(esc_file.read_text(encoding='utf-8'))
    for item in esc_doc['items'].values():
        item['state'] = 'resolved'
        item['resolved_at'] = (now + timedelta(minutes=2)).isoformat()
    esc_file.write_text(json.dumps(esc_doc), encoding='utf-8')

    rep2 = sync_engineering_loop(
        tmp_path,
        plan={'tasks': []},
        state={'tasks': {}, 'extra_tasks': rep['extra_tasks']},
        payload=pub,
        operations=ops,
        components={},
        now=now + timedelta(minutes=3),
    )
    assert any(s['task_id'] == sh_task['id'] for s in rep2['task_supersessions'])


def test_espn_original_accepts_neutral_site_college_swapped_schedule_and_saint_punctuation():
    from betting_engine.domain import Game
    from betting_engine.espn_results import _original, _team_identity_matches

    # 1. Punctuation normalization: 'Mount St. Joseph Lions' matches ESPN 'Mount St Joseph Lions'
    assert _team_identity_matches({'displayName': 'Mount St Joseph Lions'}, 'Mount St. Joseph Lions') is True

    # 2. Neutral-court college schedule vs tip home/away swap maps team IDs to tip's original sides
    tip = {
        'game_id': 'NCAAM:401929445',
        'league': 'NCAAM',
        'home': 'Southern Utah Thunderbirds',
        'away': 'Arkansas State Red Wolves',
        'start': '2026-11-23T20:30:00Z',
        'market': 'spreads',
    }
    existing = Game(
        id='NCAAM:401929445',
        league='NCAAM',
        start='2026-11-23T20:45:00Z',
        home='Arkansas State Red Wolves',
        away='Southern Utah Thunderbirds',
        home_id='2032',
        away_id='253',
        home_abbr='ARKST',
        away_abbr='SUU',
        state='pre',
        season=2027,
        neutral=True,
    )
    merged = _original([tip], existing)
    assert merged['home'] == 'Southern Utah Thunderbirds'
    assert merged['away'] == 'Arkansas State Red Wolves'
    assert merged['home_id'] == '253'
    assert merged['away_id'] == '2032'


def test_polymarket_executor_blocks_cbb_alias_when_ncaam_is_demoted(tmp_path):
    from betting_engine.polymarket_executor import PolymarketAutonomousExecutor

    with sqlite3.connect(tmp_path / 'shadow_incubator.sqlite3') as ic:
        ic.execute("""
            CREATE TABLE demoted_markets (
                id TEXT PRIMARY KEY, segment_key TEXT, league TEXT, scope TEXT, family TEXT, sub_family TEXT,
                demoted_at TEXT, rolling_roi REAL, rolling_pnl REAL, games INT, reason TEXT, stats TEXT,
                incubation_phase TEXT
            )
        """)
        ic.execute("""
            CREATE TABLE graduated_markets (
                id TEXT PRIMARY KEY, segment_key TEXT, league TEXT, scope TEXT, family TEXT, sub_family TEXT,
                graduated_at TEXT, sample_size INT, roi REAL, delta_brier REAL, max_drawdown REAL, win_rate REAL, stats TEXT
            )
        """)
        ic.execute(
            "INSERT INTO demoted_markets VALUES ('dm_ncaam', 'NCAAM|full_game|spreads', 'NCAAM', 'full_game', 'spreads', 'spreads', '2026-11-24T00:00:00Z', -0.25, -15.0, 12, 'demoted', '{}', 'research_training')"
        )

    poly = PolymarketAutonomousExecutor(db_path=tmp_path / 'engine.sqlite3')
    # Inject stale positive NCAAM alpha into cached scoreboard — CBB must STILL be blocked by NCAAM demotion!
    poly._cached_scoreboard = {
        'alpha_segments': [{
            'segment': 'NCAAM|full_game|spreads', 'league': 'NCAAM', 'scope': 'full_game', 'family': 'spreads',
            'games': 15, 'rolling_games': 15, 'pnl': 10.0, 'rolling_pnl': 10.0, 'roi_pct': 12.0, 'rolling_roi_pct': 12.0, 'rolling_units': 2.5,
        }],
        'exploration_segments': [],
        'underperforming_segments': [],
    }
    poly._scoreboard_cached_at = 9999999999.0

    ok, reason, cap = poly.is_market_eligible('CBB', 'full_game', 'spreads', require_tier_4=True)
    assert ok is False
    assert cap == 0.0
    assert 'DEMOTED_IN_SHADOW_INCUBATOR' in reason


def test_resolve_polymarket_outcome_is_home_eliminates_shared_word_side_inversion():
    from betting_engine.polymarket_runner import resolve_polymarket_outcome_is_home

    # Shared 'State': previously 'Arkansas State' matched 'Long Beach State' on 'state' -> inverted is_home=True!
    assert resolve_polymarket_outcome_is_home(
        'Long Beach State Beach', 'Spread: Long Beach State (-3.5)',
        'Long Beach State Beach', 'Arkansas State Red Wolves', idx=0
    ) is True
    assert resolve_polymarket_outcome_is_home(
        'Arkansas State Red Wolves', 'Spread: Long Beach State (-3.5)',
        'Long Beach State Beach', 'Arkansas State Red Wolves', idx=1
    ) is False

    # Shared 'Los Angeles': Lakers vs Clippers
    assert resolve_polymarket_outcome_is_home(
        'Clippers', 'Spread: Lakers (-4.5)',
        'Los Angeles Lakers', 'Los Angeles Clippers', idx=1
    ) is False
    assert resolve_polymarket_outcome_is_home(
        'Lakers', 'Spread: Lakers (-4.5)',
        'Los Angeles Lakers', 'Los Angeles Clippers', idx=0
    ) is True

    # Shared 'New': New York Knicks vs New Orleans Pelicans
    assert resolve_polymarket_outcome_is_home(
        'Pelicans', 'Spread: Knicks (-6.5)',
        'New York Knicks', 'New Orleans Pelicans', idx=1
    ) is False
    assert resolve_polymarket_outcome_is_home(
        'Knicks', 'Spread: Knicks (-6.5)',
        'New York Knicks', 'New Orleans Pelicans', idx=0
    ) is True


def test_polymarket_bipartite_event_pairing_acronyms_kickoff_and_period_scale(tmp_path):
    from betting_engine.polymarket_executor import PolymarketAutonomousExecutor
    from betting_engine.polymarket_runner import PolymarketAutonomousRunner

    runner = PolymarketAutonomousRunner(executor=PolymarketAutonomousExecutor(db_path=tmp_path / 'engine.sqlite3'))

    # 1. Shared 'Los Angeles' must NEVER match Clippers vs Lakers to 'Los Angeles Lakers vs. Phoenix Suns'
    nba_tips = [
        {
            'league': 'NBA', 'home': 'Los Angeles Clippers', 'away': 'Los Angeles Lakers',
            'start': '2026-11-25T03:00:00Z', 'model_scope': 'full_game', 'market': 'spreads',
            'predicted_home_points': 115.0, 'predicted_away_points': 110.0,
        },
        {
            'league': 'NBA', 'home': 'Phoenix Suns', 'away': 'Los Angeles Lakers',
            'start': '2026-11-26T03:00:00Z', 'model_scope': 'full_game', 'market': 'spreads',
            'predicted_home_points': 118.0, 'predicted_away_points': 111.0,
        },
    ]
    fc_nba = runner._lookup_game_forecast(
        league='NBA', teams=[], title='Los Angeles Lakers vs. Phoenix Suns',
        tips=nba_tips, event_start='2026-11-26T03:00:00Z',
    )
    assert fc_nba is not None
    assert fc_nba['home_name'] == 'Phoenix Suns'
    assert fc_nba['away_name'] == 'Los Angeles Lakers'

    # 2. 'TCU vs. Texas Tech' must match Texas Christian Horned Frogs, NOT Texas Longhorns vs Texas Tech
    ncaaf_tips = [
        {
            'league': 'NCAAF', 'home': 'Texas Longhorns', 'away': 'Texas Tech Red Raiders',
            'start': '2026-11-25T20:00:00Z', 'model_scope': 'full_game', 'market': 'spreads',
            'predicted_home_points': 34.0, 'predicted_away_points': 20.0,
        },
        {
            'league': 'NCAAF', 'home': 'Texas Christian Horned Frogs', 'away': 'Texas Tech Red Raiders',
            'start': '2026-11-25T20:00:00Z', 'model_scope': 'full_game', 'market': 'spreads',
            'predicted_home_points': 28.0, 'predicted_away_points': 31.0,
        },
    ]
    fc_cfb = runner._lookup_game_forecast(
        league='NCAAF', teams=[], title='TCU vs. Texas Tech',
        tips=ncaaf_tips, event_start='2026-11-25T20:00:00Z',
    )
    assert fc_cfb is not None
    assert fc_cfb['home_name'] == 'Texas Christian Horned Frogs'
    assert fc_cfb['proj_home'] == 28.0

    # 3. Consecutive-night series between identical teams selects exact kickoff window; period-only tip rescales to full_game
    series_tips = [
        {
            'league': 'NBA', 'home': 'Boston Celtics', 'away': 'Miami Heat',
            'start': '2026-11-24T00:30:00Z', 'model_scope': 'full_game', 'market': 'spreads',
            'predicted_home_points': 120.0, 'predicted_away_points': 102.0,
        },
        {
            'league': 'NBA', 'home': 'Boston Celtics', 'away': 'Miami Heat',
            'start': '2026-11-26T00:30:00Z', 'model_scope': 'quarter_1', 'market': 'quarter_1:spreads',
            'predicted_home_points': 24.5, 'predicted_away_points': 26.95,  # 0.245 * (100 vs 110)
        },
    ]
    fc_g2 = runner._lookup_game_forecast(
        league='NBA', teams=[], title='Celtics vs. Heat',
        tips=series_tips, event_start='2026-11-26T00:30:00Z',
    )
    assert fc_g2 is not None
    assert abs(fc_g2['proj_home'] - 100.0) < 0.2
    assert abs(fc_g2['proj_away'] - 110.0) < 0.2


def test_polymarket_soccer_3way_reg_home_prob_and_pillar2_settlement_side(tmp_path):
    import json
    from betting_engine.polymarket_executor import PolymarketAutonomousExecutor
    from betting_engine.polymarket_runner import PolymarketAutonomousRunner
    from betting_engine.polymarket_settlement import PolymarketOutcomeSettler

    db_file = tmp_path / 'engine.sqlite3'
    executor = PolymarketAutonomousExecutor(db_path=db_file)
    runner = PolymarketAutonomousRunner(executor=executor)

    # 1. Soccer 3-way forecast must preserve reg_home_prob (0.46), NOT inflate to 2-way DNB (0.46 + 0.5*0.26 = 0.59)!
    epl_tips = [
        {
            'league': 'EPL', 'home': 'Arsenal FC', 'away': 'Manchester United',
            'start': '2026-11-25T20:00:00Z', 'model_scope': 'full_game', 'market': 'three_way',
            'side': 'home', 'probability': 0.46,
        },
        {
            'league': 'EPL', 'home': 'Arsenal FC', 'away': 'Manchester United',
            'start': '2026-11-25T20:00:00Z', 'model_scope': 'full_game', 'market': 'three_way',
            'side': 'draw', 'probability': 0.26,
        },
    ]
    fc_epl = runner._lookup_game_forecast(
        league='EPL', teams=[], title='Arsenal vs. Manchester United',
        tips=epl_tips, event_start='2026-11-25T20:00:00Z',
    )
    assert fc_epl is not None
    assert fc_epl['reg_home_prob'] == 0.46
    assert abs(fc_epl['away_prob'] - 0.28) < 1e-6

    # 2. Pillar 2 fallback settlement must not invert away side on shared 'State' ('Long Beach State' vs 'Arkansas State')
    with sqlite3.connect(db_file) as c:
        c.execute("""
            INSERT INTO polymarket_orders (
                order_id, client_order_id, token_id, league, game_title, start_time,
                market_family, selection, side, line, poly_price, poly_odds, model_prob,
                expected_value, stake_usd, shares_count, status, created_at, updated_at, execution_mode, scope
            ) VALUES (
                'ord_state_1', 'cid_state_1', 'tok_state_1', 'CBB',
                'Long Beach State Beach vs. Arkansas State Red Wolves', '2026-11-23T20:00:00Z',
                'spreads', 'Arkansas State Red Wolves', 'BUY', -5.5, 0.50, 2.0, 0.55,
                0.10, 2.0, 4.0, 'filled', '2026-11-23T18:00:00Z', '2026-11-23T18:00:00Z', 'shadow', 'full_game'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bet_outcomes (
                bet_id INTEGER PRIMARY KEY, league TEXT, market TEXT, model_version TEXT,
                result TEXT, probability REAL, push_probability REAL, brier REAL, log_loss REAL, evidence TEXT
            )
        """)
        # Home = Long Beach State (80), Away = Arkansas State (70) -> Arkansas State -5.5 LOST!
        # (Previously shared 'State' matched home_name -> evaluated Long Beach State 80-70-5.5 > 0 as a false WIN!)
        ev_json = json.dumps({
            'game_title': 'Long Beach State Beach vs. Arkansas State Red Wolves',
            'score_scope': 'full_game',
            'home_team': 'Long Beach State Beach',
            'away_team': 'Arkansas State Red Wolves',
            'home_score': 80,
            'away_score': 70,
        })
        c.execute("INSERT INTO bet_outcomes (bet_id, league, market, result, evidence) VALUES (1, 'NCAAM', 'spreads', 'lost', ?)", (ev_json,))

    settler = PolymarketOutcomeSettler(db_path=db_file, log_path=tmp_path / 'poly_settle.jsonl')
    # Mock gamma_market_resolution to None so Pillar 2 official_match_scores executes
    settler.fetch_gamma_market_resolution = lambda tid: None
    settler.fetch_polymarket_positions = lambda: {}
    rep = settler.settle_orders()
    assert rep['settled_count'] == 1
    assert rep['lost_count'] == 1
    assert rep['won_count'] == 0


def test_resolve_polymarket_outcome_strict_fail_closed_refuses_guessing():
    from betting_engine.polymarket_runner import resolve_polymarket_outcome_is_home

    # Recognizes Sixers <-> Philadelphia 76ers positively
    assert resolve_polymarket_outcome_is_home(
        'Sixers', 'Spread: Sixers (-3.5)',
        'Philadelphia 76ers', 'Miami Heat', idx=0, strict=True
    ) is True

    # Strictly returns None (fail-closed -> skip bet, zero guessing!) when an unrecognized label matches neither team
    assert resolve_polymarket_outcome_is_home(
        'Mystery Club', 'Spread: Mystery Club (-3.5)',
        'Philadelphia 76ers', 'Miami Heat', idx=0, strict=True
    ) is None
