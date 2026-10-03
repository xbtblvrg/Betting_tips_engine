import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
const html=readFileSync('src/betting_engine/web/index.html','utf8');
const script=readFileSync('src/betting_engine/web/app.js','utf8');
const start=new Date(Date.now()+3600000).toISOString();
test('release checkpoint shows its own target and excludes earlier daily issues',async()=>{
 const {w,d,writes}=await boot({status:{daily_progress:{issued:3,target:15,distinct_games:2,shortfall:12,date:'2026-09-30',timezone:'Europe/Budapest',counting_basis:'since_release',counting_since:start,previously_issued_today:48,calendar_day_issued:51}}});
 assert.match(d.querySelector('#stats').textContent,/3 \/ 15.*First Issuances Since/);
 assert.match(d.querySelector('#notice').textContent,/3\/15.*48 earlier issuances excluded.*12 more needed/);
 assert.doesNotMatch(d.querySelector('#notice').textContent,/Target reached/);
 assert.deepEqual(writes,[]);await w.happyDOM.close();
});
test('segment measurement and funded decisions are separate; daily distinct games are disclosed',async()=>{
 const {w,d,writes}=await boot({status:{daily_progress:{issued:18,target:15,distinct_games:9,shortfall:0,date:'2026-09-30',timezone:'Europe/Budapest'}},automation:{shadow_capture:{checked_at:start,accepted:20,duplicates:10,rejected:0,state:'active'},shadow_evidence:{checked_at:start,note:'Shadow observations do not count toward daily funded bets.',prospective_trials:[{model_version:'v1',families:[{league:'NHL',scope:'regulation',family:'totals',distinct_contracts:20,games:2,price_clv:{game_equal_weight_mean:.02,independent_games:2},model_brier:{game_equal_weight_mean:.21},closing_market_brier:{game_equal_weight_mean:.22}}]}],legacy_funded:{families:[{league:'NHL',scope:'full_game',family:'moneyline',original_funded_decisions:5,outcomes_as_recorded:{won:1,lost:4},settled_games:5,recorded_pnl_after_stakes:-2.7,price_clv:{game_equal_weight_mean:-.01,independent_games:5}}]}}}});
 assert.match(d.querySelector('#notice').textContent,/18\/15.*9 distinct games/);
 assert.match(d.querySelector('#shadow-evidence-table').textContent,/Prospective evidence.*no additional stake/);
 assert.match(d.querySelector('#shadow-evidence-summary').textContent,/do not count toward daily funded/);
 assert.match(d.querySelector('#funded-evidence-table').textContent,/-\$2.70/);
 assert.equal(d.querySelector('#view-tips #shadow-evidence-table'),null);
 assert.deepEqual(writes,[]);await w.happyDOM.close();
});
test('parallel lanes and engineering deadlines are distinct from betting instructions',async()=>{
 const {w,d}=await boot({automation:{development:{plan_version:'parallel',state:'active',max_parallel:3,worker:{state:'running',active_count:2,active_tasks:[{task_id:'D04',step:'implementation',step_started_at:start,step_timeout_seconds:1500},{task_id:'D18',step:'integration-review',step_started_at:start,step_timeout_seconds:600}]},engineering_loop:{open_gaps:1,overdue_gaps:1,reassessment_seconds:60,gaps:[{league:'NHL',family:'core_total',reason:'Frozen inputs missing',task_id:'D18',due_at:start,state:'open',overdue:true}]}}},status:{core_markets:{rows:[{league:'NHL',away:'Away',home:'Home',market:'totals',evaluated_outcomes:6,qualified_outcomes:2,selected_selection:'Under 6.5',new_stake:3.4,status:'funded',allocation_reason:'Exact contract qualified'}]}}});
 assert.match(d.querySelector('#development-summary').textContent,/Active Codex tasks: 2 \/ 3/);
 assert.equal(d.querySelectorAll('#development-active tbody tr').length,2);
 assert.match(d.querySelector('#engineering-loop-gaps').textContent,/Frozen inputs missing/);
 assert.match(d.querySelector('#engineering-loop-gaps').textContent,/Overdue/);
 assert.match(d.querySelector('#core-markets-table').textContent,/Under 6.5/);
 assert.match(d.querySelector('#core-markets-table').textContent,/\$3.40/);
 assert.equal(d.querySelector('#view-tips #engineering-loop-gaps'),null);
 await w.happyDOM.close();
});
test('AI roles keep Codex responsible and report helper failures without exposing advice',async()=>{
 const {w,d,writes}=await boot({automation:{development:{plan_version:'test',state:'active',worker:{state:'running'},ai_roles:{scope:'Advisory only; Codex reviews every output.',latest:{codex_review:'pending'},providers:{claude:{role:'secondary',state:'authentication_required',checked_at:start},agy:{role:'tertiary',state:'received_unreviewed',checked_at:start}}}}}});
 assert.match(d.querySelector('#ai-roles-summary').textContent,/Primary brain, developer and final reviewer: Codex/);
 assert.match(d.querySelector('#ai-roles-table').textContent,/authentication required/);
 assert.match(d.querySelector('#ai-roles-table').textContent,/Google Antigravity \(agy\)/);
 assert.equal(d.querySelector('#view-tips #ai-roles-table'),null);
 assert.deepEqual(writes,[]);
 await w.happyDOM.close();
});
test('closing evidence keeps missing prices separate from zero CLV and betting instructions',async()=>{
 const {w,d,writes}=await boot({automation:{closing_evaluation:{enabled:true,evaluated_decisions:2,evaluated_games:1,status_counts:{comparable:1,missing_closing_price:1},note:'Price CLV is a price diagnostic, not realized ROI.',reports:[{league:'NBA',matchup:'A @ B',selection:'Over 210.5',market:'Total (Incl. Overtime)',issued_odds:2,closing_odds:2,price_clv:0,status:'comparable'},{league:'NBA',matchup:'A @ B',selection:'A',market:'Winner (Incl. Overtime)',issued_odds:2,closing_odds:null,price_clv:null,status:'missing_closing_price',reason:'no_eligible_exact_snapshot'}]}}});
 assert.match(d.querySelector('#closing-evaluation-summary').textContent,/2 issued decisions across 1 games/);
 assert.match(d.querySelector('#closing-evaluation-table').textContent,/0\.0%/);
 assert.match(d.querySelector('#closing-evaluation-table').textContent,/no_eligible_exact_snapshot/);
 assert.equal(d.querySelectorAll('#closing-evaluation-table tbody tr').length,2);
 assert.equal(d.querySelector('#view-tips #closing-evaluation-table'),null);
 assert.deepEqual(writes,[]);
 await w.happyDOM.close();
});
test('outcome evidence counts correlated wins by game and does not claim proven profit',async()=>{
 const {w,d}=await boot({performance:{economic_evidence:{settled_decisions:5,settled_games:1,event_dates:1,gross_pnl:6.08,gross_roi:1.216,gross_roi_interval95:null,note:'Several wins on one game do not establish repeatable profitability.',segments:[],exposure:[{league:'NHL',matchup:'A @ B',decisions:4,stake:4.8}]}},automation:{closing_capture:{state:'collecting',stored_captures:3,note:'Evaluation not yet activated.'}}});
 assert.match(d.querySelector('#economic-summary').textContent,/5 settled decisions across 1 distinct games/);
 assert.match(d.querySelector('#economic-summary').textContent,/Insufficient independent results/);
 assert.match(d.querySelector('#economic-exposure').textContent,/\$4.80/);
 assert.match(d.querySelector('#closing-capture-summary').textContent,/Evaluation not yet activated/);
 assert.equal(d.querySelector('#view-tips #economic-summary'),null);
 await w.happyDOM.close();
});
test('NCAA instructions can be filtered and fixture-level missing recommendations stay in Research',async()=>{
 const t={...tip,id:'college',league:'NCAAF'};
 const {w,d}=await boot({recommendations:[t],status:{operations:{checked_at:new Date().toISOString(),leagues:[],fixtures:[{league:'NCAAF',matchup:'Away - Home',start,state:'no_qualifying_edge',reason:'Current price below required value.',evaluated_outcomes:20,qualified_outcomes:0,new_allocations:0,issued_contracts:0}]}},api_audit:{tournament_sources:[{}],fixtures:[{responses:{fixtures:{},odds:{}}}]}});
 const filter=d.querySelector('#league-filter');filter.value='NCAAF';filter.dispatchEvent(new w.Event('change'));
 assert.equal(d.querySelectorAll('.tip-card').length,1);
 assert.match(d.querySelector('#fixture-operations-table').textContent,/Current price below required value/);
 assert.match(d.querySelector('#stake-api-summary').textContent,/2 complete fixture\/odds responses/);
 assert.doesNotMatch(d.querySelector('#tips-grid').textContent,/Current price below required value/);
 await w.happyDOM.close();
});
test('period instructions preserve hockey scope, units and exact observed tab',async()=>{
 const t={...tip,id:'period-total',league:'NHL',market:'period_2:totals',model_scope:'period_2',side:'over',line:1.5,selection:'Over 1.5',market_display:'2nd Period - Total',stake_market:{...tip.stake_market,name:'2nd Period - Total',tab:'periods'}};
 const {w,d}=await boot({recommendations:[t]});d.querySelector('[data-detail]').click();
 assert.match(d.querySelector('#detail-content').textContent,/Only goals in the 2nd Period count toward Over 1.5/);
 assert.doesNotMatch(d.querySelector('#detail-content').textContent,/Innings 1 to 5|Only runs/);
 await w.happyDOM.close();
});
test('quarter parity instruction explains Even including zero without requesting another line',async()=>{
 const t={...tip,id:'quarter-parity',league:'WNBA',market:'quarter_2:odd_even',model_scope:'quarter_2',side:'even',line:null,selection:'Even',market_display:'2nd Quarter - Odd/Even',stake_market:{...tip.stake_market,name:'2nd Quarter - Odd/Even'}};
 const {w,d}=await boot({recommendations:[t]});d.querySelector('[data-detail]').click();
 assert.match(d.querySelector('#detail-content').textContent,/combined points in the 2nd Quarter must be Even. Zero counts as Even/);
 assert.doesNotMatch(d.querySelector('#detail-content').textContent,/Innings 1 to 5/);
 await w.happyDOM.close();
});
test('development catalogue and player data do not claim production prop support',async()=>{
 const {w,d}=await boot({automation:{market_catalog:{checked_at:new Date().toISOString(),types:[{league:'NFL',market:'rushing yards',family:'player_props',scope:'regulation',tabs:[],dependencies:['player_model']}]},player_data:{checked_at:new Date().toISOString(),leagues:{NFL:{observations:100,players:10,latest_game_start:start,prop_recommendations_enabled:false}}}}});
 assert.match(d.querySelector('#expanded-catalog-table').textContent,/rushing yards/);
 assert.match(d.querySelector('#expanded-catalog-summary').textContent,/not today's betting instructions/);
 assert.match(d.querySelector('#player-data-table').textContent,/Validation and contract integration pending/);
 await w.happyDOM.close();
});
test('inning instruction keeps the exact Stake scope and never uses the final game score',async()=>{
 const t={...tip,id:'inning-total',league:'MLB',market:'innings_1_5:totals',model_scope:'innings_1_5',side:'under',line:3.5,selection:'Under 3.5',market_display:'Innings 1 to 5 - Total',stake_market:{...tip.stake_market,name:'Innings 1 to 5 - Total'}};
 const {w,d}=await boot({recommendations:[t],segment_models:{first5:{league:'MLB',scope:'innings_1_5',model_family:'negative_binomial',partitions:{train:1000,selection:300,test:400},test:{joint_log_score:4.1},baseline:{joint_log_score:4.2},validated_markets:['totals']}}});
 assert.match(d.querySelector('.tip-card').textContent,/Innings 1 to 5 - Total/);
 d.querySelector('[data-detail]').click();
 assert.match(d.querySelector('#detail-content').textContent,/Only runs in Innings 1 to 5 count toward Under 3.5/);
 assert.doesNotMatch(d.querySelector('#detail-content').textContent,/combined final score/);
 assert.match(d.querySelector('#segment-models-table').textContent,/negative_binomial/);
 await w.happyDOM.close();
});
test('inning draw selection explicitly distinguishes a draw win from a two-way push',async()=>{
 const t={...tip,id:'inning-draw',league:'MLB',market:'inning_1:three_way',model_scope:'inning_1',side:'draw',line:null,selection:'Draw',market_display:'1st Inning - 1x2',stake_market:{...tip.stake_market,name:'1st Inning - 1x2'}};
 const {w,d}=await boot({recommendations:[t]});
 d.querySelector('[data-detail]').click();
 assert.match(d.querySelector('#detail-content').textContent,/a tied score wins only the Draw selection/);
 assert.doesNotMatch(d.querySelector('#detail-content').textContent,/exact line null/);
 await w.happyDOM.close();
});
test('recorded inning selection shows the observed Stake tab from its current exact assessment',async()=>{
 const original={...tip,id:'inning-path',league:'MLB',market:'innings_1_5:totals',model_scope:'innings_1_5',side:'over',line:4.5,selection:'Over 4.5',market_display:'Innings 1 to 5 - Total',stake_market:{...tip.stake_market,name:'Innings 1 to 5 - Total',event_url:'https://stake.com/sports/baseball/usa/mlb/123-game'}};
 const current={...original,stake:0,stake_market:{...original.stake_market,tab:'Innings 1 to 5'}};
 const recorded={id:1,tip_id:original.id,stake:1,odds:2.27,status:'open',created_at:new Date().toISOString(),payload:{...original,tracking_source:'auto_assumed_placed'},instruction_review:tip.execution_review};
 const {w,d}=await boot({recommendations:[],evaluated_candidates:[current],tracked_bets:[recorded]});
 assert.match(d.querySelector('.stake-tab').textContent,/Stake tab: Innings 1 to 5/);
 assert.match(d.querySelector('.tip-card').textContent,/Select the Innings 1 to 5 tab → Innings 1 to 5 - Total → Over 4.5/);
 d.querySelector('[data-detail]').click();
 assert.match(d.querySelector('#detail-content').textContent,/Select the Innings 1 to 5 tab above the markets/);
 assert.match(d.querySelector('#detail-content').textContent,/Only runs in Innings 1 to 5 count/);
 assert.equal(original.stake_market.tab,undefined);
 await w.happyDOM.close();
});
test('navigation is not invented when an archived market has no group evidence',async()=>{
 const {w,d}=await boot();
 assert.equal(d.querySelector('.stake-tab'),null);
 d.querySelector('[data-detail]').click();
 assert.doesNotMatch(d.querySelector('#detail-content').textContent,/Select the Main tab/);
 await w.happyDOM.close();
});
test('league operations expose actual catalog coverage and unresolved repairs separately from picks',async()=>{
 const {w,d}=await boot({market_types:[{league:'NHL',market:'1st Period - Total',section:'standard',observed_outcomes:10,supported_outcomes:0,evaluated_outcomes:0,reasons:{unsupported_contract:10}}],automation:{operations:{checked_at:new Date().toISOString(),leagues:[{league:'NHL',state:'evaluating',games_48h:8,modeled_outcomes:120,qualified_families:4,research_source:'league-nhl',unresolved_gaps:1}],tracked_gaps:[{league:'NHL',code:'market_coverage',action:'market_research',status:'needs_implementation',job_status:'completed',evidence:{reason:'Period model missing'}}]}}});
 assert.match(d.querySelector('#league-operations-table').textContent,/NHL8/);
 assert.match(d.querySelector('#repair-table').textContent,/Period model missing/);
 assert.match(d.querySelector('#repair-table').textContent,/needs_implementation · completed/);
 assert.match(d.querySelector('#market-types-table').textContent,/1st Period - Total/);
 assert.equal(d.querySelector('#view-tips #market-types-table'),null);
 assert.equal(d.querySelectorAll('.tip-card').length,1);
 await w.happyDOM.close();
});
test('new instruction displays its priced dollar stake and preserves it in the detail',async()=>{
 const t={...tip,stake:3.87,stake_note:'Stress-adjusted unit sizing.'};
 const {w,d}=await boot({recommendations:[t]});
 assert.match(d.querySelector('.tip-metrics').textContent,/\$3.87/);
 d.querySelector('[data-detail]').click();
 assert.match(d.querySelector('#detail-content').textContent,/Suggested stake: \$3.87 USD/);
 assert.match(d.querySelector('#detail-content').textContent,/Stress-adjusted unit sizing/);
 await w.happyDOM.close();
});
test('model-search results show the selected recipe and actual promotion decision',async()=>{
 const {w,d}=await boot({learning_worker:{state:'monitoring',checked_at:new Date().toISOString(),jobs:[{kind:'model_search',league:'NBA',status:'completed',attempts:1,result:{selected_recipe:'regularized',promotion_gate:{promote:true},next_action:'Monitor prospective outcomes.'}}]}});
 assert.match(d.querySelector('#worker-table').textContent,/Model hypothesis search/);
 assert.match(d.querySelector('#worker-table').textContent,/regularized · Promoted/);
 await w.happyDOM.close();
});
test('learning view distinguishes failed retries and rejected experiments from promotion',async()=>{
 const {w,d}=await boot({learning_worker:{state:'monitoring',checked_at:new Date().toISOString(),jobs:[{kind:'controlled_retrain',league:'NBA',status:'completed',attempts:1,result:{promotion_gate:{promote:false,reason:'Candidate rejected: no improvement'}}},{kind:'controlled_retrain',league:'NFL',status:'retry',attempts:2,last_error:'Source unavailable'}]}});
 assert.match(d.querySelector('#worker-summary').textContent,/Training runs separately/);
 assert.match(d.querySelector('#worker-table').textContent,/Candidate rejected: no improvement/);
 assert.match(d.querySelector('#worker-table').textContent,/Retry scheduled/);
 assert.match(d.querySelector('#worker-table').textContent,/Source unavailable/);
 await w.happyDOM.close();
});
test('main page contains instructions while unfunded research is in its own view',async()=>{
 const {w,d}=await boot({alternatives:[{...tip,id:'research',stake:0}]});
 assert.equal(d.querySelector('#view-tips #alternatives-section'),null);
 assert.equal(d.querySelector('#view-tips #evaluated-table'),null);
 assert.equal(d.querySelector('#view-research').hidden,true);
 d.querySelector('[data-view="research"]').click();
 assert.equal(d.querySelector('#view-tips').hidden,true);
 assert.equal(d.querySelector('#view-research').hidden,false);
 assert.match(d.querySelector('#view-research').textContent,/No bet recommended/);
 await w.happyDOM.close();
});

test('additional team-total instruction identifies only the named team',async()=>{
 const t={...tip,id:'team-total',market:'away_total',side:'under',line:91.5,selection:'Under 91.5',market_display:'Away Total (Incl. Overtime)',is_additional:true,tracking_source:'auto_assumed_placed'};
 const recorded={id:2,tip_id:t.id,stake:1,odds:2,status:'open',created_at:new Date().toISOString(),payload:t,instruction_review:tip.execution_review};
 const {w,d}=await boot({recommendations:[],evaluated_candidates:[{...t,stake:0}],tracked_bets:[recorded]});
 assert.match(d.querySelector('.tip-card').textContent,/Additional Bet/);
 assert.match(d.querySelector('.tip-card').textContent,/STAKE TO PLACE · USDBet \$1.00/);
 d.querySelector('[data-detail]').click();
 assert.match(d.querySelector('#detail-content').textContent,/Only Away must score under 91.5 points/);
 assert.doesNotMatch(d.querySelector('#detail-content').textContent,/combined final score/);
 await w.happyDOM.close();
});
const tip={execution_review:{actionable:true,quote_at:new Date().toISOString(),valid_until:start},decision_guard:{passed:true},id:'test',rank:1,league:'NBA',game_id:'g',home:'Home',away:'Away',start,valid_until:start,market:'moneyline',market_display:'Winner (Incl. Overtime)',stake_market:{name:'Winner (Incl. Overtime)',league_url:'https://stake.com/sports/basketball/usa/nba',availability_verified:true,availability_note:'Exact current Stake offer verified.'},selection:'<img src=x onerror=alert(1)>',probability:.65,minimum_odds:1.801,reference_odds:2,reference_price:.5,reference_fee_per_share:0,reference_minimum_notional:5,reference_platform:'Stake.com',reference_url:'https://example.com',source_url:'https://example.com',quote_at:new Date().toISOString(),stake:1,stress_roi:.1,expected_roi:.2,label:'Estimated Value',reasons:['Test reason'],warnings:[],game_context:{},model_version:'test'};
async function boot(overrides={}){
 const w=new Window({url:'http://127.0.0.1:8765',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableIframePageLoading:true}});
 w.document.write(html);const writes=[];const marks={...overrides.placement_marks};
 w.testTimers=[];w.setInterval=(callback,ms)=>{w.testTimers.push({callback,ms});return 0};w.EventSource=class{addEventListener(name,callback){w.testUpdate=callback}};
 w.fetch=async(path,options)=>{
  if(options?.method&&options.method!=='GET')writes.push([path,JSON.parse(options.body||'{}')]);
  if(path.endsWith('/checklist')&&options?.method==='PUT'){
   const id=decodeURIComponent(path.split('/')[3]);const placed=JSON.parse(options.body).placed;
   if(placed)marks[id]=new Date().toISOString();else delete marks[id];
   return {ok:true,json:async()=>({tip_id:id,placed,placed_at:marks[id]||null})};
  }
  const result=path==='/api/dashboard'?{version:1,recommendations:[tip],settings:{bankroll:null,daily_risk_limit:null,daily_pick_target:15,max_bet:5,unit_stake:1,minimum_roi:.03,refresh_seconds:60,monthly_system_cost:null},engine:{running:false,stage:'Updated'},status:{mode:'research'},games:[],models:{},performance:{prospective:[]}}:path==='/api/bets'?{bets:[],performance:{actual_bets:{count:0,pnl:0,settled:0},monthly_system_cost:null,net_after_system_cost:null}}:path.includes('/quote')?{expired:false,expected_roi:.2,stress_roi:.1,stake:1,note:'Test note'}:{};
  return {ok:true,json:async()=>structuredClone(path==='/api/dashboard'?{...result,...overrides,placement_marks:marks}:result)};
 };
 w.eval(script);await new Promise(r=>setTimeout(r,10));return {w,writes,d:w.document};
}
test('dashboard renders model output and escapes source text',async()=>{
 const {w,d}=await boot();assert.equal(d.querySelectorAll('.tip-card').length,1);assert.equal(d.querySelector('.selection img'),null);assert.match(d.querySelector('.selection').textContent,/<img/);assert.equal(d.querySelector('#tip-count').textContent,'1');assert.equal(d.documentElement.lang,'en');assert.match(d.querySelector('.tip-card').textContent,/1\.81/);await w.happyDOM.close();
});
test('league filter and settings navigation work',async()=>{
 const {w,d}=await boot();d.querySelector('#league-filter').value='MLB';d.querySelector('#league-filter').dispatchEvent(new w.Event('change'));assert.equal(d.querySelectorAll('.tip-card').length,0);d.querySelector('[data-view="settings"]').click();assert.equal(d.querySelector('#view-settings').hidden,false);assert.equal(d.querySelector('[name="monthly_system_cost"]').value,'');await w.happyDOM.close();
});
test('price check form evaluates without submitting an actual bet',async()=>{
 const {w,d,writes}=await boot();d.querySelector('[data-detail]').click();const f=d.querySelector('#quote-check-form');assert.ok(f);assert.match(d.querySelector('#detail-content').textContent,/Exact current Stake offer verified/);assert.equal(d.querySelector('#bet-form [name=odds]').value,'');assert.match(d.querySelector('#detail-content').textContent,/Find This Bet on Stake/);assert.match(d.querySelector('#detail-content').textContent,/Winner \(Incl\. Overtime\)/);f.elements.namedItem('odds').value='2';f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setTimeout(r,10));assert.equal(writes[0]?.[0],'/api/tips/test/quote');assert.match(d.querySelector('#quote-result').textContent,/Stake: \$1.00/);assert.equal(writes.some(([url])=>url==='/api/bets'),false);await w.happyDOM.close();
});
test('cost view does not invent paid results or known costs',async()=>{
 const {w,d}=await boot();d.querySelector('[data-view="ledger"]').click();await new Promise(r=>setTimeout(r,10));assert.match(d.querySelector('#performance-stats').textContent,/Not Set/);assert.match(d.querySelector('#ledger-table').textContent,/No data/);await w.happyDOM.close();
});

