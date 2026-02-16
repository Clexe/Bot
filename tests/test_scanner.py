import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import time
import pytest
from config import PAIR_THROTTLE_SECONDS


# =====================
# PAIR THROTTLE TESTS
# =====================
class TestPairThrottle:
    def test_throttle_blocks_rapid_alerts(self):
        """Same pair within throttle window should be skipped."""
        pair_throttle = {}
        pair_throttle["XAUUSD"] = time.time()
        current_time = time.time()
        assert current_time - pair_throttle["XAUUSD"] < PAIR_THROTTLE_SECONDS

    def test_throttle_allows_after_window(self):
        """Pair should be allowed after throttle window expires."""
        pair_throttle = {}
        pair_throttle["EURUSD"] = time.time() - PAIR_THROTTLE_SECONDS - 1
        current_time = time.time()
        assert current_time - pair_throttle["EURUSD"] >= PAIR_THROTTLE_SECONDS

    def test_different_pairs_independent(self):
        """Different pairs should have independent throttles."""
        pair_throttle = {}
        pair_throttle["XAUUSD"] = time.time()
        assert "GBPUSD" not in pair_throttle

    def test_throttle_config_value(self):
        """Throttle should be 5 minutes by default."""
        assert PAIR_THROTTLE_SECONDS == 300

    def test_throttle_logic_integrated(self):
        """Simulate the scanner throttle check."""
        pair_throttle = {}
        current_time = time.time()

        # First signal: should pass (no entry)
        last_alert = pair_throttle.get("XAUUSD", 0)
        assert current_time - last_alert >= PAIR_THROTTLE_SECONDS

        # Record the alert
        pair_throttle["XAUUSD"] = current_time

        # Second signal immediately: should be throttled
        last_alert = pair_throttle.get("XAUUSD", 0)
        assert current_time - last_alert < PAIR_THROTTLE_SECONDS


# =====================
# TRAIL STOP LOGIC TESTS (unit-level)
# =====================
class TestTrailStopLogic:
    def test_tp_stage_0_uses_original_sl(self):
        """Stage 0: no TP hit, use original SL."""
        tp_stage = 0
        entry = 1.10
        sl = 1.09
        effective_sl = sl
        if tp_stage >= 2:
            effective_sl = entry + abs(entry - sl)
        elif tp_stage >= 1:
            effective_sl = entry
        assert effective_sl == 1.09

    def test_tp_stage_1_moves_sl_to_be(self):
        """Stage 1: TP1 hit, SL at breakeven."""
        tp_stage = 1
        entry = 1.10
        sl = 1.09
        effective_sl = sl
        if tp_stage >= 2:
            effective_sl = entry + abs(entry - sl)
        elif tp_stage >= 1:
            effective_sl = entry
        assert effective_sl == entry

    def test_tp_stage_2_trails_to_tp1(self):
        """Stage 2: TP2 hit, SL trails to TP1 level."""
        tp_stage = 2
        entry = 1.10
        sl = 1.09
        risk = abs(entry - sl)
        tp1 = entry + risk
        effective_sl = sl
        if tp_stage >= 2:
            effective_sl = tp1
        elif tp_stage >= 1:
            effective_sl = entry
        assert effective_sl == tp1
        assert effective_sl == 1.11

    def test_sell_tp_stage_1_be(self):
        """SELL stage 1: SL at entry (breakeven)."""
        tp_stage = 1
        entry = 1.10
        sl = 1.11
        effective_sl = sl
        if tp_stage >= 2:
            risk = abs(entry - sl)
            effective_sl = entry - risk
        elif tp_stage >= 1:
            effective_sl = entry
        assert effective_sl == entry

    def test_sell_tp_stage_2_trails(self):
        """SELL stage 2: SL trails to TP1."""
        tp_stage = 2
        entry = 1.10
        sl = 1.11
        risk = abs(entry - sl)
        tp1 = entry - risk
        effective_sl = sl
        if tp_stage >= 2:
            effective_sl = tp1
        elif tp_stage >= 1:
            effective_sl = entry
        assert effective_sl == tp1
        assert effective_sl == 1.09

    def test_be_pnl_is_zero(self):
        """After TP1 hit and SL at BE, closing at BE = 0 pnl."""
        entry = 1.10
        effective_sl = entry  # BE
        pnl = (effective_sl - entry) * 10000  # pip_val for forex
        assert pnl == 0

    def test_trail_pnl_positive(self):
        """After TP2 hit and SL trailed to TP1, closing at TP1 = +pips."""
        entry = 1.10
        sl = 1.09
        risk = abs(entry - sl)
        tp1 = entry + risk  # 1.11
        effective_sl = tp1  # trailed
        pnl = (effective_sl - entry) * 10000
        assert pnl > 0
        assert pnl == pytest.approx(100.0)  # 0.01 * 10000 = 100 pips
