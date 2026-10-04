from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import pytest
from betting_engine.anchor_learning import (observations,fit_candidate,evaluate_candidate,update_weights,load_snapshot,runtime_settings,effective_weight,segment,game_losses,digest,RUNTIME_KEY,prediction)

NOW=datetime(2026,10,4,12,tzinfo=timezone.utc)

def decision(index,issued=None,won=True,resolved=True,model_p=.8):
    issued=issued or NOW-timedelta(days=3,hours=-index)
    return {'tip_id':f't{index}','issued_at':issued.isoformat(),'status':('won' if won else 'lost') if resolved else 'open',
      'outcome_evidence':{'settled_at':(issued+timedelta(hours=3)).isoformat()} if resolved else None,
      'payload':{'id':f't{index}','game_id':f'g{index}','league':'NFL','market':'moneyline','side':'home','stake':2.,
        'start':(issued+timedelta(hours=1)).isoformat(),'quote_at':issued.isoformat(),'model_trained_at':(NOW-timedelta(days=90)).isoformat(),
        'model_version':'v1','decision_guard':{'passed':True},
        'market_anchor':{'status':'usable','model_probability':model_p,'market_probability':.55}}}

def rows(n=5):return observations([decision(i) for i in range(n)],NOW)[0]

def test_train_requires_actual_available_prospective_inputs():
    d=decision(1);d['outcome_evidence']['settled_at']=(NOW+timedelta(days=1)).isoformat()
    r,_=observations([d],NOW);assert r[0]['outcome'] is None and not r[0]['resolved']
    d['payload']['model_trained_at']=NOW.isoformat();r,why=observations([d],NOW);assert not r and why['nonprospective_model_input']==1

def test_fit_game_balanced_duplicate_contracts_dont_change_weight():
    data=rows();data[-1]['outcome']=0
    fitted=fit_candidate(data,NOW,minimum_games=5)
    duplicated=data+[dict(data[-1],tip_id=f'x{i}') for i in range(100)]
    assert fit_candidate(duplicated,NOW,minimum_games=5)['model_weight']==fitted['model_weight']
    assert fit_candidate(data,NOW,minimum_games=6) is None

def test_no_historical_holdout_promotion():
    data=rows();candidate=fit_candidate(data,NOW,minimum_games=5)
    assert evaluate_candidate(candidate,data,NOW)['state']=='collecting_prospective'

def test_real_later_holdout_promotes_better_logloss_only():
    fit=fit_candidate(rows(),NOW,minimum_games=5)
    later,_=observations([decision(10+i,NOW+timedelta(hours=i+1)) for i in range(5)],NOW+timedelta(days=2))
    proof=evaluate_candidate(fit,later,NOW+timedelta(days=2))
    assert proof['promote'] and proof['games']==5
    assert proof['candidate_log_loss']<proof['market_log_loss'] and proof['versus_baseline']['upper95']<0
    losing=[dict(r,outcome=0) for r in later]
    fit=fit_candidate(rows(),NOW,minimum_games=5)
    assert evaluate_candidate(fit,losing,NOW+timedelta(days=2))['state']=='rejected'

def test_holdout_waits_for_all_fixed_games_not_fast_settlers():
    fit=fit_candidate(rows(),NOW,minimum_games=5)
    ds=[decision(10+i,NOW+timedelta(hours=i+1),resolved=i!=0) for i in range(8)]
    later,_=observations(ds,NOW+timedelta(days=2))
    result=evaluate_candidate(fit,later,NOW+timedelta(days=2))
    assert result['state']=='awaiting_holdout_outcomes' and fit['holdout_game_ids']==['g10','g11','g12','g13','g14']

def test_training_games_and_other_model_cannot_enter_holdout():
    fit=fit_candidate(rows(),NOW,minimum_games=5)
    later=[dict(r,issued_at=(NOW+timedelta(hours=1)).isoformat()) for r in rows()]
    assert evaluate_candidate(fit,later,NOW+timedelta(days=1))['state']=='collecting_prospective'
    later=[dict(r,game_id=f'new{i}',model_version='v2') for i,r in enumerate(later)]
    assert evaluate_candidate(fit,later,NOW+timedelta(days=1))['state']=='collecting_prospective'

