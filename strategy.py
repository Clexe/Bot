import pandas as pd
from config import HIGH_PIP_SYMBOLS


def get_pip_value(pair):
    """Determine pip value multiplier for a given pair.

    Returns a divisor so that risk_pips / pip_value gives a sensible
    price distance for stop-loss calculations.
    """
    clean = pair.upper()
    # Crypto needs special handling per asset price magnitude
    if "BTC" in clean:
        return 0.1   # 1 pip ≈ $10, 50 pips ≈ $500
    if "ETH" in clean:
        return 1      # 1 pip ≈ $1, 50 pips ≈ $50
    if "SOL" in clean:
        return 10     # 1 pip ≈ $0.1, 50 pips ≈ $5
    if any(x in clean for x in HIGH_PIP_SYMBOLS):
        return 10
    return 10000


# =====================
# Gap 1: Body-Based Fresh Zone Detection
# =====================

def find_zones(df, lookback=40):
    """Scan the last *lookback* candles and return MSNR-style zones.

    Zone types:
      A-Level (resistance): bullish candle followed by bearish candle.
      V-Level (support):    bearish candle followed by bullish candle.
      OC-Gap:               two consecutive same-direction candles with a gap
                            between c1.close and c2.open.

    Returns a list of zone dicts:
        {type, direction, top, bottom, bar_index, fresh, miss}
    """
    if len(df) < 2:
        return []

    start = max(0, len(df) - lookback)
    zones = []

    for i in range(start, len(df) - 1):
        c1 = df.iloc[i]
        c2 = df.iloc[i + 1]
        c1_bull = c1['close'] > c1['open']
        c2_bull = c2['close'] > c2['open']

        # A-Level: bullish then bearish → supply/resistance zone
        if c1_bull and not c2_bull:
            top = max(c1['close'], c2['open'])
            bottom = min(c1['close'], c2['open'])
            if top > bottom:
                zones.append({
                    "type": "A", "direction": "supply",
                    "top": top, "bottom": bottom,
                    "bar_index": i, "fresh": True, "miss": False,
                })

        # V-Level: bearish then bullish → demand/support zone
        elif not c1_bull and c2_bull:
            top = max(c1['close'], c2['open'])
            bottom = min(c1['close'], c2['open'])
            if top > bottom:
                zones.append({
                    "type": "V", "direction": "demand",
                    "top": top, "bottom": bottom,
                    "bar_index": i, "fresh": True, "miss": False,
                })

        # OC-Gap: same direction, gap between c1.close and c2.open
        elif c1_bull == c2_bull:
            gap_top = max(c1['close'], c2['open'])
            gap_bottom = min(c1['close'], c2['open'])
            if gap_top > gap_bottom:
                direction = "supply" if not c1_bull else "demand"
                zones.append({
                    "type": "OC", "direction": direction,
                    "top": gap_top, "bottom": gap_bottom,
                    "bar_index": i, "fresh": True, "miss": False,
                })

    return zones


def mark_freshness(zones, df):
    """Update freshness on each zone by checking subsequent candle wicks.

    A zone becomes unfresh (fresh=False) if any candle after its formation
    has a wick that enters the zone.  A zone is marked miss=True if the
    first 3 candles after formation all have wicks that do NOT touch the
    zone (strong displacement).
    """
    for z in zones:
        formation = z["bar_index"] + 1  # zone forms across bar_index and bar_index+1
        start_check = formation + 1
        miss_window = 3
        miss_count = 0

        for j in range(start_check, len(df)):
            candle = df.iloc[j]
            wick_touches = candle['low'] <= z["top"] and candle['high'] >= z["bottom"]

            if wick_touches:
                z["fresh"] = False
                break

            if j - start_check < miss_window:
                miss_count += 1

        if z["fresh"] and miss_count >= miss_window:
            z["miss"] = True

    return zones


def get_fresh_zones(df, direction, lookback=40):
    """Return fresh zones matching *direction* ('supply' or 'demand').

    Sorted by strength: MISS zones first, then most recent.
    """
    zones = find_zones(df, lookback)
    zones = mark_freshness(zones, df)
    filtered = [z for z in zones if z["fresh"] and z["direction"] == direction]
    # MISS zones first, then by recency (higher bar_index = more recent)
    filtered.sort(key=lambda z: (-int(z["miss"]), -z["bar_index"]))
    return filtered


# =====================
# Existing helpers (kept)
# =====================

def find_swing_points(df_l, start=-23, end=-3):
    """Find swing high and swing low from a price range."""
    if len(df_l) < abs(start):
        return None, None
    segment = df_l.iloc[start:end]
    return segment['high'].max(), segment['low'].min()


def detect_bos(df_l, swing_high, swing_low, lookback=5):
    """Detect Break of Structure."""
    if len(df_l) < lookback:
        return False, False
    recent = df_l['close'].iloc[-lookback:]
    bullish = recent.max() > swing_high
    bearish = recent.min() < swing_low
    return bullish, bearish


