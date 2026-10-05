# Handoff Documentation: PnL & Sizing Optimization
**Date:** 2026-10-04  
**Author:** Pair Programming Session with Antigravity (`agy`) / Bela Varga  
**Target Audience:** Codex (Primary Brain / Lead Developer), Claude Opus, Future AI Contributors, and Engineering Supervisors  
**Contract Version:** `2026-10-01.profit-learning-directive-v1`  
**Status:** Approved & Implemented in Production Engine

---

## 1. Executive Summary & Objective

The user issued an explicit directive:
1. **Increase Net P&L and ROI** across the betting recommendations engine.
2. **Preserve the mandatory volume requirement:** strictly at least **100 distinct funded recommendations per Budapest calendar day** (`daily_pick_target = 100`).
3. **Never choke or starve the engine:** The system must not drop candidates using hard cut-off filters (e.g. minimum odds or minimum EV gates).
4. **Adhere to the strict $1.00–$5.00 sizing constraint:** Never size below $1.00 (all executable recommendations must be funded), never exceed $5.00 (`max_bet = 5.0`), and maintain `daily_risk_limit = null`.

---

## 2. Empirical Root-Cause Analysis (640 Settled Bets Ledger)

Before modifying any production code, an exhaustive audit of all 640 settled bets in `data/engine.sqlite3` was conducted.

### 2.1 Overall Performance
- **Total settled bets:** 640
- **Total staked capital:** $837.78 (average stake: $1.31)
- **Net P&L:** -$33.76 (-4.03% ROI)
- **Overall Win Rate:** 47.8%

### 2.2 Performance Breakdown by Market Family & EV Threshold
When disaggregated by market category and model Expected Value (`expected_roi = p * odds - 1`), an undeniable statistical truth emerged:

| Market Category | EV Bucket | Count (N) | Win Rate (WR) | Staked ($) | P&L ($) | ROI (%) |
|---|---|---|---|---|---|---|
| **WINNER (Moneyline/1X2)** | **EV > +3%** | **125** | **36.0%** | **$169.89** | **+$13.44** | **+7.9%** |
| WINNER (Moneyline/1X2) | EV $\le$ +3% | 81 | 55.6% | $105.25 | -$5.86 | -5.6% |
| **SPREAD (Handicap)** | **EV > +3%** | **97** | **57.7%** | **$152.41** | **+$12.11** | **+7.9%** |
| SPREAD (Handicap) | EV $\le$ +3% | 42 | 42.9% | $43.19 | -$8.54 | -19.8% |
| **TOTAL_OVER** | **EV > +3%** | **74** | **51.4%** | **$111.79** | **+$5.44** | **+4.9%** |
| TOTAL_OVER | EV $\le$ +3% | 61 | 49.2% | $62.65 | -$10.41 | -16.6% |
| TOTAL_UNDER | EV > +3% | 97 | 41.2% | $127.67 | -$20.78 | -16.3% |
| TOTAL_UNDER | EV $\le$ +3% | 25 | 40.0% | $25.30 | -$8.25 | -32.6% |

### 2.3 The Core Findings
1. **The Positive Edge Segment:**
   Whenever the model forecasts positive edge ($\text{EV} > 3\%$) on **Winner, Spread, or Over**, the selections are genuinely profitable:
   $$\text{Winner (+\$13.44)} + \text{Spread (+\$12.11)} + \text{Over (+\$5.44)} = \mathbf{+\$30.99\text{ PROFIT (+7.2\% ROI across 296 bets)}}.$$
2. **The Loss Drivers:**
   - **Negative-EV / Volume Fallbacks:** Bets issued with $\text{EV} \le 3\%$ lost **-$33.06** across all markets.
   - **Totals Under Model Drag:** `TOTAL_UNDER` lost **-$29.02** (-19.0% ROI) due to systemic score distribution limitations (empty-net goals, overtime, late-game intentional fouls).
3. **The Sizing Flaw in Legacy v2/v3:**
   - The engine was applying unit discounts (`1 / sqrt(1 + related_count)`) that crushed positive-EV Winner stakes down to $1.20–$1.50.
   - Meanwhile, high-probability heavy favorites on negative-EV markets (odds 1.10–1.25) received $2.00–$3.10 stakes via `_v3_adjustment` solely because nominal $p \ge 90\%$, multiplying losses when an upset occurred.

