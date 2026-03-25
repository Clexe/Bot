from utils.logger import get_logger

logger = get_logger(__name__)


async def compute_volume_profile(candles: list, num_bins: int = 50) -> dict:
    """Compute Volume Profile from candle data.

    Uses tick volume as proxy when COMEX volume unavailable.
    Returns POC, VAH, VAL, HVN zones, and LVN zones.
    """
    if len(candles) < 10:
        return {"poc": None, "vah": None, "val": None, "hvn_zones": [], "lvn_zones": []}

    price_high = max(c["high"] for c in candles)
    price_low = min(c["low"] for c in candles)
    price_range = price_high - price_low

    if price_range == 0:
        return {"poc": price_high, "vah": price_high, "val": price_low, "hvn_zones": [], "lvn_zones": []}

    bin_size = price_range / num_bins
    bins = [0.0] * num_bins
    bin_prices = [price_low + (i + 0.5) * bin_size for i in range(num_bins)]

    for c in candles:
        vol = c.get("volume", c.get("tick_volume", 1))
        c_low = c["low"]
        c_high = c["high"]
        start_bin = max(0, int((c_low - price_low) / bin_size))
        end_bin = min(num_bins - 1, int((c_high - price_low) / bin_size))
        if start_bin == end_bin:
            bins[start_bin] += vol
        else:
            spread = end_bin - start_bin + 1
            per_bin = vol / spread
            for b in range(start_bin, end_bin + 1):
                bins[b] += per_bin

    poc_idx = bins.index(max(bins))
    poc = bin_prices[poc_idx]

    total_vol = sum(bins)
    if total_vol == 0:
        return {"poc": poc, "vah": price_high, "val": price_low, "hvn_zones": [], "lvn_zones": []}

    sorted_bins = sorted(range(num_bins), key=lambda i: bins[i], reverse=True)
    cumulative = 0.0
    va_bins = set()
    for idx in sorted_bins:
        cumulative += bins[idx]
        va_bins.add(idx)
        if cumulative >= total_vol * 0.7:
            break

    vah = max(bin_prices[i] for i in va_bins)
    val = min(bin_prices[i] for i in va_bins)

    avg_vol = total_vol / num_bins
    hvn_zones = []
    lvn_zones = []
    for i in range(num_bins):
        if bins[i] > avg_vol * 1.5:
            hvn_zones.append({"price": bin_prices[i], "volume": bins[i]})
        elif bins[i] < avg_vol * 0.5:
            lvn_zones.append({"price": bin_prices[i], "volume": bins[i]})

    return {
        "poc": round(poc, 5),
        "vah": round(vah, 5),
        "val": round(val, 5),
        "hvn_zones": hvn_zones,
        "lvn_zones": lvn_zones,
    }


async def check_volume_confluence(poi_price: float, volume_profile: dict, pip_tolerance: float = 0.0005) -> dict:
    """Check if POI aligns with volume profile levels.

    POI within 5 pips of POC → +1 score.
    HVN confluence → reinforced confidence.
    """
    result = {"confluence": False, "poc_near": False, "hvn_near": False, "score_bonus": 0}

    if not volume_profile or not volume_profile.get("poc"):
        return result

    poc = volume_profile["poc"]
    if abs(poi_price - poc) <= pip_tolerance * 10:
        result["poc_near"] = True
        result["score_bonus"] += 1
        result["confluence"] = True

    for hvn in volume_profile.get("hvn_zones", []):
        if abs(poi_price - hvn["price"]) <= pip_tolerance * 10:
            result["hvn_near"] = True
            result["confluence"] = True
            break

    return result


async def adjust_tp_for_volume(tp_price: float, volume_profile: dict, direction: str) -> float:
    """Adjust TP based on volume profile.

    TP inside HVN → tighten (price likely to stall).
    TP through LVN → widen (price likely to move fast).
    """
    if not volume_profile:
        return tp_price

    for hvn in volume_profile.get("hvn_zones", []):
        if direction == "LONG" and abs(tp_price - hvn["price"]) < abs(tp_price * 0.002):
            return hvn["price"] * 0.999
        elif direction == "SHORT" and abs(tp_price - hvn["price"]) < abs(tp_price * 0.002):
            return hvn["price"] * 1.001

    return tp_price
