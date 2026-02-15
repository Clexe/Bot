from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import ContextTypes
from config import (
    ADMIN_ID, DEFAULT_SETTINGS, KNOWN_SYMBOLS, VALID_SESSIONS, VALID_MODES,
    VALID_TIMEFRAMES, VALID_HIGHER_TFS, logger,
)
from database import (
    load_users, save_user_settings, deactivate_user, get_user,
    get_user_async, save_user_settings_async, load_users_async,
    get_signal_stats_async, get_recent_signals_async,
)

# Runtime state for multi-step text input flows
RUNTIME_STATE = {}


def _main_keyboard():
    """Build the main reply keyboard."""
    return ReplyKeyboardMarkup([
        [KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")],
        [KeyboardButton("/mode"), KeyboardButton("status"), KeyboardButton("setsession")],
        [KeyboardButton("stats"), KeyboardButton("history"), KeyboardButton("help")],
    ], resize_keyboard=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - initialize user and show menu."""
    uid = str(update.effective_chat.id)
    await get_user_async(uid)
    await update.message.reply_text(
        "*Sniper V3* - SMC Trading Signals\n\n"
        "Use the menu below to configure your watchlist and preferences.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_main_keyboard(),
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle between MARKET and LIMIT execution mode."""
    uid = str(update.effective_chat.id)
    user = await get_user_async(uid)

    user["mode"] = "LIMIT" if user.get("mode") == "MARKET" else "MARKET"
    await save_user_settings_async(uid, user)
    await update.message.reply_text(
        f"*Mode Updated:* {user['mode']}\n\n"
        f"LIMIT = Pending Orders (Retest)\n"
        f"MARKET = Instant Execution",
        parse_mode=ParseMode.MARKDOWN,
    )


async def settf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set entry timeframe: /settf M5, /settf M15, /settf M30, /settf H1"""
    uid = str(update.effective_chat.id)
    user = await get_user_async(uid)

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
    await save_user_settings_async(uid, user)
    await update.message.reply_text(f"Entry timeframe set to: *{tf}*", parse_mode=ParseMode.MARKDOWN)


async def sethtf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set higher timeframe: /sethtf H4, /sethtf 1D, /sethtf 1W"""
    uid = str(update.effective_chat.id)
    user = await get_user_async(uid)

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
    await save_user_settings_async(uid, user)
    await update.message.reply_text(f"Higher timeframe set to: *{tf}*", parse_mode=ParseMode.MARKDOWN)


async def setrisk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set max risk in pips: /setrisk 30"""
    uid = str(update.effective_chat.id)
    user = await get_user_async(uid)

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
    await save_user_settings_async(uid, user)
    await update.message.reply_text(f"Max risk set to: *{pips} pips*", parse_mode=ParseMode.MARKDOWN)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: broadcast a message to all users."""
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    message_text = " ".join(context.args)
    users = await load_users_async()
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
            deactivate_user(uid)
            failed += 1
        except Exception as e:
            logger.warning("Broadcast failed for %s: %s", uid, e)
            failed += 1
    await update.message.reply_text(f"Broadcast done. Sent: {sent}, Failed: {failed}")


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: show user count and pair stats."""
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID:
        return
    users = await load_users_async()
    active_pairs = sum(len(u.get("pairs", [])) for u in users.values())
    overall_stats = await get_signal_stats_async()
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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text-based menu interactions."""
    uid = str(update.effective_chat.id)
    text = update.message.text.lower().strip()
    user = await get_user_async(uid)
    state = RUNTIME_STATE.get(uid)

    if text == "status":
        from scanner import LAST_SCAN_TIME, IS_SCANNING
        import time as _time
        time_diff = int(_time.time() - LAST_SCAN_TIME)
        scan_interval = user.get("scan_interval", DEFAULT_SETTINGS["scan_interval"])
        remaining = max(0, scan_interval - time_diff)
        status_label = "SCANNING" if IS_SCANNING else f"IDLE ({remaining}s)"
        await update.message.reply_text(
            f"*Status*\n"
            f"Mode: *{user.get('mode', 'MARKET')}*\n"
            f"Entry TF: *{user.get('timeframe', 'M15')}*\n"
            f"Higher TF: *{user.get('higher_tf', '1D')}*\n"
            f"Risk: *{user.get('risk_pips', 50)} pips*\n"
            f"Pairs: {len(user['pairs'])}\n"
            f"Session: {user['session']}\n"
            f"Scanner: {status_label}",
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
        stats = await get_signal_stats_async()
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
        signals = await get_recent_signals_async(limit=10)
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

    elif text == "help":
        await update.message.reply_text(
            "*Commands:*\n"
            "/mode - Toggle Limit/Market\n"
            "/settf - Set entry timeframe (M5/M15/M30/H1)\n"
            "/sethtf - Set higher timeframe (H4/1D/1W)\n"
            "/setrisk - Set max risk in pips\n\n"
            "*Menu:*\n"
            "add - Add pair to watchlist\n"
            "remove - Remove pair\n"
            "pairs - View watchlist\n"
            "setsession - Set trading session\n"
            "status - Check bot status\n"
            "stats - View signal performance\n"
            "history - Recent signal log",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif state == "add":
        RUNTIME_STATE[uid] = None
        # Support multiple symbols separated by newlines, commas, or spaces
        raw_symbols = text.replace(",", " ").replace("\n", " ").split()
        added = []
        skipped = []
        for raw in raw_symbols:
            symbol = raw.strip().upper()
            if not symbol:
                continue
            if symbol in user["pairs"]:
                skipped.append(f"{symbol} (already added)")
            elif symbol not in KNOWN_SYMBOLS:
                skipped.append(f"{symbol} (unknown)")
            else:
                user["pairs"].append(symbol)
                added.append(symbol)
        if added:
            await save_user_settings_async(uid, user)
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
            await save_user_settings_async(uid, user)
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
            await save_user_settings_async(uid, user)
            await update.message.reply_text(f"Session set to: {session_val}")