def test_durable_fit_then_later_promotion_and_artifact_tamper(tmp_path):
    ds=[decision(i) for i in range(5)]
    first=update_weights(tmp_path,ds,NOW,minimum_games=5,committed_at=NOW)
    assert first['approved_weights']==0 and load_snapshot(tmp_path)['weights']=={}
    ds.extend(decision(10+i,NOW+timedelta(hours=i+1)) for i in range(5))
    second=update_weights(tmp_path,ds,NOW+timedelta(days=2),minimum_games=5,committed_at=NOW+timedelta(days=2))
    assert second['approved_weights']==1
    settings=runtime_settings({'market_model_weight':.15},tmp_path)
    weight,proof=effective_weight(settings,'NFL','moneyline',None,'v1');assert weight==1 and proof
    assert effective_weight(settings,'NFL','moneyline',None,'v2')==(.15,None)
    assert effective_weight(settings,'NHL','moneyline',None,'v1')==(.15,None)
    path=tmp_path/'anchor_learning/active.json';value=json.loads(path.read_text());value['weights'][next(iter(value['weights']))]['model_weight']=.9;path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='digest'):load_snapshot(tmp_path)

def test_composite_learning_prediction_matches_live_normalization():
    r={'side':'home_over','partition':{'model':{'home_over':.4,'home_under':.3,'away_over':.2,'away_under':.1},'market':{'home_over':.25,'home_under':.25,'away_over':.25,'away_under':.25}}}
    assert prediction(r,0)==pytest.approx(.25)
    assert prediction(r,1)==pytest.approx(.4)
    assert sum(prediction(dict(r,side=k),.35) for k in r['partition']['model'])==pytest.approx(1)

def test_quarter_line_does_not_reuse_binary_target():
    assert segment('NFL','totals',44.25)!=segment('NFL','totals',44.5)
    d=decision(1);d['payload'].update(market='totals',line=44.25)
    assert observations([d],NOW)[1]['fractional_settlement_target_required']==1


def test_runtime_guard_reproduces_and_rejects_changed_weight():
    from test_governance import guard_case
    from betting_engine.decisions import candidate
    from betting_engine.governance import EvidenceGuard,KnowledgeAgent
    _,g,m,q,f,report,s=guard_case()
    key=segment(g.league,m.kind,m.line)+'|'+f.model_version
    s.update(market_anchor_mode='live',_market_weight_snapshot={'snapshot_sha256':'s','weights':{key:{'segment':segment(g.league,m.kind,m.line),'model_version':f.model_version,'model_weight':.5,'proof_sha256':'p'}}})
    t=candidate(g,m,q,f,s,{'market_probability':.55,'conditional_push_free':False})
    assert t['market_anchor']['model_weight']==.5
    guard=EvidenceGuard(KnowledgeAgent());check=guard.check(t,g,m,q,f,report,s);assert check['passed'],check['errors']
    changed=deepcopy(s);changed[RUNTIME_KEY]['weights'][key]['model_weight']=.7
    check=guard.check(t,g,m,q,f,report,changed);assert not check['passed'] and 'learned_anchor_weight_mismatch' in check['errors']


def test_composite_runtime_receipt_uses_same_learned_weight():
    from test_complete_composite_policy import case
    from test_composite_anchor_coherence import attach_source,model_report
    from betting_engine.market_anchor import contract_anchors
    from betting_engine.decisions import candidate
    from betting_engine.governance import EvidenceGuard,KnowledgeAgent
    _,g,ms,qs,f,s=case();m=ms[0];key=segment(g.league,m.kind,m.line)+'|'+f.model_version
    s.update(market_anchor_mode='live',_market_weight_snapshot={'snapshot_sha256':'s','weights':{key:{'segment':segment(g.league,m.kind,m.line),'model_weight':.35,'proof_sha256':'p'}}})
    anchors=contract_anchors(ms,qs)
    for m in ms:
        t=candidate(g,m,qs[m.id],f,s,anchors[m.id]);attach_source(t,m)
        check=EvidenceGuard(KnowledgeAgent()).check(t,g,m,qs[m.id],f,model_report(g,f),s)
        assert check['passed'],check['errors']
        assert t['market_anchor']['model_weight']==.35

def test_same_frozen_run_idempotent_and_recovers_active_write(tmp_path):
    ds=[decision(i) for i in range(5)]
    first=update_weights(tmp_path,ds,NOW,minimum_games=5,committed_at=NOW)
    state=(tmp_path/'anchor_learning/state.json').read_bytes()
    (tmp_path/'anchor_learning/active.json').unlink()
    assert update_weights(tmp_path,ds,NOW,minimum_games=5,committed_at=NOW)==first
    assert (tmp_path/'anchor_learning/state.json').read_bytes()==state
    assert load_snapshot(tmp_path) is not None


