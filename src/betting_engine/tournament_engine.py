"""Multi-Model Evolutionary Tournament Engine.

Coordinates parallel candidate models competing on live fixtures in the Shadow Incubator ($0.00 virtual stakes).
Calculates the Walk-Forward Crucible Scorecard (S) and crowns champion models for live production graduation.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .providers import DATA
from .sandbox_models.base import BaseCandidateModel
from .sandbox_models.validator import validate_candidate_model


TOURNAMENT_FILE = DATA / 'research' / 'tournament_registry.json'
INCUBATOR_DB = DATA / 'shadow_incubator.sqlite3'


@dataclass
class CandidateStanding:
    model_id: str
    version: str
    segment_key: str
    sample_size: int
    wins: int
    losses: int
    pushes: int
    win_rate: float
    simulated_roi: float
    simulated_pnl: float
    brier_model: float
    brier_market: float
    delta_brier: float
    max_drawdown: float
    scorecard_s: float
    is_qualified: bool
    status: str  # 'incubating', 'champion', 'rejected'
    failure_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_crucible_scorecard(
    roi: float,
    sample_size: int,
    delta_brier: float,
    max_drawdown: float,
) -> float:
    """Computes the continuous Crucible Scorecard Metric (S).
    
    If ROI <= 0 or delta_brier <= 0, candidate is strictly penalized.
    When both are positive:
        S = ROI * sqrt(N) * (DeltaBrier / max(0.05, MaxDrawdown))
    """
    if sample_size <= 0:
        return 0.0
    safe_dd = max(0.05, max_drawdown)
    bounded_db = max(-0.1, min(0.1, delta_brier))

    if roi <= 0.0 or bounded_db <= 0.0:
        # Penalize non-performing models so that (-ROI) * (-DeltaBrier) never creates a false positive
        loss_penalty = min(0.0, roi) * (sample_size ** 0.5)
        brier_penalty = min(0.0, bounded_db) * 10.0
        return round(loss_penalty + brier_penalty, 6)

    score = roi * (sample_size ** 0.5) * (bounded_db / safe_dd)
    return round(score, 6)


def normalize_segment_key(segment_key: str) -> str:
    """Normalizes segment keys to canonical league|scope|family representation."""
    parts = str(segment_key).split('|')
    if len(parts) >= 3:
        league, scope, family = parts[0], parts[1], parts[2]
        base_fam = family.split(':', 1)[1] if ':' in family else family
        return f"{league}|{scope}|{base_fam}"
    return str(segment_key)


class TournamentEngine:
    """Orchestrates multi-model tournaments across market segments."""

    def __init__(self, data_dir: Path = DATA, db_path: Optional[Path] = None):
        self.data_dir = Path(data_dir or DATA)
        self.registry_file = self.data_dir / 'research' / 'tournament_registry.json'
        self.db_path = Path(db_path or (self.data_dir / 'shadow_incubator.sqlite3'))
        self._runtime_classes: Dict[str, type] = {}
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Ensures incubator DB supports multi-tenant model tracking."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            # Ensure incubated_wagers table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS incubated_wagers (
                    id TEXT PRIMARY KEY,
                    fixture_id TEXT,
                    game_id TEXT,
                    league TEXT,
                    scope TEXT,
                    family TEXT,
                    sub_family TEXT,
                    raw_market_name TEXT,
                    selection TEXT,
                    side TEXT DEFAULT 'home',
                    line REAL,
                    odds REAL,
                    model_prob REAL,
                    push_prob REAL DEFAULT 0.0,
                    market_prob REAL,
                    ev REAL,
                    dynamic_sample_power_req INT DEFAULT 25,
                    captured_at TEXT,
                    status TEXT DEFAULT 'open',
                    settled_at TEXT,
                    result TEXT,
                    pnl REAL,
                    brier_model REAL,
                    brier_market REAL,
                    metadata TEXT,
                    cohort TEXT DEFAULT 'gap_market',
                    model_id TEXT DEFAULT 'baseline',
                    model_version TEXT DEFAULT 'v1.0'
                )
            """)
            # Ensure columns exist in case table was created previously without them
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(incubated_wagers)")
            existing_cols = {row[1] for row in cur.fetchall()}
            if 'model_id' not in existing_cols:
                conn.execute("ALTER TABLE incubated_wagers ADD COLUMN model_id TEXT DEFAULT 'baseline'")
            if 'model_version' not in existing_cols:
                conn.execute("ALTER TABLE incubated_wagers ADD COLUMN model_version TEXT DEFAULT 'v1.0'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incubated_model ON incubated_wagers(model_id, status)")
            conn.commit()
        finally:
            conn.close()

    def load_registry(self) -> Dict[str, Any]:
        """Loads tournament registry of active and historical candidates."""
        if not self.registry_file.exists():
            return {'tournaments': {}, 'champions': {}, 'rejected': []}
        try:
            return json.loads(self.registry_file.read_text(encoding='utf-8'))
        except Exception:
            return {'tournaments': {}, 'champions': {}, 'rejected': []}

    def save_registry(self, registry: Dict[str, Any]) -> None:
        """Saves tournament registry atomically with locking and candidate merge."""
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.registry_file.with_suffix('.lock')
        try:
            import fcntl
            with open(lock_file, 'w') as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                if self.registry_file.exists():
                    try:
                        disk_reg = json.loads(self.registry_file.read_text(encoding='utf-8'))
                        for t_key, t_val in disk_reg.get('tournaments', {}).items():
                            if t_key not in registry.get('tournaments', {}):
                                registry.setdefault('tournaments', {})[t_key] = t_val
                            else:
                                for c_id, c_val in t_val.get('candidates', {}).items():
                                    if c_id not in registry['tournaments'][t_key].get('candidates', {}):
                                        registry['tournaments'][t_key].setdefault('candidates', {})[c_id] = c_val
                    except Exception:
                        pass
                tmp = self.registry_file.with_suffix('.tmp')
                tmp.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding='utf-8')
                tmp.replace(self.registry_file)
                fcntl.flock(lf, fcntl.LOCK_UN)
        except Exception:
            tmp = self.registry_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding='utf-8')
            tmp.replace(self.registry_file)

    def register_candidate(
        self,
        segment_key: str,
        model: BaseCandidateModel,
        sample_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, List[str]]:
        """Registers a candidate model into the segment's tournament after pre-flight validation."""
        # 1. Pre-flight validation
        ctx = sample_context or {'home_expected': 2.5, 'away_expected': 2.0}
        passed, errors = validate_candidate_model(model, ctx)
        if not passed:
            return False, [f"Validation failed: {'; '.join(errors)}"]

        canonical_key = normalize_segment_key(segment_key)
        reg = self.load_registry()
        tourneys = reg.setdefault('tournaments', {})
        t_data = tourneys.setdefault(canonical_key, {
            'segment_key': canonical_key,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'candidates': {},
            'status': 'active',
        })

        cand_id = model.model_id
        # Check anti-looping: ensure model hash not rejected
        model_sig = f"{model.model_id}:{model.version}:{model.__class__.__name__}"
        sig_hash = hashlib.sha256(model_sig.encode('utf-8')).hexdigest()
        if sig_hash in reg.get('rejected', []):
            return False, ["Model signature is permanently blacklisted in tournament registry."]

        t_data['candidates'][cand_id] = {
            'model_id': model.model_id,
            'version': model.version,
            'class_name': model.__class__.__name__,
            'league': model.league,
            'scope': model.scope,
            'family': model.family,
            'registered_at': datetime.now(timezone.utc).isoformat(),
            'status': 'incubating',
            'signature_hash': sig_hash,
            'metadata': model.metadata,
        }

        self.save_registry(reg)
        if hasattr(self, '_runtime_classes'):
            self._runtime_classes[model.__class__.__name__] = model.__class__
        return True, []

    def _get_class_map(self) -> Dict[str, type]:
        """Dynamically discovers all candidate model classes in archetypes and generated models."""
        class_map = dict(getattr(self, '_runtime_classes', {}))
        try:
            import importlib
            import inspect
            import pkgutil
            from .sandbox_models import archetypes as arch_pkg
            for finder, mod_name, ispkg in pkgutil.iter_modules(arch_pkg.__path__):
                mod = importlib.import_module(f'.sandbox_models.archetypes.{mod_name}', package='betting_engine')
                for item_name, item in inspect.getmembers(mod, inspect.isclass):
                    if issubclass(item, BaseCandidateModel) and item is not BaseCandidateModel:
                        class_map[item_name] = item

            # Also discover synthesized classes from generated directory
            gen_dir = Path(__file__).resolve().parent / 'sandbox_models' / 'generated'
            if gen_dir.exists():
                for py_file in gen_dir.glob('*.py'):
                    if py_file.stem.startswith('__'):
                        continue
                    try:
                        mod = importlib.import_module(f'.sandbox_models.generated.{py_file.stem}', package='betting_engine')
                        for item_name, item in inspect.getmembers(mod, inspect.isclass):
                            if issubclass(item, BaseCandidateModel) and item is not BaseCandidateModel:
                                class_map[item_name] = item
                    except Exception:
                        pass
        except Exception:
            from .sandbox_models.archetypes.negative_binomial import NegativeBinomialOverdispersionModel
            from .sandbox_models.archetypes.situational_fatigue import SituationalFatigueModel
            from .sandbox_models.archetypes.odds_damped_kelly import OddsDampedKellyShrinkageModel
            from .sandbox_models.archetypes.bivariate_copula import BivariateCopulaHazardModel
            from .sandbox_models.archetypes.zero_inflated import ZeroInflatedPoissonModel
            class_map = {
                'NegativeBinomialOverdispersionModel': NegativeBinomialOverdispersionModel,
                'SituationalFatigueModel': SituationalFatigueModel,
                'OddsDampedKellyShrinkageModel': OddsDampedKellyShrinkageModel,
                'BivariateCopulaHazardModel': BivariateCopulaHazardModel,
                'ZeroInflatedPoissonModel': ZeroInflatedPoissonModel,
            }
        return class_map

    def _find_tournament_data(self, reg: Dict[str, Any], segment_key: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Resolves tournament data across exact, normalized, and scoped alias keys."""
        tourneys = reg.get('tournaments', {})
        if segment_key in tourneys:
            return segment_key, tourneys[segment_key]
        norm = normalize_segment_key(segment_key)
        if norm in tourneys:
            return norm, tourneys[norm]
        parts = str(segment_key).split('|')
        if len(parts) >= 3:
            lg, sc, fam = parts[0], parts[1], parts[2]
            base_fam = fam.split(':', 1)[1] if ':' in fam else fam
            scoped_fam = f"{sc}:{base_fam}"
            if f"{lg}|{sc}|{scoped_fam}" in tourneys:
                return f"{lg}|{sc}|{scoped_fam}", tourneys[f"{lg}|{sc}|{scoped_fam}"]
            if f"{lg}|{sc}|{base_fam}" in tourneys:
                return f"{lg}|{sc}|{base_fam}", tourneys[f"{lg}|{sc}|{base_fam}"]
            if f"{lg}|full_game|{scoped_fam}" in tourneys:
                return f"{lg}|full_game|{scoped_fam}", tourneys[f"{lg}|full_game|{scoped_fam}"]
            if f"{lg}|full_game|{base_fam}" in tourneys:
                return f"{lg}|full_game|{base_fam}", tourneys[f"{lg}|full_game|{base_fam}"]
        return None, None

    def get_model_by_id(self, model_id: str) -> Optional[BaseCandidateModel]:
        """Looks up and instantiates any registered candidate model by its model_id with DB fallback."""
        reg = self.load_registry()
        target_meta = None
        for t_data in reg.get('tournaments', {}).values():
            if model_id in t_data.get('candidates', {}):
                target_meta = t_data['candidates'][model_id]
                break

        # Fail-safe fallback: look up directly from demoted_markets in DB if missing from JSON
        if not target_meta and self.db_path.exists():
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=5.0)
                cur = conn.cursor()
                row = cur.execute("""
                    SELECT incubated_model_id, model_archetype, model_params, league, scope, family
                    FROM demoted_markets
                    WHERE incubated_model_id = ?
                """, (model_id,)).fetchone()
                conn.close()
                if row:
                    m_id, arch, p_json, lg, sc, fam = row
                    params = json.loads(p_json) if p_json else {}
                    target_meta = {
                        'model_id': m_id,
                        'class_name': arch,
                        'league': lg,
                        'scope': sc,
                        'family': fam,
                        'params': params,
                        'metadata': {'params': params},
                    }
            except Exception:
                pass

        if not target_meta:
            return None

        class_map = self._get_class_map()
        cls_name = target_meta.get('class_name')
        cls_target = class_map.get(cls_name)
        if not cls_target:
            return None
        try:
            meta_dict = target_meta.get('metadata') or {}
            params = meta_dict.get('params') or target_meta.get('params') or {}
            kwargs = {
                'model_id': target_meta['model_id'],
                'version': target_meta.get('version', '1.0.0'),
                'league': target_meta.get('league', 'UNKNOWN'),
                'scope': target_meta.get('scope', 'full_game'),
                'family': target_meta.get('family', 'totals'),
                'metadata': meta_dict,
            }
            kwargs.update(params)
            return cls_target(**kwargs)
        except Exception:
            return None

    def get_active_candidates(self, segment_key: str) -> List[BaseCandidateModel]:
        """Returns instantiated active candidate models for a given segment key."""
        reg = self.load_registry()
        _, t_data = self._find_tournament_data(reg, segment_key)
        if not t_data:
            return []

        class_map = self._get_class_map()
        instances: List[BaseCandidateModel] = []
        for cand_id, meta in t_data.get('candidates', {}).items():
            if meta.get('status') not in ('incubating', 'qualified', 'champion'):
                continue
            cls_name = meta.get('class_name')
            cls_target = class_map.get(cls_name)
            if not cls_target:
                continue
            try:
                meta_dict = meta.get('metadata') or {}
                params = meta_dict.get('params') or meta.get('params') or {}
                kwargs = {
                    'model_id': meta['model_id'],
                    'version': meta.get('version', '1.0.0'),
                    'league': meta.get('league', 'UNKNOWN'),
                    'scope': meta.get('scope', 'full_game'),
                    'family': meta.get('family', 'totals'),
                    'metadata': meta_dict,
                }
                kwargs.update(params)
                inst = cls_target(**kwargs)
                instances.append(inst)
            except Exception:
                continue

        return instances

    def evaluate_tournament(
        self,
        segment_key: str,
        min_sample_size: int = 25,
        min_roi: float = 0.035,
        min_delta_brier: float = 0.0005,
        max_drawdown_limit: float = 0.20,
    ) -> List[CandidateStanding]:
        """Evaluates tournament standings for all competing candidates in a segment."""
        reg = self.load_registry()
        eff_key, t_data = self._find_tournament_data(reg, segment_key)
        if not t_data or not eff_key:
            return []
        resolved_key = eff_key

        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        standings: List[CandidateStanding] = []

        try:
            for cand_id, cand_meta in t_data.get('candidates', {}).items():
                cur = conn.cursor()
                cur.execute("""
                    SELECT status, result, pnl, brier_model, brier_market
                    FROM incubated_wagers
                    WHERE model_id = ? AND status = 'settled'
                    ORDER BY settled_at ASC
                """, (cand_id,))
                rows = cur.fetchall()

                n = len(rows)
                wins = sum(1 for r in rows if r['result'] == 'won')
                losses = sum(1 for r in rows if r['result'] == 'lost')
                pushes = sum(1 for r in rows if r['result'] == 'push')
                pnl = sum(float(r['pnl'] or 0.0) for r in rows)
                # Virtual turnover assuming 1 unit per bet
                turnover = max(1.0, float(wins + losses + pushes))
                roi = round(pnl / turnover, 4)
                win_rate = round(wins / max(1, wins + losses), 4)

                bm_list = [float(r['brier_model']) for r in rows if r['brier_model'] is not None]
                b_mkt_list = [float(r['brier_market']) for r in rows if r['brier_market'] is not None]

                avg_bm = sum(bm_list) / len(bm_list) if bm_list else 0.25
                avg_bmkt = sum(b_mkt_list) / len(b_mkt_list) if b_mkt_list else 0.25
                delta_brier = round(avg_bmkt - avg_bm, 6)

                # Compute Max Drawdown
                peak = 0.0
                cum_pnl = 0.0
                max_dd = 0.0
                for r in rows:
                    cum_pnl += float(r['pnl'] or 0.0)
                    if cum_pnl > peak:
                        peak = cum_pnl
                    dd = peak - cum_pnl
                    if dd > max_dd:
                        max_dd = dd
                dd_pct = round(max_dd / max(1.0, peak + turnover), 4)

                score_s = calculate_crucible_scorecard(roi, n, delta_brier, dd_pct)

                # Assess qualifications
                reasons = []
                if n < min_sample_size:
                    reasons.append(f"Insufficient sample: {n}/{min_sample_size} games")
                if roi < min_roi:
                    reasons.append(f"ROI below hurdle: {roi*100:.1f}% < {min_roi*100:.1f}%")
                if delta_brier < min_delta_brier:
                    reasons.append(f"Delta Brier below threshold: {delta_brier:.5f} < {min_delta_brier:.5f}")
                if dd_pct > max_drawdown_limit:
                    reasons.append(f"Max DD exceeded: {dd_pct*100:.1f}% > {max_drawdown_limit*100:.1f}%")

                is_qual = (len(reasons) == 0)
                status = 'incubating'
                if is_qual:
                    status = 'qualified'
                elif n >= min_sample_size and (roi < 0.0 or delta_brier < 0.0):
                    status = 'rejected'

                standing = CandidateStanding(
                    model_id=cand_id,
                    version=cand_meta.get('version', '1.0.0'),
                    segment_key=resolved_key,
                    sample_size=n,
                    wins=wins,
                    losses=losses,
                    pushes=pushes,
                    win_rate=win_rate,
                    simulated_roi=roi,
                    simulated_pnl=round(pnl, 2),
                    brier_model=round(avg_bm, 4),
                    brier_market=round(avg_bmkt, 4),
                    delta_brier=delta_brier,
                    max_drawdown=dd_pct,
                    scorecard_s=score_s,
                    is_qualified=is_qual,
                    status=status,
                    failure_reasons=reasons,
                )
                standings.append(standing)
        finally:
            conn.close()

        # Sort standings by scorecard S descending
        standings.sort(key=lambda s: s.scorecard_s, reverse=True)
        return standings

    def crown_champion(self, segment_key: str) -> Optional[CandidateStanding]:
        """Identifies and crowns the champion candidate model if one qualifies."""
        standings = self.evaluate_tournament(segment_key)
        reg = self.load_registry()
        eff_key, t_data = self._find_tournament_data(reg, segment_key)
        canonical_key = eff_key or normalize_segment_key(segment_key)
        t_cands = t_data.get('candidates', {}) if t_data else {}
        rejected_list = reg.setdefault('rejected', [])

        has_change = False
        for s in standings:
            if s.status == 'rejected':
                c_data = t_cands.get(s.model_id, {})
                if c_data.get('status') != 'rejected':
                    c_data['status'] = 'rejected'
                    has_change = True
                sig_hash = c_data.get('signature_hash')
                if sig_hash and sig_hash not in rejected_list:
                    rejected_list.append(sig_hash)
                    has_change = True

        qualified = [s for s in standings if s.is_qualified]
        if not qualified:
            if has_change:
                self.save_registry(reg)
            return None

        champion = qualified[0]  # Highest scorecard S
        reg.setdefault('champions', {})[canonical_key] = {
            'champion_model_id': champion.model_id,
            'version': champion.version,
            'crowned_at': datetime.now(timezone.utc).isoformat(),
            'scorecard_s': champion.scorecard_s,
            'sample_size': champion.sample_size,
            'roi': champion.simulated_roi,
            'delta_brier': champion.delta_brier,
        }

        # Update candidate statuses in tournament registry
        if champion.model_id in t_cands:
            t_cands[champion.model_id]['status'] = 'champion'
            t_cands[champion.model_id]['crowned_at'] = datetime.now(timezone.utc).isoformat()

        self.save_registry(reg)
        return champion


def ensure_demoted_tournaments_active(
    tournament_engine: Optional[TournamentEngine] = None,
    data_dir: Path = DATA
) -> int:
    """Ensures 100% of demoted markets have active candidate tournament slates.
    
    Eliminates sequential queue bottlenecks so that all demoted segments actively
    compete for Crucible re-graduation in parallel.
    """
    engine = tournament_engine or TournamentEngine(data_dir=data_dir)
    incubator_db = data_dir / 'shadow_incubator.sqlite3'
    if not incubator_db.exists():
        return 0
    try:
        conn = sqlite3.connect(incubator_db)
        rows = [r[0] for r in conn.execute("SELECT segment_key FROM demoted_markets").fetchall()]
        conn.close()
    except Exception:
        return 0

    from .model_generator import synthesize_candidate_slate
    reg = engine.load_registry()
    tourneys = set(reg.get('tournaments', {}).keys())

    created = 0
    for seg in rows:
        canonical = normalize_segment_key(seg)
        if canonical not in tourneys:
            synthesize_candidate_slate(canonical, tournament_engine=engine)
            tourneys.add(canonical)
            created += 1
    return created
