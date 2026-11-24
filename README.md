# NovaFury AI Algo Bot — V2 MT5 Edition

NovaFury V2 upgrades the existing React dashboard and local Python MT5 bridge with a backend-enforced Balanced Risk Guardian, XAUUSD-only execution, broker-side initial stop-losses, AI-confirmed pullback recovery, capped profit-funded compounding, and verified ten-ticket basket execution. The existing V1 dashboard layout, XGBoost M5 model, LightGBM/CatBoost/meta ensemble, optional OpenAI/Claude supervisors, MT5 integration, and basket management are retained.

## Important defaults

- Instrument: XAUUSD only (the bridge resolves broker suffixes such as `XAUUSDm` and preserves exact broker case).
- Signal timeframe: M5; decisions use completed M5 candles, M1 entry-timing confirmation, and M15/H1 directional alignment.
- Auto Trading: DISARMED at every bridge startup. Arm it explicitly after confirming the connected demo account in the dashboard.
- Burst: exactly 10 tickets per logical basket. Orders are submitted sequentially with a shared risk budget and per-ticket risk recalculation; they are not exchange-atomic or guaranteed to fill simultaneously.
- Balanced risk defaults: 0.35% starting basket stop-risk, maximum 0.50% total allocation including a 0.10% recovery reserve; maximum 2% UTC-day equity loss; 5% persistent account high-water drawdown; 25% margin utilization; 400% minimum margin level.
- Stops: every market and recovery order includes a broker-side ATR-based SL. Smart trailing is supplementary, not a replacement for the initial SL.
- Recovery: only after a favourable excursion and a 25–65% retracement, followed by fresh XGBoost/ensemble/M15/H1 and enabled-supervisor confirmation; at most two recovery fills per basket, each with its own SL and 30-minute expiry. Recovery is not martingale or guaranteed loss recovery.
- Compounding: only realized net NovaFury profits can fund a capped 25% profit share, with a maximum 0.05 percentage-point bonus to the base basket risk. A losing historical net result does not fund a bonus.
- Basket exit: closes the full NovaFury basket after estimated net floating profit (profit + swap less configured close-cost estimate) meets the minimum target.

These are engineering defaults for demo validation, not proven optimal settings or a claim of profitability. Verify XAUUSD tick value, contract size, volume min/step, stop distances, commissions, and margin rules with your broker before any live use.

## Windows setup

1. Extract the ZIP into a normal writable folder.
2. Install MetaTrader 5 and log in to a DEMO account first. The launcher defaults to `C:\Program Files\MetaTrader 5`; edit `$Mt5Root` in `START_MT5_BOT.ps1` if your terminal is elsewhere.
3. Copy `.env.local.example` to `.env.local` if you need to adjust settings or configure provider API keys. Never share `.env.local`. Optional: populate `MT5_ALLOWED_LOGINS` with comma-separated permitted MT5 login IDs.
4. Open PowerShell in the extracted folder and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\START_MT5_BOT.ps1
```

The launcher creates `.venv`, installs `bridge\requirements.txt`, starts the local bridge and dashboard, and opens the browser. It writes PID files so the stop script targets this project only.

5. Verify that the dashboard shows the intended MT5 login/server, XAUUSD broker symbol, spread, Risk Guardian state, and AI model readiness. Auto Trading remains OFF until explicitly armed. First test manual and automatic behavior on demo only, with low account exposure.

Stop with:

```powershell
.\STOP_MT5_BOT.ps1
```

The stop script first asks the bridge to disarm and confirm cancellation of all NovaFury pending orders, including legacy V1 comment formats. It will leave the bridge running if that cleanup cannot be confirmed. Stopping the app does not close open positions; broker-side SLs remain with MT5. Inspect positions and pending orders in the terminal.

## Dashboard and bridge

- Dashboard: `http://127.0.0.1:3000`
- MT5 bridge: `http://127.0.0.1:8765`
- Health: `http://127.0.0.1:8765/api/health`
- Snapshot: `/api/snapshot?symbol=XAUUSD`
- Brain: `/api/brain?symbol=XAUUSD&direction=buy` (or `sell`)
- Risk: `/api/risk`

The bridge only accepts loopback clients and local dashboard origins. It monitors actual cash risk to every NovaFury broker-side position stop and pending-order stop; unknown or oversized managed exposure disarms the strategy and triggers a best-effort basket close. The 5% peak drawdown high-water mark persists across UTC-day rollovers. Every manual and automatic order is rechecked in Python; manual orders bypass AI approval only, never the Risk Guardian. The browser cannot set authoritative volume, bypass the account confirmation, or disable broker-side stop validation.

## AI configuration

XGBoost remains the primary M5 confirmation model. The configured LightGBM/CatBoost/meta ensemble is mandatory by default. Optional GRU/TCN support remains off unless separately installed and configured. OpenAI and Anthropic Claude can be enabled as independent supervisory vetoes in Dashboard → Settings. When an enabled provider fails, times out, or rejects, the candidate entry is blocked. Neither provider can override hard risk controls.

Tree models (XGBoost, LightGBM, CatBoost and the LightGBM meta-labeler) must pass three purged expanding-window walk-forward folds. Samples whose forward label horizon overlaps a validation fold are removed from that fold's training set. The configured mean AUC floor is 0.55 and every fold must be at least 0.50; this is a model-eligibility floor, not a profitability threshold. Optional GRU/TCN remains disabled by default and currently uses a chronological holdout rather than the tree-model purged walk-forward procedure. The first model-ready state is not evidence of profitability. The project does not include a verified profitable backtest. Demo forward-testing must include spread, commission, swap, slippage and stop execution before any live deployment.

## Tests and limitations

Run pure risk-helper tests without MT5:

```powershell
$env:PYTHONPATH = (Join-Path $PWD "bridge")
.\.venv\Scripts\python.exe -m unittest discover -s bridge\tests -v
```

Compile bridge Python files:

```powershell
.\.venv\Scripts\python.exe -c "import pathlib, py_compile; [py_compile.compile(str(p), doraise=True) for p in pathlib.Path('bridge').glob('*.py')]"
```

A connected MT5 demo terminal is required to validate actual order checks, stop placement, tick-value sizing, pending-order expiry, fill reconciliation, and basket exits. The frontend production build also requires Node.js dependencies to be installed by the launcher.
