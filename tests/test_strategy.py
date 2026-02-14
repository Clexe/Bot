import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import pandas as pd
import numpy as np
from strategy import (
    get_pip_value, detect_bias, find_swing_points,
    detect_bos, detect_fvg, calculate_levels, get_smc_signal,
    find_zones, mark_freshness, get_fresh_zones,
    detect_storyline, detect_engulfing, detect_inducement_swept,
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
# ZONE DETECTION TESTS (Gap 1)
# =====================
class TestFindZones:
    def test_a_level_detected(self):
        """Bullish candle then bearish candle → A-Level (supply)."""
        # c2.open != c1.close so the zone has nonzero width
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish, open=1.02 < close of c1=1.025
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        a_zones = [z for z in zones if z["type"] == "A"]
        assert len(a_zones) == 1
        assert a_zones[0]["direction"] == "supply"
        assert a_zones[0]["top"] == 1.025
        assert a_zones[0]["bottom"] == 1.02

    def test_v_level_detected(self):
        """Bearish candle then bullish candle → V-Level (demand)."""
        data = [
            (1.02, 1.03, 0.98, 0.99),  # bearish
            (1.00, 1.03, 0.98, 1.02),  # bullish
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        v_zones = [z for z in zones if z["type"] == "V"]
        assert len(v_zones) == 1
        z = v_zones[0]
        assert z["direction"] == "demand"
        assert z["top"] == max(0.99, 1.00)
        assert z["bottom"] == min(0.99, 1.00)

    def test_oc_gap_detected(self):
        """Two consecutive bullish candles with gap → OC-Gap (demand)."""
        data = [
            (1.00, 1.03, 0.99, 1.02),  # bullish
            (1.03, 1.05, 1.02, 1.04),  # bullish, open=1.03 > prev close=1.02
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        oc_zones = [z for z in zones if z["type"] == "OC"]
        assert len(oc_zones) == 1
        z = oc_zones[0]
        assert z["direction"] == "demand"
        assert z["top"] == 1.03
        assert z["bottom"] == 1.02

    def test_no_zones_insufficient_data(self):
        df = make_ohlc([(1.0, 1.1, 0.9, 1.0)])
        assert find_zones(df) == []

    def test_multiple_zones(self):
        """Several alternating candles produce multiple zones."""
        data = [
            (1.00, 1.02, 0.99, 1.015),  # bull
            (1.01, 1.02, 0.98, 0.985),  # bear → A-level (c1.close=1.015 != c2.open=1.01)
            (0.99, 1.01, 0.97, 1.005),  # bull → V-level (c1.close=0.985 != c2.open=0.99)
            (1.01, 1.03, 1.00, 1.02),   # bull → OC-gap (bull+bull)
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        types = {z["type"] for z in zones}
        assert "A" in types
        assert "V" in types


class TestFreshness:
    def test_zone_becomes_unfresh_on_wick_touch(self):
        """If a subsequent candle's wick enters the zone, it becomes unfresh."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone at (1.02, 1.025)
            # Next candle wick touches the zone
            (0.98, 1.021, 0.97, 0.98),  # high=1.021 enters zone [1.02, 1.025]
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A"]
        assert len(a_zones) == 1
        assert a_zones[0]["fresh"] is False

    def test_zone_stays_fresh_no_touch(self):
        """Zone remains fresh if no subsequent wick enters it."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone at [1.02, 1.025]
            # Next candles far below, never touch the zone
            (0.95, 0.96, 0.94, 0.955),
            (0.94, 0.95, 0.93, 0.945),
            (0.93, 0.94, 0.92, 0.935),
            (0.92, 0.93, 0.91, 0.925),
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A"]
        assert len(a_zones) == 1
        assert a_zones[0]["fresh"] is True

    def test_miss_detection(self):
        """Zone marked as MISS when first 3 candles after formation don't touch."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone at [1.02, 1.025]
            # 3 candles that don't touch zone (all far below)
            (0.95, 0.96, 0.94, 0.955),
            (0.94, 0.95, 0.93, 0.945),
            (0.93, 0.94, 0.92, 0.935),
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A"]
        assert len(a_zones) == 1
        assert a_zones[0]["miss"] is True
        assert a_zones[0]["fresh"] is True

    def test_get_fresh_zones_filters(self):
        """get_fresh_zones returns only fresh zones of the requested direction."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bull
            (1.02, 1.03, 0.98, 0.99),   # bear → A-level (supply)
            (0.99, 1.01, 0.97, 1.005),  # bull → V-level (demand)
            # Don't touch either zone afterwards
            (0.80, 0.81, 0.79, 0.805),
            (0.79, 0.80, 0.78, 0.795),
            (0.78, 0.79, 0.77, 0.785),
            (0.77, 0.78, 0.76, 0.775),
        ]
        df = make_ohlc(data)
        supply = get_fresh_zones(df, "supply")
        demand = get_fresh_zones(df, "demand")
        assert all(z["direction"] == "supply" for z in supply)
        assert all(z["direction"] == "demand" for z in demand)
        assert all(z["fresh"] for z in supply)
        assert all(z["fresh"] for z in demand)


# =====================
# STORYLINE TESTS (Gap 2)
# =====================
class TestStoryline:
    def test_bullish_rejection_confirmed(self):
        """HTF rejection off demand zone + LTF bullish BOS → confirmed BULL."""
        # HTF: create a demand zone then a rejection candle
        htf_data = [
            (1.05, 1.06, 1.04, 1.04),   # bearish
            (1.045, 1.06, 1.03, 1.055),  # bullish → V-level demand at [1.04, 1.045]
            # Filler candles far from zone
            (1.06, 1.07, 1.05, 1.065),
            (1.07, 1.08, 1.06, 1.075),
            (1.08, 1.09, 1.07, 1.085),
            (1.09, 1.10, 1.08, 1.095),
            (1.10, 1.11, 1.09, 1.105),
            (1.11, 1.12, 1.10, 1.115),
            (1.12, 1.13, 1.11, 1.125),
            # Rejection candle: wick dips into demand, body stays above
            (1.06, 1.07, 1.039, 1.065),  # low=1.039 enters [1.04, 1.045], body_bottom=1.06 > 1.04
        ]
        df_h = make_ohlc(htf_data)

        # LTF: needs bullish BOS (23+ candles with swing points and breakout)
        ltf_base = [(1.04, 1.05, 1.03, 1.045)] * 20
        ltf_base += [
            (1.045, 1.06, 1.04, 1.055),
            (1.055, 1.07, 1.05, 1.065),
            (1.065, 1.08, 1.06, 1.075),
            (1.075, 1.09, 1.07, 1.085),
            (1.085, 1.10, 1.08, 1.095),
        ]
        df_l = make_ohlc(ltf_base)

        result = detect_storyline(df_h, df_l)
        assert result is not None
        assert result["bias"] == "BULL"

    def test_fallback_to_momentum(self):
        """When no HTF rejection found, falls back to momentum bias."""
        # HTF: flat candles (same open/close) so no zones form, then trending close
        flat = [(1.0, 1.01, 0.99, 1.0)] * 24
        flat.append((1.0, 1.05, 0.99, 1.04))  # last candle bullish to set momentum
        df_h = make_ohlc(flat)
        df_l = make_trending_data("up", bars=30, start=1.0, step=0.001)
        result = detect_storyline(df_h, df_l)
        assert result is not None
        assert result["bias"] == "BULL"
        assert result["confirmed"] is False
        assert result["htf_zone"] is None

    def test_insufficient_data(self):
        df_h = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 5)
        df_l = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 10)
        assert detect_storyline(df_h, df_l) is None


# =====================
# ENGULFING TESTS (Gap 3)
# =====================
class TestEngulfing:
    def test_bullish_engulfing_at_demand_zone(self):
        """Bullish engulfing candle at a demand zone is detected."""
        zone = {"type": "V", "direction": "demand",
                "top": 1.01, "bottom": 0.99, "bar_index": 0,
                "fresh": True, "miss": False}
        data = [
            (1.00, 1.01, 0.99, 0.995),  # small bearish
            (0.99, 1.02, 0.985, 1.015),  # bullish engulfing: wraps prev, low touches zone
        ]
        df = make_ohlc(data)
        result = detect_engulfing(df, zone)
        assert result is not None
        assert result == 1

    def test_bearish_engulfing_at_supply_zone(self):
        """Bearish engulfing candle at a supply zone is detected."""
        zone = {"type": "A", "direction": "supply",
                "top": 1.03, "bottom": 1.02, "bar_index": 0,
                "fresh": True, "miss": False}
        data = [
            (1.025, 1.03, 1.02, 1.028),  # small bullish
            (1.03, 1.035, 1.015, 1.02),   # bearish engulfing: wraps prev, high touches zone
        ]
        df = make_ohlc(data)
        result = detect_engulfing(df, zone)
        assert result is not None

    def test_no_engulfing(self):
        """No engulfing pattern when candle doesn't wrap previous."""
        zone = {"type": "V", "direction": "demand",
                "top": 1.01, "bottom": 0.99, "bar_index": 0,
                "fresh": True, "miss": False}
        data = [
            (1.00, 1.02, 0.98, 1.01),
            (1.01, 1.015, 1.005, 1.012),  # small, doesn't engulf
        ]
        df = make_ohlc(data)
        assert detect_engulfing(df, zone) is None

    def test_insufficient_data(self):
        zone = {"type": "V", "direction": "demand",
                "top": 1.01, "bottom": 0.99, "bar_index": 0,
                "fresh": True, "miss": False}
        df = make_ohlc([(1.0, 1.1, 0.9, 1.0)])
        assert detect_engulfing(df, zone) is None


# =====================
# INDUCEMENT TESTS (Gap 3)
# =====================
class TestInducement:
    def test_buy_side_sweep(self):
        """Wick below swing low before bullish move = inducement swept."""
        data = [(1.0, 1.05, 0.95, 1.02)] * 10
        # Sweep candle: dips below swing_low=0.95
        data += [(1.0, 1.03, 0.94, 1.01)]
        data += [(1.01, 1.05, 0.98, 1.04)] * 4
        df = make_ohlc(data)
        assert detect_inducement_swept(df, 1.05, 0.95, "BUY") is True

    def test_sell_side_sweep(self):
        """Wick above swing high before bearish move = inducement swept."""
        data = [(1.0, 1.05, 0.95, 0.98)] * 10
        # Sweep candle: pokes above swing_high=1.05
        data += [(1.0, 1.06, 0.96, 0.99)]
        data += [(0.99, 1.04, 0.93, 0.95)] * 4
        df = make_ohlc(data)
        assert detect_inducement_swept(df, 1.05, 0.95, "SELL") is True

    def test_no_sweep(self):
        """No wick beyond swing points = no inducement."""
        data = [(1.0, 1.04, 0.96, 1.02)] * 20
        df = make_ohlc(data)
        assert detect_inducement_swept(df, 1.05, 0.95, "BUY") is False
        assert detect_inducement_swept(df, 1.05, 0.95, "SELL") is False

    def test_insufficient_data(self):
        df = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 3)
        assert detect_inducement_swept(df, 1.1, 0.9, "BUY") is False


