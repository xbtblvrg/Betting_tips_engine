"""Public-source-only reference ingestion; actual archives never become forecasts."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import hashlib,json
from pathlib import Path
import httpx,pytest
from betting_engine import reference_polymarket as ref

F=Path(__file__).parent/'fixtures'
EVENT=json.loads((F/'polymarket_event_1097834.json').read_bytes())
BOOKS=[json.loads((F/('polymarket_book_'+str(i)+'.json')).read_bytes())for i in range(2)]
RECEIPTS=json.loads((F/'polymarket_reference_receipts.json').read_bytes())
AT=max(row['finished_at']for row in RECEIPTS)
GAME={'id':'MLB:849844','league':'MLB','home':'Atlanta Braves','away':'Philadelphia Phillies','start':'2026-10-02T00:00:00Z','state':'scheduled'}

def market(event=None):return next(m for m in (event or EVENT)['markets']if m.get('sportsMarketType')=='moneyline')

def doc(value,name):
 original=next(row for row in RECEIPTS if row['name']==name)
 return ref.PublicDocument(deepcopy(value),{**original,'fetched_at':original['finished_at']})

class CapturedClient:
 def __init__(self,event=None):self.data=deepcopy(event or EVENT);self.calls=[]
 def event(self,identity):self.calls.append(('event',identity));return doc(self.data,'gamma-event-1097834')
 def book(self,token):
  self.calls.append(('book',token));i=next(i for i,b in enumerate(BOOKS)if b['asset_id']==token)
  return doc(BOOKS[i],'book-'+str(i))

def quote(book=None,**kwargs):
 b=deepcopy(book or BOOKS[0])
 return ref.book_quote(b,token=BOOKS[0]['asset_id'],condition=BOOKS[0]['market'],rate=Decimal('.05'),observed_at=AT,**kwargs)


def test_actual_four_full_bodies_retain_original_hashes_and_schedule_conflict():
 names={'gamma-event-1097834':'polymarket_event_1097834.json','book-0':'polymarket_book_0.json','book-1':'polymarket_book_1.json','mlb-game-849844':'polymarket_mlb_game_849844.json'}
 for row in RECEIPTS:
  body=(F/names[row['name']]).read_bytes()
  assert len(body)==row['bytes_retained'] and hashlib.sha256(body).hexdigest()==row['sha256'] and row['body_complete'] and row['status']==200
 client=CapturedClient();game=deepcopy(GAME);result=ref.observe_event(client,'1097834',[game],observed_at=AT)
 assert game==GAME and result['sports_forecast_modified'] is False and result['orders_submitted'] is False
 assert len(result['observations'])==1 and len(client.calls)==3
 row=result['observations'][0];assert row['comparable_to_sportsbook'] is False and row['usable_for_automatic_blend'] is False
 assert 'description_schedule_conflict' in row['contract']['comparison_rejections']
 assert row['contract']['prediction_market_payout']['tie_or_cancellation']=='0.5'
 assert row['contract']['fees']['rate']=='0.05'
 assert {q['side']:q['best_ask']for q in row['quotes']}=={'home':'0.51','away':'0.5'}
 assert {q['side']:q['best_bid']for q in row['quotes']}=={'home':'0.5','away':'0.49'}
 assert sum(Decimal(x)for x in row['paired_midpoint_index'].values())==1
 assert all(q['liquidity_state']=='sufficient'and q['sell_rejections']==[] for q in row['quotes'])


def test_official_actual_starters_still_missing_is_not_fixed_by_market_reference():
 body=json.loads((F/'polymarket_mlb_game_849844.json').read_bytes());game=next(g for day in body['dates']for g in day['games']if g['gamePk']==849844)
 assert game['gameDate']==GAME['start']
 assert all('probablePitcher' not in game['teams'][side]for side in ('home','away'))


@pytest.mark.parametrize('fault',['home','away','time','duplicate','league','live','missing_teams','same_order'])
def test_exact_game_matching_rejects_identity_or_timing_ambiguity(fault):
 event=deepcopy(EVENT);games=[deepcopy(GAME)]
 if fault in ('home','away'):games[0][fault]='Another Team'
 elif fault=='time':games[0]['start']='2026-10-01T18:00:00Z'
 elif fault=='duplicate':games.append(deepcopy(GAME))
 elif fault=='league':games[0]['league']='NFL'
 elif fault=='live':games[0]['state']='live'
 elif fault=='missing_teams':event['teams']=[]
 else:event['teams'][0]['ordering']='home'
 with pytest.raises(ref.ReferenceRejected):ref.match_game(event,games)


@pytest.mark.parametrize('fault',['fees_missing','fees_null','exponent','taker_only','rate_nan','inactive','accepting_missing','period_title','regulation_description','integer_total','outcome_conflict','duplicate_outcome','duplicate_token','token_path','token_object','start_conflict','condition','rules_missing'])
def test_contract_unknowns_fail_closed_without_fabricated_scope_or_zero_fees(fault):
 event=deepcopy(EVENT);m=market(event)
 if fault=='fees_missing':m.pop('feesEnabled',None)
 elif fault=='fees_null':m['feesEnabled']=None
 elif fault=='exponent':m['feeSchedule']['exponent']=2
 elif fault=='taker_only':m['feeSchedule']['takerOnly']=False
 elif fault=='rate_nan':m['feeSchedule']['rate']='NaN'
 elif fault=='inactive':m['closed']=True
 elif fault=='accepting_missing':m.pop('acceptingOrders')
 elif fault=='period_title':m['question']='First 5 innings winner'
 elif fault=='regulation_description':m['description']+=' Regulation only.'
 elif fault=='integer_total':m['sportsMarketType']='totals';m['line']=7
 elif fault=='outcome_conflict':m['outcomes']=['Atlanta Braves','Another Team']
 elif fault=='duplicate_outcome':m['outcomes']=['Atlanta Braves','Atlanta Braves']
 elif fault=='duplicate_token':m['clobTokenIds']=['1','1']
 elif fault=='token_path':m['clobTokenIds']=['../../orders','2']
 elif fault=='token_object':m['clobTokenIds']=[{},'2']
 elif fault=='start_conflict':m['gameStartTime']='2026-10-01T18:00:00Z'
 elif fault=='condition':m['conditionId']='not-a-condition'
 else:m['description']=''
 with pytest.raises(ref.ReferenceRejected):ref.winner_contract(event,m,GAME)


def test_unknown_settlement_and_unknown_description_date_remain_explicit():
 event=deepcopy(EVENT);m=market(event);m['description']='The named winner receives one dollar. Rules otherwise unavailable.'
 contract,rate=ref.winner_contract(event,m,GAME)
 assert contract['prediction_market_payout']['tie_or_cancellation'] is None
 assert set(contract['comparison_rejections'])=={'description_schedule_not_verified','tie_cancellation_terms_not_verified','sportsbook_settlement_equivalence_not_established'}


@pytest.mark.parametrize('fault',['token','condition','stale','future','timestamp_missing','timestamp_naive','empty_bid','empty_ask','price_nan','price_infinity','zero_price','negative_size','duplicate_price','crossed','tick','minimum','large_numeric','tiny_numeric','excess_levels'])
def test_book_identity_freshness_numeric_and_liquidity_constraints(fault):
 b=deepcopy(BOOKS[0])
 if fault=='token':b['asset_id']='1'
 elif fault=='condition':b['market']='0x'+'0'*64
 elif fault=='stale':b['timestamp']=str(int(_epoch(AT)*1000)-301000)
 elif fault=='future':b['timestamp']=str(int(_epoch(AT)*1000)+3000)
 elif fault=='timestamp_missing':b.pop('timestamp')
 elif fault=='timestamp_naive':b['timestamp']='2026-10-01'
 elif fault=='empty_bid':b['bids']=[]
 elif fault=='empty_ask':b['asks']=[]
 elif fault=='price_nan':b['asks'][0]['price']='NaN'
 elif fault=='price_infinity':b['asks'][0]['price']='1e999'
 elif fault=='zero_price':b['asks'][0]['price']='0'
 elif fault=='negative_size':b['bids'][0]['size']='-1'
 elif fault=='duplicate_price':b['asks'].append(deepcopy(b['asks'][0]))
 elif fault=='crossed':b['bids']=[{'price':'.99','size':'100'}]
 elif fault=='tick':b['tick_size']='.03'
 elif fault=='minimum':b['min_order_size']='0'
 elif fault=='large_numeric':b['asks'][0]['size']='1e999999'
 elif fault=='tiny_numeric':b['tick_size']='1e-999999'
 else:b['bids']=[{'price':'.1','size':'1'}]*(ref.MAX_LEVELS+1)
 with pytest.raises(ref.ReferenceRejected):quote(b)

def _epoch(value):return datetime.fromisoformat(value).timestamp()


def test_buy_depth_uses_all_levels_and_minimum_is_shares_not_dollars():
 b=deepcopy(BOOKS[0]);b['bids']=[{'price':'.4','size':'100'}];b['asks']=[{'price':'.5','size':'2'},{'price':'.6','size':'20'}]
 q=quote(b);assert len(q['buy_levels'])==2 and Decimal(q['buy_fee_estimate'])>0 and q['liquidity_state']=='sufficient'
 assert Decimal(q['buy_effective_cost_per_share'])>Decimal('.5')
 small=quote(b,notional='1');assert 'below_minimum_shares' in small['rejections']
 b['asks']=[{'price':'.5','size':'1'}];q=quote(b);assert 'insufficient_buy_depth' in q['rejections']
 b['bids']=[{'price':'.4','size':'.1'}];assert 'insufficient_sell_depth' in quote(b)['sell_rejections']


def test_fresh_metadata_and_duplicate_market_bound_prevent_unbounded_book_requests():
 client=CapturedClient();client.data['markets']=[deepcopy(market()),deepcopy(market())]
 result=ref.observe_event(client,'1097834',[GAME],observed_at=AT)
 assert result['rejections']==[{'event_id':'1097834','reason':'duplicate_full_game_winner_market'}] and len(client.calls)==1
 with pytest.raises(ref.ReferenceRejected,match='event_metadata_stale_or_future'):
  ref.observe_event(CapturedClient(),'1097834',[GAME],observed_at=(datetime.fromisoformat(AT)+timedelta(seconds=301)).isoformat())


def test_original_probability_fields_are_untouched_and_outside_window_no_books():
 game={**GAME,'start':(datetime.fromisoformat(AT)+timedelta(days=4)).isoformat(),'sports_probability':'.61'}
 event=deepcopy(EVENT);event['startTime']=game['start'];client=CapturedClient(event)
 result=ref.observe_event(client,'1097834',[game],observed_at=AT)
 assert result['rejections'][0]['reason']=='outside_pregame_48h' and len(client.calls)==1 and game['sports_probability']=='.61'


def transport(body=b'{"id":"1"}',status=200):
 calls=[]
 def handle(request):calls.append(request);return httpx.Response(status,content=body)
 return httpx.Client(transport=httpx.MockTransport(handle)),calls


def test_client_archives_complete_exact_body_and_only_anonymous_get(tmp_path):
 raw=b'{ "id": "1", "description": "original bytes" }\n';client,calls=transport(raw)
 collector=ref.PublicReferenceClient(tmp_path/'archive',client=client)
 result=collector.event('1');assert result.value['id']=='1'
 assert result.receipt['sha256']==hashlib.sha256(raw).hexdigest() and result.receipt['body_complete'] is True
 assert (collector.archive/result.receipt['archive']).read_bytes()==raw
 assert len(calls)==1 and calls[0].method=='GET' and 'authorization' not in calls[0].headers and 'cookie' not in calls[0].headers
 assert calls[0].url==httpx.URL('https://gamma-api.polymarket.com/events/1')
 assert len(list(collector.archive.glob('receipt-*.json')))==1


@pytest.mark.parametrize('fault',['http','oversize','invalid_json','duplicate_json','nonfinite_json','deep_json','timeout','redirect'])
def test_client_failures_preserve_status_body_and_bounds(tmp_path,fault):
 body={'invalid_json':b'{','duplicate_json':b'{"id":1,"id":2}','nonfinite_json':b'{"x":1e999}','deep_json':b'['*40+b'0'+b']'*40,'oversize':b'x'*40}.get(fault,b'error')
 client,calls=transport(body,503 if fault=='http' else 302 if fault=='redirect' else 200)
 if fault=='timeout':
  def handle(request):raise httpx.ReadTimeout('bounded fixture timeout')
  client=httpx.Client(transport=httpx.MockTransport(handle))
 collector=ref.PublicReferenceClient(tmp_path/'archive',client=client,max_body=32 if fault=='oversize' else ref.MAX_BODY)
 with pytest.raises(ref.ReferenceRejected):collector.event('1')
 receipts=list(collector.archive.glob('receipt-*.json'));assert len(receipts)==1
 row=json.loads(receipts[0].read_bytes());assert row['error'] and row['bytes']<=collector.max_body
 if fault=='oversize':assert row['body_complete'] is False and row['bytes']==32
 if fault=='timeout':assert row['error']=='ReadTimeout'
 if fault=='redirect':assert len(calls)==1 and row['status']==302


@pytest.mark.parametrize('identity',['../orders','https://other.test/','',True,'1?key=secret','1/2','1'*81])
def test_identifier_rejection_precedes_any_request_or_write(tmp_path,identity):
 client,calls=transport();collector=ref.PublicReferenceClient(tmp_path/'archive',client=client)
 with pytest.raises(ref.ReferenceRejected):collector.book(identity)
 assert calls==[] and not collector.archive.exists()


@pytest.mark.parametrize('headers',[{'Authorization':'unprinted-secret'},{'Cookie':'unprinted-secret'},{'Proxy-Authorization':'unprinted-secret'},{'X-Api-Key':'unprinted-secret'}])
def test_credential_bearing_client_is_rejected_without_network(tmp_path,headers):
 client=httpx.Client(headers=headers,transport=httpx.MockTransport(lambda _:pytest.fail('no request permitted')))
 with pytest.raises(ValueError,match='Authenticated clients'):ref.PublicReferenceClient(tmp_path,client=client)


def test_archive_conflict_and_symlink_do_not_overwrite_original(tmp_path):
 client,calls=transport();body=b'{"id":"1"}';archive=tmp_path/'archive';archive.mkdir();target=archive/(hashlib.sha256(body).hexdigest()+'.body');target.write_bytes(b'foreign bytes')
 with pytest.raises(ref.ReferenceRejected,match='archive_collision'):ref.PublicReferenceClient(archive,client=client).event('1')
 assert target.read_bytes()==b'foreign bytes'
 link=tmp_path/'link';link.symlink_to(archive,target_is_directory=True)
 with pytest.raises(ValueError,match='symlink'):ref.PublicReferenceClient(link,client=client)


@pytest.mark.parametrize('kwargs',[{'cookies':{'session':'unprinted-secret'}},{'params':{'api_key':'unprinted-secret'}},{'auth':('user','unprinted-secret')}])
def test_implicit_client_credentials_cannot_be_sent(tmp_path,kwargs):
 client=httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('no request permitted')),**kwargs)
 with pytest.raises(ValueError,match='Authenticated clients'):ref.PublicReferenceClient(tmp_path,client=client)


def test_failed_second_book_keeps_first_and_failure_source_receipts_without_observation():
 class BrokenClient(CapturedClient):
  def book(self,token):
   if token==BOOKS[1]['asset_id']:
    error=ref.ReferenceRejected('ReadTimeout');error.receipt={'url':'https://clob.polymarket.com/book','body_complete':False,'error':'ReadTimeout','receipt_path':'retained-failure.json'};raise error
   return super().book(token)
 result=ref.observe_event(BrokenClient(),'1097834',[GAME],observed_at=AT)
 assert result['observations']==[] and any(row['reason']=='ReadTimeout' for row in result['rejections'])
 assert len(result['source_receipts'])==3 and result['source_receipts'][-1]['receipt_path']=='retained-failure.json'


def test_stream_deadline_is_durable_and_does_not_claim_complete_body(tmp_path,monkeypatch):
 ticks=iter([0,13,14]);monkeypatch.setattr(ref.time,'monotonic',lambda:next(ticks,14))
 client,calls=transport();collector=ref.PublicReferenceClient(tmp_path/'archive',client=client,deadline=12)
 with pytest.raises(ref.ReferenceRejected,match='request_deadline') as failure:collector.event('1')
 row=json.loads(next(collector.archive.glob('receipt-*.json')).read_bytes())
 assert row['error']=='request_deadline' and row['body_complete'] is False
 assert failure.value.receipt==row


def test_server_cookie_is_not_replayed_to_public_endpoint(tmp_path):
 requests=[]
 def handle(request):
  requests.append(request)
  return httpx.Response(200,content=b'{"id":"1"}',headers={'Set-Cookie':'session=unprinted-secret; Path=/'})
 client=httpx.Client(transport=httpx.MockTransport(handle))
 collector=ref.PublicReferenceClient(tmp_path/'archive',client=client)
 collector.event('1');collector.event('1')
 assert len(requests)==2 and all('cookie'not in r.headers for r in requests)


@pytest.mark.parametrize('change',['header','auth','params'])
def test_mutated_injected_client_context_rejected_before_request(tmp_path,change):
 client,calls=transport();collector=ref.PublicReferenceClient(tmp_path/'archive',client=client)
 if change=='header':client.headers['X-Api-Key']='unprinted-secret'
 elif change=='auth':client.auth=('user','unprinted-secret')
 else:client.params={'api_key':'unprinted-secret'}
 with pytest.raises(ref.ReferenceRejected,match='authentication_context_changed'):collector.event('1')
 assert not calls and not collector.archive.exists()


def test_archive_parent_replaced_with_symlink_after_construction_is_not_written(tmp_path):
 parent=tmp_path/'parent';parent.mkdir();outside=tmp_path/'outside';outside.mkdir()
 client,calls=transport();collector=ref.PublicReferenceClient(parent/'archive',client=client)
 parent.rmdir();parent.symlink_to(outside,target_is_directory=True)
 with pytest.raises(ref.ReferenceRejected,match='archive_destination_changed'):collector.event('1')
 assert list(outside.iterdir())==[]
