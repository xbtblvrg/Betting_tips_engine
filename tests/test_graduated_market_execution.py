"""Comprehensive tests verifying the closed loop from shadow graduation to live bet issuance."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import numpy as np

from betting_engine.domain import Game, utcnow
from betting_engine.markets import Market, Quote
from betting_engine.decisions import candidate, rank_and_size
from betting_engine.governance import KnowledgeAgent, EvidenceGuard
from betting_engine.graduated_models import (
    GraduatedTennisSetModel,
    GraduatedSoccerHalfModel,
    resolve_graduated_segment_models,
)


def test_graduated_tennis_set_model_mathematics():
    """Verifies that GraduatedTennisSetModel produces exact deterministic set distributions."""
    model = GraduatedTennisSetModel(league='ATP', scope='period_1')
    assert model.report['scope'] == 'period_1'
    assert model.report['league'] == 'ATP'
    assert model.report['promotion_gate']['promote'] is True
    assert model.report['odds_used'] is False
    assert 'moneyline' in model.report['validated_markets']
    assert 'totals' in model.report['validated_markets']
    assert 'spreads' in model.report['validated_markets']

    game = Game(
        id='ATP:test_1', league='ATP', start='2026-10-15T12:00:00Z',
        home='Jannik Sinner', away='Carlos Alcaraz', home_id='SIN', away_id='ALC',
        home_abbr='SIN', away_abbr='ALC',
        state='scheduled', game_type='regular', season=2026,
        context={'surface': 'hard', 'rating_diff': 15.0}
    )

    fc = model.forecast(game)
    assert fc.game_id == 'ATP:test_1'
    assert fc.scope == 'period_1'
    assert 0.40 <= fc.p_a_wins_set <= 0.70
    assert 8.5 < fc.total < 11.5

    # 1. Moneyline
    p_h, push_h = fc.market_probability('period_1:moneyline', 'home')
    p_a, push_a = fc.market_probability('period_1:moneyline', 'away')
    assert 0.01 <= p_h <= 0.99
    assert 0.01 <= p_a <= 0.99
    assert abs(p_h + p_a - 1.0) < 1e-4
    assert push_h == 0.0 and push_a == 0.0

    # 2. Total games (9.5)
    p_over, push_o = fc.market_probability('period_1:totals', 'over', 9.5)
    p_under, push_u = fc.market_probability('period_1:totals', 'under', 9.5)
    assert 0.01 <= p_over <= 0.99
    assert 0.01 <= p_under <= 0.99
    assert abs(p_over + p_under - 1.0) < 1e-4
    assert push_o == 0.0

    # 3. Spreads (-1.5)
    p_cov, push_cov = fc.market_probability('period_1:spreads', 'home', -1.5)
    assert 0.01 <= p_cov <= 0.99
    assert push_cov == 0.0


def test_graduated_soccer_half_model_mathematics():
    """Verifies that GraduatedSoccerHalfModel produces exact bivariate Poisson half distributions."""
    model = GraduatedSoccerHalfModel(league='MLS', scope='half_1')
    assert model.report['scope'] == 'half_1'
    assert model.report['league'] == 'MLS'
    assert model.report['promotion_gate']['promote'] is True
    assert model.report['odds_used'] is False

    game = Game(
        id='MLS:test_101', league='MLS', start='2026-10-15T20:00:00Z',
        home='Inter Miami', away='LA Galaxy', home_id='MIA', away_id='LAG',
        home_abbr='MIA', away_abbr='LAG',
        state='scheduled', game_type='regular', season=2026,
        context={'rating_diff': 20.0}
    )

    fc = model.forecast(game)
    assert fc.game_id == 'MLS:test_101'
    assert fc.scope == 'half_1'
    assert 0.8 < fc.total < 1.8

    # Three-way
    p_h, _ = fc.market_probability('half_1:three_way', 'home')
    p_d, _ = fc.market_probability('half_1:three_way', 'draw')
    p_a, _ = fc.market_probability('half_1:three_way', 'away')
    assert abs(p_h + p_d + p_a - 1.0) < 1e-4

    # Both teams to score
    p_btts, _ = fc.market_probability('half_1:both_score', 'yes')
    assert 0.10 <= p_btts <= 0.50

    # Asian quarter totals (1.25)
    p_ov, push_ov = fc.market_probability('half_1:totals', 'over', 1.25)
    assert 0.01 <= p_ov <= 0.99
    assert push_ov >= 0.0


def test_resolve_graduated_segment_models():
    """Verifies that resolve_graduated_segment_models attaches new models and updates existing ones."""
    graduated_dict = {
        'ATP|period_1|tennis_sets': {
            'league': 'ATP', 'scope': 'period_1', 'family': 'tennis_sets',
            'graduated_at': '2026-10-12T10:00:00Z',
            'stats': {'sample_size': 45, 'brier_model': 0.165, 'brier_market': 0.205}
        },
        'MLS|half_1|asian_lines': {
            'league': 'MLS', 'scope': 'half_1', 'family': 'asian_lines',
            'graduated_at': '2026-10-12T10:00:00Z',
            'stats': {'sample_size': 50, 'brier_model': 0.180, 'brier_market': 0.220}
        }
    }

    existing = {}
    resolved = resolve_graduated_segment_models(graduated_dict, existing)

    assert ('ATP', 'period_1') in resolved
    assert isinstance(resolved[('ATP', 'period_1')], GraduatedTennisSetModel)
    assert ('MLS', 'half_1') in resolved
    assert isinstance(resolved[('MLS', 'half_1')], GraduatedSoccerHalfModel)


def test_end_to_end_graduated_tennis_candidate_and_evidence_guard(monkeypatch):
    """Verifies that a graduated ATP period_1 market passes candidate generation and EvidenceGuard 100%."""
    import betting_engine.decisions
    monkeypatch.setattr(betting_engine.decisions, '_get_demoted_segments', lambda: set())
    now_iso = utcnow().isoformat()
    from datetime import timedelta
    from betting_engine.domain import parse_time
    start_time = (parse_time(now_iso) + timedelta(hours=6)).isoformat()
    game = Game(
        id='ATP:live_game_1', league='ATP', start=start_time,
        home='Novak Djokovic', away='Daniil Medvedev', home_id='DJO', away_id='MED',
        home_abbr='DJO', away_abbr='MED',
        state='scheduled', game_type='regular', season=2026,
        source='https://stake.com/sports/tennis/atp/djokovic-medvedev',
        fetched_at=now_iso,
        context={'surface': 'hard', 'rating_diff': 250.0}
    )

    model = GraduatedTennisSetModel(league='ATP', scope='period_1')
    forecast = model.forecast(game)

    market = Market(
        id='stake:tennis:set1_win',
        game_id=game.id,
        league=game.league,
        kind='period_1:moneyline',
        side='home',
        line=None,
        selection='Novak Djokovic',
        platform='Stake.com',
        url=game.source,
        token='tok_1',
        condition_id='cond_1',
        description='1st Set - Winner',
        fee_rate=0.0,
        fee_exponent=0.0,
        minimum_notional=1.0,
        market_start=game.start,
        fetched_at=now_iso,
        metadata={
            'availability_verified': True,
            'fixture_slug': 'djokovic-medvedev',
            'league_path': 'tennis/atp',
            'api_source': 'stake_public_odds',
            'stake_market_name': '1st Set - Winner',
            'stake_market_groups': [{'id': 'sets', 'name': 'Sets'}],
            'stake_outcome_name': 'Novak Djokovic',
            'stake_odds': 1.85,
            'matching_basis': 'exact_event_and_teams',
            'fixture_name': 'Djokovic - Medvedev',
            'matched_teams': ['Novak Djokovic', 'Daniil Medvedev'],
            'is_graduated': True,
        }
    )

    quote = Quote(
        market_id=market.id,
        token='tok_1',
        price=1.85,
        fee_per_share=0.0,
        effective_cost=1.0 / 1.85,
        available_shares=100.0,
        minimum_notional=1.0,
        fetched_at=now_iso,
        source_timestamp=now_iso,
        status='current'
    )

    from betting_engine.storage import Store
    settings = Store().settings()
    settings['market_anchor_mode'] = 'shadow'
    settings['crucible_rules_mode'] = 'shadow'
    settings['minimum_roi'] = 0.02

    # 1. Candidate pricing
    c = candidate(
        g=game,
        m=market,
        q=quote,
        forecast=forecast,
        settings=settings,
        model_trained_at=model.report['trained_at']
    )
    assert c is not None
    assert c['id'] is not None
    assert c['model_scope'] == 'period_1'
    assert c['probability'] > 0.01

    # 2. EvidenceGuard verification
    knowledge = KnowledgeAgent()
    guard = EvidenceGuard(knowledge)

    c['stake_evidence'] = {
        'fixture_slug': market.metadata['fixture_slug'],
        'league_path': market.metadata['league_path'],
        'api_source': market.metadata['api_source'],
        'market_name': market.metadata['stake_market_name'],
        'market_groups': market.metadata['stake_market_groups'],
        'outcome_name': market.metadata['stake_outcome_name'],
        'decimal_odds': market.metadata['stake_odds'],
        'fetched_at': market.fetched_at,
        'matching_basis': market.metadata['matching_basis'],
        'fixture_name': market.metadata['fixture_name'],
        'matched_teams': market.metadata['matched_teams'],
    }

    from betting_engine.outcome_sizing import build_snapshot as outcome_snapshot, summary_for as outcome_summary, segment_key
    outcome_evidence = outcome_snapshot([], parse_time(now_iso))
    key = segment_key(c)
    summary_ev = outcome_summary(outcome_evidence, c)
    c['outcome_sizing_evidence'] = summary_ev

    check = guard.check(
        tip=c,
        game=game,
        market=market,
        quote=quote,
        forecast=forecast,
        model_report=model.report,
        settings=settings,
        trusted_outcome_summary=summary_ev
    )

    assert check['passed'] is True, f"Guard rejected candidate with errors: {check['errors']}"
    assert check['errors'] == []

    # 3. Dynamic sizing and ranking
    chosen, alternatives = rank_and_size(
        candidates=[c],
        settings=settings,
        held_families=(),
        held_contracts=(),
        held_event_roster=[],
    )
    assert len(chosen) >= 1
    selected = chosen[0]
    assert 0.50 <= selected['stake'] <= 8.0  # Must be funded ($0.50 probation floor to $8.00)
    assert selected['allocation_code'] in ('selected', 'funded')


def test_universal_graduated_model_execution():
    """Verifies that resolve_graduated_segment_models mounts GraduatedUniversalSegmentModel
    for any league and scope, and produces compliant forecasts."""
    graduated_meta = {
        'segment_key': 'NCAAM|half_1|asian_lines',
        'league': 'NCAAM',
        'scope': 'half_1',
        'family': 'asian_lines',
        'sub_family': 'asian_handicap',
        'graduated_at': '2026-10-14T12:00:00Z',
        'sample_size': 35,
        'roi': 0.065,
        'delta_brier': 0.005,
        'win_rate': 0.60,
        'stats': {'sample_size': 35, 'raw_market_names': ['1st Half - Asian Handicap']}
    }

    models = resolve_graduated_segment_models({'NCAAM|half_1|asian_lines': graduated_meta}, {})
    assert ('NCAAM', 'half_1') in models
    model = models[('NCAAM', 'half_1')]
    assert model.report['promotion_gate']['promote'] is True

    game = Game(
        id='NCAAM:101', league='NCAAM', start='2026-10-18T15:30:00Z',
        home='Duke Blue Devils', away='North Carolina Tar Heels', home_id='DUKE', away_id='UNC',
        home_abbr='DUKE', away_abbr='UNC',
        state='scheduled', game_type='regular', season=2026
    )

    fc = model.forecast(game)
    assert fc.scope == 'half_1'
    p, push = fc.market_probability('half_1:spreads', 'home', -0.5)
    assert 0.01 <= p <= 0.99


def test_dynamic_raw_name_stake_sportsbook_resolution(tmp_path, monkeypatch):
    """Verifies that StakeSportsbook dynamically parses any raw Stake market name
    saved in graduated_markets.json for both full_game and scoped derivatives."""
    from betting_engine.markets import StakeSportsbook

    grad_file = tmp_path / 'graduated_markets.json'
    grad_data = {
        'NCAAM|half_1|asian_lines': {
            'league': 'NCAAM', 'scope': 'half_1', 'family': 'asian_lines',
            'stats': {'raw_market_names': ['1st Half - Asian Handicap']}
        },
        'EPL|full_game|asian_lines': {
            'league': 'EPL', 'scope': 'full_game', 'family': 'asian_lines',
            'stats': {'raw_market_names': ['Alternative Asian Handicap']}
        }
    }
    grad_file.write_text(json.dumps(grad_data))
    monkeypatch.setattr('betting_engine.shadow_market_incubator.DATA', tmp_path)
    monkeypatch.setattr('betting_engine.markets.DATA', tmp_path)

    game = Game(
        id='NCAAM:202', league='NCAAM', start='2026-10-18T18:00:00Z',
        home='Kansas Jayhawks', away='Baylor Bears', home_id='KAN', away_id='BAY',
        home_abbr='KAN', away_abbr='BAY',
        state='scheduled', game_type='regular', season=2026
    )

    sb = StakeSportsbook(
        segment_markets={('NCAAM', 'half_1'): ['spreads']},
        full_markets={'EPL': ['spreads']}
    )

    # Scoped raw market parse
    fixture = {'id': 'ncaam_fix_1', 'name': 'Kansas Jayhawks - Baylor Bears', 'data': {'slug': 'kansas-baylor'}}
    payload = {
        'fixture': fixture,
        'groups': [
            {
                'id': 'g1',
                'name': 'Half',
                'markets': [
                    [
                        {
                            'id': 'mkt_ncaam_ah_1',
                            'name': '1st Half - Asian Handicap',
                            'status': 'active',
                            'extId': 'ncaam_ah_ext',
                            'specifiers': 'halfnr=1|hcp=-2.5',
                            'outcomes': [
                                {'id': 'out_h', 'name': 'Kansas Jayhawks (-2.5)', 'odds': 1.95, 'active': True},
                                {'id': 'out_a', 'name': 'Baylor Bears (+2.5)', 'odds': 1.88, 'active': True}
                            ]
                        }
                    ]
                ]
            }
        ]
    }

    parsed = sb._markets_for_fixture(payload, fixture, game, 'NCAAM', '2026-10-14T19:00:00Z')
    assert len(parsed) >= 1
    m = parsed[0]
    assert m.kind == 'half_1:spreads'
    assert m.line == -2.5
    assert m.metadata['stake_market_name'] == '1st Half - Asian Handicap'

    # Full game raw market parse
    game_epl = Game(
        id='EPL:303', league='EPL', start='2026-10-19T14:00:00Z',
        home='Arsenal', away='Chelsea', home_id='ARS', away_id='CHE',
        home_abbr='ARS', away_abbr='CHE',
        state='scheduled', game_type='regular', season=2026
    )
    fixture_epl = {'id': 'epl_fix_1', 'name': 'Arsenal - Chelsea', 'data': {'slug': 'arsenal-chelsea'}}
    payload_epl = {
        'fixture': fixture_epl,
        'groups': [
            {
                'id': 'g2',
                'name': 'Asian Lines',
                'markets': [
                    [
                        {
                            'id': 'mkt_epl_ah_1',
                            'name': 'Alternative Asian Handicap',
                            'status': 'active',
                            'extId': 'epl_ah_ext',
                            'specifiers': 'hcp=-1.5',
                            'outcomes': [
                                {'id': 'out_ars', 'name': 'Arsenal (-1.5)', 'odds': 2.10, 'active': True},
                                {'id': 'out_che', 'name': 'Chelsea (+1.5)', 'odds': 1.75, 'active': True}
                            ]
                        }
                    ]
                ]
            }
        ]
    }
    parsed_epl = sb._markets_for_fixture(payload_epl, fixture_epl, game_epl, 'EPL', '2026-10-14T19:00:00Z')
    assert len(parsed_epl) >= 1
    m_epl = parsed_epl[0]
    assert m_epl.kind == 'spreads'
    assert m_epl.line == -1.5
    assert m_epl.metadata['stake_market_name'] == 'Alternative Asian Handicap'


def test_strict_graduation_and_capital_defense_hard_gate(monkeypatch):
    """Verifies that:
    1. Capital Defense segments that are ungraduated are strictly quarantined to $0.00.
    2. Unproven, ungraduated candidates are routed to incubator at $0.00.
    3. Graduated candidates receive funded live issuance.
    """
    import betting_engine.decisions
    monkeypatch.setattr(betting_engine.decisions, "_get_demoted_segments", lambda *a, **k: {'NHL|full_game|totals'})
    from betting_engine.decisions import DEFAULT_SETTINGS
    settings = {
        **DEFAULT_SETTINGS,
        'family_selection_policy': 'continuous_probability_v4',
        'capital_defense': True,
        'unit_stake': 1.0,
        'is_test': False
    }

    # 1. Unproven candidate on non-graduated market
    cand_unproven = {
        'id': 'real_live_cand_unproven_1',
        'game_id': 'real_match_101',
        'league': 'VOLLEYBALL',
        'model_scope': 'full_game',
        'market': 'spreads',
        'stress_roi': 0.05,
        'expected_roi': 0.05,
        'probability': 0.65,
        'stress_probability': 0.60,
        'push_probability': 0.0,
        'reference_odds': 2.0,
        'minimum_odds': 1.5,
        'fair_odds': 1.6
    }

    # 2. Graduated candidate on NBA spreads
    cand_graduated = {
        'id': 'real_live_cand_grad_1',
        'game_id': 'real_match_102',
        'league': 'NBA',
        'model_scope': 'full_game',
        'market': 'spreads',
        'is_graduated': True,
        'stress_roi': 0.08,
        'expected_roi': 0.08,
        'probability': 0.65,
        'stress_probability': 0.60,
        'push_probability': 0.0,
        'reference_odds': 1.95,
        'minimum_odds': 1.5,
        'fair_odds': 1.6
    }

    # 3. Demoted candidate strictly quarantined to $0.00
    cand_demoted = {
        'id': 'real_live_cand_demoted_1',
        'game_id': 'real_match_103',
        'league': 'NHL',
        'model_scope': 'full_game',
        'market': 'totals',
        'stress_roi': 0.08,
        'expected_roi': 0.08,
        'probability': 0.65,
        'stress_probability': 0.60,
        'push_probability': 0.0,
        'reference_odds': 1.95,
        'minimum_odds': 1.5,
        'fair_odds': 1.6
    }

    chosen, alts = rank_and_size([cand_unproven, cand_graduated, cand_demoted], settings)
    
    # The unproven candidate must be in alternatives with stake 0.0
    unproven_rec = next(a for a in alts if a['id'] == 'real_live_cand_unproven_1')
    assert unproven_rec['stake'] == 0.0
    assert unproven_rec['allocation_code'] == 'incubator_unproven_research'

    # The demoted candidate must be strictly quarantined with stake 0.0
    demoted_rec = next(a for a in alts if a['id'] == 'real_live_cand_demoted_1')
    assert demoted_rec['stake'] == 0.0
    assert demoted_rec['allocation_code'] == 'capital_defense_quarantined'

    # The graduated candidate must be funded (probation nursery floor >= $0.50)
    grad_rec = next(c for c in chosen if c['id'] == 'real_live_cand_grad_1')
    assert grad_rec['stake'] >= 0.50
    assert grad_rec['allocation_code'] == 'funded'


def test_graduated_champion_model_inference():
    """Verifies that a market graduated via tournament executes with the champion model directly."""
    from betting_engine.tournament_engine import TournamentEngine
    from betting_engine.sandbox_models.archetypes.odds_damped_kelly import OddsDampedKellyShrinkageModel
    from betting_engine.graduated_models import GraduatedUniversalSegmentModel

    t_engine = TournamentEngine()
    segment_key = 'NBA|full_game|spreads'
    champ_model = OddsDampedKellyShrinkageModel(
        model_id='nba_champ_damped_test',
        version='2.0.0',
        league='NBA',
        scope='full_game',
        family='spreads',
        damping_strength=0.30
    )
    t_engine.register_candidate(segment_key, champ_model)

    meta = {
        'league': 'NBA',
        'scope': 'full_game',
        'family': 'spreads',
        'graduated_at': utcnow().isoformat(),
        'stats': {
            'sample_size': 35,
            'roi': 0.12,
            'champion_model_id': 'nba_champ_damped_test'
        }
    }

    grad_model = GraduatedUniversalSegmentModel('NBA', 'full_game', meta)
    assert grad_model.champion_model is not None
    assert grad_model.champion_model.model_id == 'nba_champ_damped_test'

    game = Game(
        id='NBA:test_champ_1', league='NBA', start=utcnow().isoformat(),
        home='Boston Celtics', away='Miami Heat', home_id='BOS', away_id='MIA',
        home_abbr='BOS', away_abbr='MIA',
        state='scheduled', game_type='regular', season=2026,
        source='https://stake.com/sports/basketball/nba/boston-miami',
        fetched_at=utcnow().isoformat(),
        context={'home_expected': 114.0, 'away_expected': 106.0}
    )

    fc = grad_model.forecast(game)
    assert fc.features.get('champion_model') == 'nba_champ_damped_test'
    assert 'home' in fc.model_probabilities
    assert 0.01 <= fc.model_probabilities['home'] <= 0.99


