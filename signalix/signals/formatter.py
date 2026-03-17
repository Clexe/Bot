def format_signal(signal: dict, tier: str) -> str:
    """Format telegram payload based on user tier entitlements."""
    base = [f"⚡ SIGNALIX SIGNAL  [Score: {signal['score']}/10]", "", f"Pair: {signal['pair']}", f"Bias: {signal['direction']}", f"Session: {signal['kill_zone']}", "", f"Entry:  {signal['entry']}", f"SL:     {signal['sl']}", f"TP1:    {signal['tp1']}"]
    if tier in {'pro', 'elite'}:
        base += [f"TP2:    {signal.get('tp2')}", f"TP3:    {signal.get('tp3')}"]
    if tier == 'elite':
        base += ["", "📊 Confluence:", f"• HTF Storyline: {signal['htf_bias']}", f"• POI: {signal['poi_type']} at {signal['poi_price']}", f"• Liquidity Swept: {signal['liquidity_swept']}", f"• Structure: {signal['structure_shift_type']} on {signal['ltf_timeframe']}", f"• Kill Zone: {signal['kill_zone']}"]
    if tier in {'pro', 'elite'} and signal.get('rationale'):
        base += ["", "🤖 AI Rationale:", signal['rationale']]
    base += ["", "⚠️ Risk max 1% per trade. Not financial advice."]
    return "\n".join(base)
