import os, json, asyncio, requests, pytz
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DATA_FILE = "users.json"
UTC = pytz.UTC
API_DELAY = 8.5 

SENT_SIGNALS = {}

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
        users[chat_id] = {"pairs": [], "mode": "both"}
        save_users(users)
    return users[chat_id]

# =====================
# STRATEGIES
# =====================
def get_extreme_signal(df_ltf, df_htf, symbol):
    if df_ltf.empty or df_htf.empty or len(df_htf) < 50: return None
    last_candle_ts = str(df_ltf['ts'].iloc[-1])
    is_volatile = any(x in symbol for x in ["BTC", "XAU", "XAUT", "ETH"])
    pip_val = 100 if "JPY" in symbol else 10000
    htf_trend = "BULL" if df_htf['close'].iloc[-1] > df_htf['close'].iloc[-20] else "BEAR"
    target_high = df_htf['high'].iloc[-250:].max() 
    target_low = df_htf['low'].iloc[-250:].min()
    c1, c3 = df_ltf.iloc[-3], df_ltf.iloc[-1]
    curr = c3.close
    sl_buffer = 15 / pip_val if is_volatile else 5 / pip_val
    if htf_trend == "BULL" and c3.low > c1.high and target_high > curr:
        return {"action": "BUY", "entry": curr, "tp": target_high, "sl": c1.high - sl_buffer, "be": curr + (30/pip_val), "mode": "Normal (100RR)", "ts": last_candle_ts}
    if htf_trend == "BEAR" and c3.high < c1.low and target_low < curr:
        return {"action": "SELL", "entry": curr, "tp": target_low, "sl": c1.low + sl_buffer, "be": curr - (30/pip_val), "mode": "Normal (100RR)", "ts": last_candle_ts}
    return None

def get_scalping_signal(df, symbol):
    if df.empty or len(df) < 60: return None
    last_candle_ts = str(df['ts'].iloc[-1])
    df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    curr, prev = df.iloc[-1], df.iloc[-2]
    pip_val = 100 if "JPY" in symbol else 10000
    midpoint = (curr.ema10 + curr.ema21) / 2
    if curr.ema50 > prev.ema50 and curr.low <= midpoint <= curr.high:
        return {"action": "BUY", "entry": curr.close, "tp": curr.close + (15/pip_val), "sl": curr.close - (5/pip_val), "mode": "Scalp (5m)", "ts": last_candle_ts}
    if curr.ema50 < prev.ema50 and curr.low <= midpoint <= curr.high:
        return {"action": "SELL", "entry": curr.close, "tp": curr.close - (15/pip_val), "sl": curr.close + (5/pip_val), "mode": "Scalp (5m)", "ts": last_candle_ts}
    return None

# =====================
# UI MENUS
# =====================
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Pair", callback_data="add"), InlineKeyboardButton("🗑 Remove Pair", callback_data="remove")],
        [InlineKeyboardButton("🔄 Toggle Mode", callback_data="toggle"), InlineKeyboardButton("📊 My Pairs", callback_data="list")]
    ])

