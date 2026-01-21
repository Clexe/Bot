import os, json, asyncio, requests, pytz
import pandas as pd
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    ContextTypes, 
    filters
)

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DATA_FILE = "users.json"
API_DELAY = 12.0 # Safe delay for Twelve Data free tier

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
            "pairs": [], 
            "mode": "NORMAL", 
            "session": "BOTH"
        }
        save_users(users)
    return users[chat_id]

# =====================
# STRATEGY: 100 RR SNIPER
# =====================

def get_extreme_signal(df_lt, df_ht, symbol):
    """SMC Sniper: Targets 100 RR by using M5 entries for Daily targets"""
    if df_lt.empty or df_ht.empty: return None
    
    last_ts = str(df_lt['ts'].iloc[-1])
    is_vol = any(x in symbol for x in ["BTC", "XAU", "XAUT"])
    pip_val = 100 if "JPY" in symbol else 10000
    
    # HTF Daily Bias
    ht_trend = "BULL" if df_ht['close'].iloc[-1] > df_ht['close'].iloc[-20] else "BEAR"
    tp_buy, tp_sell = df_ht['high'].iloc[-250:].max(), df_ht['low'].iloc[-250:].min()
    
    # LTF M5 Entry Setup
    c1, c3 = df_lt.iloc[-3], df_lt.iloc[-1]
    curr = c3.close
    sl_gap = 15/pip_val if is_vol else 5/pip_val

    # Sanity: Is the Daily Target actually in front of us?
    if ht_trend == "BULL" and c3.low > c1.high and tp_buy > curr:
        return {
            "action": "BUY", "entry": curr, "tp": tp_buy, 
            "sl": c1.high - sl_gap, "be": curr + (30/pip_val), "ts": last_ts
        }
    if ht_trend == "BEAR" and c3.high < c1.low and tp_sell < curr:
        return {
            "action": "SELL", "entry": curr, "tp": tp_sell, 
            "sl": c1.low + sl_gap, "be": curr - (30/pip_val), "ts": last_ts
        }
    return None

# =====================
# COMMAND HANDLERS
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    await update.message.reply_text("💹 *100 RR Sniper Bot Active*\nUse /add [symbol] to begin.", parse_mode=ParseMode.MARKDOWN)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    RUNTIME_STATE[uid] = "awaiting_pair"
    await update.message.reply_text("➕ Send the symbol (e.g., `XAU/USD`):")

async def pairs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    txt = "\n".join(user["pairs"]) or "Watchlist is empty."
    await update.message.reply_text(f"📊 *Current Watchlist:*\n{txt}", parse_mode=ParseMode.MARKDOWN)

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    if not user["pairs"]: return await update.message.reply_text("Watchlist empty.")
    btns = [[InlineKeyboardButton(p, callback_data=f"del:{p}")] for p in user["pairs"]]
    await update.message.reply_text("🗑 Select to remove:", reply_markup=InlineKeyboardMarkup(btns))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 *Commands:*\n/add - Track pair\n/remove - Delete pair\n/pairs - View list\n/start - Initialize")

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    if RUNTIME_STATE.get(uid) == "awaiting_pair":
        users = load_users(); user = get_user(users, uid)
        pair = update.message.text.upper().strip()
        if pair not in user["pairs"]: user["pairs"].append(pair)
        save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Monitoring {pair}")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = str(q.message.chat_id); users = load_users(); user = get_user(users, uid)
    if q.data.startswith("del:"):
        p = q.data.split(":")[1]
        if p in user["pairs"]: user["pairs"].remove(p); save_users(users)
        await q.edit_message_text(f"🗑 Removed {p}")

# =====================
# ENGINE
# =====================

async def fetch_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=250&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=12); d = r.json()
        if "values" not in d: # Prevent 'values' crash
            print(f"🧨 API Error ({symbol}): {d.get('message', 'Check Symbol')}"); return pd.DataFrame()
        df = pd.DataFrame(d["values"])
        for col in ['open','high','low','close']: df[col] = pd.to_numeric(df[col], errors='coerce')
        df['ts'] = pd.to_datetime(df['datetime']); return df.iloc[::-1].dropna()
    except: return pd.DataFrame()

async def scanner_loop(app: Application):
    print("🚀 Sniper Scanner Task Started.")
    while True:
        try:
            users = load_users()
            for uid, settings in users.items():
                for pair in settings["pairs"]:
                    await asyncio.sleep(API_DELAY); df_l = await fetch_data(pair, "5min")
                    if df_l.empty: continue
                    await asyncio.sleep(API_DELAY); df_h = await fetch_data(pair, "1day")
                    sig = get_extreme_signal(df_l, df_h, pair)
                    
                    if sig and SENT_SIGNALS.get(f"{uid}_{pair}") != sig['ts']:
                        msg = (f"🚨 *100 RR SNIPER*\n*{pair}*: {sig['action']}\n\n"
                               f"E: `{sig['entry']:.5f}`\nTP: `{sig['tp']:.5f}`\n"
                               f"SL: `{sig['sl']:.5f}`\n🛡 *BE:* `{sig['be']:.5f}`")
                        await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                        SENT_SIGNALS[f"{uid}_{pair}"] = sig['ts']
            await asyncio.sleep(60)
        except Exception as e: print(f"🚨 Loop Error: {e}"); await asyncio.sleep(10)

async def post_init(app: Application):
    asyncio.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("pairs", pairs_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))
    app.post_init = post_init
    app.run_polling(drop_pending_updates=True) # Fixes Conflict error

if __name__ == "__main__": main()
