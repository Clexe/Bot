import pytest
from correlation import (
    get_pair_currencies, compute_currency_exposure,
    check_correlation, get_exposure_summary,
)


class TestGetPairCurrencies:
    def test_forex_pair(self):
        assert get_pair_currencies("EURUSD") == ("EUR", "USD")

    def test_gold(self):
        assert get_pair_currencies("XAUUSD") == ("XAU", "USD")

    def test_crypto_returns_none(self):
        assert get_pair_currencies("BTCUSD") == (None, None)

    def test_synthetic_returns_none(self):
        assert get_pair_currencies("V75") == (None, None)


class TestComputeCurrencyExposure:
    def test_single_buy(self):
        positions = [{"pair": "EURUSD", "direction": "BUY"}]
        exp = compute_currency_exposure(positions)
        assert exp["EUR"] == 1
        assert exp["USD"] == -1

    def test_single_sell(self):
        positions = [{"pair": "EURUSD", "direction": "SELL"}]
        exp = compute_currency_exposure(positions)
        assert exp["EUR"] == -1
        assert exp["USD"] == 1

    def test_correlated_buys(self):
        positions = [
            {"pair": "EURUSD", "direction": "BUY"},
            {"pair": "GBPUSD", "direction": "BUY"},
        ]
        exp = compute_currency_exposure(positions)
        assert exp["USD"] == -2  # Double short USD

    def test_hedged_positions(self):
        positions = [
            {"pair": "EURUSD", "direction": "BUY"},
            {"pair": "EURUSD", "direction": "SELL"},
        ]
        exp = compute_currency_exposure(positions)
        assert exp.get("EUR", 0) == 0
        assert exp.get("USD", 0) == 0

    def test_crypto_ignored(self):
        positions = [{"pair": "BTCUSD", "direction": "BUY"}]
        exp = compute_currency_exposure(positions)
        assert len(exp) == 0


class TestCheckCorrelation:
    def test_first_trade_allowed(self):
        ok, _ = check_correlation("EURUSD", "BUY", [])
        assert ok is True

    def test_correlated_blocked(self):
        positions = [
            {"pair": "EURUSD", "direction": "BUY"},
            {"pair": "GBPUSD", "direction": "BUY"},
        ]
        # USD exposure already at -2, adding AUDUSD BUY would make it -3
        ok, reason = check_correlation("AUDUSD", "BUY", positions)
        assert ok is False
        assert "USD" in reason

    def test_opposite_direction_allowed(self):
        positions = [
            {"pair": "EURUSD", "direction": "BUY"},
            {"pair": "GBPUSD", "direction": "BUY"},
        ]
        # SELL AUDUSD would increase USD exposure (which is -2 → -1, ok)
        ok, _ = check_correlation("AUDUSD", "SELL", positions)
        assert ok is True

    def test_crypto_bypasses_check(self):
        positions = [
            {"pair": "EURUSD", "direction": "BUY"},
            {"pair": "GBPUSD", "direction": "BUY"},
            {"pair": "AUDUSD", "direction": "BUY"},
        ]
        ok, _ = check_correlation("BTCUSD", "BUY", positions)
        assert ok is True

    def test_group_limit(self):
        positions = [
            {"pair": "EURUSD", "direction": "BUY"},
            {"pair": "GBPUSD", "direction": "BUY"},
        ]
        # Both in USD_SHORTS group with BUY direction = 2 (max)
        ok, reason = check_correlation("AUDUSD", "BUY", positions,
                                       max_currency_exposure=5,  # bypass currency limit
                                       max_group_same_dir=2)
        assert ok is False
        assert "USD_SHORTS" in reason


class TestGetExposureSummary:
    def test_empty(self):
        assert get_exposure_summary([]) == "No currency exposure"

    def test_single_position(self):
        summary = get_exposure_summary([{"pair": "EURUSD", "direction": "BUY"}])
        assert "EUR" in summary
        assert "LONG" in summary

    def test_neutral_after_hedge(self):
        positions = [
            {"pair": "EURUSD", "direction": "BUY"},
            {"pair": "EURUSD", "direction": "SELL"},
        ]
        assert get_exposure_summary(positions) == "Neutral"
