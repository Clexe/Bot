from datetime import time

async def detect_htf_rejection(candles, oc_levels):
    """Check body rejection from fresh OC level with displacement away from level."""
    if len(candles) < 2:
        return False
    last = candles[-1]
    displacement = abs(last['close'] - last['open']) > abs(candles[-2]['close'] - candles[-2]['open'])
    for lvl in [l for l in oc_levels if l['freshness_status'] == 'fresh']:
        if lvl['level_type'] == 'resistance' and last['close'] < lvl['level_price'] and last['open'] < lvl['level_price'] and displacement:
            return True
        if lvl['level_type'] == 'support' and last['close'] > lvl['level_price'] and last['open'] > lvl['level_price'] and displacement:
            return True
    return False

async def detect_displacement_and_fvg(candles):
    """Detect FVG and return bool with zone and CE midpoint."""
    if len(candles) < 3:
        return False, None
    i = len(candles) - 2
    c0, c1, c2 = candles[i-1], candles[i], candles[i+1]
    if c2['low'] > c0['high']:
        lo, hi = c0['high'], c2['low']
        return True, {'type': 'bullish', 'low': lo, 'high': hi, 'ce': (lo + hi) / 2}
    if c2['high'] < c0['low']:
        lo, hi = c2['high'], c0['low']
        return True, {'type': 'bearish', 'low': lo, 'high': hi, 'ce': (lo + hi) / 2}
    return False, None

async def detect_liquidity_sweep(candles, session_levels):
    """Detect wick penetration with body close back inside followed by reversal candle."""
    if len(candles) < 4:
        return False
    for i in range(len(candles)-3, len(candles)-1):
        c = candles[i]
        if c['high'] > session_levels['high'] and c['close'] < session_levels['high']:
            return any(x['close'] < x['open'] for x in candles[i+1:i+4])
        if c['low'] < session_levels['low'] and c['close'] > session_levels['low']:
            return any(x['close'] > x['open'] for x in candles[i+1:i+4])
    return False

async def detect_structure_shift(candles):
    """Detect ChoCH/BOS via body break beyond prior swing with displacement evidence."""
    if len(candles) < 5:
        return False, None
    prev_high = max(c['high'] for c in candles[-5:-1])
    prev_low = min(c['low'] for c in candles[-5:-1])
    last = candles[-1]
    if last['close'] > prev_high:
        return True, 'BOS'
    if last['close'] < prev_low:
        return True, 'ChoCH'
    return False, None

async def detect_kill_zone(current_time_utc):
    """Return kill-zone availability and session label."""
    t = current_time_utc.time()
    if time(8,0) <= t <= time(10,0):
        return True, 'London'
    if time(13,0) <= t <= time(15,0):
        return True, 'New York'
    if time(18,0) <= t <= time(20,0):
        return True, 'New York PM'
    return False, None
