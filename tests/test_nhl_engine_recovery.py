"""A failed recovery audit must not let schedule-only finals settle tickets."""
import json
from copy import deepcopy
import pytest
from betting_engine import engine, nhl_results
from betting_engine.engine import Engine
from betting_engine.storage import Store
from test_nhl_result_recovery import observed, parse_landing, Feed, NOW


@pytest.mark.parametrize('failure_site',['attempt','report','games'])
def test_recovery_persistence_failure_preserves_both_ledgers_and_other_leagues(tmp_path,monkeypatch,failure_site):
    store=Store(tmp_path/'ledger.sqlite3');raw,meta,tip=observed(4)
    old=parse_landing(raw,meta,tip,NOW);old.context={}
    other=deepcopy(old);other.id='MLB:other';other.league='MLB'
    other_tip={**tip,'id':'other','league':'MLB','game_id':other.id}
    future=parse_landing(*observed(5),NOW);future.state='scheduled';future.start='2026-10-01T02:00:00Z'
    future_tip={**observed(5)[2],'id':'future','start':future.start}
    with store.db() as db:
        for i,t in enumerate((tip,other_tip,future_tip),1):
            body=json.dumps(t)
            db.execute('INSERT INTO bets(id,tip_id,created_at,stake,odds,platform,payload) VALUES(?,?,?,?,?,?,?)',(i,t['id'],t['start'],t['stake'],t['reference_odds'],'Stake.com',body))
            db.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload) VALUES(?,?,?,?)',('contract:'+t['id'],t['id'],t['start'],body))
        before=[tuple(r) for r in db.execute('SELECT id,payload,stake,odds FROM bets ORDER BY id')]
        original_decisions=[tuple(r) for r in db.execute('SELECT family,payload FROM system_decisions ORDER BY family')]
    monkeypatch.setattr(engine,'DATA',tmp_path);monkeypatch.setattr(engine,'utcnow',lambda:NOW)
    recover=nhl_results.recover_pending_nhl
    monkeypatch.setattr(nhl_results,'recover_pending_nhl',lambda games,bets,decisions,**kw:recover(games,bets,decisions,fetcher=Feed(),now=NOW,**kw))
    write=nhl_results.atomic_json
    def persist(path,value):
        if (failure_site=='attempt' and path.parent.name=='attempts') or (failure_site=='report' and path.name=='recovery.json') or (failure_site=='games' and path.name=='games.json'):
            raise PermissionError('injected persistence failure')
        return write(path,value)
    monkeypatch.setattr(nhl_results,'atomic_json',persist);monkeypatch.setattr(engine,'atomic_json',persist)
    worker=Engine.__new__(Engine);worker.store=store;worker.games=[old,other,future]
    worker.recover_nhl_results();worker.settle_bets();store.settle_system_decisions(worker.games)
    assert worker.nhl_settlement_status['state']=='failed'
    assert worker.nhl_settlement_status['unresolved_count']==1
    assert old.context['nhl_settlement_pending'] is True
    assert 'nhl_settlement_pending' not in other.context and 'nhl_settlement_pending' not in future.context
    with store.db() as db:
        assert [tuple(r) for r in db.execute('SELECT id,payload,stake,odds FROM bets ORDER BY id')]==before
        assert [tuple(r) for r in db.execute('SELECT family,payload FROM system_decisions ORDER BY family')]==original_decisions
        assert [(r['tip_id'],r['status']) for r in db.execute('SELECT tip_id,status FROM bets ORDER BY id')]==[(tip['id'],'open'),('other','won'),('future','open')]
        assert [(r['tip_id'],r['status']) for r in db.execute('SELECT tip_id,status FROM system_decisions ORDER BY rowid')]==[(tip['id'],'open'),('other','won'),('future','open')]
    # Retained raw evidence can recover without rewriting any original ticket.
    monkeypatch.setattr(nhl_results,'atomic_json',write);monkeypatch.setattr(engine,'atomic_json',write)
    worker.recover_nhl_results();worker.settle_bets();store.settle_system_decisions(worker.games)
    assert worker.nhl_settlement_status['unresolved_count']==0
    assert next(b for b in store.bets() if b['tip_id']==tip['id'])['status']=='won'
    assert next(d for d in store.system_decisions() if d['tip_id']==tip['id'])['status']=='won'
