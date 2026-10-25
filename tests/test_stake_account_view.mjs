import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';

const html = readFileSync('src/betting_engine/web/index.html', 'utf8');
const script = readFileSync('src/betting_engine/web/app.js', 'utf8');

async function bootStakeAccount(t, dashboardData = null) {
  const w = new Window({
    url: 'http://127.0.0.1:8765',
    settings: {
      disableCSSFileLoading: true,
      disableJavaScriptFileLoading: true,
      disableIframePageLoading: true
    }
  });
  t.after(() => w.happyDOM.close());
  w.document.write(html);
  w.setInterval = () => 0;
  w.EventSource = class {
    addEventListener() {}
  };

  const writes = [];
  w.fetch = async (path, options) => {
    if (options?.method && options.method !== 'GET') {
      writes.push({path, options});
    }
    if (path.startsWith('/api/stake-account/history')) {
      return {
        ok: true,
        json: async () => ({
          period: 'all',
          summary: {
            current_balance: 724.87,
            starting_balance: 796.92,
            net_change: -72.05,
            pct_change: -9.04,
            min_balance: 720.63,
            max_balance: 796.92,
            high_watermark: 796.92,
            data_points_count: 5
          },
          points: [
            {timestamp: '2026-10-07T22:00:00+00:00', total_balance: 762.36, available_balance: 562.36, open_exposure: 200.0, open_bets_count: 50},
            {timestamp: '2026-10-08T22:00:00+00:00', total_balance: 753.28, available_balance: 553.28, open_exposure: 200.0, open_bets_count: 50},
            {timestamp: '2026-10-09T22:00:00+00:00', total_balance: 735.42, available_balance: 535.42, open_exposure: 200.0, open_bets_count: 50},
            {timestamp: '2026-10-10T22:00:00+00:00', total_balance: 720.63, available_balance: 520.63, open_exposure: 200.0, open_bets_count: 50},
            {timestamp: '2026-10-11T16:17:13+00:00', total_balance: 724.87, available_balance: 457.45, open_exposure: 267.42, open_bets_count: 122}
          ]
        })
      };
    }
    if (path.startsWith('/api/stake-account')) {
      if (options?.method === 'POST') {
        const body = JSON.parse(options.body);
        return {
          ok: true,
          json: async () => ({
            status: 'ok',
            token_configured: true,
            token_masked: 'ecb294••••••••••••••••0407',
            base_bankroll: body.base_bankroll,
            total_balance: body.base_bankroll - 50.0,
            available_balance: body.base_bankroll - 50.0 - 250.0,
            open_exposure: 250.0,
            open_bets_count: 85,
            settled_bets_count: 120,
            settled_pnl: -50.0,
            by_league: {
              'NFL': {count: 40, exposure: 150.0},
              'WTA': {count: 45, exposure: 100.0}
            }
          })
        };
      }
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          token_configured: true,
          token_masked: 'ecb294••••••••••••••••0407',
          base_bankroll: 1000.0,
          total_balance: 950.0,
          available_balance: 700.0,
          open_exposure: 250.0,
          open_bets_count: 85,
          settled_bets_count: 120,
          settled_pnl: -50.0,
          by_league: {
            'NFL': {count: 40, exposure: 150.0},
            'WTA': {count: 45, exposure: 100.0}
          }
        })
      };
    }

    if (path.startsWith('/api/dashboard')) {
      return {
        ok: true,
        json: async () => ({
          engine: {stage: 'Updated', running: false},
          settings: {bankroll: 1000, max_bet: 5},
          recommendations: dashboardData?.recommendations || [],
          alternatives: dashboardData?.alternatives || [],
          tracked_bets: dashboardData?.tracked_bets || [],
          ...(dashboardData || {})
        })
      };
    }

    if (path.startsWith('/api/stage-betslip')) {
      const body = options?.body ? JSON.parse(options.body) : {};
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          placed: body.auto_place ? true : false,
          stake: body.stake || 2.50,
          odds: body.odds || 1.95,
          selection: body.selection
        })
      };
    }

    if (path.includes('/checklist')) {
      const body = options?.body ? JSON.parse(options.body) : {};
      return {
        ok: true,
        json: async () => ({
          placed: body.placed,
          placed_at: body.placed ? '2026-10-17T12:00:00Z' : null
        })
      };
    }

    return {ok: true, json: async () => ({})};
  };

  w.eval(script);
  await new Promise(r => setTimeout(r, 40));
  return {w, writes};
}

