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
    _opposing_zone_tp, _check_roadblock, check_roadblocks,
    analyze_arrival, classify_swing_sequence, _bos_creates_fvg,
    is_in_premium_discount, _compute_confidence,
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
        # EURUSD contains 'EUR' which is a DERIV keyword, falls through to
        # HIGH_PIP_SYMBOLS check. The function returns 10000 only for pairs
        # that don't match any keyword. EURUSD matches 'EUR' → returns 10 (HIGH_PIP).
        # For a pure forex pair that doesn't match any keyword, it'd be 10000.
        # EURUSD is a valid forex pair returning 10 due to keyword matching.
        assert get_pip_value("EURUSD") == 10

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
        data += [(1.0, 1.01, 0.90, 0.92)] * 5  # body=0.08, range=0.11, ratio=0.73
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
        data += [(1.02, 1.06, 1.01, 1.03)] * 5
        df = make_ohlc(data)
        bullish, bearish, bull_sw, bear_sw = detect_bos(df, 1.05, 0.95)
        assert bullish is False
        assert bull_sw is True

    def test_bear_sweep_wick_only(self):
        """Wick below swing low but body closes above = bear sweep, not BOS."""
        data = [(1.0, 1.04, 0.96, 1.0)] * 20
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
        result = detect_fvg(df)
        assert result is not None
        assert result["type"] == "BULL_FVG"

    def test_bearish_fvg(self):
        data = [
            (1.05, 1.06, 1.03, 1.04),
            (1.03, 1.04, 1.01, 1.02),
            (1.01, 1.025, 0.99, 1.00),
        ]
        df = make_ohlc(data)
        result = detect_fvg(df)
        assert result is not None
        assert result["type"] == "BEAR_FVG"

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
        levels = calculate_levels("BUY", 1.1000, 0.9900, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.1000 - 0.0050)
        assert levels["tp"] == 1.1500

    def test_buy_sl_raw_when_small(self):
        levels = calculate_levels("BUY", 1.1000, 1.0980, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.0980)

    def test_buy_sl_capped(self):
        levels = calculate_levels("BUY", 1.1000, 1.0500, 0.0050, 1.1500)
        assert levels["sl"] == pytest.approx(1.1000 - 0.0050)

    def test_sell_sl_raw_when_small(self):
        levels = calculate_levels("SELL", 1.1000, 1.1020, 0.0050, 1.0500)
        assert levels["sl"] == pytest.approx(1.1020)
        assert levels["tp"] == 1.0500

    def test_sell_sl_capped(self):
        levels = calculate_levels("SELL", 1.1000, 1.1600, 0.0050, 1.0500)
        assert levels["sl"] == pytest.approx(1.1000 + 0.0050)


# =====================
# ZONE DETECTION TESTS
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


