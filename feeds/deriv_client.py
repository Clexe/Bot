import asyncio
import json
import websockets
from config import DERIV_WS_URL
from utils.logger import get_logger

logger = get_logger(__name__)

_REQUEST_TIMEOUT = 15


class DerivClient:
    def __init__(self, app_id: str):
        self.app_id = app_id
        self.ws = None
        self._lock = asyncio.Lock()

    @property
    def is_connected(self):
        return self.ws is not None and not self.ws.closed

    async def connect(self):
        while True:
            try:
                self.ws = await websockets.connect(
                    f"{DERIV_WS_URL}?app_id={self.app_id}",
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                )
                logger.info("Connected to Deriv WebSocket")
                return
            except Exception as e:
                logger.warning("Deriv connect failed: %s. Retrying in 5s...", e)
                await asyncio.sleep(5)

    async def _ensure_connection(self):
        if not self.is_connected:
            logger.warning("Deriv WebSocket disconnected. Reconnecting...")
            await self.connect()

    async def _request(self, payload: dict) -> dict:
        async with self._lock:
            await self._ensure_connection()
            try:
                await self.ws.send(json.dumps(payload))
                raw = await asyncio.wait_for(self.ws.recv(), timeout=_REQUEST_TIMEOUT)
                return json.loads(raw)
            except (asyncio.TimeoutError, websockets.ConnectionClosed, Exception) as e:
                logger.warning("Deriv request failed (%s): %s", payload.get("ticks_history", "?"), e)
                self.ws = None
                await self._ensure_connection()
                try:
                    await self.ws.send(json.dumps(payload))
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=_REQUEST_TIMEOUT)
                    return json.loads(raw)
                except Exception as e2:
                    logger.error("Deriv retry failed: %s", e2)
                    self.ws = None
                    return {}

    async def get_candles(self, symbol: str, granularity: int, count: int = 100) -> list:
        payload = {
            "ticks_history": symbol,
            "style": "candles",
            "granularity": granularity,
            "count": count,
        }
        result = await self._request(payload)
        return result.get("candles", [])

    async def get_current_price(self, symbol: str) -> float | None:
        candles = await self.get_candles(symbol, 60, count=1)
        if candles:
            return float(candles[-1].get("close", 0)) or None
        return None