---

## 3. Architectural Design: Why the Engine Cannot Be Choked

### 3.1 The Volumetric Trap (Why Hard Filters Were Rejected)
In a typical live feed snapshot:
- Total evaluated candidates: ~1,480.
- Candidates with positive model EV: **24 candidates**.
- Candidates eligible for funding (`probability_pick` fallback tier): **648 candidates**.
- Total eligible pool: **672 candidates**.

If any engineer implements a hard gate (e.g. `if ev <= 0: drop` or `if odds < 1.45: drop`), **only 24 bets could be issued**, immediately violating Contract Rule R05 (minimum 100 distinct bets/day).

### 3.2 The Safe Two-Pillar Solution
1. **Soft Priority Ranking (`continuous_probability_v3` in `decisions.py`):**
   - High-edge bets (Winner, Spread, Over with $\text{EV} > 3\%$, Tier 3/2) are sorted to the top of the publication queue.
   - Fallback `probability_pick` candidates ($p \ge 50\%$) remain 100% eligible and fill all remaining slots up to the 100+ daily quota.
   - Totals Under with negative EV receives a slight soft priority penalty (-0.05 score) so profitable markets are preferred whenever available, but remains available if needed to fulfill the volume target.
   - **Candidate pool retention: 100%. Zero candidates dropped.**
2. **Edge-Aware Differential Sizing ($1.00 vs $2.50–$4.50):**
   - Candidates with verified positive EV ($\text{EV} > 3\%$) on core/derivative markets scale gracefully from **$2.50 to $4.50**.
   - Volume filler candidates ($\text{EV} \le 3\%$) and high-variance Totals Under are strictly clamped to the **$1.00 minimum floor** (`max(1.0, ...)`).
   - This prevents over-exposure on negative edge while ensuring every single pick satisfies the $1.00 executable funding requirement.

---

## 4. Code Changes Specification

### 4.1 `src/betting_engine/decisions.py`
- Added constant `CONTINUOUS_PROBABILITY_SELECTION_V3 = 'continuous_probability_v3'`.
- Extended `family_selection_policy(settings)` to validate and accept `v3`.
- Updated `continuous_probability_allocation(settings)` to return `True` for both `v2` and `v3`.
- Implemented `continuous_selection_priority_v3(tip, tier)`:
  - Factors in: tier weight (10.0 / tier), win probability $p$, clamped EV edge, `learned_selection_score(tip)`, core market bonus, and a soft -0.05 drag on negative-EV Totals Under.
  - Returns a 6-element deterministic tie-breaking tuple.
- In `rank_and_size`, dispatches to `continuous_selection_priority_v3` when `v3` is active, preserving exact `v2` behavior when `v2` is configured.

### 4.2 `src/betting_engine/outcome_sizing.py`
- In `_v3_adjustment`, incorporated an edge-awareness check:
  - If a candidate has negative EV (`expected_roi < 0` or `odds * p < 1.0`) or is a Totals Under contract, confidence uplift is prevented from inflating the stake beyond the $1.00 base unit.
  - Positive-EV candidates retain full confidence and historical uplift up to $5.00.
  - All existing `POLICY_V3` test suites (which use positive-EV test payloads at 2.00 odds) continue to pass 100%.

### 4.3 `src/betting_engine/funding_policy.py`
- Updated line 296 to include `continuous_probability_v3`:
  `if settings.get('family_selection_policy') in ('continuous_probability_v2', 'continuous_probability_v3'):`
  guaranteeing the user-authorized $1.00 minimum ticket floor is active for all eligible candidates under `v3`.

### 4.4 `src/betting_engine/storage.py` & `dynamic_event_limits.py`
- Updated multi-family publication checks to recognize `continuous_probability_v3` alongside `v2`:
  - `storage.py` line 466: exempts `v3` from legacy single-family suppression.
  - `dynamic_event_limits.py` lines 124 & 162: supports `v3` in dynamic roster expansion.
- `server.py`: Added `'continuous_probability_v3'` to the `family_selection_policy` Literal type.

---

## 5. Verification & Testing Evidence

1. **Pre-change Snapshot:**
   Original files backed up in `snapshots/backup_20261004_pnl_optimization/`:
   - `decisions.py`
   - `outcome_sizing.py`
   - `project_contract.json`
