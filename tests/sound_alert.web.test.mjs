import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';

const html=readFileSync('src/betting_engine/web/index.html','utf8');
const script=readFileSync('src/betting_engine/web/app.js','utf8');
const start=new Date(Date.now()+3600000).toISOString();

const tip={execution_review:{actionable:true,quote_at:new Date().toISOString(),valid_until:start},decision_guard:{passed:true},id:'tip-1',rank:1,league:'NBA',game_id:'g',home:'Home',away:'Away',start,valid_until:start,market:'moneyline',market_display:'Winner (Incl. Overtime)',stake_market:{name:'Winner (Incl. Overtime)',league_url:'https://stake.com/sports/basketball/usa/nba',availability_verified:true,availability_note:'Exact current Stake offer verified.'},selection:'Home',probability:.65,minimum_odds:1.80,reference_odds:2.0,reference_platform:'Stake.com',reference_url:'https://example.com',source_url:'https://example.com',quote_at:new Date().toISOString(),stake:2,stress_roi:.1,expected_roi:.2,label:'Estimated Value',reasons:['Reason'],warnings:[],model_version:'test'};

test('sound alert UI controls exist, show initial state and toggle correctly',async()=>{
 const w=new Window({url:'http://127.0.0.1:8765'});
 w.setInterval=()=>0;
 w.document.write(html);
 w.EventSource=class{addEventListener(){}};
 w.fetch=async()=>({ok:true,json:async()=>({version:1,recommendations:[tip],status:{daily_progress:{}},settings:{}})});
 w.eval(script);
 await new Promise(r=>setTimeout(r,20));

 const d=w.document;
 const topbarBtn=d.querySelector('#notify-topbar-btn');
 const toggleBtn=d.querySelector('#toggle-sound-btn');
 const testBtn=d.querySelector('#test-sound-btn');
 const label=d.querySelector('#sound-perm-label');

 assert.ok(topbarBtn);
 assert.ok(toggleBtn);
 assert.ok(testBtn);
 assert.match(topbarBtn.textContent,/Sound: ON/);
 assert.match(toggleBtn.textContent,/Sound: ON/);
 assert.match(label.textContent,/Audible chime active/);

 // Click toggle to mute
 toggleBtn.click();
 assert.match(toggleBtn.textContent,/Sound: Muted/);
 assert.match(topbarBtn.textContent,/Sound: Muted/);
 assert.equal(w.localStorage.getItem('quant_sound_enabled'),'false');

 // Click toggle again to re-enable
 toggleBtn.click();
 assert.match(toggleBtn.textContent,/Sound: ON/);
 assert.match(topbarBtn.textContent,/Sound: ON/);
 assert.equal(w.localStorage.getItem('quant_sound_enabled'),'true');

 await w.happyDOM.close();
});

test('check24hNewTickets chimes on new tickets in 24h window and stays silent on duplicates',async()=>{
 const w=new Window({url:'http://127.0.0.1:8765'});
 w.setInterval=()=>0;
 w.document.write(html);
 w.EventSource=class{addEventListener(name,callback){w.testUpdate=callback}};

 let dashData={version:1,recommendations:[tip],status:{daily_progress:{}},settings:{}};
 w.fetch=async()=>({ok:true,json:async()=>dashData});

 w.eval(script);
 await new Promise(r=>setTimeout(r,20));

 // Track chimes
 w.window._chimeCount = 0;
 w.window.playNotificationSound = function() { w.window._chimeCount++; };

 // 1. Initial boot: tip-1 was already loaded, so chime count must be 0
 assert.equal(w.window._chimeCount, 0);

 // 2. Add a new tip in the 24h window (tip-2 starting in 2 hours)
 const st2 = new Date(Date.now() + 7200000).toISOString();
 const tip2 = { ...tip, id: 'tip-2', start: st2, valid_until: st2, execution_review: { ...tip.execution_review, valid_until: st2 } };
 dashData = { version: 1, recommendations: [tip, tip2], status: { daily_progress: {} }, settings: {} };

 // Trigger update via EventSource / SSE
 await w.testUpdate();
 await new Promise(r => setTimeout(r, 20));

 // It must have chimed once for the new ticket!
 assert.equal(w.window._chimeCount, 1);
 const toastEl = w.document.querySelector('#toast');
 assert.match(toastEl.textContent, /1 new bet ticket in 24h window/);

 // 3. Refresh with same data: duplicate must NOT chime again
 await w.testUpdate();
 await new Promise(r => setTimeout(r, 20));
 assert.equal(w.window._chimeCount, 1);

 await w.happyDOM.close();
});

