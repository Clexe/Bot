import json
import asyncio
import websockets
import pandas as pd
from pybit.unified_trading import HTTP
from config import (
    DERIV_TOKEN, DERIV_APP_ID, BYBIT_KEY, BYBIT_SECRET,
    DERIV_SYMBOL_MAP, DERIV_KEYWORDS, DERIV_GRANULARITY, BYBIT_INTERVALS,
    logger,
)

# Initialize Bybit client
bybit = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)


def is_deriv_pair(clean_pair):
    """Determine if a symbol should be fetched from Deriv."""
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
        return _fetch_bybit(raw_pair, interval)


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
    """Fetch candle data from Deriv WebSocket API."""
    mapped = DERIV_SYMBOL_MAP.get(clean_pair, clean_pair)
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    gran = DERIV_GRANULARITY.get(interval, 900)
    try:
        async with websockets.connect(uri, close_timeout=10) as ws:
            # Authorize
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            auth_res = None
            for _ in range(5):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if "authorize" in msg:
                    auth_res = msg
                    break
                if "error" in msg:
                    logger.warning("Deriv auth error for %s: %s", clean_pair, msg["error"])
                    return pd.DataFrame()
            if auth_res is None:
                logger.warning("Deriv auth timeout for %s", clean_pair)
                return pd.DataFrame()

            # Fetch candles
            await ws.send(json.dumps({
                "ticks_history": mapped,
                "adjust_start_time": 1,
                "count": 100,
                "end": "latest",
                "style": "candles",
                "granularity": gran,
            }))
            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
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


def _fetch_bybit(clean_pair, interval):
    """Fetch candle data from Bybit REST API."""
    try:
        tf = BYBIT_INTERVALS.get(interval, "15")
        resp = bybit.get_kline(category="linear", symbol=clean_pair, interval=tf, limit=100)
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
        return _get_bybit_price(raw_pair)


async def _get_deriv_price(clean_pair):
    """Get current tick price from Deriv."""
    mapped = DERIV_SYMBOL_MAP.get(clean_pair, clean_pair)
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(uri, close_timeout=10) as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            for _ in range(5):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if "authorize" in msg:
                    break
                if "error" in msg:
                    return None
            else:
                return None

            await ws.send(json.dumps({"ticks": mapped, "subscribe": 0}))
            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if "tick" in res:
                return float(res["tick"]["quote"])
            return None
    except Exception as e:
        logger.error("Deriv price fetch error for %s: %s", clean_pair, e)
        return None


def _get_bybit_price(clean_pair):
    """Get current ticker price from Bybit."""
    try:
        resp = bybit.get_tickers(category="linear", symbol=clean_pair)
        if resp and 'result' in resp and resp['result'].get('list'):
            return float(resp['result']['list'][0]['lastPrice'])
        return None
    except Exception as e:
        logger.error("Bybit price fetch error for %s: %s", clean_pair, e)
        return None
