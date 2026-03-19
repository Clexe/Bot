async def detect_order_blocks(candles, timeframe: str):
    """Detect bullish and bearish order blocks preceding structure-breaking impulses."""
    obs = []
    for i in range(1, len(candles)-1):
        prev, cur, nxt = candles[i-1], candles[i], candles[i+1]
        if cur['close'] < cur['open'] and nxt['close'] > nxt['open'] and nxt['close'] > prev['high']:
            obs.append({'timeframe': timeframe, 'ob_type': 'bullish', 'ob_high': max(cur['open'], cur['close']), 'ob_low': min(cur['open'], cur['close']), 'ob_midpoint': (cur['open']+cur['close'])/2, 'validity_status': 'valid'})
        if cur['close'] > cur['open'] and nxt['close'] < nxt['open'] and nxt['close'] < prev['low']:
            obs.append({'timeframe': timeframe, 'ob_type': 'bearish', 'ob_high': max(cur['open'], cur['close']), 'ob_low': min(cur['open'], cur['close']), 'ob_midpoint': (cur['open']+cur['close'])/2, 'validity_status': 'valid'})
    return obs
