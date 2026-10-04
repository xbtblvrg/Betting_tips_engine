import copy
import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

from betting_engine.closing_prices import _contract
from betting_engine.contracts import allocation_family, contract_value, equivalent_contract_ids, settlement_scores
from betting_engine.market_anchor import contract_anchors
from betting_engine.markets import StakeSportsbook
from betting_engine.period_models import PeriodForecast
from betting_engine.presentation import stake_market
from test_markets import stake_game, stake_payload


def parsed(league='NBA', validated=('three_way', 'double_chance'), payload=None):
    game = stake_game(league)
    payload = payload or stake_payload('Will There Be Overtime', [('Yes', 8.), ('No', 1.1)])
    adapter = StakeSportsbook(segment_markets={(league, 'regulation'): validated})
    return adapter._markets_for_fixture(payload, {'id': 'f', 'slug': 'f'}, game, league, 'now')


def test_parser_preserves_stake_offer_and_canonical_validated_score_contract():
    yes, no = parsed()
    assert (yes.kind, yes.side, yes.selection) == ('regulation:three_way', 'draw', 'Yes')
    assert (no.kind, no.side, no.selection) == ('regulation:double_chance', 'home_away', 'No')
    for market in (yes, no):
        assert market.line is None
        assert market.metadata['model_scope'] == 'regulation'
        assert market.metadata['score_contract_alias']['market'] == market.kind
        assert market.description == 'Will There Be Overtime'
        assert market.metadata['stake_market_groups'] == [{'id': None, 'name': 'main'}]


@pytest.mark.parametrize('league,validated', [('NFL', ('three_way', 'double_chance')),
    ('NHL', ('three_way', 'double_chance')), ('WNBA', ('both_score',)),
    ('NBA', ('three_way',)), ('NBA', ('double_chance',)), ('NBA', ())])
def test_no_rule_or_missing_family_approval_remains_unsupported(league, validated):
    assert parsed(league, validated) == []


@pytest.mark.parametrize('change', ['duplicate', 'missing', 'extra', 'suspended', 'bad_odds', 'wrong_scope', 'line'])
def test_binary_offer_fails_closed(change):
    payload = stake_payload('Will There Be Overtime', [('Yes', 8.), ('No', 1.1)])
    market = payload['groups'][0]['markets'][0][0]
    if change == 'duplicate': market['outcomes'][1]['name'] = 'Yes'
    if change == 'missing': market['outcomes'].pop()
    if change == 'extra': market['outcomes'].append(copy.deepcopy(market['outcomes'][0]))
    if change == 'suspended': market['outcomes'][1]['active'] = False
    if change == 'bad_odds': market['outcomes'][1]['odds'] = float('nan')
    if change == 'wrong_scope': market['specifiers'] = 'period=1'
    if change == 'line': market['specifiers'] = 'total=200.5'
    assert parsed(payload=payload) == []


def test_duplicate_feed_groups_do_not_duplicate_offers_or_break_anchor():
    payload = stake_payload('Will There Be Overtime', [('Yes', 8.), ('No', 1.1)])
    payload['groups'].append(copy.deepcopy(payload['groups'][0]))
    assert len(parsed(payload=payload)) == 2


def test_anchor_is_exact_binary_condition_with_no_push_mass():
    markets = parsed()
    quotes = {m.id: SimpleNamespace(effective_cost=1/m.metadata['stake_odds'], fetched_at='now') for m in markets}
    anchors = contract_anchors(markets, quotes)
    assert len(anchors) == 2
    assert anchors[markets[0].id]['market_probability'] == pytest.approx((1/8)/(1/8+1/1.1))
    assert sum(a['market_probability'] for a in anchors.values()) == pytest.approx(1)
    assert not any(a['conditional_push_free'] for a in anchors.values())
    assert contract_anchors(markets[:1], quotes) == {}
    markets[1].metadata['stake_contract_identity'] = 'different-condition'
    assert contract_anchors(markets, quotes) == {}


def test_forecast_payoff_and_exposure_are_existing_exact_contracts():
    h, a = np.meshgrid(np.arange(7), np.arange(7))
    h, a = h.ravel(), a.ravel()
    w = np.full(len(h), 1/len(h))
    f = PeriodForecast('NBA:x', 'NBA', 'regulation', 3., 3., (h, a, w), {}, 'v', [])
    yes, no = parsed()
    assert f.market_probability(yes.kind, yes.side) == pytest.approx((1/7, 0))
    assert f.market_probability(no.kind, no.side) == pytest.approx((6/7, 0))
    assert np.all(contract_value(yes.kind, yes.side, None, h, a) == -contract_value(no.kind, no.side, None, h, a))
    with pytest.raises(ValueError, match='scopes differ'):
        PeriodForecast('g', 'NBA', 'quarter_1', 3, 3, (h,a,w), {}, 'v', []).market_probability(yes.kind, yes.side)
    for market in (yes, no):
        key=f'{market.game_id}:{market.kind}:{market.side}:None:regulation'
        canonical_id=hashlib.sha256(key.encode()).hexdigest()[:20]
        tip={'id':canonical_id, 'game_id':market.game_id, 'market':market.kind, 'side':market.side, 'line':None, 'league':'NBA'}
        assert allocation_family(tip) == market.kind
        assert canonical_id in equivalent_contract_ids(tip)


