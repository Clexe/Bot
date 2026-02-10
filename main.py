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
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID", "")

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
            "session": "BOTH",
            "mode": "MARKET"
        }
        defaults.update(saved_settings)
        users[uid] = defaults
    return users

def save_user_settings(chat_id, settings):
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
            "session": "BOTH",
            "mode": "MARKET"
        }
        save_user_settings(chat_id, default_settings)
        return default_settings
    return users[chat_id]

# =====================
# 📰 NEWS FILTER (FIXED)
# =====================
def fetch_forex_news():
    global NEWS_CACHE, LAST_NEWS_FETCH
    if time.time() - LAST_NEWS_FETCH < 3600: return 
    
    try:
        # Added User-Agent to prevent blocking
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.xml", headers=headers)
        
        root = ET.fromstring(resp.content)
        events = []
        for event in root.findall('event'):
            impact = event.find('impact').text
            if impact not in NEWS_IMPACT: continue
            
            date = event.find('date').text
            time_str = event.find('time').text
            currency = event.find('country').text
            
            # FIXED DATE FORMAT: %m-%d-%Y (Month-Day-Year)
            if "am" in time_str or "pm" in time_str:
                dt_str = f"{date} {time_str}"
                try:
                    dt_obj = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
                    events.append({"currency": currency, "time": dt_obj})
                except ValueError:
                    continue # Skip bad dates
        
        NEWS_CACHE = events
        LAST_NEWS_FETCH = time.time()
        print(f"📰 Fetched {len(events)} News Events")
    except Exception as e:
        print(f"News Fetch Error: {e}")

def is_news_blackout(pair):
    if not USE_NEWS_FILTER: return False
    fetch_forex_news()
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
            if -30 <= diff <= 30: return True
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
# SMART ROUTER & STRATEGY
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
            raw_sl = swing_low
            limit_entry = swing_high
            market_entry = c3.close
            
            if (limit_entry - raw_sl) > max_risk_price: sl_limit = limit_entry - max_risk_price
            else: sl_limit = raw_sl
            
            if (market_entry - raw_sl) > max_risk_price: sl_market = market_entry - max_risk_price
            else: sl_market = raw_sl

            sig = {
                "act": "BUY", 
                "limit_e": limit_entry, "market_e": market_entry,
                "limit_sl": sl_limit, "market_sl": sl_market,
                "tp": df_h['high'].max()
            }

    if bias == "BEAR" and bearish_bos:
        if c3.high < c1.low:
            raw_sl = swing_high
            limit_entry = swing_low
            market_entry = c3.close
            
            if (raw_sl - limit_entry) > max_risk_price: sl_limit = limit_entry + max_risk_price
            else: sl_limit = raw_sl
            
            if (raw_sl - market_entry) > max_risk_price: sl_market = market_entry + max_risk_price
            else: sl_market = raw_sl

            sig = {
                "act": "SELL", 
                "limit_e": limit_entry, "market_e": market_entry,
                "limit_sl": sl_limit, "market_sl": sl_market,
                "tp": df_h['low'].min()
            }
            
    return sig

