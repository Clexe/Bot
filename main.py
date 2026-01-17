import os
import json
import asyncio
import requests
from datetime import datetime, time, timedelta
from typing import Dict, Any, Optional, Tuple

import pytz
import pandas as pd

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DEBUG = os.getenv("DEBUG", "0") == "1"
UTC = pytz.UTC

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not TWELVE_API_KEY:
    raise RuntimeError("Missing TWELVE_API_KEY")

TWELVE_BASE = "https://api.twelvedata.com"

# =========================
# STORAGE
# =========================
DATA_FILE = "users.json"

DEFAULT_USER = {
    "enabled": True,

    # Twelve Data symbols
    "pairs_crypto": ["BTC/USD", "ETH/USD", "SOL/USD"],
    "pairs_forex": ["EUR/USD", "GBP/USD", "USD/JPY"],

    "session": "both",  # london | ny | both
    "scan_interval_sec": 60,
    "cooldown_min": 90,
    "fvg_lookback": 80,

    "killzones_utc": {
        "london": ["07:00", "10:00"],
        "ny": ["12:00", "15:00"]
    },

    "pair_config": {
        "BTC/USD": {"rr": 2.3, "fvg_min_pct": 0.08, "near_entry_pct": 0.25, "max_spread_pct": 0.20},
        "ETH/USD": {"rr": 2.1, "fvg_min_pct": 0.10, "near_entry_pct": 0.30, "max_spread_pct": 0.25},
        "SOL/USD": {"rr": 2.4, "fvg_min_pct": 0.14, "near_entry_pct": 0.45, "max_spread_pct": 0.35},
        "XAU/USD": {"rr": 2.0, "fvg_min_pct": 0.20, "near_entry_pct": 0.25, "max_spread_pct": 0.30},  # Gold
        "XAG/USD": {"rr": 2.1, "fvg_min_pct": 0.20, "near_entry_pct": 0.30, "max_spread_pct": 0.30},  # Silver
        "EUR/USD": {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.08},
        "GBP/USD": {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.10},
        "USD/JPY": {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.10},
    },

    "defaults": {
        "crypto": {"rr": 2.2, "fvg_min_pct": 0.10, "near_entry_pct": 0.30, "max_spread_pct": 0.30},
        "forex":  {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.10},
    }
}

# Runtime state per user (cooldowns + "what input are we expecting next")
RUNTIME_STATE: Dict[str, Dict[str, Any]] = {}

# Twelve Data caches
TWELVE_CACHE = {
    "forex_pairs": set(),
    "crypto_pairs": set(),
    "last_refresh": None
}

# =========================
# UTIL
# =========================
def log(*args):
    if DEBUG:
        print("[DEBUG]", *args)

def now_utc():
    return datetime.now(UTC)

async def to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

def _load_users() -> Dict[str, Any]:
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def _save_users(users: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w") as f:
        json.dump(users, f, indent=2)

def get_user(users: Dict[str, Any], chat_id: str) -> Dict[str, Any]:
    if chat_id not in users:
        users[chat_id] = json.loads(json.dumps(DEFAULT_USER))  # deep copy
        _save_users(users)
    RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}, "awaiting": None})
    return users[chat_id]

def set_awaiting(chat_id: str, kind: Optional[str], meta: Optional[Dict[str, Any]] = None):
    RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}, "awaiting": None})
    RUNTIME_STATE[chat_id]["awaiting"] = {"kind": kind, "meta": meta or {}}

def get_awaiting(chat_id: str) -> Optional[Dict[str, Any]]:
    return RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}, "awaiting": None}).get("awaiting")

def clear_awaiting(chat_id: str):
    RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}, "awaiting": None})
    RUNTIME_STATE[chat_id]["awaiting"] = None

# =========================
# TWELVE DATA CLIENT
# =========================
def twelve_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(params)
    params["apikey"] = TWELVE_API_KEY
    url = f"{TWELVE_BASE}/{path.lstrip('/')}"
    r = requests.get(url, params=params, timeout=12)
    r.raise_for_status()
    return r.json()

