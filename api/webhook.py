import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        from config import TELEGRAM_WEBHOOK_SECRET

        # Telegram echoes back the secret_token registered via setWebhook,
        # so unauthenticated POSTs to this endpoint are rejected.
        if TELEGRAM_WEBHOOK_SECRET:
            token = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if token != TELEGRAM_WEBHOOK_SECRET:
                self.send_response(403)
                self.end_headers()
                return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            update = json.loads(body)
            asyncio.run(_handle_update(update))
        except Exception:
            # Always ACK so Telegram doesn't retry a poison update forever.
            pass

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())


async def _handle_update(update):
    message = update.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")
    if not chat_id or not text:
        return

    from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ALL_PAIRS, PAIR_DISPLAY
    from delivery.telegram import TelegramClient

    tg = TelegramClient(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

    command = text.split("@")[0].strip()

    if command == "/start":
        await tg.send_message(
            chat_id,
            "Signalix Trading Bot\n\n/status - Bot status\n/pairs - Active pairs",
        )
    elif command == "/status":
        await tg.send_message(chat_id, "Bot is running on Vercel.")
    elif command == "/pairs":
        pairs = [PAIR_DISPLAY.get(p, p) for p in ALL_PAIRS]
        await tg.send_message(chat_id, "Active pairs:\n" + "\n".join(f"  {p}" for p in pairs))
