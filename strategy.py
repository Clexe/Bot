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


def detect_bias(df_h, lookback=20):
    """Detect market bias from higher timeframe data.

    Returns 'BULL' if current close > close N bars ago, else 'BEAR'.
    """
    if len(df_h) < lookback + 1:
        return None
    if df_h['close'].iloc[-1] > df_h['close'].iloc[-lookback]:
        return "BULL"
    return "BEAR"


def find_swing_points(df_l, start=-23, end=-3):
    """Find swing high and swing low from a price range.

    Returns (swing_high, swing_low) or (None, None) if insufficient data.
    """
    if len(df_l) < abs(start):
        return None, None
    segment = df_l.iloc[start:end]
    return segment['high'].max(), segment['low'].min()


def detect_bos(df_l, swing_high, swing_low, lookback=5):
    """Detect Break of Structure.

    Returns ('BULL', True/False), ('BEAR', True/False) for bullish/bearish BOS.
    """
    if len(df_l) < lookback:
        return False, False
    recent = df_l['close'].iloc[-lookback:]
    bullish = recent.max() > swing_high
    bearish = recent.min() < swing_low
    return bullish, bearish


def detect_fvg(df_l):
    """Detect Fair Value Gap (imbalance) from last 3 candles.

    Returns:
        'BULL_FVG' if bullish gap (c3.low > c1.high)
        'BEAR_FVG' if bearish gap (c3.high < c1.low)
        None if no FVG
    """
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
    """Calculate entry, SL, and TP levels for a signal.

    Args:
        sig_type: 'BUY' or 'SELL'
        entry: entry price (market or limit)
        swing_high: recent swing high
        swing_low: recent swing low
        max_risk_price: maximum allowed SL distance in price
        tp_target: take profit price target

    Returns:
        dict with 'sl' and 'tp' keys
    """
    if sig_type == "BUY":
        raw_sl = swing_low
        sl = entry - max_risk_price if (entry - raw_sl) > max_risk_price else raw_sl
    else:
        raw_sl = swing_high
        sl = entry + max_risk_price if (raw_sl - entry) > max_risk_price else raw_sl

    return {"sl": sl, "tp": tp_target}


def get_smc_signal(df_l, df_h, pair, risk_pips=50):
    """Generate SMC trading signal from price data.

    Args:
        df_l: Lower timeframe DataFrame (OHLC)
        df_h: Higher timeframe DataFrame (OHLC)
        pair: Symbol name
        risk_pips: Maximum risk in pips (default 50)

    Returns:
        Signal dict or None
    """
    if df_l.empty or df_h.empty or len(df_l) < 23 or len(df_h) < 20:
        return None

    pip_val = get_pip_value(pair)
    max_risk_price = risk_pips / pip_val

    # 1. Higher TF bias
    bias = detect_bias(df_h)
    if bias is None:
        return None

    # 2. Swing points
    swing_high, swing_low = find_swing_points(df_l)
    if swing_high is None:
        return None

    # 3. Break of structure
    bullish_bos, bearish_bos = detect_bos(df_l, swing_high, swing_low)

    # 4. Fair value gap
    fvg = detect_fvg(df_l)

    c3 = df_l.iloc[-1]
    sig = None

    if bias == "BULL" and bullish_bos and fvg == "BULL_FVG":
        # Limit entry at swing high (retest)
        limit_levels = calculate_levels("BUY", swing_high, swing_high, swing_low, max_risk_price, df_h['high'].max())
        # Market entry at current close
        market_levels = calculate_levels("BUY", c3.close, swing_high, swing_low, max_risk_price, df_h['high'].max())

        sig = {
            "act": "BUY",
            "limit_e": swing_high,
            "limit_sl": limit_levels["sl"],
            "market_e": c3.close,
            "market_sl": market_levels["sl"],
            "tp": limit_levels["tp"],
        }

    if bias == "BEAR" and bearish_bos and fvg == "BEAR_FVG":
        limit_levels = calculate_levels("SELL", swing_low, swing_high, swing_low, max_risk_price, df_h['low'].min())
        market_levels = calculate_levels("SELL", c3.close, swing_high, swing_low, max_risk_price, df_h['low'].min())

        sig = {
            "act": "SELL",
            "limit_e": swing_low,
            "limit_sl": limit_levels["sl"],
            "market_e": c3.close,
            "market_sl": market_levels["sl"],
            "tp": limit_levels["tp"],
        }

    return sig
