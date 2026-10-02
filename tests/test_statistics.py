from copy import deepcopy
from datetime import datetime, timezone
import json
import math

import pytest

from betting_engine.statistics import build_statistics, execution_source

ASOF = '2026-09-30T12:00:00Z'
LEGACY = 'Original issued decision at published odds; not an account-verified return.'


def fixtures(bid=1, *, status='won', stake=2, odds=2.5, pnl=3, game='g1', market='moneyline',
             side='home', line=None, home=3, away=1, scope='full_game', source='auto_assumed_placed'):
    tip={'id': f'tip{bid}', 'game_id': game, 'league': 'NHL', 'market': market, 'side': side,
         'line': line, 'model_scope': scope, 'start': '2026-09-30T10:00:00Z', 'stake': stake,
         'reference_odds': odds, 'probability': 0.6234567890123456, 'push_probability': 0,
         'model_version': 'original-v1', 'tracking_source': source, 'ledger_contract': 'sportsbook',
         'execution_verified': False, 'selection': 'Home', 'funding_basis': {'tier': 'model_pick'}}
    evidence={'game_id':game,'home_score':home,'away_score':away,
              'source_url':'https://official.test/final','source_fetched_at':'2026-09-30T11:00:00Z',
              'settled_at':'2026-09-30T11:01:00Z','basis':'verified_score_in_contract_scope',
              'score_scope':{'scope':scope}, 'stake_settlement_verified':False}
    bet={'id':bid,'tip_id':tip['id'],'created_at':'2026-09-29T23:00:00Z','stake':stake,'odds':odds,
         'platform':'Stake.com','status':status,'pnl':pnl,'payload':deepcopy(tip)}
    decision={'family':'contract:'+tip['id'],'tip_id':tip['id'],'issued_at':'2026-09-29T22:59:00Z',
              'status':status,'pnl':pnl,'payload':deepcopy(tip),'outcome_evidence':deepcopy(evidence)}
    outcome={'bet_id':bid,'league':'NHL','market':market,'model_version':'original-v1',
             'result':status,'probability':tip['probability'],'push_probability':0,'brier':999,
             'log_loss':999,'evidence':deepcopy(evidence)}
    return bet,decision,outcome


def report(*fixtures_, as_of=ASOF):
    return build_statistics([f[0] for f in fixtures_],[f[1] for f in fixtures_],
                            [f[2] for f in fixtures_],as_of=as_of)


def test_money_tickets_and_original_decisions_have_separate_duplicate_denominators():
    b,d,o=fixtures();duplicate=deepcopy(b);duplicate['id']=2;duplicate['stake']=1;duplicate['odds']=2;duplicate['pnl']=1
    oo=deepcopy(o);oo['bet_id']=2
    r=build_statistics([b,duplicate],[d],[o,oo],as_of=ASOF)
    money=r['ticket_ledger']['summary'];original=r['system_decisions']['summary']
    assert (money['count'],money['settled_stake'],money['payout'],money['pnl'],money['roi'])==(2,3,7,4,4/3)
    assert (original['count'],original['settled_stake'],original['pnl'])==(1,2,3)
    assert money['calibration']['observations']==1
    assert money['calibration']['excluded']=={'duplicate_ticket':1}
    assert money['settled_games']==original['settled_games']==1
    assert {x['stake'] for x in r['ticket_ledger']['closed_tickets']}=={1,2}


def test_push_void_manual_and_unknown_are_never_inferred_as_won_or_lost():
    rows=[fixtures(1),fixtures(2,status='lost',pnl=-2,home=0,away=1),
          fixtures(3,status='push',pnl=0,home=1,away=1),fixtures(4,status='void',pnl=0),
          fixtures(5,status='manual',pnl=.25),fixtures(6,status='open',pnl=None),
          fixtures(7,status='awaiting_review',pnl=100)]
    r=report(*rows);s=r['ticket_ledger']['summary']
    assert (s['won'],s['lost'],s['pushes'],s['voids'],s['manual'],s['open'],s['unknown'])==(1,1,1,1,1,1,1)
    assert s['settled']==5 and s['settled_stake']==10
    assert s['pnl']==1.25 and s['payout']==11.25 and s['roi']==.125
    assert s['hit_rate']==.5 and s['open_stake']==2
    assert next(x for x in r['ticket_ledger']['closed_tickets'] if x['status']=='manual')['result'] is None
    assert r['resolution_audit']['counts']['unknown_status']==2


