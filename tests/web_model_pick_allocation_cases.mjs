import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
const html=readFileSync('src/betting_engine/web/index.html','utf8');
const script=readFileSync('src/betting_engine/web/app.js','utf8');
const flush=async()=>{for(let i=0;i<35;i++)await Promise.resolve()};
// Each browser gets the same explicit fixture clock. Suite scheduling must not
// age valid, expired or future quotes before their assertions execute.
const FIXTURE_NOW=Date.parse('2026-09-30T12:00:00Z');
function modelPick(){
 const now=FIXTURE_NOW,quote_at=new Date(now).toISOString(),start=new Date(now+3600000).toISOString();
 const p=.6,push=0,stress=.54,odds=1.7,minimum=(1-push)/p;
 const basis={policy:'continuous_model_v1',tier:'model_pick',eligible:true,minimum_odds:minimum,maximum_stake:1};
 return {id:'model-pick',game_id:'g',rank:1,league:'NHL',home:'Home',away:'Away',start,quote_at,valid_until:new Date(now+240000).toISOString(),market:'totals',model_scope:'full_game',side:'over',line:5.5,selection:'Over 5.5',market_display:'Total (Incl. Overtime and Penalties)',probability:p,push_probability:push,stress_probability:stress,reference_odds:odds,expected_roi:p*odds+push-1,stress_roi:stress*odds+push-1,minimum_odds:1.03/stress,reference_platform:'Stake.com',reference_url:'https://stake.com/sports/ice-hockey/usa/nhl/123-home-away',source_url:'https://example.com',stake:1,stake_note:'Fixed model-pick unit.',model_version:'test',funding_basis:basis,decision_guard:{passed:true},execution_review:{state:'current',actionable:true,quote_at,valid_until:new Date(now+240000).toISOString(),funding_tier:'model_pick',recommendation_policy:'continuous_model_v1',minimum_odds:minimum},stake_market:{name:'Total (Incl. Overtime and Penalties)',availability_verified:true,tab:'Main',event_url:'https://stake.com/sports/ice-hockey/usa/nhl/123-home-away'},reasons:[],warnings:[],game_context:{}};
}
function snapshot(tip=modelPick()){
 return {version:1,recommendations:[tip],evaluated_candidates:[tip],tracked_bets:[],placement_marks:{},settings:{recommendation_policy:'continuous_model_v1',bankroll:null,daily_risk_limit:null,daily_pick_target:15,max_bet:5,unit_stake:1,minimum_roi:.03,refresh_seconds:60,monthly_system_cost:null},engine:{running:false,stage:'Updated'},status:{},games:[],models:{},performance:{prospective:[]}};
}
async function boot(t,payload=snapshot()){
 const w=new Window({url:'http://127.0.0.1:8765',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableIframePageLoading:true}});t.after(()=>w.happyDOM.close());
 const NativeDate=w.Date;
 w.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[FIXTURE_NOW]))}static now(){return FIXTURE_NOW}};
 w.document.write(html);
 let current=payload,fail=false;const writes=[],intervals=[];
 w.setInterval=(callback,ms)=>{intervals.push({callback,ms});return intervals.length};
 w.EventSource=class{addEventListener(name,callback){if(name==='update')w.testUpdate=callback}};
 w.fetch=async(path,options={})=>{
  if(options.method&&options.method!=='GET')writes.push({path,body:options.body});
  if(path==='/api/dashboard'&&fail)throw Error('offline');
  if(path==='/api/dashboard')return {ok:true,json:async()=>structuredClone(current)};
  if(path.endsWith('/checklist')){const placed=JSON.parse(options.body).placed;current.placement_marks['model-pick']=placed?new Date(FIXTURE_NOW).toISOString():undefined;return {ok:true,json:async()=>({placed,placed_at:current.placement_marks['model-pick']})}}
  if(path==='/api/bets')return {ok:true,json:async()=>({bets:[],performance:{}})};
  return {ok:true,json:async()=>({})};
 };
 w.eval(script);await flush();return {w,d:w.document,writes,intervals,offline:async()=>{fail=true;await w.testUpdate();await flush()},update:async next=>{current=next;await w.testUpdate();await flush()}};
}


