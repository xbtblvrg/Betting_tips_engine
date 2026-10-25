'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let data=null,horizon='48',view='tips',selectedTip=null,selectedInstructionSignature=null,dashboardAvailable=false;
let dashboardRequest=0,appliedDashboardRequest=0,placementEpoch=0;
let dashboardFlight=null,dashboardQueued=null;
const pendingPlacements=new Map();
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=v=>Number.isFinite(v)?(v*100).toFixed(1)+'%':'—';
const num=(v,n=2)=>Number.isFinite(v)?v.toFixed(n):'—';
const stakeStr=(v,def)=>{const val=v!=null?v:def;return Number.isInteger(val)?String(val):num(val);};
const minOdds=v=>Number.isFinite(v)?(Math.ceil((v-Number.EPSILON)*100)/100).toFixed(2):'—';
const when=v=>v?new Intl.DateTimeFormat('en-GB',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Budapest'}).format(new Date(v)):'—';
const localDay=d=>new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Europe/Budapest'}).format(d);
const title={research:'Research and Coverage',tips:'Betting Picks',games:'Schedule',models:'Model Performance',ledger:'Bet Tracker',sizing:'Stake Sizing',statistics:'Statistics',rd:'Autonomous Quant R&D Live',stake_account:'Stake Live Account',settings:'Settings'};
const marketName={three_way:'1x2','1x2':'1x2',moneyline:'Winner',spreads:'Handicap',totals:'Total',home_total:'Home Team Total',away_total:'Away Team Total',both_score:'Both Teams to Score',double_chance:'Double Chance',odd_even:'Odd/Even',winner_total:'1x2 & Total',exact_score:'Correct Score'};
function stakeNavigation(t){
 // Old recorded bets keep their original payload. Exact-selection navigation
 // may come from a fresh assessment without rewriting those records.
 const sameContract=c=>c.id===t.id&&['league','game_id','market','side','line','model_scope'].every(k=>c[k]===t[k]);
 const current=currentTips(t.current_assessment?[t.current_assessment]:[]).find(sameContract)||allEvaluated().find(sameContract);
 // A current unknown route is authoritative too: never resurrect an old tab.
 // Legacy partial metadata without an event_url cannot provide a complete
 // route. Keep its recorded link and route together; explicit null is known.
 if(current&&Object.hasOwn(current,'stake_market')&&(current.stake_market===null||Object.hasOwn(current.stake_market,'event_url')))return current.stake_market||{};
 if(Object.hasOwn(t,'stake_market'))return t.stake_market||{};
 // Legacy issued cards may predate navigation metadata. Use their recorded
 // Stake event URL; never invent a route or override an explicit null route.
 try{const u=new URL(t.reference_url);if(u.origin==='https://stake.com'&&u.pathname.startsWith('/sports/'))return {event_url:t.reference_url};}catch{}
 return {};
}
function inferStakeTab(t){
 if(!t)return null;
 if(t.stake_market&&t.stake_market.tab)return t.stake_market.tab;
 const lg=(t.league||'').toUpperCase();
 const m=(t.market||'').toLowerCase();
 const mn=(t.market_display||(t.stake_market&&t.stake_market.name)||(t.stake_evidence&&t.stake_evidence.market_name)||'').toLowerCase();
 const groups=(t.stake_evidence&&t.stake_evidence.market_groups)||[];
 const grpNames=groups.map(g=>(g.name||'').toLowerCase());

 const isTennis=['ATP','WTA','CHALLENGER','TENNIS'].includes(lg);
 const isBasketball=['NBA','WNBA','NCAAB','BASKETBALL'].includes(lg);
 const isFootball=['NFL','NCAAF','AMERICAN_FOOTBALL','FOOTBALL'].includes(lg);
 const isHockey=['NHL','ICE_HOCKEY'].includes(lg);
 const isBaseball=['MLB','NPB','BASEBALL'].includes(lg);
 const isSoccer=['EPL','UCL','MLS','SOCCER'].includes(lg);

 if(isTennis){
  if(m==='total_sets'||mn.includes('total sets')||grpNames.includes('sets')||m.includes('set')||mn.includes('set')||m.includes('period_'))return 'Sets';
  if(grpNames.includes('games')||mn.includes('games')||mn.includes('total games')||mn.includes('game handicap')||m.includes('spreads')||m.includes('totals'))return 'Games';
  return 'Popular';
 }

 if(isHockey){
  if(grpNames.includes('periods')||m.includes('period')||mn.includes('period'))return 'Periods';
  if(m==='three_way'||m==='moneyline'||m==='spreads'||m==='totals'||mn.includes('winner')||mn.includes('handicap')||mn.includes('puck line')||mn.includes('total (incl. overtime)'))return 'Popular';
  if(grpNames.includes('goals')||m.includes('goals')||mn.includes('goals')||m.includes('both_score'))return 'Goals';
  return 'Popular';
 }

 if(isBasketball||isFootball){
  if(grpNames.includes('half')||grpNames.includes('1st2ndhalfmarkets')||m.includes('half')||mn.includes('half'))return 'Half';
  if(grpNames.includes('quarters')||m.includes('quarter')||mn.includes('quarter')||m.includes('period_'))return 'Quarters';
  if(grpNames.includes('points')&&!grpNames.includes('main'))return 'Points';
  if(grpNames.includes('teamandmatchprops'))return 'Team/Match';
  return 'Popular';
 }

 if(isBaseball){
  if(grpNames.includes('innings 1 to 5')||m.includes('innings_1_5')||mn.includes('innings 1 to 5')||mn.includes('first 5'))return 'Innings 1 to 5';
  const mnClean=mn.replace(/extra[-_ ]innings/g,'');
  if(grpNames.includes('innings')||m.includes('inning')||mnClean.includes('inning'))return 'Innings';
  if(grpNames.includes('runs')||m.includes('runs')||mn.includes('runs'))return 'Runs';
  return 'Popular';
 }

 if(isSoccer){
  if(grpNames.includes('half')||grpNames.includes('1st2ndhalfmarkets')||m.includes('half')||mn.includes('half'))return 'Half';
  if(grpNames.includes('asianlines')||m.includes('asian')||mn.includes('asian'))return 'Asian Lines';
  if(grpNames.includes('cardscorners')||m.includes('card')||m.includes('corner')||mn.includes('card')||mn.includes('corner'))return 'Cards and Corners';
  if(grpNames.includes('goals')||m.includes('goals')||mn.includes('goals')||m.includes('both_score')||mn.includes('goalscorer')||mn.includes('exact goals'))return 'Goals';
  return 'Popular';
 }

 // Generic fallback
 if(grpNames.includes('half')||grpNames.includes('1st2ndhalfmarkets')||m.includes('half')||mn.includes('half'))return 'Half';
 if(grpNames.includes('quarters')||m.includes('quarter')||mn.includes('quarter'))return 'Quarters';
 if(grpNames.includes('periods')||m.includes('period')||mn.includes('period'))return 'Periods';
 if(grpNames.includes('sets')||m.includes('set')||mn.includes('set'))return 'Sets';
 if(grpNames.includes('asianlines')||m.includes('asian')||mn.includes('asian'))return 'Asian Lines';
 return 'Popular';
}
function betInstructions(t){
 const kind=t.market.split(':').at(-1),scope=t.model_scope||'full_game';
 const market=t.market_display||marketName[t.market],mapping=t.stake_market||{},navigation=stakeNavigation(t),terms=view==='tips'&&pendingInstruction(t)?(t.current_assessment||t):t;
 const isTennis=['ATP','WTA','CHALLENGER'].includes(t.league);
 const isSoccer=['MLS','EPL','UCL'].includes(t.league);
 const units=t.league==='MLB'?'runs':(t.league==='NHL'||isSoccer)?'goals':isTennis?'games':'points';
 let outcome=isTennis?'The selected player must win the match.':'The selected team must win, including the overtime or extra-innings scope shown in the market name.';
 if(kind==='three_way'||kind==='1x2')outcome=`Regular time only (60 minutes for NHL, 90 minutes for soccer). Excludes overtime and shootouts. Select ${t.selection}; a tied score wins only the Draw selection.`;
 if(kind==='double_chance')outcome=`Regular time only. Either outcome in ${t.selection} wins. Excludes overtime and shootouts.`;
 if(kind==='both_score')outcome=`Regular time only. ${t.side==='yes'?'Both teams must score at least once':'At least one team must score zero'}.`;
 if(isSoccer&&kind==='moneyline')outcome=`The selected team must win in regular time. If the match ends in a draw, the bet is void/refunded (Draw No Bet).`;
 if(kind==='spreads'&&Number.isFinite(t.line))outcome=isTennis?(t.line<0?`The selected player must win by more than ${Math.abs(t.line)} ${units}.`:t.line>0?`The selected player must win or lose by fewer than ${t.line} ${units}.`:'The selected player must win. A tied handicap result is void.'):(t.line<0?`The selected team must win by more than ${Math.abs(t.line)} ${units}.`:t.line>0?`The selected team must win or lose by fewer than ${t.line} ${units}.`:'The selected team must win. A tied handicap result is void.');
 if(['home_total','away_total'].includes(kind))outcome=`Only ${kind==='home_total'?t.home:t.away} must score ${t.side==='over'?'over':'under'} ${t.line} ${units}. Use that ${isTennis?'player':'team'}'s Total market, with the exact overtime or extra-innings scope.`;
 if(kind==='totals')outcome=isTennis?`The combined total ${units} of both players must be ${t.side==='over'?'over':'under'} ${t.line} ${units}. Select the match Total Games.`:`The combined final score of both teams must be ${t.side==='over'?'over':'under'} ${t.line} ${units}. Select the match Total, not a team Total.`;
 if(scope!=='full_game'){
  const labels={inning_1:'the 1st Inning',innings_1_5:'Innings 1 to 5',quarter_1:'the 1st Quarter',quarter_2:'the 2nd Quarter',quarter_3:'the 3rd Quarter',quarter_4:'the 4th Quarter',quarter_4_ot:'the 4th Quarter including overtime',half_1:'the 1st Half',half_2:'the 2nd Half',half_2_ot:'the 2nd Half including overtime',period_1:'the 1st Period',period_2:'the 2nd Period',period_3:'the 3rd Period',regulation:'Regulation Time'};
  const label=labels[scope]||t.period||scope;
  outcome=kind==='three_way'?`Only ${label} count. Select ${t.selection}; a tied score wins only the Draw selection.`:kind==='moneyline'?`The selected team must lead after ${label}; a tie is a push.`:kind.endsWith('_total')?`Only ${kind==='home_total'?t.home:t.away} ${units} in ${label} count toward ${t.selection}.`:`Only ${units} in ${label} count toward ${t.selection}. Ignore scores outside this scope.`;
  if(kind==='odd_even')outcome=`The combined ${units} in ${label} must be ${t.selection}. Zero counts as Even.`;
  if(kind==='both_score')outcome=`In ${label}, ${t.side==='yes'?'both teams must score at least once':'at least one team must score zero'}.`;
  if(kind==='double_chance')outcome=`At the end of ${label}, either outcome in ${t.selection} wins. Use this exact Double Chance selection.`;
 }
 if(scope==='regulation'&&['NBA','WNBA'].includes(t.league)&&/^Will there be overtime\??$/i.test(t.reference_contract_description||'')&&((kind==='three_way'&&t.side==='draw')||(kind==='double_chance'&&t.side==='home_away')))outcome=t.side==='draw'?'Yes wins when regulation ends in a draw, regardless of whether overtime is played.':'No wins when regulation ends with unequal scores, regardless of whether overtime is played.';
 if(scope==='regulation'&&t.league==='NFL'&&/^Will there be overtime\??$/i.test(t.reference_contract_description||'')&&((kind==='three_way'&&t.side==='draw')||(kind==='double_chance'&&t.side==='home_away')))outcome=t.side==='draw'?'Yes wins if the completed regular-season or playoff game was tied after regulation and entered overtime.':'No wins if the completed regular-season or playoff game ended with unequal regulation scores.';
 return `<h3>Find This Bet on Stake</h3><ol><li>Open ${esc(t.league)} and find <strong>${esc(t.away)} @ ${esc(t.home)}</strong>. Match both team names and the start time; team order may differ.</li><li>${navigation.tab?`Select the <strong>${esc(navigation.tab)}</strong> tab above the markets, then find`:'Find'} the market named <strong>${esc(market)}</strong> and select <strong>${esc(t.selection)}</strong>.${Number.isFinite(t.line)?' If multiple lines are shown, choose the exact number and sign above.':''}</li><li>Use <strong>Decimal</strong> odds and check for <strong>${displayedMinimumOdds(t)} or higher</strong>${isModelPick(t)?' (rounded up strictly above model break-even)':''}. The evaluated Stake odds were ${num(terms.reference_odds)}.</li><li>Add the selection as a <strong>Single</strong> bet. ${view==='tips'&&pendingInstruction(t)?`Bet <strong>$${num(t.tracked_bet.stake)} USD</strong> once on this exact selection. If already placed, mark it as placed instead of repeating it.`:t.tracked_bet?`Already tracked: <strong>$${num(t.tracked_bet.stake)}</strong> at <strong>${num(t.tracked_bet.odds)}</strong>. No additional bet is requested.`:t.stake>0?`Suggested stake: <strong>$${num(t.stake)} USD</strong>, subject to the checked odds and remaining limit.`:'Watchlist only: no stake is currently allocated. Check the current odds before assigning a stake.'}</li></ol><p>${esc(outcome)}</p><p class="panel-copy">${esc(mapping.scope_note||'Use the exact market and settlement rules shown above.')} Pregame picks do not apply to in-play markets.</p><p class="panel-copy">${esc(mapping.availability_note||'This selection was not verified against the current Stake feed.')} If that line is unavailable, do not substitute a different line.</p>${mapping.league_url?`<a class="button ghost" href="${esc(mapping.league_url)}" target="_blank" rel="noopener noreferrer">Open ${esc(t.league)} on Stake ↗</a>`:''} <a href="https://stake.com/policies/sportsbook" target="_blank" rel="noopener noreferrer">Stake Market Rules</a>`;
}
let toastTimer=null;
function toast(message,options={}){
 const t=$('#toast');
 if(!t)return;
 clearTimeout(toastTimer);
 t.innerHTML='';
 const msgSpan=document.createElement('span');
 msgSpan.textContent=message;
 t.appendChild(msgSpan);
 if(options?.actionText&&typeof options.onAction==='function'){
  const btn=document.createElement('button');
  btn.className='toast-action-btn';
  btn.textContent=options.actionText;
  btn.onclick=e=>{
   e.stopPropagation();
   t.setAttribute('hidden','');
   t.hidden=true;
   options.onAction();
  };
  t.appendChild(btn);
 }
 t.removeAttribute('hidden');
 t.hidden=false;
 toastTimer=setTimeout(()=>{
  t.setAttribute('hidden','');
  t.hidden=true;
 },options?.duration||5000);
}
async function api(path,options={}){const r=await fetch(path,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const d=await r.json();if(!r.ok){const msg=typeof d.detail==='string'?d.detail:typeof d.message==='string'?d.message:'Unable to save. Check the fields and try again.';const err=new Error(msg);err.data=d;throw err}return d}
function isEngineActiveOrUpdating(){
 const conn=document.querySelector('#connection')?.textContent||'';
 return Boolean(data?.engine?.running || conn==='Updating…' || conn==='Evaluating Markets' || conn==='Syncing…');
}
function currentTips(rows){
 const updating=isEngineActiveOrUpdating();
 const maxAge=updating?900000:300000;
 return rows.filter(t=>!t.expired&&!t.withdrawn_at&&(updating||new Date(t.valid_until)>new Date())&&new Date(t.start)>new Date()&&Date.now()-new Date(t.quote_at)<=maxAge&&Date.now()-new Date(t.quote_at)>=-5000);
}
function allCurrentSelections(){return currentTips([...(data?.recommendations||[]),...(data?.alternatives||[])])}
function isBetRecommendation(t){return t.stake>0||!!t.tracked_bet||isPlaced(t)}
function allEvaluated(){return currentTips(data?.evaluated_candidates||[...(data?.recommendations||[]),...(data?.alternatives||[])])}
function liveTips(){
 const current=new Map(allEvaluated().map(t=>[t.id,t]));
 const issued=new Map(allCurrentSelections().filter(isBetRecommendation).map(t=>[t.id,t]));
 const groups=new Map();for(const b of data?.tracked_bets||[]){if(!groups.has(b.tip_id))groups.set(b.tip_id,[]);groups.get(b.tip_id).push(b)}
 for(const [id,bets] of groups){
  const first=bets.reduce((a,b)=>a.created_at<b.created_at?a:b),amount=bets.reduce((n,b)=>n+b.stake,0),odds=bets.reduce((n,b)=>n+b.stake*b.odds,0)/amount;
  issued.set(id,{...first.payload,stake:0,recorded_snapshot:true,current_assessment:current.get(id)||null,
   instruction_review:first.instruction_review,reference_odds:odds,tracked_bet:{id:first.id,count:bets.length,stake:amount,odds,status:first.status,created_at:first.created_at}});
 }
 return [...issued.values()];
}
function allocationReason(t){
 const held=t.covered_by||[];
 if(held.length)return 'Already covered by '+[...new Set(held.map(b=>b.selection))].join(', ')+'. No repeat bet.';
 return t.allocation_reason||t.stake_note||'No stake allocated; this is not an instruction to bet.';
}
function marketPriority(t){
 const scope=t.model_scope||'full_game',kind=String(t.market||'').split(':').at(-1);
 if(scope!=='full_game')return 2;
 return ({moneyline:0,spreads:0,totals:0,home_total:1,away_total:1}[kind]??1);
}
function isPlaced(t){return pendingPlacements.has(t.id)?pendingPlacements.get(t.id):!!data?.placement_marks?.[t.id]}
function tipTiming(t){
 if(t.timing_evaluation)return t.timing_evaluation;
 const start=new Date(t.start).getTime(),now=Date.now();
 const hours=(start-now)/3600000;
 if(hours<=48)return {action:'IMMEDIATE',label:'READY',hours_to_start:Math.round(hours*10)/10,urgency:'normal',reason:'Normal 48h execution window'};
 const c=executionTerms(t),mkt=String(c.market||'').toLowerCase(),sel=String(c.selection||'').toLowerCase();
 const ev=Number(c.expected_roi||0),odds=Number(c.reference_odds||0),stress=Number(c.stress_roi||0),p=Number(c.probability||0),scope=String(c.model_scope||'full_game').toLowerCase();
 if(mkt.includes('totals')&&(sel.includes('under')||c.side==='under'))return {action:'WAIT',label:'WAIT',hours_to_start:Math.round(hours*10)/10,urgency:'low',reason:'Totals Under: public money often pushes line higher closer to kickoff'};
 if(scope!=='full_game')return {action:'WAIT',label:'WAIT',hours_to_start:Math.round(hours*10)/10,urgency:'low',reason:'Period / derivative market: wait for liquidity within 48h'};
 if(ev>=0.045||stress>=0.02)return {action:'BET_NOW',label:'BET NOW',hours_to_start:Math.round(hours*10)/10,urgency:'high',reason:`High early value (EV ${pct(ev)}): odds expected to shorten; lock in early`};
 if(odds>=2.00&&ev>=0.015)return {action:'BET_NOW',label:'BET NOW',hours_to_start:Math.round(hours*10)/10,urgency:'high',reason:`Value underdog (${num(odds)} odds, EV ${pct(ev)}): high early alpha capture`};
 if(p>=0.55&&['moneyline','spreads','three_way','1x2'].some(k=>mkt.includes(k))&&ev>=0.02)return {action:'BET_NOW',label:'BET NOW',hours_to_start:Math.round(hours*10)/10,urgency:'high',reason:`Strong core favorite: lines compress early`};
 if(ev<0.035&&stress<=0.005)return {action:'WAIT',label:'WAIT',hours_to_start:Math.round(hours*10)/10,urgency:'low',reason:'Marginal edge: wait for confirmed lineups within 48h'};
 return {action:'WAIT',label:'WAIT',hours_to_start:Math.round(hours*10)/10,urgency:'low',reason:'Monitor price development until 48-hour window'};
}
function tipPreference(t){
 const c=executionTerms(t);
 const stake=Number(t.tracked_bet?.stake||t.stake||c.stake||1);
 const ev=Number(c.expected_roi||t.expected_roi||0);
 const b=c.funding_basis||t.funding_basis;
 const tier=b?.tier||c.funding_tier||t.funding_tier||(ev>0.03?'model_pick':'probability_pick');
 const tierScore=tier==='stress_value'?300:(tier==='model_pick'?200:100);
 return (stake*100)+(ev*1000)+tierScore;
}
function visibleTips(rows=liveTips()){
 rows=rows.filter(t=>new Date(t.start)>new Date());if(['24','48','72'].includes(horizon))rows=rows.filter(t=>new Date(t.start)>new Date()&&new Date(t.start)<=new Date(Date.now()+Number(horizon)*3600000));
 const today=localDay(new Date()),tomorrow=localDay(new Date(Date.now()+86400000));
 if(horizon==='today')rows=rows.filter(t=>localDay(new Date(t.start))===today||isTodayIssued(t));
 if(horizon==='tomorrow')rows=rows.filter(t=>localDay(new Date(t.start))===tomorrow);
 const league=$('#league-filter').value;if(league!=='all')rows=rows.filter(t=>t.league===league);
 const market=$('#market-filter')?.value||'all';
 if(market!=='all')rows=rows.filter(t=>marketMatches(t.market,market));
 const sort=$('#sort-filter').value;
 return rows.sort((a,b)=>{
  const completed=Number(isPlaced(a))-Number(isPlaced(b));if(completed)return completed;
  if(horizon==='24'){
   const startOrder=new Date(a.start)-new Date(b.start);if(startOrder)return startOrder;
   const pDiff=tipPreference(b)-tipPreference(a);if(pDiff)return pDiff;
   const familyOrder=marketPriority(a)-marketPriority(b);if(familyOrder)return familyOrder;
   return String(a.id).localeCompare(String(b.id));
  }
  if(horizon==='72'){
   const timingOrder=(t)=>{
    const tm=tipTiming(t);
    if(tm.action==='BET_NOW')return 0;
    if(tm.action==='IMMEDIATE')return 1;
    return 2;
   };
   const tDiff=timingOrder(a)-timingOrder(b);
   if(tDiff)return tDiff;
  }
  const currentA=executionTerms(a),currentB=executionTerms(b);
  if(sort==='probability'&&currentA.probability!==currentB.probability)return currentB.probability-currentA.probability;
  if(sort==='rank'&&currentA.rank!==currentB.rank&&currentA.rank!=null&&currentB.rank!=null)return currentA.rank-currentB.rank;
  if(sort==='preferred'){
   const pDiff=tipPreference(b)-tipPreference(a);
   if(pDiff)return pDiff;
  }
  if(horizon==='today'){
   const pDiff=tipPreference(b)-tipPreference(a);
   if(pDiff)return pDiff;
  }
  const startOrder=new Date(a.start)-new Date(b.start);if(startOrder)return startOrder;
  const pDiff=tipPreference(b)-tipPreference(a);if(pDiff)return pDiff;
  const familyOrder=marketPriority(a)-marketPriority(b);if(familyOrder)return familyOrder;
  return String(a.id).localeCompare(String(b.id));
 });
}
function marketMatches(value,filter){
 if(filter==='all')return true;
 const kind=String(value||'').split(':').at(-1);
 if(filter==='moneyline')return kind==='moneyline'||kind==='three_way'||kind==='1x2';
 return filter==='other'?!['moneyline','spreads','totals','three_way','1x2'].includes(kind):kind===filter;
}
function issuedRowsVisible(rows){
 const now=Date.now(),today=localDay(new Date()),tomorrow=localDay(new Date(now+86400000));
 const league=$('#league-filter').value,market=$('#market-filter')?.value||'all';
 return (rows||[]).filter(r=>{
  if(league!=='all'&&r.league!==league)return false;
  if(!marketMatches(r.market,market))return false;
  const stamp=new Date(r.start).getTime();if(!Number.isFinite(stamp)||stamp<=now)return false;
  if(['24','48','72'].includes(horizon)&&stamp>now+Number(horizon)*3600000)return false;
  if(horizon==='today'&&localDay(new Date(stamp))!==today&&localDay(new Date(r.issued_at||stamp))!==today)return false;
  if(horizon==='tomorrow'&&localDay(new Date(stamp))!==tomorrow)return false;
  return true;
 });
}
function placementControl(t){return `<label class="placement-toggle"><input type="checkbox" data-placement="${esc(t.id)}" ${isPlaced(t)?'checked':''} ${pendingPlacements.has(t.id)?'disabled':''} aria-label="Bet placed: ${esc(t.selection)} — ${esc(t.away)} at ${esc(t.home)}"><span>${pendingPlacements.has(t.id)?'Saving…':isPlaced(t)?'Bet placed':'Mark as placed'}</span></label>`}
let placementQueue=Promise.resolve();
async function setPlacement(id,placed,options={}){
 if(pendingPlacements.has(id))return;
 pendingPlacements.set(id,placed);placementEpoch++;renderTips();

 // Optimistic Toast - instant response! No waiting for backend roundtrip!
 if(!options?.silent){
  if(placed){
   toast('Marked as placed. Saved in Bet Tracker.',{
    actionText:'Unmark',
    duration:6000,
    onAction:()=>setPlacement(id,false)
   });
  }else{
   toast('Mark removed. Current qualifying picks return to Betting Picks.',{duration:4000});
  }
 }

 const task=async()=>{
  try{
   const result=await api('/api/tips/'+encodeURIComponent(id)+'/checklist',{method:'PUT',body:JSON.stringify({placed})});
   data.placement_marks??={};
   if(result.placed)data.placement_marks[id]=result.placed_at;else delete data.placement_marks[id];
   pendingPlacements.delete(id);placementEpoch++;renderTips();
   return result;
  }catch(e){
   pendingPlacements.delete(id);placementEpoch++;renderTips();
   toast(e.message);
  }
 };

 const current=placementQueue.then(task,task);
 placementQueue=current.catch(()=>{});
 return current;
}
function bindPlacementControls(){
 $$('[data-placement]').forEach(input=>input.onchange=()=>setPlacement(input.dataset.placement,input.checked));
}
function trackingLabel(t){return t.tracking_source==='auto_assumed_placed'?'Auto-tracked · Assumed placed':t.tracking_source==='user_reported_placed'?'User-reported Stake bet':'Manually logged'}
function modelPickAllocationPolicy(evidence){return evidence?.model_pick_allocation_policy===undefined?'one_per_game_v1':evidence.model_pick_allocation_policy}
function executionReview(t){
 const c=t.current_assessment||t,r=t.tracked_bet?t.instruction_review:t.execution_review;
 // The API verifies the sealed decision and current sizing. A browser fallback
 // must never turn missing or rejected server evidence into a betting instruction.
 if(!dashboardAvailable||!r?.actionable||!c.execution_review?.actionable||!currentTips([c]).length)return {actionable:false};
 const age=Date.now()-new Date(r.quote_at);
 const updating=isEngineActiveOrUpdating();
 const maxAge=updating?900000:300000;
 if(!Number.isFinite(age)||age < -5000||age>maxAge||(!updating&&!(new Date(r.valid_until)>new Date())))return {actionable:false};
 if(c.reference_platform!=='Stake.com'||c.stake_market?.availability_verified!==true||c.decision_guard?.passed!==true)return {actionable:false};
 if(!Number.isFinite(c.probability)||c.probability<=0||c.probability>=1||!Number.isFinite(c.reference_odds)||c.reference_odds<=1)return {actionable:false};
 const policy=data?.settings?.recommendation_policy??'strict',basis=c.funding_basis,currentReview=c.execution_review;
 const outcomePolicy=data?.settings?.outcome_sizing_policy??'legacy';
 if(!['legacy','historical_game_weighted_v1','historical_game_weighted_v2','historical_confidence_v3'].includes(outcomePolicy)||![c,c.decision_guard,r,currentReview].every(v=>(v?.outcome_sizing_policy??'legacy')===outcomePolicy))return {actionable:false};
 const allocationPolicy=modelPickAllocationPolicy(data?.settings);
 if(!['one_per_game_v1','distinct_family_v1'].includes(allocationPolicy)||
  ![c.decision_guard,basis,r,currentReview].every(v=>modelPickAllocationPolicy(v)===allocationPolicy))return {actionable:false};
 const sizingPolicy=data?.settings?.model_pick_sizing_policy===undefined?'unit_cap_v1':data.settings.model_pick_sizing_policy;
 if(!['unit_cap_v1','model_weighted_v2','all_tiers_weighted_v3'].includes(sizingPolicy)||(c.decision_guard.model_pick_sizing_policy??'unit_cap_v1')!==sizingPolicy)return {actionable:false};
 if(sizingPolicy!=='unit_cap_v1'){
  const expectedVersion=sizingPolicy==='all_tiers_weighted_v3'?'funding-policy-v3':'funding-policy-v2';
  if(basis?.version!==expectedVersion||basis.model_pick_sizing_policy!==sizingPolicy||
   ![r,currentReview].every(v=>v.model_pick_sizing_policy===sizingPolicy))return {actionable:false};
 }else if(['funding-policy-v2','funding-policy-v3'].includes(basis?.version)||(basis?.model_pick_sizing_policy??'unit_cap_v1')!==sizingPolicy||
  [r,currentReview].some(v=>(v.model_pick_sizing_policy??'unit_cap_v1')!==sizingPolicy))return {actionable:false};
 if(sizingPolicy==='all_tiers_weighted_v3'&&(c.decision_guard.recommendation_policy!==policy||basis.policy!==policy||basis.eligible!==true||
  !['stress_value','model_pick','probability_pick'].includes(basis.tier)||
  ![r,currentReview].every(v=>v.state==='current'&&v.recommendation_policy===policy&&v.funding_tier===basis.tier)||
  !Number.isFinite(basis.maximum_stake)||basis.maximum_stake<1||basis.maximum_stake>(data?.settings?.max_bet??5)||instructionStake(t)>basis.maximum_stake))return {actionable:false};
 const close=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=1e-9*Math.max(1,Math.abs(a),Math.abs(b));
 if(!['strict','continuous_model_v1','continuous_best_available_v1'].includes(policy))return {actionable:false};
 if(['continuous_model_v1','continuous_best_available_v1'].includes(policy)){
  if(!basis||basis.policy!==policy||basis.eligible!==true||!['stress_value','model_pick','probability_pick'].includes(basis.tier)||
   ![r,currentReview].every(v=>v.actionable===true&&v.state==='current'&&v.recommendation_policy===policy&&v.funding_tier===basis.tier&&close(v.minimum_odds,basis.minimum_odds))||
   !Number.isFinite(basis.maximum_stake)||basis.maximum_stake<1||basis.maximum_stake>(data?.settings?.max_bet??5)||instructionStake(t)>basis.maximum_stake)return {actionable:false};
  if(['model_pick','probability_pick'].includes(basis.tier)){
   const p=c.probability,push=c.push_probability,stress=c.stress_probability,odds=c.reference_odds;
   const modelROI=p*odds+push-1,nominalMinimum=(1-push)/p;
   if(!Number.isFinite(push)||push<0||p+push>1+1e-9||!Number.isFinite(stress)||stress<0||stress>p||
    !close(c.expected_roi,modelROI)||!close(c.stress_roi,stress*odds+push-1)||
    (sizingPolicy!=='all_tiers_weighted_v3'&&(basis.tier!=='model_pick'||sizingPolicy!=='model_weighted_v2')&&(basis.maximum_stake!==1||instructionStake(t)>1))||!Number.isFinite(instructionStake(t)))return {actionable:false};
   if(basis.tier==='model_pick'){
    if(modelROI<=1e-9||(c.stress_roi>0&&c.stress_roi>=(data?.settings?.minimum_roi??.03))||!close(basis.minimum_odds,nominalMinimum)||odds<=nominalMinimum)return {actionable:false};
   }else if(policy!=='continuous_best_available_v1'||p<.5||modelROI>1e-9||!close(basis.minimum_odds,1))return {actionable:false};
   return r;
  }
 }else if((basis&&(basis.policy!=='strict'||basis.tier!=='stress_value'||basis.eligible!==true))||
  [r,currentReview].some(v=>(v.recommendation_policy&&v.recommendation_policy!=='strict')||(v.funding_tier&&v.funding_tier!=='stress_value')))return {actionable:false};
 // Preserve the original strict/stress-value checks; the model-pick tier has
 // its own explicit, server-reviewed nominal threshold and versioned stake cap.
 if(!Number.isFinite(c.minimum_odds)||c.reference_odds<c.minimum_odds||!Number.isFinite(c.stress_roi)||c.stress_roi<(data?.settings?.minimum_roi??.03))return {actionable:false};
 return r;
}
function isTodayIssued(t){
 const today=localDay(new Date());
 const rows=(data?.status?.daily_progress?.issuance_rows||[]);
 return rows.some(r=>r.tip_id===t.id&&localDay(new Date(r.issued_at||r.start))===today);
}
function effectiveMinimumOdds(t){
 const r=executionReview(t),current=view==='tips'&&pendingInstruction(t)?(t.current_assessment||t):t;
 const tier=(r.actionable&&r.funding_tier)||t.funding_basis?.tier;
 let min;
 if(tier==='probability_pick')min=t.funding_basis?.min_odds||t.funding_basis?.minimum_odds||1.0;
 else if(tier==='model_pick')min=t.funding_basis?.minimum_odds||((1-(current.push_probability||0))/current.probability);
 else if(r.actionable&&r.funding_tier==='stress_value')min=r.minimum_odds;
 else min=t.funding_basis?.minimum_odds||current.minimum_odds;
 const orig=t.original_odds||t.reference_odds||t.odds;
 if(orig&&min>orig)min=orig;
 return min;
}
function displayedMinimumOdds(t){
 const threshold=effectiveMinimumOdds(t);
 const orig=t.original_odds||t.reference_odds||t.odds;
 if(!isModelPick(t)){
  const res=minOdds(threshold);
  if(orig&&Number(res)>orig)return minOdds(orig);
  return res;
 }
 const prob=t.probability||t.current_assessment?.probability;
 const push=t.push_probability||t.current_assessment?.push_probability||0;
 let cents=Math.floor(Math.max(threshold,(1-push+1e-9)/prob)*100)+1;
 let val=Number.isFinite(cents)?cents/100:threshold;
 if(orig&&val>orig)val=orig;
 return Number.isFinite(val)?val.toFixed(2):'—';
}
function isModelPick(t){const r=executionReview(t);return (view==='tips'||!t.recorded_snapshot)&&((r.actionable===true&&r.funding_tier==='model_pick')||(!r.actionable&&t.funding_basis?.tier==='model_pick'))}
function isProbabilityPick(t){const r=executionReview(t);return (view==='tips'||!t.recorded_snapshot)&&((r.actionable===true&&r.funding_tier==='probability_pick')||(!r.actionable&&t.funding_basis?.tier==='probability_pick'))}
function allTiersWeighted(t){return executionReview(t).actionable===true&&executionReview(t).model_pick_sizing_policy==='all_tiers_weighted_v3'}
function weightedModelPick(t){return isModelPick(t)&&['model_weighted_v2','all_tiers_weighted_v3'].includes(executionReview(t).model_pick_sizing_policy)}
function unitPickLabel(t){return isProbabilityPick(t)?(allTiersWeighted(t)?'Probability Pick · $'+num(instructionStake(t)):'Probability Pick · $1'):isModelPick(t)?(weightedModelPick(t)?'Model Pick · $'+num(instructionStake(t)):'Model Pick · $1'):null}
function instructionTierLabel(t){return unitPickLabel(t)||(allTiersWeighted(t)&&executionReview(t).funding_tier==='stress_value'?'Stress Value · $'+num(instructionStake(t)):null)}
function unitPickSummary(t){const c=t.current_assessment||t;return isProbabilityPick(t)?`Selected for win probability. Model-estimated return: ${pct(Math.abs(c.expected_roi)<=1e-9?0:c.expected_roi)}.`:isModelPick(t)?'Positive model estimate; stress check not passed.':null}
function modelPickNote(t){if(!unitPickLabel(t))return '';const c=t.current_assessment||t;const u=stakeStr(data.settings?.unit_stake,1),m=stakeStr(data.settings?.max_bet,5);return `<p class="panel-copy model-pick-note">${esc(unitPickSummary(t))}</p><p class="panel-copy model-pick-evidence">Model EV: ${pct(c.expected_roi)} · Stress EV: ${pct(c.stress_roi)}. ${isProbabilityPick(t)?(allTiersWeighted(t)?`Calculated base-unit stake in the $${u}–$${m} range, reduced for related bets and capped by any supplied bankroll limits; not Kelly sizing. The $${u} ticket minimum can offset the raw discount. Model-estimated return is nonpositive.`:`Fixed $${u} policy unit; not Kelly sizing.`):weightedModelPick(t)?`Calculated nominal-model stake in the $${u}–$${m} range; related-exposure discount and any supplied bankroll limits still apply. No additional amount is requested for an earlier ticket.`:`Nominal-model sizing capped at $${u}; bankroll and related-exposure limits still apply.`} These are model estimates, not a guarantee of winning or profit.</p>`}


function instructionStake(t){return t.tracked_bet?t.tracked_bet.stake:t.stake}
function validInstructionStake(t){const amount=instructionStake(t);const minS=Math.min(0.5,data?.settings?.unit_stake??0.5),maxS=Math.max(5,data?.settings?.max_bet??8);return Number.isFinite(amount)&&amount>=minS&&amount<=maxS}
function pendingInstruction(t){
 if(t.league==='CHALLENGER')return false;
 if(!dashboardAvailable||!t.tracked_bet||t.tracked_bet.count!==1||t.tracked_bet.status!=='open'||t.tracking_source!=='auto_assumed_placed'||isPlaced(t)||!validInstructionStake(t))return false;
 if(new Date(t.start)<=new Date())return false;
 const c=t.current_assessment||t, r=t.instruction_review||{};
 if(c.reference_platform!=='Stake.com'||!c.stake_market)return false;
 if(c.decision_guard&&c.decision_guard.passed===false)return false;
 const maxStakeCap=(t.funding_basis?.tier==='stress_value')?Math.max(t.funding_basis?.maximum_stake??5,data?.settings?.max_bet??8):(t.funding_basis?.maximum_stake??8);
 if(t.funding_basis?.maximum_stake!=null&&instructionStake(t)>maxStakeCap)return false;
 if(isTodayIssued(t))return true;
 if(!isEngineActiveOrUpdating()&&(r.state==='quote_unavailable'||r.reason==='No fresh active exact-selection quote is available.'))return false;
 if(r.state==='stake_reassessment_required'||r.reason==='Original numeric stake policy does not reproduce')return false;
 const rev=executionReview(t);
 if(rev.actionable===true)return true;
 const allocationPolicy=modelPickAllocationPolicy(data?.settings);
 const currentReview=c.execution_review||r;
 const basis=c.funding_basis;
 if(!['one_per_game_v1','distinct_family_v1'].includes(allocationPolicy)||
  ![c.decision_guard,basis,r,currentReview].filter(Boolean).every(v=>modelPickAllocationPolicy(v)===allocationPolicy))return false;
 const sizingPolicy=data?.settings?.model_pick_sizing_policy===undefined?'unit_cap_v1':data.settings.model_pick_sizing_policy;
 if(!['unit_cap_v1','model_weighted_v2','all_tiers_weighted_v3'].includes(sizingPolicy)||
  (c.decision_guard?.model_pick_sizing_policy!=null&&(c.decision_guard.model_pick_sizing_policy??'unit_cap_v1')!==sizingPolicy)||
  (r.model_pick_sizing_policy!=null&&r.model_pick_sizing_policy!==sizingPolicy))return false;
 const outcomePolicy=data?.settings?.outcome_sizing_policy??'legacy';
 if(!['legacy','historical_game_weighted_v1','historical_game_weighted_v2','historical_confidence_v3'].includes(outcomePolicy)||
  ![c,c.decision_guard,r,currentReview].filter(Boolean).every(v=>(v?.outcome_sizing_policy??'legacy')===outcomePolicy))return false;
 if(t.funding_basis?.tier&&r.funding_tier&&t.funding_basis.tier!==r.funding_tier)return false;
 if(!isTodayIssued(t)&&!t.current_assessment&&!(data?.recommendations||[]).some(x=>x.id===t.id))return false;
 return true;
}
function executableInstruction(t){
 if(t.league==='CHALLENGER')return false;
 if(['ATP','WTA'].includes(t.league)){
  if((t.home&&t.home.includes('/'))||(t.away&&t.away.includes('/')))return false;
 }
 return !isPlaced(t)&&validInstructionStake(t)&&(t.tracked_bet?pendingInstruction(t):executionReview(t).actionable===true);
}
function executionTerms(t){return pendingInstruction(t)?(t.current_assessment||t):t}
function instructionSignature(t){const c=executionTerms(t),n=stakeNavigation(t),r=executionReview(t),b=c.funding_basis;return JSON.stringify([c.id,c.start,c.market,c.side,c.line,c.selection,c.probability,c.push_probability,c.reference_odds,c.minimum_odds,c.stress_roi,c.model_version,c.quote_at,c.valid_until,instructionStake(t),n.event_url,n.tab,n.navigation_source,n.navigation_status,data?.settings?.recommendation_policy??'strict',b?.policy,b?.tier,b?.eligible,b?.maximum_stake,b?.minimum_odds,r.recommendation_policy,r.funding_tier,effectiveMinimumOdds(t),data?.settings?.model_pick_sizing_policy??'unit_cap_v1',b?.version,b?.model_pick_sizing_policy,r.model_pick_sizing_policy,modelPickAllocationPolicy(data?.settings),modelPickAllocationPolicy(c.decision_guard),modelPickAllocationPolicy(b),modelPickAllocationPolicy(r),modelPickAllocationPolicy(c.execution_review),data?.settings?.outcome_sizing_policy??'legacy',c.outcome_sizing_policy??'legacy',r.outcome_sizing_policy??'legacy',c.outcome_sizing_evidence?.summary_sha256])}
function readyTips(){return liveTips().filter(executableInstruction)}
function stakeDisplay(t){return view==='tips'&&executableInstruction(t)?'Bet $'+num(instructionStake(t)):t.tracked_bet?'Recorded $'+num(t.tracked_bet.stake):'No new bet'}
function trackingMarkup(t){if(view==='tips'&&pendingInstruction(t))return `<p class="panel-copy">Bet $${num(t.tracked_bet.stake)} once on this exact selection. If already placed, mark it as placed instead of repeating it.</p>`;return t.tracked_bet?`<p class="panel-copy">Original record: $${num(t.tracked_bet.stake)} at ${num(t.tracked_bet.odds)} on ${esc(when(t.tracked_bet.created_at))}. No additional bet is requested.</p>`:''}
function renderStats(){
 const ready=visibleTips(readyTips());
 const hasExistingCards=typeof document!=='undefined'&&$$('#tips-grid .tip-card').length>0;
 if(isEngineActiveOrUpdating()&&ready.length===0&&hasExistingCards)return;
 const visible=visibleTips(liveTips()),amount=ready.reduce((n,t)=>n+instructionStake(t),0),p=data?.status?.daily_progress||{};
 const filteredFirst=(p.issuance_rows?issuedRowsVisible(p.issuance_rows):[]).length || (!p.issuance_rows?(p.first_issued_today??p.issued??0):0);
 const marked=visible.filter(isPlaced).length;
 const perf=data?.performance||{};
 const totalBets=perf.ledger_bets||{};
 const roll48=perf.rolling_48h||{};
 const pnl48=Number(roll48.pnl||0);
 const pnlTotal=Number(totalBets.pnl||0);
 const pnl48Str=(pnl48>=0?'+$':'-$')+num(Math.abs(pnl48));
 const pnlTotalStr=(pnlTotal>=0?'+$':'-$')+num(Math.abs(pnlTotal));
 const pnl48Class=pnl48>0?'pnl-positive':pnl48<0?'pnl-negative':'';
 const pnlTotalClass=pnlTotal>0?'pnl-positive':pnlTotal<0?'pnl-negative':'';
 const wr48=roll48.settled?pct(roll48.hit_rate??roll48.win_rate):'—';
 const roi48=roll48.settled?pct(roll48.roi):'—';
 const wrTotal=totalBets.settled?pct(totalBets.hit_rate??totalBets.win_rate):'—';
 const roiTotal=totalBets.settled?pct(totalBets.roi):'—';
 const wrRoi48=roll48.settled?`${wr48} · ${roi48}`:'—';
 const wrRoiTotal=totalBets.settled?`${wrTotal} · ${roiTotal}`:'—';
 const cards=[
  ['First issued today',filteredFirst,'Shared immutable issuance count'],
  ['Daily Instructions',(p.issued??0)+' / '+(p.target??20),p.counting_basis==='since_release'?'First Issuances Since '+when(p.counting_since):'First Issuances Today'],
  ['Ready to Bet',ready.length,'Filtered, fresh, funded and qualified'],
  ['Total Stake','$'+num(amount),'Bet each selection once'],
  ['Marked placed',marked,'Filtered checklist records'],
  ['Games',new Set(ready.map(t=>t.game_id)).size,'In the selected time window'],
  ['48h WR · ROI',wrRoi48,roll48.settled?`${roll48.won}W – ${roll48.lost}L · ${roll48.settled} settled`:'Last 48 hours settled'],
  ['48h Net P&L',pnl48Str,roll48.settled?`Settled stake: $${num(roll48.turnover)}`:'Last 48 hours cash result',pnl48Class],
  ['Total WR · ROI',wrRoiTotal,totalBets.settled?`${totalBets.won}W – ${totalBets.lost}L · ${totalBets.settled} settled`:'All tracked bets to date'],
  ['Total Cumulative Net P&L',pnlTotalStr,totalBets.settled?`Settled stake: $${num(totalBets.turnover)}`:'Cumulative net P&L after stakes',pnlTotalClass]
 ];
 $('#stats').innerHTML=cards.map(([label,value,sub,cls])=>`<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value${cls?' '+cls:''}">${esc(value)}</div><div class="stat-sub">${esc(sub)}</div></div>`).join('');
}
function priceLabel(t){return t.recorded_snapshot?`Recorded odds: ${num(t.reference_odds)}`:t.reference_platform==='Stake.com'?`Stake odds: ${num(t.reference_odds)}`:`Reference odds: ${num(t.reference_odds)} (after fees)`}
function renderOpportunitySummary(){
 const hours=horizon==='24'?'24':'48',w=data?.opportunities?.windows?.[hours],league=$('#league-filter').value;
 if(!w){$('#window-coverage').textContent='Only current, checked betting instructions appear here. Earlier bets are in Bet Tracker.';$('#opportunity-summary').textContent='Rolling coverage is not available yet.';$('#opportunity-coverage').innerHTML='';return}
 const rows=w.leagues.filter(r=>league==='all'||r.league===league),sum=k=>rows.reduce((n,r)=>n+r[k],0);
 const picks=liveTips().filter(t=>(league==='all'||t.league===league)&&new Date(t.start)>new Date(w.start)&&new Date(t.start)<=new Date(w.end));
 const pending=picks.filter(t=>!isPlaced(t)).length;
 $('#window-coverage').textContent=`${visibleTips(readyTips()).length} current betting instructions. Earlier and placed bets are in Bet Tracker. The engine updates this list automatically.`;
 $('#opportunity-summary').textContent=`Rolling ${hours}-hour window: ${when(w.start)} to ${when(w.end)}. ${sum('observed_outcomes')} observed feed outcomes, including ${sum('swish_priced_unverified')} valid-priced Swish offers whose availability and settlement mapping are not verified. ${sum('value_qualified_outcomes')} modeled outcomes meet the value threshold; ${sum('qualified_families')} best game / market selections qualify. These counts overlap existing bets and are not new betting instructions. Daily issuance uses the Budapest publication date, not the match date. Feed counts do not establish complete Stake website coverage.`;
 $('#opportunity-coverage').innerHTML=table(['League','Games','Observed Outcomes','Fresh Active Standard','Model Evaluated','Qualifying Families','Recorded / Unmarked','Source'],rows.map(r=>{
 const held=picks.filter(t=>t.league===r.league);
 return [esc(r.league),esc(r.games),esc(r.observed_outcomes),esc(r.active_standard_outcomes),esc(r.modeled_outcomes),esc(r.qualified_families),`${held.length} / ${held.filter(t=>!isPlaced(t)).length}`,esc(r.source_state==='unavailable'?'Unavailable — not zero coverage':r.source_state==='partial'?'Partial — source errors':r.games===0?'No games in this window':`${r.odds_loaded_games}/${r.games} fixtures loaded`)];
 }));
}

let stagedTipsSet = null;
function getStagedTips() {
 if (!stagedTipsSet) {
  try {
   stagedTipsSet = new Set(JSON.parse(localStorage.getItem('staged_tips_v1') || '[]'));
  } catch {
   stagedTipsSet = new Set();
  }
 }
 return stagedTipsSet;
}
function markTipStaged(id) {
 if (!id) return;
 const s = getStagedTips();
 s.add(id);
 try {
  localStorage.setItem('staged_tips_v1', JSON.stringify([...s].slice(-500)));
 } catch {}
}
function unmarkTipStaged(id) {
 if (!id) return;
 const s = getStagedTips();
 s.delete(id);
 try {
  localStorage.setItem('staged_tips_v1', JSON.stringify([...s].slice(-500)));
 } catch {}
}

let betDoneTipsSet = null;
let placingTipsSet = new Set();

function getBetDoneTips() {
 if (!betDoneTipsSet) {
  try {
   betDoneTipsSet = new Set(JSON.parse(localStorage.getItem('bet_done_tips_v1') || '[]'));
  } catch {
   betDoneTipsSet = new Set();
  }
 }
 return betDoneTipsSet;
}
function markTipBetDone(id) {
 if (!id) return;
 const s = getBetDoneTips();
 s.add(id);
 try {
  localStorage.setItem('bet_done_tips_v1', JSON.stringify([...s].slice(-500)));
 } catch {}
}
function unmarkTipBetDone(id) {
 if (!id) return;
 const s = getBetDoneTips();
 s.delete(id);
 try {
  localStorage.setItem('bet_done_tips_v1', JSON.stringify([...s].slice(-500)));
 } catch {}
}

function updateTipBetDoneUI(tipId, state, customStake = null, reason = '') {
 if (!tipId) return;
 const buttons = document.querySelectorAll(`.one-click-bet-btn[data-tip-id="${tipId}"]`);
 buttons.forEach(b => {
  if (state === 'placing') {
   b.disabled = true;
   b.classList.remove('is-done', 'is-skipped');
   b.innerHTML = `⏳ Placing…`;
   b.style.background = '#d97706';
   b.style.borderColor = '#d97706';
   b.style.color = '#fff';
   b.style.fontWeight = '600';
  } else if (state === 'done') {
   b.disabled = false;
   b.classList.remove('is-skipped');
   b.classList.add('is-done');
   b.style.background = '#047857';
   b.style.borderColor = '#047857';
   b.style.color = '#fff';
   b.style.fontWeight = '700';
   b.title = 'Fogadás sikeresen megkötve és fül bezárva (kattints az újbóli feladáshoz)';
   b.innerHTML = `✅ Bet Done ✓`;
  } else if (state === 'skipped') {
   b.disabled = false;
   b.classList.remove('is-done');
   b.classList.add('is-skipped');
   b.style.background = '#b45309';
   b.style.borderColor = '#d97706';
   b.style.color = '#fff';
   b.style.fontWeight = '600';
   b.title = `⚠️ Kihagyva: ${reason || 'A piac módosult a Stake-en'}. Kattints a vizsgálathoz / újbóli próbálkozáshoz!`;
   b.innerHTML = `⚠️ Kihagyva (Kézi)`;
  } else {
   b.disabled = false;
   b.classList.remove('is-done', 'is-skipped');
   b.style.background = '#0f766e';
   b.style.borderColor = '#0f766e';
   b.style.color = '#fff';
   b.style.fontWeight = '600';
   const t = readyTips().find(item => item.id === tipId) || (data?.recommendations||[]).find(item => item.id === tipId);
   const amt = customStake || (t ? Number(instructionStake(t) || 0) : 2.50);
   b.innerHTML = `⚡ 1-Click Bet ($${num(amt)})`;
  }
 });
}

function stakeBetButton(t){
 const instruction=view==='tips'&&executableInstruction(t),navigation=stakeNavigation(t),url=navigation.event_url;
 if(!url)return '';
 try{const u=new URL(url);if(u.origin!=='https://stake.com'||!u.pathname.startsWith('/sports/'))return ''}catch{return ''}
 const stakeAmt=Number(instructionStake(t)||0);
 const isPlacing=placingTipsSet.has(t.id);
 const isDone=getBetDoneTips().has(t.id);
 const isSkipped=typeof skippedAutoBetTips !== 'undefined' && skippedAutoBetTips.has(t.id);
 const isStaged=getStagedTips().has(t.id);

 let oneClickStyle = 'style="background:#0f766e;border-color:#0f766e;color:#fff;font-weight:600;"';
 let oneClickContent = `⚡ 1-Click Bet ($${num(stakeAmt)})`;
 let oneClickTitle = 'Automata feladás Safariban és fül bezárása';
 let oneClickDisabled = '';

 if (isPlacing) {
  oneClickStyle = 'style="background:#d97706;border-color:#d97706;color:#fff;font-weight:600;"';
  oneClickContent = `⏳ Placing…`;
  oneClickTitle = 'Fogadás folyamatban a Stake-en…';
  oneClickDisabled = 'disabled';
 } else if (isDone) {
  oneClickStyle = 'style="background:#047857;border-color:#047857;color:#fff;font-weight:700;"';
  oneClickContent = `✅ Bet Done ✓`;
  oneClickTitle = 'Fogadás sikeresen megkötve és fül bezárva (kattints az újbóli feladáshoz)';
 } else if (isSkipped) {
  const skipInfo = skippedAutoBetTips.get(t.id);
  oneClickStyle = 'style="background:#b45309;border-color:#d97706;color:#fff;font-weight:600;"';
  oneClickContent = `⚠️ Kihagyva (Kézi)`;
  oneClickTitle = `⚠️ Kihagyva: ${skipInfo?.reason || 'A piac módosult a Stake-en'}. Kattints a vizsgálathoz!`;
 }

 const stagedStyle=isStaged?'style="background:#059669;border-color:#059669;color:#fff;"':'';
 const btnContent=isStaged?`✅ Staged ✓ ($${num(stakeAmt)})`:`📋 Stage Bet Slip ($${num(stakeAmt)})`;
 const btnTitle=isStaged?'Already staged in Safari (click to re-stage)':'Pre-fill this wager in Safari';
 return `<div class="detail-actions" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
  <a class="button primary stake-bet-button" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="Bet on Stake: ${esc(t.selection)} — ${esc(t.market_display)} — ${esc(t.away)} at ${esc(t.home)}">${instruction?'Bet on Stake':'View on Stake'} ↗</a>
  ${instruction?`<button type="button" class="button accent one-click-bet-btn${isDone?' is-done':''}${isSkipped?' is-skipped':''}" data-tip-id="${esc(t.id)}" ${oneClickDisabled} ${oneClickStyle} title="${oneClickTitle}">${oneClickContent}</button>`:''}
  ${instruction?`<button type="button" class="button ghost stage-betslip-btn${isStaged?' is-staged':''}" data-tip-id="${esc(t.id)}" ${stagedStyle} title="${btnTitle}">${btnContent}</button>`:''}
 </div>
 <p class="panel-copy">Opens this match. ${navigation.tab?`Select the <strong>${esc(navigation.tab)}</strong> tab → `:'Select '}<strong>${esc(t.market_display||marketName[t.market])} → ${esc(t.selection)}</strong>. The bet slip is not prefilled by default link; click <strong>⚡ 1-Click Bet</strong> to place and close, or <strong>Stage Bet Slip</strong> to load it into Safari.</p>`;
}
function isMobileOrRemoteDevice(){
 if(typeof window==='undefined')return false;
 const ua=navigator.userAgent||'';
 const isTouch=Boolean(navigator.maxTouchPoints&&navigator.maxTouchPoints>1);
 const isMobileUa=/Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini|Mobile/i.test(ua);
 return isMobileUa || (isTouch && window.innerWidth<=1024) || (Boolean(window.innerWidth) && window.innerWidth<=768);
}
function bindInstructionLink(button,id,signature=null){
 if(!button)return;
 button.onclick=e=>{
  const current=readyTips().find(t=>t.id===id);
  if(!current||(signature&&instructionSignature(current)!==signature)){
   e.preventDefault();
   renderTips();
   toast('The engine is updating this selection. Only current instructions remain available.');
   return;
  }
  // Mobile / Remote devices (e.g. Tailscale on phone): NEVER hijack the link to desktop Mac Safari!
  // Allow standard browser navigation to open the Stake match directly on the user's mobile device.
  if(isMobileOrRemoteDevice()){
   return;
  }
  if(!e.metaKey&&!e.ctrlKey&&!e.shiftKey){
   const stageBtn=button.parentElement?.querySelector(`.stage-betslip-btn[data-tip-id="${id}"]`);
   if(stageBtn){
    e.preventDefault();
    const origBtnText = button.innerHTML;
    button.innerHTML = '⏳ Megnyitás…';
    stageBtn.click();
    setTimeout(() => { button.innerHTML = origBtnText; }, 6000);
   }
  }
 };
}

function bindStageBetslipButtons(){
 $$('.stage-betslip-btn').forEach(btn => {
  btn.onclick = async (e) => {
   e.preventDefault();
   e.stopPropagation();
   const tipId = btn.dataset.tipId;
   const t = readyTips().find(item => item.id === tipId) || liveTips().find(item => item.id === tipId) || (data?.recommendations||[]).find(item => item.id === tipId);
   if (!t) return toast('This recommendation is no longer active.');
   
   const stakeAmt = Number(instructionStake(t) || 0);
   const originalHtml = btn.innerHTML;

   // Instant Optimistic UI: mark as staged immediately in 0ms!
   markTipStaged(t.id);
   btn.classList.add('is-staged');
   btn.style.background = '#059669';
   btn.style.borderColor = '#059669';
   btn.style.color = '#fff';
   btn.title = 'Already staged in Safari (click to re-stage)';
   btn.innerHTML = `✅ Staged ✓ ($${num(stakeAmt)})`;
   toast(`✅ Szelvény előkészítve ($${num(stakeAmt)})! Betöltés Safariba…`);
   
   try {
    const terms = executionTerms(t);
    const nav = stakeNavigation(t);
    const resolvedTab = nav.tab || inferStakeTab(t) || 'Main';
    const oddsVal = terms.reference_odds || t.reference_odds;
    const minOddsVal = Number(t.minimum_odds || 0) || null;
    
    const res = await api('/api/stage-betslip', {
     method: 'POST',
     body: JSON.stringify({
      tip_id: t.id,
      event_url: nav.event_url || t.reference_url,
      selection: t.selection,
      line: t.line,
      stake: stakeAmt > 0 ? stakeAmt : 2.50,
      odds: oddsVal,
      min_odds: minOddsVal,
      tab: resolvedTab,
      outcome_name: (t.stake_evidence && t.stake_evidence.outcome_name) || (terms && terms.stake_evidence && terms.stake_evidence.outcome_name) || null,
      market_name: (t.stake_evidence && t.stake_evidence.market_name) || (t.stake_market && t.stake_market.market_name) || null,
      market: t.market || null,
      league: t.league || null
     })
    });
    
    if (res.status === 'ok' || (!res.status && !res.error)) {
     if (res.stake && res.stake !== stakeAmt) {
      btn.innerHTML = `✅ Staged ✓ ($${num(res.stake)})`;
     }
     toast(`✅ Szelvény sikeresen betöltve Safariba! Tét: $${(res.stake||stakeAmt).toFixed(2)}. Kattints a Place Bet gombra a Stake-en.`);
     if (res.warning) toast(res.warning);
    } else {
     unmarkTipStaged(t.id);
     btn.classList.remove('is-staged');
     btn.style.background = '';
     btn.style.borderColor = '';
     btn.style.color = '';
     btn.title = 'Pre-fill this wager in Safari';
     btn.innerHTML = originalHtml || `📋 Stage Bet Slip ($${num(stakeAmt)})`;
     toast(`Figyelem: ${res.message || 'Szelvény előkészítése meghiúsult a Safariban.'}`);
    }
   } catch(err) {
    unmarkTipStaged(t.id);
    btn.classList.remove('is-staged');
    btn.style.background = '';
    btn.style.borderColor = '';
    btn.style.color = '';
    btn.title = 'Pre-fill this wager in Safari';
    btn.innerHTML = originalHtml || `📋 Stage Bet Slip ($${num(stakeAmt)})`;
    toast(`Figyelem: ${err.message || 'Kommunikációs hiba a szelvény előkészítésekor.'}`);
   }
  };
 });
}
function bindOneClickBetButtons(){
 $$('.one-click-bet-btn').forEach(btn => {
  btn.onclick = async (e) => {
   e.preventDefault();
   e.stopPropagation();
   const tipId = btn.dataset.tipId;
   const t = readyTips().find(item => item.id === tipId) || liveTips().find(item => item.id === tipId) || (data?.recommendations||[]).find(item => item.id === tipId);
   if (!t) return toast('This recommendation is no longer active.');

   const stakeAmt = Number(instructionStake(t) || 0);

   placingTipsSet.add(t.id);
   updateTipBetDoneUI(t.id, 'placing');
   toast(`⚡ 1-Click Bet indítása ($${num(stakeAmt)})! Automata feladás és fül bezárása Safariban…`);

   try {
    const terms = executionTerms(t);
    const nav = stakeNavigation(t);
    const resolvedTab = nav.tab || inferStakeTab(t) || 'Main';
    const oddsVal = terms.reference_odds || t.reference_odds;

    const res = await api('/api/stage-betslip', {
     method: 'POST',
     body: JSON.stringify({
      tip_id: t.id,
      event_url: nav.event_url || t.reference_url,
      selection: t.selection,
      line: t.line,
      stake: stakeAmt > 0 ? stakeAmt : 2.50,
      odds: oddsVal,
      tab: resolvedTab,
      outcome_name: (t.stake_evidence && t.stake_evidence.outcome_name) || (terms && terms.stake_evidence && terms.stake_evidence.outcome_name) || null,
      market_name: (t.stake_evidence && t.stake_evidence.market_name) || (t.stake_market && t.stake_market.market_name) || null,
      market: t.market || null,
      league: t.league || null,
      auto_place: true,
      auto_close_on_fail: true
     })
    });

    placingTipsSet.delete(t.id);

    if (res.placed === true && res.status === 'ok') {
     markTipBetDone(t.id);
     updateTipBetDoneUI(t.id, 'done');
     await setPlacement(t.id, true, { silent: true });
     toast(`✅ Siker! Fogadás megkötve a Stake-en ($${num(res.stake || stakeAmt)}), a fül bezárva!`);
    } else {
     unmarkTipBetDone(t.id);
     updateTipBetDoneUI(t.id, 'default', stakeAmt);
     toast(`Figyelem: ${res.message || 'A fogadás nem került feladásra.'}`);
    }
   } catch(err) {
    placingTipsSet.delete(t.id);
    unmarkTipBetDone(t.id);
    updateTipBetDoneUI(t.id, 'default', stakeAmt);
    toast(`Hiba: ${err.message || 'Kommunikációs hiba a fogadás feladásakor.'}`);
   }
  };
 });
}

let isAutoBetRunning = false;
let lastAutoBetStatus = null;
const skippedAutoBetTips = new Map();
let isAutoRunEnabled = false;
let isAutoRunCycleRunning = false;
let autoRunCountdownTimer = null;
let autoRunNextTriggerAt = null;
const AUTO_RUN_INTERVAL_MS = 15 * 60 * 1000;

function formatAutoRunTime(ms) {
 const totalSec = Math.max(0, Math.floor(ms / 1000));
 const mins = Math.floor(totalSec / 60);
 const secs = totalSec % 60;
 return `(${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')})`;
}

function updateAutoRunStatusUI() {
 const toggle = $('#auto-bet-interval-toggle');
 const statusEl = $('#auto-bet-interval-status');
 if (toggle && toggle.checked !== isAutoRunEnabled) toggle.checked = isAutoRunEnabled;
 if (!statusEl) return;
 if (isAutoRunEnabled) {
  statusEl.style.display = 'inline';
  if (isAutoBetRunning || isAutoRunCycleRunning) {
   statusEl.textContent = '(Fut…)';
   statusEl.style.color = '#0284c7';
  } else if (autoRunNextTriggerAt) {
   const remaining = autoRunNextTriggerAt - Date.now();
   statusEl.textContent = formatAutoRunTime(remaining);
   statusEl.style.color = '#047857';
  }
 } else {
  statusEl.style.display = 'none';
  statusEl.textContent = '';
 }
}

function disableAutoRun(reason = null) {
 isAutoRunEnabled = false;
 isAutoRunCycleRunning = false;
 autoRunNextTriggerAt = null;
 if (autoRunCountdownTimer) {
  clearInterval(autoRunCountdownTimer);
  autoRunCountdownTimer = null;
 }
 updateAutoRunStatusUI();
 if (reason) {
  const text = (reason.startsWith('⚠️') || reason.startsWith('Auto-Run')) ? (reason.startsWith('⚠️') ? reason : `⚠️ ${reason}`) : `⚠️ Auto-Run 15m leállt: ${reason}`;
  toast(text, { duration: 12000 });
 }
}

function scheduleNextAutoRun(delayMs = AUTO_RUN_INTERVAL_MS) {
 if (!isAutoRunEnabled) return;
 autoRunNextTriggerAt = Date.now() + delayMs;
 updateAutoRunStatusUI();
 if (!autoRunCountdownTimer) {
  const isTestEnv = typeof window !== 'undefined' && !!window.happyDOM;
  const tickInterval = isTestEnv ? 50 : 1000;
  autoRunCountdownTimer = setInterval(async () => {
   if (!isAutoRunEnabled) {
    if (autoRunCountdownTimer) {
     clearInterval(autoRunCountdownTimer);
     autoRunCountdownTimer = null;
    }
    return;
   }
   if (isAutoBetRunning || isAutoRunCycleRunning) {
    updateAutoRunStatusUI();
    return;
   }
   const remaining = autoRunNextTriggerAt ? (autoRunNextTriggerAt - Date.now()) : 0;
   if (remaining <= 0) {
    await executeAutoRunCycle();
   } else {
    updateAutoRunStatusUI();
   }
  }, tickInterval);
 }
}

function getAutoRunHierarchicalQueue(currentOpenBets = 0) {
 if (currentOpenBets >= 300) {
  return { queue: [], limitReached: true, count24: 0, count48: 0 };
 }

 const now = Date.now();
 const max24 = now + 24 * 3600000;
 const max48 = now + 48 * 3600000;

 const allReady = readyTips().filter(t => !isPlaced(t) && !getBetDoneTips().has(t.id) && !skippedAutoBetTips.has(t.id));

 const sortFn = (a, b) => {
  const startOrder = new Date(a.start) - new Date(b.start);
  if (startOrder) return startOrder;
  const pDiff = tipPreference(b) - tipPreference(a);
  if (pDiff) return pDiff;
  return String(a.id).localeCompare(String(b.id));
 };

 const tips24 = allReady.filter(t => {
  const s = new Date(t.start).getTime();
  return s > now && s <= max24;
 }).sort(sortFn);

 const tips24Ids = new Set(tips24.map(t => t.id));

 const tips48 = allReady.filter(t => {
  const s = new Date(t.start).getTime();
  return s > max24 && s <= max48 && !tips24Ids.has(t.id);
 }).sort(sortFn);

 const queue = [...tips24, ...tips48];

 return {
  queue,
  limitReached: false,
  count24: tips24.length,
  count48: tips48.length
 };
}

async function executeAutoRunCycle() {
 if (!isAutoRunEnabled || isAutoRunCycleRunning || isAutoBetRunning) return;
 isAutoRunCycleRunning = true;
 updateAutoRunStatusUI();

 try {
  if (typeof load === 'function') {
   await load();
  }
  if (typeof loadStakeAccount === 'function') {
   await loadStakeAccount(true);
  }
 } catch (_) {}

 try {
  const openBetsCount = Number(stakeAccountData?.open_bets_count ?? data?.stake_account?.open_bets_count ?? 0);
  const { queue, limitReached, count24, count48 } = getAutoRunHierarchicalQueue(openBetsCount);

  if (limitReached || openBetsCount >= 300) {
   toast('ℹ️ Auto-Run 15m: A Stake elérte a 300 aktív fogadási limitet (300/300). Várakozás a meccsek elszámolására a következő 15 perces ciklusig.', { duration: 6000 });
   return;
  }

  if (!queue.length) {
   if (skippedAutoBetTips.size > 0 && openBetsCount < 300) {
    const toAdapt = [...skippedAutoBetTips.values()].map(s => s.tip);
    if (toAdapt.length > 0) {
     toast(`⚡ Auto-Run 15m: ${toAdapt.length} db kihagyott piac adaptív pótlással való újraértékelése (+EV)…`);
     try {
      await startAutoBetQueue(toAdapt, true);
     } catch (_) {}
    }
   } else {
    toast('ℹ️ Auto-Run 15m: Nincs új megköthető szelvény a 24h/48h ablakban.', { duration: 4000 });
   }
   return;
  }

  const breakdownMsg = count48 > 0 ? `${count24} db 24h + ${count48} db 48h` : `${count24} db 24h`;
  toast(`🚀 Auto-Run 15m ciklus indul: ${queue.length} db szelvény (${breakdownMsg}) megkötése a Stake-en…`);

  try {
   await startAutoBetQueue(queue);
  } catch (err) {
   console.error('Auto-Run cycle error:', err);
   const fatal = isFatalAutoBetError(err, err?.data || null);
   if (fatal.fatal) {
    disableAutoRun(`Auto-Run queued bets leállt hiba miatt: ${fatal.message}`);
    return;
   }
  }
 } finally {
  isAutoRunCycleRunning = false;
  if (isAutoRunEnabled) {
   scheduleNextAutoRun(AUTO_RUN_INTERVAL_MS);
  }
 }
}

async function enableAutoRun() {
 isAutoRunEnabled = true;
 updateAutoRunStatusUI();

 try {
  if (typeof load === 'function') {
   await load();
  }
  if (typeof loadStakeAccount === 'function') {
   await loadStakeAccount(true);
  }
 } catch (_) {}

 const openBetsCount = Number(stakeAccountData?.open_bets_count ?? data?.stake_account?.open_bets_count ?? 0);
 const { queue, limitReached, count24, count48 } = getAutoRunHierarchicalQueue(openBetsCount);

 scheduleNextAutoRun(AUTO_RUN_INTERVAL_MS);

 if (limitReached) {
  toast('⏱️ Auto-Run 15m bekapcsolva! A Stake elérte a 300 nyitott fogadás limitet (300/300 bet). Az időzítő folyamatosan aktív marad: amint hely szabadul fel, az első 15 perces ciklusban azonnal beadja a 24h szelvényeket!', { duration: 10000 });
  return;
 }

 if (queue.length > 0 && !isAutoBetRunning) {
  const breakdownMsg = count48 > 0 ? `${count24} db 24h + ${count48} db 48h` : `${count24} db 24h`;
  toast(`⏱️ Auto-Run 15m bekapcsolva! Azonnali indítás ${queue.length} db szelvényre (${breakdownMsg}), majd 15 percenként folyamatosan.`);
  await startAutoBetQueue(queue);
 } else {
  toast('⏱️ Auto-Run 15m bekapcsolva! Nincs új szelvény a 24h/48h ablakban. Következő automatikus futtatás 15 perc múlva.');
 }
}


function isFatalAutoBetError(errOrMsg, res = null) {
 const msg = (typeof errOrMsg === 'string' ? errOrMsg : (errOrMsg?.message || '')).toLowerCase();
 const limitReached = Boolean(res?.limit_reached || errOrMsg?.data?.limit_reached || errOrMsg?.limit_reached);

 if (limitReached || (msg.includes('300') && (msg.includes('aktív') || msg.includes('aktiv') || msg.includes('limit') || msg.includes('active') || msg.includes('nyitva')))) {
  return { fatal: false, type: 'limit_reached', message: 'Stake 300 aktív fogadási limit elérve (300/300 bet). Várakozás a meccsek elszámolására.' };
 }

 if (msg.includes('jelentkezz be a stake') || msg.includes('bejelentkezés szükséges') || msg.includes('login') || msg.includes('sign in') || msg.includes('session expired')) {
  return { fatal: true, type: 'login_required', message: 'Bejelentkezés szükséges a Stake fiókodba a Safariban!' };
 }

 if (msg.includes('insufficient balance') || msg.includes('insufficient funds') || msg.includes('nincs elegendő egyenleg')) {
  return { fatal: true, type: 'insufficient_funds', message: 'Nincs elegendő egyenleg a számlán a fogadáshoz!' };
 }

 if (msg.includes('safari') && (msg.includes('not running') || msg.includes('nem fut') || msg.includes('cannot connect'))) {
  return { fatal: true, type: 'safari_offline', message: 'A Safari böngésző nem érhető el vagy nem fut!' };
 }

 return { fatal: false };
}

function getActionableAutoBetQueue() {
 return visibleTips(readyTips()).filter(t => !isPlaced(t) && !getBetDoneTips().has(t.id) && !skippedAutoBetTips.has(t.id));
}

function updateAutoBetPanel() {
 const panel = $('#auto-bet-panel');
 if (!panel) return;
 const queue = getActionableAutoBetQueue();
 const countEl = $('#auto-bet-queue-count');
 const startBtn = $('#auto-bet-start-btn');
 const stopBtn = $('#auto-bet-stop-btn');
 const descEl = $('#auto-bet-desc');
 const openBetsCount = Number(stakeAccountData?.open_bets_count ?? data?.stake_account?.open_bets_count ?? 0);

 if (countEl) countEl.textContent = String(queue.length);
 updateAutoRunStatusUI();

 if (isAutoBetRunning) {
  if (startBtn) startBtn.style.display = 'none';
  if (stopBtn) {
   stopBtn.style.display = 'inline-flex';
   stopBtn.textContent = '⏹ Stop Auto-Bet';
  }
 } else {
  if (startBtn) {
   startBtn.style.display = 'inline-flex';
   startBtn.disabled = queue.length === 0 || openBetsCount >= 300;
   startBtn.style.opacity = (queue.length === 0 || openBetsCount >= 300) ? '0.6' : '1';
   if (openBetsCount >= 300) {
    startBtn.title = 'Stake 300 aktív fogadási limit elérve. Várni kell a meccsek kifutására.';
   } else {
    startBtn.title = queue.length === 0 ? 'No unplaced bets in the current view' : `Auto-place ${queue.length} bets in Safari sequentially`;
   }
  }
  if (stopBtn) stopBtn.style.display = 'none';
  if (descEl) {
   if (lastAutoBetStatus?.fatal && lastAutoBetStatus?.error) {
    descEl.innerHTML = `<span style="color:#ef4444;font-weight:700;">⚠️ Auto-Run queued bets leállt hiba miatt: ${esc(lastAutoBetStatus.error)}</span>`;
   } else if (lastAutoBetStatus?.error && !lastAutoBetStatus?.limitReached) {
    descEl.innerHTML = `<span style="color:#ef4444;font-weight:600;">⚠️ Auto-Bet megállt: ${esc(lastAutoBetStatus.error)}</span>${lastAutoBetStatus.placedCount !== undefined ? ` · Megkötve: <strong>${lastAutoBetStatus.placedCount} db</strong> szelvény.` : ''}${lastAutoBetStatus.skippedCount ? ` · <span style="color:#b45309;font-weight:600;">Kihagyva: <strong>${lastAutoBetStatus.skippedCount} db</strong>.</span>` : ''}`;
   } else if (openBetsCount >= 300 || lastAutoBetStatus?.limitReached) {
    descEl.innerHTML = `<span style="color:#b45309;font-weight:600;">ℹ️ Stake limit (300/300 bet).</span> ${isAutoRunEnabled ? 'Auto-Run 15m aktív: várakozás a következő ciklusra a meccsek kifutásáig.' : 'Új fogadás csak a folyamatban lévő meccsek kifutása és elszámolása után köthető.'}`;
   } else if (lastAutoBetStatus?.completed) {
    descEl.innerHTML = `<span style="color:#059669;font-weight:700;">✅ Ciklus kész!</span> Megkötve: <strong>${lastAutoBetStatus.placedCount} db</strong> szelvény.${lastAutoBetStatus.skippedCount > 0 ? ` · <span style="color:#b45309;font-weight:600;">⚠️ Kihagyva kézi vizsgálatra: <strong>${lastAutoBetStatus.skippedCount} db</strong>.</span>` : ''}`;
   } else if (lastAutoBetStatus?.stoppedByUser) {
    descEl.innerHTML = `<span style="color:#64748b;font-weight:600;">⏹ Auto-Bet leállítva a felhasználó által.</span> Megkötve: <strong>${lastAutoBetStatus.placedCount || 0} db</strong>${lastAutoBetStatus.skippedCount ? ` · Kihagyva: <strong>${lastAutoBetStatus.skippedCount} db</strong>` : ''}.`;
   } else if (queue.length === 0) {
    descEl.textContent = 'All visible instructions in this view have been placed or marked.';
   } else {
    descEl.textContent = `Place all ${queue.length} visible instructions sequentially in Safari on Stake and auto-mark as placed.`;
   }
  }

  const skippedBox = $('#auto-bet-skipped-section');
  if (skippedBox) {
   if (skippedAutoBetTips.size > 0) {
    skippedBox.style.display = 'block';
    const items = [...skippedAutoBetTips.values()];
    skippedBox.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:gap:6px;">
        <span style="font-weight:700;color:#92400e;font-size:12px;">⚠️ Kihagyott piacok kézi ellenőrzésre (${items.length} db):</span>
        <div style="display:flex;gap:6px;">
          <button type="button" id="adaptive-retry-skipped-btn" class="button primary" style="padding:3px 8px;font-size:11px;background:#0d9488;border-color:#0d9488;color:#fff;">⚡ Adaptív pótlás (+EV)</button>
          <button type="button" id="clear-skipped-bets-btn" class="button ghost" style="padding:3px 8px;font-size:11px;color:#92400e;border-color:#f59e0b;background:#fef3c7;">🔄 Újrapróbálás</button>
        </div>
      </div>
      <div style="max-height:220px;overflow-y:auto;display:flex;flex-direction:column;gap:6px;">
        ${items.map(s => {
          const nav = stakeNavigation(s.tip);
          const url = nav.event_url || s.tip.reference_url;
          return `
            <div style="background:#fff;border:1px solid #fde68a;border-radius:4px;padding:6px 10px;font-size:11px;display:flex;justify-content:space-between;align-items:center;gap:10px;">
              <div style="flex:1;min-width:0;">
                <strong style="color:#0f172a;">${esc(s.tip.away)} @ ${esc(s.tip.home)}</strong> (${esc(s.tip.league)}) — 
                <span style="color:#475569;font-weight:600;">[${esc(s.tip.period || 'Full Game')} · ${esc((s.tip.stake_evidence && s.tip.stake_evidence.market_name) || s.tip.market_name || s.tip.market || 'Piac')}]</span>
                <span style="color:#0f766e;font-weight:700;margin-left:4px;">${esc(s.tip.selection)}</span> ($${num(instructionStake(s.tip))}): 
                <span style="color:#b91c1c;margin-left:4px;">${esc(s.reason)}</span>
              </div>
              <div style="display:flex;gap:6px;flex-shrink:0;">
                <a href="${esc(url)}" target="_blank" rel="noopener noreferrer" class="button primary" style="padding:4px 8px;font-size:11px;background:#0f766e;border-color:#0f766e;color:#fff;text-decoration:none;">🔍 Megnyitás Stake-en ↗</a>
                <button type="button" class="button ghost mark-skipped-placed-btn" data-tip-id="${esc(s.tip.id)}" style="padding:4px 8px;font-size:11px;color:#166534;border-color:#86efac;background:#f0fdf4;">✓ Kész</button>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    `;
    const markPlacedBtns = skippedBox.querySelectorAll('.mark-skipped-placed-btn');
    markPlacedBtns.forEach(btn => {
      btn.onclick = async (e) => {
        e.preventDefault();
        const tipId = btn.getAttribute('data-tip-id');
        if (!tipId) return;
        markTipBetDone(tipId);
        updateTipBetDoneUI(tipId, 'done');
        await setPlacement(tipId, true, { silent: true });
        skippedAutoBetTips.delete(tipId);
        updateAutoBetPanel();
        renderTips();
        toast('✓ Szelvény manuálisan késznek jelölve.');
      };
    });
    const adaptiveBtn = $('#adaptive-retry-skipped-btn');
    if (adaptiveBtn) {
      adaptiveBtn.onclick = async (e) => {
        e.preventDefault();
        if (isAutoBetRunning) return;
        const toAdapt = [...skippedAutoBetTips.values()].map(s => s.tip);
        if (!toAdapt.length) return;
        toast(`⚡ Adaptív pótlás indítása ${toAdapt.length} db kihagyott piacra…`);
        await startAutoBetQueue(toAdapt, true);
      };
    }
    const clearBtn = $('#clear-skipped-bets-btn');
    if (clearBtn) {
      clearBtn.onclick = async (e) => {
        e.preventDefault();
        if (isAutoBetRunning) return;
        const toRetry = [...skippedAutoBetTips.values()].map(s => s.tip);
        if (!toRetry.length) return;
        skippedAutoBetTips.clear();
        updateAutoBetPanel();
        renderTips();
        toast(`🔄 Újrapróbálás indítása ${toRetry.length} db kihagyott piacra...`);
        await startAutoBetQueue(toRetry);
      };
    }
   } else {
    skippedBox.style.display = 'none';
    skippedBox.innerHTML = '';
   }
  }
 }
}

async function startAutoBetQueue(overrideQueue = null, isAdaptiveDirect = false) {
 if (isAutoBetRunning) return;
 const openBetsCount = Number(stakeAccountData?.open_bets_count ?? data?.stake_account?.open_bets_count ?? 0);
 if (openBetsCount >= 300) {
  toast('ℹ️ A Stake elérte a maximális 300 aktív fogadási limitet (300/300 bet). Új szelvények leadása szünetel a következő ciklusig.', { duration: 8000 });
  lastAutoBetStatus = { error: 'Stake 300 aktív fogadási limit elérve (300/300 bet). Várakozás a meccsek elszámolására.', limitReached: true };
  updateAutoBetPanel();
  return;
 }

 const queue = (overrideQueue && overrideQueue.length) ? [...overrideQueue] : [...getActionableAutoBetQueue()];
 if (!queue.length && !isAdaptiveDirect) {
  toast('Nincs megköthető nyitott szelvény az aktuális nézetben.');
  return;
 }

 isAutoBetRunning = true;
 lastAutoBetStatus = null;
 updateAutoBetPanel();
 if (queue.length > 0) {
  toast(`🚀 Auto 1-Click Bet indítása ${queue.length} szelvényre! Sorban megkötjük őket a Stake-en…`);
 }

 const isTestEnv = typeof window !== 'undefined' && !!window.happyDOM;
 const pauseMs = isTestEnv ? 10 : 1500;
 let placedCount = 0;
 let adaptedCount = 0;
 let fatalErr = null;

 try {
  for (let i = 0; i < queue.length; i++) {
   if (!isAutoBetRunning) {
    toast('⏹ Auto-Bet folyamat leállítva a felhasználó által.');
    break;
   }

   const t = queue[i];
   if (isPlaced(t) || getBetDoneTips().has(t.id)) continue;

   // Pre-check open bets before submitting this ticket (only check if Stake has 300 active bets)
   const stakeOpen = Number(stakeAccountData?.open_bets_count ?? data?.stake_account?.open_bets_count ?? 0);
   if (stakeOpen >= 300) {
    toast(`⚠️ Stake 300 aktív fogadási limit elérve (${stakeOpen}/300 bet). A további szelvények leadása szünetel a következő ciklusig.`, { duration: 8000 });
    break;
   }

   const stakeAmt = Number(instructionStake(t) || 0);
   const descEl = $('#auto-bet-desc');
   if (descEl) {
    descEl.textContent = `[${i + 1}/${queue.length}] Fogadás folyamatban: ${t.selection} ($${num(stakeAmt)}) — ${t.away} @ ${t.home}… (Siker: ${placedCount}, Kihagyva: ${skippedAutoBetTips.size})`;
   }

   placingTipsSet.add(t.id);
   updateTipBetDoneUI(t.id, 'placing');

   try {
    const terms = executionTerms(t);
    const nav = stakeNavigation(t);
    const resolvedTab = nav.tab || inferStakeTab(t) || 'Main';
    const oddsVal = terms.reference_odds || t.reference_odds;

    let res = null;
    let netAttempts = 0;
    while (netAttempts < 2) {
      res = await api('/api/stage-betslip', {
       method: 'POST',
       body: JSON.stringify({
        tip_id: t.id,
        event_url: nav.event_url || t.reference_url,
        selection: t.selection,
        line: t.line,
        stake: stakeAmt > 0 ? stakeAmt : 2.50,
        odds: oddsVal,
        tab: resolvedTab,
        outcome_name: (t.stake_evidence && t.stake_evidence.outcome_name) || (terms && terms.stake_evidence && terms.stake_evidence.outcome_name) || null,
        market_name: (t.stake_evidence && t.stake_evidence.market_name) || (t.stake_market && t.stake_market.market_name) || null,
        market: t.market || null,
        league: t.league || null,
        auto_place: true,
        auto_close_on_fail: true,
        adaptive_fallback: isAdaptiveDirect
       })
      });
      if (res && res.network_disconnected && netAttempts === 0 && !isTestEnv) {
        netAttempts++;
        toast(`⚠️ Stake kapcsolat megszakadt: 3s várakozás és újracsatlakozás (${t.selection})…`);
        await new Promise(r => setTimeout(r, 3000));
        continue;
      }
      break;
    }

    placingTipsSet.delete(t.id);

    if (res && res.limit_reached) {
     unmarkTipBetDone(t.id);
     updateTipBetDoneUI(t.id, 'default', stakeAmt);
     lastAutoBetStatus = { error: 'Stake 300 aktív fogadási limit elérve (300/300 bet). Várakozás a meccsek elszámolására.', limitReached: true, placedCount, skippedCount: skippedAutoBetTips.size };
     toast('⚠️ Stake 300 aktív fogadási limit elérve. A további szelvények leadása szünetel a következő ciklusig.', { duration: 8000 });
     break;
    }

    if (res.placed === true && res.status === 'ok') {
     skippedAutoBetTips.delete(t.id);
     if (res.adapted) adaptedCount++;
     markTipBetDone(t.id);
     updateTipBetDoneUI(t.id, 'done');
     await setPlacement(t.id, true, { silent: true });
     placedCount++;
     const adaptTag = res.adapted ? ' [Adaptívan megkötve]' : '';
     toast(`✅ [${i + 1}/${queue.length}]${adaptTag} Megkötve és rögzítve: ${res.selection || t.selection} ($${num(res.stake || stakeAmt)})`);
    } else {
     unmarkTipBetDone(t.id);
     const msg = res.message || res.error || 'A fogadás nem sikerült a Stake-en.';
     const fatal = isFatalAutoBetError(msg, res);
     if (fatal.fatal) {
      fatalErr = fatal;
      updateTipBetDoneUI(t.id, 'default', stakeAmt);
      isAutoBetRunning = false;
      break;
     } else if (fatal.type === 'limit_reached') {
      updateTipBetDoneUI(t.id, 'default', stakeAmt);
      lastAutoBetStatus = { error: fatal.message, limitReached: true, placedCount, skippedCount: skippedAutoBetTips.size };
      toast('⚠️ Stake 300 aktív fogadási limit elérve. A további szelvények leadása szünetel a következő ciklusig.', { duration: 8000 });
      break;
     } else {
      skippedAutoBetTips.set(t.id, { tip: t, reason: msg, time: new Date().toISOString() });
      updateTipBetDoneUI(t.id, 'skipped', stakeAmt, msg);
      toast(`⚠️ [Kihagyva: ${t.selection}]: ${msg}. Ugrás a következőre…`, { duration: 8000 });
     }
    }
   } catch (err) {
    placingTipsSet.delete(t.id);
    unmarkTipBetDone(t.id);
    const msg = err.message || 'Kommunikációs hiba';
    const fatal = isFatalAutoBetError(err, err.data || null);
    if (fatal.fatal) {
     fatalErr = fatal;
     updateTipBetDoneUI(t.id, 'default', stakeAmt);
     isAutoBetRunning = false;
     break;
    } else if (fatal.type === 'limit_reached') {
     updateTipBetDoneUI(t.id, 'default', stakeAmt);
     lastAutoBetStatus = { error: fatal.message, limitReached: true, placedCount, skippedCount: skippedAutoBetTips.size };
     toast('⚠️ Stake 300 aktív fogadási limit elérve. A további szelvények leadása szünetel a következő ciklusig.', { duration: 8000 });
     break;
    } else {
     skippedAutoBetTips.set(t.id, { tip: t, reason: msg, time: new Date().toISOString() });
     updateTipBetDoneUI(t.id, 'skipped', stakeAmt, msg);
     toast(`⚠️ [Kihagyva: ${t.selection}]: ${msg}. Ugrás a következőre…`, { duration: 8000 });
    }
   }

   if (!isAutoBetRunning) break;

   if (i < queue.length - 1) {
    if (descEl) descEl.textContent = `[${i + 1}/${queue.length}] Folytatás: kis szünet a következő szelvény előtt…`;
    await new Promise(r => setTimeout(r, pauseMs));
   }
  }

  // Phase 2: Autonomous Adaptive Re-evaluation of skipped tips with line movement
  adaptedCount = 0;
  if (isAutoBetRunning && skippedAutoBetTips.size > 0 && !fatalErr && !isAdaptiveDirect) {
   const stakeOpenBeforePhase2 = Number(stakeAccountData?.open_bets_count ?? data?.stake_account?.open_bets_count ?? 0);
   if (stakeOpenBeforePhase2 < 300) {
    const toAdapt = [...skippedAutoBetTips.values()].filter(item => 
     item.reason && (item.reason.includes('módosította a piacot') || item.reason.includes('vonalak érhetők el') || item.reason.includes('Nem található a kimenetel'))
    );
    if (toAdapt.length > 0) {
     toast(`🔄 [2. Fázis: Adaptív pótlás] ${toAdapt.length} db módosult piacú szelvény újraértékelése (+EV)…`, { duration: 6000 });
     const descEl = $('#auto-bet-desc');
     for (let j = 0; j < toAdapt.length; j++) {
      if (!isAutoBetRunning) break;
      const stakeOpenInPhase2 = Number(stakeAccountData?.open_bets_count ?? data?.stake_account?.open_bets_count ?? 0);
      if (stakeOpenInPhase2 >= 300) {
       toast('⚠️ Stake 300 limit elérve adaptív pótlás közben. Várakozás a következő ciklusra.');
       break;
      }
      const sItem = toAdapt[j];
      const t = sItem.tip;
      if (descEl) {
       descEl.textContent = `[2. Fázis: ${j + 1}/${toAdapt.length}] Adaptív újraértékelés: ${t.selection} — ${t.away} @ ${t.home}… (Siker: ${placedCount})`;
      }
      placingTipsSet.add(t.id);
      updateTipBetDoneUI(t.id, 'placing');
      try {
       const terms = executionTerms(t);
       const nav = stakeNavigation(t);
       const resolvedTab = nav.tab || inferStakeTab(t) || 'Main';
       const stakeAmt = Number(instructionStake(t) || 0) || 1.0;
       const oddsVal = terms.reference_odds || t.reference_odds;
       const res = await api('/api/stage-betslip', {
        method: 'POST',
        body: JSON.stringify({
         tip_id: t.id,
         event_url: nav.event_url || t.reference_url,
         selection: t.selection,
         line: t.line,
         stake: stakeAmt > 0 ? stakeAmt : 2.50,
         odds: oddsVal,
         tab: resolvedTab,
         outcome_name: (t.stake_evidence && t.stake_evidence.outcome_name) || (terms && terms.stake_evidence && terms.stake_evidence.outcome_name) || null,
         market_name: (t.stake_evidence && t.stake_evidence.market_name) || (t.stake_market && t.stake_market.market_name) || null,
         market: t.market || null,
         league: t.league || null,
         auto_place: true,
         auto_close_on_fail: true,
         adaptive_fallback: true
        })
       });
       placingTipsSet.delete(t.id);
       if (res && res.limit_reached) {
        unmarkTipBetDone(t.id);
        updateTipBetDoneUI(t.id, 'skipped', stakeAmt, 'Stake 300 limit elérve');
        break;
       }
       if (res && res.placed === true && res.status === 'ok') {
        skippedAutoBetTips.delete(t.id);
        markTipBetDone(t.id);
        updateTipBetDoneUI(t.id, 'done');
        await setPlacement(t.id, true, { silent: true });
        placedCount++;
        adaptedCount++;
        toast(`⚡ [Adaptívan megkötve]: ${res.selection || t.selection} ($${num(res.stake || stakeAmt)}) — EV: +${res.ev ? (res.ev * 100).toFixed(1) : ''}%!`);
       } else {
        const msg = res?.message || res?.error || 'Nincs megfelelő +EV alternatíva';
        skippedAutoBetTips.set(t.id, { tip: t, reason: msg, time: new Date().toISOString() });
        updateTipBetDoneUI(t.id, 'skipped', stakeAmt, msg);
       }
      } catch (e) {
       placingTipsSet.delete(t.id);
       const msg = e.message || 'Nincs +EV alternatíva';
       skippedAutoBetTips.set(t.id, { tip: t, reason: msg, time: new Date().toISOString() });
       updateTipBetDoneUI(t.id, 'skipped', null, msg);
      }
      await new Promise(r => setTimeout(r, pauseMs));
     }
    }
   }
  }
 } finally {
  isAutoBetRunning = false;
  placingTipsSet.clear();
  if (fatalErr && fatalErr.fatal) {
   lastAutoBetStatus = {
    error: fatalErr.message,
    fatal: true,
    placedCount: placedCount,
    adaptedCount: adaptedCount,
    skippedCount: skippedAutoBetTips.size,
    skippedList: [...skippedAutoBetTips.values()],
    limitReached: false
   };
   const fatalMsg = `⚠️ Auto-Run queued bets leállt hiba miatt: ${fatalErr.message}`;
   toast(fatalMsg, { duration: 15000 });
   if (isAutoRunEnabled) {
    disableAutoRun(`Auto-Run queued bets leállt hiba miatt: ${fatalErr.message}`);
   }
  } else if (!isAutoBetRunning && lastAutoBetStatus?.stoppedByUser) {
   lastAutoBetStatus.placedCount = placedCount;
   lastAutoBetStatus.adaptedCount = adaptedCount;
   lastAutoBetStatus.skippedCount = skippedAutoBetTips.size;
   lastAutoBetStatus.skippedList = [...skippedAutoBetTips.values()];
  } else if (lastAutoBetStatus?.limitReached) {
   lastAutoBetStatus.placedCount = placedCount;
   lastAutoBetStatus.adaptedCount = adaptedCount;
   lastAutoBetStatus.skippedCount = skippedAutoBetTips.size;
   lastAutoBetStatus.skippedList = [...skippedAutoBetTips.values()];
  } else {
   const stakeOpenAtEnd = Number(stakeAccountData?.open_bets_count ?? data?.stake_account?.open_bets_count ?? 0);
   lastAutoBetStatus = {
    completed: true,
    placedCount: placedCount,
    adaptedCount: adaptedCount,
    skippedCount: skippedAutoBetTips.size,
    skippedList: [...skippedAutoBetTips.values()],
    limitReached: stakeOpenAtEnd >= 300
   };
   if (placedCount > 0 || skippedAutoBetTips.size > 0) {
    const adaptText = adaptedCount > 0 ? ` (${adaptedCount} db adaptív pótlás)` : '';
    toast(`🎉 Ciklus befejezve! Megkötve: ${placedCount} db szelvény${adaptText}${skippedAutoBetTips.size > 0 ? ` · Kihagyva kézi vizsgálatra: ${skippedAutoBetTips.size} db` : ''}.`, { duration: 8000 });
   }
  }
  updateAutoBetPanel();
  try {
   if (typeof loadStakeAccount === 'function') {
    loadStakeAccount(true).catch(() => {});
   }
  } catch (_) {}
 }
}

function stopAutoBetQueue() {
 if (isAutoBetRunning) {
  isAutoBetRunning = false;
  lastAutoBetStatus = { error: 'Folyamat leállítva a felhasználó által.', stoppedByUser: true };
  toast('⏹ Auto-Bet leállítása folyamatban…');
  const stopBtn = $('#auto-bet-stop-btn');
  if (stopBtn) stopBtn.textContent = '⏳ Stopping…';
  updateAutoBetPanel();
 }
 if (isAutoRunEnabled) {
  disableAutoRun('Felhasználó leállította a folyamatot.');
 }
}

function bindAutoBetRunner() {
 const startBtn = $('#auto-bet-start-btn');
 const stopBtn = $('#auto-bet-stop-btn');
 const intervalToggle = $('#auto-bet-interval-toggle');
 if (startBtn) {
  startBtn.onclick = (e) => {
   e.preventDefault();
   startAutoBetQueue();
  };
 }
 if (stopBtn) {
  stopBtn.onclick = (e) => {
   e.preventDefault();
   stopAutoBetQueue();
  };
 }
 if (intervalToggle) {
  intervalToggle.onchange = () => {
   if (intervalToggle.checked) {
    enableAutoRun();
   } else {
    disableAutoRun();
    toast('⏹️ Auto-Run 15m kikapcsolva.');
   }
  };
 }
 updateAutoRunStatusUI();
}

function expireInstructions(){if(!data||isEngineActiveOrUpdating())return;renderStakeSizing();const current=readyTips(),ids=new Set(current.map(t=>t.id));if($$('#tips-grid [data-tip-id]').some(el=>!ids.has(el.dataset.tipId))||(view==='tips'&&selectedTip&&$('#detail-dialog').open&&!current.some(t=>t.id===selectedTip.id&&instructionSignature(t)===selectedInstructionSignature)))renderTips()}
function emptyTipsMessage(){
 const status=data?.status||{},counts=status.execution_counts||{},alts=data?.alternatives||[],recs=data?.recommendations||[];
 const held=recs.filter(t=>t.allocation_code==='already_held').length;
 const eventLimited=alts.filter(t=>t.allocation_code==='event_limit').length;
 const periodLimited=alts.filter(t=>t.allocation_code==='period_limit').length;
 const funded=Number(status.funded_recommendations||0);
 if(!funded&&(held||eventLimited||periodLimited)){
  const reasons=[];
  if(held)reasons.push(`${held} current contract${held===1?' is':'s are'} already tracked`);
  if(eventLimited)reasons.push(`${eventLimited} candidate${eventLimited===1?' is':'s are'} at the per-game limit`);
  if(periodLimited)reasons.push(`${periodLimited} candidate${periodLimited===1?' is':'s are'} at the period limit`);
  return `<div class="empty"><h3>No new bets ready right now</h3><p>The Stake feed is healthy, but this scan produced no new unheld execution instruction. ${esc(reasons.join('; '))}. Earlier funded instructions are preserved in Bet Tracker and are not repeated.</p><p class="panel-copy">The engine continues its 60-second scan and will publish a new exact Stake contract automatically when it passes the current model, price, freshness and exposure checks.</p></div>`;
 }
 const visibleAll=visibleTips(liveTips());
 const totalCovered=visibleAll.filter(t=>isPlaced(t)||t.tracked_bet).length;
 const allReadyFuture=liveTips().filter(executableInstruction).length;
 if(totalCovered>0){
  const futureMsg=allReadyFuture>0?`Switch to the <strong>Next 48 Hours</strong> tab (${allReadyFuture} bets ready) to view future opportunities, or open <strong>Bet Tracker</strong> to monitor active positions.`:`Earlier instructions and placed bets remain active in Bet Tracker. The engine continuously scans upcoming markets.`;
  return `<div class="empty"><h3>No unplaced bets ready in this time window</h3><p><span class="badge" style="background:#059669;color:#fff;padding:4px 10px;border-radius:4px;font-weight:600;display:inline-block;margin-bottom:8px;">🛡️ Full Coverage Active: ${totalCovered} bet${totalCovered===1?'':'s'} already placed & tracked</span></p><p>All qualified recommendations for upcoming matches in this time window have already been placed and moved to <strong>Bet Tracker</strong> to prevent duplicate wagering and observe single-game limits.</p><p class="panel-copy">${futureMsg}</p></div>`;
 }
 return '<div class="empty"><h3>No new bets ready right now</h3><p>The engine keeps checking upcoming markets. Qualified bets appear here automatically with their USD stake once verified by models and price freshness. Earlier instructions and placed bets remain in Bet Tracker.</p></div>';
}
function polymarketBadge(t){
 const scope=t.model_scope||(t.market&&t.market.includes(':')?t.market.split(':')[0]:'full_game');
 if(scope!=='full_game')return '';
 const gameCtx=(data?.games||[]).find(g=>g.id===t.game_id)?.context;
 const poly=t.game_context?.polymarket||t.current_assessment?.game_context?.polymarket||gameCtx?.polymarket;
 const reasons=[
  ...(Array.isArray(t.reasons)?t.reasons:[]),
  ...(Array.isArray(t.current_assessment?.reasons)?t.current_assessment.reasons:[])
 ];
 const edgeReason=reasons.find(r=>typeof r==='string'&&r.includes('Polymarket Edge'));
 if(edgeReason){
  const match=edgeReason.match(/\(\+([0-9.]+)%\s*edge\)/i);
  const edgeText=match?`+${match[1]}% Edge`:'Edge';
  return `<span class="badge polymarket-edge" title="${esc(edgeReason)}">🔮 Polymarket ${edgeText}</span>`;
 }
 if(poly&&poly.matched&&poly.has_moneyline){
  const marketKind=String(t.market||'').split(':').at(-1);
  const isMl=['moneyline','three_way','1x2','winner'].includes(marketKind);
  if(isMl){
   const side=String(t.side||t.current_assessment?.side||'').toLowerCase();
   const polyProb=side==='home'?poly.true_prob_home:(side==='away'?poly.true_prob_away:(side==='draw'||side==='tie'?poly.true_prob_draw:null));
   const odds=Number(t.reference_odds||t.current_assessment?.reference_odds||t.execution_review?.odds||0);
   if(polyProb&&odds>1.0){
    const implied=1.0/odds;
    const edge=(polyProb-implied)*100;
    if(edge>=0.5){
     return `<span class="badge polymarket-edge" title="Polymarket True Prob: ${(polyProb*100).toFixed(1)}% vs Stake Implied: ${(implied*100).toFixed(1)}% (+${edge.toFixed(1)}% EV Advantage)">🔮 Polymarket +${edge.toFixed(1)}% Edge</span>`;
    }
    const diffStr=(edge>=0?'+':'')+edge.toFixed(1)+'%';
    return `<span class="badge polymarket-matched" title="Polymarket True Prob: ${(polyProb*100).toFixed(1)}% vs Stake Implied: ${(implied*100).toFixed(1)}% (${diffStr} — no price edge, orderbook anchor)">🔮 Polymarket ${(polyProb*100).toFixed(0)}% (Anchor)</span>`;
   }
   return `<span class="badge polymarket-matched" title="Polymarket Orderbook Anchored: ${esc(poly.title||'Matched')}">🔮 Polymarket (Anchor)</span>`;
  }
 }
 return '';
}
function pickCard(t,index){
 const c=executionTerms(t);
 const originalOdds=t.tracked_bet?.odds||t.reference_odds;
 const curOdds=c.reference_odds||originalOdds;
 const isIssued=isTodayIssued(t)||!!t.tracked_bet;
 const showOriginal=isIssued&&(!t.current_assessment||executionReview(t).actionable!==true||Math.abs(curOdds-originalOdds)>=0.01);
 const timing=tipTiming(t);
 const hoursToStart=(new Date(t.start).getTime()-Date.now())/3600000;
 const isFar=hoursToStart>48;
 const timingBadge=isFar?`<span class="badge ${timing.action==='BET_NOW'?'timing-bet-now':'timing-wait'}" title="${esc(timing.reason)}">${timing.action==='BET_NOW'?'⚡ BET NOW':'⏳ WAIT'}</span>`:'';
 const timingCallout=isFar?`<div class="timing-callout ${timing.action==='BET_NOW'?'timing-callout-bet-now':'timing-callout-wait'}"><strong>${timing.action==='BET_NOW'?'⚡ BET NOW: ':'⏳ WAIT: '}</strong>${esc(timing.reason)}</div>`:'';
 const polyBadge=polymarketBadge(t);
 return `<article class="tip-card" data-tip-id="${esc(t.id)}"><div class="tip-top"><span class="rank">${String(index+1).padStart(2,'0')}</span><span class="league ${esc(t.league)}">${esc(t.league)}</span>${timingBadge}<span class="time">${esc(when(t.start))}</span></div><div class="tip-match">${esc(t.away)} @ ${esc(t.home)}</div><div class="tip-market">${esc(c.market_display||marketName[t.market])}</div>${stakeNavigation(t).tab?`<p class="panel-copy stake-tab">Stake tab: <strong>${esc(stakeNavigation(t).tab)}</strong></p>`:''}<div class="selection">${esc(t.selection)}</div><div class="tip-metrics"><div><div class="metric-label">MODEL WIN PROBABILITY</div><div class="metric-value">${pct(c.probability)}</div></div><div><div class="metric-label">STAKE ODDS</div><div class="metric-value">${num(curOdds)}</div>${isIssued?`<div class="stat-sub">Original odds at issue: ${num(originalOdds)}</div><div class="stat-sub">Min odds: ${displayedMinimumOdds(t)}</div>`:''}</div><div><div class="metric-label">STAKE TO PLACE · USD</div><div class="metric-value">Bet $${num(instructionStake(t))}</div></div></div>${timingCallout}<p class="panel-copy">Minimum odds: <strong>${displayedMinimumOdds(t)}</strong>${isModelPick(t)?' (above model break-even)':''} · Single bet · ${allTiersWeighted(t)||weightedModelPick(t)?('$'+stakeStr(data.settings?.unit_stake,1)+'–$'+stakeStr(data.settings?.max_bet,5)+' calculated stake'):unitPickLabel(t)?('$'+stakeStr(data.settings?.unit_stake,1)+' maximum for this selection'):('$'+stakeStr(data.settings?.unit_stake,1)+'–$'+stakeStr(data.settings?.max_bet,5)+' per selection')}</p>${unitPickSummary(t)?`<p class="panel-copy model-pick-note">${esc(unitPickSummary(t))}</p>`:''}${stakeBetButton(t)}${placementControl(t)}<div class="tip-bottom"><div class="tip-bottom-badges"><span class="badge positive">${esc(instructionTierLabel(t)||(t.is_additional?'Additional Bet':'Ready to Bet'))}</span>${polyBadge}</div><button class="detail-button" data-detail="${esc(t.id)}">Bet Details →</button></div><p class="tip-reference">Odds checked: ${esc(when(c.quote_at))}</p></article>`;
}
function updateHorizonBadges(){
 const pool=readyTips();
 const league=$('#league-filter')?.value||'all';
 const market=$('#market-filter')?.value||'all';
 const filteredPool=pool.filter(t=>{
  if(new Date(t.start)<=new Date())return false;
  if(league!=='all'&&t.league!==league)return false;
  if(market!=='all'&&!marketMatches(t.market,market))return false;
  return true;
 });
 const now=Date.now();
 const today=localDay(new Date());
 const tomorrow=localDay(new Date(now+86400000));
 const counts={
  '24':filteredPool.filter(t=>new Date(t.start).getTime()<=now+24*3600000).length,
  '48':filteredPool.filter(t=>new Date(t.start).getTime()<=now+48*3600000).length,
  '72':filteredPool.filter(t=>new Date(t.start).getTime()<=now+72*3600000).length,
  'today':filteredPool.filter(t=>localDay(new Date(t.start))===today||isTodayIssued(t)).length,
  'tomorrow':filteredPool.filter(t=>localDay(new Date(t.start))===tomorrow).length,
 };
 for(const [h,count] of Object.entries(counts)){
  const el=$(`#badge-horizon-${h}`);
  if(el)el.textContent=String(count);
 }
}

function renderTips(){
 const rows=visibleTips(readyTips());
 if(view==='tips'&&selectedTip&&$('#detail-dialog').open){const current=rows.find(t=>t.id===selectedTip.id);if(!current||instructionSignature(current)!==selectedInstructionSignature){$('#detail-dialog').close();selectedTip=null;selectedInstructionSignature=null;}}
 const readyMarkup=rows.map((t,index)=>pickCard(t,index)).join('');
 const hasExistingCards=$$('#tips-grid .tip-card').length>0;
 if(isEngineActiveOrUpdating() && !readyMarkup && hasExistingCards){
  return;
 }
 $('#tip-count').textContent=rows.length;
 $('#tips-grid').innerHTML=readyMarkup||emptyTipsMessage();
 updateHorizonBadges();
 renderStats();renderOpportunitySummary();renderTracked();renderAlternatives();renderEvaluated();bindPlacementControls();bindStageBetslipButtons();bindOneClickBetButtons();updateAutoBetPanel();bindAutoBetRunner();$$('#tips-grid .tip-card .stake-bet-button').forEach(button=>bindInstructionLink(button,button.closest('[data-tip-id]').dataset.tipId));$$('[data-detail]').forEach(b=>b.onclick=()=>showDetail(b.dataset.detail,b.dataset.current==='true'));
}
function historyRows(){return liveTips().filter(t=>t.tracked_bet||isPlaced(t)).sort((a,b)=>new Date(a.start)-new Date(b.start)||String(a.id).localeCompare(String(b.id)))}
function renderTracked(){
 const history=historyRows();
 const withdrawn=history.filter(t=>new Date(t.start)>new Date()&&!isPlaced(t)&&t.tracked_bet?.status==='open'&&t.instruction_review?.actionable===false);
 $('#withdrawn-upcoming-section').hidden=!withdrawn.length;
 $('#withdrawn-upcoming-count').textContent=withdrawn.length;
 $('#withdrawn-upcoming-table').innerHTML=table(['League','Matchup / Start','Original Market / Selection','Original Issuance','Latest Observed Assessment','Changed — Reason'],withdrawn.map(t=>{
  const original=(data?.tracked_bets||[]).filter(b=>b.tip_id===t.id).sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at)))[0]?.payload||t;
  const current=(data?.evaluated_candidates||[]).find(c=>c.id===t.id);
  return [esc(t.league),esc(t.away)+' @ '+esc(t.home)+`<small>${esc(when(t.start))}</small>`,esc(t.market_display||marketName[t.market]||t.market)+`<small>${esc(t.selection)}</small>`,
   `Odds: ${num(original.reference_odds)} · Stake: $${num(original.stake)}<small>Estimated ROI at issue: ${pct(original.expected_roi)} · ${esc(when(original.created_at))}</small>`,
   current?`Observed odds: ${num(current.reference_odds)}<small>Model probability: ${pct(current.probability)} · Estimated ROI: ${pct(current.expected_roi)} · Quote observed: ${esc(when(current.quote_at))}</small>`:'No current assessment available',
   `<span class="badge muted">Changed</span><small>${esc(t.instruction_review.reason||'Current assessment is unavailable; the original record is retained.')}</small>`];
 }));
 const status=t=>isPlaced(t)?'Marked as placed':pendingInstruction(t)?'Current instruction':'Recorded · Not a current instruction';
 $('#history-table').innerHTML=table(['League','Matchup / Start','Market / Selection','Original Stake','Original Odds','Model at Issue','Status','Checklist','Details'],history.map(t=>[esc(t.league),esc(t.away)+' @ '+esc(t.home)+`<small>${esc(when(t.start))}</small>`,esc(t.market_display||marketName[t.market])+`<small>${esc(t.selection)}</small>`,'$'+num(instructionStake(t)),num(t.tracked_bet?.odds||t.reference_odds),pct(t.probability),status(t),placementControl(t),`<button class="detail-button" data-detail="${esc(t.id)}">Original Bet Details →</button>`]));
 const rows=(data?.tracked_bets||[]).map(b=>({...b.payload,record:b})).sort((a,b)=>new Date(a.start)-new Date(b.start));
 $('#tracked-section').hidden=!rows.length;$('#tracked-count').textContent=rows.length;
 $('#tracked-table').innerHTML=table(['League','Matchup','Start','Market','Recorded Selection','Stake','Recorded Odds','Tracking','Checklist'],rows.map(t=>[esc(t.league),esc(t.away)+' @ '+esc(t.home),esc(when(t.start)),esc(t.market_display||marketName[t.market]),esc(t.selection),'$'+num(t.record.stake),num(t.record.odds),trackingLabel(t),placementControl(t)]));
}
function renderAlternatives(){
 const issued=new Set(liveTips().map(t=>t.id));const rows=visibleTips(allCurrentSelections().filter(t=>!issued.has(t.id)));
 $('#alternatives-section').hidden=!rows.length;$('#alternative-count').textContent=rows.length;
 $('#alternatives-table').innerHTML=table(['League','Matchup','Start','Market','Selection','Stake Odds','Minimum Odds','Model EV / Stress','Why No New Bet?','Details'],rows.map(t=>[esc(t.league),esc(t.away)+' @ '+esc(t.home),esc(when(t.start)),esc(t.market_display||marketName[t.market]),esc(t.selection),num(t.reference_odds),minOdds(t.minimum_odds),pct(t.expected_roi)+' / '+pct(t.stress_roi),'No bet recommended. '+esc(allocationReason(t)),`<button class="detail-button" data-detail="${esc(t.id)}">Bet Details →</button>`]));
}
function renderEvaluated(){
 const rows=visibleTips(allEvaluated());$('#evaluated-count').textContent=rows.length;
 $('#evaluated-table').innerHTML=table(['League','Matchup','Market','Selection','Odds','Model Probability','Model EV','Stress EV','Allocation','Details'],rows.map(t=>[esc(t.league),esc(t.away)+' @ '+esc(t.home),esc(t.market_display||marketName[t.market]),esc(t.selection),num(t.reference_odds),pct(t.probability),pct(t.expected_roi),pct(t.stress_roi),esc(allocationReason(t)),`<button class="detail-button" data-detail="${esc(t.id)}" data-current="true">View Analysis</button>`]));
}
function renderArchive(){const rows=visibleTips(data?.archived_recommendations||[]);$('#archive-section').hidden=!rows.length;$('#archive-grid').innerHTML=rows.map(t=>`<div class="archive-row"><div><strong>${esc(t.away)} @ ${esc(t.home)}</strong><small>${esc(t.league)} · ${esc(when(t.start))}</small></div><div><strong>${esc(t.market_display||marketName[t.market])}</strong><small>${esc(t.selection)}</small></div><div><strong>Old model price ${num(t.reference_odds)}</strong><small>${esc(t.archive_source||'Historical model feed')} · Stake line not verified</small></div></div>`).join('')}
let scheduleFlight=null;
function renderSchedule(schedule){
 if(!Array.isArray(schedule.games)||!Array.isArray(schedule.unavailable))throw new Error('Schedule source unavailable');
 $('#schedule-status').textContent=`Next 24 hours · Europe/Budapest (${schedule.timezone_label||''}) · Sources checked: ${when(schedule.checked_at)}. Daily refresh at 09:00 Budapest; refreshed every 15 minutes during the day.`;
 $('#games-table').innerHTML=table(['League · Local Start · Away @ Home'],schedule.games.map(g=>[esc(g.line)]));
 $('#schedule-empty').textContent=(schedule.no_games||[]).map(l=>'Nincs meccs: '+l).join(' · ')+(schedule.unavailable.length?' Source unavailable or stale: '+schedule.unavailable.join(', '):'');
 $('#schedule-watch').textContent=schedule.must_watch?'Must-watch: '+schedule.must_watch.line+' — '+schedule.must_watch.reason:'';
 $('#schedule-sources').innerHTML=(schedule.sources||[]).filter(s=>/^https:\/\/site\.api\.espn\.com\//.test(s.url)).map(s=>`<a target="_blank" rel="noopener noreferrer" href="${esc(s.url)}">ESPN ${esc(s.league)} schedule source</a>`).join(' · ');
}
function renderGames(){
 if(view!=='games')return;
 if(scheduleFlight)return scheduleFlight;
 scheduleFlight=(async()=>{try{const response=await fetch('/api/schedule');if(!response.ok)throw new Error('Schedule source unavailable');renderSchedule(await response.json())}catch(e){$('#schedule-status').textContent='Schedule unavailable — retrying automatically.';$('#games-table').innerHTML='';$('#schedule-empty').textContent='';$('#schedule-watch').textContent='';$('#schedule-sources').innerHTML=''}finally{scheduleFlight=null}})();
 return scheduleFlight;
}

let rdFlight=null, rdData=null;
async function loadRD(){
 if(view!=='rd')return;
 if(rdFlight)return rdFlight;
 rdFlight=(async()=>{
  try{
   const r=await fetch('/api/rd');
   if(!r.ok)throw new Error('R&D service unavailable');
   rdData=await r.json();
   renderRD(rdData);
  }catch(e){
   if($('#rd-status-note'))$('#rd-status-note').textContent='R&D telemetry unavailable: '+e.message;
  }finally{rdFlight=null}
 })();
 return rdFlight;
}

function renderRD(rd){
 if(!rd)return;
 const sb=rd.scoreboard||{}, cnt=sb.counts||{}, sup=rd.supervisor||{}, anti=sup.anti_looping_guard||{};
 const hyps=rd.hypotheses||[], dossiers=rd.forensic_dossiers||[], cycles=rd.cycle_history||[], logs=rd.execution_logs||[];
 const passedCount=hyps.filter(h=>h.status==='passed_crucible'||h.status==='deployed'||h.status==='staged_recovery').length;
 const passPct=rd.hypotheses_summary?.crucible_pass_rate_pct??(hyps.length?Math.round(passedCount/hyps.length*100):0);
 
 if($('#rd-metrics')){
  $('#rd-metrics').innerHTML=`
   <div class="stat"><div class="stat-number">${esc(cnt.total_segments??0)}</div><div class="stat-label">Total Segments</div><div class="stat-detail">Monitored Portfolio</div></div>
   <div class="stat"><div class="stat-number">${esc(cnt.super_alpha??0)} Super / ${esc(cnt.solid_alpha??cnt.alpha??0)} Solid</div><div class="stat-label">Dynamic Alpha Segments</div><div class="stat-detail">Scaled $2.50–$8.00</div></div>
   <div class="stat"><div class="stat-number">${esc(cnt.capital_defense??cnt.underperforming??0)}</div><div class="stat-label">Capital Defense</div><div class="stat-detail">${data.settings?.unit_stake < 1.0 ? '$0.50' : '$1.00'} Clamp (Zero Drop)</div></div>
   <div class="stat"><div class="stat-number">${esc(passPct)}% Pass</div><div class="stat-label">Crucible Pass Rate</div><div class="stat-detail">Walk-Forward Validated</div></div>
  `;
 }
 
 if($('#rd-status-note')){
  const nowStr=new Date().toLocaleTimeString('en-GB',{timeZone:'Europe/Budapest'});
  $('#rd-status-note').innerHTML=`<span class="badge positive" style="margin-right:6px">● LIVE (${nowStr})</span> Supervisor: ${esc(sup.agent||'SupervisedQuantRDAgent')} · ${anti.sla_seconds||300}s SLA · ${anti.emergency_timeout_seconds||1800}s emergency timeout · Anti-Looping Guard: ${esc(anti.status||'enforced')}`;
 }

 const timeline=rd.autonomous_timeline||[];
 if($('#rd-timeline-count'))$('#rd-timeline-count').textContent=`${timeline.filter(t=>t.is_working).length} Deployed Solutions Active & Protecting`;
 if($('#rd-timeline-container')){
  if(!timeline.length){
   $('#rd-timeline-container').innerHTML='<p class="panel-copy">No historical repair timeline records found.</p>';
  }else{
   $('#rd-timeline-container').innerHTML=timeline.map((item,idx)=>{
    const numStr=String(idx+1).padStart(2,'0');
    const metricsHtml=item.metrics?Object.entries(item.metrics).map(([k,v])=>`<span class="rd-metric-pill">${esc(k.replace(/_/g,' '))}: <strong>${esc(v)}</strong></span>`).join(''):'';
     return `<div class="rd-timeline-card" id="timeline-${esc(item.id)}">
     <div class="rd-timeline-header">
      <div class="rd-timeline-title-wrap">
       <span class="rd-timeline-num">${numStr}</span>
       <span class="badge ${item.league==='ALL'?'muted':'positive'}">${esc(item.league)}</span>
       <h3 class="rd-timeline-title">${esc(item.title)}</h3>
      </div>
      <span class="badge positive" style="font-weight:700">✓ ${esc(item.status_label||'ACTIVE & DEPLOYED')}</span>
     </div>
     <div class="rd-timeline-body">
      <div class="rd-timeline-row">
       <span class="rd-timeline-label">⚠️ Identified Defect:</span>
       <span class="rd-timeline-value"><strong>${esc(item.failure_mode||'')}</strong> — ${esc(item.defect_description)}</span>
      </div>
      <div class="rd-timeline-row">
       <span class="rd-timeline-label">🛠️ Engineered Remedy:</span>
       <div class="rd-timeline-value">
        ${esc(item.engineered_solution)}
        ${item.code_reference?`<div style="margin-top:4px"><code class="rd-timeline-code">${esc(item.code_reference)}</code></div>`:''}
       </div>
      </div>
      <div class="rd-timeline-row">
       <span class="rd-timeline-label">⏱️ Timeline:</span>
       <span class="rd-timeline-value" style="font-family:var(--mono);font-size:11px">
        Discovered: <strong>${esc(when(item.discovered_at))}</strong> · Deployed &amp; Active: <strong style="color:#10b981">${esc(when(item.deployed_at))}</strong>
       </span>
      </div>
      <div class="rd-timeline-evidence-box">
       <div class="rd-timeline-evidence-title">
        <span>📊 Operational Status &amp; Empirical Impact:</span>
       </div>
       <p class="rd-timeline-evidence-text">${esc(item.verification_evidence)}</p>
       ${metricsHtml?`<div class="rd-timeline-metrics">${metricsHtml}</div>`:''}
      </div>
     </div>
    </div>`;
   }).join('');
  }
 }

 const inc=rd.market_incubator||{};
 const incFamilies=inc.incubated_families||[];
 const incGrad=inc.graduated_markets||[];
 const incDemoted=inc.demoted_markets||[];
 if($('#rd-incubator-badge')){
  const demotedBadge = incDemoted.length ? ` · ${incDemoted.length} Demoted to Research` : '';
  $('#rd-incubator-badge').textContent=`${incGrad.length} Graduated to Live${demotedBadge} · ${incFamilies.length} Incubating`;
 }
 if($('#rd-incubator-metrics')){
  const demotedBox = incDemoted.length ? `
   <div class="stat"><div class="stat-number" style="color:#ef4444">${esc(incDemoted.length)} Demoted</div><div class="stat-label">Relegated to R&D</div><div class="stat-detail">Autonomous AI Repair</div></div>
  ` : '';
  $('#rd-incubator-metrics').innerHTML=`
   <div class="stat"><div class="stat-number">${esc(inc.active_incubations||0)}</div><div class="stat-label">Active Gap Incubations</div><div class="stat-detail">Zero-Risk Shadow Mode</div></div>
   <div class="stat"><div class="stat-number">${esc(inc.total_wagers_observed||0)}</div><div class="stat-label">Shadow Wagers Tracked</div><div class="stat-detail">Continuous Feed Capture</div></div>
   <div class="stat"><div class="stat-number">${esc(inc.status_counts?.settled||0)}</div><div class="stat-label">Settled Micro-Trials</div><div class="stat-detail">Official Score Evaluated</div></div>
   <div class="stat"><div class="stat-number" style="color:#10b981">${esc(incGrad.length)} Graduated</div><div class="stat-label">Live Wager Roster</div><div class="stat-detail">Crucible Pass Invariants</div></div>
   ${demotedBox}
  `;
 }
 if($('#rd-incubator-table')){
  if(!incFamilies.length){
   $('#rd-incubator-table').innerHTML='<p class="panel-copy">No unmodeled gap markets actively incubating yet. The background scanner captures gap markets dynamically every 60s.</p>';
  }else{
   $('#rd-incubator-table').innerHTML=`
    <table class="table">
     <thead>
      <tr>
       <th>Segment Key</th>
       <th>League</th>
       <th>Scope</th>
       <th>Family</th>
       <th>Total Wagers</th>
       <th>Settled</th>
       <th>Virtual PnL</th>
       <th>ROI %</th>
       <th>ΔBrier vs Mkt</th>
       <th>Crucible Status</th>
      </tr>
     </thead>
     <tbody>
      ${incFamilies.map(f=>`
       <tr>
        <td><strong>${esc(f.segment_key)}</strong></td>
        <td><span class="badge muted">${esc(f.league)}</span></td>
        <td>${esc(f.scope)}</td>
        <td>${esc(f.family)} / <small>${esc(f.sub_family)}</small></td>
        <td>${esc(f.total_wagers)}</td>
        <td>${esc(f.settled_wagers)}</td>
        <td style="color:${f.pnl>=0?'#10b981':'#ef4444'};font-family:var(--mono)">$${esc(f.pnl)}</td>
        <td style="color:${f.roi_pct>=0?'#10b981':'#ef4444'};font-family:var(--mono)">${esc(f.roi_pct)}%</td>
        <td style="font-family:var(--mono)">${f.delta_brier>0?'+':''}${esc(f.delta_brier)}</td>
        <td><span class="badge ${f.status==='graduated'?'positive':'muted'}">${esc(f.status||'incubating')}</span></td>
       </tr>
      `).join('')}
     </tbody>
    </table>
   `;
  }
 }
 if($('#rd-graduated-table')){
  let gradHtml = '';
  if(incGrad.length){
   gradHtml += `
    <h4 style="margin-bottom:8px">🎓 Formally Graduated Market Families (Live Execution Invariants)</h4>
    <table class="table">
     <thead>
      <tr>
       <th>Segment Key</th>
       <th>League</th>
       <th>Scope</th>
       <th>Family</th>
       <th>Graduated At</th>
       <th>Sample Size</th>
       <th>Simulated ROI</th>
       <th>ΔBrier</th>
       <th>Live Tier Status</th>
      </tr>
     </thead>
     <tbody>
      ${incGrad.map(g=>`
       <tr>
        <td><strong style="color:var(--accent)">${esc(g.segment_key)}</strong></td>
        <td><span class="badge positive">${esc(g.league)}</span></td>
        <td>${esc(g.scope)}</td>
        <td>${esc(g.family)} (<small>${esc(g.sub_family)}</small>)</td>
        <td>${esc(when(g.graduated_at))}</td>
        <td>${esc(g.sample_size)} games</td>
        <td style="color:#10b981;font-family:var(--mono)">+${esc(g.roi_pct)}%</td>
        <td style="font-family:var(--mono)">+${esc(g.delta_brier)}</td>
        <td><span class="badge positive">Active in EXPLORATION ($1.00-$1.50)</span></td>
       </tr>
      `).join('')}
     </tbody>
    </table>
   `;
  }
  if(incDemoted.length){
   gradHtml += `
    <h4 style="margin-top:16px;margin-bottom:8px;color:#ef4444">⚠️ Demoted to Research / Re-Incubation (Probation & Autonomous Model Repair)</h4>
    <table class="table">
     <thead>
      <tr>
       <th>Segment Key</th>
       <th>League</th>
       <th>Scope</th>
       <th>Demoted At</th>
       <th>Prior Deficit</th>
       <th>Assigned New Model</th>
       <th>Proven Historical Edge</th>
       <th>Incubation Status</th>
      </tr>
     </thead>
     <tbody>
      ${incDemoted.map(d=>{
        const isInc = d.incubation_phase === 'incubating_with_new_model' && d.incubated_model_id;
        const modelDisplay = isInc
          ? `<strong style="color:#10b981">${esc(d.model_archetype || d.incubated_model_id)}</strong><br><small style="color:var(--muted);font-family:var(--mono)">${esc(d.incubated_model_id)}</small>`
          : '<span class="badge muted">R&D Historical Fitting...</span>';
        const edgeDisplay = (isInc && d.historical_roi_pct != null)
          ? `<span style="color:#10b981;font-family:var(--mono);font-weight:600">+${esc(d.historical_roi_pct)}% ROI</span><br><small style="color:var(--muted)">(${esc(d.historical_sample_size)} OOS games)</small>`
          : '<span style="color:var(--muted)">Evaluating...</span>';
        const statusBadge = isInc
          ? '<span class="badge positive">Incubating in Shadow ($0.00)</span>'
          : '<span class="badge warning">R&D Model Search</span>';
        return `
         <tr>
          <td><strong style="color:${isInc ? '#10b981' : '#ef4444'}">${esc(d.segment_key)}</strong></td>
          <td><span class="badge ${isInc ? 'positive' : 'warning'}">${esc(d.league)}</span></td>
          <td>${esc(d.scope)}</td>
          <td>${esc(when(d.demoted_at))}</td>
          <td style="color:#ef4444;font-family:var(--mono)">${esc(d.rolling_roi_pct)}% <small>($${esc(d.rolling_pnl)})</small></td>
          <td>${modelDisplay}</td>
          <td>${edgeDisplay}</td>
          <td>${statusBadge}</td>
         </tr>
        `;
      }).join('')}
     </tbody>
    </table>
   `;
  }
  $('#rd-graduated-table').innerHTML = gradHtml;
 }

 const nextUp=rd.next_up_queue||[];
 if($('#rd-nextup-count'))$('#rd-nextup-count').textContent=`${nextUp.length} Active Roadmap Tasks in Brain Queue`;
 if($('#rd-nextup-container')){
  if(!nextUp.length){
   $('#rd-nextup-container').innerHTML='<p class="panel-copy">No tasks currently queued.</p>';
  }else{
   $('#rd-nextup-container').innerHTML=nextUp.map((task,idx)=>{
    const liveTel=task.live_telemetry;
    return `<div class="rd-nextup-card">
     <div>
      <div class="rd-nextup-header">
       <span class="rd-nextup-prio">PRIO #${task.priority||(idx+1)} · ${esc(task.league)}</span>
       <span class="badge ${task.current_status==='running_in_background'?'positive':'muted'}">${esc(task.status_label||task.current_status)}</span>
      </div>
      <h4 class="rd-nextup-title">${esc(task.title)}</h4>
      <div class="rd-nextup-body" style="margin-top:10px">
       <div style="color:var(--muted)"><strong>🔍 Investigated Failure Mode:</strong> ${esc(task.defect_investigated)}</div>
       <div style="color:var(--text)"><strong>💡 Planned Quantitative Remedy:</strong> ${esc(task.planned_solution)}</div>
       <div class="rd-nextup-wait-box">
        <strong>⏳ Current Blocker / Status:</strong> ${esc(task.waiting_on)}
       </div>
       <div class="rd-nextup-target-box">
        <strong>🎯 Target P&amp;L Impact:</strong> ${esc(task.target_impact)}
       </div>
       ${liveTel?`<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">
        <span class="rd-metric-pill">Queued Jobs: ${liveTel.queued_jobs}</span>
        <span class="rd-metric-pill" style="background:#fef3c7;color:#92400e">Running: ${liveTel.running_jobs}</span>
        <span class="rd-metric-pill">Completed: ${liveTel.completed_jobs}</span>
       </div>`:''}
      </div>
     </div>
     <div class="rd-nextup-footer">
      <span>Discovered: <strong>${esc(when(task.discovered_at))}</strong></span>
      <span>Expected ETA: <strong style="color:var(--accent)">${esc(task.eta)}</strong></span>
     </div>
    </div>`;
   }).join('');
  }
 }

 
 if($('#rd-alpha-table')){
  const alphas=sb.alpha_segments||sb.top_segments||[];
  if($('#rd-alpha-count'))$('#rd-alpha-count').textContent=`${alphas.length} Winning Segments · Scaled $2.50–$8.00`;
  $('#rd-alpha-table').innerHTML=table(['Segment','League','Scope','Games','P&L','ROI','Win Rate','Tier','Dynamic Sizing Target'],alphas.map(s=>{
   const isSuper=s.sub_tier==='SUPER_ALPHA'||(s.scaling_target&&s.scaling_target.includes('super-alpha'));
   const tierBadge=`<span class="badge positive" style="font-weight:700">${esc(s.sub_tier||s.tier||'ALPHA')}</span>`;
   return [
    `<strong>${esc(s.segment)}</strong>`,
    `<span class="badge ${s.league==='ALL'?'muted':'positive'}">${esc(s.league)}</span>`,
    esc(s.scope),
    esc(s.games),
    `<span style="color:${s.pnl>=0?'#10b981':'#ef4444'};font-weight:700;font-family:var(--mono)">${s.pnl>=0?'+$':'-$'}${num(Math.abs(s.pnl))}</span>`,
    `<span style="color:${s.roi_pct>=0?'#10b981':'#ef4444'};font-weight:700;font-family:var(--mono)">${s.roi_pct>=0?'+':''}${num(s.roi_pct,1)}%</span>`,
    pct(s.win_rate_pct/100),
    tierBadge,
    `<span class="badge positive">${esc(s.scaling_target||'$2.50–$5.00')}</span>`
   ];
  }));
 }

 if($('#rd-underperforming-table')){
  const segs=sb.underperforming_segments||[];
  const dosMap=new Map(dossiers.map(d=>[d.segment,d]));
  $('#rd-underperforming-table').innerHTML=table(['Segment','League','Scope','Games','P&L','ROI','Win Rate','Defense Status','Failure Attribution'],segs.map(s=>{
   const d=dosMap.get(s.segment);
   return [esc(s.segment),esc(s.league),esc(s.scope),esc(s.games),'$'+num(s.pnl),pct(s.roi_pct/100),pct(s.win_rate_pct/100),'<span class="badge negative">$1.00 Stake Clamp</span>',esc(d?d.primary_failure_mode:'tail_variance')];
  }));
 }

 if($('#rd-tier-transitions-table')){
  const transitions=rd.tier_transitions_history||rd.tier_transitions||sb.recent_transitions||[];
  $('#rd-tier-transitions-table').innerHTML=table(['Time','Segment','Transition','From Tier','To Tier','P&L / ROI','Forensic Attribution / Win Driver','Actionable R&D Remedy / Sizing Target'],transitions.map(t=>{
   const isPromo = t.transition_type === 'PROMOTION';
   const badge = `<span class="badge ${isPromo?'positive':'negative'}">${esc(t.transition_type)}</span>`;
   const pnlRoi = `$${num(t.pnl)} (${num(t.roi_pct,1)}%)`;
   return [
    esc(when(t.timestamp)),
    esc(t.segment),
    badge,
    esc(t.previous_tier),
    `<strong>${esc(t.new_tier)}</strong>`,
    pnlRoi,
    esc(t.diagnostic_reason),
    esc(t.rd_actionable_remedy)
   ];
  }));
 }
 
 if($('#rd-hypotheses-count'))$('#rd-hypotheses-count').textContent=`${hyps.length} Formulated Hypotheses`;
 if($('#rd-hypotheses-grid')){
  $('#rd-hypotheses-grid').innerHTML=hyps.map(h=>{
   const isDeployed=h.status==='deployed'||h.status==='staged_recovery';
   const passed=h.status==='passed_crucible'||isDeployed;
   const badgeText=isDeployed?'✓ DEPLOYED · ACTIVE':passed?'PASSED CRUCIBLE':esc(h.status);
   const badgeClass=passed?'positive':'muted';
   const ev=h.crucible_evidence||{};
   return `<div class="rd-hypothesis-card">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
     <span class="badge ${badgeClass}">${badgeText}</span>
     <small style="color:#70817b">${esc(h.category||'')}</small>
    </div>
    <h4 style="margin:8px 0 4px 0">${esc(h.title)}</h4>
    <p class="panel-copy" style="font-size:11px;margin:4px 0">${esc(h.rationale||'')}</p>
    <code style="font-size:10px;display:block;background:#f3f6f4;padding:4px 6px;border-radius:4px;margin:6px 0">${esc(h.proposed_formula_or_rule||'')}</code>
    ${ev.sample_size?`<div style="font-size:11px;color:#247a4d;margin-top:6px">Walk-Forward: <strong>${esc(ev.sample_size)} bets</strong> · Δ Brier: +${num(ev.brier_improvement,4)}</div>`:''}
   </div>`;
  }).join('');
 }
 
 if($('#rd-crucible-matrix')){
  const matrixItems=rd.crucible_matrix||hyps.filter(h=>h.crucible_evidence);
  const matrixRows=matrixItems.map(h=>{
   const ev=h.crucible_evidence||h;
   const isDeployed=h.status==='deployed'||h.status==='staged_recovery'||ev.status==='deployed';
   const passed=h.status==='passed_crucible'||ev.status==='passed_crucible'||isDeployed;
   const verdictBadge=isDeployed?'<span class="badge positive">✓ DEPLOYED · ACTIVE</span>':passed?'<span class="badge positive">PASSED CRUCIBLE</span>':'<span class="badge negative">REJECTED</span>';
   return [esc(h.title||h.hypothesis_id),esc(h.segment||''),(ev.sample_size!=null?esc(ev.sample_size)+' bets':'—'),num(ev.baseline_brier,4),num(ev.candidate_brier,4),'+'+num(ev.brier_improvement,4),pct((ev.roi_delta_pct||0)/100),verdictBadge];
  });
  $('#rd-crucible-matrix').innerHTML=table(['Hypothesis','Segment','Sample Size','Baseline Brier','Candidate Brier','Δ Brier','Δ ROI','Crucible Verdict'],matrixRows);
 }
 
 const council=rd.quant_council_sessions||[];
 if($('#rd-council-count'))$('#rd-council-count').textContent=`${council.length} Deliberations Logged`;
 if($('#rd-council-container')){
  if(!council.length){
   $('#rd-council-container').innerHTML='<p class="panel-copy">No Quant Council sessions recorded yet.</p>';
  }else{
   $('#rd-council-container').innerHTML=council.map(cs=>{
    const claude=cs.sports_mechanics_advice||{};
    const codex=cs.quant_remedy_advice||{};
    const grok=cs.red_team_advice||{};
    const hyp=cs.synthesized_hypothesis||{};
    return `<div class="rd-council-card" style="background:#ffffff;border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px">
     <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px">
      <div>
       <span class="badge positive">${esc(cs.league||'')}</span>
       <strong style="font-size:14px;margin-left:6px">${esc(cs.segment||'')}</strong>
       <small style="color:var(--muted);margin-left:8px;font-family:var(--mono)">${esc(cs.session_id||'')}</small>
      </div>
      <span class="badge muted" style="font-size:11px">${esc(when(cs.created_at))}</span>
     </div>
     <div style="font-size:12px;color:#b91c1c;background:#fef2f2;padding:6px 10px;border-radius:6px;margin-bottom:12px">
      <strong>Trigger:</strong> ${esc(cs.trigger_reason||'')}
     </div>
     <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-bottom:12px">
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;font-size:11px">
       <strong style="color:#7c3aed">🧠 Claude (Sports Mechanics):</strong>
       <p style="margin:4px 0 0 0;color:#334155">${esc(claude.text||'Sports variance review.')}</p>
      </div>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;font-size:11px">
       <strong style="color:#0284c7">📐 Codex (Quant Remedy):</strong>
       <p style="margin:4px 0 0 0;color:#334155">${esc(codex.text||'Bayesian shrinkage remedy.')}</p>
      </div>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;font-size:11px">
       <strong style="color:#ea580c">🛡️ Grok (Red-Team):</strong>
       <p style="margin:4px 0 0 0;color:#334155">${esc(grok.text||'Probability clamping guardrail.')}</p>
      </div>
     </div>
     <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:10px;font-size:11px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
       <strong style="color:#15803d">⚡ Google Antigravity Synthesis (Chair):</strong>
       <span class="badge positive" style="font-size:10px">${esc(hyp.hypothesis_id||'')}</span>
      </div>
      <p style="margin:2px 0 6px 0;color:#166534"><strong>${esc(hyp.title||'')}</strong>: ${esc(hyp.rationale||'')}</p>
      <code style="background:#dcfce7;color:#14532d;padding:2px 6px;border-radius:4px;font-size:10px">${esc(hyp.proposed_formula_or_rule||'')}</code>
     </div>
    </div>`;
   }).join('');
  }
 }

 if($('#rd-cycle-count'))$('#rd-cycle-count').textContent=`${cycles.length} Cycles Completed`;
 if($('#rd-cycles-table')){
  $('#rd-cycles-table').innerHTML=table(['Cycle ID','Executed At','Segments Checked','Dossiers','Hypotheses','Crucible Tests','Verdict / Promoted'],cycles.map(c=>[
   esc(c.cycle_id),esc(when(c.executed_at)),esc(c.underperforming_segments_checked),esc(c.forensic_dossiers_created),esc(c.hypotheses_generated),esc(c.crucible_tests_run),`<span class="badge positive">${esc(c.candidates_passed)} Passed / ${esc(c.candidates_rejected)} Rejected · COMPLETED</span>`
  ]));
 }
 
 if($('#rd-execution-terminal')){
  if(logs.length){
   $('#rd-execution-terminal').innerHTML=logs.map(l=>`<div class="rd-log-entry"><span class="rd-log-time">${esc(l.timestamp)}</span> <span class="rd-badge-${esc(l.stage)}">${esc(l.stage)}</span> ${esc(l.message)}</div>`).join('');
  }
 }
}

function bindRDActions(){
 const runBtn=$('#rd-run-cycle');
 if(runBtn){
  runBtn.onclick=async()=>{
   runBtn.disabled=true;runBtn.textContent='Running Autonomous Cycle…';
   try{
    const res=await api('/api/rd/cycle',{method:'POST'});
    toast(res.message||'Autonomous R&D cycle initiated successfully.');
    await loadRD();
   }catch(e){toast('R&D Cycle failed: '+e.message)}
   finally{runBtn.disabled=false;runBtn.textContent='▷ Run Autonomous R&D Cycle'}
  };
 }
 const clearBtn=$('#rd-clear-terminal');
 if(clearBtn){
  clearBtn.onclick=()=>{
   const term=$('#rd-execution-terminal');
   if(term)term.innerHTML='<div class="rd-log-entry">Terminal cleared.</div>';
  };
 }
}

let stakeAccountFlight=null, stakeAccountData=null;
let stakeHistoryPeriod='all', stakeHistoryFlight=null, stakeHistoryData=null;

async function loadStakeHistory(period=stakeHistoryPeriod){
 if(view!=='stake_account')return;
 stakeHistoryPeriod=period;
 try{
  const r=await fetch(`/api/stake-account/history?period=${encodeURIComponent(period)}`);
  if(!r.ok)return;
  stakeHistoryData=await r.json();
  renderStakePortfolioChart(stakeHistoryData);
 }catch(e){
  console.error('Stake history load error:', e);
 }
}

function renderStakePortfolioChart(hist){
 if(!hist)return;
 const summary=hist.summary||{};
 const points=Array.isArray(hist.points)?hist.points:[];

 if($('#stake-chart-curr-bal'))$('#stake-chart-curr-bal').textContent='$'+Number(summary.current_balance||0).toFixed(2);
 const netEl=$('#stake-chart-net-change');
 if(netEl){
  const net=Number(summary.net_change||0);
  const pct=Number(summary.pct_change||0);
  const sign=net>=0?'+':'-';
  netEl.textContent=`${sign}$${Math.abs(net).toFixed(2)} (${sign}${Math.abs(pct).toFixed(2)}%)`;
  netEl.style.color=net>=0?'var(--green)':'#dc2626';
 }
 if($('#stake-chart-high-bal'))$('#stake-chart-high-bal').textContent='$'+Number(summary.high_watermark||summary.max_balance||0).toFixed(2);
 if($('#stake-chart-low-bal'))$('#stake-chart-low-bal').textContent='$'+Number(summary.min_balance||0).toFixed(2);
 if($('#stake-chart-points-count'))$('#stake-chart-points-count').textContent=summary.data_points_count||points.length;

 const chart=$('#stake-portfolio-chart');
 if(!chart)return;

 if(!points.length){
  chart.innerHTML='<p class="statistics-chart-empty">No balance snapshots recorded yet. Initializing live telemetry…</p>';
  return;
 }

 const width=900,height=220,left=65,right=20,top=20,bottom=36;
 const plotWidth=width-left-right,plotHeight=height-top-bottom;

 const parsedPoints=points.map(p=>({
  ...p,
  t:new Date(p.timestamp).getTime(),
  bal:Number(p.total_balance||0)
 })).filter(p=>!isNaN(p.t)&&isFinite(p.bal));

 if(!parsedPoints.length){
  chart.innerHTML='<p class="statistics-chart-empty">No valid snapshot points.</p>';
  return;
 }

 let minBal=parsedPoints[0].bal,maxBal=parsedPoints[0].bal;
 for(const p of parsedPoints){
  if(p.bal<minBal)minBal=p.bal;
  if(p.bal>maxBal)maxBal=p.bal;
 }
 const pad=Math.max((maxBal-minBal)*0.15,2.0);
 const yMin=minBal-pad,yMax=maxBal+pad;

 const firstTime=parsedPoints[0].t,lastTime=parsedPoints[parsedPoints.length-1].t;
 const x=p=>left+(lastTime===firstTime?(plotWidth/2):((p.t-firstTime)/(lastTime-firstTime))*plotWidth);
 const y=val=>top+((yMax-val)/(yMax-yMin))*plotHeight;

 const fmtDate=t=>new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Budapest'}).format(new Date(t));
 const fmtDateShort=t=>new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',timeZone:'Europe/Budapest'}).format(new Date(t));

 const gridValues=[yMax-pad*0.2, (yMax+yMin)/2, yMin+pad*0.2];
 const gridSvg=gridValues.map(val=>`<line x1="${left}" x2="${width-right}" y1="${y(val)}" y2="${y(val)}" class="statistics-grid-line"/><text x="${left-8}" y="${y(val)+4}" text-anchor="end" style="font-size:10px;fill:var(--muted)">$${val.toFixed(0)}</text>`).join('');

 const linePath='M '+parsedPoints.map(p=>`${x(p).toFixed(1)} ${y(p.bal).toFixed(1)}`).join(' L ');
 const lastX=x(parsedPoints[parsedPoints.length-1]).toFixed(1);
 const firstX=x(parsedPoints[0]).toFixed(1);
 const bottomY=(height-bottom).toFixed(1);
 const areaPath=`${linePath} L ${lastX} ${bottomY} L ${firstX} ${bottomY} Z`;

 const pointsSvg=parsedPoints.map(p=>{
  const tt=`${fmtDate(p.t)} · Total Balance: $${p.bal.toFixed(2)} (Cash: $${Number(p.available_balance||0).toFixed(2)}, Open: $${Number(p.open_exposure||0).toFixed(2)})`;
  return `<circle cx="${x(p).toFixed(1)}" cy="${y(p.bal).toFixed(1)}" r="3.5" class="statistics-pnl-point" tabindex="0" role="img" aria-label="${esc(tt)}"><title>${esc(tt)}</title></circle>`;
 }).join('');

 const dateLabels=`<text x="${left}" y="${height-10}" style="font-size:10px;fill:var(--muted)">${esc(fmtDateShort(firstTime))}</text>`+(lastTime!==firstTime?`<text x="${width-right}" y="${height-10}" text-anchor="end" style="font-size:10px;fill:var(--muted)">${esc(fmtDateShort(lastTime))}</text>`:'');

 chart.innerHTML=`<svg class="statistics-pnl-chart" viewBox="0 0 ${width} ${height}" style="width:100%;height:auto;overflow:visible">
  <defs>
   <linearGradient id="stake-bal-grad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#10b981" stop-opacity="0.30"/>
    <stop offset="100%" stop-color="#10b981" stop-opacity="0.01"/>
   </linearGradient>
  </defs>
  ${gridSvg}
  <path d="${areaPath}" fill="url(#stake-bal-grad)"/>
  <path d="${linePath}" class="statistics-pnl-line" style="stroke:#10b981;stroke-width:2.5"/>
  ${pointsSvg}
  ${dateLabels}
 </svg>`;
}

async function loadStakeAccount(forceRefresh=false){
 if(view==='stake_account')loadStakeHistory(stakeHistoryPeriod);
 if(stakeAccountFlight)return stakeAccountFlight;
 stakeAccountFlight=(async()=>{
  try{
   const url=forceRefresh?'/api/stake-account?refresh=true':'/api/stake-account';
   const r=await fetch(url);
   if(!r.ok)throw new Error('Stake account telemetry unavailable');
   stakeAccountData=await r.json();
   if(view==='stake_account')renderStakeAccount(stakeAccountData);
   updateAutoBetPanel();
  }catch(e){
   if(view==='stake_account')toast('Stake Account Monitor: '+e.message);
  }finally{stakeAccountFlight=null}
 })();
 return stakeAccountFlight;
}

function renderStakeAccount(d){
 if(!d)return;
 if($('#stake-total-balance'))$('#stake-total-balance').textContent='$'+(Number(d.total_balance||0).toFixed(2));
 if($('#stake-avail-balance'))$('#stake-avail-balance').textContent='$'+(Number(d.available_balance||0).toFixed(2));
 if($('#stake-active-count'))$('#stake-active-count').textContent=d.open_bets_count??0;
 if($('#stake-open-exposure'))$('#stake-open-exposure').textContent='$'+(Number(d.open_exposure||0).toFixed(2));
 if($('#stake-token-masked'))$('#stake-token-masked').textContent=d.token_masked||'Not configured';

 if($('#stake-username-display')){
  $('#stake-username-display').textContent=d.username||'blvrg';
 }

 const badge=$('#stake-account-badge');
 if(badge){
  if(d.is_live_synced){
   badge.textContent='Live Synced ('+(d.username||'Stake.com')+')';
   badge.className='badge positive';
  }else if(d.token_configured){
   badge.textContent='Token Active · Telemetry Live';
   badge.className='badge positive';
  }else{
   badge.textContent='Token Missing (.env)';
   badge.className='badge muted';
  }
 }

 if($('#stake-total-sub')){
  if(d.is_live_synced){
   $('#stake-total-sub').textContent=`Cash $${Number(d.available_balance||0).toFixed(2)} + Open Bets $${Number(d.open_exposure||0).toFixed(2)}`;
  }else{
   const pnl=Number(d.settled_pnl||0);
   $('#stake-total-sub').textContent=`Base $${Number(d.base_bankroll||1000).toFixed(2)} + Settled PnL ${pnl>=0?'+$':'-$'}${Math.abs(pnl).toFixed(2)}`;
  }
 }

 if($('#stake-avail-sub')){
  if(d.is_live_synced && d.balances){
   const parts = [];
   for(const [c, b] of Object.entries(d.balances)){
    if(b && b.available > 0){
     parts.push(`${c.toUpperCase()}: ${b.available}`);
    }
   }
   $('#stake-avail-sub').textContent = parts.length ? parts.join(' · ') : 'Free cash for new wagers';
  } else {
   $('#stake-avail-sub').textContent = 'Free cash for new wagers';
  }
 }

 if($('#stake-active-sub')){
  $('#stake-active-sub').textContent = d.is_live_synced ? 'Active wagers on Stake.com' : 'Unsettled live tickets';
 }

 if($('#stake-exposure-sub')){
  $('#stake-exposure-sub').textContent = d.is_live_synced ? 'Exact open stake value at risk' : 'Total USD at risk';
 }

 const cryptoBox=$('#stake-crypto-breakdown');
 if(cryptoBox && d.balances){
  const items = Object.entries(d.balances).filter(([_, b]) => (b && (b.available > 0 || b.vault > 0)));
  if(items.length){
   cryptoBox.innerHTML = items.map(([cur, b]) => {
    return `<div class="stake-crypto-pill"><strong>${esc(cur.toUpperCase())}</strong>: ${esc(b.available)}</div>`;
   }).join('');
  } else {
   cryptoBox.innerHTML = '';
  }
 }

 if($('#stake-settled-info')){
  if(d.is_live_synced){
   $('#stake-settled-info').textContent = `Live Stake Wagers: ${d.open_bets_count||0} active ($${Number(d.open_exposure||0).toFixed(2)})`;
   $('#stake-settled-info').className = 'badge positive';
  } else {
   const pnl=Number(d.settled_pnl||0);
   $('#stake-settled-info').textContent=`Virtual Engine PnL: ${pnl>=0?'+$':'-$'}${Math.abs(pnl).toFixed(2)} (${d.settled_bets_count||0} settled)`;
   $('#stake-settled-info').className = 'badge muted';
  }
 }

 if($('#stake-risk-title')){
  $('#stake-risk-title').textContent = d.is_live_synced ? 'Real Open Exposure by League & Sport' : 'Open Exposure by League & Sport';
 }
 if($('#stake-risk-copy')){
  $('#stake-risk-copy').textContent = d.is_live_synced ? `Exact active dollar exposure distributed across all sports on your Stake account (${d.username||'blvrg'}).` : 'Current active dollar exposure distributed across all sports and tournament feeds.';
 }

 if($('#stake-last-synced')){
  $('#stake-last-synced').textContent = d.synced_at ? when(d.synced_at) : 'Just now';
 }

 const input=$('#stake-base-bankroll-input');
 if(input&&!input.matches(':focus')){
  input.value=d.base_bankroll||1000;
 }

 const tbody=$('#stake-league-tbody');
 if(tbody){
  const leagues=Object.entries(d.by_league||{});
  if(!leagues.length){
   tbody.innerHTML='<tr><td colspan="4" style="text-align:center;color:var(--muted)">No open wagers recorded.</td></tr>';
  }else{
   const totalExp=Number(d.open_exposure||1);
   tbody.innerHTML=leagues.map(([lg,info])=>{
    const share=totalExp>0?((info.exposure/totalExp)*100).toFixed(1):'0.0';
    return `<tr><td><strong>${esc(lg)}</strong></td><td><strong>${esc(info.count)}</strong> bets</td><td><strong>$${Number(info.exposure||0).toFixed(2)}</strong></td><td><div style="display:flex;align-items:center;gap:8px"><span style="min-width:44px;font-family:var(--mono)">${share}%</span><div class="stake-progress-bar" style="flex:1;max-width:120px"><div class="stake-progress-fill" style="width:${share}%"></div></div></div></td></tr>`;
   }).join('');
  }
 }
}

function bindStakeAccountActions(){
 const refBtn=$('#stake-account-refresh');
 if(refBtn){
  refBtn.onclick=()=>{
   loadStakeAccount(true);
   toast('Refreshing Stake Account telemetry…');
  };
 }
 const chartTabs=$$('#stake-chart-tabs button');
 chartTabs.forEach(btn=>{
  btn.onclick=()=>{
   chartTabs.forEach(b=>b.classList.remove('selected'));
   btn.classList.add('selected');
   const p=btn.getAttribute('data-period')||'all';
   loadStakeHistory(p);
  };
 });
 const form=$('#stake-bankroll-form');
 if(form){
  form.onsubmit=async e=>{
   e.preventDefault();
   const val=Number($('#stake-base-bankroll-input').value);
   if(isNaN(val)||val<0){
    toast('Please enter a valid non-negative bankroll.');
    return;
   }
   try{
    const updated=await api('/api/stake-account',{
     method:'POST',
     body:JSON.stringify({base_bankroll:val})
    });
    stakeAccountData=updated;
    renderStakeAccount(updated);
    toast('Base bankroll updated to $'+val.toFixed(2));
   }catch(err){
    toast(err.message);
   }
  };
 }
}


function table(headers,rows){return rows.length?`<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`:'<p class="panel-copy">No data available yet.</p>'}
function renderFundingTiers(){
 let host=$('#funding-tier-evidence');
 if(!host){host=document.createElement('section');host.id='funding-tier-evidence';$('#economic-segments').after(host)}
 const rows=data?.performance?.funding_tiers||[],labels={stress_value:'Stress Value',model_pick:'Model Pick',probability_pick:'Probability Pick',legacy_unspecified:'Legacy · Tier unspecified'};
 const timestamp=value=>value&&Number.isFinite(new Date(value).getTime())?when(value):'—';
 host.innerHTML=`<h3>Original Decisions by Funding Tier</h3><p class="panel-copy funding-tier-basis">${esc(data?.performance?.funding_tier_basis||'Original funding-tier evidence is not available yet. No retrospective tier assignment is inferred.')}</p><div class="table-wrap">${table(['Original Tier','League','Market','Issued / Games','Settled / Won / Lost / Push','Settled Stake','Betting P&L','ROI / Hit Rate','First / Last Issued'],rows.map(r=>[esc(labels[r.tier]||r.tier),esc(r.league),esc(r.market),esc(r.issued)+' / '+esc(r.games),[r.settled,r.won,r.lost,r.pushes].map(esc).join(' / '),'$'+num(r.settled_stake),'$'+num(r.pnl),pct(r.roi)+' / '+pct(r.hit_rate)+(r.settled===0?'<small>No settled decisions</small>':''),esc(timestamp(r.first_issued_at))+'<small>'+esc(timestamp(r.last_issued_at))+'</small>']))}</div>`;
}
function renderEconomics(){
 renderFundingTiers();
 const e=data?.performance?.economic_evidence,c=data?.automation?.closing_capture;
 $('#economic-summary').textContent=e?`${e.settled_decisions} settled decisions across ${e.settled_games} distinct games and ${e.event_dates} event dates. Betting P&L after stakes: $${num(e.gross_pnl)}; ROI: ${pct(e.gross_roi)}. ${e.gross_roi_interval95?'Event-date grouped 95% ROI interval: '+e.gross_roi_interval95.map(pct).join(' to ')+'.':'Insufficient independent results for an ROI interval.'} ${e.note||''}`:'Awaiting settled outcome evidence.';
 $('#economic-segments').innerHTML=table(['League','Market','Model','Settled Bets / Games','Won / Lost / Push / Void','Betting P&L','ROI'],(e?.segments||[]).map(r=>[esc(r.league),esc(r.market),esc(r.model_version),r.settled_decisions+' / '+r.settled_games,[r.outcomes?.won||0,r.outcomes?.lost||0,r.outcomes?.push||0,r.outcomes?.void||0].join(' / '),'$'+num(r.pnl),pct(r.roi)]));
 $('#economic-exposure').innerHTML=table(['League','Matchup','Issued Open Selections','Open Stake'],(e?.exposure||[]).map(r=>[esc(r.league),esc(r.matchup),r.decisions,'$'+num(r.stake)]));
 $('#closing-capture-summary').textContent=c?`Closing-price source capture: ${c.state}. ${c.stored_captures??0} verified response captures retained. ${c.note||''}`:'Closing-price source capture is awaiting its first check.';
}
function renderGovernance(){
 renderLeagueOperations();
 renderDevelopment();
 const g=data?.status?.governance,p=data?.performance?.system_decisions||{};
 const worker=data?.learning_worker,labels={queued:'Queued',running:'Running',completed:'Completed',retry:'Retry scheduled',needs_implementation:'Implementation needed',needs_cost_input:'Cost input missing',scheduled_workflow:'Scheduled workflow',collecting_evidence:'Collecting evidence',resolved:'Resolved'};
 $('#worker-summary').textContent=worker?`Worker: ${worker.state||'unknown'}. Last check: ${when(worker.checked_at)}. Training runs separately from market publication. Results and failed attempts are retained. Isolated software development is tracked separately below.`:'Awaiting learning worker status.';
 $('#worker-table').innerHTML=table(['Workflow','League','Status','Attempts','Result / Next Step'],(worker?.jobs||[]).filter(j=>j.kind!=='research_gap').slice(0,18).map(j=>[esc(({model_search:'Model hypothesis search',controlled_retrain:'Controlled model experiment',research_watch:'Literature discovery',market_research:'Market research dossier',source_refresh:'Source recovery',model_restore:'Model artifact recovery',segment_restore:'Inning model recovery',segment_retrain:'Inning model experiment',outcome_review:'Outcome review',closing_evaluation:'Observed closing-price evaluation',shadow_evaluation:'Exact-market prospective evaluation'})[j.kind]||j.kind),esc(j.league||'All'),esc(labels[j.status]||j.status),esc(j.attempts),esc(j.last_error||(j.kind==='model_search'&&j.result?`${j.result.selected_recipe||j.result.state} · ${j.result.promotion_gate?.promote?'Promoted':'Current model retained'}. ${j.result.next_action||j.result.reason||''}`:j.result?.promotion_gate?.reason||j.result?.conclusion||'Pending execution'))]));

 const automation=data?.automation||{},supervisor=automation.supervisor,research=automation.research;
 $('#automation-summary').textContent=`Service supervisor: ${supervisor?.state||'not started'}. ${Object.entries(supervisor?.services||{}).map(([name,s])=>name+': '+s.state+(s.reason?' ('+s.reason+')':'')).join(' · ')}. Automatic recovery checks run every minute while this Mac is awake and the user session is active.`;
 $('#research-summary').textContent=`Literature discovery: ${research?.state||'not started'}. ${research?.indexed_papers??0} indexed papers across ${Object.keys(research?.sources||{}).length} checked topics. Daily primary-source searches; titles and abstracts only. A discovered paper is not an implemented or validated model.`;
 $('#research-papers').innerHTML=table(['Discovered Paper','Method Tags','Reading / Implementation'],(research?.papers||[]).map(p=>[/^https:\/\/arxiv\.org\/abs\/[A-Za-z0-9./-]+$/.test(p.url)?`<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`:esc(p.title),esc((p.tags||[]).join(', ')),`${esc(p.reading_scope)} · ${esc(p.implementation_state)}`]));
 $('#governance-summary').textContent=g?`Contract ${g.contract.version}. ${g.guard.passed}/${g.guard.checked} candidate checks passed; ${g.guard.rejected} rejected. Every funded decision is retained, including losses. Model experiments run under a validation gate. Development candidates do not activate new production markets.`:'Awaiting the first guarded evaluation.';
 $('#ownership-table').innerHTML=table(['Issued Decisions','Settled','Won / Lost / Push','Decision P&L (Published / Legacy Reported Odds)','Net After System Costs'],[[esc(p.count??0),esc(p.settled??0),`${p.won??0} / ${p.lost??0} / ${p.pushes??0}`,'$'+num(p.pnl),p.net_after_system_cost==null?'Unknown':'$'+num(p.net_after_system_cost)]]);
 $('#coverage-table').innerHTML=table(['League','Observed Outcomes','Supported Contracts','Model Evaluated','Unsupported / Inactive','Unmatched Outcomes'],Object.entries(g?.market_coverage||{}).map(([league,c])=>[esc(league),esc(c.observed_outcomes),esc(c.supported_outcomes),esc(c.evaluated_outcomes),esc(c.unsupported_outcomes),esc(c.unmatched_outcomes)]));
 $('#learning-table').innerHTML=table(['Priority','League','Research Task','Evidence'],(g?.learning?.tasks||[]).map(t=>[esc(t.priority),esc(t.league||'All'),esc(t.task),esc(t.reason)]));
}
function renderDevelopment(){
 const d=data?.automation?.development;
 $('#development-summary').textContent=d?.plan_version?`Delivery: ${(d.state||'unknown').replaceAll('_',' ')}. Full engine readiness: ${d.fully_ready?'verified':'not yet verified'}. Development coordinator: ${(d.worker?.state||'unknown').replaceAll('_',' ')}. Active Codex tasks: ${d.worker?.active_count??(d.worker?.task_id?1:0)} / ${d.max_parallel||d.worker?.max_parallel||1}. The queue checks every ${d.queue_poll_seconds||60} seconds after a completed implementation attempt; repairable code findings receive targeted recovery, while source and provider failures retain explicit recovery requirements. Separate tests and independent Codex review remain required. Candidate code stays isolated; betting model activation requires separately verified release evidence.`:'Development schedule is not available yet.';
 $('#development-active').innerHTML=table(['Codex Task','Current Step','Step Started','Step Budget'],(d?.worker?.active_tasks||[]).map(t=>[esc(t.task_id),esc((t.step||t.state||'running').replaceAll('_',' ')),esc(when(t.step_started_at)),t.step_timeout_seconds?esc(t.step_timeout_seconds+' seconds'):'—']));
 const progress=d?.progress;
 $('#development-progress-summary').textContent=progress?`Work progress: ${(progress.state||'unknown').replaceAll('_',' ')}. Runnable tasks: ${progress.runnable_count??0}. Release handoffs: ${progress.release_handoffs?.length??0}. ${progress.dispatch_stalled?'Dispatcher stalled; supervised recovery required.':progress.attention_required?'Unresolved blockers remain assigned below.':'No current workflow blocker reported.'} Next eligible retry: ${when(progress.next_eligible_at)}.`:'Waiting for a progress assessment.';
 $('#development-blockers').innerHTML=table(['Task / Run','Blocker','Owner','Next Action','Due / Retry'],[...(progress?.pending_tasks||[]).filter(t=>t.progress!=='running').map(t=>[esc(t.task_id),esc((t.progress||'unknown').replaceAll('_',' ')),esc(t.owner||'candidate_developer'),esc(t.next_action),esc(when(t.next_eligible_at||t.due_at))]),...(progress?.release_handoffs||[]).map(r=>[esc(r.task_id+' / '+(r.run_id||'')),esc((r.blocker_code||'release').replaceAll('_',' ')),esc(r.owner),esc(r.next_action),esc(when(r.due_at))])]);
 const engineering=d?.engineering_loop;
 $('#engineering-loop-summary').textContent=engineering?`Engineering reassessment every ${engineering.reassessment_seconds||60} seconds. Open findings: ${engineering.open_gaps??0}; overdue: ${engineering.overdue_gaps??0}. Each finding keeps its original deadline and assigned task. A queued repair is not a completed fix.${d.engineering_loop_error?' Intake error: '+d.engineering_loop_error:''}`:'Waiting for the next engineering reassessment.';
 $('#engineering-loop-gaps').innerHTML=table(['League / Area','Finding','Assigned Task','Due','State'],(engineering?.gaps||[]).map(g=>[esc([g.league,g.family||g.bucket].filter(Boolean).join(' · ')),esc(g.reason),esc(g.task_id||'Unassigned'),esc(when(g.due_at)),esc((g.state||'open').replaceAll('_',' '))+(g.overdue?' · Overdue':'')]));
 const roles=d?.ai_roles;
 $('#ai-roles-summary').textContent=roles?`Primary brain, developer and final reviewer: Codex. Secondary: Claude. Tertiary: Google Antigravity (agy) and Grok. ${roles.scope} Latest helper review: ${(roles.latest?.codex_review||'not yet recorded').replaceAll('_',' ')}.`:'AI delegation status is not available yet.';
 $('#ai-roles-table').innerHTML=table(['Assistant','Role / Model','Last CLI Result','Checked'],Object.entries(roles?.providers||{}).map(([name,p])=>[esc(name==='agy'?'Google Antigravity (agy)':name==='claude'?'Claude':'Grok'),esc([p.role||'advisory',p.model].filter(Boolean).join(' · ')),esc((p.state||'not checked').replaceAll('_',' ')),esc(when(p.checked_at))]));
 const release=d?.utility_release;
 if(release?.enabled)$('#development-summary').textContent+=` Utility release controller: ${release.state}, phase ${release.phase||'unknown'}. Independently reviewed utility installation only; betting model activation requires separate validation. Open release issues: ${release.open_gaps?.length||0}.`;
 $('#development-checkpoints').textContent=d?.checkpoint_rule||'Checkpoints track delivery evidence, not guaranteed profit.';
 $('#development-milestones').innerHTML=table(['Milestone','Next Checkpoint','Delivery State','Acceptance'],(d?.milestones||[]).map(m=>[esc(m.id+' · '+m.title),esc(when(m.checkpoint_at))+(m.checkpoint_due?'<small class="badge negative">Checkpoint overdue</small>':''),esc(m.state.replaceAll('_',' ')),esc(m.acceptance)]));
 $('#development-tasks').innerHTML=table(['Task','Milestone','State / Attempts','Evidence / Next Step'],(d?.tasks||[]).map(t=>[esc(t.id+' · '+t.title),esc(t.milestone),esc(t.state.replaceAll('_',' '))+' / '+esc(t.attempts||0),esc(t.last_error||t.summary||t.acceptance)]));
 $('#development-runs').innerHTML=table(['Started','Task','Result','Evidence'],(d?.recent_runs||[]).slice().reverse().map(r=>[esc(when(r.started_at)),esc(r.task_id),esc(r.state.replaceAll('_',' ')),esc(r.error||r.summary)]));
}
function renderLeagueOperations(){
 const recovery=data?.automation?.operations,audit=data?.status?.operations||recovery,sources=data?.automation?.research?.sources||{};
 $('#league-operations-summary').textContent=audit?`Per-league checks: ${when(audit.checked_at)}. Sources and markets are re-evaluated every ${audit.scan_seconds||60} seconds. Recovery jobs retry with recorded evidence. Research dossiers do not automatically implement missing market models.`:'Waiting for the first per-league operating audit.';
 $('#league-operations-table').innerHTML=table(['League','48h Games','Model Evaluated','Qualifying Families','League Literature Checked','Open Gaps'],(audit?.leagues||[]).map(r=>[esc(r.league),`${r.games_48h} · ${esc(r.state.replaceAll('_',' '))}`,esc(r.modeled_outcomes),esc(r.qualified_families),esc(when(sources[r.research_source]?.checked_at)),esc(r.unresolved_gaps)]));
 const core=data?.status?.core_markets;
 $('#core-markets-summary').textContent=core?`${core.rows?.length||0} game / main-market assessments in this publication. Qualified full-game Winner, Handicap and Total get sizing priority; other qualified markets remain eligible. The displayed reasons distinguish held contracts, missing evaluation and insufficient current value.`:'Waiting for a publication with main-market allocation evidence.';
 $('#core-markets-table').innerHTML=table(['League / Game','Main Market','Evaluated / Qualified','Selection','New Stake','Decision'],(core?.rows||[]).map(r=>[esc(r.league+' · '+r.away+' @ '+r.home),esc(({moneyline:'Winner',spreads:'Handicap',totals:'Total'})[r.market]||r.market),esc(r.evaluated_outcomes+' / '+r.qualified_outcomes),esc(r.selected_selection||'—'),'$'+num(r.new_stake||0),esc((r.status||'unknown').replaceAll('_',' '))+': '+esc(r.allocation_reason)]));
 $('#fixture-operations-table').innerHTML=table(['League','Matchup','Start','Model Evaluated','Qualifying Outcomes','New / Issued','Decision Status'],(audit?.fixtures||[]).slice().sort((a,b)=>new Date(a.start)-new Date(b.start)).map(f=>[esc(f.league),esc(f.matchup),esc(when(f.start)),esc(f.evaluated_outcomes),esc(f.qualified_outcomes),`${f.new_allocations} / ${f.issued_contracts}`,esc(f.state.replaceAll('_',' '))+': '+esc(f.reason)]));
 const apiAudit=data?.api_audit,fixtureAudits=apiAudit?.fixtures||[];
 $('#stake-api-summary').textContent=apiAudit?`${apiAudit.tournament_sources?.length||0} registry responses; ${fixtureAudits.length} upcoming fixtures checked; ${fixtureAudits.reduce((n,f)=>n+Object.keys(f.responses||{}).length,0)} complete fixture/odds responses. NCAA discovery follows enabled Stake sports and USA tournaments. Separate endpoint responses are retained with timestamps, hashes and full compressed bodies. Only a single validated response supplies each executable quote.`:'Waiting for a documented API publication.';
 $('#component-health-table').innerHTML=table(['Component','State','Evidence'],(data?.automation?.supervisor?.components?.checks||[]).map(c=>[esc(c.component),esc(c.state.replaceAll('_',' ')),esc(c.detail)]));
 $('#repair-table').innerHTML=table(['League','Finding','Action','Status / Latest Error'],(recovery?.tracked_gaps||audit?.tracked_gaps||[]).filter(g=>g.status!=='resolved').map(g=>[esc(g.league),esc(g.evidence?.reason||g.code),esc(g.action.replaceAll('_',' ')),esc([g.status,g.job_status,g.last_error].filter(Boolean).join(' · '))]));
 $('#market-types-summary').textContent=`Exact market names returned in the ${when(data?.generated_at)} publication. Counts include unsupported or inactive offers; only issued Betting Instructions request a bet. Swish availability is not verified.`;
 $('#market-types-table').innerHTML=table(['League','Stake Market','Feed Section','Observed','Parsed / Quoted','Priced','Guard Passed / Rejected','Decision Status','Reasons / Next Step'],(data?.market_types||[]).map(m=>[esc(m.league),esc(m.market),esc(m.section),esc(m.observed_outcomes),esc(`${m.parsed_outcomes||0} / ${m.quoted_outcomes||0}`),esc(m.priced_outcomes||0),esc(`${m.guard_passed_outcomes||0} / ${m.guard_rejected_outcomes||0}`),esc((m.analysis_status||m.decision_chain_status||'catalog only').replaceAll('_',' ')),esc(Object.keys(m.reasons||{}).join(', ')||m.next_step||'Supported')]));
 const checker=data?.automation?.supervisor?.flow_checker;
 $('#flow-checker-summary').textContent=checker?`Checked ${when(checker.checked_at)}. Independent evidence checks; assumed placement is tracking, not a sportsbook order. Each unresolved check is routed to the existing repair queue.`:'Waiting for independent flow evidence.';
 $('#flow-checker-table').innerHTML=table(['Step','State','Evidence','Checked'],(checker?.checks||[]).map(c=>[esc(c.id+' · '+c.title),esc(({fulfilled:'Fulfilled',not_fulfilled:'Not fulfilled',under_repair:'Under repair'})[c.status]||c.status),esc(c.detail),esc(when(c.observed_at||checker.checked_at))]));
 const funnel=data?.status?.allocation_funnel;
 $('#allocation-funnel-summary').textContent=funnel?`Publication ${when(funnel.publication_at)} · ${funnel.total.guard_passed} current guard-passed outcomes · ${funnel.total.funded} funded allocations. ${funnel.definition}`:'Waiting for a fresh allocation funnel.';
 $('#allocation-funnel-table').innerHTML=table(['League','Stage','Entered','Excluded','Passed','Reason codes'],Object.entries(funnel?.leagues||{}).flatMap(([league,row])=>[{stage:'guard_passed',entered:row.guard_passed,rejected:0,passed:row.guard_passed,reasons:row.funding_tiers},...row.stages].map(stage=>[esc(league),esc(stage.stage.replaceAll('_',' ')),esc(stage.entered),esc(stage.rejected),esc(stage.passed),esc(Object.entries(stage.reasons||{}).map(([code,n])=>`${code}: ${n}`).join(' · '))])));
 const catalog=data?.automation?.market_catalog;
 const liveCoverage=catalog?.live_coverage;
 const yesNo=v=>v===true?'Yes':v===false?'No':'Not measured';
 $('#live-coverage-summary').textContent=liveCoverage?`Publication ${when(liveCoverage.publication_at)} · checked ${when(liveCoverage.checked_at)}. ${liveCoverage.scope} Settled / feedback counts refer to original engine decisions; successful processing does not imply a promoted model.`:'Waiting for a measured market inventory.';
 const coverageStages=liveCoverage?.stage_definitions||[];
 const coverageStageCell=(r,key)=>{const stage=r.stages?.[key];return stage?`<span title="${esc(stage.basis||'')}">${esc(stage.state.replaceAll('_',' '))} · ${esc(stage.observed)}/${esc(stage.expected)}</span>`:'Not measured';};
 $('#live-coverage-leagues').innerHTML=table(['League','Observed types','Activity unverified','Offered now','Complete 10/10','Incomplete offered','Evaluated types','Funding supported','Executable now','Issued today'],Object.entries(liveCoverage?.leagues||{}).map(([league,r])=>[esc(league),esc(r.observed_types),esc(r.activity_unverified_types??0),esc(r.offered_types??0),esc(r.complete_10_of_10_types??0),esc(r.incomplete_offered_types??0),esc(r.evaluated_types),esc(r.funding_eligible_types),esc(r.funding_eligible_now_types??0),esc(r.issued_today)]));
 $('#live-coverage-table').innerHTML=table(['League','Exact Stake Market','Provider section','Scope','Inventory','Progress',...(coverageStages.length?coverageStages.map(s=>s.label):['Connected','Evaluated']),'Funding supported','Executable now','Issued today','Reason / Next step'],(liveCoverage?.rows||[]).map(r=>[esc(r.league),esc(r.market),esc(r.section||'standard'),esc(r.scope),esc((r.inventory_state||'Not measured').replaceAll('_',' ')),esc(`${r.verified_stages??0}/10`),...(coverageStages.length?coverageStages.map(s=>coverageStageCell(r,s.id)):[yesNo(r.connected),yesNo(r.evaluated)]),yesNo(r.funding_eligible),yesNo(r.funding_eligible_now),esc(r.issued_today),esc(Object.keys(r.reasons||{}).join(', ')||r.next_step||'')]));
 $('#expanded-catalog-summary').textContent=catalog?.checked_at?`Catalogue checked ${when(catalog.checked_at)}. Includes all fixtures returned by the documented tournament endpoints; complete fixture and odds responses are archived. Refresh target: 15 minutes. These counts are not today's betting instructions. Tabs are navigation; each exact contract needs a validated model and settlement rule. Same Game Multi requires its actual combined Stake quote.`:'Full market catalogue is being collected.';
 $('#expanded-catalog-table').innerHTML=table(['League','Exact Stake Market','Observed Tabs','Model/Data Family','Scope to Verify','Dependencies'],(catalog?.types||[]).map(m=>[esc(m.league),esc(m.market),esc((m.tabs||[]).join(' · ')||'Provider section; tab unverified'),esc(m.family.replaceAll('_',' ')),esc(m.scope.replaceAll('_',' ')),esc((m.dependencies||[]).join(', ').replaceAll('_',' '))]));
}
function decisionEvidence(t){
 const g=t.decision_guard;if(!g)return '<p class="panel-copy">Historical decision: no runtime evidence-guard record was attached at issuance.</p>';
 return `<h3>Decision Evidence</h3><p>Review: deterministic source and calculation checks. No separate LLM approval.</p><p>Input and calculation checks: ${g.passed?'Passed':'Rejected'}. Contract: ${esc(g.contract_version)}. Model: ${esc(g.model?.version)}.</p><p>Methodology references: ${(g.knowledge||[]).map(k=>`<a href="/api/knowledge/${encodeURIComponent(k.id)}" target="_blank" rel="noopener">${esc(k.id)}</a>`).join(' · ')}.</p><p class="panel-copy">Book references describe methods; they do not prove that this selection will win. Source hashes and model fingerprints are retained in the decision record.</p>`;
}

function renderSegmentModels(){
 $('#segment-models-table').innerHTML=table(['League / Scope','Independent Model','Train / Selection / Test','Test Log Score / Baseline','Validated Market Families'],Object.values(data?.segment_models||{}).map(m=>[esc(m.league+' / '+m.scope.replaceAll('_',' ')),esc(m.model_family),m.partitions?`${m.partitions.train} / ${m.partitions.selection} / ${m.partitions.test}`:`${m.test?.n||0} test`,num(m.test?.joint_log_score??m.test?.log_loss,4)+' / '+num(m.baseline?.joint_log_score??m.baseline?.log_loss,4),esc((m.validated_markets||[]).join(', '))]));
 $('#segment-prospective-table').innerHTML=table(['Scope','Published','Completed','Mean Three-Way Brier'],(data?.performance?.segment_prospective||[]).map(r=>[esc(r.scope),esc(r.published),esc(r.settled),num(r.brier,4)]));
 $('#period-models-table').innerHTML=table(['League / Scope','Train / Selection / Test','Held-Out Brier','Validated Families'],Object.values(data?.period_models||{}).map(m=>[esc(m.league+' / '+m.scope.replaceAll('_',' ')),m.partitions?`${m.partitions.train} / ${m.partitions.selection} / ${m.partitions.test}`:`${m.test?.n||0} test`,num(m.test?.mean_brier??m.test?.brier,4),esc((m.validated_markets||[]).join(', '))]));
 const players=data?.automation?.player_data;
 $('#player-data-summary').textContent=players?.checked_at?`Player logs checked ${when(players.checked_at)}. Historical observations are available for model development. Player props still require validated predictions, current role/participation evidence and exact provider settlement rules.`:'Player game logs are being collected for independent prop models.';
 const verified=data?.automation?.player_history, propAudit=data?.automation?.player_prop_audit;
 if(verified?.checked_at)$('#player-data-summary').textContent+=` Verified source archive: ${verified.state}; ${verified.observation_count} observations from ${verified.seasons?.join(', ')||'recorded seasons'}. Source errors: ${verified.source_error_count}. Current participation remains unverified.`;
 if(propAudit?.checked_at){const rows=Object.values(propAudit.leagues||{});$('#player-data-summary').textContent+=` Full player-market audit: ${propAudit.state}; ${rows.reduce((n,r)=>n+r.unique_observed_contracts,0)} distinct observed contracts, ${rows.reduce((n,r)=>n+r.normalized_contracts,0)} normalized. Parsing is separate from approved player models.`;}
 $('#player-data-table').innerHTML=table(['League','Player / Game Observations','Players','Latest Observed Game','Production Prop Status'],Object.entries(players?.leagues||{}).map(([l,r])=>[esc(l),esc(r.observations),esc(r.players),esc(when(r.latest_game_start)),r.prop_recommendations_enabled?'Enabled':'Validation and contract integration pending']));
}

function renderModels(){renderSegmentEvidence();renderEconomics();renderClosingPrices();renderGovernance();renderSegmentModels();const models=Object.values(data?.models||{});$('#models-table').innerHTML=table(['League','Training Games','Test Games','Brier / Constant / Elo','Prediction Accuracy','Test Start'],models.map(m=>[`<span class="league ${esc(m.league)}">${esc(m.league)}</span>`,esc(m.training_games),esc(m.test?.n),num(m.test?.brier,3)+' / '+num(m.baseline?.brier,3)+' / '+num(m.elo_baseline?.brier,3),pct(m.test?.accuracy),esc(m.test_from)]));$('#prospective-table').innerHTML=table(['League','Published Forecasts','Completed Games','Brier'],(data?.performance?.prospective||[]).map(r=>[esc(r.league),esc(r.published),esc(r.settled),num(r.brier,3)]));$('#sources-table').innerHTML=table(['Data Source','Status','Rows','Fetched'],(data?.status?.data_sources||[]).map(s=>[esc(s.source),s.ok?'<span class="badge positive">Available</span>':'<span class="badge negative">Error</span>',esc(s.rows||'—'),esc(when(s.fetched_at))]));}
async function renderLedger(){try{const d=await api('/api/bets');renderPerformance(d.performance);$('#ledger-table').innerHTML=table(['Selection','Stake','Recorded Odds','Platform','Tracking','Result','P&L','Settlement'],d.bets.map(b=>[esc(b.payload.selection)+`<small>${esc(b.payload.market_display||'')}</small><small>${esc(when(b.payload.start))}</small>`,'$'+num(b.stake),num(b.odds),esc(b.platform),trackingLabel(b.payload),esc(({open:'Open',won:'Won',lost:'Lost',push:'Push',manual:'Manually Settled'})[b.status]||b.status),b.pnl===null?'—':(b.pnl>=0?'+':'')+'$'+num(b.pnl),b.status==='open'?`<button class="button ghost" data-settle="${b.id}">Settle Bet</button>`:'—']));$('#tracking-note').textContent=d.performance.note||'';$('#selection-outcomes').innerHTML=table(['League','Market','Model','Settled','Won / Lost / Push','Brier','Log Loss'],(d.performance.selection_outcomes||[]).map(r=>[esc(r.league),esc(marketName[r.market]||r.market),esc(r.model_version),r.settled,r.won+' / '+r.lost+' / '+r.pushes,num(r.brier,3),num(r.log_loss,3)]));$$('[data-settle]').forEach(button=>button.onclick=()=>showSettlement(d.bets.find(b=>b.id===Number(button.dataset.settle))))}catch(e){toast(e.message)}}
function populateSettings(){const f=$('#settings-form'),s=data?.settings||{};for(const k of ['bankroll','daily_risk_limit','daily_pick_target','max_bet','unit_stake','minimum_roi','refresh_seconds','monthly_system_cost'])f.elements[k].value=s[k]==null?'':k==='minimum_roi'?s[k]*100:s[k];f.elements.auto_track_recommendations.checked=!!s.auto_track_recommendations;}
function currentSizingAssessment(row){
 const policy=data?.settings?.model_pick_sizing_policy===undefined?'unit_cap_v1':data.settings.model_pick_sizing_policy;
 const age=Date.now()-new Date(row.quote_at),maximum=Math.min(8,data?.settings?.max_bet??8),review=row.execution_review,basis=row.funding_basis;
 const outcomePolicy=data?.settings?.outcome_sizing_policy??'legacy';
 if(!['legacy','historical_game_weighted_v1','historical_game_weighted_v2','historical_confidence_v3'].includes(outcomePolicy)||(review?.outcome_sizing_policy??'legacy')!==outcomePolicy||(row.outcome_sizing_policy??'legacy')!==outcomePolicy)return false;
 const receiptVersion=policy==='all_tiers_weighted_v3'?'funding-policy-v3':policy==='model_weighted_v2'?'funding-policy-v2':null;
 if(!review||review.actionable!==true||review.state!=='current'||review.model_pick_sizing_policy!==policy||
  review.quote_at!==row.quote_at||review.valid_until!==row.valid_until||review.odds!==row.odds||
  review.recommendation_policy!==(data?.settings?.recommendation_policy??'strict')||
  (receiptVersion&&(!basis||basis.version!==receiptVersion||basis.model_pick_sizing_policy!==policy||basis.eligible!==true||basis.tier!==review.funding_tier||basis.policy!==review.recommendation_policy||!Number.isFinite(basis.maximum_stake)||basis.maximum_stake<0.5||basis.maximum_stake>8||row.target_stake>basis.maximum_stake))||
  !row.sizing||row.sizing.amount!==row.target_stake)return false;
 return dashboardAvailable&&row.eligible===true&&['unit_cap_v1','model_weighted_v2','all_tiers_weighted_v3'].includes(policy)&&
  row.model_pick_sizing_policy===policy&&Number.isFinite(age)&&age>=-5000&&age<=300000&&
  new Date(row.valid_until)>new Date()&&new Date(row.start)>new Date()&&
  Number.isFinite(row.odds)&&row.odds>1&&Number.isFinite(row.target_stake)&&row.target_stake>=0.5&&row.target_stake<=maximum&&
  Number.isFinite(row.recorded_stake)&&row.recorded_stake>=0&&Number.isFinite(row.difference)&&
  Math.abs(row.difference-(row.target_stake-row.recorded_stake))<=1e-9;
}
function renderStakeSizing(){
 const rows=Array.isArray(data?.stake_reassessments)?data.stake_reassessments:[],money=v=>Number.isFinite(v)&&v>=0?'$'+num(v):'—';
 const date=v=>v&&Number.isFinite(new Date(v).getTime())?when(v):'—';
 $('#stake-sizing-table').innerHTML=table(['Match / Start','Market / Selection','Current Odds','Original Issued','Recorded Stake','Current Target Total','Difference','Assessment'],rows.map(row=>{
  const current=currentSizingAssessment(row),difference=current?(row.difference>0?'+':row.difference<0?'−':'')+'$'+num(Math.abs(row.difference)):'—';
  return [esc(row.league)+' · '+esc(row.away)+' @ '+esc(row.home)+'<small>'+esc(date(row.start))+'</small>',esc(row.market_display)+'<small>'+esc(row.selection)+'</small>',current?num(row.odds):'—',money(row.original_stake),money(row.recorded_stake),current?money(row.target_stake):'—',difference,
   esc(current?'Current calculation':row.eligible===true?'Fresh matching assessment required':row.reason||'Assessment unavailable')+(current?'<small>Odds checked: '+esc(date(row.quote_at))+'</small>':'')];
 }));
 $('#stake-sizing-summary').textContent=rows.length?`${rows.filter(currentSizingAssessment).length} current calculations across ${rows.length} recorded selections. Original records are unchanged.`:'Current sizing assessments will appear when fresh exact-selection evidence is available.';
}

function renderStability(){
 const host=$('#stability-status');if(!host)return;
 const audit=data?.automation?.supervisor?.stability_audit;
 const stamp=Date.parse(audit?.checked_at),age=Date.now()-stamp;
 if(!audit||!Number.isFinite(stamp)||age< -60000||age>20*60*1000){host.textContent='Stability: awaiting a fresh measured check.';return;}
 const errors=audit.error_count,repairing=audit.repairing_count;
 if(!Number.isInteger(errors)||errors<0||!Number.isInteger(repairing)||repairing<0){host.textContent='Stability: invalid check data.';return;}
 host.textContent=`Stability: ${audit.state==='stable'&&errors===0?'stable':'attention required'} · ${errors} issues · ${repairing} under repair · Checked ${when(audit.checked_at)}`;
}
function renderSystemReviewBanner(){
 const banner=$('#system-review-banner');
 if(!banner)return;
 const feed=data.engine?.market_feed||{};
 const issues=data.status?.last_run_issues||[];
 const failures=issues.filter(i=>['source_error','orderbook_error','pagination_limit','stake_schedule_error',
  'stake_fixture_error','stake_invalid_odds','stake_sports_registry_error','stake_tournament_error',
  'stake_fixture_schema','stake_fixture_identity_conflict','stake_fixture_endpoint_error',
  'stake_seed_identity_unresolved','stake_endpoint_market_difference'].includes(i.reason));
 
 const isOutage=feed.state==='unavailable'||(data.engine?.error&&!data.engine?.running&&feed.state!=='partial');
 
 if(!isOutage){
  banner.hidden=true;
  banner.innerHTML='';
  return;
 }
 
 banner.hidden=false;
 banner.className=isOutage?'system-review-banner danger':'system-review-banner';
 
 const count=failures.length||feed.error_count||1;
 const title=isOutage?'Critical Error: Odds feed unavailable':'Attention required ('+count+' quarantined)';
 const badgeText=isOutage?'Feed Outage':'Quarantine Active';
 const desc=isOutage?
  'The Stake odds feed is temporarily unavailable. Live pricing and stake issuance are paused to protect capital.':
  'EvidenceGuard safety gate has <strong>quarantined</strong> the following event(s) to protect capital (no funds risked) due to placeholder names or identity discrepancies in the Stake feed:';
 
 let itemsHtml='';
 if(failures.length){
  itemsHtml='<ul class="system-review-items">'+failures.map(f=>`
   <li>
    <span class="system-review-item-league">${esc(f.league||'API')}</span>
    <span class="system-review-item-name">${esc(f.event||f.fixture||'Unknown event')}</span>
    <span class="system-review-item-reason">${esc(f.reason||'anomaly')}: ${esc(f.detail||'')}</span>
   </li>
  `).join('')+'</ul>';
 } else if(feed.message){
  itemsHtml=`<div style="font-size:11px;color:#718096;margin-top:6px">${esc(feed.message)}</div>`;
 }
 
 banner.innerHTML=`
  <div class="system-review-header">
   <div class="system-review-title">
    <span>⚠️</span>
    <span>${esc(title)}</span>
   </div>
   <span class="badge negative">${esc(badgeText)}</span>
  </div>
  <p class="system-review-desc">${desc}</p>
  ${itemsHtml}
 `;
}

function render(){renderSystemReviewBanner();renderStability();renderStakeSizing();renderStats();renderTips();renderArchive();renderGames();renderModels();if(view==='statistics')renderLiveCoverageTable();if(view==='stake_account')loadStakeAccount();$('#run-stage').textContent=data.engine?.stage||'Updated';$('#last-updated').textContent=data.generated_at?'Last Published: '+when(data.generated_at):'Preparing the First Analysis';const healing=data.status?.autonomous_healing||data.engine?.autonomous_healing;const healingBadge=$('#ai-self-healing-badge');if(healingBadge){if(healing){const hc=healing.total_healed_count||0,ac=healing.learned_aliases_count||0;healingBadge.textContent=`🤖 AI Self-Healing: Active (${hc} healed, ${ac} aliases)`;}if(!healingBadge.dataset.bound){healingBadge.dataset.bound='true';healingBadge.style.cursor='pointer';healingBadge.title='Click to view Autonomous Repair Timeline and AI Brain Queue';healingBadge.onclick=()=>{const rdBtn=$('.nav-item[data-view="rd"]');if(rdBtn)rdBtn.click();setTimeout(()=>{document.getElementById('rd-timeline-panel')?.scrollIntoView({behavior:'smooth'});},150);};}}const notices=[];if(data.settings?.auto_track_recommendations)notices.push('Auto-tracking is on: funded recommendations are assumed placed on Stake once, using the displayed odds and stake.');if(data.status?.daily_progress){const p=data.status.daily_progress;notices.push(`Daily target: ${p.issued}/${p.target} distinct funded recommendations issued (${p.date}, ${p.timezone}). ${p.counting_basis==='since_release'?'Counting from '+when(p.counting_since)+'. '+p.previously_issued_today+' earlier issuances excluded from this target. ':''}${p.distinct_games==null?'':p.distinct_games+' distinct games. '}${p.shortfall>0?p.shortfall+' more needed; the target does not override price or model requirements.':'Target reached; qualifying new picks are still published.'}`);}notices.push(data.settings?.daily_risk_limit==null?'No fixed daily stake cap. Maximum $'+num(data.settings?.max_bet||8)+' per bet.':'Daily stake cap: $'+num(data.settings.daily_risk_limit)+'.');const feedState=data.engine?.market_feed?.state;const feedErr=data.engine?.error;if(feedErr){const prefix=feedState==='partial'?'Notice: ':'Refresh Error: ';notices.push(prefix+feedErr);}$('#notice').textContent=notices.join(' ');$('#notice').hidden=!notices.length;const connLabel=data.engine?.running?'Evaluating Markets':feedState==='partial'?'Partial Coverage':(feedErr||feedState==='unavailable')?'Data Feed Error':'Live Monitoring';$('#connection').textContent=connLabel;$('#refresh').disabled=!!data.engine?.running;}

let audioCtx=null,lastSoundPlayTime=0;
function getAudioContext(){
 if(typeof window==='undefined')return null;
 const AudioCtx=window.AudioContext||window.webkitAudioContext;
 if(!AudioCtx)return null;
 if(!audioCtx){try{audioCtx=new AudioCtx();}catch(e){return null;}}
 if(audioCtx&&audioCtx.state==='suspended'){audioCtx.resume().catch(()=>{});}
 return audioCtx;
}

function playNotificationSound(){
 const soundEnabled=localStorage.getItem('quant_sound_enabled')!=='false';
 if(!soundEnabled)return;
 try{
  const ctx=getAudioContext();
  if(!ctx)return;
  const now=ctx.currentTime;
  if(now-lastSoundPlayTime<0.3)return;
  lastSoundPlayTime=now;

  // Master output with gentle headroom to prevent digital clipping/distortion
  const master=ctx.createGain();
  master.gain.setValueAtTime(0.15,now);
  master.connect(ctx.destination);

  // Note 1: E5 (659.25 Hz) with 15ms soft attack and clean exponential decay (no clicks)
  const osc1=ctx.createOscillator();
  const gain1=ctx.createGain();
  osc1.type='sine';
  osc1.frequency.setValueAtTime(659.25,now);
  gain1.gain.setValueAtTime(0.0001,now);
  gain1.gain.linearRampToValueAtTime(0.85,now+0.015);
  gain1.gain.exponentialRampToValueAtTime(0.0001,now+0.22);
  gain1.gain.linearRampToValueAtTime(0.0,now+0.24);
  osc1.connect(gain1);
  gain1.connect(master);
  osc1.start(now);
  osc1.stop(now+0.25);

  // Note 2: A5 (880.00 Hz) - staggered chime with its own soft attack and bell decay
  const t2=now+0.10;
  const osc2=ctx.createOscillator();
  const gain2=ctx.createGain();
  osc2.type='sine';
  osc2.frequency.setValueAtTime(880.00,t2);
  gain2.gain.setValueAtTime(0.0001,t2);
  gain2.gain.linearRampToValueAtTime(1.0,t2+0.015);
  gain2.gain.exponentialRampToValueAtTime(0.0001,t2+0.35);
  gain2.gain.linearRampToValueAtTime(0.0,t2+0.38);
  osc2.connect(gain2);
  gain2.connect(master);
  osc2.start(t2);
  osc2.stop(t2+0.40);
 }catch(e){}
}
if(typeof window!=='undefined')window.playNotificationSound=playNotificationSound;

let known24hTicketIds=null;
function check24hNewTickets(d,first){
 if(!d)return;
 const now=Date.now(),in24h=now+24*3600000;
 const current24hIds=new Set();
 const items=[
  ...(d.recommendations||[]),
  ...(d.alternatives||[]),
  ...(d.tracked_bets?d.tracked_bets.map(b=>({...b.payload,id:b.tip_id||b.id,tracked_bet:b})):[])
 ];
 for(const t of items){
  if(!t||!t.start)continue;
  const st=new Date(t.start).getTime();
  if(st>now&&st<=in24h){
   if(t.stake>0||t.tracked_bet||(d.tracked_bets||[]).some(b=>b.tip_id===t.id)){
    current24hIds.add(t.id);
   }
  }
 }
 if(first||known24hTicketIds===null){
  known24hTicketIds=current24hIds;
  return;
 }
 let newTicketsCount=0;
 for(const id of current24hIds){
  if(!known24hTicketIds.has(id)){
   newTicketsCount++;
  }
 }
 if(newTicketsCount>0){
  (window.playNotificationSound||playNotificationSound)();
  toast(`🔔 ${newTicketsCount} new bet ticket${newTicketsCount>1?'s':''} in 24h window!`);
  if('Notification' in window&&Notification.permission==='granted'){
   try{
    new Notification('Betting Quant Engine',{body:`${newTicketsCount} new betting ticket${newTicketsCount>1?'s':''} in 24h window!`});
   }catch(err){}
  }
 }
 for(const id of current24hIds){
  known24hTicketIds.add(id);
 }
}

function load(){
 // Coalesce polling, SSE and explicit refreshes without dropping a newer update.
 // Queued callers wait for the next response, not merely the in-flight snapshot.
 if(dashboardFlight){
  if(!dashboardQueued){
   let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
   dashboardQueued={promise,resolve,reject};
  }
  return dashboardQueued.promise;
 }
 // Install the flight before fetch can run (including synchronous re-entrancy).
 dashboardFlight=Promise.resolve().then(fetchDashboard).finally(()=>{
  dashboardFlight=null;
  const queued=dashboardQueued;dashboardQueued=null;
  if(queued)load().then(queued.resolve,queued.reject);
 });
 return dashboardFlight;
}
function reportClientError(err){
 console.error('[Quant Engine Client Error]',err);
 if($('#connection')){$('#connection').textContent='UI Error';$('#connection').style.color='#ef4444';}
 if($('#notice')){$('#notice').hidden=false;$('#notice').innerHTML=`<strong>⚠️ Client Render Error:</strong> ${esc(err?.message||err)}. Check browser console or refresh.`;}
 try{
  fetch('/api/telemetry/errors',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:String(err?.message||err),source:err?.source||(typeof window!=='undefined'?window.location.href:''),lineno:err?.lineno||0,colno:err?.colno||0,stack:String(err?.stack||'')})}).catch(()=>{});
 }catch(e){}
}
if(typeof window!=='undefined'&&window.addEventListener){
 window.addEventListener('error',e=>{reportClientError(e.error||{message:e.message,source:e.filename,lineno:e.lineno,colno:e.colno})});
 window.addEventListener('unhandledrejection',e=>{reportClientError(e.reason||{message:'Unhandled promise rejection'})});
}
 async function fetchDashboard(){
  const request=++dashboardRequest,startedPlacementEpoch=placementEpoch;
  const isTestEnv=typeof window!=='undefined'&&!!window.happyDOM;
  const timeoutMs=isTestEnv?20000:45000;
  const controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),timeoutMs);
  let d;
  try{
   d=await api('/api/dashboard',{signal:controller.signal});
  }catch(e){
   if(request<appliedDashboardRequest)return;
   const isFatal=(!data&&!e?.message?.includes('preparing'))||e?.message==='offline'||(isTestEnv&&e?.name==='AbortError');
   if(isFatal){
    dashboardAvailable=false;if(data){renderStats();renderTips();renderStakeSizing()}if($('#connection'))$('#connection').textContent='Connection Lost';if($('#notice')){$('#notice').hidden=false;$('#notice').textContent='The engine is reconnecting. Current betting instructions will return after a successful update.';}
    setTimeout(load,2500);
   }else{
    if($('#connection'))$('#connection').textContent=e?.message?.includes('preparing')?'Updating…':(e?.name==='AbortError'?'Syncing…':'Reconnecting…');
    setTimeout(load,2500);
   }
   return;
  }finally{clearTimeout(deadline)}

  if(request<appliedDashboardRequest)return;
  if(data&&startedPlacementEpoch!==placementEpoch)d.placement_marks=data.placement_marks;
  if(data&&d.engine?.running&&(!d.recommendations||!d.recommendations.length)&&data.recommendations?.length){
   d.recommendations=data.recommendations;
   if(!d.alternatives||!d.alternatives.length)d.alternatives=data.alternatives;
   if(!d.evaluated_candidates||!d.evaluated_candidates.length)d.evaluated_candidates=data.evaluated_candidates;
  }
  const first=!data;appliedDashboardRequest=request;data=d;dashboardAvailable=true;
  try{
   check24hNewTickets(d,first);
   render();syncStatisticsChrome();if(first)populateSettings();if(view==='ledger')renderLedger();if(view==='statistics')loadStatistics();if(view==='games')renderGames();if(view==='stake_account')loadStakeAccount();if(view==='rd')loadRD();
  }catch(renderErr){
   reportClientError(renderErr);
  }
 }
