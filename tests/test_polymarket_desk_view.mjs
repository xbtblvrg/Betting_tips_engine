import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';

const html = readFileSync('src/betting_engine/web/index.html', 'utf8');
const script = readFileSync('src/betting_engine/web/app.js', 'utf8');

async function bootPolymarketDesk(t) {
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
    if (path === '/api/polymarket/trader/status') {
      return {
        ok: true,
        json: async () => ({
          channel: 'polymarket_autonomous_parallel_executor',
          active: true,
          dry_run: false,
          authenticated: true,
          builder_active: true,
          builder_key_configured: true,
          relayer_configured: true,
          supported_order_types: ['LIMIT_GTC', 'LIMIT_FOK', 'LIMIT_FAK', 'MARKET_FOK'],
          proxy_address: '0xee888fa7b96007f7fa270988e92bddb0ae19ed10',
          total_orders: 4,
          open_orders: 1,
          filled_orders: 3,
          rejected_orders: 0,
          realized_pnl: 14.50,
          collateral_balance_usdc: 0.00,
          shadow_trading: {
            active: true,
            status: 'ACTIVE_FORWARD_TESTING',
            label: 'Live CLOB Paper Trading',
            bets_count: 3,
            exposure_usd: 12.50,
            realized_pnl: 8.20,
            settled_count: 5,
            win_rate: 0.60,
          },
          live_trading: {
            active: false,
            status: 'STANDBY_LOW_COLLATERAL',
            label: 'Polygon On-Chain Execution',
            cash_balance_usdc: 0.00,
            bets_count: 0,
            exposure_usd: 0.00,
            realized_pnl: 0.00,
            settled_count: 0,
            win_rate: 0.0,
            proxy_address: '0xee888fa7b96007f7fa270988e92bddb0ae19ed10',
          },
          updated_at: '2026-11-01T14:00:00Z',
        }),
      };
    }
    if (path === '/api/polymarket/reset-shadow') {
      return {
        ok: true,
        json: async () => ({
          ok: true,
          deleted_count: 5,
          message: 'Shadow portfolio successfully reset.'
        }),
      };
    }
    if (path.startsWith('/api/polymarket/trader/orders')) {
      return {
        ok: true,
        json: async () => ({
          count: 1,
          orders: [
            {
              order_id: 'poly_ord_123',
              client_order_id: 'poly_nfl_tot_47',
              token_id: 'tok_nfl_tot_over_47',
              league: 'NFL',
              game_title: 'Chiefs vs Bills',
              market_family: 'totals',
              selection: 'Over 47.5',
              poly_price: 0.48,
              poly_odds: 2.08,
              expected_value: 0.12,
              stake_usd: 5.00,
              shares_count: 10.4,
              status: 'filled',
              tx_hash: '0xabc123789',
              pnl: null,
              created_at: '2026-11-01T14:00:00Z',
            }
          ],
        }),
      };
    }
    if (path === '/api/polymarket/rd-priorities') {
      return {
        ok: true,
        json: async () => ({
          proven_active_count: 1,
          unproven_gaps_count: 2,
          all_reachable_markets: [
            {
              segment_key: 'NFL|full_game|totals',
              league: 'NFL',
              scope: 'full_game',
              market_family: 'totals',
              sample_event: 'Chiefs vs Bills',
              rolling_games: 10,
              live_games: 10,
              games_needed: 0,
              rolling_pnl: 2.45,
              rolling_roi_pct: 28.8,
              rolling_units: 3.17,
              tier_status: 'TIER_4_PROVEN',
              proximity_score: 1035.2,
              stake_cap: 3.50,
            },
            {
              segment_key: 'EPL|full_game|totals',
              league: 'EPL',
              scope: 'full_game',
              market_family: 'totals',
              sample_event: 'Arsenal vs Chelsea',
              rolling_games: 8,
              live_games: 8,
              games_needed: 2,
              rolling_pnl: 1.59,
              rolling_roi_pct: 24.5,
              rolling_units: 3.18,
              conditions_met_count: 2,
              tier_status: 'APPROACHING_TIER_4',
              proximity_score: 824.5,
              stake_cap: 0.00,
            },
            {
              segment_key: 'NHL|full_game|moneyline',
              league: 'NHL',
              scope: 'full_game',
              market_family: 'moneyline',
              sample_event: 'Jets vs Hurricanes',
              rolling_games: 0,
              live_games: 0,
              games_needed: 10,
              rolling_pnl: 0.00,
              rolling_roi_pct: 0.0,
              rolling_units: 0.00,
              tier_status: 'TIER_2_INCUBATING',
              proximity_score: 50.0,
              stake_cap: 0.00,
            }
          ],
          proven_markets: [
            {
              segment_key: 'NFL|full_game|totals',
              league: 'NFL',
              scope: 'full_game',
              market_family: 'totals',
              sample_event: 'Chiefs vs Bills',
              rolling_games: 10,
              live_games: 10,
              games_needed: 0,
              rolling_pnl: 2.45,
              rolling_roi_pct: 28.8,
              rolling_units: 3.17,
              tier_status: 'TIER_4_PROVEN',
              proximity_score: 1035.2,
              stake_cap: 3.50,
            }
          ],
          unproven_gaps: [
            {
              segment_key: 'EPL|full_game|totals',
              league: 'EPL',
              scope: 'full_game',
              market_family: 'totals',
              sample_event: 'Arsenal vs Chelsea',
              rolling_games: 8,
              live_games: 8,
              games_needed: 2,
              rolling_pnl: 1.59,
              rolling_roi_pct: 24.5,
              rolling_units: 3.18,
              conditions_met_count: 2,
              tier_status: 'APPROACHING_TIER_4',
              proximity_score: 824.5,
              stake_cap: 0.00,
            },
            {
              segment_key: 'NHL|full_game|moneyline',
              league: 'NHL',
              scope: 'full_game',
              market_family: 'moneyline',
              sample_event: 'Jets vs Hurricanes',
              rolling_games: 0,
              live_games: 0,
              games_needed: 10,
              rolling_pnl: 0.00,
              rolling_roi_pct: 0.0,
              rolling_units: 0.00,
              tier_status: 'TIER_2_INCUBATING',
              proximity_score: 50.0,
              stake_cap: 0.00,
            }
          ],
        }),
      };
    }
    if (path === '/api/polymarket/runner/trigger') {
      return {
        ok: true,
        json: async () => ({
          orders_placed_count: 1,
          settlement: { settled_count: 0 },
        }),
      };
    }
    if (path === '/api/polymarket/redeem') {
      return {
        ok: true,
        json: async () => ({
          redeemable_count: 2,
        }),
      };
    }
    // Default dashboard mock
    return {
      ok: true,
      json: async () => ({
        generated_at: new Date().toISOString(),
        settings: { auto_track_recommendations: false },
        status: { daily_progress: { issued: 10, target: 100, distinct_games: 5, shortfall: 90, date: '2026-11-01', timezone: 'Europe/Budapest' } },
        tips: [],
      }),
    };
  };

  const d = w.document;
  w.eval(script);
  // Wait for initial async tasks
  await new Promise(r => setTimeout(r, 60));
  return { w, d, writes };
}

