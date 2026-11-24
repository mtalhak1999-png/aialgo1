"""NovaFury V2 balanced risk sizing helpers.

Pure calculations live here so sizing can be tested without a connected MT5 terminal.
All money values are in the account deposit currency.
"""
from __future__ import annotations

import math
from typing import Any, Callable

PROFILE = {
    "name": "balanced",
    "base_basket_risk_pct": 0.0035,
    "max_basket_risk_pct": 0.0050,
    "recovery_reserve_pct": 0.0010,
    "max_daily_loss_pct": 0.0200,
    "max_peak_drawdown_pct": 0.0500,
    "max_margin_utilization_pct": 0.25,
    "minimum_margin_level_pct": 400.0,
    "burst_orders": 10,
    "stop_atr_multiplier": 1.5,
    "atr_period": 14,
    "maximum_compound_bonus_pct": 0.0005,
    "compound_profit_share": 0.25,
    "max_recovery_fills": 2,
}


def ensure_xauusd_symbol(requested: str) -> str:
    # Validate case-insensitively, but preserve the broker's exact symbol spelling.
    # Some MT5 brokers expose case-sensitive suffixes such as XAUUSDm.
    raw = (requested or "XAUUSD").strip()
    value = raw.upper()
    if not value.startswith("XAUUSD"):
        raise ValueError("NovaFury V2 is configured for XAUUSD only.")
    if not value.replace(".", "").replace("_", "").isalnum():
        raise ValueError("Invalid XAUUSD broker symbol name.")
    return raw


def calculate_atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    """Simple mean true range using completed candles only."""
    rows = candles[-(period + 1):]
    if len(rows) < period + 1:
        raise ValueError(f"At least {period + 1} completed candles are required for ATR sizing.")
    ranges: list[float] = []
    previous_close = float(rows[0]["close"])
    for row in rows[1:]:
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        if not math.isfinite(true_range) or true_range <= 0:
            raise ValueError("Invalid candle range in ATR calculation.")
        ranges.append(true_range)
        previous_close = close
    atr = sum(ranges[-period:]) / min(period, len(ranges))
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("ATR calculation returned a non-positive value.")
    return atr


def floor_to_step(value: float, step: float) -> float:
    if not math.isfinite(value) or value <= 0 or not math.isfinite(step) or step <= 0:
        return 0.0
    # A tiny epsilon only compensates for binary floating-point representation.
    return math.floor((value + step * 1e-9) / step) * step


def size_equal_burst(
    *,
    order_calc_profit: Callable[[int, str, float, float, float], float | None],
    order_type: int,
    symbol: str,
    entry_price: float,
    stop_price: float,
    equity: float,
    risk_pct: float,
    order_count: int,
    volume_min: float,
    volume_max: float,
    volume_step: float,
    estimated_cost_per_lot: float = 0.0,
) -> dict[str, float]:
    """Return equal per-ticket volume whose stop loss plus estimated round-trip costs fit the budget."""
    values = (entry_price, stop_price, equity, risk_pct, volume_min, volume_max, volume_step, estimated_cost_per_lot)
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("Non-finite value supplied to risk sizing.")
    if equity <= 0 or risk_pct <= 0 or order_count < 1 or entry_price <= 0 or stop_price <= 0:
        raise ValueError("Invalid inputs for risk sizing.")
    if volume_min <= 0 or volume_max < volume_min or volume_step <= 0 or estimated_cost_per_lot < 0:
        raise ValueError("Broker volume specifications or estimated trading costs are invalid.")
    budget = equity * risk_pct
    one_lot_result = order_calc_profit(order_type, symbol, 1.0, entry_price, stop_price)
    if one_lot_result is None:
        raise ValueError("Broker could not calculate stop-loss cash risk for one lot.")
    loss_per_lot = abs(float(one_lot_result)) + float(estimated_cost_per_lot)
    if not math.isfinite(loss_per_lot) or loss_per_lot <= 0:
        raise ValueError("Broker returned invalid stop-loss cash risk.")
    total_lots = budget / loss_per_lot
    per_ticket = floor_to_step(total_lots / order_count, volume_step)
    if per_ticket < volume_min:
        raise ValueError(
            f"Risk budget supports less than broker minimum volume per order ({volume_min:g} lots)."
        )
    if per_ticket > volume_max:
        per_ticket = floor_to_step(volume_max, volume_step)
    aggregate_lots = per_ticket * order_count
    aggregate_risk = loss_per_lot * aggregate_lots
    # Never round upward into a breach of the requested basket budget.
    while per_ticket >= volume_min and aggregate_risk > budget + 1e-8:
        per_ticket = floor_to_step(per_ticket - volume_step, volume_step)
        aggregate_lots = per_ticket * order_count
        aggregate_risk = loss_per_lot * aggregate_lots
    if per_ticket < volume_min:
        raise ValueError("Broker minimum lot would exceed the configured basket risk budget.")
    return {
        "volumePerOrder": round(per_ticket, 8),
        "aggregateVolume": round(aggregate_lots, 8),
        "riskBudget": round(budget, 2),
        "estimatedStopLoss": round(aggregate_risk, 2),
        "lossPerLot": round(loss_per_lot, 8),
        "riskPct": risk_pct,
    }



