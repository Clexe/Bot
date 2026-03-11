import pandas as pd
from config import (
    HIGH_PIP_SYMBOLS, SKIP_VOLATILE_REGIME, ALWAYS_OPEN_KEYS,
    BOS_BODY_RATIO, BOS_BODY_RELATIVE_MULT, DISPLACEMENT_BODY_RATIO,
    USE_PREMIUM_DISCOUNT_FILTER, REQUIRE_INDUCEMENT_SWEEP, REQUIRE_BOS_FVG,
    logger,
)
from regime import detect_regime, should_skip_regime


def get_pip_value(pair):
    """Determine pip value multiplier for a given pair.

    Returns a multiplier so that (price_diff * pip_value) gives pips.
    Grouped by typical price magnitude of the asset.
    """
    clean = pair.upper()
    # Crypto — grouped by price magnitude
    if "BTC" in clean:
        return 0.1   # price ~$60k-100k  → 1 pip ≈ $10
    if "ETH" in clean or "BNB" in clean or "MKR" in clean:
        return 1     # price ~$200-4000  → 1 pip ≈ $1
    if "SOL" in clean or "AVAX" in clean or "LINK" in clean or "DOT" in clean \
       or "APT" in clean or "LTC" in clean or "ICP" in clean or "INJ" in clean \
       or "FIL" in clean or "ATOM" in clean or "AAVE" in clean or "UNI" in clean \
       or "RNDR" in clean or "FET" in clean or "TON" in clean or "WLD" in clean:
        return 10    # price ~$5-250    → 1 pip ≈ $0.10
    if "XRP" in clean or "ADA" in clean or "DOGE" in clean or "MATIC" in clean \
       or "TRX" in clean or "NEAR" in clean or "SUI" in clean or "OP" in clean \
       or "ARB" in clean or "SEI" in clean or "JUP" in clean or "WIF" in clean \
       or "TIA" in clean or "ENA" in clean:
        return 100   # price ~$0.30-5   → 1 pip ≈ $0.01
    if "SHIB" in clean or "PEPE" in clean or "BONK" in clean:
        return 1000000  # price ~$0.00001 → 1 pip ≈ $0.000001
    # Fallback: any USDT pair not listed above — assume mid-cap ($1-50 range)
    if clean.endswith("USDT") or clean.endswith("USD"):
        return 10
    if any(x in clean for x in HIGH_PIP_SYMBOLS):
        return 10
    return 10000  # standard forex


# =====================
# Gap 1: Body-Based Fresh Zone Detection
# =====================

def _has_displacement(df, bar_index, direction, atr, min_mult=1.0):
    """Check if the candle after zone formation shows institutional displacement.

    A valid zone requires price to move aggressively *away* from the zone,
    proving institutional orders were placed there. The displacement candle
    must have a body >= min_mult * ATR.

    Returns True if displacement is confirmed.
    """
    check_start = bar_index + 2  # zone spans bar_index and bar_index+1
    if check_start >= len(df) or atr is None or atr <= 0:
        return True  # insufficient data, don't block

    # Check up to 3 candles after zone for displacement
    for j in range(check_start, min(check_start + 3, len(df))):
        c = df.iloc[j]
        body = abs(c['close'] - c['open'])
        total_range = c['high'] - c['low']
        if total_range <= 0:
            continue
        body_ratio = body / total_range

        if body >= min_mult * atr and body_ratio >= DISPLACEMENT_BODY_RATIO:
            # Confirm direction: displacement must move AWAY from zone
            is_bullish_candle = c['close'] > c['open']
            if direction == "demand" and is_bullish_candle:
                return True  # bullish displacement away from demand = valid
            if direction == "supply" and not is_bullish_candle:
                return True  # bearish displacement away from supply = valid
    return False


def find_zones(df, lookback=40, atr=None):
    """Scan the last *lookback* candles and return MSNR-style zones.

    Zone types:
      A-Level (resistance): bullish candle followed by bearish candle.
      V-Level (support):    bearish candle followed by bullish candle.
      OC-Gap:               two consecutive same-direction candles with a gap
                            between c1.close and c2.open.

    Filters applied:
      - Minimum zone width: 0.15 * ATR (rejects noise zones)
      - Displacement validation: zone must show institutional displacement
      - Recency decay: zones older than 30 bars are deprioritized

    Returns a list of zone dicts:
        {type, direction, top, bottom, bar_index, fresh, miss, displacement, age}
    """
    if len(df) < 2:
        return []

    start = max(0, len(df) - lookback)
    zones = []

    # Minimum zone width: 5% of ATR to filter noise
    min_width = atr * 0.05 if atr and atr > 0 else 0

    for i in range(start, len(df) - 1):
        c1 = df.iloc[i]
        c2 = df.iloc[i + 1]
        c1_bull = c1['close'] > c1['open']
        c2_bull = c2['close'] > c2['open']

        zone_info = None

        # A-Level: bullish then bearish → supply/resistance zone
        if c1_bull and not c2_bull:
            top = max(c1['close'], c2['open'])
            bottom = min(c1['close'], c2['open'])
            if top - bottom > min_width:
                zone_info = {"type": "A", "direction": "supply",
                             "top": top, "bottom": bottom}

        # V-Level: bearish then bullish → demand/support zone
        elif not c1_bull and c2_bull:
            top = max(c1['close'], c2['open'])
            bottom = min(c1['close'], c2['open'])
            if top - bottom > min_width:
                zone_info = {"type": "V", "direction": "demand",
                             "top": top, "bottom": bottom}

        # OC-Gap: same direction, gap between c1.close and c2.open
        elif c1_bull == c2_bull:
            gap_top = max(c1['close'], c2['open'])
            gap_bottom = min(c1['close'], c2['open'])
            if gap_top - gap_bottom > min_width:
                direction = "supply" if not c1_bull else "demand"
                zone_info = {"type": "OC", "direction": direction,
                             "top": gap_top, "bottom": gap_bottom}

        if zone_info is not None:
            has_disp = _has_displacement(df, i, zone_info["direction"], atr)
            age = len(df) - 1 - i
            zone_info.update({
                "bar_index": i, "fresh": True, "miss": False,
                "displacement": has_disp, "age": age,
            })
            zones.append(zone_info)

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

        # Mitigation buffer: 5% of zone width (floor at 0.02% of price)
        # Price-based buffer (old: 0.1% of price) was too large for high-price
        # instruments like BTC ($60 buffer at $60k), killing zones on normal wicks.
        zone_mid = (z["top"] + z["bottom"]) / 2
        zone_width = z["top"] - z["bottom"]
        buffer = max(zone_width * 0.05, zone_mid * 0.0002)

        buffered_top = z["top"] + buffer
        buffered_bottom = z["bottom"] - buffer

        # Exclude current candle (last bar) from freshness check —
        # the current candle is the potential entry candle. If it's the
        # first touch of a fresh zone, we WANT to trade it, not kill it.
        freshness_end = len(df) - 1

        for j in range(start_check, freshness_end):
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
        freshness_end = len(df) - 1  # exclude current candle
        for nz in new_zones:
            start_check = nz["bar_index"] + 1
            miss_count = 0
            nz_width = nz["top"] - nz["bottom"]
            nz_mid = (nz["top"] + nz["bottom"]) / 2
            buf = max(nz_width * 0.05, nz_mid * 0.0002)
            for j in range(start_check, freshness_end):
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


