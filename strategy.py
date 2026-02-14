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
    has a wick that enters the zone OR comes within the mitigation buffer.

    Mitigation buffer: 0.1% of the zone midpoint price. If price approaches
    within this buffer and reverses, orders are considered absorbed.

    A zone is marked miss=True if the first 3 candles after formation all
    have wicks that do NOT touch the zone (strong displacement).

    SBR/RBS: If a candle body closes *through* a zone (not just wick), the
    zone is broken and becomes fresh in the opposite direction, tagged FLIP.
    """
    new_zones = []

    for z in zones:
        formation = z["bar_index"] + 1  # zone forms across bar_index and bar_index+1
        start_check = formation + 1
        miss_window = 3
        miss_count = 0

        # Mitigation buffer: 0.1% of zone midpoint
        zone_mid = (z["top"] + z["bottom"]) / 2
        buffer = zone_mid * 0.001

        buffered_top = z["top"] + buffer
        buffered_bottom = z["bottom"] - buffer

        for j in range(start_check, len(df)):
            candle = df.iloc[j]

            # SBR/RBS: body closes through the zone → broken, flip direction
            body_bottom = min(candle['open'], candle['close'])
            body_top = max(candle['open'], candle['close'])

            if z["direction"] == "demand" and body_bottom < z["bottom"]:
                # Bearish body closed below demand zone → broken → becomes supply
                z["fresh"] = False
                new_zones.append({
                    "type": "FLIP", "direction": "supply",
                    "top": z["top"], "bottom": z["bottom"],
                    "bar_index": j, "fresh": True, "miss": False,
                })
                break
            if z["direction"] == "supply" and body_top > z["top"]:
                # Bullish body closed above supply zone → broken → becomes demand
                z["fresh"] = False
                new_zones.append({
                    "type": "FLIP", "direction": "demand",
                    "top": z["top"], "bottom": z["bottom"],
                    "bar_index": j, "fresh": True, "miss": False,
                })
                break

            # Check wick touch WITH mitigation buffer
            wick_touches = (candle['low'] <= buffered_top
                           and candle['high'] >= buffered_bottom)

            if wick_touches:
                z["fresh"] = False
                break

            if j - start_check < miss_window:
                miss_count += 1

        if z["fresh"] and miss_count >= miss_window:
            z["miss"] = True

    # Add SBR/RBS flipped zones and check their freshness
    if new_zones:
        zones.extend(new_zones)
        for nz in new_zones:
            start_check = nz["bar_index"] + 1
            miss_count = 0
            zone_mid = (nz["top"] + nz["bottom"]) / 2
            buf = zone_mid * 0.001
            for j in range(start_check, len(df)):
                candle = df.iloc[j]
                wick_touches = (candle['low'] <= nz["top"] + buf
                                and candle['high'] >= nz["bottom"] - buf)
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

    Sorted by strength: FLIP zones first, then MISS, then most recent.
    """
    zones = find_zones(df, lookback)
    zones = mark_freshness(zones, df)
    filtered = [z for z in zones if z["fresh"] and z["direction"] == direction]
    # FLIP zones highest priority, then MISS, then recency
    filtered.sort(key=lambda z: (
        -int(z["type"] == "FLIP"),
        -int(z["miss"]),
        -z["bar_index"],
    ))
    return filtered


# =====================
# Arrival Physics Filter (Gap 1 - Sniper)
# =====================

def analyze_arrival(df, zone_price, lookback=3):
    """Check if price arrived at zone with compression (good) or momentum (bad).

    Calculates avg body size over last 50 candles, then checks if any of the
    last *lookback* candles has a body > 2.5x average (Marubozu). If so,
    the zone is invalidated — institutional momentum is breaking through.

    Returns:
        True if arrival is compressed (safe to trade).
        False if momentum arrival detected (invalidated).
    """
    if len(df) < lookback + 1:
        return True  # insufficient data, don't block

    # Average body size over last 50 (or available) candles
    body_window = min(50, len(df))
    bodies = (df['close'].iloc[-body_window:] - df['open'].iloc[-body_window:]).abs()
    avg_body = bodies.mean()

    if avg_body <= 0:
        return True  # flat market, no momentum

    # Check last N candles for Marubozu (body > 2.5x average)
    recent = df.iloc[-lookback:]
    for _, c in recent.iterrows():
        body = abs(c['close'] - c['open'])
        if body > 2.5 * avg_body:
            return False  # momentum arrival — invalidate

    return True  # compression arrival — safe


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
# Multi-Timeframe Storyline (Gap 5 - Sniper)
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


