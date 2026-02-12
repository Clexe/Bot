import os
import logging

# =====================
# LOGGING
# =====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("SniperV3")

# =====================
# ENV & CREDENTIALS
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
DERIV_TOKEN = os.getenv("DERIV_TOKEN")
DERIV_APP_ID = os.getenv("DERIV_APP_ID")
BYBIT_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_SECRET = os.getenv("BYBIT_API_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID", "")

# =====================
# NEWS SETTINGS
# =====================
USE_NEWS_FILTER = True
NEWS_IMPACT = ["High", "Medium"]
NEWS_CACHE_TTL = 3600  # seconds
NEWS_BLACKOUT_MINUTES = 30

# =====================
# DEFAULT USER SETTINGS (single source of truth)
# =====================
DEFAULT_SETTINGS = {
    "pairs": ["XAUUSD", "BTCUSD", "V75"],
    "scan_interval": 60,
    "cooldown": 60,
    "max_spread": 0.0005,
    "session": "BOTH",
    "mode": "MARKET",
    "timeframe": "M15",
    "higher_tf": "1D",
    "risk_pips": 50,
}

VALID_SESSIONS = {"LONDON", "NY", "BOTH"}
VALID_MODES = {"MARKET", "LIMIT"}
VALID_TIMEFRAMES = {"M5", "M15", "M30", "H1"}
VALID_HIGHER_TFS = {"H4", "1D", "1W"}

# =====================
# SUPPORTED SYMBOLS
# =====================
KNOWN_SYMBOLS = {
    # Forex
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
    "EURGBP", "EURJPY", "GBPJPY", "AUDCAD", "AUDCHF", "CADJPY", "CHFJPY",
    "EURAUD", "EURCAD", "EURCHF", "EURNZD", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
    "NZDCAD", "NZDCHF", "NZDJPY", "AUDNZD", "AUDJPY",
    # Crypto
    "BTCUSD", "ETHUSD", "SOLUSD", "BTCUSDT", "ETHUSDT", "SOLUSDT",
    # Indices
    "US30", "NAS100", "GER40", "UK100", "US500",
    # Volatility Indices
    "V75", "V10", "V25", "V50", "V100",
    "V75_1S", "V10_1S", "V25_1S", "V50_1S", "V100_1S",
    # Boom & Crash
    "BOOM300", "BOOM500", "BOOM1000", "CRASH300", "CRASH500", "CRASH1000",
    # Others
    "STEP_INDEX", "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
    "JUMP10", "JUMP25", "JUMP50", "JUMP75", "JUMP100",
}

# =====================
# DERIV SYMBOL MAPPING
# =====================
DERIV_SYMBOL_MAP = {
    "XAUUSD": "frxXAUUSD",
    "EURUSD": "frxEURUSD",
    "GBPUSD": "frxGBPUSD",
    "USDJPY": "frxUSDJPY",
    "AUDUSD": "frxAUDUSD",
    "NZDUSD": "frxNZDUSD",
    "USDCAD": "frxUSDCAD",
    "USDCHF": "frxUSDCHF",
    "EURGBP": "frxEURGBP",
    "EURJPY": "frxEURJPY",
    "GBPJPY": "frxGBPJPY",
    "CADJPY": "frxCADJPY",
    "CHFJPY": "frxCHFJPY",
    "EURAUD": "frxEURAUD",
    "EURCAD": "frxEURCAD",
    "EURCHF": "frxEURCHF",
    "EURNZD": "frxEURNZD",
    "GBPAUD": "frxGBPAUD",
    "GBPCAD": "frxGBPCAD",
    "GBPCHF": "frxGBPCHF",
    "GBPNZD": "frxGBPNZD",
    "NZDCAD": "frxNZDCAD",
    "NZDCHF": "frxNZDCHF",
    "NZDJPY": "frxNZDJPY",
    "AUDNZD": "frxAUDNZD",
    "AUDJPY": "frxAUDJPY",
    # Volatility Indices
    "V75": "R_75",
    "V10": "R_10",
    "V25": "R_25",
    "V50": "R_50",
    "V100": "R_100",
    "V75_1S": "1HZ75V",
    "V10_1S": "1HZ10V",
    "V25_1S": "1HZ25V",
    "V50_1S": "1HZ50V",
    "V100_1S": "1HZ100V",
    # Commodities
    "XAGUSD": "frxXAGUSD",
    # Indices
    "US30": "frxUSOTC",
}

DERIV_KEYWORDS = [
    "XAU", "XAG", "EUR", "GBP", "JPY", "AUD", "CAD", "NZD", "CHF",
    "R_", "V75", "V10", "V25", "V50", "V100",
    "1S", "1HZ", "FRX", "US30", "NAS", "GER", "UK100",
    "BOOM", "CRASH", "STEP", "JUMP",
]

ALWAYS_OPEN_KEYS = [
    "BTC", "ETH", "SOL", "USDT", "R_",
    "V75", "V10", "V25", "V50", "V100",
    "1HZ", "BOOM", "CRASH", "JUMP", "STEP",
]

# Pip value mapping for risk calculation
HIGH_PIP_SYMBOLS = [
    "JPY", "V75", "V10", "V25", "V50", "V100",
    "R_", "BOOM", "CRASH", "STEP", "JUMP", "1HZ",
    "XAU", "XAG", "US30", "NAS", "GER", "US500", "UK100",
]

# =====================
# TIMEFRAME GRANULARITY MAPPING
# =====================
DERIV_GRANULARITY = {
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "1D": 86400,
    "1W": 604800,
}

BYBIT_INTERVALS = {
    "M5": "5",
    "M15": "15",
    "M30": "30",
    "H1": "60",
    "H4": "240",
    "1D": "D",
    "1W": "W",
}

# =====================
# SIGNAL & SCANNER SETTINGS
# =====================
SIGNAL_TTL = 7200  # 2 hours - cleanup stale signal entries
SCAN_LOOP_INTERVAL = 60  # seconds between scan cycles
SCAN_ERROR_INTERVAL = 10  # seconds to wait after scanner error

# Rate limiter settings
RATE_LIMIT_MESSAGES_PER_SECOND = 25  # Telegram allows ~30, leave margin
RATE_LIMIT_BURST = 5
