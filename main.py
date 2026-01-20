
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
# ENV
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DEBUG = os.getenv("DEBUG", "0") == "1"

if not BOT_TOKEN or not TWELVE_API_KEY:
    raise RuntimeError("Missing env variables")

UTC = pytz.UTC
TWELVE_BASE = "https://api.twelvedata.com"
DATA_FILE = "users.json"

# =====================
# DEFAULT USER
# =====================
DEFAULT_USER = {
    "enabled": True,
    "pairs": [],
    "scan_interval": 60,
    "cooldown_min": 90,
    "session": "both",
}

# =====================
# RUNTIME STATE
# =====================
RUNTIME: Dict[str, Dict[str, Any]] = {}

# =====================
# MARKET CACHE
# =====================
MARKETS = {
    "forex": set(),
    "crypto": set(),
    "commodities": set(),
    "indices": set(),
    "last": None,
    "loading": False,
}

# =====================
# UTIL
# =====================
def log(*a):
    if DEBUG:
        print("[DEBUG]", *a)

def now():
    return datetime.now(UTC)

def load_users():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_users(d):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)

def get_user(users, chat_id):
    if chat_id not in users:
        users[chat_id] = DEFAULT_USER.copy()
        save_users(users)
    RUNTIME.setdefault(chat_id, {"cooldowns": {}, "awaiting": None})
    return users[chat_id]

async def safe_edit(query, text, markup=None, parse_mode=ParseMode.MARKDOWN):
    """
    Prevents Telegram errors like:
    - Message is not modified
    - Message to edit not found
    """
    try:
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode=parse_mode)
    except Exception as e:
        # fallback to sending a new message instead of dying
        log("safe_edit fallback:", e)
        try:
            await query.message.reply_text(text=text, reply_markup=markup, parse_mode=parse_mode)
        except Exception as e2:
            log("safe_edit second failure:", e2)

# =====================
# TWELVE DATA
# =====================
def twelve_get(path, params=None, timeout=25):
    """
    Retry + backoff so TwelveData timeouts don't kill the bot.
    """
    params = params or {}
    params["apikey"] = TWELVE_API_KEY

    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(f"{TWELVE_BASE}/{path}", params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            pytime.sleep(1.5 * (attempt + 1))
    raise last_err

async def refresh_markets(force=False):
    """
    Cached for 6 hours.
    Commodities + indices are optional (often slow).
    NEVER throws — it logs and returns.
    """
    if MARKETS["loading"]:
        return
    if (not force) and MARKETS["last"] and (now() - MARKETS["last"]) < timedelta(hours=6):
        return

    MARKETS["loading"] = True

    def _fetch():
        fx = twelve_get("forex_pairs").get("data", [])
        cr = twelve_get("cryptocurrencies").get("data", [])

        # optional endpoints
        try:
            cm = twelve_get("commodities").get("data", [])
        except Exception:
            cm = []
        try:
            ix = twelve_get("indices").get("data", [])
        except Exception:
            ix = []

        return (
            {x.get("symbol", "").upper() for x in fx if x.get("symbol")},
            {x.get("symbol", "").upper() for x in cr if x.get("symbol")},
            {x.get("symbol", "").upper() for x in cm if x.get("symbol")},
            {x.get("symbol", "").upper() for x in ix if x.get("symbol")},
        )

    try:
        fx, cr, cm, ix = await asyncio.to_thread(_fetch)
        MARKETS["forex"] = fx
        MARKETS["crypto"] = cr
        MARKETS["commodities"] = cm
        MARKETS["indices"] = ix
        MARKETS["last"] = now()
        log("Markets refreshed:", len(fx), len(cr), len(cm), len(ix))
    except Exception as e:
        log("refresh_markets failed:", e)
    finally:
        MARKETS["loading"] = False

def normalize(sym):
    s = sym.replace("_", "/").upper().strip()
    if "/" in s and len(s.split("/")) == 2:
        return s
    return None

def market_type(sym):
    if sym in MARKETS["crypto"]: return "Crypto"
    if sym in MARKETS["forex"]: return "Forex"
    if sym in MARKETS["commodities"]: return "Commodity"
    if sym in MARKETS["indices"]: return "Index"
    return None

# =====================
# STRATEGY
# =====================
async def fetch_df(symbol, interval):
    def _f():
        d = twelve_get("time_series", {
            "symbol": symbol,
            "interval": interval,
            "outputsize": 200,
            "timezone": "UTC"
        })
        rows = []
        for v in d.get("values", []):
            rows.append([
                pd.to_datetime(v["datetime"], utc=True),
                float(v["open"]),
                float(v["high"]),
                float(v["low"]),
                float(v["close"]),
            ])
        df = pd.DataFrame(rows, columns=["ts","open","high","low","close"])
        return df.sort_values("ts")

    return await asyncio.to_thread(_f)

def fvg(df, direction):
    for i in range(2, len(df)):
        a, b, c = df.iloc[i-2], df.iloc[i-1], df.iloc[i]
        if direction == "BUY" and a.high < c.low:
            return (a.high + c.low) / 2
        if direction == "SELL" and a.low > c.high:
            return (a.low + c.high) / 2
    return None

# =====================
# MENUS
# =====================
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Pair", callback_data="add"),
         InlineKeyboardButton("🗑 Remove Pair", callback_data="remove")],
        [InlineKeyboardButton("📊 My Pairs", callback_data="pairs"),
         InlineKeyboardButton("ℹ Help", callback_data="help")]
    ])