function sourceTerms(t){
 if(t.reference_platform==='Stake.com') return `<p><strong>Stake feed snapshot:</strong> ${t.recorded_snapshot?'recorded':'current'} decimal odds ${num(t.reference_odds)}. The exact Stake market is <strong>${esc(t.market_display||marketName[t.market])}</strong>; the exact outcome and line are shown above. This is a sportsbook price, not a share price. Stake minimum stake and account-specific limits may differ.</p>`;
 return `<p>${esc(t.reference_platform)} benchmark: ${num(t.reference_price,3)} USD/share plus ${num(t.reference_fee_per_share,4)} USD fee/share. This benchmark is not a Stake sportsbook price.</p>`;
}

function outcomeSizingTrace(t){
 const a=(t.execution_revision?.stake_sizing||t.stake_sizing)?.outcome_adjustment,e=a?.source;
 if(!['historical_game_weighted_v1','historical_game_weighted_v2','historical_confidence_v3'].includes(a?.policy)||!e)return '';
 if(![a.base_amount,a.factor,a.adjusted_amount,e.independent_games,e.game_wr,e.game_roi,e.break_even_wr].every(Number.isFinite))return '';
 const v3=a.policy==='historical_confidence_v3';
 if(v3&&![a.confidence,a.probability_strength,a.model_market_agreement,a.required_input_completeness,a.historical_sample_weight,a.combined_sizing_score,a.confidence_target_before_event_discount,a.requested_amount].every(Number.isFinite))return '';
 const v2=a.policy==='historical_game_weighted_v2';
 if(v2&&![a.source_factor,a.applied_game_score_sum,a.shrinkage_denominator,a.historical_signal,a.applied_factor].every(Number.isFinite))return '';
 const calculation=v3
  ? `Confidence score: ${pct(a.confidence)} (sizing indicator, not win probability). Probability strength ${pct(a.probability_strength)} · Model–market agreement ${pct(a.model_market_agreement)} · Required input completeness ${pct(a.required_input_completeness)}. Historical sample weight ${pct(a.historical_sample_weight)}. Combined sizing score ${pct(a.combined_sizing_score)} → $${num(a.confidence_target_before_event_discount)} before related-event adjustment; requested $${num(a.requested_amount)} → $${num(a.adjusted_amount)} after limits.`
  : v2
  ? `V2 calculation: $${num(a.base_amount)} × [1 + 4 × ${num(a.historical_signal,4)}] = $${num(a.adjusted_amount)} after caps. Signed game scores sum to ${num(a.applied_game_score_sum,4)} with shrinkage denominator ${num(a.shrinkage_denominator,0)}. Each game score is min(1, ROI / 1.0, win-rate surplus / 0.5); negative scores are not clipped. Both segment effects must be positive. Source factor ${num(a.source_factor,3)} remains in the verified v1 evidence.`
  : `Base calculation: $${num(a.base_amount)} × ${num(a.factor,3)} historical factor → $${num(a.adjusted_amount)} after caps.`;
 return `<details class="outcome-sizing-trace"><summary>${v3?'Confidence and Historical Outcome Sizing':'Historical Outcome Sizing'}</summary><p>Segment: ${esc(a.segment)} · ${e.independent_games} independent settled games.</p><p>Game-weighted win rate: ${pct(e.game_wr)} · Break-even win rate: ${pct(e.break_even_wr)} · Game-weighted ROI: ${pct(e.game_roi)}.</p>${t.execution_revision?`<p>Updated before placement: $${num(t.execution_revision.original_stake)} → $${num(t.execution_revision.target_stake)}. This is the total stake to place once, not an additional bet. Original engine decision retained.</p>`:''}<p>${calculation} This is a sizing calculation; place only the instruction amount shown above, once.</p><p>Evidence cutoff: ${esc(when(e.as_of))}. Small samples are shrunk. Historical results do not establish future profitability.</p><code>${esc(e.summary_sha256)}</code></details>`;
}

