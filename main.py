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
API_DELAY = 8.5 # Respects Twelve Data Free Tier limits (8 calls/min)

# Deduplication state to prevent spamming the same candle
SENT_SIGNALS = {}

# =====================
# STRATEGY ENGINES
# =====================

def get_extreme_signal(df_ltf, df_htf, symbol):
    """Normal Mode: 100+ RR Logic using HTF Targets + LTF FVG Sniper"""
    if df_ltf.empty or df_htf.empty or len(df_htf) < 50: return None
    
    last_candle_ts = str(df_ltf['ts'].iloc[-1])
    is_volatile = any(x in symbol for x in ["BTC", "XAU", "XAUT", "ETH"])
    pip_val = 100 if "JPY" in symbol else 10000
    
    # High Timeframe Bias & Extreme Targets (Weekly/Monthly Swing Highs/Lows)
    htf_trend = "BULL" if df_htf['close'].iloc[-1] > df_htf['close'].iloc[-20] else "BEAR"
    target_high = df_htf['high'].iloc[-250:].max() 
    target_low = df_htf['low'].iloc[-250:].min()
    
    # Low Timeframe Sniper Entry (5m FVG)
    c1, c3 = df_ltf.iloc[-3], df_ltf.iloc[-1]
    curr = c3.close
    
    # SL & Break-Even Logic
    sl_buffer = 15 / pip_val if is_volatile else 5 / pip_val
    be_offset = 30 / pip_val 

    # BULLISH Sniper
    if htf_trend == "BULL" and c3.low > c1.high:
        sl = c1.high - sl_buffer
        if target_high > curr: # Sanity check: TP must be above entry
            return {"action": "BUY", "entry": curr, "tp": target_high, "sl": sl, 
                    "be": curr + be_offset, "mode": "Normal (100RR)", "ts": last_candle_ts}

    # BEARISH Sniper
    if htf_trend == "BEAR" and c3.high < c1.low:
        sl = c1.low + sl_buffer
        if target_low < curr: # Sanity check: TP must be below entry
            return {"action": "SELL", "entry": curr, "tp": target_low, "sl": sl, 
                    "be": curr - be_offset, "mode": "Normal (100RR)", "ts": last_candle_ts}
    return None

def get_scalping_signal(df, symbol):
    """5-Minute EMA Scalping System (Ref: Uploaded PDFs)"""
    if df.empty or len(df) < 60: return None
    last_candle_ts = str(df['ts'].iloc[-1])
    
    # Required Indicators: EMA 10, 21, and 50
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    curr, prev = df.iloc[-1], df.iloc[-2]
    pip_val = 100 if "JPY" in symbol else 10000
    midpoint = (curr.ema10 + curr.ema21) / 2 # Midpoint entry rule

    # Trend Determination: EMA 50 slope and Higher Highs/Lower Lows
    is_uptrend = curr.ema50 > prev.ema50 and curr.high > prev.high
    is_downtrend = curr.ema50 < prev.ema50 and curr.low < prev.low

    if is_uptrend and curr.low <= midpoint <= curr.high:
        return {"action": "BUY", "entry": curr.close, "tp": curr.close + (15/pip_val), 
                "sl": curr.close - (5/pip_val), "mode": "Scalp (5m)", "ts": last_candle_ts}
    
    if is_downtrend and curr.low <= midpoint <= curr.high:
        return {"action": "SELL", "entry": curr.close, "tp": curr.close - (15/pip_val), 
                "sl": curr.close + (5/pip_val), "mode": "Scalp (5m)", "ts": last_candle_ts}
    return None

# =====================
# CORE SYSTEM ENGINE
# =====================

async def scanner_loop(app: Application):
    while True:
        users = load_users()
        for chat_id, settings in users.items():
            mode = settings.get("mode", "both")
            for pair in settings.get("pairs", []):
                key = f"{chat_id}_{pair}"
                
                # Normal Mode Scan (High Timeframe Targeting)
                if mode in ["normal", "both"]:
                    await asyncio.sleep(API_DELAY)
                    df_ht = await fetch_data(pair, "1day", 250)
                    await asyncio.sleep(API_DELAY)
                    df_lt = await fetch_data(pair, "5min", 50)
                    sig = get_extreme_signal(df_lt, df_ht, pair)
                    if sig and SENT_SIGNALS.get(f"{key}_n") != sig['ts']:
                        await send_alert(app, chat_id, pair, sig)
                        SENT_SIGNALS[f"{key}_n"] = sig['ts']

                # Scalp Mode Scan (5m EMA Pullback)
                if mode in ["scalp", "both"]:
                    await asyncio.sleep(API_DELAY)
                    df_5m = await fetch_data(pair, "5min", 75)
                    sig = get_scalping_signal(df_5m, pair)
                    if sig and SENT_SIGNALS.get(f"{key}_s") != sig['ts']:
                        await send_alert(app, chat_id, pair, sig)
                        SENT_SIGNALS[f"{key}_s"] = sig['ts']
        await asyncio.sleep(60)

async def send_alert(app, chat_id, pair, sig):
    risk = abs(sig['entry'] - sig['sl'])
    reward = abs(sig['tp'] - sig['entry'])
    rr = reward / risk if risk > 0 else 0
    
    be_text = f"\n🛡 *Move SL to Break-Even at:* `{sig['be']:.5f}`" if 'be' in sig else ""
    
    text = (f"🚨 *{sig['mode']} SIGNAL FOUND*\n\n"
            f"Pair: *{pair}*\nAction: *{sig['action']}*\n"
            f"RR Ratio: *1:{rr:.1f}*\n\n"
            f"Entry: `{sig['entry']:.5f}`\n"
            f"Target: `{sig['tp']:.5f}`\n"
            f"Stop: `{sig['sl']:.5f}`" + be_text)
    try:
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except: pass

# =====================
# UI HANDLERS & STARTUP
# =====================

def load_users():
    if not os.path.exists(DATA_FILE): return {}
    with open(DATA_FILE, 'r') as f: return json.load(f)

def save_users(data):
    with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=4)

async def fetch_data(symbol, interval, outputsize=100):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=12)
        data = r.json()
        if "values" not in data: return pd.DataFrame()
        df = pd.DataFrame(data["values"])
        df[['open','high','low','close']] = df[['open','high','low','close']].apply(pd.to_numeric)
        df['ts'] = pd.to_datetime(df['datetime'])
        return df.iloc[::-1]
    except: return pd.DataFrame()

async def on_startup(app: Application):
    """Initializes the background scanner task correctly"""
    asyncio.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("💹 Trading Bot Active.", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Toggle Mode", callback_data="toggle")]]))))
    app.post_init = on_startup 
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
