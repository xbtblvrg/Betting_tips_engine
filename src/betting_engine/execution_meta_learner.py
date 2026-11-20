"""Self-Optimizing Execution & Kelly Meta-Learner (CLV, Calibration & Odds-Regime Optimizer).

Learns continuous empirical alpha curves across odds regimes, pre-game lead-time horizons,
Brier calibration skill, and Closing Line Value (CLV) per market segment without hardcoded
step clamps (Commandments 1, 3, 4 & 10).

Provides:
- Continuous Gaussian kernel regression over log-odds xi = ln(max(1.01, odds - 0.40))
  with Empirical-Bayes shrinkage weight w_eb = N_eff / (N_eff + 15.0).
- Continuous priority score adjustment (meta_priority_boost in [-0.45, +0.55]).
- Continuous Kelly sizing multiplier (meta_kelly_multiplier in [0.65, 1.45]).
- Continuous adaptive EV qualification hurdle (meta_ev_hurdle in [0.012, 0.045]).
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from .contracts import allocation_family, split_market
from .providers import DATA

log = logging.getLogger(__name__)


def _log_odds_coord(odds: float) -> float:
    return math.log(max(1.01, float(odds) - 0.40))


class ExecutionMetaLearner:
    """Continuous Empirical-Bayes Meta-Learner for odds-regime, CLV, and Kelly execution optimization."""

    _cache_lock_ts: float = 0.0
    _cached_dir: Optional[str] = None
    _cached_observations: Optional[Dict[str, List[Tuple[float, float, float, float, float]]]] = None
    _cached_clv: Optional[Dict[str, Tuple[int, float]]] = None
    _cache_ttl: float = 60.0

    def __init__(self, data_dir: Path | str = DATA):
        self.data_dir = Path(data_dir) if isinstance(data_dir, str) else data_dir
        self.engine_db = self.data_dir / "engine.sqlite3"
        self.incubator_db = self.data_dir / "shadow_incubator.sqlite3"
        self.closing_eval_file = self.data_dir / "closing_evaluation.json"

    def _load_empirical_memory(
        self, force_refresh: bool = False
    ) -> Tuple[Dict[str, List[Tuple[float, float, float, float, float]]], Dict[str, Tuple[int, float]]]:
        """Loads settled wager observations and CLV records indexed by canonical segment key.

        Observation tuple format:
            (xi_log_odds, unit_roi, brier_delta, calibration_residual, weight)
        """
        now = time.monotonic()
        if (
            not force_refresh
            and ExecutionMetaLearner._cached_observations is not None
            and ExecutionMetaLearner._cached_clv is not None
            and ExecutionMetaLearner._cached_dir == str(self.data_dir)
            and (now - ExecutionMetaLearner._cache_lock_ts) < ExecutionMetaLearner._cache_ttl
        ):
            return ExecutionMetaLearner._cached_observations, ExecutionMetaLearner._cached_clv

        obs_map: Dict[str, List[Tuple[float, float, float, float, float]]] = {}
        clv_map: Dict[str, Tuple[int, float]] = {}

        # 1. Ingest live settled decisions from engine.sqlite3 (weight = 2.5x because live execution)
        if self.engine_db.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.engine_db.resolve()}?mode=ro", uri=True, timeout=5.0)) as conn:
                    rows = conn.execute("""
                        SELECT payload, status, pnl
                        FROM system_decisions
                        WHERE status IN ('won', 'lost')
                        ORDER BY issued_at DESC
                        LIMIT 2500
                    """).fetchall()
                    for p_raw, st, pnl_raw in rows:
                        p = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
                        if not isinstance(p, dict):
                            continue
                        lg = str(p.get('league') or '').upper().strip()
                        mkt = str(p.get('market') or '').lower().strip()
                        if not lg or not mkt:
                            continue
                        sc, base_m = split_market(mkt)
                        sc = str(p.get('model_scope') or sc or 'full_game').lower().strip()
                        fam = allocation_family(p) or base_m
                        od = max(1.05, float(p.get('reference_odds') or p.get('odds') or 2.0))
                        prob = max(0.01, min(0.99, float(p.get('probability') or (1.0 / od))))
                        mkt_p = max(0.01, min(0.99, float((p.get('market_anchor') or {}).get('market_probability') or (1.0 / od))))
                        stk = max(0.50, float(p.get('stake') or 1.0))
                        unit_roi = float(pnl_raw or 0.0) / stk
                        y = 1.0 if st == 'won' else 0.0
                        b_delta = ((mkt_p - y) ** 2) - ((prob - y) ** 2)
                        resid = y - prob
                        xi = _log_odds_coord(od)
                        rec = (xi, unit_roi, b_delta, resid, 2.5)
                        for k in {f"{lg}|{sc}|{fam}", f"{lg}|{sc}|{base_m}", f"{lg}|*|{fam}"}:
                            obs_map.setdefault(k, []).append(rec)
            except Exception as e_eng:
                log.debug("[META-LEARNER] Could not load live decisions: %s", e_eng)

        # 2. Ingest recent settled incubator wagers (weight = 1.0x, bounded per segment)
        if self.incubator_db.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.incubator_db.resolve()}?mode=ro", uri=True, timeout=5.0)) as iconn:
                    rows_inc = iconn.execute("""
                        SELECT league, scope, family, sub_family, odds, model_prob, market_prob, result, pnl
                        FROM incubated_wagers
                        WHERE status = 'settled' AND result IN ('won', 'lost')
                        ORDER BY rowid DESC
                        LIMIT 8000
                    """).fetchall()
                    for lg_i, sc_i, fam_i, sf_i, od_i, mp_i, mkp_i, res_i, pnl_i in rows_inc:
                        lg_u = str(lg_i or '').upper().strip()
                        sc_l = str(sc_i or 'full_game').lower().strip()
                        fam_l = str(fam_i or '').lower().strip()
                        sf_l = str(sf_i or fam_l).lower().strip()
                        if not lg_u or not fam_l:
                            continue
                        od = max(1.05, float(od_i or 2.0))
                        prob = max(0.01, min(0.99, float(mp_i if mp_i is not None else (1.0 / od))))
                        mkt_p = max(0.01, min(0.99, float(mkp_i if mkp_i is not None else (1.0 / od))))
                        y = 1.0 if res_i == 'won' else 0.0
                        unit_roi = float(pnl_i if pnl_i is not None else ((od - 1.0) if y == 1.0 else -1.0))
                        b_delta = ((mkt_p - y) ** 2) - ((prob - y) ** 2)
                        resid = y - prob
                        xi = _log_odds_coord(od)
                        rec = (xi, unit_roi, b_delta, resid, 1.0)
                        for k in {f"{lg_u}|{sc_l}|{fam_l}", f"{lg_u}|{sc_l}|{sf_l}"}:
                            bucket = obs_map.setdefault(k, [])
                            if len(bucket) < 250:
                                bucket.append(rec)
            except Exception as e_inc:
                log.debug("[META-LEARNER] Could not load incubator wagers: %s", e_inc)

        # 3. Ingest CLV evidence from closing_evaluation.json
        if self.closing_eval_file.exists():
            try:
                c_data = json.loads(self.closing_eval_file.read_text(encoding="utf-8"))
                clv_acc: Dict[str, List[float]] = {}
                for rep in c_data.get("reports", []):
                    clv_val = rep.get("price_clv")
                    if isinstance(clv_val, (int, float)) and math.isfinite(float(clv_val)):
                        lg_c = str(rep.get("league") or "").upper().strip()
                        mk_c = str(rep.get("market_key") or "full_game:moneyline").lower().strip()
                        if mk_c.startswith("full_game:"):
                            sc_c, base_c = "full_game", mk_c.split(":", 1)[1]
                        else:
                            sc_c, base_c = split_market(mk_c)
                            if ":" in base_c:
                                base_c = base_c.split(":")[-1]
                        for ck in {f"{lg_c}|{sc_c}|{base_c}", f"{lg_c}|*|*"}:
                            clv_acc.setdefault(ck, []).append(float(clv_val))
                for ck, vals in clv_acc.items():
                    if vals:
                        clv_map[ck] = (len(vals), sum(vals) / len(vals))
            except Exception as e_clv:
                log.debug("[META-LEARNER] Could not load closing_evaluation.json: %s", e_clv)

        ExecutionMetaLearner._cached_observations = obs_map
        ExecutionMetaLearner._cached_clv = clv_map
        ExecutionMetaLearner._cached_dir = str(self.data_dir)
        ExecutionMetaLearner._cache_lock_ts = now
        return obs_map, clv_map

    def evaluate_candidate_regime(
        self,
        tip: Dict[str, Any],
        force_refresh: bool = False,
    ) -> Dict[str, float]:
        """Evaluates continuous odds-regime, calibration reliability, and CLV execution signals for a candidate tip."""
        neutral = {
            'effective_sample_size': 0.0,
            'shrinkage_weight': 0.0,
            'kernel_roi': 0.0,
            'kernel_brier_delta': 0.0,
            'kernel_residual_bias': 0.0,
            'segment_clv': 0.0,
            'lead_time_factor': 1.0,
            'meta_priority_boost': 0.0,
            'meta_kelly_multiplier': 1.0,
            'meta_ev_hurdle': 0.020,
        }
        if not isinstance(tip, dict):
            return neutral

        lg = str(tip.get('league') or '').upper().strip()
        mkt = str(tip.get('market') or '').lower().strip()
        if not lg or not mkt:
            return neutral

        sc_split, base_m = split_market(mkt)
        sc = str(tip.get('model_scope') or sc_split or 'full_game').lower().strip()
        fam = allocation_family(tip) or base_m
        odds = max(1.05, float(tip.get('reference_odds') or tip.get('odds') or 2.0))
        xi_target = _log_odds_coord(odds)

        obs_map, clv_map = self._load_empirical_memory(force_refresh=force_refresh)
        records = (
            obs_map.get(f"{lg}|{sc}|{base_m}")
            or obs_map.get(f"{lg}|{sc}|{fam}")
            or obs_map.get(f"{lg}|*|{fam}")
            or []
        )
        clv_tuple = (
            clv_map.get(f"{lg}|{sc}|{base_m}")
            or clv_map.get(f"{lg}|{sc}|{fam}")
            or clv_map.get(f"{lg}|*|*")
        )

        # Lead-time continuous timing curve (deterministic from sealed tip timestamps)
        lead_hours = 12.0
        try:
            start_str = tip.get('start')
            ref_str = tip.get('created_at') or tip.get('quote_at')
            if start_str and ref_str:
                start_dt = datetime.fromisoformat(str(start_str).replace('Z', '+00:00'))
                ref_dt = datetime.fromisoformat(str(ref_str).replace('Z', '+00:00'))
                lead_hours = max(0.0, min(72.0, (start_dt - ref_dt).total_seconds() / 3600.0))
        except Exception:
            lead_hours = 12.0

        # Smooth Gaussian bell centered around 14h pre-game liquidity sweet spot (range [0.94, 1.06])
        lead_time_factor = round(0.96 + 0.08 * math.exp(-((lead_hours - 14.0) ** 2) / (2.0 * (18.0 ** 2))), 4)

        segment_clv = 0.0
        if clv_tuple:
            n_clv, mean_clv = clv_tuple
            clv_shrink = float(n_clv) / (float(n_clv) + 8.0)
            segment_clv = round(clv_shrink * max(-0.15, min(0.15, mean_clv)), 5)

        if not records:
            return {
                **neutral,
                'segment_clv': segment_clv,
                'lead_time_factor': lead_time_factor,
            }

        # Gaussian kernel regression over log-odds xi with bandwidth h = 0.35
        bandwidth_sq_2 = 2.0 * (0.35 ** 2)
        w_sum = 0.0
        roi_w_sum = 0.0
        brier_w_sum = 0.0
        resid_w_sum = 0.0

        for xi_i, roi_i, b_delta_i, resid_i, base_w in records:
            k_w = base_w * math.exp(-((xi_target - xi_i) ** 2) / bandwidth_sq_2)
            w_sum += k_w
            roi_w_sum += k_w * roi_i
            brier_w_sum += k_w * b_delta_i
            resid_w_sum += k_w * resid_i

        if w_sum <= 1e-6:
            return neutral

        # Empirical Bayes shrinkage w_eb = N_eff / (N_eff + 15.0)
        shrink_w = w_sum / (w_sum + 15.0)
        kernel_roi = shrink_w * (roi_w_sum / w_sum)
        kernel_brier_delta = shrink_w * (brier_w_sum / w_sum)
        kernel_residual_bias = shrink_w * (resid_w_sum / w_sum)

        # Overconfidence penalty when model systematically overpredicts win probability in this odds regime
        overconf_drag = max(0.0, -kernel_residual_bias - 0.02)

        raw_priority = (
            1.40 * kernel_roi
            + 4.00 * kernel_brier_delta
            + 1.50 * segment_clv
            - 1.80 * overconf_drag
            + (lead_time_factor - 1.0)
        )
        meta_priority_boost = round(max(-0.45, min(0.55, raw_priority)), 4)

        raw_kelly_mult = (
            1.0
            + 0.85 * kernel_roi
            + 3.00 * kernel_brier_delta
            + 1.20 * segment_clv
            - 1.50 * overconf_drag
        ) * lead_time_factor
        meta_kelly_multiplier = round(max(0.65, min(1.45, raw_kelly_mult)), 4)

        raw_ev_hurdle = (
            0.020
            - 0.04 * kernel_roi
            - 0.12 * kernel_brier_delta
            - 0.05 * segment_clv
            + 0.20 * max(0.0, -kernel_residual_bias - 0.015)
        )
        meta_ev_hurdle = round(max(0.012, min(0.045, raw_ev_hurdle)), 4)

        return {
            'effective_sample_size': round(w_sum, 2),
            'shrinkage_weight': round(shrink_w, 4),
            'kernel_roi': round(kernel_roi, 4),
            'kernel_brier_delta': round(kernel_brier_delta, 5),
            'kernel_residual_bias': round(kernel_residual_bias, 4),
            'segment_clv': segment_clv,
            'lead_time_factor': lead_time_factor,
            'meta_priority_boost': meta_priority_boost,
            'meta_kelly_multiplier': meta_kelly_multiplier,
            'meta_ev_hurdle': meta_ev_hurdle,
        }


_GLOBAL_META_LEARNER: Optional[ExecutionMetaLearner] = None


def get_execution_meta_learner(data_dir: Path | str = DATA) -> ExecutionMetaLearner:
    global _GLOBAL_META_LEARNER
    if _GLOBAL_META_LEARNER is None or str(_GLOBAL_META_LEARNER.data_dir) != str(data_dir):
        _GLOBAL_META_LEARNER = ExecutionMetaLearner(data_dir=data_dir)
    return _GLOBAL_META_LEARNER
