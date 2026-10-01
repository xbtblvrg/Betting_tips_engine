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

test('continuous model pick requires positive model EV and shows the effective nominal threshold and honest stress note',async t=>{
 const {d,writes}=await boot(t);
 assert.equal(d.querySelectorAll('.tip-card').length,1);const card=d.querySelector('.tip-card').textContent;
 assert.match(card,/Model Pick · \$1/);assert.match(card,/Bet \$1\.00/);assert.match(card,/Minimum odds: 1\.67/);assert.match(card,/Positive model estimate; stress check not passed\./);
 assert.doesNotMatch(card,/Estimated Value|Ready to Bet|1\.91|\$1–\$5 per selection/);
 d.querySelector('[data-detail]').click();const detail=d.querySelector('#detail-content').textContent;
 assert.match(detail,/Model EV: 2\.0% · Stress EV: -8\.2%/);assert.match(detail,/not a guarantee of winning or profit/);assert.match(detail,/1\.67 or higher/);assert.doesNotMatch(detail,/1\.91 or higher/);assert.deepEqual(writes,[]);
});

const malformedCases=[
 ['missing explicit opt-in',p=>delete p.settings.recommendation_policy],
 ['strict mode',p=>p.settings.recommendation_policy='strict'],
 ['missing funding basis',p=>delete p.recommendations[0].funding_basis],
 ['unfunded basis',p=>p.recommendations[0].funding_basis.tier='unfunded'],
 ['basis not eligible',p=>p.recommendations[0].funding_basis.eligible=false],
 ['forged basis policy',p=>p.recommendations[0].funding_basis.policy='strict'],
 ['mismatched server tier',p=>p.recommendations[0].execution_review.funding_tier='stress_value'],
 ['missing server policy',p=>delete p.recommendations[0].execution_review.recommendation_policy],
 ['missing server current state',p=>delete p.recommendations[0].execution_review.state],
 ['rejected website/current review',p=>{p.recommendations[0].execution_review.actionable=false;p.recommendations[0].execution_review.state='website_feed_conflict'}],
 ['negative nominal EV',p=>{const c=p.recommendations[0];c.reference_odds=1.6;c.expected_roi=c.probability*1.6-1;c.stress_roi=c.stress_probability*1.6-1}],
 ['zero nominal EV',p=>{const c=p.recommendations[0];c.reference_odds=1/c.probability;c.expected_roi=0;c.stress_roi=c.stress_probability*c.reference_odds-1}],
 ['forged positive nominal EV',p=>p.recommendations[0].expected_roi=.15],
 ['invalid probability mass',p=>p.recommendations[0].push_probability=.5],
 ['missing push probability',p=>delete p.recommendations[0].push_probability],
 ['forged stress EV',p=>p.recommendations[0].stress_roi=0],
 ['over one-dollar instruction',p=>p.recommendations[0].stake=1.01],
 ['forged two-dollar basis maximum',p=>p.recommendations[0].funding_basis.maximum_stake=2],
 ['mismatched effective threshold',p=>p.recommendations[0].execution_review.minimum_odds=1.5],
 ['forged matching but non-nominal threshold',p=>{p.recommendations[0].execution_review.minimum_odds=1.5;p.recommendations[0].funding_basis.minimum_odds=1.5}],
];
for(const [name,mutate] of malformedCases)test('model pick fail-closed: '+name,async t=>{const p=snapshot();mutate(p);const {d,writes}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0);assert.deepEqual(writes,[])});

test('model pick floating threshold comparisons tolerate serialization noise only',async t=>{const p=snapshot();p.recommendations[0].execution_review.minimum_odds+=1e-12;const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1)});

