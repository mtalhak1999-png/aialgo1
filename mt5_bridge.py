import json
import os
import ipaddress
import sys
import time
import math
import socket
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta, timezone

try:
    from risk_engine import (
        PROFILE as RISK_PROFILE, ensure_xauusd_symbol, calculate_atr,
        size_equal_burst, update_equity_anchors, margin_level_is_safe,
        capped_compound_bonus,
    )
except Exception as exc:
    raise RuntimeError(f"NovaFury V2 risk engine failed to import: {exc}") from exc

try:
    import MetaTrader5 as mt5
except Exception as exc:
    mt5 = None
    MT5_IMPORT_ERROR = str(exc)

try:
    from xgb_confirmation import confirm as xgb_confirm, TRAIN_BARS as XGB_TRAIN_BARS
except Exception as exc:
    xgb_confirm = None
    XGB_TRAIN_BARS = 600
    XGB_ENGINE_IMPORT_ERROR = str(exc)

try:
    from ai_ensemble import confirm as ai_ensemble_confirm, status as ai_ensemble_status, TRAIN_BARS as AI_ENSEMBLE_TRAIN_BARS
except Exception as exc:
    ai_ensemble_confirm = None
    AI_ENSEMBLE_TRAIN_BARS = 4000
    AI_ENSEMBLE_IMPORT_ERROR = str(exc)
    def ai_ensemble_status():
        return {
            # Fail closed if the configured-by-default AI ensemble cannot import.
            "enabled": True,
            "ready": False,
            "configuredModels": ["lightgbm", "catboost", "meta"],
            "lastError": AI_ENSEMBLE_IMPORT_ERROR,
        }

try:
    from ai_brain import (
        decide as ai_decide, status as ai_status,
        provider_settings as ai_provider_settings, save_provider_settings as save_ai_provider_settings,
    )
except Exception as exc:
    ai_decide = None
    AI_BRAIN_IMPORT_ERROR = str(exc)
    def ai_status():
        return {
            "enabled": False,
            "configured": False,
            "ready": False,
            "model": "unavailable",
            "error": AI_BRAIN_IMPORT_ERROR,
        }
    def ai_provider_settings():
        return {"openai": {"enabled": False, "configured": False, "model": "unavailable"},
                "anthropic": {"enabled": False, "configured": False, "model": "unavailable"}}
    def save_ai_provider_settings(payload):
        raise RuntimeError(f"AI provider settings unavailable: {AI_BRAIN_IMPORT_ERROR}")


def _load_bridge_env_files():
    """Load project-local settings before reading bridge constants."""
    project_root = Path(__file__).resolve().parent.parent
    for filename in (".env.local", ".env"):
        env_path = project_root / filename
        if not env_path.is_file():
            continue
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                if key:
                    os.environ.setdefault(key, value)
        except OSError:
            continue


_load_bridge_env_files()


def _bounded_env_float(name, default, minimum, maximum):
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid numeric configuration {name}: {exc}") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"Configuration {name} must be finite.")
    return min(float(maximum), max(float(minimum), value))


HOST = os.environ.get("MT5_BRIDGE_HOST", "127.0.0.1").strip()
try:
    if not ipaddress.ip_address(HOST).is_loopback:
        HOST = "127.0.0.1"
except ValueError:
    HOST = "127.0.0.1"
PORT = int(os.environ.get("MT5_BRIDGE_PORT", "8765"))
REQUESTED_SYMBOL = os.environ.get("SYMBOL", "XAUUSD").strip() or "XAUUSD"
TIMEFRAME_NAME = os.environ.get("TIMEFRAME", "M5").strip().upper() or "M5"
MAGIC = int(os.environ.get("MAGIC", "987654"))
DEVIATION = int(os.environ.get("DEVIATION", "50"))
COMMENT = "NovaFury"

# Cross-brain consensus weights. XGBoost and the multi-model ensemble share
# one coordinator; OpenAI remains a supervisory risk/quality gate, not a price
# probability voter. Weights are normalized over whichever direction brains are enabled.
BRAIN_XGB_WEIGHT = max(0.0, float(os.environ.get("BRAIN_XGB_WEIGHT", "0.35")))
BRAIN_ENSEMBLE_WEIGHT = max(0.0, float(os.environ.get("BRAIN_ENSEMBLE_WEIGHT", "0.65")))
BRAIN_CONSENSUS_THRESHOLD = min(0.95, max(0.50, float(os.environ.get("BRAIN_CONSENSUS_THRESHOLD", "0.60"))))
BRAIN_REQUIRE_XGB = os.environ.get("BRAIN_REQUIRE_XGB", "1").strip().lower() in ("1", "true", "yes", "on")
BRAIN_REQUIRE_ENSEMBLE = os.environ.get("BRAIN_REQUIRE_ENSEMBLE", "1").strip().lower() in ("1", "true", "yes", "on")
BRAIN_MAX_SPREAD_POINTS = max(1.0, float(os.environ.get("BRAIN_MAX_SPREAD_POINTS", "80")))
BRAIN_MIN_MARGIN_LEVEL = max(0.0, float(os.environ.get("BRAIN_MIN_MARGIN_LEVEL", "150")))

