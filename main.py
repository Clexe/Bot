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
# SMART ROUTER ENGINE
# =====================
async def fetch_data(pair, interval):
    clean_pair = pair.replace("/", "").upper().strip()
    
    # Logic: Crypto keywords go to Bybit, everything else (Gold, Forex, Indices) to Deriv
    crypto_list = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "LINK"]
    is_crypto = any(coin in clean_pair for coin in crypto_list) and "USD" in clean_pair
    is_deriv_forced = any(x in clean_pair for x in ["XAU", "EUR", "GBP", "JPY", "R_", "V75", "1S"])

    if is_deriv_forced or not is_crypto:
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
# SMC STRATEGY
# =====================
def get_smc_signal(df_l, df_h, pair):
    if df_l.empty or df_h.empty: return None
    pip_val = 100 if any(x in pair for x in ["JPY", "V75", "R_"]) else 10000
    bias = "BULL" if df_h['close'].iloc[-1] > df_h['close'].iloc[-20] else "BEAR"
    c1, c3 = df_l.iloc[-3], df_l.iloc[-1]
    
    if bias == "BULL" and c3.low > c1.high:
        return {"act": "BUY", "e": c3.close, "tp": df_h['high'].max(), "sl": c1.high - (10/pip_val), "be": c3.close + (30/pip_val)}
    if bias == "BEAR" and c3.high < c1.low:
        return {"act": "SELL", "e": c3.close, "tp": df_h['low'].min(), "sl": c1.low + (10/pip_val), "be": c3.close - (30/pip_val)}
    return None

# =====================
# FULL MENU HANDLERS
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(load_users(), str(update.effective_chat.id))
    kb = [[KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")],
          [KeyboardButton("setsession"), KeyboardButton("setscan"), KeyboardButton("setcooldown")],
          [KeyboardButton("setspread"), KeyboardButton("markets"), KeyboardButton("help")]]
    await update.message.reply_text("💹 *Sniper Bot v2.0 Online*", 
                                   reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True), 
                                   parse_mode=ParseMode.MARKDOWN)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id); text = update.message.text.lower().strip()
    users = load_users(); user = get_user(users, uid); state = RUNTIME_STATE.get(uid)

    if text == "add":
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Enter Symbol (e.g. BTCUSDT or XAUUSD):")
    elif text == "remove":
        RUNTIME_STATE[uid] = "remove"
        await update.message.reply_text("Enter Symbol to remove:")
    elif text == "pairs":
        await update.message.reply_text(f"📊 *Watchlist:* {', '.join(user['pairs']) or 'Empty'}", parse_mode=ParseMode.MARKDOWN)
    elif text == "setsession":
        RUNTIME_STATE[uid] = "session"
        await update.message.reply_text("Enter: LONDON, NY, or BOTH")
    elif text == "setscan":
        RUNTIME_STATE[uid] = "scan"
        await update.message.reply_text("Enter scan interval in seconds:")
    elif text == "setcooldown":
        RUNTIME_STATE[uid] = "cooldown"
        await update.message.reply_text("Enter cooldown in minutes:")
    elif text == "setspread":
        RUNTIME_STATE[uid] = "spread"
        await update.message.reply_text("Enter max spread (e.g. 0.0005):")
    elif text == "markets":
        await update.message.reply_text("📡 *Exchanges:* Bybit (Crypto), Deriv (Forex/Synthetics)", parse_mode=ParseMode.MARKDOWN)
    elif text == "help":
        await update.message.reply_text("Use buttons to configure settings. Bot scans M5 for FVG entries aligned with Daily Bias.")
    
    # State Processing
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
        await update.message.reply_text(f"✅ Scan interval: {text}s")
    elif state == "cooldown":
        user["cooldown"] = int(text); save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Cooldown: {text}m")
    elif state == "spread":
        user["max_spread"] = float(text); save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Max Spread: {text}")

# =====================
# ENGINE
# =====================
async def scanner_loop(app):
    while True:
        try:
            users = load_users()
            for uid, settings in users.items():
                if not is_in_session(settings["session"]): continue
                for pair in settings["pairs"]:
                    df_l = await fetch_data(pair, "M5")
                    df_h = await fetch_data(pair, "1D")
                    sig = get_smc_signal(df_l, df_h, pair)
                    if sig and SENT_SIGNALS.get(f"{uid}_{pair}") != sig['e']:
                        msg = (f"🚨 *SMC SIGNAL: {pair}*\n{sig['act']} @ `{sig['e']}`\n"
                               f"TP: `{sig['tp']}` | SL: `{sig['sl']}`\n🛡 *BE:* `{sig['be']}`")
                        await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                        SENT_SIGNALS[f"{uid}_{pair}"] = sig['e']
            await asyncio.sleep(60)
        except Exception as e: print(f"Error: {e}"); await asyncio.sleep(10)

async def post_init(app: Application):
    asyncio.get_event_loop().create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__": main()