async def twelve_refresh_markets(force: bool = False) -> bool:
    now = now_utc()
    last = TWELVE_CACHE["last_refresh"]
    if (not force) and last and (now - last) < timedelta(hours=6) and TWELVE_CACHE["forex_pairs"]:
        return True

    def _fetch():
        fx = twelve_get("forex_pairs", {})
        fx_set = set()
        for item in fx.get("data", []):
            sym = (item.get("symbol") or "").upper()
            if "/" in sym:
                fx_set.add(sym)

        cc = twelve_get("cryptocurrencies", {})
        c_set = set()
        for item in cc.get("data", []):
            sym = (item.get("symbol") or "").upper()
            if "/" in sym:
                c_set.add(sym)

        return fx_set, c_set

    try:
        fx_set, c_set = await to_thread(_fetch)
        TWELVE_CACHE["forex_pairs"] = fx_set
        TWELVE_CACHE["crypto_pairs"] = c_set
        TWELVE_CACHE["last_refresh"] = now
        log("Twelve refreshed", len(fx_set), len(c_set))
        return True
    except Exception as ex:
        log("twelve_refresh_markets error", ex)
        return False

def normalize_symbol(raw: str) -> Optional[str]:
    s = raw.strip().upper()

    if "/" in s:
        parts = s.split("/")
        if len(parts) == 2 and 2 <= len(parts[0]) <= 6 and 2 <= len(parts[1]) <= 6:
            return f"{parts[0]}/{parts[1]}"
        return None

    if "_" in s:
        parts = s.split("_")
        if len(parts) == 2 and 2 <= len(parts[0]) <= 6 and 2 <= len(parts[1]) <= 6:
            return f"{parts[0]}/{parts[1]}"
        return None

    # EURUSD / BTCUSD
    if len(s) == 6 and s.isalnum():
        return f"{s[:3]}/{s[3:]}"
    return None

def is_forex_symbol(sym: str) -> bool:
    return sym in TWELVE_CACHE["forex_pairs"]

def is_crypto_symbol(sym: str) -> bool:
    return sym in TWELVE_CACHE["crypto_pairs"]

async def fetch_timeseries(symbol: str, interval: str, outputsize: int = 250) -> pd.DataFrame:
    def _fetch():
        data = twelve_get("time_series", {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "UTC"
        })
        if data.get("status") != "ok":
            raise RuntimeError(data.get("message", "TwelveData error"))

        rows = []
        for v in data.get("values", []):
            ts = pd.to_datetime(v["datetime"], utc=True)
            rows.append([ts, float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])])

        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        return df.sort_values("ts")

    return await to_thread(_fetch)

async def twelve_spread_pct(symbol: str) -> Optional[float]:
    def _fetch():
        q = twelve_get("quote", {"symbol": symbol})
        bid = q.get("bid") or q.get("bid_price")
        ask = q.get("ask") or q.get("ask_price")
        if bid is None or ask is None:
            return None
        bid = float(bid); ask = float(ask)
        if bid <= 0:
            return None
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 100.0

    try:
        return await to_thread(_fetch)
    except Exception as ex:
        log("twelve_spread_pct error", symbol, ex)
        return None

# =========================
# SESSIONS
# =========================
def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))

def in_killzone(cfg) -> Tuple[bool, str]:
    now_t = now_utc().time()
    kz = cfg["killzones_utc"]

    london_s, london_e = map(_parse_hhmm, kz["london"])
    ny_s, ny_e = map(_parse_hhmm, kz["ny"])

    pref = cfg.get("session", "both")
    london_ok = london_s <= now_t <= london_e
    ny_ok = ny_s <= now_t <= ny_e

    if pref == "london":
        return (london_ok, "London")
    if pref == "ny":
        return (ny_ok, "New York")

    if london_ok:
        return (True, "London")
    if ny_ok:
        return (True, "New York")
    return (False, "Off-session")

# =========================
# NEWS FILTER
# =========================
def high_impact_news_block() -> bool:
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        events = requests.get(url, timeout=5).json()

        # Check if the response is empty
        if not events:
            log("Empty response from news API.")
            return False

        now = datetime.utcnow().replace(tzinfo=UTC)  # Make sure 'now' is timezone-aware

        for e in events:
            if e.get("impact") != "High":
                continue
            title = (e.get("title") or "").upper()
            if not any(x in title for x in ["CPI", "FOMC", "NFP"]):
                continue
            event_time = datetime.fromisoformat(e["date"].replace("Z", ""))
            event_time = event_time.replace(tzinfo=UTC)

            if abs((event_time - now).total_seconds()) < 3600:
                return True
    except Exception as ex:
        log("News filter error:", ex)
    return False


