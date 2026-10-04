import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
const html=readFileSync('src/betting_engine/web/index.html','utf8');
const script=readFileSync('src/betting_engine/web/app.js','utf8');
const oldURL='https://stake.com/sports/ice-hockey/usa/nhl/original-event';
const newURL='https://stake.com/sports/ice-hockey/usa/nhl/46728057-colorado-avalanche-los-angeles-kings';
function selection(){
 const quote_at=new Date().toISOString(),start=new Date(Date.now()+3600000).toISOString();
 return {id:'double-chance',rank:1,league:'NHL',game_id:'nhl-game',home:'Colorado Avalanche',away:'Los Angeles Kings',start,valid_until:start,quote_at,
  market:'regulation:double_chance',model_scope:'regulation',side:'draw_away',line:null,market_display:'Double Chance',selection:'Draw or Los Angeles Kings',probability:.65,minimum_odds:1.8,reference_odds:2,reference_platform:'Stake.com',stake:2.34,stress_roi:.1,expected_roi:.3,reasons:[],warnings:[],game_context:{},model_version:'fixed',decision_guard:{passed:true},execution_review:{actionable:true,quote_at,valid_until:start},stake_market:{name:'Double Chance',availability_verified:true,event_url:oldURL,tab:'Winner'}};
}
async function boot(t,body){
 const w=new Window({url:'http://127.0.0.1:8765',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableIframePageLoading:true}});
 t.after(()=>w.happyDOM.close());w.document.write(html);const writes=[];
 w.setInterval=()=>0;w.EventSource=class{addEventListener(name,cb){if(name==='update')w.testUpdate=cb}};
 w.fetch=async(path,options)=>{if(options?.method&&options.method!=='GET')writes.push(path);return {ok:true,json:async()=>structuredClone(path==='/api/dashboard'?{version:1,settings:{max_bet:5,minimum_roi:.03,daily_pick_target:15,refresh_seconds:60},engine:{running:false},status:{},games:[],models:{},performance:{prospective:[]},...body}:{bets:[],performance:{actual_bets:{}}})}};
 w.eval(script+'\n;window.navigationHelpers={navigation:stakeNavigation,button:stakeBetButton,setView:value=>view=value};');await new Promise(r=>setTimeout(r,10));return {w,d:w.document,writes};
}
function tracked(original,current){return {recommendations:[],evaluated_candidates:[current],tracked_bets:[{id:1,tip_id:original.id,stake:2.34,odds:2,status:'open',created_at:original.quote_at,payload:{...original,tracking_source:'auto_assumed_placed'},instruction_review:current.execution_review}]}}

test('current unknown website route overrides recorded Winner in card and details',async t=>{
 const original=selection(),current={...original,stake:0,stake_market:{...original.stake_market,tab:null,event_url:newURL,navigation_status:'website_tab_unconfirmed'}};
 const body=tracked(original,current),before=structuredClone(body),{d,writes}=await boot(t,body);
 assert.equal(d.querySelectorAll('.tip-card').length,1);assert.equal(d.querySelector('.stake-tab'),null);
 assert.equal(d.querySelector('.stake-bet-button').href,newURL);
 assert.doesNotMatch(d.querySelector('.tip-card').textContent,/Winner tab|Stake tab:|Select the Main/);
 assert.match(d.querySelector('.tip-card').textContent,/Bet \$2\.34/);
 d.querySelector('.tip-card [data-detail]').click();
 assert.doesNotMatch(d.querySelector('#detail-content').textContent,/Winner tab|Select the Main/);
 assert.match(d.querySelector('#detail-content').textContent,/Regulation Time/);
 assert.match(d.querySelector('#detail-content').textContent,/Draw or Los Angeles Kings/);
 assert.deepEqual(body,before);assert.deepEqual(writes,[]);
});