test('accountability view reports losses and incomplete coverage without a profit claim',async()=>{
 const {w,d}=await boot({performance:{system_decisions:{count:4,settled:3,won:1,lost:2,pushes:0,pnl:-1,net_after_system_cost:null}},status:{governance:{contract:{version:'test'},guard:{passed:2,checked:3,rejected:1},market_coverage:{NBA:{observed_outcomes:20,supported_outcomes:4,evaluated_outcomes:2,unsupported_outcomes:16,unmatched_outcomes:0}},learning:{tasks:[{priority:1,task:'Collect evidence',reason:'No profit backtest'}]}}}});
 assert.match(d.querySelector('#governance-summary').textContent,/including losses/);
 assert.match(d.querySelector('#ownership-table').textContent,/\$-1.00/);
 assert.match(d.querySelector('#ownership-table').textContent,/Unknown/);
 assert.match(d.querySelector('#coverage-table').textContent,/16/);
 assert.match(d.querySelector('#learning-table').textContent,/No profit backtest/);
 await w.happyDOM.close();
});

test('blank daily cap saves null and daily progress distinguishes target from issued picks',async()=>{
 const {w,d,writes}=await boot({status:{daily_progress:{issued:9,target:15,shortfall:6,date:'2026-09-28',timezone:'Europe/Budapest'}}});
 assert.match(d.querySelector('#notice').textContent,/9\/15 distinct funded/);
 assert.match(d.querySelector('#notice').textContent,/No fixed daily stake cap/);
 d.querySelector('[data-view="settings"]').click();
 const f=d.querySelector('#settings-form');assert.equal(f.elements.namedItem('daily_risk_limit').value,'');
 f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
 await new Promise(r=>setTimeout(r,10));
 const body=writes.find(([url])=>url==='/api/settings')?.[1];
 assert.equal(body.daily_risk_limit,null);assert.equal(body.daily_pick_target,15);assert.equal(body.max_bet,5);assert.equal(body.refresh_seconds,60);
 await w.happyDOM.close();
});

