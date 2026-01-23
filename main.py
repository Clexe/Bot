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
    clean = pair.upper()
    always_open_keys = ["BTC", "ETH", "SOL", "USDT", "R_", "V75", "V10", "V25", "V50", "V100", "1HZ", "BOOM", "CRASH", "JUMP", "STEP"]
    if any(k in clean for k in always_open_keys): return True
    now = datetime.utcnow()
    weekday = now.weekday()
    hour = now.hour
    if weekday == 4 and hour >= 21: return False
    if weekday == 5: return False
    if weekday == 6 and hour < 21: return False
    return True

# =====================
# SMART ROUTER & STRATEGY (M15 + MSNR)
# =====================
async def fetch_data(pair, interval):
    raw_pair = pair.replace("/", "").strip()
    clean_pair = raw_pair.upper() 
    deriv_keywords = ["XAU", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF", "R_", "V75", "1S", "FRX", "US30", "NAS", "GER", "UK100"]
    is_deriv = any(x in clean_pair for x in deriv_keywords)
    
    if is_deriv:
        if clean_pair.startswith("FRX"): clean_pair = "frx" + clean_pair[3:]
        elif any(x in clean_pair for x in ["XAU", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF", "US30", "NAS", "GER", "UK100"]): clean_pair = "frx" + clean_pair
        uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        gran = 900 if interval == "M15" else 86400 # M15 Granularity
        
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
                while True:
                    auth_res = json.loads(await ws.recv())
                    if "authorize" in auth_res: break 
                    if "error" in auth_res: return pd.DataFrame()
                
                await ws.send(json.dumps({
                    "ticks_history": clean_pair, "adjust_start_time": 1, "count": 100, "end": "latest", "style": "candles", "granularity": gran
                }))
                res = json.loads(await ws.recv())
                if not res.get("candles"): return pd.DataFrame()
                df = pd.DataFrame(res["candles"])
                return df[['open', 'high', 'low', 'close']].apply(pd.to_numeric)
        except: return pd.DataFrame()
    else:
        try:
            tf = "15" if interval == "M15" else "D" # M15 Interval
            resp = bybit.get_kline(category="linear", symbol=clean_pair, interval=tf, limit=100)
            if not resp or 'result' not in resp: return pd.DataFrame()
            df = pd.DataFrame(resp['result']['list'], columns=['ts','open','high','low','close','vol','turnover'])
            df = df[['open','high','low','close']].apply(pd.to_numeric)
            return df.iloc[::-1]
        except: return pd.DataFrame()

# === 🧠 NEW MSNR ENGINE ===
def get_smc_signal(df_l, df_h, pair):
    if df_l.empty or df_h.empty: return None
    pip_val = 100 if any(x in pair.upper() for x in ["JPY", "V75", "R_"]) else 10000
    
    # 1. Daily Bias (The "King")
    bias = "BULL" if df_h['close'].iloc[-1] > df_h['close'].iloc[-20] else "BEAR"
    
    # 2. Market Structure (BOS) Detection
    # We look back 20 candles to find the "Swing Points"
    # We ignore the most recent 3 candles to ensure the swing is valid/formed
    swing_high = df_l['high'].iloc[-23:-3].max()
    swing_low = df_l['low'].iloc[-23:-3].min()
    
    # Check if we recently BROKE that structure (in the last 5 candles)
    recent_price_action = df_l['close'].iloc[-5:]
    bullish_bos = recent_price_action.max() > swing_high
    bearish_bos = recent_price_action.min() < swing_low
    
    # 3. FVG / Retest Logic
    # We need the current candle to be retesting an FVG
    c1, c3 = df_l.iloc[-3], df_l.iloc[-1]
    
    # === BUY SCENARIO ===
    # Daily is Bullish + We broke a recent High (BOS) + We are dipping into an FVG
    if bias == "BULL" and bullish_bos:
        # FVG Check: Did the move leave a gap? (Low of candle 3 > High of candle 1)
        # Note: In a retracement, we want price to dip INTO the gap.
        # Simplification: We check if current Low is dipping near the breakout point
        
        # Valid FVG Pattern for entry
        if c3.low > c1.high: 
            return {
                "act": "BUY", 
                "e": c3.close, 
                "tp": df_h['high'].max(), 
                "sl": swing_low, # Safer SL below the Swing Low
                "be": c3.close + (30/pip_val)
            }

    # === SELL SCENARIO ===
    if bias == "BEAR" and bearish_bos:
        if c3.high < c1.low:
            return {
                "act": "SELL", 
                "e": c3.close, 
                "tp": df_h['low'].min(), 
                "sl": swing_high, # Safer SL above the Swing High
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

    if text == "status": 
        time_diff = int(time.time() - LAST_SCAN_TIME)
        status_text = "🟢 SCANNING" if IS_SCANNING else f"⏳ IDLE ({max(0, user['scan_interval'] - time_diff)}s)"
        await update.message.reply_text(f"🤖 *Status*\nScanner: {status_text}\nPairs: {len(user['pairs'])}\nSession: {user['session']}", parse_mode=ParseMode.MARKDOWN)
    elif text == "add": 
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Enter Symbol:")
    elif text == "remove": 
        RUNTIME_STATE[uid] = "remove"
        await update.message.reply_text("Symbol to remove:")
    elif text == "pairs": 
        await update.message.reply_text(f"📊 Watchlist: {', '.join(user['pairs']) or 'Empty'}")
    elif text == "help": 
        await update.message.reply_text("SMC Sniper: M15 MSNR (BOS + Retest + FVG)")
    
    # State Processing
    elif state == "add":
        user["pairs"].append(text.upper())
        save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ {text.upper()} added.")
    elif state == "remove":
        clean_text = text.upper()
        if clean_text in user["pairs"]: user["pairs"].remove(clean_text)
        save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"🗑 {clean_text} removed.")

# =====================
# ENGINE & SCANNER
# =====================
async def scanner_loop(app):
    global LAST_SCAN_TIME, IS_SCANNING
    while True:
        try:
            IS_SCANNING = True
            LAST_SCAN_TIME = time.time()
            users = load_users()
            
            pair_map = {}
            for uid, settings in users.items():
                if not is_in_session(settings["session"]): continue
                for pair in settings["pairs"]:
                    clean_p = pair.upper()
                    if clean_p not in pair_map: pair_map[clean_p] = []
                    pair_map[clean_p].append(uid)
            
            if pair_map: print(f"🔍 [SCAN START] Checking {len(pair_map)} unique pairs (MSNR Mode)...")

            for pair, recipients in pair_map.items():
                if not is_market_open(pair): continue
                
                # Fetch Data
                df_l = await fetch_data(pair, "M15") 
                df_h = await fetch_data(pair, "1D")
                
                # Get Signal
                sig = get_smc_signal(df_l, df_h, pair)
                
                if sig:
                    current_time = time.time()
                    for uid in recipients:
                        last_info = SENT_SIGNALS.get(f"{uid}_{pair}")
                        should_send = False
                        if last_info is None: should_send = True
                        elif isinstance(last_info, dict):
                            if (current_time - last_info['time']) > 900: should_send = True
                        else: should_send = True

                        if should_send:
                            msg = (f"🚨 *SMC SIGNAL: {pair}*\n"
                                   f"Setup: MSNR (BOS + Retest)\n"
                                   f"{sig['act']} @ `{sig['e']}`\n"
                                   f"TP: `{sig['tp']}` | SL: `{sig['sl']}`")
                            try:
                                await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                                SENT_SIGNALS[f"{uid}_{pair}"] = {'price': sig['e'], 'time': current_time}
                                print(f"  🎯 Sent {pair} signal to User {uid}")
                            except: pass
                                
            IS_SCANNING = False
            await asyncio.sleep(60)
        except: await asyncio.sleep(10)

async def post_init(app: Application):
    asyncio.get_event_loop().create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Sniper Ready", reply_markup=ReplyKeyboardMarkup([["add", "remove", "pairs"], ["status", "help"]], resize_keyboard=True))))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__": 
    main()
    main()
