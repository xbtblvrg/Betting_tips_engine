import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
test('historical sizing trace discloses clustered outcomes and caps without extra bet instructions',async()=>{
 const w=new Window({url:'http://localhost',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});
 w.document.write(readFileSync('src/betting_engine/web/index.html','utf8'));
 w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};
 w.fetch=async()=>({ok:true,json:async()=>({version:1,recommendations:[],settings:{},engine:{},status:{},games:[],models:{},performance:{}})});
 w.eval(readFileSync('src/betting_engine/web/app.js','utf8')+'\nwindow.outcomeSizingTrace=outcomeSizingTrace;');
 const row={stake_sizing:{outcome_adjustment:{policy:'historical_game_weighted_v1',base_amount:1,factor:1.25,adjusted_amount:1.25,segment:'NBA|full_game|<img src=x>',source:{independent_games:12,game_wr:.7,game_roi:.12,break_even_wr:.6,as_of:new Date().toISOString(),summary_sha256:'abc'}}}};
 w.traceInput=row;const output=w.eval('outcomeSizingTrace(traceInput)');const el=w.document.createElement('div');el.innerHTML=output;
 assert.match(el.textContent,/12 independent settled games/);assert.match(el.textContent,/70.0%/);assert.match(el.textContent,/\$1.25 after caps/);assert.match(el.textContent,/place only the instruction amount/);assert.equal(el.querySelector('img'),null);
 assert.equal(w.eval('outcomeSizingTrace({})'),'');w.traceInput.stake_sizing.outcome_adjustment.factor=null;assert.equal(w.eval('outcomeSizingTrace(traceInput)'),'');
 await new Promise(r=>setTimeout(r,20));await w.happyDOM.close();
});
