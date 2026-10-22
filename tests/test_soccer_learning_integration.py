import io
import json
from pathlib import Path
import joblib
import pytest

from betting_engine.domain import utcnow
from betting_engine.soccer_model import SoccerModel
from betting_engine.train import train
from betting_engine.learning import LearningWorker
from betting_engine.failure_forensics import ForensicDossier
from betting_engine.ai_investigator import synthesize_domain_investigation
from betting_engine.hypothesis_engine import ResearchRecord
from betting_engine.quant_crucible import apply_hypothesis_simulation


def test_soccer_retraining_execution(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True)
    (data_dir / 'models').mkdir()
    (data_dir / 'games.json').write_text(json.dumps([]))
    import betting_engine.train
    monkeypatch.setattr(betting_engine.train, 'DATA', data_dir)
    reports = train(leagues=['EPL', 'UCL'], controlled=True)
    assert 'EPL' in reports
    assert 'UCL' in reports
    assert reports['EPL'].get('version') == 'EPL-dixon-coles-v1'
    assert reports['EPL'].get('promotion_gate', {}).get('promote') is True
    assert reports['UCL'].get('version') == 'UCL-dixon-coles-v1'
    assert reports['UCL'].get('promotion_gate', {}).get('promote') is True


def test_learning_worker_controlled_retrain_soccer(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir(parents=True)
    (data_dir / 'models').mkdir()
    (data_dir / 'games.json').write_text(json.dumps([]))
    import betting_engine.train
    monkeypatch.setattr(betting_engine.train, 'DATA', data_dir)
    from betting_engine.storage import Store
    worker = LearningWorker(store=Store(tmp_path / 'db.sqlite'), data_dir=data_dir)
    job = {'kind': 'controlled_retrain', 'league': 'EPL'}
    res = worker.execute(job)
    assert res['league'] == 'EPL'
    assert res['promotion_gate']['promote'] is True
    assert res['test']['brier'] > 0


def test_ai_investigator_soccer_domain_reasoning():
    dossier = ForensicDossier(
        segment="EPL|full_game|three_way",
        league="EPL",
        scope="full_game",
        family="three_way",
        evaluated_at=utcnow().isoformat(),
        total_bets=10,
        losses=7,
        wins=3,
        pushes=0,
        win_rate_pct=30.0,
        total_staked=10.0,
        total_pnl=-3.5,
        roi_pct=-35.0,
        primary_failure_mode="draw_compression",
        confidence_score=0.88,
        failure_breakdown={"draw_rate": 28.0},
        actionable_remedy="Apply away draw protection filter"
    )
    report = synthesize_domain_investigation(dossier)
    assert report.league == "EPL"
    assert "Draw compression" in report.primary_sports_mechanic
    assert len(report.hypotheses) > 0
    assert report.hypotheses[0].category == "filter_gate"
    assert "Asian Handicap" in report.hypotheses[0].title


def test_quant_crucible_soccer_simulation():
    rec = ResearchRecord(
        hypothesis_id="hyp_soccer_draw_protection_test",
        segment="EPL|full_game|three_way",
        category="filter_gate",
        proposed_formula_or_rule="away_draw_protection",
        title="Soccer Away Draw Protection",
        rationale="Avoid raw away moneyline",
        league="EPL",
        status="staged_recovery",
        created_at=utcnow().isoformat(),
        updated_at=utcnow().isoformat()
    )
    # Decision: Away side on three_way market should be filtered out
    dec_away = {
        'payload': {
            'market': 'three_way',
            'side': 'away',
            'reference_odds': 2.80,
            'probability': 0.40
        }
    }
    p, inc = apply_hypothesis_simulation(dec_away, rec)
    assert inc is False  # Filtered out!

    # Decision: Home side on three_way market should be kept
    dec_home = {
        'payload': {
            'market': 'three_way',
            'side': 'home',
            'reference_odds': 1.95,
            'probability': 0.55
        }
    }
    p, inc = apply_hypothesis_simulation(dec_home, rec)
    assert inc is True  # Kept!