test('tracked model pick retains the original once-only dollar and moves to tracker after marking',async t=>{
 const p=snapshot(),current=p.recommendations[0],original=structuredClone(current);original.tracking_source='auto_assumed_placed';original.reference_odds=1.75;
 p.recommendations=[];current.stake=0;p.tracked_bets=[{id:7,tip_id:original.id,created_at:original.quote_at,stake:1,odds:1.75,status:'open',payload:original,instruction_review:{...current.execution_review}}];
 const {d,w,writes,update}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Bet \$1\.00/);assert.match(d.querySelector('.tip-card').textContent,/1\.70/);
 d.querySelector('[data-detail]').click();assert.equal(d.querySelector('#record-bet').hidden,true);assert.match(d.querySelector('#detail-content').textContent,/Bet \$1\.00 once/);
 d.querySelector('.close-dialog').click();const check=d.querySelector('[data-placement]');check.checked=true;await check.onchange();await update(p);assert.equal(d.querySelectorAll('.tip-card').length,0);assert.equal(writes.length,1);assert.match(writes[0].path,/checklist$/);assert.equal(p.tracked_bets[0].stake,1);assert.equal(p.tracked_bets[0].odds,1.75);
});

test('recorded stakes above one dollar are not resized into model-pick instructions',async t=>{const p=snapshot(),current=p.recommendations[0],original={...current,tracking_source:'auto_assumed_placed',stake:1.5};p.recommendations=[];current.stake=0;p.tracked_bets=[{id:8,tip_id:current.id,created_at:current.quote_at,stake:1.5,odds:1.7,status:'open',payload:original,instruction_review:{...current.execution_review}}];const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0);assert.match(d.querySelector('#history-table').textContent,/\$1\.50/)});

test('tracked fallback requires matching instruction review and current assessment tier',async t=>{const p=snapshot(),c=p.recommendations[0];p.recommendations=[];c.stake=0;p.tracked_bets=[{id:9,tip_id:c.id,created_at:c.quote_at,stake:1,odds:1.7,status:'open',payload:{...c,tracking_source:'auto_assumed_placed'},instruction_review:{...c.execution_review,funding_tier:'stress_value'}}];const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0)});

test('model pick expiry still removes card and closes its modal on the existing one-second tick',async t=>{const p=snapshot();const {w,d,intervals}=await boot(t,p);d.querySelector('[data-detail]').click();const OriginalDate=w.Date,later=OriginalDate.now()+301000;w.Date=class extends OriginalDate{constructor(...args){super(...(args.length?args:[later]))}static now(){return later}};intervals.find(x=>x.ms===1000).callback();assert.equal(d.querySelectorAll('.tip-card').length,0);assert.equal(d.querySelector('#detail-dialog').open,false)});

function stressPick(){const t=modelPick();t.reference_odds=2;t.expected_roi=t.probability*2-1;t.stress_roi=t.stress_probability*2-1;t.stake=3;t.funding_basis={policy:'continuous_model_v1',tier:'stress_value',eligible:true,minimum_odds:t.minimum_odds,maximum_stake:5};t.execution_review={...t.execution_review,funding_tier:'stress_value',minimum_odds:t.minimum_odds};return t}
test('continuous primary stress-value instructions retain justified stakes above one dollar',async t=>{const {d}=await boot(t,snapshot(stressPick()));assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Bet \$3\.00/);assert.doesNotMatch(d.querySelector('.tip-card').textContent,/Model Pick/)});
test('strict legacy instructions without new receipts remain unchanged',async t=>{const p=snapshot(stressPick());delete p.settings.recommendation_policy;delete p.recommendations[0].funding_basis;delete p.recommendations[0].execution_review.recommendation_policy;delete p.recommendations[0].execution_review.funding_tier;const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Bet \$3\.00/);assert.doesNotMatch(d.querySelector('.tip-card').textContent,/Model Pick/)});
test('changing funding policy alone invalidates the open modal even when prices and probabilities are unchanged',async t=>{const p=snapshot(stressPick());const {d,update}=await boot(t,p);d.querySelector('[data-detail]').click();assert.equal(d.querySelector('#detail-dialog').open,true);const next=structuredClone(p);next.settings.recommendation_policy='strict';next.recommendations[0].funding_basis.policy='strict';next.recommendations[0].execution_review.recommendation_policy='strict';next.evaluated_candidates=next.recommendations;await update(next);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.equal(d.querySelector('#detail-dialog').open,false)});
test('a current tier change replaces a model pick and closes its earlier modal',async t=>{const {d,update}=await boot(t);d.querySelector('[data-detail]').click();await update(snapshot(stressPick()));assert.equal(d.querySelector('#detail-dialog').open,false);assert.match(d.querySelector('.tip-card').textContent,/Bet \$3\.00/);assert.doesNotMatch(d.querySelector('.tip-card').textContent,/Model Pick/)});

