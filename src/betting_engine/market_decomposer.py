"""Dynamic Market Decomposer & Semantic Normalizer.

Transforms observed raw Stake gap markets into standardized mathematical
contract specifications across 4 generalized families:
1. Asian Lines (Quarter-handicaps and quarter-totals with split payoffs)
2. Tennis Set Markets (Markov-chain service game probability derivations)
3. Bivariate Score Derivatives (Dixon-Coles joint distribution for BTTS, Double Chance, Exact Goals)
4. Sequence & Time-to-Event (Hazard rates for first goal/first inning scoring)

Nothing is hardcoded: line extraction, threshold derivations, and payoff rules
are dynamically parsed from market titles and specifiers.
"""
from dataclasses import dataclass, field
import math
import re
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class DecomposedContract:
    raw_market_name: str
    league: str
    fixture_id: str
    scope: str
    family: str
    sub_family: str
    mathematical_model: str
    line: Optional[float] = None
    side: Optional[str] = None
    selection: Optional[str] = None
    odds: float = 2.0
    specifiers: str = ""
    dynamic_sample_power_req: int = 25
    metadata: Dict[str, Any] = field(default_factory=dict)


def extract_line(market_name: str, specifiers: str = "", outcome_name: str = "") -> Optional[float]:
    """Dynamically extracts numeric line from specifiers, market name, or outcome text."""
    # 1. Specifiers check (e.g. 'total=2.25' or 'hcp=-0.75')
    if specifiers:
        m = re.search(r'(?:total|hcp|handicap|spread)=([+-]?\d+(?:\.\d+)?)', specifiers, re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass

    # 2. Text in parentheses (e.g. 'Over (+2.25)' or 'Handicap (-0.75)')
    m_par = re.search(r'\(([+-]?\d+(?:\.\d+)?)\)', outcome_name or market_name)
    if m_par:
        try:
            return float(m_par.group(1))
        except ValueError:
            pass

    # 3. Numeric trailing line (e.g. 'Total Games 9.5' or 'Over 2.5')
    m_num = re.search(r'\b([+-]?\d+(?:\.\d+)?)\b', outcome_name or market_name)
    if m_num:
        try:
            val = float(m_num.group(1))
            if abs(val) < 250:  # Sensible score/line bound
                return val
        except ValueError:
            pass

    return None


def decompose_market(market_record: Dict[str, Any], league: str, fixture_id: str) -> List[DecomposedContract]:
    """Dynamically categorizes an unmodeled Stake market into candidate DecomposedContracts."""
    raw_name = str(market_record.get('market') or market_record.get('name') or '')
    s = raw_name.lower()
    specifiers = str(market_record.get('specifiers') or '')
    outcomes = market_record.get('outcomes') or []

    # Dynamic scope detection
    scope = 'full_game'
    if '1st half' in s or 'first half' in s or 'half 1' in s:
        scope = 'half_1'
    elif '2nd half' in s or 'second half' in s or 'half 2' in s:
        scope = 'half_2'
    elif '1st period' in s or 'first period' in s:
        scope = 'period_1'
    elif '2nd period' in s or 'second period' in s:
        scope = 'period_2'
    elif '3rd period' in s or 'third period' in s:
        scope = 'period_3'
    elif '1st quarter' in s or 'first quarter' in s or 'quarter 1' in s or '1st qtr' in s:
        scope = 'quarter_1'
    elif '2nd quarter' in s or 'second quarter' in s or 'quarter 2' in s or '2nd qtr' in s:
        scope = 'quarter_2'
    elif '3rd quarter' in s or 'third quarter' in s or 'quarter 3' in s or '3rd qtr' in s:
        scope = 'quarter_3'
    elif '4th quarter' in s or 'fourth quarter' in s or 'quarter 4' in s or '4th qtr' in s:
        scope = 'quarter_4'
    elif '1st set' in s or 'first set' in s:
        scope = 'period_1'  # Scoped to 1st set
    elif '2nd set' in s or 'second set' in s:
        scope = 'period_2'
    elif '1st inning' in s:
        scope = 'inning_1'
    elif 'innings 1 to 5' in s or 'first 5 innings' in s:
        scope = 'innings_1_5'
    elif 'incl. overtime' in s or 'including overtime' in s:
        scope = 'full_game'

    contracts = []

    # Family 1: Asian Lines (Handicaps and Totals)
    has_quarter_split = any(
        isinstance(o, dict) and any(q in str(o.get('name', '')) for q in ('.25', '.75')) for o in outcomes
    ) or any(q in specifiers for q in ('.25', '.75'))
    is_asian = 'asian' in s or has_quarter_split
    is_spread = 'handicap' in s or 'spread' in s
    is_total = 'total' in s or 'goals' in s or 'games' in s or 'points' in s

    if is_asian and is_spread:
        for idx, out in enumerate(outcomes):
            o_name = str(out.get('name') or '')
            odds = float(out.get('odds') or 2.0)
            line = extract_line(raw_name, specifiers, o_name)
            s_low = o_name.lower()
            if '1' in o_name or 'home' in s_low or (len(outcomes) == 2 and idx == 0 and 'draw' not in s_low):
                side = 'home'
            elif '2' in o_name or 'away' in s_low or (len(outcomes) == 2 and idx == 1 and 'draw' not in s_low):
                side = 'away'
            else:
                side = 'draw'
            contracts.append(DecomposedContract(
                raw_market_name=raw_name,
                league=league,
                fixture_id=fixture_id,
                scope=scope,
                family='asian_lines',
                sub_family='asian_handicap',
                mathematical_model='split_distribution_margin',
                line=line,
                side=side,
                selection=o_name,
                odds=odds,
                specifiers=specifiers,
                dynamic_sample_power_req=max(20, int(20 * math.sqrt(max(0.1, odds - 1.0)))),
                metadata={'is_quarter_line': line is not None and abs(round(line * 4) % 2) == 1}
            ))

    elif is_asian and is_total:
        for out in outcomes:
            o_name = str(out.get('name') or '')
            odds = float(out.get('odds') or 2.0)
            line = extract_line(raw_name, specifiers, o_name)
            side = 'over' if 'over' in o_name.lower() else ('under' if 'under' in o_name.lower() else 'exact')
            contracts.append(DecomposedContract(
                raw_market_name=raw_name,
                league=league,
                fixture_id=fixture_id,
                scope=scope,
                family='asian_lines',
                sub_family='asian_total',
                mathematical_model='split_distribution_total',
                line=line,
                side=side,
                selection=o_name,
                odds=odds,
                specifiers=specifiers,
                dynamic_sample_power_req=max(20, int(20 * math.sqrt(max(0.1, odds - 1.0)))),
                metadata={'is_quarter_line': line is not None and abs(round(line * 4) % 2) == 1}
            ))

    # Family 2: Tennis Set Markets
    elif league in ('ATP', 'WTA', 'CHALLENGER') and ('set' in s or 'games' in s or 'double result' in s or 'nil' in s):
        if 'double result' in s:
            sub_fam = 'double_result'
        elif 'nil' in s:
            sub_fam = 'set_to_nil'
        elif 'winner' in s:
            sub_fam = 'set_winner'
        elif 'total' in s:
            sub_fam = 'set_totals'
        else:
            sub_fam = 'set_handicap'
        for idx, out in enumerate(outcomes):
            o_name = str(out.get('name') or '')
            odds = float(out.get('odds') or 2.0)
            line = extract_line(raw_name, specifiers, o_name)
            s_low = o_name.lower()
            if sub_fam == 'double_result':
                side = o_name.strip()
            elif sub_fam == 'set_to_nil':
                side = 'yes' if 'yes' in s_low else 'no'
            elif 'over' in s_low:
                side = 'over'
            elif 'under' in s_low:
                side = 'under'
            elif '1' in o_name or 'home' in s_low or (len(outcomes) == 2 and idx == 0):
                side = 'home'
            elif '2' in o_name or 'away' in s_low or (len(outcomes) == 2 and idx == 1):
                side = 'away'
            else:
                side = 'home' if idx == 0 else 'away'
            contracts.append(DecomposedContract(
                raw_market_name=raw_name,
                league=league,
                fixture_id=fixture_id,
                scope=scope,
                family='tennis_sets',
                sub_family=sub_fam,
                mathematical_model='markov_chain_service_hold',
                line=line,
                side=side,
                selection=o_name,
                odds=odds,
                specifiers=specifiers,
                dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                metadata={'tennis_scope': scope}
            ))

    # Family 3: Bivariate Score Derivatives (Soccer / Hockey)
    elif ('both teams to score' in s or 'btts' in s or 'clean sheet' in s or 'correct score' in s or 'exact goals' in s or 'double chance' in s or 'draw no bet' in s or 'dnb' in s or 'goal range' in s or 'both halves' in s or 'score in both halves' in s or 'win both halves' in s or '& both teams to score' in s or 'total & both' in s or '1x2 & total' in s or (league in ('MLS', 'EPL', 'UCL') and 'halftime/fulltime' in s)):
        if 'halftime/fulltime' in s:
            sub_fam = 'halftime_fulltime'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='bivariate_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='bivariate_half_full',
                    line=None,
                    side=o_name.strip(),
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(30, int(30 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'ht_ft_selection': o_name}
                ))
        elif '& both teams to score' in s or 'total & both' in s or '1x2 & total' in s:
            sub_fam = 'btts_combo'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                line = extract_line(raw_name, specifiers, o_name)
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='bivariate_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='dixon_coles_bivariate',
                    line=line,
                    side=o_name.strip(),
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(30, int(30 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'combo_type': raw_name}
                ))
        elif 'goal range' in s:
            sub_fam = 'goal_range'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='bivariate_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='dixon_coles_bivariate',
                    line=None,
                    side=o_name.strip(),
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'goal_range_selection': o_name}
                ))
        elif 'both halves' in s or 'score in both halves' in s or 'win both halves' in s:
            sub_fam = 'both_halves'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                line = extract_line(raw_name, specifiers, o_name) or 1.5
                side = 'yes' if 'yes' in o_name.lower() or 'over' in o_name.lower() else ('no' if 'no' in o_name.lower() or 'under' in o_name.lower() else o_name.strip())
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='bivariate_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='dixon_coles_bivariate',
                    line=line,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'both_halves_type': raw_name}
                ))
        elif 'double chance' in s:
            sub_fam = 'double_chance'
            for idx, out in enumerate(outcomes):
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                o_low = o_name.lower().replace(' ', '')
                if '1x' in o_low or 'home/draw' in o_low or 'homeordraw' in o_low or '1orx' in o_low:
                    side = '1x'
                elif 'x2' in o_low or 'draw/away' in o_low or 'draworaway' in o_low or 'xor2' in o_low:
                    side = 'x2'
                elif '12' in o_low or 'home/away' in o_low or 'homeoraway' in o_low or '1or2' in o_low:
                    side = '12'
                elif idx == 0:
                    side = '1x'
                elif idx == 1:
                    side = '12'
                else:
                    side = 'x2'
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='bivariate_derivatives',
                    sub_family='double_chance',
                    mathematical_model='dixon_coles_bivariate',
                    line=None,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'soccer_scope': scope}
                ))
        elif 'draw no bet' in s or 'dnb' in s:
            for idx, out in enumerate(outcomes):
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                s_low = o_name.lower()
                side = 'home' if ('1' in o_name or 'home' in s_low or idx == 0) else 'away'
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family='moneyline',
                    mathematical_model='empirical_scope_outcome',
                    line=None,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'draw_no_bet': True}
                ))
        else:
            sub_fam = 'both_teams_score' if 'both' in s else ('correct_score' if 'correct' in s else 'exact_goals')
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                side = 'yes' if 'yes' in o_name.lower() else ('no' if 'no' in o_name.lower() else o_name.lower())
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='bivariate_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='dixon_coles_bivariate',
                    line=None,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(30, int(30 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'target_score': o_name if sub_fam == 'correct_score' else None}
                ))

    # Family 4: Sequence & Time-to-Event (1st Goal, Last Goal, 1st Inning 3-way)
    elif ('1st goal' in s or 'first goal' in s or 'last goal' in s or 'team to score first' in s or 'team to score last' in s):
        sub_fam = 'last_to_score' if ('last' in s) else 'first_to_score'
        for idx, out in enumerate(outcomes):
            o_name = str(out.get('name') or '')
            odds = float(out.get('odds') or 2.0)
            s_low = o_name.lower()
            if 'no goal' in s_low or 'none' in s_low:
                side = 'no_goal'
            elif '1' in o_name or 'home' in s_low or idx == 0:
                side = 'home'
            elif '2' in o_name or 'away' in s_low or idx == 1:
                side = 'away'
            else:
                side = 'no_goal'
            contracts.append(DecomposedContract(
                raw_market_name=raw_name,
                league=league,
                fixture_id=fixture_id,
                scope=scope,
                family='sequence_hazard',
                sub_family=sub_fam,
                mathematical_model='poisson_point_hazard',
                line=None,
                side=side,
                selection=o_name,
                odds=odds,
                specifiers=specifiers,
                dynamic_sample_power_req=max(35, int(35 * math.sqrt(max(0.1, odds - 1.0)))),
                metadata={'event_type': 'goal'}
            ))

    # Family 5: American & Multi-Sport Derivatives (NFL, NBA, NHL, MLB, NCAAF, WNBA, NCAAB, Soccer, Tennis)
    elif league in ('NBA', 'NFL', 'NHL', 'MLB', 'NCAAF', 'WNBA', 'NCAAB') or (is_spread or is_total or '1x2' in s or 'winner' in s or 'margin' in s or 'odd/even' in s or 'odd / even' in s or 'highest scoring' in s):
        # 1. Team Totals (Home/Away specific total)
        clean_name = re.sub(r'\(.*?\)', '', raw_name).strip()
        for pfx in ('1st half -', '2nd half -', '1st period -', '2nd period -', '3rd period -',
                    '1st quarter -', '2nd quarter -', '3rd quarter -', '4th quarter -',
                    '1st inning -', 'innings 1 to 5 -', 'first 5 innings -'):
            if clean_name.lower().startswith(pfx):
                clean_name = clean_name[len(pfx):].strip()
        is_team_total = is_total and (
            'team' in s
            or (clean_name.lower() not in ('total', 'asian total', 'total games', 'total points', 'total goals') and 'total' in clean_name.lower())
        )

        if is_team_total:
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                line = extract_line(raw_name, specifiers, o_name)
                side = 'over' if 'over' in o_name.lower() else ('under' if 'under' in o_name.lower() else 'exact')
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family='team_total',
                    mathematical_model='empirical_score_team_total',
                    line=line,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(20, int(20 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'team_target': raw_name}
                ))

        elif is_spread:
            for idx, out in enumerate(outcomes):
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                line = extract_line(raw_name, specifiers, o_name)
                s_low = o_name.lower()
                if '1' in o_name or 'home' in s_low or (len(outcomes) == 2 and idx == 0):
                    side = 'home'
                elif '2' in o_name or 'away' in s_low or (len(outcomes) == 2 and idx == 1):
                    side = 'away'
                else:
                    side = 'home' if idx == 0 else 'away'
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family='spreads',
                    mathematical_model='empirical_score_margin',
                    line=line,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(20, int(20 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'is_quarter_line': False}
                ))

        elif is_total:
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                line = extract_line(raw_name, specifiers, o_name)
                side = 'over' if 'over' in o_name.lower() else ('under' if 'under' in o_name.lower() else 'exact')
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family='totals',
                    mathematical_model='empirical_score_total',
                    line=line,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(20, int(20 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'is_quarter_line': False}
                ))

        elif 'winning margin' in s:
            sub_fam = 'winning_margin'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='empirical_score_margin',
                    line=None,
                    side=o_name.strip(),
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'winning_margin_band': o_name}
                ))

        elif 'overtime' in s or 'will there be overtime' in s:
            sub_fam = 'overtime'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                side = 'yes' if 'yes' in o_name.lower() else 'no'
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='empirical_regulation_tie',
                    line=None,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={}
                ))

        elif 'highest scoring' in s:
            sub_fam = 'highest_scoring_period'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='empirical_period_expectation',
                    line=None,
                    side=o_name.strip(),
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={}
                ))

        elif 'odd/even' in s or 'odd / even' in s:
            sub_fam = 'odd_even'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                side = 'odd' if 'odd' in o_name.lower() else 'even'
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='empirical_score_parity',
                    line=None,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(20, int(20 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={}
                ))

        elif 'halftime/fulltime' in s:
            sub_fam = 'halftime_fulltime'
            for out in outcomes:
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='empirical_score_margin',
                    line=None,
                    side=o_name.strip(),
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(30, int(30 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={'ht_ft_selection': o_name}
                ))

        elif '1x2' in s or '3-way' in s or 'three way' in s or 'winner' in s or 'moneyline' in s:
            sub_fam = 'three_way' if ('1x2' in s or '3-way' in s or len(outcomes) == 3) else 'moneyline'
            for idx, out in enumerate(outcomes):
                o_name = str(out.get('name') or '')
                odds = float(out.get('odds') or 2.0)
                s_low = o_name.lower()
                if 'draw' in s_low or 'tie' in s_low or 'x' == s_low:
                    side = 'draw'
                elif '1' in o_name or 'home' in s_low or idx == 0:
                    side = 'home'
                elif '2' in o_name or 'away' in s_low or idx == 1:
                    side = 'away'
                else:
                    side = 'draw'
                contracts.append(DecomposedContract(
                    raw_market_name=raw_name,
                    league=league,
                    fixture_id=fixture_id,
                    scope=scope,
                    family='american_derivatives',
                    sub_family=sub_fam,
                    mathematical_model='empirical_scope_outcome',
                    line=None,
                    side=side,
                    selection=o_name,
                    odds=odds,
                    specifiers=specifiers,
                    dynamic_sample_power_req=max(25, int(25 * math.sqrt(max(0.1, odds - 1.0)))),
                    metadata={}
                ))

    return contracts
