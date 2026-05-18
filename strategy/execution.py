from config import PIP_SIZE, MIN_SL_PIPS, MIN_RR, PAIR_DISPLAY
from utils.logger import get_logger

logger = get_logger(__name__)


def compute_trade(setup: dict, levels: dict) -> dict | None:
    """Compute entry, SL, TP and validate R:R >= 1:3."""
    pair = setup["pair"]
    direction = setup["direction"]
    entry = setup["entry_price"]
    pip = PIP_SIZE.get(pair, 0.0001)

    sl = _compute_sl(setup, direction, pip)
    if sl is None:
        return None

    sl_pips = abs(entry - sl) / pip
    if sl_pips < MIN_SL_PIPS:
        if direction == "LONG":
            sl = entry - MIN_SL_PIPS * pip
        else:
            sl = entry + MIN_SL_PIPS * pip
        sl_pips = MIN_SL_PIPS

    tp = _compute_tp(direction, levels, entry)
    if tp is None:
        return None

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        return None
    rr = reward / risk

    if rr < MIN_RR:
        logger.info("%s setup rejected: R:R %.1f < %.1f", pair, rr, MIN_RR)
        return None

    return {
        "pair": pair,
        "display_pair": PAIR_DISPLAY.get(pair, pair),
        "direction": direction,
        "setup_type": setup["setup_type"],
        "entry": round(entry, 5),
        "sl": round(sl, 5),
        "tp": round(tp, 5),
        "sl_pips": round(sl_pips, 1),
        "tp_pips": round(abs(tp - entry) / pip, 1),
        "rr": round(rr, 1),
        "confluences": setup.get("confluences", []),
    }


def _compute_sl(setup, direction, pip):
    poi_low = setup.get("poi_low")
    poi_high = setup.get("poi_high")
    sweep = setup.get("sweep")

    if direction == "LONG":
        candidates = []
        if poi_low is not None:
            candidates.append(poi_low - 2 * pip)
        if sweep:
            candidates.append(sweep.get("wick", sweep.get("level_price", 0)) - 2 * pip)
        return min(candidates) if candidates else None
    else:
        candidates = []
        if poi_high is not None:
            candidates.append(poi_high + 2 * pip)
        if sweep:
            candidates.append(sweep.get("wick", sweep.get("level_price", 0)) + 2 * pip)
        return max(candidates) if candidates else None


def _compute_tp(direction, levels, entry):
    if direction == "LONG":
        targets = [levels[k] for k in ("PDH", "PWH", "ASIAN_HIGH") if k in levels and levels[k] > entry]
        return min(targets) if targets else None
    else:
        targets = [levels[k] for k in ("PDL", "PWL", "ASIAN_LOW") if k in levels and levels[k] < entry]
        return max(targets) if targets else None