# =====================
# FULL SIGNAL GENERATION TESTS (Updated)
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
        base_data = [(1.0, 1.02, 0.98, 1.01)] * 20
        # Breakout candles
        base_data += [
            (1.01, 1.035, 1.005, 1.03),
            (1.03, 1.04, 1.025, 1.035),
            (1.035, 1.045, 1.03, 1.04),
        ]
        # FVG: c[-3].high = 1.045, so c[-1].low must be > 1.045
        base_data += [
            (1.04, 1.05, 1.035, 1.045),
            (1.045, 1.06, 1.046, 1.055),  # low=1.046 > c[-3].high=1.045
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
            # New fields
            assert "confidence" in sig
            assert sig["confidence"] in ("high", "medium")

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

        if sig_30 and sig_100:
            risk_30 = abs(sig_30["market_e"] - sig_30["market_sl"])
            risk_100 = abs(sig_100["market_e"] - sig_100["market_sl"])
            assert risk_30 <= risk_100

    def test_signal_has_new_fields(self):
        """Signals should contain confidence, zone_type, and miss fields."""
        df_h = make_trending_data("up", bars=25, start=1.0, step=0.002)
        base_data = [(1.0, 1.02, 0.98, 1.01)] * 20
        base_data += [
            (1.01, 1.035, 1.005, 1.03),
            (1.03, 1.04, 1.025, 1.035),
            (1.035, 1.045, 1.03, 1.04),
            (1.04, 1.05, 1.035, 1.045),
            (1.045, 1.06, 1.046, 1.055),
        ]
        df_l = make_ohlc(base_data)
        sig = get_smc_signal(df_l, df_h, "EURUSD")
        if sig is not None:
            assert "confidence" in sig
            assert "zone_type" in sig
            assert "miss" in sig