test('all selected alternatives are visible, open details and follow league filters',async()=>{
 const alternative={...tip,id:'alt',league:'NHL',selection:'Under 6.5',market:'totals',side:'under',line:6.5,stake:0};
 const expired={...alternative,id:'expired',valid_until:new Date(Date.now()-1000).toISOString()};
 const {w,d,writes}=await boot({alternatives:[alternative,expired]});
 assert.equal(d.querySelector('#alternative-count').textContent,'1');
 assert.equal(d.querySelectorAll('#alternatives-table tbody tr').length,1);
 d.querySelector('[data-detail="alt"]').click();
 assert.match(d.querySelector('#detail-content').textContent,/Under 6.5/);
 const f=d.querySelector('#quote-check-form');f.elements.namedItem('odds').value='2';
 f.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
 await new Promise(r=>setTimeout(r,10));assert.equal(writes[0][0],'/api/tips/alt/quote');
 d.querySelector('#league-filter').value='NBA';d.querySelector('#league-filter').dispatchEvent(new w.Event('change'));
 assert.equal(d.querySelector('#alternatives-section').hidden,true);
 await w.happyDOM.close();
});

test('auto-tracked picks show original stakes without asking for another confirmation',async()=>{
 const recorded={id:1,tip_id:tip.id,stake:1,odds:1.9,status:'open',created_at:new Date().toISOString(),payload:{...tip,tracking_source:'auto_assumed_placed'},instruction_review:tip.execution_review};
 const held={...tip,stake:0,tracked_bet:{id:1,stake:1,odds:1.9,status:'open',created_at:recorded.created_at}};
 const {w,d,writes}=await boot({recommendations:[held],tracked_bets:[recorded],settings:{auto_track_recommendations:true,minimum_roi:.03}});
 assert.equal(d.querySelector('#tracked-count').textContent,'1');
 assert.match(d.querySelector('#tracked-table').textContent,/Auto-tracked/);
 assert.match(d.querySelector('.tip-metrics').textContent,/Bet \$1.00/);
 assert.equal(d.querySelector('[name=auto_track_recommendations]').checked,true);
 d.querySelector('[data-detail]').click();
 assert.equal(d.querySelector('#record-bet').hidden,true);
 assert.match(d.querySelector('#detail-content').textContent,/Bet \$1.00 once/);
 assert.equal(writes.length,0);
 await w.happyDOM.close();
});

