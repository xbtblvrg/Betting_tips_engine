"""Dynamic Model Archetype Registry for Continuous Reinforcement Learning.

Maintains an active catalog of successful candidate models synthesized by AI (Codex, Claude)
that have proven profitable in Walk-Forward Crucible backtests. Dynamically exposes these
proven models to the baseline candidate search grid so that future demoted markets can
immediately test them, creating a cumulative, self-enriching mathematical memory.
"""
from __future__ import annotations
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple, Type

from .providers import DATA
from .sandbox_models.base import BaseCandidateModel

log = logging.getLogger(__name__)

GENERATED_MODELS_DIR = Path("src/betting_engine/sandbox_models/generated")
DEFAULT_INCUBATOR_DB = DATA / "shadow_incubator.sqlite3"


@dataclass
class RegisteredArchetype:
    model_id: str
    class_name: str
    league: str
    scope: str
    family: str
    source_file_path: str
    source_code: str
    oos_simulated_roi: float = 0.0
    oos_delta_brier: float = 0.0
    live_realized_roi: float = 0.0
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DynamicArchetypeRegistry:
    """Manages continuous ingestion and retrieval of AI-synthesized quantitative models."""

    _instance: Optional[DynamicArchetypeRegistry] = None

    def __init__(
        self,
        models_dir: Path = GENERATED_MODELS_DIR,
        db_path: Path | str = DEFAULT_INCUBATOR_DB
    ):
        self.models_dir = Path(models_dir)
        p = Path(db_path) if isinstance(db_path, str) else db_path
        if p.is_dir():
            p = p / "shadow_incubator.sqlite3"
        self.db_path = p
        self._cache: Dict[str, RegisteredArchetype] = {}
        self._last_refresh: float = 0.0
        self.refresh_registry()

    @classmethod
    def get_instance(cls) -> DynamicArchetypeRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def refresh_registry(self) -> int:
        """Discovers and caches all Crucible-approved models from the feedback loop and generated dir."""
        now = time.monotonic()
        if now - self._last_refresh < 10.0 and self._cache:
            return len(self._cache)

        if not self.db_path.exists():
            return len(self._cache)

        try:
            from .codex_model_synthesizer import check_ast_safety
            conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=15.0)
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT model_id, class_name, league, scope, family, source_file_path,
                           oos_simulated_roi, oos_delta_brier, live_realized_roi
                    FROM ai_synthesis_feedback_loop
                    WHERE crucible_passed = 1 OR oos_simulated_roi >= 0.035
                    ORDER BY (oos_simulated_roi * 0.4 + COALESCE(live_realized_roi, 0.0) * 0.6) DESC
                """)
                rows = cur.fetchall()
            finally:
                conn.close()

            new_cache: Dict[str, RegisteredArchetype] = {}
            for r in rows:
                mid, cls_name, lg, sc, fam, fpath, oos_roi, delta_brier, live_roi = r
                file_candidate = Path(fpath) if fpath and Path(fpath).exists() else (self.models_dir / f"{mid}.py")
                if not file_candidate.exists():
                    continue

                try:
                    code = file_candidate.read_text(encoding="utf-8")
                    is_safe, found_cls, _ = check_ast_safety(code)
                    if not is_safe or not found_cls:
                        continue

                    new_cache[mid] = RegisteredArchetype(
                        model_id=mid,
                        class_name=found_cls,
                        league=lg,
                        scope=sc,
                        family=fam,
                        source_file_path=str(file_candidate),
                        source_code=code,
                        oos_simulated_roi=float(oos_roi or 0.0),
                        oos_delta_brier=float(delta_brier or 0.0),
                        live_realized_roi=float(live_roi or 0.0),
                    )
                except Exception as e_read:
                    log.debug("Could not cache dynamic archetype %s: %s", mid, e_read)

            self._cache = new_cache
            self._last_refresh = now
        except Exception as e:
            log.debug("Registry refresh error: %s", e)

        return len(self._cache)

    def register_new_model(
        self,
        model_id: str,
        source_code: str,
        class_name: str,
        league: str,
        scope: str,
        family: str,
        oos_roi: float = 0.0,
        delta_brier: float = 0.0
    ) -> bool:
        """Instantly adds a newly validated model to the active registry."""
        from .codex_model_synthesizer import check_ast_safety
        is_safe, found_cls, err = check_ast_safety(source_code)
        if not is_safe or not found_cls:
            return False

        self._cache[model_id] = RegisteredArchetype(
            model_id=model_id,
            class_name=found_cls,
            league=league,
            scope=scope,
            family=family,
            source_file_path=str(self.models_dir / f"{model_id}.py"),
            source_code=source_code,
            oos_simulated_roi=oos_roi,
            oos_delta_brier=delta_brier,
        )
        return True

    def get_dynamic_candidates_for_segment(
        self,
        league: str,
        scope: str,
        family: str
    ) -> List[Tuple[str, BaseCandidateModel, Dict[str, Any]]]:
        """Retrieves and instantiates registered AI models compatible with the given market."""
        self.refresh_registry()
        lg_u = league.upper()
        fam_l = family.lower()
        sc_l = scope.lower()

        candidates: List[Tuple[str, BaseCandidateModel, Dict[str, Any]]] = []

        for mid, arch in self._cache.items():
            # Check compatibility: same league and similar family (e.g. totals with totals, spreads with spreads)
            is_lg_match = (arch.league.upper() == lg_u)
            is_fam_match = (arch.family.lower() in fam_l or fam_l in arch.family.lower())

            if is_lg_match and is_fam_match:
                try:
                    from .codex_model_synthesizer import instantiate_synthesized_class
                    instance = instantiate_synthesized_class(
                        arch.source_code,
                        arch.class_name,
                        model_id=f"dyn_{mid}",
                        league=league,
                        scope=scope,
                        family=family
                    )
                    candidates.append((
                        f"Dynamic_{arch.class_name}",
                        instance,
                        {'source_model_id': mid, 'historical_roi': arch.oos_simulated_roi}
                    ))
                except Exception as e_inst:
                    log.debug("Could not instantiate dynamic model %s for %s: %s", mid, league, e_inst)

        return candidates