def test_weekly_durable_job_persists_fit_and_respects_seven_days(tmp_path,monkeypatch):
    monkeypatch.setattr("betting_engine.domain.utcnow",lambda:NOW)
    from betting_engine.storage import Store
    from betting_engine.learning import LearningWorker
    store=Store(tmp_path/'data/engine.sqlite3');worker=LearningWorker(store,tmp_path/'data')
    d=decision(1)
    with store.db() as db:
        db.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload,status,pnl,outcome_evidence) VALUES(?,?,?,?,?,?,?)',('f',d['tip_id'],d['issued_at'],json.dumps(d['payload']),d['status'],1.,json.dumps(d['outcome_evidence'])))
    first=worker.plan_anchor_weights(NOW)
    assert first and worker.plan_anchor_weights(NOW)==first
    with store.db() as db:
        job=dict(db.execute('SELECT * FROM learning_jobs WHERE id=?',(first,)).fetchone())
    result=worker.execute(job);assert result['accepted_rows']==1 and result['approved_weights']==0
    with store.db() as db:db.execute("UPDATE learning_jobs SET status='completed',updated_at=? WHERE id=?",(NOW.isoformat(),first))
    assert worker.plan_anchor_weights(NOW+timedelta(days=6)) is None
    assert worker.plan_anchor_weights(NOW+timedelta(days=7))!=first


def test_publication_receipt_catches_concurrent_weight_promotion():
    from test_governance import guard_case
    from betting_engine.decisions import candidate,rank_and_size
    from betting_engine.governance import EvidenceGuard,KnowledgeAgent,verify_publication
    _,g,m,q,f,report,s=guard_case();s['market_anchor_mode']='live'
    t=candidate(g,m,q,f,s,{'market_probability':.55,'conditional_push_free':False})
    assert EvidenceGuard(KnowledgeAgent()).check(t,g,m,q,f,report,s)['passed']
    rank_and_size([t],s)
    changed={**s,RUNTIME_KEY:{'snapshot_sha256':'new','weights':{}}}
    with pytest.raises(ValueError,match='anchor settings changed'):verify_publication(t,datetime.now(timezone.utc),changed)


def test_existing_setting_save_cannot_persist_runtime_artifact(tmp_path):
    from betting_engine.storage import Store
    store=Store(tmp_path/'engine.sqlite3')
    store.save_settings({'unit_stake':2,RUNTIME_KEY:{'malicious':'ignored'}})
    with store.db() as db:value=json.loads(db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()[0])
    assert RUNTIME_KEY not in value

def test_delayed_job_uses_actual_fit_availability_not_queued_cutoff(tmp_path):
    ds=[decision(i) for i in range(5)]
    committed=NOW+timedelta(days=2)
    update_weights(tmp_path,ds,NOW,minimum_games=5,committed_at=committed)
    ds.extend(decision(10+i,NOW+timedelta(hours=i+1)) for i in range(5))
    result=update_weights(tmp_path,ds,NOW+timedelta(days=3),minimum_games=5,committed_at=NOW+timedelta(days=3))
    assert result['approved_weights']==0
    assert result['segments'][0]['state']=='collecting_prospective'


def test_changed_baseline_cannot_promote_against_obsolete_weight(tmp_path):
    ds=[decision(i) for i in range(5)]
    update_weights(tmp_path,ds,NOW,minimum_games=5,committed_at=NOW)
    ds.extend(decision(10+i,NOW+timedelta(hours=i+1)) for i in range(5))
    result=update_weights(tmp_path,ds,NOW+timedelta(days=2),minimum_games=5,fallback_weight=.7,committed_at=NOW+timedelta(days=2))
    assert result['approved_weights']==0 and result['segments'][0]['state']=='rejected'
    assert 'baseline changed' in result['segments'][0]['reason']


def promoted(tmp_path):
    ds=[decision(i) for i in range(5)]
    update_weights(tmp_path,ds,NOW,minimum_games=5,committed_at=NOW)
    ds.extend(decision(10+i,NOW+timedelta(hours=i+1)) for i in range(5))
    update_weights(tmp_path,ds,NOW+timedelta(days=2),minimum_games=5,committed_at=NOW+timedelta(days=2))
    active=load_snapshot(tmp_path);row=next(iter(active['weights'].values()))
    proof_path=tmp_path/'anchor_learning/archive'/(row['proof_sha256']+'.json')
    return active,json.loads(proof_path.read_text())


