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

        try:
            result = asyncio.run(_setup())
            status = 200
        except Exception as e:
            result = {"status": "error", "message": str(e)}
            status = 500

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


async def _setup():
    import asyncpg
    from config import DATABASE_URL, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_WEBHOOK_SECRET
    from database.db import ServerlessDB
    from database.schema import init_schema
    from delivery.telegram import TelegramClient

    result = {}

    # Prefer an explicit production URL — VERCEL_URL points at the
    # deployment-specific URL (e.g. preview), not the stable domain.
    base_url = (
        os.getenv("WEBHOOK_BASE_URL")
        or os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
        or os.getenv("VERCEL_URL", "")
    )

    tg = TelegramClient(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    if base_url:
        base_url = base_url.replace("https://", "").replace("http://", "").rstrip("/")
        webhook_url = f"https://{base_url}/api/webhook"
        result["webhook"] = await tg.set_webhook(webhook_url, secret_token=TELEGRAM_WEBHOOK_SECRET)
        result["webhook_url"] = webhook_url
    else:
        result["webhook"] = {"error": "no base URL found — set WEBHOOK_BASE_URL"}

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        db = ServerlessDB(conn)
        await init_schema(db)
        await conn.close()
        result["database"] = "schema initialized"
    except Exception as e:
        result["database"] = f"error: {e}"

    return result
