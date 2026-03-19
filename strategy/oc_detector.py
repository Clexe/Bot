async def detect_oc_levels(candles, timeframe):
    """Detect MSNR OC levels from body-only A/V pivots and return list for persistence."""
    levels = []
    for i in range(1, len(candles)-1):
        prev, cur, nxt = candles[i-1], candles[i], candles[i+1]
        body_high = max(cur['open'], cur['close'])
        body_low = min(cur['open'], cur['close'])
        if body_high > max(prev['open'], prev['close']) and body_high > max(nxt['open'], nxt['close']):
            levels.append({'timeframe': timeframe, 'level_price': body_high, 'level_type': 'resistance', 'freshness_status': 'fresh'})
        if body_low < min(prev['open'], prev['close']) and body_low < min(nxt['open'], nxt['close']):
            levels.append({'timeframe': timeframe, 'level_price': body_low, 'level_type': 'support', 'freshness_status': 'fresh'})
    return levels
