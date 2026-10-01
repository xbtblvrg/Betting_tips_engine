"""Exact website/feed conflicts established by a controlled UI observation.

Missing navigation alone is not a funding veto. These two concrete events were
checked on the actual website: the regulation market cannot be located in the
exposed tabs or their search, while the API advertises a hidden winner group.
Keep the original decisions; require a reviewed website recovery before lifting
these narrowly scoped restrictions. Do not substitute a period or Winner bet.
"""
from urllib.parse import urlsplit

OBSERVATION_ID = 'stake-navigation-20260930'
EVENT_SLUGS = frozenset({
    '46728056-vancouver-canucks-edmonton-oilers',
    '46728057-colorado-avalanche-los-angeles-kings',
})


def website_availability_issue(tip):
    if tip.get('league') != 'NHL' or tip.get('market') != 'regulation:double_chance':
        return None
    evidence = tip.get('stake_evidence') or {}
    slug = evidence.get('fixture_slug')
    try:
        url = urlsplit(tip.get('reference_url') or '')
        prefix = '/sports/ice-hockey/usa/nhl/'
        url_slug = url.path[len(prefix):] if url.scheme == 'https' and url.netloc == 'stake.com' and url.path.startswith(prefix) else None
    except (TypeError, ValueError):
        url_slug = None
    if slug not in EVENT_SLUGS and url_slug not in EVENT_SLUGS:
        return None
    return {
        'code': 'stake_website_market_unconfirmed',
        'observation_id': OBSERVATION_ID,
        'state': 'website_feed_conflict',
        'reason': 'The regulation Double Chance market is in the feed but could not be located on this Stake event page. Period Double Chance and Winner are different contracts. A verified website recovery is required.',
    }