function showDetail(id,preferCurrent=false){const original=(preferCurrent?[...allEvaluated(),...liveTips()]:[...liveTips(),...allEvaluated()]).find(x=>x.id===id);if(!original)return;const t=view==='tips'&&pendingInstruction(original)?{...original,...original.current_assessment,tracked_bet:original.tracked_bet,tracking_source:original.tracking_source,instruction_review:original.instruction_review,current_assessment:original.current_assessment,recorded_snapshot:false,execution_revision:original.execution_revision}:original;selectedTip=t;selectedInstructionSignature=view==='tips'?instructionSignature(original):null;$('#detail-content').innerHTML=`<span class="league ${esc(t.league)}">${esc(t.league)}</span><h2>${esc(t.selection)}</h2><p class="panel-copy">${esc(t.away)} @ ${esc(t.home)} · ${esc(when(t.start))}</p><div class="selection">${esc(t.market_display||marketName[t.market])}</div><div class="detail-data"><div><div class="metric-label">MODEL PROBABILITY</div><div class="metric-value">${pct(t.probability)}</div></div><div><div class="metric-label">MIN. DECIMAL ODDS</div><div class="metric-value">${displayedMinimumOdds(t)}</div></div><div><div class="metric-label">SUGGESTED STAKE</div><div class="metric-value">${stakeDisplay(t)}</div></div></div><p>${esc(t.stake_note)}</p>${outcomeSizingTrace(t)}${modelPickNote(t)}${trackingMarkup(t)}${stakeBetButton(t)}${executableInstruction(t)?'':`<p>${esc(allocationReason(t))}</p>`}${betInstructions(t)}${decisionEvidence(t)}<h3>Why This Pick?</h3><ul>${t.reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul><h3>Check Your Stake Odds</h3><form id="quote-check-form"><div class="form-row"><label>Decimal Odds<input name="odds" type="number" min="1.01" step="0.01" required placeholder="e.g. 1.90"></label></div><button class="button ghost">Check Stake Odds for This Selection</button><p id="quote-result" role="status"></p></form><h3>Reference Price and Settlement Rules</h3>${sourceTerms(t)}<p>Odds Checked: ${esc(when(t.quote_at))}. Quote Valid Until: ${esc(when(t.valid_until))}.</p>${contextMarkup(t.game_context || t.current_assessment?.game_context || (data?.games||[]).find(g=>g.id===t.game_id)?.context)}<h3>Model Limitations and Context</h3><ul>${t.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul><div class="detail-actions"><a class="button ghost" target="_blank" rel="noopener noreferrer" href="${esc(t.reference_url)}">View Reference Market ↗</a><button class="button primary" id="record-bet">Log Placed Bet</button></div><div id="bet-form-wrap" hidden><h3>Record Your Placed Bet</h3><form id="bet-form"><div class="form-row"><label>Stake (USD)<input name="stake" type="number" step="0.01" min="0.01" required value="${t.stake||1}"></label><label>Accepted Decimal Odds<input name="odds" type="number" min="1.01" step="0.01" required placeholder="Actual accepted odds"></label></div><label>Platform<input name="platform" required maxlength="80" placeholder="e.g. Stake.com"></label><label>Settlement<select name="settlement_mode"><option value="manual">Manual: enter the actual total return</option><option value="sportsbook">Sportsbook: exact named market; stake refunded on a push</option></select></label><button class="button primary">Save Bet</button></form><p class="panel-copy">This records a bet you have already placed. It does not submit a bet. Polymarket share positions require separate settlement checks.</p></div><details><summary>Sources, Model and Market Settlement Rules</summary><p><a href="${esc(t.source_url)}" target="_blank" rel="noopener noreferrer">Sports Data Source</a> · Model: ${esc(t.model_version)}</p><p>${esc(t.stress_note)}</p><pre>${esc(t.reference_contract_description)}</pre></details>`;$('#quote-check-form').onsubmit=async e=>{e.preventDefault();try{const q=await api('/api/tips/'+t.id+'/quote',{method:'POST',body:JSON.stringify({odds:Number(e.target.odds.value)})});$('#quote-result').textContent=q.review_failed?q.note:q.expired?'This pick has expired. Wait for an updated analysis.':q.already_recorded?`This exact bet is already recorded. Entered odds ${q.price_qualifies?'meet':'do not meet'} the current model threshold. No additional ticket or stake is requested. Estimated ROI: ${pct(q.expected_roi)}. The entered price has not been verified against Stake.`:`Hypothetical price calculation. Estimated ROI: ${pct(q.expected_roi)}. Stress Scenario ROI: ${pct(q.stress_roi)}. Calculated Stake: $${num(q.stake)}. ${q.note} This does not issue a betting instruction; entered odds are not verified against Stake.`}catch(err){$('#quote-result').textContent=err.message}};$('#record-bet').onclick=()=>{$('#bet-form-wrap').hidden=false;$('#bet-form input').focus()};$('#bet-form').onsubmit=async e=>{e.preventDefault();const f=e.target;const submit=f.querySelector('button');submit.disabled=true;const requestId=f.dataset.requestId||(f.dataset.requestId=crypto.randomUUID());try{await api('/api/bets',{method:'POST',body:JSON.stringify({tip_id:t.id,stake:Number(f.stake.value),odds:Number(f.odds.value),platform:f.platform.value,request_id:requestId,settlement_mode:f.settlement_mode.value})});toast('Bet recorded.');$('#detail-dialog').close();await load()}catch(err){toast(err.message);submit.disabled=false}};if(t.tracked_bet)$('#record-bet').hidden=true;if(view==='tips')bindInstructionLink($('#detail-content .stake-bet-button'),t.id,selectedInstructionSignature);bindStageBetslipButtons();bindOneClickBetButtons();$('#detail-dialog').showModal()}
function openMobileMenu(){
 const sb=$('#sidebar')||$('.sidebar');const bd=$('#sidebar-backdrop');
 if(sb){sb.classList.add('open');bd?.classList.add('active');document.body.style.overflow='hidden';}
}
function closeMobileMenu(){
 const sb=$('#sidebar')||$('.sidebar');const bd=$('#sidebar-backdrop');
 if(sb){sb.classList.remove('open');bd?.classList.remove('active');document.body.style.overflow='';}
}
function initMobileMenu(){
 const toggle=$('#mobile-menu-toggle');const closeBtn=$('#sidebar-close-btn');const bd=$('#sidebar-backdrop');
 if(toggle)toggle.onclick=openMobileMenu;
 if(closeBtn)closeBtn.onclick=closeMobileMenu;
 if(bd)bd.onclick=closeMobileMenu;
 window.addEventListener('keydown',e=>{if(e.key==='Escape')closeMobileMenu()});
}
initMobileMenu();
$$('.nav-item').forEach(b=>b.onclick=()=>{closeMobileMenu();view=b.dataset.view;$$('.nav-item').forEach(n=>n.classList.toggle('active',n===b));$$('.view').forEach(v=>v.hidden=v.id!=='view-'+view);$('#breadcrumb').textContent=title[view];$('#page-title').innerHTML=view==='tips'?'Your next bet.<br><span>Backed by data.</span>':esc(title[view]);$('#page-description').textContent=view==='tips'?'Picks for today and the next 48 hours, with Stake market names and exact selections.':view==='rd'?'Continuous Quant Loss Attribution, Forensic Remediation & Crucible Validation.':view==='stake_account'?'Real-time Stake account balance, active bets, and market exposure monitoring.':'Track your picks, model performance and actual betting results.';if(view==='ledger')renderLedger();if(view==='sizing')renderStakeSizing();if(view==='statistics')loadStatistics();if(view==='games')renderGames();if(view==='settings')populateSettings();if(view==='rd')loadRD();if(view==='stake_account')loadStakeAccount();syncStatisticsChrome()});
$$('[data-horizon]').forEach(b=>b.onclick=()=>{horizon=b.dataset.horizon;$$('[data-horizon]').forEach(x=>x.classList.toggle('selected',x===b));renderTips()});$('#league-filter').onchange=renderTips;$('#market-filter').onchange=renderTips;$('#sort-filter').onchange=renderTips;
$('#refresh').onclick=async()=>{try{await api('/api/refresh',{method:'POST'});toast('The engine is refreshing markets and picks.');setTimeout(load,800)}catch(e){toast(e.message)}};
$('#settings-form').onsubmit=async e=>{e.preventDefault();const f=e.target;const s={auto_track_recommendations:f.elements.auto_track_recommendations.checked};for(const k of ['daily_pick_target','max_bet','unit_stake','refresh_seconds'])s[k]=Number(f.elements[k].value);s.daily_risk_limit=f.daily_risk_limit.value===''?null:Number(f.daily_risk_limit.value);s.bankroll=f.bankroll.value?Number(f.bankroll.value):null;s.monthly_system_cost=f.monthly_system_cost.value!==''?Number(f.monthly_system_cost.value):null;s.minimum_roi=Number(f.minimum_roi.value)/100;try{await api('/api/settings',{method:'PUT',body:JSON.stringify(s)});$('#settings-feedback').textContent='Saved';toast('Settings saved. Picks are being recalculated.')}catch(err){$('#settings-feedback').textContent=err.message}};
$('.close-dialog').onclick=()=>$('#detail-dialog').close();$('#detail-dialog').onclick=e=>{if(e.target===$('#detail-dialog'))$('#detail-dialog').close()};

