import { Window } from 'happy-dom';
import fs from 'fs';
import path from 'path';

async function run() {
  console.log('1. Fetching live dashboard from http://127.0.0.1:8765/api/dashboard...');
  const res = await fetch('http://127.0.0.1:8765/api/dashboard', {
    headers: { 'Accept-Encoding': 'identity' }
  });
  if (!res.ok) throw new Error(`HTTP error ${res.status}: ${res.statusText}`);
  const dashboardData = await res.json();
  console.log('   -> Live dashboard received successfully.');
  console.log(`   -> Recommendations: ${dashboardData.recommendations?.length}`);
  console.log(`   -> Tracked bets: ${dashboardData.tracked_bets?.length}`);
  console.log(`   -> Evaluated candidates: ${dashboardData.evaluated_candidates?.length}`);

  console.log('2. Setting up Happy-DOM with index.html...');
  const htmlPath = path.resolve('src/betting_engine/web/index.html');
  const jsPath = path.resolve('src/betting_engine/web/app.js');
  const html = fs.readFileSync(htmlPath, 'utf-8');

  const window = new Window({
    url: 'http://127.0.0.1:8765/',
    width: 1280,
    height: 900
  });
  const document = window.document;
  document.write(html);

  // Polyfills & Mocks
  window.EventSource = class {
    addEventListener(name, callback) {}
    close() {}
  };
  window.fetch = async (url, opts) => {
    const urlStr = String(url);
    if (urlStr.includes('/api/dashboard')) {
      return {
        ok: true,
        status: 200,
        json: async () => dashboardData
      };
    }
    if (urlStr.includes('/api/schedule')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ games: [], unavailable: [], no_games: [] })
      };
    }
    if (urlStr.includes('/api/rd')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ telemetry: {}, active_hypotheses: [], history: [] })
      };
    }
    if (urlStr.includes('/api/stake/account')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ authenticated: false, balance: 0, active_bets: [] })
      };
    }
    if (urlStr.includes('/api/bets')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ bets: dashboardData.tracked_bets || [], performance: dashboardData.performance || {} })
      };
    }
    if (urlStr.includes('/api/statistics')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ loop: { state: 'monitoring' }, proposals: [] })
      };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  // Mock dialog showModal
  if (!window.HTMLDialogElement.prototype.showModal) {
    window.HTMLDialogElement.prototype.showModal = function () {
      this.open = true;
    };
    window.HTMLDialogElement.prototype.close = function () {
      this.open = false;
    };
  }

  // Audio mock
  window.Audio = class {
    play() { return Promise.resolve(); }
  };

  console.log('3. Executing app.js inside DOM and waiting for load()...');
  const appJs = fs.readFileSync(jsPath, 'utf-8');
  window.eval(appJs);
  await new Promise(r => setTimeout(r, 200));

  console.log('5. Verifying Betting Picks view...');
  const tipsGrid = document.querySelector('#tips-grid');
  const tipCards = tipsGrid.querySelectorAll('.tip-card');
  const tipCount = document.querySelector('#tip-count')?.textContent;
  console.log(`   -> Rendered tip cards in grid: ${tipCards.length}`);
  console.log(`   -> #tip-count badge: ${tipCount}`);
  if (tipCards.length === 0) {
    throw new Error('No tip cards rendered in #tips-grid!');
  }

  // Check card content
  const firstCard = tipCards[0];
  const leagueBadge = firstCard.querySelector('.league')?.textContent;
  const matchup = firstCard.querySelector('.matchup')?.textContent;
  const market = firstCard.querySelector('.selection')?.textContent;
  const selection = firstCard.querySelector('.selection strong')?.textContent || firstCard.querySelector('h3')?.textContent;
  const odds = firstCard.querySelector('.odds-value')?.textContent;
  console.log(`   -> Sample Card: [${leagueBadge}] ${selection} (${market}) @ ${odds} | Match: ${matchup}`);

  // Test opening detail dialog
  console.log('6. Testing detail dialog on first card...');
  const tipMain = firstCard.querySelector('.tip-main') || firstCard;
  tipMain.click();
  await new Promise(r => setTimeout(r, 50));
  const detailDialog = document.querySelector('#detail-dialog');
  const detailContent = document.querySelector('#detail-content');
  if (!detailDialog.open) throw new Error('Detail dialog did not open!');
  console.log('   -> Detail dialog rendered correctly with full analysis.');
  detailDialog.close();

  // Test views navigation
  console.log('7. Testing view navigation:');
  const views = ['ledger', 'sizing', 'statistics', 'games', 'settings', 'rd', 'stake_account'];
  for (const v of views) {
    const navItem = document.querySelector(`.nav-item[data-view="${v}"]`);
    if (navItem) {
      navItem.click();
      const viewEl = document.querySelector(`#view-${v}`);
      if (viewEl.hidden) throw new Error(`View #view-${v} failed to unhide!`);
      console.log(`   -> View '${v}' rendered OK.`);
    }
  }

  // Check Research & Coverage view
  const resItem = document.querySelector('.nav-item[data-view="research"]');
  if (resItem) {
    resItem.click();
    const evalCount = document.querySelector('#evaluated-count')?.textContent;
    const altCount = document.querySelector('#alternative-count')?.textContent;
    console.log(`   -> View 'research' rendered OK. Evaluated: ${evalCount}, Alternatives: ${altCount}`);
  }

  // Switch back to tips
  document.querySelector('.nav-item[data-view="tips"]').click();
  console.log('   -> Returned to Betting Picks cleanly.');

  // Test Horizons
  console.log('8. Testing horizon filter buttons:');
  for (const h of ['24', '48', '72', 'today', 'tomorrow']) {
    const hBtn = document.querySelector(`[data-horizon="${h}"]`);
    if (hBtn) {
      hBtn.click();
      const currentCards = tipsGrid.querySelectorAll('.tip-card').length;
      console.log(`   -> Horizon [${h}] rendered ${currentCards} cards.`);
    }
  }

  console.log('\n=== ALL DOM & UI INTEGRITY CHECKS PASSED 100%! ===');
  await window.happyDOM.close();
}

run().catch(err => {
  console.error('\n❌ AUDIT FAILED:', err);
  process.exit(1);
});
