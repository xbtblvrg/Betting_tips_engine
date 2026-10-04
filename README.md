# Betting Tips Engine

Local sports research, model evaluation, Stake-market recommendations, immutable decision tracking, settlement, statistics and controlled learning. The web interface is in English. Bets are placed manually; the application never submits wagers.

**Current handoff:** [AGENTS.md](AGENTS.md), [project requirements](config/project_contract.json), [current status](docs/STATUS.hu.md), [development plan](config/development_plan.json). Historical status entries describe their own releases; the latest entry and active versioned configuration take precedence. A fresh installation uses [runtime_settings.json](config/runtime_settings.json), captured from the running system: 100 daily first-issuance target, USD1–5 per selection, no aggregate daily stake budget, probability-first selection and versioned dynamic exposure limits. A target is not a guarantee of available opportunities or profit.

## Clone and install

Fully supported deployment: **Apple Silicon macOS**, Homebrew Python **3.14** (`brew install python@3.14`), `uv`, Node.js **22+** for tests, Xcode Command Line Tools for the pinned SQLite runtime. Linux can run the foreground web/learning processes; macOS launchd installers and the native SQLite patch are platform-specific. Windows requires a Unix environment such as WSL and is not a tested deployment target.

```bash
git clone git@github.com:xbtblvrg/Betting_tips_engine.git
cd Betting_tips_engine
cp .env.example .env
# Edit .env: local paths, optional CLI paths and this machine's Tailscale hostname.
python3 scripts/bootstrap.py
npm ci --ignore-scripts
```

The bootstrap installs exact Python dependencies from `uv.lock`, installs the pinned project-local SQLite on macOS, verifies and copies the current model seed, initializes current settings **only for a new database**, and resolves this machine's AI CLI paths. It never overwrites an existing ledger, settings database or model file. Review its reported paths. CLI authentication is separate: log in to installed Codex, Claude, Antigravity (`agy`) and Grok before running their workers. No paid credits are purchased.

`configure_host` intentionally updates machine-specific executable and network fields in `config/`; these local differences are expected on a different host. Re-running bootstrap discovers newly installed CLIs. Do not run it on an active production deployment just to update dependencies; use the reviewed release workflow.

The `.env` is read as data, without shell execution or variable interpolation. Already exported environment variables take precedence. Public Stake/Polymarket read-only adapters do not need account trading credentials. No exchange keys or wallet private keys are necessary.

## Start

Foreground (leave the terminal running):

```bash
.venv/bin/python -m uvicorn betting_engine.server:app --host 127.0.0.1 --port 8765
```

Open **http://127.0.0.1:8765**. The first cycle downloads current sports data and quotes; allow it to complete. Do not run a second web/engine process against the same database.

For automatic restart and background operation on macOS, stop the foreground process, then:

```bash
.venv/bin/python scripts/service.py install
.venv/bin/python scripts/learning_service.py install
.venv/bin/python scripts/supervisor_service.py install
.venv/bin/python scripts/schedule_service.py
# Only after local AI CLIs are installed and authenticated:
.venv/bin/python scripts/development_service.py install
.venv/bin/python scripts/release_service.py install
```

LaunchAgents start at user login; they do not keep a powered-off or sleeping computer processing games. `service.py status` inspects the web service; its `stop` action stops it without deleting records. Equivalent `status`/`stop` actions exist for learning, supervisor, development and release. The schedule installer runs a timezone-aware job every minute; the daily schedule is refreshed around 09:00 Europe/Budapest.

For private remote access, install/login to Tailscale, set `BETTING_TAILSCALE_HOST` in `.env`, and run `tailscale serve --bg --https=8443 http://127.0.0.1:8765`. Use **this machine's** hostname, not the original Mac's address. No public funnel is required. See [Tailscale notes](docs/TAILSCALE.hu.md).

## Verify

```bash
curl --fail http://127.0.0.1:8765/api/health
ulimit -n 1024
.venv/bin/python -m pytest -q
npm test
```

Check the dashboard's publication time, market-source state, allocation funnel, statistics and tracking. `Ready to Bet` requires an exact committed issuance and current execution review. Changed cards retain their recorded event link with `View on Stake`; their original odds/stakes are not rewritten. AI worker heartbeats do not by themselves prove completed development or improved betting results.

## What the repository backs up

- Source, English web UI, tests/fixtures, dependency locks, current requirements and development/release instructions.
- Knowledge cards and derived literature notes used by the guard/research process.
- `runtime_seed/`: hash-verified current inference artifacts and their reports, without training archives or betting/account data. These are joblib/pickle files: load only from a trusted checkout. Keep the pinned Python/scikit-learn environment.
- `config/runtime_settings.json`: deployable current policy defaults, excluding bankroll and personal cost settings.

## What remains private and separate

`.env`, live SQLite databases and WAL files, bankroll/execution history, source-response caches, learned outcome history, raw books and old training/development archives are **not** committed. A clone starts a **new ledger and new outcome-learning history** while using the saved models and current selection policy. It cannot reproduce the original machine's historical WR/ROI without a separate private runtime-data migration. Never run two machines assuming the same manual execution ledger represents distinct bets.

For a full disaster-recovery migration, stop all writers, transfer `.env` and the complete active `data/` directory privately, then restart on the destination; preserve the ledger and paired WAL/SHM files together, or use SQLite's backup API. Never copy only a live `.sqlite3` file while writers run. Do not put that backup in Git. The source Git history is preserved.

To train new candidates, use the existing [controlled learning](docs/AUTONOMOUS_ENGINEERING.hu.md) workflow. Do not replace trained artifacts with guessed probabilities or treat a successful clone/test run as evidence of betting profitability.

The public native-schema recovery fixtures required by the regression suite are included in a hash-verified, bounded test archive. Tests extract it into one temporary directory, removed when the test process exits; they do not populate production `data/`. One optional replay of the original private decision ledger is skipped when that private input is absent. Its synthetic boundary tests still run. The private ledger is intentionally not a Git fixture.
