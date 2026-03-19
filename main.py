import asyncio
from aiohttp import web
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from config import settings
from database.db import Database
from database.schema import initialize_schema
from delivery.scheduler import make_scheduler, process_delivery_queue
from delivery.telegram_bot import TelegramDelivery
from api.stats_server import make_app
from bot.handlers import (
    start_command, mode_command, settf_command, sethtf_command,
    setrisk_command, setbalance_command, setriskpct_command,
    touchmode_command, journal_command,
    broadcast_command, users_command, handle_text,
)
from utils.logger import get_logger

logger = get_logger(__name__)


async def alive_job():
    """Health heartbeat for Railway logs to avoid cold starts."""
    logger.info("SIGNALIX ALIVE")


async def start():
    """Start database, Telegram bot, scheduler, and aiohttp API server."""
    # ── Database ──
    db = Database(settings.database_url)
    await db.connect()
    await initialize_schema(db)
    logger.info("Database ready")

    # ── Delivery scheduler ──
    telegram_delivery = TelegramDelivery(settings.telegram_bot_token)
    scheduler = make_scheduler()
    scheduler.add_job(process_delivery_queue, 'interval', seconds=60, args=[db, telegram_delivery])
    scheduler.add_job(alive_job, 'interval', minutes=10)
    scheduler.start()

    # ── Telegram bot (polling) ──
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["db"] = db

    # Command handlers
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

    # Text menu handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # ── aiohttp stats server ──
    web_app = make_app(db)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("Stats API server started on port 8080")

    # ── Start Telegram polling (non-blocking) ──
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("Signalix started — Telegram bot + API server running")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await runner.cleanup()
        await db.close()


if __name__ == '__main__':
    asyncio.run(start())
