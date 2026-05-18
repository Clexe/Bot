from datetime import datetime, time as dtime
from utils.logger import get_logger

logger = get_logger(__name__)


def map_key_levels(daily_candles: list, h1_candles: list) -> dict:
    """Map PDH, PDL, PWH, PWL, and Asian session high/low."""
    levels = {}
    completed = _completed_daily(daily_candles)

    if len(completed) >= 1:
        levels["PDH"] = completed[-1]["high"]
        levels["PDL"] = completed[-1]["low"]
    if len(completed) >= 2:
        levels["PPDH"] = completed[-2]["high"]
        levels["PPDL"] = completed[-2]["low"]

    weeks = _group_by_week(completed)
    if len(weeks) >= 2:
        pw = weeks[-2]
        levels["PWH"] = max(c["high"] for c in pw)
        levels["PWL"] = min(c["low"] for c in pw)

    asian = _asian_levels(h1_candles)
    if asian:
        levels["ASIAN_HIGH"] = asian["high"]
        levels["ASIAN_LOW"] = asian["low"]

    return levels


def _completed_daily(daily_candles: list) -> list:
    if not daily_candles:
        return []
    now = datetime.utcnow()
    completed = []
    for c in daily_candles:
        ts = c.get("timestamp", 0)
        dt = datetime.utcfromtimestamp(ts) if isinstance(ts, (int, float)) else ts
        if dt.date() < now.date():
            completed.append(c)
    return completed if completed else daily_candles[:-1]


def _group_by_week(daily_candles: list) -> list:
    weeks = {}
    for c in daily_candles:
        ts = c.get("timestamp", 0)
        dt = datetime.utcfromtimestamp(ts) if isinstance(ts, (int, float)) else ts
        key = dt.isocalendar()[:2]
        weeks.setdefault(key, []).append(c)
    return [weeks[k] for k in sorted(weeks.keys())]


def _asian_levels(h1_candles: list) -> dict:
    """Asian session (00:00-08:00 UTC) high/low from today's H1 candles."""
    now = datetime.utcnow()
    asian = []
    for c in h1_candles:
        ts = c.get("timestamp", 0)
        dt = datetime.utcfromtimestamp(ts) if isinstance(ts, (int, float)) else ts
        if dt.date() == now.date() and dtime(0, 0) <= dt.time() <= dtime(8, 0):
            asian.append(c)

    if not asian:
        for c in h1_candles:
            ts = c.get("timestamp", 0)
            dt = datetime.utcfromtimestamp(ts) if isinstance(ts, (int, float)) else ts
            if dtime(0, 0) <= dt.time() <= dtime(8, 0):
                asian.append(c)
        asian = asian[-8:]

    if not asian:
        return {}
    return {
        "high": max(c["high"] for c in asian),
        "low": min(c["low"] for c in asian),
    }
