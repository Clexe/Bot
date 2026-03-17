async def update_signal_outcomes(db, signal: dict, current_price: float):
    """Update signal outcome when TP/SL gets hit based on direction and live price."""
    if signal['direction'] == 'LONG':
        if current_price <= float(signal['sl']):
            outcome = 'SL'
        elif current_price >= float(signal['tp3']):
            outcome = 'TP3'
        elif current_price >= float(signal['tp2']):
            outcome = 'TP2'
        elif current_price >= float(signal['tp1']):
            outcome = 'TP1'
        else:
            return
    else:
        if current_price >= float(signal['sl']):
            outcome = 'SL'
        elif current_price <= float(signal['tp3']):
            outcome = 'TP3'
        elif current_price <= float(signal['tp2']):
            outcome = 'TP2'
        elif current_price <= float(signal['tp1']):
            outcome = 'TP1'
        else:
            return
    await db.execute("UPDATE signals SET outcome=%s, outcome_recorded_at=NOW() WHERE id=%s", (outcome, signal['id']))
