"""Hashable identity for raw JSON observations, never execution approval."""
import json


def inventory_key(market_id, name, specifiers):
    # JSON preserves object/list/scalar distinctions, including a string that
    # merely looks like JSON. Sorting object keys deduplicates the same raw
    # observation across feed groups without rewriting its retained evidence.
    return json.dumps([market_id, name, specifiers], sort_keys=True,
                      separators=(',', ':'), ensure_ascii=True)


def specifier_rejection(specifiers):
    if specifiers is not None and not isinstance(specifiers, str):
        return 'invalid_specifiers_type'
    return None
