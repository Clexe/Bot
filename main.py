import os, json, asyncio, websockets, time
from datetime import datetime
import pandas as pd
from pybit.unified_trading import HTTP
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
DERIV_TOKEN = os.getenv("DERIV_TOKEN")
DERIV_APP_ID = os.getenv("DERIV_APP_ID")
BYBIT_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET = os.getenv("BYBIT_API_SECRET")
DATA_FILE = "users.json"

bybit = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)
SENT_SIGNALS = {} 
RUNTIME_STATE = {}
LAST_SCAN_TIME = 0
IS_SCANNING = False
LAST_SIGNAL_INFO = "None"

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
            "pairs": [], "scan_interval": 60, "cooldown": 60, 
            "max_spread": 0.0005, "session": "BOTH"
        }
        save_users(users)
    return users[chat_id]

def is_in_session(session_type):
    now_hour = datetime.utcnow().hour
    if session_type == "LONDON": return 8 <= now_hour <= 16
    if session_type == "NY": return 13 <= now_hour <= 21
    return True 

# =====================
# ENGINE & ROUTING
# =====================
async def fetch_data(pair, interval):
    clean_pair = pair.replace("/", "").upper().strip()
    is_deriv = any(x in clean_pair for x in ["XAU", "EUR", "GBP", "JPY", "R_", "V75", "1S"])
    
    if is_deriv:
        if any(x in clean_pair for x in ["XAU", "EUR", "GBP", "JPY"]) and not clean_pair.startswith("frx"):
            clean_pair = "frx" + clean_pair
        uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        gran = 300 if interval == "M5" else 86400
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
                await ws.recv()
                await ws.send(json.dumps({"ticks_history": clean_pair, "count": 100, "style": "candles", "granularity": gran}))
                res = json.loads(await ws.recv())
                return pd.DataFrame(res.get("candles", []))
        except: return pd.DataFrame()
    else:
        try:
            tf = "5" if interval == "M5" else "D"
            resp = bybit.get_kline(category="linear", symbol=clean_pair, interval=tf, limit=100)
            df = pd.DataFrame(resp['result']['list'], columns=['ts','o','h','l','c','v','t'])
            df.rename(columns={'o':'open','h':'high','l':'low','c':'close'}, inplace=True)
            return df.iloc[::-1].apply(pd.to_numeric)
        except: return pd.DataFrame()

# =====================
# FULL COMMAND HANDLER
# =====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id); text = update.message.text.lower().strip()
    users = load_users(); user = get_user(users, uid); state = RUNTIME_STATE.get(uid)

    # Main Command Router
    if text == "status":
        time_diff = int(time.time() - LAST_SCAN_TIME)
        scan_msg = "🟢 Active" if IS_SCANNING else f"⏳ Idle ({max(0, user['scan_interval'] - time_diff)}s)"
        await update.message.reply_text(f"🤖 *System Status*\nScanner: {scan_msg}\nPairs: {len(user['pairs'])}\nLast Signal: {LAST_SIGNAL_INFO}", parse_mode=ParseMode.MARKDOWN)
    elif text == "add": RUNTIME_STATE[uid] = "add"; await update.message.reply_text("Enter Symbol (BTCUSDT, XAUUSD):")
    elif text == "remove": RUNTIME_STATE[uid] = "remove"; await update.message.reply_text("Symbol to remove:")
    elif text == "pairs": await update.message.reply_text(f"Watchlist: {', '.join(user['pairs']) or 'Empty'}")
    elif text == "markets": await update.message.reply_text("Bybit: Crypto | Deriv: Forex & Indices")
    elif text == "setsession": RUNTIME_STATE[uid] = "session"; await update.message.reply_text("Choose: LONDON, NY, BOTH")
    elif text == "setscan": RUNTIME_STATE[uid] = "scan"; await update.message.reply_text("Enter Seconds:")
    elif text == "setcooldown": RUNTIME_STATE[uid] = "cooldown"; await update.message.reply_text("Enter Minutes:")
    elif text == "setspread": RUNTIME_STATE[uid] = "spread"; await update.message.reply_text("Enter Spread (e.g. 0.0005):")
    elif text == "help": await update.message.reply_text("SMC Sniper v4.0: M5 FVGs aligned with Daily Bias.")
    
    # Input Processing
    elif state == "add":
        user["pairs"].append(text.upper()); save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ {text.upper()} added.")
    elif state == "remove":
        if text.upper() in user["pairs"]: user["pairs"].remove(text.upper()); save_users(users)
        RUNTIME_STATE[uid] = None; await update.message.reply_text(f"🗑 {text.upper()} removed.")
    elif state == "session":
        user["session"] = text.upper(); save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Session: {text.upper()}")
    elif state == "scan":
        user["scan_interval"] = int(text); save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Interval set.")

# =====================
# ENGINE & SCANNER
# =====================
async def scanner_loop(app):
    global LAST_SCAN_TIME, IS_SCANNING, LAST_SIGNAL_INFO
    while True:
        IS_SCANNING = True; LAST_SCAN_TIME = time.time()
        users = load_users()
        for uid, settings in users.items():
            if not is_in_session(settings["session"]): continue
            print(f"🔍 Checking {len(settings['pairs'])} pairs...")
            for pair in settings["pairs"]:
                df_l = await fetch_data(pair, "M5")
                df_h = await fetch_data(pair, "1D")
                # Strategy logic here... (FVG detection)
        IS_SCANNING = False; await asyncio.sleep(60)

async def post_init(app: Application):
    asyncio.get_event_loop().create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("SMC Active", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")], [KeyboardButton("status"), KeyboardButton("setsession"), KeyboardButton("help")]], resize_keyboard=True))))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__": main()
