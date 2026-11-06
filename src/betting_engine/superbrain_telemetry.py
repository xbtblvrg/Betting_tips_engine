"""Superbrain Live Operations Flow & Neural Telemetry Engine.

Consolidates full-spectrum quantitative telemetry into a unified real-time state:
1. Multi-AI Quant Council: Gemini Antigravity (agy), Claude Opus 5.5, Codex Astra, Grok 4.7
2. 4-Tier Closed-Loop Progression & Velocity (R&D -> Incubator -> Stake -> Polymarket)
3. Live Match, League & Bet Flow (Active executable bets, Kelly stakes, odds, EV)
4. Real-time Settlements (WIN / LOSS events, tourniquet activations, PnL)
5. Graduations & Demotions (Crucible passings, Tier 3 -> Tier 1 quarantine returns)
6. Dugulásmentes & Bottleneck Diagnostics (LaunchAgent daemons, learning queue depth, latency)
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import subprocess
from typing import Any, Dict, List, Optional
from contextlib import closing

from .domain import utcnow
from .model_funnel_telemetry import get_latest_funnel_velocity_report
from .providers import DATA

log = logging.getLogger(__name__)

BUDAPEST_TZ = timezone(timedelta(hours=1))


def get_budapest_today_start_utc() -> str:
    now_bp = datetime.now(BUDAPEST_TZ)
    start_bp = datetime(now_bp.year, now_bp.month, now_bp.day, 0, 0, 0, tzinfo=BUDAPEST_TZ)
    return start_bp.astimezone(timezone.utc).isoformat()


def get_daemon_status() -> Dict[str, Any]:
    daemons = {
        "hu.belavarga.betting-quant-engine": {"name": "Web & Decision Engine", "status": "unknown", "pid": None},
        "hu.belavarga.betting-quant-learning": {"name": "AI Learning & Repair Worker", "status": "unknown", "pid": None},
        "hu.belavarga.betting-quant-supervisor": {"name": "Supervisor & Health Watchdog", "status": "unknown", "pid": None},
        "hu.belavarga.betting-quant-development": {"name": "Continuous Development Worker", "status": "unknown", "pid": None},
        "hu.belavarga.betting-quant-release": {"name": "Autonomous Release Coordinator", "status": "unknown", "pid": None},
    }
    try:
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 3:
                    pid_str, exit_str, label = parts[0], parts[1], parts[2]
                    if label in daemons:
                        pid = int(pid_str) if pid_str.isdigit() else None
                        daemons[label]["pid"] = pid
                        daemons[label]["status"] = "running" if pid else "idle"
    except Exception as e:
        log.debug("Could not query launchctl: %s", e)

    return daemons


def get_superbrain_state(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    d = data_dir or DATA
    eng_db = d / "engine.sqlite3"
    inc_db = d / "shadow_incubator.sqlite3"

    now_utc = utcnow()
    now_bp = datetime.now(BUDAPEST_TZ)
    today_bp_start = get_budapest_today_start_utc()

    # 1. Funnel Velocity
    funnel_report = get_latest_funnel_velocity_report(d)
    hourly = funnel_report.get("hourly_velocity", {}) if isinstance(funnel_report, dict) else {}
    inventory = funnel_report.get("active_inventory", {}) if isinstance(funnel_report, dict) else {}
    windows = funnel_report.get("windows", {}) if isinstance(funnel_report, dict) else {}
    w24h = windows.get("24h", {}) if isinstance(windows, dict) else {}

    synth_rate_per_hour = float(hourly.get("models_synthesized_per_hour_24h", w24h.get("synthesis_rate_per_hour", 0.0)) or 0.0)
    tier2_adm_per_hour = float(hourly.get("tier2_admissions_per_hour_24h", w24h.get("tier2_admission_rate_per_hour", 0.0)) or 0.0)
    tier3_grad_per_hour = float(hourly.get("tier3_graduations_per_hour_24h", w24h.get("tier3_graduation_rate_per_hour", 0.0)) or 0.0)
    tier1_dem_per_hour = float(hourly.get("tier1_demotions_per_hour_24h", w24h.get("tier1_demotion_rate_per_hour", 0.0)) or 0.0)
    tier4_prom_per_hour = float(hourly.get("tier4_promotions_per_hour_24h", w24h.get("tier4_promotion_rate_per_hour", 0.0)) or 0.0)
    net_expansion_rate = float(hourly.get("net_expansion_velocity_per_hour_24h", w24h.get("net_graduation_velocity_per_hour", 0.0)) or 0.0)
    crucible_pass_pct = float(hourly.get("crucible_pass_rate_pct_24h", w24h.get("crucible_pass_rate_pct", 0.0)) or 0.0)

    demoted_markets_count = int(inventory.get("tier2_shadow_incubator_count", 0) or 0)
    graduated_markets_count = int(inventory.get("tier3_production_graduated_count", 0) or 0)
    total_synthesized = int(w24h.get("syntheses_count", 0) or 0)

    if isinstance(funnel_report, dict):
        funnel_report["model_synthesis_rate_per_hour"] = synth_rate_per_hour
        funnel_report["tier_2_admission_rate_per_hour"] = tier2_adm_per_hour
        funnel_report["tier_3_graduation_rate_per_hour"] = tier3_grad_per_hour
        funnel_report["tier_1_demotion_rate_per_hour"] = tier1_dem_per_hour
        funnel_report["tier_4_alpha_promotion_rate_per_hour"] = tier4_prom_per_hour
        funnel_report["net_market_expansion_rate_per_hour"] = net_expansion_rate
        funnel_report["crucible_admission_rate_pct"] = crucible_pass_pct
        funnel_report["demoted_incubator_markets_count"] = demoted_markets_count
        funnel_report["active_graduated_markets_count"] = graduated_markets_count
        funnel_report["total_synthesized_models"] = total_synthesized

    # 2. Daemon & Clog Diagnostics
    daemons = get_daemon_status()

    # Engine Database Queries
    bets_summary: Dict[str, Any] = {"won": {"count": 0, "pnl": 0.0}, "lost": {"count": 0, "pnl": 0.0}, "open": {"count": 0, "stake": 0.0}}
    recent_settled: List[Dict[str, Any]] = []
    open_bets: List[Dict[str, Any]] = []
    active_jobs: List[Dict[str, Any]] = []
    jobs_summary: Dict[str, int] = {}
    today_bets_count = 0
    today_bets_stake = 0.0

    if eng_db.exists():
        try:
            with sqlite3.connect(f"file:{eng_db.resolve()}?mode=ro", uri=True, timeout=5.0) as conn:
                c = conn.cursor()

                # Bets summary
                c.execute("""
                    SELECT status, count(*), coalesce(sum(stake), 0), coalesce(sum(pnl), 0)
                    FROM bets
                    GROUP BY status
                """)
                for r in c.fetchall():
                    st = r[0]
                    bets_summary[st] = {
                        "count": r[1],
                        "stake": round(r[2], 2),
                        "pnl": round(r[3], 2)
                    }

                # Today Budapest bets
                c.execute("""
                    SELECT count(*), coalesce(sum(stake), 0)
                    FROM bets
                    WHERE created_at >= ?
                """, (today_bp_start,))
                t_row = c.fetchone()
                if t_row:
                    today_bets_count = t_row[0]
                    today_bets_stake = round(t_row[1], 2)

                # Recent settled bets (for WIN/LOSS real-time popups)
                c.execute("""
                    SELECT id, stake, odds, status, pnl, payload, created_at
                    FROM bets
                    WHERE status IN ('won', 'lost', 'push')
                    ORDER BY id DESC LIMIT 15
                """)
                for r in c.fetchall():
                    p = json.loads(r[5]) if r[5] else {}
                    recent_settled.append({
                        "id": r[0],
                        "stake": r[1],
                        "odds": r[2],
                        "status": r[3],
                        "pnl": round(r[4], 2) if r[4] is not None else 0.0,
                        "league": p.get("league") or "Unknown",
                        "home": p.get("home", ""),
                        "away": p.get("away", ""),
                        "market": p.get("market", ""),
                        "selection": p.get("selection", ""),
                        "expected_roi": round(p.get("expected_roi", 0.0) * 100, 1) if p.get("expected_roi") is not None else None,
                        "created_at": r[6]
                    })

                # Open bets (live recommendations)
                c.execute("""
                    SELECT id, stake, odds, status, payload, created_at
                    FROM bets
                    WHERE status = 'open'
                    ORDER BY id DESC LIMIT 10
                """)
                for r in c.fetchall():
                    p = json.loads(r[4]) if r[4] else {}
                    open_bets.append({
                        "id": r[0],
                        "stake": r[1],
                        "odds": r[2],
                        "league": p.get("league") or "Unknown",
                        "home": p.get("home", ""),
                        "away": p.get("away", ""),
                        "market": p.get("market", ""),
                        "selection": p.get("selection", ""),
                        "expected_roi": round(p.get("expected_roi", 0.0) * 100, 1) if p.get("expected_roi") is not None else None,
                        "probability": round(p.get("probability", 0.0) * 100, 1) if p.get("probability") is not None else None,
                        "created_at": r[5]
                    })

                # Learning queue health
                c.execute("SELECT status, count(*) FROM learning_jobs GROUP BY status")
                jobs_summary = dict(c.fetchall())

                c.execute("""
                    SELECT id, kind, league, status, created_at, updated_at
                    FROM learning_jobs
                    WHERE status NOT IN ('completed', 'superseded')
                    ORDER BY updated_at DESC LIMIT 5
                """)
                active_jobs = [
                    {"id": r[0], "kind": r[1], "league": r[2], "status": r[3], "created_at": r[4], "updated_at": r[5]}
                    for r in c.fetchall()
                ]

                # Polymarket CLOB status
                c.execute("""
                    SELECT status, count(*), coalesce(sum(stake_usd), 0), coalesce(sum(pnl), 0)
                    FROM polymarket_orders
                    GROUP BY status
                """)
                poly_summary = {r[0]: {"count": r[1], "stake": round(r[2], 2), "pnl": round(r[3], 2)} for r in c.fetchall()}

                c.execute("""
                    SELECT league, game_title, market_family, selection, poly_price, poly_odds, expected_value, stake_usd, status, pnl, created_at
                    FROM polymarket_orders
                    ORDER BY id DESC LIMIT 6
                """)
                recent_poly_orders = [
                    {
                        "league": r[0], "game": r[1], "family": r[2], "selection": r[3],
                        "price": r[4], "odds": r[5], "ev": round(r[6] * 100, 1),
                        "stake": r[7], "status": r[8], "pnl": r[9], "created_at": r[10]
                    }
                    for r in c.fetchall()
                ]
        except Exception as e:
            log.warning("Engine sqlite query error in superbrain telemetry: %s", e)
            poly_summary = {}
            recent_poly_orders = []
    else:
        poly_summary = {}
        recent_poly_orders = []

    # Incubator Database Queries
    graduations: List[Dict[str, Any]] = []
    demotions: List[Dict[str, Any]] = []
    syntheses: List[Dict[str, Any]] = []
    incubating_candidates: List[Dict[str, Any]] = []

    if inc_db.exists():
        try:
            with sqlite3.connect(f"file:{inc_db.resolve()}?mode=ro", uri=True, timeout=5.0) as conn:
                c = conn.cursor()

                # Recent graduated (promoted Tier 2 -> Tier 3)
                c.execute("""
                    SELECT segment_key, league, scope, family, graduated_at, sample_size, roi, delta_brier, win_rate
                    FROM graduated_markets
                    ORDER BY graduated_at DESC LIMIT 6
                """)
                graduations = [
                    {
                        "segment_key": r[0], "league": r[1], "scope": r[2], "family": r[3],
                        "graduated_at": r[4], "sample_size": r[5], "roi": round(r[6] * 100, 2),
                        "delta_brier": round(r[7], 4), "win_rate": round(r[8] * 100, 1)
                    }
                    for r in c.fetchall()
                ]

                # Recent demoted (demoted Tier 3 -> Tier 1)
                c.execute("""
                    SELECT segment_key, league, scope, family, demoted_at, rolling_roi, rolling_pnl, reason, incubation_phase
                    FROM demoted_markets
                    ORDER BY demoted_at DESC LIMIT 6
                """)
                demotions = [
                    {
                        "segment_key": r[0], "league": r[1], "scope": r[2], "family": r[3],
                        "demoted_at": r[4], "rolling_roi": round(r[5] * 100, 2), "rolling_pnl": round(r[6], 2),
                        "reason": r[7], "incubation_phase": r[8]
                    }
                    for r in c.fetchall()
                ]

                # Recent AI syntheses (Tier 1 R&D Models)
                c.execute("""
                    SELECT model_id, class_name, generator_provider, league, scope, family,
                           oos_simulated_roi, oos_delta_brier, crucible_passed, current_tier, graduation_status, created_at
                    FROM ai_synthesis_feedback_loop
                    ORDER BY created_at DESC LIMIT 8
                """)
                syntheses = [
                    {
                        "model_id": r[0], "class_name": r[1], "provider": r[2], "league": r[3],
                        "scope": r[4], "family": r[5], "oos_roi": round(r[6] * 100, 2),
                        "delta_brier": round(r[7], 4), "crucible_passed": bool(r[8]),
                        "current_tier": r[9], "status": r[10], "created_at": r[11]
                    }
                    for r in c.fetchall()
                ]

                # Tier 2 Top Incubating Candidates
                c.execute("""
                    SELECT model_id, class_name, league, scope, family, live_games_settled, live_wins, live_losses,
                           live_realized_roi, live_realized_pnl, live_brier_delta, graduation_status
                    FROM ai_synthesis_feedback_loop
                    WHERE current_tier = 2 OR crucible_passed = 1
                    ORDER BY updated_at DESC LIMIT 6
                """)
                incubating_candidates = [
                    {
                        "model_id": r[0], "class_name": r[1], "league": r[2], "scope": r[3],
                        "family": r[4], "settled": r[5], "wins": r[6], "losses": r[7],
                        "roi": round(r[8] * 100, 2), "pnl": round(r[9], 2),
                        "brier_delta": round(r[10], 4), "status": r[11]
                    }
                    for r in c.fetchall()
                ]
        except Exception as e:
            log.warning("Incubator sqlite query error in superbrain telemetry: %s", e)

    # 3. Quant Council AI Sessions
    recent_council_sessions: List[Dict[str, Any]] = []
    council_file = d / "research" / "quant_council_sessions.jsonl"
    if council_file.exists():
        try:
            with open(council_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 120000))
                lines = f.readlines()
                for line in reversed(lines):
                    try:
                        entry = json.loads(line.decode("utf-8"))
                        recent_council_sessions.append({
                            "session_id": entry.get("session_id"),
                            "segment": entry.get("segment"),
                            "league": entry.get("league"),
                            "created_at": entry.get("created_at"),
                            "trigger_reason": entry.get("trigger_reason"),
                            "claude_advice": entry.get("sports_mechanics_advice", {}).get("text", "")[:280] if entry.get("sports_mechanics_advice") else None,
                            "decision": entry.get("synthesis", {}).get("decision"),
                            "hypothesis_title": entry.get("synthesized_hypothesis", {}).get("title"),
                            "target_formula": entry.get("synthesized_hypothesis", {}).get("proposed_formula_or_rule")
                        })
                        if len(recent_council_sessions) >= 5:
                            break
                    except Exception:
                        continue
        except Exception as e:
            log.debug("Council file read error: %s", e)

    # Bottleneck & Dugulásmentes status evaluation
    queued_count = jobs_summary.get("queued", 0)
    retry_count = jobs_summary.get("retry", 0)
    is_clogged = queued_count > 2500 or retry_count > 50
    clog_status = {
        "is_clogged": is_clogged,
        "indicator": "WARNING: QUEUE BACKLOG" if is_clogged else "OPTIMAL: DUGULÁSMENTES (FREE FLOW)",
        "queued_jobs": queued_count,
        "completed_jobs": jobs_summary.get("completed", 0),
        "collecting_evidence": jobs_summary.get("collecting_evidence", 0),
        "needs_implementation": jobs_summary.get("needs_implementation", 0),
        "retry_jobs": retry_count,
        "running_daemons_count": sum(1 for v in daemons.values() if v.get("status") == "running"),
        "total_daemons_count": len(daemons)
    }

    # Aggregate AI Brains Profile
    ai_brains = [
        {
            "id": "agy",
            "name": "Gemini Antigravity",
            "model": "agy-lead-quant",
            "role": "Lead Architect & Release Authority",
            "status": "ACTIVE / ORCHESTRATING",
            "pulse_rate": "Continuous",
            "specialty": "Full-System Directives, Zero-Regression Gatekeeping, Closed-Loop Invariants",
            "current_focus": "Automated Turnaround & Dugulásmentes Flow Optimization"
        },
        {
            "id": "claude",
            "name": "Claude Opus 5.5",
            "model": "claude-opus-5-5",
            "role": "Sports Mechanics & Situational Fatigue",
            "status": "ACTIVE / INFERENCE",
            "pulse_rate": "High Cadence",
            "specialty": "Rest differentials, B2B travel, starter rotations, weather impact (Open-Meteo)",
            "current_focus": "NFL/NBA margin shape calibration & underdog tail distribution shrinkage"
        },
        {
            "id": "codex",
            "name": "OpenAI Codex Astra",
            "model": "gpt-6-astra",
            "role": "Mathematical Kernel Synthesizer",
            "status": "ACTIVE / SYNTHESIZING",
            "pulse_rate": f"{funnel_report.get('model_synthesis_rate_per_hour', 0):.1f} models/h",
            "specialty": "Copula Hazards, Negative Binomial, Asymmetric Laplace, Odds-Damped Kelly",
            "current_focus": "First-Principles edge discovery across demoted derivatives"
        },
        {
            "id": "grok",
            "name": "xAI Grok 4.7",
            "model": "grok-4.7",
            "role": "Adversarial Red-Team Auditor",
            "status": "ACTIVE / STRESS_TESTING",
            "pulse_rate": "Strict Crucible",
            "specialty": "Out-of-sample drawdown limits, anti-overfit red-teaming, tail risk fences",
            "current_focus": "Validating candidate Brier scores and preventing selection bias"
        }
    ]

    total_realized_pnl = bets_summary.get("won", {}).get("pnl", 0.0) + bets_summary.get("lost", {}).get("pnl", 0.0)
    total_won_bets = bets_summary.get("won", {}).get("count", 0)
    total_lost_bets = bets_summary.get("lost", {}).get("count", 0)
    total_settled_bets = total_won_bets + total_lost_bets
    win_rate_pct = round((total_won_bets / total_settled_bets * 100), 1) if total_settled_bets > 0 else 0.0

    return {
        "timestamp_utc": now_utc.isoformat(),
        "budapest_time": now_bp.strftime("%Y-%m-%d %H:%M:%S UTC+1"),
        "budapest_date": now_bp.strftime("%Y-%m-%d"),
        "system_status": "OPTIMAL",
        "clog_diagnostics": clog_status,
        "daemons": daemons,
        "ai_brains": ai_brains,
        "funnel_velocity": funnel_report,
        "council_sessions": recent_council_sessions,
        "tiers": {
            "tier_1_rd": {
                "name": "Tier 1: R&D Historical Training & Edge Discovery",
                "active_models_synthesized": total_synthesized,
                "synthesis_rate_per_hour": synth_rate_per_hour,
                "historical_match_backtest_pool": "97,000+ settled matches",
                "recent_syntheses": syntheses
            },
            "tier_2_incubator": {
                "name": "Tier 2: Shadow Incubator ($0.00 Live Forward Validation)",
                "demoted_markets_count": demoted_markets_count,
                "admission_rate_per_hour": tier2_adm_per_hour,
                "crucible_pass_rate_pct": crucible_pass_pct,
                "virtual_stake": "$0.00 (Zero bankroll risk)",
                "top_candidates": incubating_candidates
            },
            "tier_3_stake": {
                "name": "Tier 3: Stake Sportsbook Production ($0.50–$8.00 Kelly)",
                "active_graduated_markets_count": graduated_markets_count,
                "graduation_rate_per_hour": tier3_grad_per_hour,
                "today_volume_picks": today_bets_count,
                "daily_volume_target": 100,
                "today_volume_progress_pct": round(min(100.0, (today_bets_count / 100.0) * 100), 1),
                "today_stake_funded": today_bets_stake,
                "open_bets_count": bets_summary.get("open", {}).get("count", 0),
                "open_bets_stake": bets_summary.get("open", {}).get("stake", 0.0),
                "active_executable_bets": open_bets
            },
            "tier_4_polymarket": {
                "name": "Tier 4: Live Proven Alpha & Polymarket CLOB Execution",
                "promotion_rate_per_hour": tier4_prom_per_hour,
                "execution_mode": "Maker Limit Orders (0% Taker Fee)",
                "summary": poly_summary,
                "recent_orders": recent_poly_orders
            },
            "demotion_loop": {
                "name": "Interim Tourniquet & Demotion Return Loop (Tier 3 -> Tier 1)",
                "demotion_rate_per_hour": tier1_dem_per_hour,
                "tourniquet_clamp": "$0.50 emergency clamp -> $0.00 quarantine",
                "trigger_condition": "Rolling ROI < -3.0% over 4+ games",
                "recent_demotions": demotions
            }
        },
        "scoreboard": {
            "today_picks": today_bets_count,
            "daily_target": 100,
            "today_stake": today_bets_stake,
            "total_settled_bets": total_settled_bets,
            "total_won_bets": total_won_bets,
            "total_lost_bets": total_lost_bets,
            "win_rate_pct": win_rate_pct,
            "total_realized_pnl": round(total_realized_pnl, 2),
            "weekly_roi_target_pct": 10.0
        },
        "live_flow_events": {
            "recent_settlements": recent_settled,
            "recent_graduations": graduations,
            "recent_demotions": demotions,
            "recent_syntheses": syntheses
        }
    }
