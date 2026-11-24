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
