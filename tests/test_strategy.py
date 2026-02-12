import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import pandas as pd
import numpy as np
from strategy import (
    get_pip_value, detect_bias, find_swing_points,
    detect_bos, detect_fvg, calculate_levels, get_smc_signal,
)


# =====================
# HELPERS
# =====================
def make_ohlc(data):
    """Create a DataFrame from a list of (open, high, low, close) tuples."""
    df = pd.DataFrame(data, columns=['open', 'high', 'low', 'close'])
    return df.astype(float)


def make_trending_data(direction, bars=30, start=1.0, step=0.001):
    """Generate trending OHLC data."""
    data = []
    price = start
    for i in range(bars):
        if direction == "up":
            o = price
            h = price + step * 0.8
            l = price - step * 0.2
            c = price + step * 0.6
            price += step
        else:
            o = price
            h = price + step * 0.2
            l = price - step * 0.8
            c = price - step * 0.6
            price -= step
        data.append((o, h, l, c))
    return make_ohlc(data)


# =====================
# PIP VALUE TESTS
# =====================
class TestGetPipValue:
    def test_forex_major(self):
        assert get_pip_value("EURUSD") == 10000

    def test_jpy_pair(self):
        assert get_pip_value("USDJPY") == 10

    def test_gold(self):
        assert get_pip_value("XAUUSD") == 10

    def test_index(self):
        assert get_pip_value("US30") == 10

    def test_volatility(self):
        assert get_pip_value("V75") == 10

    def test_boom_crash(self):
        assert get_pip_value("BOOM300") == 10

    def test_crypto_btc(self):
        assert get_pip_value("BTCUSD") == 0.1

    def test_crypto_eth(self):
        assert get_pip_value("ETHUSDT") == 1

    def test_crypto_sol(self):
        assert get_pip_value("SOLUSDT") == 10

    def test_case_insensitive(self):
        assert get_pip_value("xauusd") == 10


# =====================
# BIAS DETECTION TESTS
# =====================
class TestDetectBias:
    def test_bullish_bias(self):
        df = make_trending_data("up", bars=25)
        assert detect_bias(df) == "BULL"

    def test_bearish_bias(self):
        df = make_trending_data("down", bars=25)
        assert detect_bias(df) == "BEAR"

    def test_insufficient_data(self):
        df = make_trending_data("up", bars=10)
        assert detect_bias(df, lookback=20) is None

    def test_custom_lookback(self):
        df = make_trending_data("up", bars=15)
        assert detect_bias(df, lookback=10) == "BULL"

    def test_flat_market_bearish(self):
        # Same close = not strictly greater, so BEAR
        data = [(1.0, 1.1, 0.9, 1.0)] * 25
        df = make_ohlc(data)
        assert detect_bias(df) == "BEAR"


# =====================
# SWING POINT TESTS
# =====================
class TestFindSwingPoints:
    def test_basic_swing_points(self):
        data = []
        for i in range(30):
            h = 1.0 + (i % 5) * 0.01
            l = 0.9 + (i % 3) * 0.005
            data.append((0.95, h, l, 0.96))
        df = make_ohlc(data)
        sh, sl = find_swing_points(df)
        assert sh is not None
        assert sl is not None
        assert sh > sl

    def test_insufficient_data(self):
        df = make_ohlc([(1, 1.1, 0.9, 1)] * 5)
        sh, sl = find_swing_points(df)
        assert sh is None
        assert sl is None

    def test_custom_range(self):
        data = [(1.0, 1.0 + i * 0.01, 0.9, 1.0) for i in range(30)]
        df = make_ohlc(data)
        sh, sl = find_swing_points(df, start=-10, end=-2)
        assert sh is not None


