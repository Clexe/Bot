import os
import json
import asyncio
import logging
import websockets
import time
import requests
import psycopg2
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta
import pandas as pd
from pybit.unified_trading import HTTP
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# =====================
# LOGGING
# =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("SniperV3")

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
DERIV_TOKEN = os.getenv("DERIV_TOKEN")
DERIV_APP_ID = os.getenv("DERIV_APP_ID")
BYBIT_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET = os.getenv("BYBIT_API_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID", "")

# News Settings
USE_NEWS_FILTER = True
NEWS_IMPACT = ["High", "Medium"]

# Default user settings (single source of truth)
DEFAULT_SETTINGS = {
    "pairs": ["XAUUSD", "BTCUSD", "V75"],
    "scan_interval": 60,
    "cooldown": 60,
    "max_spread": 0.0005,
    "session": "BOTH",
    "mode": "MARKET"
}

VALID_SESSIONS = {"LONDON", "NY", "BOTH"}

# Supported symbols for validation
KNOWN_SYMBOLS = {
    "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
    "EURGBP", "EURJPY", "GBPJPY", "AUDCAD", "AUDCHF",
    "BTCUSD", "ETHUSD", "SOLUSD", "BTCUSDT", "ETHUSDT",
    "US30", "NAS100", "GER40", "UK100", "US500",
    "V75", "V10", "V25", "V50", "V100",
    "V75_1S", "V10_1S", "V25_1S", "V50_1S", "V100_1S",
    "BOOM300", "BOOM500", "BOOM1000", "CRASH300", "CRASH500", "CRASH1000",
    "STEP_INDEX", "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
    "JUMP10", "JUMP25", "JUMP50", "JUMP75", "JUMP100",
}

# Signal TTL for cleanup (2 hours)
SIGNAL_TTL = 7200

# Initialize Bybit
bybit = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)

# Global State
SENT_SIGNALS = {}
RUNTIME_STATE = {}
LAST_SCAN_TIME = 0
IS_SCANNING = False
NEWS_CACHE = []
LAST_NEWS_FETCH = 0

# =====================
# DATABASE ENGINE
# =====================
@contextmanager
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    settings JSONB DEFAULT '{}'::jsonb,
                    is_active BOOLEAN DEFAULT TRUE,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            conn.commit()
            cur.close()
        logger.info("Database table ready")
    except Exception as e:
        logger.error("DB init error: %s", e)

def load_users():
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, settings FROM users WHERE is_active = TRUE")
        rows = cur.fetchall()
        cur.close()

    users = {}
    for r in rows:
        uid = str(r[0])
        saved_settings = r[1] if r[1] else {}
        settings = {**DEFAULT_SETTINGS, **saved_settings}
        # Ensure pairs is always a list
        if not isinstance(settings.get("pairs"), list):
            settings["pairs"] = list(DEFAULT_SETTINGS["pairs"])
        users[uid] = settings
    return users

def save_user_settings(chat_id, settings):
    with get_db_connection() as conn:
        cur = conn.cursor()
        json_settings = json.dumps(settings)
        cur.execute("""
            INSERT INTO users (user_id, settings, is_active)
            VALUES (%s, %s, TRUE)
            ON CONFLICT (user_id)
            DO UPDATE SET settings = %s, is_active = TRUE;
        """, (chat_id, json_settings, json_settings))
        conn.commit()
        cur.close()

def deactivate_user(chat_id):
    """Mark a user as inactive (e.g. they blocked the bot)."""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE users SET is_active = FALSE WHERE user_id = %s", (chat_id,))
            conn.commit()
            cur.close()
        logger.info("Deactivated user %s", chat_id)
    except Exception as e:
        logger.error("Failed to deactivate user %s: %s", chat_id, e)

def get_user(users, chat_id):
    chat_id = str(chat_id)
    if chat_id not in users:
        default_settings = {**DEFAULT_SETTINGS}
        default_settings["pairs"] = list(DEFAULT_SETTINGS["pairs"])  # fresh copy
        save_user_settings(chat_id, default_settings)
        return default_settings
    return users[chat_id]