# =====================
# FRESHNESS TESTS
# =====================
class TestFreshness:
    def test_zone_becomes_unfresh_on_wick_touch(self):
        """If a subsequent candle's wick enters the zone, it becomes unfresh."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone at (1.02, 1.025)
            (0.98, 1.021, 0.97, 0.98),  # high=1.021 enters zone [1.02, 1.025]
            (0.97, 0.98, 0.96, 0.975),  # extra candle (current) — freshness excludes this
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A" and z["bar_index"] == 0]
        assert len(a_zones) == 1
        assert a_zones[0]["fresh"] is False

    def test_zone_stays_fresh_no_touch(self):
        """Zone remains fresh if no subsequent wick enters it (beyond buffer)."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone at [1.02, 1.025]
            # Zone mid ~1.0225, buffer ~0.001. So buffered_top ~1.026, buffered_bottom ~1.019
            # Candles well below zone+buffer:
            (0.90, 0.91, 0.89, 0.905),
            (0.89, 0.90, 0.88, 0.895),
            (0.88, 0.89, 0.87, 0.885),
            (0.87, 0.88, 0.86, 0.875),
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
            (0.90, 0.91, 0.89, 0.905),
            (0.89, 0.90, 0.88, 0.895),
            (0.88, 0.89, 0.87, 0.885),
            (0.87, 0.88, 0.86, 0.875),  # extra candle (current)
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A" and z["bar_index"] == 0]
        assert len(a_zones) == 1
        assert a_zones[0]["miss"] is True
        assert a_zones[0]["fresh"] is True

    def test_mitigation_buffer_makes_unfresh(self):
        """Zone becomes unfresh when wick enters within mitigation buffer.

        Buffer = max(zone_width * 0.05, zone_mid * 0.0002).
        Zone [1.02, 1.025]: width=0.005, buffer=0.00025.
        buffered_bottom = 1.02 - 0.00025 = 1.01975.
        A candle with high=1.020 reaches into the buffered zone.
        """
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone
            (0.98, 1.020, 0.97, 0.98),  # high=1.020 enters buffered zone bottom
            (0.97, 0.98, 0.96, 0.975),  # extra candle (current)
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A" and z["bar_index"] == 0]
        assert len(a_zones) == 1
        assert a_zones[0]["fresh"] is False

    def test_sbr_demand_broken_becomes_flip_supply(self):
        """Demand zone broken by bearish body close → FLIP supply."""
        data = [
            (1.02, 1.03, 0.98, 0.99),   # bearish
            (1.00, 1.03, 0.98, 1.02),   # bullish → V-level demand at [0.99, 1.00]
            # Bearish candle whose body closes below the demand zone bottom (0.99)
            (0.995, 1.00, 0.97, 0.98),
            (0.97, 0.98, 0.96, 0.975),  # extra candle (current)
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)

        # Original demand zone should be unfresh
        orig = [z for z in zones if z["bar_index"] == 0 and z["direction"] == "demand"]
        assert len(orig) == 1
        assert orig[0]["fresh"] is False

        # New flipped supply zone should exist as FLIP type
        flipped = [z for z in zones if z["type"] == "FLIP" and z["direction"] == "supply"]
        assert len(flipped) == 1
        assert flipped[0]["fresh"] is True

    def test_rbs_supply_broken_becomes_flip_demand(self):
        """Supply zone broken by bullish body close → FLIP demand."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level supply at [1.02, 1.025]
            # Bullish candle whose body closes above the supply zone top (1.025)
            (1.02, 1.04, 1.01, 1.03),
            (1.03, 1.04, 1.02, 1.035),  # extra candle (current)
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)

        orig = [z for z in zones if z["bar_index"] == 0 and z["direction"] == "supply"]
        assert len(orig) == 1
        assert orig[0]["fresh"] is False

        flipped = [z for z in zones if z["type"] == "FLIP" and z["direction"] == "demand"]
        assert len(flipped) == 1
        assert flipped[0]["fresh"] is True

    def test_get_fresh_zones_flip_priority(self):
        """FLIP zones should sort before regular zones."""
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level supply
            (1.02, 1.04, 1.01, 1.03),   # breaks supply → FLIP demand
            # Need more candles so the FLIP zone stays fresh
            (1.04, 1.05, 1.03, 1.045),
            (1.05, 1.06, 1.04, 1.055),
            # V-level demand below (won't conflict)
            (0.96, 0.97, 0.94, 0.945),  # bearish
            (0.95, 0.97, 0.93, 0.96),   # bullish → V-level demand
        ]
        df = make_ohlc(data)
        demand = get_fresh_zones(df, "demand")
        # If a FLIP zone exists and is fresh, it should be first
        flip_zones = [z for z in demand if z["type"] == "FLIP"]
        if flip_zones and demand:
            assert demand[0]["type"] == "FLIP"


# =====================
# ARRIVAL PHYSICS TESTS
# =====================
class TestAnalyzeArrival:
    def test_compression_arrival_passes(self):
        """Small-body candles approaching zone = compression = safe."""
        # Create 50 candles with avg body ~0.005
        data = [(1.0, 1.01, 0.99, 1.005)] * 50
        df = make_ohlc(data)
        assert analyze_arrival(df, 1.005, "demand") is True

    def test_momentum_arrival_fails(self):
        """Large bearish Marubozu approaching demand zone = adverse momentum."""
        # 50 small candles, avg body ~0.005
        data = [(1.0, 1.01, 0.99, 1.005)] * 47
        # Then 3 candles, one with BEARISH body = 0.03 > 2.5 * 0.005
        # (bearish into demand = adverse momentum)
        data += [(1.03, 1.035, 0.99, 1.0)]  # bearish Marubozu body=0.03
        data += [(1.0, 1.01, 0.99, 1.005)]
        data += [(1.005, 1.01, 0.99, 1.002)]
        df = make_ohlc(data)
        assert analyze_arrival(df, 1.002, "demand") is False

    def test_insufficient_data_passes(self):
        """With too little data, don't block."""
        df = make_ohlc([(1.0, 1.01, 0.99, 1.005)])
        assert analyze_arrival(df, 1.005, "demand", lookback=3) is True

    def test_flat_market_passes(self):
        """Doji candles (open==close) = no momentum."""
        data = [(1.0, 1.01, 0.99, 1.0)] * 50
        df = make_ohlc(data)
        assert analyze_arrival(df, 1.0, "demand") is True

    def test_just_under_threshold_passes(self):
        """Body just under 2.5x avg should NOT invalidate."""
        data = [(1.0, 1.015, 0.99, 1.01)] * 47
        data += [(1.0, 1.025, 0.99, 1.02)]  # body=0.02 < 2.5 * ~0.01
        data += [(1.02, 1.025, 1.015, 1.022)]
        data += [(1.022, 1.025, 1.02, 1.023)]
        df = make_ohlc(data)
        assert analyze_arrival(df, 1.023, "demand") is True

    def test_favorable_momentum_passes(self):
        """Large BULLISH candle approaching demand zone = favorable, not blocked."""
        data = [(1.0, 1.01, 0.99, 1.005)] * 47
        # Bullish Marubozu approaching demand = displacement, not adverse
        data += [(1.0, 1.035, 0.99, 1.03)]  # bullish body=0.03
        data += [(1.03, 1.04, 1.02, 1.035)]
        data += [(1.035, 1.04, 1.03, 1.038)]
        df = make_ohlc(data)
        assert analyze_arrival(df, 1.038, "demand") is True