test('chronological checklist moves placed picks to Tracker and keeps new actionable picks on the main page',async()=>{
 const at=h=>new Date(Date.now()+h*3600000).toISOString();
 const rows=[{...tip,id:'later',start:at(3)},{...tip,id:'early',start:at(1)},{...tip,id:'middle',start:at(2)}];
 const {w,d,writes}=await boot({recommendations:rows});
 const order=()=>[...d.querySelectorAll('.tip-card')].map(el=>el.dataset.tipId);
 assert.equal(d.querySelector('#sort-filter').value,'start');
 assert.deepEqual(order(),['early','middle','later']);
 const mark=d.querySelector('[data-placement="early"]');mark.checked=true;mark.dispatchEvent(new w.Event('change'));
 await new Promise(r=>setTimeout(r,10));
 assert.deepEqual(order(),['middle','later']);
 assert.deepEqual(writes[0],['/api/tips/early/checklist',{placed:true}]);
 assert.equal(d.querySelector('#tips-grid [data-tip-id="early"]'),null);assert.ok(d.querySelector('#view-ledger [data-placement="early"]'));
 rows.push({...tip,id:'new-pick',start:at(4)});
 await w.testUpdate();
 assert.deepEqual(order(),['middle','later','new-pick']);
 assert.equal(d.querySelector('[data-placement="early"]').checked,true);
 const undo=d.querySelector('[data-placement="early"]');undo.checked=false;undo.dispatchEvent(new w.Event('change'));
 await new Promise(r=>setTimeout(r,10));
 assert.deepEqual(order(),['early','middle','later','new-pick']);
 assert.equal(writes.some(([url])=>url==='/api/bets'),false);
 await w.happyDOM.close();
});