test('Polymarket Desk tab button navigates to view-polymarket and loads telemetry', async (t) => {
  const { d, w } = await bootPolymarketDesk(t);

  const polyNavBtn = d.querySelector('.nav-item[data-view="polymarket"]');
  assert.ok(polyNavBtn, 'Polymarket nav item must exist in sidebar');

  // Click nav item
  polyNavBtn.click();
  await new Promise(r => setTimeout(r, 60));

  const viewPoly = d.querySelector('#view-polymarket');
  assert.equal(viewPoly.hidden, false, 'view-polymarket must become visible');

  // Check telemetry values
  const balEl = d.querySelector('#poly-usdc-balance');
  assert.equal(balEl.textContent, '$0.00');

  const pnlEl = d.querySelector('#poly-realized-pnl');
  assert.equal(pnlEl.textContent, '+$0.00');

  // Check separate Shadow Desk telemetry
  const shadowPnlEl = d.querySelector('#poly-shadow-pnl');
  assert.equal(shadowPnlEl.textContent, '+$8.20');

  const shadowBetsEl = d.querySelector('#poly-shadow-bets-count');
  assert.equal(shadowBetsEl.textContent, '3');

  const shadowExpEl = d.querySelector('#poly-shadow-exposure');
  assert.equal(shadowExpEl.textContent, '$12.50');

  const shadowWrEl = d.querySelector('#poly-shadow-winrate');
  assert.match(shadowWrEl.textContent, /60\.0%/);

  const shadowBadge = d.querySelector('#poly-shadow-status-badge');
  assert.match(shadowBadge.textContent, /SHADOW ACTIVE/);

  // Check separate Live Desk telemetry
  const liveStatusBadge = d.querySelector('#poly-live-status-badge');
  assert.match(liveStatusBadge.textContent, /STANDBY/);

  const liveBetsEl = d.querySelector('#poly-live-bets-count');
  assert.equal(liveBetsEl.textContent, '0');

  const liveExpEl = d.querySelector('#poly-live-exposure');
  assert.equal(liveExpEl.textContent, '$0.00');

  const ordersEl = d.querySelector('#poly-total-orders');
  assert.equal(ordersEl.textContent, '4');

  const builderBadge = d.querySelector('#poly-builder-badge');
  assert.match(builderBadge.textContent, /Builder SDK: Active/);

  // Check low collateral alert
  const alertEl = d.querySelector('#poly-funding-alert');
  assert.equal(alertEl.style.display, 'block');
  assert.match(alertEl.textContent, /Low USDC Balance/);
  assert.match(alertEl.textContent, /0xee888fa7b96007f7fa270988e92bddb0ae19ed10/);

  // Check orders table with Mode badge
  const tbody = d.querySelector('#poly-orders-tbody');
  assert.match(tbody.textContent, /LIVE/);
  assert.match(tbody.textContent, /Chiefs vs Bills/);
  assert.match(tbody.textContent, /Over 47.5/);
  assert.match(tbody.textContent, /\$5\.00/);

  // Check matrix table
  const mbody = d.querySelector('#poly-matrix-tbody');
  assert.match(mbody.textContent, /NFL\|full_game\|totals/);
  assert.match(mbody.textContent, /Tier 3 Proven/);
  assert.match(mbody.textContent, /EPL\|full_game\|totals/);
  assert.match(mbody.textContent, /Approaching Tier 4/);
  assert.match(mbody.textContent, /2 more games needed/);
  assert.match(mbody.textContent, /\+\$1\.59/);
  assert.match(mbody.textContent, /NHL\|full_game\|moneyline/);
  assert.match(mbody.textContent, /Tier 2 Incubating/);

  // Check proximity ordering: Proven (NFL) -> Approaching (EPL) -> Incubating (NHL)
  const rows = mbody.querySelectorAll('tr');
  assert.equal(rows.length, 3, 'Must render exactly 3 markets');
  assert.match(rows[0].textContent, /NFL\|full_game\|totals/, 'First row must be Tier 4 Proven Alpha');
  assert.match(rows[0].textContent, /10 \/ 10/, 'Tier 4 row shows 10 / 10 matches');
  assert.match(rows[0].textContent, /\$3\.50/, 'Tier 4 row shows allocated max stake');

  assert.match(rows[1].textContent, /EPL\|full_game\|totals/, 'Second row must be Approaching Tier 4');
  assert.match(rows[1].textContent, /8 \/ 10/, 'Approaching row shows 8 / 10 matches');
  assert.match(rows[1].textContent, /2 more games needed/, 'Approaching row shows 2 more games needed');
  assert.match(rows[1].textContent, /\$0\.00/, 'Approaching row has $0.00 Polymarket stake');

  assert.match(rows[2].textContent, /NHL\|full_game\|moneyline/, 'Third row must be Incubating');
  assert.match(rows[2].textContent, /0 \/ 10/, 'Incubating row shows 0 / 10 matches');
  assert.match(rows[2].textContent, /No live bets yet/);
});

