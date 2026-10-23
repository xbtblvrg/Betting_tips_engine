# Autonomous Quant Engine — System Constitution & Prime Directives

This document is the supreme and unalterable Constitution of the Betting Quant Engine.
All previous historical sediment, temporary release notes (Releases 15–20), legacy incident logs,
and conflicting historical instructions are permanently superseded by these **10 Mandatory Commandments**.
Every AI system (Gemini Antigravity, Claude, Codex, Grok), background worker, and supervisor
must strictly comply with these directives without exception.

---

## The 10 Supreme Mandatory Commandments (A 10 Legfőbb Rendszerparancs)

### 1. Prime Financial Optimization: Min. 10.0% Weekly ROI & Daily Cumulative PnL Uptrend
- **Optimization Objective:** Continuous compound wealth growth of the aggregate betting portfolio.
  Mandatory Target: minimum **10.0% sustained total weekly portfolio ROI** and a consistent daily cumulative PnL uptrend.
- **Active Aggregation Mandate:** Merely clamping or letting negative submarkets bleed at minimum stake is strictly unacceptable because it drags down the total portfolio ROI. The total weekly ROI constraint requires active turnaround of all negative segments.
- **Epistemic Invariant:** Profit is a mathematical optimization objective, **never an established fact, guaranteed winner, or promise of zero possible loss**. Neither agents nor guards may claim guaranteed profits.

### 2. Strict Daily Volume Mandate: Min. 100 Funded Recommendations per Day
- **Volume Floor:** Szigorúan legalább **100 különböző, finanszírozott ajánlás Budapest naptári naponként** (legalább 700 db / gördülő hét), felső darabszám-plafon nélkül.
- **Non-Negotiable Constraint:** The 100 picks/day floor is a strict, non-negotiable minimum constraint, not an aspiration or ceiling. Volume must never be artificially choked or throttled. Any daily shortfall remains an unfulfilled engineering gap to be actively resolved through market discovery.

### 3. Zero Hardcoded Clamps (The Anti-Constant Mandate)
- **Anti-Constant Principle:** Arbitrary static odds ceilings (e.g., 2.80), stepped discrete filters, or static bookmaker blending are **strictly forbidden**.
- **Continuous Valuation:** Any contract with odds $\ge 1.40$ and positive mathematical expectation ($EV = P \cdot O - 1 > 0$) is fully eligible. High-odds underdogs ($O \ge 3.00, 4.00+$) represent valid alpha if verified by calibrated domain models.
- **Pure Independent Models:** Sports probabilities derive purely from domain models (`market_anchor_mode = 'shadow'`, `model_weight = 1.0`). Blending bookmaker vig into pre-game forecasts is forbidden.

### 4. Continuous, Risk-Adjusted Fractional Kelly Sizing ($0.50 – $8.00)
- **Continuous Sizing Formula:** Every stake is a continuous mathematical function of edge ($EV$), win probability, estimation uncertainty, and intra-game exposure.
- **Stake Bounds:** Stakes are strictly bounded within **[$0.50, $8.00]** ($0.50 floor, maximum $8.00 per bet).
- **No Daily Risk Ceiling:** The aggregate daily dollar limit is null (`daily_risk_limit = null`). It is strictly forbidden to restore any static daily dollar ceiling (e.g. $10). Capital is allocated dynamically across positive-EV opportunities.

### 5. Dynamic 72-Hour Liquidity Horizon & Deep Multi-Sport Coverage
- **72-Hour Dynamic Schedule Horizon:** The system dynamically scans all upcoming fixtures up to **72 hours** across all active liquid leagues: NFL, NHL, NBA, WNBA, MLB, NCAA sports, MLS, and Tennis.
- **High-Cadence Refresh:** Live odds and upcoming fixtures are scanned every 60 seconds; the full returned tournament catalogue is refreshed every 15 minutes. Piacokat időhiányra vagy szűk horizontra hivatkozva kihagyni tilos.

### 6. Dual Dynamic Capital Strategy (Alpha Acceleration, Interim Tourniquet & Demoted Quarantine)
- **Alpha Acceleration:** Segments demonstrating statistically robust positive ROI and positive CLV dynamically scale capital (**$2.50–$8.00**) and receive publication priority.
- **Phase 1: Interim Capital Defense ($0.50 Tourniquet):** When a segment or submarket first wobbles (rolling $\text{ROI} < -3\%$ or negative rolling PnL over 4+ games), the $0.50 stake clamp and EV hurdle (+3.5%) act as an **interim emergency liquidity tourniquet**, never a permanent resting state. The system enqueues autonomous AI repair in `learning_jobs`.
- **Phase 2: Demoted Market Quarantine & 3-Tier Closed-Loop Progression ($0.00 & Mandatory Re-Graduation):** When a market officially deteriorates into `demoted_markets`, **all real-money betting is strictly suspended ($0.00 stake)**. Clamping or letting demoted markets bleed real capital at $0.50 is strictly forbidden because it drags down total portfolio ROI and Win Rate. The market is transitioned to the **3-Tier Closed-Loop Recovery Progression**:
  1. **Tier 1 (R&D Historical Training & Edge Discovery):** The system trains and tunes candidate model archetypes (Copula Hazard, Negative Binomial, Situational Fatigue, Odds-Damped Kelly, ZIP) on historical settled matches (97k+ games). A candidate model must prove positive statistical edge ($ROI_{oos} \ge +3.5\%$, positive Brier skill, $DD < 25\%$) on out-of-sample historical data BEFORE admission to the incubator.
  2. **Tier 2 (Shadow Incubator Live Forward Validation - $0.00):** Only historically proven candidate models are admitted to the Shadow Incubator, formally bound to the demoted market (`incubation_phase = 'incubating_with_new_model'`). The new model paper-trades on upcoming live fixtures in real time with $0.00 virtual stakes.
  3. **Tier 3 (Production Live Re-Graduation - $0.50–$8.00):** After accumulating at least $N \ge 25$ live settled matches proving sustained positive profit ($ROI \ge +3.5\%$), the market officially graduates (`graduated_markets`), removes quarantine, and resumes live funded execution under continuous risk-adjusted Kelly sizing ($0.50–$8.00).

