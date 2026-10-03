import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
const html=readFileSync('src/betting_engine/web/index.html','utf8'),script=readFileSync('src/betting_engine/web/app.js','utf8');
function section(pnl){return {summary:{},closed_tickets:[],cumulative_performance:{basis:'Recorded settled cash; match dates in Budapest.',coverage:{dated:2},summary:{pnl,roi:pnl/10,hit_rate:.5,max_drawdown:3},points:[{at:'2026-10-01T10:00:00Z',pnl:-3,settled_stake:5,roi:-.6,hit_rate:0,settled:1,games:1},{at:'2026-10-02T10:00:00Z',pnl,settled_stake:10,roi:pnl/10,hit_rate:.5,settled:2,games:2}]},top_markets:[{scope:'period_1',market_family:'totals',market_label:'1st Period - Total',pnl:2,roi:.2,hit_rate:.5,settled:2,games:1}]}}
async function boot(){
 const w=new Window({url:'http://127.0.0.1:8765',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});w.document.write(html);w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};
 const report={as_of:'2026-10-03T12:00:00Z',ticket_ledger:section(4),system_decisions:section(-2)},calls=[];
 w.fetch=async(path,options={})=>{assert.ok(!options.method||options.method==='GET');calls.push(String(path));return {ok:true,json:async()=>String(path).startsWith('/api/statistics?')?structuredClone(report):{recommendations:[],alternatives:[],settings:{},status:{},engine:{},games:[],models:{},performance:{prospective:[]}}}};
 w.eval(script+'\nwindow.__cumulative={render:renderStatisticsPerformance,flight:()=>statisticsFlight,dashboard:()=>dashboardFlight};');await w.__cumulative.dashboard();w.document.querySelector('[data-view="statistics"]').click();await w.__cumulative.flight();
 return {w,d:w.document,report,calls};
}
test('Cumulative chart preserves negative cash, exact accessible points and basis separation',async()=>{
 const b=await boot(),d=b.d;
 assert.match(d.querySelector('#statistics-performance-metrics').textContent,/Net P&L\$4\.00ROI40\.00%Win Rate50\.00%/);
 assert.ok(d.querySelector('.statistics-zero-line'));assert.equal(d.querySelectorAll('.statistics-pnl-point').length,2);
 assert.match(d.querySelector('.statistics-pnl-point').getAttribute('aria-label'),/−\$3\.00/);
 assert.match(d.querySelector('#statistics-performance-table').textContent,/−\$3\.00.*\$4\.00/s);
 assert.match(d.querySelector('#statistics-top-markets').textContent,/1st Period - Total.*2 \/ 1/s);
 const select=d.querySelector('#statistics-performance-basis');select.value='system_decisions';select.dispatchEvent(new b.w.Event('change'));
 assert.match(d.querySelector('#statistics-performance-metrics').textContent,/−\$2\.00ROI-20\.00%/);
 assert.match(d.querySelector('#statistics-chart-title').textContent,/Original engine decisions/);
 assert.match(d.querySelector('#statistics-performance-note').textContent,/extra tickets do not add another engine decision/);
 await b.w.happyDOM.close();
});
test('Filters refresh cumulative chart and leaders together while preserving chosen basis',async()=>{
 const b=await boot();const select=b.d.querySelector('#statistics-performance-basis');select.value='system_decisions';select.dispatchEvent(new b.w.Event('change'));
 b.report.system_decisions=section(12);b.report.system_decisions.top_markets=[];
 b.d.querySelector('#statistics-period').value='7d';b.d.querySelector('#statistics-period').dispatchEvent(new b.w.Event('change'));await b.w.__cumulative.flight();
 assert.ok(b.calls.some(p=>p.includes('period=7d')));assert.equal(select.value,'system_decisions');
 assert.match(b.d.querySelector('#statistics-performance-metrics').textContent,/\$12\.00/);assert.match(b.d.querySelector('#statistics-top-markets').textContent,/No profitable settled bet types/);await b.w.happyDOM.close();
});
test('Unknown cash and hostile metadata cannot invent results or inject SVG/HTML',async()=>{
 const b=await boot(),s=section(null);s.cumulative_performance.summary={pnl:null,roi:null,hit_rate:null,max_drawdown:null};s.cumulative_performance.points=[{at:'invalid',pnl:9},{at:'2026-10-01T00:00:00Z',pnl:null}];s.cumulative_performance.basis='<img src=x onerror=alert(1)>';
 s.top_markets=[{scope:'<svg onload=alert(1)>',market_label:'<img src=x>',pnl:1},{pnl:-10},...Array.from({length:8},(_,i)=>({pnl:i+2,market_label:'Type '+i}))];
 b.w.__cumulative.render({ticket_ledger:s});assert.equal(b.d.querySelector('#statistics-performance-chart svg'),null);assert.match(b.d.querySelector('#statistics-performance-metrics').textContent,/ROI—Win Rate—/);assert.match(b.d.querySelector('#statistics-performance-coverage').textContent,/2 points/);assert.equal(b.d.querySelectorAll('#statistics-top-markets tbody tr').length,5);assert.equal(b.d.querySelector('#statistics-top-markets img'),null);
 s.top_markets=s.top_markets.slice(0,1);b.w.__cumulative.render({ticket_ledger:s});assert.equal(b.d.querySelector('#statistics-top-markets svg'),null);assert.match(b.d.querySelector('#statistics-top-markets').textContent,/<img src=x>/);assert.equal(b.d.querySelector('#statistics-performance-note img'),null);await b.w.happyDOM.close();
});
test('Outcome monitoring clearly retains its global original-decision scope',async()=>{
 const b=await boot();b.report.improvement_loop={last_reviewed_at:'2026-10-03T10:00:00Z',cumulative_monitor:{state:'drawdown_observed',summary:{current_drawdown:6},diagnostic_due:true}};
 b.w.__cumulative.render(b.report);const text=b.d.querySelector('#statistics-performance-monitor').textContent;
 assert.match(text,/Full original-decision history, independent of the chart filters/);assert.match(text,/drawdown observed.*\$6\.00.*Last reviewed.*diagnostic is due/s);assert.match(text,/does not automatically change stakes or promote a model/);await b.w.happyDOM.close();
});