# NovaFury V2 recovery: confirmation-based pullback only; exposure is reserved
# from a separate capped risk allowance and never uses a loss-multiplying ladder.
RECOVERY_ENABLED = os.environ.get("RECOVERY_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
RECOVERY_ARM_ATR_MULTIPLIER = min(5.0, max(0.75, float(os.environ.get("RECOVERY_ARM_ATR_MULTIPLIER", "0.75"))))
RECOVERY_PULLBACK_MIN_FRACTION = min(0.80, max(0.25, float(os.environ.get("RECOVERY_PULLBACK_MIN_FRACTION", "0.25"))))
RECOVERY_PULLBACK_MAX_FRACTION = min(0.65, max(0.35, float(os.environ.get("RECOVERY_PULLBACK_MAX_FRACTION", "0.65"))))
RECOVERY_MAX_PENDING = 1
RECOVERY_MAX_FILLS_PER_BASKET = int(RISK_PROFILE["max_recovery_fills"])
RECOVERY_CHECK_SEC = max(1.0, float(os.environ.get("RECOVERY_CHECK_SEC", "5")))
RECOVERY_COMMENT_PREFIX = "NFR"
RECOVERY_PENDING_EXPIRY_MINUTES = min(30, max(1, int(os.environ.get("RECOVERY_PENDING_EXPIRY_MINUTES", "30"))))

# V2 Balanced execution profile. These are demo-validation defaults, not
# profitability claims. Every submitted ticket receives a broker-side SL.
# Environment overrides may make the profile more conservative, never more aggressive.
RISK_BASE_BASKET_PCT = _bounded_env_float("RISK_BASE_BASKET_PCT", RISK_PROFILE["base_basket_risk_pct"], 0.0, RISK_PROFILE["base_basket_risk_pct"])
RISK_MAX_BASKET_PCT = _bounded_env_float("RISK_MAX_BASKET_PCT", RISK_PROFILE["max_basket_risk_pct"], 0.0, RISK_PROFILE["max_basket_risk_pct"])
RISK_RECOVERY_RESERVE_PCT = _bounded_env_float("RISK_RECOVERY_RESERVE_PCT", RISK_PROFILE["recovery_reserve_pct"], 0.0, RISK_PROFILE["recovery_reserve_pct"])
RISK_DAILY_LOSS_PCT = _bounded_env_float("RISK_DAILY_LOSS_PCT", RISK_PROFILE["max_daily_loss_pct"], 0.0, RISK_PROFILE["max_daily_loss_pct"])
RISK_MAX_PEAK_DD_PCT = _bounded_env_float("RISK_MAX_PEAK_DD_PCT", RISK_PROFILE["max_peak_drawdown_pct"], 0.0, RISK_PROFILE["max_peak_drawdown_pct"])
RISK_MAX_MARGIN_UTIL_PCT = _bounded_env_float("RISK_MAX_MARGIN_UTIL_PCT", RISK_PROFILE["max_margin_utilization_pct"], 0.0, RISK_PROFILE["max_margin_utilization_pct"])
RISK_MIN_MARGIN_LEVEL = max(float(RISK_PROFILE["minimum_margin_level_pct"]), _bounded_env_float("RISK_MIN_MARGIN_LEVEL", RISK_PROFILE["minimum_margin_level_pct"], RISK_PROFILE["minimum_margin_level_pct"], 10000.0))
RISK_STOP_ATR_MULTIPLIER = _bounded_env_float("RISK_STOP_ATR_MULTIPLIER", RISK_PROFILE["stop_atr_multiplier"], RISK_PROFILE["stop_atr_multiplier"], 10.0)
COMPOUND_MAX_BONUS_PCT = _bounded_env_float("COMPOUND_MAX_BONUS_PCT", RISK_PROFILE["maximum_compound_bonus_pct"], 0.0, RISK_PROFILE["maximum_compound_bonus_pct"])
COMPOUND_PROFIT_SHARE = _bounded_env_float("COMPOUND_PROFIT_SHARE", RISK_PROFILE["compound_profit_share"], 0.0, RISK_PROFILE["compound_profit_share"])

BASKET_MIN_NET_PROFIT = max(0.0, float(os.environ.get("BASKET_MIN_NET_PROFIT", "1.00")))
ESTIMATED_ROUND_TRIP_COST_PER_LOT = max(
    0.0, float(os.environ.get("ESTIMATED_ROUND_TRIP_COST_PER_LOT", os.environ.get("ESTIMATED_CLOSE_COST_PER_LOT", "0.0")))
)
MAX_SPREAD_POINTS = _bounded_env_float("MAX_SPREAD_POINTS", BRAIN_MAX_SPREAD_POINTS, 1.0, 80.0)
BRAIN_MAX_SPREAD_POINTS = min(80.0, BRAIN_MAX_SPREAD_POINTS)
FIXED_BURST_ORDERS = 10
if RISK_BASE_BASKET_PCT <= 0 or RISK_MAX_BASKET_PCT <= 0 or RISK_RECOVERY_RESERVE_PCT < 0:
    raise RuntimeError("Balanced Risk Guardian percentages must be positive (recovery reserve may be zero).")
if RISK_MAX_BASKET_PCT < RISK_BASE_BASKET_PCT + COMPOUND_MAX_BONUS_PCT + RISK_RECOVERY_RESERVE_PCT:
    raise RuntimeError("Balanced basket cap must cover base risk, maximum compound bonus and recovery reserve.")
if RISK_DAILY_LOSS_PCT <= 0 or RISK_MAX_PEAK_DD_PCT <= 0 or RISK_MAX_MARGIN_UTIL_PCT <= 0:
    raise RuntimeError("Balanced daily-loss, peak-drawdown and margin limits must be positive.")
if RECOVERY_PULLBACK_MIN_FRACTION >= RECOVERY_PULLBACK_MAX_FRACTION:
    raise RuntimeError("Recovery pullback minimum must be less than its maximum.")
RISK_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "risk_state.json"
RISK_STATE_MARKER_PATH = Path(__file__).resolve().parent.parent / "data" / "risk_state.initialized"
execution_lock = threading.RLock()
execution_state_lock = threading.RLock()
execution_armed = False
armed_account = None
execution_epoch = 0  # incremented on every disarm to invalidate in-flight burst tickets
risk_state = None
risk_state_error = ""
smart_profit_peaks = {}
smart_profit_last_error = {}

# Per-position trailing is supplementary protection; initial SL is mandatory.
SMART_PROFIT_ENABLED = os.environ.get("SMART_PROFIT_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
SMART_PROFIT_TRAIL_POINTS = max(1.0, float(os.environ.get("SMART_PROFIT_TRAIL_POINTS", "60")))
SMART_PROFIT_STEP_POINTS = max(1.0, float(os.environ.get("SMART_PROFIT_STEP_POINTS", "5")))
SMART_PROFIT_BE_OFFSET_POINTS = max(0.0, float(os.environ.get("SMART_PROFIT_BE_OFFSET_POINTS", "0")))
SMART_PROFIT_CHECK_SEC = max(0.05, float(os.environ.get("SMART_PROFIT_CHECK_SEC", "0.25")))
SMART_PROFIT_COMMENT = "NovaFury-SmartProfit"

SMART_BULK_EXIT_ENABLED = os.environ.get("SMART_BULK_EXIT_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
SMART_BULK_EXIT_CHECK_US = max(100_000, min(5_000_000, int(os.environ.get("SMART_BULK_EXIT_CHECK_US", "500000"))))
SMART_BULK_EXIT_CHECK_SEC = SMART_BULK_EXIT_CHECK_US / 1_000_000.0
SMART_BULK_EXIT_MAX_WORKERS = max(1, min(10, int(os.environ.get("SMART_BULK_EXIT_MAX_WORKERS", "10"))))
bulk_exit_lock = threading.Lock()
bulk_exit_symbols_in_progress = set()

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MT5_ROOT = Path(os.environ.get("MT5_ROOT", r"C:\Program Files\MetaTrader 5"))

TF_MAP = {
    "M1": getattr(mt5, "TIMEFRAME_M1", 1) if mt5 else 1,
    "M2": getattr(mt5, "TIMEFRAME_M2", 2) if mt5 else 2,
    "M3": getattr(mt5, "TIMEFRAME_M3", 3) if mt5 else 3,
    "M4": getattr(mt5, "TIMEFRAME_M4", 4) if mt5 else 4,
    "M5": getattr(mt5, "TIMEFRAME_M5", 5) if mt5 else 5,
    "M6": getattr(mt5, "TIMEFRAME_M6", 6) if mt5 else 6,
    "M10": getattr(mt5, "TIMEFRAME_M10", 10) if mt5 else 10,
    "M12": getattr(mt5, "TIMEFRAME_M12", 12) if mt5 else 12,
    "M15": getattr(mt5, "TIMEFRAME_M15", 15) if mt5 else 15,
    "M30": getattr(mt5, "TIMEFRAME_M30", 30) if mt5 else 30,
    "H1": getattr(mt5, "TIMEFRAME_H1", 16385) if mt5 else 16385,
}

state_lock = threading.RLock()
terminal_path = None
resolved_symbol_cache = {}


def json_safe(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (Path,)):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, "_asdict"):
        return {k: json_safe(v) for k, v in obj._asdict().items()}
    try:
        return obj.item()
    except Exception:
        return str(obj)


def _risk_state_write(payload):
    RISK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = RISK_STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(RISK_STATE_PATH)
    # A separate initialization marker prevents an accidentally deleted state
    # file from silently creating a fresh daily/high-water anchor after restart.
    marker = {
        "version": 1, "login": int(payload["login"]), "server": str(payload["server"]),
        "initializedAt": float(payload.get("initializedAt", payload.get("updatedAt", time.time()))),
    }
    marker_temp = RISK_STATE_MARKER_PATH.with_suffix(".tmp")
    marker_temp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    try:
        os.chmod(marker_temp, 0o600)
    except OSError:
        pass
    marker_temp.replace(RISK_STATE_MARKER_PATH)


def _utc_day_key():
    return datetime.now(timezone.utc).date().isoformat()


def _ensure_risk_state(account, reset=False):
    """Load a persistent account/day anchor; fail closed on corruption/account mismatch."""
    global risk_state, risk_state_error
    login = int(account["login"])
    server = str(account["server"])
    equity = float(account["equity"])
    if equity <= 0 or not math.isfinite(equity):
        raise RuntimeError("Risk Guardian cannot initialize from a non-positive account equity.")
    with execution_state_lock:
        if RISK_STATE_MARKER_PATH.exists() and not RISK_STATE_PATH.exists() and not reset:
            risk_state_error = (
                "Risk state file is missing even though this installation was previously initialized. "
                "Trading is blocked; restore data/risk_state.json from a trusted backup rather than creating a new anchor."
            )
            raise RuntimeError(risk_state_error)
        if risk_state is None or reset:
            if not reset and RISK_STATE_PATH.exists():
                try:
                    saved = json.loads(RISK_STATE_PATH.read_text(encoding="utf-8"))
                    if not isinstance(saved, dict) or saved.get("version") != 2:
                        raise ValueError("Unsupported risk-state version")
                    if int(saved.get("login", -1)) != login or str(saved.get("server", "")) != server:
                        raise ValueError("Stored Risk Guardian account does not match the connected MT5 account. Confirm the account and reset the daily anchor.")
                    risk_state = saved
                    risk_state.setdefault("compoundSpent", 0.0)
                    if not math.isfinite(float(risk_state.get("compoundSpent", 0.0))) or float(risk_state.get("compoundSpent", 0.0)) < 0:
                        raise ValueError("Stored compounding commitment is invalid")
                    if not RISK_STATE_MARKER_PATH.exists():
                        _risk_state_write(risk_state)
                except Exception as exc:
                    risk_state_error = f"Risk state unavailable: {exc}. Trading remains blocked until an administrator restores or explicitly recovers the state."
                    raise RuntimeError(risk_state_error)
            else:
                now = time.time()
                risk_state = {
                    "version": 2, "login": login, "server": server,
                    "dayKey": _utc_day_key(), "dayStartEquity": equity,
                    "peakEquity": equity, "compoundStartBalance": float(account["balance"]),
                    "compoundSpent": 0.0,
                    "initializedAt": now, "updatedAt": now,
                }
                risk_state_error = ""
                _risk_state_write(risk_state)
        if int(risk_state.get("login", -1)) != login or str(risk_state.get("server", "")) != server:
            risk_state_error = "Connected MT5 account changed; reinitialize Risk Guardian before trading."
            raise RuntimeError(risk_state_error)
        today = _utc_day_key()
        prior_state = dict(risk_state)
        risk_state = update_equity_anchors(risk_state, equity, today)
        if risk_state != prior_state:
            risk_state["updatedAt"] = time.time()
            _risk_state_write(risk_state)
        risk_state_error = ""
        return risk_state


def _risk_snapshot(account):
    state = _ensure_risk_state(account)
    equity = float(account["equity"])
    day_start = float(state["dayStartEquity"])
    peak = float(state["peakEquity"])
    daily_loss_pct = daily_loss / day_start if day_start else 1.0
    peak_drawdown_pct = peak_drawdown / peak if peak else 1.0
    margin = max(0.0, float(account.get("margin", 0.0) or 0.0))
    margin_level = float(account.get("marginLevel", 0.0) or 0.0)
    margin_utilization_pct = margin / equity if equity > 0 else 1.0
    halt_reasons = []
    if daily_loss_pct >= RISK_DAILY_LOSS_PCT:
        halt_reasons.append("daily_loss")
    if peak_drawdown_pct >= RISK_MAX_PEAK_DD_PCT:
        halt_reasons.append("peak_drawdown")
    if margin_utilization_pct > RISK_MAX_MARGIN_UTIL_PCT:
        halt_reasons.append("margin_utilization")
    if not margin_level_is_safe(margin, margin_level, RISK_MIN_MARGIN_LEVEL):
        halt_reasons.append("margin_level")
    return {
        "profile": "balanced", "executionArmed": bool(execution_armed),
        "autoTrade": bool(execution_armed), "dailyLossPct": daily_loss_pct,
        "dailyLossLimitPct": RISK_DAILY_LOSS_PCT,
        "peakDrawdownPct": peak_drawdown_pct,
        "peakDrawdownLimitPct": RISK_MAX_PEAK_DD_PCT,
        "baseBasketRiskPct": RISK_BASE_BASKET_PCT,
        "maxBasketRiskPct": RISK_MAX_BASKET_PCT,
        "recoveryReservePct": RISK_RECOVERY_RESERVE_PCT,
        "marginUtilizationPct": margin_utilization_pct,
        "marginLevelPct": margin_level,
        "maxMarginUtilizationPct": RISK_MAX_MARGIN_UTIL_PCT,
        "minimumMarginLevelPct": RISK_MIN_MARGIN_LEVEL,
        "burstOrders": FIXED_BURST_ORDERS,
        "riskStateError": risk_state_error,
        "haltReasons": halt_reasons,
        "halted": bool(halt_reasons),
    }


def _assert_account_authorized(account):
    allowed_logins = {x.strip() for x in os.environ.get("MT5_ALLOWED_LOGINS", "").split(",") if x.strip()}
    if allowed_logins and str(account.get("login")) not in allowed_logins:
        raise RuntimeError("Connected MT5 login is not in MT5_ALLOWED_LOGINS.")


def _position_stop_is_live(position):
    """Accept initial, break-even or profit-locked SLs if they remain beyond the live market."""
    symbol = str(getattr(position, "symbol", ""))
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False
    sl = float(getattr(position, "sl", 0.0) or 0.0)
    volume = float(getattr(position, "volume", 0.0) or 0.0)
    entry = float(getattr(position, "price_open", 0.0) or 0.0)
    if sl <= 0 or volume <= 0 or entry <= 0:
        return False
    is_buy = int(position.type) == getattr(mt5, "POSITION_TYPE_BUY", 0)
    # A stop at entry or in locked profit is valid. It must simply remain on
    # the protective side of the executable close quote, not necessarily the
    # loss side of the original entry price.
    if is_buy:
        close_quote = float(getattr(tick, "bid", 0.0) or 0.0)
        return close_quote > 0 and sl < close_quote
    close_quote = float(getattr(tick, "ask", 0.0) or 0.0)
    return close_quote > 0 and sl > close_quote


def _current_bot_stop_risk(symbol: str | None = None):
    """Calculate all NovaFury cash risk to broker-held stops, positions and pending orders."""
    raw_positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if raw_positions is None:
        raise RuntimeError(f"NovaFury stop-risk position query failed: {mt5.last_error()}")
    raw_orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    if raw_orders is None:
        raise RuntimeError(f"NovaFury stop-risk pending-order query failed: {mt5.last_error()}")

    total = 0.0
    position_details = []
    pending_details = []
    for position in raw_positions:
        if int(getattr(position, "magic", 0)) != MAGIC:
            continue
        position_symbol = str(position.symbol)
        ensure_xauusd_symbol(position_symbol)
        sl = float(getattr(position, "sl", 0.0) or 0.0)
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        is_buy = int(position.type) == getattr(mt5, "POSITION_TYPE_BUY", 0)
        if sl <= 0 or entry <= 0 or volume <= 0 or not _position_stop_is_live(position):
            raise RuntimeError(
                f"Managed position {position.ticket} has no valid live broker-side protective stop; "
                "the stop must remain beyond the current executable close quote."
            )
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        result = mt5.order_calc_profit(order_type, position_symbol, volume, entry, sl)
        if result is None or not math.isfinite(float(result)):
            raise RuntimeError(f"MT5 could not calculate stop risk for position {position.ticket}: {mt5.last_error()}")
        price_risk = max(0.0, -float(result))
        cost_reserve = volume * ESTIMATED_ROUND_TRIP_COST_PER_LOT
        risk = price_risk + cost_reserve
        total += risk
        position_details.append({
            "ticket": int(position.ticket), "symbol": position_symbol,
            "risk": round(risk, 2), "priceRisk": round(price_risk, 2),
            "costReserve": round(cost_reserve, 2), "volume": volume, "stopLoss": sl,
        })

    buy_limit = getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2)
    sell_limit = getattr(mt5, "ORDER_TYPE_SELL_LIMIT", 3)
    for order in raw_orders:
        if int(getattr(order, "magic", 0)) != MAGIC:
            continue
        order_symbol = str(order.symbol)
        ensure_xauusd_symbol(order_symbol)
        order_type = int(getattr(order, "type", -1))
        if order_type not in (buy_limit, sell_limit):
            raise RuntimeError(
                f"Managed pending order {order.ticket} uses unsupported order type {order_type}; "
                "cancel it before V2 can verify aggregate risk."
            )
        entry = float(getattr(order, "price_open", getattr(order, "price_current", 0.0)) or 0.0)
        sl = float(getattr(order, "sl", 0.0) or 0.0)
        volume = float(getattr(order, "volume_current", 0.0) or getattr(order, "volume_initial", 0.0) or 0.0)
        is_buy = order_type == buy_limit
        if entry <= 0 or sl <= 0 or volume <= 0:
            raise RuntimeError(f"Managed pending order {order.ticket} has no valid price, volume or broker-side stop.")
        if (is_buy and sl >= entry) or (not is_buy and sl <= entry):
            raise RuntimeError(f"Managed pending order {order.ticket} has a stop on the wrong side of its entry price.")
        calc_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        result = mt5.order_calc_profit(calc_type, order_symbol, volume, entry, sl)
        if result is None or not math.isfinite(float(result)):
            raise RuntimeError(f"MT5 could not calculate stop risk for pending order {order.ticket}: {mt5.last_error()}")
        price_risk = max(0.0, -float(result))
        cost_reserve = volume * ESTIMATED_ROUND_TRIP_COST_PER_LOT
        risk = price_risk + cost_reserve
        total += risk
        pending_details.append({
            "ticket": int(order.ticket), "symbol": order_symbol,
            "risk": round(risk, 2), "priceRisk": round(price_risk, 2),
            "costReserve": round(cost_reserve, 2), "volume": volume,
            "entryPrice": entry, "stopLoss": sl,
        })

    return {
        "risk": round(total, 2),
        "positions": position_details,
        "pendingOrders": pending_details,
    }


def _risk_gate(account, quote, allow_existing=False):
    status = _risk_snapshot(account)
    if risk_state_error:
        raise RuntimeError(risk_state_error)
    if status["halted"]:
        raise RuntimeError("Risk Guardian hard limit reached: " + ", ".join(status.get("haltReasons", [])) + ". Trading is halted.")
    if not bool(account.get("tradeAllowed")):
        raise RuntimeError("MT5 account does not currently allow trading.")
    spread = float(quote.get("spread", 0) or 0)
    if spread <= 0 or spread > MAX_SPREAD_POINTS:
        raise RuntimeError(f"Spread gate blocked: {spread:g} points; maximum is {MAX_SPREAD_POINTS:g}.")
    equity = float(account.get("equity", 0) or 0)
    margin = float(account.get("margin", 0) or 0)
    if equity <= 0 or margin / equity > RISK_MAX_MARGIN_UTIL_PCT:
        raise RuntimeError("Risk Guardian margin-utilization limit would be exceeded.")
    margin_level = float(account.get("marginLevel", 0) or 0)
    if not margin_level_is_safe(margin, margin_level, RISK_MIN_MARGIN_LEVEL):
        raise RuntimeError(
            f"Margin level is unavailable or below the {RISK_MIN_MARGIN_LEVEL:.1f}% Balanced floor while margin is in use."
        )
    if not allow_existing:
        positions = mt5.positions_get()
        if positions is None:
            raise RuntimeError(f"MT5 account position query failed: {mt5.last_error()}")
        bot_positions = [p for p in positions if int(getattr(p, "magic", 0)) == MAGIC]
        pendings = recovery_pending_orders() if "recovery_pending_orders" in globals() else []
        if bot_positions or pendings:
            raise RuntimeError("A NovaFury basket or pending order already exists on this account; V2 allows one managed basket at a time.")
    else:
        exposure = _current_bot_stop_risk()
        maximum_cash_risk = float(account["equity"]) * RISK_MAX_BASKET_PCT
        if exposure["risk"] > maximum_cash_risk + 0.01:
            raise RuntimeError(
                f"Current NovaFury stop risk ${exposure['risk']:.2f} exceeds the ${maximum_cash_risk:.2f} aggregate Balanced cap."
            )
    return status


def _cancel_all_recovery_orders(symbol=None):
    orders = recovery_pending_orders(symbol)
    failed = []
    for order in orders:
        if not delete_pending_order(int(order.ticket)):
            failed.append(int(order.ticket))
    # Verify broker state after cancellation; never close a basket while a recovery order may remain live.
    remaining = {int(o.ticket) for o in recovery_pending_orders(symbol)}
    unresolved = sorted(set(failed) | remaining)
    return {"ok": not unresolved, "cancelled": len(orders) - len(unresolved), "unresolved": unresolved}



def _assert_existing_positions_protected(symbol: str):
    positions = mt5.positions_get()
    if positions is None:
        raise RuntimeError(f"MT5 account position query failed during arming: {mt5.last_error()}")
    bot_positions = [p for p in positions if int(getattr(p, "magic", 0)) == MAGIC]
    for position in bot_positions:
        if str(getattr(position, "symbol", "")) != symbol:
            raise RuntimeError(
                f"Cannot arm XAUUSD-only V2 while legacy NovaFury position {position.ticket} remains on {position.symbol}. "
                "Close or manage that position separately first."
            )
        if not _position_stop_is_live(position):
            raise RuntimeError(
                f"Cannot arm: existing NovaFury position {position.ticket} has no valid live broker-side stop-loss. "
                "Protect or close legacy exposure first."
            )
    pending = recovery_pending_orders()
    if pending:
        first = pending[0]
        raise RuntimeError(
            f"Cannot arm while NovaFury pending order {first.ticket} remains on {first.symbol}. "
            "Disarm/clean legacy pending orders, verify the terminal is clear, and arm again."
        )
    return {"positions": len(bot_positions), "pendingNovaFuryOrders": 0}

def _disarm(reason="Disarmed by user"):
    global execution_armed, armed_account, execution_epoch
    # Flip the arming flag immediately so an in-flight burst stops at its next
    # per-ticket check. Then serialize recovery cleanup against the recovery
    # manager and close manager so no pending order can be created after cleanup.
    with execution_state_lock:
        previous_account = armed_account
        execution_armed = False
        armed_account = None
        execution_epoch += 1
    try:
        with execution_lock:
            # Never send cleanup requests into a different account after a terminal/account
            # switch. Leaving an unknown account untouched is safer than cancelling its orders.
            if previous_account and mt5 is not None:
                current = account_payload()
                current_key = (int(current["login"]), str(current["server"]))
                if current_key != previous_account:
                    cleanup = {"ok": False, "cancelled": 0, "unresolved": [],
                               "error": "MT5 account changed; recovery cleanup was not sent to the new account."}
                else:
                    cleanup = _cancel_all_recovery_orders()
            else:
                cleanup = _cancel_all_recovery_orders()
    except Exception as exc:
        cleanup = {"ok": False, "error": repr(exc)}
    print(f"[NovaFury] EXECUTION DISARMED: {reason}; recoveryCleanup={cleanup}", flush=True)
    return cleanup


def find_terminal(root: Path) -> str | None:
    candidates = [
        root / "terminal64.exe",
        root / "terminal.exe",
        root / "MetaTrader 5" / "terminal64.exe",
        root / "MetaTrader 5" / "terminal.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    if root.is_dir():
        for name in ("terminal64.exe", "terminal.exe"):
            hits = list(root.rglob(name))
            if hits:
                return str(hits[0])
    common = [
        Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
        Path(r"C:\Program Files\MetaTrader 5\terminal.exe"),
    ]
    for c in common:
        if c.is_file():
            return str(c)
    return None


def ensure_mt5():
    global terminal_path
    if mt5 is None:
        raise RuntimeError(
            "MetaTrader5 Python package is not installed. Run START_MT5_BOT.ps1 again."
        )
    with state_lock:
        try:
            ti = mt5.terminal_info()
            if ti is not None:
                return True
        except Exception:
            pass
        terminal_path = find_terminal(DEFAULT_MT5_ROOT)
        ok = False
        if terminal_path:
            ok = bool(mt5.initialize(path=terminal_path, timeout=60000))
        if not ok:
            ok = bool(mt5.initialize(timeout=60000))
        if not ok:
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        return True


def resolve_symbol(requested: str) -> str:
    ensure_mt5()
    requested = ensure_xauusd_symbol(requested or REQUESTED_SYMBOL)
    if requested in resolved_symbol_cache:
        return resolved_symbol_cache[requested]
    exact = mt5.symbol_info(requested)
    if exact is not None:
        if not exact.visible:
            mt5.symbol_select(requested, True)
        resolved_symbol_cache[requested] = requested
        return requested

    candidates = [
        requested + "m",
        requested + ".m",
        requested + ".",
        requested + "_",
        requested + "pro",
    ]
    all_syms = mt5.symbols_get()
    names = [s.name for s in all_syms] if all_syms else []
    for cand in candidates:
        if cand in names:
            mt5.symbol_select(cand, True)
            resolved_symbol_cache[requested] = cand
            return cand
    # Best-effort substring match for brokers with suffixes/prefixes.
    needle = requested.replace(".", "").lower()
    for name in names:
        compact = name.replace(".", "").lower()
        if compact == needle or compact.startswith(needle) or compact.endswith(needle):
            mt5.symbol_select(name, True)
            resolved_symbol_cache[requested] = name
            return name
    raise RuntimeError(f"Symbol '{requested}' was not found in this MT5 terminal.")


def round_volume(symbol: str, volume: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol_info({symbol}) failed")
    vmin = float(info.volume_min or 0.01)
    vmax = float(info.volume_max or volume)
    step = float(info.volume_step or vmin)
    volume = min(max(volume, vmin), vmax)
    steps = round((volume - vmin) / step)
    out = vmin + steps * step
    decimals = max(0, min(8, len(str(step).split(".")[-1].rstrip("0")) if "." in str(step) else 0))
    return round(out, decimals)


def pick_filling(symbol: str):
    info = mt5.symbol_info(symbol)
    preferred = []
    if info is not None:
        mode = int(getattr(info, "filling_mode", 0) or 0)
        if mode & getattr(mt5, "SYMBOL_FILLING_FOK", 1):
            preferred.append(mt5.ORDER_FILLING_FOK)
        if mode & getattr(mt5, "SYMBOL_FILLING_IOC", 2):
            preferred.append(mt5.ORDER_FILLING_IOC)
    preferred.extend([getattr(mt5, "ORDER_FILLING_RETURN", 2),
                      getattr(mt5, "ORDER_FILLING_IOC", 1),
                      getattr(mt5, "ORDER_FILLING_FOK", 0)])
    seen = []
    for x in preferred:
        if x not in seen:
            seen.append(x)
    return seen


def symbol_quote(requested: str):
    symbol = resolve_symbol(requested)
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        raise RuntimeError(f"Unable to read tick for {symbol}")
    bid = float(tick.bid)
    ask = float(tick.ask)
    digits = int(info.digits)
    point = float(info.point)
    spread_points = int(round((ask - bid) / point)) if point else 0
    return {
        "symbol": symbol,
        "requestedSymbol": requested,
        "name": str(getattr(info, "description", symbol) or symbol),
        "bid": bid,
        "ask": ask,
        "spread": spread_points,
        "changePercent": 0,
        "high": bid,
        "low": bid,
        "digits": digits,
        "point": point,
        "tradeMode": int(getattr(info, "trade_mode", 0)),
        "volumeMin": float(getattr(info, "volume_min", 0.01)),
        "volumeMax": float(getattr(info, "volume_max", 100.0)),
        "volumeStep": float(getattr(info, "volume_step", 0.01)),
    }


def positions_payload(requested: str):
    symbol = resolve_symbol(requested)
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        raise RuntimeError(f"positions_get({symbol}) failed: {mt5.last_error()}")
    # Show only positions opened with this bot's magic number so manual positions
    # on the same symbol are never treated as the bot's basket.
    positions = [p for p in positions if int(getattr(p, "magic", 0)) == MAGIC]
    out = []
    for p in positions:
        direction = "buy" if int(p.type) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "sell"
        open_price = float(p.price_open)
        current = float(p.price_current)
        out.append({
            "id": str(int(p.ticket)),
            "ticket": int(p.ticket),
            "symbol": str(p.symbol),
            "direction": direction,
            "lotSize": float(p.volume),
            "openPrice": open_price,
            "currentPrice": current,
            "profit": float(p.profit),
            "openTime": int(p.time) * 1000,
            "stopLoss": float(p.sl) if p.sl else None,
            "takeProfit": float(p.tp) if p.tp else None,
            "comment": str(p.comment or ""),
            "magic": int(p.magic),
        })
    return out


_history_cache = {}
_history_cache_lock = threading.Lock()

def recent_history(requested: str, days=365, limit=100):
    """Reconstruct NovaFury-only closed positions from MT5 entry and exit deals.

    Cache is account-scoped so a terminal/login switch cannot reuse another
    account's journal or realized-profit compounding history.
    """
    symbol = resolve_symbol(requested)
    account = account_payload()
    cache_key = (int(account["login"]), str(account["server"]), symbol, int(days), int(limit))
    now_mono = time.monotonic()
    with _history_cache_lock:
        cached = _history_cache.get(cache_key)
        if cached and now_mono - cached[0] < 20.0:
            return [dict(row) for row in cached[1]]
    end = datetime.now()
    start = end - timedelta(days=days)
    deals = mt5.history_deals_get(start, end, group=symbol + "*")
    if deals is None:
        raise RuntimeError(f"MT5 history query failed: {mt5.last_error()}")
    rows = {}
    entry_codes = {getattr(mt5, "DEAL_ENTRY_IN", 0), getattr(mt5, "DEAL_ENTRY_INOUT", 2)}
    exit_codes = {getattr(mt5, "DEAL_ENTRY_OUT", 1), getattr(mt5, "DEAL_ENTRY_OUT_BY", 3), getattr(mt5, "DEAL_ENTRY_INOUT", 2)}
    for deal in deals:
        if int(getattr(deal, "magic", 0)) != MAGIC:
            continue
        pid = int(getattr(deal, "position_id", 0) or 0)
        if not pid:
            continue
        entry_code = int(getattr(deal, "entry", -1))
        bucket = rows.setdefault(pid, {
            "id": str(pid), "symbol": str(deal.symbol), "direction": "buy",
            "entryVolume": 0.0, "exitVolume": 0.0, "entryValue": 0.0,
            "exitValue": 0.0, "lotSize": 0.0, "openPrice": 0.0,
            "closePrice": 0.0, "profit": 0.0, "openTime": 0, "closeTime": 0,
        })
        deal_type = int(getattr(deal, "type", 0))
        volume = float(getattr(deal, "volume", 0.0) or 0.0)
        price = float(getattr(deal, "price", 0.0) or 0.0)
        deal_time = int(getattr(deal, "time_msc", 0) or (int(getattr(deal, "time", 0)) * 1000))
        # Include commission/fee/swap from every deal in the position lifecycle.
        bucket["profit"] += (float(getattr(deal, "profit", 0.0) or 0.0)
                             + float(getattr(deal, "swap", 0.0) or 0.0)
                             + float(getattr(deal, "commission", 0.0) or 0.0)
                             + float(getattr(deal, "fee", 0.0) or 0.0))
        if entry_code in entry_codes:
            bucket["entryVolume"] += volume
            bucket["entryValue"] += volume * price
            if not bucket["openTime"] or deal_time < bucket["openTime"]:
                bucket["openTime"] = deal_time
            # Entry deal type is the side of the position.
            bucket["direction"] = "buy" if deal_type == getattr(mt5, "DEAL_TYPE_BUY", 0) else "sell"
        if entry_code in exit_codes:
            bucket["exitVolume"] += volume
            bucket["exitValue"] += volume * price
            bucket["closeTime"] = max(bucket["closeTime"], deal_time)
    output = []
    for item in rows.values():
        # A partial close is realized cash flow, but it is not a completed trade
        # cycle and must not appear as a closed position or fund compounding yet.
        if item["entryVolume"] <= 0 or item["exitVolume"] + 1e-8 < item["entryVolume"]:
            continue
        item["openPrice"] = item["entryValue"] / item["entryVolume"]
        item["closePrice"] = item["exitValue"] / item["exitVolume"]
        item["lotSize"] = min(item["entryVolume"], item["exitVolume"])
        item["profit"] = round(item["profit"], 2)
        item["openTime"] = int(item["openTime"])
        item["closeTime"] = int(item["closeTime"])
        output.append(item)
    output.sort(key=lambda row: row["closeTime"], reverse=True)
    output = output[:max(1, int(limit))]
    with _history_cache_lock:
        _history_cache[cache_key] = (now_mono, [dict(row) for row in output])
    return output


def account_payload():
    ai = mt5.account_info()
    if ai is None:
        raise RuntimeError(f"account_info failed: {mt5.last_error()}")
    return {
        "login": int(ai.login),
        "server": str(ai.server),
        "name": str(ai.name),
        "currency": str(ai.currency),
        "balance": float(ai.balance),
        "equity": float(ai.equity),
        "margin": float(ai.margin),
        "freeMargin": float(ai.margin_free),
        "marginLevel": float(ai.margin_level or 0.0),
        "tradeAllowed": bool(getattr(ai, "trade_allowed", False)),
    }


def snapshot(requested: str):
    ensure_mt5()
    q = symbol_quote(requested)
    account = account_payload()
    pos = positions_payload(requested)
    history = recent_history(requested)
    risk = _risk_snapshot(account)
    return {
        "ok": True,
        "connected": True,
        "terminalPath": terminal_path,
        "symbol": q["symbol"],
        "requestedSymbol": requested,
        "timeframe": TIMEFRAME_NAME,
        "magic": MAGIC,
        "smartProfit": {
            "enabled": SMART_PROFIT_ENABLED,
            "trailPoints": SMART_PROFIT_TRAIL_POINTS,
            "stepPoints": SMART_PROFIT_STEP_POINTS,
            "breakEvenOffsetPoints": SMART_PROFIT_BE_OFFSET_POINTS,
            "checkSeconds": SMART_PROFIT_CHECK_SEC,
        },
        "smartBulkExit": {
            "enabled": SMART_BULK_EXIT_ENABLED,
            "checkSeconds": SMART_BULK_EXIT_CHECK_SEC,  # kept for existing dashboard compatibility
            "checkMicroseconds": SMART_BULK_EXIT_CHECK_US,
            "maxParallelCloses": SMART_BULK_EXIT_MAX_WORKERS,
        },
        "quote": q,
        "account": account,
        "positions": pos,
        "history": history,
        "risk": risk,
        "runtime": {"executionArmed": bool(execution_armed), "autoTrade": bool(execution_armed)},
        "timestamp": int(time.time() * 1000),
    }


def candles(requested: str, count=60, timeframe_name=None, include_forming=False):
    ensure_mt5()
    symbol = resolve_symbol(requested)
    selected_tf = (timeframe_name or TIMEFRAME_NAME).upper()
    tf = TF_MAP.get(selected_tf)
    if tf is None:
        raise ValueError(f"Unsupported timeframe: {selected_tf}")
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, int(max(10, min(10000, count))))
    if rates is None:
        raise RuntimeError(f"copy_rates_from_pos failed: {mt5.last_error()}")
    rows = [{
        "time": int(r["time"]) * 1000,
        "open": float(r["open"]), "high": float(r["high"]),
        "low": float(r["low"]), "close": float(r["close"]),
        "volume": int(r["tick_volume"]),
    } for r in rates]
    rows.sort(key=lambda row: row["time"])
    # MT5 position 0 is the forming candle. Strategy/model decisions use completed bars only.
    if not include_forming and rows:
        rows = rows[:-1]
    return rows


def timeframe_bias(symbol: str, timeframe_name: str):
    rows = candles(symbol, 80, timeframe_name=timeframe_name, include_forming=False)
    if len(rows) < 25:
        return "neutral"
    closes = [float(row["close"]) for row in rows]
    def ema(values, period):
        alpha = 2.0 / (period + 1.0)
        value = sum(values[:period]) / period
        for point in values[period:]:
            value = point * alpha + value * (1.0 - alpha)
        return value
    fast, slow = ema(closes, 9), ema(closes, 21)
    latest = closes[-1]
    if fast > slow and latest > fast:
        return "buy"
    if fast < slow and latest < fast:
        return "sell"
    return "neutral"


def xgb_confirmation(requested: str, direction: str | None = None, candle_rows=None):
    ensure_mt5()
    if xgb_confirm is None:
        return {
            "enabled": False,
            "available": False,
            "ready": False,
            "model": "xgboost-m5-direction",
            "direction": direction or "neutral",
            "directionProbability": 0.5,
            "probabilityUp": 0.5,
            "probabilityDown": 0.5,
            "threshold": 0.58,
            "confirmed": False,
            "error": f"XGBoost engine import failed: {XGB_ENGINE_IMPORT_ERROR}",
        }
    symbol = resolve_symbol(requested)
    rows = candle_rows if candle_rows is not None else candles(symbol, 600)
    return xgb_confirm(symbol, rows, direction)


def ai_ensemble_confirmation(requested: str, direction: str | None = None, candle_rows=None):
    symbol = requested
    configured = ai_ensemble_status()
    if ai_ensemble_confirm is not None and not configured.get("enabled", True):
        return {
            "enabled": False,
            "available": False,
            "ready": False,
            "model": "ai-ensemble-disabled",
            "symbol": symbol,
            "direction": direction or "neutral",
            "directionProbability": 0.5,
            "probabilityUp": 0.5,
            "metaProbability": 0.0,
            "threshold": 0.60,
            "metaThreshold": 0.58,
            "confirmed": False,
            "selectedModels": configured.get("configuredModels", []),
            "trainedBars": 0,
            "version": 0,
            "ageSeconds": None,
            "models": {},
            "error": "AI ensemble disabled",
        }
    ensure_mt5()
    symbol = resolve_symbol(requested)
    if ai_ensemble_confirm is None:
        return {
            "enabled": True,
            "available": False,
            "ready": False,
            "model": "ai-ensemble-unavailable",
            "symbol": symbol,
            "direction": direction or "neutral",
            "directionProbability": 0.5,
            "metaProbability": 0.0,
            "threshold": 0.60,
            "metaThreshold": 0.58,
            "confirmed": False,
            "error": f"AI ensemble import failed: {AI_ENSEMBLE_IMPORT_ERROR}",
        }
    rows = candle_rows if candle_rows is not None else candles(symbol, AI_ENSEMBLE_TRAIN_BARS)
    return ai_ensemble_confirm(symbol, rows, direction)


def ai_decision_for(
    requested: str,
    direction: str | None = None,
    prepared_snapshot=None,
    candle_rows=None,
    xgb_result=None,
    ensemble_result=None,
    consensus_result=None,
    recovery_context=False,
):
    ensure_mt5()
    if ai_decide is None:
        return {
            "enabled": False,
            "available": False,
            "ready": False,
            "model": "unavailable",
            "allowed": False,
            "preferredDirection": direction or "neutral",
            "confidence": 0.0,
            "entryQuality": 0.0,
            "riskMode": "blocked",
            "rationale": f"AI brain import failed: {AI_BRAIN_IMPORT_ERROR}",
            "error": AI_BRAIN_IMPORT_ERROR,
        }
    symbol = resolve_symbol(requested)
    snap = prepared_snapshot if prepared_snapshot is not None else snapshot(symbol)
    rows = candle_rows if candle_rows is not None else candles(symbol, AI_ENSEMBLE_TRAIN_BARS)
    xgb_data = xgb_result if xgb_result is not None else xgb_confirmation(symbol, direction or "neutral", rows)
    ensemble_data = ensemble_result if ensemble_result is not None else ai_ensemble_confirmation(symbol, direction or "neutral", rows)
    xgb_for_supervisor = dict(xgb_data)
    xgb_for_supervisor["required"] = BRAIN_REQUIRE_XGB
    ensemble_for_supervisor = dict(ensemble_data)
    ensemble_for_supervisor["required"] = BRAIN_REQUIRE_ENSEMBLE
    return ai_decide(
        symbol=symbol,
        direction=(direction or "neutral").lower(),
        candles=rows[-120:],
        quote=snap["quote"],
        account=snap["account"],
        xgb=xgb_for_supervisor,
        ensemble=ensemble_for_supervisor,
        consensus=consensus_result,
        positions=snap["positions"],
        history=snap["history"],
        recovery_context=recovery_context,
    )


def evaluate_m5_entry_signal(rows):
    """Server-side mirror of the dashboard M5 rule signal; browser input is not trusted."""
    if not isinstance(rows, list) or len(rows) < 35:
        return {"direction": "neutral", "qualityScore": 0, "reason": "At least 35 completed M5 candles are required."}
    try:
        closes = [float(row["close"]) for row in rows]
        def ema(values, period):
            alpha = 2.0 / (period + 1.0)
            value = sum(values[:period]) / period
            for point in values[period:]:
                value = point * alpha + value * (1.0 - alpha)
            return value
        ema9, ema21 = ema(closes, 9), ema(closes, 21)
        changes = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
        avg_gain = sum(max(0.0, change) for change in changes) / 14.0
        avg_loss = sum(max(0.0, -change) for change in changes) / 14.0
        rsi = 100.0 if avg_loss <= 1e-12 and avg_gain > 0 else 50.0 if avg_loss <= 1e-12 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
        last = rows[-1]
        prior = rows[-21:-1]
        prior_low = min(float(row["low"]) for row in prior)
        prior_high = max(float(row["high"]) for row in prior)
        sweep_low = float(last["low"]) < prior_low and float(last["close"]) > prior_low
        sweep_high = float(last["high"]) > prior_high and float(last["close"]) < prior_high
        bullish_trend = ema9 > ema21 and float(last["close"]) > ema9
        bearish_trend = ema9 < ema21 and float(last["close"]) < ema9
        bullish_candle = float(last["close"]) > float(last["open"])
        bearish_candle = float(last["close"]) < float(last["open"])
        avg_volume = sum(float(row.get("volume", 0.0) or 0.0) for row in prior[-20:]) / max(1, min(20, len(prior)))
        volume_support = avg_volume > 0 and float(last.get("volume", 0.0) or 0.0) >= avg_volume * 0.8
        bull_rsi = 50 <= rsi <= 68
        bear_rsi = 32 <= rsi <= 50
        buy_score = (25 if bullish_trend else 0) + (30 if sweep_low else 0) + (20 if bull_rsi else 0) + (15 if bullish_candle else 0) + (10 if volume_support else 0)
        sell_score = (25 if bearish_trend else 0) + (30 if sweep_high else 0) + (20 if bear_rsi else 0) + (15 if bearish_candle else 0) + (10 if volume_support else 0)
        if bullish_trend and (sweep_low or (bull_rsi and bullish_candle)) and buy_score >= 65 and buy_score > sell_score:
            direction, score = "buy", buy_score
        elif bearish_trend and (sweep_high or (bear_rsi and bearish_candle)) and sell_score >= 65 and sell_score > buy_score:
            direction, score = "sell", sell_score
        else:
            direction, score = "neutral", max(buy_score, sell_score)
        return {
            "direction": direction, "qualityScore": int(score), "rsi14": round(rsi, 2),
            "ema9": round(ema9, 5), "ema21": round(ema21, 5),
            "liquiditySweep": bool(sweep_low or sweep_high),
            "reason": "Server-side completed M5 rule signal confirms the requested side." if direction != "neutral" else "No sufficiently aligned completed M5 setup.",
        }
    except Exception as exc:
        return {"direction": "neutral", "qualityScore": 0, "reason": f"M5 rule-signal calculation failed: {exc}"}


def combined_brain_for(requested: str, direction: str | None = None, recovery_context=False):
    """Run all configured brains on one shared MT5 snapshot and return one verdict."""
    ensure_mt5()
    symbol = resolve_symbol(requested)
    requested_direction = (direction or "neutral").strip().lower()
    snap = snapshot(symbol)
    history_rows = candles(symbol, AI_ENSEMBLE_TRAIN_BARS, include_forming=False)

    # The local rule signal is independently recomputed on the bridge from
    # completed M5 bars. The browser cannot manufacture a directional signal.
    rule_signal = evaluate_m5_entry_signal(history_rows)

    # Each direction model receives the exact same ordered M5 candle batch.
    xgb_data = xgb_confirmation(symbol, requested_direction, history_rows[-max(160, int(XGB_TRAIN_BARS)):])
    ensemble_data = ai_ensemble_confirmation(symbol, requested_direction, history_rows)

    xgb_enabled = bool(xgb_data.get("enabled", True))
    ensemble_enabled = bool(ensemble_data.get("enabled", True))
    vote_items = []
    if xgb_enabled and BRAIN_REQUIRE_XGB and BRAIN_XGB_WEIGHT > 0:
        vote_items.append(("xgboost", float(xgb_data.get("directionProbability", 0.5)), BRAIN_XGB_WEIGHT))
    if ensemble_enabled and BRAIN_REQUIRE_ENSEMBLE and BRAIN_ENSEMBLE_WEIGHT > 0:
        vote_items.append(("ensemble", float(ensemble_data.get("directionProbability", 0.5)), BRAIN_ENSEMBLE_WEIGHT))
    vote_items = [(name, max(0.0, min(1.0, score)), weight) for name, score, weight in vote_items]
    total_weight = sum(item[2] for item in vote_items)
    consensus_probability = (
        sum(score * weight for _, score, weight in vote_items) / total_weight
        if total_weight > 0 else 0.5
    )
    consensus_result = {
        "probability": round(consensus_probability, 4),
        "threshold": BRAIN_CONSENSUS_THRESHOLD,
        "passed": consensus_probability >= BRAIN_CONSENSUS_THRESHOLD if vote_items else True,
        "weights": {name: weight for name, _, weight in vote_items},
    }

    # Enabled external AI supervisors run last: each receives the shared candle
    # set, model outputs, meta-label score, and the already calculated consensus.
    ai_data = ai_decision_for(
        symbol,
        requested_direction,
        prepared_snapshot=snap,
        candle_rows=history_rows,
        xgb_result=xgb_data,
        ensemble_result=ensemble_data,
        consensus_result=consensus_result,
        recovery_context=recovery_context,
    )

    xgb_ready = bool(xgb_data.get("available") and xgb_data.get("ready"))
    ensemble_ready = bool(ensemble_data.get("available") and ensemble_data.get("ready"))
    # A brain marked as required must be installed, enabled, ready, and agree.
    # Disabling a required model is a blocker rather than silently removing its vote.
    xgb_pass = (not BRAIN_REQUIRE_XGB) or (
        xgb_enabled
        and xgb_ready
        and xgb_data.get("direction") == requested_direction
        and bool(xgb_data.get("confirmed"))
    )
    ensemble_pass = (not BRAIN_REQUIRE_ENSEMBLE) or (
        ensemble_enabled
        and ensemble_ready
        and ensemble_data.get("direction") == requested_direction
        and bool(ensemble_data.get("confirmed"))
    )

    # The ensemble includes LightGBM/CatBoost and optional GRU/TCN directional
    # votes, then separately applies its meta-label probability gate.
    # A no-direction or conflicting vote is never translated into approval.
    ai_enabled = bool(ai_data.get("enabled"))
    ai_pass = (not ai_enabled) or (
        bool(ai_data.get("available"))
        and bool(ai_data.get("ready"))
        and bool(ai_data.get("allowed"))
        and ai_data.get("preferredDirection") == requested_direction
    )
    account_pass = bool(snap.get("account", {}).get("tradeAllowed", False))
    existing_positions = snap.get("positions", [])
    same_side_pass = (not existing_positions) or (recovery_context and all(p.get("direction") == requested_direction for p in existing_positions))
    flat_pass = same_side_pass
    spread_points = float(snap.get("quote", {}).get("spread", 0.0) or 0.0)
    margin_level = float(snap.get("account", {}).get("marginLevel", 0.0) or 0.0)
    margin_used = float(snap.get("account", {}).get("margin", 0.0) or 0.0)
    spread_pass = spread_points <= BRAIN_MAX_SPREAD_POINTS
    margin_pass = margin_level_is_safe(margin_used, margin_level, max(BRAIN_MIN_MARGIN_LEVEL, RISK_MIN_MARGIN_LEVEL))
    try:
        # M1 is used only as an entry-timing confirmation. M5 remains the
        # primary signal/model timeframe; M15/H1 provide broader directional context.
        mtf1 = timeframe_bias(symbol, "M1")
        mtf15 = timeframe_bias(symbol, "M15")
        mtf60 = timeframe_bias(symbol, "H1")
        m1_pass = mtf1 == requested_direction
        mtf_pass = mtf15 == requested_direction and mtf60 == requested_direction
    except Exception:
        mtf1, mtf15, mtf60, m1_pass, mtf_pass = "unavailable", "unavailable", "unavailable", False, False
    directional_pass = requested_direction in {"buy", "sell"} and rule_signal.get("direction") == requested_direction
    consensus_pass = consensus_probability >= BRAIN_CONSENSUS_THRESHOLD
    checks = {
        "directionalSignal": directional_pass,
        "xgboost": xgb_pass,
        "multiModelEnsemble": ensemble_pass,
        "combinedConsensus": consensus_pass if vote_items else True,
        "openAiSupervisor": ai_pass,
        "accountTradingAllowed": account_pass,
        "spreadLimit": spread_pass,
        "marginFloor": margin_pass,
        "noExistingPositionsOrOpposingBasket": flat_pass,
        "m1EntryTiming": m1_pass,
        "m15H1TrendAlignment": mtf_pass,
    }
    required_brains_pass = all(checks.values())
    blockers = [name for name, passed in checks.items() if not passed]
    approved = bool(required_brains_pass)

    direction_votes = []
    for name, data in (("xgboost", xgb_data), ("ensemble", ensemble_data)):
        if not data.get("enabled", True):
            continue
        probability = float(data.get("directionProbability", 0.5))
        direction_votes.append({
            "brain": name,
            "direction": data.get("direction", "neutral"),
            "directionProbability": round(probability, 4),
            "confirmed": bool(data.get("confirmed", False)),
            "ready": bool(data.get("ready", False)),
        })

    verdict = {
        "enabled": True,
        "ready": bool((not BRAIN_REQUIRE_XGB or (xgb_enabled and xgb_ready)) and (not BRAIN_REQUIRE_ENSEMBLE or (ensemble_enabled and ensemble_ready))),
        "approved": approved,
        "symbol": symbol,
        "timeframe": TIMEFRAME_NAME,
        "direction": requested_direction,
        "ruleSignal": rule_signal,
        "consensusProbability": round(consensus_probability, 4),
        "consensusThreshold": BRAIN_CONSENSUS_THRESHOLD,
        "spreadPoints": spread_points,
        "maxSpreadPoints": BRAIN_MAX_SPREAD_POINTS,
        "marginLevel": margin_level,
        "minimumMarginLevel": max(BRAIN_MIN_MARGIN_LEVEL, RISK_MIN_MARGIN_LEVEL),
        "multiTimeframe": {"M1": mtf1, "M5": requested_direction, "M15": mtf15, "H1": mtf60,
                            "entryTimingAligned": m1_pass, "trendAligned": mtf_pass,
                            "aligned": bool(m1_pass and mtf_pass)},
        "recoveryContext": bool(recovery_context),
        "votes": direction_votes,
        "checks": checks,
        "blockers": blockers,
        "reason": "All configured brains and hard account checks approved the same direction." if approved else "Blocked by: " + ", ".join(blockers),
        "modelFlow": ["server_completed_m5_rule_signal", "xgboost", "lightgbm_catboost_sequence_ensemble", "meta_label", "m1_entry_timing", "m15_h1_trend_alignment", "enabled_external_ai_supervisors", "risk_gates", "mt5_execution"],
        "timestamp": int(time.time() * 1000),
    }
    return {
        "ok": True,
        "symbol": symbol,
        "direction": requested_direction,
        "xgb": xgb_data,
        "ensemble": ensemble_data,
        "ai": ai_data,
        "brain": verdict,
    }


def _stop_prices(symbol: str, direction: str, entry_price: float, atr: float):
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol_info({symbol}) failed")
    point = float(getattr(info, "point", 0.0) or 0.0)
    if point <= 0:
        raise RuntimeError("Broker symbol point size is invalid.")
    minimum_points = max(int(getattr(info, "trade_stops_level", 0) or 0), int(getattr(info, "trade_freeze_level", 0) or 0)) + 2
    distance = max(atr * RISK_STOP_ATR_MULTIPLIER, minimum_points * point)
    digits = int(getattr(info, "digits", 2))
    stop = entry_price - distance if direction == "buy" else entry_price + distance
    return round(stop, digits), distance


def _risk_size_for_order(symbol, direction, entry_price, stop_price, equity, risk_pct, count):
    info = mt5.symbol_info(symbol)
    order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    return size_equal_burst(
        order_calc_profit=mt5.order_calc_profit,
        order_type=order_type,
        symbol=symbol,
        entry_price=entry_price,
        stop_price=stop_price,
        equity=equity,
        risk_pct=risk_pct,
        order_count=count,
        volume_min=float(info.volume_min),
        volume_max=float(info.volume_max),
        volume_step=float(info.volume_step),
        estimated_cost_per_lot=ESTIMATED_ROUND_TRIP_COST_PER_LOT,
    )


def _calculate_initial_risk_pct(equity, history):
    # Only realized net profits can fund a capped bonus, and previously committed
    # profit cannot be reused for later baskets. State is account/login scoped.
    if risk_state is None:
        raise RuntimeError("Risk Guardian state is not initialized; compounding is unavailable.")
    realized_net = sum(float(item.get("profit", 0.0) or 0.0) for item in history)
    compound = capped_compound_bonus(
        equity=float(equity), realized_net_profit=realized_net,
        profit_share=COMPOUND_PROFIT_SHARE,
        already_committed=float(risk_state.get("compoundSpent", 0.0) or 0.0),
        maximum_bonus_pct=COMPOUND_MAX_BONUS_PCT,
    )
    risk_pct = min(
        RISK_BASE_BASKET_PCT + compound["bonusPct"],
        RISK_MAX_BASKET_PCT - RISK_RECOVERY_RESERVE_PCT,
    )
    compound.update({
        "realizedNetProfit": round(realized_net, 2),
        "profitFundedPool": compound["earnedPool"],
        "compoundBonusPct": compound["bonusPct"],
    })
    return risk_pct, compound


def _commit_compound_bonus(account, bonus_cash):
    """Persist the profit-pool reservation before any ticket can be sent."""
    global risk_state
    amount = float(bonus_cash or 0.0)
    if amount <= 0:
        return
    with execution_state_lock:
        state = _ensure_risk_state(account)
        state["compoundSpent"] = round(float(state.get("compoundSpent", 0.0) or 0.0) + amount, 2)
        state["updatedAt"] = time.time()
        _risk_state_write(state)
        risk_state = state


def _release_compound_bonus(account, bonus_cash):
    """Refund a reservation only when the bridge verified that zero tickets filled."""
    global risk_state
    amount = float(bonus_cash or 0.0)
    if amount <= 0:
        return
    with execution_state_lock:
        state = _ensure_risk_state(account)
        state["compoundSpent"] = max(0.0, round(float(state.get("compoundSpent", 0.0) or 0.0) - amount, 2))
        state["updatedAt"] = time.time()
        _risk_state_write(state)
        risk_state = state


def build_order(symbol: str, direction: str, volume: float, filling: int, stop_loss: float, comment=COMMENT):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"symbol_info_tick({symbol}) failed")
    price = float(tick.ask if direction == "buy" else tick.bid)
    order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    return {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol,
        "volume": float(volume), "type": order_type, "price": price,
        "sl": float(stop_loss), "tp": 0.0, "deviation": DEVIATION,
        "magic": MAGIC, "comment": str(comment)[:31],
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
    }


def send_single(symbol: str, direction: str, volume: float, stop_loss: float, comment=COMMENT):
    errors = []
    for filling in pick_filling(symbol):
        request = build_order(symbol, direction, volume, filling, stop_loss, comment)
        try:
            check = mt5.order_check(request)
            if check is None or int(getattr(check, "retcode", -1)) != 0:
                errors.append(f"preflight rejected: {getattr(check, 'comment', 'no result') if check is not None else mt5.last_error()}")
                continue
            result = mt5.order_send(request)
            if result is None:
                errors.append(f"order_send None: {mt5.last_error()}")
                continue
            retcode = int(result.retcode)
            if retcode == getattr(mt5, "TRADE_RETCODE_DONE", 10009):
                return {
                    "ok": True, "retcode": retcode, "comment": str(result.comment or ""),
                    "order": int(result.order), "deal": int(result.deal),
                    "price": float(result.price), "volume": float(result.volume),
                    "stopLoss": float(stop_loss), "partial": False,
                }
            if retcode == getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010):
                return {
                    "ok": False, "partial": True, "retcode": retcode,
                    "comment": "Partial fill returned; remaining burst tickets stopped for reconciliation.",
                    "order": int(getattr(result, "order", 0) or 0),
                    "deal": int(getattr(result, "deal", 0) or 0),
                    "price": float(getattr(result, "price", 0.0) or 0.0),
                    "volume": float(getattr(result, "volume", 0.0) or 0.0),
                    "stopLoss": float(stop_loss),
                }
            errors.append(f"retcode={retcode}, comment={result.comment}, filling={filling}")
        except Exception as exc:
            errors.append(repr(exc))
    return {"ok": False, "retcode": 0, "comment": " | ".join(errors), "order": 0, "deal": 0, "price": 0, "volume": 0.0, "stopLoss": float(stop_loss), "partial": False}


