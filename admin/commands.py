from config import ADMIN_CHAT_IDS
from strategy.cot_filter import refresh_cot
from utils.logger import get_logger

logger = get_logger(__name__)


def is_admin(chat_id: int) -> bool:
    """Check admin authorization by configured chat IDs."""
    return chat_id in ADMIN_CHAT_IDS


async def log_admin(db, admin_chat_id: int, command: str, parameters: str, result: str):
    """Persist admin command execution audit log."""
    try:
        await db.execute(
            "INSERT INTO admin_logs (admin_chat_id, command, parameters, result) VALUES (%s,%s,%s,%s)",
            (admin_chat_id, command, parameters, result),
        )
    except Exception as e:
        logger.error("Failed to log admin command: %s", e)


async def handle_admin_command(db, chat_id: int, command: str, args: str = "") -> str:
    """Route admin commands and return response text."""
    if not is_admin(chat_id):
        return "⛔ Unauthorized"

    cmd = command.lower().strip("/")

    handlers = {
        "botstatus": cmd_botstatus,
        "stats": cmd_stats,
        "flowstatus": cmd_flowstatus,
        "pauseflow": cmd_pauseflow,
        "resumeflow": cmd_resumeflow,
        "pauseprecision": cmd_pauseprecision,
        "resumeprecision": cmd_resumeprecision,
        "pausebot": cmd_pausebot,
        "resumebot": cmd_resumebot,
        "pausepair": cmd_pausepair,
        "resumepair": cmd_resumepair,
        "setflowminimum": cmd_setflowminimum,
        "setprecisionminimum": cmd_setprecisionminimum,
        "setflowrr": cmd_setflowrr,
        "setprecisionrr": cmd_setprecisionrr,
        "enginestats": cmd_enginestats,
        "cotstatus": cmd_cotstatus,
        "refreshcot": cmd_refreshcot,
        "rejectedsetups": cmd_rejectedsetups,
        "manualsignal": cmd_manualsignal,
    }

    handler = handlers.get(cmd)
    if not handler:
        return f"❌ Unknown command: /{cmd}"

    try:
        result = await handler(db, args)
        await log_admin(db, chat_id, cmd, args, result[:200])
        return result
    except Exception as e:
        logger.error("Admin command %s failed: %s", cmd, e)
        return f"❌ Error: {e}"


async def cmd_botstatus(db, args: str) -> str:
    bot_paused = await db.fetchrow("SELECT value FROM bot_settings WHERE key='bot_paused'")
    precision_enabled = await db.fetchrow("SELECT value FROM bot_settings WHERE key='precision_signals_enabled'")
    flow_enabled = await db.fetchrow("SELECT value FROM bot_settings WHERE key='flow_signals_enabled'")

    precision_last = await db.fetchrow(
        "SELECT sent_at FROM signals WHERE signal_type='precision' ORDER BY sent_at DESC LIMIT 1"
    )
    flow_last = await db.fetchrow(
        "SELECT sent_at FROM signals WHERE signal_type='flow' ORDER BY sent_at DESC LIMIT 1"
    )

    queue_total = await db.fetchrow("SELECT COUNT(*) AS c FROM delivery_queue WHERE delivered=false")
    errors_today = await db.fetchrow(
        "SELECT COUNT(*) AS c FROM errors WHERE logged_at > NOW() - INTERVAL '24 hours'"
    )

    cot_cache = await db.fetchrow("SELECT valid_until FROM cot_cache ORDER BY cached_at DESC LIMIT 1")

    p_status = "✅ Active" if precision_enabled and precision_enabled["value"] == "true" else "⏸ Paused"
    f_status = "✅ Active" if flow_enabled and flow_enabled["value"] == "true" else "⏸ Paused"
    bot_status = "✅ ONLINE" if bot_paused and bot_paused["value"] == "false" else "⏸ PAUSED"

    p_last = str(precision_last["sent_at"])[:16] if precision_last else "Never"
    f_last = str(flow_last["sent_at"])[:16] if flow_last else "Never"
    cot_valid = str(cot_cache["valid_until"])[:10] if cot_cache else "N/A"

    return (
        f"Status: {bot_status}\n\n"
        f"ENGINES\n"
        f"Precision:    {p_status} (last: {p_last})\n"
        f"Flow:         {f_status} (last: {f_last})\n\n"
        f"DATABASE\n"
        f"PostgreSQL:   ✅ Connected\n\n"
        f"EXTERNAL APIS\n"
        f"COT Cache:    Valid until {cot_valid}\n\n"
        f"SCHEDULER\n"
        f"Queue pending: {queue_total['c'] if queue_total else 0}\n"
        f"Errors (24h):  {errors_today['c'] if errors_today else 0}"
    )