# =====================
# NEWS FILTER
# =====================
def fetch_forex_news():
    global NEWS_CACHE, LAST_NEWS_FETCH
    if time.time() - LAST_NEWS_FETCH < 3600:
        return

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
            headers=headers,
            timeout=15
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        events = []
        for event in root.findall('event'):
            impact = event.find('impact').text
            if impact not in NEWS_IMPACT:
                continue

            date = event.find('date').text
            time_str = event.find('time').text
            currency = event.find('country').text

            if "am" in time_str or "pm" in time_str:
                dt_str = f"{date} {time_str}"
                dt_obj = None
                for fmt in ("%m-%d-%Y %I:%M%p", "%Y-%m-%d %I:%M%p"):
                    try:
                        dt_obj = datetime.strptime(dt_str, fmt)
                        break
                    except ValueError:
                        continue
                if dt_obj is None:
                    logger.warning("Unparseable news date: %s", dt_str)
                    continue
                events.append({"currency": currency, "time": dt_obj})

        NEWS_CACHE = events
        LAST_NEWS_FETCH = time.time()
        logger.info("Fetched %d news events", len(events))
    except Exception as e:
        logger.error("News fetch error: %s", e)

def is_news_blackout(pair):
    if not USE_NEWS_FILTER:
        return False
    fetch_forex_news()
    currencies = set()
    for code in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"):
        if code in pair:
            currencies.add(code)
    if "XAU" in pair:
        currencies.add("USD")

    now = datetime.utcnow()
    for event in NEWS_CACHE:
        if event['currency'] in currencies:
            diff = (event['time'] - now).total_seconds() / 60
            if -30 <= diff <= 30:
                return True
    return False

# =====================
# TIME & MARKET FILTERS
# =====================
def is_in_session(session_type):
    now_hour = datetime.utcnow().hour
    if session_type == "LONDON":
        return 8 <= now_hour <= 16
    if session_type == "NY":
        return 13 <= now_hour <= 21
    return True

ALWAYS_OPEN_KEYS = [
    "BTC", "ETH", "SOL", "USDT", "R_",
    "V75", "V10", "V25", "V50", "V100",
    "1HZ", "BOOM", "CRASH", "JUMP", "STEP"
]

def is_market_open(pair):
    clean = pair.upper()
    if any(k in clean for k in ALWAYS_OPEN_KEYS):
        return True

    now = datetime.utcnow()
    weekday = now.weekday()
    hour = now.hour
    if weekday == 4 and hour >= 21:
        return False
    if weekday == 5:
        return False
    if weekday == 6 and hour < 21:
        return False
    return True

# =====================
# SMART ROUTER & STRATEGY
# =====================
async def fetch_data(pair, interval):
    raw_pair = pair.replace("/", "").strip()
    clean_pair = raw_pair.upper()
    deriv_keywords = [
        "XAU", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF",
        "R_", "V75", "1S", "FRX", "US30", "NAS", "GER", "UK100",
        "BOOM", "CRASH", "STEP"
    ]
    is_deriv = any(x in clean_pair for x in deriv_keywords)

    if is_deriv:
        return await _fetch_deriv(clean_pair, interval)
    else:
        return _fetch_bybit(clean_pair, interval)


DERIV_SYMBOL_MAP = {
    "XAUUSD": "frxXAUUSD",
    "EURUSD": "frxEURUSD",
    "GBPUSD": "frxGBPUSD",
    "USDJPY": "frxUSDJPY",
    "AUDUSD": "frxAUDUSD",
    "NZDUSD": "frxNZDUSD",
    "USDCAD": "frxUSDCAD",
    "USDCHF": "frxUSDCHF",
    "EURGBP": "frxEURGBP",
    "EURJPY": "frxEURJPY",
    "GBPJPY": "frxGBPJPY",
    "US30": "OTC_US30",
}


