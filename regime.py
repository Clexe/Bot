"""Market regime detection — ATR volatility + trend strength + killzone awareness.

Classifies the current market into one of:
  TRENDING_BULL / TRENDING_BEAR / RANGING / VOLATILE

The scanner uses this to:
  - Skip signals in VOLATILE (chop) conditions
  - Adjust stop-loss padding in high-volatility regimes
  - Provide regime context in signal messages
  - Weight signal quality by session killzone
"""

import pandas as pd
from datetime import datetime, timezone
from config import logger


def compute_atr(df, period=14):
    """Compute Average True Range over *period* candles.

    Returns the latest ATR value, or None if insufficient data.
    """
    if len(df) < period + 1:
        return None

    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    tr_values = []
    for i in range(1, len(df)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_values.append(tr)

    if len(tr_values) < period:
        return None

    # Wilder's smoothed ATR
    atr = sum(tr_values[:period]) / period
    for tr in tr_values[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


def compute_atr_ratio(df, fast=7, slow=28):
    """ATR ratio: fast ATR / slow ATR.

    > 1.5 → expanding volatility (volatile/breakout)
    < 0.7 → contracting volatility (compression/ranging)
    """
    atr_fast = compute_atr(df, fast)
    atr_slow = compute_atr(df, slow)
    if atr_fast is None or atr_slow is None or atr_slow <= 0:
        return 1.0
    return atr_fast / atr_slow


def compute_trend_strength(df, lookback=20):
    """Simple trend strength measure using directional price movement.

    Returns a value between -1.0 (strong bear) and +1.0 (strong bull).
    Near 0 = ranging/directionless.

    Uses the ratio of net price movement to total candle-by-candle movement.
    This is effectively an Efficiency Ratio (Kaufman).
    """
    if len(df) < lookback + 1:
        return 0.0

    closes = df['close'].values
    recent = closes[-lookback:]

    net_move = abs(recent[-1] - recent[0])
    total_move = sum(abs(recent[i] - recent[i - 1]) for i in range(1, len(recent)))

    if total_move <= 0:
        return 0.0

    efficiency = net_move / total_move  # 0 to 1

    # Add direction
    if recent[-1] > recent[0]:
        return efficiency
    return -efficiency


def detect_regime(df, atr_period=14, trend_lookback=20):
    """Detect the current market regime.

    Returns dict:
        regime: 'TRENDING_BULL' | 'TRENDING_BEAR' | 'RANGING' | 'VOLATILE'
        atr: current ATR value
        atr_ratio: fast/slow ATR ratio (volatility expansion/contraction)
        trend_strength: -1.0 to +1.0 efficiency ratio
        sl_multiplier: multiplier for stop-loss distance (wider in volatile)
    """
    atr = compute_atr(df, atr_period)
    atr_ratio = compute_atr_ratio(df)
    trend = compute_trend_strength(df, trend_lookback)

    if atr is None:
        return {
            "regime": "UNKNOWN", "atr": 0, "atr_ratio": 1.0,
            "trend_strength": 0, "sl_multiplier": 1.0,
        }

    abs_trend = abs(trend)

    # Classify
    if atr_ratio > 1.8 and abs_trend < 0.3:
        # High volatility but no direction = chop
        regime = "VOLATILE"
        sl_mult = 1.3  # wider stops
    elif abs_trend >= 0.35:
        # Strong directional movement
        regime = "TRENDING_BULL" if trend > 0 else "TRENDING_BEAR"
        sl_mult = 1.0
    elif atr_ratio < 0.75:
        # Low volatility, no trend = tight range
        regime = "RANGING"
        sl_mult = 0.85  # tighter stops OK in compression
    else:
        # Moderate — could go either way, treat as neutral trending
        if abs_trend >= 0.2:
            regime = "TRENDING_BULL" if trend > 0 else "TRENDING_BEAR"
        else:
            regime = "RANGING"
        sl_mult = 1.0

    return {
        "regime": regime,
        "atr": round(atr, 6),
        "atr_ratio": round(atr_ratio, 2),
        "trend_strength": round(trend, 3),
        "sl_multiplier": sl_mult,
    }


def should_skip_regime(regime_info, signal_direction, always_open=False):
    """Decide whether to skip a trade based on regime.

    Returns (skip: bool, reason: str).

    Rules:
      - VOLATILE regime: skip trades (chop = death by a thousand cuts)
        Exception: always_open pairs (crypto/synthetics) are inherently
        volatile, so VOLATILE regime alone should not block them.
      - TRENDING_BULL + SELL signal: skip (counter-trend)
      - TRENDING_BEAR + BUY signal: skip (counter-trend)
      - RANGING: allow (zone trading works well in ranges)
    """
    regime = regime_info.get("regime", "UNKNOWN")

    if regime == "VOLATILE" and not always_open:
        return True, "volatile_chop"

    if regime == "TRENDING_BULL" and signal_direction == "SELL":
        # Allow only if trend is weak-ish (efficiency < 0.5)
        if abs(regime_info.get("trend_strength", 0)) > 0.5:
            return True, "counter_trend"

    if regime == "TRENDING_BEAR" and signal_direction == "BUY":
        if abs(regime_info.get("trend_strength", 0)) > 0.5:
            return True, "counter_trend"

    return False, ""


def detect_killzone():
    """Detect if current time falls within a high-probability killzone.

    Killzones are periods of peak institutional activity:
      - London Open: 07:00-10:00 UTC (highest forex volume)
      - NY Open: 12:00-15:00 UTC (highest overlap volume)
      - London Close: 15:00-17:00 UTC (rebalancing flows)
      - Asian Session: 23:00-02:00 UTC (lower volume, ranging)

    Returns dict with:
      killzone: name of current killzone or 'OFF_PEAK'
      quality_mult: multiplier for signal quality (1.0=normal, 1.2=killzone)
    """
    now = datetime.now(timezone.utc)
    hour = now.hour

    if 7 <= hour < 10:
        return {"killzone": "LONDON_OPEN", "quality_mult": 1.2}
    if 12 <= hour < 15:
        return {"killzone": "NY_OPEN", "quality_mult": 1.2}
    if 15 <= hour < 17:
        return {"killzone": "LONDON_CLOSE", "quality_mult": 1.1}
    if hour >= 23 or hour < 2:
        return {"killzone": "ASIAN", "quality_mult": 0.8}

    return {"killzone": "OFF_PEAK", "quality_mult": 0.9}


def compute_adx(df, period=14):
    """Compute Average Directional Index for trend strength measurement.

    ADX > 25 = trending, ADX < 20 = ranging, ADX > 50 = very strong trend.
    More reliable than simple efficiency ratio for distinguishing
    trending vs ranging conditions.

    Returns ADX value or None if insufficient data.
    """
    if len(df) < period * 2 + 1:
        return None

    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values

    # True Range, +DM, -DM
    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, len(df)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return None

    # Wilder's smoothing for ATR, +DI, -DI
    atr_s = sum(tr_list[:period]) / period
    plus_dm_s = sum(plus_dm_list[:period]) / period
    minus_dm_s = sum(minus_dm_list[:period]) / period

    dx_values = []

    for i in range(period, len(tr_list)):
        atr_s = (atr_s * (period - 1) + tr_list[i]) / period
        plus_dm_s = (plus_dm_s * (period - 1) + plus_dm_list[i]) / period
        minus_dm_s = (minus_dm_s * (period - 1) + minus_dm_list[i]) / period

        if atr_s <= 0:
            continue

        plus_di = 100 * plus_dm_s / atr_s
        minus_di = 100 * minus_dm_s / atr_s

        di_sum = plus_di + minus_di
        if di_sum <= 0:
            continue

        dx = 100 * abs(plus_di - minus_di) / di_sum
        dx_values.append(dx)

    if len(dx_values) < period:
        return None

    # ADX = Wilder's smoothed DX
    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period

    return round(adx, 2)
