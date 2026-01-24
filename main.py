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
from telegram.error import Forbidden, BadRequest
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
DERIV_TOKEN = os.getenv("DERIV_TOKEN")
DERIV_APP_ID = os.getenv("DERIV_APP_ID")
BYBIT_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET = os.getenv("BYBIT_API_SECRET")

# Get the ID from Railway/Environment, or default to empty string if missing
ADMIN_ID = os.getenv("ADMIN_ID", "")
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
            "pairs": ["XAUUSD", "BTCUSD", "V75"], # Default pairs
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
    # Always open markets
    always_open_keys = ["BTC", "ETH", "SOL", "USDT", "R_", "V75", "V10", "V25", "V50", "V100", "1HZ", "BOOM", "CRASH", "JUMP", "STEP"]
    if any(k in clean for k in always_open_keys): return True
    
    # Forex/Commodities Hours (Closed Weekends)
    now = datetime.utcnow()
    weekday = now.weekday() # 0=Mon, 6=Sun
    hour = now.hour
    
    # Close Friday 21:00 UTC
    if weekday == 4 and hour >= 21: return False
    # Closed Saturday
    if weekday == 5: return False
    # Open Sunday 21:00 UTC
    if weekday == 6 and hour < 21: return False
    
    return True

# =====================
# SMART ROUTER & STRATEGY (M15 + MSNR)
# =====================
async def fetch_data(pair, interval):
    raw_pair = pair.replace("/", "").strip()
    clean_pair = raw_pair.upper() 
    deriv_keywords = ["XAU", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF", "R_", "V75", "1S", "FRX", "US30", "NAS", "GER", "UK100", "BOOM", "CRASH", "STEP"]
    is_deriv = any(x in clean_pair for x in deriv_keywords)
    
    if is_deriv:
        # Normalize Deriv Symbol Names
        if clean_pair == "XAUUSD": clean_pair = "frxXAUUSD"
        elif clean_pair == "EURUSD": clean_pair = "frxEURUSD"
        elif clean_pair == "GBPUSD": clean_pair = "frxGBPUSD"
        elif clean_pair == "US30": clean_pair = "OTC_US30" # Example mapping, adjust as needed for Deriv
        
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
        # Bybit Data
        try:
            tf = "15" if interval == "M15" else "D" 
            resp = bybit.get_kline(category="linear", symbol=clean_pair, interval=tf, limit=100)
            if not resp or 'result' not in resp: return pd.DataFrame()
            df = pd.DataFrame(resp['result']['list'], columns=['ts','open','high','low','close','vol','turnover'])
            df = df[['open','high','low','close']].apply(pd.to_numeric)
            return df.iloc[::-1]
        except: return pd.DataFrame()

# === 🧠 MSNR ENGINE (Market Structure + Retest + CLAMPED SL) ===
def get_smc_signal(df_l, df_h, pair):
    if df_l.empty or df_h.empty: return None
    
    # 1. Determine Pip Divisor based on asset class
    clean_pair = pair.upper()
    # If it's Gold, Indices, or Synthetics -> 1 pip is usually 0.1 or 1.0, NOT 0.0001
    if any(x in clean_pair for x in ["JPY", "V75", "R_", "BOOM", "CRASH", "STEP", "XAU", "US30", "NAS", "GER", "US500"]):
        pip_val = 10  
    else:
        pip_val = 10000 # Standard Forex
    
    # 2. Daily Bias
    bias = "BULL" if df_h['close'].iloc[-1] > df_h['close'].iloc[-20] else "BEAR"
    
    # 3. Market Structure (BOS)
    swing_high = df_l['high'].iloc[-23:-3].max()
    swing_low = df_l['low'].iloc[-23:-3].min()
    recent_price_action = df_l['close'].iloc[-5:]
    bullish_bos = recent_price_action.max() > swing_high
    bearish_bos = recent_price_action.min() < swing_low
    
    # 4. Entry Logic (Retest into FVG)
    c1, c3 = df_l.iloc[-3], df_l.iloc[-1]
    
    # Define max risk in price (50 pips)
    max_risk_price = 50 / pip_val 

    if bias == "BULL" and bullish_bos:
        if c3.low > c1.high: 
            entry = c3.close
            raw_sl = swing_low
            
            # --- CLAMP LOGIC START ---
            if (entry - raw_sl) > max_risk_price:
                final_sl = entry - max_risk_price # Clamp to 50 pips
            else:
                final_sl = raw_sl
            # --- CLAMP LOGIC END ---

            return {"act": "BUY", "e": entry, "tp": df_h['high'].max(), "sl": final_sl}

    if bias == "BEAR" and bearish_bos:
        if c3.high < c1.low:
            entry = c3.close
            raw_sl = swing_high
            
            # --- CLAMP LOGIC START ---
            if (raw_sl - entry) > max_risk_price:
                final_sl = entry + max_risk_price # Clamp to 50 pips
            else:
                final_sl = raw_sl
            # --- CLAMP LOGIC END ---

            return {"act": "SELL", "e": entry, "tp": df_h['low'].min(), "sl": final_sl}
            
    return None

# =====================
# ADMIN COMMANDS (NEW)
# =====================

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check Admin ID
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized. You are not the admin.")
        return

    # Check for message
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/broadcast Your Message Here`", parse_mode=ParseMode.MARKDOWN)
        return

    message_text = " ".join(context.args)
    users = load_users()
    count = 0
    blocked_count = 0
    
    status_msg = await update.message.reply_text(f"⏳ Sending to {len(users)} users...")

    for uid in list(users.keys()): # Use list() to avoid runtime changes
        try:
            await context.bot.send_message(
                chat_id=uid, 
                text=f"📢 *ANNOUNCEMENT*\n\n{message_text}", 
                parse_mode=ParseMode.MARKDOWN
            )
            count += 1
            await asyncio.sleep(0.05) # Prevent flood limits
        except Forbidden:
            # User blocked the bot
            blocked_count += 1
        except Exception as e:
            print(f"Failed to send to {uid}: {e}")

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id, 
        message_id=status_msg.message_id, 
        text=f"✅ Broadcast Complete.\n\nsent: {count}\n🚫 Blocked: {blocked_count}"
    )

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check Admin ID
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return

    users = load_users()
    active_pairs = 0
    for u in users.values():
        active_pairs += len(u.get("pairs", []))

    msg = (
        f"👥 *USER STATISTICS*\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 Total Users: `{len(users)}`\n"
        f"📊 Total Pairs Watched: `{active_pairs}`\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

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
    
    # 2. Add/Remove/Pairs
    elif text == "add": 
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Enter Symbol (e.g. XAUUSD):")
    elif text == "remove": 
        RUNTIME_STATE[uid] = "remove"
        await update.message.reply_text("Symbol to remove:")
    elif text == "pairs": 
        await update.message.reply_text(f"📊 Watchlist: {', '.join(user['pairs']) or 'Empty'}")

    # 3. Settings
    elif text == "markets": 
        await update.message.reply_text("📡 Bybit: Crypto | Deriv: Forex/Synthetics")
    elif text == "setsession": 
        RUNTIME_STATE[uid] = "session"
        await update.message.reply_text("Enter LONDON, NY, or BOTH:")
    elif text == "setscan": 
        RUNTIME_STATE[uid] = "scan"
        await update.message.reply_text("Enter Scan seconds:")
    elif text == "setcooldown": 
        RUNTIME_STATE[uid] = "cooldown"
        await update.message.reply_text("Enter Cooldown minutes:")
    elif text == "setspread": 
        RUNTIME_STATE[uid] = "spread"
        await update.message.reply_text("Enter Max Spread (e.g. 0.0005):")
        
    # 4. HELP
    elif text == "help": 
        help_msg = (
            "📚 *SMC SNIPER GUIDE*\n\n"
            "🟢 *Quick Start:*\n"
            "1. Type `add` -> Enter Symbol (e.g. `XAUUSD`)\n"
            "2. Type `status` -> Check if scanner is running\n\n"
            "⚙️ *Commands:*\n"
            "• `add` - Add a pair to watchlist\n"
            "• `remove` - Remove a pair\n"
            "• `pairs` - View your active list\n"
            "• `status` - Check connection & session\n"
            "• `setsession` - Filter by London/NY hours\n\n"
            "🧠 *Strategy:* M15 MSNR (Daily Bias + BOS + Retest)\n"
            "🛡️ *Safety:* Max Risk capped at 50 pips."
        )
        await update.message.reply_text(help_msg, parse_mode=ParseMode.MARKDOWN)
    
    # 5. State Processing
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
            
            if pair_map: print(f"🔍 [SCAN] Checking {len(pair_map)} unique pairs...")

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
    
    # User Commands
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Sniper Ready", reply_markup=ReplyKeyboardMarkup([
        [KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")], 
        [KeyboardButton("status"), KeyboardButton("setsession"), KeyboardButton("markets")], 
        [KeyboardButton("help")]
    ], resize_keyboard=True))))
    
    # Admin Commands
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("users", users_command))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__": 
    main()