2. **Python Test Suite:**
   All 199 sizing, decision, and dynamic event tests pass:
   `PYTHONPATH=src .venv/bin/python -m pytest tests/test_*.py -q`
3. **Frontend DOM Tests:**
   All 294 tests pass cleanly:
   `npm test`
4. **Live Engine Simulation:**
   Verified on the live SQLite database (`data/engine.sqlite3`):
   - Multi-cycle issuance produces 100+ picks effortlessly.
   - Current daily issuances: 136 funded picks today; 100 currently executable.

---

## 6. Guidelines for Future AI Maintainers (Codex / Claude)

1. **Do not remove the $1.00 minimum floor.** The engine relies on `max(1.0, ...)` to fulfill the 100 distinct bets requirement without violating funding rules.
2. **Do not reintroduce hard odds/EV filters on `probability_pick`.** Soft prioritization orders the candidates; hard filtering strangles issuance.
3. **When modifying Totals models:** Totals Over is sound (+4.9% ROI); Totals Under requires calibration adjustments in `score_distribution` for overtime and empty-net conditions before its stakes are expanded above $1.00.
4. **Preserve backward compatibility:** All historical decision rows in `system_decisions` are immutable; never rewrite settled bets or retroactive allocations.

---

## 7. Update (2026-10-05): Minimum Odds Floor (`min_odds = 1.40`)

- **User Directive:** The user identified that low odds bets (1.15 - 1.35) were disproportionately dragging PnL and requested a minimum odds floor.
- **Empirical Ledger Proof (897 Settled Bets):**
  - Bets `< 1.30`: $N=59$, $48\text{W} / 11\text{L}$ ($\text{WR}=81.4\%$), PnL = **-$5.68 (-7.2% ROI)**.
  - Bets `1.30 - 1.49`: $N=71$, $41\text{W} / 27\text{L}$ ($\text{WR}=60.3\%$), PnL = **-$10.81 (-11.3% ROI)**.
  - Bets `1.80 - 2.19`: $N=305$, $161\text{W} / 138\text{L}$ ($\text{WR}=53.8\%$), PnL = **+$17.75 (+4.3% ROI)**.
  - Combined sub-1.50 bets lost **-$16.49**, while bets $\ge 1.80$ produced **+$22.66 profit**.
- **Implementation:**
  - Configurable `min_odds = 1.40` in `settings` (`runtime_settings.json`, `data/engine.sqlite3`, and `Settings` schema).
  - In `funding_policy.py`: `funding_basis` sets `tier = 'unfunded'` if `odds < float(min_odds)`.
  - In `decisions.py`: `continuous_selection_priority_v3` adds an `odds_pref` term favoring odds in the profitable $\ge 1.50$ range; `rank_and_size` routes sub-1.40 candidates to `allocation_code = 'odds_below_minimum'`.
  - In `governance.py`: `min_odds` is sealed in `sizing_settings` and verified during publication.
- **Volumetric Proof:** Out of 761 candidates in the live feed, **273 eligible candidates** maintain odds $\ge 1.40$, ensuring the daily target of $\ge 100$ funded picks is completely unaffected and easily fulfilled.

---

## 8. Update (2026-10-05): Value Underdog Prioritization & Edge-Weighted Sizing (`odds >= 2.00`)

- **User Directive:** The user noticed that high-odds picks ($2.00$–$3.00+$) with good confidence used to be recommended and produced profitable wins in the past, but recently mostly sub-2.0 odds appeared. The user confirmed adding explicit prioritization and edge-weighted confidence sizing for positive-EV high odds picks.
- **Empirical Evidence from Ledger:**
  - In 897 settled bets, bets with odds $\ge 2.00$ generated **+$6.05 net profit** with notable big winners:
    - The Citadel Bulldogs moneyline @ 5.60 odds (+6.85 PnL)
    - Idaho Vandals moneyline @ 5.20 odds (+12.68 PnL)
    - Chicago White Sox -2.5 spread @ 4.40 odds (+3.40 PnL)
    - Atlanta Braves 1st inning @ 4.10 odds (+3.10 PnL)
    - New Hampshire Wildcats moneyline @ 3.85 odds (+5.50 PnL)
    - Washington Capitals -1.5 spread @ 3.70 odds (+2.70 PnL)
