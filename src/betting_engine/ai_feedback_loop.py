"""Multi-AI Synthesis Reinforcement Feedback Loop (RLQS) Engine.

Maintains an immutable, persistent memory of synthesized candidate models,
their historical Walk-Forward Crucible metrics, and real-time live settlement
telemetry. Injects few-shot quantitative context (successful exemplars and
failed counter-examples) into future AI model synthesis prompts.

Mandatory League Priority Hierarchy:
1. NBA & NFL (Primary US liquid leagues)
2. NHL (High cadence, deep liquidity)
3. MLB & College Sports (NCAAF, NCAAM, NCAAW)
4. Tennis (ATP, WTA)
5. Soccer (MLS, EPL, UCL)
"""
from __future__ import annotations
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from .providers import DATA

log = logging.getLogger(__name__)
DEFAULT_INCUBATOR_DB = DATA / "shadow_incubator.sqlite3"


LEAGUE_PRIORITY_MAP: Dict[str, int] = {
    'NBA': 0,
    'NFL': 0,
    'NHL': 1,
    'MLB': 2,
    'NCAAF': 2,
    'NCAAM': 2,
    'NCAAW': 2,
    'ATP': 3,
    'WTA': 3,
    'CHALLENGER': 3,
    'MLS': 4,
    'EPL': 4,
    'UCL': 4,
}


def get_league_priority_weight(league: str) -> int:
    """Returns numerical sort rank for league: 0 for NBA/NFL, 1 for NHL, etc."""
    return LEAGUE_PRIORITY_MAP.get(league.upper(), 5)


