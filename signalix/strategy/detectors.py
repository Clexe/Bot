from datetime import time, datetime
from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def detect_htf_rejection(candles: list, oc_levels: list) -> dict:
    """Check body rejection from fresh OC level with displacement away from level."""
    if len(candles) < 2:
        return {"rejected": False}

    last = candles[-1]
    prev = candles[-2]
    displacement = abs(last["close"] - last["open"]) > abs(prev["close"] - prev["open"])

    for lvl in oc_levels:
        if lvl.get("freshness_status") != "fresh":
            continue
        lp = float(lvl["level_price"])

        if lvl["level_type"] == "resistance" and last["close"] < lp and last["open"] < lp and displacement:
            return {"rejected": True, "level": lp, "type": "resistance", "direction": "SHORT"}
        if lvl["level_type"] == "support" and last["close"] > lp and last["open"] > lp and displacement:
            return {"rejected": True, "level": lp, "type": "support", "direction": "LONG"}

    return {"rejected": False}


async def detect_displacement_and_fvg(candles: list) -> dict:
    """Detect FVG and return zone with CE midpoint."""
    if len(candles) < 3:
        return {"found": False}

    for i in range(len(candles) - 2, max(0, len(candles) - 6), -1):
        c0 = candles[i - 1]
        c1 = candles[i]
        c2 = candles[i + 1] if i + 1 < len(candles) else None
        if c2 is None:
            continue

        body_size = abs(c1["close"] - c1["open"])
        avg_body = sum(abs(c["close"] - c["open"]) for c in candles[max(0, i - 10):i]) / max(1, min(10, i))
        has_displacement = body_size > avg_body * 1.5

        if c2["low"] > c0["high"]:
            lo, hi = c0["high"], c2["low"]
            return {
                "found": True, "type": "bullish", "low": lo, "high": hi,
                "ce": (lo + hi) / 2, "displacement": has_displacement,
            }
        if c2["high"] < c0["low"]:
            lo, hi = c2["high"], c0["low"]
            return {
                "found": True, "type": "bearish", "low": lo, "high": hi,
                "ce": (lo + hi) / 2, "displacement": has_displacement,
            }

    return {"found": False}


async def detect_liquidity_sweep(candles: list, session_levels: dict) -> dict:
    """Judas Swing detector: wick penetration of Asian level, body closes back inside,
    FVG created within 3 candles."""
    if len(candles) < 4 or not session_levels:
        return {"detected": False}

    for i in range(max(0, len(candles) - 5), len(candles) - 1):
        c = candles[i]

        if c["high"] > session_levels.get("high", float("inf")) and c["close"] < session_levels["high"]:
            reversal = False
            for j in range(i + 1, min(i + 4, len(candles))):
                if candles[j]["close"] < candles[j]["open"]:
                    reversal = True
                    break
            if reversal:
                fvg = await detect_displacement_and_fvg(candles[i:min(i + 4, len(candles))])
                return {
                    "detected": True, "direction": "SHORT",
                    "sweep_level": session_levels["high"], "sweep_wick": c["high"],
                    "fvg_created": fvg.get("found", False),
                }

        if c["low"] < session_levels.get("low", 0) and c["close"] > session_levels["low"]:
            reversal = False
            for j in range(i + 1, min(i + 4, len(candles))):
                if candles[j]["close"] > candles[j]["open"]:
                    reversal = True
                    break
            if reversal:
                fvg = await detect_displacement_and_fvg(candles[i:min(i + 4, len(candles))])
                return {
                    "detected": True, "direction": "LONG",
                    "sweep_level": session_levels["low"], "sweep_wick": c["low"],
                    "fvg_created": fvg.get("found", False),
                }

    return {"detected": False}


async def detect_structure_shift(candles: list) -> dict:
    """Detect CHoCH or BOS with displacement evidence and FVG on same move."""
    if len(candles) < 5:
        return {"confirmed": False, "type": None}

    swing_highs = []
    swing_lows = []
    for i in range(2, len(candles) - 2):
        if candles[i]["high"] > candles[i - 1]["high"] and candles[i]["high"] > candles[i + 1]["high"]:
            swing_highs.append((i, candles[i]["high"]))
        if candles[i]["low"] < candles[i - 1]["low"] and candles[i]["low"] < candles[i + 1]["low"]:
            swing_lows.append((i, candles[i]["low"]))

    if not swing_highs or not swing_lows:
        return {"confirmed": False, "type": None}

    last = candles[-1]
    prev_high = swing_highs[-1][1]
    prev_low = swing_lows[-1][1]

    body_size = abs(last["close"] - last["open"])
    avg_body = sum(abs(c["close"] - c["open"]) for c in candles[-10:-1]) / max(1, len(candles[-10:-1]))
    has_displacement = body_size > avg_body * 1.5

    if last["close"] > prev_high and has_displacement:
        fvg = await detect_displacement_and_fvg(candles[-5:])
        shift_type = "BOS" if len(swing_highs) >= 2 and swing_highs[-1][1] > swing_highs[-2][1] else "CHoCH"
        return {
            "confirmed": True, "type": shift_type, "direction": "LONG",
            "break_level": prev_high, "fvg": fvg if fvg.get("found") else None,
        }

    if last["close"] < prev_low and has_displacement:
        fvg = await detect_displacement_and_fvg(candles[-5:])
        shift_type = "BOS" if len(swing_lows) >= 2 and swing_lows[-1][1] < swing_lows[-2][1] else "CHoCH"
        return {
            "confirmed": True, "type": shift_type, "direction": "SHORT",
            "break_level": prev_low, "fvg": fvg if fvg.get("found") else None,
        }

    return {"confirmed": False, "type": None}


