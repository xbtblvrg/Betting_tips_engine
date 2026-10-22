import {Window} from 'happy-dom';
import {readFileSync, writeFileSync} from 'node:fs';

const html = readFileSync('src/betting_engine/web/index.html', 'utf8');
const script = readFileSync('src/betting_engine/web/app.js', 'utf8');

async function dump35() {
  const w = new Window({url: 'http://127.0.0.1:8765'});
  w.EventSource = class {
    addEventListener(name, cb) {}
    close() {}
  };
  w.document.write(html);

  // Fetch real data from server
  const dashResp = await fetch('http://127.0.0.1:8765/api/dashboard');
  const dashData = await dashResp.json();

  w.fetch = async (url) => {
    if (url.includes('/api/dashboard')) return { ok: true, json: async () => dashData };
    if (url.includes('/api/settings')) return { ok: true, json: async () => dashData.settings };
    if (url.includes('/api/bets')) return { ok: true, json: async () => ({ bets: dashData.tracked_bets }) };
    return { ok: true, json: async () => ({}) };
  };

  w.eval(script);
  await new Promise(r => setTimeout(r, 600));

  // Switch to 72h horizon
  const btn72 = w.document.querySelector('[data-horizon="72"]');
  if (btn72) {
    btn72.click();
    await new Promise(r => setTimeout(r, 300));
  }

  const cards = w.document.querySelectorAll('#tips-grid .tip-card');
  const result = [];
  for (let i = 0; i < cards.length; i++) {
    const card = cards[i];
    const metrics = card.querySelectorAll('.metric-value');
    const subs = card.querySelectorAll('.stat-sub');
    const timing = card.querySelector('.timing-callout')?.textContent?.trim() || '';
    const stakeTab = card.querySelector('.stake-tab')?.textContent?.trim() || '';
    const poly = card.querySelector('.polymarket-edge, .polymarket-matched')?.textContent?.trim() || '';
    const tier = card.querySelector('.tip-bottom-badges .badge')?.textContent?.trim() || '';

    result.push({
      num: i + 1,
      league: card.querySelector('.league')?.textContent?.trim(),
      time: card.querySelector('.time')?.textContent?.trim(),
      match: card.querySelector('.tip-match')?.textContent?.trim(),
      market: card.querySelector('.tip-market')?.textContent?.trim(),
      stakeTab: stakeTab,
      selection: card.querySelector('.selection')?.textContent?.trim(),
      probability: metrics[0]?.textContent?.trim(),
      odds: metrics[1]?.textContent?.trim(),
      origOdds: subs[0]?.textContent?.trim() || '',
      minOdds: subs[1]?.textContent?.trim() || '',
      stake: metrics[2]?.textContent?.trim(),
      tier: tier,
      timing: timing,
      polymarket: poly
    });
  }

  writeFileSync('data/current_35_instructions.json', JSON.stringify(result, null, 2));
  console.log('Saved', result.length, 'instructions to data/current_35_instructions.json');
  await w.happyDOM.close();
}

dump35().catch(console.error);
