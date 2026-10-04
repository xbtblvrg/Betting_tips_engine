"""Explicit reproducible funding tiers; callers must enforce source/model guards.

This pure policy never publishes, records, places or settles a bet. The opt-in
model tier retains the actual stress failure and is not a profitability claim.
"""
from collections.abc import Mapping
from decimal import Decimal, ROUND_DOWN
import math

VERSION = 'funding-policy-v1'
WEIGHTED_VERSION = 'funding-policy-v2'
ALL_TIERS_VERSION = 'funding-policy-v3'
MODEL_PICK_VERSION = 'model-pick-unit-v1'
WEIGHTED_MODEL_PICK_VERSION = 'model-pick-unit-v2'
PROBABILITY_PICK_VERSION = 'probability-pick-unit-v1'
ALL_TIERS_MODEL_PICK_VERSION = 'model-pick-unit-v3'
ALL_TIERS_PROBABILITY_PICK_VERSION = 'probability-pick-unit-v3'
MODEL_ALLOCATION_POLICIES = ('one_per_game_v1', 'distinct_family_v1')


def model_allocation_policy(settings):
    value = settings.get('model_pick_allocation_policy', 'one_per_game_v1')
    if value not in MODEL_ALLOCATION_POLICIES:
        raise ValueError('Unknown model-pick allocation policy')
    return value


MODEL_EV_EPSILON = 1e-9
POLICIES = ('strict', 'continuous_model_v1', 'continuous_best_available_v1')
MODEL_SIZING_POLICIES = ('unit_cap_v1', 'model_weighted_v2', 'all_tiers_weighted_v3')
CLV_GATE_MODES = ('legacy', 'prospective_positive_clv')


def _finite(value, name):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Invalid finite numeric input: ' + name)
        return float(value)
    except (OverflowError, TypeError):
        raise ValueError('Invalid finite numeric input: ' + name) from None


def _inputs(tip, settings):
    if not isinstance(tip, Mapping) or not isinstance(settings, Mapping):
        raise ValueError('Tip and settings must be mappings')
    try:
        p = _finite(tip['probability'], 'probability')
        stress = _finite(tip['stress_probability'], 'stress_probability')
        push = _finite(tip['push_probability'], 'push_probability')
        odds = _finite(tip['reference_odds'], 'reference_odds')
        roi = _finite(settings['minimum_roi'], 'minimum_roi')
        for key in ('max_bet', 'unit_stake', 'kelly_fraction', 'max_bankroll_fraction'):
            if _finite(settings[key], key) <= 0:
                raise ValueError('Sizing setting must be positive: ' + key)
    except KeyError as exc:
        raise ValueError('Missing funding input: ' + str(exc.args[0])) from None
    if not (0 <= stress <= p < 1 and 0 <= push <= 1 and p + push <= 1 and odds > 1 and roi >= 0):
        raise ValueError('Invalid funding probability mass, odds or minimum ROI')
    if settings.get('bankroll') is not None and _finite(settings['bankroll'], 'bankroll') <= 0:
        raise ValueError('Supplied bankroll must be positive')
    if settings.get('daily_risk_limit') is not None and _finite(settings['daily_risk_limit'], 'daily_risk_limit') < 0:
        raise ValueError('Daily risk limit must be nonnegative or unset')
    policy = settings.get('recommendation_policy', 'strict')
    if not isinstance(policy, str) or policy not in POLICIES:
        raise ValueError('Unknown recommendation policy')
    return p, stress, push, odds, roi, policy