function probabilitySnapshot(roiNegative=true){const t=modelPick();t.reference_odds=roiNegative?1.6:1/t.probability;t.expected_roi=t.probability*t.reference_odds+t.push_probability-1;t.stress_roi=t.stress_probability*t.reference_odds+t.push_probability-1;t.funding_basis={policy:'continuous_best_available_v1',tier:'probability_pick',eligible:true,minimum_odds:1,maximum_stake:1};t.execution_review={...t.execution_review,funding_tier:'probability_pick',recommendation_policy:'continuous_best_available_v1',minimum_odds:1};const p=snapshot(t);p.settings.recommendation_policy='continuous_best_available_v1';return p}
test('explicit best-available mode shows a negative-EV probability pick honestly at one dollar',async t=>{const {d,writes}=await boot(t,probabilitySnapshot());assert.equal(d.querySelectorAll('.tip-card').length,1);const card=d.querySelector('.tip-card').textContent;assert.match(card,/Probability Pick · \$1/);assert.match(card,/Selected for win probability\. Model-estimated return: -4\.0%\./);assert.match(card,/Bet \$1\.00/);assert.match(card,/Minimum odds: 1\.00/);assert.doesNotMatch(card,/Positive model estimate|Estimated Value|Model Pick ·/);d.querySelector('[data-detail]').click();assert.match(d.querySelector('#detail-content').textContent,/Model EV: -4\.0% · Stress EV: -13\.6%/);assert.match(d.querySelector('#detail-content').textContent,/Fixed \$1 policy unit; not Kelly sizing/);assert.deepEqual(writes,[])});
test('zero-EV probability pick is labelled zero, never a positive-return claim',async t=>{const {d}=await boot(t,probabilitySnapshot(false));assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Model-estimated return: 0\.0%/);assert.doesNotMatch(d.querySelector('.tip-card').textContent,/Positive model estimate/)});
const invalidProbabilityCases=[
 ['positive-only mode',p=>{p.settings.recommendation_policy='continuous_model_v1';p.recommendations[0].funding_basis.policy='continuous_model_v1';p.recommendations[0].execution_review.recommendation_policy='continuous_model_v1'}],
 ['absent mode',p=>delete p.settings.recommendation_policy],
 ['missing basis',p=>delete p.recommendations[0].funding_basis],
 ['forged positive-edge label',p=>{p.recommendations[0].funding_basis.tier='model_pick';p.recommendations[0].execution_review.funding_tier='model_pick'}],
 ['positive EV cannot masquerade as probability-only',p=>{const c=p.recommendations[0];c.reference_odds=1.7;c.expected_roi=.6*1.7-1;c.stress_roi=.54*1.7-1}],
 ['win probability below one half',p=>{const c=p.recommendations[0];c.probability=.49;c.stress_probability=.43;c.expected_roi=.49*c.reference_odds-1;c.stress_roi=.43*c.reference_odds-1}],
 ['over one-dollar allocation',p=>p.recommendations[0].stake=2],
 ['wrong minimum odds basis',p=>{p.recommendations[0].funding_basis.minimum_odds=1.5;p.recommendations[0].execution_review.minimum_odds=1.5}],
 ['forged review truthy string',p=>p.recommendations[0].execution_review.actionable='true'],
 ['unknown source/website state',p=>{p.recommendations[0].execution_review.actionable=false;p.recommendations[0].execution_review.state='website_feed_conflict'}],
 ['nonfinite model EV',p=>p.recommendations[0].expected_roi=Infinity],
];
for(const [name,mutate] of invalidProbabilityCases)test('probability pick fail-closed: '+name,async t=>{const p=probabilitySnapshot();mutate(p);const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,0)});
test('best-available policy keeps the positive-EV model tier and its separate label',async t=>{const p=snapshot();p.settings.recommendation_policy='continuous_best_available_v1';p.recommendations[0].funding_basis.policy='continuous_best_available_v1';p.recommendations[0].execution_review.recommendation_policy='continuous_best_available_v1';const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Model Pick · \$1/);assert.doesNotMatch(d.querySelector('.tip-card').textContent,/Probability Pick/)});
test('changing between two one-dollar tiers invalidates the old modal and updates return disclosure',async t=>{const {d,update}=await boot(t);d.querySelector('[data-detail]').click();await update(probabilitySnapshot());assert.equal(d.querySelector('#detail-dialog').open,false);assert.match(d.querySelector('.tip-card').textContent,/Probability Pick · \$1/);assert.match(d.querySelector('.tip-card').textContent,/return: -4\.0%/)});


