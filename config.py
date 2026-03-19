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