async def _fetch_deriv(clean_pair, interval):
    mapped = DERIV_SYMBOL_MAP.get(clean_pair, clean_pair)
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    gran = 900 if interval == "M15" else 86400
    try:
        async with websockets.connect(uri, close_timeout=10) as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            # Wait for auth response with timeout
            auth_res = None
            for _ in range(5):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if "authorize" in msg:
                    auth_res = msg
                    break
                if "error" in msg:
                    logger.warning("Deriv auth error for %s: %s", clean_pair, msg["error"])
                    return pd.DataFrame()
            if auth_res is None:
                logger.warning("Deriv auth timeout for %s", clean_pair)
                return pd.DataFrame()

            await ws.send(json.dumps({
                "ticks_history": mapped,
                "adjust_start_time": 1,
                "count": 100,
                "end": "latest",
                "style": "candles",
                "granularity": gran
            }))
            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if not res.get("candles"):
                if "error" in res:
                    logger.warning("Deriv candles error for %s: %s", clean_pair, res["error"])
                return pd.DataFrame()
            df = pd.DataFrame(res["candles"])
            return df[['open', 'high', 'low', 'close']].apply(pd.to_numeric)
    except asyncio.TimeoutError:
        logger.warning("Deriv WebSocket timeout for %s", clean_pair)
        return pd.DataFrame()
    except Exception as e:
        logger.error("Deriv fetch error for %s: %s", clean_pair, e)
        return pd.DataFrame()


def _fetch_bybit(clean_pair, interval):
    try:
        tf = "15" if interval == "M15" else "D"
        resp = bybit.get_kline(category="linear", symbol=clean_pair, interval=tf, limit=100)
        if not resp or 'result' not in resp or not resp['result'].get('list'):
            logger.warning("Bybit empty response for %s", clean_pair)
            return pd.DataFrame()
        df = pd.DataFrame(resp['result']['list'], columns=['ts', 'open', 'high', 'low', 'close', 'vol', 'turnover'])
        df = df[['open', 'high', 'low', 'close']].apply(pd.to_numeric)
        return df.iloc[::-1]
    except Exception as e:
        logger.error("Bybit fetch error for %s: %s", clean_pair, e)
        return pd.DataFrame()


def get_smc_signal(df_l, df_h, pair):
    if df_l.empty or df_h.empty or len(df_l) < 23 or len(df_h) < 20:
        return None

    clean_pair = pair.upper()
    if any(x in clean_pair for x in ["JPY", "V75", "R_", "BOOM", "CRASH", "STEP", "XAU", "US30", "NAS", "GER", "US500"]):
        pip_val = 10
    else:
        pip_val = 10000

    bias = "BULL" if df_h['close'].iloc[-1] > df_h['close'].iloc[-20] else "BEAR"

    swing_high = df_l['high'].iloc[-23:-3].max()
    swing_low = df_l['low'].iloc[-23:-3].min()
    recent_price_action = df_l['close'].iloc[-5:]
    bullish_bos = recent_price_action.max() > swing_high
    bearish_bos = recent_price_action.min() < swing_low

    c1, c3 = df_l.iloc[-3], df_l.iloc[-1]
    max_risk_price = 50 / pip_val

    sig = None

    if bias == "BULL" and bullish_bos:
        if c3.low > c1.high:
            raw_sl = swing_low

            limit_entry = swing_high
            limit_sl = limit_entry - max_risk_price if (limit_entry - raw_sl) > max_risk_price else raw_sl

            market_entry = c3.close
            market_sl = market_entry - max_risk_price if (market_entry - raw_sl) > max_risk_price else raw_sl

            sig = {
                "act": "BUY",
                "limit_e": limit_entry, "limit_sl": limit_sl,
                "market_e": market_entry, "market_sl": market_sl,
                "tp": df_h['high'].max()
            }

    if bias == "BEAR" and bearish_bos:
        if c3.high < c1.low:
            raw_sl = swing_high

            limit_entry = swing_low
            limit_sl = limit_entry + max_risk_price if (raw_sl - limit_entry) > max_risk_price else raw_sl

            market_entry = c3.close
            market_sl = market_entry + max_risk_price if (raw_sl - market_entry) > max_risk_price else raw_sl

            sig = {
                "act": "SELL",
                "limit_e": limit_entry, "limit_sl": limit_sl,
                "market_e": market_entry, "market_sl": market_sl,
                "tp": df_h['low'].min()
            }

    return sig


