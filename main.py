# =========================
# IMPORTS
# =========================
import os
import json
import asyncio
import requests
from datetime import datetime, time, timedelta
from typing import Dict, Any, Optional, Tuple

import pytz
import pandas as pd
import numpy as np

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

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DEBUG = os.getenv("DEBUG", "0") == "1"

UTC = pytz.UTC
TWELVE_BASE = "https://api.twelvedata.com"

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not TWELVE_API_KEY:
    raise RuntimeError("Missing TWELVE_API_KEY")

# =========================
# STORAGE
# =========================
DATA_FILE = "users.json"

DEFAULT_USER = {
    "enabled": True,

    "pairs_crypto": ["BTC/USD", "ETH/USD", "SOL/USD"],
    "pairs_forex": [
        "EUR/USD", "GBP/USD", "USD/JPY",
        "XAU/USD", "XAG/USD"
    ],

    "session": "both",
    "scan_interval_sec": 60,
    "cooldown_min": 90,
    "fvg_lookback": 80,

    "killzones_utc": {
        "london": ["07:00", "10:00"],
        "ny": ["12:00", "15:00"]
    },

    "pair_config": {
        "BTC/USD": {"rr": 2.3, "fvg_min_pct": 0.08, "near_entry_pct": 0.25, "max_spread_pct": 0.25},
        "ETH/USD": {"rr": 2.1, "fvg_min_pct": 0.10, "near_entry_pct": 0.30, "max_spread_pct": 0.30},
        "SOL/USD": {"rr": 2.4, "fvg_min_pct": 0.14, "near_entry_pct": 0.45, "max_spread_pct": 0.40},

        "EUR/USD": {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.08},
        "GBP/USD": {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.10},
        "USD/JPY": {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.10},

        "XAU/USD": {"rr": 1.8, "fvg_min_pct": 0.04, "near_entry_pct": 0.15, "max_spread_pct": 0.25},
        "XAG/USD": {"rr": 1.6, "fvg_min_pct": 0.05, "near_entry_pct": 0.18, "max_spread_pct": 0.30},
    }
}

RUNTIME_STATE: Dict[str, Dict[str, Any]] = {}
TWELVE_CACHE = {"forex_pairs": set(), "crypto_pairs": set(), "last_refresh": None}

# =========================
# UTIL
# =========================
def log(*args):
    if DEBUG:
        print("[DEBUG]", *args)

def now_utc():
    return datetime.now(UTC)

async def to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

def _load_users():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def _save_users(users):
    with open(DATA_FILE, "w") as f:
        json.dump(users, f, indent=2)

def get_user(users, chat_id):
    if chat_id not in users:
        users[chat_id] = json.loads(json.dumps(DEFAULT_USER))
        _save_users(users)
    RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}})
    return users[chat_id]

# =========================
# TWELVE DATA
# =========================
def twelve_get(path, params):
    params["apikey"] = TWELVE_API_KEY
    r = requests.get(f"{TWELVE_BASE}/{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()

async def fetch_timeseries(symbol, interval, outputsize=250):
    def _fetch():
        data = twelve_get("time_series", {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "UTC"
        })
        rows = [
            [
                pd.to_datetime(v["datetime"], utc=True),
                float(v["open"]), float(v["high"]),
                float(v["low"]), float(v["close"])
            ]
            for v in data["values"]
        ]
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        return df.sort_values("ts")

    return await to_thread(_fetch)

async def twelve_spread_pct(symbol):
    try:
        q = twelve_get("quote", {"symbol": symbol})
        bid = float(q.get("bid") or q.get("bid_price"))
        ask = float(q.get("ask") or q.get("ask_price"))
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 100
    except:
        return None

# =========================
# NEWS FILTER (FIXED TZ)
# =========================
def high_impact_news_block():
    try:
        events = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=5
        ).json()

        now = datetime.utcnow().replace(tzinfo=UTC)

        for e in events:
            if e.get("impact") != "High":
                continue
            if not any(x in (e.get("title") or "").upper() for x in ["CPI", "FOMC", "NFP"]):
                continue

            et = datetime.fromisoformat(e["date"].replace("Z", "")).replace(tzinfo=UTC)
            if abs((et - now).total_seconds()) < 3600:
                return True
    except:
        pass
    return False

