import aiohttp
from utils.logger import get_logger

logger = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class TelegramClient:
    """Lightweight Telegram Bot API client for serverless use."""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    async def send_signal(self, message: str):
        await self.send_message(self.chat_id, message, parse_mode="HTML")

    async def send_message(self, chat_id, text: str, parse_mode: str = None):
        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error("Telegram send failed: %s", await resp.text())
        except Exception as e:
            logger.error("Telegram send error: %s", e)

    async def set_webhook(self, webhook_url: str, secret_token: str = "") -> dict:
        url = f"{TELEGRAM_API}/bot{self.token}/setWebhook"
        payload = {"url": webhook_url}
        if secret_token:
            payload["secret_token"] = secret_token
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                return await resp.json()