def _cleanup_old_signals():
    """Remove expired entries from SENT_SIGNALS to prevent memory leak."""
    now = time.time()
    expired = [k for k, v in SENT_SIGNALS.items() if isinstance(v, dict) and (now - v['time']) > SIGNAL_TTL]
    for k in expired:
        del SENT_SIGNALS[k]
    if expired:
        logger.info("Cleaned up %d expired signal entries", len(expired))


def _format_signal_msg(sig, pair, mode):
    """Format a signal message with R:R ratio."""
    if mode == "LIMIT":
        entry = sig['limit_e']
        sl = sig['limit_sl']
        label = "LIMIT"
        emoji = "\U0001f3af"  # target
    else:
        entry = sig['market_e']
        sl = sig['market_sl']
        label = "MARKET"
        emoji = "\U0001f6a8"  # siren

    tp = sig['tp']
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = f"{reward / risk:.1f}" if risk > 0 else "N/A"

    return (
        f"{emoji} *SMC SIGNAL ({label})*\n"
        f"Symbol: `{pair}`\n"
        f"Action: *{sig['act']} {label}*\n"
        f"Entry: `{entry:.5f}`\n"
        f"TP: `{tp:.5f}` | SL: `{sl:.5f}`\n"
        f"R:R = *1:{rr}*"
    )