test('an older dashboard response cannot undo a saved placement or removal',async()=>{
 const {w,d,writes}=await boot({recommendations:[{...tip,id:'early'}, {...tip,id:'later',start:new Date(Date.now()+7200000).toISOString()}]});
 const originalFetch=w.fetch;
 const order=()=>[...d.querySelectorAll('.tip-card')].map(el=>el.dataset.tipId);
 for(const placed of [true,false]){
  const snapshot=await (await originalFetch('/api/dashboard')).json();
  let release;const delayed=new Promise(resolve=>release=resolve);
  w.fetch=(path,options)=>path==='/api/dashboard'?delayed.then(()=>({ok:true,json:async()=>snapshot})):originalFetch(path,options);
  const refresh=w.testUpdate();
  const input=d.querySelector('[data-placement="early"]');input.checked=placed;
  await input.onchange();
  assert.equal(d.querySelector('[data-placement="early"]').checked,placed);
  release();await refresh;
  assert.equal(d.querySelector('[data-placement="early"]').checked,placed,'pre-save snapshot must not undo the acknowledged click');
  assert.deepEqual(order(),placed?['later']:['early','later']);
 }
 w.fetch=originalFetch;await w.testUpdate();
 assert.equal(d.querySelector('[data-placement="early"]').checked,false);
 assert.equal(writes.length,2);
 assert.equal(writes.some(([url])=>url==='/api/bets'),false);
 await w.happyDOM.close();
});

test('pending placement survives a rerender and disables every copy until saved',async()=>{
 const recorded={id:1,tip_id:tip.id,stake:1,odds:1.9,status:'open',created_at:new Date().toISOString(),payload:tip};
 const {w,d,writes}=await boot({tracked_bets:[recorded]});
 const originalFetch=w.fetch;let release;const delayed=new Promise(resolve=>release=resolve);
 w.fetch=(path,options)=>path.endsWith('/checklist')?delayed.then(()=>originalFetch(path,options)):originalFetch(path,options);
 const input=d.querySelector('[data-placement="test"]');input.checked=true;const saved=input.onchange();
 await w.testUpdate();
 const controls=[...d.querySelectorAll('[data-placement="test"]')];
 assert.ok(controls.length>=2);
 assert.ok(controls.every(el=>el.checked&&el.disabled),'refreshed controls must retain the pending state');
 assert.match(d.querySelector('.placement-toggle').textContent,/Saving/);
 release();await saved;
 assert.ok([...d.querySelectorAll('[data-placement="test"]')].every(el=>el.checked&&!el.disabled));
 assert.equal(writes.length,1);
 await w.happyDOM.close();
});

test('queued refresh applies newer checklist changes from another device after the older response',async()=>{
 const {w,d}=await boot();let firstRelease;const first=new Promise(resolve=>firstRelease=resolve);
 const originalFetch=w.fetch;const snapshot=await(await originalFetch('/api/dashboard')).json();let calls=0;
 w.fetch=()=>++calls===1?first.then(()=>({ok:true,json:async()=>snapshot})):Promise.resolve({ok:true,json:async()=>({...snapshot,placement_marks:{test:new Date().toISOString()}})});
 const older=w.testUpdate(),newer=w.testUpdate();
 await Promise.resolve();assert.equal(calls,1,'the newer snapshot waits for the in-flight request');
 firstRelease();await older;await newer;
 assert.equal(d.querySelector('[data-placement="test"]').checked,true);
 assert.equal(d.querySelector('[data-placement="test"]').checked,true);
 // A later device-side removal must still synchronize normally.
 w.fetch=originalFetch;await w.testUpdate();
 assert.equal(d.querySelector('[data-placement="test"]').checked,false);
 await w.happyDOM.close();
});

test('a failed placement save restores the current controls and permits one retry',async()=>{
 const {w,d,writes}=await boot();const originalFetch=w.fetch;
 w.fetch=(path,options)=>path.endsWith('/checklist')?Promise.resolve({ok:false,json:async()=>({detail:'Save failed'})}):originalFetch(path,options);
 const input=d.querySelector('[data-placement="test"]');input.checked=true;await input.onchange();
 const restored=d.querySelector('[data-placement="test"]');
 assert.equal(restored.checked,false);assert.equal(restored.disabled,false);
 assert.match(d.querySelector('#toast').textContent,/Save failed/);
 w.fetch=originalFetch;restored.checked=true;await restored.onchange();
 assert.equal(d.querySelector('[data-placement="test"]').checked,true);
 assert.equal(writes.length,1);
 await w.happyDOM.close();
});

test('unfunded candidates never look like executable recommendations',async()=>{
 const {w,d}=await boot({recommendations:[tip,{...tip,id:'watch',stake:0,selection:'Watch candidate'}]});
 assert.equal(d.querySelectorAll('.tip-card').length,1);
 assert.equal(d.querySelector('[data-tip-id="watch"]'),null);
 assert.match(d.querySelector('#alternatives-table').textContent,/Watch candidate/);
 assert.match(d.querySelector('#alternatives-table').textContent,/No bet recommended/);
 await w.happyDOM.close();
});

test('recorded selections survive changed lines and retain original odds and probabilities',async()=>{
 const recorded={id:1,tip_id:'original',stake:1,odds:1.78,status:'open',created_at:new Date().toISOString(),payload:{...tip,id:'original',market:'totals',side:'under',line:184.5,selection:'Under 184.5',probability:.7,tracking_source:'user_reported_placed'}};
 const changed={...tip,id:'changed',market:'totals',line:182.5,selection:'Under 182.5',stake:0,covered_by:[{selection:'Under 184.5'}]};
 const {w,d}=await boot({recommendations:[changed],tracked_bets:[recorded],placement_marks:{original:new Date().toISOString()}});
 assert.equal(d.querySelector('#tips-grid [data-tip-id="original"]'),null);
 const history=d.querySelector('#history-table');assert.match(history.textContent,/Under 184.5/);assert.match(history.textContent,/1.78/);assert.match(history.textContent,/70.0%/);
 assert.doesNotMatch(history.textContent,/undefined/);
 assert.match(d.querySelector('#alternatives-table').textContent,/Already covered by Under 184.5/);
 assert.match(d.querySelector('#stats').textContent,/Ready to Bet0/);
 assert.equal(d.querySelector('#view-tips #tracked-section'),null);assert.equal(d.querySelector('#tracked-section').open,false);assert.equal(d.querySelector('#archive-section').open,false);
 await w.happyDOM.close();
});

