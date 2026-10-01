"""Rolling coverage from the same publication as the model decisions.

Observed offers are not a count of independent bets. Swish prices have no
validated availability/settlement mapping and never enter the active count.
"""
from collections import Counter, defaultdict
from datetime import timedelta
import math
from .domain import parse_time

from .stake_api import ALL_LEAGUES as LEAGUES


def active_standard_outcomes(market):
    if market.get('status') not in (None, 'active'):
        return 0
    return sum(
        o.get('active') is True and o.get('status') in (None, 'active')
        and type(o.get('odds')) in (int, float)
        and math.isfinite(o['odds']) and o['odds'] > 1
        for o in market.get('outcomes') or []
    )


def inventory_snapshot(fixtures, markets, schedule_counts, issues):
    """Compact per-fixture evidence, committed atomically with recommendations."""
    grouped = defaultdict(list)
    for m in markets:
        grouped[(m['league'], m['fixture_id'])].append(m)
    rows = []
    seen = set()
    for fixture in fixtures:
        key = (fixture['league'], fixture['fixture_id'])
        if key in seen:
            continue
        seen.add(key)
        buckets = {}
        for m in grouped[key]:
            group = (m['fetched_at'], m['reason'])
            bucket = buckets.setdefault(group, dict(observed=0, active_standard=0,
                swish_unverified=0, supported=0, fetched_at=m['fetched_at'], reason=m['reason']))
            bucket['observed'] += m['outcome_count']
            bucket['active_standard'] += m.get('active_outcomes', 0)
            bucket['supported'] += m.get('supported_outcomes', 0)
            if m.get('provider_section', '').startswith('swishMarkets.'):
                bucket['swish_unverified'] += m['outcome_count'] - m.get('invalid_prices', 0)
        rows.append({**fixture, 'counts': list(buckets.values())})
    return {'fixtures': rows, 'schedule_counts': schedule_counts,
            'issues': issues, 'scope': 'Connected prematch feed; not complete website coverage'}


def rolling_opportunities(payload, recorded, marks, now, minimum_roi):
    snapshot = payload.get('opportunity_inventory')
    if snapshot is None:
        return {'state': 'awaiting_publication', 'windows': {}}
    current = {t['id']: t for t in payload.get('evaluated_candidates', [])
               if not t.get('expired') and parse_time(t['valid_until']) > now}
    selected = {t['id']: t for t in payload.get('recommendations', []) + payload.get('alternatives', [])
                if t['id'] in current}
    held = {b['tip_id']: b['payload'] for b in recorded}
    windows = {}
    for hours in (24, 48):
        end = now + timedelta(hours=hours)
        def inside(t):
            return bool(t.get('start')) and now < parse_time(t['start']) <= end
        rows = []
        for league in sorted(set(LEAGUES)|set(snapshot['schedule_counts'])|{f['league'] for f in snapshot['fixtures']}):
            fixtures = [f for f in snapshot['fixtures'] if f['league'] == league and inside(f)]
            counts = [m for f in fixtures for m in f['counts']]
            fresh = [m for f in fixtures if f['active'] for m in f['counts']
                     if 0 <= (now - parse_time(m['fetched_at'])).total_seconds() <= 300]
            modeled = [t for t in current.values() if t.get('league') == league and inside(t)]
            best = [t for t in selected.values() if t.get('league') == league and inside(t)]
            issued = [t for t in held.values() if t.get('league') == league and inside(t)]
            reasons = Counter()
            for m in counts:
                if m['observed'] > m['supported']:
                    reasons[m['reason']] += m['observed'] - m['supported']
            errors = [i for i in snapshot['issues'] if i.get('league') == league]
            rows.append({
                'league': league,
                'source_state': 'unavailable' if league not in snapshot['schedule_counts'] else 'partial' if errors else 'observed',
                'calendar_games': snapshot['schedule_counts'].get(league),
                'games': len(fixtures), 'matched_games': sum(f['matched'] for f in fixtures),
                'odds_loaded_games': sum(f.get('odds_loaded', False) for f in fixtures),
                'observed_outcomes': sum(m['observed'] for m in counts),
                'active_standard_outcomes': sum(m['active_standard'] for m in fresh),
                'swish_priced_unverified': sum(m['swish_unverified'] for m in counts),
                'modeled_outcomes': len(modeled),
                'value_qualified_outcomes': sum(t.get('stress_roi', -1) >= minimum_roi for t in modeled),
                'qualified_families': sum(t.get('stress_roi', -1) >= minimum_roi for t in best),
                'recorded_selections': len(issued),
                'unmarked_selections': sum(not marks.get(t['id']) for t in issued),
                'not_supported_reasons': dict(reasons), 'source_errors': errors,
            })
        fields = ('games', 'matched_games', 'odds_loaded_games', 'observed_outcomes',
                  'active_standard_outcomes', 'swish_priced_unverified', 'modeled_outcomes',
                  'value_qualified_outcomes', 'qualified_families', 'recorded_selections', 'unmarked_selections')
        windows[str(hours)] = {'start': now.isoformat(), 'end': end.isoformat(), 'leagues': rows,
                              'totals': {k: sum(r[k] for r in rows) for k in fields}}
    return {'state': 'available', 'generated_at': payload['generated_at'], 'as_of': now.isoformat(),
            'scope': snapshot['scope'], 'windows': windows}
