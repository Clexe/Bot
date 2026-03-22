import asyncio
import signal
from aiohttp import web
from signalix.config import (
    DATABASE_URL, TELEGRAM_BOT_TOKEN, DERIV_APP_ID,
)
from signalix.database.db import Database
from signalix.database.schema import initialize_schema
from signalix.feeds.deriv_client import DerivClient
from signalix.feeds.bybit_client import BybitClient
from signalix.delivery.scheduler import (
    make_scheduler, process_delivery_queue,
    run_precision_scan, run_flow_scan,
)
from signalix.delivery.telegram_bot import TelegramDelivery
from signalix.signals.tracker import track_open_signals
from signalix.strategy.cot_filter import refresh_cot
from signalix.api.stats_server import make_app
from signalix.admin.commands import handle_admin_command, is_admin
from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def alive_job():
    """Health heartbeat for Railway logs."""
    logger.info("SIGNALIX ALIVE")


async def cot_refresh_job(db):
    """Weekly COT data refresh."""
    try:
        results = await refresh_cot(db)
        logger.info("COT refresh completed: %s", results)
    except Exception as e:
        logger.error("COT refresh failed: %s", e)


async def tracking_job(db, telegram, deriv_client, bybit_client):
    """Track open signal outcomes via live prices."""
    try:
        current_prices = {}

        from signalix.config import FOREX_PAIRS, CRYPTO_PAIRS
        for pair in FOREX_PAIRS:
            try:
                raw = await deriv_client.get_history(pair, granularity=60, count=1)
                if raw:
                    current_prices[pair] = float(raw[-1].get("close", 0))
            except Exception:
                pass

        for pair in CRYPTO_PAIRS:
            try:
                data = await bybit_client.get_kline(pair, "M1", limit=1)
                result_list = data.get("result", {}).get("list", [])
                if result_list:
                    current_prices[pair] = float(result_list[0][4])
            except Exception:
                pass

        if current_prices:
            await track_open_signals(db, current_prices, telegram)
    except Exception as e:
        logger.error("Tracking job error: %s", e)


async def setup_telegram_commands(telegram, db):
    """Set up Telegram bot command handlers."""
    from telegram.ext import ApplicationBuilder, CommandHandler

    app_builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    async def cmd_handler(update, context):
        if not update.message:
            return
        chat_id = update.message.chat_id
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        command = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""
        response = await handle_admin_command(db, chat_id, command, args)
        await update.message.reply_text(response)

    commands = [
        "botstatus", "stats", "flowstatus", "pauseflow", "resumeflow",
        "pauseprecision", "resumeprecision", "pausebot", "resumebot",
        "pausepair", "resumepair", "setflowminimum", "setprecisionminimum",
        "setflowrr", "setprecisionrr", "enginestats", "cotstatus",
        "refreshcot", "rejectedsetups", "manualsignal",
    ]
    for cmd in commands:
        app_builder.add_handler(CommandHandler(cmd, cmd_handler))

    await app_builder.initialize()
    await app_builder.start()
    await app_builder.updater.start_polling()
    return app_builder


async def start():
    """Start all services: database, feeds, scheduler, API server, Telegram bot."""
    # Initialize database
    db = Database(DATABASE_URL)
    await db.connect()
    await initialize_schema(db)
    logger.info("Database initialized")

    # Initialize feeds
    deriv_client = DerivClient(DERIV_APP_ID)
    bybit_client = BybitClient()

    try:
        await deriv_client.connect()
        logger.info("Deriv WebSocket connected")
    except Exception as e:
        logger.error("Deriv connection failed: %s", e)

    try:
        await bybit_client.connect()
        logger.info("Bybit WebSocket connected")
    except Exception as e:
        logger.error("Bybit connection failed: %s", e)

    # Initialize Telegram delivery
    telegram = TelegramDelivery(TELEGRAM_BOT_TOKEN)

    # Initialize scheduler with all jobs
    scheduler = make_scheduler()

    # Precision scan every 15 minutes
    scheduler.add_job(
        run_precision_scan, "interval", minutes=15,
        args=[db, telegram, deriv_client, bybit_client],
        id="precision_scan",
    )

    # Flow scan every 5 minutes
    scheduler.add_job(
        run_flow_scan, "interval", minutes=5,
        args=[db, telegram, deriv_client, bybit_client],
        id="flow_scan",
    )

    # COT refresh every Sunday at 18:00 UTC
    scheduler.add_job(
        cot_refresh_job, "cron", day_of_week="sun", hour=18,
        args=[db], id="cot_refresh",
    )

    # Delivery queue processor every 60 seconds
    scheduler.add_job(
        process_delivery_queue, "interval", seconds=60,
        args=[db, telegram], id="delivery_queue",
    )

    # Signal tracking every 60 seconds
    scheduler.add_job(
        tracking_job, "interval", seconds=60,
        args=[db, telegram, deriv_client, bybit_client],
        id="signal_tracking",
    )

    # Health check every 10 minutes
    scheduler.add_job(alive_job, "interval", minutes=10, id="health_check")

    scheduler.start()
    logger.info("APScheduler started with %d jobs", len(scheduler.get_jobs()))

    # Start aiohttp stats server
    app = make_app(db)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Stats API server started on port 8080")

    # Start Telegram bot with admin command handlers
    tg_app = None
    try:
        tg_app = await setup_telegram_commands(telegram, db)
        logger.info("Telegram bot started with admin command handlers")
    except Exception as e:
        logger.error("Telegram bot setup failed: %s", e)

    logger.info("Signalix started — Precision + Flow engines running")

    # Graceful shutdown handler
    shutdown_event = asyncio.Event()

    def handle_sigterm(*_):
        logger.info("SIGTERM received, shutting down...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_sigterm)

    await shutdown_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    if tg_app:
        try:
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        except Exception:
            pass
    await runner.cleanup()
    await db.close()
    logger.info("Signalix shutdown complete")


if __name__ == "__main__":
    asyncio.run(start())