@dataclass
class AIFeedbackRecord:
    id: str
    segment_key: str
    league: str
    scope: str
    family: str
    model_id: str
    class_name: str
    generator_provider: str
    source_file_path: str
    claude_hypothesis: Optional[str] = None
    codex_rationale: Optional[str] = None
    grok_critique: Optional[str] = None
    oos_sample_size: int = 0
    oos_win_rate: float = 0.0
    oos_simulated_roi: float = 0.0
    oos_delta_brier: float = 0.0
    oos_max_drawdown: float = 0.0
    crucible_passed: bool = False
    current_tier: int = 2
    live_games_settled: int = 0
    live_wins: int = 0
    live_losses: int = 0
    live_pushes: int = 0
    live_win_rate: float = 0.0
    live_realized_roi: float = 0.0
    live_realized_pnl: float = 0.0
    live_brier_delta: float = 0.0
    live_max_drawdown: float = 0.0
    graduation_status: str = "incubating"
    learned_insight: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AIFeedbackLoopManager:
    """Orchestrates reinforcement feedback tracking and prompt context generation for model synthesis."""

    def __init__(self, db_path: Path | str = DEFAULT_INCUBATOR_DB):
        p = Path(db_path) if isinstance(db_path, str) else db_path
        if p.is_dir():
            p = p / "shadow_incubator.sqlite3"
        self.db_path = p
        self._ensure_schema()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Idempotently creates feedback table and index."""
        try:
            with closing(self._get_connection()) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ai_synthesis_feedback_loop (
                        id TEXT PRIMARY KEY,
                        segment_key TEXT NOT NULL,
                        league TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        family TEXT NOT NULL,
                        model_id TEXT NOT NULL UNIQUE,
                        class_name TEXT NOT NULL,
                        generator_provider TEXT NOT NULL,
                        source_file_path TEXT NOT NULL,
                        claude_hypothesis TEXT,
                        codex_rationale TEXT,
                        grok_critique TEXT,
                        oos_sample_size INT DEFAULT 0,
                        oos_win_rate REAL DEFAULT 0.0,
                        oos_simulated_roi REAL DEFAULT 0.0,
                        oos_delta_brier REAL DEFAULT 0.0,
                        oos_max_drawdown REAL DEFAULT 0.0,
                        crucible_passed INT DEFAULT 0,
                        current_tier INT DEFAULT 2,
                        live_games_settled INT DEFAULT 0,
                        live_wins INT DEFAULT 0,
                        live_losses INT DEFAULT 0,
                        live_pushes INT DEFAULT 0,
                        live_win_rate REAL DEFAULT 0.0,
                        live_realized_roi REAL DEFAULT 0.0,
                        live_realized_pnl REAL DEFAULT 0.0,
                        live_brier_delta REAL DEFAULT 0.0,
                        live_max_drawdown REAL DEFAULT 0.0,
                        graduation_status TEXT DEFAULT 'incubating',
                        learned_insight TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ai_fb_league_fam 
                    ON ai_synthesis_feedback_loop(league, family)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ai_fb_model_id 
                    ON ai_synthesis_feedback_loop(model_id)
                """)
                conn.commit()
        except Exception as e:
            log.warning("Could not initialize ai_synthesis_feedback_loop table: %s", e)

    def record_synthesis_attempt(self, record: AIFeedbackRecord) -> bool:
        """Stores or updates synthesis record with historical Crucible results."""
        now_iso = datetime.now(timezone.utc).isoformat()
        for attempt in range(8):
            try:
                with closing(self._get_connection()) as conn:
                    conn.execute("""
                        INSERT INTO ai_synthesis_feedback_loop (
                            id, segment_key, league, scope, family, model_id, class_name,
                            generator_provider, source_file_path, claude_hypothesis,
                            codex_rationale, grok_critique, oos_sample_size, oos_win_rate,
                            oos_simulated_roi, oos_delta_brier, oos_max_drawdown,
                            crucible_passed, current_tier, graduation_status,
                            learned_insight, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(model_id) DO UPDATE SET
                            oos_sample_size = excluded.oos_sample_size,
                            oos_win_rate = excluded.oos_win_rate,
                            oos_simulated_roi = excluded.oos_simulated_roi,
                            oos_delta_brier = excluded.oos_delta_brier,
                            oos_max_drawdown = excluded.oos_max_drawdown,
                            crucible_passed = excluded.crucible_passed,
                            updated_at = excluded.updated_at
                    """, (
                        record.id, record.segment_key, record.league.upper(), record.scope,
                        record.family, record.model_id, record.class_name,
                        record.generator_provider, record.source_file_path,
                        record.claude_hypothesis, record.codex_rationale, record.grok_critique,
                        record.oos_sample_size, record.oos_win_rate, record.oos_simulated_roi,
                        record.oos_delta_brier, record.oos_max_drawdown,
                        1 if record.crucible_passed else 0, record.current_tier,
                        record.graduation_status, record.learned_insight,
                        record.created_at or now_iso, now_iso
                    ))
                    conn.commit()
                return True
            except sqlite3.OperationalError as e_sql:
                if "locked" in str(e_sql) and attempt < 7:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                log.error("Failed to record synthesis attempt for %s: %s", record.model_id, e_sql)
                return False
            except Exception as e:
                log.error("Failed to record synthesis attempt for %s: %s", record.model_id, e)
                return False
        return False

    def record_settlement(
        self,
        model_id: str,
        wager_result: str,
        odds: float,
        pnl: float,
        model_prob: Optional[float] = None,
        market_prob: Optional[float] = None,
    ) -> bool:
        """Updates live performance metrics in real time when a paper or live wager settles."""
        now_iso = datetime.now(timezone.utc).isoformat()
        for attempt in range(8):
            try:
                with closing(self._get_connection()) as conn:
                    row = conn.execute(
                        "SELECT * FROM ai_synthesis_feedback_loop WHERE model_id = ?",
                        (model_id,)
                    ).fetchone()

                    if not row:
                        return False

                    games = row["live_games_settled"] + 1
                    wins = row["live_wins"] + (1 if wager_result == "won" else 0)
                    losses = row["live_losses"] + (1 if wager_result == "lost" else 0)
                    pushes = row["live_pushes"] + (1 if wager_result == "push" else 0)
                    tot_pnl = row["live_realized_pnl"] + pnl
                    win_rate = (wins / max(1, wins + losses)) if (wins + losses) > 0 else 0.0
                    roi = tot_pnl / max(1, games)

                    # Incremental Brier delta tracking if probabilities provided
                    brier_delta = row["live_brier_delta"]
                    if model_prob is not None and market_prob is not None and wager_result != "push":
                        y = 1.0 if wager_result == "won" else 0.0
                        bm = (model_prob - y) ** 2
                        bmkt = (market_prob - y) ** 2
                        inst_delta = bmkt - bm
                        brier_delta = round((row["live_brier_delta"] * (games - 1) + inst_delta) / games, 5)

                    # Determine if graduation criteria achieved (N >= 25, ROI >= +3.5%)
                    status = row["graduation_status"]
                    tier = row["current_tier"]
                    insight = row["learned_insight"] or ""

                    if games >= 25 and roi >= 0.035 and status != "graduated":
                        status = "graduated"
                        tier = 3
                        insight = f"CONFIRMED ALPHA: Sustained {roi*100:.1f}% live ROI across {games} settled matches."
                    elif games >= 10 and roi < -0.05 and status != "demoted":
                        status = "flagged_remedy"
                        insight = f"VARIANCE WARNING: Live ROI dropped to {roi*100:.1f}%. Parameter shrinkage required."

                    conn.execute("""
                        UPDATE ai_synthesis_feedback_loop SET
                            live_games_settled = ?,
                            live_wins = ?,
                            live_losses = ?,
                            live_pushes = ?,
                            live_win_rate = ?,
                            live_realized_roi = ?,
                            live_realized_pnl = ?,
                            live_brier_delta = ?,
                            graduation_status = ?,
                            current_tier = ?,
                            learned_insight = ?,
                            updated_at = ?
                        WHERE model_id = ?
                    """, (
                        games, wins, losses, pushes, round(win_rate, 4),
                        round(roi, 4), round(tot_pnl, 2), brier_delta,
                        status, tier, insight, now_iso, model_id
                    ))
                    conn.commit()
                return True
            except sqlite3.OperationalError as e_sql:
                if "locked" in str(e_sql) and attempt < 7:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                log.warning("Could not record settlement feedback for %s: %s", model_id, e_sql)
                return False
            except Exception as e:
                log.warning("Could not record settlement feedback for %s: %s", model_id, e)
                return False
        return False

    def get_in_context_learning_prompt(
        self,
        league: str,
        family: str,
        limit_success: int = 3,
        limit_failed: int = 2,
    ) -> str:
        """Constructs an in-context few-shot learning prompt for AI code synthesis.
        
        Supplies previously discovered positive alpha formulations and warns against
        previously failed mathematical structures in the specific league.
        """
        lg = league.upper()
        fam = family.lower()

        try:
            with closing(self._get_connection()) as conn:
                # 1. Fetch top profitable models in this league/family
                cur = conn.cursor()
                cur.execute("""
                    SELECT class_name, oos_simulated_roi, oos_delta_brier, live_realized_roi,
                           live_games_settled, codex_rationale, claude_hypothesis
                    FROM ai_synthesis_feedback_loop
                    WHERE league = ? AND (oos_simulated_roi >= 0.035 OR live_realized_roi >= 0.035)
                    ORDER BY (oos_simulated_roi * 0.4 + COALESCE(live_realized_roi, 0.0) * 0.6) DESC
                    LIMIT ?
                """, (lg, limit_success))
                success_rows = cur.fetchall()

                # 2. Fetch failed models to avoid repeating mistakes
                cur.execute("""
                    SELECT class_name, oos_simulated_roi, oos_delta_brier, codex_rationale
                    FROM ai_synthesis_feedback_loop
                    WHERE league = ? AND crucible_passed = 0
                    ORDER BY oos_simulated_roi ASC
                    LIMIT ?
                """, (lg, limit_failed))
                failed_rows = cur.fetchall()

        except Exception as e:
            log.debug("Feedback retrieval error: %s", e)
            return ""

        sections = []
        if success_rows:
            sections.append(f"VERIFIED PROFITABLE ALPHA EXEMPLARS IN {lg} (LEARN FROM THESE):")
            for r in success_rows:
                l_roi = f" | Live ROI: {r['live_realized_roi']*100:.1f}% ({r['live_games_settled']} games)" if r['live_games_settled'] > 0 else ""
                sections.append(
                    f"- Archetype: {r['class_name']} | OOS ROI: +{r['oos_simulated_roi']*100:.1f}% | "
                    f"Delta Brier: +{r['oos_delta_brier']:.4f}{l_roi}\n"
                    f"  Core mathematical rationale: {r['codex_rationale'] or 'Adaptive score lattice'}"
                )

        if failed_rows:
            sections.append(f"\nFAILED COUNTER-EXAMPLES IN {lg} (AVOID THESE MISTAKES):")
            for r in failed_rows:
                sections.append(
                    f"- Rejected Archetype: {r['class_name']} | OOS Deficit: {r['oos_simulated_roi']*100:.1f}%\n"
                    f"  Failure Mode: Did not generate positive alpha; bookmaker closing line dominated forecasts."
                )

        if not sections:
            return f"PRIOR QUANT ADVISORY: Target high-conviction positive EV on {lg} {fam}. Emphasize heavy-tailed variance and key numbers."

        return "\n".join(sections)
