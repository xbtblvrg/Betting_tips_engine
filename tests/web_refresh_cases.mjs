import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
const html=readFileSync('src/betting_engine/web/index.html','utf8');
const script=readFileSync('src/betting_engine/web/app.js','utf8');
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve()};
function snapshot(){
 const now=Date.now(),start=new Date(now+3600000).toISOString(),quote_at=new Date(now).toISOString();
 const tip={id:'one',rank:1,league:'NBA',game_id:'g',home:'Home',away:'Away',start,valid_until:start,market:'moneyline',market_display:'Winner (Incl. Overtime)',selection:'Home',probability:.65,minimum_odds:1.8,reference_odds:2,reference_platform:'Stake.com',reference_url:'https://stake.com/sports/basketball/usa/nba/game',source_url:'https://example.com',quote_at,stake:2,stress_roi:.1,expected_roi:.2,reasons:[],warnings:[],game_context:{},model_version:'test',decision_guard:{passed:true},execution_review:{actionable:true,quote_at,valid_until:start},stake_market:{name:'Winner (Incl. Overtime)',availability_verified:true}};
 return {version:1,recommendations:[tip],placement_marks:{},settings:{bankroll:null,daily_risk_limit:null,daily_pick_target:15,max_bet:5,unit_stake:1,minimum_roi:.03,refresh_seconds:60,monthly_system_cost:null},engine:{running:false,stage:'Updated'},status:{},games:[],models:{},performance:{prospective:[]}};
}
async function boot(t){
 const w=new Window({url:'http://127.0.0.1:8765',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableIframePageLoading:true}});
 t.after(()=>w.happyDOM.close());w.document.write(html);
 const pending=[],intervals=[],timeouts=[],writes=[],marks={};let active=0,peak=0,fetchHook=null;
 w.setInterval=(callback,ms)=>{intervals.push({callback,ms});return intervals.length};
 w.setTimeout=(callback,ms)=>{timeouts.push({callback,ms});return timeouts.length};
 w.clearTimeout=id=>{if(timeouts[id-1])timeouts[id-1].cleared=true};
 w.EventSource=class{addEventListener(name,callback){if(name==='update')w.testUpdate=callback}};
 w.fetch=(path,options)=>{
  if(path==='/api/dashboard'){
   active++;peak=Math.max(peak,active);let resolve,reject;
   const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
   pending.push({respond:(body=snapshot())=>{active--;resolve({ok:true,json:async()=>structuredClone(body)})},fail:()=>{active--;reject(Error('offline'))},respondWithJSON:json=>{active--;resolve({ok:true,json})}});
   fetchHook?.();return promise;
  }
  if(path.startsWith('/api/autorun'))return Promise.resolve({ok:true,json:async()=>({})});
  writes.push({path,options});
  if(path.endsWith('/checklist')){
   const {placed}=JSON.parse(options.body);if(placed)marks.one=new Date().toISOString();else delete marks.one;
   return Promise.resolve({ok:true,json:async()=>({tip_id:'one',placed,placed_at:marks.one||null})});
  }
  return Promise.resolve({ok:true,json:async()=>({})});
 };
 w.eval(script);await flush();assert.equal(pending.length,1);pending[0].respond();await flush();
 return {w,d:w.document,pending,intervals,timeouts,writes,marks,peak:()=>peak,setHook:hook=>fetchHook=hook};
}

test('poll and SSE bursts coalesce to one active request and one awaited fresh response',async t=>{
 const h=await boot(t),{w,pending,intervals}=h;
 const first=w.testUpdate();await flush();assert.equal(pending.length,2);
 const queued=w.testUpdate();
 assert.equal(intervals.find(i=>i.ms===15000).callback(),queued);
 assert.equal(w.testUpdate(),queued);assert.equal(w.testUpdate(),queued);
 await flush();assert.equal(pending.length,2,'the held request cannot be multiplied by triggers');
 let queuedDone=false;queued.then(()=>queuedDone=true);
 pending[1].respond();await first;await flush();
 assert.equal(pending.length,3);assert.equal(queuedDone,false,'queued callers must wait for the new snapshot');
 const latest=snapshot();latest.engine.stage='New SSE publication';pending[2].respond(latest);await queued;
 assert.equal(h.d.querySelector('#run-stage').textContent,'New SSE publication');
 assert.equal(h.peak(),1);await flush();assert.equal(pending.length,3,'burst does not become an unbounded request queue');
});

test('a trigger during the trailing request schedules exactly one further fresh request',async t=>{
 const h=await boot(t),{w,pending}=h;
 const first=w.testUpdate(),second=w.testUpdate();await flush();pending[1].respond();await first;await flush();
 const third=w.testUpdate();assert.equal(w.testUpdate(),third);await flush();assert.equal(pending.length,3);
 pending[2].respond();await second;await flush();assert.equal(pending.length,4);
 pending[3].respond();await third;assert.equal(h.peak(),1);
});

test('synchronous fetch reentrancy cannot bypass the single-flight gate',async t=>{
 const h=await boot(t);let reentered;
 h.setHook(()=>{h.setHook(null);reentered=h.w.testUpdate()});
 const first=h.w.testUpdate();await flush();assert.equal(h.pending.length,2);
 h.pending[1].respond();await first;await flush();assert.equal(h.pending.length,3);
 h.pending[2].respond();await reentered;assert.equal(h.peak(),1);
});

