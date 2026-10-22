"""Read-only current sizing of recorded contracts; never an order or revision.

The recorded stake is an execution assumption unless a user receipt says
otherwise. An unchecked personal checklist does not resolve that ambiguity.
"""
from collections import defaultdict
from datetime import timedelta
import hashlib
import json
import math

from .decisions import remaining_budget
from .domain import parse_time
from .funding_policy import size_recommendation
from .instructions import current_execution_review, preference_execution_review


def assess_existing_stakes(records, current_by_id, settings, now,
                           event_counts, probability_ids, committed=0., marks=None, original_issuances=None):
    """Return exact-contract hypothetical total targets without mutating inputs."""
    grouped = defaultdict(list)
    for record in records:
        if record.get('status') == 'open':
            grouped[record['tip_id']].append(record)
    rows = []
    for tip_id, tickets in grouped.items():
        original = tickets[0].get('payload', {})
        current = current_by_id.get(tip_id)
        row = {
            'version': 'existing-stake-assessment-v1', 'tip_id': tip_id,
            'bet_ids': [b['id'] for b in tickets],
            **{k: original.get(k) for k in ('league', 'game_id', 'home', 'away', 'start', 'market', 'side', 'line', 'model_scope', 'period', 'selection')},
            'market_display': original.get('market_display', original.get('market')),
            'tracking_sources': sorted({b.get('payload', {}).get('tracking_source', 'manual') for b in tickets}),
            'marked_placed': bool((marks or {}).get(tip_id)),
            'assessed_at': now.isoformat(), 'eligible': False,
            'target_stake': None, 'difference': None, 'original_stake': None,
            'recorded_stake': None, 'new_bet_instruction': False,
            'model_pick_sizing_policy': settings.get('model_pick_sizing_policy', 'unit_cap_v1'),
            **({'outcome_sizing_policy':settings['outcome_sizing_policy']} if 'outcome_sizing_policy' in settings else {}),
            'note': 'Current total sizing assessment only. The difference is not an additional betting instruction. Original records and daily issuance counts are unchanged.',
        }
        try:
            amounts = [b['stake'] for b in tickets]
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0 for v in amounts):
                raise ValueError('Recorded stake is invalid.')
            row['recorded_stake'] = round(sum(amounts), 2)
            # One immutable first issuance per contract, not one per ticket.
            # Duplicated/manual receipt payloads cannot establish this amount.
            issued = (original_issuances or {}).get(tip_id)
            if not isinstance(issued, bool) and isinstance(issued, (float, int)) and math.isfinite(issued) and 0.5 <= issued <= 8:
                row['original_stake'] = issued
            if not now < parse_time(original['start']) <= now + timedelta(hours=48):
                raise ValueError('The recorded match is outside the current pregame 48-hour window.')
            review = preference_execution_review(current,
                current_execution_review(current, now, settings['minimum_roi'], settings), probability_ids)
            if not review['actionable']:
                raise ValueError(review['reason'])
            identity = ('id', 'game_id', 'league', 'home', 'away', 'start', 'market', 'side', 'line')
            for ticket in tickets:
                payload = ticket['payload']
                if (ticket['tip_id'] != payload.get('id')
                        or any(payload.get(k) != current.get(k) for k in identity)
                        or payload.get('model_scope', 'full_game') != current.get('model_scope', 'full_game')
                        or parse_time(current['quote_at']) < parse_time(payload['quote_at'])):
                    raise ValueError('The current exact contract does not match every original recorded ticket.')
            count = event_counts.get(current['game_id'], 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError('Current recorded event exposure is unavailable.')
            related = count - 1  # Exclude this exact contract, not its whole event.
            remaining = remaining_budget(settings, max(0., committed - row['recorded_stake']))
            sizing = size_recommendation(current, settings, remaining, related)
            if not 0.5 <= sizing['amount'] <= min(8., settings['max_bet']):
                raise ValueError('Current sizing does not support a USD0.50 minimum ticket.')
            row.update(eligible=True, reason='Fresh exact contract and current sizing calculation verified.',
                target_stake=sizing['amount'], difference=round(sizing['amount'] - row['recorded_stake'], 2),
                quote_at=current['quote_at'], valid_until=current['valid_until'], odds=current['reference_odds'],
                market_display=current.get('market_display', row['market_display']),
                sizing=sizing, funding_basis=current.get('funding_basis'),
                execution_review=review, current_candidate_sha256=current['decision_guard']['candidate_sha256'],
                current_model_version=current['model_version'])
        except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError) as exc:
            row['reason'] = str(exc) or 'Current sizing evidence is unavailable.'
        from .presentation import english_payload
        row = english_payload(row)
        row['assessment_sha256'] = hashlib.sha256(json.dumps(row, sort_keys=True, allow_nan=False).encode()).hexdigest()
        rows.append(row)
    return sorted(rows, key=lambda r: (r.get('start') or '', r['tip_id']))