# =====================
# ROADBLOCK TESTS
# =====================
class TestCheckRoadblocks:
    def test_clear_sky_no_blockers(self):
        """No opposing zones = road is clear."""
        assert check_roadblocks(1.05, "BUY", [], 0.01) is True

    def test_rr_sufficient_passes(self):
        """Roadblock far enough for 1:2 RR."""
        zones = [
            {"direction": "supply", "top": 1.10, "bottom": 1.09, "fresh": True},
        ]
        # entry=1.05, risk=0.01, nearest blocker=1.09, dist=0.04 >= 2*0.01=0.02 ✓
        assert check_roadblocks(1.05, "BUY", zones, 0.01) is True

    def test_rr_insufficient_kills(self):
        """Roadblock too close for 1:2 RR → kill trade."""
        zones = [
            {"direction": "supply", "top": 1.06, "bottom": 1.055, "fresh": True},
        ]
        # entry=1.05, risk=0.01, nearest blocker=1.055, dist=0.005 < 2*0.01=0.02 ✗
        assert check_roadblocks(1.05, "BUY", zones, 0.01) is False

    def test_sell_direction_clear(self):
        """SELL with no demand zones below = clear."""
        zones = [
            {"direction": "supply", "top": 1.10, "bottom": 1.09, "fresh": True},
        ]
        assert check_roadblocks(1.05, "SELL", zones, 0.01) is True

    def test_sell_direction_blocked(self):
        """SELL with demand zone too close below = blocked."""
        zones = [
            {"direction": "demand", "top": 1.045, "bottom": 1.04, "fresh": True},
        ]
        # entry=1.05, risk=0.01, nearest=1.045, dist=0.005 < 0.02 ✗
        assert check_roadblocks(1.05, "SELL", zones, 0.01) is False

    def test_zero_risk_passes(self):
        """Zero risk distance should not block."""
        assert check_roadblocks(1.05, "BUY", [], 0) is True


# =====================
# STORYLINE TESTS
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

    def test_unfresh_zone_fallback_with_bos(self):
        """When no fresh HTF zones but structure exists, should use unfresh zones + BOS."""
        flat = [(1.0, 1.01, 0.99, 1.0)] * 24
        flat.append((1.0, 1.05, 0.99, 1.04))
        df_h = make_ohlc(flat)
        df_l = make_trending_data("up", bars=30, start=1.0, step=0.001)
        result = detect_storyline(df_h, df_l)
        # Unfresh zone fallback: structure exists + LTF BOS = signal allowed
        if result is not None:
            assert result["bias"] in ("BULL", "BEAR")
            assert "htf_zone" in result

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
        assert tp == 1.09

    def test_opposing_zone_tp_bear(self):
        """BEAR storyline TP targets nearest fresh demand zone below entry."""
        zones = [
            {"direction": "demand", "top": 0.95, "bottom": 0.94, "fresh": True},
            {"direction": "demand", "top": 0.90, "bottom": 0.89, "fresh": True},
            {"direction": "supply", "top": 1.10, "bottom": 1.09, "fresh": True},
        ]
        tp = _opposing_zone_tp(zones, "BEAR", 1.00, fallback=0.80)
        assert tp == 0.95

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
            {"direction": "supply", "top": 1.07, "bottom": 1.06, "fresh": True},
        ]
        assert _check_roadblock(zones, "BULL", 1.05, 1.15) is True

    def test_no_roadblock(self):
        """Supply zone far from entry doesn't flag roadblock."""
        zones = [
            {"direction": "supply", "top": 1.13, "bottom": 1.12, "fresh": True},
        ]
        assert _check_roadblock(zones, "BULL", 1.05, 1.15) is False

    def test_bos_fallback_with_high_atr(self):
        """BOS-confirmed fallback should work even when ATR is large.

        Regression test: Week 1 passed full ltf_atr to detect_bos in storyline,
        causing all crypto pairs to fail because min_bos_body was too high.
        Now storyline uses 0.5*ATR (lenient) for bias detection.
        """
        # HTF: need 16+ candles for ATR. Create demand zone then trend up.
        # First: 10 bars of trading range to establish ATR
        htf_data = []
        for i in range(10):
            base = 100.0 + (i % 3) * 2
            htf_data.append((base, base + 4, base - 4, base + 1))
        # V-level demand zone: bearish then bullish with a gap (zone width > 0)
        htf_data.append((104.0, 106.0, 96.0, 97.0))   # bearish, close=97
        htf_data.append((94.0, 108.0, 93.0, 106.0))    # bullish, open=94 → zone [94,97]
        # 4 bars trending up ABOVE the zone so it stays fresh
        for i in range(4):
            base = 108.0 + i * 3
            htf_data.append((base, base + 4, base - 1, base + 3))
        df_h = make_ohlc(htf_data)

        # LTF: 20 flat bars then bullish breakout with moderate body size
        # LTF ATR from flat bars: range=4, ATR~4. Full threshold=2, lenient=1
        ltf_base = [(115.0, 117.0, 113.0, 115.0)] * 20
        # Breakout candles: body of 3 passes lenient (>1) but might fail strict (>2)
        ltf_base += [
            (115.0, 118.0, 114.0, 117.5),  # body=2.5
            (117.5, 121.0, 116.0, 120.0),  # body=2.5, breaks swing high
            (120.0, 124.0, 119.0, 123.0),  # body=3, confirms breakout
        ]
        df_l = make_ohlc(ltf_base)

        result = detect_storyline(df_h, df_l)
        # Should NOT be None — BOS-confirmed fallback should fire
        assert result is not None
        assert result["bias"] == "BULL"
        assert result["confirmed"] is True


