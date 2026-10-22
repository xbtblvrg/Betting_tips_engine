import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';

const html = readFileSync('src/betting_engine/web/index.html', 'utf8');
const script = readFileSync('src/betting_engine/web/app.js', 'utf8');

async function bootRD(t) {
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
    if (path === '/api/rd') {
      return {
        ok: true,
        json: async () => ({
          status: 'operational',
          scoreboard: {
            counts: {total_segments: 87, alpha: 16, underperforming: 31},
            total_staked: 142.50,
            total_pnl: 18.20,
            overall_roi_pct: 12.77,
            alpha_segments: [
              {
                segment: 'NFL|full_game|spreads',
                league: 'NFL',
                scope: 'full_game',
                games: 27,
                pnl: 50.25,
                roi_pct: 28.98,
                win_rate_pct: 72.2,
                tier: 'ALPHA_BOOST',
                sub_tier: 'SUPER_ALPHA',
                scaling_target: '$5.00–$8.00 super-alpha scaling'
              }
            ],
            underperforming_segments: [
              {
                segment: 'NHL|full_game|totals',
                league: 'NHL',
                scope: 'full_game',
                games: 31,
                pnl: -10.50,
                roi_pct: -22.31,
                win_rate_pct: 45.7
              }
            ]
          },
          supervisor: {
            agent: 'SupervisedQuantRDAgent',
            governance_state: 'active_supervised',
            anti_looping_guard: {
              status: 'enforced',
              sla_seconds: 300,
              emergency_timeout_seconds: 1800
            }
          },
          hypotheses_summary: {
            total: 5,
            passed_crucible: 5,
            rejected: 0,
            crucible_pass_rate_pct: 100.0
          },
          hypotheses: [
            {
              hypothesis_id: 'hyp_nhl_totals_negbin_v1',
              title: 'Negative Binomial overdispersion for NHL game totals',
              category: 'distribution_switch',
              segment: 'NHL|full_game|totals',
              rationale: 'Replace equidistant Poisson variance with Negative Binomial to account for empty-net clusters.',
              proposed_formula_or_rule: 'NegBin(r=mean**2 / (var - mean), p=mean / var) with alpha=1.22',
              status: 'passed_crucible',
              crucible_evidence: {
                sample_size: 328,
                baseline_brier: 0.2356,
                candidate_brier: 0.2324,
                brier_improvement: 0.0033,
                roi_delta_pct: 0.0
              }
            }
          ],
          forensic_dossiers: [
            {
              segment: 'NHL|full_game|totals',
              primary_failure_mode: 'tail_variance',
              actionable_remedy: 'Negative Binomial overdispersion with empty-net situational parameter'
            }
          ],
          execution_logs: [
            {
              timestamp: '2026-10-06T16:16:04.548102+00:00',
              stage: 'FORENSICS',
              level: 'INFO',
              message: 'Forensic loss attribution started for NHL|full_game|totals',
              extra: {}
            },
            {
              timestamp: '2026-10-06T16:16:05.123456+00:00',
              stage: 'CRUCIBLE',
              level: 'INFO',
              message: 'Crucible backtest evaluated: hyp_nhl_totals_negbin_v1 delta Brier +0.0033',
              extra: {}
            }
          ],
          cycle_history: [
            {
              cycle_id: 'rd_cycle_20261006T161604',
              executed_at: '2026-10-06T16:16:04.547936+00:00',
              underperforming_segments_checked: 31,
              forensic_dossiers_created: 11,
              hypotheses_generated: 14,
              crucible_tests_run: 1,
              candidates_passed: 1,
              candidates_rejected: 0,
              details: []
            }
          ],
          autonomous_timeline: [
            {
              id: 'tl_nba_preseason_clamp',
              title: 'NBA Pre-Season 4th Quarter Volatility & Kelly Stake Clamp',
              league: 'NBA',
              sport: 'Basketball',
              segment: 'NBA|full_game|totals',
              discovered_at: '2026-10-10T23:45:00Z',
              deployed_at: '2026-10-11T02:15:00Z',
              failure_mode: 'Tail Variance & Rotational Chaos',
              defect_description: 'Pre-season NBA games experienced severe totals volatility due to deep bench rotations and intentional fouls in the 4th quarter. Unconstrained Kelly sizing allocated $5.00 maximum bets, causing a -$22.15 overnight deficit.',
              engineered_solution: 'outcome_sizing.py: Hard preseason_clamp ($1.00 max stake ceiling across all pre-season games, overriding Kelly sizing)',
              code_reference: 'src/betting_engine/outcome_sizing.py:324-332',
              status: 'deployed_and_working',
              status_label: 'ACTIVE & SHIELDING',
              is_working: true,
              verification_evidence: 'The -$22.15 pre-season loss bleed was immediately halted. Zero tickets above $1.00 have been issued for pre-season games since deployment.',
              metrics: {pnl_impact: 'Loss bleed stopped', max_stake: '$1.00'}
            }
          ],
          next_up_queue: [
            {
              id: 'next_ncaaf_hfa_retrain',
              priority: 1,
              title: 'NCAAF College Football: League-Specific 80-Elo HFA & Pace Model Retraining',
              league: 'NCAAF',
              sport: 'American Football',
              segment: 'NCAAF|full_game|spreads',
              discovered_at: '2026-10-10T22:00:00Z',
              eta: 'Next 24h Walk-Forward Learning Cycle',
              current_status: 'in_progress_queued',
              status_label: 'R&D / QUEUED',
              waiting_on: 'Walk-forward Crucible Brier score comparison across 240+ college games',
              defect_investigated: 'FormState in models.py previously used NFL pro 50-Elo home field advantage, whereas collegiate HFA is empirically 75–85 Elo points.',
              planned_solution: 'League-specific HOME_ADVANTAGE dictionary (80 Elo) in models.py and retrained tempo/pace.',
              target_impact: 'Reverse -$64.45 NCAAF deficit and graduate segment to Solid Alpha.'
            }
          ],
          market_incubator: {
            department: 'Autonomous Market Expansion & Shadow Graduation Department (AMIE-GD)',
            active_incubations: 33,
            total_wagers_observed: 1389,
            status_counts: {open: 1389},
            graduated_markets_count: 1,
            graduated_markets: [
              {
                segment_key: 'ATP|full_game|tennis_sets',
                league: 'ATP',
                scope: 'full_game',
                family: 'tennis_sets',
                sub_family: 'set_totals',
                graduated_at: '2026-10-12T16:00:00Z',
                sample_size: 25,
                roi_pct: 12.5,
                delta_brier: 0.0045,
                stats: {}
              }
            ],
            incubated_families: [
              {
                segment_key: 'MLS|full_game|asian_lines',
                league: 'MLS',
                scope: 'full_game',
                family: 'asian_lines',
                sub_family: 'asian_handicap',
                total_wagers: 18,
                settled_wagers: 0,
                pnl: 0.0,
                roi_pct: 0.0,
                delta_brier: 0.0,
                status: 'incubating'
              }
            ]
          },
          quant_council_sessions: [
            {
              session_id: 'council_test_001',
              segment: 'NHL|full_game|totals',
              league: 'NHL',
              created_at: '2026-10-13T18:00:00Z',
              trigger_reason: 'ROI -22.3% across 31 games under EMPTY_NET_TAIL_VARIANCE',
              sports_mechanics_advice: {text: 'Claude mechanics: 3rd period empty net clustering expands tail variance.'},
              quant_remedy_advice: {text: 'Codex remedy: apply Negative Binomial dispersion alpha=1.22.'},
              red_team_advice: {text: 'Grok red-team: clamp maximum probability shift to 3.5%.'},
              synthesis: {decision: 'Approved for Quantitative Crucible walk-forward backtest'},
              synthesized_hypothesis: {
                hypothesis_id: 'hyp_council_nhl_full_game_totals_v1',
                title: 'Quant Council: NHL EMPTY_NET_TAIL_VARIANCE Net-PnL Accelerator',
                rationale: 'Council Synthesis under mandatory prime directive',
                proposed_formula_or_rule: 'negbin_overdispersion_correction'
              }
            }
          ]
        })
      };
    }
    if (path === '/api/rd/cycle') {
      return {
        ok: true,
        json: async () => ({
          success: true,
          cycle: {
            cycle_id: 'rd_cycle_test_123',
            underperforming_segments_checked: 31
          }
        })
      };
    }
    if (path === '/api/dashboard') {
      return {
        ok: true,
        json: async () => ({
          version: 1,
          settings: {max_bet: 5, minimum_roi: 0.03, daily_pick_target: 100, refresh_seconds: 60},
          engine: {running: false},
          status: {},
          games: [],
          models: {},
          performance: {prospective: []}
        })
      };
    }
    return {ok: true, json: async () => ({})};
  };

  w.eval(script);
  await new Promise(r => setTimeout(r, 20));
  return {w, d: w.document, writes};
}

