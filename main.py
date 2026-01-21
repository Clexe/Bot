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
API_DELAY = 12.0 

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
            "pairs": [], "mode": "NORMAL", "session": "BOTH", 
            "scan_interval": 60, "cooldown": 60, "max_spread": 0.0005
        }
        save_users(users)
    return users[chat_id]

# =====================
# STRATEGY: 100 RR SNIPER
# =====================

def get_extreme_signal(df_lt, df_ht, symbol):
    if df_lt.empty or df_ht.empty: return None
    pip_val = 100 if "JPY" in symbol else 10000
    ht_trend = "BULL" if df_ht['close'].iloc[-1] > df_ht['close'].iloc[-20] else "BEAR"
    tp_buy, tp_sell = df_ht['high'].iloc[-250:].max(), df_ht['low'].iloc[-250:].min()
    c1, c3 = df_lt.iloc[-3], df_lt.iloc[-1]
    curr = c3.close
    sl_gap = 15/pip_val if any(x in symbol for x in ["BTC", "XAU"]) else 5/pip_val

    # TP Sanity & BE logic
    if ht_trend == "BULL" and c3.low > c1.high and tp_buy > curr:
        return {"action": "BUY", "entry": curr, "tp": tp_buy, "sl": c1.high - sl_gap, "be": curr + (30/pip_val), "ts": str(df_lt['ts'].iloc[-1])}
    if ht_trend == "BEAR" and c3.high < c1.low and tp_sell < curr:
        return {"action": "SELL", "entry": curr, "tp": tp_sell, "sl": c1.low + sl_gap, "be": curr - (30/pip_val), "ts": str(df_lt['ts'].iloc[-1])}
    return None

# =====================
# COMMAND HANDLERS
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    await update.message.reply_text("💹 *100 RR Sniper System Online*\nUse /help to see all commands.", parse_mode=ParseMode.MARKDOWN)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = ("📖 *Command List:*\n"
           "1. `/add` - Add symbols (BTC/USD)\n"
           "2. `/pairs` - View watchlist\n"
           "3. `/markets` - View supported markets\n"
           "4. `/setsession` - London, NY, or Both\n"
           "5. `/setscan` - Set scan interval (sec)\n"
           "6. `/setcooldown` - Set alert cooldown (min)\n"
           "7. `/setspread` - Set max allowed spread")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def markets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💹 *Supported Markets:*\nForex, Crypto, and Commodities (Gold/Oil) via Twelve Data API.")

async def router_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split('@')[0][1:]
    uid = str(update.effective_chat.id)
    if cmd == "setsession":
        kb = [[InlineKeyboardButton("London", callback_data="ses:london"), InlineKeyboardButton("NY", callback_data="ses:newyork"), InlineKeyboardButton("Both", callback_data="ses:both")]]
        await update.message.reply_text("⏰ Choose Trading Session:", reply_markup=InlineKeyboardMarkup(kb))
    elif cmd in ["add", "setscan", "setcooldown", "setspread"]:
        RUNTIME_STATE[uid] = cmd
        await update.message.reply_text(f"⚙️ Enter new value for {cmd.upper()}:")
    elif cmd == "pairs":
        user = get_user(load_users(), uid)
        txt = "\n".join(user["pairs"]) or "Watchlist is empty."
        await update.message.reply_text(f"📊 *Watchlist:*\n{txt}", parse_mode=ParseMode.MARKDOWN)

# =====================
# CALLBACKS & INPUT
# =====================

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id); state = RUNTIME_STATE.get(uid); text = update.message.text.strip()
    users = load_users(); user = get_user(users, uid)
    try:
        if state == "add":
            if text.upper() not in user["pairs"]: user["pairs"].append(text.upper())
        elif state == "setscan": user["scan_interval"] = int(text)
        elif state == "setcooldown": user["cooldown"] = int(text)
        elif state == "setspread": user["max_spread"] = float(text)
        save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text("✅ Setting updated.")
    except: await update.message.reply_text("❌ Invalid input.")

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); uid = str(q.message.chat_id); users = load_users(); user = get_user(users, uid)
    if q.data.startswith("ses:"):
        user["session"] = q.data.split(":")[1]; save_users(users)
        await q.edit_message_text(f"✅ Session: {user['session'].upper()}")

# =====================
# ENGINE
# =====================

async def fetch_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=250&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=12); d = r.json()
        if "values" not in d: # Fixed 'values' crash
            print(f"🧨 API Error ({symbol}): {d.get('message', 'No data')}"); return pd.DataFrame()
        df = pd.DataFrame(d["values"])
        for col in ['open','high','low','close']: df[col] = pd.to_numeric(df[col], errors='coerce')
        df['ts'] = pd.to_datetime(df['datetime']); return df.iloc[::-1].dropna()
    except: return pd.DataFrame()

async def scanner_loop(app: Application):
    print("🚀 Sniper Scanner Active.")
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
        except Exception as e: print(f"🚨 Scanner Error: {e}"); await asyncio.sleep(10)

async def post_init(app: Application):
    asyncio.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("markets", markets_cmd))
    # Map all settings commands to the router
    for c in ["add", "pairs", "setsession", "setscan", "setcooldown", "setspread"]:
        app.add_handler(CommandHandler(c, router_cmd))
    
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))
    app.post_init = post_init
    app.run_polling(drop_pending_updates=True) # Clears Conflict error

if __name__ == "__main__": main()