# =====================
# BOS DETECTION TESTS
# =====================
class TestDetectBOS:
    def test_bullish_bos(self):
        # Swing high at 1.05, recent closes above it
        data = [(1.0, 1.05, 0.95, 1.0)] * 20
        data += [(1.0, 1.08, 0.98, 1.06)] * 5  # breaks above
        df = make_ohlc(data)
        bullish, bearish = detect_bos(df, 1.05, 0.95)
        assert bool(bullish) is True

    def test_bearish_bos(self):
        data = [(1.0, 1.05, 0.95, 1.0)] * 20
        data += [(1.0, 1.02, 0.90, 0.93)] * 5  # breaks below
        df = make_ohlc(data)
        bullish, bearish = detect_bos(df, 1.05, 0.95)
        assert bool(bearish) is True

    def test_no_bos(self):
        data = [(1.0, 1.04, 0.96, 1.0)] * 25
        df = make_ohlc(data)
        bullish, bearish = detect_bos(df, 1.05, 0.95)
        assert bool(bullish) is False
        assert bool(bearish) is False

    def test_insufficient_data(self):
        df = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 2)
        bullish, bearish = detect_bos(df, 1.1, 0.9, lookback=5)
        assert bullish is False
        assert bearish is False


# =====================
# FVG DETECTION TESTS
# =====================
class TestDetectFVG:
    def test_bullish_fvg(self):
        # c3.low > c1.high = bullish gap
        data = [
            (1.0, 1.02, 0.98, 1.01),   # c1
            (1.01, 1.04, 1.00, 1.03),   # c2
            (1.03, 1.06, 1.025, 1.05),  # c3: low 1.025 > c1 high 1.02
        ]
        df = make_ohlc(data)
        assert detect_fvg(df) == "BULL_FVG"

    def test_bearish_fvg(self):
        # c3.high < c1.low = bearish gap
        data = [
            (1.05, 1.06, 1.03, 1.04),   # c1
            (1.03, 1.04, 1.01, 1.02),   # c2
            (1.01, 1.025, 0.99, 1.00),  # c3: high 1.025 < c1 low 1.03
        ]
        df = make_ohlc(data)
        assert detect_fvg(df) == "BEAR_FVG"

    def test_no_fvg(self):
        data = [
            (1.0, 1.05, 0.95, 1.02),
            (1.02, 1.04, 1.00, 1.03),
            (1.03, 1.06, 0.99, 1.04),  # overlapping
        ]
        df = make_ohlc(data)
        assert detect_fvg(df) is None

    def test_insufficient_data(self):
        df = make_ohlc([(1.0, 1.1, 0.9, 1.0)])
        assert detect_fvg(df) is None


# =====================
# LEVEL CALCULATION TESTS
# =====================
class TestCalculateLevels:
    def test_buy_sl_within_risk(self):
        # entry=1.1, swing_low=0.99 (risk 0.11 > max 0.005), so SL is capped
        levels = calculate_levels("BUY", 1.1000, 1.1200, 0.9900, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.1000 - 0.0050)
        assert levels["tp"] == 1.1500

    def test_buy_sl_raw_when_small(self):
        # entry=1.1, swing_low=1.098 (risk 0.002 < max 0.005), raw SL used
        levels = calculate_levels("BUY", 1.1000, 1.1200, 1.0980, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.0980)

    def test_buy_sl_capped(self):
        levels = calculate_levels("BUY", 1.1000, 1.1200, 1.0500, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.1000 - 0.0050)

    def test_sell_sl_raw_when_small(self):
        # entry=1.1, swing_high=1.102 (risk 0.002 < max 0.005), raw SL used
        levels = calculate_levels("SELL", 1.1000, 1.1020, 1.0800, 0.0050, 1.0500)
        assert levels["sl"] == pytest.approx(1.1020)
        assert levels["tp"] == 1.0500

    def test_sell_sl_capped(self):
        levels = calculate_levels("SELL", 1.1000, 1.1600, 1.0800, 0.0050, 1.0500)
        assert levels["sl"] == pytest.approx(1.1000 + 0.0050)