def detect_fvg(df_l):
    """Detect Fair Value Gap (imbalance) from last 3 candles."""
    if len(df_l) < 3:
        return None
    c1 = df_l.iloc[-3]
    c3 = df_l.iloc[-1]
    if c3.low > c1.high:
        return "BULL_FVG"
    if c3.high < c1.low:
        return "BEAR_FVG"
    return None


def calculate_levels(sig_type, entry, swing_high, swing_low, max_risk_price, tp_target):
    """Calculate entry, SL, and TP levels for a signal."""
    if sig_type == "BUY":
        raw_sl = swing_low
        sl = entry - max_risk_price if (entry - raw_sl) > max_risk_price else raw_sl
    else:
        raw_sl = swing_high
        sl = entry + max_risk_price if (raw_sl - entry) > max_risk_price else raw_sl

    return {"sl": sl, "tp": tp_target}


# =====================
# Gap 2: Multi-Timeframe Storyline
# =====================

def detect_bias(df_h, lookback=20):
    """Legacy bias detection (momentum check). Kept for fallback."""
    if len(df_h) < lookback + 1:
        return None
    if df_h['close'].iloc[-1] > df_h['close'].iloc[-lookback]:
        return "BULL"
    return "BEAR"


def _htf_rejection(df_h, zones, direction, candles_to_check=3):
    """Check if a recent HTF candle rejected off a fresh zone.

    For a bullish rejection (demand zone): wick enters zone but body closes
    above it.  For bearish rejection (supply zone): wick enters zone but
    body closes below it.
    """
    start = max(0, len(df_h) - candles_to_check)
    for i in range(len(df_h) - 1, start - 1, -1):
        c = df_h.iloc[i]
        for z in zones:
            wick_enters = c['low'] <= z['top'] and c['high'] >= z['bottom']
            if not wick_enters:
                continue

            body_top = max(c['open'], c['close'])
            body_bottom = min(c['open'], c['close'])

            if direction == "demand" and body_bottom >= z['bottom']:
                # Wick dipped into demand but body closed above → bullish rejection
                return z
            if direction == "supply" and body_top <= z['top']:
                # Wick poked into supply but body closed below → bearish rejection
                return z
    return None


def detect_storyline(df_h, df_l):
    """Detect HTF rejection + LTF breakout confirmation.

    Returns {bias, htf_zone, confirmed} or None.
    """
    if len(df_h) < 10 or len(df_l) < 23:
        return None

    htf_zones = find_zones(df_h, lookback=40)
    htf_zones = mark_freshness(htf_zones, df_h)
    fresh_htf = [z for z in htf_zones if z["fresh"]]

    demand_zones = [z for z in fresh_htf if z["direction"] == "demand"]
    supply_zones = [z for z in fresh_htf if z["direction"] == "supply"]

    # Check for bullish rejection (off demand)
    bull_zone = _htf_rejection(df_h, demand_zones, "demand")
    if bull_zone:
        swing_high, swing_low = find_swing_points(df_l)
        if swing_high is not None:
            bull_bos, _ = detect_bos(df_l, swing_high, swing_low)
            if bull_bos:
                return {"bias": "BULL", "htf_zone": bull_zone, "confirmed": True}
        return {"bias": "BULL", "htf_zone": bull_zone, "confirmed": False}

    # Check for bearish rejection (off supply)
    bear_zone = _htf_rejection(df_h, supply_zones, "supply")
    if bear_zone:
        swing_high, swing_low = find_swing_points(df_l)
        if swing_high is not None:
            _, bear_bos = detect_bos(df_l, swing_high, swing_low)
            if bear_bos:
                return {"bias": "BEAR", "htf_zone": bear_zone, "confirmed": True}
        return {"bias": "BEAR", "htf_zone": bear_zone, "confirmed": False}

    # Fallback: momentum-based bias (backward compat)
    fallback_bias = detect_bias(df_h)
    if fallback_bias:
        return {"bias": fallback_bias, "htf_zone": None, "confirmed": False}

    return None


# =====================
# Gap 3: Retest Confirmation
# =====================