test('Stake Live Account view renders telemetry, cards, and league exposure table', async (t) => {
  const {w} = await bootStakeAccount(t);
  const doc = w.document;

  const navBtn = doc.querySelector('.nav-item[data-view="stake_account"]');
  assert.ok(navBtn, 'Nav button for stake_account should exist');
  assert.match(navBtn.textContent, /Stake Live Account/);

  // Click nav item to switch to stake_account view
  navBtn.click();
  await new Promise(r => setTimeout(r, 40));

  const viewSec = doc.querySelector('#view-stake_account');
  assert.ok(viewSec, 'view-stake_account should exist');
  assert.strictEqual(viewSec.hidden, false, 'view-stake_account should be visible');

  // Check metrics
  assert.strictEqual(doc.querySelector('#stake-total-balance').textContent, '$950.00');
  assert.strictEqual(doc.querySelector('#stake-avail-balance').textContent, '$700.00');
  assert.strictEqual(doc.querySelector('#stake-active-count').textContent, '85');
  assert.strictEqual(doc.querySelector('#stake-open-exposure').textContent, '$250.00');
  assert.strictEqual(doc.querySelector('#stake-token-masked').textContent, 'ecb294••••••••••••••••0407');

  // Check league breakdown table
  const tbody = doc.querySelector('#stake-league-tbody');
  assert.ok(tbody);
  assert.match(tbody.innerHTML, /NFL/);
  assert.match(tbody.innerHTML, /\$150\.00/);
  assert.match(tbody.innerHTML, /WTA/);
  assert.match(tbody.innerHTML, /\$100\.00/);
});

test('Stake Live Account refresh button reloads telemetry', async (t) => {
  const {w} = await bootStakeAccount(t);
  const doc = w.document;

  const navBtn = doc.querySelector('.nav-item[data-view="stake_account"]');
  navBtn.click();
  await new Promise(r => setTimeout(r, 40));

  const refreshBtn = doc.querySelector('#stake-account-refresh');
  assert.ok(refreshBtn, 'Refresh button should exist');
  refreshBtn.click();
  await new Promise(r => setTimeout(r, 40));

  assert.strictEqual(doc.querySelector('#stake-total-balance').textContent, '$950.00');
  assert.strictEqual(doc.querySelector('#stake-avail-balance').textContent, '$700.00');
});

test('Stake Live Account renders real Safari session with balances and username', async (t) => {
  const {w} = await bootStakeAccount(t);
  const doc = w.document;

  // Mock live synced response
  w.fetch = async (path) => {
    if (path.startsWith('/api/stake-account')) {
      return {
        ok: true,
        json: async () => ({
          status: 'ok',
          is_live_synced: true,
          sync_source: 'Live Safari Session (Stake.com)',
          username: 'blvrg',
          token_configured: true,
          token_masked: 'ecb294••••••••••••••••0407',
          total_balance: 711.61,
          available_balance: 441.95,
          open_exposure: 269.66,
          open_bets_count: 121,
          balances: {
            usdt: {available: 313.0059, vault: 0.0},
            usdc: {available: 128.9407, vault: 0.0},
            ltc: {available: 0.0001, vault: 0.0}
          },
          virtual_tracked: {
            open_bets_count: 426,
            open_exposure: 988.55,
            settled_bets_count: 1974,
            settled_pnl: -94.57
          }
        })
      };
    }
    return {ok: true, json: async () => ({})};
  };

  const navBtn = doc.querySelector('.nav-item[data-view="stake_account"]');
  navBtn.click();
  await new Promise(r => setTimeout(r, 40));

  assert.strictEqual(doc.querySelector('#stake-total-balance').textContent, '$711.61');
  assert.strictEqual(doc.querySelector('#stake-avail-balance').textContent, '$441.95');
  assert.strictEqual(doc.querySelector('#stake-active-count').textContent, '121');
  assert.strictEqual(doc.querySelector('#stake-open-exposure').textContent, '$269.66');
  assert.strictEqual(doc.querySelector('#stake-username-display').textContent, 'blvrg');
  assert.match(doc.querySelector('#stake-account-badge').textContent, /Live Synced \(blvrg\)/);
  assert.match(doc.querySelector('#stake-crypto-breakdown').innerHTML, /USDT/);
  assert.match(doc.querySelector('#stake-crypto-breakdown').innerHTML, /USDC/);
});

test('Stage Bet Slip button renders on actionable recommendation and triggers /api/stage-betslip', async (t) => {
  const future = new Date(Date.now() + 3600000).toISOString();
  const sampleRec = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future},
    decision_guard: {passed: true},
    id: 'tip-stage-1',
    rank: 1,
    league: 'NFL',
    game_id: 'g1',
    home: 'Chiefs',
    away: 'Bills',
    start: future,
    valid_until: future,
    market: 'moneyline',
    market_display: 'Winner (Incl. Overtime)',
    stake_market: {
      name: 'Winner (Incl. Overtime)',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-123',
      tab: 'Main'
    },
    selection: 'Chiefs',
    probability: 0.65,
    minimum_odds: 1.80,
    reference_odds: 1.95,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-123',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.50,
    stress_roi: 0.1,
    expected_roi: 0.2,
    label: 'Estimated Value',
    reasons: ['Edge detected'],
    warnings: [],
    game_context: {},
    model_version: 'test'
  };

  const {w, writes} = await bootStakeAccount(t, {recommendations: [sampleRec]});
  const doc = w.document;

  const stageBtn = doc.querySelector('.stage-betslip-btn');
  assert.ok(stageBtn, 'Stage Bet Slip button should exist on actionable card');
  assert.match(stageBtn.textContent, /Stage Bet Slip \(\$2\.50\)/);

  const oneClickBtn = doc.querySelector('.one-click-bet-btn');
  assert.ok(oneClickBtn, '1-Click Bet button should exist on actionable card');
  assert.match(oneClickBtn.textContent, /1-Click Bet \(\$2\.50\)/);

  // Click stage button
  stageBtn.click();
  await new Promise(r => setTimeout(r, 40));

  const postCall = writes.find(w => w.path === '/api/stage-betslip');
  assert.ok(postCall, 'Should have called /api/stage-betslip');
  const payload = JSON.parse(postCall.options.body);
  assert.strictEqual(payload.selection, 'Chiefs');
  assert.strictEqual(payload.stake, 2.50);
  assert.strictEqual(payload.event_url, 'https://stake.com/sports/american-football/nfl/chiefs-bills-123');

  // Click 1-Click Bet button
  oneClickBtn.click();
  await new Promise(r => setTimeout(r, 40));
  const autoPlaceCall = writes.filter(w => w.path === '/api/stage-betslip').pop();
  assert.ok(autoPlaceCall, 'Should have called /api/stage-betslip for 1-Click Bet');
  const autoPayload = JSON.parse(autoPlaceCall.options.body);
  assert.strictEqual(autoPayload.auto_place, true);
});

