import pytest
from betting_engine.decisions import *

def test_ev_and_push_contract():
    assert sportsbook_ev(.55,0,2)==pytest.approx(.1)
    assert sportsbook_ev(.5,.1,2)==pytest.approx(.1)
    assert minimum_odds(.5,.1)==pytest.approx(1.8)

def test_negative_edge_no_kelly_stake():
    s={**DEFAULT_SETTINGS,'bankroll':1000}
    assert stake_size(.4,0,2,s,10)[0]==0

def test_stake_respects_bankroll_and_remaining():
    s={**DEFAULT_SETTINGS,'bankroll':1000}
    assert stake_size(.75,0,2,s,2)[0]==2
    assert stake_size(.75,0,2,s,.8)[0]==0

def test_no_rounding_small_kelly_up_to_one():
    s={**DEFAULT_SETTINGS,'bankroll':20}
    assert stake_size(.6,0,2,s,10)[0]==0

def test_no_daily_cap_allows_more_than_fifteen_but_preserves_per_bet_and_edge_rules():
    rows=[{'id':str(i),'game_id':str(i),'market':'moneyline','stress_roi':.1,'probability':.7,'stress_probability':.65,'push_probability':0,'reference_odds':2,'minimum_odds':1.6} for i in range(20)]
    settings={**DEFAULT_SETTINGS,'unit_stake':5}
    chosen,_=rank_and_size(rows,settings,open_exposure=1000)
    assert len(chosen)==20 and sum(t['stake'] for t in chosen)==100
    assert stake_size(.4,0,2,settings,remaining_budget(settings,1000))[0]==0
    assert remaining_budget({**settings,'daily_risk_limit':10},17)==0

def test_selection_prefers_stressed_value_over_raw_win_probability():
    rows=[{'id':str(i),'game_id':'same','market':'moneyline','stress_roi':roi,'probability':p,'stress_probability':p-.03,'push_probability':0,'reference_odds':odds,'minimum_odds':1.1} for i,(roi,p,odds) in enumerate([(.04,.8,1.4),(.12,.6,2.)])]
    chosen,_=rank_and_size(rows,DEFAULT_SETTINGS)
    assert chosen[0]['id']=='1'

def test_market_duplicate_family_not_extra_recommendation():
    def row(id,line):return {'id':id,'game_id':'a','market':'spreads','rank_score':.1,'stress_roi':.1,'probability':.6,'stress_probability':.55,'push_probability':0,'reference_odds':2,'minimum_odds':1.9,'line':line}
    chosen,alt=rank_and_size([row('1',1.5),row('2',2.5)],DEFAULT_SETTINGS)
    assert len(chosen)==1

def test_every_selected_family_has_rank_and_explicit_nonallocation_reason():
    rows=[{'id':str(i),'game_id':'g','market':kind,'rank_score':.1,'stress_roi':.1,'probability':.6,'stress_probability':.55,'push_probability':0,'reference_odds':2,'minimum_odds':1.9} for i,kind in enumerate(['moneyline','spreads','totals'])]
    chosen,alternatives=rank_and_size(rows,{**DEFAULT_SETTINGS,'allow_event_additions':False,'max_per_event':2},held_families=[('g','moneyline'),('g','totals')])
    assert len(chosen)+len(alternatives)==3
    assert all(t['rank'] and t['allocation_reason'] and t['stake']==0 for t in chosen+alternatives)
    assert next(t for t in alternatives if t['market']=='totals')['allocation_code']=='already_held'

def test_stake_candidate_uses_refund_on_push_not_exchange_half_payout():
    from types import SimpleNamespace
    future=(utcnow()+timedelta(hours=2)).isoformat()
    g=SimpleNamespace(id='g',league='MLB',home='Home',away='Away',start=future,state='scheduled',context={},source='test')
    m=SimpleNamespace(kind='totals',side='under',line=8,platform='Stake.com',selection='Under 8',url='https://stake.com',id='m',description='Total (Incl. Extra Innings)')
    q=SimpleNamespace(fetched_at=utcnow().isoformat(),effective_cost=1/1.8,price=1/1.8,fee_per_share=0,minimum_notional=0)
    f=SimpleNamespace(market_probability=lambda *args:(.55,.1),disagreement=0,total=7.5,features={'home_rest':1,'away_rest':1},warnings=[],model_version='v',model_probabilities={},home_points=4,away_points=3.5)
    t=candidate(g,m,q,f,DEFAULT_SETTINGS)
    assert t['expected_roi']==pytest.approx(sportsbook_ev(.55,.1,1.8))
    assert t['stress_roi']==pytest.approx(sportsbook_ev(.49,.1,1.8))


