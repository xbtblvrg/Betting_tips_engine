"""Public reference observations, separate from sports forecasts and orders.

Only anonymous GET endpoints are exposed. Complete bounded response bytes are
content-addressed before parsing. Binary winner observations retain prediction-
market payout terms; they never become Stake quotes or calibrated probabilities.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

VERSION = 'public-polymarket-reference-v1'
MAX_BODY = 2 * 1024 * 1024
MAX_MARKETS = 256
MAX_LEVELS = 4096
MAX_QUOTE_AGE = 300


class ReferenceRejected(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _number(value, *, low=Decimal('0'), high=None):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ReferenceRejected('invalid_numeric_value')
    if len(str(value)) > 128:raise ReferenceRejected('numeric_value_outside_bounds')
    try:value = Decimal(str(value))
    except InvalidOperation as exc:raise ReferenceRejected('invalid_numeric_value') from exc
    if (not value.is_finite() or abs(value.as_tuple().exponent) > 18 or value < low
            or value > (Decimal('1e15') if high is None else high)):
        raise ReferenceRejected('numeric_value_outside_bounds')
    return value


def _when(value):
    if not isinstance(value, str):raise ReferenceRejected('timestamp_missing')
    try:value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:raise ReferenceRejected('timestamp_invalid') from exc
    if value.tzinfo is None:raise ReferenceRejected('timestamp_timezone_missing')
    return value.astimezone(timezone.utc)


def _json(body):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:raise ReferenceRejected('duplicate_json_key')
            result[key] = value
        return result
    try:
        value = json.loads(body, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ReferenceRejected('nonfinite_json')))
        pending = [(value, 0)]
        while pending:
            row, depth = pending.pop()
            if depth > 32:raise ReferenceRejected('json_nesting_exceeded')
            if isinstance(row, float) and not math.isfinite(row):raise ReferenceRejected('nonfinite_json')
            if isinstance(row, dict):pending.extend((v, depth+1) for v in row.values())
            elif isinstance(row, list):pending.extend((v, depth+1) for v in row)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReferenceRejected('invalid_json') from exc


def _identifier(value, maximum=80):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,'+str(maximum)+'}', value):
        raise ReferenceRejected('invalid_public_identifier')
    return value


@dataclass(frozen=True)
class PublicDocument:
    value: object
    receipt: dict


class PublicReferenceClient:
    """Explicit archive destination; no credentials, provider cache or order API."""
    def __init__(self, archive, *, client=None, max_body=MAX_BODY, deadline=12):
        if type(max_body) is not int or not 0 < max_body <= MAX_BODY:
            raise ValueError('Invalid public response bound')
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not 0 < deadline <= 30:
            raise ValueError('Invalid public request deadline')
        if client is not None and (getattr(client, 'auth', None) is not None
                or bool(getattr(client, 'cookies', None)) or bool(getattr(client, 'params', None))
                or any(key.lower() not in ('accept','accept-encoding','connection','user-agent') for key in getattr(client, 'headers', {}))):
            raise ValueError('Authenticated clients are outside public reference scope')
        self.archive = Path(archive).absolute()
        if any(path.is_symlink() for path in (self.archive, *self.archive.parents)):
            raise ValueError('Archive destination must not use symlinks')
        self.max_body, self.deadline = max_body, float(deadline)
        self.client = client or httpx.Client(follow_redirects=False, trust_env=False,
            headers={'User-Agent':'BettingReferenceObservation/1.0', 'Accept-Encoding':'identity'})
        self._owns_client = client is None

    def close(self):
        if self._owns_client:self.client.close()

    def _get(self, url):
        # A prior public response may have set cookies; never replay those.
        if self.client.cookies:
            self.client.cookies.clear()
        if (self.client.auth is not None or self.client.params
                or any(key.lower() not in ('accept','accept-encoding','connection','user-agent') for key in self.client.headers)):
            raise ReferenceRejected('client_authentication_context_changed')
        started = datetime.now(timezone.utc);clock = time.monotonic()
        chunks = [];size = 0;status = None;complete = False;error = None
        try:
            # Each socket wait is at most three seconds; elapsed deadline is
            # checked between bounded chunks, so a stalled peer cannot wait forever.
            with self.client.stream('GET', url, timeout=min(3., self.deadline),
                    follow_redirects=False) as response:
                status = response.status_code
                for chunk in response.iter_bytes(chunk_size=16384):
                    if time.monotonic()-clock > self.deadline:raise ReferenceRejected('request_deadline')
                    remaining = self.max_body-size
                    chunks.append(chunk[:remaining]);size += min(len(chunk), remaining)
                    if len(chunk) > remaining:raise ReferenceRejected('response_too_large')
                complete = True
                if status != 200:raise ReferenceRejected('http_status_'+str(status))
        except (httpx.HTTPError, ReferenceRejected) as exc:
            error = exc.reason if isinstance(exc, ReferenceRejected) else type(exc).__name__
        body = b''.join(chunks);digest = hashlib.sha256(body).hexdigest()
        # Record failures as failures; a prefix hash is never a complete-body pin.
        receipt = {'version':VERSION, 'url':url, 'method':'GET', 'authentication':'none',
                   'started_at':started.isoformat(), 'fetched_at':datetime.now(timezone.utc).isoformat(),
                   'elapsed_seconds':time.monotonic()-clock, 'deadline_seconds':self.deadline,
                   'maximum_socket_wait_seconds':min(3., self.deadline), 'status':status,
                   'body_complete':complete, 'bytes':len(body), 'sha256':digest,
                   'archive':digest+'.body', 'receipt_path':'receipt-'+uuid4().hex+'.json', 'error':error}
        if any(path.is_symlink() for path in (self.archive, *self.archive.parents)):
            raise ReferenceRejected('archive_destination_changed')
        self.archive.mkdir(parents=True, exist_ok=True)
        target = self.archive/(digest+'.body')
        try:
            with target.open('xb') as handle:handle.write(body)
        except FileExistsError:
            if target.is_symlink() or target.read_bytes() != body:raise ReferenceRejected('archive_collision')
        try:
            if error is not None:raise ReferenceRejected(error)
            value = _json(body)
        except ReferenceRejected as exc:
            receipt['error'] = exc.reason
            exc.receipt = receipt
            raise
        finally:
            (self.archive/receipt['receipt_path']).write_text(json.dumps(receipt, sort_keys=True)+'\n')
        return PublicDocument(value, receipt)

    def sports(self):return self._get('https://gamma-api.polymarket.com/sports')

    def event(self, identity):
        return self._get('https://gamma-api.polymarket.com/events/'+_identifier(identity, 20))

    def events(self, series, *, offset=0, limit=10):
        _identifier(series, 20)
        if type(offset) is not int or not 0 <= offset <= 1000 or type(limit) is not int or not 1 <= limit <= 10:
            raise ReferenceRejected('invalid_discovery_bound')
        return self._get('https://gamma-api.polymarket.com/events?'+urlencode(
            {'closed':'false', 'series_id':series, 'offset':offset, 'limit':limit}))

    def book(self, token):
        return self._get('https://clob.polymarket.com/book?'+urlencode({'token_id':_identifier(token)}))


def _name(value):
    if not isinstance(value, str):return ''
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', value).encode('ascii','ignore').decode().lower())


def match_game(event, games):
    """Exact team names/abbreviations and exact current start, never fuzzy teams."""
    start = _when(event.get('startTime'))
    teams = event.get('teams')
    if not isinstance(teams, list) or len(teams) != 2:raise ReferenceRejected('event_team_identity_missing')
    sides = {}
    for side in ('home','away'):
        found = [team for team in teams if isinstance(team, dict) and team.get('ordering') == side]
        if len(found) != 1:raise ReferenceRejected('event_team_order_ambiguous')
        sides[side] = found[0]
    matches = []
    for game in games:
        if game.get('state') != 'scheduled':continue
        if any(str(sides[side].get('league','')).upper() != game.get('league') for side in sides):continue
        if any(not _name(sides[side].get('name')) or _name(sides[side]['name']) != _name(game.get(side)) for side in sides):continue
        if _when(game.get('start')) != start:continue
        matches.append(game)
    if len(matches) != 1:raise ReferenceRejected('no_unique_exact_scheduled_game')
    return matches[0]


def _fee(market):
    enabled = market.get('feesEnabled')
    if type(enabled) is not bool:raise ReferenceRejected('fee_state_unknown')
    if not enabled:return Decimal('0'), {'fees_enabled':False, 'rate':'0', 'source':'Gamma explicit feesEnabled=false'}
    schedule = market.get('feeSchedule')
    if not isinstance(schedule, dict) or schedule.get('takerOnly') is not True or schedule.get('exponent') != 1 or isinstance(schedule.get('exponent'), bool):
        raise ReferenceRejected('fee_formula_unverified')
    rate = _number(schedule.get('rate'), high=Decimal('1'))
    return rate, {'fees_enabled':True, 'rate':str(rate), 'exponent':1, 'taker_only':True,
                  'formula':'shares * rate * price * (1 - price)', 'source':'Gamma feeSchedule',
                  'rounding_note':'Unrounded fee estimate; venue applies its current precision rules.'}


def winner_contract(event, market, game):
    """Conservative full-game winner extraction; source rules stay separately bound."""
    if event.get('active') is not True or event.get('closed') is not False or market.get('active') is not True or market.get('closed') is not False or market.get('acceptingOrders') is not True:
        raise ReferenceRejected('market_inactive')
    if market.get('sportsMarketType') != 'moneyline':raise ReferenceRejected('unsupported_market_kind')
    if _when(market.get('gameStartTime')) != _when(event.get('startTime')):
        raise ReferenceRejected('structured_start_conflict')
    title = str(event.get('title',''))+' '+str(market.get('question',''))
    description = market.get('description')
    if not isinstance(description,str) or not description:raise ReferenceRejected('settlement_rules_missing')
    if re.search(r'\b(?:half|quarters?|innings?|periods?|regulation|f[135]|[12]h|q[1-4]|p[1-3])\b', title, re.I):
        raise ReferenceRejected('non_full_game_contract')
    if re.search(r'\b(?:regulation only|excluding overtime|first \d+ innings?|first half)\b',description,re.I):
        raise ReferenceRejected('settlement_scope_conflict')
    condition = market.get('conditionId')
    if not isinstance(condition,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',condition):raise ReferenceRejected('condition_id_invalid')
    outcomes, tokens = market.get('outcomes'), market.get('clobTokenIds')
    if isinstance(outcomes,str):outcomes = _json(outcomes)
    if isinstance(tokens,str):tokens = _json(tokens)
    if not isinstance(outcomes,list) or not isinstance(tokens,list) or len(outcomes)!=2 or len(tokens)!=2 or any(not isinstance(token,str) for token in tokens) or len(set(tokens))!=2:
        raise ReferenceRejected('binary_outcome_identity_invalid')
    selection=[]
    for label, token in zip(outcomes,tokens):
        side=[s for s in ('home','away')if _name(label) and _name(label)==_name(game.get(s))]
        if len(side)!=1:raise ReferenceRejected('outcome_team_identity_conflict')
        selection.append({'side':side[0], 'label':label, 'token_id':_identifier(token)})
    if len({row['side']for row in selection})!=2:raise ReferenceRejected('duplicate_outcome_side')
    rate, fees = _fee(market)
    reasons=[]
    # A known ET description date is interpreted explicitly, never ignored when
    # structured fields have been updated after rescheduling.
    dates=re.findall(r'scheduled for ([A-Za-z]+ \d{1,2}) at (\d{1,2}:\d{2}[AP]M) ET',description,re.I)
    if not dates:reasons.append('description_schedule_not_verified')
    for date,clock in dates:
        try:
            stamp=datetime.strptime(str(_when(game['start']).year)+' '+date+' '+clock.upper(),'%Y %B %d %I:%M%p').replace(tzinfo=ZoneInfo('America/New_York'))
        except ValueError:reasons.append('description_schedule_not_verified');continue
        if stamp.astimezone(timezone.utc)!=_when(game['start']):reasons.append('description_schedule_conflict')
    half_payout=bool(re.search(r'resolve 50[–-]50',description,re.I))
    if not half_payout or not re.search(r'canceled|cancelled',description,re.I) or not re.search(r'\btie\b',description,re.I):
        reasons.append('tie_cancellation_terms_not_verified')
    reasons.append('sportsbook_settlement_equivalence_not_established')
    return {'game_id':game['id'], 'league':game['league'], 'start':game['start'], 'scope':'full_game',
            'market_kind':'moneyline', 'line':None, 'condition_id':condition, 'selections':selection,
            'rules_sha256':hashlib.sha256(description.encode()).hexdigest(), 'fees':fees,
            'prediction_market_payout':{'winner':'1', 'loser':'0', 'tie_or_cancellation':'0.5' if half_payout else None},
            'settlement_equivalence':'not_established', 'comparison_rejections':sorted(set(reasons))},rate


def book_quote(book, *, token, condition, rate, observed_at, notional='5'):
    if not isinstance(book,dict) or book.get('asset_id')!=token or book.get('market')!=condition:
        raise ReferenceRejected('book_identity_mismatch')
    now=_when(observed_at);stamp=book.get('timestamp')
    if not isinstance(stamp,(str,int)) or isinstance(stamp,bool) or not re.fullmatch(r'\d{10,16}',str(stamp)):
        raise ReferenceRejected('book_timestamp_invalid')
    try:stamp=datetime.fromtimestamp(int(stamp)/1000,timezone.utc)
    except (ValueError,OverflowError,OSError) as exc:raise ReferenceRejected('book_timestamp_invalid')from exc
    age=(now-stamp).total_seconds()
    if not -2 <= age <= MAX_QUOTE_AGE:raise ReferenceRejected('book_stale_or_future')
    levels={}
    for side in ('bids','asks'):
        rows=book.get(side)
        if not isinstance(rows,list) or not 1<=len(rows)<=MAX_LEVELS:raise ReferenceRejected('book_depth_missing_or_excessive')
        parsed=[];prices=set()
        for row in rows:
            if not isinstance(row,dict):raise ReferenceRejected('book_level_invalid')
            p=_number(row.get('price'),high=Decimal('1'));q=_number(row.get('size'))
            if not 0<p<1 or q<=0 or p in prices:raise ReferenceRejected('book_level_invalid')
            prices.add(p);parsed.append((p,q))
        levels[side]=sorted(parsed,reverse=side=='bids')
    bid,ask=levels['bids'][0][0],levels['asks'][0][0]
    if bid>=ask:raise ReferenceRejected('book_crossed_or_locked')
    minimum=_number(book.get('min_order_size'));tick=_number(book.get('tick_size'),high=Decimal('1'))
    if minimum<=0 or tick<=0:raise ReferenceRejected('book_trading_constraints_invalid')
    if any(price%tick for rows in levels.values()for price,_ in rows):raise ReferenceRejected('book_tick_mismatch')
    cash=_number(notional,high=Decimal('100'));rate=_number(rate,high=Decimal('1'))
    if cash<=0:raise ReferenceRejected('notional_invalid')
    remaining=cash;shares=fees=cost=Decimal('0');used=[]
    for price,available in levels['asks']:
        fee=rate*price*(1-price);quantity=min(available,remaining/(price+fee))
        shares+=quantity;fees+=quantity*fee;cost+=quantity*price;remaining-=quantity*(price+fee)
        used.append({'price':str(price),'shares':str(quantity)})
        if remaining<=Decimal('0.000000000001'):break
    reasons=[]
    if remaining>Decimal('0.000000000001'):reasons.append('insufficient_buy_depth')
    if shares<minimum:reasons.append('below_minimum_shares')
    sell_remaining=shares;sell_gross=sell_fees=Decimal('0')
    for price,available in levels['bids']:
        quantity=min(available,sell_remaining);sell_gross+=quantity*price
        sell_fees+=quantity*rate*price*(1-price);sell_remaining-=quantity
        if sell_remaining<=Decimal('0.000000000001'):break
    sell_reasons=[]
    if sell_remaining>Decimal('0.000000000001'):sell_reasons.append('insufficient_sell_depth')
    if shares<minimum:sell_reasons.append('below_minimum_shares')
    return {'token_id':token, 'condition_id':condition, 'source_timestamp':stamp.isoformat(),
            'age_seconds':age,'best_bid':str(bid),'best_ask':str(ask),'spread':str(ask-bid),
            'midpoint_index':str((bid+ask)/2),'bid_depth_shares':str(sum(q for _,q in levels['bids'])),
            'ask_depth_shares':str(sum(q for _,q in levels['asks'])), 'minimum_order_shares':str(minimum),
            'tick_size':str(tick), 'requested_notional':str(cash), 'buy_shares':str(shares),
            'buy_trade_cost':str(cost),'buy_fee_estimate':str(fees),'buy_effective_cost_per_share':str((cost+fees)/shares),
            'sell_gross_proceeds':str(sell_gross),'sell_fee_estimate':str(sell_fees),
            'sell_net_proceeds':str(sell_gross-sell_fees),'sell_rejections':sell_reasons,
            'buy_levels':used,'liquidity_state':'sufficient' if not reasons else 'insufficient',
            'rejections':reasons,'claim':'Observed order-book depth and estimated taker cost; no order submission or guaranteed fill.'}


def observe_event(client, identity, games, *, observed_at=None, notional='5'):
    """Collect source-bound reference observations without touching model forecasts."""
    event_doc=client.event(identity);event=event_doc.value
    if not isinstance(event,dict) or str(event.get('id'))!=identity:raise ReferenceRejected('event_schema_invalid')
    checked_at=observed_at or datetime.now(timezone.utc).isoformat()
    age=(_when(checked_at)-_when(event_doc.receipt.get('fetched_at'))).total_seconds()
    if not -2<=age<=MAX_QUOTE_AGE:raise ReferenceRejected('event_metadata_stale_or_future')
    markets=event.get('markets')
    if not isinstance(markets,list) or len(markets)>MAX_MARKETS:raise ReferenceRejected('event_market_bound')
    result={'version':VERSION,'event_id':identity,'observed_at':checked_at,
            'source_receipts':[event_doc.receipt],'observations':[],'rejections':[],
            'sports_forecast_modified':False,'stake_quote_substitution':False,'orders_submitted':False}
    try:game=match_game(event,games)
    except ReferenceRejected as exc:
        result['rejections'].append({'event_id':identity,'reason':exc.reason});return result
    lead=(_when(game['start'])-_when(checked_at)).total_seconds()
    if not 0<lead<=48*3600:
        result['rejections'].append({'event_id':identity,'reason':'outside_pregame_48h'});return result
    if sum(isinstance(m,dict) and m.get('sportsMarketType')=='moneyline' for m in markets)>1:
        result['rejections'].append({'event_id':identity,'reason':'duplicate_full_game_winner_market'});return result
    for market in markets:
        if not isinstance(market,dict):result['rejections'].append({'reason':'market_schema_invalid'});continue
        try:
            contract,rate=winner_contract(event,market,game);quotes=[]
            for selection in contract['selections']:
                document=client.book(selection['token_id']);result['source_receipts'].append(document.receipt)
                quote=book_quote(document.value,token=selection['token_id'],condition=contract['condition_id'],
                    rate=rate,observed_at=observed_at or datetime.now(timezone.utc).isoformat(),notional=notional)
                quotes.append({**selection,**quote,'source_sha256':document.receipt['sha256']})
            total=sum(Decimal(quote['midpoint_index'])for quote in quotes)
            result['observations'].append({'market_id':str(market.get('id')), 'contract':contract,
                'quotes':quotes,'paired_midpoint_index':{quote['side']:str(Decimal(quote['midpoint_index'])/total)for quote in quotes},
                'comparable_to_sportsbook':False,'usable_for_automatic_blend':False,
                'claim':'Prediction-market price index only; exact settlement mapping and validated blend remain separate gates.'})
        except ReferenceRejected as exc:
            if getattr(exc, 'receipt', None) is not None:result['source_receipts'].append(exc.receipt)
            result['rejections'].append({'market_id':str(market.get('id')),'reason':exc.reason})
    return result
