import os
import json
import asyncio
import requests
import time as pytime
from datetime import datetime, timedelta
from typing import Dict, Any

import pytz
import pandas as pd

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DEBUG = os.getenv("DEBUG", "0") == "1"

if not BOT_TOKEN or not TWELVE_API_KEY:
    raise RuntimeError("Missing env variables")

UTC = pytz.UTC
TWELVE_BASE = "https://api.twelvedata.com"
DATA_FILE = "users.json"

# Rate limiting: Twelve Data Free tier is approx 8 calls/min. 
# We'll implement a small delay between pair scans.
API_DELAY = 8.0 

# =====================
# DEFAULT USER
# =====================
DEFAULT_USER = {
    "enabled": True,
    "pairs": [],
    "scan_interval": 300, # Increased to 5 mins to save API weight
    "cooldown_min": 60,
}

RUNTIME: Dict[str, Dict[str, Any]] = {}
MARKETS = {"forex": set(), "crypto": set(), "commodities": set(), "indices": set(), "last": None, "loading": False}

# =====================
# UTIL
# =====================
def log(*a):
    if DEBUG: print("[DEBUG]", *a)

def now():
    return datetime.now(UTC)

def load_users():
    try:
        with open(DATA_FILE) as f: return json.load(f)
    except: return {}

def save_users(d):
    with open(DATA_FILE, "w") as f: json.dump(d, f, indent=2)

def get_user(users, chat_id):
    if chat_id not in users:
        users[chat_id] = DEFAULT_USER.copy()
        save_users(users)
    RUNTIME.setdefault(chat_id, {"cooldowns": {}, "awaiting": None})
    return users[chat_id]

async def safe_edit(query, text, markup=None, parse_mode=ParseMode.MARKDOWN):
    try:
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode=parse_mode)
    except Exception:
        await query.message.reply_text(text=text, reply_markup=markup, parse_mode=parse_mode)

# =====================
# TWELVE DATA API
# =====================
def twelve_get(path, params=None, timeout=25):
    params = params or {}
    params["apikey"] = TWELVE_API_KEY
    for attempt in range(3):
        try:
            r = requests.get(f"{TWELVE_BASE}/{path}", params=params, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if "status" in data and data["status"] == "error":
                log("API Error:", data.get("message"))
                return {}
            return data
        except Exception as e:
            pytime.sleep(2 * (attempt + 1))
    return {}

async def refresh_markets(force=False):
    if MARKETS["loading"] or ((not force) and MARKETS["last"] and (now() - MARKETS["last"]) < timedelta(hours=6)):
        return
    MARKETS["loading"] = True
    try:
        def _fetch():
            fx = twelve_get("forex_pairs").get("data", [])
            cr = twelve_get("cryptocurrencies").get("data", [])
            return ({x.get("symbol", "").upper() for x in fx}, {x.get("symbol", "").upper() for x in cr})
        
        fx, cr = await asyncio.to_thread(_fetch)
        MARKETS["forex"], MARKETS["crypto"] = fx, cr
        MARKETS["last"] = now()
    finally:
        MARKETS["loading"] = False

def normalize(sym):
    s = sym.replace("_", "/").upper().strip()
    return s if "/" in s else None

def market_type(sym):
    if sym in MARKETS["crypto"]: return "Crypto"
    if sym in MARKETS["forex"]: return "Forex"
    return None

# =====================
# MSNR STRATEGY LOGIC
# =====================
async def fetch_df(symbol, interval):
    def _f():
        d = twelve_get("time_series", {"symbol": symbol, "interval": interval, "outputsize": 50})
        rows = []
        for v in d.get("values", []):
            rows.append([pd.to_datetime(v["datetime"], utc=True), float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])])
        return pd.DataFrame(rows, columns=["ts","open","high","low","close"]).sort_values("ts") if rows else pd.DataFrame()
    return await asyncio.to_thread(_f)

def calculate_msnr(df):
    """
    M: Momentum (Close vs SMA 20)
    S: Support (Lowest Low of last 15 periods)
    NR: Next Resistance (Highest High of last 15 periods)
    """
    if df.empty or len(df) < 25: return None
    
    df['sma20'] = df['close'].rolling(window=20).mean()
    curr = df.iloc[-1]
    prev_candles = df.iloc[-15:-1]
    
    support = prev_candles['low'].min()
    resistance = prev_candles['high'].max()
    momentum = "BULL" if curr.close > curr.sma20 else "BEAR"
    
    return {
        "momentum": momentum,
        "support": support,
        "resistance": resistance,
        "close": curr.close
    }

