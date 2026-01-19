# =========================
# IMPORTS
# =========================
import os
import json
import asyncio
import requests
from datetime import datetime, time, timedelta
from typing import Dict, Any

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

if not BOT_TOKEN or not TWELVE_API_KEY:
    raise RuntimeError("Missing TELEGRAM_TOKEN or TWELVE_API_KEY")

UTC = pytz.UTC
TWELVE_BASE = "https://api.twelvedata.com"

# =========================
# STORAGE
# =========================
DATA_FILE = "users.json"

DEFAULT_USER = {
    "enabled": True,
    "session": "both",
    "scan_interval_sec": 60,
    "cooldown_min": 90,
    "fvg_lookback": 80,

    "pairs_crypto": ["BTC/USD", "ETH/USD"],
    "pairs_forex": ["EUR/USD", "XAU/USD", "XAG/USD"],

    "pair_config": {
        "BTC/USD": {"rr": 2.3, "fvg_min_pct": 0.08, "near_entry_pct": 0.25, "max_spread_pct": 0.30},
        "ETH/USD": {"rr": 2.1, "fvg_min_pct": 0.10, "near_entry_pct": 0.30, "max_spread_pct": 0.30},
        "EUR/USD": {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.10},
        "XAU/USD": {"rr": 1.8, "fvg_min_pct": 0.04, "near_entry_pct": 0.15, "max_spread_pct": 0.25},
        "XAG/USD": {"rr": 1.6, "fvg_min_pct": 0.05, "near_entry_pct": 0.18, "max_spread_pct": 0.30},
    }
}

RUNTIME: Dict[str, Dict[str, Any]] = {}

# =========================
# UTIL
# =========================
def log(*a):
    if DEBUG:
        print("[DEBUG]", *a)

def now_utc():
    return datetime.now(UTC)

def load_users():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_users(u):
    with open(DATA_FILE, "w") as f:
        json.dump(u, f, indent=2)

def get_user(users, chat_id):
    if chat_id not in users:
        users[chat_id] = json.loads(json.dumps(DEFAULT_USER))
        save_users(users)
    RUNTIME.setdefault(chat_id, {"cooldowns": {}})
    return users[chat_id]

# =========================
# TWELVE DATA
# =========================
def twelve_get(path, params):
    params["apikey"] = TWELVE_API_KEY
    r = requests.get(f"{TWELVE_BASE}/{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()

async def fetch_df(symbol, tf, limit=250):
    def _f():
        d = twelve_get("time_series", {
            "symbol": symbol,
            "interval": tf,
            "outputsize": limit,
            "timezone": "UTC"
        })
        rows = [[
            pd.to_datetime(v["datetime"], utc=True),
            float(v["open"]), float(v["high"]),
            float(v["low"]), float(v["close"])
        ] for v in d["values"]]
        df = pd.DataFrame(rows, columns=["ts","open","high","low","close"])
        return df.sort_values("ts")
    return await asyncio.to_thread(_f)

async def spread_pct(symbol):
    try:
        q = twelve_get("quote", {"symbol": symbol})
        bid, ask = float(q["bid"]), float(q["ask"])
        return (ask - bid) / ((ask + bid) / 2) * 100
    except:
        return None

# =========================
# NEWS FILTER
# =========================
def high_impact_news():
    try:
        ev = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=5
        ).json()
        now = datetime.utcnow().replace(tzinfo=UTC)
        for e in ev:
            if e.get("impact") != "High":
                continue
            if not any(x in (e.get("title") or "") for x in ["CPI","FOMC","NFP"]):
                continue
            t = datetime.fromisoformat(e["date"].replace("Z","")).replace(tzinfo=UTC)
            if abs((t-now).total_seconds()) < 3600:
                return True
    except:
        pass
    return False

# =========================
# SMC + FVG
# =========================
def find_swings(df):
    sh, sl = [], []
    for i in range(2, len(df)-2):
        if df.high.iloc[i] == df.high.iloc[i-2:i+3].max():
            sh.append(df.high.iloc[i])
        if df.low.iloc[i] == df.low.iloc[i-2:i+3].min():
            sl.append(df.low.iloc[i])
    return sh, sl