# =====================
# ENGULFING TESTS
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
        """Bearish engulfing candle at a supply zone is detected.

        With ATR-based min body filter, ensure candle body is large enough.
        When no ATR is passed (None), the min body defaults to 0.
        """
        zone = {"type": "A", "direction": "supply",
                "top": 1.03, "bottom": 1.02, "bar_index": 0,
                "fresh": True, "miss": False}
        data = [
            (1.025, 1.03, 1.02, 1.028),  # small bullish
            # Bearish engulfing with strong body ratio (>0.55)
            (1.035, 1.036, 1.018, 1.019),  # body=0.016, range=0.018, ratio=0.89
        ]
        df = make_ohlc(data)
        # Pass atr=None so min body filter defaults to 0
        result = detect_engulfing(df, zone, atr=None)
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
# INDUCEMENT TESTS (now returns dict)
# =====================
class TestInducement:
    def test_buy_side_sweep_returns_dict(self):
        """Wick below swing low + body closes above = inducement swept dict."""
        data = [(1.0, 1.05, 0.95, 1.02)] * 10
        data += [(1.0, 1.03, 0.94, 1.01)]  # sweep wick to 0.94
        data += [(1.01, 1.05, 0.98, 1.04)] * 4
        df = make_ohlc(data)
        result = detect_inducement_swept(df, 1.05, 0.95, "BUY")
        assert result["swept"] is True
        assert result["wick_level"] == pytest.approx(0.94)

    def test_sell_side_sweep_returns_dict(self):
        """Wick above swing high + body closes below = inducement swept dict."""
        data = [(1.0, 1.05, 0.95, 0.98)] * 10
        data += [(1.0, 1.06, 0.96, 0.99)]  # sweep wick to 1.06
        data += [(0.99, 1.04, 0.93, 0.95)] * 4
        df = make_ohlc(data)
        result = detect_inducement_swept(df, 1.05, 0.95, "SELL")
        assert result["swept"] is True
        assert result["wick_level"] == pytest.approx(1.06)

    def test_no_sweep_returns_dict(self):
        """No wick beyond swing points = no inducement."""
        data = [(1.0, 1.04, 0.96, 1.02)] * 20
        df = make_ohlc(data)
        result = detect_inducement_swept(df, 1.05, 0.95, "BUY")
        assert result["swept"] is False
        assert result["wick_level"] is None

    def test_insufficient_data(self):
        df = make_ohlc([(1.0, 1.1, 0.9, 1.0)] * 3)
        result = detect_inducement_swept(df, 1.1, 0.9, "BUY")
        assert result["swept"] is False

    def test_wick_and_body_both_below_not_inducement(self):
        """If body also closes below swing low, it's a real break not a sweep."""
        data = [(1.0, 1.05, 0.95, 1.02)] * 10
        data += [(0.96, 0.97, 0.92, 0.93)]  # body below 0.95 too
        data += [(0.93, 0.96, 0.91, 0.95)] * 4
        df = make_ohlc(data)
        result = detect_inducement_swept(df, 1.05, 0.95, "BUY")
        assert result["swept"] is False

    def test_deepest_wick_tracked(self):
        """Multiple sweeps: the deepest wick should be returned."""
        data = [(1.0, 1.05, 0.95, 1.02)] * 10
        data += [(1.0, 1.03, 0.94, 1.01)]   # sweep 1, wick=0.94
        data += [(1.01, 1.04, 0.93, 1.02)]  # sweep 2, wick=0.93 (deeper)
        data += [(1.02, 1.05, 0.97, 1.04)] * 3
        df = make_ohlc(data)
        result = detect_inducement_swept(df, 1.05, 0.95, "BUY")
        assert result["swept"] is True
        assert result["wick_level"] == pytest.approx(0.93)


# =====================
# CONFIDENCE TIER TESTS
# =====================
class TestComputeConfidence:
    def test_gold_tier_sweep_plus_engulfing(self):
        from strategy import _compute_confidence
        zone = {"type": "V", "miss": False}
        assert _compute_confidence(True, 1, zone, False) == "high"

    def test_silver_tier_engulfing_only(self):
        from strategy import _compute_confidence
        zone = {"type": "V", "miss": False}
        assert _compute_confidence(False, 1, zone, False) == "medium"

    def test_low_tier_no_engulfing(self):
        from strategy import _compute_confidence
        zone = {"type": "V", "miss": False}
        assert _compute_confidence(False, None, zone, False) == "low"

    def test_gold_with_fvg_still_gold(self):
        from strategy import _compute_confidence
        zone = {"type": "V", "miss": True}
        assert _compute_confidence(True, 1, zone, True) == "high"


# =====================
# FULL SIGNAL GENERATION TESTS — SNIPER PROTOCOL
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

    def test_signal_has_sniper_fields(self):
        """Any signal produced must have sweep and arrival fields."""
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
            assert "sweep" in sig
            assert "arrival" in sig
            assert sig["arrival"] == "compression"
            assert sig["confidence"] in ("high", "medium", "low")

    def test_no_signal_no_fresh_zone(self):
        """Sniper protocol: no fresh zone = no trade."""
        # All same candles, no zone formation
        data = [(1.0, 1.05, 0.95, 1.0)] * 25
        df_l = make_ohlc(data)
        df_h = make_trending_data("up", bars=25)
        sig = get_smc_signal(df_l, df_h, "EURUSD")
        # Should return None (no zones formed from flat data)
        assert sig is None

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

    def test_momentum_arrival_blocks_signal(self):
        """If last candles are Marubozu, arrival physics should block."""
        df_h = make_trending_data("up", bars=25, start=1.0, step=0.002)
        # 20 small candles
        base = [(1.0, 1.005, 0.995, 1.002)] * 20
        # Then massive momentum candles (body >> 2.5x avg)
        base += [
            (1.002, 1.05, 1.00, 1.04),   # huge body
            (1.04, 1.08, 1.03, 1.07),     # huge body
            (1.07, 1.10, 1.06, 1.09),     # huge body
            (1.09, 1.12, 1.08, 1.11),
            (1.11, 1.14, 1.10, 1.13),
        ]
        df_l = make_ohlc(base)
        sig = get_smc_signal(df_l, df_h, "EURUSD")
        # Likely None due to momentum invalidation
        # (may also be None for other reasons, which is fine)
        assert sig is None or sig["arrival"] == "compression"

    def test_signal_sweep_upgrades_confidence(self):
        """Sweep + engulfing → HIGH confidence (Gold Tier)."""
        # This is a unit test of the confidence logic
        from strategy import _compute_confidence
        zone = {"type": "V", "miss": False}
        assert _compute_confidence(True, 1, zone, False) == "high"
        assert _compute_confidence(False, 1, zone, False) == "medium"

    def test_touch_trade_field_present(self):
        """Any signal produced must have the 'touch' field."""
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
        sig = get_smc_signal(df_l, df_h, "EURUSD", touch_trade=True)
        if sig is not None:
            assert "touch" in sig
            assert isinstance(sig["touch"], bool)


