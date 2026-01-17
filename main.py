import os
import json
import asyncio
import requests
from datetime import datetime, time, timedelta
from typing import Dict, Any, Optional, Tuple

import pytz
import pandas as pd
import ccxt

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY")
DEBUG = os.getenv("DEBUG", "0") == "1"

CRYPTO_EXCHANGE_ID = os.getenv("CRYPTO_EXCHANGE", "binance")  # for crypto candles + spread
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

    # user pairs (stored normalized)
    "pairs_crypto": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],  # crypto uses CCXT symbols
    "pairs_forex": ["EUR/USD"],  # forex uses Twelve Data symbols like EUR/USD

    "session": "both",  # london | ny | both
    "scan_interval_sec": 60,
    "cooldown_min": 90,
    "fvg_lookback": 80,

    "killzones_utc": {
        "london": ["07:00", "10:00"],
        "ny": ["12:00", "15:00"]
    },

    # per-pair config (spread filter included)
    "pair_config": {
        "BTC/USDT": {"rr": 2.3, "fvg_min_pct": 0.08, "near_entry_pct": 0.25, "max_spread_pct": 0.08},
        "ETH/USDT": {"rr": 2.1, "fvg_min_pct": 0.10, "near_entry_pct": 0.30, "max_spread_pct": 0.10},
        "SOL/USDT": {"rr": 2.4, "fvg_min_pct": 0.14, "near_entry_pct": 0.45, "max_spread_pct": 0.18},

        # forex defaults per symbol if you want to override later
        "EUR/USD": {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.08},
    },

    "defaults": {
        "crypto": {"rr": 2.2, "fvg_min_pct": 0.10, "near_entry_pct": 0.30, "max_spread_pct": 0.15},
        "forex":  {"rr": 2.0, "fvg_min_pct": 0.03, "near_entry_pct": 0.20, "max_spread_pct": 0.08},
    }
}

# Runtime state (cooldowns) – in memory
RUNTIME_STATE: Dict[str, Dict[str, Any]] = {}

# Twelve Data caches
TWELVE_CACHE = {
    "forex_pairs": set(),   # {"EUR/USD", ...}
    "cryptos": set(),       # {"BTC/USD", ...} (from /cryptocurrencies)
    "last_refresh": None
}

# =========================
# UTIL
# =========================
def log(*args):
    if DEBUG:
        print("[DEBUG]", *args)

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
    RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}})
    return users[chat_id]

async def to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

def now_utc():
    return datetime.now(UTC)

# =========================
# CRYPTO (CCXT)
# =========================
def build_exchange():
    ex_class = getattr(ccxt, CRYPTO_EXCHANGE_ID, None)
    if not ex_class:
        raise RuntimeError(f"Unsupported CRYPTO_EXCHANGE: {CRYPTO_EXCHANGE_ID}")
    return ex_class({"enableRateLimit": True, "timeout": 30000})

CRYPTO_EXCHANGE = build_exchange()

async def crypto_load_markets():
    def _load():
        CRYPTO_EXCHANGE.load_markets()
        return True
    return await to_thread(_load)

def crypto_symbol_exists(symbol: str) -> bool:
    try:
        return symbol in CRYPTO_EXCHANGE.markets
    except:
        return False

