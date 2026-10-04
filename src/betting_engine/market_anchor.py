"""Market-relative probability utilities.

The module is deliberately pure.  It can be used by the live decision path
or by a shadow trial without fetching data, mutating the ledger, or changing a
ticket after issuance.  A complete Stake contract is required before a
de-vigged probability is returned.
"""
from __future__ import annotations

from collections import defaultdict
import math


EPSILON = 1e-9


def conditional_push_free_line(kind: str, line) -> bool:
    """Whether a two-way quoted Asian contract is priced on non-push mass.

    Integer totals/handicaps and quarter lines have a zero/half settlement
    component.  The sportsbook's two prices identify the relative win/loss
    mass only; the model's push mass must be retained separately.  Half lines
    are included harmlessly (their model push mass is normally zero), while
    three-way markets never use this branch.
    """
    if kind.rsplit(':', 1)[-1] not in ('totals','spreads','home_total','away_total') or line is None:
        return False
    try:
        value=float(line)
        return math.isfinite(value) and abs(value*4-round(value*4))<1e-9
    except (TypeError,ValueError):
        return False


def _probability(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid {name}")
    if not 0.0 < value < 1.0:
        raise ValueError(f"Invalid {name}")
    return float(value)


def devig_probabilities(odds_by_outcome: dict[str, float]) -> dict[str, float]:
    """Return proportional no-vig probabilities for a complete market.

    The input is an exclusive market's decimal odds.  Missing, duplicate,
    non-finite and non-positive-vig contracts fail closed; an incomplete
    Stake response never becomes a fabricated market anchor.
    """
    if not isinstance(odds_by_outcome, dict) or len(odds_by_outcome) < 2:
        raise ValueError("A complete exclusive market with at least two outcomes is required")
    implied = {}
    for outcome, odds in odds_by_outcome.items():
        if not isinstance(outcome, str) or not outcome:
            raise ValueError("Invalid market outcome")
        if isinstance(odds, bool) or not isinstance(odds, (int, float)) or not math.isfinite(odds) or odds <= 1:
            raise ValueError("Invalid decimal odds")
        implied[outcome] = 1.0 / float(odds)
    total = sum(implied.values())
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Invalid implied probability mass")
    return {outcome: value / total for outcome, value in implied.items()}


def conditional_devig_probabilities(odds_by_outcome: dict[str, float]) -> dict[str, float]:
    """Two-way devig explicitly labelled as conditional on no push.

    The arithmetic is proportional devig, but the separate name and receipt
    prevent callers from accidentally treating the result as unconditional
    probability when an integer/quarter line has model push mass.
    """
    if not isinstance(odds_by_outcome, dict) or len(odds_by_outcome)!=2:
        raise ValueError('A complete two-way Asian market is required')
    return devig_probabilities(odds_by_outcome)


def logit(value: float) -> float:
    value = _probability(value, "probability")
    return math.log(value / (1.0 - value))


def sigmoid(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid logit")
    # Stable on both tails without importing a numerical package.
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def conditional_model_probability(win: float, push: float, *, allow_endpoints: bool = False) -> float:
    """Normalize only floating-point closure of validated win/push mass.

    Decimal complements such as .1/.9 can divide to 1.0000000000000002.
    Four ulps at unit mass cover arithmetic rounding, not substantive excess.
    The legacy path keeps its existing division and strict-logit semantics.
    """
    if not all(not isinstance(v, bool) and isinstance(v, (int, float))
               and math.isfinite(v) for v in (win, push)):
        raise ValueError('Invalid conditional model mass')
    if not 0 <= win <= 1 or not 0 <= push < 1:
        raise ValueError('Invalid conditional model mass')
    tolerance = 4 * math.ulp(1.0)
    if win + push > 1 + tolerance:
        raise ValueError('Invalid conditional model mass')
    conditional = win / (1 - push)
    if allow_endpoints and abs(conditional - 1) <= tolerance:
        return 1.0
    if allow_endpoints and conditional > 1:
        raise ValueError('Invalid conditional model mass')
    return conditional


def _model_probability(value: float, allow_endpoints: bool) -> float:
    if allow_endpoints and not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1:
        return float(value)
    return _probability(value, "model probability")


def logit_blend(model_probability: float, market_probability: float, model_weight: float, *, allow_endpoint_model: bool = False) -> float:
    """Blend independent model and no-vig market probabilities in logit space."""
    model = _model_probability(model_probability, allow_endpoint_model)
    market = _probability(market_probability, "market probability")
    if isinstance(model_weight, bool) or not isinstance(model_weight, (int, float)) or not math.isfinite(model_weight):
        raise ValueError("Invalid model weight")
    if not 0.0 <= model_weight <= 1.0:
        raise ValueError("Model weight must be in [0, 1]")
    if allow_endpoint_model and model_weight == 1:
        return model
    # Epsilon is numerical regularization only at the logit transform;
    # reported raw model mass and disagreement retain their exact values.
    transformed_model = min(1.0 - EPSILON, max(EPSILON, model)) if model in (0., 1.) else model
    return sigmoid((1.0 - float(model_weight)) * logit(market) + float(model_weight) * logit(transformed_model))


def anchor_status(model_probability: float, market_probability: float, *, threshold: float = 0.30, allow_endpoint_model: bool = False) -> str:
    """Classify disagreement before a contract can receive funding."""
    model = _model_probability(model_probability, allow_endpoint_model)
    market = _probability(market_probability, "market probability")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold <= 0 or threshold >= 1:
        raise ValueError("Invalid disagreement threshold")
    return 'review' if abs(model - market) > threshold else 'usable'


def binary_overtime_anchor(market, anchor):
    """Only the exact source-backed Yes/No alias bypasses score partitions."""
    from .overtime_contracts import overtime_alias
    alias = overtime_alias(getattr(market,'league',None), market.metadata.get('stake_market_name'),
                           market.metadata.get('stake_outcome_name'))
    return (alias == (market.kind, market.side) and market.line is None
            and anchor.get('probability_semantics') == 'binary_overtime_alias_v1'
            and set(anchor.get('market_probabilities', {})) == {'draw', 'home_away'})


def contract_anchors(markets, quotes):
    """Build exact-contract anchors from parsed Stake markets and quotes.

    Each `Market` carries the provider's condition identity.  Grouping by
    `(game, condition, kind, scope, line)` prevents a neighboring period or
    alternate line from being mixed into the devig calculation.
    """
    markets = list(markets)
    groups = defaultdict(list)
    for market in markets:
        quote = quotes.get(market.id)
        if quote is None:
            continue
        identity = market.metadata.get('stake_contract_identity', market.condition_id)
        # Stake expresses a two-way handicap as opposite signed lines
        # (home -7.5 / away +7.5).  The condition identity already contains
        # the exact offered line, so including the side's signed line here
        # would split one complete contract into two one-sided groups and
        # silently remove its market anchor.
        from .overtime_contracts import overtime_alias
        alias = overtime_alias(market.league, market.metadata.get('stake_market_name'),
                               market.metadata.get('stake_outcome_name')) if hasattr(market, 'league') else None
        binary_overtime = alias == (market.kind, market.side) and market.line is None
        key = (market.game_id, identity, 'overtime_alias' if binary_overtime else market.kind, market.metadata.get('model_scope'))
        groups[key].append((market, quote))
    result = {}
    expected = {
        'moneyline': {'home', 'away'}, 'spreads': {'home', 'away'},
        'totals': {'over', 'under'}, 'home_total': {'over', 'under'},
        'away_total': {'over', 'under'}, 'three_way': {'home', 'draw', 'away'},
        'odd_even': {'odd', 'even'}, 'both_score': {'yes', 'no'},
        'exact_total': {'over', 'exact', 'under'},
        'overtime_alias': {'draw', 'home_away'},
    }
    for (_, _, kind, _), rows in groups.items():
        base = kind.rsplit(':', 1)[-1]
        sides = expected.get(base)
        if base == 'winner_total':
            first = rows[0][0]
            binary = (getattr(first, 'league', None) == 'WNBA'
                and first.metadata.get('model_scope') == 'full_game'
                and first.metadata.get('stake_market_name') == 'Winner & Total (Incl. Overtime)')
            sides = {winner+'_'+total for winner in (('home', 'away') if binary else ('home', 'away', 'draw'))
                     for total in ('over', 'under')}
        if base == 'exact_score':
            # These are complete exclusive multi-outcome contracts.  The
            # parser has already checked their exact Stake outcome syntax;
            # retain all outcomes for one proportional devig calculation.
            sides = {market.side for market, _ in rows}
        if not sides or len(rows) != len(sides) or {market.side for market, _ in rows} != sides:
            continue
        try:
            odds = {market.side: 1.0 / quote.effective_cost for market, quote in rows}
            market_name = str(rows[0][0].metadata.get('stake_market_name') or getattr(rows[0][0], 'description', '') or '').lower()
            # Draw No Bet is a two-way contract whose draw outcome is a push.
            # Stake exposes it with moneyline-shaped sides, so the kind alone
            # cannot identify its conditional (push-free) pricing rule.
            conditional = conditional_push_free_line(kind, rows[0][0].line) or 'draw no bet' in market_name
            fair = conditional_devig_probabilities(odds) if conditional else devig_probabilities(odds)
        except (ValueError, ZeroDivisionError):
            continue
        for market, quote in rows:
            if base == 'three_way':
                from .domain import parse_time
                try:
                    selected_at = parse_time(quote.fetched_at)
                    if any(not -5 <= (selected_at - parse_time(peer.fetched_at)).total_seconds() <= 300 for _, peer in rows):
                        continue
                except (TypeError, ValueError, AttributeError):
                    continue
            result[market.id] = {
                'market_probability': fair[market.side],
                'market_probabilities': fair,
                'market_weight_source': 'Stake exact condition, conditional push-free devig' if conditional else 'Stake exact condition, proportional devig',
                'conditional_push_free': conditional,
                **({'probability_semantics': 'binary_overtime_alias_v1'} if base == 'overtime_alias' else {}),
                'market_anchor_key': [market.game_id, market.condition_id, market.kind, market.line],
                'quote_at': quote.fetched_at,
                **({'exclusive_odds': odds, 'exclusive_probabilities': fair,
                    'exclusive_partition_kind': kind, 'partition_version': 'exclusive_score_partition_v1',
                    'probability_semantics': 'exclusive_three_way_normalized_v1',
                    'exclusive_source_contracts': sorted(m.id for m, _ in rows),
                    'exclusive_quote_times': {m.id: q.fetched_at for m, q in rows}} if base == 'three_way' else {}),
            }
    # Double-chance outcomes overlap. Their reciprocal odds cannot be
    # normalized as three mutually exclusive outcomes. Bind to one complete
    # underlying 1x2 market in exactly the same game and settlement scope.
    underlying = defaultdict(dict)
    for key, rows in groups.items():
        game, identity, kind, scope = key
        if kind.rsplit(':', 1)[-1] == 'three_way' and all(m.id in result for m, _ in rows):
            underlying[(game, kind.rsplit(':', 1)[0] if ':' in kind else '', scope)][identity] = rows
    for market in markets:
        if market.kind.rsplit(':', 1)[-1] != 'double_chance' or market.id not in quotes:
            continue
        if binary_overtime_anchor(market, result.get(market.id, {})):
            continue
        prefix = market.kind.rsplit(':', 1)[0] if ':' in market.kind else ''
        partitions = underlying.get((market.game_id, prefix, market.metadata.get('model_scope')), {})
        if len(partitions) != 1:
            continue
        rows = next(iter(partitions.values()))
        from .domain import parse_time
        try:
            selected_at = parse_time(quotes[market.id].fetched_at)
            if any(not -5 <= (selected_at - parse_time(q.fetched_at)).total_seconds() <= 300 for _, q in rows):
                continue
        except (TypeError, ValueError, AttributeError):
            continue
        source = result[rows[0][0].id]
        fair = source['exclusive_probabilities']
        unions = double_chance_probabilities(fair)
        if market.side not in unions:
            continue
        result[market.id] = {
            'market_probability': unions[market.side], 'market_probabilities': unions,
            'market_weight_source': 'Stake exact-scope exclusive 1x2 partition; double chance union',
            'conditional_push_free': False,
            'market_anchor_key': [market.game_id, market.condition_id, market.kind, market.line],
            'quote_at': quotes[market.id].fetched_at,
            'exclusive_odds': source['exclusive_odds'], 'exclusive_probabilities': fair,
            'exclusive_partition_kind': rows[0][0].kind,
            'exclusive_source_contracts': sorted(m.id for m, _ in rows),
            'exclusive_quote_times': {m.id: q.fetched_at for m, q in rows},
            'partition_version': 'exclusive_score_partition_v1',
            'probability_semantics': 'derived_double_chance_union_v1',
        }
    return result



def double_chance_probabilities(exclusive):
    if set(exclusive) != {'home', 'draw', 'away'} or not math.isclose(sum(exclusive.values()), 1., abs_tol=1e-9):
        raise ValueError('Double chance requires a complete exclusive 1x2 distribution')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in exclusive.values()):
        raise ValueError('Invalid exclusive outcome mass')
    return {'home_draw': exclusive['home'] + exclusive['draw'],
            'away_draw': exclusive['away'] + exclusive['draw'],
            'home_away': exclusive['home'] + exclusive['away']}


def anchored_score_partition(forecast, market, anchor, *, weight, threshold, mode, allow_endpoint_model=False):
    """Reproducible exclusive 1x2 blending and derived overlapping DC unions."""
    kind = market.kind.rsplit(':', 1)[-1]
    if kind not in ('three_way', 'double_chance'):
        raise ValueError('Not an exclusive-score/derived double-chance contract')
    expected_kind = market.kind.rsplit(':', 1)[0] + ':three_way' if ':' in market.kind else 'three_way'
    if anchor.get('exclusive_partition_kind') != expected_kind or anchor.get('partition_version') != 'exclusive_score_partition_v1':
        raise ValueError('Exact-scope exclusive partition is required')
    semantics = 'exclusive_three_way_normalized_v1' if kind == 'three_way' else 'derived_double_chance_union_v1'
    if anchor.get('probability_semantics') != semantics:
        raise ValueError('Missing or invalid probability semantics')
    fair = devig_probabilities(anchor['exclusive_odds'])
    if set(fair) != {'home', 'draw', 'away'} or fair != anchor.get('exclusive_probabilities'):
        raise ValueError('Exclusive market partition does not reproduce')
    raw = {}
    for side in ('home', 'draw', 'away'):
        probability, push = forecast.market_probability(expected_kind, side, None)
        if push != 0:
            raise ValueError('Exclusive 1x2 model cannot contain push mass')
        raw[side] = _model_probability(probability, True)
    if not math.isclose(sum(raw.values()), 1., abs_tol=1e-9):
        raise ValueError('Incomplete exclusive model partition')
    blended = {side: logit_blend(raw[side], fair[side], weight, allow_endpoint_model=True) for side in raw}
    mass = sum(blended.values())
    normalized = {side: value / mass for side, value in blended.items()}
    status = 'review' if any(anchor_status(raw[side], fair[side], threshold=threshold, allow_endpoint_model=True) != 'usable' for side in raw) else 'usable'
    source = raw if kind == 'three_way' else double_chance_probabilities(raw)
    # Other outcomes may have zero mass even when the selected contract is
    # interior. Preserve the selected-contract endpoint policy separately.
    _model_probability(source[market.side], allow_endpoint_model)
    market_probs = fair if kind == 'three_way' else double_chance_probabilities(fair)
    outcomes = normalized if kind == 'three_way' else double_chance_probabilities(normalized)
    if market_probs[market.side] != anchor.get('market_probability') or market_probs != anchor.get('market_probabilities'):
        raise ValueError('Selected probability differs from exclusive partition')
    return {**anchor, 'model_probability': source[market.side], 'model_probabilities': raw,
            'blended_probabilities': normalized, 'blended_probability': outcomes[market.side],
            'model_weight': weight, 'disagreement_threshold': threshold, 'status': status, 'mode': mode}

def fit_model_weight(observations, *, minimum_games: int = 30, grid_step: float = 0.05, prediction_fn=None):
    """Fit a model logit weight on an earlier chronological sample.

    The returned weight is a candidate only.  A caller must run a later
    prospective holdout and apply its own promotion gate.  This keeps the
    weekly learning loop reproducible and prevents post-outcome leakage.
    """
    if isinstance(minimum_games, bool) or not isinstance(minimum_games, int) or minimum_games < 1:
        raise ValueError("Invalid minimum game count")
    if isinstance(grid_step, bool) or not isinstance(grid_step, (int, float)) or not math.isfinite(grid_step) or not 0 < grid_step <= 1:
        raise ValueError("Invalid grid step")
    rows = sorted(observations, key=lambda row: str(row.get('issued_at', '')))
    games = {row.get('game_id') for row in rows if row.get('game_id')}
    if len(games) < minimum_games:
        return {'state': 'insufficient_evidence', 'independent_games': len(games), 'weight': 0.0}
    valid = []
    for row in rows:
        try:
            p = _probability(row['model_probability'], 'model probability')
            q = _probability(row['market_probability'], 'market probability')
            y = row['outcome']
            if y not in (0, 1):
                raise ValueError
            if not row.get('game_id'):raise ValueError
            valid.append((row,p,q,y))
        except (KeyError, TypeError, ValueError):
            continue
    games={row['game_id'] for row,_,_,_ in valid}
    if len(games)<minimum_games:
        return {'state': 'insufficient_evidence', 'independent_games': len(games), 'weight': 0.0}
    candidates = [min(1.0, round(i * float(grid_step), 10)) for i in range(int(1 / float(grid_step)) + 1)]
    def loss(weight):
        groups={}
        for row,p,q,y in valid:
            blended=prediction_fn(row,weight) if prediction_fn else logit_blend(p,q,weight)
            groups.setdefault(row['game_id'],[]).append(-y*math.log(max(EPSILON,blended))-(1-y)*math.log(max(EPSILON,1-blended)))
        return math.fsum(math.fsum(values)/len(values) for values in groups.values())/len(groups)
    best = min(candidates, key=lambda weight: (loss(weight), weight))
    return {'state': 'candidate', 'independent_games': len(games), 'observations': len(valid),
            'weight': best, 'log_loss': loss(best), 'grid_step': float(grid_step),
            'basis': 'chronological training sample; prospective holdout required before promotion'}


def verify_score_anchor_source(game, market, anchor, data_dir):
    """Bind all partition prices to the hash-verified original Stake response.

    Reparse the archived payload with the existing exact-scope parser; no
    generated candidate probability or supplied anchor odds is a source.
    """
    from pathlib import Path
    meta = market.metadata.get('api_source') or {}
    archive = meta.get('archive', '')
    target = Path(data_dir) / archive
    stat = target.stat()
    source = _score_source_anchors(str(Path(data_dir).resolve()), meta.get('sha256'), archive,
        meta.get('bytes'), meta.get('fetched_at'), stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns,
        game.id, game.league, game.home, game.away, game.start,
        getattr(game,'home_abbr',''),getattr(game,'away_abbr',''),
        market.metadata.get('model_scope'), market.metadata.get('fixture_slug'),
        market.metadata.get('league_path'))
    expected = source.get(market.id)
    if not expected:
        raise ValueError('Selected score partition absent from verified source')
    keys = ('market_probability','market_probabilities','conditional_push_free',
            'market_anchor_key','quote_at','exclusive_odds','exclusive_probabilities',
            'exclusive_partition_kind','exclusive_source_contracts','exclusive_quote_times',
            'partition_version','probability_semantics')
    if any(anchor.get(key) != expected.get(key) for key in keys):
        raise ValueError('Score partition anchor differs from archived source')
    return {'source_sha256': meta['sha256'], 'source_market_id': market.id,
            'probability_semantics': expected['probability_semantics']}


from functools import lru_cache

@lru_cache(maxsize=64)
def _score_source_anchors(data_dir, sha, archive, size, fetched_at, mtime_ns, compressed_size, inode, ctime_ns,
                          game_id, league, home, away, start, home_abbr, away_abbr, scope, slug, league_path):
    # Stat generation participates in the key; archive hash is checked on every
    # new body generation. Cache contains only parsed anchors, not raw bodies.
    from pathlib import Path
    from types import SimpleNamespace
    import json
    from .player_runtime import _body
    from .markets import StakeSportsbook
    from .domain import parse_time
    raw = json.loads(_body(Path(data_dir), {'archive':archive, 'sha256':sha, 'bytes':size}))
    fixture = raw.get('fixture') or {}
    if (not fixture.get('id') or fixture.get('status') != 'active'
            or fixture.get('enabled') is not True or fixture.get('preMatchEnabled') is not True
            or fixture.get('blacklisted') is True or
            abs(float(fixture['startTime']) / 1000 - parse_time(start).timestamp()) > 1):
        raise ValueError('Score source fixture lifecycle/start mismatch')
    # Replay only has score-contract inputs. An unrelated NFL overtime offer
    # must be rejected for absent verified phase, never supplied an invented one.
    game = SimpleNamespace(id=game_id, league=league, home=home, away=away, start=start,
                           home_abbr=home_abbr, away_abbr=away_abbr, context={}, source='', game_type='')
    book = StakeSportsbook(fetcher=object(),segment_markets={(league,scope):('three_way','double_chance')},
                           full_markets={league:('three_way','double_chance')})
    parsed = book._markets_for_fixture(raw, {'id':fixture['id'],'slug':slug,'league_path':league_path},game,league,fetched_at)
    quotes, errors = book.quotes(parsed)
    if errors:
        raise ValueError('Invalid archived score quote')
    return contract_anchors(parsed, quotes)
