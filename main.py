import os
import time as pytime
import asyncio
import json
import requests
from datetime import datetime, time, timedelta

import pytz
import pandas as pd
import ccxt
from telegram import Bot

# =========================
# ENV (Railway Variables)
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DEBUG = os.getenv("DEBUG", "0") == "1"

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise RuntimeError("Missing TELEGRAM_TOKEN or CHAT_ID environment variables")

# =========================
# SETTINGS
# =========================
UTC = pytz.UTC

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

HTF = "1h"
LTF = "5m"

SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "60"))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "90"))

# Killzones in UTC (tune if you want)
LONDON_START = time(7, 0)
LONDON_END   = time(10, 0)
NY_START     = time(12, 0)
NY_END       = time(15, 0)

# Per-coin tuning
COIN_CONFIG = {
    "BTC/USDT": {"rr": 2.3, "fvg_min_pct": 0.08, "near_entry_pct": 0.25},
    "ETH/USDT": {"rr": 2.1, "fvg_min_pct": 0.10, "near_entry_pct": 0.30},
    "SOL/USDT": {"rr": 2.4, "fvg_min_pct": 0.14, "near_entry_pct": 0.45},
}

# How far back to search for FVGs
FVG_LOOKBACK = int(os.getenv("FVG_LOOKBACK", "80"))  # 80 x 5m = ~6.5 hours

# =========================
# EXCHANGE + TELEGRAM
# =========================
exchange = ccxt.binance({"enableRateLimit": True})
bot = Bot(token=TELEGRAM_TOKEN)

# =========================
# STATE
# =========================
# open_trades[symbol] = {"time": datetime_utc, "direction": "BUY"/"SELL", "entry": float, ...}
open_trades = {}

STATS_FILE = "stats.json"

# =========================
# HELPERS
# =========================
def log(*args):
    if DEBUG:
        print("[DEBUG]", *args)

def now_utc():
    return datetime.now(UTC)

def in_killzone():
    t = now_utc().time()
    return (LONDON_START <= t <= LONDON_END) or (NY_START <= t <= NY_END)

def killzone_name():
    t = now_utc().time()
    if LONDON_START <= t <= LONDON_END:
        return "London"
    if NY_START <= t <= NY_END:
        return "New York"
    return "Off-session"

def high_impact_news_block():
    """
    Blocks ±60 minutes around HIGH impact CPI/FOMC/NFP using FinancialJuice calendar feed.
    If feed fails, we default to NOT blocking (so the bot still runs).
    """
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

