from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

async def queue_signal_for_delivery(db, signal_id: int, chat_id: int, message: str, delay_minutes: int):
    """Insert delayed delivery row used by free-tier signal delay."""
    deliver_at = datetime.utcnow() + timedelta(minutes=delay_minutes)
    await db.execute("""INSERT INTO delivery_queue (signal_id, chat_id, message, deliver_at, delivered) VALUES (%s,%s,%s,%s,false)""", (signal_id, chat_id, message, deliver_at))

async def process_delivery_queue(db, telegram):
    """Deliver all due queue items and mark rows delivered."""
    due = await db.fetch("SELECT * FROM delivery_queue WHERE deliver_at <= NOW() AND delivered = false")
    for item in due:
        await telegram.send_message(item['chat_id'], item['message'])
        await db.execute("UPDATE delivery_queue SET delivered=true, delivered_at=NOW() WHERE id=%s", (item['id'],))

def make_scheduler():
    """Create AsyncIOScheduler instance."""
    return AsyncIOScheduler()
