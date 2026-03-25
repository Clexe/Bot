import asyncio
from datetime import datetime, timedelta
import aiohttp
from utils.logger import get_logger

logger = get_logger(__name__)

CFTC_GOLD_CODE = "088691"
CFTC_SILVER_CODE = "084691"
CFTC_BASE_URL = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"

PAIR_TO_CFTC = {
    "XAUUSD": CFTC_GOLD_CODE,
    "XAGUSD": CFTC_SILVER_CODE,
}


async def fetch_cot_data(cftc_code: str, lookback_weeks: int = 32):
    """Fetch CFTC COT data for a commodity futures code."""
    try:
        params = {
            "$where": f"cftc_commodity_code='{cftc_code}'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": str(lookback_weeks),
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(CFTC_BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                return await resp.json()
    except Exception as e:
        logger.error("COT fetch failed for %s: %s", cftc_code, e)
        return None


def calculate_percentile(values: list, current: int) -> int:
    """Calculate percentile rank of current value within historical values."""
    if not values:
        return 50
    below = sum(1 for v in values if v <= current)
    return int((below / len(values)) * 100)


async def get_cot_bias(pair: str, db) -> dict:
    """Get COT bias for a pair. Returns bias, percentile, and whether it passes Gate 1.

    Reads cot_lookback_weeks from bot_settings on every call.
    Falls back to cot_cache if live fetch fails.
    """
    if pair not in PAIR_TO_CFTC:
        return {"passed": True, "bias": None, "percentile": None, "skip_reason": "no_cot_pair"}

    lookback_row = await db.fetchrow(
        "SELECT value FROM bot_settings WHERE key='cot_lookback_weeks'"
    )
    lookback_weeks = int(lookback_row["value"]) if lookback_row else 32

    cached = await db.fetchrow(
        "SELECT * FROM cot_cache WHERE pair=%s AND valid_until > NOW() ORDER BY cached_at DESC LIMIT 1",
        (pair,),
    )

    cftc_code = PAIR_TO_CFTC[pair]
    data = await fetch_cot_data(cftc_code, lookback_weeks)

    if not data:
        if cached:
            logger.warning("COT fetch failed for %s, using cache", pair)
            bias = cached["bias"]
            percentile = cached["percentile"]
        else:
            logger.warning("COT fetch failed for %s and no cache available", pair)
            return {"passed": False, "bias": "NEUTRAL", "percentile": 50, "reject_reason": "COT unavailable"}
    else:
        commercial_nets = []
        for row in data:
            try:
                comm_long = int(row.get("comm_positions_long_all", 0))
                comm_short = int(row.get("comm_positions_short_all", 0))
                commercial_nets.append(comm_long - comm_short)
            except (ValueError, TypeError):
                continue

        if not commercial_nets:
            return {"passed": False, "bias": "NEUTRAL", "percentile": 50, "reject_reason": "No valid COT data"}

        current_net = commercial_nets[0]
        percentile = calculate_percentile(commercial_nets, current_net)

        if percentile <= 20:
            bias = "BULLISH"
        elif percentile >= 80:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        valid_until = datetime.utcnow() + timedelta(days=7)
        try:
            await db.execute(
                """INSERT INTO cot_cache (pair, bias, percentile, commercial_net, lookback_weeks, valid_until)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (pair, bias, percentile, current_net, lookback_weeks, valid_until),
            )
        except Exception as e:
            logger.error("Failed to cache COT for %s: %s", pair, e)

    if bias == "NEUTRAL":
        return {"passed": False, "bias": bias, "percentile": percentile, "reject_reason": "COT neutral zone"}

    return {"passed": True, "bias": bias, "percentile": percentile}


async def apply_intermarket_downgrade(pair: str, direction: str, score: int, db) -> int:
    """Apply intermarket downgrade rules for gold/silver.

    DXY above Weekly OC → downgrade gold/silver long scores by 2.
    US10Y above Weekly resistance → downgrade gold long by 1.
    """
    if pair not in ("XAUUSD", "XAGUSD"):
        return score

    dxy_levels = await db.fetch(
        "SELECT * FROM oc_levels WHERE pair='DXY' AND timeframe='W' AND freshness_status='fresh' AND level_type='resistance' ORDER BY level_price DESC LIMIT 1"
    )
    if dxy_levels and direction == "LONG":
        score -= 2
        logger.info("Intermarket downgrade: DXY above Weekly OC, %s LONG score -2", pair)

    us10y_levels = await db.fetch(
        "SELECT * FROM oc_levels WHERE pair='US10Y' AND timeframe='W' AND freshness_status='fresh' AND level_type='resistance' ORDER BY level_price DESC LIMIT 1"
    )
    if us10y_levels and direction == "LONG" and pair == "XAUUSD":
        score -= 1
        logger.info("Intermarket downgrade: US10Y above Weekly resistance, XAUUSD LONG score -1")

    return max(score, 0)


async def refresh_cot(db):
    """Force refresh COT data for all COT-enabled pairs. Called by /refreshcot admin command."""
    results = {}
    for pair, cftc_code in PAIR_TO_CFTC.items():
        lookback_row = await db.fetchrow(
            "SELECT value FROM bot_settings WHERE key='cot_lookback_weeks'"
        )
        lookback_weeks = int(lookback_row["value"]) if lookback_row else 32

        data = await fetch_cot_data(cftc_code, lookback_weeks)
        if data:
            commercial_nets = []
            for row in data:
                try:
                    comm_long = int(row.get("comm_positions_long_all", 0))
                    comm_short = int(row.get("comm_positions_short_all", 0))
                    commercial_nets.append(comm_long - comm_short)
                except (ValueError, TypeError):
                    continue

            if commercial_nets:
                current_net = commercial_nets[0]
                percentile = calculate_percentile(commercial_nets, current_net)
                if percentile <= 20:
                    bias = "BULLISH"
                elif percentile >= 80:
                    bias = "BEARISH"
                else:
                    bias = "NEUTRAL"

                valid_until = datetime.utcnow() + timedelta(days=7)
                await db.execute(
                    """INSERT INTO cot_cache (pair, bias, percentile, commercial_net, lookback_weeks, valid_until)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (pair, bias, percentile, current_net, lookback_weeks, valid_until),
                )
                results[pair] = {"bias": bias, "percentile": percentile}
            else:
                results[pair] = {"error": "No valid data rows"}
        else:
            results[pair] = {"error": "Fetch failed"}

    return results