def test_sport_cycle_limit_prevents_single_sport_monopoly():
    def make_cand(cid, gid, league):
        return {
            'id': cid, 'game_id': gid, 'league': league, 'market': 'moneyline',
            'side': 'home', 'rank_score': 0.15, 'stress_roi': 0.15,
            'probability': 0.65, 'stress_probability': 0.60, 'push_probability': 0,
            'reference_odds': 2.0, 'minimum_odds': 1.6, 'reference_minimum_notional': 1.0
        }
    # 4 ATP games + 2 NFL games with max_sport_per_cycle = 2
    candidates = [
        make_cand('atp1', 'g_atp1', 'ATP'),
        make_cand('atp2', 'g_atp2', 'ATP'),
        make_cand('atp3', 'g_atp3', 'ATP'),
        make_cand('atp4', 'g_atp4', 'ATP'),
        make_cand('nfl1', 'g_nfl1', 'NFL'),
        make_cand('nfl2', 'g_nfl2', 'NFL'),
    ]
    settings = {**DEFAULT_SETTINGS, 'max_sport_per_cycle': 2, 'max_new_funded_per_publication': 10}
    chosen, alternatives = rank_and_size(candidates, settings)
    funded = [c for c in chosen if c['stake'] > 0]
    funded_atp = [c for c in funded if c['league'] == 'ATP']
    funded_nfl = [c for c in funded if c['league'] == 'NFL']
    assert len(funded_atp) == 2, f"Expected 2 ATP picks due to sport cap, got {len(funded_atp)}"
    assert len(funded_nfl) == 2, f"Expected 2 NFL picks, got {len(funded_nfl)}"
    blocked_atp = [a for a in alternatives if a.get('allocation_code') == 'sport_cycle_limit']
    assert len(blocked_atp) == 2


def test_max_picks_per_family_limit():
    held_roster = [
        {'id': 't1', 'game_id': 'g_nfl', 'league': 'NFL', 'market': 'spreads', 'stake': 5.0, 'side': 'home', 'line': 3.5},
        {'id': 't2', 'game_id': 'g_nfl', 'league': 'NFL', 'market': 'spreads', 'stake': 3.5, 'side': 'home', 'line': 5.5},
    ]
    # 3rd candidate in same family (spreads) on g_nfl
    cand_spread3 = {
        'id': 'cand_sp3', 'game_id': 'g_nfl', 'league': 'NFL', 'market': 'spreads',
        'side': 'home', 'line': 7.5, 'rank_score': 0.20, 'stress_roi': 0.15,
        'probability': 0.65, 'stress_probability': 0.60, 'push_probability': 0,
        'reference_odds': 2.0, 'minimum_odds': 1.6, 'reference_minimum_notional': 1.0
    }
    # Distinct family (totals) candidate on g_nfl
    cand_total = {
        'id': 'cand_tot', 'game_id': 'g_nfl', 'league': 'NFL', 'market': 'totals',
        'side': 'over', 'line': 45.5, 'rank_score': 0.18, 'stress_roi': 0.12,
        'probability': 0.62, 'stress_probability': 0.58, 'push_probability': 0,
        'reference_odds': 1.95, 'minimum_odds': 1.6, 'reference_minimum_notional': 1.0
    }
    settings = {**DEFAULT_SETTINGS, 'max_picks_per_family': 2, 'max_new_funded_per_publication': 10}
    chosen, alternatives = rank_and_size([cand_spread3, cand_total], settings, held_event_roster=held_roster)
    
    # cand_spread3 must be blocked because 2 spreads are already held
    blocked = [a for a in alternatives if a['id'] == 'cand_sp3']
    assert len(blocked) == 1
    assert blocked[0]['allocation_code'] == 'event_family_limit'
    assert 'limit: 2' in blocked[0]['allocation_reason']
    
    # cand_total must be funded because totals has 0 held picks
    funded = [c for c in chosen if c['id'] == 'cand_tot' and c['stake'] > 0]
    assert len(funded) == 1