# =========================
# SMC LOGIC
# =========================
def find_swings(df, left=2, right=2):
    sh, sl = [], []
    for i in range(left, len(df)-right):
        if df.high.iloc[i] == df.high.iloc[i-left:i+right+1].max():
            sh.append(df.high.iloc[i])
        if df.low.iloc[i] == df.low.iloc[i-left:i+right+1].min():
            sl.append(df.low.iloc[i])
    return sh, sl

def htf_bias_from_structure(df):
    sh, sl = find_swings(df)
    if len(sh) < 2 or len(sl) < 2:
        return None
    if sh[-1] > sh[-2] and sl[-1] > sl[-2]:
        return "BUY"
    if sh[-1] < sh[-2] and sl[-1] < sl[-2]:
        return "SELL"
    return None

def scan_fvg(df, direction, min_gap_pct, lookback):
    best = None
    for i in range(max(2, len(df)-lookback), len(df)):
        c0, c1, c2 = df.iloc[i-2], df.iloc[i-1], df.iloc[i]
        if direction == "BUY" and c0.high < c2.low:
            gap = (c2.low - c0.high) / c1.close * 100
            if gap >= min_gap_pct:
                best = {"mid": (c0.high + c2.low)/2, "gap_pct": gap}
        if direction == "SELL" and c0.low > c2.high:
            gap = (c0.low - c2.high) / c1.close * 100
            if gap >= min_gap_pct:
                best = {"mid": (c0.low + c2.high)/2, "gap_pct": gap}
    return best

def atr(df, period=14):
    tr = pd.concat([
        df.high - df.low,
        (df.high - df.close.shift()).abs(),
        (df.low - df.close.shift()).abs()
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def swing_sl(df, direction):
    a = atr(df)
    if direction == "BUY":
        return df.low.iloc[-14:].min() - a * 0.5
    return df.high.iloc[-14:].max() + a * 0.5

def in_price_proximity(price, entry, pct):
    return abs(price - entry) / price * 100 <= pct

# =========================
# SCANNER
# =========================
async def scan_for_user(app, chat_id, cfg):
    if high_impact_news_block():
        return

    symbols = cfg["pairs_crypto"] + cfg["pairs_forex"]

    for sym in symbols:
        sp = await twelve_spread_pct(sym)
        if sp and sp > cfg["pair_config"][sym]["max_spread_pct"]:
            continue

        htf = await fetch_timeseries(sym, "1h")
        bias = htf_bias_from_structure(htf)
        if not bias:
            continue

        ltf = await fetch_timeseries(sym, "5min")
        fvg = scan_fvg(
            ltf, bias,
            cfg["pair_config"][sym]["fvg_min_pct"],
            cfg["fvg_lookback"]
        )
        if not fvg:
            continue

        price = ltf.close.iloc[-1]
        entry = fvg["mid"]

        if not in_price_proximity(price, entry, cfg["pair_config"][sym]["near_entry_pct"]):
            continue

        sl = swing_sl(ltf, bias)
        risk = abs(entry - sl)
        rr = cfg["pair_config"][sym]["rr"]

        tp1 = entry + risk if bias == "BUY" else entry - risk
        tp2 = entry + risk * rr if bias == "BUY" else entry - risk * rr

        msg = (
            f"📌 *SMC + FVG ALERT*\n\n"
            f"Pair: *{sym}*\n"
            f"Bias: *{bias}*\n"
            f"FVG Gap: *{fvg['gap_pct']:.2f}%*\n"
            f"RR: *1:{rr}*\n\n"
            f"Entry: `{entry:.5f}`\n"
            f"SL: `{sl:.5f}`\n\n"
            f"🎯 TP1 (1R): `{tp1:.5f}`\n"
            f"🎯 TP2 ({rr}R): `{tp2:.5f}`"
        )

        await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(1)

async def background_scanner(app):
    while True:
        users = _load_users()
        for chat_id, cfg in users.items():
            await scan_for_user(app, chat_id, cfg)
        await asyncio.sleep(60)

async def on_startup(app):
    asyncio.create_task(background_scanner(app))

# =========================
# TELEGRAM APP
# =========================
def build_app():
    app = Application.builder().token(BOT_TOKEN).build()
    app.post_init = on_startup
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot is live ✅")))
    return app

if __name__ == "__main__":
    build_app().run_polling(close_loop=False)