test('horizon badges display correct pick counts for 24h, 48h, 72h, today and tomorrow',async()=>{
 const w=new Window({url:'http://127.0.0.1:8765'});
 w.setInterval=()=>0;
 w.document.write(html);
 w.EventSource=class{addEventListener(name,callback){w.testUpdate=callback}};

 const makeTip = (id, hours, league='NBA') => {
  const st = new Date(Date.now() + hours * 3600000).toISOString();
  return {
   ...tip,
   id,
   league,
   start: st,
   valid_until: st,
   execution_review: { ...tip.execution_review, valid_until: st }
  };
 };

 // tip in 2 hours (24h, 48h, 72h)
 const tip1 = makeTip('t-2h', 2, 'NBA');
 // tip in 30 hours (48h, 72h)
 const tip2 = makeTip('t-30h', 30, 'NHL');
 // tip in 60 hours (72h only)
 const tip3 = makeTip('t-60h', 60, 'NFL');

 const dashData={version:1,recommendations:[tip1,tip2,tip3],status:{daily_progress:{}},settings:{}};
 w.fetch=async()=>({ok:true,json:async()=>dashData});

 w.eval(script);
 await new Promise(r=>setTimeout(r,20));

 const d=w.document;
 const b24=d.querySelector('#badge-horizon-24');
 const b48=d.querySelector('#badge-horizon-48');
 const b72=d.querySelector('#badge-horizon-72');

 assert.ok(b24);
 assert.ok(b48);
 assert.ok(b72);

 assert.equal(b24.textContent,'1');
 assert.equal(b48.textContent,'2');
 assert.equal(b72.textContent,'3');

 // Filter by NHL
 const leagueFilter=d.querySelector('#league-filter');
 leagueFilter.value='NHL';
 leagueFilter.dispatchEvent(new w.Event('change'));
 await new Promise(r=>setTimeout(r,20));

 assert.equal(b24.textContent,'0');
 assert.equal(b48.textContent,'1');
 assert.equal(b72.textContent,'1');

 await w.happyDOM.close();
});

test('marking a bet as placed displays an Unmark action button in the toast which reverts the placement',async()=>{
 const w=new Window({url:'http://127.0.0.1:8765'});
 w.setInterval=()=>0;
 w.document.write(html);
 w.EventSource=class{addEventListener(){}};

 const apiCalls=[];
 const marks={};
 const dashData={version:1,recommendations:[tip],status:{daily_progress:{}},settings:{}};

 w.fetch=async(path,options)=>{
  if(path.endsWith('/checklist')&&options?.method==='PUT'){
   const body=JSON.parse(options.body);
   apiCalls.push({path,body});
   if(body.placed)marks['tip-1']=new Date().toISOString();else delete marks['tip-1'];
   return {ok:true,json:async()=>({tip_id:'tip-1',placed:body.placed,placed_at:marks['tip-1']||null})};
  }
  return {ok:true,json:async()=>({...dashData,placement_marks:marks})};
 };

 w.eval(script);
 await new Promise(r=>setTimeout(r,20));

 const d=w.document;
 const input=d.querySelector('[data-placement="tip-1"]');
 assert.ok(input);
 assert.equal(input.checked,false);

 // Mark bet as placed
 input.checked=true;
 await input.onchange();
 await new Promise(r=>setTimeout(r,20));

 // Toast appears with message and Unmark button
 const toastEl=d.querySelector('#toast');
 assert.equal(toastEl.hidden,false);
 assert.match(toastEl.textContent,/Marked as placed. Saved in Bet Tracker./);
 const unmarkBtn=toastEl.querySelector('.toast-action-btn');
 assert.ok(unmarkBtn);
 assert.equal(unmarkBtn.textContent,'Unmark');

 // Main grid no longer displays the placed tip
 assert.equal(d.querySelector('#tips-grid [data-tip-id="tip-1"]'),null);
 assert.deepEqual(apiCalls[0],{path:'/api/tips/tip-1/checklist',body:{placed:true}});

 // Click Unmark button in the toast!
 unmarkBtn.click();
 await new Promise(r=>setTimeout(r,20));

 // Tip returns to main grid and toast updates
 assert.ok(d.querySelector('#tips-grid [data-tip-id="tip-1"]'));
 assert.match(toastEl.textContent,/Mark removed. Current qualifying picks return to Betting Picks./);
 assert.deepEqual(apiCalls[1],{path:'/api/tips/tip-1/checklist',body:{placed:false}});

 await w.happyDOM.close();
});

