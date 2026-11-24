"""NovaFury optional final supervisor for the unified multi-brain decision chain.

This module is intentionally NOT used in the tick-level order execution path.
It receives the shared market snapshot, XGBoost, LightGBM/CatBoost/optional
GRU-or-TCN ensemble scores, meta-label score, and weighted cross-brain consensus.
It returns a validated regime/quality veto-or-allow policy; it cannot execute orders.

OpenAI and Anthropic API keys are read server-side from the process environment
or .env.local. The dashboard receives provider metadata only, never stored secrets.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


def _load_simple_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for filename in (".env.local", ".env"):
        path = PROJECT_DIR / filename
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                out.setdefault(key, value)
        except Exception:
            pass
    return out


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    # Read the local env file on demand so dashboard changes apply without
    # restarting the MT5 bridge. Secrets are never returned to the browser.
    return _load_simple_env().get(name, default)


def _bool(name: str, default: bool = False) -> bool:
    return _env(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


_state_lock = threading.RLock()
_state: dict[str, Any] = {
    "decision": None,
    "created_at": 0.0,
    "cache_key": None,
    "last_error": "",
    "request_count": 0,
}


def _runtime_settings() -> dict[str, Any]:
    return {
        "refreshSeconds": max(5.0, float(_env("AI_REFRESH_SEC", "30"))),
        "minConfidence": min(0.99, max(0.50, float(_env("AI_MIN_CONFIDENCE", "0.72")))),
        "maxSpreadPoints": max(1.0, float(_env("AI_MAX_SPREAD_POINTS", "80"))),
        "failClosed": _bool("AI_FAIL_CLOSED", True),
        "httpTimeout": max(2.0, float(_env("AI_HTTP_TIMEOUT_SEC", "12"))),
    }


def provider_settings() -> dict[str, dict[str, Any]]:
    """Return safe provider configuration metadata; never return API secrets."""
    legacy_openai_enabled = _bool("AI_BRAIN_ENABLED", False)
    return {
        "openai": {
            "enabled": _bool("OPENAI_ENABLED", legacy_openai_enabled),
            "configured": bool(_env("OPENAI_API_KEY", "").strip()),
            "model": _env("OPENAI_MODEL", _env("AI_MODEL", "gpt-5.4-mini")).strip() or "gpt-5.4-mini",
        },
        "anthropic": {
            "enabled": _bool("ANTHROPIC_ENABLED", False),
            "configured": bool(_env("ANTHROPIC_API_KEY", "").strip()),
            "model": _env("ANTHROPIC_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5",
        },
    }


def save_provider_settings(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Persist dashboard provider settings in .env.local (server-side only)."""
    if not isinstance(payload, dict):
        raise ValueError("Provider configuration must be an object")

    current = provider_settings()
    values: dict[str, str] = {}
    for name, enabled_key, model_key, secret_key, default_model in (
        ("openai", "openaiEnabled", "openaiModel", "openaiApiKey", "gpt-5.4-mini"),
        ("anthropic", "anthropicEnabled", "anthropicModel", "anthropicApiKey", "claude-sonnet-5"),
    ):
        enabled = payload.get(enabled_key, current[name]["enabled"])
        if not isinstance(enabled, bool):
            raise ValueError(f"{enabled_key} must be true or false")
        model = str(payload.get(model_key, current[name]["model"] or default_model)).strip()
        if not model or len(model) > 120 or any(ch in model for ch in "\r\n"):
            raise ValueError(f"Invalid model identifier for {name}")
        secret = payload.get(secret_key, "")
        if secret is None:
            secret = ""
        if not isinstance(secret, str) or len(secret) > 500 or any(ch in secret for ch in "\r\n"):
            raise ValueError(f"Invalid API key value for {name}")
        existing_secret = _env("OPENAI_API_KEY" if name == "openai" else "ANTHROPIC_API_KEY", "").strip()
        if enabled and not (secret.strip() or existing_secret):
            raise ValueError(f"Add the {name.title()} API key before enabling this provider")
        values[enabled_key.replace("Enabled", "_ENABLED").upper()] = "1" if enabled else "0"
        values[model_key.replace("Model", "_MODEL").upper()] = model
        if secret.strip():
            values["OPENAI_API_KEY" if name == "openai" else "ANTHROPIC_API_KEY"] = secret.strip()

    # Keep the legacy OpenAI switch synchronized for older status consumers.
    values["AI_BRAIN_ENABLED"] = values["OPENAI_ENABLED"]
    values["AI_MODEL"] = values["OPENAI_MODEL"]

    env_path = PROJECT_DIR / ".env.local"
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    output: list[str] = []
    seen: set[str] = set()
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                if key not in seen:
                    output.append(f"{key}={values[key]}")
                    seen.add(key)
                continue
        output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")

    temp_path = env_path.with_suffix(".local.tmp")
    temp_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass
    temp_path.replace(env_path)
    # The bridge loads .env.local into os.environ at startup; refresh these
    # process values too so dashboard updates take effect immediately.
    os.environ.update(values)
    with _state_lock:
        _state.update({"decision": None, "created_at": 0.0, "cache_key": None, "last_error": "", "request_count": 0})
    return provider_settings()

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else fallback
    except Exception:
        return fallback


