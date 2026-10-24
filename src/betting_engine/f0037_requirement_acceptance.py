"""Milestone M08 Full Requirement Acceptance & Compliance Engine.

Implements:
1. DailyComplianceLedger: Budapest timezone day-bucketing, DST transition handling,
   distinct recommendation tracking, and immutable shortfall retention against
   daily targets (15 floor, 20 prime, 100 mandate floor, 700 rolling weekly).
2. DualDynamicCapitalPolicy: Dual dynamic capital allocation with alpha acceleration
   ($2.50-$8.00), capital defense ($0.50 clamp on wobbling segments), and capital
   quarantine ($0.00 on demoted markets).
3. ThreeTierClosedLoopRecovery: 3-tier closed-loop recovery requiring offline settled
   training (>= +3.5% out-of-sample ROI, positive Brier skill) before Shadow Incubator
   admission ($0.00), and N >= 25 live settled games with sustained profit before
   graduation to real money.
4. ClusterRobustEconomicEvaluator: Game-clustered bootstrap confidence intervals for
   ROI and PnL, strict non-guaranteed profit invariants, and honest reporting of
   target shortfalls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import random
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

BUDAPEST_TZ = ZoneInfo("Europe/Budapest")


@dataclass(frozen=True)
class DistinctRecommendation:
    game_id: str
    market: str
    selection: str
    probability: float
    reference_odds: float
    expected_roi: float
    timestamp: datetime
    stake: float = 0.0

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.game_id, self.market, self.selection)


@dataclass
class DailyComplianceReport:
    budapest_date: str
    distinct_funded_count: int
    threshold_15_status: str
    threshold_15_shortfall: int
    threshold_20_status: str
    threshold_20_shortfall: int
    threshold_100_status: str
    threshold_100_shortfall: int
    weekly_rolling_count: int
    weekly_700_status: str
    weekly_700_shortfall: int
    retained_shortfalls: Dict[str, int]
    padded_with_unjustified: bool = False
    evidence_bar_lowered: bool = False


class DailyComplianceLedger:
    """Budapest-aligned daily compliance ledger with quota-safe admission."""

    def __init__(self, targets: Optional[Dict[str, int]] = None):
        self.targets = targets or {
            "daily_floor": 15,
            "daily_prime": 20,
            "daily_mandate": 100,
            "weekly_rolling": 700,
        }
        self._history: Dict[str, List[DistinctRecommendation]] = {}

    def get_budapest_date(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        bp_dt = dt.astimezone(BUDAPEST_TZ)
        return bp_dt.strftime("%Y-%m-%d")

    def record_recommendations(
        self,
        recommendations: List[Dict[str, Any]],
        filter_passing: bool = True
    ) -> List[DistinctRecommendation]:
        admitted: List[DistinctRecommendation] = []
        seen_keys: Set[Tuple[str, str, str]] = set()

        for r in recommendations:
            if filter_passing and not r.get("passed_all_gates", False):
                continue
            ts = r.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            elif not isinstance(ts, datetime):
                ts = datetime.now(timezone.utc)

            rec = DistinctRecommendation(
                game_id=str(r["game_id"]),
                market=str(r["market"]),
                selection=str(r["selection"]),
                probability=float(r["probability"]),
                reference_odds=float(r["reference_odds"]),
                expected_roi=float(r.get("expected_roi", 0.0)),
                timestamp=ts,
                stake=float(r.get("stake", 0.0))
            )

            if rec.key not in seen_keys:
                seen_keys.add(rec.key)
                b_date = self.get_budapest_date(rec.timestamp)
                self._history.setdefault(b_date, []).append(rec)
                admitted.append(rec)

        return admitted

    def evaluate_compliance(
        self,
        budapest_date: str,
        trailing_days: int = 7
    ) -> DailyComplianceReport:
        day_recs = self._history.get(budapest_date, [])
        distinct_funded = len([r for r in day_recs if r.stake > 0.0])

        dates = sorted(self._history.keys())
        rolling_count = 0
        if budapest_date in dates:
            idx = dates.index(budapest_date)
            window = dates[max(0, idx - trailing_days + 1):idx + 1]
            for d in window:
                rolling_count += len([r for r in self._history.get(d, []) if r.stake > 0.0])
        else:
            rolling_count = distinct_funded

        t15 = self.targets["daily_floor"]
        t20 = self.targets["daily_prime"]
        t100 = self.targets["daily_mandate"]
        t700 = self.targets["weekly_rolling"]

        s15 = max(0, t15 - distinct_funded)
        s20 = max(0, t20 - distinct_funded)
        s100 = max(0, t100 - distinct_funded)
        s700 = max(0, t700 - rolling_count)

        shortfalls = {
            "daily_15": s15,
            "daily_20": s20,
            "daily_100": s100,
            "weekly_700": s700,
        }

        return DailyComplianceReport(
            budapest_date=budapest_date,
            distinct_funded_count=distinct_funded,
            threshold_15_status="compliant" if s15 == 0 else "shortfall_retained",
            threshold_15_shortfall=s15,
            threshold_20_status="compliant" if s20 == 0 else "shortfall_retained",
            threshold_20_shortfall=s20,
            threshold_100_status="compliant" if s100 == 0 else "shortfall_retained",
            threshold_100_shortfall=s100,
            weekly_rolling_count=rolling_count,
            weekly_700_status="compliant" if s700 == 0 else "shortfall_retained",
            weekly_700_shortfall=s700,
            retained_shortfalls=shortfalls,
            padded_with_unjustified=False,
            evidence_bar_lowered=False
        )


class DualDynamicCapitalPolicy:
    """Dual dynamic capital allocation policy."""

    ALPHA_MIN = 2.50
    ALPHA_MAX = 8.00
    DEFENSE_CLAMP = 0.50
    QUARANTINE_STAKE = 0.00

    @classmethod
    def allocate_capital(
        cls,
        segment: str,
        tier: str,
        is_quarantined: bool = False,
        wobbling: bool = False,
        base_stake: float = 3.00
    ) -> Dict[str, Any]:
        if is_quarantined or tier in ("DEMOTED", "QUARANTINE"):
            return {
                "segment": segment,
                "tier": "QUARANTINE",
                "stake": cls.QUARANTINE_STAKE,
                "mode": "quarantine_defense",
                "allocation_status": "incubator_re_graduation"
            }

        if wobbling or tier == "CAPITAL_DEFENSE":
            return {
                "segment": segment,
                "tier": "CAPITAL_DEFENSE",
                "stake": cls.DEFENSE_CLAMP,
                "mode": "capital_defense_clamp",
                "allocation_status": "defended"
            }

        if tier == "PROBATION":
            return {
                "segment": segment,
                "tier": "PROBATION",
                "stake": cls.DEFENSE_CLAMP,
                "mode": "probation_nursery",
                "allocation_status": "nursery"
            }

        clamped_stake = max(cls.ALPHA_MIN, min(cls.ALPHA_MAX, base_stake))
        return {
            "segment": segment,
            "tier": tier,
            "stake": clamped_stake,
            "mode": "alpha_acceleration",
            "allocation_status": "scaled"
        }


class ThreeTierClosedLoopRecovery:
    """3-tier closed-loop recovery protocol for demoted and candidate models."""

    MIN_OFFLINE_GAMES = 97000
    MIN_OUT_OF_SAMPLE_ROI = 0.035
    MIN_INCUBATOR_LIVE_GAMES = 25

    @classmethod
    def evaluate_tier1_offline_candidate(
        cls,
        trained_games: int,
        out_of_sample_roi: float,
        brier_skill_score: float
    ) -> Dict[str, Any]:
        eligible = (
            trained_games >= cls.MIN_OFFLINE_GAMES
            and out_of_sample_roi >= cls.MIN_OUT_OF_SAMPLE_ROI
            and brier_skill_score > 0.0
        )
        return {
            "tier": 1,
            "status": "admitted_to_shadow_incubator" if eligible else "rejected",
            "eligible_for_shadow_incubator": eligible,
            "trained_games": trained_games,
            "out_of_sample_roi": out_of_sample_roi,
            "brier_skill_score": brier_skill_score,
            "shadow_stake": 0.00 if eligible else None
        }

    @classmethod
    def evaluate_tier2_shadow_graduation(
        cls,
        live_settled_games: int,
        live_pnl: float,
        live_roi: float
    ) -> Dict[str, Any]:
        graduated = (
            live_settled_games >= cls.MIN_INCUBATOR_LIVE_GAMES
            and live_pnl > 0.0
            and live_roi > 0.0
        )
        return {
            "tier": 2,
            "status": "graduated_to_real_money" if graduated else "remain_in_incubator",
            "graduated": graduated,
            "live_settled_games": live_settled_games,
            "live_pnl": live_pnl,
            "live_roi": live_roi,
            "recommended_initial_stake": 0.50 if graduated else 0.00
        }


class ClusterRobustEconomicEvaluator:
    """Cluster-robust bootstrap economic evaluator without profit guarantees."""

    @classmethod
    def evaluate(
        cls,
        bets: List[Dict[str, Any]],
        n_bootstraps: int = 500,
        cluster_key: str = "game_id",
        alpha: float = 0.05
    ) -> Dict[str, Any]:
        if not bets:
            return {
                "n_bets": 0,
                "total_staked": 0.0,
                "net_pnl": 0.0,
                "roi": 0.0,
                "ci_lower_roi": 0.0,
                "ci_upper_roi": 0.0,
                "profit_guaranteed": False,
                "status": "no_data"
            }

        total_staked = sum(b.get("stake", 0.0) for b in bets)
        net_pnl = sum(b.get("pnl", 0.0) for b in bets)
        roi = net_pnl / total_staked if total_staked > 0 else 0.0

        clusters: Dict[str, List[Dict[str, Any]]] = {}
        for b in bets:
            cid = str(b.get(cluster_key, b.get("id", "")))
            clusters.setdefault(cid, []).append(b)

        cluster_ids = list(clusters.keys())
        rng = random.Random(42)
        bootstrap_rois = []

        for _ in range(n_bootstraps):
            sampled_ids = [rng.choice(cluster_ids) for _ in range(len(cluster_ids))]
            s_stake = sum(sum(b.get("stake", 0.0) for b in clusters[cid]) for cid in sampled_ids)
            s_pnl = sum(sum(b.get("pnl", 0.0) for b in clusters[cid]) for cid in sampled_ids)
            if s_stake > 0:
                bootstrap_rois.append(s_pnl / s_stake)

        bootstrap_rois.sort()
        if bootstrap_rois:
            low_idx = int(alpha / 2 * len(bootstrap_rois))
            high_idx = int((1 - alpha / 2) * len(bootstrap_rois))
            ci_lower = bootstrap_rois[max(0, low_idx)]
            ci_upper = bootstrap_rois[min(len(bootstrap_rois) - 1, high_idx)]
        else:
            ci_lower, ci_upper = roi, roi

        status = "target_met" if roi >= 0.05 else ("profitable" if roi > 0 else "target_missed")

        return {
            "n_bets": len(bets),
            "n_clusters": len(clusters),
            "total_staked": round(total_staked, 2),
            "net_pnl": round(net_pnl, 2),
            "roi": round(roi, 4),
            "ci_lower_roi": round(ci_lower, 4),
            "ci_upper_roi": round(ci_upper, 4),
            "profit_guaranteed": False,
            "status": status
        }
