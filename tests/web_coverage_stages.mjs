import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';

test('exact coverage renders ten independent stages, offered inventory and incomplete totals',async()=>{
 const w=new Window({url:'http://127.0.0.1:8765',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});
 w.document.write(readFileSync('src/betting_engine/web/index.html','utf8'));
 w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};
 const definitions=['Data','Parsing','Model','Pricing','Guard','Selection','Sizing','Issuance / tracking','Settlement','Learning'].map((label,i)=>({id:String(i),label}));
 const row={league:'NFL',market:'1st Quarter - Draw No Bet',scope:'quarter_1',inventory_state:'offered_now',verified_stages:1,stages:Object.fromEntries(definitions.map(s=>[s.id,{state:s.id==='1'?'verified':'not_observed',observed:s.id==='1'?2:0,expected:2,basis:'Exact source <script>unsafe</script>'}])),funding_eligible:false,funding_eligible_now:false,issued_today:0};
 const report={publication_at:new Date().toISOString(),checked_at:new Date().toISOString(),scope:'Measured coverage',stage_definitions:definitions,rows:[row,{...row,market:'Rushing Yards',section:'swishMarkets.playerProps',inventory_state:'observed_activity_unverified',offered_now:false}],leagues:{NFL:{observed_types:1,offered_types:1,complete_10_of_10_types:0,incomplete_offered_types:1,evaluated_types:0,funding_eligible_types:0,issued_today:0}}};
 const dashboard={version:1,recommendations:[],settings:{bankroll:null,daily_risk_limit:null,daily_pick_target:100,max_bet:5,unit_stake:1,minimum_roi:.03,refresh_seconds:60},engine:{running:true,stage:'Updated'},status:{},games:[],models:{},performance:{prospective:[]},automation:{market_catalog:{live_coverage:report}}};
 w.fetch=async path=>({ok:true,json:async()=>path==='/api/dashboard'?dashboard:{bets:[],performance:{actual_bets:{count:0,pnl:0,settled:0}}}});
 w.eval(readFileSync('src/betting_engine/web/app.js','utf8'));await new Promise(r=>setTimeout(r,20));
 const table=w.document.querySelector('#live-coverage-table');
 for(const d of definitions)assert.match(table.textContent,new RegExp(d.label));
 assert.match(table.textContent,/offered now/);assert.match(table.textContent,/1\/10/);assert.match(table.textContent,/verified · 2\/2/);
 assert.equal(table.querySelector('script'),null);
 assert.match(table.textContent,/observed activity unverified/);
 assert.match(table.textContent,/swishMarkets.playerProps/);
 assert.match(w.document.querySelector('#live-coverage-leagues').textContent,/Complete 10\/10/);
 await w.happyDOM.close();
});