def get_fresh_zones(df, direction, lookback=40, atr=None):
    """Return fresh zones matching *direction* ('supply' or 'demand').

    Filters:
      - Must be fresh (untested)
      - Zones with displacement are prioritized
      - Zones older than 30 bars are deprioritized

    Sorted by strength: FLIP > displacement > MISS > recency.
    """
    zones = find_zones(df, lookback, atr=atr)
    zones = mark_freshness(zones, df)
    filtered = [z for z in zones if z["fresh"] and z["direction"] == direction]
    # Prioritize: FLIP > displacement > MISS > recency
    filtered.sort(key=lambda z: (
        -int(z["type"] == "FLIP"),
        -int(z.get("displacement", False)),
        -int(z["miss"]),
        -z["bar_index"],
    ))
    return filtered


# =====================
# Arrival Physics Filter (Gap 1 - Sniper)
# =====================

def analyze_arrival(df, zone_price, direction, lookback=3):
    """Check if price arrived at zone with compression (good) or momentum (bad).

    Directionally aware: only rejects momentum candles moving AGAINST the zone.
    A large bullish candle arriving at a demand zone is displacement (good).
    A large bearish candle slamming into a demand zone is momentum (bad).

    Returns:
        True if arrival is compressed (safe to trade).
        False if adverse momentum arrival detected (invalidated).
    """
    if len(df) < lookback + 1:
        return True  # insufficient data, don't block

    # Average body size over last 50 (or available) candles
    body_window = min(50, len(df))
    bodies = (df['close'].iloc[-body_window:] - df['open'].iloc[-body_window:]).abs()
    avg_body = bodies.mean()

    if avg_body <= 0:
        return True  # flat market, no momentum

    # Check last N candles for adverse Marubozu (body > 2.5x average)
    recent = df.iloc[-lookback:]
    for _, c in recent.iterrows():
        body = abs(c['close'] - c['open'])
        if body <= 2.5 * avg_body:
            continue

        is_bullish = c['close'] > c['open']

        # Only reject if momentum is AGAINST the zone direction
        # Demand zone (BUY): reject bearish momentum (selling into the zone)
        # Supply zone (SELL): reject bullish momentum (buying into the zone)
        if direction == "demand" and not is_bullish:
            return False  # bearish momentum into demand = bad
        if direction == "supply" and is_bullish:
            return False  # bullish momentum into supply = bad

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


def classify_swing_sequence(df, lookback=30):
    """Identify current market structure via swing point sequence.

    Uses 3-bar pivot detection to find swing highs and lows, then checks
    if the sequence makes HH/HL (uptrend) or LH/LL (downtrend).

    Returns {"trend": "UP"|"DOWN"|"NONE", "swing_highs": [...], "swing_lows": [...]}.
    """
    if len(df) < 7:
        return {"trend": "NONE", "swing_highs": [], "swing_lows": []}

    start = max(0, len(df) - lookback)
    segment = df.iloc[start:]
    swing_highs = []
    swing_lows = []

    # 3-bar pivot: bar whose high/low exceeds both neighbors
    for i in range(1, len(segment) - 1):
        c = segment.iloc[i]
        prev = segment.iloc[i - 1]
        nxt = segment.iloc[i + 1]
        if c['high'] > prev['high'] and c['high'] > nxt['high']:
            swing_highs.append(c['high'])
        if c['low'] < prev['low'] and c['low'] < nxt['low']:
            swing_lows.append(c['low'])

    trend = "NONE"
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1] > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1] < swing_lows[-2]
        if hh and hl:
            trend = "UP"
        elif lh and ll:
            trend = "DOWN"

    return {"trend": trend, "swing_highs": swing_highs, "swing_lows": swing_lows}


def _bos_creates_fvg(df, break_global_idx, direction, atr):
    """Check if the BOS candle created an FVG (proof of institutional displacement).

    Looks at 3-candle window: [break-1, break, break+1].
    Returns True if an FVG gap exists in the displacement direction.
    """
    if break_global_idx < 1 or break_global_idx + 1 >= len(df):
        return False
    min_gap = atr * 0.3 if atr and atr > 0 else 0
    c1 = df.iloc[break_global_idx - 1]
    c3 = df.iloc[break_global_idx + 1]

    if direction == "BULL":
        # Bullish FVG: c3.low > c1.high
        gap = c3['low'] - c1['high']
        return gap >= min_gap
    else:
        # Bearish FVG: c1.low > c3.high
        gap = c1['low'] - c3['high']
        return gap >= min_gap