def _verify_position_stops(symbol: str, expected_fill=False):
    # MT5 may publish a fill a fraction of a second after order_send returns.
    # Retry briefly, but treat a failed position query as unsafe rather than empty.
    positions = None
    for attempt in range(5):
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            print(f"[NovaFury] CRITICAL: position query failed during SL verification: {mt5.last_error()}", flush=True)
            return False
        bot_positions = [p for p in positions if int(getattr(p, "magic", 0)) == MAGIC]
        if bot_positions or not expected_fill:
            break
        time.sleep(0.20)
    if expected_fill and not bot_positions:
        print("[NovaFury] CRITICAL: MT5 reported a fill but no NovaFury position became visible; execution disarmed for reconciliation.", flush=True)
        return False
    missing = []
    for position in bot_positions:
        if not _position_stop_is_live(position):
            missing.append(position)
    for position in missing:
        # If the broker omitted or corrupted the requested SL, immediately attempt
        # to close that exposure. The caller still disarms even if close succeeds.
        print(f"[NovaFury] CRITICAL: position {position.ticket} has no valid broker SL; attempting immediate close", flush=True)
        close_position(position)
    return not missing


def open_burst(requested: str, direction: str, volume: float, count: int, source="manual", expected_login=None, expected_server=None):
    """Open one fixed ten-ticket basket under a single aggregate stop-risk budget."""
    ensure_mt5()
    if direction not in ("buy", "sell"):
        raise ValueError("direction must be buy or sell")
    with execution_lock:
        with execution_state_lock:
            burst_epoch = execution_epoch
        symbol = resolve_symbol(requested)
        account = account_payload()
        _assert_account_authorized(account)
        if (expected_login is None or int(expected_login) != int(account["login"])
                or str(expected_server or "") != str(account["server"])):
            raise RuntimeError("Order rejected: active MT5 login/server does not match the confirmed dashboard account.")
        if source == "auto":
            if not execution_armed or not armed_account or armed_account != (int(account["login"]), str(account["server"])):
                raise RuntimeError("Automatic execution is disarmed. Confirm the connected MT5 account and arm Auto Trading.")
        elif source != "manual":
            raise RuntimeError("Invalid execution source.")

        quote = symbol_quote(symbol)
        _risk_gate(account, quote, allow_existing=False)
        rows = candles(symbol, 100, include_forming=False)
        atr = calculate_atr(rows, int(RISK_PROFILE["atr_period"]))
        entry = float(quote["ask"] if direction == "buy" else quote["bid"])
        stop_loss, stop_distance = _stop_prices(symbol, direction, entry, atr)
        # The compounding pool must include the full available closed-trade ledger,
        # not just the 100 rows shown in the dashboard's recent-history table.
        history = recent_history(symbol, days=3650, limit=10000)
        risk_pct, compound = _calculate_initial_risk_pct(float(account["equity"]), history)
        initial_size = _risk_size_for_order(
            symbol, direction, entry, stop_loss, float(account["equity"]), risk_pct, FIXED_BURST_ORDERS
        )
        risk_budget = float(initial_size["riskBudget"])
        absolute_budget = float(account["equity"]) * (RISK_MAX_BASKET_PCT - RISK_RECOVERY_RESERVE_PCT)
        if risk_budget > absolute_budget + 0.01:
            raise RuntimeError("Calculated initial basket risk exceeds the reserved Balanced risk allocation.")

        compound_bonus_reserved = float(compound.get("bonusCash", 0.0) or 0.0)
        _commit_compound_bonus(account, compound_bonus_reserved)

        results = []
        risk_spent = 0.0
        margin_reserved = 0.0
        base_margin = float(account.get("margin", 0.0) or 0.0)
        risk_reconcile_failed = False
        volume_requested = 0.0
        basket_tag = f"NF2-{int(time.time()) % 100000000:08d}-{'B' if direction == 'buy' else 'S'}"
        order_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol_info({symbol}) failed before burst execution")

        for index in range(FIXED_BURST_ORDERS):
            current_account = account_payload()
            try:
                if int(current_account["login"]) != int(account["login"]) or str(current_account["server"]) != str(account["server"]):
                    _disarm("MT5 account changed during burst")
                    raise RuntimeError("MT5 account changed during burst; remaining tickets stopped.")
                _assert_account_authorized(current_account)
                current_quote = symbol_quote(symbol)
                _risk_gate(current_account, current_quote, allow_existing=True)
                current_price = float(current_quote["ask"] if direction == "buy" else current_quote["bid"])
                point = float(current_quote["point"])
                if (direction == "buy" and stop_loss >= current_price) or (direction == "sell" and stop_loss <= current_price):
                    raise RuntimeError("Price moved through the planned protective stop; remaining burst tickets stopped.")
                min_stop_distance = (int(getattr(info, "trade_stops_level", 0) or 0) + 2) * point
                if abs(current_price - stop_loss) < min_stop_distance:
                    raise RuntimeError("Protective stop is inside broker minimum stop distance; remaining burst tickets stopped.")

                remaining_count = FIXED_BURST_ORDERS - index
                remaining_budget = max(0.0, risk_budget - risk_spent)
                current_equity = float(current_account["equity"])
                if current_equity <= 0 or remaining_budget <= 0:
                    raise RuntimeError("No approved basket risk budget remains for another ticket.")
                # Recalculate each ticket against the live quote and remaining cash-risk
                # budget. This prevents price drift during sequential execution from
                # multiplying the aggregate planned loss beyond the original basket cap.
                ticket_size = _risk_size_for_order(
                    symbol, direction, current_price, stop_loss, current_equity,
                    remaining_budget / current_equity, remaining_count,
                )
                requested_volume = float(ticket_size["volumePerOrder"])
                projected_margin = mt5.order_calc_margin(order_type, symbol, requested_volume, current_price)
                if projected_margin is None or not math.isfinite(float(projected_margin)):
                    raise RuntimeError("MT5 could not calculate projected margin for the next burst ticket.")
                # Account margin can lag an individual deal update. Reserve the
                # broker-calculated margin for already-filled tickets as well as
                # the next ticket, using the larger of live account margin and
                # initial margin plus our local fill reservations.
                effective_margin = max(float(current_account["margin"]), base_margin + margin_reserved)
                if (effective_margin + float(projected_margin)) / current_equity > RISK_MAX_MARGIN_UTIL_PCT:
                    raise RuntimeError("Projected margin utilization would exceed the Balanced 25% ceiling; remaining burst tickets stopped.")
                volume_requested += requested_volume
                # Serialize the final send with /api/disarm. A disarm increments
                # execution_epoch; after that, no additional manual or auto ticket
                # from this burst can be submitted.
                with execution_state_lock:
                    if execution_epoch != burst_epoch:
                        raise RuntimeError("Execution was disarmed during burst; remaining tickets stopped.")
                    if source == "auto" and (not execution_armed or armed_account != (int(account["login"]), str(account["server"]))):
                        raise RuntimeError("Auto Trading was disarmed during burst; remaining tickets stopped.")
                    result = send_single(
                        symbol, direction, requested_volume, stop_loss,
                        f"{basket_tag}-{index + 1:02d}",
                    )
                result["requestedVolume"] = requested_volume
                if result.get("ok") or result.get("partial"):
                    filled_volume = float(result.get("volume", 0.0) or (requested_volume if result.get("ok") else 0.0))
                    fill_price = float(result.get("price", 0.0) or current_price)
                    fill_loss = mt5.order_calc_profit(order_type, symbol, filled_volume, fill_price, stop_loss)
                    actual_margin = mt5.order_calc_margin(order_type, symbol, filled_volume, fill_price)
                    if actual_margin is None or not math.isfinite(float(actual_margin)):
                        risk_reconcile_failed = True
                        result["marginReconciliation"] = "failed"
                    else:
                        margin_reserved += max(0.0, float(actual_margin))
                        result["marginReconciliation"] = "verified"
                    if fill_loss is None or not math.isfinite(float(fill_loss)):
                        risk_reconcile_failed = True
                        result["estimatedStopRisk"] = None
                        result["riskReconciliation"] = "failed"
                    else:
                        ticket_risk = abs(float(fill_loss)) + filled_volume * ESTIMATED_ROUND_TRIP_COST_PER_LOT
                        risk_spent += ticket_risk
                        result["estimatedStopRisk"] = round(ticket_risk, 2)
                        result["riskReconciliation"] = "verified"
                    result["fillPrice"] = fill_price
                else:
                    result["estimatedStopRisk"] = 0.0
            except Exception as exc:
                result = {"ok": False, "partial": False, "comment": str(exc), "volume": 0.0,
                          "requestedVolume": 0.0, "estimatedStopRisk": 0.0}
            results.append(result)
            # Any rejection or partial fill ends the burst. Reconciliation/management
            # takes over; we never send the remaining tickets blindly.
            if not result.get("ok") or risk_reconcile_failed:
                break

        filled = sum(1 for result in results if result.get("ok") or result.get("partial"))
        if filled == 0 and compound_bonus_reserved > 0:
            try:
                _release_compound_bonus(account, compound_bonus_reserved)
            except Exception as refund_exc:
                print(f"[NovaFury] WARNING: unused compounding reservation could not be refunded: {refund_exc!r}", flush=True)
        stops_ok = _verify_position_stops(symbol, expected_fill=filled > 0)
        actual_stop_risk = None
        try:
            actual_stop_risk = _current_bot_stop_risk(symbol)["risk"]
        except Exception as exposure_exc:
            risk_reconcile_failed = True
            print(f"[NovaFury] CRITICAL: aggregate stop-risk reconciliation failed: {exposure_exc!r}", flush=True)
        risk_overrun = (risk_spent > risk_budget + 0.01) or (
            actual_stop_risk is not None and actual_stop_risk > risk_budget + 0.01
        )
        emergency_close = None
        if not stops_ok or risk_overrun or risk_reconcile_failed:
            _disarm("Protective stop verification or aggregate risk reconciliation failed")
            # If broker-side protection or aggregate risk cannot be proven, attempt
            # to flatten the entire NovaFury basket, not just the ticket with a bad SL.
            try:
                emergency_close = close_all(symbol)
                if not emergency_close.get("ok"):
                    print(f"[NovaFury] CRITICAL: emergency basket close incomplete: {emergency_close}", flush=True)
            except Exception as close_exc:
                emergency_close = {"ok": False, "error": repr(close_exc)}
                print(f"[NovaFury] CRITICAL: emergency basket close failed: {close_exc!r}", flush=True)
        return {
            "ok": filled == FIXED_BURST_ORDERS and len(results) == FIXED_BURST_ORDERS and stops_ok and not risk_overrun and not risk_reconcile_failed,
            "symbol": symbol, "direction": direction, "countRequested": FIXED_BURST_ORDERS,
            "countSubmitted": len(results), "countFilled": filled,
            "countRejected": sum(1 for result in results if not result.get("ok") and not result.get("partial")),
            "volumePerOrder": float(initial_size["volumePerOrder"]),
            "aggregateVolumeRequested": round(volume_requested, 8),
            "stopLoss": stop_loss, "stopDistance": stop_distance, "atr": atr,
            "estimatedStopLoss": round(risk_spent, 2), "actualBrokerStopRisk": actual_stop_risk,
            "riskBudget": round(risk_budget, 2), "riskPct": risk_pct, "compound": compound, "source": source,
            "protectiveStopsVerified": stops_ok, "riskOverrun": risk_overrun,
            "riskReconciliationFailed": risk_reconcile_failed, "emergencyClose": emergency_close,
            "results": results,
        }


