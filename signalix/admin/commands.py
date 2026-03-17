from signalix.config import settings

def is_admin(chat_id: int) -> bool:
    """Check admin authorization by configured chat IDs."""
    return chat_id in settings.admin_chat_ids

async def log_admin(db, admin_chat_id: int, command: str, parameters: str, result: str):
    """Persist admin command execution audit log."""
    await db.execute("INSERT INTO admin_logs (admin_chat_id, command, parameters, result) VALUES (%s,%s,%s,%s)", (admin_chat_id, command, parameters, result))