async def fetch_crypto_ohlcv(symbol: str, timeframe: str, limit: int = 250) -> pd.DataFrame:
    def _fetch():
        ohlc = CRYPTO_EXCHANGE.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlc, columns=["ts", "open", "high", "low", "close", "vol"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df
    return await to_thread(_fetch)

async def crypto_spread_pct(symbol: str) -> Optional[float]:
    def _tick():
        t = CRYPTO_EXCHANGE.fetch_ticker(symbol)
        bid = t.get("bid")
        ask = t.get("ask")
        if bid is None or ask is None or bid <= 0:
            return None
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 100.0
    try:
        return await to_thread(_tick)
    except Exception as ex:
        log("crypto_spread_pct error", symbol, ex)
        return None

# =========================
# TWELVE DATA (FOREX + DISCOVERY)
# =========================
def twelve_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(params)
    params["apikey"] = TWELVE_API_KEY
    url = f"{TWELVE_BASE}/{path.lstrip('/')}"
    r = requests.get(url, params=params, timeout=12)
    r.raise_for_status()
    return r.json()

async def twelve_refresh_markets(force: bool = False) -> bool:
    """
    Auto-discovery:
      - forex pairs via /forex_pairs (daily updated)
      - crypto list via /cryptocurrencies (daily updated)
    Cache refresh every 6 hours unless forced.
    """
    now = now_utc()
    last = TWELVE_CACHE["last_refresh"]
    if (not force) and last and (now - last) < timedelta(hours=6) and TWELVE_CACHE["forex_pairs"]:
        return True

    def _fetch_all():
        forex = twelve_get("forex_pairs", {})
        fx_set = set()
        for item in forex.get("data", []):
            sym = (item.get("symbol") or "").upper()
            if "/" in sym:
                fx_set.add(sym)

        cryptos = twelve_get("cryptocurrencies", {})  # symbols like BTC/USD
        c_set = set()
        for item in cryptos.get("data", []):
            sym = (item.get("symbol") or "").upper()
            if "/" in sym:
                c_set.add(sym)

        return fx_set, c_set

    try:
        fx_set, c_set = await to_thread(_fetch_all)
        TWELVE_CACHE["forex_pairs"] = fx_set
        TWELVE_CACHE["cryptos"] = c_set
        TWELVE_CACHE["last_refresh"] = now
        log("Twelve refreshed", "FX:", len(fx_set), "Crypto:", len(c_set))
        return True
    except Exception as ex:
        log("twelve_refresh_markets error", ex)
        return False

def twelve_forex_exists(symbol: str) -> bool:
    return symbol.upper() in TWELVE_CACHE["forex_pairs"]

def twelve_crypto_exists(symbol: str) -> bool:
    return symbol.upper() in TWELVE_CACHE["cryptos"]

def normalize_forex_symbol(raw: str) -> Optional[str]:
    """
    Accept:
      EUR_USD, EUR/USD, EURUSD
    Normalize to:
      EUR/USD
    """
    s = raw.strip().upper()
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 2 and len(parts[0]) == 3 and len(parts[1]) == 3:
            return f"{parts[0]}/{parts[1]}"
        return None
    if "_" in s:
        parts = s.split("_")
        if len(parts) == 2 and len(parts[0]) == 3 and len(parts[1]) == 3:
            return f"{parts[0]}/{parts[1]}"
        return None
    if len(s) == 6 and s.isalpha():
        return f"{s[:3]}/{s[3:]}"
    return None

async def fetch_twelve_timeseries(symbol: str, interval: str, outputsize: int = 250) -> pd.DataFrame:
    """
    Twelve Data candles:
      interval examples: '5min', '1h'
    Returns df with: ts, open, high, low, close
    """
    def _fetch():
        data = twelve_get("time_series", {"symbol": symbol, "interval": interval, "outputsize": outputsize, "timezone": "UTC"})
        if data.get("status") != "ok":
            # some errors come as {"status":"error","message":"..."}
            raise RuntimeError(data.get("message", "TwelveData error"))

        values = data.get("values", [])
        rows = []
        for v in values:
            # datetime like "2025-02-28 14:30:00"
            ts = pd.to_datetime(v["datetime"], utc=True)
            o = float(v["open"])
            h = float(v["high"])
            l = float(v["low"])
            c = float(v["close"])
            rows.append([ts, o, h, l, c])

        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        df = df.sort_values("ts")  # ascending
        return df

    return await to_thread(_fetch)

async def twelve_spread_pct(symbol: str) -> Optional[float]:
    """
    Twelve Data /quote often returns only 'price' for many instruments.
    If bid/ask exists, compute spread%.
    If not, returns None (spread not available from data feed).
    """
    def _fetch():
        q = twelve_get("quote", {"symbol": symbol})
        # try common fields
        bid = q.get("bid") or q.get("bid_price")
        ask = q.get("ask") or q.get("ask_price")
        if bid is None or ask is None:
            return None
        bid = float(bid)
        ask = float(ask)
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
    state = RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}})
    last = state["cooldowns"].get(symbol)
    if not last:
        return True
    last_dt = datetime.fromisoformat(last)
    return now_utc() - last_dt >= timedelta(minutes=cooldown_min)

def mark_cooldown(chat_id: str, symbol: str):
    state = RUNTIME_STATE.setdefault(chat_id, {"cooldowns": {}})
    state["cooldowns"][symbol] = now_utc().isoformat()

# =========================
# TELEGRAM COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)

    await crypto_load_markets()
    await twelve_refresh_markets()

    msg = (
        "🟢 *SMC Scanner is ON*\n\n"
        "Commands:\n"
        "• /pairs\n"
        "• /add BTC/USDT (crypto)\n"
        "• /add EUR_USD or EUR/USD or EURUSD (forex)\n"
        "• /remove BTC/USDT\n"
        "• /setsession london|ny|both\n"
        "• /setcooldown 60\n"
        "• /setscan 60\n"
        "• /setspread PAIR 0.10  (max spread %)\n"
        "• /markets\n"
        "• /help\n\n"
        f"Session: *{cfg['session']}* | Scan: *{cfg['scan_interval_sec']}s* | Cooldown: *{cfg['cooldown_min']}m*\n\n"
        "Note: Crypto spread filter is real bid/ask via exchange. Forex spread may be unavailable (mid-price feed)."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)

    crypto = "\n".join(cfg["pairs_crypto"]) or "None"
    fx = "\n".join(cfg["pairs_forex"]) or "None"

    await update.message.reply_text(
        f"*Crypto pairs:*\n{crypto}\n\n*Forex pairs:*\n{fx}",
        parse_mode=ParseMode.MARKDOWN
    )

