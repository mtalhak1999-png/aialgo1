# NovaFury V2 — MT5 live-bridge notes

This document describes the V2 code now included in this package. It supersedes the earlier V1 live notes. Start in a MetaTrader 5 DEMO account only.

## Execution profile

- `SYMBOL=XAUUSD`, broker suffix resolution supported. All other symbols are rejected.
- `TIMEFRAME=M5`; signal and model decisions use completed candles. M15 and H1 EMA trend alignment are additional filters.
- Fixed ten-ticket logical basket. The bridge submits tickets sequentially, recalculates volume against remaining aggregate cash risk before each ticket, and stops on rejection/partial fill/disarm/account mismatch.
- All market and recovery requests include broker-side ATR-based SL. Missing or incorrectly oriented stops cause disarming and attempted emergency closure.
- `order_check` must return a successful preflight before `order_send`. Partial fills stop the burst and are counted as exposure for reconciliation.

## Balanced Risk Guardian defaults

M5 is the primary signal/model timeframe. M1 supplies a completed-candle entry-timing confirmation, while M15 and H1 must agree with the proposed direction. These timeframe checks are additional to XGBoost and the validated ensemble; no timeframe or AI vote can override the Risk Guardian.

| Control | Default |
|---|---:|
| Starting basket risk | 0.35% of equity |
| Maximum base basket allocation | 0.40% of equity |
| Recovery reserve | 0.10% of equity |
| Absolute basket allocation ceiling | 0.50% of equity |
| Daily equity loss halt | 2% from UTC-day start equity |
| Peak-equity drawdown halt | 5% from recorded peak |
| Margin utilisation ceiling | 25% |
| Minimum margin level | 400% |
| Initial SL distance | max(1.5 × M5 ATR(14), broker stop/freeze minimum + buffer) |

Actual lot size is calculated from MT5 `order_calc_profit` and broker `volume_min`, `volume_max`, and `volume_step`. If broker minimum volume exceeds the approved cash-risk limit, the entry is rejected. Each ticket also checks projected margin.

Risk-state data is stored locally in `data/risk_state.json`, tied to MT5 login/server. An initialization marker detects a deleted state file; missing/corrupt state or account mismatch fails closed. A valid daily/peak-loss anchor cannot be manually reset; it rolls automatically at the next UTC day boundary so the loss stop cannot be bypassed. `/api/risk/reset-day` is an administrative recovery path only for a corrupt or account-mismatched state, requires the confirmed login, verifies recovery-order cleanup, and refuses while NovaFury positions remain open. Keep the data folder and do not delete or edit the state file casually.

## Capped compounding

Only reconstructed NovaFury realized net P/L (profit + swap + commission + fees) can fund the compound pool. The configured share is 25%; the bonus is capped at 0.05 percentage point of equity and the combined base allocation cannot exceed the 0.40% base/recovery-reserved ceiling. It is not a literal doubling or martingale ladder.

## AI-confirmed pullback recovery

Recovery requires a favourable excursion of at least 0.75 ATR, a 25–65% retracement from that excursion, fresh mandatory XGBoost and ensemble approval, M15/H1 alignment, all enabled OpenAI/Claude supervisors, and the same risk/spread/margin controls as an initial trade. At most two recovery entry orders are allowed per basket. Each is limited to a 0.05% equity risk budget, has its own ATR-based broker SL and 30-minute expiry. It does not increase size to offset a losing position. Recovery stops when the thesis or any hard gate fails.

The bridge serializes recovery, entry and basket-exit transitions. Disarming cancels recovery orders and verifies broker state. If cancellation cannot be confirmed, the stop script deliberately leaves the bridge running and reports the unresolved condition.

## Basket exit and trailing

The smart basket manager closes the full NovaFury symbol basket when combined floating profit + swap less `ESTIMATED_ROUND_TRIP_COST_PER_LOT` meets `BASKET_MIN_NET_PROFIT`. Configure the estimated full round-trip entry+exit fees/commission per lot in account currency; the same estimate is reserved in stop-risk sizing and subtracted from basket exit P/L. Zero is only a placeholder assumption, not a claim that trading is free. It cancels and verifies recovery pending-order removal before closing.

Smart per-position trailing is supplementary. Initial SL remains on the broker even when the bridge is stopped. Market close requests can be partial or rejected; the bridge reports partial/failed closes for reconciliation and retries the basket manager on later cycles.

## Security and endpoints

The bridge binds to `127.0.0.1` only and rejects non-loopback requests and unapproved origins. POST bodies must be JSON and are limited to 64 KB. Manual order requests require `manualConfirm` and the current dashboard-confirmed MT5 login; automatic requests additionally require the in-process Auto Trading arm and a fresh passing server-side brain decision.

Relevant endpoints:

- `GET /api/health` — bridge health.
- `GET /api/status` — terminal, account, risk and runtime state.
- `GET /api/snapshot?symbol=XAUUSD` — account, quote, positions and history.
- `GET /api/brain?symbol=XAUUSD&direction=buy` — unified model/supervisor decision.
- `GET /api/risk` — Risk Guardian telemetry.
- `POST /api/arm` — arm after login/server confirmation and existing-stop verification.
- `POST /api/disarm` — disarm and cancel recovery orders.
- `POST /api/order` — manual or automatically supervised ten-ticket basket.
- `POST /api/close_all` — cancel recovery orders then close NovaFury positions.
- `POST /api/risk/reset-day` — explicitly reset risk state after login confirmation.

## Validation

Unit tests cover pure risk helpers only. They do not emulate a broker. Actual XAUUSD contract specifications, preflight retcodes, stop retention, order fill/partial-fill behavior, expiry, and basket closes must be validated on the broker's demo server. No profitability or win-rate claim is made by this package.
