async def build_storyline(pair: str, price: float, htf_levels: list):
    """Determine bullish/bearish/neutral storyline from HTF level interactions."""
    daily = [l for l in htf_levels if l['timeframe'] == 'D' and l['freshness_status'] == 'fresh']
    if not daily:
        return {'pair': pair, 'direction': 'neutral', 'origin_level': None, 'target_level': None, 'dol_target': None}
    above = [l for l in daily if l['level_price'] < price]
    below = [l for l in daily if l['level_price'] > price]
    if above and below:
        return {'pair': pair, 'direction': 'bullish', 'origin_level': max(l['level_price'] for l in above), 'target_level': min(l['level_price'] for l in below), 'dol_target': min(l['level_price'] for l in below)}
    return {'pair': pair, 'direction': 'neutral', 'origin_level': None, 'target_level': None, 'dol_target': None}
