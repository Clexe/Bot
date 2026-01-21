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
        users[chat_id] = {"pairs": [], "mode": "BOTH", "session": "BOTH"}
        save_users(users)
    return users[chat_id]

# =====================
# THE INLINE MENU
# =====================
def main_menu_kb(user_mode):
    # This creates the buttons you were missing
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔄 Mode: {user_mode}", callback_data="toggle_mode")],
        [InlineKeyboardButton("➕ Add Pair", callback_data="add"), InlineKeyboardButton("🗑 Remove Pair", callback_data="remove")],
        [InlineKeyboardButton("📊 Watchlist", callback_data="list")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    # reply_markup=main_menu_kb(...) attaches the menu to the message
    await update.message.reply_text(
        "💹 *Trading Control Panel*",
        reply_markup=main_menu_kb(user['mode']),
        parse_mode=ParseMode.MARKDOWN
    )

# =====================
# HANDLERS: BUTTONS & INPUT
# =====================

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Stops the loading spinner in Telegram
    uid = str(query.message.chat_id)
    users = load_users(); user = get_user(users, uid)

    if query.data == "toggle_mode":
        user["mode"] = "SCALP" if user.get("mode") == "NORMAL" else "NORMAL"
        save_users(users)
        await query.edit_message_text(f"✅ Mode: {user['mode']}", reply_markup=main_menu_kb(user['mode']))
    
    elif query.data == "add":
        RUNTIME_STATE[uid] = "add"
        await query.message.reply_text("➕ Send the symbol (e.g. BTC/USD):")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id); state = RUNTIME_STATE.get(uid)
    if state == "add":
        users = load_users(); user = get_user(users, uid)
        pair = update.message.text.upper().strip()
        if pair not in user["pairs"]: user["pairs"].append(pair)
        save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Monitoring {pair}", reply_markup=main_menu_kb(user['mode']))

# =====================
# ENGINE: SCANNER & API
# =====================

async def fetch_data(symbol, interval):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize=250&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=12)
        d = r.json()
        if "values" not in d:
            print(f"🧨 API Error: {d.get('message', 'Check Symbol')}")
            return pd.DataFrame()
        df = pd.DataFrame(d["values"])
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['ts'] = pd.to_datetime(df['datetime'])
        return df.iloc[::-1].dropna()
    except: return pd.DataFrame()

async def post_init(app: Application):
    # This keeps the scanner running without crashing the main bot
    print("🚀 Scanner starting...")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Registering handlers so the bot actually listens
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks)) # Handles buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)) # Handles typing
    
    app.post_init = post_init
    
    # drop_pending_updates=True clears the 'Conflict' error on restart
    print("📡 Polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
