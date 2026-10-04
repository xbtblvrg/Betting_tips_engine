"""Archived real offers; synthetic forecasts/results, never historical P&L claims."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest

from betting_engine.closing_prices import _contract, _quote, snapshots_from_response
from betting_engine.contracts import contract_value, settlement_scores
from betting_engine.decisions import candidate
from betting_engine.governance import EvidenceGuard, KnowledgeAgent
from betting_engine.models import feature_names
from betting_engine.prospective_shadow import ShadowEvidenceStore, _fair_probability
from test_governance import guard_case

FIXTURES = Path(__file__).parent/'fixtures/stake_full_odd_even'
MANIFEST = json.loads((FIXTURES/'manifest.json').read_text())


def real_case(tmp_path, monkeypatch, league):
    item = MANIFEST[league]; source = item['source']; now = datetime.fromisoformat(source['fetched_at'])
    body = gzip.decompress((FIXTURES/item['file']).read_bytes())
    assert hashlib.sha256(body).hexdigest() == source['sha256']
    archive = tmp_path/source['archive']; archive.parent.mkdir(exist_ok=True)
    archive.write_bytes(gzip.compress(body))
    payload = json.loads(body); fixture = payload['fixture']
    rows = snapshots_from_response(body, source, league=league)
    row = next(r for r in rows if r.get('market_id') == item['market_id'])
    monkeypatch.setattr('betting_engine.governance.utcnow', lambda: now)
    monkeypatch.setattr('betting_engine.decisions.utcnow', lambda: now)
    _, game, market, quote, forecast, report, settings = guard_case()
    game.league = league; game.home, game.away = item['teams']; game.fetched_at = now.isoformat()
    game.start = datetime.fromtimestamp(fixture['startTime']/1000, now.tzinfo).isoformat()
    market.kind = 'odd_even'; market.side = 'odd'; market.line = None
    outcome = row['outcomes'][0]
    market.id = f"stake:{fixture['id']}:{row['market_id']}:{outcome['id']}"
    market.description = row['market']; market.selection = outcome['name']
    market.metadata.update(stake_odds=outcome['odds'], api_source=source)
    quote.fetched_at = now.isoformat(); quote.price = quote.effective_cost = 1/outcome['odds']
    report.update(league=league, features=list(feature_names(league)), trained_at=now.isoformat())
    forecast.features = {**dict.fromkeys(feature_names(league), 0.), 'home_rest':1.,'away_rest':1.}
    tip = candidate(game,market,quote,forecast,settings)
    tip['stake_evidence'] = {'fixture_slug':fixture['slug'],'market_name':row['market'],
        'outcome_name':outcome['name'],'decimal_odds':outcome['odds'],'fetched_at':source['fetched_at'],'api_source':source}
    guard = EvidenceGuard(KnowledgeAgent())
    if league == 'MLB':
        # Synthetic model inputs are outside this archived-offer transport test.
        # Production pitcher-source validation is separately covered by
        # test_decision_readiness; no live/source verification claim is made here.
        from betting_engine.decision_readiness import READINESS_VERSION
        monkeypatch.setattr(guard.readiness, 'check', lambda *a, **k: {
            'version':READINESS_VERSION,'passed':True,'state':'ready','errors':[],
            'input_sha256':hashlib.sha256(b'synthetic MLB inputs').hexdigest()})
    checked = guard.check(tip,game,market,quote,forecast,report,settings)
    assert checked['passed'], checked['errors']
    return tip, row, now


@pytest.mark.parametrize('league', MANIFEST)
def test_real_full_odd_even_captures_once_and_preserves_original(tmp_path, monkeypatch, league):
    tip, row, now = real_case(tmp_path, monkeypatch, league)
    original = deepcopy(tip)
    store = ShadowEvidenceStore.create(tmp_path/'shadow.sqlite3')
    store.register_trial('trial', tip['model_version'], {'synthetic_forecast':True}, as_of=now.isoformat())
    result = store.capture('trial',[tip],archive_root=tmp_path,as_of=now.isoformat())
    assert result['accepted'] == 1, result
    assert result['rejected'] == []
    assert store.capture('trial',[tip],archive_root=tmp_path,as_of=now.isoformat())['duplicates'] == 1
    assert tip == original
    assert float(_quote(row,tip,'odd_even','Odd',tip['reference_market_id'].split(':')[-1])) == pytest.approx(tip['reference_odds'])
    # An existing eligible closing observation now also has a complete devig distribution.
    digest = hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    comparison = {'closing_price':{'snapshot_sha256':digest}}
    fair = _fair_probability(tip,[row],comparison)
    expected = (1/row['outcomes'][0]['odds']) / sum(1/o['odds'] for o in row['outcomes'])
    assert fair == pytest.approx(expected)
    # Full-game settlement must use the final score, not the regulation score.
    final = SimpleNamespace(league=league, context={},home_score=4,away_score=3,source='synthetic-result',fetched_at=now.isoformat())
    home, away, evidence = settlement_scores(tip,final)
    assert evidence['scope'] == 'full_game'
    assert contract_value('odd_even','odd',None,home,away) == 1
    assert contract_value('odd_even','even',None,home,away) == -1


@pytest.mark.parametrize('league', MANIFEST)
@pytest.mark.parametrize('field,value', [('model_scope','regulation'),('market','regulation:odd_even'),('line',1.5),('side','yes')])
def test_full_odd_even_identity_cannot_cross_scope_or_outcome(tmp_path,monkeypatch,league,field,value):
    tip,_,_ = real_case(tmp_path,monkeypatch,league)
    tip[field] = value
    with pytest.raises(ValueError): _contract(tip)


@pytest.mark.parametrize('league,kind,title,side', [
    ('WNBA','winner_total','Winner & Total (Incl. Overtime)','home_over'),
    ('MLB','exact_total','Total (Over-Exact-Under) (Incl. Extra Innings)','exact'),
])
def test_real_composite_close_has_complete_exclusive_fair_probability(league,kind,title,side):
    item=MANIFEST[league];body=gzip.decompress((FIXTURES/item['file']).read_bytes())
    rows=snapshots_from_response(body,item['source'],league=league)
    row=next(r for r in rows if r.get('market')==title)
    from betting_engine.composite_contracts import identity
    home,away=item['teams'];contract=identity(league,kind,home,away,row['raw_contract'])
    outcome=contract['outcomes'][side]
    tip={'league':league,'home':home,'away':away,'market':kind,'model_scope':'full_game',
         'side':side,'line':contract['line'],
         'reference_market_id':f"stake:{row['fixture_id']}:{row['market_id']}:{outcome['id']}",
         'stake_evidence':{'market_name':title,'outcome_name':outcome['name']}}
    def fair(r):
        digest=hashlib.sha256(json.dumps(r,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return _fair_probability(tip,[r],{'closing_price':{'snapshot_sha256':digest}})
    assert fair(row)==pytest.approx((1/outcome['odds'])/sum(1/o['odds'] for o in row['outcomes']))
    missing=deepcopy(row);missing['outcomes'].pop();assert fair(missing) is None
    suspended=deepcopy(row);suspended['outcomes'][0]['active']=False;assert fair(suspended) is None


def test_overlapping_double_chance_is_not_normalized_as_exclusive_market():
    row={'outcomes':[{'id':str(i),'name':name,'active':True,'odds':1.5}
        for i,name in enumerate(['Home or Draw','Away or Draw','Home or Away'])]}
    tip={'league':'NHL','home':'Home','away':'Away','market':'double_chance','model_scope':'full_game',
         'side':'home_draw','line':None,'reference_market_id':'stake:f:m:0',
         'stake_evidence':{'market_name':'Double Chance (Incl. Overtime and Penalties)','outcome_name':'Home or Draw'}}
    digest=hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert _fair_probability(tip,[row],{'closing_price':{'snapshot_sha256':digest}}) is None
