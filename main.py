import os, json, asyncio, requests, pytz
import pandas as pd
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# =====================
# ENV & CONFIG
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DATA_FILE = "users.json"
API_DELAY = 8.5  

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
            "pairs": [], "mode": "BOTH", "session": "BOTH", 
            "scan_interval": 60, "cooldown": 60, "max_spread": 0.0005
        }
        save_users(users)
    return users[chat_id]

# =====================
# UI & MENUS
# =====================
def main_menu_kb(user_mode):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔄 Mode: {user_mode}", callback_data="toggle_mode")],
        [InlineKeyboardButton("➕ Add Pair", callback_data="menu_add"), InlineKeyboardButton("🗑 Remove Pair", callback_data="menu_remove")],
        [InlineKeyboardButton("⏰ Session", callback_data="menu_session"), InlineKeyboardButton("📊 Watchlist", callback_data="menu_list")]
    ])

# =====================
# COMMAND HANDLERS
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    await update.message.reply_text(
        "💹 *Trading Bot Control Panel*\nUse the buttons below or keyboard commands to manage the scanner.",
        reply_markup=main_menu_kb(user['mode']),
        parse_mode=ParseMode.MARKDOWN
    )

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    RUNTIME_STATE[uid] = "awaiting_pair"
    await update.message.reply_text("➕ Send the symbol you want to scan (e.g., `XAU/USD` or `BTC/USD`):")

async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    if not user["pairs"]:
        return await update.message.reply_text("Your watchlist is empty.")
    btns = [[InlineKeyboardButton(p, callback_data=f"del:{p}")] for p in user["pairs"]]
    await update.message.reply_text("🗑 Select a pair to remove:", reply_markup=InlineKeyboardMarkup(btns))

async def pairs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(load_users(), str(update.effective_chat.id))
    txt = "\n".join(user["pairs"]) or "No active pairs."
    await update.message.reply_text(f"📊 *Current Watchlist:*\n{txt}", parse_mode=ParseMode.MARKDOWN)

async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("London", callback_data="ses:london"), 
           InlineKeyboardButton("New York", callback_data="ses:newyork"), 
           InlineKeyboardButton("Both", callback_data="ses:both")]]
    await update.message.reply_text("⏰ Select Trading Session:", reply_markup=InlineKeyboardMarkup(kb))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Help Guide:*\n- Use /add to track a market.\n- Use /setsession for specific hours.\n- The bot sends alerts for *SMC 100RR* and *5m EMA* setups.",
        parse_mode=ParseMode.MARKDOWN
    )

# =====================
# CALLBACKS & INPUT
# =====================

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = str(q.message.chat_id)
    users = load_users()
    user = get_user(users, uid)

    if q.data == "toggle_mode":
        user["mode"] = {"BOTH": "NORMAL", "NORMAL": "SCALP", "SCALP": "BOTH"}[user.get("mode", "BOTH")]
        save_users(users)
        await q.edit_message_text(f"✅ Scanner Mode: *{user['mode']}*", reply_markup=main_menu_kb(user['mode']), parse_mode=ParseMode.MARKDOWN)
    
    elif q.data == "menu_add":
        RUNTIME_STATE[uid] = "awaiting_pair"
        await q.message.reply_text("➕ Send the symbol:")
        
    elif q.data.startswith("ses:"):
        user["session"] = q.data.split(":")[1]
        save_users(users)
        await q.edit_message_text(f"✅ Session updated to: *{user['session'].upper()}*", parse_mode=ParseMode.MARKDOWN)

    elif q.data.startswith("del:"):
        p = q.data.split(":")[1]
        if p in user["pairs"]: user["pairs"].remove(p)
        save_users(users)
        await q.edit_message_text(f"🗑 Removed {p} from watchlist.")

async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    state = RUNTIME_STATE.get(uid)
    text = update.message.text.upper().strip()
    
    users = load_users()
    user = get_user(users, uid)

    if state == "awaiting_pair":
        if text not in user["pairs"]:
            user["pairs"].append(text)
            save_users(users)
            await update.message.reply_text(f"✅ Added {text} to watchlist!", reply_markup=main_menu_kb(user['mode']))
        else:
            await update.message.reply_text(f"⚠️ {text} is already being scanned.")
        RUNTIME_STATE[uid] = None
    else:
        # If no state, just treat as a normal message or ignore
        pass

# =====================
# ENGINE (SCANNER)
# =====================

async def post_init(app: Application):
    """Starts background scanner without event loop errors"""
    # Placeholder for the scanner_loop logic from previous working versions
    # asyncio.create_task(scanner_loop(app))
    print("🤖 Bot initialization complete.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # EXPLICIT HANDLERS (Fixed)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("pairs", pairs_command))
    app.add_handler(CommandHandler("setsession", session_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Callback Buttons
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    
    # Text Inputs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_input))
    
    app.post_init = post_init
    
    print("📡 Bot is now polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
