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
    from .decisions import remaining_budget
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
        reproduced = size_recommendation(tip, settings, remaining, related)
        if reproduced != tip.get('stake_sizing') or reproduced['amount'] != tip['stake']:
            raise ValueError('Publication blocked: related-game exposure or remaining allocation changed')
        remaining -= tip['stake']
        families.add(family)
        held_ids.update(aliases)