test('Quant R&D Live subpage renders architecture, telemetry, underperforming segments and hypotheses', async t => {
  const {w, d} = await bootRD(t);

  // Click on the Quant R&D nav button
  const rdNav = d.querySelector('[data-view="rd"]');
  assert.ok(rdNav, 'Quant R&D nav item must exist in sidebar');
  rdNav.click();

  await new Promise(r => setTimeout(r, 30));

  // Check view visibility
  assert.equal(d.querySelector('#view-rd').hidden, false, '#view-rd must be visible');
  assert.equal(d.querySelector('#view-tips').hidden, true, '#view-tips must be hidden');
  assert.match(d.querySelector('#breadcrumb').textContent, /Autonomous Quant R&D Live/);

  // Check metrics cards
  const metrics = d.querySelector('#rd-metrics');
  assert.ok(metrics, '#rd-metrics exists');
  assert.match(metrics.textContent, /87/); // 87 segments
  assert.match(metrics.textContent, /16/); // 16 alpha
  assert.match(metrics.textContent, /31/); // 31 underperforming
  assert.match(metrics.textContent, /100% Pass/); // crucible pass rate

  // Check Dynamic Alpha segments table
  const alphaTable = d.querySelector('#rd-alpha-table');
  assert.ok(alphaTable, '#rd-alpha-table exists');
  assert.match(alphaTable.textContent, /NFL\|full_game\|spreads/);
  assert.match(alphaTable.textContent, /\$5\.00–\$8\.00 super-alpha scaling/);
  assert.match(alphaTable.textContent, /SUPER_ALPHA/);

  // Check Underperforming segments table
  const table = d.querySelector('#rd-underperforming-table');
  assert.ok(table, '#rd-underperforming-table exists');
  assert.match(table.textContent, /NHL\|full_game\|totals/);
  assert.match(table.textContent, /\$1\.00 Stake Clamp/);
  assert.match(table.textContent, /tail_variance/);

  // Check Hypotheses cards
  const hypGrid = d.querySelector('#rd-hypotheses-grid');
  assert.ok(hypGrid, '#rd-hypotheses-grid exists');
  assert.match(hypGrid.textContent, /Negative Binomial overdispersion for NHL game totals/);
  assert.match(hypGrid.textContent, /PASSED CRUCIBLE/);
  assert.match(hypGrid.textContent, /328 bets/);

  // Check Anti-looping and supervisor text
  const statusNote = d.querySelector('#rd-status-note');
  assert.match(statusNote.textContent, /SupervisedQuantRDAgent/);
  assert.match(statusNote.textContent, /300s SLA/);
  assert.match(statusNote.textContent, /1800s emergency timeout/);
  assert.match(statusNote.textContent, /Anti-Looping Guard: enforced/);
});