def test_settlement_uses_completed_regulation_not_final_or_ot_participation():
    game=stake_game('NBA');game.home_score=111;game.away_score=105
    tip={'market':'regulation:three_way'}
    with pytest.raises(ValueError, match='completed segment'):
        settlement_scores(tip,game)
    game.context['segment_scores']={'regulation':{'home':100,'away':100,'complete':True,
        'source':'official', 'fetched_at':'now', 'source_sha256':'abc'}}
    h,a,e=settlement_scores(tip,game)
    assert (h,a)==(100,100) and e['scope']=='regulation'
    assert contract_value(tip['market'],'draw',None,h,a)==1
    game.context['segment_scores']['regulation']['complete']=False
    with pytest.raises(ValueError): settlement_scores(tip,game)


def test_exact_closing_identity_and_user_facing_rules():
    for market in parsed():
        tip={'league':'NBA', 'home':'Home', 'away':'Away', 'market':market.kind, 'side':market.side,
             'line':None, 'model_scope':'regulation', 'selection':market.selection,
             'reference_contract_description':market.description,
             'stake_evidence':{'market_name':market.description,'outcome_name':market.selection}}
        assert _contract(tip)==(market.description,market.selection,'regulation',market.kind.split(':')[1])
        assert 'Whether overtime is actually played' in stake_market(tip)['scope_note']
        tip['side']='home'
        with pytest.raises(ValueError):_contract(tip)


@pytest.mark.parametrize('kind,side', [('regulation:three_way','draw'), ('regulation:double_chance','home_away')])
def test_existing_guard_rejects_unapproved_alias_family_and_failed_promotion(kind, side):
    from betting_engine.decisions import candidate
    from betting_engine.governance import EvidenceGuard, KnowledgeAgent
    from test_governance import guard_case
    _,game,market,quote,forecast,report,settings=guard_case()
    market.kind=kind;market.side=side;market.selection='Yes' if side=='draw' else 'No'
    market.description='Will There Be Overtime'
    forecast.scope='regulation'
    report.update(scope='regulation',validated_markets=[],promotion_gate={'promote':True})
    tip=candidate(game,market,quote,forecast,settings)
    result=EvidenceGuard(KnowledgeAgent()).check(tip,game,market,quote,forecast,report,settings)
    assert not result['passed'] and 'segment_market_not_validated' in result['errors']
    report.update(validated_markets=[kind.split(':')[1]],promotion_gate={'promote':False})
    result=EvidenceGuard(KnowledgeAgent()).check(tip,game,market,quote,forecast,report,settings)
    assert not result['passed'] and 'segment_market_not_validated' in result['errors']


def nfl_game(phase='REG'):
    from betting_engine.overtime_contracts import NFL_PHASE_SOURCE
    game=stake_game('NFL');game.source=NFL_PHASE_SOURCE
    game.game_type='regular' if phase=='REG' else 'playoff'
    game.context['nfl_phase_evidence']={'game_id':game.id,'raw_game_type':phase,'source':game.source,
        'source_sha256':'a'*64,'fetched_at':'now'}
    return game


@pytest.mark.parametrize('phase', ['REG','WC','DIV','CON','SB'])
def test_nfl_source_backed_normal_games_enable_binary_alias(phase):
    game=nfl_game(phase)
    payload=stake_payload('Will There Be Overtime', [('Yes',8.),('No',1.1)])
    markets=StakeSportsbook(segment_markets={('NFL','regulation'):['three_way','double_chance']})._markets_for_fixture(payload,{'id':'f','slug':'f'},game,'NFL','now')
    assert len(markets)==2
    assert markets[0].metadata['score_contract_alias']['phase_evidence']['raw_game_type']==phase
    assert markets[0].metadata['score_contract_alias']['sports_rule_section']=='16-1-1'


