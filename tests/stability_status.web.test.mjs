import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
const html=readFileSync('src/betting_engine/web/index.html','utf8'),script=readFileSync('src/betting_engine/web/app.js','utf8');
test('Stability line reports measured errors and actual repairs without treating stale data as healthy',async()=>{
 const w=new Window({url:'http://localhost',settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});w.document.write(html);w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};w.fetch=async()=>({ok:true,json:async()=>({recommendations:[],alternatives:[],settings:{},status:{},engine:{},games:[],models:{},performance:{prospective:[]}})});
 w.eval(script+'\nwindow.checkStability=(a)=>{data={automation:{supervisor:{stability_audit:a}}};renderStability()};window.flight=()=>dashboardFlight;');await w.flight();
 const read=()=>w.document.querySelector('#stability-status').textContent;
 w.checkStability({state:'stable',checked_at:new Date().toISOString(),error_count:0,repairing_count:0});assert.match(read(),/stable · 0 issues · 0 under repair/);
 w.checkStability({state:'attention_required',checked_at:new Date().toISOString(),error_count:3,repairing_count:1});assert.match(read(),/3 issues · 1 under repair/);
 w.checkStability({state:'stable',checked_at:'2000-01-01',error_count:0,repairing_count:0});assert.match(read(),/fresh measured check/);
 w.checkStability({state:'stable',checked_at:new Date().toISOString(),error_count:-1,repairing_count:0});assert.match(read(),/invalid check data/);
 w.checkStability({state:'stable',checked_at:new Date().toISOString(),error_count:null,repairing_count:0});assert.match(read(),/invalid check data/);
 await w.happyDOM.close();
});
