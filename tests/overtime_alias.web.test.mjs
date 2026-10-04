import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';

test('overtime instructions show exact Yes/No and regulation result',async()=>{
 const w=new Window();
 try {
  const script=readFileSync('src/betting_engine/web/app.js','utf8');
  w.document.write(readFileSync('src/betting_engine/web/index.html','utf8'));
  w.fetch=()=>new Promise(()=>{});w.setInterval=()=>0;w.EventSource=class{addEventListener(){}};
  w.eval(script+'\nwindow.instructions=betInstructions;');
  for(const [kind,side,selection,pattern] of [['three_way','draw','Yes',/Yes wins when regulation ends in a draw/],['double_chance','home_away','No',/No wins when regulation ends with unequal scores/]]){
   const markup=w.instructions({market:'regulation:'+kind,model_scope:'regulation',league:'NBA',side,selection,reference_contract_description:'Will There Be Overtime',market_display:'Will There Be Overtime',home:'Home',away:'Away',minimum_odds:1.5,reference_odds:2,stake:0});
   assert.match(markup,pattern);assert.match(markup,/regardless of whether overtime is played/);
   assert.doesNotMatch(markup,/Use this exact Double Chance selection/);
  }
 } finally {await w.happyDOM.close();}
});