test('Running autonomous cycle button invokes POST /api/rd/cycle', async t => {
  const {w, d, writes} = await bootRD(t);

  const rdNav = d.querySelector('[data-view="rd"]');
  rdNav.click();
  await new Promise(r => setTimeout(r, 30));

  const runBtn = d.querySelector('#rd-run-cycle');
  assert.ok(runBtn, '#rd-run-cycle button exists');
  runBtn.click();

  await new Promise(r => setTimeout(r, 30));

  assert.ok(writes.some(w => w.path === '/api/rd/cycle'), 'POST /api/rd/cycle was invoked');
});

test('Live activity terminal, crucible comparison matrix, and cycle history table render correctly', async t => {
  const {w, d} = await bootRD(t);

  const rdNav = d.querySelector('[data-view="rd"]');
  rdNav.click();
  await new Promise(r => setTimeout(r, 30));

  // 1. Terminal log entries
  const term = d.querySelector('#rd-execution-terminal');
  assert.ok(term, '#rd-execution-terminal must exist');
  assert.match(term.textContent, /Forensic loss attribution started for NHL\|full_game\|totals/);
  assert.match(term.textContent, /Crucible backtest evaluated: hyp_nhl_totals_negbin_v1/);
  const forensicsBadge = term.querySelector('.rd-badge-FORENSICS');
  assert.ok(forensicsBadge, 'FORENSICS badge must be rendered in terminal');
  assert.equal(forensicsBadge.textContent, 'FORENSICS');

  // Test Clear Log button
  const clearBtn = d.querySelector('#rd-clear-terminal');
  assert.ok(clearBtn, '#rd-clear-terminal button exists');
  clearBtn.click();
  assert.match(term.textContent, /Terminal cleared/);

  // 2. Crucible comparison matrix
  const matrix = d.querySelector('#rd-crucible-matrix');
  assert.ok(matrix, '#rd-crucible-matrix must exist');
  assert.match(matrix.textContent, /Negative Binomial overdispersion for NHL game totals/);
  assert.match(matrix.textContent, /328 bets/);
  assert.match(matrix.textContent, /0\.2356/); // baseline brier
  assert.match(matrix.textContent, /0\.2324/); // candidate brier
  assert.match(matrix.textContent, /\+0\.0033/); // brier improvement
  assert.match(matrix.textContent, /PASSED CRUCIBLE/);

  // 3. Autonomous cycle run history table
  const cyclesTable = d.querySelector('#rd-cycles-table');
  assert.ok(cyclesTable, '#rd-cycles-table must exist');
  assert.match(cyclesTable.textContent, /rd_cycle_20261006T161604/);
  assert.match(cyclesTable.textContent, /31/); // 31 checked
  assert.match(cyclesTable.textContent, /11/); // 11 dossiers
  assert.match(cyclesTable.textContent, /14/); // 14 hypotheses
  assert.match(cyclesTable.textContent, /1 Passed/);
  assert.match(cyclesTable.textContent, /COMPLETED/);

  // 4. Cycle count
  const cycleCount = d.querySelector('#rd-cycle-count');
  assert.equal(cycleCount.textContent, '1 Cycles Completed');
});

