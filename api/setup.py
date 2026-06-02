import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(BaseHTTPRequestHandler):
    """One-time setup: register Telegram webhook and initialize DB schema."""

    def do_GET(self):
        from config import CRON_SECRET
        if CRON_SECRET:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {CRON_SECRET}":
                self.send_response(401)
                self.end_headers()
                return

        result = asyncio.run(_setup())

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


async def _setup():
    import asyncpg
    from config import DATABASE_URL, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
    from database.db import ServerlessDB
    from database.schema import init_schema
    from delivery.telegram import TelegramClient

    result = {}

    # Telegram webhook
    vercel_url = os.getenv("VERCEL_URL", "")
    tg = TelegramClient(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    if vercel_url:
        webhook_url = f"https://{vercel_url}/api/webhook"
        result["webhook"] = await tg.set_webhook(webhook_url)
    else:
        result["webhook"] = {"error": "VERCEL_URL not set"}

    # Database schema
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        db = ServerlessDB(conn)
        await init_schema(db)
        await conn.close()
        result["database"] = "schema initialized"
    except Exception as e:
        result["database"] = f"error: {e}"

    return result
