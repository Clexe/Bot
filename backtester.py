"""Backtesting engine for the MSNR Sniper Protocol.

Replays historical candle data through the strategy to compute
win rate, P&L, drawdown, and per-pair breakdowns without live trading.

Usage (from Telegram):
    /backtest XAUUSD 30   → backtest XAUUSD over last 30 days
    /backtest              → backtest all watchlist pairs, 30 days
"""

import pandas as pd
from strategy import get_smc_signal, get_pip_value, calculate_levels


def _walk_forward(df_l, df_h, pair, risk_pips=50, touch_trade=False,
                  window_l=50, step=1):
    """Walk-forward signal generator.

    Slides a window across the LTF data, calling get_smc_signal at each step.
    HTF data is always passed in full (slow-moving context).

    Yields (bar_index, signal_dict) for each signal produced.
    """
    if len(df_l) < window_l or len(df_h) < 20:
        return

    for i in range(window_l, len(df_l), step):
        chunk_l = df_l.iloc[:i].copy().reset_index(drop=True)
        sig = get_smc_signal(chunk_l, df_h, pair,
                             risk_pips=risk_pips, touch_trade=touch_trade)
        if sig:
            yield i, sig


def _evaluate_signal(sig, df_l, bar_index, pair, mode="LIMIT"):
    """Evaluate a signal against future candles to determine outcome.

    Walks candles after the signal bar to check if TP1/TP2/TP3 or SL is hit.

    Returns dict:
        outcome: WIN_TP1 | WIN_TP2 | WIN_TP3 | LOSS | OPEN
        pnl_pips: realized pip P&L
        rr: realized reward-to-risk ratio
        bars_held: number of candles to resolution
    """
    pip_val = get_pip_value(pair)

    if mode == "LIMIT":
        entry = sig["limit_e"]
        sl = sig["limit_sl"]
    else:
        entry = sig["market_e"]
        sl = sig["market_sl"]

    tp1 = sig.get("tp1", sig["tp"])
    tp2 = sig.get("tp2", sig["tp"])
    tp3 = sig.get("tp3", sig["tp"])
    direction = sig["act"]
    risk = abs(entry - sl)

    if risk <= 0:
        return {"outcome": "SKIP", "pnl_pips": 0, "rr": 0, "bars_held": 0}

    # Walk future bars — start at bar_index + 1 to avoid look-ahead bias.
    # The signal fires on bar_index, so the earliest the market can fill
    # is the NEXT bar. Starting at bar_index would let the signal "see"
    # the candle it was generated on, inflating backtest results.
    sl_current = sl  # will be moved to BE after TP1
    best_tp_hit = None

    for j in range(bar_index + 1, len(df_l)):
        candle = df_l.iloc[j]

        if direction == "BUY":
            # Check SL first (worst case)
            if candle["low"] <= sl_current:
                if best_tp_hit is None:
                    pnl = (sl_current - entry) * pip_val
                    return {"outcome": "LOSS", "pnl_pips": round(pnl, 1),
                            "rr": round(pnl / (risk * pip_val), 2) if risk > 0 else 0,
                            "bars_held": j - bar_index}
                else:
                    # SL hit after partial TP — classify based on actual P&L
                    pnl = (sl_current - entry) * pip_val
                    outcome = "BREAKEVEN" if abs(pnl) < 1 else best_tp_hit
                    return {"outcome": outcome, "pnl_pips": round(pnl, 1),
                            "rr": round(pnl / (risk * pip_val), 2) if risk > 0 else 0,
                            "bars_held": j - bar_index}

            # Check TPs (ascending order)
            if best_tp_hit is None and candle["high"] >= tp1:
                best_tp_hit = "WIN_TP1"
                sl_current = entry  # Move SL to breakeven
            if best_tp_hit == "WIN_TP1" and candle["high"] >= tp2:
                best_tp_hit = "WIN_TP2"
            if best_tp_hit in ("WIN_TP1", "WIN_TP2") and candle["high"] >= tp3:
                pnl = (tp3 - entry) * pip_val
                return {"outcome": "WIN_TP3", "pnl_pips": round(pnl, 1),
                        "rr": round(pnl / (risk * pip_val), 2),
                        "bars_held": j - bar_index}

        else:  # SELL
            if candle["high"] >= sl_current:
                if best_tp_hit is None:
                    pnl = (entry - sl_current) * pip_val
                    return {"outcome": "LOSS", "pnl_pips": round(pnl, 1),
                            "rr": round(pnl / (risk * pip_val), 2) if risk > 0 else 0,
                            "bars_held": j - bar_index}
                else:
                    pnl = (entry - sl_current) * pip_val
                    outcome = "BREAKEVEN" if abs(pnl) < 1 else best_tp_hit
                    return {"outcome": outcome, "pnl_pips": round(pnl, 1),
                            "rr": round(pnl / (risk * pip_val), 2) if risk > 0 else 0,
                            "bars_held": j - bar_index}

            if best_tp_hit is None and candle["low"] <= tp1:
                best_tp_hit = "WIN_TP1"
                sl_current = entry
            if best_tp_hit == "WIN_TP1" and candle["low"] <= tp2:
                best_tp_hit = "WIN_TP2"
            if best_tp_hit in ("WIN_TP1", "WIN_TP2") and candle["low"] <= tp3:
                pnl = (entry - tp3) * pip_val
                return {"outcome": "WIN_TP3", "pnl_pips": round(pnl, 1),
                        "rr": round(pnl / (risk * pip_val), 2),
                        "bars_held": j - bar_index}

    # Still open at end of data
    last_close = df_l.iloc[-1]["close"]
    if direction == "BUY":
        pnl = (last_close - entry) * pip_val
    else:
        pnl = (entry - last_close) * pip_val

    return {"outcome": best_tp_hit or "OPEN", "pnl_pips": round(pnl, 1),
            "rr": round(pnl / (risk * pip_val), 2) if risk > 0 else 0,
            "bars_held": len(df_l) - bar_index}


