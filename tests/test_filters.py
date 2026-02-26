import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone
from filters import is_in_session, is_market_open, is_news_blackout


def _utc(year, month, day, hour, minute=0):
    """Create a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# =====================
# SESSION FILTER TESTS
# =====================
class TestIsInSession:
    @patch('filters.datetime')
    def test_london_session_in_range(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 12, 10)
        assert is_in_session("LONDON") is True

    @patch('filters.datetime')
    def test_london_session_out_of_range(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 12, 5)
        assert is_in_session("LONDON") is False

    @patch('filters.datetime')
    def test_ny_session_in_range(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 12, 15)
        assert is_in_session("NY") is True

    @patch('filters.datetime')
    def test_ny_session_out_of_range(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 12, 22)
        assert is_in_session("NY") is False

    @patch('filters.datetime')
    def test_both_always_true(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 12, 3)
        assert is_in_session("BOTH") is True

    @patch('filters.datetime')
    def test_london_boundary_start(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 12, 7)
        assert is_in_session("LONDON") is True

    @patch('filters.datetime')
    def test_london_boundary_end(self, mock_dt):
        # 16 is now exclusive (< 16), so hour 16 is outside London
        mock_dt.now.return_value = _utc(2026, 2, 12, 15)
        assert is_in_session("LONDON") is True


# =====================
# MARKET OPEN TESTS
# =====================
class TestIsMarketOpen:
    def test_crypto_always_open(self):
        assert is_market_open("BTCUSD") is True
        assert is_market_open("ETHUSD") is True

    def test_volatility_always_open(self):
        assert is_market_open("V75") is True
        assert is_market_open("V100_1S") is True

    def test_boom_crash_always_open(self):
        assert is_market_open("BOOM300") is True
        assert is_market_open("CRASH500") is True

    def test_step_always_open(self):
        assert is_market_open("STEP_INDEX") is True

    @patch('filters.datetime')
    def test_forex_friday_evening_closed(self, mock_dt):
        # Friday Feb 13 2026 is a Friday, 22:00 UTC
        mock_dt.now.return_value = _utc(2026, 2, 13, 22)
        assert is_market_open("EURUSD") is False

    @patch('filters.datetime')
    def test_forex_saturday_closed(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 14, 12)  # Saturday
        assert is_market_open("EURUSD") is False

    @patch('filters.datetime')
    def test_forex_sunday_morning_closed(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 15, 10)  # Sunday morning
        assert is_market_open("EURUSD") is False

    @patch('filters.datetime')
    def test_forex_sunday_evening_open(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 15, 22)  # Sunday 22:00
        assert is_market_open("XAUUSD") is True

    @patch('filters.datetime')
    def test_forex_weekday_open(self, mock_dt):
        mock_dt.now.return_value = _utc(2026, 2, 11, 14)  # Wednesday
        assert is_market_open("GBPUSD") is True


# =====================
# NEWS BLACKOUT TESTS (now async)
# =====================
class TestNewsBlackout:
    @pytest.mark.asyncio
    @patch('filters.USE_NEWS_FILTER', False)
    async def test_disabled_filter(self):
        assert await is_news_blackout("EURUSD") is False

    @pytest.mark.asyncio
    @patch('filters.fetch_forex_news', new_callable=AsyncMock)
    @patch('filters._NEWS_CACHE', [])
    async def test_no_news_no_blackout(self, mock_fetch):
        assert await is_news_blackout("EURUSD") is False

    @pytest.mark.asyncio
    @patch('filters.fetch_forex_news', new_callable=AsyncMock)
    async def test_blackout_during_news(self, mock_fetch):
        import filters
        now = datetime.utcnow()
        filters._NEWS_CACHE = [
            {"currency": "USD", "time": now}
        ]
        assert await is_news_blackout("EURUSD") is True
        assert await is_news_blackout("XAUUSD") is True  # XAU -> USD
        filters._NEWS_CACHE = []

    @pytest.mark.asyncio
    @patch('filters.fetch_forex_news', new_callable=AsyncMock)
    async def test_no_blackout_unrelated_currency(self, mock_fetch):
        import filters
        now = datetime.utcnow()
        filters._NEWS_CACHE = [
            {"currency": "JPY", "time": now}
        ]
        assert await is_news_blackout("EURUSD") is False
        filters._NEWS_CACHE = []