@pytest.mark.parametrize('change', ['PRE','unknown','missing','bad_hash','wrong_id','wrong_source','conflict'])
def test_nfl_unknown_or_exhibition_phase_fails_closed(change):
    game=nfl_game()
    evidence=game.context['nfl_phase_evidence']
    if change in ('PRE','unknown'):evidence['raw_game_type']=change
    if change=='missing':game.context.clear()
    if change=='bad_hash':evidence['source_sha256']='missing'
    if change=='wrong_id':evidence['game_id']='NFL:other'
    if change=='wrong_source':game.source='https://example.com'
    if change=='conflict':game.game_type='preseason'
    payload=stake_payload('Will There Be Overtime', [('Yes',8.),('No',1.1)])
    assert not StakeSportsbook(segment_markets={('NFL','regulation'):['three_way','double_chance']})._markets_for_fixture(payload,{'id':'f','slug':'f'},game,'NFL','now')


def test_nfl_overtime_settlement_requires_final_and_completed_regulation():
    game=nfl_game();game.home_score=27;game.away_score=24
    game.context['segment_scores']={'regulation':{'home':24,'away':24,'complete':True,
        'source':'official','fetched_at':'now','source_sha256':'b'*64}}
    tip={'market':'regulation:three_way','reference_contract_description':'Will There Be Overtime'}
    with pytest.raises(ValueError,match='Completed normal NFL'):settlement_scores(tip,game)
    game.state='final'
    assert settlement_scores(tip,game)[:2]==(24,24)
    game.context['segment_scores']['regulation']['complete']=False
    with pytest.raises(ValueError,match='completed segment'):settlement_scores(tip,game)


def test_nfl_provider_attaches_raw_phase_and_source_digest():
    from betting_engine.providers import SportsData
    row={'season':'2030','game_type':'REG','home_team':'DAL','away_team':'BAL',
        'home_score':'','away_score':'','gameday':'2030-01-01','gametime':'13:00',
        'game_id':'2030_01_BAL_DAL','location':'Home'}
    fetcher=SimpleNamespace(csv=lambda *args:([row],{'fetched_at':'now','sha256':'a'*64}))
    games,_=SportsData(fetcher=fetcher).nfl()
    assert games[0].context['nfl_phase_evidence']['raw_game_type']=='REG'
    assert games[0].context['nfl_phase_evidence']['source_sha256']=='a'*64


@pytest.mark.parametrize('league', ['NBA','NFL'])
@pytest.mark.parametrize('learned_weight', [None,.4])
def test_approved_alias_traverses_actual_guard_with_exact_identity_and_anchor(league, tmp_path, learned_weight):
    from datetime import timedelta
    from betting_engine.domain import utcnow
    from betting_engine.decisions import candidate, DEFAULT_SETTINGS
    from betting_engine.governance import EvidenceGuard, KnowledgeAgent
    from betting_engine.models import feature_names
    now=utcnow();game=nfl_game() if league=='NFL' else stake_game('NBA')
    game.start=(now+timedelta(hours=2)).isoformat();game.fetched_at=now.isoformat()
    if league=='NBA':game.source='https://sports.test/game'
    else:
        from zoneinfo import ZoneInfo
        local=now.astimezone(ZoneInfo('America/New_York'))+timedelta(hours=2)
        game.start=local.replace(second=0,microsecond=0).astimezone(now.tzinfo).isoformat()
        raw=('game_id,game_type,home_team,away_team,season,gameday,gametime\n'
             f'x,REG,DAL,BAL,{game.season},{local:%Y-%m-%d},{local:%H:%M}\n').encode()
        digest=hashlib.sha256(raw).hexdigest()
        (tmp_path/'raw').mkdir();(tmp_path/'raw'/(digest+'.data')).write_bytes(raw)
        game.context['nfl_phase_evidence']['source_sha256']=digest

    payload=stake_payload('Will There Be Overtime',[('Yes',2.),('No',2.)])
    markets=StakeSportsbook(segment_markets={(league,'regulation'):['three_way','double_chance']})._markets_for_fixture(payload,{'id':'f','slug':'f'},game,league,now.isoformat())
    quotes={m.id:SimpleNamespace(fetched_at=now.isoformat(),effective_cost=.5,price=.5,fee_per_share=0,minimum_notional=0) for m in markets}
    anchors=contract_anchors(markets,quotes)
    features={**dict.fromkeys(feature_names(league),0.),'home_rest':1.,'away_rest':1.}
    forecast=PeriodForecast(game.id,league,'regulation',20.,20.,
        (np.array([20.,21.]),np.array([20.,20.]),np.array([.6,.4])),features,'synthetic-fixture-v1',[])
    # Explicit synthetic approved test model; no production report is modified.
    report={'league':league,'scope':'regulation','version':forecast.model_version,'odds_used':False,
        'features':list(feature_names(league)),'trained_at':now.isoformat(),'code_sha256':'fixture-code',
        'training_data_fingerprint':'fixture-data','validated_markets':['three_way','double_chance'],
        'promotion_gate':{'promote':True}}
    settings={**DEFAULT_SETTINGS,'auto_track_recommendations':True,'market_anchor_mode':'live','market_model_weight':.15}
    if learned_weight is not None:
        from betting_engine.anchor_learning import segment,RUNTIME_KEY
        key=segment(league,'regulation:three_way',semantics='binary_overtime_alias_v1')
        settings[RUNTIME_KEY]={'snapshot_sha256':'approved-test-snapshot','weights':{key+'|'+forecast.model_version:{
            'segment':key,'model_weight':learned_weight,'proof_sha256':'approved-test-proof'}}}
    for market in markets:
        quote=quotes[market.id]
        tip=candidate(game,market,quote,forecast,settings,anchors[market.id])
        assert tip['market_anchor']['model_weight']==(learned_weight or .15)
        tip['stake_evidence']={'market_name':market.description,'outcome_name':market.selection,
            'fixture_slug':'f','market_groups':market.metadata['stake_market_groups'],
            'league_path':market.metadata['league_path'],
            'score_contract_alias':market.metadata['score_contract_alias']}
        result=EvidenceGuard(KnowledgeAgent(), data_dir=tmp_path).check(tip,game,market,quote,forecast,report,settings)
        assert result['passed'],result['errors']
        assert _contract(tip)[2]=='regulation'
        tip['stake_evidence']['score_contract_alias']={}
        result=EvidenceGuard(KnowledgeAgent()).check(tip,game,market,quote,forecast,report,settings)
        assert not result['passed'] and 'overtime_alias_evidence_mismatch' in result['errors']


