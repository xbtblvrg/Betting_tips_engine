"""Unchanged Stake response and independent ESPN identity reproduce the omission."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
import gzip
import hashlib
import json
from pathlib import Path
import pytest
from betting_engine.domain import Game,parse_time
from betting_engine.markets import StakeSportsbook,team_matches
from betting_engine.market_anchor import contract_anchors
from betting_engine.closing_prices import snapshots_from_response,evaluate_closing_price
from betting_engine.team_identity import exact_team_name_match

ROOT=Path(__file__).parent/'fixtures/clippers_alias'


def observed():
    body=gzip.decompress((ROOT/'response.json.gz').read_bytes())
    source=json.loads((ROOT/'source.json').read_text())
    assert hashlib.sha256(body).hexdigest()==source['sha256']
    game=Game(**json.loads((ROOT/'game.json').read_text()))
    return body,source,game


def parsed():
    body,source,game=observed();payload=json.loads(body)
    book=StakeSportsbook()
    markets=book._markets_for_fixture(payload,payload['fixture'],game,'NBA',source['fetched_at'])
    return markets,book.quotes(markets)[0],game


def test_real_complete_response_recovers_ten_outcomes_and_all_anchors():
    markets,quotes,g=parsed()
    assert (g.id,g.home_id,g.home,g.away_id,g.away)==('NBA:401918010','12','LA Clippers','9','Golden State Warriors')
    assert g.source.startswith('https://site.api.espn.com/')
    assert Counter(m.kind for m in markets)=={'moneyline':2,'spreads':6,'totals':6,'home_total':2,'away_total':2}
    assert len(contract_anchors(markets,quotes))==18
    assert {m.side for m in markets if m.kind=='moneyline'}=={'home','away'}
    for magnitude in (2.5,4.5,6.5):
        assert {(m.side,m.line) for m in markets if m.kind=='spreads' and abs(m.line)==magnitude}=={('home',magnitude),('away',-magnitude)}
    assert all(m.metadata['stake_outcome_name'].startswith('Los Angeles Clippers') for m in markets if m.kind in ('moneyline','spreads') and m.side=='home')
    assert {m.description for m in markets if m.kind=='home_total'}=={'Los Angeles Clippers Total (Incl. Overtime)'}


@pytest.mark.parametrize('left,right',[('LA Clippers','Los Angeles Clippers'),('Los Angeles Clippers','LA Clippers')])
def test_explicit_alias_is_bidirectional(left,right):
    assert exact_team_name_match(left,right)
    assert team_matches(left,right)


@pytest.mark.parametrize('other',['Los Angeles Lakers','LA Lakers','Los Angeles','LA','Clippers Women','LA Clippers Academy'])
def test_alias_never_guesses_city_or_other_team(other):
    assert not exact_team_name_match('LA Clippers',other)
    assert not team_matches(other,'LA Clippers','LAC')


def test_conflicting_lakers_outcome_cannot_complete_clippers_winner_or_handicap():
    body,source,g=observed();payload=json.loads(body)
    for group in payload['groups']:
        for bucket in group['markets']:
            for raw in bucket:
                for outcome in raw.get('outcomes',[]):
                    outcome['name']=outcome['name'].replace('Los Angeles Clippers','Los Angeles Lakers')
    markets=StakeSportsbook()._markets_for_fixture(payload,payload['fixture'],g,'NBA',source['fetched_at'])
    assert not any(m.kind in ('moneyline','spreads') for m in markets)


@pytest.mark.parametrize('kind',['moneyline','spreads','home_total'])
def test_closing_adapter_preserves_exact_observed_alias_contract(kind):
    body,source,g=observed();markets,quotes,_=parsed()
    m=next(m for m in markets if m.kind==kind and (kind=='home_total' or m.side=='home'))
    # Only observation time is controlled to exercise close-window mechanics;
    # this does not claim the archived response was a real closing observation.
    start=parse_time(g.start);source['fetched_at']=(start-timedelta(minutes=1)).isoformat()
    rows=snapshots_from_response(body,source,league='NBA')
    t={'id':'alias-test','game_id':g.id,'league':g.league,'home':g.home,'away':g.away,'start':g.start,
       'market':m.kind,'side':m.side,'line':m.line,'model_scope':'full_game','stake':1,
       'reference_platform':'Stake.com','reference_market_id':m.id,'reference_odds':m.metadata['stake_odds'],
       'probability':.5,'model_version':'test','stake_evidence':{'fixture_slug':m.metadata['fixture_slug'],
           'market_name':m.description,'outcome_name':m.metadata['stake_outcome_name']}}
    decision={'payload':t,'tip_id':t['id'],'family':'contract:alias-test','issued_at':(start-timedelta(hours=1)).isoformat()}
    result=evaluate_closing_price(decision,rows,as_of=(start+timedelta(hours=1)).isoformat())
    assert result['status']=='comparable',result
    assert result['price_clv']==pytest.approx(0)
