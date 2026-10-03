import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
const html=readFileSync('src/betting_engine/web/index.html','utf8'),script=readFileSync('src/betting_engine/web/app.js','utf8');
const stamp='2026-09-30T21:00:00Z';
const summary={count:6,settled:5,open:1,won:2,lost:1,pushes:1,voids:0,manual:1,unknown:0,games:4,settled_games:3,stake:12,open_stake:2,settled_stake:10,payout:11.5,pnl:1.5,roi:.15,hit_rate:2/3,money_complete:true,calibration:{observations:2,independent_games:2,brier:.21,log_loss:.5}};
function report(){return {as_of:stamp,filters:{excluded_unknown_match_dates:{tickets:0,decisions:0}},available_leagues:['MLB','NHL'],ticket_ledger:{summary:{...summary},by_league:[{league:'NHL',...summary}],by_league_market:[{league:'NHL',scope:'full_game',market_family:'moneyline',...summary}],by_execution_source:[{execution_source:'assumed',...summary}],source_coverage:{retained_archive_hash:5},closed_tickets:[{league:'NHL',matchup:'Away @ Home',selection:'Home',market_label:'Winner (Incl. Overtime and Penalties)',start:stamp,status:'won',stake:2,odds:2,payout:4,pnl:2,execution_source:'assumed',settlement_source:{evidence_level:'retained_archive_hash'}}]},system_decisions:{summary:{...summary,count:5,settled:4,won:1,pnl:-.5,roi:-.0625,settled_stake:8,payout:7.5,hit_rate:.5}},resolution_audit:{issues:[]},improvement_loop:{source_verified:true,last_reviewed_at:stamp,state:'diagnosed',proposals:[{title:'Review negative segment return',reason:'Original losses retained.',league:'NHL',scope:'full_game',market_family:'moneyline',assessment:'diagnostic_only',due_at:stamp,overdue:true,task:{id:'D18',state:'running',run_id:'run-1'},validated_improvement:{state:'not_established'}}]}}}
async function boot(response=report()){
 const w=new Window({url:'http://127.0.0.1:8765',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true,disableIframePageLoading:true}});w.document.write(html);
 const calls=[],writes=[],errors=[];w.addEventListener('error',e=>errors.push(e.message));w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};
 let stats=async()=>({ok:true,json:async()=>structuredClone(response)});
 w.fetch=async(path,options={})=>{calls.push(String(path));if(options.method&&!['GET','HEAD'].includes(options.method))writes.push(path);if(String(path).startsWith('/api/statistics?'))return stats(path,options);return {ok:true,json:async()=>({version:1,recommendations:[],alternatives:[],settings:{max_bet:5,daily_risk_limit:null},status:{},engine:{stage:'Updated'},games:[],models:{},performance:{prospective:[]}})}};
 w.eval(script+'\nwindow.__statisticsTest={dashboard:()=>dashboardFlight,statistics:()=>statisticsFlight,load:()=>load()};');await w.__statisticsTest.dashboard();
 return {w,d:w.document,calls,writes,errors,setStats:value=>{stats=value},open:async()=>{w.document.querySelector('[data-view="statistics"]').click();await w.__statisticsTest.statistics()}};
}
test('Statistics shows cash winrate, original decisions, exposure and source evidence distinctly',async()=>{
 const b=await boot();await b.open();assert.equal(b.d.querySelector('#view-statistics').hidden,false);assert.equal(b.d.querySelector('#stats').hidden,true);assert.equal(b.d.querySelector('#notice').hidden,true);
 const cards=b.d.querySelector('#statistics-cards').textContent;for(const pattern of [/Completed Tickets5/,/Ticket Win Rate66\.67%/,/2 wins \/ 3 decisive tickets/,/Net P&L\$1\.50/,/Settled Stakes\$10\.00/,/Total Returned\$11\.50/,/Return on Stakes15\.00%/])assert.match(cards,pattern);
 assert.match(b.d.querySelector('#statistics-decisions').textContent,/−\$0\.50/);assert.match(b.d.querySelector('#statistics-exposure').textContent,/Open: 1 tickets \/ \$2\.00/);assert.match(b.d.querySelector('#statistics-calibration').textContent,/2 scored original contracts across 2 distinct games/);assert.match(b.d.querySelector('#statistics-tickets').textContent,/Away @ Home.*Winner \(Incl\. Overtime and Penalties\)/);assert.match(b.d.querySelector('#statistics-provenance').textContent,/Archive \+ hash reference retained5/);assert.doesNotMatch(b.d.querySelector('#view-statistics').textContent,/\[object Object\]/);assert.equal(b.d.querySelector('#view-statistics [data-settle],#view-statistics .stake-bet-button'),null);assert.deepEqual(b.writes,[]);assert.deepEqual(b.errors,[]);await b.w.happyDOM.close();
});
test('Statistics escapes untrusted evidence and preserves unproven improvement status',async()=>{
 const r=report();r.ticket_ledger.closed_tickets[0].selection='<img src=x onerror=alert(1)>';r.improvement_loop.proposals[0].title='<script>bad()</script>';const b=await boot(r);await b.open();assert.equal(b.d.querySelector('#statistics-tickets img'),null);assert.equal(b.d.querySelector('#statistics-improvements script'),null);assert.match(b.d.querySelector('#statistics-improvements').textContent,/D18.*running.*run-1.*Overdue.*not established/);assert.match(b.d.querySelector('#statistics-improvements').textContent,/<script>/);assert.deepEqual(b.writes,[]);await b.w.happyDOM.close();
});
test('Incomplete cash and zero decisive samples stay unknown, not zero performance',async()=>{
 const r=report();Object.assign(r.ticket_ledger.summary,{won:0,lost:0,hit_rate:null,roi:null,pnl:null,payout:null,settled_stake:null,money_complete:false});const b=await boot(r);await b.open();const cards=b.d.querySelector('#statistics-cards').textContent;assert.match(cards,/Ticket Win Rate—/);assert.match(cards,/Net P&L—/);assert.match(cards,/Return on Stakes—/);assert.doesNotMatch(cards,/NaN|Infinity|0\.00%/);assert.match(b.d.querySelector('#statistics-basis').textContent,/totals and ROI are withheld/);await b.w.happyDOM.close();
});
test('Statistics filters are GET-only and polling preserves Statistics chrome',async()=>{
 const b=await boot();await b.open();b.d.querySelector('#statistics-league').value='NHL';b.d.querySelector('#statistics-period').value='7d';b.d.querySelector('#statistics-execution').value='reported';b.d.querySelector('#statistics-period').dispatchEvent(new b.w.Event('change'));await b.w.__statisticsTest.statistics();assert.ok(b.calls.some(p=>p.includes('period=7d&league=NHL&execution=reported')));await b.w.__statisticsTest.load();await b.w.__statisticsTest.statistics();assert.equal(b.d.querySelector('#stats').hidden,true);assert.equal(b.d.querySelector('#notice').hidden,true);b.d.querySelector('[data-view="tips"]').click();assert.equal(b.d.querySelector('#stats').hidden,false);assert.equal(b.d.querySelector('#view-statistics').hidden,true);assert.deepEqual(b.writes,[]);await b.w.happyDOM.close();
});
test('Incomplete or failed Statistics response hides totals with an explicit retry path',async()=>{
 const b=await boot({});await b.open();assert.equal(b.d.querySelector('#statistics-results').hidden,true);assert.match(b.d.querySelector('#statistics-status').textContent,/Incomplete statistics response/);b.setStats(async()=>({ok:true,json:async()=>report()}));b.d.querySelector('#statistics-refresh').click();await b.w.__statisticsTest.statistics();assert.equal(b.d.querySelector('#statistics-results').hidden,false);b.setStats(async()=>({ok:false,json:async()=>({detail:'Source temporarily unavailable'})}));b.d.querySelector('#statistics-refresh').click();await b.w.__statisticsTest.statistics();assert.equal(b.d.querySelector('#statistics-results').hidden,true);assert.match(b.d.querySelector('#statistics-status').textContent,/Source temporarily unavailable.*Refresh Statistics/);assert.deepEqual(b.writes,[]);await b.w.happyDOM.close();
});
test('A late old-filter Statistics response cannot overwrite the newer cohort',async()=>{
 const b=await boot();await b.open();let finish,first=true;b.setStats(async()=>{if(first){first=false;return new Promise(resolve=>{finish=()=>{const r=report();r.ticket_ledger.summary.pnl=999;resolve({ok:true,json:async()=>r})}})}const r=report();r.ticket_ledger.summary.pnl=-7;return {ok:true,json:async()=>r}});
 b.d.querySelector('#statistics-period').value='7d';b.d.querySelector('#statistics-period').dispatchEvent(new b.w.Event('change'));await Promise.resolve();b.d.querySelector('#statistics-period').value='30d';b.d.querySelector('#statistics-period').dispatchEvent(new b.w.Event('change'));await b.w.__statisticsTest.statistics();finish();await new Promise(r=>setTimeout(r,5));assert.match(b.d.querySelector('#statistics-cards').textContent,/Net P&L−\$7\.00/);assert.doesNotMatch(b.d.querySelector('#statistics-cards').textContent,/999/);assert.deepEqual(b.writes,[]);await b.w.happyDOM.close();
});

