"""Fail-closed current execution guidance, separate from immutable decisions."""
import hashlib
import json
import math

from .decisions import minimum_odds, sportsbook_ev, stake_assessment
from .decision_readiness import READINESS_VERSION
from .domain import parse_time
from .freshness import fresh_quote, MAX_QUOTE_AGE_SECONDS
from .governance import evidence_digest, verify_publication, SEALED_FIELDS

# Exact pre-readiness receipt format, retained only for original issued records.
LEGACY_SEALED_FIELDS = ('id','game_id','league','home','away','start','market','side','line','selection',
    'model_version','probability','push_probability','stress_probability','reference_odds',
    'expected_roi','stress_roi','minimum_odds','quote_at','valid_until','reference_platform',
    'reference_market_id','reference_contract_description','reference_url','stake_evidence','model_scope')


def _denied(state, reason):
    return {'state': state, 'actionable': False, 'reason': reason,
            'review_type': 'deterministic_evidence_and_calculation_checks', 'llm_approval': False}


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def current_execution_review(tip, now, minimum_roi, sizing_settings=None, max_quote_age=MAX_QUOTE_AGE_SECONDS):
    """Validate the current sealed model/quote, without authorizing a new ticket.

    A zero allocation is valid for an already held exact contract. The caller
    must separately require a funded new instruction or one unplaced issuance.
    No source fields or historical record are changed by this function.
    """
    try:
        if not isinstance(tip, dict) or not fresh_quote(tip, now, max_age_seconds=max_quote_age):
            return _denied('quote_unavailable', 'No fresh active exact-selection quote is available.')
        if tip.get('reference_platform') != 'Stake.com':
            return _denied('market_unverified', 'The exact Stake market is not verified.')
        from .stake_website_availability import website_availability_issue
        website_issue = website_availability_issue(tip)
        if website_issue:
            return {**_denied(website_issue['state'], website_issue['reason']),
                    'website_evidence': website_issue}
        if not _finite(minimum_roi) or minimum_roi < 0:
            return _denied('review_missing', 'The current model threshold is invalid.')
        readiness = tip.get('input_readiness', {})
        if (readiness.get('version') != READINESS_VERSION or readiness.get('passed') is not True
                or readiness.get('state') != 'ready' or tip.get('decision_guard', {}).get('input_readiness') != readiness):
            return _denied('internal_reassessment_required', 'The current sports inputs need a fresh readiness and model review.')
        # Recheck the seal, freshness and any nonzero allocation. A passed flag
        # alone cannot authorize mutated odds, probabilities, scope or line.
        verify_publication(tip, now, sizing_settings, max_quote_age=max_quote_age)
        guard = tip['decision_guard']
        policy=guard.get('recommendation_policy','strict')
        model_sizing=guard.get('model_pick_sizing_policy','unit_cap_v1')
        if sizing_settings is not None and policy!=sizing_settings.get('recommendation_policy','strict'):
            return _denied('policy_changed', 'The recommendation policy changed. A fresh sealed review is required.')
        if sizing_settings is not None and model_sizing!=sizing_settings.get('model_pick_sizing_policy','unit_cap_v1'):
            return _denied('policy_changed', 'The model-pick sizing policy changed. A fresh sealed review is required.')
        if not math.isclose(guard['minimum_roi'], minimum_roi, abs_tol=1e-12, rel_tol=0):
            return _denied('policy_changed', 'The model threshold changed. A fresh evidence review is required.')
        if sizing_settings is not None:
            policy_keys = ('bankroll', 'unit_stake', 'max_bet', 'kelly_fraction', 'max_bankroll_fraction')
            if 'fallback_clv_gate' in sizing_settings or 'fallback_clv_gate' in guard.get('sizing_settings', {}):
                policy_keys += ('fallback_clv_gate', 'clv_min_games')
            reviewed_settings = guard.get('sizing_settings', {})
            if any(k not in reviewed_settings or k not in sizing_settings
                   or reviewed_settings[k] != sizing_settings[k] for k in policy_keys):
                return _denied('policy_changed', 'The configured stake policy changed. A fresh sealed model and sizing review is required.')
            if sizing_settings.get('publication_limits_version'):
                from .decisions import publication_limit_receipt
                expected_limits=publication_limit_receipt(sizing_settings)
                if guard.get('publication_limits')!=expected_limits:
                    return _denied('policy_changed','The live publication exposure limits changed. A fresh sealed review is required.')
        allocation_policy=guard.get('model_pick_allocation_policy','one_per_game_v1')
        if sizing_settings is not None and allocation_policy!=sizing_settings.get('model_pick_allocation_policy','one_per_game_v1'):
            return _denied('policy_changed','The model-pick allocation policy changed. A fresh sealed review is required.')
        evidence = tip.get('stake_evidence', {})
        if (not all(isinstance(evidence.get(k), str) and evidence[k] for k in ('fixture_slug', 'market_name', 'outcome_name'))
                or evidence.get('fetched_at') != tip['quote_at']
                or not _finite(evidence.get('decimal_odds'))
                or not math.isclose(evidence['decimal_odds'], tip['reference_odds'], abs_tol=1e-9, rel_tol=0)):
            return _denied('market_unverified', 'The exact Stake selection and price evidence are incomplete.')
        keys = ('probability', 'stress_probability', 'push_probability', 'reference_odds',
                'expected_roi', 'stress_roi', 'minimum_odds', 'stake')
        if not all(_finite(tip.get(k)) for k in keys):
            return _denied('invalid_values', 'The model, quote or allocation contains invalid values.')
        p, stress, push, odds = (tip[k] for k in ('probability', 'stress_probability', 'push_probability', 'reference_odds'))
        if (not 0 <= stress <= p <= 1 or not 0 <= push <= 1 or p + push > 1 + 1e-9 or odds <= 1
                or (tip['stake'] != 0 and not 0.5 <= tip['stake'] <= 8)
                or (tip.get('line') is not None and not _finite(tip['line']))
                or not tip.get('model_version') or guard.get('model', {}).get('version') != tip['model_version']):
            return _denied('invalid_values', 'The model, quote or allocation is inconsistent.')
        expected = {'expected_roi': sportsbook_ev(p, push, odds), 'stress_roi': sportsbook_ev(stress, push, odds),
                    'minimum_odds': minimum_odds(stress, push, minimum_roi)}
        if any(not math.isclose(tip[k], value, abs_tol=1e-9, rel_tol=0) for k, value in expected.items()):
            return _denied('invalid_values', 'The current probability, value and minimum odds do not reproduce.')
        from .funding_policy import funding_basis
        basis_settings={**guard['sizing_settings'],'minimum_roi':minimum_roi,'recommendation_policy':policy,'model_pick_sizing_policy':model_sizing,'model_pick_allocation_policy':allocation_policy,'family_selection_policy':guard.get('family_selection_policy','stressed_value_v1')}
        basis=funding_basis(tip,basis_settings)
        if not basis['eligible']:
            return _denied('value_below_threshold', 'The current model assessment and exact-selection price do not meet the value threshold.')
        return {'state': 'current', 'actionable': True,
                'reason': 'The fresh exact Stake selection passes the current sealed model and price checks.',
                'quote_at': tip['quote_at'], 'valid_until': tip['valid_until'], 'odds': odds,
                'minimum_odds': basis['minimum_odds'], 'probability': p, 'stress_probability': stress,
                'funding_tier':basis['tier'],'recommendation_policy':policy,'model_pick_sizing_policy':model_sizing,
                **({'family_selection_policy':guard['family_selection_policy']} if 'family_selection_policy' in guard else {}),
                **({'outcome_sizing_policy':guard['outcome_sizing_policy']} if 'outcome_sizing_policy' in guard else {}),
                **({'model_pick_allocation_policy':allocation_policy} if allocation_policy!='one_per_game_v1' else {}),
                'push_probability': push, 'expected_roi': tip['expected_roi'], 'stress_roi': tip['stress_roi'],
                'model_version': tip['model_version'],
                'review_type': 'deterministic_evidence_and_calculation_checks', 'llm_approval': False}
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
        return _denied('review_missing', 'The current sealed model, price or sizing evidence does not validate.')


