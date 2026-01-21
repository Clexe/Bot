import os, json, asyncio, requests, pytz
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DATA_FILE = "users.json"
UTC = pytz.UTC

# Rate limit management for Twelve Data Free tier (8 calls/min)
BASE_DELAY = 8.0 

# =====================
# STRATEGY LOGIC
# =====================

def get_msnr_smc_signal(df):
    """Normal Mode: MSNR + SMC + FVG (1H Timeframe)"""
    if len(df) < 30: return None
    df['sma20'] = df['close'].rolling(window=20).mean()
    curr, prev = df.iloc[-1], df.iloc[-15:-1]
    
    support, resistance = prev['low'].min(), prev['high'].max()
    momentum = "BULL" if curr.close > curr.sma20 else "BEAR"
    
    # FVG Detection
    for i in range(len(df)-1, len(df)-6, -1):
        c1, c3 = df.iloc[i-2], df.iloc[i]
        if momentum == "BULL" and c3.low > c1.high:
            return {"action": "BUY", "entry": curr.close, "tp": resistance, "sl": support, "mode": "Normal"}
        if momentum == "BEAR" and c3.high < c1.low:
            return {"action": "SELL", "entry": curr.close, "tp": support, "sl": resistance, "mode": "Normal"}
    return None

def get_scalping_signal(df):
    """Scalp Mode: 5m EMA Scalping System (Ref: PDF Source)"""
    if len(df) < 60: return None
    # Calculate EMAs per system requirements
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    curr, prev = df.iloc[-1], df.iloc[-2]
    
    # Trend Determination: Higher Highs/Lower Lows and EMA 50 Slope
    is_uptrend = curr.ema50 > prev.ema50 and curr.high > prev.high
    is_downtrend = curr.ema50 < prev.ema50 and curr.low < prev.low
    
    # Entry: Midway between EMA 10 and EMA 21
    midpoint = (curr.ema10 + curr.ema21) / 2

    if is_uptrend and curr.low <= midpoint <= curr.high:
        return {"action": "BUY", "entry": curr.close, "tp": curr.close + 0.0010, "sl": curr.close - 0.0005, "mode": "Scalp"}
    
    if is_downtrend and curr.low <= midpoint <= curr.high:
        return {"action": "SELL", "entry": curr.close, "tp": curr.close - 0.0010, "sl": curr.close + 0.0005, "mode": "Scalp"}
    
    return None

# =====================
# CORE ENGINE & HANDLERS
# =====================

def load_users():
    try:
        with open(DATA_FILE) as f: return json.load(f)
    except: return {}

def save_users(data):
    with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=4)

async def fetch_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=75&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=10)
        data = r.json()
        if "values" not in data: return pd.DataFrame()
        df = pd.DataFrame(data["values"])
        df[['open','high','low','close']] = df[['open','high','low','close']].apply(pd.to_numeric)
        return df.iloc[::-1]
    except: return pd.DataFrame()

async def scanner_loop(app: Application):
    while True:
        users = load_users()
        for chat_id, settings in users.items():
            mode = settings.get("mode", "both")
            for pair in settings.get("pairs", []):
                # Normal Mode Scan (1H)
                if mode in ["normal", "both"]:
                    await asyncio.sleep(BASE_DELAY)
                    df_1h = await fetch_data(pair, "1h")
                    sig = get_msnr_smc_signal(df_1h)
                    if sig: await send_alert(app, chat_id, pair, sig)

                # Scalp Mode Scan (5m)
                if mode in ["scalp", "both"]:
                    await asyncio.sleep(BASE_DELAY)
                    df_5m = await fetch_data(pair, "5min")
                    sig = get_scalping_signal(df_5m)
                    if sig: await send_alert(app, chat_id, pair, sig)
        await asyncio.sleep(30)

async def send_alert(app, chat_id, pair, sig):
    text = (f"🚨 *{sig['mode']} ALERT*\n\nPair: *{pair}*\nAction: *{sig['action']}*\n"
            f"Entry: `{sig['entry']:.5f}`\nTP: `{sig['tp']:.5f}`\nSL: `{sig['sl']:.5f}`")
    await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)

# =====================
# INTERFACE & STARTUP
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    users = load_users()
    if chat_id not in users:
        users[chat_id] = {"pairs": [], "mode": "both"}
        save_users(users)
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Pair", callback_data="add"), InlineKeyboardButton("⚙️ Toggle Mode", callback_data="toggle")],
        [InlineKeyboardButton("📊 My Pairs", callback_data="list")]
    ]
    await update.message.reply_text("💹 *Dual-Strategy Scanner Online*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    users = load_users()

    if query.data == "toggle":
        current = users[chat_id].get("mode", "both")
        modes = {"both": "normal", "normal": "scalp", "scalp": "both"}
        users[chat_id]["mode"] = modes[current]
        save_users(users)
