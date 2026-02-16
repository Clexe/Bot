"""Drawdown circuit breaker — account-level protection.

Tracks daily/weekly P&L and consecutive losses to pause trading
when the account is in danger. Every professional fund has these.

Circuit breaker levels:
  - Max daily loss (default -3%): pause for rest of day
  - Max weekly loss (default -5%): reduce size by 50%
  - Consecutive loss streak (default 4): pause for configurable hours
  - Max open trades (default 5): prevent overexposure
"""

import time
from datetime import datetime, timedelta
from config import logger

# In-memory tracking (per-user would need DB, but global is fine for signal bot)
_daily_pnl = {}       # {date_str: total_pnl_pips}
_weekly_pnl = {}       # {week_str: total_pnl_pips}
_consecutive_losses = 0
_last_loss_time = 0
_open_trade_count = 0
_pause_until = 0       # timestamp — pause trading until this time

# Defaults (overridden by config)
MAX_DAILY_LOSS_PIPS = -150      # pips
MAX_WEEKLY_LOSS_PIPS = -300     # pips
MAX_CONSECUTIVE_LOSSES = 4
LOSS_STREAK_PAUSE_HOURS = 4
MAX_OPEN_TRADES = 5


def configure(daily_loss=None, weekly_loss=None, max_streak=None,
              pause_hours=None, max_open=None):
    """Update circuit breaker thresholds."""
    global MAX_DAILY_LOSS_PIPS, MAX_WEEKLY_LOSS_PIPS, MAX_CONSECUTIVE_LOSSES
    global LOSS_STREAK_PAUSE_HOURS, MAX_OPEN_TRADES

    if daily_loss is not None:
        MAX_DAILY_LOSS_PIPS = daily_loss
    if weekly_loss is not None:
        MAX_WEEKLY_LOSS_PIPS = weekly_loss
    if max_streak is not None:
        MAX_CONSECUTIVE_LOSSES = max_streak
    if pause_hours is not None:
        LOSS_STREAK_PAUSE_HOURS = pause_hours
    if max_open is not None:
        MAX_OPEN_TRADES = max_open


def record_trade_result(pnl_pips, is_win):
    """Record a trade outcome for circuit breaker tracking.

    Called by scanner when a signal outcome is determined.
    """
    global _consecutive_losses, _last_loss_time, _pause_until

    today = datetime.utcnow().strftime("%Y-%m-%d")
    week = datetime.utcnow().strftime("%Y-W%W")

    _daily_pnl[today] = _daily_pnl.get(today, 0) + pnl_pips
    _weekly_pnl[week] = _weekly_pnl.get(week, 0) + pnl_pips

    if is_win:
        _consecutive_losses = 0
    else:
        _consecutive_losses += 1
        _last_loss_time = time.time()

        if _consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            _pause_until = time.time() + (LOSS_STREAK_PAUSE_HOURS * 3600)
            logger.warning(
                "Circuit breaker: %d consecutive losses — pausing for %dh",
                _consecutive_losses, LOSS_STREAK_PAUSE_HOURS,
            )

    # Cleanup old entries (keep last 7 days)
    cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    for d in list(_daily_pnl.keys()):
        if d < cutoff:
            del _daily_pnl[d]


def set_open_trade_count(count):
    """Update the current number of open trades."""
    global _open_trade_count
    _open_trade_count = count


def check_circuit_breaker():
    """Check if trading should be paused.

    Returns (allowed: bool, reason: str, size_multiplier: float).
    size_multiplier is 1.0 normally, 0.5 when in weekly drawdown.
    """
    now = time.time()

    # Check loss streak pause
    if now < _pause_until:
        remaining = int((_pause_until - now) / 60)
        return False, f"loss_streak_pause ({remaining}min remaining)", 0

    # Check max open trades
    if _open_trade_count >= MAX_OPEN_TRADES:
        return False, f"max_open_trades ({_open_trade_count}/{MAX_OPEN_TRADES})", 0

    # Check daily loss
    today = datetime.utcnow().strftime("%Y-%m-%d")
    daily = _daily_pnl.get(today, 0)
    if daily <= MAX_DAILY_LOSS_PIPS:
        return False, f"daily_loss_limit ({daily:.0f}/{MAX_DAILY_LOSS_PIPS} pips)", 0

    # Check weekly loss — don't stop, but reduce size
    week = datetime.utcnow().strftime("%Y-W%W")
    weekly = _weekly_pnl.get(week, 0)
    size_mult = 1.0
    if weekly <= MAX_WEEKLY_LOSS_PIPS:
        size_mult = 0.5  # Half size when in weekly drawdown
        logger.info("Weekly drawdown active (%.0f pips) — reducing size to 50%%", weekly)

    return True, "", size_mult


def get_drawdown_status():
    """Get current drawdown status for display.

    Returns dict with current metrics.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    week = datetime.utcnow().strftime("%Y-W%W")

    paused = time.time() < _pause_until
    pause_remaining = max(0, int((_pause_until - time.time()) / 60)) if paused else 0

    return {
        "daily_pnl": round(_daily_pnl.get(today, 0), 1),
        "weekly_pnl": round(_weekly_pnl.get(week, 0), 1),
        "consecutive_losses": _consecutive_losses,
        "open_trades": _open_trade_count,
        "paused": paused,
        "pause_remaining_min": pause_remaining,
        "daily_limit": MAX_DAILY_LOSS_PIPS,
        "weekly_limit": MAX_WEEKLY_LOSS_PIPS,
        "max_streak": MAX_CONSECUTIVE_LOSSES,
        "max_open": MAX_OPEN_TRADES,
    }


def reset_streak():
    """Manually reset the consecutive loss counter (e.g., after pause)."""
    global _consecutive_losses, _pause_until
    _consecutive_losses = 0
    _pause_until = 0
    logger.info("Circuit breaker: loss streak reset manually")
