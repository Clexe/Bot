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
    deriv_api_key: str = os.environ.get("DERIV_API_KEY", "")
    deriv_app_id: str = os.environ.get("DERIV_APP_ID", "")
    bybit_api_key: str = os.environ.get("BYBIT_API_KEY", "")
    bybit_api_secret: str = os.environ.get("BYBIT_API_SECRET", "")
    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_channel_id: str = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    paystack_secret_key: str = os.environ.get("PAYSTACK_SECRET_KEY", "")
    admin_chat_ids: List[int] = tuple(int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").split(",") if x.strip())

settings = Settings()