async def markets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await crypto_load_markets()
    await twelve_refresh_markets(force=True)

    crypto_count = len(CRYPTO_EXCHANGE.markets) if hasattr(CRYPTO_EXCHANGE, "markets") else 0
    fx_count = len(TWELVE_CACHE["forex_pairs"])
    td_crypto_count = len(TWELVE_CACHE["cryptos"])

    msg = (
        "📊 *Markets availability*\n\n"
        f"*Crypto exchange:* `{CRYPTO_EXCHANGE_ID}`\n"
        f"*Crypto symbols loaded:* {crypto_count}\n\n"
        f"*TwelveData forex pairs discovered:* {fx_count}\n"
        f"*TwelveData crypto symbols discovered:* {td_crypto_count}\n\n"
        "Examples:\n"
        f"• Forex: EUR/USD, GBP/USD, USD/JPY\n"
        f"• Crypto (CCXT): BTC/USDT, ETH/USDT\n\n"
        "Add pairs with /add\n"
        "Set max spread with /setspread PAIR PCT"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def add_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /add BTC/USDT  or  /add EUR_USD")

    raw = context.args[0].strip()
    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)

    # Crypto if it contains "/"
    if "/" in raw and len(raw.split("/")) == 2 and len(raw.split("/")[0]) >= 2:
        pair = raw.upper()

        await crypto_load_markets()
        if not crypto_symbol_exists(pair):
            return await update.message.reply_text("❌ Crypto pair not found on exchange. Use /markets to check availability.")

        if pair not in cfg["pairs_crypto"]:
            cfg["pairs_crypto"].append(pair)

        cfg["pair_config"].setdefault(pair, cfg["defaults"]["crypto"].copy())
        _save_users(users)
        return await update.message.reply_text(f"✅ Added crypto pair: {pair}")

    # Otherwise treat as forex and normalize
    await twelve_refresh_markets()
    fx = normalize_forex_symbol(raw)
    if not fx:
        return await update.message.reply_text("❌ Invalid forex format. Try EUR_USD or EUR/USD or EURUSD")

    if not twelve_forex_exists(fx):
        return await update.message.reply_text("❌ Forex pair not found in TwelveData list. Use /markets and check spelling.")

    if fx not in cfg["pairs_forex"]:
        cfg["pairs_forex"].append(fx)

    cfg["pair_config"].setdefault(fx, cfg["defaults"]["forex"].copy())
    _save_users(users)
    await update.message.reply_text(f"✅ Added forex pair: {fx}")

async def remove_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /remove BTC/USDT  or  /remove EUR/USD")

    pair = context.args[0].strip().upper()

    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)

    if pair in cfg["pairs_crypto"]:
        cfg["pairs_crypto"].remove(pair)
        _save_users(users)
        return await update.message.reply_text(f"🗑 Removed crypto pair: {pair}")

    # normalize forex input if needed
    fx = normalize_forex_symbol(pair) or pair
    if fx in cfg["pairs_forex"]:
        cfg["pairs_forex"].remove(fx)
        _save_users(users)
        return await update.message.reply_text(f"🗑 Removed forex pair: {fx}")

    await update.message.reply_text("Pair not found in your list.")

async def setsession(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /setsession london|ny|both")

    val = context.args[0].strip().lower()
    if val not in ["london", "ny", "both"]:
        return await update.message.reply_text("Pick one: london, ny, both")

    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)
    cfg["session"] = val
    _save_users(users)
    await update.message.reply_text(f"✅ Session set to: {val}")

async def setcooldown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /setcooldown 60")
    try:
        minutes = int(context.args[0])
    except:
        return await update.message.reply_text("Cooldown must be a number (minutes).")

    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)
    cfg["cooldown_min"] = max(1, minutes)
    _save_users(users)
    await update.message.reply_text(f"✅ Cooldown set to: {cfg['cooldown_min']} minutes")

async def setscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /setscan 60")
    try:
        sec = int(context.args[0])
    except:
        return await update.message.reply_text("Scan interval must be a number (seconds).")

    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)
    cfg["scan_interval_sec"] = max(15, sec)
    _save_users(users)
    await update.message.reply_text(f"✅ Scan interval set to: {cfg['scan_interval_sec']} seconds")

