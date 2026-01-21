import os, json, asyncio, requests, pytz
import pandas as pd
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DATA_FILE = "users.json"
API_DELAY = 12.0  # Safe delay for 8 requests/min limit

SENT_SIGNALS = {}
RUNTIME_STATE = {}

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
            "pairs": [], "mode": "BOTH", "session": "BOTH", 
            "scan_interval": 60, "cooldown": 60, "max_spread": 0.0005
        }
        save_users(users)
    return users[chat_id]

# =====================
# STRATEGIES
# =====================

def get_extreme_signal(df_lt, df_ht, symbol):
    if df_lt.empty or df_ht.empty: return None
    last_ts = str(df_lt['ts'].iloc[-1])
    is_vol = any(x in symbol for x in ["BTC", "XAU", "XAUT"])
    pip_val = 100 if "JPY" in symbol else 10000
    
    ht_trend = "BULL" if df_ht['close'].iloc[-1] > df_ht['close'].iloc[-20] else "BEAR"
    tp_buy, tp_sell = df_ht['high'].iloc[-250:].max(), df_ht['low'].iloc[-250:].min()
    
    c1, c3 = df_lt.iloc[-3], df_lt.iloc[-1]
    curr = c3.close
    sl_gap = 15/pip_val if is_vol else 5/pip_val

    if ht_trend == "BULL" and c3.low > c1.high and tp_buy > curr:
        return {"action": "BUY", "entry": curr, "tp": tp_buy, "sl": c1.high - sl_gap, "be": curr + (30/pip_val), "mode": "Normal", "ts": last_ts}
    if ht_trend == "BEAR" and c3.high < c1.low and tp_sell < curr:
        return {"action": "SELL", "entry": curr, "tp": tp_sell, "sl": c1.low + sl_gap, "be": curr - (30/pip_val), "mode": "Normal", "ts": last_ts}
    return None

def get_scalping_signal(df, symbol):
    if len(df) < 60: return None
    last_ts = str(df['ts'].iloc[-1])
    df['ema10'] = df['close'].ewm(span=10).mean()
    df['ema21'] = df['close'].ewm(span=21).mean()
    df['ema50'] = df['close'].ewm(span=50).mean()
    curr, prev = df.iloc[-1], df.iloc[-2]
    pip_val = 100 if "JPY" in symbol else 10000
    mid = (curr.ema10 + curr.ema21) / 2

    if curr.ema50 > prev.ema50 and curr.low <= mid <= curr.high:
        return {"action": "BUY", "entry": curr.close, "tp": curr.close + (15/pip_val), "sl": curr.close - (5/pip_val), "mode": "Scalp", "ts": last_ts}
    if curr.ema50 < prev.ema50 and curr.low <= mid <= curr.high:
        return {"action": "SELL", "entry": curr.close, "tp": curr.close - (15/pip_val), "sl": curr.close + (5/pip_val), "mode": "Scalp", "ts": last_ts}
    return None

# =====================
# COMMANDS & INPUT
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    await update.message.reply_text("💹 *Trading Control Panel*")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    RUNTIME_STATE[str(update.effective_chat.id)] = "add"
    await update.message.reply_text("➕ Symbol to add (e.g. BTC/USD):")

async def pairs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    txt = "\n".join(user["pairs"]) or "None"
    await update.message.reply_text(f"📊 *Watchlist:*\n{txt}", parse_mode=ParseMode.MARKDOWN)

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id); users = load_users(); user = get_user(users, uid)
    state = RUNTIME_STATE.get(uid); text = update.message.text.strip().upper()
    if state == "add":
        if text not in user["pairs"]: user["pairs"].append(text)
        save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Monitoring {text}")

# =====================
# ENGINE: SCANNER & API
# =====================

async def fetch_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=250&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=12)
        d = r.json()
        
        # FIX: Check for 'values' before accessing it to prevent the crash
        if "values" not in d:
            print(f"🧨 API Error for {symbol}: {d.get('message', 'No message provided')}")
            return pd.DataFrame()
            
        df = pd.DataFrame(d["values"])
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['ts'] = pd.to_datetime(df['datetime'])
        return df.iloc[::-1].dropna()
    except Exception as e:
        print(f"🧨 System Error: {e}"); return pd.DataFrame()

async def scanner_loop(app: Application):
    print("🚀 Scanner background task started.")
    while True:
        try:
            users = load_users()
            for uid, settings in users.items():
                for pair in settings["pairs"]:
                    key = f"{uid}_{pair}"
                    await asyncio.sleep(API_DELAY)
                    df_l = await fetch_data(pair, "5min")
                    if df_l.empty: continue

                    if settings["mode"] in ["NORMAL", "BOTH"]:
                        await asyncio.sleep(API_DELAY)
                        df_h = await fetch_data(pair, "1day")
                        sig = get_extreme_signal(df_l, df_h, pair)
                        if sig and SENT_SIGNALS.get(f"{key}_n") != sig['ts']:
                            await app.bot.send_message(uid, f"🚨 *NORMAL*\n{pair}: {sig['action']}", parse_mode=ParseMode.MARKDOWN)
                            SENT_SIGNALS[f"{key}_n"] = sig['ts']
                    
                    if settings["mode"] in ["SCALP", "BOTH"]:
                        sig = get_scalping_signal(df_l, pair)
                        if sig and SENT_SIGNALS.get(f"{key}_s") != sig['ts']:
                            await app.bot.send_message(uid, f"🚨 *SCALP*\n{pair}: {sig['action']}", parse_mode=ParseMode.MARKDOWN)
                            SENT_SIGNALS[f"{key}_s"] = sig['ts']
            await asyncio.sleep(60)
        except Exception as e: print(f"🚨 Scanner loop error: {e}"); await asyncio.sleep(10)

async def post_init(app: Application):
    asyncio.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("pairs", pairs_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))
    app.post_init = post_init
    
    # drop_pending_updates=True is key to fixing the Conflict error on restart
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
