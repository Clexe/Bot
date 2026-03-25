import asyncio
import json
import aiohttp
import websockets
from config import BYBIT_REST_URL, BYBIT_WS_URL, CRYPTO_PAIRS, TF_MAP_BYBIT, CANDLE_REQUIREMENTS
from utils.logger import get_logger

logger = get_logger(__name__)


class BybitClient:
    """Bybit V5 REST and WebSocket client for BTCUSDT and ETHUSDT perpetual futures."""

    def __init__(self):
        self.ws = None
        self._connected = False

    async def get_kline(self, symbol: str, timeframe: str, limit: int = None) -> dict:
        """Fetch historical candle data via GET /v5/market/kline.

        Enforces candle requirements from config if limit not specified.
        """
        if limit is None:
            tf_label = _tf_to_label(timeframe)
            limit = CANDLE_REQUIREMENTS.get(tf_label, 100)

        interval = TF_MAP_BYBIT.get(timeframe)
        if not interval:
            logger.error("Unknown timeframe for Bybit: %s", timeframe)
            return {"result": {"list": []}}

        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": str(min(limit, 1000)),
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BYBIT_REST_URL}/v5/market/kline",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    r.raise_for_status()
                    return await r.json()
        except Exception as e:
            logger.error("Bybit kline fetch failed for %s %s: %s", symbol, timeframe, e)
            return {"result": {"list": []}}

    async def connect(self):
        """Connect WebSocket and subscribe to kline streams with auto-reconnect."""
        while True:
            try:
                self.ws = await websockets.connect(BYBIT_WS_URL)
                subs = []
                for pair in CRYPTO_PAIRS:
                    subs.append(f"kline.1.{pair}")
                    subs.append(f"kline.5.{pair}")
                    subs.append(f"kline.15.{pair}")

                await self.ws.send(json.dumps({"op": "subscribe", "args": subs}))
                self._connected = True
                logger.info("Bybit WebSocket connected")
                return
            except Exception as e:
                logger.error("Bybit WebSocket connection failed: %s", e)
                self._connected = False
                await asyncio.sleep(5)

    async def recv(self) -> dict:
        """Receive next WebSocket payload with auto-reconnect on disconnect."""
        while True:
            try:
                data = await self.ws.recv()
                return json.loads(data)
            except (websockets.ConnectionClosed, Exception) as e:
                logger.warning("Bybit WebSocket disconnected: %s. Reconnecting...", e)
                self._connected = False
                await asyncio.sleep(5)
                await self.connect()

    @property
    def connected(self) -> bool:
        return self._connected and self.ws is not None


def _tf_to_label(tf: str) -> str:
    """Map timeframe code to candle requirements key."""
    mapping = {
        "M": "Monthly", "W": "Weekly", "D": "Daily",
        "H4": "H4", "H1": "H1", "M15": "M15", "M5": "M5",
    }
    return mapping.get(tf, tf)