function allTierSnapshot(tier='probability_pick',amount=3){
 const p=snapshot(),c=p.recommendations[0];p.settings.recommendation_policy='continuous_best_available_v1';p.settings.model_pick_sizing_policy='all_tiers_weighted_v3';
 c.stake=amount;c.reference_odds=tier==='probability_pick'?1.6:tier==='stress_value'?2:1.7;
 c.expected_roi=c.probability*c.reference_odds-1;c.stress_roi=c.stress_probability*c.reference_odds-1;
 const minimum=tier==='probability_pick'?1:tier==='stress_value'?c.minimum_odds:(1-c.push_probability)/c.probability;
 c.funding_basis={version:'funding-policy-v3',policy:p.settings.recommendation_policy,model_pick_sizing_policy:'all_tiers_weighted_v3',tier,eligible:true,minimum_odds:minimum,maximum_stake:5};
 c.decision_guard={passed:true,recommendation_policy:p.settings.recommendation_policy,model_pick_sizing_policy:'all_tiers_weighted_v3'};
 c.execution_review={...c.execution_review,recommendation_policy:p.settings.recommendation_policy,model_pick_sizing_policy:'all_tiers_weighted_v3',funding_tier:tier,minimum_odds:minimum};
 return p;
}
// Independent allocation-policy parity checks. Every old test stays unchanged.
const KEY='model_pick_allocation_policy',LEGACY='one_per_game_v1',DISTINCT='distinct_family_v1';
function allocationSnapshot({allocation=DISTINCT,tier='model_pick',tracked=false}={}){
 const p=allTierSnapshot(tier,2.5),c=p.recommendations[0];
 c.market='period_1:totals';c.model_scope='period_1';c.period='1st Period';c.line=1.5;c.selection='Over 1.5';c.market_display='1st Period - Total';c.stake_market.name=c.market_display;c.stake_market.tab='Periods';
 for(const part of [p.settings,c.funding_basis,c.decision_guard,c.execution_review])part[KEY]=allocation;
 if(tracked){const original=structuredClone(c);original.tracking_source='auto_assumed_placed';for(const part of [original.funding_basis,original.decision_guard,original.execution_review])delete part[KEY];p.recommendations=[];c.stake=0;p.tracked_bets=[{id:77,tip_id:c.id,created_at:c.quote_at,stake:1,odds:c.reference_odds,status:'open',payload:original,instruction_review:{...c.execution_review}}]}
 return p;
}
function withoutPolicy(p){for(const v of [p.settings,p.evaluated_candidates[0].decision_guard,p.evaluated_candidates[0].funding_basis,p.evaluated_candidates[0].execution_review,...p.tracked_bets.map(b=>b.instruction_review)])delete v[KEY];return p}
for(const allocation of [LEGACY,DISTINCT])for(const tier of ['stress_value','model_pick','probability_pick'])test(`matching allocation ${allocation} preserves ${tier} stake and labels`,async t=>{
 const {d,writes}=await boot(t,allocationSnapshot({allocation,tier}));assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Bet \$2\.50/);assert.match(d.querySelector('.tip-card').textContent,/1st Period - Total/);d.querySelector('[data-detail]').click();assert.match(d.querySelector('#detail-content').textContent,/1st Period/);assert.deepEqual(writes,[]);
});
test('legacy allocation absence stays compatible without adding receipt fields',async t=>{const p=withoutPolicy(allocationSnapshot()),before=structuredClone(p);const {d,writes}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.deepEqual(p,before);assert.deepEqual(writes,[])});
const boundaries={settings:p=>p.settings,guard:p=>p.evaluated_candidates[0].decision_guard,basis:p=>p.evaluated_candidates[0].funding_basis,current_review:p=>p.evaluated_candidates[0].execution_review,instruction_review:p=>p.tracked_bets[0].instruction_review};
for(const [name,get] of Object.entries(boundaries))for(const [label,value] of [['legacy mismatch',LEGACY],['unknown','unlimited'],['null',null],['boolean',true],['object',{}],['missing',undefined]])test(`distinct allocation rejects ${name} ${label}`,async t=>{
 const p=allocationSnapshot({tracked:true});if(value===undefined)delete get(p)[KEY];else get(p)[KEY]=value;const {d,writes}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0);assert.match(d.querySelector('#history-table').textContent,/\$1\.00/);assert.deepEqual(writes,[]);
});
for(const [name,get] of Object.entries(boundaries))test(`legacy default rejects injected distinct ${name}`,async t=>{const p=withoutPolicy(allocationSnapshot({tracked:true}));get(p)[KEY]=DISTINCT;const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0)});
test('unknown matching allocation strings cannot grant authority',async t=>{const {d}=await boot(t,allocationSnapshot({allocation:'future-unapproved'}));assert.equal(d.querySelectorAll('.tip-card').length,0)});
test('old tracked ticket keeps original amount with fresh distinct-family evidence',async t=>{const p=allocationSnapshot({tracked:true}),before=structuredClone(p.tracked_bets[0]);const {d,writes}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Bet \$1\.00/);d.querySelector('[data-detail]').click();assert.match(d.querySelector('#detail-content').textContent,/Bet \$1\.00 once/);assert.equal(d.querySelector('#record-bet').hidden,true);assert.deepEqual(p.tracked_bets[0],before);assert.deepEqual(writes,[])});
for(const [from,to] of [[LEGACY,DISTINCT],[DISTINCT,LEGACY]])test(`allocation ${from} to ${to} invalidates modal at identical odds and stake`,async t=>{
 const {d,update}=await boot(t,allocationSnapshot({allocation:from}));d.querySelector('[data-detail]').click();assert.equal(d.querySelector('#detail-dialog').open,true);const next=allocationSnapshot({allocation:to});next.version=2;await update(next);assert.equal(d.querySelector('#detail-dialog').open,false);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Bet \$2\.50/);
});
test('explicit default alone does not close an otherwise identical legacy modal',async t=>{const {d,update}=await boot(t,withoutPolicy(allocationSnapshot()));d.querySelector('[data-detail]').click();await update(allocationSnapshot({allocation:LEGACY}));assert.equal(d.querySelector('#detail-dialog').open,true)});
for(const [name,mutate] of [
 ['guard failure',p=>p.evaluated_candidates[0].decision_guard.passed=false],
 ['server rejection',p=>p.evaluated_candidates[0].execution_review.actionable=false],
 ['stale quote',p=>p.evaluated_candidates[0].quote_at=new Date(FIXTURE_NOW-301000).toISOString()],
 ['invalid model probability',p=>p.evaluated_candidates[0].probability=NaN],
 ['over stake cap',p=>p.evaluated_candidates[0].stake=5.01],
 ['negative nominal edge',p=>{const c=p.evaluated_candidates[0];c.reference_odds=1.6;c.expected_roi=.6*1.6-1;c.stress_roi=.54*1.6-1}],
])test(`distinct policy preserves ${name}`,async t=>{const p=allocationSnapshot();mutate(p);const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0)});


