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

# Twelve Data Free tier: 8 calls/min. 
# Each pair now takes 2 calls (1H and 5m). 
# We wait 8 seconds between every request to avoid "429 Too Many Requests"
API_DELAY = 8.5 

if not BOT_TOKEN or not TWELVE_API_KEY:
    print("❌ FATAL: Missing Environment Variables (TELEGRAM_TOKEN or TWELVE_API_KEY)")

# =====================
# DATA STORAGE
# =====================
def load_users():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_users(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# =====================
# STRATEGIES
# =====================

def get_msnr_smc_signal(df):
    """Normal Mode: MSNR + SMC + FVG (1H Timeframe)"""
    if df.empty or len(df) < 30: return None
    df['sma20'] = df['close'].rolling(window=20).mean()
    curr = df.iloc[-1]
    prev_window = df.iloc[-15:-1]
    
    support, resistance = prev_window['low'].min(), prev_window['high'].max()
    momentum = "BULL" if curr.close > curr.sma20 else "BEAR"
    
    # Simple FVG Check
    c1, c3 = df.iloc[-3], df.iloc[-1]
    if momentum == "BULL" and c3.low > c1.high:
        return {"action": "BUY", "entry": curr.close, "tp": resistance, "sl": support, "mode": "Normal (1H)"}
    if momentum == "BEAR" and c3.high < c1.low:
        return {"action": "SELL", "entry": curr.close, "tp": support, "sl": resistance, "mode": "Normal (1H)"}
    return None

def get_scalping_signal(df):
    """Scalp Mode: 5m EMA Scalping (From PDF Sources)"""
    if df.empty or len(df) < 55: return None
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    curr, prev = df.iloc[-1], df.iloc[-2]
    
    # Trend based on EMA 50 slope
    is_uptrend = curr.ema50 > prev.ema50
    is_downtrend = curr.ema50 < prev.ema50
    
    # Entry: Price pulls back to the EMA 10/21 'Value Zone'
    # Based on PDF: Low touches zone in uptrend, or High touches zone in downtrend
    if is_uptrend and curr.low <= curr.ema10 and curr.close > curr.ema21:
        return {"action": "BUY", "entry": curr.close, "tp": curr.close + (curr.close - curr.ema50), "sl": curr.ema50, "mode": "Scalp (5m)"}
    
    if is_downtrend and curr.high >= curr.ema10 and curr.close < curr.ema21:
        return {"action": "SELL", "entry": curr.close, "tp": curr.close - (curr.ema50 - curr.close), "sl": curr.ema50, "mode": "Scalp (5m)"}
    
    return None

# =====================
# ENGINE
# =====================

async def fetch_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=70&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=12)
        data = r.json()
        if "values" not in data: 
            print(f"⚠️ API Error for {symbol}: {data.get('message', 'Unknown Error')}")
            return pd.DataFrame()
        df = pd.DataFrame(data["values"])
        df[['open','high','low','close']] = df[['open','high','low','close']].apply(pd.to_numeric)
        return df.iloc[::-1] # Newest last
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return pd.DataFrame()

async def scanner_loop(app: Application):
    print("🚀 Scanner Loop Started...")
    while True:
        users = load_users()
        if not users:
            await asyncio.sleep(10)
            continue

        for chat_id, settings in users.items():
            mode = settings.get("mode", "both")
            for pair in settings.get("pairs", []):
                print(f"🔍 Scanning {pair} for User {chat_id} (Mode: {mode})")
                
                # NORMAL SCAN
                if mode in ["normal", "both"]:
                    df_1h = await fetch_data(pair, "1h")
                    sig = get_msnr_smc_signal(df_1h)
                    if sig: await send_alert(app, chat_id, pair, sig)
                    await asyncio.sleep(API_DELAY) # Avoid API ban

                # SCALP SCAN
                if mode in ["scalp", "both"]:
                    df_5m = await fetch_data(pair, "5min")
                    sig = get_scalping_signal(df_5m)
                    if sig: await send_alert(app, chat_id, pair, sig)
                    await asyncio.sleep(API_DELAY) # Avoid API ban
        
        await asyncio.sleep(30)

async def send_alert(app, chat_id, pair, sig):
    text = (f"🚨 *{sig['mode']} SIGNAL*\n\n"
            f"Symbol: *{pair}*\n"
            f"Action: *{sig['action']}*\n\n"
            f"Entry: `{sig['entry']:.5f}`\n"
            f"Target: `{sig['tp']:.5f}`\n"
            f"Stop: `{sig['sl']:.5f}`")
    try:
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"❌ Failed to send alert: {e}")

# =====================
# INTERFACE
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    users = load_users()
    if chat_id not in users:
        users[chat_id] = {"pairs": [], "mode": "both"}
        save_users(users)
    
    kb = [
        [InlineKeyboardButton("➕ Add Pair", callback_data="add"), InlineKeyboardButton("🔄 Toggle Mode", callback_data="toggle")],
        [InlineKeyboardButton("📊 My Watchlist", callback_data="list")]
    ]
    await update.message.reply_text(
        "💹 *Dual-Strategy Bot Online*\n\n- *Normal*: MSNR + SMC + FVG (1H)\n- *Scalp*: 10/21/50 EMA (5m)", 
        reply_markup=InlineKeyboardMarkup(kb), 
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    users = load_users()

    if query.data == "toggle":
        curr = users[chat_id].get("mode", "both")
        next_mode = {"both": "normal", "normal": "scalp", "scalp": "both"}[curr]
        users[chat_id]["mode"] = next_mode
        save_users(users)
        await query.edit_message_text(f"✅ Mode set to: *{next_mode.upper()}*", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu")]]))

    elif query.data == "add":
        context.user_data["state"] = "adding"
        await query.message.reply_text("Please send the pair symbol (e.g., `EUR/USD`)")

    elif query.data == "list":
        pairs = "\n".join(users[chat_id]["pairs"]) or "No pairs added."
        await query.edit_message_text(f"📍 *Your Watchlist:*\n{pairs}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu")]]))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if context.user_data.get("state") == "adding":
        symbol = update.message.text.upper().strip()
        users = load_users()
        if symbol not in users[chat_id]["pairs"]:
            users[chat_id]["pairs"].append(symbol)
            save_users(users)
        context.user_data["state"] = None
        await update.message.reply_text(f"✅ Added {symbol} to monitoring.")

# =====================
# STARTUP FIX
# =====================
async def on_startup(app: Application):
    """Starts the scanner task as soon as the bot is ready"""
    print("🤖 Bot is starting up...")
    asyncio.create_task(scanner_loop(app))

def main():
    print("🛠 Initializing Application...")
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Correct way to start background tasks in PTB 20+
    app.post_init = on_startup 
    
    print("📡 Starting Polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
