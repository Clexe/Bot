import asyncio
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from config import BOT_TOKEN, logger
from database import init_db
from handlers import (
    start_command, mode_command, settf_command, sethtf_command, setrisk_command,
    broadcast_command, users_command, handle_text,
)
from scanner import scanner_loop


async def post_init(app: Application):
    """Initialize database and start scanner on bot startup."""
    init_db()
    asyncio.create_task(scanner_loop(app))
    logger.info("Sniper V3 started")


async def post_shutdown(app: Application):
    """Cleanup on shutdown."""
    logger.info("Sniper V3 shutting down")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("settf", settf_command))
    app.add_handler(CommandHandler("sethtf", sethtf_command))
    app.add_handler(CommandHandler("setrisk", setrisk_command))
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