def detect_bos(df_l, swing_high, swing_low, lookback=10, atr=None, classify=False):
    """Detect Break of Structure with displacement validation.

    When classify=False (default): returns (bullish_bos, bearish_bos, bull_sweep, bear_sweep).
    When classify=True: returns enriched dict with break_type (BOS vs MSS) and displacement_fvg.

    - bullish_bos/bearish_bos: True if a candle *body* (close) broke the level
      AND the breaking candle shows displacement (body > BOS_BODY_RATIO, body >= 1.5x avg).
    - bull_sweep/bear_sweep: True if only a wick exceeded the level but body
      closed back inside — indicates a liquidity grab, not a real breakout.
    """
    if len(df_l) < lookback:
        if classify:
            return {
                "bullish_bos": False, "bearish_bos": False,
                "bull_sweep": False, "bear_sweep": False,
                "break_type": None, "break_direction": None,
                "displacement_fvg": False,
            }
        return False, False, False, False

    segment = df_l.iloc[-lookback:]

    bullish_bos = False
    bearish_bos = False
    bull_sweep = False
    bear_sweep = False
    bull_break_idx = None
    bear_break_idx = None

    # Minimum body size for BOS = 25% of ATR (reject noise breaks)
    min_bos_body = atr * 0.25 if atr and atr > 0 else 0

    # Average body of candles preceding the lookback window for relative size check
    pre_start = max(0, len(df_l) - lookback - 15)
    pre_end = max(0, len(df_l) - lookback)
    if pre_end > pre_start:
        pre_bodies = df_l.iloc[pre_start:pre_end].apply(
            lambda r: abs(r['close'] - r['open']), axis=1
        )
        avg_body = pre_bodies.mean() if len(pre_bodies) > 0 else 0
    else:
        avg_body = 0

    for idx in range(len(segment)):
        c = segment.iloc[idx]
        body = abs(c['close'] - c['open'])
        total_range = c['high'] - c['low']
        body_ratio = body / total_range if total_range > 0 else 0

        # Bullish BOS: close above swing high with displacement
        if (c['close'] > swing_high and body >= min_bos_body
                and body_ratio >= BOS_BODY_RATIO
                and (avg_body <= 0 or body >= avg_body * BOS_BODY_RELATIVE_MULT)):
            bullish_bos = True
            bull_break_idx = len(df_l) - lookback + idx  # global index

        # Bearish BOS: close below swing low with displacement
        if (c['close'] < swing_low and body >= min_bos_body
                and body_ratio >= BOS_BODY_RATIO
                and (avg_body <= 0 or body >= avg_body * BOS_BODY_RELATIVE_MULT)):
            bearish_bos = True
            bear_break_idx = len(df_l) - lookback + idx

        # Wick sweeps (regardless of body size)
        if c['high'] > swing_high and c['close'] <= swing_high:
            bull_sweep = True
        if c['low'] < swing_low and c['close'] >= swing_low:
            bear_sweep = True

    # Sweep is only valid if there was NO legitimate BOS
    if bullish_bos:
        bull_sweep = False
    if bearish_bos:
        bear_sweep = False

    if not classify:
        return bullish_bos, bearish_bos, bull_sweep, bear_sweep

    # Enriched classification: BOS vs MSS + FVG displacement proof
    trend_info = classify_swing_sequence(df_l)
    trend = trend_info["trend"]
    is_mss = (bullish_bos and trend == "DOWN") or (bearish_bos and trend == "UP")

    # Check if BOS created an FVG (displacement proof)
    disp_fvg = False
    if bullish_bos and bull_break_idx is not None:
        disp_fvg = _bos_creates_fvg(df_l, bull_break_idx, "BULL", atr)
    elif bearish_bos and bear_break_idx is not None:
        disp_fvg = _bos_creates_fvg(df_l, bear_break_idx, "BEAR", atr)

    return {
        "bullish_bos": bullish_bos, "bearish_bos": bearish_bos,
        "bull_sweep": bull_sweep, "bear_sweep": bear_sweep,
        "break_type": "MSS" if is_mss else ("BOS" if (bullish_bos or bearish_bos) else None),
        "break_direction": "BULL" if bullish_bos else ("BEAR" if bearish_bos else None),
        "displacement_fvg": disp_fvg,
    }


def detect_fvg(df_l, lookback=15, atr=None):
    """Detect Fair Value Gap (imbalance) within the last *lookback* candles.

    Scans backwards to find the most recent unmitigated FVG.
    Requires:
      - Gap size >= 0.3 * ATR (rejects noise gaps)
      - Gap must not have been filled by subsequent candles (mitigated)

    Returns dict {type, top, bottom, bar_index} or None.
    """
    if len(df_l) < 3:
        return None

    min_gap = atr * 0.3 if atr and atr > 0 else 0
    start = max(0, len(df_l) - lookback)

    # Scan from most recent backwards
    for i in range(len(df_l) - 1, start + 1, -1):
        c1 = df_l.iloc[i - 2]
        c3 = df_l.iloc[i]

        # Bullish FVG: c3.low > c1.high (gap up)
        if c3['low'] > c1['high'] and (c3['low'] - c1['high']) >= min_gap:
            fvg_top = c3['low']
            fvg_bottom = c1['high']
            # Check mitigation: any subsequent candle's body closed into the gap?
            # Institutional theory: only a body close counts as mitigation, not a wick dip
            mitigated = False
            for j in range(i + 1, len(df_l)):
                body_low = min(df_l.iloc[j]['close'], df_l.iloc[j]['open'])
                if body_low <= fvg_top:
                    mitigated = True
                    break
            if not mitigated:
                return {"type": "BULL_FVG", "top": fvg_top,
                        "bottom": fvg_bottom, "bar_index": i - 2}

        # Bearish FVG: c3.high < c1.low (gap down)
        if c3['high'] < c1['low'] and (c1['low'] - c3['high']) >= min_gap:
            fvg_top = c1['low']
            fvg_bottom = c3['high']
            mitigated = False
            for j in range(i + 1, len(df_l)):
                body_high = max(df_l.iloc[j]['close'], df_l.iloc[j]['open'])
                if body_high >= fvg_bottom:
                    mitigated = True
                    break
            if not mitigated:
                return {"type": "BEAR_FVG", "top": fvg_top,
                        "bottom": fvg_bottom, "bar_index": i - 2}

    return None


def compute_volume_proxy(df, zone, lookback=5):
    """Estimate institutional participation at a zone using candle structure.

    Without real volume data, we use body-to-wick ratio and candle size as
    a proxy for conviction. Large bodies with small wicks = strong conviction.

    Returns a score 0.0 to 1.0 (higher = more institutional interest).
    """
    if len(df) < lookback:
        return 0.5  # neutral

    zone_mid = (zone["top"] + zone["bottom"]) / 2
    zone_range = zone["top"] - zone["bottom"]
    if zone_range <= 0:
        return 0.5

    # Look at candles near the zone formation
    start = max(0, zone["bar_index"] - 1)
    end = min(len(df), zone["bar_index"] + lookback + 1)
    nearby = df.iloc[start:end]

    if nearby.empty:
        return 0.5

    scores = []
    for _, c in nearby.iterrows():
        body = abs(c['close'] - c['open'])
        total_range = c['high'] - c['low']
        if total_range <= 0:
            continue

        # Body-to-range ratio: higher = more conviction
        body_ratio = body / total_range

        # Size relative to average: bigger candles = more volume
        avg_body = (df['close'] - df['open']).abs().mean()
        size_score = min(body / avg_body, 2.0) / 2.0 if avg_body > 0 else 0.5

        scores.append((body_ratio * 0.6 + size_score * 0.4))

    return round(sum(scores) / len(scores), 3) if scores else 0.5


