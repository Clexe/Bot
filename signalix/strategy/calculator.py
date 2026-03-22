from signalix.config import PAIR_CONFIG
from signalix.utils.helpers import calculate_rr, in_round_number, get_pip_value
from signalix.utils.logger import get_logger

logger = get_logger(__name__)


async def compute_precision_levels(pair: str, direction: str, entry: float,
                                   sweep_wick: float, tp_candidates: list,
                                   db) -> dict:
    """Calculate entry, SL, TP levels for Precision engine.

    Entry at M15 FVG CE or OB midpoint.
    SL below sweep wick + pair buffer. TP1 minimum 1:3.
    Never at round number. Spring wick priority over MSNR boundary.
    """
    buffer_pips = PAIR_CONFIG.get(pair, {}).get("sl_buffer_pips", 2)
    pip_size = get_pip_value(pair)
    buffer = buffer_pips * pip_size

    min_rr_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='min_precision_rr'")
    min_rr = float(min_rr_row["value"]) if min_rr_row else 3.0

    if direction == "LONG":
        sl = sweep_wick - buffer
        if in_round_number(sl, pip_size):
            sl -= pip_size * 2
    else:
        sl = sweep_wick + buffer
        if in_round_number(sl, pip_size):
            sl += pip_size * 2

    tps = _adjust_round_numbers(tp_candidates[:3], pip_size)
    rrs = [calculate_rr(entry, sl, tp) for tp in tps]

    if not rrs or rrs[0] < min_rr:
        return None

    return {
        "entry": round(entry, 5), "sl": round(sl, 5),
        "tp1": round(tps[0], 5) if len(tps) > 0 else None,
        "tp2": round(tps[1], 5) if len(tps) > 1 else None,
        "tp3": round(tps[2], 5) if len(tps) > 2 else None,
        "rr_tp1": rrs[0] if len(rrs) > 0 else None,
        "rr_tp2": rrs[1] if len(rrs) > 1 else None,
        "rr_tp3": rrs[2] if len(rrs) > 2 else None,
    }


async def compute_flow_levels(pair: str, direction: str, entry: float,
                              sweep_wick: float, tp_candidates: list,
                              db) -> dict:
    """Calculate entry, SL, TP levels for Flow engine.

    Entry at M15 FVG CE or OB midpoint.
    SL below sweep wick + pair buffer. TP1 minimum 1:2. No TP3.
    """
    buffer_pips = PAIR_CONFIG.get(pair, {}).get("sl_buffer_pips", 2)
    pip_size = get_pip_value(pair)
    buffer = buffer_pips * pip_size

    min_rr_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='min_flow_rr'")
    min_rr = float(min_rr_row["value"]) if min_rr_row else 2.0

    if direction == "LONG":
        sl = sweep_wick - buffer
        if in_round_number(sl, pip_size):
            sl -= pip_size * 2
    else:
        sl = sweep_wick + buffer
        if in_round_number(sl, pip_size):
            sl += pip_size * 2

    tps = _adjust_round_numbers(tp_candidates[:2], pip_size)
    rrs = [calculate_rr(entry, sl, tp) for tp in tps]

    if not rrs or rrs[0] < min_rr:
        return None

    return {
        "entry": round(entry, 5), "sl": round(sl, 5),
        "tp1": round(tps[0], 5) if len(tps) > 0 else None,
        "tp2": round(tps[1], 5) if len(tps) > 1 else None,
        "tp3": None,
        "rr_tp1": rrs[0] if len(rrs) > 0 else None,
        "rr_tp2": rrs[1] if len(rrs) > 1 else None,
        "rr_tp3": None,
    }


async def compute_position_size(pair: str, entry: float, sl: float,
                                account_balance: float, db) -> float:
    """Calculate position size based on risk percentage from bot_settings."""
    risk_row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='max_risk_percent'")
    risk_pct = float(risk_row["value"]) if risk_row else 1.0

    risk_amount = account_balance * (risk_pct / 100)
    sl_distance = abs(entry - sl)
    if sl_distance == 0:
        return 0.0

    pip_size = get_pip_value(pair)
    sl_pips = sl_distance / pip_size

    if pair in ("BTCUSDT", "ETHUSDT"):
        lot_size = risk_amount / sl_distance
    else:
        pip_value_per_lot = 10.0
        lot_size = risk_amount / (sl_pips * pip_value_per_lot)

    return round(lot_size, 2)


def _adjust_round_numbers(prices: list, pip_size: float) -> list:
    adjusted = []
    for p in prices:
        if in_round_number(p, pip_size):
            p -= pip_size * 3
        adjusted.append(p)
    return adjusted