async def cmd_stats(db, args: str) -> str:
    # Precision
    p_total = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE signal_type='precision'")
    p_win = await db.fetchrow(
        "SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END),0) AS w FROM signals WHERE signal_type='precision' AND outcome IS NOT NULL"
    )
    p_win2 = await db.fetchrow(
        "SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP2','TP3') THEN 100 ELSE 0 END),0) AS w FROM signals WHERE signal_type='precision' AND outcome IS NOT NULL"
    )
    p_rr = await db.fetchrow(
        "SELECT COALESCE(AVG(final_rr_achieved),0) AS a FROM signals WHERE signal_type='precision' AND outcome IS NOT NULL"
    )

    # Flow
    f_total = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE signal_type='flow'")
    f_win = await db.fetchrow(
        "SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END),0) AS w FROM signals WHERE signal_type='flow' AND outcome IS NOT NULL"
    )
    f_rr = await db.fetchrow(
        "SELECT COALESCE(AVG(final_rr_achieved),0) AS a FROM signals WHERE signal_type='flow' AND outcome IS NOT NULL"
    )

    # Combined
    total_30d = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE sent_at > NOW() - INTERVAL '30 days'")
    overall = await db.fetchrow(
        "SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END),0) AS w FROM signals WHERE outcome IS NOT NULL"
    )
    best = await db.fetchrow(
        "SELECT pair FROM signals WHERE outcome IN ('TP1','TP2','TP3') GROUP BY pair ORDER BY COUNT(*) DESC LIMIT 1"
    )
    week_s = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE sent_at > NOW() - INTERVAL '7 days'")
    week_w = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE sent_at > NOW() - INTERVAL '7 days' AND outcome IN ('TP1','TP2','TP3')")
    month_s = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE sent_at > NOW() - INTERVAL '30 days'")
    month_w = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE sent_at > NOW() - INTERVAL '30 days' AND outcome IN ('TP1','TP2','TP3')")

    return (
        "📊 SIGNALIX PERFORMANCE\n\n"
        "━━━ PRECISION ENGINE ━━━\n"
        f"Total signals: {p_total['c']}\n"
        f"Win rate (TP1): {round(float(p_win['w']),1)}%\n"
        f"Win rate (TP2): {round(float(p_win2['w']),1)}%\n"
        f"Avg R:R achieved: {round(float(p_rr['a']),2)}\n\n"
        "━━━ FLOW ENGINE ━━━\n"
        f"Total signals: {f_total['c']}\n"
        f"Win rate (TP1): {round(float(f_win['w']),1)}%\n"
        f"Avg R:R achieved: {round(float(f_rr['a']),2)}\n\n"
        "━━━ COMBINED ━━━\n"
        f"Total signals (30d): {total_30d['c']}\n"
        f"Overall win rate: {round(float(overall['w']),1)}%\n"
        f"Best performing pair: {best['pair'] if best else 'N/A'}\n"
        f"This week: {week_s['c']} signals, {week_w['c']} wins\n"
        f"This month: {month_s['c']} signals, {month_w['c']} wins"
    )


async def cmd_flowstatus(db, args: str) -> str:
    flow_enabled = await db.fetchrow("SELECT value FROM bot_settings WHERE key='flow_signals_enabled'")
    today = await db.fetchrow(
        "SELECT COUNT(*) AS c FROM signals WHERE signal_type='flow' AND sent_at > CURRENT_DATE"
    )
    win_7d = await db.fetchrow(
        "SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END),0) AS w FROM signals WHERE signal_type='flow' AND sent_at > NOW() - INTERVAL '7 days' AND outcome IS NOT NULL"
    )
    pending = await db.fetchrow(
        "SELECT COUNT(*) AS c FROM signals WHERE signal_type='flow' AND outcome IS NULL"
    )
    status = "✅ Active" if flow_enabled and flow_enabled["value"] == "true" else "⏸ Paused"

    return (
        f"Flow signals today: {today['c']}\n"
        f"Flow win rate (7 days): {round(float(win_7d['w']),1)}%\n"
        f"Flow signals pending: {pending['c']}\n"
        f"Flow engine: {status}"
    )


async def cmd_pauseflow(db, args: str) -> str:
    await db.execute("UPDATE bot_settings SET value='false', updated_at=NOW() WHERE key='flow_signals_enabled'")
    return "⏸ Flow engine paused. Precision continues."


async def cmd_resumeflow(db, args: str) -> str:
    await db.execute("UPDATE bot_settings SET value='true', updated_at=NOW() WHERE key='flow_signals_enabled'")
    return "✅ Flow engine resumed."


