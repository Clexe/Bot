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

# =====================
# DATA MANAGEMENT
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
# UPDATED SMART ROUTER
# =====================
async def fetch_data(pair, interval):
    # 1. First, Keep Case Sensitivity for the Prefix Check
    raw_pair = pair.replace("/", "").strip()
    clean_pair = raw_pair.upper() 
    
    # 2. Expanded Keyword List (Added "FRX", "US30", "NAS", "GER")
    deriv_keywords = ["XAU", "EUR", "GBP", "JPY", "R_", "V75", "1S", "FRX", "US30", "NAS", "GER"]
    is_deriv = any(x in clean_pair for x in deriv_keywords)
    
    if is_deriv:
        # 3. Smart Prefix Logic (Fixes the Double-Prefix Bug)
        if clean_pair.startswith("FRX"):
            # If user entered "frxXAUUSD", it became "FRXXAUUSD". Fix it to "frxXAUUSD"
            clean_pair = "frx" + clean_pair[3:]
        elif any(x in clean_pair for x in ["XAU", "EUR", "GBP", "JPY", "US30", "NAS", "GER"]):
            # If user entered "XAUUSD" (no prefix), add it.
            clean_pair = "frx" + clean_pair
            
        uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        gran = 300 if interval == "M5" else 86400
        
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
                while True:
                    auth_res = json.loads(await ws.recv())
                    if "authorize" in auth_res: break 
                
                await ws.send(json.dumps({
                    "ticks_history": clean_pair, 
                    "count": 100, 
                    "style": "candles", 
                    "granularity": gran
                }))
                
                res = json.loads(await ws.recv())
                candles = res.get("candles", [])
                
                if not candles:
                    print(f"⚠️ Deriv: No data for {clean_pair}")
                    return pd.DataFrame()
                
                df = pd.DataFrame(candles)
                return df[['open', 'high', 'low', 'close']].apply(pd.to_numeric)
        except Exception as e:
            print(f"❌ Deriv Error ({clean_pair}): {e}")
            return pd.DataFrame()
    else:
        # Bybit Engine (unchanged)
        try:
            tf = "5" if interval == "M5" else "D"
            resp = bybit.get_kline(category="linear", symbol=clean_pair, interval=tf, limit=100)
            df = pd.DataFrame(resp['result']['list'], columns=['ts','open','high','low','close','vol','turnover'])
            df = df[['open','high','low','close']].apply(pd.to_numeric)
            return df.iloc[::-1]
        except Exception as e:
            print(f"❌ Bybit Error ({clean_pair}): {e}")
            return pd.DataFrame()

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
# THE 10 COMMANDS
# =====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id); text = update.message.text.lower().strip()
    users = load_users(); user = get_user(users, uid); state = RUNTIME_STATE.get(uid)

    # Functions 1-10 explicitly defined
    if text == "status": # 1
        time_diff = int(time.time() - LAST_SCAN_TIME)
        status_text = "🟢 SCANNING" if IS_SCANNING else f"⏳ IDLE ({max(0, user['scan_interval'] - time_diff)}s)"
        await update.message.reply_text(f"🤖 *Status*\nScanner: {status_text}\nPairs: {len(user['pairs'])}\nSession: {user['session']}")
    elif text == "add": # 2
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Enter Symbol (BTCUSDT, XAUUSD):")
    elif text == "remove": # 3
        RUNTIME_STATE[uid] = "remove"
        await update.message.reply_text("Symbol to remove:")
    elif text == "pairs": # 4
        await update.message.reply_text(f"📊 Watchlist: {', '.join(user['pairs']) or 'Empty'}")
    elif text == "markets": # 5
        await update.message.reply_text("📡 Bybit: Crypto | Deriv: Forex/Synthetics")
    elif text == "setsession": # 6
        RUNTIME_STATE[uid] = "session"
        await update.message.reply_text("Enter LONDON, NY, or BOTH:")
    elif text == "setscan": # 7
        RUNTIME_STATE[uid] = "scan"
        await update.message.reply_text("Enter Scan seconds:")
    elif text == "setcooldown": # 8
        RUNTIME_STATE[uid] = "cooldown"
        await update.message.reply_text("Enter Cooldown minutes:")
    elif text == "setspread": # 9
        RUNTIME_STATE[uid] = "spread"
        await update.message.reply_text("Enter Max Spread (e.g. 0.0005):")
    elif text == "help": # 10
        await update.message.reply_text("SMC Sniper: M5 FVG entries aligned with Daily Bias.")
    
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
        await update.message.reply_text(f"✅ Scan set.")
    elif state == "cooldown":
        user["cooldown"] = int(text); save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Cooldown set.")
    elif state == "spread":
        user["max_spread"] = float(text); save_users(users); RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Spread set.")

# =====================
# ENGINE & SCANNER
# =====================
async def scanner_loop(app):
    global LAST_SCAN_TIME, IS_SCANNING
    while True:
        try:
            IS_SCANNING = True; LAST_SCAN_TIME = time.time()
            users = load_users()
            for uid, settings in users.items():
                if not is_in_session(settings["session"]): continue
                print(f"🔍 [SCAN START] User {uid} | {len(settings['pairs'])} pairs")
                for pair in settings["pairs"]:
                    exchange = "DERIV" if any(x in pair.upper() for x in ["R_", "V75", "XAU", "EUR", "GBP"]) else "BYBIT"
                    print(f"  ➡️ Checking {pair} on {exchange}...")
                    df_l = await fetch_data(pair, "M5"); df_h = await fetch_data(pair, "1D")
                    sig = get_smc_signal(df_l, df_h, pair)
                    if sig and SENT_SIGNALS.get(f"{uid}_{pair}") != sig['e']:
                        msg = f"🚨 *SMC SIGNAL: {pair}*\n{sig['act']} @ `{sig['e']}`\nTP: `{sig['tp']}` | SL: `{sig['sl']}`"
                        await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                        SENT_SIGNALS[f"{uid}_{pair}"] = sig['e']
                        print(f"  🎯 SIGNAL TRIGGERED: {pair}")
            IS_SCANNING = False; await asyncio.sleep(60)
        except Exception as e: print(f"Error: {e}"); await asyncio.sleep(10)

async def post_init(app: Application):
    asyncio.get_event_loop().create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Sniper Ready", reply_markup=ReplyKeyboardMarkup([[KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")], [KeyboardButton("status"), KeyboardButton("setsession"), KeyboardButton("markets")], [KeyboardButton("setscan"), KeyboardButton("setcooldown"), KeyboardButton("setspread")], [KeyboardButton("help")]], resize_keyboard=True))))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__": main()