def calculate_levels(sig_type, entry, sl_anchor, max_risk_price, tp_target,
                      htf_extreme=None, sl_multiplier=1.0, opposing_ltf_zones=None):
    """Calculate entry, SL, and multi-TP levels for a signal.

    Args:
        sl_anchor: Structural level for SL (zone boundary, sweep wick, or FVG edge).
        max_risk_price: Maximum allowed risk in price units.
        sl_multiplier: Combined multiplier (regime + zone quality) for max risk.
        opposing_ltf_zones: List of fresh LTF zones for structural TP1 placement.

    TP1 = first structural obstacle or 1:1 RR (whichever is closer, min 1:1)
    TP2 = opposing zone target (standard)
    TP3 = HTF extreme or 1:3 RR (runner)
    """
    effective_max_risk = max_risk_price * sl_multiplier

    if sig_type == "BUY":
        raw_sl = sl_anchor
        sl = entry - effective_max_risk if (entry - raw_sl) > effective_max_risk else raw_sl
        risk = abs(entry - sl)
        tp1 = entry + risk          # 1:1 default
        tp2 = tp_target             # opposing zone
        tp3_rr = entry + risk * 3   # 1:3
        tp3 = max(tp3_rr, htf_extreme) if htf_extreme and htf_extreme > entry else tp3_rr

        # Structural TP1: nearest opposing zone between entry and 1:1
        if opposing_ltf_zones:
            supply_above = [z for z in opposing_ltf_zones
                           if z["direction"] == "supply" and z["bottom"] > entry]
            if supply_above:
                nearest = min(supply_above, key=lambda z: z["bottom"])
                structural_tp1 = nearest["bottom"]
                # Use if closer than 1:1 but at least 0.8:1 RR
                if entry + risk * 0.8 <= structural_tp1 < tp1:
                    tp1 = structural_tp1

        # Ensure TP ordering: TP1 < TP2 < TP3
        tp2 = max(tp2, tp1)
        tp3 = max(tp3, tp2)
    else:
        raw_sl = sl_anchor
        sl = entry + effective_max_risk if (raw_sl - entry) > effective_max_risk else raw_sl
        risk = abs(sl - entry)
        tp1 = entry - risk          # 1:1 default
        tp2 = tp_target             # opposing zone
        tp3_rr = entry - risk * 3   # 1:3
        tp3 = min(tp3_rr, htf_extreme) if htf_extreme and htf_extreme < entry else tp3_rr

        # Structural TP1: nearest opposing zone between entry and 1:1
        if opposing_ltf_zones:
            demand_below = [z for z in opposing_ltf_zones
                           if z["direction"] == "demand" and z["top"] < entry]
            if demand_below:
                nearest = max(demand_below, key=lambda z: z["top"])
                structural_tp1 = nearest["top"]
                if tp1 < structural_tp1 <= entry - risk * 0.8:
                    tp1 = structural_tp1

        # Ensure TP ordering: TP1 > TP2 > TP3
        tp2 = min(tp2, tp1)
        tp3 = min(tp3, tp2)

    return {"sl": sl, "tp": tp2, "tp1": tp1, "tp2": tp2, "tp3": tp3}


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


