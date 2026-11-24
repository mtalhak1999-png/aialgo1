# NovaFury Professional Dashboard

This build replaces the former phone-shaped terminal plus AI radar split screen with a responsive desktop/mobile trading workspace.

## Navigation

- Overview: MT5 account metrics, M5 candlestick chart, EMA overlays, unified AI result, execution controls and open positions.
- AI intelligence: XGBoost, LightGBM, CatBoost, optional GRU/TCN, meta-label, OpenAI supervisor and cross-brain votes.
- Positions: current MT5 positions and basket close action.
- Trade history: closed deals received from the local bridge.
- Settings: frontend controls and account/connection information.

## Integration notes

The redesign uses the existing local bridge endpoints and existing React order callbacks. No strategy or bridge execution logic was intentionally changed. Account equity, margin, free margin and margin level now display the broker snapshot values instead of placeholder zeros. Manual BUY/SELL are explicit overrides and continue to use the pre-existing manual order handler.

## Safety note

The existing default automatic-trading setting has not been changed by this visual redesign. The existing order path may not attach an initial stop-loss by default. Test on a demo account and verify risk settings before live use.