### 7. The Law of Market Preservation & Mandatory Crucible Re-Graduation
- **Permanent Ban Forbidden:** **Soha, semmilyen veszteséges sportágat, ligát vagy piacot tilos véglegesen törölni, kikapcsolni vagy eldobni.**
- **Autonomous Repair & Mandatory Re-Graduation:** Demoted markets remain active in the **Shadow Incubator** ($0.00 virtual stakes), continuing to track live odds and fixtures with their designated candidate models without risking user bankroll. Underperforming segments automatically enqueue root-cause research tasks in `learning_jobs`. **A demoted market can ONLY return to funded real-money execution after successfully passing the Walk-Forward Crucible backtest and proving positive ROI and Brier score improvement (Re-Graduation).**

### 8. Autonomous Structural & Capability Expansion Mandate ("Dugulásmentes Logika & Pozitív Fordulat")
- **Active Turnaround Principle:** When formulaic/in-sample parameter tuning is insufficient or cash leaks persist, the system is strictly mandated to execute a **3-Dimensional Capability Expansion Plan** designed to convert negative drag into positive alpha:
  - **Dimension A (Data):** Ingest verified free/public data features (starting goalies, probable pitchers, outdoor stadium wind).
  - **Dimension B (Portfolio Shield & Edge Restructuring):** Immediately quarantine leaky derivative submarkets (e.g., volatile NHL Unders at low odds, high-wind outdoor Overs) and tighten EV hurdles while engineering positive-edge structural replacements without choking overall volume.
  - **Dimension C (UI):** Render actionable badges, turnaround status, and verification indicators on the dashboard.

### 9. Zero Paid External Sources & Maximum Multi-AI Cognitive Leverage
- **Zero Paid Sources:** Substantial funds are already committed to the 4 configured AI systems. Proposing or subscribing to ANY other paid external sources, commercial data feeds, or paid betting/odds APIs (Pinnacle API, Sportradar, paid Swish, OddsJam) is **STRICTLY FORBIDDEN ($0.00 external data cost)**.
- **100% Free / Public Feeds:** Only free, public, official, or scrapable open feeds are authorized: NHL Official Web API (`api-web.nhle.com`), MLB Stats API (`statsapi.mlb.com`), Open-Meteo Weather API, Polymarket Open CLOB, and ESPN Public API.
- **Multi-AI Quant Council:** Continuous quantitative leverage must be extracted from the 4 paid AI brains:
  - **Gemini Antigravity (`agy`)**: Permanent primary quant brain, lead architect, and final release authority.
  - **Claude (`claude-opus-5-5`)**: Sports domain mechanics, lineup/rest variations, situational modeling.
  - **Codex (`gpt-6-astra`)**: Mathematical kernel formulations, calibration curves, statistical proof.
  - **Grok (`grok-4.7`)**: Adversarial red-team auditor, overfit prevention, stress-testing.

### 10. Immutable Betting Ledger & Manual Stake Execution Boundary
- **Ledger Immutability:** Every funded recommendation is an immutable system record in `system_decisions` and `bets`. Losses, original accepted odds, and model snapshots can **never be deleted, overwritten, or retrospectively edited**.
- **Execution Boundary:** The main recommendation list displays currently executable, server-reviewed instructions. The system tracks **assumed manual placement** (`assumed manual execution`); all bets are manually executed by the user on Stake. **Direct automated order submission is strictly prohibited and unauthorized.**
- **Zero-Regression Engineering Standard:** All code, query, model, and pipeline modifications must pass **100% of Python unit test suites and frontend Happy-DOM tests** before atomic production activation.

---

## Authority & Governance

1. **Lead Autonomous Brain:** Gemini Antigravity (`agy`) holds permanent standing autonomous code repair and development authority (2026-10-13, 2026-10-17). It plans, implements, verifies, and deploys fixes end-to-end to eliminate bottlenecks and optimize PnL.
2. **Persistent Requirement Ledger:** `config/project_contract.json` is the persistent system contract, strictly synchronized with these 10 Commandments.
3. **Execution List Integrity:** Keep the main website execution list clean, fast, and in English, using exact Stake sportsbook terminology.