async def setspread(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /setspread PAIR 0.10  (percent)")
    raw_pair = context.args[0].strip()
    try:
        pct = float(context.args[1])
    except:
        return await update.message.reply_text("Spread must be a number. Example: /setspread BTC/USDT 0.10")

    users = _load_users()
    chat_id = str(update.effective_chat.id)
    cfg = get_user(users, chat_id)

    # determine which list it belongs to
    pair = raw_pair.upper()
    fx = normalize_forex_symbol(raw_pair) or pair

    if (pair not in cfg["pairs_crypto"]) and (fx not in cfg["pairs_forex"]):
        return await update.message.reply_text("Add the pair first with /add, then set spread.")

    target = pair if pair in cfg["pairs_crypto"] else fx
    set_pair_cfg(cfg, target, {"max_spread_pct": max(0.001, pct)})
    _save_users(users)
    await update.message.reply_text(f"✅ Max spread for {target} set to {pct}%")

# =========================
# SCANNER LOOP
# =========================
async def scan_for_user(app: Application, chat_id: str, cfg: dict):
    ok, session_name = in_killzone(cfg)
    if not ok:
        return

    if high_impact_news_block():
        return

    # ---- CRYPTO ----
    for sym in cfg["pairs_crypto"]:
        if not cooldown_ok(chat_id, sym, cfg["cooldown_min"]):
            continue

        pconf = pair_cfg(cfg, sym, "crypto")
        max_spread = float(pconf.get("max_spread_pct", cfg["defaults"]["crypto"]["max_spread_pct"]))
        sp = await crypto_spread_pct(sym)

        # apply spread filter if we can calculate it
        if sp is not None and sp > max_spread:
            log("Crypto spread blocked", sym, sp, ">", max_spread)
            continue

        try:
            htf = await fetch_crypto_ohlcv(sym, "1h", limit=250)
            bias = htf_bias_from_structure(htf)
            if not bias:
                continue

            ltf = await fetch_crypto_ohlcv(sym, "5m", limit=250)
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

            msg = (
                f"📌 *SMC + FVG ALERT*\n\n"
                f"Pair: *{sym}*\n"
                f"Session: *{session_name}*\n"
                f"Bias: *{bias}*\n"
                f"FVG gap: *{fvg['gap_pct']:.2f}%*\n"
                f"Spread: *{(sp if sp is not None else 0):.3f}%* (max {max_spread}%)\n\n"
                f"Entry: `{entry:.4f}`\n"
                f"SL: `{sl:.4f}`\n"
                f"TP: `{tp:.4f}`\n"
            )

            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=ParseMode.MARKDOWN)
            mark_cooldown(chat_id, sym)
            await asyncio.sleep(1)

        except Exception as ex:
            log("Crypto scan error", chat_id, sym, ex)

    # ---- FOREX (Twelve Data candles) ----
    for fx in cfg["pairs_forex"]:
        if not cooldown_ok(chat_id, fx, cfg["cooldown_min"]):
            continue

        pconf = pair_cfg(cfg, fx, "forex")
        max_spread = float(pconf.get("max_spread_pct", cfg["defaults"]["forex"]["max_spread_pct"]))
        sp = await twelve_spread_pct(fx)  # may be None on many feeds

        # Only enforce forex spread filter if bid/ask exists in quote response
        if sp is not None and sp > max_spread:
            log("Forex spread blocked", fx, sp, ">", max_spread)
            continue

        try:
            htf = await fetch_twelve_timeseries(fx, "1h", outputsize=250)
            bias = htf_bias_from_structure(htf)
            if not bias:
                continue

            ltf = await fetch_twelve_timeseries(fx, "5min", outputsize=250)
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
                f"Pair: *{fx}* (Forex)\n"
                f"Session: *{session_name}*\n"
                f"Bias: *{bias}*\n"
                f"FVG gap: *{fvg['gap_pct']:.2f}%*\n"
                f"Spread: *{spread_line}*\n\n"
                f"Entry: `{entry:.5f}`\n"
                f"SL: `{sl:.5f}`\n"
                f"TP: `{tp:.5f}`\n"
            )

            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode=ParseMode.MARKDOWN)
            mark_cooldown(chat_id, fx)
            await asyncio.sleep(1)

        except Exception as ex:
            log("Forex scan error", chat_id, fx, ex)

async def background_scanner(app: Application):
    # preload
    try:
        await crypto_load_markets()
    except Exception as ex:
        log("crypto_load_markets failed", ex)
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

def build_app():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("pairs", pairs))
    app.add_handler(CommandHandler("markets", markets))
    app.add_handler(CommandHandler("add", add_pair))
    app.add_handler(CommandHandler("remove", remove_pair))
    app.add_handler(CommandHandler("setsession", setsession))
    app.add_handler(CommandHandler("setcooldown", setcooldown))
    app.add_handler(CommandHandler("setscan", setscan))
    app.add_handler(CommandHandler("setspread", setspread))

    app.post_init = on_startup
    return app

if __name__ == "__main__":
    build_app().run_polling(close_loop=False)