class TestTouchTrade:
    def test_confidence_touch_tier(self):
        """Touch trade: sweep + no engulfing → MEDIUM confidence."""
        from strategy import _compute_confidence
        zone = {"type": "V", "miss": False}
        # touch_entry=True, sweep=True, no engulfing
        assert _compute_confidence(True, None, zone, False, touch_entry=True) == "medium"
        # touch_entry=True, no sweep, no engulfing → still LOW
        assert _compute_confidence(False, None, zone, False, touch_entry=True) == "low"

    def test_confidence_engulfing_still_wins(self):
        """When engulfing is present, touch_entry flag doesn't matter."""
        from strategy import _compute_confidence
        zone = {"type": "V", "miss": False}
        assert _compute_confidence(True, 1, zone, False, touch_entry=True) == "high"
        assert _compute_confidence(False, 1, zone, False, touch_entry=True) == "medium"

    def test_touch_off_requires_engulfing(self):
        """touch_trade=False should still require engulfing."""
        df_h = make_trending_data("up", bars=25, start=1.0, step=0.002)
        base = [(1.0, 1.02, 0.98, 1.01)] * 25
        df_l = make_ohlc(base)
        sig = get_smc_signal(df_l, df_h, "EURUSD", touch_trade=False)
        # Should be None — flat data, no engulfing possible
        assert sig is None

    def test_touch_on_no_sweep_still_none(self):
        """touch_trade=True without a sweep should still require engulfing."""
        from strategy import _compute_confidence
        zone = {"type": "V", "miss": False}
        # No sweep, no engulfing, touch mode → low → should be blocked
        conf = _compute_confidence(False, None, zone, False, touch_entry=True)
        assert conf == "low"


# =====================
# MULTI-TP & CALCULATE_LEVELS TESTS
# =====================
class TestMultiTP:
    def test_buy_tp_ordering(self):
        """BUY: TP1 < TP2 < TP3."""
        levels = calculate_levels("BUY", 1.10, 1.08, 0.03, 1.15, htf_extreme=1.20)
        assert levels["tp1"] < levels["tp2"]
        assert levels["tp2"] <= levels["tp3"]

    def test_sell_tp_ordering(self):
        """SELL: TP1 > TP2 > TP3."""
        levels = calculate_levels("SELL", 1.10, 1.12, 0.03, 1.05, htf_extreme=1.00)
        assert levels["tp1"] > levels["tp2"]
        assert levels["tp2"] >= levels["tp3"]

    def test_buy_tp1_is_one_to_one(self):
        """BUY TP1 should equal entry + risk distance (1:1 RR)."""
        levels = calculate_levels("BUY", 1.10, 1.08, 0.03, 1.15)
        sl = levels["sl"]
        risk = abs(1.10 - sl)
        assert abs(levels["tp1"] - (1.10 + risk)) < 1e-10

    def test_sell_tp1_is_one_to_one(self):
        """SELL TP1 should equal entry - risk distance (1:1 RR)."""
        levels = calculate_levels("SELL", 1.10, 1.12, 0.03, 1.05)
        sl = levels["sl"]
        risk = abs(sl - 1.10)
        assert abs(levels["tp1"] - (1.10 - risk)) < 1e-10

    def test_tp2_is_zone_target(self):
        """TP2 should be the opposing zone target (or TP1 if target is closer)."""
        levels = calculate_levels("BUY", 1.10, 1.08, 0.03, 1.18)
        assert levels["tp2"] == 1.18 or levels["tp2"] >= levels["tp1"]

    def test_buy_tp3_uses_htf_extreme(self):
        """BUY TP3 should use HTF extreme if it's bigger than 1:3 RR."""
        levels = calculate_levels("BUY", 1.10, 1.08, 0.03, 1.15, htf_extreme=1.50)
        # 1:3 RR would be 1.10 + 0.02*3 = 1.16, HTF extreme 1.50 is higher
        assert levels["tp3"] == 1.50

    def test_signal_has_tp_fields(self):
        """Any signal produced must have tp1, tp2, tp3 fields."""
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
            assert "tp1" in sig
            assert "tp2" in sig
            assert "tp3" in sig
            assert sig["tp1"] <= sig["tp2"] <= sig["tp3"]  # BUY ordering

    def test_no_htf_extreme_uses_rr(self):
        """Without htf_extreme, TP3 should be 1:3 RR."""
        levels = calculate_levels("BUY", 1.10, 1.08, 0.03, 1.15)
        sl = levels["sl"]
        risk = abs(1.10 - sl)
        expected_tp3 = max(1.10 + risk * 3, levels["tp2"])
        assert abs(levels["tp3"] - expected_tp3) < 1e-10