# =========================
# STRATEGY: STRUCTURE + FVG
# =========================
def find_swings(df, left=2, right=2):
    highs = df["high"].values
    lows = df["low"].values
    swing_highs, swing_lows = [], []
    for i in range(left, len(df) - right):
        win_h = highs[i-left:i+right+1]
        win_l = lows[i-left:i+right+1]
        if highs[i] == win_h.max():
            swing_highs.append((i, highs[i]))
        if lows[i] == win_l.min():
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows

def htf_bias_from_structure(htf_df):
    sh, sl = find_swings(htf_df, left=2, right=2)
    if len(sh) < 2 or len(sl) < 2:
        return None
    (_, h1), (_, h2) = sh[-2], sh[-1]
    (_, l1), (_, l2) = sl[-2], sl[-1]
    if h2 > h1 and l2 > l1:
        return "BUY"
    if h2 < h1 and l2 < l1:
        return "SELL"
    return None

def scan_fvg(ltf_df, direction, min_gap_pct, lookback=80):
    n = len(ltf_df)
    start = max(2, n - lookback)
    best = None
    for i in range(start, n):
        c0 = ltf_df.iloc[i-2]
        c1 = ltf_df.iloc[i-1]
        c2 = ltf_df.iloc[i]

        if direction == "BUY" and c0["high"] < c2["low"]:
            gap_low = float(c0["high"])
            gap_high = float(c2["low"])
            mid = (gap_low + gap_high) / 2
            gap_pct = ((gap_high - gap_low) / float(c1["close"])) * 100
            if gap_pct >= min_gap_pct:
                best = {"mid": mid, "gap_pct": gap_pct}

        if direction == "SELL" and c0["low"] > c2["high"]:
            gap_low = float(c2["high"])
            gap_high = float(c0["low"])
            mid = (gap_low + gap_high) / 2
            gap_pct = ((gap_high - gap_low) / float(c1["close"])) * 100
            if gap_pct >= min_gap_pct:
                best = {"mid": mid, "gap_pct": gap_pct}
    return best

def recent_swing_sl(ltf_df, direction, lookback=14):
    window = ltf_df.iloc[-lookback:]
    return float(window["low"].min()) if direction == "BUY" else float(window["high"].max())

def in_price_proximity(price, entry, near_entry_pct):
    return (abs(price - entry) / price * 100) <= near_entry_pct

# =========================
# CONFIG HELPERS
# =========================
def pair_cfg(cfg: Dict[str, Any], symbol: str, kind: str) -> Dict[str, float]:
    pc = cfg.get("pair_config", {})
    if symbol in pc:
        return pc[symbol]
    return cfg["defaults"][kind].copy()

def set_pair_cfg(cfg: Dict[str, Any], symbol: str, updates: Dict[str, float]):
    cfg.setdefault("pair_config", {})
    cfg["pair_config"].setdefault(symbol, {})
    cfg["pair_config"][symbol].update(updates)

# =========================
# COOLDOWNS
# =========================
def cooldown_ok(chat_id: str, symbol: str, cooldown_min: int) -> bool:
    state = RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}, "awaiting": None})
    last = state["cooldowns"].get(symbol)
    if not last:
        return True
    last_dt = datetime.fromisoformat(last)
    return now_utc() - last_dt >= timedelta(minutes=cooldown_min)

def mark_cooldown(chat_id: str, symbol: str):
    state = RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}, "awaiting": None})
    state["cooldowns"][symbol] = now_utc().isoformat()

# =========================
# INLINE MENUS
# =========================
def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 My Pairs", callback_data="menu:pairs"),
         InlineKeyboardButton("➕ Add", callback_data="menu:add")],
        [InlineKeyboardButton("🗑 Remove", callback_data="menu:remove"),
         InlineKeyboardButton("📈 Markets", callback_data="menu:markets")],
        [InlineKeyboardButton("⏰ Sessions", callback_data="menu:sessions"),
         InlineKeyboardButton("⚙️ Settings", callback_data="menu:settings")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="menu:help")]
    ])

