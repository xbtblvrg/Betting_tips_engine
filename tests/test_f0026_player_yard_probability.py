"""Synthetic yard distributions, pinned D06 integration, and source-schema checks."""
from copy import deepcopy
from dataclasses import asdict, replace, FrozenInstanceError
import hashlib
import json
import math
from pathlib import Path

import pytest
from scipy import integrate, stats

from betting_engine.player_model_study import METRICS
from betting_engine.player_prop_probability import load_count_forecast
from betting_engine.player_yard_probability import (
    YardForecast, RECIPES, evaluate_yard_payoff, load_yard_forecast, yard_log_mass,
)

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / 'docs/d06_player_model_study_evidence'
FOLDER = 'NFL_player_rushing_yards'


def sha(body):
    return hashlib.sha256(body).hexdigest()


def forecast(**changes):
    return replace(YardForecast('NFL/player/rushing_yards', 'nflverse-gsis',
        'full_game_including_overtime', 'synthetic-player', 'synthetic-team',
        'player_laplace', 0., 1., 'player_team', 'a'*64, 'b'*64, 'c'*64, 'd'*64,
        '2025-02-01T00:00:00Z', '2025-03-01T00:00:00Z', ('synthetic_fixture_only',)), **changes)


def evaluate(model=None, **kwargs):
    return evaluate_yard_payoff(model or forecast(), **kwargs)


def repin(root):
    index = {str(p.relative_to(root)): sha(p.read_bytes()) for p in root.rglob('*.json')
             if p.name != 'artifacts.json'}
    (root / 'artifacts.json').write_text(json.dumps(index))
    return sha((root / 'artifacts.json').read_bytes())


def synthetic_archive(tmp_path, recipe='player_laplace'):
    root = tmp_path / 'study'
    (root / FOLDER).mkdir(parents=True)
    plan = dict(mode='retrospective_descriptive', odds_used=False,
        metrics=[asdict(METRICS[-1])], continuous_scale_floor=1., shrinkage=5.,
        selection_score='game_balanced_nll', selection_start='2025-01-01T00:00:00Z',
        test_start='2025-02-01T00:00:00Z', as_of='2025-03-01T00:00:00Z', input_sha256='d'*64)
    (root / 'plan.json').write_text(json.dumps(plan))
    selection = {r: {'game_balanced_nll': 1. if r == recipe else 2.} for r in RECIPES}
    study = dict(metric='NFL/player/rushing_yards', state='retrospective_comparison_completed',
        validation_gate={'accepted': False, 'reasons': ['historical_availability_not_authenticated']},
        selected_recipe=recipe, selection=selection,
        split_counts={s: {'rows': 100} for s in ('train', 'selection', 'test')})
    report = dict(plan_sha256=sha((root / 'plan.json').read_bytes()), odds_used=False,
        production_props_enabled=False, historical_availability_authenticated=False, studies=[study])
    (root / 'report.json').write_text(json.dumps(report))
    (root / FOLDER / 'selection-lock.json').write_text(json.dumps(dict(
        selected_recipe=recipe, selection=selection, test_used_for_selection=False)))
    (root / FOLDER / (recipe + '-test-model.json')).write_text(json.dumps(dict(
        recipe=recipe, global_mean=-1., player_team_means={} if recipe == 'pooled_normal' else {'["p","t"]': -3.},
        shrinkage=5., training_rows=200, scale=2.)))
    return root, repin(root)


def load(root, pin, **kwargs):
    return load_yard_forecast(root, artifact_index_sha256=pin,
        player_id=kwargs.pop('player_id', 'p'), team_id=kwargs.pop('team_id', 't'), **kwargs)


def mutate(root, relative, change):
    path = root / relative
    value = json.loads(path.read_bytes())
    change(value)
    path.write_text(json.dumps(value))
    if relative == 'plan.json':
        mutate(root, 'report.json', lambda r: r.update(plan_sha256=sha(path.read_bytes())))
    return repin(root)


