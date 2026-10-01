"""Passive catalogue context; never a quote, model or settlement approval.

Keep response metadata separate from the tournament schedule and bundle-level
checks. In particular, the selected endpoint's identity check does not validate
a companion response's team title, and market activity does not imply that the
fixture permits pregame betting. Unknown evidence stays unknown.
"""
from copy import deepcopy


def fixture_context(payload, schedule_fixture, audit=None, endpoint=None):
    """Return detached observed evidence without resolving names or timestamps.

    ``audit`` is the existing fixture_bundle result. Its checks concern the
    selected response; this context retains that distinction for every row.
    Exact startTime, status, flags and unknown provider fields remain raw data.
    No caller can authorize execution through this passive adapter.
    """
    audit = audit or {}
    identity = audit.get("identity_verified")
    return {
        "schema": "catalog-fixture-context-v1",
        "role": "catalogue_observation_not_execution_approval",
        "execution_authorized": False,
        "schedule_fixture": deepcopy(schedule_fixture),
        "response_fixture": deepcopy(payload.get("fixture")),
        "response_endpoint": endpoint,
        "bundle_selected_endpoint": audit.get("selected_endpoint"),
        "bundle_identity_verified": identity if type(identity) is bool else None,
        "bundle_seed_name_resolution": deepcopy(audit.get("seed_name_resolution")),
        "bundle_errors": deepcopy(audit.get("errors", [])),
    }