async def cmd_pauseprecision(db, args: str) -> str:
    await db.execute("UPDATE bot_settings SET value='false', updated_at=NOW() WHERE key='precision_signals_enabled'")
    return "⏸ Precision engine paused. Flow continues."


async def cmd_resumeprecision(db, args: str) -> str:
    await db.execute("UPDATE bot_settings SET value='true', updated_at=NOW() WHERE key='precision_signals_enabled'")
    return "✅ Precision engine resumed."


async def cmd_pausebot(db, args: str) -> str:
    await db.execute("UPDATE bot_settings SET value='true', updated_at=NOW() WHERE key='bot_paused'")
    return "⏸ Bot paused. No signals will be sent."


async def cmd_resumebot(db, args: str) -> str:
    await db.execute("UPDATE bot_settings SET value='false', updated_at=NOW() WHERE key='bot_paused'")
    return "✅ Bot resumed."


async def cmd_pausepair(db, args: str) -> str:
    pair = args.strip().upper()
    if not pair:
        return "Usage: /pausepair XAUUSD"
    row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='paused_pairs'")
    current = row["value"] if row else ""
    pairs = [p.strip() for p in current.split(",") if p.strip()]
    if pair not in pairs:
        pairs.append(pair)
    await db.execute("UPDATE bot_settings SET value=%s, updated_at=NOW() WHERE key='paused_pairs'", (",".join(pairs),))
    return f"⏸ {pair} paused."


async def cmd_resumepair(db, args: str) -> str:
    pair = args.strip().upper()
    if not pair:
        return "Usage: /resumepair XAUUSD"
    row = await db.fetchrow("SELECT value FROM bot_settings WHERE key='paused_pairs'")
    current = row["value"] if row else ""
    pairs = [p.strip() for p in current.split(",") if p.strip() and p.strip() != pair]
    await db.execute("UPDATE bot_settings SET value=%s, updated_at=NOW() WHERE key='paused_pairs'", (",".join(pairs),))
    return f"✅ {pair} resumed."


async def cmd_setflowminimum(db, args: str) -> str:
    try:
        score = int(args.strip())
        await db.execute("UPDATE bot_settings SET value=%s, updated_at=NOW() WHERE key='min_flow_score'", (str(score),))
        return f"✅ Flow minimum score set to {score}/8."
    except ValueError:
        return "Usage: /setflowminimum 6"


async def cmd_setprecisionminimum(db, args: str) -> str:
    try:
        score = int(args.strip())
        await db.execute("UPDATE bot_settings SET value=%s, updated_at=NOW() WHERE key='min_precision_score'", (str(score),))
        return f"✅ Precision minimum score set to {score}/15."
    except ValueError:
        return "Usage: /setprecisionminimum 10"


async def cmd_setflowrr(db, args: str) -> str:
    try:
        rr = float(args.strip())
        await db.execute("UPDATE bot_settings SET value=%s, updated_at=NOW() WHERE key='min_flow_rr'", (str(rr),))
        return f"✅ Flow minimum R:R set to 1:{rr}."
    except ValueError:
        return "Usage: /setflowrr 2.0"


async def cmd_setprecisionrr(db, args: str) -> str:
    try:
        rr = float(args.strip())
        await db.execute("UPDATE bot_settings SET value=%s, updated_at=NOW() WHERE key='min_precision_rr'", (str(rr),))
        return f"✅ Precision minimum R:R set to 1:{rr}."
    except ValueError:
        return "Usage: /setprecisionrr 3.0"


