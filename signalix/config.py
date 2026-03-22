import os
from typing import Dict, List, Tuple

# ─── WebSocket / REST URLs ───────────────────────────────────────────────────
DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3"
BYBIT_REST_URL = "https://api.bybit.com"
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

# ─── Pairs ───────────────────────────────────────────────────────────────────
FOREX_PAIRS: List[str] = ["EURUSD", "GBPUSD", "XAUUSD", "USDJPY"]
CRYPTO_PAIRS: List[str] = ["BTCUSDT", "ETHUSDT"]
ALL_PAIRS: List[str] = FOREX_PAIRS + CRYPTO_PAIRS

PAIR_CONFIG: Dict[str, dict] = {
    "XAUUSD":  {"sl_buffer_pips": 3, "precision_all_layers": True,  "cot": True},
    "XAGUSD":  {"sl_buffer_pips": 5, "precision_all_layers": True,  "cot": True},
    "EURUSD":  {"sl_buffer_pips": 2, "precision_all_layers": False, "cot": False},
    "GBPUSD":  {"sl_buffer_pips": 3, "precision_all_layers": False, "cot": False},
    "USDJPY":  {"sl_buffer_pips": 2, "precision_all_layers": False, "cot": False},
    "BTCUSDT": {"sl_buffer_pips": 0, "precision_all_layers": False, "cot": False},
    "ETHUSDT": {"sl_buffer_pips": 0, "precision_all_layers": False, "cot": False},
}

# ─── Timeframes ──────────────────────────────────────────────────────────────
TIMEFRAMES: List[str] = ["M", "W", "D", "H4", "H1", "M15", "M5"]

TF_MAP_BYBIT: Dict[str, str] = {
    "M1": "1", "M5": "5", "M15": "15", "H1": "60",
    "H4": "240", "D": "D", "W": "W", "M": "M",
}

TF_MAP_DERIV: Dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "H1": 3600,
    "H4": 14400, "D": 86400, "W": 604800, "M": 2592000,
}

CANDLE_REQUIREMENTS: Dict[str, int] = {
    "Monthly": 50, "Weekly": 100, "Daily": 100,
    "H4": 100, "H1": 100, "M15": 100, "M5": 50,
}

# ─── Kill Zone Schedule ─────────────────────────────────────────────────────
SCAN_SCHEDULE: Dict[str, dict] = {
    "precision": {
        "interval_minutes": 15,
        "active_during_kill_zones_only": True,
        "kill_zones": [
            ("08:00", "10:00"),
            ("13:00", "15:00"),
            ("18:00", "20:00"),
        ],
    },
    "flow": {
        "interval_minutes": 5,
        "active_during_kill_zones_only": True,
        "kill_zones": [
            ("07:30", "10:30"),
            ("12:30", "15:30"),
            ("18:00", "20:00"),
        ],
    },
    "cot_refresh": {"day": "sunday", "time": "18:00"},
    "health_check": {"interval_minutes": 10},
}

# ─── Signal Tier Rules ──────────────────────────────────────────────────────
PRECISION_TIER_RULES: Dict[str, dict] = {
    "free":  {"min_score": 14, "delay_minutes": 60},
    "basic": {"min_score": 12, "delay_minutes": 0},
    "pro":   {"min_score": 10, "delay_minutes": 0},
    "elite": {"min_score": 10, "delay_minutes": 0},
}

FLOW_TIER_RULES: Dict[str, dict] = {
    "free":  None,
    "basic": {"min_score": 8, "delay_minutes": 0},
    "pro":   {"min_score": 6, "delay_minutes": 0},
    "elite": {"min_score": 6, "delay_minutes": 0},
}

# ─── Environment Variables ──────────────────────────────────────────────────
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
DERIV_API_KEY: str = os.environ.get("DERIV_API_KEY", "")
DERIV_APP_ID: str = os.environ.get("DERIV_APP_ID", "")
BYBIT_API_KEY: str = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET: str = os.environ.get("BYBIT_API_SECRET", "")
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID: str = os.environ.get("TELEGRAM_CHANNEL_ID", "")
PAYSTACK_SECRET_KEY: str = os.environ.get("PAYSTACK_SECRET_KEY", "")
ADMIN_CHAT_IDS: Tuple[int, ...] = tuple(
    int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").split(",") if x.strip()
)