// Strict plus legacy sizing is still a valid configuration when the explicitly
// activated allocation policy supplies its sealed funding basis from the server.
function strictUnitAllocationSnapshot(){
 const p=allocationSnapshot({tier:'stress_value'}),c=p.evaluated_candidates[0];
 p.settings.recommendation_policy='strict';p.settings.model_pick_sizing_policy='unit_cap_v1';
 c.decision_guard.recommendation_policy='strict';c.decision_guard.model_pick_sizing_policy='unit_cap_v1';
 c.funding_basis.policy='strict';c.funding_basis.version='funding-policy-v1';delete c.funding_basis.model_pick_sizing_policy;
 c.execution_review.recommendation_policy='strict';c.execution_review.model_pick_sizing_policy='unit_cap_v1';
 return p;
}
test('strict unit-cap policy displays a fresh distinct-allocation Stress Value instruction',async t=>{
 const {d,writes}=await boot(t,strictUnitAllocationSnapshot());
 assert.equal(d.querySelectorAll('.tip-card').length,1);
 assert.match(d.querySelector('.tip-card').textContent,/Bet \$2\.50/);
 assert.deepEqual(writes,[]);
});
for(const missing of ['basis','basis_policy'])test(`strict unit-cap distinct allocation rejects missing ${missing}`,async t=>{
 const p=strictUnitAllocationSnapshot(),c=p.evaluated_candidates[0];
 if(missing==='basis')delete c.funding_basis;else delete c.funding_basis[KEY];
 const {d,writes}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0);assert.deepEqual(writes,[]);
});