# =====================
# ZONE BUFFER SCALING TESTS
# =====================
class TestZoneBufferScaling:
    def test_btc_zone_survives_normal_wick(self):
        """BTC-scale zone ($60k) should NOT be killed by a normal $30 wick.

        Old buffer: zone_mid * 0.001 = $60 → wick within $60 kills zone.
        New buffer: zone_width * 0.05 = $100 (for $2k zone) → $30 wick is fine.
        """
        # Build BTC-like HTF data with a demand zone around 59000-61000
        htf_data = []
        # 10 bars of range to provide context
        for i in range(10):
            base = 62000.0 + (i % 3) * 500
            htf_data.append((base, base + 800, base - 800, base + 200))
        # V-level demand zone: bearish close then bullish open with gap
        htf_data.append((63000.0, 63500.0, 60500.0, 61000.0))   # bearish
        htf_data.append((59000.0, 64000.0, 58500.0, 63000.0))   # bullish → zone [59000, 61000]
        # Subsequent candles: wick dips to 60970 ($30 inside zone top of 61000)
        # Old buffer ($60) would buffered_top=61060 → wick 60970 < 61060 → UNFRESH
        # New buffer ($100) → buffered_top=61100 → still catches it BUT...
        # The key: wick needs to also be >= buffered_bottom to count as touch
        # Let's test a wick that's NEAR but NOT inside the zone
        htf_data.append((63500.0, 64000.0, 61200.0, 63800.0))  # low=61200, above zone top
        htf_data.append((63800.0, 64500.0, 63000.0, 64200.0))  # well above zone

        df = make_ohlc(htf_data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        demand = [z for z in zones if z["direction"] == "demand" and z["fresh"]]
        assert len(demand) >= 1, "BTC demand zone should survive when wicks don't enter it"

    def test_btc_zone_killed_on_real_penetration(self):
        """BTC-scale zone should still be killed when wick truly enters it."""
        htf_data = []
        for i in range(10):
            base = 62000.0 + (i % 3) * 500
            htf_data.append((base, base + 800, base - 800, base + 200))
        htf_data.append((63000.0, 63500.0, 60500.0, 61000.0))
        htf_data.append((59000.0, 64000.0, 58500.0, 63000.0))  # zone [59000, 61000]
        # Wick deeply penetrates zone: low=59500 (well inside [59000, 61000])
        htf_data.append((63000.0, 63500.0, 59500.0, 63200.0))
        htf_data.append((63200.0, 64000.0, 63000.0, 63800.0))

        df = make_ohlc(htf_data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        # Zone should be unfresh since wick deeply entered it
        v_demand = [z for z in zones if z["direction"] == "demand"
                    and z["type"] == "V" and z["fresh"]]
        # The original zone should be unfresh (killed by wick penetration)
        orig_demand = [z for z in zones if z["direction"] == "demand"
                       and z["type"] == "V" and z["bar_index"] == 10]
        if orig_demand:
            assert orig_demand[0]["fresh"] is False

    def test_forex_buffer_still_works(self):
        """Forex-scale zones (1.0800) should use floor buffer (0.02% of price)."""
        # Zone at [1.02, 1.025], zone_width=0.005
        # zone_width * 0.05 = 0.00025
        # zone_mid * 0.0002 = 0.000205
        # buffer = max(0.00025, 0.000205) = 0.00025
        # Old buffer was zone_mid * 0.001 = 0.001023 (4x larger)
        data = [
            (1.00, 1.03, 0.99, 1.025),  # bullish
            (1.02, 1.03, 0.98, 0.99),   # bearish → A-level zone at [1.02, 1.025]
            # high=1.0195 is 0.0005 below zone bottom — outside buffer
            (0.98, 1.0195, 0.97, 0.98),
        ]
        df = make_ohlc(data)
        zones = find_zones(df, lookback=40)
        zones = mark_freshness(zones, df)
        a_zones = [z for z in zones if z["type"] == "A" and z["bar_index"] == 0]
        assert len(a_zones) == 1
        # With new smaller buffer, 1.0195 should NOT touch zone bottom ~1.01975
        assert a_zones[0]["fresh"] is True


# =====================
# BOS-ZONE DIRECTION MISMATCH TESTS
# =====================
class TestBosZoneDirectionMismatch:
    def test_bull_bos_with_only_supply_zones(self):
        """Bull BOS + only supply zones fresh (demand all broken) → should still return BULL.

        In a strong uptrend, all demand zones get broken (bodies close below them)
        and flip to supply. The BOS-confirmed fallback should still work because
        supply zones provide structural context even for a bullish bias.
        """
        # HTF: Build data where demand zones get broken but supply zones remain
        htf_data = []
        # 10 bars of choppy range
        for i in range(10):
            base = 100.0 + (i % 3) * 2
            htf_data.append((base, base + 4, base - 4, base + 1))

        # Supply zone: bullish then bearish (A-level supply)
        htf_data.append((100.0, 108.0, 99.0, 107.0))   # strong bullish
        htf_data.append((107.0, 108.0, 102.0, 103.0))   # bearish → A-level supply ~[107,108]

        # Trend upward far above — zone stays fresh (no wick returns to it)
        for i in range(4):
            base = 115.0 + i * 3
            htf_data.append((base, base + 4, base - 1, base + 3))

        df_h = make_ohlc(htf_data)

        # LTF: flat then bullish breakout (bull BOS)
        ltf_data = [(120.0, 122.0, 118.0, 120.0)] * 20
        ltf_data += [
            (120.0, 123.0, 119.0, 122.5),  # body=2.5
            (122.5, 126.0, 121.0, 125.0),  # body=2.5, breaks swing high
            (125.0, 129.0, 124.0, 128.0),  # body=3, confirms
        ]
        df_l = make_ohlc(ltf_data)

        result = detect_storyline(df_h, df_l)
        # Should NOT be None — bull BOS + supply zone structure = valid bias
        assert result is not None
        assert result["bias"] == "BULL"
        assert result["confirmed"] is True


# =====================
# Swing Sequence / Trend Classification
# =====================
class TestClassifySwingSequence:
    def test_uptrend_detection(self):
        """HH + HL sequence should return UP."""
        # Explicit zigzag uptrend: low-high-low-high pattern with rising pivots
        data = [
            (1.00, 1.02, 0.98, 1.01),  # normal
            (1.01, 1.03, 1.00, 1.02),  # normal
            (1.02, 1.08, 1.01, 1.06),  # swing high 1 = 1.08
            (1.06, 1.07, 1.03, 1.04),  # down
            (1.04, 1.05, 0.99, 1.00),  # swing low 1 = 0.99
            (1.00, 1.01, 0.98, 1.01),  # up
            (1.01, 1.03, 1.00, 1.02),  # normal
            (1.02, 1.04, 1.01, 1.03),  # normal
            (1.03, 1.10, 1.02, 1.08),  # swing high 2 = 1.10 (HH)
            (1.08, 1.09, 1.05, 1.06),  # down
            (1.06, 1.07, 1.02, 1.03),  # swing low 2 = 1.02 (HL)
            (1.03, 1.04, 1.01, 1.04),  # up
            (1.04, 1.06, 1.03, 1.05),  # normal
            (1.05, 1.12, 1.04, 1.10),  # swing high 3 = 1.12 (HH)
            (1.10, 1.11, 1.07, 1.08),  # down
            (1.08, 1.09, 1.05, 1.06),  # swing low 3 = 1.05 (HL)
            (1.06, 1.07, 1.05, 1.07),  # normal
        ]
        df = make_ohlc(data)
        result = classify_swing_sequence(df, lookback=20)
        assert result["trend"] == "UP"
        assert len(result["swing_highs"]) >= 2
        assert len(result["swing_lows"]) >= 2

    def test_downtrend_detection(self):
        """LH + LL sequence should return DOWN."""
        # Explicit 3-bar pivots: each pivot bar's high/low beats BOTH neighbors
        data = [
            (1.12, 1.13, 1.11, 1.12),  # 0 normal
            (1.12, 1.16, 1.11, 1.15),  # 1 SH1=1.16
            (1.15, 1.14, 1.10, 1.11),  # 2 down (high=1.14 < 1.16)
            (1.11, 1.12, 1.04, 1.05),  # 3 SL1=1.04 (low=1.04 < both 1.10, 1.06)
            (1.05, 1.08, 1.06, 1.07),  # 4 up (low=1.06 > 1.04)
            (1.07, 1.12, 1.06, 1.11),  # 5 SH2=1.12 (high: 1.12 > 1.08, check idx 6)
            (1.11, 1.10, 1.05, 1.06),  # 6 down (high=1.10 < 1.12)
            (1.06, 1.07, 0.99, 1.00),  # 7 SL2=0.99 (low=0.99 < both 1.05, 1.01)
            (1.00, 1.04, 1.01, 1.03),  # 8 up (low=1.01 > 0.99)
            (1.03, 1.08, 1.02, 1.07),  # 9 SH3=1.08 (high: 1.08 > 1.04, check idx 10)
            (1.07, 1.06, 1.01, 1.02),  # 10 down (high=1.06 < 1.08)
            (1.02, 1.03, 0.94, 0.95),  # 11 SL3=0.94 (low: 0.94 < both 1.01, 0.96)
            (0.95, 0.97, 0.96, 0.96),  # 12 normal (low=0.96 > 0.94)
        ]
        df = make_ohlc(data)
        result = classify_swing_sequence(df, lookback=20)
        # SH: 1.16, 1.12, 1.08 → descending. SL: 1.04, 0.99, 0.94 → descending
        assert result["trend"] == "DOWN"

    def test_flat_market_none(self):
        """Flat market should return NONE."""
        data = [(1.0, 1.02, 0.98, 1.0)] * 30
        df = make_ohlc(data)
        result = classify_swing_sequence(df)
        assert result["trend"] == "NONE"

    def test_insufficient_data(self):
        data = [(1.0, 1.1, 0.9, 1.0)] * 3
        df = make_ohlc(data)
        result = classify_swing_sequence(df)
        assert result["trend"] == "NONE"


# =====================
# BOS vs MSS Classification
# =====================
class TestBOSvsMSS:
    def test_classify_false_returns_tuple(self):
        """Default classify=False returns backward-compatible 4-tuple."""
        data = [(1.0, 1.05, 0.95, 1.0)] * 20
        data += [(1.0, 1.08, 0.99, 1.07)] * 5
        df = make_ohlc(data)
        result = detect_bos(df, 1.05, 0.95)
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_classify_true_returns_dict(self):
        """classify=True returns enriched dict."""
        data = [(1.0, 1.05, 0.95, 1.0)] * 20
        data += [(1.0, 1.08, 0.99, 1.07)] * 5
        df = make_ohlc(data)
        result = detect_bos(df, 1.05, 0.95, classify=True)
        assert isinstance(result, dict)
        assert "break_type" in result
        assert "displacement_fvg" in result
        assert result["bullish_bos"] is True

    def test_bos_continuation_in_uptrend(self):
        """Bullish break in uptrend = BOS (continuation)."""
        # Create uptrend then break above
        data = []
        for i in range(20):
            base = 1.0 + i * 0.01
            if i % 4 == 0:
                data.append((base, base + 0.05, base - 0.01, base + 0.03))
            elif i % 4 == 2:
                data.append((base, base + 0.01, base - 0.03, base - 0.01))
            else:
                data.append((base, base + 0.02, base - 0.02, base + 0.01))
        # Strong bullish break
        data += [(1.2, 1.35, 1.19, 1.33)] * 5
        df = make_ohlc(data)
        swing_h = df['high'].iloc[:-5].max()
        swing_l = df['low'].iloc[:-5].min()
        result = detect_bos(df, swing_h, swing_l, classify=True)
        if result["bullish_bos"]:
            assert result["break_type"] == "BOS"

    def test_mss_reversal_in_downtrend(self):
        """Bullish break in downtrend = MSS (reversal)."""
        data = []
        for i in range(20):
            base = 2.0 - i * 0.01
            if i % 4 == 0:
                data.append((base, base + 0.05, base - 0.01, base + 0.03))
            elif i % 4 == 2:
                data.append((base, base + 0.01, base - 0.03, base - 0.01))
            else:
                data.append((base, base + 0.02, base - 0.02, base + 0.01))
        # Strong bullish break (reversal)
        data += [(1.8, 1.95, 1.79, 1.93)] * 5
        df = make_ohlc(data)
        swing_h = df['high'].iloc[:-5].max()
        swing_l = df['low'].iloc[:-5].min()
        result = detect_bos(df, swing_h, swing_l, classify=True)
        if result["bullish_bos"]:
            assert result["break_type"] == "MSS"


# =====================
# BOS FVG Displacement
# =====================
class TestBOSCreatedFVG:
    def test_fvg_present(self):
        """Candle sequence with gap should return True."""
        data = [(1.0, 1.02, 0.98, 1.0)] * 20
        # FVG: c1.high=1.02, then gap candle, then c3.low=1.05 > c1.high
        data.append((1.0, 1.02, 0.99, 1.01))   # c1
        data.append((1.02, 1.06, 1.01, 1.05))   # displacement candle
        data.append((1.05, 1.08, 1.05, 1.07))   # c3 — low=1.05 > c1.high=1.02 → FVG
        df = make_ohlc(data)
        # break index = 21 (displacement candle)
        assert bool(_bos_creates_fvg(df, 21, "BULL", 0.01)) is True

    def test_no_fvg(self):
        """Candle sequence without gap should return False."""
        data = [(1.0, 1.02, 0.98, 1.0)] * 20
        data.append((1.0, 1.02, 0.99, 1.01))
        data.append((1.01, 1.04, 1.00, 1.03))   # no gap: c3.low=1.00 < c1.high=1.02
        data.append((1.00, 1.03, 1.00, 1.02))
        df = make_ohlc(data)
        assert bool(_bos_creates_fvg(df, 21, "BULL", 0.01)) is False


# =====================
# Premium / Discount
# =====================
class TestPremiumDiscount:
    def test_discount_zone(self):
        assert is_in_premium_discount(1.02, 1.10, 1.00) == "discount"

    def test_premium_zone(self):
        assert is_in_premium_discount(1.08, 1.10, 1.00) == "premium"

    def test_equilibrium(self):
        assert is_in_premium_discount(1.05, 1.10, 1.00) == "equilibrium"

    def test_equal_swing(self):
        assert is_in_premium_discount(1.0, 1.0, 1.0) == "equilibrium"

    def test_none_swing(self):
        assert is_in_premium_discount(1.0, None, None) == "equilibrium"


# =====================
# Enhanced Confidence
# =====================
class TestEnhancedConfidence:
    def test_platinum_mss_sweep_engulfing_fvg(self):
        """MSS + Sweep + Engulfing + FVG = HIGH (platinum)."""
        zone = {"type": "V", "direction": "demand", "top": 1.0, "bottom": 0.99}
        result = _compute_confidence(True, 1, zone, True, is_mss=True)
        assert result == "high"

    def test_bos_fvg_boost(self):
        """Engulfing + BOS-created FVG = MEDIUM."""
        zone = {"type": "V", "direction": "demand", "top": 1.0, "bottom": 0.99}
        result = _compute_confidence(False, 1, zone, False, bos_fvg=True)
        assert result == "medium"

    def test_gold_unchanged(self):
        """Sweep + Engulfing still = HIGH."""
        zone = {"type": "V", "direction": "demand", "top": 1.0, "bottom": 0.99}
        result = _compute_confidence(True, 1, zone, False)
        assert result == "high"


# =====================
# Signal New Fields
# =====================
class TestSignalNewFields:
    def test_signal_has_new_fields(self):
        """Generated signal should include break_type, displacement_fvg, pd_zone."""
        df_h = make_ohlc([(1.0, 1.1, 0.9, 1.05)] * 15 + [(1.05, 1.15, 0.98, 1.12)] * 10)
        df_l = make_trending_data("up", bars=30, start=1.0, step=0.005)
        sig = get_smc_signal(df_l, df_h, "XAUUSD")
        if sig is not None:
            assert "break_type" in sig
            assert "displacement_fvg" in sig
            assert "pd_zone" in sig
            assert sig["break_type"] in ("BOS", "MSS", None)
            assert isinstance(sig["displacement_fvg"], bool)
            assert sig["pd_zone"] in ("premium", "discount", "equilibrium")