# =====================
# COMMAND HANDLERS
# =====================
async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle between MARKET and LIMIT execution for the user."""
    uid = str(update.effective_chat.id)
    users = load_users()
    user = get_user(users, uid)
    
    if user.get("mode") == "MARKET":
        user["mode"] = "LIMIT"
    else:
        user["mode"] = "MARKET"
    
    save_user_settings(uid, user)
    await update.message.reply_text(f"🔄 **Your Mode Updated:** {user['mode']}\n\nLIMIT = Pending Orders (Retest)\nMARKET = Instant Execution", parse_mode=ParseMode.MARKDOWN)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = str(update.effective_user.id)
    if sender_id != ADMIN_ID: return
    if not context.args: return
    message_text = " ".join(context.args)
    users = load_users()
    for uid in list(users.keys()):
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 *ANNOUNCEMENT*\n\n{message_text}", parse_mode=ParseMode.MARKDOWN)
        except: pass

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    text = update.message.text.lower().strip()
    users = load_users() 
    user = get_user(users, uid)
    state = RUNTIME_STATE.get(uid)

    if text == "status": 
        time_diff = int(time.time() - LAST_SCAN_TIME)
        status_text = "🟢 SCANNING" if IS_SCANNING else f"⏳ IDLE ({max(0, user['scan_interval'] - time_diff)}s)"
        await update.message.reply_text(f"🤖 *Status*\nYour Mode: *{user.get('mode', 'MARKET')}*\nPairs: {len(user['pairs'])}\nSession: {user['session']}", parse_mode=ParseMode.MARKDOWN)
    
    elif text == "add": 
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Enter Symbol (e.g. XAUUSD):")
    elif text == "remove": 
        RUNTIME_STATE[uid] = "remove"
        await update.message.reply_text("Symbol to remove:")
    elif text == "pairs": 
        await update.message.reply_text(f"📊 Watchlist: {', '.join(user['pairs']) or 'Empty'}")
    elif text == "setsession": 
        RUNTIME_STATE[uid] = "session"
        await update.message.reply_text("Enter LONDON, NY, or BOTH:")
    elif text == "help": 
        await update.message.reply_text("Commands:\n/mode - Toggle Limit/Market\nadd - Add Pair\nremove - Remove Pair\npairs - View List\nstatus - Check Bot", parse_mode=ParseMode.MARKDOWN)
    
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
                if is_news_blackout(pair): continue
                
                df_l = await fetch_data(pair, "M15") 
                df_h = await fetch_data(pair, "1D")
                
                sig = get_smc_signal(df_l, df_h, pair)
                
                if sig:
                    current_time = time.time()
                    
                    msg_market = (f"🚨 *SMC SIGNAL (MARKET)*\n"
                                  f"Symbol: {pair}\n"
                                  f"Action: *{sig['act']} NOW*\n"
                                  f"Entry: `{sig['market_e']:.5f}`\n"
                                  f"TP: `{sig['tp']:.5f}` | SL: `{sig['market_sl']:.5f}`")
                    
                    msg_limit = (f"🎯 *SMC SIGNAL (LIMIT)*\n"
                                 f"Symbol: {pair}\n"
                                 f"Action: *{sig['act']} LIMIT*\n"
                                 f"Entry: `{sig['limit_e']:.5f}`\n"
                                 f"TP: `{sig['tp']:.5f}` | SL: `{sig['limit_sl']:.5f}`")

                    for uid in recipients:
                        last_info = SENT_SIGNALS.get(f"{uid}_{pair}")
                        user_conf = get_user(users, uid)
                        should_send = False
                        
                        if last_info is None: should_send = True
                        elif isinstance(last_info, dict):
                            if (current_time - last_info['time']) > (user_conf['cooldown'] * 60): should_send = True
                        else: should_send = True

                        if should_send:
                            try:
                                if user_conf.get("mode") == "LIMIT":
                                    final_msg = msg_limit
                                    price_log = sig['limit_e']
                                else:
                                    final_msg = msg_market
                                    price_log = sig['market_e']
                                    
                                await app.bot.send_message(uid, final_msg, parse_mode=ParseMode.MARKDOWN)
                                SENT_SIGNALS[f"{uid}_{pair}"] = {'price': price_log, 'time': current_time}
                                print(f"  🎯 Sent {pair} ({user_conf.get('mode')}) to {uid}")
                            except: pass
                                
            IS_SCANNING = False
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Loop Error: {e}")
            await asyncio.sleep(10)

async def post_init(app: Application):
    init_db()
    asyncio.get_event_loop().create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Sniper V3 Ready", reply_markup=ReplyKeyboardMarkup([
        [KeyboardButton("add"), KeyboardButton("remove"), KeyboardButton("pairs")], 
        [KeyboardButton("status"), KeyboardButton("setsession")],
        [KeyboardButton("help")]
    ], resize_keyboard=True))))
    
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("mode", mode_command)) 
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__": 
    main()
