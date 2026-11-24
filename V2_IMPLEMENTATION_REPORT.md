# NovaFury V2 — Implementation Report

## Scope

This package is an implementation update to the supplied NovaFury MT5 dashboard project, not only an architecture document. The V1 React dashboard structure, local Python MT5 bridge, XGBoost M5 confirmation, LightGBM/CatBoost/meta ensemble, optional OpenAI/Anthropic supervisors, and basket-management workflow have been retained and upgraded.

## Selected profile

- Risk: Balanced
- Instrument: XAUUSD only; broker suffixes such as `XAUUSDm` are supported
- Primary decision timeframe: completed M5 candles
- Entry timing/context: completed M1 plus M15/H1 directional alignment
- Burst: exactly 10 sequentially submitted tickets grouped as one logical basket
- Recovery: AI-confirmed pullback only; no loss-multiplying martingale
- Compounding: capped profit-funded bonus using realized net closed-trade profit
- Auto Trading: OFF/disarmed on every bridge startup

## Main implemented changes

### Risk Guardian and execution

- Risk decisions and volume sizing are enforced in the Python bridge, not trusted to React state or browser-provided lot size/order count.
- Balanced demo-profile defaults: 0.35% initial basket risk, 0.50% aggregate risk ceiling, 0.10% reserved for recovery, 2% UTC daily equity-loss limit, persistent 5% account peak drawdown, 25% margin-utilization ceiling, and 400% minimum margin level.
- Every initial and recovery order includes a broker-side ATR-based stop-loss.
- Lot sizing uses MT5 `order_calc_profit` and broker volume min/max/step data. If the broker minimum lot cannot fit the risk budget, the trade is rejected.
- The fixed 10-ticket burst recalculates risk and projected margin for each ticket, stops on a rejection or partial fill, and reconciles actual fills and broker-held stops.
- Disarming invalidates an in-flight burst and serializes pending-order cleanup with execution/recovery management.
- A watchdog checks positions and pending orders against actual broker-side stop risk, and monitors account drawdown and margin limits. Invalid or unverifiable exposure disarms the system and triggers a best-effort cleanup/close.
- Manual entries bypass AI approval only; they still require explicit confirmation and pass the same server-side risk checks.
- MT5 account login and server must match the dashboard-confirmed account. Optional login allow-list is available through `MT5_ALLOWED_LOGINS`.
- The bridge binds to loopback and rejects non-loopback requests and unapproved browser origins. It no longer uses wildcard CORS.

### Recovery and compounding

- Recovery requires an initial V2 parent basket, favourable movement of at least 0.75 ATR, a 25–65% retracement, fresh XGBoost/ensemble approval, M1/M15/H1 alignment and every enabled external supervisor's approval.
- Maximum two recovery fills per basket; each is limited to 0.05% equity risk within the aggregate basket cap, has its own broker stop-loss, and expires after 30 minutes.
- Recovery pending orders are verified and cancelled before basket-close operations. Cleanup failure prevents the close sequence from proceeding blindly.
- Compounding uses realized net P/L from fully closed NovaFury trades, including profit, swap, commission and fees. 25% of net profit can fund the bonus, capped at 0.05 percentage point of equity. Already committed profit is persisted per login/server and cannot be re-used for another basket.

### AI and reporting

- XGBoost remains the primary M5 confirmer and uses its configured 600-bar training window by default.
- XGBoost, LightGBM, CatBoost and meta-label tree models use three purged expanding-window walk-forward folds. Training samples whose forward label window overlaps validation are purged. The default model eligibility floor is mean AUC 0.55 with every fold at least 0.50.
- A model failing validation is unavailable for voting; an installed model is not treated as ready merely because it imports.
- OpenAI and Claude remain optional supervisory vetoes. When enabled, provider errors/timeouts/rejections fail closed; neither provider can override the Risk Guardian.
- The base M5 signal no longer emits a fixed 91–97% win-probability-looking number. It returns neutral for weak/ambiguous setups and exposes a measurable rule-quality score.
- Trade history reconstructs opening/closing prices and times by MT5 position ID, filters by the NovaFury magic number and records net profit including costs. Partial exits are not treated as fully closed cycles for compounding.

