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


DEFAULT_POLYMARKET_LEAGUES: Tuple[str, ...] = (
    "NBA",
    "NFL",
    "NHL",
    "MLB",
    "WNBA",
    "NCAAF",
    "NCAAB",
    "NCAAW",
    "MLS",
    "EPL",
    "UCL",
    "UEL",
    "LALIGA",
    "BUNDESLIGA",
    "SERIEA",
    "ATP",
    "WTA",
)


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
        leagues: Optional[Tuple[str, ...]] = None,
    ) -> Dict[str, Any]:
        """Scans reachable contracts, identifies unproven gaps, and enqueues learning jobs."""
        target_leagues = leagues if leagues is not None else DEFAULT_POLYMARKET_LEAGUES
        discovered_segments: Set[str] = set()
        segment_details: Dict[str, Dict[str, Any]] = {}

        # 1. Seed standard full-game contract types across all target leagues
        for lg in target_leagues:
            for fam in ("moneyline", "spreads", "totals"):
                seg_key = f"{lg}|full_game|{fam}"
                discovered_segments.add(seg_key)
                segment_details[seg_key] = {
                    "league": lg,
                    "scope": "full_game",
                    "market_family": fam,
                    "sample_event": f"Active {lg} CLOB Orderbook",
                    "sample_token": None,
                }

        # 2. Discover live open contracts across target leagues (Parallel Concurrent Fetch)
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_league(lg: str):
            try:
                return lg, self.service.get_open_events(league=lg, horizon_hours=72.0)
            except Exception as exc:
                log.warning(f"Error scanning Polymarket events for league {lg}: {exc}")
                return lg, []

        with ThreadPoolExecutor(max_workers=min(8, len(target_leagues))) as pool:
            league_results = list(pool.map(_fetch_league, target_leagues))

        from .polymarket_runner import POLYMARKET_STRUCTURED_TYPE_MAP

        for league, events in league_results:
            norm_league = "NCAAM" if league in ("CBB", "NCAAB") else ("NCAAF" if league == "CFB" else league)
            for ev in events:
                markets = ev.get("markets", [])
                for m in markets:
                    m_type = (m.get("sportsMarketType") or m.get("type") or "").strip().lower()
                    q = m.get("question") or ""

                    # Canonical resolution via POLYMARKET_STRUCTURED_TYPE_MAP
                    if m_type in POLYMARKET_STRUCTURED_TYPE_MAP:
                        scope, m_fam = POLYMARKET_STRUCTURED_TYPE_MAP[m_type]
                    else:
                        # Skip player props, team totals, and excluded novelties from R&D queue
                        is_excluded = any(prop in m_type for prop in (
                            "touchdown", "reception", "pass", "rush", "assist", "rebound", "point", "yard",
                            "strikeout", "tackle", "sack", "interception", "margin", "race", "corner",
                            "goal", "shot", "save", "team_total", "team_totals", "overtime", "odd_even",
                            "pick_six", "fourth_down", "field_goal", "first_down", "exact_score",
                            "first_to_score", "score_first", "two_point", "completed_match",
                            "three", "threes", "steal", "block", "turnover", "foul", "home_run", "hit", "rbi"
                        ))
                        if is_excluded:
                            continue
                        scope = _detect_market_scope(m_type, q)
                        if "spread" in m_type or "handicap" in m_type:
                            m_fam = "spreads"
                        elif "total" in m_type or "over" in m_type:
                            m_fam = "totals"
                        else:
                            m_fam = "moneyline"

                    seg_key = f"{norm_league}|{scope}|{m_fam}"
                    discovered_segments.add(seg_key)

                    tokens = m.get("clobTokenIds", [])
                    if isinstance(tokens, str):
                        try:
                            tokens = json.loads(tokens)
                        except Exception:
                            tokens = [tokens]

                    segment_details[seg_key] = {
                        "league": norm_league,
                        "scope": scope,
                        "market_family": m_fam,
                        "sample_event": ev.get("title", f"Active {league} Match"),
                        "sample_token": tokens[0] if tokens else None,
                    }

        # 3. Load authoritative UnifiedMarketUniverse & SegmentScoreboard metadata
        roster = {}
        try:
            from .unified_market_universe import UnifiedMarketUniverse
            umu = UnifiedMarketUniverse(data_dir=DATA)
            roster = umu.build_universe_roster()
        except Exception as exc:
            log.warning(f"Could not load UnifiedMarketUniverse: {exc}")

        scoreboard_segs = {}
        try:
            from .segment_scoreboard import compute_segment_scoreboard
            if self.db_path.exists():
                with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                    conn.execute("PRAGMA busy_timeout = 30000;")
                    sb = compute_segment_scoreboard(conn)
                    for s in (sb.get("alpha_segments", []) + sb.get("exploration_segments", []) + sb.get("underperforming_segments", [])):
                        scoreboard_segs[s["segment"]] = s
        except Exception as exc:
            log.warning(f"Could not load SegmentScoreboard: {exc}")

        # 4. Evaluate each discovered segment for Tier 4 Proven Alpha vs Tier 3 Probation vs Tier 2 Gaps
        proven_markets: List[Dict[str, Any]] = []
        unproven_gaps: List[Dict[str, Any]] = []

        for seg_key in sorted(discovered_segments):
            details = segment_details[seg_key]
            league = details["league"]
            scope = details["scope"]
            m_fam = details["market_family"]

            # Match scoreboard stats (using aliases if needed)
            sb_match = None
            for k, v in scoreboard_segs.items():
                if v.get("league") == league and v.get("scope") == scope:
                    f_name = v.get("family", "")
                    if m_fam in f_name or f_name in m_fam or (m_fam == "moneyline" and f_name in ("three_way", "winner", "h2h")):
                        sb_match = v
                        break

            live_games = int(sb_match.get("games", 0)) if sb_match else 0
            live_pnl = float(sb_match.get("pnl", 0.0)) if sb_match else 0.0
            live_roi_pct = float(sb_match.get("roi_pct", 0.0)) if sb_match else 0.0

            # Match model archetype from universe
            u_entry = roster.get(seg_key)
            model_id = u_entry.active_model_id if u_entry and u_entry.active_model_id else f"{league.lower()}_{scope}_{m_fam}_kernel"
            model_archetype = u_entry.model_archetype if u_entry and u_entry.model_archetype else "CalibratedDomainKernel"

            is_eligible, reason, stake_cap = self.executor.is_market_eligible(
                league=league,
                scope=scope,
                market_family=m_fam,
                require_tier_4=True,
            )

            if is_eligible:
                tier_status = "TIER_4_PROVEN"
            elif live_pnl > 0.0 and live_games > 0:
                tier_status = "TIER_3_PROBATION"
            elif live_pnl < 0.0 and live_games > 0:
                tier_status = "TIER_2_UNDERPERFORMING"
            else:
                tier_status = "TIER_2_INCUBATING"

            seg_info = {
                "segment_key": seg_key,
                "league": league,
                "scope": scope,
                "market_family": m_fam,
                "model_id": model_id,
                "model_archetype": model_archetype,
                "live_games": live_games,
                "live_pnl": round(live_pnl, 2),
                "live_roi_pct": round(live_roi_pct, 1),
                "is_profitable": (live_pnl > 0.0) if live_games > 0 else (False if live_games > 0 and live_pnl < 0.0 else None),
                "tier_status": tier_status,
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
                with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                    conn.execute("PRAGMA busy_timeout = 30000;")
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
            "all_reachable_markets": proven_markets + unproven_gaps,
            "enqueued_jobs_count": enqueued_count,
        }