def preference_execution_review(tip,review,allowed_ids):
    """Withdraw superseded probability guidance without changing its ledger."""
    if review.get('actionable') and (tip or {}).get('funding_basis',{}).get('tier')=='probability_pick' and tip.get('id') not in allowed_ids:
        return _denied('preference_superseded','A better current or previously issued model choice supersedes this probability-only instruction. The original record remains tracked.')
    return review


def _original_budget(original, issued_at):
    """Verify an immutable historical budget without requiring a new seal format.

    v1 sizing is retained as the historical policy; fresh v2 sizing below must
    independently support its amount. A missing original seal is never guessed.
    """
    guard = original['decision_guard']
    expected = evidence_digest(original)
    legacy = hashlib.sha256(json.dumps({k: original.get(k) for k in LEGACY_SEALED_FIELDS},
                                      sort_keys=True, allow_nan=False).encode()).hexdigest()
    format_name = 'current_seal'
    if guard.get('candidate_sha256') != expected:
        # Historical schemas are exact code versions, not a relaxed current seal.
        # 6f18869 added anchor keys and 8a8fcac added calibration keys
        # unconditionally; their null fields changed earlier immutable hashes.
        formats = (
            ('pre_anchor_seal', '2026-10-02T17:39:48+00:00',
             ('market_anchor','probability_basis','calibration_uncertainty','model_trained_at')),
            ('pre_calibration_seal', '2026-10-02T18:06:19+00:00',
             ('calibration_uncertainty','model_trained_at')),
        )
        historical_format = None
        new_policies = ('family_selection_policy','probability_support_policy',
                        'outcome_sizing_policy','outcome_sizing_evidence',
                        'composite_contract_policy','composite_contract_evidence','event_limit_evidence')
        for name, cutoff, omitted in formats:
            if (issued_at > parse_time(cutoff)
                    or any(k in original or k in guard for k in omitted + new_policies)):
                continue
            fields = {k: original.get(k) for k in SEALED_FIELDS if k not in omitted}
            if 'funding_basis' in original:
                fields['funding_basis'] = original['funding_basis']
            digest = hashlib.sha256(json.dumps(fields,sort_keys=True,allow_nan=False).encode()).hexdigest()
            if guard.get('candidate_sha256') == digest:
                historical_format = name
                break
        if historical_format:
            format_name = historical_format
        elif ('input_readiness' not in original and 'input_readiness' not in guard
              and guard.get('candidate_sha256') == legacy):
            format_name = 'legacy_pre_readiness_seal'
        else:
            raise ValueError('Original evidence seal does not validate')
    if guard.get('passed') is not True or not fresh_quote(original, issued_at):
        raise ValueError('Original issuance was not reviewed while fresh')
    sizing = original['stake_sizing']
    max_stake_cap = max(5.0, float((guard.get('sizing_settings') or {}).get('max_bet', 8.0)))
    if (sizing['version'] not in ('stress-kelly-unit-v1', 'stress-kelly-unit-v2', 'model-pick-unit-v1', 'model-pick-unit-v2', 'model-pick-unit-v3', 'probability-pick-unit-v1', 'probability-pick-unit-v3')
            or sizing['amount'] != original['stake'] or not _finite(sizing['maximum_stake'])
            or not original['stake'] <= sizing['maximum_stake'] <= max_stake_cap
            or any(not _finite(sizing[k]) or not _finite(original[k])
                   or not math.isclose(sizing[k], original[k], abs_tol=1e-9, rel_tol=0)
                   for k in ('stress_probability', 'push_probability', 'stress_roi'))
            or not math.isclose(sizing['odds'], original['reference_odds'], abs_tol=1e-9, rel_tol=0)):
        raise ValueError('Original sizing does not match the immutable decision')
    settings = {'bankroll': sizing['bankroll'], 'unit_stake': sizing['base_unit'],
        'max_bet': sizing['maximum_stake'], 'kelly_fraction': sizing['kelly_fraction'],
        'max_bankroll_fraction': sizing['max_bankroll_fraction']}
    reviewed = guard.get('sizing_settings')
    if reviewed and (any(settings[k] != reviewed[k] for k in ('bankroll','unit_stake','kelly_fraction','max_bankroll_fraction'))
                     or settings['max_bet'] > reviewed['max_bet']):
        raise ValueError('Original sizing settings changed after issuance review')
    from .outcome_sizing_runtime import outcome_policy,verify_evidence
    outcome=outcome_policy(guard)
    if outcome!=outcome_policy(original) or outcome!=outcome_policy(guard.get('sizing_settings',{})):raise ValueError('Original outcome sizing policy mismatch')
    verify_evidence(original,guard,issued_at)
    if sizing['version'] in ('model-pick-unit-v1','model-pick-unit-v2','model-pick-unit-v3','probability-pick-unit-v1','probability-pick-unit-v3') or outcome!='legacy':
        from .funding_policy import funding_basis,size_recommendation
        settings['family_selection_policy']=guard.get('family_selection_policy','stressed_value_v1')
        settings.update(minimum_roi=guard['minimum_roi'],recommendation_policy=guard.get('recommendation_policy','strict'),model_pick_sizing_policy=guard.get('model_pick_sizing_policy','unit_cap_v1'),model_pick_allocation_policy=guard.get('model_pick_allocation_policy','one_per_game_v1'))
        for key in ('fallback_clv_gate','clv_min_games','outcome_sizing_policy','family_selection_policy','min_odds','capital_defense','alpha_acceleration'):
            if key in guard.get('sizing_settings', {}): settings[key]=guard['sizing_settings'][key]
        if (settings['recommendation_policy']!='strict' or 'funding_basis' in original) and original.get('funding_basis')!=funding_basis(original,{**settings,'max_bet':guard['sizing_settings']['max_bet']}):
            raise ValueError('Original funding policy does not reproduce')
        rem_cap=sizing['maximum_stake']
        if settings.get('alpha_acceleration') and sizing.get('outcome_adjustment',{}).get('caps',{}).get('remaining') is not None:
            rem_cap=max(rem_cap,sizing['outcome_adjustment']['caps']['remaining'])
        if settings.get('alpha_acceleration') and guard.get('sizing_settings',{}).get('max_bet'):
            settings['max_bet']=max(settings['max_bet'],guard['sizing_settings']['max_bet'])
        reproduced=size_recommendation(original,settings,rem_cap,sizing['related_contracts'])
    else:
        reproduced = stake_assessment(original['stress_probability'], original['push_probability'],
            original['reference_odds'], settings, sizing['maximum_stake'], sizing['related_contracts'])
    # v1 and v2 share the numeric unit/Kelly formula. v2 added diagnostic
    # fields, stricter validation and different prose; never rewrite v1 into v2.
    if sizing['version'] in ('stress-kelly-unit-v2','model-pick-unit-v1','model-pick-unit-v2','model-pick-unit-v3','probability-pick-unit-v1','probability-pick-unit-v3') or outcome!='legacy':
        valid = reproduced == sizing
    else:
        keys = ('amount','mode','full_kelly_fraction','related_contracts','event_discount','reference_kelly_fraction',
                'bankroll','base_unit','kelly_fraction','max_bankroll_fraction','maximum_stake','personalized')
        valid = all(sizing.get(k) == reproduced[k] for k in keys)
    if not valid:
        raise ValueError('Original numeric stake policy does not reproduce')
    return format_name