def funding_basis(tip, settings):
    """Recompute eligibility from probabilities/odds, never supplied ROI or tier.

    ``minimum_odds=None`` denotes no finite threshold when the selected basis
    assigns zero win probability. The model tier needs EV strictly above the
    numerical zero allowance; its displayed threshold is nominal break-even.
    """
    from .decisions import sportsbook_ev
    p, stress, push, odds, roi, policy = _inputs(tip, settings)
    allocation_policy = model_allocation_policy(settings)
    sizing_policy = settings.get('model_pick_sizing_policy', 'unit_cap_v1')
    if not isinstance(sizing_policy, str) or sizing_policy not in MODEL_SIZING_POLICIES:
        raise ValueError('Unknown model-pick sizing policy')
    all_tiers = sizing_policy == 'all_tiers_weighted_v3'
    weighted = sizing_policy in ('model_weighted_v2', 'all_tiers_weighted_v3')
    model_roi = sportsbook_ev(p, push, odds)
    stress_roi = sportsbook_ev(stress, push, odds)
    if not all(math.isfinite(value) for value in (model_roi, stress_roi)):
        raise ValueError('Funding ROI overflow')
    tier = ('stress_value' if stress_roi > 0 and stress_roi >= roi else
            'model_pick' if policy in ('continuous_model_v1', 'continuous_best_available_v1') and p > 0 and model_roi > MODEL_EV_EPSILON else
            'probability_pick' if policy == 'continuous_best_available_v1' and p >= .5 and model_roi <= MODEL_EV_EPSILON else 'unfunded')
    if settings.get('fallback_clv_gate', 'legacy') not in CLV_GATE_MODES:
        raise ValueError('Unknown fallback CLV gate mode')
    clv_gate = tip.get('clv_evidence') if isinstance(tip, Mapping) else None
    clv_min_games = settings.get('clv_min_games', 30)
    if isinstance(clv_min_games, bool) or not isinstance(clv_min_games, int) or clv_min_games < 1:
        raise ValueError('Invalid CLV minimum independent games')
    clv_passed = bool(isinstance(clv_gate, Mapping)
                      and clv_gate.get('state') == 'positive'
                      and clv_gate.get('source_verified') is True
                      and isinstance(clv_gate.get('independent_games'), int)
                      and clv_gate.get('independent_games') >= clv_min_games
                      and isinstance(clv_gate.get('clv'), (int, float))
                      and math.isfinite(float(clv_gate.get('clv')))
                      and float(clv_gate.get('clv')) > 0)
    shadow_tier = tier if tier in ('model_pick', 'probability_pick') else None
    continuous_diverse = settings.get('family_selection_policy') == 'continuous_probability_v2'
    if not continuous_diverse and settings.get('fallback_clv_gate', 'legacy') == 'prospective_positive_clv' and shadow_tier and not clv_passed:
        tier = 'unfunded'
    model = tier == 'model_pick'
    probability_only = tier == 'probability_pick'
    probability = p if model else stress
    threshold = (1 - push + (0 if model else roi)) / probability if probability > 0 else None
    if threshold is not None and not math.isfinite(threshold):
        threshold = None  # No representable executable threshold; never serialize infinity.
    if probability_only:
        threshold = 1.0  # This explicit preference tier does not claim a value threshold.
    cap = min(float(settings['max_bet']), 1. if (model and not weighted) or (probability_only and not all_tiers) else 5.) if tier != 'unfunded' else 0.
    note = ('Win-probability preference only, not a positive-value or Kelly allocation. Nominal model EV may be negative. '
            'Fixed unit at most $1; profit is unproven, with actual nominal and stressed EV retained.' if probability_only else
            'Positive nominal model EV only; the stress-value requirement fails. '
            'Maximum $1; profit is unproven and the original stressed probability/ROI remain unchanged.' if model else
            'Positive EV satisfies the configured stress-value requirement; normal bounded stake sizing applies. Profit is unproven.' if tier == 'stress_value' else
            'No allocation under the selected policy. A nominally negative or numerically zero edge is never funded.')
    if model and weighted:
        note = ('Positive nominal model EV only; the stress-value requirement fails. '
                'Nominal-probability sizing uses the related-event discount and any supplied bankroll limits, '
                'within the configured $1–$5 range. Profit is unproven; actual stressed probability/ROI remain unchanged.')
    if probability_only and all_tiers:
        note = ('Win-probability preference only, not a positive-value or Kelly allocation. Nominal model EV may be negative. '
                'Configured base-unit sizing uses a related-event discount and any supplied bankroll-fraction cap, '
                'within the configured $1–$5 range; no tier-specific $1 cap. The $1 ticket minimum may bind. '
                'Profit is unproven, with actual nominal and stressed EV retained.')
    result = {'version': VERSION, 'policy': policy, 'minimum_roi': roi, 'tier': tier,
            'eligible': tier != 'unfunded', 'probability_basis': 'model_win_probability_only' if probability_only else 'model' if model else 'stress',
            'minimum_odds': threshold, 'maximum_stake': cap,
            'model_probability': p, 'stress_probability': stress, 'push_probability': push,
            'odds': odds, 'model_roi': model_roi, 'stress_roi': stress_roi,
            'note': note, 'claim': 'Reproducible allocation policy only. Exact current contract, source, independent model and publication guards remain mandatory; no guaranteed win or established profit.'}
    if 'fallback_clv_gate' in settings:
        result.update(fallback_clv_gate=settings.get('fallback_clv_gate', 'legacy'), clv_min_games=clv_min_games,
                      clv_evidence=clv_gate if isinstance(clv_gate, Mapping) else None,
                      clv_gate_passed=clv_passed)
    if continuous_diverse:
        result.update(family_selection_policy='continuous_probability_v2',
                      clv_gate_role='measurement_only')
    if shadow_tier and tier == 'unfunded' and settings.get('fallback_clv_gate', 'legacy') == 'prospective_positive_clv':
        result.update(shadow_only=True, shadow_tier=shadow_tier,
                      shadow_reason=f'{shadow_tier} requires source-verified positive prospective CLV on at least {clv_min_games} independent games.')
        result['note'] = result['note'] + ' Shadow only: no stake until the segment passes the prospective CLV gate.'
    if weighted:
        result.update(version=ALL_TIERS_VERSION if all_tiers else WEIGHTED_VERSION, model_pick_sizing_policy=sizing_policy)
    if allocation_policy != 'one_per_game_v1':
        result['model_pick_allocation_policy'] = allocation_policy
    return result