test('Stake Live Account view renders portfolio history chart and switches periods', async t => {
  const {w} = await bootStakeAccount(t);
  const doc = w.document;

  // Navigate to stake_account view
  const navBtn = doc.querySelector('.nav-item[data-view="stake_account"]');
  assert.ok(navBtn, 'Stake Live Account nav button should exist');
  navBtn.click();
  await new Promise(r => setTimeout(r, 60));

  const chart = doc.getElementById('stake-portfolio-chart');
  assert.ok(chart, 'Chart container should exist');
  const svg = chart.querySelector('svg');
  assert.ok(svg, 'SVG chart should be rendered');

  const currBal = doc.getElementById('stake-chart-curr-bal');
  assert.ok(currBal, 'Current balance metric should exist');
  assert.strictEqual(currBal.textContent, '$724.87');

  const netChange = doc.getElementById('stake-chart-net-change');
  assert.ok(netChange, 'Net change metric should exist');
  assert.match(netChange.textContent, /-\$72\.05/);

  const points = chart.querySelectorAll('circle.statistics-pnl-point');
  assert.strictEqual(points.length, 5, 'Should render 5 points in chart SVG');

  // Test period tabs
  const tab24h = doc.querySelector('#stake-chart-tabs button[data-period="24h"]');
  assert.ok(tab24h, '24h tab button should exist');
  tab24h.click();
  await new Promise(r => setTimeout(r, 40));
  assert.ok(tab24h.classList.contains('selected'), '24h tab should have selected class');
});

test('Auto 1-Click Bet Runner iterates through actionable tips, stages, auto-places and marks them placed', async t => {
  const future1 = new Date(Date.now() + 3600000).toISOString();
  const future2 = new Date(Date.now() + 7200000).toISOString();
  const sampleRec1 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future1},
    decision_guard: {passed: true},
    id: 'tip-auto-1',
    rank: 1,
    game_id: 'game-auto-1',
    league: 'NFL',
    home: 'Bills',
    away: 'Chiefs',
    market: 'moneyline',
    market_display: 'Winner',
    start: future1,
    valid_until: future1,
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-123',
      tab: 'Main'
    },
    selection: 'Chiefs',
    probability: 0.65,
    minimum_odds: 1.80,
    reference_odds: 1.95,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-123',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.50,
    stress_roi: 0.1,
    expected_roi: 0.2,
    label: 'Estimated Value',
    reasons: ['Edge detected'],
    warnings: [],
    game_context: {},
    model_version: 'test'
  };

  const sampleRec2 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future2},
    decision_guard: {passed: true},
    id: 'tip-auto-2',
    rank: 2,
    game_id: 'game-auto-2',
    league: 'ATP',
    home: 'Alcaraz',
    away: 'Sinner',
    market: 'moneyline',
    market_display: 'Winner',
    start: future2,
    valid_until: future2,
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/tennis/atp/alcaraz-sinner-456',
      tab: 'Main'
    },
    selection: 'Alcaraz',
    probability: 0.60,
    minimum_odds: 1.75,
    reference_odds: 1.88,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/tennis/atp/alcaraz-sinner-456',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 3.00,
    stress_roi: 0.08,
    expected_roi: 0.15,
    label: 'Estimated Value',
    reasons: ['Model edge'],
    warnings: [],
    game_context: {},
    model_version: 'test'
  };

  const {w, writes} = await bootStakeAccount(t, {recommendations: [sampleRec1, sampleRec2]});
  const doc = w.document;

  const panel = doc.getElementById('auto-bet-panel');
  assert.ok(panel, 'Auto-bet panel should exist in DOM');

  const startBtn = doc.getElementById('auto-bet-start-btn');
  const countBadge = doc.getElementById('auto-bet-queue-count');

  assert.ok(startBtn, 'Auto-bet start button should exist');
  assert.strictEqual(countBadge.textContent, '2', 'Should have 2 actionable bets in queue');

  // Trigger auto-bet queue execution
  startBtn.click();
  await new Promise(r => setTimeout(r, 120));

  // Verify that /api/stage-betslip was called twice with auto_place: true
  const stageCalls = writes.filter(w => w.path === '/api/stage-betslip');
  assert.strictEqual(stageCalls.length, 2, 'Should have called /api/stage-betslip for both tips');

  const body1 = JSON.parse(stageCalls[0].options.body);
  assert.strictEqual(body1.tip_id, 'tip-auto-1');
  assert.strictEqual(body1.auto_place, true);

  const body2 = JSON.parse(stageCalls[1].options.body);
  assert.strictEqual(body2.tip_id, 'tip-auto-2');
  assert.strictEqual(body2.auto_place, true);

  // Verify that checklist was called to mark them placed
  const checklistCalls = writes.filter(w => w.path.includes('/checklist'));
  assert.strictEqual(checklistCalls.length, 2, 'Should have marked both tips as placed');
  assert.strictEqual(JSON.parse(checklistCalls[0].options.body).placed, true);
  assert.strictEqual(JSON.parse(checklistCalls[1].options.body).placed, true);

  // Queue should now be empty (0)
  assert.strictEqual(countBadge.textContent, '0', 'Queue should be 0 after auto-placing both');
  assert.strictEqual(startBtn.disabled, true, 'Start button should be disabled when queue is empty');
});