def test_zero_and_missing_denominators_are_null_not_zero_or_profit_claims():
    r=build_statistics([],[],[],as_of=ASOF);s=r['ticket_ledger']['summary']
    assert s['count']==0 and s['pnl']==0 and s['roi'] is None and s['hit_rate'] is None
    s=report(fixtures(status='open',pnl=None))['ticket_ledger']['summary']
    assert s['settled']==0 and s['roi'] is None and s['payout']==0
    s=report(fixtures(stake=0,pnl=0))['ticket_ledger']['summary']
    assert s['roi'] is None


@pytest.mark.parametrize('value',[None,float('nan'),float('inf'),True,'3'])
def test_invalid_closed_money_keeps_known_subtotals_but_no_fabricated_total(value):
    bad=fixtures(2,pnl=value)
    s=report(fixtures(),bad)['ticket_ledger']['summary']
    assert s['settled']==2 and s['excluded_money_rows']==1
    assert not s['money_complete'] and s['pnl'] is None and s['payout'] is None and s['roi'] is None
    assert s['known_pnl']==3 and s['known_settled_stake']==2
    json.dumps(report(fixtures(),bad),allow_nan=False)


def test_negative_payout_is_an_integrity_problem_not_accepted_cashflow():
    r=report(fixtures(pnl=-3));s=r['ticket_ledger']['summary']
    assert s['payout'] is None and not s['money_complete']
    assert 'negative_payout' in r['resolution_audit']['counts']


def test_known_recorded_pnl_is_not_silently_replaced_by_expected_price_payoff():
    r=report(fixtures(pnl=1));s=r['ticket_ledger']['summary']
    assert s['pnl']==1 and s['payout']==3
    assert r['resolution_audit']['counts']['recorded_pnl_disagrees_with_result']==2


def test_calibration_uses_frozen_precision_conditional_push_and_game_weighting():
    a=fixtures();b=fixtures(2,game='g1',status='lost',pnl=-2,home=0,away=1);c=fixtures(3,game='g2')
    for f in (a,b,c):
        f[0]['payload'].update(probability=.4,push_probability=.2)
        f[1]['payload'].update(probability=.4,push_probability=.2)
    s=report(a,b,c)['system_decisions']['summary']['calibration']
    assert s['observations']==3 and s['independent_games']==2
    assert s['brier']==.25 and s['game_equal_weight_brier']==.25
    assert s['log_loss']==pytest.approx(math.log(2))
    diagram=s['reliability_diagram']
    assert len(diagram)==10 and sum(row['observations'] for row in diagram)==3
    assert diagram[5]['observations']==3 and diagram[5]['predicted']==pytest.approx(.5)
    assert diagram[5]['observed']==pytest.approx(2/3)
    r=report(fixtures()); row=r['ticket_ledger']['closed_tickets'][0]
    assert row['probability']==.6234567890123456
    assert row['brier']==pytest.approx((.6234567890123456-1)**2)
    assert row['brier']!=999  # Stored derived scores are not blindly trusted.


@pytest.mark.parametrize('p,push',[(None,0),(1.1,0),(.9,.2),(.5,1),(True,0),(.5,-.1)])
def test_invalid_original_probability_excluded_from_calibration_not_money(p,push):
    f=fixtures();f[0]['payload'].update(probability=p,push_probability=push)
    s=report(f)['ticket_ledger']['summary']
    assert s['pnl']==3 and s['calibration']['observations']==0
    assert s['calibration']['excluded']=={'invalid_probability':1}


def test_execution_source_is_explicit_and_checklist_never_account_verification():
    rows=[fixtures(1)[0],fixtures(2,source='user_reported_placed')[0],fixtures(3)[0],fixtures(4)[0],fixtures(5)[0]]
    rows[2]['payload']['execution_verified']=True
    rows[3]['payload'].pop('tracking_source');rows[3]['payload']['ledger_contract']='manual'
    rows[4]['payload']['tracking_source']='unknown_provider';rows[4]['payload']['placed_at']=ASOF
    assert [execution_source(r) for r in rows]==['assumed','reported','verified','manual','unknown']
    r=build_statistics(rows,[],[],as_of=ASOF)
    assert {x['execution_source'] for x in r['ticket_ledger']['by_execution_source']}=={'assumed','reported','verified','manual','unknown'}
    assert r['ticket_ledger']['closed_tickets'][0]['settlement_source']['independently_verified_by_statistics'] is False


