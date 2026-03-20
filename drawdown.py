"""Drawdown circuit breaker — account-level protection.

Tracks daily/weekly P&L and consecutive losses to pause trading.
Ported from _old/drawdown.py.
"""

import time
from datetime import datetime, timedelta
from utils.logger import get_logger

logger = get_logger(__name__)

# In-memory tracking
_daily_pnl = {}
_weekly_pnl = {}
_consecutive_losses = 0
_last_loss_time = 0
_open_trade_count = 0
_pause_until = 0

# Defaults
MAX_DAILY_LOSS_PIPS = -150
MAX_WEEKLY_LOSS_PIPS = -300
MAX_CONSECUTIVE_LOSSES = 4
LOSS_STREAK_PAUSE_HOURS = 4
MAX_OPEN_TRADES = 5


def record_trade_result(pnl_pips, is_win):
    """Record a trade outcome for circuit breaker tracking."""
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

    # Cleanup old entries
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
    """
    now = time.time()

    if now < _pause_until:
        remaining = int((_pause_until - now) / 60)
        return False, f"loss_streak_pause ({remaining}min remaining)", 0

    today = datetime.utcnow().strftime("%Y-%m-%d")
    daily = _daily_pnl.get(today, 0)
    if daily <= MAX_DAILY_LOSS_PIPS:
        return False, f"daily_loss_limit ({daily:.0f}/{MAX_DAILY_LOSS_PIPS} pips)", 0

    week = datetime.utcnow().strftime("%Y-W%W")
    weekly = _weekly_pnl.get(week, 0)
    size_mult = 1.0
    if weekly <= MAX_WEEKLY_LOSS_PIPS:
        size_mult = 0.5
        logger.info("Weekly drawdown active (%.0f pips) — reducing size to 50%%", weekly)

    return True, "", size_mult


def get_drawdown_status():
    """Get current drawdown status for display."""
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
    }


def reset_streak():
    """Manually reset the consecutive loss counter."""
    global _consecutive_losses, _pause_until
    _consecutive_losses = 0
    _pause_until = 0
    logger.info("Circuit breaker: loss streak reset manually")
