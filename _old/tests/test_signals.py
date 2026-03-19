import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import time
from signals import format_signal_msg, should_send_signal, cleanup_old_signals


# =====================
# FORMAT SIGNAL MESSAGE TESTS
# =====================
class TestFormatSignalMsg:
    def setup_method(self):
        self.sig = {
            "act": "BUY",
            "limit_e": 1.10000,
            "limit_sl": 1.09500,
            "market_e": 1.10200,
            "market_sl": 1.09700,
            "tp": 1.12000,
            "tp1": 1.10700,
            "tp2": 1.12000,
            "tp3": 1.13500,
        }

    def test_market_mode_format(self):
        msg = format_signal_msg(self.sig, "EURUSD", "MARKET")
        assert "MARKET" in msg
        assert "BUY" in msg
        assert "EURUSD" in msg
        assert "1.10200" in msg
        assert "R:R" in msg

    def test_limit_mode_format(self):
        msg = format_signal_msg(self.sig, "EURUSD", "LIMIT")
        assert "LIMIT" in msg
        assert "1.10000" in msg

    def test_rr_calculation(self):
        msg = format_signal_msg(self.sig, "EURUSD", "MARKET")
        # Entry 1.102, SL 1.097, TP2 1.120
        # Risk = 0.005, Reward = 0.018, R:R = 3.6
        assert "1:3.6" in msg

    def test_multi_tp_shown(self):
        msg = format_signal_msg(self.sig, "EURUSD", "MARKET")
        assert "TP1:" in msg
        assert "TP2:" in msg
        assert "TP3:" in msg
        assert "(1:1)" in msg
        assert "(Zone)" in msg
        assert "(Runner)" in msg

    def test_sell_signal(self):
        sig = {
            "act": "SELL",
            "limit_e": 1.10000,
            "limit_sl": 1.10500,
            "market_e": 1.09800,
            "market_sl": 1.10300,
            "tp": 1.08000,
            "tp1": 1.09300,
            "tp2": 1.08000,
            "tp3": 1.06500,
        }
        msg = format_signal_msg(sig, "GBPUSD", "MARKET")
        assert "SELL" in msg
        assert "GBPUSD" in msg

    def test_zero_risk(self):
        sig = {**self.sig, "market_sl": self.sig["market_e"]}
        msg = format_signal_msg(sig, "EURUSD", "MARKET")
        assert "N/A" in msg

    def test_confidence_shown(self):
        sig = {**self.sig, "confidence": "high", "sweep": True, "touch": False}
        msg = format_signal_msg(sig, "EURUSD", "MARKET")
        assert "[HIGH]" in msg
        assert "SWEEP" in msg

    def test_touch_trade_tag(self):
        sig = {**self.sig, "confidence": "medium", "sweep": True, "touch": True}
        msg = format_signal_msg(sig, "EURUSD", "LIMIT")
        assert "TOUCH" in msg
        assert "SWEEP" in msg
        assert "[MEDIUM]" in msg

    def test_no_extra_tags_when_absent(self):
        sig = {**self.sig}  # no confidence/sweep/touch keys
        msg = format_signal_msg(sig, "EURUSD", "MARKET")
        assert "TOUCH" not in msg
        assert "SWEEP" not in msg

    def test_lot_size_shown_with_balance(self):
        msg = format_signal_msg(self.sig, "EURUSD", "MARKET",
                                balance=10000, risk_pct=1, pip_value=10000)
        assert "Lot:" in msg
        assert "$10,000" in msg

    def test_lot_size_hidden_without_balance(self):
        msg = format_signal_msg(self.sig, "EURUSD", "MARKET", balance=0)
        assert "Lot:" not in msg

    def test_risk_pips_shown(self):
        msg = format_signal_msg(self.sig, "EURUSD", "MARKET", pip_value=10000)
        assert "Risk:" in msg
        assert "pips" in msg

    def test_backward_compat_no_tp_fields(self):
        """Old signal dicts without tp1/tp2/tp3 should still work."""
        sig = {
            "act": "BUY",
            "limit_e": 1.10, "limit_sl": 1.09,
            "market_e": 1.10, "market_sl": 1.09,
            "tp": 1.12,
        }
        msg = format_signal_msg(sig, "EURUSD", "MARKET")
        assert "TP1:" in msg  # falls back to tp for all 3


