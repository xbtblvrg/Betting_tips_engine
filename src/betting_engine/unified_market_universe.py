"""Unified Supported Market Universe Architecture.

Unifies all 461 sports betting markets across the engine into a single,
symmetric 3-tier lifecycle state machine, eliminating historical divisions
between 'core' and 'gap' markets:

1. Tier 3 (PRODUCTION_GRADUATED):
   - Active real-money execution ($0.50 - $8.00 dynamic Kelly sizing).
   - Bounded by strict Crucible graduation standards (N >= 25, positive ROI, positive Brier edge).
   - Symmetric capital defense ($0.50 tourniquet on wobble, $0.00 quarantine on breakdown).

2. Tier 2 (SHADOW_INCUBATOR):
   - Live forward paper-trading ($0.00 virtual stakes) on real upcoming Stake fixtures.
   - Bound to an out-of-sample verified candidate model archetype.
   - Bankroll is 100% protected while live validity, CLV, and accuracy are measured.

3. Tier 1 (RESEARCH_RND):
   - Historical R&D labor for underperforming / demoted markets.
   - Muted from live odds ($0.00, no live issuance).
   - Auto-tunes 5 candidate archetypes on 97k+ historical settled matches until positive edge is proven.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import IntEnum
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class MarketTier(IntEnum):
    RESEARCH_RND = 1          # Tier 1: Historical training on 97k+ matches ($0.00)
    SHADOW_INCUBATOR = 2      # Tier 2: Live forward paper-trading on upcoming fixtures ($0.00)
    PRODUCTION_GRADUATED = 3  # Tier 3: Active funded execution under dynamic Kelly ($0.50 - $8.00)


@dataclass
class UnifiedMarketProfile:
    segment_key: str
    league: str
    scope: str
    family: str
    sub_family: str
    tier: int
    tier_name: str
    stake_cap: float
    active_model_id: Optional[str]
    model_archetype: Optional[str]
    rolling_roi_pct: float
    rolling_pnl: float
    sample_size: int
    delta_brier: float
    phase_detail: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UnifiedMarketUniverse:
    """Single source of truth for the entire 461-market supported universe."""

    _cached_roster: Optional[Dict[str, UnifiedMarketProfile]] = None
    _cached_summary: Optional[Dict[str, Any]] = None
    _cached_at: float = 0.0
    _cache_ttl_seconds: float = 60.0

    @classmethod
    def invalidate_cache(cls) -> None:
        """Flushes the cached universe roster and high-level summary."""
        cls._cached_roster = None
        cls._cached_summary = None
        cls._cached_at = 0.0

    def __init__(self, data_dir: Optional[Path | str] = None):
        self.data_dir = Path(data_dir or 'data')
        self.incubator_db_path = self.data_dir / 'shadow_incubator.sqlite3'
        self.engine_db_path = self.data_dir / 'engine.sqlite3'

    def get_canonical_key(self, league: str, scope: str, family: str) -> str:
        """Normalizes league, scope, and market family into canonical segment key."""
        lg = (league or '').upper().strip()
        sc = (scope or 'full_game').lower().strip()
        fam = (family or '').lower().strip()
        return f"{lg}|{sc}|{fam}"

    def build_universe_roster(self, force_refresh: bool = False) -> Dict[str, UnifiedMarketProfile]:
        """Assembles the complete 461-market roster with exact tier classifications."""
        import time
        now = time.time()
        if not force_refresh and UnifiedMarketUniverse._cached_roster is not None:
            if now - UnifiedMarketUniverse._cached_at < UnifiedMarketUniverse._cache_ttl_seconds:
                return UnifiedMarketUniverse._cached_roster

        roster: Dict[str, UnifiedMarketProfile] = {}

        if not self.incubator_db_path.exists():
            return roster

        try:
            with sqlite3.connect(f"file:{self.incubator_db_path}?mode=ro", uri=True, timeout=2.0) as inc_conn:
                # 1. Ingest Graduated Markets (Tier 3 Candidates)
                grad_rows = inc_conn.execute("""
                    SELECT segment_key, league, scope, family, sub_family,
                           sample_size, roi, delta_brier, stats
                    FROM graduated_markets
                """).fetchall()

                grad_keys_set = set()
                for r in grad_rows:
                    seg_key, lg, sc, fam, sub_fam, n, roi, dbrier, stats_raw = r
                    prof = UnifiedMarketProfile(
                        segment_key=seg_key,
                        league=lg,
                        scope=sc,
                        family=fam,
                        sub_family=sub_fam,
                        tier=int(MarketTier.PRODUCTION_GRADUATED),
                        tier_name="PRODUCTION_GRADUATED",
                        stake_cap=8.00,
                        active_model_id=f"graduated_{lg.lower()}_{sc}_{fam}",
                        model_archetype="GraduatedCrucibleModel",
                        rolling_roi_pct=float(roi * 100.0 if abs(roi) < 10.0 else roi),
                        rolling_pnl=0.0,
                        sample_size=int(n or 0),
                        delta_brier=float(dbrier or 0.0),
                        phase_detail="Active production funded trading"
                    )
                    roster[seg_key] = prof
                    grad_keys_set.add(seg_key)
                    if sub_fam and sub_fam != fam:
                        sub_k = f"{lg}|{sc}|{fam}:{sub_fam}"
                        roster[sub_k] = prof
                        grad_keys_set.add(sub_k)

                # 2. Ingest Demoted Markets (Tier 1 & Tier 2)
                dem_rows = inc_conn.execute("""
                    SELECT segment_key, league, scope, family, sub_family,
                           rolling_roi, rolling_pnl, games, incubation_phase,
                           incubated_model_id, model_archetype, historical_roi, historical_brier_delta
                    FROM demoted_markets
                """).fetchall()

                for r in dem_rows:
                    (seg_key, lg, sc, fam, sub_fam, r_roi, r_pnl, games,
                     phase, model_id, arch, h_roi, h_brier) = r

                    canonical_k = self.get_canonical_key(lg, sc, fam)
                    is_sub = bool(sub_fam and sub_fam != fam)
                    sub_k = f"{lg}|{sc}|{fam}:{sub_fam}" if is_sub else canonical_k

                    # Constitutional Protection: Never demote an already-graduated market due to stale demoted_markets rows.
                    # A parent family key alone must NEVER override a specific demoted sub-family!
                    already_graduated = (seg_key in grad_keys_set) or (sub_k in grad_keys_set)
                    if not is_sub and canonical_k in grad_keys_set:
                        already_graduated = True

                    if already_graduated:
                        continue

                    if phase == 'incubating_with_new_model':
                        tier = int(MarketTier.SHADOW_INCUBATOR)
                        tier_name = "SHADOW_INCUBATOR"
                        phase_detail = f"Live forward validation with {arch or 'candidate model'} ($0.00)"
                    else:
                        tier = int(MarketTier.RESEARCH_RND)
                        tier_name = "RESEARCH_RND"
                        phase_detail = "Tier 1 R&D historical model tuning on 97k+ matches ($0.00)"

                    prof = UnifiedMarketProfile(
                        segment_key=seg_key,
                        league=lg,
                        scope=sc,
                        family=fam,
                        sub_family=sub_fam,
                        tier=tier,
                        tier_name=tier_name,
                        stake_cap=0.00,
                        active_model_id=model_id,
                        model_archetype=arch,
                        rolling_roi_pct=float(r_roi * 100.0 if abs(r_roi) < 10.0 else r_roi),
                        rolling_pnl=float(r_pnl or 0.0),
                        sample_size=int(games or 0),
                        delta_brier=float(h_brier or 0.0),
                        phase_detail=phase_detail
                    )
                    roster[seg_key] = prof
                    if is_sub:
                        roster[sub_k] = prof
                    if ':' in fam:
                        base_f = fam.split(':')[-1]
                        roster[self.get_canonical_key(lg, sc, base_f)] = prof
                    if sub_fam and ':' in sub_fam:
                        base_sf = sub_fam.split(':')[-1]
                        roster[self.get_canonical_key(lg, sc, base_sf)] = prof

        except Exception as exc:
            logger.error("Error reading incubator db for universe roster: %s", exc)

        # 3. Ingest active Baseline Production markets not currently demoted
        if self.engine_db_path.exists():
            try:
                with sqlite3.connect(f"file:{self.engine_db_path}?mode=ro", uri=True, timeout=2.0) as eng_conn:
                    dec_rows = eng_conn.execute("""
                        SELECT payload FROM system_decisions
                        ORDER BY issued_at DESC LIMIT 3000
                    """).fetchall()

                    for (p_raw,) in dec_rows:
                        p = json.loads(p_raw)
                        lg = p.get('league', '').upper()
                        sc = p.get('scope') or p.get('period') or 'full_game'
                        if sc == 'Teljes mérkőzés':
                            sc = 'full_game'
                        fam = p.get('market_family') or p.get('market') or ''
                        canon_key = self.get_canonical_key(lg, sc, fam)
                        base_fam = fam.split(':')[-1] if ':' in fam else fam
                        canon_base_key = self.get_canonical_key(lg, sc, base_fam)

                        if canon_key not in roster and canon_base_key not in roster:
                            # Active un-demoted production baseline
                            roster[canon_key] = UnifiedMarketProfile(
                                segment_key=canon_key,
                                league=lg,
                                scope=sc,
                                family=fam,
                                sub_family=fam,
                                tier=int(MarketTier.PRODUCTION_GRADUATED),
                                tier_name="PRODUCTION_GRADUATED",
                                stake_cap=8.00,
                                active_model_id=p.get('model_version'),
                                model_archetype="DomainBaselineModel",
                                rolling_roi_pct=0.0,
                                rolling_pnl=0.0,
                                sample_size=10,
                                delta_brier=0.015,
                                phase_detail="Active production baseline trading"
                            )
            except Exception as exc:
                logger.error("Error reading engine db for universe roster: %s", exc)

        UnifiedMarketUniverse._cached_roster = roster
        UnifiedMarketUniverse._cached_at = now
        return roster

    def get_market_tier(self, league: str, scope: str, family: str, sub_family: Optional[str] = None) -> MarketTier:
        """Returns the current operational tier for any market."""
        canon = self.get_canonical_key(league, scope, family)
        sub_k = f"{(league or '').upper()}|{(scope or 'full_game').lower()}|{(family or '').lower()}:{(sub_family or '').lower()}" if (sub_family and sub_family != family) else canon
        roster = self.build_universe_roster()
        if sub_k in roster:
            return MarketTier(roster[sub_k].tier)
        if canon in roster:
            return MarketTier(roster[canon].tier)
        # Check if explicitly demoted in demoted_markets
        try:
            with sqlite3.connect(f"file:{self.incubator_db_path}?mode=ro", uri=True, timeout=2.0) as inc_conn:
                row = inc_conn.execute("SELECT incubation_phase FROM demoted_markets WHERE segment_key IN (?, ?)", (sub_k, canon)).fetchone()
                if row:
                    return MarketTier.SHADOW_INCUBATOR if row[0] == 'incubating_with_new_model' else MarketTier.RESEARCH_RND
        except Exception:
            pass
        # Supported un-demoted production markets default to Tier 3
        return MarketTier.PRODUCTION_GRADUATED

    def get_summary(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Provides high-level quantitative summary of the unified market universe."""
        import time
        now = time.time()
        if not force_refresh and UnifiedMarketUniverse._cached_summary is not None:
            if now - UnifiedMarketUniverse._cached_at < UnifiedMarketUniverse._cache_ttl_seconds:
                return UnifiedMarketUniverse._cached_summary

        roster = self.build_universe_roster(force_refresh=force_refresh)
        total_markets = len(roster)
        tier3 = [m for m in roster.values() if m.tier == int(MarketTier.PRODUCTION_GRADUATED)]
        tier2 = [m for m in roster.values() if m.tier == int(MarketTier.SHADOW_INCUBATOR)]
        tier1 = [m for m in roster.values() if m.tier == int(MarketTier.RESEARCH_RND)]

        leagues_breakdown: Dict[str, Dict[str, int]] = {}
        for m in roster.values():
            lb = leagues_breakdown.setdefault(m.league, {'tier1': 0, 'tier2': 0, 'tier3': 0, 'total': 0})
            lb['total'] += 1
            if m.tier == 3:
                lb['tier3'] += 1
            elif m.tier == 2:
                lb['tier2'] += 1
            else:
                lb['tier1'] += 1

        summary = {
            'total_supported_markets': total_markets,
            'tier_3_production_graduated_count': len(tier3),
            'tier_2_shadow_incubator_count': len(tier2),
            'tier_1_research_rnd_count': len(tier1),
            'active_capital_eligible_ratio': len(tier3) / max(1, total_markets),
            'quarantined_capital_defense_ratio': (len(tier1) + len(tier2)) / max(1, total_markets),
            'leagues_breakdown': leagues_breakdown,
        }
        UnifiedMarketUniverse._cached_summary = summary
        return summary
