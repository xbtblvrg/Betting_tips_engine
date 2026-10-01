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
for(const amount of [2,3,4,5])test('v3 probability displays actual amount '+amount+' with nonpositive non-Kelly disclosure',async t=>{
 const {d,writes}=await boot(t,allTierSnapshot('probability_pick',amount));const card=d.querySelector('.tip-card');assert.ok(card);assert.match(card.textContent,new RegExp('Probability Pick · \\$'+amount+'\\.00'));assert.match(card.textContent,/\$1–\$5 calculated stake/);assert.doesNotMatch(card.textContent,/\$1 maximum/);assert.match(card.textContent,/return: -4\.0%/);
 d.querySelector('[data-detail]').click();const detail=d.querySelector('#detail-content').textContent;assert.match(detail,/Calculated base-unit stake/);assert.match(detail,/reduced for related bets/);assert.match(detail,/not Kelly sizing/);assert.match(detail,/return is nonpositive/);assert.doesNotMatch(detail,/Fixed \$1 policy unit/);assert.deepEqual(writes,[]);
});
for(const tier of ['model_pick','stress_value'])test('v3 '+tier+' labels actual calculated amount',async t=>{const {d}=await boot(t,allTierSnapshot(tier,4.25));assert.match(d.querySelector('.tip-card').textContent,new RegExp((tier==='model_pick'?'Model Pick':'Stress Value')+' · \\$4\\.25'));assert.match(d.querySelector('.tip-card').textContent,/Bet \$4\.25/)});
const invalid=[
 ['null mode',p=>p.settings.model_pick_sizing_policy=null],
 ['unknown mode',p=>p.settings.model_pick_sizing_policy='all_weighted'],
 ['legacy setting',p=>p.settings.model_pick_sizing_policy='unit_cap_v1'],
 ['v2 setting',p=>p.settings.model_pick_sizing_policy='model_weighted_v2'],
 ['missing setting',p=>delete p.settings.model_pick_sizing_policy],
 ['missing basis version',p=>delete p.recommendations[0].funding_basis.version],
 ['v2 basis version',p=>p.recommendations[0].funding_basis.version='funding-policy-v2'],
 ['null basis mode',p=>p.recommendations[0].funding_basis.model_pick_sizing_policy=null],
 ['missing guard mode',p=>delete p.recommendations[0].decision_guard.model_pick_sizing_policy],
 ['wrong guard policy',p=>p.recommendations[0].decision_guard.recommendation_policy='strict'],
 ['wrong current review mode',p=>p.recommendations[0].execution_review.model_pick_sizing_policy='model_weighted_v2'],
 ['wrong review tier',p=>p.recommendations[0].execution_review.funding_tier='model_pick'],
 ['failed current review',p=>p.recommendations[0].execution_review.actionable=false],
 ['expired quote',p=>p.recommendations[0].quote_at=new Date(FIXTURE_NOW-301000).toISOString()],
 ['expired validity',p=>p.recommendations[0].valid_until=new Date(FIXTURE_NOW-1000).toISOString()],
 ['started event',p=>p.recommendations[0].start=new Date(FIXTURE_NOW-1000).toISOString()],
 ['too much',p=>p.recommendations[0].stake=5.01],
 ['forged maximum',p=>p.recommendations[0].funding_basis.maximum_stake=6],
 ['over configured max',p=>p.settings.max_bet=2],
 ['over basis maximum',p=>p.recommendations[0].funding_basis.maximum_stake=2],
 ['nonfinite stake',p=>p.recommendations[0].stake=NaN],
 ['below minimum',p=>p.recommendations[0].stake=.99]
];
for(const [name,mutate]of invalid)test('v3 main instruction rejects '+name,async t=>{const p=allTierSnapshot();mutate(p);const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0)});
test('v2 probability remains capped at one dollar and legacy receipt labels remain intact',async t=>{
 const p=allTierSnapshot('probability_pick',1),c=p.recommendations[0];p.settings.model_pick_sizing_policy='model_weighted_v2';c.funding_basis.version='funding-policy-v2';c.funding_basis.model_pick_sizing_policy=c.decision_guard.model_pick_sizing_policy=c.execution_review.model_pick_sizing_policy='model_weighted_v2';c.funding_basis.maximum_stake=1;
 const {d,update}=await boot(t,p);assert.match(d.querySelector('.tip-card').textContent,/Probability Pick · \$1/);assert.match(d.querySelector('.tip-card').textContent,/\$1 maximum/);const larger=structuredClone(p);larger.recommendations[0].stake=2;larger.recommendations[0].funding_basis.maximum_stake=5;await update(larger);assert.equal(d.querySelectorAll('.tip-card').length,0);
});
test('fresh v3 assessment leaves original pending one-dollar instruction at one dollar',async t=>{
 const p=allTierSnapshot('probability_pick',0),c=p.recommendations[0],original=modelPick();original.tracking_source='auto_assumed_placed';const copy=structuredClone(original);p.recommendations=[];p.tracked_bets=[{id:8,tip_id:c.id,created_at:original.quote_at,stake:1,odds:1.7,status:'open',payload:original,instruction_review:{...c.execution_review}}];
 const {d,writes}=await boot(t,p);assert.match(d.querySelector('.tip-card').textContent,/Bet \$1\.00/);assert.match(d.querySelector('.tip-card').textContent,/Probability Pick · \$1\.00/);assert.deepEqual(original,copy);assert.deepEqual(writes,[]);
});
function assessment(overrides={}){const now=FIXTURE_NOW,current=allTierSnapshot().recommendations[0];const row={tip_id:'model-pick',bet_ids:[8],league:'NHL',away:'Away',home:'Home',start:new Date(now+3600000).toISOString(),market_display:'Total (Incl. Overtime and Penalties)',selection:'Over 5.5',original_stake:1,recorded_stake:1,tracking_sources:['auto_assumed_placed'],eligible:true,reason:'Current calculation',target_stake:3,difference:2,quote_at:new Date(now).toISOString(),valid_until:new Date(now+240000).toISOString(),odds:1.6,sizing:{amount:3},model_pick_sizing_policy:'all_tiers_weighted_v3',...overrides};row.execution_review={...current.execution_review,quote_at:row.quote_at,valid_until:row.valid_until,odds:row.odds};row.funding_basis=current.funding_basis;row.sizing={amount:row.target_stake};return row}
function sizingSnapshot(row=assessment()){const p=allTierSnapshot();p.stake_reassessments=[row];return p}
test('Stake Sizing is a separate read-only view with exact target total and signed difference',async t=>{
 const {d,writes}=await boot(t,sizingSnapshot());d.querySelector('[data-view="sizing"]').click();const section=d.querySelector('#view-sizing');assert.equal(section.hidden,false);assert.equal(d.querySelector('#view-tips').hidden,true);assert.match(section.textContent,/no additional bet is issued here/);assert.match(section.textContent,/not a cashout request/);assert.match(section.textContent,/\$3\.00/);assert.match(section.textContent,/\+\$2\.00/);assert.equal(section.querySelectorAll('button,a,input,form').length,0);assert.deepEqual(writes,[]);
});
test('negative sizing difference is a lower total assessment only',async t=>{const {d}=await boot(t,sizingSnapshot(assessment({recorded_stake:4,target_stake:2,difference:-2})));assert.match(d.querySelector('#stake-sizing-table').textContent,/−\$2\.00/);assert.match(d.querySelector('#view-sizing').textContent,/lower current target/)});
for(const [name,change]of [
 ['expired quote',()=>({quote_at:new Date(FIXTURE_NOW-301000).toISOString()})],['expired validity',()=>({valid_until:new Date(FIXTURE_NOW-1000).toISOString()})],['started game',()=>({start:new Date(FIXTURE_NOW-1000).toISOString()})],['future quote',()=>({quote_at:new Date(FIXTURE_NOW+10000).toISOString()})],['different policy',()=>({model_pick_sizing_policy:'model_weighted_v2'})],['null policy',()=>({model_pick_sizing_policy:null})],['nonfinite target',()=>({target_stake:NaN})],['over maximum',()=>({target_stake:6,difference:5})],['invalid difference',()=>({difference:3})],['blocked source',()=>({eligible:false,reason:'Fresh source required'})]
])test('Stake Sizing hides the numeric target for '+name,async t=>{const {d}=await boot(t,sizingSnapshot(assessment(change())));const cells=[...d.querySelectorAll('#stake-sizing-table tbody td')];assert.equal(cells[2].textContent,'—');assert.equal(cells[5].textContent,'—');assert.equal(cells[6].textContent,'—');assert.match(d.querySelector('#stake-sizing-table').textContent,/\$1\.00/)});
test('Stake Sizing expires on the existing one-second tick without another API update',async t=>{
 const row=assessment({valid_until:new Date(FIXTURE_NOW+1000).toISOString()}),p=sizingSnapshot(row),{d,w,intervals}=await boot(t,p);assert.match(d.querySelector('#stake-sizing-table').textContent,/\$3\.00/);const now=w.Date.now();w.Date.now=()=>now+2000;const OriginalDate=w.Date;w.Date=class extends OriginalDate{constructor(...args){super(...(args.length?args:[now+2000]))}static now(){return now+2000}};intervals.find(i=>i.ms===1000).callback();assert.equal(d.querySelectorAll('#stake-sizing-table tbody td')[5].textContent,'—');
});
test('Stake Sizing hides current targets immediately on dashboard failure',async t=>{const {d,offline}=await boot(t,sizingSnapshot());await offline();assert.equal(d.querySelectorAll('#stake-sizing-table tbody td')[5].textContent,'—')});
test('Stake Sizing escapes source text and never mutates main amounts',async t=>{const p=sizingSnapshot(assessment({selection:'<img src=x onerror=alert(1)>',reason:'<script>bad()</script>'}));const {d}=await boot(t,p);assert.equal(d.querySelector('#stake-sizing-table img'),null);assert.match(d.querySelector('#stake-sizing-table').textContent,/<img/);assert.match(d.querySelector('.tip-card').textContent,/Bet \$3\.00/)});

