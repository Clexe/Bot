from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def detect_order_blocks(candles: list, timeframe: str) -> list:
    """Detect bullish OB, bearish OB, and Breaker Block formations."""
    obs = []
    if len(candles) < 3:
        return obs

    for i in range(1, len(candles) - 1):
        prev_c, cur, nxt = candles[i - 1], candles[i], candles[i + 1]

        if cur["close"] < cur["open"] and nxt["close"] > nxt["open"] and nxt["close"] > prev_c["high"]:
            ob_high = max(cur["open"], cur["close"])
            ob_low = min(cur["open"], cur["close"])
            touch_count = 0
            mitigated = False
            for j in range(i + 2, len(candles)):
                if candles[j]["low"] <= ob_high and candles[j]["high"] >= ob_low:
                    touch_count += 1
                if candles[j]["close"] < ob_low:
                    mitigated = True
                    break

            obs.append({
                "timeframe": timeframe,
                "ob_type": "breaker_bullish" if mitigated else "bullish",
                "ob_high": ob_high, "ob_low": ob_low,
                "ob_midpoint": (ob_high + ob_low) / 2,
                "validity_status": "breaker" if mitigated else "valid",
                "touch_count": touch_count,
            })

        if cur["close"] > cur["open"] and nxt["close"] < nxt["open"] and nxt["close"] < prev_c["low"]:
            ob_high = max(cur["open"], cur["close"])
            ob_low = min(cur["open"], cur["close"])
            touch_count = 0
            mitigated = False
            for j in range(i + 2, len(candles)):
                if candles[j]["low"] <= ob_high and candles[j]["high"] >= ob_low:
                    touch_count += 1
                if candles[j]["close"] > ob_high:
                    mitigated = True
                    break

            obs.append({
                "timeframe": timeframe,
                "ob_type": "breaker_bearish" if mitigated else "bearish",
                "ob_high": ob_high, "ob_low": ob_low,
                "ob_midpoint": (ob_high + ob_low) / 2,
                "validity_status": "breaker" if mitigated else "valid",
                "touch_count": touch_count,
            })

    return obs


async def store_order_blocks(db, pair: str, obs: list):
    """Persist detected order blocks to database."""
    for ob in obs:
        try:
            existing = await db.fetchrow(
                """SELECT id FROM order_blocks WHERE pair=%s AND timeframe=%s AND ob_type=%s
                   AND ABS(ob_midpoint - %s) < 0.001""",
                (pair, ob["timeframe"], ob["ob_type"], ob["ob_midpoint"]),
            )
            if not existing:
                await db.execute(
                    """INSERT INTO order_blocks (pair, timeframe, ob_type, ob_high, ob_low, ob_midpoint, validity_status, touch_count)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (pair, ob["timeframe"], ob["ob_type"], ob["ob_high"],
                     ob["ob_low"], ob["ob_midpoint"], ob["validity_status"], ob["touch_count"]),
                )
            else:
                await db.execute(
                    "UPDATE order_blocks SET validity_status=%s, touch_count=%s WHERE id=%s",
                    (ob["validity_status"], ob["touch_count"], existing["id"]),
                )
        except Exception as e:
            logger.error("Failed to store OB for %s: %s", pair, e)