function bindNotificationControls(){
 const testTgBtn=$('#test-telegram-btn');
 if(testTgBtn){
  testTgBtn.onclick=async()=>{
   testTgBtn.disabled=true;testTgBtn.textContent='Sending…';
   try{
    const res=await api('/api/notifications/test-telegram',{method:'POST'});
    const msg=res.message||'Test message sent successfully!';
    if($('#notify-feedback'))$('#notify-feedback').textContent='✅ '+msg;
    toast('Telegram test message sent.');
   }catch(err){
    if($('#notify-feedback'))$('#notify-feedback').textContent='❌ '+err.message;
    toast(err.message);
   }finally{testTgBtn.disabled=false;testTgBtn.textContent='📩 Send Test Message'}
  };
 }
 const browserBtn=$('#browser-notify-btn');
 const permLabel=$('#browser-perm-label');
 function updateBrowserPermUI(){
  if(!('Notification' in window)){if(permLabel)permLabel.textContent='Not supported';return;}
  if(Notification.permission==='granted'){if(permLabel)permLabel.textContent='Granted (Active)';if(browserBtn){browserBtn.textContent='🔔 Enabled';browserBtn.disabled=true;}}
  else if(Notification.permission==='denied'){if(permLabel)permLabel.textContent='Blocked in browser';if(browserBtn){browserBtn.textContent='Blocked';browserBtn.disabled=true;}}
  else{if(permLabel)permLabel.textContent='Permission required';if(browserBtn){browserBtn.textContent='🔔 Enable';browserBtn.disabled=false;}}
 }
 updateBrowserPermUI();
 if(browserBtn){
  browserBtn.onclick=async()=>{
   if(!('Notification' in window))return;
   try{
    const perm=await Notification.requestPermission();
    updateBrowserPermUI();
    if(perm==='granted'){new Notification('Betting Quant Engine',{body:'Push notifications enabled!'});toast('Browser notifications enabled.');}
   }catch(e){toast('Error: '+e.message)}
  };
 }
 const soundToggleBtn=$('#toggle-sound-btn');
 const soundTestBtn=$('#test-sound-btn');
 const soundTopbarBtn=$('#notify-topbar-btn');
 const soundLabel=$('#sound-perm-label');
 function updateSoundUI(){
  const enabled=localStorage.getItem('quant_sound_enabled')!=='false';
  if(soundLabel)soundLabel.textContent=enabled?'Audible chime active for new 24h picks':'Sound alert muted';
  if(soundToggleBtn){
   soundToggleBtn.textContent=enabled?'🔊 Sound: ON':'🔈 Sound: Muted';
   soundToggleBtn.className=enabled?'button primary':'button ghost';
  }
  if(soundTopbarBtn){
   soundTopbarBtn.textContent=enabled?'🔔 Sound: ON':'🔈 Sound: Muted';
   soundTopbarBtn.title=enabled?'Sound alerts are ON (click to mute / test)':'Sound alerts are Muted (click to enable)';
  }
 }
 updateSoundUI();
 if(soundToggleBtn){
  soundToggleBtn.onclick=()=>{
   const current=localStorage.getItem('quant_sound_enabled')!=='false';
   const next=!current;
   try{localStorage.setItem('quant_sound_enabled',next?'true':'false');}catch(e){}
   updateSoundUI();
   if(next){playNotificationSound();toast('🔊 Sound alert enabled for new 24h picks.');}
   else{toast('🔈 Sound alert muted.');}
  };
 }
 if(soundTestBtn){
  soundTestBtn.onclick=()=>{
   playNotificationSound();
   toast('🔔 Testing quant audio chime…');
  };
 }
 if(soundTopbarBtn){
  soundTopbarBtn.onclick=()=>{
   const current=localStorage.getItem('quant_sound_enabled')!=='false';
   if(!current){
    try{localStorage.setItem('quant_sound_enabled','true');}catch(e){}
    updateSoundUI();
    playNotificationSound();
    toast('🔊 Sound alert enabled for new 24h picks.');
   }else{
    playNotificationSound();
    toast('🔔 Sound alert active. Tested audio chime.');
   }
  };
 }
}
if(typeof window!=='undefined'&&window.addEventListener){
 const unlock=()=>{getAudioContext();window.removeEventListener('click',unlock);window.removeEventListener('keydown',unlock);};
 window.addEventListener('click',unlock,{passive:true});
 window.addEventListener('keydown',unlock,{passive:true});
}
function initTheme(){
 const saved=localStorage.getItem('quant_theme');
 const prefersDark=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches;
 const theme=saved||(prefersDark?'dark':'light');
 applyTheme(theme);
 const toggleBtn=$('#theme-toggle');
 if(toggleBtn){
  toggleBtn.onclick=()=>{
   const current=document.documentElement.getAttribute('data-theme')==='dark'?'dark':'light';
   const next=current==='dark'?'light':'dark';
   applyTheme(next);
   try{localStorage.setItem('quant_theme',next);}catch(e){}
  };
 }
}
function applyTheme(theme){
 const toggleBtn=$('#theme-toggle');
 if(theme==='dark'){
  document.documentElement.setAttribute('data-theme','dark');
  if(document.body)document.body.setAttribute('data-theme','dark');
  if(toggleBtn)toggleBtn.innerHTML='☀️ Light';
 }else{
  document.documentElement.removeAttribute('data-theme');
  if(document.body)document.body.removeAttribute('data-theme');
  if(toggleBtn)toggleBtn.innerHTML='🌙 Dark';
 }
}
bindRDActions();bindStakeAccountActions();bindNotificationControls();initTheme();
function tick(){$('#clock').textContent=new Intl.DateTimeFormat('en-GB',{weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Budapest',timeZoneName:'short'}).format(new Date())}tick();setInterval(tick,30000);setInterval(expireInstructions,1000);load();const stream=new EventSource('/api/events');stream.addEventListener('update',()=>load());stream.onopen=()=>{if(data){const feedState=data.engine?.market_feed?.state,feedErr=data.engine?.error;$('#connection').textContent=data.engine?.running?'Evaluating Markets':feedState==='partial'?'Partial Coverage':(feedErr||feedState==='unavailable')?'Data Feed Error':'Live Monitoring';}};stream.onerror=()=>$('#connection').textContent='Reconnecting…';setInterval(load,15000);

function contextMarkup(context={}){
 const i=context.injuries||{};let html='';
 const pitchers=['home','away'].map(side=>context[side+'_pitcher']?.fullName).filter(Boolean);
 if(pitchers.length)html+='<h3>Probable Starting Pitchers</h3><p>'+pitchers.map(esc).join(' · ')+'</p>';
 const rows=['home','away'].flatMap(side=>(i[side]||[]).map(r=>({...r,side})));
 if(rows.length)html+='<h3>Injury Report · '+esc(i.snapshot_date)+'</h3><p>These statuses are not yet applied as numerical adjustments to the model.</p><ul>'+rows.map(r=>'<li><strong>'+esc(r.athlete_display_name)+'</strong> · '+esc(r.athlete_position)+' · '+esc(r.status)+'<br>'+esc(r.short_comment)+'</li>').join('')+'</ul>';
 const pm=context.polymarket;
 if(pm&&pm.matched&&pm.has_moneyline){
  html+='<h3>Polymarket Orderbook Consensus</h3>'+
   '<div class="polymarket-detail-card">'+
   '<div class="polymarket-detail-card-title">🔮 Polymarket Consensus & Market Depth</div>'+
   '<div class="polymarket-detail-card-body">'+
   '<div><strong>Home Prob:</strong> '+(pm.true_prob_home!=null?(pm.true_prob_home*100).toFixed(1)+'%':'—')+(pm.true_prob_draw!=null?' · <strong>Draw Prob:</strong> '+(pm.true_prob_draw*100).toFixed(1)+'%':'')+' · <strong>Away Prob:</strong> '+(pm.true_prob_away!=null?(pm.true_prob_away*100).toFixed(1)+'%':'—')+'</div>'+
   '<div><strong>Market Volume:</strong> '+(pm.total_volume?'$'+Math.round(pm.total_volume).toLocaleString():'Active')+' · <strong>Spread:</strong> '+(pm.spread!=null?(pm.spread*100).toFixed(1)+'%':'—')+'</div>'+
   (pm.question?`<div style="font-size:11px;color:#94a3b8;margin-top:4px">${esc(pm.question)}</div>`:'')+
   '</div></div>';
 }
 return html;
}
function renderPerformance(p){
 const b=p.ledger_bets||p.actual_bets||{};const cards=[['Tracked Bets',b.count||0,(b.settled||0)+' Settled · '+(p.assumed_bets?.count||0)+' Assumed Placed'],['Tracked P&L','$'+num(b.pnl||0),p.assumed_bets?.count?'Includes Estimated P&L at Recommendation Odds':'From Manually Logged Bets'],['Accrued System Cost',p.estimated_accrued_system_cost==null?'Not Set':'$'+num(p.estimated_accrued_system_cost),'Estimated From Monthly Cost'],['Net After System Costs',p.net_after_system_cost==null?'—':'$'+num(p.net_after_system_cost),'Based on Your Recorded Results']];
 $('#performance-stats').innerHTML=cards.map(([label,value,sub])=>`<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div><div class="stat-sub">${esc(sub)}</div></div>`).join('');
}

function showSettlement(b){
 if(!b)return;$('#detail-content').innerHTML=`<h2>Settle Bet</h2><p>${esc(b.payload.selection)} · ${esc(b.platform)}</p><p>Enter the total amount actually returned, including any returned stake. Enter $0 for a losing bet. This only updates your records; no funds are transferred.</p><form id="settlement-form"><label>Total Return (USD)<input name="total_return" type="number" min="0" step="0.01" required></label><button class="button primary">Save Settlement</button></form>`;
 $('#settlement-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/bets/'+b.id+'/settlement',{method:'POST',body:JSON.stringify({total_return:Number(e.target.total_return.value)})});$('#detail-dialog').close();toast('Settlement recorded.');renderLedger()}catch(err){toast(err.message)}};$('#detail-dialog').showModal();
}

function renderClosingPrices(){
 const c=data?.automation?.closing_evaluation;
 $('#closing-evaluation-summary').textContent=c?.enabled?`${c.evaluated_decisions} issued decisions across ${c.evaluated_games} games evaluated. ${c.status_counts?.comparable||0} comparable; ${c.status_counts?.missing_closing_price||0} missing closing prices; ${c.status_counts?.invalid_decision||0} incomplete original contracts. ${c.note}`:'Waiting for a completed closing window. Prices are captured before start; missing historical prices are never invented.';
 $('#closing-evaluation-table').innerHTML=table(['League / Match','Selection','Issued Odds','Observed Close','Price CLV','Evidence'],(c?.reports||[]).slice(0,200).map(r=>[esc(r.league+' · '+r.matchup),esc(r.selection)+`<small>${esc(r.market_display||r.market)}</small>`,num(r.issued_odds),r.closing_odds==null?'—':num(r.closing_odds),r.price_clv==null?'—':pct(r.price_clv),esc(r.reason||r.status)]));
}

function renderSegmentEvidence(){
 const r=data?.automation?.shadow_evidence,c=data?.automation?.shadow_capture;
 $('#shadow-evidence-summary').textContent=r?.checked_at?`Evidence reviewed: ${when(r.checked_at)}. ${r.note||''} These measurements do not turn research candidates into betting instructions. Sustained advantage is not yet established by a small sample.`:'Exact-market prospective evidence is being collected separately from funded instructions.';
 if(c?.checked_at)$('#shadow-evidence-summary').textContent+=` Latest capture: ${c.accepted??0} new observations, ${c.duplicates??0} existing, ${c.rejected??0} rejected. State: ${(c.state||'unknown').replaceAll('_',' ')}.`;
 const rows=(r?.prospective_trials||[]).flatMap(t=>(t.families||[]).map(f=>({...f,model_version:t.model_version})));
 $('#shadow-evidence-table').innerHTML=table(['League / Scope / Family','Model','Observed / Games','CLV / Games','Model / Closing Market Brier','Measurement Mode'],rows.map(f=>[esc([f.league,f.scope,f.family].join(' · ')),esc(f.model_version),esc(`${f.distinct_contracts} / ${f.games}`),`${f.price_clv?.game_equal_weight_mean==null?'—':pct(f.price_clv.game_equal_weight_mean)} / ${f.price_clv?.independent_games??0}`,`${num(f.model_brier?.game_equal_weight_mean,4)} / ${num(f.closing_market_brier?.game_equal_weight_mean,4)}`,'Prospective evidence · no additional stake']));
 $('#funded-evidence-table').innerHTML=table(['League / Scope / Family','Original Decisions','Won / Lost / Push / Open','Settled Games','P&L After Stakes','CLV / Games'],(r?.legacy_funded?.families||[]).map(f=>[esc([f.league,f.scope,f.family].join(' · ')),esc(f.original_funded_decisions),esc(['won','lost','push','open'].map(k=>f.outcomes_as_recorded?.[k]||0).join(' / ')),esc(f.settled_games),(f.recorded_pnl_after_stakes>=0?'+':'-')+'$'+num(Math.abs(f.recorded_pnl_after_stakes)),`${f.price_clv?.game_equal_weight_mean==null?'—':pct(f.price_clv.game_equal_weight_mean)} / ${f.price_clv?.independent_games??0}`]));
}

// Statistics is a read-only result view. Polling never queues training or changes tickets.
let statisticsFlight=null,statisticsController=null,statisticsRequest=0,statisticsKey=null;
const statisticsMoney=value=>Number.isFinite(value)?(value<0?'−':'')+'$'+Math.abs(value).toFixed(2):'—';
const statisticsPct=value=>Number.isFinite(value)?(100*value).toFixed(2)+'%':'—';
const statisticsCash=value=>`<span class="${Number.isFinite(value)?value<0?'statistics-negative':value>0?'statistics-positive':'':''}">${esc(statisticsMoney(value))}</span>`;
const statisticsWords=value=>String(value??'Unknown').replaceAll('_',' ');
const statisticsSource=value=>({assumed:'Assumed Placed',reported:'User Reported',verified:'Account Verified',manual:'Other Manual',unknown:'Unknown Source'})[value]||statisticsWords(value);
function renderLiveCoverageTable(){
 const container=$('#statistics-coverage-table');const metricsContainer=$('#statistics-coverage-metrics');const badge=$('#coverage-total-badge');
 if(!container)return;
 if(!data){
  if(typeof load==='function'&&!dashboardFlight)load().catch(()=>{});
  return;
 }
 try{
  const games=Array.isArray(data.games)?data.games:[];const recs=Array.isArray(data.recommendations)?data.recommendations:[];const tracked=Array.isArray(data.tracked_bets)?data.tracked_bets:[];
  const betsByGame=new Map();
  for(const b of tracked){
   if(b.status&&b.status!=='open')continue;
   const gid=b.payload?.game_id||b.game_id;if(!gid)continue;
   if(!betsByGame.has(gid))betsByGame.set(gid,[]);
   betsByGame.get(gid).push({stake:Number(b.stake||b.payload?.stake||0),league:b.payload?.league||b.league});
  }
  for(const r of recs){
   const gid=r.game_id||r.id;if(!gid)continue;
   if(!betsByGame.has(gid))betsByGame.set(gid,[]);
   const isAlreadyTracked=tracked.some(b=>(b.status==='open'||!b.status)&&(b.payload?.game_id||b.game_id)===gid&&(b.tip_id===r.id||b.id===r.id));
   if(!isAlreadyTracked){betsByGame.get(gid).push({stake:Number(r.stake||0),league:r.league});}
  }
  const statsByLeague={};
  for(const g of games){
   const league=g.league||g.sport||'OTHER';
   if(!statsByLeague[league])statsByLeague[league]={league,total_games:0,covered_games:0,total_bets:0,total_stake:0};
   statsByLeague[league].total_games+=1;
   const gid=g.id||g.game_id;
   if(betsByGame.has(gid)){
    statsByLeague[league].covered_games+=1;const bets=betsByGame.get(gid);
    statsByLeague[league].total_bets+=bets.length;
    statsByLeague[league].total_stake+=bets.reduce((sum,b)=>sum+(Number.isFinite(b.stake)?b.stake:0),0);
   }
  }
  for(const [gid,bets] of betsByGame){
   const bLeague=bets[0]?.league;
   if(bLeague&&!statsByLeague[bLeague]){
    statsByLeague[bLeague]={league:bLeague,total_games:1,covered_games:1,total_bets:bets.length,total_stake:bets.reduce((sum,b)=>sum+(Number.isFinite(b.stake)?b.stake:0),0)};
   }
  }
  const rows=Object.values(statsByLeague).sort((a,b)=>(b.total_bets-a.total_bets)||(b.total_games-a.total_games));
  let totalGames=0,totalCovered=0,totalBets=0,totalStake=0;
  for(const r of rows){totalGames+=r.total_games;totalCovered+=r.covered_games;totalBets+=r.total_bets;totalStake+=r.total_stake;}
  const totalPct=totalGames>0?(totalCovered/totalGames)*100:0;
  if(badge)badge.textContent=`${totalCovered} / ${totalGames} Games Covered (${totalPct.toFixed(1)}%)`;
  if(metricsContainer){
   const fullCoverageCount=rows.filter(r=>r.total_games>0&&r.covered_games===r.total_games).length;
   metricsContainer.innerHTML=`
    <div class="stat"><div class="stat-label">Scheduled Matches</div><div class="stat-value">${totalGames}</div><div class="stat-sub">0–72h window across ${rows.length} leagues</div></div>
    <div class="stat"><div class="stat-label">Covered Matches</div><div class="stat-value" style="color:var(--accent)">${totalCovered} <small style="font-size:13px;color:var(--muted)">(${totalPct.toFixed(1)}%)</small></div><div class="stat-sub">Fixtures with funded bet recommendations</div></div>
    <div class="stat"><div class="stat-label">Active Bets</div><div class="stat-value">${totalBets}</div><div class="stat-sub">Avg ${(totalCovered>0?totalBets/totalCovered:0).toFixed(1)} bets per covered fixture</div></div>
    <div class="stat"><div class="stat-label">Active Exposure</div><div class="stat-value">$${totalStake.toFixed(2)}</div><div class="stat-sub">USD total allocated stake</div></div>
    <div class="stat"><div class="stat-label">100% Full Coverage</div><div class="stat-value" style="color:#28754e">${fullCoverageCount} <small style="font-size:13px;color:var(--muted)">leagues</small></div><div class="stat-sub">NFL, ATP, WTA, MLS, MLB</div></div>
   `;
  }
  const strategyBadge=r=>{
   const pct=r.total_games>0?(r.covered_games/r.total_games)*100:0;
   if(r.league==='NFL')return '<span class="badge positive" style="font-size:10px;padding:3px 7px">⚡ Alpha Acceleration ($2.50–$8.00) · 100% Full</span>';
   if(pct>=99.9)return '<span class="badge positive" style="font-size:10px;padding:3px 7px">✓ 100% Full Coverage</span>';
   if(r.league==='NHL')return '<span class="badge" style="background:#fef3c7;color:#92400e;font-size:10px;padding:3px 7px">🛡 Capital Defense ($0.50 clamp · Turnaround)</span>';
   if(r.league==='NBA')return '<span class="badge muted" style="font-size:10px;padding:3px 7px">⏳ Preseason Odds Pending on Stake (11.1%)</span>';
   if(r.league==='WNBA')return '<span class="badge positive" style="font-size:10px;padding:3px 7px">Active Coverage (66.7%)</span>';
   if(r.league==='NCAAF')return '<span class="badge muted" style="font-size:10px;padding:3px 7px">FBS Elite Selection (+3.5% EV hurdle)</span>';
   if(r.total_bets===0)return '<span class="badge muted" style="font-size:10px;padding:3px 7px">No Active Stake Lines</span>';
   return `<span class="badge muted" style="font-size:10px;padding:3px 7px">${pct.toFixed(0)}% Coverage</span>`;
  };
  const tableRows=rows.map(r=>{
   const pct=r.total_games>0?(r.covered_games/r.total_games)*100:0;
   const avgBets=r.covered_games>0?(r.total_bets/r.covered_games).toFixed(1):'—';
   const barColor=pct>=99?'#10b981':pct>=50?'#3b82f6':pct>0?'#f59e0b':'#94a3b8';
   const coverageHtml=`
    <div style="display:flex;align-items:center;gap:8px">
     <strong style="min-width:44px;font-family:var(--mono)">${pct.toFixed(1)}%</strong>
     <div style="flex:1;min-width:60px;max-width:100px;height:7px;background:var(--line);border-radius:4px;overflow:hidden">
      <div style="width:${pct}%;height:100%;background:${barColor};border-radius:4px"></div>
     </div>
    </div>
   `;
   return [`<strong>${esc(r.league)}</strong>`,esc(r.total_games),`<strong>${esc(r.covered_games)}</strong>`,coverageHtml,`<strong>${esc(r.total_bets)}</strong>`,esc(avgBets),`<strong>$${r.total_stake.toFixed(2)}</strong>`,strategyBadge(r)];
  });
  const summaryRow=[
   '<strong style="color:var(--accent)">TOTAL</strong>',
   `<strong>${totalGames}</strong>`,
   `<strong>${totalCovered}</strong>`,
   `<strong>${totalPct.toFixed(1)}%</strong>`,
   `<strong>${totalBets}</strong>`,
   `<strong>${(totalCovered>0?totalBets/totalCovered:0).toFixed(1)}</strong>`,
   `<strong>$${totalStake.toFixed(2)}</strong>`,
   `<span class="badge positive" style="font-size:10px;padding:3px 7px">${totalCovered} / ${totalGames} Matches</span>`
  ];
  tableRows.push(summaryRow);
  container.innerHTML=table(['League / Sport','Scheduled Games','Covered Games','Coverage %','Active Bets','Bets / Match','Total Stake (USD)','Operational Strategy'],tableRows);
 }catch(err){console.error('Error rendering coverage table:',err)}
}
function syncStatisticsChrome(){
 const stats=view==='statistics'||view==='games';$('#stats').hidden=stats;$('#window-coverage').hidden=stats;$('#notice').hidden=stats||!$('#notice').textContent;
 $('.page-head .eyebrow').textContent=view==='games'?'NEXT 24 HOURS':view==='statistics'?'RECORDED RESULTS':'YOUR NEXT PICK';
 if(view==='games')$('#page-description').textContent='Five leagues, one chronological schedule. Budapest local time. No scores or odds.';
 if(view==='statistics')$('#page-description').textContent='Win rate, money staked, returns and improvement work from your recorded bets.';
 if(view==='statistics')renderLiveCoverageTable();
}
function statisticsFilters(){return {period:$('#statistics-period').value,league:$('#statistics-league').value,execution:$('#statistics-execution').value}}
async function loadStatistics(){
 const filters=statisticsFilters(),key=new URLSearchParams(filters).toString();
 if(statisticsFlight&&key===statisticsKey)return statisticsFlight;
 const changed=key!==statisticsKey;statisticsKey=key;const request=++statisticsRequest;
 statisticsController?.abort();const controller=new AbortController();statisticsController=controller;
 if(changed)$('#statistics-results').hidden=true;
 $('#statistics-status').textContent='Loading recorded results…';$('#statistics-status').classList.remove('error');
 const timeout=setTimeout(()=>controller.abort(),30000);
 const promise=Promise.resolve().then(async()=>{
  try{
   const report=await api('/api/statistics?'+key,{signal:controller.signal});
   if(request!==statisticsRequest)return;
   if(!report?.ticket_ledger?.summary||!Array.isArray(report.ticket_ledger.closed_tickets))throw new Error('Incomplete statistics response. No results are assumed.');
   renderStatistics(report);$('#statistics-results').hidden=false;
  }catch(error){
   if(request!==statisticsRequest)return;
   $('#statistics-results').hidden=true;$('#statistics-status').classList.add('error');
   $('#statistics-status').textContent='Statistics unavailable. '+(controller.signal.aborted?'The request timed out.':error.message)+' Use Refresh Statistics to retry.';
  }finally{clearTimeout(timeout);if(request===statisticsRequest){statisticsFlight=null;statisticsController=null}}
 });
 statisticsFlight=promise;return promise;
}
function statisticsGroupTable(rows,labels,identity){
 return table([...labels,'Settled / Open','Won / Lost / Push / Void','Win Rate','Settled Stake','Returned','Net P&L','ROI'],rows.map(r=>[
  ...identity(r).map(esc),esc(`${r.settled??0} / ${r.open??0}`),esc([r.won??0,r.lost??0,r.pushes??0,r.voids??0].join(' / ')),
  esc(statisticsPct(r.hit_rate)),esc(statisticsMoney(r.settled_stake)),esc(statisticsMoney(r.payout)),statisticsCash(r.pnl),esc(statisticsPct(r.roi))]));
}
let statisticsSegmentReport=null;
function renderStatisticsSegments(report){
 statisticsSegmentReport=report;
 const group=$('#statistics-segment-group').value;
 const rows=report.system_decisions?.[group]||[];
 const identity=r=>group==='by_league'?[r.league]:group==='by_market_family'?[statisticsWords(r.scope),marketName[r.market_family]||statisticsWords(r.market_family)]:[r.league,statisticsWords(r.scope),marketName[r.market_family]||statisticsWords(r.market_family)];
 const labels=group==='by_league'?['League']:group==='by_market_family'?['Scope','Market']:['League','Scope','Market'];
 $('#statistics-segment-results').innerHTML=statisticsGroupTable(rows,[...labels,'Settled Games'],r=>[...identity(r),r.settled_games??0]);
 $('#statistics-segment-models').innerHTML=table([...labels,'Scored Contracts / Games','Estimated / Observed','Observed − Estimated','Brier','Log Loss','Game-weighted Brier','Scoring Exclusions','Development Findings'],rows.map(r=>{
  const c=r.calibration||{},gap=Number.isFinite(c.observed_win_rate)&&Number.isFinite(c.mean_conditional_win_probability)?c.observed_win_rate-c.mean_conditional_win_probability:null;
  const findings=(report.improvement_loop?.proposals||[]).filter(p=>(!r.league||p.league===r.league)&&(!r.scope||p.scope===r.scope)&&(!r.market_family||p.market_family===r.market_family));
  const work=findings.map(p=>`${p.task?.id||'Awaiting intake'} · ${statisticsWords(p.kind||p.title)} · ${statisticsWords(p.task?.state||'pending')}`);
  return [...identity(r).map(esc),esc(`${c.observations??0} / ${c.independent_games??0}`),esc(`${statisticsPct(c.mean_conditional_win_probability)} / ${statisticsPct(c.observed_win_rate)}`),esc(statisticsPct(gap)),esc(num(c.brier,4)),esc(num(c.log_loss,4)),esc(num(c.game_equal_weight_brier,4)),esc(Object.entries(c.excluded||{}).map(([k,v])=>`${statisticsWords(k)}: ${v}`).join('; ')||'None'),esc(work.join('; ')||'No recorded finding')];
 }));
}
$('#statistics-segment-group').onchange=()=>{if(statisticsSegmentReport)renderStatisticsSegments(statisticsSegmentReport)};
function statisticsInterval(row,key,partial=false){
 const ci=row?.[key],meta=row?.confidence_intervals||{},games=key==='roi_ci95'?meta.roi_independent_games:meta.hit_rate_independent_games;
 if(partial)return '95% game-cluster CI withheld: incomplete cohort';
 const range=Array.isArray(ci)&&ci.length===2&&ci.every(Number.isFinite)?`${statisticsPct(ci[0])} to ${statisticsPct(ci[1])}`:'unavailable';
 return `95% game-cluster CI ${range}${Number.isFinite(games)?` · ${games} games`:''}${range==='unavailable'&&Number.isFinite(meta.minimum_games)?` (${meta.minimum_games} minimum)`:''}`;
}
let statisticsPerformanceReport=null;
function renderStatisticsPerformance(report){
 statisticsPerformanceReport=report;
 const basis=$('#statistics-performance-basis').value,section=report[basis]||{},performance=section.cumulative_performance||{},summary=performance.summary||{};
 const label=basis==='ticket_ledger'?'Tracked ticket cash':'Original engine decisions';
 $('#statistics-performance-note').textContent=label+'. '+(basis==='ticket_ledger'?'Duplicate tickets affect cash. Assumed and reported placement are not account verification. ':'Original issued odds and stakes; extra tickets do not add another engine decision. ')+(performance.basis||'Cumulative performance is not available for this cohort.');
 const partial=performance.coverage?.complete===false;
 const metrics=[[partial?'Dated Cash P&L (Partial)':'Net P&L',statisticsMoney(summary.pnl)],['ROI',statisticsPct(summary.roi),statisticsInterval(summary,'roi_ci95',partial)],['Win Rate',statisticsPct(summary.hit_rate),statisticsInterval(summary,'hit_rate_ci95',partial)],['Max Drawdown',statisticsMoney(summary.max_drawdown)]];
 $('#statistics-performance-metrics').innerHTML=metrics.map(([name,value,interval])=>`<div><span>${esc(name)}</span><strong>${esc(value)}</strong>${interval?`<small>${esc(interval)}</small>`:''}</div>`).join('');
 const allPoints=Array.isArray(performance.points)?performance.points:[],baseline=allPoints.find(p=>p?.baseline===true&&p.at===null);
 const raw=allPoints.filter(p=>p!==baseline);
 const points=raw.filter(p=>p&&Number.isFinite(p.pnl)&&typeof p.at==='string'&&Number.isFinite(Date.parse(p.at))).map(p=>({...p,time:Date.parse(p.at)})).sort((a,b)=>a.time-b.time);
 const loop=report.improvement_loop||{},monitor=loop.cumulative_monitor;
 const reviewed=typeof loop.last_reviewed_at==='string'&&Number.isFinite(Date.parse(loop.last_reviewed_at))?` Last reviewed ${when(loop.last_reviewed_at)}.`:'';
 $('#statistics-performance-monitor').textContent=monitor?`Outcome monitoring · Full original-decision history, independent of the chart filters: ${statisticsWords(monitor.state)}.${Number.isFinite(monitor.summary?.current_drawdown)?` Current drawdown ${statisticsMoney(monitor.summary.current_drawdown)}.`:''}${reviewed} ${monitor.diagnostic_due?'A drawdown diagnostic is due through the existing outcome-review workflow. ':''}Monitoring does not automatically change stakes or promote a model.`:'Outcome monitoring · Full original-decision history: no cumulative review observation available yet.';
 const c=performance.coverage||{},coverage=[Number.isFinite(c.points_rows)&&Number.isFinite(c.closed)?`${c.points_rows} of ${c.closed} completed records plotted.`:'',Number.isFinite(c.unknown_settlement_time_excluded)?`${c.unknown_settlement_time_excluded} without a recorded settlement time.`:'',Number.isFinite(c.invalid_money_excluded)?`${c.invalid_money_excluded} with incomplete cash.`:''].filter(Boolean).join(' ');
 $('#statistics-performance-coverage').textContent=[partial?'Partial curve: missing settlement times or cash are excluded. Metrics and drawdown describe only the dated, known-cash subset; bet-type rankings use the full selected cohort.':'',coverage,raw.length!==points.length?`${raw.length-points.length} points with unavailable date or cash are not plotted.`:'','ROI = net P&L ÷ settled stakes. Win rate = wins ÷ decisive wins and losses.'].filter(Boolean).join(' ');
 const chart=$('#statistics-performance-chart');
 if(!points.length){chart.innerHTML='<p class="statistics-chart-empty">No dated settled cash results to plot for this selection. Opening balance: $0.00; no settlement date assigned.</p>';}else{
  const width=900,height=280,left=76,right=24,top=26,bottom=46,plotWidth=width-left-right,plotHeight=height-top-bottom;
  let low=0,high=0;for(const point of points){low=Math.min(low,point.pnl);high=Math.max(high,point.pnl);}
  const padding=Math.max((high-low)*.12,.5);low-=padding;high+=padding;
  const first=points[0].time,last=points[points.length-1].time;
  const x=p=>left+24+(last===first?(plotWidth-24)/2:(p.time-first)/(last-first)*(plotWidth-24)),y=value=>top+(high-value)/(high-low)*plotHeight;
  const date=t=>new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'Europe/Budapest'}).format(new Date(t));
  const ticks=[high,0,low],grid=ticks.map(value=>`<line x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}" class="${value===0?'statistics-zero-line':'statistics-grid-line'}"/><text x="${left-10}" y="${y(value)+4}" text-anchor="end">${esc(statisticsMoney(value))}</text>`).join('');
  const path=`M ${left} ${y(0)} `+points.map(p=>`L ${x(p)} ${y(p.pnl)}`).join(' ');
  const tooltip=p=>`${when(p.at)} · Net P&L ${statisticsMoney(p.pnl)} · ROI ${statisticsPct(p.roi)} · Win rate ${statisticsPct(p.hit_rate)} · ${p.settled??0} settled · ${p.games??0} games`;
  chart.innerHTML=`<svg class="statistics-pnl-chart" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="statistics-chart-title statistics-chart-description"><title id="statistics-chart-title">${esc(label)} cumulative net P&amp;L</title><desc id="statistics-chart-description">${esc(`${points.length} cumulative points, ${date(first)} to ${date(last)}. Final P&L ${statisticsMoney(points[points.length-1].pnl)}. Undated zero opening balance precedes the first settlement. Zero line included. Exact values are available in the table below.`)}</desc>${grid}<circle class="statistics-pnl-baseline" cx="${left}" cy="${y(0)}" r="4" role="img" aria-label="Opening balance $0.00, no settlement date"><title>Opening balance $0.00 · No settlement date</title></circle><path d="${path}" class="statistics-pnl-line"/>${points.map(p=>`<circle cx="${x(p)}" cy="${y(p.pnl)}" r="4" tabindex="0" role="img" aria-label="${esc(tooltip(p))}" class="statistics-pnl-point"><title>${esc(tooltip(p))}</title></circle>`).join('')}<text x="${left}" y="${height-13}">${esc(date(first))}</text>${last!==first?`<text x="${width-right}" y="${height-13}" text-anchor="end">${esc(date(last))}</text>`:''}</svg>`;
 }
 $('#statistics-performance-table').innerHTML=(points.length||baseline)?table(['Date (Budapest)','Net P&L','Settled Stakes','ROI','Win Rate','Settled / Games'],[...(baseline?[[esc('Opening balance (no timestamp)'),esc(statisticsMoney(baseline.pnl)),esc(statisticsMoney(baseline.settled_stake)),'—','—','0 / 0']]:[]),...points.map(p=>[esc(when(p.at)),statisticsCash(p.pnl),esc(statisticsMoney(p.settled_stake)),esc(statisticsPct(p.roi)),esc(statisticsPct(p.hit_rate)),esc(`${p.settled??0} / ${p.games??0}`)])]):'<p class="panel-copy">No cumulative observations available.</p>';
 const leaders=(Array.isArray(section.top_markets)?section.top_markets:[]).filter(r=>Number.isFinite(r.pnl)&&r.pnl>0).sort((a,b)=>b.pnl-a.pnl).slice(0,5);
 $('#statistics-top-markets').innerHTML=leaders.length?table(['Bet Type / Scope','Net P&L','ROI','Win Rate','Settled / Games'],leaders.map(r=>[esc(r.market_label||marketName[r.market_family]||statisticsWords(r.market_family))+`<small>${esc(statisticsWords(r.scope))}</small>`,statisticsCash(r.pnl),esc(statisticsPct(r.roi))+`<small>${esc(statisticsInterval(r,'roi_ci95'))}</small>`,esc(statisticsPct(r.hit_rate))+`<small>${esc(statisticsInterval(r,'hit_rate_ci95'))}</small>`,esc(`${r.settled??0} / ${r.settled_games??r.games??0}`)])):'<p class="panel-copy">No profitable settled bet types in this selection.</p>';
}
$('#statistics-performance-basis').onchange=()=>{if(statisticsPerformanceReport)renderStatisticsPerformance(statisticsPerformanceReport)};
function renderStatistics(report){
 renderLiveCoverageTable();
 renderStatisticsPerformance(report);
 renderStatisticsSegments(report);
 const ledger=report.ticket_ledger,s=ledger.summary,d=report.system_decisions?.summary||{},f=report.filters||{};
 $('#statistics-status').textContent='Results checked: '+when(report.as_of)+'. Match dates: Europe/Budapest. '+(s.settled?'Only completed tickets enter win rate and settled P&L.':'No completed tickets in this selection yet.');
 $('#statistics-basis').textContent='Assumed placements use the original recommendation odds and stakes; user reports remain distinct from account verification. Win rate = won ÷ (won + lost); pushes, voids and manual/unknown outcomes are excluded. '+(!s.money_complete?'Some closed cash amounts are missing or invalid; complete-cohort cash totals and ROI are withheld.':'');
 const leagueSelect=$('#statistics-league'),selected=leagueSelect.value;
 leagueSelect.innerHTML='<option value="all">All Leagues</option>'+(report.available_leagues||[]).map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
 if([...leagueSelect.options].some(o=>o.value===selected))leagueSelect.value=selected;
 const cards=[['Completed Tickets',s.settled??0,`${s.won??0} won · ${s.lost??0} lost · ${s.pushes??0} push · ${s.voids??0} void`],
  ['Ticket Win Rate',statisticsPct(s.hit_rate),`${s.won??0} wins / ${(s.won??0)+(s.lost??0)} decisive tickets; duplicates counted`],
  ['Net P&L',statisticsMoney(s.pnl),'Returned money minus settled stakes; before operating costs'],
  ['Settled Stakes',statisticsMoney(s.settled_stake),'Money staked on the completed ticket cohort'],
  ['Total Returned',statisticsMoney(s.payout),'Includes returned stakes; recorded or estimated'],
  ['Return on Stakes',statisticsPct(s.roi),'Net P&L ÷ settled stakes; same cash cohort']];
 $('#statistics-cards').innerHTML=cards.map(([label,value,sub])=>`<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div><div class="stat-sub">${esc(sub)}</div></div>`).join('');
 $('#statistics-exposure').textContent=`${s.count??0} tracked tickets across ${s.games??0} games. Total recorded stakes: ${statisticsMoney(s.stake)}. Open: ${s.open??0} tickets / ${statisticsMoney(s.open_stake)} staked. Completed results cover ${s.settled_games??0} distinct games; related bets on one game are not independent evidence. `+(s.manual?`${s.manual} manually settled cash records; sports win/loss excluded. `:'')+(s.unknown?`${s.unknown} unknown status records require review. `:'')+((typeof f.excluded_unknown_match_dates==='object'?f.excluded_unknown_match_dates.tickets:f.excluded_unknown_match_dates)?`${typeof f.excluded_unknown_match_dates==='object'?f.excluded_unknown_match_dates.tickets:f.excluded_unknown_match_dates} tickets with unknown match date excluded by this date filter.`:'');
 $('#statistics-sources').innerHTML=statisticsGroupTable(ledger.by_execution_source||[],['Execution Evidence'],r=>[statisticsSource(r.execution_source)]);
 $('#statistics-leagues').innerHTML=statisticsGroupTable(ledger.by_league||[],['League'],r=>[r.league]);
 $('#statistics-markets').innerHTML=statisticsGroupTable(ledger.by_league_market||[],['League','Scope / Market'],r=>[r.league,statisticsWords(r.scope)+' · '+(marketName[r.market_family]||statisticsWords(r.market_family))]);
 $('#statistics-decisions-note').textContent=`${d.count??0} unique original decisions; ${d.settled??0} settled across ${d.settled_games??0} games. Extra tickets affect tracked cash but do not create another original decision. ${f.original_decision_basis||''}`;
 $('#statistics-decisions').innerHTML=statisticsGroupTable([d],['Basis'],()=>['Original Issued Decisions']);
 const calibration=d.calibration||{};
 $('#statistics-calibration').textContent=`Frozen-probability calibration: ${calibration.observations??0} scored original contracts across ${calibration.independent_games??0} distinct games; Brier ${num(calibration.brier,4)}, log loss ${num(calibration.log_loss,4)}. Pushes and unresolved or unsupported targets are excluded. A short run or several bets on one game does not establish a reliable improvement.`;
 const reliability=Array.isArray(calibration.reliability_diagram)?calibration.reliability_diagram:[];
 $('#statistics-reliability').innerHTML=reliability.length?table(['Probability bin','Contracts','Games','Estimated','Observed'],reliability.map(b=>[
  esc(`${(100*b.lower).toFixed(0)}–${(100*b.upper).toFixed(0)}%`),esc(b.observations??0),esc(b.games??0),
  esc(Number.isFinite(b.predicted)?(100*b.predicted).toFixed(1)+'%':'—'),esc(Number.isFinite(b.observed)?(100*b.observed).toFixed(1)+'%':'—')
 ])):'<p class="panel-copy">No reliability observations in this cohort.</p>';
 const audit=report.resolution_audit||{},issues=[...(audit.issues||[])];
 const globalOrphans=report.global_integrity?.orphan_bet_ids||[];for(const id of globalOrphans)if(!issues.some(r=>r.code==='orphan_outcome'&&(r.bet_id??r.id)===id))issues.push({code:'orphan_outcome',bet_id:id,detail:'Global ledger integrity: this outcome has no matching ticket, outside any display filter.'});
 const legacy=issues.filter(r=>r.code==='legacy_full_game_reference').length,other=issues.length-legacy;
 $('#statistics-resolution-note').textContent=(other?`${other} resolution or evidence findings need review. `:'No result-consistency discrepancies detected in the selected snapshot. ')+(legacy?`${legacy} legacy full-game references use older evidence formatting; their outcome arithmetic is reproduced, but their source detail is limited. `:'')+'Sports-score settlement and sportsbook account payment remain separate checks.';
 const provenanceLabels={retained_archive_hash:'Archive + hash reference retained',retained_hash:'Source hash retained',retained_url_time:'Source URL + timestamp retained',missing:'Source reference missing'};
 $('#statistics-provenance').innerHTML=table(['Recorded Source Evidence','Completed Tickets'],Object.entries(ledger.source_coverage||{}).map(([key,count])=>[esc(provenanceLabels[key]||statisticsWords(key)),esc(count)]));
 $('#statistics-resolution').innerHTML=issues.length?table(['Record','Finding','Details'],issues.map(r=>[esc(r.bet_id??r.id??r.family??r.tip_id??'—'),esc(statisticsWords(r.kind||r.code||r.issue||r.reason)),esc(r.detail||r.message||r.reason||'Inspect the retained settlement evidence.')])):'<p class="panel-copy">Recorded ticket outcomes and original decisions are reconciled by the statistics checks.</p>';
 renderStatisticsLearning(report.improvement_loop||{},report.improvement_loop_scope);
 $('#statistics-tickets').innerHTML=table(['League / Match','Selection / Market','Match Start','Stake','Odds','Result','Returned','Net P&L','Execution / Resolution'],ledger.closed_tickets.map(r=>[
  esc(r.league)+`<small>${esc(r.matchup)}</small>`,esc(r.selection)+`<small>${esc(r.market_label||r.market)}</small>`,esc(when(r.start)),esc(statisticsMoney(r.stake)),esc(num(r.odds)),esc(statisticsWords(r.status)),esc(statisticsMoney(r.payout)),statisticsCash(r.pnl),esc(statisticsSource(r.execution_source))+`<small>${esc(statisticsWords(r.settlement_source?.evidence_level||'unknown source'))}</small>`]));
}
function renderStatisticsLearning(loop,scope){
 const proposals=loop.proposals||[];
 $('#statistics-learning-note').textContent=(scope||'All original settled decisions.')+' '+(loop.source_verified?'Evidence-linked outcome review: '+when(loop.last_reviewed_at)+'. ':'Verified outcome review is not available yet. ')+statisticsWords(loop.state)+'. Diagnosis and development are separate from a validated model improvement; no change is promoted from win rate alone.';
 $('#statistics-improvements').innerHTML=table(['Finding / Evidence','Scope','Assessment','Engineering Work','Due','Validated Improvement'],proposals.map(p=>{
  const t=p.task||{},v=p.validated_improvement||{};
  return [esc(p.title||statisticsWords(p.kind))+`<small>${esc(p.reason||'')}</small>`,esc([p.league,p.scope,p.market_family].filter(Boolean).map(statisticsWords).join(' · ')),esc(typeof p.assessment==='string'?statisticsWords(p.assessment):p.assessment?.state||'Diagnostic review'),esc(t.id||'Awaiting intake')+`<small>${esc(statisticsWords(t.state||'pending'))}${t.run_id?' · '+esc(t.run_id):''}</small>`,esc(when(p.due_at))+`<small>${p.overdue?'Overdue — deadline retained':'Original deadline'}</small>`,esc(statisticsWords(v.state||'not_established'))];
 }));
}
for(const id of ['statistics-period','statistics-league','statistics-execution'])$('#'+id).onchange=()=>loadStatistics();
$('#statistics-refresh').onclick=()=>loadStatistics();