def _market_features(candles: list[dict[str, Any]]) -> dict[str, float]:
    closes = [_safe_float(c.get("close")) for c in candles if _safe_float(c.get("close")) > 0]
    highs = [_safe_float(c.get("high")) for c in candles]
    lows = [_safe_float(c.get("low")) for c in candles]
    volumes = [_safe_float(c.get("volume")) for c in candles]

    if not closes:
        return {}

    last = closes[-1]
    def pct_ago(n: int) -> float:
        if len(closes) <= n:
            return 0.0
        base = closes[-1 - n]
        return ((last / base) - 1.0) * 100.0 if base else 0.0

    returns = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev:
            returns.append((closes[i] / prev) - 1.0)

    recent_returns = returns[-30:] if returns else []
    volatility = statistics.pstdev(recent_returns) * 10000.0 if len(recent_returns) > 1 else 0.0

    ranges = []
    for h, l, c in zip(highs[-30:], lows[-30:], closes[-30:]):
        if h and l and c:
            ranges.append(abs(h - l) / c * 10000.0)
    avg_range = statistics.mean(ranges) if ranges else 0.0

    fast_n = min(9, len(closes))
    slow_n = min(21, len(closes))
    ema_fast = sum(closes[-fast_n:]) / fast_n
    ema_slow = sum(closes[-slow_n:]) / slow_n

    vol_last = volumes[-1] if volumes else 0.0
    vol_base = statistics.mean(volumes[-20:-1]) if len(volumes) > 20 else (statistics.mean(volumes[:-1]) if len(volumes) > 1 else vol_last)
    volume_ratio = (vol_last / vol_base) if vol_base > 0 else 1.0

    return {
        "last": round(last, 5),
        "return_3_bars_pct": round(pct_ago(3), 5),
        "return_6_bars_pct": round(pct_ago(6), 5),
        "return_12_bars_pct": round(pct_ago(12), 5),
        "return_24_bars_pct": round(pct_ago(24), 5),
        "volatility_bps_like": round(volatility, 3),
        "avg_range_bps_like": round(avg_range, 3),
        "ema_fast_minus_slow_pct": round(((ema_fast - ema_slow) / last) * 100 if last else 0.0, 5),
        "volume_ratio": round(volume_ratio, 3),
        "bars": len(closes),
    }


def _default_decision(reason: str, enabled: bool = False) -> dict[str, Any]:
    runtime = _runtime_settings()
    return {
        "enabled": enabled,
        "available": False,
        "ready": False,
        "model": _env("AI_MODEL", "gpt-5.4-mini"),
        "regime": "unknown",
        "allowed": not runtime["failClosed"] if not enabled else False,
        "preferredDirection": "neutral",
        "confidence": 0.0,
        "entryQuality": 0.0,
        "riskMode": "blocked" if enabled else "normal",
        "rationale": reason,
        "createdAt": int(time.time() * 1000),
        "ageSeconds": None,
        "error": reason if enabled else "",
    }