test('Auto 1-Click Bet Runner halts cleanly when stop button is pressed', async t => {
  const future1 = new Date(Date.now() + 3600000).toISOString();
  const future2 = new Date(Date.now() + 7200000).toISOString();
  const rec1 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future1},
    decision_guard: {passed: true},
    id: 'tip-stop-1',
    rank: 1,
    game_id: 'g-stop-1',
    league: 'NFL',
    home: 'Bills',
    away: 'Chiefs',
    market: 'moneyline',
    market_display: 'Winner',
    start: future1,
    valid_until: future1,
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      event_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-123',
      tab: 'Main'
    },
    selection: 'Chiefs',
    probability: 0.65,
    minimum_odds: 1.80,
    reference_odds: 1.95,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-123',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.50,
    expected_roi: 0.2,
    reasons: ['Edge detected'],
    warnings: [],
    model_version: 'test'
  };

  const rec2 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future2},
    decision_guard: {passed: true},
    id: 'tip-stop-2',
    rank: 2,
    game_id: 'g-stop-2',
    league: 'ATP',
    home: 'Alcaraz',
    away: 'Sinner',
    market: 'moneyline',
    market_display: 'Winner',
    start: future2,
    valid_until: future2,
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      event_url: 'https://stake.com/sports/tennis/atp/alcaraz-sinner-456',
      tab: 'Main'
    },
    selection: 'Alcaraz',
    probability: 0.60,
    minimum_odds: 1.75,
    reference_odds: 1.88,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/tennis/atp/alcaraz-sinner-456',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 3.00,
    expected_roi: 0.15,
    reasons: ['Model edge'],
    warnings: [],
    model_version: 'test'
  };

  const {w, writes} = await bootStakeAccount(t, {recommendations: [rec1, rec2]});
  const doc = w.document;

  const startBtn = doc.getElementById('auto-bet-start-btn');
  const stopBtn = doc.getElementById('auto-bet-stop-btn');

  // Start auto-bet
  startBtn.click();
  // Immediately request stop
  stopBtn.click();
  await new Promise(r => setTimeout(r, 120));

  // Verify only the first one was placed or stopped before second
  const stageCalls = writes.filter(w => w.path === '/api/stage-betslip');
  assert.ok(stageCalls.length <= 1, 'Should have stopped before processing both tips');
});

test('Auto 1-Click Bet Runner halts immediately and marks nothing when 300 active bets limit error occurs', async t => {
  const future1 = new Date(Date.now() + 3600000).toISOString();
  const future2 = new Date(Date.now() + 7200000).toISOString();
  const rec1 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future1},
    decision_guard: {passed: true},
    id: 'tip-limit-1',
    rank: 1,
    game_id: 'g-limit-1',
    league: 'NFL',
    home: 'Bills',
    away: 'Chiefs',
    market: 'moneyline',
    market_display: 'Winner',
    start: future1,
    valid_until: future1,
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-1',
      tab: 'Main'
    },
    selection: 'Chiefs',
    probability: 0.65,
    minimum_odds: 1.80,
    reference_odds: 1.95,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-1',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.50,
    stress_roi: 0.1,
    expected_roi: 0.2,
    reasons: ['Edge detected'],
    warnings: [],
    model_version: 'test'
  };
  const rec2 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future2},
    decision_guard: {passed: true},
    id: 'tip-limit-2',
    rank: 2,
    game_id: 'g-limit-2',
    league: 'NFL',
    home: 'Eagles',
    away: 'Cowboys',
    market: 'moneyline',
    market_display: 'Winner',
    start: future2,
    valid_until: future2,
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/american-football/nfl/cowboys-eagles-2',
      tab: 'Main'
    },
    selection: 'Eagles',
    probability: 0.65,
    minimum_odds: 1.80,
    reference_odds: 1.95,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/cowboys-eagles-2',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.50,
    stress_roi: 0.1,
    expected_roi: 0.2,
    reasons: ['Edge detected'],
    warnings: [],
    model_version: 'test'
  };

  const {w, writes} = await bootStakeAccount(t, {recommendations: [rec1, rec2]});
  const origFetch = w.fetch;
  w.fetch = async (path, options) => {
    if (path.startsWith('/api/stage-betslip')) {
      return {
        ok: false,
        status: 400,
        json: async () => ({
          status: 'error',
          placed: false,
          limit_reached: true,
          message: 'Elérted a Stake maximális aktív fogadási limitjét (max 300 bet)!'
        })
      };
    }
    return origFetch(path, options);
  };

  const doc = w.document;
  const startBtn = doc.getElementById('auto-bet-start-btn');
  startBtn.click();
  await new Promise(r => setTimeout(r, 60));

  // Runner must halt on first bet!
  const descEl = doc.getElementById('auto-bet-desc');
  assert.ok(descEl);
  assert.match(descEl.innerHTML, /300 bet/);

  // Must NOT mark tip as placed
  const checklistCalls = writes.filter(w => w.path.includes('/checklist'));
  assert.strictEqual(checklistCalls.length, 0, 'No bets should be marked as placed when limit error occurs');
});