# =====================
# TELEGRAM HANDLERS
# =====================
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Pair", callback_data="add"), InlineKeyboardButton("🗑 Remove", callback_data="remove")],
        [InlineKeyboardButton("📊 My Pairs", callback_data="pairs")]
    ])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    get_user(load_users(), chat_id)
    await update.message.reply_text("🤖 *MSNR Strategy Bot Online*\nScanning for Momentum, Support, and Resistance.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())
    asyncio.create_task(refresh_markets())

async def callbacks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    users = load_users()
    chat_id = str(q.message.chat.id)
    user = get_user(users, chat_id)

    if q.data == "add":
        RUNTIME[chat_id]["awaiting"] = "add"
        await safe_edit(q, "Send pair (e.g., `EUR/USD` or `BTC/USD`)")
    elif q.data == "pairs":
        txt = "\n".join(user["pairs"]) or "No pairs"
        await safe_edit(q, f"*Active Scans:*\n{txt}", kb_main())
    elif q.data == "remove":
        btns = [[InlineKeyboardButton(s, callback_data=f"rm:{s}")] for s in user["pairs"]]
        await safe_edit(q, "Select to remove:", InlineKeyboardMarkup(btns))
    elif q.data.startswith("rm:"):
        sym = q.data.split(":")[1]
        if sym in user["pairs"]: user["pairs"].remove(sym); save_users(users)
        await safe_edit(q, f"Removed {sym}", kb_main())

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = load_users()
    chat_id = str(update.effective_chat.id)
    user = get_user(users, chat_id)
    if RUNTIME[chat_id].get("awaiting") != "add": return

    sym = normalize(update.message.text)
    if not sym:
        await update.message.reply_text("Invalid format. Use BASE/QUOTE")
        return

    if sym not in user["pairs"]:
        user["pairs"].append(sym)
        save_users(users)
    
    RUNTIME[chat_id]["awaiting"] = None
    await update.message.reply_text(f"✅ Now scanning {sym}", reply_markup=kb_main())

# =====================
# MAIN SCANNER LOOP
# =====================
async def scanner(app: Application):
    while True:
        users = load_users()
        for chat_id, user in users.items():
            if not user.get("enabled") or not user["pairs"]: continue
            
            for sym in user["pairs"]:
                try:
                    # Rate limit protection
                    await asyncio.sleep(API_DELAY) 
                    
                    df = await fetch_df(sym, "1h")
                    msnr = calculate_msnr(df)
                    if not msnr: continue

                    signal = None
                    # Buy: Price is bullish momentum and sitting at support
                    if msnr["momentum"] == "BULL" and msnr["close"] <= msnr["support"] * 1.001:
                        signal = "BUY (At Support)"
                        tp, sl = msnr["resistance"], msnr["support"] * 0.997
                    
                    # Sell: Price is bearish momentum and hitting resistance
                    elif msnr["momentum"] == "BEAR" and msnr["close"] >= msnr["resistance"] * 0.999:
                        signal = "SELL (At Resistance)"
                        tp, sl = msnr["support"], msnr["resistance"] * 1.003

                    if signal:
                        # Cooldown check
                        last_alert = RUNTIME[chat_id]["cooldowns"].get(sym)
                        if last_alert and (now() - last_alert) < timedelta(minutes=user["cooldown_min"]):
                            continue

                        msg = (
                            f"🔔 *MSNR SIGNAL: {sym}*\n\n"
                            f"Direction: *{signal}*\n"
                            f"Price: `{msnr['close']:.5f}`\n"
                            f"Target (Next R/S): `{tp:.5f}`\n"
                            f"Stop Loss: `{sl:.5f}`\n"
                            f"Momentum: `{msnr['momentum']}`"
                        )
                        await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=ParseMode.MARKDOWN)
                        RUNTIME[chat_id]["cooldowns"][sym] = now()

                except Exception as e:
                    log(f"Error scanning {sym}: {e}")

        await asyncio.sleep(60)

async def post_init(app: Application):
    asyncio.create_task(scanner(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.post_init = post_init
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