def _probability_base_units_v3(result, tip, settings, remaining, related_count, basis):
    """Explicit preference budget, independent of estimated edge or Kelly.

    U / sqrt(1 + n) is the exposure-adjusted base unit. A configured unit and
    available cap both >= $1 permit the documented minimum ticket floor; a
    smaller configured unit or available cap yields zero. The floor does not
    erase the raw discounted amount or claim that realized exposure fell.
    """
    unit = float(settings['unit_stake'])
    related = int(related_count)
    discount = 1. / math.sqrt(1 + related)
    discounted = unit * discount
    cap = min(5., float(settings['max_bet']), remaining)
    limits = ['explicit_probability_preference', 'not_positive_value_or_kelly']
    if related:
        limits.append('related_event_exposure')
    bankroll = settings.get('bankroll')
    if bankroll is not None:
        bankroll_cap = float(bankroll) * float(settings['max_bankroll_fraction'])
        if not math.isfinite(bankroll_cap):
            raise ValueError('Bankroll allocation numeric overflow')
        if bankroll_cap < cap:
            limits.append('bankroll_fraction_cap')
        cap = min(cap, bankroll_cap)
    if unit < 1:
        limits.append('configured_unit_below_minimum')
    if unit >= 1 and cap >= 1 and discounted < 1:
        limits.append('minimum_unit_floor')
    if discounted > cap:
        limits.append('per_bet_or_available_cap')
    value = min(cap, max(1., discounted)) if unit >= 1 and cap >= 1 else 0.
    unrelated = min(cap, unit) if unit >= 1 and cap >= 1 else 0.
    # Decimal floor avoids letting a binary-float epsilon exceed an available
    # cap just below a cent; only the new version uses this rounding rule.
    amount = float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)) if value >= 1 else 0.
    unrelated_amount = float(Decimal(str(unrelated)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)) if unrelated >= 1 else 0.
    if amount == 0:
        limits.append('below_minimum_stake')
    result.update(version=ALL_TIERS_PROBABILITY_PICK_VERSION, mode='probability_only_base_units_v3',
        model_pick_sizing_policy='all_tiers_weighted_v3', amount=amount,
        stress_probability=tip['stress_probability'], stress_roi=basis['stress_roi'],
        model_probability=tip['probability'], model_roi=basis['model_roi'],
        probability_basis='model_win_probability_only', full_kelly_fraction=0.,
        related_contracts=related, event_discount=discount,
        before_event_discount=unit, after_event_discount=discounted,
        maximum_stake=cap, amount_without_related_bets=unrelated_amount,
        binding_factors=limits, sizing_formula='base_unit / sqrt(1 + related_contracts), minimum-ticket floor when affordable, bounded by available caps',
        note=basis['note'] + f' Configured base unit ${unit:.2f}; {related} related contracts; '
            f'exposure multiplier {discount:.3f}; discounted unit ${discounted:.2f}. '
            f'Fund only when the configured unit and available cap both permit the $1 minimum. '
            f'Calculated stake ${amount:.2f}, available cap ${cap:.2f}; no Kelly fraction or confidence multiplier is used. '
            'The minimum ticket floor can offset the raw discount; no bankroll or positive edge is invented.')
    return result