test('all modeled outcomes remain inspectable without being counted as extra bets',async()=>{
 const rejected={...tip,id:'other-side',stake:0,selection:'Other Side',allocation_code:'not_selected',allocation_reason:'Another selection offers higher modeled value.'};
 const {w,d}=await boot({evaluated_candidates:[tip,rejected]});
 assert.equal(d.querySelectorAll('.tip-card').length,1);
 assert.equal(d.querySelector('#evaluated-count').textContent,'2');
 assert.match(d.querySelector('#evaluated-table').textContent,/Other Side/);
 d.querySelector('#evaluated-table [data-detail="other-side"]').click();
 assert.match(d.querySelector('#detail-content').textContent,/higher modeled value/);
 await w.happyDOM.close();
});

test('rolling 24h filters issued picks and coverage without changing daily issuance',async()=>{
 const at=h=>new Date(Date.now()+h*3600000).toISOString();
 const near={...tip,id:'near',start:at(2)},far={...tip,id:'far',start:at(30)};
 const row={league:'NBA',games:1,observed_outcomes:100,active_standard_outcomes:40,modeled_outcomes:6,value_qualified_outcomes:2,qualified_families:1,swish_priced_unverified:60,source_state:'observed',odds_loaded_games:1};
 const win=hours=>({start:at(0),end:at(hours),leagues:[{...row,games:hours===24?1:2}]});
 const {w,d}=await boot({recommendations:[near,far],opportunities:{generated_at:at(0),windows:{24:win(24),48:win(48)}},status:{daily_progress:{issued:1,target:15}}});
 assert.equal(d.querySelectorAll('.tip-card').length,2);
 d.querySelector('[data-horizon="24"]').click();
 assert.equal(d.querySelectorAll('.tip-card').length,1);
 assert.match(d.querySelector('#window-coverage').textContent,/1 current betting instructions/);
 assert.match(d.querySelector('#opportunity-summary').textContent,/100 observed feed outcomes/);
 assert.match(d.querySelector('#stats').textContent,/1 \/ 15/);
 await w.happyDOM.close();
});

test('Bet on Stake opens the recorded fixture and does not mark or submit a bet',async()=>{
 const url='https://stake.com/sports/basketball/usa/nba/123-home-away';
 const t={...tip,stake_market:{...tip.stake_market,event_url:url,bet_link_scope:'event'}};
 const {w,d,writes}=await boot({recommendations:[t]});
 const button=d.querySelector('.tip-card .stake-bet-button');assert.ok(button);
 assert.equal(button.href,url);assert.equal(button.target,'_blank');assert.match(button.rel,/noopener/);
 assert.match(d.querySelector('.tip-card').textContent,/bet slip is not prefilled/);
 assert.equal(writes.length,0);
 await w.happyDOM.close();
});

test('automation shows real recovery and abstract-only research status',async()=>{
 const {w,d}=await boot({automation:{supervisor:{state:'active',services:{engine:{state:'healthy'}}},research:{state:'active',indexed_papers:1,sources:{sports:{}},papers:[{title:'Model paper',url:'https://arxiv.org/abs/2609.12345v1',tags:['calibration'],reading_scope:'title_and_abstract_only',implementation_state:'not_implemented'}]}}});
 assert.match(d.querySelector('#automation-summary').textContent,/engine: healthy/);
 assert.match(d.querySelector('#research-summary').textContent,/1 indexed papers/);
 assert.match(d.querySelector('#research-papers').textContent,/not_implemented/);
 await w.happyDOM.close();
});

test('unmarked old instruction loses its executable stake when current value falls',async()=>{
 const t={...tip,selection:'Home',tracking_source:'auto_assumed_placed'};
 const b={id:1,tip_id:t.id,stake:1,odds:2,status:'open',created_at:new Date().toISOString(),payload:t,instruction_review:{state:'price_below_threshold',actionable:false,reason:'Current odds no longer meet the model threshold.'}};
 const {w,d}=await boot({recommendations:[{...tip,stake:0}],tracked_bets:[b]});
 assert.equal(d.querySelector('.tip-card'),null);assert.doesNotMatch(d.querySelector('#view-tips').textContent,/Recheck Required/);
 assert.match(d.querySelector('#history-table').textContent,/2.00/);assert.match(d.querySelector('#history-table').textContent,/Not a current instruction/);
 assert.match(d.querySelector('#stats').textContent,/Total Stake\$0.00/);
 await w.happyDOM.close();
});

test('Today does not revive a pregame instruction after the match starts',async()=>{
 const t={...tip,start:new Date(Date.now()-60000).toISOString()};
 const b={id:1,tip_id:t.id,stake:1,odds:2,status:'open',created_at:new Date().toISOString(),payload:t};
 const {w,d}=await boot({recommendations:[],tracked_bets:[b]});
 d.querySelector('[data-horizon="today"]').click();
 assert.equal(d.querySelectorAll('.tip-card').length,0);
 await w.happyDOM.close();
});

test('delivery view distinguishes candidate readiness and overdue production checkpoints',async()=>{
 const {w,d}=await boot({automation:{development:{plan_version:'test',state:'attention_required',fully_ready:false,cadence_seconds:3600,worker:{state:'running',task_id:'D01'},checkpoint_rule:'Dates are progress reviews, not guaranteed delivery.',milestones:[{id:'M02',title:'Inputs',checkpoint_at:new Date().toISOString(),checkpoint_due:true,state:'validation_pending',acceptance:'Live evidence required'}],tasks:[{id:'D01',title:'Point-in-time inputs',milestone:'M02',state:'candidate_ready',attempts:1,summary:'Tests passed'}],recent_runs:[{started_at:new Date().toISOString(),task_id:'D01',state:'retry',error:'Review rejected'}]}}});
 assert.match(d.querySelector('#development-summary').textContent,/not yet verified/);
 assert.match(d.querySelector('#development-summary').textContent,/betting model activation requires separately verified release evidence/);
 assert.match(d.querySelector('#development-milestones').textContent,/Checkpoint overdue/);
 assert.match(d.querySelector('#development-tasks').textContent,/candidate ready/);
 assert.match(d.querySelector('#development-runs').textContent,/Review rejected/);
 await w.happyDOM.close();
});