test('Filtered Statistics still exposes global orphan outcome findings',async()=>{
 const r=report();r.global_integrity={orphan_outcome_count:1,orphan_bet_ids:[999]};const b=await boot(r);await b.open();assert.match(b.d.querySelector('#statistics-resolution-note').textContent,/1 resolution or evidence findings/);assert.match(b.d.querySelector('#statistics-resolution').textContent,/999.*orphan outcome.*Global ledger integrity/);await b.w.happyDOM.close();
});

test('Global integrity findings are not counted twice in an unfiltered audit',async()=>{
 const r=report();r.resolution_audit.issues=[{category:'outcome',id:999,code:'orphan_outcome'}];r.global_integrity={orphan_outcome_count:1,orphan_bet_ids:[999]};const b=await boot(r);await b.open();assert.match(b.d.querySelector('#statistics-resolution-note').textContent,/1 resolution or evidence findings/);assert.equal(b.d.querySelectorAll('#statistics-resolution tbody tr').length,1);assert.equal(r.resolution_audit.issues.length,1);await b.w.happyDOM.close();
});

test('Segment analysis keeps frozen model and engine cash cohorts distinct across league and scope',async()=>{
 const r=report();
 const make=(league,scope,brier)=>({...summary,league,scope,market_family:'moneyline',calibration:{observations:7,independent_games:3,mean_conditional_win_probability:.7,observed_win_rate:.4,brier,log_loss:.8123,game_equal_weight_brier:.3123,excluded:{resolution_evidence_issue:2}}});
 r.system_decisions.by_league_market=[make('NHL','full_game',.1234),make('NHL','period_1',.2345),make('MLB','full_game',.3456)];
 r.system_decisions.by_league=[make('NHL',null,.4567)];r.system_decisions.by_market_family=[make(null,'period_1',.5678)];
 const b=await boot(r);await b.open();
 const rows=[...b.d.querySelectorAll('#statistics-segment-models tbody tr')].map(x=>x.textContent);
 assert.equal(rows.length,3);assert.match(rows[0],/NHL.*full game.*7 \/ 3.*70\.00% \/ 40\.00%.*-30\.00%.*0\.1234.*0\.8123.*0\.3123.*resolution evidence issue: 2.*D18/);
 assert.match(rows[1],/NHL.*period 1.*0\.2345/);assert.doesNotMatch(rows[1],/D18/);assert.match(rows[2],/MLB.*0\.3456/);assert.doesNotMatch(rows[2],/D18/);
 assert.match(b.d.querySelector('#statistics-segment-results').textContent,/Settled Games/);
 const select=b.d.querySelector('#statistics-segment-group');select.value='by_league';select.dispatchEvent(new b.w.Event('change'));
 assert.equal(b.d.querySelectorAll('#statistics-segment-models tbody tr').length,1);assert.match(b.d.querySelector('#statistics-segment-models').textContent,/0\.4567/);
 select.value='by_market_family';select.dispatchEvent(new b.w.Event('change'));assert.match(b.d.querySelector('#statistics-segment-models').textContent,/0\.5678/);
 assert.equal(b.calls.filter(x=>x.startsWith('/api/statistics?')).length,1);assert.deepEqual(b.writes,[]);await b.w.happyDOM.close();
});

test('Unscored original segments show unknown model quality and escape development findings',async()=>{
 const r=report();r.system_decisions.by_league_market=[{league:'NHL',scope:'full_game',market_family:'moneyline',settled_games:0}];r.improvement_loop.proposals[0].task.id='<img src=x onerror=bad()>';
 const b=await boot(r);await b.open();const host=b.d.querySelector('#statistics-segment-models');assert.match(host.textContent,/0 \/ 0.*— \/ —/);assert.doesNotMatch(host.textContent,/NaN|undefined|Infinity/);assert.equal(host.querySelector('img'),null);assert.match(host.textContent,/<img/);await b.w.happyDOM.close();
});