def _base_size_recommendation(tip, settings, remaining, related_count=0):
    """Normal eligible sizing is byte-equivalent to existing stake_assessment.

    Model picks use its nominal-probability sizing and related-event discount,
    within the explicit legacy $1 or weighted $5 cap. A supplied bankroll is
    still binding and can yield $0. Legacy probability-only fixed units remain
    $1; explicit v3 uses bounded configured units without a positive-Kelly claim.
    No ticket, bankroll, original probability or input mapping is modified.
    """
    from .decisions import stake_assessment
    basis = funding_basis(tip, settings)
    if remaining != math.inf:
        _finite(remaining, 'remaining')
    if isinstance(remaining, bool) or remaining < 0:
        raise ValueError('Invalid remaining allocation')
    _finite(related_count, 'related_count')
    if related_count < 0 or int(related_count) != related_count:
        raise ValueError('Invalid related contract count')
    model = basis['tier'] == 'model_pick'
    sizing_policy = basis.get('model_pick_sizing_policy', 'unit_cap_v1')
    all_tiers = sizing_policy == 'all_tiers_weighted_v3'
    weighted_model = model and sizing_policy in ('model_weighted_v2', 'all_tiers_weighted_v3')
    probability_only = basis['tier'] == 'probability_pick'
    local_settings = dict(settings)
    if model or probability_only:
        local_settings['max_bet'] = min(float(settings['max_bet']), 5. if weighted_model or all_tiers else 1.)
    probability = tip['probability'] if model or probability_only else tip['stress_probability']
    # Zero remaining prevents a below-threshold strict candidate from acquiring
    # a stake merely because the lower-level sizing heuristic sees positive EV.
    result = stake_assessment(probability, tip['push_probability'], tip['reference_odds'],
        local_settings, remaining if basis['eligible'] else 0., related_count)
    if model and model_allocation_policy(settings) == 'distinct_family_v1':
        result['note'] = result['note'].replace('prior same-game contracts', 'held or co-issued same-game contracts')
    if any(isinstance(value, float) and not math.isfinite(value) for value in result.values()):
        raise ValueError('Stake assessment numeric overflow')
    if probability_only and all_tiers:
        return _probability_base_units_v3(result, tip, settings, remaining, related_count, basis)
    if probability_only:
        # Explicit preference allocation, not the Kelly result computed above.
        # Related exposure is retained as information; no discount is claimed.
        bankroll = settings.get('bankroll')
        cap = min(1., float(settings['max_bet']), remaining)
        limits = ['explicit_probability_preference', 'not_positive_value_or_kelly']
        if bankroll is not None:
            bankroll_cap = float(bankroll) * float(settings['max_bankroll_fraction'])
            if not math.isfinite(bankroll_cap):
                raise ValueError('Bankroll allocation numeric overflow')
            if bankroll_cap < cap:limits.append('bankroll_fraction_cap')
            cap = min(cap, bankroll_cap)
        amount = 1. if cap >= 1. else 0.
        if cap < 1:limits.extend(('per_bet_or_available_cap', 'below_minimum_stake'))
        if related_count:limits.append('related_exposure_recorded')
        result.update(version=PROBABILITY_PICK_VERSION, mode='probability_only_fixed_unit',
            amount=amount, stress_probability=tip['stress_probability'], stress_roi=basis['stress_roi'],
            model_probability=tip['probability'], model_roi=basis['model_roi'],
            probability_basis='model_win_probability_only', full_kelly_fraction=0.,
            event_discount=1., before_event_discount=1., after_event_discount=1.,
            maximum_stake=cap, amount_without_related_bets=amount, binding_factors=limits,
            note=basis['note'] + f' Related contracts: {int(related_count)}; exposure is recorded without claiming a discount or optimized portfolio. '
                + f'No bankroll is invented. New fixed stake ${amount:.2f}, available cap ${cap:.2f}.')
        return result
    if not basis['eligible']:
        result['binding_factors'] = [*result['binding_factors'], 'funding_policy_ineligible']
        result['note'] += ' No allocation: the selected funding-policy requirement is not met.'
        return result
    if not model:
        return result
    result.update(version=ALL_TIERS_MODEL_PICK_VERSION if all_tiers else WEIGHTED_MODEL_PICK_VERSION if weighted_model else MODEL_PICK_VERSION, mode='model_pick_' + result['mode'],
        stress_probability=tip['stress_probability'], stress_roi=basis['stress_roi'],
        model_probability=tip['probability'], model_roi=basis['model_roi'], probability_basis='model')
    if weighted_model:
        result['model_pick_sizing_policy'] = sizing_policy
    # The original stress-sizing wording would mislabel the inputs in bankroll
    # mode. Keep the actual calculation explanation without inventing stress.
    result['note'] = basis['note'] + ' ' + result['note'].replace('from stressed probability', 'from nominal model probability')
    return result


