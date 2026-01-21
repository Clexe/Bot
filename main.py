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
    raise RuntimeError("Missing env variables. Ensure TELEGRAM_TOKEN and TWELVE_API_KEY are set.")

UTC = pytz.UTC
TWELVE_BASE = "https://api.twelvedata.com"
DATA_FILE = "users.json"

# RATE LIMITING: Twelve Data free tier is ~8 calls/min. 
# We wait 8 seconds between pairs to stay safe.
API_DELAY = 8.0 

# =====================
# DEFAULT USER & STATE
# =====================
DEFAULT_USER = {
    "enabled": True,
    "pairs": [],
    "scan_interval": 300, 
    "cooldown_min": 60,
}

RUNTIME: Dict[str, Dict[str, Any]] = {}
MARKETS = {"forex": set(), "crypto": set(), "last": None, "loading": False}

# =====================
# UTILS & DATA PERSISTENCE
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
        try: await query.message.reply_text(text=text, reply_markup=markup, parse_mode=parse_mode)
        except: pass

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
            if data.get("status") == "error": return {}
            return data
        except Exception as e:
            pytime.sleep(2 * (attempt + 1))
    return {}

async def refresh_markets():
    if MARKETS["loading"]: return
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

# =====================
# UNIFIED STRATEGY (MSNR + SMC + FVG)
# =====================
async def fetch_df(symbol, interval):
    def _f():
        d = twelve_get("time_series", {"symbol": symbol, "interval": interval, "outputsize": 50})
        rows = []
        for v in d.get("values", []):
            rows.append([pd.to_datetime(v["datetime"], utc=True), float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])])
        return pd.DataFrame(rows, columns=["ts","open","high","low","close"]).sort_values("ts") if rows else pd.DataFrame()
    return await asyncio.to_thread(_f)

def get_combined_signal(df):
    if df.empty or len(df) < 30: return None
    
    # 1. MSNR: Momentum (SMA20) & Resistance/Support (Lookback 15)
    df['sma20'] = df['close'].rolling(window=20).mean()
    curr = df.iloc[-1]
    prev_window = df.iloc[-15:-1]
    
    support = prev_window['low'].min()
    resistance = prev_window['high'].max()
    momentum = "BULL" if curr.close > curr.sma20 else "BEAR"
    
    # 2. SMC/FVG: Find most recent Imbalance
    fvg_price = None
    direction = None
    
    # Check last 5 candles for an FVG
    for i in range(len(df)-1, len(df)-6, -1):
        c1, c2, c3 = df.iloc[i-2], df.iloc[i-1], df.iloc[i]
        if c3.low > c1.high: # Bullish FVG
            fvg_price = (c3.low + c1.high) / 2
            direction = "BUY"
            break
        elif c3.high < c1.low: # Bearish FVG
            fvg_price = (c3.high + c1.low) / 2
            direction = "SELL"
            break

    # 3. Filter: Signal must align with MSNR Momentum
    if direction == "BUY" and momentum == "BULL":
        return {"action": "BUY", "entry": fvg_price, "sl": support, "tp": resistance, "type": "SMC+MSNR"}
    if direction == "SELL" and momentum == "BEAR":
        return {"action": "SELL", "entry": fvg_price, "sl": resistance, "tp": support, "type": "SMC+MSNR"}
    
    return None

# =====================
# BOT HANDLERS
# =====================
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Pair", callback_data="add"), InlineKeyboardButton("🗑 Remove", callback_data="remove")],
        [InlineKeyboardButton("📊 Active Scans", callback_data="list")]
    ])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    get_user(load_users(), chat_id)
    await update.message.reply_text("🚀 *Unified MSNR & SMC Scanner*\nStatus: Monitoring Markets...", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())
    asyncio.create_task(refresh_markets())

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    users = load_users(); chat_id = str(q.message.chat.id); user = get_user(users, chat_id)

    if q.data == "add":
        RUNTIME[chat_id]["awaiting"] = "add"
        await safe_edit(q, "Send Pair (e.g. `EUR/USD`, `XAU/USD`, `BTC/USD`)")
    elif q.data == "list":
        txt = "\n".join(user["pairs"]) or "No pairs added."
        await safe_edit(q, f"*Your Watchlist:*\n{txt}", kb_main())
    elif q.data == "remove":
        btns = [[InlineKeyboardButton(s, callback_data=f"rm:{s}")] for s in user["pairs"]]
        await safe_edit(q, "Select pair to remove:", InlineKeyboardMarkup(btns))
    elif q.data.startswith("rm:"):
        sym = q.data.split(":")[1]
        if sym in user["pairs"]: user["pairs"].remove(sym); save_users(users)
        await safe_edit(q, f"✅ Removed {sym}", kb_main())

async def text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = load_users(); chat_id = str(update.effective_chat.id); user = get_user(users, chat_id)
    if RUNTIME[chat_id].get("awaiting") != "add": return
    
    sym = update.message.text.upper().replace("_", "/").strip()
    if "/" not in sym:
        await update.message.reply_text("Invalid format. Use `EUR/USD` style.")
        return

    if sym not in user["pairs"]:
        user["pairs"].append(sym)
        save_users(users)
    RUNTIME[chat_id]["awaiting"] = None
    await update.message.reply_text(f"✅ Added {sym} to scanner.", reply_markup=kb_main())

# =====================
# SCANNER TASK
# =====================
async def scanner_loop(app: Application):
    while True:
        users = load_users()
        for chat_id, user in users.items():
            if not user.get("enabled") or not user["pairs"]: continue
            
            for sym in user["pairs"]:
                try:
                    await asyncio.sleep(API_DELAY) # Global Rate Limit
                    
                    df = await fetch_df(sym, "1h")
                    signal = get_combined_signal(df)
                    
                    if signal:
                        # Cooldown logic
                        last_ts = RUNTIME[chat_id]["cooldowns"].get(sym)
                        if last_ts and (now() - last_ts) < timedelta(minutes=user["cooldown_min"]):
                            continue

                        msg = (
                            f"🎯 *NEW {signal['type']} SIGNAL*\n\n"
                            f"Pair: *{sym}*\n"
                            f"Action: *{signal['action']}*\n"
                            f"Entry Zone: `{signal['entry']:.5f}`\n"
                            f"TP (Next Target): `{signal['tp']:.5f}`\n"
                            f"SL (Invalidation): `{signal['sl']:.5f}`\n\n"
                            f"💡 _Strategy: MSNR Momentum + SMC Imbalance._"
                        )
                        await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=ParseMode.MARKDOWN)
                        RUNTIME[chat_id]["cooldowns"][sym] = now()

                except Exception as e:
                    log(f"Scan Error {sym}: {e}")
        
        await asyncio.sleep(60)

# =====================
# RUN
# =====================
async def on_startup(app: Application):
    asyncio.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.post_init = on_startup
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