test('Polymarket Desk action buttons trigger runner cycle, reset-shadow, and auto-redeem API calls', async (t) => {
  const { d, writes } = await bootPolymarketDesk(t);

  const polyNavBtn = d.querySelector('.nav-item[data-view="polymarket"]');
  polyNavBtn.click();
  await new Promise(r => setTimeout(r, 60));

  // Trigger cycle
  const trigBtn = d.querySelector('#poly-trigger-cycle-btn');
  assert.ok(trigBtn, 'Trigger cycle button must exist');
  trigBtn.click();
  await new Promise(r => setTimeout(r, 60));

  assert.ok(writes.some(w => w.path === '/api/polymarket/runner/trigger'), 'Runner trigger endpoint must be called');

  // Trigger reset-shadow
  const resetBtn = d.querySelector('#poly-reset-shadow-btn');
  assert.ok(resetBtn, 'Reset shadow button must exist');
  resetBtn.click();
  await new Promise(r => setTimeout(r, 60));

  assert.ok(writes.some(w => w.path === '/api/polymarket/reset-shadow'), 'Reset shadow endpoint must be called');

  // Trigger redeem
  const redeemBtn = d.querySelector('#poly-settle-redeem-btn');
  assert.ok(redeemBtn, 'Redeem button must exist');
  redeemBtn.click();
  await new Promise(r => setTimeout(r, 60));

  assert.ok(writes.some(w => w.path === '/api/polymarket/redeem'), 'Redeem endpoint must be called');
});
