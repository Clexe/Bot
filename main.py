import os, json, asyncio, websockets, time
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
        users[chat_id] = {"pairs": [], "scan_interval": 60, "max_spread": 0.0005}
        save_users(users)
    return users[chat_id]

# =====================
# EXCHANGE ENGINES
# =====================
async def get_bybit_data(symbol, interval="5"):
    try:
        # Fetches 100 candles (M5 or Daily)
        resp = bybit.get_kline(category="linear", symbol=symbol.replace("/", ""), interval=interval, limit=100)
        df = pd.DataFrame(resp['result']['list'], columns=['ts','o','h','l','c','v','t'])
        for col in ['o','h','l','c']: df[col] = pd.to_numeric(df[col])
        df.rename(columns={'o':'open','h':'high','l':'low','c':'close'}, inplace=True)
        return df.iloc[::-1] # Reverse to correct order
    except: return pd.DataFrame()

async def get_deriv_data(symbol, interval=300):
    uri = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    try:
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            await ws.recv()
            await ws.send(json.dumps({"ticks_history": symbol, "count": 100, "end": "latest", "style": "candles", "granularity": interval}))
            res = await ws.recv(); data = json.loads(res)
            if "candles" in data:
                df = pd.DataFrame(data["candles"])
                for col in ['open','high','low','close']: df[col] = pd.to_numeric(df[col])
                return df
            return pd.DataFrame()
    except: return pd.DataFrame()

# =====================
# SMC STRATEGY LOGIC
# =====================
def get_sniper_signal(df_lt, df_ht, symbol):
    if df_lt.empty or df_ht.empty: return None
    pip_val = 100 if any(x in symbol for x in ["JPY", "V75", "R_"]) else 10000
    
    # 1. HTF Daily Bias (SMC Orderflow)
    ht_trend = "BULL" if df_ht['close'].iloc[-1] > df_ht['close'].iloc[-20] else "BEAR"
    tp_buy, tp_sell = df_ht['high'].max(), df_ht['low'].min()
    
    # 2. LTF M5 FVG Detection
    c1, c2, c3 = df_lt.iloc[-3], df_lt.iloc[-2], df_lt.iloc[-1]
    curr = c3.close
    sl_gap = 10 / pip_val

    # Buy: Bullish FVG + Trend Alignment + TP Sanity Check
    if ht_trend == "BULL" and c3.low > c1.high and tp_buy > curr:
        return {"action": "BUY", "entry": curr, "tp": tp_buy, "sl": c1.high - sl_gap, "be": curr + (30/pip_val)}
    
    # Sell: Bearish FVG + Trend Alignment + TP Sanity Check
    if ht_trend == "BEAR" and c3.high < c1.low and tp_sell < curr:
        return {"action": "SELL", "entry": curr, "tp": tp_sell, "sl": c1.low + sl_gap, "be": curr - (30/pip_val)}
    return None

# =====================
# TELEGRAM UI
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(load_users(), str(update.effective_chat.id))
    kb = [[KeyboardButton("/add"), KeyboardButton("/remove"), KeyboardButton("/pairs")],
          [KeyboardButton("/status"), KeyboardButton("/setscan"), KeyboardButton("/help")]]
    await update.message.reply_text("💹 *SMC Sniper Terminal Active*", 
                                   reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True), 
                                   parse_mode=ParseMode.MARKDOWN)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id); text = update.message.text
    users = load_users(); user = get_user(users, uid); state = RUNTIME_STATE.get(uid)

    if text == "/status":
        await update.message.reply_text(f"🤖 *Status:* Online\n📡 *Scanning:* {len(user['pairs'])} pairs")
    elif text == "/add":
        RUNTIME_STATE[uid] = "add"
        await update.message.reply_text("Send Symbol (e.g. BTCUSDT):")
    elif state == "add":
        pair = text.upper().strip()
        if pair not in user["pairs"]: user["pairs"].append(pair); save_users(users)
        RUNTIME_STATE[uid] = None
        await update.message.reply_text(f"✅ {pair} Added.")

# =====================
# SCANNER LOOP & DEPLOYMENT
# =====================
async def scanner_loop(app):
    print("🚀 Scanner Engine Started...")
    while True:
        try:
            users = load_users()
            for uid, settings in users.items():
                for pair in settings["pairs"]:
                    if any(x in pair for x in ["R_", "V75"]):
                        df_l = await get_deriv_data(pair, 300)
                        df_h = await get_deriv_data(pair, 86400)
                    else:
                        df_l = await get_bybit_data(pair, "5")
                        df_h = await get_bybit_data(pair, "D")
                    
                    sig = get_sniper_signal(df_l, df_h, pair)
                    if sig and SENT_SIGNALS.get(f"{uid}_{pair}") != sig['entry']:
                        msg = (f"🚨 *SMC SNIPER: {pair}*\nAction: {sig['action']}\n\n"
                               f"E: `{sig['entry']}`\nTP: `{sig['tp']}`\nSL: `{sig['sl']}`\n🛡 *BE:* `{sig['be']}`")
                        await app.bot.send_message(uid, msg, parse_mode=ParseMode.MARKDOWN)
                        SENT_SIGNALS[f"{uid}_{pair}"] = sig['entry']
            await asyncio.sleep(60)
        except Exception as e: print(f"Error: {e}"); await asyncio.sleep(10)

async def post_init(app: Application):
    loop = asyncio.get_event_loop()
    loop.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = post_init
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()