test('Auto 1-Click Bet Runner blocks start when account already has 300 open bets', async t => {
  const future1 = new Date(Date.now() + 3600000).toISOString();
  const rec1 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future1},
    decision_guard: {passed: true},
    id: 'tip-block-1',
    rank: 1,
    game_id: 'g-block-1',
    league: 'NFL',
    home: 'Bills',
    away: 'Chiefs',
    market: 'moneyline',
    start: future1,
    valid_until: future1,
    stake_market: {name: 'Winner', availability_verified: true, event_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-1', tab: 'Main'},
    selection: 'Chiefs',
    probability: 0.65,
    reference_odds: 1.95,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-1',
    quote_at: new Date().toISOString(),
    stake: 2.50,
  };

  const {w, writes} = await bootStakeAccount(t, {
    recommendations: [rec1],
    stake_account: {open_bets_count: 300, total_balance: 500}
  });

  const doc = w.document;
  const startBtn = doc.getElementById('auto-bet-start-btn');
  assert.strictEqual(startBtn.disabled, true, 'Start button should be disabled when open_bets_count is 300');

  // Attempt click anyway
  startBtn.click();
  await new Promise(r => setTimeout(r, 40));

  const stageCalls = writes.filter(w => w.path === '/api/stage-betslip');
  assert.strictEqual(stageCalls.length, 0, 'Should not attempt any stage-betslip call when 300 limit is reached');
});

test('Auto 1-Click Bet Runner skips changed market, continues queue, and displays manual inspection section', async t => {
  const future1 = new Date(Date.now() + 3600000).toISOString();
  const future2 = new Date(Date.now() + 7200000).toISOString();
  const rec1 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future1},
    decision_guard: {passed: true},
    id: 'tip-skip-1',
    rank: 1,
    game_id: 'game-skip-1',
    league: 'NHL',
    home: 'Canucks',
    away: 'Lightning',
    market: 'totals',
    market_display: 'Total Goals',
    start: future1,
    valid_until: future1,
    stake_market: {
      name: 'Total Goals',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/ice-hockey/nhl/lightning-canucks-123',
      tab: 'Main'
    },
    selection: 'Under 3.5',
    line: 3.5,
    probability: 0.58,
    minimum_odds: 1.80,
    reference_odds: 1.95,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/ice-hockey/nhl/lightning-canucks-123',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.50,
    stress_roi: 0.1,
    expected_roi: 0.2,
    label: 'Estimated Value',
    reasons: ['Edge detected'],
    warnings: [],
    game_context: {},
    model_version: 'test'
  };

  const rec2 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future2},
    decision_guard: {passed: true},
    id: 'tip-skip-2',
    rank: 2,
    game_id: 'game-skip-2',
    league: 'ATP',
    home: 'Alcaraz',
    away: 'Sinner',
    market: 'moneyline',
    market_display: 'Winner',
    start: future2,
    valid_until: future2,
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/tennis/atp/alcaraz-sinner-456',
      tab: 'Main'
    },
    selection: 'Alcaraz',
    probability: 0.60,
    minimum_odds: 1.75,
    reference_odds: 1.88,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/tennis/atp/alcaraz-sinner-456',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 3.00,
    stress_roi: 0.08,
    expected_roi: 0.15,
    label: 'Estimated Value',
    reasons: ['Model edge'],
    warnings: [],
    game_context: {},
    model_version: 'test'
  };

  const {w, writes} = await bootStakeAccount(t, {recommendations: [rec1, rec2]});

  const origFetch = w.fetch;
  w.fetch = async (path, options) => {
    if (path.startsWith('/api/stage-betslip')) {
      const body = JSON.parse(options.body || '{}');
      if (body.tip_id === 'tip-skip-1') {
        return {
          ok: false,
          status: 400,
          json: async () => ({
            status: 'error',
            placed: false,
            message: 'A fogadóiroda módosította a piacot. A Stake kínálatában a kért vonal (under 3.5) helyett jelenleg ezek a vonalak érhetők el: Under 5.5, Under 6.5.'
          })
        };
      }
      if (body.tip_id === 'tip-skip-2') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ok',
            placed: true,
            stake: 3.00,
            selection: 'Alcaraz'
          })
        };
      }
    }
    return origFetch(path, options);
  };

  const doc = w.document;
  const startBtn = doc.getElementById('auto-bet-start-btn');
  startBtn.click();
  await new Promise(r => setTimeout(r, 120));

  // Verify that tip 2 WAS placed
  const checklistCalls = writes.filter(w => w.path.includes('/checklist'));
  assert.strictEqual(checklistCalls.length, 1, 'Only tip 2 should be marked placed');
  assert.strictEqual(checklistCalls[0].path.includes('tip-skip-2'), true);

  // Verify that tip 1 button reflects skipped state
  const btn1 = doc.querySelector('.one-click-bet-btn[data-tip-id="tip-skip-1"]');
  assert.ok(btn1);
  assert.strictEqual(btn1.classList.contains('is-skipped'), true, 'Tip 1 button should have is-skipped class');
  assert.match(btn1.textContent, /Kihagyva/);

  // Verify that skipped section is displayed with details
  const skippedSection = doc.getElementById('auto-bet-skipped-section');
  assert.ok(skippedSection);
  assert.strictEqual(skippedSection.style.display, 'block');
  assert.match(skippedSection.innerHTML, /Canucks/);
  assert.match(skippedSection.innerHTML, /Under 3\.5/);
  assert.match(skippedSection.innerHTML, /A fogadóiroda módosította a piacot/);
});

