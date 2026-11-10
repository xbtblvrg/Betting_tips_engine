"""AI Archetype Developer Agent for Continuous Quantitative Evolution.

Harvests candidate models that successfully graduated through the Walk-Forward Crucible,
distills their structural innovations into generic parameterized archetypes, and registers
them in the active distilled archetype catalog. Transforms the algorithmic engine from
a static 5-template fallback into a self-enriching, adaptive smart synthesis engine.
"""
from __future__ import annotations

import ast
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import importlib.util
import json
import logging
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Type

from .providers import DATA
from .sandbox_models.base import BaseCandidateModel, DiscreteDistribution

log = logging.getLogger(__name__)

DEFAULT_INCUBATOR_DB = DATA / "shadow_incubator.sqlite3"
DISTILLED_ARCHETYPES_DIR = Path("src/betting_engine/sandbox_models/archetypes/dynamically_distilled")
GENERATED_MODELS_DIR = Path("src/betting_engine/sandbox_models/generated")


@dataclass
class DistilledArchetypeRecord:
    archetype_id: str
    name: str
    base_class_name: str
    category: str
    compatible_leagues: List[str]
    compatible_families: List[str]
    source_template_code: str
    parameter_spec: Dict[str, Any]
    origin_model_id: str
    origin_provider: str
    historical_oos_roi: float
    historical_delta_brier: float
    times_instantiated: int = 0
    times_crucible_passed: int = 0
    is_active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AIArchetypeDeveloperAgent:
    """Autonomous agent that harvests winning candidate models and expands the algorithmic engine."""

    _instance: Optional[AIArchetypeDeveloperAgent] = None

    def __init__(
        self,
        db_path: Path | str = DEFAULT_INCUBATOR_DB,
        archetypes_dir: Path | str = DISTILLED_ARCHETYPES_DIR,
        generated_dir: Path | str = GENERATED_MODELS_DIR,
    ):
        p = Path(db_path) if isinstance(db_path, str) else db_path
        if p.is_dir():
            p = p / "shadow_incubator.sqlite3"
        self.db_path = p
        self.archetypes_dir = Path(archetypes_dir)
        self.generated_dir = Path(generated_dir)
        self.archetypes_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_table_exists()

    @classmethod
    def get_instance(cls) -> AIArchetypeDeveloperAgent:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_table_exists(self) -> None:
        """Idempotently ensures distilled_archetype_catalog exists in SQLite."""
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(f"file:{self.db_path.resolve()}", uri=True, timeout=10.0)) as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS distilled_archetype_catalog (
                        archetype_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        base_class_name TEXT NOT NULL,
                        category TEXT NOT NULL,
                        compatible_leagues TEXT NOT NULL,
                        compatible_families TEXT NOT NULL,
                        source_template_code TEXT NOT NULL,
                        parameter_spec TEXT NOT NULL,
                        origin_model_id TEXT NOT NULL,
                        origin_provider TEXT NOT NULL,
                        historical_oos_roi REAL NOT NULL,
                        historical_delta_brier REAL NOT NULL,
                        times_instantiated INTEGER DEFAULT 0,
                        times_crucible_passed INTEGER DEFAULT 0,
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_distilled_archetypes_category ON distilled_archetype_catalog(category)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_distilled_archetypes_active ON distilled_archetype_catalog(is_active)")

    @staticmethod
    def categorize_model(class_name: str, code: str, league: str, family: str) -> Tuple[str, List[str], List[str]]:
        """Determines category, compatible leagues, and compatible families from code and domain mechanics."""
        combined = f"{class_name.lower()} {code.lower()}"
        lg_u = league.upper().strip()
        fam_l = family.lower().strip()

        # 1. Category
        if "possession" in combined and "lattice" in combined:
            category = "possession_lattice"
        elif "compound" in combined or "possess" in combined:
            category = "compound_possession"
        elif "weibull" in combined or "renewal" in combined or "hazard" in combined:
            category = "renewal_hazard"
        elif "markov" in combined or "tennis" in combined:
            category = "absorbing_markov"
        elif "dixon" in combined or "copula" in combined:
            category = "bivariate_tail"
        elif "hurdle" in combined or "negbin" in combined or "negative_binomial" in combined:
            category = "hurdle_overdispersion"
        elif "laplace" in combined or "skellam" in combined or "spread" in combined:
            category = "score_differential"
        else:
            category = "general_divergent"

        # 2. Compatible Leagues
        basketball = ["NBA", "WNBA", "NCAAM"]
        football = ["NFL", "NCAAF"]
        hockey = ["NHL"]
        soccer = ["MLS", "EPL", "UCL"]
        tennis = ["ATP", "WTA", "CHALLENGER"]
        baseball = ["MLB"]

        if lg_u in basketball:
            compatible_leagues = basketball
        elif lg_u in football:
            compatible_leagues = football
        elif lg_u in hockey:
            compatible_leagues = hockey
        elif lg_u in soccer:
            compatible_leagues = soccer
        elif lg_u in tennis:
            compatible_leagues = tennis
        elif lg_u in baseball:
            compatible_leagues = baseball
        elif "nba" in combined or "wnba" in combined:
            compatible_leagues = basketball
        elif "nfl" in combined or "ncaaf" in combined:
            compatible_leagues = football
        elif "nhl" in combined or "empty_net" in combined:
            compatible_leagues = hockey
        elif "soccer" in combined or "dixon" in combined:
            compatible_leagues = soccer
        elif "tennis" in combined or "atp" in combined or "wta" in combined:
            compatible_leagues = tennis
        elif "baseball" in combined or "mlb" in combined:
            compatible_leagues = baseball
        else:
            compatible_leagues = [lg_u]

        # 3. Compatible Families
        compatible_families = [fam_l]
        if any(t in fam_l for t in ("totals", "home_total", "away_total")):
            for extra in ("totals", "home_total", "away_total"):
                if extra not in compatible_families:
                    compatible_families.append(extra)
        elif any(t in fam_l for t in ("tennis_sets", "sets", "spreads", "moneyline", "three_way")):
            for extra in ("tennis_sets", "sets", "spreads", "moneyline", "three_way"):
                if extra not in compatible_families:
                    compatible_families.append(extra)

        return category, compatible_leagues, compatible_families

    def distill_from_candidate(
        self,
        model_id: str,
        source_code: str,
        class_name: str,
        league: str,
        scope: str,
        family: str,
        oos_roi: float = 0.0,
        delta_brier: float = 0.0,
        provider: str = "codex",
    ) -> Optional[DistilledArchetypeRecord]:
        """Validates and distills a winning candidate model into a reusable parameterized archetype."""
        from .codex_model_synthesizer import check_ast_safety, instantiate_synthesized_class

        # 1. Safety check
        is_safe, found_class, err = check_ast_safety(source_code)
        if not is_safe or not found_class:
            log.debug("Distillation rejected unsafe or malformed code for %s: %s", model_id, err)
            return None

        # 2. Runtime sanity instantiation test
        try:
            instance = instantiate_synthesized_class(
                source_code,
                found_class,
                model_id=f"distill_test_{model_id}",
                league=league,
                scope=scope,
                family=family,
            )
            # Evaluate with sample context
            sample_ctx = {"home_expected": 2.5, "away_expected": 2.0}
            dist = instance.predict_distribution(sample_ctx)
            has_valid_dist = (
                isinstance(dist, DiscreteDistribution)
                and (
                    (dist.p_home + dist.p_away + getattr(dist, 'p_draw', 0.0)) > 0.0
                    or (bool(dist.scores) and sum(dist.scores.values()) > 0.0)
                )
            )
            if not has_valid_dist:
                log.debug("Distillation rejected model %s: invalid distribution output", model_id)
                return None
        except Exception as e_run:
            log.debug("Distillation runtime test failed for %s: %s", model_id, e_run)
            return None

        # 3. Categorize & generate archetype ID
        category, comp_leagues, comp_families = self.categorize_model(
            found_class, source_code, league, family
        )
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', found_class)
        archetype_id = f"arch_{category}_{safe_name[:30]}_{league.lower()}"

        # 4. Extract parameter spec
        param_spec: Dict[str, Any] = {
            "origin_league": league,
            "origin_scope": scope,
            "origin_family": family,
            "category": category,
            "calibratable_priors": [
                "baseline_home", "baseline_away", "dispersion_alpha", "hazard_shape_k"
            ]
        }

        record = DistilledArchetypeRecord(
            archetype_id=archetype_id,
            name=f"Distilled {found_class}",
            base_class_name=found_class,
            category=category,
            compatible_leagues=comp_leagues,
            compatible_families=comp_families,
            source_template_code=source_code,
            parameter_spec=param_spec,
            origin_model_id=model_id,
            origin_provider=provider,
            historical_oos_roi=float(oos_roi or 0.0),
            historical_delta_brier=float(delta_brier or 0.0),
            times_instantiated=0,
            times_crucible_passed=1,
            is_active=True,
        )

        # 5. Commit to catalog & save to file
        self.save_archetype(record)
        return record

    def save_archetype(self, record: DistilledArchetypeRecord) -> bool:
        """Persists archetype code to disk and registers metadata in SQLite."""
        # 1. Save Python file
        file_path = self.archetypes_dir / f"{record.archetype_id}.py"
        try:
            file_path.write_text(record.source_template_code, encoding="utf-8")
        except Exception as e_file:
            log.warning("Could not write archetype file %s: %s", file_path, e_file)
            return False

        # 2. Insert or replace in SQLite
        now = datetime.now(timezone.utc).isoformat()
        try:
            with closing(sqlite3.connect(f"file:{self.db_path.resolve()}", uri=True, timeout=10.0)) as conn:
                with conn:
                    conn.execute("""
                        INSERT INTO distilled_archetype_catalog (
                            archetype_id, name, base_class_name, category, compatible_leagues,
                            compatible_families, source_template_code, parameter_spec, origin_model_id,
                            origin_provider, historical_oos_roi, historical_delta_brier, times_instantiated,
                            times_crucible_passed, is_active, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(archetype_id) DO UPDATE SET
                            historical_oos_roi = MAX(distilled_archetype_catalog.historical_oos_roi, excluded.historical_oos_roi),
                            times_crucible_passed = distilled_archetype_catalog.times_crucible_passed + 1,
                            compatible_leagues = excluded.compatible_leagues,
                            compatible_families = excluded.compatible_families,
                            parameter_spec = excluded.parameter_spec,
                            source_template_code = excluded.source_template_code,
                            updated_at = excluded.updated_at
                    """, (
                        record.archetype_id,
                        record.name,
                        record.base_class_name,
                        record.category,
                        json.dumps(record.compatible_leagues),
                        json.dumps(record.compatible_families),
                        record.source_template_code,
                        json.dumps(record.parameter_spec),
                        record.origin_model_id,
                        record.origin_provider,
                        record.historical_oos_roi,
                        record.historical_delta_brier,
                        record.times_instantiated,
                        record.times_crucible_passed,
                        1 if record.is_active else 0,
                        record.created_at,
                        now,
                    ))
            log.info("Successfully registered distilled archetype: %s (%s)", record.archetype_id, record.name)
            return True
        except Exception as e_db:
            log.error("Failed to commit archetype %s to database: %s", record.archetype_id, e_db)
            return False

    def get_distilled_archetypes_for_market(
        self,
        league: str,
        scope: str,
        family: str,
        limit: int = 5,
    ) -> List[DistilledArchetypeRecord]:
        """Retrieves active distilled archetypes matching the given market, ranked by ROI."""
        if not self.db_path.exists():
            return []

        lg_u = league.upper().strip()
        fam_l = family.lower().strip()

        records: List[DistilledArchetypeRecord] = []
        try:
            with closing(sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=5.0)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT archetype_id, name, base_class_name, category, compatible_leagues,
                           compatible_families, source_template_code, parameter_spec, origin_model_id,
                           origin_provider, historical_oos_roi, historical_delta_brier,
                           times_instantiated, times_crucible_passed, is_active, created_at, updated_at
                    FROM distilled_archetype_catalog
                    WHERE is_active = 1
                    ORDER BY historical_oos_roi DESC, times_crucible_passed DESC
                """)
                rows = cur.fetchall()

            for r in rows:
                leagues = json.loads(r[4])
                families = json.loads(r[5])
                param_spec = json.loads(r[7])

                # Match league and family
                match_lg = (lg_u in [x.upper() for x in leagues])
                match_fam = any(fam_l in str(f).lower() or str(f).lower() in fam_l for f in families)

                if match_lg and match_fam:
                    records.append(DistilledArchetypeRecord(
                        archetype_id=r[0],
                        name=r[1],
                        base_class_name=r[2],
                        category=r[3],
                        compatible_leagues=leagues,
                        compatible_families=families,
                        source_template_code=r[6],
                        parameter_spec=param_spec,
                        origin_model_id=r[8],
                        origin_provider=r[9],
                        historical_oos_roi=float(r[10] or 0.0),
                        historical_delta_brier=float(r[11] or 0.0),
                        times_instantiated=int(r[12] or 0),
                        times_crucible_passed=int(r[13] or 0),
                        is_active=bool(r[14]),
                        created_at=r[15],
                        updated_at=r[16],
                    ))
                    if len(records) >= limit:
                        break
        except Exception as e:
            log.debug("Error querying distilled archetypes: %s", e)

        return records

    def bootstrap_from_existing_champions(self, min_roi: float = 0.035, limit: int = 100) -> int:
        """Scans ai_synthesis_feedback_loop for existing champion models and distills them."""
        if not self.db_path.exists():
            return 0

        harvested_count = 0
        try:
            with closing(sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=10.0)) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT model_id, class_name, league, scope, family, source_file_path,
                           oos_simulated_roi, oos_delta_brier, generator_provider
                    FROM ai_synthesis_feedback_loop
                    WHERE crucible_passed = 1 AND oos_simulated_roi >= ?
                    ORDER BY oos_simulated_roi DESC
                    LIMIT ?
                """, (min_roi, limit))
                rows = cur.fetchall()

            for r in rows:
                mid, cls_name, lg, sc, fam, fpath, oos_roi, delta_brier, provider = r
                # Find source file
                candidate_paths = [
                    Path(fpath) if fpath else None,
                    self.generated_dir / f"{mid}.py",
                ]
                source_code = None
                for cp in candidate_paths:
                    if cp and cp.exists():
                        try:
                            source_code = cp.read_text(encoding="utf-8")
                            break
                        except Exception:
                            pass

                if source_code:
                    rec = self.distill_from_candidate(
                        model_id=mid,
                        source_code=source_code,
                        class_name=cls_name,
                        league=lg,
                        scope=sc,
                        family=fam,
                        oos_roi=float(oos_roi or 0.0),
                        delta_brier=float(delta_brier or 0.0),
                        provider=provider or "codex",
                    )
                    if rec:
                        harvested_count += 1
        except Exception as e:
            log.warning("Bootstrap from existing champions encountered error: %s", e)

        log.info("Bootstrapped %d distilled archetypes from existing champions.", harvested_count)
        return harvested_count
