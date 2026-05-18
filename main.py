import asyncio
import os
import signal

import aiohttp.web

from config import DATABASE_URL, DERIV_APP_ID, SCAN_INTERVAL_MINUTES
from database.db import Database
from database.schema import init_schema
from feeds.deriv_client import DerivClient
from feeds.bybit_client import BybitClient
from delivery.telegram_bot import TelegramBot
from delivery.scheduler import run_scan, run_tracker, make_scheduler
from utils.logger import get_logger

logger = get_logger(__name__)


async def _health(request):
    return aiohttp.web.Response(text="OK")


async def start():
    logger.info("Starting Signalix Bot...")

    db = Database(DATABASE_URL)
    await db.connect()
    await init_schema(db)

    deriv = DerivClient(DERIV_APP_ID)
    await deriv.connect()
    bybit = BybitClient()

    telegram = TelegramBot()
    await telegram.start_polling()

    scheduler = make_scheduler()
    scheduler.add_job(
        run_scan, "interval", minutes=SCAN_INTERVAL_MINUTES,
        args=[db, telegram, deriv, bybit],
        id="scan", max_instances=1, misfire_grace_time=60,
    )
    scheduler.add_job(
        run_tracker, "interval", minutes=1,
        args=[db, deriv, bybit],
        id="tracker", max_instances=1, misfire_grace_time=30,
    )
    scheduler.start()
    logger.info("Scheduler started — scan every %dm, tracker every 1m", SCAN_INTERVAL_MINUTES)

    app = aiohttp.web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health server on port %d", port)

    stop_event = asyncio.Event()

    def _shutdown(*_):
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    logger.info("Bot is running")
    await stop_event.wait()

    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await telegram.stop()
    await bybit.close()
    await runner.cleanup()
    await db.close()


if __name__ == "__main__":
    asyncio.run(start())