test('main instructions fail closed without a current server review',async()=>{
 for(const patch of [{execution_review:null},{execution_review:{actionable:false}},{decision_guard:{passed:false}},{stake_market:{...tip.stake_market,availability_verified:false}},{reference_platform:'Historical Model'},{stress_roi:NaN},{stake:0.99},{stake:5.01}]){
  const {w,d}=await boot({recommendations:[{...tip,...patch}]});
  assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);
  assert.match(d.querySelector('#stats').textContent,/Total Stake\$0.00/);
  assert.doesNotMatch(d.querySelector('#view-tips').textContent,/Recheck Required/);
  await w.happyDOM.close();
 }
});
test('main instructions enforce quote lifetime even when a review claims actionable',async()=>{
 for(const patch of [{quote_at:new Date(Date.now()-301000).toISOString()},{quote_at:new Date(Date.now()+60000).toISOString()},{valid_until:new Date(Date.now()-1).toISOString()},{withdrawn_at:new Date().toISOString()},{execution_review:{...tip.execution_review,quote_at:new Date(Date.now()-301000).toISOString()}},{execution_review:{...tip.execution_review,valid_until:'invalid'}}]){
  const {w,d}=await boot({recommendations:[{...tip,...patch}]});assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);await w.happyDOM.close();
 }
});
test('a clean pending instruction uses current estimates and its original one-time USD stake',async()=>{
 const original={...tip,selection:'Home',probability:.65,reference_odds:2,tracking_source:'auto_assumed_placed'};
 const current={...tip,selection:'Home',probability:.72,reference_odds:2.2,minimum_odds:1.55,stake:0};
 const record={id:1,tip_id:tip.id,stake:3.25,odds:2,status:'open',created_at:new Date().toISOString(),payload:original,instruction_review:tip.execution_review};
 const {w,d}=await boot({recommendations:[],evaluated_candidates:[current],tracked_bets:[record]});
 const card=d.querySelector('#tips-grid .tip-card');assert.ok(card);assert.match(card.textContent,/72.0%/);assert.match(card.textContent,/STAKE ODDS2.20/);assert.match(card.textContent,/Bet \$3.25/);assert.match(card.textContent,/Minimum odds: 1.55/);assert.doesNotMatch(card.textContent,/65.0%|Recheck Required|Recorded odds/);
 const history=d.querySelector('#history-table');assert.match(history.textContent,/65.0%/);assert.match(history.textContent,/2.00/);
 card.querySelector('[data-detail]').click();assert.match(d.querySelector('#detail-content').textContent,/72.0%/);assert.doesNotMatch(d.querySelector('#detail-content').textContent,/Already covered by/);
 assert.match(d.querySelector('#stats').textContent,/Total Stake\$3.25/);
 await w.happyDOM.close();
});
test('multiple tickets and reported execution never become another main instruction',async()=>{
 for(const sources of [['auto_assumed_placed','auto_assumed_placed'],['auto_assumed_placed','user_reported_placed'],['user_reported_placed']]){
  const records=sources.map((tracking_source,i)=>({id:i+1,tip_id:tip.id,stake:1,odds:2,status:'open',created_at:new Date().toISOString(),payload:{...tip,tracking_source},instruction_review:tip.execution_review}));
  const {w,d}=await boot({recommendations:[],evaluated_candidates:[{...tip,stake:0}],tracked_bets:records});
  assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);assert.equal(d.querySelectorAll('#tracked-table tbody tr').length,sources.length);assert.ok(d.querySelector('#view-ledger #tracked-table'));assert.equal(d.querySelector('#view-tips #tracked-table'),null);await w.happyDOM.close();
 }
});
test('a rejected recorded review cannot be bypassed by a positive current assessment',async()=>{
 const b={id:1,tip_id:tip.id,stake:2,odds:2,status:'open',created_at:new Date().toISOString(),payload:{...tip,tracking_source:'auto_assumed_placed'},instruction_review:{actionable:false,state:'stake_reassessment_required'}};
 const {w,d}=await boot({recommendations:[],evaluated_candidates:[{...tip,stake:0}],tracked_bets:[b]});
 assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);assert.match(d.querySelector('#history-table').textContent,/\$2.00/);await w.happyDOM.close();
});
test('connection loss hides executable instructions until the next successful update',async()=>{
 const {w,d}=await boot();assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,1);
 const original=w.fetch;w.fetch=async()=>{throw Error('offline')};await w.testUpdate();assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);assert.match(d.querySelector('#notice').textContent,/reconnecting/);
 w.fetch=original;await w.testUpdate();assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,1);await w.happyDOM.close();
});

test('same-selection model and quote changes close the old instruction detail',async()=>{
 const rows=[{...tip}];const {w,d}=await boot({recommendations:rows});
 d.querySelector('#tips-grid [data-detail]').click();assert.equal(d.querySelector('#detail-dialog').open,true);
 rows[0]={...tip,probability:.81,reference_odds:2.2,minimum_odds:1.4};await w.testUpdate();
 assert.match(d.querySelector('#tips-grid').textContent,/81.0%/);assert.equal(d.querySelector('#detail-dialog').open,false);
 d.querySelector('#tips-grid [data-detail]').click();assert.match(d.querySelector('#detail-content').textContent,/81.0%/);assert.match(d.querySelector('#detail-content').textContent,/1.40/);
 await w.happyDOM.close();
});

test('probability and rank sorting use current reviewed terms of pending instructions',async()=>{
 const rows=[{...tip,id:'a',probability:.62,rank:2,stake:0},{...tip,id:'b',probability:.8,rank:1,stake:0}];
 const records=rows.map((t,i)=>({id:i+1,tip_id:t.id,stake:1,odds:2,status:'open',created_at:new Date().toISOString(),payload:{...t,probability:i?.6:.9,rank:i?2:1,tracking_source:'auto_assumed_placed'},instruction_review:tip.execution_review}));
 const {w,d}=await boot({recommendations:[],evaluated_candidates:rows,tracked_bets:records});
 for(const value of ['probability','rank']){const filter=d.querySelector('#sort-filter');filter.value=value;filter.dispatchEvent(new w.Event('change'));assert.deepEqual([...d.querySelectorAll('#tips-grid .tip-card')].map(el=>el.dataset.tipId),['b','a']);}
 await w.happyDOM.close();
});

test('pending instructions navigate to the currently verified Stake fixture URL',async()=>{
 const old='https://stake.com/sports/basketball/usa/nba/old-event';
 const current='https://stake.com/sports/basketball/usa/nba/current-event';
 const b={id:1,tip_id:tip.id,stake:1,odds:2,status:'open',created_at:new Date().toISOString(),payload:{...tip,tracking_source:'auto_assumed_placed',stake_market:{...tip.stake_market,event_url:old}},instruction_review:tip.execution_review};
 const {w,d}=await boot({recommendations:[],tracked_bets:[b],evaluated_candidates:[{...tip,stake:0,stake_market:{...tip.stake_market,event_url:current}}]});
 assert.equal(d.querySelector('#tips-grid .stake-bet-button').href,current);assert.equal(b.payload.stake_market.event_url,old);
 await w.happyDOM.close();
});

test('expired detail navigation is blocked even while dashboard refresh hangs',async()=>{
 const url='https://stake.com/sports/basketball/usa/nba/current-event';
 const {w,d,writes}=await boot({recommendations:[{...tip,stake_market:{...tip.stake_market,event_url:url}}]});
 d.querySelector('#tips-grid [data-detail]').click();const link=d.querySelector('#detail-content .stake-bet-button');assert.ok(link.onclick);
 const OriginalDate=w.Date;const later=OriginalDate.now()+301000;
 w.Date=class extends OriginalDate{constructor(...args){super(...(args.length?args:[later]))}static now(){return later}};
 const event=new w.MouseEvent('click',{bubbles:true,cancelable:true});link.dispatchEvent(event);
 assert.equal(event.defaultPrevented,true);assert.equal(d.querySelector('#detail-dialog').open,false);assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);assert.deepEqual(writes,[]);
 await w.happyDOM.close();
});