for(const [name,mutate]of [
 ['missing execution review',row=>delete row.execution_review],
 ['mismatched review mode',row=>row.execution_review.model_pick_sizing_policy='model_weighted_v2'],
 ['mismatched review quote',row=>row.execution_review.odds=2],
 ['mismatched review time',row=>row.execution_review.quote_at=new Date(FIXTURE_NOW-1000).toISOString()],
 ['wrong basis version',row=>row.funding_basis.version='funding-policy-v2'],
 ['wrong basis tier',row=>row.funding_basis.tier='model_pick'],
 ['above basis cap',row=>row.funding_basis.maximum_stake=2],
 ['mismatched sizing amount',row=>row.sizing.amount=2]
])test('Stake Sizing rejects '+name,async t=>{const row=assessment();mutate(row);const {d}=await boot(t,sizingSnapshot(row));assert.equal(d.querySelectorAll('#stake-sizing-table tbody td')[5].textContent,'—')});

test('v3 historical probability outcomes are not all labeled as one-dollar tickets',async t=>{const p=allTierSnapshot();p.performance.funding_tiers=[{tier:'probability_pick',league:'NHL',market:'totals',issued:2,games:2,settled:1,won:0,lost:1,pushes:0,settled_stake:4,pnl:-4,roi:-1,hit_rate:0}];const {d}=await boot(t,p);const text=d.querySelector('#funding-tier-evidence').textContent;assert.match(text,/Probability Pick/);assert.doesNotMatch(text,/Probability Pick · \$1/);assert.match(text,/\$-4\.00/)});
test('v3 strict stress assessment also requires matching policy, mode and basis',async t=>{const p=allTierSnapshot('stress_value',4),c=p.recommendations[0];p.settings.recommendation_policy=c.decision_guard.recommendation_policy=c.funding_basis.policy=c.execution_review.recommendation_policy='strict';const {d,update}=await boot(t,p);assert.match(d.querySelector('.tip-card').textContent,/Stress Value · \$4\.00/);const wrong=structuredClone(p);wrong.recommendations[0].execution_review.recommendation_policy='continuous_model_v1';await update(wrong);assert.equal(d.querySelectorAll('.tip-card').length,0)});
test('same amount on a v2 to v3 mode transition closes the open instruction modal',async t=>{const p=allTierSnapshot('model_pick',1),c=p.recommendations[0];p.settings.model_pick_sizing_policy=c.decision_guard.model_pick_sizing_policy=c.funding_basis.model_pick_sizing_policy=c.execution_review.model_pick_sizing_policy='model_weighted_v2';c.funding_basis.version='funding-policy-v2';const {d,update}=await boot(t,p);d.querySelector('[data-detail]').click();assert.equal(d.querySelector('#detail-dialog').open,true);await update(allTierSnapshot('model_pick',1));assert.equal(d.querySelector('#detail-dialog').open,false)});
