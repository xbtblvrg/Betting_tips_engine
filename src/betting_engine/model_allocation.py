"""Atomic checks for opt-in, distinct-family nominal model allocations.

This is an allocation policy, not a change to probabilities, evidence quality,
or a claim that different market families are independent bets.
"""
import json
from collections import Counter

from .contracts import allocation_family, equivalent_contract_ids


def held_inventory(connection):
    rows = connection.execute(
        "SELECT tip_id,payload FROM bets WHERE stake>0 UNION "
        "SELECT tip_id,payload FROM system_decisions WHERE json_extract(payload,'$.stake')>0"
    ).fetchall()
    families = set()
    identities = set()
    exact_ids = set()
    for row in rows:
        tip = json.loads(row['payload'])
        identities.add(row['tip_id'])
        exact_ids.add(row['tip_id'])
        if tip.get('id') and tip.get('market'):
            identities.update(equivalent_contract_ids(tip))
        if tip.get('game_id') and tip.get('market'):
            families.add((tip['game_id'], allocation_family(tip)))
    return families, identities, exact_ids


def verify_family_publication(connection, candidates, settings):
    """Called inside publication's immediate transaction; writes nothing.

The same actual open-event exposure and remaining allocation used by sizing
are reread here. A concurrent ticket cannot leave a stale allocation funded.
"""
    from .decisions import remaining_budget, publication_limits
    from .funding_policy import size_recommendation
    from .instruction_refill import TransactionStore

    unique = {}
    for tip in candidates:
        if tip['id'] in unique and tip != unique[tip['id']]:
            raise ValueError('Publication blocked: conflicting copies of the same candidate')
        unique[tip['id']] = tip
    families, held_ids, exact_ids = held_inventory(connection)
    store = TransactionStore(connection)
    counts = store.held_event_counts()
    # Reproduce the same per-game budget that rank_and_size uses.  The old
    # guard only replayed the global remaining budget, so a second funded
    # contract on one game was sized against the full $5 cap instead of the
    # residual event cap.  That made an otherwise valid publication fail
    # closed with a misleading "related-game exposure" error.
    event_stakes = Counter(store.held_event_stakes())
    # Legacy fixtures intentionally have no publication-limits switch.  Keep
    # their uncapped sizing semantics; the live service opts into v1 explicitly.
    limits = publication_limits(settings)
    event_cap = (limits['max_stake_per_event'] if limits else float('inf'))
    new_event_stakes = Counter()
    remaining = remaining_budget(settings, store.risk_committed())
    funded = [tip for tip in unique.values() if tip.get('stake', 0) > 0]
    funded.sort(key=lambda tip: (tip.get('allocation_policy', {}).get('allocation_rank', 0), tip['id']))
    new_counts = Counter(tip['game_id'] for tip in funded
                         if not (equivalent_contract_ids(tip) & held_ids))
    seen_aliases = set()
    for tip in funded:
        aliases = equivalent_contract_ids(tip)
        if aliases & seen_aliases:
            raise ValueError('Publication blocked: duplicate equivalent allocation')
        seen_aliases.update(aliases)
    for tip in funded:
        aliases = equivalent_contract_ids(tip)
        if aliases & held_ids:
            if tip['id'] not in exact_ids:
                raise ValueError('Publication blocked: equivalent contract already issued under another identity')
            # An identical prior issuance is idempotent, not a new allocation.
            continue
        family = (tip['game_id'], allocation_family(tip))
        if tip.get('funding_basis', {}).get('tier') == 'model_pick' and family in families:
            raise ValueError('Publication blocked: model-pick family allocation already issued')
        related = counts.get(tip['game_id'], 0) + new_counts[tip['game_id']] - 1
        event_available = max(0.0, event_cap - event_stakes[tip['game_id']] - new_event_stakes[tip['game_id']])
        sizing_budget = min(remaining, event_available)
        reproduced = size_recommendation(tip, settings, sizing_budget, related)
        recorded = tip.get('stake_sizing') or {}
        # ``maximum_stake`` and the explanatory note include the temporary
        # remaining/event budget used while the batch was assembled.  They can
        # legitimately differ after a sibling allocation is removed, while
        # the actual USD amount and all exposure inputs remain identical.
        volatile = {'maximum_stake', 'note'}
        sizing_matches = (set(reproduced) | set(recorded)) - volatile
        def comparable(sizing,key):
            value=sizing.get(key)
            if key=='outcome_adjustment' and isinstance(value,dict):
                # The same effective remaining budget is diagnostic here;
                # amount, base amount, factor and source must still replay.
                value={k:v for k,v in value.items() if k not in ('caps','base_note')}
            return value
        if (any(comparable(reproduced,key) != comparable(recorded,key) for key in sizing_matches)
                or reproduced['amount'] != tip['stake']):
            mismatches = [key for key in sorted(sizing_matches)
                          if comparable(reproduced,key) != comparable(recorded,key)]
            raise ValueError('Publication blocked: related-game exposure or remaining allocation changed '
                             f"(tip={tip.get('id')}, actual={tip.get('stake')}, reproduced={reproduced.get('amount')}, "
                             f"remaining={remaining}, event_available={event_available}, related={related}, "
                             f"game={tip.get('game_id')}, held_event_stake={event_stakes.get(tip.get('game_id'), 0)}, "
                             f"mismatches={mismatches})")
        remaining -= tip['stake']
        new_event_stakes[tip['game_id']] += tip['stake']
        families.add(family)
        held_ids.update(aliases)
