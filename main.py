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

# State for deduplication & live runtime
SENT_SIGNALS = {} 
RUNTIME_STATE = {} # Tracks user session state (e.g., waiting for spread input)

# =====================
# DATA HELPERS
# =====================
def load_users():
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_users(data):
    with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=4)

def get_user(users, chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "pairs": [], "mode": "both", "session": "both", 
            "scan_interval": 60, "cooldown": 60, "max_spread": 0.0005
        }
        save_users(users)
    return users[chat_id]

# =====================
# STRATEGIES
# =====================

def get_extreme_signal(df_lt, df_ht, symbol):
    """Normal Mode: MSNR/SMC Sniper (100RR)"""
    if df_lt.empty or df_ht.empty: return None
    last_ts = str(df_lt['ts'].iloc[-1])
    pip_val = 100 if "JPY" in symbol else 10000
    
    htf_trend = "BULL" if df_ht['close'].iloc[-1] > df_ht['close'].iloc[-20] else "BEAR"
    target_high = df_ht['high'].iloc[-250:].max() 
    target_low = df_ht['low'].iloc[-250:].min()
    
    c1, c3 = df_lt.iloc[-3], df_lt.iloc[-1]
    curr = c3.close

    if htf_trend == "BULL" and c3.low > c1.high and target_high > curr:
        return {"action": "BUY", "entry": curr, "tp": target_high, "sl": c1.high - (5/pip_val), "be": curr + (30/pip_val), "mode": "Normal", "ts": last_ts}
    if htf_trend == "BEAR" and c3.high < c1.low and target_low < curr:
        return {"action": "SELL", "entry": curr, "tp": target_low, "sl": c1.low + (5/pip_val), "be": curr - (30/pip_val), "mode": "Normal", "ts": last_ts}
    return None

def get_scalping_signal(df, symbol):
    """5m EMA Strategy (10, 21, 50 EMAs)"""
    if len(df) < 60: return None
    last_ts = str(df['ts'].iloc[-1])
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    curr, prev = df.iloc[-1], df.iloc[-2]
    pip_val = 100 if "JPY" in symbol else 10000
    mid = (curr.ema10 + curr.ema21) / 2

    if curr.ema50 > prev.ema50 and curr.low <= mid <= curr.high:
        return {"action": "BUY", "entry": curr.close, "tp": curr.close + (15/pip_val), "sl": curr.close - (5/pip_val), "mode": "Scalp", "ts": last_ts}
    if curr.ema50 < prev.ema50 and curr.low <= mid <= curr.high:
        return {"action": "SELL", "entry": curr.close, "tp": curr.close - (15/pip_val), "sl": curr.close + (5/pip_val), "mode": "Scalp", "ts": last_ts}
    return None

# =====================
# COMMAND HANDLERS
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💹 *Trading Control Center*\nUse /menu or the keyboard commands to manage the bot.", parse_mode=ParseMode.MARKDOWN)

async def add_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    RUNTIME_STATE[str(update.effective_chat.id)] = "awaiting_pair"
    await update.message.reply_text("Please send the symbol to add (e.g. `XAUT/USD`):")

async def remove_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    if not user["pairs"]: return await update.message.reply_text("Watchlist is empty.")
    btns = [[InlineKeyboardButton(p, callback_data=f"del:{p}")] for p in user["pairs"]]
    await update.message.reply_text("Select to remove:", reply_markup=InlineKeyboardMarkup(btns))

async def list_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    txt = "\n".join(user["pairs"]) or "No pairs."
    await update.message.reply_text(f"📊 *Watchlist:*\n{txt}", parse_mode=ParseMode.MARKDOWN)

async def set_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("London", callback_data="ses:london"), 
           InlineKeyboardButton("New York", callback_data="ses:newyork"),
           InlineKeyboardButton("Both", callback_data="ses:both")]]
    await update.message.reply_text("Choose Trading Session:", reply_markup=InlineKeyboardMarkup(kb))

async def set_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    RUNTIME_STATE[str(update.effective_chat.id)] = "awaiting_scan"
    await update.message.reply_text("Enter scan interval in seconds (e.g. `60`):")

async def set_spread(update: Update, context: ContextTypes.DEFAULT_TYPE):
    RUNTIME_STATE[str(update.effective_chat.id)] = "awaiting_spread"
    await update.message.reply_text("Enter max allowed spread (e.g. `0.0002`):")

# =====================
# MESSAGE HANDLER (INPUT)
# =====================

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    state = RUNTIME_STATE.get(uid)
    text = update.message.text.strip()
    users = load_users()

    if state == "awaiting_pair":
        pair = text.upper()
        if pair not in users[uid]["pairs"]:
            users[uid]["pairs"].append(pair)
            save_users(users)
        await update.message.reply_text(f"✅ Added {pair}")
    
    elif state == "awaiting_scan":
        users[uid]["scan_interval"] = int(text)
        save_users(users)
        await update.message.reply_text(f"⏱ Scan set to {text}s")

    elif state == "awaiting_spread":
        users[uid]["max_spread"] = float(text)
        save_users(users)
        await update.message.reply_text(f"📏 Max spread set to {text}")

    RUNTIME_STATE[uid] = None

# =====================
# CORE ENGINE
# =====================

async def fetch_data(symbol, interval, outputsize=100):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=12)
        d = r.json()
        if "values" not in d: return pd.DataFrame()
        df = pd.DataFrame(d["values"]).apply(pd.to_numeric, errors='ignore')
        df['ts'] = pd.to_datetime(df['datetime'])
        return df.iloc[::-1]
    except: return pd.DataFrame()

async def scanner_loop(app: Application):
    while True:
        users = load_users()
        for cid, settings in users.items():
            # Session Filter
            now_utc = datetime.now(pytz.UTC)
            hour = now_utc.hour
            sess = settings.get("session", "both")
            if sess == "london" and not (7 <= hour <= 15): continue
            if sess == "newyork" and not (12 <= hour <= 20): continue
            
            for pair in settings["pairs"]:
                # Normal & Scalp Logic with API_DELAY and SENT_SIGNALS check...
                pass 
        await asyncio.sleep(60)

async def on_startup(app: Application):
    asyncio.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Command mappings
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_pair))
    app.add_handler(CommandHandler("remove", remove_pair))
    app.add_handler(CommandHandler("pairs", list_pairs))
    app.add_handler(CommandHandler("setsession", set_session))
    app.add_handler(CommandHandler("setscan", set_scan))
    app.add_handler(CommandHandler("setspread", set_spread))
    app.add_handler(CallbackQueryHandler(handle_input)) # For Inline Buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))

    app.post_init = on_startup 
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
