import os
from dataclasses import dataclass
from typing import Dict, List

DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3"
BYBIT_REST_URL = "https://api.bybit.com"
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

FOREX_PAIRS = ["EURUSD", "GBPUSD", "XAUUSD", "USDJPY"]
CRYPTO_PAIRS = ["BTCUSDT", "ETHUSDT"]
TIMEFRAMES = ["M", "W", "D", "H4", "H1", "M15", "M5"]

TF_MAP_BYBIT: Dict[str, str] = {
    "M1": "1", "M5": "5", "M15": "15", "H1": "60", "H4": "240", "D": "D", "W": "W", "M": "M"
}

@dataclass(frozen=True)
class Settings:
    """Runtime configuration sourced from Railway environment variables."""

    database_url: str = os.environ.get("DATABASE_URL", "")
    deriv_api_key: str = os.environ.get("DERIV_TOKEN", "")
    deriv_app_id: str = os.environ.get("DERIV_APP_ID", "")
    bybit_api_key: str = os.environ.get("BYBIT_API_KEY", "")
    bybit_api_secret: str = os.environ.get("BYBIT_API_SECRET", "")
    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    telegram_bot_token: str = os.environ.get("TELEGRAM_TOKEN", "")
    telegram_channel_id: str = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    paystack_secret_key: str = os.environ.get("PAYSTACK_SECRET_KEY", "")
    admin_chat_ids: List[int] = tuple(int(x) for x in os.environ.get("ADMIN_ID", "").split(",") if x.strip())

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

# Pip value: symbols containing these keys use 100 pips/unit (JPY, metals, indices, synthetics)
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

