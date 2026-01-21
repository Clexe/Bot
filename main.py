import os, json, asyncio, requests, pytz
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# =====================
# CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DATA_FILE = "users.json"
API_DELAY = 8.5 

# Deduplication state: pair -> last_signal_timestamp
SENT_SIGNALS = {}

# =====================
# STRATEGIES
# =====================

def get_extreme_signal(df_ltf, df_htf, symbol):
    if df_ltf.empty or df_htf.empty or len(df_htf) < 50: return None
    
    # Track which candle we are on to prevent spamming the same candle
    last_candle_time = str(df_ltf['ts'].iloc[-1])
    
    is_volatile = any(x in symbol for x in ["BTC", "XAU", "XAUT", "ETH"])
    is_jpy = "JPY" in symbol
    pip_val = 100 if is_jpy else 10000
    
    htf_trend = "BULL" if df_htf['close'].iloc[-1] > df_htf['close'].iloc[-20] else "BEAR"
    target_high = df_htf['high'].iloc[-250:].max() 
    target_low = df_htf['low'].iloc[-250:].min()
    
    c1, c3 = df_ltf.iloc[-3], df_ltf.iloc[-1]
    curr = c3.close
    sl_buffer = 15 / pip_val if is_volatile else 5 / pip_val

    # BULLISH Sniper
    if htf_trend == "BULL" and c3.low > c1.high and target_high > curr:
        return {"action": "BUY", "entry": curr, "tp": target_high, "sl": c1.high - sl_buffer, 
                "be": curr + (30/pip_val), "mode": "Normal (100RR)", "ts": last_candle_time}

    # BEARISH Sniper
    if htf_trend == "BEAR" and c3.high < c1.low and target_low < curr:
        return {"action": "SELL", "entry": curr, "tp": target_low, "sl": c1.low + sl_buffer, 
                "be": curr - (30/pip_val), "mode": "Normal (100RR)", "ts": last_candle_time}
    return None

def get_scalping_signal(df, symbol):
    if df.empty or len(df) < 60: return None
    last_candle_time = str(df['ts'].iloc[-1])
    
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    curr, prev = df.iloc[-1], df.iloc[-2]
    pip_val = 100 if "JPY" in symbol else 10000
    midpoint = (curr.ema10 + curr.ema21) / 2

    if curr.ema50 > prev.ema50 and curr.low <= midpoint <= curr.high:
        return {"action": "BUY", "entry": curr.close, "tp": curr.close + (15/pip_val), 
                "sl": curr.close - (5/pip_val), "mode": "Scalp (5m)", "ts": last_candle_time}
    
    if curr.ema50 < prev.ema50 and curr.low <= midpoint <= curr.high:
        return {"action": "SELL", "entry": curr.close, "tp": curr.close - (15/pip_val), 
                "sl": curr.close + (5/pip_val), "mode": "Scalp (5m)", "ts": last_candle_time}
    return None

# =====================
# CORE SCANNER (ANTI-SPAM)
# =====================

async def scanner_loop(app: Application):
    while True:
        users = load_users()
        for chat_id, settings in users.items():
            mode = settings.get("mode", "both")
            for pair in settings.get("pairs", []):
                # Unique key for deduplication
                signal_key = f"{chat_id}_{pair}"
                
                # NORMAL MODE
                if mode in ["normal", "both"]:
                    await asyncio.sleep(API_DELAY)
                    df_htf = await fetch_data(pair, "1day", 250)
                    await asyncio.sleep(API_DELAY)
                    df_ltf = await fetch_data(pair, "5min", 50)
                    sig = get_extreme_signal(df_ltf, df_htf, pair)
                    
                    # Deduplication Check
                    if sig and SENT_SIGNALS.get(f"{signal_key}_normal") != sig['ts']:
                        await send_alert(app, chat_id, pair, sig)
                        SENT_SIGNALS[f"{signal_key}_normal"] = sig['ts']

                # SCALP MODE
                if mode in ["scalp", "both"]:
                    await asyncio.sleep(API_DELAY)
                    df_5m = await fetch_data(pair, "5min", 75)
                    sig = get_scalping_signal(df_5m, pair)
                    
                    # Deduplication Check
                    if sig and SENT_SIGNALS.get(f"{signal_key}_scalp") != sig['ts']:
                        await send_alert(app, chat_id, pair, sig)
                        SENT_SIGNALS[f"{signal_key}_scalp"] = sig['ts']
        await asyncio.sleep(60)

# (Helper functions fetch_data, send_alert, load_users remain same)
