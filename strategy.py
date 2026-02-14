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

    SBR/RBS: If a candle body closes *through* a zone (not just wick), the
    zone is broken and becomes fresh in the opposite direction.
    """
    new_zones = []

    for z in zones:
        formation = z["bar_index"] + 1  # zone forms across bar_index and bar_index+1
        start_check = formation + 1
        miss_window = 3
        miss_count = 0

        for j in range(start_check, len(df)):
            candle = df.iloc[j]
            wick_touches = candle['low'] <= z["top"] and candle['high'] >= z["bottom"]

            # SBR/RBS: body closes through the zone → broken, flip direction
            body_bottom = min(candle['open'], candle['close'])
            body_top = max(candle['open'], candle['close'])

            if z["direction"] == "demand" and body_bottom < z["bottom"]:
                # Bearish body closed below demand zone → broken → becomes supply
                z["fresh"] = False
                new_zones.append({
                    "type": z["type"], "direction": "supply",
                    "top": z["top"], "bottom": z["bottom"],
                    "bar_index": j, "fresh": True, "miss": False,
                })
                break
            if z["direction"] == "supply" and body_top > z["top"]:
                # Bullish body closed above supply zone → broken → becomes demand
                z["fresh"] = False
                new_zones.append({
                    "type": z["type"], "direction": "demand",
                    "top": z["top"], "bottom": z["bottom"],
                    "bar_index": j, "fresh": True, "miss": False,
                })
                break

            if wick_touches:
                z["fresh"] = False
                break

            if j - start_check < miss_window:
                miss_count += 1

        if z["fresh"] and miss_count >= miss_window:
            z["miss"] = True

    # Add SBR/RBS flipped zones and recursively check their freshness
    if new_zones:
        zones.extend(new_zones)
        # Check freshness of newly created zones only
        for nz in new_zones:
            start_check = nz["bar_index"] + 1
            miss_count = 0
            for j in range(start_check, len(df)):
                candle = df.iloc[j]
                wick_touches = candle['low'] <= nz["top"] and candle['high'] >= nz["bottom"]
                if wick_touches:
                    nz["fresh"] = False
                    break
                if j - start_check < 3:
                    miss_count += 1
            if nz["fresh"] and miss_count >= 3:
                nz["miss"] = True

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
    """Detect Break of Structure with wick-vs-body distinction.

    Returns (bullish_bos, bearish_bos, bull_sweep, bear_sweep).
    - bullish_bos/bearish_bos: True if a candle *body* (close) broke the level.
    - bull_sweep/bear_sweep: True if only a wick exceeded the level but body
      closed back inside — indicates a liquidity grab, not a real breakout.
    """
    if len(df_l) < lookback:
        return False, False, False, False

    segment = df_l.iloc[-lookback:]
    closes = segment['close']
    highs = segment['high']
    lows = segment['low']

    bullish_bos = bool(closes.max() > swing_high)
    bearish_bos = bool(closes.min() < swing_low)

    # Wick-only sweep: wick exceeded but no body close beyond
    bull_sweep = bool(highs.max() > swing_high) and not bullish_bos
    bear_sweep = bool(lows.min() < swing_low) and not bearish_bos

    return bullish_bos, bearish_bos, bull_sweep, bear_sweep


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


def _opposing_zone_tp(fresh_zones, bias, entry_price, fallback):
    """Find TP from the nearest opposing fresh HTF zone.

    BULL → nearest fresh supply zone's bottom above entry.
    BEAR → nearest fresh demand zone's top below entry.
    Falls back to HTF max/min if no opposing zone found.
    """
    if bias == "BULL":
        candidates = [z for z in fresh_zones
                      if z["direction"] == "supply" and z["bottom"] > entry_price]
        candidates.sort(key=lambda z: z["bottom"])
        return candidates[0]["bottom"] if candidates else fallback
    else:
        candidates = [z for z in fresh_zones
                      if z["direction"] == "demand" and z["top"] < entry_price]
        candidates.sort(key=lambda z: -z["top"])
        return candidates[0]["top"] if candidates else fallback


def _check_roadblock(fresh_zones, bias, entry_price, tp_target):
    """Check if an intermediate fresh zone sits close to entry (within 30% of range).

    Returns True if a roadblock is near, meaning limited room to breathe.
    """
    total_range = abs(tp_target - entry_price)
    if total_range <= 0:
        return False

    if bias == "BULL":
        # Look for supply zones between entry and TP
        blockers = [z for z in fresh_zones
                    if z["direction"] == "supply"
                    and z["bottom"] > entry_price
                    and z["bottom"] < tp_target]
        for z in blockers:
            dist = z["bottom"] - entry_price
            if dist / total_range <= 0.3:
                return True
    else:
        blockers = [z for z in fresh_zones
                    if z["direction"] == "demand"
                    and z["top"] < entry_price
                    and z["top"] > tp_target]
        for z in blockers:
            dist = entry_price - z["top"]
            if dist / total_range <= 0.3:
                return True
    return False


def detect_storyline(df_h, df_l):
    """Detect HTF rejection + LTF breakout confirmation.

    Returns {bias, htf_zone, tp_target, confirmed, roadblock_near} or None.
    TP targets the nearest opposing fresh HTF zone (same-TF rule).
    """
    if len(df_h) < 10 or len(df_l) < 23:
        return None

    htf_zones = find_zones(df_h, lookback=40)
    htf_zones = mark_freshness(htf_zones, df_h)
    fresh_htf = [z for z in htf_zones if z["fresh"]]

    demand_zones = [z for z in fresh_htf if z["direction"] == "demand"]
    supply_zones = [z for z in fresh_htf if z["direction"] == "supply"]

    current_price = df_l['close'].iloc[-1]

    # Check for bullish rejection (off demand)
    bull_zone = _htf_rejection(df_h, demand_zones, "demand")
    if bull_zone:
        tp = _opposing_zone_tp(fresh_htf, "BULL", current_price, df_h['high'].max())
        roadblock = _check_roadblock(fresh_htf, "BULL", current_price, tp)

        swing_high, swing_low = find_swing_points(df_l)
        confirmed = False
        if swing_high is not None:
            result = detect_bos(df_l, swing_high, swing_low)
            bull_bos = result[0]
            confirmed = bool(bull_bos)
        return {
            "bias": "BULL", "htf_zone": bull_zone, "tp_target": tp,
            "confirmed": confirmed, "roadblock_near": roadblock,
        }

    # Check for bearish rejection (off supply)
    bear_zone = _htf_rejection(df_h, supply_zones, "supply")
    if bear_zone:
        tp = _opposing_zone_tp(fresh_htf, "BEAR", current_price, df_h['low'].min())
        roadblock = _check_roadblock(fresh_htf, "BEAR", current_price, tp)

        swing_high, swing_low = find_swing_points(df_l)
        confirmed = False
        if swing_high is not None:
            result = detect_bos(df_l, swing_high, swing_low)
            bear_bos = result[1]
            confirmed = bool(bear_bos)
        return {
            "bias": "BEAR", "htf_zone": bear_zone, "tp_target": tp,
            "confirmed": confirmed, "roadblock_near": roadblock,
        }

    # Fallback: momentum-based bias (backward compat)
    fallback_bias = detect_bias(df_h)
    if fallback_bias:
        fallback_tp = df_h['high'].max() if fallback_bias == "BULL" else df_h['low'].min()
        return {
            "bias": fallback_bias, "htf_zone": None, "tp_target": fallback_tp,
            "confirmed": False, "roadblock_near": False,
        }

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

    For BUY:  a wick dipped below swing_low but the body (close) closed back
              above it — confirming a trap / stop hunt.
    For SELL: a wick spiked above swing_high but body closed back below.
    """
    if len(df) < lookback:
        return False

    start = max(0, len(df) - lookback)

    for i in range(start, len(df)):
        c = df.iloc[i]
        if direction == "BUY":
            if c['low'] < swing_low and min(c['open'], c['close']) >= swing_low:
                return True
        elif direction == "SELL":
            if c['high'] > swing_high and max(c['open'], c['close']) <= swing_high:
                return True
    return False