def kb_add() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Crypto", callback_data="add:crypto"),
         InlineKeyboardButton("➕ Add Forex", callback_data="add:forex")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:main")]
    ])

def kb_sessions(current: str) -> InlineKeyboardMarkup:
    def mark(v): return "✅ " + v.upper() if current == v else v.upper()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(mark("london"), callback_data="sess:london"),
         InlineKeyboardButton(mark("ny"), callback_data="sess:ny"),
         InlineKeyboardButton(mark("both"), callback_data="sess:both")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:main")]
    ])

def kb_settings(cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⏱ Scan: {cfg['scan_interval_sec']}s", callback_data="set:scan"),
         InlineKeyboardButton(f"🧊 Cooldown: {cfg['cooldown_min']}m", callback_data="set:cooldown")],
        [InlineKeyboardButton("📉 Set Spread Filter", callback_data="set:spread")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu:main")]
    ])

def kb_remove_list(cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    buttons = []
    # show up to 12 buttons (6 crypto + 6 forex)
    for sym in cfg["pairs_crypto"][:6]:
        buttons.append([InlineKeyboardButton(f"🗑 {sym}", callback_data=f"rm:{sym}")])
    for sym in cfg["pairs_forex"][:6]:
        buttons.append([InlineKeyboardButton(f"🗑 {sym}", callback_data=f"rm:{sym}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)

# =========================
# TELEGRAM HANDLERS
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)
    await twelve_refresh_markets(force=True)
    clear_awaiting(chat_id)

    ok, session_name = in_killzone(cfg)
    status_line = f"Session window: {session_name} | News filter: {'ON' if high_impact_news_block() else 'OFF'}"

    text = (
        "🟢 *SMC Scanner is ON*\n\n"
        f"{status_line}\n\n"
        "Use the buttons below. You can still type commands if you want.\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    users = _load_users()
    chat_id = str(query.message.chat.id)
    cfg = get_user(users, chat_id)

    data = query.data

    if data == "menu:main":
        clear_awaiting(chat_id)
        ok, session_name = in_killzone(cfg)
        text = (
            "🟢 *SMC Scanner is ON*\n\n"
            f"Session window: {session_name}\n\n"
            "Choose an option:"
        )
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())

    if data == "menu:pairs":
        clear_awaiting(chat_id)
        text = (
            "📊 *Your Pairs*\n\n"
            f"*Crypto:*\n" + ("\n".join(cfg["pairs_crypto"]) if cfg["pairs_crypto"] else "None") + "\n\n"
            f"*Forex:*\n" + ("\n".join(cfg["pairs_forex"]) if cfg["pairs_forex"] else "None")
        )
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())

    if data == "menu:add":
        clear_awaiting(chat_id)
        text = (
            "➕ *Add Market*\n\n"
            "Pick Crypto or Forex.\n"
            "You can also type a symbol directly (example: BTC/USD or EUR/USD)."
        )
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_add())

    if data == "menu:remove":
        clear_awaiting(chat_id)
        text = "🗑 *Remove Market*\n\nTap a pair to remove it."
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_remove_list(cfg))

    if data == "menu:markets":
        clear_awaiting(chat_id)
        await twelve_refresh_markets(force=True)
        text = (
            "📈 *Markets Available (Twelve Data)*\n\n"
            f"Forex pairs discovered: *{len(TWELVE_CACHE['forex_pairs'])}*\n"
            f"Crypto pairs discovered: *{len(TWELVE_CACHE['crypto_pairs'])}*\n\n"
            "Examples:\n"
            "Forex: EUR/USD, GBP/USD, USD/JPY\n"
            "Crypto: BTC/USD, ETH/USD, SOL/USD\n\n"
            "Use ➕ Add to add symbols."
        )
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())

    if data == "menu:sessions":
        clear_awaiting(chat_id)
        text = "⏰ *Sessions*\n\nChoose when signals are allowed."
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_sessions(cfg["session"]))

    if data == "menu:settings":
        clear_awaiting(chat_id)
        text = "⚙️ *Settings*\n\nTap to change."
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_settings(cfg))

    if data == "menu:help":
        clear_awaiting(chat_id)
        text = (
            "ℹ️ *Help*\n\n"
            "This bot scans crypto + forex using SMC bias + Fair Value Gaps.\n"
            "It sends alerts only (no auto trading).\n\n"
            "Flow:\n"
            "1) Add pairs\n"
            "2) Choose session\n"
            "3) Wait for alerts\n\n"
            "Tip: Add symbols like BTC/USD or EUR/USD."
        )
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())

    # Add flows
    if data == "add:crypto":
        set_awaiting(chat_id, "add_symbol", {"kind": "crypto"})
        return await query.edit_message_text(
            "➕ *Add Crypto*\n\nSend a symbol like:\n`BTC/USD`\n`ETH/USD`\n\nType it now:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu:add")]])
        )

    if data == "add:forex":
        set_awaiting(chat_id, "add_symbol", {"kind": "forex"})
        return await query.edit_message_text(
            "➕ *Add Forex*\n\nSend a symbol like:\n`EUR/USD`\n`GBP/USD`\n`USD/JPY`\n\nType it now:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu:add")]])
        )

    # Session selection
    if data.startswith("sess:"):
        chosen = data.split(":")[1]
        cfg["session"] = chosen
        users[chat_id] = cfg
        _save_users(users)
        clear_awaiting(chat_id)
        text = f"✅ Session set to *{chosen.upper()}*"
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_sessions(cfg["session"]))

    # Remove pair
    if data.startswith("rm:"):
        sym = data.split("rm:")[1]
        if sym in cfg["pairs_crypto"]:
            cfg["pairs_crypto"].remove(sym)
        if sym in cfg["pairs_forex"]:
            cfg["pairs_forex"].remove(sym)
        users[chat_id] = cfg
        _save_users(users)
        clear_awaiting(chat_id)
        text = f"🗑 Removed: *{sym}*"
        return await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_remove_list(cfg))

    # Settings flows
    if data == "set:scan":
        set_awaiting(chat_id, "set_scan", {})
        return await query.edit_message_text(
            "⏱ *Set Scan Interval*\n\nSend seconds (min 15). Example:\n`60`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu:settings")]])
        )

    if data == "set:cooldown":
        set_awaiting(chat_id, "set_cooldown", {})
        return await query.edit_message_text(
            "🧊 *Set Cooldown*\n\nSend minutes (min 1). Example:\n`90`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu:settings")]])
        )

    if data == "set:spread":
        set_awaiting(chat_id, "set_spread_symbol", {})
        return await query.edit_message_text(
            "📉 *Spread Filter*\n\nFirst send the symbol you want to configure.\nExample:\n`EUR/USD` or `BTC/USD`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu:settings")]])
        )


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)
    await twelve_refresh_markets()

    awaiting = get_awaiting(chat_id)
    text = update.message.text.strip()

    # If nothing is awaited, treat as "quick add"
    if not awaiting or not awaiting.get("kind"):
        sym = normalize_symbol(text)
        if not sym:
            return await update.message.reply_text("I didn’t understand that. Use buttons or type like BTC/USD or EUR/USD.", reply_markup=kb_main())
        return await do_add_symbol(update, users, chat_id, cfg, sym)

    kind = awaiting["kind"]
    meta = awaiting.get("meta", {})

    if kind == "add_symbol":
        sym = normalize_symbol(text)
        if not sym:
            return await update.message.reply_text("Invalid symbol. Try like BTC/USD or EUR/USD.", reply_markup=kb_main())

        intended = meta.get("kind")
        if intended == "crypto" and not is_crypto_symbol(sym):
            return await update.message.reply_text("That doesn’t look like a supported crypto symbol. Try BTC/USD.", reply_markup=kb_main())
        if intended == "forex" and not is_forex_symbol(sym):
            return await update.message.reply_text("That doesn’t look like a supported forex symbol. Try EUR/USD.", reply_markup=kb_main())

        clear_awaiting(chat_id)
        return await do_add_symbol(update, users, chat_id, cfg, sym)

    if kind == "set_scan":
        try:
            sec = int(text)
            cfg["scan_interval_sec"] = max(15, sec)
            users[chat_id] = cfg
            _save_users(users)
            clear_awaiting(chat_id)
            return await update.message.reply_text(f"✅ Scan interval set to {cfg['scan_interval_sec']}s", reply_markup=kb_settings(cfg))
        except:
            return await update.message.reply_text("Send a number like 60.")

    if kind == "set_cooldown":
        try:
            minutes = int(text)
            cfg["cooldown_min"] = max(1, minutes)
            users[chat_id] = cfg
            _save_users(users)
            clear_awaiting(chat_id)
            return await update.message.reply_text(f"✅ Cooldown set to {cfg['cooldown_min']} minutes", reply_markup=kb_settings(cfg))
        except:
            return await update.message.reply_text("Send a number like 90.")

    if kind == "set_spread_symbol":
        sym = normalize_symbol(text)
        if not sym:
            return await update.message.reply_text("Invalid symbol. Example: EUR/USD or BTC/USD.")
        if (sym not in cfg["pairs_crypto"]) and (sym not in cfg["pairs_forex"]):
            return await update.message.reply_text("Add the pair first (Add menu), then set spread.")
        set_awaiting(chat_id, "set_spread_value", {"symbol": sym})
        return await update.message.reply_text(f"Now send max spread % for {sym}. Example: 0.10")

    if kind == "set_spread_value":
        sym = meta.get("symbol")
        try:
            pct = float(text)
            if not sym:
                clear_awaiting(chat_id)
                return await update.message.reply_text("Something went wrong, try again.", reply_markup=kb_main())
            set_pair_cfg(cfg, sym, {"max_spread_pct": max(0.001, pct)})
            users[chat_id] = cfg
            _save_users(users)
            clear_awaiting(chat_id)
            return await update.message.reply_text(f"✅ Max spread for {sym} set to {pct}%", reply_markup=kb_settings(cfg))
        except:
            return await update.message.reply_text("Send a number like 0.10")


