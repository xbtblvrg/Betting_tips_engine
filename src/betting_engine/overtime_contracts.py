"""Stake basketball overtime offers are exact aliases of regulation score bets.

Rule source: https://stake.com/policies/sportsbook (Basketball, checked 2026-10-04).
Yes means a regulation draw regardless of whether overtime is actually played.
NFL regular/postseason uses the official 2026 Rule 16-1-1 tie trigger and Stake
official-stat settlement rule; explicit source-backed season phase is mandatory.
"""
TITLES = ('Will There be Overtime', 'Will There be Overtime?', 'Will There Be Overtime', 'Will there be overtime', 'Will There Be Overtime?', 'Will there be overtime?')
RULE_URL = 'https://stake.com/policies/sportsbook'
NFL_RULE_URL = 'https://static.www.nfl.com/image/upload/fl_attachment/league/tqivdkzt9mu6wdgsh1ku.pdf'
NFL_PHASE_SOURCE = 'https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv'

def verified_nfl_phase(game_id, context, source=None, game_type=None):
    evidence = context.get('nfl_phase_evidence', {})
    phase = evidence.get('raw_game_type')
    digest = evidence.get('source_sha256')
    return (evidence.get('game_id') == game_id and phase in ('REG', 'WC', 'DIV', 'CON', 'SB')
            and evidence.get('source') == NFL_PHASE_SOURCE
            and (source is None or source == NFL_PHASE_SOURCE)
            and (game_type is None or game_type == ('regular' if phase == 'REG' else 'playoff'))
            and isinstance(digest, str) and len(digest) == 64
            and all(c in '0123456789abcdef' for c in digest) and bool(evidence.get('fetched_at')))



def overtime_alias(league, title, outcome):
    if league not in ('NBA', 'WNBA', 'NFL') or title not in TITLES:
        return None
    return {'Yes': ('regulation:three_way', 'draw'),
            'No': ('regulation:double_chance', 'home_away')}.get(outcome)


def parse_overtime(market, game, fixture, url, fetched_at, validated, groups, identity):
    import math
    from .markets import Market
    from .period_contracts import validate_scoped_specifiers
    # Both independently gated canonical families are required. Never mutate a
    # model report or infer approval from the mere presence of a score model.
    if not {'three_way', 'double_chance'} <= set(validated):
        return []
    if game.league == 'NFL' and not verified_nfl_phase(game.id, game.context, game.source, game.game_type):
        return []
    outcomes = market.get('outcomes') or []
    if len(outcomes) != 2 or {o.get('name') for o in outcomes} != {'Yes', 'No'}:
        return []
    if any(o.get('active') is not True or o.get('status') not in (None, 'active')
           or isinstance(o.get('odds'), bool) or not isinstance(o.get('odds'), (int, float))
           or not math.isfinite(o['odds']) or o['odds'] <= 1 for o in outcomes):
        return []
    try:
        validate_scoped_specifiers(market.get('specifiers'), 'regulation')
    except ValueError:
        return []
    result = []
    for outcome in outcomes:
        alias = overtime_alias(game.league, market['name'], outcome['name'])
        if alias is None:
            return []
        kind, side = alias
        result.append(Market(
            id=f'stake:{fixture.get("id", fixture.get("slug", ""))}:{market.get("id", market["name"])}:{outcome.get("id", outcome["name"])}',
            game_id=game.id, league=game.league, kind=kind, side=side, line=None,
            selection=outcome['name'], platform='Stake.com', url=url,
            token=str(outcome.get('id', outcome['name'])), condition_id=str(market.get('id', market['name'])), description=market['name'],
            fee_rate=0., fee_exponent=1., minimum_notional=0., market_start=game.start,
            fetched_at=fetched_at, metadata={
                'provider': 'stake-sports-data', 'fixture_slug': fixture.get('slug') or fixture.get('name', ''),
                'league_path': url.split('/sports/', 1)[1].rsplit('/', 1)[0],
                'stake_market_name': market['name'], 'stake_outcome_name': outcome['name'],
                'stake_market_groups': groups, 'stake_specifiers': market.get('specifiers'),
                'stake_contract_identity': identity, 'stake_odds': float(outcome['odds']),
                'stake_scope': 'regulation', 'model_scope': 'regulation', 'availability_verified': True,
                'score_contract_alias': {'market': kind, 'side': side, 'line': None,
                    'rule_url': RULE_URL, 'rule_section': 'American Football' if game.league == 'NFL' else 'Basketball',
                    **({'sports_rule_url': NFL_RULE_URL, 'sports_rule_section': '16-1-1',
                        'phase_evidence': dict(game.context['nfl_phase_evidence'])} if game.league == 'NFL' else {}),
                    'rule': 'regulation_draw' if side == 'draw' else 'regulation_non_draw'},
            }))
    return result


def verify_nfl_phase_source(game, data_dir):
    """Reproduce phase and fixture from the immutable schedule response bytes."""
    import csv
    import hashlib
    import io
    from pathlib import Path
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from .domain import parse_time
    if not verified_nfl_phase(game.id, game.context, game.source, game.game_type):
        return False
    evidence = game.context['nfl_phase_evidence']
    path = Path(data_dir) / 'raw' / (evidence['source_sha256'] + '.data')
    try:
        if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
            return False
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != evidence['source_sha256']:
            return False
        rows = [r for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
                if 'NFL:' + r.get('game_id', '') == game.id]
        if len(rows) != 1:
            return False
        row = rows[0]
        start = datetime.fromisoformat(row['gameday']+'T'+(row.get('gametime') or '13:00')).replace(
            tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
        return (row['game_type'] == evidence['raw_game_type']
                and row['home_team'] == game.home_id and row['away_team'] == game.away_id
                and int(row['season']) == game.season and start == parse_time(game.start))
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return False


def deduplicate_alias_candidates(candidates):
    """Keep one best guarded current offer per unchanged canonical contract ID.

    Complete offers must remain intact until per-condition anchoring and guard
    reproduction finish. Only then collapse payoff aliases, before allocation
    or publication. Original held decisions are never input or modified here.
    """
    alias_ids = {c['id'] for c in candidates if c.get('reference_contract_description') in TITLES}
    best = {}
    for candidate in candidates:
        if candidate['id'] not in alias_ids:
            continue
        priority = (candidate['reference_odds'], candidate.get('expected_roi', float('-inf')),
                    str(candidate.get('reference_market_id', '')))
        current = best.get(candidate['id'])
        if current is None or priority > current[0]:
            best[candidate['id']] = (priority, candidate)
    emitted = set()
    result = []
    for candidate in candidates:
        identity = candidate['id']
        if identity not in alias_ids:
            result.append(candidate)
        elif identity not in emitted:
            result.append(best[identity][1]);emitted.add(identity)
    return result