# =====================
# Signal Generation (updated flow)
# =====================

def _compute_confidence(storyline, zone, engulfing, inducement, fvg_match):
    """Compute three-tier confidence: high / medium / low."""
    if (storyline["confirmed"] and engulfing and zone
            and (inducement or zone.get("miss"))):
        return "high"
    if storyline["confirmed"] and zone and engulfing:
        return "medium"
    if storyline.get("roadblock_near"):
        return "low"
    if not storyline["confirmed"]:
        return "low"
    # Confirmed storyline but missing engulfing/zone — medium if FVG present
    if fvg_match:
        return "medium"
    return "low"


def get_smc_signal(df_l, df_h, pair, risk_pips=50):
    """Generate SMC trading signal from price data.

    Flow:
      1. Storyline (HTF rejection + LTF breakout confirmation + opposing zone TP)
      2. BOS (body-based, wick sweeps detected separately)
      3. Fresh Zone (required for confirmed storylines)
      4. FVG (confluence bonus — not a hard gate)
      5. Retest check (wick touches zone)
      6. Engulfing confirmation (required when confirmed + zone exists)
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

    # 2. Swing points + BOS (body-based + wick sweep)
    swing_high, swing_low = find_swing_points(df_l)
    if swing_high is None:
        return None

    bullish_bos, bearish_bos, bull_sweep, bear_sweep = detect_bos(
        df_l, swing_high, swing_low
    )

    # Wick-only sweep in the opposite direction strengthens the opposing case
    # e.g., bull_sweep (wick above swing_high, body below) = bearish trap signal
    if bias == "BULL" and bull_sweep and not bullish_bos:
        # Wick grabbed above but body closed below — this is a bearish trap, skip BUY
        pass
    if bias == "BEAR" and bear_sweep and not bearish_bos:
        pass

    # 3. FVG (confluence bonus, not hard gate)
    fvg = detect_fvg(df_l)

    c = df_l.iloc[-1]
    sig = None

    # Use storyline TP target (opposing HTF zone), fall back to HTF extreme
    storyline_tp = storyline.get("tp_target")

    if bias == "BULL" and bullish_bos:
        fresh = get_fresh_zones(df_l, "demand")
        zone = fresh[0] if fresh else None

        entry_price = zone["top"] if zone else swing_high
        tp_target = storyline_tp if storyline_tp else df_h['high'].max()

        # Retest check
        retest_ok = True
        if zone:
            retest_ok = c['low'] <= zone['top'] and c['high'] >= zone['bottom']

        # Engulfing confirmation
        engulfing = None
        if zone and retest_ok:
            engulfing = detect_engulfing(df_l, zone)

        # Require engulfing when storyline is confirmed and zone exists
        if storyline["confirmed"] and zone and not engulfing:
            return None

        # Inducement check
        inducement = detect_inducement_swept(df_l, swing_high, swing_low, "BUY")

        fvg_match = fvg == "BULL_FVG"
        confidence = _compute_confidence(storyline, zone, engulfing, inducement, fvg_match)

        sl_anchor = zone["bottom"] if zone else swing_low
        limit_levels = calculate_levels("BUY", entry_price, swing_high, sl_anchor, max_risk_price, tp_target)
        market_levels = calculate_levels("BUY", c['close'], swing_high, sl_anchor, max_risk_price, tp_target)

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

    if bias == "BEAR" and bearish_bos:
        fresh = get_fresh_zones(df_l, "supply")
        zone = fresh[0] if fresh else None

        entry_price = zone["bottom"] if zone else swing_low
        tp_target = storyline_tp if storyline_tp else df_h['low'].min()

        retest_ok = True
        if zone:
            retest_ok = c['high'] >= zone['bottom'] and c['low'] <= zone['top']

        engulfing = None
        if zone and retest_ok:
            engulfing = detect_engulfing(df_l, zone)

        if storyline["confirmed"] and zone and not engulfing:
            return None

        inducement = detect_inducement_swept(df_l, swing_high, swing_low, "SELL")

        fvg_match = fvg == "BEAR_FVG"
        confidence = _compute_confidence(storyline, zone, engulfing, inducement, fvg_match)

        sl_anchor = zone["top"] if zone else swing_high
        limit_levels = calculate_levels("SELL", entry_price, sl_anchor, swing_low, max_risk_price, tp_target)
        market_levels = calculate_levels("SELL", c['close'], sl_anchor, swing_low, max_risk_price, tp_target)

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