async def detect_kill_zone(current_time_utc) -> dict:
    """Return kill-zone info for both Precision and Flow engines."""
    t = current_time_utc.time() if hasattr(current_time_utc, "time") else current_time_utc

    # Both engines active 24/7 — session label is for display only
    session = "Off-Hours"

    if time(7, 0) <= t <= time(11, 0):
        session = "London"
    elif time(12, 0) <= t <= time(16, 0):
        session = "New York"
    elif time(17, 0) <= t <= time(21, 0):
        session = "New York PM"
    elif time(0, 0) <= t <= time(7, 0):
        session = "Asian"

    return {
        "precision_active": True,
        "flow_active": True,
        "session": session,
        "active": True,
    }


async def get_daily_bias(candles_daily: list) -> str:
    """Simplified Daily bias for Flow engine. Returns BULLISH/BEARISH/NEUTRAL."""
    if len(candles_daily) < 5:
        return "NEUTRAL"

    recent = candles_daily[-5:]
    up_days = sum(1 for c in recent if c["close"] > c["open"])
    down_days = sum(1 for c in recent if c["close"] < c["open"])

    closes = [c["close"] for c in candles_daily[-20:]]
    ma_20 = sum(closes) / len(closes)
    last_close = recent[-1]["close"]

    if up_days >= 3 and last_close > ma_20:
        return "BULLISH"
    elif down_days >= 3 and last_close < ma_20:
        return "BEARISH"
    return "NEUTRAL"


async def identify_poi_relaxed(candles_h4: list) -> dict:
    """Flow engine POI detection — accepts up to 2 wick touches."""
    if len(candles_h4) < 5:
        return {"found": False}

    for i in range(len(candles_h4) - 3, max(0, len(candles_h4) - 20), -1):
        prev_c = candles_h4[i - 1] if i > 0 else None
        c = candles_h4[i]
        next_c = candles_h4[i + 1] if i + 1 < len(candles_h4) else None
        if not prev_c or not next_c:
            continue

        # Bullish OB
        if c["close"] < c["open"] and next_c["close"] > next_c["open"] and next_c["close"] > prev_c["high"]:
            ob_high = max(c["open"], c["close"])
            ob_low = min(c["open"], c["close"])
            ob_mid = (ob_high + ob_low) / 2
            touch_count = sum(
                1 for j in range(i + 2, len(candles_h4))
                if candles_h4[j]["low"] <= ob_high and candles_h4[j]["high"] >= ob_low
            )
            if touch_count <= 2:
                return {
                    "found": True, "type": "OB", "price": ob_mid,
                    "high": ob_high, "low": ob_low,
                    "touch_count": touch_count, "direction": "LONG",
                }

        # Bearish OB
        if c["close"] > c["open"] and next_c["close"] < next_c["open"] and next_c["close"] < prev_c["low"]:
            ob_high = max(c["open"], c["close"])
            ob_low = min(c["open"], c["close"])
            ob_mid = (ob_high + ob_low) / 2
            touch_count = sum(
                1 for j in range(i + 2, len(candles_h4))
                if candles_h4[j]["low"] <= ob_high and candles_h4[j]["high"] >= ob_low
            )
            if touch_count <= 2:
                return {
                    "found": True, "type": "OB", "price": ob_mid,
                    "high": ob_high, "low": ob_low,
                    "touch_count": touch_count, "direction": "SHORT",
                }

    fvg = await detect_displacement_and_fvg(candles_h4[-10:])
    if fvg.get("found"):
        return {
            "found": True, "type": "FVG", "price": fvg["ce"],
            "high": fvg["high"], "low": fvg["low"],
            "touch_count": 0,
            "direction": "LONG" if fvg["type"] == "bullish" else "SHORT",
        }

    return {"found": False}


async def get_asian_session_levels(candles_h1: list) -> dict:
    """Get Asian session (00:00-08:00 UTC) high/low for Judas Swing detection."""
    asian_candles = []
    for c in candles_h1:
        ts = c.get("timestamp") or c.get("time")
        if ts:
            if isinstance(ts, (int, float)):
                dt = datetime.utcfromtimestamp(ts)
            elif isinstance(ts, datetime):
                dt = ts
            else:
                continue
            if time(0, 0) <= dt.time() <= time(8, 0):
                asian_candles.append(c)

    if not asian_candles:
        return {}

    return {
        "high": max(c["high"] for c in asian_candles),
        "low": min(c["low"] for c in asian_candles),
    }
