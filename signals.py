import time
from config import SIGNAL_TTL, CONFIDENCE_SIZE_MULTIPLIERS, logger


def _calc_lot_size(risk_pips, pip_value, balance, risk_pct, size_multiplier=1.0):
    """Calculate lot size from account balance and risk percentage.

    Formula: lot = (balance * risk_pct / 100) / (risk_pips * pip_per_lot) * multiplier
    pip_per_lot varies by asset, approximated from pip_value.

    size_multiplier adjusts for:
      - Confidence tier (high=1.5x, medium=1.0x, low=0.5x)
      - Drawdown state (weekly drawdown=0.5x)
    """
    if risk_pips <= 0 or balance <= 0 or risk_pct <= 0:
        return None
    # pip_per_lot: USD P&L per pip per lot, varies by asset class
    if pip_value >= 10000:
        pip_per_lot = 10.0    # Standard forex (EURUSD, GBPUSD)
    elif pip_value >= 100:
        pip_per_lot = 100.0   # Sub-dollar crypto (XRP, ADA, DOGE)
    elif pip_value >= 10:
        pip_per_lot = 10.0    # Mid-cap crypto/metals (SOL, LINK, XAU)
    elif pip_value >= 1:
        pip_per_lot = 1.0     # Large-cap crypto (ETH, BNB)
    else:
        pip_per_lot = 0.1     # BTC (pip_value=0.1)
    risk_amount = balance * risk_pct / 100
    lot = risk_amount / (risk_pips * pip_per_lot)
    lot *= size_multiplier
    return round(max(lot, 0.01), 2)


def format_signal_msg(sig, pair, mode, balance=0, risk_pct=1, pip_value=None,
                      size_multiplier=1.0):
    """Format a signal message with multi-TP levels, R:R, regime, and volume info.

    Args:
        sig: Signal dict from strategy
        pair: Symbol name
        mode: 'MARKET' or 'LIMIT'
        balance: User's account balance (0 = don't show lot size)
        risk_pct: Risk percentage per trade
        pip_value: Pip value divisor for lot size calculation
        size_multiplier: Drawdown-adjusted multiplier for lot size

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

    # Adaptive lot size: confidence tier * drawdown multiplier
    lot_line = ""
    confidence = sig.get('confidence', 'medium')
    conf_mult = CONFIDENCE_SIZE_MULTIPLIERS.get(confidence, 1.0)
    total_mult = conf_mult * size_multiplier

    if balance > 0 and pip_value and risk_pips_val > 0:
        lot = _calc_lot_size(risk_pips_val, pip_value, balance, risk_pct,
                             size_multiplier=total_mult)
        if lot and lot > 0:
            mult_note = ""
            if total_mult != 1.0:
                mult_note = f" x{total_mult:.1f}"
            lot_line = f"\nLot: `{lot}` ({risk_pct}% of ${balance:,.0f}{mult_note})"

    # Confidence + trigger info
    trigger_parts = []
    # MSS/BOS badge
    break_type = sig.get('break_type')
    if break_type == "MSS":
        trigger_parts.append("MSS")
    elif break_type == "BOS":
        trigger_parts.append("BOS")
    if sig.get('touch'):
        trigger_parts.append("TOUCH")
    if sig.get('sweep'):
        trigger_parts.append("SWEEP")
    if sig.get('displacement_fvg'):
        trigger_parts.append("FVG")
    trigger_tag = " | ".join(trigger_parts) if trigger_parts else ""
    trigger_line = f"\n{trigger_tag}" if trigger_tag else ""

    # Regime + volume proxy + premium/discount context line
    regime = sig.get('regime', '')
    vol_proxy = sig.get('volume_proxy', 0)
    vol_label = "HIGH" if vol_proxy >= 0.65 else "MED" if vol_proxy >= 0.4 else "LOW"
    pd_zone = sig.get('pd_zone', '')
    pd_tag = f" | {pd_zone.upper()}" if pd_zone and pd_zone != "equilibrium" else ""
    context_line = ""
    if regime:
        regime_short = regime.replace("TRENDING_", "T-").replace("RANGING", "RANGE")
        context_line = f"\nRegime: {regime_short} | Vol: {vol_label}{pd_tag}"

    return (
        f"{emoji} *SMC SIGNAL ({label})* [{confidence.upper()}]\n"
        f"Symbol: `{pair}`\n"
        f"Action: *{sig['act']} {label}*\n"
        f"Entry: `{entry:.5f}`\n"
        f"TP1: `{tp1:.5f}` (1:1)\n"
        f"TP2: `{tp2:.5f}` (Zone)\n"
        f"TP3: `{tp3:.5f}` (Runner)\n"
        f"SL: `{sl:.5f}`\n"
        f"R:R = *1:{rr}*{risk_line}{lot_line}{trigger_line}{context_line}"
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

    time_since_last = current_time - last_info.get('time', 0)
    time_elapsed = time_since_last > cooldown_sec
    direction_changed = last_info.get('direction') != sig['act']
    # Direction flip allowed after half-cooldown (prevents whipsaw in chop)
    if direction_changed:
        return time_since_last > (cooldown_sec * 0.5)
    return time_elapsed


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
