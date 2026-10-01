"""Exact raw Stake scoped offers, plus adversarial specifier boundaries."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import gzip
import hashlib
import json
import re

import pytest

from betting_engine.closing_prices import _quote, evaluate_closing_price, snapshots_from_response
from betting_engine.scope_markets import named_scopes
from test_closing_prices import decision, snapshot, synchronize_raw, CUTOFF


def case(kind='totals', scope='period_1', league='NHL', spec=None):
    d, row = decision(), snapshot()
    tip = d['payload']
    suffix = {'period_1': '1st Period', 'period_2': '2nd Period', 'quarter_1': '1st Quarter',
              'quarter_4_ot': '4th Quarter', 'inning_1': '1st Inning', 'half_1': '1st Half'}[scope]
    label = {'totals': 'Total', 'home_total': 'Home Total', 'spreads': 'Handicap',
             'moneyline': 'Draw No Bet', 'three_way': '1x2', 'odd_even': 'Odd/Even',
             'both_score': 'Both Teams to Score', 'double_chance': 'Double Chance'}[kind]
    title = suffix + ' - ' + label + (' (Incl. Overtime)' if scope.endswith('_ot') else '')
    side, line, name = {'totals': ('over', 1.5, 'Over 1.5'), 'home_total': ('over', 1.5, 'Over 1.5'),
                       'spreads': ('home', -.5, 'Home (-0.5)'), 'moneyline': ('home', None, 'Home'),
                       'three_way': ('draw', None, 'Draw'), 'odd_even': ('odd', None, 'Odd'),
                       'both_score': ('yes', None, 'Yes'), 'double_chance': ('home_draw', None, 'Home or Draw')}[kind]
    selector = {'period_1': 'periodnr=1', 'period_2': 'periodnr=2', 'quarter_1': 'quarternr=1',
                'quarter_4_ot': 'quarternr=4', 'inning_1': 'inningnr=1', 'half_1': ''}[scope]
    if spec is None:
        spec = '|'.join(x for x in (selector, 'hcp=-0.5' if kind == 'spreads' else 'total=1.5' if 'total' in kind else '') if x)
    tip.update(league=league, market=f'{scope}:{kind}', model_scope=scope, side=side, line=line)
    tip['stake_evidence'].update(market_name=title, outcome_name=name)
    row.update(league=league, market=title, scope=scope, specifiers=spec)
    row['outcomes'][0]['name'] = name
    if kind == 'spreads':
        row['outcomes'].append({'id': 'other', 'name': 'Away (0.5)', 'active': True, 'odds': 1.8})
        row['raw_fixture']['name'] = 'Home - Away'
    synchronize_raw(row)
    return d, row


@pytest.mark.parametrize('scope,league', [('period_1', 'NHL'), ('period_2', 'NHL'), ('quarter_1', 'NFL'),
                                         ('quarter_4_ot', 'NFL'), ('inning_1', 'MLB'), ('half_1', 'WNBA')])
@pytest.mark.parametrize('kind', ['totals', 'home_total', 'spreads', 'moneyline', 'three_way', 'odd_even', 'both_score', 'double_chance'])
def test_validated_scope_keys_are_order_independent_and_immutable(scope, league, kind):
    d, row = case(kind, scope, league)
    if kind == 'home_total' and scope == 'quarter_4_ot':
        # No such named contract is supported; this is not a specifier exemption.
        with pytest.raises(ValueError, match='unsupported_contract_title'):
            _quote(row, d['payload'], kind, row['outcomes'][0]['name'], 'outcome')
        return
    before = deepcopy((d, row))
    assert evaluate_closing_price(d, [row], as_of=CUTOFF)['status'] == 'comparable'
    assert (d, row) == before
    row['specifiers'] = '|'.join(reversed(row['specifiers'].split('|')))
    synchronize_raw(row)
    result = evaluate_closing_price(d, [row], as_of=CUTOFF)
    assert result['status'] == 'comparable'
    assert result['price_clv'] == pytest.approx(.05)


@pytest.mark.parametrize('kind,spec', [
    ('totals', 'total=1.5|periodnr=2'), ('totals', 'total=1.5|quarternr=1'),
    ('totals', 'periodnr=01|total=1.5'), ('totals', 'periodnr=0|total=1.5'),
    ('totals', 'periodnr=1|total=1.5|inningnr=1'), ('totals', 'periodnr=1|periodnr=1|total=1.5'),
    ('totals', 'periodnr=1|total=1.5|total=1.5'), ('totals', 'periodnr=1|total=1.5|total=2.5'),
    ('totals', 'periodnr=1|total=1.5|'), ('totals', 'periodnr=1|total=1.5|hcp=1.5'),
    ('totals', 'periodnr=1|total=1.5garbage'), ('totals', 'periodnr=1|total=NaN'),
    ('totals', 'periodnr=1|total=1e309'), ('totals', 'periodnr=1|total=1.25'),
    ('totals', 'periodnr=1|total=-1.5'), ('totals', 'periodnr=1|total=2.5'),
    ('totals', 'periodnr=1|total=1.5|unknown=1'), ('totals', 'periodnr =1|total=1.5'),
    ('totals', None), ('totals', 15), ('totals', 'periodnr=1|total=' + '9'*600),
    ('spreads', 'periodnr=1'), ('spreads', 'periodnr=1|hcp=-.5'),
    ('spreads', 'periodnr=1|hcp=-0.25'), ('spreads', 'periodnr=1|hcp=-1.5'),
    ('spreads', 'periodnr=1|hcp=NaN'), ('spreads', 'periodnr=1|hcp=-0.5|hcp=-0.5'),
    ('moneyline', 'periodnr=1|total=1.5'), ('moneyline', 'periodnr=1|periodnr=2'),
])
def test_conflicting_unknown_duplicate_or_malformed_specifiers_stay_rejected(kind, spec):
    d, row = case(kind)
    row['specifiers'] = spec
    synchronize_raw(row)
    before = deepcopy((d, row))
    result = evaluate_closing_price(d, [row], as_of=CUTOFF)
    assert result['status'] == 'missing_closing_price'
    assert result['price_clv'] is None
    assert result['rejection_counts']
    assert (d, row) == before


@pytest.mark.parametrize('mutation', ['same_sign', 'duplicate_team', 'specifier_sign', 'wrong_selected_sign', 'other_line'])
def test_signed_handicap_pair_cannot_conflict_with_named_selection_or_fixture(mutation):
    d, row = case('spreads')
    if mutation == 'same_sign':row['outcomes'][1]['name'] = 'Away (-0.5)'
    elif mutation == 'duplicate_team':row['outcomes'][1]['name'] = 'Home (0.5)'
    elif mutation == 'specifier_sign':row['specifiers'] = 'hcp=0.5|periodnr=1'
    elif mutation == 'wrong_selected_sign':row['outcomes'][0]['name'] = 'Home (0.5)'
    else:row['outcomes'][1]['name'] = 'Away (1.5)'
    synchronize_raw(row)
    assert evaluate_closing_price(d, [row], as_of=CUTOFF)['status'] == 'missing_closing_price'


def test_reversed_schedule_does_not_flip_stake_handicap_sign():
    d, row = case('spreads')
    d['payload'].update(home='Away', away='Home', side='away')
    assert evaluate_closing_price(d, [row], as_of=CUTOFF)['status'] == 'comparable'


def test_full_game_does_not_accept_a_scoped_selector_and_raw_mismatch_is_not_repaired():
    d, row = decision(), snapshot()
    row['specifiers'] += '|periodnr=1'
    synchronize_raw(row)
    assert evaluate_closing_price(d, [row], as_of=CUTOFF)['status'] == 'missing_closing_price'
    d, row = case()
    row['raw_contract']['specifiers'] = 'total=1.5'
    assert evaluate_closing_price(d, [row], as_of=CUTOFF)['rejection_counts'] == {'raw_contract_mismatch': 1}


def test_exact_fixture_start_stays_mandatory():
    d, row = case()
    row['raw_fixture']['startTime'] += 30*60*1000
    assert evaluate_closing_price(d, [row], as_of=CUTOFF)['rejection_counts'] == {'fixture_evidence_start_mismatch': 1}


@pytest.mark.parametrize('league,prefix', [('NHL', 'period_'), ('NFL', 'quarter_')])
def test_hash_archived_real_scoped_offers_replay_without_raw_row_changes(league, prefix):
    folder = Path(__file__).parent / 'fixtures/stake_handicap_specifiers'
    manifest = json.loads((folder/'manifest.json').read_text())[league]
    raw = gzip.decompress((folder/manifest['body_file']).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == manifest['source']['sha256']
    rows = snapshots_from_response(raw, manifest['source'], league=league)
    game = manifest['game']; names = named_scopes(league, game['home'], game['away'])
    seen = set()
    for row in rows:
        if row.get('market') not in names:continue
        scope, kind = names[row['market']]
        if not scope.startswith(prefix) or kind not in {'moneyline', 'three_way', 'spreads', 'totals', 'odd_even', 'both_score', 'double_chance'}:continue
        for outcome in row['outcomes']:
            if not outcome['active']:continue
            name = outcome['name'];line = None
            if kind == 'spreads':
                match = re.fullmatch(r'(.+) \(([+-]?\d+(?:\.\d+)?)\)', name)
                side = 'home' if match[1] == game['home'] else 'away';line = float(match[2])
            elif kind == 'totals':
                side, value = name.lower().split();line=float(value)
            else:
                sides = {'moneyline': {game['home']: 'home', game['away']: 'away'},
                         'three_way': {game['home']: 'home', game['away']: 'away', 'Draw': 'draw'},
                         'odd_even': {'Odd': 'odd', 'Even': 'even'}, 'both_score': {'Yes': 'yes', 'No': 'no'},
                         'double_chance': {game['home']+' or Draw': 'home_draw', 'Draw or '+game['home']: 'home_draw',
                          game['away']+' or Draw': 'away_draw', 'Draw or '+game['away']: 'away_draw', game['home']+' or '+game['away']: 'home_away'}}
                side=sides[kind][name]
            tip = {**game, 'market': scope+':'+kind, 'model_scope': scope, 'side': side, 'line': line,
                   'stake_evidence': {'market_name': row['market'], 'outcome_name': name}}
            before = deepcopy(row)
            assert _quote(row, tip, kind, name, outcome['id']) == Decimal(str(outcome['odds']))
            assert row == before
            seen.add((scope, kind))
    assert {'spreads', 'totals', 'moneyline', 'three_way'} <= {kind for _,kind in seen}
    assert len({scope for scope,_ in seen}) >= (3 if league == 'NHL' else 4)
