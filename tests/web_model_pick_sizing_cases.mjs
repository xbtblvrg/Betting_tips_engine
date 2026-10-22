import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
const html=readFileSync('src/betting_engine/web/index.html','utf8');
const script=readFileSync('src/betting_engine/web/app.js','utf8');
const flush=async()=>{for(let i=0;i<35;i++)await Promise.resolve()};
function modelPick(){
 const now=Date.now(),quote_at=new Date(now).toISOString(),start=new Date(now+3600000).toISOString();
 const p=.6,push=0,stress=.54,odds=1.7,minimum=(1-push)/p;
 const basis={policy:'continuous_model_v1',tier:'model_pick',eligible:true,minimum_odds:minimum,maximum_stake:1};
 return {id:'model-pick',game_id:'g',rank:1,league:'NHL',home:'Home',away:'Away',start,quote_at,valid_until:new Date(now+240000).toISOString(),market:'totals',model_scope:'full_game',side:'over',line:5.5,selection:'Over 5.5',market_display:'Total (Incl. Overtime and Penalties)',probability:p,push_probability:push,stress_probability:stress,reference_odds:odds,expected_roi:p*odds+push-1,stress_roi:stress*odds+push-1,minimum_odds:1.03/stress,reference_platform:'Stake.com',reference_url:'https://stake.com/sports/ice-hockey/usa/nhl/123-home-away',source_url:'https://example.com',stake:1,stake_note:'Fixed model-pick unit.',model_version:'test',funding_basis:basis,decision_guard:{passed:true},execution_review:{state:'current',actionable:true,quote_at,valid_until:new Date(now+240000).toISOString(),funding_tier:'model_pick',recommendation_policy:'continuous_model_v1',minimum_odds:minimum},stake_market:{name:'Total (Incl. Overtime and Penalties)',availability_verified:true,tab:'Main',event_url:'https://stake.com/sports/ice-hockey/usa/nhl/123-home-away'},reasons:[],warnings:[],game_context:{}};
}
function snapshot(tip=modelPick()){
 return {version:1,recommendations:[tip],evaluated_candidates:[tip],tracked_bets:[],placement_marks:{},settings:{recommendation_policy:'continuous_model_v1',bankroll:null,daily_risk_limit:null,daily_pick_target:15,max_bet:5,unit_stake:1,minimum_roi:.03,refresh_seconds:60,monthly_system_cost:null},engine:{running:false,stage:'Updated'},status:{},games:[],models:{},performance:{prospective:[]}};
}
async function boot(t,payload=snapshot()){
 const w=new Window({url:'http://127.0.0.1:8765',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableIframePageLoading:true}});t.after(()=>w.happyDOM.close());w.document.write(html);
 let current=payload;const writes=[],intervals=[];
 w.setInterval=(callback,ms)=>{intervals.push({callback,ms});return intervals.length};
 w.EventSource=class{addEventListener(name,callback){if(name==='update')w.testUpdate=callback}};
 w.fetch=async(path,options={})=>{
  if(options.method&&options.method!=='GET')writes.push({path,body:options.body});
  if(path==='/api/dashboard')return {ok:true,json:async()=>structuredClone(current)};
  if(path.endsWith('/checklist')){const placed=JSON.parse(options.body).placed;current.placement_marks['model-pick']=placed?new Date().toISOString():undefined;return {ok:true,json:async()=>({placed,placed_at:current.placement_marks['model-pick']})}}
  if(path==='/api/bets')return {ok:true,json:async()=>({bets:[],performance:{}})};
  return {ok:true,json:async()=>({})};
 };
 w.eval(script);await flush();return {w,d:w.document,writes,intervals,update:async next=>{current=next;await w.testUpdate();await flush()}};
}