def run_backtest(df_l, df_h, pair, risk_pips=50, touch_trade=False,
                 mode="LIMIT", cooldown_bars=10):
    """Run a full backtest on historical data.

    Args:
        df_l: LTF OHLC DataFrame (full history)
        df_h: HTF OHLC DataFrame (full history)
        pair: Symbol name
        risk_pips: Max risk in pips
        touch_trade: Whether touch trade mode is on
        mode: LIMIT or MARKET
        cooldown_bars: Minimum bars between signals (prevent over-trading)

    Returns:
        dict with:
            trades: list of trade dicts
            summary: {total, wins, losses, open, win_rate, total_pips,
                      avg_pips, max_dd, profit_factor, avg_rr, avg_bars}
    """
    trades = []
    last_signal_bar = -cooldown_bars

    for bar_idx, sig in _walk_forward(df_l, df_h, pair,
                                       risk_pips=risk_pips,
                                       touch_trade=touch_trade):
        # Cooldown: skip if too close to last signal
        if bar_idx - last_signal_bar < cooldown_bars:
            continue

        result = _evaluate_signal(sig, df_l, bar_idx, pair, mode=mode)
        if result["outcome"] == "SKIP":
            continue

        trades.append({
            "bar": bar_idx,
            "direction": sig["act"],
            "entry": sig["limit_e"] if mode == "LIMIT" else sig["market_e"],
            "sl": sig["limit_sl"] if mode == "LIMIT" else sig["market_sl"],
            "tp1": sig.get("tp1", sig["tp"]),
            "tp2": sig.get("tp2", sig["tp"]),
            "tp3": sig.get("tp3", sig["tp"]),
            "confidence": sig.get("confidence", "medium"),
            "touch": sig.get("touch", False),
            "sweep": sig.get("sweep", False),
            **result,
        })
        last_signal_bar = bar_idx

    # Build summary
    summary = _build_summary(trades)
    return {"trades": trades, "summary": summary}


def _build_summary(trades):
    """Compute aggregate statistics from a list of trade results."""
    if not trades:
        return {
            "total": 0, "wins": 0, "losses": 0, "open": 0,
            "win_rate": 0, "total_pips": 0, "avg_pips": 0,
            "max_dd": 0, "profit_factor": 0, "avg_rr": 0, "avg_bars": 0,
        }

    wins = [t for t in trades if t["outcome"].startswith("WIN")]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    breakevens = [t for t in trades if t["outcome"] == "BREAKEVEN"]
    open_trades = [t for t in trades if t["outcome"] == "OPEN"]
    closed = wins + losses + breakevens

    total_pips = sum(t["pnl_pips"] for t in closed)
    gross_profit = sum(t["pnl_pips"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_pips"] for t in losses)) if losses else 0

    # Max drawdown (peak-to-trough in cumulative pips)
    # Include ALL trades (including OPEN) — unrealized losses matter for risk
    running = 0
    peak = 0
    max_dd = 0
    for t in trades:
        running += t["pnl_pips"]
        peak = max(peak, running)
        dd = peak - running
        max_dd = max(max_dd, dd)

    win_count = len(wins)
    loss_count = len(losses)
    closed_count = win_count + loss_count

    return {
        "total": len(trades),
        "wins": win_count,
        "losses": loss_count,
        "open": len(open_trades),
        "win_rate": round(win_count / closed_count * 100, 1) if closed_count else 0,
        "total_pips": round(total_pips, 1),
        "avg_pips": round(total_pips / closed_count, 1) if closed_count else 0,
        "max_dd": round(max_dd, 1),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0,
        "avg_rr": round(sum(t["rr"] for t in closed) / closed_count, 2) if closed_count else 0,
        "avg_bars": round(sum(t["bars_held"] for t in closed) / closed_count, 1) if closed_count else 0,
    }


def format_backtest_result(pair, summary, mode="LIMIT"):
    """Format backtest summary as a Telegram message."""
    if summary["total"] == 0:
        return f"*Backtest: {pair}*\nNo signals generated in the test period."

    pf = f"{summary['profit_factor']:.2f}" if summary['profit_factor'] != float('inf') else "INF"

    return (
        f"*Backtest: {pair}* ({mode})\n"
        f"{'=' * 28}\n"
        f"Trades: {summary['total']}\n"
        f"Wins: {summary['wins']} | Losses: {summary['losses']} | Open: {summary['open']}\n"
        f"Win Rate: *{summary['win_rate']}%*\n"
        f"Total P&L: *{summary['total_pips']:+.1f} pips*\n"
        f"Avg P&L: {summary['avg_pips']:+.1f} pips/trade\n"
        f"Max Drawdown: {summary['max_dd']:.1f} pips\n"
        f"Profit Factor: {pf}\n"
        f"Avg R:R: {summary['avg_rr']:.2f}\n"
        f"Avg Hold: {summary['avg_bars']:.0f} bars"
    )
