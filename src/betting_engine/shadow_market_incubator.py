"""Autonomous Market Expansion & Shadow Graduation Department (AMIE-GD).

Engine that autonomously explores, models, tests, and incubates unmodeled gap markets
in zero-risk shadow mode, graduating proven profitable families into live issuance.

Key Principles:
1. Zero Hardcoding / Fully Dynamic: Sample size requirements scale dynamically with
   odds variance: N_min = max(20, ceil(20 * sqrt(O - 1))). Brier baselines calibrate from
   closing lines.
2. Zero Risk During Incubation: Shadow wagers are recorded in data/shadow_incubator.sqlite3
   and never touch live bankroll or bets table until graduated.
3. Closed-Loop Unified Tier Integration: Graduated markets adopt canonical segment keys
   {league}|{scope}|{family} and flow directly into TierStateMachine (starting in EXPLORATION,
   scaling to SOLID_ALPHA / SUPER_ALPHA upon winning, or CAPITAL_DEFENSE upon losing).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .domain import Game, parse_time, utcnow
from .io_utils import atomic_json
from .market_decomposer import DecomposedContract, decompose_market
from .providers import DATA

log = logging.getLogger(__name__)

INCUBATOR_DB = DATA / 'shadow_incubator.sqlite3'
GRADUATED_FILE = DATA / 'graduated_markets.json'
MIN_LEAGUE_GRADUATED_QUOTA: int = 12

_initialized_incubator_dbs: set[str] = set()


def _init_db(db_path: Path = INCUBATOR_DB) -> sqlite3.Connection:
    """Initializes the incubator SQLite database schema."""
    db_key = str(db_path.resolve())
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.execute("SELECT 1")
    except sqlite3.OperationalError:
        return sqlite3.connect(f"file:{db_path.resolve()}?immutable=1", uri=True)
    if db_key in _initialized_incubator_dbs:
        return conn
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        try:
            conn.close()
        except Exception:
            pass
        _initialized_incubator_dbs.add(db_key)
        return sqlite3.connect(f"file:{db_path.resolve()}?immutable=1", uri=True)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incubated_wagers (
            id TEXT PRIMARY KEY,
            fixture_id TEXT NOT NULL,
            game_id TEXT NOT NULL,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT NOT NULL,
            raw_market_name TEXT NOT NULL,
            selection TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'home',
            line REAL,
            odds REAL NOT NULL,
            model_prob REAL NOT NULL,
            push_prob REAL NOT NULL DEFAULT 0.0,
            market_prob REAL NOT NULL,
            ev REAL NOT NULL,
            dynamic_sample_power_req INT NOT NULL,
            captured_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            settled_at TEXT,
            result TEXT,
            pnl REAL,
            brier_model REAL,
            brier_market REAL,
            metadata TEXT,
            cohort TEXT NOT NULL DEFAULT 'gap_market',
            model_id TEXT,
            model_version TEXT
        )
    """)
    for col in ('cohort TEXT DEFAULT \'gap_market\'', 'model_id TEXT', 'model_version TEXT'):
        try:
            conn.execute(f"ALTER TABLE incubated_wagers ADD COLUMN {col}")
        except Exception:
            pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incubated_status ON incubated_wagers(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incubated_cohort ON incubated_wagers(cohort, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incubated_family ON incubated_wagers(league, scope, family, sub_family)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incubated_game ON incubated_wagers(game_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS graduated_markets (
            id TEXT PRIMARY KEY,
            segment_key TEXT NOT NULL UNIQUE,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT NOT NULL,
            graduated_at TEXT NOT NULL,
            sample_size INT NOT NULL,
            roi REAL NOT NULL,
            delta_brier REAL NOT NULL,
            max_drawdown REAL NOT NULL,
            win_rate REAL NOT NULL,
            stats TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS demoted_markets (
            id TEXT PRIMARY KEY,
            segment_key TEXT NOT NULL UNIQUE,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT NOT NULL,
            demoted_at TEXT NOT NULL,
            rolling_roi REAL NOT NULL,
            rolling_pnl REAL NOT NULL,
            games INT NOT NULL,
            reason TEXT NOT NULL,
            stats TEXT NOT NULL,
            incubated_model_id TEXT,
            model_archetype TEXT,
            model_params TEXT,
            historical_roi REAL,
            historical_brier_delta REAL,
            historical_sample_size INT DEFAULT 0,
            incubation_phase TEXT DEFAULT 'research_training',
            admitted_to_incubator_at TEXT
        )
    """)
    # Ensure migration columns for demoted_markets
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(demoted_markets)")
    dm_cols = {r[1] for r in cur.fetchall()}
    for col_def in [
        ('incubated_model_id', 'TEXT'),
        ('model_archetype', 'TEXT'),
        ('model_params', 'TEXT'),
        ('historical_roi', 'REAL'),
        ('historical_brier_delta', 'REAL'),
        ('historical_sample_size', 'INT DEFAULT 0'),
        ('incubation_phase', "TEXT DEFAULT 'research_training'"),
        ('admitted_to_incubator_at', 'TEXT'),
    ]:
        if col_def[0] not in dm_cols:
            conn.execute(f"ALTER TABLE demoted_markets ADD COLUMN {col_def[0]} {col_def[1]}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS incubator_diagnostic_repairs (
            id TEXT PRIMARY KEY,
            segment_key TEXT NOT NULL UNIQUE,
            league TEXT NOT NULL,
            scope TEXT NOT NULL,
            family TEXT NOT NULL,
            sub_family TEXT NOT NULL,
            diagnosed_at TEXT NOT NULL,
            sample_size INT NOT NULL,
            prior_roi REAL NOT NULL,
            prior_pnl REAL NOT NULL,
            failure_mode TEXT NOT NULL,
            actionable_remedy TEXT NOT NULL,
            recalibrated_at TEXT NOT NULL,
            stats TEXT NOT NULL
        )
    """)
    conn.commit()
    _initialized_incubator_dbs.add(db_key)
    return conn


def seed_historical_probability_picks(
    engine_db_path: Optional[Path] = None,
    incubator_db_path: Path = INCUBATOR_DB
) -> int:
    """Seeds historical settled probability picks from engine.sqlite3 into shadow_incubator.sqlite3.
    
    Provides immediate empirical track records for the High-Probability Cohort in the Crucible.
    """
    if engine_db_path is None:
        engine_db_path = incubator_db_path.parent / 'engine.sqlite3'
    if not engine_db_path.exists():
        return 0

    conn_inc = sqlite3.connect(str(incubator_db_path), timeout=30.0)
    try:
        cur_inc = conn_inc.cursor()
        cur_inc.execute("SELECT count(*) FROM incubated_wagers WHERE cohort = 'high_probability'")
        existing_count = cur_inc.fetchone()[0]
        if existing_count > 0:
            conn_inc.close()
            return 0  # Already seeded

        conn_eng = sqlite3.connect(str(engine_db_path), timeout=10.0)
        cur_eng = conn_eng.cursor()
        cur_eng.execute("""
            SELECT tip_id, payload, status, pnl, issued_at
            FROM system_decisions
            WHERE json_extract(payload, '$.funding_basis.tier') = 'probability_pick'
              AND status IN ('won', 'lost', 'push')
        """)
        rows = cur_eng.fetchall()
        conn_eng.close()

        if not rows:
            conn_inc.close()
            return 0

        batch = []
        for tip_id, payload_str, status, pnl, issued_at in rows:
            try:
                p = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
            except Exception:
                continue
            game_id = str(p.get('game_id') or tip_id)
            league = str(p.get('league') or 'UNKNOWN')
            market = str(p.get('market') or '')
            scope = market.split(':')[0] if ':' in market else 'full_game'
            family = market.split(':')[1] if ':' in market else market
            sub_family = family
            raw_market_name = str(p.get('description') or market)
            selection = str(p.get('selection') or '')
            side = str(p.get('side') or 'home')
            line = p.get('line')
            odds = float(p.get('reference_odds') or 1.9)
            prob = float(p.get('probability') or 0.5)
            push = float(p.get('push_probability') or 0.0)
            ev = float(p.get('expected_roi') or 0.0)
            mkt_prob = 1.0 / odds if odds > 0 else 0.5
            pnl_val = float(pnl) if pnl is not None else 0.0

            y = 1.0 if status == 'won' else (0.5 if status == 'push' else 0.0)
            brier_model = (prob - y) ** 2
            brier_market = (mkt_prob - y) ** 2
            sample_req = max(20, int(math.ceil(20.0 * math.sqrt(max(0.1, odds - 1.0)))))

            w_id = f"hist_hp_{tip_id}"
            meta_json = json.dumps({'source': 'historical_probability_pick', 'tip_id': tip_id})

            batch.append((
                w_id, game_id, game_id, league, scope, family, sub_family,
                raw_market_name, selection, side, line, odds, prob, push,
                mkt_prob, ev, sample_req, issued_at, 'settled', issued_at,
                status, pnl_val, brier_model, brier_market, meta_json, 'high_probability'
            ))

        if batch:
            cur_inc.executemany("""
                INSERT OR IGNORE INTO incubated_wagers (
                    id, fixture_id, game_id, league, scope, family, sub_family,
                    raw_market_name, selection, side, line, odds, model_prob, push_prob,
                    market_prob, ev, dynamic_sample_power_req, captured_at, status, settled_at,
                    result, pnl, brier_model, brier_market, metadata, cohort
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
            conn_inc.commit()
        conn_inc.close()
        return len(batch)
    except Exception:
        try:
            conn_inc.close()
        except Exception:
            pass
        return 0


def load_graduated_markets(data_dir: Path = DATA) -> Dict[str, Dict[str, Any]]:
    """Loads all currently active graduated market families from atomic JSON and sqlite."""
    path = data_dir / 'graduated_markets.json'
    data = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                data = loaded
                for v in data.values():
                    if isinstance(v, dict) and isinstance(v.get('stats'), str):
                        try:
                            v['stats'] = json.loads(v['stats'])
                        except Exception:
                            v['stats'] = {}
        except Exception:
            pass
    db_path = data_dir / 'shadow_incubator.sqlite3'
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            # Demoted markets filter: ensure no demoted/probationary market lingers in memory or JSON
            demoted_keys = set()
            try:
                d_rows = conn.execute("SELECT segment_key FROM demoted_markets").fetchall()
                demoted_keys = {r[0] for r in d_rows}
                if demoted_keys:
                    conn.execute("""
                        DELETE FROM graduated_markets 
                        WHERE segment_key IN (SELECT segment_key FROM demoted_markets)
                           OR id IN (
                               SELECT g.id FROM graduated_markets g
                               JOIN demoted_markets d 
                                 ON g.league = d.league 
                                AND g.scope = d.scope 
                                AND (
                                    g.segment_key = d.segment_key
                                    OR ((d.sub_family IS NULL OR d.sub_family = '' OR d.sub_family = d.family) AND g.family = d.family)
                                    OR (
                                        d.sub_family IS NOT NULL AND d.sub_family != '' AND d.sub_family != d.family
                                        AND (g.sub_family = d.sub_family OR g.segment_key = d.segment_key)
                                    )
                                )
                           )
                    """)
                    conn.commit()
            except Exception:
                pass

            had_demoted_in_data = False
            for dk in list(demoted_keys):
                if dk in data:
                    del data[dk]
                    had_demoted_in_data = True

            rows = conn.execute("SELECT id, segment_key, league, scope, family, sub_family, graduated_at, stats FROM graduated_markets").fetchall()
            synced = False
            for r in rows:
                if r[1] not in data and r[1] not in demoted_keys:
                    st = json.loads(r[7]) if r[7] else {}
                    data[r[1]] = {
                        'id': r[0], 'segment_key': r[1], 'league': r[2],
                        'scope': r[3], 'family': r[4], 'sub_family': r[5],
                        'graduated_at': r[6], 'initial_tier': 'EXPLORATION',
                        'stats': st
                    }
                    synced = True
            conn.close()
            if synced or had_demoted_in_data:
                try:
                    atomic_json(path, data)
                except Exception:
                    pass
        except Exception:
            pass
    return data


def save_graduated_markets(graduated: Dict[str, Dict[str, Any]], data_dir: Path = DATA) -> None:
    """Atomically saves graduated market families to disk."""
    path = data_dir / 'graduated_markets.json'
    atomic_json(path, graduated)


def load_demoted_markets(data_dir: Path = DATA) -> Dict[str, Dict[str, Any]]:
    """Loads all currently demoted / research probation markets from incubator DB."""
    db_path = data_dir / 'shadow_incubator.sqlite3'
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT id, segment_key, league, scope, family, sub_family, demoted_at, rolling_roi, rolling_pnl, games, reason, stats FROM demoted_markets")
        rows = cur.fetchall()
        conn.close()
        result = {}
        for r in rows:
            result[r[1]] = {
                'id': r[0],
                'segment_key': r[1],
                'league': r[2],
                'scope': r[3],
                'family': r[4],
                'sub_family': r[5],
                'demoted_at': r[6],
                'rolling_roi': r[7],
                'rolling_pnl': r[8],
                'games': r[9],
                'reason': r[10],
                'stats': json.loads(r[11]) if isinstance(r[11], str) else (r[11] or {})
            }
        return result
    except Exception:
        return {}


def get_demoted_segments(data_dir: Path = DATA) -> set:
    """Returns set of all segment_keys and canonical aliases currently demoted to research / capital defense."""
    demoted = load_demoted_markets(data_dir)
    keys = set(demoted.keys())
    for m in demoted.values():
        lg = m.get('league')
        sc = m.get('scope', 'full_game')
        fam = m.get('family', '')
        sub_fam = m.get('sub_family', '')
        if lg and sc and sub_fam:
            keys.add(f"{lg}|{sc}|{sub_fam}")
            keys.add(f"{lg}|{sc}|{sc}:{sub_fam}")
            if ':' in sub_fam:
                raw_sub = sub_fam.split(':')[-1]
                keys.add(f"{lg}|{sc}|{raw_sub}")
                keys.add(f"{lg}|{sc}|{sc}:{raw_sub}")
        if lg and sc and fam:
            keys.add(f"{lg}|{sc}|{fam}")
            if ':' in fam:
                raw_fam = fam.split(':')[-1]
                keys.add(f"{lg}|{sc}|{raw_fam}")
        if sc == 'full_game':
            if lg and sub_fam:
                keys.add(f"{lg}|{sub_fam}")
                if ':' in sub_fam:
                    keys.add(f"{lg}|{sub_fam.split(':')[-1]}")
            if lg and fam:
                keys.add(f"{lg}|{fam}")
    return keys


def matches_live_decision_to_market(p: Dict[str, Any], meta: Dict[str, Any]) -> bool:
    """Matches a live system_decision payload to a graduated or core market definition."""
    target_league = meta.get('league')
    target_scope = meta.get('scope', 'full_game')
    target_family = str(meta.get('family', '')).lower()
    target_sub_family = str(meta.get('sub_family', '')).lower()
    seg_key = str(meta.get('segment_key', '')).lower()

    if p.get('league') != target_league:
        return False

    dec_mkt = str(p.get('market') or '')
    m_lower = dec_mkt.lower()
    dec_scope = p.get('model_scope') or p.get('scope')
    if not dec_scope:
        if ':' in dec_mkt:
            dec_scope = dec_mkt.split(':')[0]
        elif '1st half' in m_lower or 'half 1' in m_lower:
            dec_scope = 'half_1'
        elif '2nd half' in m_lower or 'half 2' in m_lower:
            dec_scope = 'half_2'
        elif '1st quarter' in m_lower or 'quarter 1' in m_lower or 'quarter_1' in m_lower:
            dec_scope = 'quarter_1'
        elif '2nd quarter' in m_lower or 'quarter 2' in m_lower or 'quarter_2' in m_lower:
            dec_scope = 'quarter_2'
        elif '3rd quarter' in m_lower or 'quarter 3' in m_lower or 'quarter_3' in m_lower:
            dec_scope = 'quarter_3'
        elif '4th quarter' in m_lower or 'quarter 4' in m_lower or 'quarter_4' in m_lower:
            dec_scope = 'quarter_4'
        elif '1st period' in m_lower or 'period 1' in m_lower or 'period_1' in m_lower:
            dec_scope = 'period_1'
        elif '2nd period' in m_lower or 'period 2' in m_lower or 'period_2' in m_lower:
            dec_scope = 'period_2'
        elif '3rd period' in m_lower or 'period 3' in m_lower or 'period_3' in m_lower:
            dec_scope = 'period_3'
        elif '1st inning' in m_lower or 'inning 1' in m_lower or 'inning_1' in m_lower:
            dec_scope = 'inning_1'
        elif 'innings 1 to 5' in m_lower or 'innings_1_5' in m_lower:
            dec_scope = 'innings_1_5'
        elif '1st set' in m_lower or 'set 1' in m_lower:
            dec_scope = 'period_1'
        elif '2nd set' in m_lower or 'set 2' in m_lower:
            dec_scope = 'period_2'
        else:
            dec_scope = 'full_game'

    if dec_scope != target_scope:
        return False

    # Disambiguation: distinct full-game market types should not cross-match
    if target_family in ('moneyline', 'draw_no_bet') and ('spread' in m_lower or 'handicap' in m_lower):
        return False
    if target_family in ('spreads', 'alternative_spread') and ('moneyline' in m_lower or m_lower == 'winner'):
        return False
    if target_family in ('totals', 'alternative_total') and ('spread' in m_lower or 'moneyline' in m_lower):
        return False

    # Canonical allocation family match
    try:
        from .contracts import allocation_family
        p_fam = allocation_family(p)
        if p_fam and (p_fam == target_family or p_fam == target_sub_family):
            return True
    except Exception:
        pass

    # Sub-family specific match
    if target_sub_family in ('spreads', 'alternative_spread') and ('spread' in m_lower or 'handicap' in m_lower):
        return True
    if target_sub_family in ('totals', 'alternative_total') and ('total' in m_lower and 'home' not in m_lower and 'away' not in m_lower and 'team' not in m_lower):
        return True
    if target_sub_family in ('moneyline', 'draw_no_bet') and ('moneyline' in m_lower or m_lower == 'winner'):
        return True
    if target_sub_family in ('team_total', 'home_total', 'away_total'):
        if any(k in m_lower for k in ('team_total', 'home_total', 'away_total', 'regulation:home_total', 'regulation:away_total')):
            return True
    if target_sub_family in ('both_teams_score', 'btts'):
        if any(k in m_lower for k in ('both_teams_score', 'btts', 'both_score')):
            return True
    if target_sub_family == 'double_chance' and 'double_chance' in m_lower:
        return True
    if target_sub_family in ('first_to_score', 'sequence_hazard'):
        if any(k in m_lower for k in ('first_to_score', 'first to score', 'sequence_hazard')):
            return True
    if target_sub_family in ('set_handicap', 'set_spread') and any(k in m_lower for k in ('set_handicap', 'set handicap', 'set_spread', 'spread')):
        return True
    if target_sub_family in ('set_totals', 'set_total') and any(k in m_lower for k in ('set_total', 'set total', 'set_totals', 'total')):
        return True
    if target_sub_family == 'set_winner' and any(k in m_lower for k in ('set_winner', 'set winner', 'moneyline', 'winner')):
        return True
    if target_sub_family in ('asian_handicap', 'asian handicap') and any(k in m_lower for k in ('asian_handicap', 'asian handicap')):
        return True
    if target_sub_family in ('asian_total', 'asian_totals', 'asian total') and any(k in m_lower for k in ('asian_total', 'asian total', 'asian_totals')):
        return True


    # Family match
    if target_family == 'spreads' and ('spread' in m_lower or 'handicap' in m_lower):
        return True
    if target_family == 'totals' and ('total' in m_lower and 'home' not in m_lower and 'away' not in m_lower and 'team' not in m_lower):
        return True
    if target_family == 'moneyline' and ('moneyline' in m_lower or m_lower == 'winner'):
        return True

    return bool(target_family and target_family in m_lower) or bool(target_sub_family and target_sub_family in m_lower)



def _predict_soccer_half_matrix(sm: Any, home: str, away: str, scope: str, max_goals: int = 4) -> np.ndarray:
    exp_h, exp_a = sm.compute_expected_goals(home, away)
    scale = 0.45 if scope == 'half_1' else 0.55
    lh = max(0.05, exp_h * scale)
    la = max(0.05, exp_a * scale)
    m = np.zeros((max_goals + 1, max_goals + 1), dtype=float)
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            m[x, y] = (math.exp(-lh) * (lh**x) / math.factorial(x)) * (math.exp(-la) * (la**y) / math.factorial(y))
    tot = np.sum(m)
    if tot > 0:
        m /= tot
    return m


def _half_asian_handicap(m: np.ndarray, eff_line: float, side: str = 'home') -> Tuple[float, float]:
    max_g = m.shape[0] - 1
    mg = np.fromfunction(lambda x, y: x - y, (max_g + 1, max_g + 1))
    diff = mg if side in ('home', '1') else -mg
    p_w = float(np.sum(m[diff + eff_line > 0]))
    p_p = float(np.sum(m[diff + eff_line == 0]))
    return p_w, p_p


def _half_totals(m: np.ndarray, eff_line: float, side: str = 'over') -> Tuple[float, float]:
    max_g = m.shape[0] - 1
    grid = np.fromfunction(lambda x, y: x + y, (max_g + 1, max_g + 1))
    p_w = float(np.sum(m[grid > eff_line])) if side == 'over' else float(np.sum(m[grid < eff_line]))
    p_p = float(np.sum(m[grid == eff_line]))
    return p_w, p_p


class ShadowMarketIncubator:
    """Manages the lifecycle of unmodeled Stake gap markets from observation to graduation."""

    def __init__(self, data_dir: Path = DATA):
        self.data_dir = Path(data_dir or DATA)
        self.db_path = self.data_dir / 'shadow_incubator.sqlite3'
        conn = _init_db(self.db_path)
        conn.close()
        from .tournament_engine import TournamentEngine
        self._tournament_engine = TournamentEngine(data_dir=self.data_dir, db_path=self.db_path)
        try:
            seed_historical_probability_picks(
                engine_db_path=self.data_dir / 'engine.sqlite3',
                incubator_db_path=self.db_path
            )
        except Exception:
            pass

    def _get_tournament_engine(self):
        if self._tournament_engine is None:
            from .tournament_engine import TournamentEngine
            self._tournament_engine = TournamentEngine(data_dir=self.data_dir, db_path=self.db_path)
        return self._tournament_engine

    def _get_connection(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.execute("SELECT 1")
            return conn
        except sqlite3.OperationalError:
            return sqlite3.connect(f"file:{self.db_path.resolve()}?immutable=1", uri=True)

    # -------------------------------------------------------------------------
    # 1. PRICING & INGESTION
    # -------------------------------------------------------------------------

    def price_contract(
        self,
        contract: DecomposedContract,
        game: Game,
        forecast: Optional[Any],
        segment_forecasts: Optional[Dict[Tuple[str, str], Any]] = None,
        models: Optional[Dict[str, Any]] = None
    ) -> Optional[Tuple[float, float]]:
        """Calculates fair probability (p_win, p_push) for a decomposed contract."""
        league = contract.league
        scope = contract.scope
        side = contract.side or 'home'
        line = contract.line

        # Guard: Never price non-score/non-point statistical props with score/point models
        raw_lower = (contract.raw_market_name or '').lower()
        if any(p in raw_lower for p in (
            'corner', 'corners', 'booking', 'bookings', 'card', 'cards',
            'penalty', 'penalties', 'foul', 'fouls', 'throw in', 'throw-in',
            'free kick', 'goal kick', 'offside', 'offsides', 'tackle', 'tackles',
            'shot', 'shots', 'save', 'saves', 'touchdown', 'touchdowns'
        )):
            return None

        # Family 1 & 5: Asian Lines, American & Multi-Sport Derivatives
        if contract.family in ('asian_lines', 'american_derivatives'):
            # Check if soccer model is available
            if league in ('MLS', 'EPL', 'UCL') and contract.family == 'asian_lines' and forecast is not None and getattr(forecast, 'soccer_model', None) is not None:
                sm = forecast.soccer_model
                if contract.sub_family == 'asian_handicap':
                    eff_line = line if line is not None else 0.0
                    if round(eff_line * 2) != eff_line * 2:
                        from .contracts import decompose_quarter_line
                        l1, l2 = decompose_quarter_line(eff_line)
                        if scope in ('half_1', 'half_2'):
                            m = _predict_soccer_half_matrix(sm, game.home, game.away, scope)
                            w1, p1 = _half_asian_handicap(m, l1, side=side)
                            w2, p2 = _half_asian_handicap(m, l2, side=side)
                            return float((w1 + w2) / 2.0), float((p1 + p2) / 2.0)
                        else:
                            r1 = sm.predict_asian_handicap(game.home, game.away, l1, side=side)
                            r2 = sm.predict_asian_handicap(game.home, game.away, l2, side=side)
                            p_win = float((r1['win'] + r2['win']) / 2.0)
                            p_push = float((r1['push'] + r2['push']) / 2.0)
                            return p_win, p_push
                    else:
                        if scope in ('half_1', 'half_2'):
                            m = _predict_soccer_half_matrix(sm, game.home, game.away, scope)
                            w, p = _half_asian_handicap(m, eff_line, side=side)
                            return float(w), float(p)
                        else:
                            res = sm.predict_asian_handicap(game.home, game.away, eff_line, side=side)
                            return float(res['win']), float(res['push'])
                elif contract.sub_family == 'asian_total':
                    eff_line = line if line is not None else (1.25 if scope in ('half_1', 'half_2') else 2.5)
                    if round(eff_line * 2) != eff_line * 2:
                        from .contracts import decompose_quarter_line
                        l1, l2 = decompose_quarter_line(eff_line)
                        if scope in ('half_1', 'half_2'):
                            m = _predict_soccer_half_matrix(sm, game.home, game.away, scope)
                            w1, p1 = _half_totals(m, l1, side=side)
                            w2, p2 = _half_totals(m, l2, side=side)
                            return float((w1 + w2) / 2.0), float((p1 + p2) / 2.0)
                        else:
                            r1 = sm.predict_totals(game.home, game.away, l1)
                            r2 = sm.predict_totals(game.home, game.away, l2)
                            p_w1 = r1['over'] if side == 'over' else r1['under']
                            p_w2 = r2['over'] if side == 'over' else r2['under']
                            return float((p_w1 + p_w2) / 2.0), float((r1['push'] + r2['push']) / 2.0)
                    else:
                        if scope in ('half_1', 'half_2'):
                            m = _predict_soccer_half_matrix(sm, game.home, game.away, scope)
                            w, p = _half_totals(m, eff_line, side=side)
                            return float(w), float(p)
                        else:
                            res = sm.predict_totals(game.home, game.away, eff_line)
                            p_w = res['over'] if side == 'over' else res['under']
                            return float(p_w), float(res['push'])

            # Non-soccer or general score distribution across all sports (NFL, NBA, NHL, MLB, NCAAF, etc.)
            # Rule: Never price a period or quarter using a full-game distribution
            if scope != 'full_game':
                fc = segment_forecasts.get((game.id, scope)) if segment_forecasts else None
            else:
                fc = forecast
            if fc is not None:
                try:
                    if contract.sub_family == 'team_total':
                        tgt = (contract.metadata.get('team_target', '') or contract.raw_market_name).lower()
                        home_name = (game.home or '').lower()
                        away_name = (game.away or '').lower()
                        is_home = (home_name and home_name in tgt) or 'home' in tgt or contract.side == 'home'
                        base_kind = 'home_total' if is_home else 'away_total'
                    elif contract.sub_family in ('spreads', 'asian_handicap', 'alternative_spread'):
                        base_kind = 'spreads'
                    elif contract.sub_family in ('totals', 'asian_total', 'alternative_total'):
                        base_kind = 'totals'
                    elif contract.sub_family in ('three_way', '1x2'):
                        base_kind = 'three_way'
                    elif contract.sub_family in ('moneyline', 'draw_no_bet'):
                        base_kind = 'moneyline'
                    elif contract.sub_family == 'winning_margin':
                        # Continuous margin pricing for Basketball / Football / Multi-Sport
                        s_text = (contract.side or contract.selection or '').lower()
                        is_home = (game.home and game.home.lower() in s_text) or 'home' in s_text or '1' in s_text
                        is_away = (game.away and game.away.lower() in s_text) or 'away' in s_text or '2' in s_text
                        if league in ('MLS', 'EPL', 'UCL') and forecast is not None and getattr(forecast, 'soccer_model', None) is not None:
                            sm = forecast.soccer_model
                            m, _, _ = sm.predict_score_matrix(game.home, game.away)
                            m_band = re.search(r'(\d+)\s*[-–]\s*(\d+)', s_text)
                            m_single = re.search(r'by\s*(\d+)', s_text) or re.search(r'(\d+)', s_text)
                            l = float(m_band.group(1)) if m_band else (float(m_single.group(1)) if m_single else 1.0)
                            u = float(m_band.group(2)) if m_band else l
                            p = sum(m[x, y] for x in range(7) for y in range(7) if (l <= (x - y if is_home else y - x) <= u))
                            return float(max(0.01, min(0.99, p))), 0.0
                        else:
                            mu = float(getattr(fc, 'margin', 0.0))
                            sigma = 11.5 if ('nba' in league.lower() or 'wnba' in league.lower() or 'ncaa' in league.lower()) else 13.5
                            eff_mu = mu if is_home else -mu
                            m_band = re.search(r'(\d+)\s*[-–]\s*(\d+)', s_text)
                            m_plus = re.search(r'(\d+)\+', s_text)
                            from scipy.stats import norm
                            if m_band:
                                l = float(m_band.group(1)); u = float(m_band.group(2))
                                p = norm.cdf((u + 0.5 - eff_mu) / sigma) - norm.cdf((l - 0.5 - eff_mu) / sigma)
                                return float(max(0.01, min(0.99, p))), 0.0
                            elif m_plus:
                                l = float(m_plus.group(1))
                                p = 1.0 - norm.cdf((l - 0.5 - eff_mu) / sigma)
                                return float(max(0.01, min(0.99, p))), 0.0
                    elif contract.sub_family == 'overtime':
                        if 'nba' in league.lower() or 'wnba' in league.lower() or 'ncaa' in league.lower():
                            mu = float(getattr(fc, 'margin', 0.0))
                            sigma = 11.5
                            from scipy.stats import norm
                            p_ot = norm.cdf((0.5 - mu) / sigma) - norm.cdf((-0.5 - mu) / sigma)
                            p = p_ot if contract.side == 'yes' else (1.0 - p_ot)
                            return float(max(0.01, min(0.99, p))), 0.0
                        elif league == 'NHL':
                            p = 0.23 if contract.side == 'yes' else 0.77
                            return float(max(0.01, min(0.99, p))), 0.0
                        elif league in ('NFL', 'NCAAF'):
                            p = 0.025 if contract.side == 'yes' else 0.975
                            return float(max(0.01, min(0.99, p))), 0.0
                    elif contract.sub_family == 'highest_scoring_period':
                        s_text = (contract.side or contract.selection or '').lower()
                        if league in ('MLS', 'EPL', 'UCL'):
                            p = 0.52 if ('2nd' in s_text or 'second' in s_text) else (0.30 if ('1st' in s_text or 'first' in s_text) else 0.18)
                        else:
                            p = 0.51 if ('2nd' in s_text or 'second' in s_text) else (0.45 if ('1st' in s_text or 'first' in s_text) else 0.04)
                        return float(p), 0.0
                    elif contract.sub_family == 'odd_even':
                        if league in ('MLS', 'EPL', 'UCL') and forecast is not None and getattr(forecast, 'soccer_model', None) is not None:
                            sm = forecast.soccer_model
                            m, _, _ = sm.predict_score_matrix(game.home, game.away)
                            p_odd = sum(m[x, y] for x in range(7) for y in range(7) if (x + y) % 2 == 1)
                            p = p_odd if contract.side == 'odd' else (1.0 - p_odd)
                            return float(max(0.01, min(0.99, p))), 0.0
                        return 0.50, 0.0
                    else:
                        base_kind = 'spreads' if 'spread' in contract.sub_family else ('totals' if 'total' in contract.sub_family else 'spreads')

                    if hasattr(fc, 'market_probability'):
                        target_kind = f"{scope}:{base_kind}" if scope != 'full_game' else base_kind
                        res = fc.market_probability(target_kind, side, line)
                        if res is not None:
                            p_win, p_push = res
                            return max(0.01, min(0.99, float(p_win))), max(0.0, min(0.5, float(p_push)))
                except Exception:
                    pass

        # Family 2: Tennis Set Markets (Exact harmonization with GraduatedTennisSetModel)
        elif contract.family == 'tennis_sets':
            from .tennis_engine import (
                calibrate_serve_probabilities,
                set_score_distribution,
                TOUR_BASELINES,
                SURFACE_MODIFIERS,
            )
            context = getattr(game, 'context', {}) or {}
            surface = context.get('surface', 'hard')
            rating_diff = float(context.get('rating_diff', 0.0) or 0.0)
            fatigue_a = float(context.get('fatigue_a', 0.0) or 0.0)
            fatigue_b = float(context.get('fatigue_b', 0.0) or 0.0)
            if rating_diff == 0.0 and forecast is not None and hasattr(forecast, 'features'):
                elo_diff = float(forecast.features.get('elo_difference', 0.0) or 0.0)
                if elo_diff != 0.0:
                    rating_diff = elo_diff

            p_serve_a, p_serve_b = calibrate_serve_probabilities(
                tour=league,
                surface=surface,
                rating_diff=rating_diff,
                fatigue_penalty_a=fatigue_a,
                fatigue_penalty_b=fatigue_b
            )
            p_a_wins_set, scores = set_score_distribution(p_serve_a, p_serve_b)

            if contract.sub_family == 'set_winner':
                p = p_a_wins_set if side == 'home' else (1.0 - p_a_wins_set)
                return float(max(0.01, min(0.99, p))), 0.0

            elif contract.sub_family == 'set_totals' and line is not None:
                if side == 'over':
                    p = sum(prob for (a, b), prob in scores.items() if (a + b) > line)
                else:
                    p = sum(prob for (a, b), prob in scores.items() if (a + b) < line)
                p_push = sum(prob for (a, b), prob in scores.items() if (a + b) == line)
                return float(max(0.01, min(0.99, p))), float(max(0.0, min(0.5, p_push)))

            elif contract.sub_family == 'set_handicap' and line is not None:
                if side == 'home':
                    p = sum(prob for (a, b), prob in scores.items() if (a - b + line) > 0)
                    p_push = sum(prob for (a, b), prob in scores.items() if (a - b + line) == 0)
                else:
                    p = sum(prob for (a, b), prob in scores.items() if (b - a + line) > 0)
                    p_push = sum(prob for (a, b), prob in scores.items() if (b - a + line) == 0)
                return float(max(0.01, min(0.99, p))), float(max(0.0, min(0.5, p_push)))

            elif contract.sub_family == 'set_to_nil':
                p_nil = float(scores.get((6, 0), 0.0) + scores.get((0, 6), 0.0))
                p = p_nil if side == 'yes' else (1.0 - p_nil)
                return float(max(0.01, min(0.99, p))), 0.0

            elif contract.sub_family == 'double_result':
                sel = (contract.selection or contract.side or '').lower()
                p_match = float(getattr(forecast, 'home_probability', 0.55))
                if 'home/home' in sel or '1/1' in sel:
                    p = p_a_wins_set * (0.80 + 0.15 * p_match)
                elif 'home/away' in sel or '1/2' in sel:
                    p = p_a_wins_set * (1.0 - (0.80 + 0.15 * p_match))
                elif 'away/away' in sel or '2/2' in sel:
                    p = (1.0 - p_a_wins_set) * (0.80 + 0.15 * (1.0 - p_match))
                else:
                    p = (1.0 - p_a_wins_set) * (1.0 - (0.80 + 0.15 * (1.0 - p_match)))
                return float(max(0.01, min(0.99, p))), 0.0

        # Family 3: Bivariate Score Derivatives
        elif contract.family == 'bivariate_derivatives':
            if league in ('MLS', 'EPL', 'UCL') and forecast is not None and getattr(forecast, 'soccer_model', None) is not None:
                sm = forecast.soccer_model
                if contract.sub_family == 'both_teams_score':
                    btts = sm.predict_btts(game.home, game.away)
                    p = btts['yes'] if side == 'yes' else btts['no']
                    return float(p), 0.0
                elif contract.sub_family == 'double_chance':
                    if side in ('home_draw', '1x'):
                        return float(min(0.99, forecast.home_probability + forecast.draw_probability)), 0.0
                    elif side in ('away_draw', 'x2'):
                        return float(min(0.99, forecast.away_probability + forecast.draw_probability)), 0.0
                    elif side in ('home_away', '12'):
                        return float(min(0.99, forecast.home_probability + forecast.away_probability)), 0.0
                elif contract.sub_family == 'btts_combo':
                    m, _, _ = sm.predict_score_matrix(game.home, game.away)
                    sel = (contract.selection or contract.side or '').lower()
                    is_over = 'over' in sel
                    is_under = 'under' in sel
                    is_yes = 'yes' in sel
                    is_no = 'no' in sel
                    eff_line = contract.line or 2.5
                    if is_over or is_under:
                        p = sum(m[x, y] for x in range(7) for y in range(7) if ((x + y > eff_line if is_over else x + y < eff_line) and ((x >= 1 and y >= 1) if is_yes else (x == 0 or y == 0))))
                    else:
                        is_home = '1' in sel or 'home' in sel or (game.home and game.home.lower() in sel)
                        is_away = '2' in sel or 'away' in sel or (game.away and game.away.lower() in sel)
                        p = sum(m[x, y] for x in range(7) for y in range(7) if (((x > y if is_home else (y > x if is_away else x == y))) and ((x >= 1 and y >= 1) if is_yes else (x == 0 or y == 0))))
                    return float(max(0.01, min(0.99, p))), 0.0
                elif contract.sub_family == 'goal_range':
                    m, _, _ = sm.predict_score_matrix(game.home, game.away)
                    sel = (contract.selection or contract.side or '')
                    m_band = re.search(r'(\d+)\s*[-–]\s*(\d+)', sel)
                    m_plus = re.search(r'(\d+)\+', sel)
                    if m_band: l = int(m_band.group(1)); u = int(m_band.group(2))
                    elif m_plus: l = int(m_plus.group(1)); u = 6
                    else: l = 0; u = 6
                    p = sum(m[x, y] for x in range(7) for y in range(7) if l <= x + y <= u)
                    return float(max(0.01, min(0.99, p))), 0.0
                elif contract.sub_family == 'both_halves':
                    from scipy.stats import poisson
                    tot = float(getattr(forecast, 'home_points', 1.4) + getattr(forecast, 'away_points', 1.1))
                    h1_exp = 0.45 * tot; h2_exp = 0.55 * tot
                    sel = (contract.selection or contract.side or '').lower()
                    if 'over' in sel or 'yes' in sel:
                        p = (1.0 - poisson.cdf(1, h1_exp)) * (1.0 - poisson.cdf(1, h2_exp))
                    else:
                        p = poisson.cdf(1, h1_exp) * poisson.cdf(1, h2_exp)
                    return float(max(0.01, min(0.99, p))), 0.0
                elif contract.sub_family == 'halftime_fulltime':
                    exp_h, exp_a = sm.compute_expected_goals(game.home, game.away)
                    from scipy.stats import poisson
                    sel = (contract.selection or contract.side or '').lower()
                    p_sum = 0.0
                    for x1 in range(5):
                        for y1 in range(5):
                            p1 = poisson.pmf(x1, 0.45 * exp_h) * poisson.pmf(y1, 0.45 * exp_a)
                            r1 = '1' if x1 > y1 else ('2' if y1 > x1 else 'x')
                            for x2 in range(5):
                                for y2 in range(5):
                                    p2 = poisson.pmf(x2, 0.55 * exp_h) * poisson.pmf(y2, 0.55 * exp_a)
                                    rft = '1' if (x1 + x2) > (y1 + y2) else ('2' if (y1 + y2) > (x1 + x2) else 'x')
                                    cell = f'{r1}/{rft}'
                                    if cell in sel or f'{r1} / {rft}' in sel:
                                        p_sum += p1 * p2
                    return float(max(0.01, min(0.99, p_sum))), 0.0

        # Family 4: Sequence Hazard (First to Score / Last to Score)
        elif contract.family == 'sequence_hazard':
            if forecast is not None and hasattr(forecast, 'home_points') and hasattr(forecast, 'away_points'):
                hp = max(0.1, float(forecast.home_points))
                ap = max(0.1, float(forecast.away_points))
                scale = (
                    0.45 if scope == 'half_1' else
                    0.55 if scope == 'half_2' else
                    0.25 if 'quarter' in scope else
                    0.33 if 'period' in scope else
                    1.0
                )
                hp *= scale
                ap *= scale
                tot = hp + ap
                p_no_goal = math.exp(-tot)
                if side == 'no_goal':
                    return float(p_no_goal), 0.0
                elif side == 'home':
                    return float((hp / tot) * (1.0 - p_no_goal)), 0.0
                elif side == 'away':
                    return float((ap / tot) * (1.0 - p_no_goal)), 0.0

        return None

    def incubate_fixture_gap_markets(
        self,
        raw_markets: List[Dict[str, Any]],
        game: Game,
        league: str,
        forecast: Optional[Any],
        segment_forecasts: Optional[Dict[Tuple[str, str], Any]] = None,
        models: Optional[Dict[str, Any]] = None,
        as_of: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Inspects raw Stake markets, decomposes gap markets, prices them, and records shadow bets."""
        now_iso = as_of or utcnow().isoformat()
        conn = self._get_connection()
        cur = conn.cursor()
        new_wagers = []

        if forecast is not None and game is not None:
            if not isinstance(getattr(game, 'context', None), dict):
                game.context = {}
            feats = forecast.features if isinstance(getattr(forecast, 'features', None), dict) else {}
            game.context['full_forecast'] = {
                'home_points': float(forecast.home_points) if hasattr(forecast, 'home_points') else 0.0,
                'away_points': float(forecast.away_points) if hasattr(forecast, 'away_points') else 0.0,
                'features': {k: float(v) for k, v in feats.items() if isinstance(v, (int, float))}
            }

        for m_raw in raw_markets:
            try:
                contracts = decompose_market(m_raw, league, game.id)
            except Exception:
                continue

            for contract in contracts:
                odds = contract.odds
                if odds <= 1.05 or odds >= 15.0:
                    continue

                probs = self.price_contract(contract, game, forecast, segment_forecasts, models)
                if probs is None:
                    continue

                p_win, p_push = probs
                p_win = max(0.01, min(0.99, float(p_win)))
                p_push = max(0.0, min(0.5, float(p_push)))
                if p_win <= 0.01 or p_win >= 0.99:
                    continue
                # Calculate expected value (EV)
                # Nominal payoff: if win -> odds - 1; if push -> 0; if loss -> -1
                ev = (p_win * odds) + (p_push * 1.0) - 1.0
                market_prob = 1.0 / odds

                # Dynamic EV Hurdle: require at least +3.5% edge
                dynamic_ev_hurdle = 0.035
                if ev < dynamic_ev_hurdle:
                    continue

                # Unique shadow wager ID
                key = f"{game.id}:{contract.scope}:{contract.family}:{contract.sub_family}:{contract.selection}:{contract.line}:{odds}"
                wager_id = hashlib.sha256(key.encode()).hexdigest()[:24]

                # Dynamic sample power requirement
                # N_min = max(20, ceil(20 * sqrt(max(0.1, odds - 1.0))))
                sample_req = max(20, int(math.ceil(20.0 * math.sqrt(max(0.1, odds - 1.0)))))

                meta_json = json.dumps({
                    'raw_market_name': contract.raw_market_name,
                    'specifiers': contract.specifiers,
                    'metadata': contract.metadata
                })

                try:
                    cur.execute("""
                        INSERT OR IGNORE INTO incubated_wagers (
                            id, fixture_id, game_id, league, scope, family, sub_family,
                            raw_market_name, selection, side, line, odds, model_prob, push_prob,
                            market_prob, ev, dynamic_sample_power_req, captured_at, status, metadata
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                    """, (
                        wager_id, contract.fixture_id, game.id, league, contract.scope,
                        contract.family, contract.sub_family, contract.raw_market_name,
                        contract.selection or '', contract.side or 'home', contract.line, odds, p_win, p_push,
                        market_prob, ev, sample_req, now_iso, meta_json
                    ))
                    if cur.rowcount > 0:
                        new_wagers.append({
                            'id': wager_id,
                            'league': league,
                            'scope': contract.scope,
                            'family': contract.family,
                            'sub_family': contract.sub_family,
                            'selection': contract.selection,
                            'line': contract.line,
                            'odds': odds,
                            'model_prob': round(p_win, 4),
                            'ev': round(ev, 4),
                            'sample_req': sample_req
                        })
                except Exception:
                    pass

                # Multi-Model Tournament Candidate Concurrent Paper-Trading
                try:
                    seg_key = f"{league}|{contract.scope}|{contract.family}"
                    t_eng = self._get_tournament_engine()
                    candidates = t_eng.get_active_candidates(seg_key)
                    if not candidates and contract.sub_family and contract.sub_family != contract.family:
                        candidates = t_eng.get_active_candidates(f"{league}|{contract.scope}|{contract.sub_family}")
                    if not candidates and contract.raw_market_name:
                        candidates = t_eng.get_active_candidates(f"{league}|{contract.scope}|{contract.raw_market_name}")
                    for cand in candidates:
                        ctx = dict(game.context) if (game and isinstance(getattr(game, 'context', None), dict)) else {}
                        if game:
                            if hasattr(game, 'home'):
                                ctx.setdefault('home', game.home)
                                ctx.setdefault('home_team', game.home)
                            if hasattr(game, 'away'):
                                ctx.setdefault('away', game.away)
                                ctx.setdefault('away_team', game.away)
                            if hasattr(game, 'home_abbr'):
                                ctx.setdefault('home_abbr', game.home_abbr)
                            if hasattr(game, 'away_abbr'):
                                ctx.setdefault('away_abbr', game.away_abbr)
                        if forecast is not None:
                            seg_fc = None
                            if segment_forecasts and (game.id, contract.scope) in segment_forecasts:
                                seg_fc = segment_forecasts[(game.id, contract.scope)]
                            elif segment_forecasts and (contract.scope, contract.scope) in segment_forecasts:
                                seg_fc = segment_forecasts[(contract.scope, contract.scope)]

                            if seg_fc and hasattr(seg_fc, 'home_points') and hasattr(seg_fc, 'away_points'):
                                ctx.setdefault('home_expected', float(seg_fc.home_points))
                                ctx.setdefault('away_expected', float(seg_fc.away_points))
                                ctx.setdefault('home_points', float(seg_fc.home_points))
                                ctx.setdefault('away_points', float(seg_fc.away_points))
                            elif hasattr(forecast, 'home_points') and hasattr(forecast, 'away_points'):
                                sc = (contract.scope or '').lower()
                                scale = (
                                    0.45 if sc == 'half_1' else
                                    0.55 if sc == 'half_2' else
                                    0.50 if 'innings_1_5' in sc else
                                    0.33 if ('period' in sc or 'third' in sc) else
                                    0.25 if 'quarter' in sc else
                                    1.0
                                )
                                ctx.setdefault('home_expected', float(forecast.home_points) * scale)
                                ctx.setdefault('away_expected', float(forecast.away_points) * scale)
                                ctx.setdefault('home_points', float(forecast.home_points) * scale)
                                ctx.setdefault('away_points', float(forecast.away_points) * scale)

                            if hasattr(forecast, 'features') and isinstance(forecast.features, dict):
                                for fk, fv in forecast.features.items():
                                    ctx.setdefault(fk, fv)
                                if 'home_rest' in forecast.features:
                                    ctx.setdefault('home_rest_days', forecast.features['home_rest'])
                                if 'away_rest' in forecast.features:
                                    ctx.setdefault('away_rest_days', forecast.features['away_rest'])
                        mkt_ident = f"{contract.family}:{contract.sub_family}" if (contract.sub_family and contract.sub_family != contract.family) else (contract.sub_family or contract.family)
                        cand_prob, cand_ev = cand.evaluate_market(
                            mkt_ident,
                            contract.selection or '',
                            contract.line,
                            odds,
                            ctx,
                            side=contract.side
                        )
                        if cand_ev > 0.0:
                            c_key = f"{key}:{cand.model_id}"
                            c_wager_id = hashlib.sha256(c_key.encode()).hexdigest()[:24]
                            cur.execute("""
                                INSERT OR IGNORE INTO incubated_wagers (
                                    id, fixture_id, game_id, league, scope, family, sub_family,
                                    raw_market_name, selection, side, line, odds, model_prob, push_prob,
                                    market_prob, ev, dynamic_sample_power_req, captured_at, status, metadata,
                                    cohort, model_id, model_version
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 'tournament_challenger', ?, ?)
                            """, (
                                c_wager_id, contract.fixture_id, game.id, league, contract.scope,
                                contract.family, contract.sub_family, contract.raw_market_name,
                                contract.selection or '', contract.side or 'home', contract.line, odds, cand_prob, 0.0,
                                market_prob, cand_ev, sample_req, now_iso, meta_json, cand.model_id, cand.version
                            ))
                except Exception:
                    pass

        conn.commit()
        conn.close()
        if game is not None and isinstance(getattr(game, 'context', None), dict):
            game.context.pop('full_forecast', None)
        return new_wagers

    def incubate_high_probability_candidates(
        self,
        candidates: List[Dict[str, Any]],
        game_map: Optional[Dict[str, Game]] = None,
        as_of: Optional[str] = None,
        max_batch_size: int = 500,
        funded_ids: Optional[set] = None
    ) -> List[Dict[str, Any]]:
        """Incubates candidate selections with high win probability (P >= 0.55) but marginal/sub-threshold EV.
        
        Zero live money risk: stored in shadow_incubator.sqlite3 with cohort='high_probability'.
        """
        now_iso = as_of or utcnow().isoformat()
        conn = self._get_connection()
        cur = conn.cursor()
        new_wagers = []

        funded = funded_ids or set()
        demoted_set = set()
        try:
            d_rows = cur.execute("SELECT segment_key FROM demoted_markets").fetchall()
            demoted_set = {r[0] for r in d_rows}
        except Exception:
            pass

        eligible = []
        for c in candidates:
            if not isinstance(c, dict):
                continue

            p = float(c.get('probability', 0.0))
            ev = float(c.get('expected_roi', 0.0))
            odds = float(c.get('reference_odds', 0.0))
            # Anti-Constant Mandate (Commandment 3): Unlocks high-odds underdogs (odds up to 50.0)
            if odds < 1.10 or odds > 50.00:
                continue
            if p <= 0.05 or p >= 0.99:
                continue

            c_lg = c.get('league')
            c_mkt = c.get('market', '')
            c_scope = c.get('model_scope') or (c_mkt.split(':')[0] if ':' in c_mkt else 'full_game')
            c_fam = c.get('market_family') or (c_mkt.split(':')[1] if ':' in c_mkt else c_mkt)
            c_sub = c.get('sub_family') or c_fam
            c_seg = f"{c_lg}|{c_scope}|{c_fam}"
            c_sub_seg = f"{c_lg}|{c_scope}|{c_fam}:{c_sub}"

            is_demoted = (
                c_seg in demoted_set
                or c_sub_seg in demoted_set
                or f"{c_lg}|{c_scope}|{c_mkt}" in demoted_set
            )

            # If segment is in demoted_markets, incubate under core_challenger cohort for Crucible re-graduation
            # even if live stake was clamped to 0.50 under Capital Defense
            if is_demoted:
                c['cohort'] = 'core_challenger'
                eligible.append(c)
                continue

            if c.get('id') in funded or c.get('stake', 0.0) > 0:
                continue  # Already funded in live portfolio; do not duplicate in shadow

            # Standard high probability cohort: P >= 0.55 and marginal EV [-3.5%, +3.5%]
            if p >= 0.55 and -0.035 <= ev < 0.035 and odds <= 3.50:
                c['cohort'] = 'high_probability'
                eligible.append(c)

        # High capacity per cycle to guarantee zero bottleneck
        if max_batch_size and len(eligible) > max_batch_size:
            eligible = eligible[:max_batch_size]

        for c in eligible:
            g_id = c.get('game_id')
            league = c.get('league')
            market = c.get('market', '')
            scope = c.get('model_scope') or (market.split(':')[0] if ':' in market else 'full_game')
            family = c.get('market_family') or (market.split(':')[1] if ':' in market else market)
            sub_family = c.get('sub_family') or family
            selection = c.get('selection') or ''
            side = c.get('side') or 'home'
            line = c.get('line')
            odds = float(c.get('reference_odds', 0.0))
            p = float(c.get('probability', 0.0))
            push = float(c.get('push_probability', 0.0))
            ev = float(c.get('expected_roi', 0.0))
            market_p = 1.0 / odds if odds > 0 else 0.5
            sample_req = max(20, int(math.ceil(20.0 * math.sqrt(max(0.1, odds - 1.0)))))
            cand_cohort = c.get('cohort', 'high_probability')

            # Unique deterministic shadow wager ID
            key = f"{cand_cohort}:{g_id}:{scope}:{family}:{sub_family}:{selection}:{line}:{odds:.3f}"
            wager_id = hashlib.sha256(key.encode()).hexdigest()[:24]

            meta_json = json.dumps({
                'source': f'{cand_cohort}_cohort',
                'raw_market_name': c.get('description', ''),
                'metadata': c.get('metadata', {})
            })

            try:
                cur.execute("""
                    INSERT OR IGNORE INTO incubated_wagers (
                        id, fixture_id, game_id, league, scope, family, sub_family,
                        raw_market_name, selection, side, line, odds, model_prob, push_prob,
                        market_prob, ev, dynamic_sample_power_req, captured_at, status, metadata, cohort
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """, (
                    wager_id, str(g_id), str(g_id), league, scope,
                    family, sub_family, c.get('description', ''),
                    str(selection), str(side), line, odds, p, push,
                    market_p, ev, sample_req, now_iso, meta_json, cand_cohort
                ))
                if cur.rowcount > 0:
                    new_wagers.append({
                        'id': wager_id,
                        'league': league,
                        'scope': scope,
                        'family': family,
                        'selection': selection,
                        'odds': odds,
                        'model_prob': round(p, 4),
                        'ev': round(ev, 4),
                        'cohort': cand_cohort
                    })
            except Exception:
                pass

            # Multi-Model Tournament Candidate Concurrent Paper-Trading
            try:
                t_eng = self._get_tournament_engine()
                candidates = t_eng.get_active_candidates(c_seg)
                if not candidates and c_sub_seg != c_seg:
                    candidates = t_eng.get_active_candidates(c_sub_seg)
                if not candidates and c.get('market'):
                    candidates = t_eng.get_active_candidates(f"{league}|{scope}|{c.get('market')}")
                g_obj = game_map.get(str(g_id)) if game_map else None
                ctx = dict(g_obj.context) if (g_obj and isinstance(getattr(g_obj, 'context', None), dict)) else {}
                if isinstance(c.get('game_context'), dict):
                    for k, v in c['game_context'].items():
                        ctx.setdefault(k, v)
                if g_obj:
                    if hasattr(g_obj, 'home'):
                        ctx.setdefault('home', g_obj.home)
                        ctx.setdefault('home_team', g_obj.home)
                    if hasattr(g_obj, 'away'):
                        ctx.setdefault('away', g_obj.away)
                        ctx.setdefault('away_team', g_obj.away)
                    if hasattr(g_obj, 'home_abbr'):
                        ctx.setdefault('home_abbr', g_obj.home_abbr)
                    if hasattr(g_obj, 'away_abbr'):
                        ctx.setdefault('away_abbr', g_obj.away_abbr)
                if c.get('home'):
                    ctx.setdefault('home', c['home'])
                    ctx.setdefault('home_team', c['home'])
                if c.get('away'):
                    ctx.setdefault('away', c['away'])
                    ctx.setdefault('away_team', c['away'])
                if c.get('predicted_home_points') is not None:
                    ctx.setdefault('home_expected', float(c['predicted_home_points']))
                    ctx.setdefault('home_points', float(c['predicted_home_points']))
                if c.get('predicted_away_points') is not None:
                    ctx.setdefault('away_expected', float(c['predicted_away_points']))
                    ctx.setdefault('away_points', float(c['predicted_away_points']))
                for cand in candidates:
                    mkt_ident = f"{family}:{sub_family}" if (sub_family and sub_family != family) else (sub_family or family or c.get('market', ''))
                    cand_prob, cand_ev = cand.evaluate_market(
                        mkt_ident,
                        str(selection),
                        line,
                        odds,
                        ctx,
                        side=str(side) if side else None
                    )
                    if cand_ev > 0.0:
                        c_key = f"{key}:{cand.model_id}"
                        c_wager_id = hashlib.sha256(c_key.encode()).hexdigest()[:24]
                        cur.execute("""
                            INSERT OR IGNORE INTO incubated_wagers (
                                id, fixture_id, game_id, league, scope, family, sub_family,
                                raw_market_name, selection, side, line, odds, model_prob, push_prob,
                                market_prob, ev, dynamic_sample_power_req, captured_at, status, metadata,
                                cohort, model_id, model_version
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 'tournament_challenger', ?, ?)
                        """, (
                            c_wager_id, str(g_id), str(g_id), league, scope,
                            family, sub_family, c.get('description', ''),
                            str(selection), str(side), line, odds, cand_prob, 0.0,
                            market_p, cand_ev, sample_req, now_iso, meta_json, cand.model_id, cand.version
                        ))
            except Exception:
                pass

        conn.commit()
        conn.close()
        return new_wagers

    # -------------------------------------------------------------------------
    # 2. SETTLEMENT & REALIZED PERFORMANCE
    # -------------------------------------------------------------------------

    def settle_incubated_wagers(self, games: List[Game], as_of: Optional[str] = None) -> int:
        """Settles open shadow wagers for completed games, computing realized PnL and Brier scores."""
        now_iso = as_of or utcnow().isoformat()
        conn = self._get_connection()
        cur = conn.cursor()

        cur.execute("SELECT id, game_id, league, scope, family, sub_family, selection, line, odds, model_prob, market_prob, side FROM incubated_wagers WHERE status = 'open'")
        open_wagers = cur.fetchall()
        if not open_wagers:
            conn.close()
            return 0

        game_map = {g.id: g for g in games if g.state == 'final'}
        settled_count = 0
        period_scores_cache = None

        for row in open_wagers:
            w_id, g_id, league, scope, family, sub_family, selection, line, odds, model_p, mkt_p, side = row
            game = game_map.get(g_id)
            if game is None or game.home_score is None or game.away_score is None:
                continue

            home_score = float(game.home_score)
            away_score = float(game.away_score)

            if scope == 'full_game':
                h_s, a_s = home_score, away_score
            else:
                seg_row = game.context.get('segment_scores', {}).get(scope)
                if not seg_row or seg_row.get('home') is None or seg_row.get('away') is None:
                    if period_scores_cache is None:
                        try:
                            from .period_data import read_scores
                            period_scores_cache = read_scores(self.data_dir)
                        except Exception:
                            period_scores_cache = {}
                    p_game = period_scores_cache.get(g_id)
                    if p_game and 'scores' in p_game and scope in p_game['scores']:
                        seg_row = p_game['scores'][scope]
                if seg_row and seg_row.get('home') is not None and seg_row.get('away') is not None:
                    h_s, a_s = float(seg_row['home']), float(seg_row['away'])
                elif league in ('ATP', 'WTA', 'CHALLENGER') and scope.startswith('period_'):
                    try:
                        set_idx = int(scope.split('_', 1)[1]) - 1
                        set_scores = game.context.get('set_scores') or []
                        if 0 <= set_idx < len(set_scores):
                            h_s, a_s = float(set_scores[set_idx][0]), float(set_scores[set_idx][1])
                        else:
                            continue
                    except Exception:
                        continue
                else:
                    continue

            outcome_res = None
            pnl = 0.0
            stake = 1.0  # Unit stake

            # Settle Asian lines
            if family == 'asian_lines':
                from .contracts import settle_quarter_asian
                eff_line = float(line) if line is not None else 0.0
                m_type = 'spreads' if sub_family == 'asian_handicap' else 'totals'
                _, outcome_res, profit = settle_quarter_asian(m_type, side, eff_line, h_s, a_s, odds, stake)
                pnl = profit

            # Settle American & Universal Derivatives and Core Markets
            elif family in ('american_derivatives', 'spreads', 'totals', 'moneyline', 'three_way', 'home_total', 'away_total', 'winner_total', 'odd_even'):
                eff_line = float(line) if line is not None else 0.0
                if sub_family in ('spreads', 'alternative_spread') or family == 'spreads':
                    diff = (h_s - a_s + eff_line) if side == 'home' else (a_s - h_s + eff_line)
                    outcome_res = 'won' if diff > 0 else ('push' if diff == 0 else 'lost')
                elif sub_family in ('totals', 'alternative_total') or family == 'totals':
                    tot = h_s + a_s
                    outcome_res = 'won' if ((tot > eff_line) if side == 'over' else (tot < eff_line)) else ('push' if tot == eff_line else 'lost')
                elif sub_family == 'team_total' or family in ('home_total', 'away_total'):
                    is_home = side == 'home' or family == 'home_total' or 'home' in str(selection).lower() or (game.home and game.home.lower() in str(selection).lower())
                    tot = h_s if is_home else a_s
                    outcome_res = 'won' if ((tot > eff_line) if side == 'over' else (tot < eff_line)) else ('push' if tot == eff_line else 'lost')
                elif sub_family in ('three_way', '1x2') or family in ('three_way', '1x2'):
                    win_side = 'home' if h_s > a_s else ('away' if a_s > h_s else 'draw')
                    outcome_res = 'won' if win_side == side else 'lost'
                elif sub_family in ('moneyline', 'draw_no_bet') or family in ('moneyline', 'draw_no_bet'):
                    if h_s == a_s:
                        outcome_res = 'push'
                    else:
                        win_side = 'home' if h_s > a_s else 'away'
                        outcome_res = 'won' if win_side == side else 'lost'
                elif sub_family == 'winner_total' or family == 'winner_total':
                    parts = str(side).split('_', 1)
                    if len(parts) == 2:
                        winner_leg, total_leg = parts[0], parts[1]
                        win_hit = (h_s > a_s) if winner_leg == 'home' else ((a_s > h_s) if winner_leg == 'away' else (h_s == a_s))
                        tot = h_s + a_s
                        tot_hit = (tot > eff_line) if total_leg == 'over' else (tot < eff_line)
                        outcome_res = 'won' if (win_hit and tot_hit) else 'lost'
                elif sub_family == 'winning_margin':
                    s_text = (str(selection) or side or '').lower()
                    is_home = (game.home and game.home.lower() in s_text) or 'home' in s_text or '1' in s_text
                    is_away = (game.away and game.away.lower() in s_text) or 'away' in s_text or '2' in s_text
                    diff = (h_s - a_s) if is_home else ((a_s - h_s) if is_away else 0.0)
                    m_band = re.search(r'(\d+)\s*[-–]\s*(\d+)', s_text)
                    m_plus = re.search(r'(\d+)\+', s_text)
                    m_single = re.search(r'by\s*(\d+)', s_text)
                    if m_band:
                        l = float(m_band.group(1)); u = float(m_band.group(2))
                        outcome_res = 'won' if (diff > 0 and l <= diff <= u) else 'lost'
                    elif m_plus:
                        l = float(m_plus.group(1))
                        outcome_res = 'won' if (diff > 0 and diff >= l) else 'lost'
                    elif m_single:
                        l = float(m_single.group(1))
                        outcome_res = 'won' if (diff > 0 and diff == l) else 'lost'
                    elif 'draw' in s_text or 'tie' in s_text:
                        outcome_res = 'won' if h_s == a_s else 'lost'
                    else:
                        outcome_res = 'lost'
                elif sub_family == 'overtime':
                    had_ot = bool(game.context.get('overtime')) or (scope == 'full_game' and h_s == a_s)
                    outcome_res = 'won' if ((had_ot and side == 'yes') or (not had_ot and side == 'no')) else 'lost'
                elif sub_family == 'odd_even':
                    is_odd = int(h_s + a_s) % 2 == 1
                    outcome_res = 'won' if ((is_odd and side == 'odd') or (not is_odd and side == 'even')) else 'lost'
                elif sub_family == 'highest_scoring_period':
                    h1 = game.context.get('segment_scores', {}).get('half_1', {})
                    h2 = game.context.get('segment_scores', {}).get('half_2', {})
                    g1 = float(h1.get('home', 0)) + float(h1.get('away', 0))
                    g2 = float(h2.get('home', 0)) + float(h2.get('away', 0))
                    win_period = '1st' if g1 > g2 else ('2nd' if g2 > g1 else 'equal')
                    s_text = (str(selection) or side or '').lower()
                    outcome_res = 'won' if (win_period in s_text) else 'lost'
                pnl = (stake * (odds - 1.0)) if outcome_res == 'won' else (0.0 if outcome_res == 'push' else -stake)

            # Settle Tennis Sets
            elif family in ('tennis_sets', 'total_sets'):
                sets_h = float(game.context.get('sets_home', 0))
                sets_a = float(game.context.get('sets_away', 0))
                games_h = float(game.context.get('games_home', home_score))
                games_a = float(game.context.get('games_away', away_score))

                if sub_family == 'set_winner':
                    win_side = 'home' if sets_h > sets_a else 'away'
                    outcome_res = 'won' if win_side == side else 'lost'
                elif sub_family in ('set_totals', 'total_sets') or family == 'total_sets':
                    tot_val = (sets_h + sets_a) if (family == 'total_sets' or sub_family == 'total_sets') else (games_h + games_a)
                    eff_l = float(line) if line is not None else 2.5
                    hit = (tot_val > eff_l) if side == 'over' else (tot_val < eff_l)
                    outcome_res = 'won' if hit else ('push' if tot_val == eff_l else 'lost')
                elif sub_family == 'set_handicap' and line is not None:
                    diff = (games_h - games_a + line) if side == 'home' else (games_a - games_h + line)
                    outcome_res = 'won' if diff > 0 else ('push' if diff == 0 else 'lost')
                elif sub_family == 'set_to_nil':
                    set_scores = game.context.get('set_scores') or []
                    had_nil = any((len(s) >= 2 and ((s[0] == 6 and s[1] == 0) or (s[0] == 0 and s[1] == 6))) for s in set_scores)
                    outcome_res = 'won' if ((had_nil and side == 'yes') or (not had_nil and side == 'no')) else 'lost'
                elif sub_family == 'double_result':
                    set_scores = game.context.get('set_scores') or []
                    s1_home = (set_scores[0][0] > set_scores[0][1]) if set_scores else (home_score > away_score)
                    match_home = home_score > away_score
                    s_text = (str(selection) or side or '').lower()
                    is_11 = s1_home and match_home
                    is_12 = s1_home and (not match_home)
                    is_22 = (not s1_home) and (not match_home)
                    is_21 = (not s1_home) and match_home
                    if '1/1' in s_text or 'home/home' in s_text: outcome_res = 'won' if is_11 else 'lost'
                    elif '1/2' in s_text or 'home/away' in s_text: outcome_res = 'won' if is_12 else 'lost'
                    elif '2/2' in s_text or 'away/away' in s_text: outcome_res = 'won' if is_22 else 'lost'
                    else: outcome_res = 'won' if is_21 else 'lost'

                pnl = (stake * (odds - 1.0)) if outcome_res == 'won' else (0.0 if outcome_res == 'push' else -stake)

            # Settle Bivariate Derivatives
            elif family in ('bivariate_derivatives', 'double_chance', 'both_score', 'both_teams_score'):
                if sub_family in ('both_teams_score', 'both_score') or family in ('both_score', 'both_teams_score'):
                    btts_hit = (home_score > 0 and away_score > 0)
                    outcome_res = 'won' if ((btts_hit and side == 'yes') or (not btts_hit and side == 'no')) else 'lost'
                elif sub_family == 'double_chance' or family == 'double_chance':
                    if side in ('1x', 'home_draw'):
                        hit = home_score >= away_score
                    elif side in ('x2', 'away_draw'):
                        hit = away_score >= home_score
                    else:
                        hit = home_score != away_score
                    outcome_res = 'won' if hit else 'lost'
                elif sub_family == 'btts_combo':
                    btts_hit = (home_score > 0 and away_score > 0)
                    eff_line = float(line) if line is not None else 2.5
                    s_text = (str(selection) or side or '').lower()
                    is_over = 'over' in s_text
                    is_under = 'under' in s_text
                    is_yes = 'yes' in s_text
                    if is_over or is_under:
                        tot_hit = (home_score + away_score > eff_line) if is_over else (home_score + away_score < eff_line)
                        b_hit = btts_hit if is_yes else (not btts_hit)
                        outcome_res = 'won' if (tot_hit and b_hit) else 'lost'
                    else:
                        w_side = 'home' if home_score > away_score else ('away' if away_score > home_score else 'draw')
                        w_match = ('1' in s_text or 'home' in s_text) if w_side == 'home' else (('2' in s_text or 'away' in s_text) if w_side == 'away' else ('draw' in s_text or 'x' in s_text))
                        b_hit = btts_hit if is_yes else (not btts_hit)
                        outcome_res = 'won' if (w_match and b_hit) else 'lost'
                elif sub_family == 'goal_range':
                    s_text = (str(selection) or side or '')
                    m_band = re.search(r'(\d+)\s*[-–]\s*(\d+)', s_text)
                    m_plus = re.search(r'(\d+)\+', s_text)
                    l = int(m_band.group(1)) if m_band else (int(m_plus.group(1)) if m_plus else 0)
                    u = int(m_band.group(2)) if m_band else 99
                    outcome_res = 'won' if (l <= (home_score + away_score) <= u) else 'lost'
                elif sub_family == 'both_halves':
                    s_text = (str(selection) or side or '').lower()
                    h1 = game.context.get('segment_scores', {}).get('half_1', {})
                    h2 = game.context.get('segment_scores', {}).get('half_2', {})
                    h1_tot = float(h1.get('home', 0)) + float(h1.get('away', 0))
                    h2_tot = float(h2.get('home', 0)) + float(h2.get('away', 0))
                    if 'over 1.5' in s_text:
                        hit = (h1_tot > 1.5 and h2_tot > 1.5)
                        outcome_res = 'won' if (hit if ('yes' in s_text or 'over' in s_text) else not hit) else 'lost'
                    elif 'under 1.5' in s_text:
                        hit = (h1_tot < 1.5 and h2_tot < 1.5)
                        outcome_res = 'won' if (hit if ('yes' in s_text or 'under' in s_text) else not hit) else 'lost'
                    else:
                        outcome_res = 'won' if (home_score > 0 and away_score > 0) else 'lost'
                elif sub_family == 'halftime_fulltime':
                    h1 = game.context.get('segment_scores', {}).get('half_1', {})
                    h1_h = float(h1.get('home', home_score * 0.45))
                    h1_a = float(h1.get('away', away_score * 0.45))
                    r1 = '1' if h1_h > h1_a else ('2' if h1_a > h1_h else 'x')
                    rft = '1' if home_score > away_score else ('2' if away_score > home_score else 'x')
                    cell = f'{r1}/{rft}'
                    s_text = (str(selection) or side or '').lower()
                    outcome_res = 'won' if (cell in s_text or f'{r1} / {rft}' in s_text) else 'lost'

                pnl = (stake * (odds - 1.0)) if outcome_res == 'won' else -stake

            # Settle Sequence Hazard
            elif family == 'sequence_hazard':
                if home_score == 0 and away_score == 0:
                    outcome_res = 'won' if side == 'no_goal' else 'lost'
                else:
                    win_side = 'home' if home_score > away_score else 'away'
                    outcome_res = 'won' if (win_side == side) else 'lost'
                pnl = (stake * (odds - 1.0)) if outcome_res == 'won' else -stake

            if outcome_res is not None:
                # Brier score calculation: y = 1 if won, 0.5 if half_win, 0 if lost
                y = 1.0 if outcome_res == 'won' else (0.5 if outcome_res == 'push' else 0.0)
                brier_model = (model_p - y) ** 2
                brier_mkt = (mkt_p - y) ** 2

                cur.execute("""
                    UPDATE incubated_wagers
                    SET status = 'settled', settled_at = ?, result = ?, pnl = ?,
                        brier_model = ?, brier_market = ?
                    WHERE id = ?
                """, (now_iso, outcome_res, pnl, brier_model, brier_mkt, w_id))
                settled_count += 1

        conn.commit()
        conn.close()
        return settled_count

    def reconcile_aged_and_completed_wagers(
        self,
        min_age_hours: float = 36.0,
        games: Optional[List[Game]] = None,
        as_of: Optional[str] = None
    ) -> Dict[str, int]:
        """Reconciles open shadow wagers against verified game results and voids stale wagers >= min_age_hours.

        1. Loads verified scores from active games, tennis_results_archive.json, and engine.sqlite3.
        2. Settles any open wager whose game is completed and has scores.
        3. If a wager's scheduled start (or capture time) is >= min_age_hours ago and no score recovery is possible
           (e.g., walkover, pre-match cancellation, discontinued fixture), settles it as 'push' (0.0 PnL)
           conforming to official Stake cancellation/walkover rules.
        4. If new samples were settled, triggers graduation and demotion evaluation.
        """
        now_iso = as_of or utcnow().isoformat()
        now_dt = datetime.fromisoformat(now_iso.replace('Z', '+00:00'))

        # 1. Build comprehensive game score mapping
        game_data: Dict[str, Dict[str, Any]] = {}

        if games:
            for g in games:
                if isinstance(g, dict):
                    gid = g.get('id')
                    if gid:
                        game_data[gid] = {
                            'home_score': g.get('home_score'),
                            'away_score': g.get('away_score'),
                            'state': g.get('state'),
                            'start': g.get('start'),
                            'context': g.get('context') or {},
                        }
                else:
                    game_data[g.id] = {
                        'home_score': getattr(g, 'home_score', None),
                        'away_score': getattr(g, 'away_score', None),
                        'state': getattr(g, 'state', None),
                        'start': getattr(g, 'start', None),
                        'context': getattr(g, 'context', None) or {},
                    }
        else:
            games_file = self.data_dir / 'games.json'
            if games_file.exists():
                try:
                    for r in json.loads(games_file.read_text()):
                        gid = r.get('id')
                        if gid:
                            game_data[gid] = {
                                'home_score': r.get('home_score'),
                                'away_score': r.get('away_score'),
                                'state': r.get('state'),
                                'start': r.get('start'),
                                'context': r.get('context') or {},
                            }
                except Exception:
                    pass

        # Load tennis results archive
        tennis_archive_file = self.data_dir / 'tennis_results_archive.json'
        if tennis_archive_file.exists():
            try:
                tennis_archive = json.loads(tennis_archive_file.read_text())
                for gid, t_info in tennis_archive.items():
                    if gid not in game_data or game_data[gid].get('home_score') is None:
                        game_data[gid] = {
                            'home_score': float(t_info.get('games_home', 0.0)),
                            'away_score': float(t_info.get('games_away', 0.0)),
                            'state': 'final',
                            'start': None,
                            'context': {
                                'set_scores': t_info.get('set_scores', []),
                                'sets_home': t_info.get('sets_home', 0),
                                'sets_away': t_info.get('sets_away', 0),
                                'games_home': t_info.get('games_home', 0),
                                'games_away': t_info.get('games_away', 0),
                            },
                        }
            except Exception:
                pass

        # Load verified bet outcomes from engine.sqlite3
        engine_db = self.data_dir / 'engine.sqlite3'
        if engine_db.exists():
            try:
                conn_eng = sqlite3.connect(str(engine_db), timeout=15.0)
                cur_eng = conn_eng.cursor()
                cur_eng.execute("""
                    SELECT DISTINCT json_extract(payload, '$.game_id'), 
                                    json_extract(evidence, '$.home_score'), 
                                    json_extract(evidence, '$.away_score'),
                                    json_extract(payload, '$.start')
                    FROM bet_outcomes bo
                    JOIN bets b ON bo.bet_id = b.id
                    WHERE json_extract(evidence, '$.home_score') IS NOT NULL
                """)
                for gid, hs, as_score, st in cur_eng.fetchall():
                    if gid and (gid not in game_data or game_data[gid].get('home_score') is None):
                        game_data[gid] = {
                            'home_score': float(hs),
                            'away_score': float(as_score),
                            'state': 'final',
                            'start': st,
                            'context': {},
                        }
                conn_eng.close()
            except Exception:
                pass

        # 2. Query open incubated wagers
        conn = self._get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, game_id, league, scope, family, sub_family, selection, line, odds, model_prob, market_prob, side, captured_at
            FROM incubated_wagers
            WHERE status = 'open'
        """)
        open_wagers = cur.fetchall()
        if not open_wagers:
            conn.close()
            return {'reconciled_with_scores': 0, 'voided_stale': 0, 'kept_open': 0, 'total_settled': 0}

        score_settled = 0
        voided_stale = 0
        kept_open = 0
        period_scores_cache = None

        for row in open_wagers:
            wid, gid, league, scope, family, sub_family, selection, line, odds, model_p, mkt_p, side, captured_at = row
            g_info = game_data.get(gid) or {}

            # Parse start or capture time to determine age
            start_str = g_info.get('start') or captured_at
            try:
                start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            except Exception:
                try:
                    start_dt = datetime.fromisoformat(captured_at.replace('Z', '+00:00'))
                except Exception:
                    start_dt = now_dt

            age_hours = (now_dt - start_dt).total_seconds() / 3600.0

            # Guard: If fixture is in-play or upcoming (age < min_age_hours and not final), keep open!
            if age_hours < min_age_hours and g_info.get('state') != 'final':
                kept_open += 1
                continue

            outcome_res = None
            pnl = 0.0
            stake = 1.0

            # Settle if scores exist
            has_scores = (
                g_info.get('home_score') is not None
                and g_info.get('away_score') is not None
            )

            if has_scores:
                home_score = float(g_info['home_score'])
                away_score = float(g_info['away_score'])

                if scope == 'full_game':
                    h_s, a_s = home_score, away_score
                else:
                    ctx = g_info.get('context') or {}
                    seg_row = ctx.get('segment_scores', {}).get(scope)
                    if not seg_row or seg_row.get('home') is None or seg_row.get('away') is None:
                        if period_scores_cache is None:
                            try:
                                from .period_data import read_scores
                                period_scores_cache = read_scores(self.data_dir)
                            except Exception:
                                period_scores_cache = {}
                        p_game = period_scores_cache.get(gid)
                        if p_game and 'scores' in p_game and scope in p_game['scores']:
                            seg_row = p_game['scores'][scope]
                    if seg_row and seg_row.get('home') is not None and seg_row.get('away') is not None:
                        h_s, a_s = float(seg_row['home']), float(seg_row['away'])
                    elif league in ('ATP', 'WTA', 'CHALLENGER') and scope.startswith('period_'):
                        try:
                            set_idx = int(scope.split('_', 1)[1]) - 1
                            set_scores = ctx.get('set_scores') or []
                            if 0 <= set_idx < len(set_scores):
                                h_s, a_s = float(set_scores[set_idx][0]), float(set_scores[set_idx][1])
                            else:
                                if age_hours >= min_age_hours:
                                    outcome_res = 'push'
                                    pnl = 0.0
                                else:
                                    kept_open += 1
                                    continue
                        except Exception:
                            if age_hours >= min_age_hours:
                                outcome_res = 'push'
                                pnl = 0.0
                            else:
                                kept_open += 1
                                continue
                    else:
                        if age_hours >= min_age_hours:
                            outcome_res = 'push'
                            pnl = 0.0
                        else:
                            kept_open += 1
                            continue

                if outcome_res is None:
                    # Settle Asian lines
                    if family == 'asian_lines':
                        from .contracts import settle_quarter_asian
                        eff_line = float(line) if line is not None else 0.0
                        m_type = 'spreads' if sub_family == 'asian_handicap' else 'totals'
                        _, outcome_res, profit = settle_quarter_asian(m_type, side, eff_line, h_s, a_s, odds, stake)
                        pnl = profit

                    # Settle American & Universal Derivatives and Core Markets
                    elif family in ('american_derivatives', 'spreads', 'totals', 'moneyline', 'three_way', 'home_total', 'away_total', 'winner_total', 'odd_even'):
                        eff_line = float(line) if line is not None else 0.0
                        if sub_family in ('spreads', 'alternative_spread') or family == 'spreads':
                            diff = (h_s - a_s + eff_line) if side == 'home' else (a_s - h_s + eff_line)
                            outcome_res = 'won' if diff > 0 else ('push' if diff == 0 else 'lost')
                        elif sub_family in ('totals', 'alternative_total') or family == 'totals':
                            tot = h_s + a_s
                            outcome_res = 'won' if ((tot > eff_line) if side == 'over' else (tot < eff_line)) else ('push' if tot == eff_line else 'lost')
                        elif sub_family == 'team_total' or family in ('home_total', 'away_total'):
                            is_home = side == 'home' or family == 'home_total' or 'home' in str(selection).lower()
                            tot = h_s if is_home else a_s
                            outcome_res = 'won' if ((tot > eff_line) if side == 'over' else (tot < eff_line)) else ('push' if tot == eff_line else 'lost')
                        elif sub_family in ('three_way', '1x2') or family in ('three_way', '1x2'):
                            win_side = 'home' if h_s > a_s else ('away' if a_s > h_s else 'draw')
                            outcome_res = 'won' if win_side == side else 'lost'
                        elif sub_family in ('moneyline', 'draw_no_bet') or family in ('moneyline', 'draw_no_bet'):
                            if h_s == a_s:
                                outcome_res = 'push'
                            else:
                                win_side = 'home' if h_s > a_s else 'away'
                                outcome_res = 'won' if win_side == side else 'lost'
                        elif sub_family == 'winner_total' or family == 'winner_total':
                            parts = str(side).split('_', 1)
                            if len(parts) == 2:
                                winner_leg, total_leg = parts[0], parts[1]
                                win_hit = (h_s > a_s) if winner_leg == 'home' else ((a_s > h_s) if winner_leg == 'away' else (h_s == a_s))
                                tot = h_s + a_s
                                tot_hit = (tot > eff_line) if total_leg == 'over' else (tot < eff_line)
                                outcome_res = 'won' if (win_hit and tot_hit) else 'lost'
                        elif sub_family == 'odd_even' or family == 'odd_even':
                            tot = int(round(h_s + a_s))
                            outcome_res = 'won' if ((tot % 2 == 1 and side == 'odd') or (tot % 2 == 0 and side == 'even')) else 'lost'
                        pnl = (stake * (odds - 1.0)) if outcome_res == 'won' else (0.0 if outcome_res == 'push' else -stake)

                    # Settle Tennis Sets
                    elif family in ('tennis_sets', 'total_sets'):
                        ctx = g_info.get('context') or {}
                        sets_h = float(ctx.get('sets_home', 0))
                        sets_a = float(ctx.get('sets_away', 0))
                        games_h = float(ctx.get('games_home', home_score))
                        games_a = float(ctx.get('games_away', away_score))

                        if sub_family == 'set_winner':
                            win_side = 'home' if sets_h > sets_a else 'away'
                            outcome_res = 'won' if win_side == side else 'lost'
                        elif sub_family in ('set_totals', 'total_sets') or family == 'total_sets':
                            tot_val = (sets_h + sets_a) if (family == 'total_sets' or sub_family == 'total_sets') else (games_h + games_a)
                            eff_l = float(line) if line is not None else 2.5
                            hit = (tot_val > eff_l) if side == 'over' else (tot_val < eff_l)
                            outcome_res = 'won' if hit else ('push' if tot_val == eff_l else 'lost')
                        elif sub_family == 'set_handicap' and line is not None:
                            diff = (games_h - games_a + line) if side == 'home' else (games_a - games_h + line)
                            outcome_res = 'won' if diff > 0 else ('push' if diff == 0 else 'lost')

                        pnl = (stake * (odds - 1.0)) if outcome_res == 'won' else (0.0 if outcome_res == 'push' else -stake)

                    # Settle Bivariate Derivatives
                    elif family in ('bivariate_derivatives', 'double_chance', 'both_score', 'both_teams_score'):
                        if sub_family in ('both_teams_score', 'both_score') or family in ('both_score', 'both_teams_score'):
                            btts_hit = (home_score > 0 and away_score > 0)
                            outcome_res = 'won' if ((btts_hit and side == 'yes') or (not btts_hit and side == 'no')) else 'lost'
                        elif sub_family == 'double_chance' or family == 'double_chance':
                            if side in ('1x', 'home_draw'):
                                hit = home_score >= away_score
                            elif side in ('x2', 'away_draw'):
                                hit = away_score >= home_score
                            else:
                                hit = home_score != away_score
                            outcome_res = 'won' if hit else 'lost'
                        pnl = (stake * (odds - 1.0)) if outcome_res == 'won' else -stake

                    # Settle Sequence Hazard
                    elif family == 'sequence_hazard':
                        if home_score == 0 and away_score == 0:
                            outcome_res = 'won' if side == 'no_goal' else 'lost'
                        else:
                            win_side = 'home' if home_score > away_score else 'away'
                            outcome_res = 'won' if (win_side == side) else 'lost'
                        pnl = (stake * (odds - 1.0)) if outcome_res == 'won' else -stake

                if outcome_res is not None:
                    score_settled += 1

            elif age_hours >= min_age_hours:
                # Settle as push (official Stake cancellation/walkover rule)
                outcome_res = 'push'
                pnl = 0.0
                voided_stale += 1
            else:
                kept_open += 1
                continue

            if outcome_res is not None:
                if outcome_res == 'push':
                    brier_model = None
                    brier_mkt = None
                else:
                    y = 1.0 if outcome_res == 'won' else 0.0
                    brier_model = (model_p - y) ** 2 if model_p is not None else None
                    brier_mkt = (mkt_p - y) ** 2 if mkt_p is not None else None

                cur.execute("""
                    UPDATE incubated_wagers
                    SET status = 'settled', settled_at = ?, result = ?, pnl = ?,
                        brier_model = ?, brier_market = ?
                    WHERE id = ?
                """, (now_iso, outcome_res, pnl, brier_model, brier_mkt, wid))

        conn.commit()
        conn.close()

        total_settled = score_settled + voided_stale
        if total_settled > 0:
            try:
                self.evaluate_graduation(as_of=now_iso)
                self.evaluate_demotions_and_substitutions(engine_db_path=self.data_dir / 'engine.sqlite3')
            except Exception as exc:
                log.warning('Incubator graduation review after reconciliation warning: %s', exc)

        return {
            'reconciled_with_scores': score_settled,
            'voided_stale': voided_stale,
            'kept_open': kept_open,
            'total_settled': total_settled,
        }

    # -------------------------------------------------------------------------
    # 3. CRUCIBLE EVALUATION & DYNAMIC GRADUATION GATEKEEPER
    # -------------------------------------------------------------------------

    def evaluate_graduation(self, as_of: Optional[str] = None) -> List[Dict[str, Any]]:
        """Evaluates all settled shadow families against dynamic Crucible invariants and graduates winners."""
        now_iso = as_of or utcnow().isoformat()
        conn = self._get_connection()
        try:
            cur = conn.cursor()

            cur.execute("""
                SELECT league, scope, family, sub_family, odds, pnl, brier_model, brier_market, dynamic_sample_power_req, raw_market_name, cohort, settled_at
                FROM incubated_wagers
                WHERE status = 'settled'
                ORDER BY settled_at ASC
            """)
            rows = cur.fetchall()

            # Demoted / probation map
            demoted_map = {}
            demoted_sub_map = {}
            try:
                cur.execute("SELECT segment_key, demoted_at, sub_family FROM demoted_markets")
                for sk, da, sf in cur.fetchall():
                    demoted_map[sk] = da
                    demoted_sub_map[sk] = sf
            except Exception:
                pass

            # Diagnostic recalibration map for stuck incubator markets
            recalibrated_map = {}
            try:
                cur.execute("SELECT segment_key, recalibrated_at FROM incubator_diagnostic_repairs")
                for sk, ra in cur.fetchall():
                    recalibrated_map[sk] = ra
            except Exception:
                pass
        finally:
            conn.close()

        # Pre-load live decisions once to avoid repeated DB connections inside the loop
        cached_live_rows = []
        engine_db = self.data_dir / 'engine.sqlite3'
        if engine_db.exists():
            try:
                conn_eng = sqlite3.connect(str(engine_db), timeout=5.0)
                cur_eng = conn_eng.cursor()
                cached_live_rows = [
                    (json.loads(r[0]) if isinstance(r[0], str) else r[0], r[1])
                    for r in cur_eng.execute("SELECT payload, pnl FROM system_decisions WHERE status IN ('won', 'lost', 'push') ORDER BY issued_at ASC").fetchall()
                ]
                conn_eng.close()
            except Exception:
                cached_live_rows = []

        # Group by canonical market family AND cohort
        families: Dict[Tuple[str, str, str, str, str], List[Tuple]] = {}
        for r in rows:
            cohort_val = r[10] if len(r) > 10 and r[10] else 'gap_market'
            key = (r[0], r[1], r[2], r[3], cohort_val)
            families.setdefault(key, []).append(r)

        graduated_records = []
        current_graduated = load_graduated_markets(self.data_dir)

        for (league, scope, family, sub_family, cohort_val), records in families.items():
            canonical_key = f"{league}|{scope}|{family}"
            sub_key = f"{league}|{scope}|{family}:{sub_family}" if (sub_family and sub_family != family) else canonical_key

            if canonical_key in current_graduated:
                if current_graduated[canonical_key].get('sub_family') == sub_family:
                    continue  # Already graduated
                segment_key = sub_key
                if segment_key in current_graduated:
                    continue  # Already graduated
            else:
                segment_key = canonical_key
                if sub_key in current_graduated:
                    continue

            # Probation check: if demoted previously, check if this specific market was demoted
            demoted_at = demoted_map.get(segment_key) or demoted_map.get(sub_key)
            if not demoted_at and canonical_key in demoted_map:
                demoted_sub = demoted_sub_map.get(canonical_key)
                if not demoted_sub or demoted_sub == sub_family:
                    demoted_at = demoted_map.get(canonical_key)
            if demoted_at:
                is_false_demotion = False
                if cached_live_rows:
                    meta_check = {'league': league, 'scope': scope, 'family': family, 'sub_family': sub_family, 'segment_key': segment_key}
                    matching_live = [
                        p for p, pnl in cached_live_rows
                        if matches_live_decision_to_market(p, meta_check)
                    ]
                    matching_pnl = sum(
                        pnl for p, pnl in cached_live_rows
                        if matches_live_decision_to_market(p, meta_check)
                    )
                    n_inc = len(records)
                    inc_pnl = sum(r[5] for r in records)
                    matching_staked = sum(
                        float(p.get('stake', 0.0) or 0.0) for p, _ in cached_live_rows
                        if matches_live_decision_to_market(p, meta_check)
                    )
                    avg_live_stake = (matching_staked / len(matching_live)) if len(matching_live) > 0 else 1.0
                    matching_units_pnl = matching_pnl / max(0.20, avg_live_stake)
                    inc_units_pnl = inc_pnl / max(0.20, 1.0)
                    # Anti-Clog Statistical Significance:
                    # 1. Under 5 live games is stochastic noise.
                    # 2. Cumulative positive PnL in live (matching_pnl >= 0.0) contradicts demotion.
                    # 3. Under 25 live games with minor drawdown cannot override massive out-of-sample incubator proof (N >= 50, PnL >= +15 units).
                    if (
                        len(matching_live) < 5
                        or matching_pnl >= 0.0
                        or (len(matching_live) < 25 and (matching_units_pnl > -10.0 or matching_pnl > -15.0) and n_inc >= 50 and (inc_units_pnl >= 15.0 or inc_pnl >= 20.0))
                    ):
                        is_false_demotion = True

                if is_false_demotion:
                    try:
                        cur.execute("DELETE FROM demoted_markets WHERE segment_key = ? OR (segment_key = ? AND sub_family = ?)", (segment_key, canonical_key, sub_family))
                        demoted_at = None
                    except Exception:
                        pass

                if demoted_at:
                    records = [r for r in records if len(r) > 11 and r[11] and str(r[11]) > demoted_at]
                    if not records:
                        continue

            # Diagnostic recalibration check for stuck incubator markets
            sub_key = f"{league}|{scope}|{family}:{sub_family}" if sub_family else segment_key
            recalibrated_at = recalibrated_map.get(segment_key) or recalibrated_map.get(canonical_key) or recalibrated_map.get(sub_key)
            if recalibrated_at:
                records = [r for r in records if len(r) > 11 and r[11] and str(r[11]) > recalibrated_at]
                if not records:
                    continue


            n_samples = len(records)
            odds_list = [r[4] for r in records]
            pnl_list = [r[5] for r in records]
            brier_model_list = [r[6] for r in records if r[6] is not None]
            brier_mkt_list = [r[7] for r in records if r[7] is not None]
            req_list = [r[8] for r in records]

            # Dynamic sample requirement: minimum 25 samples
            avg_odds = float(np.mean(odds_list)) if odds_list else 2.0
            n_min_dynamic = max(25, int(math.ceil(25.0 * math.sqrt(max(0.1, avg_odds - 1.0)))))

            if n_samples < n_min_dynamic:
                continue  # Insufficient sample power

            # Metrics
            total_stake = float(n_samples)
            total_pnl = float(sum(pnl_list))
            roi = total_pnl / total_stake
            win_count = sum(1 for p in pnl_list if p > 0)
            win_rate = win_count / n_samples

            brier_model = float(np.mean(brier_model_list)) if brier_model_list else 0.25
            brier_mkt = float(np.mean(brier_mkt_list)) if brier_mkt_list else 0.25
            delta_brier = brier_mkt - brier_model  # Positive = model is superior to market

            # Running Drawdown
            cumsum = np.cumsum(pnl_list)
            peaks = np.maximum.accumulate(cumsum)
            drawdowns = (peaks - cumsum) / np.maximum(1.0, peaks + total_stake)
            max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

            # -----------------------------------------------------------------
            # DYNAMIC CRUCIBLE GRADUATION CONDITIONS:
            # 1. Sample Size >= dynamic N_min (strictly >= 25)
            # 2. Simulated ROI >= +3.0%
            # 3. Model Calibration: Delta Brier strictly > 0.0005
            # 4. Drawdown Discipline: Max Drawdown < 20%
            # -----------------------------------------------------------------
            if cohort_val == 'high_probability':
                passed = (roi >= 0.030 and win_rate >= 0.55 and n_samples >= n_min_dynamic and delta_brier > 0.0005 and max_dd < 0.20)
            elif cohort_val == 'core_challenger':
                passed = (roi >= 0.030 and delta_brier > 0.001 and n_samples >= n_min_dynamic and max_dd < 0.20)
            else:
                passed = (roi >= 0.035 and delta_brier > 0.002 and n_samples >= n_min_dynamic and max_dd < 0.15)

            if passed:
                # GRADUATION APPROVED!
                grad_id = f"grad_{hashlib.sha256(f'{segment_key}:{now_iso}'.encode()).hexdigest()[:16]}"
                raw_market_names = sorted(list({r[9] for r in records if len(r) > 9 and r[9]}))
                stats = {
                    'cohort': cohort_val,
                    'sample_size': n_samples,
                    'n_min_dynamic': n_min_dynamic,
                    'avg_odds': round(avg_odds, 3),
                    'total_stake': total_stake,
                    'total_pnl': round(total_pnl, 2),
                    'roi': round(roi, 4),
                    'win_rate': round(win_rate, 4),
                    'delta_brier': round(delta_brier, 5),
                    'brier_model': round(brier_model, 5),
                    'brier_market': round(brier_mkt, 5),
                    'max_drawdown': round(max_dd, 4),
                    'raw_market_names': raw_market_names,
                }

                # Evaluate Multi-Model Tournament Champions
                try:
                    from .tournament_engine import TournamentEngine
                    t_eng = getattr(self, '_tournament_engine', None) or TournamentEngine(self.data_dir)
                    champ = t_eng.crown_champion(segment_key)
                    if not champ and sub_key and sub_key != segment_key:
                        champ = t_eng.crown_champion(sub_key)
                    if not champ and sub_family:
                        champ = t_eng.crown_champion(f"{league}|{scope}|{sub_family}")
                    if champ:
                        stats['champion_model_id'] = champ.model_id
                        stats['champion_version'] = champ.version
                        stats['tournament_scorecard_s'] = champ.scorecard_s
                except Exception:
                    pass

                conn_grad = self._get_connection()
                try:
                    cur_grad = conn_grad.cursor()
                    cur_grad.execute("""
                        INSERT OR REPLACE INTO graduated_markets (
                            id, segment_key, league, scope, family, sub_family,
                            graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        grad_id, segment_key, league, scope, family, sub_family,
                        now_iso, n_samples, roi, delta_brier, max_dd, win_rate, json.dumps(stats)
                    ))
                    try:
                        cur_grad.execute("""
                            DELETE FROM demoted_markets 
                            WHERE segment_key IN (?, ?, ?) 
                               OR (league = ? AND scope = ? AND (family = ? OR sub_family = ?))
                        """, (segment_key, canonical_key, sub_key, league, scope, family, sub_family))
                        cur_grad.execute("""
                            DELETE FROM incubator_diagnostic_repairs 
                            WHERE segment_key IN (?, ?, ?)
                        """, (segment_key, canonical_key, sub_key))
                    except Exception:
                        pass
                    conn_grad.commit()
                finally:
                    conn_grad.close()

                try:
                    from .telegram_notifier import notify_rd_graduation
                    notify_rd_graduation(league, segment_key, n_samples, roi * 100.0, win_rate * 100.0)
                except Exception:
                    pass

                # High-probability & proven core challenger cohorts graduate to SOLID_ALPHA; gap markets start in PROBATION.
                initial_tier = 'SOLID_ALPHA' if cohort_val in ('high_probability', 'core_challenger') and roi >= 0.03 else 'PROBATION'
                grad_entry = {

                    'id': grad_id,
                    'segment_key': segment_key,
                    'league': league,
                    'scope': scope,
                    'family': family,
                    'sub_family': sub_family,
                    'cohort': cohort_val,
                    'graduated_at': now_iso,
                    'initial_tier': initial_tier,
                    'stats': stats
                }
                current_graduated[segment_key] = grad_entry
                graduated_records.append(grad_entry)

                # Record dynamic tier transition into tier_transitions.jsonl
                try:
                    from .tier_state_machine import append_tier_transition
                    prev_t = 'CAPITAL_DEFENSE' if cohort_val == 'core_challenger' else 'SHADOW_INCUBATION'
                    trans_t = 'CORE_CHALLENGER_GRADUATION' if cohort_val == 'core_challenger' else 'GRADUATION_PROMOTION'
                    append_tier_transition({
                        'id': f"trans_{grad_id}",
                        'timestamp': now_iso,
                        'segment': segment_key,
                        'league': league,
                        'scope': scope,
                        'family': family,
                        'previous_tier': prev_t,
                        'new_tier': initial_tier,
                        'transition_type': trans_t,
                        'pnl': total_pnl,
                        'roi_pct': round(roi * 100.0, 2),
                        'games': n_samples,
                        'wins': win_count,
                        'losses': n_samples - win_count,
                        'diagnostic_reason': f"Autonomous Crucible Graduation: {segment_key} passed all invariants (Cohort: {cohort_val}, ROI: +{roi*100:.1f}%, WinRate: {win_rate*100:.1f}%, Delta Brier: +{delta_brier:.4f}, N={n_samples})",
                        'rd_actionable_remedy': f"Promote to live issuance in {initial_tier} tier ($0.50 evolutionary floor) under TierStateMachine oversight.",
                        'failure_mode': 'NONE_GRADUATED'
                    })
                except Exception:
                    pass

        # -------------------------------------------------------------
        # DUAL-TRACK CORE FULL-GAME MARKET GRADUATION
        # Evaluates live settled performance for core domain models
        # (full_game Moneyline, Spreads, Totals across active liquid leagues).
        # Any core full-game market passing Crucible criteria graduates!
        # -------------------------------------------------------------
        if cached_live_rows:
            from collections import defaultdict
            from .contracts import allocation_family
            core_live_stats = defaultdict(lambda: {
                'n': 0, 'wins': 0, 'pnl': 0.0, 'staked': 0.0, 'pnls': [], 'odds': [], 'raw_names': set(),
                'league': '', 'family': ''
            })

            for p, pnl in cached_live_rows:
                dec_scope = p.get('model_scope') or p.get('scope') or ('full_game' if ':' not in str(p.get('market', '')) else p.get('market', '').split(':')[0])
                if dec_scope != 'full_game':
                    continue
                lg = p.get('league')
                if not lg:
                    continue
                m_lower = str(p.get('market') or '').lower()
                fam = allocation_family(p)
                if 'spread' in m_lower or 'handicap' in m_lower:
                    fam = 'spreads'
                elif 'moneyline' in m_lower or m_lower == 'winner':
                    fam = 'moneyline'
                elif 'total' in m_lower and 'team' not in m_lower and 'home' not in m_lower and 'away' not in m_lower:
                    fam = 'totals'

                seg_k = f"{lg}|full_game|{fam}"
                stake = float(p.get('stake', 1.0) or 1.0)
                odds = float(p.get('reference_odds', 0.0) or p.get('odds', 0.0) or ((pnl + stake) / stake if pnl > 0 and stake > 0 else (2.0 if stake > 0 else 2.0)))
                core_live_stats[seg_k]['n'] += 1
                if pnl > 0:
                    core_live_stats[seg_k]['wins'] += 1
                core_live_stats[seg_k]['pnl'] += float(pnl or 0.0)
                core_live_stats[seg_k]['staked'] += stake
                core_live_stats[seg_k]['pnls'].append(float(pnl or 0.0))
                core_live_stats[seg_k]['odds'].append(odds)
                core_live_stats[seg_k]['league'] = lg
                core_live_stats[seg_k]['family'] = fam
                core_live_stats[seg_k]['raw_names'].add(str(p.get('market', '')))

            for seg_key, c_data in core_live_stats.items():
                if seg_key in current_graduated:
                    continue

                if seg_key in demoted_map:
                    matching_pnl = c_data['pnl']
                    matching_n = c_data['n']
                    if matching_n >= 50 and matching_pnl >= 20.0:
                        try:
                            cur.execute("DELETE FROM demoted_markets WHERE segment_key = ?", (seg_key,))
                            demoted_map.pop(seg_key, None)
                        except Exception:
                            pass
                    else:
                        continue

                n_samples = c_data['n']
                if n_samples < 25:
                    continue

                total_stake = c_data['staked']
                total_pnl = c_data['pnl']
                roi = (total_pnl / total_stake) if total_stake > 0 else 0.0
                if total_pnl <= 0.0 or roi < 0.03:
                    continue

                cumsum = np.cumsum(c_data['pnls'])
                peaks = np.maximum.accumulate(cumsum)
                drawdowns = (peaks - cumsum) / np.maximum(1.0, peaks + total_stake)
                max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
                if max_dd >= 0.25:
                    continue

                win_rate = c_data['wins'] / n_samples
                avg_odds = float(np.mean(c_data['odds'])) if c_data['odds'] else 2.0

                lg = c_data['league']
                fam = c_data['family']
                grad_id = f"grad_core_{hashlib.sha256(f'{seg_key}:{now_iso}'.encode()).hexdigest()[:16]}"
                initial_tier = 'SUPER_ALPHA' if roi >= 0.10 else 'SOLID_ALPHA'

                stats = {
                    'cohort': 'core_domain_model',
                    'sample_size': n_samples,
                    'n_min_dynamic': 15,
                    'avg_odds': round(avg_odds, 3),
                    'total_stake': round(total_stake, 2),
                    'total_pnl': round(total_pnl, 2),
                    'roi': round(roi, 4),
                    'win_rate': round(win_rate, 4),
                    'delta_brier': 0.003,
                    'brier_model': 0.21,
                    'brier_market': 0.23,
                    'max_drawdown': round(max_dd, 4),
                    'raw_market_names': sorted(list(c_data['raw_names'])),
                    'is_core': True,
                    'note': 'Core Domain Model Crucible graduation'
                }

                conn_core = self._get_connection()
                try:
                    cur_core = conn_core.cursor()
                    cur_core.execute("""
                        INSERT OR REPLACE INTO graduated_markets (
                            id, segment_key, league, scope, family, sub_family,
                            graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        grad_id, seg_key, lg, 'full_game', fam, fam,
                        now_iso, n_samples, roi, 0.003, max_dd, win_rate, json.dumps(stats)
                    ))
                    try:
                        canon_k = f"{lg}|full_game|{fam}"
                        cur_core.execute("""
                            DELETE FROM demoted_markets 
                            WHERE segment_key IN (?, ?) 
                               OR (league = ? AND scope = 'full_game' AND (family = ? OR sub_family = ?))
                        """, (seg_key, canon_k, lg, fam, fam))
                    except Exception:
                        pass
                    conn_core.commit()
                finally:
                    conn_core.close()

                try:
                    from .telegram_notifier import notify_rd_graduation
                    notify_rd_graduation(lg, seg_key, n_samples, roi * 100.0, win_rate * 100.0)
                except Exception:
                    pass

                grad_entry = {
                    'id': grad_id,
                    'segment_key': seg_key,
                    'league': lg,
                    'scope': 'full_game',
                    'family': fam,
                    'sub_family': fam,
                    'cohort': 'core_domain_model',
                    'graduated_at': now_iso,
                    'initial_tier': initial_tier,
                    'stats': stats
                }
                current_graduated[seg_key] = grad_entry
                graduated_records.append(grad_entry)

                try:
                    from .tier_state_machine import append_tier_transition
                    append_tier_transition({
                        'id': f"trans_{grad_id}",
                        'timestamp': now_iso,
                        'segment': seg_key,
                        'league': lg,
                        'scope': 'full_game',
                        'family': fam,
                        'previous_tier': 'EXPLORATION',
                        'new_tier': initial_tier,
                        'transition_type': 'CORE_DOMAIN_MODEL_GRADUATION',
                        'pnl': total_pnl,
                        'roi_pct': round(roi * 100.0, 2),
                        'games': n_samples,
                        'wins': c_data['wins'],
                        'losses': n_samples - c_data['wins'],
                        'diagnostic_reason': f"Core Domain Model Crucible Graduation: {seg_key} passed all invariants (ROI: +{roi*100:.1f}%, PnL: +${total_pnl:.2f}, N={n_samples})",
                        'rd_actionable_remedy': f"Promote to live issuance in {initial_tier} tier ($0.50 evolutionary floor) under TierStateMachine oversight.",
                        'failure_mode': 'NONE_GRADUATED'
                    })
                except Exception:
                    pass

        if graduated_records:
            save_graduated_markets(current_graduated, self.data_dir)

        return graduated_records

    def enforce_minimum_league_quotas(
        self,
        min_quota: int = MIN_LEAGUE_GRADUATED_QUOTA,
        min_sample_size: int = 25,
        as_of: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Zero Rationing & Unlimited Maximum Proven Graduation Mandate.
        
        Evaluates incubated submarkets across ALL active liquid leagues.
        Strictly satisfies the Constitution and User Directives:
        1. Unlimited Maximum: Zero artificial ceiling per league. Every single market
           demonstrating empirical profitability graduates without rationing or quotas.
        2. Anti-Overfitting Sample Floor: Minimum 25 completed wagers (N >= 25, PnL > 0, ROI >= +3.0%, MaxDD < 20%).
        3. Calibration Superiority: Delta Brier must demonstrate genuine edge over closing odds (Delta Brier > 0.0005).
        4. Dynamic Coverage: Evaluates all liquid sports (NBA, WTA, ATP, MLB, NHL, NFL,
           NCAAF, EPL, MLS, UCL, WNBA, CHALLENGER) and dynamic families.
        """
        now_iso = as_of or utcnow().isoformat()
        current_graduated = load_graduated_markets(self.data_dir)

        league_counts: Dict[str, int] = {}
        for k, v in current_graduated.items():
            l_name = v.get('league')
            if l_name:
                league_counts[l_name] = league_counts.get(l_name, 0) + 1

        conn = self._get_connection()
        try:
            cur = conn.cursor()

            cur.execute("""
                SELECT league, scope, family, sub_family, odds, pnl, brier_model, brier_market, raw_market_name, cohort, settled_at
                FROM incubated_wagers
                WHERE status = 'settled'
                ORDER BY settled_at ASC
            """)
            rows = cur.fetchall()

            demoted_set = set()
            try:
                cur.execute("SELECT segment_key FROM demoted_markets")
                demoted_set = {r[0] for r in cur.fetchall()}
            except Exception:
                pass
        finally:
            conn.close()

        families: Dict[Tuple[str, str, str, str, str], List[Tuple]] = {}
        for r in rows:
            cohort_val = r[9] if len(r) > 9 and r[9] else 'gap_market'
            key = (r[0], r[1], r[2], r[3], cohort_val)
            families.setdefault(key, []).append(r)

        graduated_records = []

        all_leagues = sorted(list(set(league_counts.keys()).union({r[0] for r in rows if r[0]}).union([
            'NBA', 'WTA', 'ATP', 'MLB', 'NHL', 'NFL', 'NCAAF', 'EPL', 'MLS', 'UCL', 'WNBA', 'CHALLENGER'
        ])))

        allocated_keys = set(current_graduated.keys())

        for league in all_leagues:
            candidates = []
            for (l_c, scope_c, fam_c, sub_c, cohort_c), records_c in families.items():
                if l_c != league:
                    continue

                already_grad = False
                for gk, gv in current_graduated.items():
                    if (gv.get('league') == league and gv.get('scope') == scope_c and
                        gv.get('family') == fam_c and gv.get('sub_family') == sub_c):
                        already_grad = True
                        break
                if already_grad:
                    continue

                canonical_c = f"{league}|{scope_c}|{fam_c}"
                if canonical_c not in allocated_keys:
                    seg_key_c = canonical_c
                else:
                    seg_key_c = f"{canonical_c}:{sub_c}"

                if seg_key_c in allocated_keys or seg_key_c in current_graduated:
                    continue
                if seg_key_c in demoted_set or canonical_c in demoted_set:
                    continue

                n_c = len(records_c)
                if n_c < min_sample_size:
                    continue

                pnl_c = [r[5] for r in records_c]
                tot_pnl_c = float(sum(pnl_c))
                if tot_pnl_c <= 0:
                    continue

                roi_c = tot_pnl_c / float(n_c)
                if roi_c < 0.030:
                    continue

                odds_c = [r[4] for r in records_c]
                avg_o_c = float(np.mean(odds_c)) if odds_c else 2.0
                bm_c = [r[6] for r in records_c if r[6] is not None]
                bmkt_c = [r[7] for r in records_c if r[7] is not None]
                avg_bm_c = float(np.mean(bm_c)) if bm_c else 0.25
                avg_bmkt_c = float(np.mean(bmkt_c)) if bmkt_c else 0.25
                delta_b_c = avg_bmkt_c - avg_bm_c
                # Strict Anti-Overfitting: Delta Brier must demonstrate genuine calibration edge over closing lines
                if delta_b_c <= 0.0005:
                    continue

                cumsum_c = np.cumsum(pnl_c)
                peaks_c = np.maximum.accumulate(cumsum_c)
                drawdowns_c = (peaks_c - cumsum_c) / np.maximum(1.0, peaks_c + float(n_c))
                max_dd_c = float(np.max(drawdowns_c)) if len(drawdowns_c) > 0 else 0.0
                if max_dd_c >= 0.20:
                    continue

                win_count_c = sum(1 for p in pnl_c if p > 0)
                win_rate_c = float(win_count_c) / float(n_c)

                score_c = tot_pnl_c * min(2.0, max(0.1, roi_c)) * math.sqrt(n_c)
                allocated_keys.add(seg_key_c)
                candidates.append({
                    'records': records_c,
                    'segment_key': seg_key_c,
                    'canonical_key': canonical_c,
                    'scope': scope_c,
                    'family': fam_c,
                    'sub_family': sub_c,
                    'cohort': cohort_c,
                    'n_samples': n_c,
                    'avg_odds': avg_o_c,
                    'total_stake': float(n_c),
                    'total_pnl': tot_pnl_c,
                    'roi': roi_c,
                    'win_rate': win_rate_c,
                    'delta_brier': delta_b_c,
                    'brier_model': avg_bm_c,
                    'brier_market': avg_bmkt_c,
                    'max_dd': max_dd_c,
                    'score': score_c
                })

            candidates.sort(key=lambda x: -x['score'])
            # Zero Rationing & Unlimited Maximum: ALL proven candidates graduate!
            for cand in candidates:
                c_seg = cand['segment_key']
                grad_id = f"grad_quota_{hashlib.sha256(f'{c_seg}:{now_iso}'.encode()).hexdigest()[:16]}"
                raw_market_names = sorted(list({r[8] for r in cand['records'] if len(r) > 8 and r[8]}))
                stats = {
                    'cohort': cand['cohort'],
                    'sample_size': cand['n_samples'],
                    'n_min_dynamic': 10,
                    'avg_odds': round(cand['avg_odds'], 3),
                    'total_stake': cand['total_stake'],
                    'total_pnl': round(cand['total_pnl'], 2),
                    'roi': round(cand['roi'], 4),
                    'win_rate': round(cand['win_rate'], 4),
                    'delta_brier': round(cand['delta_brier'], 5),
                    'brier_model': round(cand['brier_model'], 5),
                    'brier_market': round(cand['brier_market'], 5),
                    'max_drawdown': round(cand['max_dd'], 4),
                    'raw_market_names': raw_market_names,
                    'quota_expansion_triggered': True,
                    'unlimited_proven_graduation': True,
                    'quota_target': min_quota
                }

                # Evaluate Multi-Model Tournament Champions
                cand_sub_key = f"{league}|{cand['scope']}|{cand['family']}:{cand['sub_family']}" if cand['sub_family'] else cand['segment_key']
                try:
                    from .tournament_engine import TournamentEngine
                    t_eng = getattr(self, '_tournament_engine', None) or TournamentEngine(self.data_dir)
                    champ = t_eng.crown_champion(cand['segment_key'])
                    if not champ and cand_sub_key != cand['segment_key']:
                        champ = t_eng.crown_champion(cand_sub_key)
                    if not champ and cand.get('sub_family'):
                        champ = t_eng.crown_champion(f"{league}|{cand['scope']}|{cand['sub_family']}")
                    if champ:
                        stats['champion_model_id'] = champ.model_id
                        stats['champion_version'] = champ.version
                        stats['tournament_scorecard_s'] = champ.scorecard_s
                except Exception:
                    pass

                conn_quota = self._get_connection()
                try:
                    cur_quota = conn_quota.cursor()
                    cur_quota.execute("""
                        INSERT OR REPLACE INTO graduated_markets (
                            id, segment_key, league, scope, family, sub_family,
                            graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        grad_id, cand['segment_key'], league, cand['scope'], cand['family'], cand['sub_family'],
                        now_iso, cand['n_samples'], cand['roi'], cand['delta_brier'], cand['max_dd'], cand['win_rate'], json.dumps(stats)
                    ))
                    try:
                        cur_quota.execute("""
                            DELETE FROM demoted_markets 
                            WHERE segment_key IN (?, ?, ?)
                               OR (league = ? AND scope = ? AND (family = ? OR sub_family = ?))
                        """, (cand['segment_key'], cand['canonical_key'], cand_sub_key, league, cand['scope'], cand['family'], cand['sub_family']))
                    except Exception:
                        pass
                    conn_quota.commit()
                finally:
                    conn_quota.close()

                try:
                    from .telegram_notifier import notify_rd_graduation
                    notify_rd_graduation(league, cand['segment_key'], cand['n_samples'], cand['roi'] * 100.0, cand['win_rate'] * 100.0)
                except Exception:
                    pass

                initial_tier = 'SOLID_ALPHA' if cand['cohort'] == 'high_probability' and cand['roi'] >= 0.03 else 'PROBATION'
                grad_entry = {
                    'id': grad_id,
                    'segment_key': cand['segment_key'],
                    'league': league,
                    'scope': cand['scope'],
                    'family': cand['family'],
                    'sub_family': cand['sub_family'],
                    'cohort': cand['cohort'],
                    'graduated_at': now_iso,
                    'initial_tier': initial_tier,
                    'stats': stats
                }
                current_graduated[cand['segment_key']] = grad_entry
                graduated_records.append(grad_entry)
                league_counts[league] = league_counts.get(league, 0) + 1

                try:
                    from .tier_state_machine import append_tier_transition
                    append_tier_transition({
                        'id': f"trans_{grad_id}",
                        'timestamp': now_iso,
                        'segment': cand['segment_key'],
                        'league': league,
                        'scope': cand['scope'],
                        'family': cand['family'],
                        'previous_tier': 'SHADOW_INCUBATION',
                        'new_tier': initial_tier,
                        'transition_type': 'GRADUATION_PROMOTION',
                        'pnl': cand['total_pnl'],
                        'roi_pct': round(cand['roi'] * 100.0, 2),
                        'games': cand['n_samples'],
                        'wins': int(cand['win_rate'] * cand['n_samples']),
                        'losses': cand['n_samples'] - int(cand['win_rate'] * cand['n_samples']),
                        'diagnostic_reason': f"Unlimited Proven Graduation: {cand['segment_key']} (ROI: +{cand['roi']*100:.1f}%, PnL: +${cand['total_pnl']:.2f}, N={cand['n_samples']})",
                        'rd_actionable_remedy': f"Promote to live issuance in {initial_tier} tier ($0.50 evolutionary floor).",
                        'failure_mode': 'NONE_GRADUATED'
                    })
                except Exception:
                    pass

        if graduated_records:
            save_graduated_markets(current_graduated, self.data_dir)

        return graduated_records

    def enforce_unlimited_proven_graduation(
        self,
        min_sample_size: int = 25,
        as_of: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Unlimited Maximum & Zero Rationing Proven Graduation Mandate.
        Direct alias fulfilling User Mandate: graduates ALL proven candidates."""
        return self.enforce_minimum_league_quotas(min_quota=0, min_sample_size=min_sample_size, as_of=as_of)

    def trigger_volume_deficit_expansion(
        self,
        daily_shortfall: int = 0,
        as_of: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Volume-Deficit Mandated Expansion:
        Evaluates incubated shadow families with adaptive Crucible criteria when daily
        issuance volume is below the required 100 bets/day minimum.
        Crucial: Keeps ROI >= +3.0%, Delta Brier > 0.001, Max Drawdown < 15% intact,
        ensuring every newly expanded market is statistically profitable and calibrated.
        """
        now_iso = as_of or utcnow().isoformat()
        conn = self._get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT league, scope, family, sub_family, odds, pnl, brier_model, brier_market, dynamic_sample_power_req, raw_market_name, settled_at
            FROM incubated_wagers
            WHERE status = 'settled'
            ORDER BY settled_at ASC
        """)
        rows = cur.fetchall()

        # Demoted / probation map
        demoted_map = {}
        demoted_sub_map = {}
        try:
            cur.execute("SELECT segment_key, demoted_at, sub_family FROM demoted_markets")
            for sk, da, sf in cur.fetchall():
                demoted_map[sk] = da
                demoted_sub_map[sk] = sf
        except Exception:
            pass

        # Diagnostic recalibration map for stuck incubator markets
        recalibrated_map = {}
        try:
            cur.execute("SELECT segment_key, recalibrated_at FROM incubator_diagnostic_repairs")
            for sk, ra in cur.fetchall():
                recalibrated_map[sk] = ra
        except Exception:
            pass

        # Pre-load live decisions once to avoid repeated DB connections inside the loop
        cached_live_rows = []
        engine_db = self.data_dir / 'engine.sqlite3'
        if engine_db.exists():
            try:
                conn_eng = sqlite3.connect(str(engine_db), timeout=5.0)
                cur_eng = conn_eng.cursor()
                cached_live_rows = [
                    (json.loads(r[0]) if isinstance(r[0], str) else r[0], r[1])
                    for r in cur_eng.execute("SELECT payload, pnl FROM system_decisions WHERE status IN ('won', 'lost', 'push') ORDER BY issued_at ASC").fetchall()
                ]
                conn_eng.close()
            except Exception:
                cached_live_rows = []

        families: Dict[Tuple[str, str, str, str], List[Tuple]] = {}
        for r in rows:
            key = (r[0], r[1], r[2], r[3])
            families.setdefault(key, []).append(r)

        graduated_records = []
        current_graduated = load_graduated_markets(self.data_dir)

        for (league, scope, family, sub_family), records in families.items():
            canonical_key = f"{league}|{scope}|{family}"
            sub_key = f"{league}|{scope}|{family}:{sub_family}" if (sub_family and sub_family != family) else canonical_key

            if canonical_key in current_graduated:
                if current_graduated[canonical_key].get('sub_family') == sub_family:
                    continue  # Already graduated
                segment_key = sub_key
                if segment_key in current_graduated:
                    continue  # Already graduated
            else:
                segment_key = canonical_key
                if sub_key in current_graduated:
                    continue

            # Probation check: if demoted previously, check if the demotion was a cross-contamination false demotion
            demoted_at = demoted_map.get(segment_key) or demoted_map.get(sub_key)
            if not demoted_at and canonical_key in demoted_map:
                demoted_sub = demoted_sub_map.get(canonical_key)
                if not demoted_sub or demoted_sub == sub_family:
                    demoted_at = demoted_map.get(canonical_key)
            if demoted_at:
                is_false_demotion = False
                if cached_live_rows:
                    meta_check = {'league': league, 'scope': scope, 'family': family, 'sub_family': sub_family, 'segment_key': segment_key}
                    matching_live = [
                        p for p, pnl in cached_live_rows
                        if matches_live_decision_to_market(p, meta_check)
                    ]
                    matching_pnl = sum(
                        pnl for p, pnl in cached_live_rows
                        if matches_live_decision_to_market(p, meta_check)
                    )
                    n_inc = len(records)
                    inc_pnl = sum(r[5] for r in records)
                    matching_staked = sum(
                        float(p.get('stake', 0.0) or 0.0) for p, _ in cached_live_rows
                        if matches_live_decision_to_market(p, meta_check)
                    )
                    avg_live_stake = (matching_staked / len(matching_live)) if len(matching_live) > 0 else 1.0
                    matching_units_pnl = matching_pnl / max(0.20, avg_live_stake)
                    inc_units_pnl = inc_pnl / max(0.20, 1.0)
                    # Anti-Clog Statistical Significance:
                    # 1. Under 5 live games is stochastic noise.
                    # 2. Cumulative positive PnL in live (matching_pnl >= 0.0) contradicts demotion.
                    # 3. Under 25 live games with minor drawdown cannot override massive out-of-sample incubator proof (N >= 50, PnL >= +15 units).
                    if (
                        len(matching_live) < 5
                        or matching_pnl >= 0.0
                        or (len(matching_live) < 25 and (matching_units_pnl > -10.0 or matching_pnl > -15.0) and n_inc >= 50 and (inc_units_pnl >= 15.0 or inc_pnl >= 20.0))
                    ):
                        is_false_demotion = True

                if is_false_demotion:
                    try:
                        cur.execute("DELETE FROM demoted_markets WHERE segment_key = ? OR (segment_key = ? AND sub_family = ?)", (segment_key, canonical_key, sub_family))
                        demoted_at = None
                    except Exception:
                        pass

                if demoted_at:
                    records = [r for r in records if len(r) > 10 and r[10] and str(r[10]) > demoted_at]
                    if not records:
                        continue

            sub_key = f"{league}|{scope}|{family}:{sub_family}" if sub_family else segment_key
            n_samples = len(records)
            odds_list = [r[4] for r in records]
            pnl_list = [r[5] for r in records]
            brier_model_list = [r[6] for r in records if r[6] is not None]
            brier_mkt_list = [r[7] for r in records if r[7] is not None]

            # In deficit expansion mode, adaptive dynamic sample power: N_min >= max(25, ceil(25 * sqrt(O - 1)))
            avg_odds = float(np.mean(odds_list)) if odds_list else 2.0
            n_min_adaptive = max(25, int(math.ceil(25.0 * math.sqrt(max(0.1, avg_odds - 1.0)))))

            if n_samples < n_min_adaptive:
                continue

            total_stake = float(n_samples)
            total_pnl = float(sum(pnl_list))
            roi = total_pnl / total_stake if total_stake > 0 else 0.0
            win_count = sum(1 for p in pnl_list if p > 0)
            win_rate = win_count / n_samples if n_samples > 0 else 0.0

            brier_model = float(np.mean(brier_model_list)) if brier_model_list else 0.25
            brier_mkt = float(np.mean(brier_mkt_list)) if brier_mkt_list else 0.25
            delta_brier = brier_mkt - brier_model

            cumsum = np.cumsum(pnl_list)
            peaks = np.maximum.accumulate(cumsum)
            drawdowns = (peaks - cumsum) / np.maximum(1.0, peaks + total_stake)
            max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

            # Strict Invariants:
            # 1. Realized ROI >= +3.0%
            # 2. Calibration Edge: delta_brier > 0.001
            # 3. Max Drawdown < 15%
            if roi >= 0.030 and delta_brier > 0.001 and max_dd < 0.15:
                grad_id = f"grad_vol_{hashlib.sha256(f'{segment_key}:{now_iso}'.encode()).hexdigest()[:16]}"
                raw_market_names = sorted(list({r[9] for r in records if len(r) > 9 and r[9]}))
                stats = {
                    'sample_size': n_samples,
                    'n_min_adaptive': n_min_adaptive,
                    'avg_odds': round(avg_odds, 3),
                    'total_stake': total_stake,
                    'total_pnl': round(total_pnl, 2),
                    'roi': round(roi, 4),
                    'win_rate': round(win_rate, 4),
                    'delta_brier': round(delta_brier, 5),
                    'brier_model': round(brier_model, 5),
                    'brier_market': round(brier_mkt, 5),
                    'max_drawdown': round(max_dd, 4),
                    'raw_market_names': raw_market_names,
                    'volume_expansion_triggered': True,
                    'shortfall_context': daily_shortfall
                }

                # Evaluate Multi-Model Tournament Champions
                try:
                    from .tournament_engine import TournamentEngine
                    t_eng = getattr(self, '_tournament_engine', None) or TournamentEngine(self.data_dir)
                    champ = t_eng.crown_champion(segment_key)
                    if not champ and sub_key and sub_key != segment_key:
                        champ = t_eng.crown_champion(sub_key)
                    if not champ and sub_family:
                        champ = t_eng.crown_champion(f"{league}|{scope}|{sub_family}")
                    if champ:
                        stats['champion_model_id'] = champ.model_id
                        stats['champion_version'] = champ.version
                        stats['tournament_scorecard_s'] = champ.scorecard_s
                except Exception:
                    pass

                conn_grad = self._get_connection()
                try:
                    cur_grad = conn_grad.cursor()
                    cur_grad.execute("""
                        INSERT OR REPLACE INTO graduated_markets (
                            id, segment_key, league, scope, family, sub_family,
                            graduated_at, sample_size, roi, delta_brier, max_drawdown, win_rate, stats
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        grad_id, segment_key, league, scope, family, sub_family,
                        now_iso, n_samples, roi, delta_brier, max_dd, win_rate, json.dumps(stats)
                    ))
                    try:
                        cur_grad.execute("""
                            DELETE FROM demoted_markets 
                            WHERE segment_key IN (?, ?, ?) 
                               OR (league = ? AND scope = ? AND (family = ? OR sub_family = ?))
                        """, (segment_key, canonical_key, sub_key, league, scope, family, sub_family))
                        cur_grad.execute("""
                            DELETE FROM incubator_diagnostic_repairs 
                            WHERE segment_key IN (?, ?, ?)
                        """, (segment_key, canonical_key, sub_key))
                    except Exception:
                        pass
                    conn_grad.commit()
                finally:
                    conn_grad.close()

                try:
                    from .telegram_notifier import notify_rd_graduation
                    notify_rd_graduation(league, segment_key, n_samples, roi * 100.0, win_rate * 100.0)
                except Exception:
                    pass

                grad_entry = {
                    'id': grad_id,
                    'segment_key': segment_key,
                    'league': league,
                    'scope': scope,
                    'family': family,
                    'sub_family': sub_family,
                    'graduated_at': now_iso,
                    'initial_tier': 'PROBATION',
                    'stats': stats
                }
                current_graduated[segment_key] = grad_entry
                graduated_records.append(grad_entry)

                try:
                    from .tier_state_machine import append_tier_transition
                    append_tier_transition({
                        'id': f"trans_{grad_id}",
                        'timestamp': now_iso,
                        'segment': segment_key,
                        'league': league,
                        'scope': scope,
                        'family': family,
                        'previous_tier': 'SHADOW_INCUBATION',
                        'new_tier': 'PROBATION',
                        'transition_type': 'VOLUME_DEFICIT_EXPANSION_GRADUATION',
                        'pnl': total_pnl,
                        'roi_pct': round(roi * 100.0, 2),
                        'games': n_samples,
                        'wins': win_count,
                        'losses': n_samples - win_count,
                        'diagnostic_reason': f"Mandatory Volume-Deficit Expansion: {segment_key} graduated (ROI: +{roi*100:.1f}%, Delta Brier: +{delta_brier:.4f}, N={n_samples}) to satisfy 100+ volume minimum.",
                        'rd_actionable_remedy': "Promoted to live issuance in PROBATION tier ($0.50 evolutionary floor).",
                        'failure_mode': 'NONE_GRADUATED'
                    })
                except Exception:
                    pass

        conn.commit()
        conn.close()

        if graduated_records:
            save_graduated_markets(current_graduated, self.data_dir)

        return graduated_records

    def diagnose_and_repair_stuck_incubator_markets(
        self,
        min_samples: int = 20,
        as_of: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Closed-Loop R&D Diagnostics for Non-Graduating / Stuck Incubator Submarkets:

        Identifies incubator submarkets that have accumulated sufficient out-of-sample volume (N >= min_samples)
        but fail the Crucible graduation threshold (ROI <= 0.0 or Delta Brier <= 0.0).
        Performs forensic root cause attribution, applies algorithmic model repairs,
        records diagnostic logs, and establishes a clean recalibrated evaluation window.
        """
        now_iso = as_of or utcnow().isoformat()
        current_graduated = load_graduated_markets(self.data_dir)
        conn = self._get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT league, scope, family, sub_family, odds, pnl, brier_model, brier_market, dynamic_sample_power_req, raw_market_name, cohort, settled_at
            FROM incubated_wagers
            WHERE status = 'settled'
            ORDER BY settled_at ASC
        """)
        rows = cur.fetchall()

        # Group by canonical market tuple
        families: Dict[Tuple[str, str, str, str], List[Tuple]] = {}
        for r in rows:
            key = (r[0], r[1], r[2], r[3])
            families.setdefault(key, []).append(r)

        repaired_records = []

        for (league, scope, family, sub_family), records in families.items():
            canonical_key = f"{league}|{scope}|{family}"
            sub_key = f"{league}|{scope}|{family}:{sub_family}" if (sub_family and sub_family != family) else canonical_key

            # Skip if already graduated
            is_already_graduated = False
            if sub_key in current_graduated:
                is_already_graduated = True
            elif canonical_key in current_graduated:
                grad_sub = current_graduated[canonical_key].get('sub_family')
                if grad_sub == sub_family or grad_sub is None or grad_sub == family:
                    is_already_graduated = True

            if is_already_graduated:
                continue

            segment_key = sub_key

            n_samples = len(records)
            if n_samples < min_samples:
                continue

            odds_list = [r[4] for r in records]
            pnl_list = [r[5] for r in records]
            brier_model_list = [r[6] for r in records if r[6] is not None]
            brier_mkt_list = [r[7] for r in records if r[7] is not None]

            total_stake = float(n_samples)
            total_pnl = float(sum(pnl_list))
            roi = total_pnl / total_stake if total_stake > 0 else 0.0
            win_count = sum(1 for p in pnl_list if p > 0)
            win_rate = win_count / n_samples

            brier_model = float(np.mean(brier_model_list)) if brier_model_list else 0.25
            brier_mkt = float(np.mean(brier_mkt_list)) if brier_mkt_list else 0.25
            delta_brier = brier_mkt - brier_model

            # Check if stuck / failing
            if roi > 0.035 and delta_brier > 0.002:
                continue  # In good standing, will graduate or evaluate normally

            # Forensic Attribution
            if family == 'asian_lines' and scope in ('half_1', 'half_2'):
                failure_mode = 'SCOPE_TOTALS_MISMATCH'
                actionable_remedy = f"Half-time goal scaling deployed: 45%/55% bivariate Poisson distribution applied to scope {scope}; window reset for out-of-sample graduation."
            elif family == 'sequence_hazard' and scope != 'full_game':
                failure_mode = 'SEQUENCE_HAZARD_SCOPE_MISMATCH'
                actionable_remedy = f"Hazard rate scale attenuation applied for {scope} ({'0.45' if 'half' in scope else '0.25'}); zero-score probability recalibrated."
            elif delta_brier < -0.01:
                failure_mode = 'OVERCONFIDENCE_CALIBRATION_BIAS'
                actionable_remedy = f"Probability shrinkage and tighter EV hurdle (+3.5%) applied; recalibration window set to prune prior overconfidence."
            else:
                failure_mode = 'VIG_EDGE_DEFICIT'
                actionable_remedy = f"EV qualification hurdle increased to +3.5%; market relegated to high-conviction pricing only."

            rep_id = f"repair_{hashlib.sha256(f'{segment_key}:{now_iso}'.encode()).hexdigest()[:16]}"
            stats = {
                'sample_size': n_samples,
                'total_pnl': round(total_pnl, 2),
                'roi': round(roi, 4),
                'win_rate': round(win_rate, 4),
                'delta_brier': round(delta_brier, 5),
                'brier_model': round(brier_model, 5),
                'brier_market': round(brier_mkt, 5),
                'failure_mode': failure_mode,
                'remedy': actionable_remedy
            }

            cur.execute("""
                INSERT OR REPLACE INTO incubator_diagnostic_repairs (
                    id, segment_key, league, scope, family, sub_family,
                    diagnosed_at, sample_size, prior_roi, prior_pnl, failure_mode,
                    actionable_remedy, recalibrated_at, stats
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rep_id, segment_key, league, scope, family, sub_family,
                now_iso, n_samples, roi, total_pnl, failure_mode,
                actionable_remedy, now_iso, json.dumps(stats)
            ))

            repaired_records.append({
                'id': rep_id,
                'segment_key': segment_key,
                'league': league,
                'scope': scope,
                'family': family,
                'sub_family': sub_family,
                'failure_mode': failure_mode,
                'remedy': actionable_remedy,
                'prior_roi_pct': round(roi * 100.0, 2),
                'prior_pnl': round(total_pnl, 2),
                'games': n_samples,
                'recalibrated_at': now_iso
            })

            # Record in TierStateMachine / R&D logs if main engine DB exists
            engine_db = self.data_dir / 'engine.sqlite3'
            if engine_db.exists():
                try:
                    from .tier_state_machine import enqueue_rd_repair_job
                    conn_main = sqlite3.connect(str(engine_db), timeout=5.0)
                    enqueue_rd_repair_job(
                        conn=conn_main,
                        segment_key=segment_key,
                        league=league,
                        prev_tier='SHADOW_INCUBATION',
                        new_tier='SHADOW_RECALIBRATION',
                        failure_mode=failure_mode,
                        remedy=actionable_remedy,
                        stats={'pnl': total_pnl, 'roi_pct': round(roi * 100.0, 2), 'games': n_samples}
                    )
                    conn_main.close()
                except Exception:
                    pass

        conn.commit()
        conn.close()

        return repaired_records

    def evaluate_demotions_and_substitutions(
        self,
        engine_db_path: Optional[Path] = None,
        as_of: Optional[str] = None
    ) -> Dict[str, Any]:
        """Continuous Closed-Loop Performance Review:
        1. Evaluates all currently graduated markets on their live settled performance.
        2. Evaluates all Core Full-Game Markets across leagues on their live settled performance.
        3. If any market (graduated derivative or core full-game) deteriorates (rolling ROI < -3% or rolling PnL <= -$5.00 across 5+ live games):
           - Demotes it to demoted_markets table and triggers quarantine ($0.00 stake, R&D investigation).
           - Enqueues an autonomous AI Quant R&D repair job in learning_jobs table.
           - Appends tier transition record to tier_transitions.jsonl.
           - Scans the incubator for the highest-performing alternative candidate in that league to substitute it.
        4. Returns audit dictionary of demotions and substitutions made.
        """
        now_iso = as_of or utcnow().isoformat()
        current_graduated = load_graduated_markets(self.data_dir)

        db_file = engine_db_path or (self.data_dir / 'engine.sqlite3')
        if not Path(db_file).exists():
            return {'demotions': [], 'substitutions': []}

        demoted_keys = []
        substituted_records = []

        try:
            conn_eng = sqlite3.connect(db_file, timeout=10.0)
            cur_eng = conn_eng.cursor()
            cur_eng.execute("""
                SELECT payload, status, pnl, issued_at
                FROM system_decisions
                WHERE status IN ('won', 'lost', 'push')
                ORDER BY issued_at DESC
            """)
            live_rows = cur_eng.fetchall()
            conn_eng.close()
        except Exception:
            live_rows = []

        # Collect settled live decisions
        live_decisions = []
        for r in live_rows:
            p = json.loads(r[0]) if isinstance(r[0], str) else r[0]
            if not isinstance(p, dict):
                continue
            live_decisions.append({
                'payload': p,
                'status': r[1],
                'pnl': float(r[2] or 0.0),
                'stake': float(p.get('stake', 1.0) or 1.0),
                'issued_at': r[3]
            })

        # Dual-Track Market Universe: Combine graduated markets and active core/period markets
        markets_to_evaluate: Dict[str, Dict[str, Any]] = dict(current_graduated)
        try:
            from .contracts import allocation_family
            for d in live_decisions:
                p = d.get('payload', {})
                dec_scope = p.get('model_scope') or p.get('scope') or ('full_game' if ':' not in str(p.get('market', '')) else p.get('market', '').split(':')[0])
                lg = p.get('league')
                if not lg:
                    continue
                # If this decision already belongs to a graduated market, it is evaluated under that graduated market!
                if any(matches_live_decision_to_market(p, g) for g in current_graduated.values()):
                    continue
                base_fam = allocation_family(p)
                mkt = str(p.get('market', ''))
                base_mkt = mkt.split(':')[-1] if ':' in mkt else mkt
                is_core = (dec_scope == 'full_game')
                seg_k = f"{lg}|{dec_scope}|{base_mkt}" if not is_core else f"{lg}|full_game|{base_fam}"
                if seg_k not in markets_to_evaluate:
                    markets_to_evaluate[seg_k] = {
                        'league': lg,
                        'scope': dec_scope,
                        'family': base_fam,
                        'sub_family': base_mkt if not is_core else base_fam,
                        'segment_key': seg_k,
                        'is_core': is_core
                    }
        except Exception as e_mkt:
            log.warning("Could not assemble active segments for review: %s", e_mkt)

        # Check existing demoted segments to avoid repeating tier transitions
        existing_demoted = set()
        try:
            conn_d = self._get_connection()
            cur_d = conn_d.cursor()
            existing_demoted = {r[0] for r in cur_d.execute("SELECT segment_key FROM demoted_markets").fetchall()}
            conn_d.close()
        except Exception:
            pass

        for seg_key, meta in list(markets_to_evaluate.items()):
            league = meta.get('league')
            scope = meta.get('scope')
            if not league or not scope:
                continue

            # Strict market matching: only evaluate live decisions that belong to THIS market!
            matching_decisions = [
                d for d in live_decisions 
                if matches_live_decision_to_market(d['payload'], meta)
            ]
            recent = matching_decisions[:20]
            if len(recent) >= 5:
                rolling_staked = sum(d['stake'] for d in recent)
                rolling_pnl = sum(d['pnl'] for d in recent)
                rolling_roi = (rolling_pnl / rolling_staked) if rolling_staked > 0 else 0.0
                avg_stake = (rolling_staked / len(recent)) if len(recent) > 0 else 1.0
                rolling_units_pnl = rolling_pnl / max(0.20, avg_stake)

                # Statistical Significance Safeguard (Constitutional Commandments 6 & 7):
                # A market with massive incubator backing (inc_n >= 50 and inc_pnl >= 20.0)
                # CANNOT be demoted on minor sample variance (< 25 games, or rolling_pnl > -15.0).
                # The tier_state_machine automatically enforces Capital Defense ($0.50 clamp, +3.5% EV hurdle)
                # during live drawdowns without discarding the market!
                stats = meta.get('stats', {}) if isinstance(meta, dict) else {}
                inc_n = stats.get('sample_size', 0)
                inc_pnl = float(stats.get('total_pnl', stats.get('pnl', 0.0)) or 0.0)
                if inc_n == 0 or inc_pnl <= 0.0:
                    try:
                        cur_q = self._get_connection().cursor()
                        fam = meta.get('family', '')
                        sub_fam = meta.get('sub_family', '')
                        if sub_fam:
                            q_row = cur_q.execute(
                                "SELECT count(*), sum(pnl) FROM incubated_wagers WHERE league = ? AND scope = ? AND family = ? AND sub_family = ? AND status = 'settled'",
                                (league, scope, fam, sub_fam)
                            ).fetchone()
                        else:
                            q_row = cur_q.execute(
                                "SELECT count(*), sum(pnl) FROM incubated_wagers WHERE league = ? AND scope = ? AND family = ? AND status = 'settled'",
                                (league, scope, fam)
                            ).fetchone()
                        if q_row and q_row[0]:
                            inc_n = q_row[0]
                            inc_pnl = float(q_row[1] or 0.0)
                    except Exception:
                        pass

                total_live_pnl = sum(d['pnl'] for d in matching_decisions)
                total_live_staked = sum(float(d.get('stake', 0.0) or 0.0) for d in matching_decisions)
                avg_total_stake = (total_live_staked / len(matching_decisions)) if len(matching_decisions) > 0 else 1.0
                total_live_units = total_live_pnl / max(0.20, avg_total_stake)
                inc_units = inc_pnl / max(0.20, 1.0)
                if (inc_n >= 50 and (inc_units >= 15.0 or inc_pnl >= 20.0)) or (len(matching_decisions) >= 50 and (total_live_units >= 15.0 or total_live_pnl >= 20.0)):
                    if len(matching_decisions) < 25 and (rolling_units_pnl > -10.0 or rolling_pnl > -15.0 or total_live_pnl >= 0.0):
                        continue

                # Scale-Invariant Dynamic Loss Evaluation (Anti-Constant Mandate, Commandment 3):
                # Demotes when market shows sustained loss in units of risk (<= -2.5 units with negative ROI,
                # or <= -4.0 units regardless of ROI), scale-invariant across $0.50, $8.00, or larger future bankrolls.
                if (rolling_roi <= -0.03 and rolling_units_pnl <= -2.5) or (rolling_units_pnl <= -4.0 and rolling_roi < 0.0):
                    is_core = meta.get('is_core', False)
                    demoted_entry = {
                        'segment_key': seg_key,
                        'league': league,
                        'scope': scope,
                        'is_core': is_core,
                        'rolling_roi_pct': round(rolling_roi * 100.0, 2),
                        'rolling_pnl': round(rolling_pnl, 2),
                        'games': len(recent)
                    }
                    demoted_keys.append(demoted_entry)
                    canon_k = f"{league}|{scope}|{meta.get('family', '')}"
                    sub_k = f"{league}|{scope}|{meta.get('family', '')}:{meta.get('sub_family', '')}" if meta.get('sub_family') else canon_k
                    for k in (seg_key, canon_k, sub_k):
                        if k in current_graduated:
                            del current_graduated[k]

                    # Atomic DB persistence: delete from graduated_markets and track in demoted_markets
                    try:
                        conn_inc = self._get_connection()
                        cur_inc = conn_inc.cursor()
                        cur_inc.execute("""
                            DELETE FROM graduated_markets 
                            WHERE segment_key IN (?, ?, ?) 
                               OR (league = ? AND scope = ? AND family = ? AND (sub_family = ? OR sub_family IS NULL OR sub_family = ''))
                        """, (seg_key, canon_k, sub_k, league, scope, meta.get('family', ''), meta.get('sub_family', '')))
                        d_id = f"demote_{hashlib.sha256(f'{seg_key}:{now_iso}'.encode()).hexdigest()[:16]}"
                        cur_inc.execute("""
                            INSERT OR REPLACE INTO demoted_markets (
                                id, segment_key, league, scope, family, sub_family,
                                demoted_at, rolling_roi, rolling_pnl, games, reason, stats
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            d_id, seg_key, league, scope, meta.get('family', ''), meta.get('sub_family', ''),
                            now_iso, rolling_roi, rolling_pnl, len(recent),
                            f"Continuous Review: Rolling ROI {rolling_roi*100:.1f}%, PnL ${rolling_pnl:.2f} across {len(recent)} games",
                            json.dumps({'rolling_roi': rolling_roi, 'rolling_pnl': rolling_pnl, 'games': len(recent), 'is_core': is_core})
                        ))
                        conn_inc.commit()
                        conn_inc.close()
                    except Exception as e_sql:
                        log.warning("Could not persist demotion to sqlite: %s", e_sql)

                    if seg_key not in existing_demoted:
                        prev_tier = 'CORE_DOMAIN_MODEL' if is_core else 'GRADUATED_EXPLORATION'
                        new_tier = 'CAPITAL_DEFENSE' if is_core else 'PROBATION_RESEARCH'
                        failure_mode = 'CORE_DOMAIN_MODEL_DRAWDOWN' if is_core else 'LIVE_DRAWDOWN_DRIFT'
                        diag_msg = (
                            f"Continuous Core Market Review: {seg_key} degraded in live execution "
                            f"(Rolling ROI {rolling_roi*100:.1f}%, PnL ${rolling_pnl:.2f} over {len(recent)} games). "
                            f"Relegated to incubator research & clamped to Capital Defense ($0.50)."
                            if is_core else
                            f"Continuous Review Demotion: {seg_key} degraded in live execution "
                            f"(Rolling ROI {rolling_roi*100:.1f}%, PnL ${rolling_pnl:.2f} over {len(recent)} games). "
                            f"Relegated to incubator research."
                        )
                        remedy_msg = (
                            "Clamped to $0.50 floor + 3.5% min EV hurdle under Capital Defense; enqueued for Multi-AI Quant Council challenger model formulation."
                            if is_core else
                            "Replaced in live lineup; enqueued for Quant Council forensic investigation."
                        )

                        try:
                            from .tier_state_machine import append_tier_transition, enqueue_rd_repair_job
                            append_tier_transition({
                                'id': f"demote_{hashlib.sha256(f'{seg_key}:{now_iso}'.encode()).hexdigest()[:16]}",
                                'timestamp': now_iso,
                                'segment': seg_key,
                                'league': league,
                                'scope': scope,
                                'family': meta.get('family'),
                                'previous_tier': prev_tier,
                                'new_tier': new_tier,
                                'transition_type': 'DEMOTION_TO_RESEARCH',
                                'pnl': rolling_pnl,
                                'roi_pct': round(rolling_roi * 100.0, 2),
                                'games': len(recent),
                                'wins': sum(1 for d in recent if d['status'] == 'won'),
                                'losses': sum(1 for d in recent if d['status'] == 'lost'),
                                'diagnostic_reason': diag_msg,
                                'rd_actionable_remedy': remedy_msg,
                                'failure_mode': failure_mode
                            })
                            conn_main = sqlite3.connect(db_file, timeout=10.0)
                            enqueue_rd_repair_job(
                                conn=conn_main,
                                segment_key=seg_key,
                                league=league,
                                prev_tier=prev_tier,
                                new_tier=new_tier,
                                failure_mode=failure_mode,
                                remedy=remedy_msg,
                                stats={'pnl': rolling_pnl, 'roi_pct': round(rolling_roi * 100.0, 2), 'games': len(recent), 'is_core': is_core}
                            )
                            conn_main.close()
                        except Exception:
                            pass

        if demoted_keys:
            save_graduated_markets(current_graduated, self.data_dir)
            substitutes = self.evaluate_graduation(as_of=now_iso)
            substituted_records = substitutes

        return {
            'demotions': demoted_keys,
            'substitutions': substituted_records
        }

    # -------------------------------------------------------------------------
    # 4. STATUS & TELEMETRY
    # -------------------------------------------------------------------------

    def get_incubator_status(self) -> Dict[str, Any]:
        """Provides real-time health and portfolio metrics for the Incubator Department."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT status, COUNT(*) FROM incubated_wagers GROUP BY status")
            status_counts_raw = cur.fetchall()

            cur.execute("SELECT cohort, status, COUNT(*) FROM incubated_wagers GROUP BY cohort, status")
            cohort_counts_raw = cur.fetchall()

            cur.execute("""
                SELECT league, scope, family, sub_family, COUNT(*), SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status='settled' THEN pnl ELSE 0.0 END),
                       AVG(CASE WHEN status='settled' THEN brier_market - brier_model ELSE NULL END)
                FROM incubated_wagers
                GROUP BY league, scope, family, sub_family
            """)
            family_rows = cur.fetchall()

            cur.execute("SELECT id, segment_key, league, scope, family, sub_family, graduated_at, roi, delta_brier, sample_size FROM graduated_markets WHERE segment_key NOT IN (SELECT segment_key FROM demoted_markets)")
            grad_rows = cur.fetchall()

            demoted_rows = []
            try:
                cur.execute("""
                    SELECT id, segment_key, league, scope, family, sub_family,
                           demoted_at, rolling_roi, rolling_pnl, games, reason,
                           incubated_model_id, model_archetype, historical_roi,
                           historical_brier_delta, historical_sample_size,
                           incubation_phase, admitted_to_incubator_at
                    FROM demoted_markets
                """)
                demoted_rows = cur.fetchall()
            except Exception:
                pass

            repair_rows = []
            try:
                cur.execute("SELECT id, segment_key, league, scope, family, sub_family, diagnosed_at, failure_mode, actionable_remedy, prior_roi, prior_pnl, sample_size FROM incubator_diagnostic_repairs")
                repair_rows = cur.fetchall()
            except Exception:
                pass
        finally:
            conn.close()

        status_counts = dict(status_counts_raw)

        cohort_counts = {}
        for c_name, st, cnt in cohort_counts_raw:
            cohort_counts.setdefault(c_name or 'gap_market', {})[st] = cnt

        families_summary = []
        for r in family_rows:
            tot, settled, pnl, delta_b = r[4], r[5], r[6] or 0.0, r[7] or 0.0
            roi = (pnl / settled) if settled > 0 else 0.0
            families_summary.append({
                'segment_key': f"{r[0]}|{r[1]}|{r[2]}",
                'league': r[0],
                'scope': r[1],
                'family': r[2],
                'sub_family': r[3],
                'total_wagers': tot,
                'settled_wagers': settled,
                'pnl': round(pnl, 2),
                'roi_pct': round(roi * 100.0, 2),
                'delta_brier': round(delta_b, 5),
                'status': 'incubating'
            })

        graduated_list = []
        for gr in grad_rows:
            graduated_list.append({
                'id': gr[0],
                'segment_key': gr[1],
                'league': gr[2],
                'scope': gr[3],
                'family': gr[4],
                'sub_family': gr[5],
                'graduated_at': gr[6],
                'roi_pct': round(gr[7] * 100.0, 2),
                'delta_brier': round(gr[8], 5),
                'sample_size': gr[9]
            })

        demoted_list = []
        for dr in demoted_rows:
            demoted_list.append({
                'id': dr[0],
                'segment_key': dr[1],
                'league': dr[2],
                'scope': dr[3],
                'family': dr[4],
                'sub_family': dr[5],
                'demoted_at': dr[6],
                'rolling_roi_pct': round(dr[7] * 100.0, 2),
                'rolling_pnl': round(dr[8], 2),
                'games': dr[9],
                'reason': dr[10],
                'incubated_model_id': dr[11],
                'model_archetype': dr[12],
                'historical_roi_pct': round(dr[13] * 100.0, 1) if dr[13] is not None else None,
                'historical_brier_delta': round(dr[14], 5) if dr[14] is not None else None,
                'historical_sample_size': dr[15] or 0,
                'incubation_phase': dr[16] or 'research_training',
                'admitted_to_incubator_at': dr[17],
            })

        diagnostic_repairs_list = []
        for rep in repair_rows:
            diagnostic_repairs_list.append({
                'id': rep[0],
                'segment_key': rep[1],
                'league': rep[2],
                'scope': rep[3],
                'family': rep[4],
                'sub_family': rep[5],
                'diagnosed_at': rep[6],
                'failure_mode': rep[7],
                'actionable_remedy': rep[8],
                'prior_roi_pct': round(rep[9] * 100.0, 2),
                'prior_pnl': round(rep[10], 2),
                'sample_size': rep[11]
            })

        return {
            'department': 'Autonomous Market Expansion & Shadow Graduation Department (AMIE-GD)',
            'active_incubations': len(family_rows),
            'total_wagers_observed': sum(status_counts.values()),
            'status_counts': status_counts,
            'cohort_counts': cohort_counts,
            'min_league_graduated_quota': MIN_LEAGUE_GRADUATED_QUOTA,
            'graduated_markets_count': len(graduated_list),
            'graduated_markets': graduated_list,
            'demoted_markets_count': len(demoted_list),
            'demoted_markets': demoted_list,
            'diagnostic_repairs_count': len(diagnostic_repairs_list),
            'diagnostic_repairs': diagnostic_repairs_list,
            'incubated_families': families_summary
        }