def recovery_pending_orders(symbol: str | None = None):
    """Return all pending orders owned by NovaFury's magic number.

    This intentionally includes legacy V1 comment formats so a leftover pending
    order cannot execute after V2 is armed or after the dashboard is stopped.
    """
    orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    if orders is None:
        raise RuntimeError(f"MT5 pending-order query failed: {mt5.last_error()}")
    return [o for o in orders if int(getattr(o, "magic", 0)) == MAGIC]


def delete_pending_order(order_ticket: int):
    for _ in range(2):
        try:
            result = mt5.order_send({
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": int(order_ticket),
                "magic": MAGIC,
                "comment": COMMENT,
            })
            if result is not None and int(result.retcode) in (
                getattr(mt5, "TRADE_RETCODE_DONE", 10009),
                getattr(mt5, "TRADE_RETCODE_PLACED", 10008),
            ):
                return True
        except Exception:
            pass
    return False


def _recovery_fill_count(symbol: str, basket_tag: str):
    """Count distinct recovery position IDs, including already-closed fills."""
    start = datetime.now() - timedelta(days=90)
    end = datetime.now()
    deals = mt5.history_deals_get(start, end, group=symbol + "*")
    if deals is None:
        raise RuntimeError(f"Recovery history query failed: {mt5.last_error()}")
    basket_key = str(basket_tag)[-8:]
    order_ids = set()
    position_ids = set()
    entry_codes = {getattr(mt5, "DEAL_ENTRY_IN", 0), getattr(mt5, "DEAL_ENTRY_INOUT", 2)}
    for deal in deals:
        comment = str(getattr(deal, "comment", "") or "")
        if int(getattr(deal, "magic", 0)) != MAGIC or not comment.startswith(RECOVERY_COMMENT_PREFIX):
            continue
        if basket_key not in comment or int(getattr(deal, "entry", -1)) not in entry_codes:
            continue
        order_id = int(getattr(deal, "order", 0) or 0)
        position_id = int(getattr(deal, "position_id", 0) or 0)
        if order_id:
            order_ids.add(order_id)  # one accepted pending order counts once even if it partially fills
        elif position_id:
            position_ids.add(position_id)
    # If this broker does not retain order IDs in entry history, fall back to current
    # recovery positions. Order IDs are preferred because MT5 netting accounts may merge
    # several fills into one position identifier.
    if not order_ids and not position_ids:
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            raise RuntimeError(f"Recovery position query failed: {mt5.last_error()}")
        for position in positions:
            comment = str(getattr(position, "comment", "") or "")
            if int(getattr(position, "magic", 0)) == MAGIC and comment.startswith(RECOVERY_COMMENT_PREFIX) and basket_key in comment:
                position_ids.add(int(getattr(position, "identifier", 0) or getattr(position, "ticket", 0)))
    return len(order_ids or position_ids)


