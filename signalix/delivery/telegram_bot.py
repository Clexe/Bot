from telegram import Bot
from signalix.config import PRECISION_TIER_RULES, FLOW_TIER_RULES
from signalix.signals.formatter import format_precision_signal, format_flow_signal
from signalix.delivery.scheduler import queue_signal_for_delivery
from signalix.utils.logger import get_logger

logger = get_logger(__name__)


class TelegramDelivery:
    """Telegram sender with dual-engine tier-based delivery."""

    def __init__(self, token: str):
        self.bot = Bot(token=token)

    async def send_message(self, chat_id: int, message: str):
        """Send text message to a telegram chat id."""
        try:
            await self.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.error("Failed to send message to %s: %s", chat_id, e)

    async def deliver_signal(self, db, signal: dict):
        """Deliver signal to all eligible users based on engine type and tier rules."""
        signal_type = signal.get("signal_type", "precision")
        score = signal.get("score", 0)

        users = await db.fetch("SELECT telegram_chat_id, tier FROM users WHERE is_active=true")

        for user in users:
            tier = user["tier"]
            chat_id = user["telegram_chat_id"]

            if signal_type == "precision":
                rules = PRECISION_TIER_RULES.get(tier)
                if not rules:
                    continue
                if score < rules["min_score"]:
                    continue

                message = format_precision_signal(signal, tier)
                delay = rules.get("delay_minutes", 0)

                if delay > 0:
                    signal_id = signal.get("id")
                    if signal_id:
                        await queue_signal_for_delivery(db, signal_id, chat_id, message, delay)
                else:
                    await self.send_message(chat_id, message)

            elif signal_type == "flow":
                # Free tier never receives Flow signals
                if tier == "free":
                    continue

                rules = FLOW_TIER_RULES.get(tier)
                if not rules:
                    continue
                if score < rules["min_score"]:
                    continue

                message = format_flow_signal(signal, tier)
                await self.send_message(chat_id, message)