def test_overtime_and_canonical_offers_anchor_separately_then_best_quote_has_single_identity():
    from betting_engine.overtime_contracts import deduplicate_alias_candidates
    from betting_engine.decisions import candidate, DEFAULT_SETTINGS
    from betting_engine.domain import utcnow
    from datetime import timedelta
    game=stake_game('NBA');game.start=(utcnow()+timedelta(hours=2)).isoformat()
    overtime=stake_payload('Will There Be Overtime',[('Yes',3.),('No',1.5)])
    normal=stake_payload('1x2',[(game.home,3.),('Draw',4.),(game.away,3.)])
    normal['groups'][0]['markets'][0][0]['id']='normal'
    overtime['groups'].extend(normal['groups'])
    markets=StakeSportsbook(segment_markets={('NBA','regulation'):['three_way','double_chance']})._markets_for_fixture(overtime,{'id':'f','slug':'f'},game,'NBA',utcnow().isoformat())
    assert len(markets)==5
    quotes={m.id:SimpleNamespace(fetched_at=m.fetched_at,effective_cost=1/m.metadata['stake_odds'],price=1/m.metadata['stake_odds'],fee_per_share=0,minimum_notional=0) for m in markets}
    anchors=contract_anchors(markets,quotes)
    assert len(anchors)==5
    draw_markets=[m for m in markets if m.side=='draw'];assert len(draw_markets)==2
    forecast=PeriodForecast(game.id,'NBA','regulation',20,20,(np.array([20,21]),np.array([20,20]),np.array([.4,.6])),{'home_rest':1.,'away_rest':1.},'v',[])
    candidates=[candidate(game,m,quotes[m.id],forecast,DEFAULT_SETTINGS,anchors[m.id]) for m in draw_markets]
    assert candidates[0]['id']==candidates[1]['id']
    frozen=copy.deepcopy(candidates)
    for order in (candidates,list(reversed(candidates))):
        selected=deduplicate_alias_candidates(order)
        assert len(selected)==1 and selected[0]['reference_odds']==4.
        assert selected[0]['selection']=='Draw'
    assert candidates==frozen


def test_overtime_closing_capture_uses_original_yes_no_quote():
    from test_closing_prices import decision,snapshot,synchronize_raw,evaluate
    d,row=decision(),snapshot(2.)
    d['payload'].update(market='regulation:three_way',side='draw',line=None,model_scope='regulation')
    d['payload']['stake_evidence'].update(market_name='Will There Be Overtime',outcome_name='Yes')
    row.update(market='Will There Be Overtime',scope='regulation',specifiers='')
    row['outcomes'][0]['name']='Yes'
    assert evaluate([synchronize_raw(row)],d)['status']=='comparable'
    row['outcomes'][0]['name']='Draw'
    assert evaluate([synchronize_raw(row)],d)['status']=='missing_closing_price'


def test_observed_feed_title_with_lowercase_be_is_supported():
    assert len(parsed(payload=stake_payload('Will There be Overtime',[('Yes',8.),('No',1.1)])))==2
