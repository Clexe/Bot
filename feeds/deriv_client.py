import asyncio, json, websockets
from config import DERIV_WS_URL, FOREX_PAIRS
from utils.logger import get_logger

logger = get_logger(__name__)


class DerivClient:
    """Deriv websocket client for forex candle streaming and history retrieval."""

    def __init__(self, app_id: str):
        self.app_id = app_id
        self.ws = None

    @property
    def is_connected(self):
        return self.ws is not None and not self.ws.closed

    async def connect(self):
        """Connect with auto-reconnect semantics."""
        while True:
            try:
                self.ws = await websockets.connect(f"{DERIV_WS_URL}?app_id={self.app_id}")
                return
            except Exception:
                await asyncio.sleep(5)

    async def _ensure_connection(self):
        """Reconnect if the websocket is closed."""
        if not self.is_connected:
            logger.warning("Deriv WebSocket disconnected. Reconnecting...")
            await self.connect()

    async def get_history(self, symbol: str, granularity: int = 60, count: int = 500):
        """Fetch historical candles using ticks_history request."""
        await self._ensure_connection()
        try:
            await self.ws.send(json.dumps({"ticks_history": symbol, "style": "candles", "granularity": granularity, "count": count}))
            payload = json.loads(await self.ws.recv())
            return payload.get("candles", [])
        except (websockets.ConnectionClosed, Exception) as e:
            logger.warning("Deriv WebSocket error during get_history(%s): %s. Reconnecting...", symbol, e)
            self.ws = None
            await self._ensure_connection()
            await self.ws.send(json.dumps({"ticks_history": symbol, "style": "candles", "granularity": granularity, "count": count}))
            payload = json.loads(await self.ws.recv())
            return payload.get("candles", [])

    async def subscribe_candles(self):
        """Subscribe to real-time candles for configured forex pairs."""
        for pair in FOREX_PAIRS:
            await self.ws.send(json.dumps({"ticks_history": pair, "style": "candles", "granularity": 60, "subscribe": 1}))

    async def recv(self):
        """Receive next websocket payload with auto-reconnect."""
        while True:
            try:
                return json.loads(await self.ws.recv())
            except (websockets.ConnectionClosed, Exception) as e:
                logger.warning("Deriv WebSocket disconnected: %s. Reconnecting...", e)
                self.ws = None
                await asyncio.sleep(5)
                await self._ensure_connection()
