async def update_signal_outcomes(db, signal: dict, current_price: float):
    """Update signal outcome when TP/SL gets hit based on direction and live price."""
    tp1 = float(signal['tp1']) if signal.get('tp1') is not None else None
    tp2 = float(signal['tp2']) if signal.get('tp2') is not None else None
    tp3 = float(signal['tp3']) if signal.get('tp3') is not None else None
    sl = float(signal['sl'])

    if signal['direction'] == 'LONG':
        if current_price <= sl:
            outcome = 'SL'
        elif tp3 is not None and current_price >= tp3:
            outcome = 'TP3'
        elif tp2 is not None and current_price >= tp2:
            outcome = 'TP2'
        elif tp1 is not None and current_price >= tp1:
            outcome = 'TP1'
        else:
            return
    else:
        if current_price >= sl:
            outcome = 'SL'
        elif tp3 is not None and current_price <= tp3:
            outcome = 'TP3'
        elif tp2 is not None and current_price <= tp2:
            outcome = 'TP2'
        elif tp1 is not None and current_price <= tp1:
            outcome = 'TP1'
        else:
            return
    await db.execute("UPDATE signals SET outcome=%s, outcome_recorded_at=NOW() WHERE id=%s", (outcome, signal['id']))