test('navigation-only refresh closes the old detail and shows the new path',async t=>{
 const tip=selection(),body={recommendations:[tip]},h=await boot(t,body);
 h.d.querySelector('.tip-card [data-detail]').click();assert.equal(h.d.querySelector('#detail-dialog').open,true);
 const unchangedQuote=tip.quote_at;tip.stake_market={...tip.stake_market,tab:null,navigation_status:'website_tab_unconfirmed'};
 await h.w.testUpdate();assert.equal(tip.quote_at,unchangedQuote);
 assert.equal(h.d.querySelector('#detail-dialog').open,false);assert.equal(h.d.querySelector('.stake-tab'),null);
 h.d.querySelector('.tip-card [data-detail]').click();assert.doesNotMatch(h.d.querySelector('#detail-content').textContent,/Winner tab/);
});

test('current route and event URL are one source even for historical view links',async t=>{
 const original=selection(),current={...original,stake_market:{...original.stake_market,event_url:newURL,tab:null}};
 const {w}=await boot(t,{recommendations:[],evaluated_candidates:[current]});
 w.navigationHelpers.setView('ledger');
 const markup=w.navigationHelpers.button(original);
 assert.match(markup,/View on Stake/);assert.ok(markup.includes(newURL));assert.ok(!markup.includes(oldURL));assert.doesNotMatch(markup,/Winner tab/);
});

test('stale or different-contract assessment cannot replace historical navigation',async t=>{
 const original=selection();original.stake_market.tab='Periods';
 const {w}=await boot(t,{recommendations:[],evaluated_candidates:[]});
 for(const delta of [{id:'different'}, {market:'period_1:double_chance'}, {side:'home_draw'}, {quote_at:new Date(Date.now()-301000).toISOString()}]){
  const input={...original,current_assessment:{...original,...delta,stake_market:{event_url:newURL,tab:'Wrong'}}};
  const nav=w.navigationHelpers.navigation(input);assert.equal(nav.tab,'Periods');assert.equal(nav.event_url,oldURL);
 }
});

test('fresh explicit null navigation cannot resurrect an old event link',async t=>{
 const original=selection(),current={...original,stake_market:null};const {w}=await boot(t,{recommendations:[],evaluated_candidates:[current]});
 assert.equal(w.navigationHelpers.button(original), '');
});

test('event-link-only refresh also invalidates the open instruction',async t=>{
 const tip=selection();tip.stake_market.tab=null;const body={recommendations:[tip]},h=await boot(t,body);
 h.d.querySelector('.tip-card [data-detail]').click();tip.stake_market.event_url=newURL;await h.w.testUpdate();
 assert.equal(h.d.querySelector('#detail-dialog').open,false);assert.equal(h.d.querySelector('.stake-bet-button').href,newURL);
});

test('legacy issued changed cards retain their exact recorded Stake link and shared card layout',async t=>{
 const original=selection();delete original.stake_market;original.reference_url=oldURL;
 original.tracking_source='auto_assumed_placed';
 const current={...original,stake:0,execution_review:{actionable:false,reason:'Sizing no longer validates'}};
 const body=tracked(original,current);body.status={daily_progress:{issuance_rows:[{tip_id:original.id,issued_at:new Date().toISOString(),start:original.start}]}};
 const {d,w,writes}=await boot(t,body);
 const card=d.querySelector('.changed-card');assert.ok(card);
 const link=card.querySelector('.stake-bet-button');assert.ok(link);assert.equal(link.href,oldURL);assert.equal(link.target,'_blank');assert.match(link.textContent,/View on Stake/);
 assert.deepEqual([...card.querySelectorAll('.tip-metrics .metric-label')].map(x=>x.textContent),['MODEL WIN PROBABILITY','ORIGINAL ODDS','ORIGINAL STAKE · USD']);
 assert.match(card.textContent,/No bet anymore/);assert.ok(card.querySelector('[data-placement]'));assert.deepEqual(writes,[]);
 w.navigationHelpers.setView('ledger');assert.equal(w.navigationHelpers.button({...original,reference_url:'https://example.com/sports/event'}),'');
 assert.equal(w.navigationHelpers.button({...original,stake_market:null}),'');
});
