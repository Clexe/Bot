import os
import json
import asyncio
import websockets
import time
import requests
import psycopg2
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
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

# 🚀 DATABASE CONNECTION
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID", "")

# Signal Mode: "MARKET" (Instant) or "LIMIT" (Retest Entry)
SIGNAL_MODE = os.getenv("SIGNAL_MODE", "MARKET").upper() 

# News Settings
USE_NEWS_FILTER = True
NEWS_IMPACT = ["High", "Medium"] 

# Initialize Bybit
bybit = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)

# Global State
SENT_SIGNALS = {} 
RUNTIME_STATE = {}
LAST_SCAN_TIME = 0
IS_SCANNING = False
NEWS_CACHE = []
LAST_NEWS_FETCH = 0

# =====================
# 🗄️ DATABASE ENGINE
# =====================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    """Create table if it doesn't exist."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                settings JSONB DEFAULT '{}'::jsonb,
                is_active BOOLEAN DEFAULT TRUE,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database Table Ready")
    except Exception as e:
        print(f"❌ DB Init Error: {e}")

def load_users():
    """Fetch all active users."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, settings FROM users WHERE is_active = TRUE")
    rows = cur.fetchall()
    conn.close()
    
    users = {}
    for r in rows:
        uid = str(r[0])
        saved_settings = r[1] if r[1] else {}
        defaults = {
            "pairs": ["XAUUSD", "BTCUSD", "V75"], 
            "scan_interval": 60, 
            "cooldown": 60,
            "max_spread": 0.0005,
            "session": "BOTH"
        }
        defaults.update(saved_settings)
        users[uid] = defaults
    return users

def save_user_settings(chat_id, settings):
    """Save/Update user settings."""
    conn = get_db_connection()
    cur = conn.cursor()
    json_settings = json.dumps(settings)
    cur.execute("""
        INSERT INTO users (user_id, settings, is_active) 
        VALUES (%s, %s, TRUE)
        ON CONFLICT (user_id) 
        DO UPDATE SET settings = %s, is_active = TRUE;
    """, (chat_id, json_settings, json_settings))
    conn.commit()
    conn.close()

def get_user(users, chat_id):
    chat_id = str(chat_id)
    if chat_id not in users:
        default_settings = {
            "pairs": ["XAUUSD", "BTCUSD", "V75"], 
            "scan_interval": 60, 
            "cooldown": 60,
            "max_spread": 0.0005,
            "session": "BOTH"
        }
        save_user_settings(chat_id, default_settings)
        return default_settings
    return users[chat_id]

# =====================
# 📰 NEWS FILTER
# =====================
def fetch_forex_news():
    global NEWS_CACHE, LAST_NEWS_FETCH
    if time.time() - LAST_NEWS_FETCH < 3600: return # Cache for 1 hour
    
    try:
        resp = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.xml")
        root = ET.fromstring(resp.content)
        events = []
        for event in root.findall('event'):
            impact = event.find('impact').text
            if impact not in NEWS_IMPACT: continue
            
            date = event.find('date').text
            time_str = event.find('time').text
            currency = event.find('country').text
            
            if "am" in time_str or "pm" in time_str:
                dt_str = f"{date} {time_str}"
                dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %I:%M%p")
                events.append({"currency": currency, "time": dt_obj})
        
        NEWS_CACHE = events
        LAST_NEWS_FETCH = time.time()
        print(f"📰 Fetched {len(events)} High Impact News Events")
    except Exception as e:
        print(f"News Fetch Error: {e}")

def is_news_blackout(pair):
    if not USE_NEWS_FILTER: return False
    fetch_forex_news()
    
    # Map pair to currency
    currencies = []
    if "USD" in pair: currencies.append("USD")
    if "EUR" in pair: currencies.append("EUR")
    if "GBP" in pair: currencies.append("GBP")
    if "JPY" in pair: currencies.append("JPY")
    if "XAU" in pair: currencies.append("USD")
    
    now = datetime.utcnow()
    for event in NEWS_CACHE:
        if event['currency'] in currencies:
            diff = (event['time'] - now).total_seconds() / 60
            if -30 <= diff <= 30: # 30 min blackout
                return True
    return False

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
    deriv_keywords = ["XAU", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF", "R_", "V75", "1S", "FRX", "US30", "NAS", "GER", "UK100", "BOOM", "CRASH", "STEP"]
    is_deriv = any(x in clean_pair for x in deriv_keywords)
    
    if is_deriv:
        if clean_pair == "XAUUSD": clean_pair = "frxXAUUSD"
        elif clean_pair == "EURUSD": clean_pair = "frxEURUSD"
        elif clean_pair == "GBPUSD": clean_pair = "frxGBPUSD"
        elif clean_pair == "US30": clean_pair = "OTC_US30"
        
        uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
        gran = 900 if interval == "M15" else 86400 
        
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
            tf = "15" if interval == "M15" else "D" 
            resp = bybit.get_kline(category="linear", symbol=clean_pair, interval=tf, limit=100)
            if not resp or 'result' not in resp: return pd.DataFrame()
            df = pd.DataFrame(resp['result']['list'], columns=['ts','open','high','low','close','vol','turnover'])
            df = df[['open','high','low','close']].apply(pd.to_numeric)
            return df.iloc[::-1]
        except: return pd.DataFrame()

def get_smc_signal(df_l, df_h, pair):
    if df_l.empty or df_h.empty: return None
    
    clean_pair = pair.upper()
    if any(x in clean_pair for x in ["JPY", "V75", "R_", "BOOM", "CRASH", "STEP", "XAU", "US30", "NAS", "GER", "US500"]):
        pip_val = 10  
    else:
        pip_val = 10000 
    
    bias = "BULL" if df_h['close'].iloc[-1] > df_h['close'].iloc[-20] else "BEAR"
    
    swing_high = df_l['high'].iloc[-23:-3].max()
    swing_low = df_l['low'].iloc[-23:-3].min()
    recent_price_action = df_l['close'].iloc[-5:]
    bullish_bos = recent_price_action.max() > swing_high
    bearish_bos = recent_price_action.min() < swing_low
    
    c1, c3 = df_l.iloc[-3], df_l.iloc[-1]
    max_risk_price = 50 / pip_val 

    sig = None

    if bias == "BULL" and bullish_bos:
        if c3.low > c1.high: 
            # 🚀 LIMIT MODE LOGIC
            if SIGNAL_MODE == "LIMIT":
                entry = swing_high # Retest of broken resistance
                type_str = "LIMIT"
            else:
                entry = c3.close # Market Execution
                type_str = "MARKET"
            
            raw_sl = swing_low
            # Clamp SL
            if (entry - raw_sl) > max_risk_price: final_sl = entry - max_risk_price
            else: final_sl = raw_sl
            
            sig = {"act": "BUY", "type": type_str, "e": entry, "tp": df_h['high'].max(), "sl": final_sl}

    if bias == "BEAR" and bearish_bos:
        if c3.high < c1.low:
            if SIGNAL_MODE == "LIMIT":
                entry = swing_low # Retest of broken support
                type_str = "LIMIT"
            else:
                entry = c3.close
                type_str = "MARKET"
            
            raw_sl = swing_high
            # Clamp SL
            if (raw_sl - entry) > max_risk_price: final_sl = entry + max_risk_price
            else: final_sl = raw_sl

            sig = {"act": "SELL", "type": type_str, "e": entry, "tp": df_h['low'].min(), "sl": final_sl}
            
    return sig

# =====================
# ADMIN COMMANDS
# =====================
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/broadcast Message`")
        return

    message_text = " ".join(context.args)
    users = load_users() # Loads from DB
    count = 0
    blocked_count = 0
    
    status_msg = await update.message.reply_text(f"⏳ Sending to {len(users)} users...")

    for uid in list(users.keys()):
        try:
            await context.bot.send_message(
                chat_id=uid, 
                text=f"📢 *ANNOUNCEMENT*\n\n{message_text}", 
                parse_mode=ParseMode.MARKDOWN
            )
            count += 1
            await asyncio.sleep(0.05)
        except Forbidden:
            blocked_count += 1
        except Exception as e:
            print(f"Failed to send to {uid}: {e}")

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id, 
        message_id=status_msg.message_id, 
        text=f"✅ Broadcast Complete.\n\nsent: {count}\n🚫 Blocked: {blocked_count}"
    )

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID: return
    users = load_users()
    active_pairs = sum([len(u.get("pairs", [])) for u in users.values()])
    await update.message.reply_text(f"👥 Users: `{len(users)}` | Pairs: `{active_pairs}`", parse_mode=ParseMode.MARKDOWN)

# =====================
# TELEGRAM HANDLERS
# =====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    text = update.message.text.lower().strip()
    
    users = load_users() # Checks DB
    user = get_user(users, uid)
    state = RUNTIME_STATE.get(uid)

    if text == "status": 
        time_diff = int(time.time() - LAST_SCAN_TIME)
        status_text = "🟢 SCANNING" if IS_SCANNING else f"⏳ IDLE ({max(0, user['scan_interval'] - time_diff)}s)"
        await update.message.reply_text(f"🤖 *Status*\nScanner: {status_text}\nPairs: {len(user['pairs'])}\nSession: {user['session']}", parse_mode=ParseMode.MARKDOWN)
    
    elif text == "add": 
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Enter Symbol (e.g. XAUUSD):")
    elif text == "remove": 
        RUNTIME_STATE[uid] = "remove"
        await update.message.reply_text("Symbol to remove:")
    elif text == "pairs": 
        await update.message.reply_text(f"📊 Watchlist: {', '.join(user['pairs']) or 'Empty'}")
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
        await update.message.reply_text("Enter Max Spread:")
    elif text == "help": 
        await update.message.reply_text("Commands: add, remove, pairs, status, setsession", parse_mode=ParseMode.MARKDOWN)
    
    # State Processing (Saves to DB)
    elif state == "add":
        if text.upper() not in user["pairs"]:
            user["pairs"].append(text.upper())
            save_user_settings(uid, user)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ {text.upper()} added.")
    elif state == "remove":
        clean_text = text.upper()
        if clean_text in user["pairs"]: 
            user["pairs"].remove(clean_text)
            save_user_settings(uid, user)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"🗑 {clean_text} removed.")
    elif state == "session":
        user["session"] = text.upper()
        save_user_settings(uid, user)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Session: {text.upper()}")
    elif state == "scan":
        user["scan_interval"] = int(text)
        save_user_settings(uid, user)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Scan set.")
    elif state == "cooldown":
        user["cooldown"] = int(text)
        save_user_settings(uid, user)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ Cooldown set.")
    elif state == "spread":
        user["max_spread"] = float(text)
        save_user_settings(uid, user)
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
            users = load_users() # Fetch from DB
            
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
                if is_news_blackout(pair):
                    print(f"⏸️ News Blackout for {pair}")
                    continue
                
                df_l = await fetch_data(pair, "M15") 
                df_h = await fetch_data(pair, "1D")
                
                sig = get_smc_signal(df_l, df_h, pair)
                
                if sig:
                    current_time = time.time()
                    msg = (f"🚨 *SMC SIGNAL ({sig['type']})*\n"
                           f"Symbol: {pair}\n"
                           f"Setup: MSNR (BOS + Retest)\n"
                           f"Action: *{sig['act']}*\n"
                           f"Entry: `{sig['e']:.5f}`\n"
                           f"TP: `{sig['tp']:.5f}` | SL: `{sig['sl']:.5f}`")

                    for uid in recipients:
                        last_info = SENT_SIGNALS.get(f"{uid}_{pair}")
                        should_send = False
                        if last_info is None: should_send = True
                        elif isinstance(last_info, dict):
                            if (current_time - last_info['time']) > (get_user(users, uid)['cooldown'] * 60): should_send = True
                        else: should_send = True

                        if should_send:
                            try:
                                await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                                SENT_SIGNALS[f"{uid}_{pair}"] = {'price': sig['e'], 'time': current_time}
                                print(f"  🎯 Sent {pair} signal to User {uid}")
                            except: pass
                                
            IS_SCANNING = False
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Loop Error: {e}")
            await asyncio.sleep(10)

async def post_init(app: Application):
    init_db() # 🚀 Initialize Table on Startup
    asyncio.get_event_loop().create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Sniper V2 Ready", reply_markup=ReplyKeyboardMarkup([
        [KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")], 
        [KeyboardButton("status"), KeyboardButton("setsession"), KeyboardButton("markets")], 
        [KeyboardButton("help")]
    ], resize_keyboard=True))))
    
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("users", users_command))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__": 
    main()
