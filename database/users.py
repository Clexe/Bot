import json
import asyncio
from utils.logger import get_logger

logger = get_logger(__name__)

# Default user settings (single source of truth)
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
    "touch_trade": False,
    "balance": 0,
    "risk_pct": 1,
}


async def get_user_async(db, chat_id):
    """Get user settings, creating with defaults if not exists."""
    chat_id = str(chat_id)
    row = await db.fetchrow(
        "SELECT settings FROM users WHERE user_id = %s AND is_active = TRUE",
        (chat_id,),
    )
    if row:
        saved = row["settings"] if row["settings"] else {}
        merged = {**DEFAULT_SETTINGS, **saved}
        if not isinstance(merged.get("pairs"), list):
            merged["pairs"] = list(DEFAULT_SETTINGS["pairs"])
        return merged
    # New user
    defaults = {**DEFAULT_SETTINGS, "pairs": list(DEFAULT_SETTINGS["pairs"])}
    await save_user_settings_async(db, chat_id, defaults)
    return defaults


async def save_user_settings_async(db, chat_id, settings):
    """Upsert user settings as JSONB."""
    chat_id = str(chat_id)
    json_settings = json.dumps(settings)
    await db.execute(
        """INSERT INTO users (user_id, settings, is_active)
           VALUES (%s, %s, TRUE)
           ON CONFLICT (user_id)
           DO UPDATE SET settings = %s, is_active = TRUE;""",
        (chat_id, json_settings, json_settings),
    )


async def load_users_async(db):
    """Load all active users with their settings."""
    rows = await db.fetch(
        "SELECT user_id, settings FROM users WHERE is_active = TRUE"
    )
    users = {}
    for r in rows:
        uid = str(r["user_id"])
        saved = r["settings"] if r["settings"] else {}
        merged = {**DEFAULT_SETTINGS, **saved}
        if not isinstance(merged.get("pairs"), list):
            merged["pairs"] = list(DEFAULT_SETTINGS["pairs"])
        users[uid] = merged
    return users


async def deactivate_user_async(db, chat_id):
    """Mark a user as inactive."""
    chat_id = str(chat_id)
    await db.execute(
        "UPDATE users SET is_active = FALSE WHERE user_id = %s", (chat_id,)
    )
    logger.info("Deactivated user %s", chat_id)
