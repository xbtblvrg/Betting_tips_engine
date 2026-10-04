"""Explicit NFL Winner reference choice; never replaces a fitted artifact.

Replay is reconstructed from later sports snapshots. Matching old aggregate
metrics does not recover the original input archive or establish profitability.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import heapq
import json
import math
from pathlib import Path

from .domain import parse_time

POLICY = 'nfl-reconstructed-elo-winner-v1'
FORMULA = 'clip(1/(1+10**(-elo_difference-0.125*(1-neutral))),0.005,0.995)'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def probability(features):
    d, neutral = features['elo_difference'], features['neutral']
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (d, neutral)) or neutral not in (0, 1):
        raise ValueError('Invalid reference features')
    exponent = max(-300, min(300, -d-.125*(1-neutral)))
    return min(.995, max(.005, 1/(1+10**exponent)))


def replay(games, test_rows, cutoff):
    """Independent Elo-only replay of the archived sports rows, no odds inputs."""
    teams, pending, predictions, ids = {}, [], {}, set()
    def team(key):
        return teams.setdefault(key, {'elo': 1500., 'last': None, 'n': 0})
    def update(g):
        h, a = team(g['home_id']), team(g['away_id'])
        for t in (h, a):
            if t['last'] and (parse_time(g['start'])-parse_time(t['last'])).days > 60:
                t['elo'] = 1500+(t['elo']-1500)*.5
        p = 1/(1+10**((a['elo']-h['elo']-(0 if g['neutral'] else 50))/400))
        y = .5 if g['home_score'] == g['away_score'] else float(g['home_score'] > g['away_score'])
        h['elo'] += 32*(y-p); a['elo'] -= 32*(y-p)
        for t in (h, a):
            t['n'] += 1; t['last'] = g['start']
    end = parse_time(cutoff)
    for g in sorted(games, key=lambda g: (parse_time(g['start']), g['id'])):
        if g['id'] in ids:
            raise ValueError('Duplicate reference game')
        ids.add(g['id'])
        start = parse_time(g['start'])
        if start >= end:
            raise ValueError('Reference input after reconstruction cutoff')
        if g['neutral'] not in (True, False) or any(type(g[k]) not in (int, float) or not math.isfinite(g[k]) or g[k] < 0 for k in ('home_score', 'away_score')):
            raise ValueError('Invalid reference sports row')
        while pending and pending[0][0] <= start:
            update(heapq.heappop(pending)[3])
        h, a = team(g['home_id']), team(g['away_id'])
        def elo(t):
            rest = (start-parse_time(t['last'])).total_seconds()/86400 if t['last'] else 30.
            return 1500+(t['elo']-1500)*(.5 if rest > 60 else 1)
        if min(h['n'], a['n']) >= 5 and g['home_score'] != g['away_score']:
            predictions[g['id']] = {'game_id': g['id'], 'date': g['start'],
                'p': probability({'elo_difference': (elo(h)-elo(a))/400, 'neutral': int(g['neutral'])}),
                'result': int(g['home_score'] > g['away_score'])}
        available = max(start+timedelta(hours=8), start.replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1))
        heapq.heappush(pending, (available, start, g['id'], g))
    rows = []
    for expected in test_rows:
        row = predictions.get(expected['game_id'])
        if row is None or (row['date'], row['result']) != (expected['date'], expected['result']):
            raise ValueError('Reference test event/outcome differs from champion')
        rows.append(row)
    if not rows or len({r['game_id'] for r in rows}) != len(rows):
        raise ValueError('Invalid reference test cohort')
    return rows


def metrics(rows):
    return {'n': len(rows), 'brier': sum((r['p']-r['result'])**2 for r in rows)/len(rows),
        'log_loss': -sum(r['result']*math.log(r['p'])+(1-r['result'])*math.log1p(-r['p']) for r in rows)/len(rows)}


def validate_reference(evidence, champion):
    e = deepcopy(evidence)
    seal = e.pop('sha256', None)
    if seal != digest(e) or e.get('policy') != POLICY or e.get('formula') != FORMULA:
        raise ValueError('Reference evidence identity/digest mismatch')
    if champion.get('league') != 'NFL' or champion.get('scope', 'full_game') != 'full_game' or e['champion_report_sha256'] != digest(champion):
        raise ValueError('Reference champion changed')
    if e.get('original_input_archive_recovered') is not False:
        raise ValueError('Unsupported reference archival claim')
    rows = replay(e['games'], champion['test_predictions'], champion['trained_at'])
    if rows != e['test_predictions']:
        raise ValueError('Reference frozen predictions differ from independent replay')
    actual = metrics(rows)
    for key in ('n', 'brier', 'log_loss'):
        if abs(actual[key]-champion['elo_baseline'][key]) > 1e-10:
            raise ValueError('Reconstructed reference does not reproduce archived comparator')
    if champion['test']['brier'] <= actual['brier']:
        raise ValueError('Reference choice lacks observed comparator improvement')
    return actual


def reference_report(evidence, champion):
    actual = validate_reference(evidence, champion)
    provenance = {k: deepcopy(evidence[k]) for k in ('policy', 'formula', 'sha256', 'champion_report_sha256',
        'snapshot_sha256', 'original_input_archive_recovered', 'original_training_fingerprint',
        'reconstructed_training_fingerprint', 'limitation', 'constructed_at')}
    return {'league': 'NFL', 'scope': 'full_game', 'version': POLICY+'-'+evidence['sha256'][:16],
        'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'training_data_fingerprint': evidence['sha256'], 'trained_at': evidence['constructed_at'],
        'features': deepcopy(champion['features']), 'odds_used': False,
        'model_family': 'Fixed odds-independent Elo reference, explicit Winner-only policy choice',
        'test': actual, 'elo_baseline': deepcopy(actual), 'test_predictions': deepcopy(evidence['test_predictions']),
        'winner_reference': provenance, 'profit_backtest': None,
        'evaluation_status': 'reconstructed retrospective comparator; prospective validation pending',
        'promotion_gate': {'promote': False, 'reason': 'Runtime reference choice, not fitted-model promotion'},
        'deployment_policy': POLICY}


class ReferenceForecast:
    """The selected reference cannot accidentally price score/scoped contracts."""
    def __init__(self, base):
        self.__dict__.update(base.__dict__)
    def public(self):
        return {k: v for k, v in self.__dict__.items() if k != 'residuals'}
    def market_probability(self, kind, side, line=None):
        if kind != 'moneyline' or side not in ('home', 'away') or line is not None:
            raise ValueError('Winner reference cannot price another contract family')
        return (self.home_probability if side == 'home' else self.away_probability), self.draw_probability


def choose_reference(forecast, champion, evidence, market):
    if market != 'moneyline' or forecast.league != 'NFL':
        return forecast, champion
    report = reference_report(evidence, champion)
    if forecast.model_version != champion['version']:
        raise ValueError('Reference feature provenance differs from champion')
    p = probability(forecast.features)
    draw = forecast.draw_probability
    if not math.isfinite(draw) or not 0 <= draw < 1:
        raise ValueError('Invalid inherited tie probability')
    selected = ReferenceForecast(replace(forecast, home_probability=p*(1-draw),
        away_probability=(1-p)*(1-draw), model_version=report['version'],
        warnings=forecast.warnings+['Winner uses a reconstructed Elo reference; original training inputs were not recovered; prospective profitability is unproven.']))
    return selected, report


def load_reference(root, champion):
    policy_path = Path(root)/'config/winner_reference_policy.json'
    if not policy_path.exists():
        return None
    policy = json.loads(policy_path.read_text())
    if policy.get('active') is not True:
        return None
    if policy.get('version') != POLICY or policy.get('league') != 'NFL' or policy.get('market') != 'moneyline':
        raise ValueError('Unsupported Winner reference policy')
    path = Path(root)/'config/reference_evidence/NFL-elo-v1.json'
    evidence = json.loads(path.read_text())
    if policy.get('evidence_sha256') != evidence.get('sha256'):
        raise ValueError('Reference policy evidence binding differs')
    validate_reference(evidence, champion)
    return evidence


def verify_selected_forecast(forecast, report):
    """Guard-side probability reproduction from the current sports features."""
    ref = report.get('winner_reference')
    if not ref:
        return
    if (ref.get('policy') != POLICY or ref.get('formula') != FORMULA
            or report.get('version') != POLICY+'-'+ref.get('sha256','')[:16]
            or forecast.model_version != report['version']
            or report.get('league') != 'NFL' or report.get('scope') != 'full_game'
            or report.get('code_sha256') != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
        raise ValueError('Invalid selected reference provenance')
    p, draw = probability(forecast.features), forecast.draw_probability
    if not math.isfinite(draw) or not 0 <= draw < 1:
        raise ValueError('Invalid reference tie probability')
    for actual, expected in ((forecast.home_probability,p*(1-draw)),
                             (forecast.away_probability,(1-p)*(1-draw))):
        if not math.isfinite(actual) or abs(actual-expected)>1e-12:
            raise ValueError('Reference probability differs from independent formula')
