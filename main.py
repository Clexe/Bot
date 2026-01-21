import os, json, asyncio, requests, websockets
import pandas as pd
from datetime import datetime
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
API_DELAY = 12.0 

SENT_SIGNALS = {}
RUNTIME_STATE = {}

# Initialize Bybit
bybit = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)

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
            "scan_interval": 60, "cooldown": 60
        }
        save_users(users)
    return users[chat_id]

# =====================
# STRATEGY: 100 RR SNIPER
# =====================
def get_sniper_signal(df_lt, df_ht, symbol):
    if df_lt.empty or df_ht.empty: return None
    pip_val = 100 if any(x in symbol for x in ["JPY", "V75", "R_"]) else 10000
    
    # HTF Bias (Daily Trend)
    ht_trend = "BULL" if df_ht['close'].iloc[-1] > df_ht['close'].iloc[-20] else "BEAR"
    tp_buy, tp_sell = df_ht['high'].max(), df_ht['low'].min()
    
    # LTF M5 Entry Logic
    c1, c3 = df_lt.iloc[-3], df_lt.iloc[-1]
    curr = c3.close
    sl_gap = 10 / pip_val

    # Buy Setup: Displacement + TP Sanity
    if ht_trend == "BULL" and c3.low > c1.high and tp_buy > curr:
        return {
            "action": "BUY", "entry": curr, "tp": tp_buy, 
            "sl": c1.high - sl_gap, "be": curr + (30/pip_val), "ts": str(df_lt['ts'].iloc[-1])
        }
    
    # Sell Setup: Displacement + TP Sanity
    if ht_trend == "BEAR" and c3.high < c1.low and tp_sell < curr:
        return {
            "action": "SELL", "entry": curr, "tp": tp_sell, 
            "sl": c1.low + sl_gap, "be": curr - (30/pip_val), "ts": str(df_lt['ts'].iloc[-1])
        }
    return None

# =====================
# EXCHANGE ENGINES
# =====================

async def get_bybit_data(symbol, interval):
    """Fetches Crypto from Bybit"""
    try:
        tf = "5" if interval == "5min" else "D"
        resp = bybit.get_kline(category="linear", symbol=symbol.replace("/", ""), interval=tf, limit=100)
        df = pd.DataFrame(resp['result']['list'], columns=['ts', 'open', 'high', 'low', 'close', 'vol', 'turnover'])
        for col in ['open', 'high', 'low', 'close']: df[col] = pd.to_numeric(df[col])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms')
        return df.iloc[::-1]
    except: return pd.DataFrame()

async def get_deriv_data(symbol, interval):
    """Fetches Synthetic/Forex from Deriv via WebSocket"""
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    gran = 300 if interval == "5min" else 86400
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            await ws.recv()
            await ws.send(json.dumps({
                "ticks_history": symbol, "count": 100, "end": "latest",
                "style": "candles", "granularity": gran
            }))
            res = await ws.recv(); data = json.loads(res)
            if "candles" in data:
                df = pd.DataFrame(data["candles"])
                for col in ['open', 'high', 'low', 'close']: df[col] = pd.to_numeric(df[col])
                df['ts'] = pd.to_datetime(df['epoch'], unit='s')
                return df
            return pd.DataFrame()
    except: return pd.DataFrame()

# =====================
# UI & HANDLERS
# =====================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton("➕ Add Pair"), KeyboardButton("📊 Watchlist")], 
          [KeyboardButton("⚙️ Settings"), KeyboardButton("📖 Help")]]
    await update.message.reply_text("💹 *100 RR Sniper System Online*", 
                                   reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True), 
                                   parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id); text = update.message.text
    users = load_users(); user = get_user(users, uid)
    
    if text == "➕ Add Pair":
        RUNTIME_STATE[uid] = "awaiting_symbol"
        await update.message.reply_text("Send Symbol (e.g. BTCUSDT or R_75):")
    elif text == "📊 Watchlist":
        await update.message.reply_text(f"📊 *Watchlist:*\n" + ("\n".join(user['pairs']) or "Empty"), parse_mode=ParseMode.MARKDOWN)
    elif text == "⚙️ Settings":
        await update.message.reply_text(f"⚙️ *Settings:*\nScan: {user['scan_interval']}s\nMode: {user['mode']}")
    elif RUNTIME_STATE.get(uid) == "awaiting_symbol":
        sym = text.upper().strip()
        if sym not in user["pairs"]: user["pairs"].append(sym); save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ {sym} added!")

# =====================
# SCANNER LOOP
# =====================

async def scanner_loop(app: Application):
    print("🚀 Sniper Scanner Active.")
    while True:
        try:
            users = load_users()
            for uid, settings in users.items():
                for pair in settings["pairs"]:
                    # Smart Routing
                    if any(x in pair for x in ["R_", "V75", "V100", "FRX"]):
                        df_l = await get_deriv_data(pair, "5min")
                        df_h = await get_deriv_data(pair, "1day")
                    else:
                        df_l = await get_bybit_data(pair, "5min")
                        df_h = await get_bybit_data(pair, "1day")
                    
                    sig = get_sniper_signal(df_l, df_h, pair)
                    if sig and SENT_SIGNALS.get(f"{uid}_{pair}") != sig['ts']:
                        msg = (f"🚨 *100 RR SNIPER*\n*{pair}*: {sig['action']}\n\n"
                               f"E: `{sig['entry']}`\nTP: `{sig['tp']}`\n"
                               f"SL: `{sig['sl']}`\n🛡 *BE:* `{sig['be']}`")
                        await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                        SENT_SIGNALS[f"{uid}_{pair}"] = sig['ts']
            await asyncio.sleep(60)
        except Exception as e: print(f"Scanner Error: {e}"); await asyncio.sleep(10)

async def post_init(app: Application):
    asyncio.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.post_init = post_init
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