test('zero stress EV at a zero threshold remains a model pick rather than claiming positive stress value',async t=>{const p=snapshot();const c=p.recommendations[0];p.settings.minimum_roi=0;c.stress_probability=1/c.reference_odds;c.stress_roi=c.stress_probability*c.reference_odds-1;const {d}=await boot(t,p);assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Model Pick · \$1/)});
test('Model Performance shows original funding tier losses and missing outcomes without retrospective claims',async t=>{const p=snapshot();p.performance.funding_tier_basis='Original immutable issued tier; P&L before operating costs. <img src=x onerror=alert(1)>';p.performance.funding_tiers=[{tier:'probability_pick',league:'NHL',market:'totals',issued:3,games:2,settled:2,won:0,lost:2,pushes:0,settled_stake:2,pnl:-2,roi:-1,hit_rate:0,first_issued_at:p.recommendations[0].quote_at,last_issued_at:p.recommendations[0].quote_at},{tier:'legacy_unspecified',league:'<script>bad()</script>',market:'<b>moneyline</b>',issued:1,games:1,settled:0,won:0,lost:0,pushes:0,settled_stake:0,pnl:0,roi:null,hit_rate:null,first_issued_at:null,last_issued_at:null}];const {d,update}=await boot(t,p);const report=d.querySelector('#view-models #funding-tier-evidence');assert.ok(report);assert.match(report.textContent,/Original Decisions by Funding Tier/);assert.match(report.textContent,/Probability Pick/);assert.match(report.textContent,/\$-2\.00/);assert.match(report.textContent,/-100\.0% \/ 0\.0%/);assert.match(report.textContent,/Legacy · Tier unspecified/);assert.match(report.textContent,/— \/ —No settled decisions/);assert.match(report.textContent,/<script>bad\(\)<\/script>/);assert.equal(report.querySelectorAll('img,script,b').length,0);assert.match(report.querySelector('.funding-tier-basis').textContent,/Original immutable issued tier/);assert.equal(d.querySelector('#tips-grid #funding-tier-evidence'),null);await update(p);assert.equal(d.querySelectorAll('#funding-tier-evidence').length,1)});

test('model-pick details retain nominal sizing and bankroll/exposure limits rather than claiming a non-Kelly unit',async t=>{const {d}=await boot(t);d.querySelector('[data-detail]').click();const body=d.querySelector('#detail-content').textContent;assert.match(body,/Nominal-model sizing capped at \$1; bankroll and related-exposure limits still apply/);assert.doesNotMatch(body,/not Kelly sizing/)});

test('an exact-cent model break-even is never displayed as an inclusive acceptable betting price',async t=>{const c=modelPick();c.probability=.5;c.stress_probability=.45;c.reference_odds=2.02;c.expected_roi=.5*2.02-1;c.stress_roi=.45*2.02-1;c.minimum_odds=1.03/.45;c.funding_basis.minimum_odds=2;c.execution_review.minimum_odds=2;const {d}=await boot(t,snapshot(c));assert.equal(d.querySelectorAll('.tip-card').length,1);assert.match(d.querySelector('.tip-card').textContent,/Minimum odds: 2\.01 \(above model break-even\)/);d.querySelector('[data-detail]').click();const detail=d.querySelector('#detail-content').textContent;assert.match(detail,/2\.01 or higher \(rounded up strictly above model break-even\)/);assert.doesNotMatch(detail,/2\.00 or higher/);assert.match(detail,/evaluated Stake odds were 2\.02/)});