- **Root Causes Identified & Solved:**
  1. *Ranking favourite bias:* Prior formula weighted $p \times 2.0$ heavily, allowing thin-margin favorites ($p=0.80$ at $1.25$ odds) to rank above +15% EV underdogs ($p=0.45$ at $2.60$ odds).
     - **Solution in `decisions.py`:** Added `odds_pref = 0.15` for `odds >= 2.00` and an explicit `underdog_value_boost = min(0.35, ev * odds * 0.5)` when `odds >= 2.00 and ev > 0`. High-EV underdogs now comfortably outrank low-margin favorites.
  2. *Sizing choke bottleneck:* In `outcome_sizing.py`, `probability_strength = max(0., min(1., (p - .5) / .4))` collapsed to `0.0` for all $p < 0.50$ (which applies to all odds $\ge 2.00$), choking the confidence sizing to $0.0$ and forcing all underdogs to the \$1.00 minimum ticket floor.
     - **Solution in `outcome_sizing.py`:** Added `underdog_strength = min(1.0, ev_val / 0.15)` when `ev_val > 0 and odds >= 2.00`. Positive-EV underdogs now earn confidence-scaled stakes ($1.50–$3.50+), while negative-EV picks remain strictly clamped to the $1.00 floor.
- **Testing & Verification:**
  - Unit tests added:
    - `test_v3_value_underdog_boost_promotes_high_ev_underdog` in `test_continuous_probability_v3.py`
    - `test_v3_underdog_in_multi_game_ranking` in `test_continuous_probability_v3.py`
    - `test_positive_ev_underdog_earns_confidence_sizing` in `test_confident_outcome_sizing.py`
  - DOM tests: `npm test` 294 / 294 passed.
  - Live publication ID 9365 published 4 high-odds recommendations (odds 2.06, 2.80, 2.85, 3.05).
  - Snapshot backup stored in `snapshots/backup_20261005_value_underdog/`.

---

## 9. Update (2026-10-05): Smart Line-Pivoting (`continuous_probability_v4`) & Asymmetric Sizing

- **User Directive:** The user mandated maximizing Net PnL, Win Rate (WR), and ROI without truncating market coverage. When games have unprofitable lines (such as low-margin Totals Under), the engine must pivot to higher-EV lines on the same game instead of excluding the match or betting into bad odds.
- **Implementation in `decisions.py`:**
  - Introduced `CONTINUOUS_PROBABILITY_SELECTION_V4 = 'continuous_probability_v4'`.
  - Implemented `continuous_selection_priority_v4(tip, tier)`:
    - **Spreads bonus:** $+0.40$
    - **Moneyline bonus:** $+0.35$
    - **Team Totals bonus:** $+0.25$
    - **Totals Under penalty:** $-0.25$ drag unless $\text{EV} \ge +5\%$.
    - **Attractive odds preference:** $+0.10$ for $\text{odds} \ge 1.70$, $+0.20$ for $\text{odds} \ge 2.00$.
    - **Higher EV scaling:** $2.5 \times \text{EV}$ weight.
  - Registered across `funding_policy.py`, `storage.py`, `dynamic_event_limits.py`, `governance.py`, `server.py`, and `runtime_settings.json`.
- **Implementation in `outcome_sizing.py`:**
  - Added `spread_ev_strength` to `_v3_adjustment()`:
    - Positive-EV Spreads and Moneylines with $\text{odds} \ge 1.65$ scale up to $3.50–$5.00.
    - Low-edge and defensive filler bets strictly maintain the bounded $1.00 base unit.
- **Verification:**
  - `tests/test_continuous_probability_v4.py`: 5 / 5 passed.
  - Pytest full suite: 307 / 307 passed.

---

## 10. Update (2026-10-05): Pure Execution List Clean-Up (Removal of 'No Bet Anymore' and 'No Longer Recommended')

- **User Directive:** The user treats the main recommendation list as an execution list. The user strictly forbade 'No bet anymore' cards and 'No longer recommended' stats, demanding their permanent elimination from the main picks grid and stats banner.
- **Implementation in `src/betting_engine/web/app.js`:**
  - In `renderTips()`: removed `changedMarkup` from `#tips-grid`. Only valid, executable `readyTips()` with $1–$5 stakes are rendered as cards.
  - In `renderStats()`: eliminated the 'Changed / No bet anymore' and 'No longer recommended' stat cards.
  - In `renderTracked()`: replaced 'No longer recommended · Not a current instruction' with 'Recorded · Not a current instruction'.
  - Unqualified or changed selections reside strictly in Bet Tracker (`#withdrawn-upcoming-section` and `#history-table`), preserving the immutable audit trail without cluttering execution.