# =====================
# HANDLERS
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    get_user(load_users(), chat_id)
    await update.message.reply_text(
        "💹 *Trading Bot Control Panel*\nMode: Normal (HTF Swings) + Scalp (5m EMA)",
        reply_markup=main_menu_kb(),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    users = load_users()
    user = get_user(users, chat_id)

    if query.data == "add":
        context.user_data["state"] = "adding"
        await query.message.reply_text("Send the pair you want to scan (e.g. `EUR/USD` or `XAUT/USD`)")

    elif query.data == "list":
        pairs = "\n".join(user["pairs"]) or "No active pairs."
        await query.message.reply_text(f"📊 *Current Watchlist:*\n{pairs}\nMode: {user.get('mode', 'both')}", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())

    elif query.data == "remove":
        if not user["pairs"]:
            await query.message.reply_text("Watchlist is empty.", reply_markup=main_menu_kb())
            return
        btns = [[InlineKeyboardButton(p, callback_data=f"del:{p}")] for p in user["pairs"]]
        await query.message.reply_text("Select a pair to remove:", reply_markup=InlineKeyboardMarkup(btns))

    elif query.data.startswith("del:"):
        pair = query.data.split(":")[1]
        if pair in user["pairs"]:
            user["pairs"].remove(pair)
            save_users(users)
        await query.message.reply_text(f"✅ Removed {pair}", reply_markup=main_menu_kb())

    elif query.data == "toggle":
        curr = user.get("mode", "both")
        modes = {"both": "normal", "normal": "scalp", "scalp": "both"}
        user["mode"] = modes[curr]
        save_users(users)
        await query.message.reply_text(f"🔄 Mode switched to: *{user['mode'].upper()}*", reply_markup=main_menu_kb(), parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if context.user_data.get("state") == "adding":
        pair = update.message.text.upper().strip()
        users = load_users()
        user = get_user(users, chat_id)
        if pair not in user["pairs"]:
            user["pairs"].append(pair)
            save_users(users)
        context.user_data["state"] = None
        await update.message.reply_text(f"✅ Monitoring {pair}", reply_markup=main_menu_kb())

# =====================
# CORE SCANNER & DATA
# =====================
async def fetch_data(symbol, interval, outputsize=100):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={TWELVE_API_KEY}"
    try:
        r = await asyncio.to_thread(requests.get, url, timeout=12)
        d = r.json()
        if "values" not in d: return pd.DataFrame()
        df = pd.DataFrame(d["values"])
        df[['open','high','low','close']] = df[['open','high','low','close']].apply(pd.to_numeric)
        df['ts'] = pd.to_datetime(df['datetime'])
        return df.iloc[::-1]
    except: return pd.DataFrame()

async def scanner_loop(app: Application):
    while True:
        users = load_users()
        for chat_id, settings in users.items():
            mode = settings.get("mode", "both")
            for pair in settings.get("pairs", []):
                key = f"{chat_id}_{pair}"
                if mode in ["normal", "both"]:
                    await asyncio.sleep(API_DELAY)
                    df_h = await fetch_data(pair, "1day", 250)
                    await asyncio.sleep(API_DELAY)
                    df_l = await fetch_data(pair, "5min", 50)
                    sig = get_extreme_signal(df_l, df_h, pair)
                    if sig and SENT_SIGNALS.get(f"{key}_n") != sig['ts']:
                        await send_alert(app, chat_id, pair, sig)
                        SENT_SIGNALS[f"{key}_n"] = sig['ts']
                if mode in ["scalp", "both"]:
                    await asyncio.sleep(API_DELAY)
                    df_5 = await fetch_data(pair, "5min", 75)
                    sig = get_scalping_signal(df_5, pair)
                    if sig and SENT_SIGNALS.get(f"{key}_s") != sig['ts']:
                        await send_alert(app, chat_id, pair, sig)
                        SENT_SIGNALS[f"{key}_s"] = sig['ts']
        await asyncio.sleep(60)

async def send_alert(app, chat_id, pair, sig):
    risk = abs(sig['entry'] - sig['sl'])
    reward = abs(sig['tp'] - sig['entry'])
    rr = reward / risk if risk > 0 else 0
    be_msg = f"\n🛡 *Break-Even Level:* `{sig['be']:.5f}`" if 'be' in sig else ""
    msg = (f"🚨 *{sig['mode']} SIGNAL*\n\nPair: *{pair}*\nAction: *{sig['action']}*\nRR: *1:{rr:.1f}*\n\n"
           f"Entry: `{sig['entry']:.5f}`\nTarget: `{sig['tp']:.5f}`\nStop: `{sig['sl']:.5f}`" + be_msg)
    try: await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)
    except: pass

async def on_startup(app: Application):
    asyncio.create_task(scanner_loop(app))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.post_init = on_startup 
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
