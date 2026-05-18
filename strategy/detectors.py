def detect_fvg(candles: list, lookback: int = 10) -> list:
    """Find Fair Value Gaps in recent candles."""
    fvgs = []
    start = max(1, len(candles) - lookback)
    for i in range(start, len(candles) - 1):
        prev = candles[i - 1]
        curr = candles[i]
        nxt = candles[i + 1]

        if nxt["low"] > prev["high"]:
            fvgs.append({
                "type": "bullish", "high": nxt["low"], "low": prev["high"],
                "ce": (nxt["low"] + prev["high"]) / 2, "index": i,
            })
        if nxt["high"] < prev["low"]:
            fvgs.append({
                "type": "bearish", "high": prev["low"], "low": nxt["high"],
                "ce": (prev["low"] + nxt["high"]) / 2, "index": i,
            })
    return fvgs


def detect_order_block(candles: list, lookback: int = 10) -> list:
    """Find Order Blocks — last opposing candle before displacement."""
    obs = []
    avg = _avg_body(candles, 20)
    start = max(1, len(candles) - lookback)
    for i in range(start, len(candles)):
        c = candles[i]
        prev = candles[i - 1]
        body = abs(c["close"] - c["open"])

        if c["close"] > c["open"] and body > avg * 1.5 and prev["close"] < prev["open"]:
            obs.append({
                "type": "bullish",
                "high": max(prev["open"], prev["close"]),
                "low": min(prev["open"], prev["close"]),
                "mid": (prev["open"] + prev["close"]) / 2,
                "index": i - 1,
            })
        if c["close"] < c["open"] and body > avg * 1.5 and prev["close"] > prev["open"]:
            obs.append({
                "type": "bearish",
                "high": max(prev["open"], prev["close"]),
                "low": min(prev["open"], prev["close"]),
                "mid": (prev["open"] + prev["close"]) / 2,
                "index": i - 1,
            })
    return obs


def detect_displacement(candles: list, lookback: int = 5) -> list:
    """Detect displacement candles (body >> average)."""
    displacements = []
    avg = _avg_body(candles, 20)
    start = max(0, len(candles) - lookback)
    for i in range(start, len(candles)):
        c = candles[i]
        body = abs(c["close"] - c["open"])
        if body > avg * 1.5:
            displacements.append({
                "index": i,
                "direction": "LONG" if c["close"] > c["open"] else "SHORT",
                "body_size": body,
            })
    return displacements


def detect_choch(candles: list) -> dict:
    """Detect Change of Character — break against prevailing swing structure."""
    if len(candles) < 5:
        return {"detected": False}

    swing_highs, swing_lows = _swing_points(candles)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"detected": False}

    last_sh = swing_highs[-1][1]
    prev_sh = swing_highs[-2][1]
    last_sl = swing_lows[-1][1]
    prev_sl = swing_lows[-2][1]
    avg = _avg_body(candles, 10)

    for offset in range(min(3, len(candles))):
        idx = len(candles) - 1 - offset
        c = candles[idx]
        body = abs(c["close"] - c["open"])
        has_disp = body > avg * 1.2

        if last_sl < prev_sl and c["close"] > last_sh and has_disp:
            return {"detected": True, "direction": "LONG", "break_level": last_sh, "index": idx}
        if last_sh > prev_sh and c["close"] < last_sl and has_disp:
            return {"detected": True, "direction": "SHORT", "break_level": last_sl, "index": idx}

    return {"detected": False}


def detect_bos(candles: list) -> dict:
    """Detect Break of Structure in trending direction."""
    if len(candles) < 5:
        return {"detected": False}

    swing_highs, swing_lows = _swing_points(candles)
    if not swing_highs or not swing_lows:
        return {"detected": False}

    last_sh = swing_highs[-1][1]
    last_sl = swing_lows[-1][1]
    avg = _avg_body(candles, 10)

    for offset in range(min(3, len(candles))):
        idx = len(candles) - 1 - offset
        c = candles[idx]
        body = abs(c["close"] - c["open"])
        has_disp = body > avg * 1.2

        if c["close"] > last_sh and has_disp:
            return {"detected": True, "direction": "LONG", "break_level": last_sh, "index": idx}
        if c["close"] < last_sl and has_disp:
            return {"detected": True, "direction": "SHORT", "break_level": last_sl, "index": idx}

    return {"detected": False}


def detect_liquidity_sweep(candles: list, levels: dict, lookback: int = 10) -> list:
    """Detect wick sweeps of key levels (wick through, body closes back)."""
    sweeps = []
    start = max(0, len(candles) - lookback)
    level_names = ["PDH", "PDL", "PWH", "PWL", "ASIAN_HIGH", "ASIAN_LOW"]

    for i in range(start, len(candles)):
        c = candles[i]
        body_high = max(c["open"], c["close"])
        body_low = min(c["open"], c["close"])

        for name in level_names:
            if name not in levels:
                continue
            lvl = levels[name]

            if c["high"] > lvl > body_high:
                sweeps.append({
                    "index": i, "level_name": name, "level_price": lvl,
                    "sweep_type": "above", "wick": c["high"],
                    "implied_direction": "SHORT",
                })
            if c["low"] < lvl < body_low:
                sweeps.append({
                    "index": i, "level_name": name, "level_price": lvl,
                    "sweep_type": "below", "wick": c["low"],
                    "implied_direction": "LONG",
                })
    return sweeps


def _swing_points(candles: list):
    highs, lows = [], []
    for i in range(2, len(candles) - 2):
        if candles[i]["high"] > candles[i - 1]["high"] and candles[i]["high"] > candles[i + 1]["high"]:
            highs.append((i, candles[i]["high"]))
        if candles[i]["low"] < candles[i - 1]["low"] and candles[i]["low"] < candles[i + 1]["low"]:
            lows.append((i, candles[i]["low"]))
    return highs, lows


def _avg_body(candles: list, period: int = 20) -> float:
    recent = candles[-period:] if len(candles) >= period else candles
    bodies = [abs(c["close"] - c["open"]) for c in recent]
    return sum(bodies) / max(1, len(bodies))
