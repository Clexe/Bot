from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ALL_PAIRS, PAIR_DISPLAY
from utils.logger import get_logger

logger = get_logger(__name__)


class TelegramBot:
    def __init__(self):
        self.app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.bot = self.app.bot
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("pairs", self._cmd_pairs))

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Signalix Trading Bot\n\n"
            "/status - Bot status\n"
            "/pairs - Active pairs"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Bot is running.")

    async def _cmd_pairs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        pairs = [PAIR_DISPLAY.get(p, p) for p in ALL_PAIRS]
        await update.message.reply_text("Active pairs:\n" + "\n".join(f"  {p}" for p in pairs))

    async def send_signal(self, message: str):
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Telegram send failed: %s", e)

    async def start_polling(self):
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot polling started")

    async def stop(self):
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception:
            pass