async def cmd_enginestats(db, args: str) -> str:
    p_30d = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE signal_type='precision' AND sent_at > NOW() - INTERVAL '30 days'")
    p_win = await db.fetchrow("SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END),0) AS w FROM signals WHERE signal_type='precision' AND outcome IS NOT NULL")
    p_rr = await db.fetchrow("SELECT COALESCE(AVG(final_rr_achieved),0) AS a FROM signals WHERE signal_type='precision' AND outcome IS NOT NULL")
    p_best = await db.fetchrow("SELECT pair FROM signals WHERE signal_type='precision' AND outcome IN ('TP1','TP2','TP3') GROUP BY pair ORDER BY COUNT(*) DESC LIMIT 1")
    p_avg_score = await db.fetchrow("SELECT COALESCE(AVG(score),0) AS a FROM signals WHERE signal_type='precision'")

    f_30d = await db.fetchrow("SELECT COUNT(*) AS c FROM signals WHERE signal_type='flow' AND sent_at > NOW() - INTERVAL '30 days'")
    f_win = await db.fetchrow("SELECT COALESCE(AVG(CASE WHEN outcome IN ('TP1','TP2','TP3') THEN 100 ELSE 0 END),0) AS w FROM signals WHERE signal_type='flow' AND outcome IS NOT NULL")
    f_rr = await db.fetchrow("SELECT COALESCE(AVG(final_rr_achieved),0) AS a FROM signals WHERE signal_type='flow' AND outcome IS NOT NULL")
    f_best = await db.fetchrow("SELECT pair FROM signals WHERE signal_type='flow' AND outcome IN ('TP1','TP2','TP3') GROUP BY pair ORDER BY COUNT(*) DESC LIMIT 1")
    f_avg_score = await db.fetchrow("SELECT COALESCE(AVG(score),0) AS a FROM signals WHERE signal_type='flow'")

    return (
        "PRECISION ENGINE          FLOW ENGINE\n"
        f"Signals (30d): {p_30d['c']:<14}Signals (30d): {f_30d['c']}\n"
        f"Win rate TP1: {round(float(p_win['w']),1)}%{' '*(12-len(str(round(float(p_win['w']),1))))}Win rate TP1: {round(float(f_win['w']),1)}%\n"
        f"Avg R:R: {round(float(p_rr['a']),2):<18}Avg R:R: {round(float(f_rr['a']),2)}\n"
        f"Best pair: {(p_best['pair'] if p_best else 'N/A'):<16}Best pair: {f_best['pair'] if f_best else 'N/A'}\n"
        f"Avg score: {round(float(p_avg_score['a']),1)}/15{' '*(11-len(str(round(float(p_avg_score['a']),1))))}Avg score: {round(float(f_avg_score['a']),1)}/8"
    )


async def cmd_cotstatus(db, args: str) -> str:
    """COT status — applies to Precision engine only."""
    cot_entries = await db.fetch("SELECT pair, bias, percentile, cached_at, valid_until FROM cot_cache ORDER BY cached_at DESC LIMIT 5")
    if not cot_entries:
        return "📊 COT Status (Precision Engine Only)\nNo COT data cached."

    lines = ["📊 COT Status (Precision Engine Only)"]
    for entry in cot_entries:
        lines.append(
            f"  {entry['pair']}: {entry['bias']} ({entry['percentile']}th pct) "
            f"cached {str(entry['cached_at'])[:10]} valid until {str(entry['valid_until'])[:10]}"
        )
    return "\n".join(lines)


async def cmd_refreshcot(db, args: str) -> str:
    """Refresh COT — applies to Precision engine only."""
    results = await refresh_cot(db)
    lines = ["🔄 COT Refresh (Precision Engine Only)"]
    for pair, data in results.items():
        if "error" in data:
            lines.append(f"  {pair}: ❌ {data['error']}")
        else:
            lines.append(f"  {pair}: {data['bias']} ({data['percentile']}th pct)")
    return "\n".join(lines)


async def cmd_rejectedsetups(db, args: str) -> str:
    rows = await db.fetch(
        "SELECT engine_type, pair, direction, score, gate_failed, rejection_reason, detected_at FROM rejected_setups ORDER BY detected_at DESC LIMIT 10"
    )
    if not rows:
        return "No rejected setups in last records."

    lines = ["Recent Rejected Setups:"]
    for r in rows:
        gate_info = f"Gate {r['gate_failed']}" if r.get("gate_failed") else "Scoring"
        lines.append(
            f"  [{r.get('engine_type','?').upper()}] {r['pair']} {r.get('direction','?')} "
            f"| {gate_info}: {r['rejection_reason']} | Score: {r.get('score',0)} | {str(r['detected_at'])[:16]}"
        )
    return "\n".join(lines)


async def cmd_manualsignal(db, args: str) -> str:
    """Manual signal insertion — admin only."""
    parts = args.strip().split()
    if len(parts) < 6:
        return "Usage: /manualsignal <type> <pair> <direction> <entry> <sl> <tp1> [tp2] [tp3]"

    signal_type = parts[0].lower()
    pair = parts[1].upper()
    direction = parts[2].upper()
    entry = float(parts[3])
    sl = float(parts[4])
    tp1 = float(parts[5])
    tp2 = float(parts[6]) if len(parts) > 6 else None
    tp3 = float(parts[7]) if len(parts) > 7 else None

    max_score = 15 if signal_type == "precision" else 8

    await db.execute(
        """INSERT INTO signals (signal_type, pair, direction, entry, sl, tp1, tp2, tp3, score, max_score, is_manual)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)""",
        (signal_type, pair, direction, entry, sl, tp1, tp2, tp3, max_score, max_score),
    )
    return f"✅ Manual {signal_type} signal created for {pair} {direction}"
