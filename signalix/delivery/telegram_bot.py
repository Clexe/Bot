from telegram import Bot

class TelegramDelivery:
    """Telegram sender utility with retry hooks handled by caller."""

    def __init__(self, token: str):
        self.bot = Bot(token=token)

    async def send_message(self, chat_id: int, message: str):
        """Send text message to a telegram chat id."""
        await self.bot.send_message(chat_id=chat_id, text=message)
