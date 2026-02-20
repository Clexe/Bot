import json
import asyncio
import websockets
import pandas as pd
from pybit.unified_trading import HTTP
from config import (
    DERIV_TOKEN, DERIV_APP_ID, BYBIT_KEY, BYBIT_SECRET,
    DERIV_SYMBOL_MAP, DERIV_KEYWORDS, DERIV_GRANULARITY, BYBIT_INTERVALS,
    DERIV_CANDLE_COUNT, logger,
)

# Initialize Bybit client with larger connection pool to avoid overflow warnings
bybit = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET, max_retries=2)

# Semaphore to throttle concurrent Bybit REST calls (avoid rate limit 10006)
_bybit_semaphore = asyncio.Semaphore(3)


class DerivSession:
    """Persistent Deriv WebSocket session with request multiplexing.

    Maintains a single WebSocket connection, authenticates once, and
    multiplexes concurrent requests using req_id matching. Reconnects
    automatically on failure.
    """

    def __init__(self, max_concurrent=5):
        self._ws = None
        self._authorized = False
        self._connect_lock = asyncio.Lock()
        self._pending = {}       # req_id -> Future
        self._next_id = 1
        self._reader_task = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def _ensure_connected(self):
        """Connect and authorize if not already connected."""
        if self._ws and self._authorized:
            return
        async with self._connect_lock:
            # Double-check after acquiring lock
            if self._ws and self._authorized:
                return
            await self._connect()

    async def _connect(self):
        """Open WebSocket and authorize."""
        # Clean up old connection
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        self._authorized = False
        self._ws = None

        uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        self._ws = await websockets.connect(uri, close_timeout=10)

        # Authorize
        await self._ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        for _ in range(5):
            msg = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))
            if "authorize" in msg:
                self._authorized = True
                break
            if "error" in msg:
                raise ConnectionError(f"Deriv auth error: {msg['error']}")
        if not self._authorized:
            raise ConnectionError("Deriv auth timeout")

        # Start background reader
        self._reader_task = asyncio.create_task(self._reader())
        logger.info("Deriv WebSocket session connected")

    async def _reader(self):
        """Background task: read responses and dispatch to pending futures."""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                req_id = msg.get("req_id")
                if req_id and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
        except (websockets.exceptions.ConnectionClosed, Exception):
            pass
        finally:
            # Connection lost — fail all pending requests
            self._authorized = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("WebSocket closed"))
            self._pending.clear()

    async def request(self, payload, timeout=15):
        """Send a request and wait for the matching response."""
        async with self._semaphore:
            # Ensure connected (reconnect if needed)
            try:
                await self._ensure_connected()
            except Exception as e:
                raise ConnectionError(f"Deriv connect failed: {e}")

            req_id = self._next_id
            self._next_id += 1
            payload["req_id"] = req_id

            loop = asyncio.get_event_loop()
            future = loop.create_future()
            self._pending[req_id] = future

            try:
                await self._ws.send(json.dumps(payload))
                return await asyncio.wait_for(future, timeout=timeout)
            except (websockets.exceptions.ConnectionClosed, ConnectionError):
                # Connection died mid-request — force reconnect on next call
                self._authorized = False
                raise
            finally:
                self._pending.pop(req_id, None)

    async def close(self):
        """Shut down the session."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._authorized = False


# Global session instance
_deriv_session = DerivSession(max_concurrent=5)


def is_deriv_pair(clean_pair):
    """Determine if a symbol should be fetched from Deriv.

    USDT-suffixed pairs always go to Bybit regardless of currency
    keywords (e.g. EURUSDT contains 'EUR' but is a Bybit crypto pair).
    """
    if clean_pair.endswith("USDT"):
        return False
    return any(x in clean_pair for x in DERIV_KEYWORDS)


async def fetch_data(pair, interval):
    """Fetch OHLC data from the appropriate exchange.

    Args:
        pair: Symbol name (e.g. 'XAUUSD')
        interval: Timeframe string (e.g. 'M15', '1D')

    Returns:
        DataFrame with 'open', 'high', 'low', 'close' columns, or empty DataFrame
    """
    raw_pair = pair.replace("/", "").strip().upper()
    if is_deriv_pair(raw_pair):
        return await _fetch_deriv(raw_pair, interval)
    else:
        async with _bybit_semaphore:
            return await asyncio.to_thread(_fetch_bybit, raw_pair, interval)


async def fetch_data_parallel(pairs, interval):
    """Fetch data for multiple pairs in parallel.

    Args:
        pairs: List of symbol names
        interval: Timeframe string

    Returns:
        Dict mapping pair -> DataFrame
    """
    tasks = {pair: fetch_data(pair, interval) for pair in pairs}
    results = {}
    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for pair, result in zip(tasks.keys(), gathered):
        if isinstance(result, Exception):
            logger.error("Parallel fetch error for %s: %s", pair, result)
            results[pair] = pd.DataFrame()
        else:
            results[pair] = result
    return results


async def _fetch_deriv(clean_pair, interval):
    """Fetch candle data from Deriv via shared WebSocket session."""
    mapped = DERIV_SYMBOL_MAP.get(clean_pair, clean_pair)
    gran = DERIV_GRANULARITY.get(interval, 900)
    try:
        res = await _deriv_session.request({
            "ticks_history": mapped,
            "adjust_start_time": 1,
            "count": DERIV_CANDLE_COUNT,
            "end": "latest",
            "style": "candles",
            "granularity": gran,
        }, timeout=15)
        if not res.get("candles"):
            if "error" in res:
                logger.warning("Deriv candles error for %s: %s", clean_pair, res["error"])
            return pd.DataFrame()
        df = pd.DataFrame(res["candles"])
        return df[['open', 'high', 'low', 'close']].apply(pd.to_numeric)
    except asyncio.TimeoutError:
        logger.warning("Deriv WebSocket timeout for %s", clean_pair)
        return pd.DataFrame()
    except Exception as e:
        logger.error("Deriv fetch error for %s: %s", clean_pair, e)
        return pd.DataFrame()


def _bybit_category(symbol):
    """Return the correct Bybit category for a symbol.

    USDT pairs are 'linear' (perpetual), bare USD pairs are 'inverse'.
    """
    if symbol.endswith("USDT"):
        return "linear"
    return "inverse"


def _fetch_bybit(clean_pair, interval):
    """Fetch candle data from Bybit REST API."""
    try:
        tf = BYBIT_INTERVALS.get(interval, "15")
        category = _bybit_category(clean_pair)
        resp = bybit.get_kline(category=category, symbol=clean_pair, interval=tf, limit=100)
        if not resp or 'result' not in resp or not resp['result'].get('list'):
            logger.warning("Bybit empty response for %s", clean_pair)
            return pd.DataFrame()
        df = pd.DataFrame(
            resp['result']['list'],
            columns=['ts', 'open', 'high', 'low', 'close', 'vol', 'turnover']
        )
        df = df[['open', 'high', 'low', 'close']].apply(pd.to_numeric)
        return df.iloc[::-1].reset_index(drop=True)
    except Exception as e:
        logger.error("Bybit fetch error for %s: %s", clean_pair, e)
        return pd.DataFrame()


async def fetch_current_price(pair):
    """Fetch the current market price for a pair.

    Used by the outcome checker to evaluate open signals.
    """
    raw_pair = pair.replace("/", "").strip().upper()
    if is_deriv_pair(raw_pair):
        return await _get_deriv_price(raw_pair)
    else:
        async with _bybit_semaphore:
            return await asyncio.to_thread(_get_bybit_price, raw_pair)


async def _get_deriv_price(clean_pair):
    """Get current tick price from Deriv via shared WebSocket session."""
    mapped = DERIV_SYMBOL_MAP.get(clean_pair, clean_pair)
    try:
        res = await _deriv_session.request({
            "ticks": mapped,
        }, timeout=10)
        if "tick" in res:
            return float(res["tick"]["quote"])
        logger.warning("Deriv tick response missing 'tick' for %s: %s", clean_pair, res.get("error", res))
        return None
    except Exception as e:
        logger.error("Deriv price fetch error for %s: %s", clean_pair, e)
        return None


def _get_bybit_price(clean_pair):
    """Get current ticker price from Bybit."""
    try:
        category = _bybit_category(clean_pair)
        resp = bybit.get_tickers(category=category, symbol=clean_pair)
        if resp and 'result' in resp and resp['result'].get('list'):
            return float(resp['result']['list'][0]['lastPrice'])
        return None
    except Exception as e:
        logger.error("Bybit price fetch error for %s: %s", clean_pair, e)
        return None