def test_contradictory_side_blocked_cross_family_moneyline_and_spreads():
    held_roster = [
        {'id': 't_held', 'game_id': 'g_ucl', 'league': 'UCL', 'market': 'spreads', 'stake': 2.0, 'side': 'away', 'line': 1.0}
    ]
    # New candidate is Atletico Madrid +0 (side=home, line=0.0) which maps to moneyline family
    cand_home = {
        'id': 'cand_home', 'game_id': 'g_ucl', 'league': 'UCL', 'market': 'spreads',
        'side': 'home', 'line': 0.0, 'rank_score': 0.20, 'stress_roi': 0.15,
        'probability': 0.65, 'stress_probability': 0.60, 'push_probability': 0,
        'reference_odds': 1.8, 'minimum_odds': 1.5, 'reference_minimum_notional': 1.0
    }
    chosen, alternatives = rank_and_size([cand_home], DEFAULT_SETTINGS, held_event_roster=held_roster)
    blocked = [a for a in alternatives if a['id'] == 'cand_home']
    assert len(blocked) == 1
    assert blocked[0]['allocation_code'] == 'contradictory_side_blocked'


def test_portfolio_shield_quarantine_allocation_code_preserved(monkeypatch):
    import betting_engine.capability_expansion
    monkeypatch.setattr(
        betting_engine.capability_expansion,
        'check_portfolio_shield',
        lambda c: {'action': 'quarantine', 'reason': 'Quarantined by active shield', 'shielded': True}
    )
    cand_nhl = {
        'id': 'cand_nhl_under', 'game_id': 'g_nhl_1', 'league': 'NHL', 'market': 'totals',
        'side': 'under', 'line': 5.5, 'rank_score': 0.25, 'stress_roi': 0.10,
        'probability': 0.60, 'stress_probability': 0.55, 'push_probability': 0,
        'reference_odds': 1.95, 'minimum_odds': 1.5, 'reference_minimum_notional': 1.0
    }
    chosen, alternatives = rank_and_size([cand_nhl], DEFAULT_SETTINGS)
    assert len(chosen) == 1
    assert chosen[0]['stake'] == 0.0
    assert chosen[0]['allocation_code'] == 'portfolio_shield_quarantined'
    assert 'Quarantined by active shield' in chosen[0]['allocation_reason']