async def do_add_symbol(update: Update, users: Dict[str, Any], chat_id: str, cfg: Dict[str, Any], sym: str):
    # Validate against discovered lists
    if is_crypto_symbol(sym):
        if sym not in cfg["pairs_crypto"]:
            cfg["pairs_crypto"].append(sym)
        cfg["pair_config"].setdefault(sym, cfg["defaults"]["crypto"].copy())
        users[chat_id] = cfg
        _save_users(users)
        return await update.message.reply_text(f"✅ Added crypto: {sym}", reply_markup=kb_main())

    if is_forex_symbol(sym):
        if sym not in cfg["pairs_forex"]:
            cfg["pairs_forex"].append(sym)
        cfg["pair_config"].setdefault(sym, cfg["defaults"]["forex"].copy())
        users[chat_id] = cfg
        _save_users(users)
        return await update.message.reply_text(f"✅ Added forex: {sym}", reply_markup=kb_main())

    return await update.message.reply_text("❌ Symbol not found in Twelve Data lists. Tap 📈 Markets to confirm.", reply_markup=kb_main())

# =========================
# SCANNER LOOP
# =========================
async def scan_for_user(app: Application, chat_id: str, cfg: dict):
    ok, session_name = in_killzone(cfg)
    if not ok:
        return
    if high_impact_news_block():
        return

    # Crypto scan
    for sym in cfg["pairs_crypto"]:
        if not cooldown_ok(chat_id, sym, cfg["cooldown_min"]):
            continue

        pconf = pair_cfg(cfg, sym, "crypto")
        max_spread = float(pconf.get("max_spread_pct", cfg["defaults"]["crypto"]["max_spread_pct"]))
        sp = await twelve_spread_pct(sym)
        if sp is not None and sp > max_spread:
            continue

        try:
            htf = await fetch_timeseries(sym, "1h", outputsize=250)
            bias = htf_bias_from_structure(htf)
            if not bias:
                continue

            ltf = await fetch_timeseries(sym, "5min", outputsize=250)
            fvg = scan_fvg(ltf, bias, float(pconf["fvg_min_pct"]), lookback=int(cfg["fvg_lookback"]))
            if not fvg:
                continue

            price = float(ltf["close"].iloc[-1])
            entry = float(fvg["mid"])
            if not in_price_proximity(price, entry, float(pconf["near_entry_pct"])):
                continue

            sl = recent_swing_sl(ltf, bias, lookback=14)
            risk = abs(entry - sl)
            if risk <= 0:
                continue

            rr = float(pconf["rr"])
            tp = entry + risk * rr if bias == "BUY" else entry - risk * rr

            spread_line = f"{sp:.3f}% (max {max_spread}%)" if sp is not None else f"Unavailable (max {max_spread}%)"
            msg = (
                f"📌 *SMC + FVG ALERT*\n\n"
                f"Pair: *{sym}* (Crypto)\n"
                f"Session: *{session_name}*\n"
                f"Bias: *{bias}*\n"
                f"FVG gap: *{fvg['gap_pct']:.2f}%*\n"
                f"Spread: *{spread_line}*\n\n"
                f"Entry: `{entry:.4f}`\n"
                f"SL: `{sl:.4f}`\n"
                f"TP: `{tp:.4f}`\n"
            )
            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=ParseMode.MARKDOWN)
            mark_cooldown(chat_id, sym)
            await asyncio.sleep(0.8)

        except Exception as ex:
            log("Crypto scan error", chat_id, sym, ex)

    # Forex scan
    for sym in cfg["pairs_forex"]:
        if not cooldown_ok(chat_id, sym, cfg["cooldown_min"]):
            continue

        pconf = pair_cfg(cfg, sym, "forex")
        max_spread = float(pconf.get("max_spread_pct", cfg["defaults"]["forex"]["max_spread_pct"]))
        sp = await twelve_spread_pct(sym)
        if sp is not None and sp > max_spread:
            continue

        try:
            htf = await fetch_timeseries(sym, "1h", outputsize=250)
            bias = htf_bias_from_structure(htf)
            if not bias:
                continue

            ltf = await fetch_timeseries(sym, "5min", outputsize=250)
            fvg = scan_fvg(ltf, bias, float(pconf["fvg_min_pct"]), lookback=int(cfg["fvg_lookback"]))
            if not fvg:
                continue

            price = float(ltf["close"].iloc[-1])
            entry = float(fvg["mid"])
            if not in_price_proximity(price, entry, float(pconf["near_entry_pct"])):
                continue

            sl = recent_swing_sl(ltf, bias, lookback=14)
            risk = abs(entry - sl)
            if risk <= 0:
                continue

            rr = float(pconf["rr"])
            tp = entry + risk * rr if bias == "BUY" else entry - risk * rr

            spread_line = f"{sp:.3f}% (max {max_spread}%)" if sp is not None else f"Unavailable (max {max_spread}%)"
            msg = (
                f"📌 *SMC + FVG ALERT*\n\n"
                f"Pair: *{sym}* (Forex)\n"
                f"Session: *{session_name}*\n"
                f"Bias: *{bias}*\n"
                f"FVG gap: *{fvg['gap_pct']:.2f}%*\n"
                f"Spread: *{spread_line}*\n\n"
                f"Entry: `{entry:.5f}`\n"
                f"SL: `{sl:.5f}`\n"
                f"TP: `{tp:.5f}`\n"
            )
            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=ParseMode.MARKDOWN)
            mark_cooldown(chat_id, sym)
            await asyncio.sleep(0.8)

        except Exception as ex:
            log("Forex scan error", chat_id, sym, ex)

async def background_scanner(app: Application):
    await twelve_refresh_markets(force=True)
    while True:
        try:
            users = _load_users()
            for chat_id, cfg in users.items():
                if not cfg.get("enabled", True):
                    continue
                await scan_for_user(app, chat_id, cfg)

            min_interval = min([u.get("scan_interval_sec", 60) for u in users.values()] or [60])
            await asyncio.sleep(max(15, min_interval))

        except Exception as ex:
            log("Scanner loop error", ex)
            await asyncio.sleep(30)

async def on_startup(app: Application):
    asyncio.create_task(background_scanner(app))

# =========================
# BUILD APP
# =========================
def build_app():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    app.add_handler(CallbackQueryHandler(menu_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))

    app.post_init = on_startup
    return app

if __name__ == "__main__":
    build_app().run_polling(close_loop=False)