def htf_bias(df):
    sh, sl = find_swings(df)
    if len(sh)<2 or len(sl)<2: return None
    if sh[-1]>sh[-2] and sl[-1]>sl[-2]: return "BUY"
    if sh[-1]<sh[-2] and sl[-1]<sl[-2]: return "SELL"
    return None

def scan_fvg(df, dir, min_pct, lookback):
    best=None
    for i in range(max(2,len(df)-lookback),len(df)):
        a,b,c=df.iloc[i-2],df.iloc[i-1],df.iloc[i]
        if dir=="BUY" and a.high<c.low:
            g=(c.low-a.high)/b.close*100
            if g>=min_pct: best={"mid":(a.high+c.low)/2,"gap":g}
        if dir=="SELL" and a.low>c.high:
            g=(a.low-c.high)/b.close*100
            if g>=min_pct: best={"mid":(a.low+c.high)/2,"gap":g}
    return best

def atr(df):
    tr = pd.concat([
        df.high-df.low,
        (df.high-df.close.shift()).abs(),
        (df.low-df.close.shift()).abs()
    ],axis=1).max(axis=1)
    return tr.rolling(14).mean().iloc[-1]

def swing_sl(df, dir):
    a=atr(df)
    return df.low.iloc[-14:].min()-a*0.5 if dir=="BUY" else df.high.iloc[-14:].max()+a*0.5

# =========================
# MENUS
# =========================
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Pairs",callback_data="pairs"),
         InlineKeyboardButton("⚙️ Settings",callback_data="settings")],
        [InlineKeyboardButton("ℹ️ Help",callback_data="help")]
    ])

# =========================
# HANDLERS
# =========================
async def cmd_start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    users=load_users()
    cfg=get_user(users,str(update.effective_chat.id))
    await update.message.reply_text(
        "🟢 *SMC Scanner Active*\n\nAlerts only.\nLondon + NY.\n",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_main()
    )

async def menu(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    if q.data=="pairs":
        return await q.edit_message_text("📊 Your markets are active.",reply_markup=kb_main())
    if q.data=="settings":
        return await q.edit_message_text("⚙️ Settings are auto-managed.",reply_markup=kb_main())
    if q.data=="help":
        return await q.edit_message_text("ℹ️ SMC + FVG scanner.\nAlerts only.",reply_markup=kb_main())

# =========================
# SCANNER
# =========================
async def scan_user(app,chat_id,cfg):
    if high_impact_news(): return
    for sym in cfg["pairs_crypto"]+cfg["pairs_forex"]:
        sp=await spread_pct(sym)
        if sp and sp>cfg["pair_config"][sym]["max_spread_pct"]:
            continue

        htf=await fetch_df(sym,"1h")
        bias=htf_bias(htf)
        if not bias: continue

        ltf=await fetch_df(sym,"5min")
        fvg=scan_fvg(ltf,bias,cfg["pair_config"][sym]["fvg_min_pct"],cfg["fvg_lookback"])
        if not fvg: continue

        entry=fvg["mid"]
        sl=swing_sl(ltf,bias)
        risk=abs(entry-sl)
        rr=cfg["pair_config"][sym]["rr"]

        tp1=entry+risk if bias=="BUY" else entry-risk
        tp2=entry+risk*rr if bias=="BUY" else entry-risk*rr

        msg=(
            f"📌 *SMC + FVG ALERT*\n\n"
            f"*{sym}* | {bias}\n\n"
            f"Entry: `{entry:.5f}`\n"
            f"SL: `{sl:.5f}`\n"
            f"TP1: `{tp1:.5f}`\n"
            f"TP2: `{tp2:.5f}`"
        )
        await app.bot.send_message(chat_id=int(chat_id),text=msg,parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(1)

async def scanner(app):
    while True:
        users=load_users()
        for cid,cfg in users.items():
            if cfg.get("enabled"):
                await scan_user(app,cid,cfg)
        await asyncio.sleep(60)

async def on_startup(app):
    asyncio.create_task(scanner(app))

# =========================
# APP
# =========================
def build():
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CallbackQueryHandler(menu))
    app.post_init=on_startup
    return app

if __name__=="__main__":
    build().run_polling(close_loop=False)