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

# API Rate Management
# Free tier: 8 calls/min. We set a baseline delay.
BASE_DELAY = 8.0 

# =====================
# STRATEGY LOGIC
# =====================

def get_msnr_smc_signal(df):
    """Normal Trade: MSNR + SMC + FVG (1H Timeframe)"""
    if len(df) < 30: return None
    df['sma20'] = df['close'].rolling(window=20).mean()
    curr, prev = df.iloc[-1], df.iloc[-15:-1]
    
    support, resistance = prev['low'].min(), prev['high'].max()
    momentum = "BULL" if curr.close > curr.sma20 else "BEAR"
    
    # FVG Detection
    for i in range(len(df)-1, len(df)-6, -1):
        c1, c3 = df.iloc[i-2], df.iloc[i]
        if momentum == "BULL" and c3.low > c1.high:
            return {"action": "BUY", "entry": curr.close, "tp": resistance, "sl": support, "mode": "Normal (MSNR/SMC)"}
        if momentum == "BEAR" and c3.high < c1.low:
            return {"action": "SELL", "entry": curr.close, "tp": support, "sl": resistance, "mode": "Normal (MSNR/SMC)"}
    return None

def get_scalping_signal(df):
    """Scalping Mode: 5m EMA Strategy (From Notebooks)"""
    if len(df) < 50: return None
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    curr = df.iloc[-1]
    
    # Buy: Price > 50 EMA, and pulls back between 10 & 21 EMA
    if curr.close > curr.ema50:
        if curr.low <= curr.ema10 and curr.close >= curr.ema21:
            return {"action": "BUY", "entry": curr.close, "tp": curr.close + (curr.close - curr.ema50), "sl": curr.ema50, "mode": "Scalp (5m EMA)"}
            
    # Sell: Price < 50 EMA, and pulls back between 10 & 21 EMA
    elif curr.close < curr.ema50:
        if curr.high >= curr.ema10 and curr.close <= curr.ema21:
            return {"action": "SELL", "entry": curr.close, "tp": curr.close - (curr.ema50 - curr.close), "sl": curr.ema50, "mode": "Scalp (5m EMA)"}
    return None

# =====================
# CORE ENGINE
# =====================

async def fetch_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=70&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=15)
        data = r.json()
        if "values" not in data: return pd.DataFrame()
        df = pd.DataFrame(data["values"])
        df[['open','high','low','close']] = df[['open','high','low','close']].apply(pd.to_numeric)
        return df.iloc[::-1] # Sort chronologically
    except: return pd.DataFrame()

async def scanner_loop(app: Application):
    while True:
        try:
            with open(DATA_FILE) as f: users = json.load(f)
        except: users = {}

        for chat_id, settings in users.items():
            for pair in settings.get("pairs", []):
                # 1. NORMAL TRADE SCAN (1H)
                await asyncio.sleep(BASE_DELAY)
                df_1h = await fetch_data(pair, "1h")
                signal_n = get_msnr_smc_signal(df_1h)
                if signal_n: await send_alert(app, chat_id, pair, signal_n)

                # 2. SCALPING SCAN (5m)
                await asyncio.sleep(BASE_DELAY)
                df_5m = await fetch_data(pair, "5min")
                signal_s = get_scalping_signal(df_5m)
                if signal_s: await send_alert(app, chat_id, pair, signal_s)
        
        await asyncio.sleep(30)

async def send_alert(app, chat_id, pair, signal):
    msg = (f"🚨 *{signal['mode']} ALERT*\n\n"
           f"Pair: *{pair}*\nAction: *{signal['action']}*\n"
           f"Entry: `{signal['entry']:.5f}`\nTP: `{signal['tp']:.5f}`\nSL: `{signal['sl']:.5f}`")
    await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)

# =====================
# TELEGRAM UI
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    # Initialize user in JSON if not exists
    await update.message.reply_text("💹 *Dual-Strategy Bot Active*\n\nMonitoring Normal (1H) and Scalp (5m) setups.", 
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Pair", callback_data="add")]]), 
                                   parse_mode=ParseMode.MARKDOWN)

async def add_pair_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Standard text logic to add pairs to users.json
    pass # Implementation similar to previous logic

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    # ... add other handlers (callback, message) ...
    app.post_init = lambda a: asyncio.create_task(scanner_loop(a))
    app.run_polling()

if __name__ == "__main__":
    main()
