import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
test('historical sizing trace discloses clustered outcomes and caps without extra bet instructions',async()=>{
 const w=new Window({url:'http://localhost',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});
 w.document.write(readFileSync('src/betting_engine/web/index.html','utf8'));
 w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};
 w.fetch=async()=>({ok:true,json:async()=>({version:1,recommendations:[],settings:{},engine:{},status:{},games:[],models:{},performance:{}})});
 w.eval(readFileSync('src/betting_engine/web/app.js','utf8')+'\nwindow.outcomeSizingTrace=outcomeSizingTrace;window.checkSizing=(row,settings)=>{data={settings};dashboardAvailable=true;return currentSizingAssessment(row);};');
 const row={stake_sizing:{outcome_adjustment:{policy:'historical_game_weighted_v1',base_amount:1,factor:1.25,adjusted_amount:1.25,segment:'NBA|full_game|<img src=x>',source:{independent_games:12,game_wr:.7,game_roi:.12,break_even_wr:.6,as_of:new Date().toISOString(),summary_sha256:'abc'}}}};
 w.traceInput=row;const output=w.eval('outcomeSizingTrace(traceInput)');const el=w.document.createElement('div');el.innerHTML=output;
 assert.match(el.textContent,/12 independent settled games/);assert.match(el.textContent,/70.0%/);assert.match(el.textContent,/\$1.25 after caps/);assert.match(el.textContent,/place only the instruction amount/);assert.equal(el.querySelector('img'),null);
 assert.equal(w.eval('outcomeSizingTrace({})'),'');w.traceInput.stake_sizing.outcome_adjustment.factor=null;assert.equal(w.eval('outcomeSizingTrace(traceInput)'),'');
 await new Promise(r=>setTimeout(r,20));
 const now=new Date().toISOString(),end=new Date(Date.now()+60000).toISOString();
 const sr={quote_at:now,valid_until:end,start:end,odds:2,target_stake:1,recorded_stake:0,difference:1,eligible:true,model_pick_sizing_policy:'unit_cap_v1',sizing:{amount:1},execution_review:{actionable:true,state:'current',quote_at:now,valid_until:end,odds:2,model_pick_sizing_policy:'unit_cap_v1',recommendation_policy:'strict'}};
 assert.equal(w.checkSizing(sr,{}),true);
 assert.equal(w.checkSizing(sr,{outcome_sizing_policy:'historical_game_weighted_v1'}),false);
 sr.outcome_sizing_policy='historical_game_weighted_v1';sr.execution_review.outcome_sizing_policy='historical_game_weighted_v1';
 assert.equal(w.checkSizing(sr,{outcome_sizing_policy:'historical_game_weighted_v1'}),true);
 assert.equal(w.checkSizing(sr,{outcome_sizing_policy:'unknown'}),false);
 sr.outcome_sizing_policy='historical_game_weighted_v2';sr.execution_review.outcome_sizing_policy='historical_game_weighted_v2';
 assert.equal(w.checkSizing(sr,{outcome_sizing_policy:'historical_game_weighted_v2'}),true);
 await w.happyDOM.close();
});

test('historical v2 trace discloses its scaled factor and keeps v1 source evidence explicit',async()=>{
 const w=new Window({url:'http://localhost',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});
 w.document.write(readFileSync('src/betting_engine/web/index.html','utf8'));
 w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};
 w.fetch=async()=>({ok:true,json:async()=>({version:1,recommendations:[],settings:{},engine:{},status:{},games:[],models:{},performance:{}})});
 w.eval(readFileSync('src/betting_engine/web/app.js','utf8')+'\nwindow.outcomeSizingTrace=outcomeSizingTrace;');
 await new Promise(r=>setTimeout(r,20));
 const row={stake_sizing:{outcome_adjustment:{policy:'historical_game_weighted_v2',base_amount:2,source_factor:1.5,factor:3,applied_factor:3,historical_signal:.5,applied_game_score_sum:20,shrinkage_denominator:40,adjusted_amount:5,segment:'NBA|full_game|moneyline',source:{independent_games:20,factor:1.5,game_wr:.7,game_roi:.12,break_even_wr:.6,as_of:new Date().toISOString(),summary_sha256:'abc'}}}};
 const el=w.document.createElement('div');el.innerHTML=w.outcomeSizingTrace(row);
 assert.match(el.textContent,/V2 calculation/);assert.match(el.textContent,/1 \+ 4/);assert.match(el.textContent,/verified v1 evidence/);assert.match(el.textContent,/\$5\.00 after caps/);assert.match(el.textContent,/negative scores are not clipped/);assert.match(el.textContent,/win-rate surplus \/ 0.5/);
 await w.happyDOM.close();
});

test('confidence sizing exposes drivers without relabelling confidence as win probability',async()=>{
 const w=new Window({url:'http://localhost',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});w.document.write(readFileSync('src/betting_engine/web/index.html','utf8'));w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};w.fetch=async()=>({ok:true,json:async()=>({recommendations:[],settings:{},engine:{},status:{},games:[],models:{},performance:{}})});
 w.eval(readFileSync('src/betting_engine/web/app.js','utf8')+'\nwindow.trace=outcomeSizingTrace;window.flight=()=>dashboardFlight;');await w.flight();
 const a={policy:'historical_confidence_v3',base_amount:1,factor:3,adjusted_amount:3,segment:'NFL|full_game|moneyline',confidence:.7,probability_strength:.8,model_market_agreement:.9,required_input_completeness:1,historical_sample_weight:.5,combined_sizing_score:.5,confidence_target_before_event_discount:3,requested_amount:3,source:{independent_games:20,game_wr:.7,game_roi:.2,break_even_wr:.6,as_of:new Date().toISOString(),summary_sha256:'hash'}};
 const host=w.document.createElement('div');host.innerHTML=w.trace({stake_sizing:{outcome_adjustment:a}});assert.match(host.textContent,/Confidence score: 70.0%/);assert.match(host.textContent,/not win probability/);assert.match(host.textContent,/requested \$3.00/);assert.match(host.textContent,/20 independent settled games/);
 const revised=w.trace({execution_revision:{original_stake:1,target_stake:3,stake_sizing:{outcome_adjustment:a}}});assert.match(revised,/Updated before placement/);assert.match(revised,/not an additional bet/);
 a.confidence=null;assert.equal(w.trace({stake_sizing:{outcome_adjustment:a}}),'');await w.happyDOM.close();
});