test('Autonomous bug repair timeline and next up brain queue render correctly', async t => {
  const {w, d} = await bootRD(t);

  const rdNav = d.querySelector('[data-view="rd"]');
  rdNav.click();
  await new Promise(r => setTimeout(r, 40));

  // 1. Timeline Panel
  const tlPanel = d.querySelector('#rd-timeline-panel');
  assert.ok(tlPanel, '#rd-timeline-panel must exist');
  assert.match(tlPanel.textContent, /NBA Pre-Season/);
  assert.match(tlPanel.textContent, /ACTIVE & SHIELDING/);
  assert.match(tlPanel.textContent, /outcome_sizing\.py:324-332/);
  assert.match(tlPanel.textContent, /loss bleed was immediately halted/);

  // 2. Next Up Panel
  const nuPanel = d.querySelector('#rd-nextup-panel');
  assert.ok(nuPanel, '#rd-nextup-panel must exist');
  assert.match(nuPanel.textContent, /NCAAF College Football/);
  assert.match(nuPanel.textContent, /PRIO #1/);
  assert.match(nuPanel.textContent, /R&D \/ QUEUED/);
  assert.match(nuPanel.textContent, /Next 24h Walk-Forward Learning Cycle/);
  assert.match(nuPanel.textContent, /Reverse -\$64\.45 NCAAF deficit/);

  // 3. AI Self-Healing Badge Click Handler Navigation
  const healingBadge = d.querySelector('#ai-self-healing-badge');
  assert.ok(healingBadge, '#ai-self-healing-badge must exist');
  // Switch away to tips first
  const tipsNav = d.querySelector('[data-view="tips"]');
  tipsNav.click();
  await new Promise(r => setTimeout(r, 20));
  assert.equal(d.querySelector('#breadcrumb').textContent, 'Betting Picks');

  // Click healing badge
  healingBadge.click();
  await new Promise(r => setTimeout(r, 30));
  assert.equal(d.querySelector('#breadcrumb').textContent, 'Autonomous Quant R&D Live');
});

test('AMIE-GD Shadow Market Incubator renders metrics, incubating families, and graduated markets', async t => {
  const {w, d} = await bootRD(t);

  const rdNav = d.querySelector('[data-view="rd"]');
  rdNav.click();
  await new Promise(r => setTimeout(r, 40));

  const incPanel = d.querySelector('#rd-incubator-panel');
  assert.ok(incPanel, '#rd-incubator-panel must exist');
  assert.match(incPanel.textContent, /AUTONOMOUS MARKET EXPANSION & SHADOW GRADUATION/);
  assert.match(incPanel.textContent, /33/);
  assert.match(incPanel.textContent, /1389/);
  assert.match(incPanel.textContent, /MLS\|full_game\|asian_lines/);
  assert.match(incPanel.textContent, /ATP\|full_game\|tennis_sets/);
});

test('Multi-AI Quant Council renders deliberations, member advice, and Antigravity synthesis', async t => {
  const {w, d} = await bootRD(t);

  const rdNav = d.querySelector('[data-view="rd"]');
  rdNav.click();
  await new Promise(r => setTimeout(r, 40));

  const councilPanel = d.querySelector('#rd-council-panel');
  assert.ok(councilPanel, '#rd-council-panel must exist');
  assert.match(councilPanel.textContent, /Multi-AI Quant Council/);
  assert.match(councilPanel.textContent, /1 Deliberations Logged/);
  assert.match(councilPanel.textContent, /Claude \(Sports Mechanics\)/);
  assert.match(councilPanel.textContent, /Codex \(Quant Remedy\)/);
  assert.match(councilPanel.textContent, /Grok \(Red-Team\)/);
  assert.match(councilPanel.textContent, /Google Antigravity Synthesis/);
  assert.match(councilPanel.textContent, /hyp_council_nhl_full_game_totals_v1/);
});