def instruction_review(record, current, now, minimum_roi, related_contracts=None, sizing_settings=None, max_quote_age=MAX_QUOTE_AGE_SECONDS):
    """Keep one original issued stake only while the exact current bet supports it."""
    try:
        original = record['payload']
        if record['status'] != 'open':
            return _denied('settled', 'This recorded bet is settled.')
        if parse_time(original['start']) <= now:
            return _denied('started', 'The match has started. This pregame instruction is historical.')
        if original.get('tracking_source') != 'auto_assumed_placed':
            return _denied('already_recorded', 'This manually recorded bet is not a new betting instruction.')
        if original.get('league') == 'CHALLENGER':
            return _denied('challenger_tour_quarantined', 'Challenger tennis is 100% quarantined to research. Capital is reserved for ATP, WTA, and major sports.')
        if original.get('league') in ('ATP', 'WTA'):
            if '/' in str(original.get('home', '')) or '/' in str(original.get('away', '')):
                return _denied('doubles_unsupported', 'Doubles tennis matches are not supported by the single-player Markov model.')
        review = current_execution_review(current, now, minimum_roi, sizing_settings, max_quote_age=max_quote_age)
        if review['state'] in ('current', 'value_below_threshold') and parse_time(current['quote_at']) < parse_time(original['quote_at']):
            return _denied('assessment_stale', 'The current quote predates the issued instruction. A newer exact-selection check is required.')
        if not review['actionable']:
            if review['state'] == 'value_below_threshold':
                changed = {k: {'issued': original.get(k), 'current': current.get(k)}
                    for k in ('model_version', 'probability', 'stress_probability', 'push_probability')
                    if original.get(k) != current.get(k)}
                if changed:
                    return {**_denied('model_assessment_changed', 'The model or its inputs changed the assessment; this exact selection no longer meets the value threshold.'),
                            'assessment_changes': changed,
                            'issued_odds': original.get('reference_odds'), 'current_odds': current.get('reference_odds')}
                return {**_denied('price_below_threshold', 'The current exact-selection odds no longer meet the unchanged model assessment.'),
                        'issued_odds': original.get('reference_odds'), 'current_odds': current.get('reference_odds')}
            return review
        keys = ('id', 'game_id', 'league', 'home', 'away', 'market', 'side', 'line', 'start')
        if (record.get('tip_id') != original.get('id')
                or any(original.get(k) != current.get(k) for k in keys)
                or original.get('model_scope', 'full_game') != current.get('model_scope', 'full_game')):
            return _denied('contract_changed', 'The current selection does not match the original exact recorded contract.')
        amount = record.get('stake')
        if (not _finite(amount) or not 0.5 <= amount <= 8 or not _finite(original.get('stake'))
                or original.get('stake') != amount or not _finite(record.get('odds'))
                or record.get('odds') != original.get('reference_odds')):
            return _denied('original_stake_unverified', 'The original single issued stake and odds do not validate.')
        # Verify the original allocation at its actual issuance time, not at
        # today's time, and retain the frozen amount even when the price moves.
        revision = original.get('execution_revision', {})
        issued_at = parse_time(revision.get('revised_at', record['created_at']))
        if issued_at > now:
            return _denied('original_stake_unverified', 'The original issuance timestamp is invalid.')
        original_format = _original_budget(original, issued_at)
        sizing = original['stake_sizing']
        settings = current['decision_guard']['sizing_settings']
        related = sizing['related_contracts']
        if related_contracts is not None:
            if not _finite(related_contracts) or related_contracts < 0 or int(related_contracts) != related_contracts:
                raise ValueError('Invalid current related exposure')
            related = max(related, related_contracts)
        policy=current['decision_guard'].get('recommendation_policy','strict')
        if policy!='strict' or current['decision_guard'].get('outcome_sizing_policy','legacy')!='legacy':
            from .funding_policy import size_recommendation
            settings={**settings,'minimum_roi':minimum_roi,'recommendation_policy':policy,'model_pick_sizing_policy':current['decision_guard'].get('model_pick_sizing_policy','unit_cap_v1'),'model_pick_allocation_policy':current['decision_guard'].get('model_pick_allocation_policy','one_per_game_v1'),'family_selection_policy':current['decision_guard'].get('family_selection_policy','stressed_value_v1')}
            for key in ('capital_defense', 'alpha_acceleration'):
                if key in current['decision_guard'].get('sizing_settings', {}):
                    settings[key] = current['decision_guard']['sizing_settings'][key]
                elif sizing_settings is not None and key in sizing_settings:
                    settings[key] = sizing_settings[key]
            rem_cap=sizing['maximum_stake']
            if settings.get('alpha_acceleration') and sizing.get('outcome_adjustment',{}).get('caps',{}).get('remaining') is not None:
                rem_cap=max(rem_cap,sizing['outcome_adjustment']['caps']['remaining'])
            if settings.get('alpha_acceleration') and current['decision_guard'].get('sizing_settings',{}).get('max_bet'):
                settings['max_bet']=max(settings['max_bet'],current['decision_guard']['sizing_settings']['max_bet'])
            supported=size_recommendation(current,settings,rem_cap,related)
        else:
            supported = stake_assessment(current['stress_probability'], current['push_probability'],
                current['reference_odds'], settings, sizing['maximum_stake'], related)
        if supported['amount'] + 1e-9 < amount:
            return _denied('stake_reassessment_required', 'The current model no longer supports the original issued stake. The record remains unchanged.')
        return {**review, 'stake': amount, 'stake_basis': ('authorized_unplaced_revision' if revision else 'original_single_issuance'),
                **({'execution_revision': revision} if revision else {}),
                'current_supported_stake': supported['amount'], 'original_evidence_format': original_format,
                'repeat_bet': False}
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
        return _denied('original_stake_unverified', 'The original contract or sizing evidence does not validate.')