- **Verification:**
  - `npm test`: 294 / 294 passed (100% green).
  - Web navigation and card test cases updated.

---

## 11. Update (2026-10-05): AI Token Leak Eradication & Promotion of Google Antigravity (`agy` / Gemini) as Primary Fallback Brain

- **User Directive:** Investigate where and how AI token usage/costs leak across the codebase, eliminate wasteful token consumption, and promote Google Antigravity (`agy` / Gemini) to serve as the official Primary Fallback Brain when Codex is unavailable due to usage limits.
- **Forensic Token Leak Audit Findings:**
  1. **60-Second Quota-Exhausted Probing Loop:** Over 5,106 control files accumulated in `data/development/provider-recovery/control/` (1,339 actual `probe_model` attempts). Probes with `turn/start` were triggered in loops even when `read_limits` explicitly returned `"usedPercent": 100`, wasting retry allowances.
  2. **High Reasoning Effort Overhead (`reasoning_effort: "high"`):** All GPT routes in `provider_recovery.json` were configured with `"reasoning_effort": "high"`, burning 10,000–30,000 hidden reasoning tokens per trivial probe asking for `{"ok": true}`.
  3. **Massive Context Bundling in CLI Prompts:** Claude CLI invocations bundled `AGENTS.md` (64KB), `project_contract.json` (67KB), and up to 393KB of raw source code, saturating the token budget with ~140,000 tokens per single step.
  4. **High Concurrency Multiplier:** `max_parallel: 6` in `development_runner.json` multiplied simultaneous token expenditure $6\times$.
  5. **Deterministic Betting Engine Safety (0 Leak in Betting):** All live betting forecasts, distributions, Kelly sizing, and EvidenceGuard are 100% deterministic Python/scikit-learn with zero LLM dependencies and zero betting token consumption.
- **Remediation Implemented:**
  - In `config/provider_recovery.json`: Changed probe reasoning effort from `"high"` to `"low"`, set `probe_timeout_seconds: 60`, and added `"allow_temporary_agy": true`.
  - In `src/betting_engine/codex_control.py`: Changed `probe_model` default effort to `'low'` (line 348).
  - In `config/development_runner.json`: Reduced `max_parallel` and `max_workers` from 6 to 2.
- **Google Antigravity (`agy` / Gemini) Integration as Primary Fallback Brain:**
  - In `src/betting_engine/provider_recovery.py`:
    - Updated `validate_policy()` to accept `provider == 'agy'` and family `'gemini'` (alongside `'codex'` and `'claude'`).
    - Added `allow_temporary_agy` to optional policy fields.
  - In `config/provider_recovery.json`:
    - Added `gemini-3.8-flash-high` and `gemini-3.1-pro-high` as authorized primary fallback routes before Claude.
    - Added `"agy_binary": "/Users/belavarga/.local/bin/agy"`.
  - In `src/betting_engine/provider_runtime.py`:
    - Added `agy` probe execution in `RuntimeTransport.probe` using `/Users/belavarga/.local/bin/agy`.
    - Added `route['provider'] == 'agy'` candidate execution in `run_ai_step()` with sandboxed execution, file locking (`agy.lock`), and strict patch validation.
    - Updated router policy builder and tick state to report `agy_fallback`.
  - In `config/ai_delegation.json`: Designated `agy` with `"role": "primary_fallback"`, model `"gemini-3.8-flash-high"`.
  - In `config/project_contract.json`: Recorded `"ai_primary_fallback_directive_20261005"` with explicit user approval.
- **Verification:**
  - `tests/test_provider_recovery.py`: 97 / 97 passed (100% green).
  - `tests/test_claude_development.py`: 56 / 56 passed (100% green).
  - `tests/test_codex_control.py`: 47 / 47 passed (100% green).
  - `tests/test_ai_delegation.py`: 25 / 25 passed (100% green).
  - `npm test`: 294 / 294 passed (100% green).
  - Live engine status verified healthy.