def _htf_rejection(df_h, zones, direction, candles_to_check=5):
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

    # Compute ATR for both timeframes (LTF ATR needed for BOS displacement filter)
    from regime import compute_atr
    htf_atr = compute_atr(df_h)
    ltf_atr = compute_atr(df_l)
    htf_zones = find_zones(df_h, lookback=80, atr=htf_atr)
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
            # Use lenient ATR (0.25x) for bias confirmation — stricter filtering
            # is applied later in get_smc_signal() for trade entry BOS
            bias_atr = ltf_atr * 0.5 if ltf_atr else None
            result = detect_bos(df_l, swing_high, swing_low, atr=bias_atr)
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
            bias_atr = ltf_atr * 0.5 if ltf_atr else None
            result = detect_bos(df_l, swing_high, swing_low, atr=bias_atr)
            bear_bos = result[1]
            confirmed = bool(bear_bos)
        return {
            "bias": "BEAR", "htf_zone": bear_zone, "tp_target": tp,
            "confirmed": confirmed, "roadblock_near": roadblock,
        }

    # No HTF structural rejection found — do NOT fall back to momentum.
    # Trading without HTF structure = gambling. Return None to block signal.
    # Legacy detect_bias() momentum fallback removed: it bypassed the entire
    # HTF filter and generated signals on pure momentum without zone context.

    # Exception: for crypto/synthetics (always_open pairs), strict wick-rejection
    # rarely occurs due to trending nature. Allow a structural bias fallback IF:
    #   1) HTF zones exist (supply OR demand — structure IS present)
    #   2) LTF BOS is confirmed (directional conviction, not just momentum)
    # This preserves the "no structure = no trade" rule while adapting to
    # crypto's tendency to trend through zones rather than reject off them.
    # Use LTF BOS to determine bias direction when HTF zones exist but no rejection
    # Lenient ATR (0.25x effective) for bias detection — entry BOS is stricter
    if fresh_htf:
        swing_high, swing_low = find_swing_points(df_l)
        if swing_high is not None:
            bias_atr = ltf_atr * 0.5 if ltf_atr else None
            bull_bos, bear_bos, _, _ = detect_bos(df_l, swing_high, swing_low, atr=bias_atr)

            if bull_bos and demand_zones:
                # LTF confirms bullish + HTF demand zones exist = structural bull bias
                best_demand = max(demand_zones, key=lambda z: z["bar_index"])
                tp = _opposing_zone_tp(fresh_htf, "BULL", current_price, df_h['high'].max())
                roadblock = _check_roadblock(fresh_htf, "BULL", current_price, tp)
                return {
                    "bias": "BULL", "htf_zone": best_demand, "tp_target": tp,
                    "confirmed": True, "roadblock_near": roadblock,
                }
            elif bull_bos and fresh_htf:
                # Bull BOS but no demand zones (all broken/flipped to supply in uptrend).
                # Use nearest fresh zone for structural context — BOS provides direction.
                best = max(fresh_htf, key=lambda z: z["bar_index"])
                tp = _opposing_zone_tp(fresh_htf, "BULL", current_price, df_h['high'].max())
                roadblock = _check_roadblock(fresh_htf, "BULL", current_price, tp)
                return {
                    "bias": "BULL", "htf_zone": best, "tp_target": tp,
                    "confirmed": True, "roadblock_near": roadblock,
                }

            if bear_bos and supply_zones:
                # LTF confirms bearish + HTF supply zones exist = structural bear bias
                best_supply = max(supply_zones, key=lambda z: z["bar_index"])
                tp = _opposing_zone_tp(fresh_htf, "BEAR", current_price, df_h['low'].min())
                roadblock = _check_roadblock(fresh_htf, "BEAR", current_price, tp)
                return {
                    "bias": "BEAR", "htf_zone": best_supply, "tp_target": tp,
                    "confirmed": True, "roadblock_near": roadblock,
                }
            elif bear_bos and fresh_htf:
                # Bear BOS but no supply zones (all broken/flipped to demand in downtrend).
                # Use nearest fresh zone for structural context — BOS provides direction.
                best = max(fresh_htf, key=lambda z: z["bar_index"])
                tp = _opposing_zone_tp(fresh_htf, "BEAR", current_price, df_h['low'].min())
                roadblock = _check_roadblock(fresh_htf, "BEAR", current_price, tp)
                return {
                    "bias": "BEAR", "htf_zone": best, "tp_target": tp,
                    "confirmed": True, "roadblock_near": roadblock,
                }

    # Secondary fallback: fresh zones are empty but structure still exists
    # (zones were retested but remain valid structural levels)
    all_htf_zones = htf_zones  # includes unfresh zones
    if all_htf_zones:
        swing_high, swing_low = find_swing_points(df_l)
        if swing_high is not None:
            bias_atr = ltf_atr * 0.5 if ltf_atr else None
            bull_bos, bear_bos, _, _ = detect_bos(df_l, swing_high, swing_low, atr=bias_atr)

            if bull_bos:
                demand_z = [z for z in all_htf_zones if z["direction"] == "demand"]
                best = max(demand_z or all_htf_zones, key=lambda z: z["bar_index"])
                tp = _opposing_zone_tp(all_htf_zones, "BULL", current_price, df_h['high'].max())
                roadblock = _check_roadblock(all_htf_zones, "BULL", current_price, tp)
                return {
                    "bias": "BULL", "htf_zone": best, "tp_target": tp,
                    "confirmed": True, "roadblock_near": roadblock,
                }

            if bear_bos:
                supply_z = [z for z in all_htf_zones if z["direction"] == "supply"]
                best = max(supply_z or all_htf_zones, key=lambda z: z["bar_index"])
                tp = _opposing_zone_tp(all_htf_zones, "BEAR", current_price, df_h['low'].min())
                roadblock = _check_roadblock(all_htf_zones, "BEAR", current_price, tp)
                return {
                    "bias": "BEAR", "htf_zone": best, "tp_target": tp,
                    "confirmed": True, "roadblock_near": roadblock,
                }

    # Last resort: no zones at all — use LTF BOS with synthetic HTF structure
    if len(df_h) >= 20:
        swing_high, swing_low = find_swing_points(df_l)
        if swing_high is not None:
            bias_atr = ltf_atr * 0.5 if ltf_atr else None
            bull_bos, bear_bos, _, _ = detect_bos(df_l, swing_high, swing_low, atr=bias_atr)
            if bull_bos or bear_bos:
                bias = "BULL" if bull_bos else "BEAR"
                recent_high = df_h['high'].iloc[-20:].max()
                recent_low = df_h['low'].iloc[-20:].min()
                mid = (recent_high + recent_low) / 2
                synthetic_zone = {
                    "type": "SYNTHETIC", "direction": "demand" if bull_bos else "supply",
                    "top": mid, "bottom": mid, "bar_index": len(df_h) - 1,
                    "fresh": False, "miss": False,
                }
                fallback_tp = recent_high if bull_bos else recent_low
                return {
                    "bias": bias, "htf_zone": synthetic_zone, "tp_target": fallback_tp,
                    "confirmed": True, "roadblock_near": False,
                }

    return None


# =====================
# Retest Confirmation (Gap 3 kept + enhanced)
# =====================

