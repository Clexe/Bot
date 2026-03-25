from signals.formatter import format_cancel_message, format_update_message
from utils.logger import get_logger

logger = get_logger(__name__)


async def track_open_signals(db, current_prices: dict, telegram):
    """Monitor all open signals from both engines via live prices.

    TP1 hit: breakeven + trail H4 swing. TP2 hit: trail to TP1.
    TP3/SL hit: close and log. Auto-cancel conditions.
    Sends update messages to all recipients.
    """
    open_signals = await db.fetch(
        "SELECT * FROM signals WHERE outcome IS NULL ORDER BY sent_at DESC"
    )

    for signal in open_signals:
        pair = signal["pair"]
        price = current_prices.get(pair)
        if price is None:
            continue

        direction = signal["direction"]
        entry = float(signal["entry"])
        sl = float(signal["sl"])
        tp1 = float(signal["tp1"]) if signal["tp1"] else None
        tp2 = float(signal["tp2"]) if signal.get("tp2") else None
        tp3 = float(signal["tp3"]) if signal.get("tp3") else None

        outcome = None
        event = None

        if direction == "LONG":
            if price <= sl:
                outcome = "SL"
                event = "SL"
            elif tp3 and price >= tp3:
                outcome = "TP3"
                event = "TP3"
            elif tp2 and price >= tp2:
                outcome = "TP2"
                event = "TP2"
            elif tp1 and price >= tp1:
                outcome = "TP1"
                event = "TP1"
        else:
            if price >= sl:
                outcome = "SL"
                event = "SL"
            elif tp3 and price <= tp3:
                outcome = "TP3"
                event = "TP3"
            elif tp2 and price <= tp2:
                outcome = "TP2"
                event = "TP2"
            elif tp1 and price <= tp1:
                outcome = "TP1"
                event = "TP1"

        if not outcome:
            await _check_auto_cancel(db, signal, price, telegram)
            continue

        # Update signal outcome
        try:
            rr_achieved = abs(price - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
            await db.execute(
                """UPDATE signals SET outcome=%s, outcome_recorded_at=NOW(),
                   final_rr_achieved=%s WHERE id=%s""",
                (outcome, round(rr_achieved, 2), signal["id"]),
            )
        except Exception as e:
            logger.error("Failed to update outcome for signal %s: %s", signal["id"], e)

        # Handle trailing stops on partial TPs
        if event == "TP1" and tp2:
            try:
                await db.execute(
                    "UPDATE signals SET sl=%s, outcome=NULL WHERE id=%s",
                    (entry, signal["id"]),
                )
            except Exception as e:
                logger.error("Failed to move SL to breakeven for signal %s: %s", signal["id"], e)

        elif event == "TP2" and tp3:
            try:
                await db.execute(
                    "UPDATE signals SET sl=%s, outcome=NULL WHERE id=%s",
                    (tp1, signal["id"]),
                )
            except Exception as e:
                logger.error("Failed to trail SL to TP1 for signal %s: %s", signal["id"], e)

        # Send update to all recipients
        msg = format_update_message(signal, event)
        await _send_to_recipients(db, signal, msg, telegram)


async def _check_auto_cancel(db, signal: dict, current_price: float, telegram):
    """Check auto-cancel conditions for both engines.

    1. Price closes full candle body beyond structural high/low before entry triggers
    2. POI OB or FVG fully mitigated
    3. Price moved 50%+ toward TP without returning to entry zone — mark stale
    """
    entry = float(signal["entry"])
    tp1 = float(signal["tp1"]) if signal["tp1"] else None
    direction = signal["direction"]

    if not tp1:
        return

    tp_distance = abs(tp1 - entry)
    current_distance = abs(current_price - entry)

    # Condition 3: Price moved 50%+ toward TP without triggering entry
    if tp_distance > 0 and current_distance > tp_distance * 0.5:
        if (direction == "LONG" and current_price > entry) or \
           (direction == "SHORT" and current_price < entry):
            reason = "Price moved 50%+ toward TP without entry trigger — stale"
            await _cancel_signal(db, signal, reason, telegram)
            return

    # Condition 1: Price closed beyond structure before entry
    if direction == "LONG" and current_price < float(signal["sl"]) * 0.99:
        reason = "Price closed below SL structure before entry"
        await _cancel_signal(db, signal, reason, telegram)
    elif direction == "SHORT" and current_price > float(signal["sl"]) * 1.01:
        reason = "Price closed above SL structure before entry"
        await _cancel_signal(db, signal, reason, telegram)


async def _cancel_signal(db, signal: dict, reason: str, telegram):
    """Cancel a signal and notify recipients."""
    try:
        await db.execute(
            "UPDATE signals SET outcome='CANCELLED', outcome_recorded_at=NOW() WHERE id=%s",
            (signal["id"],),
        )
    except Exception as e:
        logger.error("Failed to cancel signal %s: %s", signal["id"], e)

    msg = format_cancel_message(signal, reason)
    await _send_to_recipients(db, signal, msg, telegram)


async def _send_to_recipients(db, signal: dict, message: str, telegram):
    """Send update/cancel message to all users who received the signal."""
    try:
        users = await db.fetch("SELECT telegram_chat_id, tier FROM users WHERE is_active=true")
        for user in users:
            try:
                await telegram.send_message(user["telegram_chat_id"], message)
            except Exception as e:
                logger.error("Failed to send update to %s: %s", user["telegram_chat_id"], e)
    except Exception as e:
        logger.error("Failed to fetch recipients: %s", e)