### Dashboard and launch scripts

- Existing V1 dashboard visual structure is retained, with live Risk Guardian status and model-fold metrics added.
- Auto Trading defaults OFF and has explicit account/login/server confirmation when arming.
- Manual BUY/SELL uses explicit confirmation and the bridge determines actual volume.
- Browser-side fixed/video lot ladders and synthetic trading simulation are removed from the live path.
- Launcher reads `.env.local` before applying defaults, starts bridge/dashboard, and polls health. Stop script targets only process IDs recorded by this project; it disarms and verifies pending-order cleanup before stopping the bridge.

## Balanced profile defaults

| Control | Default |
|---|---:|
| Initial basket risk | 0.35% equity |
| Maximum aggregate basket risk | 0.50% equity |
| Recovery reserve | 0.10% equity |
| Profit-funded bonus share | 25% of realized net profit |
| Maximum bonus | 0.05 percentage point of equity |
| Daily equity-loss halt | 2% |
| Persistent peak drawdown halt | 5% |
| Maximum margin utilization | 25% |
| Minimum margin level | 400% |
| Spread ceiling | 80 broker points |
| Burst tickets | 10 |
| Initial SL | At least 1.5 × completed M5 ATR(14), subject to broker stop/freeze distance |

These are proposed engineering defaults for demo testing. They are not proof of profitability or universal optimal settings. The XAUUSD contract, point size, tick value, volume steps, leverage, commission, swap and execution conditions differ by broker.

## Validation performed in this build environment

- Python syntax compilation: passed for `bridge/*.py`.
- Unit tests: 18 passed (`bridge/tests`). These cover risk sizing, instrument scope, ATR, volume-step flooring, purged walk-forward split behaviour, compounding caps, risk-state equity anchors, margin-level failure cases and broker-stop exposure handling for positions/pending orders.
- Bridge HTTP smoke test: `GET /api/health` returned version 2.0.
- Origin restriction smoke test: a request with an untrusted browser origin was rejected.
- TypeScript/TSX transpile syntax check: 12 source files, zero syntax diagnostics.

## Not verified here — required before live deployment

- The MetaTrader5 Python package/terminal is unavailable in this build environment, so no broker login, order_check, order_send, actual SL retention, pending expiry, partial fill or basket close was exercised against a demo account.
- Node dependencies could not be installed from the npm registry in this environment. A full Vite production build and TypeScript semantic type-check were therefore not completed; only TypeScript/TSX syntax transpilation was performed. The Windows launcher will install dependencies when run on a connected Windows machine.
- PowerShell is not installed in this environment, so `.ps1` scripts were source-reviewed but not executed.
- No verified backtest or profitability claim is included. Forward test on demo with actual spread, commission, swap, slippage and stop execution.

## First-run checklist

1. Extract the project to a writable folder on Windows.
2. Log in to an MT5 DEMO account. Confirm the terminal path in `START_MT5_BOT.ps1` or `.env.local`.
3. Copy `.env.local.example` to `.env.local` when custom settings are needed. Never share real provider keys or `.env.local`.
4. Set `ESTIMATED_ROUND_TRIP_COST_PER_LOT` to a realistic account-currency estimate for the full entry-plus-exit cost per lot. The supplied `0.0` is a placeholder, not an assertion that trading is free.
5. Ensure no legacy NovaFury magic-987654 positions or pending orders remain on other symbols, and ensure any existing XAUUSD positions have valid broker-side stops. V2 refuses to arm when legacy exposure is not protected or cannot be reconciled.
6. Run `START_MT5_BOT.ps1`. Confirm the dashboard login, server, XAUUSD broker symbol, spread, risk state and AI readiness.
7. Keep Auto Trading disarmed until a demo test plan has passed. Test manual/automatic entries, stop placement, recovery cancellation, partial fills and basket exits on demo.
8. Use `STOP_MT5_BOT.ps1` to disarm and cancel pending NovaFury orders. Stopping the application does not close open positions; broker-held stops remain with MT5.

This is an updated demo-validation build, not a guarantee of safe or profitable live trading.
