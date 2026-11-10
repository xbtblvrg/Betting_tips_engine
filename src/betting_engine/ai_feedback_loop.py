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
    validation_errors: Optional[List[str]] = None
    failure_reason: Optional[str] = None
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
                        validation_errors TEXT,
                        failure_reason TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                # Auto-migrate optional columns if not present
                for col_def in ("validation_errors TEXT", "failure_reason TEXT"):
                    try:
                        conn.execute(f"ALTER TABLE ai_synthesis_feedback_loop ADD COLUMN {col_def}")
                    except Exception:
                        pass
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
        val_errs_json = json.dumps(record.validation_errors) if record.validation_errors else None
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
                            learned_insight, validation_errors, failure_reason, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(model_id) DO UPDATE SET
                            oos_sample_size = excluded.oos_sample_size,
                            oos_win_rate = excluded.oos_win_rate,
                            oos_simulated_roi = excluded.oos_simulated_roi,
                            oos_delta_brier = excluded.oos_delta_brier,
                            oos_max_drawdown = excluded.oos_max_drawdown,
                            crucible_passed = excluded.crucible_passed,
                            current_tier = excluded.current_tier,
                            graduation_status = excluded.graduation_status,
                            learned_insight = excluded.learned_insight,
                            validation_errors = excluded.validation_errors,
                            failure_reason = excluded.failure_reason,
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
                        val_errs_json, record.failure_reason,
                        record.created_at or now_iso, now_iso
                    ))
                    conn.commit()

                # Real-time Distillation of Champion Models by AIArchetypeDeveloperAgent
                if record.crucible_passed:
                    try:
                        from pathlib import Path
                        from .ai_archetype_developer import AIArchetypeDeveloperAgent
                        fpath = Path(record.source_file_path) if record.source_file_path else None
                        if fpath and fpath.exists():
                            code_text = fpath.read_text(encoding="utf-8")
                            agent = AIArchetypeDeveloperAgent(db_path=self.db_path)
                            agent.distill_from_candidate(
                                model_id=record.model_id,
                                source_code=code_text,
                                class_name=record.class_name,
                                league=record.league,
                                scope=record.scope,
                                family=record.family,
                                oos_roi=record.oos_simulated_roi,
                                delta_brier=record.oos_delta_brier,
                                provider=record.generator_provider,
                            )
                    except Exception as e_dist:
                        log.debug("Real-time distillation skipped for %s: %s", record.model_id, e_dist)

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

    def record_settlements_batch(
        self,
        events: List[Tuple[str, str, float, float, Optional[float], Optional[float]]],
    ) -> int:
        """Batch-updates live performance metrics in a single SQLite transaction."""
        if not events:
            return 0
        now_iso = datetime.now(timezone.utc).isoformat()

        # Aggregate by model_id in memory first
        by_model: Dict[str, List[Tuple[str, float, float, Optional[float], Optional[float]]]] = {}
        for m_id, out_res, odds, pnl, mod_p, mkt_p in events:
            by_model.setdefault(m_id, []).append((out_res, odds, pnl, mod_p, mkt_p))

        updated_count = 0
        for attempt in range(8):
            try:
                with closing(self._get_connection()) as conn:
                    cur = conn.cursor()
                    cur.execute("PRAGMA busy_timeout = 60000")
                    model_ids = list(by_model.keys())
                    for i in range(0, len(model_ids), 500):
                        batch_ids = model_ids[i:i + 500]
                        placeholders = ','.join(['?'] * len(batch_ids))
                        rows = cur.execute(
                            f"SELECT * FROM ai_synthesis_feedback_loop WHERE model_id IN ({placeholders})",
                            batch_ids
                        ).fetchall()
                        row_map = {r["model_id"]: dict(r) for r in rows}

                        updates = []
                        for m_id in batch_ids:
                            if m_id not in row_map:
                                continue
                            r = row_map[m_id]
                            ev_list = by_model[m_id]

                            games = r["live_games_settled"]
                            wins = r["live_wins"]
                            losses = r["live_losses"]
                            pushes = r["live_pushes"]
                            tot_pnl = r["live_realized_pnl"]
                            brier_delta = r["live_brier_delta"]

                            for out_res, odds, pnl, mod_p, mkt_p in ev_list:
                                games += 1
                                if out_res == "won":
                                    wins += 1
                                elif out_res == "lost":
                                    losses += 1
                                elif out_res == "push":
                                    pushes += 1
                                tot_pnl += pnl
                                if mod_p is not None and mkt_p is not None and out_res != "push":
                                    y = 1.0 if out_res == "won" else 0.0
                                    bm = (mod_p - y) ** 2
                                    bmkt = (mkt_p - y) ** 2
                                    inst_delta = bmkt - bm
                                    brier_delta = round((brier_delta * (games - 1) + inst_delta) / games, 5)

                            win_rate = (wins / max(1, wins + losses)) if (wins + losses) > 0 else 0.0
                            roi = tot_pnl / max(1, games)

                            status = r["graduation_status"]
                            tier = r["current_tier"]
                            insight = r["learned_insight"] or ""

                            if games >= 25 and roi >= 0.035 and status != "graduated":
                                status = "graduated"
                                tier = 3
                                insight = f"CONFIRMED ALPHA: Sustained {roi*100:.1f}% live ROI across {games} settled matches."
                            elif games >= 10 and roi < -0.05 and status != "demoted":
                                status = "flagged_remedy"
                                insight = f"VARIANCE WARNING: Live ROI dropped to {roi*100:.1f}%. Parameter shrinkage required."

                            updates.append((
                                games, wins, losses, pushes, round(win_rate, 4),
                                round(roi, 4), round(tot_pnl, 2), brier_delta,
                                status, tier, insight, now_iso, m_id
                            ))

                        if updates:
                            cur.executemany("""
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
                            """, updates)
                            updated_count += len(updates)
                    conn.commit()
                return updated_count
            except sqlite3.OperationalError as e_sql:
                if "locked" in str(e_sql) and attempt < 7:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                log.warning("Could not batch record settlement feedback: %s", e_sql)
                return updated_count
            except Exception as e:
                log.warning("Could not batch record settlement feedback: %s", e)
                return updated_count
        return updated_count

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
                # 1. Fetch top profitable models in this league/family (grouped by distinct archetype)
                cur = conn.cursor()
                cur.execute("""
                    SELECT class_name, max(oos_simulated_roi) as oos_simulated_roi, max(oos_delta_brier) as oos_delta_brier,
                           max(COALESCE(live_realized_roi, 0.0)) as live_realized_roi,
                           max(live_games_settled) as live_games_settled,
                           max(codex_rationale) as codex_rationale,
                           max(claude_hypothesis) as claude_hypothesis
                    FROM ai_synthesis_feedback_loop
                    WHERE league = ? AND (oos_simulated_roi >= 0.035 OR live_realized_roi >= 0.035)
                    GROUP BY class_name
                    ORDER BY (max(oos_simulated_roi) * 0.4 + COALESCE(max(live_realized_roi), 0.0) * 0.6) DESC
                    LIMIT ?
                """, (lg, limit_success))
                success_rows = cur.fetchall()

                # 2. Fetch distinct failed models, excluding already successful classes
                succ_classes = {r['class_name'] for r in success_rows}
                cur.execute("""
                    SELECT class_name, min(oos_simulated_roi) as oos_simulated_roi, min(oos_delta_brier) as oos_delta_brier, max(codex_rationale) as codex_rationale
                    FROM ai_synthesis_feedback_loop
                    WHERE league = ? AND crucible_passed = 0
                    GROUP BY class_name
                    ORDER BY min(oos_simulated_roi) ASC
                    LIMIT 10
                """, (lg,))
                raw_failed = cur.fetchall()
                failed_rows = [r for r in raw_failed if r['class_name'] not in succ_classes][:limit_failed]

                # 3. Fetch recent observed runtime and pre-flight defects in this league
                try:
                    cur.execute("""
                        SELECT class_name, failure_reason, learned_insight, graduation_status, created_at
                        FROM ai_synthesis_feedback_loop
                        WHERE league = ? AND (
                            graduation_status IN ('preflight_failed', 'syntax_failed', 'instantiation_failed')
                            OR failure_reason IS NOT NULL
                            OR learned_insight LIKE '%Pre-flight failed%'
                            OR learned_insight LIKE '%Instantiation error%'
                            OR learned_insight LIKE '%Syntax%'
                        )
                        ORDER BY created_at DESC
                        LIMIT 3
                    """, (lg,))
                    recent_defects = cur.fetchall()
                except Exception:
                    recent_defects = []

        except Exception as e:
            log.debug("Feedback retrieval error: %s", e)
            return ""

        sections = []
        if success_rows:
            sections.append(f"HISTORICAL EMPIRICAL ALPHA BENCHMARKS IN {lg} (OBJECTIVE MATHEMATICAL TARGETS TO MATCH OR EXCEED):")
            for idx, r in enumerate(success_rows, 1):
                l_roi = f" | Live ROI: {r['live_realized_roi']*100:.1f}% ({r['live_games_settled']} games)" if r['live_games_settled'] > 0 else ""
                sections.append(
                    f"- Empirical Benchmark {idx}: Out-of-sample ROI achieved: +{r['oos_simulated_roi']*100:.1f}% | "
                    f"Delta Brier skill: +{r['oos_delta_brier']:.4f}{l_roi}\n"
                    f"  Objective: Formulate independent quantitative architecture matching or exceeding this empirical skill."
                )

        if failed_rows:
            sections.append(f"\nHISTORICAL EMPIRICAL CALIBRATION DEFICITS IN {lg} (DEFICITS TO OVERCOME):")
            for idx, r in enumerate(failed_rows, 1):
                sections.append(
                    f"- Empirical Deficit {idx}: Out-of-sample deficit: {r['oos_simulated_roi']*100:.1f}%\n"
                    f"  Observed Pathology: Uncalibrated probability mass dominated by bookmaker line; eliminate tail mispricing from first principles."
                )

        if recent_defects:
            sections.append(f"\nRECENT OBSERVED RUNTIME IMPLEMENTATION PITFALLS IN {lg} (SYSTEM DEFECTS TO PREVENT IN CODE):")
            for idx, r in enumerate(recent_defects, 1):
                reason = r['failure_reason'] or r['learned_insight'] or "Execution/pre-flight validation error"
                reason_clean = str(reason).replace('\n', ' ')[:200]
                sections.append(
                    f"- Observed Defect {idx}: {reason_clean}\n"
                    f"  Requirement: Strictly ensure valid DiscreteDistribution output (non-negative probabilities summing to 1.0) and latency under 250ms."
                )

        if not sections:
            return f"PRIOR QUANT ADVISORY: Target high-conviction positive EV on {lg} {fam} with calibrated distribution estimation."

        sections.append("\n(Note: Empirical benchmarks above quantify observed statistical skill targets. Formulate optimal probability structures independently from first principles based on observed data moments.)")
        return "\n".join(sections)