def place_recovery_pending(position, atr, risk_pct, basket_tag):
    symbol = str(position.symbol)
    direction = "buy" if int(position.type) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "sell"
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return {"ok": False, "comment": f"No symbol/tick for {symbol}"}
    point = float(info.point)
    digits = int(info.digits)
    # Enter only after a confirmed pullback. The limit is placed a small ATR
    # fraction beyond the confirmation quote, on the correct side of market.
    buffer = max(point * 2, atr * 0.10)
    price = float(tick.ask - buffer if direction == "buy" else tick.bid + buffer)
    price = round(price, digits)
    if direction == "buy" and price >= float(tick.ask):
        return {"ok": False, "comment": "Recovery BUY limit is not below ask"}
    if direction == "sell" and price <= float(tick.bid):
        return {"ok": False, "comment": "Recovery SELL limit is not above bid"}
    stop_loss, _ = _stop_prices(symbol, direction, price, atr)
    size = _risk_size_for_order(symbol, direction, price, stop_loss,
                                float(account_payload()["equity"]), risk_pct, 1)
    order_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == "buy" else mt5.ORDER_TYPE_SELL_LIMIT
    comment = f"{RECOVERY_COMMENT_PREFIX}-{str(basket_tag)[-8:]}-{int(position.ticket) % 1000000:06d}"[:31]
    margin_type = mt5.ORDER_TYPE_BUY if direction == "buy" else mt5.ORDER_TYPE_SELL
    projected_margin = mt5.order_calc_margin(margin_type, symbol, size["volumePerOrder"], price)
    account = account_payload()
    if projected_margin is None or not math.isfinite(float(projected_margin)):
        return {"ok": False, "comment": "MT5 could not calculate projected margin for the recovery order."}
    if (float(account["margin"]) + float(projected_margin)) / max(float(account["equity"]), 0.01) > RISK_MAX_MARGIN_UTIL_PCT:
        return {"ok": False, "comment": "Recovery would exceed the Balanced margin-utilization ceiling."}
    request = {
        "action": mt5.TRADE_ACTION_PENDING, "symbol": symbol,
        "volume": size["volumePerOrder"], "type": order_type,
        "price": price, "sl": stop_loss, "tp": 0.0,
        "deviation": DEVIATION, "magic": MAGIC, "comment": comment,
        "type_time": mt5.ORDER_TIME_SPECIFIED,
        "expiration": int(time.time()) + RECOVERY_PENDING_EXPIRY_MINUTES * 60,
        "type_filling": getattr(mt5, "ORDER_FILLING_RETURN", 2),
    }
    check = mt5.order_check(request)
    if check is None or int(getattr(check, "retcode", -1)) != 0:
        return {"ok": False, "comment": f"Recovery preflight rejected: {getattr(check, 'comment', 'no result') if check is not None else mt5.last_error()}"}
    # Atomic with disarm: if the user disarms while AI approval/order_check is
    # running, the pending recovery order is not sent. If send wins the lock,
    # disarm runs immediately afterward and removes/verifies the new order.
    with execution_state_lock:
        if not execution_armed or armed_account != (int(account["login"]), str(account["server"])):
            return {"ok": False, "comment": "Recovery cancelled because automatic execution was disarmed before submission."}
        result = mt5.order_send(request)
    if result is not None and int(result.retcode) in (getattr(mt5, "TRADE_RETCODE_DONE", 10009), getattr(mt5, "TRADE_RETCODE_PLACED", 10008)):
        order_ticket = int(getattr(result, "order", 0) or 0)
        if order_ticket:
            verified = mt5.orders_get(ticket=order_ticket)
            if verified is None:
                return {"ok": False, "comment": f"Recovery order accepted but broker verification failed: {mt5.last_error()}", "order": order_ticket}
            if not verified:
                # It may have triggered immediately; require an identifiable live recovery position.
                live = mt5.positions_get(symbol=symbol)
                if live is None or not any(int(getattr(p, "magic", 0)) == MAGIC and str(getattr(p, "comment", "") or "").startswith(RECOVERY_COMMENT_PREFIX) for p in live):
                    return {"ok": False, "comment": "Recovery order was accepted but could not be found as pending or filled; manager remains fail-closed.", "order": order_ticket}
        return {"ok": True, "order": order_ticket, "price": price,
                "volume": size["volumePerOrder"], "stopLoss": stop_loss, "parentTicket": int(position.ticket),
                "riskBudget": size["riskBudget"]}
    return {"ok": False, "comment": str(getattr(result, "comment", "order_send failed") if result is not None else mt5.last_error())}


