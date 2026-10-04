"""Exact-title evidence projection of the existing catalogue, never a funding gate."""
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
from .domain import parse_time, utcnow
from .market_catalog import classify

LEAGUES = ('NFL', 'NHL', 'MLB', 'NBA', 'WNBA')


def _key(row):
    title = row.get('market_name') or row.get('market')
    if isinstance(row.get('stake_market'), dict):
        title = row['stake_market'].get('name') or title
    title = (row.get('stake_evidence') or {}).get('market_name') or title
    # Exact source title defines scope. Model scope alone must not merge titles.
    section = row.get('section', row.get('provider_section', 'standard'))
    scope = classify({'market': title, 'provider_section': section})['scope']
    return row.get('league'), section, title, scope


def _fresh(stamp, now):
    try:
        return -5 <= (now - parse_time(stamp)).total_seconds() <= 300
    except (TypeError, ValueError, AttributeError):
        return False


def build_live_coverage(publication, catalog, decisions=(), feedback_tip_ids=(), now=None):
    """Project observed titles plus exact current evidence and immutable decisions.

    decisions: dict rows from system_decisions (payload may be JSON or dict).
    feedback_tip_ids: IDs explicitly present in a successful feedback input;
    a recent worker heartbeat alone is not proof of feedback for a decision.
    Funding support means a guarded current candidate passes policy and minimum
    sizing before exposure. Ready-now separately requires executable funded
    selection and an unmarked checklist. Neither grants permanent title approval.
    """
    now = now or utcnow()
    current = _fresh(publication.get('generated_at'), now)
    def active(tip):
        try:
            return parse_time(tip.get('start')) > now and parse_time(tip.get('valid_until')) > now
        except (TypeError, ValueError, AttributeError):
            return False
    groups = {}
    for source in [*catalog.get('types', []), *publication.get('market_types', [])]:
        key = _key(source)
        if key[0] not in LEAGUES or not key[2]:
            continue
        groups.setdefault(key, {})
    live = {_key(row): row for row in publication.get('market_types', [])}
    funded = Counter()
    qualifying = Counter()
    from .funding_policy import funding_basis, size_recommendation
    settings = publication.get('settings', {})
    for tip in publication.get('evaluated_candidates', []):
        if not (current and active(tip) and _fresh(tip.get('quote_at'), now) and
                (tip.get('decision_guard') or {}).get('passed') is True and
                (tip.get('input_readiness') or {}).get('passed') is True):
            continue
        try:
            basis = funding_basis(tip, settings)
            size = size_recommendation(tip, settings, float(settings['max_bet']), 0)
            if basis['eligible'] and 1 <= size['amount'] <= min(5, settings['max_bet']):
                qualifying[_key(tip)] += 1
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    from .dashboard_payload import _execution_counts
    if current:
        ready = _execution_counts(publication.get('recommendations', []) + publication.get('alternatives', []),
                                  publication.get('evaluated_candidates', []), publication.get('tracked_bets', []),
                                  publication.get('placement_marks', {}), {'max_bet': 5, **settings}, {}, now,
                                  include_ready_ids=True)['ready_tip_ids']
        tip_index = {t['id']: t for t in publication.get('recommendations', []) + publication.get('alternatives', [])}
        tip_index.update({b['tip_id']: b['payload'] for b in publication.get('tracked_bets', [])})
        for identity in ready:
            if identity in tip_index:
                funded[_key(tip_index[identity])] += 1
    historical = defaultdict(list)
    seen = set()
    for decision in decisions:
        tip = decision.get('payload') or {}
        if isinstance(tip, str):
            tip = json.loads(tip)
        identity = decision.get('family') or decision.get('tip_id') or tip.get('id')
        if not identity or identity in seen or tip.get('stake', 0) <= 0:
            continue
        seen.add(identity)
        key = _key(tip)
        if key[0] not in LEAGUES or not key[2]:
            continue
        groups.setdefault(key, {})
        historical[key].append((decision, tip))
    feedback = set(feedback_tip_ids)
    today = now.astimezone(ZoneInfo('Europe/Budapest')).date()
    rows = []
    for key in sorted(groups):
        source = live.get(key, {})
        original = historical[key]
        issued_today = 0
        closed = []
        for decision, tip in original:
            try:
                if parse_time(decision.get('issued_at')).astimezone(ZoneInfo('Europe/Budapest')).date() == today:
                    issued_today += 1
            except (TypeError, ValueError, AttributeError):
                pass
            if decision.get('status') in ('won', 'lost', 'push', 'void', 'half_won', 'half_lost'):
                closed.append((decision, tip))
        fed = sum((d.get('tip_id') or t.get('id')) in feedback for d, t in closed)
        parsed = source.get('parsed_outcomes', 0) if current else 0
        priced = source.get('priced_outcomes', 0) if current else 0
        passed = source.get('guard_passed_outcomes', 0) if current else 0
        rows.append({'league': key[0], 'section': key[1], 'market': key[2], 'scope': key[3],
                     'observed_in_current_publication': bool(source) and current,
                     'observed_outcomes': source.get('observed_outcomes', 0),
                     'parsed_outcomes': parsed, 'priced_outcomes': priced,
                     'guard_passed_outcomes': passed, 'currently_fundable_outcomes': funded[key],
                     'connected': parsed > 0, 'evaluated': current and (source.get('evaluated_outcomes', 0) > 0 or source.get('guard_checked_outcomes', 0) > 0 or passed > 0),
                     'funding_eligible': qualifying[key] > 0,
                     'funding_eligible_now': funded[key] > 0,
                     'qualifying_before_exposure': qualifying[key],
                     'funding_supported': qualifying[key] > 0,
                     'issued_today': issued_today, 'settled_decisions': len(closed),
                     'feedback_decisions': fed, 'settled_and_feedback': bool(closed) and fed == len(closed),
                     'reasons': source.get('reasons', {}),
                     'next_step': source.get('next_step') or 'Obtain a fresh exact-contract evaluation for this observed title.',
                     'evidence_state': 'current_publication' if current and source else 'catalogue_or_history_only'})
    return {'version': 'live-market-coverage-v1', 'checked_at': now.isoformat(),
            'publication_at': publication.get('generated_at'), 'publication_version': publication.get('version'),
            'catalogue_at': catalog.get('checked_at'), 'publication_fresh': current,
            'scope': 'Observed feed titles; not proof of complete Stake website coverage. Funding support means a fresh guarded candidate passes current funding policy and minimum sizing before exposure/family allocation; eligible-now means an executable funded selection.',
            'feedback_definition': 'Every settled original decision of this exact title occurs in supplied successful feedback input IDs; this does not prove model promotion.',
            'rows': rows,
            'leagues': {league: {'observed_types': sum(r['league'] == league for r in rows),
                                'evaluated_types': sum(r['league'] == league and r['evaluated'] for r in rows),
                                'funding_eligible_types': sum(r['league'] == league and r['funding_eligible'] for r in rows),
                                'funding_eligible_now_types': sum(r['league'] == league and r['funding_eligible_now'] for r in rows),
                                'guard_passed_types': sum(r['league'] == league and r['guard_passed_outcomes'] > 0 for r in rows),
                                'issued_today': sum(r['issued_today'] for r in rows if r['league'] == league)} for league in LEAGUES}}


