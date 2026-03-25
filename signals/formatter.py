def format_precision_signal(signal: dict, tier: str) -> str:
    """Format Precision signal per tier."""
    if tier == "free":
        return _format_precision_basic(signal)
    if tier == "basic":
        return _format_precision_basic(signal)
    if tier == "pro":
        return _format_precision_full(signal, show_score=True)
    if tier == "elite":
        return _format_precision_full(signal, show_score=True, show_confluence=True)
    return _format_precision_basic(signal)


def format_flow_signal(signal: dict, tier: str) -> str:
    """Format Flow signal per tier. Never sent to free tier."""
    if tier == "basic":
        return _format_flow_basic(signal)
    if tier in ("pro", "elite"):
        return _format_flow_full(signal, show_score=True)
    return _format_flow_basic(signal)


def _format_precision_full(signal: dict, show_score: bool = False, show_confluence: bool = False) -> str:
    lines = []
    if show_score:
        lines.append(f"⚡ PRECISION SIGNAL  [Score: {signal['score']}/15]")
    else:
        lines.append("⚡ PRECISION SIGNAL")

    lines.append("")
    lines.append(f"Pair: {signal['pair']}")
    lines.append(f"Bias: {signal['direction']}")
    lines.append(f"Session: {signal.get('kill_zone', 'N/A')}")
    lines.append("")
    lines.append(f"Entry:  {signal['entry']}")
    sl_pips = signal.get("sl_pips", "")
    sl_str = f"SL:     {signal['sl']}"
    if sl_pips:
        sl_str += f"  ({sl_pips} pips)"
    lines.append(sl_str)
    lines.append(f"TP1:    {signal['tp1']}  (1:{signal.get('rr_tp1', 'N/A')})")
    if signal.get("tp2"):
        lines.append(f"TP2:    {signal['tp2']}  (1:{signal.get('rr_tp2', 'N/A')})")
    if signal.get("tp3"):
        lines.append(f"TP3:    {signal['tp3']}  (1:{signal.get('rr_tp3', 'N/A')})")

    if show_confluence:
        lines.append("")
        lines.append("📊 Confluence:")
        if signal.get("cot_bias"):
            lines.append(f"• COT Bias: {signal['cot_bias']} ({signal.get('cot_percentile', 'N/A')}th percentile)")
        if signal.get("wyckoff_phase"):
            lines.append(f"• Wyckoff Phase: {signal['wyckoff_phase']}")
        lines.append(f"• HTF Storyline: {signal.get('htf_bias', 'N/A')}")
        lines.append(f"• POI: {signal.get('poi_type', 'N/A')} at {signal.get('poi_price', 'N/A')}")
        if signal.get("judas_swing"):
            lines.append("• Judas Swing: yes — Asian level swept")
        if signal.get("structure_shift_type"):
            lines.append(f"• Structure: {signal['structure_shift_type']} on M15")
        lines.append(f"• Kill Zone: {signal.get('kill_zone', 'N/A')}")
        if signal.get("volume_profile_confluence"):
            lines.append("• Volume Profile: POC/HVN confluence confirmed")

    if signal.get("rationale"):
        lines.append("")
        lines.append("🤖 AI Rationale:")
        lines.append(signal["rationale"])

    lines.append("")
    lines.append("⚠️ Risk max 1% per trade. Not financial advice.")
    return "\n".join(lines)


def _format_precision_basic(signal: dict) -> str:
    lines = [
        "⚡ PRECISION SIGNAL",
        "",
        f"Pair: {signal['pair']}",
        f"Bias: {signal['direction']}",
        "",
        f"Entry:  {signal['entry']}",
        f"SL:     {signal['sl']}",
        f"TP1:    {signal['tp1']}  (1:{signal.get('rr_tp1', 'N/A')})",
        "",
        "⚠️ Risk max 1% per trade. Not financial advice.",
    ]
    return "\n".join(lines)


def _format_flow_full(signal: dict, show_score: bool = False) -> str:
    lines = []
    if show_score:
        lines.append(f"🔄 FLOW SIGNAL  [Score: {signal['score']}/8]")
    else:
        lines.append("🔄 FLOW SIGNAL")

    lines.append("")
    lines.append(f"Pair: {signal['pair']}")
    lines.append(f"Bias: {signal['direction']}")
    lines.append(f"Session: {signal.get('kill_zone', 'N/A')}")
    lines.append("")
    lines.append(f"Entry:  {signal['entry']}")
    sl_pips = signal.get("sl_pips", "")
    sl_str = f"SL:     {signal['sl']}"
    if sl_pips:
        sl_str += f"  ({sl_pips} pips)"
    lines.append(sl_str)
    lines.append(f"TP1:    {signal['tp1']}  (1:{signal.get('rr_tp1', 'N/A')})")
    if signal.get("tp2"):
        lines.append(f"TP2:    {signal['tp2']}  (1:{signal.get('rr_tp2', 'N/A')})")

    lines.append("")
    lines.append("📊 Setup:")
    lines.append(f"• Daily Bias: {signal.get('daily_bias', signal.get('htf_bias', 'N/A'))}")
    lines.append(f"• POI: {signal.get('poi_type', 'N/A')} at {signal.get('poi_price', 'N/A')} ({signal.get('poi_touch_count', 0)} touches)")
    if signal.get("choch_type") or signal.get("structure_shift_type"):
        lines.append(f"• Structure: {signal.get('choch_type', signal.get('structure_shift_type', 'CHoCH'))} on M15")
    lines.append(f"• Kill Zone: {signal.get('kill_zone', 'N/A')}")

    if signal.get("rationale"):
        lines.append("")
        lines.append("🤖 AI Rationale:")
        lines.append(signal["rationale"])

    lines.append("")
    lines.append("⚠️ Risk max 1% per trade. Not financial advice.")
    return "\n".join(lines)


def _format_flow_basic(signal: dict) -> str:
    lines = [
        "🔄 FLOW SIGNAL",
        "",
        f"Pair: {signal['pair']}",
        f"Bias: {signal['direction']}",
        "",
        f"Entry:  {signal['entry']}",
        f"SL:     {signal['sl']}",
        f"TP1:    {signal['tp1']}  (1:{signal.get('rr_tp1', 'N/A')})",
        "",
        "⚠️ Risk max 1% per trade. Not financial advice.",
    ]
    return "\n".join(lines)


def format_cancel_message(signal: dict, reason: str) -> str:
    """Format cancellation notification for both engines."""
    engine_label = "PRECISION" if signal.get("signal_type") == "precision" else "FLOW"
    return (
        f"⚠️ SIGNALIX CANCEL — {engine_label} Signal #{signal.get('id', '?')} "
        f"on {signal['pair']} cancelled.\n"
        f"Reason: {reason}. No trade."
    )


def format_update_message(signal: dict, event: str, new_sl: float = None) -> str:
    """Format TP hit / SL move update notification."""
    engine_label = "⚡ PRECISION" if signal.get("signal_type") == "precision" else "🔄 FLOW"
    lines = [f"{engine_label} UPDATE — Signal #{signal.get('id', '?')} {signal['pair']}"]
    if event == "TP1":
        lines.append(f"✅ TP1 hit at {signal['tp1']}! SL moved to breakeven at {signal['entry']}")
    elif event == "TP2":
        lines.append(f"✅ TP2 hit at {signal['tp2']}! SL moved to TP1 at {signal['tp1']}")
    elif event == "TP3":
        lines.append(f"✅ TP3 hit at {signal['tp3']}! Trade closed. Full target achieved.")
    elif event == "SL":
        lines.append(f"❌ SL hit. Trade closed.")
    return "\n".join(lines)