def test_laplace_push_is_interval_mass_not_continuous_density():
    result = evaluate(operator='over', line=0, decimal_odds=2)
    assert result['p_push'] == pytest.approx(1 - math.exp(-.5))
    assert result['p_win'] == result['p_loss'] == pytest.approx(.5 * math.exp(-.5))
    assert result['p_push'] != stats.laplace.pdf(0)
    assert result['scenario_net_ev_per_unit'] == pytest.approx(0)
    assert result['p_void'] is None


@pytest.mark.parametrize('recipe', RECIPES)
@pytest.mark.parametrize('k', [-12, -3, 0, 2, 17])
def test_mass_matches_independent_density_quadrature(recipe, k):
    f = forecast(recipe=recipe, location=-1.25, scale=3.)
    density = stats.laplace if recipe.endswith('laplace') else stats.norm
    expected = integrate.quad(lambda x: density.pdf(x, loc=-1.25, scale=3.), k-.5, k+.5)[0]
    assert math.exp(yard_log_mass(f, k)) == pytest.approx(expected, rel=1e-10)


@pytest.mark.parametrize('recipe', RECIPES)
def test_negative_support_and_integer_translation(recipe):
    f = forecast(recipe=recipe)
    assert evaluate(f, operator='under', line=-.5)['p_win'] > 0
    for k in (-20, -1, 0, 3, 50):
        assert yard_log_mass(f, k) == pytest.approx(yard_log_mass(replace(f, location=-7), k-7))
    assert sum(math.exp(yard_log_mass(f, k)) for k in range(-100, 101)) == pytest.approx(1.)


@pytest.mark.parametrize('line', [-1000000, -100., -2.5, -2, -.5, 0, .5, 7, 100., 1000000])
@pytest.mark.parametrize('recipe', ['player_laplace', 'player_normal'])
def test_complements_mass_and_changed_line_monotonicity(recipe, line):
    f = forecast(recipe=recipe)
    a = evaluate(f, operator='over', line=line)
    b = evaluate(f, operator='under', line=line)
    assert a['p_win'] == b['p_loss'] and a['p_loss'] == b['p_win']
    assert sum(a[k] for k in ('p_win', 'p_loss', 'p_push')) == pytest.approx(1., abs=1e-12)
    if int(line) == line:
        ladder = evaluate(f, operator='at_least', line=line)
        assert ladder['p_win'] == pytest.approx(a['p_win'] + a['p_push'])
        assert ladder['p_push'] == 0
    if abs(line) < 1000000:
        earlier = evaluate(f, operator='over', line=line-.5)
        assert earlier['p_win'] >= a['p_win']


@pytest.mark.parametrize('recipe,line', [('player_normal', 10), ('player_laplace', 50)])
def test_stable_tail_mass_when_cdf_has_rounded_to_one(recipe, line):
    dist = stats.norm if recipe.endswith('normal') else stats.laplace
    assert dist.cdf(line-.5) == 1.
    result = evaluate(forecast(recipe=recipe), operator='over', line=line)
    assert result['p_win'] > 0 and result['p_push'] > 0
    assert math.isfinite(yard_log_mass(forecast(recipe=recipe), 1000000))


def test_price_independence_ev_refund_and_immutable_forecast():
    f = forecast(location=-2.3, scale=4.)
    before = asdict(f)
    a = evaluate(f, operator='under', line=-2, decimal_odds=1.5)
    b = evaluate(f, operator='under', line=-2, decimal_odds=3.5)
    for key in ('forecast', 'p_win', 'p_loss', 'p_push', 'fair_decimal_odds'):
        assert a[key] == b[key]
    assert asdict(f) == before
    assert a['scenario_net_ev_per_unit'] == pytest.approx(a['p_win']*.5-a['p_loss'])
    assert a['scenario_net_ev_per_unit'] < b['scenario_net_ev_per_unit']
    assert evaluate(f, operator='under', line=-2, decimal_odds=a['fair_decimal_odds'])['scenario_net_ev_per_unit'] == pytest.approx(0, abs=1e-14)
    assert not a['publishable'] and not a['production_props_enabled'] and not a['execution_eligible']
    assert 'rounded_integer_distribution_not_validated' in a['activation_blockers']
    assert a['assessment_sha256'] != b['assessment_sha256']
    with pytest.raises(FrozenInstanceError):
        f.scale = 2


