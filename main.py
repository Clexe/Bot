import asyncio
import signal
from aiohttp import web
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from config import (
    DATABASE_URL, TELEGRAM_BOT_TOKEN, DERIV_APP_ID,
    FOREX_PAIRS, CRYPTO_PAIRS, DERIV_SYMBOL_MAP,
)
from database.db import Database
from database.schema import initialize_schema
from feeds.deriv_client import DerivClient
from feeds.bybit_client import BybitClient
from delivery.scheduler import (
    make_scheduler, process_delivery_queue,
    run_precision_scan, run_flow_scan,
)
from delivery.telegram_bot import TelegramDelivery
from signals.tracker import track_open_signals
from strategy.cot_filter import refresh_cot
from api.stats_server import make_app
from admin.commands import handle_admin_command, is_admin
from bot.handlers import (
    start_command, mode_command, settf_command, sethtf_command,
    setrisk_command, setbalance_command, setriskpct_command,
    touchmode_command, journal_command,
    broadcast_command, users_command, handle_text,
)
from utils.logger import get_logger

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

        for pair in FOREX_PAIRS:
            try:
                deriv_sym = DERIV_SYMBOL_MAP.get(pair, pair)
                raw = await deriv_client.get_history(deriv_sym, granularity=60, count=1)
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
    web_app = make_app(db)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Stats API server started on port 8080")

    # Start Telegram bot with user + admin command handlers
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.bot_data["db"] = db

    # User command handlers (from main's bot.handlers)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("settf", settf_command))
    app.add_handler(CommandHandler("sethtf", sethtf_command))
    app.add_handler(CommandHandler("setrisk", setrisk_command))
    app.add_handler(CommandHandler("setbalance", setbalance_command))
    app.add_handler(CommandHandler("setriskpct", setriskpct_command))
    app.add_handler(CommandHandler("touchmode", touchmode_command))
    app.add_handler(CommandHandler("journal", journal_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("users", users_command))

    # Admin command handlers (dual-engine admin)
    async def admin_cmd_handler(update, context):
        if not update.message:
            return
        chat_id = update.message.chat_id
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        command = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""
        response = await handle_admin_command(db, chat_id, command, args)
        await update.message.reply_text(response)

    admin_commands = [
        "botstatus", "stats", "flowstatus", "pauseflow", "resumeflow",
        "pauseprecision", "resumeprecision", "pausebot", "resumebot",
        "pausepair", "resumepair", "setflowminimum", "setprecisionminimum",
        "setflowrr", "setprecisionrr", "enginestats", "cotstatus",
        "refreshcot", "rejectedsetups", "manualsignal",
    ]
    for cmd in admin_commands:
        app.add_handler(CommandHandler(cmd, admin_cmd_handler))

    # Text menu handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
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
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception:
        pass
    await runner.cleanup()
    await db.close()
    logger.info("Signalix shutdown complete")


if __name__ == "__main__":
    asyncio.run(start())
