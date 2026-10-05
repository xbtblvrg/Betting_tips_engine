"""Platform adapters and strict matching. Public data reads only; no order API."""
from __future__ import annotations
from dataclasses import dataclass,asdict
from datetime import timedelta, datetime, timezone
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter
import json,re,hashlib,unicodedata,math
from .domain import Game,utcnow,parse_time,LEAGUES
from .providers import Fetcher,DATA
from .team_identity import exact_team_name_match, team_name_variants

@dataclass
class Market:
    id:str
    game_id:str
    league:str
    kind:str
    side:str
    line:float|None
    selection:str
    platform:str
    url:str
    token:str
    condition_id:str
    description:str
    fee_rate:float
    fee_exponent:float
    minimum_notional:float
    market_start:str
    fetched_at:str
    metadata:dict
    def dict(self):return asdict(self)

@dataclass
class Quote:
    market_id:str
    token:str
    price:float
    fee_per_share:float
    effective_cost:float
    available_shares:float
    minimum_notional:float
    fetched_at:str
    source_timestamp:str
    status:str='current'
    def dict(self):return asdict(self)

ALIASES={'la':'losangeles','st':'saint'}
def normalize(s):
    s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()
    s=s.replace('l.a.','los angeles').replace('l.a.','los angeles')
    return re.sub(r'[^a-z0-9]','',s)

def team_matches(label,name,abbr=''):
    a,b=normalize(label),normalize(name)
    if not a:return False
    if exact_team_name_match(label,name) or (abbr and a==normalize(abbr)):return True
    # Exact mascot suffix, at least 4 characters; no approximate/fuzzy matching.
    if len(a)>=4 and b.endswith(a):return True
    return a.replace('la','losangeles',1)==b if a.startswith('la') else False

