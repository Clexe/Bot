from signalix.utils.helpers import calculate_rr, in_round_number

async def compute_trade_levels(direction: str, entry: float, sweep_low: float, sweep_high: float, tp_candidates: list):
    """Return entry/SL/TP levels with round-number and minimum RR handling."""
    if direction == 'LONG':
        sl = sweep_low - 0.0003
        if in_round_number(sl):
            sl -= 0.0002
    else:
        sl = sweep_high + 0.0003
        if in_round_number(sl):
            sl += 0.0002
    tps = tp_candidates[:3]
    rrs = [calculate_rr(entry, sl, tp) for tp in tps]
    if rrs and rrs[0] < 3:
        return None
    return {'entry': entry, 'sl': sl, 'tp1': tps[0], 'tp2': tps[1] if len(tps)>1 else None, 'tp3': tps[2] if len(tps)>2 else None, 'rr_tp1': rrs[0], 'rr_tp2': rrs[1] if len(rrs)>1 else None, 'rr_tp3': rrs[2] if len(rrs)>2 else None}
