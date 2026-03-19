import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import pandas as pd
from backtester import (
    _walk_forward, _evaluate_signal, run_backtest,
    _build_summary, format_backtest_result,
)


def make_ohlc(data):
    df = pd.DataFrame(data, columns=['open', 'high', 'low', 'close'])
    return df.astype(float)


def make_trending_data(direction, bars=30, start=1.0, step=0.001):
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
# EVALUATE SIGNAL TESTS
# =====================
class TestEvaluateSignal:
    def test_buy_hits_tp1_then_sl_at_be(self):
        """BUY signal: price hits TP1, SL moved to BE, then SL hit = WIN at BE."""
        sig = {
            "act": "BUY", "limit_e": 1.10, "limit_sl": 1.09,
            "market_e": 1.10, "market_sl": 1.09,
            "tp": 1.12, "tp1": 1.11, "tp2": 1.12, "tp3": 1.13,
        }
        # bar 0 = entry, bar 1 = hits TP1 (1.11), bar 2 = drops to BE (1.10)
        data = [
            (1.10, 1.105, 1.095, 1.10),  # entry bar
            (1.10, 1.115, 1.10, 1.11),   # hits TP1
            (1.11, 1.11, 1.095, 1.095),  # drops to BE (1.10)
        ]
        df = make_ohlc(data)
        result = _evaluate_signal(sig, df, 0, "EURUSD", mode="LIMIT")
        assert result["outcome"] == "WIN_TP1"
        assert result["pnl_pips"] == 0  # breakeven

    def test_buy_hits_all_tps(self):
        """BUY signal hits TP3 = full win."""
        sig = {
            "act": "BUY", "limit_e": 1.10, "limit_sl": 1.09,
            "market_e": 1.10, "market_sl": 1.09,
            "tp": 1.12, "tp1": 1.11, "tp2": 1.12, "tp3": 1.13,
        }
        data = [
            (1.10, 1.115, 1.095, 1.11),   # hits TP1
            (1.11, 1.125, 1.105, 1.12),   # hits TP2
            (1.12, 1.135, 1.115, 1.13),   # hits TP3
        ]
        df = make_ohlc(data)
        result = _evaluate_signal(sig, df, 0, "EURUSD", mode="LIMIT")
        assert result["outcome"] == "WIN_TP3"
        assert result["pnl_pips"] > 0

    def test_buy_sl_hit_loss(self):
        """BUY signal: SL hit before any TP = LOSS."""
        sig = {
            "act": "BUY", "limit_e": 1.10, "limit_sl": 1.09,
            "market_e": 1.10, "market_sl": 1.09,
            "tp": 1.12, "tp1": 1.11, "tp2": 1.12, "tp3": 1.13,
        }
        data = [
            (1.10, 1.105, 1.085, 1.09),  # drops to SL
        ]
        df = make_ohlc(data)
        result = _evaluate_signal(sig, df, 0, "EURUSD", mode="LIMIT")
        assert result["outcome"] == "LOSS"
        assert result["pnl_pips"] < 0

    def test_sell_hits_tp1_trail(self):
        """SELL signal: TP1 hit, SL moved to BE."""
        sig = {
            "act": "SELL", "limit_e": 1.10, "limit_sl": 1.11,
            "market_e": 1.10, "market_sl": 1.11,
            "tp": 1.08, "tp1": 1.09, "tp2": 1.08, "tp3": 1.07,
        }
        data = [
            (1.10, 1.105, 1.085, 1.09),   # hits TP1
            (1.09, 1.105, 1.085, 1.10),   # bounces to BE
        ]
        df = make_ohlc(data)
        result = _evaluate_signal(sig, df, 0, "EURUSD", mode="LIMIT")
        assert result["outcome"] == "WIN_TP1"

    def test_open_at_end_of_data(self):
        """Signal still open when data runs out."""
        sig = {
            "act": "BUY", "limit_e": 1.10, "limit_sl": 1.09,
            "market_e": 1.10, "market_sl": 1.09,
            "tp": 1.12, "tp1": 1.11, "tp2": 1.12, "tp3": 1.13,
        }
        data = [
            (1.10, 1.105, 1.095, 1.102),
            (1.102, 1.106, 1.098, 1.104),
        ]
        df = make_ohlc(data)
        result = _evaluate_signal(sig, df, 0, "EURUSD", mode="LIMIT")
        assert result["outcome"] == "OPEN"

    def test_zero_risk_skipped(self):
        """Signal with zero risk is skipped."""
        sig = {
            "act": "BUY", "limit_e": 1.10, "limit_sl": 1.10,
            "market_e": 1.10, "market_sl": 1.10,
            "tp": 1.12, "tp1": 1.11, "tp2": 1.12, "tp3": 1.13,
        }
        df = make_ohlc([(1.10, 1.11, 1.09, 1.10)])
        result = _evaluate_signal(sig, df, 0, "EURUSD")
        assert result["outcome"] == "SKIP"