test('Auto-Run 30m toggle switches to 24h horizon, triggers auto-bet, and enforces 300 open bets limit', async t => {
  const future1 = new Date(Date.now() + 3600000).toISOString();
  const future2 = new Date(Date.now() + 7200000).toISOString();
  const rec1 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future1},
    decision_guard: {passed: true},
    id: 'tip-autorun-1',
    rank: 1,
    game_id: 'g-autorun-1',
    start: future1,
    valid_until: future1,
    home: 'Bills',
    away: 'Chiefs',
    league: 'NFL',
    selection: 'Chiefs',
    market: 'moneyline',
    market_display: 'Winner',
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-123',
      tab: 'Main'
    },
    line: 0,
    reference_odds: 1.95,
    minimum_odds: 1.80,
    probability: 0.65,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-123',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.50,
    allocated_stake: 2.50,
    stress_roi: 0.1,
    expected_roi: 0.2,
    label: 'Estimated Value',
    reasons: ['Edge detected'],
    warnings: [],
    game_context: {},
    model_version: 'test'
  };
  const rec2 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future2},
    decision_guard: {passed: true},
    id: 'tip-autorun-2',
    rank: 2,
    game_id: 'g-autorun-2',
    start: future2,
    valid_until: future2,
    home: 'Alcaraz',
    away: 'Sinner',
    league: 'ATP',
    selection: 'Alcaraz',
    market: 'moneyline',
    market_display: 'Winner',
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      availability_note: 'Exact current Stake offer verified.',
      event_url: 'https://stake.com/sports/tennis/atp/alcaraz-sinner-456',
      tab: 'Main'
    },
    line: 0,
    reference_odds: 1.88,
    minimum_odds: 1.75,
    probability: 0.60,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/tennis/atp/alcaraz-sinner-456',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 3.00,
    allocated_stake: 3.00,
    stress_roi: 0.08,
    expected_roi: 0.15,
    label: 'Estimated Value',
    reasons: ['Model edge'],
    warnings: [],
    game_context: {},
    model_version: 'test'
  };

  const {w, writes} = await bootStakeAccount(t, {
    recommendations: [rec1, rec2],
    stake_account: {open_bets_count: 50}
  });
  const doc = w.document;

  const toggle = doc.getElementById('auto-bet-interval-toggle');
  const statusEl = doc.getElementById('auto-bet-interval-status');
  const stopBtn = doc.getElementById('auto-bet-stop-btn');

  assert.ok(toggle, 'Auto-run 30m toggle should exist in DOM');
  assert.ok(statusEl, 'Auto-run status element should exist in DOM');
  assert.strictEqual(toggle.checked, false, 'Toggle should be unchecked by default');
  assert.strictEqual(statusEl.style.display, 'none', 'Status should be hidden by default');

  // Trigger enabling Auto-Run 30m
  toggle.checked = true;
  toggle.onchange();
  await new Promise(r => setTimeout(r, 120));

  // Should have triggered /api/stage-betslip calls for actionable picks
  const stageCalls = writes.filter(w => w.path === '/api/stage-betslip');
  assert.ok(stageCalls.length >= 1, 'Should have triggered bets execution upon enabling');

  // Stop button should cancel auto-run and uncheck toggle
  stopBtn.click();
  assert.strictEqual(toggle.checked, false, 'Stop button must disable and uncheck auto-run toggle');

  // Now test 300 open bets limit guard:
  const {w: wLimit, writes: writesLimit} = await bootStakeAccount(t, {
    recommendations: [rec1],
    stake_account: {open_bets_count: 300}
  });
  const docLimit = wLimit.document;
  const toggleLimit = docLimit.getElementById('auto-bet-interval-toggle');
  assert.ok(toggleLimit);

  // When Stake account has 300 open bets, enabling Auto-Run stays active and waits for capacity, without placing bets
  toggleLimit.checked = true;
  toggleLimit.onchange();
  assert.strictEqual(toggleLimit.checked, true, 'Auto-Run 15m must remain active and wait for capacity when at 300 bets');
  const stageCallsAtLimit = writesLimit.filter(w => w.path === '/api/stage-betslip');
  assert.strictEqual(stageCallsAtLimit.length, 0, 'Must not place any bets when at 300 active bets limit');
});

