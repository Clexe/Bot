import time
import pytest
from drawdown import (
    check_circuit_breaker, record_trade_result, set_open_trade_count,
    get_drawdown_status, reset_streak, configure,
    _daily_pnl, _weekly_pnl,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset drawdown state between tests."""
    import drawdown
    drawdown._daily_pnl.clear()
    drawdown._weekly_pnl.clear()
    drawdown._consecutive_losses = 0
    drawdown._last_loss_time = 0
    drawdown._open_trade_count = 0
    drawdown._pause_until = 0
    configure(daily_loss=-150, weekly_loss=-300, max_streak=4,
              pause_hours=4, max_open=5)
    yield


class TestCheckCircuitBreaker:
    def test_all_clear_by_default(self):
        allowed, reason, mult = check_circuit_breaker()
        assert allowed is True
        assert mult == 1.0

    def test_max_open_trades(self):
        set_open_trade_count(5)
        allowed, reason, _ = check_circuit_breaker()
        assert allowed is False
        assert "max_open_trades" in reason

    def test_daily_loss_limit(self):
        record_trade_result(-200, False)
        allowed, reason, _ = check_circuit_breaker()
        assert allowed is False
        assert "daily_loss_limit" in reason

    def test_weekly_drawdown_reduces_size(self):
        record_trade_result(-350, False)
        allowed, reason, mult = check_circuit_breaker()
        # Daily limit hit first since -350 > -150
        assert allowed is False


class TestRecordTradeResult:
    def test_win_resets_streak(self):
        record_trade_result(-50, False)
        record_trade_result(-50, False)
        record_trade_result(100, True)
        status = get_drawdown_status()
        assert status["consecutive_losses"] == 0

    def test_loss_streak_triggers_pause(self):
        for _ in range(4):
            record_trade_result(-30, False)
        status = get_drawdown_status()
        assert status["paused"] is True
        assert status["pause_remaining_min"] > 0

    def test_daily_pnl_tracks(self):
        record_trade_result(50, True)
        record_trade_result(-30, False)
        status = get_drawdown_status()
        assert status["daily_pnl"] == 20.0


class TestResetStreak:
    def test_clears_pause(self):
        for _ in range(4):
            record_trade_result(-30, False)
        assert get_drawdown_status()["paused"] is True
        reset_streak()
        assert get_drawdown_status()["paused"] is False
        assert get_drawdown_status()["consecutive_losses"] == 0


class TestGetDrawdownStatus:
    def test_returns_all_fields(self):
        status = get_drawdown_status()
        assert "daily_pnl" in status
        assert "weekly_pnl" in status
        assert "consecutive_losses" in status
        assert "open_trades" in status
        assert "paused" in status
        assert "daily_limit" in status
        assert "weekly_limit" in status
