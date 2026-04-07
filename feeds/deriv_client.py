import asyncio, json, websockets
from config import DERIV_WS_URL, FOREX_PAIRS, DERIV_SYMBOL_MAP
from utils.logger import get_logger

logger = get_logger(__name__)

# Timeout for a single send+recv round-trip on the WebSocket.
_REQUEST_TIMEOUT = 15


class DerivClient:
    """Deriv websocket client for forex candle streaming and history retrieval."""

    def __init__(self, app_id: str):
        self.app_id = app_id
        self.ws = None
        self._lock = asyncio.Lock()

    @property
    def is_connected(self):
        return self.ws is not None and not self.ws.closed

    async def connect(self):
        """Connect with auto-reconnect semantics."""
        while True:
            try:
                self.ws = await websockets.connect(
                    f"{DERIV_WS_URL}?app_id={self.app_id}",
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                )
                return
            except Exception:
                await asyncio.sleep(5)

    async def _ensure_connection(self):
        """Reconnect if the websocket is closed."""
        if not self.is_connected:
            logger.warning("Deriv WebSocket disconnected. Reconnecting...")
            await self.connect()

    async def _request(self, payload: dict) -> dict:
        """Send a request and receive the response, serialised by a lock.

        The lock ensures only one coroutine uses the WebSocket at a time,
        preventing the 'cannot call recv while another coroutine is already
        waiting' error that occurs when tracking_job, precision_scan, and
        flow_scan all hit the same connection concurrently.

        A timeout prevents permanent hangs if the server never responds.
        """
        async with self._lock:
            await self._ensure_connection()
            try:
                await self.ws.send(json.dumps(payload))
                raw = await asyncio.wait_for(self.ws.recv(), timeout=_REQUEST_TIMEOUT)
                return json.loads(raw)
            except (asyncio.TimeoutError, websockets.ConnectionClosed, Exception) as e:
                logger.warning("Deriv request failed (%s): %s. Reconnecting...", payload.get("ticks_history", "?"), e)
                self.ws = None
                await self._ensure_connection()
                # One retry after reconnect
                try:
                    await self.ws.send(json.dumps(payload))
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=_REQUEST_TIMEOUT)
                    return json.loads(raw)
                except Exception as e2:
                    logger.error("Deriv request retry failed: %s", e2)
                    self.ws = None
                    return {}

    async def get_history(self, symbol: str, granularity: int = 60, count: int = 500):
        """Fetch historical candles using ticks_history request."""
        payload = {
            "ticks_history": symbol,
            "end": "latest",
            "style": "candles",
            "granularity": granularity,
            "count": count,
        }
        result = await self._request(payload)
        if "error" in result:
            logger.error("Deriv API error for %s (granularity=%s): %s",
                         symbol, granularity, result["error"].get("message", result["error"]))
            return []
        candles = result.get("candles", [])
        if not candles:
            logger.warning("Deriv returned 0 candles for %s granularity=%s", symbol, granularity)
        return candles

    async def subscribe_candles(self):
        """Subscribe to real-time candles for configured forex pairs."""
        for pair in FOREX_PAIRS:
            deriv_sym = DERIV_SYMBOL_MAP.get(pair, pair)
            payload = {
                "ticks_history": deriv_sym,
                "end": "latest",
                "style": "candles",
                "granularity": 60,
                "subscribe": 1,
            }
            await self._request(payload)

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
