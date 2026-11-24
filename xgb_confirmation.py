"""NovaFury XGBoost entry-confirmation engine.

This module is deliberately additive: it does not replace the existing EMA/RSI/
liquidity-sweep signal.  It learns a short-horizon directional probability from
live MT5 M5 candles and is used only as an automatic-entry confirmation layer.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from risk_engine import purged_expanding_walk_forward_splits


def _load_local_env() -> dict[str, str]:
    """Read project-local configuration before module constants are evaluated."""
    project_root = Path(__file__).resolve().parent.parent
    values: dict[str, str] = {}
    for filename in (".env.local", ".env"):
        path = project_root / filename
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                values.setdefault(key, value)
        except OSError:
            continue
    return values


_LOCAL_ENV = _load_local_env()


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or _LOCAL_ENV.get(name) or default

try:
    import xgboost as xgb
except Exception as exc:  # pragma: no cover - exercised on missing dependency
    xgb = None
    XGB_IMPORT_ERROR = str(exc)


ENABLED = _env("XGB_CONFIRMATION_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
THRESHOLD = float(_env("XGB_THRESHOLD", "0.58"))
TRAIN_BARS = int(_env("XGB_TRAIN_BARS", "600"))
HORIZON = int(_env("XGB_HORIZON", "3"))
REFRESH_SECONDS = max(300.0, float(_env("XGB_MODEL_REFRESH_SEC", "300")))
MIN_ROWS = int(_env("XGB_MIN_ROWS", "160"))
MIN_VALIDATION_AUC = min(0.70, max(0.50, float(_env("XGB_MIN_VALIDATION_AUC", _env("AI_MIN_VALIDATION_AUC", "0.55")))))

FEATURE_NAMES = [
    "ret1",
    "ret3",
    "ret5",
    "ema9_gap",
    "ema21_gap",
    "rsi14",
    "atr14_norm",
    "body_norm",
    "range_norm",
    "upper_wick_norm",
    "lower_wick_norm",
    "volume_ratio",
    "breakout_up",
    "breakout_down",
]

_state_lock = threading.RLock()
_state: dict[str, Any] = {
    "model": None,
    "symbol": None,
    "trained_at": 0.0,
    "bars_seen": 0,
    "last_bar_time": None,
    "validation": {},
    "version": 0,
    "last_error": "",
}


def _ema(values: np.ndarray, period: int) -> float:
    if len(values) == 0:
        return 0.0
    period = max(1, min(period, len(values)))
    alpha = 2.0 / (period + 1.0)
    ema = float(np.mean(values[:period]))
    for value in values[period:]:
        ema = alpha * float(value) + (1.0 - alpha) * ema
    return ema


def _rsi(values: np.ndarray, period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    diffs = np.diff(values[-(period + 1):])
    gains = np.clip(diffs, 0.0, None)
    losses = np.clip(-diffs, 0.0, None)
    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(values: np.ndarray, period: int = 14) -> float:
    highs = values[:, 1]
    lows = values[:, 2]
    closes = values[:, 3]
    prev_close = np.roll(closes, 1)
    prev_close[0] = closes[0]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev_close), np.abs(lows - prev_close)))
    if len(tr) < period:
        return float(np.mean(tr))
    return float(np.mean(tr[-period:]))


def _features_for_index(rows: np.ndarray, idx: int) -> list[float] | None:
    if idx < 25:
        return None
    window = rows[: idx + 1]
    opens = window[:, 0]
    highs = window[:, 1]
    lows = window[:, 2]
    closes = window[:, 3]
    volumes = window[:, 4]
    close = float(closes[-1])
    if close <= 0:
        return None

    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    atr = max(_atr(window, 14), close * 1e-6)
    rsi = _rsi(closes, 14)
    bar_range = max(float(highs[-1] - lows[-1]), close * 1e-8)
    body = float(closes[-1] - opens[-1])
    upper_wick = float(highs[-1] - max(opens[-1], closes[-1]))
    lower_wick = float(min(opens[-1], closes[-1]) - lows[-1])
    vol_base = float(np.mean(volumes[-21:-1])) if len(volumes) > 21 else float(np.mean(volumes[:-1]))
    vol_base = max(vol_base, 1.0)

    recent_high = float(np.max(highs[-6:-1]))
    recent_low = float(np.min(lows[-6:-1]))
    return [
        float((closes[-1] / closes[-2]) - 1.0),
        float((closes[-1] / closes[-4]) - 1.0),
        float((closes[-1] / closes[-6]) - 1.0),
        float((close - ema9) / atr),
        float((close - ema21) / atr),
        float(rsi / 100.0),
        float(atr / close),
        float(body / atr),
        float(bar_range / atr),
        float(upper_wick / atr),
        float(lower_wick / atr),
        float(volumes[-1] / vol_base),
        float((close - recent_high) / atr),
        float((close - recent_low) / atr),
    ]


def _build_dataset(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    if len(rows) < max(MIN_ROWS + HORIZON + 30, 220):
        return None
    start = max(25, len(rows) - TRAIN_BARS - HORIZON)
    end = len(rows) - HORIZON
    xs: list[list[float]] = []
    ys: list[int] = []
    origins: list[int] = []
    label_ends: list[int] = []
    for i in range(start, end):
        feats = _features_for_index(rows, i)
        if feats is None:
            continue
        close = float(rows[i, 3])
        future = float(rows[i + HORIZON, 3])
        atr = max(_atr(rows[: i + 1], 14), close * 1e-6)
        move = (future - close) / atr
        # Ignore tiny moves less than ~0.05 ATR; this keeps the training label
        # focused on meaningful short-horizon direction while preserving both
        # classes for the confirmation model.
        if abs(move) < 0.05:
            continue
        xs.append(feats)
        ys.append(1 if move > 0 else 0)
        origins.append(i)
        label_ends.append(i + HORIZON)
    if len(xs) < MIN_ROWS or len(set(ys)) < 2:
        return None
    return (np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.float32),
            np.asarray(origins, dtype=np.int32), np.asarray(label_ends, dtype=np.int32))


def _auc(y_true: np.ndarray, probability: np.ndarray) -> float | None:
    y = np.asarray(y_true, dtype=np.int32)
    p = np.asarray(probability, dtype=np.float64)
    positives = int(np.sum(y == 1))
    negatives = int(np.sum(y == 0))
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=np.float64)
    ranks[order] = np.arange(1, len(p) + 1, dtype=np.float64)
    sorted_p = p[order]
    left = 0
    while left < len(order):
        right = left + 1
        while right < len(order) and sorted_p[right] == sorted_p[left]:
            right += 1
        if right - left > 1:
            ranks[order[left:right]] = (left + 1 + right) / 2.0
        left = right
    return round(float((np.sum(ranks[y == 1]) - positives * (positives + 1) / 2.0) / (positives * negatives)), 4)


def _fit(symbol: str, rows: np.ndarray) -> dict[str, Any]:
    if xgb is None:
        raise RuntimeError(f"XGBoost import failed: {XGB_IMPORT_ERROR}")
    dataset = _build_dataset(rows)
    if dataset is None:
        raise RuntimeError("Not enough directional M5 history to train the XGBoost confirmation model")
    features, labels, origins, label_ends = dataset
    folds = purged_expanding_walk_forward_splits(
        origins.tolist(), label_ends.tolist(), min_train_samples=MIN_ROWS, min_validation_samples=30,
    )
    fold_aucs: list[float] = []
    validation_rows = 0
    for train_indices, validation_indices in folds:
        y_train = labels[train_indices]
        y_val = labels[validation_indices]
        if len(np.unique(y_train)) < 2 or len(np.unique(y_val)) < 2:
            continue
        dtrain = xgb.DMatrix(features[train_indices], label=y_train, feature_names=FEATURE_NAMES)
        dval = xgb.DMatrix(features[validation_indices], label=y_val, feature_names=FEATURE_NAMES)
        positives = float(np.sum(y_train == 1))
        negatives = float(np.sum(y_train == 0))
        params = {
            "objective": "binary:logistic", "eval_metric": "logloss",
            "max_depth": 4, "eta": 0.05, "subsample": 0.90,
            "colsample_bytree": 0.90, "min_child_weight": 3.0,
            "lambda": 1.0, "alpha": 0.05, "tree_method": "hist",
            "seed": 42, "nthread": 2,
            "scale_pos_weight": negatives / max(positives, 1.0),
        }
        validation_model = xgb.train(params, dtrain, num_boost_round=180, verbose_eval=False)
        predictions = validation_model.predict(dval)
        auc = _auc(y_val.astype(np.int32), predictions)
        if auc is not None:
            fold_aucs.append(float(auc))
            validation_rows += len(validation_indices)
    if len(fold_aucs) < 3:
        raise RuntimeError("XGBoost did not produce three valid purged walk-forward folds; collect more completed M5 history.")
    mean_auc = float(np.mean(fold_aucs))
    min_auc = float(np.min(fold_aucs))
    if mean_auc < MIN_VALIDATION_AUC or min_auc < 0.50:
        raise RuntimeError(
            f"XGBoost failed purged walk-forward validation: mean AUC {mean_auc:.3f}, "
            f"minimum fold AUC {min_auc:.3f}; required mean >= {MIN_VALIDATION_AUC:.2f} and every fold >= 0.50."
        )
    all_data = xgb.DMatrix(features, label=labels, feature_names=FEATURE_NAMES)
    positives = float(np.sum(labels == 1))
    negatives = float(np.sum(labels == 0))
    params = {
        "objective": "binary:logistic", "eval_metric": "logloss",
        "max_depth": 4, "eta": 0.05, "subsample": 0.90,
        "colsample_bytree": 0.90, "min_child_weight": 3.0,
        "lambda": 1.0, "alpha": 0.05, "tree_method": "hist",
        "seed": 42, "nthread": 2,
        "scale_pos_weight": negatives / max(positives, 1.0),
    }
    model = xgb.train(params, all_data, num_boost_round=180, verbose_eval=False)
    return {
        "model": model, "rows": len(labels), "symbol": symbol,
        "validationAuc": round(mean_auc, 4),
        "minimumFoldAuc": round(min_auc, 4),
        "foldAucs": [round(value, 4) for value in fold_aucs],
        "validationFolds": len(fold_aucs), "validationRows": validation_rows,
        "validationMode": "purged_expanding_walk_forward",
    }


def _prepare_rows(candles: list[dict[str, Any]]) -> np.ndarray:
    rows = []
    for c in candles:
        rows.append([
            float(c.get("open", 0.0)),
            float(c.get("high", 0.0)),
            float(c.get("low", 0.0)),
            float(c.get("close", 0.0)),
            float(c.get("volume", 0.0)),
        ])
    arr = np.asarray(rows, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 5:
        raise ValueError("Invalid candle array")
    return arr


def confirm(symbol: str, candles: list[dict[str, Any]], base_direction: str | None = None) -> dict[str, Any]:
    """Return the XGBoost probability used to confirm the existing bot signal."""
    now = time.time()
    base = {
        "enabled": bool(ENABLED),
        "available": False,
        "ready": False,
        "model": "xgboost-m5-direction",
        "version": 0,
        "symbol": symbol,
        "probabilityUp": 0.5,
        "probabilityDown": 0.5,
        "direction": base_direction or "neutral",
        "directionProbability": 0.5,
        "threshold": THRESHOLD,
        "confirmed": False,
        "trainedRows": 0,
        "ageSeconds": None,
        "error": "",
    }
    if not ENABLED:
        base["error"] = "XGBoost confirmation disabled"
        return base
    try:
        rows = _prepare_rows(candles)
        latest_bar_time = float(candles[-1].get("time", 0.0)) if candles else 0.0
        with _state_lock:
            model = _state.get("model")
            same_symbol = _state.get("symbol") == symbol
            stale = (now - float(_state.get("trained_at", 0.0))) >= REFRESH_SECONDS
            new_completed_bar = _state.get("last_bar_time") != latest_bar_time
            if model is None or not same_symbol or stale or new_completed_bar or int(_state.get("bars_seen", 0)) != len(rows):
                fitted = _fit(symbol, rows)
                model = fitted["model"]
                _state["model"] = model
                _state["symbol"] = symbol
                _state["trained_at"] = now
                _state["bars_seen"] = len(rows)
                _state["last_bar_time"] = latest_bar_time
                _state["validation"] = {k: fitted[k] for k in ("validationAuc", "minimumFoldAuc", "foldAucs", "validationFolds", "validationRows", "validationMode")}
                _state["version"] = int(_state.get("version", 0)) + 1
                _state["last_error"] = ""
            # The latest feature row must use the same feature engineering as training.
            latest = _features_for_index(rows, len(rows) - 1)
            if latest is None:
                raise RuntimeError("Not enough M5 bars for the latest XGBoost feature row")
            dcur = xgb.DMatrix(np.asarray([latest], dtype=np.float32), feature_names=FEATURE_NAMES)
            p_up = float(model.predict(dcur)[0])
            p_down = 1.0 - p_up
            direction = (base_direction or "neutral").lower()
            direction_probability = p_up if direction == "buy" else p_down if direction == "sell" else max(p_up, p_down)
            confirmed = direction in {"buy", "sell"} and direction_probability >= THRESHOLD
            cached_dataset = _build_dataset(rows)
            trained_rows = int(len(cached_dataset[1])) if cached_dataset is not None else 0
            age_seconds = max(0.0, now - float(_state["trained_at"]))
            return {
                **base,
                "available": True,
                "ready": True,
                "version": int(_state["version"]),
                "probabilityUp": round(p_up, 4),
                "probabilityDown": round(p_down, 4),
                "direction": direction,
                "directionProbability": round(direction_probability, 4),
                "confirmed": bool(confirmed),
                "trainedRows": trained_rows,
                **(_state.get("validation") or {}),
                "ageSeconds": round(age_seconds, 1),
            }
    except Exception as exc:
        with _state_lock:
            _state["last_error"] = str(exc)
        base["error"] = str(exc)
        return base
