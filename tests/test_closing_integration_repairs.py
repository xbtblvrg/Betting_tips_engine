from datetime import datetime, timedelta
import gzip

import pytest

from test_captured_price_evaluation import decision, archive, response, freeze
from betting_engine.closing_runtime import closed_cohorts
from betting_engine.closing_runtime import evaluate_game
from betting_engine.storage import Store
from betting_engine.learning import LearningWorker
import json
import sqlite3
from betting_engine.closing_prices import evaluate_closing_price


def test_later_same_event_schedule_conflict_cannot_resurrect_old_quote(tmp_path):
    old=archive(tmp_path,observed='2026-09-29T19:58:00+00:00')
    changed=response();changed['fixture']['startTime']+=60000
    changed['groups'][0]['markets'][0][0].update(status='suspended',outcomes=[])
    newer=archive(tmp_path,changed)
    result=freeze(tmp_path,[old,newer])['reports'][0]
    assert result['status']=='missing_closing_price' and result['price_clv'] is None
    assert 'conflicting_fixture_start' in {e['reason'] for e in result['capture_errors']}


def test_legacy_full_game_scope_is_identified_by_exact_title_without_mutation():
    d=decision();del d['payload']['model_scope'];before=json.dumps(d,sort_keys=True)
    result=evaluate_closing_price(d,[],as_of='2026-09-29T21:00:00+00:00')
    assert result['status']=='missing_closing_price'
    assert json.dumps(d,sort_keys=True)==before
    d['payload']['model_scope']='half_1'
    assert evaluate_closing_price(d,[],as_of='2026-09-29T21:00:00+00:00')['status']=='invalid_decision'


@pytest.mark.parametrize('outcome',['Draw or Away','Away or Draw'])
def test_exact_observed_double_chance_word_orders_are_both_understood(outcome):
    d=decision();t=d['payload'];t.update(league='NHL',market='regulation:double_chance',model_scope='regulation',side='away_draw',line=None)
    t['stake_evidence'].update(market_name='Double Chance',outcome_name=outcome)
    assert evaluate_closing_price(d,[],as_of='2026-09-29T21:00:00+00:00')['status']=='missing_closing_price'
    t['side']='home_draw'
    assert evaluate_closing_price(d,[],as_of='2026-09-29T21:00:00+00:00')['status']=='invalid_decision'


def test_corrupted_capture_with_wrong_id_and_matching_url_still_blocks(tmp_path):
    old=archive(tmp_path,observed='2026-09-29T19:58:00+00:00')
    newer=archive(tmp_path,response(1.9));newer['fixture_id']='incorrect-id'
    (tmp_path/newer['archive']).write_bytes(gzip.compress(b'{"changed":"body"}'))
    result=freeze(tmp_path,[old,newer])['reports'][0]
    assert result['status']=='missing_closing_price' and result['price_clv'] is None
    assert 'response_hash_mismatch' in {e['reason'] for e in result['capture_errors']}


def test_closing_cohort_waits_for_capture_completion_and_ignores_later_settlement():
    d=decision();start=datetime.fromisoformat(d['payload']['start'])
    assert closed_cohorts([d],start+timedelta(seconds=89))==[]
    first=closed_cohorts([d],start+timedelta(seconds=90))
    d.update(status='won',pnl=3.3,outcome_evidence={'updated':'score correction'})
    assert closed_cohorts([d],start+timedelta(hours=2))==first
    assert 'pnl' not in first[0]['decisions'][0]


def test_runtime_freezes_actual_capture_schema_once_and_never_mutates_wagers(tmp_path,monkeypatch):
    d=decision();start=datetime.fromisoformat(d['payload']['start']);now=start+timedelta(seconds=90)
    s=Store(tmp_path/'engine.sqlite3')
    with s.db() as db:
        db.execute('INSERT INTO system_decisions VALUES (?,?,?,?,?,?,?)',
            (d['family'],d['tip_id'],d['issued_at'],json.dumps(d['payload']),'lost',-3,json.dumps(d['outcome_evidence'])))
    row=archive(tmp_path)
    from betting_engine.closing_capture import capture_issued_prices
    source={'url':row['source_url'],'sha256':row['sha256'],'archive':row['archive'],'fetched_at':row['observed_at']}
    capture_issued_prices(s,{'api_audit':{'fixtures':[{'fixture_id':'fixture','league':'NBA','responses':{'odds':source}}]}},datetime.fromisoformat(row['observed_at']))
    original=s.system_decisions()
    first=evaluate_game(s,tmp_path,'NBA:example',start.isoformat(),now)
    second=evaluate_game(s,tmp_path,'NBA:example',start.isoformat(),now+timedelta(minutes=1))
    assert first['study_id']==second['study_id'] and first['status_counts']['comparable']==1
    report=json.loads((tmp_path/'closing_evaluation.json').read_text())
    assert report['evaluated_decisions']==1 and report['reports'][0]['price_clv']==pytest.approx(.05)
    assert not report['profit_established']
    from betting_engine.presentation import english_payload
    displayed=english_payload(report)
    assert displayed['reports'][0]['market_display']=='Total (Incl. Overtime)'
    assert 'stake_market' not in displayed['reports'][0]
    assert s.system_decisions()==original and s.bets()==[]
    with sqlite3.connect(tmp_path/'closing_studies.sqlite3') as db:assert db.execute('SELECT count(*) FROM studies').fetchone()[0]==1
    monkeypatch.setattr('betting_engine.learning.utcnow',lambda:now)
    worker=LearningWorker(s,tmp_path);worker.plan();worker.plan()
    with s.db() as db:
        jobs=[dict(r) for r in db.execute("SELECT * FROM learning_jobs WHERE kind='closing_evaluation'")]
    assert len(jobs)==1
    monkeypatch.setattr('betting_engine.closing_runtime.utcnow',lambda:now)
    assert worker.execute(jobs[0])['study_id']==first['study_id']