def test_scope_groups_stay_distinct_and_period_never_settles_from_fullgame_evidence():
    full=fixtures();period=fixtures(2,market='period_1:moneyline',scope='period_1')
    r=report(full,period)
    assert {(x['scope'],x['market_family']) for x in r['system_decisions']['by_league_market']}=={('full_game','moneyline'),('period_1','moneyline')}
    period[2]['evidence']['score_scope']['scope']='full_game'
    r=report(period)
    assert r['ticket_ledger']['summary']['calibration']['observations']==0
    assert r['ticket_ledger']['closed_tickets'][0]['reproduced_result'] is None
    assert 'settlement_scope_unverified' in r['resolution_audit']['counts']


def test_narrow_legacy_fullgame_reference_is_labeled_and_does_not_admit_periods():
    f=fixtures();f[1]['outcome_evidence'].pop('score_scope');f[1]['outcome_evidence']['basis']=LEGACY
    r=report(f)
    assert r['system_decisions']['summary']['calibration']['observations']==1
    assert r['system_decisions']['resolution_coverage']=={'legacy_full_game_reference':1}
    assert r['system_decisions']['summary']['issues']=={'legacy_full_game_reference':1}
    assert r['resolution_audit']['warning_count']==1 and r['resolution_audit']['error_count']==0
    for market in ('period_1:moneyline','innings_1_5:moneyline'):
        f[1]['payload']['market']=market
        r=report(f)
        assert r['system_decisions']['summary']['calibration']['observations']==0
        assert r['system_decisions']['resolution_coverage']=={'unverified_scope':1}
    f[1]['payload']['market']='moneyline';f[1]['outcome_evidence']['basis']='arbitrary_guess'
    assert report(f)['system_decisions']['summary']['calibration']['observations']==0


def test_outcome_conflicts_and_score_mismatches_remain_visible():
    f=fixtures();f[2]['result']='lost';f[2]['evidence']['home_score']=0
    f[2]['market']='totals'
    r=report(f);issues=r['ticket_ledger']['closed_tickets'][0]['issues']
    assert 'bet_outcome_status_mismatch' in issues and 'recorded_result_disagrees_with_scores' in issues
    assert 'bet_outcome_market_mismatch' in issues
    assert r['ticket_ledger']['summary']['calibration']['observations']==0
    assert r['ticket_ledger']['summary']['pnl']==3  # Original money preserved, flagged.


def test_provenance_levels_are_retained_references_not_reverified_sources():
    rows=[fixtures(i) for i in range(1,5)]
    rows[0][2]['evidence']['score_scope']['official_result']={'sha256':'a'*64,'archive':'sources/a.json','url':'https://official.test/a','fetched_at':ASOF}
    rows[1][2]['evidence']['score_scope']['source_sha256']='b'*64
    rows[3][2]['evidence'].pop('source_url');rows[3][2]['evidence'].pop('source_fetched_at')
    r=report(*rows)
    assert r['ticket_ledger']['source_coverage']=={'retained_archive_hash':1,'retained_hash':1,'retained_url_time':1,'missing':1}
    assert all(not x['settlement_source']['independently_verified_by_statistics'] for x in r['ticket_ledger']['closed_tickets'])


def test_fingerprints_exclude_asof_input_order_but_bind_outcomes_and_original_terms():
    a,b=fixtures(),fixtures(2,status='lost',pnl=-2,home=0,away=1)
    old=report(a,b); newer=report(b,a,as_of='2026-10-01T00:00:00Z')
    assert old['evidence_sha256']==newer['evidence_sha256']
    assert old['system_decisions']['evidence_sha256']==newer['system_decisions']['evidence_sha256']
    a[0]['stake']=4
    changed=report(a,b)
    assert old['evidence_sha256']!=changed['evidence_sha256']
    assert old['system_decisions']['evidence_sha256']==changed['system_decisions']['evidence_sha256']
    a[1]['outcome_evidence']['home_score']=4
    assert report(a,b)['system_decisions']['evidence_sha256']!=old['system_decisions']['evidence_sha256']


def test_source_rows_not_mutated_and_shadow_fields_not_added_to_money():
    f=fixtures();before=deepcopy(f)
    r=report(f)
    assert f==before and r['ticket_ledger']['summary']['count']==1
    assert 'prospective' not in r and 'shadow' not in r