def _validate_model_output(obj: Any, model: str = "", provider: str = "ai") -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError(f"{provider} returned non-object JSON")

    regimes = {"trend", "range", "breakout", "high_volatility", "low_volatility", "unstable"}
    directions = {"buy", "sell", "neutral"}
    risk_modes = {"normal", "reduced", "blocked"}

    regime = str(obj.get("regime", "unstable")).strip().lower()
    direction = str(obj.get("preferredDirection", "neutral")).strip().lower()
    risk_mode = str(obj.get("riskMode", "blocked")).strip().lower()

    if regime not in regimes:
        regime = "unstable"
    if direction not in directions:
        direction = "neutral"
    if risk_mode not in risk_modes:
        risk_mode = "blocked"

    confidence = _clamp(_safe_float(obj.get("confidence"), 0.0), 0.0, 1.0)
    quality = _clamp(_safe_float(obj.get("entryQuality"), 0.0), 0.0, 1.0)
    requested_allowed = bool(obj.get("tradeAllowed", False))

    rationale = str(obj.get("rationale", "")).strip()
    if len(rationale) > 500:
        rationale = rationale[:500]

    return {
        "enabled": True,
        "available": True,
        "ready": True,
        "model": model,
        "provider": provider,
        "regime": regime,
        "allowed": bool(requested_allowed and risk_mode != "blocked"),
        "preferredDirection": direction,
        "confidence": round(confidence, 4),
        "entryQuality": round(quality, 4),
        "riskMode": risk_mode,
        "rationale": rationale,
        "createdAt": int(time.time() * 1000),
        "ageSeconds": 0.0,
        "error": "",
    }


def _call_openai(context: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    api_key = _env("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    selected_model = model or _env("OPENAI_MODEL", _env("AI_MODEL", "gpt-5.4-mini"))
    timeout = _runtime_settings()["httpTimeout"]

    # The schema constrains the model to the exact decision contract already
    # consumed by NovaFury. It does not add or change trading instructions.
    schema = {
        "type": "object",
        "properties": {
            "regime": {
                "type": "string",
                "enum": ["trend", "range", "breakout", "high_volatility", "low_volatility", "unstable"],
            },
            "tradeAllowed": {"type": "boolean"},
            "preferredDirection": {
                "type": "string",
                "enum": ["buy", "sell", "neutral"],
            },
            "confidence": {"type": "number"},
            "entryQuality": {"type": "number"},
            "riskMode": {
                "type": "string",
                "enum": ["normal", "reduced", "blocked"],
            },
            "rationale": {"type": "string"},
        },
        "required": [
            "regime",
            "tradeAllowed",
            "preferredDirection",
            "confidence",
            "entryQuality",
            "riskMode",
            "rationale",
        ],
        "additionalProperties": False,
    }

    system_instruction = (
        "You are the supervisory regime classifier for a retail MT5 XAUUSD M5 scalper. "
        "You are connected to the deterministic rule signal, XGBoost, the LightGBM/CatBoost "
        "directional ensemble, its meta-label quality model, and optionally a GRU or TCN sequence model. "
        "Treat their outputs as evidence: identify disagreement, do not average incompatible labels, "
        "and block when required model evidence is missing or conflicting. "
        "Do not invent prices or news. Use only the structured market snapshot provided. "
        "You are NOT the execution engine. Preserve capital by blocking trades whenever the "
        "regime is unclear, spread is excessive, or the requested direction is weak. "
        "Never recommend increasing lot size, leverage, recovery size, or changing hard risk limits. "
        "Return only JSON matching the supplied schema."
    )

    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {
                "role": "user",
                "content": "Evaluate this XAUUSD M5 snapshot for a secondary trade-quality gate:\n"
                           + json.dumps(context, separators=(",", ":"), ensure_ascii=True),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "novafury_ai_decision",
                "strict": True,
                "schema": schema,
            },
        },
        "max_completion_tokens": 300,
        "reasoning_effort": "none",
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:500]}") from exc

    data = json.loads(raw)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI returned no completion choices")
    message = choices[0].get("message") or {}
    if message.get("refusal"):
        raise RuntimeError(f"OpenAI refused the AI supervisor request: {message['refusal'][:300]}")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI returned no JSON content")
    return _validate_model_output(json.loads(content), selected_model, "openai")


