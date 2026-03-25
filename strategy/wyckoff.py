from utils.logger import get_logger

logger = get_logger(__name__)


async def detect_wyckoff_phase(candles_daily: list, pair: str, db) -> dict:
    """Detect Wyckoff accumulation/distribution phase on Daily chart.

    Returns:
        dict with phase (A/B/C/D/E), signal_permitted, priority, and detail.

    Phase A/B → signal_permitted=False
    Phase C   → signal_permitted=True, priority=HIGH
    Phase D/E → signal_permitted=True, priority=NORMAL
    """
    if len(candles_daily) < 30:
        return {"phase": None, "signal_permitted": False, "priority": None, "detail": "Insufficient data"}

    recent = candles_daily[-30:]
    highs = [c["high"] for c in recent]
    lows = [c["low"] for c in recent]
    closes = [c["close"] for c in recent]

    range_high = max(highs)
    range_low = min(lows)
    range_size = range_high - range_low
    if range_size == 0:
        return {"phase": None, "signal_permitted": False, "priority": None, "detail": "Zero range"}

    mid = (range_high + range_low) / 2

    last_5_highs = highs[-5:]
    last_5_lows = lows[-5:]
    last_5_closes = closes[-5:]

    spring_detected = False
    upthrust_detected = False

    for i in range(len(recent) - 5, len(recent)):
        c = recent[i]
        body_low = min(c["open"], c["close"])
        body_high = max(c["open"], c["close"])

        if c["low"] < range_low and body_low > range_low:
            if i + 1 < len(recent):
                next_c = recent[i + 1]
                disp = abs(next_c["close"] - next_c["open"])
                avg_body = sum(abs(x["close"] - x["open"]) for x in recent[-10:]) / 10
                if next_c["close"] > next_c["open"] and disp > avg_body * 1.5:
                    spring_detected = True

        if c["high"] > range_high and body_high < range_high:
            if i + 1 < len(recent):
                next_c = recent[i + 1]
                disp = abs(next_c["close"] - next_c["open"])
                avg_body = sum(abs(x["close"] - x["open"]) for x in recent[-10:]) / 10
                if next_c["close"] < next_c["open"] and disp > avg_body * 1.5:
                    upthrust_detected = True

    high_variance = max(last_5_highs) - min(last_5_highs)
    low_variance = max(last_5_lows) - min(last_5_lows)
    contracting = high_variance < range_size * 0.15 and low_variance < range_size * 0.15

    expanding_up = last_5_closes[-1] > range_high * 0.95 and all(
        closes[-(i + 1)] >= closes[-(i + 2)] for i in range(min(3, len(closes) - 1))
    )
    expanding_down = last_5_closes[-1] < range_low * 1.05 and all(
        closes[-(i + 1)] <= closes[-(i + 2)] for i in range(min(3, len(closes) - 1))
    )

    breakout_up = last_5_closes[-1] > range_high
    breakout_down = last_5_closes[-1] < range_low

    if breakout_up or breakout_down:
        phase = "E"
        detail = "Breakout — markup/markdown phase"
        return {"phase": phase, "signal_permitted": True, "priority": "NORMAL", "detail": detail}

    if expanding_up or expanding_down:
        phase = "D"
        detail = "Sign of Strength / Weakness — trending from range"
        return {"phase": phase, "signal_permitted": True, "priority": "NORMAL", "detail": detail}

    if spring_detected:
        phase = "C"
        detail = "Spring detected — SSL sweep + bullish displacement"
        result = {"phase": phase, "signal_permitted": True, "priority": "HIGH", "detail": detail, "direction": "LONG"}
        await _store_wyckoff(pair, result, db)
        return result

    if upthrust_detected:
        phase = "C"
        detail = "Upthrust detected — BSL sweep + bearish displacement"
        result = {"phase": phase, "signal_permitted": True, "priority": "HIGH", "detail": detail, "direction": "SHORT"}
        await _store_wyckoff(pair, result, db)
        return result

    if contracting:
        phase = "B"
        detail = "Range contraction — building cause"
        return {"phase": phase, "signal_permitted": False, "priority": None, "detail": detail}

    phase = "A"
    detail = "Preliminary support/supply — stopping action"
    return {"phase": phase, "signal_permitted": False, "priority": None, "detail": detail}


async def _store_wyckoff(pair: str, result: dict, db):
    """Store Wyckoff phase transition in storylines table."""
    try:
        await db.execute(
            """UPDATE storylines SET wyckoff_phase=%s WHERE pair=%s AND is_active=true""",
            (result["phase"], pair),
        )
    except Exception as e:
        logger.error("Failed to store Wyckoff phase for %s: %s", pair, e)
