"""Pure, revisioned player-prop payout decisions; never updates a betting ledger.

Input observations/rules must come from separately verified source adapters.
This module verifies supplied source bytes, exact normalized identities and
policy, not an API caller's authority or the truth of an arbitrary JSON file.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import re
from urllib.parse import urlparse

from .domain import parse_time, utcnow
from .player_prop_contracts import canonical_hash, number, numeric_text, SEMANTIC_FIELDS

VERSION = 'player-prop-settlement-v1'
IDENTITY = ('league', 'fixture_id', 'player_id', 'metric', 'scope',
            'includes_overtime', 'includes_shootout')
OFFICIAL_HOSTS = {'NBA': ('nba.com',), 'WNBA': ('wnba.com',), 'NFL': ('nfl.com',),
                 'NHL': ('nhl.com',), 'NCAAM': ('ncaa.com', 'ncaa.org'),
                 'NCAAW': ('ncaa.com', 'ncaa.org'), 'NCAAF': ('ncaa.com', 'ncaa.org')}
SIGNED_METRICS = {'plus_minus', 'passing_yards', 'rushing_yards', 'receiving_yards'}


def _source_errors(source, bodies, now, *, league=None, rules=False):
    if not isinstance(source, dict): return ['missing_source']
    errors = []
    digest = source.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        errors.append('invalid_source_hash')
    body = bodies.get(digest)
    if not isinstance(body, bytes) or hashlib.sha256(body).hexdigest() != digest:
        errors.append('source_bytes_missing_or_changed')
    url = urlparse(source.get('url', '')); host = url.hostname or ''
    allowed = ('stake.com',) if rules else OFFICIAL_HOSTS.get(league, ())
    if url.scheme != 'https' or url.username or url.password or not any(host == h or host.endswith('.'+h) for h in allowed):
        errors.append('unapproved_source_host')
    if rules and url.path != '/policies/sportsbook': errors.append('unverified_rule_document')
    try:
        if parse_time(source['fetched_at']) > now: errors.append('future_source_snapshot')
    except (KeyError, TypeError, ValueError): errors.append('missing_source_timestamp')
    return errors


def settle_player_prop(ticket, observation, rules, *, evidence_bodies, as_of=None, previous=None):
    """Return a source-bound assessment or explicit pending reasons.

    Money uses exact decimal arithmetic. A later stat correction creates a new
    assessment linked to the previous one; ticket, stake and accepted odds are
    unchanged. Missing stats, DNP policy and cancellation evidence never mean 0.
    """
    now = parse_time(as_of) if isinstance(as_of, str) else as_of or utcnow()
    original = deepcopy(ticket); contract = original.get('contract', {})
    ticket_hash = canonical_hash(original)
    base = {'version': VERSION, 'ticket_id': original.get('id'), 'ticket_sha256': ticket_hash,
            'contract_id': contract.get('contract_id'), 'state': 'pending_review', 'result': None,
            'payout': None, 'profit': None, 'assessed_at': now.isoformat(),
            'previous_assessment_sha256': previous.get('assessment_sha256') if previous else None,
            'observation_sha256': canonical_hash(observation) if observation is not None else None,
            'rules_sha256': canonical_hash(rules) if rules is not None else None,
            'original_ticket_unchanged': True}
    reasons = []
    def finish():
        base['reasons'] = list(dict.fromkeys(reasons))
        base['assessment_sha256'] = canonical_hash(base)
        return base
    if not isinstance(contract, dict) or contract.get('normalization') != 'normalized':
        reasons.append('contract_not_normalized'); return finish()
    if (canonical_hash({k: contract.get(k) for k in SEMANTIC_FIELDS}) != contract.get('contract_sha256')
            or contract.get('contract_id') != contract.get('contract_sha256')):
        reasons.append('contract_changed'); return finish()
    if any(contract.get(k) is None for k in IDENTITY):
        reasons.append('incomplete_contract_identity'); return finish()
    try:
        stake = number(original['stake']); odds = number(original['odds'])
        if stake <= 0 or odds <= 1: raise ValueError('Invalid accepted money')
        if not original.get('id') or parse_time(original['placed_at']) > now:
            raise ValueError('Invalid ticket time/identity')
        line = number(contract['line'])
    except (KeyError, TypeError, ValueError):
        reasons.append('invalid_original_ticket'); return finish()
    base.update(stake=numeric_text(stake), accepted_odds=numeric_text(odds))
    if not isinstance(rules, dict): reasons.append('missing_settlement_rules'); return finish()
    reasons.extend(_source_errors(rules.get('source'), evidence_bodies, now, rules=True))
    if any(rules.get(k) != contract[k] for k in ('league', 'metric', 'scope', 'includes_overtime', 'includes_shootout')):
        reasons.append('rule_contract_mismatch')
    if rules.get('participation_requirement') not in ('played', 'started'):
        reasons.append('unknown_participation_rule')
    if rules.get('no_participation') not in ('void', 'pending_review'):
        reasons.append('unknown_dnp_rule')
    if rules.get('stat_corrections') not in ('apply_official', 'manual_review'):
        reasons.append('unknown_stat_correction_rule')
    if contract.get('operator') in ('over', 'under') and rules.get('integer_equality') != 'push':
        reasons.append('unknown_integer_push_rule')
    if contract.get('operator') not in ('over', 'under', 'at_least', 'yes', 'no'):
        reasons.append('unknown_payoff_operator')
    if not isinstance(observation, dict):
        reasons.append('missing_player_observation'); return finish()
    reasons.extend(_source_errors(observation.get('source'), evidence_bodies, now, league=contract['league']))
    if observation.get('entity_type') != 'player': reasons.append('player_statistics_required')
    if observation.get('official') is not True: reasons.append('official_player_statistics_required')
    if any(observation.get(k) != contract[k] for k in IDENTITY): reasons.append('observation_identity_mismatch')
    try:
        available = parse_time(observation['available_at'])
        fetched = parse_time(observation['source']['fetched_at'])
        if available > now or available > fetched: reasons.append('future_player_observation')
    except (KeyError, TypeError, ValueError): reasons.append('missing_observation_timestamp')
    revision = observation.get('revision')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        reasons.append('invalid_stat_revision')
    if previous:
        try:
            if parse_time(previous['assessed_at']) > now: reasons.append('future_previous_assessment')
        except (KeyError, TypeError, ValueError): reasons.append('invalid_previous_assessment_time')
        if previous.get('ticket_sha256') != ticket_hash: reasons.append('original_ticket_changed')
        prior_payload = {k: v for k, v in previous.items() if k != 'assessment_sha256'}
        if canonical_hash(prior_payload) != previous.get('assessment_sha256'): reasons.append('previous_assessment_changed')
        if previous.get('observation_sha256') == base['observation_sha256'] and previous.get('rules_sha256') == base['rules_sha256']:
            # Idempotent exact repeat is safe only after verifying original
            # ticket, previous assessment and currently supplied evidence.
            if not reasons: return deepcopy(previous)
        elif previous.get('result') in ('win', 'loss', 'push', 'void'):
            if previous.get('rules_sha256') != base['rules_sha256']:
                reasons.append('settled_rule_change_requires_review')
            if rules.get('stat_corrections') != 'apply_official': reasons.append('stat_correction_requires_review')
            if observation.get('supersedes_observation_sha256') != previous.get('observation_sha256'):
                reasons.append('unlinked_stat_correction')
            if not isinstance(revision, int) or revision <= previous.get('stat_revision', 0):
                reasons.append('nonincreasing_stat_revision')
            try:
                if available < parse_time(previous['stat_available_at']): reasons.append('backdated_stat_correction')
            except (KeyError, TypeError, ValueError): reasons.append('missing_correction_timestamp')
    if reasons: return finish()
    base.update(stat_revision=revision, stat_available_at=available.isoformat())
    status = observation.get('event_status')
    if status in ('cancelled', 'abandoned'):
        if rules.get(status) == 'void' and observation.get('cancellation_final') is True:
            result = 'void'
        else:
            reasons.append('event_cancellation_requires_review'); return finish()
    else:
        participation = observation.get('participation', {})
        eligibility = participation.get(rules['participation_requirement'])
        if not isinstance(eligibility, bool):
            reasons.append('unknown_player_participation'); return finish()
        if eligibility is False:
            # Absence during an unfinished game is not a confirmed DNP.
            if status != 'final' or participation.get('final') is not True:
                reasons.append('participation_not_final'); return finish()
            if rules['no_participation'] != 'void':
                reasons.append('dnp_requires_review'); return finish()
            result = 'void'
        else:
            if status != 'final' or observation.get('scope_complete') is not True:
                reasons.append('player_scope_not_final'); return finish()
            try:
                value = number(observation.get('value'))
                if value != value.to_integral_value(): raise ValueError('Player count/yard statistics must be integral')
                if value < 0 and contract['metric'] not in SIGNED_METRICS: raise ValueError('Negative count')
            except ValueError:
                reasons.append('missing_or_invalid_player_stat'); return finish()
            base['stat_value'] = numeric_text(value)
            operator = contract['operator']
            if operator in ('yes', 'no'):
                if line != 1 or contract['metric'] not in ('touchdowns_scored', 'shutouts', 'goalie_win'):
                    reasons.append('unknown_binary_metric_definition'); return finish()
                result = 'win' if (value >= 1) == (operator == 'yes') else 'loss'
            elif operator == 'at_least': result = 'win' if value >= line else 'loss'
            elif value == line: result = 'push'
            elif operator == 'over': result = 'win' if value > line else 'loss'
            else: result = 'win' if value < line else 'loss'
    payout = stake * odds if result == 'win' else stake if result in ('push', 'void') else Decimal(0)
    base.update(state='settled', result=result, payout=numeric_text(payout), profit=numeric_text(payout-stake))
    return finish()
