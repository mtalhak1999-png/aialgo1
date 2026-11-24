# NovaFury Multi-Model AI Brain (XAUUSD M5)

This is an additive confirmation layer for the existing MT5 bot. It does not place orders, change lot sizes, set recovery multipliers, or replace the existing EMA/RSI/liquidity-sweep signal or XGBoost confirmation.

## Unified cross-brain decision pipeline

1. The existing EMA/RSI/liquidity-sweep strategy proposes BUY/SELL/neutral.
2. The bridge fetches one ordered M5 candle history batch and shares it with XGBoost and the multi-model ensemble.
3. XGBoost predicts direction. LightGBM and CatBoost independently predict direction; optionally one PyTorch GRU or TCN contributes a sequence vote.
4. The ensemble combines its available directional models using their chronological validation diagnostics. The meta-label model separately scores whether the proposed direction is likely to pass conservative ATR profit/stop barriers.
5. The bridge calculates a weighted cross-brain consensus from XGBoost and the multi-model ensemble. Defaults are 35% XGBoost and 65% ensemble, normalized over the configured required voters.
6. When enabled, OpenAI and/or Anthropic Claude receive the common market snapshot, XGBoost result, each ensemble model's status/scores, meta-label probability, and weighted consensus. Each acts as an independent final supervisory regime/risk gate; when both are enabled, both must approve the same direction.
7. `/api/brain` returns all individual outputs plus one final approval and explicit blocker list. The dashboard uses this approval for automatic entries, and the Python `/api/order` endpoint recalculates the same brain verdict immediately before an automatic order is sent. A failed, stale, unavailable, disabled-required, or disagreeing brain blocks auto-entry.

## Unified coordinator configuration

```text
BRAIN_XGB_WEIGHT=0.35
BRAIN_ENSEMBLE_WEIGHT=0.65
BRAIN_CONSENSUS_THRESHOLD=0.60
BRAIN_REQUIRE_XGB=1
BRAIN_REQUIRE_ENSEMBLE=1
BRAIN_MAX_SPREAD_POINTS=80
BRAIN_MIN_MARGIN_LEVEL=150
```

These values are loaded by the bridge from `.env.local`. Configure `OPENAI_API_KEY` / `OPENAI_ENABLED=1` and/or `ANTHROPIC_API_KEY` / `ANTHROPIC_ENABLED=1` to connect the external supervisors. Both remain off by default because the project archive does not contain API keys. They can also be configured from Dashboard -> Settings. GRU/TCN remains optional; choose one architecture at a time and install PyTorch.

## Default configuration

```text
AI_ENSEMBLE_ENABLED=1
AI_ENSEMBLE_MODELS=lightgbm,catboost,meta
AI_ENSEMBLE_THRESHOLD=0.60
AI_META_THRESHOLD=0.58
AI_ENSEMBLE_TRAIN_BARS=4000
AI_ENSEMBLE_MIN_BARS=700
AI_ENSEMBLE_HORIZON=3
AI_ENSEMBLE_REFRESH_SEC=900
AI_SESSION_FEATURES=1
AI_SEQUENCE_ENABLED=0
AI_SEQUENCE_ARCH=gru
AI_SEQUENCE_MIN_BARS=2500
AI_SEQUENCE_LENGTH=32
```

Copy `.env.local.example` to `.env.local` and adjust the settings there. The bridge reads `.env.local` locally; no model credentials or parameters are placed in browser JavaScript.

## Model roles

- **LightGBM:** fast tabular direction classifier, constrained to shallow trees and regularized leaves.
- **CatBoost:** independent tabular direction classifier, also constrained to shallow trees and regularization.
- **Meta-labeling:** uses the primary rule direction as the proposed side and trains a separate LightGBM score model. Historical labels use ATR-based profit/stop barriers; if the same candle reaches both barriers, it is conservatively labeled as a loss.
- **GRU / TCN:** optional PyTorch sequence model. It is disabled by default and requires substantially more M5 history than the tree models. Only one sequence architecture is selected at a time.

Feature inputs include normalized returns, EMA gaps, RSI, ATR, candle body/range/wicks, tick-volume ratio, recent breakout distances and approximate UTC session flags. Session flags are approximate UTC windows, not broker-local session calendars.

## Install the base ensemble

The standard launcher installs `bridge/requirements.txt`, which includes LightGBM and CatBoost:

```powershell
.\.venv\Scripts\python.exe -m pip install -r bridge\requirements.txt
```

## Enable GRU or TCN (optional)

Install the additional PyTorch dependency using the wheel appropriate for the machine (CPU or CUDA):

```powershell
.\.venv\Scripts\python.exe -m pip install -r bridge\requirements-ai-deep.txt
```

Then set one of the following in `.env.local`:

```text
AI_SEQUENCE_ENABLED=1
AI_SEQUENCE_ARCH=gru
```

or

```text
AI_SEQUENCE_ENABLED=1
AI_SEQUENCE_ARCH=tcn
```

For this sequence option, the default minimum is 2,500 M5 bars. More data and walk-forward testing are preferable; the minimum is only a technical training guard, not a guarantee of statistical reliability. The PyTorch model trains on CPU and can take longer on initial training.

## Endpoints

- `GET /api/ensemble?symbol=XAUUSD&direction=buy` — trains/caches and returns current confirmation.
- `GET /api/ensemble/status` — reports configuration and latest training status.
- `GET /api/brain?symbol=XAUUSD&direction=buy` — unified coordinator; returns XGBoost, ensemble, OpenAI, weighted consensus, safety checks and final approval.

The dashboard displays directional probability, meta success probability, selected models, validation AUC and error status. Validation AUC is a chronological holdout diagnostic, not a guaranteed live win rate.

## Fail-closed behavior

When `AI_ENSEMBLE_ENABLED=1` and `BRAIN_REQUIRE_ENSEMBLE=1`, the unified automatic-entry gate blocks if any explicitly selected model is unavailable, history is insufficient, training fails, direction does not match, or either the direction/meta threshold or weighted cross-brain threshold is missed. A model configured as required cannot be silently skipped if its engine is disabled or unavailable. `BRAIN_REQUIRE_XGB` and `BRAIN_REQUIRE_ENSEMBLE` control which directional engines are required. OpenAI and/or Anthropic are additional final supervisory gates only when their provider switches are enabled; neither can override deterministic account/model blockers. If both are enabled, both must approve. Automatic orders are revalidated in the bridge immediately before execution. Manual burst buttons remain an explicit manual override and retain their direct execution behavior. Test on a demo account and use independent broker-side stop-loss and drawdown protections before live trading.