# =====================
# HANDLERS
# =====================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = load_users()
    chat_id = str(update.effective_chat.id)
    get_user(users, chat_id)

    # Respond immediately so /start never dies on TwelveData
    await update.message.reply_text(
        "🟢 *SMC Scanner Online*\n\nUse menu below.\n(If markets are still loading, give it ~10s.)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main()
    )

    # Load markets in background (prevents timeouts killing UI)
    asyncio.create_task(refresh_markets(force=False))

async def callbacks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer(cache_time=1)

    users = load_users()
    chat_id = str(q.message.chat.id)
    user = get_user(users, chat_id)

    if q.data == "add":
        RUNTIME[chat_id]["awaiting"] = "add"
        return await safe_edit(
            q,
            "Send symbol (e.g. `XAU/USD`, `BTC/USD`, `EUR/USD`)",
            markup=None,
            parse_mode=ParseMode.MARKDOWN
        )

    if q.data == "remove":
        buttons = [
            [InlineKeyboardButton(sym, callback_data=f"rm:{sym}")]
            for sym in user["pairs"]
        ] or [[InlineKeyboardButton("None", callback_data="noop")]]
        return await safe_edit(q, "Select pair to remove:", InlineKeyboardMarkup(buttons), parse_mode=None)

    if q.data.startswith("rm:"):
        sym = q.data.split("rm:")[1]
        if sym in user["pairs"]:
            user["pairs"].remove(sym)
            save_users(users)
        return await safe_edit(q, f"Removed {sym}", kb_main(), parse_mode=None)

    if q.data == "pairs":
        txt = "\n".join(user["pairs"]) or "No pairs added"
        return await safe_edit(q, f"*Your Pairs:*\n{txt}", kb_main(), parse_mode=ParseMode.MARKDOWN)

    if q.data == "help":
        return await safe_edit(
            q,
            "This bot scans Twelve Data markets using SMC + FVG.\n\nAdd pairs and wait for alerts.",
            kb_main(),
            parse_mode=None
        )

    if q.data == "noop":
        return

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = load_users()
    chat_id = str(update.effective_chat.id)
    user = get_user(users, chat_id)

    if RUNTIME[chat_id]["awaiting"] != "add":
        return

    sym = normalize(update.message.text)
    if not sym:
        return await update.message.reply_text("Invalid format. Example: XAU/USD", reply_markup=kb_main())

    # ensure markets exist; don't block forever
    await refresh_markets(force=False)

    if not market_type(sym):
        return await update.message.reply_text(
            "Pair not supported (or markets still loading). Try /start again in 10s.",
            reply_markup=kb_main()
        )

    if sym not in user["pairs"]:
        user["pairs"].append(sym)
        save_users(users)

    RUNTIME[chat_id]["awaiting"] = None
    await update.message.reply_text(f"✅ Added {sym}", reply_markup=kb_main())

# =====================
# SCANNER
# =====================
async def scanner(app: Application):
    while True:
        users = load_users()

        # keep markets warm in background
        asyncio.create_task(refresh_markets(force=False))

        for chat_id, user in users.items():
            for sym in user["pairs"]:
                try:
                    htf = await fetch_df(sym, "1h")
                    if len(htf) < 3:
                        continue

                    bias = "BUY" if htf.close.iloc[-1] > htf.close.iloc[-2] else "SELL"

                    ltf = await fetch_df(sym, "5min")
                    if len(ltf) < 5:
                        continue

                    entry = fvg(ltf, bias)
                    if not entry:
                        continue

                    sl = ltf.low.min() if bias == "BUY" else ltf.high.max()
                    risk = max(abs(entry - sl), abs(ltf.close.iloc[-1] * 0.002))
                    tp = entry + risk * 2 if bias == "BUY" else entry - risk * 2

                    msg = (
                        f"📌 *SMC + FVG ALERT*\n\n"
                        f"Pair: *{sym}*\n"
                        f"Bias: *{bias}*\n"
                        f"Entry: `{entry:.4f}`\n"
                        f"SL: `{sl:.4f}`\n"
                        f"TP: `{tp:.4f}`"
                    )
                    await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=ParseMode.MARKDOWN)
                    await asyncio.sleep(0.7)

                except Exception as e:
                    log("scan error:", sym, e)

        await asyncio.sleep(60)

async def startup(app: Application):
    asyncio.create_task(scanner(app))

# =====================
# RUN
# =====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.post_init = startup

    # IMPORTANT: helps on redeploys/restarts
    app.run_polling(drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()