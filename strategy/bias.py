from datetime import datetime
from utils.logger import get_logger

logger = get_logger(__name__)


def compute_daily_bias(daily_candles: list) -> dict:
    """Daily bias via BOS on previous day candles.

    Bullish if PDC > PPDH  (Break of Structure up)
    Bearish if PDC < PPDL  (Break of Structure down)
    Bullish if PDL < PPDL & PDC > PPDL  (sweep + reclaim)
    Bearish if PDH > PPDH & PDC < PPDH  (sweep + reject)
    """
    completed = _completed_daily(daily_candles)
    if len(completed) < 2:
        return {"bias": "NEUTRAL", "reason": "insufficient data"}

    pd = completed[-1]
    ppd = completed[-2]

    pdc = pd["close"]
    pdh = pd["high"]
    pdl = pd["low"]
    ppdh = ppd["high"]
    ppdl = ppd["low"]

    if pdc > ppdh:
        return {"bias": "BULLISH", "reason": "PDC > PPDH (BOS up)", "pdh": pdh, "pdl": pdl}
    if pdc < ppdl:
        return {"bias": "BEARISH", "reason": "PDC < PPDL (BOS down)", "pdh": pdh, "pdl": pdl}
    if pdl < ppdl and pdc > ppdl:
        return {"bias": "BULLISH", "reason": "PDL swept PPDL, PDC reclaimed", "pdh": pdh, "pdl": pdl}
    if pdh > ppdh and pdc < ppdh:
        return {"bias": "BEARISH", "reason": "PDH swept PPDH, PDC rejected", "pdh": pdh, "pdl": pdl}

    return {"bias": "NEUTRAL", "reason": "no clear daily BOS", "pdh": pdh, "pdl": pdl}


def compute_weekly_bias(daily_candles: list) -> dict:
    """Weekly bias built from daily candles grouped by ISO week."""
    completed = _completed_daily(daily_candles)
    if len(completed) < 10:
        return {"bias": "NEUTRAL", "reason": "insufficient data for weekly"}

    weeks = {}
    for c in completed:
        ts = c.get("timestamp", 0)
        dt = datetime.utcfromtimestamp(ts) if isinstance(ts, (int, float)) else ts
        key = dt.isocalendar()[:2]
        if key not in weeks:
            weeks[key] = {"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]}
        else:
            w = weeks[key]
            w["high"] = max(w["high"], c["high"])
            w["low"] = min(w["low"], c["low"])
            w["close"] = c["close"]

    sorted_weeks = [weeks[k] for k in sorted(weeks.keys())]
    if len(sorted_weeks) < 3:
        return {"bias": "NEUTRAL", "reason": "insufficient weekly bars"}

    pw = sorted_weeks[-2]
    ppw = sorted_weeks[-3]

    pwc = pw["close"]
    pwh = pw["high"]
    pwl = pw["low"]
    ppwh = ppw["high"]
    ppwl = ppw["low"]

    if pwc > ppwh:
        return {"bias": "BULLISH", "reason": "PWC > PPWH (weekly BOS up)", "pwh": pwh, "pwl": pwl}
    if pwc < ppwl:
        return {"bias": "BEARISH", "reason": "PWC < PPWL (weekly BOS down)", "pwh": pwh, "pwl": pwl}
    if pwl < ppwl and pwc > ppwl:
        return {"bias": "BULLISH", "reason": "PWL swept PPWL, PWC reclaimed", "pwh": pwh, "pwl": pwl}
    if pwh > ppwh and pwc < ppwh:
        return {"bias": "BEARISH", "reason": "PWH swept PPWH, PWC rejected", "pwh": pwh, "pwl": pwl}

    return {"bias": "NEUTRAL", "reason": "no clear weekly BOS", "pwh": pwh, "pwl": pwl}


def get_htf_bias(daily_candles: list) -> dict:
    """Final HTF bias — weekly overrides daily."""
    daily = compute_daily_bias(daily_candles)
    weekly = compute_weekly_bias(daily_candles)

    if weekly["bias"] != "NEUTRAL":
        return {
            "bias": weekly["bias"],
            "source": "weekly",
            "reason": weekly["reason"],
            "daily_bias": daily["bias"],
            "daily_reason": daily["reason"],
            "pdh": daily.get("pdh"),
            "pdl": daily.get("pdl"),
            "pwh": weekly.get("pwh"),
            "pwl": weekly.get("pwl"),
        }

    return {
        "bias": daily["bias"],
        "source": "daily",
        "reason": daily["reason"],
        "daily_bias": daily["bias"],
        "daily_reason": daily["reason"],
        "pdh": daily.get("pdh"),
        "pdl": daily.get("pdl"),
        "pwh": weekly.get("pwh"),
        "pwl": weekly.get("pwl"),
    }


def _completed_daily(daily_candles: list) -> list:
    """Return only completed daily candles (exclude today's partial bar)."""
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