# =====================
# COMMAND HANDLERS
# =====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    users = load_users()
    get_user(users, uid)  # ensure user is created
    await update.message.reply_text(
        "Sniper V3 Ready",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")],
            [KeyboardButton("/mode"), KeyboardButton("status"), KeyboardButton("setsession")],
            [KeyboardButton("help")]
        ], resize_keyboard=True)
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle between MARKET and LIMIT execution."""
    uid = str(update.effective_chat.id)
    users = load_users()
    user = get_user(users, uid)

    user["mode"] = "LIMIT" if user.get("mode") == "MARKET" else "MARKET"
    save_user_settings(uid, user)
    await update.message.reply_text(
        f"*Mode Updated:* {user['mode']}\n\nLIMIT = Pending Orders (Retest)\nMARKET = Instant Execution",
        parse_mode=ParseMode.MARKDOWN
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    message_text = " ".join(context.args)
    users = load_users()
    sent, failed = 0, 0
    for uid in list(users.keys()):
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"*ANNOUNCEMENT*\n\n{message_text}",
                parse_mode=ParseMode.MARKDOWN
            )
            sent += 1
        except Forbidden:
            deactivate_user(uid)
            failed += 1
        except Exception as e:
            logger.warning("Broadcast send failed for %s: %s", uid, e)
            failed += 1
    await update.message.reply_text(f"Broadcast done. Sent: {sent}, Failed: {failed}")


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID:
        return
    users = load_users()
    active_pairs = sum(len(u.get("pairs", [])) for u in users.values())
    await update.message.reply_text(
        f"Users: `{len(users)}` | Pairs tracked: `{active_pairs}`",
        parse_mode=ParseMode.MARKDOWN
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    text = update.message.text.lower().strip()
    users = load_users()
    user = get_user(users, uid)
    state = RUNTIME_STATE.get(uid)

    if text == "status":
        time_diff = int(time.time() - LAST_SCAN_TIME)
        scan_interval = user.get("scan_interval", DEFAULT_SETTINGS["scan_interval"])
        remaining = max(0, scan_interval - time_diff)
        status_label = "SCANNING" if IS_SCANNING else f"IDLE ({remaining}s)"
        await update.message.reply_text(
            f"*Status*\n"
            f"Mode: *{user.get('mode', 'MARKET')}*\n"
            f"Pairs: {len(user['pairs'])}\n"
            f"Session: {user['session']}\n"
            f"Scanner: {status_label}",
            parse_mode=ParseMode.MARKDOWN
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

    elif text == "help":
        await update.message.reply_text(
            "*Commands:*\n"
            "/mode - Toggle Limit/Market\n"
            "add - Add pair to watchlist\n"
            "remove - Remove pair\n"
            "pairs - View watchlist\n"
            "setsession - Set trading session\n"
            "status - Check bot status",
            parse_mode=ParseMode.MARKDOWN
        )

    elif state == "add":
        symbol = text.upper()
        RUNTIME_STATE[uid] = None
        if symbol in user["pairs"]:
            await update.message.reply_text(f"{symbol} is already in your watchlist.")
        elif symbol not in KNOWN_SYMBOLS:
            await update.message.reply_text(
                f"Unknown symbol: {symbol}\n"
                f"Use standard symbols like XAUUSD, BTCUSD, V75, etc."
            )
        else:
            user["pairs"].append(symbol)
            save_user_settings(uid, user)
            await update.message.reply_text(f"{symbol} added to watchlist.")

    elif state == "remove":
        symbol = text.upper()
        RUNTIME_STATE[uid] = None
        if symbol in user["pairs"]:
            user["pairs"].remove(symbol)
            save_user_settings(uid, user)
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
            save_user_settings(uid, user)
            await update.message.reply_text(f"Session set to: {session_val}")


# =====================
# ENGINE & SCANNER
# =====================
async def scanner_loop(app):
    global LAST_SCAN_TIME, IS_SCANNING
    while True:
        try:
            IS_SCANNING = True
            LAST_SCAN_TIME = time.time()
            users = load_users()

            # Cleanup stale signal entries periodically
            _cleanup_old_signals()

            pair_map = {}
            for uid, settings in users.items():
                if not is_in_session(settings["session"]):
                    continue
                for pair in settings["pairs"]:
                    clean_p = pair.upper()
                    if clean_p not in pair_map:
                        pair_map[clean_p] = []
                    pair_map[clean_p].append(uid)

            if pair_map:
                logger.info("Scanning %d unique pairs for %d users", len(pair_map), len(users))

            for pair, recipients in pair_map.items():
                if not is_market_open(pair):
                    continue
                if is_news_blackout(pair):
                    continue

                df_l = await fetch_data(pair, "M15")
                df_h = await fetch_data(pair, "1D")

                sig = get_smc_signal(df_l, df_h, pair)

                if sig:
                    current_time = time.time()

                    for uid in recipients:
                        signal_key = f"{uid}_{pair}"
                        last_info = SENT_SIGNALS.get(signal_key)
                        user_conf = get_user(users, uid)
                        cooldown_sec = user_conf['cooldown'] * 60
                        should_send = False

                        if last_info is None:
                            should_send = True
                        elif isinstance(last_info, dict):
                            time_elapsed = (current_time - last_info['time']) > cooldown_sec
                            direction_changed = last_info.get('direction') != sig['act']
                            if time_elapsed or direction_changed:
                                should_send = True
                        else:
                            should_send = True

                        if should_send:
                            mode = user_conf.get("mode", "MARKET")
                            msg = _format_signal_msg(sig, pair, mode)
                            entry_price = sig['limit_e'] if mode == "LIMIT" else sig['market_e']
                            try:
                                await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                                SENT_SIGNALS[signal_key] = {
                                    'price': entry_price,
                                    'time': current_time,
                                    'direction': sig['act']
                                }
                                logger.info("Sent %s %s (%s) to %s", sig['act'], pair, mode, uid)
                            except Forbidden:
                                logger.info("User %s blocked bot, deactivating", uid)
                                deactivate_user(uid)
                            except BadRequest as e:
                                logger.warning("Bad request sending to %s: %s", uid, e)
                            except Exception as e:
                                logger.error("Failed to send signal to %s: %s", uid, e)

            IS_SCANNING = False
            await asyncio.sleep(60)
        except Exception as e:
            logger.error("Scanner loop error: %s", e, exc_info=True)
            IS_SCANNING = False
            await asyncio.sleep(10)


async def post_init(app: Application):
    init_db()
    asyncio.get_event_loop().create_task(scanner_loop(app))


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("mode", mode_command))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()


if __name__ == "__main__":
    main()