def test_same_export_row_does_not_double_count_but_conflicting_id_is_quarantined():
    b,d,o=fixtures()
    r=build_statistics([b,deepcopy(b)],[d,deepcopy(d)],[o,deepcopy(o)],as_of=ASOF)
    assert r['ticket_ledger']['summary']['count']==r['system_decisions']['summary']['count']==1
    assert r['resolution_audit']['counts']['duplicate_input_row']==3
    changed=deepcopy(b);changed['stake']=5
    r=build_statistics([b,changed],[d],[o],as_of=ASOF)
    assert r['ticket_ledger']['summary']['count']==0
    assert r['resolution_audit']['counts']['conflicting_duplicate_identity']==1


def test_no_hidden_500_ticket_cutoff_and_all_groups_reconcile():
    rows=[fixtures(i,game=f'g{i}',status='lost',pnl=-2,home=0,away=1) for i in range(1,602)]
    r=report(*rows);s=r['ticket_ledger']['summary']
    assert s['count']==601 and s['pnl']==-1202
    assert sum(x['settled'] for x in r['ticket_ledger']['by_league_market'])==601
    assert len(r['ticket_ledger']['closed_tickets'])==601


@pytest.mark.parametrize('asof',[None,'2026-09-30',datetime(2026,9,30)])
def test_report_requires_explicit_aware_asof(asof):
    with pytest.raises(ValueError):build_statistics([],[],[],as_of=asof)


def test_report_rejects_unparsed_database_payload():
    b,d,o=fixtures();b['payload']=json.dumps(b['payload'])
    with pytest.raises(ValueError):build_statistics([b],[d],[o],as_of=ASOF)


def test_conflicting_duplicate_resolution_is_order_independent_and_preserves_ticket_money():
    b,d,o=fixtures();bad=deepcopy(b);bad['id']=2
    bad_outcome=deepcopy(o);bad_outcome['bet_id']=2;bad_outcome['evidence']['home_score']=0
    first=build_statistics([b,bad],[d],[o,bad_outcome],as_of=ASOF)
    reverse=build_statistics([bad,b],[d],[bad_outcome,o],as_of=ASOF)
    assert first==reverse
    s=first['ticket_ledger']['summary']
    assert s['count']==2 and s['pnl']==6 and s['settled_stake']==4
    assert s['calibration']['observations']==0 and s['calibration']['brier'] is None
    assert s['calibration']['excluded']=={'resolution_evidence_issue':2}


def test_conflicting_duplicate_contract_not_just_probabilities_is_excluded():
    b,d,o=fixtures();bad=deepcopy(b);bad['id']=2;bad['payload']['side']='away'
    bad_outcome=deepcopy(o);bad_outcome['bet_id']=2
    r=build_statistics([b,bad],[d],[o,bad_outcome],as_of=ASOF)
    assert r['ticket_ledger']['summary']['calibration']['excluded']=={'conflicting_contract_observations':2}


def test_void_and_manual_returns_do_not_require_finished_match_scores():
    f=fixtures(status='void',pnl=0)
    f[2]['evidence']={'basis':'reported_cancellation','source_url':'https://official.test/cancelled','settled_at':ASOF}
    f[1]['outcome_evidence']=deepcopy(f[2]['evidence'])
    r=report(f)
    assert r['ticket_ledger']['summary']['payout']==2
    assert r['resolution_audit']['issue_count']==0
    assert r['ticket_ledger']['closed_tickets'][0]['resolution_evidence_state']=='not_applicable'


def test_original_home_away_and_exact_stake_market_display_are_preserved():
    f=fixtures();f[0]['payload'].update(away='Away Team',home='Home Team',market_display='Winner (Incl. Overtime and Penalties)')
    r=report(f)['ticket_ledger']['closed_tickets'][0]
    assert r['matchup']=='Away Team @ Home Team'
    assert r['market_label']=='Winner (Incl. Overtime and Penalties)'
    f[0]['payload'].pop('market_display');f[0]['payload']['stake_market']={'name':'Exact Original Market'}
    assert report(f)['ticket_ledger']['closed_tickets'][0]['market_label']=='Exact Original Market'
    f[0]['payload'].pop('stake_market');f[0]['payload']['stake_evidence']={'market_name':'Total (Incl. Overtime)'}
    assert report(f)['ticket_ledger']['closed_tickets'][0]['market_label']=='Total (Incl. Overtime)'
    f[0]['payload'].pop('away')
    assert report(f)['ticket_ledger']['closed_tickets'][0]['matchup'] is None
