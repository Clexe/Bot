import os
import json
import asyncio
import websockets
import time
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

# Initialize Bybit
bybit = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)

# Global State
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
            "pairs": [], 
            "scan_interval": 60, 
            "cooldown": 60, 
            "max_spread": 0.0005, 
            "session": "BOTH"
        }
        save_users(users)
    return users[chat_id]

# =====================
# TIME & MARKET FILTERS
# =====================
def is_in_session(session_type):
    now_hour = datetime.utcnow().hour
    if session_type == "LONDON": return 8 <= now_hour <= 16
    if session_type == "NY": return 13 <= now_hour <= 21
    return True 

def is_market_open(pair):
    """
    Checks if the market is open for the specific pair.
    - Crypto/Synthetics: Always True
    - Forex/Gold/Indices: Closed Weekends
    """
    clean = pair.upper()
    # 1. Always Open Assets (Crypto & Synthetics)
    always_open_keys = ["BTC", "ETH", "SOL", "USDT", "R_", "V75", "V10", "V25", "V50", "V100", "1HZ", "BOOM", "CRASH", "JUMP", "STEP"]
    if any(k in clean for k in always_open_keys):
        return True
        
    # 2. Traditional Market Hours (Forex, Metals, Indices)
    # Logic based on UTC time.
    now = datetime.utcnow()
    weekday = now.weekday() # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    hour = now.hour
    
    # Friday: Most markets close around 21:00 UTC
    if weekday == 4 and hour >= 21: return False
    
    # Saturday: Closed all day
    if weekday == 5: return False
    
    # Sunday: Markets open late (approx 21:00 UTC for Sydney session)
    if weekday == 6 and hour < 21: return False
    
    return True

