import os
from dataclasses import dataclass
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
DERIV_API_KEY: str = os.environ.get("DERIV_TOKEN", os.environ.get("DERIV_API_KEY", ""))
DERIV_APP_ID: str = os.environ.get("DERIV_APP_ID", "")
BYBIT_API_KEY: str = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET: str = os.environ.get("BYBIT_API_SECRET", "")
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHANNEL_ID: str = os.environ.get("TELEGRAM_CHANNEL_ID", "")
PAYSTACK_SECRET_KEY: str = os.environ.get("PAYSTACK_SECRET_KEY", "")
ADMIN_CHAT_IDS: Tuple[int, ...] = tuple(
    int(x) for x in os.environ.get("ADMIN_ID", os.environ.get("ADMIN_CHAT_IDS", "")).split(",") if x.strip()
)

# ─── Settings dataclass (backwards compat with main's style) ────────────────
@dataclass(frozen=True)
class Settings:
    """Runtime configuration sourced from Railway environment variables."""
    database_url: str = DATABASE_URL
    deriv_api_key: str = DERIV_API_KEY
    deriv_app_id: str = DERIV_APP_ID
    bybit_api_key: str = BYBIT_API_KEY
    bybit_api_secret: str = BYBIT_API_SECRET
    deepseek_api_key: str = DEEPSEEK_API_KEY
    telegram_bot_token: str = TELEGRAM_BOT_TOKEN
    telegram_channel_id: str = TELEGRAM_CHANNEL_ID
    paystack_secret_key: str = PAYSTACK_SECRET_KEY
    admin_chat_ids: Tuple[int, ...] = ADMIN_CHAT_IDS

settings = Settings()

# =====================
# HANDLER CONSTANTS
# =====================
VALID_SESSIONS = {"LONDON", "NY", "BOTH"}
VALID_MODES = {"MARKET", "LIMIT"}
VALID_TIMEFRAMES = {"M5", "M15", "M30", "H1"}
VALID_HIGHER_TFS = {"H4", "1D", "1W"}

FOREX_BASES = {
    "EUR", "GBP", "USD", "JPY", "AUD", "NZD", "CAD", "CHF",
    "XAU", "XAG",
}

KNOWN_SYMBOLS = {
    # Forex
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
    "EURGBP", "EURJPY", "GBPJPY", "AUDCAD", "AUDCHF", "CADJPY", "CHFJPY",
    "EURAUD", "EURCAD", "EURCHF", "EURNZD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
    "NZDCAD", "NZDCHF", "NZDJPY", "AUDNZD", "AUDJPY",
    # Crypto
    "BTCUSD", "ETHUSD", "SOLUSD",
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "SHIBUSDT", "LTCUSDT", "BNBUSDT", "TRXUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "PEPEUSDT", "WIFUSDT",
    "BONKUSDT", "TONUSDT", "FILUSDT", "ATOMUSDT", "ICPUSDT", "INJUSDT",
    "FETUSDT", "RNDRUSDT", "TIAUSDT", "SEIUSDT", "JUPUSDT", "WLDUSDT",
    "ENAUSDT", "AAVEUSDT", "MKRUSDT", "UNIUSDT",
    # Volatility Indices
    "V75", "V10", "V25", "V50", "V100",
    "V75_1S", "V10_1S", "V25_1S", "V50_1S", "V100_1S",
    # Boom & Crash
    "BOOM300", "BOOM500", "BOOM1000", "CRASH300", "CRASH500", "CRASH1000",
    # Synthetic
    "STEP_INDEX", "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
    "JUMP10", "JUMP25", "JUMP50", "JUMP75", "JUMP100",
}

# =====================
# DERIV SYMBOL MAPPING (full)
# =====================
DERIV_SYMBOL_MAP = {
    "XAUUSD": "frxXAUUSD", "XAGUSD": "frxXAGUSD",
    "EURUSD": "frxEURUSD", "GBPUSD": "frxGBPUSD",
    "USDJPY": "frxUSDJPY", "AUDUSD": "frxAUDUSD",
    "NZDUSD": "frxNZDUSD", "USDCAD": "frxUSDCAD", "USDCHF": "frxUSDCHF",
    "EURGBP": "frxEURGBP", "EURJPY": "frxEURJPY", "GBPJPY": "frxGBPJPY",
    "CADJPY": "frxCADJPY", "CHFJPY": "frxCHFJPY",
    "EURAUD": "frxEURAUD", "EURCAD": "frxEURCAD", "EURCHF": "frxEURCHF", "EURNZD": "frxEURNZD",
    "GBPAUD": "frxGBPAUD", "GBPCAD": "frxGBPCAD", "GBPCHF": "frxGBPCHF", "GBPNZD": "frxGBPNZD",
    "NZDCAD": "frxNZDCAD", "NZDCHF": "frxNZDCHF", "NZDJPY": "frxNZDJPY",
    "AUDNZD": "frxAUDNZD", "AUDJPY": "frxAUDJPY",
    # Volatility Indices
    "V75": "R_75", "V10": "R_10", "V25": "R_25", "V50": "R_50", "V100": "R_100",
    "V75_1S": "1HZ75V", "V10_1S": "1HZ10V", "V25_1S": "1HZ25V",
    "V50_1S": "1HZ50V", "V100_1S": "1HZ100V",
}

DERIV_KEYWORDS = [
    "XAU", "XAG", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF",
    "R_", "V75", "V10", "V25", "V50", "V100",
    "1S", "1HZ", "FRX", "BOOM", "CRASH", "STEP", "JUMP",
]

ALWAYS_OPEN_KEYS = [
    "BTC", "ETH", "SOL", "USDT", "R_",
    "V75", "V10", "V25", "V50", "V100",
    "1HZ", "BOOM", "CRASH", "JUMP", "STEP",
]

# Deriv granularity mapping
DERIV_GRANULARITY = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D": 86400, "1D": 86400, "W": 604800, "1W": 604800,
}

# Pip value: symbols containing these keys use 100 pips/unit
HIGH_PIP_SYMBOLS = [
    "JPY", "V75", "V10", "V25", "V50", "V100",
    "R_", "BOOM", "CRASH", "STEP", "JUMP", "1HZ",
    "XAU", "XAG",
]

# =====================
# SIGNAL & SCANNER SETTINGS
# =====================
SIGNAL_TTL = 7200
SCAN_LOOP_INTERVAL = 60
SCAN_ERROR_INTERVAL = 10
SIGNAL_MAX_AGE_HOURS = 24
AUTO_WIN_PIPS = 100
BE_BUFFER_PIPS = 2

# Adaptive scan interval per user timeframe
ADAPTIVE_SCAN_INTERVALS = {
    "M5": 30, "M15": 60, "M30": 90, "H1": 120,
}

# Confidence-based position sizing
CONFIDENCE_SIZE_MULTIPLIERS = {
    "high": 1.5, "medium": 1.0, "low": 0.5,
}


def get_pip_value(pair: str) -> float:
    """Return pip value multiplier for a pair."""
    clean = pair.upper()
    if any(k in clean for k in HIGH_PIP_SYMBOLS):
        return 100.0
    return 10000.0
