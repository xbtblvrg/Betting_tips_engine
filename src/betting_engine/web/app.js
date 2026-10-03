'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let data=null,horizon='48',view='tips',selectedTip=null,selectedInstructionSignature=null,dashboardAvailable=false;
let dashboardRequest=0,appliedDashboardRequest=0,placementEpoch=0;
let dashboardFlight=null,dashboardQueued=null;
const pendingPlacements=new Map();
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=v=>Number.isFinite(v)?(v*100).toFixed(1)+'%':'—';
const num=(v,n=2)=>Number.isFinite(v)?v.toFixed(n):'—';
const minOdds=v=>Number.isFinite(v)?(Math.ceil((v-Number.EPSILON)*100)/100).toFixed(2):'—';
const when=v=>v?new Intl.DateTimeFormat('en-GB',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Budapest'}).format(new Date(v)):'—';
const localDay=d=>new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Europe/Budapest'}).format(d);
const title={research:'Research and Coverage',tips:'Betting Picks',games:'Schedule',models:'Model Performance',ledger:'Bet Tracker',sizing:'Stake Sizing',statistics:'Statistics',settings:'Settings'};
const marketName={three_way:'1x2',moneyline:'Winner',spreads:'Handicap',totals:'Total',home_total:'Home Team Total',away_total:'Away Team Total'};
function stakeNavigation(t){
 // Old recorded bets keep their original payload. Exact-selection navigation
 // may come from a fresh assessment without rewriting those records.
 const sameContract=c=>c.id===t.id&&['league','game_id','market','side','line','model_scope'].every(k=>c[k]===t[k]);
 const current=currentTips(t.current_assessment?[t.current_assessment]:[]).find(sameContract)||allEvaluated().find(sameContract);
 // A current unknown route is authoritative too: never resurrect an old tab.
 // Legacy partial metadata without an event_url cannot provide a complete
 // route. Keep its recorded link and route together; explicit null is known.
 return current&&Object.hasOwn(current,'stake_market')&&(current.stake_market===null||Object.hasOwn(current.stake_market,'event_url'))?(current.stake_market||{}):(t.stake_market||{});
}
function betInstructions(t){
 const kind=t.market.split(':').at(-1),scope=t.model_scope||'full_game';
 const market=t.market_display||marketName[t.market],mapping=t.stake_market||{},navigation=stakeNavigation(t),terms=view==='tips'&&pendingInstruction(t)?(t.current_assessment||t):t;
 const units=t.league==='MLB'?'runs':t.league==='NHL'?'goals':'points';
 let outcome='The selected team must win, including the overtime or extra-innings scope shown in the market name.';
 if(kind==='spreads'&&Number.isFinite(t.line))outcome=t.line<0?`The selected team must win by more than ${Math.abs(t.line)} ${units}.`:t.line>0?`The selected team must win or lose by fewer than ${t.line} ${units}.`:'The selected team must win. A tied handicap result is void.';
 if(['home_total','away_total'].includes(kind))outcome=`Only ${kind==='home_total'?t.home:t.away} must score ${t.side==='over'?'over':'under'} ${t.line} ${units}. Use that team's Total market, with the exact overtime or extra-innings scope.`;
 if(kind==='totals')outcome=`The combined final score of both teams must be ${t.side==='over'?'over':'under'} ${t.line} ${units}. Select the match Total, not a team Total.`;
 if(scope!=='full_game'){
  const labels={inning_1:'the 1st Inning',innings_1_5:'Innings 1 to 5',quarter_1:'the 1st Quarter',quarter_2:'the 2nd Quarter',quarter_3:'the 3rd Quarter',quarter_4:'the 4th Quarter',quarter_4_ot:'the 4th Quarter including overtime',half_1:'the 1st Half',half_2:'the 2nd Half',half_2_ot:'the 2nd Half including overtime',period_1:'the 1st Period',period_2:'the 2nd Period',period_3:'the 3rd Period',regulation:'Regulation Time'};
  const label=labels[scope]||t.period||scope;
  outcome=kind==='three_way'?`Only ${label} count. Select ${t.selection}; a tied score wins only the Draw selection.`:kind==='moneyline'?`The selected team must lead after ${label}; a tie is a push.`:kind.endsWith('_total')?`Only ${kind==='home_total'?t.home:t.away} ${units} in ${label} count toward ${t.selection}.`:`Only ${units} in ${label} count toward ${t.selection}. Ignore scores outside this scope.`;
  if(kind==='odd_even')outcome=`The combined ${units} in ${label} must be ${t.selection}. Zero counts as Even.`;
  if(kind==='both_score')outcome=`In ${label}, ${t.side==='yes'?'both teams must score at least once':'at least one team must score zero'}.`;
  if(kind==='double_chance')outcome=`At the end of ${label}, either outcome in ${t.selection} wins. Use this exact Double Chance selection.`;
 }
 return `<h3>Find This Bet on Stake</h3><ol><li>Open ${esc(t.league)} and find <strong>${esc(t.away)} @ ${esc(t.home)}</strong>. Match both team names and the start time; team order may differ.</li><li>${navigation.tab?`Select the <strong>${esc(navigation.tab)}</strong> tab above the markets, then find`:'Find'} the market named <strong>${esc(market)}</strong> and select <strong>${esc(t.selection)}</strong>.${Number.isFinite(t.line)?' If multiple lines are shown, choose the exact number and sign above.':''}</li><li>Use <strong>Decimal</strong> odds and check for <strong>${displayedMinimumOdds(t)} or higher</strong>${isModelPick(t)?' (rounded up strictly above model break-even)':''}. The evaluated Stake odds were ${num(terms.reference_odds)}.</li><li>Add the selection as a <strong>Single</strong> bet. ${view==='tips'&&pendingInstruction(t)?`Bet <strong>$${num(t.tracked_bet.stake)} USD</strong> once on this exact selection. If already placed, mark it as placed instead of repeating it.`:t.tracked_bet?`Already tracked: <strong>$${num(t.tracked_bet.stake)}</strong> at <strong>${num(t.tracked_bet.odds)}</strong>. No additional bet is requested.`:t.stake>0?`Suggested stake: <strong>$${num(t.stake)} USD</strong>, subject to the checked odds and remaining limit.`:'Watchlist only: no stake is currently allocated. Check the current odds before assigning a stake.'}</li></ol><p>${esc(outcome)}</p><p class="panel-copy">${esc(mapping.scope_note||'Use the exact market and settlement rules shown above.')} Pregame picks do not apply to in-play markets.</p><p class="panel-copy">${esc(mapping.availability_note||'This selection was not verified against the current Stake feed.')} If that line is unavailable, do not substitute a different line.</p>${mapping.league_url?`<a class="button ghost" href="${esc(mapping.league_url)}" target="_blank" rel="noopener noreferrer">Open ${esc(t.league)} on Stake ↗</a>`:''} <a href="https://stake.com/policies/sportsbook" target="_blank" rel="noopener noreferrer">Stake Market Rules</a>`;
}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,4200)}
async function api(path,options={}){const r=await fetch(path,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Unable to save. Check the fields and try again.');return d}
function currentTips(rows){return rows.filter(t=>!t.expired&&!t.withdrawn_at&&new Date(t.valid_until)>new Date()&&new Date(t.start)>new Date()&&Date.now()-new Date(t.quote_at)<=300000&&Date.now()-new Date(t.quote_at)>=-5000)}
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
function visibleTips(rows=liveTips()){
 rows=rows.filter(t=>new Date(t.start)>new Date());if(['24','48'].includes(horizon))rows=rows.filter(t=>new Date(t.start)>new Date()&&new Date(t.start)<=new Date(Date.now()+Number(horizon)*3600000));
 const today=localDay(new Date()),tomorrow=localDay(new Date(Date.now()+86400000));
 if(horizon==='today')rows=rows.filter(t=>localDay(new Date(t.start))===today);
 if(horizon==='tomorrow')rows=rows.filter(t=>localDay(new Date(t.start))===tomorrow);
 const league=$('#league-filter').value;if(league!=='all')rows=rows.filter(t=>t.league===league);
 const market=$('#market-filter')?.value||'all';
 if(market!=='all')rows=rows.filter(t=>marketMatches(t.market,market));
 const sort=$('#sort-filter').value;
 return rows.sort((a,b)=>{
  const completed=Number(isPlaced(a))-Number(isPlaced(b));if(completed)return completed;
  const currentA=executionTerms(a),currentB=executionTerms(b);
  if(sort==='probability'&&currentA.probability!==currentB.probability)return currentB.probability-currentA.probability;
  if(sort==='rank'&&currentA.rank!==currentB.rank&&currentA.rank!=null&&currentB.rank!=null)return currentA.rank-currentB.rank;
  const startOrder=new Date(a.start)-new Date(b.start);if(startOrder)return startOrder;
  const familyOrder=marketPriority(a)-marketPriority(b);if(familyOrder)return familyOrder;
  return String(a.id).localeCompare(String(b.id));
 });
}
function marketMatches(value,filter){
 if(filter==='all')return true;
 const kind=String(value||'').split(':').at(-1);
 return filter==='other'?!['moneyline','spreads','totals'].includes(kind):kind===filter;
}
function issuedRowsVisible(rows){
 const now=Date.now(),today=localDay(new Date()),tomorrow=localDay(new Date(now+86400000));
 const league=$('#league-filter').value,market=$('#market-filter')?.value||'all';
 return (rows||[]).filter(r=>{
  if(league!=='all'&&r.league!==league)return false;
  if(!marketMatches(r.market,market))return false;
  const stamp=new Date(r.start).getTime();if(!Number.isFinite(stamp)||stamp<=now)return false;
  if(['24','48'].includes(horizon)&&stamp>now+Number(horizon)*3600000)return false;
  if(horizon==='today'&&localDay(new Date(stamp))!==today)return false;
  if(horizon==='tomorrow'&&localDay(new Date(stamp))!==tomorrow)return false;
  return true;
 });
}
function placementControl(t){return `<label class="placement-toggle"><input type="checkbox" data-placement="${esc(t.id)}" ${isPlaced(t)?'checked':''} ${pendingPlacements.has(t.id)?'disabled':''} aria-label="Bet placed: ${esc(t.selection)} — ${esc(t.away)} at ${esc(t.home)}"><span>${pendingPlacements.has(t.id)?'Saving…':isPlaced(t)?'Bet placed':'Mark as placed'}</span></label>`}
function bindPlacementControls(){
 $$('[data-placement]').forEach(input=>input.onchange=async()=>{
  const id=input.dataset.placement;const placed=input.checked;
  if(pendingPlacements.has(id))return;
  pendingPlacements.set(id,placed);placementEpoch++;renderTips();
  try{
   const result=await api('/api/tips/'+encodeURIComponent(id)+'/checklist',{method:'PUT',body:JSON.stringify({placed})});
   data.placement_marks??={};
   if(result.placed)data.placement_marks[id]=result.placed_at;else delete data.placement_marks[id];
   pendingPlacements.delete(id);placementEpoch++;renderTips();
   toast(result.placed?'Marked as placed. Saved in Bet Tracker.':'Mark removed. Current qualifying picks return to Betting Picks.');
  }catch(e){pendingPlacements.delete(id);placementEpoch++;renderTips();toast(e.message)}
 });
}
function trackingLabel(t){return t.tracking_source==='auto_assumed_placed'?'Auto-tracked · Assumed placed':t.tracking_source==='user_reported_placed'?'User-reported Stake bet':'Manually logged'}
function modelPickAllocationPolicy(evidence){return evidence?.model_pick_allocation_policy===undefined?'one_per_game_v1':evidence.model_pick_allocation_policy}
function executionReview(t){
 const c=t.current_assessment||t,r=t.tracked_bet?t.instruction_review:t.execution_review;
 // The API verifies the sealed decision and current sizing. A browser fallback
 // must never turn missing or rejected server evidence into a betting instruction.
 if(!dashboardAvailable||!r?.actionable||!c.execution_review?.actionable||!currentTips([c]).length)return {actionable:false};
 const age=Date.now()-new Date(r.quote_at);
 if(!Number.isFinite(age)||age < -5000||age>300000||!(new Date(r.valid_until)>new Date()))return {actionable:false};
 if(c.reference_platform!=='Stake.com'||c.stake_market?.availability_verified!==true||c.decision_guard?.passed!==true)return {actionable:false};
 if(!Number.isFinite(c.probability)||c.probability<=0||c.probability>=1||!Number.isFinite(c.reference_odds)||c.reference_odds<=1)return {actionable:false};
 const policy=data?.settings?.recommendation_policy??'strict',basis=c.funding_basis,currentReview=c.execution_review;
 const outcomePolicy=data?.settings?.outcome_sizing_policy??'legacy';
 if(!['legacy','historical_game_weighted_v1','historical_game_weighted_v2'].includes(outcomePolicy)||![c,c.decision_guard,r,currentReview].every(v=>(v?.outcome_sizing_policy??'legacy')===outcomePolicy))return {actionable:false};
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
  !Number.isFinite(basis.maximum_stake)||basis.maximum_stake<1||basis.maximum_stake>5||instructionStake(t)>basis.maximum_stake))return {actionable:false};
 const close=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=1e-9*Math.max(1,Math.abs(a),Math.abs(b));
 if(!['strict','continuous_model_v1','continuous_best_available_v1'].includes(policy))return {actionable:false};
 if(['continuous_model_v1','continuous_best_available_v1'].includes(policy)){
  if(!basis||basis.policy!==policy||basis.eligible!==true||!['stress_value','model_pick','probability_pick'].includes(basis.tier)||
   ![r,currentReview].every(v=>v.actionable===true&&v.state==='current'&&v.recommendation_policy===policy&&v.funding_tier===basis.tier&&close(v.minimum_odds,basis.minimum_odds))||
   !Number.isFinite(basis.maximum_stake)||basis.maximum_stake<1||basis.maximum_stake>5||instructionStake(t)>basis.maximum_stake)return {actionable:false};
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
function effectiveMinimumOdds(t){const r=executionReview(t),current=view==='tips'&&pendingInstruction(t)?t.current_assessment:t;return (view==='tips'||!t.recorded_snapshot)&&r.actionable&&['model_pick','probability_pick'].includes(r.funding_tier)?r.minimum_odds:current.minimum_odds}
function displayedMinimumOdds(t){
 const threshold=effectiveMinimumOdds(t);
 if(!isModelPick(t))return minOdds(threshold);
 const c=t.current_assessment||t;
 // Inclusive UI prices use the next cent strictly above nominal break-even
 // and its EV-zero tolerance; never tell a user that exact break-even qualifies.
 const cents=Math.floor(Math.max(threshold,(1-c.push_probability+1e-9)/c.probability)*100)+1;
 return Number.isFinite(cents)?(cents/100).toFixed(2):'—';
}
function isModelPick(t){const r=executionReview(t);return (view==='tips'||!t.recorded_snapshot)&&r.actionable===true&&r.funding_tier==='model_pick'}
function isProbabilityPick(t){const r=executionReview(t);return (view==='tips'||!t.recorded_snapshot)&&r.actionable===true&&r.funding_tier==='probability_pick'}
function allTiersWeighted(t){return executionReview(t).actionable===true&&executionReview(t).model_pick_sizing_policy==='all_tiers_weighted_v3'}
function weightedModelPick(t){return isModelPick(t)&&['model_weighted_v2','all_tiers_weighted_v3'].includes(executionReview(t).model_pick_sizing_policy)}
function unitPickLabel(t){return isProbabilityPick(t)?(allTiersWeighted(t)?'Probability Pick · $'+num(instructionStake(t)):'Probability Pick · $1'):isModelPick(t)?(weightedModelPick(t)?'Model Pick · $'+num(instructionStake(t)):'Model Pick · $1'):null}
function instructionTierLabel(t){return unitPickLabel(t)||(allTiersWeighted(t)&&executionReview(t).funding_tier==='stress_value'?'Stress Value · $'+num(instructionStake(t)):null)}
function unitPickSummary(t){const c=t.current_assessment||t;return isProbabilityPick(t)?`Selected for win probability. Model-estimated return: ${pct(Math.abs(c.expected_roi)<=1e-9?0:c.expected_roi)}.`:isModelPick(t)?'Positive model estimate; stress check not passed.':null}
function modelPickNote(t){if(!unitPickLabel(t))return '';const c=t.current_assessment||t;return `<p class="panel-copy model-pick-note">${esc(unitPickSummary(t))}</p><p class="panel-copy model-pick-evidence">Model EV: ${pct(c.expected_roi)} · Stress EV: ${pct(c.stress_roi)}. ${isProbabilityPick(t)?(allTiersWeighted(t)?'Calculated base-unit stake in the $1–$5 range, reduced for related bets and capped by any supplied bankroll limits; not Kelly sizing. The $1 ticket minimum can offset the raw discount. Model-estimated return is nonpositive.':'Fixed $1 policy unit; not Kelly sizing.'):weightedModelPick(t)?'Calculated nominal-model stake in the $1–$5 range; related-exposure discount and any supplied bankroll limits still apply. No additional amount is requested for an earlier ticket.':'Nominal-model sizing capped at $1; bankroll and related-exposure limits still apply.'} These are model estimates, not a guarantee of winning or profit.</p>`}


function instructionStake(t){return t.tracked_bet?t.tracked_bet.stake:t.stake}
function validInstructionStake(t){const amount=instructionStake(t);return Number.isFinite(amount)&&amount>=1&&amount<=Math.min(5,data?.settings?.max_bet??5)}
function pendingInstruction(t){return !!t.tracked_bet&&t.tracked_bet.count===1&&t.tracked_bet.status==='open'&&t.tracking_source==='auto_assumed_placed'&&!isPlaced(t)&&validInstructionStake(t)&&executionReview(t).actionable===true}
function executableInstruction(t){return !isPlaced(t)&&validInstructionStake(t)&&(t.tracked_bet?pendingInstruction(t):executionReview(t).actionable===true)}
function executionTerms(t){return pendingInstruction(t)?t.current_assessment:t}
function instructionSignature(t){const c=executionTerms(t),n=stakeNavigation(t),r=executionReview(t),b=c.funding_basis;return JSON.stringify([c.id,c.start,c.market,c.side,c.line,c.selection,c.probability,c.push_probability,c.reference_odds,c.minimum_odds,c.stress_roi,c.model_version,c.quote_at,c.valid_until,instructionStake(t),n.event_url,n.tab,n.navigation_source,n.navigation_status,data?.settings?.recommendation_policy??'strict',b?.policy,b?.tier,b?.eligible,b?.maximum_stake,b?.minimum_odds,r.recommendation_policy,r.funding_tier,effectiveMinimumOdds(t),data?.settings?.model_pick_sizing_policy??'unit_cap_v1',b?.version,b?.model_pick_sizing_policy,r.model_pick_sizing_policy,modelPickAllocationPolicy(data?.settings),modelPickAllocationPolicy(c.decision_guard),modelPickAllocationPolicy(b),modelPickAllocationPolicy(r),modelPickAllocationPolicy(c.execution_review),data?.settings?.outcome_sizing_policy??'legacy',c.outcome_sizing_policy??'legacy',r.outcome_sizing_policy??'legacy',c.outcome_sizing_evidence?.summary_sha256])}
function readyTips(){return liveTips().filter(executableInstruction)}
function stakeDisplay(t){return view==='tips'&&executableInstruction(t)?'Bet $'+num(instructionStake(t)):t.tracked_bet?'Recorded $'+num(t.tracked_bet.stake):'No new bet'}
function trackingMarkup(t){if(view==='tips'&&pendingInstruction(t))return `<p class="panel-copy">Bet $${num(t.tracked_bet.stake)} once on this exact selection. If already placed, mark it as placed instead of repeating it.</p>`;return t.tracked_bet?`<p class="panel-copy">Original record: $${num(t.tracked_bet.stake)} at ${num(t.tracked_bet.odds)} on ${esc(when(t.tracked_bet.created_at))}. No additional bet is requested.</p>`:''}
function renderStats(){
 const ready=visibleTips(readyTips()),visible=visibleTips(liveTips()),amount=ready.reduce((n,t)=>n+instructionStake(t),0),p=data?.status?.daily_progress||{},league=$('#league-filter').value;
 const filteredFirst=(p.issuance_rows?issuedRowsVisible(p.issuance_rows):[]).length || (!p.issuance_rows?(p.first_issued_today??p.issued??0):0);
 const marked=visible.filter(isPlaced).length;
 const started=(data?.evaluated_candidates||[]).filter(t=>(league==='all'||t.league===league)&&(!t.start||new Date(t.start)<=new Date()||t.expired)).length;
 const noLonger=visible.filter(t=>t.tracked_bet&&t.instruction_review?.actionable===false).length;
 $('#stats').innerHTML=[
  ['First issued today',filteredFirst,'Shared immutable issuance count'],
  ['Ready to Bet',ready.length,'Filtered, fresh, funded and qualified'],
  ['Marked placed',marked,'Filtered checklist records'],
  ['Started / expired',started,'Filtered lifecycle rows'],
  ['No longer recommended',noLonger,'Tracked, reassessed rows'],
  ['Games',new Set(ready.map(t=>t.game_id)).size,'In the selected time window'],
  ['Total Stake','$'+num(amount),'Bet each selection once'],
  ['Daily Instructions',(p.issued??0)+' / '+(p.target??20),p.counting_basis==='since_release'?'First Issuances Since '+when(p.counting_since):'First Issuances Today']
 ].map(([label,value,sub])=>`<div class="stat"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div><div class="stat-sub">${esc(sub)}</div></div>`).join('');
}
function priceLabel(t){return t.recorded_snapshot?`Recorded odds: ${num(t.reference_odds)}`:t.reference_platform==='Stake.com'?`Stake odds: ${num(t.reference_odds)}`:`Reference odds: ${num(t.reference_odds)} (after fees)`}
function renderOpportunitySummary(){
 const hours=horizon==='48'?'48':'24',w=data?.opportunities?.windows?.[hours],league=$('#league-filter').value;
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

function stakeBetButton(t){
 const instruction=view==='tips'&&executableInstruction(t),navigation=stakeNavigation(t),url=navigation.event_url;
 if(!url)return '';
 try{const u=new URL(url);if(u.origin!=='https://stake.com'||!u.pathname.startsWith('/sports/'))return ''}catch{return ''}
 return `<div class="detail-actions"><a class="button primary stake-bet-button" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="Bet on Stake: ${esc(t.selection)} — ${esc(t.market_display)} — ${esc(t.away)} at ${esc(t.home)}">${instruction?'Bet on Stake':'View on Stake'} ↗</a></div><p class="panel-copy">Opens this match. ${navigation.tab?`Select the <strong>${esc(navigation.tab)}</strong> tab → `:'Select '}<strong>${esc(t.market_display||marketName[t.market])} → ${esc(t.selection)}</strong>. The bet slip is not prefilled.</p>`;
}
function bindInstructionLink(button,id,signature=null){if(button)button.onclick=e=>{const current=readyTips().find(t=>t.id===id);if(!current||(signature&&instructionSignature(current)!==signature)){e.preventDefault();renderTips();toast('The engine is updating this selection. Only current instructions remain available.');}}}
function expireInstructions(){if(!data)return;renderStakeSizing();const current=readyTips(),ids=new Set(current.map(t=>t.id));if($$('#tips-grid [data-tip-id]').some(el=>!ids.has(el.dataset.tipId))||(view==='tips'&&selectedTip&&$('#detail-dialog').open&&!current.some(t=>t.id===selectedTip.id&&instructionSignature(t)===selectedInstructionSignature)))renderTips()}
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
 return '<div class="empty"><h3>No new bets ready right now</h3><p>The engine keeps checking upcoming markets. Qualified bets appear here automatically with their USD stake. Earlier instructions and placed bets remain in Bet Tracker.</p></div>';
}
function renderTips(){
 const rows=visibleTips(readyTips());
 if(view==='tips'&&selectedTip&&$('#detail-dialog').open){const current=rows.find(t=>t.id===selectedTip.id);if(!current||instructionSignature(current)!==selectedInstructionSignature){$('#detail-dialog').close();selectedTip=null;selectedInstructionSignature=null;}}$('#tip-count').textContent=rows.length;
 $('#tips-grid').innerHTML=rows.length?rows.map((t,index)=>{
  const c=executionTerms(t);
  return `<article class="tip-card" data-tip-id="${esc(t.id)}"><div class="tip-top"><span class="rank">${String(index+1).padStart(2,'0')}</span><span class="league ${esc(t.league)}">${esc(t.league)}</span><span class="time">${esc(when(t.start))}</span></div><div class="tip-match">${esc(t.away)} @ ${esc(t.home)}</div><div class="tip-market">${esc(c.market_display||marketName[t.market])}</div>${stakeNavigation(t).tab?`<p class="panel-copy stake-tab">Stake tab: <strong>${esc(stakeNavigation(t).tab)}</strong></p>`:''}<div class="selection">${esc(t.selection)}</div><div class="tip-metrics"><div><div class="metric-label">MODEL WIN PROBABILITY</div><div class="metric-value">${pct(c.probability)}</div></div><div><div class="metric-label">STAKE ODDS</div><div class="metric-value">${num(c.reference_odds)}</div></div><div><div class="metric-label">STAKE TO PLACE · USD</div><div class="metric-value">Bet $${num(instructionStake(t))}</div></div></div><p class="panel-copy">Minimum odds: <strong>${displayedMinimumOdds(t)}</strong>${isModelPick(t)?' (above model break-even)':''} · Single bet · ${allTiersWeighted(t)||weightedModelPick(t)?'$1–$5 calculated stake':unitPickLabel(t)?'$1 maximum for this selection':'$1–$5 per selection'}</p>${unitPickSummary(t)?`<p class="panel-copy model-pick-note">${esc(unitPickSummary(t))}</p>`:''}${stakeBetButton(t)}${placementControl(t)}<div class="tip-bottom"><span class="badge positive">${instructionTierLabel(t)|| (t.is_additional?'Additional Bet':'Ready to Bet')}</span><button class="detail-button" data-detail="${esc(t.id)}">Bet Details →</button></div><p class="tip-reference">Odds checked: ${esc(when(c.quote_at))}</p></article>`;
 }).join(''):emptyTipsMessage();
 renderStats();renderOpportunitySummary();renderTracked();renderAlternatives();renderEvaluated();bindPlacementControls();$$('#tips-grid .stake-bet-button').forEach(button=>bindInstructionLink(button,button.closest('[data-tip-id]').dataset.tipId));$$('[data-detail]').forEach(b=>b.onclick=()=>showDetail(b.dataset.detail,b.dataset.current==='true'));
}
function historyRows(){return liveTips().filter(t=>t.tracked_bet||isPlaced(t)).sort((a,b)=>new Date(a.start)-new Date(b.start)||String(a.id).localeCompare(String(b.id)))}
function renderTracked(){
 const history=historyRows();
 const status=t=>isPlaced(t)?'Marked as placed':pendingInstruction(t)?'Current instruction':t.tracked_bet&&t.instruction_review?.actionable===false?'No longer recommended · Not a current instruction':'Recorded · Not a current instruction';
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
 const catalog=data?.automation?.market_catalog;
 $('#expanded-catalog-summary').textContent=catalog?.checked_at?`Catalogue checked ${when(catalog.checked_at)}. Includes all fixtures returned by the documented tournament endpoints; complete fixture and odds responses are archived. Refresh target: 15 minutes. These counts are not today's betting instructions. Tabs are navigation; each exact contract needs a validated model and settlement rule. Same Game Multi requires its actual combined Stake quote.`:'Full market catalogue is being collected.';
 $('#expanded-catalog-table').innerHTML=table(['League','Exact Stake Market','Observed Tabs','Model/Data Family','Scope to Verify','Dependencies'],(catalog?.types||[]).map(m=>[esc(m.league),esc(m.market),esc((m.tabs||[]).join(' · ')||'Provider section; tab unverified'),esc(m.family.replaceAll('_',' ')),esc(m.scope.replaceAll('_',' ')),esc((m.dependencies||[]).join(', ').replaceAll('_',' '))]));
}
function decisionEvidence(t){
 const g=t.decision_guard;if(!g)return '<p class="panel-copy">Historical decision: no runtime evidence-guard record was attached at issuance.</p>';
 return `<h3>Decision Evidence</h3><p>Review: deterministic source and calculation checks. No separate LLM approval.</p><p>Input and calculation checks: ${g.passed?'Passed':'Rejected'}. Contract: ${esc(g.contract_version)}. Model: ${esc(g.model?.version)}.</p><p>Methodology references: ${(g.knowledge||[]).map(k=>`<a href="/api/knowledge/${encodeURIComponent(k.id)}" target="_blank" rel="noopener">${esc(k.id)}</a>`).join(' · ')}.</p><p class="panel-copy">Book references describe methods; they do not prove that this selection will win. Source hashes and model fingerprints are retained in the decision record.</p>`;
}

function renderSegmentModels(){
 $('#segment-models-table').innerHTML=table(['League / Scope','Independent Model','Train / Selection / Test','Test Log Score / Baseline','Validated Market Families'],Object.values(data?.segment_models||{}).map(m=>[esc(m.league+' / '+m.scope.replaceAll('_',' ')),esc(m.model_family),`${m.partitions.train} / ${m.partitions.selection} / ${m.partitions.test}`,num(m.test.joint_log_score,4)+' / '+num(m.baseline.joint_log_score,4),esc(m.validated_markets.join(', '))]));
 $('#segment-prospective-table').innerHTML=table(['Scope','Published','Completed','Mean Three-Way Brier'],(data?.performance?.segment_prospective||[]).map(r=>[esc(r.scope),esc(r.published),esc(r.settled),num(r.brier,4)]));
 $('#period-models-table').innerHTML=table(['League / Scope','Train / Selection / Test','Held-Out Brier','Validated Families'],Object.values(data?.period_models||{}).map(m=>[esc(m.league+' / '+m.scope.replaceAll('_',' ')),`${m.partitions.train} / ${m.partitions.selection} / ${m.partitions.test}`,num(m.test.mean_brier,4),esc(m.validated_markets.join(', '))]));
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
 const age=Date.now()-new Date(row.quote_at),maximum=Math.min(5,data?.settings?.max_bet??5),review=row.execution_review,basis=row.funding_basis;
 const outcomePolicy=data?.settings?.outcome_sizing_policy??'legacy';
 if(!['legacy','historical_game_weighted_v1','historical_game_weighted_v2'].includes(outcomePolicy)||(review?.outcome_sizing_policy??'legacy')!==outcomePolicy||(row.outcome_sizing_policy??'legacy')!==outcomePolicy)return false;
 const receiptVersion=policy==='all_tiers_weighted_v3'?'funding-policy-v3':policy==='model_weighted_v2'?'funding-policy-v2':null;
 if(!review||review.actionable!==true||review.state!=='current'||review.model_pick_sizing_policy!==policy||
  review.quote_at!==row.quote_at||review.valid_until!==row.valid_until||review.odds!==row.odds||
  review.recommendation_policy!==(data?.settings?.recommendation_policy??'strict')||
  (receiptVersion&&(!basis||basis.version!==receiptVersion||basis.model_pick_sizing_policy!==policy||basis.eligible!==true||basis.tier!==review.funding_tier||basis.policy!==review.recommendation_policy||!Number.isFinite(basis.maximum_stake)||basis.maximum_stake<1||basis.maximum_stake>5||row.target_stake>basis.maximum_stake))||
  !row.sizing||row.sizing.amount!==row.target_stake)return false;
 return dashboardAvailable&&row.eligible===true&&['unit_cap_v1','model_weighted_v2','all_tiers_weighted_v3'].includes(policy)&&
  row.model_pick_sizing_policy===policy&&Number.isFinite(age)&&age>=-5000&&age<=300000&&
  new Date(row.valid_until)>new Date()&&new Date(row.start)>new Date()&&
  Number.isFinite(row.odds)&&row.odds>1&&Number.isFinite(row.target_stake)&&row.target_stake>=1&&row.target_stake<=maximum&&
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

function render(){renderStakeSizing();renderStats();renderTips();renderArchive();renderGames();renderModels();$('#run-stage').textContent=data.engine?.stage||'Updated';$('#last-updated').textContent=data.generated_at?'Last Published: '+when(data.generated_at):'Preparing the First Analysis';const notices=[];if(data.settings?.auto_track_recommendations)notices.push('Auto-tracking is on: funded recommendations are assumed placed on Stake once, using the displayed odds and stake.');if(data.status?.daily_progress){const p=data.status.daily_progress;notices.push(`Daily target: ${p.issued}/${p.target} distinct funded recommendations issued (${p.date}, ${p.timezone}). ${p.counting_basis==='since_release'?'Counting from '+when(p.counting_since)+'. '+p.previously_issued_today+' earlier issuances excluded from this target. ':''}${p.distinct_games==null?'':p.distinct_games+' distinct games. '}${p.shortfall>0?p.shortfall+' more needed; the target does not override price or model requirements.':'Target reached; qualifying new picks are still published.'}`);}notices.push(data.settings?.daily_risk_limit==null?'No fixed daily stake cap. Maximum $5 per bet.':'Daily stake cap: $'+num(data.settings.daily_risk_limit)+'.');if(data.engine?.error)notices.push('Refresh Error: '+data.engine.error);$('#notice').textContent=notices.join(' ');$('#notice').hidden=!notices.length;$('#connection').textContent=data.engine?.running?'Evaluating Markets':data.engine?.error?'Data Feed Error':'Live Monitoring';$('#refresh').disabled=!!data.engine?.running;}
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
async function fetchDashboard(){
 const request=++dashboardRequest,startedPlacementEpoch=placementEpoch;
 const controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),20000);
 try{
  const d=await api('/api/dashboard',{signal:controller.signal});
  if(request<appliedDashboardRequest)return;
  // A request begun before/during a checklist write may contain pre-save marks.
  // Preserve acknowledged local state until a request begun after that write.
  if(data&&startedPlacementEpoch!==placementEpoch)d.placement_marks=data.placement_marks;
  const first=!data;appliedDashboardRequest=request;data=d;dashboardAvailable=true;
  render();syncStatisticsChrome();if(first)populateSettings();if(view==='ledger')renderLedger();if(view==='statistics')loadStatistics();if(view==='games')renderGames();
 }catch(e){
  if(request<appliedDashboardRequest)return;
  dashboardAvailable=false;if(data){renderStats();renderTips();renderStakeSizing()}$('#connection').textContent='Connection Lost';$('#notice').hidden=false;$('#notice').textContent='The engine is reconnecting. Current betting instructions will return after a successful update.';
 }finally{clearTimeout(deadline)}
}
function sourceTerms(t){
 if(t.reference_platform==='Stake.com') return `<p><strong>Stake feed snapshot:</strong> ${t.recorded_snapshot?'recorded':'current'} decimal odds ${num(t.reference_odds)}. The exact Stake market is <strong>${esc(t.market_display||marketName[t.market])}</strong>; the exact outcome and line are shown above. This is a sportsbook price, not a share price. Stake minimum stake and account-specific limits may differ.</p>`;
 return `<p>${esc(t.reference_platform)} benchmark: ${num(t.reference_price,3)} USD/share plus ${num(t.reference_fee_per_share,4)} USD fee/share. This benchmark is not a Stake sportsbook price.</p>`;
}

function outcomeSizingTrace(t){
 const a=t.stake_sizing?.outcome_adjustment,e=a?.source;
 if(!['historical_game_weighted_v1','historical_game_weighted_v2'].includes(a?.policy)||!e)return '';
 if(![a.base_amount,a.factor,a.adjusted_amount,e.independent_games,e.game_wr,e.game_roi,e.break_even_wr].every(Number.isFinite))return '';
 const v2=a.policy==='historical_game_weighted_v2';
 if(v2&&![a.source_factor,a.applied_game_score_sum,a.shrinkage_denominator,a.historical_signal,a.applied_factor].every(Number.isFinite))return '';
 const calculation=v2
  ? `V2 calculation: $${num(a.base_amount)} × [1 + 4 × ${num(a.historical_signal,4)}] = $${num(a.adjusted_amount)} after caps. Signed game scores sum to ${num(a.applied_game_score_sum,4)} with shrinkage denominator ${num(a.shrinkage_denominator,0)}. Each game score is min(1, ROI / 1.0, win-rate surplus / 0.5); negative scores are not clipped. Both segment effects must be positive. Source factor ${num(a.source_factor,3)} remains in the verified v1 evidence.`
  : `Base calculation: $${num(a.base_amount)} × ${num(a.factor,3)} historical factor → $${num(a.adjusted_amount)} after caps.`;
 return `<details class="outcome-sizing-trace"><summary>Historical Outcome Sizing</summary><p>Segment: ${esc(a.segment)} · ${e.independent_games} independent settled games.</p><p>Game-weighted win rate: ${pct(e.game_wr)} · Break-even win rate: ${pct(e.break_even_wr)} · Game-weighted ROI: ${pct(e.game_roi)}.</p><p>${calculation} This is a sizing calculation; place only the instruction amount shown above, once.</p><p>Evidence cutoff: ${esc(when(e.as_of))}. Small samples are shrunk. Historical results do not establish future profitability.</p><code>${esc(e.summary_sha256)}</code></details>`;
}

function showDetail(id,preferCurrent=false){const original=(preferCurrent?[...allEvaluated(),...liveTips()]:[...liveTips(),...allEvaluated()]).find(x=>x.id===id);if(!original)return;const t=view==='tips'&&pendingInstruction(original)?{...original,...original.current_assessment,tracked_bet:original.tracked_bet,tracking_source:original.tracking_source,instruction_review:original.instruction_review,current_assessment:original.current_assessment,recorded_snapshot:false}:original;selectedTip=t;selectedInstructionSignature=view==='tips'?instructionSignature(original):null;$('#detail-content').innerHTML=`<span class="league ${esc(t.league)}">${esc(t.league)}</span><h2>${esc(t.selection)}</h2><p class="panel-copy">${esc(t.away)} @ ${esc(t.home)} · ${esc(when(t.start))}</p><div class="selection">${esc(t.market_display||marketName[t.market])}</div><div class="detail-data"><div><div class="metric-label">MODEL PROBABILITY</div><div class="metric-value">${pct(t.probability)}</div></div><div><div class="metric-label">MIN. DECIMAL ODDS</div><div class="metric-value">${displayedMinimumOdds(t)}</div></div><div><div class="metric-label">SUGGESTED STAKE</div><div class="metric-value">${stakeDisplay(t)}</div></div></div><p>${esc(t.stake_note)}</p>${outcomeSizingTrace(t)}${modelPickNote(t)}${trackingMarkup(t)}${stakeBetButton(t)}${executableInstruction(t)?'':`<p>${esc(allocationReason(t))}</p>`}${betInstructions(t)}${decisionEvidence(t)}<h3>Why This Pick?</h3><ul>${t.reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul><h3>Check Your Stake Odds</h3><form id="quote-check-form"><div class="form-row"><label>Decimal Odds<input name="odds" type="number" min="1.01" step="0.01" required placeholder="e.g. 1.90"></label></div><button class="button ghost">Check Stake Odds for This Selection</button><p id="quote-result" role="status"></p></form><h3>Reference Price and Settlement Rules</h3>${sourceTerms(t)}<p>Odds Checked: ${esc(when(t.quote_at))}. Quote Valid Until: ${esc(when(t.valid_until))}.</p>${contextMarkup(t.game_context)}<h3>Model Limitations and Context</h3><ul>${t.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul><div class="detail-actions"><a class="button ghost" target="_blank" rel="noopener noreferrer" href="${esc(t.reference_url)}">View Reference Market ↗</a><button class="button primary" id="record-bet">Log Placed Bet</button></div><div id="bet-form-wrap" hidden><h3>Record Your Placed Bet</h3><form id="bet-form"><div class="form-row"><label>Stake (USD)<input name="stake" type="number" step="0.01" min="0.01" required value="${t.stake||1}"></label><label>Accepted Decimal Odds<input name="odds" type="number" min="1.01" step="0.01" required placeholder="Actual accepted odds"></label></div><label>Platform<input name="platform" required maxlength="80" placeholder="e.g. Stake.com"></label><label>Settlement<select name="settlement_mode"><option value="manual">Manual: enter the actual total return</option><option value="sportsbook">Sportsbook: exact named market; stake refunded on a push</option></select></label><button class="button primary">Save Bet</button></form><p class="panel-copy">This records a bet you have already placed. It does not submit a bet. Polymarket share positions require separate settlement checks.</p></div><details><summary>Sources, Model and Market Settlement Rules</summary><p><a href="${esc(t.source_url)}" target="_blank" rel="noopener noreferrer">Sports Data Source</a> · Model: ${esc(t.model_version)}</p><p>${esc(t.stress_note)}</p><pre>${esc(t.reference_contract_description)}</pre></details>`;$('#quote-check-form').onsubmit=async e=>{e.preventDefault();try{const q=await api('/api/tips/'+t.id+'/quote',{method:'POST',body:JSON.stringify({odds:Number(e.target.odds.value)})});$('#quote-result').textContent=q.review_failed?q.note:q.expired?'This pick has expired. Wait for an updated analysis.':q.already_recorded?`This exact bet is already recorded. Entered odds ${q.price_qualifies?'meet':'do not meet'} the current model threshold. No additional ticket or stake is requested. Estimated ROI: ${pct(q.expected_roi)}. The entered price has not been verified against Stake.`:`Hypothetical price calculation. Estimated ROI: ${pct(q.expected_roi)}. Stress Scenario ROI: ${pct(q.stress_roi)}. Calculated Stake: $${num(q.stake)}. ${q.note} This does not issue a betting instruction; entered odds are not verified against Stake.`}catch(err){$('#quote-result').textContent=err.message}};$('#record-bet').onclick=()=>{$('#bet-form-wrap').hidden=false;$('#bet-form input').focus()};$('#bet-form').onsubmit=async e=>{e.preventDefault();const f=e.target;const submit=f.querySelector('button');submit.disabled=true;const requestId=f.dataset.requestId||(f.dataset.requestId=crypto.randomUUID());try{await api('/api/bets',{method:'POST',body:JSON.stringify({tip_id:t.id,stake:Number(f.stake.value),odds:Number(f.odds.value),platform:f.platform.value,request_id:requestId,settlement_mode:f.settlement_mode.value})});toast('Bet recorded.');$('#detail-dialog').close();await load()}catch(err){toast(err.message);submit.disabled=false}};if(t.tracked_bet)$('#record-bet').hidden=true;if(view==='tips')bindInstructionLink($('#detail-content .stake-bet-button'),t.id,selectedInstructionSignature);$('#detail-dialog').showModal()}
$$('.nav-item').forEach(b=>b.onclick=()=>{view=b.dataset.view;$$('.nav-item').forEach(n=>n.classList.toggle('active',n===b));$$('.view').forEach(v=>v.hidden=v.id!=='view-'+view);$('#breadcrumb').textContent=title[view];$('#page-title').innerHTML=view==='tips'?'Your next bet.<br><span>Backed by data.</span>':esc(title[view]);$('#page-description').textContent=view==='tips'?'Picks for today and the next 48 hours, with Stake market names and exact selections.':'Track your picks, model performance and actual betting results.';if(view==='ledger')renderLedger();if(view==='sizing')renderStakeSizing();if(view==='statistics')loadStatistics();if(view==='games')renderGames();if(view==='settings')populateSettings();syncStatisticsChrome()});
$$('[data-horizon]').forEach(b=>b.onclick=()=>{horizon=b.dataset.horizon;$$('[data-horizon]').forEach(x=>x.classList.toggle('selected',x===b));renderTips()});$('#league-filter').onchange=renderTips;$('#market-filter').onchange=renderTips;$('#sort-filter').onchange=renderTips;
$('#refresh').onclick=async()=>{try{await api('/api/refresh',{method:'POST'});toast('The engine is refreshing markets and picks.');setTimeout(load,800)}catch(e){toast(e.message)}};
$('#settings-form').onsubmit=async e=>{e.preventDefault();const f=e.target;const s={auto_track_recommendations:f.elements.auto_track_recommendations.checked};for(const k of ['daily_pick_target','max_bet','unit_stake','refresh_seconds'])s[k]=Number(f.elements[k].value);s.daily_risk_limit=f.daily_risk_limit.value===''?null:Number(f.daily_risk_limit.value);s.bankroll=f.bankroll.value?Number(f.bankroll.value):null;s.monthly_system_cost=f.monthly_system_cost.value!==''?Number(f.monthly_system_cost.value):null;s.minimum_roi=Number(f.minimum_roi.value)/100;try{await api('/api/settings',{method:'PUT',body:JSON.stringify(s)});$('#settings-feedback').textContent='Saved';toast('Settings saved. Picks are being recalculated.')}catch(err){$('#settings-feedback').textContent=err.message}};
$('.close-dialog').onclick=()=>$('#detail-dialog').close();$('#detail-dialog').onclick=e=>{if(e.target===$('#detail-dialog'))$('#detail-dialog').close()};
function tick(){$('#clock').textContent=new Intl.DateTimeFormat('en-GB',{weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Budapest',timeZoneName:'short'}).format(new Date())}tick();setInterval(tick,30000);setInterval(expireInstructions,1000);load();const stream=new EventSource('/api/events');stream.addEventListener('update',()=>load());stream.onerror=()=>$('#connection').textContent='Reconnecting…';setInterval(load,15000);

function contextMarkup(context={}){
 const i=context.injuries||{};let html='';
 const pitchers=['home','away'].map(side=>context[side+'_pitcher']?.fullName).filter(Boolean);
 if(pitchers.length)html+='<h3>Probable Starting Pitchers</h3><p>'+pitchers.map(esc).join(' · ')+'</p>';
 const rows=['home','away'].flatMap(side=>(i[side]||[]).map(r=>({...r,side})));
 if(rows.length)html+='<h3>Injury Report · '+esc(i.snapshot_date)+'</h3><p>These statuses are not yet applied as numerical adjustments to the model.</p><ul>'+rows.map(r=>'<li><strong>'+esc(r.athlete_display_name)+'</strong> · '+esc(r.athlete_position)+' · '+esc(r.status)+'<br>'+esc(r.short_comment)+'</li>').join('')+'</ul>';
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
function syncStatisticsChrome(){
 const stats=view==='statistics'||view==='games';$('#stats').hidden=stats;$('#window-coverage').hidden=stats;$('#notice').hidden=stats||!$('#notice').textContent;
 $('.page-head .eyebrow').textContent=view==='games'?'NEXT 24 HOURS':view==='statistics'?'RECORDED RESULTS':'YOUR NEXT PICK';
 if(view==='games')$('#page-description').textContent='Five leagues, one chronological schedule. Budapest local time. No scores or odds.';
 if(view==='statistics')$('#page-description').textContent='Win rate, money staked, returns and improvement work from your recorded bets.';
}
function statisticsFilters(){return {period:$('#statistics-period').value,league:$('#statistics-league').value,execution:$('#statistics-execution').value}}
async function loadStatistics(){
 const filters=statisticsFilters(),key=new URLSearchParams(filters).toString();
 if(statisticsFlight&&key===statisticsKey)return statisticsFlight;
 const changed=key!==statisticsKey;statisticsKey=key;const request=++statisticsRequest;
 statisticsController?.abort();const controller=new AbortController();statisticsController=controller;
 if(changed)$('#statistics-results').hidden=true;
 $('#statistics-status').textContent='Loading recorded results…';$('#statistics-status').classList.remove('error');
 const timeout=setTimeout(()=>controller.abort(),15000);
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
let statisticsPerformanceReport=null;
function renderStatisticsPerformance(report){
 statisticsPerformanceReport=report;
 const basis=$('#statistics-performance-basis').value,section=report[basis]||{},performance=section.cumulative_performance||{},summary=performance.summary||{};
 const label=basis==='ticket_ledger'?'Tracked ticket cash':'Original engine decisions';
 $('#statistics-performance-note').textContent=label+'. '+(basis==='ticket_ledger'?'Duplicate tickets affect cash. Assumed and reported placement are not account verification. ':'Original issued odds and stakes; extra tickets do not add another engine decision. ')+(performance.basis||'Cumulative performance is not available for this cohort.');
 const metrics=[['Net P&L',statisticsMoney(summary.pnl)],['ROI',statisticsPct(summary.roi)],['Win Rate',statisticsPct(summary.hit_rate)],['Max Drawdown',statisticsMoney(summary.max_drawdown)]];
 $('#statistics-performance-metrics').innerHTML=metrics.map(([name,value])=>`<div><span>${esc(name)}</span><strong>${esc(value)}</strong></div>`).join('');
 const raw=Array.isArray(performance.points)?performance.points:[];
 const points=raw.filter(p=>p&&Number.isFinite(p.pnl)&&typeof p.at==='string'&&Number.isFinite(Date.parse(p.at))).map(p=>({...p,time:Date.parse(p.at)})).sort((a,b)=>a.time-b.time);
 const loop=report.improvement_loop||{},monitor=loop.cumulative_monitor;
 const reviewed=typeof loop.last_reviewed_at==='string'&&Number.isFinite(Date.parse(loop.last_reviewed_at))?` Last reviewed ${when(loop.last_reviewed_at)}.`:'';
 $('#statistics-performance-monitor').textContent=monitor?`Outcome monitoring · Full original-decision history, independent of the chart filters: ${statisticsWords(monitor.state)}.${Number.isFinite(monitor.summary?.current_drawdown)?` Current drawdown ${statisticsMoney(monitor.summary.current_drawdown)}.`:''}${reviewed} ${monitor.diagnostic_due?'A drawdown diagnostic is due through the existing outcome-review workflow. ':''}Monitoring does not automatically change stakes or promote a model.`:'Outcome monitoring · Full original-decision history: no cumulative review observation available yet.';
 const coverage=Object.entries(performance.coverage||{}).filter(([,v])=>['number','string','boolean'].includes(typeof v)).map(([k,v])=>`${statisticsWords(k)}: ${v}`).join(' · ');
 $('#statistics-performance-coverage').textContent=[coverage,raw.length!==points.length?`${raw.length-points.length} points with unavailable date or cash are not plotted.`:'','ROI = net P&L ÷ settled stakes. Win rate = wins ÷ decisive wins and losses.'].filter(Boolean).join(' ');
 const chart=$('#statistics-performance-chart');
 if(!points.length){chart.innerHTML='<p class="statistics-chart-empty">No dated settled cash results to plot for this selection.</p>';}else{
  const width=900,height=280,left=76,right=24,top=26,bottom=46,plotWidth=width-left-right,plotHeight=height-top-bottom;
  let low=0,high=0;for(const point of points){low=Math.min(low,point.pnl);high=Math.max(high,point.pnl);}
  const padding=Math.max((high-low)*.12,.5);low-=padding;high+=padding;
  const first=points[0].time,last=points[points.length-1].time;
  const x=p=>left+(last===first?plotWidth/2:(p.time-first)/(last-first)*plotWidth),y=value=>top+(high-value)/(high-low)*plotHeight;
  const date=t=>new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'Europe/Budapest'}).format(new Date(t));
  const ticks=[high,0,low],grid=ticks.map(value=>`<line x1="${left}" x2="${width-right}" y1="${y(value)}" y2="${y(value)}" class="${value===0?'statistics-zero-line':'statistics-grid-line'}"/><text x="${left-10}" y="${y(value)+4}" text-anchor="end">${esc(statisticsMoney(value))}</text>`).join('');
  const path=`M ${x(points[0])} ${y(0)} `+points.map(p=>`L ${x(p)} ${y(p.pnl)}`).join(' ');
  const tooltip=p=>`${when(p.at)} · Net P&L ${statisticsMoney(p.pnl)} · ROI ${statisticsPct(p.roi)} · Win rate ${statisticsPct(p.hit_rate)} · ${p.settled??0} settled · ${p.games??0} games`;
  chart.innerHTML=`<svg class="statistics-pnl-chart" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="statistics-chart-title statistics-chart-description"><title id="statistics-chart-title">${esc(label)} cumulative net P&amp;L</title><desc id="statistics-chart-description">${esc(`${points.length} cumulative points, ${date(first)} to ${date(last)}. Final P&L ${statisticsMoney(points[points.length-1].pnl)}. Zero line included. Exact values are available in the table below.`)}</desc>${grid}<path d="${path}" class="statistics-pnl-line"/>${points.map(p=>`<circle cx="${x(p)}" cy="${y(p.pnl)}" r="4" tabindex="0" role="img" aria-label="${esc(tooltip(p))}" class="statistics-pnl-point"><title>${esc(tooltip(p))}</title></circle>`).join('')}<text x="${left}" y="${height-13}">${esc(date(first))}</text>${last!==first?`<text x="${width-right}" y="${height-13}" text-anchor="end">${esc(date(last))}</text>`:''}</svg>`;
 }
 $('#statistics-performance-table').innerHTML=points.length?table(['Date (Budapest)','Net P&L','Settled Stakes','ROI','Win Rate','Settled / Games'],points.map(p=>[esc(when(p.at)),statisticsCash(p.pnl),esc(statisticsMoney(p.settled_stake)),esc(statisticsPct(p.roi)),esc(statisticsPct(p.hit_rate)),esc(`${p.settled??0} / ${p.games??0}`)])):'<p class="panel-copy">No cumulative observations available.</p>';
 const leaders=(Array.isArray(section.top_markets)?section.top_markets:[]).filter(r=>Number.isFinite(r.pnl)&&r.pnl>0).sort((a,b)=>b.pnl-a.pnl).slice(0,5);
 $('#statistics-top-markets').innerHTML=leaders.length?table(['Bet Type / Scope','Net P&L','ROI','Win Rate','Settled / Games'],leaders.map(r=>[esc(r.market_label||marketName[r.market_family]||statisticsWords(r.market_family))+`<small>${esc(statisticsWords(r.scope))}</small>`,statisticsCash(r.pnl),esc(statisticsPct(r.roi)),esc(statisticsPct(r.hit_rate)),esc(`${r.settled??0} / ${r.settled_games??r.games??0}`)])):'<p class="panel-copy">No profitable settled bet types in this selection.</p>';
}
$('#statistics-performance-basis').onchange=()=>{if(statisticsPerformanceReport)renderStatisticsPerformance(statisticsPerformanceReport)};
function renderStatistics(report){
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
