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

    # Twelve Data symbols (added Gold and Silver here)
    "pairs_crypto": ["BTC/USD", "ETH/USD", "SOL/USD", "XAU/USD", "XAG/USD"],
    "pairs_forex": ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "CAD/JPY", "NZD/USD"],

    "session": "both",  # london | ny | both
    "scan_interval_sec": 60,
    "cooldown_min": 90,
    "fvg_lookback": 80,  # Fair Value Gap lookback

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
        "AUD/USD": {"rr": 2.1, "fvg_min_pct": 0.05, "near_entry_pct": 0.20, "max_spread_pct": 0.10},  # New forex
        "CAD/JPY": {"rr": 2.0, "fvg_min_pct": 0.05, "near_entry_pct": 0.20, "max_spread_pct": 0.10},  # New forex
        "NZD/USD": {"rr": 2.1, "fvg_min_pct": 0.05, "near_entry_pct": 0.20, "max_spread_pct": 0.10},  # New forex
    },

    "defaults": {
        "crypto": {"rr": 2.2, "fvg_min_pct": 0.10, "near_entry_pct": 0.30, "max_spread_pct": 0.30},
        "forex":  {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.10},
    }
}

RUNTIME_STATE: Dict[str, Dict[str, Any]] = {}

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
        now = datetime.utcnow()
        for e in events:
            if e.get("impact") != "High":
                continue
            title = (e.get("title") or "").upper()
            if not any(x in title for x in ["CPI", "FOMC", "NFP"]):
                continue
            event_time = datetime.fromisoformat(e["date"].replace("Z", ""))
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
         InlineKeyboardButton("➕ Add Market", callback_data="menu:add")],
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

def kb_settings(cfg: Dict[str, Any]) -> InlineKeyboard:
