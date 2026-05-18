import aiohttp
from utils.logger import get_logger

logger = get_logger(__name__)

BYBIT_BASE = "https://api.bybit.com"


class BybitClient:
    def __init__(self):
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_kline(self, symbol: str, interval: str, limit: int = 100) -> dict:
        session = await self._get_session()
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        try:
            async with session.get(f"{BYBIT_BASE}/v5/market/kline", params=params) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("Bybit kline fetch failed for %s %s: %s", symbol, interval, e)
            return {}

    async def get_ticker(self, symbol: str) -> dict:
        session = await self._get_session()
        params = {"category": "linear", "symbol": symbol}
        try:
            async with session.get(f"{BYBIT_BASE}/v5/market/tickers", params=params) as resp:
                data = await resp.json()
                tickers = data.get("result", {}).get("list", [])
                return tickers[0] if tickers else {}
        except Exception as e:
            logger.error("Bybit ticker fetch failed for %s: %s", symbol, e)
            return {}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