def check_roadblocks(entry_price, direction, fresh_zones, risk_distance):
    """Hard 1:2 RR filter — kill the trade if no clear sky to target.

    Scans for the nearest opposing fresh zone (the roadblock).
    If distance to that roadblock < 2.0 * risk_distance, return False (kill).
    Returns True if road is clear (RR >= 1:2).
    """
    if risk_distance <= 0:
        return True

    if direction == "BUY":
        blockers = [z for z in fresh_zones
                    if z["direction"] == "supply" and z["bottom"] > entry_price]
        if not blockers:
            return True  # no opposing zones = clear sky
        blockers.sort(key=lambda z: z["bottom"])
        nearest_dist = blockers[0]["bottom"] - entry_price
    else:
        blockers = [z for z in fresh_zones
                    if z["direction"] == "demand" and z["top"] < entry_price]
        if not blockers:
            return True
        blockers.sort(key=lambda z: -z["top"])
        nearest_dist = entry_price - blockers[0]["top"]

    return nearest_dist >= 2.0 * risk_distance


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
# Retest Confirmation (Gap 3 kept + enhanced)
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

    Returns dict {swept: bool, wick_level: float} so SL can be placed
    below/above the sweep wick for maximum protection.
    """
    result = {"swept": False, "wick_level": None}

    if len(df) < lookback:
        return result

    start = max(0, len(df) - lookback)

    for i in range(start, len(df)):
        c = df.iloc[i]
        if direction == "BUY":
            if c['low'] < swing_low and min(c['open'], c['close']) >= swing_low:
                result["swept"] = True
                if result["wick_level"] is None or c['low'] < result["wick_level"]:
                    result["wick_level"] = c['low']
        elif direction == "SELL":
            if c['high'] > swing_high and max(c['open'], c['close']) <= swing_high:
                result["swept"] = True
                if result["wick_level"] is None or c['high'] > result["wick_level"]:
                    result["wick_level"] = c['high']
    return result


# =====================
# Signal Generation — MSNR Sniper Protocol
# =====================

def _compute_confidence(sweep_detected, engulfing, zone, fvg_match):
    """Compute confidence tier based on trigger quality.

    Gold Tier: Inducement Sweep + Engulfing → HIGH
    Silver Tier: Engulfing Only → MEDIUM
    Otherwise: LOW (should typically be filtered out by layers above)
    """
    if sweep_detected and engulfing:
        return "high"
    if engulfing:
        return "medium"
    return "low"


def get_smc_signal(df_l, df_h, pair, risk_pips=50):
    """Generate SMC trading signal using the MSNR Sniper Protocol.

    5-Layer Invalidation Filter:
      Layer 1 (Zone):    Fresh V-Level or FLIP zone? (If No → None)
      Layer 2 (Trend):   Matches H4 Storyline bias? (If No → None)
      Layer 3 (Physics): Compression arrival, not momentum? (If No → None)
      Layer 4 (Space):   RR > 1:2 to nearest roadblock? (If No → None)
      Layer 5 (Trigger): Gold=Sweep+Engulfing (HIGH) / Silver=Engulfing (MEDIUM)

    Returns signal dict or None. Dict shape unchanged for scanner.py compat.
    """
    if df_l.empty or df_h.empty or len(df_l) < 23 or len(df_h) < 20:
        return None

    pip_val = get_pip_value(pair)
    max_risk_price = risk_pips / pip_val

    # --- Layer 2: H4 Storyline ---
    storyline = detect_storyline(df_h, df_l)
    if storyline is None:
        return None
    bias = storyline["bias"]

    # Swing points + BOS
    swing_high, swing_low = find_swing_points(df_l)
    if swing_high is None:
        return None

    bullish_bos, bearish_bos, bull_sweep, bear_sweep = detect_bos(
        df_l, swing_high, swing_low
    )

    # H4 Gatekeeper: FORBID trading against H4 bias
    # If H4 says BEAR, no BUY signals allowed (and vice versa)
    if bias == "BULL" and not bullish_bos:
        return None
    if bias == "BEAR" and not bearish_bos:
        return None

    # FVG (confluence bonus, not hard gate)
    fvg = detect_fvg(df_l)

    c = df_l.iloc[-1]
    storyline_tp = storyline.get("tp_target")

    # All fresh LTF zones for roadblock scanning
    all_fresh_ltf = find_zones(df_l, lookback=40)
    all_fresh_ltf = mark_freshness(all_fresh_ltf, df_l)
    all_fresh_ltf = [z for z in all_fresh_ltf if z["fresh"]]

    if bias == "BULL":
        # --- Layer 1: Fresh Zone ---
        fresh = get_fresh_zones(df_l, "demand")
        zone = fresh[0] if fresh else None
        if zone is None:
            return None  # No fresh zone = no trade

        entry_price = zone["top"]
        tp_target = storyline_tp if storyline_tp else df_h['high'].max()

        # --- Layer 3: Arrival Physics ---
        if not analyze_arrival(df_l, zone["top"]):
            return None  # Momentum arrival — invalidated

        # --- Layer 4: Roadblock RR Check ---
        risk_distance = abs(entry_price - zone["bottom"])
        if risk_distance <= 0:
            risk_distance = max_risk_price
        if not check_roadblocks(entry_price, "BUY", all_fresh_ltf, risk_distance):
            return None  # RR < 1:2 to nearest roadblock — kill trade

        # Soft roadblock check (for confidence)
        roadblock_near = _check_roadblock(all_fresh_ltf, "BULL", entry_price, tp_target)

        # Retest check
        retest_ok = c['low'] <= zone['top'] and c['high'] >= zone['bottom']

        # --- Layer 5: Trigger ---
        engulfing = None
        if retest_ok:
            engulfing = detect_engulfing(df_l, zone)

        # Inducement / Sweep check
        induce = detect_inducement_swept(df_l, swing_high, swing_low, "BUY")
        sweep_detected = induce["swept"]

        # Must have at least engulfing to take the trade
        if not engulfing:
            return None

        fvg_match = fvg == "BULL_FVG"
        confidence = _compute_confidence(sweep_detected, engulfing, zone, fvg_match)

        # SL placement: below sweep wick if available, else below zone
        if sweep_detected and induce["wick_level"] is not None:
            sl_anchor = induce["wick_level"]
        else:
            sl_anchor = zone["bottom"]

        limit_levels = calculate_levels(
            "BUY", entry_price, swing_high, sl_anchor, max_risk_price, tp_target
        )
        market_levels = calculate_levels(
            "BUY", c['close'], swing_high, sl_anchor, max_risk_price, tp_target
        )

        return {
            "act": "BUY",
            "limit_e": entry_price,
            "limit_sl": limit_levels["sl"],
            "market_e": c['close'],
            "market_sl": market_levels["sl"],
            "tp": limit_levels["tp"],
            "confidence": confidence,
            "zone_type": zone["type"],
            "miss": zone["miss"],
            "sweep": sweep_detected,
            "arrival": "compression",
        }

    if bias == "BEAR":
        # --- Layer 1: Fresh Zone ---
        fresh = get_fresh_zones(df_l, "supply")
        zone = fresh[0] if fresh else None
        if zone is None:
            return None

        entry_price = zone["bottom"]
        tp_target = storyline_tp if storyline_tp else df_h['low'].min()

        # --- Layer 3: Arrival Physics ---
        if not analyze_arrival(df_l, zone["bottom"]):
            return None

        # --- Layer 4: Roadblock RR Check ---
        risk_distance = abs(zone["top"] - entry_price)
        if risk_distance <= 0:
            risk_distance = max_risk_price
        if not check_roadblocks(entry_price, "SELL", all_fresh_ltf, risk_distance):
            return None

        roadblock_near = _check_roadblock(all_fresh_ltf, "BEAR", entry_price, tp_target)

        retest_ok = c['high'] >= zone['bottom'] and c['low'] <= zone['top']

        engulfing = None
        if retest_ok:
            engulfing = detect_engulfing(df_l, zone)

        induce = detect_inducement_swept(df_l, swing_high, swing_low, "SELL")
        sweep_detected = induce["swept"]

        if not engulfing:
            return None

        fvg_match = fvg == "BEAR_FVG"
        confidence = _compute_confidence(sweep_detected, engulfing, zone, fvg_match)

        if sweep_detected and induce["wick_level"] is not None:
            sl_anchor = induce["wick_level"]
        else:
            sl_anchor = zone["top"]

        limit_levels = calculate_levels(
            "SELL", entry_price, sl_anchor, swing_low, max_risk_price, tp_target
        )
        market_levels = calculate_levels(
            "SELL", c['close'], sl_anchor, swing_low, max_risk_price, tp_target
        )

        return {
            "act": "SELL",
            "limit_e": entry_price,
            "limit_sl": limit_levels["sl"],
            "market_e": c['close'],
            "market_sl": market_levels["sl"],
            "tp": limit_levels["tp"],
            "confidence": confidence,
            "zone_type": zone["type"],
            "miss": zone["miss"],
            "sweep": sweep_detected,
            "arrival": "compression",
        }

    return None
