import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';

const html = readFileSync('src/betting_engine/web/index.html', 'utf8');
const script = readFileSync('src/betting_engine/web/app.js', 'utf8');
const superbrainHtml = readFileSync('src/betting_engine/web/superbrain.html', 'utf8');

function makeTip(overrides = {}) {
  const now = Date.now();
  const start = new Date(now + 3600000).toISOString();
  const quote_at = new Date(now).toISOString();
  return {
    id: 'tip-kelly-1',
    rank: 1,
    league: 'NBA',
    game_id: 'game-1',
    home: 'Boston Celtics',
    away: 'Miami Heat',
    start,
    valid_until: start,
    market: 'spreads',
    market_display: 'Handicap (Incl. Overtime)',
    model_scope: 'full_game',
    side: 'home',
    line: -4.5,
    selection: 'Boston Celtics -4.5',
    probability: 0.58,
    push_probability: 0.0,
    stress_probability: 0.55,
    minimum_odds: 1.75,
    reference_odds: 1.95,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/basketball/usa/nba/celtics-heat',
    source_url: 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    quote_at,
    stake: 6.75,
    stress_roi: 0.0725,
    expected_roi: 0.131,
    reasons: ['Independent domain margin advantage'],
    warnings: [],
    game_context: {},
    model_version: 'nba_spread_v3',
    funding_basis: {
      version: 'funding-policy-v3',
      policy: 'continuous_model_v1',
      model_pick_sizing_policy: 'all_tiers_weighted_v3',
      model_pick_allocation_policy: 'distinct_family_v1',
      eligible: true,
      tier: 'stress_value',
      minimum_odds: 1.75,
      maximum_stake: 8.0,
    },
    decision_guard: {
      passed: true,
      recommendation_policy: 'continuous_model_v1',
      model_pick_sizing_policy: 'all_tiers_weighted_v3',
      model_pick_allocation_policy: 'distinct_family_v1',
    },
    execution_review: {
      actionable: true,
      state: 'current',
      quote_at,
      valid_until: start,
      recommendation_policy: 'continuous_model_v1',
      model_pick_sizing_policy: 'all_tiers_weighted_v3',
      model_pick_allocation_policy: 'distinct_family_v1',
      funding_tier: 'stress_value',
      minimum_odds: 1.75,
    },
    stake_market: {
      name: 'Handicap (Incl. Overtime)',
      tab: 'Popular',
      event_url: 'https://stake.com/sports/basketball/usa/nba/celtics-heat',
      availability_verified: true,
    },
    ...overrides,
  };
}

async function bootDashboard(payloadOverrides = {}) {
  const w = new Window({
    url: 'http://127.0.0.1:8765',
    settings: { disableCSSFileLoading: true, disableJavaScriptFileLoading: true },
  });
  w.document.write(html);
  w.setInterval = () => 0;
  w.EventSource = class { addEventListener() {} };
  const basePayload = {
    version: 1,
    generated_at: new Date().toISOString(),
    recommendations: [],
    alternatives: [],
    evaluated_candidates: [],
    tracked_bets: [],
    placement_marks: {},
    settings: {
      bankroll: null,
      daily_risk_limit: null,
      daily_pick_target: 100,
      max_bet: 8.0,
      unit_stake: 0.5,
      minimum_roi: 0.03,
      refresh_seconds: 60,
      monthly_system_cost: null,
      recommendation_policy: 'continuous_model_v1',
      model_pick_sizing_policy: 'all_tiers_weighted_v3',
      model_pick_allocation_policy: 'distinct_family_v1',
    },
    engine: { running: false, stage: 'Updated' },
    status: { daily_progress: { issued: 42, target: 100, shortfall: 58, date: '2026-11-27', timezone: 'Europe/Budapest' } },
    games: [],
    models: {},
    performance: { prospective: [] },
    ...payloadOverrides,
  };
  w.fetch = async (url) => {
    if (String(url).startsWith('/api/dashboard')) {
      return { ok: true, json: async () => structuredClone(basePayload) };
    }
    return { ok: true, json: async () => ({}) };
  };
  w.eval(script + '\nwindow.__flight = () => dashboardFlight;\nwindow.__checkStability = (a) => { data.automation = { supervisor: { stability_audit: a } }; renderStability(); };');
  await w.__flight();
  return { w, d: w.document };
}

