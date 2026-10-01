"""Exact archived Stake player offers, independent of pricing/publication.

Feed recognition is not player-model, execution, or settlement approval.
Swish names do not imply player IDs or matching provider event IDs.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from urllib.parse import urlparse

from .domain import parse_time
from .stake_api import market_records

VERSION = 'player-prop-contract-v1'
BASKETBALL = {'NBA', 'WNBA', 'NCAAM', 'NCAAW'}
FOOTBALL = {'NFL', 'NCAAF'}
METRICS = {
    'basketball': {'points': 'points', 'rebounds': 'rebounds', 'assists': 'assists',
        '3-point field goals': 'three_pointers_made', '3pt field goals': 'three_pointers_made',
        'blocks': 'blocks', 'steals': 'steals', 'turnovers': 'turnovers',
        'points + rebounds + assists': 'points_rebounds_assists',
        'rebounds + assists': 'rebounds_assists'},
    'football': {'passing yards': 'passing_yards', 'passing touchdowns': 'passing_touchdowns',
        'passing completions': 'passing_completions', 'passing attempts': 'passing_attempts',
        'interceptions': 'interceptions_thrown', 'rushing yards': 'rushing_yards',
        'rushing touchdowns': 'rushing_touchdowns', 'receiving yards': 'receiving_yards',
        'receiving touchdowns': 'receiving_touchdowns', 'receptions': 'receptions',
        'longest reception': 'longest_reception', 'anytime touchdown': 'touchdowns_scored'},
    'hockey': {'points': 'points', 'goals': 'goals', 'assists': 'assists',
        'shots': 'shots', 'blocked shots': 'blocked_shots', 'hits': 'hits',
        'faceoffs won': 'faceoffs_won', 'powerplay points': 'powerplay_points',
        'plus minus': 'plus_minus', 'goals against': 'goals_against',
        'saves': 'saves', 'shutouts': 'shutouts', 'win': 'goalie_win'},
}
SCOPE_PREFIXES = {'1st half ': 'half_1', '2nd half ': 'half_2',
    '1st quarter ': 'quarter_1', '2nd quarter ': 'quarter_2',
    '3rd quarter ': 'quarter_3', '4th quarter ': 'quarter_4',
    '1st period ': 'period_1', '2nd period ': 'period_2', '3rd period ': 'period_3'}
NUMBER = r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'
SEMANTIC_FIELDS = ('league', 'fixture_id', 'market_id', 'outcome_id', 'provider_section',
    'provider_game_id', 'player_id', 'player_name', 'metric', 'scope', 'includes_overtime',
    'includes_shootout', 'operator', 'line', 'market_name', 'selection')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Missing or invalid numeric value')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Invalid numeric value') from None
    if not result.is_finite():
        raise ValueError('Nonfinite numeric value')
    return result


def numeric_text(value):
    result = number(value)
    return format(result.normalize(), 'f') if result else '0'


def specifiers(raw):
    value = raw.get('specifiers', '')
    if not isinstance(value, str): raise ValueError('Malformed specifiers')
    pairs = {}
    for piece in value.split('|') if value else []:
        key, sep, item = piece.partition('=')
        if not sep or not key or key in pairs: raise ValueError('Duplicate or malformed specifier')
        pairs[key] = item
    return pairs


def sport_family(league):
    return 'basketball' if league in BASKETBALL else 'football' if league in FOOTBALL else 'hockey' if league == 'NHL' else None


def parse_metric(title, league, *, standard=False):
    """Recognize observed words; preserve unspecified overtime as unknown."""
    text = title.casefold().strip()
    overtime = shootout = None
    scope = None
    for suffix, ot, so in ((' (incl. overtime and penalties)', True, True),
                           (' (incl. overtime)', True, False),
                           (' (regular time)', False, False)):
        if text.endswith(suffix):
            text = text[:-len(suffix)]; overtime, shootout = ot, so
            scope = 'full_game' if ot else 'regulation'; break
    for prefix, segment in SCOPE_PREFIXES.items():
        if text.startswith(prefix):
            text = text[len(prefix):].removeprefix('- ').strip(); scope = segment; break
    if standard:
        text = text.removeprefix('player ').removeprefix('total ')
    family = sport_family(league)
    return {'metric': METRICS.get(family, {}).get(text), 'scope': scope,
            'includes_overtime': overtime, 'includes_shootout': shootout}


def _named_market(raw):
    title = raw.get('name', '')
    if not isinstance(title, str): return None
    match = re.fullmatch(r'(.+?) total (.+)', title, re.I)
    return (match[1], match[2]) if match else None


def _explicit_player(raw):
    value = specifiers(raw).get('player')
    return value if isinstance(value, str) and re.fullmatch(r'sr:player:\d+', value) else None


def _source(body, evidence):
    if not isinstance(body, bytes): raise ValueError('Provide original decompressed endpoint bytes')
    if hashlib.sha256(body).hexdigest() != evidence.get('sha256'):
        raise ValueError('Endpoint body SHA256 mismatch')
    url = urlparse(evidence.get('url', ''))
    if url.scheme != 'https' or url.hostname != 'odds-data.stake.com' or url.username or url.password or url.query or url.fragment:
        raise ValueError('Source is not an exact official Stake endpoint')
    parse_time(evidence['fetched_at'])
    payload = json.loads(body)
    fixture = payload.get('fixture', {})
    if not isinstance(fixture, dict) or fixture.get('id') != evidence.get('fixture_id'):
        raise ValueError('Endpoint fixture identity mismatch')
    slug = fixture.get('slug')
    if not slug or slug != evidence.get('fixture_slug') or url.path not in ('/odds/' + slug, '/fixtures/' + slug):
        raise ValueError('Endpoint fixture slug mismatch')
    if not isinstance(payload.get('groups'), list): raise ValueError('Missing standard group inventory')
    if payload.get('swishMarkets') is not None and not isinstance(payload['swishMarkets'], dict):
        raise ValueError('Malformed Swish inventory')
    return payload, fixture


def _finish(row):
    reasons = list(dict.fromkeys(row.pop('_reasons')))
    row['recognition'] = 'recognized' if row['metric'] else 'unrecognized'
    row['normalization'] = 'normalized' if row['metric'] and not reasons else 'needs_review'
    row['unresolved'] = reasons
    row['contract_id'] = canonical_hash({k: row.get(k) for k in SEMANTIC_FIELDS})
    row['contract_sha256'] = canonical_hash({k: row.get(k) for k in SEMANTIC_FIELDS})
    row['model_approved'] = False
    row['publishable'] = False
    row['settlement_rules_verified'] = False
    row['claim'] = 'Observed contract only; independent player model, identity bridge, participation and settlement evidence remain required.'
    return row


def normalize_player_props(body, evidence, *, league):
    """Return every observed player outcome, including unresolved/inactive rows.

    evidence binds original bytes to an already identified tournament fixture.
    No price implies availability or a missing stat/player ID. No I/O occurs.
    """
    payload, fixture = _source(body, evidence)
    if league != evidence.get('league'): raise ValueError('League evidence mismatch')
    records = list(market_records(payload))
    raw_variants = defaultdict(set)
    for _, raw in records:
        raw_variants[str(raw.get('id'))].add(canonical_hash(raw))
    names = defaultdict(set)
    name_evidence = defaultdict(dict)
    # Identity donors must be checked across every tab before any ladder is
    # joined. A second, contradictory raw version cannot be hidden by a valid
    # copy of the same market, nor can a name overwrite an explicit player ID.
    for _, raw in records:
        named = _named_market(raw)
        if not named or named[0].casefold() == 'player': continue
        problems = []
        player = None
        try:
            specs = specifiers(raw)
            player = _explicit_player(raw)
            if 'player' not in specs: continue
            if not player: problems.append('invalid_player_id')
        except ValueError:
            problems.append('malformed_specifiers')
        if not raw.get('id'): problems.append('missing_contract_id')
        if len(raw_variants[str(raw.get('id'))]) > 1:
            problems.append('conflicting_market_id')
        sha = canonical_hash(raw)
        name_evidence[named[0]][sha] = {
            'market_id': raw.get('id'), 'raw_market_sha256': sha,
            'player_id': player, 'issues': problems,
        }
        if player: names[named[0]].add(player)
    seen = set(); output = []; issues = []
    source = {k: evidence[k] for k in ('url', 'fetched_at', 'sha256')}
    base = {'version': VERSION, 'league': league, 'fixture_id': fixture['id'],
        'fixture_slug': fixture['slug'], 'source': source,
        'fixture_enabled': fixture.get('enabled') is True,
        'fixture_status': fixture.get('status')}
    for group, raw in records:
        named = _named_market(raw)
        generic = isinstance(raw.get('name'), str) and raw['name'].casefold().startswith('player ')
        try: player_id = _explicit_player(raw)
        except ValueError: player_id = None
        player_hint = isinstance(raw.get('specifiers'), str) and 'player=' in raw['specifiers']
        if not (generic or player_id or player_hint): continue
        key = canonical_hash(raw)
        if key in seen: continue
        seen.add(key)
        tabs = [{k: g.get(k) for k in ('id', 'name')} for g, r in records if canonical_hash(r) == key]
        tabs = list({canonical_hash(t): t for t in tabs}.values())
        title = named[1] if named else raw.get('name', '')
        semantic = parse_metric(title, league, standard=True)
        errors = []
        try: specs = specifiers(raw)
        except ValueError:
            specs = {}; errors.append('malformed_specifiers')
        if 'player' in specs and not player_id: errors.append('invalid_player_id')
        if len(raw_variants[str(raw.get('id'))]) > 1: errors.append('conflicting_market_id')
        outcomes = raw.get('outcomes')
        if not isinstance(outcomes, list):
            issues.append({'market_id': raw.get('id'), 'reason': 'malformed_outcomes'}); continue
        ids = Counter(o.get('id') for o in outcomes if isinstance(o, dict))
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                issues.append({'market_id': raw.get('id'), 'reason': 'malformed_outcome'}); continue
            reasons = list(errors); name = outcome.get('name'); player = player_id
            player_name = named[0] if named else None
            identity_evidence = []
            operator = line = None
            matched = re.fullmatch(r'(Over|Under) (' + NUMBER + ')', name or '', re.I)
            ladder = re.fullmatch(r'(.+) (\d+)\+', name or '')
            if matched:
                operator = matched[1].casefold(); line = numeric_text(matched[2])
                try:
                    if 'total' not in specs or number(specs['total']) != number(line): reasons.append('line_specifier_mismatch')
                except ValueError: reasons.append('invalid_line')
            elif isinstance(name, str) and name.casefold() in ('yes', 'no') and semantic['metric'] in ('touchdowns_scored', 'shutouts', 'goalie_win'):
                operator = name.casefold(); line = '1'
            elif generic and ladder:
                player_name = ladder[1]; operator = 'at_least'; line = numeric_text(ladder[2])
                mapped = names.get(player_name, set())
                donors = name_evidence.get(player_name, {})
                identity_evidence = [deepcopy(donors[sha]) for sha in sorted(donors)]
                donor_conflict = any(d['issues'] for d in identity_evidence)
                if donor_conflict: reasons.append('conflicting_player_identity_evidence')
                if player_id:
                    if mapped and mapped != {player_id}:
                        reasons.append('player_identity_conflict')
                elif 'malformed_specifiers' in errors or 'invalid_player_id' in errors:
                    pass  # An explicit but invalid identity is never guessed.
                elif donor_conflict:
                    pass
                elif len(mapped) == 1:
                    player = next(iter(mapped))
                else: reasons.append('ambiguous_player_identity' if mapped else 'missing_player_id')
            else: reasons.append('unrecognized_selection')
            if not player: reasons.append('missing_player_id')
            if not player_name: reasons.append('missing_player_name')
            if not semantic['metric']: reasons.append('unknown_metric')
            if semantic['scope'] is None or semantic['includes_overtime'] is None: reasons.append('unverified_settlement_scope')
            if not raw.get('id') or not outcome.get('id'): reasons.append('missing_contract_id')
            if ids[outcome.get('id')] > 1: reasons.append('duplicate_outcome_id')
            try:
                price = float(number(outcome.get('odds')))
                if not math.isfinite(price) or price <= 1:
                    price = None; reasons.append('invalid_decimal_odds')
            except (ValueError, OverflowError): price = None; reasons.append('invalid_decimal_odds')
            active = (fixture.get('enabled') is True and fixture.get('preMatchEnabled') is True
                      and not fixture.get('blacklisted') and fixture.get('status') == 'active'
                      and raw.get('status') == 'active' and outcome.get('active') is True and price is not None and price > 1)
            output.append(_finish({**base, **semantic, 'provider_section': 'standard',
                'provider_game_id': fixture['id'], 'market_id': raw.get('id'), 'outcome_id': outcome.get('id'),
                'market_name': raw.get('name'), 'selection': name, 'player_id': player, 'player_name': player_name,
                'identity_basis': 'explicit_player_specifier' if player_id else 'same_snapshot_exact_name_and_explicit_player_specifier' if player else None,
                'identity_evidence': identity_evidence,
                'operator': operator, 'line': line, 'decimal_odds': price, 'active': active,
                'availability': 'active_standard_offer' if active else 'not_active',
                'tabs': tabs, 'raw_market_sha256': key, 'raw_specifiers': raw.get('specifiers'),
                '_reasons': reasons}))
    swish = (payload.get('swishMarkets') or {}).get('playerProps', [])
    if not isinstance(swish, list): raise ValueError('Malformed Swish playerProps')
    swish_variants = defaultdict(set)
    swish_copies = Counter()
    for raw in swish:
        if isinstance(raw, dict):
            sha = canonical_hash(raw); swish_variants[raw.get('id')].add(sha); swish_copies[sha] += 1
    seen_swish = set()
    for raw in swish:
        if not isinstance(raw, dict):
            issues.append({'reason': 'malformed_swish_player_prop'}); continue
        raw_sha = canonical_hash(raw)
        if raw_sha in seen_swish: continue
        seen_swish.add(raw_sha)
        semantic = parse_metric(str(raw.get('marketName', '')), league)
        errors = ['missing_player_id', 'unverified_provider_event_mapping', 'unverified_swish_availability']
        if not semantic['metric']: errors.append('unknown_metric')
        if semantic['scope'] is None or semantic['includes_overtime'] is None: errors.append('unverified_settlement_scope')
        if len(swish_variants[raw.get('id')]) > 1: errors.append('conflicting_market_id')
        if raw.get('sportStatType') != 'player': errors.append('wrong_stat_entity')
        outcomes = raw.get('outcomes')
        if not isinstance(outcomes, list):
            issues.append({'market_id': raw.get('id'), 'reason': 'malformed_outcomes'}); continue
        ids = Counter(o.get('id') for o in outcomes if isinstance(o, dict))
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                issues.append({'market_id': raw.get('id'), 'reason': 'malformed_outcome'}); continue
            for operator in ('over', 'under'):
                reasons = list(errors)
                try: line = numeric_text(outcome.get('line'))
                except ValueError: line = None; reasons.append('invalid_line')
                try:
                    price = float(number(outcome.get(operator)))
                    if not math.isfinite(price) or price <= 1:
                        price = None; reasons.append('invalid_decimal_odds')
                except (ValueError, OverflowError): price = None; reasons.append('invalid_decimal_odds')
                if not raw.get('id') or not outcome.get('id'): reasons.append('missing_contract_id')
                if ids[outcome.get('id')] > 1: reasons.append('duplicate_outcome_id')
                output.append(_finish({**base, **semantic, 'provider_section': 'swishMarkets.playerProps',
                    'provider_game_id': raw.get('gameId'), 'market_id': raw.get('id'), 'outcome_id': outcome.get('id'),
                    'market_name': raw.get('marketName'), 'selection': operator,
                    'player_id': None, 'player_name': raw.get('competitorName'), 'team_name': raw.get('teamName'),
                    'identity_basis': None, 'operator': operator, 'line': line, 'decimal_odds': price,
                    'active': False, 'availability': 'unverified_swish_offer', 'tabs': [],
                    'raw_market_sha256': raw_sha, 'observed_copies': swish_copies[raw_sha], '_reasons': reasons}))
    # Some real Swish snapshots repeat the same market/outcome with different
    # team display strings. Retain every raw variant and conflict, but expose
    # one semantic offer per exact contract identity, never extra opportunities.
    unique = {}
    for row in output:
        variant = {'raw_market_sha256': row['raw_market_sha256'], 'team_name': row.get('team_name')}
        if row['contract_id'] in unique:
            old = unique[row['contract_id']]
            if variant not in old['source_variants']: old['source_variants'].append(variant)
            old['unresolved'] = sorted(set(old['unresolved'] + row['unresolved']))
            old['normalization'] = 'needs_review' if old['unresolved'] else old['normalization']
        else:
            row['source_variants'] = [variant]; unique[row['contract_id']] = row
    output = list(unique.values())
    return {'version': VERSION, 'league': league, 'fixture_id': fixture['id'], 'source': source,
            'contracts': output, 'issues': issues, 'counts': dict(Counter(x['normalization'] for x in output)),
            'production_supported': False}


OFFICIAL_PLAYER_HOSTS = {'NBA': ('nba.com',), 'WNBA': ('wnba.com',), 'NHL': ('nhl.com',),
    'NFL': ('nfl.com',), 'NCAAM': ('ncaa.com', 'ncaa.org'), 'NCAAW': ('ncaa.com', 'ncaa.org'),
    'NCAAF': ('ncaa.com', 'ncaa.org')}


def _pointer(value, pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('Invalid source JSON pointer')
    for part in pointer.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        try: value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError): raise ValueError('Source pointer missing') from None
    return value


def _proof_source(source, bodies, allowed):
    if not isinstance(source, dict): raise ValueError('Missing resolution source')
    body = bodies.get(source.get('sha256'))
    if not isinstance(body, bytes) or hashlib.sha256(body).hexdigest() != source.get('sha256'):
        raise ValueError('Resolution source bytes missing or altered')
    parsed = urlparse(source.get('url', '')); host = parsed.hostname or ''
    if parsed.scheme != 'https' or parsed.username or parsed.password or not any(host == h or host.endswith('.'+h) for h in allowed):
        raise ValueError('Unapproved resolution source host')
    parse_time(source['fetched_at'])
    return json.loads(body)


def resolve_player_identity(contract, resolution, *, evidence_bodies):
    """Apply explicit source-bound player AND provider-event mapping evidence.

    `player_source` points to official roster/stat bytes with player_id_pointer
    and player_name_pointer. `mapping_source` points to a separately verified
    Stake/official crosswalk record selected by mapping_pointer. That record
    must explicitly contain league, Stake fixture_id, provider_game_id,
    provider_player_name and official_player_id; names alone never join games.
    This API does not create mapping evidence, approve a model, infer scope or
    confirm Swish execution availability. It cannot turn missing evidence into
    an executable bet. Source adapters must preserve and review the crosswalk.
    """
    row = deepcopy(contract)
    expected = canonical_hash({k: row.get(k) for k in SEMANTIC_FIELDS})
    if expected != row.get('contract_sha256') or row.get('contract_id') != expected:
        raise ValueError('Original prop contract changed')
    if resolution.get('contract_id') != expected or resolution.get('feed_source_sha256') != row.get('source', {}).get('sha256'):
        raise ValueError('Resolution is not bound to this exact feed observation')
    allowed = OFFICIAL_PLAYER_HOSTS.get(row['league'], ())
    player_body = _proof_source(resolution.get('player_source'), evidence_bodies, allowed)
    mapping_body = _proof_source(resolution.get('mapping_source'), evidence_bodies, (*allowed, 'odds-data.stake.com'))
    player_id = _pointer(player_body, resolution.get('player_id_pointer'))
    player_name = _pointer(player_body, resolution.get('player_name_pointer'))
    if isinstance(player_id, bool) or not isinstance(player_id, (str, int)) or not str(player_id) or not isinstance(player_name, str) or not player_name:
        raise ValueError('Official player identity is incomplete')
    mapping = _pointer(mapping_body, resolution.get('mapping_pointer'))
    required = {'league': row['league'], 'fixture_id': row['fixture_id'],
        'provider_game_id': row['provider_game_id'], 'provider_player_name': row['player_name'],
        'official_player_id': str(player_id), 'official_player_name': player_name}
    if not isinstance(mapping, dict) or any(mapping.get(k) != value for k, value in required.items()):
        raise ValueError('Explicit player/event mapping mismatch')
    if row.get('player_id') and mapping.get('provider_player_id') != row['player_id']:
        raise ValueError('Known provider player ID conflicts with identity mapping')
    if row.get('team_name') and mapping.get('provider_team_name') != row['team_name']:
        raise ValueError('Provider team identity mismatch')
    row['provider_player_id'] = row.get('player_id')
    row['player_id'] = str(player_id)
    row['official_player_name'] = player_name
    row['identity_basis'] = 'explicit_source_bound_player_and_event_crosswalk'
    row['identity_resolution'] = {'resolution_sha256': canonical_hash(resolution),
        'original_contract_id': expected, 'player_source': deepcopy(resolution['player_source']),
        'mapping_source': deepcopy(resolution['mapping_source'])}
    row['unresolved'] = [reason for reason in row['unresolved'] if reason not in
                         ('missing_player_id', 'unverified_provider_event_mapping')]
    row['normalization'] = 'normalized' if row['metric'] and not row['unresolved'] else 'needs_review'
    row['contract_id'] = row['contract_sha256'] = canonical_hash({k: row.get(k) for k in SEMANTIC_FIELDS})
    row['model_approved'] = row['publishable'] = False
    return row