test('local expiry removes instructions and closes details without a network response',async()=>{
 const {w,d}=await boot();d.querySelector('#tips-grid [data-detail]').click();
 const OriginalDate=w.Date;const later=OriginalDate.now()+301000;
 w.Date=class extends OriginalDate{constructor(...args){super(...(args.length?args:[later]))}static now(){return later}};
 w.testTimers.find(t=>t.ms===1000).callback();
 assert.equal(d.querySelectorAll('#tips-grid .tip-card').length,0);assert.equal(d.querySelector('#detail-dialog').open,false);
 await w.happyDOM.close();
});

test('manual price checks display a failed engine review without a hypothetical stake',async()=>{
 const {w,d,writes}=await boot();d.querySelector('#tips-grid [data-detail]').click();const original=w.fetch;
 w.fetch=(path,options)=>path.endsWith('/quote')?Promise.resolve({ok:true,json:async()=>({review_failed:true,stake:0,note:'The current sports inputs need a fresh readiness and model review.'})}):original(path,options);
 const form=d.querySelector('#quote-check-form');form.elements.namedItem('odds').value='9';await form.onsubmit({preventDefault(){},target:form});
 assert.match(d.querySelector('#quote-result').textContent,/current sports inputs/);assert.doesNotMatch(d.querySelector('#quote-result').textContent,/Stake:/);assert.equal(writes.some(([url])=>url==='/api/bets'),false);
 await w.happyDOM.close();
});

test('Tracker detail retains the original snapshot without issuing a current instruction',async()=>{
 const url='https://stake.com/sports/basketball/usa/nba/recorded-event';
 const b={id:1,tip_id:tip.id,stake:1,odds:2,status:'open',created_at:new Date().toISOString(),payload:{...tip,tracking_source:'auto_assumed_placed',stake_market:{...tip.stake_market,event_url:url}},instruction_review:tip.execution_review};
 const {w,d}=await boot({recommendations:[],tracked_bets:[b],evaluated_candidates:[{...tip,probability:.8,stake:0}]});
 d.querySelector('[data-view="ledger"]').click();await new Promise(r=>setTimeout(r,10));d.querySelector('#history-table [data-detail]').click();
 assert.match(d.querySelector('#detail-content').textContent,/65.0%/);assert.match(d.querySelector('#detail-content').textContent,/Recorded \$1.00/);assert.match(d.querySelector('#detail-content').textContent,/No additional bet is requested/);
 assert.doesNotMatch(d.querySelector('#detail-content').textContent,/Bet \$1.00 once/);assert.match(d.querySelector('#detail-content .stake-bet-button').textContent,/View on Stake/);
 await w.happyDOM.close();
});

 test('development heartbeat does not conceal stalled work or release ownership',async()=>{
 const {w,d,writes}=await boot({automation:{development:{plan_version:'recovery',state:'attention_required',worker:{state:'healthy'},progress:{state:'dispatcher_stalled',dispatch_stalled:true,attention_required:true,runnable_count:2,pending_tasks:[{task_id:'D15',progress:'runnable',owner:'candidate_developer',next_action:'Dispatch exact review repair'}],release_handoffs:[{task_id:'D08',run_id:'run-1',blocker_code:'file_not_allowed',owner:'root_release_coordinator',next_action:'Review exact passive utility',due_at:start}]}}}});
 assert.match(d.querySelector('#development-progress-summary').textContent,/Dispatcher stalled/);
 assert.match(d.querySelector('#development-blockers').textContent,/root_release_coordinator/);
 assert.match(d.querySelector('#development-blockers').textContent,/Dispatch exact review repair/);
 assert.match(d.querySelector('#development-blockers').textContent,/Review exact passive utility/);
 assert.equal(d.querySelector('#view-tips #development-blockers'),null);
 assert.deepEqual(writes,[]);await w.happyDOM.close();
 });

// Include refresh regressions in both CLI and trusted-controller DOM runs.
import './web_refresh_cases.mjs';
import './web_navigation_cases.mjs';
import './web_continuous_delivery_cases.mjs';
import './web_model_pick_sizing_cases.mjs';
import './web_all_tier_sizing_cases.mjs';

// Root-reviewed Statistics regressions; preserve every existing test above.
import './statistics.web.test.mjs';

test('changed upcoming records remain visible as read-only history without changing execution',async()=>{
 const make=(id,extra={},review={actionable:false,reason:'Original stake no longer supported <script>bad</script>'})=>({id,tip_id:id,stake:2,odds:1.8,status:'open',created_at:new Date().toISOString(),payload:{...tip,id,stake:2,reference_odds:1.8,tracking_source:'auto_assumed_placed',...extra},instruction_review:review});
 const rows=[make('changed'),make('started',{start:new Date(Date.now()-1000).toISOString()}),make('marked'),{...make('settled'),status:'won'},make('current',{}, {actionable:true})];
 const {w,d,writes}=await boot({recommendations:[],evaluated_candidates:[],tracked_bets:rows,placement_marks:{marked:start}});
 const panel=d.querySelector('#withdrawn-upcoming-section');assert.equal(panel.hidden,false);assert.equal(d.querySelector('#withdrawn-upcoming-count').textContent,'1');
 assert.match(panel.textContent,/Original stake no longer supported/);assert.match(panel.textContent,/\$2.00/);assert.match(panel.textContent,/1.80/);
 assert.equal(panel.querySelectorAll('a,button,input,script').length,0);assert.equal(d.querySelectorAll('.tip-card').length,0);assert.deepEqual(writes,[]);
 assert.match(d.querySelector('#history-table').textContent,/Recorded/);await w.happyDOM.close();
});
test('changed history separates original issuance from revised execution and latest observation',async()=>{
 const original={...tip,id:'changed-revision',stake:1.25,reference_odds:1.9,expected_roi:.14,created_at:new Date(Date.now()-60000).toISOString()};
 const record={id:4,tip_id:original.id,stake:3.5,odds:2.2,status:'open',created_at:original.created_at,payload:original,instruction_review:{actionable:false,reason:'Assessment changed'}};
 const current={...original,reference_odds:1.7,expected_roi:-.03,probability:.57};
 const {w,d,writes}=await boot({recommendations:[],tracked_bets:[record],evaluated_candidates:[current]});
 const panel=d.querySelector('#withdrawn-upcoming-section');assert.match(panel.textContent,/Odds: 1.90 · Stake: \$1.25/);assert.match(panel.textContent,/Observed odds: 1.70/);assert.match(panel.textContent,/14.0%/);assert.doesNotMatch(panel.textContent,/\$3.50/);assert.deepEqual(writes,[]);await w.happyDOM.close();
});
test('today issued changed selections stay in the Betting Picks card list',async()=>{
 const id='changed-pick';
 const changed={...tip,id,stake:1,reference_odds:2,created_at:new Date().toISOString(),tracking_source:'auto_assumed_placed'};
 const record={id:7,tip_id:id,stake:1,odds:2,status:'open',created_at:changed.created_at,payload:changed,instruction_review:{actionable:false,reason:'Model no longer supports'}};
 const {w,d,writes}=await boot({recommendations:[],evaluated_candidates:[],tracked_bets:[record],status:{daily_progress:{issuance_rows:[{tip_id:id,issued_at:new Date().toISOString(),start:tip.start}]}}});
 const card=d.querySelector('.changed-card');assert.ok(card);assert.match(card.textContent,/No bet anymore/);assert.match(card.textContent,/Model no longer supports/);assert.equal(card.querySelector('.stake-bet-button'),null);assert.deepEqual(writes,[]);await w.happyDOM.close();
});
