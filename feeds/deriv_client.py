import asyncio
import json
import websockets
from config import DERIV_WS_URL
from utils.logger import get_logger

logger = get_logger(__name__)

_REQUEST_TIMEOUT = 15


class DerivClient:
    """Ephemeral Deriv WebSocket client for serverless invocations."""

    def __init__(self, app_id: str):
        self.app_id = app_id
        self.ws = None

    async def connect(self):
        self.ws = await websockets.connect(
            f"{DERIV_WS_URL}?app_id={self.app_id}",
            close_timeout=5,
        )
        logger.info("Connected to Deriv WebSocket")

    async def get_candles(self, symbol: str, granularity: int, count: int = 100) -> list:
        payload = {
            "ticks_history": symbol,
            "style": "candles",
            "granularity": granularity,
            "count": count,
        }
        await self.ws.send(json.dumps(payload))
        raw = await asyncio.wait_for(self.ws.recv(), timeout=_REQUEST_TIMEOUT)
        return json.loads(raw).get("candles", [])

    async def get_current_price(self, symbol: str) -> float | None:
        candles = await self.get_candles(symbol, 60, count=1)
        if candles:
            return float(candles[-1].get("close", 0)) or None
        return None

    async def close(self):
        if self.ws:
            await self.ws.close()
