import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import ALL_PAIRS, CRYPTO_PAIRS, FOREX_PAIRS
from strategy.precision_pipeline import run_precision_pipeline
from strategy.flow_pipeline import run_flow_pipeline
from signals.generator import generate_signal
from ai.deepseek_client import generate_precision_rationale, generate_flow_rationale
from utils.logger import get_logger

logger = get_logger(__name__)


async def queue_signal_for_delivery(db, signal_id: int, chat_id: int, message: str, delay_minutes: int):
    """Insert delayed delivery row used by free-tier signal delay."""
    deliver_at = datetime.utcnow() + timedelta(minutes=delay_minutes)
    await db.execute(
        "INSERT INTO delivery_queue (signal_id, chat_id, message, deliver_at, delivered) VALUES (%s,%s,%s,%s,false)",
        (signal_id, chat_id, message, deliver_at),
    )


async def process_delivery_queue(db, telegram):
    """Deliver all due queue items and mark rows delivered."""
    try:
        due = await db.fetch("SELECT * FROM delivery_queue WHERE deliver_at <= NOW() AND delivered = false")
        for item in due:
            try:
                await telegram.send_message(item["chat_id"], item["message"])
                await db.execute("UPDATE delivery_queue SET delivered=true, delivered_at=NOW() WHERE id=%s", (item["id"],))
            except Exception as e:
                logger.error("Delivery failed for queue item %s: %s", item["id"], e)
    except Exception as e:
        logger.error("Delivery queue processing failed: %s", e)


async def run_precision_scan(db, telegram, deriv_client, bybit_client):
    """Precision scan: runs every 15 minutes during Kill Zones."""
    logger.info("Running Precision scan cycle")

    stats = {"scanned": 0, "no_candles": 0, "rejected": 0, "no_signal": 0, "sent": 0, "errors": 0}

    for pair in ALL_PAIRS:
        try:
            stats["scanned"] += 1
            candles = await _fetch_candles(pair, deriv_client, bybit_client)
            if not candles:
                stats["no_candles"] += 1
                continue

            result = await run_precision_pipeline(pair, candles, db)
            if result.get("status") != "passed":
                stats["rejected"] += 1
                logger.info("Precision REJECTED %s: gate=%s reason=%s", pair, result.get("gate", "?"), result.get("reason", "?"))
                continue

            signal = await generate_signal(result, db)
            if not signal:
                stats["no_signal"] += 1
                continue

            rationale = await generate_precision_rationale(signal)
            signal["rationale"] = rationale
            if rationale:
                try:
                    await db.execute("UPDATE signals SET rationale=%s WHERE id=%s", (rationale, signal.get("id")))
                except Exception:
                    pass

            await telegram.deliver_signal(db, signal)
            stats["sent"] += 1
            logger.info("Precision signal sent: %s %s score=%s/15", pair, signal["direction"], signal["score"])

        except Exception as e:
            stats["errors"] += 1
            logger.error("Precision scan error for %s: %s", pair, e)
            try:
                await db.execute(
                    "INSERT INTO errors (error_type, error_message, pair) VALUES (%s, %s, %s)",
                    ("precision_scan", str(e), pair),
                )
            except Exception:
                pass

    logger.info(
        "Precision scan summary: %d scanned | %d sent | %d rejected | %d no_candles | %d no_signal | %d errors",
        stats["scanned"], stats["sent"], stats["rejected"], stats["no_candles"], stats["no_signal"], stats["errors"],
    )


async def run_flow_scan(db, telegram, deriv_client, bybit_client):
    """Flow scan: runs every 5 minutes during Kill Zones (extended windows)."""
    logger.info("Running Flow scan cycle")

    stats = {"scanned": 0, "no_candles": 0, "rejected": 0, "no_signal": 0, "sent": 0, "errors": 0}

    for pair in ALL_PAIRS:
        try:
            stats["scanned"] += 1
            candles = await _fetch_candles(pair, deriv_client, bybit_client)
            if not candles:
                stats["no_candles"] += 1
                continue

            result = await run_flow_pipeline(pair, candles, db)
            if result.get("status") != "passed":
                stats["rejected"] += 1
                logger.info("Flow REJECTED %s: gate=%s reason=%s", pair, result.get("gate", "?"), result.get("reason", "?"))
                continue

            signal = await generate_signal(result, db)
            if not signal:
                stats["no_signal"] += 1
                continue

            rationale = await generate_flow_rationale(signal)
            signal["rationale"] = rationale
            if rationale:
                try:
                    await db.execute("UPDATE signals SET rationale=%s WHERE id=%s", (rationale, signal.get("id")))
                except Exception:
                    pass

            await telegram.deliver_signal(db, signal)
            stats["sent"] += 1
            logger.info("Flow signal sent: %s %s score=%s/8", pair, signal["direction"], signal["score"])

        except Exception as e:
            stats["errors"] += 1
            logger.error("Flow scan error for %s: %s", pair, e)
            try:
                await db.execute(
                    "INSERT INTO errors (error_type, error_message, pair) VALUES (%s, %s, %s)",
                    ("flow_scan", str(e), pair),
                )
            except Exception:
                pass

    logger.info(
        "Flow scan summary: %d scanned | %d sent | %d rejected | %d no_candles | %d no_signal | %d errors",
        stats["scanned"], stats["sent"], stats["rejected"], stats["no_candles"], stats["no_signal"], stats["errors"],
    )


async def _fetch_candles(pair: str, deriv_client, bybit_client) -> dict:
    """Fetch multi-timeframe candles for a pair from the appropriate feed.

    Wrapped in an overall timeout so a single slow pair cannot block an
    entire scan cycle indefinitely (the root cause of run_flow_scan
    reaching max running instances).
    """
    try:
        return await asyncio.wait_for(
            _fetch_candles_inner(pair, deriv_client, bybit_client),
            timeout=120,
        )
    except asyncio.TimeoutError:
        logger.error("Candle fetch timed out for %s after 120s", pair)
        return {}


async def _fetch_candles_inner(pair: str, deriv_client, bybit_client) -> dict:
    """Inner implementation of candle fetching."""
    candles = {}

    try:
        if pair in CRYPTO_PAIRS:
            for tf in ("D", "H4", "H1", "M15", "M5"):
                data = await bybit_client.get_kline(pair, tf, limit=100)
                result_list = data.get("result", {}).get("list", [])
                candles[tf] = [
                    {
                        "timestamp": float(c[0]) / 1000,
                        "open": float(c[1]), "high": float(c[2]),
                        "low": float(c[3]), "close": float(c[4]),
                        "volume": float(c[5]),
                    }
                    for c in reversed(result_list)
                ]
            candles["Daily"] = candles.get("D", [])
        else:
            from config import TF_MAP_DERIV
            for tf, granularity in TF_MAP_DERIV.items():
                if tf in ("M1",):
                    continue
                raw = await deriv_client.get_history(pair, granularity=granularity, count=100)
                candles[tf] = [
                    {
                        "timestamp": c.get("epoch", 0),
                        "open": float(c.get("open", 0)), "high": float(c.get("high", 0)),
                        "low": float(c.get("low", 0)), "close": float(c.get("close", 0)),
                    }
                    for c in raw
                ]
            candles["Daily"] = candles.get("D", [])
    except Exception as e:
        logger.error("Failed to fetch candles for %s: %s", pair, e)
        return {}

    return candles


def make_scheduler():
    """Create AsyncIOScheduler instance."""
    return AsyncIOScheduler()
