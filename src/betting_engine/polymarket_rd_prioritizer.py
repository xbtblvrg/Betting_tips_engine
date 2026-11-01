"""Polymarket Autonomous R&D Prioritizer.

Scans active Polymarket tournaments and events (NBA, NFL, NHL, etc.), determines
which contracts are actively offered on Polymarket, filters by Tier 3 graduation,
and autonomously enqueues high-priority learning jobs for unproven reachable markets.

Prime Directives:
- Reachable Market Focus: Only enqueue R&D for contracts genuinely offered on Polymarket.
- Zero Hardcoded Bans: As soon as a candidate model passes the Crucible and re-graduates,
  it automatically unlocks in PolymarketAutonomousExecutor.
- Priority Elevation: Tagged as CRITICAL_URGENT_POLYMARKET so Codex and HistoricalTrainer
  prioritize them ahead of generic tasks.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from .domain import utcnow
from .polymarket_service import PolymarketService, _detect_market_scope
from .polymarket_executor import PolymarketAutonomousExecutor
from .providers import DATA

log = logging.getLogger(__name__)


class PolymarketRDPrioritizer:
    """Prioritizes model synthesis and calibration repairs for liquid Polymarket contracts."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        executor: Optional[PolymarketAutonomousExecutor] = None,
    ):
        self.db_path = Path(db_path) if db_path else (DATA / "engine.sqlite3")
        self.cache_dir = Path(cache_dir) if cache_dir else (DATA / "cache" / "polymarket")
        self.service = PolymarketService(cache_dir=self.cache_dir)
        self.executor = executor or PolymarketAutonomousExecutor(db_path=self.db_path, dry_run=True)

    def scan_and_prioritize_gaps(
        self,
        leagues: Tuple[str, ...] = ("NBA", "NFL", "NHL"),
    ) -> Dict[str, Any]:
        """Scans reachable contracts, identifies unproven gaps, and enqueues learning jobs."""
        discovered_segments: Set[str] = set()
        segment_details: Dict[str, Dict[str, Any]] = {}

        # 1. Discover reachable contracts across target leagues
        for league in leagues:
            try:
                events = self.service.get_open_events(league=league, horizon_hours=72.0)
                for ev in events:
                    markets = ev.get("markets", [])
                    for m in markets:
                        m_type = (m.get("sportsMarketType") or m.get("type") or "moneyline").lower()
                        q = m.get("question") or ""
                        scope = _detect_market_scope(m_type, q)

                        # Normalize market family
                        if "spread" in m_type or "handicap" in m_type:
                            m_fam = "spreads"
                        elif "total" in m_type or "over" in m_type:
                            m_fam = "totals"
                        else:
                            m_fam = "moneyline"

                        seg_key = f"{league}|{scope}|{m_fam}"
                        discovered_segments.add(seg_key)

                        if seg_key not in segment_details:
                            tokens = m.get("clobTokenIds", [])
                            if isinstance(tokens, str):
                                try:
                                    tokens = json.loads(tokens)
                                except Exception:
                                    tokens = [tokens]
                            segment_details[seg_key] = {
                                "league": league,
                                "scope": scope,
                                "market_family": m_fam,
                                "sample_event": ev.get("title", "Unknown"),
                                "sample_token": tokens[0] if tokens else None,
                            }
            except Exception as exc:
                log.warning(f"Error scanning Polymarket events for league {league}: {exc}")

        # 2. Evaluate each discovered segment for Tier 3 Proven Alpha
        proven_markets: List[Dict[str, Any]] = []
        unproven_gaps: List[Dict[str, Any]] = []

        for seg_key in sorted(discovered_segments):
            details = segment_details[seg_key]
            league = details["league"]
            scope = details["scope"]
            m_fam = details["market_family"]

            is_eligible, reason, stake_cap = self.executor.is_market_eligible(
                league=league,
                scope=scope,
                market_family=m_fam,
            )

            seg_info = {
                "segment_key": seg_key,
                "league": league,
                "scope": scope,
                "market_family": m_fam,
                "sample_event": details["sample_event"],
                "sample_token": details["sample_token"],
                "reason": reason,
                "stake_cap": stake_cap,
            }

            if is_eligible:
                proven_markets.append(seg_info)
            else:
                unproven_gaps.append(seg_info)

        # 3. Enqueue learning jobs for unproven reachable gaps
        enqueued_count = 0
        now_iso = utcnow().isoformat()

        if self.db_path.exists() and unproven_gaps:
            try:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    for gap in unproven_gaps:
                        seg_key = gap["segment_key"]
                        league = gap["league"]
                        job_id = f"poly_gap_{seg_key.lower().replace('|', '_')}"
                        payload = {
                            "kind": "urgent_polymarket_model_repair",
                            "segment_key": seg_key,
                            "league": league,
                            "scope": gap["scope"],
                            "market_family": gap["market_family"],
                            "sample_event": gap["sample_event"],
                            "sample_token": gap["sample_token"],
                            "priority_tag": "CRITICAL_URGENT_POLYMARKET",
                            "objective": "Pass Crucible out-of-sample backtest (ROI >= +3.5%, Brier skill > 0) to graduate market into live Polymarket execution.",
                            "timestamp": now_iso,
                        }

                        conn.execute(
                            """
                            INSERT INTO learning_jobs (
                                id, kind, league, status, created_at, updated_at, next_attempt, payload
                            ) VALUES (?, 'urgent_polymarket_model_repair', ?, 'queued', ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                updated_at=excluded.updated_at,
                                payload=excluded.payload;
                            """,
                            (
                                job_id,
                                league,
                                now_iso,
                                now_iso,
                                now_iso,
                                json.dumps(payload),
                            ),
                        )
                        enqueued_count += 1
            except Exception as exc:
                log.error(f"Failed to enqueue Polymarket R&D repair jobs into SQLite: {exc}")

        log.info(
            f"Polymarket R&D Gap Analysis complete: {len(discovered_segments)} reachable segments, "
            f"{len(proven_markets)} proven active, {len(unproven_gaps)} unproven gaps ({enqueued_count} enqueued)."
        )

        return {
            "timestamp": now_iso,
            "reachable_segments_count": len(discovered_segments),
            "proven_active_count": len(proven_markets),
            "unproven_gaps_count": len(unproven_gaps),
            "proven_markets": proven_markets,
            "unproven_gaps": unproven_gaps,
            "enqueued_jobs_count": enqueued_count,
        }
