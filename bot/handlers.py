from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import ContextTypes
from config import (
    settings, KNOWN_SYMBOLS, VALID_SESSIONS, VALID_MODES,
    VALID_TIMEFRAMES, VALID_HIGHER_TFS, FOREX_BASES,
)
from database.users import (
    get_user_async, save_user_settings_async,
    load_users_async, deactivate_user_async, DEFAULT_SETTINGS,
)
from database.signal_queries import (
    get_signal_stats_async, get_recent_signals_async,
    get_pair_breakdown_async, get_session_breakdown_async,
    get_zone_type_stats_async, get_regime_stats_async,
    get_open_signals_async,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Runtime state for multi-step text input flows
RUNTIME_STATE = {}


def _db(context):
    """Shortcut to get the shared Database instance from bot_data."""
    return context.bot_data["db"]


def _main_keyboard():
    """Build the main reply keyboard."""
    return ReplyKeyboardMarkup([
        [KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")],
        [KeyboardButton("/mode"), KeyboardButton("status"), KeyboardButton("setsession")],
        [KeyboardButton("stats"), KeyboardButton("history"), KeyboardButton("help")],
        [KeyboardButton("exposure"), KeyboardButton("drawdown"), KeyboardButton("/journal")],
    ], resize_keyboard=True)


# =====================
# COMMAND HANDLERS
# =====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - initialize user and show menu."""
    uid = str(update.effective_chat.id)
    await get_user_async(_db(context), uid)
    await update.message.reply_text(
        "*Signalix* - SMC Trading Signals\n\n"
        "Use the menu below to configure your watchlist and preferences.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_main_keyboard(),
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle between MARKET and LIMIT execution mode."""
    db = _db(context)
    uid = str(update.effective_chat.id)
    user = await get_user_async(db, uid)
    user["mode"] = "LIMIT" if user.get("mode") == "MARKET" else "MARKET"
    await save_user_settings_async(db, uid, user)
    await update.message.reply_text(
        f"*Mode Updated:* {user['mode']}\n\n"
        f"LIMIT = Pending Orders (Retest)\n"
        f"MARKET = Instant Execution",
        parse_mode=ParseMode.MARKDOWN,
    )


async def settf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set entry timeframe: /settf M5, /settf M15, /settf M30, /settf H1"""
    db = _db(context)
    uid = str(update.effective_chat.id)
    user = await get_user_async(db, uid)
    if not context.args:
        await update.message.reply_text(
            f"Current timeframe: *{user.get('timeframe', 'M15')}*\n"
            f"Usage: `/settf M5` or `/settf M15` or `/settf M30` or `/settf H1`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    tf = context.args[0].upper()
    if tf not in VALID_TIMEFRAMES:
        await update.message.reply_text(
            f"Invalid timeframe. Choose: {', '.join(sorted(VALID_TIMEFRAMES))}"
        )
        return
    user["timeframe"] = tf
    await save_user_settings_async(db, uid, user)
    await update.message.reply_text(f"Entry timeframe set to: *{tf}*", parse_mode=ParseMode.MARKDOWN)


async def sethtf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set higher timeframe: /sethtf H4, /sethtf 1D, /sethtf 1W"""
    db = _db(context)
    uid = str(update.effective_chat.id)
    user = await get_user_async(db, uid)
    if not context.args:
        await update.message.reply_text(
            f"Current higher TF: *{user.get('higher_tf', '1D')}*\n"
            f"Usage: `/sethtf H4` or `/sethtf 1D` or `/sethtf 1W`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    tf = context.args[0].upper()
    if tf not in VALID_HIGHER_TFS:
        await update.message.reply_text(
            f"Invalid higher timeframe. Choose: {', '.join(sorted(VALID_HIGHER_TFS))}"
        )
        return
    user["higher_tf"] = tf
    await save_user_settings_async(db, uid, user)
    await update.message.reply_text(f"Higher timeframe set to: *{tf}*", parse_mode=ParseMode.MARKDOWN)


async def setrisk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set max risk in pips: /setrisk 30"""
    db = _db(context)
    uid = str(update.effective_chat.id)
    user = await get_user_async(db, uid)
    if not context.args:
        await update.message.reply_text(
            f"Current max risk: *{user.get('risk_pips', 50)} pips*\n"
            f"Usage: `/setrisk 30` (range: 10-200)",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        pips = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please enter a number. Usage: `/setrisk 30`", parse_mode=ParseMode.MARKDOWN)
        return
    if pips < 10 or pips > 200:
        await update.message.reply_text("Risk must be between 10 and 200 pips.")
        return
    user["risk_pips"] = pips
    await save_user_settings_async(db, uid, user)
    await update.message.reply_text(f"Max risk set to: *{pips} pips*", parse_mode=ParseMode.MARKDOWN)


async def setbalance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set account balance for lot size calculation: /setbalance 10000"""
    db = _db(context)
    uid = str(update.effective_chat.id)
    user = await get_user_async(db, uid)
    if not context.args:
        bal = user.get("balance", 0)
        bal_str = f"${bal:,.0f}" if bal else "Not set"
        await update.message.reply_text(
            f"Current balance: *{bal_str}*\n"
            f"Usage: `/setbalance 10000`\n"
            f"Set to 0 to hide lot size from signals",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        balance = float(context.args[0].replace(",", ""))
    except ValueError:
        await update.message.reply_text("Please enter a number. Usage: `/setbalance 10000`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    if balance < 0 or balance > 100_000_000:
        await update.message.reply_text("Balance must be between 0 and 100,000,000.")
        return
    user["balance"] = balance
    await save_user_settings_async(db, uid, user)
    if balance > 0:
        await update.message.reply_text(
            f"Balance set to: *${balance:,.0f}*\nLot sizes will appear in signals.",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text("Balance cleared. Lot sizes hidden from signals.")


async def setriskpct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set risk percentage per trade: /setriskpct 2"""
    db = _db(context)
    uid = str(update.effective_chat.id)
    user = await get_user_async(db, uid)
    if not context.args:
        await update.message.reply_text(
            f"Current risk: *{user.get('risk_pct', 1)}%* per trade\n"
            f"Usage: `/setriskpct 2` (range: 0.5-10)",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        pct = float(context.args[0])
    except ValueError:
        await update.message.reply_text("Please enter a number. Usage: `/setriskpct 2`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    if pct < 0.5 or pct > 10:
        await update.message.reply_text("Risk must be between 0.5% and 10%.")
        return
    user["risk_pct"] = pct
    await save_user_settings_async(db, uid, user)
    await update.message.reply_text(f"Risk per trade set to: *{pct}%*", parse_mode=ParseMode.MARKDOWN)


async def touchmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle touch trade mode: /touchmode"""
    db = _db(context)
    uid = str(update.effective_chat.id)
    user = await get_user_async(db, uid)
    user["touch_trade"] = not user.get("touch_trade", False)
    await save_user_settings_async(db, uid, user)
    status = "ON" if user["touch_trade"] else "OFF"
    await update.message.reply_text(
        f"*Touch Trade:* {status}\n\n"
        f"ON = Limit entries at zone tap when sweep detected (no engulfing needed)\n"
        f"OFF = Require engulfing confirmation before entry",
        parse_mode=ParseMode.MARKDOWN,
    )


async def journal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analytics dashboard: /journal or /journal 7 (days)."""
    db = _db(context)
    days = 30
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            pass
    days = max(1, min(days, 90))

    stats = await get_signal_stats_async(db, days=days)
    pair_data = await get_pair_breakdown_async(db, days=days)
    session_data = await get_session_breakdown_async(db, days=days)
    zone_data = await get_zone_type_stats_async(db, days=days)
    regime_data = await get_regime_stats_async(db, days=days)

    lines = [f"*Analytics Dashboard ({days}d)*\n"]

    if stats and stats['total'] > 0:
        closed = stats['wins'] + stats['losses']
        lines.append(
            f"*Overall*\n"
            f"Signals: {stats['total']} | Closed: {closed}\n"
            f"Win Rate: *{stats['win_rate']:.1f}%*\n"
            f"P&L: *{stats['total_pips']:+.1f} pips* | Avg: {stats['avg_pips']:+.1f}/trade\n"
        )
    else:
        lines.append("No signal data for this period.\n")

    if pair_data:
        lines.append("*Per Pair*")
        for p in pair_data[:10]:
            icon = "+" if p['total_pips'] >= 0 else "-"
            lines.append(
                f"`{p['pair']:8s}` {p['wins']}W/{p['losses']}L "
                f"({p['win_rate']}%) {icon}{abs(p['total_pips']):.1f}p"
            )
        lines.append("")

    if session_data:
        lines.append("*Per Session*")
        for s in session_data:
            icon = "+" if s['total_pips'] >= 0 else "-"
            lines.append(
                f"`{s['session']:10s}` {s['wins']}W/{s['losses']}L "
                f"({s['win_rate']}%) {icon}{abs(s['total_pips']):.1f}p"
            )
        lines.append("")

    if zone_data:
        lines.append("*Per Zone Type*")
        for z in zone_data:
            icon = "+" if z['total_pips'] >= 0 else "-"
            lines.append(
                f"`{z['zone_type']:6s}` {z['wins']}W/{z['losses']}L "
                f"({z['win_rate']}%) {icon}{abs(z['total_pips']):.1f}p"
            )
        lines.append("")

    if regime_data:
        lines.append("*Per Regime*")
        for r in regime_data:
            icon = "+" if r['total_pips'] >= 0 else "-"
            regime_short = r['regime'].replace("TRENDING_", "T-")
            lines.append(
                f"`{regime_short:10s}` {r['wins']}W/{r['losses']}L "
                f"({r['win_rate']}%) {icon}{abs(r['total_pips']):.1f}p"
            )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# =====================
# ADMIN COMMANDS
# =====================

def _is_admin(user_id):
    """Check if a user is an admin."""
    return user_id in settings.admin_chat_ids


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: broadcast a message to all users."""
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    db = _db(context)
    message_text = " ".join(context.args)
    users = await load_users_async(db)
    sent, failed = 0, 0
    for uid in list(users.keys()):
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"*ANNOUNCEMENT*\n\n{message_text}",
                parse_mode=ParseMode.MARKDOWN,
            )
            sent += 1
        except Forbidden:
            await deactivate_user_async(db, uid)
            failed += 1
        except Exception as e:
            logger.warning("Broadcast failed for %s: %s", uid, e)
            failed += 1
    await update.message.reply_text(f"Broadcast done. Sent: {sent}, Failed: {failed}")


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: show user count and pair stats."""
    if not _is_admin(update.effective_user.id):
        return
    db = _db(context)
    users = await load_users_async(db)
    active_pairs = sum(len(u.get("pairs", [])) for u in users.values())
    overall_stats = await get_signal_stats_async(db)
    stats_line = ""
    if overall_stats:
        stats_line = (
            f"\nSignals: {overall_stats['total']} "
            f"(W:{overall_stats['wins']} L:{overall_stats['losses']} O:{overall_stats['open']})\n"
            f"Win Rate: {overall_stats['win_rate']:.1f}% | P&L: {overall_stats['total_pips']} pips"
        )
    await update.message.reply_text(
        f"Users: `{len(users)}` | Pairs: `{active_pairs}`{stats_line}",
        parse_mode=ParseMode.MARKDOWN,
    )


# =====================
# TEXT MENU HANDLER
# =====================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text-based menu interactions."""
    db = _db(context)
    uid = str(update.effective_chat.id)
    text = update.message.text.lower().strip()
    user = await get_user_async(db, uid)
    state = RUNTIME_STATE.get(uid)

    if text == "status":
        open_sigs = await get_open_signals_async(db)
        await update.message.reply_text(
            f"*Status*\n"
            f"Mode: *{user.get('mode', 'MARKET')}*\n"
            f"Entry TF: *{user.get('timeframe', 'M15')}*\n"
            f"Higher TF: *{user.get('higher_tf', '1D')}*\n"
            f"Risk: *{user.get('risk_pips', 50)} pips* ({user.get('risk_pct', 1)}%)\n"
            f"Balance: *{'${:,.0f}'.format(user.get('balance', 0)) if user.get('balance') else 'Not set'}*\n"
            f"Touch Trade: *{'ON' if user.get('touch_trade') else 'OFF'}*\n"
            f"Pairs: {len(user['pairs'])}\n"
            f"Session: {user['session']}\n"
            f"Open Trades: {len(open_sigs)}",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif text == "add":
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Enter symbol to add (e.g. XAUUSD):")

    elif text == "remove":
        if not user["pairs"]:
            await update.message.reply_text("Your watchlist is empty.")
            return
        RUNTIME_STATE[uid] = "remove"
        await update.message.reply_text(
            f"Symbol to remove:\nCurrent: {', '.join(user['pairs'])}"
        )

    elif text == "pairs":
        if user['pairs']:
            await update.message.reply_text(f"Watchlist: {', '.join(user['pairs'])}")
        else:
            await update.message.reply_text("Watchlist is empty. Use 'add' to add symbols.")

    elif text == "setsession":
        RUNTIME_STATE[uid] = "session"
        await update.message.reply_text("Enter session: LONDON, NY, or BOTH")

    elif text == "stats":
        stats = await get_signal_stats_async(db)
        if not stats or stats['total'] == 0:
            await update.message.reply_text("No signal data yet. Signals will be tracked automatically.")
            return
        closed = stats['wins'] + stats['losses']
        await update.message.reply_text(
            f"*Signal Performance (30d)*\n\n"
            f"Total Signals: {stats['total']}\n"
            f"Open: {stats['open']}\n"
            f"Closed: {closed}\n"
            f"Wins: {stats['wins']} | Losses: {stats['losses']}\n"
            f"Win Rate: *{stats['win_rate']:.1f}%*\n"
            f"Total P&L: *{stats['total_pips']} pips*\n"
            f"Avg P&L: {stats['avg_pips']} pips/trade",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif text == "history":
        signals = await get_recent_signals_async(db, limit=10)
        if not signals:
            await update.message.reply_text("No signal history yet.")
            return
        lines = ["*Recent Signals*\n"]
        for s in signals:
            outcome = s['outcome']
            if outcome == "WIN":
                icon = "+"
            elif outcome == "LOSS":
                icon = "-"
            else:
                icon = "~"
            pnl = f"{s['pnl_pips']:+.1f}p" if s['pnl_pips'] else "open"
            lines.append(
                f"`{s['created_at']}` {s['direction']} {s['pair']} "
                f"[{icon}{outcome}] {pnl}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    elif text == "exposure":
        open_sigs = await get_open_signals_async(db)
        open_list = "\n".join(
            f"  {s['direction']} {s['pair']}" for s in open_sigs
        ) if open_sigs else "  None"
        await update.message.reply_text(
            f"*Open Positions ({len(open_sigs)})*\n{open_list}",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif text == "drawdown":
        await update.message.reply_text(
            "*Risk Shield*\nDrawdown tracking will be available once the full strategy engine is ported.",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif text == "help":
        await update.message.reply_text(
            "*Commands:*\n"
            "/mode - Toggle Limit/Market\n"
            "/settf - Set entry timeframe (M5/M15/M30/H1)\n"
            "/sethtf - Set higher timeframe (H4/1D/1W)\n"
            "/setrisk - Set max risk in pips\n"
            "/setbalance - Set account balance for lot sizing\n"
            "/setriskpct - Set risk % per trade\n"
            "/touchmode - Toggle touch trade mode\n"
            "/journal - Analytics dashboard\n\n"
            "*Menu:*\n"
            "add - Add pair to watchlist\n"
            "remove - Remove pair\n"
            "pairs - View watchlist\n"
            "setsession - Set trading session\n"
            "status - Check bot status\n"
            "stats - View signal performance\n"
            "history - Recent signal log\n"
            "exposure - View open positions",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif state == "add":
        RUNTIME_STATE[uid] = None
        raw_symbols = text.replace(",", " ").replace("\n", " ").split()
        added = []
        skipped = []
        for raw in raw_symbols:
            symbol = raw.strip().upper()
            if not symbol:
                continue
            if symbol in user["pairs"]:
                skipped.append(f"{symbol} (already added)")
            elif symbol not in KNOWN_SYMBOLS and not symbol.endswith("USDT"):
                skipped.append(f"{symbol} (unknown)")
            elif symbol.endswith("USDT") and symbol[:-4] in FOREX_BASES:
                skipped.append(f"{symbol} (forex pair, not available on Bybit)")
            else:
                user["pairs"].append(symbol)
                added.append(symbol)
        if added:
            await save_user_settings_async(db, uid, user)
        parts = []
        if added:
            parts.append(f"Added: {', '.join(added)}")
        if skipped:
            parts.append(f"Skipped: {', '.join(skipped)}")
        if parts:
            await update.message.reply_text("\n".join(parts))
        else:
            await update.message.reply_text("No valid symbols provided. Use standard symbols like XAUUSD, BTCUSD, V75, etc.")

    elif state == "remove":
        symbol = text.upper()
        RUNTIME_STATE[uid] = None
        if symbol in user["pairs"]:
            user["pairs"].remove(symbol)
            await save_user_settings_async(db, uid, user)
            await update.message.reply_text(f"{symbol} removed.")
        else:
            await update.message.reply_text(f"{symbol} not found in your watchlist.")

    elif state == "session":
        session_val = text.upper()
        RUNTIME_STATE[uid] = None
        if session_val not in VALID_SESSIONS:
            await update.message.reply_text(f"Invalid session. Choose: {', '.join(VALID_SESSIONS)}")
        else:
            user["session"] = session_val
            await save_user_settings_async(db, uid, user)
            await update.message.reply_text(f"Session set to: {session_val}")
