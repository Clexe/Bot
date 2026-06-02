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

        result = asyncio.run(_run())

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


async def _run():
    import asyncpg
    from config import DATABASE_URL, DERIV_APP_ID
    from database.db import ServerlessDB
    from database.schema import init_schema
    from feeds.deriv_client import DerivClient
    from feeds.bybit_client import BybitClient
    from signals.tracker import track_open_signals

    conn = await asyncpg.connect(DATABASE_URL)
    db = ServerlessDB(conn)
    deriv = DerivClient(DERIV_APP_ID)
    await deriv.connect()
    bybit = BybitClient()

    try:
        await init_schema(db)
        await track_open_signals(db, deriv, bybit)
        return {"status": "completed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        await deriv.close()
        await bybit.close()
        await conn.close()