def size_recommendation(tip, settings, remaining, related_count=0):
    """Apply optional historical sizing after the unchanged eligibility formula."""
    from .outcome_sizing_runtime import outcome_policy
    from .outcome_sizing import apply_adjustment, verify_summary, segment_key
    policy = outcome_policy(settings)
    base = _base_size_recommendation(tip, settings, remaining, related_count)
    if settings.get('family_selection_policy') == 'continuous_probability_v2':
        # Explicit user-authorized minimum ticket, not a Kelly-derived edge.
        # Never spend a dollar the event/bankroll/per-ticket caps do not allow.
        basis = funding_basis(tip, settings)
        cap = min(remaining, float(settings['max_bet']), 5.)
        if settings.get('bankroll') is not None:
            cap = min(cap, float(settings['bankroll']) * float(settings['max_bankroll_fraction']))
        if basis['eligible'] and cap >= 1 and base['amount'] < 1:
            base = {**base, 'amount': 1., 'maximum_stake': cap,
                    'minimum_ticket_floor': {'version': 'authorized_minimum_v1',
                        'before_floor': base['amount'], 'amount': 1., 'available_cap': cap},
                    'binding_factors': [*base['binding_factors'], 'authorized_minimum_ticket_floor'],
                    'note': base['note'] + ' User-authorized $1 minimum ticket when affordable; this floor is not a positive-EV claim.'}
    if policy == 'legacy':
        return base
    evidence = tip.get('outcome_sizing_evidence')
    if not verify_summary(evidence) or evidence['segment'] != segment_key(tip):
        raise ValueError('Outcome sizing evidence is missing or invalid')
    # Normalize unused budget to the effective base cap. Historical replay
    # supplies this same recorded cap, not today's portfolio or bankroll.
    cap = base['maximum_stake']
    return apply_adjustment(base, tip, {**settings, 'max_bet': cap}, cap)