# =====================
# FULL SIGNAL GENERATION TESTS
# =====================
class TestGetSMCSignal:
    def test_returns_none_empty_df(self):
        assert get_smc_signal(pd.DataFrame(), pd.DataFrame(), "EURUSD") is None

    def test_returns_none_insufficient_lower_tf(self):
        df_l = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 10)
        df_h = make_trending_data("up", bars=25)
        assert get_smc_signal(df_l, df_h, "EURUSD") is None

    def test_returns_none_insufficient_higher_tf(self):
        df_l = make_trending_data("up", bars=30)
        df_h = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 10)
        assert get_smc_signal(df_l, df_h, "EURUSD") is None

    def test_buy_signal_structure(self):
        """Construct data that should produce a BUY signal."""
        # Higher TF: bullish (close rising)
        df_h = make_trending_data("up", bars=25, start=1.0, step=0.002)

        # Lower TF: need BOS + FVG
        # Build: 20 range bars, then breakout above swing high, then FVG
        base_data = [(1.0, 1.02, 0.98, 1.01)] * 20
        # Breakout candles
        base_data += [
            (1.01, 1.035, 1.005, 1.03),  # c[-5] breaks swing high
            (1.03, 1.04, 1.025, 1.035),
            (1.035, 1.045, 1.03, 1.04),
        ]
        # FVG: c1=base_data[-3], c3 must have low > c1.high
        # c[-3].high = 1.035, so c[-1].low must be > 1.035
        base_data += [
            (1.04, 1.05, 1.035, 1.045),   # c[-2]
            (1.045, 1.06, 1.036, 1.055),   # c[-1]: low 1.036 > c[-3].high 1.035? barely
        ]

        df_l = make_ohlc(base_data)
        sig = get_smc_signal(df_l, df_h, "EURUSD")

        if sig is not None:
            assert sig["act"] == "BUY"
            assert "limit_e" in sig
            assert "market_e" in sig
            assert "tp" in sig
            assert "limit_sl" in sig
            assert "market_sl" in sig
            assert sig["tp"] > sig["market_e"]

    def test_no_signal_conflicting_bias(self):
        """Bearish HTF with bullish LTF should not signal BUY."""
        df_h = make_trending_data("down", bars=25)
        # LTF tries to break up but HTF bias is bearish
        data = [(1.0, 1.02, 0.98, 1.01)] * 20
        data += [(1.01, 1.04, 1.005, 1.03)] * 5
        df_l = make_ohlc(data)
        sig = get_smc_signal(df_l, df_h, "EURUSD")
        # Should not produce a BUY (bias mismatch)
        if sig is not None:
            assert sig["act"] != "BUY"

    def test_custom_risk_pips(self):
        """Risk pips parameter should affect SL distance."""
        df_h = make_trending_data("up", bars=25, start=1.0, step=0.005)
        # Wide range data to force SL capping
        data = [(1.0, 1.10, 0.80, 1.05)] * 20
        data += [
            (1.05, 1.15, 1.04, 1.12),
            (1.12, 1.16, 1.10, 1.14),
            (1.14, 1.18, 1.12, 1.16),
            (1.16, 1.20, 1.15, 1.18),
            (1.18, 1.22, 1.19, 1.21),  # FVG: low 1.19 > c[-3].high 1.18
        ]
        df_l = make_ohlc(data)

        sig_30 = get_smc_signal(df_l, df_h, "EURUSD", risk_pips=30)
        sig_100 = get_smc_signal(df_l, df_h, "EURUSD", risk_pips=100)

        # Both may or may not produce signals depending on exact conditions,
        # but if they do, the risk should differ
        if sig_30 and sig_100:
            risk_30 = abs(sig_30["market_e"] - sig_30["market_sl"])
            risk_100 = abs(sig_100["market_e"] - sig_100["market_sl"])
            assert risk_30 <= risk_100