test('executionReview accepts $0.50 floor and $8.00 ceiling under continuous Kelly sizing and rejects out-of-range caps', async () => {
  const floorTip = makeTip({
    id: 'tip-floor-050',
    stake: 0.5,
    funding_basis: {
      version: 'funding-policy-v3',
      policy: 'continuous_model_v1',
      model_pick_sizing_policy: 'all_tiers_weighted_v3',
      model_pick_allocation_policy: 'distinct_family_v1',
      eligible: true,
      tier: 'stress_value',
      minimum_odds: 1.75,
      maximum_stake: 0.5,
    },
  });
  const highTip = makeTip({ id: 'tip-ceil-800', stake: 7.85 });
  const invalidLowCapTip = makeTip({
    id: 'tip-bad-cap',
    stake: 0.4,
    funding_basis: {
      version: 'funding-policy-v3',
      policy: 'continuous_model_v1',
      model_pick_sizing_policy: 'all_tiers_weighted_v3',
      model_pick_allocation_policy: 'distinct_family_v1',
      eligible: true,
      tier: 'stress_value',
      minimum_odds: 1.75,
      maximum_stake: 0.4,
    },
  });

  const { w, d } = await bootDashboard({ recommendations: [floorTip, highTip, invalidLowCapTip] });
  const cards = [...d.querySelectorAll('#tips-grid .tip-card')];
  assert.equal(cards.length, 2, '$0.50 and $7.85/$8.00 picks must render while $0.40 invalid cap is rejected');
  assert.match(cards[0].textContent, /Bet \$0\.50/);
  assert.match(cards[1].textContent, /Bet \$7\.85/);

  // Desktop "Bet on Stake ↗" link must remain a clean direct link without Hungarian hijack
  const stakeLink = cards[0].querySelector('.stake-bet-button');
  assert.match(stakeLink.textContent, /Bet on Stake/);
  stakeLink.click();
  assert.doesNotMatch(stakeLink.textContent, /Megnyitás/);
  await w.happyDOM.close();
});

test('Stake balance KPI never fabricates $557.93 when unsynced and renders $0.00 honestly when zero', async () => {
  const { w: wNull, d: dNull } = await bootDashboard({
    performance: { live_stake_account: { total_balance: null, available_balance: null } },
  });
  const statsNull = dNull.querySelector('#stats').textContent;
  assert.doesNotMatch(statsNull, /557\.93/);
  assert.match(statsNull, /Awaiting Sync/);
  await wNull.happyDOM.close();

  const { w: wZero, d: dZero } = await bootDashboard({
    performance: { live_stake_account: { total_balance: 0, available_balance: 0, timestamp: new Date().toISOString() } },
  });
  const statsZero = dZero.querySelector('#stats').textContent;
  assert.doesNotMatch(statsZero, /557\.93/);
  assert.match(statsZero, /Stake Account Balance\$0\.00/);
  await wZero.happyDOM.close();
});

