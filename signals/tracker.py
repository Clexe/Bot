from datetime import datetime
from config import DERIV_PAIRS
from utils.logger import get_logger

logger = get_logger(__name__)


async def track_open_signals(db, deriv_client, bybit_client):
    """Check open signals against current price for SL/TP hits."""
    try:
        rows = await db.fetch("SELECT * FROM signals WHERE status = 'open'")
    except Exception as e:
        logger.error("Failed to fetch open signals: %s", e)
        return

    for sig in rows:
        try:
            pair = sig["pair"]
            price = await _get_price(pair, deriv_client, bybit_client)
            if price is None:
                continue

            direction = sig["direction"]
            sl = sig["sl_price"]
            tp = sig["tp_price"]
            hit = None

            if direction == "LONG":
                if price <= sl:
                    hit = "loss"
                elif price >= tp:
                    hit = "win"
            else:
                if price >= sl:
                    hit = "loss"
                elif price <= tp:
                    hit = "win"

            if hit:
                await db.execute(
                    "UPDATE signals SET status='closed', result=$1, closed_at=NOW() WHERE id=$2",
                    hit, sig["id"],
                )
                await _update_daily_stats(db, hit)
                logger.info("Signal %s (%s %s) -> %s", sig["id"], pair, direction, hit)

        except Exception as e:
            logger.error("Tracker error for signal %s: %s", sig["id"], e)


async def get_daily_losses(db) -> int:
    row = await db.fetchrow("SELECT losses FROM daily_stats WHERE date = CURRENT_DATE")
    return row["losses"] if row else 0


async def _update_daily_stats(db, result: str):
    await db.execute(
        """INSERT INTO daily_stats (date, signals_sent, wins, losses)
           VALUES (CURRENT_DATE, 0, 0, 0)
           ON CONFLICT (date) DO NOTHING"""
    )
    col = "wins" if result == "win" else "losses"
    await db.execute(
        f"UPDATE daily_stats SET {col} = {col} + 1 WHERE date = CURRENT_DATE"
    )


async def _get_price(pair, deriv_client, bybit_client):
    try:
        if pair in DERIV_PAIRS:
            return await deriv_client.get_current_price(pair)
        else:
            ticker = await bybit_client.get_ticker(pair)
            p = ticker.get("lastPrice")
            return float(p) if p else None
    except Exception:
        return None