def test_d40_uncertainty_ev_hurdle_and_volume_backstop(monkeypatch):
    import betting_engine.decisions
    # Mock governor status: no alpha, no defense for tennis ATP
    monkeypatch.setattr(
        betting_engine.decisions,
        '_get_governor_status',
        lambda: {'super_alpha_list': [], 'solid_alpha_list': [], 'capital_defense_list': ['NCAAF|full_game|spreads'], 'quarantine_list': []}
    )
    monkeypatch.setattr(
        betting_engine.decisions,
        '_get_demoted_segments',
        lambda: set()
    )
    settings = {
        **DEFAULT_SETTINGS,
        'recommendation_policy': 'continuous_model_v1',
        'family_selection_policy': 'continuous_probability_v4',
        'model_pick_sizing_policy': 'all_tiers_weighted_v3',
        'outcome_sizing_policy': 'legacy',
        'capital_defense': True,
    }
    
    cand_low_ev = {
        'id': 'cand_low_ev_1', 'game_id': 'g_test_1', 'league': 'ATP', 'market': 'moneyline',
        'side': 'home', 'line': 0.0, 'stress_roi': -0.01,
        'probability': 0.55, 'stress_probability': 0.50, 'push_probability': 0,
        'reference_odds': 1.84, 'minimum_odds': 1.80, 'expected_roi': 0.012, # 1.2% EV (sub-2.0% noise)
        'reference_minimum_notional': 1.0, 'model_scope': 'full_game'
    }

    # Case A: When daily_funded_count >= 100, low-EV candidate is filtered by uncertainty hurdle
    chosen_a, alts_a = rank_and_size([cand_low_ev], settings, daily_funded_count=105)
    assert len(chosen_a) == 0
    assert len(alts_a) >= 1
    assert alts_a[-1]['allocation_code'] == 'ev_hurdle_insufficient'
    assert 'Uncertainty Hurdle' in alts_a[-1]['allocation_reason']

    # Case B: When daily_funded_count < 100 (volume deficit), volume backstop activates and allows funding
    chosen_b, alts_b = rank_and_size([cand_low_ev], settings, daily_funded_count=50)
    assert len(chosen_b) == 1
    assert chosen_b[0]['allocation_code'] == 'funded'
    assert chosen_b[0]['stake'] >= 1.0

    # Case C: Capital Defense segment strictly enforces 3.5% EV hurdle even in volume deficit
    cand_ncaaf = {
        'id': 'cand_ncaaf_1', 'game_id': 'g_test_2', 'league': 'NCAAF', 'market': 'spreads',
        'side': 'home', 'line': -3.5, 'stress_roi': -0.01,
        'probability': 0.53, 'stress_probability': 0.50, 'push_probability': 0,
        'reference_odds': 1.95, 'minimum_odds': 1.88, 'expected_roi': 0.0335, # 3.35% EV (< 3.5% hurdle)
        'reference_minimum_notional': 1.0, 'model_scope': 'full_game'
    }
    chosen_c, alts_c = rank_and_size([cand_ncaaf], settings, daily_funded_count=50)
    assert len(chosen_c) == 0
    assert len(alts_c) >= 1
    assert alts_c[-1]['allocation_code'] == 'ev_hurdle_insufficient'
    assert 'Capital Defense Hurdle' in alts_c[-1]['allocation_reason']

    # Case D: Capital Defense segment with >= 3.5% EV is funded but strictly clamped to $0.50 tourniquet
    cand_ncaaf_high_ev = {
        'id': 'cand_ncaaf_2', 'game_id': 'g_test_3', 'league': 'NCAAF', 'market': 'spreads',
        'side': 'home', 'line': -3.5, 'stress_roi': 0.05,
        'probability': 0.58, 'stress_probability': 0.55, 'push_probability': 0,
        'reference_odds': 1.95, 'minimum_odds': 1.75, 'expected_roi': 0.08, # 8.0% EV (> 3.5% hurdle)
        'reference_minimum_notional': 1.0, 'model_scope': 'full_game',
        'model_version': 'NCAAF-spreads-graduated-v1' # Model has 'graduated' in name
    }
    chosen_d, alts_d = rank_and_size([cand_ncaaf_high_ev], settings, daily_funded_count=50)
    assert len(chosen_d) == 1
    assert chosen_d[0]['allocation_code'] == 'funded'
    assert chosen_d[0]['stake'] == 0.50, f"Expected $0.50 clamp, got {chosen_d[0]['stake']}"
    assert 'Capped at $0.50 emergency capital defense tourniquet' in chosen_d[0]['stake_note']

    # Case E: Demoted derivative market is quarantined to $0.00
    monkeypatch.setattr(
        betting_engine.decisions,
        '_get_demoted_segments',
        lambda: {'ATP|period_2|period_2:moneyline'}
    )
    cand_demoted_deriv = {
        'id': 'cand_demoted_1', 'game_id': 'g_test_4', 'league': 'ATP', 'market': 'period_2:moneyline',
        'side': 'home', 'line': 0.0, 'stress_roi': 0.05,
        'probability': 0.60, 'stress_probability': 0.55, 'push_probability': 0,
        'reference_odds': 1.90, 'minimum_odds': 1.70, 'expected_roi': 0.06,
        'reference_minimum_notional': 1.0, 'model_scope': 'period_2',
        'model_version': 'ATP-p2-graduated-v1'
    }
    chosen_e, alts_e = rank_and_size([cand_demoted_deriv], settings, daily_funded_count=50)
    assert len(chosen_e) == 0
    assert len(alts_e) >= 1
    assert alts_e[-1]['allocation_code'] == 'capital_defense_quarantined'
    assert alts_e[-1]['stake'] == 0.0

    # Case F: Demoted core market (e.g. NFL spreads) is also strictly quarantined to $0.00 until re-graduation
    monkeypatch.setattr(
        betting_engine.decisions,
        '_get_demoted_segments',
        lambda: {'NFL|full_game|spreads'}
    )
    cand_demoted_core = {
        'id': 'cand_demoted_core_1', 'game_id': 'g_test_5', 'league': 'NFL', 'market': 'spreads',
        'side': 'home', 'line': -3.5, 'stress_roi': 0.08,
        'probability': 0.58, 'stress_probability': 0.55, 'push_probability': 0,
        'reference_odds': 1.95, 'minimum_odds': 1.75, 'expected_roi': 0.08,
        'reference_minimum_notional': 1.0, 'model_scope': 'full_game',
        'model_version': 'NFL-spreads-v1'
    }
    chosen_f, alts_f = rank_and_size([cand_demoted_core], settings, daily_funded_count=50)
    assert len(chosen_f) == 0
    assert len(alts_f) >= 1
    assert alts_f[-1]['allocation_code'] == 'capital_defense_quarantined'
    assert alts_f[-1]['stake'] == 0.0
    assert 'Market performance degraded' in alts_f[-1]['allocation_reason']