test('explicit Refresh is retained while a dashboard request is pending',async t=>{
 const h=await boot(t),{w,d,pending,timeouts,writes}=h;
 const first=w.testUpdate();await flush();
 await d.querySelector('#refresh').onclick();
 assert.equal(writes.filter(r=>r.path==='/api/refresh'&&r.options.method==='POST').length,1);
 const manual=timeouts.find(r=>r.ms===800).callback();await flush();assert.equal(pending.length,2);
 pending[1].respond();await first;await flush();assert.equal(pending.length,3);
 const latest=snapshot();latest.engine.stage='Explicit refresh result';pending[2].respond(latest);await manual;
 assert.equal(d.querySelector('#run-stage').textContent,'Explicit refresh result');assert.equal(h.peak(),1);
});

test('transport failure fails closed and still executes the queued refresh, then releases the slot',async t=>{
 const h=await boot(t),{w,d,pending}=h;
 const first=w.testUpdate(),queued=w.testUpdate();await flush();pending[1].fail();await first;await flush();
 assert.equal(d.querySelector('#connection').textContent,'Connection Lost');
 assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);
 assert.equal(pending.length,3);pending[2].respond();await queued;
 assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,1);
 const failed=w.testUpdate();await flush();pending[3].fail();await failed;
 const retry=w.testUpdate();await flush();assert.equal(pending.length,5);pending[4].respond();await retry;
 assert.equal(h.peak(),1);
});

test('the flight includes JSON decoding, and malformed JSON permits a fresh retry',async t=>{
 const h=await boot(t),{w,pending}=h;let rejectJSON;
 const json=new Promise((_,reject)=>rejectJSON=reject);
 const first=w.testUpdate();await flush();pending[1].respondWithJSON(()=>json);await flush();
 const queued=w.testUpdate();await flush();assert.equal(pending.length,2,'reading the response body remains in flight');
 rejectJSON(SyntaxError('incomplete JSON'));await first;await flush();assert.equal(pending.length,3);
 pending[2].respond();await queued;assert.equal(h.d.querySelectorAll('#tips-grid .tip-card').length,1);
});

for(const phase of ['fetch','body'])test(`a stalled dashboard ${phase} is aborted on deadline and cannot block the queued update forever`,async t=>{
 const h=await boot(t),{w,d,pending,timeouts}=h,original=w.fetch;let signal;
 w.fetch=(_path,options)=>{
  signal=options.signal;
  const stalled=new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new w.DOMException('Aborted','AbortError')),{once:true}));
  return phase==='fetch'?stalled:Promise.resolve({ok:true,json:()=>stalled});
 };
 const first=w.testUpdate(),queued=w.testUpdate();await flush();assert.equal(signal.aborted,false);
 const deadline=timeouts.find(r=>r.ms===20000&&!r.cleared);assert.ok(deadline,'each dashboard read has a bounded deadline');
 w.fetch=original;deadline.callback();await first;await flush();
 assert.equal(signal.aborted,true);assert.equal(deadline.cleared,true);
 assert.equal(d.querySelector('#connection').textContent,'Connection Lost');
 assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);
 assert.equal(pending.length,2,'only the coalesced next request starts after abort');
 pending[1].respond();await queued;
 assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,1);
 assert.equal(timeouts.filter(r=>r.ms===20000&&!r.cleared).length,0,'success clears the remaining deadline');
});

test('coalesced refreshes preserve acknowledged placement and removal before applying fresh server marks',async t=>{
 const h=await boot(t),{w,d,pending,marks}=h;
 for(const placed of [true,false]){
  const stale=snapshot();stale.placement_marks=structuredClone(marks);
  const first=w.testUpdate();await flush();const index=pending.length-1;
  const input=d.querySelector('[data-placement="one"]');input.checked=placed;await input.onchange();
  const queued=w.testUpdate();pending[index].respond(stale);await first;await flush();
  assert.equal(d.querySelector('[data-placement="one"]').checked,placed,'pre-save response cannot resurrect or hide the wrong instruction');
  assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,placed?0:1);
  const fresh=snapshot();fresh.placement_marks=structuredClone(marks);pending[index+1].respond(fresh);await queued;
  assert.equal(d.querySelector('[data-placement="one"]').checked,placed);
 }
 assert.equal(h.writes.filter(r=>r.path.endsWith('/checklist')).length,2);
 assert.equal(h.writes.some(r=>r.path==='/api/bets'),false);assert.equal(h.peak(),1);
});

test('one-second quote expiry remains independent while active and queued refreshes are blocked',async t=>{
 const h=await boot(t),{w,d,pending,intervals}=h,old=snapshot();
 d.querySelector('#tips-grid [data-detail]').click();assert.equal(d.querySelector('#detail-dialog').open,true);
 const first=w.testUpdate(),queued=w.testUpdate();await flush();
 const OriginalDate=w.Date,later=OriginalDate.now()+301000;
 w.Date=class extends OriginalDate{constructor(...args){super(...(args.length?args:[later]))}static now(){return later}};
 const expiry=intervals.find(i=>i.ms===1000);assert.ok(expiry);expiry.callback();
 assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);assert.equal(d.querySelector('#detail-dialog').open,false);
 pending[1].respond(old);await first;await flush();pending[2].respond(old);await queued;
 assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0,'a late response cannot revive an expired quote');
 assert.equal(h.writes.length,0);assert.equal(h.peak(),1);
});
