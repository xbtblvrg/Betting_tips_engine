"""Anti-tautological test verifying that AI development and self-healing prompts

receive real, deep, verbose debug telemetry rather than operating blind.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from betting_engine import claude_development as cd
from betting_engine.verbose_logger import get_verbose_logger


REPORT_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string'},
        'task_complete': {'type': 'boolean'},
        'remaining': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['summary', 'task_complete', 'remaining'],
    'additionalProperties': False,
}


def test_ai_prompt_contains_verbose_telemetry_and_errors(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    workspace = root / 'data/development/workspaces/test-D99'
    workspace.mkdir(parents=True)
    (workspace / 'src/betting_engine').mkdir(parents=True)
    (workspace / 'tests').mkdir()
    (workspace / 'docs').mkdir()
    (workspace / 'src/betting_engine/mod.py').write_text('X = 1\n')
    (workspace / 'AGENTS.md').write_text('Constitution rules\n')

    # Emit distinct telemetry events and a service error into the real or test data directory
    data_dir = root / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = data_dir / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)

    vl = get_verbose_logger(data_dir=data_dir)
    vl.trace_decision(
        candidate_id="cand_ai_telemetry_verify_123",
        fixture="Lakers vs Celtics",
        market="totals:over:225.5",
        prob=0.612,
        odds=1.92,
        ev=0.084,
        kelly_fraction=0.091,
        final_stake=1.75,
        accepted=True,
    )
    (logs_dir / 'service-error.log').write_text("CRITICAL: Database lock timeout during batch commit\n")

    transport = tmp_path / 'transport'
    transport.mkdir(mode=0o700)

    step = cd.prepare_step(
        root=root,
        workspace=workspace,
        directory=transport,
        task={'id': 'D99', 'title': 'Resolve batch commit lock'},
        prompt='Investigate batch commit lock and decision engine stability',
        result_schema=REPORT_SCHEMA,
    )

    stdin_content = step['stdin']

    # The AI instruction delivered via stdin must contain real system telemetry
    assert 'RUNTIME VERBOSE DEBUG TELEMETRY (REAL SYSTEM EVIDENCE)' in stdin_content
    # It must contain raw empirical telemetry, not arbitrary hallucinations
    assert 'cand_ai_telemetry_verify_123' in stdin_content or 'Database lock timeout' in stdin_content
    # The bounded prompt length must remain strictly within transport limits (< 1.5MB)
    assert len(stdin_content.encode('utf-8')) < 1536 * 1024