test('Auto 1-Click Bet Runner autonomously executes Phase 2 adaptive fallback on moved line and places qualifying +EV alternative', async t => {
  const future1 = new Date(Date.now() + 3600000).toISOString();
  const rec1 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future1},
    decision_guard: {passed: true},
    id: 'tip-adapt-1',
    rank: 1,
    game_id: 'game-adapt-1',
    league: 'NBA',
    home: '76ers',
    away: 'Pistons',
    market: 'totals',
    market_display: 'Total (Incl. Overtime)',
    start: future1,
    valid_until: future1,
    stake_market: {
      name: 'Total (Incl. Overtime)',
      availability_verified: true,
      event_url: 'https://stake.com/sports/basketball/usa/nba/76ers-pistons',
      tab: 'Main'
    },
    selection: 'Under 223.5',
    line: 223.5,
    probability: 0.54,
    minimum_odds: 1.85,
    reference_odds: 2.11,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/basketball/usa/nba/76ers-pistons',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 1.00,
    stress_roi: 0.1,
    expected_roi: 0.14,
    label: 'Estimated Value',
    reasons: ['Edge detected'],
    warnings: [],
    game_context: {},
    model_version: 'test'
  };

  const {w, writes} = await bootStakeAccount(t, {recommendations: [rec1]});

  const origFetch = w.fetch;
  let callCount = 0;
  w.fetch = async (path, options) => {
    if (path.startsWith('/api/stage-betslip')) {
      const body = JSON.parse(options.body || '{}');
      callCount++;
      // First pass: line moved, stage-betslip returns error with available candidates
      if (!body.adaptive_fallback) {
        return {
          ok: false,
          status: 400,
          json: async () => ({
            status: 'error',
            placed: false,
            message: 'A fogadóiroda módosította a piacot. A Stake kínálatában a kért vonal (under 223.5) helyett jelenleg ezek a vonalak érhetők el: Under 224.5, Under 225.5.',
            available_candidates: [{selection: 'Under 224.5', line: 224.5, odds: 2.19}]
          })
        };
      }
      // Second pass: adaptive_fallback is true -> returns successful placement!
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'ok',
          placed: true,
          adapted: true,
          original_selection: 'Under 223.5',
          selection: 'Under 224.5',
          odds: 2.19,
          stake: 1.00,
          ev: 0.2325,
          message: 'Adaptívan megkötve: Under 224.5 @ 2.19 (Eredeti: Under 223.5) · EV: +23.3%'
        })
      };
    }
    return origFetch(path, options);
  };

  const doc = w.document;
  const startBtn = doc.getElementById('auto-bet-start-btn');
  startBtn.click();
  await new Promise(r => setTimeout(r, 180));

  // Phase 1 skipped, then Phase 2 placed adaptively -> 2 stage-betslip calls
  assert.strictEqual(callCount, 2, 'Should invoke stage-betslip in Phase 1 and Phase 2');

  // Tip should be marked placed in checklist
  const checklistCalls = writes.filter(w => w.path.includes('/checklist') && w.path.includes('tip-adapt-1'));
  assert.strictEqual(checklistCalls.length, 1, 'Adapted tip should be marked placed in checklist');
  assert.strictEqual(JSON.parse(checklistCalls[0].options.body).placed, true);

  // Queue count should be 0 after adaptive placement
  const countBadge = doc.getElementById('auto-bet-queue-count');
  assert.strictEqual(countBadge.textContent, '0', 'Queue should be 0 after adaptive resolution');

  // Skipped box should be empty or hidden because it was resolved
  const skippedBox = doc.getElementById('auto-bet-skipped-section');
  assert.ok(skippedBox);
  assert.strictEqual(skippedBox.style.display, 'none', 'Skipped section should be hidden after adaptive resolution');
});

