"""Controller-export integration replay, never current execution evidence.

Required inputs are hash-bound by the supplied manifest. Missing inputs fail
this acceptance suite; no model fit, network call, Engine or ledger is used.
"""
from copy import deepcopy
from datetime import timedelta
import gzip
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
import pytest

from betting_engine import decisions, governance
from betting_engine.contracts import split_market
from betting_engine.domain import Game, parse_time
from betting_engine.governance import EvidenceGuard, KnowledgeAgent
from betting_engine.instructions import current_execution_review
from betting_engine.markets import StakeSportsbook

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


@pytest.fixture(scope='module')
def exported():
    manifest = json.loads((DATA / 'development_inputs/manifest.json').read_text())
    assert manifest['state'] == 'exported' and not manifest['errors']
    files = {f['path']: f for f in manifest['files']}

    def bound(path):
        row = files[path]
        target = ROOT / path
        assert target.resolve().is_relative_to(DATA.resolve())
        assert not target.is_symlink()
        raw = target.read_bytes()
        assert len(raw) == row['bytes']
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        return raw

    publication = json.loads(bound('data/development_inputs/publication.json'))['payload']
    models = {}
    for entry in manifest['models']:
        if entry['league'] == 'MLB':
            continue  # Separate inning-count model, no marginal-period substitution.
        stem = f"data/period_models/{entry['league']}-{entry['scope']}"
        artifact = bound(stem + '.joblib')
        report_bytes = bound(stem + '.json')
        assert hashlib.sha256(artifact).hexdigest() == entry['artifact_sha256']
        assert hashlib.sha256(report_bytes).hexdigest() == entry['report_sha256']
        model = joblib.load(io.BytesIO(artifact))
        report = json.loads(report_bytes)
        assert model.report == report
        assert report['version'] == entry['version']
        assert report['promotion_gate']['promote'] is True
        assert report['odds_used'] is False
        assert parse_time(report['fit_through']) < parse_time(report['test_from'])
        assert report['partitions']['test'] == report['test']['n']
        models[(entry['league'], entry['scope'])] = model
    return publication, models, bound


def test_frozen_forecasts_reproduce_with_original_features_and_models(exported):
    publication, models, _ = exported
    games = {g['id']: Game(**deepcopy(g)) for g in publication['games']}
    checked = 0
    for recorded in publication['segment_forecasts'].values():
        key = recorded['league'], recorded['scope']
        if key not in models:
            continue
        model = models[key]
        vector = np.array([recorded['features'][k] for k in model.report['features']])
        forecast = model.forecast(games[recorded['game_id']], SimpleNamespace(features=lambda g: vector))
        assert forecast.model_version == recorded['model_version']
        assert forecast.home_points == pytest.approx(recorded['home_points'], abs=1e-12)
        assert forecast.away_points == pytest.approx(recorded['away_points'], abs=1e-12)
        assert forecast.model_probabilities == pytest.approx(recorded['model_probabilities'], abs=1e-12)
        checked += 1
    assert checked > 0


@pytest.mark.parametrize('league', ['NHL', 'WNBA', 'NFL'])
def test_exact_archived_offers_reproduce_candidate_and_guard_checks(exported, monkeypatch, league):
    publication, models, bound = exported
    tips = [t for t in publication['evaluated_candidates']
            if t['league'] == league and split_market(t['market'])[0] != 'full_game']
    assert tips
    games = {g['id']: Game(**deepcopy(g)) for g in publication['games']}
    guard = EvidenceGuard(KnowledgeAgent(ROOT), data_dir=DATA)
    book = StakeSportsbook(fetcher=object(), segment_markets={key: m.report['validated_markets'] for key, m in models.items()})
    cache = {}
    for original in tips:
        before = deepcopy(original)
        scope, _ = split_market(original['market'])
        game = games[original['game_id']]
        source = original['stake_evidence']['api_source']
        cache_key = (game.id, source['sha256'], source['fetched_at'])
        if cache_key not in cache:
            raw = gzip.decompress(bound('data/' + source['archive']))
            assert hashlib.sha256(raw).hexdigest() == source['sha256']
            body = json.loads(raw)
            fixture = body['fixture']
            # The schedule endpoint calls this field date; fixture responses
            # use startTime. Preserve its exact value when adapting the schema.
            assert book._match_fixture({**fixture, 'date': fixture['startTime']}, [game], league).id == game.id
            assert parse_time(game.start).timestamp() * 1000 == fixture['startTime']
            markets = book._markets_for_fixture(body, fixture, game, league, source['fetched_at'])
            for m in markets:
                m.metadata['api_source'] = deepcopy(source)
            cache[cache_key] = {m.id: m for m in markets}
        market = cache[cache_key][original['reference_market_id']]
        assert market.kind == original['market']
        assert market.side == original['side'] and market.line == original['line']
        assert market.metadata['stake_specifiers'] is not None
        quote = book.quotes([market])[0][market.id]
        model = models[(league, scope)]
        recorded = publication['segment_forecasts'][f'{game.id}:{scope}']
        vector = np.array([recorded['features'][k] for k in model.report['features']])
        forecast = model.forecast(game, SimpleNamespace(features=lambda g: vector))
        # Replay at the original observed check instant; timestamps are unmodified.
        at = parse_time(original['decision_guard']['checked_at'])
        monkeypatch.setattr(decisions, 'utcnow', lambda: at)
        monkeypatch.setattr(governance, 'utcnow', lambda: at)
        tip = decisions.candidate(game, market, quote, forecast, publication['settings'])
        assert tip is not None
        for key in ('probability', 'push_probability', 'expected_roi', 'stress_roi', 'reference_odds'):
            assert tip[key] == pytest.approx(original[key], abs=1e-12)
        tip['stake_evidence'] = deepcopy(original['stake_evidence'])
        review = guard.check(tip, game, market, quote, forecast, model.report, publication['settings'])
        assert review['passed'], review['errors']
        tampered = deepcopy(tip)
        tampered['probability'] += .001
        rejected = guard.check(tampered, game, market, quote, forecast, model.report, publication['settings'])
        assert 'calculation_mismatch:probability' in rejected['errors']
        expired = parse_time(quote.fetched_at) + timedelta(seconds=301)
        monkeypatch.setattr(decisions, 'utcnow', lambda: expired)
        assert decisions.candidate(game, market, quote, forecast, publication['settings']) is None
        assert not current_execution_review(tip, expired, publication['settings']['minimum_roi'])['actionable']
        assert original == before


def test_report_family_rejection_is_not_overridden_by_contract_recognition(exported):
    _, models, _ = exported
    model = models[('WNBA', 'quarter_1')]
    assert 'totals' not in model.report['validated_markets']
    from test_e514cb13f60_scope_identity import TOTALS
    case = next(c for c in TOTALS if c['row']['league'] == 'WNBA')
    raw = deepcopy(case['row']['raw_contract'])
    raw['name'] = '1st Quarter - Total'
    raw['specifiers'] = 'quarternr=1|total=20.5'
    for outcome, side in zip(raw['outcomes'], ('Over', 'Under')):
        outcome['name'] = side + ' 20.5'
    book = StakeSportsbook(fetcher=object(), segment_markets={('WNBA', 'quarter_1'): model.report['validated_markets']})
    assert book._markets_for_fixture({'groups': [{'name': 'quarters', 'markets': [[raw]]}]},
        {'id': 'mutation-only', 'slug': 'mutation-only'}, Game(**case['game']), 'WNBA', case['row']['source']['fetched_at']) == []