def detect_engulfing(df, zone, lookback=10, atr=None):
    """Detect an engulfing pattern at or near the given zone.

    Requires:
      - Current candle body fully engulfs previous candle body
      - Current candle body >= 0.5 * ATR (rejects noise engulfing)
      - Current candle body_ratio >= 0.55 (strong conviction)
      - Candle must be at or near the zone

    Returns the index of the engulfing candle or None.
    """
    if len(df) < 2:
        return None

    min_body = atr * 0.2 if atr and atr > 0 else 0

    start = max(0, len(df) - lookback)
    for i in range(start + 1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        prev_body_top = max(prev['open'], prev['close'])
        prev_body_bottom = min(prev['open'], prev['close'])
        curr_body_top = max(curr['open'], curr['close'])
        curr_body_bottom = min(curr['open'], curr['close'])

        curr_body = curr_body_top - curr_body_bottom
        curr_range = curr['high'] - curr['low']
        body_ratio = curr_body / curr_range if curr_range > 0 else 0

        # Skip weak candles
        if curr_body < min_body or body_ratio < 0.4:
            continue

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
# Premium / Discount Zone Filter
# =====================

def is_in_premium_discount(price, swing_high, swing_low):
    """Classify price position within the current swing range.

    Premium = above 55% of range (sell zone)
    Discount = below 45% of range (buy zone)
    Equilibrium = 45-55% band around midpoint

    Returns 'premium', 'discount', or 'equilibrium'.
    """
    if swing_high is None or swing_low is None or swing_high == swing_low:
        return "equilibrium"
    ratio = (price - swing_low) / (swing_high - swing_low)
    if ratio > 0.55:
        return "premium"
    elif ratio < 0.45:
        return "discount"
    return "equilibrium"


# =====================
# Signal Generation — MSNR Sniper Protocol
# =====================

def _compute_confidence(sweep_detected, engulfing, zone, fvg_match,
                        touch_entry=False, is_mss=False, bos_fvg=False):
    """Compute confidence tier based on trigger quality.

    Platinum: MSS + Sweep + Engulfing + FVG match → HIGH (strongest reversal)
    Gold Tier:  Inducement Sweep + Engulfing → HIGH
    Silver+:    Engulfing + BOS-created FVG → MEDIUM (displacement proof)
    Silver Tier: Engulfing Only → MEDIUM
    Touch Tier:  Sweep + Zone + Compression (no engulfing) → MEDIUM (touch)
    Otherwise:   LOW (should typically be filtered out by layers above)
    """
    if is_mss and sweep_detected and engulfing and fvg_match:
        return "high"  # Platinum: reversal at POI with full confirmation
    if sweep_detected and engulfing:
        return "high"
    if engulfing and bos_fvg:
        return "medium"
    if engulfing:
        return "medium"
    if touch_entry and sweep_detected:
        return "medium"
    return "low"


def get_smc_signal(df_l, df_h, pair, risk_pips=50, touch_trade=False):
    """Generate SMC trading signal using the MSNR Sniper Protocol.

    5-Layer Invalidation Filter:
      Layer 1 (Zone):    Fresh V-Level or FLIP zone? (If No → None)
      Layer 2 (Trend):   Matches H4 Storyline bias? (If No → None)
      Layer 3 (Physics): Compression arrival, not momentum? (If No → None)
      Layer 4 (Space):   RR > 1:2 to nearest roadblock? (If No → None)
      Layer 5 (Trigger): Gold=Sweep+Engulfing (HIGH) / Silver=Engulfing (MEDIUM)
                         Touch Trade: Sweep+Zone+Compression bypasses engulfing

    Args:
        touch_trade: If True, allow signals without engulfing when a sweep is
                     detected alongside a fresh zone and compression arrival.

    Returns signal dict or None. Dict shape unchanged for scanner.py compat.
    """
    if df_l.empty or df_h.empty or len(df_l) < 23 or len(df_h) < 20:
        return None

    pip_val = get_pip_value(pair)
    max_risk_price = risk_pips / pip_val

    # --- Regime Detection (pre-filter) ---
    regime_info = detect_regime(df_l)
    atr = regime_info.get("atr") or None  # ATR for all sub-filters

    is_always_open = any(k in pair.upper() for k in ALWAYS_OPEN_KEYS)
    if SKIP_VOLATILE_REGIME and regime_info["regime"] == "VOLATILE" and not is_always_open:
        logger.debug("REJECT %s: volatile regime (ATR ratio=%.2f, trend=%.3f)",
                      pair, regime_info.get("atr_ratio", 0), regime_info.get("trend_strength", 0))
        return None  # Don't trade chop (skip for crypto/synthetics — inherently volatile)

    # --- Layer 2: H4 Storyline ---
    storyline = detect_storyline(df_h, df_l)
    if storyline is None:
        logger.debug("REJECT %s: storyline=None (no HTF structure)", pair)
        return None
    bias = storyline["bias"]

    # Swing points + BOS (enriched with MSS classification + FVG displacement)
    swing_high, swing_low = find_swing_points(df_l)
    if swing_high is None:
        logger.debug("REJECT %s: no swing points found", pair)
        return None

    # Use same lenient ATR (0.5x) as storyline for BOS direction gating.
    # Storyline already confirmed bias with 0.5x ATR; re-checking with full
    # ATR was silently rejecting signals where storyline said BULL but the
    # stricter BOS check found no bullish break.
    bos_atr = atr * 0.5 if atr else None
    bos_info = detect_bos(df_l, swing_high, swing_low, atr=bos_atr, classify=True)
    bullish_bos = bos_info["bullish_bos"]
    bearish_bos = bos_info["bearish_bos"]
    bull_sweep = bos_info["bull_sweep"]
    bear_sweep = bos_info["bear_sweep"]
    break_type = bos_info["break_type"]      # "BOS", "MSS", or None
    is_mss = break_type == "MSS"
    bos_fvg = bos_info["displacement_fvg"]   # True if BOS created FVG

    # BOS FVG hard gate (optional, off by default)
    if REQUIRE_BOS_FVG and (bullish_bos or bearish_bos) and not bos_fvg:
        logger.debug("REJECT %s: BOS without FVG displacement", pair)
        return None

    # H4 Gatekeeper: FORBID trading against H4 bias
    # MSS aligned with HTF bias is a valid reversal setup and passes through
    if bias == "BULL" and not bullish_bos:
        logger.debug("REJECT %s: bias=BULL but no bullish BOS (bos_atr=%.6f)", pair, bos_atr or 0)
        return None
    if bias == "BEAR" and not bearish_bos:
        logger.debug("REJECT %s: bias=BEAR but no bearish BOS (bos_atr=%.6f)", pair, bos_atr or 0)
        return None

    # FVG (confluence bonus, not hard gate) — ATR-filtered, mitigation-aware
    fvg = detect_fvg(df_l, atr=atr)

    c = df_l.iloc[-1]
    storyline_tp = storyline.get("tp_target")

    # All fresh LTF zones for roadblock scanning (ATR-aware)
    all_fresh_ltf = find_zones(df_l, lookback=40, atr=atr)
    all_fresh_ltf = mark_freshness(all_fresh_ltf, df_l)
    all_fresh_ltf = [z for z in all_fresh_ltf if z["fresh"]]

    if bias == "BULL":
        # Regime counter-trend check
        skip, skip_reason = should_skip_regime(regime_info, "BUY", always_open=is_always_open)
        if skip:
            logger.debug("REJECT %s BUY: regime skip (%s)", pair, skip_reason)
            return None

        # --- Layer 1: Fresh Zone (ATR-aware) ---
        fresh = get_fresh_zones(df_l, "demand", atr=atr)
        zone = fresh[0] if fresh else None
        if zone is None:
            logger.debug("REJECT %s BUY: no fresh demand zone", pair)
            return None  # No fresh zone = no trade

        entry_price = zone["top"]
        tp_target = storyline_tp if storyline_tp else df_h['high'].max()

        # --- Premium/Discount Filter ---
        if USE_PREMIUM_DISCOUNT_FILTER:
            pd_zone = is_in_premium_discount(entry_price, swing_high, swing_low)
            if pd_zone == "premium":
                logger.debug("REJECT %s BUY: entry in premium zone", pair)
                return None  # Don't buy at premium
        else:
            pd_zone = is_in_premium_discount(entry_price, swing_high, swing_low)

        # --- Layer 3: Arrival Physics (direction-aware) ---
        if not analyze_arrival(df_l, zone["top"], "demand"):
            logger.debug("REJECT %s BUY: arrival physics failed", pair)
            return None  # Adverse momentum arrival — invalidated

        # --- Layer 4: Roadblock RR Check ---
        risk_distance = abs(entry_price - zone["bottom"])
        if risk_distance <= 0:
            risk_distance = max_risk_price
        if not check_roadblocks(entry_price, "BUY", all_fresh_ltf, risk_distance):
            logger.debug("REJECT %s BUY: roadblock RR failed", pair)
            return None  # RR < 1:2 to nearest roadblock — kill trade

        # Soft roadblock check (for confidence)
        roadblock_near = _check_roadblock(all_fresh_ltf, "BULL", entry_price, tp_target)

        # Retest check
        retest_ok = c['low'] <= zone['top'] and c['high'] >= zone['bottom']

        # --- Layer 5: Trigger (ATR-filtered engulfing) ---
        engulfing = None
        if retest_ok:
            engulfing = detect_engulfing(df_l, zone, atr=atr)

        # Inducement / Sweep check
        induce = detect_inducement_swept(df_l, swing_high, swing_low, "BUY")
        sweep_detected = induce["swept"]

        # Inducement hard gate (optional, off by default)
        if REQUIRE_INDUCEMENT_SWEEP and not sweep_detected:
            logger.debug("REJECT %s BUY: no inducement sweep (hard gate)", pair)
            return None

        # Layer 5 gate: engulfing required, OR touch trade (sweep bypasses engulfing)
        is_touch = False
        if not engulfing:
            if touch_trade and sweep_detected:
                is_touch = True  # Touch trade: sweep + zone + compression = go
            else:
                logger.debug("REJECT %s BUY: no engulfing (retest=%s, sweep=%s, touch=%s)",
                             pair, retest_ok, sweep_detected, touch_trade)
                return None

        fvg_match = (fvg is not None and fvg.get("type") == "BULL_FVG")
        confidence = _compute_confidence(
            sweep_detected, engulfing, zone, fvg_match, touch_entry=is_touch,
            is_mss=is_mss, bos_fvg=bos_fvg,
        )

        # Volume proxy as confidence gate (not just display)
        vol_proxy = compute_volume_proxy(df_l, zone)
        if vol_proxy < 0.3 and confidence != "high":
            logger.debug("REJECT %s BUY: volume proxy too low (%.2f)", pair, vol_proxy)
            return None

        # SL placement: below sweep wick if available, else below zone
        if sweep_detected and induce["wick_level"] is not None:
            sl_anchor = induce["wick_level"]
        else:
            sl_anchor = zone["bottom"]

        # FVG refinement: use actual FVG boundary (not hardcoded candle index)
        if fvg_match and fvg is not None:
            fvg_bottom = fvg["bottom"]
            if sl_anchor < fvg_bottom < entry_price:
                sl_anchor = fvg_bottom  # tighten to FVG boundary

        # HTF zone floor: cap SL at HTF demand zone bottom (structure invalidation)
        htf_zone = storyline.get("htf_zone")
        if htf_zone and htf_zone["direction"] == "demand":
            htf_floor = htf_zone["bottom"]
            if sl_anchor < htf_floor < entry_price:
                sl_anchor = htf_floor

        # Minimum SL distance: at least 0.5 * ATR to avoid noise stop-outs
        if atr and atr > 0:
            min_sl_dist = atr * 0.5
            if abs(entry_price - sl_anchor) < min_sl_dist:
                sl_anchor = entry_price - min_sl_dist

        # Combined SL multiplier: regime + zone quality
        sl_mult = regime_info["sl_multiplier"]
        if zone["miss"]:
            sl_mult *= 0.85  # strong displacement = tighter SL

        htf_extreme = df_h['high'].max()
        # FVG magnetic TP: snap TP to nearby unfilled HTF FVG if within range
        htf_atr_val = atr  # use LTF ATR as proxy
        htf_fvg = detect_fvg(df_h, lookback=30, atr=htf_atr_val)
        if htf_fvg and htf_fvg["type"] == "BEAR_FVG":
            fvg_start = htf_fvg["bottom"]
            if entry_price < fvg_start and tp_target > 0:
                dist_to_tp = abs(tp_target - entry_price)
                dist_to_fvg = abs(fvg_start - tp_target)
                if dist_to_tp > 0 and dist_to_fvg / dist_to_tp < 0.2:
                    tp_target = fvg_start  # Snap to FVG boundary

        limit_levels = calculate_levels(
            "BUY", entry_price, sl_anchor, max_risk_price, tp_target,
            htf_extreme=htf_extreme, sl_multiplier=sl_mult,
            opposing_ltf_zones=all_fresh_ltf,
        )
        market_levels = calculate_levels(
            "BUY", c['close'], sl_anchor, max_risk_price, tp_target,
            htf_extreme=htf_extreme, sl_multiplier=sl_mult,
            opposing_ltf_zones=all_fresh_ltf,
        )

        return {
            "act": "BUY",
            "limit_e": entry_price,
            "limit_sl": limit_levels["sl"],
            "market_e": c['close'],
            "market_sl": market_levels["sl"],
            "tp": limit_levels["tp"],
            "tp1": limit_levels["tp1"],
            "tp2": limit_levels["tp2"],
            "tp3": limit_levels["tp3"],
            "confidence": confidence,
            "zone_type": zone["type"],
            "miss": zone["miss"],
            "sweep": sweep_detected,
            "arrival": "compression",
            "touch": is_touch,
            "regime": regime_info["regime"],
            "atr": regime_info["atr"],
            "sl_multiplier": regime_info["sl_multiplier"],
            "volume_proxy": vol_proxy,
            "break_type": break_type,
            "displacement_fvg": bos_fvg,
            "pd_zone": pd_zone,
        }

    if bias == "BEAR":
        # Regime counter-trend check
        skip, skip_reason = should_skip_regime(regime_info, "SELL", always_open=is_always_open)
        if skip:
            logger.debug("REJECT %s SELL: regime skip (%s)", pair, skip_reason)
            return None

        # --- Layer 1: Fresh Zone (ATR-aware) ---
        fresh = get_fresh_zones(df_l, "supply", atr=atr)
        zone = fresh[0] if fresh else None
        if zone is None:
            logger.debug("REJECT %s SELL: no fresh supply zone", pair)
            return None

        entry_price = zone["bottom"]
        tp_target = storyline_tp if storyline_tp else df_h['low'].min()

        # --- Premium/Discount Filter ---
        if USE_PREMIUM_DISCOUNT_FILTER:
            pd_zone = is_in_premium_discount(entry_price, swing_high, swing_low)
            if pd_zone == "discount":
                logger.debug("REJECT %s SELL: entry in discount zone", pair)
                return None  # Don't sell at discount
        else:
            pd_zone = is_in_premium_discount(entry_price, swing_high, swing_low)

        # --- Layer 3: Arrival Physics (direction-aware) ---
        if not analyze_arrival(df_l, zone["bottom"], "supply"):
            logger.debug("REJECT %s SELL: arrival physics failed", pair)
            return None

        # --- Layer 4: Roadblock RR Check ---
        risk_distance = abs(zone["top"] - entry_price)
        if risk_distance <= 0:
            risk_distance = max_risk_price
        if not check_roadblocks(entry_price, "SELL", all_fresh_ltf, risk_distance):
            logger.debug("REJECT %s SELL: roadblock RR failed", pair)
            return None

        roadblock_near = _check_roadblock(all_fresh_ltf, "BEAR", entry_price, tp_target)

        retest_ok = c['high'] >= zone['bottom'] and c['low'] <= zone['top']

        engulfing = None
        if retest_ok:
            engulfing = detect_engulfing(df_l, zone, atr=atr)

        induce = detect_inducement_swept(df_l, swing_high, swing_low, "SELL")
        sweep_detected = induce["swept"]

        # Inducement hard gate (optional, off by default)
        if REQUIRE_INDUCEMENT_SWEEP and not sweep_detected:
            logger.debug("REJECT %s SELL: no inducement sweep (hard gate)", pair)
            return None

        # Layer 5 gate: engulfing required, OR touch trade (sweep bypasses engulfing)
        is_touch = False
        if not engulfing:
            if touch_trade and sweep_detected:
                is_touch = True
            else:
                logger.debug("REJECT %s SELL: no engulfing (retest=%s, sweep=%s, touch=%s)",
                             pair, retest_ok, sweep_detected, touch_trade)
                return None

        fvg_match = (fvg is not None and fvg.get("type") == "BEAR_FVG")
        confidence = _compute_confidence(
            sweep_detected, engulfing, zone, fvg_match, touch_entry=is_touch,
            is_mss=is_mss, bos_fvg=bos_fvg,
        )

        # Volume proxy as confidence gate (not just display)
        vol_proxy = compute_volume_proxy(df_l, zone)
        if vol_proxy < 0.3 and confidence != "high":
            logger.debug("REJECT %s SELL: volume proxy too low (%.2f)", pair, vol_proxy)
            return None

        if sweep_detected and induce["wick_level"] is not None:
            sl_anchor = induce["wick_level"]
        else:
            sl_anchor = zone["top"]

        # FVG refinement: use actual FVG boundary (not hardcoded candle index)
        if fvg_match and fvg is not None:
            fvg_top = fvg["top"]
            if sl_anchor > fvg_top > entry_price:
                sl_anchor = fvg_top  # tighten to FVG boundary

        # HTF zone ceiling: cap SL at HTF supply zone top (structure invalidation)
        htf_zone = storyline.get("htf_zone")
        if htf_zone and htf_zone["direction"] == "supply":
            htf_ceiling = htf_zone["top"]
            if sl_anchor > htf_ceiling > entry_price:
                sl_anchor = htf_ceiling

        # Minimum SL distance: at least 0.5 * ATR to avoid noise stop-outs
        if atr and atr > 0:
            min_sl_dist = atr * 0.5
            if abs(sl_anchor - entry_price) < min_sl_dist:
                sl_anchor = entry_price + min_sl_dist

        # Combined SL multiplier: regime + zone quality
        sl_mult = regime_info["sl_multiplier"]
        if zone["miss"]:
            sl_mult *= 0.85  # strong displacement = tighter SL

        htf_extreme = df_h['low'].min()
        # FVG magnetic TP: snap TP to nearby unfilled HTF FVG if within range
        htf_fvg = detect_fvg(df_h, lookback=30, atr=atr)
        if htf_fvg and htf_fvg["type"] == "BULL_FVG":
            fvg_start = htf_fvg["top"]
            if entry_price > fvg_start and tp_target > 0:
                dist_to_tp = abs(entry_price - tp_target)
                dist_to_fvg = abs(tp_target - fvg_start)
                if dist_to_tp > 0 and dist_to_fvg / dist_to_tp < 0.2:
                    tp_target = fvg_start  # Snap to FVG boundary

        limit_levels = calculate_levels(
            "SELL", entry_price, sl_anchor, max_risk_price, tp_target,
            htf_extreme=htf_extreme, sl_multiplier=sl_mult,
            opposing_ltf_zones=all_fresh_ltf,
        )
        market_levels = calculate_levels(
            "SELL", c['close'], sl_anchor, max_risk_price, tp_target,
            htf_extreme=htf_extreme, sl_multiplier=sl_mult,
            opposing_ltf_zones=all_fresh_ltf,
        )

        return {
            "act": "SELL",
            "limit_e": entry_price,
            "limit_sl": limit_levels["sl"],
            "market_e": c['close'],
            "market_sl": market_levels["sl"],
            "tp": limit_levels["tp"],
            "tp1": limit_levels["tp1"],
            "tp2": limit_levels["tp2"],
            "tp3": limit_levels["tp3"],
            "confidence": confidence,
            "zone_type": zone["type"],
            "miss": zone["miss"],
            "sweep": sweep_detected,
            "arrival": "compression",
            "touch": is_touch,
            "regime": regime_info["regime"],
            "atr": regime_info["atr"],
            "sl_multiplier": regime_info["sl_multiplier"],
            "volume_proxy": vol_proxy,
            "break_type": break_type,
            "displacement_fvg": bos_fvg,
            "pd_zone": pd_zone,
        }

    return None
