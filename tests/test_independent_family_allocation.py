"""Independent real-guard allocation probes; only temporary databases."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from betting_engine.contracts import equivalent_contract_ids
from betting_engine.governance import verify_publication
from betting_engine.domain import utcnow
from betting_engine.instructions import instruction_review
from betting_engine.instruction_refill import ready_snapshot
import test_continuous_delivery as cases
from test_model_family_allocations import reviewed, rank, publish

@pytest.mark.parametrize("direction", ["winner_first", "spread_first"])
def test_prior_alias_is_not_a_new_issuance(reviewed, tmp_path, direction):
    winner, settings, check = reviewed(cases.BEST)
    spread, _, other = reviewed(cases.BEST, market_kind='spreads', side='home', line=-.5, start=winner['start'])
    assert check['passed'] and other['passed']
    assert winner['id'] in equivalent_contract_ids(spread)
    original, alias = (winner, spread) if direction == 'winner_first' else (spread, winner)
    store = cases.database(tmp_path, settings)
    publish(store, [original])
    before = deepcopy((store.system_decisions(), store.bets(), store.daily_progress()))
    with pytest.raises(ValueError, match='equivalent|alias|issued'):
        publish(store, [alias])
    assert (store.system_decisions(), store.bets(), store.daily_progress()) == before


def test_same_publication_duplicate_alias_rejected(reviewed, tmp_path):
    winner, settings, _ = reviewed(cases.BEST)
    spread, _, _ = reviewed(cases.BEST, market_kind='spreads', side='home', line=-.5, start=winner['start'])
    store = cases.database(tmp_path, settings)
    with pytest.raises(ValueError, match='duplicate equivalent'):
        publish(store, [winner, spread])
    assert not store.system_decisions() and not store.bets()


def test_parallel_stale_distinct_family_issuance_serializes(reviewed, tmp_path):
    winner, settings, _ = reviewed(cases.BEST)
    total, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5, start=winner['start'])
    store = cases.database(tmp_path, settings)
    barrier = Barrier(2)
    def attempt(tip):
        barrier.wait()
        try:
            publish(store,[tip])
            return 'committed'
        except ValueError as exc:
            assert 'related-game exposure' in str(exc)
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [winner, total]))
    assert sorted(outcomes) == ['committed', 'stale']
    assert len(store.system_decisions()) == len(store.bets()) == 1


def test_settled_old_family_still_consumes_lifetime_slot(reviewed, tmp_path):
    old, settings, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5)
    new, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=216.5, start=old['start'])
    store = cases.database(tmp_path, settings)
    publish(store, [old])
    store.settle(store.bets()[0]['id'], 'lost', -old['stake'])
    before = deepcopy((store.bets(), store.system_decisions()))
    rank(store,[new],settings)
    assert new['stake'] == 0 and new['allocation_code'] == 'model_pick_family_covered'
    assert (store.bets(), store.system_decisions()) == before


def test_distinct_additions_preserve_exact_original_stakes(reviewed, tmp_path):
    old, settings, _ = reviewed(cases.BEST)
    total, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5, start=old['start'])
    store = cases.database(tmp_path, settings)
    publish(store,[old])
    original = deepcopy(store.system_decisions()[0]); ticket = deepcopy(store.bets()[0])
    rank(store,[total],settings); publish(store,[total])
    assert original in store.system_decisions() and ticket in store.bets()
    refreshed, _, _ = reviewed(cases.BEST, start=old['start'], held=[old['id']])
    review = instruction_review(ticket, refreshed, utcnow(), settings['minimum_roi'], sizing_settings=settings)
    assert review['actionable'] and review['stake'] == ticket['stake']


def test_conflicting_duplicate_views_cannot_hide_funded_same_family(reviewed, tmp_path):
    old, settings, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=215.5)
    new, _, _ = reviewed(cases.BEST, market_kind='totals', side='over', line=216.5, start=old['start'])
    store=cases.database(tmp_path,settings)
    publish(store,[old])
    unfunded=deepcopy(new); unfunded['stake']=0.; unfunded.pop('stake_sizing',None)
    verify_publication(unfunded,utcnow())
    before=deepcopy((store.system_decisions(),store.bets()))
    with pytest.raises(ValueError):
        store.publish({'recommendations':[new], 'evaluated_candidates':[unfunded]}, require_guard=True)
    assert (store.system_decisions(),store.bets())==before


from test_decision_readiness import inputs as verified_mlb_inputs

def test_separately_guarded_settlement_scopes_get_separate_families(verified_mlb_inputs, tmp_path):
    from betting_engine.governance import EvidenceGuard, KnowledgeAgent
    from betting_engine.decisions import candidate
    from betting_engine.models import feature_names
    _, _, market, quote, forecast, report, settings = cases.guard_case()
    data_dir, _, game, verified, *_ = verified_mlb_inputs
    forecast.game_id = market.game_id = game.id
    forecast.features = verified.features
    forecast.market_probability = lambda *args: (.53, 0.)
    forecast.margin = 10.
    report.update(league='MLB', features=list(feature_names('MLB')))
    settings.update(recommendation_policy=cases.BEST, model_pick_sizing_policy='all_tiers_weighted_v3',
                    model_pick_allocation_policy='distinct_family_v1')
    market.url = 'https://stake.com/sports/baseball/usa/mlb/exact-fixture'
    guard = EvidenceGuard(KnowledgeAgent(), data_dir=data_dir)
    tips = []
    for scope, group in [('full_game','main'), ('inning_1','innings'), ('innings_1_5','innings 1 to 5')]:
        market.kind = 'moneyline' if scope == 'full_game' else scope + ':moneyline'
        market.metadata['stake_market_groups'] = [{'id':scope,'name':group}]
        forecast.scope = scope
        report.update(scope=scope, promotion_gate={'promote':True}, validated_markets=['moneyline'])
        tip=candidate(game,market,quote,forecast,settings)
        tip['stake_evidence']={'fixture_slug':'exact-fixture','market_name':market.description,
            'outcome_name':market.selection,'decimal_odds':2.,'fetched_at':quote.fetched_at,
            'market_groups':deepcopy(market.metadata['stake_market_groups'])}
        check=guard.check(tip,game,market,quote,forecast,report,settings)
        assert check['passed'], check
        tips.append(tip)
    store=cases.database(tmp_path,settings)
    selected,_=rank(store,tips,settings)
    assert len(selected)==3 and all(t['stake']>=1 for t in selected)
    assert sorted(t['stake_sizing']['related_contracts'] for t in selected)==[2,2,2]
    publish(store,selected)
    assert store.held_allocation_families()=={(game.id,'moneyline'),(game.id,'inning_1:moneyline'),(game.id,'innings_1_5:moneyline')}
    assert len(store.system_decisions())==3


@pytest.mark.parametrize('field', ['model_pick_allocation_policy','recommendation_policy','model_pick_sizing_policy'])
def test_policy_drift_at_commit_does_not_create_records(reviewed, tmp_path, field):
    tip,settings,_=reviewed(cases.BEST)
    store=cases.database(tmp_path,settings)
    values={'model_pick_allocation_policy':'one_per_game_v1', 'recommendation_policy':'strict', 'model_pick_sizing_policy':'unit_cap_v1'}
    store.save_settings({field:values[field]})
    with pytest.raises(ValueError,match='policy'):
        publish(store,[tip])
    assert not store.system_decisions() and not store.bets() and store.latest() is None


def test_exact_legacy_original_survives_new_policy_revalidation(monkeypatch, reviewed, tmp_path):
    original_guard_case=cases.guard_case
    def legacy():
        parts=original_guard_case(); parts[-1].pop('model_pick_allocation_policy',None)
        return parts
    with monkeypatch.context() as patch:
        patch.setattr(cases,'guard_case',legacy)
        old,old_settings,_=cases.reviewed(cases.BEST)
    store=cases.database(tmp_path,old_settings)
    publish(store,[old])
    frozen=deepcopy(store.system_decisions()); record=deepcopy(store.bets()[0])
    current,settings,_=reviewed(cases.BEST,start=old['start'],held=[old['id']])
    store.save_settings({'model_pick_allocation_policy':'distinct_family_v1'})
    review=instruction_review(record,current,utcnow(),settings['minimum_roi'],sizing_settings=settings)
    assert review['actionable'] and review['stake']==record['stake']
    assert store.system_decisions()==frozen and store.bets()[0]==record


def test_same_batch_pending_instructions_remain_executable(reviewed,monkeypatch,tmp_path):
    original=cases.guard_case
    def two_dollar_units():
        parts=original();parts[-1]['unit_stake']=2.
        return parts
    monkeypatch.setattr(cases,'guard_case',two_dollar_units)
    winner,settings,_=reviewed(cases.BEST)
    total,_,_=reviewed(cases.BEST,market_kind='totals',side='over',line=215.5,start=winner['start'])
    home,_,check=reviewed(cases.BEST,market_kind='home_total',side='over',line=105.5,start=winner['start'])
    assert check['passed']
    store=cases.database(tmp_path,settings)
    tips,_=rank(store,[winner,total,home],settings)
    assert len(tips)==3 and all(t['stake']>=1 for t in tips)
    assert max(t['stake'] for t in tips)>1
    publish(store,tips)
    before=deepcopy((store.bets(),store.system_decisions()))
    current={t['id']:t for t in tips}; counts=store.held_event_counts()
    reviews=[instruction_review(r,current[r['tip_id']],utcnow(),settings['minimum_roi'],
        counts[r['payload']['game_id']]-1,settings) for r in store.open_bets()]
    assert all(r['actionable'] for r in reviews), {'stakes':[t['stake'] for t in tips], 'reviews':reviews}
    assert set(ready_snapshot(store)['ids'])==set(current)
    assert (store.bets(),store.system_decisions())==before


def test_bankroll_bound_final_roster_removes_weak_choice_without_rotation(reviewed, monkeypatch, tmp_path):
    original=cases.guard_case
    def bounded():
        parts=original();parts[-1].update(bankroll=140.,max_bankroll_fraction=.02)
        return parts
    monkeypatch.setattr(cases,'guard_case',bounded)
    weak,settings,_=reviewed(cases.BEST,probability=.53)
    home,_,a=reviewed(cases.BEST,probability=.57,market_kind='home_total',side='over',line=105.5,start=weak['start'])
    away,_,b=reviewed(cases.BEST,probability=.57,market_kind='away_total',side='over',line=105.5,start=weak['start'])
    inferior,_,c=reviewed(cases.BEST,probability=.52,side='away',start=weak['start'])
    assert a['passed'] and b['passed'] and c['passed']
    store=cases.database(tmp_path,settings)
    selected,_=rank(store,[weak,home,away,inferior],settings)
    assert weak['stake']==0
    assert inferior['stake']==0 and inferior['id'] not in {t['id'] for t in selected}
    funded=[t for t in selected if t['stake']>0]
    assert {t['id'] for t in funded}=={home['id'],away['id']}
    assert {t['stake_sizing']['related_contracts'] for t in funded}=={1}
    assert {t['stake'] for t in funded}=={1.73}
    publish(store,selected)
    assert len(store.bets())==len(store.system_decisions())==2
    assert set(ready_snapshot(store)['ids'])=={t['id'] for t in funded}


def test_new_batch_racing_with_prior_allocation_rejects_atomically(reviewed,tmp_path):
    first,settings,_=reviewed(cases.BEST)
    total,_,_=reviewed(cases.BEST,market_kind='totals',side='over',line=215.5,start=first['start'])
    home,_,_=reviewed(cases.BEST,market_kind='home_total',side='over',line=105.5,start=first['start'])
    store=cases.database(tmp_path,settings)
    batch,_=rank(store,[total,home],settings)
    publish(store,[first])
    before=deepcopy((store.bets(),store.system_decisions()))
    import pytest
    with pytest.raises(ValueError,match='exposure|remaining'):
        publish(store,batch)
    assert (store.bets(),store.system_decisions())==before


def test_new_allocation_setting_remains_compatible_with_strict_policy(reviewed,tmp_path):
    tip,settings,guard=reviewed('strict',probability=.72)
    assert guard['passed'] and tip['stake']>0
    store=cases.database(tmp_path,settings)
    publish(store,[tip])
    assert len(store.system_decisions())==1

def test_stress_value_receipt_stays_actionable_under_new_allocation_mode(reviewed,tmp_path):
    from betting_engine.instructions import instruction_review
    from betting_engine.domain import utcnow
    tip,settings,guard=reviewed(cases.BEST,probability=.72)
    assert guard['passed'] and tip['funding_basis']['tier']=='stress_value'
    store=cases.database(tmp_path,settings);publish(store,[tip])
    record=store.bets()[0]
    result=instruction_review(record,tip,utcnow(),settings['minimum_roi'],sizing_settings=settings)
    assert result['actionable'],result



def test_explicit_allocation_seals_basis_for_legacy_strict_sizing(reviewed,monkeypatch,tmp_path):
    from betting_engine.governance import evidence_digest
    from betting_engine.instructions import current_execution_review
    original=cases.guard_case
    def unit_capped():
        parts=original();parts[-1]['model_pick_sizing_policy']='unit_cap_v1'
        return parts
    monkeypatch.setattr(cases,'guard_case',unit_capped)
    tip,settings,guard=reviewed('strict',probability=.72)
    assert guard['passed'] and tip['funding_basis']['tier']=='stress_value'
    assert tip['funding_basis']['model_pick_allocation_policy']=='distinct_family_v1'
    assert current_execution_review(tip,utcnow(),settings['minimum_roi'],settings)['actionable']
    store=cases.database(tmp_path,settings);publish(store,[tip])
    result=instruction_review(store.bets()[0],tip,utcnow(),settings['minimum_roi'],sizing_settings=settings)
    assert result['actionable'] and result['stake']==tip['stake']
    missing=deepcopy(tip);missing.pop('funding_basis')
    missing['decision_guard']['candidate_sha256']=evidence_digest(missing)
    with pytest.raises(ValueError,match='funding policy evidence changed or missing'):
        verify_publication(missing,utcnow())