def fetch_df(symbol, tf, limit=300):
    ohlc = exchange.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(ohlc, columns=["ts","open","high","low","close","vol"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df

def find_swings(df, left=2, right=2):
    """
    Fractal swings:
    swing high at i if high[i] is max of window [i-left, i+right]
    swing low at i if low[i] is min of window [i-left, i+right]
    """
    highs = df["high"].values
    lows = df["low"].values
    swing_highs = []
    swing_lows = []

    for i in range(left, len(df)-right):
        win_h = highs[i-left:i+right+1]
        win_l = lows[i-left:i+right+1]
        if highs[i] == win_h.max():
            swing_highs.append((i, highs[i]))
        if lows[i] == win_l.min():
            swing_lows.append((i, lows[i]))

    return swing_highs, swing_lows

def htf_bias_from_structure(htf_df):
    """
    Uses last 2 swing highs and last 2 swing lows.
    BUY if higher-high AND higher-low.
    SELL if lower-high AND lower-low.
    """
    sh, sl = find_swings(htf_df, left=2, right=2)
    if len(sh) < 2 or len(sl) < 2:
        return None

    (i1h, h1), (i2h, h2) = sh[-2], sh[-1]
    (i1l, l1), (i2l, l2) = sl[-2], sl[-1]

    # Ensure the latest swings are reasonably recent in the series
    if max(i2h, i2l) < len(htf_df) - 50:
        return None

    if h2 > h1 and l2 > l1:
        return "BUY"
    if h2 < h1 and l2 < l1:
        return "SELL"
    return None

def scan_fvg(ltf_df, direction, min_gap_pct, lookback=80):
    """
    Standard 3-candle FVG / imbalance:
    Bullish FVG at i if high[i-2] < low[i]
    Bearish FVG at i if low[i-2] > high[i]

    Returns dict:
      { "type": "BULL"/"BEAR", "low": float, "high": float, "mid": float, "idx": int, "gap_pct": float }
    """
    n = len(ltf_df)
    start = max(2, n - lookback)

    best = None  # most recent valid FVG

    for i in range(start, n):
        c0 = ltf_df.iloc[i-2]
        c1 = ltf_df.iloc[i-1]
        c2 = ltf_df.iloc[i]

        if direction == "BUY":
            # bullish gap between c0.high and c2.low
            if c0["high"] < c2["low"]:
                gap_low = float(c0["high"])
                gap_high = float(c2["low"])
                mid = (gap_low + gap_high) / 2.0
                gap_pct = ((gap_high - gap_low) / float(c1["close"])) * 100.0
                if gap_pct >= min_gap_pct:
                    best = {"type": "BULL", "low": gap_low, "high": gap_high, "mid": mid, "idx": i, "gap_pct": gap_pct}

        if direction == "SELL":
            # bearish gap between c2.high and c0.low (inverted)
            if c0["low"] > c2["high"]:
                gap_low = float(c2["high"])
                gap_high = float(c0["low"])
                mid = (gap_low + gap_high) / 2.0
                gap_pct = ((gap_high - gap_low) / float(c1["close"])) * 100.0
                if gap_pct >= min_gap_pct:
                    best = {"type": "BEAR", "low": gap_low, "high": gap_high, "mid": mid, "idx": i, "gap_pct": gap_pct}

    return best

def recent_swing_sl(ltf_df, direction, lookback=12):
    """
    More realistic SL than last candle low/high:
    - BUY: below the minimum low of last N candles
    - SELL: above the maximum high of last N candles
    """
    window = ltf_df.iloc[-lookback:]
    if direction == "BUY":
        return float(window["low"].min())
    else:
        return float(window["high"].max())

def in_price_proximity(price, entry, near_entry_pct):
    diff_pct = abs(price - entry) / price * 100.0
    return diff_pct <= near_entry_pct

def cleanup_cooldowns():
    cut = now_utc() - timedelta(minutes=COOLDOWN_MIN)
    for sym in list(open_trades.keys()):
        if open_trades[sym]["time"] < cut:
            del open_trades[sym]

def load_stats():
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

async def send(text):
    await bot.send_message(chat_id=CHAT_ID, text=text)

# =========================
# MAIN LOOP
# =========================
async def run():
    await send("✅ Bot is live. Scanning killzones only.")
    last_heartbeat_hour = None

    while True:
        try:
            cleanup_cooldowns()

            # Heartbeat once per hour (debug comfort)
            current_hour = now_utc().hour
            if last_heartbeat_hour != current_hour:
                last_heartbeat_hour = current_hour
                log("Heartbeat", now_utc().isoformat(), "Killzone:", in_killzone(), killzone_name())

            if not in_killzone():
                log("Off killzone. Sleeping.")
                await asyncio.sleep(SCAN_INTERVAL_SEC)
                continue

            if high_impact_news_block():
                log("News blocked. Sleeping.")
                await asyncio.sleep(SCAN_INTERVAL_SEC)
                continue

            for symbol in SYMBOLS:
                if symbol in open_trades:
                    log(symbol, "in cooldown, skip")
                    continue

                cfg = COIN_CONFIG.get(symbol)
                if not cfg:
                    continue

                # HTF bias
                htf_df = fetch_df(symbol, HTF, limit=250)
                bias = htf_bias_from_structure(htf_df)
                if not bias:
                    log(symbol, "no HTF bias")
                    continue

                # LTF FVG scan
                ltf_df = fetch_df(symbol, LTF, limit=250)
                fvg = scan_fvg(
                    ltf_df,
                    direction=bias,
                    min_gap_pct=cfg["fvg_min_pct"],
                    lookback=FVG_LOOKBACK
                )
                if not fvg:
                    log(symbol, "no FVG found")
                    continue

                price = float(ltf_df["close"].iloc[-1])
                entry = float(fvg["mid"])

                # Don’t alert if price is far from the entry
                if not in_price_proximity(price, entry, cfg["near_entry_pct"]):
                    log(symbol, "FVG exists but price too far from entry", "price", price, "entry", entry)
                    continue

                # SL / TP
                sl = recent_swing_sl(ltf_df, bias, lookback=14)
                rr = float(cfg["rr"])
                risk = abs(entry - sl)
                if risk <= 0:
                    continue

                if bias == "BUY":
                    tp = entry + risk * rr
                else:
                    tp = entry - risk * rr

                open_trades[symbol] = {
                    "time": now_utc(),
                    "direction": bias,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "gap_pct": fvg["gap_pct"],
                }

                msg = (
                    f"📌 SMC + MSNR (FVG) ALERT\n\n"
                    f"Pair: {symbol}\n"
                    f"Session: {killzone_name()} (UTC)\n"
                    f"Bias: {bias}\n"
                    f"FVG gap: {fvg['gap_pct']:.2f}%\n\n"
                    f"Entry: {entry:.4f}\n"
                    f"SL: {sl:.4f}\n"
                    f"TP: {tp:.4f}\n\n"
                    f"Cooldown: {COOLDOWN_MIN} min"
                )
                await send(msg)

                # small pause to be nice to APIs + Telegram
                await asyncio.sleep(2)

            await asyncio.sleep(SCAN_INTERVAL_SEC)

        except Exception as e:
            print("ERROR:", repr(e))
            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(run())
