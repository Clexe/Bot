from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def detect_oc_levels(candles: list, timeframe: str) -> list:
    """Detect MSNR OC levels from body-only A/V pivots.

    ONLY candle bodies, ignore wicks.
    A-shape (resistance) and V-shape (support) pivot detection.
    Track freshness per level (zero wick touches = fresh).
    """
    levels = []
    if len(candles) < 3:
        return levels

    for i in range(1, len(candles) - 1):
        prev_c, cur, nxt = candles[i - 1], candles[i], candles[i + 1]
        body_high = max(cur["open"], cur["close"])
        body_low = min(cur["open"], cur["close"])
        prev_body_high = max(prev_c["open"], prev_c["close"])
        prev_body_low = min(prev_c["open"], prev_c["close"])
        nxt_body_high = max(nxt["open"], nxt["close"])
        nxt_body_low = min(nxt["open"], nxt["close"])

        if body_high > prev_body_high and body_high > nxt_body_high:
            touch_count = 0
            for j in range(i + 1, len(candles)):
                if candles[j]["high"] >= body_high * 0.9999 and candles[j]["high"] <= body_high * 1.0001:
                    touch_count += 1
            freshness = "fresh" if touch_count == 0 else "tested"
            levels.append({
                "timeframe": timeframe, "level_price": body_high,
                "level_type": "resistance", "freshness_status": freshness,
                "touch_count": touch_count,
            })

        if body_low < prev_body_low and body_low < nxt_body_low:
            touch_count = 0
            for j in range(i + 1, len(candles)):
                if candles[j]["low"] <= body_low * 1.0001 and candles[j]["low"] >= body_low * 0.9999:
                    touch_count += 1
            freshness = "fresh" if touch_count == 0 else "tested"
            levels.append({
                "timeframe": timeframe, "level_price": body_low,
                "level_type": "support", "freshness_status": freshness,
                "touch_count": touch_count,
            })

    return levels


async def detect_miss_levels(candles: list, timeframe: str) -> list:
    """Detect MISS (institutional) levels where price approached but didn't touch."""
    levels = []
    oc_levels = await detect_oc_levels(candles, timeframe)

    for lvl in oc_levels:
        if lvl["touch_count"] == 0:
            close_approaches = 0
            for c in candles:
                body_high = max(c["open"], c["close"])
                body_low = min(c["open"], c["close"])
                lp = lvl["level_price"]

                if lvl["level_type"] == "resistance":
                    if body_high > lp * 0.998 and body_high < lp:
                        close_approaches += 1
                elif lvl["level_type"] == "support":
                    if body_low < lp * 1.002 and body_low > lp:
                        close_approaches += 1

            if close_approaches >= 2:
                levels.append({**lvl, "is_miss": True, "approach_count": close_approaches})

    return levels


async def store_oc_levels(db, pair: str, levels: list):
    """Persist detected OC levels to database."""
    for lvl in levels:
        try:
            existing = await db.fetchrow(
                "SELECT id FROM oc_levels WHERE pair=%s AND timeframe=%s AND ABS(level_price - %s) < 0.001 AND level_type=%s",
                (pair, lvl["timeframe"], lvl["level_price"], lvl["level_type"]),
            )
            if not existing:
                await db.execute(
                    """INSERT INTO oc_levels (pair, timeframe, level_price, level_type, freshness_status, touch_count)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (pair, lvl["timeframe"], lvl["level_price"], lvl["level_type"],
                     lvl["freshness_status"], lvl["touch_count"]),
                )
            else:
                await db.execute(
                    "UPDATE oc_levels SET freshness_status=%s, touch_count=%s, last_tested_at=NOW() WHERE id=%s",
                    (lvl["freshness_status"], lvl["touch_count"], existing["id"]),
                )
        except Exception as e:
            logger.error("Failed to store OC level for %s: %s", pair, e)