# =====================
# LOT SIZE CALCULATION TESTS
# =====================
class TestLotSize:
    def test_standard_forex_lot(self):
        from signals import _calc_lot_size
        # $10k balance, 1% risk = $100 risk, 50 pips * $10/pip/lot = $500/lot
        lot = _calc_lot_size(50, 10000, 10000, 1)
        assert lot == 0.20  # 100 / 500

    def test_zero_balance_returns_none(self):
        from signals import _calc_lot_size
        assert _calc_lot_size(50, 10000, 0, 1) is None

    def test_zero_risk_pips_returns_none(self):
        from signals import _calc_lot_size
        assert _calc_lot_size(0, 10000, 10000, 1) is None

    def test_higher_risk_pct_bigger_lot(self):
        from signals import _calc_lot_size
        lot_1 = _calc_lot_size(50, 10000, 10000, 1)
        lot_2 = _calc_lot_size(50, 10000, 10000, 2)
        assert lot_2 > lot_1


# =====================
# SHOULD SEND SIGNAL TESTS
# =====================
class TestShouldSendSignal:
    def test_first_signal_always_sends(self):
        assert should_send_signal({}, "user1_EURUSD", {"act": "BUY"}, 3600) is True

    def test_within_cooldown_same_direction(self):
        sent = {
            "user1_EURUSD": {
                "time": time.time() - 10,
                "direction": "BUY",
            }
        }
        assert should_send_signal(sent, "user1_EURUSD", {"act": "BUY"}, 3600) is False

    def test_cooldown_expired(self):
        sent = {
            "user1_EURUSD": {
                "time": time.time() - 7200,
                "direction": "BUY",
            }
        }
        assert should_send_signal(sent, "user1_EURUSD", {"act": "BUY"}, 3600) is True

    def test_direction_change_bypasses_cooldown(self):
        sent = {
            "user1_EURUSD": {
                "time": time.time() - 10,  # recent
                "direction": "BUY",
            }
        }
        # Direction changed to SELL - should send immediately
        assert should_send_signal(sent, "user1_EURUSD", {"act": "SELL"}, 3600) is True

    def test_invalid_last_info_type(self):
        sent = {"user1_EURUSD": "old_format"}
        assert should_send_signal(sent, "user1_EURUSD", {"act": "BUY"}, 3600) is True

    def test_different_pairs_independent(self):
        sent = {
            "user1_EURUSD": {
                "time": time.time() - 10,
                "direction": "BUY",
            }
        }
        assert should_send_signal(sent, "user1_GBPUSD", {"act": "BUY"}, 3600) is True


# =====================
# CLEANUP TESTS
# =====================
class TestCleanupOldSignals:
    def test_removes_expired(self):
        sent = {
            "user1_EURUSD": {"time": time.time() - 10000, "direction": "BUY"},
            "user2_GBPUSD": {"time": time.time() - 10, "direction": "SELL"},
        }
        cleaned = cleanup_old_signals(sent)
        assert cleaned == 1
        assert "user1_EURUSD" not in sent
        assert "user2_GBPUSD" in sent

    def test_no_expired(self):
        sent = {
            "user1_EURUSD": {"time": time.time() - 10, "direction": "BUY"},
        }
        cleaned = cleanup_old_signals(sent)
        assert cleaned == 0
        assert len(sent) == 1

    def test_empty_dict(self):
        sent = {}
        cleaned = cleanup_old_signals(sent)
        assert cleaned == 0

    def test_handles_non_dict_values(self):
        sent = {
            "old_key": "not_a_dict",
            "valid": {"time": time.time() - 10, "direction": "BUY"},
        }
        cleaned = cleanup_old_signals(sent)
        assert "old_key" in sent  # non-dict entries are preserved
        assert "valid" in sent


# =====================
# RATE LIMITER TESTS
# =====================
class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_rate_limiter_creation(self):
        from rate_limiter import RateLimiter
        rl = RateLimiter(rate=10)
        assert rl._rate == 10
        assert rl._max_tokens == 10

    @pytest.mark.asyncio
    async def test_acquire_consumes_token(self):
        from rate_limiter import RateLimiter
        rl = RateLimiter(rate=100)
        initial_tokens = rl._tokens
        await rl.acquire()
        # After acquire, tokens should be less (accounting for refill)
        assert rl._tokens < initial_tokens