test('Auto-Run 15m hierarchical queuing prioritizes 24h horizon first, then 48h if slots remain', async t => {
  const now = Date.now();
  const time10h = new Date(now + 10 * 3600000).toISOString();
  const time30h = new Date(now + 30 * 3600000).toISOString();

  const rec24h = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: time10h},
    decision_guard: {passed: true},
    id: 'tip-hier-24h',
    rank: 1,
    game_id: 'g-24h',
    league: 'NFL',
    home: 'Falcons',
    away: 'Saints',
    market: 'spreads',
    market_display: 'Handicap',
    line: -3.5,
    start: time10h,
    valid_until: time10h,
    stake_market: {
      name: 'Handicap',
      availability_verified: true,
      availability_note: 'Verified.',
      event_url: 'https://stake.com/sports/american-football/nfl/saints-falcons',
      tab: 'Main'
    },
    selection: 'Falcons -3.5',
    probability: 0.60,
    minimum_odds: 1.85,
    reference_odds: 1.95,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/saints-falcons',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.00,
    stress_roi: 0.1,
    expected_roi: 0.2,
    reasons: ['24h pick'],
    warnings: [],
    model_version: 'test'
  };

  const rec48h = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: time30h},
    decision_guard: {passed: true},
    id: 'tip-hier-48h',
    rank: 2,
    game_id: 'g-48h',
    league: 'NFL',
    home: 'Packers',
    away: 'Bears',
    market: 'totals',
    market_display: 'Total',
    line: 44.5,
    start: time30h,
    valid_until: time30h,
    stake_market: {
      name: 'Total',
      availability_verified: true,
      availability_note: 'Verified.',
      event_url: 'https://stake.com/sports/american-football/nfl/bears-packers',
      tab: 'Main'
    },
    selection: 'Over 44.5',
    probability: 0.58,
    minimum_odds: 1.80,
    reference_odds: 1.90,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/bears-packers',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 1.50,
    stress_roi: 0.1,
    expected_roi: 0.18,
    reasons: ['48h pick'],
    warnings: [],
    model_version: 'test'
  };

  const {w, writes} = await bootStakeAccount(t, {
    recommendations: [rec24h, rec48h],
    stake_account: {open_bets_count: 100}
  });

  const doc = w.document;
  const toggle = doc.getElementById('auto-bet-interval-toggle');
  assert.ok(toggle);

  // Turn on Auto-Run 15m
  toggle.checked = true;
  toggle.onchange();
  await new Promise(r => setTimeout(r, 150));

  const stageCalls = writes.filter(w => w.path === '/api/stage-betslip');
  // Both 24h and 48h should be queued because slots (300 - 100 = 200) > 0 and 24h was fulfilled first
  assert.strictEqual(stageCalls.length, 2, 'Both 24h and 48h bets should be placed when slots remain');
  assert.strictEqual(JSON.parse(stageCalls[0].options.body).tip_id, 'tip-hier-24h', '24h tip must be submitted first');
  assert.strictEqual(JSON.parse(stageCalls[1].options.body).tip_id, 'tip-hier-48h', '48h tip must be submitted second');
});

test('Auto-Run 15m halts and marks fatal error on genuine system error (e.g. login required)', async t => {
  const future1 = new Date(Date.now() + 3600000).toISOString();
  const rec1 = {
    execution_review: {actionable: true, quote_at: new Date().toISOString(), valid_until: future1},
    decision_guard: {passed: true},
    id: 'tip-fatal-1',
    rank: 1,
    game_id: 'g-fatal-1',
    league: 'NFL',
    home: 'Bills',
    away: 'Chiefs',
    market: 'moneyline',
    market_display: 'Winner',
    start: future1,
    valid_until: future1,
    stake_market: {
      name: 'Winner',
      availability_verified: true,
      availability_note: 'Verified.',
      event_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-fatal',
      tab: 'Main'
    },
    selection: 'Chiefs',
    probability: 0.65,
    minimum_odds: 1.80,
    reference_odds: 1.95,
    reference_price: 0.5,
    reference_platform: 'Stake.com',
    reference_url: 'https://stake.com/sports/american-football/nfl/chiefs-bills-fatal',
    source_url: 'https://example.com',
    quote_at: new Date().toISOString(),
    stake: 2.50,
    stress_roi: 0.1,
    expected_roi: 0.2,
    reasons: ['Edge detected'],
    warnings: [],
    model_version: 'test'
  };

  const {w} = await bootStakeAccount(t, {recommendations: [rec1]});
  const origFetch = w.fetch;
  w.fetch = async (path, options) => {
    if (path.startsWith('/api/stage-betslip')) {
      return {
        ok: false,
        status: 401,
        json: async () => ({
          status: 'error',
          placed: false,
          message: 'Bejelentkezés szükséges a Stake fiókodba a Safariban!'
        })
      };
    }
    return origFetch(path, options);
  };

  const doc = w.document;
  const toggle = doc.getElementById('auto-bet-interval-toggle');
  assert.ok(toggle);

  toggle.checked = true;
  toggle.onchange();
  await new Promise(r => setTimeout(r, 120));

  // Toggle must be disabled / unchecked on genuine fatal error
  assert.strictEqual(toggle.checked, false, 'Auto-Run 15m must uncheck on genuine fatal error');

  const descEl = doc.getElementById('auto-bet-desc');
  assert.ok(descEl);
  assert.match(descEl.innerHTML, /Auto-Run queued bets leállt hiba miatt/);
  assert.match(descEl.innerHTML, /Bejelentkezés szükséges/);
});