def test_missing_frozen_training_input_cannot_load_approved_weights(tmp_path):
    _,proof=promoted(tmp_path)
    (tmp_path/'anchor_learning/archive'/(proof['input_archive']+'.json')).unlink()
    with pytest.raises(OSError):load_snapshot(tmp_path)


def test_modified_frozen_holdout_cannot_load_approved_weights(tmp_path):
    _,proof=promoted(tmp_path)
    path=tmp_path/'anchor_learning/archive'/(proof['promotion_input_archive']+'.json')
    value=json.loads(path.read_text());value['rows'][-1]['outcome']=0;path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='input_digest'):load_snapshot(tmp_path)


def test_weight_publication_epoch_excludes_promotion_until_db_commit(tmp_path,monkeypatch):
    import threading
    import betting_engine.storage as storage
    from betting_engine.anchor_learning import publish_snapshot
    store=storage.Store(tmp_path/'engine.sqlite3');folder=tmp_path/'anchor_learning';folder.mkdir()
    entered=threading.Event();completed=threading.Event();threads=[]
    original=store._auto_track
    def paused(db,payload,now):
        def promote():entered.set();publish_snapshot(folder,{'test':True});completed.set()
        t=threading.Thread(target=promote);threads.append(t);t.start()
        assert entered.wait(1) and not completed.wait(.05)
        assert not (folder/'active.json').exists()
        return original(db,payload,now)
    monkeypatch.setattr(store,'_auto_track',paused)
    store.publish({'recommendations':[]})
    assert completed.wait(1)
    for t in threads:t.join()
    assert store.latest() is not None

def test_legacy_threeway_and_doublechance_are_not_learning_sources():
    for market in ('three_way','period_1:double_chance'):
        d=decision(1);d['payload']['market']=market
        data,reasons=observations([d],NOW)
        assert not data and reasons['legacy_incompatible_partition_semantics']==1


def test_double_chance_training_predicts_union_of_exclusive_partition():
    d=decision(1);t=d['payload'];t.update(market='quarter_1:double_chance',side='home_draw')
    t['market_anchor'].update(probability_semantics='derived_double_chance_union_v1',model_probability=.7,market_probability=.65,
        model_probabilities={'home':.4,'draw':.3,'away':.3},exclusive_probabilities={'home':.35,'draw':.3,'away':.35})
    data,reasons=observations([d],NOW);assert not reasons
    assert prediction(data[0],0)==pytest.approx(.65) and prediction(data[0],1)==pytest.approx(.7)
    assert 'derived_double_chance_union_v1' in data[0]['segment']


def test_validated_snapshot_cache_skips_refit_but_modified_proof_invalidates(tmp_path,monkeypatch):
    import betting_engine.anchor_learning as mod
    active,proof=promoted(tmp_path)
    monkeypatch.setattr(mod,'fit_candidate',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('unexpected refit')))
    assert load_snapshot(tmp_path)==active
    row=next(iter(active['weights'].values()));path=tmp_path/'anchor_learning/archive'/(row['proof_sha256']+'.json')
    path.write_text(path.read_text()+' ')
    with pytest.raises(AssertionError,match='unexpected refit'):load_snapshot(tmp_path)

def test_composite_learned_proof_cannot_be_carried_through_unchecked():
    from test_complete_composite_policy import case
    from betting_engine.market_anchor import contract_anchors
    from betting_engine.composite_contracts import receipt,anchored_partition
    _,g,ms,qs,f,s=case();m=ms[0];anchor=contract_anchors(ms,qs)[m.id]
    anchor['learned_weight']={'proof_sha256':'forged'}
    with pytest.raises(ValueError,match='provenance'):anchored_partition(receipt(g,m,f,s),m,s,anchor,qs[m.id].fetched_at)

def test_settings_change_during_fit_cannot_activate_a_stale_baseline(tmp_path):
    ds=[decision(i) for i in range(5)]
    update_weights(tmp_path,ds,NOW,minimum_games=5,committed_at=NOW)
    before=(tmp_path/'anchor_learning/active.json').read_bytes()
    ds.extend(decision(10+i,NOW+timedelta(hours=i+1)) for i in range(5))
    with pytest.raises(ValueError,match='baseline_changed_during_fit'):
        update_weights(tmp_path,ds,NOW+timedelta(days=2),minimum_games=5,committed_at=NOW+timedelta(days=2),fallback_reader=lambda:.7)
    assert (tmp_path/'anchor_learning/active.json').read_bytes()==before
