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
    _opposing_zone_tp, _check_roadblock,
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
        data = [(1.0, 1.05, 0.95, 1.0)] * 20
        data += [(1.0, 1.08, 0.98, 1.06)] * 5  # body closes above swing high
        df = make_ohlc(data)
        bullish, bearish, bull_sw, bear_sw = detect_bos(df, 1.05, 0.95)
        assert bool(bullish) is True
        assert bull_sw is False  # not a sweep, it's a real break

    def test_bearish_bos(self):
        data = [(1.0, 1.05, 0.95, 1.0)] * 20
        data += [(1.0, 1.02, 0.90, 0.93)] * 5  # body closes below swing low
        df = make_ohlc(data)
        bullish, bearish, bull_sw, bear_sw = detect_bos(df, 1.05, 0.95)
        assert bool(bearish) is True
        assert bear_sw is False

    def test_no_bos(self):
        data = [(1.0, 1.04, 0.96, 1.0)] * 25
        df = make_ohlc(data)
        bullish, bearish, bull_sw, bear_sw = detect_bos(df, 1.05, 0.95)
        assert bool(bullish) is False
        assert bool(bearish) is False

    def test_insufficient_data(self):
        df = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 2)
        result = detect_bos(df, 1.1, 0.9, lookback=5)
        assert result == (False, False, False, False)

    def test_bull_sweep_wick_only(self):
        """Wick above swing high but body closes below = bull sweep, not BOS."""
        data = [(1.0, 1.04, 0.96, 1.0)] * 20
        # Wick to 1.06 (above swing_high=1.05) but close at 1.03 (below)
        data += [(1.02, 1.06, 1.01, 1.03)] * 5
        df = make_ohlc(data)
        bullish, bearish, bull_sw, bear_sw = detect_bos(df, 1.05, 0.95)
        assert bullish is False
        assert bull_sw is True  # wick-only sweep

    def test_bear_sweep_wick_only(self):
        """Wick below swing low but body closes above = bear sweep, not BOS."""
        data = [(1.0, 1.04, 0.96, 1.0)] * 20
        # Wick to 0.93 (below swing_low=0.95) but close at 0.97 (above)
        data += [(0.98, 1.02, 0.93, 0.97)] * 5
        df = make_ohlc(data)
        bullish, bearish, bull_sw, bear_sw = detect_bos(df, 1.05, 0.95)
        assert bearish is False
        assert bear_sw is True


