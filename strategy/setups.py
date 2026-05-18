from strategy.detectors import (
    detect_fvg, detect_order_block, detect_displacement,
    detect_choch, detect_liquidity_sweep,
)
from utils.logger import get_logger

logger = get_logger(__name__)


async def scan_setups(pair: str, candles: dict, bias_info: dict, levels: dict) -> list:
    """Scan for continuation and reversal setups on M15 framework."""
    setups = []
    bias = bias_info.get("bias")
    if bias == "NEUTRAL":
        return setups

    m15 = candles.get("M15", [])
    m5 = candles.get("M5", [])
    if len(m15) < 10:
        return setups

    cont = _check_continuation(pair, m15, m5, bias, levels)
    if cont:
        setups.append(cont)

    rev = _check_reversal(pair, m15, m5, bias, levels)
    if rev:
        setups.append(rev)

    return setups


def _check_continuation(pair, m15, m5, bias, levels):
    """Continuation: displacement in bias direction -> FVG/OB -> retracement.

    1. Bias confirmed from HTF
    2. M15 sweep of liquidity
    3. M15 or M5 displacement confirms intent
    4. FVG or OB forms = PoI
    5. Price near PoI (retracement)
    """
    direction = "LONG" if bias == "BULLISH" else "SHORT"
    target_type = "bullish" if bias == "BULLISH" else "bearish"

    disps = detect_displacement(m15, lookback=5)
    bias_disps = [d for d in disps if d["direction"] == direction]
    if not bias_disps and m5:
        bias_disps = [d for d in detect_displacement(m5, lookback=10) if d["direction"] == direction]
    if not bias_disps:
        return None

    fvgs = [f for f in detect_fvg(m15, lookback=10) if f["type"] == target_type]
    obs = [o for o in detect_order_block(m15, lookback=10) if o["type"] == target_type]

    poi, poi_source = _pick_poi(fvgs, obs)
    if poi is None:
        return None

    if not _price_near_poi(m15[-1]["close"], poi):
        return None

    sweeps = detect_liquidity_sweep(m15, levels, lookback=15)
    entry = poi.get("ce", poi.get("mid", (poi["high"] + poi["low"]) / 2))

    confluences = [
        f"HTF bias: {bias}",
        "Displacement confirmed",
        f"{poi_source} @ {poi['low']:.5f}-{poi['high']:.5f}",
    ]
    if sweeps:
        confluences.append(f"Liquidity swept: {sweeps[-1]['level_name']}")

    return {
        "pair": pair,
        "setup_type": "continuation",
        "direction": direction,
        "poi_source": poi_source,
        "poi_high": poi["high"],
        "poi_low": poi["low"],
        "entry_price": entry,
        "confluences": confluences,
        "sweep": sweeps[-1] if sweeps else None,
    }


def _check_reversal(pair, m15, m5, bias, levels):
    """Reversal: sweep against bias -> CHoCH -> FVG/OB -> retracement.

    1. Bias confirmed
    2. Price sweeps against bias (e.g., bullish bias, sweeps below PDL/Asian Low)
    3. CHoCH on M15 in bias direction after the sweep
    4. FVG/OB forms
    5. Price near PoI
    """
    sweeps = detect_liquidity_sweep(m15, levels, lookback=15)
    if not sweeps:
        return None

    if bias == "BULLISH":
        against = [s for s in sweeps if s["sweep_type"] == "below"]
    else:
        against = [s for s in sweeps if s["sweep_type"] == "above"]
    if not against:
        return None

    sweep = against[-1]
    post_sweep = m15[sweep["index"]:]
    if len(post_sweep) < 4:
        return None

    choch = detect_choch(post_sweep)
    if not choch.get("detected"):
        return None

    direction = "LONG" if bias == "BULLISH" else "SHORT"
    if choch["direction"] != direction:
        return None

    target_type = "bullish" if bias == "BULLISH" else "bearish"
    fvgs = [f for f in detect_fvg(post_sweep, lookback=8) if f["type"] == target_type]
    obs = [o for o in detect_order_block(post_sweep, lookback=8) if o["type"] == target_type]

    poi, poi_source = _pick_poi(fvgs, obs)
    if poi is None:
        return None

    if not _price_near_poi(m15[-1]["close"], poi):
        return None

    entry = poi.get("ce", poi.get("mid", (poi["high"] + poi["low"]) / 2))

    confluences = [
        f"HTF bias: {bias}",
        f"Liquidity swept: {sweep['level_name']} @ {sweep['level_price']:.5f}",
        "CHoCH confirmed on M15",
        f"{poi_source} @ {poi['low']:.5f}-{poi['high']:.5f}",
    ]

    return {
        "pair": pair,
        "setup_type": "reversal",
        "direction": direction,
        "poi_source": poi_source,
        "poi_high": poi["high"],
        "poi_low": poi["low"],
        "entry_price": entry,
        "confluences": confluences,
        "sweep": sweep,
    }


def _pick_poi(fvgs, obs):
    """Pick the most recent PoI from FVGs and OBs."""
    poi = None
    source = None
    if fvgs:
        poi = fvgs[-1]
        source = "FVG"
    if obs:
        ob = obs[-1]
        if poi is None or ob["index"] > poi["index"]:
            poi = ob
            source = "OB"
    return poi, source


def _price_near_poi(price, poi):
    zone = poi["high"] - poi["low"]
    buffer = zone * 0.5
    return (poi["low"] - buffer) <= price <= (poi["high"] + buffer)