def _manage_recovery_once_locked():
    if not RECOVERY_ENABLED or not execution_armed:
        return
    ensure_mt5()
    symbol = resolve_symbol(REQUESTED_SYMBOL)
    raw_positions = mt5.positions_get(symbol=symbol)
    if raw_positions is None:
        raise RuntimeError(f"Recovery position query failed: {mt5.last_error()}")
    positions = [p for p in raw_positions if int(getattr(p, "magic", 0)) == MAGIC]
    if not positions:
        return
    directions = {"buy" if int(p.type) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "sell" for p in positions}
    if len(directions) != 1:
        return
    direction = next(iter(directions))
    pending = recovery_pending_orders(symbol)
    if pending:
        return
    # Only the oldest non-recovery parent is used to avoid a burst placing many pending orders.
    parents = [
        p for p in positions
        if str(getattr(p, "comment", "") or "").startswith("NF2-")
        and not str(getattr(p, "comment", "") or "").startswith(RECOVERY_COMMENT_PREFIX)
    ]
    if not parents:
        return
    parent = sorted(parents, key=lambda p: int(getattr(p, "time", 0)))[0]
    parent_comment = str(getattr(parent, "comment", "") or "NF2BASKET")
    basket_tag = parent_comment.rsplit("-", 1)[0] if parent_comment.rsplit("-", 1)[-1].isdigit() else parent_comment
    if _recovery_fill_count(symbol, basket_tag) >= RECOVERY_MAX_FILLS_PER_BASKET:
        return
    rows = candles(symbol, 100, include_forming=False)
    atr = calculate_atr(rows, int(RISK_PROFILE["atr_period"]))
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return
    # Measure favorable excursion since the parent opened, then wait for a 25–65% pullback.
    open_time = int(getattr(parent, "time", 0))
    recent = [row for row in rows if int(row["time"] / 1000) >= open_time]
    if not recent:
        recent = rows[-30:]
    entry = float(parent.price_open)
    if direction == "buy":
        favorable_extreme = max(float(row["high"]) for row in recent)
        current = float(tick.ask)
        excursion = favorable_extreme - entry
        pullback = favorable_extreme - current
    else:
        favorable_extreme = min(float(row["low"]) for row in recent)
        current = float(tick.bid)
        excursion = entry - favorable_extreme
        pullback = current - favorable_extreme
    if excursion < atr * RECOVERY_ARM_ATR_MULTIPLIER or excursion <= 0:
        return
    fraction = pullback / excursion
    if fraction < RECOVERY_PULLBACK_MIN_FRACTION or fraction > RECOVERY_PULLBACK_MAX_FRACTION:
        return
    # The parent thesis must still be valid and all mandatory brains must re-confirm.
    coordinated = combined_brain_for(symbol, direction, recovery_context=True)
    brain = coordinated.get("brain", {})
    if not coordinated.get("ok") or not brain.get("ready") or not brain.get("approved"):
        print(f"[NovaFury] Recovery blocked by AI/risk confirmation: {brain.get('reason', 'not approved')}", flush=True)
        return
    account = account_payload()
    quote = symbol_quote(symbol)
    _risk_gate(account, quote, allow_existing=True)
    # Recovery is capped at 0.10% equity across at most two fills (0.05% each),
    # but only the genuinely unused aggregate risk capacity may be allocated.
    current_stop_risk = _current_bot_stop_risk(symbol)["risk"]
    maximum_cash_risk = float(account["equity"]) * RISK_MAX_BASKET_PCT
    unused_capacity = max(0.0, maximum_cash_risk - current_stop_risk)
    per_fill_cash_risk = min(
        float(account["equity"]) * RISK_RECOVERY_RESERVE_PCT / RECOVERY_MAX_FILLS_PER_BASKET,
        unused_capacity,
    )
    if per_fill_cash_risk <= 0.01:
        print("[NovaFury] Recovery blocked: no unused cash-risk capacity remains inside the aggregate basket cap.", flush=True)
        return
    per_fill_risk_pct = per_fill_cash_risk / float(account["equity"])
    result = place_recovery_pending(parent, atr, per_fill_risk_pct, basket_tag)
    if result.get("ok"):
        print(f"[NovaFury] AI-confirmed recovery pending: parent={parent.ticket} dir={direction} price={result['price']} volume={result['volume']} pullback={fraction:.2f}", flush=True)
    else:
        print(f"[NovaFury] Recovery not placed: {result.get('comment', 'unknown')}", flush=True)


def manage_recovery_once():
    # Serialize pending-order creation against disarm, basket close and burst
    # submission. _disarm clears execution_armed before waiting for this lock.
    with execution_lock:
        return _manage_recovery_once_locked()


def recovery_loop():
    while True:
        try:
            manage_recovery_once()
        except Exception as exc:
            print(f"[NovaFury] Recovery manager blocked safely: {exc!r}", flush=True)
        time.sleep(RECOVERY_CHECK_SEC)


