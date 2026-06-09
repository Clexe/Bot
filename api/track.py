import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        from config import CRON_SECRET
        if CRON_SECRET:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {CRON_SECRET}":
                self.send_response(401)
                self.end_headers()
                return

        try:
            result = asyncio.run(_run())
            status = 200
        except Exception as e:
            result = {"status": "error", "message": str(e)}
            status = 500

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


async def _run():
    import asyncpg
    from config import DATABASE_URL, DERIV_APP_ID
    from database.db import ServerlessDB
    from feeds.deriv_client import DerivClient
    from feeds.bybit_client import BybitClient
    from signals.tracker import track_open_signals

    conn = await asyncpg.connect(DATABASE_URL)
    db = ServerlessDB(conn)

    try:
        # Skip feed connections entirely when there's nothing to track —
        # this cron fires every minute and usually has no open signals.
        open_count = await db.fetchval(
            "SELECT COUNT(*) FROM signals WHERE status = 'open'"
        )
        if not open_count:
            return {"status": "completed", "open_signals": 0}

        deriv = DerivClient(DERIV_APP_ID)  # connects lazily on first request
        bybit = BybitClient()
        try:
            await track_open_signals(db, deriv, bybit)
            return {"status": "completed", "open_signals": open_count}
        finally:
            await deriv.close()
            await bybit.close()
    except asyncpg.exceptions.UndefinedTableError:
        # First run before /api/setup created the schema — nothing to track.
        return {"status": "completed", "open_signals": 0}
    finally:
        await conn.close()
