import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    ALL_PAIRS, DERIV_PAIRS, BYBIT_PAIRS,
    TF_MAP_DERIV, TF_MAP_BYBIT,
    MAX_DAILY_LOSSES, PAIR_DISPLAY, DUPLICATE_PREVENTION_HOURS,
)
from strategy.bias import get_htf_bias
from strategy.levels import map_key_levels
from strategy.setups import scan_setups
from strategy.execution import compute_trade
from signals.generator import create_signal, format_signal
from signals.tracker import track_open_signals, get_daily_losses
from utils.helpers import in_kill_zone
from utils.logger import get_logger

logger = get_logger(__name__)


async def run_scan(db, telegram, deriv_client, bybit_client):
    """Main scan cycle — runs every 15 min, only inside kill zones."""
    kz = in_kill_zone()
    if not kz["active"]:
        return

    session = kz["session"]
    logger.info("Scan cycle — %s session", session)

    losses = await get_daily_losses(db)
    if losses >= MAX_DAILY_LOSSES:
        logger.info("Daily loss limit reached (%d). Skipping.", losses)
        return

    for pair in ALL_PAIRS:
        try:
            candles = await _fetch_candles(pair, deriv_client, bybit_client)
            if not candles:
                continue

            daily = candles.get("D", [])
            if len(daily) < 5:
                continue

            bias_info = get_htf_bias(daily)
            if bias_info["bias"] == "NEUTRAL":
                continue

            levels = map_key_levels(daily, candles.get("H1", []))
            setups = await scan_setups(pair, candles, bias_info, levels)
            if not setups:
                continue

            for setup in setups:
                if await _is_duplicate(db, pair, setup["direction"]):
                    continue

                trade = compute_trade(setup, levels)
                if not trade:
                    continue

                trade["bias"] = bias_info["bias"]
                trade["session"] = session

                signal = await create_signal(trade, db)
                if not signal:
                    continue

                await telegram.send_signal(format_signal(signal))

                await db.execute(
                    """INSERT INTO daily_stats (date, signals_sent, wins, losses)
                       VALUES (CURRENT_DATE, 1, 0, 0)
                       ON CONFLICT (date)
                       DO UPDATE SET signals_sent = daily_stats.signals_sent + 1"""
                )

                display = PAIR_DISPLAY.get(pair, pair)
                logger.info("Signal: %s %s %s R:R 1:%.1f",
                            display, setup["direction"], setup["setup_type"], trade["rr"])

        except Exception as e:
            logger.error("Scan error %s: %s", pair, e)
            try:
                await db.execute(
                    "INSERT INTO errors (error_type, error_message, pair) VALUES ($1,$2,$3)",
                    "scan", str(e), pair,
                )
            except Exception:
                pass


async def run_tracker(db, deriv_client, bybit_client):
    try:
        await track_open_signals(db, deriv_client, bybit_client)
    except Exception as e:
        logger.error("Tracker error: %s", e)


async def _fetch_candles(pair, deriv_client, bybit_client):
    try:
        return await asyncio.wait_for(
            _fetch_inner(pair, deriv_client, bybit_client),
            timeout=120,
        )
    except asyncio.TimeoutError:
        logger.error("Candle fetch timed out for %s", pair)
        return {}


async def _fetch_inner(pair, deriv_client, bybit_client):
    candles = {}
    if pair in DERIV_PAIRS:
        for tf, gran in TF_MAP_DERIV.items():
            raw = await deriv_client.get_candles(pair, gran, count=100)
            candles[tf] = [
                {
                    "timestamp": c.get("epoch", 0),
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                }
                for c in raw
            ]
    elif pair in BYBIT_PAIRS:
        for tf, interval in TF_MAP_BYBIT.items():
            data = await bybit_client.get_kline(pair, interval, limit=100)
            result_list = data.get("result", {}).get("list", [])
            candles[tf] = [
                {
                    "timestamp": float(c[0]) / 1000,
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                for c in reversed(result_list)
            ]
    return candles


async def _is_duplicate(db, pair, direction):
    cutoff = datetime.utcnow() - timedelta(hours=DUPLICATE_PREVENTION_HOURS)
    row = await db.fetchrow(
        "SELECT id FROM signals WHERE pair=$1 AND direction=$2 AND created_at > $3 LIMIT 1",
        pair, direction, cutoff,
    )
    return row is not None


def make_scheduler():
    return AsyncIOScheduler()