def detect_engulfing(df, zone, lookback=10):
    """Detect an engulfing pattern at or near the given zone.

    Returns the index of the engulfing candle or None.
    """
    if len(df) < 2:
        return None

    start = max(0, len(df) - lookback)
    for i in range(start + 1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        prev_body_top = max(prev['open'], prev['close'])
        prev_body_bottom = min(prev['open'], prev['close'])
        curr_body_top = max(curr['open'], curr['close'])
        curr_body_bottom = min(curr['open'], curr['close'])

        # Bullish engulfing at demand zone
        if (curr['close'] > curr['open']
                and curr_body_bottom <= prev_body_bottom
                and curr_body_top >= prev_body_top
                and curr['low'] <= zone['top']):
            return i

        # Bearish engulfing at supply zone
        if (curr['close'] < curr['open']
                and curr_body_bottom <= prev_body_bottom
                and curr_body_top >= prev_body_top
                and curr['high'] >= zone['bottom']):
            return i

    return None


def detect_inducement_swept(df, swing_high, swing_low, direction, lookback=15):
    """Check if liquidity was swept before the current move.

    For BUY:  was there a wick below swing_low (sweeping sell-side liquidity)?
    For SELL: was there a wick above swing_high (sweeping buy-side liquidity)?
    """
    if len(df) < lookback:
        return False

    start = max(0, len(df) - lookback)
    segment = df.iloc[start:]

    if direction == "BUY":
        return bool(segment['low'].min() < swing_low)
    if direction == "SELL":
        return bool(segment['high'].max() > swing_high)
    return False


# =====================
# Signal Generation (updated flow)
# =====================

def get_smc_signal(df_l, df_h, pair, risk_pips=50):
    """Generate SMC trading signal from price data.

    New flow:
      1. Storyline (HTF rejection + LTF breakout confirmation)
      2. BOS
      3. Fresh Zone
      4. FVG (confluence filter)
      5. Retest check (wick touches zone)
      6. Engulfing confirmation
      7. Inducement (bonus confidence)

    Returns signal dict or None. Dict shape unchanged for scanner.py compat.
    """
    if df_l.empty or df_h.empty or len(df_l) < 23 or len(df_h) < 20:
        return None

    pip_val = get_pip_value(pair)
    max_risk_price = risk_pips / pip_val

    # 1. Storyline
    storyline = detect_storyline(df_h, df_l)
    if storyline is None:
        return None
    bias = storyline["bias"]

    # 2. Swing points + BOS
    swing_high, swing_low = find_swing_points(df_l)
    if swing_high is None:
        return None

    bullish_bos, bearish_bos = detect_bos(df_l, swing_high, swing_low)

    # 3. FVG (confluence)
    fvg = detect_fvg(df_l)

    c = df_l.iloc[-1]
    sig = None

    if bias == "BULL" and bullish_bos and fvg == "BULL_FVG":
        # Fresh zone lookup
        fresh = get_fresh_zones(df_l, "demand")
        zone = fresh[0] if fresh else None

        # Determine entry level
        entry_price = zone["top"] if zone else swing_high
        tp_target = df_h['high'].max()

        # Retest check: current candle wick touches zone
        retest_ok = True
        if zone:
            retest_ok = c['low'] <= zone['top'] and c['high'] >= zone['bottom']

        # Engulfing confirmation
        engulfing = None
        if zone and retest_ok:
            engulfing = detect_engulfing(df_l, zone)

        # Require engulfing when storyline is confirmed, allow without for fallback
        if storyline["confirmed"] and zone and not engulfing:
            return None

        # Inducement check
        inducement = detect_inducement_swept(df_l, swing_high, swing_low, "BUY")

        # Calculate levels using zone boundary or swing
        sl_anchor = zone["bottom"] if zone else swing_low
        limit_levels = calculate_levels("BUY", entry_price, swing_high, sl_anchor, max_risk_price, tp_target)
        market_levels = calculate_levels("BUY", c['close'], swing_high, sl_anchor, max_risk_price, tp_target)

        # Confidence
        confidence = "high" if (engulfing and zone and (inducement or zone.get("miss"))) else "medium"

        sig = {
            "act": "BUY",
            "limit_e": entry_price,
            "limit_sl": limit_levels["sl"],
            "market_e": c['close'],
            "market_sl": market_levels["sl"],
            "tp": limit_levels["tp"],
            "confidence": confidence,
            "zone_type": zone["type"] if zone else None,
            "miss": zone["miss"] if zone else False,
        }

    if bias == "BEAR" and bearish_bos and fvg == "BEAR_FVG":
        fresh = get_fresh_zones(df_l, "supply")
        zone = fresh[0] if fresh else None

        entry_price = zone["bottom"] if zone else swing_low
        tp_target = df_h['low'].min()

        retest_ok = True
        if zone:
            retest_ok = c['high'] >= zone['bottom'] and c['low'] <= zone['top']

        engulfing = None
        if zone and retest_ok:
            engulfing = detect_engulfing(df_l, zone)

        if storyline["confirmed"] and zone and not engulfing:
            return None

        inducement = detect_inducement_swept(df_l, swing_high, swing_low, "SELL")

        sl_anchor = zone["top"] if zone else swing_high
        limit_levels = calculate_levels("SELL", entry_price, sl_anchor, swing_low, max_risk_price, tp_target)
        market_levels = calculate_levels("SELL", c['close'], sl_anchor, swing_low, max_risk_price, tp_target)

        confidence = "high" if (engulfing and zone and (inducement or zone.get("miss"))) else "medium"

        sig = {
            "act": "SELL",
            "limit_e": entry_price,
            "limit_sl": limit_levels["sl"],
            "market_e": c['close'],
            "market_sl": market_levels["sl"],
            "tp": limit_levels["tp"],
            "confidence": confidence,
            "zone_type": zone["type"] if zone else None,
            "miss": zone["miss"] if zone else False,
        }

    return sig