def update_equity_anchors(state: dict[str, Any], equity: float, day_key: str) -> dict[str, Any]:
    """Roll the daily start anchor while preserving the account high-water mark."""
    value = float(equity)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Equity anchor must be a finite positive amount.")
    updated = dict(state)
    if updated.get("dayKey") != str(day_key):
        updated["dayKey"] = str(day_key)
        updated["dayStartEquity"] = value
    peak = float(updated.get("peakEquity", value))
    if not math.isfinite(peak) or peak <= 0:
        raise ValueError("Stored peak equity is invalid.")
    if value > peak:
        updated["peakEquity"] = value
    return updated


def capped_compound_bonus(
    *, equity: float, realized_net_profit: float, profit_share: float,
    already_committed: float, maximum_bonus_pct: float,
) -> dict[str, float]:
    """Calculate a capped bonus from uncommitted realized net profits only."""
    values = (equity, realized_net_profit, profit_share, already_committed, maximum_bonus_pct)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("Non-finite input to compounding calculation.")
    if equity <= 0 or not 0 <= profit_share <= 1 or already_committed < 0 or maximum_bonus_pct < 0:
        raise ValueError("Invalid compounding configuration or account values.")
    earned_pool = max(0.0, realized_net_profit * profit_share)
    available_pool = max(0.0, earned_pool - already_committed)
    bonus_cash = min(equity * maximum_bonus_pct, available_pool)
    return {
        "earnedPool": round(earned_pool, 2),
        "alreadyCommitted": round(already_committed, 2),
        "availablePool": round(available_pool, 2),
        "bonusCash": round(bonus_cash, 2),
        "bonusPct": bonus_cash / equity,
    }


def margin_level_is_safe(margin_used: float, margin_level: float, minimum_level: float) -> bool:
    """A zero margin level is acceptable only when no margin is currently used."""
    used = float(margin_used)
    level = float(margin_level)
    floor = float(minimum_level)
    if not all(math.isfinite(v) for v in (used, level, floor)) or used < 0 or floor < 0:
        return False
    if used <= 0:
        return True
    return level > 0 and level >= floor

def purged_expanding_walk_forward_splits(
    origins: list[int] | tuple[int, ...],
    label_ends: list[int] | tuple[int, ...],
    *,
    min_train_samples: int = 120,
    min_validation_samples: int = 30,
) -> list[tuple[list[int], list[int]]]:
    """Create time-ordered expanding folds and purge labels crossing validation start.

    Returns positional indices into the supplied arrays. A training sample is
    eligible only when its full future-label window ends strictly before the
    validation window begins.
    """
    if len(origins) != len(label_ends) or len(origins) < 1:
        raise ValueError("Walk-forward origins and label ends must have equal non-zero length.")
    origin_values = [int(value) for value in origins]
    end_values = [int(value) for value in label_ends]
    if any(end_value < origin for origin, end_value in zip(origin_values, end_values)):
        raise ValueError("A label end cannot precede its sample origin.")
    lower = min(origin_values)
    upper = max(origin_values) + 1
    span = max(1, upper - lower)
    fold_specs = ((0.55, 0.70), (0.70, 0.85), (0.85, 1.00))
    folds: list[tuple[list[int], list[int]]] = []
    for start_fraction, end_fraction in fold_specs:
        validation_start = lower + int(span * start_fraction)
        validation_end = upper if end_fraction >= 1.0 else lower + int(span * end_fraction)
        train_indices = [
            index for index, (origin, label_end) in enumerate(zip(origin_values, end_values))
            if origin < validation_start and label_end < validation_start
        ]
        validation_indices = [
            index for index, origin in enumerate(origin_values)
            if validation_start <= origin < validation_end
        ]
        if len(train_indices) < min_train_samples or len(validation_indices) < min_validation_samples:
            continue
        folds.append((train_indices, validation_indices))
    return folds