test('Auto-Run drawer starts collapsed, dynamic leagues populate #league-filter, and #stability-status toggles visibility cleanly', async () => {
  const laligaTip = makeTip({ id: 'tip-laliga', league: 'LALIGA', home: 'Real Madrid', away: 'Barcelona' });
  const { w, d } = await bootDashboard({
    recommendations: [laligaTip],
    automation: {
      supervisor: {
        stability_audit: {
          state: 'stable',
          checked_at: new Date().toISOString(),
          error_count: 0,
          repairing_count: 0,
        },
      },
    },
  });

  // 1. Auto-Run drawer exists and is collapsed by default
  const drawer = d.querySelector('details.autorun-drawer');
  assert.ok(drawer, 'autorun-drawer details wrapper must exist');
  assert.equal(drawer.hasAttribute('open'), false, 'autorun-drawer must be collapsed by default');
  assert.ok(drawer.querySelector('#auto-bet-panel'), '#auto-bet-panel must remain inside drawer');

  // 2. Dynamic league filter includes LALIGA and uses Priority 1..4 optgroup labels
  const leagueSelect = d.querySelector('#league-filter');
  const optgroups = [...leagueSelect.querySelectorAll('optgroup')].map((g) => g.label);
  assert.ok(optgroups.some((l) => l.includes('Priority 1: US Major Sports')));
  assert.ok([...leagueSelect.options].some((o) => o.value === 'LALIGA'));

  // 3. Healthy stability status stays hidden; degraded un-hides immediately
  const stab = d.querySelector('#stability-status');
  assert.equal(stab.hidden, true);
  w.__checkStability({
    state: 'attention_required',
    checked_at: new Date().toISOString(),
    error_count: 2,
    repairing_count: 1,
  });
  assert.equal(stab.hidden, false);
  assert.match(stab.textContent, /attention required · 2 issues · 1 under repair/);

  // 4. Superbrain HTML invariants: no raw LaTeX arrows, valid camera targets, no falsy 540/1786028 fallbacks, 100% English
  assert.doesNotMatch(superbrainHtml, /\$\\rightarrow\$/);
  assert.doesNotMatch(superbrainHtml, /Futó|gombócok/);
  assert.match(superbrainHtml, /id:\s*'bottleneck_watchdog'/);
  assert.match(superbrainHtml, /tiers_pipeline:\s*'tier_3'/);
  assert.doesNotMatch(superbrainHtml, /\|\|\s*540|\|\|\s*1786028/);

  // 5. Sidebar 4th group header matches constitutional spec ("Account & Settings")
  const groupLabels = [...d.querySelectorAll('.nav-group-label')].map((el) => el.textContent.trim());
  assert.deepEqual(groupLabels, [
    'Execution',
    'Portfolio & Ledger',
    'Quant R&D & Diagnostics',
    'Account & Settings',
  ]);

  await w.happyDOM.close();
});

test('Changing #league-filter while on Betting Picks flushes dirtyViews on Research tab switch and expands >250 modeled outcomes', async () => {
  const manyEvaluated = [];
  for (let i = 0; i < 260; i++) {
    manyEvaluated.push(
      makeTip({
        id: `eval-${i}`,
        league: i < 255 ? 'NBA' : 'NHL',
        game_id: `game-${i}`,
        selection: i < 255 ? `Celtics -${(i % 10) + 1}.5` : `Bruins Over 5.5 #${i}`,
        stake: 0,
      })
    );
  }
  const altNba = makeTip({ id: 'alt-nba', league: 'NBA', stake: 0, selection: 'Heat +4.5' });
  const altNhl = makeTip({ id: 'alt-nhl', league: 'NHL', stake: 0, selection: 'Rangers ML' });
  const { w, d } = await bootDashboard({
    recommendations: [makeTip({ id: 'rec-nba', league: 'NBA', stake: 3.5 })],
    alternatives: [altNba, altNhl],
    evaluated_candidates: manyEvaluated,
  });

  // Initial hydration caps #evaluated-table at 250 rows with expansion button for all 260
  assert.equal(d.querySelectorAll('#evaluated-table tbody tr').length, 250);
  const expandBtnInitial = d.querySelector('#evaluated-show-all-btn');
  assert.ok(expandBtnInitial);
  assert.match(expandBtnInitial.textContent, /Show all 260 modeled outcomes/);

  // Filter to NHL while on Betting Picks (#view-tips): Research is off-screen & marked dirty
  const leagueFilter = d.querySelector('#league-filter');
  leagueFilter.value = 'NHL';
  leagueFilter.onchange();
  assert.equal(d.querySelector('#evaluated-count').textContent, '260', 'Off-screen Research table deferred while hidden');

  // Switch to Research (#view-research): dirtyViews must flush immediately to the 5 NHL candidates!
  d.querySelector('button.nav-item[data-view="research"]').click();
  assert.equal(d.querySelector('#view-research').hidden, false);
  assert.equal(d.querySelector('#evaluated-count').textContent, '5');
  assert.equal(d.querySelectorAll('#evaluated-table tbody tr').length, 5);
  assert.match(d.querySelector('#alternatives-table').textContent, /Rangers ML/);
  assert.doesNotMatch(d.querySelector('#alternatives-table').textContent, /Heat \+4\.5/);

  // Switch filter back to 'all' while on Research and click "Show all 260 modeled outcomes"
  leagueFilter.value = 'all';
  leagueFilter.onchange();
  assert.equal(d.querySelectorAll('#evaluated-table tbody tr').length, 250);
  d.querySelector('#evaluated-show-all-btn').click();
  assert.equal(d.querySelectorAll('#evaluated-table tbody tr').length, 260);

  await w.happyDOM.close();
});

