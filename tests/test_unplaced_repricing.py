from copy import deepcopy
import json
from datetime import timedelta
import pytest
from betting_engine.domain import utcnow
from betting_engine.instructions import instruction_review
from test_existing_stake_reassessment import case


def setup(tmp_path):
    store, current, settings = case(tmp_path)
    store.save_settings(settings)
    store.publish({'recommendations':[current], 'evaluated_candidates':[current]}, require_guard=True)
    return store,current,settings


def test_authorized_revision_preserves_decisions_counts_and_audits_execution(tmp_path):
    store,current,settings=setup(tmp_path)
    original=deepcopy(store.system_decisions());count=store.daily_progress()['issued']
    before=store.bets()[0]
    result=store.reprice_unplaced('user-1',[current['id']],'User explicitly reports this ready batch unplaced')
    after=store.bets()[0]
    assert len(result['revised'])==1 and result['new_issuances']==0
    assert before['stake']==1 and after['stake']==4
    assert store.system_decisions()==original and store.daily_progress()['issued']==count
    assert after['id']==before['id'] and after['created_at']==before['created_at']
    assert store.held_event_stakes()[current['game_id']]==4 and store.open_exposure()==4
    review=instruction_review(after,current,utcnow(),settings['minimum_roi'],sizing_settings=settings)
    assert review['actionable'] and review['stake']==4
    assert review['stake_basis']=='authorized_unplaced_revision'
    with store.db() as c:
        audit=c.execute('SELECT * FROM bet_reconciliation').fetchone()
        assert json.loads(audit['before_record'])['stake']==1
        assert json.loads(audit['after_record'])['stake']==4
    assert store.reprice_unplaced('user-1',[current['id']],result['authorization'])==result
    assert len(store.bets())==1
    with pytest.raises(ValueError,match='reused'):
        store.reprice_unplaced('user-1',[current['id']],'different')


@pytest.mark.parametrize('state',['marked','reported','duplicate','stale','missing'])
def test_never_reprices_non_ready_or_reported_tickets(tmp_path,state):
    store,current,settings=setup(tmp_path)
    if state=='marked':store.mark_placed(current['id'],True)
    elif state=='reported':
        store.import_reported_bets('receipt',[{'tip':current,'stake':1,'odds':2}])
    elif state=='duplicate':
        with store.db() as c:
            c.execute("INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload) SELECT tip_id,created_at,stake,odds,platform,payload FROM bets")
    elif state=='stale':
        # The publication fails current guard freshness even without a new cycle.
        import betting_engine.unplaced_repricing as module
        original_now=module.utcnow
        module.utcnow=lambda:original_now()+timedelta(seconds=301)
    before=deepcopy(store.bets())
    try:
        result=store.reprice_unplaced('req',[current['id'] if state!='missing' else 'absent'],'explicit unplaced')
    finally:
        if state=='stale':module.utcnow=original_now
    assert not result['revised'] and result['skipped']
    assert store.bets()==before


def test_settlement_cash_uses_revised_stake_original_decision_does_not(tmp_path):
    store,current,settings=setup(tmp_path)
    store.reprice_unplaced('req',[current['id']],'explicit unplaced')
    bet=store.open_bets()[0]
    store.settle(bet['id'],'lost',-bet['stake'])
    assert store.bets()[0]['pnl']==-4
    assert store.system_decisions()[0]['payload']['stake']==1


def test_event_cap_includes_other_held_cash_and_never_creates_topup(tmp_path):
    store,current,settings=setup(tmp_path)
    from betting_engine.decisions import PUBLICATION_LIMITS_V2
    settings.update(publication_limits_version=PUBLICATION_LIMITS_V2,max_per_event=4,
                    max_period_per_event=2,max_stake_per_event=10.,
                    max_new_funded_per_publication=15,max_daily_family_share=.35)
    current['decision_guard']['publication_limits']={k:settings[k] for k in (
        'publication_limits_version','max_new_funded_per_publication','max_per_event',
        'max_period_per_event','max_stake_per_event','max_daily_family_share')}
    store.save_settings(settings)
    with store.db() as c:
        payload={**current,'id':'other-held','tracking_source':'user_reported_placed'}
        c.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload) VALUES (?,?,?,?,?,?)',
                  ('other-held',utcnow().isoformat(),8.,2.,'Stake.com',json.dumps(payload)))
    store.publish({'recommendations':[current],'evaluated_candidates':[current]},require_guard=True)
    result=store.reprice_unplaced('cap',[current['id']],'explicit unplaced')
    assert result['revised'][0]['new_stake']==2
    assert store.held_event_stakes()[current['game_id']]==10
    assert len(store.bets())==2 and store.daily_progress()['issued']==1


def test_parallel_same_request_is_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store,current,_=setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:store.reprice_unplaced('same',[current['id']],'explicit unplaced'),range(2)))
    assert results[0]==results[1]
    with store.db() as c:assert c.execute('SELECT COUNT(*) FROM bet_reconciliation').fetchone()[0]==1


def test_settlement_reader_cannot_commit_stale_cash_after_revision(tmp_path):
    store,current,_=setup(tmp_path)
    old=store.open_bets()[0]
    store.reprice_unplaced('resize',[current['id']],'explicit unplaced')
    store.settle(old['id'],'lost',-old['stake'],evidence={'basis':'verified_score_in_contract_scope'})
    assert store.bets()[0]['pnl']==-4


from test_outcome_sizing_integration import make_tip, history, insert


def test_repricing_replays_outcome_summary_against_locked_original_ledger(make_tip,tmp_path):
    import test_continuous_delivery as cases
    rows=history()
    original,settings,check=make_tip(rows,probability=.65,odds=1.4)
    assert check['passed']
    store=cases.database(tmp_path,settings);insert(store,rows)
    store.publish({'recommendations':[original],'evaluated_candidates':[original]},require_guard=True)
    current,_,check=make_tip(store.system_decisions(),probability=.65,odds=1.4,
                           start=original['start'],held=[original['id']])
    assert check['passed']
    store.publish({'recommendations':[current],'evaluated_candidates':[current]},require_guard=True)
    before=deepcopy(store.bets())
    # Emulate changed historical evidence after the original publication. A
    # self-consistent candidate hash cannot substitute for transactional replay.
    with store.db() as c:
        c.execute("UPDATE system_decisions SET pnl=-100 WHERE family=?",(rows[0]['family'],))
    with pytest.raises(ValueError,match='differs from original ledger'):
        store.reprice_unplaced('ledger-race',[current['id']],'explicit unplaced')
    assert store.bets()==before
    with store.db() as c:assert c.execute('SELECT COUNT(*) FROM bet_reconciliation').fetchone()[0]==0