@pytest.mark.parametrize('line', [True, None, 'NaN', 'Infinity', '-0.25', '2.50000000000000000000000000000001', 1000001])
def test_bad_lines_rejected_without_rounding(line):
    with pytest.raises(ValueError):
        evaluate(operator='over', line=line)


@pytest.mark.parametrize('odds', [True, 0, 1, 'NaN', 'Infinity', '1000000000001'])
def test_bad_prices_rejected(odds):
    with pytest.raises(ValueError):
        evaluate(operator='over', line=0, decimal_odds=odds)


@pytest.mark.parametrize('change', [dict(recipe='player_nb'), dict(metric_key='NFL/player/passing_yards'),
    dict(namespace='espn'), dict(stat_scope='quarter_1'), dict(discretization='truncate_at_zero'),
    dict(location=float('nan')), dict(location=True), dict(location=1000001),
    dict(scale=0), dict(scale=.5), dict(scale=float('inf'))])
def test_other_scopes_and_parameters_rejected(change):
    with pytest.raises(ValueError):
        evaluate(forecast(**change), operator='over', line=0)


@pytest.mark.parametrize('operator,line', [('yes', 1), ('at_least', .5), ('over', 'x')])
def test_unsupported_operators_and_ladders(operator, line):
    with pytest.raises(ValueError):
        evaluate(operator=operator, line=line)


@pytest.mark.parametrize('value', [None, True, .5, 'NaN', 1000001])
def test_integer_mass_rejects_missing_fractional_targets(value):
    with pytest.raises(ValueError):
        yard_log_mass(forecast(), value)


@pytest.mark.parametrize('recipe', RECIPES)
def test_model_loader_uses_signed_means_exact_team_and_pooled_fallback(tmp_path, recipe):
    root, pin = synthetic_archive(tmp_path, recipe)
    f = load(root, pin)
    assert f.location == (-1 if recipe == 'pooled_normal' else -3)
    other = load(root, pin, team_id='other')
    assert other.location == -1 and other.location_basis == 'pooled_fallback'
    assert f.fit_event_start_cutoff == '2025-02-01T00:00:00Z'
    assert f.validation_reasons == ('historical_availability_not_authenticated',)


@pytest.mark.parametrize('relative,change,error', [
    ('plan.json', lambda p: p.update(odds_used=True), 'independent'),
    ('plan.json', lambda p: p.update(continuous_scale_floor=.1), 'semantics'),
    ('plan.json', lambda p: p.update(test_start=p['selection_start']), 'chronological'),
    ('plan.json', lambda p: p.update(input_sha256=None), 'input_hash'),
    ('report.json', lambda p: p['studies'].append(deepcopy(p['studies'][0])), 'ambiguous'),
    ('report.json', lambda p: p['studies'][0]['validation_gate'].update(accepted=True), 'study_state'),
    (FOLDER+'/selection-lock.json', lambda p: p.update(test_used_for_selection=True), 'lock'),
    (FOLDER+'/player_laplace-test-model.json', lambda p: p.update(training_rows=300), 'membership'),
    (FOLDER+'/player_laplace-test-model.json', lambda p: p.update(scale=.1), 'parameters'),
    (FOLDER+'/player_laplace-test-model.json', lambda p: p.update(extra=True), 'schema'),
    (FOLDER+'/player_laplace-test-model.json', lambda p: p.update(player_team_means={'["p", "t"]': 3}), 'mean'),
])
def test_semantic_corruption_rejects_even_with_repinned_bytes(tmp_path, relative, change, error):
    root, _ = synthetic_archive(tmp_path)
    pin = mutate(root, relative, change)
    with pytest.raises(ValueError, match=error):
        load(root, pin)


