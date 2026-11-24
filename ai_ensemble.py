"""NovaFury multi-model confirmation brain for MT5 M5 candles.

Adds LightGBM, CatBoost, optional PyTorch GRU/TCN sequence learning and a
meta-labeling filter.  This module is a secondary entry gate: it never sends
orders, adjusts volume, or replaces the primary rule/XGBoost signal.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from risk_engine import purged_expanding_walk_forward_splits

try:
    from lightgbm import LGBMClassifier
except Exception as exc:  # optional runtime dependency
    LGBMClassifier = None
    LIGHTGBM_IMPORT_ERROR = str(exc)
else:
    LIGHTGBM_IMPORT_ERROR = ""

try:
    from catboost import CatBoostClassifier
except Exception as exc:  # optional runtime dependency
    CatBoostClassifier = None
    CATBOOST_IMPORT_ERROR = str(exc)
else:
    CATBOOST_IMPORT_ERROR = ""

try:
    import torch
    from torch import nn
except Exception as exc:  # optional heavy dependency
    torch = None
    nn = None
    TORCH_IMPORT_ERROR = str(exc)
else:
    TORCH_IMPORT_ERROR = ""


def _load_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for filename in (".env.local", ".env"):
        path = os.path.join(project_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as stream:
                for raw in stream:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key, value = key.strip(), value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                        value = value[1:-1]
                    out.setdefault(key, value)
        except OSError:
            continue
    return out


_FILE_ENV = _load_env_file()


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    return _FILE_ENV.get(name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


ENABLED = _bool("AI_ENSEMBLE_ENABLED", True)
MODEL_NAMES = [x.strip().lower() for x in _env("AI_ENSEMBLE_MODELS", "lightgbm,catboost,meta").split(",") if x.strip()]
MODEL_NAMES = list(dict.fromkeys(MODEL_NAMES))
DIRECTION_THRESHOLD = min(0.90, max(0.50, float(_env("AI_ENSEMBLE_THRESHOLD", "0.60"))))
META_THRESHOLD = min(0.90, max(0.50, float(_env("AI_META_THRESHOLD", "0.58"))))
TRAIN_BARS = max(500, min(10000, int(_env("AI_ENSEMBLE_TRAIN_BARS", "4000"))))
MIN_BARS = max(250, min(TRAIN_BARS, int(_env("AI_ENSEMBLE_MIN_BARS", "700"))))
HORIZON = max(2, min(8, int(_env("AI_ENSEMBLE_HORIZON", "3"))))
REFRESH_SECONDS = max(60.0, float(_env("AI_ENSEMBLE_REFRESH_SEC", "900")))
SEQUENCE_ENABLED = _bool("AI_SEQUENCE_ENABLED", False)
SEQUENCE_ARCH = _env("AI_SEQUENCE_ARCH", "gru").strip().lower()
if SEQUENCE_ARCH not in {"gru", "tcn"}:
    SEQUENCE_ARCH = "gru"
SEQUENCE_MIN_BARS = max(1200, min(10000, int(_env("AI_SEQUENCE_MIN_BARS", "2500"))))
SEQUENCE_LENGTH = max(16, min(64, int(_env("AI_SEQUENCE_LENGTH", "32"))))
SESSION_FEATURES = _bool("AI_SESSION_FEATURES", True)
MIN_WALK_FORWARD_AUC = min(0.70, max(0.50, float(_env("AI_MIN_VALIDATION_AUC", "0.55"))))

FEATURE_NAMES = [
    "ret1", "ret3", "ret5", "ema9_gap_atr", "ema21_gap_atr", "rsi14",
    "atr14_norm", "body_atr", "range_atr", "upper_wick_atr", "lower_wick_atr",
    "volume_ratio", "breakout_up_atr", "breakout_down_atr", "hour_sin",
    "hour_cos", "london_session", "new_york_session", "london_ny_overlap", "weekday",
]

_lock = threading.RLock()
_state: dict[str, Any] = {
    "symbol": None,
    "last_bar_time": None,
    "trained_at": 0.0,
    "models": {},
    "last_result": None,
    "last_error": "",
    "version": 0,
}


def _ema(values: np.ndarray, period: int) -> float:
    if len(values) == 0:
        return 0.0
    period = max(1, min(period, len(values)))
    alpha = 2.0 / (period + 1.0)
    value = float(np.mean(values[:period]))
    for item in values[period:]:
        value = alpha * float(item) + (1.0 - alpha) * value
    return value


def _rsi(values: np.ndarray, period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    diff = np.diff(values[-(period + 1):])
    gain = float(np.mean(np.clip(diff, 0.0, None)))
    loss = float(np.mean(np.clip(-diff, 0.0, None)))
    if loss <= 1e-12:
        return 100.0 if gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + gain / loss))


def _atr(rows: np.ndarray, period: int = 14) -> float:
    part = rows[-max(period, 1):]
    highs, lows, closes = part[:, 1], part[:, 2], part[:, 3]
    previous = np.roll(closes, 1)
    previous[0] = rows[-len(part) - 1, 3] if len(rows) > len(part) else closes[0]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - previous), np.abs(lows - previous)))
    return max(float(np.mean(tr)), float(rows[-1, 3]) * 1e-8)


def _session_features(timestamp: float) -> tuple[float, float, float, float, float, float]:
    """Approximate UTC session flags (London 07-16, NY 13-22); not broker-local."""
    try:
        dt = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        dt = datetime.now(timezone.utc)
    hour = dt.hour + dt.minute / 60.0
    london = 1.0 if 7.0 <= hour < 16.0 else 0.0
    new_york = 1.0 if 13.0 <= hour < 22.0 else 0.0
    overlap = 1.0 if 13.0 <= hour < 16.0 else 0.0
    return float(np.sin(2 * np.pi * hour / 24.0)), float(np.cos(2 * np.pi * hour / 24.0)), london, new_york, overlap, float(dt.weekday()) / 6.0


def _feature_at(rows: np.ndarray, index: int) -> np.ndarray | None:
    if index < 25:
        return None
    window = rows[: index + 1]
    opens, highs, lows, closes, volumes = (window[:, i] for i in range(5))
    close = float(closes[-1])
    if not np.isfinite(close) or close <= 0:
        return None
    atr = _atr(window, 14)
    ema9, ema21 = _ema(closes, 9), _ema(closes, 21)
    rsi = _rsi(closes, 14) / 100.0
    bar_range = max(float(highs[-1] - lows[-1]), close * 1e-8)
    body = float(closes[-1] - opens[-1])
    upper_wick = float(highs[-1] - max(opens[-1], closes[-1]))
    lower_wick = float(min(opens[-1], closes[-1]) - lows[-1])
    vol_base = float(np.mean(volumes[-21:-1])) if len(volumes) > 21 else float(np.mean(volumes[:-1]))
    vol_base = max(vol_base, 1.0)
    recent_high = float(np.max(highs[-6:-1]))
    recent_low = float(np.min(lows[-6:-1]))
    ret1 = (close / float(closes[-2]) - 1.0) if closes[-2] else 0.0
    ret3 = (close / float(closes[-4]) - 1.0) if closes[-4] else 0.0
    ret5 = (close / float(closes[-6]) - 1.0) if closes[-6] else 0.0
    timestamp = float(window[-1, 5]) if window.shape[1] > 5 else 0.0
    hour_sin, hour_cos, london, new_york, overlap, weekday = _session_features(timestamp)
    if not SESSION_FEATURES:
        hour_sin = hour_cos = london = new_york = overlap = weekday = 0.0
    features = np.asarray([
        ret1, ret3, ret5, (close - ema9) / atr, (close - ema21) / atr,
        rsi, atr / close, body / atr, bar_range / atr, upper_wick / atr,
        lower_wick / atr, float(volumes[-1]) / vol_base,
        (close - recent_high) / atr, (close - recent_low) / atr,
        hour_sin, hour_cos, london, new_york, overlap, weekday,
    ], dtype=np.float32)
    if not np.all(np.isfinite(features)):
        return None
    return features


def _prepare_rows(candles: list[dict[str, Any]]) -> np.ndarray:
    data: list[list[float]] = []
    for candle in candles:
        data.append([
            float(candle.get("open", 0.0)),
            float(candle.get("high", 0.0)),
            float(candle.get("low", 0.0)),
            float(candle.get("close", 0.0)),
            float(candle.get("volume", 0.0)),
            float(candle.get("time", 0.0)) / 1000.0,
        ])
    rows = np.asarray(data, dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] != 6 or len(rows) < MIN_BARS:
        raise RuntimeError(f"Need at least {MIN_BARS} M5 candles; received {len(rows)}")
    if not np.all(np.isfinite(rows)) or np.any(rows[:, 3] <= 0):
        raise RuntimeError("MT5 candle history contains invalid OHLC values")
    return rows


def _direction_at(rows: np.ndarray, index: int) -> str:
    close = float(rows[index, 3])
    window = rows[: index + 1]
    closes = window[:, 3]
    ema9, ema21 = _ema(closes, 9), _ema(closes, 21)
    rsi = _rsi(closes, 14)
    previous = rows[max(0, index - 4):index]
    sweep_low = bool(len(previous) and rows[index, 2] <= float(np.min(previous[:, 2])) and close > float(np.min(previous[:, 2])))
    sweep_high = bool(len(previous) and rows[index, 1] >= float(np.max(previous[:, 1])) and close < float(np.max(previous[:, 1])))
    bullish = ema9 >= ema21 and close >= ema9
    bearish = ema9 <= ema21 and close <= ema9
    if (rsi < 38.0 or sweep_low) and bullish:
        return "buy"
    if (rsi > 64.0 or sweep_high) and bearish:
        return "sell"
    return "buy" if ema9 > ema21 else "sell"


def _build_direction_dataset(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    end = len(rows) - HORIZON
    start = max(25, end - TRAIN_BARS)
    xs: list[np.ndarray] = []
    ys: list[int] = []
    origins: list[int] = []
    label_ends: list[int] = []
    for i in range(start, end):
        features = _feature_at(rows, i)
        if features is None:
            continue
        close = float(rows[i, 3])
        atr = _atr(rows[: i + 1], 14)
        move_atr = (float(rows[i + HORIZON, 3]) - close) / atr
        if abs(move_atr) < 0.05:
            continue
        xs.append(features)
        ys.append(1 if move_atr > 0 else 0)
        origins.append(i)
        label_ends.append(i + HORIZON)
    if len(xs) < 220 or len(set(ys)) < 2:
        return None
    return (np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.int32),
            np.asarray(origins, dtype=np.int32), np.asarray(label_ends, dtype=np.int32))


def _build_meta_dataset(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """Meta-label each historical primary-rule trade using conservative barriers."""
    horizon = max(HORIZON, 5)
    end = len(rows) - horizon
    start = max(30, end - TRAIN_BARS)
    xs: list[np.ndarray] = []
    ys: list[int] = []
    origins: list[int] = []
    label_ends: list[int] = []
    take_atr, stop_atr = 0.75, 0.65
    for i in range(start, end):
        features = _feature_at(rows, i)
        if features is None:
            continue
        side = 1.0 if _direction_at(rows, i) == "buy" else -1.0
        entry = float(rows[i, 3])
        atr = _atr(rows[: i + 1], 14)
        outcome = 0
        resolved = False
        for j in range(i + 1, min(i + horizon + 1, len(rows))):
            favorable = (float(rows[j, 1]) - entry) if side > 0 else (entry - float(rows[j, 2]))
            adverse = (entry - float(rows[j, 2])) if side > 0 else (float(rows[j, 1]) - entry)
            hit_target = favorable >= take_atr * atr
            hit_stop = adverse >= stop_atr * atr
            # Same-bar ambiguity is labeled as a loss; this avoids optimistic labels.
            if hit_stop and hit_target:
                outcome, resolved = 0, True
                break
            if hit_target:
                outcome, resolved = 1, True
                break
            if hit_stop:
                outcome, resolved = 0, True
                break
        if not resolved:
            final_return = side * (float(rows[i + horizon, 3]) - entry) / atr
            outcome = 1 if final_return >= 0.10 else 0
        xs.append(np.concatenate([features, np.asarray([side], dtype=np.float32)]))
        ys.append(outcome)
        origins.append(i)
        label_ends.append(i + horizon)
    if len(xs) < 220 or len(set(ys)) < 2:
        return None
    return (np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.int32),
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
    # Average ranks for ties.
    sorted_p = p[order]
    left = 0
    while left < len(order):
        right = left + 1
        while right < len(order) and sorted_p[right] == sorted_p[left]:
            right += 1
        if right - left > 1:
            ranks[order[left:right]] = (left + 1 + right) / 2.0
        left = right
    auc = (float(np.sum(ranks[y == 1])) - positives * (positives + 1) / 2.0) / (positives * negatives)
    return round(float(auc), 4)


def _make_tree_model(name: str):
    if name == "lightgbm":
        if LGBMClassifier is None:
            raise RuntimeError(f"LightGBM is not installed: {LIGHTGBM_IMPORT_ERROR}")
        return LGBMClassifier(
            objective="binary", n_estimators=140, learning_rate=0.035,
            max_depth=5, num_leaves=15, min_child_samples=24,
            subsample=0.85, subsample_freq=1, colsample_bytree=0.85,
            reg_lambda=2.0, reg_alpha=0.1, verbosity=-1, n_jobs=2,
            random_state=42,
        )
    if CatBoostClassifier is None:
        raise RuntimeError(f"CatBoost is not installed: {CATBOOST_IMPORT_ERROR}")
    return CatBoostClassifier(
        iterations=140, depth=5, learning_rate=0.035,
        loss_function="Logloss", eval_metric="AUC", l2_leaf_reg=5,
        random_seed=42, verbose=False, allow_writing_files=False,
        thread_count=2, random_strength=0.5,
    )


def _fit_tree(name: str, dataset: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]) -> tuple[Any, dict[str, Any]]:
    """Purged expanding-window walk-forward validation, then a final full-data fit.

    Each label observes future candles. Training rows whose label horizon touches
    a validation window are purged so the validation fold cannot leak outcomes.
    """
    x, y, origins, label_ends = dataset
    if not (len(x) == len(y) == len(origins) == len(label_ends)):
        raise RuntimeError(f"{name} dataset arrays have inconsistent lengths")
    folds = purged_expanding_walk_forward_splits(
        origins.tolist(), label_ends.tolist(), min_train_samples=120, min_validation_samples=30,
    )
    fold_aucs: list[float] = []
    validation_rows = 0
    for train_indices, validation_indices in folds:
        x_train, y_train = x[train_indices], y[train_indices]
        x_val, y_val = x[validation_indices], y[validation_indices]
        if len(np.unique(y_train)) < 2 or len(np.unique(y_val)) < 2:
            continue
        fold_model = _make_tree_model(name)
        fold_model.fit(x_train, y_train)
        probabilities = fold_model.predict_proba(x_val)[:, 1]
        auc = _auc(y_val, probabilities)
        if auc is not None:
            fold_aucs.append(float(auc))
            validation_rows += int(len(y_val))

    if len(fold_aucs) < 3:
        raise RuntimeError(
            f"{name} did not produce three valid purged walk-forward folds; "
            "collect more completed M5 history or inspect label balance."
        )
    mean_auc = float(np.mean(fold_aucs))
    min_auc = float(np.min(fold_aucs))
    if mean_auc < MIN_WALK_FORWARD_AUC or min_auc < 0.50:
        raise RuntimeError(
            f"{name} failed purged walk-forward validation: mean AUC {mean_auc:.3f}, "
            f"minimum fold AUC {min_auc:.3f}; required mean >= {MIN_WALK_FORWARD_AUC:.2f} "
            "and every fold >= 0.50. Model is not eligible to approve trades."
        )

    final_model = _make_tree_model(name)
    final_model.fit(x, y)
    return final_model, {
        "validationAuc": round(mean_auc, 4),
        "minimumFoldAuc": round(min_auc, 4),
        "foldAucs": [round(value, 4) for value in fold_aucs],
        "validationFolds": len(fold_aucs),
        "validationMode": "purged_expanding_walk_forward",
        "validationThreshold": MIN_WALK_FORWARD_AUC,
        "trainedRows": int(len(y)),
        "validationRows": int(validation_rows),
    }


def _fit_sequence(rows: np.ndarray, arch: str) -> tuple[Any, dict[str, Any]]:
    if torch is None or nn is None:
        raise RuntimeError(f"PyTorch is not installed: {TORCH_IMPORT_ERROR}")
    if len(rows) < SEQUENCE_MIN_BARS:
        raise RuntimeError(f"{arch.upper()} needs at least {SEQUENCE_MIN_BARS} M5 bars; received {len(rows)}")

    feature_rows: list[np.ndarray] = []
    labels: list[int] = []
    for i in range(25, len(rows) - HORIZON):
        feature = _feature_at(rows, i)
        if feature is None:
            continue
        move = float(rows[i + HORIZON, 3] - rows[i, 3]) / _atr(rows[: i + 1], 14)
        if abs(move) < 0.05:
            continue
        feature_rows.append(feature)
        labels.append(1 if move > 0 else 0)
    if len(feature_rows) < 600 or len(set(labels)) < 2:
        raise RuntimeError(f"Not enough labeled samples to train {arch.upper()}")

    feature_array = np.asarray(feature_rows, dtype=np.float32)
    label_array = np.asarray(labels, dtype=np.float32)
    seq_x: list[np.ndarray] = []
    seq_y: list[float] = []
    for i in range(SEQUENCE_LENGTH - 1, len(feature_array)):
        seq_x.append(feature_array[i - SEQUENCE_LENGTH + 1:i + 1])
        seq_y.append(float(label_array[i]))
    x = np.asarray(seq_x, dtype=np.float32)
    y = np.asarray(seq_y, dtype=np.float32)
    split = max(1, min(len(y) - 1, int(len(y) * 0.8)))
    x_train, y_train = x[:split], y[:split]
    x_val, y_val = x[split:], y[split:]
    mean = x_train.mean(axis=(0, 1), keepdims=True)
    std = x_train.std(axis=(0, 1), keepdims=True)
    std[std < 1e-6] = 1.0
    x_train = (x_train - mean) / std
    x_val = (x_val - mean) / std

    torch.manual_seed(42)
    torch.set_num_threads(2)

    if arch == "gru":
        class SequenceModel(nn.Module):
            def __init__(self, input_size: int):
                super().__init__()
                self.gru = nn.GRU(input_size, 24, batch_first=True)
                self.dropout = nn.Dropout(0.25)
                self.output = nn.Linear(24, 1)

            def forward(self, value):
                sequence, _ = self.gru(value)
                return self.output(self.dropout(sequence[:, -1, :])).squeeze(-1)
    else:
        class SequenceModel(nn.Module):
            def __init__(self, input_size: int):
                super().__init__()
                self.net = nn.Sequential(
                    nn.Conv1d(input_size, 32, kernel_size=3, padding=2, dilation=1),
                    nn.GELU(), nn.Dropout(0.20),
                    nn.Conv1d(32, 24, kernel_size=3, padding=4, dilation=2),
                    nn.GELU(), nn.Dropout(0.20),
                    nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Linear(24, 1),
                )

            def forward(self, value):
                return self.net(value.transpose(1, 2)).squeeze(-1)

    model = SequenceModel(feature_array.shape[1]).cpu()
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.02)
    loss_fn = nn.BCEWithLogitsLoss()
    tx = torch.from_numpy(x_train)
    ty = torch.from_numpy(y_train)
    model.train()
    for _epoch in range(8):
        permutation = torch.randperm(len(tx))
        for start in range(0, len(tx), 128):
            batch = permutation[start:start + 128]
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(tx[batch]), ty[batch])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

    model.eval()
    with torch.no_grad():
        val_logits = model(torch.from_numpy(x_val)) if len(x_val) else torch.empty(0)
        val_probability = torch.sigmoid(val_logits).cpu().numpy() if len(x_val) else np.asarray([], dtype=np.float32)
    auc = _auc(y_val.astype(np.int32), val_probability) if len(y_val) else None
    model_payload = {"model": model, "mean": mean.astype(np.float32), "std": std.astype(np.float32), "arch": arch}
    return model_payload, {"validationAuc": auc, "validationMode": "chronological_holdout_optional_sequence", "validationThreshold": MIN_WALK_FORWARD_AUC, "trainedRows": int(len(y_train)), "validationRows": int(len(y_val))}


def _predict_tree(model: Any, features: np.ndarray) -> float:
    return float(np.clip(model.predict_proba(np.asarray([features], dtype=np.float32))[0, 1], 0.0, 1.0))


def _predict_sequence(payload: dict[str, Any], rows: np.ndarray) -> float:
    model = payload["model"]
    latest: list[np.ndarray] = []
    start = max(25, len(rows) - SEQUENCE_LENGTH)
    for i in range(start, len(rows)):
        feature = _feature_at(rows, i)
        if feature is not None:
            latest.append(feature)
    if len(latest) != SEQUENCE_LENGTH:
        raise RuntimeError("Not enough current bars to create sequence-model inference window")
    sequence = np.asarray(latest, dtype=np.float32)[None, :, :]
    sequence = (sequence - payload["mean"]) / payload["std"]
    model.eval()
    with torch.no_grad():
        value = torch.sigmoid(model(torch.from_numpy(sequence))).item()
    return float(np.clip(value, 0.0, 1.0))


def _train(symbol: str, rows: np.ndarray) -> dict[str, Any]:
    trained: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    direction_data = _build_direction_dataset(rows)
    meta_data = _build_meta_dataset(rows)
    latest = _feature_at(rows, len(rows) - 1)
    if latest is None:
        raise RuntimeError("Unable to calculate current market features")

    selected = list(dict.fromkeys(MODEL_NAMES + ([SEQUENCE_ARCH] if SEQUENCE_ENABLED else [])))
    if not selected:
        raise RuntimeError("No models selected in AI_ENSEMBLE_MODELS")

    for name in selected:
        try:
            if name in {"lightgbm", "catboost"}:
                if direction_data is None:
                    raise RuntimeError("Not enough directional labels; collect more M5 history")
                model, stats = _fit_tree(name, direction_data)
                trained[name] = model
                probability_up = _predict_tree(model, latest)
                statuses[name] = {"available": True, **stats, "probabilityUp": round(probability_up, 4)}
            elif name == "meta":
                if meta_data is None:
                    raise RuntimeError("Not enough meta-label samples or both outcome classes are not present")
                model, stats = _fit_tree("lightgbm", meta_data)
                trained[name] = model
                side = 1.0 if _direction_at(rows, len(rows) - 1) == "buy" else -1.0
                current_meta = np.concatenate([latest, np.asarray([side], dtype=np.float32)])
                success_probability = _predict_tree(model, current_meta)
                statuses[name] = {"available": True, **stats, "successProbability": round(success_probability, 4)}
            elif name in {"gru", "tcn"}:
                if not SEQUENCE_ENABLED:
                    raise RuntimeError("Sequence model is disabled; set AI_SEQUENCE_ENABLED=1")
                payload, stats = _fit_sequence(rows, name)
                auc = stats.get("validationAuc")
                if auc is None or float(auc) < MIN_WALK_FORWARD_AUC:
                    raise RuntimeError(f"{name.upper()} chronological holdout AUC {auc} is below the configured {MIN_WALK_FORWARD_AUC:.2f} minimum; it cannot vote.")
                trained[name] = payload
                probability_up = _predict_sequence(payload, rows)
                statuses[name] = {"available": True, **stats, "probabilityUp": round(probability_up, 4)}
            else:
                raise RuntimeError(f"Unknown AI ensemble model: {name}")
        except Exception as exc:
            statuses[name] = {"available": False, "error": str(exc)}

    direction_names = [name for name in selected if name in {"lightgbm", "catboost", "gru", "tcn"}]
    direction_values: list[float] = []
    direction_weights: list[float] = []
    for name in direction_names:
        status = statuses.get(name, {})
        if not status.get("available"):
            continue
        auc = status.get("validationAuc")
        # Bounded weight adjustment is applied only after all purged folds pass.
        weight = max(0.05, min(0.50, float(auc if auc is not None else 0.50) - 0.45))
        status["ensembleWeight"] = round(weight, 4)
        direction_values.append(float(status["probabilityUp"]))
        direction_weights.append(weight)
    meta_probability = float(statuses.get("meta", {}).get("successProbability", 0.0))
    weighted_direction = float(np.average(direction_values, weights=direction_weights)) if direction_values else 0.5
    return {
        "models": trained,
        "statuses": statuses,
        "directionNames": direction_names,
        "directionProbabilityUp": weighted_direction,
        "metaProbability": meta_probability,
        "selected": selected,
        "trainedBars": int(len(rows)),
    }


def confirm(symbol: str, candles: list[dict[str, Any]], base_direction: str | None = None) -> dict[str, Any]:
    """Return ensemble confirmation for the existing rule direction."""
    direction = (base_direction or "neutral").strip().lower()
    result_base: dict[str, Any] = {
        "enabled": ENABLED,
        "available": False,
        "ready": False,
        "model": "lightgbm+catboost+meta-labeling" + (f"+{SEQUENCE_ARCH}" if SEQUENCE_ENABLED else ""),
        "symbol": symbol,
        "direction": direction,
        "directionProbability": 0.5,
        "probabilityUp": 0.5,
        "metaProbability": 0.0,
        "threshold": DIRECTION_THRESHOLD,
        "metaThreshold": META_THRESHOLD,
        "confirmed": False,
        "selectedModels": list(dict.fromkeys(MODEL_NAMES + ([SEQUENCE_ARCH] if SEQUENCE_ENABLED else []))),
        "trainedBars": 0,
        "version": 0,
        "ageSeconds": None,
        "models": {},
        "error": "",
    }
    if not ENABLED:
        result_base["error"] = "AI ensemble disabled"
        return result_base
    try:
        rows = _prepare_rows(candles)
        last_bar_time = float(rows[-1, 5]) if rows.shape[1] > 5 else None
        now = time.time()
        with _lock:
            cache_valid = (
                _state.get("symbol") == symbol
                and _state.get("last_bar_time") == last_bar_time
                and _state.get("last_result") is not None
                and (now - float(_state.get("trained_at", 0.0))) < REFRESH_SECONDS
            )
            if cache_valid:
                cached = dict(_state["last_result"])
                cached["ageSeconds"] = round(max(0.0, now - float(_state["trained_at"])), 1)
                cached["direction"] = direction
                current_features = _feature_at(rows, len(rows) - 1)
                if current_features is None:
                    raise RuntimeError("Unable to calculate current market features")

                # Re-score the changing current candle with cached fitted models;
                # training remains cached, but the live inference is not stale.
                cached_models = _state.get("models", {})
                cached_statuses = cached.get("models", {})
                current_probabilities: list[float] = []
                current_weights: list[float] = []
                for name in cached.get("selectedModels", []):
                    if name == "meta":
                        continue
                    fitted_model = cached_models.get(name)
                    model_status = cached_statuses.get(name, {})
                    if fitted_model is None or not model_status.get("available"):
                        continue
                    if name in {"gru", "tcn"}:
                        probability = _predict_sequence(fitted_model, rows)
                    else:
                        probability = _predict_tree(fitted_model, current_features)
                    model_status["probabilityUp"] = round(probability, 4)
                    current_probabilities.append(probability)
                    current_weights.append(float(model_status.get("ensembleWeight", 1.0)))
                p_up = float(np.average(current_probabilities, weights=current_weights)) if current_probabilities else 0.5
                cached["probabilityUp"] = round(p_up, 4)
                p_direction = p_up if direction == "buy" else (1.0 - p_up if direction == "sell" else 0.5)
                cached["directionProbability"] = round(p_direction, 4)
                meta_model = cached_models.get("meta")
                if meta_model is not None and direction in {"buy", "sell"}:
                    side = 1.0 if direction == "buy" else -1.0
                    current_meta = np.concatenate([current_features, np.asarray([side], dtype=np.float32)])
                    meta = _predict_tree(meta_model, current_meta)
                else:
                    meta = 0.0
                cached["metaProbability"] = round(meta, 4)
                cached["confirmed"] = bool(cached.get("ready") and direction in {"buy", "sell"} and p_direction >= DIRECTION_THRESHOLD and meta >= META_THRESHOLD)
                return cached

        fitted = _train(symbol, rows)
        latest = _feature_at(rows, len(rows) - 1)
        if latest is None:
            raise RuntimeError("Unable to calculate current market features")
        statuses = fitted["statuses"]
        directional_models = [
            name for name in fitted["directionNames"]
            if statuses.get(name, {}).get("available")
        ]
        selected = fitted["selected"]
        missing = [name for name in selected if not statuses.get(name, {}).get("available")]
        # Fail closed: every explicitly selected model must be loadable/trained.
        available = bool(directional_models) and not missing and ("meta" not in selected or statuses.get("meta", {}).get("available"))
        p_up = float(fitted["directionProbabilityUp"])
        p_direction = p_up if direction == "buy" else (1.0 - p_up if direction == "sell" else 0.5)
        meta_model = fitted["models"].get("meta")
        if meta_model is not None and direction in {"buy", "sell"}:
            side = 1.0 if direction == "buy" else -1.0
            current_meta = np.concatenate([latest, np.asarray([side], dtype=np.float32)])
            meta_probability = _predict_tree(meta_model, current_meta)
        else:
            meta_probability = 0.0
        confirmed = bool(available and direction in {"buy", "sell"} and p_direction >= DIRECTION_THRESHOLD and meta_probability >= META_THRESHOLD)
        errors = [f"{name}: {statuses[name].get('error')}" for name in missing]
        result = {
            **result_base,
            "available": available,
            "ready": available,
            "directionProbability": round(p_direction, 4),
            "probabilityUp": round(p_up, 4),
            "metaProbability": round(meta_probability, 4),
            "confirmed": confirmed,
            "trainedBars": fitted["trainedBars"],
            "version": int(_state.get("version", 0)) + 1,
            "ageSeconds": 0.0,
            "models": statuses,
            "error": "; ".join(errors) if errors else "",
        }
        with _lock:
            _state.update({
                "symbol": symbol,
                "last_bar_time": last_bar_time,
                "trained_at": now,
                "models": fitted["models"],
                "last_result": dict(result),
                "last_error": result["error"],
                "version": int(_state.get("version", 0)) + 1,
            })
        return result
    except Exception as exc:
        result_base["error"] = str(exc)
        with _lock:
            _state["last_error"] = str(exc)
        return result_base


def status() -> dict[str, Any]:
    with _lock:
        trained_at = float(_state.get("trained_at", 0.0))
        result = _state.get("last_result")
        return {
            "enabled": ENABLED,
            "configuredModels": list(dict.fromkeys(MODEL_NAMES + ([SEQUENCE_ARCH] if SEQUENCE_ENABLED else []))),
            "directionThreshold": DIRECTION_THRESHOLD,
            "metaThreshold": META_THRESHOLD,
            "trainBars": TRAIN_BARS,
            "minBars": MIN_BARS,
            "refreshSeconds": REFRESH_SECONDS,
            "minimumValidationAuc": MIN_WALK_FORWARD_AUC,
            "treeValidationMode": "purged_expanding_walk_forward",
            "sequenceEnabled": SEQUENCE_ENABLED,
            "sequenceArchitecture": SEQUENCE_ARCH if SEQUENCE_ENABLED else None,
            "ready": bool(result and result.get("ready")),
            "version": int(_state.get("version", 0)),
            "ageSeconds": round(max(0.0, time.time() - trained_at), 1) if trained_at else None,
            "lastError": str(_state.get("last_error", "")),
            "lastResult": result,
            "dependencies": {
                "lightgbm": LGBMClassifier is not None,
                "catboost": CatBoostClassifier is not None,
                "pytorch": torch is not None,
            },
        }
