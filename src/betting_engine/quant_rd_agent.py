"""Supervised Autonomous Quant R&D Agent.

Orchestrates the continuous, dynamic autonomous research cycle:
1. Detects underperforming segments from Segment Scoreboard.
2. Conducts forensic failure attribution.
3. Invokes cognitive sports reasoning to formulate testable hypotheses.
4. Validates candidate recipes in the leak-free Walk-Forward Crucible.
5. Records durable memory in the Research Journal.
6. Submits qualified candidates to the Release Coordinator for staged recovery.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional
from pathlib import Path

from .ai_investigator import synthesize_domain_investigation
from .failure_forensics import run_forensic_investigation
from .hypothesis_engine import HypothesisJournal, process_diagnostic_report
from .quant_crucible import run_crucible_walk_forward
from .segment_scoreboard import compute_segment_scoreboard
from .providers import DATA


def append_rd_log(stage: str, message: str, level: str = 'INFO', extra: Optional[dict] = None) -> dict:
    """Appends an execution log entry to durable jsonl audit trail."""
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        'timestamp': now,
        'stage': stage,
        'level': level,
        'message': message,
        'extra': extra or {}
    }
    log_path = DATA / 'research' / 'rd_execution_log.jsonl'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')
        # Bounded rotation to prevent memory and disk bloat: prune to latest 1000 lines if large
        if log_path.stat().st_size > 400_000:
            lines = log_path.read_text(encoding='utf-8').strip().split('\n')
            if len(lines) > 2000:
                log_path.write_text('\n'.join(lines[-1000:]) + '\n', encoding='utf-8')
    except Exception:
        pass
    return entry


def get_recent_rd_logs(limit: int = 80) -> List[dict]:
    """Reads the most recent execution log entries."""
    log_path = DATA / 'research' / 'rd_execution_log.jsonl'
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding='utf-8').strip().split('\n')
        entries = []
        for line in lines[-limit:]:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
        return entries
    except Exception:
        return []


def save_cycle_record(summary: 'RDCycleSummary') -> None:
    """Records the cycle summary in durable history."""
    history_path = DATA / 'research' / 'rd_cycles.json'
    history_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cycles = []
        if history_path.exists():
            try:
                cycles = json.loads(history_path.read_text(encoding='utf-8'))
                if not isinstance(cycles, list):
                    cycles = []
            except Exception:
                cycles = []
        cycles.insert(0, summary.to_dict())
        cycles = cycles[:30]  # Retain last 30 cycles
        history_path.write_text(json.dumps(cycles, indent=2), encoding='utf-8')
    except Exception:
        pass


def get_recent_cycles(limit: int = 15) -> List[dict]:
    """Returns recent cycle summaries."""
    history_path = DATA / 'research' / 'rd_cycles.json'
    if not history_path.exists():
        return []
    try:
        cycles = json.loads(history_path.read_text(encoding='utf-8'))
        return cycles[:limit] if isinstance(cycles, list) else []
    except Exception:
        return []


@dataclass
class RDCycleSummary:
    cycle_id: str
    executed_at: str
    underperforming_segments_checked: int
    forensic_dossiers_created: int
    hypotheses_generated: int
    crucible_tests_run: int
    candidates_passed: int
    candidates_rejected: int
    details: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SupervisedQuantRDAgent:
    """Supervised autonomous agent executing continuous quant self-improvement."""

    def __init__(
        self,
        db_conn: sqlite3.Connection,
        journal: Optional[HypothesisJournal] = None,
        cap_journal: Optional[Any] = None
    ):
        self.conn = db_conn
        self.journal = journal or HypothesisJournal()
        self.cap_journal = cap_journal

    def run_autonomous_cycle(self, target_segment: Optional[str] = None, max_segments: Optional[int] = None) -> RDCycleSummary:
        """Executes a complete autonomous R&D iteration with rich telemetry logging."""
        now = datetime.now(timezone.utc).isoformat()
        cycle_id = f"rd_cycle_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

        append_rd_log('START', f"Starting autonomous R&D self-improvement iteration {cycle_id}", extra={'cycle_id': cycle_id})

        # 1. Inspect segment scoreboard for troubled areas
        scoreboard = compute_segment_scoreboard(self.conn)
        # Explicit user standing directive: strictly prioritize segments with largest net cash leaks first
        underperforming = sorted(
            scoreboard.get('underperforming_segments', []),
            key=lambda s: (s.get('pnl', 0.0), s.get('roi_pct', 0.0))
        )
        total_segments = scoreboard.get('counts', {}).get('total_segments', len(underperforming))

        if target_segment:
            matching = [s for s in underperforming if s['segment'] == target_segment]
            if matching:
                underperforming = matching
            else:
                league = target_segment.split('|')[0] if '|' in target_segment else 'UNKNOWN'
                underperforming = [{
                    'segment': target_segment,
                    'league': league,
                    'games': 4,
                    'pnl': -1.0,
                    'roi_pct': -5.0
                }]
            append_rd_log(
                'TARGETED_REPAIR',
                f"Targeted Autonomous Repair initiated specifically for segment {target_segment}",
                extra={'target_segment': target_segment}
            )
        elif max_segments and len(underperforming) > max_segments:
            underperforming = underperforming[:max_segments]

        append_rd_log(
            'SCOREBOARD',
            f"Segment Scoreboard evaluated across {total_segments} slices: {len(underperforming)} underperforming segments prioritized by net cash leak under $1.00 capital defense.",
            extra={'underperforming_count': len(underperforming), 'alpha_count': scoreboard.get('counts', {}).get('alpha', 0)}
        )

        dossiers_count = 0
        hypotheses_count = 0
        crucible_count = 0
        passed_count = 0
        rejected_count = 0
        cycle_details = []

        for seg in underperforming:
            segment_key = seg['segment']
            # Direct alignment with AGENTS.md directive: ROI < -3% or net cash loss <= -$5.00, and at least 4 games
            if seg['games'] < 4 or (seg['roi_pct'] > -3.0 and seg['pnl'] > -5.0) or seg['pnl'] >= 0:
                continue

            append_rd_log(
                'FORENSICS',
                f"Decomposing failure modes for {segment_key} (games: {seg['games']}, PnL: ${seg['pnl']:.2f}, ROI: {seg['roi_pct']}%)",
                extra={'segment': segment_key, 'pnl': seg['pnl'], 'games': seg['games']}
            )

            # 2. Forensic attribution
            dossier = run_forensic_investigation(segment_key, self.conn)
            if not dossier:
                append_rd_log('FORENSICS', f"Insufficient settled wagers in {segment_key} to isolate statistical variance from skill deficit.")
                continue
            dossiers_count += 1

            append_rd_log(
                'ATTRIBUTION',
                f"Primary root failure identified in {segment_key}: {dossier.primary_failure_mode} (confidence: {dossier.confidence_score*100:.0f}%, remedy: {dossier.actionable_remedy})",
                extra={'segment': segment_key, 'failure_mode': dossier.primary_failure_mode}
            )

            # 3. Cognitive domain reasoning
            diagnostic_report = synthesize_domain_investigation(dossier)

            # 3.b Multi-AI Quant Council Deliberation (Antigravity, Codex, Claude, Grok)
            try:
                from .quant_council import convene_quant_council
                council_session = convene_quant_council(dossier, conn=self.conn)
                if council_session and council_session.synthesized_hypothesis:
                    from .ai_investigator import DiagnosticHypothesis
                    c_hyp_dict = council_session.synthesized_hypothesis
                    c_hyp = DiagnosticHypothesis(**c_hyp_dict)
                    # Deduplicate: ensure not already in report
                    if not any(h.hypothesis_id == c_hyp.hypothesis_id for h in diagnostic_report.hypotheses):
                        diagnostic_report.hypotheses.append(c_hyp)
                    append_rd_log(
                        'QUANT_COUNCIL',
                        f"Quant Council convened for {segment_key}: synthesized multi-AI hypothesis '{c_hyp.title}' "
                        f"under mandatory Win Rate and Net PnL uptrend directive.",
                        extra={'hypothesis_id': c_hyp.hypothesis_id, 'session_id': council_session.session_id}
                    )
            except Exception as e:
                append_rd_log('QUANT_COUNCIL_INFO', f"Quant council skipped/cached: {e}")

            append_rd_log(
                'COGNITIVE_AI',
                f"Sports intelligence reasoning for {segment_key}: {diagnostic_report.primary_sports_mechanic}. Formulated {len(diagnostic_report.hypotheses)} mathematical candidate rules.",
                extra={'segment': segment_key, 'mechanic': diagnostic_report.primary_sports_mechanic}
            )

            # 4. Enqueue hypotheses into durable journal
            active_hypotheses = process_diagnostic_report(diagnostic_report, self.journal)
            hypotheses_count += len(active_hypotheses)

            seg_detail = {
                'segment': segment_key,
                'failure_mode': dossier.primary_failure_mode,
                'sports_mechanic': diagnostic_report.primary_sports_mechanic,
                'hypotheses_tested': []
            }

            # 5. Execute Walk-Forward Crucible for each active hypothesis
            for hyp in active_hypotheses:
                if hyp.status in ('passed_crucible', 'rejected', 'deployed'):
                    continue

                crucible_count += 1
                append_rd_log(
                    'CRUCIBLE',
                    f"Crucible Walk-Forward Backtest initiated: testing {hyp.hypothesis_id} ('{hyp.title}') on historical wagers without lookahead leakage...",
                    extra={'hypothesis_id': hyp.hypothesis_id, 'segment': segment_key}
                )

                res = run_crucible_walk_forward(hyp, self.conn)

                if res.passed:
                    passed_count += 1
                    self.journal.update_result(
                        hypothesis_id=hyp.hypothesis_id,
                        status='deployed',
                        brier_imp=res.brier_improvement,
                        roi_delta=res.roi_delta_pct,
                        evidence=res.to_dict(),
                        note=f"Passed crucible in cycle {cycle_id} with +{res.roi_delta_pct}% ROI delta. Automatically deployed into live production engine."
                    )
                    status_label = 'PASSED'
                    append_rd_log(
                        'CRUCIBLE_VERDICT',
                        f"CRUCIBLE PASS & DEPLOY: {hyp.hypothesis_id} beat baseline across {res.sample_size} wagers (Brier: {res.baseline_brier:.4f} -> {res.candidate_brier:.4f}, ΔBrier: +{res.brier_improvement:.4f}, simulated ΔROI: +{res.roi_delta_pct:.1f}%). Candidate automatically deployed into live decision engine.",
                        level='SUCCESS',
                        extra={'hypothesis_id': hyp.hypothesis_id, 'brier_imp': res.brier_improvement}
                    )
                else:
                    rejected_count += 1
                    self.journal.update_result(
                        hypothesis_id=hyp.hypothesis_id,
                        status='rejected',
                        brier_imp=res.brier_improvement,
                        roi_delta=res.roi_delta_pct,
                        evidence=res.to_dict(),
                        note=f"Rejected in cycle {cycle_id}: {'; '.join(res.failure_reasons)}"
                    )
                    try:
                        from .quant_council import invalidate_segment_cache
                        invalidate_segment_cache(segment_key)
                    except Exception:
                        pass
                    status_label = 'REJECTED'
                    append_rd_log(
                        'CRUCIBLE_VERDICT',
                        f"CRUCIBLE REJECT: {hyp.hypothesis_id} failed gates: {'; '.join(res.failure_reasons)}. Permanently blacklisted in journal to prevent cyclic re-testing.",
                        level='WARN',
                        extra={'hypothesis_id': hyp.hypothesis_id, 'reasons': res.failure_reasons}
                    )

                seg_detail['hypotheses_tested'].append({
                    'id': hyp.hypothesis_id,
                    'title': hyp.title,
                    'status': status_label,
                    'brier_improvement': res.brier_improvement,
                    'roi_delta_pct': res.roi_delta_pct
                })

            # MANDATORY 3-DIMENSIONAL CAPABILITY EXPANSION HOOK:
            # When formulaic in-sample tuning is insufficient or cash leak is persistent,
            # synthesize and enact a 3-Dimensional Capability Expansion Plan.
            has_passing_gain = any(h.get('status') == 'PASSED' and h.get('roi_delta_pct', 0) > 0 for h in seg_detail['hypotheses_tested'])
            if not has_passing_gain or seg.get('pnl', 0.0) <= -5.0 or seg.get('roi_pct', 0.0) < -3.0:
                try:
                    from .capability_expansion import synthesize_capability_expansion_plan, CapabilityPlanJournal
                    if self.cap_journal is not None:
                        cap_journal = self.cap_journal
                    elif self.journal and hasattr(self.journal, 'journal_path'):
                        cap_journal = CapabilityPlanJournal(self.journal.journal_path.parent / 'capability_expansion_plans.json')
                    else:
                        cap_journal = CapabilityPlanJournal()
                    last_res = res if 'res' in locals() else None
                    plan = synthesize_capability_expansion_plan(
                        segment=segment_key,
                        conn=self.conn,
                        dossier=dossier,
                        crucible_result=last_res
                    )
                    cap_journal.upsert_plan(plan)
                    seg_detail['capability_expansion_plan'] = plan.plan_id
                    append_rd_log(
                        'CAPABILITY_EXPANSION',
                        f"3-Dimensional Capability Expansion Plan synthesized for {segment_key} (leak: ${plan.cash_leak_pnl:+.2f}, ROI: {plan.realized_roi_pct:+.1f}%): "
                        f"Data Reqs: {len(plan.data_requirements)}, Portfolio Shields: {len(plan.portfolio_shield_rules)}, UI Badges: {len(plan.ui_enhancements)}. "
                        f"Patch Scope: '{plan.actionable_ai_patch_scope}'",
                        level='INFO',
                        extra={'plan_id': plan.plan_id, 'segment': segment_key, 'portfolio_shields': len(plan.portfolio_shield_rules)}
                    )
                except Exception as e:
                    append_rd_log('CAPABILITY_EXPANSION_ERROR', f"Capability expansion synthesis error for {segment_key}: {e}", level='WARN')

            cycle_details.append(seg_detail)

        summary = RDCycleSummary(
            cycle_id=cycle_id,
            executed_at=now,
            underperforming_segments_checked=len(underperforming),
            forensic_dossiers_created=dossiers_count,
            hypotheses_generated=hypotheses_count,
            crucible_tests_run=crucible_count,
            candidates_passed=passed_count,
            candidates_rejected=rejected_count,
            details=cycle_details
        )

        append_rd_log(
            'CYCLE_COMPLETE',
            f"Autonomous R&D iteration {cycle_id} completed successfully: inspected {len(underperforming)} segments, created {dossiers_count} forensic dossiers, generated {hypotheses_count} hypotheses, ran {crucible_count} Crucible backtests ({passed_count} passed, {rejected_count} rejected).",
            extra={'cycle_id': cycle_id, 'passed': passed_count}
        )

        save_cycle_record(summary)
        try:
            from .quant_inquisitor import run_continuous_inquisitor_cycle
            inq_res = run_continuous_inquisitor_cycle(self.conn, self.journal, limit=3)
            append_rd_log(
                'INQUISITOR_BRAINSTORMING_COMPLETE',
                f"Proactive Quant Inquisitor executed: asked {inq_res.get('questions_inquired')} questions, brainstormed {inq_res.get('hypotheses_brainstormed')} hypotheses into shadow observation.",
                extra={'inquisitor_result': inq_res}
            )
        except Exception as e:
            append_rd_log('INQUISITOR_BRAINSTORMING_ERROR', f"Proactive Inquisitor skipped: {e}")
        try:
            from .lab_meta_reviewer import run_lab_meta_review
            run_lab_meta_review(self.conn)
            append_rd_log(
                'META_SYNTHESIS_COMPLETE',
                'Holistic AI lab meta-review and cross-factor synthesis completed successfully.',
                extra={'cycle_id': cycle_id}
            )
        except Exception as e:
            append_rd_log('META_SYNTHESIS_ERROR', f"Meta-review skipped: {e}")
        return summary



def trigger_rd_cycle(conn: sqlite3.Connection) -> RDCycleSummary:
    """Helper entry point to trigger an on-demand R&D self-improvement run."""
    agent = SupervisedQuantRDAgent(conn)
    return agent.run_autonomous_cycle()