def export_markdown(report, path):
    """Deterministic user-facing export; caller chooses existing docs destination."""
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    def yes(value):
        return 'Yes' if value else 'No'
    lines = ['# Live Stake market coverage', '', 'Checked: ' + report['checked_at'], '', report['scope'], '',
             '| League | Exact Stake title | Scope | Connected | Evaluated | Funding supported / ready now | Issued today | Settled / feedback |',
             '|---|---|---|---|---|---|---:|---|']
    for row in report['rows']:
        lines.append('| ' + ' | '.join(map(cell, [row['league'], row['market'], row['scope'], yes(row['connected']), yes(row['evaluated']), yes(row['funding_supported']) + ' / ' + yes(row['funding_eligible_now']), row['issued_today'], str(row['settled_decisions']) + ' / ' + str(row['feedback_decisions'])])) + ' |')
    target = Path(path)
    from .io_utils import atomic_bytes
    atomic_bytes(target, ('\n'.join(lines) + '\n').encode())
    return str(target)


def read_decisions(connection):
    """Read only compact immutable fields; never materialize multi-MB guard trees."""
    sql = """SELECT family,tip_id,issued_at,status,
      json_object('id',tip_id,'league',json_extract(payload,'$.league'),
      'stake',json_extract(payload,'$.stake'),
      'stake_market',json_extract(payload,'$.stake_market'),
      'stake_evidence',json_object('market_name',json_extract(payload,'$.stake_evidence.market_name')),
      'market',json_extract(payload,'$.market')) payload FROM system_decisions"""
    return [dict(row) for row in connection.execute(sql)]


def feedback_ids(data_dir):
    """Hash-check current successful feedback's decision input chunks only."""
    import hashlib
    import re
    data = Path(data_dir)
    path = data / 'outcome_feedback/latest.json'
    if not path.exists():
        return [], 'No successful feedback receipt'
    try:
        state = json.loads(path.read_text())
        ref = state['latest_review']
        def read(reference, folder):
            sha = reference['sha256']
            relative = reference['path']
            if not re.fullmatch(r'[0-9a-f]{64}', sha) or not re.fullmatch(folder + r'/[0-9a-f]{64}\.json', relative):
                raise ValueError('Invalid feedback evidence path')
            target = data / relative
            if target.is_symlink() or not target.resolve().is_relative_to(data.resolve()) or target.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Invalid feedback evidence size/path')
            body = target.read_bytes()
            if hashlib.sha256(body).hexdigest() != sha:
                raise ValueError('Feedback evidence hash mismatch')
            return json.loads(body)
        review = read(ref, 'outcome_feedback/reviews')
        ids = []
        chunks = review.get('input_archive', {}).get('chunks', [])
        if len(chunks) > 10000:
            raise ValueError('Feedback chunk count limit')
        for chunk in chunks:
            if chunk['kind'] != 'decisions':
                continue
            records = read(chunk, 'outcome_feedback/inputs')
            if records.get('kind') != 'decisions' or len(records.get('rows', [])) != chunk['rows']:
                raise ValueError('Feedback decision chunk binding mismatch')
            ids.extend(row['tip_id'] for row in records['rows'] if row.get('tip_id'))
        if not chunks:
            ids = [row['tip_id'] for row in review.get('inputs', {}).get('decisions', []) if row.get('tip_id')]
        return ids, ref['sha256']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [], str(exc)


def build_from_store(store, publication, catalog, data_dir, now=None):
    with store.db() as connection:
        decisions = read_decisions(connection)
    ids, evidence = feedback_ids(data_dir)
    report = build_live_coverage(publication, catalog, decisions, ids, now)
    report['feedback_evidence'] = evidence
    return report
