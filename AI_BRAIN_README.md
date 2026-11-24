# NovaFury External AI Supervisors

OpenAI and Anthropic Claude are optional final supervisors inside NovaFury's unified cross-brain coordinator. The local XGBoost, LightGBM/CatBoost, optional GRU/TCN and meta-labeling pipeline continues to run independently. Enabled external supervisors receive the common market snapshot and upstream scores; they cannot execute orders or override deterministic account and model checks.

## Connected decision chain

Rule signal -> shared ordered M5 candles -> XGBoost + LightGBM/CatBoost (+ optional GRU or TCN) -> meta-label trade-quality gate -> weighted XGBoost/ensemble consensus -> enabled OpenAI and/or Anthropic review -> deterministic account/spread/margin/position checks -> unified `/api/brain` approval -> existing MT5 order execution.

The default directional weights are 35% XGBoost and 65% multi-model ensemble. The combined score must meet `BRAIN_CONSENSUS_THRESHOLD` (default 60%). Each required engine must also pass its own configured threshold; meta-labeling is a separate trade-quality gate, not a directional voter. Both external AI providers are disabled by default.

When both OpenAI and Anthropic are enabled, **both must return a valid approval for the same direction**. A rejection, timeout, malformed response, or provider/API error blocks the AI-supervised automatic entry. When only one provider is enabled, it acts as the final AI gate. When both are disabled, the original local model pipeline is unchanged.

## Activate from the dashboard

1. Start NovaFury with `START_MT5_BOT.ps1`.
2. Open Dashboard -> Settings -> External AI supervisors.
3. Paste the OpenAI and/or Anthropic API key, choose the model ID, and turn on the provider.
4. Select **Save AI Providers**. The local bridge writes settings to `.env.local`; no bridge restart is required.

Default model IDs are `gpt-5.4-mini` for OpenAI and `claude-sonnet-5` for Anthropic. The model field accepts a provider-supported model ID. API usage is billed by the respective provider and is not included with NovaFury.

## Environment variables

```text
OPENAI_API_KEY=
OPENAI_ENABLED=0
OPENAI_MODEL=gpt-5.4-mini
ANTHROPIC_API_KEY=
ANTHROPIC_ENABLED=0
ANTHROPIC_MODEL=claude-sonnet-5
AI_REFRESH_SEC=30
AI_MIN_CONFIDENCE=0.72
AI_MAX_SPREAD_POINTS=80
AI_FAIL_CLOSED=1
AI_HTTP_TIMEOUT_SEC=12
```

`AI_BRAIN_ENABLED` and `AI_MODEL` remain supported as legacy OpenAI settings. The dashboard keeps those compatibility values synchronized.

## Provider endpoints

- `GET /api/providers` returns enabled/configured flags and model IDs, never API secrets.
- `POST /api/providers` stores provider settings in `.env.local`; it is restricted to loopback clients and localhost/127.0.0.1 dashboard origins.
- `GET /api/brain?symbol=XAUUSD&direction=buy` returns XGBoost, ensemble, both provider decisions, weighted consensus, checks, blockers and final approval.
- `GET /api/ai/status` returns safe provider metadata and latest supervisor state.

The local bridge uses the OpenAI Chat Completions API and Anthropic Messages API with Python's standard HTTP library; no extra SDK dependency is required. Each model response is parsed and validated against the same decision contract before it can affect the unified gate.

## Scope and safety

The external supervisors cannot change lot size, burst count, recovery multiplier, hard risk limits, or directly place/close orders. They cannot override deterministic spread/margin/trading-permission/position checks or failed required model votes. Existing strategy calculations, XGBoost/ensemble settings, MT5 order payloads and execution management are unchanged.

Keep `.env.local` private and never commit it. The dashboard sends new API keys only to the local bridge; the bridge never returns stored keys to the browser. The local env file is plain text on disk, so protect access to the Windows account and project folder.

Test provider integration and all entry/exit behavior on a demo account before live trading. External AI approval is not proof of profitability or a substitute for broker-side stop-loss and drawdown controls.
