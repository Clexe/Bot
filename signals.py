import time
from config import SIGNAL_TTL, logger


def _calc_lot_size(risk_pips, pip_value, balance, risk_pct):
    """Calculate lot size from account balance and risk percentage.

    Formula: lot = (balance * risk_pct / 100) / (risk_pips * pip_per_lot)
    pip_per_lot varies by asset, approximated from pip_value.
    """
    if risk_pips <= 0 or balance <= 0 or risk_pct <= 0:
        return None
    # pip_per_lot: how many $ per pip per 1 standard lot
    # pip_value 10000 → forex (10$ per pip/lot), pip_value 10 → JPY/metals (10$),
    # pip_value 0.1 → BTC (~10$ per pip/lot at $100k BTC)
    if pip_value >= 10000:
        pip_per_lot = 10.0    # Standard forex
    elif pip_value >= 100:
        pip_per_lot = 10.0    # JPY pairs
    elif pip_value >= 1:
        pip_per_lot = 10.0    # Metals, indices
    else:
        pip_per_lot = 1.0     # Crypto
    risk_amount = balance * risk_pct / 100
    lot = risk_amount / (risk_pips * pip_per_lot)
    return round(lot, 2)


def format_signal_msg(sig, pair, mode, balance=0, risk_pct=1, pip_value=None):
    """Format a signal message with multi-TP levels and R:R ratio.

    Args:
        sig: Signal dict with act, limit_e, limit_sl, market_e, market_sl, tp, tp1, tp2, tp3
        pair: Symbol name
        mode: 'MARKET' or 'LIMIT'
        balance: User's account balance (0 = don't show lot size)
        risk_pct: Risk percentage per trade
        pip_value: Pip value divisor for lot size calculation

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

    tp1 = sig.get('tp1', sig['tp'])
    tp2 = sig.get('tp2', sig['tp'])
    tp3 = sig.get('tp3', sig['tp'])
    risk = abs(entry - sl)
    reward = abs(tp2 - entry)
    rr = f"{reward / risk:.1f}" if risk > 0 else "N/A"

    # Risk in pips
    risk_pips_val = risk * pip_value if pip_value else 0
    risk_line = f"\nRisk: `{risk_pips_val:.1f}` pips" if risk_pips_val > 0 else ""

    # Lot size
    lot_line = ""
    if balance > 0 and pip_value and risk_pips_val > 0:
        lot = _calc_lot_size(risk_pips_val, pip_value, balance, risk_pct)
        if lot and lot > 0:
            lot_line = f"\nLot: `{lot}` ({risk_pct}% of ${balance:,.0f})"

    # Confidence + trigger info
    confidence = sig.get('confidence', 'medium').upper()
    trigger_parts = []
    if sig.get('touch'):
        trigger_parts.append("TOUCH")
    if sig.get('sweep'):
        trigger_parts.append("SWEEP")
    trigger_tag = " | ".join(trigger_parts) if trigger_parts else ""
    trigger_line = f"\n{trigger_tag}" if trigger_tag else ""

    return (
        f"{emoji} *SMC SIGNAL ({label})* [{confidence}]\n"
        f"Symbol: `{pair}`\n"
        f"Action: *{sig['act']} {label}*\n"
        f"Entry: `{entry:.5f}`\n"
        f"TP1: `{tp1:.5f}` (1:1)\n"
        f"TP2: `{tp2:.5f}` (Zone)\n"
        f"TP3: `{tp3:.5f}` (Runner)\n"
        f"SL: `{sl:.5f}`\n"
        f"R:R = *1:{rr}*{risk_line}{lot_line}{trigger_line}"
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
