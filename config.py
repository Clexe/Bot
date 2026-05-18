import os
from datetime import time

DATABASE_URL = os.getenv("DATABASE_URL", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ADMIN_CHAT_IDS = [int(x) for x in os.getenv("ADMIN_CHAT_IDS", "").split(",") if x.strip()]
DERIV_APP_ID = os.getenv("DERIV_APP_ID", "")
DERIV_WS_URL = os.getenv("DERIV_WS_URL", "wss://ws.derivws.com/websockets/v3")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

DERIV_PAIRS = ["frxEURUSD", "frxGBPUSD", "frxXAUUSD", "frxGBPJPY"]
BYBIT_PAIRS = ["BTCUSDT"]
ALL_PAIRS = DERIV_PAIRS + BYBIT_PAIRS

PAIR_DISPLAY = {
    "frxEURUSD": "EURUSD",
    "frxGBPUSD": "GBPUSD",
    "frxXAUUSD": "XAUUSD",
    "frxGBPJPY": "GBPJPY",
    "BTCUSDT": "BTCUSDT",
}

PIP_SIZE = {
    "frxEURUSD": 0.0001,
    "frxGBPUSD": 0.0001,
    "frxXAUUSD": 0.10,
    "frxGBPJPY": 0.01,
    "BTCUSDT": 1.0,
}

MIN_SL_PIPS = 3
MIN_RR = 3.0
MAX_DAILY_LOSSES = 3

TF_MAP_DERIV = {
    "D": 86400,
    "H1": 3600,
    "M15": 900,
    "M5": 300,
}

TF_MAP_BYBIT = {
    "D": "D",
    "H1": "60",
    "M15": "15",
    "M5": "5",
}

KILL_ZONES = [
    {"name": "London", "start": time(7, 0), "end": time(11, 0)},
    {"name": "New York", "start": time(12, 0), "end": time(17, 0)},
]

SCAN_INTERVAL_MINUTES = 15
DUPLICATE_PREVENTION_HOURS = 4