# =====================
# BUILD SUMMARY TESTS
# =====================
class TestBuildSummary:
    def test_empty_trades(self):
        summary = _build_summary([])
        assert summary["total"] == 0
        assert summary["win_rate"] == 0

    def test_all_wins(self):
        trades = [
            {"outcome": "WIN_TP2", "pnl_pips": 50, "rr": 2.0, "bars_held": 10},
            {"outcome": "WIN_TP1", "pnl_pips": 20, "rr": 1.0, "bars_held": 5},
        ]
        summary = _build_summary(trades)
        assert summary["wins"] == 2
        assert summary["losses"] == 0
        assert summary["win_rate"] == 100.0
        assert summary["total_pips"] == 70

    def test_mixed_results(self):
        trades = [
            {"outcome": "WIN_TP2", "pnl_pips": 50, "rr": 2.0, "bars_held": 10},
            {"outcome": "LOSS", "pnl_pips": -30, "rr": -1.0, "bars_held": 3},
        ]
        summary = _build_summary(trades)
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["win_rate"] == 50.0
        assert summary["total_pips"] == 20
        assert summary["profit_factor"] == round(50 / 30, 2)

    def test_max_drawdown(self):
        trades = [
            {"outcome": "WIN_TP1", "pnl_pips": 30, "rr": 1.0, "bars_held": 5},
            {"outcome": "LOSS", "pnl_pips": -50, "rr": -1.0, "bars_held": 3},
            {"outcome": "LOSS", "pnl_pips": -20, "rr": -1.0, "bars_held": 3},
        ]
        summary = _build_summary(trades)
        # Peak at 30, then drops to -40, drawdown = 70
        assert summary["max_dd"] == 70

    def test_open_trades_excluded_from_stats(self):
        trades = [
            {"outcome": "WIN_TP1", "pnl_pips": 30, "rr": 1.0, "bars_held": 5},
            {"outcome": "OPEN", "pnl_pips": 10, "rr": 0.5, "bars_held": 2},
        ]
        summary = _build_summary(trades)
        assert summary["total"] == 2
        assert summary["open"] == 1
        assert summary["total_pips"] == 30  # only closed trades


# =====================
# FORMAT BACKTEST RESULT TESTS
# =====================
class TestFormatBacktestResult:
    def test_no_trades(self):
        summary = _build_summary([])
        msg = format_backtest_result("XAUUSD", summary)
        assert "No signals" in msg

    def test_with_trades(self):
        summary = {
            "total": 10, "wins": 6, "losses": 4, "open": 0,
            "win_rate": 60.0, "total_pips": 120.5, "avg_pips": 12.1,
            "max_dd": 45.0, "profit_factor": 2.1, "avg_rr": 1.5, "avg_bars": 8.3,
        }
        msg = format_backtest_result("XAUUSD", summary, mode="LIMIT")
        assert "XAUUSD" in msg
        assert "60.0%" in msg
        assert "+120.5" in msg
        assert "Profit Factor" in msg
        assert "LIMIT" in msg

    def test_inf_profit_factor(self):
        summary = {
            "total": 2, "wins": 2, "losses": 0, "open": 0,
            "win_rate": 100.0, "total_pips": 50, "avg_pips": 25,
            "max_dd": 0, "profit_factor": float('inf'), "avg_rr": 2.0, "avg_bars": 5,
        }
        msg = format_backtest_result("EURUSD", summary)
        assert "INF" in msg


# =====================
# RUN BACKTEST INTEGRATION
# =====================
class TestRunBacktest:
    def test_insufficient_data_returns_empty(self):
        df_l = make_ohlc([(1.0, 1.01, 0.99, 1.0)] * 10)
        df_h = make_ohlc([(1.0, 1.01, 0.99, 1.0)] * 5)
        result = run_backtest(df_l, df_h, "EURUSD")
        assert result["trades"] == []
        assert result["summary"]["total"] == 0

    def test_cooldown_prevents_overtrading(self):
        """Cooldown should prevent signals on consecutive bars."""
        df_l = make_trending_data("up", bars=100, start=1.0, step=0.001)
        df_h = make_trending_data("up", bars=30, start=1.0, step=0.005)
        result = run_backtest(df_l, df_h, "EURUSD", cooldown_bars=20)
        # Even if signals fire, they should be spaced at least 20 bars
        for i in range(1, len(result["trades"])):
            diff = result["trades"][i]["bar"] - result["trades"][i - 1]["bar"]
            assert diff >= 20
