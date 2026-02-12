import time
from config import SIGNAL_TTL, logger


def format_signal_msg(sig, pair, mode):
    """Format a signal message with R:R ratio.

    Args:
        sig: Signal dict with act, limit_e, limit_sl, market_e, market_sl, tp
        pair: Symbol name
        mode: 'MARKET' or 'LIMIT'

    Returns:
        Formatted message string
    """
    if mode == "LIMIT":
        entry = sig['limit_e']
        sl = sig['limit_sl']
        label = "LIMIT"
        emoji = "\U0001f3af"
    else:
        entry = sig['market_e']
        sl = sig['market_sl']
        label = "MARKET"
        emoji = "\U0001f6a8"

    tp = sig['tp']
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = f"{reward / risk:.1f}" if risk > 0 else "N/A"

    return (
        f"{emoji} *SMC SIGNAL ({label})*\n"
        f"Symbol: `{pair}`\n"
        f"Action: *{sig['act']} {label}*\n"
        f"Entry: `{entry:.5f}`\n"
        f"TP: `{tp:.5f}` | SL: `{sl:.5f}`\n"
        f"R:R = *1:{rr}*"
    )


def should_send_signal(sent_signals, signal_key, sig, cooldown_sec):
    """Determine whether a signal should be sent.

    Checks cooldown and direction change.

    Args:
        sent_signals: Dict of previously sent signals
        signal_key: Key like 'uid_PAIR'
        sig: Current signal dict
        cooldown_sec: Cooldown period in seconds

    Returns:
        True if signal should be sent
    """
    last_info = sent_signals.get(signal_key)
    current_time = time.time()

    if last_info is None:
        return True
    if not isinstance(last_info, dict):
        return True

    time_elapsed = (current_time - last_info.get('time', 0)) > cooldown_sec
    direction_changed = last_info.get('direction') != sig['act']
    return time_elapsed or direction_changed


def cleanup_old_signals(sent_signals):
    """Remove expired entries from sent_signals dict.

    Args:
        sent_signals: Dict to clean up (modified in place)

    Returns:
        Number of entries cleaned
    """
    now = time.time()
    expired = [
        k for k, v in sent_signals.items()
        if isinstance(v, dict) and (now - v.get('time', 0)) > SIGNAL_TTL
    ]
    for k in expired:
        del sent_signals[k]
    if expired:
        logger.info("Cleaned up %d expired in-memory signal entries", len(expired))
    return len(expired)