def _stake_contract_key(market):
    # Keep raw JSON identity hashable even for a malformed neighboring offer.
    # Status and exact outcomes bind tab/support attribution to the same offer.
    return json.dumps([market.get('id'), market.get('name'), market.get('specifiers'),
                       market.get('status'), market.get('outcomes')],
                      sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _stake_contract_rejection(market):
    outcomes = market.get('outcomes')
    if outcomes is not None and (not isinstance(outcomes, list) or any(not isinstance(o, dict) for o in outcomes)):
        return 'invalid_outcomes_type'
    if market.get('specifiers') is not None and not isinstance(market['specifiers'], str):
        return 'invalid_specifiers_type'
    if (not isinstance(market.get('name'), str)
            or isinstance(market.get('id'), (dict, list))):
        return 'invalid_market_identity'
    return None


def _stake_categorical_side(base, name, game):
    if base in ('odd_even', 'both_score'):
        side = name.lower()
        return side if side in (('odd', 'even') if base == 'odd_even' else ('yes', 'no')) else None
    if base == 'double_chance':
        labels = name.split(' or ')
        sides = ['draw' if label == 'Draw' else 'home' if team_matches(label, game.home, game.home_abbr)
                 else 'away' if team_matches(label, game.away, game.away_abbr) else None for label in labels]
        return {frozenset(('home', 'draw')): 'home_draw', frozenset(('away', 'draw')): 'away_draw',
                frozenset(('home', 'away')): 'home_away'}.get(frozenset(sides)) if len(sides) == 2 else None
    team = re.sub(r'\s*\([+-]?\d+(?:\.\d+)?\)\s*$', '', name) if base == 'spreads' else name
    return ('draw' if base == 'three_way' and name == 'Draw' else 'home' if team_matches(team, game.home, game.home_abbr)
            else 'away' if team_matches(team, game.away, game.away_abbr) else None)


def _stake_joint_side(base, name, game):
    """Parse the exact composite outcome labels used by Stake.

    The returned value is deliberately canonical and contains no odds-derived
    inference.  The eventual probability is calculated jointly from the
    scope-matched score distribution in ``contracts.contract_value``.
    """
    if base == 'winner_total':
        parts=re.split(r'\s*&\s*',str(name),maxsplit=1)
        if len(parts)!=2:return None
        outcome,total=parts
        side=_stake_categorical_side('three_way',outcome.strip(),game)
        if side is None:return None
        mt=re.fullmatch(r'(Over|Under)\s+([+-]?\d+(?:\.\d+)?)',total.strip(),re.I)
        return f'{side}_{mt[1].lower()}' if mt else None
    if base == 'exact_total':
        mt=re.fullmatch(r'(Over|Under|Exact)\s+([+-]?\d+(?:\.\d+)?)',str(name).strip(),re.I)
        return mt[1].lower() if mt else None
    if base == 'exact_score':
        return str(name).strip() if re.fullmatch(r'\d+:\d+',str(name).strip()) else None
    return None


def match_event(event,games,league):
    try:start=parse_time(event.get('startTime') or event['markets'][0]['gameStartTime'])
    except (ValueError,KeyError,IndexError,TypeError):return None
    teams=event.get('teams',[])
    home=[t for t in teams if t.get('ordering')=='home'];away=[t for t in teams if t.get('ordering')=='away']
    if len(home)!=1 or len(away)!=1:return None
    def agrees(t,name,abbr):return any(team_matches(t.get(k,''),name,abbr) for k in ('name','alias','abbreviation'))
    matches=[g for g in games if g.league==league and abs((parse_time(g.start)-start).total_seconds())<=900 and agrees(home[0],g.home,g.home_abbr) and agrees(away[0],g.away,g.away_abbr)]
    return matches[0] if len(matches)==1 else None

def parse_market(event,m,g,fetched_at):
    if not(m.get('active') and not m.get('closed') and m.get('acceptingOrders')):return [],'inactive'
    kind=m.get('sportsMarketType');desc=m.get('description','');question=m.get('question','')
    if kind not in ('moneyline','spreads','totals'):return [],'unsupported_market'
    # Full-game prices only; no regulation-only/period market substituted.
    if re.search(r'\b(?:half|quarters?|innings?|periods?|regulation|f[135]|[12]h|q[1-4]|p[1-3])\b',(question+' '+event.get('title','')).lower()):return [],'period_market'
    try:
        outcomes=json.loads(m['outcomes']) if isinstance(m['outcomes'],str) else m['outcomes'];tokens=json.loads(m['clobTokenIds']) if isinstance(m['clobTokenIds'],str) else m['clobTokenIds']
        if len(outcomes)!=2 or len(tokens)!=2:return [],'not_binary'
        line=float(m['line']) if m.get('line') is not None else None
        fees=m.get('feeSchedule')
        if m.get('feesEnabled') and (not fees or 'rate' not in fees):return [],'unknown_fees'
        rate=float(fees['rate']) if m.get('feesEnabled') else 0.;exponent=float((fees or {}).get('exponent',1))
        if exponent!=1:return [],'unsupported_fee_formula'
    except (ValueError,TypeError,KeyError,json.JSONDecodeError):return [],'schema_error'
    sides=[]
    for outcome in outcomes:
        if kind=='totals':side={'over':'over','under':'under'}.get(outcome.lower())
        else:
            hs=team_matches(outcome,g.home,g.home_abbr);aws=team_matches(outcome,g.away,g.away_abbr)
            side='home' if hs and not aws else ('away' if aws and not hs else None)
        if not side:return [],'unmatched_outcome'
        sides.append(side)
    if len(set(sides))!=2:return [],'duplicate_outcomes'
    if kind!='moneyline' and (line is None or line%1==0):return [],'integer_line_needs_settlement_rule'
    if kind=='spreads':
        anchor=re.search(r'^Spread:\s*(.*?)\s*\(([+-]?\d+(?:\.\d+)?)\)',question)
        if not anchor or not team_matches(anchor[1],g.home if sides[0]=='home' else g.away,g.home_abbr if sides[0]=='home' else g.away_abbr):return [],'spread_anchor_ambiguous'
        if float(anchor[2])!=line:return [],'line_conflict'
        # Explicit numerical description must agree with the structured line.
        described=re.search(r'win(?:s)?(?: the game)? by (\d+(?:\.\d+)?) or more',desc,re.I)
        more=re.search(r'win(?:s)?(?: the game)? by more than (\d+(?:\.\d+)?)',desc,re.I)
        if line<0:
            if described and float(described[1])!=int(abs(line))+1:return [],'description_line_conflict'
            if more and float(more[1])!=abs(line):return [],'description_line_conflict'
    result=[]
    for i,(outcome,side,token) in enumerate(zip(outcomes,sides,tokens)):
        actual_line=(line if i==0 else -line) if kind=='spreads' else line
        selection=(g.home if side=='home' else g.away) if kind=='moneyline' else ((g.home if side=='home' else g.away)+f' {actual_line:+g}' if kind=='spreads' else ('Összpontszám felett ' if side=='over' else 'Összpontszám alatt ')+f'{line:g}')
        result.append(Market(id=f'polymarket:{m["id"]}:{i}',game_id=g.id,league=g.league,kind=kind,side=side,line=actual_line,selection=selection,platform='Polymarket',url='https://polymarket.com/event/'+event['slug'],token=token,condition_id=m['conditionId'],description=desc,fee_rate=rate,fee_exponent=exponent,minimum_notional=float(m.get('orderMinSize') or 0),market_start=g.start,fetched_at=fetched_at,metadata={'raw_market_id':m['id'],'event_id':event['id'],'fee_source':'Gamma feeSchedule','fee_schedule':fees,'tie_payout':.5 if '50-50' in desc else None}))
    return result,None

class Polymarket:
    def __init__(self,fetcher=None):self.fetcher=fetcher or Fetcher()
    def discover(self,games,horizon=48):
        now=utcnow();end=now+timedelta(hours=horizon);markets=[];issues=[];events=[]
        sports,meta=self.fetcher.json('https://gamma-api.polymarket.com/sports',3600)
        def league_events(s):
            allrows=[]
            # Pagination is bounded and truncation is surfaced, not silent.
            for offset in range(0,1000,100):
                url=f'https://gamma-api.polymarket.com/events?closed=false&limit=100&series_id={s["series"]}&offset={offset}'
                d,m=self.fetcher.json(url,120)
                allrows.extend((e,m) for e in d)
                if len(d)<100:return allrows,False
            return allrows,True
        wanted=[s for s in sports if s.get('sport','').upper() in LEAGUES]
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures={pool.submit(league_events,s):s['sport'].upper() for s in wanted}
            for f in as_completed(futures):
                league=futures[f]
                try:rows,truncated=f.result()
                except Exception as e:issues.append({'league':league,'reason':'source_error','detail':str(e)});continue
                if truncated:issues.append({'league':league,'reason':'pagination_limit'})
                for e,m in rows:
                    try:start=parse_time(e.get('startTime') or '')
                    except ValueError:continue
                    if not now<start<=end:continue
                    g=match_event(e,games,league)
                    if not g:issues.append({'league':league,'event':e.get('title'),'reason':'unmatched_schedule'});continue
                    if g.state!='scheduled':continue
                    events.append({'league':league,'id':e['id'],'game_id':g.id,'title':e['title'],'start':g.start})
                    for raw in e.get('markets',[]):
                        ms,error=parse_market(e,raw,g,m['fetched_at']);markets.extend(ms)
                        if error:issues.append({'league':league,'event':e.get('title'),'market':raw.get('question'),'reason':error})
        return list({m.id:m for m in markets}.values()),events,issues
    def quotes(self,markets):
        result={};issues=[];unique={m.token:m for m in markets};tokens=list(unique)
        for pos in range(0,len(tokens),100):
            part=tokens[pos:pos+100]
            try:
                r=self.fetcher.client.post('https://clob.polymarket.com/books',json=[{'token_id':t} for t in part]);r.raise_for_status();books=r.json();fetched=utcnow().isoformat()
                archive=DATA/'quotes';archive.mkdir(parents=True,exist_ok=True);sha=hashlib.sha256(r.content).hexdigest()
                # Retain decision-relevant depth plus a hash of the full response.
                from .io_utils import atomic_json
                evidence={'fetched_at':fetched,'response_sha256':sha,'url':'https://clob.polymarket.com/books','books':[{k:b.get(k) for k in ('market','asset_id','timestamp','hash','min_order_size','tick_size')} | {'best_asks':sorted(b.get('asks',[]),key=lambda a:float(a['price']))[:3]} for b in books]}
                atomic_json(archive/(sha+'.json'),evidence)
                for b in books:
                    token=b.get('asset_id');m=unique.get(token)
                    if not m:continue
                    asks=sorted([(float(x['price']),float(x['size'])) for x in b.get('asks',[]) if 0<float(x['price'])<1 and float(x['size'])>0])
                    if not asks:continue
                    if b.get('market')!=m.condition_id:
                        issues.append({'market':m.id,'reason':'condition_id_mismatch'});continue
                    price,size=asks[0];fee=m.fee_rate*price*(1-price)
                    # Quote top-of-book price only if enough liquidity for $5 plus fees.
                    if size*(price+fee)<5:
                        issues.append({'market':m.id,'reason':'insufficient_top_level_depth'});continue
                    result[m.id]=Quote(m.id,token,price,fee,price+fee,size,m.minimum_notional,fetched,str(b.get('timestamp','')))
            except Exception as e:issues.append({'reason':'orderbook_error','detail':str(e)[:200]})
        return result,issues


class StakeSportsbook:
    """Read-only adapter for Stake's public Sports Data API.

    The engine deliberately uses this adapter as the recommendation market
    source.  Polymarket lines may be useful as a separate benchmark, but they
    are not interchangeable with a Stake sportsbook line or settlement
    contract.
    """
    BASE = 'https://odds-data.stake.com'
    SPORT_BY_LEAGUE = {
        'NBA': 'basketball', 'WNBA': 'basketball', 'MLB': 'baseball',
        'NHL': 'ice-hockey', 'NFL': 'american-football','NCAAF':'american-football','NCAAM':'basketball','NCAAW':'basketball',
        'MLS': 'soccer',
    }
    TOURNAMENT_BY_LEAGUE = {'NBA':'nba','WNBA':'wnba','MLB':'mlb','NHL':'nhl','NFL':'nfl','MLS':'major-league-soccer'}
    LEAGUE_PATH = {
        'NBA': 'basketball/usa/nba', 'WNBA': 'basketball/usa/wnba',
        'MLB': 'baseball/usa/mlb', 'NHL': 'ice-hockey/usa/nhl',
        'NFL': 'american-football/usa/nfl',
        'NCAAF':'american-football/usa/ncaa-regular','NCAAM':'basketball/usa/ncaa','NCAAW':'basketball/usa/ncaa-women',
        'MLS': 'soccer/usa/major-league-soccer',
    }
    LEAGUE_SCOPE = {
        'NBA': 'Overtime', 'WNBA': 'Overtime', 'NFL': 'Overtime',
        'MLB': 'Extra Innings', 'NHL': 'Overtime and Penalties','NCAAF':'Overtime','NCAAM':'Overtime','NCAAW':'Overtime',
        'MLS': 'Regular Time',
    }

    def __init__(self, fetcher=None,segment_markets=None,full_markets=None):
        self.fetcher = fetcher or Fetcher()
        self.inventory = []
        self.fixture_audit = []
        self.segment_markets=segment_markets or {}
        self.full_markets=full_markets or {}

    @staticmethod
    def _fixture_teams(name):
        parts = re.split(r'\s+[-–—]\s+', str(name or ''), maxsplit=1)
        return parts if len(parts) == 2 else []

    def _match_fixture(self, fixture, games, league):
        try:
            start = datetime.fromtimestamp(float(fixture['date']) / 1000, tz=timezone.utc)
        except (KeyError, TypeError, ValueError, OSError):
            return None
        if not fixture.get('enabled') or not fixture.get('preMatchEnabled'):
            return None
        if fixture.get('status') not in (None, 'active'):
            return None
        teams = self._fixture_teams(fixture.get('name'))
        if len(teams) != 2:
            return None
        matches = [
            g for g in games
            if g.league == league
            # Stake and league feeds can differ by a displayed half-hour
            # during schedule normalization. Exact team pairing keeps this
            # wider join unambiguous while retaining a conservative one-hour
            # window.
            and abs((parse_time(g.start) - start).total_seconds()) <= 3600
            and team_matches(teams[0], g.home, g.home_abbr)
            and team_matches(teams[1], g.away, g.away_abbr)
        ]
        # A provider can expose the fixture in away-home display order.  Keep
        # the match only when both sides are unambiguous.
        if not matches:
            matches = [
                g for g in games
                if g.league == league
                and abs((parse_time(g.start) - start).total_seconds()) <= 3600
                and team_matches(teams[0], g.away, g.away_abbr)
                and team_matches(teams[1], g.home, g.home_abbr)
            ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _line_from_outcome(outcome):
        match = re.search(r'\(([+-]?\d+(?:\.\d+)?)\)\s*$', str(outcome.get('name', '')))
        return float(match.group(1)) if match else None

    def _match_seed_fixture_from_outcomes(self,fixture,payload,games,league):
        # A playoff calendar can retain seed placeholders while the same
        # fixture's active Winner contract already names the actual teams.
        # Never infer seeds from time alone or override a conflicting real title.
        teams=self._fixture_teams(fixture.get('name'))
        from .stake_api import seed_title, _seed_team, market_records
        if league!='MLB' or len(teams)!=2 or not seed_title(fixture.get('name')):return None
        named=[name for name in teams if not _seed_team(name)]
        pairs=set()
        for _,market in market_records(payload):
            if market.get('name')!='Winner (Incl. Extra Innings)' or market.get('status') not in (None,'active'):continue
            outcomes=market.get('outcomes') or []
            if len(outcomes)!=2 or any(o.get('active') is not True or o.get('status') not in (None,'active') for o in outcomes):continue
            pair=tuple(sorted(str(o.get('name') or '').strip() for o in outcomes))
            if pair[0] and pair[0].casefold()!=pair[1].casefold():pairs.add(pair)
        if len(pairs)!=1:return None
        pair=next(iter(pairs))
        if any(not any(team_matches(name,actual,'') for actual in pair) for name in named):return None
        return self._match_fixture({**fixture,'name':' - '.join(pair)},games,league)

    @staticmethod
    def _total_from_market(market):
        spec = str(market.get('specifiers') or '')
        match = re.search(r'(?:^|\|)total=([+-]?\d+(?:\.\d+)?)', spec)
        if match:
            return float(match.group(1))
        for outcome in market.get('outcomes') or []:
            match = re.search(r'(?:Over|Under)\s+([+-]?\d+(?:\.\d+)?)', str(outcome.get('name', '')), re.I)
            if match:
                return float(match.group(1))
        return None

    @staticmethod
    def _handicap_from_market(market, scope):
        # Stake scopes precede hcp (e.g. periodnr=1|hcp=-0.5). Parse exact
        # keys, never a prefix/sub-string; ambiguous or unsupported specifiers
        # cannot establish a current executable handicap contract.
        raw = market.get('specifiers')
        if not isinstance(raw, str) or not raw or len(raw) > 512:
            return None
        specifiers = {}
        for token in raw.split('|'):
            parts = token.split('=')
            if len(parts) != 2 or not all(parts) or parts[0] in specifiers:
                return None
            specifiers[parts[0]] = parts[1]
        if set(specifiers) - {'hcp', 'periodnr', 'quarternr'}:
            return None
        for key, prefix in (('periodnr', 'period_'), ('quarternr', 'quarter_')):
            if key in specifiers and scope.removesuffix('_ot') != prefix + specifiers[key]:
                return None
        value = specifiers.get('hcp', '')
        if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', value):
            return None
        line = float(value)
        if not math.isfinite(line) or not math.isfinite(line * 2) or line * 2 != round(line * 2):
            # Asian quarter lines need split-contract pricing, not this path.
            return None
        return line

    def _handicap_outcomes_agree(self, outcomes, line, fixture, game):
        # The signed outcome value is authoritative for each selection. The
        # specifier must describe the same two opposite lines, not another
        # market hidden behind a correct-looking display name.
        sides = {}
        for outcome in outcomes:
            name = str(outcome.get('name') or '')
            team = re.sub(r'\s*\([+-]?\d+(?:\.\d+)?\)\s*$', '', name)
            matches = [side for side in ('home', 'away')
                       if team_matches(team, getattr(game, side), getattr(game, side + '_abbr'))]
            actual = self._line_from_outcome(outcome)
            if len(matches) != 1 or matches[0] in sides or actual is None or not math.isfinite(actual) or abs(actual) != abs(line):
                return False
            sides[matches[0]] = actual
        if set(sides) != {'home', 'away'} or sides['home'] != -sides['away']:
            return False
        # hcp is attached to the first listed fixture team; the schedule join
        # can legitimately reverse home/away presentation. Never flip the
        # selection sign to force it to match the independent schedule.
        teams = self._fixture_teams(fixture.get('name'))
        if len(teams) == 2:
            matches = [side for side in ('home', 'away')
                       if team_matches(teams[0], getattr(game, side), getattr(game, side + '_abbr'))]
            if len(matches) == 1 and sides[matches[0]] != line:
                return False
        return True

    def _markets_for_fixture(self, payload, fixture, game, league, fetched_at):
        expected_scope = self.LEAGUE_SCOPE[league]
        fixture_slug = fixture.get('slug') or fixture.get('name', '')
        league_path=fixture.get('league_path') or self.LEAGUE_PATH[league]
        url = f'https://stake.com/sports/{league_path}/{fixture_slug}'
        result = []
        seen = set()
        groups_by_market={}
        for group in payload.get('groups',[]):
            name=str(group.get('name') or '').strip()
            if not name:continue
            entry={'id':group.get('id'),'name':name}
            for bucket in group.get('markets',[]):
                for raw in bucket or []:
                    if raw.get('status') not in (None,'active'):continue
                    key=_stake_contract_key(raw)
                    groups=groups_by_market.setdefault(key,[])
                    if entry not in groups:groups.append(entry)
        for group in payload.get('groups', []):
            for bucket in group.get('markets', []):
                for market in bucket or []:
                    if _stake_contract_rejection(market):
                        continue
                    name = str(market.get('name') or '')
                    if market.get('status') not in (None, 'active'):
                        continue
                    from .overtime_contracts import TITLES, parse_overtime
                    if name in TITLES:
                        aliases = parse_overtime(market, game, fixture, url, fetched_at,
                            self.segment_markets.get((league, 'regulation'), ()),
                            groups_by_market.get(_stake_contract_key(market), []), _stake_contract_key(market))
                        for alias in aliases:
                            if alias.id not in seen:
                                result.append(alias)
                                seen.add(alias.id)
                        continue
                    total_sides=[side for side in ('home','away') if any(name==f'{team} Total (Incl. {expected_scope})' for team in team_name_variants(getattr(game,side)))]
                    total_kind=total_sides[0]+'_total' if len(total_sides)==1 else None
                    full_derived={
                        f'Odd/Even (Incl. {expected_scope})':'odd_even',
                        f'Both Teams to Score (Incl. {expected_scope})':'both_score',
                        f'Double Chance (Incl. {expected_scope})':'double_chance',
                        '1x2 & Total':'winner_total',
                        f'1x2 & Total (Incl. {expected_scope})':'winner_total',
                        f'Winner & Total (Incl. {expected_scope})':'winner_total',
                        f'Total (Over-Exact-Under) (Incl. {expected_scope})':'exact_total',
                        'Correct Score':'exact_score',
                    }
                    if league == 'MLS':
                        full_derived.update({
                            '1x2':'three_way',
                            'Draw No Bet':'moneyline',
                            'Both Teams to Score':'both_score',
                            'Double Chance':'double_chance',
                            'Asian Handicap':'spreads',
                            'Asian Total':'totals',
                        })
                    scope='full_game'
                    if total_kind or name in {f'{base} (Incl. {expected_scope})' for base in ('Winner','Handicap','Total')} or name in full_derived:
                        base = total_kind or full_derived.get(name) or ('moneyline' if name.startswith('Winner (') else 'spreads' if name.startswith('Handicap (') else 'totals')
                    else:
                        from .scope_markets import named_scopes
                        mapping=named_scopes(league,game.home,game.away)
                        if name not in mapping:continue
                        scope,base=mapping[name]
                        if base not in self.segment_markets.get((league,scope),()):continue
                    kind=base if scope=='full_game' else f'{scope}:{base}'
                    if league.startswith('NCAA') and base not in self.full_markets.get(league,()):continue
                    line = self._total_from_market(market) if base in ('totals','home_total','away_total','winner_total','exact_total') else None
                    handicap = self._handicap_from_market(market, scope) if base == 'spreads' else None
                    if base == 'spreads' and handicap is None:
                        continue
                    if base == 'spreads':
                        # The API encodes the handicap on each outcome, so use
                        # the actual signed value for that specific selection.
                        line = None
                    outcomes = market.get('outcomes') or []
                    # Observed WNBA Winner & Total includes overtime and has
                    # two winners crossed with Over/Under. Regulation 1x2
                    # remains a distinct six-outcome contract. This recognition
                    # does not waive composite-family validation in the guard.
                    binary_winner_total = (league == 'WNBA' and scope == 'full_game'
                        and name == 'Winner & Total (Incl. Overtime)')
                    if binary_winner_total:
                        if any(o.get('active') is not True or o.get('status') not in (None, 'active')
                               for o in outcomes):
                            continue
                        from .period_contracts import scoped_total_line
                        try:
                            line = scoped_total_line(market.get('specifiers'), scope)
                        except ValueError:
                            continue
                    expected_count = (3 if base in ('three_way','double_chance','exact_total') else
                                      (4 if binary_winner_total else 6) if base == 'winner_total' else None)
                    if ((expected_count is not None and len(outcomes) != expected_count) or
                            (base == 'exact_score' and len(outcomes) < 2) or
                            any(not isinstance(o.get('odds'), (int, float)) or not math.isfinite(o['odds']) or float(o['odds']) <= 1 for o in outcomes)):
                        continue
                    if base == 'spreads' and not self._handicap_outcomes_agree(outcomes, handicap, fixture, game):
                        continue
                    if scope != 'full_game' and base != 'spreads':
                        from .period_contracts import scoped_total_line, validate_scoped_specifiers
                        try:
                            if base in ('totals', 'home_total', 'away_total', 'winner_total', 'exact_total'):
                                line = scoped_total_line(market.get('specifiers'), scope)
                            else:
                                validate_scoped_specifiers(market.get('specifiers'), scope)
                        except ValueError:
                            continue
                    if base in ('totals', 'home_total', 'away_total'):
                        labels = [re.fullmatch(r'(Over|Under)\s+([+-]?\d+(?:\.\d+)?)',
                                              str(o.get('name') or ''), re.I) for o in outcomes]
                        if (any(label is None for label in labels)
                                or {label[1].lower() for label in labels} != {'over', 'under'}
                                or any(float(label[2]) != line for label in labels)):
                            continue
                    elif base in ('winner_total','exact_total','exact_score'):
                        parsed_sides=[_stake_joint_side(base,str(o.get('name') or ''),game) for o in outcomes]
                        expected = ({'over','under','exact'} if base=='exact_total' else
                            {winner+'_'+total for winner in (('home','away') if binary_winner_total else ('home','away','draw'))
                             for total in ('over','under')} if base=='winner_total' else None)
                        if binary_winner_total and (line is None or not math.isfinite(line)
                                or line < 0 or line % 1 != .5):
                            # Integer/quarter composite totals need an explicit
                            # push/split payout rule before this path can price them.
                            continue
                        if (any(s is None for s in parsed_sides)
                                or (expected is not None and set(parsed_sides)!=expected)
                                or len(set(parsed_sides)) != len(parsed_sides)):
                            continue
                        if base in ('winner_total','exact_total'):
                            described=[]
                            for o in outcomes:
                                match=re.search(r'(?:Over|Under|Exact)\s+([+-]?\d+(?:\.\d+)?)$',str(o.get('name') or ''),re.I)
                                described.append(float(match[1]) if match else None)
                            if line is None or any(value is None or value != line for value in described):
                                continue
                    elif base != 'spreads':
                        expected = {'odd', 'even'} if base == 'odd_even' else {'yes', 'no'} if base == 'both_score' else (
                            {'home_draw', 'away_draw', 'home_away'} if base == 'double_chance' else
                            {'home', 'away', 'draw'} if base == 'three_way' else {'home', 'away'})
                        if {_stake_categorical_side(base, str(o.get('name') or ''), game) for o in outcomes} != expected:
                            continue
                    for outcome in outcomes:
                        if outcome.get('active') is not True or outcome.get('status') not in (None, 'active'):
                            continue
                        outcome_name = str(outcome.get('name') or '')
                        if base in ('totals','home_total','away_total'):
                            side = 'over' if outcome_name.lower().startswith('over ') else 'under' if outcome_name.lower().startswith('under ') else None
                            actual_line = line
                            explicit = re.fullmatch(r'(?:Over|Under)\s+([+-]?\d+(?:\.\d+)?)', outcome_name, re.I)
                            if not explicit or float(explicit[1]) != line:
                                continue
                        elif base in ('winner_total','exact_total','exact_score'):
                            side = _stake_joint_side(base,outcome_name,game)
                            actual_line = line
                        else:
                            side = _stake_categorical_side(base, outcome_name, game)
                            actual_line = self._line_from_outcome(outcome) if base == 'spreads' else None
                        if side is None or (base in ('totals','home_total','away_total','spreads','winner_total','exact_total') and actual_line is None):
                            continue
                        if scope!='full_game' and actual_line is not None and (not math.isfinite(actual_line) or actual_line*2!=round(actual_line*2)):
                            # Quarter Asian lines split the stake across two
                            # contracts; this count-pricing path does not do so.
                            continue
                        key = (game.id, kind, side, actual_line)
                        if key in seen:
                            continue
                        seen.add(key)
                        if base in ('moneyline','three_way'): selection = 'Draw' if side=='draw' else game.home if side == 'home' else game.away
                        elif base == 'spreads': selection = f'{game.home if side == "home" else game.away} {actual_line:+g}'
                        elif base in ('totals','home_total','away_total'): selection = f'{"Over" if side == "over" else "Under"} {actual_line:g}'
                        else:selection=outcome_name
                        composite_raw = {}
                        from .composite_contracts import TITLES, identity as composite_identity
                        if (league,kind) in TITLES and name==TITLES[(league,kind)]:
                            try:composite_identity(league,kind,game.home,game.away,market)
                            except (ValueError,TypeError,KeyError):continue
                            from copy import deepcopy
                            composite_raw={'composite_raw_contract':deepcopy(market)}
                        result.append(Market(
                            id=f'stake:{fixture.get("id", fixture_slug)}:{market.get("id", name)}:{outcome.get("id", outcome_name)}',
                            game_id=game.id, league=league, kind=kind, side=side, line=actual_line,
                            selection=selection, platform='Stake.com', url=url,
                            token=str(outcome.get('id', outcome_name)), condition_id=str(market.get('id', name)),
                            description=name, fee_rate=0., fee_exponent=1., minimum_notional=0.,
                            market_start=game.start, fetched_at=fetched_at,
                            metadata={**composite_raw,'provider': 'stake-sports-data', 'fixture_slug': fixture_slug,
                                      'league_path':league_path,
                                      'stake_market_name': name, 'stake_outcome_name': outcome_name,
                                      'stake_market_groups':groups_by_market.get(_stake_contract_key(market),[]),
                                      'stake_specifiers':market.get('specifiers'),
                                      'stake_contract_identity':_stake_contract_key(market),
                                      'stake_odds': float(outcome['odds']), 'stake_scope': expected_scope if scope=='full_game' else scope,
                                      'model_scope':scope,
                                      'availability_verified': True},
                        ))
        return result

    def discover(self, games, horizon=48):
        from .stake_api import tournaments,league_fixtures,fixture_bundle,market_records,ALL_LEAGUES
        from .opportunities import active_standard_outcomes
        now=utcnow();end=now+timedelta(hours=horizon)
        self.inventory=[];self.fixture_audit=[];self.schedule_counts={};self.endpoint_audit=[]
        registry,available,registry_sources,issues=tournaments(self.fetcher)
        self.registry_sources=registry_sources
        markets=[];events=[];targets=[]
        def load(league):
            if league not in available:
                # Known pro endpoints still provide coverage during registry failure;
                # college absence is never inferred from that failure.
                if league.startswith('NCAA'):return league,([],[],[],False)
                return league,league_fixtures(self.fetcher,league)
            return league,league_fixtures(self.fetcher,league,registry[league])
        for league,(fixtures,sources,errors,ok) in ThreadPoolExecutor(5).map(load,registry):
            issues.extend(errors)
            if ok:self.schedule_counts[league]=len(fixtures)
            for fixture in fixtures:
                start=datetime.fromtimestamp(float(fixture['date'])/1000,tz=timezone.utc)
                if not now<start<=end:continue
                enabled=bool(fixture.get('enabled') and fixture.get('preMatchEnabled') and not fixture.get('blacklisted')
                             and fixture.get('status') in (None,'active') and not fixture.get('identity_conflict'))
                game=self._match_fixture(fixture,games,league)
                audit={'league':league,'fixture_id':fixture['id'],'name':fixture.get('name'),'start':start.isoformat(),
                       'matched':game is not None,'active':enabled,'fetched_at':max((r.get('fetched_at','') for r in sources),default=now.isoformat()),
                       'schedule_sources':fixture['schedule_sources'],'schedule_time_comparison':fixture.get('schedule_time_comparison'),
                       'game_id':game.id if game else None}
                self.fixture_audit.append(audit)
                # Fetch disabled fixtures too: inventory and active eligibility are distinct.
                targets.append((league,fixture,game,audit))
        def fetch(target):
            league,fixture,game,audit=target
            try:return target,fixture_bundle(self.fetcher,fixture),None
            except Exception as exc:return target,None,str(exc)[:500]
        for (league,fixture,game,audit),bundle,error in ThreadPoolExecutor(6).map(fetch,targets):
            if error:
                issues.append({'league':league,'event':fixture.get('name'),'reason':'stake_fixture_error','detail':error});continue
            payload,meta,endpoint_audit,responses=bundle
            endpoint_audit.update(league=league,fixture_id=fixture['id']);self.endpoint_audit.append(endpoint_audit)
            issues.extend({**e,'league':league,'event':fixture.get('name')} for e in endpoint_audit['errors'])
            actual=payload['fixture'];active=audit['active'] and endpoint_audit['identity_verified'] and bool(actual.get('enabled') and actual.get('preMatchEnabled') and not actual.get('blacklisted') and actual.get('status') in (None,'active'))
            basis='active_winner_outcome_team_names' if endpoint_audit.get('seed_name_resolution') else 'schedule_team_names'
            if game is None:
                game=self._match_seed_fixture_from_outcomes(fixture,payload,games,league)
                basis='active_winner_outcome_team_names' if game else 'unmatched'
            if game is not None and active:
                try:
                    stake_start=datetime.fromtimestamp(float(actual['startTime'])/1000,tz=timezone.utc)
                    # Provider timestamps may round subsecond precision. A real
                    # schedule conflict needs source reconciliation, not guessed
                    # closing windows or silently relabelled original tickets.
                    mismatch=abs((stake_start-parse_time(game.start)).total_seconds())>1
                except (KeyError,TypeError,ValueError,OverflowError):
                    mismatch=True;stake_start=None
                if mismatch:
                    active=False
                    audit['start_conflict']={'sports_start':game.start,'stake_start':stake_start.isoformat() if stake_start else None}
                    issues.append({'league':league,'event':fixture.get('name'),'reason':'stake_start_mismatch',**audit['start_conflict']})
            audit.update(matched=game is not None,game_id=game.id if game else None,matching_basis=basis,odds_loaded=True,active=active,endpoint_audit=endpoint_audit)
            if game is not None:events.append({'league':league,'id':fixture['id'],'game_id':game.id,'title':fixture.get('name'),'start':game.start,'provider':'Stake.com','matching_basis':basis,'matched_teams':[game.home,game.away]})
            parsed=self._markets_for_fixture(payload,fixture,game,league,meta['fetched_at']) if game and active else []
            for m in parsed:m.metadata.update(matching_basis=basis,fixture_name=fixture.get('name'),matched_teams=[game.home,game.away],api_source=endpoint_audit['responses'][endpoint_audit['selected_endpoint']])
            markets.extend(parsed);supported=Counter(m.metadata['stake_contract_identity'] for m in parsed);seen=set()
            for group,raw in market_records(payload):
                key=_stake_contract_key(raw)
                if key in seen:continue
                seen.add(key);count=len(raw['outcomes']) if isinstance(raw.get('outcomes'),list) else 0;n=supported.get(key,0)
                reason='missing_league_model' if league not in LEAGUES else 'fixture_inactive' if not active else 'unmatched_fixture' if game is None else 'supported' if n==count and count else 'partially_supported' if n else 'unsupported_or_inactive_contract'
                if rejection := _stake_contract_rejection(raw):
                    n=0;reason=rejection
                self.inventory.append({'league':league,'fixture_id':fixture['id'],'fixture':fixture.get('name'),'market_id':raw.get('id'),'market':raw.get('name'),
                    'specifiers':raw.get('specifiers'),'outcome_count':count,'active_outcomes':active_standard_outcomes(raw) if active and reason != 'invalid_outcomes_type' else 0,'supported_outcomes':n,
                    'reason':reason,'status':raw.get('status'),'fetched_at':meta['fetched_at']})
            self._inventory_swish(payload,fixture,league,meta['fetched_at'])
        return list({m.id:m for m in markets}.values()),events,issues

    def _inventory_swish(self,payload,fixture,league,fetched_at):
        swish=payload.get('swishMarkets') or {}
        for category,rows in swish.items():
            if not isinstance(rows,list):continue
            for market in rows:
                if not isinstance(market,dict):continue
                offers=[o.get(side) for o in market.get('outcomes',[]) for side in ('over','under') if o.get(side) is not None]
                invalid=sum(not isinstance(o,(int,float)) or not math.isfinite(o) or o<=1 for o in offers)
                self.inventory.append({'league':league,'fixture_id':fixture.get('id'),'fixture':fixture.get('name'),
                    'market_id':market.get('id'),'market':market.get('marketName'),'competitor':market.get('competitorName'),
                    'provider_section':'swishMarkets.'+category,'outcome_count':len(offers),'invalid_prices':invalid,
                    'supported_outcomes':0,'reason':'swish_contract_scope_and_model_not_validated','fetched_at':fetched_at})

    def quotes(self, markets):
        result = {}; issues = []
        for market in markets:
            odds = market.metadata.get('stake_odds')
            if not isinstance(odds, (int, float)) or odds <= 1:
                issues.append({'market': market.id, 'reason': 'stake_invalid_odds'})
                continue
            result[market.id] = Quote(
                market_id=market.id, token=market.token, price=1 / float(odds),
                fee_per_share=0., effective_cost=1 / float(odds), available_shares=5 / (1 / float(odds)),
                minimum_notional=0., fetched_at=market.fetched_at, source_timestamp=market.fetched_at,
            )
        return result, issues
