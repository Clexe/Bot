import asyncio, aiohttp, json, websockets
from config import BYBIT_REST_URL, BYBIT_WS_URL, CRYPTO_PAIRS, TF_MAP_BYBIT

class BybitClient:
    """Bybit V5 REST and websocket market data client."""

    def __init__(self):
        self.ws = None

    async def get_kline(self, symbol: str, timeframe: str, limit: int = 500):
        """Fetch historical kline data from Bybit REST."""
        params = {"category": "linear", "symbol": symbol, "interval": TF_MAP_BYBIT[timeframe], "limit": str(limit)}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BYBIT_REST_URL}/v5/market/kline", params=params) as r:
                return await r.json()

    async def connect(self):
        """Connect websocket and subscribe to M1 klines."""
        while True:
            try:
                self.ws = await websockets.connect(BYBIT_WS_URL)
                await self.ws.send(json.dumps({"op": "subscribe", "args": [f"kline.1.{p}" for p in CRYPTO_PAIRS]}))
                return
            except Exception:
                await asyncio.sleep(5)

    async def recv(self):
        """Receive next bybit websocket payload."""
        return json.loads(await self.ws.recv())