def manage_smart_profit_once():
    """Protect NovaFury positions with a break-even floor and monotonic trailing SL."""
    if not SMART_PROFIT_ENABLED:
        return
    ensure_mt5()
    positions = [
        p for p in (mt5.positions_get() or [])
        if int(getattr(p, "magic", 0)) == MAGIC
    ]
    active = {int(p.ticket) for p in positions}
    for ticket in list(smart_profit_peaks):
        if ticket not in active:
            smart_profit_peaks.pop(ticket, None)
            smart_profit_last_error.pop(ticket, None)

    for p in positions:
        ticket = int(p.ticket)
        symbol = str(p.symbol)
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            continue
        point = float(getattr(info, "point", 0.0) or 0.0)
        if point <= 0:
            continue
        digits = int(getattr(info, "digits", 2))
        is_buy = int(p.type) == getattr(mt5, "POSITION_TYPE_BUY", 0)
        entry = float(p.price_open)
        # Use the price at which this position could currently be closed.
        current = float(tick.bid if is_buy else tick.ask)
        old_sl = float(getattr(p, "sl", 0.0) or 0.0)

        # Seed each ticket's trailing peak from its own entry price. This keeps
        # each burst position's protection anchored independently to that entry.
        prior_peak = smart_profit_peaks.get(ticket, entry)
        peak = max(float(prior_peak), current) if is_buy else min(float(prior_peak), current)
        smart_profit_peaks[ticket] = peak
        favorable_points = ((peak - entry) / point) if is_buy else ((entry - peak) / point)
        # Arm at the first positive move. Broker minimum-stop/freeze distance may
        # delay the actual SL placement until the entry price can be protected.
        if favorable_points <= 0:
            continue

        offset = SMART_PROFIT_BE_OFFSET_POINTS * point
        trail = SMART_PROFIT_TRAIL_POINTS * point
        min_distance_points = max(
            int(getattr(info, "trade_stops_level", 0) or 0),
            int(getattr(info, "trade_freeze_level", 0) or 0),
        ) + 1
        min_distance = min_distance_points * point

        if is_buy:
            floor_sl = entry + offset
            candidate = max(floor_sl, peak - trail)
            broker_max_sl = current - min_distance
            # Do not set a stop in loss just to satisfy broker distance rules.
            if broker_max_sl < floor_sl:
                continue
            candidate = min(candidate, broker_max_sl)
            candidate = round(candidate, digits)
            if old_sl > 0 and candidate <= old_sl + SMART_PROFIT_STEP_POINTS * point * 0.999:
                continue
        else:
            ceiling_sl = entry - offset
            candidate = min(ceiling_sl, peak + trail)
            broker_min_sl = current + min_distance
            if broker_min_sl > ceiling_sl:
                continue
            candidate = max(candidate, broker_min_sl)
            candidate = round(candidate, digits)
            if old_sl > 0 and candidate >= old_sl - SMART_PROFIT_STEP_POINTS * point * 0.999:
                continue

        # Ignore a numerically unchanged stop after rounding. This avoids broker
        # request spam while keeping the existing take-profit untouched.
        if old_sl > 0 and abs(candidate - old_sl) < point * 0.5:
            continue
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": candidate,
            "tp": float(getattr(p, "tp", 0.0) or 0.0),
            "magic": MAGIC,
            "comment": SMART_PROFIT_COMMENT,
        }
        try:
            result = mt5.order_send(request)
            retcode = int(getattr(result, "retcode", 0) or 0) if result is not None else 0
            if result is not None and retcode == getattr(mt5, "TRADE_RETCODE_DONE", 10009):
                smart_profit_last_error.pop(ticket, None)
                print(
                    f"[NovaFury] Smart Profit SL updated: ticket={ticket} symbol={symbol} "
                    f"side={'BUY' if is_buy else 'SELL'} sl={candidate} "
                    f"favorable={favorable_points:.1f}pts trail={SMART_PROFIT_TRAIL_POINTS:g}pts",
                    flush=True,
                )
            else:
                reason = str(getattr(result, "comment", "order_send returned None") or "")
                now = time.monotonic()
                previous = smart_profit_last_error.get(ticket, (0.0, ""))
                if now - previous[0] >= 30 or reason != previous[1]:
                    print(
                        f"[NovaFury] Smart Profit SL not updated for ticket={ticket}: "
                        f"retcode={retcode} {reason}",
                        flush=True,
                    )
                    smart_profit_last_error[ticket] = (now, reason)
        except Exception as exc:
            now = time.monotonic()
            previous = smart_profit_last_error.get(ticket, (0.0, ""))
            reason = repr(exc)
            if now - previous[0] >= 30 or reason != previous[1]:
                print(f"[NovaFury] Smart Profit SL error for ticket={ticket}: {reason}", flush=True)
                smart_profit_last_error[ticket] = (now, reason)


def smart_profit_loop():
    while True:
        try:
            manage_smart_profit_once()
        except Exception as exc:
            now = time.monotonic()
            previous = smart_profit_last_error.get(0, (0.0, ""))
            reason = repr(exc)
            if now - previous[0] >= 30 or reason != previous[1]:
                print(f"[NovaFury] Smart Profit manager temporarily unavailable: {reason}", flush=True)
                smart_profit_last_error[0] = (now, reason)
        time.sleep(SMART_PROFIT_CHECK_SEC)


def close_position(p):
    symbol = str(p.symbol)
    direction = "sell" if int(p.type) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "buy"
    volume = round_volume(symbol, float(p.volume))
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"ok": False, "comment": f"no tick for {symbol}"}
    price = float(tick.bid if direction == "sell" else tick.ask)
    order_type = mt5.ORDER_TYPE_SELL if direction == "sell" else mt5.ORDER_TYPE_BUY
    errors = []
    for filling in pick_filling(symbol):
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "position": int(p.ticket),
            "price": price,
            "deviation": DEVIATION,
            "magic": MAGIC,
            "comment": COMMENT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        try:
            result = mt5.order_send(request)
            if result is not None and int(result.retcode) == getattr(mt5, "TRADE_RETCODE_DONE", 10009):
                return {"ok": True, "retcode": int(result.retcode), "comment": str(result.comment or ""), "ticket": int(p.ticket)}
            if result is not None and int(result.retcode) == getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010):
                return {"ok": False, "partial": True, "retcode": int(result.retcode), "comment": "Partial close; remaining exposure requires reconciliation.", "ticket": int(p.ticket)}
            errors.append(str(getattr(result, "comment", "")))
        except Exception as exc:
            errors.append(repr(exc))
    return {"ok": False, "comment": " | ".join(errors), "ticket": int(p.ticket)}


def close_positions_fast(positions):
    """Submit basket exits concurrently rather than waiting for each close in turn."""
    positions = list(positions or [])
    if not positions:
        return []
    workers = min(SMART_BULK_EXIT_MAX_WORKERS, len(positions))
    if workers == 1:
        return [close_position(positions[0])]

    results_by_index = [None] * len(positions)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nova-bulk-close") as pool:
        futures = {pool.submit(close_position, position): index for index, position in enumerate(positions)}
        for future, index in list(futures.items()):
            try:
                results_by_index[index] = future.result()
            except Exception as exc:
                results_by_index[index] = {
                    "ok": False,
                    "comment": repr(exc),
                    "ticket": int(getattr(positions[index], "ticket", 0) or 0),
                }
    return results_by_index


def cancel_recovery_for_basket(positions):
    """Cancel every recovery pending order for the basket and verify broker state."""
    symbol = str(positions[0].symbol) if positions else None
    cleanup = _cancel_all_recovery_orders(symbol)
    if not cleanup.get("ok"):
        raise RuntimeError(f"Recovery orders remain live; basket close aborted: {cleanup}")
    return cleanup


def _manage_smart_bulk_exit_once_locked():
    """Close the whole symbol basket after its configured estimated net-profit floor is met."""
    if not SMART_BULK_EXIT_ENABLED:
        return
    ensure_mt5()
    raw_positions = mt5.positions_get()
    if raw_positions is None:
        raise RuntimeError(f"MT5 position query failed during basket management: {mt5.last_error()}")
    bot_positions = [p for p in raw_positions if int(getattr(p, "magic", 0)) == MAGIC]
    baskets = {}
    for position in bot_positions:
        baskets.setdefault(str(position.symbol), []).append(position)

    for symbol, basket in baskets.items():
        with bulk_exit_lock:
            if symbol in bulk_exit_symbols_in_progress:
                continue

        # Exit the whole symbol basket when its combined floating P/L plus
        # swap enters the positive-profit zone. No per-position profit condition
        # is used: losing positions are closed along with profitable positions.
        profits = [
            float(getattr(p, "profit", 0.0) or 0.0) + float(getattr(p, "swap", 0.0) or 0.0)
            for p in basket
        ]
        estimated_close_cost = sum(float(getattr(p, "volume", 0.0) or 0.0) for p in basket) * ESTIMATED_ROUND_TRIP_COST_PER_LOT
        net_after_estimated_cost = sum(profits) - estimated_close_cost
        if not profits or net_after_estimated_cost < BASKET_MIN_NET_PROFIT:
            continue

        with bulk_exit_lock:
            if symbol in bulk_exit_symbols_in_progress:
                continue
            bulk_exit_symbols_in_progress.add(symbol)

        try:
            tickets = [int(p.ticket) for p in basket]
            print(
                f"[NovaFury] SMART BULK EXIT triggered: symbol={symbol} positions={len(basket)} "
                f"basket_estimated_net={net_after_estimated_cost:.2f}; submitting up to {min(len(basket), SMART_BULK_EXIT_MAX_WORKERS)} "
                f"close requests concurrently",
                flush=True,
            )
            cleanup = cancel_recovery_for_basket(basket)
            print(f"[NovaFury] Recovery orders cancelled before exit: {cleanup}", flush=True)
            results = close_positions_fast(basket)
            closed = sum(1 for result in results if result and result.get("ok"))
            failed = [result for result in results if not result or not result.get("ok")]
            print(
                f"[NovaFury] SMART BULK EXIT result: symbol={symbol} requested={len(tickets)} "
                f"accepted={closed} failed={len(failed)}",
                flush=True,
            )
            for result in failed:
                print(
                    f"[NovaFury] SMART BULK EXIT close failed: ticket={result.get('ticket', '?')} "
                    f"reason={result.get('comment', 'unknown')}",
                    flush=True,
                )
        finally:
            with bulk_exit_lock:
                bulk_exit_symbols_in_progress.discard(symbol)


def manage_smart_bulk_exit_once():
    # The execution lock prevents a just-opened first ticket being closed while
    # the remainder of its ten-ticket burst is still being submitted.
    with execution_lock:
        return _manage_smart_bulk_exit_once_locked()


def smart_bulk_exit_loop():
    while True:
        try:
            manage_smart_bulk_exit_once()
        except Exception as exc:
            now = time.monotonic()
            previous = smart_profit_last_error.get(-1, (0.0, ""))
            reason = repr(exc)
            if now - previous[0] >= 30 or reason != previous[1]:
                print(f"[NovaFury] Smart bulk-exit manager temporarily unavailable: {reason}", flush=True)
                smart_profit_last_error[-1] = (now, reason)
        time.sleep(SMART_BULK_EXIT_CHECK_SEC)


def close_all(requested: str):
    with execution_lock:
        ensure_mt5()
        symbol = resolve_symbol(requested)
        raw_positions = mt5.positions_get(symbol=symbol)
        if raw_positions is None:
            raise RuntimeError(f"MT5 position query failed before basket close: {mt5.last_error()}")
        positions = [p for p in raw_positions if int(getattr(p, "magic", 0)) == MAGIC]
        cleanup = _cancel_all_recovery_orders(symbol)
        if not cleanup.get("ok"):
            return {"ok": False, "count": 0, "results": [], "error": "Basket close aborted because recovery pending orders could not be confirmed cancelled.", "recoveryCleanup": cleanup}
        results = close_positions_fast(positions)
        return {"ok": all(r.get("ok") for r in results) if results else True, "count": len(results), "results": results, "recoveryCleanup": cleanup}


