"""Model Funnel Velocity & Flow Rate Telemetry Engine.

Measures and logs quantitative throughput and flow velocity across the 4-tier closed-loop lifecycle:
1. AI Model Synthesis Rate (models / hour synthesized in Tier 1 R&D by Claude/Codex)
2. Tier 2 Admission Rate (models / hour passing the Walk-Forward Crucible into Shadow Incubator)
3. Tier 3 Graduation Rate (markets / hour graduating from Incubator into live Stake execution)
4. Tier 1 Demotion Rate (markets / hour evicted from live execution into R&D quarantine)
5. Tier 4 Proven Alpha Promotion Rate (markets / hour qualifying for Polymarket CLOB execution)
6. Net Expansion Velocity (graduations/hour - demotions/hour) and Conversion Ratios.

Provides structured rolling telemetry over 1h, 6h, 24h, and 7d horizons, and persists
audit logs to `data/research/model_funnel_velocity.json` and `.jsonl`.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from contextlib import closing

from .domain import utcnow
from .io_utils import atomic_json
from .providers import DATA

log = logging.getLogger(__name__)

FUNNEL_VELOCITY_SNAPSHOT_FILE = "model_funnel_velocity.json"
FUNNEL_VELOCITY_LOG_FILE = "model_funnel_velocity.jsonl"


@dataclass
class WindowFunnelVelocity:
    window_hours: float
    window_label: str
    syntheses_count: int
    synthesis_rate_per_hour: float
    tier2_admissions_count: int
    tier2_admission_rate_per_hour: float
    tier3_graduations_count: int
    tier3_graduation_rate_per_hour: float
    tier1_demotions_count: int
    tier1_demotion_rate_per_hour: float
    tier4_promotions_count: int
    tier4_promotion_rate_per_hour: float
    net_graduation_velocity_per_hour: float
    crucible_pass_rate_pct: float
    graduation_conversion_rate_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModelFunnelTelemetry:
    timestamp: str
    active_inventory: Dict[str, int]
    windows: Dict[str, WindowFunnelVelocity]
    hourly_velocity: Dict[str, float]
    diagnostics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "active_inventory": self.active_inventory,
            "windows": {k: v.to_dict() for k, v in self.windows.items()},
            "hourly_velocity": self.hourly_velocity,
            "diagnostics": self.diagnostics,
        }


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    if not den or den <= 0.0:
        return default
    return round(num / den, 4)


def compute_model_funnel_velocity(
    data_dir: Optional[Path | str] = None,
    as_of: Optional[datetime] = None,
) -> ModelFunnelTelemetry:
    """Computes comprehensive quantitative flow rates and funnel velocity metrics."""
    base_dir = Path(data_dir or DATA)
    now_dt = as_of or utcnow()
    now_iso = now_dt.isoformat()

    incubator_db = base_dir / "shadow_incubator.sqlite3"
    engine_db = base_dir / "engine.sqlite3"

    inventory = {
        "tier1_research_training_count": 0,
        "tier2_shadow_incubator_count": 0,
        "tier3_production_graduated_count": 0,
        "tier4_live_proven_alpha_count": 0,
        "total_supported_universe_count": 0,
    }

    # Time boundaries
    windows_def = {
        "1h": (1.0, now_dt - timedelta(hours=1)),
        "6h": (6.0, now_dt - timedelta(hours=6)),
        "24h": (24.0, now_dt - timedelta(hours=24)),
        "7d": (168.0, now_dt - timedelta(days=7)),
    }

    raw_window_counts: Dict[str, Dict[str, int]] = {
        k: {
            "syntheses": 0,
            "tier2_admissions": 0,
            "tier3_graduations": 0,
            "tier1_demotions": 0,
            "tier4_promotions": 0,
        }
        for k in windows_def
    }

    diagnostics: Dict[str, Any] = {
        "incubator_db_accessible": False,
        "engine_db_accessible": False,
        "latest_synthesis_timestamp": None,
        "latest_graduation_timestamp": None,
        "latest_demotion_timestamp": None,
    }

    if incubator_db.exists():
        try:
            with closing(sqlite3.connect(f"file:{incubator_db}?mode=ro", uri=True, timeout=10.0)) as inc_conn:
                cur = inc_conn.cursor()
                diagnostics["incubator_db_accessible"] = True

                # Current Tier 1 & 2 Inventory
                phases = cur.execute("""
                    SELECT incubation_phase, count(*) 
                    FROM demoted_markets 
                    GROUP BY incubation_phase
                """).fetchall()
                for p_name, cnt in phases:
                    if p_name == "incubating_with_new_model":
                        inventory["tier2_shadow_incubator_count"] = cnt
                    elif p_name == "research_training":
                        inventory["tier1_research_training_count"] = cnt

                # Current Tier 3 Inventory
                t3_count = cur.execute("SELECT count(*) FROM graduated_markets").fetchone()[0]
                inventory["tier3_production_graduated_count"] = t3_count
                inventory["total_supported_universe_count"] = (
                    inventory["tier1_research_training_count"]
                    + inventory["tier2_shadow_incubator_count"]
                    + inventory["tier3_production_graduated_count"]
                )

                # Latest Timestamps
                cur.execute("SELECT max(created_at) FROM ai_synthesis_feedback_loop")
                diagnostics["latest_synthesis_timestamp"] = cur.fetchone()[0]

                cur.execute("SELECT max(graduated_at) FROM graduated_markets")
                diagnostics["latest_graduation_timestamp"] = cur.fetchone()[0]

                cur.execute("SELECT max(demoted_at) FROM demoted_markets")
                diagnostics["latest_demotion_timestamp"] = cur.fetchone()[0]

                # Rolling Window Queries
                for w_key, (w_hours, start_dt) in windows_def.items():
                    start_iso = start_dt.isoformat()

                    # 1. Syntheses in window
                    synth_cnt = cur.execute(
                        "SELECT count(*) FROM ai_synthesis_feedback_loop WHERE created_at >= ?",
                        (start_iso,),
                    ).fetchone()[0]
                    raw_window_counts[w_key]["syntheses"] = int(synth_cnt or 0)

                    # 2. Tier 2 Admissions (Crucible passed)
                    t2_cnt = cur.execute(
                        "SELECT count(*) FROM ai_synthesis_feedback_loop WHERE crucible_passed = 1 AND created_at >= ?",
                        (start_iso,),
                    ).fetchone()[0]
                    raw_window_counts[w_key]["tier2_admissions"] = int(t2_cnt or 0)

                    # 3. Tier 3 Graduations
                    t3_cnt = cur.execute(
                        "SELECT count(*) FROM graduated_markets WHERE graduated_at >= ?",
                        (start_iso,),
                    ).fetchone()[0]
                    raw_window_counts[w_key]["tier3_graduations"] = int(t3_cnt or 0)

                    # 4. Tier 1 Demotions (quarantine)
                    dem_cnt = cur.execute(
                        "SELECT count(*) FROM demoted_markets WHERE demoted_at >= ?",
                        (start_iso,),
                    ).fetchone()[0]
                    raw_window_counts[w_key]["tier1_demotions"] = int(dem_cnt or 0)

        except Exception as exc:
            log.warning("Could not read incubator db for funnel velocity: %s", exc)

    # Ingest Tier 4 Alpha from Engine DB / SegmentScoreboard
    if engine_db.exists():
        try:
            from .segment_scoreboard import compute_segment_scoreboard
            with closing(sqlite3.connect(f"file:{engine_db}?mode=ro", uri=True, timeout=5.0)) as eng_conn:
                diagnostics["engine_db_accessible"] = True
                sb = compute_segment_scoreboard(eng_conn)
                alpha_segs = sb.get("alpha_segments", [])
                inventory["tier4_live_proven_alpha_count"] = len(alpha_segs)

                # Tier 4 promotions in window (alpha segments with earliest positive decision in window)
                for w_key, (w_hours, start_dt) in windows_def.items():
                    start_iso = start_dt.isoformat()
                    cur_eng = eng_conn.cursor()
                    prom_cnt = cur_eng.execute("""
                        SELECT count(DISTINCT family) 
                        FROM system_decisions 
                        WHERE status = 'won' AND issued_at >= ? AND pnl > 0.0
                    """, (start_iso,)).fetchone()[0]
                    # Bounded by total alpha segments count
                    raw_window_counts[w_key]["tier4_promotions"] = min(int(prom_cnt or 0), len(alpha_segs))
        except Exception as exc:
            log.warning("Could not read engine db for Tier 4 funnel velocity: %s", exc)

    # Assemble Structured Window Velocities
    windows_output: Dict[str, WindowFunnelVelocity] = {}
    for w_key, (w_hours, _) in windows_def.items():
        counts = raw_window_counts[w_key]
        n_synth = counts["syntheses"]
        n_t2 = counts["tier2_admissions"]
        n_t3 = counts["tier3_graduations"]
        n_dem = counts["tier1_demotions"]
        n_t4 = counts["tier4_promotions"]

        rate_synth = round(n_synth / w_hours, 2)
        rate_t2 = round(n_t2 / w_hours, 2)
        rate_t3 = round(n_t3 / w_hours, 2)
        rate_dem = round(n_dem / w_hours, 2)
        rate_t4 = round(n_t4 / w_hours, 2)
        net_vel = round(rate_t3 - rate_dem, 2)

        pass_rate = round((n_t2 / n_synth * 100.0), 2) if n_synth > 0 else 0.0
        grad_conv = round((n_t3 / n_t2 * 100.0), 2) if n_t2 > 0 else 0.0

        windows_output[w_key] = WindowFunnelVelocity(
            window_hours=w_hours,
            window_label=w_key,
            syntheses_count=n_synth,
            synthesis_rate_per_hour=rate_synth,
            tier2_admissions_count=n_t2,
            tier2_admission_rate_per_hour=rate_t2,
            tier3_graduations_count=n_t3,
            tier3_graduation_rate_per_hour=rate_t3,
            tier1_demotions_count=n_dem,
            tier1_demotion_rate_per_hour=rate_dem,
            tier4_promotions_count=n_t4,
            tier4_promotion_rate_per_hour=rate_t4,
            net_graduation_velocity_per_hour=net_vel,
            crucible_pass_rate_pct=pass_rate,
            graduation_conversion_rate_pct=grad_conv,
        )

    # 24h baseline as canonical hourly velocity
    h24 = windows_output["24h"]
    h1 = windows_output["1h"]
    hourly_velocity = {
        "models_synthesized_per_hour_24h": h24.synthesis_rate_per_hour,
        "models_synthesized_per_hour_1h": h1.synthesis_rate_per_hour,
        "tier2_admissions_per_hour_24h": h24.tier2_admission_rate_per_hour,
        "tier2_admissions_per_hour_1h": h1.tier2_admission_rate_per_hour,
        "tier3_graduations_per_hour_24h": h24.tier3_graduation_rate_per_hour,
        "tier3_graduations_per_hour_1h": h1.tier3_graduation_rate_per_hour,
        "tier1_demotions_per_hour_24h": h24.tier1_demotion_rate_per_hour,
        "tier1_demotions_per_hour_1h": h1.tier1_demotion_rate_per_hour,
        "tier4_promotions_per_hour_24h": h24.tier4_promotion_rate_per_hour,
        "net_expansion_velocity_per_hour_24h": h24.net_graduation_velocity_per_hour,
        "crucible_pass_rate_pct_24h": h24.crucible_pass_rate_pct,
    }

    return ModelFunnelTelemetry(
        timestamp=now_iso,
        active_inventory=inventory,
        windows=windows_output,
        hourly_velocity=hourly_velocity,
        diagnostics=diagnostics,
    )


def record_funnel_velocity_telemetry(
    data_dir: Optional[Path | str] = None,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Computes, persists, and logs the current funnel flow rate telemetry."""
    base_dir = Path(data_dir or DATA)
    research_dir = base_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    telemetry = compute_model_funnel_velocity(data_dir=base_dir, as_of=as_of)
    payload = telemetry.to_dict()

    snapshot_path = research_dir / FUNNEL_VELOCITY_SNAPSHOT_FILE
    log_path = research_dir / FUNNEL_VELOCITY_LOG_FILE

    # 1. Atomic Snapshot Write
    try:
        atomic_json(snapshot_path, payload)
    except Exception as exc:
        log.warning("Could not write funnel velocity snapshot to %s: %s", snapshot_path, exc)

    # 2. Append to Continuous JSONL Log
    try:
        line_entry = {
            "timestamp": payload["timestamp"],
            "hourly_velocity": payload["hourly_velocity"],
            "active_inventory": payload["active_inventory"],
            "windows_summary": {
                w: {
                    "synth_rate": v["synthesis_rate_per_hour"],
                    "t2_rate": v["tier2_admission_rate_per_hour"],
                    "t3_rate": v["tier3_graduation_rate_per_hour"],
                    "dem_rate": v["tier1_demotion_rate_per_hour"],
                    "pass_rate_pct": v["crucible_pass_rate_pct"],
                }
                for w, v in payload["windows"].items()
            },
        }
        with open(log_path, "a", encoding="utf-8") as f_log:
            f_log.write(json.dumps(line_entry) + "\n")
    except Exception as exc:
        log.warning("Could not append funnel velocity to %s: %s", log_path, exc)

    return payload


def get_latest_funnel_velocity_report(data_dir: Optional[Path | str] = None) -> Dict[str, Any]:
    """Retrieves the latest persisted funnel velocity report, generating fresh if missing or stale."""
    base_dir = Path(data_dir or DATA)
    snapshot_path = base_dir / "research" / FUNNEL_VELOCITY_SNAPSHOT_FILE

    if snapshot_path.exists():
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f_snap:
                data = json.load(f_snap)
                # Verify freshness (if within 60s, return cached)
                ts = data.get("timestamp")
                if ts:
                    record_dt = datetime.fromisoformat(ts)
                    if (utcnow() - record_dt).total_seconds() < 60.0:
                        return data
        except Exception:
            pass

    return record_funnel_velocity_telemetry(data_dir=base_dir)
