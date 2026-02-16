import pandas as pd
import pytest
from regime import (
    compute_atr, compute_atr_ratio, compute_trend_strength,
    detect_regime, should_skip_regime,
)


def _make_df(data, columns=("open", "high", "low", "close")):
    return pd.DataFrame(data, columns=columns)


def _trending_bull_df(n=50):
    """Create a steadily rising price series."""
    rows = []
    for i in range(n):
        o = 100 + i
        c = o + 0.5
        h = c + 0.2
        l = o - 0.2
        rows.append((o, h, l, c))
    return _make_df(rows)


def _trending_bear_df(n=50):
    rows = []
    for i in range(n):
        o = 200 - i
        c = o - 0.5
        h = o + 0.2
        l = c - 0.2
        rows.append((o, h, l, c))
    return _make_df(rows)


def _ranging_df(n=50):
    """Create a sideways choppy market."""
    rows = []
    for i in range(n):
        base = 100 + (0.3 if i % 2 == 0 else -0.3)
        o = base
        c = base + 0.1
        h = base + 0.2
        l = base - 0.2
        rows.append((o, h, l, c))
    return _make_df(rows)


def _volatile_df(n=50):
    """Create volatile choppy candles with huge wicks but no direction."""
    rows = []
    for i in range(n):
        direction = 1 if i % 2 == 0 else -1
        o = 100 + direction * 5
        c = 100 - direction * 5
        h = max(o, c) + 8
        l = min(o, c) - 8
        rows.append((o, h, l, c))
    return _make_df(rows)


class TestComputeATR:
    def test_insufficient_data(self):
        df = _make_df([(1, 2, 0.5, 1.5)] * 5)
        assert compute_atr(df, period=14) is None

    def test_returns_positive_value(self):
        df = _trending_bull_df(30)
        atr = compute_atr(df, period=14)
        assert atr is not None
        assert atr > 0


class TestComputeATRRatio:
    def test_returns_around_one_for_stable(self):
        df = _ranging_df(60)
        ratio = compute_atr_ratio(df, fast=7, slow=28)
        assert 0.5 < ratio < 2.0

    def test_insufficient_data_returns_default(self):
        df = _make_df([(1, 2, 0.5, 1.5)] * 3)
        assert compute_atr_ratio(df) == 1.0


class TestComputeTrendStrength:
    def test_bull_trend_positive(self):
        df = _trending_bull_df()
        ts = compute_trend_strength(df)
        assert ts > 0.3

    def test_bear_trend_negative(self):
        df = _trending_bear_df()
        ts = compute_trend_strength(df)
        assert ts < -0.3

    def test_ranging_near_zero(self):
        df = _ranging_df()
        ts = compute_trend_strength(df)
        assert abs(ts) < 0.3


class TestDetectRegime:
    def test_trending_bull(self):
        df = _trending_bull_df()
        result = detect_regime(df)
        assert result["regime"] in ("TRENDING_BULL", "RANGING")
        assert result["atr"] > 0

    def test_trending_bear(self):
        df = _trending_bear_df()
        result = detect_regime(df)
        assert result["regime"] in ("TRENDING_BEAR", "RANGING")

    def test_ranging(self):
        df = _ranging_df()
        result = detect_regime(df)
        assert result["regime"] in ("RANGING", "TRENDING_BULL", "TRENDING_BEAR")

    def test_volatile(self):
        df = _volatile_df(60)
        result = detect_regime(df)
        # With extreme wicks and no direction, should detect volatility
        assert result["regime"] in ("VOLATILE", "RANGING")

    def test_unknown_on_small_data(self):
        df = _make_df([(1, 2, 0.5, 1.5)] * 3)
        result = detect_regime(df)
        assert result["regime"] == "UNKNOWN"
        assert result["sl_multiplier"] == 1.0


class TestShouldSkipRegime:
    def test_volatile_skips(self):
        info = {"regime": "VOLATILE", "trend_strength": 0.1}
        skip, reason = should_skip_regime(info, "BUY")
        assert skip is True
        assert reason == "volatile_chop"

    def test_strong_counter_trend_skips(self):
        info = {"regime": "TRENDING_BULL", "trend_strength": 0.6}
        skip, reason = should_skip_regime(info, "SELL")
        assert skip is True
        assert reason == "counter_trend"

    def test_with_trend_passes(self):
        info = {"regime": "TRENDING_BULL", "trend_strength": 0.6}
        skip, _ = should_skip_regime(info, "BUY")
        assert skip is False

    def test_ranging_allows_both(self):
        info = {"regime": "RANGING", "trend_strength": 0.1}
        skip_buy, _ = should_skip_regime(info, "BUY")
        skip_sell, _ = should_skip_regime(info, "SELL")
        assert skip_buy is False
        assert skip_sell is False

    def test_weak_counter_trend_allowed(self):
        info = {"regime": "TRENDING_BULL", "trend_strength": 0.3}
        skip, _ = should_skip_regime(info, "SELL")
        assert skip is False