class Handler(BaseHTTPRequestHandler):
    server_version = "NovaFuryMT5/2.0"

    def _is_local_dashboard_request(self):
        """Only accept provider secret/config writes from loopback clients and origins."""
        try:
            peer = ipaddress.ip_address(self.client_address[0])
            if not peer.is_loopback:
                return False
            origin = self.headers.get("Origin", "").strip()
            if not origin:
                return True
            origin_host = (urlparse(origin).hostname or "").lower()
            return origin_host in {"localhost", "127.0.0.1", "::1"}
        except Exception:
            return False

    def _is_allowed_local_request(self):
        try:
            peer = ipaddress.ip_address(self.client_address[0])
            if not peer.is_loopback:
                return False
            origin = self.headers.get("Origin", "").strip()
            if origin:
                parsed = urlparse(origin)
                host = (parsed.hostname or "").lower()
                port = parsed.port
                return parsed.scheme in {"http", "https"} and host in {"localhost", "127.0.0.1", "::1"} and port in {None, 3000, 4173, 5173}
            host_header = self.headers.get("Host", "127.0.0.1").split(":", 1)[0].strip("[]").lower()
            return host_header in {"localhost", "127.0.0.1", "::1"}
        except Exception:
            return False

    def _send(self, status, data):
        raw = json.dumps(json_safe(data), separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        origin = self.headers.get("Origin", "").strip()
        if origin and self._is_allowed_local_request():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        if not self._is_allowed_local_request():
            self._send(403, {"ok": False, "error": "Only local dashboard origins are permitted."})
            return
        self._send(200, {"ok": True})

    def do_GET(self):
        if not self._is_allowed_local_request():
            self._send(403, {"ok": False, "error": "Bridge is restricted to local clients."})
            return
        try:
            url = urlparse(self.path)
            q = parse_qs(url.query)
            requested = q.get("symbol", [REQUESTED_SYMBOL])[0]
            if url.path == "/api/status":
                ensure_mt5()
                ai = account_payload()
                self._send(200, {
                    "ok": True,
                    "connected": True,
                    "terminalPath": terminal_path,
                    "symbol": resolve_symbol(requested),
                    "requestedSymbol": requested,
                    "timeframe": TIMEFRAME_NAME,
                    "server": ai["server"],
                    "login": ai["login"],
                    "tradeAllowed": ai["tradeAllowed"],
                    "risk": _risk_snapshot(ai),
                    "runtime": {"executionArmed": bool(execution_armed), "autoTrade": bool(execution_armed)},
                    "magic": MAGIC,
                    "smartProfit": {
                        "enabled": SMART_PROFIT_ENABLED,
                        "trailPoints": SMART_PROFIT_TRAIL_POINTS,
                        "stepPoints": SMART_PROFIT_STEP_POINTS,
                        "breakEvenOffsetPoints": SMART_PROFIT_BE_OFFSET_POINTS,
                        "checkSeconds": SMART_PROFIT_CHECK_SEC,
                    },
                    "smartBulkExit": {
                        "enabled": SMART_BULK_EXIT_ENABLED,
                        "checkSeconds": SMART_BULK_EXIT_CHECK_SEC,  # kept for existing dashboard compatibility
                        "checkMicroseconds": SMART_BULK_EXIT_CHECK_US,
                        "maxParallelCloses": SMART_BULK_EXIT_MAX_WORKERS,
                        "trigger": "combined floating profit plus swap less configured estimated round-trip costs exceeds the minimum net basket target",
                    },
                })
            elif url.path == "/api/snapshot":
                self._send(200, snapshot(requested))
            elif url.path == "/api/candles":
                self._send(200, {"ok": True, "candles": candles(requested, int(q.get("count", [60])[0]), include_forming=False)})
            elif url.path == "/api/xgb":
                direction = q.get("direction", ["neutral"])[0]
                self._send(200, {"ok": True, "xgb": xgb_confirmation(requested, direction)})
            elif url.path == "/api/ensemble":
                direction = q.get("direction", ["neutral"])[0]
                self._send(200, {"ok": True, "ensemble": ai_ensemble_confirmation(requested, direction)})
            elif url.path == "/api/ensemble/status":
                self._send(200, {"ok": True, "ensemble": ai_ensemble_status()})
            elif url.path == "/api/brain":
                direction = q.get("direction", ["neutral"])[0]
                self._send(200, combined_brain_for(requested, direction))
            elif url.path == "/api/ai/status":
                self._send(200, {"ok": True, "ai": ai_status()})
            elif url.path == "/api/providers":
                if not self._is_local_dashboard_request():
                    self._send(403, {"ok": False, "error": "AI provider settings are available only to the local dashboard."})
                    return
                self._send(200, {"ok": True, "providers": ai_provider_settings()})
            elif url.path == "/api/ai":
                direction = q.get("direction", ["neutral"])[0]
                self._send(200, {"ok": True, "ai": ai_decision_for(requested, direction)})
            elif url.path == "/api/risk":
                account = account_payload()
                risk = _risk_snapshot(account)
                try:
                    exposure = _current_bot_stop_risk()
                    risk["currentBrokerStopRisk"] = exposure["risk"]
                    risk["currentBrokerStopRiskPct"] = (exposure["risk"] / float(account["equity"])) if float(account["equity"]) > 0 else 0.0
                    risk["currentBrokerStopRiskCap"] = float(account["equity"]) * RISK_MAX_BASKET_PCT
                    risk["managedPositionCount"] = len(exposure["positions"])
                    risk["managedPendingOrderCount"] = len(exposure["pendingOrders"])
                    risk["exposureCheckError"] = ""
                except Exception as exposure_exc:
                    risk["currentBrokerStopRisk"] = None
                    risk["exposureCheckError"] = str(exposure_exc)
                    risk["halted"] = True
                self._send(200, {"ok": True, "risk": risk})
            elif url.path == "/api/health":
                self._send(200, {"ok": True, "service": "NovaFury MT5 bridge", "version": "2.0"})
            else:
                self._send(404, {"ok": False, "error": "Not found"})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        if not self._is_allowed_local_request():
            self._send(403, {"ok": False, "error": "Bridge is restricted to local clients."})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                self._send(415, {"ok": False, "error": "POST requests must use application/json."})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 65536:
                self._send(413, {"ok": False, "error": "Request body is too large."})
                return
            body = self.rfile.read(length) if length else b"{}"
            data = json.loads(body.decode("utf-8"))
            if self.path == "/api/arm":
                global execution_armed, armed_account
                ensure_mt5()
                account = account_payload()
                expected_login = data.get("expectedLogin")
                expected_server = str(data.get("expectedServer", ""))
                if expected_login is None or int(expected_login) != int(account["login"]) or expected_server != str(account["server"]):
                    self._send(409, {"ok": False, "error": "Account confirmation does not match the connected MT5 login/server."})
                    return
                try:
                    _assert_account_authorized(account)
                except Exception as auth_exc:
                    self._send(403, {"ok": False, "error": str(auth_exc)})
                    return
                quote = symbol_quote(REQUESTED_SYMBOL)
                _risk_gate(account, quote, allow_existing=True)
                protected_state = _assert_existing_positions_protected(quote["symbol"])
                with execution_state_lock:
                    execution_armed = True
                    armed_account = (int(account["login"]), str(account["server"]))
                self._send(200, {"ok": True, "runtime": {"executionArmed": True, "autoTrade": True}, "risk": _risk_snapshot(account), "protectedState": protected_state})
            elif self.path == "/api/disarm":
                cleanup = _disarm("Dashboard disarm")
                self._send(200, {"ok": bool(cleanup.get("ok")), "runtime": {"executionArmed": False, "autoTrade": False}, "recoveryCleanup": cleanup})
            elif self.path == "/api/risk/reset-day":
                ensure_mt5()
                account = account_payload()
                if data.get("expectedLogin") is None or int(data.get("expectedLogin")) != int(account["login"]):
                    self._send(409, {"ok": False, "error": "Confirmed login does not match connected MT5 account."})
                    return
                # A valid daily anchor cannot be manually reset: doing so would
                # let the operator bypass the daily-loss or peak-drawdown halt.
                # This endpoint is only an administrative recovery path for a
                # corrupt or account-mismatched persisted state. Normal day roll
                # is automatic at the next UTC day boundary.
                try:
                    _ensure_risk_state(account, reset=False)
                    self._send(409, {"ok": False, "error": "A valid Risk Guardian anchor cannot be manually reset. Daily limits roll automatically at the next UTC day boundary."})
                    return
                except Exception:
                    if not risk_state_error:
                        raise
                    if "file is missing" in risk_state_error.lower():
                        self._send(409, {"ok": False, "error": risk_state_error})
                        return
                cleanup = _disarm("Corrupt/mismatched Risk Guardian state recovery")
                if not cleanup.get("ok"):
                    self._send(409, {"ok": False, "error": "Risk state was not reinitialized because recovery-order cancellation could not be verified.", "recoveryCleanup": cleanup})
                    return
                positions = mt5.positions_get()
                if positions is None:
                    raise RuntimeError(f"MT5 position query failed during Risk Guardian recovery: {mt5.last_error()}")
                if any(int(getattr(position, "magic", 0)) == MAGIC for position in positions):
                    self._send(409, {"ok": False, "error": "Risk state cannot be reinitialized while any NovaFury positions remain open. Close/manage them first."})
                    return
                _ensure_risk_state(account, reset=True)
                self._send(200, {"ok": True, "risk": _risk_snapshot(account), "recoveryCleanup": cleanup})
            elif self.path == "/api/providers":
                if not self._is_local_dashboard_request():
                    self._send(403, {"ok": False, "error": "Provider settings can only be changed from the local dashboard."})
                    return
                settings = save_ai_provider_settings(data)
                self._send(200, {"ok": True, "providers": settings, "message": "AI provider settings saved locally. Restart is not required."})
            elif self.path == "/api/order":
                requested = data.get("symbol", REQUESTED_SYMBOL)
                direction = str(data.get("direction", "buy")).lower()
                require_brain = bool(data.get("requireBrainApproval", False))
                source = "auto" if require_brain else "manual"
                expected_login = data.get("expectedLogin")
                expected_server = data.get("expectedServer")
                if require_brain:
                    coordinated = combined_brain_for(requested, direction)
                    verdict = coordinated.get("brain", {})
                    decision_age_ms = time.time() * 1000.0 - float(verdict.get("timestamp", 0) or 0)
                    if (not coordinated.get("ok") or not verdict.get("ready") or not verdict.get("approved")
                            or decision_age_ms < 0 or decision_age_ms > 15000):
                        self._send(409, {"ok": False, "error": verdict.get("reason", "Unified AI brain did not approve this entry."),
                                         "decisionAgeMs": decision_age_ms, "brain": verdict})
                        return
                elif not bool(data.get("manualConfirm", False)):
                    self._send(403, {"ok": False, "error": "Manual orders require an explicit dashboard confirmation."})
                    return
                result = open_burst(requested, direction, float(data.get("volume", 0.01)), FIXED_BURST_ORDERS,
                                    source=source, expected_login=expected_login, expected_server=expected_server)
                self._send(200 if result.get("ok") else 207, result)
            elif self.path == "/api/close_all":
                self._send(200, close_all(data.get("symbol", REQUESTED_SYMBOL)))
            elif self.path == "/api/refresh":
                self._send(200, snapshot(data.get("symbol", REQUESTED_SYMBOL)))
            else:
                self._send(404, {"ok": False, "error": "Not found"})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})


def risk_watchdog_loop():
    """Continuously disarm and flatten NovaFury exposure if account-level limits breach."""
    while True:
        try:
            if mt5 is not None:
                account = account_payload()
                if execution_armed and armed_account != (int(account["login"]), str(account["server"])):
                    _disarm("Connected MT5 account changed")
                try:
                    exposure = _current_bot_stop_risk()
                    maximum_cash_risk = float(account["equity"]) * RISK_MAX_BASKET_PCT
                    if exposure["risk"] > maximum_cash_risk + 0.01:
                        print(
                            f"[NovaFury] RISK HALT: broker-stop exposure ${exposure['risk']:.2f} exceeds "
                            f"aggregate cap ${maximum_cash_risk:.2f}; disarming and closing all NovaFury exposure.",
                            flush=True,
                        )
                        _disarm("Aggregate broker-stop risk exceeded")
                        symbols = {item["symbol"] for item in exposure["positions"]}
                        for sym in symbols:
                            close_all(sym)
                except Exception as exposure_exc:
                    # Missing/invalid stops or an unverifiable stop-risk calculation
                    # must not be ignored. Disarm first, then attempt to close any
                    # identifiable NovaFury positions.
                    print(f"[NovaFury] CRITICAL RISK GUARD: stop exposure could not be verified: {exposure_exc!r}", flush=True)
                    _disarm("Broker stop protection/exposure verification failed")
                    raw = mt5.positions_get()
                    if raw is None:
                        raise RuntimeError(f"Unable to enumerate NovaFury positions after stop-risk failure: {mt5.last_error()}")
                    for sym in {str(p.symbol) for p in raw if int(getattr(p, "magic", 0)) == MAGIC}:
                        close_all(sym)
                status = _risk_snapshot(account)
                if status.get("halted"):
                    if execution_armed:
                        _disarm("Daily loss / peak drawdown limit reached")
                    raw_positions = mt5.positions_get()
                    if raw_positions is None:
                        raise RuntimeError(f"MT5 position query failed during risk halt: {mt5.last_error()}")
                    raw_orders = mt5.orders_get()
                    if raw_orders is None:
                        raise RuntimeError(f"MT5 pending-order query failed during risk halt: {mt5.last_error()}")
                    symbols = {str(p.symbol) for p in raw_positions if int(getattr(p, "magic", 0)) == MAGIC}
                    symbols.update(str(o.symbol) for o in raw_orders if int(getattr(o, "magic", 0)) == MAGIC)
                    if symbols:
                        print("[NovaFury] RISK HALT: daily-loss/peak-drawdown limit reached; cancelling pending orders and closing NovaFury baskets", flush=True)
                    for sym in symbols:
                        close_all(sym)
        except Exception as exc:
            print(f"[NovaFury] Risk watchdog unavailable; new orders remain guarded: {exc!r}", flush=True)
        time.sleep(1.0)


def main():
    if mt5 is None:
        print(f"[NovaFury] MetaTrader5 import failed: {MT5_IMPORT_ERROR}", flush=True)
    else:
        try:
            ensure_mt5()
            ai = account_payload()
            actual = resolve_symbol(REQUESTED_SYMBOL)
            print("[NovaFury] MT5 bridge ready", flush=True)
            print(f"[NovaFury] Terminal: {terminal_path}", flush=True)
            print(f"[NovaFury] Server: {ai['server']} | Login: {ai['login']}", flush=True)
            print(f"[NovaFury] Symbol: {actual} | Timeframe: {TIMEFRAME_NAME} | Magic: {MAGIC}", flush=True)
            print(f"[NovaFury] Trade allowed: {ai['tradeAllowed']}", flush=True)
            print("[NovaFury V2] Risk profile: BALANCED | Auto execution starts DISARMED", flush=True)
            print(f"[NovaFury V2] Basket risk={RISK_BASE_BASKET_PCT * 100:.2f}% | absolute cap={RISK_MAX_BASKET_PCT * 100:.2f}% | daily loss={RISK_DAILY_LOSS_PCT * 100:.2f}%", flush=True)
            print(
                f"[NovaFury] AI-confirmed pullback recovery: {'ON' if RECOVERY_ENABLED else 'OFF'} | "
                f"favourable excursion={RECOVERY_ARM_ATR_MULTIPLIER:g} ATR | "
                f"pullback={RECOVERY_PULLBACK_MIN_FRACTION:.0%}-{RECOVERY_PULLBACK_MAX_FRACTION:.0%} | "
                f"max fills/basket={RECOVERY_MAX_FILLS_PER_BASKET}",
                flush=True,
            )
            if xgb_confirm is not None:
                print("[NovaFury] XGBoost confirmation engine: READY (lazy-trains on live M5 history)", flush=True)
            else:
                print(f"[NovaFury] XGBoost confirmation engine: IMPORT ERROR - {XGB_ENGINE_IMPORT_ERROR}", flush=True)
            ensemble = ai_ensemble_status()
            print(
                f"[NovaFury] Unified brain coordinator: XGB weight={BRAIN_XGB_WEIGHT:g} | "
                f"ensemble weight={BRAIN_ENSEMBLE_WEIGHT:g} | threshold={BRAIN_CONSENSUS_THRESHOLD:g}",
                flush=True,
            )
            if ensemble.get("enabled"):
                print(
                    f"[NovaFury] AI ensemble: ON | models={','.join(ensemble.get('configuredModels', []))} | "
                    f"trainBars={ensemble.get('trainBars')} | threshold={ensemble.get('directionThreshold')}",
                    flush=True,
                )
            else:
                print("[NovaFury] AI ensemble: OFF", flush=True)
            ai = ai_status()
            if ai.get("enabled"):
                print(
                    f"[NovaFury] AI brain: {'CONFIGURED' if ai.get('configured') else 'NO API KEY'} | "
                    f"model={ai.get('model')} | refresh={ai.get('refreshSeconds')}s | failClosed={ai.get('failClosed')}",
                    flush=True,
                )
            else:
                print("[NovaFury] AI brain: OFF (existing strategy/XGBoost path remains unchanged)", flush=True)
        except Exception as exc:
            print(f"[NovaFury] MT5 connection not ready: {exc}", flush=True)
            print("[NovaFury] The bridge will keep running; fix/open MT5 and refresh.", flush=True)

    recovery_thread = threading.Thread(target=recovery_loop, name="nova-recovery", daemon=True)
    recovery_thread.start()
    smart_profit_thread = threading.Thread(target=smart_profit_loop, name="nova-smart-profit", daemon=True)
    smart_profit_thread.start()
    smart_bulk_exit_thread = threading.Thread(target=smart_bulk_exit_loop, name="nova-smart-bulk-exit", daemon=True)
    smart_bulk_exit_thread.start()
    risk_watchdog_thread = threading.Thread(target=risk_watchdog_loop, name="nova-risk-watchdog", daemon=True)
    risk_watchdog_thread.start()

    print(
        f"[NovaFury] Smart Bulk Exit: {'ON' if SMART_BULK_EXIT_ENABLED else 'OFF'} | "
        f"cost-aware net basket target={BASKET_MIN_NET_PROFIT:g} less estimated round-trip costs/lot={ESTIMATED_ROUND_TRIP_COST_PER_LOT:g} | "
        f"check={SMART_BULK_EXIT_CHECK_US} us ({SMART_BULK_EXIT_CHECK_SEC:g}s) | "
        f"max parallel closes={SMART_BULK_EXIT_MAX_WORKERS}",
        flush=True,
    )

    print(
        f"[NovaFury] Per-position Smart Profit trailing: {'ON' if SMART_PROFIT_ENABLED else 'OFF'} | "
        f"basket-exit-only mode leaves per-position trailing SL management disabled",
        flush=True,
    )

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[NovaFury] Bridge API: http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