# =====================
# SMART ROUTER & STRATEGY
# =====================
async def fetch_data(pair, interval):
    # 1. Clean and normalize the pair name
    raw_pair = pair.replace("/", "").strip()
    clean_pair = raw_pair.upper() 
    
    # 2. Define Deriv keywords (including Index codes)
    deriv_keywords = ["XAU", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF", "R_", "V75", "1S", "FRX", "US30", "NAS", "GER", "UK100"]
    is_deriv = any(x in clean_pair for x in deriv_keywords)
    
    if is_deriv:
        # 3. Smart Prefix Logic
        # If it starts with "FRX" (case insensitive check), fix casing to "frx"
        if clean_pair.startswith("FRX"):
            clean_pair = "frx" + clean_pair[3:]
        # If it's a forex/metal/index without prefix, add "frx"
        elif any(x in clean_pair for x in ["XAU", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF", "US30", "NAS", "GER", "UK100"]):
            clean_pair = "frx" + clean_pair
            
        uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        gran = 300 if interval == "M5" else 86400
        
        try:
            async with websockets.connect(uri) as ws:
                # 4. Auth Loop
                await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
                while True:
                    auth_res = json.loads(await ws.recv())
                    if "authorize" in auth_res: 
                        break 
                    if "error" in auth_res:
                        print(f"❌ Auth Error: {auth_res['error']['message']}")
                        return pd.DataFrame()
                
                # 5. Request Data (FIXED: Added 'end' and 'adjust_start_time')
                await ws.send(json.dumps({
                    "ticks_history": clean_pair,
                    "adjust_start_time": 1,
                    "count": 100,
                    "end": "latest",
                    "style": "candles",
                    "granularity": gran
                }))
                
                res = json.loads(await ws.recv())
                candles = res.get("candles", [])
                
                # 6. Error Trapping
                if not candles:
                    if "error" in res:
                        print(f"⚠️ Deriv Error ({clean_pair}): {res['error']['message']}")
                    else:
                        print(f"⚠️ Deriv: No data returned for {clean_pair} (Market Closed?)")
                    return pd.DataFrame()
                
                # 7. Numeric Conversion
                df = pd.DataFrame(candles)
                return df[['open', 'high', 'low', 'close']].apply(pd.to_numeric)
                
        except Exception as e:
            print(f"❌ Connection Error ({clean_pair}): {e}")
            return pd.DataFrame()
    else:
        # Bybit Engine
        try:
            tf = "5" if interval == "M5" else "D"
            resp = bybit.get_kline(category="linear", symbol=clean_pair, interval=tf, limit=100)
            
            if not resp or 'result' not in resp or 'list' not in resp['result']:
                return pd.DataFrame()

            # Bybit V5 returns: [ts, open, high, low, close, vol, turnover]
            df = pd.DataFrame(resp['result']['list'], columns=['ts','open','high','low','close','vol','turnover'])
            df = df[['open','high','low','close']].apply(pd.to_numeric)
            return df.iloc[::-1] # Reverse to chronological order
        except Exception as e:
            print(f"❌ Bybit Error ({clean_pair}): {e}")
            return pd.DataFrame()

def get_smc_signal(df_l, df_h, pair):
    if df_l.empty or df_h.empty: return None
    # Adjust pip value for JPY pairs and Synthetics
    pip_val = 100 if any(x in pair.upper() for x in ["JPY", "V75", "R_"]) else 10000
    
    # Simple Bias Check
    bias = "BULL" if df_h['close'].iloc[-1] > df_h['close'].iloc[-20] else "BEAR"
    c1, c3 = df_l.iloc[-3], df_l.iloc[-1]
    
    if bias == "BULL" and c3.low > c1.high:
        return {
            "act": "BUY", 
            "e": c3.close, 
            "tp": df_h['high'].max(), 
            "sl": c1.high - (10/pip_val), 
            "be": c3.close + (30/pip_val)
        }
    if bias == "BEAR" and c3.high < c1.low:
        return {
            "act": "SELL", 
            "e": c3.close, 
            "tp": df_h['low'].min(), 
            "sl": c1.low + (10/pip_val), 
            "be": c3.close - (30/pip_val)
        }
    return None

# =====================
# TELEGRAM HANDLERS
# =====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    text = update.message.text.lower().strip()
    users = load_users()
    user = get_user(users, uid)
    state = RUNTIME_STATE.get(uid)

    # 1. Status
    if text == "status": 
        time_diff = int(time.time() - LAST_SCAN_TIME)
        status_text = "🟢 SCANNING" if IS_SCANNING else f"⏳ IDLE ({max(0, user['scan_interval'] - time_diff)}s)"
        await update.message.reply_text(f"🤖 *Status*\nScanner: {status_text}\nPairs: {len(user['pairs'])}\nSession: {user['session']}", parse_mode=ParseMode.MARKDOWN)
    
    # 2. Add
    elif text == "add": 
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Enter Symbol (e.g., BTCUSDT, XAUUSD, US30):")
    
    # 3. Remove
    elif text == "remove": 
        RUNTIME_STATE[uid] = "remove"
        await update.message.reply_text("Symbol to remove:")
    
    # 4. Pairs
    elif text == "pairs": 
        await update.message.reply_text(f"📊 Watchlist: {', '.join(user['pairs']) or 'Empty'}")
    
    # 5. Markets
    elif text == "markets": 
        await update.message.reply_text("📡 Bybit: Crypto | Deriv: Forex/Synthetics")
    
    # 6. Set Session
    elif text == "setsession": 
        RUNTIME_STATE[uid] = "session"
        await update.message.reply_text("Enter LONDON, NY, or BOTH:")
    
    # 7. Set Scan
    elif text == "setscan": 
        RUNTIME_STATE[uid] = "scan"
        await update.message.reply_text("Enter Scan seconds:")
    
    # 8. Set Cooldown
    elif text == "setcooldown": 
        RUNTIME_STATE[uid] = "cooldown"
        await update.message.reply_text("Enter Cooldown minutes:")
    
    # 9. Set Spread
    elif text == "setspread": 
        RUNTIME_STATE[uid] = "spread"
        await update.message.reply_text("Enter Max Spread (e.g. 0.0005):")
    
    # 10. Help
    elif text == "help": 
        await update.message.reply_text("SMC Sniper: M5 FVG entries aligned with Daily Bias.")
    
    # State Processing
    elif state == "add":
        # No need to add prefixes here, the fetch_data function handles it dynamically
        user["pairs"].append(text.upper())
        save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ {text.upper()} added.")
    
    elif state == "remove":
        clean_text = text.upper()
        if clean_text in user["pairs"]: 
            user["pairs"].remove(clean_text)
            save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"🗑 {clean_text} removed.")
        
    elif state == "session":
        user["session"] = text.upper()
        save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Session: {text.upper()}")
        
    elif state == "scan":
        user["scan_interval"] = int(text)
        save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Scan set.")
        
    elif state == "cooldown":
        user["cooldown"] = int(text)
        save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Cooldown set.")
        
    elif state == "spread":
        user["max_spread"] = float(text)
        save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Spread set.")

# =====================
# ENGINE & SCANNER (OPTIMIZED)
# =====================
async def scanner_loop(app):
    global LAST_SCAN_TIME, IS_SCANNING
    while True:
        try:
            IS_SCANNING = True
            LAST_SCAN_TIME = time.time()
            users = load_users()
            
            # --- PHASE 1: PRE-CALCULATION (Loop Inversion) ---
            # Group users by PAIR to minimize API calls.
            pair_map = {}
            
            for uid, settings in users.items():
                if not is_in_session(settings["session"]): continue
                for pair in settings["pairs"]:
                    clean_p = pair.upper()
                    if clean_p not in pair_map:
                        pair_map[clean_p] = []
                    pair_map[clean_p].append(uid)
            
            if pair_map:
                print(f"🔍 [SCAN START] Checking {len(pair_map)} unique pairs...")

            # --- PHASE 2: EFFICIENT SCANNING ---
            for pair, recipients in pair_map.items():
                
                # Market Filter (Skip closed markets to save resources)
                if not is_market_open(pair):
                    continue

                # Exchange Detection for Logs
                deriv_keys = ["FRX", "R_", "V75", "XAU", "EUR", "GBP", "JPY", "US30", "NAS", "GER", "AUD", "CAD"]
                exchange = "DERIV" if any(x in pair.upper() for x in deriv_keys) else "BYBIT"
                
                print(f"  ➡️ Checking {pair} on {exchange}...")
                
                # Fetch Data (ONCE per pair)
                df_l = await fetch_data(pair, "M5")
                df_h = await fetch_data(pair, "1D")
                
                # Get Signal
                sig = get_smc_signal(df_l, df_h, pair)
                
                # --- PHASE 3: BROADCAST ---
                if sig:
                    sig_key = f"{pair}_{sig['e']}"
                    
                    for uid in recipients:
                        # Check if user already got THIS signal
                        last_sent = SENT_SIGNALS.get(f"{uid}_{pair}")
                        
                        if last_sent != sig['e']:
                            msg = (f"🚨 *SMC SIGNAL: {pair}*\n"
                                   f"{sig['act']} @ `{sig['e']}`\n"
                                   f"TP: `{sig['tp']}` | SL: `{sig['sl']}`")
                            try:
                                await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                                SENT_SIGNALS[f"{uid}_{pair}"] = sig['e']
                                print(f"  🎯 Sent {pair} signal to User {uid}")
                            except Exception as e:
                                print(f"  ❌ Failed to send to {uid}: {e}")
                                
            IS_SCANNING = False
            await asyncio.sleep(60)
            
        except Exception as e: 
            print(f"❌ Scanner Loop Error: {e}")
            await asyncio.sleep(10)

async def post_init(app: Application):
    asyncio.get_event_loop().create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Sniper Ready", reply_markup=ReplyKeyboardMarkup([
        [KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")], 
        [KeyboardButton("status"), KeyboardButton("setsession"), KeyboardButton("markets")], 
        [KeyboardButton("setscan"), KeyboardButton("setcooldown"), KeyboardButton("setspread")], 
        [KeyboardButton("help")]
    ], resize_keyboard=True))))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__": 
    main()