function weightedSnapshot(amount=2.81){const p=snapshot();p.settings.model_pick_sizing_policy='model_weighted_v2';const c=p.recommendations[0];c.stake=amount;c.funding_basis={...c.funding_basis,version:'funding-policy-v2',model_pick_sizing_policy:'model_weighted_v2',maximum_stake:5};c.execution_review.model_pick_sizing_policy='model_weighted_v2';c.decision_guard.model_pick_sizing_policy='model_weighted_v2';return p}
for(const amount of [1,2.81,5])test('weighted model instruction shows its actual calculated stake '+amount,async t=>{const {d,writes}=await boot(t,weightedSnapshot(amount));assert.equal(d.querySelectorAll('.tip-card').length,1);const card=d.querySelector('.tip-card').textContent;assert.match(card,new RegExp('Model Pick · \\$'+amount.toFixed(2).replace('.','\\.')));assert.match(card,/\$1–\$5 calculated stake/);assert.match(card,/Positive model estimate; stress check not passed/);d.querySelector('[data-detail]').click();const detail=d.querySelector('#detail-content').textContent;assert.match(detail,/Calculated nominal-model stake in the \$1–\$5 range/);assert.match(detail,/related-exposure discount and any supplied bankroll limits/);assert.match(detail,/not a guarantee/);assert.doesNotMatch(detail,/capped at \$1;/);assert.deepEqual(writes,[])});
const invalidWeighted=[
 ['missing guard mode',p=>delete p.recommendations[0].decision_guard.model_pick_sizing_policy],
 ['wrong guard mode',p=>p.recommendations[0].decision_guard.model_pick_sizing_policy='unit_cap_v1'],
 ['missing setting',p=>delete p.settings.model_pick_sizing_policy],
 ['unknown setting',p=>p.settings.model_pick_sizing_policy='auto_raise'],
 ['null setting does not activate weighted',p=>p.settings.model_pick_sizing_policy=null],
 ['legacy mode with new receipt',p=>p.settings.model_pick_sizing_policy='unit_cap_v1'],
 ['missing basis version',p=>delete p.recommendations[0].funding_basis.version],
 ['legacy basis version',p=>p.recommendations[0].funding_basis.version='funding-policy-v1'],
 ['missing basis mode',p=>delete p.recommendations[0].funding_basis.model_pick_sizing_policy],
 ['wrong basis mode',p=>p.recommendations[0].funding_basis.model_pick_sizing_policy='unit_cap_v1'],
 ['missing review mode',p=>delete p.recommendations[0].execution_review.model_pick_sizing_policy],
 ['wrong review mode',p=>p.recommendations[0].execution_review.model_pick_sizing_policy='unit_cap_v1'],
 ['above hard cap',p=>p.recommendations[0].stake=5.01],
 ['above current basis cap',p=>{p.recommendations[0].funding_basis.maximum_stake=2;p.recommendations[0].stake=2.81}],
 ['above configured max',p=>p.settings.max_bet=2],
 ['forged basis above five',p=>p.recommendations[0].funding_basis.maximum_stake=6],
 ['below funded minimum',p=>p.recommendations[0].stake=.49],
 ['nonfinite stake',p=>p.recommendations[0].stake=Infinity],
 ['negative nominal model return',p=>{const c=p.recommendations[0];c.reference_odds=1.6;c.expected_roi=c.probability*1.6-1;c.stress_roi=c.stress_probability*1.6-1}],
 ['server website hold',p=>{p.recommendations[0].execution_review.actionable=false;p.recommendations[0].execution_review.state='website_feed_conflict'}],
];
for(const [name,mutate] of invalidWeighted)test('weighted stake fail-closed: '+name,async t=>{const p=weightedSnapshot();mutate(p);const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0)});
test('legacy default mode still permits one dollar only with no added receipt fields',async t=>{const p=snapshot();const {d,update}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);const larger=snapshot();larger.recommendations[0].stake=2;larger.recommendations[0].funding_basis.maximum_stake=5;await update(larger);assert.equal(d.querySelectorAll('.tip-card').length,0)});
test('original one-dollar instruction stays one dollar under a fresh weighted review without top-up',async t=>{const p=weightedSnapshot(0),c=p.recommendations[0],original=modelPick();original.tracking_source='auto_assumed_placed';const originalCopy=structuredClone(original);p.recommendations=[];p.tracked_bets=[{id:22,tip_id:c.id,created_at:original.quote_at,stake:1,odds:1.7,status:'open',payload:original,instruction_review:{...c.execution_review}}];const {d,writes}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Model Pick · \$1\.00/);assert.match(d.querySelector('.tip-card').textContent,/Bet \$1\.00/);d.querySelector('[data-detail]').click();assert.match(d.querySelector('#detail-content').textContent,/Bet \$1\.00 once/);assert.match(d.querySelector('#detail-content').textContent,/No additional amount is requested for an earlier ticket/);assert.equal(d.querySelector('#record-bet').hidden,true);assert.deepEqual(original,originalCopy);assert.deepEqual(writes,[])});
test('tracked instruction requires the fresh review sizing mode too',async t=>{const p=weightedSnapshot(0),c=p.recommendations[0];p.recommendations=[];p.tracked_bets=[{id:23,tip_id:c.id,created_at:c.quote_at,stake:1,odds:1.7,status:'open',payload:{...modelPick(),tracking_source:'auto_assumed_placed'},instruction_review:{...c.execution_review,model_pick_sizing_policy:'unit_cap_v1'}}];const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0);assert.match(d.querySelector('#history-table').textContent,/\$1\.00/)});
test('sizing mode change alone invalidates a one-dollar modal while retaining the amount',async t=>{const {d,update}=await boot(t);d.querySelector('[data-detail]').click();assert.equal(d.querySelector('#detail-dialog').open,true);await update(weightedSnapshot(1));assert.equal(d.querySelector('#detail-dialog').open,false);assert.match(d.querySelector('.tip-card').textContent,/Bet \$1\.00/)});
function weightedProbability(amount=1){const p=weightedSnapshot(amount),c=p.recommendations[0];p.settings.recommendation_policy='continuous_best_available_v1';c.reference_odds=1.6;c.expected_roi=c.probability*1.6-1;c.stress_roi=c.stress_probability*1.6-1;c.funding_basis={...c.funding_basis,policy:'continuous_best_available_v1',tier:'probability_pick',minimum_odds:1,maximum_stake:1};c.execution_review={...c.execution_review,recommendation_policy:'continuous_best_available_v1',funding_tier:'probability_pick',minimum_odds:1};return p}
test('weighted setting does not change the probability-pick one-dollar ceiling',async t=>{const {d,update}=await boot(t,weightedProbability());assert.match(d.querySelector('.tip-card').textContent,/Probability Pick · \$1/);assert.match(d.querySelector('.tip-card').textContent,/return: -4\.0%/);await update(weightedProbability(2));assert.equal(d.querySelectorAll('.tip-card').length,0);const forged=weightedProbability(2);forged.recommendations[0].funding_basis.maximum_stake=5;await update(forged);assert.equal(d.querySelectorAll('.tip-card').length,0)});
test('strict stress-value staking stays unchanged with an explicit weighted settings receipt',async t=>{const p=weightedSnapshot(4),c=p.recommendations[0];p.settings.recommendation_policy='strict';c.reference_odds=2;c.expected_roi=c.probability*2-1;c.stress_roi=c.stress_probability*2-1;c.funding_basis={...c.funding_basis,policy:'strict',tier:'stress_value',minimum_odds:c.minimum_odds};c.execution_review={...c.execution_review,recommendation_policy:'strict',funding_tier:'stress_value',minimum_odds:c.minimum_odds};const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Bet \$4\.00/);assert.doesNotMatch(d.querySelector('.tip-card').textContent,/Model Pick/)});
test('aggregate historical Model Pick report does not falsely label all stakes one dollar',async t=>{const p=weightedSnapshot();p.performance.funding_tiers=[{tier:'model_pick',league:'NHL',market:'totals',issued:2,games:2,settled:1,won:0,lost:1,pushes:0,settled_stake:2.81,pnl:-2.81,roi:-1,hit_rate:0}];const {d}=await boot(t,p);const text=d.querySelector('#funding-tier-evidence').textContent;assert.match(text,/Model Pick/);assert.doesNotMatch(text,/Model Pick · \$1/);assert.match(text,/\$-2\.81/)});

test('explicit null sizing mode is invalid even beside an otherwise valid legacy receipt',async t=>{const p=snapshot();p.settings.model_pick_sizing_policy=null;const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0)});
