import json
from types import SimpleNamespace

import joblib
import pytest

from betting_engine import train, training_evidence
from test_models import game


@pytest.mark.parametrize('kwargs', [{}, {'controlled': False}, {'controlled': True}])
def test_existing_champion_cannot_bypass_rejected_gate(tmp_path, monkeypatch, kwargs):
    monkeypatch.setattr(train, 'DATA', tmp_path)
    (tmp_path/'models').mkdir()
    (tmp_path/'games.json').write_text(json.dumps([game('g', '2025-01-01').dict()]))
    path = tmp_path/'models/NBA.joblib'
    joblib.dump(SimpleNamespace(report={'version': 'champion'}), path)
    original = path.read_bytes()
    report = {'version': 'candidate', 'training_games': 1, 'test': {},
              'baseline': {}, 'brier_delta_ci95': []}
    model = SimpleNamespace(version='candidate', report=report)
    model.fit = None
    class Fitter:
        def fit(self, *args, **kw):
            return SimpleNamespace(version='candidate', report=dict(report))
    monkeypatch.setattr(train, 'LeagueModel', lambda *args: Fitter())
    monkeypatch.setattr(training_evidence, 'capture_inputs', lambda *a, **k: {})
    monkeypatch.setattr(training_evidence, 'bind_archive', lambda *a, **k: None)
    compared = []
    def reject(old, new):
        compared.append(old.report['version'])
        return {'promote': False, 'reason': 'Rejected comparison'}
    monkeypatch.setattr(train, 'promotion_gate', reject)
    result = train.train(leagues=['NBA'], **kwargs)
    assert compared == ['champion']
    assert result['NBA']['promotion_gate']['promote'] is False
    assert path.read_bytes() == original
    assert len(list((tmp_path/'models/versions').glob('*/report.json'))) == 1
