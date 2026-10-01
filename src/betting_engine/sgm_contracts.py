"""Experimental Same Game Multi contracts; no network, pricing or publication.

Bindings describe a supplied raw combined-quote object, NOT a claimed Stake API
schema. There is no verified live adapter yet. Even a valid contract remains
unverified and cannot authorize a betting instruction.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import (
    Context, Decimal, DecimalException, DivisionByZero, InvalidOperation,
    Overflow, ROUND_HALF_EVEN, localcontext,
)
import hashlib
import json
import re

VERSION = 'sgm-contract-v2'


@dataclass(frozen=True)
class SGMScope:
    period: str
    includes_overtime: bool
    includes_shootout: bool
    settlement_rules_sha256: str


@dataclass(frozen=True)
class SGMLeg:
    league: str
    fixture_id: str
    market_id: str
    outcome_id: str
    market_name: str
    outcome_name: str
    group_id: str
    group_name: str
    market: str
    side: str
    line: str | None
    scope: SGMScope
    active: bool
    book: str = field(kw_only=True)
    phase: str = field(kw_only=True)
    specifiers: str = field(kw_only=True)


@dataclass(frozen=True)
class SGMQuote:
    quote_id: str
    league: str
    fixture_id: str
    event_start: str
    decimal_odds: str
    price_origin: str
    status: str
    legs: tuple[SGMLeg, ...]
    combination_rules_sha256: str
    source_published_at: str | None = None
    source_expires_at: str | None = None
    book: str = field(kw_only=True)
    phase: str = field(kw_only=True)


@dataclass(frozen=True)
class CombinedQuoteEvidence:
    """Original bytes plus explicit relative JSON pointers under ONE quote node.

    field_paths maps normalized leaf names (e.g. legs.0.outcome_id) to raw
    pointers relative to quote_pointer. Mapping correctness needs independent
    adapter review; a hash proves integrity, not authenticity or availability.
    observed_at is local receipt time, never a fabricated source publication.
    source_ref is retained verbatim; no URL is constructed or fetched.
    """
    body: bytes
    sha256: str
    observed_at: str
    source_ref: str
    quote_pointer: str
    field_paths: tuple[tuple[str, str], ...]
    provenance: str  # test_fixture or unverified_capture; neither is live proof


@dataclass(frozen=True)
class SGMValidation:
    contract_valid: bool
    reasons: tuple[str, ...]
    contract_id: str | None
    quote_fingerprint: str | None
    expires_at: str | None
    dependency_cluster: str | None
    availability_verified: bool = False
    publishable: bool = False


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _time(value):
    if not isinstance(value, str):
        raise ValueError('missing time')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('timezone required')
    return stamp.astimezone(timezone.utc)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('invalid number')
    if len(str(value)) > 64:
        raise ValueError('oversized decimal')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid number') from exc
    if not result.is_finite():
        raise ValueError('nonfinite number')
    if len(result.as_tuple().digits) > 24 or abs(result.as_tuple().exponent) > 12:
        raise ValueError('decimal outside experimental wire bounds')
    return result


def _canonical_number(value):
    number = _number(value)
    return '0' if number == 0 else str(number.normalize())


def _pointer(value, path):
    if not isinstance(path, str) or (path and not path.startswith('/')):
        raise ValueError('invalid JSON pointer')
    for key in path.split('/')[1:] if path else ():
        if re.search(r'~(?![01])', key):
            raise ValueError('invalid JSON escape')
        key = key.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not re.fullmatch(r'0|[1-9][0-9]*', key):
                raise ValueError('invalid array index')
            value = value[int(key)]
        else:
            value = value[key]
    return value


def _leaves(value, prefix=''):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f'{prefix}.{key}' if prefix else key)
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            yield from _leaves(item, f'{prefix}.{index}')
    else:
        yield prefix, value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _check_evidence(quote, evidence):
    if not isinstance(evidence, CombinedQuoteEvidence):
        return ['missing_combined_quote_evidence']
    if (type(evidence.body) is not bytes or not _sha(evidence.sha256)
            or hashlib.sha256(evidence.body).hexdigest() != evidence.sha256):
        return ['source_hash_mismatch']
    if not _text(evidence.source_ref) or evidence.provenance not in ('test_fixture', 'unverified_capture'):
        return ['unverified_source_provenance']
    if (type(evidence.field_paths) is not tuple
            or any(type(pair) is not tuple or len(pair) != 2 for pair in evidence.field_paths)):
        return ['invalid_quote_binding']
    try:
        # Decimal consumes the original JSON token, before any binary-float
        # rounding can turn a changed source price/line into an apparent match.
        # JSON integers already parse losslessly as Python ints. Keep strict
        # duplicate-key/nonfinite checks and apply the existing wire bounds.
        body = json.loads(evidence.body, object_pairs_hook=_unique_object, parse_float=Decimal,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        node = _pointer(body, evidence.quote_pointer)
        if not isinstance(node, dict):
            return ['invalid_combined_quote_object']
        fields = dict(evidence.field_paths)
        expected = dict(_leaves(asdict(quote)))
        # The exact list is also bound, so an omitted or extra raw leg cannot
        # silently become a different combination. IDs alone are insufficient.
        if len(fields) != len(evidence.field_paths) or set(fields) != set(expected) | {'leg_count'}:
            return ['incomplete_quote_binding']
        if not all(isinstance(path, str) for path in fields.values()):
            return ['invalid_quote_binding']
        if type(_pointer(node, fields['leg_count'])) is not list:
            return ['invalid_leg_list_binding']
        if len(_pointer(node, fields['leg_count'])) != len(quote.legs):
            return ['source_leg_count_mismatch']
        leg_root = fields['leg_count']
        if not leg_root or fields['decimal_odds'].startswith(leg_root + '/'):
            return ['combined_price_binding_required']
        # Bind every leg to its corresponding member of the actual quoted
        # list. A selection elsewhere in the response is not a quoted leg.
        for key in expected:
            if key.startswith('legs.'):
                index = key.split('.')[1]
                if not fields[key].startswith(leg_root + '/' + index + '/'):
                    return ['leg_outside_combined_quote']
            elif fields[key] == leg_root or fields[key].startswith(leg_root + '/'):
                # A leg's identity, active state or lifetime is not evidence
                # for the combination. This applies to all quote fields, not
                # only its price: an active leg cannot revive a suspended SGM.
                # Include the slash boundary so sibling metadata stays valid.
                return ['quote_field_inside_leg_list:' + key]
        reasons = []
        for key, value in expected.items():
            raw = _pointer(node, fields[key])
            if key == 'decimal_odds' or key.endswith('.line') and value is not None:
                equal = _number(raw) == _number(value)
            else:
                equal = type(raw) is type(value) and raw == value
            if not equal:
                reasons.append('source_field_mismatch:' + key)
        return reasons
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError, DecimalException):
        return ['invalid_quote_binding']


def _leg_key(leg):
    """Integer-score payoff aliases only, with explicit tie/OT semantics."""
    line = None if leg.line is None else _number(leg.line)
    base, side = leg.market, leg.side
    no_draw = (leg.scope.period == 'full_game' and leg.scope.includes_overtime
               and (leg.league == 'WNBA' or leg.league == 'NHL' and leg.scope.includes_shootout))
    if base == 'spreads':
        if line == 0 or no_draw and line in (Decimal('-.5'), Decimal('.5')):
            base, line = 'moneyline', None
        elif line == Decimal('-.5'):
            base, line = 'three_way', None
    if no_draw and base == 'three_way' and side != 'draw':
        base = 'moneyline'
    return base, side, _canonical_number(line) if line is not None else None


def _joint_win_possible(legs):
    """Exact integer feasibility, not a forecast or a settlement engine.

    Supported score legs bound H, A, H+A and H-A. Eliminate A by comparing
    every lower affine bound with every upper bound. Remaining constraints
    bound integer H, so floor/ceiling division preserves parity exactly.
    There is no guessed maximum score or pairwise-only compatibility check.
    A push is not a win: combinations without any all-winning result are
    conservatively rejected pending verified combined void/push rules.
    """
    bounds = {'home': [0, None], 'away': [0, None],
              'total': [0, None], 'margin': [None, None]}

    def bound(key, low=None, high=None):
        old_low, old_high = bounds[key]
        if low is not None:
            bounds[key][0] = low if old_low is None else max(low, old_low)
        if high is not None:
            bounds[key][1] = high if old_high is None else min(high, old_high)

    for leg in legs:
        line = _number(leg.line) if leg.line is not None else Decimal(0)
        # Lines are validated as integer or half-integer before reaching here.
        floor = int(line.to_integral_value(rounding='ROUND_FLOOR'))
        ceil = int(line.to_integral_value(rounding='ROUND_CEILING'))
        if leg.market in ('moneyline', 'three_way'):
            if leg.side == 'home':
                bound('margin', low=1)
            elif leg.side == 'away':
                bound('margin', high=-1)
            else:
                bound('margin', low=0, high=0)
        elif leg.market == 'spreads':
            if leg.side == 'home':
                bound('margin', low=1 - ceil)
            else:
                bound('margin', high=ceil - 1)
        else:
            key = {'totals': 'total', 'home_total': 'home', 'away_total': 'away'}[leg.market]
            if leg.side == 'over':
                bound(key, low=floor + 1)
            else:
                bound(key, high=ceil - 1)

    def feasible(margin_low=None, margin_high=None):
        hl, hu = bounds['home']
        al, au = bounds['away']
        tl, tu = bounds['total']
        dl, du = bounds['margin']
        if margin_low is not None:
            dl = margin_low if dl is None else max(dl, margin_low)
        if margin_high is not None:
            du = margin_high if du is None else min(du, margin_high)
        for low, high in ((hl, hu), (al, au), (tl, tu), (dl, du)):
            if low is not None and high is not None and low > high:
                return False
        lower = [(0, al), (-1, tl)]
        upper = []
        if du is not None:
            lower.append((1, -du))
        if au is not None:
            upper.append((0, au))
        if tu is not None:
            upper.append((-1, tu))
        if dl is not None:
            upper.append((1, -dl))
        for lm, lb in lower:
            for um, ub in upper:
                coefficient, rhs = lm - um, ub - lb
                if coefficient == 0:
                    if rhs < 0:
                        return False
                elif coefficient > 0:
                    limit = rhs // coefficient
                    hu = limit if hu is None else min(hu, limit)
                else:
                    hl = max(hl, -((-rhs) // coefficient))
        return hu is None or hl <= hu

    scope = legs[0].scope
    no_draw = (legs[0].league in ('WNBA', 'NHL')
               and scope.period == 'full_game' and scope.includes_overtime)
    return (feasible(margin_low=1) or feasible(margin_high=-1)) if no_draw else feasible()


def validate_sgm_quote(quote, evidence, *, now):
    """Check a normalized quote without claiming live support or independence.

    The effective deadline is the earliest of receipt+300s, source publication
    +300s (if actually provided), source expiry and event start. Exact deadlines
    are expired. Different scopes are conservatively blocked in this version.
    """
    # Decimal.normalize(), line arithmetic and exponent rendering otherwise
    # inherit the caller's context, potentially merging distinct contract IDs
    # or rounding unsupported split lines into accepted half lines. Preserve
    # the original default-context identities. Wire inputs have at most 24
    # digits; doubling a line needs at most 25, so 28 is exact here. Specify all
    # settings rather than inheriting mutable DefaultContext, and isolate flags.
    with localcontext(Context(
        prec=28, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
        capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )):
        return _validate_sgm_quote(quote, evidence, now=now)


def _validate_sgm_quote(quote, evidence, *, now):
    reasons = []
    deadline = None
    if not isinstance(quote, SGMQuote):
        return SGMValidation(False, ('invalid_quote_type',), None, None, None, None)
    if not all(_text(x) for x in (quote.quote_id, quote.fixture_id, quote.league)):
        reasons.append('missing_quote_identity')
    if quote.league not in ('MLS', 'NFL', 'NHL', 'WNBA'):
        reasons.append('unsupported_league')
    if quote.book != 'stake':
        reasons.append('unsupported_book')
    if quote.phase != 'prematch':
        reasons.append('unsupported_quote_phase')
    if quote.price_origin != 'stake_combined_quote':
        reasons.append('combined_price_required')
    if quote.status != 'active':
        reasons.append('quote_not_active')
    if not _sha(quote.combination_rules_sha256):
        reasons.append('missing_combination_settlement_rules')
    try:
        if not _text(quote.decimal_odds) or _number(quote.decimal_odds) <= 1:
            raise ValueError('invalid decimal odds')
    except ValueError:
        reasons.append('invalid_combined_odds')
    try:
        clock = _time(now)
        observed = _time(evidence.observed_at) if isinstance(evidence, CombinedQuoteEvidence) else None
        start = _time(quote.event_start)
        if observed is None:
            raise ValueError('missing receipt time')
        if observed > clock:
            reasons.append('future_observation')
        deadline = min(observed + timedelta(seconds=300), start)
        if start <= clock:
            reasons.append('event_started')
        if quote.source_published_at is not None:
            published = _time(quote.source_published_at)
            if published > observed:
                reasons.append('source_time_after_observation')
            deadline = min(deadline, published + timedelta(seconds=300))
        if quote.source_expires_at is not None:
            expiry = _time(quote.source_expires_at)
            if expiry <= observed:
                reasons.append('source_expired_at_observation')
            if quote.source_published_at is not None and expiry <= published:
                reasons.append('invalid_source_lifetime')
            deadline = min(deadline, expiry)
        if clock >= deadline:
            reasons.append('quote_expired')
    except (ValueError, TypeError, OverflowError):
        reasons.append('invalid_quote_time')
    if type(quote.legs) is not tuple or len(quote.legs) < 2:
        reasons.append('at_least_two_immutable_legs_required')
        legs = ()
    else:
        legs = quote.legs
    exact_ids, payoffs = set(), set()
    scope = None
    for leg in legs:
        if not isinstance(leg, SGMLeg) or not isinstance(leg.scope, SGMScope):
            reasons.append('invalid_leg_type')
            continue
        if not all(_text(x) for x in (leg.market_id, leg.outcome_id, leg.market_name,
                                     leg.outcome_name, leg.group_id, leg.group_name)) or type(leg.specifiers) is not str:
            reasons.append('missing_exact_leg_identity')
            continue
        if leg.league != quote.league or leg.fixture_id != quote.fixture_id:
            reasons.append('different_event_or_league')
        if leg.book != quote.book or leg.book != 'stake':
            reasons.append('different_leg_book')
        if leg.phase != quote.phase or leg.phase != 'prematch':
            reasons.append('incompatible_leg_phase')
        if leg.active is not True:
            reasons.append('leg_not_active')
        s = leg.scope
        periods = ('full_game', 'regulation', 'half_1', 'half_2', 'quarter_1',
                   'quarter_2', 'quarter_3', 'quarter_4', 'period_1', 'period_2', 'period_3')
        if (s.period not in periods or type(s.includes_overtime) is not bool
                or type(s.includes_shootout) is not bool or not _sha(s.settlement_rules_sha256)
                or s.period != 'full_game' and (s.includes_overtime or s.includes_shootout)
                or s.includes_shootout and (leg.league != 'NHL' or not s.includes_overtime)
                or s.period.startswith('quarter_') and leg.league not in ('WNBA', 'NFL')
                or s.period.startswith('period_') and leg.league != 'NHL'
                or s.period.startswith('half_') and leg.league == 'NHL'
                or leg.league == 'MLS' and (s.includes_overtime or s.includes_shootout)
                or s.period == 'full_game' and (
                    leg.league == 'MLS' or not s.includes_overtime
                    or leg.league == 'NHL' and not s.includes_shootout)):
            reasons.append('invalid_settlement_scope')
        if scope is not None and scope != s:
            reasons.append('incompatible_leg_scopes')
        scope = s
        selection_id = (leg.market_id, leg.outcome_id)
        if selection_id in exact_ids:
            reasons.append('duplicate_leg')
        exact_ids.add(selection_id)
        try:
            if leg.market in ('spreads', 'totals', 'home_total', 'away_total'):
                if not _text(leg.line):
                    raise ValueError('normalized line must be a decimal string')
                line = _number(leg.line)
                if line * 2 != (line * 2).to_integral_value():
                    raise ValueError('split lines require separate settlement')
                if leg.market != 'spreads' and line < 0:
                    raise ValueError('negative score totals unsupported')
                sides = ('home', 'away') if leg.market == 'spreads' else ('over', 'under')
            elif leg.market in ('moneyline', 'three_way'):
                if leg.line is not None:
                    raise ValueError('unexpected line')
                sides = ('home', 'away', 'draw') if leg.market == 'three_way' else ('home', 'away')
            else:
                raise ValueError('unsupported leg semantics')
            if leg.side not in sides:
                raise ValueError('invalid side')
            if (leg.side == 'draw' and s.period == 'full_game'
                    and leg.league in ('WNBA', 'NHL')):
                raise ValueError('draw impossible in this settlement scope')
            key = _leg_key(leg)
            if key in payoffs:
                reasons.append('equivalent_leg')
            payoffs.add(key)
        except (ValueError, TypeError, DecimalException):
            reasons.append('unsupported_leg_contract')
    if not reasons and not _joint_win_possible(legs):
        reasons.append('incompatible_leg_outcomes')
    # A malformed internal record must be rejected before serializing it.
    if all(isinstance(x, SGMLeg) and isinstance(x.scope, SGMScope) for x in legs):
        reasons.extend(_check_evidence(quote, evidence))
    reasons = tuple(dict.fromkeys(reasons))
    contract_id = fingerprint = cluster = None
    if not reasons:
        identities = []
        for leg in legs:
            # Display names, navigation groups and activity are quote evidence,
            # not new contracts. Retain exact provider IDs and specifiers.
            identity = {key: value for key, value in asdict(leg).items()
                        if key not in ('active', 'market_name', 'outcome_name', 'group_id', 'group_name')}
            if identity['line'] is not None:
                identity['line'] = _canonical_number(identity['line'])
            identities.append(identity)
        contract_id = _digest({'version': VERSION, 'legs': sorted(identities, key=_digest),
                               'combination_rules_sha256': quote.combination_rules_sha256})
        fingerprint = _digest({'contract_id': contract_id, 'quote': asdict(quote),
                               'source_sha256': evidence.sha256, 'observed_at': evidence.observed_at,
                               'source_ref': evidence.source_ref, 'provenance': evidence.provenance,
                               'quote_pointer': evidence.quote_pointer, 'field_paths': evidence.field_paths})
        cluster = quote.league + ':' + quote.fixture_id
    return SGMValidation(not reasons, reasons, contract_id, fingerprint,
                         deadline.isoformat() if deadline else None, cluster)
