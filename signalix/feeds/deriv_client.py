import asyncio, json, websockets
from signalix.config import DERIV_WS_URL, FOREX_PAIRS

class DerivClient:
    """Deriv websocket client for forex candle streaming and history retrieval."""

    def __init__(self, app_id: str):
        self.app_id = app_id
        self.ws = None

    async def connect(self):
        """Connect with auto-reconnect semantics."""
        while True:
            try:
                self.ws = await websockets.connect(f"{DERIV_WS_URL}?app_id={self.app_id}")
                return
            except Exception:
                await asyncio.sleep(5)

    async def get_history(self, symbol: str, granularity: int = 60, count: int = 500):
        """Fetch historical candles using ticks_history request."""
        await self.ws.send(json.dumps({"ticks_history": symbol, "style": "candles", "granularity": granularity, "count": count}))
        payload = json.loads(await self.ws.recv())
        return payload.get("candles", [])

    async def subscribe_candles(self):
        """Subscribe to real-time candles for configured forex pairs."""
        for pair in FOREX_PAIRS:
            await self.ws.send(json.dumps({"ticks_history": pair, "style": "candles", "granularity": 60, "subscribe": 1}))

    async def recv(self):
        """Receive next websocket payload."""
        return json.loads(await self.ws.recv())
