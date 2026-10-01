"""Synthetic official-stat interchange tests; no claimed real player outcomes."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from betting_engine.domain import utcnow
from betting_engine.player_prop_contracts import canonical_hash, SEMANTIC_FIELDS
from betting_engine.player_prop_settlement import settle_player_prop
from test_player_prop_contracts import offers


def case(operator='over', line='20.5', value=21, *, metric='points', league='WNBA'):
    contract = next(r for r in offers('wnba-standard')['contracts']
                    if r['market_id']=='4eef0420-ec41-47a7-8dfb-e99ef4285d5c' and r['operator']=='over')
    # Explicit synthetic changes test settlement semantics independently of
    # the real quote. They never appear as claimed observed Stake offers.
    contract.update(operator=operator, line=line, metric=metric, league=league)
    contract['contract_sha256'] = canonical_hash({k:contract.get(k) for k in SEMANTIC_FIELDS})
    contract['contract_id'] = contract['contract_sha256']
    now = utcnow(); when = (now-timedelta(minutes=1)).isoformat()
    ticket = {'id':'synthetic-ticket-1', 'contract':contract, 'stake':'2.25', 'odds':'1.84',
              'placed_at':(now-timedelta(hours=3)).isoformat()}
    rule_body = b'Synthetic reviewed rule fixture: participate, DNP void, equality pushes, official stat corrections.'
    rule_hash = hashlib.sha256(rule_body).hexdigest()
    rules = {k:contract[k] for k in ('league','metric','scope','includes_overtime','includes_shootout')}
    rules.update(participation_requirement='played', no_participation='void', integer_equality='push',
                 stat_corrections='apply_official', cancelled='void', abandoned='void',
                 source={'url':'https://stake.com/policies/sportsbook','sha256':rule_hash,'fetched_at':when})
    obs = {k:contract[k] for k in ('league','fixture_id','player_id','metric','scope','includes_overtime','includes_shootout')}
    obs.update(entity_type='player', official=True, event_status='final', scope_complete=True,
               available_at=when, revision=1, value=value, participation={'played':True,'started':True,'final':True})
    host = {'WNBA':'wnba.com','NBA':'nba.com','NHL':'nhl.com','NFL':'nfl.com','NCAAF':'ncaa.com'}[league]
    obs_body = json.dumps({'test_fixture':'synthetic normalized official-stat interchange','stat':value}).encode()
    obs_hash = hashlib.sha256(obs_body).hexdigest()
    obs['source']={'url':'https://'+host+'/synthetic-test-fixture','sha256':obs_hash,'fetched_at':when}
    return ticket, obs, rules, {rule_hash:rule_body, obs_hash:obs_body}, now


def settle(args, previous=None):
    ticket, obs, rules, bodies, now = args
    return settle_player_prop(ticket, obs, rules, evidence_bodies=bodies, as_of=now, previous=previous)


@pytest.mark.parametrize('op,line,value,outcome,payout,profit', [
    ('over','20.5',21,'win','4.14','1.89'), ('over','20.5',20,'loss','0','-2.25'),
    ('under','20.5',20,'win','4.14','1.89'), ('under','20.5',21,'loss','0','-2.25'),
    ('over','20',20,'push','2.25','0'), ('under','20',20,'push','2.25','0'),
    ('at_least','20',20,'win','4.14','1.89'), ('at_least','20',19,'loss','0','-2.25')])
def test_all_comparison_and_integer_push_branches(op,line,value,outcome,payout,profit):
    args=case(op,line,value); before=deepcopy(args[:3]); result=settle(args)
    assert result['state']=='settled' and result['result']==outcome
    assert result['payout']==payout and result['profit']==profit
    assert args[:3]==before


@pytest.mark.parametrize('metric,league,value', [('plus_minus','NHL',-3),('receiving_yards','NFL',-3),('rushing_yards','NCAAF',-3)])
def test_signed_metric_values_and_lines_are_preserved(metric,league,value):
    result=settle(case('under','-2.5',value,metric=metric,league=league))
    assert result['result']=='win' and result['stat_value']=='-3'


@pytest.mark.parametrize('operator,value,result', [('yes',0,'loss'),('yes',1,'win'),('yes',2,'win'),('no',0,'win'),('no',2,'loss')])
def test_anytime_touchdown_yes_no_includes_multiple_scores(operator,value,result):
    assert settle(case(operator,'1',value,metric='touchdowns_scored',league='NFL'))['result']==result


def test_confirmed_dnp_voids_but_missing_participation_does_not():
    args=case(); args[1]['participation']={'played':False,'final':True}; args[1]['value']=None
    result=settle(args); assert result['result']=='void' and result['profit']=='0'
    args[1]['participation']={}
    assert settle(args)['reasons']==['unknown_player_participation']
    args[1]['participation']={'played':False,'final':False}
    assert settle(args)['reasons']==['participation_not_final']


def test_start_requirement_is_not_the_same_as_played():
    args=case(); args[2]['participation_requirement']='started'
    args[1]['participation'].update(played=True,started=False)
    assert settle(args)['result']=='void'
    args[1]['participation'].pop('started')
    assert 'unknown_player_participation' in settle(args)['reasons']


@pytest.mark.parametrize('value',[None, float('nan'), float('inf'), -1, 20.5, True])
def test_missing_or_invalid_count_never_becomes_zero(value):
    args=case(value=0); args[1]['value']=value
    if isinstance(value,float) and (value!=value or value==float('inf')):
        with pytest.raises(ValueError): settle(args)
    else:
        result=settle(args); assert result['state']=='pending_review' and result['payout'] is None


def test_actual_zero_is_valid_under_bet_result_when_participation_is_proven():
    assert settle(case('under','0.5',0))['result']=='win'


@pytest.mark.parametrize('key,value', [('league','NBA'),('fixture_id','other-game'),('player_id','sr:player:999'),
    ('metric','assists'),('scope','half_1'),('includes_overtime',False),('includes_shootout',True)])
def test_exact_league_player_event_metric_and_scope_are_mandatory(key,value):
    args=case(); args[1][key]=value
    assert 'observation_identity_mismatch' in settle(args)['reasons']


def test_full_game_scores_cannot_settle_a_player_prop():
    args=case(); args[1].update(entity_type='team', value=110)
    assert 'player_statistics_required' in settle(args)['reasons']


def test_missing_rules_and_unknown_dnp_or_push_policy_stay_pending():
    args=list(case()); args[2]=None
    assert 'missing_settlement_rules' in settle(args)['reasons']
    for key in ('participation_requirement','no_participation','integer_equality','stat_corrections'):
        args=case(); args[2].pop(key)
        result=settle(args); assert result['state']=='pending_review' and result['payout'] is None


def test_cancelled_match_needs_final_sourced_cancellation_and_explicit_void_rule():
    args=case(); args[1].update(event_status='cancelled',cancellation_final=True)
    assert settle(args)['result']=='void'
    args[1]['cancellation_final']=False
    assert 'event_cancellation_requires_review' in settle(args)['reasons']


def test_incomplete_period_or_nonfinal_game_never_uses_fullgame_value():
    args=case(); args[1]['scope_complete']=False
    assert 'player_scope_not_final' in settle(args)['reasons']
    args[1].update(scope_complete=True,event_status='live')
    assert 'player_scope_not_final' in settle(args)['reasons']


def test_stats_source_bytes_official_host_and_availability_time_are_checked():
    args=case(); args[3].pop(args[1]['source']['sha256'])
    assert 'source_bytes_missing_or_changed' in settle(args)['reasons']
    args=case(); args[1]['source']['url']='https://example.test/boxscore'
    assert 'unapproved_source_host' in settle(args)['reasons']
    args=case(); args[1]['available_at']=(args[4]+timedelta(minutes=1)).isoformat()
    assert 'future_player_observation' in settle(args)['reasons']


def correction(args, previous):
    ticket,obs,rules,bodies,now=deepcopy(args)
    obs.update(value=19,revision=2,available_at=now.isoformat(),supersedes_observation_sha256=previous['observation_sha256'])
    body=b'Synthetic official stat correction to nineteen points';sha=hashlib.sha256(body).hexdigest();bodies[sha]=body
    obs['source']={**obs['source'],'sha256':sha,'fetched_at':now.isoformat()}
    return ticket,obs,rules,bodies,now


def test_official_stat_correction_creates_revision_and_keeps_original_ticket_money():
    args=case(value=21); original_ticket=deepcopy(args[0]); old=settle(args); assert old['result']=='win'
    changed=correction(args,old); new=settle(changed,old)
    assert new['result']=='loss' and new['previous_assessment_sha256']==old['assessment_sha256']
    assert new['ticket_sha256']==old['ticket_sha256'] and new['accepted_odds']=='1.84' and new['stake']=='2.25'
    assert old['result']=='win' and args[0]==original_ticket and changed[0]==original_ticket
    assert settle(changed,new)==new


def test_unlinked_decreasing_or_policy_ambiguous_corrections_require_review():
    args=case(); old=settle(args)
    changed=correction(args,old); changed[1].pop('supersedes_observation_sha256')
    assert 'unlinked_stat_correction' in settle(changed,old)['reasons']
    changed=correction(args,old); changed[1]['revision']=1
    assert 'nonincreasing_stat_revision' in settle(changed,old)['reasons']
    args[2]['stat_corrections']='manual_review'; old=settle(args); changed=correction(args,old)
    assert 'stat_correction_requires_review' in settle(changed,old)['reasons']


def test_changed_ticket_and_previous_assessment_cannot_be_reconciled_silently():
    args=case(); old=settle(args); changed=correction(args,old); changed[0]['odds']='3.5'
    assert 'original_ticket_changed' in settle(changed,old)['reasons']
    tampered={**old,'profit':'999'}
    assert 'previous_assessment_changed' in settle(args,tampered)['reasons']


def test_changed_contract_is_rejected_before_any_payout():
    args=case(); args[0]['contract']['line']='0.5'
    assert settle(args)['reasons']==['contract_changed']


def test_changed_contract_receipt_identity_cannot_settle():
    args = case(); args[0]['contract']['contract_id'] = 'different-receipt'
    result = settle(args)
    assert result['state'] == 'pending_review' and 'contract_changed' in result['reasons']
