import asyncio
import sys
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from config import BOT_TOKEN, DATABASE_URL, logger
from database import init_db, close_pool
from handlers import (
    start_command, mode_command, settf_command, sethtf_command, setrisk_command,
    setbalance_command, setriskpct_command, touchmode_command,
    backtest_command, journal_command,
    broadcast_command, users_command, handle_text,
)
from scanner import scanner_loop

# Store scanner task reference so unobserved exceptions aren't lost
_scanner_task = None


def _validate_env():
    """Validate that required environment variables are set.

    Crashes early with a clear message instead of cryptic runtime errors.
    Railway injects env vars at runtime, so we check at startup.
    """
    missing = []
    if not BOT_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not DATABASE_URL:
        missing.append("DATABASE_URL")
    if missing:
        logger.critical("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)


async def post_init(app: Application):
    """Initialize database and start scanner on bot startup."""
    global _scanner_task
    init_db()
    _scanner_task = asyncio.create_task(scanner_loop(app))
    _scanner_task.add_done_callback(_scanner_done_callback)
    logger.info("Sniper V3 started")


def _scanner_done_callback(task):
    """Log scanner task exceptions instead of silently swallowing them."""
    if task.cancelled():
        logger.info("Scanner task cancelled")
    elif task.exception():
        logger.critical("Scanner task died with exception: %s", task.exception(),
                        exc_info=task.exception())


async def post_shutdown(app: Application):
    """Cleanup on shutdown: cancel scanner, close DB pool."""
    global _scanner_task
    if _scanner_task and not _scanner_task.done():
        _scanner_task.cancel()
        try:
            await _scanner_task
        except asyncio.CancelledError:
            pass
    close_pool()
    logger.info("Sniper V3 shut down cleanly")


def main():
    _validate_env()
    app = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("settf", settf_command))
    app.add_handler(CommandHandler("sethtf", sethtf_command))
    app.add_handler(CommandHandler("setrisk", setrisk_command))
    app.add_handler(CommandHandler("setbalance", setbalance_command))
    app.add_handler(CommandHandler("setriskpct", setriskpct_command))
    app.add_handler(CommandHandler("touchmode", touchmode_command))
    app.add_handler(CommandHandler("backtest", backtest_command))
    app.add_handler(CommandHandler("journal", journal_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("users", users_command))

    # Text menu handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Lifecycle hooks
    app.post_init = post_init
    app.post_shutdown = post_shutdown

    app.run_polling()


if __name__ == "__main__":
    main()