# =====================
# FVG DETECTION TESTS
# =====================
class TestDetectFVG:
    def test_bullish_fvg(self):
        data = [
            (1.0, 1.02, 0.98, 1.01),
            (1.01, 1.04, 1.00, 1.03),
            (1.03, 1.06, 1.025, 1.05),
        ]
        df = make_ohlc(data)
        assert detect_fvg(df) == "BULL_FVG"

    def test_bearish_fvg(self):
        data = [
            (1.05, 1.06, 1.03, 1.04),
            (1.03, 1.04, 1.01, 1.02),
            (1.01, 1.025, 0.99, 1.00),
        ]
        df = make_ohlc(data)
        assert detect_fvg(df) == "BEAR_FVG"

    def test_no_fvg(self):
        data = [
            (1.0, 1.05, 0.95, 1.02),
            (1.02, 1.04, 1.00, 1.03),
            (1.03, 1.06, 0.99, 1.04),
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
        levels = calculate_levels("BUY", 1.1000, 1.1200, 0.9900, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.1000 - 0.0050)
        assert levels["tp"] == 1.1500

    def test_buy_sl_raw_when_small(self):
        levels = calculate_levels("BUY", 1.1000, 1.1200, 1.0980, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.0980)

    def test_buy_sl_capped(self):
        levels = calculate_levels("BUY", 1.1000, 1.1200, 1.0500, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.1000 - 0.0050)

    def test_sell_sl_raw_when_small(self):
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
            (1.01, 1.02, 0.98, 0.985),  # bear → A-level
            (0.99, 1.01, 0.97, 1.005),  # bull → V-level
            (1.01, 1.03, 1.00, 1.02),   # bull → OC-gap
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
            (0.98, 1.021, 0.97, 0.98),  # high=1.021 enters zone [1.02, 1.025]
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A" and z["bar_index"] == 0]
        assert len(a_zones) == 1
        assert a_zones[0]["fresh"] is False

    def test_zone_stays_fresh_no_touch(self):
        """Zone remains fresh if no subsequent wick enters it."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone at [1.02, 1.025]
            (0.95, 0.96, 0.94, 0.955),
            (0.94, 0.95, 0.93, 0.945),
            (0.93, 0.94, 0.92, 0.935),
            (0.92, 0.93, 0.91, 0.925),
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A" and z["bar_index"] == 0]
        assert len(a_zones) == 1
        assert a_zones[0]["fresh"] is True

    def test_miss_detection(self):
        """Zone marked as MISS when first 3 candles after formation don't touch."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone at [1.02, 1.025]
            (0.95, 0.96, 0.94, 0.955),
            (0.94, 0.95, 0.93, 0.945),
            (0.93, 0.94, 0.92, 0.935),
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A" and z["bar_index"] == 0]
        assert len(a_zones) == 1
        assert a_zones[0]["miss"] is True
        assert a_zones[0]["fresh"] is True

    def test_sbr_demand_broken_becomes_supply(self):
        """Demand zone broken by bearish body close → flips to fresh supply."""
        data = [
            (1.02, 1.03, 0.98, 0.99),   # bearish
            (1.00, 1.03, 0.98, 1.02),   # bullish → V-level demand at [0.99, 1.00]
            # Bearish candle whose body closes below the demand zone bottom (0.99)
            (0.995, 1.00, 0.97, 0.98),  # body_bottom = min(0.995, 0.98) = 0.98 < 0.99
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)

        # Original demand zone should be unfresh
        orig = [z for z in zones if z["bar_index"] == 0 and z["direction"] == "demand"]
        assert len(orig) == 1
        assert orig[0]["fresh"] is False

        # New flipped supply zone should exist and be fresh
        flipped = [z for z in zones if z["direction"] == "supply"
                   and z["top"] == orig[0]["top"] and z["bottom"] == orig[0]["bottom"]]
        assert len(flipped) == 1
        assert flipped[0]["fresh"] is True

    def test_rbs_supply_broken_becomes_demand(self):
        """Supply zone broken by bullish body close → flips to fresh demand."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level supply at [1.02, 1.025]
            # Bullish candle whose body closes above the supply zone top (1.025)
            (1.02, 1.04, 1.01, 1.03),   # body_top = max(1.02, 1.03) = 1.03 > 1.025
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)

        # Original supply zone should be unfresh
        orig = [z for z in zones if z["bar_index"] == 0 and z["direction"] == "supply"]
        assert len(orig) == 1
        assert orig[0]["fresh"] is False

        # New flipped demand zone should exist
        flipped = [z for z in zones if z["direction"] == "demand"
                   and z["top"] == orig[0]["top"] and z["bottom"] == orig[0]["bottom"]]
        assert len(flipped) == 1
        assert flipped[0]["fresh"] is True

    def test_get_fresh_zones_filters(self):
        """get_fresh_zones returns only fresh zones of the requested direction."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bull
            (1.02, 1.03, 0.98, 0.99),   # bear → A-level (supply)
            (0.99, 1.01, 0.97, 1.005),  # bull → V-level (demand)
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
        htf_data = [
            (1.05, 1.06, 1.04, 1.04),   # bearish
            (1.045, 1.06, 1.03, 1.055),  # bullish → V-level demand
            (1.06, 1.07, 1.05, 1.065),
            (1.07, 1.08, 1.06, 1.075),
            (1.08, 1.09, 1.07, 1.085),
            (1.09, 1.10, 1.08, 1.095),
            (1.10, 1.11, 1.09, 1.105),
            (1.11, 1.12, 1.10, 1.115),
            (1.12, 1.13, 1.11, 1.125),
            # Rejection candle: wick dips into demand, body stays above
            (1.06, 1.07, 1.039, 1.065),
        ]
        df_h = make_ohlc(htf_data)

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
        assert "tp_target" in result
        assert "roadblock_near" in result

    def test_fallback_to_momentum(self):
        """When no HTF rejection found, falls back to momentum bias."""
        flat = [(1.0, 1.01, 0.99, 1.0)] * 24
        flat.append((1.0, 1.05, 0.99, 1.04))
        df_h = make_ohlc(flat)
        df_l = make_trending_data("up", bars=30, start=1.0, step=0.001)
        result = detect_storyline(df_h, df_l)
        assert result is not None
        assert result["bias"] == "BULL"
        assert result["confirmed"] is False
        assert result["htf_zone"] is None
        assert "tp_target" in result
        assert result["roadblock_near"] is False

    def test_insufficient_data(self):
        df_h = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 5)
        df_l = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 10)
        assert detect_storyline(df_h, df_l) is None

    def test_opposing_zone_tp_bull(self):
        """BULL storyline TP targets nearest fresh supply zone above entry."""
        zones = [
            {"direction": "supply", "top": 1.10, "bottom": 1.09, "fresh": True},
            {"direction": "supply", "top": 1.20, "bottom": 1.19, "fresh": True},
            {"direction": "demand", "top": 0.95, "bottom": 0.94, "fresh": True},
        ]
        tp = _opposing_zone_tp(zones, "BULL", 1.05, fallback=1.30)
        assert tp == 1.09  # nearest supply bottom above entry

    def test_opposing_zone_tp_bear(self):
        """BEAR storyline TP targets nearest fresh demand zone below entry."""
        zones = [
            {"direction": "demand", "top": 0.95, "bottom": 0.94, "fresh": True},
            {"direction": "demand", "top": 0.90, "bottom": 0.89, "fresh": True},
            {"direction": "supply", "top": 1.10, "bottom": 1.09, "fresh": True},
        ]
        tp = _opposing_zone_tp(zones, "BEAR", 1.00, fallback=0.80)
        assert tp == 0.95  # nearest demand top below entry

    def test_opposing_zone_tp_fallback(self):
        """Falls back to HTF extreme when no opposing zone found."""
        zones = [
            {"direction": "demand", "top": 0.95, "bottom": 0.94, "fresh": True},
        ]
        tp = _opposing_zone_tp(zones, "BULL", 1.05, fallback=1.30)
        assert tp == 1.30

    def test_roadblock_near(self):
        """Supply zone within 30% of entry→TP range flags roadblock."""
        zones = [
            # Supply zone at 1.06 bottom, entry=1.05, TP=1.15
            # dist=0.01, range=0.10, ratio=0.10 → within 30%
            {"direction": "supply", "top": 1.07, "bottom": 1.06, "fresh": True},
        ]
        assert _check_roadblock(zones, "BULL", 1.05, 1.15) is True

    def test_no_roadblock(self):
        """Supply zone far from entry doesn't flag roadblock."""
        zones = [
            # Supply zone at 1.12 bottom, entry=1.05, TP=1.15
            # dist=0.07, range=0.10, ratio=0.70 → not within 30%
            {"direction": "supply", "top": 1.13, "bottom": 1.12, "fresh": True},
        ]
        assert _check_roadblock(zones, "BULL", 1.05, 1.15) is False


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
            (0.99, 1.02, 0.985, 1.015),  # bullish engulfing
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
            (1.03, 1.035, 1.015, 1.02),   # bearish engulfing
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
            (1.01, 1.015, 1.005, 1.012),
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
    def test_buy_side_sweep_body_closes_back(self):
        """Wick below swing low + body closes above = inducement swept."""
        data = [(1.0, 1.05, 0.95, 1.02)] * 10
        # Sweep: wick to 0.94 (below 0.95) but body closes at 1.01 (above 0.95)
        data += [(1.0, 1.03, 0.94, 1.01)]
        data += [(1.01, 1.05, 0.98, 1.04)] * 4
        df = make_ohlc(data)
        assert detect_inducement_swept(df, 1.05, 0.95, "BUY") is True

    def test_sell_side_sweep_body_closes_back(self):
        """Wick above swing high + body closes below = inducement swept."""
        data = [(1.0, 1.05, 0.95, 0.98)] * 10
        # Sweep: wick to 1.06 (above 1.05) but body closes at 0.99 (below 1.05)
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

    def test_wick_and_body_both_below_not_inducement(self):
        """If body also closes below swing low, it's a real break not a sweep."""
        data = [(1.0, 1.05, 0.95, 1.02)] * 10
        # Body closes at 0.93 (below swing_low=0.95) — real break, not a trap
        data += [(0.96, 0.97, 0.92, 0.93)]
        data += [(0.93, 0.96, 0.91, 0.95)] * 4
        df = make_ohlc(data)
        assert detect_inducement_swept(df, 1.05, 0.95, "BUY") is False


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
            assert sig["act"] == "BUY"
            assert "limit_e" in sig
            assert "market_e" in sig
            assert "tp" in sig
            assert "limit_sl" in sig
            assert "market_sl" in sig
            assert "confidence" in sig
            assert sig["confidence"] in ("high", "medium", "low")

    def test_no_signal_conflicting_bias(self):
        """Bearish HTF with bullish LTF should not signal BUY."""
        df_h = make_trending_data("down", bars=25)
        data = [(1.0, 1.02, 0.98, 1.01)] * 20
        data += [(1.01, 1.04, 1.005, 1.03)] * 5
        df_l = make_ohlc(data)
        sig = get_smc_signal(df_l, df_h, "EURUSD")
        if sig is not None:
            assert sig["act"] != "BUY"

    def test_custom_risk_pips(self):
        """Risk pips parameter should affect SL distance."""
        df_h = make_trending_data("up", bars=25, start=1.0, step=0.005)
        data = [(1.0, 1.10, 0.80, 1.05)] * 20
        data += [
            (1.05, 1.15, 1.04, 1.12),
            (1.12, 1.16, 1.10, 1.14),
            (1.14, 1.18, 1.12, 1.16),
            (1.16, 1.20, 1.15, 1.18),
            (1.18, 1.22, 1.19, 1.21),
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
            assert sig["confidence"] in ("high", "medium", "low")
            assert "zone_type" in sig
            assert "miss" in sig

    def test_signal_without_fvg_still_possible(self):
        """FVG is now a confluence bonus, not a hard gate — signals can fire without it."""
        # This tests that the code path doesn't require FVG
        # We just verify the function doesn't crash when FVG is None
        df_h = make_trending_data("up", bars=25, start=1.0, step=0.002)
        # LTF with BOS but no FVG gap
        base_data = [(1.0, 1.02, 0.98, 1.01)] * 20
        base_data += [
            (1.01, 1.03, 1.005, 1.025),
            (1.025, 1.04, 1.02, 1.035),
            (1.035, 1.05, 1.03, 1.045),
            (1.045, 1.06, 1.03, 1.055),  # no gap: low 1.03 < c[-3].high 1.04
            (1.055, 1.07, 1.04, 1.065),  # overlapping
        ]
        df_l = make_ohlc(base_data)
        # Should not crash — may return None or a signal depending on BOS
        sig = get_smc_signal(df_l, df_h, "EURUSD")
        if sig is not None:
            assert sig["act"] in ("BUY", "SELL")
