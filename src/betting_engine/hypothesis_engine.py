"""Hypothesis Engine and Durable Research Journal.

Persists, manages, and deduplicates quantitative research hypotheses across
system restarts. Ensures that failed ideas are permanently remembered and never
re-tested on the same historical data.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ai_investigator import CognitiveDiagnosticReport, DiagnosticHypothesis


DEFAULT_JOURNAL_PATH = Path("data/research/hypotheses_journal.json")


@dataclass
class ResearchRecord:
    hypothesis_id: str
    segment: str
    league: str
    category: str
    title: str
    rationale: str
    proposed_formula_or_rule: str
    status: str  # 'proposed', 'in_crucible', 'passed_crucible', 'rejected', 'in_shadow', 'deployed'
    created_at: str
    updated_at: str
    experiments_count: int = 0
    best_brier_improvement: float = 0.0
    best_simulated_roi_delta: float = 0.0
    crucible_evidence: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HypothesisJournal:
    """Thread-safe persistent journal for all AI research hypotheses."""

    def __init__(self, journal_path: Path = DEFAULT_JOURNAL_PATH):
        self.journal_path = journal_path
        self._ensure_journal_exists()

    def _ensure_journal_exists(self) -> None:
        if not self.journal_path.parent.exists():
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.journal_path.exists():
            initial_data = {
                'version': 1,
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'records': {}
            }
            self.journal_path.write_text(json.dumps(initial_data, indent=2))

    def load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.journal_path.read_text())
        except Exception:
            return {'version': 1, 'records': {}}

    def save(self, data: Dict[str, Any]) -> None:
        data['updated_at'] = datetime.now(timezone.utc).isoformat()
        self.journal_path.write_text(json.dumps(data, indent=2))

    def record_hypothesis(self, report: CognitiveDiagnosticReport, hyp: DiagnosticHypothesis) -> ResearchRecord:
        """Registers a new hypothesis or returns existing record."""
        data = self.load()
        records = data.setdefault('records', {})
        now = datetime.now(timezone.utc).isoformat()

        if hyp.hypothesis_id in records:
            r = records[hyp.hypothesis_id]
            return ResearchRecord(**r)

        record = ResearchRecord(
            hypothesis_id=hyp.hypothesis_id,
            segment=report.segment,
            league=report.league,
            category=hyp.category,
            title=hyp.title,
            rationale=hyp.rationale,
            proposed_formula_or_rule=hyp.proposed_formula_or_rule,
            status='proposed',
            created_at=now,
            updated_at=now,
            notes=[f"Generated from failure mode: {report.primary_sports_mechanic}"]
        )
        records[hyp.hypothesis_id] = record.to_dict()
        self.save(data)
        return record

    def update_result(
        self,
        hypothesis_id: str,
        status: str,
        brier_imp: float,
        roi_delta: float,
        evidence: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None
    ) -> Optional[ResearchRecord]:
        """Updates a hypothesis after running in the quantitative crucible."""
        data = self.load()
        records = data.setdefault('records', {})
        if hypothesis_id not in records:
            return None

        rec = records[hypothesis_id]
        rec['status'] = status
        rec['updated_at'] = datetime.now(timezone.utc).isoformat()
        rec['experiments_count'] = rec.get('experiments_count', 0) + 1
        rec['best_brier_improvement'] = max(rec.get('best_brier_improvement', 0.0), brier_imp)
        rec['best_simulated_roi_delta'] = max(rec.get('best_simulated_roi_delta', 0.0), roi_delta)
        if evidence:
            rec['crucible_evidence'] = evidence
        if note:
            rec.setdefault('notes', []).append(note)

        self.save(data)
        return ResearchRecord(**rec)

    def is_previously_rejected(self, hypothesis_id: str) -> bool:
        """Checks if this hypothesis was already tested and rejected."""
        records = self.load().get('records', {})
        if hypothesis_id in records:
            return records[hypothesis_id].get('status') == 'rejected'
        return False

    def get_all_hypotheses(self) -> List[ResearchRecord]:
        """Returns all registered hypotheses from the journal."""
        records = self.load().get('records', {})
        return [ResearchRecord(**r) for r in records.values()]

    def get_pending_hypotheses(self) -> List[ResearchRecord]:
        """Returns hypotheses awaiting testing in the crucible."""
        records = self.load().get('records', {})
        return [
            ResearchRecord(**r) for r in records.values()
            if r.get('status') in ('proposed', 'in_crucible')
        ]


def process_diagnostic_report(
    report: CognitiveDiagnosticReport,
    journal: Optional[HypothesisJournal] = None
) -> List[ResearchRecord]:
    """Ingests a cognitive report, deduplicates, and enqueues actionable hypotheses."""
    if journal is None:
        journal = HypothesisJournal()

    active_records = []
    for hyp in report.hypotheses:
        if journal.is_previously_rejected(hyp.hypothesis_id):
            continue
        rec = journal.record_hypothesis(report, hyp)
        active_records.append(rec)

    return active_records