def test_recipe_choice_never_uses_outer_test_scores(tmp_path):
    root, _ = synthetic_archive(tmp_path)
    pin = mutate(root, 'report.json', lambda p: p['studies'][0].update(test={'player_laplace': {'game_balanced_nll': 999999}}))
    assert load(root, pin).recipe == 'player_laplace'
    # Consistent report+lock lying about the selection winner still fails.
    for rel in ('report.json', FOLDER+'/selection-lock.json'):
        def change(p):
            s = p['studies'][0] if 'studies' in p else p
            s['selection']['player_normal']['game_balanced_nll'] = 0.
        pin = mutate(root, rel, change)
    with pytest.raises(ValueError, match='earlier_selection'):
        load(root, pin)


def test_hash_drift_duplicate_json_and_symlink_rejected(tmp_path):
    root, pin = synthetic_archive(tmp_path)
    with pytest.raises(ValueError, match='index_hash'):
        load(root, 'f'*64)
    model = root / FOLDER / 'player_laplace-test-model.json'
    body = model.read_bytes()
    model.write_bytes(body + b' ')
    with pytest.raises(ValueError, match='artifact_hash'):
        load(root, pin)
    model.write_bytes(body[:-1] + b',"scale":2}')
    pin = repin(root)
    with pytest.raises(ValueError, match='duplicate'):
        load(root, pin)
    model.unlink()
    model.symlink_to(root/'plan.json')
    with pytest.raises(ValueError, match='unsafe_artifact'):
        load(root, pin)


def test_real_selected_model_loads_but_original_count_reader_still_rejects_nfl():
    pin = sha((STUDY/'artifacts.json').read_bytes())
    f = load(STUDY, pin, player_id='00-0023853', team_id='ARI')
    assert f.recipe == 'player_laplace' and f.location_basis == 'player_team'
    assert f.scale == pytest.approx(2.840967521187697)
    result = evaluate(f, operator='under', line=0)
    assert result['p_push'] > 0 and not result['publishable']
    assert f.validation_reasons
    with pytest.raises(ValueError, match='discrete_distribution_required'):
        load_count_forecast(STUDY, artifact_index_sha256=pin,
            metric_key='NFL/player/rushing_yards', player_id=f.player_id, team_id=f.team_id)


@pytest.mark.parametrize('operator,line', [('over', -2), ('under', -2), ('over', -2.5), ('at_least', -2)])
def test_signed_probability_weights_agree_with_existing_payout_evaluator(operator, line):
    # Reuse the existing explicitly synthetic settlement fixture unchanged.
    from test_player_prop_settlement import case, settle
    f = forecast(location=-2., scale=1.)
    expected = 0.
    template = case(operator, str(line), 0, metric='rushing_yards', league='NFL')
    for actual in range(-50, 51):
        args = deepcopy(template)
        args[1]['value'] = actual
        body = json.dumps({'synthetic_yards': actual}).encode()
        args[1]['source']['sha256'] = sha(body)
        args[3][sha(body)] = body
        payout = settle(args)
        assert payout['state'] == 'settled'
        expected += math.exp(yard_log_mass(f, actual)) * float(payout['profit']) / float(args[0]['stake'])
    result = evaluate(f, operator=operator, line=line, decimal_odds='1.84')
    assert result['scenario_net_ev_per_unit'] == pytest.approx(expected, abs=1e-12)
    args = case(operator, str(line), None, metric='rushing_yards', league='NFL')
    args[1]['participation'] = {'played': False, 'final': True}
    assert settle(args)['result'] == 'void'
    assert result['p_void'] is None  # Settling a known DNP does not estimate its probability.