def _call_anthropic(context: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    """Call Anthropic Messages API using the same strict post-validated decision schema."""
    api_key = _env("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    selected_model = model or _env("ANTHROPIC_MODEL", "claude-sonnet-5")
    timeout = _runtime_settings()["httpTimeout"]
    system_instruction = (
        "You are the supervisory regime classifier for NovaFury, an MT5 XAUUSD M5 system. "
        "Review the supplied rule signal, XGBoost, LightGBM/CatBoost/optional sequence ensemble, "
        "meta-label quality score, weighted consensus, spread and account constraints. "
        "You are not the execution engine. Never increase lot size, leverage, recovery size or risk limits. "
        "Only approve when the requested direction is supported, conditions are clear and risk is acceptable. "
        "Return a single JSON object with exactly these fields: regime, tradeAllowed, preferredDirection, "
        "confidence, entryQuality, riskMode, rationale. Use regime trend/range/breakout/high_volatility/"
        "low_volatility/unstable; preferredDirection buy/sell/neutral; riskMode normal/reduced/blocked. "
        "confidence and entryQuality must be numbers from 0 to 1. tradeAllowed must be boolean."
    )
    payload = {
        "model": selected_model,
        "max_tokens": 500,
        "system": system_instruction,
        "messages": [{
            "role": "user",
            "content": "Evaluate this market snapshot. Return JSON only:\n"
                       + json.dumps(context, separators=(",", ":"), ensure_ascii=True),
        }],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"Anthropic API HTTP {exc.code}: {detail[:500]}") from exc
    data = json.loads(raw)
    blocks = data.get("content") or []
    content = "\n".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text").strip()
    if not content:
        raise RuntimeError("Anthropic returned no text content")
    # Accept a fenced JSON object if the model wraps the response despite the prompt.
    if content.startswith("```"):
        content = content.strip("`").replace("json\n", "", 1).strip()
    try:
        output = json.loads(content)
    except json.JSONDecodeError as exc:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("Anthropic response did not contain valid JSON") from exc
        output = json.loads(content[start:end + 1])
    return _validate_model_output(output, selected_model, "anthropic")


def decide(
    symbol: str,
    direction: str,
    candles: list[dict[str, Any]],
    quote: dict[str, Any],
    account: dict[str, Any],
    xgb: dict[str, Any],
    positions: list[dict[str, Any]],
    history: list[dict[str, Any]],
    ensemble: dict[str, Any] | None = None,
    consensus: dict[str, Any] | None = None,
    recovery_context: bool = False,
) -> dict[str, Any]:
    now = time.time()
    providers = provider_settings()
    enabled_providers = [name for name, cfg in providers.items() if cfg.get("enabled")]
    runtime = _runtime_settings()
    if not enabled_providers:
        result = _default_decision("OpenAI and Anthropic supervisors are disabled; local model pipeline remains active.", enabled=False)
        result["allowed"] = True  # disabled optional supervisors do not veto the original pipeline
        result["providers"] = {name: {
            "enabled": bool(cfg.get("enabled")), "configured": bool(cfg.get("configured")),
            "model": cfg.get("model", ""), "ready": False,
            "error": "Disabled" if not cfg.get("enabled") else "",
        } for name, cfg in providers.items()}
        return result

    ensemble = ensemble or {}
    consensus = consensus or {}
    # Cache only for the exact same instrument, side, and upstream model versions.
    # This prevents a stale BUY supervisor response from being reused for SELL.
    cache_key = "|".join((
        str(symbol), str(direction), str(xgb.get("version", 0)),
        str(round(_safe_float(xgb.get("directionProbability"), 0.5), 4)),
        str(ensemble.get("version", 0)),
        str(round(_safe_float(ensemble.get("directionProbability"), 0.5), 4)),
        str(round(_safe_float(ensemble.get("metaProbability"), 0.0), 4)),
        str(round(_safe_float(consensus.get("probability"), 0.5), 4)),
        str(bool(consensus.get("passed", True))),
        str(round(_safe_float(quote.get("spread")), 1)),
        str(bool(account.get("tradeAllowed", False))),
        str(round(_safe_float(account.get("marginLevel")), 1)),
        str(round(_safe_float(account.get("equity")), 2)),
        str(round(_safe_float(account.get("freeMargin")), 2)),
        str(len(positions)),
        str(bool(recovery_context)),
        str(len(history)),
        str([(name, cfg.get("enabled"), cfg.get("model"), cfg.get("configured")) for name, cfg in providers.items()]),
    ))
    with _state_lock:
        cached = _state.get("decision")
        created = float(_state.get("created_at", 0.0))
        if cached and _state.get("cache_key") == cache_key and (now - created) < runtime["refreshSeconds"]:
            out = dict(cached)
            out["ageSeconds"] = round(max(0.0, now - created), 1)
            return out

    spread = _safe_float(quote.get("spread"))
    margin_level = _safe_float(account.get("marginLevel"))
    trade_allowed = bool(account.get("tradeAllowed", False))
    xgb_prob = _safe_float(xgb.get("directionProbability"), 0.5)
    xgb_direction = str(xgb.get("direction", "neutral"))

    # Hard safety gates are deterministic and never overridable by an external AI provider.
    if not trade_allowed:
        result = _default_decision("MT5 account reports trading disabled.", enabled=True)
        return _publish(result, cache_key)
    if positions and not recovery_context:
        result = _default_decision("Existing NovaFury basket is open; ordinary entry gate will not add exposure.", enabled=True)
        result["riskMode"] = "blocked"
        return _publish(result, cache_key)
    if recovery_context:
        same_direction = bool(positions) and all(
            str(item.get("direction", "")).lower() == direction for item in positions
        )
        if not same_direction:
            result = _default_decision("Recovery review requires an existing same-direction NovaFury basket.", enabled=True)
            result["riskMode"] = "blocked"
            return _publish(result, cache_key)
    if spread > runtime["maxSpreadPoints"]:
        result = _default_decision(f"Spread {spread:g} points exceeds AI max {runtime['maxSpreadPoints']:g}.", enabled=True)
        return _publish(result, cache_key)
    if margin_level > 0 and margin_level < 150:
        result = _default_decision(f"Margin level {margin_level:g}% is below safety floor.", enabled=True)
        return _publish(result, cache_key)
    xgb_required = bool(xgb.get("required", True))
    xgb_enabled = bool(xgb.get("enabled", True))
    if direction not in {"buy", "sell"}:
        result = _default_decision("The base strategy did not produce a tradable direction.", enabled=True)
        return _publish(result, cache_key)
    if xgb_required and not xgb_enabled:
        result = _default_decision("Required XGBoost engine is disabled or unavailable.", enabled=True)
        return _publish(result, cache_key)
    if xgb_required and xgb_direction != direction:
        result = _default_decision("Direction is not aligned with the required XGBoost/rule strategy.", enabled=True)
        return _publish(result, cache_key)
    if xgb_required and (not bool(xgb.get("available", False)) or not bool(xgb.get("ready", False)) or not bool(xgb.get("confirmed", False)) or xgb_prob < _safe_float(xgb.get("threshold"), 0.58)):
        result = _default_decision("XGBoost confirmation is not strong enough for an AI-supervised entry.", enabled=True)
        return _publish(result, cache_key)

    ensemble_enabled = bool(ensemble.get("enabled", False))
    ensemble_required = bool(ensemble.get("required", True))
    ensemble_direction = str(ensemble.get("direction", "neutral")).lower()
    ensemble_probability = _safe_float(ensemble.get("directionProbability"), 0.5)
    meta_probability = _safe_float(ensemble.get("metaProbability"), 0.0)
    if ensemble_required and (
        not ensemble_enabled
        or not bool(ensemble.get("available", False))
        or not bool(ensemble.get("ready", False))
        or not bool(ensemble.get("confirmed", False))
        or ensemble_direction != direction
    ):
        result = _default_decision(
            "Multi-model ensemble did not approve the same direction; OpenAI supervisor blocked before API review.",
            enabled=True,
        )
        return _publish(result, cache_key)
    if consensus and not bool(consensus.get("passed", False)):
        result = _default_decision(
            "Weighted cross-brain consensus is below threshold; OpenAI supervisor blocked before API review.",
            enabled=True,
        )
        return _publish(result, cache_key)

    recent_history = history[:30]
    wins = sum(1 for h in recent_history if _safe_float(h.get("profit")) > 0)
    losses = sum(1 for h in recent_history if _safe_float(h.get("profit")) < 0)
    total_profit = sum(_safe_float(h.get("profit")) for h in recent_history)

    context = {
        "symbol": symbol,
        "timeframe": "M5",
        "requestedDirection": direction,
        "tradeMode": "confirmed_pullback_recovery" if recovery_context else "new_basket_entry",
        "existingSameDirectionPositions": len(positions) if recovery_context else 0,
        "quote": {
            "bid": _safe_float(quote.get("bid")),
            "ask": _safe_float(quote.get("ask")),
            "spreadPoints": spread,
        },
        "account": {
            "equity": round(_safe_float(account.get("equity")), 2),
            "freeMargin": round(_safe_float(account.get("freeMargin")), 2),
            "marginLevel": round(margin_level, 2),
            "tradeAllowed": trade_allowed,
        },
        "xgboost": {
            "required": xgb_required,
            "direction": xgb_direction,
            "directionProbability": round(xgb_prob, 4),
            "threshold": round(_safe_float(xgb.get("threshold"), 0.58), 4),
            "trainedRows": int(_safe_float(xgb.get("trainedRows"), 0)),
            "ageSeconds": _safe_float(xgb.get("ageSeconds"), 0.0),
            "confirmed": bool(xgb.get("confirmed", False)),
        },
        "crossBrainConsensus": {
            "probability": round(_safe_float(consensus.get("probability"), 0.5), 4),
            "threshold": round(_safe_float(consensus.get("threshold"), 0.60), 4),
            "passed": bool(consensus.get("passed", True)),
            "weights": consensus.get("weights", {}),
        },
        "ensemble": {
            "enabled": ensemble_enabled,
            "required": ensemble_required,
            "direction": ensemble_direction,
            "directionProbability": round(ensemble_probability, 4),
            "directionThreshold": round(_safe_float(ensemble.get("threshold"), 0.60), 4),
            "metaSuccessProbability": round(meta_probability, 4),
            "metaThreshold": round(_safe_float(ensemble.get("metaThreshold"), 0.58), 4),
            "confirmed": bool(ensemble.get("confirmed", False)),
            "trainedBars": int(_safe_float(ensemble.get("trainedBars"), 0)),
            "selectedModels": list(ensemble.get("selectedModels", [])),
            "models": {
                name: {
                    "available": bool(payload.get("available", False)),
                    "probabilityUp": payload.get("probabilityUp"),
                    "successProbability": payload.get("successProbability"),
                    "validationAuc": payload.get("validationAuc"),
                    "ensembleWeight": payload.get("ensembleWeight"),
                }
                for name, payload in (ensemble.get("models", {}) or {}).items()
            },
        },
        "market": _market_features(candles),
        "recentJournal": {
            "trades": len(recent_history),
            "wins": wins,
            "losses": losses,
            "winRate": round((wins / len(recent_history)) if recent_history else 0.0, 4),
            "netProfit": round(total_profit, 2),
        },
    }

    provider_results: dict[str, dict[str, Any]] = {}

    def call_provider(provider_name: str) -> dict[str, Any]:
        cfg = providers[provider_name]
        if provider_name == "openai":
            return _call_openai(context, cfg["model"])
        return _call_anthropic(context, cfg["model"])

    # Call both external services in parallel so enabling both does not add the
    # two provider latencies together. Both still receive the same frozen context.
    with ThreadPoolExecutor(max_workers=min(2, len(enabled_providers))) as pool:
        futures = {name: pool.submit(call_provider, name) for name in enabled_providers}
        for provider_name in enabled_providers:
            cfg = providers[provider_name]
            try:
                provider_result = futures[provider_name].result()
                # Each enabled supervisor is an independent veto gate. A provider
                # cannot override another provider's rejection or hard risk checks.
                if provider_result["preferredDirection"] != direction:
                    provider_result["allowed"] = False
                    provider_result["riskMode"] = "blocked"
                    provider_result["rationale"] = "AI direction does not match the upstream model consensus direction."
                if provider_result["confidence"] < runtime["minConfidence"]:
                    provider_result["allowed"] = False
                    provider_result["riskMode"] = "blocked"
                    provider_result["rationale"] = f"AI confidence {provider_result['confidence']:.2f} is below minimum {runtime['minConfidence']:.2f}."
                if provider_result["entryQuality"] < runtime["minConfidence"]:
                    provider_result["allowed"] = False
                    provider_result["riskMode"] = "blocked"
                    provider_result["rationale"] = "AI entry quality is below the configured minimum."
                provider_results[provider_name] = provider_result
            except Exception as exc:
                provider_results[provider_name] = {
                    "enabled": True, "available": False, "ready": False, "model": cfg["model"],
                    "provider": provider_name, "regime": "unknown", "allowed": False,
                    "preferredDirection": "neutral", "confidence": 0.0, "entryQuality": 0.0,
                    "riskMode": "blocked", "rationale": f"{provider_name.title()} request failed.",
                    "error": str(exc)[:500], "createdAt": int(time.time() * 1000), "ageSeconds": 0.0,
                }

    # When both services are enabled, both must be ready and approve the same
    # side. With one enabled, that provider alone is the supervisor gate.
    all_ready = all(item.get("ready") and item.get("available") for item in provider_results.values())
    all_allowed = bool(provider_results) and all(item.get("allowed") for item in provider_results.values())
    preferred = direction if all_allowed else next((item.get("preferredDirection", "neutral") for item in provider_results.values()), "neutral")
    confidence = min((float(item.get("confidence", 0.0)) for item in provider_results.values()), default=0.0)
    quality = min((float(item.get("entryQuality", 0.0)) for item in provider_results.values()), default=0.0)
    blocked = not all_ready or not all_allowed
    names = ", ".join(name.title() for name in enabled_providers)
    failed = [f"{name.title()}: {item.get('rationale') or item.get('error') or 'blocked'}" for name, item in provider_results.items() if not item.get("allowed")]
    combined = {
        "enabled": True, "available": all_ready, "ready": all_ready,
        "model": " + ".join(str(item.get("model", name)) for name, item in provider_results.items()),
        "provider": "multi" if len(provider_results) > 1 else enabled_providers[0],
        "providers": provider_results, "providerCount": len(provider_results),
        "regime": next((item.get("regime", "unknown") for item in provider_results.values() if item.get("regime") != "unknown"), "unknown"),
        "allowed": bool(all_ready and all_allowed),
        "preferredDirection": preferred, "confidence": round(confidence, 4),
        "entryQuality": round(quality, 4),
        "riskMode": "blocked" if blocked else ("reduced" if any(item.get("riskMode") == "reduced" for item in provider_results.values()) else "normal"),
        "rationale": (f"All enabled AI supervisors ({names}) approved the upstream direction." if not blocked else "AI supervisor gate blocked: " + "; ".join(failed or ["one or more providers are not ready"])),
        "createdAt": int(time.time() * 1000), "ageSeconds": 0.0,
        "error": "; ".join(str(item.get("error")) for item in provider_results.values() if item.get("error")),
    }
    return _publish(combined, cache_key)


def _publish(result: dict[str, Any], cache_key: str | None = None) -> dict[str, Any]:
    result.setdefault("providers", {name: {
        "enabled": bool(cfg.get("enabled")), "configured": bool(cfg.get("configured")),
        "model": cfg.get("model", ""), "ready": False,
        "error": "" if not cfg.get("enabled") else "Waiting for provider review",
    } for name, cfg in provider_settings().items()})
    with _state_lock:
        _state["decision"] = dict(result)
        _state["created_at"] = time.time()
        _state["cache_key"] = cache_key
        _state["last_error"] = result.get("error", "")
        _state["request_count"] = int(_state.get("request_count", 0)) + 1
        result["ageSeconds"] = 0.0
        return dict(result)


def status() -> dict[str, Any]:
    now = time.time()
    runtime = _runtime_settings()
    providers = provider_settings()
    active = [name for name, cfg in providers.items() if cfg.get("enabled")]
    with _state_lock:
        created = float(_state.get("created_at", 0.0))
        decision = _state.get("decision")
        return {
            "enabled": bool(active),
            "configured": all(bool(providers[name].get("configured")) for name in active) if active else False,
            "model": " + ".join(providers[name]["model"] for name in active),
            "providers": providers,
            "refreshSeconds": runtime["refreshSeconds"],
            "minConfidence": runtime["minConfidence"],
            "failClosed": runtime["failClosed"],
            "ready": bool(decision),
            "ageSeconds": round(max(0.0, now - created), 1) if created else None,
            "lastError": str(_state.get("last_error", "")),
            "requestCount": int(_state.get("request_count", 0)),
            "decision": decision,
        }
